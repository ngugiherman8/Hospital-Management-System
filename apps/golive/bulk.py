"""Bulk import of lists (drugs, prices, tests, suppliers, wards and so on) from a CSV file.

Every importer follows the same rules, so an administrator can trust them:
- a file is first checked as a preview and rolled back; nothing is saved until the person confirms;
- a row that cannot be saved is reported with its row number and reason, and does not stop the other rows;
- each importer matches on a natural key (a code, a name), so running the same file twice does not duplicate;
- every change is audited like any other change, under the administrator who confirmed it.
"""

import csv
import datetime
import io
import os
import re
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
YES = {"yes", "y", "true", "1", "x"}
NO = {"no", "n", "false", "0"}


class BulkError(Exception):
    """A problem with the whole file (wrong columns, too big), not one row."""


class RowError(Exception):
    """A problem with one row."""


@dataclass
class Result:
    row: int
    label: str
    status: str  # created, updated, skipped, error
    message: str = ""


@dataclass
class Importer:
    key: str
    title: str
    blurb: str
    columns: list  # (name, required, note)
    sample: list
    handler: object = None
    label: object = None
    prepare: object = None
    whole_file: object = None
    order: int = 0
    starter: list = field(default_factory=list)  # common ready-made rows, filled in at the bottom of this file

    @property
    def required(self):
        return [c[0] for c in self.columns if c[1]]

    @property
    def names(self):
        return [c[0] for c in self.columns]


# ---------------------------------------------------------------- helpers


def norm_header(text):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def money(text, what="amount"):
    try:
        value = Decimal((text or "").replace(",", "").strip())
    except InvalidOperation:
        raise RowError(f"'{text}' is not a valid {what}") from None
    if value < 0:
        raise RowError(f"{what} cannot be negative")
    return value.quantize(Decimal("0.01"))


