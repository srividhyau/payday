from django.core.management.base import BaseCommand

from attendance.models import AttendanceRecord
from attendance.views import _merge_edited_fields
from src import metrics


class Command(BaseCommand):
    help = (
        "Refines the manually-edited marker for existing records that predate "
        "manually_edited_fields (so they currently show the generic grey 'detail not "
        "tracked' dot instead of a specific one). Two passes: (1) any manually_edited=True "
        "row whose shift is a real OT code (M-OT/E-OT/ME-OT/Full-OT) gets 'Shift' added — "
        "that shift value can only have gotten there through the edit popup, not the "
        "device export, for a row already known to be hand-edited. (2) anything still "
        "left with no field detail after that is assumed to be a punch-time correction "
        "(the popup's other main use) and gets 'Punch In, Punch Out' added. Only ever "
        "touches rows already flagged manually_edited=True; never flips manually_edited "
        "itself, and never touches the ~490 non-GS-shift rows that were never actually "
        "hand-edited (those come from routine data, e.g. fixed-shift 'Company' "
        "subcategory employees)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would change without saving anything.",
        )

    def _plan(self, queryset, fields_to_add):
        to_update = []
        for record in queryset:
            merged = _merge_edited_fields(record.manually_edited_fields, fields_to_add)
            if merged != record.manually_edited_fields:
                to_update.append((record, record.manually_edited_fields, merged))
        return to_update

    def _report(self, to_update):
        for record, before, after in to_update:
            self.stdout.write(
                f"{record.employee.code} {record.employee.name} {record.date} "
                f"shift={record.shift}: {before!r} -> {after!r}"
            )

    def _apply(self, to_update):
        for record, _before, after in to_update:
            record.manually_edited_fields = after
        AttendanceRecord.objects.bulk_update([r for r, _, _ in to_update], ["manually_edited_fields"])

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        self.stdout.write("--- Pass 1: infer Shift from a real OT shift code ---")
        shift_candidates = AttendanceRecord.objects.filter(
            manually_edited=True, shift__in=sorted(metrics.OT_SHIFT_CODES),
        )
        shift_updates = self._plan(shift_candidates, ["Shift"])
        self._report(shift_updates)
        if not dry_run:
            self._apply(shift_updates)
        self.stdout.write(
            self.style.SUCCESS(f"{'Would update' if dry_run else 'Updated'} {len(shift_updates)} record(s).")
        )

        self.stdout.write("--- Pass 2: assume the rest are punch-time corrections ---")
        # Re-filter fresh (rather than reusing shift_candidates) so pass 2 sees
        # pass 1's in-memory changes even under --dry-run, where nothing was
        # actually saved to the database yet.
        shift_updated_pks = {r.pk for r, _, _ in shift_updates}
        time_candidates = [
            r for r in AttendanceRecord.objects.filter(manually_edited=True, manually_edited_fields="")
            if r.pk not in shift_updated_pks
        ]
        time_updates = self._plan(time_candidates, ["Punch In", "Punch Out"])
        self._report(time_updates)
        if not dry_run:
            self._apply(time_updates)
        self.stdout.write(
            self.style.SUCCESS(f"{'Would update' if dry_run else 'Updated'} {len(time_updates)} record(s).")
        )
