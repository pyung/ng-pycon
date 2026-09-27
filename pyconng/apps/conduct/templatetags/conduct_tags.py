"""
Linking to the Code of Conduct from anywhere.

The ticket purchase page asked visitors to agree to the Code of Conduct behind
``href="#"`` -- a link to nothing, on the page where somebody hands over money. It
could not be a hard-coded path either, because the page is a Wagtail page whose
slug an organizer can change. Hence a tag: it resolves the live page's own URL, or
falls back to the reporting form, which at least explains what the Code of Conduct
is for.
"""

from django import template
from django.core.cache import cache

register = template.Library()

CACHE_KEY = "conduct:coc_page_url"
CACHE_SECONDS = 300


@register.simple_tag
def code_of_conduct_url():
    """
    The live Code of Conduct page's URL, or ``/conduct/report/`` if there is none.

    Cached briefly: this is called from the footer and the purchase page, and
    neither should pay for a page lookup on every request.
    """
    cached = cache.get(CACHE_KEY)
    if cached:
        return cached

    from conduct.models import CodeOfConductPage

    url = "/conduct/report/"
    try:
        page = CodeOfConductPage.objects.live().first()
        if page is not None:
            url = page.get_url() or url
    except Exception:  # noqa: BLE001 - never break a page over a missing link
        pass

    cache.set(CACHE_KEY, url, CACHE_SECONDS)
    return url


#: Slugs an organizer might plausibly give the terms page, in order of preference.
TERMS_SLUGS = ("terms", "terms-and-conditions", "terms-conditions", "terms-of-sale")

TERMS_CACHE_KEY = "conduct:terms_page_url"


@register.simple_tag
def terms_url():
    """
    The terms and conditions page's URL, or an empty string when there is none.

    Empty rather than "#": the ticket page asks buyers to agree to terms, and a
    link to nothing is worse than naming them in plain text. The caller decides
    what to render, and an empty result is the signal that the page still needs
    writing.
    """
    cached = cache.get(TERMS_CACHE_KEY)
    if cached is not None:
        return cached

    from wagtail.models import Page

    url = ""
    try:
        page = Page.objects.live().filter(slug__in=TERMS_SLUGS).first()
        if page is not None:
            url = page.get_url() or ""
    except Exception:  # noqa: BLE001
        pass

    cache.set(TERMS_CACHE_KEY, url, CACHE_SECONDS)
    return url
