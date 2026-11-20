"""Load the hospital's own set-up data from CSV files in one folder. Every loader is safe to run again.

users.csv          username, first_name, last_name, role, department, phone, email
prices.csv         code, name, category, payer, amount          (name and category only needed for a new item)
drugs.csv          generic_name, strength, form, route, reorder_level, price
tests.csv          code, name, kind, sample_type, unit, ref_low, ref_high, normal_range, price
wards.csv          ward, kind, beds                             (beds: a number, or numbers separated by ;)
opening_stock.csv  generic_name, strength, form, batch_number, expiry_date, quantity, unit_cost
"""

import csv
import datetime
import secrets
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Role, User
from apps.billing.models import ItemPrice, PayerKind, ServiceItem
from apps.orders.models import TestCatalogue
from apps.pharmacy_rx.models import Drug
from apps.stock import services as stock_services
from apps.stock.models import GoodsReceipt, StockBatch, Supplier
from apps.wards.models import Bed, Ward

LOADERS = ["users", "tests", "drugs", "prices", "wards", "opening_stock"]  # drugs and tests first: prices refer to them


class SetupError(Exception):
    pass


def _rows(folder, name):
    path = Path(folder) / f"{name}.csv"
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        reader.fieldnames = [(h or "").strip().lower().replace(" ", "_") for h in (reader.fieldnames or [])]
        return [
            {k: (v or "").strip() for k, v in row.items()}
            for row in reader
            if any((v or "").strip() for v in row.values())
        ]


def _money(text, where):
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise SetupError(f"{where}: '{text}' is not an amount") from None
    if value < 0:
        raise SetupError(f"{where}: an amount cannot be negative")
    return value.quantize(Decimal("0.01"))


def _choice(text, choices, where, what):
    wanted = text.strip().lower()
    for value, label in choices:
        if wanted in (str(value).lower(), str(label).lower()):
            return value
    raise SetupError(f"{where}: '{text}' is not a valid {what} (use one of: {', '.join(str(v) for v, _ in choices)})")


def load_users(rows, log, credentials):
    for n, r in enumerate(rows, start=2):
        where = f"users.csv row {n}"
        username = r.get("username", "")
        if not username:
            raise SetupError(f"{where}: username is missing")
        role = _choice(r.get("role", ""), Role.choices, where, "role")
        user = User.objects.filter(username=username).first()
        if user:
            log(f"user {username}: already exists, left as it is")
            continue
        password = secrets.token_urlsafe(9)
        user = User(
            username=username,
            first_name=r.get("first_name", ""),
            last_name=r.get("last_name", ""),
            role=role,
            department=r.get("department", ""),
            phone=r.get("phone", ""),
            email=r.get("email", ""),
            is_staff=role == Role.ADMIN,
            must_change_password=True,
        )
        user.set_password(password)
        user.save()
        credentials.append((username, role, password))
        log(f"user {username}: created as {role}")


def load_tests(rows, log):
    for n, r in enumerate(rows, start=2):
        where = f"tests.csv row {n}"
        if not r.get("code") or not r.get("name"):
            raise SetupError(f"{where}: code and name are both needed")
        kind = _choice(r.get("kind") or "lab", TestCatalogue.Kind.choices, where, "kind")
        values = {
            "name": r["name"],
            "kind": kind,
            "sample_type": r.get("sample_type", ""),
            "unit": r.get("unit", ""),
            "normal_range": r.get("normal_range", ""),
            "price": _money(r.get("price") or "0", where),
        }
        for key in ("ref_low", "ref_high"):
            if r.get(key):
                values[key] = _money(r[key], where) if "-" not in r[key] else Decimal(r[key])
        test, created = TestCatalogue.objects.update_or_create(code=r["code"], defaults=values)
        category = ServiceItem.Category.IMAGING if kind == "imaging" else ServiceItem.Category.LAB
        item, _ = ServiceItem.objects.get_or_create(
            test=test, defaults={"code": f"T-{test.code}", "name": test.name, "category": category}
        )
        if values["price"] > 0:
            ItemPrice.objects.update_or_create(
                service=item, payer_kind=PayerKind.CASH, defaults={"amount": values["price"]}
            )
        log(f"test {test.code}: {'created' if created else 'updated'}")


def load_drugs(rows, log):
    for n, r in enumerate(rows, start=2):
        where = f"drugs.csv row {n}"
        if not r.get("generic_name"):
            raise SetupError(f"{where}: generic_name is missing")
        key = {"generic_name": r["generic_name"], "strength": r.get("strength", ""), "form": r.get("form", "")}
        price = _money(r.get("price") or "0", where)
        reorder = r.get("reorder_level", "")
        if reorder and not reorder.isdigit():
            raise SetupError(f"{where}: reorder_level must be a whole number")
        defaults = {"price": price, "route": r.get("route") or "Oral"}
        if reorder:
            defaults["reorder_level"] = int(reorder)
        drug, created = Drug.objects.update_or_create(**key, defaults=defaults)
        item, _ = ServiceItem.objects.get_or_create(
            drug=drug, defaults={"code": f"D-{drug.pk}", "name": str(drug), "category": ServiceItem.Category.DRUG}
        )
        if price > 0:
            ItemPrice.objects.update_or_create(service=item, payer_kind=PayerKind.CASH, defaults={"amount": price})
        log(f"drug {drug}: {'created' if created else 'updated'}")


