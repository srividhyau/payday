from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class EmployeeQuerySet(models.QuerySet):
    def active_on(self, on_date):
        """Employees active on a single date: those with an EmploymentPeriod
        covering it, or (for employees with no periods recorded at all —
        the pre-history-tracking default) everyone, so nobody vanishes from
        Mark Attendance just because their periods were never set up."""
        no_periods = models.Q(employment_periods__isnull=True)
        covering = models.Q(employment_periods__start_date__lte=on_date) & (
            models.Q(employment_periods__end_date__isnull=True)
            | models.Q(employment_periods__end_date__gte=on_date)
        )
        return self.filter(no_periods | covering).distinct()

    def active_during(self, start_date, end_date):
        """Employees active at any point within [start_date, end_date] —
        used for the month grid, where someone active for only part of the
        month should still appear so their partial attendance is visible."""
        no_periods = models.Q(employment_periods__isnull=True)
        overlapping = models.Q(employment_periods__start_date__lte=end_date) & (
            models.Q(employment_periods__end_date__isnull=True)
            | models.Q(employment_periods__end_date__gte=start_date)
        )
        return self.filter(no_periods | overlapping).distinct()


class Employee(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    company = models.CharField(max_length=150, blank=True)
    category = models.CharField(
        max_length=100, blank=True,
        help_text="Broad category from the attendance export, e.g. Staff/Helper/Worker.",
    )
    subcategory = models.CharField(max_length=100, blank=True)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="employees"
    )
    designation = models.CharField(max_length=100, blank=True)

    ot_rate_per_hour = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Overtime pay rate for this employee, per hour.",
    )

    # Salary — basic_salary applies to everyone (Helpers/Staff/Operators use
    # it as their whole fixed salary; Company Workers combine it with da/hra
    # below). hra/da/pf_number/esi_number only apply to Company Workers, but
    # live here rather than a separate table for the same reason
    # ot_rate_per_hour does — one flat field per employee, no history.
    basic_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hra = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    da = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pf_number = models.CharField(max_length=40, blank=True)
    esi_number = models.CharField(max_length=40, blank=True)

    # Per-employee overrides for the Salary page's Company Workers
    # calculation (src/payroll.py) — most Company Workers are PF/ESI
    # eligible by default, so those default True; TDS is the exception
    # rather than the rule, so it defaults False. PF/ESI here only gate
    # whether the deduction is computed at all — ESI still separately
    # respects the statutory wage ceiling even when enabled. There's no
    # automatic TDS amount (no slab/rate logic built) — enabling it is a
    # record-keeping flag for HR; the actual figure is still entered via
    # SalaryAdjustment.deductions, same as today.
    pf_enabled = models.BooleanField(default=True)
    esi_enabled = models.BooleanField(default=True)
    tds_enabled = models.BooleanField(default=False)

    # Bank details for salary transfer — sourced from the monthly salary
    # workbook's department sheets (Op/I&B/Staff/Helpers/Company Workers),
    # not from the eSSL attendance export.
    account_name = models.CharField(max_length=150, blank=True)
    bank_name = models.CharField(max_length=150, blank=True)
    account_no = models.CharField(max_length=40, blank=True)
    ifsc_code = models.CharField(max_length=20, blank=True)
    branch = models.CharField(max_length=100, blank=True)

    objects = EmployeeQuerySet.as_manager()

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def is_active_on(self, on_date) -> bool:
        """Single-employee version of the active_on() queryset filter —
        for the odd spot that already has an Employee instance in hand and
        just needs a yes/no, rather than filtering a whole list."""
        periods = self.employment_periods.all()
        if not periods:
            return True
        return periods.filter(start_date__lte=on_date).filter(
            models.Q(end_date__isnull=True) | models.Q(end_date__gte=on_date)
        ).exists()


