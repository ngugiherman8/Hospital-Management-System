import datetime
import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.appointments.models import Appointment, Status, TransitionBlocked
from apps.clinical.models import Consultation
from apps.core.models import AuditLog
from apps.dental.models import DentalProcedure, ToothRecord, TreatmentPlan, TreatmentPlanItem
from apps.imaging.models import CriticalAck, ImagingItem, ImagingReport, ImagingStudy, save_report
from apps.maternity.models import (
    AntenatalContact,
    ContactSchedule,
    DeliverySummary,
    PregnancyEpisode,
    record_delivery,
)
from apps.orders.models import Order, OrderItem
from apps.patients.models import Patient
from conftest import make_user


def clinic_visit(patient, clinic, **kw):
    return Appointment.objects.create(patient=patient, kind="walk_in", clinic=clinic, **kw)


def to_with_doctor(visit, user):
    """Move a visit to 'With doctor' using the right path for its clinic."""
    if visit.status == Status.WAITING_TRIAGE:
        from apps.accounts.models import Role

        reception = make_user(Role.RECEPTION, username=f"rec-{visit.pk}")
        visit.move_to(Status.WAITING_DOCTOR, reception)
    visit.move_to(Status.WITH_DOCTOR, user)
    return visit


@pytest.fixture
def general_visit(patient, nurse, doctor):
    from apps.clinical.models import TriageRecord

    visit = clinic_visit(patient, "general")
    TriageRecord.objects.create(appointment=visit, nurse=nurse, complaint="Cough")
    visit.move_to(Status.WAITING_DOCTOR, nurse)
    visit.move_to(Status.WITH_DOCTOR, doctor)
    return visit


def order_study(visit, doctor, code="XR-CHEST", **answers):
    item = ImagingItem.objects.get(test__code=code)
    order = Order.objects.create(appointment=visit, kind="imaging", ordered_by=doctor)
    oi = OrderItem.objects.create(order=order, test=item.test)
    return ImagingStudy.objects.create(
        order_item=oi, patient=visit.patient, item=item, clinical_reason="Cough 3 weeks", **answers
    )


# ---------- access ----------

NEW_PAGES = [
    ("/imaging/", {"radiographer", "radiologist", "doctor", "dentist", "midwife"}),
    ("/maternity/", {"midwife", "doctor"}),
    ("/maternity/due/", {"midwife", "doctor"}),
]
ROLES = (
    "reception",
    "nurse",
    "doctor",
    "lab",
    "pharmacist",
    "cashier",
    "dentist",
    "midwife",
    "radiographer",
    "radiologist",
)


@pytest.mark.parametrize("url,allowed", NEW_PAGES)
def test_new_pages_by_role(client, db, url, allowed):
    assert client.get(url).status_code == 302
    for role in ROLES:
        client.force_login(make_user(role, username=f"u-{role}"))
        assert client.get(url).status_code == (200 if role in allowed else 403), (url, role)


def test_new_roles_can_sign_in_and_see_the_queue(login, dentist, midwife, radiographer, radiologist):
    for user in (dentist, midwife, radiographer, radiologist):
        assert login(user).get("/appointments/").status_code == 200


def test_study_and_dental_pages_by_role(client, db, patient, general_visit, catalogue):
    visit = clinic_visit(patient, "dental")
    study = order_study(general_visit, general_visit.doctor or make_user("doctor", username="d9"))
    pages = {
        f"/dental/visit/{visit.pk}/": {"dentist", "doctor"},
        f"/imaging/study/{study.pk}/": {"radiographer", "radiologist", "doctor", "dentist", "midwife"},
    }
    users = {role: make_user(role, username=f"v-{role}") for role in ROLES}
    for url, allowed in pages.items():
        client.logout()
        assert client.get(url).status_code == 302
        for role in ROLES:
            client.force_login(users[role])
            assert client.get(url).status_code == (200 if role in allowed else 403), (url, role)


