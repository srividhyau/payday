from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from attendance.salary_bank_import import import_departments


class Command(BaseCommand):
    help = (
        "Backfill Employee.department from a monthly salary workbook's "
        "department sheets (Op/I&B/Staff/Helpers/Company Workers). Only "
        "fills employees that currently have no department set — never "
        "overwrites an existing value, since the attendance import is the "
        "primary source of truth for this field."
    )

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str)
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would change without saving anything.",
        )

    def handle(self, *args, **options):
        path = Path(options["file_path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        report = import_departments(path, dry_run=options["dry_run"])

        for line in report.updated:
            self.stdout.write(self.style.SUCCESS(f"  updated: {line}"))
        for name in report.unmatched:
            self.stdout.write(self.style.WARNING(f"  no matching employee: {name}"))

        summary = f"{len(report.updated)} employee(s); {len(report.unmatched)} unmatched."
        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE(f"\nDry run: would update {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nUpdated {summary}"))
