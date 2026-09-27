"""
Accessibility and performance checks over rendered HTML.

Module 1 of the 2027 brief asks for performance and accessibility, and the honest
state before this was: no audit had ever been run, and nothing in CI looked. The
first thing an audit needs is something that runs, which is why these rules are
markup-level and stdlib-only -- see parser.py for why there is no headless
browser here.

Every rule states the WCAG criterion or the performance cost it stands for, and
severity is meant strictly: an ``error`` is something that is wrong on any page
it appears on, and a ``warning`` is something worth a look that a template may
have a good reason for. Only errors fail the build, because a check that cries
wolf gets switched off and then nothing is checked at all.
"""

import re
from dataclasses import dataclass

from .parser import parse

#: Hosts a page may fetch from without the third-party rule complaining. Anything
#: else is a request to somebody else's server on the critical path, which on a
#: slow Nigerian connection is the difference between a page and a blank screen.
ALLOWED_EXTERNAL_HOSTS = frozenset(
    {
        "fonts.googleapis.com",
        "fonts.gstatic.com",
    }
)

#: Input types that are not user-facing fields and so need no label.
UNLABELLED_INPUT_TYPES = frozenset(
    {"hidden", "submit", "button", "reset", "image"}
)

_ABSOLUTE_URL = re.compile(r"^(?:https?:)?//([^/]+)", re.IGNORECASE)
_NUMERIC = re.compile(r"^\d+$")

#: Characters a title uses to join a page name to the site name. One of
#: these at either end means a piece of the title rendered empty.
SEPARATORS = "-\u2013\u2014|\u00b7:«»"


@dataclass(frozen=True)
class Finding:
    """One problem, at one place, in one page."""

    rule: str
    severity: str
    message: str
    line: int
    detail: str = ""

    @property
    def is_error(self):
        return self.severity == "error"

    def __str__(self):
        where = f"line {self.line}" if self.line else "page"
        tail = f" [{self.detail}]" if self.detail else ""
        return f"{self.severity:7} {self.rule:22} {where:9} {self.message}{tail}"


def _host(url):
    match = _ABSOLUTE_URL.match((url or "").strip())
    return match.group(1).lower() if match else None


def _trim(text, limit=70):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------
# Rules. Each takes the parsed document and yields Findings.
# --------------------------------------------------------------------------


def check_page_title(doc, site_name=None):
    """
    WCAG 2.4.2: a page needs a title that describes it.

    The error is a title that begins or ends with a separator. That is the
    unambiguous signal a template left a hole where the page's own name should
    be, and it is what caught base_2026.html reading ``{{ seo_title }}`` instead
    of ``{{ page.seo_title }}``: every page on the live theme shipped a title of
    " - PyCon Nigeria 2026" and nothing else.

    A title equal to the site name is left alone -- for a homepage that is right.
    """
    title = doc.first("title")
    if title is None:
        yield Finding("page-title", "error", "No <title> element.", 0)
        return

    text = " ".join(title.text_content().split())
    if not text:
        yield Finding("page-title", "error", "<title> is empty.", title.line)
        return

    if text[0] in SEPARATORS or text[-1] in SEPARATORS:
        yield Finding(
            "page-title",
            "error",
            "<title> starts or ends with a separator, so the page's own name is "
            "missing from it.",
            title.line,
            _trim(text),
        )
        return

    if site_name and text.count(site_name) > 1:
        yield Finding(
            "title-repeats-site",
            "warning",
            "Site name appears twice in <title>: the page template adds it and so "
            "does the suffix in the base template.",
            title.line,
            _trim(text),
        )


def check_html_lang(doc):
    """WCAG 3.1.1: the page's language, so a screen reader picks the right voice."""
    html = doc.first("html")
    if html is None:
        return
    if not (html.get("lang") or "").strip():
        yield Finding("html-lang", "error", "<html> has no lang attribute.", html.line)


def check_landmarks(doc):
    """
    One main landmark, and a named nav when there is more than one.

    Without <main> there is nowhere for a skip link to go and no way for a screen
    reader to jump past the header (WCAG 1.3.1, 2.4.1).
    """
    mains = [n for n in doc.iter("main") if n.get("role") != "presentation"]
    mains += [n for n in doc.iter("div", "section") if n.get("role") == "main"]
    if not mains:
        yield Finding("main-landmark", "error", "No <main> landmark on the page.", 0)
    elif len(mains) > 1:
        yield Finding(
            "main-landmark",
            "error",
            f"{len(mains)} main landmarks; there must be exactly one.",
            mains[1].line,
        )

    navs = list(doc.iter("nav"))
    if len(navs) > 1:
        for nav in navs:
            if not (nav.get("aria-label") or nav.get("aria-labelledby")):
                yield Finding(
                    "nav-label",
                    "warning",
                    "Several <nav> landmarks and this one is unnamed, so they are "
                    "indistinguishable in a landmark list.",
                    nav.line,
                )


