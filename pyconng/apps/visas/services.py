"""
What happens to a visa letter request: submit, decide, issue, purge.

Every function here writes to the audit trail, including the ones that only *read*.
That is unusual and deliberate: the read of a full passport number is itself the
event worth recording, because the question somebody will eventually ask is not
"what did we store" but "who looked at it".
"""

import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from audit.services import record
from editions.current import current_year

from . import eligibility
from .models import VisaLetterRequest, VisaSettings, generate_reference

logger = logging.getLogger(__name__)


class VisaError(Exception):
    """A refusal with a reason worth showing somebody."""


def request_for(user, year=None):
    """This person's request for the edition, or None."""
    if user is None or not user.is_authenticated:
        return None
    return VisaLetterRequest.objects.filter(
        user=user, conference_year=year or current_year()
    ).first()


@transaction.atomic
def submit(request_obj, *, by=None):
    """
    Move a request into the queue, recording the basis as it stands today.

    The basis is re-read here rather than taken from the form: what the letter will
    claim about this person has to come from their records, not their typing.
    """
    if not request_obj.can_edit:
        raise VisaError("This request has already been decided.")

    assessment = eligibility.assess(request_obj.user, request_obj.conference_year)
    if not assessment.ok:
        raise VisaError(assessment.reason)

    first_time = request_obj.status != VisaLetterRequest.STATUS_SUBMITTED
    request_obj.basis = assessment.basis
    request_obj.status = VisaLetterRequest.STATUS_SUBMITTED
    if first_time or not request_obj.submitted_at:
        request_obj.submitted_at = timezone.now()
    request_obj.save()

    record(
        target=request_obj,
        action="Submitted visa letter request" if first_time else "Updated visa letter request",
        actor=by or request_obj.user,
        new_value=request_obj.status,
        note=f"Basis: {assessment.basis_label}",
        conference_year=request_obj.conference_year,
    )
    if first_time:
        _notify(request_obj, "received")
    return request_obj


@transaction.atomic
def approve(request_obj, *, by=None, note="", notify=True):
    """
    Approve a request and issue its reference.

    The basis is checked again, because the gap between submission and decision is
    where a refunded ticket or a lapsed grant offer turns a true statement on our
    letterhead into a false one.
    """
    if request_obj.status == VisaLetterRequest.STATUS_APPROVED:
        return request_obj
    if request_obj.status not in (
        VisaLetterRequest.STATUS_SUBMITTED, VisaLetterRequest.STATUS_REJECTED
    ):
        raise VisaError("Only a submitted request can be approved.")

    assessment = eligibility.assess(request_obj.user, request_obj.conference_year)
    if not assessment.ok:
        raise VisaError(
            "This applicant is no longer eligible: "
            f"{assessment.reason} Decide the underlying record first."
        )

    previous = request_obj.status
    request_obj.basis = assessment.basis
    request_obj.status = VisaLetterRequest.STATUS_APPROVED
    request_obj.decision_note = note
    request_obj.decided_at = timezone.now()
    request_obj.decided_by = by if getattr(by, "pk", None) else None
    if not request_obj.reference:
        request_obj.reference = generate_reference(request_obj.conference_year)
    request_obj.letter_issued_at = timezone.now()
    request_obj.save()

    record(
        target=request_obj,
        action="Approved visa letter request",
        actor=by,
        old_value=previous,
        new_value=request_obj.status,
        note=f"Reference {request_obj.reference}, basis {assessment.basis_label}",
        conference_year=request_obj.conference_year,
    )
    if notify:
        _notify(request_obj, "approved")
    return request_obj


@transaction.atomic
def reject(request_obj, *, by=None, note="", notify=True):
    """
    Refuse a request. A reason is required.

    Not optional, because the applicant can often fix the problem -- a passport
    that expires too soon, a name that does not match their account -- and a
    refusal with no reason turns a solvable problem into a dead end.
    """
    if not note or not note.strip():
        raise VisaError(
            "Say why. The applicant can often fix the problem, and a bare refusal "
            "leaves them with nothing to act on."
        )
    if request_obj.status not in (
        VisaLetterRequest.STATUS_SUBMITTED, VisaLetterRequest.STATUS_APPROVED
    ):
        raise VisaError("Only a submitted request can be rejected.")

    previous = request_obj.status
    request_obj.status = VisaLetterRequest.STATUS_REJECTED
    request_obj.decision_note = note.strip()
    request_obj.decided_at = timezone.now()
    request_obj.decided_by = by if getattr(by, "pk", None) else None
    # The reference is kept, not cleared: if a letter was already issued and is now
    # withdrawn, an embassy checking that reference must be told it is not valid.
    request_obj.letter_issued_at = None
    request_obj.save()

    record(
        target=request_obj,
        action="Rejected visa letter request",
        actor=by,
        old_value=previous,
        new_value=request_obj.status,
        note=note.strip()[:500],
        conference_year=request_obj.conference_year,
    )
    if notify:
        _notify(request_obj, "rejected")
    return request_obj