class EmploymentPeriod(models.Model):
    """One continuous stretch of employment for an employee — start_date to
    end_date (end_date blank = still active). Someone who takes a month off
    and rejoins gets a second period rather than reusing or deleting the
    first, so their earlier tenure's attendance/OT history stays intact and
    the exact leave/rejoin dates are preserved. Managed from the Employee
    admin page (inline)."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="employment_periods")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Leave blank if still active.")

    class Meta:
        ordering = ["employee__name", "start_date"]

    def __str__(self):
        return f"{self.employee.name}: {self.start_date} – {self.end_date or 'present'}"

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("End date can't be before the start date.")
        overlapping = EmploymentPeriod.objects.filter(employee_id=self.employee_id).exclude(pk=self.pk)
        for other in overlapping:
            other_end = other.end_date or date.max
            this_end = self.end_date or date.max
            if self.start_date <= other_end and other.start_date <= this_end:
                raise ValidationError(f"Overlaps an existing period: {other}.")


class UploadBatch(models.Model):
    """One HR upload of a DailyAttendance export file."""

    file_name = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    row_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.file_name} ({self.uploaded_at:%Y-%m-%d %H:%M})"


class AttendanceRecord(models.Model):
    STATUS_CHOICES = [
        ("P", "Present"),
        ("A", "Absent"),
        ("PH", "Paid Holiday"),
        ("CO", "Comp Off"),
        ("PL", "Personal Leave"),
        ("WO", "Week Off"),
        ("H", "Holiday"),
        ("HD", "Half Day"),
        ("EL", "Earned Leave"),
        ("PM", "Permission"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance_records")
    date = models.DateField()
    shift = models.CharField(max_length=20, blank=True)
    time_in = models.CharField(max_length=20, blank=True)
    time_out = models.CharField(max_length=20, blank=True)
    work_hours = models.FloatField(default=0)
    ot_hours = models.FloatField(default=0)
    status = models.CharField(max_length=5, choices=STATUS_CHOICES)
    batch = models.ForeignKey(
        UploadBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name="records"
    )
    # Set whenever HR corrects this row by hand (edit_record_view's
    # single-cell popup, or bulk_set_shift_view's whole-day action) —
    # never touched by the importer itself, so it stays a reliable "did
    # a person change this, not just the device export" flag for
    # Device Records to visually call out (see dashboard.html).
    manually_edited = models.BooleanField(default=False)
    # Comma-separated subset of "Punch In", "Punch Out", "Shift", "Status"
    # — which specific field(s) a hand edit actually changed, so the
    # Device Records tooltip can say e.g. "EDITED - Punch Out, Status"
    # instead of just flagging the row as edited with no detail. Grows by
    # union across repeated edits (see edit_record_view/
    # bulk_set_shift_view) rather than being overwritten each time, so a
    # field edited once keeps showing here even if a later edit only
    # touches something else.
    manually_edited_fields = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["employee__code", "date"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="unique_employee_date"),
        ]

    def __str__(self):
        return f"{self.employee.code} {self.date} {self.status}"


class SalaryAdjustment(models.Model):
    """One employee's monthly payroll adjustments — HR's input to the
    Salary page (see src/payroll.py for how these combine with attendance
    and Employee.basic_salary/hra/da into a final NET). adjust_days lets HR
    add/subtract paid days on top of what attendance shows (e.g. an
    approved Comp Off used); deductions/additions are flat amounts.
    manual_amount is only meaningful for Operators, whose pay is
    piece-rate/production-based and isn't derivable from attendance at
    all — it's entered by hand each month, same as in the source
    workbook."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="salary_adjustments")
    year = models.IntegerField()
    month = models.IntegerField()

    adjust_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    additions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    manual_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    hold = models.BooleanField(default=False, help_text="Withhold this month's pay (still computed, not paid).")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "year", "month"], name="unique_employee_salary_month"),
        ]
        ordering = ["employee__code"]

    def __str__(self):
        return f"{self.employee.code} {self.year}-{self.month:02d}"


