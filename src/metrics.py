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

import datetime

import pandas as pd

STATUS_PRESENT = "P"
STATUS_ABSENT = "A"
STATUS_PAID_HOLIDAY = "PH"
STATUS_COMP_OFF = "CO"
STATUS_PERSONAL_LEAVE = "PL"
STATUS_WEEK_OFF = "WO"
STATUS_HOLIDAY = "H"


def work_day_credit(work_hours) -> float:
    """Weights a single day's attendance credit from its work_hours,
    matching the source workbook's Attendance formula: <=3h counts as
    Absent (0 credit), <=5.5h as a Half day (0.5 credit), otherwise a
    full Present (1 credit). Used instead of counting status == "P" so a
    day marked Present with only a couple of logged hours doesn't count
    as a full work day."""
    if work_hours is None or pd.isna(work_hours):
        return 0.0
    work_hours = float(work_hours)
    if work_hours <= 3:
        return 0.0
    if work_hours <= 5.5:
        return 0.5
    return 1.0


def recompute_from_punch(time_in: str, time_out: str) -> tuple[float, str]:
    """Recomputes work_hours and a status code (A/HD/P) from a corrected
    in/out punch pair — used when HR manually fixes a missed punch via the
    dashboard's edit popup. Mirrors work_day_credit's own thresholds
    (<=3h Absent, <=5.5h Half Day, otherwise Present)."""
    t_in, t_out = _parse_time_str(time_in), _parse_time_str(time_out)
    if t_in is None or t_out is None:
        return 0.0, STATUS_ABSENT
    minutes = _minutes_between(t_in, t_out)
    if minutes < 0:
        minutes += 24 * 60  # overnight shift
    hours = round(minutes / 60, 2)
    if hours <= 3:
        status = STATUS_ABSENT
    elif hours <= 5.5:
        status = "HD"
    else:
        status = STATUS_PRESENT
    return hours, status


def infer_working_days(daily: pd.DataFrame) -> int:
    """Default working-day count: distinct dates present in the file that
    aren't a day off. A date counts as off if every employee is marked Week
    Off that day (WO can legitimately vary per employee), or if *any*
    employee is marked Holiday that day (H only ever comes from the
    company-wide SpecialDay calendar — apply_special_days only sets it for
    employees who didn't work, so one person working through a Holiday
    shouldn't stop it counting as a day off for everyone else). Callers can
    override this (the original sheet allowed manual entry, e.g. 26 or 27)."""
    if daily.empty:
        return 0
    off_per_date = daily.groupby("date")["status"].apply(
        lambda s: (s == STATUS_WEEK_OFF).all() or (s == STATUS_HOLIDAY).any()
    )
    all_dates = daily["date"].nunique()
    off_dates = int(off_per_date.sum())
    working_days = all_dates - off_dates
    return working_days if working_days > 0 else all_dates


def employee_summary(
    daily: pd.DataFrame, working_days: int, shift_ot_map: dict | None = None
) -> pd.DataFrame:
    """One row per employee: the attendance-sheet-style rollup.

    shift_ot_map, if given, maps emp_code -> total shift-based OT hours
    (see overtime_view) for the period, and overrides total_ot_hours so it
    matches the /ot/ page's calculation instead of the raw ot_hours
    column. Pass None to keep the raw-column total (e.g. for Staff, who
    are excluded from shift-based OT entirely)."""
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
        present = round(float(_work_day_credit_series(g).sum()), 1)
        paid_holiday = int((g["status"] == STATUS_PAID_HOLIDAY).sum())
        comp_off = int((g["status"] == STATUS_COMP_OFF).sum())
        personal_leave = int((g["status"] == STATUS_PERSONAL_LEAVE).sum())
        # Personal Leave = TotalWorkingDays - (Paid_Holidays + Working_Days +
        # Comp_Off) — matches the source workbook exactly. Holiday isn't
        # subtracted again here because infer_working_days() already drops
        # Holiday dates out of working_days itself, so subtracting
        # holiday_days too would double-count that exclusion.
        accounted = present + paid_holiday + comp_off
        absent = max(round(working_days - accounted, 1), 0)
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
    if shift_ot_map is not None:
        result["total_ot_hours"] = (
            result["emp_code"].map(shift_ot_map).fillna(0.0).round(2)
        )
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


