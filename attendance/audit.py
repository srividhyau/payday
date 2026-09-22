"""Thin write helpers for AuditLogEntry — kept out of models.py since
these are called from view code in both attendance and piecerate, not
from the model itself. See AuditLogEntry's docstring for why this is
written explicitly by each mutating view rather than via signals.
"""
from __future__ import annotations

from .models import AuditLogEntry


def log_change(
    actor: str, app_label: str, model_name: str, object_id, object_repr: str,
    field_name: str, old_value, new_value,
) -> None:
    """No-op if old == new (as strings) — every call site diffs a whole
    record's worth of fields and this is the one place that decides
    which of them actually changed, so callers can pass every field
    unconditionally without pre-filtering."""
    old_str = "" if old_value is None else str(old_value)
    new_str = "" if new_value is None else str(new_value)
    if old_str == new_str:
        return
    AuditLogEntry.objects.create(
        actor=actor or "unknown", app_label=app_label, model_name=model_name,
        object_id=str(object_id), object_repr=object_repr, field_name=field_name,
        old_value=old_str, new_value=new_str,
    )


def log_changes(
    actor: str, app_label: str, model_name: str, object_id, object_repr: str,
    changes: dict,
) -> None:
    """changes: {field_name: (old_value, new_value)}."""
    for field_name, (old_value, new_value) in changes.items():
        log_change(actor, app_label, model_name, object_id, object_repr, field_name, old_value, new_value)
