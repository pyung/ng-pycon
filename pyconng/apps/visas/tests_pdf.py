"""
Tests for the hand-written PDF writer.

The writer produces a file format, so the only honest test is to read the file back
and check it says what it should -- the same approach as the QR encoder, where an
independently written decoder caught a real bug the generator was blind to.

Nothing here depends on a PDF tool being installed. The parsing below is deliberate
and separate from the writing code: it finds objects by their byte offsets in the
cross-reference table rather than by searching, so a wrong offset fails the test
instead of being routed around. A wrong offset is exactly the bug a reader would
hit and the writer would never notice.
"""

import re
import zlib

from django.test import SimpleTestCase

from visas import pdf


def parse(data):
    """
    A minimal reader: follow the xref, return ``{object number: body bytes}``.

    Refuses anything inconsistent rather than recovering from it, because a
    tolerant reader here would hide the class of bug this exists to catch.
    """
    assert data.startswith(b"%PDF-"), "no PDF header"
    assert data.rstrip().endswith(b"%%EOF"), "no EOF marker"

    match = re.search(rb"startxref\s+(\d+)\s+%%EOF", data)
    assert match, "no startxref"
    xref_at = int(match.group(1))
    assert data[xref_at:xref_at + 4] == b"xref", "startxref does not point at the table"

    header = re.match(rb"xref\s+(\d+)\s+(\d+)\s+", data[xref_at:])
    assert header, "malformed xref header"
    first, count = int(header.group(1)), int(header.group(2))
    assert first == 0, "the table should start at object 0"

    body_at = xref_at + header.end()
    entries = []
    for index in range(count):
        row = data[body_at + index * 20: body_at + (index + 1) * 20]
        assert len(row) == 20, f"short xref row {index}"
        entries.append((int(row[:10]), row[17:18]))

    assert entries[0][1] == b"f", "object 0 must be free"

    objects = {}
    for number, (offset, kind) in enumerate(entries):
        if kind != b"n":
            continue
        # The offset must land exactly on the object header. Off-by-a-few still
        # opens in a forgiving reader and is still wrong.
        expected = f"{number} 0 obj".encode()
        assert data[offset:offset + len(expected)] == expected, (
            f"object {number} is not at the offset the xref claims ({offset})"
        )
        body_start = offset + len(expected)
        # Find the end the way a reader does: a stream's length comes from its own
        # dictionary, never from searching for "endobj" -- compressed bytes contain
        # that sequence often enough, and a parser that searches quietly truncates
        # the object instead of failing.
        head_end = data.index(b"stream", body_start) if _has_stream(data, body_start) else -1
        if head_end == -1:
            end = data.index(b"endobj", body_start)
        else:
            length = int(
                re.search(rb"/Length\s+(\d+)", data[body_start:head_end]).group(1)
            )
            stream_at = head_end + len(b"stream\n")
            end = data.index(b"endobj", stream_at + length)
            assert data[stream_at + length: end].strip() == b"endstream", (
                f"object {number}'s stream does not end where /Length says"
            )
        objects[number] = data[body_start:end].strip()
    return objects


def _has_stream(data, body_start):
    """True when this object's dictionary is followed by a stream."""
    dict_end = data.index(b">>", body_start)
    return data[dict_end:dict_end + 12].lstrip(b">\r\n ").startswith(b"stream")


def trailer(data):
    raw = data[data.rindex(b"trailer"):]
    return {
        key.decode(): int(value)
        for key, value in re.findall(rb"/(Size|Root|Info)\s+(\d+)", raw)
    }


def page_text(data):
    """Every string drawn with Tj, in order, across all pages."""
    objects = parse(data)
    out = []
    for body in objects.values():
        if b"/FlateDecode" not in body or b"stream" not in body:
            continue
        length = int(re.search(rb"/Length\s+(\d+)", body).group(1))
        stream_at = body.index(b"stream") + len(b"stream\n")
        # No try/except: a stream that will not inflate is a bug in the writer, and
        # skipping it here is how the page that went missing stayed missing.
        content = zlib.decompress(body[stream_at:stream_at + length]).decode("cp1252")
        out.extend(re.findall(r"\((.*?)\) Tj", content))
    return out