def department_attendance_extremes(emp_summary: pd.DataFrame, n: int = 2) -> dict:
    """For each department, the n employees with the highest and n with the
    lowest attendance_pct — e.g. for a "top / bottom performers" callout
    next to the department summary table. Each value is
    {"top": [...], "worst": [...]}, worst-first, as {"emp_name", "attendance_pct"} dicts."""
    if emp_summary.empty:
        return {}
    result = {}
    for dept, block in emp_summary.groupby("department"):
        ranked = block.sort_values("attendance_pct", ascending=False)
        result[dept] = {
            "top": ranked.head(n)[["emp_name", "attendance_pct"]].to_dict("records"),
            "worst": ranked.tail(n).iloc[::-1][["emp_name", "attendance_pct"]].to_dict("records"),
        }
    return result


def department_high_leave(emp_summary: pd.DataFrame, threshold: int = 4) -> dict:
    """For each department, employees whose absent_days exceeds threshold —
    a "watch list" callout next to the department summary table. Uses
    absent_days rather than personal_leave_days because real eSSL exports
    tend to only ever use the P/A status codes in practice — PH/CO/PL/WO/H
    show up as zero, so personal_leave_days alone misses real leave-taking.
    Maps department -> [{"emp_name", "absent_days"}, ...], highest first."""
    if emp_summary.empty:
        return {}
    result = {}
    for dept, block in emp_summary.groupby("department"):
        flagged = block[block["absent_days"] > threshold].sort_values(
            "absent_days", ascending=False
        )
        result[dept] = flagged[["emp_name", "absent_days"]].to_dict("records")
    return result


def department_missed_punch_summary(daily: pd.DataFrame, issues: set) -> list:
    """Departments with at least one missed-out-punch case (see
    punch_issues), each with a total count and the individual employees
    (name + their own count) behind it — for the "By department" panel's
    Missed Punch view. Highest department count first, employees within a
    department highest count first."""
    if not issues or daily.empty:
        return []
    emp_dept = dict(daily[["emp_code", "department"]].drop_duplicates().values)
    emp_name = dict(daily[["emp_code", "emp_name"]].drop_duplicates().values)
    dept_counts: dict = {}
    emp_counts: dict = {}
    for emp_code, _ in issues:
        dept = emp_dept.get(emp_code, "Unassigned")
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
        emp_counts[emp_code] = emp_counts.get(emp_code, 0) + 1

    rows = []
    for dept, total in dept_counts.items():
        people = sorted(
            (
                {"emp_name": emp_name.get(emp_code, emp_code), "count": count}
                for emp_code, count in emp_counts.items()
                if emp_dept.get(emp_code) == dept
            ),
            key=lambda p: -p["count"],
        )
        rows.append({"department": dept, "missed_count": total, "people": people})
    return sorted(rows, key=lambda row: -row["missed_count"])


def department_ot_summary(shift_ot_table: pd.DataFrame) -> list:
    """Per-department shift-based OT rollup — headcount with any OT, who
    logged the most/least, and the department total — for the "By
    department" panel's OT View. Highest total first."""
    if shift_ot_table.empty:
        return []
    per_emp = (
        shift_ot_table.groupby(["department", "emp_code", "emp_name"])["total_ot_hours"]
        .sum()
        .reset_index()
    )
    rows = []
    for dept, group in per_emp.groupby("department"):
        group = group.sort_values("total_ot_hours", ascending=False)
        top = group.iloc[0]
        lowest = group.iloc[-1]
        rows.append({
            "department": dept,
            "headcount": len(group),
            "top_name": top["emp_name"],
            "top_hours": round(float(top["total_ot_hours"]), 2),
            "lowest_name": lowest["emp_name"],
            "lowest_hours": round(float(lowest["total_ot_hours"]), 2),
            "total_hours": round(float(group["total_ot_hours"].sum()), 2),
        })
    return sorted(rows, key=lambda row: -row["total_hours"])


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


