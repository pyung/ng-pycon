"""
Context processors for PyCon Nigeria.

Edition data comes from the editions app rather than constants in this module,
so opening a new year is an admin change instead of a deploy. The template
context keys are unchanged: templates carry on reading ``conference_year``,
``conference_theme``, ``conference_year_info``, ``available_years``,
``current_year``, ``is_current_year``, ``is_year_specific_url`` and
``base_template``.

Two groups of keys were added for the accessibility and performance pass:
``fonts_href``, the theme's web-font stylesheet, and the footer chrome
(``newsletter_title``, ``footer_links``, the social URLs and so on) that the
2026 footer template had always read by bare name without anything supplying it.
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
from editions.webfonts import fonts_href

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
        # The theme's typefaces. These used to be an @import in each theme's CSS
        # that PostCSS dropped, so no edition has ever loaded the fonts it was
        # designed with; the base template emits a <link> for this instead.
        "fonts_href": fonts_href(edition.theme if edition else None),
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

    context = {
        "navigation_menu_items": navigation_items,
        "page_conference_year": page_year,
        "nav_login_href": nav_login_href,
        "nav_login_label": nav_login_label,
    }
    context.update(_footer_context(home_page))
    return context


#: HomePage fields the site footer reads. The 2026 footer template asks for them
#: by bare name -- ``{{ newsletter_title }}``, not ``{{ page.newsletter_title }}``
#: -- so on a Wagtail page they resolved to nothing and on every other view there
#: was nothing to resolve. The result was a footer permanently showing defaults:
#: no newsletter form, no social icons at all, and three hard-coded links to
#: pages that do not exist. Supplying them here fixes every page at once, and
#: keeps working on views that have no ``page`` in context.
FOOTER_FIELDS = (
    "footer_copyright",
    "footer_links",
    "newsletter_title",
    "newsletter_description",
    "twitter_url",
    "facebook_url",
    "linkedin_url",
    "instagram_url",
    "youtube_url",
    "github_url",
)


def _footer_context(home_page):
    """
    Footer, social and newsletter values from the edition's homepage.

    Returns every key even when there is no homepage, so a template can test a
    value without a missing-variable silently reading as empty either way.
    """
    return {
        field: getattr(home_page, field, "") or ""
        for field in FOOTER_FIELDS
    }
