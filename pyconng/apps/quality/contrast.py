"""
Colour contrast over the theme palettes (WCAG 1.4.3).

The rest of the audit reads rendered HTML; contrast cannot be read that way
without a browser to resolve the cascade. What *can* be checked without one is
the palette itself: each theme declares its colours as custom properties, and the
components combine them in a small number of documented ways -- white on the
primary for a button, the light text on the dark section, and so on. Those pairs
are listed below with the component that uses them, so a palette change that
makes a real combination unreadable is caught.

This does not prove the site passes 1.4.3: text over a photograph, a gradient or
a hover state is out of reach here. It does mean a new edition's palette cannot
quietly ship an unreadable button.
"""

import re
from dataclasses import dataclass

#: Selector -> declarations, for the scopes a theme uses. Both forms appear in the
#: source CSS: :root holds the shared defaults, and the theme class overrides them.
_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}", re.MULTILINE)
_CUSTOM_PROPERTY = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+)", re.IGNORECASE)
_HEX = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.IGNORECASE)


@dataclass(frozen=True)
class Pair:
    """One foreground/background combination a component actually renders."""

    component: str
    foreground: str
    background: str
    #: 4.5 for normal text, 3.0 for large text (>=24px, or >=18.66px bold) and
    #: for the boundary of a user-interface component.
    minimum: float = 4.5


#: The combinations the themes put on screen. Each names the rule that creates it
#: so the pairing can be checked against the CSS rather than taken on trust.
CHECKED_PAIRS = (
    Pair(".theme-button label", "#ffffff", "--theme-primary"),
    Pair(".theme-link on a light page", "--theme-primary", "#ffffff"),
    Pair(".btn-gold label", "--theme-dark", "--theme-accent"),
    Pair(".btn-outline label", "--theme-primary-light", "--theme-dark"),
    Pair(".dark-section body text", "--theme-text-light", "--theme-dark"),
    Pair("page body text", "--theme-text", "--theme-bg-light"),
)


def _scopes(css_text, theme):
    """
    Custom properties by scope: ``:root``, ``[data-theme]`` and ``.theme-<theme>``.

    The three are not interchangeable. The base template puts ``data-theme`` on
    <html> and ``theme-<year>`` on <body>, so where both declare a token the class
    wins for everything inside the body -- it is the nearer ancestor, whatever the
    source order says. Modelling that is the difference between reporting a real
    contrast failure and reporting a mis-read one.
    """
    root, attribute, klass = {}, {}, {}
    for selector, body in _BLOCK.findall(css_text):
        selector = " ".join(selector.split())
        declarations = {
            name.lower(): value.strip()
            for name, value in _CUSTOM_PROPERTY.findall(body)
        }
        if not declarations:
            continue
        if f".theme-{theme}" in selector:
            klass.update(declarations)
        elif f'[data-theme="{theme}"]' in selector:
            attribute.update(declarations)
        elif ":root" in selector:
            root.update(declarations)
    return root, attribute, klass


def parse_palette(css_text, theme):
    """Custom properties in effect for content inside <body> under ``theme``."""
    root, attribute, klass = _scopes(css_text, theme)
    palette = dict(root)
    palette.update(attribute)
    palette.update(klass)
    return palette


def conflicting_tokens(css_text, theme):
    """
    Tokens a theme defines twice with different values, as
    ``{token: {"[data-theme]": value, ".theme-x": value}}``.

    Both selectors match on every page, so a token in here has one value on
    <html> and another on <body>. The class wins where it applies, but the pair is
    a trap: change one and the other carries on applying wherever the body class
    does not reach, and neither reads as wrong on its own.
    """
    _, attribute, klass = _scopes(css_text, theme)
    conflicts = {}
    for token, value in klass.items():
        other = attribute.get(token)
        if other is not None and other.lower() != value.lower():
            conflicts[token] = {"[data-theme]": other, f".theme-{theme}": value}
    return conflicts


def resolve(value, palette, _depth=0):
    """
    A colour token or literal, reduced to a hex string. None when it cannot be.

    Follows ``var(--x)`` chains, which the palettes use freely -- ``--theme-primary``
    is often defined as ``var(--theme-primary-600)``.
    """
    if value is None or _depth > 10:
        return None
    value = value.strip()
    if value.startswith("--"):
        return resolve(palette.get(value.lower()), palette, _depth + 1)
    match = re.match(r"var\(\s*(--[a-z0-9-]+)\s*(?:,([^)]*))?\)", value, re.IGNORECASE)
    if match:
        resolved = resolve(match.group(1), palette, _depth + 1)
        if resolved is None and match.group(2):
            return resolve(match.group(2), palette, _depth + 1)
        return resolved
    if value.lower() == "white":
        return "#ffffff"
    if value.lower() == "black":
        return "#000000"
    return value if _HEX.match(value) else None


def _channels(hex_colour):
    text = hex_colour.lstrip("#")
    if len(text) in (3, 4):
        text = "".join(c * 2 for c in text[:3])
    text = text[:6]
    return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))


def relative_luminance(hex_colour):
    """WCAG relative luminance."""

    def linearise(channel):
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearise(c) for c in _channels(hex_colour))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first, second):
    """The WCAG contrast ratio between two hex colours, 1.0 to 21.0."""
    a = relative_luminance(first)
    b = relative_luminance(second)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def audit_palette(css_text, theme):
    """
    ``[(Pair, ratio, passes)]`` for every pair whose colours the theme defines.

    A pair using a token the theme does not declare is skipped rather than
    guessed at: the 2024 palette has no --theme-dark, and inventing one would
    report a ratio for something nobody sees.
    """
    palette = parse_palette(css_text, theme)
    results = []
    for pair in CHECKED_PAIRS:
        foreground = resolve(pair.foreground, palette)
        background = resolve(pair.background, palette)
        if not foreground or not background:
            continue
        ratio = contrast_ratio(foreground, background)
        results.append((pair, round(ratio, 2), ratio >= pair.minimum))
    return results
