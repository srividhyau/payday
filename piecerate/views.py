import base64
import calendar
import hashlib
import functools
import io
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from PIL import Image, ImageColor, ImageDraw, ImageFont

from attendance.audit import log_change, log_changes
from attendance.models import AuditLogEntry, Employee, SpecialDay

from .models import (
    PAY_TYPE_CHOICES, PAY_TYPE_OPERATOR, Operation, OperatorLink, PieceRateEntry, RateCardOperation, Style,
)
from .permissions import can_edit_piece_rate, can_revoke_operator_links


def _require_piece_rate_editor(request, redirect_to=None):
    """Shared gate for Master Operations/Templates writes — everyone with
    general app access can still view both pages; only a superuser or a
    "Piece Rate Editor" can add/edit/delete. See piecerate/permissions.py."""
    if not can_edit_piece_rate(request.user):
        messages.error(request, "You don't have permission to edit this.")
        return redirect(redirect_to or request.path)
    return None


def _master_rate_map():
    return dict(Operation.objects.values_list("name", "rate"))


def _style_row(s, master_map):
    total = s.operations.aggregate(total=Sum("rate"))["total"] or Decimal("0")
    # SQLite's SUM() runs in floating point over Decimal columns, so
    # without rounding this can come back as e.g. 45.8000000000000003.
    total = total.quantize(Decimal("0.01"))

    # What the same set of operations would total at their Master
    # Operations rate — same "vs master" comparison as the Rate Card
    # page's per-row coloring, just summed for the whole style.
    master_total = sum(
        (master_map.get(name, Decimal("0")) for name in s.operations.values_list("name", flat=True)),
        Decimal("0"),
    )
    if total > master_total:
        rate_class = "rate-higher"
    elif total < master_total:
        rate_class = "rate-lower"
    else:
        rate_class = ""

    return {
        "style": s,
        "operation_count": s.operations.count(),
        "total_rate": total,
        "rate_class": rate_class,
    }


def _parse_decimal(value):
    try:
        return Decimal(str(value).strip() or "0")
    except InvalidOperation:
        return Decimal("0")


def _parse_int(value):
    try:
        return max(0, int(str(value).strip() or "0"))
    except ValueError:
        return 0


def _parse_month_date(date_param):
    if date_param:
        try:
            return date_cls.fromisoformat(date_param)
        except ValueError:
            pass
    return date_cls.today().replace(day=1)


def _unique_name(base_name):
    """base_name if free, else the first unused "<base_name> (2)" / "(3)" / ..."""
    if not Style.objects.filter(name__iexact=base_name).exists():
        return base_name
    n = 2
    while Style.objects.filter(name__iexact=f"{base_name} ({n})").exists():
        n += 1
    return f"{base_name} ({n})"


@login_required
def production_shortcut_view(request):
    """The "Production" menu link — Production is really a per-style
    page, so this jumps to the current month's first style (by name);
    the Production page itself has a dropdown to switch to any other
    style running the same month. Falls back to the Styles list only
    when there's nothing set up yet this month to jump to."""
    today = date_cls.today()
    style = Style.objects.filter(year=today.year, month=today.month, is_template=False).order_by("name").first()
    if style:
        return redirect("piece_rate_production", style_id=style.id)
    messages.info(request, "No styles set up yet this month — add one below, then open its Production page.")
    return redirect("piece_rate")


