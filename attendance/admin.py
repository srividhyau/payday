from django.contrib import admin

from .models import AttendanceRecord, Department, Employee, UploadBatch


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "department", "designation")
    list_filter = ("department",)
    search_fields = ("code", "name")


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
