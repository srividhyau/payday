"""
Payday - Attendance Dashboard

Upload a DailyAttendance export from the eSSL fingerprint system and get an
attendance dashboard equivalent to the old MonthlyAttendance / Month_Attendance
/ Summary pivot-table workbook: KPIs, an employee-wise rollup, a date x
employee heatmap, and a department breakdown.

Run with: streamlit run app.py
"""
import io

import pandas as pd
import streamlit as st

from src import metrics, parser

st.set_page_config(page_title="Payday - Attendance Dashboard", layout="wide")

st.title("Payday — Attendance Dashboard")
st.caption(
    "Upload the DailyAttendance export from the eSSL fingerprint system to "
    "generate the monthly attendance rollup."
)

with st.sidebar:
    st.header("1. Upload")
    uploaded = st.file_uploader(
        "DailyAttendance export (.xlsx, .xlsm, or .csv)",
        type=["xlsx", "xlsm", "xls", "csv"],
    )
    use_sample = st.checkbox("Use sample data instead", value=uploaded is None)

    st.header("2. Working days")
    override_working_days = st.checkbox("Manually set working days for the period", value=False)
    manual_working_days = None
    if override_working_days:
        manual_working_days = st.number_input("Working days", min_value=1, max_value=31, value=26)

if uploaded is None and not use_sample:
    st.info("Upload a DailyAttendance file, or check 'Use sample data' in the sidebar to preview the dashboard.")
    st.stop()

try:
    if use_sample:
        raw_df = pd.read_csv("sample_data/sample_daily_attendance.csv")
    else:
        raw_df = parser.load_file(uploaded)
    daily = parser.normalize(raw_df)
except Exception as e:
    st.error(f"Couldn't read that file: {e}")
    st.stop()

if daily.empty:
    st.warning("No valid attendance rows found in the file.")
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