@login_required
def piece_rate_view(request):
    """Style list — the Piece Rate module's home page, scoped to a month
    exactly like Salary. Each month, add the styles being run that
    month — either blank ("create_style") or duplicated from a
    Template's rate card ("duplicate_template"), both tagged to the
    month being viewed. Duplicating copies every RateCardOperation row,
    so the new style is fully independent from the template afterward."""
    current = _parse_month_date(request.GET.get("date"))
    year, month = current.year, current.month

    if request.method == "POST":
        action = request.POST.get("action", "")
        redirect_url = f"{request.path}?date={current.isoformat()}"

        if action == "create_style":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Style name is required.")
            elif Style.objects.filter(name__iexact=name).exists():
                messages.error(request, f'A style named "{name}" already exists.')
            else:
                Style.objects.create(name=name, year=year, month=month, start_date=date_cls(year, month, 1))
                messages.success(request, f'Style "{name}" created.')
            return redirect(redirect_url)
        if action == "duplicate_template":
            template = get_object_or_404(Style, id=request.POST.get("template_id"), is_template=True)
            new_name = _unique_name(f"{template.name} {calendar.month_abbr[month]} {year}")
            new_style = Style.objects.create(name=new_name, year=year, month=month, start_date=date_cls(year, month, 1))
            RateCardOperation.objects.bulk_create([
                RateCardOperation(
                    style=new_style, op_code=op.op_code, section=op.section, name=op.name,
                    machine=op.machine, rate=op.rate, pay_type=op.pay_type,
                )
                for op in template.operations.all()
            ])
            messages.success(request, f'"{new_name}" created from "{template.name}".')
            return redirect(redirect_url)
        if action == "rename_style":
            style = get_object_or_404(Style, id=request.POST.get("style_id"))
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Style name is required.")
            elif Style.objects.filter(name__iexact=name).exclude(pk=style.pk).exists():
                messages.error(request, f'A style named "{name}" already exists.')
            else:
                style.name = name
                start_date_raw = request.POST.get("start_date", "").strip()
                if start_date_raw:
                    try:
                        style.start_date = date_cls.fromisoformat(start_date_raw)
                    except ValueError:
                        pass
                if request.FILES.get("image"):
                    style.image = request.FILES["image"]
                style.save()
                messages.success(request, f'Style "{name}" updated.')
            return redirect(redirect_url)
        if action == "delete_style":
            Style.objects.filter(id=request.POST.get("style_id")).delete()
            messages.success(request, "Style deleted.")
            return redirect(redirect_url)
        if action.startswith("shift_next_month_"):
            style = get_object_or_404(
                Style, id=action[len("shift_next_month_"):], is_template=False,
            )
            # Re-home the style one month forward — Operator/Style/Management
            # Summary all group by the style's own year/month (not each
            # entry's date), so this alone moves its rate card, Order Qty,
            # and every already-logged entry into next month's reporting.
            # The Production page separately makes sure nothing already
            # logged under the old month becomes invisible just because it
            # no longer falls within the style's new month.
            next_month_date = (date_cls(style.year, style.month, 1) + timedelta(days=32)).replace(day=1)
            style.year, style.month = next_month_date.year, next_month_date.month
            style.save()
            messages.success(
                request,
                f'"{style.name}" shifted to {calendar.month_name[style.month]} {style.year}.',
            )
            return redirect(redirect_url)
        if action.startswith("save_as_template_"):
            denied = _require_piece_rate_editor(request, redirect_to=redirect_url)
            if denied:
                return denied
            style = get_object_or_404(
                Style, id=action[len("save_as_template_"):], is_template=False,
            )
            new_name = _unique_name(style.name)
            new_template = Style.objects.create(name=new_name, is_template=True)
            RateCardOperation.objects.bulk_create([
                RateCardOperation(
                    style=new_template, op_code=op.op_code, section=op.section, name=op.name,
                    machine=op.machine, rate=op.rate, pay_type=op.pay_type,
                )
                for op in style.operations.all()
            ])
            messages.success(request, f'"{new_name}" added to Templates.')
            return redirect(redirect_url)

    month_styles = Style.objects.filter(year=year, month=month, is_template=False)
    prev_date = (current.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_date = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    master_map = _master_rate_map()

    return render(request, "piecerate/style_list.html", {
        "styles": [_style_row(s, master_map) for s in month_styles],
        "templates": Style.objects.filter(is_template=True),
        "year": year,
        "month": month,
        "month_name": calendar.month_name[month],
        "prev_date": prev_date.isoformat(),
        "next_date": next_date.isoformat(),
    })


@login_required
def template_list_view(request):
    """Permanent, reusable rate cards with no month of their own — kept
    as a reference, never used for production directly."""
    if request.method == "POST":
        denied = _require_piece_rate_editor(request)
        if denied:
            return denied
        action = request.POST.get("action", "")
        if action == "create_template":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Template name is required.")
            elif Style.objects.filter(name__iexact=name).exists():
                messages.error(request, f'A style named "{name}" already exists.')
            else:
                Style.objects.create(name=name, is_template=True)
                messages.success(request, f'Template "{name}" created.')
            return redirect("piece_rate_templates")
        if action == "rename_style":
            style = get_object_or_404(Style, id=request.POST.get("style_id"), is_template=True)
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Template name is required.")
            elif Style.objects.filter(name__iexact=name).exclude(pk=style.pk).exists():
                messages.error(request, f'A style named "{name}" already exists.')
            else:
                style.name = name
                style.save()
                messages.success(request, f'Template renamed to "{name}".')
            return redirect("piece_rate_templates")
        if action == "delete_style":
            Style.objects.filter(id=request.POST.get("style_id"), is_template=True).delete()
            messages.success(request, "Template deleted.")
            return redirect("piece_rate_templates")

    templates = Style.objects.filter(is_template=True)
    master_map = _master_rate_map()
    return render(request, "piecerate/template_list.html", {
        "templates": [_style_row(s, master_map) for s in templates],
    })


def _rate_card_op_snapshot(op: RateCardOperation) -> dict:
    """Every field the Rate Card page's create/edit form can touch, for
    diffing before/after an edit — see AuditLogEntry."""
    return {
        "section": op.section, "name": op.name, "machine": op.machine,
        "rate": op.rate, "pay_type": op.pay_type, "order_quantity": op.order_quantity,
    }


@login_required
def rate_card_view(request, style_id):
    """One Style's rate card: every Operation with its Section/Machine/
    Rate/Pay type. Add, edit, delete an Operation here — no daily
    quantity entry or pay computation in this module yet."""
    style = get_object_or_404(Style, id=style_id)

    if request.method == "POST":
        if style.is_template:
            denied = _require_piece_rate_editor(request)
            if denied:
                return denied
        action = request.POST.get("action", "")

        actor = request.user.get_username() or "unknown"

        if action.startswith("delete_"):
            op_id = action[len("delete_"):]
            deleted_op = RateCardOperation.objects.filter(id=op_id, style=style).first()
            if deleted_op:
                log_change(
                    actor, "piecerate", "RateCardOperation", deleted_op.id,
                    f"{style.name} — {deleted_op.name}", "__deleted__", deleted_op.name, "deleted",
                )
            RateCardOperation.objects.filter(id=op_id, style=style).delete()
            messages.success(request, "Operation deleted.")
            return redirect("piece_rate_rate_card", style_id=style.id)

        if action == "reorder":
            order = [int(v) for v in request.POST.get("order_csv", "").split(",") if v.strip()]
            ops = list(RateCardOperation.objects.filter(style=style, id__in=order))
            ops_by_id = {op.id: op for op in ops}
            # Two-phase renumber: op_code is unique per (style, op_code), so
            # jump every row to a temp range first to avoid transient
            # collisions with rows that haven't been renumbered yet.
            for op in ops:
                op.op_code += 100000
            RateCardOperation.objects.bulk_update(ops, ["op_code"])
            for position, op_id in enumerate(order, start=1):
                if op_id in ops_by_id:
                    ops_by_id[op_id].op_code = position
            RateCardOperation.objects.bulk_update(ops, ["op_code"])
            messages.success(request, "Order saved.")
            return redirect("piece_rate_rate_card", style_id=style.id)

        if action == "bulk_order_quantity":
            qty = _parse_int(request.POST.get("bulk_order_quantity"))
            before = list(style.operations.values("id", "name", "order_quantity"))
            updated = style.operations.update(order_quantity=qty)
            for op_row in before:
                log_change(
                    actor, "piecerate", "RateCardOperation", op_row["id"],
                    f"{style.name} — {op_row['name']}", "order_quantity", op_row["order_quantity"], qty,
                )
            messages.success(request, f"Order Qty set to {qty} on {updated} operation(s).")
            return redirect("piece_rate_rate_card", style_id=style.id)

        if action == "create" or action.startswith("edit_"):
            suffix = "" if action == "create" else f"_{action[len('edit_'):]}"
            is_new = not suffix
            if suffix:
                op = get_object_or_404(RateCardOperation, id=action[len("edit_"):], style=style)
                old_values = _rate_card_op_snapshot(op)
            else:
                op = RateCardOperation(style=style)
                last_code = RateCardOperation.objects.filter(style=style).count()
                op.op_code = last_code + 1
                old_values = None

            name = request.POST.get(f"name{suffix}", "").strip()
            duplicate = RateCardOperation.objects.filter(style=style, name=name).exclude(pk=op.pk)

            if not name:
                messages.error(request, "Operation name is required.")
            elif duplicate.exists():
                messages.error(request, f'"{name}" is already on this rate card.')
            else:
                op.section = request.POST.get(f"section{suffix}", "").strip()
                op.name = name
                op.machine = request.POST.get(f"machine{suffix}", "").strip()
                op.rate = _parse_decimal(request.POST.get(f"rate{suffix}"))
                op.pay_type = request.POST.get(f"pay_type{suffix}") or PAY_TYPE_OPERATOR
                op.order_quantity = _parse_int(request.POST.get(f"order_quantity{suffix}"))
                op.save()
                object_repr = f"{style.name} — {op.name}"
                if is_new:
                    log_change(actor, "piecerate", "RateCardOperation", op.id, object_repr, "__created__", "", "created")
                else:
                    new_values = _rate_card_op_snapshot(op)
                    changes = {
                        field: (old_values[field], new_values[field])
                        for field in old_values if str(old_values[field]) != str(new_values[field])
                    }
                    if changes:
                        log_changes(actor, "piecerate", "RateCardOperation", op.id, object_repr, changes)
                messages.success(request, f'Operation "{op.name}" saved.')
            return redirect("piece_rate_rate_card", style_id=style.id)

    master_operations = Operation.objects.all()
    master_map = {
        op.name: {
            "section": op.section, "machine": op.machine, "rate": str(op.rate), "pay_type": op.pay_type,
        }
        for op in master_operations
    }
    return render(request, "piecerate/rate_card.html", {
        "style": style,
        "all_styles": Style.objects.all(),
        "operations": style.operations.all(),
        "master_operations": master_operations,
        "master_map": master_map,
        "pay_type_choices": PAY_TYPE_CHOICES,
        "can_edit_this_style": not style.is_template or can_edit_piece_rate(request.user),
    })


@login_required
def master_operations_view(request):
    """Master Operations catalog — the canonical list of sewing
    operations shared across styles, seeded from last month's rate
    cards after de-duplicating spelling/abbreviation drift. Add, edit,
    delete an operation here."""
    if request.method == "POST":
        denied = _require_piece_rate_editor(request)
        if denied:
            return denied
        action = request.POST.get("action", "")
        if action in ("create", "edit"):
            if action == "edit":
                op = get_object_or_404(Operation, id=request.POST.get("op_id"))
            else:
                op = Operation()

            name = request.POST.get("name", "").strip()
            duplicate = Operation.objects.filter(name__iexact=name).exclude(pk=op.pk)

            if not name:
                messages.error(request, "Operation name is required.")
            elif duplicate.exists():
                messages.error(request, f'"{name}" is already in the master list.')
            else:
                op.section = request.POST.get("section", "").strip()
                op.name = name
                op.machine = request.POST.get("machine", "").strip()
                op.rate = _parse_decimal(request.POST.get("rate"))
                op.save()
                messages.success(request, f'Operation "{op.name}" saved.')
            return redirect("piece_rate_operations")

        if action == "delete":
            Operation.objects.filter(id=request.POST.get("op_id")).delete()
            messages.success(request, "Operation deleted.")
            return redirect("piece_rate_operations")

    sections = list(
        Operation.objects.exclude(section="").order_by("section").values_list("section", flat=True).distinct()
    )
    section_filter = request.GET.get("section", "")
    query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort") or "section"
    direction = request.GET.get("dir") or "asc"
    if sort not in ("section", "name", "machine", "rate"):
        sort = "section"
    order_field = sort if direction == "asc" else f"-{sort}"
    secondary = "name" if sort != "name" else "section"

    operations = Operation.objects.all()
    if section_filter:
        operations = operations.filter(section=section_filter)
    if query:
        operations = operations.filter(name__icontains=query)
    operations = operations.order_by(order_field, secondary)

    return render(request, "piecerate/master_operations.html", {
        "operations": operations,
        "query": query,
        "sort": sort,
        "dir": direction,
        "sections": sections,
        "section_filter": section_filter,
    })


def _cell_object_id(rc_op_id, employee_id, entry_date) -> str:
    """Stable key for one Production cell (operation + operator + day),
    for AuditLogEntry.object_id — deliberately NOT PieceRateEntry.pk,
    since a quantity dropping to 0 deletes that row and a later entry
    recreates it under a new pk; this composite key stays the same
    across that delete/recreate, so a cell's full history is always
    one lookup regardless of how many times it's been cleared."""
    return f"{rc_op_id}:{employee_id}:{entry_date.isoformat()}"


def _cell_object_repr(style, rc_op, employee, entry_date) -> str:
    return f"{style.name} — {rc_op.name} — {employee.name} — {entry_date.isoformat()}"


def _log_piece_rate_quantity_change(actor, rc_op, employee, entry_date, old_quantity, new_quantity) -> None:
    """One AuditLogEntry for a Production cell's quantity changing —
    shared by every path that can change a PieceRateEntry's quantity
    (the desktop/mobile Production grid's supervisor cell-save AND the
    operator's own no-login mobile self-entry page), so a cell's history
    is always complete regardless of which UI made the edit, and the
    key/repr can't drift between the two call sites."""
    log_change(
        actor, "piecerate", "PieceRateEntry",
        _cell_object_id(rc_op.id, employee.id, entry_date),
        _cell_object_repr(rc_op.style, rc_op, employee, entry_date),
        "quantity", old_quantity, new_quantity,
    )


@login_required
def _save_production_cell(request, style):
    """One day's (operation, operator) quantity, from either Production
    template's AJAX cell-save call — same endpoint, same rules,
    regardless of which UI is driving it. Every change (including a
    remove-operator wipe) is audit-logged — see AuditLogEntry — keyed by
    _cell_object_id, not the PieceRateEntry row's own pk."""
    rc_op = get_object_or_404(RateCardOperation, id=request.POST.get("rate_card_operation_id"), style=style)
    employee = get_object_or_404(Employee, id=request.POST.get("employee_id"))
    actor = request.user.get_username() or "unknown"

    if request.POST.get("action") == "remove_operator":
        removed = list(PieceRateEntry.objects.filter(rate_card_operation=rc_op, employee=employee))
        PieceRateEntry.objects.filter(rate_card_operation=rc_op, employee=employee).delete()
        for entry in removed:
            _log_piece_rate_quantity_change(actor, rc_op, employee, entry.date, entry.quantity, 0)
        return JsonResponse({"ok": True})

    try:
        entry_date = date_cls.fromisoformat(request.POST.get("date", ""))
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid date."}, status=400)
    quantity = _parse_int(request.POST.get("quantity"))

    existing = PieceRateEntry.objects.filter(rate_card_operation=rc_op, employee=employee, date=entry_date).first()
    old_quantity = existing.quantity if existing else 0

    # Order Qty is enforced client-side only (a confirm popup) for now —
    # a supervisor can go over it here as long as they confirm it, rather
    # than being hard-blocked.
    if quantity > 0:
        PieceRateEntry.objects.update_or_create(
            rate_card_operation=rc_op, employee=employee, date=entry_date,
            defaults={"quantity": quantity, "entered_by": PieceRateEntry.ENTERED_BY_SUPERVISOR},
        )
    else:
        PieceRateEntry.objects.filter(rate_card_operation=rc_op, employee=employee, date=entry_date).delete()

    _log_piece_rate_quantity_change(actor, rc_op, employee, entry_date, old_quantity, quantity)

    op_total = PieceRateEntry.objects.filter(rate_card_operation=rc_op).aggregate(total=Sum("quantity"))["total"] or 0
    return JsonResponse({"ok": True, "op_total": op_total})


@login_required
def production_cell_history_view(request):
    """AJAX endpoint behind the Production grid's per-cell history
    trigger (see production.html) — every AuditLogEntry recorded for one
    (operation, operator, day) cell, newest first. Keyed by
    _cell_object_id, not a PieceRateEntry pk, so history survives that
    row being deleted (quantity -> 0) and recreated later."""
    try:
        rc_op_id = int(request.GET.get("rate_card_operation_id", ""))
        employee_id = int(request.GET.get("employee_id", ""))
        entry_date = date_cls.fromisoformat(request.GET.get("date", ""))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid cell."}, status=400)

    object_id = _cell_object_id(rc_op_id, employee_id, entry_date)
    entries = AuditLogEntry.objects.filter(
        app_label="piecerate", model_name="PieceRateEntry", object_id=object_id,
    ).order_by("-timestamp")
    return JsonResponse({
        "ok": True,
        "entries": [
            {
                "timestamp": date_format(entry.timestamp, "j M Y, g:i a"),
                "actor": entry.actor,
                "old_value": entry.old_value,
                "new_value": entry.new_value,
            }
            for entry in entries
        ],
    })


def _build_production_context(style):
    """Everything both Production templates (desktop grid, mobile
    cards) need to render the same style — one place computing it so
    the two UIs can never quietly drift apart on what a cell's color,
    total, or availability actually means."""
    num_days_in_month = calendar.monthrange(style.year, style.month)[1]
    month_start = date_cls(style.year, style.month, 1)
    month_end = date_cls(style.year, style.month, num_days_in_month)
    # A style that actually started before the 1st of its own month
    # (Style.start_date, e.g. the 20th of the prior month) shows those
    # earlier days too, not just once someone happens to log against
    # one — the whole point of setting start_date is to see them.
    range_start = min(style.start_date, month_start) if style.start_date else month_start
    base_days = {range_start + timedelta(n) for n in range((month_end - range_start).days + 1)}

    rc_ops = list(style.operations.all())

    # A style "shifted to next month" keeps every day that already has an
    # entry logged against it, even the ones from before the shift — so
    # nothing already recorded ever disappears off this page just because
    # it no longer falls in the style's current month (or its start_date
    # range above).
    entries_outside_range = set(
        PieceRateEntry.objects.filter(rate_card_operation__in=rc_ops)
        .exclude(date__gte=range_start, date__lte=month_end)
        .values_list("date", flat=True)
    )
    days = sorted(base_days | entries_outside_range)
    # Everything outside the style's own calendar month — its start_date
    # days and any stray carried-over entries alike — highlighted the
    # same way, so the header/cell shading always agrees with each other.
    carried_over_dates = [d for d in days if d < month_start or d > month_end]
    carried_over_set = set(carried_over_dates)
    day_headers = [
        {"date": d, "day": d.day, "dow": d.strftime("%a"), "is_carried_over": d in carried_over_set}
        for d in days
    ]

    # Same Holiday/Paid Holiday/Comp Off calendar (and colors) as the
    # Attendance dashboard, so a day already marked off there reads the
    # same way here.
    special_days = {
        sd.date.isoformat(): sd.day_type
        for sd in SpecialDay.objects.filter(date__in=days)
    }

    # Same "active at some point in the month" rule Attendance/Salary use
    # (Employee.active_during, via EmploymentPeriod) — someone who's left
    # or hasn't joined yet shouldn't be pickable as an operator for this
    # style's month, even if their Employee record is still around.
    all_employees = list(
        Employee.objects.filter(department__name__iexact="Operator")
        .active_during(month_start, month_end)
        .order_by("name")
    )
    employees_by_id = {e.id: e for e in all_employees}

    # Which (operation, employee, date) cells have ANY recorded audit
    # history at all — a single global lookup rather than one query per
    # cell, since the point is just "is there anything to show" (see
    # _cell_object_id) for deciding whether the Production grid's
    # history-trigger dot is worth rendering on that cell; a cell that
    # was entered and then cleared back to 0 still has history even
    # though it has no current PieceRateEntry row.
    history_ids = set(
        AuditLogEntry.objects.filter(app_label="piecerate", model_name="PieceRateEntry")
        .values_list("object_id", flat=True).distinct()
    )

    entries_by_op = {}
    source_by_op = {}
    operator_qty_by_op = {}
    for e in PieceRateEntry.objects.filter(rate_card_operation__in=rc_ops):
        entries_by_op.setdefault(e.rate_card_operation_id, {}).setdefault(e.employee_id, {})[e.date.isoformat()] = e.quantity
        if e.entered_by == PieceRateEntry.ENTERED_BY_OPERATOR:
            source_class = "entered-operator"
        elif e.was_operator_entered:
            # Currently the supervisor's value, but an operator had
            # their own entry here at some point — a dot rather than
            # the full operator fill, so "still theirs" and "used to
            # be theirs, now corrected" don't look identical.
            source_class = "corrected"
        else:
            source_class = ""
        source_by_op.setdefault(e.rate_card_operation_id, {}).setdefault(e.employee_id, {})[e.date.isoformat()] = source_class
        if source_class == "corrected":
            operator_qty_by_op.setdefault(e.rate_card_operation_id, {}).setdefault(e.employee_id, {})[e.date.isoformat()] = e.operator_quantity

    op_rows = []
    for op in rc_ops:
        op_entries = entries_by_op.get(op.id, {})
        op_source = source_by_op.get(op.id, {})
        op_operator_qty = operator_qty_by_op.get(op.id, {})
        rows = []
        for emp_id, day_map in op_entries.items():
            employee = employees_by_id.get(emp_id)
            if not employee:
                continue
            rows.append({
                "employee": employee, "days": day_map, "total": sum(day_map.values()),
                "source_class": op_source.get(emp_id, {}),
                "operator_qty": op_operator_qty.get(emp_id, {}),
                "has_history": {
                    d.isoformat(): _cell_object_id(op.id, emp_id, d) in history_ids for d in days
                },
            })
        rows.sort(key=lambda r: r["employee"].name)

        op_total = sum(r["total"] for r in rows)
        diff = op_total - op.order_quantity
        if not op.order_quantity:
            diff_class, diff_display = "", ""
        elif diff > 0:
            diff_class, diff_display = "diff-more", f"+{diff}"
        elif diff < 0:
            diff_class, diff_display = "diff-less", str(diff)
        else:
            diff_class, diff_display = "diff-balanced", "0"

        used_employee_ids = {r["employee"].id for r in rows}
        op_rows.append({
            "op": op,
            "rows": rows,
            "op_total": op_total,
            "diff_class": diff_class,
            "diff_display": diff_display,
            "available_employees": [e for e in all_employees if e.id not in used_employee_ids],
        })

    sibling_styles = Style.objects.filter(year=style.year, month=style.month, is_template=False).order_by("name")

    return {
        "style": style,
        "days": days,
        "op_rows": op_rows,
        "special_days": special_days,
        "day_headers": day_headers,
        "carried_over_dates": sorted(carried_over_dates),
        "sibling_styles": sibling_styles,
        "today": date_cls.today(),
        # Every operator eligible for this style's month, regardless of
        # who's already on a given operation — desktop Production's "+"
        # picker lists this (not the per-row available_employees) so
        # every operator is always visible there; available_employees is
        # still what decides whether the "+" itself is worth showing at
        # all (and remains what production_mobile.html's own picker uses).
        "all_employees": all_employees,
    }


def production_view(request, style_id):
    """Per-style daily production entry — the desktop grid, one column
    per day of the style's own month, every operator who has logged
    against an operation getting a row. An operation's order_quantity
    (set on the Rate Card page) is the total every operator's entries
    for it should sum to across the month — op_total vs order_quantity
    is what drives the match/mismatch coloring client-side expects.
    See production_mobile_view for the phone-friendly alternative,
    sharing this same data and the same cell-save endpoint."""
    style = get_object_or_404(Style, id=style_id, is_template=False)
    if request.method == "POST":
        return _save_production_cell(request, style)
    return render(request, "piecerate/production.html", _build_production_context(style))


def production_mobile_view(request, style_id):
    """Same style, same data, same cell-save endpoint as production_view
    — just a compact, one-operator-row-at-a-time layout instead of the
    full day x operator grid, since that grid doesn't fit comfortably
    on a phone screen. An experimental parallel UI for now rather than
    a replacement, reachable via the "Mobile view" link on the desktop
    page (and back again via "Desktop view" here)."""
    style = get_object_or_404(Style, id=style_id, is_template=False)
    if request.method == "POST":
        return _save_production_cell(request, style)
    return render(request, "piecerate/production_mobile.html", _build_production_context(style))


def _qty_diff_class(quantity, order_quantity):
    """Shared by Operator Summary and Style Summary — the same
    more/less/balanced classes Production uses, keyed off a leaf
    operation's worked quantity vs. its order_quantity."""
    if not order_quantity:
        return ""
    diff = quantity - order_quantity
    if diff > 0:
        return "diff-more"
    if diff < 0:
        return "diff-less"
    return "diff-balanced"


def _month_piece_rate_entries(year, month):
    return (
        PieceRateEntry.objects.filter(
            rate_card_operation__style__year=year, rate_card_operation__style__month=month,
        )
        .select_related("employee", "rate_card_operation", "rate_card_operation__style")
    )


@login_required
def operator_summary_view(request):
    """What every operator worked on this month, across every style, and
    how much it comes to — quantity x the operation's current Rate Card
    rate. Amounts here always reflect whatever a Rate Card currently
    says (no per-entry rate snapshot), so editing a rate after the fact
    shifts past months' totals too — same as everywhere else rates are
    used in this app."""
    current = _parse_month_date(request.GET.get("date"))
    year, month = current.year, current.month
    prev_date = (current.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_date = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

    entries = _month_piece_rate_entries(year, month)

    # Operator -> Style -> Operation, each level totaling the ones below it.
    by_employee = {}
    for e in entries:
        rc_op = e.rate_card_operation
        style = rc_op.style
        emp_bucket = by_employee.setdefault(e.employee_id, {"employee": e.employee, "styles": {}})
        style_bucket = emp_bucket["styles"].setdefault(style.id, {"style_name": style.name, "ops": {}})
        op_bucket = style_bucket["ops"].setdefault(rc_op.id, {
            "op_name": rc_op.name, "section": rc_op.section, "rate": rc_op.rate,
            "order_quantity": rc_op.order_quantity, "quantity": 0,
        })
        op_bucket["quantity"] += e.quantity

    operator_rows = []
    grand_total = Decimal("0")
    for emp_data in by_employee.values():
        style_list = []
        employee_total = Decimal("0")
        # Order Qty per style is a MAX across that style's operations (one
        # order size per style, see management_summary_view) — an
        # operator who worked across several styles gets those per-style
        # order sizes SUMMED, since each is a genuinely separate order.
        # Quantity and Rate are plain sums: quantity because pieces made
        # is additive regardless of style/operation; rate because each
        # (style, operation) row is distinct, so nothing is double-counted.
        employee_order_qty = 0
        employee_rate = Decimal("0")
        employee_quantity = 0
        for style_data in emp_data["styles"].values():
            op_list = []
            style_total = Decimal("0")
            style_order_qty = 0
            for op_bucket in style_data["ops"].values():
                amount = (op_bucket["rate"] * op_bucket["quantity"]).quantize(Decimal("0.01"))
                style_total += amount
                diff_class = _qty_diff_class(op_bucket["quantity"], op_bucket["order_quantity"])
                op_list.append({**op_bucket, "amount": amount, "diff_class": diff_class})
                style_order_qty = max(style_order_qty, op_bucket["order_quantity"])
                employee_rate += op_bucket["rate"]
                employee_quantity += op_bucket["quantity"]
            op_list.sort(key=lambda o: o["op_name"])
            style_list.append({"style_name": style_data["style_name"], "ops": op_list, "total": style_total})
            employee_total += style_total
            employee_order_qty += style_order_qty
        style_list.sort(key=lambda s: s["style_name"])
        operator_rows.append({
            "employee": emp_data["employee"], "styles": style_list, "total": employee_total,
            "order_quantity": employee_order_qty, "rate_sum": employee_rate, "quantity_sum": employee_quantity,
        })
        grand_total += employee_total

    operator_rows.sort(key=lambda r: r["employee"].name)

    return render(request, "piecerate/operator_summary.html", {
        "operator_rows": operator_rows,
        "grand_total": grand_total,
        "year": year,
        "month": month,
        "month_name": calendar.month_name[month],
        "current_date": current.isoformat(),
        "prev_date": prev_date.isoformat(),
        "next_date": next_date.isoformat(),
    })


@login_required
def style_summary_view(request):
    """Same data as Operator Summary, grouped Style -> Operator ->
    Operation instead of Operator -> Style -> Operation — which style is
    costing the most and who worked it, rather than what one operator
    did across every style."""
    current = _parse_month_date(request.GET.get("date"))
    year, month = current.year, current.month
    prev_date = (current.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_date = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

    entries = _month_piece_rate_entries(year, month)

    # Style -> Operator -> Operation, each level totaling the ones below it.
    by_style = {}
    for e in entries:
        rc_op = e.rate_card_operation
        style = rc_op.style
        style_bucket = by_style.setdefault(style.id, {"style_name": style.name, "employees": {}})
        emp_bucket = style_bucket["employees"].setdefault(e.employee_id, {"employee": e.employee, "ops": {}})
        op_bucket = emp_bucket["ops"].setdefault(rc_op.id, {
            "op_name": rc_op.name, "section": rc_op.section, "rate": rc_op.rate,
            "order_quantity": rc_op.order_quantity, "quantity": 0,
        })
        op_bucket["quantity"] += e.quantity

    style_rows = []
    grand_total = Decimal("0")
    for style_data in by_style.values():
        employee_list = []
        style_total = Decimal("0")
        # Order Qty/Rate come from the style's own DISTINCT operations —
        # the same operation can appear once per employee who worked it,
        # so seen_op_ids makes sure each operation's order_quantity/rate
        # is only counted once no matter how many people touched it.
        # Quantity has no such risk; every entry is summed regardless.
        style_order_qty = 0
        style_rate = Decimal("0")
        style_quantity = 0
        seen_op_ids = set()
        for emp_data in style_data["employees"].values():
            op_list = []
            employee_total = Decimal("0")
            for op_id, op_bucket in emp_data["ops"].items():
                amount = (op_bucket["rate"] * op_bucket["quantity"]).quantize(Decimal("0.01"))
                employee_total += amount
                diff_class = _qty_diff_class(op_bucket["quantity"], op_bucket["order_quantity"])
                op_list.append({**op_bucket, "amount": amount, "diff_class": diff_class})
                style_quantity += op_bucket["quantity"]
                if op_id not in seen_op_ids:
                    seen_op_ids.add(op_id)
                    style_order_qty = max(style_order_qty, op_bucket["order_quantity"])
                    style_rate += op_bucket["rate"]
            op_list.sort(key=lambda o: o["op_name"])
            employee_list.append({"employee": emp_data["employee"], "ops": op_list, "total": employee_total})
            style_total += employee_total
        employee_list.sort(key=lambda e: e["employee"].name)
        style_rows.append({
            "style_name": style_data["style_name"], "employees": employee_list, "total": style_total,
            "order_quantity": style_order_qty, "rate_sum": style_rate, "quantity_sum": style_quantity,
        })
        grand_total += style_total

    style_rows.sort(key=lambda r: r["style_name"])

    return render(request, "piecerate/style_summary.html", {
        "style_rows": style_rows,
        "grand_total": grand_total,
        "year": year,
        "month": month,
        "month_name": calendar.month_name[month],
        "current_date": current.isoformat(),
        "prev_date": prev_date.isoformat(),
        "next_date": next_date.isoformat(),
    })


@login_required
def management_summary_view(request):
    """Style -> Operation -> Operator, planned vs actual: every operation
    on every non-template style set up this month, whether or not anyone
    has logged against it yet — Planned Amount (rate x Order Qty, the
    budgeted cost) next to Actual Amount (rate x what was actually
    worked). Unlike Operator/Style Summary (built purely from logged
    entries), this starts from the rate cards themselves, so an
    operation with a big plan and zero actual work still shows up —
    the whole point of a management/budget view."""
    current = _parse_month_date(request.GET.get("date"))
    year, month = current.year, current.month
    prev_date = (current.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_date = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

    styles = Style.objects.filter(year=year, month=month, is_template=False).prefetch_related("operations")

    actual_by_op = {}
    for e in _month_piece_rate_entries(year, month):
        op_bucket = actual_by_op.setdefault(e.rate_card_operation_id, {})
        emp_bucket = op_bucket.setdefault(e.employee_id, {"employee": e.employee, "quantity": 0})
        emp_bucket["quantity"] += e.quantity

    style_rows = []
    grand_planned = Decimal("0")
    grand_actual = Decimal("0")
    for style in styles:
        op_rows = []
        style_planned = Decimal("0")
        style_actual = Decimal("0")
        style_order_qty = 0
        style_rate = Decimal("0")
        style_quantity = 0
        for op in style.operations.all():
            # The style's overall order quantity, not a sum across
            # operations — every operation is normally cut for the same
            # order, so the highest order_quantity seen is that order
            # size; some operations legitimately apply to only part of
            # it (a lower order_quantity), which shouldn't inflate this.
            style_order_qty = max(style_order_qty, op.order_quantity)
            style_rate += op.rate
            planned_amount = (op.rate * op.order_quantity).quantize(Decimal("0.01"))

            operator_list = []
            actual_quantity = 0
            for emp_data in actual_by_op.get(op.id, {}).values():
                qty = emp_data["quantity"]
                actual_quantity += qty
                operator_list.append({
                    "employee": emp_data["employee"], "quantity": qty,
                    "amount": (op.rate * qty).quantize(Decimal("0.01")),
                })
            operator_list.sort(key=lambda o: o["employee"].name)

            actual_amount = (op.rate * actual_quantity).quantize(Decimal("0.01"))
            op_rows.append({
                "op": op, "planned_amount": planned_amount, "actual_quantity": actual_quantity,
                "actual_amount": actual_amount, "diff_class": _qty_diff_class(actual_quantity, op.order_quantity),
                "operators": operator_list,
            })
            style_planned += planned_amount
            style_actual += actual_amount
            style_quantity += actual_quantity

        style_rows.append({
            "style": style, "ops": op_rows, "planned_total": style_planned, "actual_total": style_actual,
            "order_quantity": style_order_qty, "rate_sum": style_rate, "quantity_sum": style_quantity,
        })
        grand_planned += style_planned
        grand_actual += style_actual

    return render(request, "piecerate/management_summary.html", {
        "style_rows": style_rows,
        "grand_planned": grand_planned,
        "grand_actual": grand_actual,
        "year": year,
        "month": month,
        "month_name": calendar.month_name[month],
        "current_date": current.isoformat(),
        "prev_date": prev_date.isoformat(),
        "next_date": next_date.isoformat(),
    })


def _qr_data_uri(data):
    qr = qrcode.make(data)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@login_required
def operator_links_view(request):
    """Generate/manage each operator's private mobile entry link (see
    OperatorLink and operator_entry_view) — generating/regenerating is
    gated the same way as Master Operations/Templates, since handing one
    out is effectively granting daily production-entry access. Revoking
    is open to the narrower "Operator Link Revoker" role too, who never
    see the links themselves (a link IS the operator's identity)."""
    can_manage = can_edit_piece_rate(request.user)
    can_revoke = can_revoke_operator_links(request.user)
    if not (can_manage or can_revoke):
        messages.error(request, "You don't have permission to edit this.")
        return redirect("home")

    if request.method == "POST":
        employee = get_object_or_404(Employee, id=request.POST.get("employee_id"))
        action = request.POST.get("action", "")
        if action == "revoke":
            if not can_revoke:
                messages.error(request, "You don't have permission to revoke links.")
                return redirect("piece_rate_operator_links")
        else:
            denied = _require_piece_rate_editor(request)
            if denied:
                return denied
        if action == "generate":
            OperatorLink.objects.get_or_create(employee=employee)
            messages.success(request, f"Link created for {employee.name}.")
        elif action == "regenerate":
            OperatorLink.objects.filter(employee=employee).delete()
            OperatorLink.objects.create(employee=employee)
            messages.success(request, f"Link regenerated for {employee.name} — the old one no longer works.")
        elif action == "revoke":
            OperatorLink.objects.filter(employee=employee).delete()
            messages.success(request, f"Link revoked for {employee.name}.")
        return redirect("piece_rate_operator_links")

    # Same "currently working" rule the Production page's operator
    # picker uses — someone who's left shouldn't be handed (or keep) a
    # working link, even if their Employee record is still around.
    employees = list(
        Employee.objects.filter(department__name__iexact="Operator")
        .active_on(date_cls.today())
        .select_related("piece_rate_link")
        .order_by("name")
    )
    rows = []
    for employee in employees:
        link = getattr(employee, "piece_rate_link", None)
        url = qr_data_uri = None
        if link and can_manage:
            url = request.build_absolute_uri(reverse("piece_rate_operator_entry", args=[link.token]))
            qr_data_uri = _qr_data_uri(url)
        rows.append({"employee": employee, "link": link, "url": url, "qr_data_uri": qr_data_uri})

    return render(request, "piecerate/operator_links.html", {"rows": rows, "can_manage": can_manage, "can_revoke": can_revoke})




def _style_start(style):
    """The first day operators may log against this style — the
    explicit start_date if set, else the 1st of its own year/month."""
    return style.start_date or date_cls(style.year, style.month, 1)


def _day_label(d, today):
    if d == today:
        return _("today")
    if d == today - timedelta(days=1):
        return _("yesterday")
    return date_format(d, "d M")


def _group_ops_by_section(ops):
    """Operations grouped by their rate-card section, for the operator
    entry page's searchable operation picker — sorted alphabetically,
    with unsectioned operations pushed into an "Other" group at the
    end rather than sorted in among named sections."""
    groups = {}
    for op in ops:
        groups.setdefault(op.section or "Other", []).append(op)
    return [
        {"name": name, "ops": groups[name]}
        for name in sorted(groups, key=lambda s: (s == "Other", s))
    ]


def operator_icon_view(request, token, size):
    """A generated home-screen icon for one operator's link — their
    name's first letter over a color derived from their name (so two
    operators' shortcuts on the same phone/tablet look different at a
    glance), instead of every operator sharing the same plain company
    logo. Public like operator_entry_view since it's just an icon."""
    link = get_object_or_404(OperatorLink, token=token)
    name = link.employee.name or "?"
    initial = name.strip()[:1].upper() or "?"

    size = max(32, min(size, 512))
    hue = int(hashlib.md5(name.encode()).hexdigest(), 16) % 360
    color = ImageColor.getrgb(f"hsl({hue}, 45%, 38%)")

    img = Image.new("RGB", (size, size), color)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=int(size * 0.58))
    draw.text((size / 2, size / 2 + size * 0.03), initial, font=font, fill="white", anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    response = HttpResponse(buf.getvalue(), content_type="image/png")
    response["Cache-Control"] = "public, max-age=86400"
    return response


OPERATOR_LANG_COOKIE = "op_lang"


def _op_display_name(rc_op):
    """The operation's name in the active language, looked up in the
    locale catalog (locale/<lang>/LC_MESSAGES/django.po) with the
    English name as the msgid — so a name with no entry there just
    shows in English."""
    return _(rc_op.name)


def operator_language(view):
    """Runs the operator entry page in the operator's chosen language
    (?lang= to switch, remembered in a cookie so the phone keeps it) —
    activated here instead of via LocaleMiddleware so no other page in
    the app is affected. Wraps the POST too, since the flash messages
    are translated at the moment they're created."""
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        supported = dict(settings.LANGUAGES)
        requested = request.GET.get("lang")
        lang = requested if requested in supported else request.COOKIES.get(OPERATOR_LANG_COOKIE, "en")
        if lang not in supported:
            lang = "en"
        request.operator_lang = lang
        with translation.override(lang):
            response = view(request, *args, **kwargs)
        if requested in supported:
            response.set_cookie(OPERATOR_LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
        return response
    return wrapper


@operator_language
def operator_entry_view(request, token):
    """The mobile self-entry page — no login, the token in the URL IS
    the identity, so a phone with this link bookmarked stays linked to
    this one operator indefinitely (see OperatorLink). Add-only: a
    day's (operation, operator) entry can be submitted once and never
    edited or deleted from here, relying on PieceRateEntry's own
    uniqueness constraint as the backstop. Corrections happen on the
    desktop Production page, which still has full edit rights.

    There's no separate page-level date picker — the page itself always
    reflects today. Backdating happens by tapping a day directly inside
    an operation's own calendar (any date the style has actually been
    running — Style.start_date, or the 1st of its month if that isn't
    set — through today, and not already logged for that operation),
    which is what actually sets the date a submission logs against;
    re-checked server-side on submit regardless of what the calendar
    let you tap, same as every other rule here."""
    link = get_object_or_404(OperatorLink, token=token)
    employee = link.employee
    today = date_cls.today()

    styles = Style.objects.filter(
        year=today.year, month=today.month, is_template=False,
    ).prefetch_related("operations")
    style_list = list(styles)

    def own_url():
        return reverse("piece_rate_operator_entry", args=[token])

    if request.method == "POST":
        rc_op = get_object_or_404(RateCardOperation, id=request.POST.get("rate_card_operation_id"), style__in=styles)
        quantity = _parse_int(request.POST.get("quantity"))
        try:
            entry_date = date_cls.fromisoformat(request.POST.get("date", ""))
        except ValueError:
            messages.error(request, _("Pick a date in the calendar first."))
            return redirect(own_url())
        day_label = _day_label(entry_date, today)

        if entry_date > today or entry_date < _style_start(rc_op.style):
            messages.error(request, _("%(style)s wasn't running yet on %(day)s.") % {"style": rc_op.style.name, "day": day_label})
            return redirect(own_url())
        if not quantity:
            messages.error(request, _("Enter how many pieces you made."))
            return redirect(own_url())

        # Add-only past this point — except for a row an operator
        # added earlier *today*, which they can still revise without a
        # supervisor, no matter which date it's actually logged
        # against (e.g. a backdated entry for the 20th, added just
        # now, is still fixable right now). Once today ends, it locks
        # like everything else.
        existing = PieceRateEntry.objects.filter(
            rate_card_operation=rc_op, employee=employee, date=entry_date,
        ).first()
        if existing and existing.created_at.date() != today:
            messages.error(request, _("You already logged this for %(day)s — ask your supervisor if it needs to change.") % {"day": day_label})
            return redirect(own_url())

        if rc_op.order_quantity:
            other_total = PieceRateEntry.objects.filter(rate_card_operation=rc_op).exclude(
                employee=employee, date=entry_date,
            ).aggregate(total=Sum("quantity"))["total"] or 0
            if other_total + quantity > rc_op.order_quantity:
                messages.error(
                    request,
                    _("That would bring the total to %(total)s, more than the %(planned)s planned for this. Check with your supervisor.")
                    % {"total": other_total + quantity, "planned": rc_op.order_quantity},
                )
                return redirect(own_url())

        actor = f"{employee.name} (self-entry)"

        if existing:
            old_quantity = existing.quantity
            existing.quantity = quantity
            existing.entered_by = PieceRateEntry.ENTERED_BY_OPERATOR
            existing.was_operator_entered = True
            existing.operator_quantity = quantity
            existing.save(update_fields=["quantity", "entered_by", "was_operator_entered", "operator_quantity"])
            _log_piece_rate_quantity_change(actor, rc_op, employee, entry_date, old_quantity, quantity)
            messages.success(request, _("Updated %(op)s for %(day)s to %(qty)s.") % {"op": _op_display_name(rc_op), "day": day_label, "qty": quantity})
            return redirect(own_url())

        try:
            PieceRateEntry.objects.create(
                rate_card_operation=rc_op, employee=employee, date=entry_date, quantity=quantity,
                entered_by=PieceRateEntry.ENTERED_BY_OPERATOR, was_operator_entered=True,
                operator_quantity=quantity,
            )
        except IntegrityError:
            messages.error(request, _("You already logged this for %(day)s.") % {"day": day_label})
            return redirect(own_url())

        _log_piece_rate_quantity_change(actor, rc_op, employee, entry_date, 0, quantity)
        messages.success(request, _("Logged %(qty)s for %(op)s.") % {"qty": quantity, "op": _op_display_name(rc_op)})
        return redirect(own_url())

    started_styles = [s for s in style_list if today >= _style_start(s)]
    style_rows = [{"style": style, "ops": list(style.operations.all())} for style in started_styles]
    style_rows = [row for row in style_rows if row["ops"]]
    for row in style_rows:
        for op in row["ops"]:
            op.display_name = _op_display_name(op)
        row["sections"] = _group_ops_by_section(row["ops"])

    # One query for every op that needs a calendar, instead of one
    # query per operation.
    op_ids_for_calendars = {op.id for row in style_rows for op in row["ops"]}
    entries_by_op = {}
    added_today_by_op = {}
    corrected_by_op = {}
    operator_qty_by_op = {}
    for e in PieceRateEntry.objects.filter(employee=employee, rate_card_operation_id__in=op_ids_for_calendars):
        entries_by_op.setdefault(e.rate_card_operation_id, {})[e.date] = e.quantity
        if e.created_at.date() == today:
            added_today_by_op.setdefault(e.rate_card_operation_id, set()).add(e.date)
        # A supervisor changed a value the operator themselves entered
        # — surfaced here too (not just the desktop page) so an
        # operator can see their own number was corrected, and to
        # what, without having to ask.
        if e.entered_by == PieceRateEntry.ENTERED_BY_SUPERVISOR and e.was_operator_entered:
            corrected_by_op.setdefault(e.rate_card_operation_id, {})[e.date] = e.operator_quantity

    weeks_cache = {}

    def _weeks_for(style):
        # A style that started before the 1st of its own month (e.g.
        # the 20th of the prior month) needs those earlier days in the
        # grid too, so a plain single-month calendar() call won't do —
        # this snaps out to full weeks covering [style_start, month
        # end] instead, however far back that reaches.
        style_start = _style_start(style)
        num_days_in_month = calendar.monthrange(style.year, style.month)[1]
        month_start = date_cls(style.year, style.month, 1)
        month_end = date_cls(style.year, style.month, num_days_in_month)
        range_start = min(style_start, month_start)
        key = (range_start, month_end)
        if key not in weeks_cache:
            grid_start = range_start - timedelta(days=range_start.weekday())
            grid_end = month_end + timedelta(days=6 - month_end.weekday())
            all_days = [grid_start + timedelta(n) for n in range((grid_end - grid_start).days + 1)]
            weeks = [all_days[i:i + 7] for i in range(0, len(all_days), 7)]
            weeks_cache[key] = (weeks, range_start, month_end)
        return weeks_cache[key]

    def _build_calendar(op, style):
        qty_map = entries_by_op.get(op.id, {})
        added_today_dates = added_today_by_op.get(op.id, set())
        corrected_dates = corrected_by_op.get(op.id, {})
        style_start = _style_start(style)
        weeks_raw, range_start, month_end = _weeks_for(style)
        default_date = None
        weeks = []
        for week in weeks_raw:
            days = []
            for d in week:
                has_qty = d in qty_map
                added_today = d in added_today_dates
                # A logged day stays open to tapping — for an edit, not
                # a second add — only if it was itself added earlier
                # today, whatever date it's actually logged against;
                # anything added on an earlier day is add-only.
                clickable = style_start <= d <= today and (not has_qty or added_today)
                if d == today and clickable:
                    default_date = d
                days.append({
                    "date": d, "in_month": range_start <= d <= month_end, "quantity": qty_map.get(d),
                    "is_today": d == today, "clickable": clickable,
                    # Any day whose entry was added today — regardless
                    # of which date it's logged against — reads as
                    # visually distinct: it's still freely editable
                    # right now, unlike an older logged day.
                    "added_today": added_today,
                    # A supervisor changed this day's value from what
                    # the operator themselves originally entered.
                    "is_corrected": d in corrected_dates,
                    "operator_qty": corrected_dates.get(d),
                    # A grid spanning two months repeats day-of-month
                    # numbers (both months can have a 20th) — flag the
                    # earlier month's days so the template can label
                    # them (e.g. "20 Aug") instead of a bare, ambiguous
                    # "20".
                    "other_month": d.month != style.month,
                })
            weeks.append(days)
        for week in weeks:
            for day in week:
                day["is_selected"] = day["date"] == default_date
        if range_start.month == style.month:
            month_label = f"{date_format(range_start, 'F')} {style.year}"
        else:
            month_label = f"{date_format(range_start, 'M')}–{date_format(date_cls(style.year, style.month, 1), 'M')} {style.year}"
        return {
            "op_id": op.id, "weeks": weeks, "total": sum(qty_map.values()), "month_label": month_label,
            "order_quantity": op.order_quantity,
            "default_date": default_date.isoformat() if default_date else "",
            "default_qty": qty_map.get(default_date, "") if default_date else "",
        }

    logged_ops = []
    for row in style_rows:
        row["op_calendars"] = [_build_calendar(op, row["style"]) for op in row["ops"]]
        for op, cal in zip(row["ops"], row["op_calendars"]):
            if cal["total"]:
                logged_ops.append({"op": op, "style": row["style"], "total": cal["total"]})

    return render(request, "piecerate/operator_entry.html", {
        "employee": employee,
        "today": today,
        "style_rows": style_rows,
        "logged_ops": logged_ops,
        "no_styles_this_month": not style_list,
        "not_started_yet": bool(style_list) and not started_styles,
        "token": token,
        "languages": settings.LANGUAGES,
        "current_lang": request.operator_lang,
    })


