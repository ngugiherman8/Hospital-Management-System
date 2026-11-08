"""Every new screen: anonymous users are sent to sign-in, and roles that may not use it get 403."""

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def urls(with_doctor, malaria, paracetamol, beds):
    from apps.clinical.models import Visit
    from apps.orders.models import Order

    visit = Visit.objects.get(queue_entry=with_doctor)
    order = Order.objects.create(visit=visit, test=malaria)
    e, v, o, p = with_doctor.pk, visit.pk, order.pk, visit.patient.pk
    return {
        "triage": f"/clinical/triage/{e}/",
        "call_in": f"/clinical/call-in/{e}/",
        "consult": f"/clinical/consult/{e}/",
        "visit": f"/clinical/visits/{v}/",
        "history": f"/clinical/patients/{p}/history/",
        "diagnosis_add": f"/clinical/visits/{v}/diagnoses/add/",
        "note_add": f"/clinical/visits/{v}/notes/add/",
        "vitals_add": f"/clinical/visits/{v}/vitals/add/",
        "send_to_lab": f"/clinical/visits/{v}/send-to-lab/",
        "order_add": f"/orders/visits/{v}/add/",
        "lab": "/orders/lab/",
        "lab_collect": f"/orders/lab/{o}/collect/",
        "lab_result": f"/orders/lab/{o}/result/",
        "rx_add": f"/rx/visits/{v}/add/",
        "rx_list": "/rx/",
        "wards": "/wards/",
        "admit": f"/wards/admit/visit/{v}/",
        "transfer": f"/wards/visits/{v}/transfer/",
        "discharge": f"/wards/visits/{v}/discharge/",
        "bed_ready": f"/wards/beds/{beds[0].pk}/ready/",
    }


ALL = [
    "triage", "call_in", "consult", "visit", "history", "diagnosis_add", "note_add", "vitals_add", "send_to_lab",
    "order_add", "lab", "lab_collect", "lab_result", "rx_add", "rx_list", "wards", "admit", "transfer", "discharge",
    "bed_ready",
]  # fmt: skip

POST_ONLY = {"call_in", "diagnosis_add", "note_add", "vitals_add", "send_to_lab", "order_add", "lab_collect",
             "lab_result", "rx_add", "transfer", "discharge", "bed_ready"}  # fmt: skip


@pytest.mark.parametrize("name", ALL)
def test_anonymous_redirected_to_sign_in(client, urls, name):
    r = client.post(urls[name]) if name in POST_ONLY else client.get(urls[name])
    assert r.status_code == 302 and "/login/" in r.url


# (screen, method, roles that must be refused)
FORBIDDEN = [
    ("triage", "post", ["doctor", "receptionist", "lab", "pharmacist", "cashier"]),
    ("triage", "get", ["lab", "pharmacist", "cashier"]),
    ("call_in", "post", ["nurse", "receptionist", "lab", "pharmacist", "cashier"]),
    ("consult", "get", ["receptionist", "lab", "pharmacist", "cashier"]),
    ("consult", "post", ["nurse", "receptionist", "lab", "pharmacist", "cashier"]),
    ("diagnosis_add", "post", ["nurse", "receptionist", "lab", "pharmacist", "cashier"]),
    ("order_add", "post", ["nurse", "receptionist", "lab", "pharmacist", "cashier"]),
    ("rx_add", "post", ["nurse", "receptionist", "lab", "pharmacist", "cashier"]),
    ("send_to_lab", "post", ["nurse", "receptionist", "lab", "pharmacist", "cashier"]),
    ("note_add", "post", ["receptionist", "lab", "pharmacist", "cashier"]),
    ("vitals_add", "post", ["receptionist", "lab", "pharmacist", "cashier"]),
    ("lab", "get", ["receptionist", "pharmacist", "cashier"]),
    ("lab_collect", "post", ["doctor", "nurse", "receptionist", "pharmacist", "cashier"]),
    ("lab_result", "post", ["receptionist", "pharmacist", "cashier"]),
    ("rx_list", "get", ["receptionist", "lab", "cashier"]),
    ("wards", "get", ["lab", "pharmacist", "cashier"]),
    ("admit", "get", ["nurse", "receptionist", "lab", "pharmacist", "cashier"]),
    ("transfer", "post", ["doctor", "receptionist", "lab", "pharmacist", "cashier"]),
    ("discharge", "post", ["nurse", "receptionist", "lab", "pharmacist", "cashier"]),
    ("bed_ready", "post", ["doctor", "receptionist", "lab", "pharmacist", "cashier"]),
]


@pytest.mark.parametrize("name,method,roles", FORBIDDEN)
def test_wrong_roles_get_403(urls, as_user, name, method, roles):
    from tests.conftest import make_user

    for role in roles:
        user = make_user(f"u-{role}-{name}", role)
        r = getattr(as_user(user), method)(urls[name])
        assert r.status_code == 403, f"{role} should not {method} {name}"


def test_admin_can_open_everything(urls, as_user, admin_user):
    c = as_user(admin_user)
    for name in ("consult", "lab", "rx_list", "wards", "history", "visit"):
        assert c.get(urls[name]).status_code == 200, name


def test_pharmacist_prescription_list_shows_no_notes(as_user, pharmacist, with_doctor, paracetamol, doctor):
    from apps.clinical.models import ClinicalNote, Visit
    from apps.pharmacy_rx.models import Prescription

    visit = Visit.objects.get(queue_entry=with_doctor)
    ClinicalNote.objects.create(visit=visit, kind="history", text="Secret history text", author=doctor)
    Prescription.objects.create(
        visit=visit, drug=paracetamol, dose="1 g", units_per_dose=2, days=3, prescribed_by=doctor
    )
    body = as_user(pharmacist).get("/rx/").content.decode()
    assert "Paracetamol" in body and "Secret history text" not in body


def test_reception_cashier_pharmacist_never_see_notes_or_results(as_user, with_doctor, doctor, malaria):
    from apps.clinical.models import ClinicalNote, Visit
    from apps.orders.models import Order, Result
    from tests.conftest import make_user

    visit = Visit.objects.get(queue_entry=with_doctor)
    ClinicalNote.objects.create(visit=visit, kind="history", text="Confidential note", author=doctor)
    o = Order.objects.create(visit=visit, test=malaria, status="resulted")
    Result.objects.create(order=o, value="Positive", flag="abnormal")
    for role in ("receptionist", "cashier", "pharmacist"):
        c = as_user(make_user(f"x-{role}", role))
        for url in (f"/clinical/visits/{visit.pk}/", f"/clinical/patients/{visit.patient.pk}/history/"):
            r = c.get(url)
            assert r.status_code == 403
            assert b"Confidential note" not in r.content and b"Positive" not in r.content
