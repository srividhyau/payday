from django.urls import path

from . import views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("upload/", views.upload_view, name="upload"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("edit-record/", views.edit_record_view, name="edit_record"),
    path("bulk-set-shift/", views.bulk_set_shift_view, name="bulk_set_shift"),
    path("toggle-month-lock/", views.toggle_month_lock_view, name="toggle_month_lock"),
]
