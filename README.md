# Payday — Attendance Dashboard

Django, backed by SQLite. Handles HR file uploads, a company holiday
calendar, and renders the attendance dashboard, replacing the old
pivot-table/Power-Query workbook for turning the eSSL fingerprint system's
DailyAttendance export into a monthly attendance view.

- **Upload** — via the Django `/upload/` page. Re-uploading a file, or one
  covering overlapping dates, upserts by employee+date instead of
  duplicating rows. Data can also be browsed/edited in the Django admin.
- **Holiday Calendar** (`/calendar/`) — set company-wide Holidays, Paid
  Holidays, and Comp Off days. They apply to every employee automatically:
  credited whether or not someone worked that day, but anyone who came in
  anyway still shows their real hours and gets it counted as Time Off (OT)
  instead of an ordinary work day.
- **Dashboard** (`/dashboard/`) — KPIs, a department breakdown (with
  top/lowest attendance and a leave watch-list), the holiday calendar for
  the loaded period, and a department-grouped date × employee hours grid
  matching the "Row Labels" hierarchy of the original `Month_Attendance`
  pivot table, with a heat-map, punch-time tooltips, and a missed-punch
  filter.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser   # optional, for the Django admin

python manage.py runserver         # http://localhost:8000/upload/
```

To try it without real data: `python manage.py import_attendance sample_data/sample_daily_attendance.csv`.

## What it does

- Parses a DailyAttendance export (flexible column matching — works with
  headers like `Employee Code`/`EmpCode`, `In`/`In Time`, `Work_Hours`, etc.,
  and also a real-world headerless eSSL export by column position).
- Computes, per employee: working days, present days, absent/personal-leave
  days, paid holiday & comp-off days, total & average work hours, overtime
  (Time Off) hours, and attendance %.
- Shows a department breakdown, plus a department-grouped date × employee
  view (collapsible department rows, employees nested below — the
  `Month_Attendance` pivot-table layout).
- Shows the Working Days / Holiday summary strip from the top of the
  original sheet.
- Lets you export the computed summary back out as an `.xlsx`.

## Data handling — important

**Never commit real attendance/payroll files, or `db.sqlite3`, to this
repo.** They contain employee names, salaries, and bank details.
`.gitignore` already excludes `db.sqlite3`, `/data/`, and any
`.xlsx`/`.xlsm`/`.xls` file outside `sample_data/`.

## Leave classification

The raw eSSL export typically only marks each day `P` (present) or `A`
(absent); it doesn't natively distinguish personal leave, comp-off, or paid
holiday. Those come from the Holiday Calendar (`/calendar/`) instead —
Personal Leave is computed as `Working Days − (Paid Holidays + Present +
Comp Off)`, matching the original workbook's Power Query formula.

## Project layout

```
manage.py                  Django entrypoint
config/                    Django project settings/urls
attendance/                Django app: models, admin, views, import command
  models.py                 Department, Employee, UploadBatch, AttendanceRecord, SpecialDay
  importer.py                Upserts a parsed DataFrame into the database
  views.py / urls.py          Upload (/upload/), dashboard (/dashboard/), calendar (/calendar/)
  management/commands/        `import_attendance <file>` CLI import
src/parser.py               Loads & normalizes an attendance export (framework-agnostic)
src/metrics.py               Attendance calculations (per-employee, per-department, pivot)
sample_data/                 Synthetic demo data (safe to commit)
```

## Before deploying beyond localhost

This is a local-dev MVP: `DEBUG = True`, no authentication on `/upload/`,
and the secret key is committed. Before running it anywhere reachable by
others, set `DEBUG = False`, move `SECRET_KEY` to an environment variable,
restrict `ALLOWED_HOSTS`, and put `/upload/` and `/calendar/` behind login
(`@login_required`).

## Roadmap (not in this MVP)

- Payroll calculation (earned salary, deductions, tax) per employee category
- Bank transfer sheet export (IFSC, account no., amount)
