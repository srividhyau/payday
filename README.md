# Payday — Attendance Dashboard

A Streamlit dashboard that replaces the old pivot-table/Power-Query workbook
for turning the eSSL fingerprint system's DailyAttendance export into a
monthly attendance view.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL Streamlit prints, and either upload a DailyAttendance
export (`.xlsx`/`.xlsm`/`.csv`) or check "Use sample data" in the sidebar to
try it with the synthetic data in `sample_data/`.

## What it does

- Parses a DailyAttendance export (flexible column matching — works with
  headers like `Employee Code`/`EmpCode`, `In`/`In Time`, `Work_Hours`, etc.)
- Computes, per employee: working days, present days, absent days, total &
  average work hours, overtime hours, and attendance %.
- Shows a department breakdown and a date × employee daily-hours pivot
  (the equivalent of the old `Month_Attendance` pivot table).
- Lets you export the computed summary back out as an `.xlsx`.

## Data handling — important

**Never commit real attendance/payroll files to this repo.** They contain
employee names, salaries, and bank details. `.gitignore` already excludes
`/data/` and any `.xlsx`/`.xlsm`/`.xls` file outside `sample_data/`. When
testing against a real export locally, keep it in a `data/` folder (gitignored)
or outside the repo entirely.

## Known limitation — leave classification

The raw eSSL export typically only marks each day `P` (present) or `A`
(absent); it doesn't natively distinguish personal leave, comp-off, or paid
holiday the way the old workbook's `Staff`/`Summary` sheets did. Those
distinctions came from a separate, manually maintained leave register. This
MVP computes absent days as `working days − present days` (with working days
configurable in the sidebar, since weekly offs/holidays aren't reliably
flagged in the raw export). Reconciling personal-leave/comp-off codes against
a leave register is a good next step once that data source is available.

## Project layout

```
app.py              Streamlit entrypoint
src/parser.py        Loads & normalizes the uploaded attendance export
src/metrics.py        Attendance calculations (per-employee, per-department, pivot)
sample_data/          Synthetic demo data (safe to commit)
```

## Roadmap (not in this MVP)

- Payroll calculation (earned salary, deductions, tax) per employee category
- Bank transfer sheet export (IFSC, account no., amount)
