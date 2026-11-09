import pytest

from apps.clinical.models import Visit, VisitDiagnosis
from apps.core.models import AuditLog
from apps.orders.models import Order, Result
from apps.pharmacy_rx.models import Prescription

pytestmark = pytest.mark.django_db


def visit_of(entry):
    return Visit.objects.get(queue_entry=entry)


def test_prescription_print_lists_drugs_and_is_logged(with_doctor, doctor, paracetamol, as_user):
    v = visit_of(with_doctor)
    Prescription.objects.create(visit=v, drug=paracetamol, dose="1 g", units_per_dose=2, days=3, prescribed_by=doctor)
    r = as_user(doctor).get(f"/rx/visits/{v.pk}/print/")
    assert r.status_code == 200 and b"Paracetamol" in r.content and b"18" in r.content
    assert AuditLog.objects.filter(action="print", reason="Prescription").exists()


def test_prescription_print_roles(with_doctor, pharmacist, reception, cashier, as_user):
    v = visit_of(with_doctor)
    assert as_user(pharmacist).get(f"/rx/visits/{v.pk}/print/").status_code == 200
    assert as_user(reception).get(f"/rx/visits/{v.pk}/print/").status_code == 403
    assert as_user(cashier).get(f"/rx/visits/{v.pk}/print/").status_code == 403


def test_lab_result_print_shows_the_corrected_result(with_doctor, lab, doctor, malaria, as_user):
    o = Order.objects.create(visit=visit_of(with_doctor), test=malaria, status="resulted")
    first = Result.objects.create(order=o, value="Negative", flag="normal", resulted_by=lab)
    Result.objects.create(
        order=o, value="Positive", flag="abnormal", resulted_by=lab, corrects=first, comment="Re-read"
    )
    r = as_user(doctor).get(f"/orders/lab/{o.pk}/print/")
    assert r.status_code == 200 and b"Positive" in r.content and b"corrects an earlier" in r.content
    assert AuditLog.objects.filter(action="print", reason="Lab result").exists()


def test_lab_result_print_needs_a_result_and_the_right_role(with_doctor, malaria, doctor, reception, as_user):
    o = Order.objects.create(visit=visit_of(with_doctor), test=malaria)
    assert as_user(doctor).get(f"/orders/lab/{o.pk}/print/").status_code == 302
    assert as_user(reception).get(f"/orders/lab/{o.pk}/print/").status_code == 403


def test_referral_letter(with_doctor, doctor, icd, as_user):
    v = visit_of(with_doctor)
    d = as_user(doctor)
    assert d.get(f"/clinical/visits/{v.pk}/referral/").status_code == 302  # not a referral yet
    VisitDiagnosis.objects.create(visit=v, icd10=icd, added_by=doctor)
    d.post(
        f"/clinical/consult/{with_doctor.pk}/",
        {"outcome": "refer", "referred_to": "County Referral Hospital", "action": "finish"},
    )
    r = d.get(f"/clinical/visits/{v.pk}/referral/")
    assert r.status_code == 200 and b"County Referral Hospital" in r.content and b"B50.9" in r.content
    assert AuditLog.objects.filter(action="print", reason="Referral letter").exists()


def test_referral_letter_roles(with_doctor, reception, pharmacist, as_user):
    v = visit_of(with_doctor)
    for u in (reception, pharmacist):
        assert as_user(u).get(f"/clinical/visits/{v.pk}/referral/").status_code == 403
