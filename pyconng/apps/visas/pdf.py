"""
A PDF writer, written rather than installed.

Nothing in ``requirements.txt`` can make a PDF, and nothing can be added: this
deployment has no package index reachable from it, and the two usual answers cost
more than they look. ReportLab is a C extension; WeasyPrint wants Pango, Cairo and
HarfBuzz present in the image. Either means a rebuilt image and a deploy, and a
deploy is the thing this team has said is expensive. So this module writes the file
directly, the same reasoning as ``tickets/qr.py``.

The scope is deliberately one document: a formal letter on a letterhead. Flowed
paragraphs with real line breaking, a rule, a label/value block, a signature block,
page breaks, and an optional JPEG logo. No tables, no colour, no vector drawing
beyond a horizontal rule.

PDF is a plain-text container with a byte-offset index, which is the only part that
is fiddly: every object's position in the file is recorded in the cross-reference
table, so the file is assembled once and measured as it is written.

Fonts are the base-14 Type 1 faces every reader has built in, so nothing is
embedded and the output stays a few kilobytes. That fixes the character set at
WinAnsi: characters outside it (a Yoruba 'ẹ', say) are folded to their nearest
Latin form for *drawing only* -- see :func:`_winansi`. The stored record always
keeps what the applicant typed. This matters less than it sounds for the passport
name, which a machine-readable passport spells in A-Z anyway, and the fold is
explicit rather than a silent question mark in the middle of somebody's name.
"""

import unicodedata
import zlib

#: A4 in points, which is what an embassy in Lagos or Berlin expects to print.
A4 = (595.28, 841.89)

HELVETICA = "Helvetica"
HELVETICA_BOLD = "Helvetica-Bold"
HELVETICA_OBLIQUE = "Helvetica-Oblique"

#: Advance widths for the printable ASCII range, in 1/1000 em, from the Adobe
#: metrics for the base-14 faces. Needed for line breaking: without real widths,
#: wrapping is a guess, and a formal letter with ragged over-long lines reads as
#: something run off in a hurry.
_ASCII_WIDTHS = {
    HELVETICA: (
        "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 "
        "556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 "
        "1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 "
        "667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 "
        "333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 "
        "556 556 333 500 278 556 500 722 500 500 500 334 260 334 584"
    ),
    HELVETICA_BOLD: (
        "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 "
        "556 556 556 556 556 556 556 556 556 556 333 333 584 584 584 611 "
        "975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 "
        "667 778 722 667 611 722 667 944 667 667 611 333 278 333 584 556 "
        "333 556 611 556 611 556 333 611 611 278 278 556 278 889 611 611 "
        "611 611 389 556 333 611 556 778 556 556 500 389 280 389 584"
    ),
}
# The oblique face shares its metrics with the roman one.
_ASCII_WIDTHS[HELVETICA_OBLIQUE] = _ASCII_WIDTHS[HELVETICA]

_WIDTHS = {
    name: {chr(32 + i): int(w) for i, w in enumerate(table.split())}
    for name, table in _ASCII_WIDTHS.items()
}

#: Latin-1 characters that WinAnsi has and ASCII does not, kept as themselves
#: rather than folded. Their widths match the letter they are built on, which is
#: true of these faces, so the width lookup decomposes instead of tabulating them.
_KEEP = set("\xa0¡¢£¤¥¦§¨©ª«¬­®¯°±²³´µ¶·¸¹º»¼½¾¿")


def _fold(char):
    """The nearest drawable character, or '' if there is no sensible one."""
    decomposed = unicodedata.normalize("NFKD", char)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    try:
        base.encode("cp1252")
    except UnicodeEncodeError:
        return ""
    return base


def _winansi(text):
    """
    ``text`` reduced to what the base-14 fonts can draw.

    Accents survive where WinAnsi has the composed character (é, ü, ñ). Where it
    does not (ẹ, ṣ, ọ -- ordinary in Yoruba), the mark is dropped and the letter
    kept, which is what a passport's machine-readable line does too. Anything with
    no Latin form at all is dropped rather than drawn as a box.
    """
    out = []
    for char in text:
        if char in _KEEP:
            out.append(char)
            continue
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            out.append(_fold(char))
        else:
            out.append(char)
    return "".join(out)


def text_width(text, font, size):
    """Width of ``text`` in points, for line breaking and centring."""
    widths = _WIDTHS[font]
    total = 0
    for char in _winansi(text):
        if char in widths:
            total += widths[char]
            continue
        # A composed Latin-1 letter: its width is the width of the letter it is
        # built on in these faces.
        base = _fold(char) or "n"
        total += widths.get(base[:1], 556)
    return total * size / 1000.0


