"""
Web fonts per theme.

Each theme's source CSS declares its typefaces with an ``@import url(...)`` of a
Google Fonts stylesheet. That import never reached the browser: a CSS ``@import``
is only valid before any other rule, and these sat after ``@tailwind utilities``,
so PostCSS dropped every one of them. No theme has ever loaded the typefaces it
was designed with -- every edition has been rendering in the system stack.

The URLs live here instead, and the base template emits a ``<link>`` for them.
That also removes a request chained behind the stylesheet: the browser can start
fetching the fonts as soon as it has the HTML, rather than after the CSS parses.

A theme with no entry gets no web font and falls back to the system stack. That
is the right failure: a missing typeface must not stop a new edition opening.
Adding a theme here is the same kind of change as adding its CSS file.
"""

#: Theme slug -> Google Fonts stylesheet URL. ``display=swap`` is required on
#: every entry: text must paint in the fallback face rather than stay invisible
#: while the web font downloads, which matters most on a slow connection.
THEME_WEBFONTS = {
    "2024": (
        "https://fonts.googleapis.com/css2"
        "?family=JetBrains+Mono:wght@300;400;500;600;700"
        "&family=Space+Grotesk:wght@300;400;500;600;700"
        "&family=Inter:wght@300;400;500;600;700"
        "&display=swap"
    ),
    "2025": (
        "https://fonts.googleapis.com/css2"
        "?family=Comfortaa:wght@300;400;500;600;700"
        "&family=Nunito:wght@300;400;500;600;700;800"
        "&family=Caveat:wght@400;500;600;700"
        "&family=Inter:wght@300;400;500;600;700"
        "&display=swap"
    ),
    "2026": (
        "https://fonts.googleapis.com/css2"
        "?family=Source+Sans+3:wght@400;500;600;700"
        "&family=Syne:wght@500;600;700;800"
        "&display=swap"
    ),
}

#: Used when an edition's theme has no entry of its own, so a year that reuses an
#: older design still gets type. 2026 is the current house style.
FALLBACK_THEME = "2026"


def fonts_href(theme):
    """
    The stylesheet URL for ``theme``, or ``None`` when it has no web font.

    An unknown theme -- a 2027 edition pointing at a design that has not been
    built yet, say -- falls back to the house style rather than to nothing, so
    the page still reads as PyCon Nigeria.
    """
    if not theme:
        return None
    return THEME_WEBFONTS.get(theme) or THEME_WEBFONTS.get(FALLBACK_THEME)
