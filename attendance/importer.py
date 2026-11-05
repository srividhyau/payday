"""
Bridges the framework-agnostic parsing in src/parser.py to the Django
models. Used by both the HR upload view (attendance/views.py) and the
`import_attendance` management command.
"""
from __future__ import annotations

from django.db import transaction

from src import metrics as attendance_metrics
from src import parser as attendance_parser

from .models import AttendanceRecord, Employee, MonthLock, PendingDeviceAttendance, UploadBatch


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
    with everything straight from the file, including shift.

    Likewise, an *existing* Employee's department/category/subcategory are
    HR-managed (set via the Employee form — e.g. an Operator/Bartrack/OT
    combination that drives OT/Salary tab routing) and are never touched
    by a re-upload, even if the device export's own values differ or say
    something generic like "DefaultCategory". Only name/designation/
    company (identity details the device export is authoritative for) get
    refreshed on an existing employee. A code that isn't in Employees never
    creates one: its rows wait in PendingDeviceAttendance until the employee
    is added from the Employees page."""
    if daily_df.empty:
        raise ValueError("No attendance rows found in that file.")

    batch = UploadBatch.objects.create(
        file_name=file_name or "upload",
        period_start=daily_df["date"].min().date(),
        period_end=daily_df["date"].max().date(),
        row_count=len(daily_df),
    )

    # A month locked on the Attendance dashboard (All or Missed Punch —
    # either one means attendance itself is meant to be frozen, same as
    # edit_record_view's own _month_is_locked check) is skipped entirely
    # here too — otherwise a re-import (e.g. a corrected export covering
    # the same dates) could silently fill in a missed punch for a month
    # that's supposed to be done, bypassing the one gate the manual edit
    # path already respects.
    locked_year_months = set(
        MonthLock.objects.filter(view__in=[MonthLock.VIEW_ALL, MonthLock.VIEW_ISSUES])
        .values_list("year", "month")
    )

    emp_cache: dict[str, Employee | None] = {}

    for row in daily_df.itertuples(index=False):
        if row.emp_code not in emp_cache:
            emp = Employee.objects.filter(code=row.emp_code).first()
            if emp:
                emp.name = row.emp_name
                emp.designation = row.designation
                emp.company = getattr(row, "company", "")
                emp.save(update_fields=["name", "designation", "company"])
            emp_cache[row.emp_code] = emp
        emp = emp_cache[row.emp_code]

        row_date = row.date.date()
        if (row_date.year, row_date.month) in locked_year_months:
            continue

        if emp is None:
            PendingDeviceAttendance.objects.update_or_create(
                emp_code=row.emp_code, date=row_date,
                defaults={
                    "emp_name": row.emp_name, "designation": row.designation,
                    "company": getattr(row, "company", ""), "shift": row.shift,
                    "time_in": row.time_in, "time_out": row.time_out,
                    "work_hours": row.work_hours, "ot_hours": row.ot_hours,
                    "status": row.status, "batch": batch,
                },
            )
            continue

        try:
            record = AttendanceRecord.objects.get(employee=emp, date=row_date)
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
                date=row_date,
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


def attach_pending_attendance(employee: Employee) -> int:
    """Moves an employee's waiting device rows into AttendanceRecord. A row
    already recorded for that date, or in a month locked since the upload,
    is left as it is. Returns how many rows were moved."""
    locked_year_months = set(
        MonthLock.objects.filter(view__in=[MonthLock.VIEW_ALL, MonthLock.VIEW_ISSUES])
        .values_list("year", "month")
    )
    moved = 0
    with transaction.atomic():
        for row in PendingDeviceAttendance.objects.filter(emp_code=employee.code):
            if (row.date.year, row.date.month) in locked_year_months:
                continue
            _, created = AttendanceRecord.objects.get_or_create(
                employee=employee, date=row.date,
                defaults={
                    "shift": row.shift, "time_in": row.time_in, "time_out": row.time_out,
                    "work_hours": row.work_hours, "ot_hours": row.ot_hours,
                    "status": row.status, "batch": row.batch,
                },
            )
            moved += int(created)
            row.delete()
    return moved
