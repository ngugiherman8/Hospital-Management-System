"""Bring old patient records in, safely and repeatably.

Rules that keep it safe to run more than once:
- every row needs a stable `legacy_id`; a row already imported is never imported twice;
- what staff typed into the system is never overwritten (with `update_blank`, only empty fields are filled);
- the same person twice (same ID number, or same name and birth date) is linked, not duplicated;
- a weaker match (same surname and birth date, say) is held for a person to decide, using a `decision` column:
  `new` (a different person) or `same_as:<legacy_id or hospital number>`.
"""

import csv
import datetime
import re
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.patients.models import Patient, normalize_phone

from .models import LegacyRecord

ALIASES = {
    "legacy_id": ["legacy_id", "id", "old_id", "record_id", "file_id"],
    "legacy_number": ["legacy_number", "file_number", "card_number", "old_number", "reg_no", "patient_no"],
    "first_name": ["first_name", "firstname", "given_name"],
    "last_name": ["last_name", "lastname", "surname", "family_name"],
    "name": ["name", "full_name", "patient_name"],
    "date_of_birth": ["date_of_birth", "dob", "birth_date", "birthdate"],
    "age": ["age"],
    "sex": ["sex", "gender"],
    "id_number": ["id_number", "national_id", "id_no", "idnumber"],
    "id_type": ["id_type"],
    "phone": ["phone", "mobile", "telephone", "tel", "phone_number"],
    "email": ["email"],
    "residence": ["residence", "address", "village", "location", "town"],
    "kin_name": ["kin_name", "next_of_kin", "nok", "nok_name"],
    "kin_relationship": ["kin_relationship", "nok_relationship", "relationship"],
    "kin_phone": ["kin_phone", "nok_phone", "nok_contact"],
    "allergies": ["allergies", "allergy"],
    "payer": ["payer", "payment_type"],
    "payer_name": ["payer_name", "insurer", "company"],
    "member_number": ["member_number", "insurance_number", "member_no"],
    "decision": ["decision"],
}
JUNK = {"", "n/a", "na", "nil", "none", "unknown", "-", "--", "0", "null"}
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%y", "%d-%m-%y"]


def _header_key(text):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def clean_text(value):
    value = re.sub(r"\s+", " ", (value or "").strip())
    return "" if value.lower() in JUNK else value


def clean_name(value):
    value = clean_text(value)
    if value and (value.isupper() or value.islower()):
        value = re.sub(r"[A-Za-z]+(?:'[A-Za-z]+)?", lambda m: m.group(0).capitalize(), value)
    return value


def parse_date(text):
    text = clean_text(text)
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_sex(text):
    t = clean_text(text).lower()
    if t in ("f", "female", "woman", "girl"):
        return Patient.Sex.FEMALE
    if t in ("m", "male", "man", "boy"):
        return Patient.Sex.MALE
    return ""


def parse_payer(text):
    t = clean_text(text).lower()
    if not t or t in ("cash", "self", "self pay", "mpesa", "m-pesa", "out of pocket"):
        return Patient.Payer.CASH
    if "sha" in t or "sha" == t or "nhif" in t:
        return Patient.Payer.SHA
    if "corp" in t or "company" in t:
        return Patient.Payer.CORPORATE
    if "insur" in t or "private" in t:
        return Patient.Payer.INSURER
    return Patient.Payer.CASH


@dataclass
class Row:
    number: int
    raw: dict
    legacy_id: str = ""
    legacy_number: str = ""
    data: dict = field(default_factory=dict)
    decision: str = ""
    dob_estimated: bool = False
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class Outcome:
    legacy_id: str
    outcome: str
    hospital_number: str = ""
    note: str = ""