def load_prices(rows, log):
    for n, r in enumerate(rows, start=2):
        where = f"prices.csv row {n}"
        code = r.get("code", "")
        if not code:
            raise SetupError(f"{where}: code is missing")
        payer = _choice(r.get("payer") or "cash", PayerKind.choices, where, "payer")
        amount = _money(r.get("amount", ""), where)
        item = ServiceItem.objects.filter(code=code).first()
        if item is None:
            if not (r.get("name") and r.get("category")):
                raise SetupError(f"{where}: '{code}' is new, so name and category are needed")
            category = _choice(r["category"], ServiceItem.Category.choices, where, "category")
            item = ServiceItem.objects.create(code=code, name=r["name"], category=category)
        price, created = ItemPrice.objects.update_or_create(service=item, payer_kind=payer, defaults={"amount": amount})
        log(f"price {code} ({payer}): {amount}")


def load_wards(rows, log):
    for n, r in enumerate(rows, start=2):
        where = f"wards.csv row {n}"
        if not r.get("ward"):
            raise SetupError(f"{where}: ward is missing")
        kind = _choice(r.get("kind") or "general", Ward.Kind.choices, where, "ward kind")
        ward, _ = Ward.objects.get_or_create(name=r["ward"], defaults={"kind": kind})
        spec = r.get("beds", "")
        numbers = (
            [str(i) for i in range(1, int(spec) + 1)]
            if spec.isdigit()
            else [b.strip() for b in spec.split(";") if b.strip()]
        )
        added = 0
        for number in numbers:
            _, created = Bed.objects.get_or_create(ward=ward, number=number)
            added += created
        log(f"ward {ward.name}: {added} beds added, {ward.beds.count()} in total")


def load_opening_stock(rows, log, counted_on, user=None):
    if not rows:
        return
    supplier, _ = Supplier.objects.get_or_create(name="Opening stock (counted)")
    receipt = GoodsReceipt.objects.create(
        supplier=supplier, supplier_invoice=f"OPENING-{counted_on:%Y%m%d}", received_by=user, received_on=counted_on
    )
    loaded = 0
    for n, r in enumerate(rows, start=2):
        where = f"opening_stock.csv row {n}"
        drug = Drug.objects.filter(
            generic_name__iexact=r.get("generic_name", ""),
            strength__iexact=r.get("strength", ""),
            form__iexact=r.get("form", ""),
        ).first()
        if drug is None:
            raise SetupError(
                f"{where}: drug '{r.get('generic_name')} {r.get('strength')} {r.get('form')}' "
                "is not in drugs.csv or the drug list"
            )
        try:
            expiry = datetime.date.fromisoformat(r.get("expiry_date", ""))
        except ValueError:
            raise SetupError(f"{where}: expiry_date must look like 2027-03-31") from None
        qty = r.get("quantity", "")
        if not qty.isdigit() or int(qty) <= 0:
            raise SetupError(f"{where}: quantity must be a whole number above zero")
        batch_number = r.get("batch_number", "")
        if not batch_number:
            raise SetupError(f"{where}: batch_number is missing")
        if StockBatch.objects.filter(drug=drug, batch_number=batch_number, expiry_date=expiry).exists():
            log(f"stock {drug} batch {batch_number}: already there, not added again")
            continue
        try:
            stock_services.receive_stock(
                receipt, drug, batch_number, expiry, int(qty), _money(r.get("unit_cost") or "0", where), user
            )
        except ValidationError as exc:
            raise SetupError(f"{where}: {'; '.join(exc.messages)}") from None
        loaded += 1
        log(f"stock {drug} batch {batch_number}: {qty} counted")


def load_folder(folder, counted_on=None, user=None, log=print):
    """Load every file found, all or nothing. Returns the new accounts as (username, role, password)."""
    credentials = []
    folder = Path(folder)
    if not folder.is_dir():
        raise SetupError(f"{folder} is not a folder")
    counted_on = counted_on or datetime.date.today()
    with transaction.atomic():
        found = 0
        for name in LOADERS:
            rows = _rows(folder, name)
            if rows is None:
                continue
            found += 1
            if name == "users":
                load_users(rows, log, credentials)
            elif name == "opening_stock":
                load_opening_stock(rows, log, counted_on, user)
            else:
                globals()[f"load_{name}"](rows, log)
        if not found:
            raise SetupError("No set-up files found. Expected: " + ", ".join(f"{n}.csv" for n in LOADERS))
    return credentials
