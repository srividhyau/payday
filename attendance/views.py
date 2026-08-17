import calendar as py_calendar
from datetime import date as date_cls
from datetime import timedelta

import pandas as pd
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render

from src import metrics

from .forms import UploadForm
from .importer import import_file
from .models import AttendanceRecord, MonthLock, SpecialDay, UploadBatch

# Mon..Sun abbreviations for the grid's day-of-week header — Thursday and
# Sunday get two letters (TH/SU) instead of just T/S, so they aren't
# ambiguous with Tuesday and Saturday.
_DOW_LABELS = {0: "M", 1: "T", 2: "W", 3: "TH", 4: "F", 5: "S", 6: "SU"}


def home_view(request):
    """Landing page — the app's root URL. Just a branded splash with links
    into the three real pages (Upload, Attendance, Holiday Calendar)."""
    return render(request, "attendance/home.html")


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
            except Exception as exc:  # noqa: BLE001 - surface any parse/import error to HR
                messages.error(request, f"Import failed: {exc}")
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
    current = date_cls.fromisoformat(date_param) if date_param else default_date
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

    working_days = metrics.infer_working_days(daily)
    holidays = metrics.holiday_dates(daily)
    paid_holidays = metrics.paid_holiday_dates(daily)
    comp_offs = metrics.comp_off_dates(daily)

    # Shift-based OT (M-OT/E-OT/ME-OT/Full-OT), same calculation as /ot/ —
    # drives every OT figure on this page except the day-cell's bold-red
    # OT text/background, which stays tied to special-day-worked hours.
    shift_ot_table = metrics.overtime_view(daily)
    shift_ot_map = (
        {(r.emp_code, r.date): r.total_ot_hours for r in shift_ot_table.itertuples(index=False)}
        if not shift_ot_table.empty else {}
    )
    emp_ot_totals = (
        shift_ot_table.groupby("emp_code")["total_ot_hours"].sum().to_dict()
        if not shift_ot_table.empty else {}
    )

    emp_summary = metrics.employee_summary(daily, working_days, shift_ot_map=emp_ot_totals)
    dept_summary = metrics.department_summary(emp_summary)
    kpi = metrics.kpis(emp_summary)

    extremes = metrics.department_attendance_extremes(emp_summary, n=2)
    high_leave = metrics.department_high_leave(emp_summary, threshold=4)
    dept_summary_rows = dept_summary.to_dict("records")
    for d in dept_summary_rows:
        d.update(extremes.get(d["department"], {"top": [], "worst": []}))
        d["high_leave"] = high_leave.get(d["department"], [])

    month_view, day_labels = metrics.month_attendance_view(daily, working_days)
    dates = sorted(daily["date"].unique())
    time_labels = metrics.punch_time_labels(daily, shift_ot_map)
    issues = metrics.punch_issues(daily)
    dept_missed_punch = metrics.department_missed_punch_summary(daily, issues)
    dept_ot_summary = metrics.department_ot_summary(shift_ot_table)
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
            suggested_shift = (
                metrics.suggest_shift_code(
                    time_in, time_out, special_worked_map.get(key, False),
                    dept_map.get(key, ""), row[d],
                )
                if not is_dept and shift == "GS" and not staff_map.get(key, False) else ""
            )
            day_cells.append({
                "value": "" if pd.isna(row[d]) else f"{row[d]:.1f}",
                "band": metrics.hours_heat_band(row[d]),
                "title": "" if is_dept else time_labels.get(key, ""),
                "issue": not is_dept and key in issues,
                "special": emp_status if is_special_cell else "",
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
                    not is_dept and shift == "GS" and bool(time_in) and bool(time_out)
                    and not staff_map.get(key, False)
                    and (bool(suggested_shift) or dept_map.get(key, "") == "HOUSE KEEPING")
                ),
            })
        summary_cells = [row[c] for c in summary_cols]
        total_ot = emp_ot_totals.get(row["Emp Code"], 0) if not is_dept else 0
        table_rows.append({
            "label": row["Row Labels"],
            "is_dept": is_dept,
            "day_cells": day_cells,
            "summary_cells": summary_cells,
            "total_ot": "" if is_dept or not total_ot else round(float(total_ot), 2),
        })

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
        "day_labels": day_labels,
        "day_headers": day_headers,
        "summary_cols": summary_cols,
        "table_rows": table_rows,
        "heat_colors": metrics.HEAT_COLORS,
        "total_cols": 1 + len(day_labels) + len(summary_cols) + 1,
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
        messages.error(request, "This view is locked for this month — unlock it first to make changes.")
        return redirect(next_url)

    try:
        record = AttendanceRecord.objects.get(employee__code=emp_code, date=date_str)
    except AttendanceRecord.DoesNotExist:
        messages.error(request, f"No attendance record for {emp_code} on {date_str}.")
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
    return redirect(next_url)


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
        messages.error(request, "Missing date.")
        return redirect(next_url)

    if _month_is_locked(date_str, view):
        messages.error(request, "This view is locked for this month — unlock it first to make changes.")
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
    return redirect(next_url)


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
        messages.error(request, "Missing month.")
        return redirect(next_url)

    view = request.POST.get("view", "all")
    if view not in dict(MonthLock.VIEW_CHOICES):
        view = MonthLock.VIEW_ALL

    pin = request.POST.get("pin", "").strip()
    action = request.POST.get("action", "")
    if pin != settings.ATTENDANCE_LOCK_PIN:
        messages.error(request, "Incorrect PIN.")
        return redirect(next_url)

    view_label = dict(MonthLock.VIEW_CHOICES)[view]
    if action == "lock":
        MonthLock.objects.get_or_create(year=year, month=month, view=view)
        messages.success(request, f"{view_label} is now locked for {py_calendar.month_name[month]} {year}.")
    elif action == "unlock":
        MonthLock.objects.filter(year=year, month=month, view=view).delete()
        messages.success(request, f"{view_label} is now unlocked for {py_calendar.month_name[month]} {year}.")
    return redirect(next_url)
