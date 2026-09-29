"""
Which URLs the frontend audit visits.

Every live Wagtail page, plus the app views that are not pages -- the CFP, ticket
and grant landing pages, sign-in, sign-up and search. Those are the pages a
first-time visitor is most likely to land on from a link, and none of them is in
the page tree, so a tree walk alone would miss them entirely.

Anything behind a login or a role is left out on purpose: the audit runs
anonymously, so an authenticated page would only ever report its redirect.
"""

import logging

from django.urls import NoReverseMatch, reverse

logger = logging.getLogger(__name__)

#: Named URLs to audit alongside the page tree. Any name that does not resolve is
#: skipped quietly -- an app can be removed without breaking the audit.
NAMED_URLS = (
    "cfp:landing",
    "tickets:home",
    "grants:landing",
    "volunteers:landing",
    "login",
    "signup",
    "search",
)


def page_urls():
    """Relative URLs of every live, public page in the tree."""
    from wagtail.models import Page

    urls = []
    for page in Page.objects.live().public().specific(defer=True):
        if page.depth <= 1:
            continue  # The tree root is not a page anyone can visit.
        try:
            url = page.get_url()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not resolve a URL for page %s: %s", page.pk, exc)
            continue
        if url:
            urls.append(url)
    return urls


def named_urls():
    """Relative URLs for NAMED_URLS, skipping any that do not resolve."""
    urls = []
    for name in NAMED_URLS:
        try:
            urls.append(reverse(name))
        except NoReverseMatch:
            logger.debug("Audit URL %s does not resolve; skipping.", name)
    return urls


def all_urls():
    """Every URL to audit, de-duplicated, page tree first."""
    seen = []
    for url in page_urls() + named_urls():
        if url not in seen:
            seen.append(url)
    return seen
