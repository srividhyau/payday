import calendar as py_calendar
import html
import json
import logging
import urllib.error
import urllib.request
import uuid
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from src import metrics, payroll

from .forms import UploadForm
from .importer import import_file
from .models import (
    AttendanceRecord, Department, Employee, MonthLock, SalaryAdjustment, SpecialDay, UploadBatch,
)

logger = logging.getLogger(__name__)


def _error(request, text: str) -> None:
    """messages.error() plus a matching log line — a user-facing error
    should always leave a trace in the log, not just a one-time flash
    message that's gone once the page reloads. Use this instead of
    messages.error() directly unless the call site already logs its own
    more detailed line (e.g. logger.exception with a traceback)."""
    messages.error(request, text)
    logger.warning("%s [%s, user=%s]", text, request.path, request.user)

# Mon..Sun abbreviations for the grid's day-of-week header — Thursday and
# Sunday get two letters (TH/SU) instead of just T/S, so they aren't
# ambiguous with Tuesday and Saturday.
_DOW_LABELS = {0: "M", 1: "T", 2: "W", 3: "TH", 4: "F", 5: "S", 6: "SU"}


def _parse_month_date(date_param: str | None, default: date_cls) -> date_cls:
    """Parses the "date" query param used to pick a target month across
    the dashboard and OT Details report — falls back to `default` for a
    missing or malformed value instead of letting a bad bookmarked/shared
    link 500."""
    if not date_param:
        return default
    try:
        return date_cls.fromisoformat(date_param)
    except ValueError:
        return default


@login_required
def home_view(request):
    """Landing page — the app's root URL. Just a branded splash with links
    into the three real pages (Upload, Attendance, Holiday Calendar)."""
    return render(request, "attendance/home.html")


@login_required
def upload_view(request):
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = request.FILES["file"]
            try:
                batch = import_file(uploaded, file_name=uploaded.name)
                messages.success(
                    request,
                    f"Imported {batch.row_count} rows from {batch.period_start} to "
                    f"{batch.period_end}.",
                )
                logger.info(
                    "Upload imported: %s rows, %s to %s, file=%s",
                    batch.row_count, batch.period_start, batch.period_end, uploaded.name,
                )
            except Exception as exc:  # noqa: BLE001 - surface any parse/import error to HR
                messages.error(request, f"Import failed: {exc}")
                logger.exception("Upload import failed for file=%s", uploaded.name)
            return redirect("upload")
    else:
        form = UploadForm()

    recent_batches = UploadBatch.objects.all()[:10]
    return render(request, "attendance/upload.html", {"form": form, "batches": recent_batches})


def _load_daily_data() -> pd.DataFrame:
    """Loads all attendance records into a DataFrame shaped for src/metrics.py."""
    rows = AttendanceRecord.objects.select_related("employee", "employee__department").values(
        "employee__code", "employee__name", "employee__department__name", "employee__designation",
        "employee__category", "employee__subcategory", "employee__company",
        "date", "shift", "time_in", "time_out", "work_hours", "ot_hours", "status",
    )
    df = pd.DataFrame.from_records(rows)
    if df.empty:
        return df
    df = df.rename(columns={
        "employee__code": "emp_code", "employee__name": "emp_name",
        "employee__department__name": "department", "employee__designation": "designation",
        "employee__category": "category", "employee__subcategory": "subcategory",
        "employee__company": "company",
    })
    df["department"] = df["department"].fillna("Unassigned")
    df["date"] = pd.to_datetime(df["date"])
    return df


def _build_month_grid(daily: pd.DataFrame, special_days: dict, emp_rate_map: dict, ot_tooltip: bool = False) -> dict:
    """Builds the day x employee grid (day_headers + table_rows, matching
    the Month_Attendance pivot layout) shared by the dashboard's Month
    Attendance grid and the OT Details report's Full Monthly tab. Also
    returns a few intermediate values (working_days, issues,
    shift_ot_table) that dashboard_view still needs for its own KPI/
    department cards, so it doesn't have to recompute them."""
    working_days = metrics.infer_working_days(daily)

    # Shift-based OT (M-OT/E-OT/ME-OT/Full-OT), same calculation as the OT
    # Details report — drives every OT figure here except the day-cell's
    # bold-red OT text/background, which stays tied to special-day-worked
    # hours.
    shift_ot_table = metrics.overtime_view(daily)
    shift_ot_map = (
        {(r.emp_code, r.date): r.total_ot_hours for r in shift_ot_table.itertuples(index=False)}
        if not shift_ot_table.empty else {}
    )
    emp_ot_totals = (
        shift_ot_table.groupby("emp_code")["total_ot_hours"].sum().to_dict()
        if not shift_ot_table.empty else {}
    )

    first_date = pd.Timestamp(daily["date"].iloc[0])
    _, days_in_month = py_calendar.monthrange(first_date.year, first_date.month)
    dates = [
        pd.Timestamp(year=first_date.year, month=first_date.month, day=d)
        for d in range(1, days_in_month + 1)
    ]
    month_view, day_labels = metrics.month_attendance_view(daily, working_days, dates=dates)
    time_labels = metrics.punch_time_labels(
        daily, shift_ot_map, ot_rate_map=emp_rate_map if ot_tooltip else None
    )
    issues = metrics.punch_issues(daily)
    special_status_map = {(r.emp_code, r.date): r.status for r in daily.itertuples(index=False)}
    ot_map = {(r.emp_code, r.date): r.ot_hours for r in daily.itertuples(index=False)}
    punch_map = {
        (r.emp_code, r.date): (r.shift, r.time_in, r.time_out) for r in daily.itertuples(index=False)
    }
    staff_map = {
        (r.emp_code, r.date): r.subcategory == "Staff" for r in daily.itertuples(index=False)
    }
    special_worked_map = {
        (r.emp_code, r.date): r.special_worked for r in daily.itertuples(index=False)
    }
    dept_map = {
        (r.emp_code, r.date): r.department for r in daily.itertuples(index=False)
    }
    special_day_codes = {SpecialDay.HOLIDAY, SpecialDay.PAID_HOLIDAY, SpecialDay.COMP_OFF}
    day_headers = [
        {
            "label": d,
            "dow": _DOW_LABELS[pd.Timestamp(date).dayofweek],
            "special": special_days.get(pd.Timestamp(date).date(), ""),
            "date_iso": pd.Timestamp(date).strftime("%Y-%m-%d"),
        }
        for d, date in zip(day_labels, dates)
    ]
    summary_cols = ["Work Days", "Comp Off", "Time Off", "Paid Holiday", "Personal Leave"]

    table_rows = []
    for _, row in month_view.iterrows():
        is_dept = row["Row Labels"].startswith("▸")
        day_cells = []
        for d, date in zip(day_labels, dates):
            key = (row["Emp Code"], date)
            emp_status = special_status_map.get(key)
            # Holiday/Paid Holiday/Comp Off cells always keep the special
            # background, whether or not the employee worked that day — an
            # hours-based heat-map color doesn't apply on OT days, so
            # special_worked only drives the bold-red OT text (see "ot"
            # below), not the background.
            is_special_cell = not is_dept and emp_status in special_day_codes
            shift, time_in, time_out = punch_map.get(key, ("", "", ""))
            time_in = metrics.clean_punch_time(time_in)
            time_out = metrics.clean_punch_time(time_out)
            # Gated on "not already a confirmed OT shift" rather than
            # shift == "GS" specifically — a blank/missing shift value
            # (some rows never got a code at all) is just as eligible for
            # a suggestion as an explicit "GS", and excluding it here was
            # silently hiding real OT-eligible days that had no shift
            # code of any kind.
            suggested_shift = (
                metrics.suggest_shift_code(
                    time_in, time_out, special_worked_map.get(key, False),
                    dept_map.get(key, ""), row[d],
                )
                if not is_dept and shift not in metrics.OT_SHIFT_CODES and not staff_map.get(key, False) else ""
            )
            day_cells.append({
                "value": "" if pd.isna(row[d]) else f"{row[d]:.1f}",
                "band": metrics.hours_heat_band(row[d]),
                "title": "" if is_dept else time_labels.get(key, ""),
                "issue": not is_dept and key in issues,
                "special": emp_status if is_special_cell else "",
                "leave": not is_dept and emp_status in ("A", "PL"),
                "ot": not is_dept and ot_map.get(key, 0) > 0,
                "emp_code": "" if is_dept else row["Emp Code"],
                "date_iso": "" if is_dept else pd.Timestamp(date).strftime("%Y-%m-%d"),
                "shift": "" if is_dept else shift,
                "time_in": "" if is_dept else time_in,
                "time_out": "" if is_dept else time_out,
                "is_staff": not is_dept and staff_map.get(key, False),
                "shift_flag": shift if (not is_dept and shift in metrics.OT_SHIFT_CODES) else "",
                "shift_ot_hours": (
                    "" if is_dept or not shift_ot_map.get(key)
                    else f"{shift_ot_map[key]:.2f}".rstrip("0").rstrip(".")
                ),
                "suggested_shift": suggested_shift,
                "gs_show_time": (
                    not is_dept and shift not in metrics.OT_SHIFT_CODES and bool(time_in) and bool(time_out)
                    and not staff_map.get(key, False)
                    and (bool(suggested_shift) or dept_map.get(key, "") == "HOUSE KEEPING")
                ),
            })
        summary_cells = [row[c] for c in summary_cols]
        total_ot = emp_ot_totals.get(row["Emp Code"], 0) if not is_dept else 0
        ot_rate = float(emp_rate_map.get(row["Emp Code"], 0)) if not is_dept else 0
        total_ot_amount = float(total_ot) * ot_rate if not is_dept else 0
        table_rows.append({
            "label": row["Row Labels"],
            "is_dept": is_dept,
            "day_cells": day_cells,
            "summary_cells": summary_cells,
            "total_ot": "" if is_dept or not total_ot else round(float(total_ot), 2),
            "ot_rate": "" if is_dept or not ot_rate else round(ot_rate, 2),
            "total_ot_amount": "" if is_dept or not total_ot_amount else round(total_ot_amount, 2),
        })

    return {
        "working_days": working_days,
        "shift_ot_table": shift_ot_table,
        "emp_ot_totals": emp_ot_totals,
        "issues": issues,
        "day_labels": day_labels,
        "day_headers": day_headers,
        "summary_cols": summary_cols,
        "table_rows": table_rows,
        "total_cols": 1 + len(day_labels) + len(summary_cols) + 3,
    }


