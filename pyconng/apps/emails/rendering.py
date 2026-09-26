"""Placeholder substitution for admin-edited email copy."""

import re

#: A {placeholder} marker: a single identifier in braces, nothing else.
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def safe_format(text, context):
    """
    Replace ``{name}`` markers from ``context``, leaving unknown ones untouched.

    Unlike ``str.format``, a typo or a stray brace cannot raise -- an organizer
    editing copy in the admin should never be able to stop an email sending.
    A marker with no matching value is left visible, which makes the mistake
    obvious in a test send rather than silently producing an empty gap.
    """
    if not text:
        return ""

    def substitute(match):
        name = match.group(1)
        if name in context:
            value = context[name]
            return "" if value is None else str(value)
        return match.group(0)

    return PLACEHOLDER_RE.sub(substitute, text)