def number(text, what):
    try:
        return Decimal((text or "").replace(",", "").strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise RowError(f"'{text}' is not a valid {what}") from None


def whole(text, what):
    text = (text or "").strip()
    if not text.isdigit():
        raise RowError(f"{what} must be a whole number")
    return int(text)


def flag(text, default=False):
    t = (text or "").strip().lower()
    if not t:
        return default
    if t in YES:
        return True
    if t in NO:
        return False
    raise RowError(f"'{text}' should be yes or no")


def choice(text, choices, what):
    wanted = (text or "").strip().lower()
    for value, label in choices:
        if wanted in (str(value).lower(), str(label).lower()):
            return value
    raise RowError(f"'{text}' is not a valid {what} (use one of: {', '.join(str(v) for v, _ in choices)})")


def parse_date(text, what):
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise RowError(f"{what} must look like 2027-03-31")


# ---------------------------------------------------------------- handlers


def _drug_label(r):
    return " ".join(p for p in (r.get("generic_name"), r.get("strength"), r.get("form")) if p)


def do_drug(r, ctx):
    from apps.billing.models import ItemPrice, PayerKind, ServiceItem
    from apps.pharmacy_rx.models import Drug

    if not r.get("generic_name"):
        raise RowError("generic_name is missing")
    price = money(r.get("price") or "0", "price")
    defaults = {"price": price, "route": r.get("route") or "Oral"}
    if r.get("reorder_level"):
        defaults["reorder_level"] = whole(r["reorder_level"], "reorder_level")
    defaults["is_active"] = flag(r.get("active"), True)
    drug, created = Drug.objects.update_or_create(
        generic_name=r["generic_name"], strength=r.get("strength", ""), form=r.get("form", ""), defaults=defaults
    )
    item, _ = ServiceItem.objects.get_or_create(
        drug=drug, defaults={"code": f"D-{drug.pk}", "name": str(drug), "category": ServiceItem.Category.DRUG}
    )
    if price > 0:
        ItemPrice.objects.update_or_create(service=item, payer_kind=PayerKind.CASH, defaults={"amount": price})
    return ("created" if created else "updated"), ""


def do_supplier(r, ctx):
    from apps.stock.models import Supplier

    if not r.get("name"):
        raise RowError("name is missing")
    if r.get("email"):
        try:
            validate_email(r["email"])
        except ValidationError:
            raise RowError(f"'{r['email']}' is not a valid email address") from None
    defaults = {k: r[k] for k in ("phone", "email") if r.get(k)}
    if r.get("active"):
        defaults["is_active"] = flag(r["active"], True)
    _, created = Supplier.objects.update_or_create(name=r["name"], defaults=defaults)
    return ("created" if created else "updated"), ""


def do_price(r, ctx):
    from apps.billing.models import ItemPrice, PayerKind, ServiceItem

    code = r.get("code", "")
    if not code:
        raise RowError("code is missing")
    payer = choice(r.get("payer") or "cash", PayerKind.choices, "payer")
    amount = money(r["amount"], "amount") if r.get("amount") else None
    item = ServiceItem.objects.filter(code=code).first()
    note = ""
    if item is None:
        if not (r.get("name") and r.get("category")):
            raise RowError(f"'{code}' is a new item, so name and category are needed")
        category = choice(r["category"], ServiceItem.Category.choices, "category")
        item = ServiceItem.objects.create(code=code, name=r["name"], category=category)
        note = "new price-list item"
    if amount is None:
        if note:
            return "created", note + ", no price set yet (add it later)"
        return "skipped", "no amount given, existing price left as it is"
    _, created = ItemPrice.objects.update_or_create(service=item, payer_kind=payer, defaults={"amount": amount})
    return ("created" if created else "updated"), note


def do_test(r, ctx):
    from apps.billing.models import ItemPrice, PayerKind, ServiceItem
    from apps.imaging.models import ImagingItem, ImagingModality
    from apps.orders.models import TestCatalogue

    if not r.get("code") or not r.get("name"):
        raise RowError("code and name are both needed")
    kind = choice(r.get("kind") or "lab", TestCatalogue.Kind.choices, "kind")
    price = money(r.get("price") or "0", "price")
    values = {
        "name": r["name"],
        "kind": kind,
        "sample_type": r.get("sample_type", ""),
        "unit": r.get("unit", ""),
        "normal_range": r.get("normal_range", ""),
        "price": price,
    }
    for key in ("ref_low", "ref_high"):
        if r.get(key):
            values[key] = number(r[key], key)
    modality = None
    if kind == "imaging":
        if not r.get("modality"):
            raise RowError("an imaging test needs a modality (add modalities first with the Imaging modalities import)")
        modality = ImagingModality.objects.filter(name__iexact=r["modality"]).first()
        if modality is None:
            raise RowError(f"modality '{r['modality']}' does not exist yet")
    test, created = TestCatalogue.objects.update_or_create(code=r["code"], defaults=values)
    category = ServiceItem.Category.IMAGING if kind == "imaging" else ServiceItem.Category.LAB
    item, _ = ServiceItem.objects.get_or_create(
        test=test, defaults={"code": f"T-{test.code}", "name": test.name, "category": category}
    )
    if price > 0:
        ItemPrice.objects.update_or_create(service=item, payer_kind=PayerKind.CASH, defaults={"amount": price})
    if modality:
        ImagingItem.objects.update_or_create(
            test=test,
            defaults={
                "modality": modality,
                "body_part": r.get("body_part", ""),
                "preparation": r.get("preparation", ""),
                "uses_radiation": flag(r.get("uses_radiation")),
                "uses_contrast": flag(r.get("uses_contrast")),
            },
        )
    return ("created" if created else "updated"), ""


def do_modality(r, ctx):
    from apps.imaging.models import ImagingModality

    if not r.get("name"):
        raise RowError("name is missing")
    code = (r.get("dicom_code") or "").upper()
    if not code or len(code) > 4:
        raise RowError("dicom_code is needed and has at most 4 letters (DX, US, CT, MR, MG, RF...)")
    _, created = ImagingModality.objects.update_or_create(
        name=r["name"],
        defaults={
            "dicom_code": code,
            "room": r.get("room", ""),
            "can_send_dicom": flag(r.get("can_send_dicom")),
            "scanner_address": r.get("scanner_address", ""),
        },
    )
    return ("created" if created else "updated"), ""


def do_ward(r, ctx):
    from apps.wards.models import Bed, Ward

    if not r.get("ward"):
        raise RowError("ward is missing")
    kind = choice(r.get("kind") or "general", Ward.Kind.choices, "ward kind")
    ward, created = Ward.objects.get_or_create(name=r["ward"], defaults={"kind": kind})
    spec = r.get("beds", "")
    numbers = (
        [str(i) for i in range(1, int(spec) + 1)]
        if spec.isdigit()
        else [b.strip() for b in spec.split(";") if b.strip()]
    )
    if len(numbers) > 500:
        raise RowError("more than 500 beds in one ward is probably a mistake")
    added = sum(Bed.objects.get_or_create(ward=ward, number=n)[1] for n in numbers)
    return ("created" if created else "updated"), f"{added} beds added, {ward.beds.count()} in total"


def do_department(r, ctx):
    from apps.hr.models import Department

    if not r.get("name"):
        raise RowError("name is missing")
    _, created = Department.objects.update_or_create(
        name=r["name"], defaults={"is_active": flag(r.get("active"), True)}
    )
    return ("created" if created else "updated"), ""


def do_leave_type(r, ctx):
    from apps.hr.models import LeaveType

    if not r.get("name"):
        raise RowError("name is missing")
    days = number(r.get("annual_days") or "0", "annual_days")
    if days < 0 or days > 366:
        raise RowError("annual_days must be between 0 and 366")
    _, created = LeaveType.objects.update_or_create(
        name=r["name"],
        defaults={
            "annual_days": days,
            "uses_balance": flag(r.get("uses_balance"), True),
            "is_paid": flag(r.get("is_paid"), True),
        },
    )
    return ("created" if created else "updated"), ""


def prepare_opening_stock(ctx, rows):
    from apps.stock.models import GoodsReceipt, Supplier

    if rows:
        supplier, _ = Supplier.objects.get_or_create(name="Opening stock (counted)")
        today = datetime.date.today()
        ctx["state"]["receipt"] = GoodsReceipt.objects.create(
            supplier=supplier, supplier_invoice=f"OPENING-{today:%Y%m%d}", received_by=ctx["user"], received_on=today
        )


def do_opening_stock(r, ctx):
    from apps.pharmacy_rx.models import Drug
    from apps.stock import services as stock_services
    from apps.stock.models import StockBatch

    drug = Drug.objects.filter(
        generic_name__iexact=r.get("generic_name", ""),
        strength__iexact=r.get("strength", ""),
        form__iexact=r.get("form", ""),
    ).first()
    if drug is None:
        raise RowError(
            "no drug in the drug list matches this name, strength and form "
            "(import the drugs first, and match them exactly)"
        )
    expiry = parse_date(r.get("expiry_date"), "expiry_date")
    qty = whole(r.get("quantity"), "quantity")
    if qty <= 0:
        raise RowError("quantity must be above zero")
    batch = r.get("batch_number", "")
    if not batch:
        raise RowError("batch_number is missing")
    if StockBatch.objects.filter(drug=drug, batch_number=batch, expiry_date=expiry).exists():
        return "skipped", "this batch is already in stock, not added again"
    stock_services.receive_stock(
        ctx["state"]["receipt"], drug, batch, expiry, qty, money(r.get("unit_cost") or "0", "unit_cost"), ctx["user"]
    )
    return "created", f"{qty} counted"


def patients_whole_file(text, user, dry_run):
    """Patients use the existing, careful importer (duplicate matching, legacy ids)."""
    from .importer import run_import

    handle, path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        outcomes = run_import(path, source="bulk upload", dry_run=dry_run, estimate_from_age=True)
    finally:
        os.unlink(path)
    results = []
    for i, o in enumerate(outcomes, start=1):
        if o.outcome == "rejected":
            status = "error"
        elif o.outcome in ("held_for_review", "already_imported"):
            status = "skipped"
        elif o.outcome == "created":
            status = "created"
        else:
            status = "updated"
        note = o.note or ""
        if o.outcome == "held_for_review":
            note = "possible duplicate, held for a person to decide. " + note
        elif o.outcome.startswith("linked"):
            note = "same person already registered, linked. " + note
        results.append(Result(i, f"{o.legacy_id} {o.hospital_number}".strip(), status, note.strip()))
    return results


# ---------------------------------------------------------------- registry

IMPORTERS = [
    Importer(
        "patients",
        "Patients",
        "Register many patients at once. Duplicates are linked, not created twice.",
        [
            ("legacy_id", True, "Any unique id from your old list or register"),
            ("first_name", True, ""),
            ("last_name", True, ""),
            ("date_of_birth", True, "2019-05-02 or 02/05/2019"),
            ("sex", True, "M or F"),
            ("id_number", False, "National ID or passport"),
            ("phone", False, "0712 345 678"),
            ("residence", False, ""),
            ("kin_name", False, "Next of kin"),
            ("kin_phone", False, ""),
            ("allergies", False, ""),
            ("payer", False, "cash, sha, insurer or corporate"),
            ("payer_name", False, "Insurer or company"),
            ("member_number", False, ""),
        ],
        [
            {
                "legacy_id": "P001",
                "first_name": "Wanjiru",
                "last_name": "Kamau",
                "date_of_birth": "1992-03-14",
                "sex": "F",
                "phone": "0712000001",
                "payer": "sha",
            }
        ],
        whole_file=patients_whole_file,
        order=1,
    ),
    Importer(
        "drugs",
        "Drugs",
        "The drug list with prices and reorder levels. Also creates each drug's price-list item.",
        [
            ("generic_name", True, "Amoxicillin"),
            ("strength", False, "500 mg"),
            ("form", False, "Capsule"),
            ("route", False, "Oral (default)"),
            ("price", False, "Cash price per unit"),
            ("reorder_level", False, "Whole number"),
            ("active", False, "yes or no (default yes)"),
        ],
        [
            {
                "generic_name": "Amoxicillin",
                "strength": "500 mg",
                "form": "Capsule",
                "route": "Oral",
                "price": "15",
                "reorder_level": "100",
                "active": "yes",
            }
        ],
        do_drug,
        _drug_label,
        order=2,
    ),
    Importer(
        "opening_stock",
        "Opening stock",
        "Counted stock to start with. The drug must already be in the drug list.",
        [
            ("generic_name", True, ""),
            ("strength", False, "Must match the drug list"),
            ("form", False, "Must match the drug list"),
            ("batch_number", True, ""),
            ("expiry_date", True, "2027-03-31"),
            ("quantity", True, "Whole number"),
            ("unit_cost", False, ""),
        ],
        [
            {
                "generic_name": "Amoxicillin",
                "strength": "500 mg",
                "form": "Capsule",
                "batch_number": "B1023",
                "expiry_date": "2027-03-31",
                "quantity": "500",
                "unit_cost": "9",
            }
        ],
        do_opening_stock,
        lambda r: f"{_drug_label(r)} batch {r.get('batch_number', '')}",
        prepare_opening_stock,
        order=3,
    ),
    Importer(
        "suppliers",
        "Suppliers",
        "Companies you buy stock from.",
        [("name", True, ""), ("phone", False, ""), ("email", False, ""), ("active", False, "yes or no")],
        [{"name": "Kenya Medical Supplies", "phone": "0711000000", "email": "orders@example.com", "active": "yes"}],
        do_supplier,
        lambda r: r.get("name", ""),
        order=4,
    ),
    Importer(
        "prices",
        "Price list",
        "Prices for each kind of payer. A new code needs a name and category.",
        [
            ("code", True, "The price-list code"),
            ("name", False, "Needed only for a new item"),
            ("category", False, "registration, consultation, lab, imaging, dental, bed, drug, antenatal, other"),
            ("payer", False, "cash (default), sha, insurer or corporate"),
            ("amount", False, "Price in shillings. Leave blank to add the item now and price it later"),
        ],
        [
            {
                "code": "CONS-GEN",
                "name": "General consultation",
                "category": "consultation",
                "payer": "cash",
                "amount": "500",
            }
        ],
        do_price,
        lambda r: f"{r.get('code', '')} ({r.get('payer') or 'cash'})",
        order=5,
    ),
    Importer(
        "modalities",
        "Imaging modalities",
        "The imaging machines and rooms. Add these before imaging tests.",
        [
            ("name", True, "X-ray"),
            ("dicom_code", True, "DX, US, CT, MR, MG, RF"),
            ("room", False, ""),
            ("can_send_dicom", False, "yes or no"),
            ("scanner_address", False, ""),
        ],
        [{"name": "X-ray", "dicom_code": "DX", "room": "Room 1", "can_send_dicom": "no"}],
        do_modality,
        lambda r: r.get("name", ""),
        order=6,
    ),
    Importer(
        "tests",
        "Lab and imaging test catalogue",
        "Lab tests and imaging studies with prices and normal ranges.",
        [
            ("code", True, "Unique, up to 20 characters"),
            ("name", True, ""),
            ("kind", False, "lab (default) or imaging"),
            ("sample_type", False, "Blood, urine..."),
            ("unit", False, "mmol/L"),
            ("ref_low", False, "Normal low"),
            ("ref_high", False, "Normal high"),
            ("normal_range", False, "Text such as Negative"),
            ("price", False, "Cash price"),
            ("modality", False, "Imaging only: must exist"),
            ("body_part", False, "Imaging only"),
            ("preparation", False, "Imaging only"),
            ("uses_radiation", False, "Imaging only: yes or no"),
            ("uses_contrast", False, "Imaging only: yes or no"),
        ],
        [
            {"code": "FBC", "name": "Full blood count", "kind": "lab", "sample_type": "Blood (EDTA)", "price": "800"},
            {
                "code": "CXR",
                "name": "Chest X-ray",
                "kind": "imaging",
                "price": "1500",
                "modality": "X-ray",
                "body_part": "Chest",
                "uses_radiation": "yes",
            },
        ],
        do_test,
        lambda r: f"{r.get('code', '')} {r.get('name', '')}".strip(),
        order=7,
    ),
    Importer(
        "wards",
        "Wards and beds",
        "Wards with their beds. beds can be a number (12) or bed names separated by ;",
        [
            ("ward", True, ""),
            ("kind", False, "general, maternity, paediatric or private"),
            ("beds", False, "12  or  A1;A2;A3"),
        ],
        [
            {"ward": "Male ward", "kind": "general", "beds": "12"},
            {"ward": "Maternity", "kind": "maternity", "beds": "M1;M2;M3"},
        ],
        do_ward,
        lambda r: r.get("ward", ""),
        order=8,
    ),
    Importer(
        "departments",
        "Departments",
        "Hospital departments for staff records.",
        [("name", True, ""), ("active", False, "yes or no")],
        [{"name": "Outpatient", "active": "yes"}],
        do_department,
        lambda r: r.get("name", ""),
        order=9,
    ),
    Importer(
        "leave_types",
        "Leave types",
        "Types of staff leave and how many days each earns per year.",
        [
            ("name", True, ""),
            ("annual_days", False, "Days per year"),
            ("uses_balance", False, "yes or no"),
            ("is_paid", False, "yes or no"),
        ],
        [{"name": "Annual leave", "annual_days": "21", "uses_balance": "yes", "is_paid": "yes"}],
        do_leave_type,
        lambda r: r.get("name", ""),
        order=10,
    ),
]
BY_KEY = {i.key: i for i in IMPORTERS}


# ---------------------------------------------------------------- engine


def decode(raw):
    if len(raw) > MAX_BYTES:
        raise BulkError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Split it into smaller files.")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise BulkError("The file could not be read. Save it as CSV (UTF-8) and try again.")


def read_table(importer, text):
    reader = csv.DictReader(io.StringIO(text))
    headers = [norm_header(h) for h in (reader.fieldnames or [])]
    if not headers:
        raise BulkError("The file is empty or has no header row.")
    missing = [c for c in importer.required if c not in headers and importer.key != "patients"]
    if missing:
        raise BulkError(
            "These columns are missing from the first row: "
            + ", ".join(missing)
            + ". Download the template to see them."
        )
    rows = []
    for number_, raw in enumerate(reader, start=2):
        values = {norm_header(k): (v or "").strip() for k, v in raw.items() if k is not None}
        if not any(values.values()):
            continue
        rows.append((number_, values))
        if len(rows) > MAX_ROWS:
            raise BulkError(f"More than {MAX_ROWS} rows. Split the file into smaller ones.")
    if not rows:
        raise BulkError("The file has a header but no data rows.")
    return rows


def run(importer, text, user, dry_run):
    """Returns a list of Result. With dry_run nothing is saved."""
    if importer.whole_file:
        read_table(importer, text)  # same size and shape checks
        return importer.whole_file(text, user, dry_run)
    rows = read_table(importer, text)
    results = []

    class _Rollback(Exception):
        pass

    try:
        with transaction.atomic():
            ctx = {"user": user, "state": {}}
            if importer.prepare:
                importer.prepare(ctx, rows)
            for number_, r in rows:
                label = (importer.label(r) if importer.label else "") or f"row {number_}"
                try:
                    with transaction.atomic():
                        status, message = importer.handler(r, ctx)
                except RowError as exc:
                    results.append(Result(number_, label, "error", str(exc)))
                except ValidationError as exc:
                    results.append(Result(number_, label, "error", " ".join(exc.messages)))
                except Exception as exc:  # noqa: BLE001 - one bad row must not stop the rest
                    results.append(
                        Result(number_, label, "error", f"could not be saved ({type(exc).__name__}: {str(exc)[:120]})")
                    )
                else:
                    results.append(Result(number_, label, status, message))
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    return results


def summarise(results):
    counts = {"created": 0, "updated": 0, "skipped": 0, "error": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def template_csv(importer, starter=False):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(importer.names)
    for sample in importer.starter if starter else importer.sample:
        writer.writerow([sample.get(n, "") for n in importer.names])
    return "\ufeff" + out.getvalue()


def errors_csv(results):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["row", "item", "problem"])
    for r in results:
        if r.status == "error":
            writer.writerow([r.row, r.label, r.message])
    return "\ufeff" + out.getvalue()


# ---------------------------------------------------------------- starter data and downloads
from .bulk_starter import STARTER  # noqa: E402

for _imp in IMPORTERS:
    _imp.starter = STARTER.get(_imp.key, [])

README = """HOSPITAL MANAGEMENT SYSTEM: BULK IMPORT FILES

starter-data/   Common data most hospitals need, ready to upload. Open in Excel, add your prices, then upload.
blank-templates/  Empty files with one example row, for your own lists.

Upload them in this order (Administration, Bulk import):
  1 imaging modalities  2 drugs  3 tests  4 prices  5 wards  6 departments  7 leave types
  then your own: suppliers, opening stock, patients.

Please read before you upload:
- Prices are blank on purpose. Every hospital sets its own. Fill the price columns, then upload again:
  the import updates existing items and does not duplicate them.
- Reference ranges are usual adult values. Your clinical lead must confirm them before go-live.
- The drug list follows the groups of the Kenya Essential Medicines List. Check it against your formulary,
  delete rows you do not stock, and add the ones that are missing.
- Bed counts are blank. Add them in the wards file (12, or names like A1;A2;A3) and upload again.
- Save as CSV (UTF-8) if you edit in Excel.
"""


def starter_zip():
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README)
        for i in sorted(IMPORTERS, key=lambda i: i.order):
            z.writestr(f"blank-templates/{i.order:02d}-{i.key}.csv", template_csv(i))
            if i.starter:
                z.writestr(f"starter-data/{i.order:02d}-{i.key}.csv", template_csv(i, starter=True))
    return buffer.getvalue()