def _ot_only_rows(table_rows: list) -> list:
    """Keeps only employee rows with actual OT that month, and drops any
    department header left with no employees under it afterward — used by
    the OT Details report's Full Monthly View tab, which should only ever
    list people who had OT, not the whole roster with everything but OT
    cells blanked out."""
    filtered = []
    pending_dept = None
    pending_emps: list = []

    def flush():
        if pending_dept is not None and pending_emps:
            filtered.append(pending_dept)
            filtered.extend(pending_emps)

    for row in table_rows:
        if row["is_dept"]:
            flush()
            pending_dept = row
            pending_emps = []
        elif row.get("total_ot"):
            pending_emps.append(row)
    flush()
    return filtered


def _restrict_to_ot_cells(table_rows: list) -> list:
    """Empties every day cell that didn't actually earn real OT credit —
    used alongside _ot_only_rows by the OT Details report's Full Monthly
    View, so it only ever shows numbers that match overtime_view()'s real
    per-day totals, not the dashboard's "this looks like it should have
    had a shift code" suggestion nudge (which is a workflow aid for fixing
    data, not an OT amount — a suggested day with no shift code actually
    set carries zero real credit, same as an assigned shift that ended up
    earning nothing, e.g. clocked out just before the 5:30pm OT cutoff).
    cell["shift_ot_hours"] already holds that real per-day credit (from
    shift_ot_map, sourced from overtime_view()), so it's the single source
    of truth for whether a cell counts here.

    Mutates and returns table_rows in place; the dashboard's own grid is
    unaffected since it builds its own separate copy per request."""
    for row in table_rows:
        if row["is_dept"]:
            continue
        for cell in row["day_cells"]:
            has_credit = bool(cell["shift_ot_hours"])
            if not has_credit:
                cell["value"] = ""
                cell["shift_flag"] = ""
                cell["gs_show_time"] = False
            else:
                cell["gs_show_time"] = not cell["shift_flag"]
            cell["suggested_shift"] = ""
    return table_rows