@transaction.atomic
def withdraw(request_obj, *, by=None):
    """The applicant takes it back, usually because they are no longer coming."""
    if not request_obj.can_withdraw:
        raise VisaError("This request can no longer be withdrawn.")
    previous = request_obj.status
    request_obj.status = VisaLetterRequest.STATUS_WITHDRAWN
    request_obj.save(update_fields=["status", "updated_at"])
    record(
        target=request_obj,
        action="Withdrew visa letter request",
        actor=by or request_obj.user,
        old_value=previous,
        new_value=request_obj.status,
        conference_year=request_obj.conference_year,
    )
    return request_obj


def reveal_passport_number(request_obj, *, by, reason=""):
    """
    The full passport number, with the read written to the audit trail first.

    Recorded before it is returned, so an exception on the way out cannot leave a
    read unlogged. The number itself is never written to the trail -- that would
    copy the sensitive value into a second, longer-lived place, which is the
    opposite of the point.
    """
    record(
        target=request_obj,
        action="Viewed full passport number",
        actor=by,
        note=(reason or "Reviewing a visa letter request")[:500],
        conference_year=request_obj.conference_year,
    )
    return request_obj.passport_number


def letter_bytes(request_obj, *, by=None, count=True):
    """
    Generate the letter, recording who took a copy.

    Downloads are counted rather than merely permitted: an applicant fetching the
    same letter eleven times is usually somebody whose embassy has rejected the
    format, which is worth knowing before the next twenty people hit it.
    """
    from . import letter

    data = letter.build(request_obj)
    if count:
        VisaLetterRequest.objects.filter(pk=request_obj.pk).update(
            letter_downloads=F("letter_downloads") + 1
        )
    record(
        target=request_obj,
        action="Downloaded visa invitation letter",
        actor=by,
        note=f"Reference {request_obj.reference}",
        conference_year=request_obj.conference_year,
    )
    return data


def verify(reference):
    """
    Resolve a reference for the public verification page.

    Returns the request when the reference names a letter that is currently valid,
    and None otherwise -- including for a letter that was issued and later
    withdrawn, which is the case the endpoint exists for. Deliberately gives the
    same answer for "never existed" and "no longer valid" only in its return value;
    the view distinguishes them, because an embassy needs to know which it is.
    """
    if not reference:
        return None
    return VisaLetterRequest.objects.filter(
        reference=reference.strip().upper()
    ).first()


def queue(year=None, status=None):
    """The officer's queue: oldest waiting first, because that is the risk."""
    year = year or current_year()
    qs = (
        VisaLetterRequest.objects.for_year(year)
        .exclude(status=VisaLetterRequest.STATUS_DRAFT)
        .select_related("user", "decided_by")
    )
    if status:
        qs = qs.filter(status=status)
    return qs.order_by("submitted_at")


def overview(year=None):
    """Counts for the queue header, and the two numbers that mean act now."""
    year = year or current_year()
    qs = VisaLetterRequest.objects.for_year(year).exclude(
        status=VisaLetterRequest.STATUS_DRAFT
    )
    pending = qs.filter(status=VisaLetterRequest.STATUS_SUBMITTED)
    settings_obj = VisaSettings.for_year(year)
    waiting = [r for r in pending if (r.days_waiting or 0) >= settings_obj.processing_days]
    return {
        "year": year,
        "settings": settings_obj,
        "total": qs.count(),
        "pending": pending.count(),
        "approved": qs.filter(status=VisaLetterRequest.STATUS_APPROVED).count(),
        "rejected": qs.filter(status=VisaLetterRequest.STATUS_REJECTED).count(),
        "withdrawn": qs.filter(status=VisaLetterRequest.STATUS_WITHDRAWN).count(),
        # Past the turnaround we promised on the form, which is the number that
        # should make somebody uncomfortable.
        "overdue": len(waiting),
        "holding_details": qs.holding_details().count(),
        "purge_due_on": settings_obj.purge_on_or_after(),
    }


def _notify(request_obj, template):
    """Tell the applicant. Never raises: email is not the record."""
    from .emails import send_visa_email

    try:
        send_visa_email(request_obj, template)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Visa letter email %s failed for request %s", template, request_obj.pk
        )
