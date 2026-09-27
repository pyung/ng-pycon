"""
The waitlist, for when a ticket type sells out.

Worth having for two reasons that are not "sell more tickets". Sell-outs are
followed by refunds and no-shows, so places do come back, and telling the people
who asked first is fairer than whoever happens to reload the page. And the list
itself is the only honest measure of unmet demand when next year's allocation is
being decided.

One field, no account needed. Somebody who has just been told the thing they
wanted is gone will not sign up to be told again later.
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from editions.current import current_year

from .models import TicketSettings, TicketType, TicketWaitlistEntry

logger = logging.getLogger(__name__)


def is_enabled(year=None):
    return TicketSettings.for_year(year or current_year()).waitlist_enabled


@transaction.atomic
def join(*, email, ticket_type=None, year=None, user=None):
    """
    Add somebody to the waitlist, or return the entry they already have.

    Returns ``(entry, created)``. Joining twice is not an error and does not reset
    their place: the queue is ordered by when they first asked.
    """
    year = year or current_year()
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("A waitlist entry needs an email address.")

    try:
        with transaction.atomic():
            entry = TicketWaitlistEntry.objects.create(
                conference_year=year,
                ticket_type=ticket_type,
                email=email,
                user=user if getattr(user, "pk", None) else None,
            )
            return entry, True
    except IntegrityError:
        # The unique constraint did its job: they are already on this list.
        existing = TicketWaitlistEntry.objects.filter(
            conference_year=year, ticket_type=ticket_type, email=email
        ).first()
        return existing, False


def waiting_for(ticket_type, year=None):
    """
    Everybody still waiting for ``ticket_type``, oldest first.

    Includes the people who asked for any type, because a specific type coming
    back satisfies them too.
    """
    year = year or (ticket_type.conference_year if ticket_type else current_year())
    return (
        TicketWaitlistEntry.objects.filter(conference_year=year)
        .filter(_type_filter(ticket_type))
        .filter(notified_at__isnull=True, converted_at__isnull=True)
        .order_by("created_at")
    )


def _type_filter(ticket_type):
    """``Q`` matching entries for this type, or for no particular type."""
    from django.db.models import Q

    if ticket_type is None:
        return Q(ticket_type__isnull=True)
    return Q(ticket_type=ticket_type) | Q(ticket_type__isnull=True)


def notify_available(ticket_type, *, limit=None, dry_run=False):
    """
    Tell the front of the queue that places have opened up.

    ``limit`` defaults to however many places are actually free, so the list is not
    told about stock that does not exist. Marks each entry notified as it goes, so
    an interrupted run does not tell the same person twice on the next one.
    """
    remaining = ticket_type.remaining_count
    if remaining <= 0:
        return []
    count = remaining if limit is None else min(limit, remaining)

    told = []
    for entry in waiting_for(ticket_type)[:count]:
        if not dry_run:
            _notify(entry, ticket_type)
            entry.notified_at = timezone.now()
            entry.save(update_fields=["notified_at"])
        told.append(entry)
    return told


def _notify(entry, ticket_type):
    from emails.services import send_email, site_url

    return send_email(
        template="tickets/waitlist_available",
        to=[entry.email],
        subject=f"A PyCon Nigeria {entry.conference_year} ticket is available",
        context={
            "ticket_type": ticket_type.name,
            "conference_year": entry.conference_year,
            "purchase_url": f"{site_url()}/tickets/purchase/",
            # Said plainly, because it is true and because it stops a complaint
            # later: being told is not a reservation.
            "first_come": True,
        },
        tags=["tickets", "waitlist"],
        conference_year=entry.conference_year,
    )


def mark_converted(email, year=None):
    """
    Note that somebody on the list went on to buy.

    Called after a successful purchase so the list shrinks on its own, and so the
    unmet-demand figure is not inflated by people who were satisfied.
    """
    year = year or current_year()
    return TicketWaitlistEntry.objects.filter(
        conference_year=year,
        email__iexact=(email or "").strip(),
        converted_at__isnull=True,
    ).update(converted_at=timezone.now())
