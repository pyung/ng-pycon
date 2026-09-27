"""
A small QR encoder, for check-in codes.

Written rather than installed, for two reasons. This project is run by people who
find deploying expensive, so a dependency added for one 30-character string is a
poor trade. And the payload here is fixed in shape -- an uppercase URL ending in a
ten-character code -- which means only a narrow, well-bounded slice of QR is
needed: alphanumeric mode, error correction level M, versions 1 to 3. Those three
versions have a single error-correction block each, so there is no interleaving to
get wrong, and version 3 holds 61 characters, roughly double what a check-in URL
needs.

Everything here follows ISO/IEC 18004. The parts that cannot be checked by
inspection are checked against the specification's own worked example and by
decoding the output again -- see tests_qr.py, which reads a finished matrix back
with placement logic written separately from the placement logic here. A QR that
almost works is worse than none at all, because it fails at a door with a queue
behind it.

    >>> matrix = encode("HTTPS://PYCON.NG/C/ABCDEFGHJK")
    >>> len(matrix)
    25
    >>> svg = to_svg(matrix)
"""

#: Alphanumeric mode's character set, in its code-point order. Index is the value.
ALPHANUMERIC = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"

#: Mode indicator for alphanumeric, four bits.
MODE_ALPHANUMERIC = 0b0010

#: Error-correction level M's two-bit indicator. Level M corrects about 15% of
#: codewords, which is the usual choice for something printed on a badge that may
#: be creased or thumbed.
EC_LEVEL_M = 0b00

#: version -> (modules per side, data codewords, ec codewords). Level M, one block.
VERSIONS = {
    1: (21, 16, 10),
    2: (25, 28, 16),
    3: (29, 44, 26),
}

#: Alignment pattern centre for each version. Version 1 has none; 2 and 3 have one,
#: at 4 * version + 10 on both axes.
ALIGNMENT_CENTRES = {1: None, 2: 18, 3: 22}

#: Remainder bits: data-region modules beyond the codewords, left light and then
#: masked like any other data module (spec table 1). Version 1 has none; versions
#: 2 to 6 have seven. Getting this wrong makes a matrix of the right size whose
#: last codeword lands seven modules early, which no scanner will read.
REMAINDER_BITS = {1: 0, 2: 7, 3: 7}

#: Pad bytes, alternating, appended after the terminator (spec 8.4.9).
PAD_BYTES = (0xEC, 0x11)

#: BCH generator for the 15-bit format information, and the mask applied to it.
FORMAT_GENERATOR = 0b10100110111
FORMAT_MASK = 0b101010000010010

#: Primitive polynomial for GF(256) as QR uses it.
GF_PRIMITIVE = 0x11D


class QRError(ValueError):
    """The payload cannot be encoded in the narrow slice of QR supported here."""


# ---------------------------------------------------------------------------
# GF(256) arithmetic and Reed-Solomon
# ---------------------------------------------------------------------------

def _build_tables():
    exp = [0] * 512
    log = [0] * 256
    value = 1
    for i in range(255):
        exp[i] = value
        log[value] = i
        value <<= 1
        if value & 0x100:
            value ^= GF_PRIMITIVE
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


GF_EXP, GF_LOG = _build_tables()


def gf_multiply(a, b):
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def generator_polynomial(degree):
    """
    The Reed-Solomon generator polynomial of ``degree``, highest term first.

    Built as the product of (x - a^i) for i in 0..degree-1, which in GF(256) is
    (x + a^i).
    """
    poly = [1]
    for i in range(degree):
        poly = _polynomial_multiply(poly, [1, GF_EXP[i]])
    return poly


def _polynomial_multiply(a, b):
    result = [0] * (len(a) + len(b) - 1)
    for i, coefficient_a in enumerate(a):
        if coefficient_a == 0:
            continue
        for j, coefficient_b in enumerate(b):
            result[i + j] ^= gf_multiply(coefficient_a, coefficient_b)
    return result


def error_correction_codewords(data, count):
    """``count`` error-correction codewords for the data codewords given."""
    generator = generator_polynomial(count)
    remainder = list(data) + [0] * count
    for i in range(len(data)):
        lead = remainder[i]
        if lead == 0:
            continue
        for j, coefficient in enumerate(generator):
            remainder[i + j] ^= gf_multiply(coefficient, lead)
    return remainder[len(data):]


# ---------------------------------------------------------------------------
# Data encoding
# ---------------------------------------------------------------------------

def can_encode(text):
    """Whether every character of ``text`` is in alphanumeric mode's set."""
    return all(character in ALPHANUMERIC for character in text)


