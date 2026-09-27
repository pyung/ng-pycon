"""
Tests for the QR encoder.

A hand-written encoder needs more than "it produced a square". Three kinds of
check here:

* against the specification's own worked example -- the error-correction
  codewords for "01234567" and the published generator polynomial, which pin the
  Reed-Solomon arithmetic exactly;
* against published constants -- the sixteen format-information strings, the data
  module counts per version, and the coordinates of the first codeword;
* by reading a finished matrix back. The decoder below derives the reserved
  modules from coordinates listed out of the specification, and walks the data
  region with its own loop, rather than calling the encoder's functions. Sharing
  that logic would let a placement mistake pass in both directions.

What none of this proves is that a phone camera reads it, which is the one check
worth doing by hand before an event.
"""

from django.test import SimpleTestCase

from tickets import qr

#: ISO/IEC 18004 Annex I: "01234567" as a version 1-M symbol.
SPEC_DATA_CODEWORDS = [
    0x10, 0x20, 0x0C, 0x56, 0x61, 0x80, 0xEC, 0x11,
    0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11,
]
SPEC_EC_CODEWORDS = [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55]

#: The published format-information bit strings for error-correction level M.
PUBLISHED_FORMAT_STRINGS = {
    0: "101010000010010",
    1: "101000100100101",
    2: "101111001111100",
    3: "101101101001011",
    4: "100010111111001",
    5: "100000011001110",
    6: "100111110010111",
    7: "100101010100000",
}


# ---------------------------------------------------------------------------
# An independent reader, for the round trip
# ---------------------------------------------------------------------------

def reserved_modules(version):
    """
    Coordinates of every function-pattern module, listed from the specification.

    Written out rather than taken from qr.function_pattern_mask on purpose: this
    is the check on that function, so it must not be the same code.
    """
    size = qr.VERSIONS[version][0]
    reserved = set()

    # Finder patterns and their separators: an 8x8 square in each of three corners.
    for top, left in ((0, 0), (0, size - 8), (size - 8, 0)):
        for r in range(8):
            for c in range(8):
                reserved.add((top + r, left + c))

    # Timing patterns: all of row 6 and all of column 6.
    for i in range(size):
        reserved.add((6, i))
        reserved.add((i, 6))

    # Alignment pattern: a 5x5 square centred at 4 * version + 10, versions 2-6.
    centre = qr.ALIGNMENT_CENTRES[version]
    if centre is not None:
        for r in range(centre - 2, centre + 3):
            for c in range(centre - 2, centre + 3):
                reserved.add((r, c))

    # Format information: row 8 and column 8 beside the finder patterns, plus the
    # dark module.
    for i in range(9):
        reserved.add((8, i))
        reserved.add((i, 8))
    for i in range(8):
        reserved.add((8, size - 1 - i))
        reserved.add((size - 1 - i, 8))
    reserved.add((4 * version + 9, 8))
    return reserved


def read_data_bits(matrix, version):
    """
    The data bits of ``matrix``, in codeword order, with the mask removed.

    Walks the data region as the specification describes it: pairs of columns from
    the right edge, alternating upward and downward, skipping column 6.
    """
    size = qr.VERSIONS[version][0]
    reserved = reserved_modules(version)

    # Which mask was used, from the first copy of the format information.
    format_positions = (
        [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(i, 8) for i in range(5, -1, -1)]
    )
    raw = 0
    for row, col in format_positions:
        raw = (raw << 1) | matrix[row][col]
    unmasked = raw ^ qr.FORMAT_MASK
    ec_level = unmasked >> 13
    mask_pattern = (unmasked >> 10) & 0b111
    rule = qr.MASK_FUNCTIONS[mask_pattern]

    bits = []
    column = size - 1
    going_up = True
    while column > 0:
        if column == 6:
            column = 5
        row_sequence = range(size - 1, -1, -1) if going_up else range(0, size)
        for row in row_sequence:
            for col in (column, column - 1):
                if (row, col) in reserved:
                    continue
                value = matrix[row][col]
                if rule(row, col):
                    value ^= 1
                bits.append(value)
        going_up = not going_up
        column -= 2
    return bits, ec_level, mask_pattern


