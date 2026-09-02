from django.core.management.base import BaseCommand

from attendance.models import AttendanceRecord
from attendance.views import _merge_edited_fields
from src import metrics


class Command(BaseCommand):
    help = (
        "Refines the manually-edited marker for existing records that predate "
        "manually_edited_fields (so they currently show the generic grey 'detail not "
        "tracked' dot instead of a specific one). Every manually_edited=True row gets "
        "'Punch In, Punch Out' as a baseline (the popup's main use is fixing a punch), "
        "and additionally gets 'Shift' merged in if its shift is a real OT code (M-OT/"
        "E-OT/ME-OT/Full-OT) — that value can only have gotten there through the edit "
        "popup, not the device export, for a row already known to be hand-edited. A row "
        "with both ends up with both dots on Device Records (the diagonal-striped "
        "triangle). Only ever touches rows already flagged manually_edited=True; never "
        "flips manually_edited itself, and never touches the ~490 non-GS-shift rows "
        "that were never actually hand-edited (those come from routine data, e.g. "
        "fixed-shift 'Company' subcategory employees). Safe to re-run — merging is "
        "additive, so it never removes a field a real edit_record_view diff already "
        "recorded."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would change without saving anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        records = AttendanceRecord.objects.filter(manually_edited=True)

        to_update = []
        for record in records:
            fields_to_add = ["Punch In", "Punch Out"]
            if record.shift in metrics.OT_SHIFT_CODES:
                fields_to_add.append("Shift")
            merged = _merge_edited_fields(record.manually_edited_fields, fields_to_add)
            if merged != record.manually_edited_fields:
                to_update.append((record, record.manually_edited_fields, merged))

        for record, before, after in to_update:
            self.stdout.write(
                f"{record.employee.code} {record.employee.name} {record.date} "
                f"shift={record.shift}: {before!r} -> {after!r}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run: would update {len(to_update)} record(s)."))
            return

        for record, _before, after in to_update:
            record.manually_edited_fields = after
        AttendanceRecord.objects.bulk_update([r for r, _, _ in to_update], ["manually_edited_fields"])
        self.stdout.write(self.style.SUCCESS(f"Updated {len(to_update)} record(s)."))
