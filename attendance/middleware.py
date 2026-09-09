from django.contrib import messages
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

# Group names for the restricted roles — each created by its own migration
# (0015_salary_viewer_group, 0016_employee_edit_group). See _ROLE_ACCESS
# below for what membership in each actually allows; enforcement lives in
# RoleRestrictionMiddleware.
SALARY_VIEWER_GROUP = "Salary Viewer"
EMPLOYEE_EDIT_GROUP = "Employee Edit"

# Deny-by-default per role: for each restricted group, the URL names it
# may GET at all, and — of those — the ones it may also POST to. Anything
# not listed is blocked for that role regardless of method, so a new view
# added later is safe unless someone deliberately adds it here. A user in
# more than one restricted group gets the union of what each allows.
_ROLE_ACCESS = {
    SALARY_VIEWER_GROUP: {
        "get": {
            "salary", "salary_download", "salary_bank_download",
            "cash_withdrawal", "cash_withdrawal_download",
            "cash_register", "cash_register_download",
            "login", "logout",
        },
        # None of the payroll pages themselves, so viewing never doubles
        # as an edit path; login/logout obviously need POST to function.
        "post": {"login", "logout"},
    },
    EMPLOYEE_EDIT_GROUP: {
        "get": {"employee_list", "employee_create", "employee_edit", "login", "logout"},
        "post": {"employee_create", "employee_edit", "login", "logout"},
    },
}

# Where an over-reach redirects to, per role — the first page that role is
# actually allowed to see.
_ROLE_HOME = {
    SALARY_VIEWER_GROUP: "salary",
    EMPLOYEE_EDIT_GROUP: "employee_list",
}


class RoleRestrictionMiddleware:
    """Enforces every restricted role in _ROLE_ACCESS: a user in one or
    more of these groups may only GET the URL names their role(s) allow,
    and POST only where additionally allowed — everything else is denied
    regardless of method. Superusers are never restricted, so there's
    always a way to manage this from the admin/shell even if a group is
    misconfigured. A user in no restricted group at all is unaffected
    (full access), same as before this middleware covered more than one
    role. Must sit after AuthenticationMiddleware (needs request.user)
    and after MessageMiddleware (uses messages.error) in MIDDLEWARE."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and not user.is_superuser:
            role_names = set(user.groups.values_list("name", flat=True)) & _ROLE_ACCESS.keys()
            if role_names:
                allowed_get = set().union(*(_ROLE_ACCESS[r]["get"] for r in role_names))
                allowed_post = set().union(*(_ROLE_ACCESS[r]["post"] for r in role_names))
                try:
                    url_name = resolve(request.path_info).url_name
                except Resolver404:
                    url_name = None
                allowed = url_name in allowed_get and (
                    request.method != "POST" or url_name in allowed_post
                )
                if not allowed:
                    messages.error(request, "Your account doesn't have access to that page.")
                    home = _ROLE_HOME[sorted(role_names)[0]]
                    return redirect(home)
        return self.get_response(request)
