"""
Bridges the framework-agnostic parsing in src/parser.py to the Django
models. Used by both the HR upload view (attendance/views.py) and the
`import_attendance` management command.
"""
from __future__ import annotations

from django.db import transaction

from src import metrics as attendance_metrics
from src import parser as attendance_parser

from .models import AttendanceRecord, Department, Employee, UploadBatch


@transaction.atomic
def import_dataframe(daily_df, file_name: str = "") -> UploadBatch:
    """Upsert a normalized daily-attendance DataFrame (see
    src/parser.normalize) into the database. Re-importing a file, or a file
    that overlaps a previous one, only ever touches an *existing*
    employee+date row if that row currently has a missed punch (a missing
    time_in or time_out) — in that case its punch times/hours/status get
    filled in from the file, but never its `shift`, since that's only ever
    changed through the dashboard's edit tools and a re-upload (e.g. a
    corrected export covering the same dates) shouldn't be able to
    silently wipe out a manually-set OT shift code. An existing row that
    already has real punches is left completely untouched — the file is
    assumed stale for it. A genuinely new employee+date row is created
    with everything straight from the file, including shift."""
    if daily_df.empty:
        raise ValueError("No attendance rows found in that file.")

    batch = UploadBatch.objects.create(
        file_name=file_name or "upload",
        period_start=daily_df["date"].min().date(),
        period_end=daily_df["date"].max().date(),
        row_count=len(daily_df),
    )

    dept_cache: dict[str, Department] = {}
    emp_cache: dict[str, Employee] = {}

    for row in daily_df.itertuples(index=False):
        dept_name = (row.department or "Unassigned").strip() or "Unassigned"
        dept = dept_cache.get(dept_name)
        if dept is None:
            dept, _ = Department.objects.get_or_create(name=dept_name)
            dept_cache[dept_name] = dept

        emp = emp_cache.get(row.emp_code)
        if emp is None:
            company = getattr(row, "company", "")
            category = getattr(row, "category", "")
            subcategory = getattr(row, "subcategory", "")
            emp, created = Employee.objects.get_or_create(
                code=row.emp_code,
                defaults={
                    "name": row.emp_name, "department": dept, "designation": row.designation,
                    "company": company, "category": category, "subcategory": subcategory,
                },
            )
            if not created:
                emp.name = row.emp_name
                emp.department = dept
                emp.designation = row.designation
                emp.company = company
                emp.category = category
                emp.subcategory = subcategory
                emp.save(update_fields=[
                    "name", "department", "designation", "company", "category", "subcategory",
                ])
            emp_cache[row.emp_code] = emp

        try:
            record = AttendanceRecord.objects.get(employee=emp, date=row.date.date())
            had_missed_punch = (
                not attendance_metrics.clean_punch_time(record.time_in)
                or not attendance_metrics.clean_punch_time(record.time_out)
            )
            if had_missed_punch:
                record.time_in = row.time_in
                record.time_out = row.time_out
                record.work_hours = row.work_hours
                record.ot_hours = row.ot_hours
                record.status = row.status
                record.batch = batch
                record.save(update_fields=[
                    "time_in", "time_out", "work_hours", "ot_hours", "status", "batch",
                ])
        except AttendanceRecord.DoesNotExist:
            AttendanceRecord.objects.create(
                employee=emp,
                date=row.date.date(),
                shift=row.shift,
                time_in=row.time_in,
                time_out=row.time_out,
                work_hours=row.work_hours,
                ot_hours=row.ot_hours,
                status=row.status,
                batch=batch,
            )

    return batch


def import_file(file_obj, file_name: str | None = None) -> UploadBatch:
    """Parse + import an uploaded file object (Django UploadedFile or an
    open file handle with a `.name`)."""
    raw = attendance_parser.load_file(file_obj)
    daily = attendance_parser.normalize(raw)
    return import_dataframe(daily, file_name=file_name or getattr(file_obj, "name", "upload"))
