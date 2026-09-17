from django.contrib import messages
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

# Group names for the restricted roles — each created by its own migration
# (0015_salary_viewer_group, 0016_employee_edit_group). See _ROLE_ACCESS
# below for what membership in each actually allows; enforcement lives in
# RoleRestrictionMiddleware.
SALARY_VIEWER_GROUP = "Salary Viewer"
EMPLOYEE_EDIT_GROUP = "Employee Edit"
PRODUCTION_HEAD_GROUP = "Production Head"

# Every URL name under the Piece Rate module — Styles/Templates/Master
# Operations/the summaries (view-only, no POST branch) plus each style's
# Rate Card and Production pages (real POST actions). piece_rate_operator_entry
# is the public mobile self-entry page — normally reached signed out
# entirely (anonymous requests never hit this middleware at all), listed
# here only so a Production Head who's also logged into their own
# account doesn't get redirected away from it.
_PIECE_RATE_GET_URLS = {
    "piece_rate", "piece_rate_templates", "piece_rate_operations",
    "piece_rate_operator_summary", "piece_rate_style_summary", "piece_rate_management_summary",
    "piece_rate_rate_card", "piece_rate_production", "piece_rate_production_shortcut",
    "piece_rate_operator_links", "piece_rate_operator_entry", "piece_rate_operator_icon",
}
_PIECE_RATE_POST_URLS = {
    "piece_rate", "piece_rate_templates", "piece_rate_operations",
    "piece_rate_rate_card", "piece_rate_production",
    "piece_rate_operator_links", "piece_rate_operator_entry",
}

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
    # Confined to Piece Rate and nothing else — but full read/write
    # across the whole module (see piecerate.permissions.can_edit_piece_rate,
    # which also treats this group as a Piece Rate Editor so Master
    # Operations/Templates edits aren't separately gated for them).
    PRODUCTION_HEAD_GROUP: {
        "get": _PIECE_RATE_GET_URLS | {"login", "logout"},
        "post": _PIECE_RATE_POST_URLS | {"login", "logout"},
    },
}

# Where an over-reach redirects to, per role — the first page that role is
# actually allowed to see.
_ROLE_HOME = {
    SALARY_VIEWER_GROUP: "salary",
    EMPLOYEE_EDIT_GROUP: "employee_list",
    PRODUCTION_HEAD_GROUP: "piece_rate",
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
