"""
Deleting passport details once they cannot be needed again.

Retention is the part of data protection that is always agreed and rarely
implemented, because nothing breaks when it does not happen. So it is a function
with tests and a management command rather than a paragraph in a policy.

What goes: the passport number, the date of birth, the passport expiry. What stays:
the reference, the decision, who made it, the name on the letter and the audit
trail. An embassy may check a reference months later, and the record of who approved
what is the thing an audit exists for -- neither needs the passport number.
"""

import logging

from audit.services import record
from editions.current import current_year

from .models import VisaLetterRequest, VisaSettings

logger = logging.getLogger(__name__)


def due(year=None):
    """
    Requests whose details are past their retention date, as a queryset.

    Empty until the conference is over and the retention window has passed, which
    is why the command is safe to run daily from the moment the module ships.
    """
    year = year or current_year()
    settings_obj = VisaSettings.for_year(year)
    purge_from = settings_obj.purge_on_or_after()
    if purge_from is None:
        # No dates on the edition, so "after the conference" has no meaning yet.
        return VisaLetterRequest.objects.none()
    from django.utils import timezone

    if timezone.localdate() < purge_from:
        return VisaLetterRequest.objects.none()
    return VisaLetterRequest.objects.for_year(year).holding_details()


def purge_year(year=None, *, by=None, force=False, dry_run=False):
    """
    Delete the details for ``year``. Returns how many requests were changed.

    ``force`` ignores the retention date, for an officer who wants the data gone
    now. It still records who did it: deleting personal data early is the right
    instinct, and it is also an action somebody may need to explain.
    """
    year = year or current_year()
    targets = (
        VisaLetterRequest.objects.for_year(year).holding_details()
        if force
        else due(year)
    )
    targets = list(targets.select_related("user"))
    if dry_run:
        return len(targets)

    purged = 0
    for request_obj in targets:
        if request_obj.purge_details():
            purged += 1
            record(
                target=request_obj,
                action="Deleted passport details",
                actor=by,
                actor_label="" if getattr(by, "pk", None) else "retention schedule",
                note=(
                    "Manual deletion by the visa team"
                    if force and by is not None
                    else "Retention period elapsed"
                ),
                conference_year=year,
            )
    if purged:
        logger.info("Purged passport details for %s visa request(s) in %s", purged, year)
    return purged