def wrap(text, font, size, max_width):
    """
    ``text`` broken into lines that fit ``max_width``.

    Breaks on spaces, and only splits inside a word when a single word cannot fit
    at all -- a passport number or a long URL, which must be shown even if it has
    to be broken.
    """
    lines = []
    for hard_line in text.split("\n"):
        words = hard_line.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if text_width(candidate, font, size) <= max_width or not current:
                if text_width(candidate, font, size) > max_width and not current:
                    # One unbreakable word wider than the column.
                    part = ""
                    for char in word:
                        if text_width(part + char, font, size) > max_width and part:
                            lines.append(part)
                            part = char
                        else:
                            part += char
                    current = part
                    continue
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _escape(text):
    """Escape a string for a PDF literal."""
    return (
        _winansi(text)
        .replace("\\", r"\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
    )


class Document:
    """
    A flowed, single-column document.

    Content is appended in reading order and the cursor moves down the page,
    breaking to a new one when it runs out of room. Coordinates are only exposed
    where a letter genuinely needs them (the signature block), because a letter is
    a sequence of paragraphs, not a canvas.
    """

    def __init__(self, title="", author="PyCon Nigeria", size=A4, margin=64):
        self.width, self.height = size
        self.margin = margin
        self.title = title
        self.author = author
        self._pages = []
        self._images = []
        self._new_page()

    # -- page management ---------------------------------------------------

    def _new_page(self):
        self._ops = []
        self._pages.append(self._ops)
        self.y = self.height - self.margin

    @property
    def column_width(self):
        return self.width - 2 * self.margin

    def _ensure(self, needed):
        """Break to a new page if ``needed`` points will not fit."""
        if self.y - needed < self.margin:
            self._new_page()
            return True
        return False

    # -- content ----------------------------------------------------------

    def spacer(self, points):
        self.y -= points

    def rule(self, width=0.75, gap_above=6, gap_below=10, indent=0):
        self._ensure(gap_above + gap_below + width)
        self.y -= gap_above
        self._ops.append(
            f"{width:.2f} w {self.margin + indent:.2f} {self.y:.2f} m "
            f"{self.width - self.margin:.2f} {self.y:.2f} l S"
        )
        self.y -= gap_below

    def text_line(self, text, font=HELVETICA, size=10.5, x=None, leading=None):
        """One line, no wrapping. Returns the baseline y it was drawn at."""
        leading = leading or size * 1.35
        self._ensure(leading)
        self.y -= leading
        x = self.margin if x is None else x
        self._ops.append(
            f"BT /{self._font_key(font)} {size:.2f} Tf "
            f"{x:.2f} {self.y:.2f} Td ({_escape(text)}) Tj ET"
        )
        return self.y

    def centred(self, text, font=HELVETICA, size=10.5, leading=None):
        x = (self.width - text_width(text, font, size)) / 2
        return self.text_line(text, font=font, size=size, x=x, leading=leading)

    def paragraph(self, text, font=HELVETICA, size=10.5, leading=None, space_after=8):
        """A wrapped paragraph. Blank input draws nothing, including no space."""
        if not text:
            return
        leading = leading or size * 1.45
        for line in wrap(text, font, size, self.column_width):
            self.text_line(line, font=font, size=size, leading=leading)
        self.y -= space_after

    def heading(self, text, size=13, space_before=10, space_after=6):
        self.y -= space_before
        for line in wrap(text, HELVETICA_BOLD, size, self.column_width):
            self.text_line(line, font=HELVETICA_BOLD, size=size)
        self.y -= space_after

    def fields(self, rows, label_width=150, size=10.5, leading=None, space_after=10):
        """
        A block of label/value pairs, labels bold in their own column.

        Values wrap within what is left, so a long address does not run off the
        page or collide with the next label.
        """
        leading = leading or size * 1.45
        value_width = self.column_width - label_width
        for label, value in rows:
            lines = wrap(str(value or "—"), HELVETICA, size, value_width)
            self._ensure(leading * len(lines))
            first = True
            for line in lines:
                self.y -= leading
                if first:
                    self._ops.append(
                        f"BT /{self._font_key(HELVETICA_BOLD)} {size:.2f} Tf "
                        f"{self.margin:.2f} {self.y:.2f} Td ({_escape(label)}) Tj ET"
                    )
                    first = False
                self._ops.append(
                    f"BT /{self._font_key(HELVETICA)} {size:.2f} Tf "
                    f"{self.margin + label_width:.2f} {self.y:.2f} Td "
                    f"({_escape(line)}) Tj ET"
                )
        self.y -= space_after

    def image(self, jpeg_bytes, width, height, x=None):
        """
        Place a JPEG. Passed through as a DCTDecode stream, not re-encoded.

        JPEG only, because PDF carries JPEG data verbatim -- a PNG would mean
        implementing its filters here. Callers hand in a JPEG rendition, which
        Wagtail produces from whatever was uploaded.
        """
        self._ensure(height + 4)
        self.y -= height
        x = self.margin if x is None else x
        name = f"Im{len(self._images) + 1}"
        self._images.append((name, jpeg_bytes))
        self._ops.append(
            f"q {width:.2f} 0 0 {height:.2f} {x:.2f} {self.y:.2f} cm /{name} Do Q"
        )
        self.y -= 4

    def _font_key(self, font):
        return {
            HELVETICA: "F1",
            HELVETICA_BOLD: "F2",
            HELVETICA_OBLIQUE: "F3",
        }[font]

    # -- output -----------------------------------------------------------

    def render(self):
        """The finished file as bytes."""
        objects = []           # 1-indexed object bodies, as bytes

        def add(body):
            objects.append(body)
            return len(objects)

        font_objs = {
            key: add(
                b"<< /Type /Font /Subtype /Type1 /BaseFont /"
                + name.encode("ascii")
                + b" /Encoding /WinAnsiEncoding >>"
            )
            for key, name in (
                (b"F1", HELVETICA), (b"F2", HELVETICA_BOLD), (b"F3", HELVETICA_OBLIQUE)
            )
        }

        image_objs = {}
        for name, data in self._images:
            width, height = _jpeg_size(data)
            image_objs[name] = add(
                b"<< /Type /XObject /Subtype /Image /Width "
                + str(width).encode()
                + b" /Height " + str(height).encode()
                + b" /ColorSpace /DeviceRGB /BitsPerComponent 8 "
                b"/Filter /DCTDecode /Length " + str(len(data)).encode()
                + b" >>\nstream\n" + data + b"\nendstream"
            )

        pages_obj = len(objects) + 1          # reserved; filled in below
        add(b"")                              # placeholder for /Pages

        page_objs = []
        for ops in self._pages:
            stream = zlib.compress("\n".join(ops).encode("cp1252", "replace"))
            content = add(
                b"<< /Length " + str(len(stream)).encode()
                + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream"
            )
            resources = (
                b"<< /Font << " + b" ".join(
                    b"/" + key + b" " + str(num).encode() + b" 0 R"
                    for key, num in font_objs.items()
                ) + b" >>"
            )
            if image_objs:
                resources += b" /XObject << " + b" ".join(
                    b"/" + name.encode() + b" " + str(num).encode() + b" 0 R"
                    for name, num in image_objs.items()
                ) + b" >>"
            resources += b" >>"
            page_objs.append(add(
                b"<< /Type /Page /Parent " + str(pages_obj).encode() + b" 0 R "
                b"/MediaBox [0 0 " + f"{self.width:.2f} {self.height:.2f}".encode()
                + b"] /Resources " + resources
                + b" /Contents " + str(content).encode() + b" 0 R >>"
            ))

        objects[pages_obj - 1] = (
            b"<< /Type /Pages /Kids [ "
            + b" ".join(str(n).encode() + b" 0 R" for n in page_objs)
            + b" ] /Count " + str(len(page_objs)).encode() + b" >>"
        )

        catalog = add(
            b"<< /Type /Catalog /Pages " + str(pages_obj).encode() + b" 0 R >>"
        )
        info = add(
            b"<< /Title (" + _escape(self.title).encode("cp1252", "replace")
            + b") /Author (" + _escape(self.author).encode("cp1252", "replace")
            + b") /Producer (PyCon Nigeria) >>"
        )

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"

        xref_at = len(out)
        out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode("ascii")
        out += (
            b"trailer\n<< /Size " + str(len(objects) + 1).encode()
            + b" /Root " + str(catalog).encode() + b" 0 R"
            + b" /Info " + str(info).encode() + b" 0 R >>\n"
            b"startxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
        )
        return bytes(out)


def _jpeg_size(data):
    """
    ``(width, height)`` from a JPEG's start-of-frame marker.

    Read rather than trusted from elsewhere, because the /Width and /Height in the
    PDF must match the actual image or readers show it stretched or not at all.
    """
    index = 2
    while index < len(data) - 9:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = int.from_bytes(data[index + 2:index + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return width, height
        index += 2 + length
    raise ValueError("Not a JPEG, or no start-of-frame marker found.")
