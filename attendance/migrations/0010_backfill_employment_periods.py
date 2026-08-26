from datetime import date

from django.db import migrations


FALLBACK_START = date(2020, 1, 1)


def backfill_periods(apps, schema_editor):
    """Gives every existing employee one open-ended EmploymentPeriod, so
    "active on date" queries have real data to work with from day one
    instead of relying on the (no-periods = active) fallback forever.
    start_date is their earliest AttendanceRecord date if one exists,
    else an arbitrary "always been active" marker far in the past."""
    Employee = apps.get_model("attendance", "Employee")
    EmploymentPeriod = apps.get_model("attendance", "EmploymentPeriod")
    AttendanceRecord = apps.get_model("attendance", "AttendanceRecord")

    for emp in Employee.objects.all():
        earliest = (
            AttendanceRecord.objects.filter(employee=emp).order_by("date").values_list("date", flat=True).first()
        )
        EmploymentPeriod.objects.create(
            employee=emp, start_date=earliest or FALLBACK_START, end_date=None,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0009_employmentperiod"),
    ]

    operations = [
        migrations.RunPython(backfill_periods, noop_reverse),
    ]
