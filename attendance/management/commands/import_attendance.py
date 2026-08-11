from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from attendance.importer import import_file


class Command(BaseCommand):
    help = "Import a DailyAttendance export (xlsx/xlsm/csv) into the database."

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str)

    def handle(self, *args, **options):
        path = Path(options["file_path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        with open(path, "rb") as f:
            # open() file objects already expose the path via `.name`, which
            # parser.load_file() uses to detect xlsx vs csv.
            try:
                batch = import_file(f, file_name=path.name)
            except Exception as exc:  # noqa: BLE001
                raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {batch.row_count} rows ({batch.period_start} to "
                f"{batch.period_end}) into batch #{batch.id}."
            )
        )
