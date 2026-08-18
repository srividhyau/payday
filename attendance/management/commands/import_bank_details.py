from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from attendance.salary_bank_import import import_bank_details


class Command(BaseCommand):
    help = (
        "Populate Employee bank-transfer details (account/IFSC/branch) from "
        "a monthly salary workbook's 'Bank Details' sheet. By default only "
        "updates employees already in the system; pass --create-missing to "
        "also create a new Employee for any unmatched name (with a "
        "placeholder code derived from their name, since this sheet has no "
        "real employee code — reconcile that later if the person also "
        "appears in a real attendance upload)."
    )

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str)
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would change without saving anything.",
        )
        parser.add_argument(
            "--create-missing", action="store_true",
            help="Create a new Employee for names with no existing match.",
        )

    def handle(self, *args, **options):
        path = Path(options["file_path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        try:
            report = import_bank_details(
                path, dry_run=options["dry_run"], create_missing=options["create_missing"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        for line in report.created:
            self.stdout.write(self.style.SUCCESS(f"  created: {line}"))
        for line in report.updated:
            self.stdout.write(self.style.SUCCESS(f"  updated: {line}"))
        for name in report.unmatched:
            self.stdout.write(self.style.WARNING(f"  no matching employee: {name}"))
        for line in report.conflicts:
            self.stdout.write(self.style.ERROR(f"  conflict: {line}"))

        summary = (
            f"{len(report.created)} created; {len(report.updated)} updated; "
            f"{len(report.unmatched)} unmatched; {len(report.conflicts)} conflicting (skipped)."
        )
        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE(f"\nDry run: {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n{summary}"))
