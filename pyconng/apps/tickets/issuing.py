"""
Issuing a ticket to somebody who should not pay for one.

Four kinds of person get in free, and every one of them was previously a manual
job: a speaker whose talk was accepted, a volunteer on the rota, a travel-grant
recipient, and whoever a sponsor sends. Doing that by hand for a hundred people is
how somebody arrives at a conference they were invited to and finds no ticket.

The rule throughout is that eligibility is read from the records that already
prove it -- a confirmed proposal, an approved grant, a role assignment, a sponsor's
allocation -- rather than from a second list that has to be kept in step. And every
run is idempotent: issuing twice for the same person and reason is a no-op, so the
command is safe to run again after it is interrupted, which it will be.
"""

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.services import record
from editions.current import current_year

from .models import Ticket, TicketSale, TicketType
from .utils import generate_order_code

logger = logging.getLogger(__name__)

#: The reasons a ticket is issued rather than sold. Stored on the ticket so a
#: complimentary ticket can always be explained, and used as the idempotency key.
REASON_SPEAKER = "speaker"
REASON_VOLUNTEER = "volunteer"
REASON_GRANT = "grant recipient"
REASON_SPONSOR = "sponsor allocation"
REASON_ORGANIZER = "organizer"
REASONS = (
    REASON_SPEAKER,
    REASON_VOLUNTEER,
    REASON_GRANT,
    REASON_SPONSOR,
    REASON_ORGANIZER,
)


class IssueError(Exception):
    """A ticket could not be issued, with a reason worth showing somebody."""


def complimentary_type(year, name="Complimentary"):
    """
    The invitation-only ticket type free tickets are issued against.

    Created on demand rather than required as setup: the first speaker acceptance
    should not fail because nobody made a ticket type. Priced at zero and marked
    invitation-only, so it never appears on the purchase page.
    """
    ticket_type, created = TicketType.objects.get_or_create(
        name=name,
        conference_year=year,
        defaults={
            "description": "Issued by the organizers. Not for sale.",
            "price": Decimal("0"),
            "early_bird_price": Decimal("0"),
            "early_bird_count": 0,
            "regular_count": 0,
            "availability": TicketType.INVITE,
            "is_active": True,
            "display_order": 999,
        },
    )
    if created:
        logger.info("Created the %s ticket type for %s.", name, year)
    return ticket_type


def already_issued(user, year, reason):
    """The existing complimentary ticket for this person and reason, if any."""
    return (
        Ticket.objects.filter(
            user=user,
            conference_year=year,
            complimentary_reason=reason,
        )
        .exclude(status=Ticket.CANCELLED)
        .first()
    )


@transaction.atomic
def issue_complimentary_ticket(
    *,
    user,
    year=None,
    reason,
    ticket_type=None,
    issued_by=None,
    sponsor=None,
    full_name=None,
    notify=True,
):
    """
    Give ``user`` a paid-up ticket for nothing, and name the place.

    Returns ``(ticket, created)``. Already having one for this reason returns it
    untouched -- the automation runs repeatedly and must not hand anybody a second
    ticket, nor bump a sponsor's claimed count twice.

    The ticket is created already PAID, because there is nothing to pay: an ISSUED
    complimentary ticket would sit in the purchase flow waiting for a payment that
    will never come, and would not count as admitting anybody.
    """
    if reason not in REASONS:
        raise IssueError(f"{reason!r} is not one of the known reasons: {', '.join(REASONS)}")
    if user is None or not getattr(user, "pk", None):
        raise IssueError("A complimentary ticket needs an account to belong to.")

    year = year or current_year()
    existing = already_issued(user, year, reason)
    if existing is not None:
        return existing, False

    ticket_type = ticket_type or complimentary_type(year)
    now = timezone.now()
    ticket = Ticket.objects.create(
        order=generate_order_code(),
        user=user,
        ticket_type=ticket_type,
        quantity=1,
        amount=Decimal("0"),
        total_amount=Decimal("0"),
        status=Ticket.PAID,
        date_paid=now,
        conference_year=year,
        complimentary_reason=reason,
        issued_by=issued_by if getattr(issued_by, "pk", None) else None,
        issued_for_sponsor=sponsor,
        created_tickets=True,
    )
    TicketSale.objects.create(
        ticket=ticket,
        user=user,
        attendee_email=user.email or "",
        full_name=full_name or user.get_full_name() or user.email or "Attendee",
    )

    if sponsor is not None:
        # Counted here rather than left to the organizer, so the allocation cannot
        # be quietly overspent. `remaining` on Sponsor reads these two fields.
        sponsor.tickets_claimed = (sponsor.tickets_claimed or 0) + 1
        sponsor.save(update_fields=["tickets_claimed"])

    record(
        target=ticket,
        action="Complimentary ticket issued",
        actor=issued_by,
        actor_label="" if getattr(issued_by, "pk", None) else "automation",
        new_value=reason,
        note=f"{ticket.order} to {user.email}",
        conference_year=year,
    )

    if notify:
        _notify(ticket, reason)
    return ticket, True