def check_skip_link(doc):
    """
    WCAG 2.4.1: a way past the navigation.

    Checks the target exists, because a skip link pointing at a missing id is
    worse than none -- it looks handled and silently does nothing.
    """
    body = doc.first("body")
    if body is None:
        return
    ids = {n.get("id") for n in doc.walk() if n.get("id")}

    first_focusable = None
    for node in body.walk():
        if node is not body and node.is_focusable():
            first_focusable = node
            break

    if first_focusable is None:
        return

    href = (first_focusable.get("href") or "").strip()
    if not href.startswith("#"):
        yield Finding(
            "skip-link",
            "error",
            "The first focusable element is not a skip link, so a keyboard user "
            "tabs the whole navigation before reaching the content.",
            first_focusable.line,
            f"<{first_focusable.tag}> {_trim(first_focusable.accessible_name())}",
        )
        return

    target = href[1:]
    if target and target not in ids:
        yield Finding(
            "skip-link",
            "error",
            f"Skip link points at #{target}, which is not on the page.",
            first_focusable.line,
        )


def check_images(doc):
    """
    Alt text (WCAG 1.1.1) and the two image attributes that cost real money:
    intrinsic size, whose absence shifts the layout as images arrive, and
    ``loading``, whose absence downloads every image before first paint.
    """
    for img in doc.iter("img"):
        if not img.has("alt"):
            yield Finding(
                "img-alt",
                "error",
                "<img> has no alt attribute. Use alt=\"\" if it is decorative.",
                img.line,
                _trim(img.get("src")),
            )

        for attr in ("width", "height"):
            value = (img.get(attr) or "").strip()
            if value and not _NUMERIC.match(value):
                yield Finding(
                    "bad-dimension",
                    "error",
                    f'{attr}="{value}" is not a number, so the browser ignores it '
                    f"and reserves no space.",
                    img.line,
                    _trim(img.get("src")),
                )

        has_size = _NUMERIC.match((img.get("width") or "").strip() or "x") and _NUMERIC.match(
            (img.get("height") or "").strip() or "x"
        )
        if not has_size:
            yield Finding(
                "img-dimensions",
                "warning",
                "No width and height, so this image shifts the layout as it loads.",
                img.line,
                _trim(img.get("src")),
            )

        if not img.has("loading"):
            yield Finding(
                "img-loading",
                "warning",
                'No loading attribute. Use loading="lazy" below the fold, '
                '"eager" with fetchpriority="high" for a hero.',
                img.line,
                _trim(img.get("src")),
            )


def check_control_names(doc):
    """
    WCAG 4.1.2: every control needs a name.

    The failure this catches is an icon-only button: visually obvious, and
    announced as "button" with nothing else.
    """
    for node in doc.iter("a", "button"):
        if node.tag == "a" and not (node.get("href") or "").strip():
            continue
        if node.get("aria-hidden") == "true":
            continue
        if not node.accessible_name():
            yield Finding(
                "control-name",
                "error",
                f"<{node.tag}> has no accessible name: no text, no aria-label, no titled image.",
                node.line,
                _trim(node.get("href") or node.get("class")),
            )


def check_field_labels(doc):
    """WCAG 3.3.2: a form field needs a label a screen reader can find."""
    labelled_ids = {
        (n.get("for") or "").strip()
        for n in doc.iter("label")
        if (n.get("for") or "").strip()
    }

    for field in doc.iter("input", "select", "textarea"):
        field_type = (field.get("type") or "text").lower()
        if field.tag == "input" and field_type in UNLABELLED_INPUT_TYPES:
            continue
        if _hidden_from_assistive_tech(field):
            # Not in the accessibility tree, so a label would never be read. A
            # spam honeypot is the legitimate case; a mistake here is reported by
            # check_aria_hidden_focusable instead, which is the real finding.
            continue
        if field.get("aria-label") or field.get("aria-labelledby") or field.get("title"):
            continue
        if (field.get("id") or "").strip() in labelled_ids:
            continue
        # A field wrapped in its own label needs no `for`.
        ancestor = field.parent
        wrapped = False
        while ancestor is not None:
            if ancestor.tag == "label":
                wrapped = True
                break
            ancestor = ancestor.parent
        if wrapped:
            continue
        yield Finding(
            "field-label",
            "error",
            f"<{field.tag}> has no label. A placeholder is not a label: it "
            f"disappears on focus.",
            field.line,
            _trim(field.get("name") or field.get("id")),
        )