def decode(matrix, version=None):
    """``matrix`` back to the text it encodes. Alphanumeric mode only."""
    size = len(matrix)
    if version is None:
        version = next(v for v, spec in qr.VERSIONS.items() if spec[0] == size)

    bits, _, _ = read_data_bits(matrix, version)
    data_codewords = qr.VERSIONS[version][1]
    stream = bits[: data_codewords * 8]

    def take(count, cursor):
        value = 0
        for i in range(count):
            value = (value << 1) | stream[cursor + i]
        return value, cursor + count

    mode, cursor = take(4, 0)
    if mode != qr.MODE_ALPHANUMERIC:
        raise AssertionError(f"mode indicator was {mode:04b}, not alphanumeric")
    length, cursor = take(9, cursor)

    out = []
    remaining = length
    while remaining >= 2:
        pair, cursor = take(11, cursor)
        out.append(qr.ALPHANUMERIC[pair // 45])
        out.append(qr.ALPHANUMERIC[pair % 45])
        remaining -= 2
    if remaining:
        single, cursor = take(6, cursor)
        out.append(qr.ALPHANUMERIC[single])
    return "".join(out)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class ReedSolomonTests(SimpleTestCase):
    def test_spec_example_error_correction_codewords(self):
        """The one check that pins the arithmetic: Annex I of the specification."""
        self.assertEqual(
            qr.error_correction_codewords(SPEC_DATA_CODEWORDS, 10), SPEC_EC_CODEWORDS
        )

    def test_generator_polynomial_matches_the_published_exponents(self):
        exponents = [qr.GF_LOG[c] for c in qr.generator_polynomial(10)]
        self.assertEqual(
            exponents, [0, 251, 67, 46, 61, 118, 70, 64, 94, 32, 45]
        )

    def test_generator_polynomials_for_the_other_supported_versions(self):
        self.assertEqual(
            [qr.GF_LOG[c] for c in qr.generator_polynomial(16)][:5],
            [0, 120, 104, 107, 109],
        )
        self.assertEqual(
            [qr.GF_LOG[c] for c in qr.generator_polynomial(26)][:5],
            [0, 173, 125, 158, 2],
        )

    def test_field_arithmetic_basics(self):
        self.assertEqual(qr.gf_multiply(0, 5), 0)
        self.assertEqual(qr.gf_multiply(1, 5), 5)
        # Multiplication is commutative, and a^255 wraps to 1.
        self.assertEqual(qr.gf_multiply(7, 11), qr.gf_multiply(11, 7))
        self.assertEqual(qr.GF_EXP[255], 1)


class FormatInformationTests(SimpleTestCase):
    def test_every_mask_matches_the_published_string(self):
        for pattern, expected in PUBLISHED_FORMAT_STRINGS.items():
            with self.subTest(mask=pattern):
                got = "".join(str(b) for b in qr.format_bits(pattern))
                self.assertEqual(got, expected)


class DataEncodingTests(SimpleTestCase):
    def test_hello_world_matches_the_published_bit_stream(self):
        bits = "".join(
            f"{codeword:08b}" for codeword in qr.encode_data("HELLO WORLD", 1)
        )
        expected = (
            "0010"
            "000001011"
            "01100001011"
            "01111000110"
            "10001011100"
            "10110111000"
            "10011010100"
            "001101"
        )
        self.assertTrue(bits.startswith(expected), bits[: len(expected)])

    def test_padding_alternates_the_two_pad_bytes(self):
        codewords = qr.encode_data("A", 1)
        self.assertEqual(len(codewords), qr.VERSIONS[1][1])
        # Three codewords carry mode, count and the single character; the rest are
        # pad bytes starting at 0xEC and alternating (spec 8.4.9).
        self.assertEqual(codewords[3:9], [0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11])

    def test_capacities_match_the_published_table(self):
        self.assertEqual(qr.capacity(1), 20)
        self.assertEqual(qr.capacity(2), 38)
        self.assertEqual(qr.capacity(3), 61)

    def test_the_smallest_version_that_fits_is_chosen(self):
        self.assertEqual(qr.smallest_version("A" * 20), 1)
        self.assertEqual(qr.smallest_version("A" * 21), 2)
        self.assertEqual(qr.smallest_version("A" * 38), 2)
        self.assertEqual(qr.smallest_version("A" * 39), 3)

    def test_too_long_raises_rather_than_truncating(self):
        """A QR holding half a code scans perfectly and admits nobody."""
        with self.assertRaises(qr.QRError):
            qr.smallest_version("A" * 62)

    def test_characters_outside_alphanumeric_mode_are_named(self):
        with self.assertRaises(qr.QRError) as caught:
            qr.encode_data("hello!", 1)
        self.assertIn("'!'", str(caught.exception))

    def test_lowercase_is_accepted_by_encode_because_it_uppercases(self):
        self.assertEqual(
            qr.encode("https://pycon.ng/c/abcdefghjk"),
            qr.encode("HTTPS://PYCON.NG/C/ABCDEFGHJK"),
        )


class ModulePlacementTests(SimpleTestCase):
    def test_data_module_counts_match_the_codewords_plus_remainder(self):
        for version, (_, data, ec) in qr.VERSIONS.items():
            with self.subTest(version=version):
                self.assertEqual(
                    len(qr.data_module_positions(version)),
                    (data + ec) * 8 + qr.REMAINDER_BITS[version],
                )

    def test_first_codeword_lands_on_the_published_coordinates(self):
        self.assertEqual(
            qr.data_module_positions(1)[:8],
            [(20, 20), (20, 19), (19, 20), (19, 19), (18, 20), (18, 19), (17, 20), (17, 19)],
        )

    def test_reserved_modules_agree_with_the_specification_coordinates(self):
        """The encoder's own reserved map, against coordinates listed separately."""
        for version in qr.VERSIONS:
            with self.subTest(version=version):
                mask = qr.function_pattern_mask(version)
                expected = reserved_modules(version)
                got = {
                    (y, x)
                    for y, row in enumerate(mask)
                    for x, value in enumerate(row)
                    if value
                }
                self.assertEqual(got, expected)

    def test_the_finder_patterns_are_where_they_belong(self):
        matrix = qr.encode("HELLO")
        size = len(matrix)
        for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
            with self.subTest(corner=(top, left)):
                # Outer ring dark, inner ring light, 3x3 core dark.
                self.assertEqual(matrix[top][left], 1)
                self.assertEqual(matrix[top + 1][left + 1], 0)
                self.assertEqual(matrix[top + 3][left + 3], 1)

    def test_the_timing_patterns_alternate(self):
        matrix = qr.encode("HELLO")
        size = len(matrix)
        for i in range(8, size - 8):
            with self.subTest(i=i):
                self.assertEqual(matrix[6][i], 1 if i % 2 == 0 else 0)
                self.assertEqual(matrix[i][6], 1 if i % 2 == 0 else 0)

    def test_the_dark_module_is_dark(self):
        for version in qr.VERSIONS:
            with self.subTest(version=version):
                matrix = qr.encode("A" * qr.capacity(version), version=version)
                self.assertEqual(matrix[4 * version + 9][8], 1)

    def test_version_2_has_its_alignment_pattern(self):
        matrix = qr.encode("A" * 30)
        self.assertEqual(len(matrix), 25)
        self.assertEqual(matrix[18][18], 1)   # centre
        self.assertEqual(matrix[17][18], 0)   # inner ring
        self.assertEqual(matrix[16][18], 1)   # outer ring


class RoundTripTests(SimpleTestCase):
    """
    Encode, then read back with the independent reader above.

    This is what verifies masking, the data zigzag and the format information
    together: any one of them wrong and the payload comes back as noise.
    """

    def test_a_checkin_url_survives_the_round_trip(self):
        payload = "HTTPS://PYCON.NG/C/ABCDEFGHJK"
        self.assertEqual(decode(qr.encode(payload)), payload)

    def test_round_trip_across_every_version_and_length(self):
        for version in qr.VERSIONS:
            for length in (1, 2, 7, qr.capacity(version) - 1, qr.capacity(version)):
                payload = ("PYCON2027ABCDEFGHJKMNPRSTUVWXY23479" * 3)[:length]
                with self.subTest(version=version, length=length):
                    matrix = qr.encode(payload, version=version)
                    self.assertEqual(decode(matrix, version), payload)

    def test_the_reader_recovers_the_error_correction_level(self):
        _, ec_level, _ = read_data_bits(qr.encode("HELLO"), 1)
        self.assertEqual(ec_level, qr.EC_LEVEL_M)

    def test_the_chosen_mask_is_the_lowest_scoring_one(self):
        """
        The specification requires the best mask, not merely a valid one: a poor
        choice still decodes here but is harder for a real scanner to lock on to.
        """
        payload = "HTTPS://PYCON.NG/C/ABCDEFGHJK"
        version = qr.smallest_version(payload)
        chosen = qr.encode(payload)
        _, _, mask_pattern = read_data_bits(chosen, version)

        best_score = qr.penalty(chosen)
        for pattern in range(8):
            if pattern == mask_pattern:
                continue
            with self.subTest(alternative=pattern):
                alternative = _matrix_with_mask(payload, version, pattern)
                self.assertLessEqual(best_score, qr.penalty(alternative))


def _matrix_with_mask(payload, version, pattern):
    """A matrix built exactly as encode() builds one, but with a chosen mask."""
    size = qr.VERSIONS[version][0]
    base = qr._blank(size)
    qr._place_finder(base, 0, 0)
    qr._place_finder(base, 0, size - 7)
    qr._place_finder(base, size - 7, 0)
    centre = qr.ALIGNMENT_CENTRES[version]
    if centre is not None:
        qr._place_alignment(base, centre)
    qr._place_timing(base)
    qr._reserve_format_areas(base, version)
    qr._place_data(base, qr.full_codewords(payload.upper(), version), version)
    candidate = qr.apply_mask(base, version, pattern)
    qr._place_format(candidate, pattern)
    return candidate


class PenaltyTests(SimpleTestCase):
    """
    Each rule against a hand-computed value.

    Tested one at a time because the four move in opposite directions: adding dark
    modules raises rule 1 and lowers rule 4, so comparing two whole scores proves
    nothing about either. That mistake is why these are separate functions.
    """

    def test_an_all_light_square_scores_exactly_as_computed(self):
        """
        A 10x10 of light modules, worked out by hand:

        * rule 1: ten rows each one run of ten, 3 + 5 = 8 apiece, and ten columns
          the same -> 160
        * rule 2: nine by nine overlapping blocks of four light -> 243
        * rule 3: a needle is eleven modules, so none fits -> 0
        * rule 4: nothing dark, 50 away from half, floor(50 / 5) = 10 -> 100
        """
        blank = [[0] * 10 for _ in range(10)]
        self.assertEqual(qr.penalty_runs(blank), 160)
        self.assertEqual(qr.penalty_blocks(blank), 243)
        self.assertEqual(qr.penalty_needles(blank), 0)
        self.assertEqual(qr.penalty_balance(blank), 100)
        self.assertEqual(qr.penalty(blank), 503)

    def test_runs_shorter_than_five_cost_nothing(self):
        # A checkerboard: no two adjacent modules match in any direction.
        board = [[(y + x) % 2 for x in range(8)] for y in range(8)]
        self.assertEqual(qr.penalty_runs(board), 0)
        self.assertEqual(qr.penalty_blocks(board), 0)
        self.assertEqual(qr.penalty_balance(board), 0)

    def test_a_run_of_exactly_five_costs_three_and_each_extra_one_more(self):
        """
        Eight modules wide, so the light remainder is itself under five and adds
        nothing -- a light run counts as much as a dark one, which is easy to
        forget when reading the rule.
        """
        for length, expected in ((4, 0), (5, 3), (6, 4), (8, 6)):
            with self.subTest(length=length):
                line = [[1] * length + [0] * (8 - length)]
                # One row only, so each column is a single module and adds nothing.
                self.assertEqual(qr.penalty_runs(line), expected)

    def test_a_light_run_counts_as_much_as_a_dark_one(self):
        self.assertEqual(qr.penalty_runs([[1, 1, 1, 1, 0, 0, 0, 0, 0]]), 3)

    def test_one_solid_two_by_two_block_costs_three(self):
        matrix = [[0, 0, 0], [0, 1, 1], [0, 1, 1]]
        self.assertEqual(qr.penalty_blocks(matrix), 3)

    def test_each_needle_costs_forty_in_both_orientations(self):
        for needle in qr.NEEDLES:
            with self.subTest(needle=needle):
                row = [list(needle)]
                self.assertEqual(qr.penalty_needles(row), 40)
                column = [[value] for value in needle]
                self.assertEqual(qr.penalty_needles(column), 40)

    def test_balance_is_free_within_five_percent_of_half(self):
        half = [[1] * 5 + [0] * 5 for _ in range(10)]
        self.assertEqual(qr.penalty_balance(half), 0)
        all_dark = [[1] * 10 for _ in range(10)]
        self.assertEqual(qr.penalty_balance(all_dark), 100)


class SvgTests(SimpleTestCase):
    def test_the_svg_carries_a_quiet_zone_and_a_label(self):
        matrix = qr.encode("HELLO")
        svg = qr.to_svg(matrix, module=4, quiet_zone=4, title="Ticket ABCDEFGHJK")
        # 21 modules plus four each side.
        self.assertIn('viewBox="0 0 29 29"', svg)
        self.assertIn('width="116"', svg)
        self.assertIn('aria-label="Ticket ABCDEFGHJK"', svg)
        self.assertIn('role="img"', svg)

    def test_the_svg_draws_one_subpath_per_dark_module(self):
        matrix = qr.encode("HELLO")
        svg = qr.to_svg(matrix)
        dark = sum(sum(row) for row in matrix)
        self.assertEqual(svg.count("M"), dark)

    def test_empty_input_is_refused(self):
        with self.assertRaises(qr.QRError):
            qr.encode("")
