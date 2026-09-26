"""
Context processors for PyCon Nigeria.

Edition data comes from the editions app rather than constants in this module,
so opening a new year is an admin change instead of a deploy. The template
context keys are unchanged: templates carry on reading ``conference_year``,
``conference_theme``, ``conference_year_info``, ``available_years``,
``current_year``, ``is_current_year``, ``is_year_specific_url`` and
``base_template``.
"""

import logging
import re

from django.conf import settings
from django.db.models import Q
from django.template.loader import get_template
from django.urls import reverse
from wagtail.models import Site

from editions.current import (
    current_year,
    edition_for_year,
    known_years,
    published_editions,
)

logger = logging.getLogger(__name__)

#: Matches a year-prefixed path such as /2024/ or /2025/schedule/
YEAR_URL_RE = re.compile(r"^/(\d{4})/")


def _resolve_year(path):
    """
    The edition a request addresses.

    Returns ``(year, url_named_a_year)``. An unrecognised year in the URL falls
    back to the current edition, but still counts as a year-specific URL -- the
    behaviour the hard-coded version had.
    """
    match = YEAR_URL_RE.match(path)
    year = None
    if match:
        year = int(match.group(1))
        if year not in known_years():
            year = None
    if year is None:
        year = current_year()
    return year, match is not None


def conference_context(request):
    """Edition year, theme and palette for the requested URL."""
    year, is_year_specific_url = _resolve_year(request.path)

    edition = edition_for_year(year)
    if edition is None:
        edition = edition_for_year(current_year())

    # Fall back to base.html when a theme has no dedicated base template.
    base_template = f"base_{year}.html"
    try:
        get_template(base_template)
    except Exception:
        base_template = "base.html"

    now_year = current_year()
    return {
        "conference_year": year,
        "conference_theme": edition.theme if edition else None,
        "conference_year_info": edition,
        "available_years": published_editions(),
        "current_year": now_year,
        "current_edition": edition if year == now_year else edition_for_year(now_year),
        "is_current_year": year == now_year,
        "is_year_specific_url": is_year_specific_url,
        "base_template": base_template,
    }


def site_context(request):
    """General site information."""
    return {
        "site_name": "PyCon Nigeria",
        "site_tagline": "The premier Python conference in Nigeria",
        "debug": settings.DEBUG,
    }


def navigation_context(request):
    """
    Navigation menu for the edition this URL addresses.

    The current edition's homepage is the one with ``conference_year`` unset or
    equal to the current year; archived years have it set explicitly.
    """
    year, _ = _resolve_year(request.path)
    now_year = current_year()

    navigation_items = None
    page_year = None
    home_page = None

    try:
        site = Site.find_for_request(request)
        if site:
            from home.models import HomePage

            if year == now_year:
                root_page = site.root_page.specific
                if isinstance(root_page, HomePage) and (
                    root_page.conference_year is None
                    or root_page.conference_year == now_year
                ):
                    home_page = root_page

                if not home_page:
                    child = (
                        site.root_page.get_children()
                        .type(HomePage)
                        .live()
                        .filter(
                            Q(conference_year__isnull=True)
                            | Q(conference_year=now_year)
                        )
                        .first()
                    )
                    home_page = child.specific if child else None

                if not home_page:
                    home_page = (
                        HomePage.objects.live()
                        .filter(
                            Q(conference_year__isnull=True)
                            | Q(conference_year=now_year)
                        )
                        .first()
                    )

                # Last resort: the site root, whatever year it claims.
                if not home_page and isinstance(root_page, HomePage):
                    home_page = root_page
            else:
                home_page = (
                    HomePage.objects.live().filter(conference_year=year).first()
                    or HomePage.objects.live().filter(slug=str(year)).first()
                )

            if home_page:
                if not isinstance(home_page, HomePage):
                    home_page = home_page.specific
                navigation_items = home_page.navigation_menu_items
                page_year = home_page.conference_year or year
    except Exception as exc:  # noqa: BLE001
        logger.error("Error getting navigation for year %s: %s", year, exc)

    nav_login_href = reverse("login")
    nav_login_label = "Sign In"
    if home_page is not None:
        try:
            nav_login_href = home_page.get_login_href(request)
            nav_login_label = home_page.get_login_label()
        except Exception:  # noqa: BLE001
            pass

    return {
        "navigation_menu_items": navigation_items,
        "page_conference_year": page_year,
        "nav_login_href": nav_login_href,
        "nav_login_label": nav_login_label,
    }
