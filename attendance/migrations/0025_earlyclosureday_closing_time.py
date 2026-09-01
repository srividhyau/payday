"""Switch EarlyClosureDay from a raw full_day_hours number to an actual
closing_time (e.g. 14:30) — HR knows the clock time, not a duration, and
full_day_hours is now just a computed property derived from it (see
attendance/models.py). Preserves any existing rows by converting their
stored full_day_hours back into an equivalent closing_time (09:00 +
full_day_hours) before dropping the old field, rather than a plain
drop-and-recreate that would silently lose that data.
"""
from datetime import timedelta

from django.db import migrations, models


def full_day_hours_to_closing_time(apps, schema_editor):
    EarlyClosureDay = apps.get_model("attendance", "EarlyClosureDay")
    for row in EarlyClosureDay.objects.all():
        start = timedelta(hours=9)
        closing = start + timedelta(hours=float(row.full_day_hours))
        total_minutes = int(closing.total_seconds() // 60)
        row.closing_time = f"{total_minutes // 60:02d}:{total_minutes % 60:02d}:00"
        row.save(update_fields=["closing_time"])


def closing_time_to_full_day_hours(apps, schema_editor):
    EarlyClosureDay = apps.get_model("attendance", "EarlyClosureDay")
    for row in EarlyClosureDay.objects.all():
        minutes = row.closing_time.hour * 60 + row.closing_time.minute - 9 * 60
        row.full_day_hours = round(max(minutes, 0) / 60, 2)
        row.save(update_fields=["full_day_hours"])


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0024_earlyclosureday'),
    ]

    operations = [
        migrations.AddField(
            model_name='earlyclosureday',
            name='closing_time',
            field=models.TimeField(null=True, blank=True),
        ),
        migrations.RunPython(full_day_hours_to_closing_time, closing_time_to_full_day_hours),
        migrations.RemoveField(
            model_name='earlyclosureday',
            name='full_day_hours',
        ),
        migrations.AlterField(
            model_name='earlyclosureday',
            name='closing_time',
            field=models.TimeField(
                help_text='What time the company actually closes on this date (e.g. 14:30) — '
                          'the expected full day for Short Days/Permission Hours is computed '
                          'from this minus the standard 9:00 AM start.',
            ),
        ),
    ]
