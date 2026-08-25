from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.home_view, name="home"),
    path("upload/", views.upload_view, name="upload"),
    path("mark-attendance/", views.mark_attendance_view, name="mark_attendance"),
    path("mark-attendance/month/", views.mark_attendance_month_view, name="mark_attendance_month"),
    path("mark-attendance/set-status/", views.set_attendance_status_view, name="set_attendance_status"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("reports/ot-details/", views.ot_details_view, name="ot_details"),
    path("reports/ot-details/download/", views.ot_details_download_view, name="ot_details_download"),
    path(
        "reports/ot-details/download-grid/", views.ot_details_download_grid_view,
        name="ot_details_download_grid",
    ),
    path("calendar/", views.calendar_view, name="calendar"),
    path("edit-record/", views.edit_record_view, name="edit_record"),
    path("bulk-set-shift/", views.bulk_set_shift_view, name="bulk_set_shift"),
    path("toggle-month-lock/", views.toggle_month_lock_view, name="toggle_month_lock"),
    path("mark-attendance/send-telegram-report/", views.send_telegram_report_view, name="send_telegram_report"),
    path(
        "mark-attendance/send-day-report/", views.send_day_attendance_report_view,
        name="send_day_attendance_report",
    ),
]
