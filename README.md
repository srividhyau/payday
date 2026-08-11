# Payday — Attendance Dashboard

Django + Streamlit, backed by SQLite. Django handles HR file uploads and data
storage (via its admin and a simple upload page); Streamlit reads the same
database to render the attendance dashboard, replacing the old
pivot-table/Power-Query workbook for turning the eSSL fingerprint system's
DailyAttendance export into a monthly attendance view.

- **Django** — HR uploads a DailyAttendance export at `/upload/`, which gets
  parsed and persisted to SQLite (re-uploads upsert by employee+date, so
  multiple monthly files accumulate without duplicating rows). Data can also
  be browsed/edited in the Django admin.
- **Streamlit** — reads that same database and renders the dashboard: KPIs,
  employee-wise attendance, department breakdown, and a date × employee
  hours pivot.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser   # optional, for the Django admin

python manage.py runserver         # Django: upload page + admin, http://localhost:8000/upload/
streamlit run dashboard/streamlit_app.py   # Streamlit: dashboard, in a separate terminal
```

To try it without real data: `python manage.py import_attendance sample_data/sample_daily_attendance.csv`,
or check "Use sample data" in the Streamlit sidebar.

## What it does

- Parses a DailyAttendance export (flexible column matching — works with
  headers like `Employee Code`/`EmpCode`, `In`/`In Time`, `Work_Hours`, etc.)
- Computes, per employee: working days, present days, absent days, total &
  average work hours, overtime hours, and attendance %.
- Shows a department breakdown and a date × employee daily-hours pivot
  (the equivalent of the old `Month_Attendance` pivot table).
- Lets you export the computed summary back out as an `.xlsx`.

## Data handling — important

**Never commit real attendance/payroll files, or `db.sqlite3`, to this
repo.** They contain employee names, salaries, and bank details.
`.gitignore` already excludes `db.sqlite3`, `/data/`, and any
`.xlsx`/`.xlsm`/`.xls` file outside `sample_data/`.

## Known limitation — leave classification

The raw eSSL export typically only marks each day `P` (present) or `A`
(absent); it doesn't natively distinguish personal leave, comp-off, or paid
holiday the way the old workbook's `Staff`/`Summary` sheets did. Those
distinctions came from a separate, manually maintained leave register. This
MVP computes absent days as `working days − present days` (with working days
configurable in the Streamlit sidebar, since weekly offs/holidays aren't
reliably flagged in the raw export). Reconciling personal-leave/comp-off
codes against a leave register is a good next step once that data source is
available.

## Project layout

```
manage.py                  Django entrypoint
config/                    Django project settings/urls
attendance/                Django app: models, admin, upload view, import command
  models.py                 Department, Employee, UploadBatch, AttendanceRecord
  importer.py                Upserts a parsed DataFrame into the database
  views.py / urls.py          HR upload page (/upload/)
  management/commands/        `import_attendance <file>` CLI import
dashboard/streamlit_app.py Streamlit dashboard, reads the Django DB via the ORM
src/parser.py               Loads & normalizes an attendance export (framework-agnostic)
src/metrics.py               Attendance calculations (per-employee, per-department, pivot)
sample_data/                 Synthetic demo data (safe to commit)
```

## Before deploying beyond localhost

This is a local-dev MVP: `DEBUG = True`, no authentication on `/upload/`,
and the secret key is committed. Before running it anywhere reachable by
others, set `DEBUG = False`, move `SECRET_KEY` to an environment variable,
restrict `ALLOWED_HOSTS`, and put `/upload/` behind login (`@login_required`).

## Roadmap (not in this MVP)

- Payroll calculation (earned salary, deductions, tax) per employee category
- Bank transfer sheet export (IFSC, account no., amount)
