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
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="employees"
    )
    designation = models.CharField(max_length=100, blank=True)

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
