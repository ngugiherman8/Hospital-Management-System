import csv
import datetime

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.billing.models import ServiceItem
from apps.pharmacy_rx.models import Drug
from apps.stock.services import on_hand
from apps.wards.models import Bed

FUTURE = (datetime.date.today() + datetime.timedelta(days=400)).isoformat()


def put(folder, name, header, rows):
    with open(folder / f"{name}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def folder(tmp_path):
    put(tmp_path, "users", ["username", "first_name", "last_name", "role", "department"],
        [["jwanjiru", "Jane", "Wanjiru", "cashier", "Cashier"], ["pkamau", "Peter", "Kamau", "Doctor", "OPD"]])  # fmt: skip
    put(tmp_path, "drugs", ["generic_name", "strength", "form", "reorder_level", "price"],
        [["Amoxicillin", "500mg", "Capsule", "100", "15"]])  # fmt: skip
    put(tmp_path, "tests", ["code", "name", "kind", "price"], [["MAL", "Malaria slide", "lab", "150"]])
    put(tmp_path, "prices", ["code", "name", "category", "payer", "amount"],
        [["REG", "Registration", "registration", "cash", "200"], ["REG", "", "", "sha", "0"]])  # fmt: skip
    put(tmp_path, "wards", ["ward", "kind", "beds"], [["Ward A", "general", "4"], ["Maternity", "maternity", "M1;M2"]])
    put(tmp_path, "opening_stock", ["generic_name", "strength", "form", "batch_number", "expiry_date", "quantity", "unit_cost"],
        [["Amoxicillin", "500mg", "Capsule", "B-1", FUTURE, "250", "8"]])  # fmt: skip
    return tmp_path


@pytest.mark.django_db
def test_load_everything_and_run_it_again(folder):
    out = str(folder / "new.csv")
    call_command("load_setup", str(folder), "--credentials-out", out)
    jane = User.objects.get(username="jwanjiru")
    assert jane.must_change_password and jane.role == "cashier"
    assert User.objects.get(username="pkamau").role == "doctor"
    creds = list(csv.DictReader(open(out)))
    assert len(creds) == 2 and jane.check_password(
        next(c for c in creds if c["username"] == "jwanjiru")["temporary_password"]
    )
    drug = Drug.objects.get()
    assert drug.reorder_level == 100 and on_hand(drug) == 250
    assert ServiceItem.objects.get(drug=drug).price_for("cash") == 15
    assert ServiceItem.objects.get(code="REG").price_for("cash") == 200
    assert ServiceItem.objects.get(code="REG").price_for("sha") == 0
    assert Bed.objects.count() == 6
    call_command("load_setup", str(folder), "--credentials-out", str(folder / "second.csv"))
    assert on_hand(drug) == 250 and Bed.objects.count() == 6 and User.objects.count() == 2
    assert not (folder / "second.csv").exists()  # no new accounts, so no password file


@pytest.mark.django_db
def test_one_bad_row_loads_nothing(folder):
    put(folder, "opening_stock", ["generic_name", "strength", "form", "batch_number", "expiry_date", "quantity"],
        [["Amoxicillin", "500mg", "Capsule", "B-1", FUTURE, "250"], ["Nothing", "", "", "B-2", FUTURE, "5"]])  # fmt: skip
    with pytest.raises(CommandError, match="row 3"):
        call_command("load_setup", str(folder))
    assert User.objects.count() == 0 and Drug.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,header,row,message",
    [
        ("users", ["username", "role"], ["x", "wizard"], "not a valid role"),
        ("prices", ["code", "payer", "amount"], ["NEW", "cash", "10"], "name and category are needed"),
        ("prices", ["code", "payer", "amount"], ["REG", "cash", "ten"], "not an amount"),
        ("prices", ["code", "payer", "amount"], ["REG", "cash", "-5"], "negative"),
        ("wards", ["ward", "kind", "beds"], ["", "general", "2"], "ward is missing"),
    ],
)
def test_bad_values_are_explained(tmp_path, name, header, row, message):
    put(tmp_path, name, header, [row])
    with pytest.raises(CommandError, match=message):
        call_command("load_setup", str(tmp_path))


@pytest.mark.django_db
def test_expired_opening_stock_is_refused(folder):
    put(folder, "opening_stock", ["generic_name", "strength", "form", "batch_number", "expiry_date", "quantity"],
        [["Amoxicillin", "500mg", "Capsule", "OLD", "2020-01-01", "10"]])  # fmt: skip
    with pytest.raises(CommandError, match="expired"):
        call_command("load_setup", str(folder))


@pytest.mark.django_db
def test_new_account_must_choose_a_password_first(client, folder):
    call_command("load_setup", str(folder), "--credentials-out", str(folder / "n.csv"))
    temp = next(csv.DictReader(open(folder / "n.csv")))
    client.login(username=temp["username"], password=temp["temporary_password"])
    resp = client.get("/patients/")
    assert resp.status_code == 302 and resp.url.startswith("/password/")
    resp = client.post(
        "/password/",
        {
            "old_password": temp["temporary_password"],
            "new_password1": "A-much-longer-1pass",
            "new_password2": "A-much-longer-1pass",
        },
    )
    assert resp.status_code == 302
    assert client.get("/patients/").status_code == 200
