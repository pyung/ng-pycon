"""Validation shared by the models that carry a ``conference_year``."""

import re

from django.core.exceptions import ValidationError

#: ``#rgb`` or ``#rrggbb``, and nothing else.
HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


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


def validate_hex_color(value):
    """
    Reject anything that is not a plain hex colour.

    These values are emitted into a ``<style>`` block so an organizer can retune
    the palette without a deploy. CSS is not an HTML context, so Django's
    autoescaping does not protect it: a value containing ``}`` could close the
    rule and add its own, and one containing ``</style>`` could leave CSS
    altogether. Restricting the field to ``#rgb``/``#rrggbb`` removes that whole
    class of problem at the point of entry, and
    :meth:`editions.models.Edition.css_variables` drops anything that got in
    before this validator existed.
    """
    if not HEX_COLOR.match(value or ""):
        raise ValidationError(
            "Enter a hex colour such as #0E6B3D or #fff \u2014 "
            f"{value!r} is not one.",
        )