def month_attendance_view(
    daily: pd.DataFrame, working_days: int | None = None, dates: list | None = None
) -> tuple:
    """Single wide table matching the original Month_Attendance pivot sheet:
    a department label row (grouping only, no aggregated values) followed
    by its employees indented below, one column per day-of-month with the
    day's work hours, and summary columns on the right (Work Days = present
    days, CompOff, Time Off, Paid Holiday, Personal Leave).

    "Time Off" is credited per day using the same tiered thresholds as
    Work Days (work_day_credit: <=3h -> 0, <=5.5h -> half day, >5.5h ->
    full day), applied to ot_hours instead of work_hours, then summed —
    and only for employees whose subcategory is "Staff" (everyone else
    shows blank, this metric doesn't apply to them).

    "Personal Leave" = working_days - (Paid Holiday + Work Days + Comp
    Off), same formula as employee_summary()'s absent_days. working_days
    defaults to infer_working_days(daily) if not given explicitly.

    Returns (table, day_columns) — day_columns lists which columns are the
    per-day hours, so callers can style/colour just those.
    """
    if daily.empty:
        return pd.DataFrame(), []

    if working_days is None:
        working_days = infer_working_days(daily)

    # `dates` defaults to only the days that actually have a record, which
    # blanks out any day nobody has marked/uploaded yet — pass every day of
    # the month in explicitly (see _build_month_grid) to show the full
    # calendar grid regardless of data gaps.
    if dates is None:
        dates = sorted(daily["date"].unique())
    single_month = len({(pd.Timestamp(d).year, pd.Timestamp(d).month) for d in dates}) == 1
    day_labels = [str(pd.Timestamp(d).day) for d in dates] if single_month else [
        pd.Timestamp(d).strftime("%d-%b") for d in dates
    ]

    hours_pivot = daily.pivot_table(
        index=["department", "emp_code", "emp_name"], columns="date", values="work_hours", aggfunc="sum"
    ).reindex(columns=dates, fill_value=0.0)

    def emp_counts(g: pd.DataFrame) -> dict:
        is_staff = "subcategory" in g.columns and (g["subcategory"] == "Staff").any()
        time_off = round(float(g["ot_hours"].apply(work_day_credit).sum()), 1) if is_staff else ""
        work_days = round(float(_work_day_credit_series(g).sum()), 1)
        comp_off = int((g["status"] == STATUS_COMP_OFF).sum())
        paid_holiday = int((g["status"] == STATUS_PAID_HOLIDAY).sum())
        personal_leave = max(round(working_days - (work_days + paid_holiday + comp_off), 1), 0)
        return {
            "Work Days": work_days,
            "Comp Off": comp_off or "",
            "Time Off": time_off,
            "Paid Holiday": paid_holiday or "",
            "Personal Leave": personal_leave,
        }

    per_emp_counts = {
        key: emp_counts(g) for key, g in daily.groupby(["department", "emp_code", "emp_name"])
    }

    summary_cols = ["Work Days", "Comp Off", "Time Off", "Paid Holiday", "Personal Leave"]
    rows = []
    for dept in sorted(hours_pivot.index.get_level_values("department").unique()):
        dept_block = hours_pivot.xs(dept, level="department")
        rows.append(
            ["▸ " + dept, "", *[float("nan")] * len(day_labels), *[""] * len(summary_cols)]
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
    # as 'object' from being built via a list of mixed rows).
    table[day_labels] = table[day_labels].astype(float)
    return table, day_labels


def _punch_missing(value: str) -> bool:
    """The eSSL export uses an all-zero time ("00:00", "0:00", "00:00:00",
    ...) as its placeholder for "no punch recorded", same as a genuinely
    empty string. Checked digit-by-digit since the export isn't consistent
    about the leading zero or whether seconds are included."""
    v = (value or "").strip()
    if not v:
        return True
    digits = v.replace(":", "")
    return digits != "" and set(digits) == {"0"}


def clean_punch_time(value: str) -> str:
    """Public wrapper around _punch_missing — returns "" for a missing/
    all-zero punch placeholder ("0:00" etc.), otherwise the stripped
    value. Use this anywhere a raw time_in/time_out is about to be shown
    or handed to the browser, so "0:00" never displays as if it were a
    real punch time."""
    return "" if _punch_missing(value) else (value or "").strip()


def punch_time_labels(
    daily: pd.DataFrame, ot_hours_map: dict | None = None, ot_rate_map: dict | None = None
) -> dict:
    """Maps (emp_code, date) -> "DATE - DD-Mon-YYYY\\nIN - HH:MM:SS\\n
    OUT - HH:MM:SS\\nHRS - N\\nSHIFT - X\\nOT - Nh" text (one field per
    line — see the tooltip's `white-space: pre-line` in dashboard.html),
    so callers can label/tooltip the month_attendance_view() day cells
    with the date, punch times, worked hours, shift code, and OT hours
    without cluttering the heatmap grid itself — the date line matters
    most when the grid is scrolled far enough that the column header
    isn't visible alongside the hovered cell. emp_code alone is enough to
    key on since Employee.code is unique across departments. Skips days
    with no punch at all (plain absence); says "missing" for whichever
    side is missing on a one-sided punch.

    ot_hours_map, if given, overrides the raw ot_hours column — pass
    overtime_view()'s per-record total_ot_hours (keyed the same way) so
    the label reflects the actual shift-based OT calculation rather than
    whatever the source file happened to report.

    ot_rate_map, if given (emp_code -> Employee.ot_rate_per_hour), adds
    "OT RATE - <rate>/hr" and "OT AMT - <rate x ot_hours>" lines — used by
    the OT View tooltip."""
    labels = {}
    for row in daily.itertuples(index=False):
        in_missing = _punch_missing(row.time_in)
        out_missing = _punch_missing(row.time_out)
        if in_missing and out_missing:
            continue
        time_in = "missing" if in_missing else row.time_in.strip()
        time_out = "missing" if out_missing else row.time_out.strip()
        shift = row.shift.strip() if isinstance(row.shift, str) and row.shift.strip() else "—"
        hrs = round(float(row.work_hours), 1) if pd.notna(row.work_hours) else 0.0
        key = (row.emp_code, row.date)
        if ot_hours_map is not None:
            ot_hours = round(float(ot_hours_map.get(key, 0.0)), 1)
        else:
            ot_hours = round(float(row.ot_hours), 1) if pd.notna(row.ot_hours) else 0.0
        date_label = pd.Timestamp(row.date).strftime("%d-%b-%Y")
        label = f"DATE - {date_label}\nIN - {time_in}\nOUT - {time_out}\nHRS - {hrs}\nSHIFT - {shift}\nOT - {ot_hours}h"
        if ot_rate_map is not None:
            rate = float(ot_rate_map.get(row.emp_code, 0.0))
            label += f"\nOT RATE - {rate}/hr\nOT AMT - {round(ot_hours * rate, 2)}"
        labels[key] = label
    return labels


def punch_issues(daily: pd.DataFrame) -> set:
    """Set of (emp_code, date) with exactly one side of the punch pair
    missing — a real in-punch but no out-punch ("forgot to clock out"), or
    a real out-punch but no in-punch (a stray one-sided punch) — distinct
    from a plain absence where both are missing."""
    issues = set()
    for row in daily.itertuples(index=False):
        in_missing = _punch_missing(row.time_in)
        out_missing = _punch_missing(row.time_out)
        if in_missing != out_missing:
            issues.add((row.emp_code, row.date))
    return issues


HEAT_COLORS = {
    "red": "#F1A9A9",  # light red — 0.5-5h, a very short day
    "green": "#D4EDDA",  # soft sage — 5-9.5h, normal range
    "mid": "#FFE8A1",  # warm gold — 9.5-10.5h, expected full day + some OT
    "high": "#E8C4E8",  # dusty plum — 10.5-11.5h, heavy OT
    "veryhigh": "#C9B8E8",  # lavender — past 11.5h, a darker/more saturated heavy-OT flag
}


def hours_heat_band(value) -> str | None:
    """Discrete hours heat-map band matching the original workbook's
    conditional formatting."""
    if value == "" or value is None or pd.isna(value):
        return None
    value = float(value)
    if value < 0.5:
        return None
    if value < 5:
        return "red"
    if value < 9.5:
        return "green"
    if value <= 10.5:
        return "mid"
    if value <= 11.5:
        return "high"
    return "veryhigh"


def apply_special_days(daily: pd.DataFrame, special_days: dict) -> pd.DataFrame:
    """Overlays a company-wide Holiday/Paid Holiday/Comp Off calendar (see
    the SpecialDay model) onto the daily attendance DataFrame before
    metrics are computed. special_days maps date -> "H"/"PH"/"CO".

    All three types apply to everyone on that date, unconditionally —
    status is overridden regardless of whether the employee actually came
    in, so Working Days / Paid Holiday / Comp Off counts always credit the
    whole company for that date, matching the source workbook.

    Separately, anyone who *did* work that day gets it folded into
    ot_hours (so Time Off picks it up) and flagged via the "special_worked"
    column. Callers use that flag for two things the status override alone
    can't express: excluding the day from Work Days credit (it counts as
    OT instead), and — in the Django dashboard grid — showing the real
    heat-map color for that cell instead of the flat special-day color,
    since status says "H"/"PH"/"CO" either way.
    """
    daily = daily.copy()
    daily["special_worked"] = False
    if daily.empty or not special_days:
        return daily
    for raw_date, day_type in special_days.items():
        if day_type not in (STATUS_HOLIDAY, STATUS_PAID_HOLIDAY, STATUS_COMP_OFF):
            continue
        mask = daily["date"] == pd.Timestamp(raw_date)
        if not mask.any():
            continue
        worked = mask & (daily["work_hours"] > 0)
        daily.loc[mask, "status"] = day_type
        daily.loc[worked, "ot_hours"] = daily.loc[worked, ["ot_hours", "work_hours"]].max(axis=1)
        daily.loc[worked, "special_worked"] = True
    return daily


def _work_day_credit_series(g: pd.DataFrame) -> pd.Series:
    """Per-row Work Day credit for a group, zeroing out any day flagged
    special_worked (a Paid Holiday/Holiday/Comp Off someone worked through
    — that day's hours count toward Time Off instead, via apply_special_days
    folding them into ot_hours)."""
    credit = g["work_hours"].apply(work_day_credit)
    if "special_worked" in g.columns:
        credit = credit.where(~g["special_worked"], 0.0)
    return credit


def holiday_dates(daily: pd.DataFrame) -> list:
    """Dates flagged Holiday (status 'H') for at least one employee — shown
    in the summary panel the way the original sheet listed them."""
    if daily.empty or "status" not in daily.columns:
        return []
    dates = daily.loc[daily["status"] == STATUS_HOLIDAY, "date"].dropna().unique()
    return sorted(pd.Timestamp(d).strftime("%d-%b") for d in dates)


def paid_holiday_dates(daily: pd.DataFrame) -> list:
    """Dates flagged Paid Holiday (status 'PH') for at least one employee —
    same idea as holiday_dates(), for the company calendar's Paid Holiday
    days."""
    if daily.empty or "status" not in daily.columns:
        return []
    dates = daily.loc[daily["status"] == STATUS_PAID_HOLIDAY, "date"].dropna().unique()
    return sorted(pd.Timestamp(d).strftime("%d-%b") for d in dates)


def comp_off_dates(daily: pd.DataFrame) -> list:
    """Dates flagged Comp Off (status 'CO') for at least one employee — same
    idea as holiday_dates(), for the company calendar's Comp Off days."""
    if daily.empty or "status" not in daily.columns:
        return []
    dates = daily.loc[daily["status"] == STATUS_COMP_OFF, "date"].dropna().unique()
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


# Shift codes that pre-authorize overtime, and which side of the shift they
# cover — mirrors the source workbook's DailyAttendance_OT Power Query.
_IN_OT_SHIFTS = {"M-OT", "ME-OT", "Full-OT"}  # counts time clocked in before 9:00 AM
_OUT_OT_SHIFTS = {"E-OT", "ME-OT", "Full-OT"}  # counts time clocked out after 5:30 PM

# All shift codes that pre-authorize OT — public, for callers that just
# need to flag/color a day by its shift code (e.g. the dashboard grid)
# without recomputing hours themselves.
OT_SHIFT_CODES = _IN_OT_SHIFTS | _OUT_OT_SHIFTS
_OT_NINE_AM = datetime.time(9, 0, 0)
_OT_FIVE_THIRTY_PM = datetime.time(17, 30, 0)
_OT_SIX_AM = datetime.time(6, 0, 0)
# A Full-OT day (a worked Holiday/Paid Holiday/Comp Off) worked for more
# than this many hours credits a full EL day; 6 hours or less credits
# half a day instead (see overtime_view's "el_day_credit" column) — its
# own threshold, distinct from LeaveLedgerEntry.COMP_OFF_HOUR_THRESHOLD.
_EL_FULL_DAY_HOURS = 6


def _parse_time_str(value) -> datetime.time | None:
    text = (str(value) if value is not None else "").strip()
    if not text or text.lower() == "nan":
        return None
    parts = text.split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
        sec = int(parts[2]) if len(parts) > 2 else 0
        return datetime.time(h % 24, m, sec)
    except (ValueError, IndexError):
        return None


_SUGGEST_MORNING_IN = datetime.time(8, 20, 0)
_SUGGEST_EVENING_OUT = datetime.time(18, 0, 0)


def suggest_shift_code(
    time_in, time_out, special_worked: bool = False, department: str = "", work_hours=None
) -> str:
    """Suggests which OT shift code an ordinary GS day's punches look like
    they should have had, purely from the actual times worked (or the
    company calendar) — never changes the stored shift, just a visual
    nudge in the dashboard's OT view that a shift code was probably
    forgotten:

    - Worked a Holiday/Paid Holiday/Comp Off (special_worked) -> "Full-OT",
      regardless of specific times.
    - House Keeping doesn't run a fixed shift schedule like the other
      departments, so it isn't judged by clock-in/out time of day at all —
      any day worked more than 8.5h is "ME-OT" outright.
    - Clocked in before 8:20 AM AND out after 6:00 PM -> "ME-OT".
    - Clocked in before 8:20 AM only -> "M-OT".
    - Clocked out after 6:00 PM, OR clocked out after midnight (before
      6:00 AM — an overnight shift, e.g. clocked out 0:32 AM) -> "E-OT".
    - Otherwise -> "" (no suggestion, an ordinary GS day)."""
    if special_worked:
        return "Full-OT"
    if _punch_missing(time_in) or _punch_missing(time_out):
        return ""
    if department == "HOUSE KEEPING":
        return "ME-OT" if work_hours is not None and not pd.isna(work_hours) and float(work_hours) > 8.5 else ""
    t_in, t_out = _parse_time_str(time_in), _parse_time_str(time_out)
    early_in = t_in is not None and t_in < _SUGGEST_MORNING_IN
    # A time-of-day comparison alone can't tell "0:32 AM" apart from an
    # ordinary early morning — it only means "very late" if the person
    # worked into the next day, which a plain t_out > 18:00 check misses
    # entirely (0:32 sorts as earlier than 18:00, not later).
    late_out = t_out is not None and (t_out > _SUGGEST_EVENING_OUT or t_out < _OT_SIX_AM)
    if early_in and late_out:
        return "ME-OT"
    if early_in:
        return "M-OT"
    if late_out:
        return "E-OT"
    return ""


def _round_to_quarter_hour(total_minutes: float) -> float:
    """<20 minutes doesn't count at all; otherwise round to the nearest
    15-minute increment, expressed in hours — matches the source Power
    Query's OT rounding exactly."""
    if total_minutes < 20:
        return 0.0
    return round(round(total_minutes / 15) * 15 / 60, 2)


def _minutes_between(start: datetime.time, end: datetime.time) -> float:
    today = datetime.date.today()
    return (datetime.datetime.combine(today, end) - datetime.datetime.combine(today, start)).total_seconds() / 60


def overtime_view(daily: pd.DataFrame) -> pd.DataFrame:
    """Shift-based OT view, mirroring the source workbook's
    DailyAttendance_OT Power Query. Only applies to employees on a
    special OT-approved shift code for that day (not the ordinary "GS"
    shift):

    - "M-OT"/"ME-OT"/"Full-OT" credits time clocked in before 9:00 AM.
    - "E-OT"/"ME-OT"/"Full-OT" credits time clocked out after 5:30 PM
      (handling the overnight case where "Out" is a time after midnight).
    - "Full-OT" instead counts the entire day's worked hours as OT.

    HOUSE KEEPING is the one exception to the shift-code requirement:
    that department doesn't run a fixed shift schedule, so it's never
    judged by clock-in/out time of day (or by whether a shift code was
    even set) — any day worked over 8.5h simply credits
    (work_hours - 8.5) as OT, full stop. A Full-OT day (a holiday worked)
    still credits the entire span instead, same as every other
    department.

    Staff subcategory employees are the other exception: rather than
    requiring a shift code to be set per day (edit_record_view still
    won't let one be set on their AttendanceRecord — this only affects
    the OT calculation itself), every Staff day is treated as if it were
    "ME-OT" — both the before-9AM and after-5:30PM credit sides apply
    automatically, on GS days too — EXCEPT a day flagged special_worked
    (they actually worked a declared Holiday/Paid Holiday/Comp Off, see
    apply_special_days), which instead gets "Full-OT" treatment: the
    entire day's hours count as OT, not just the two slivers outside
    9AM-5:30PM. That distinction matters beyond just the bigger number —
    a Full-OT day is also what LeaveLedgerEntry credits toward a Staff
    employee's EL balance (see its own docstring), so an ordinary heavy
    day (early in, late out) stays ME-OT and funds OT pay only, while an
    actual holiday worked funds both OT pay and EL. This is a deliberate
    simplification (no per-day OT authorization step for Staff, unlike
    every other subcategory) rather than a reflection of anything in
    their actual shift column, which stays whatever it already was
    (typically "GS"). The "effective_shift" output column carries this
    stand-in value (as opposed to "shift", which always shows the real
    punched-in shift code) so callers can render/color a Staff day the
    same way any other employee's real M-OT/E-OT/ME-OT/Full-OT day is.

    A Full-OT day also gets an "el_day_credit" value — 1.0 if that day's
    work_hours exceeds _EL_FULL_DAY_HOURS (6h), otherwise 0.5 for 6h or
    less worked. 0.0 on every non-Full-OT day. This is what LeaveLedgerEntry
    actually credits per day (not a flat 1 per Full-OT day) — computed
    here, once, so the OT page and the EL ledger can never disagree about
    how many EL days a given Full-OT day is worth.

    Both sides round to the nearest 15 minutes and require at least 20
    minutes to count at all. Only rows already marked Present are
    considered, matching the source query's own pre-filter.

    Returns a flat DataFrame (one row per employee/date with real OT),
    sorted by date then department then employee — empty if nothing
    qualifies (e.g. the uploaded file only ever uses the "GS" shift).
    """
    columns = [
        "date", "emp_code", "emp_name", "department", "designation", "shift",
        "effective_shift", "time_in", "time_out", "work_hours", "in_ot_hours",
        "out_ot_hours", "total_ot_hours", "full_day_ot", "el_day_credit",
    ]
    if daily.empty:
        return pd.DataFrame(columns=columns)

    # status == Present, OR special_worked (apply_special_days overwrites
    # status to H/PH/CO for every employee on a special calendar day, even
    # ones who actually worked it — special_worked is what still marks
    # those as real attendance, so an OT shift set on a holiday isn't
    # silently dropped here). Callers that skip apply_special_days never
    # get a special_worked column, so status alone decides for them.
    if "special_worked" in daily.columns:
        present_mask = (daily["status"] == STATUS_PRESENT) | daily["special_worked"]
    else:
        present_mask = daily["status"] == STATUS_PRESENT
    present = daily[present_mask].copy()
    is_staff = (
        present["subcategory"] == "Staff" if "subcategory" in present.columns
        else pd.Series(False, index=present.index)
    )
    # HOUSE KEEPING is judged purely on hours worked (see docstring), and
    # Staff are always treated as "ME-OT" (see docstring) — neither is
    # ever excluded here just for sitting on the ordinary "GS" shift the
    # way every other department/subcategory is.
    is_house_keeping = present["department"] == "HOUSE KEEPING"
    present = present[(present["shift"] != "GS") | is_house_keeping | is_staff]
    if present.empty:
        return pd.DataFrame(columns=columns)

    # Staff's real shift column (typically "GS") is left untouched in the
    # output — this is purely an internal stand-in so the same shift-code
    # branching below treats every Staff day as "ME-OT", or "Full-OT" on
    # a day they worked a declared Holiday/Paid Holiday/Comp Off (see
    # docstring), without special-casing every function.
    is_staff = (
        present["subcategory"] == "Staff" if "subcategory" in present.columns
        else pd.Series(False, index=present.index)
    )
    present["_effective_shift"] = present["shift"].mask(is_staff, "ME-OT")
    if "special_worked" in present.columns:
        present.loc[is_staff & present["special_worked"], "_effective_shift"] = "Full-OT"

    def work_hours_span(row) -> float:
        t_in, t_out = _parse_time_str(row["time_in"]), _parse_time_str(row["time_out"])
        if t_in is None or t_out is None:
            return 0.0
        minutes = _minutes_between(t_in, t_out)
        if minutes < 0:
            minutes += 24 * 60  # overnight shift
        hours = round(minutes / 60, 2)
        # The 4h floor only makes sense as a "did they really show up"
        # gate for the M-OT/E-OT partial-day credit below — Full-OT counts
        # the entire span worked as OT regardless of length, so it must
        # skip the floor (a holiday worked for e.g. 3.5h should credit
        # 3.5h OT, not 0). HOUSE KEEPING skips it too since its OT is
        # computed straight off this same span (see total_ot_hours below).
        if row["_effective_shift"] == "Full-OT" or row["department"] == "HOUSE KEEPING":
            return hours
        return hours if hours >= 4 else 0.0

    def in_ot_hours(row) -> float:
        if row["_effective_shift"] not in _IN_OT_SHIFTS:
            return 0.0
        t_in = _parse_time_str(row["time_in"])
        if t_in is None:
            return 0.0
        minutes = _minutes_between(t_in, _OT_NINE_AM)
        return _round_to_quarter_hour(minutes) if minutes > 0 else 0.0

    def out_ot_hours(row) -> float:
        if row["_effective_shift"] not in _OUT_OT_SHIFTS:
            return 0.0
        t_out = _parse_time_str(row["time_out"])
        if t_out is None:
            return 0.0
        if t_out >= _OT_FIVE_THIRTY_PM:
            minutes = _minutes_between(_OT_FIVE_THIRTY_PM, t_out)
        elif t_out < _OT_SIX_AM:
            minutes = _minutes_between(_OT_FIVE_THIRTY_PM, t_out) + 24 * 60
        else:
            minutes = 0.0
        return _round_to_quarter_hour(minutes) if minutes > 0 else 0.0

    present["work_hours"] = present.apply(work_hours_span, axis=1)
    present["in_ot_hours"] = present.apply(in_ot_hours, axis=1)
    present["out_ot_hours"] = present.apply(out_ot_hours, axis=1)

    def total_ot(row) -> float:
        if row["_effective_shift"] == "Full-OT":
            return row["work_hours"]
        if row["department"] == "HOUSE KEEPING":
            return round(max(row["work_hours"] - 8.5, 0.0), 2)
        return row["in_ot_hours"] + row["out_ot_hours"]

    present["total_ot_hours"] = present.apply(total_ot, axis=1)
    present["full_day_ot"] = (present["_effective_shift"] == "Full-OT").astype(int)
    present["effective_shift"] = present["_effective_shift"]
    full_ot_mask = present["_effective_shift"] == "Full-OT"
    present["el_day_credit"] = 0.0
    present.loc[full_ot_mask & (present["work_hours"] > _EL_FULL_DAY_HOURS), "el_day_credit"] = 1.0
    present.loc[full_ot_mask & (present["work_hours"] <= _EL_FULL_DAY_HOURS), "el_day_credit"] = 0.5

    present = present.sort_values(["date", "department", "emp_code"]).reset_index(drop=True)
    return present[columns]
