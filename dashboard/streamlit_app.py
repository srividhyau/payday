"""
Payday - Attendance Dashboard

Upload a DailyAttendance export here to preview it instantly, and optionally
save it to the Django database so it persists across sessions (same database
the Django /upload/ page and admin use). The dashboard always renders from
whatever's currently loaded: the database, an ad-hoc upload, or sample data.

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

from attendance.importer import import_dataframe  # noqa: E402
from attendance.models import AttendanceRecord  # noqa: E402
from src import metrics, parser  # noqa: E402

st.set_page_config(page_title="Payday - Attendance Dashboard", layout="wide")

st.title("Payday — Attendance Dashboard")
st.caption(
    "Upload a DailyAttendance export from the eSSL fingerprint system, or load "
    "what's already been saved to the database."
)


@st.cache_data(ttl=60)
def load_daily_data() -> pd.DataFrame:
    rows = AttendanceRecord.objects.select_related("employee", "employee__department").values(
        "employee__code",
        "employee__name",
        "employee__department__name",
        "employee__designation",
        "employee__category",
        "employee__subcategory",
        "employee__company",
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
            "employee__category": "category",
            "employee__subcategory": "subcategory",
            "employee__company": "company",
        }
    )
    df["department"] = df["department"].fillna("Unassigned")
    df["date"] = pd.to_datetime(df["date"])
    return df


# --- Sidebar: data source ---------------------------------------------------
with st.sidebar:
    st.header("1. Upload")
    uploaded = st.file_uploader(
        "DailyAttendance export (.xlsx, .xlsm, or .csv)",
        type=["xlsx", "xlsm", "xls", "csv"],
    )
    save_to_db = st.checkbox(
        "Save this upload to the database", value=True,
        help="Unchecked = preview only, nothing is stored. Checked = upserted into "
        "the same database the Django /upload/ page and admin use.",
    )
    if uploaded is not None:
        try:
            raw_df = parser.load_file(uploaded)
            uploaded_daily = parser.normalize(raw_df)
            if save_to_db:
                batch = import_dataframe(uploaded_daily, file_name=uploaded.name)
                st.success(f"Saved {batch.row_count} rows ({batch.period_start} to {batch.period_end}).")
                load_daily_data.clear()
            else:
                st.info(f"Previewing {len(uploaded_daily)} rows (not saved).")
        except Exception as e:
            st.error(f"Couldn't read that file: {e}")
            uploaded_daily = None
    else:
        uploaded_daily = None

    st.divider()
    if st.button("Refresh from database"):
        load_daily_data.clear()
    use_sample = st.checkbox("Use sample data instead (demo)", value=False)

    st.header("2. Working days")
    override_working_days = st.checkbox("Manually set working days for the period", value=False)
    manual_working_days = None
    if override_working_days:
        manual_working_days = st.number_input("Working days", min_value=1, max_value=31, value=26)

# --- Choose data source: fresh upload (preview) > sample > database --------
if uploaded_daily is not None and not save_to_db:
    daily = uploaded_daily
elif use_sample:
    daily = parser.normalize(pd.read_csv("sample_data/sample_daily_attendance.csv"))
else:
    daily = load_daily_data()

if daily.empty:
    st.info(
        "No attendance data yet. Upload a DailyAttendance export in the sidebar, "
        "or check 'Use sample data' to preview the dashboard."
    )
    st.stop()

# --- Filters -----------------------------------------------------------
min_date, max_date = daily["date"].min(), daily["date"].max()
departments = sorted(daily["department"].dropna().unique().tolist())
categories = sorted(c for c in daily.get("category", pd.Series(dtype=str)).dropna().unique().tolist() if c)

date_range = st.date_input(
    "Period", value=(min_date.date(), max_date.date()),
    min_value=min_date.date(), max_value=max_date.date(),
)
col_a, col_b = st.columns([1, 1])
with col_a:
    dept_filter = st.multiselect("Departments", departments, default=departments)
with col_b:
    cat_filter = st.multiselect("Categories", categories, default=categories) if categories else []

if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    daily = daily[(daily["date"] >= start) & (daily["date"] <= end)]
if dept_filter:
    daily = daily[daily["department"].isin(dept_filter)]
if cat_filter:
    daily = daily[daily["category"].isin(cat_filter)]

working_days = manual_working_days or metrics.infer_working_days(daily)
holidays = metrics.holiday_dates(daily)

emp_summary = metrics.employee_summary(daily, working_days)
dept_summary = metrics.department_summary(emp_summary)
kpi = metrics.kpis(emp_summary)

# --- Summary panel (Working Days / Holiday, like the top of Month_Attendance) --
st.subheader(f"Summary — {daily['date'].min():%d %b} to {daily['date'].max():%d %b}")
s1, s2 = st.columns([1, 3])
s1.metric("Working Days", working_days)
s2.markdown("**Holiday**: " + (", ".join(holidays) if holidays else "none flagged in this data"))

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

# --- Employee-wise table, grouped by department (Row Labels style) ---------
st.subheader("Employee-wise attendance — by department")
grouped_summary = metrics.department_grouped_summary(emp_summary)
st.dataframe(grouped_summary, use_container_width=True, hide_index=True, height=min(600, 40 + 35 * len(grouped_summary)))

st.divider()

# --- Date x Employee pivot, grouped by department (Month_Attendance style) --
st.subheader("Daily work-hours pivot — by department")
grouped_pivot = metrics.department_grouped_pivot(daily)
st.dataframe(grouped_pivot, use_container_width=True, hide_index=True, height=min(600, 40 + 35 * len(grouped_pivot)))

# --- Export -----------------------------------------------------------
st.divider()
flat_pivot = metrics.date_by_employee_pivot(daily, value="work_hours")
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    emp_summary.to_excel(writer, sheet_name="Employee Summary", index=False)
    dept_summary.to_excel(writer, sheet_name="Department Summary", index=False)
    grouped_pivot.to_excel(writer, sheet_name="Month_Attendance Style", index=False)
    flat_pivot.to_excel(writer, sheet_name="Daily Hours Pivot", index=False)
st.download_button(
    "Download summary (.xlsx)",
    data=buf.getvalue(),
    file_name="attendance_summary.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