class MonthLock(models.Model):
    """Marks one calendar month's attendance/payroll as frozen for one of
    several editable views — the Attendance dashboard's All and Missed
    Punch views, the OT page's OT View tab, and each of the Salary page's
    five tabs — each locked/unlocked independently, even though several
    of them share the same underlying data, so e.g. OT View can stay
    locked after payroll while Missed Punch remains open for corrections,
    or Company Workers salary can be finalized while Operators is still
    being entered. Locking/unlocking both require the PIN (see
    settings.ATTENDANCE_LOCK_PIN) — there's no user login in this app, so
    the PIN is the only gate. Presence of a row for (year, month, view)
    means that view is locked for that month."""

    VIEW_ALL = "all"
    VIEW_ISSUES = "issues"
    VIEW_OT = "ot"
    VIEW_SALARY_COMPANY = "salary_company"
    VIEW_SALARY_HELPER = "salary_helper"
    VIEW_SALARY_STAFF = "salary_staff"
    VIEW_SALARY_CONTRACTORS = "salary_contractors"
    VIEW_SALARY_OPERATORS = "salary_operators"
    VIEW_SALARY_FIXED_PAYMENTS = "salary_fixed_payments"
    VIEW_CHOICES = [
        (VIEW_ALL, "All"),
        (VIEW_ISSUES, "Missed Punch"),
        (VIEW_OT, "OT View"),
        (VIEW_SALARY_COMPANY, "Salary — Company Workers"),
        (VIEW_SALARY_HELPER, "Salary — Helpers"),
        (VIEW_SALARY_STAFF, "Salary — Staff"),
        (VIEW_SALARY_CONTRACTORS, "Salary — Contractors"),
        (VIEW_SALARY_OPERATORS, "Salary — Operators"),
        (VIEW_SALARY_FIXED_PAYMENTS, "Salary — Fixed Payments"),
    ]

    year = models.IntegerField()
    month = models.IntegerField()
    view = models.CharField(max_length=24, choices=VIEW_CHOICES, default=VIEW_ALL)
    locked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["year", "month", "view"], name="unique_month_view_lock"),
        ]

    def __str__(self):
        return f"{self.year}-{self.month:02d} ({self.get_view_display()}) locked"


class SpecialDay(models.Model):
    """A company-wide calendar entry — applies to every employee on that
    date, except anyone listed in downgraded_employees, who get plain
    Holiday (unpaid) instead of this date's real day_type (see
    src/metrics.apply_special_days) — set up on the /calendar/ page and
    overlaid onto attendance data before metrics are computed."""

    HOLIDAY = "H"
    PAID_HOLIDAY = "PH"
    COMP_OFF = "CO"
    TYPE_CHOICES = [
        (HOLIDAY, "Holiday"),
        (PAID_HOLIDAY, "Paid Holiday"),
        (COMP_OFF, "Comp Off"),
    ]

    date = models.DateField(unique=True)
    day_type = models.CharField(max_length=2, choices=TYPE_CHOICES)
    name = models.CharField(max_length=150, blank=True)
    # Employees downgraded to plain Holiday (unpaid) on this date instead
    # of its real day_type — e.g. a declared Paid Holiday that only some
    # employees (say, those still on probation) actually get paid for;
    # everyone else still gets the real day_type. Meaningless when
    # day_type is already "H" (nothing to downgrade from).
    downgraded_employees = models.ManyToManyField(
        "Employee", blank=True, related_name="downgraded_special_days",
    )

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.date} ({self.get_day_type_display()})"


class EarlyClosureDay(models.Model):
    """A date the whole company closes earlier than usual (e.g. a half
    day before a festival) — company-wide, like SpecialDay. closing_time
    (e.g. 14:30) is what HR actually knows and enters; full_day_hours
    (the hours between the standard 9:00 AM start and closing_time)
    replaces the standard 8.5h "what counts as a full day" baseline for
    everyone on this date, so leaving at the sanctioned earlier time
    isn't flagged as a Short Day or docked Permission Hours the way an
    ordinary shortfall would be (see
    src/metrics.is_short_hours/permission_hours_by_employee, and
    attendance/views.py's _early_closure_hours). Doesn't touch
    Holiday/Paid Holiday/Comp Off status at all — this is purely about
    the expected-hours baseline, an orthogonal concept to SpecialDay."""

    # Matches src/metrics.py's own _OT_NINE_AM — the one fixed reference
    # point every shift-based OT/lateness calculation in this app already
    # measures from, so "closes at 14:30" and "M-OT before 9:00 AM" agree
    # on what "the start of the day" means.
    STANDARD_START_HOUR = 9

    date = models.DateField(unique=True)
    closing_time = models.TimeField(
        help_text="What time the company actually closes on this date (e.g. 14:30) — "
                  "the expected full day for Short Days/Permission Hours is computed "
                  "from this minus the standard 9:00 AM start.",
    )
    note = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.date} (closes at {self.closing_time:%H:%M})"

    @property
    def full_day_hours(self) -> float:
        """Hours between the standard 9:00 AM start and this date's
        closing_time — see the class docstring."""
        minutes = (
            self.closing_time.hour * 60 + self.closing_time.minute
            - self.STANDARD_START_HOUR * 60
        )
        return round(max(minutes, 0) / 60, 2)


