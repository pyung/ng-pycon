"""
A small HTML tree, enough to ask accessibility questions of a rendered page.

Deliberately stdlib-only. The alternatives were a headless browser (axe-core via
Playwright), which needs a browser in CI and a lot of minutes, or BeautifulSoup,
which is another dependency to keep current. Neither is warranted: the checks
that matter here -- is there a title, does every image have alt text, is every
form field labelled -- are answerable from the markup, and a check that runs in
two seconds on every push is worth more than a thorough one nobody runs.

What this cannot see is anything only a browser knows: computed colour contrast,
focus order once JavaScript has moved things, or whether a control is actually
reachable. Those still need a person and a real audit. See audit.py.
"""

from html.parser import HTMLParser

#: Elements that never have a closing tag, so the parser must not nest into them.
VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

#: Elements whose content is text, not markup. HTMLParser already treats script
#: and style as CDATA; listing them keeps the tree shape honest.
RAW_TEXT_ELEMENTS = frozenset({"script", "style"})

#: Anything a keyboard can land on without an explicit tabindex.
NATURALLY_FOCUSABLE = frozenset(
    {"a", "area", "button", "input", "select", "textarea", "iframe", "summary"}
)


class Node:
    """One element. ``tag`` is None for the synthetic document root."""

    __slots__ = ("tag", "attrs", "line", "children", "parent", "text")

    def __init__(self, tag=None, attrs=None, line=0, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.line = line
        self.children = []
        self.parent = parent
        self.text = ""

    def get(self, name, default=None):
        """An attribute's value. Names are lower-cased by the parser."""
        return self.attrs.get(name, default)

    def has(self, name):
        return name in self.attrs

    def walk(self):
        """This node, then every descendant, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()

    def iter(self, *tags):
        """Descendants with any of ``tags``, excluding this node."""
        wanted = frozenset(tags)
        for node in self.walk():
            if node is not self and node.tag in wanted:
                yield node

    def first(self, *tags):
        for node in self.iter(*tags):
            return node
        return None

    def text_content(self):
        """
        Visible text, skipping script and style, and skipping anything hidden
        from the accessibility tree -- an aria-hidden span contributes no name.
        """
        if self.tag in RAW_TEXT_ELEMENTS:
            return ""
        if self.get("aria-hidden") == "true":
            return ""
        parts = [self.text]
        for child in self.children:
            parts.append(child.text_content())
        return " ".join(p for p in parts if p).strip()

    def accessible_name(self):
        """
        A usable approximation of the accessible name.

        Real name computation is a long algorithm; this covers the cases that
        actually go wrong in templates: an icon-only button with no aria-label,
        and an image link whose only content is an img with empty alt.
        """
        for attr in ("aria-label", "title"):
            value = (self.get(attr) or "").strip()
            if value:
                return value
        if self.get("aria-labelledby"):
            # Resolving the reference needs the whole document; treat the
            # presence of the attribute as a name and let a person check it.
            return "(aria-labelledby)"
        text = self.text_content()
        if text:
            return text
        # An image's alt text names the link or button that wraps it.
        for descendant in self.iter("img"):
            alt = (descendant.get("alt") or "").strip()
            if alt:
                return alt
        # A dynamic Alpine binding counts: :aria-label is set before paint.
        for attr in self.attrs:
            if attr in (":aria-label", "x-bind:aria-label"):
                return "(bound aria-label)"
        return ""

    def is_focusable(self):
        if self.get("disabled") is not None:
            return False
        tabindex = (self.get("tabindex") or "").strip()
        if tabindex.startswith("-"):
            # tabindex="-1" takes an element out of the tab order even when its
            # tag would normally be focusable. A spam honeypot relies on this.
            return False
        if self.tag in NATURALLY_FOCUSABLE:
            # A link is only focusable with an href.
            if self.tag == "a":
                return bool((self.get("href") or "").strip())
            if self.tag == "input":
                return (self.get("type") or "").lower() != "hidden"
            return True
        return bool(tabindex)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<{self.tag} line={self.line}>"


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self._stack = [self.root]

    @property
    def _current(self):
        return self._stack[-1]

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        # A repeated attribute keeps the first value, as browsers do.
        collected = {}
        for name, value in attrs:
            collected.setdefault(name.lower(), "" if value is None else value)
        node = Node(tag, collected, line, self._current)
        self._current.children.append(node)
        if tag not in VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        line = self.getpos()[0]
        collected = {}
        for name, value in attrs:
            collected.setdefault(name.lower(), "" if value is None else value)
        self._current.children.append(Node(tag, collected, line, self._current))

    def handle_endtag(self, tag):
        # Close back to the matching open tag when one exists. Unbalanced markup
        # is common in hand-written templates and must not lose the rest of the
        # document: an unmatched close is ignored rather than popping blindly.
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data):
        stripped = data.strip()
        if stripped:
            node = self._current
            node.text = f"{node.text} {stripped}".strip() if node.text else stripped


def parse(html):
    """The document root of ``html``. Never raises on malformed markup."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root