def _hidden_from_assistive_tech(node):
    """True when this element or an ancestor is aria-hidden."""
    current = node
    while current is not None:
        if current.get("aria-hidden") == "true":
            return True
        current = current.parent
    return False


def check_dead_links(doc):
    """
    A link that goes nowhere. Announced as a link, moves focus, does nothing --
    and in this codebase it is usually a to-do somebody forgot (WCAG 2.4.4).
    """
    for link in doc.iter("a"):
        href = (link.get("href") or "").strip()
        if href in ("#", ""):
            if link.has("@click") or link.has("x-on:click"):
                continue  # A scripted control; noted by control-role instead.
            yield Finding(
                "dead-link",
                "error",
                f'href="{href}" goes nowhere.',
                link.line,
                _trim(link.accessible_name()),
            )


def check_duplicate_ids(doc):
    """
    Duplicate ids break label association and aria references, and they break
    them silently -- the first match wins and the rest of the page is a lie.
    """
    seen = {}
    for node in doc.walk():
        node_id = (node.get("id") or "").strip()
        if not node_id:
            continue
        if node_id in seen:
            yield Finding(
                "duplicate-id",
                "error",
                f'id="{node_id}" is used more than once (first at line {seen[node_id]}).',
                node.line,
            )
        else:
            seen[node_id] = node.line


def check_aria_hidden_focusable(doc):
    """
    ``aria-hidden="true"`` around something focusable strands a keyboard user on
    an element their screen reader refuses to describe (WCAG 4.1.2).
    """
    for node in doc.walk():
        if node.get("aria-hidden") != "true":
            continue
        for descendant in node.walk():
            if descendant is not node and descendant.is_focusable():
                yield Finding(
                    "aria-hidden-focusable",
                    "error",
                    f'aria-hidden="true" contains a focusable <{descendant.tag}>.',
                    descendant.line,
                )
                break


def check_viewport(doc):
    """WCAG 1.4.4: do not stop people zooming. Low vision is very common."""
    for meta in doc.iter("meta"):
        if (meta.get("name") or "").lower() != "viewport":
            continue
        content = (meta.get("content") or "").lower()
        if "user-scalable=no" in content.replace(" ", ""):
            yield Finding(
                "viewport-zoom", "error", "Viewport blocks zooming (user-scalable=no).", meta.line
            )
        match = re.search(r"maximum-scale=([\d.]+)", content.replace(" ", ""))
        if match and float(match.group(1)) < 2:
            yield Finding(
                "viewport-zoom",
                "error",
                f"Viewport caps zoom at {match.group(1)}; 2 is the minimum.",
                meta.line,
            )


def check_blocking_scripts(doc):
    """
    A script in <head> with neither defer, async nor type=module stops the parser
    until it has downloaded and run. On a slow connection that is the whole
    time-to-first-paint.
    """
    head = doc.first("head")
    if head is None:
        return
    for script in head.iter("script"):
        if not (script.get("src") or "").strip():
            continue
        if script.has("defer") or script.has("async"):
            continue
        if (script.get("type") or "").lower() == "module":
            continue
        yield Finding(
            "blocking-script",
            "error",
            "Script in <head> with no defer/async blocks the first paint.",
            script.line,
            _trim(script.get("src")),
        )


#: Elements that fetch a subresource, and the attribute holding its URL. Only
#: these count for the third-party rule: an <a> to another site is a link, not a
#: request on this page's critical path.
SUBRESOURCE_ATTRS = {
    "script": "src",
    "img": "src",
    "iframe": "src",
    "source": "src",
    "video": "src",
    "audio": "src",
    "embed": "src",
    "track": "src",
    "link": "href",
}

#: Hosts that only exist on a developer's machine. A link to one of these in
#: production sends the visitor to their own computer.
DEV_HOST_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", ".local:", "testserver")


def check_third_party_assets(doc, internal_hosts=()):
    """
    Every off-site asset is a DNS lookup, a TLS handshake and someone else's
    uptime. Fine for fonts; not fine for a JavaScript framework the repo already
    ships in node_modules.
    """
    internal = {h.lower() for h in internal_hosts}
    for node in doc.walk():
        attr = SUBRESOURCE_ATTRS.get(node.tag)
        if not attr:
            continue
        host = _host(node.get(attr))
        if not host or host in internal or host in ALLOWED_EXTERNAL_HOSTS:
            continue
        yield Finding(
            "third-party-asset",
            "warning",
            f"<{node.tag}> loads from {host}.",
            node.line,
            _trim(node.get(attr)),
        )


