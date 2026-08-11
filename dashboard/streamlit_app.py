"""
Payday - Attendance Dashboard (reads from the Django database)

HR uploads DailyAttendance exports through the Django app (/upload/), which
parses and persists them to SQLite. This Streamlit app reads that same
database and renders the attendance dashboard on top of it, so data
accumulates across months instead of being re-uploaded every session.

Run with: streamlit run dashboard/streamlit_app.py
(Run `python manage.py migrate` at least once first.)
"""
import io
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# --- Wire up Django so we can use its ORM from this standalone script -----
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from attendance.models import AttendanceRecord  # noqa: E402
from src import metrics  # noqa: E402

st.set_page_config(page_title="Payday - Attendance Dashboard", layout="wide")

st.title("Payday — Attendance Dashboard")
st.caption(
    "Reads attendance data uploaded via the Django app. "
    "Upload new DailyAttendance exports at the /upload/ page."
)


@st.cache_data(ttl=60)
def load_daily_data() -> pd.DataFrame:
    rows = AttendanceRecord.objects.select_related("employee", "employee__department").values(
        "employee__code",
        "employee__name",
        "employee__department__name",
        "employee__designation",
        "date",
        "shift",
        "time_in",
        "time_out",
        "work_hours",
        "ot_hours",
        "status",
    )
    df = pd.DataFrame.from_records(rows)
    if df.empty:
        return df
    df = df.rename(
        columns={
            "employee__code": "emp_code",
            "employee__name": "emp_name",
            "employee__department__name": "department",
            "employee__designation": "designation",
        }
    )
    df["department"] = df["department"].fillna("Unassigned")
    df["date"] = pd.to_datetime(df["date"])
    return df


with st.sidebar:
    st.header("Data source")
    if st.button("Refresh from database"):
        load_daily_data.clear()
    use_sample = st.checkbox("Use sample data instead (demo)", value=False)

    st.header("Working days")
    override_working_days = st.checkbox("Manually set working days for the period", value=False)
    manual_working_days = None
    if override_working_days:
        manual_working_days = st.number_input("Working days", min_value=1, max_value=31, value=26)

daily = pd.read_csv("sample_data/sample_daily_attendance.csv") if use_sample else load_daily_data()
if use_sample:
    from src import parser
    daily = parser.normalize(daily)

if daily.empty:
    st.info(
        "No attendance data yet. Upload a DailyAttendance export at the Django app's "
        "/upload/ page (`python manage.py runserver`), or check 'Use sample data' to preview."
    )
    st.stop()

# --- Filters -----------------------------------------------------------
min_date, max_date = daily["date"].min(), daily["date"].max()
departments = sorted(daily["department"].dropna().unique().tolist())

col_a, col_b = st.columns([2, 3])
with col_a:
    date_range = st.date_input(
        "Period", value=(min_date.date(), max_date.date()),
        min_value=min_date.date(), max_value=max_date.date(),
    )
with col_b:
    dept_filter = st.multiselect("Departments", departments, default=departments)

if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    daily = daily[(daily["date"] >= start) & (daily["date"] <= end)]
if dept_filter:
    daily = daily[daily["department"].isin(dept_filter)]

working_days = manual_working_days or metrics.infer_working_days(daily)

emp_summary = metrics.employee_summary(daily, working_days)
dept_summary = metrics.department_summary(emp_summary)
kpi = metrics.kpis(emp_summary)

# --- KPI cards -----------------------------------------------------------
st.subheader(f"Summary — {daily['date'].min():%d %b} to {daily['date'].max():%d %b} ({working_days} working days)")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Headcount", kpi["headcount"])
k2.metric("Avg. attendance", f"{kpi['avg_attendance_pct']}%")
k3.metric("Total OT hours", kpi["total_ot_hours"])
k4.metric("Total absent-days", kpi["total_absent_days"])

st.divider()

# --- Department breakdown -------------------------------------------------
st.subheader("By department")
c1, c2 = st.columns([1, 1])
with c1:
    st.dataframe(dept_summary, use_container_width=True, hide_index=True)
with c2:
    st.bar_chart(dept_summary.set_index("department")["avg_attendance_pct"])

st.divider()

# --- Employee-wise table ---------------------------------------------------
st.subheader("Employee-wise attendance")
st.dataframe(
    emp_summary.rename(columns={
        "emp_code": "Emp Code", "emp_name": "Name", "department": "Department",
        "designation": "Designation", "working_days": "Working Days",
        "present_days": "Present", "absent_days": "Absent",
        "paid_holiday_days": "Paid Holiday", "comp_off_days": "Comp Off",
        "personal_leave_days": "Personal Leave", "total_work_hours": "Total Hours",
        "avg_work_hours": "Avg Hours/Day", "total_ot_hours": "OT Hours",
        "attendance_pct": "Attendance %",
    }),
    use_container_width=True,
    hide_index=True,
)

st.divider()

# --- Date x Employee heatmap-style pivot -----------------------------------
st.subheader("Daily work-hours pivot")
pivot = metrics.date_by_employee_pivot(daily, value="work_hours")
st.dataframe(pivot, use_container_width=True, hide_index=True)

# --- Export -----------------------------------------------------------
st.divider()
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    emp_summary.to_excel(writer, sheet_name="Employee Summary", index=False)
    dept_summary.to_excel(writer, sheet_name="Department Summary", index=False)
    pivot.to_excel(writer, sheet_name="Daily Hours Pivot", index=False)
st.download_button(
    "Download summary (.xlsx)",
    data=buf.getvalue(),
    file_name="attendance_summary.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
