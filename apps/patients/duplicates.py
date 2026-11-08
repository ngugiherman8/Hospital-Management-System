"""Duplicate detection run before a new patient is created.

A candidate is reported when it shares an ID document, shares a phone number,
or has a similar name and the same date of birth.
"""

from difflib import SequenceMatcher

from django.db.models import Q

from .models import Patient, normalize_name, normalize_phone

NAME_THRESHOLD = 0.82
MAX_RESULTS = 5


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def find_duplicates(*, first_name, last_name, date_of_birth=None, phone="", id_type="", id_number="", exclude_pk=None):
    phone_n = normalize_phone(phone)
    id_number = (id_number or "").strip()

    query = Q()
    if id_number and id_type != "none":
        query |= Q(id_type=id_type, id_number=id_number)
    if phone_n:
        query |= Q(phone_normalized=phone_n)
    if date_of_birth:
        query |= Q(date_of_birth=date_of_birth)
    if not query:
        return []

    wanted = normalize_name(first_name, last_name)
    results = []
    qs = Patient.objects.filter(query)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    for p in qs[:200]:
        reasons, score = [], 0.0
        if id_number and id_type != "none" and p.id_type == id_type and p.id_number == id_number:
            reasons.append("Same ID number")
            score += 3
        if phone_n and p.phone_normalized == phone_n:
            reasons.append("Same phone number")
            score += 2
        sim = _similarity(wanted, normalize_name(p.first_name, p.last_name))
        # allow extra middle/other names: compare also first+last token overlap
        if sim < NAME_THRESHOLD and wanted and p.full_name:
            sim = max(
                sim,
                _similarity(wanted.split()[0], p.first_name.lower())
                * _similarity(wanted.split()[-1], p.last_name.lower()),
            )
        if date_of_birth and p.date_of_birth == date_of_birth and sim >= NAME_THRESHOLD:
            reasons.append("Similar name and date of birth")
            score += 1 + sim
        if reasons:
            results.append((score, p, reasons))
    results.sort(key=lambda r: -r[0])
    return [{"patient": p, "reasons": reasons} for _, p, reasons in results[:MAX_RESULTS]]
