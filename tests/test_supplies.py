import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role
from apps.hr.models import Department
from apps.supplies import services
from apps.supplies.models import Asset, MaintenanceTicket, Requisition, StoreItem, StoreMovement
from conftest import make_user


@pytest.fixture
def store(db):
    dept = Department.objects.create(name="Ward")
    gloves = StoreItem.objects.create(code="GLV", name="Gloves", category="medical", unit="box", reorder_level=5)
    paper = StoreItem.objects.create(code="A4", name="A4 paper", category="stationery", unit="ream", reorder_level=2)
    keeper = make_user(Role.STOREKEEPER, username="keeper")
    boss = make_user(Role.MANAGER, username="boss")
    nurse = make_user(Role.NURSE, username="nurse")
    return dict(dept=dept, gloves=gloves, paper=paper, keeper=keeper, boss=boss, nurse=nurse)


def stock(s, item, n):
    services.receive(s[item], n, Decimal("100"), "DN-1", s["keeper"])


def test_receive_updates_stock_and_last_price(store):
    stock(store, "gloves", 20)
    assert services.on_hand(store["gloves"]) == 20
    store["gloves"].refresh_from_db()
    assert store["gloves"].unit_cost == 100
    with pytest.raises(ValidationError):
        services.receive(store["gloves"], 0, None, "", store["keeper"])


def test_levels_flag_low_stock_and_value(store):
    stock(store, "gloves", 4)
    rows = {r["item"].code: r for r in services.levels()}
    assert rows["GLV"]["low"] and rows["GLV"]["value"] == 400
    assert rows["A4"]["low"]  # nothing in stock at all


def test_movements_cannot_be_edited_or_wrong_signed(store):
    stock(store, "gloves", 5)
    move = StoreMovement.objects.get()
    move.quantity = 99
    with pytest.raises(PermissionError):
        move.save()
    with pytest.raises(PermissionError):
        StoreMovement.objects.all().delete()
    with pytest.raises(ValidationError):
        StoreMovement.objects.create(item=store["gloves"], kind="issue", quantity=3)
    with pytest.raises(ValidationError):
        StoreMovement.objects.create(item=store["gloves"], kind="adjust", quantity=-1, reason=" ")


def test_adjustment_cannot_take_more_than_is_there(store):
    stock(store, "gloves", 3)
    with pytest.raises(ValidationError, match="Only 3"):
        services.adjust(store["gloves"], -5, "Lost", store["keeper"])
    services.adjust(store["gloves"], -2, "Damaged in flood", store["keeper"], written_off=True)
    assert services.on_hand(store["gloves"]) == 1
    assert StoreMovement.objects.filter(kind="writeoff").count() == 1


def make_req(s, qty=4):
    return services.raise_requisition(
        s["dept"], s["nurse"], "Weekly", [(s["gloves"], qty), (s["gloves"], 1), (s["paper"], 2)]
    )


def test_requisition_flow(store):
    stock(store, "gloves", 10)
    stock(store, "paper", 1)
    req = make_req(store)
    assert req.number.startswith("REQ-") and req.lines.get(item=store["gloves"]).quantity == 5  # merged
    with pytest.raises(ValidationError, match="Only an approved"):
        services.issue(req, {}, store["keeper"])
    with pytest.raises(ValidationError, match="your own"):
        services.decide(req, True, store["nurse"])
    with pytest.raises(ValidationError, match="why"):
        services.decide(req, False, store["boss"])
    services.decide(req, True, store["boss"])
    lines = {ln.item.code: ln for ln in req.lines.all()}
    with pytest.raises(ValidationError, match="only 1"):
        services.issue(req, {lines["GLV"].pk: 5, lines["A4"].pk: 2}, store["keeper"])  # only 1 ream in stock
    with pytest.raises(ValidationError, match="between 0 and"):
        services.issue(req, {lines["GLV"].pk: 6}, store["keeper"])
    services.issue(req, {lines["GLV"].pk: 5, lines["A4"].pk: 1}, store["keeper"])
    req.refresh_from_db()
    assert req.status == "issued"
    assert services.on_hand(store["gloves"]) == 5 and services.on_hand(store["paper"]) == 0
    assert StoreMovement.objects.filter(kind="issue", department=store["dept"]).count() == 2
    with pytest.raises(ValidationError):
        services.issue(req, {lines["GLV"].pk: 1}, store["keeper"])  # cannot issue twice