# ---------- visit status ----------


def test_dental_patient_skips_triage_and_goes_to_the_dentist(patient, dentist, reception, catalogue):
    visit = clinic_visit(patient, "dental")
    visit.move_to(Status.WAITING_DOCTOR, reception)
    visit.move_to(Status.WITH_DOCTOR, dentist)
    assert visit.status == Status.WITH_DOCTOR


def test_booked_dental_visit_checks_in_straight_to_the_dentist(patient, reception, dentist, tomorrow_10am):
    visit = Appointment.objects.create(patient=patient, kind="booked", clinic="dental", scheduled_for=tomorrow_10am)
    visit.move_to(Status.WAITING_DOCTOR, reception)
    assert visit.queue_number == 1 and visit.status == Status.WAITING_DOCTOR


def test_midwife_runs_mch_visits_but_not_general_ones(patient, midwife, nurse):
    from apps.clinical.models import TriageRecord

    mch = clinic_visit(patient, "mch")
    TriageRecord.objects.create(appointment=mch, nurse=nurse, complaint="ANC")
    mch.move_to(Status.WAITING_DOCTOR, nurse)
    mch.move_to(Status.WITH_DOCTOR, midwife)
    general = clinic_visit(patient, "general")
    TriageRecord.objects.create(appointment=general, nurse=nurse, complaint="x")
    general.move_to(Status.WAITING_DOCTOR, nurse)
    with pytest.raises(PermissionError):
        general.move_to(Status.WITH_DOCTOR, midwife)


def test_dentist_cannot_work_a_general_visit(login, dentist, general_visit):
    assert login(dentist).get(f"/clinical/visit/{general_visit.pk}/").status_code == 403
    assert login(dentist).post(f"/clinical/visit/{general_visit.pk}/note/", {"history": "x"}).status_code == 403


def test_waiting_for_results_needs_a_report_for_scans(general_visit, doctor, radiographer, radiologist, catalogue):
    study = order_study(general_visit, doctor)
    general_visit.move_to(Status.AWAITING_RESULTS, doctor)
    study.move_to("arrived", radiographer)
    study.move_to("images_done", radiographer)
    with pytest.raises(TransitionBlocked):
        general_visit.move_to(Status.WITH_DOCTOR, doctor)  # images taken but no report yet
    save_report(study, radiologist, "Clear lungs", "Normal chest")
    general_visit.refresh_from_db()
    assert general_visit.status == Status.WITH_DOCTOR  # moved back automatically


# ---------- imaging ----------


def test_radiation_order_stores_the_pregnancy_answer(login, doctor, general_visit, catalogue, patient):
    item = ImagingItem.objects.get(test__code="XR-CHEST")
    url = f"/imaging/visit/{general_visit.pk}/order/"
    client = login(doctor)
    client.post(url, {"studies": [item.pk], "clinical_reason": "Cough"})
    assert not ImagingStudy.objects.exists()  # a woman of 34 must be asked about pregnancy
    client.post(url, {"studies": [item.pk], "clinical_reason": "Cough", "may_be_pregnant": "no"})
    study = ImagingStudy.objects.get()
    assert study.may_be_pregnant is False and study.accession_number.startswith("IMG-")
    assert study.order_item.order.kind == "imaging"


def test_men_are_not_asked_about_pregnancy(login, doctor, nurse, catalogue):
    man = Patient.objects.create(
        first_name="Otieno", last_name="Ouma", date_of_birth=datetime.date(1985, 5, 5), sex="M"
    )
    from apps.clinical.models import TriageRecord

    visit = clinic_visit(man, "general")
    TriageRecord.objects.create(appointment=visit, nurse=nurse, complaint="Cough")
    visit.move_to(Status.WAITING_DOCTOR, nurse)
    visit.move_to(Status.WITH_DOCTOR, doctor)
    item = ImagingItem.objects.get(test__code="XR-CHEST")
    login(doctor).post(f"/imaging/visit/{visit.pk}/order/", {"studies": [item.pk], "clinical_reason": "Cough"})
    assert ImagingStudy.objects.get().may_be_pregnant is None


