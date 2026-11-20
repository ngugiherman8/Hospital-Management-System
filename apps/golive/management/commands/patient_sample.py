import csv
import random

from django.core.management.base import BaseCommand, CommandError

from apps.golive.models import LegacyRecord

COLUMNS = [
    "hospital_number",
    "legacy_id",
    "legacy_number",
    "name",
    "date_of_birth",
    "sex",
    "id_number",
    "phone",
    "residence",
    "kin_name",
    "matches_paper",
    "what_differs",
    "checked_by",
]


class Command(BaseCommand):
    help = (
        "Pick imported patients at random for a receptionist and a nurse to check against the paper record. "
        "Fill matches_paper with Y or N, then run with --result."
    )

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=50)
        parser.add_argument("--seed", type=int, default=None, help="Same seed, same sample.")
        parser.add_argument("--out", default="patient_sample.csv")
        parser.add_argument("--result", help="Read a completed sheet and report the match rate.")

    def handle(self, *args, **opts):
        if opts["result"]:
            return self.read_result(opts["result"])
        records = list(LegacyRecord.objects.select_related("patient").order_by("patient_id", "id"))
        seen, unique = set(), []
        for r in records:
            if r.patient_id not in seen:
                seen.add(r.patient_id)
                unique.append(r)
        if not unique:
            raise CommandError("No imported patients to sample.")
        chosen = random.Random(opts["seed"]).sample(unique, min(opts["count"], len(unique)))
        with open(opts["out"], "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            for r in chosen:
                p = r.patient
                writer.writerow(
                    [p.hospital_number, r.legacy_id, r.legacy_number, p.full_name, p.date_of_birth, p.sex, p.id_number,
                     p.phone, p.residence, p.kin_name, "", "", ""]
                )  # fmt: skip
        self.stdout.write(f"{len(chosen)} patients written to {opts['out']}.")

    def read_result(self, path):
        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        done = [r for r in rows if (r.get("matches_paper") or "").strip()]
        yes = [r for r in done if r["matches_paper"].strip().lower() in ("y", "yes")]
        no = [r for r in done if r not in yes]
        self.stdout.write(f"{len(rows)} in the sample, {len(done)} checked, {len(yes)} match, {len(no)} do not.")
        for r in no:
            self.stdout.write(f"  {r['hospital_number']} {r['name']}: {r.get('what_differs') or 'no reason given'}")
        if len(done) < len(rows):
            self.stdout.write(self.style.WARNING(f"{len(rows) - len(done)} rows not checked yet."))
        if no or len(done) < len(rows):
            raise CommandError("The sample does not match yet.")
        self.stdout.write(self.style.SUCCESS("Every sampled patient matches the paper record."))