@login_required
def dashboard_view(request):
    """Server-rendered Month Attendance dashboard: KPIs, department
    breakdown, holiday calendar, and the day x employee hours grid.

    Scoped to one calendar month at a time (with prev/next nav, like
    ot_view/calendar_view) — otherwise the grid mixes every date ever
    uploaded into one non-continuous table. Nav is driven by a single
    "date" query param (any day within the target month)."""
    daily_all = _load_daily_data()
    month_keys_all = (
        sorted({(ts.year, ts.month) for ts in daily_all["date"]}) if not daily_all.empty else []
    )
    if not month_keys_all:
        return render(request, "attendance/dashboard.html", {"empty": True, "has_data": False})

    default_date = date_cls(*month_keys_all[-1], 1)
    date_param = request.GET.get("date")
    current = _parse_month_date(date_param, default_date)
    year, month = current.year, current.month

    prev_date = (current.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_date = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    current_view = request.GET.get("view", "all")
    if current_view not in ("all", "issues", "ot"):
        current_view = "all"
    locked_views = set(
        MonthLock.objects.filter(year=year, month=month).values_list("view", flat=True)
    )
    month_nav = {
        "has_data": True,
        "year": year,
        "month": month,
        "month_name": py_calendar.month_name[month],
        "prev_date": prev_date.isoformat(),
        "next_date": next_date.isoformat(),
        "current_date": current.isoformat(),
        "current_view": current_view,
        "current_view_label": dict(MonthLock.VIEW_CHOICES)[current_view],
        "is_locked": current_view in locked_views,
        "lock_status": {v: (v in locked_views) for v, _ in MonthLock.VIEW_CHOICES},
    }

    daily = daily_all[(daily_all["date"].dt.year == year) & (daily_all["date"].dt.month == month)]
    if daily.empty:
        return render(request, "attendance/dashboard.html", {"empty": True, **month_nav})

    special_day_list = list(SpecialDay.objects.all())
    special_days = {sd.date: sd.day_type for sd in special_day_list}
    daily = metrics.apply_special_days(daily, special_days)

    holidays = metrics.holiday_dates(daily)
    paid_holidays = metrics.paid_holiday_dates(daily)
    comp_offs = metrics.comp_off_dates(daily)

    emp_rate_map = dict(Employee.objects.values_list("code", "ot_rate_per_hour"))
    grid = _build_month_grid(daily, special_days, emp_rate_map, ot_tooltip=(current_view == "ot"))
    working_days = grid["working_days"]
    shift_ot_table = grid["shift_ot_table"]
    issues = grid["issues"]

    emp_summary = metrics.employee_summary(daily, working_days, shift_ot_map=grid["emp_ot_totals"])
    dept_summary = metrics.department_summary(emp_summary)
    kpi = metrics.kpis(emp_summary)

    extremes = metrics.department_attendance_extremes(emp_summary, n=2)
    high_leave = metrics.department_high_leave(emp_summary, threshold=4)
    dept_summary_rows = dept_summary.to_dict("records")
    for d in dept_summary_rows:
        d.update(extremes.get(d["department"], {"top": [], "worst": []}))
        d["high_leave"] = high_leave.get(d["department"], [])

    dept_missed_punch = metrics.department_missed_punch_summary(daily, issues)
    dept_ot_summary = metrics.department_ot_summary(shift_ot_table)

    context = {
        **month_nav,
        "empty": False,
        "period_start": daily["date"].min(),
        "period_end": daily["date"].max(),
        "working_days": working_days,
        "holidays": holidays,
        "paid_holidays": paid_holidays,
        "comp_offs": comp_offs,
        "kpi": kpi,
        "missed_punch_count": len(issues),
        "dept_summary": dept_summary_rows,
        "dept_missed_punch": dept_missed_punch,
        "dept_ot_summary": dept_ot_summary,
        "day_labels": grid["day_labels"],
        "day_headers": grid["day_headers"],
        "summary_cols": grid["summary_cols"],
        "table_rows": grid["table_rows"],
        "heat_colors": metrics.HEAT_COLORS,
        "total_cols": grid["total_cols"],
        "day_types": SpecialDay.TYPE_CHOICES,
    }
    return render(request, "attendance/dashboard.html", context)


def _build_month_weeks(year, month, special_map, today):
    """Monday-start week grid for one month, each day annotated with its
    SpecialDay type (if any). Shared by calendar_view (editable) and
    dashboard_view (read-only preview of the period being viewed)."""
    cal = py_calendar.Calendar(firstweekday=0)
    month_weeks = cal.monthdatescalendar(year, month)
    return [
        [
            {
                "date": d,
                "in_month": d.month == month,
                "is_today": d == today,
                "day_type": special_map[d].day_type if d in special_map else "",
                "name": special_map[d].name if d in special_map else "",
            }
            for d in week
        ]
        for week in month_weeks
    ]


@login_required
def calendar_view(request):
    """Company-wide Holiday / Paid Holiday / Comp Off calendar. Click a day
    to set/clear its type — each change is its own POST + redirect back to
    the same month, so no JS beyond auto-submitting the select is needed."""
    if request.method == "POST":
        day_str = request.POST.get("date")
        day_type = request.POST.get("day_type", "")
        if day_str:
            if day_type in dict(SpecialDay.TYPE_CHOICES):
                SpecialDay.objects.update_or_create(date=day_str, defaults={"day_type": day_type})
            else:
                SpecialDay.objects.filter(date=day_str).delete()
        return redirect(
            f"{request.path}?year={request.POST.get('year')}&month={request.POST.get('month')}"
        )

    today = date_cls.today()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    cal = py_calendar.Calendar(firstweekday=0)  # Monday-start weeks
    month_weeks = cal.monthdatescalendar(year, month)
    visible_dates = [d for week in month_weeks for d in week]
    special_map = {sd.date: sd for sd in SpecialDay.objects.filter(date__in=visible_dates)}

    weeks = _build_month_weeks(year, month, special_map, today)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

    context = {
        "year": year,
        "month": month,
        "month_name": py_calendar.month_name[month],
        "weeks": weeks,
        "day_types": SpecialDay.TYPE_CHOICES,
        "prev_year": prev_year, "prev_month": prev_month,
        "next_year": next_year, "next_month": next_month,
        "upcoming": SpecialDay.objects.filter(date__gte=today).order_by("date")[:20],
    }
    return render(request, "attendance/calendar.html", context)


def _month_is_locked(date_str: str, view: str) -> bool:
    try:
        d = date_cls.fromisoformat(date_str)
    except ValueError:
        return False
    return MonthLock.objects.filter(year=d.year, month=d.month, view=view).exists()


@login_required
def edit_record_view(request):
    """HR correction for a single employee/date cell — fixes a missed punch
    (time_in/time_out) and/or sets the shift code (GS/M-OT/E-OT/ME-OT/
    Full-OT) so it feeds correctly into the OT view. Triggered by the
    dashboard grid's click-to-edit popup; redirects back to wherever the
    popup was opened from."""
    next_url = request.POST.get("next") or "dashboard"
    if request.method != "POST":
        return redirect(next_url)

    emp_code = request.POST.get("emp_code", "").strip()
    date_str = request.POST.get("date", "").strip()
    time_in = request.POST.get("time_in", "").strip()
    time_out = request.POST.get("time_out", "").strip()
    shift = request.POST.get("shift", "").strip()
    view = request.POST.get("view", "all")

    if _month_is_locked(date_str, view):
        _error(request, "This view is locked for this month — unlock it first to make changes.")
        return redirect(next_url)

    try:
        record = AttendanceRecord.objects.get(employee__code=emp_code, date=date_str)
    except AttendanceRecord.DoesNotExist:
        _error(request, f"No attendance record for {emp_code} on {date_str}.")
        return redirect(next_url)

    record.time_in = time_in
    record.time_out = time_out
    # Staff are excluded from shift-based OT entirely (see overtime_view) —
    # their shift is never editable, regardless of what the form submits,
    # so a disabled/bypassed field can't silently change or clear it.
    if record.employee.subcategory != "Staff":
        record.shift = shift
    record.work_hours, record.status = metrics.recompute_from_punch(time_in, time_out)
    record.save()
    messages.success(request, f"Updated {emp_code} on {date_str}.")
    logger.info(
        "Record edited: emp=%s date=%s time_in=%s time_out=%s shift=%s by user=%s",
        emp_code, date_str, time_in, time_out, shift, request.user,
    )
    return redirect(next_url)


@login_required
def bulk_set_shift_view(request):
    """Sets the shift code for every employee on one date at once, and/or
    the company-wide calendar day type (Holiday/Paid Holiday/Comp Off) for
    that same date — one popup, triggered by clicking a day column header
    in the dashboard grid, instead of separate shift and calendar-editing
    flows. Staff subcategory employees are excluded from the shift update,
    matching overtime_view's own exclusion (shift-based OT never applies
    to them); the calendar day type still applies company-wide."""
    next_url = request.POST.get("next") or "dashboard"
    if request.method != "POST":
        return redirect(next_url)

    date_str = request.POST.get("date", "").strip()
    shift = request.POST.get("shift", "").strip()
    day_type = request.POST.get("day_type", "")
    view = request.POST.get("view", "all")
    if not date_str:
        _error(request, "Missing date.")
        return redirect(next_url)

    if _month_is_locked(date_str, view):
        _error(request, "This view is locked for this month — unlock it first to make changes.")
        return redirect(next_url)

    updated = (
        AttendanceRecord.objects.filter(date=date_str)
        .exclude(employee__subcategory="Staff")
        .update(shift=shift)
    )
    if day_type in dict(SpecialDay.TYPE_CHOICES):
        SpecialDay.objects.update_or_create(date=date_str, defaults={"day_type": day_type})
    else:
        SpecialDay.objects.filter(date=date_str).delete()

    messages.success(
        request,
        f"Set shift '{shift}' for {updated} employee(s) and calendar type '{day_type or '—'}' on {date_str}.",
    )
    logger.info(
        "Bulk shift set: date=%s shift=%s day_type=%s updated=%s by user=%s",
        date_str, shift, day_type or "-", updated, request.user,
    )
    return redirect(next_url)


@login_required
def toggle_month_lock_view(request):
    """Locks or unlocks one calendar month's attendance data for one of
    the three dashboard views (All/Missed Punch/OT View) independently,
    gated by the shared PIN (settings.ATTENDANCE_LOCK_PIN) — there's no
    user login in this app, so the PIN is the only check on either
    direction. Triggered by the dashboard's lock-toggle button next to the
    view tabs."""
    next_url = request.POST.get("next") or "dashboard"
    if request.method != "POST":
        return redirect(next_url)

    try:
        year = int(request.POST.get("year", ""))
        month = int(request.POST.get("month", ""))
    except ValueError:
        _error(request, "Missing month.")
        return redirect(next_url)

    view = request.POST.get("view", "all")
    if view not in dict(MonthLock.VIEW_CHOICES):
        view = MonthLock.VIEW_ALL

    pin = request.POST.get("pin", "").strip()
    action = request.POST.get("action", "")
    if pin != settings.ATTENDANCE_LOCK_PIN:
        messages.error(request, "Incorrect PIN.")
        logger.warning(
            "Incorrect lock PIN attempt: view=%s %s-%s by user=%s", view, year, month, request.user,
        )
        return redirect(next_url)

    view_label = dict(MonthLock.VIEW_CHOICES)[view]
    if action == "lock":
        MonthLock.objects.get_or_create(year=year, month=month, view=view)
        messages.success(request, f"{view_label} is now locked for {py_calendar.month_name[month]} {year}.")
    elif action == "unlock":
        MonthLock.objects.filter(year=year, month=month, view=view).delete()
        messages.success(request, f"{view_label} is now unlocked for {py_calendar.month_name[month]} {year}.")
    logger.info(
        "Month lock %s: view=%s %s-%s by user=%s", action, view, year, month, request.user,
    )
    return redirect(next_url)


def _notify_telegram(text: str, parse_mode: str | None = None) -> tuple[bool, str]:
    """Posts a text message to the configured bot/chat/topic (see
    settings.TELEGRAM_*). Never raises — returns (ok, error) so a caller
    that needs to know (the Day view's "Send Report" button) can surface a
    failure, while a best-effort caller (the bulk save notification) can
    just ignore the result."""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False, "Telegram isn't configured (missing bot token or chat id)."
    payload = {"chat_id": chat_id, "text": text}
    if settings.TELEGRAM_TOPIC_ID:
        payload["message_thread_id"] = settings.TELEGRAM_TOPIC_ID
    if parse_mode:
        payload["parse_mode"] = parse_mode
    request_obj = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request_obj, timeout=5)
        logger.info("Telegram message sent.")
        return True, ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        logger.warning("Telegram notification failed: %s", detail)
        return False, detail
    except urllib.error.URLError as exc:
        logger.warning("Telegram notification failed: %s", exc)
        return False, str(exc.reason)
    except Exception as exc:  # noqa: BLE001 - a timeout/SSL/connection error
        # isn't a URLError/HTTPError in every case (e.g. a read timeout can
        # raise a bare TimeoutError) — catch-all so a send failure is always
        # logged and reported back, never silently swallowed.
        logger.exception("Telegram notification failed unexpectedly: %s", exc)
        return False, str(exc)