class WidthTests(SimpleTestCase):
    def test_a_known_width(self):
        """
        Helvetica's 'A' is 667/1000 em, from the Adobe metrics. A table typed by
        hand deserves one value checked against the source.
        """
        self.assertAlmostEqual(pdf.text_width("A", pdf.HELVETICA, 1000), 667)

    def test_bold_is_wider_where_it_should_be(self):
        self.assertGreater(
            pdf.text_width("mmm", pdf.HELVETICA_BOLD, 12),
            pdf.text_width("mmm", pdf.HELVETICA, 12),
        )

    def test_width_scales_with_size(self):
        small = pdf.text_width("PyCon Nigeria", pdf.HELVETICA, 10)
        large = pdf.text_width("PyCon Nigeria", pdf.HELVETICA, 20)
        self.assertAlmostEqual(large, small * 2, places=6)

    def test_an_accented_letter_is_as_wide_as_its_base(self):
        self.assertEqual(
            pdf.text_width("é", pdf.HELVETICA, 12),
            pdf.text_width("e", pdf.HELVETICA, 12),
        )

    def test_the_empty_string_is_zero(self):
        self.assertEqual(pdf.text_width("", pdf.HELVETICA, 12), 0)


class EncodingTests(SimpleTestCase):
    def test_latin_1_accents_survive(self):
        self.assertEqual(pdf._winansi("Adéwálé Ñuñez"), "Adéwálé Ñuñez")

    def test_yoruba_marks_fold_to_the_base_letter(self):
        """
        WinAnsi has no ẹ or ṣ. Dropping the mark and keeping the letter is what a
        passport's machine-readable line does, and is better than a black box in
        the middle of somebody's name on an official document.
        """
        self.assertEqual(pdf._winansi("Ẹgbẹ Ṣola"), "Egbe Sola")

    def test_something_with_no_latin_form_is_dropped_not_drawn(self):
        self.assertEqual(pdf._winansi("Ada 中文 Lovelace"), "Ada  Lovelace")

    def test_parentheses_and_backslashes_are_escaped(self):
        self.assertEqual(pdf._escape(r"a (b) \c"), r"a \(b\) \\c")


class WrapTests(SimpleTestCase):
    def test_lines_fit_the_column(self):
        text = "The delegate named below is expected at the conference. " * 6
        lines = pdf.wrap(text, pdf.HELVETICA, 10.5, 400)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(pdf.text_width(line, pdf.HELVETICA, 10.5), 400)

    def test_no_word_is_lost_or_duplicated(self):
        text = "one two three four five six seven eight nine ten"
        lines = pdf.wrap(text, pdf.HELVETICA, 10, 60)
        self.assertEqual(" ".join(lines).split(), text.split())

    def test_an_unbreakable_word_is_split_rather_than_overflowing(self):
        """A passport number in a narrow column must be shown, even broken."""
        lines = pdf.wrap("A" * 80, pdf.HELVETICA, 10, 50)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(pdf.text_width(line, pdf.HELVETICA, 10), 50)

    def test_explicit_newlines_are_kept(self):
        lines = pdf.wrap("First line\nSecond line", pdf.HELVETICA, 10, 500)
        self.assertEqual(lines, ["First line", "Second line"])