class CashWithdrawal(models.Model):
    """One cash withdrawal — money taken out for a fixed purpose outside
    of payroll (e.g. petty cash, office expenses, an advance), logged
    against a month as a whole (no specific day) like Salary/OT Details
    rather than tied to an employee record. Simpler and older than
    CashRegisterEntry below — kept as its own page/menu item rather than
    folded into the register, since the two serve different habits (a
    quick undated list here vs. a dated running-balance ledger there)."""

    year = models.IntegerField()
    month = models.IntegerField()
    purpose = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-year", "-month", "-id"]

    def __str__(self):
        return f"{self.year}-{self.month:02d} — {self.purpose} — {self.amount}"


class CashRegisterEntry(models.Model):
    """One line in the petty cash register — a real day-by-day cashbook:
    every Cash In (e.g. withdrawn from the bank into the office cash box)
    and Cash Out (an expense/payment made from that cash), each on its
    own date. The running/opening/closing balance is never stored — it's
    always computed as the signed sum of every entry up to a given point
    (see _cash_register_context in views.py), so it can never drift out
    of sync with the entries themselves. A month's opening balance is
    just the running total of everything before that month started,
    which is what gives the register its month-to-month carry-over: to
    seed the very first balance (before this system existed), just add
    one Cash In entry dated before your first real entry."""

    TYPE_IN = "in"
    TYPE_OUT = "out"
    TYPE_CHOICES = [
        (TYPE_IN, "Cash In"),
        (TYPE_OUT, "Cash Out"),
    ]

    date = models.DateField()
    entry_type = models.CharField(max_length=3, choices=TYPE_CHOICES)
    purpose = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "created_at", "id"]

    def __str__(self):
        sign = "+" if self.entry_type == self.TYPE_IN else "-"
        return f"{self.date} {sign}{self.amount} — {self.purpose}"


class LeaveLedgerEntry(models.Model):
    """One Staff employee's EL (Earned Leave) / Comp-Off accrual for one
    month — a monthly "closing" snapshot posted by HR (see
    leave_ledger_view), not a live-computed balance: EL's 6-day cap and
    encashment overflow are sequential (each month's credit depends on
    the running balance every prior month already posted), so the
    balance has to be carried forward row by row, the same way a real
    leave register would be closed each month.

    EL: 1 day per day the employee worked a company-wide Holiday/Paid
    Holiday/Comp Off (SpecialDay) that month — or 0.5 if that day's
    worked hours were 6 or fewer (a half day worked still counts, just
    not a full one) — see src/metrics.overtime_view's "el_day_credit"
    column (and its own _EL_FULL_DAY_HOURS constant), reused here so this
    can never disagree with what the OT page's EL Days column already
    shows for that day. Capped at EL_CAP days running
    balance; whatever would push it over that cap is "encashed" instead
    (tracked in el_encashed — this app only tracks the amount owed, HR
    pays it by hand, see leave_ledger_view). Spent EL — a day actually
    taken off — is captured the same way any other leave is: marking
    that AttendanceRecord's status as "EL" (see STATUS_CHOICES). el_taken
    counts those days for the month and is subtracted after that month's
    credit/cap/encashment is worked out, so taking leave can't itself
    change how much gets encashed; it can take the running balance
    negative if more was taken than banked, which is left visible rather
    than silently blocked (see leave_ledger_view).

    Comp-Off: hours worked beyond COMP_OFF_HOUR_THRESHOLD on any day
    that month, summed and added to a running hours balance — no cap;
    HR grants time off against this balance manually, outside this app.

    is_manual rows exist purely to seed a starting balance from before
    this ledger existed (or to hand-correct a mistake) — HR sets
    el_balance_after/comp_off_balance_after directly instead of letting
    that month's attendance compute them; every later month's posting
    still carries forward from whatever the most recent row (manual or
    computed) says the balance was, so a seeded opening balance flows
    through normally from then on."""

    EL_CAP = Decimal("6")
    COMP_OFF_HOUR_THRESHOLD = Decimal("8.5")

    employee = models.ForeignKey("Employee", on_delete=models.CASCADE, related_name="leave_ledger_entries")
    year = models.IntegerField()
    month = models.IntegerField()

    full_ot_days = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    el_credited = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    el_encashed = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    el_taken = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    el_balance_after = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    comp_off_hours_earned = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    comp_off_balance_after = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    is_manual = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "year", "month"], name="unique_leave_ledger_month"),
        ]
        ordering = ["employee__name", "year", "month"]

    def __str__(self):
        return f"{self.employee.code} {self.year}-{self.month:02d} EL={self.el_balance_after} CompOff={self.comp_off_balance_after}h"
