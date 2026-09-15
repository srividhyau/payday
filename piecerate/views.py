import calendar
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from attendance.models import Employee, SpecialDay

from .models import PAY_TYPE_CHOICES, PAY_TYPE_OPERATOR, Operation, PieceRateEntry, RateCardOperation, Style
from .permissions import can_edit_piece_rate


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
                Style.objects.create(name=name, year=year, month=month)
                messages.success(request, f'Style "{name}" created.')
            return redirect(redirect_url)
        if action == "duplicate_template":
            template = get_object_or_404(Style, id=request.POST.get("template_id"), is_template=True)
            new_name = _unique_name(f"{template.name} {calendar.month_abbr[month]} {year}")
            new_style = Style.objects.create(name=new_name, year=year, month=month)
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
                style.save()
                messages.success(request, f'Style renamed to "{name}".')
            return redirect(redirect_url)
        if action == "delete_style":
            Style.objects.filter(id=request.POST.get("style_id")).delete()
            messages.success(request, "Style deleted.")
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

        if action.startswith("delete_"):
            op_id = action[len("delete_"):]
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
            updated = style.operations.update(order_quantity=qty)
            messages.success(request, f"Order Qty set to {qty} on {updated} operation(s).")
            return redirect("piece_rate_rate_card", style_id=style.id)

        if action == "create" or action.startswith("edit_"):
            suffix = "" if action == "create" else f"_{action[len('edit_'):]}"
            if suffix:
                op = get_object_or_404(RateCardOperation, id=action[len("edit_"):], style=style)
            else:
                op = RateCardOperation(style=style)
                last_code = RateCardOperation.objects.filter(style=style).count()
                op.op_code = last_code + 1

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


@login_required
def production_view(request, style_id):
    """Per-style daily production entry. One grid, scoped to the style's
    own month: every operator who has logged against an operation gets a
    row, one column per day of that month. An operation's order_quantity
    (set on the Rate Card page) is the total every operator's entries for
    it should sum to across the month — op_total vs order_quantity is
    what drives the match/mismatch coloring client-side expects.

    A cell save is a single day's (operation, operator) quantity — POST
    here is AJAX-only (see the fetch call in production.html), not a
    full-page form submit, since the grid can have far too many cells
    for one big multi-row submit to be practical."""
    style = get_object_or_404(Style, id=style_id, is_template=False)

    if request.method == "POST":
        rc_op = get_object_or_404(RateCardOperation, id=request.POST.get("rate_card_operation_id"), style=style)
        employee = get_object_or_404(Employee, id=request.POST.get("employee_id"))

        if request.POST.get("action") == "remove_operator":
            PieceRateEntry.objects.filter(rate_card_operation=rc_op, employee=employee).delete()
            return JsonResponse({"ok": True})

        day = _parse_int(request.POST.get("day"))
        quantity = _parse_int(request.POST.get("quantity"))
        num_days = calendar.monthrange(style.year, style.month)[1]
        if day < 1 or day > num_days:
            return JsonResponse({"ok": False, "error": "Invalid day."}, status=400)

        entry_date = date_cls(style.year, style.month, day)

        if rc_op.order_quantity:
            other_total = PieceRateEntry.objects.filter(rate_card_operation=rc_op).exclude(
                employee=employee, date=entry_date
            ).aggregate(total=Sum("quantity"))["total"] or 0
            hypothetical = other_total + quantity
            if hypothetical > rc_op.order_quantity:
                return JsonResponse({
                    "ok": False,
                    "error": (
                        f"This would bring the total to {hypothetical}, which exceeds "
                        f"the Order Qty of {rc_op.order_quantity}. Reduce the quantity."
                    ),
                }, status=400)

        if quantity > 0:
            PieceRateEntry.objects.update_or_create(
                rate_card_operation=rc_op, employee=employee, date=entry_date,
                defaults={"quantity": quantity},
            )
        else:
            PieceRateEntry.objects.filter(rate_card_operation=rc_op, employee=employee, date=entry_date).delete()

        op_total = PieceRateEntry.objects.filter(rate_card_operation=rc_op).aggregate(total=Sum("quantity"))["total"] or 0
        return JsonResponse({"ok": True, "op_total": op_total})

    num_days = calendar.monthrange(style.year, style.month)[1]
    days = list(range(1, num_days + 1))
    day_headers = [
        {"day": d, "dow": date_cls(style.year, style.month, d).strftime("%a")}
        for d in days
    ]

    # Same Holiday/Paid Holiday/Comp Off calendar (and colors) as the
    # Attendance dashboard, so a day already marked off there reads the
    # same way here.
    special_days = {
        sd.date.day: sd.day_type
        for sd in SpecialDay.objects.filter(date__year=style.year, date__month=style.month)
    }

    rc_ops = list(style.operations.all())
    all_employees = list(Employee.objects.filter(department__name__iexact="Operator").order_by("name"))
    employees_by_id = {e.id: e for e in all_employees}

    entries_by_op = {}
    for e in PieceRateEntry.objects.filter(rate_card_operation__in=rc_ops):
        entries_by_op.setdefault(e.rate_card_operation_id, {}).setdefault(e.employee_id, {})[e.date.day] = e.quantity

    op_rows = []
    for op in rc_ops:
        op_entries = entries_by_op.get(op.id, {})
        rows = []
        for emp_id, day_map in op_entries.items():
            employee = employees_by_id.get(emp_id)
            if not employee:
                continue
            rows.append({"employee": employee, "days": day_map, "total": sum(day_map.values())})
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

    return render(request, "piecerate/production.html", {
        "style": style,
        "days": days,
        "op_rows": op_rows,
        "special_days": special_days,
        "day_headers": day_headers,
    })


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
        for style_data in emp_data["styles"].values():
            op_list = []
            style_total = Decimal("0")
            for op_bucket in style_data["ops"].values():
                amount = (op_bucket["rate"] * op_bucket["quantity"]).quantize(Decimal("0.01"))
                style_total += amount
                diff_class = _qty_diff_class(op_bucket["quantity"], op_bucket["order_quantity"])
                op_list.append({**op_bucket, "amount": amount, "diff_class": diff_class})
            op_list.sort(key=lambda o: o["op_name"])
            style_list.append({"style_name": style_data["style_name"], "ops": op_list, "total": style_total})
            employee_total += style_total
        style_list.sort(key=lambda s: s["style_name"])
        operator_rows.append({"employee": emp_data["employee"], "styles": style_list, "total": employee_total})
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
        for emp_data in style_data["employees"].values():
            op_list = []
            employee_total = Decimal("0")
            for op_bucket in emp_data["ops"].values():
                amount = (op_bucket["rate"] * op_bucket["quantity"]).quantize(Decimal("0.01"))
                employee_total += amount
                diff_class = _qty_diff_class(op_bucket["quantity"], op_bucket["order_quantity"])
                op_list.append({**op_bucket, "amount": amount, "diff_class": diff_class})
            op_list.sort(key=lambda o: o["op_name"])
            employee_list.append({"employee": emp_data["employee"], "ops": op_list, "total": employee_total})
            style_total += employee_total
        employee_list.sort(key=lambda e: e["employee"].name)
        style_rows.append({"style_name": style_data["style_name"], "employees": employee_list, "total": style_total})
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

        style_rows.append({
            "style": style, "ops": op_rows, "planned_total": style_planned, "actual_total": style_actual,
            "order_quantity": style_order_qty, "rate_sum": style_rate,
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
