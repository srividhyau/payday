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
    """Only members of "Operator Link Revoker" — strictly the group, so
    revoking is a separate, explicitly granted permission that not even
    a superuser or Piece Rate Editor has by default (they can
    generate/regenerate links but see no Revoke button until added to
    the group)."""
    if not (user and user.is_authenticated):
        return False
    return user.groups.filter(name=OPERATOR_LINK_REVOKER_GROUP).exists()
