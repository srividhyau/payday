from django import forms


class UploadForm(forms.Form):
    file = forms.FileField(
        label="DailyAttendance export",
        help_text="From the eSSL fingerprint system: .xlsx, .xlsm, or .csv",
    )
