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
                "emp_code", "emp_name", "department", "designation", "category", "subcategory", "company",
                "working_days", "present_days", "absent_days",
                "paid_holiday_days", "comp_off_days", "personal_leave_days",
                "total_work_hours", "avg_work_hours", "total_ot_hours",
                "attendance_pct",
            ]
        )

    group_cols = ["emp_code", "emp_name", "department", "designation"]
    for optional_col in ("category", "subcategory", "company"):
        if optional_col in daily.columns:
            group_cols.append(optional_col)

    grp = daily.groupby(group_cols, dropna=False)

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


def department_grouped_pivot(daily: pd.DataFrame) -> pd.DataFrame:
    """Date x employee work-hours pivot, grouped under a department subtotal
    row — like the "Row Labels" hierarchy (department, then its employees)
    in the original Month_Attendance pivot table."""
    if daily.empty:
        return pd.DataFrame()

    date_cols = sorted(daily["date"].unique())
    col_labels = [pd.Timestamp(d).strftime("%d-%b") for d in date_cols]

    per_emp = daily.pivot_table(
        index=["department", "emp_code", "emp_name"], columns="date", values="work_hours", aggfunc="sum"
    ).reindex(columns=date_cols, fill_value=0.0)

    rows = []
    for dept in sorted(per_emp.index.get_level_values("department").unique()):
        dept_block = per_emp.xs(dept, level="department")
        dept_total = dept_block.sum(axis=0)
        rows.append(["▸ " + dept, "", *[round(v, 1) for v in dept_total.values]])
        for (emp_code, emp_name), vals in dept_block.iterrows():
            rows.append([f"    {emp_name}", emp_code, *[round(v, 1) for v in vals.values]])

    return pd.DataFrame(rows, columns=["Row Labels", "Emp Code", *col_labels])


def department_grouped_summary(emp_summary: pd.DataFrame) -> pd.DataFrame:
    """Employee summary table grouped under a department subtotal row,
    matching the department -> employees hierarchy of the original sheet."""
    if emp_summary.empty:
        return pd.DataFrame()

    metric_cols = [
        "working_days", "present_days", "absent_days", "paid_holiday_days",
        "comp_off_days", "personal_leave_days", "total_work_hours", "total_ot_hours",
    ]
    rows = []
    for dept in sorted(emp_summary["department"].unique()):
        block = emp_summary[emp_summary["department"] == dept]
        totals = block[metric_cols].sum()
        avg_pct = round(block["attendance_pct"].mean(), 1)
        rows.append(
            ["▸ " + dept, "", *[round(v, 1) for v in totals.values], avg_pct]
        )
        for _, r in block.iterrows():
            rows.append(
                [f"    {r['emp_name']}", r["emp_code"], *[r[c] for c in metric_cols], r["attendance_pct"]]
            )

    columns = [
        "Row Labels", "Emp Code", "Working Days", "Present", "Absent", "Paid Holiday",
        "Comp Off", "Personal Leave", "Total Hours", "OT Hours", "Attendance %",
    ]
    return pd.DataFrame(rows, columns=columns)


