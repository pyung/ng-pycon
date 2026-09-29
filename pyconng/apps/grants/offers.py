"""
The offer lifecycle: approved, then accepted, declined, or lapsed.

An award used to jump straight from approved to paid, which left two holes. A
recipient had no way to say yes or no, so nobody knew whether they were coming until
the money moved; and a grant somebody had quietly given up on held its budget for
ever, because nothing ever released it.

So an approval is an *offer* with a deadline. Accepting it issues the ticket and
fixes the money in place. Declining or lapsing releases it and -- if the edition is
set up for it -- offers it to the best-scored waitlisted applicant it covers, which
is the automatic promotion the requirement asks for.

Every transition writes to the audit trail. These decisions move money, which is the
case the audit requirement was written for.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.services import record
from editions.current import current_year

from . import budget
from .models import GrantSettings, TravelGrantApplication, TravelGrantPayment

logger = logging.getLogger(__name__)


class OfferError(Exception):
    """A transition was refused, with a reason worth showing somebody."""


def deadline_for(year=None, from_time=None):
    """
    When an offer made now would lapse, or None if this edition's never do.

    Read from the settings rather than hard-coded, because how long people get is a
    judgement that changes with how close the conference is.
    """
    settings_obj = GrantSettings.objects.filter(
        conference_year=year or current_year()
    ).first()
    days = settings_obj.acceptance_days if settings_obj else 14
    if not days or days <= 0:
        return None
    return (from_time or timezone.now()) + timedelta(days=days)


@transaction.atomic
def make_offer(application, amount, *, by=None, note="", notify=True, promoted=False):
    """
    Approve a grant as an offer with a deadline. Returns the application.

    Refuses anything the budget cannot cover -- this is the enforcement the cap
    never had. The payment row is created here so finance can see what is coming,
    but it stays pending until the recipient accepts: paying somebody who has not
    said yes is how money goes out to people who never arrive.
    """
    amount = budget.assert_award(application, amount, application.conference_year)

    previous = application.status
    application.status = TravelGrantApplication.STATUS_APPROVED
    application.approved_amount = amount
    application.decision_at = timezone.now()
    application.acceptance_deadline = deadline_for(application.conference_year)
    application.accepted_at = None
    application.declined_at = None
    if note:
        application.decision_note = note
    if promoted:
        application.promoted_from_waitlist_at = timezone.now()
    application.save()

    TravelGrantPayment.objects.update_or_create(
        application=application,
        defaults={
            "amount_paid": amount,
            "payment_status": TravelGrantPayment.STATUS_PENDING,
        },
    )

    record(
        target=application,
        action="Travel grant offered" + (" (promoted from waitlist)" if promoted else ""),
        actor=by,
        old_value=previous,
        new_value=application.status,
        note=(
            f"Amount: {amount}"
            + (
                f"; accept by {application.acceptance_deadline:%Y-%m-%d}"
                if application.acceptance_deadline
                else "; no acceptance deadline"
            )
        ),
        conference_year=application.conference_year,
    )

    if notify:
        _notify(
            application,
            "grants/promoted" if promoted else None,
        )
    return application


@transaction.atomic
def accept(application, *, by=None):
    """
    The recipient says yes. Fixes the money and issues their ticket.

    Refuses a passed deadline: the budget has very likely been offered onwards by
    then, and honouring a late acceptance would overspend quietly, which is the exact
    failure this module was rebuilt to prevent.
    """
    if application.status == TravelGrantApplication.STATUS_ACCEPTED:
        return application
    if not application.awaiting_acceptance:
        raise OfferError(
            f"This grant is {application.get_status_display().lower()}, so there is "
            f"nothing to accept."
        )
    if application.offer_has_expired:
        raise OfferError(
            "The deadline for accepting this grant has passed. Please write to us "
            "— we may be able to help, but the budget may already be committed."
        )

    previous = application.status
    application.status = TravelGrantApplication.STATUS_ACCEPTED
    application.accepted_at = timezone.now()
    application.save()

    record(
        target=application,
        action="Travel grant accepted by applicant",
        actor=by or application.user,
        old_value=previous,
        new_value=application.status,
        note=f"Amount: {application.approved_amount}",
        conference_year=application.conference_year,
    )

    _issue_ticket(application, by)
    _notify(application, "grants/accepted_confirmation")
    return application


@transaction.atomic
def decline(application, *, by=None, reason="", promote=True):
    """
    The recipient says no. Releases the money, and offers it onwards.

    Allowed after the deadline on purpose: somebody telling us late that they cannot
    come is doing us a favour, and refusing the message because a timer ran out
    would be perverse.
    """
    if not application.can_decline:
        raise OfferError(
            f"This grant is {application.get_status_display().lower()}, so there is "
            f"nothing to decline."
        )

    previous = application.status
    freed = application.committed_amount
    application.status = TravelGrantApplication.STATUS_DECLINED
    application.declined_at = timezone.now()
    if reason:
        application.decline_reason = reason
    application.save()

    TravelGrantPayment.objects.filter(
        application=application, payment_status=TravelGrantPayment.STATUS_PENDING
    ).delete()

    record(
        target=application,
        action="Travel grant declined by applicant",
        actor=by or application.user,
        old_value=previous,
        new_value=application.status,
        note=f"Released: {freed}" + (f"; reason: {reason[:120]}" if reason else ""),
        conference_year=application.conference_year,
    )

    promoted = promote_from_waitlist(application.conference_year, by=by) if promote else []
    return application, promoted


@transaction.atomic
def lapse(application, *, by=None, promote=True):
    """
    Nobody answered in time. Releases the money the same way a decline does.

    A separate status from declined because the two mean different things to whoever
    reads this later: one person made a choice, the other never saw the email.
    """
    if not application.offer_has_expired:
        raise OfferError("This offer has not expired.")

    previous = application.status
    freed = application.committed_amount
    application.status = TravelGrantApplication.STATUS_LAPSED
    application.save()

    TravelGrantPayment.objects.filter(
        application=application, payment_status=TravelGrantPayment.STATUS_PENDING
    ).delete()

    record(
        target=application,
        action="Travel grant lapsed, not accepted in time",
        actor=by,
        actor_label="" if getattr(by, "pk", None) else "automation",
        old_value=previous,
        new_value=application.status,
        note=f"Released: {freed}",
        conference_year=application.conference_year,
    )

    _notify(application, "grants/lapsed")
    promoted = promote_from_waitlist(application.conference_year, by=by) if promote else []
    return application, promoted


def expire_overdue(year=None, *, by=None, dry_run=False, promote=True):
    """
    Lapse every offer whose deadline has passed. Returns the applications lapsed.

    Idempotent, so the command behind it is safe to run on a schedule and safe to
    run again after it is interrupted.
    """
    year = year or current_year()
    overdue = [
        application
        for application in TravelGrantApplication.objects.filter(
            conference_year=year,
            status__in=TravelGrantApplication.AWAITING_ACCEPTANCE_STATUSES,
            acceptance_deadline__isnull=False,
        ).select_related("user")
        if application.offer_has_expired
    ]
    if dry_run:
        return overdue, []

    lapsed, promoted = [], []
    for application in overdue:
        # Promotion is held until the end so the freed budget is pooled: lapsing
        # three small offers may fund one larger applicant that none of them could.
        _, _ = lapse(application, by=by, promote=False)
        lapsed.append(application)
    if promote and lapsed:
        promoted = promote_from_waitlist(year, by=by)
    return lapsed, promoted


@transaction.atomic
def promote_from_waitlist(year=None, *, by=None, limit=None, notify=True):
    """
    Turn freed budget into offers for the best-scored waitlisted applicants.

    Returns the applications promoted, which is empty when the edition has switched
    automatic promotion off, when nothing is waitlisted, or when what is left does
    not cover anybody. Promotion is deliberately not silent: the applicant gets the
    same offer email as anybody else, with its own deadline.
    """
    year = year or current_year()
    settings_obj = GrantSettings.objects.filter(conference_year=year).first()
    if settings_obj is not None and not settings_obj.auto_promote_waitlist:
        return []

    promoted = []
    for candidate in budget.affordable_waitlisted(year, limit=limit):
        application = candidate["application"]
        try:
            make_offer(
                application,
                candidate["amount"],
                by=by,
                notify=notify,
                promoted=True,
            )
        except budget.BudgetError:
            # The budget moved between building the list and acting on it. Stop
            # rather than skipping: the next candidate is more expensive.
            logger.info(
                "Stopped promoting from the waitlist for %s: budget exhausted.", year
            )
            break
        promoted.append(application)
    return promoted


def _issue_ticket(application, by):
    """
    Issue the free ticket, now that there is an acceptance to trigger it.

    This is what makes module 4's auto-issue requirement fire on the decision rather
    than waiting for a scheduled command. Never raises: the acceptance has happened
    and must stand, so a ticketing problem is logged for an organizer instead.
    """
    try:
        from tickets.issuing import REASON_GRANT, issue_complimentary_ticket

        ticket, created = issue_complimentary_ticket(
            user=application.user,
            year=application.conference_year,
            reason=REASON_GRANT,
            issued_by=by,
            # The acceptance confirmation mentions the ticket, so a second email
            # saying the same thing is noise.
            notify=False,
        )
        if created:
            logger.info(
                "Issued grant ticket %s to %s", ticket.order, application.user.email
            )
        return ticket
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not issue a grant ticket for %s; acceptance stands.",
            application.user.email,
        )
        return None


def grant_ticket(application):
    """The complimentary ticket issued for this grant, if there is one."""
    try:
        from tickets.issuing import REASON_GRANT, already_issued

        return already_issued(
            application.user, application.conference_year, REASON_GRANT
        )
    except Exception:  # noqa: BLE001
        return None


def _notify(application, template=None):
    """
    Tell the applicant. Falls back to the existing decision email for a plain offer,
    so the wording an organizer may already have overridden in the admin is used.
    """
    from .emails import send_grant_decision

    if template is None:
        send_grant_decision(application)
        return True

    from emails.services import send_email, site_url

    user = application.user
    if not user or not user.email:
        return False

    subjects = {
        "grants/promoted": "PyCon Nigeria Travel Grant — A place has opened up",
        "grants/accepted_confirmation": "PyCon Nigeria Travel Grant — Confirmed",
        "grants/lapsed": "PyCon Nigeria Travel Grant — Offer expired",
    }
    return send_email(
        template=template,
        to=[user.email],
        subject=subjects.get(template, "PyCon Nigeria Travel Grant"),
        context={
            "applicant_name": user.get_full_name() or user.email,
            "conference_year": application.conference_year,
            "amount": application.approved_amount,
            "deadline": application.acceptance_deadline,
            "days_left": application.days_left_to_accept,
            "dashboard_url": f"{site_url()}/grants/my-application/",
        },
        tags=["grants", template.rsplit("/", 1)[-1]],
        conference_year=application.conference_year,
        fail_silently=True,
    )
