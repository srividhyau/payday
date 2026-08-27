from datetime import date

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
    """Marks one calendar month's attendance as frozen for one of three
    editable grids — the Attendance dashboard's All and Missed Punch
    views, plus the OT page's OT View tab — each locked/unlocked
    independently, even though they share the same underlying data, so
    e.g. OT View can stay locked after payroll while Missed Punch remains
    open for corrections. Locking/unlocking both require the PIN (see
    settings.ATTENDANCE_LOCK_PIN) — there's no user login in this app, so
    the PIN is the only gate. Presence of a row for (year, month, view)
    means that view is locked for that month."""

    VIEW_ALL = "all"
    VIEW_ISSUES = "issues"
    VIEW_OT = "ot"
    VIEW_CHOICES = [
        (VIEW_ALL, "All"),
        (VIEW_ISSUES, "Missed Punch"),
        (VIEW_OT, "OT View"),
    ]

    year = models.IntegerField()
    month = models.IntegerField()
    view = models.CharField(max_length=10, choices=VIEW_CHOICES, default=VIEW_ALL)
    locked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["year", "month", "view"], name="unique_month_view_lock"),
        ]

    def __str__(self):
        return f"{self.year}-{self.month:02d} ({self.get_view_display()}) locked"


class SpecialDay(models.Model):
    """A company-wide calendar entry — applies to every employee on that
    date. Set up on the /calendar/ page and overlaid onto attendance data
    before metrics are computed (see src/metrics.apply_special_days)."""

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

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.date} ({self.get_day_type_display()})"


class CashWithdrawal(models.Model):
    """One cash-register entry — money withdrawn for a fixed purpose
    outside of payroll (e.g. petty cash, office expenses, an advance), logged
    against a month as a whole (no specific day) like Salary/OT Details
    rather than tied to an employee record."""

    year = models.IntegerField()
    month = models.IntegerField()
    purpose = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-year", "-month", "-id"]

    def __str__(self):
        return f"{self.year}-{self.month:02d} — {self.purpose} — {self.amount}"