def test_contrast_order_needs_allergy_and_kidney_answers(login, doctor, general_visit, catalogue):
    item = ImagingItem.objects.get(test__code="CT-ABD-C")
    url = f"/imaging/visit/{general_visit.pk}/order/"
    client = login(doctor)
    client.post(url, {"studies": [item.pk], "clinical_reason": "Mass", "may_be_pregnant": "no"})
    assert not ImagingStudy.objects.exists()
    client.post(
        url,
        {
            "studies": [item.pk],
            "clinical_reason": "Mass",
            "may_be_pregnant": "no",
            "contrast_allergy": "no",
            "kidney_problem": "yes",
        },
    )
    study = ImagingStudy.objects.get()
    assert study.contrast_allergy is False and study.kidney_problem is True


def test_contrast_study_cannot_start_until_safety_is_confirmed(general_visit, doctor, radiographer, catalogue):
    study = order_study(
        general_visit, doctor, "CT-ABD-C", may_be_pregnant=False, contrast_allergy=False, kidney_problem=False
    )
    study.move_to("arrived", radiographer)
    with pytest.raises(ValidationError):
        study.move_to("images_done", radiographer)
    study.confirm_safety(radiographer)
    study.move_to("images_done", radiographer)
    assert study.status == "images_done" and study.radiographer == radiographer


def test_possible_pregnancy_blocks_radiation_study_until_confirmed(general_visit, doctor, radiographer, catalogue):
    study = order_study(general_visit, doctor, may_be_pregnant=True)
    study.move_to("arrived", radiographer)
    with pytest.raises(ValidationError):
        study.move_to("images_done", radiographer)


def test_study_moves_only_in_order_and_only_by_the_radiographer(
    login, general_visit, doctor, radiographer, radiologist, catalogue
):
    study = order_study(general_visit, doctor)
    with pytest.raises(PermissionError):
        study.move_to("images_done", radiographer)  # cannot skip "arrived"
    url = f"/imaging/study/{study.pk}/action/"
    for user in (doctor, radiologist):
        assert login(user).post(url, {"action": "arrived"}).status_code == 403
    login(radiographer).post(url, {"action": "arrived"})
    study.refresh_from_db()
    assert study.status == "arrived"


def test_reports_cannot_be_written_before_images_are_done(general_visit, doctor, radiologist, catalogue):
    study = order_study(general_visit, doctor)
    with pytest.raises(ValidationError):
        save_report(study, radiologist, "x", "y")


def test_final_report_is_locked_and_addendum_keeps_the_original(
    general_visit, doctor, radiographer, radiologist, catalogue
):
    study = order_study(general_visit, doctor)
    study.move_to("arrived", radiographer)
    study.move_to("images_done", radiographer)
    prelim = save_report(study, radiologist, "Findings v1", "Impression v1")
    final = save_report(study, radiologist, "Findings v2", "Impression v2", final=True)
    study.refresh_from_db()
    assert (prelim.status, final.status, study.status) == ("preliminary", "final", "final")
    with pytest.raises(PermissionError):
        final.save()
    with pytest.raises(ValidationError):
        save_report(study, radiologist, "Changed", "Changed")  # a reason is needed
    addendum = save_report(study, radiologist, "Extra finding", "Addendum", reason="Second look")
    assert addendum.addendum_of == final and addendum.status == "final"
    assert ImagingReport.objects.get(pk=final.pk).findings == "Findings v2"
    assert study.reports.count() == 3