def _notify(ticket, reason):
    """Tell the recipient they have a ticket. Never the reason a send failed."""
    from emails.services import send_email, site_url

    if not ticket.user or not ticket.user.email:
        return False
    return send_email(
        template="tickets/complimentary",
        to=[ticket.user.email],
        subject=f"Your PyCon Nigeria {ticket.conference_year} ticket",
        context={
            "user_name": ticket.user.get_full_name() or ticket.user.email,
            "reason": reason,
            "order_code": ticket.order,
            "conference_year": ticket.conference_year,
            "ticket_url": f"{site_url()}/tickets/detail/{ticket.order}/",
        },
        tags=["tickets", "complimentary"],
        conference_year=ticket.conference_year,
    )


# ---------------------------------------------------------------------------
# Who is eligible, read from the records that prove it
# ---------------------------------------------------------------------------

def eligible_speakers(year):
    """
    Accounts belonging to speakers with a confirmed talk.

    Confirmed rather than merely accepted: an accepted speaker who never replies
    has their slot released by ``expire_unconfirmed_talks``, and issuing them a
    ticket in the meantime means chasing it back.
    """
    from cfp.models import Proposal

    users = {}
    proposals = (
        Proposal.objects.filter(
            conference_year=year, status=Proposal.STATUS_CONFIRMED
        )
        .select_related("speaker__user")
    )
    for proposal in proposals:
        speaker = proposal.speaker
        if speaker and speaker.user_id:
            users[speaker.user_id] = speaker.user
    return list(users.values())


def eligible_grant_recipients(year):
    """
    Accounts whose travel grant they have accepted.

    Not merely approved: an approval is now an offer with a deadline, and issuing a
    free ticket for one nobody has answered gives away a seat that may be declined.
    Accepting a grant issues the ticket on the spot, so this is the backfill for
    grants decided before that existed.
    """
    from grants.models import TravelGrantApplication

    applications = TravelGrantApplication.objects.filter(
        conference_year=year,
        status__in=TravelGrantApplication.PAYABLE_STATUSES,
    ).select_related("user")
    return list({a.user_id: a.user for a in applications if a.user_id}.values())


def eligible_volunteers(year):
    """
    Accounts holding the volunteer role for this edition.

    Reads role assignments because that is where a volunteer exists today. When
    module 6 builds an application and acceptance flow, this is the one function
    that changes.
    """
    from accounts.roles import Role, users_with_role

    return list(users_with_role(Role.VOLUNTEER, year))


#: Reason -> the function that finds who qualifies. Adding a category is one entry.
ELIGIBILITY = {
    REASON_SPEAKER: eligible_speakers,
    REASON_GRANT: eligible_grant_recipients,
    REASON_VOLUNTEER: eligible_volunteers,
}


def issue_for_reason(reason, year=None, *, issued_by=None, dry_run=False, notify=True):
    """
    Issue a ticket to everyone eligible under ``reason``.

    Returns ``(issued, skipped)`` as lists of users. Skipped means they already had
    one, which is the normal case on every run after the first.
    """
    year = year or current_year()
    finder = ELIGIBILITY.get(reason)
    if finder is None:
        raise IssueError(f"No eligibility rule for {reason!r}.")

    issued, skipped = [], []
    for user in finder(year):
        if already_issued(user, year, reason) is not None:
            skipped.append(user)
            continue
        if dry_run:
            issued.append(user)
            continue
        _, created = issue_complimentary_ticket(
            user=user, year=year, reason=reason, issued_by=issued_by, notify=notify
        )
        (issued if created else skipped).append(user)
    return issued, skipped


# ---------------------------------------------------------------------------
# Sponsor allocations
# ---------------------------------------------------------------------------

def sponsor_allocation(sponsor):
    """``(allocated, claimed, remaining)`` for one sponsor."""
    allocated = sponsor.tickets_allocated or 0
    claimed = sponsor.tickets_claimed or 0
    return allocated, claimed, max(allocated - claimed, 0)


def issue_sponsor_ticket(*, sponsor, user, issued_by=None, full_name=None, notify=True):
    """
    Spend one of a sponsor's allocated tickets on ``user``.

    Refuses rather than overspending. A sponsor who has used their five tickets
    asking for a sixth is a conversation about money, not something the software
    should decide by handing one out.
    """
    _, _, remaining = sponsor_allocation(sponsor)
    if remaining <= 0:
        raise IssueError(
            f"{sponsor.name} has no tickets left: "
            f"{sponsor.tickets_claimed or 0} of {sponsor.tickets_allocated or 0} claimed."
        )
    return issue_complimentary_ticket(
        user=user,
        year=sponsor.conference_year,
        reason=REASON_SPONSOR,
        issued_by=issued_by,
        sponsor=sponsor,
        full_name=full_name,
        notify=notify,
    )
