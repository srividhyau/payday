from django.contrib import admin

from .models import AttendanceRecord, Department, Employee, MonthLock, SpecialDay, UploadBatch


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "code", "name", "department", "category", "subcategory", "company", "designation",
        "ot_rate_per_hour",
        "account_name", "bank_name", "account_no", "ifsc_code", "branch",
    )
    list_filter = ("department", "category")
    search_fields = ("code", "name", "account_no", "ifsc_code")


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