def smallest_version(text):
    """
    The smallest supported version that holds ``text``.

    Raises rather than silently truncating: a QR that encodes half a code scans
    successfully and admits nobody.
    """
    for version in sorted(VERSIONS):
        if len(text) <= capacity(version):
            return version
    raise QRError(
        f"{len(text)} characters needs a QR version above 3, which this encoder "
        f"does not build. The longest payload it takes is {capacity(3)} characters."
    )


def capacity(version):
    """How many alphanumeric characters fit in ``version`` at level M."""
    _, data_codewords, _ = VERSIONS[version]
    available = data_codewords * 8 - 4 - 9  # mode indicator, then character count
    pairs, remainder = divmod(available, 11)
    return pairs * 2 + (1 if remainder >= 6 else 0)


def _bits(value, length):
    return [(value >> shift) & 1 for shift in range(length - 1, -1, -1)]


def encode_data(text, version):
    """
    ``text`` as the data codewords for ``version``: header, payload, pad.

    Alphanumeric mode packs characters in pairs, eleven bits for two, six for a
    trailing single (spec 8.4.3).
    """
    if not can_encode(text):
        offenders = sorted({c for c in text if c not in ALPHANUMERIC})
        raise QRError(
            "These characters are outside QR alphanumeric mode: "
            + ", ".join(repr(c) for c in offenders)
        )

    _, data_codewords, _ = VERSIONS[version]
    bits = _bits(MODE_ALPHANUMERIC, 4) + _bits(len(text), 9)

    for index in range(0, len(text) - 1, 2):
        first = ALPHANUMERIC.index(text[index])
        second = ALPHANUMERIC.index(text[index + 1])
        bits += _bits(first * 45 + second, 11)
    if len(text) % 2:
        bits += _bits(ALPHANUMERIC.index(text[-1]), 6)

    total_bits = data_codewords * 8
    if len(bits) > total_bits:
        raise QRError(f"{len(text)} characters overflows version {version}.")

    # Terminator: up to four zero bits, then zeros to the next byte boundary.
    bits += [0] * min(4, total_bits - len(bits))
    while len(bits) % 8 and len(bits) < total_bits:
        bits.append(0)

    codewords = [
        int("".join(str(bit) for bit in bits[i : i + 8]), 2)
        for i in range(0, len(bits), 8)
    ]
    # Pad bytes alternate 0xEC, 0x11 from the first one added until the version's
    # data capacity is filled (spec 8.4.9).
    for index in range(data_codewords - len(codewords)):
        codewords.append(PAD_BYTES[index % 2])
    return codewords


def full_codewords(text, version):
    """Data codewords followed by their error-correction codewords."""
    _, _, ec_count = VERSIONS[version]
    data = encode_data(text, version)
    return data + error_correction_codewords(data, ec_count)


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------

def _blank(size):
    """A matrix of None, meaning "no module decided yet"."""
    return [[None] * size for _ in range(size)]


def _place_finder(matrix, row, col):
    """A 7x7 finder pattern with its one-module separator (spec 6.3.3)."""
    size = len(matrix)
    for r in range(-1, 8):
        for c in range(-1, 8):
            y, x = row + r, col + c
            if not (0 <= y < size and 0 <= x < size):
                continue
            inside = 0 <= r <= 6 and 0 <= c <= 6
            if not inside:
                matrix[y][x] = 0  # separator
                continue
            edge = r in (0, 6) or c in (0, 6)
            centre = 2 <= r <= 4 and 2 <= c <= 4
            matrix[y][x] = 1 if (edge or centre) else 0


def _place_alignment(matrix, centre):
    """The single 5x5 alignment pattern of versions 2 and 3."""
    for r in range(-2, 3):
        for c in range(-2, 3):
            edge = r in (-2, 2) or c in (-2, 2)
            matrix[centre + r][centre + c] = 1 if (edge or (r == 0 and c == 0)) else 0


def _place_timing(matrix):
    """The alternating row and column at index 6, joining the finder patterns."""
    size = len(matrix)
    for i in range(size):
        if matrix[6][i] is None:
            matrix[6][i] = 1 if i % 2 == 0 else 0
        if matrix[i][6] is None:
            matrix[i][6] = 1 if i % 2 == 0 else 0


def _reserve_format_areas(matrix, version):
    """
    Mark the modules the format information will occupy, plus the dark module.

    Reserved before data placement so the zigzag skips them; the values go in
    afterwards, once the mask is known.
    """
    size = len(matrix)
    for i in range(9):
        if matrix[8][i] is None:
            matrix[8][i] = 0
        if matrix[i][8] is None:
            matrix[i][8] = 0
    for i in range(8):
        if matrix[8][size - 1 - i] is None:
            matrix[8][size - 1 - i] = 0
        if matrix[size - 1 - i][8] is None:
            matrix[size - 1 - i][8] = 0
    # The dark module, always 1, at (4 * version + 9, 8).
    matrix[4 * version + 9][8] = 1