def test_empty_requisition_and_cancel(store):
    with pytest.raises(ValidationError, match="at least one"):
        services.raise_requisition(store["dept"], store["nurse"], "", [])
    req = make_req(store)
    services.cancel(req, store["nurse"])
    with pytest.raises(ValidationError):
        services.decide(req, True, store["boss"])


def asset(**kw):
    kw.setdefault("name", "Autoclave")
    kw.setdefault("category", "medical")
    return Asset.objects.create(**kw)


def test_asset_tags_are_in_sequence(db):
    assert [asset().tag, asset().tag] == ["AST-00001", "AST-00002"]


def test_straight_line_value(db):
    today = timezone.localdate()
    a = asset(
        purchase_date=today - datetime.timedelta(days=int(365.25 * 2.5)), purchase_cost=100000, useful_life_years=5
    )
    assert abs(a.book_value() - Decimal("50000")) < Decimal("50")
    old = asset(purchase_date=today - datetime.timedelta(days=4000), purchase_cost=100000, useful_life_years=5)
    assert old.book_value() == 0


def test_service_due_dates(db):
    today = timezone.localdate()
    a = asset(service_interval_months=6, last_serviced=today - datetime.timedelta(days=200))
    assert a.service_state() == "overdue"
    b = asset(service_interval_months=6, last_serviced=today - datetime.timedelta(days=170))
    assert b.service_state() == "soon"
    assert asset().service_state() == ""
    assert asset(
        service_interval_months=12, last_serviced=datetime.date(2024, 2, 29)
    ).next_service_due == datetime.date(2025, 2, 28)
    assert {a.pk, b.pk} <= {x.pk for x in services.assets_needing_service()}


def test_breakdown_puts_it_under_repair_and_closing_restores_it(store):
    a = asset()
    t1 = services.open_ticket(a, "breakdown", "urgent", "Will not heat", store["nurse"])
    a.refresh_from_db()
    assert a.status == "under_repair"
    t2 = services.open_ticket(a, "breakdown", "normal", "Door seal", store["nurse"])
    services.close_ticket(t1, "Element replaced", Decimal("2500"), store["keeper"])
    a.refresh_from_db()
    assert a.status == "under_repair"  # second fault still open
    services.close_ticket(t2, "Seal replaced", 0, store["keeper"])
    a.refresh_from_db()
    assert a.status == "in_service"
    with pytest.raises(ValidationError, match="already closed"):
        services.close_ticket(t1, "again", 0, store["keeper"])


def test_planned_service_resets_the_clock(store):
    a = asset(service_interval_months=6, last_serviced=timezone.localdate() - datetime.timedelta(days=300))
    t = services.open_ticket(a, "planned", "normal", "Annual service", store["keeper"])
    a.refresh_from_db()
    assert a.status == "in_service"  # planned work does not take it out of service
    services.close_ticket(t, "Serviced", 5000, store["keeper"])
    a.refresh_from_db()
    assert a.last_serviced == timezone.localdate() and a.service_state() == ""


