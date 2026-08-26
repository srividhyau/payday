from datetime import date

from django.contrib import admin

from .models import (
    AttendanceRecord, Department, Employee, EmploymentPeriod, MonthLock, SalaryAdjustment, SpecialDay,
    UploadBatch,
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


class EmploymentPeriodInline(admin.TabularInline):
    model = EmploymentPeriod
    extra = 0
    # Leave-and-rejoin is add-a-row (new start_date) or edit-end_date, not
    # something to build from scratch every time — so the most recent
    # period is right there without scrolling.
    ordering = ("-start_date",)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "code", "name", "department", "category", "subcategory", "company", "designation",
        "ot_rate_per_hour", "basic_salary", "hra", "da", "pf_number", "esi_number",
        "pf_enabled", "esi_enabled", "tds_enabled", "is_currently_active",
        "account_name", "bank_name", "account_no", "ifsc_code", "branch",
    )
    list_filter = ("department", "category", "subcategory", "pf_enabled", "esi_enabled", "tds_enabled")
    list_editable = ("pf_enabled", "esi_enabled", "tds_enabled")
    search_fields = ("code", "name", "account_no", "ifsc_code")
    inlines = [EmploymentPeriodInline]

    @admin.display(boolean=True, description="Active")
    def is_currently_active(self, obj):
        return obj.is_active_on(date.today())


@admin.register(EmploymentPeriod)
class EmploymentPeriodAdmin(admin.ModelAdmin):
    list_display = ("employee", "start_date", "end_date")
    list_filter = ("employee__department",)
    search_fields = ("employee__code", "employee__name")
    date_hierarchy = "start_date"


@admin.register(UploadBatch)
class UploadBatchAdmin(admin.ModelAdmin):
    list_display = ("file_name", "uploaded_at", "period_start", "period_end", "row_count")
    readonly_fields = ("uploaded_at",)


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "date", "status", "work_hours", "ot_hours", "batch")
    list_filter = ("status", "employee__department", "batch")
    search_fields = ("employee__code", "employee__name")
    date_hierarchy = "date"


@admin.register(SpecialDay)
class SpecialDayAdmin(admin.ModelAdmin):
    list_display = ("date", "day_type", "name")
    list_filter = ("day_type",)
    date_hierarchy = "date"


@admin.register(MonthLock)
class MonthLockAdmin(admin.ModelAdmin):
    list_display = ("year", "month", "locked_at")
    list_filter = ("year",)


@admin.register(SalaryAdjustment)
class SalaryAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        "employee", "year", "month", "adjust_days", "deductions", "additions", "manual_amount", "hold",
    )
    list_filter = ("year", "month", "hold", "employee__department", "employee__subcategory")
    search_fields = ("employee__code", "employee__name")
