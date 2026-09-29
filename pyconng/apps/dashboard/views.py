"""
Unified dashboard for PyCon Nigeria (current year only).

Role-based access: attendee, CFP reviewer, travel grant reviewer,
program chair, finance team, super admin.
"""

from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from wagtail.models import Site

from home.models import HomePage, SponsorPage
from editions.current import current_year

from accounts.roles import roles_for

from grants.models import GrantReviewerAssignment
from grants.services import GrantService


def _cfp_assignment_count(user):
    """How many proposals this person has been asked to review."""
    from cfp.models import ReviewerAssignment
    return ReviewerAssignment.objects.filter(reviewer=user).count()


def _get_cfp_open():
    try:
        from cfp.services import CFPService
        return CFPService.is_cfp_open()
    except Exception:
        return False


def _get_current_year_home_page(request):
    """
    Resolve the live HomePage for current_year() (same strategy as navigation_context).
    """
    site = Site.find_for_request(request)
    if not site:
        return None

    root_page = site.root_page.specific
    home_page = None

    if isinstance(root_page, HomePage):
        if root_page.conference_year is None or root_page.conference_year == current_year():
            home_page = root_page

    if not home_page:
        child = (
            site.root_page.get_children()
            .type(HomePage)
            .live()
            .filter(
                Q(conference_year__isnull=True) | Q(conference_year=current_year())
            )
            .first()
        )
        if child:
            home_page = child.specific

    if not home_page:
        home_page = (
            HomePage.objects.live()
            .filter(
                Q(conference_year__isnull=True) | Q(conference_year=current_year())
            )
            .first()
        )

    if not home_page and isinstance(root_page, HomePage):
        home_page = root_page

    return home_page


def _get_current_year_sponsor_page(request):
    """First live SponsorPage under the current year HomePage; prefer slug 'sponsorship'."""
    home_page = _get_current_year_home_page(request)
    if not home_page:
        return None

    qs = (
        home_page.get_children()
        .live()
        .type(SponsorPage)
        .specific()
        .order_by("path")
    )
    preferred = qs.filter(slug="sponsorship").first()
    if preferred:
        return preferred
    return qs.first()


def _volunteer_context(user):
    """
    Volunteer state for the dashboard: where they stand, and their next shift.

    The next shift rather than all of them, because the dashboard is a summary and
    "when do I next need to be somewhere" is the question it should answer. Fails
    soft: the dashboard is the page people land on, and it must not break because a
    volunteer table is mid-migration.
    """
    try:
        from volunteers import services as volunteer_services

        year = current_year()
        application = volunteer_services.application_for(user, year)
        settings_obj = volunteer_services.settings_for(year)
        roster = []
        if application is not None and settings_obj.shifts_published:
            roster = list(volunteer_services.roster_for(application))
        return {
            "volunteer_application": application,
            "volunteer_open": settings_obj.is_open,
            "volunteer_shifts_published": settings_obj.shifts_published,
            "volunteer_shift_count": len(roster),
            "volunteer_next_shift": roster[0].shift if roster else None,
        }
    except Exception:  # noqa: BLE001
        return {
            "volunteer_application": None,
            "volunteer_open": False,
            "volunteer_shifts_published": False,
            "volunteer_shift_count": 0,
            "volunteer_next_shift": None,
        }


def dashboard(request):
    """Unified role-based dashboard. Login required. Current year only."""
    if not request.user.is_authenticated:
        return redirect(reverse("login") + f"?next={request.path}")

    roles = roles_for(request.user, current_year())

    cfp_context = None
    if roles.is_cfp_reviewer:
        cfp_context = {"assignment_count": _cfp_assignment_count(request.user)}

    grant_application = GrantService.get_user_application(request.user)
    grant_open = GrantService.is_grant_open()
    cfp_info = GrantService.get_user_cfp_info(request.user)
    cfp_open = _get_cfp_open()

    grant_assignment_count = 0
    if roles.is_grant_reviewer:
        grant_assignment_count = GrantReviewerAssignment.objects.filter(
            reviewer=request.user
        ).count()

    sponsor_page = _get_current_year_sponsor_page(request)
    volunteer = _volunteer_context(request.user)

    context = {
        "conference_year": current_year(),
        "roles": roles,
        "grant_application": grant_application,
        "grant_open": grant_open,
        "grant_settings": GrantService.get_current_settings(),
        "cfp_info": cfp_info,
        "cfp_open": cfp_open,
        "cfp_context": cfp_context,
        "grant_assignment_count": grant_assignment_count,
        "sponsor_page": sponsor_page,
        **volunteer,
    }

    return render(request, "dashboard/dashboard.html", context)