def test_closing_needs_a_note_and_disposal_rules(store):
    a = asset()
    t = services.open_ticket(a, "breakdown", "normal", "Broken", store["nurse"])
    with pytest.raises(ValidationError, match="Write what"):
        services.close_ticket(t, " ", 0, store["keeper"])
    with pytest.raises(ValidationError, match="open maintenance"):
        services.dispose(a, "Beyond repair", store["keeper"])
    services.close_ticket(t, "Cancelled: user error", 0, store["keeper"], cancelled=True)
    with pytest.raises(ValidationError, match="why"):
        services.dispose(a, " ", store["keeper"])
    services.dispose(a, "Beyond repair", store["keeper"])
    with pytest.raises(ValidationError, match="disposed"):
        services.open_ticket(a, "breakdown", "normal", "x", store["nurse"])


def client(user):
    c = Client()
    c.force_login(user)
    return c


def test_pages_and_roles(store):
    stock(store, "gloves", 10)
    nurse, keeper, boss = client(store["nurse"]), client(store["keeper"]), client(store["boss"])
    other = client(make_user(Role.DOCTOR, username="other"))
    assert nurse.get("/supplies/").status_code == 403
    assert nurse.get("/supplies/assets/").status_code == 403
    assert keeper.get("/supplies/").status_code == 200 and boss.get("/supplies/").status_code == 200
    assert boss.get("/supplies/receive/").status_code == 403  # the manager approves, the storekeeper handles stock
    assert nurse.get("/supplies/requisitions/new/").status_code == 200
    r = nurse.post(
        "/supplies/requisitions/new/",
        {"department": store["dept"].pk, "item_1": store["gloves"].pk, "qty_1": 3},
    )
    req = Requisition.objects.get()
    assert r.status_code == 302 and req.requested_by == store["nurse"]
    bad = nurse.post("/supplies/requisitions/new/", {"department": store["dept"].pk, "item_1": store["gloves"].pk})
    assert bad.status_code == 200 and Requisition.objects.count() == 1  # item without a number
    assert other.get(f"/supplies/requisitions/{req.pk}/").status_code == 403  # not theirs
    assert nurse.get(f"/supplies/requisitions/{req.pk}/").status_code == 200
    assert nurse.post(f"/supplies/requisitions/{req.pk}/approve/").status_code == 403
    assert keeper.post(f"/supplies/requisitions/{req.pk}/approve/").status_code == 403
    boss.post(f"/supplies/requisitions/{req.pk}/approve/")
    line = req.lines.get()
    assert nurse.post(f"/supplies/requisitions/{req.pk}/issue/", {f"qty_{line.pk}": "3"}).status_code == 403
    keeper.post(f"/supplies/requisitions/{req.pk}/issue/", {f"qty_{line.pk}": "3"})
    assert Requisition.objects.get().status == "issued" and services.on_hand(store["gloves"]) == 7
    assert [len(c.get("/supplies/requisitions/").context["rows"]) for c in (nurse, keeper, other)] == [1, 1, 0]


def test_anyone_can_report_a_fault_by_tag(store):
    a = asset()
    nurse = client(store["nurse"])
    r = nurse.post("/supplies/fault/", {"asset_tag": a.tag.lower(), "priority": "urgent", "description": "Sparks"})
    assert r.status_code == 302 and MaintenanceTicket.objects.get().reported_by == store["nurse"]
    assert (
        nurse.post("/supplies/fault/", {"asset_tag": "NOPE", "priority": "normal", "description": "x"}).status_code
        == 200
    )
    assert nurse.get("/supplies/maintenance/").status_code == 403
    keeper = client(store["keeper"])
    t = MaintenanceTicket.objects.get()
    keeper.post(f"/supplies/maintenance/{t.pk}/start/", {"assigned_to": "Biomed Ltd"})
    keeper.post(f"/supplies/maintenance/{t.pk}/close/", {"resolution": "Cable replaced", "cost": "1200"})
    t.refresh_from_db()
    assert t.status == "done" and t.cost == 1200 and t.assigned_to == "Biomed Ltd"
    bad = keeper.post(f"/supplies/maintenance/{t.pk}/close/", {"resolution": "x", "cost": "abc"})
    assert bad.status_code == 302
