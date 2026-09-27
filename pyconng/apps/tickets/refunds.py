"""
Refunds: asked for, decided, paid.

Three separate moments with their own timestamps, because in practice they are
days apart and "we agreed to refund you" is not "the money has left". Conflating
them is how a refund gets approved in a meeting and never paid.

Paystack has a refund API and it is wired in, but the record here does not depend
on it: refunds get made by bank transfer often enough that a system which can only
record an API-driven one records nothing.
"""

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.services import record

from .models import Refund, Ticket, TicketSettings

logger = logging.getLogger(__name__)


class RefundError(Exception):
    """A refund could not be recorded, with a reason worth showing somebody."""


#: What each refund_state means, for a page that has to explain itself.
STATE_MESSAGES = {
    "already_refunded": "This order has already been refunded.",
    "not_paid": "This order has not been paid, so there is nothing to refund.",
    "complimentary": "This ticket was issued rather than bought, so there is nothing to refund.",
    "nothing_to_refund": "Everything paid on this order has already been refunded.",
    "no_policy": "Refunds are not offered for this edition.",
    "deadline_passed": "The refund deadline for this edition has passed.",
    "refundable": "",
}


@transaction.atomic
def request_refund(ticket, *, amount=None, reason, requested_by=None, force=False):
    """
    Record a refund request against ``ticket``.

    ``force`` skips the policy check, for an organizer making an exception --
    which happens, and is better recorded as a deliberate override than done by
    editing rows. Without it, a request outside the policy is refused with the
    reason.
    """
    settings_obj = TicketSettings.for_year(ticket.conference_year)
    state = ticket.refund_state(settings_obj)
    if state != "refundable" and not force:
        raise RefundError(STATE_MESSAGES.get(state, "This order cannot be refunded."))

    amount = Decimal(amount) if amount is not None else ticket.suggested_refund(settings_obj)
    if amount <= 0:
        raise RefundError("A refund needs an amount above zero.")
    if amount > ticket.refundable_amount:
        raise RefundError(
            f"{amount} is more than the {ticket.refundable_amount} still refundable "
            f"on this order."
        )

    refund = Refund.objects.create(
        ticket=ticket,
        amount=amount,
        reason=reason,
        requested_by=requested_by if getattr(requested_by, "pk", None) else None,
    )
    record(
        target=ticket,
        action="Refund requested",
        actor=requested_by,
        new_value=str(amount),
        note=f"{ticket.order}: {reason[:150]}",
        conference_year=ticket.conference_year,
    )
    return refund


@transaction.atomic
def decide_refund(refund, *, approve, decided_by=None, note=""):
    """Approve or reject a request. Does not move money; ``mark_paid`` does that."""
    if refund.status in (Refund.PAID,):
        raise RefundError("This refund has already been paid.")
    previous = refund.get_status_display()
    refund.status = Refund.APPROVED if approve else Refund.REJECTED
    refund.decided_by = decided_by if getattr(decided_by, "pk", None) else None
    if note:
        refund.note = note
    refund.save()

    record(
        target=refund.ticket,
        action="Refund decided",
        actor=decided_by,
        old_value=previous,
        new_value=refund.get_status_display(),
        note=f"{refund.ticket.order}: {refund.amount}",
        conference_year=refund.ticket.conference_year,
    )
    return refund


@transaction.atomic
def mark_paid(refund, *, provider_reference="", paid_by=None, release_place=True):
    """
    Record that the money has gone back, and release the place.

    ``release_place`` moves the order to REFUNDED, which is what stops it counting
    against the ticket type's allocation. Turned off for a partial refund, where
    the attendee is still coming.
    """
    if refund.status == Refund.REJECTED:
        raise RefundError("A rejected refund cannot be marked paid.")

    refund.status = Refund.PAID
    refund.provider_reference = provider_reference or refund.provider_reference
    refund.save()

    ticket = refund.ticket
    if release_place:
        ticket.status = Ticket.REFUNDED
        ticket.save(update_fields=["status"])

    record(
        target=ticket,
        action="Refund paid",
        actor=paid_by,
        new_value=str(refund.amount),
        note=(
            f"{ticket.order}: {refund.amount}"
            + (f", reference {refund.provider_reference}" if refund.provider_reference else "")
            + ("; place released" if release_place else "; place kept")
        ),
        conference_year=ticket.conference_year,
    )
    _notify(refund)
    return refund


def _notify(refund):
    """Tell the buyer the money is on its way back."""
    from emails.services import send_email

    ticket = refund.ticket
    if not ticket.user or not ticket.user.email:
        return False
    return send_email(
        template="tickets/refunded",
        to=[ticket.user.email],
        subject=f"Your PyCon Nigeria {ticket.conference_year} refund",
        context={
            "user_name": ticket.user.get_full_name() or ticket.user.email,
            "order_code": ticket.order,
            "amount": refund.amount,
            "conference_year": ticket.conference_year,
            "reference": refund.provider_reference,
        },
        tags=["tickets", "refund"],
        conference_year=ticket.conference_year,
    )


def refund_through_paystack(refund):
    """
    Ask Paystack to reverse the original charge.

    Kept separate from ``mark_paid`` on purpose: the API call can fail, and a
    failure must not leave the record saying the money went back. The caller marks
    it paid only once this returns a reference.
    """
    from .services import PaystackService

    ticket = refund.ticket
    if not ticket.paystack_reference:
        raise RefundError(
            "This order has no Paystack reference, so it was not paid through "
            "Paystack. Refund it by transfer and record the reference by hand."
        )
    result = PaystackService().refund_transaction(
        ticket.paystack_reference, amount=refund.amount
    )
    if not result:
        raise RefundError(
            "Paystack did not accept the refund. Check the dashboard before retrying."
        )
    return result["reference"]