def function_pattern_mask(version):
    """
    A matrix of booleans: True where a module is part of a function pattern.

    Separate from the matrix itself so masking can be applied to data modules
    only, and so the tests can assert where the function patterns are without
    re-deriving them.
    """
    size = VERSIONS[version][0]
    reserved = [[False] * size for _ in range(size)]
    matrix = _blank(size)
    _place_finder(matrix, 0, 0)
    _place_finder(matrix, 0, size - 7)
    _place_finder(matrix, size - 7, 0)
    centre = ALIGNMENT_CENTRES[version]
    if centre is not None:
        _place_alignment(matrix, centre)
    _place_timing(matrix)
    _reserve_format_areas(matrix, version)
    for y in range(size):
        for x in range(size):
            reserved[y][x] = matrix[y][x] is not None
    return reserved


def data_module_positions(version):
    """
    Every data module, in the order codeword bits are written into it.

    Two-module-wide columns from the right edge leftwards, travelling up then
    down alternately, skipping the vertical timing pattern at column 6
    (spec 8.7.3).
    """
    size = VERSIONS[version][0]
    reserved = function_pattern_mask(version)
    positions = []
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1  # the timing column is not part of a column pair
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if not reserved[row][c]:
                    positions.append((row, c))
        upward = not upward
        col -= 2
    return positions


def _place_data(matrix, codewords, version):
    positions = data_module_positions(version)
    bits = [bit for codeword in codewords for bit in _bits(codeword, 8)]
    bits += [0] * REMAINDER_BITS[version]
    if len(bits) != len(positions):
        raise QRError(
            f"version {version}: {len(bits)} bits for {len(positions)} data modules"
        )
    for (row, col), bit in zip(positions, bits):
        matrix[row][col] = bit


