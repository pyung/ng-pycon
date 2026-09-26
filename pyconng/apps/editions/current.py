"""
Which edition is "now".

``current_year()`` is the drop-in replacement for the old hard-coded
``CURRENT_YEAR`` constant. It reads the database, so rolling the site over to a
new edition is a change an organizer makes in the admin.

Resolution order:

1. the edition flagged ``is_current``
2. failing that, the newest published edition
3. failing that, ``settings.PYCON_FALLBACK_YEAR`` (default 2026)

Step 3 matters more than it looks: this runs during ``migrate`` on a fresh
database, before the editions table exists, and must not raise.
"""

from django.conf import settings
from django.core.cache import cache
from django.db.utils import DatabaseError

YEAR_CACHE_KEY = "pyconng:current_edition_year"
CACHE_TTL = 300  # seconds; also invalidated explicitly when an Edition is saved

FALLBACK_YEAR = getattr(settings, "PYCON_FALLBACK_YEAR", 2026)


def _resolve_year():
    from .models import Edition

    try:
        edition = Edition.objects.filter(is_current=True).order_by("-year").first()
        if edition is None:
            edition = Edition.objects.published().order_by("-year").first()
        if edition is not None:
            return edition.year
    except DatabaseError:
        # Table not created yet (initial migrate) or the database is unreachable.
        pass
    return FALLBACK_YEAR


def current_year():
    """The current edition's year. Cheap: cached, and falls back safely."""
    cached = cache.get(YEAR_CACHE_KEY)
    if cached is not None:
        return cached
    year = _resolve_year()
    try:
        cache.set(YEAR_CACHE_KEY, year, CACHE_TTL)
    except Exception:
        # A broken cache backend must not take the site down.
        pass
    return year


def current_edition():
    """The current :class:`~editions.models.Edition`, or None if none exists."""
    from .models import Edition

    try:
        return Edition.objects.filter(year=current_year()).first()
    except DatabaseError:
        return None


def edition_for_year(year):
    """One edition by year, or None."""
    from .models import Edition

    try:
        return Edition.objects.filter(year=year).first()
    except DatabaseError:
        return None


def published_editions():
    """
    Published editions as ``{year: Edition}``, oldest first.

    Keyed by year and iterated with ``.items`` in templates, matching the shape
    the old CONFERENCE_YEARS dict had.
    """
    from .models import Edition

    try:
        return {e.year: e for e in Edition.objects.published().order_by("year")}
    except DatabaseError:
        return {}


def known_years():
    """Every year that has an edition, published or not."""
    from .models import Edition

    try:
        return set(Edition.objects.values_list("year", flat=True))
    except DatabaseError:
        return set()


def invalidate():
    """Drop the cached year. Called whenever an Edition changes."""
    try:
        cache.delete(YEAR_CACHE_KEY)
    except Exception:
        pass
