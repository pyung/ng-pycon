"""
Getting the money to the recipient, and evidence that it arrived.

Three gaps this closes. The recipient had no way to tell us where to send the money,
so bank details travelled by email. There was a receipt field but only finance could
put anything in it, which is the wrong way round for a reimbursement -- the person
with the receipt is the person who paid. And a payment could be marked paid but never
checked against what the receipt actually said, so "paid" and "accounted for" were
the same state.
"""

import logging

from django.db import transaction
from django.utils import timezone

from audit.services import record

from .models import TravelGrantApplication, TravelGrantPayment

logger = logging.getLogger(__name__)


class PayoutError(Exception):
    """Something could not be done, with a reason worth showing somebody."""


#: The fields an applicant fills in. Listed once so the form, the view and the audit
#: note cannot drift apart about what counts as payout details.
PAYOUT_FIELDS = (
    "payout_method",
    "bank_name",
    "account_name",
    "account_number",
    "payout_notes",
)


@transaction.atomic
def save_payout_details(application, data, *, by=None):
    """
    Record where to send the money.

    The audit note deliberately says only that details were provided or changed, never
    the details themselves: the trail is readable by anyone with admin access and an
    account number is not something to scatter through it.
    """
    changed = []
    for field in PAYOUT_FIELDS:
        if field not in data:
            continue
        new = (data.get(field) or "").strip()
        if new != (getattr(application, field) or ""):
            changed.append(field)
        setattr(application, field, new)
    application.save(update_fields=list(PAYOUT_FIELDS) + ["updated_at"])

    if changed:
        record(
            target=application,
            action="Travel grant payout details updated",
            actor=by or application.user,
            note=f"Fields changed: {', '.join(changed)}. Values are not recorded here.",
            conference_year=application.conference_year,
        )
    return application


def payment_for(application):
    """The payment row for an awarded grant, created on demand."""
    if not application.is_awarded:
        return None
    payment, _ = TravelGrantPayment.objects.get_or_create(
        application=application,
        defaults={
            "amount_paid": application.approved_amount,
            "payment_status": TravelGrantPayment.STATUS_PENDING,
        },
    )
    return payment


@transaction.atomic
def upload_receipt(application, receipt_file, *, by=None):
    """
    Attach a receipt, from either side.

    Allowed for the recipient, which it was not before. A reimbursement's receipt
    belongs to the person who paid, and making finance the only route meant it arrived
    as an email attachment somebody had to remember to file.
    """
    payment = payment_for(application)
    if payment is None:
        raise PayoutError(
            "This grant has not been accepted yet, so there is nothing to claim against."
        )
    if payment.is_verified:
        raise PayoutError(
            "This payment has already been verified. Write to us if the receipt needs "
            "correcting."
        )

    payment.receipt = receipt_file
    payment.receipt_uploaded_at = timezone.now()
    payment.save(update_fields=["receipt", "receipt_uploaded_at", "updated_at"])

    record(
        target=application,
        action="Travel grant receipt uploaded",
        actor=by or application.user,
        new_value=payment.receipt.name.rsplit("/", 1)[-1],
        conference_year=application.conference_year,
    )
    return payment


@transaction.atomic
def mark_paid(application, *, amount=None, reference="", by=None, notify=True):
    """Record that the money has gone out, and tell the recipient."""
    payment = payment_for(application)
    if payment is None:
        raise PayoutError("Only an accepted grant can be paid.")

    previous = payment.get_payment_status_display()
    payment.payment_status = TravelGrantPayment.STATUS_PAID
    payment.amount_paid = amount if amount is not None else (
        payment.amount_paid or application.approved_amount
    )
    if reference:
        payment.reference = reference
    payment.paid_at = payment.paid_at or timezone.now()
    payment.save()

    if application.status != TravelGrantApplication.STATUS_PAID:
        application.status = TravelGrantApplication.STATUS_PAID
        application.save(update_fields=["status", "updated_at"])

    record(
        target=application,
        action="Travel grant paid",
        actor=by,
        old_value=previous,
        new_value=payment.get_payment_status_display(),
        note=f"Amount: {payment.amount_paid}"
        + (f"; reference {payment.reference}" if payment.reference else ""),
        conference_year=application.conference_year,
    )

    if notify:
        from .emails import send_grant_payment_sent

        send_grant_payment_sent(application, payment)
    return payment


@transaction.atomic
def verify_receipt(application, *, by=None, note=""):
    """
    Confirm the receipt matches what was paid.

    A separate state from paid, because they are separate facts: money leaving an
    account and money being accounted for are the two halves of a reimbursement, and
    collapsing them means the second never happens.
    """
    payment = payment_for(application)
    if payment is None:
        raise PayoutError("Only an accepted grant can be verified.")
    if not payment.is_paid:
        raise PayoutError("Mark the payment as paid before verifying its receipt.")
    if not payment.receipt:
        raise PayoutError(
            "There is no receipt to verify. Ask the recipient to upload one."
        )

    previous = payment.get_payment_status_display()
    payment.payment_status = TravelGrantPayment.STATUS_VERIFIED
    payment.receipt_verified_at = timezone.now()
    payment.receipt_verified_by = by if getattr(by, "pk", None) else None
    payment.save()

    record(
        target=application,
        action="Travel grant receipt verified",
        actor=by,
        old_value=previous,
        new_value=payment.get_payment_status_display(),
        note=note or f"Amount: {payment.amount_paid}",
        conference_year=application.conference_year,
    )
    return payment


def outstanding_receipts(year):
    """
    Reimbursements paid but not yet evidenced, oldest first.

    The list finance chases. Direct payments are left out: their paper trail is on our
    side, and asking a recipient to evidence a transaction they never handled is
    asking for something they cannot give.
    """
    return (
        TravelGrantPayment.objects.filter(
            application__conference_year=year,
            payment_type=TravelGrantPayment.PAYMENT_TYPE_REIMBURSEMENT,
            payment_status=TravelGrantPayment.STATUS_PAID,
            receipt="",
        )
        .select_related("application", "application__user")
        .order_by("paid_at")
    )


def awaiting_payout_details(year):
    """Accepted recipients who have not told us where to send the money."""
    return (
        TravelGrantApplication.objects.filter(
            conference_year=year,
            status__in=(
                TravelGrantApplication.STATUS_ACCEPTED,
                TravelGrantApplication.STATUS_PAID,
            ),
        )
        .filter(account_number="")
        .select_related("user")
        .order_by("accepted_at")
    )