def test_critical_finding_alerts_the_ordering_doctor_until_acknowledged(
    login, general_visit, doctor, radiographer, radiologist, catalogue
):
    other = make_user("doctor", username="doctor2")
    study = order_study(general_visit, doctor)
    study.move_to("arrived", radiographer)
    study.move_to("images_done", radiographer)
    report = save_report(study, radiologist, "Large pneumothorax", "Urgent", critical=True)
    banner = "Critical finding:"
    assert banner in login(doctor).get("/appointments/").content.decode()
    assert banner not in login(other).get("/appointments/").content.decode()  # not their order
    assert login(other).post(f"/imaging/report/{report.pk}/acknowledge/").status_code == 302
    assert not CriticalAck.objects.exists()  # refused
    login(doctor).post(f"/imaging/report/{report.pk}/acknowledge/")
    ack = CriticalAck.objects.get()
    assert ack.doctor == doctor and ack.acknowledged_at
    assert banner not in login(doctor).get("/appointments/").content.decode()
    assert study.unacknowledged_critical is None


def test_accession_numbers_are_unique_and_gap_free(general_visit, doctor, catalogue):
    numbers = [order_study(general_visit, doctor).accession_number for _ in range(3)]
    assert [n.rsplit("-", 1)[1] for n in numbers] == ["000001", "000002", "000003"]


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_orders_get_different_accession_numbers(patient, doctor, nurse, catalogue):
    from apps.clinical.models import TriageRecord

    visit = clinic_visit(patient, "general")
    TriageRecord.objects.create(appointment=visit, nurse=nurse, complaint="x")
    visit.move_to(Status.WAITING_DOCTOR, nurse)
    visit.move_to(Status.WITH_DOCTOR, doctor)
    results, barrier = [], threading.Barrier(2)

    def run():
        try:
            barrier.wait()
            results.append(order_study(Appointment.objects.get(pk=visit.pk), doctor).accession_number)
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [f"IMG-{timezone.localdate().year}-000001", f"IMG-{timezone.localdate().year}-000002"]


def test_scan_printouts_can_be_attached_but_only_jpeg_or_pdf(
    login, general_visit, doctor, radiographer, catalogue, settings, tmp_path
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = tmp_path
    study = order_study(general_visit, doctor)
    client = login(radiographer)
    url = f"/imaging/study/{study.pk}/file/"
    client.post(url, {"file": SimpleUploadedFile("evil.exe", b"MZ")})
    assert study.files.count() == 0
    client.post(url, {"file": SimpleUploadedFile("scan.pdf", b"%PDF-1.4"), "note": "Printout"})
    attachment = study.files.get()
    assert client.get(f"/imaging/file/{attachment.pk}/").status_code == 200
    assert login(make_user("reception", username="rr")).get(f"/imaging/file/{attachment.pk}/").status_code == 403
    assert AuditLog.objects.filter(action="view", model_label="imaging.StudyFile").exists()


def test_cancelling_a_scan_cancels_the_study(login, general_visit, doctor, catalogue):
    study = order_study(general_visit, doctor)
    login(doctor).post(f"/orders/item/{study.order_item.pk}/cancel/")
    study.refresh_from_db()
    assert study.status == "cancelled"


# ---------- pregnancy ----------


def test_due_date_is_280_days_from_the_lmp_including_leap_years(patient):
    leap = PregnancyEpisode.objects.create(patient=patient, lmp=datetime.date(2024, 2, 29))
    assert leap.edd == datetime.date(2024, 12, 5)
    leap.status = "ended"
    leap.save()
    month_end = PregnancyEpisode.objects.create(patient=patient, lmp=datetime.date(2025, 1, 31))
    assert month_end.edd == datetime.date(2025, 11, 7)
    assert month_end.gestation(datetime.date(2025, 3, 7)) == (5, 0)  # 35 days
    assert month_end.gestation(datetime.date(2025, 3, 10)) == (5, 3)


def test_ultrasound_due_date_moves_the_gestation(patient):
    episode = PregnancyEpisode.objects.create(patient=patient, lmp=datetime.date(2025, 1, 1))
    episode.edd = episode.edd + datetime.timedelta(days=7)
    episode.save()
    assert episode.gestation(datetime.date(2025, 2, 5)) == (4, 0)  # a week behind the LMP count


def test_only_one_active_pregnancy_per_patient(patient):
    PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(days=40))
    with pytest.raises(IntegrityError), transaction.atomic():
        PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(days=10))