def read_rows(path, estimate_from_age=False, today=None):
    today = today or timezone.localdate()
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        keys = {_header_key(h): h for h in (reader.fieldnames or [])}

        def pick(raw, field_name):
            for alias in ALIASES[field_name]:
                if alias in keys:
                    return raw.get(keys[alias]) or ""
            return ""

        rows = []
        for number, raw in enumerate(reader, start=2):  # row 1 is the header
            if not any((v or "").strip() for v in raw.values()):
                continue
            row = Row(number=number, raw=raw, legacy_id=clean_text(pick(raw, "legacy_id")))
            row.decision = clean_text(pick(raw, "decision"))
            first, last = clean_name(pick(raw, "first_name")), clean_name(pick(raw, "last_name"))
            if not (first and last):
                parts = clean_name(pick(raw, "name")).split(" ")
                if parts and parts[0]:
                    first = first or parts[0]
                    last = last or " ".join(parts[1:])
            dob = parse_date(pick(raw, "date_of_birth"))
            if dob is None and estimate_from_age:
                age = clean_text(pick(raw, "age"))
                if age.isdigit() and 0 <= int(age) <= 110:
                    dob = datetime.date(today.year - int(age), 7, 1)
                    row.dob_estimated = True
            sex = parse_sex(pick(raw, "sex"))
            id_number = clean_text(pick(raw, "id_number")).upper()
            payer = parse_payer(pick(raw, "payer"))
            phone_raw = clean_text(pick(raw, "phone"))
            phone = normalize_phone(phone_raw)
            if phone_raw and not phone.startswith("+254"):
                row.warnings.append(f"phone '{phone_raw}' is not a Kenyan mobile number")
            row.data = {
                "first_name": first,
                "last_name": last,
                "date_of_birth": dob,
                "sex": sex,
                "id_number": id_number,
                "id_type": (
                    Patient.IdType.PASSPORT
                    if "passport" in clean_text(pick(raw, "id_type")).lower()
                    else Patient.IdType.BIRTH_CERT
                    if "birth" in clean_text(pick(raw, "id_type")).lower()
                    else Patient.IdType.NATIONAL_ID
                    if id_number
                    else Patient.IdType.NONE
                ),
                "phone": phone,
                "email": clean_text(pick(raw, "email")).lower(),
                "residence": clean_text(pick(raw, "residence")),
                "kin_name": clean_name(pick(raw, "kin_name")),
                "kin_relationship": clean_text(pick(raw, "kin_relationship")),
                "kin_phone": normalize_phone(clean_text(pick(raw, "kin_phone"))),
                "allergies": clean_text(pick(raw, "allergies")),
                "payer": payer,
                "payer_name": clean_text(pick(raw, "payer_name")),
                "member_number": clean_text(pick(raw, "member_number")),
            }
            row.legacy_number = clean_text(pick(raw, "legacy_number"))
            if not row.legacy_id:
                row.problems.append("no legacy_id (every row needs a stable id from the old system)")
            if not first or not last:
                row.problems.append("name is missing (a first and a last name are both needed)")
            if dob is None:
                row.problems.append("date of birth is missing or not readable")
            elif dob > today or dob.year < 1900:
                row.problems.append(f"date of birth {dob} is not possible")
            if not sex:
                row.problems.append("sex is missing or not readable (M or F)")
            rows.append(row)
        return rows


def _strong_match(data, patient):
    if data["id_number"] and patient.id_number and data["id_number"].upper() == patient.id_number.upper():
        return True
    return (
        patient.first_name.lower() == data["first_name"].lower()
        and patient.last_name.lower() == data["last_name"].lower()
        and patient.date_of_birth == data["date_of_birth"]
    )


def _weak_matches(data):
    """People the system already holds who might be this person but are not certain."""
    found = Patient.find_possible_duplicates(
        data["first_name"], data["last_name"], data["date_of_birth"], phone="", id_number=data["id_number"]
    )
    return [p for p in found if not _strong_match(data, p)]


BLANKABLE = [
    "id_number",
    "phone",
    "email",
    "residence",
    "kin_name",
    "kin_relationship",
    "kin_phone",
    "allergies",
    "payer_name",
    "member_number",
]


def _fill_blanks(patient, data):
    changed = []
    for name in BLANKABLE:
        if data.get(name) and not getattr(patient, name):
            setattr(patient, name, data[name])
            changed.append(name)
    if "id_number" in changed and patient.id_type == Patient.IdType.NONE:
        patient.id_type = data["id_type"]
    if changed:
        patient.save()
    return changed