def month_attendance_view(daily: pd.DataFrame) -> tuple:
    """Single wide table matching the original Month_Attendance pivot sheet:
    department subtotal rows with indented employees below, one column per
    day-of-month with the day's work hours, and summary columns on the
    right (Work Days = present days, CompOff, Full OT, Paid Holiday,
    Personal Leave).

    "Full OT" is the count of days with any logged OT hours (ot_hours > 0)
    — the original workbook didn't document its exact definition, so this
    is the closest reasonable read; adjust FULL_OT_MIN_HOURS below if HR's
    definition differs.

    Returns (table, day_columns) — day_columns lists which columns are the
    per-day hours, so callers can style/colour just those.
    """
    FULL_OT_MIN_HOURS = 0.0  # a day counts as "Full OT" if ot_hours > this

    if daily.empty:
        return pd.DataFrame(), []

    dates = sorted(daily["date"].unique())
    single_month = len({(pd.Timestamp(d).year, pd.Timestamp(d).month) for d in dates}) == 1
    day_labels = [str(pd.Timestamp(d).day) for d in dates] if single_month else [
        pd.Timestamp(d).strftime("%d-%b") for d in dates
    ]

    hours_pivot = daily.pivot_table(
        index=["department", "emp_code", "emp_name"], columns="date", values="work_hours", aggfunc="sum"
    ).reindex(columns=dates, fill_value=0.0)

    def emp_counts(g: pd.DataFrame) -> dict:
        return {
            "Work Days": int((g["status"] == STATUS_PRESENT).sum()),
            "CompOff": int((g["status"] == STATUS_COMP_OFF).sum()),
            "Full OT": int((g["ot_hours"] > FULL_OT_MIN_HOURS).sum()),
            "Paid Holiday": int((g["status"] == STATUS_PAID_HOLIDAY).sum()),
            "Personal Leave": int((g["status"] == STATUS_PERSONAL_LEAVE).sum()),
        }

    per_emp_counts = {
        key: emp_counts(g) for key, g in daily.groupby(["department", "emp_code", "emp_name"])
    }

    summary_cols = ["Work Days", "CompOff", "Full OT", "Paid Holiday", "Personal Leave"]
    rows = []
    for dept in sorted(hours_pivot.index.get_level_values("department").unique()):
        dept_block = hours_pivot.xs(dept, level="department")
        dept_keys = [k for k in per_emp_counts if k[0] == dept]
        dept_totals = {c: sum(per_emp_counts[k][c] for k in dept_keys) for c in summary_cols}
        rows.append(
            ["▸ " + dept, "", *[float("nan")] * len(day_labels), *[dept_totals[c] for c in summary_cols]]
        )
        for (emp_code, emp_name), vals in dept_block.iterrows():
            counts = per_emp_counts[(dept, emp_code, emp_name)]
            day_vals = [round(v, 1) if v > 0 else float("nan") for v in vals.values]
            rows.append(
                [f"    {emp_code} - {emp_name}", emp_code, *day_vals, *[counts[c] for c in summary_cols]]
            )

    columns = ["Row Labels", "Emp Code", *day_labels, *summary_cols]
    table = pd.DataFrame(rows, columns=columns)
    # Force the day columns to a proper float dtype (they'd otherwise end up
    # as 'object' from being built via a list of mixed rows), so NaN is
    # handled consistently by the Styler instead of rendering as "None".
    table[day_labels] = table[day_labels].astype(float)
    return table, day_labels


def style_month_attendance(table: pd.DataFrame, day_labels: list):
    """Pandas Styler for month_attendance_view(): green-yellow-red heatmap
    on the day-hours columns (blank cells stay blank), and a shaded
    background on department subtotal rows — matching the original
    workbook's conditional formatting."""
    is_dept_row = table["Row Labels"].str.startswith("▸")

    def shade_dept_rows(row):
        if is_dept_row.loc[row.name]:
            return ["background-color: #FCE4D6"] * len(row)
        return [""] * len(row)

    styler = table.style.apply(shade_dept_rows, axis=1)
    if day_labels:
        styler = styler.background_gradient(
            subset=day_labels, cmap="RdYlGn", vmin=0, vmax=10, axis=None
        )
        styler = styler.format(precision=1, na_rep="", subset=day_labels)
    return styler


def holiday_dates(daily: pd.DataFrame) -> list:
    """Dates flagged Holiday (status 'H') for at least one employee — shown
    in the summary panel the way the original sheet listed them."""
    if daily.empty or "status" not in daily.columns:
        return []
    dates = daily.loc[daily["status"] == STATUS_HOLIDAY, "date"].dropna().unique()
    return sorted(pd.Timestamp(d).strftime("%d-%b") for d in dates)


def kpis(emp_summary: pd.DataFrame) -> dict:
    if emp_summary.empty:
        return {"headcount": 0, "avg_attendance_pct": 0.0, "total_ot_hours": 0.0, "total_absent_days": 0}
    return {
        "headcount": int(emp_summary["emp_code"].nunique()),
        "avg_attendance_pct": round(float(emp_summary["attendance_pct"].mean()), 1),
        "total_ot_hours": round(float(emp_summary["total_ot_hours"].sum()), 1),
        "total_absent_days": int(emp_summary["absent_days"].sum()),
    }
