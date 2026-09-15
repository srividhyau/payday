PIECE_RATE_EDITOR_GROUP = "Piece Rate Editor"


def can_edit_piece_rate(user):
    """Superusers and members of the "Piece Rate Editor" group may add,
    edit, delete or reorder Master Operations and Templates. Everyone
    else with general app access can still view both pages, just not
    change them — Styles (the month-scoped working rate cards) is
    unaffected by this and stays open to anyone with general access."""
    return bool(user and user.is_authenticated and (user.is_superuser or user.groups.filter(name=PIECE_RATE_EDITOR_GROUP).exists()))
