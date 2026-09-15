import calendar
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render

from .models import PAY_TYPE_CHOICES, PAY_TYPE_OPERATOR, Operation, RateCardOperation, Style
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
