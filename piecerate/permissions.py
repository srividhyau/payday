from attendance.middleware import OPERATOR_LINK_REVOKER_GROUP, PRODUCTION_HEAD_GROUP

PIECE_RATE_EDITOR_GROUP = "Piece Rate Editor"


def can_edit_piece_rate(user):
    """Superusers, members of the "Piece Rate Editor" group, and members
    of "Production Head" (who are confined to Piece Rate entirely — see
    attendance.middleware — but get full read/write within it) may add,
    edit, delete or reorder Master Operations and Templates. Everyone
    else with general app access can still view both pages, just not
    change them — Styles (the month-scoped working rate cards) is
    unaffected by this and stays open to anyone with general access."""
    if not (user and user.is_authenticated):
        return False
    return bool(
        user.is_superuser
        or user.groups.filter(name__in=[PIECE_RATE_EDITOR_GROUP, PRODUCTION_HEAD_GROUP]).exists()
    )


def can_revoke_operator_links(user):
    """Superusers and members of "Operator Link Revoker" — deliberately
    not everyone who can edit Piece Rate, so revoking stays a separate,
    explicitly granted permission (an editor who isn't in the group can
    generate/regenerate links but never sees a Revoke button)."""
    if not (user and user.is_authenticated):
        return False
    return bool(user.is_superuser or user.groups.filter(name=OPERATOR_LINK_REVOKER_GROUP).exists())