def _telegram_send_photo(photo_bytes: bytes, filename: str, caption: str = "") -> tuple[bool, str]:
    """Posts an image to the configured Telegram bot/chat/topic via
    sendPhoto. Unlike _notify_telegram, this one reports back (ok, error)
    instead of swallowing failures — it's triggered by an explicit "Send
    Report" click, so the user needs to know if it didn't go through."""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False, "Telegram isn't configured (missing bot token or chat id)."

    fields = {"chat_id": chat_id, "caption": caption[:1024]}
    if settings.TELEGRAM_TOPIC_ID:
        fields["message_thread_id"] = settings.TELEGRAM_TOPIC_ID

    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        if not value:
            continue
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode("utf-8")
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8")
    body += photo_bytes
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    request_obj = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        urllib.request.urlopen(request_obj, timeout=20)
        logger.info("Telegram photo sent.")
        return True, ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        logger.warning("Telegram sendPhoto failed: %s", detail)
        return False, detail
    except urllib.error.URLError as exc:
        logger.warning("Telegram sendPhoto failed: %s", exc)
        return False, str(exc.reason)
    except Exception as exc:  # noqa: BLE001 - a timeout/SSL/connection error
        # isn't a URLError/HTTPError in every case (e.g. a read timeout can
        # raise a bare TimeoutError), and a 20MB+ photo makes that more
        # likely than for the plain-text sendMessage above — catch-all so a
        # send failure is always logged and reported back, never silent.
        logger.exception("Telegram sendPhoto failed unexpectedly: %s", exc)
        return False, str(exc)


def _pick_department(request, departments):
    """Shared default-department resolution for the Mark Attendance flow —
    the explicit request param wins, else "Operators" (this flow's main
    use case), else whatever department happens to exist first."""
    dept_id = request.GET.get("department") or request.POST.get("department")
    if dept_id:
        dept = Department.objects.filter(id=dept_id).first()
        if dept:
            return dept
    return Department.objects.filter(name__iexact="Operators").first() or (departments[0] if departments else None)


