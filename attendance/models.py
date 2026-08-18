from django.db import models


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


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

    # Bank details for salary transfer — sourced from the monthly salary
    # workbook's department sheets (Op/I&B/Staff/Helpers/Company Workers),
    # not from the eSSL attendance export.
    account_name = models.CharField(max_length=150, blank=True)
    bank_name = models.CharField(max_length=150, blank=True)
    account_no = models.CharField(max_length=40, blank=True)
    ifsc_code = models.CharField(max_length=20, blank=True)
    branch = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


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


class MonthLock(models.Model):
    """Marks one calendar month's attendance as frozen for one of the
    dashboard's three views (All/Missed Punch/OT View) — each view is
    locked/unlocked independently, even though they share the same
    underlying grid, so e.g. OT View can stay locked after payroll while
    Missed Punch remains open for corrections. Locking/unlocking both
    require the PIN (see settings.ATTENDANCE_LOCK_PIN) — there's no user
    login in this app, so the PIN is the only gate. Presence of a row for
    (year, month, view) means that view is locked for that month."""

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
