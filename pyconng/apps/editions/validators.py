"""Validation shared by the models that carry a ``conference_year``."""

from django.core.exceptions import ValidationError


def validate_edition_year(value):
    """
    Reject a year that has no edition.

    Applied to the year fields an organizer types by hand, so a mistyped 2072
    is caught in the admin instead of quietly creating records nobody can find.

    Django runs field validators through forms and ``full_clean()``, not on a
    bare ``save()`` -- so this guards human entry without threatening existing
    rows or code that sets the year programmatically.
    """
    from .current import known_years

    years = known_years()
    if years and value not in years:
        known = ", ".join(str(y) for y in sorted(years))
        raise ValidationError(
            f"No conference edition exists for {value}. Known editions: {known}. "
            "Create the edition first under Editions.",
        )
