"""
Bridges the framework-agnostic parsing in src/parser.py to the Django
models. Used by both the HR upload view (attendance/views.py) and the
`import_attendance` management command.
"""
from __future__ import annotations

from django.db import transaction

from src import parser as attendance_parser

from .models import AttendanceRecord, Department, Employee, UploadBatch


@transaction.atomic
def import_dataframe(daily_df, file_name: str = "") -> UploadBatch:
    """Upsert a normalized daily-attendance DataFrame (see
    src/parser.normalize) into the database. Re-importing a file, or a file
    that overlaps a previous one, updates existing rows for the same
    employee+date rather than duplicating them."""
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
            emp, created = Employee.objects.get_or_create(
                code=row.emp_code,
                defaults={
                    "name": row.emp_name, "department": dept, "designation": row.designation,
                    "company": company, "category": category,
                },
            )
            if not created:
                emp.name = row.emp_name
                emp.department = dept
                emp.designation = row.designation
                emp.company = company
                emp.category = category
                emp.save(update_fields=["name", "department", "designation", "company", "category"])
            emp_cache[row.emp_code] = emp

        AttendanceRecord.objects.update_or_create(
            employee=emp,
            date=row.date.date(),
            defaults=dict(
                shift=row.shift,
                time_in=row.time_in,
                time_out=row.time_out,
                work_hours=row.work_hours,
                ot_hours=row.ot_hours,
                status=row.status,
                batch=batch,
            ),
        )

    return batch


def import_file(file_obj, file_name: str | None = None) -> UploadBatch:
    """Parse + import an uploaded file object (Django UploadedFile or an
    open file handle with a `.name`)."""
    raw = attendance_parser.load_file(file_obj)
    daily = attendance_parser.normalize(raw)
    return import_dataframe(daily, file_name=file_name or getattr(file_obj, "name", "upload"))
