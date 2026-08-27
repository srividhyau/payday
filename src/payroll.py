"""Payroll calculations for the Salary page — pure functions over plain
numbers, no Django/DB access, same separation as metrics.py/parser.py.
attendance/views.py gathers the inputs (attendance-derived paid days,
Employee.basic_salary/hra/da, SalaryAdjustment) and calls these.

Formulas were reverse-engineered from the source payroll workbook (a
monthly "<Month> Salary.xlsm") and verified against real rows — see each
function's docstring for the exact numbers checked.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

# Statutory ESI wage ceiling (India) — gross pay at or under this is
# ESI-eligible, above it is exempt. Flag/adjust if your actual threshold
# differs; this is the standard figure at time of writing, not fetched
# from any live source.
ESI_WAGE_CEILING = Decimal("21000")
PF_RATE = Decimal("0.12")
# PF Employer Contribution, empirically reverse-engineered (this workbook
# doesn't use the standard 12%-of-Basic+DA split into EPF/EPS) — verified
# exactly against two real rows: 2213.833333/12299.074074 == 0.18 and
# 2169.556667/12053.092593 == 0.18, both against Earned Total Wages, not
# Earned Basic+DA.
PF_EMPLOYER_RATE = Decimal("0.18")
ESI_EMPLOYEE_RATE = Decimal("0.0075")
ESI_EMPLOYER_RATE = Decimal("0.0325")


def working_days_in_month(year: int, month: int) -> int:
    """Calendar days in the month minus Sundays (the only fixed weekly
    off) — verified against a real month: 31 calendar days, 4 Sundays ->
    27, matching the workbook's "Total Days" column exactly.

    Holiday/Paid Holiday/Comp Off (attendance.models.SpecialDay) do NOT
    reduce this denominator — they're compensated days, so they instead
    add to the paid-days *numerator* (see the paid-days query in
    attendance.views.salary_view, which counts P/PH/CO on any date), same
    as the workbook's own "Total Paid Days = Working Days + Paid Holiday +
    Comp Off" measured against a fixed Sundays-only "Total Days"."""
    _, days_in_month = calendar.monthrange(year, month)
    sundays = sum(1 for d in range(1, days_in_month + 1) if date(year, month, d).weekday() == 6)
    return days_in_month - sundays


def compute_company_worker_pay(
    basic_salary: Decimal, hra: Decimal, da: Decimal,
    paid_days: Decimal, working_days: int,
    adjust_days: Decimal = Decimal(0), deductions: Decimal = Decimal(0), additions: Decimal = Decimal(0),
    pf_enabled: bool = True, esi_enabled: bool = True,
) -> dict:
    """Company Workers: Basic+DA and HRA are each prorated by earned days
    over working days, then PF (12% of earned Basic+DA) and ESI (0.75% of
    earned total, only if GROSS is at/under the ESI wage ceiling) are
    deducted — unless pf_enabled/esi_enabled (Employee.pf_enabled/
    esi_enabled — some employees are exempt) turn one off outright; the
    wage ceiling still applies on top of esi_enabled, it can't force ESI
    on above the statutory cap. Verified against a real payroll row:
    Basic 5000, DA 5496, HRA 2787, 25/27 paid days -> earned_basic_da
    9718.52, earned_hra 2580.56, pf 1166.22, esi 92.24, net 11041 (all
    matched exactly, to the rupee, before the final round)."""
    basic_da = basic_salary + da
    gross = basic_da + hra
    earned_days = paid_days + adjust_days
    ratio = (earned_days / working_days) if working_days else Decimal(0)
    earned_basic_da = basic_da * ratio
    earned_hra = hra * ratio
    earned_total = earned_basic_da + earned_hra
    pf = PF_RATE * earned_basic_da if pf_enabled else Decimal(0)
    pf_employer = PF_EMPLOYER_RATE * earned_total if pf_enabled else Decimal(0)
    esi_eligible = esi_enabled and gross <= ESI_WAGE_CEILING
    esi = ESI_EMPLOYEE_RATE * earned_total if esi_eligible else Decimal(0)
    esi_employer = ESI_EMPLOYER_RATE * earned_total if esi_eligible else Decimal(0)
    # Round PF/ESI first, then sum the rounded figures for every total
    # that includes them (PF+ESI, Total Deduction, NET) — so each matches
    # what you'd get by hand-adding the displayed cells, rather than a
    # penny off from summing pre-rounding.
    pf_r, pf_employer_r = round(pf, 2), round(pf_employer, 2)
    esi_r, esi_employer_r = round(esi, 2), round(esi_employer, 2)
    total_deduction = pf_r + esi_r + deductions
    net = earned_total + additions - total_deduction
    return {
        "basic_da": round(basic_da, 2),
        "gross": round(gross, 2),
        "earned_days": round(earned_days, 2),
        "earned_basic_da": round(earned_basic_da, 2),
        "earned_hra": round(earned_hra, 2),
        "earned_total": round(earned_total, 2),
        "pf": pf_r,
        "pf_employer": pf_employer_r,
        "esi": esi_r,
        "esi_employer": esi_employer_r,
        "pf_esi_employee": pf_r + esi_r,
        "pf_esi_employer": pf_employer_r + esi_employer_r,
        "total_deduction": round(total_deduction, 2),
        "net": round(net, 2),
    }


def compute_prorated_pay(
    basic_salary: Decimal, paid_days: Decimal, working_days: int,
    adjust_days: Decimal = Decimal(0), deductions: Decimal = Decimal(0), additions: Decimal = Decimal(0),
) -> dict:
    """Helpers and Staff: a single fixed salary prorated by earned days
    over working days, plus flat deductions/additions. Verified against a
    real Helpers row: FIXED SALARY 11123, 6/27 paid days -> earned 2472
    exactly."""
    earned_days = paid_days + adjust_days
    ratio = (earned_days / working_days) if working_days else Decimal(0)
    earned_salary = basic_salary * ratio
    net = earned_salary + additions - deductions
    return {"earned_salary": round(earned_salary, 2), "net": round(net, 2)}


def compute_operator_pay(
    manual_amount: Decimal | None, deductions: Decimal = Decimal(0), additions: Decimal = Decimal(0),
) -> dict:
    """Operators: pay is piece-rate/production-based, entered by hand each
    month (not derivable from attendance at all) — this just combines it
    with flat deductions/additions."""
    net = (manual_amount or Decimal(0)) + additions - deductions
    return {"net": round(net, 2)}