def check_dev_urls(doc):
    """
    A development URL that reached the rendered page.

    The 2026 navigation menu is built from CMS values, and somebody entered them
    as absolute http://127.0.0.1:8000/... links on their own machine. Every one of
    those points a live visitor at their own computer. Nothing in the codebase
    could have caught it; a check over the rendered page can.
    """
    for node in doc.walk():
        for attr in ("href", "src", "action", "content"):
            value = (node.get(attr) or "").strip()
            if not value:
                continue
            host = _host(value)
            haystack = (host or value).lower()
            if any(marker in haystack for marker in DEV_HOST_MARKERS):
                yield Finding(
                    "dev-url",
                    "error",
                    f"<{node.tag} {attr}> points at a development host.",
                    node.line,
                    _trim(value),
                )
                break


def check_heading_order(doc):
    """
    WCAG 1.3.1. Headings are how a screen reader user skims; one h1 naming the
    page and no skipped levels is what makes that work.
    """
    main = doc.first("main") or doc.first("body")
    if main is None:
        return
    headings = [n for n in main.iter("h1", "h2", "h3", "h4", "h5", "h6")]
    h1s = [h for h in headings if h.tag == "h1"]
    if not h1s:
        yield Finding("heading-order", "warning", "No <h1> in the main content.", main.line)
    elif len(h1s) > 1:
        yield Finding(
            "heading-order",
            "warning",
            f"{len(h1s)} <h1> elements; one names the page.",
            h1s[1].line,
        )

    previous = 0
    for heading in headings:
        level = int(heading.tag[1])
        if previous and level > previous + 1:
            yield Finding(
                "heading-order",
                "warning",
                f"<{heading.tag}> follows <h{previous}>, skipping a level.",
                heading.line,
                _trim(heading.text_content()),
            )
        previous = level


def check_blank_target(doc):
    """A new tab should not hand the opener to the page it opens."""
    for link in doc.iter("a"):
        if (link.get("target") or "").lower() != "_blank":
            continue
        rel = (link.get("rel") or "").lower()
        if "noopener" not in rel and "noreferrer" not in rel:
            yield Finding(
                "blank-noopener",
                "warning",
                'target="_blank" without rel="noopener".',
                link.line,
                _trim(link.get("href")),
            )


#: Every rule, in report order. Rules taking extra arguments are called by name
#: in ``audit_html`` rather than iterated, so this stays a plain list.
RULES = (
    check_html_lang,
    check_dev_urls,
    check_landmarks,
    check_skip_link,
    check_images,
    check_control_names,
    check_field_labels,
    check_dead_links,
    check_duplicate_ids,
    check_aria_hidden_focusable,
    check_viewport,
    check_blocking_scripts,
    check_heading_order,
    check_blank_target,
)


def audit_html(html, site_name="PyCon Nigeria", internal_hosts=()):
    """
    Every finding in ``html``, errors first then warnings, each group by line.

    ``site_name`` lets the title rule tell "the page has a name" from "the page
    is named after the site", which is the difference the live site got wrong.
    ``internal_hosts`` are this site's own names, so an absolute link home is not
    reported as a third-party request.
    """
    doc = parse(html)
    findings = list(check_page_title(doc, site_name=site_name))
    findings.extend(check_third_party_assets(doc, internal_hosts=internal_hosts))
    for rule in RULES:
        findings.extend(rule(doc))
    order = {"error": 0, "warning": 1}
    findings.sort(key=lambda f: (order.get(f.severity, 2), f.line, f.rule))
    return findings


def errors(findings):
    return [f for f in findings if f.is_error]


def collapse(findings):
    """
    Fold repeats of the same problem into one line each, with a count.

    A single mistake in shared chrome -- eight footer links all pointing at a
    development host, say -- otherwise fills the report and buries everything
    else. Reports collapse; tests do not, because a test wants every instance.
    """
    grouped = {}
    order = []
    for finding in findings:
        key = (finding.rule, finding.severity, finding.message, finding.detail)
        if key not in grouped:
            grouped[key] = [finding, 0]
            order.append(key)
        grouped[key][1] += 1

    result = []
    for key in order:
        finding, count = grouped[key]
        if count == 1:
            result.append(finding)
            continue
        result.append(
            Finding(
                finding.rule,
                finding.severity,
                f"{finding.message} ({count} occurrences, first at line {finding.line})",
                finding.line,
                finding.detail,
            )
        )
    return result


def summarise(findings):
    """``{rule: count}`` for errors only, for a one-line report."""
    counts = {}
    for finding in errors(findings):
        counts[finding.rule] = counts.get(finding.rule, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
