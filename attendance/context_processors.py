from piecerate.permissions import can_edit_piece_rate

from .middleware import (
    EMPLOYEE_EDIT_GROUP, OPERATOR_LINK_REVOKER_GROUP, PRODUCTION_HEAD_GROUP, SALARY_VIEWER_GROUP,
)


def role_flags(request):
    """Exposes is_salary_viewer/is_employee_editor/is_production_head/
    is_piece_rate_editor to every template (via _topbar.html and
    salary.html/cash_withdrawal.html/piecerate templates) so nav links
    and edit controls a role can't use aren't shown in the first place.
    is_salary_viewer/is_employee_editor/is_production_head are
    *restricting* roles (actual enforcement is RoleRestrictionMiddleware,
    this is UX only) — is_piece_rate_editor is the opposite, a *granting*
    permission (enforced in piecerate/views.py itself), true for superusers
    too since they can always do everything."""
    user = getattr(request, "user", None)
    authenticated_non_super = bool(user and user.is_authenticated and not user.is_superuser)
    is_salary_viewer = authenticated_non_super and user.groups.filter(name=SALARY_VIEWER_GROUP).exists()
    is_employee_editor = authenticated_non_super and user.groups.filter(name=EMPLOYEE_EDIT_GROUP).exists()
    is_production_head = authenticated_non_super and user.groups.filter(name=PRODUCTION_HEAD_GROUP).exists()
    is_operator_link_revoker = (
        authenticated_non_super and user.groups.filter(name=OPERATOR_LINK_REVOKER_GROUP).exists()
    )
    return {
        "is_operator_link_revoker": is_operator_link_revoker,
        "is_salary_viewer": is_salary_viewer,
        "is_employee_editor": is_employee_editor,
        "is_production_head": is_production_head,
        "is_piece_rate_editor": can_edit_piece_rate(user),
        # Read server-side (not just client-side JS) so the very first
        # render already has the sidebar in the right state — otherwise
        # a collapsed sidebar would flash open on every single page
        # load before the client-side JS in _topbar.html caught up.
        "sidebar_collapsed": request.COOKIES.get("sidebarCollapsed") == "1",
    }