def test_three_antenatal_contacts_show_gestation_next_visit_and_missed(login, midwife, patient, catalogue):
    lmp = timezone.localdate() - datetime.timedelta(days=24 * 7)
    login(midwife).post(
        f"/maternity/patient/{patient.pk}/start/",
        {"lmp": lmp.isoformat(), "previous_pregnancies": 1, "previous_births": 1},
    )
    episode = PregnancyEpisode.objects.get()
    for offset in (8, 14, 20):
        day = lmp + datetime.timedelta(weeks=offset)
        login(midwife).post(
            f"/maternity/episode/{episode.pk}/contact/",
            {"date": day.isoformat(), "bp_systolic": 118, "bp_diastolic": 76, "weight_kg": "62.0"},
        )
    contacts = list(episode.contacts.order_by("number"))
    assert [c.number for c in contacts] == [1, 2, 3]
    assert [c.weeks for c in contacts] == [8, 14, 20]
    assert episode.gestation_label == "24 weeks 0 days"
    row, due, status = episode.next_contact()
    assert row.contact_number == 4 and row.due_at == 30 and status == "upcoming"
    # contact 4 is due at 30 weeks; at 33 weeks with only 3 contacts it is missed
    assert episode.next_contact(lmp + datetime.timedelta(weeks=33))[2] == "missed"


def test_a_changed_schedule_changes_the_due_list(login, midwife, patient, catalogue):
    lmp = timezone.localdate() - datetime.timedelta(weeks=20)
    episode = PregnancyEpisode.objects.create(patient=patient, lmp=lmp)
    AntenatalContact.objects.create(episode=episode, date=lmp + datetime.timedelta(weeks=10))
    assert episode.next_contact()[2] == "due" or episode.next_contact()[2] == "missed"
    ContactSchedule.objects.filter(care="antenatal", contact_number=2).update(due_at=36, grace_days=7)
    assert episode.next_contact()[2] == "upcoming"
    assert "Nothing due" in login(midwife).get("/maternity/due/").content.decode()
    ContactSchedule.objects.filter(care="antenatal", contact_number=2).update(due_at=20)
    assert patient.full_name in login(midwife).get("/maternity/due/").content.decode()


def test_danger_signs_and_high_blood_pressure_flag_the_woman(patient):
    episode = PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(weeks=12))
    assert not episode.has_danger_flag
    AntenatalContact.objects.create(episode=episode, bp_systolic=150, bp_diastolic=95)
    assert episode.has_danger_flag


def test_antenatal_contacts_are_never_edited(patient):
    episode = PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(weeks=12))
    contact = AntenatalContact.objects.create(episode=episode, weight_kg=60)
    contact.weight_kg = 70
    with pytest.raises(PermissionError):
        contact.save()
    with pytest.raises(PermissionError):
        contact.delete()