MASK_FUNCTIONS = (
    lambda i, j: (i + j) % 2 == 0,
    lambda i, j: i % 2 == 0,
    lambda i, j: j % 3 == 0,
    lambda i, j: (i + j) % 3 == 0,
    lambda i, j: (i // 2 + j // 3) % 2 == 0,
    lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
    lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
    lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0,
)


def apply_mask(matrix, version, pattern):
    """A copy of ``matrix`` with mask ``pattern`` applied to its data modules."""
    reserved = function_pattern_mask(version)
    rule = MASK_FUNCTIONS[pattern]
    size = len(matrix)
    return [
        [
            row[x] ^ 1 if (not reserved[y][x] and rule(y, x)) else row[x]
            for x in range(size)
        ]
        for y, row in enumerate(matrix)
    ]


def format_bits(pattern, ec_level=EC_LEVEL_M):
    """
    The 15 bits of format information for an EC level and mask (spec 8.9).

    Five data bits extended by a BCH(15, 5) remainder, then XORed with a fixed
    mask so the all-zero case is not a blank field.
    """
    data = (ec_level << 3) | pattern
    remainder = data << 10
    # Long division by the BCH generator, in GF(2): subtract is XOR.
    while remainder.bit_length() > 10:
        remainder ^= FORMAT_GENERATOR << (remainder.bit_length() - 11)
    return _bits(((data << 10) | remainder) ^ FORMAT_MASK, 15)


def _place_format(matrix, pattern):
    """Write the format bits into both of their copies (spec 8.9, figure 25)."""
    size = len(matrix)
    bits = format_bits(pattern)

    # First copy: around the top-left finder pattern.
    positions = []
    for i in range(6):
        positions.append((8, i))
    positions.append((8, 7))
    positions.append((8, 8))
    positions.append((7, 8))
    for i in range(5, -1, -1):
        positions.append((i, 8))
    for (row, col), bit in zip(positions, bits):
        matrix[row][col] = bit

    # Second copy: split between the other two finder patterns.
    mirrored = []
    for i in range(7):
        mirrored.append((size - 1 - i, 8))
    for i in range(8):
        mirrored.append((8, size - 8 + i))
    for (row, col), bit in zip(mirrored, bits):
        matrix[row][col] = bit


# ---------------------------------------------------------------------------
# Mask selection
# ---------------------------------------------------------------------------

#: Rule 3's needle: the 1:1:3:1:1 finder proportion with four light modules to one
#: side, in both directions. A scanner looks for exactly this to find the corners,
#: so anything resembling it elsewhere is expensive.
NEEDLES = (
    (1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0),
    (0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1),
)


def _lines(matrix):
    """Every row, then every column, as tuples. Both rules 1 and 3 scan both."""
    rows = [tuple(row) for row in matrix]
    columns = [tuple(column) for column in zip(*matrix)]
    return rows + columns


def penalty_runs(matrix):
    """Rule 1: five adjacent modules of one colour cost 3, plus 1 for each extra."""
    score = 0
    for line in _lines(matrix):
        run = 1
        for index in range(1, len(line)):
            if line[index] == line[index - 1]:
                run += 1
                continue
            if run >= 5:
                score += 3 + (run - 5)
            run = 1
        if run >= 5:
            score += 3 + (run - 5)
    return score


def penalty_blocks(matrix):
    """Rule 2: every 2x2 block of one colour costs 3. Overlaps count separately."""
    size = len(matrix)
    score = 0
    for y in range(size - 1):
        for x in range(size - 1):
            first = matrix[y][x]
            if (
                matrix[y][x + 1] == first
                and matrix[y + 1][x] == first
                and matrix[y + 1][x + 1] == first
            ):
                score += 3
    return score


def penalty_needles(matrix):
    """Rule 3: each finder-like sequence in a row or column costs 40."""
    score = 0
    for line in _lines(matrix):
        for index in range(len(line) - 10):
            if line[index : index + 11] in NEEDLES:
                score += 40
    return score


def penalty_balance(matrix):
    """
    Rule 4: 10 points for every 5% the dark proportion strays from half.

    Floored, as the specification has it, so a symbol within 5% of even costs
    nothing here.
    """
    size = len(matrix)
    dark = sum(sum(row) for row in matrix)
    proportion = dark * 100 / (size * size)
    return 10 * int(abs(proportion - 50) / 5)


def penalty(matrix):
    """
    The mask evaluation score (spec 8.8.2). Lower is better.

    Four rules, all about making the symbol easy for a scanner to lock on to:
    avoid long same-colour runs, avoid solid blocks, avoid anything resembling a
    finder pattern, and keep light and dark roughly balanced. Split into four
    functions so each can be checked against a hand-computed matrix -- tested
    together, they mask each other's mistakes.
    """
    return (
        penalty_runs(matrix)
        + penalty_blocks(matrix)
        + penalty_needles(matrix)
        + penalty_balance(matrix)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encode(text, version=None):
    """
    ``text`` as a QR matrix: a list of rows of 0 and 1, 1 being dark.

    Uppercases the payload first, because alphanumeric mode has no lowercase and a
    URL's host is case-insensitive anyway. Picks the smallest version that fits
    and the mask with the lowest penalty, as the specification requires.
    """
    text = (text or "").upper()
    if not text:
        raise QRError("Nothing to encode.")
    version = version or smallest_version(text)
    if version not in VERSIONS:
        raise QRError(f"Version {version} is outside the supported range 1-3.")
    if len(text) > capacity(version):
        raise QRError(
            f"{len(text)} characters does not fit version {version} "
            f"(holds {capacity(version)})."
        )

    size = VERSIONS[version][0]
    base = _blank(size)
    _place_finder(base, 0, 0)
    _place_finder(base, 0, size - 7)
    _place_finder(base, size - 7, 0)
    centre = ALIGNMENT_CENTRES[version]
    if centre is not None:
        _place_alignment(base, centre)
    _place_timing(base)
    _reserve_format_areas(base, version)
    _place_data(base, full_codewords(text, version), version)

    best = None
    for pattern in range(8):
        candidate = apply_mask(base, version, pattern)
        _place_format(candidate, pattern)
        score = penalty(candidate)
        if best is None or score < best[0]:
            best = (score, candidate)
    return best[1]


def to_svg(matrix, module=4, quiet_zone=4, title="QR code"):
    """
    ``matrix`` as an inline SVG string.

    SVG rather than a raster: it is a few hundred bytes, stays sharp when printed
    on a badge or zoomed on a phone, and needs no image library or media file. The
    quiet zone is part of the drawing because a QR without four modules of margin
    is unreliable to scan, and callers forget.
    """
    size = len(matrix)
    span = size + quiet_zone * 2
    path = []
    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            if value:
                path.append(f"M{x + quiet_zone} {y + quiet_zone}h1v1h-1z")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {span} {span}" '
        f'width="{span * module}" height="{span * module}" role="img" '
        f'aria-label="{title}" shape-rendering="crispEdges">'
        f'<rect width="{span}" height="{span}" fill="#ffffff"/>'
        f'<path d="{"".join(path)}" fill="#000000"/>'
        f"</svg>"
    )


def to_text(matrix, dark="##", light="  "):
    """``matrix`` as text, for looking at one in a terminal or a test failure."""
    return "\n".join("".join(dark if v else light for v in row) for row in matrix)