def _resolve_target(ref, linked_this_run):
    """`same_as:` target: a legacy id already linked, or a hospital number."""
    if ref in linked_this_run:
        return linked_this_run[ref]
    record = LegacyRecord.objects.filter(legacy_id=ref).select_related("patient").first()
    if record:
        return record.patient
    return Patient.objects.filter(hospital_number__iexact=ref).first()


def run_import(path, source="", update_blank=False, estimate_from_age=False, dry_run=False):
    """Returns a list of Outcome. Nothing is saved on a dry run."""
    rows = read_rows(path, estimate_from_age=estimate_from_age)
    source = source or path.rsplit("/", 1)[-1]
    outcomes = []
    linked = {}  # legacy_id -> Patient, for rows linked earlier in this same run
    seen_ids = set()

    class _Rollback(Exception):
        pass

    try:
        with transaction.atomic():
            for row in rows:
                out = _one_row(row, source, update_blank, linked, seen_ids)
                outcomes.append(out)
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    return outcomes


def _one_row(row, source, update_blank, linked, seen_ids):
    if row.problems:
        return Outcome(row.legacy_id, "rejected", note="; ".join(row.problems) + f" (row {row.number})")
    if row.legacy_id in seen_ids:
        return Outcome(row.legacy_id, "rejected", note=f"legacy_id appears twice in the file (row {row.number})")
    seen_ids.add(row.legacy_id)
    note = "; ".join(row.warnings)
    existing = LegacyRecord.objects.filter(legacy_id=row.legacy_id).select_related("patient").first()
    if existing:
        if update_blank:
            changed = _fill_blanks(existing.patient, row.data)
            if changed:
                return Outcome(
                    row.legacy_id, "updated", existing.patient.hospital_number, "filled blank: " + ", ".join(changed)
                )
        return Outcome(row.legacy_id, "already_imported", existing.patient.hospital_number, note)

    data = row.data
    target, how = None, ""
    decision = row.decision
    if decision.lower().startswith("same_as:"):
        ref = decision.split(":", 1)[1].strip()
        target = _resolve_target(ref, linked)
        if target is None:
            return Outcome(
                row.legacy_id, "rejected", note=f"decision names '{ref}', which was not found (row {row.number})"
            )
        how = "linked_by_decision"
    else:
        # Same person already in the system (made earlier in the pilot, or earlier in this file)?
        for candidate in Patient.find_possible_duplicates(
            data["first_name"], data["last_name"], data["date_of_birth"], id_number=data["id_number"]
        ):
            if _strong_match(data, candidate):
                target = candidate
                how = "linked_in_file" if candidate.pk in {p.pk for p in linked.values()} else "linked_existing"
                break
        if target is None and decision.lower() != "new":
            weak = _weak_matches(data)
            if weak:
                names = ", ".join(f"{p.hospital_number} {p.full_name}" for p in weak[:3])
                return Outcome(
                    row.legacy_id,
                    "held_for_review",
                    note=f"may be the same as {names}. Add decision 'new' or 'same_as:<id>' and run again "
                    f"(row {row.number})",
                )
    if target is None:
        target = Patient(**data)
        target.save()
        how = "created"
    elif update_blank:
        _fill_blanks(target, data)
    LegacyRecord.objects.create(
        legacy_id=row.legacy_id,
        legacy_number=row.legacy_number,
        patient=target,
        source=source,
        dob_estimated=row.dob_estimated,
    )
    linked[row.legacy_id] = target
    if row.dob_estimated:
        note = (note + "; " if note else "") + "date of birth estimated from age"
    return Outcome(row.legacy_id, how, target.hospital_number, note)


def write_report(outcomes, path):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["legacy_id", "outcome", "hospital_number", "note"])
        for o in outcomes:
            writer.writerow([o.legacy_id, o.outcome, o.hospital_number, o.note])


def summarise(outcomes):
    counts = {}
    for o in outcomes:
        counts[o.outcome] = counts.get(o.outcome, 0) + 1
    return counts