def test_delivery_creates_a_linked_newborn_and_postnatal_follow_up(login, midwife, patient, catalogue):
    episode = PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(weeks=41))
    born = timezone.now() - datetime.timedelta(days=2)
    delivery = record_delivery(
        episode, midwife, baby_sex="F", delivered_at=born, mode="vaginal", mother_outcome="Well", birth_weight_kg="3.2"
    )
    baby = delivery.baby
    episode.refresh_from_db()
    assert baby.mother == patient and baby.hospital_number != patient.hospital_number
    assert baby.last_name == patient.last_name and baby.date_of_birth == timezone.localtime(born).date()
    assert episode.status == "delivered"
    pending = {who: (row.contact_number, status) for who, _p, row, _d, status in delivery.pending_contacts()}
    assert pending["mother"][0] == 1 and pending["baby"][0] == 1
    client = login(midwife)
    client.post(
        f"/maternity/delivery/{delivery.pk}/postnatal/baby/",
        {"date": timezone.localdate().isoformat(), "weight_kg": "3.1", "findings": "Feeding well"},
    )
    client.post(
        f"/maternity/delivery/{delivery.pk}/postnatal/mother/",
        {"date": timezone.localdate().isoformat(), "bp_systolic": 120, "bp_diastolic": 80},
    )
    assert delivery.postnatal_contacts.count() == 2
    assert {who: row.contact_number for who, _p, row, _d, _s in delivery.pending_contacts()} == {"mother": 2, "baby": 2}


def test_stillbirth_creates_no_baby_record(midwife, patient):
    episode = PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(weeks=38))
    delivery = record_delivery(episode, midwife, delivered_at=timezone.now(), baby_outcome="stillbirth")
    assert delivery.baby is None and Patient.objects.filter(mother=patient).count() == 0


def test_a_closed_pregnancy_cannot_deliver_twice(midwife, patient):
    episode = PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(weeks=38))
    record_delivery(episode, midwife, baby_sex="M", delivered_at=timezone.now())
    with pytest.raises(ValidationError):
        record_delivery(episode, midwife, baby_sex="M", delivered_at=timezone.now())


def test_pregnancy_data_is_private_to_midwives_doctors_and_admins(
    login, patient, midwife, doctor, nurse, reception, dentist, lab, admin_user
):
    episode = PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(weeks=20))
    AntenatalContact.objects.create(episode=episode, hiv_status="positive")
    url = f"/maternity/episode/{episode.pk}/"
    for user in (midwife, doctor, admin_user):
        assert login(user).get(url).status_code == 200
    for user in (nurse, reception, dentist, lab):
        assert login(user).get(url).status_code == 403
        page = login(user).get(f"/patients/{patient.pk}/")
        assert b"positive" not in page.content and b"Pregnancy record" not in page.content
    AuditLog.objects.filter(action="view").delete()
    login(midwife).get(url)
    assert {"maternity.PregnancyEpisode", "maternity.AntenatalContact"} <= set(
        AuditLog.objects.filter(action="view").values_list("model_label", flat=True)
    )


def test_newborn_page_links_back_to_the_mother(login, midwife, doctor, patient):
    episode = PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(weeks=40))
    baby = record_delivery(episode, midwife, baby_sex="M", delivered_at=timezone.now()).baby
    page = login(doctor).get(f"/patients/{baby.pk}/").content.decode()
    assert "Mother:" in page and patient.hospital_number in page


# ---------- dental ----------


@pytest.fixture
def dental_visit(patient, dentist, reception, catalogue):
    visit = clinic_visit(patient, "dental")
    visit.move_to(Status.WAITING_DOCTOR, reception)
    visit.move_to(Status.WITH_DOCTOR, dentist)
    return visit


def test_fdi_numbers_are_checked(dental_visit, dentist, patient):
    from apps.dental.models import DentalVisit

    consultation = Consultation.objects.create(appointment=dental_visit, doctor=dentist)
    dv = DentalVisit.objects.create(consultation=consultation, dentist=dentist)
    for bad in (0, 19, 49, 50, 56, 66, 86, 99):
        with pytest.raises(ValidationError):
            ToothRecord.objects.create(patient=patient, visit=dv, tooth=bad, condition="decayed")
    for good in (11, 48, 51, 85):
        ToothRecord.objects.create(patient=patient, visit=dv, tooth=good, condition="decayed")


