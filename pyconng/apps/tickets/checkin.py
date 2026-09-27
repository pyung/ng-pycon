"""
Looking up a place at the door.

Module 4's part of check-in: every place carries a code, the code is in a QR on
the ticket, and the code resolves to exactly one place with a clear yes or no. The
scanner that tolerates a bad connection and a queue is module 11's job; this is
what it will call.

The rules are deliberately blunt, because they are applied by a volunteer with
people waiting: one code, one place, one admission. A second scan of the same code
is reported as already used rather than silently accepted, because the usual cause
is two people trying to use one ticket.
"""

import logging

from django.utils import timezone

from audit.services import record

from .models import Ticket, TicketSale, TicketSettings

logger = logging.getLogger(__name__)

#: Outcomes of a scan. Strings rather than booleans so the screen can say which.
OK = "ok"
ALREADY_CHECKED_IN = "already_checked_in"
NOT_FOUND = "not_found"
NOT_PAID = "not_paid"
TOO_EARLY = "too_early"

MESSAGES = {
    OK: "Admitted.",
    ALREADY_CHECKED_IN: "This ticket has already been used.",
    NOT_FOUND: "No ticket matches that code.",
    NOT_PAID: "This order is not paid, or has been refunded.",
    TOO_EARLY: "Check-in has not opened yet.",
}


def normalise(code):
    """
    Tidy a code typed or scanned in.

    Uppercased, with spaces and hyphens dropped: the code is read off a badge and
    people insert both. Also strips a URL prefix, so scanning with a camera app
    that hands over the whole link still works.
    """
    text = (code or "").strip().upper()
    if "/" in text:
        text = text.rstrip("/").rsplit("/", 1)[-1]
    return text.replace(" ", "").replace("-", "")


def find(code):
    """The place with this check-in code, or None."""
    code = normalise(code)
    if not code:
        return None
    return (
        TicketSale.objects.select_related("ticket", "ticket__ticket_type", "user")
        .filter(checkin_code=code)
        .first()
    )


def inspect(code, *, year=None):
    """
    What would happen if this code were scanned, without recording anything.

    Returns ``(outcome, sale)``. Used by the check-in screen to show a name before
    a volunteer confirms, and by ``check_in`` so the rules live in one place.
    """
    sale = find(code)
    if sale is None:
        return NOT_FOUND, None
    if sale.ticket.status != Ticket.PAID:
        return NOT_PAID, sale
    policy = TicketSettings.for_year(year or sale.ticket.conference_year)
    if not policy.checkin_open:
        return TOO_EARLY, sale
    if sale.checked_in_at is not None:
        return ALREADY_CHECKED_IN, sale
    return OK, sale


def check_in(code, *, by=None, year=None):
    """
    Admit the holder of ``code``.

    Returns ``(outcome, sale)``. Only an ``OK`` outcome writes anything, so a
    refused scan leaves no trace on the record beyond the log -- a volunteer
    scanning the wrong badge twice must not mark anybody in.
    """
    outcome, sale = inspect(code, year=year)
    if outcome != OK:
        logger.info("Check-in refused (%s) for code %r", outcome, normalise(code))
        return outcome, sale

    sale.checked_in_at = timezone.now()
    sale.checked_in_by = by if getattr(by, "pk", None) else None
    sale.save(update_fields=["checked_in_at", "checked_in_by"])

    record(
        target=sale.ticket,
        action="Attendee checked in",
        actor=by,
        new_value=sale.full_name,
        note=f"{sale.checkin_code} ({sale.ticket_type_name})",
        conference_year=sale.ticket.conference_year,
    )
    return OK, sale


def undo(sale, *, by=None):
    """
    Clear a check-in, for the mistake that will happen at least once an event.

    Recorded rather than silent: "this person was marked in and then out again" is
    exactly the sort of thing somebody asks about afterwards.
    """
    sale.checked_in_at = None
    sale.checked_in_by = None
    sale.save(update_fields=["checked_in_at", "checked_in_by"])
    record(
        target=sale.ticket,
        action="Check-in undone",
        actor=by,
        old_value=sale.full_name,
        note=sale.checkin_code,
        conference_year=sale.ticket.conference_year,
    )
    return sale


def checkin_url(sale, base_url=None):
    """
    The URL a scanner opens, and what the QR carries.

    Uppercase throughout so the whole string fits QR alphanumeric mode, which keeps
    the symbol small enough to print on a badge and read from a phone screen. A
    host is case-insensitive and the code's alphabet has no lowercase, so nothing
    is lost.
    """
    from emails.services import site_url

    base = (base_url or site_url() or "").rstrip("/")
    base = base.replace("HTTPS://", "https://").replace("HTTP://", "http://")
    without_scheme = base.split("://", 1)[-1] if "://" in base else base
    scheme = "HTTPS" if base.startswith("https") else "HTTP"
    return f"{scheme}://{without_scheme.upper()}/TICKETS/C/{sale.checkin_code}"


def qr_svg(sale, *, module=4, base_url=None):
    """
    The QR for one place, as inline SVG, or None if it cannot be built.

    Returns None rather than raising: a ticket page missing its QR but showing the
    code in text is usable, and one that 500s is not. The code beside it is the
    fallback a volunteer types.
    """
    from . import qr

    try:
        return qr.to_svg(
            qr.encode(checkin_url(sale, base_url)),
            module=module,
            title=f"Check-in code {sale.checkin_code}",
        )
    except qr.QRError:
        logger.exception("Could not build a QR code for %s", sale.checkin_code)
        return None