def _build_simple_month_grid(dept, year: int, month: int) -> dict:
    """Builds the employee x day-of-month grid (day_headers, table_rows,
    day_totals, total_present) behind Mark Attendance's Month view table —
    and, rendered off-screen, behind the Day view's "Send Report" button
    too, so it can screenshot a month table without navigating away. Plain
    DB reads keyed by (employee, day-of-month); unrelated to the
    dashboard's pandas-based _build_month_grid, which draws from a
    different data source (_load_daily_data)."""
    _, days_in_month = py_calendar.monthrange(year, month)
    day_headers = [
        {
            "day": d,
            "dow": _DOW_LABELS[date_cls(year, month, d).weekday()],
            "date_iso": date_cls(year, month, d).isoformat(),
        }
        for d in range(1, days_in_month + 1)
    ]

    employees = (
        Employee.objects.filter(department=dept)
        .active_during(date_cls(year, month, 1), date_cls(year, month, days_in_month))
        .order_by("name")
        if dept else Employee.objects.none()
    )
    status_map = {
        (r["employee_id"], r["date"].day): r["status"]
        for r in AttendanceRecord.objects.filter(
            employee__in=employees, date__year=year, date__month=month
        ).values("employee_id", "date", "status")
    }
    total_days = len(day_headers)
    day_totals = [0] * total_days
    table_rows = []
    for emp in employees:
        cells = [
            {
                "day": d["day"],
                "date_iso": d["date_iso"],
                "status": status_map.get((emp.id, d["day"]), ""),
            }
            for d in day_headers
        ]
        present_count = 0
        absent_count = 0
        for i, c in enumerate(cells):
            if c["status"] == "P":
                present_count += 1
                day_totals[i] += 1
            elif not c["status"]:
                absent_count += 1
        table_rows.append({
            "employee": emp,
            "cells": cells,
            "present_count": present_count,
            "total_days": total_days,
            "percent_absent": round(absent_count / total_days * 100) if total_days else 0,
        })

    return {
        "day_headers": day_headers,
        "table_rows": table_rows,
        "day_totals": day_totals,
        "total_present": sum(day_totals),
    }


@login_required
def mark_attendance_view(request):
    """Daily attendance marking for one department at a time (Operators by
    default) — a fast, mobile-friendly alternative to waiting on the eSSL
    fingerprint export, for departments where punches aren't captured that
    way. One page load = one department + one date; marking is a single
    bulk POST that upserts an AttendanceRecord per employee, same
    upsert-by-(employee, date) semantics as the fingerprint importer."""
    date_param = request.GET.get("date") or request.POST.get("date")
    try:
        target_date = date_cls.fromisoformat(date_param) if date_param else date_cls.today()
    except ValueError:
        target_date = date_cls.today()

    departments = list(Department.objects.all())
    dept = _pick_department(request, departments)

    redirect_url = f"{request.path}?date={target_date.isoformat()}" + (f"&department={dept.id}" if dept else "")

    if request.method == "POST":
        if dept is None:
            _error(request, "No department selected.")
            return redirect(redirect_url)
        if _month_is_locked(target_date.isoformat(), MonthLock.VIEW_ALL):
            _error(request, "This month is locked — unlock it on the dashboard first.")
            return redirect(redirect_url)

        employees = Employee.objects.filter(department=dept).active_on(target_date)
        valid_status = dict(AttendanceRecord.STATUS_CHOICES)
        saved = 0
        present_count = 0
        absent_count = 0
        for emp in employees:
            # The Present/Absent toggle (radio, name=status_<id>) and the
            # "More…" dropdown (select, name=status_more_<id>) are separate
            # fields — only one is ever meaningful at a time (the template's
            # JS keeps them mutually exclusive), so an unchecked radio group
            # falls back to whatever the select holds.
            status = request.POST.get(f"status_{emp.id}", "").strip()
            if not status:
                status = request.POST.get(f"status_more_{emp.id}", "").strip()
            if status not in valid_status:
                continue
            shift = request.POST.get(f"shift_{emp.id}", "").strip()
            AttendanceRecord.objects.update_or_create(
                employee=emp, date=target_date,
                defaults={"status": status, "shift": shift},
            )
            saved += 1
            if status == "P":
                present_count += 1
            elif status == "A":
                absent_count += 1
        messages.success(
            request, f"Marked attendance for {saved} employee(s) in {dept.name} on {target_date:%d %b %Y}."
        )
        logger.info(
            "Day attendance saved: dept=%s date=%s saved=%s present=%s absent=%s by user=%s",
            dept.name, target_date, saved, present_count, absent_count, request.user,
        )
        if saved:
            other_count = saved - present_count - absent_count
            breakdown = f"{present_count} Present, {absent_count} Absent"
            if other_count:
                breakdown += f", {other_count} Other"
            _notify_telegram(
                f"🗓 Attendance marked — {dept.name} — {target_date:%d %b %Y}\n{saved} employee(s): {breakdown}"
            )
        return redirect(redirect_url)

    employees = (
        Employee.objects.filter(department=dept).active_on(target_date).order_by("name")
        if dept else Employee.objects.none()
    )
    existing = {
        r.employee_id: r
        for r in AttendanceRecord.objects.filter(date=target_date, employee__in=employees)
    }
    rows = [
        {
            "employee": emp,
            "status": existing[emp.id].status if emp.id in existing else "A",
            "shift": existing[emp.id].shift if emp.id in existing else "",
        }
        for emp in employees
    ]

    # "Send Report" sends the day's attendance as text plus a screenshot of
    # the whole month — the month grid below is rendered off-screen purely
    # so that screenshot can be captured without navigating to Month view.
    month_grid = _build_simple_month_grid(dept, target_date.year, target_date.month) if dept else None

    context = {
        "target_date": target_date,
        "prev_date": (target_date - timedelta(days=1)).isoformat(),
        "next_date": (target_date + timedelta(days=1)).isoformat(),
        "departments": departments,
        "dept": dept,
        "rows": rows,
        "status_choices": AttendanceRecord.STATUS_CHOICES,
        "is_locked": _month_is_locked(target_date.isoformat(), MonthLock.VIEW_ALL),
        "month_name": py_calendar.month_name[target_date.month],
        "month_day_headers": month_grid["day_headers"] if month_grid else [],
        "month_table_rows": month_grid["table_rows"] if month_grid else [],
        "month_day_totals": month_grid["day_totals"] if month_grid else [],
        "month_total_present": month_grid["total_present"] if month_grid else 0,
    }
    return render(request, "attendance/mark_attendance.html", context)


@login_required
def log_client_error_view(request):
    """Client-side failures (e.g. html2canvas throwing before any Telegram
    request is even made — see the "Send Report" buttons) are invisible to
    the server by definition, so there's nothing here for the normal error
    logging to catch. This just gives the browser a way to hand that
    failure to the server log instead of it vanishing in the console."""
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    message = request.POST.get("message", "")[:2000]
    context = request.POST.get("context", "")[:200]
    logger.warning("Client-side error [%s, user=%s]: %s", context or "unknown", request.user, message)
    return JsonResponse({"ok": True})


