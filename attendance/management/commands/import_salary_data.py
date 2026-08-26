from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from attendance.salary_bank_import import import_salary_data


class Command(BaseCommand):
    help = (
        "Populate Employee.basic_salary/hra/da/pf_number/esi_number/"
        "pf_enabled/esi_enabled/tds_enabled from a monthly salary "
        "workbook's Company Workers/Helpers/Staff sheets — nothing else "
        "on Employee is touched. Salary figures (basic_salary/hra/da) are "
        "only filled once (never overwritten); pf_enabled/esi_enabled/"
        "tds_enabled always sync to the sheet's current value, since "
        "those can change month to month. Only applies a sheet's row to "
        "an Employee whose subcategory already matches that sheet — "
        "always run --dry-run first and read the "
        "skipped_wrong_subcategory/unmatched lines, those need a human "
        "to reconcile, not a re-run."
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
        dry_run = options["dry_run"]

        report = import_salary_data(path, dry_run=dry_run)

        for line in report.updated:
            self.stdout.write(self.style.SUCCESS(f"  updated: {line}"))
        for line in report.skipped_already_set:
            self.stdout.write(self.style.NOTICE(f"  skipped (already set): {line}"))
        for line in report.skipped_wrong_subcategory:
            self.stdout.write(self.style.WARNING(f"  skipped (subcategory mismatch): {line}"))
        for line in report.unmatched:
            self.stdout.write(self.style.WARNING(f"  no matching employee: {line}"))

        summary = (
            f"{len(report.updated)} updated; {len(report.skipped_already_set)} already set; "
            f"{len(report.skipped_wrong_subcategory)} subcategory mismatch; "
            f"{len(report.unmatched)} unmatched."
        )
        if dry_run:
            self.stdout.write(self.style.NOTICE(f"\nDry run: {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n{summary}"))
