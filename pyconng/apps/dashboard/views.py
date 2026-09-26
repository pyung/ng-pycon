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
    }

    return render(request, "dashboard/dashboard.html", context)
