"""
Attendance metric calculations, mirroring the logic in the original
MonthlyAttendance / Month_Attendance / Summary pivot-table workbook:

  - Working Days: number of company working days in the period.
  - Present Days: days with status "P".
  - Personal Leave / Paid Holiday / Comp Off: counted from status codes.
  - Absent Days: working days not accounted for by any of the above.
  - OT Hours: sum of daily overtime hours.
  - Attendance %: present days / working days.
"""
from __future__ import annotations

import pandas as pd

STATUS_PRESENT = "P"
STATUS_ABSENT = "A"
STATUS_PAID_HOLIDAY = "PH"
STATUS_COMP_OFF = "CO"
STATUS_PERSONAL_LEAVE = "PL"
STATUS_WEEK_OFF = "WO"
STATUS_HOLIDAY = "H"


def infer_working_days(daily: pd.DataFrame) -> int:
    """Default working-day count: distinct dates present in the file that
    aren't universally marked Week Off/Holiday across employees. Callers can
    override this (the original sheet allowed manual entry, e.g. 26 or 27)."""
    if daily.empty:
        return 0
    all_off_per_date = daily.groupby("date")["status"].apply(
        lambda s: s.isin([STATUS_WEEK_OFF, STATUS_HOLIDAY]).all()
    )
    all_dates = daily["date"].nunique()
    off_dates = int(all_off_per_date.sum())
    working_days = all_dates - off_dates
    return working_days if working_days > 0 else all_dates


def employee_summary(daily: pd.DataFrame, working_days: int) -> pd.DataFrame:
    """One row per employee: the attendance-sheet-style rollup."""
    if daily.empty:
        return pd.DataFrame(
            columns=[
                "emp_code", "emp_name", "department", "designation",
                "working_days", "present_days", "absent_days",
                "paid_holiday_days", "comp_off_days", "personal_leave_days",
                "total_work_hours", "avg_work_hours", "total_ot_hours",
                "attendance_pct",
            ]
        )

    grp = daily.groupby(["emp_code", "emp_name", "department", "designation"], dropna=False)

    def summarize(g: pd.DataFrame) -> pd.Series:
        present = int((g["status"] == STATUS_PRESENT).sum())
        paid_holiday = int((g["status"] == STATUS_PAID_HOLIDAY).sum())
        comp_off = int((g["status"] == STATUS_COMP_OFF).sum())
        personal_leave = int((g["status"] == STATUS_PERSONAL_LEAVE).sum())
        accounted = present + paid_holiday + comp_off + personal_leave
        absent = max(working_days - accounted, 0)
        total_hours = float(g["work_hours"].sum())
        avg_hours = float(g.loc[g["work_hours"] > 0, "work_hours"].mean() or 0.0)
        ot_hours = float(g["ot_hours"].sum())
        pct = (present / working_days * 100) if working_days else 0.0
        return pd.Series(
            {
                "working_days": working_days,
                "present_days": present,
                "absent_days": absent,
                "paid_holiday_days": paid_holiday,
                "comp_off_days": comp_off,
                "personal_leave_days": personal_leave,
                "total_work_hours": round(total_hours, 2),
                "avg_work_hours": round(avg_hours, 2),
                "total_ot_hours": round(ot_hours, 2),
                "attendance_pct": round(pct, 1),
            }
        )

    result = grp.apply(summarize, include_groups=False).reset_index()
    return result.sort_values("department").reset_index(drop=True)


def department_summary(emp_summary: pd.DataFrame) -> pd.DataFrame:
    if emp_summary.empty:
        return pd.DataFrame(columns=["department", "headcount", "avg_attendance_pct", "total_ot_hours"])
    return (
        emp_summary.groupby("department")
        .agg(
            headcount=("emp_code", "nunique"),
            avg_attendance_pct=("attendance_pct", "mean"),
            total_ot_hours=("total_ot_hours", "sum"),
        )
        .round(1)
        .reset_index()
        .sort_values("headcount", ascending=False)
    )


def date_by_employee_pivot(daily: pd.DataFrame, value: str = "work_hours") -> pd.DataFrame:
    """Employee x Date matrix, mirroring the Month_Attendance pivot table.
    value: "work_hours" or "status"."""
    if daily.empty:
        return pd.DataFrame()
    values_col = value
    aggfunc = "sum" if value == "work_hours" else "first"
    pivot = daily.pivot_table(
        index=["emp_code", "emp_name"],
        columns="date",
        values=values_col,
        aggfunc=aggfunc,
    )
    pivot.columns = [c.strftime("%d-%b") for c in pivot.columns]
    return pivot.reset_index()


def kpis(emp_summary: pd.DataFrame) -> dict:
    if emp_summary.empty:
        return {"headcount": 0, "avg_attendance_pct": 0.0, "total_ot_hours": 0.0, "total_absent_days": 0}
    return {
        "headcount": int(emp_summary["emp_code"].nunique()),
        "avg_attendance_pct": round(float(emp_summary["attendance_pct"].mean()), 1),
        "total_ot_hours": round(float(emp_summary["total_ot_hours"].sum()), 1),
        "total_absent_days": int(emp_summary["absent_days"].sum()),
    }