@login_required
def send_day_attendance_report_view(request):
    """Sends a simple text attendance table (name — status, one per line)
    for one department/date to Telegram — the Day view's "Send Report"
    button. Plain text rather than a screenshot: the emp-list is just a
    column of radio toggles, which doesn't capture well as an image, so
    this rebuilds the same data as a Telegram-native monospace table."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)

    dept = Department.objects.filter(id=request.POST.get("department")).first()
    if not dept:
        return JsonResponse({"ok": False, "error": "Invalid department."}, status=400)
    try:
        target_date = date_cls.fromisoformat(request.POST.get("date", ""))
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid date."}, status=400)

    employees = Employee.objects.filter(department=dept).active_on(target_date).order_by("name")
    if not employees:
        return JsonResponse({"ok": False, "error": "No employees in this department."}, status=400)

    status_map = {
        r.employee_id: r.status
        for r in AttendanceRecord.objects.filter(date=target_date, employee__in=employees)
    }
    status_labels = dict(AttendanceRecord.STATUS_CHOICES)
    rows = [(emp.name, status_labels.get(status_map.get(emp.id, "A"), "Absent")) for emp in employees]

    name_width = max(len(name) for name, _ in rows)
    table_text = "\n".join(f"{name.ljust(name_width)}  {status}" for name, status in rows)
    present = sum(1 for _, status in rows if status == "Present")
    absent = sum(1 for _, status in rows if status == "Absent")

    text = (
        f"<b>🗓 Day Attendance — {html.escape(dept.name)} — {target_date:%d %b %Y}</b>\n"
        f"{present} Present, {absent} Absent\n"
        f"<pre>{html.escape(table_text)}</pre>"
    )
    try:
        ok, error = _notify_telegram(text, parse_mode="HTML")
    except Exception:
        logger.exception("send_day_attendance_report_view failed unexpectedly.")
        return JsonResponse({"ok": False, "error": "Unexpected server error."}, status=500)
    if not ok:
        return JsonResponse({"ok": False, "error": error or "Telegram send failed."}, status=502)
    return JsonResponse({"ok": True})


@login_required
def mark_attendance_month_view(request):
    """Whole-month companion to mark_attendance_view — one department at a
    time, a grid of employee x day-of-month, each cell directly editable
    (click it, pick a status, save) via set_attendance_status_view below.
    Read-heavy by design: one query for the whole month's records rather
    than N+1 per cell."""
    date_param = request.GET.get("date")
    try:
        current = date_cls.fromisoformat(date_param) if date_param else date_cls.today()
    except ValueError:
        current = date_cls.today()
    year, month = current.year, current.month

    departments = list(Department.objects.all())
    dept = _pick_department(request, departments)

    prev_date = (current.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_date = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

    grid = _build_simple_month_grid(dept, year, month)

    context = {
        "current": current,
        "year": year,
        "month": month,
        "month_name": py_calendar.month_name[month],
        "prev_date": prev_date.isoformat(),
        "next_date": next_date.isoformat(),
        "departments": departments,
        "dept": dept,
        "day_headers": grid["day_headers"],
        "table_rows": grid["table_rows"],
        "day_totals": grid["day_totals"],
        "total_present": grid["total_present"],
        "status_choices": AttendanceRecord.STATUS_CHOICES,
        "status_labels": dict(AttendanceRecord.STATUS_CHOICES),
        "is_locked": _month_is_locked(current.isoformat(), MonthLock.VIEW_ALL),
    }
    return render(request, "attendance/mark_attendance_month.html", context)


@login_required
def send_telegram_report_view(request):
    """Receives a screenshot (captured client-side via html2canvas — see
    mark_attendance_month.html's "Send Report" button) and forwards it to
    Telegram as a photo. JSON in, JSON out: the button is a fetch() call,
    not a form submit, so it can re-enable itself and show an error inline
    instead of navigating away."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    image = request.FILES.get("image")
    if not image:
        return JsonResponse({"ok": False, "error": "No image provided."}, status=400)
    caption = request.POST.get("caption", "").strip()
    try:
        ok, error = _telegram_send_photo(image.read(), image.name or "report.png", caption)
    except Exception:
        logger.exception("send_telegram_report_view failed unexpectedly.")
        return JsonResponse({"ok": False, "error": "Unexpected server error."}, status=500)
    if not ok:
        return JsonResponse({"ok": False, "error": error or "Telegram send failed."}, status=502)
    return JsonResponse({"ok": True})


@login_required
def set_attendance_status_view(request):
    """Single-cell save for the month grid — each day cell is its own tiny
    form whose submit button already carries the *target* status (the
    opposite of whatever's currently shown, computed in the template), so
    one click toggles Present <-> Absent with no popup. Same
    upsert-by-(employee, date) as mark_attendance_view's bulk save, just one
    record at a time."""
    next_url = request.POST.get("next") or "mark_attendance_month"
    if request.method != "POST":
        return redirect(next_url)

    date_str = request.POST.get("date", "").strip()
    if _month_is_locked(date_str, MonthLock.VIEW_ALL):
        _error(request, "This month is locked — unlock it on the dashboard first.")
        return redirect(next_url)

    try:
        emp = Employee.objects.get(id=request.POST.get("employee_id", "").strip())
        target_date = date_cls.fromisoformat(date_str)
    except (Employee.DoesNotExist, ValueError):
        _error(request, "Invalid employee or date.")
        return redirect(next_url)

    status = request.POST.get("status", "").strip()
    valid_status = dict(AttendanceRecord.STATUS_CHOICES)
    if status not in valid_status:
        _error(request, "Invalid status.")
        return redirect(next_url)

    AttendanceRecord.objects.update_or_create(
        employee=emp, date=target_date, defaults={"status": status},
    )
    messages.success(request, f"{emp.name}: {valid_status[status]} on {target_date:%d %b}.")
    logger.info(
        "Month-grid cell set: emp=%s date=%s status=%s by user=%s",
        emp.code, target_date, status, request.user,
    )
    return redirect(next_url)


# (tab key, page label, Employee.subcategory value) — Operators aren't
# looped in here since their context key doesn't follow the "{tab_key}_
# rows" pattern (see salary_view), and they're filtered by
# category="Operator" instead of subcategory, unlike the other three.
_SALARY_SUBCATEGORY_TABS = [
    ("company", "Company Workers", "Company"),
    ("helper", "Helpers", "Helper"),
    ("staff", "Staff", "Staff"),
]


def _salary_decimal(request, field: str, emp_id) -> Decimal:
    raw = request.POST.get(f"{field}_{emp_id}", "").strip()
    if not raw:
        return Decimal(0)
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal(0)


@login_required
def salary_view(request):
    """One page, one month at a time, four tabs (Company Workers/Helpers/
    Staff/Operators) — each a bulk-editable table of that month's payroll
    adjustments (Adjust Days/Deductions/Additions, plus Manual Amount for
    Operators), with Gross/PF/ESI/NET computed via src/payroll.py from
    Employee.basic_salary/hra/da and this month's attendance. Save is one
    upsert-by-(employee, year, month) per row, same bulk shape as
    mark_attendance_view's day-view save — one tab's table = one POST."""
    date_param = request.GET.get("date") or request.POST.get("date")
    current = _parse_month_date(date_param, date_cls.today().replace(day=1))
    year, month = current.year, current.month
    prev_date = (current.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_date = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

    if request.method == "POST":
        tab = request.POST.get("tab", "")
        employee_ids = request.POST.getlist("employee_id")
        saved = 0
        for emp_id in employee_ids:
            emp = Employee.objects.filter(id=emp_id).first()
            if not emp:
                continue
            manual_amount_raw = request.POST.get(f"manual_amount_{emp_id}", "").strip()
            manual_amount = None
            if manual_amount_raw:
                try:
                    manual_amount = Decimal(manual_amount_raw)
                except InvalidOperation:
                    manual_amount = None
            SalaryAdjustment.objects.update_or_create(
                employee=emp, year=year, month=month,
                defaults={
                    "adjust_days": _salary_decimal(request, "adjust_days", emp_id),
                    "deductions": _salary_decimal(request, "deductions", emp_id),
                    "additions": _salary_decimal(request, "additions", emp_id),
                    "manual_amount": manual_amount,
                    "hold": request.POST.get(f"hold_{emp_id}") == "on",
                    "notes": request.POST.get(f"notes_{emp_id}", "").strip(),
                },
            )
            saved += 1
        messages.success(request, f"Saved salary adjustments for {saved} employee(s) ({tab}).")
        logger.info(
            "Salary adjustments saved: tab=%s %s-%s saved=%s by user=%s",
            tab, year, month, saved, request.user,
        )
        return redirect(f"{request.path}?date={current.isoformat()}")

    _, days_in_month = py_calendar.monthrange(year, month)
    month_start, month_end = current.replace(day=1), current.replace(day=days_in_month)

    working_days = payroll.working_days_in_month(year, month)
    special_day_counts = {
        r["day_type"]: r["n"]
        for r in SpecialDay.objects.filter(date__year=year, date__month=month)
        .values("day_type").annotate(n=Count("id"))
    }
    holiday_count = special_day_counts.get(SpecialDay.HOLIDAY, 0)
    paid_holiday_count = special_day_counts.get(SpecialDay.PAID_HOLIDAY, 0)
    comp_off_count = special_day_counts.get(SpecialDay.COMP_OFF, 0)

    adjustments = {a.employee_id: a for a in SalaryAdjustment.objects.filter(year=year, month=month)}
    paid_days_map = {
        r["employee_id"]: r["n"]
        for r in AttendanceRecord.objects.filter(
            date__year=year, date__month=month, status__in=["P", "PH", "CO"],
        ).values("employee_id").annotate(n=Count("id"))
    }

    def build_rows(employees, kind):
        rows = []
        for emp in employees:
            adj = adjustments.get(emp.id)
            paid_days = Decimal(paid_days_map.get(emp.id, 0))
            adjust_days = adj.adjust_days if adj else Decimal(0)
            deductions = adj.deductions if adj else Decimal(0)
            additions = adj.additions if adj else Decimal(0)
            manual_amount = adj.manual_amount if adj else None
            if kind == "company":
                calc = payroll.compute_company_worker_pay(
                    emp.basic_salary, emp.hra, emp.da, paid_days, working_days,
                    adjust_days, deductions, additions,
                    pf_enabled=emp.pf_enabled, esi_enabled=emp.esi_enabled,
                )
            elif kind == "operators":
                calc = payroll.compute_operator_pay(manual_amount, deductions, additions)
            else:
                calc = payroll.compute_prorated_pay(
                    emp.basic_salary, paid_days, working_days, adjust_days, deductions, additions,
                )
            rows.append({
                "employee": emp,
                "paid_days": paid_days,
                "adjust_days": adjust_days,
                "deductions": deductions,
                "additions": additions,
                "manual_amount": manual_amount,
                "hold": adj.hold if adj else False,
                "notes": adj.notes if adj else "",
                "calc": calc,
            })
        return rows

    def sum_rows(rows, kind):
        """Column totals for a tab's footer row — sums whatever numeric
        fields that tab's rows actually carry (employee Basic/HRA/DA for
        Company Workers, manual_amount for Operators, every payroll.py
        calc field for all of them), so each tab's template can reference
        totals.<field> the same way it references row.<field>."""
        totals = {
            "paid_days": sum((r["paid_days"] for r in rows), Decimal(0)),
            "adjust_days": sum((r["adjust_days"] for r in rows), Decimal(0)),
            "deductions": sum((r["deductions"] for r in rows), Decimal(0)),
            "additions": sum((r["additions"] for r in rows), Decimal(0)),
        }
        if kind == "company":
            totals["basic_salary"] = sum((r["employee"].basic_salary for r in rows), Decimal(0))
            totals["da"] = sum((r["employee"].da for r in rows), Decimal(0))
            totals["hra"] = sum((r["employee"].hra for r in rows), Decimal(0))
        elif kind in ("helper", "staff"):
            totals["basic_salary"] = sum((r["employee"].basic_salary for r in rows), Decimal(0))
        elif kind == "operators":
            totals["manual_amount"] = sum((r["manual_amount"] or Decimal(0) for r in rows), Decimal(0))
        calc_keys = rows[0]["calc"].keys() if rows else []
        calc_totals = {k: Decimal(0) for k in calc_keys}
        for r in rows:
            for k in calc_keys:
                calc_totals[k] += r["calc"][k]
        totals["calc"] = {k: round(v, 2) for k, v in calc_totals.items()}
        return totals

    context = {
        "current": current,
        "year": year,
        "month": month,
        "month_name": py_calendar.month_name[month],
        "prev_date": prev_date.isoformat(),
        "next_date": next_date.isoformat(),
        "working_days": working_days,
        "holiday_count": holiday_count,
        "paid_holiday_count": paid_holiday_count,
        "comp_off_count": comp_off_count,
    }
    for tab_key, _label, subcategory in _SALARY_SUBCATEGORY_TABS:
        employees = (
            Employee.objects.filter(subcategory__iexact=subcategory)
            .active_during(month_start, month_end).order_by("name")
        )
        rows = build_rows(employees, tab_key)
        context[f"{tab_key}_rows"] = rows
        context[f"{tab_key}_totals"] = sum_rows(rows, tab_key)
    operator_rows = build_rows(
        Employee.objects.filter(category__iexact="Operator").active_during(month_start, month_end).order_by("name"),
        "operators",
    )
    context["operator_rows"] = operator_rows
    context["operator_totals"] = sum_rows(operator_rows, "operators")
    return render(request, "attendance/salary.html", context)


def _ot_details_context(date_param: str | None) -> dict:
    """Computes everything the OT Details report (page and downloads) need
    for one month — the same shift-based OT numbers as the dashboard's OT
    View tab (see overtime_view), as a Monthly Summary grouped by
    department (one row per employee within each department: total OT
    hours/rate/amount, plus a department header row with its subtotal).
    Shared by ot_details_view and the two download views so they can
    never drift apart."""
    daily_all = _load_daily_data()
    month_keys_all = (
        sorted({(ts.year, ts.month) for ts in daily_all["date"]}) if not daily_all.empty else []
    )
    if not month_keys_all:
        return {"has_data": False}

    default_date = date_cls(*month_keys_all[-1], 1)
    current = _parse_month_date(date_param, default_date)
    year, month = current.year, current.month

    prev_date = (current.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_date = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_nav = {
        "has_data": True,
        "year": year,
        "month": month,
        "month_name": py_calendar.month_name[month],
        "prev_date": prev_date.isoformat(),
        "next_date": next_date.isoformat(),
        "current_date": current.isoformat(),
    }

    daily = daily_all[(daily_all["date"].dt.year == year) & (daily_all["date"].dt.month == month)]
    if daily.empty:
        return {"empty": True, "summary_rows": [], **month_nav}

    special_days = {sd.date: sd.day_type for sd in SpecialDay.objects.all()}
    daily = metrics.apply_special_days(daily, special_days)
    emp_rate_map = dict(Employee.objects.values_list("code", "ot_rate_per_hour"))
    grid = _build_month_grid(daily, special_days, emp_rate_map, ot_tooltip=True)
    shift_ot_table = grid["shift_ot_table"]

    summary_rows = []
    if not shift_ot_table.empty:
        ot_rows = shift_ot_table[shift_ot_table["total_ot_hours"] > 0]
        totals = (
            ot_rows.groupby(["department", "emp_code", "emp_name"])["total_ot_hours"]
            .sum().reset_index()
        )
        emp_rows_by_dept: dict = {}
        for r in totals.itertuples(index=False):
            rate = float(emp_rate_map.get(r.emp_code, 0))
            hours = round(float(r.total_ot_hours), 2)
            emp_rows_by_dept.setdefault(r.department, []).append({
                "is_dept": False,
                "emp_code": r.emp_code,
                "emp_name": r.emp_name,
                "department": r.department,
                "ot_hours": hours,
                "ot_rate": rate,
                "ot_amount": round(hours * rate, 2),
            })

        for dept in sorted(emp_rows_by_dept):
            emp_rows = sorted(emp_rows_by_dept[dept], key=lambda row: -row["ot_amount"])
            dept_hours = round(sum(row["ot_hours"] for row in emp_rows), 2)
            dept_amount = round(sum(row["ot_amount"] for row in emp_rows), 2)
            summary_rows.append({
                "is_dept": True,
                "department": dept,
                "headcount": len(emp_rows),
                "ot_hours": dept_hours,
                "ot_amount": dept_amount,
            })
            summary_rows.extend(emp_rows)

    summary_total_hours = round(sum(r["ot_hours"] for r in summary_rows if not r["is_dept"]), 2)
    summary_total_amount = round(sum(r["ot_amount"] for r in summary_rows if not r["is_dept"]), 2)

    return {
        **month_nav,
        "empty": False,
        "summary_rows": summary_rows,
        "summary_total_hours": summary_total_hours,
        "summary_total_amount": summary_total_amount,
        "day_headers": grid["day_headers"],
        "table_rows": _restrict_to_ot_cells(_ot_only_rows(grid["table_rows"])),
        # Not grid["total_cols"] — that's sized for dashboard.html's grid,
        # which renders 5 extra summary_cols (Work Days/Comp Off/etc.)
        # this report's grid never shows (label + day columns + 3 OT
        # columns only).
        "total_cols": 1 + len(grid["day_headers"]) + 3,
        "heat_colors": metrics.HEAT_COLORS,
    }


@login_required
def ot_details_view(request):
    """Reports > OT Details page — see _ot_details_context for the actual
    computation. Renders as two tabs, each with its own Print/Download:
    Monthly Summary (grouped by department) and Full Monthly View (the
    day x employee grid), scoped to one calendar month with prev/next
    nav."""
    context = _ot_details_context(request.GET.get("date"))
    return render(request, "attendance/ot_details.html", context)


def _apply_grid_borders(ws, min_row: int, max_row: int, max_col: int) -> None:
    """Thin border on every side of every cell in the given range — used
    by the OT Details .xlsx downloads so the header/data/total rows read
    as a proper ruled table instead of borderless openpyxl default cells."""
    from openpyxl.styles import Border, Side

    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for row in ws.iter_rows(min_row=min_row, max_row=max_row, max_col=max_col):
        for cell in row:
            cell.border = border


@login_required
def ot_details_download_view(request):
    """Downloads the OT Details Monthly Summary (grouped by department,
    one row per employee: OT hours, OT rate, OT amount, plus a department
    subtotal row) for one month as an .xlsx — same numbers as the report
    page's Monthly Summary tab, via the same _ot_details_context, just
    formatted as a workbook instead of HTML."""
    import openpyxl
    from openpyxl.styles import Font

    context = _ot_details_context(request.GET.get("date"))
    if not context.get("has_data") or context.get("empty"):
        raise Http404("No OT data for that month.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "OT Summary"

    month_label = f"{context['month_name']} {context['year']}"
    ws.append([f"OT Monthly Summary — {month_label}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    headers = [
        "Code", "Employee", "Department", "OT Hours", "OT Rate/hr", "OT Amount",
        "Dept OT Hours", "Dept OT Amount",
    ]
    ws.append(headers)
    header_row_num = ws.max_row
    for cell in ws[header_row_num]:
        cell.font = Font(bold=True)

    for row in context["summary_rows"]:
        if row["is_dept"]:
            ws.append([
                row["department"], "", "", "", "", "", row["ot_hours"], row["ot_amount"],
            ])
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)
        else:
            ws.append([
                row["emp_code"], row["emp_name"], row["department"],
                row["ot_hours"], row["ot_rate"], row["ot_amount"], "", "",
            ])

    ws.append([
        "", "", "Total", context["summary_total_hours"], "", context["summary_total_amount"], "", "",
    ])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)

    _apply_grid_borders(ws, header_row_num, ws.max_row, len(headers))

    widths = [10, 26, 22, 12, 12, 14, 14, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = f"ot-summary-{context['year']}-{context['month']:02d}.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@login_required
def ot_details_download_grid_view(request):
    """Downloads the OT Details Full Monthly View (day x employee grid,
    grouped by department, OT-relevant cells only — see _restrict_to_ot_cells)
    for one month as an .xlsx, via the same _ot_details_context used by the
    report page and the summary download."""
    import openpyxl
    from openpyxl.styles import Font

    context = _ot_details_context(request.GET.get("date"))
    if not context.get("has_data") or context.get("empty"):
        raise Http404("No OT data for that month.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "OT Full Monthly View"

    month_label = f"{context['month_name']} {context['year']}"
    day_headers = context["day_headers"]
    ws.append([f"OT Full Monthly View — {month_label}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    headers = ["Employee"] + [d["label"] for d in day_headers] + ["Total OT", "OT Rate/hr", "OT Amount"]
    ws.append(headers)
    header_row_num = ws.max_row
    for cell in ws[header_row_num]:
        cell.font = Font(bold=True)

    for row in context["table_rows"]:
        if row["is_dept"]:
            ws.append([row["label"].lstrip("▸ ").strip()])
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)
        else:
            ws.append(
                [row["label"].strip()]
                + [cell["value"] for cell in row["day_cells"]]
                + [row["total_ot"], row["ot_rate"], row["total_ot_amount"]]
            )

    _apply_grid_borders(ws, header_row_num, ws.max_row, len(headers))

    ws.column_dimensions["A"].width = 26
    for i in range(2, 2 + len(day_headers)):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 5
    for i in range(2 + len(day_headers), 5 + len(day_headers)):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 12

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = f"ot-full-monthly-{context['year']}-{context['month']:02d}.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response