def test_tooth_records_are_append_only_and_history_stays_readable(login, dentist, dental_visit, patient):
    client = login(dentist)
    client.post(f"/dental/visit/{dental_visit.pk}/tooth/", {"tooth": 16, "surface": "O", "condition": "decayed"})
    client.post(f"/dental/visit/{dental_visit.pk}/tooth/", {"tooth": 16, "surface": "O", "condition": "filled"})
    records = list(ToothRecord.objects.order_by("id"))
    assert [r.condition for r in records] == ["decayed", "filled"]
    with pytest.raises(PermissionError):
        records[0].save()
    with pytest.raises(PermissionError):
        records[0].delete()
    from apps.dental.models import current_chart

    assert current_chart(patient)[16][0][2] == "filled"  # latest wins, earlier entry still stored


def test_invalid_tooth_is_refused_by_the_screen(login, dentist, dental_visit):
    login(dentist).post(
        f"/dental/visit/{dental_visit.pk}/tooth/", {"tooth": 99, "surface": "O", "condition": "decayed"}
    )
    assert not ToothRecord.objects.exists()


def test_dental_case_plan_consent_filling_extraction_and_billing_ready(login, dentist, doctor, dental_visit, patient):
    client = login(dentist)
    fill = DentalProcedure.objects.get(code="FILL")
    extract = DentalProcedure.objects.get(code="EXTRACT")
    fill.default_price, extract.default_price = 2500, 1500
    fill.save()
    extract.save()
    client.post(f"/dental/visit/{dental_visit.pk}/plan/item/", {"tooth": 16, "procedure": fill.pk})
    client.post(f"/dental/visit/{dental_visit.pk}/plan/item/", {"tooth": 38, "procedure": extract.pk})
    plan = TreatmentPlan.objects.get()
    assert plan.total_estimate == 4000
    filling, extraction = plan.items.all()
    # no work before the patient has seen the estimate and agreed
    client.post(f"/dental/visit/{dental_visit.pk}/plan/item/{filling.pk}/action/", {"action": "complete"})
    filling.refresh_from_db()
    assert filling.status == "planned"
    client.post(f"/dental/visit/{dental_visit.pk}/plan/{plan.pk}/consent/", {"agreed_by": ""})
    plan.refresh_from_db()
    assert not plan.has_consent
    client.post(f"/dental/visit/{dental_visit.pk}/plan/{plan.pk}/consent/", {"agreed_by": "Wanjiru Kamau (patient)"})
    plan.refresh_from_db()
    assert plan.has_consent and plan.consent_by.startswith("Wanjiru") and plan.consent_recorded_by == dentist
    client.post(
        f"/dental/visit/{dental_visit.pk}/plan/item/{filling.pk}/action/",
        {"action": "complete", "anaesthetic_note": "Lignocaine 2%"},
    )
    client.post(f"/dental/visit/{dental_visit.pk}/plan/item/{extraction.pk}/action/", {"action": "complete"})
    filling.refresh_from_db()
    extraction.refresh_from_db()
    plan.refresh_from_db()
    assert filling.anaesthetic_note == "Lignocaine 2%" and filling.ready_for_billing and extraction.ready_for_billing
    assert plan.status == "closed"
    assert b"Ready for billing" in client.get(f"/dental/visit/{dental_visit.pk}/").content
    assert login(doctor).get(f"/dental/visit/{dental_visit.pk}/").status_code == 200  # doctors can view
    assert (
        login(doctor)
        .post(f"/dental/visit/{dental_visit.pk}/tooth/", {"tooth": 16, "surface": "O", "condition": "decayed"})
        .status_code
        == 403
    )


def test_declined_items_are_never_ready_for_billing_or_counted(dentist, dental_visit, patient, catalogue):
    plan = TreatmentPlan.objects.create(patient=patient, created_by=dentist)
    item = TreatmentPlanItem.objects.create(
        plan=plan, tooth=11, procedure=DentalProcedure.objects.get(code="FILL"), estimate=2000
    )
    item.decline()
    assert not item.ready_for_billing and plan.total_estimate == 0


