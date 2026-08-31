from django.contrib import messages
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

# Group name for the restricted, view-only role — created by migration
# 0015_salary_viewer_group. A user in this group can only ever see the
# Salary and Cash Withdrawal pages, and only GET them (no editing,
# locking, or Telegram sends) — see SalaryViewerRestrictionMiddleware.
SALARY_VIEWER_GROUP = "Salary Viewer"

# URL names this role may GET at all. Deny-by-default: anything not
# listed here (including every Attendance/OT/Calendar page, and every
# shared editing endpoint like edit_record/toggle_month_lock) is blocked
# for this role regardless of method, so a new view added later is safe
# unless someone deliberately adds it here.
_ALLOWED_GET_URL_NAMES = {
    "salary", "salary_download", "salary_bank_download",
    "cash_withdrawal", "cash_withdrawal_download",
    "cash_register", "cash_register_download",
    "login", "logout",
}
# Of those, the ones this role may also POST to — none of the payroll
# pages themselves, so viewing never doubles as an edit path; login/
# logout obviously need POST to function at all.
_ALLOWED_POST_URL_NAMES = {"login", "logout"}


class SalaryViewerRestrictionMiddleware:
    """Enforces the "Salary Viewer" role: view (GET) only Salary and Cash
    Withdrawal — including their Excel/Bank Excel downloads — nothing
    else, and no POST to anything except login/logout. Superusers are
    never restricted, so there's always a way to manage this from the
    admin/shell even if the group is misconfigured. Must sit after
    AuthenticationMiddleware (needs request.user) and after
    MessageMiddleware (uses messages.error) in MIDDLEWARE."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and not user.is_superuser:
            if user.groups.filter(name=SALARY_VIEWER_GROUP).exists():
                try:
                    url_name = resolve(request.path_info).url_name
                except Resolver404:
                    url_name = None
                allowed = url_name in _ALLOWED_GET_URL_NAMES and (
                    request.method != "POST" or url_name in _ALLOWED_POST_URL_NAMES
                )
                if not allowed:
                    messages.error(request, "Your account can only view Salary and Cash Withdrawal.")
                    return redirect("salary")
        return self.get_response(request)