class StructureTests(SimpleTestCase):
    def build(self):
        doc = pdf.Document(title="Invitation letter PYNG-2027-ABC123")
        doc.heading("PyCon Nigeria 2027")
        doc.paragraph("A paragraph of the letter, long enough to wrap. " * 8)
        doc.rule()
        doc.fields([("Passport number", "A01234567"), ("Nationality", "Kenyan")])
        return doc.render()

    def test_every_xref_offset_lands_on_its_object(self):
        """The one thing a PDF writer gets wrong that nothing else notices."""
        objects = parse(self.build())
        self.assertGreater(len(objects), 4)

    def test_the_trailer_is_consistent(self):
        data = self.build()
        objects = parse(data)
        info = trailer(data)
        self.assertEqual(info["Size"], len(objects) + 1)
        self.assertIn(info["Root"], objects)
        self.assertIn(info["Info"], objects)
        self.assertIn(b"/Type /Catalog", objects[info["Root"]])

    def test_the_catalog_points_at_a_pages_tree_that_counts_its_pages(self):
        data = self.build()
        objects = parse(data)
        root = objects[trailer(data)["Root"]]
        pages_num = int(re.search(rb"/Pages (\d+) 0 R", root).group(1))
        pages = objects[pages_num]
        kids = re.findall(rb"(\d+) 0 R", pages)
        count = int(re.search(rb"/Count (\d+)", pages).group(1))
        self.assertEqual(count, len(kids))
        for kid in kids:
            self.assertIn(b"/Type /Page", objects[int(kid)])

    def test_the_drawn_text_is_what_was_asked_for(self):
        drawn = " ".join(page_text(self.build()))
        self.assertIn("PyCon Nigeria 2027", drawn)
        self.assertIn("A01234567", drawn)
        self.assertIn("Kenyan", drawn)

    def test_content_that_overruns_starts_a_second_page(self):
        doc = pdf.Document()
        for index in range(120):
            doc.paragraph(f"Line {index} of a very long document that must paginate.")
        data = doc.render()
        objects = parse(data)
        page_count = sum(1 for body in objects.values() if b"/Type /Page " in body)
        self.assertGreater(page_count, 1)
        # And nothing was dropped at the break.
        drawn = " ".join(page_text(data))
        self.assertIn("Line 0 ", drawn + " ")
        self.assertIn("Line 119 ", drawn + " ")

    def test_fonts_are_referenced_not_embedded(self):
        """Base-14 only: an embedded font would multiply the file size for nothing."""
        data = self.build()
        self.assertIn(b"/BaseFont /Helvetica", data)
        self.assertNotIn(b"/FontFile", data)

    def test_the_title_reaches_the_document_info(self):
        data = self.build()
        info = parse(data)[trailer(data)["Info"]]
        self.assertIn(b"PYNG-2027-ABC123", info)

    def test_a_name_outside_winansi_does_not_corrupt_the_file(self):
        doc = pdf.Document(title="Ẹgbẹ")
        doc.paragraph("Ẹgbẹ Ṣola Adéwálé")
        data = doc.render()
        parse(data)
        self.assertIn("Egbe Sola Adéwálé", " ".join(page_text(data)))


class JpegTests(SimpleTestCase):
    #: The smallest valid JPEG this can be tested with: a 1x1 grey pixel, as
    #: produced by any encoder. Enough to exercise the size reader and the
    #: pass-through, without a fixture file.
    TINY = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300"
        + "08060607060508070707090909080a0c140d0c0b0b0c191213"
        + "0f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30"
        + "31343434 1f27393d38323c2e333432".replace(" ", "")
        + "ffc0000b080001000101011100"
        + "ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
        + "ffda0008010100003f00d2cf20"
        + "ffd9"
    )

    def test_the_size_is_read_from_the_start_of_frame(self):
        self.assertEqual(pdf._jpeg_size(self.TINY), (1, 1))

    def test_something_that_is_not_a_jpeg_is_refused(self):
        with self.assertRaises(ValueError):
            pdf._jpeg_size(b"\xff\xd8" + b"\x00" * 64)

    def test_an_image_is_carried_through_byte_for_byte(self):
        """
        DCTDecode means the JPEG travels verbatim. Re-encoding it would need an
        encoder, which is the dependency this module exists to avoid.
        """
        doc = pdf.Document()
        doc.image(self.TINY, 40, 40)
        data = doc.render()
        parse(data)
        self.assertIn(b"/Filter /DCTDecode", data)
        self.assertIn(self.TINY, data)
