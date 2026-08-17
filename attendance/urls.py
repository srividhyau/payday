from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.home_view, name="home"),
    path("upload/", views.upload_view, name="upload"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("edit-record/", views.edit_record_view, name="edit_record"),
    path("bulk-set-shift/", views.bulk_set_shift_view, name="bulk_set_shift"),
    path("toggle-month-lock/", views.toggle_month_lock_view, name="toggle_month_lock"),
]
