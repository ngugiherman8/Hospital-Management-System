from django.core.management.base import BaseCommand, CommandError

from apps.golive import importer


class Command(BaseCommand):
    help = (
        "Import old patient records from a CSV. Safe to run again. Use --dry-run first, then read the report: "
        "rejected rows need fixing in the CSV, held_for_review rows need a decision."
    )

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--report", help="Write a CSV saying what happened to every row.")
        parser.add_argument("--dry-run", action="store_true", help="Work everything out, save nothing.")
        parser.add_argument("--update-blank", action="store_true", help="Fill empty fields of people already imported.")
        parser.add_argument(
            "--estimate-from-age",
            action="store_true",
            help="Where only an age is known, use 1 July of the birth year (flagged as estimated).",
        )

    def handle(self, *args, **opts):
        try:
            outcomes = importer.run_import(
                opts["file"],
                update_blank=opts["update_blank"],
                estimate_from_age=opts["estimate_from_age"],
                dry_run=opts["dry_run"],
            )
        except FileNotFoundError:
            raise CommandError(f"File not found: {opts['file']}") from None
        if opts["report"]:
            importer.write_report(outcomes, opts["report"])
        counts = importer.summarise(outcomes)
        self.stdout.write(("DRY RUN, nothing saved. " if opts["dry_run"] else "") + f"{len(outcomes)} rows read.")
        for name, n in sorted(counts.items()):
            self.stdout.write(f"  {name}: {n}")
        problems = counts.get("rejected", 0) + counts.get("held_for_review", 0)
        if problems:
            self.stdout.write(self.style.WARNING(f"{problems} rows need attention (see the report)."))
