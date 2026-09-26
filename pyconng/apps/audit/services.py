"""Recording and reading the audit trail."""

import logging

from django.contrib.contenttypes.models import ContentType

from .models import AuditEntry

logger = logging.getLogger(__name__)


def record(
    target,
    action,
    actor=None,
    actor_label="",
    old_value="",
    new_value="",
    note="",
    conference_year=None,
):
    """
    Record an action against ``target``.

    Never raises: an audit write must not be the reason a grant decision or a
    proposal submission fails. A failure is logged loudly instead, because a
    silently missing trail is worse than a noisy one.
    """
    try:
        resolved_actor = actor if getattr(actor, "pk", None) else None
        label = actor_label or (
            getattr(resolved_actor, "email", "") or getattr(resolved_actor, "username", "")
            if resolved_actor
            else "system"
        )

        if conference_year is None:
            conference_year = getattr(target, "conference_year", None)

        return AuditEntry.objects.create(
            actor=resolved_actor,
            actor_label=label or "system",
            action=action,
            old_value=str(old_value or ""),
            new_value=str(new_value or ""),
            note=note or "",
            target_type=ContentType.objects.get_for_model(target.__class__),
            target_id=str(target.pk),
            target_label=str(target)[:255],
            conference_year=conference_year,
        )
    except Exception:
        logger.exception(
            "Failed to record audit entry: action=%r target=%r", action, target,
        )
        return None


def entries_for(target, limit=None):
    """Every recorded action against one object, newest first."""
    queryset = AuditEntry.objects.filter(
        target_type=ContentType.objects.get_for_model(target.__class__),
        target_id=str(target.pk),
    ).select_related("actor")
    return queryset[:limit] if limit else queryset
