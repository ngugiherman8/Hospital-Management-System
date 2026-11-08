"""Load a full ICD-10 list from a CSV with columns code,title (adds new codes, updates titles)."""

import csv

from django.core.management.base import BaseCommand

from apps.clinical.models import ICD10Code


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("csv_path")

    def handle(self, *args, csv_path, **opts):
        n = 0
        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code, title = (row.get("code") or "").strip(), (row.get("title") or "").strip()
                if code and title:
                    ICD10Code.objects.update_or_create(code=code, defaults={"title": title[:200]})
                    n += 1
        self.stdout.write(self.style.SUCCESS(f"Imported {n} ICD-10 codes."))
