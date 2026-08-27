from .middleware import SALARY_VIEWER_GROUP


def role_flags(request):
    """Exposes is_salary_viewer to every template (via _topbar.html and
    salary.html/cash_withdrawal.html) so nav links and edit controls this
    role can't use aren't shown in the first place — the actual
    enforcement is SalaryViewerRestrictionMiddleware; this is UX only."""
    user = getattr(request, "user", None)
    is_salary_viewer = bool(
        user and user.is_authenticated and not user.is_superuser
        and user.groups.filter(name=SALARY_VIEWER_GROUP).exists()
    )
    return {"is_salary_viewer": is_salary_viewer}