def test_a_plan_item_needs_a_procedure_and_a_valid_tooth(patient, dentist, catalogue):
    plan = TreatmentPlan.objects.create(patient=patient, created_by=dentist)
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        TreatmentPlanItem.objects.create(plan=plan, tooth=11, procedure_id=None, estimate=100, status="done")
    with pytest.raises(ValidationError):
        TreatmentPlanItem.objects.create(
            plan=plan, tooth=77 + 30, procedure=DentalProcedure.objects.get(code="FILL"), estimate=1
        )


def test_adding_work_asks_for_consent_again(login, dentist, dental_visit, patient):
    client = login(dentist)
    fill = DentalProcedure.objects.get(code="FILL")
    client.post(f"/dental/visit/{dental_visit.pk}/plan/item/", {"tooth": 16, "procedure": fill.pk})
    plan = TreatmentPlan.objects.get()
    client.post(f"/dental/visit/{dental_visit.pk}/plan/{plan.pk}/consent/", {"agreed_by": "Patient"})
    client.post(f"/dental/visit/{dental_visit.pk}/plan/item/", {"tooth": 17, "procedure": fill.pk})
    plan.refresh_from_db()
    assert not plan.has_consent


def test_dental_patient_with_an_open_pregnancy_sees_a_notice(login, dentist, dental_visit, patient):
    assert b"Pregnancy notice" not in login(dentist).get(f"/dental/visit/{dental_visit.pk}/").content
    PregnancyEpisode.objects.create(patient=patient, lmp=timezone.localdate() - datetime.timedelta(weeks=10))
    page = login(dentist).get(f"/dental/visit/{dental_visit.pk}/").content
    assert b"Pregnancy notice" in page and b"Blood group" not in page  # the notice only, not the record


def test_dental_xray_goes_through_imaging_and_allergy_warning_applies(login, dentist, dental_visit, patient, catalogue):
    patient.allergies = "Amoxicillin"
    patient.save()
    item = ImagingItem.objects.get(test__code="DX-PA")
    assert item.modality.room == "Dental X-ray room"
    Consultation.objects.create(appointment=dental_visit, doctor=dentist)
    login(dentist).post(
        f"/imaging/visit/{dental_visit.pk}/order/",
        {"studies": [item.pk], "clinical_reason": "Toothache", "may_be_pregnant": "no"},
    )
    assert ImagingStudy.objects.get().item == item
    from apps.pharmacy_rx.models import Drug

    drug = Drug.objects.get(generic_name="Amoxicillin")
    login(dentist).post(
        f"/prescriptions/visit/{dental_visit.pk}/prescribe/",
        {"drug": drug.pk, "dose": "1", "frequency": "tds", "days": 5, "quantity": 15},
    )
    from apps.pharmacy_rx.models import PrescriptionItem

    assert not PrescriptionItem.objects.exists()  # blocked until acknowledged


# ---------- timeline and seed ----------


def test_timeline_shows_scans_with_their_report(
    login, doctor, general_visit, radiographer, radiologist, patient, catalogue
):
    study = order_study(general_visit, doctor)
    study.move_to("arrived", radiographer)
    study.move_to("images_done", radiographer)
    save_report(study, radiologist, "Clear", "Normal chest radiograph")
    page = login(doctor).get(f"/patients/{patient.pk}/").content.decode()
    assert "Chest X-ray" in page and "Normal chest radiograph" in page


def test_seed_creates_the_modalities_schedule_and_procedures(catalogue):
    assert ContactSchedule.objects.filter(care="antenatal").count() == 8
    assert ContactSchedule.objects.filter(care="postnatal").count() == 4
    codes = set(ImagingItem.objects.values_list("modality__dicom_code", flat=True))
    assert {"DX", "US", "CT", "MR", "MG"} <= codes
    assert DentalProcedure.objects.count() >= 6
    assert DeliverySummary.objects.count() == 0
