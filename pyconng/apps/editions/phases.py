"""
Where an edition is in its lifecycle, and what the homepage should therefore ask
visitors to do.

The primary call to action changes with the phase — "Submit a talk" while the
CFP is open, "Buy tickets" once it is not, "Watch the talks" afterwards. Derived
from live system state rather than a date calendar, so the button cannot invite
submissions to a CFP that has closed.

**Precedence**, highest first, because several can be true at once:

1. ``past`` — the conference has finished
2. ``during`` — it is happening right now
3. ``cfp_open`` — submissions are open; a short, hard-deadlined window, so it
   outranks ticket sales, which run for months
4. ``tickets_on_sale`` — tickets available
5. ``schedule_live`` — talks published but no tickets available
6. ``announced`` — nothing open yet

Organizers are not stuck with this. ``HomePage`` resolves its button as: the
manual hero fields if filled, then a per-phase override, then the default below.
"""

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

PAST = "past"
DURING = "during"
CFP_OPEN = "cfp_open"
TICKETS_ON_SALE = "tickets_on_sale"
SCHEDULE_LIVE = "schedule_live"
ANNOUNCED = "announced"

PHASE_CHOICES = [
    (ANNOUNCED, "Announced — nothing open yet"),
    (CFP_OPEN, "Call for proposals open"),
    (TICKETS_ON_SALE, "Tickets on sale"),
    (SCHEDULE_LIVE, "Schedule published"),
    (DURING, "Conference happening now"),
    (PAST, "Conference finished"),
]

#: Default wording per phase. Overridable per edition in the admin.
DEFAULT_LABELS = {
    ANNOUNCED: "Get conference updates",
    CFP_OPEN: "Submit a talk",
    TICKETS_ON_SALE: "Buy tickets",
    SCHEDULE_LIVE: "View the schedule",
    DURING: "View the schedule",
    PAST: "Watch the talks",
}


def _safe_reverse(name, fallback=""):
    try:
        return reverse(name)
    except NoReverseMatch:
        return fallback


def phase_for(year):
    """The lifecycle phase of one edition."""
    from .current import edition_for_year

    today = timezone.localdate()
    edition = edition_for_year(year)

    if edition and edition.starts_on:
        last_day = edition.ends_on or edition.starts_on
        if today > last_day:
            return PAST
        if edition.starts_on <= today <= last_day:
            return DURING

    if _cfp_is_open(year):
        return CFP_OPEN
    if _tickets_available(year):
        return TICKETS_ON_SALE
    if _talks_published(year):
        return SCHEDULE_LIVE
    return ANNOUNCED


def _cfp_is_open(year):
    try:
        from cfp.models import CFPSettings

        settings_obj = CFPSettings.objects.filter(conference_year=year).first()
        return bool(settings_obj and settings_obj.is_open)
    except Exception:  # noqa: BLE001 - never let the homepage fail over a CTA
        return False


def _tickets_available(year):
    try:
        from tickets.models import TicketType

        for ticket_type in TicketType.objects.active().for_year(year):
            if not ticket_type.is_sold_out:
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _talks_published(year):
    try:
        from program.models import Talk

        return Talk.objects.published().for_year(year).exists()
    except Exception:  # noqa: BLE001
        return False


def default_cta(year, phase=None):
    """
    ``(phase, label, url)`` for an edition's primary call to action.

    Every URL returned points at something that exists today: there is no
    schedule page yet, so the schedule phases fall back to the year's archive if
    one is published and to tickets otherwise. A call to action that 404s is
    worse than a slightly wrong one.
    """
    phase = phase or phase_for(year)
    label = DEFAULT_LABELS[phase]

    cfp_url = _safe_reverse("cfp:landing", "/cfp/")
    tickets_url = _safe_reverse("tickets:home", "/tickets/")
    archive_url = _archive_url(year)

    if phase == CFP_OPEN:
        url = cfp_url
    elif phase == TICKETS_ON_SALE:
        url = tickets_url
    elif phase in (SCHEDULE_LIVE, DURING):
        url = archive_url or tickets_url
    elif phase == PAST:
        url = archive_url or "/"
    else:
        url = tickets_url or cfp_url

    return phase, label, url


def _archive_url(year):
    """The published archive page for a year, if there is one."""
    try:
        from program.models import YearArchivePage

        page = YearArchivePage.objects.live().filter(conference_year=year).first()
        return page.url if page else ""
    except Exception:  # noqa: BLE001
        return ""
