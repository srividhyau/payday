from django import forms

from .models import Employee


class UploadForm(forms.Form):
    file = forms.FileField(
        label="DailyAttendance export",
        help_text="From the eSSL fingerprint system: .xlsx, .xlsm, or .csv",
    )


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            "code", "name", "department", "company", "category", "subcategory", "designation",
            "ot_rate_per_hour", "basic_salary", "hra", "da", "pf_number", "esi_number",
            "pf_enabled", "esi_enabled", "tds_enabled",
            "payment_method", "account_name", "bank_name", "account_no", "ifsc_code", "branch",
        ]
