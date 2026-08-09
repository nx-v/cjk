"""GlyphWiki → font encoding scheme (1 glyph : 2 code points).

## Roles

* **Bucket / pigeonhole fonts** (``build_cjk.py``) cover real Unicode
  Hanzi, Hangul, Tangut, and Yi. They run independently of the GlyphWiki
  PUA font. D4 variants use **VS01..VS08 directly** after the character
  (no Supplementary PUA marker).
* **Everything else from GlyphWiki** is addressed only through the Private
  Use Area, as a **two-codepoint ligature**.

## GlyphWiki ligature (1→2)

Each GlyphWiki base name maps to:

    <SPUA marker> + <BMP PUA index>

Example::

    U+FEFE0  U+F400

* **SPUA marker** — Supplementary PUA-A/B
  (``U+F0000..U+FFFFD``, ``U+100000..U+10FFFD``), skipping noncharacters.
* **BMP PUA index** — ``U+E000..U+F8FF`` (6400 slots per marker).

OpenType GSUB ligates the pair to the rendered glyph.

Assignment order: sort dump entries by ``(related code point,
stroke count, name)``, then fill markers × PUA slots row-major
(exhaust one marker's 6400 PUA indices, then advance the marker).

Capacity ≈ ``131_068 × 6_400`` ≈ 838 million — far above the dump size.

## GlyphWiki ligature fonts (57 600 glyphs per file)

One TTF per SPUA marker (``build_glyphwiki_fonts``), **named after that
marker** (the first code point of every ligature in the file), e.g.
``F0000.ttf``. Each file contains:

* 6 400 BMP PUA selector glyphs (U+E000..U+F8FF)
* 6 400 × 8 = 51 200 rendered outlines (identity + 7 unique D4 variants)

Total **57 600** glyphs (plus ``.notdef`` and the SPUA marker). Identity is
rendered into the CJK typo box (ascender 0.88em / descender -0.12em),
centered at ``y ≈ 0.38em`` like Han/Yi, then D4 flips/rotates about that
midpoint. Result glyph names are the GlyphWiki canonical names
(e.g. ``u4e00``, ``cdp-81dd``), not ``g`` + hex. GSUB::

    <SPUA marker>  <BMP PUA>   → identity outline
    <identity>     <VS02..08>  → D4 variant outline

Only dump entries whose ``related`` code point falls in
``build_cjk.CHAR_RANGES``, plus CJK Radicals Supplement
(U+2E80..2EFF), Kangxi Radicals (U+2F00..2FDF), and GETA MARK
(U+3013 〓 — GlyphWiki's unencoded/placeholder related), are packed.
HKCS annotation overlays, non-mincho styles (sans/gothic/calligraphy/bitmap/
shape experiments), glyphs with non-mincho KAGE stroke types (``0:99:N``
shotai ≠ mincho, exotic type codes), and glyphs that embed overlay pieces
are excluded — see ``GLYPH_EXCLUSION_NAME_RES`` / ``GLYPH_EXCLUSION_DATA_MARKERS``
/ ``is_non_mincho_stroke_data``.
Empty placeholders (``0:-1:-1:-1``), full-frame aliases of another packed
glyph, and duplicate resolved outlines (keep one canonical form) are also
dropped before ligature assignment.

## D4 variants — VS01..VS08 (bucket + GlyphWiki fonts)

Bucket fonts emit transformed outlines **in the same TTF** as the base
glyph. Variants are the two-codepoint GSUB ligature::

    <han / hangul / tangut / yi>  <VS0n>

No Supplementary PUA marker. VS01..VS08 (U+E000..U+E007) are also
cmap'd into every bucket font as zero-width marks. UVS mirrors them at
U+FE00..FE07. ``U+FE08`` overlays the preceding pair (prior glyphs
zero-width; last keeps advance; chain with more FE08).

The 8 unique square symmetries (dihedral group D4); geometric duplicates
such as ``mxy === r180`` are omitted:

======= ========== =========================
Name    Code point Transform
======= ========== =========================
VS01    U+E000     identity
VS02    U+E001     r90 (90° CCW)
VS03    U+E002     r180
VS04    U+E003     r270
VS05    U+E004     mx (reflect horizontal)
VS06    U+E005     my (reflect vertical)
VS07    U+E006     r90mx (diagonal)
VS08    U+E007     r90my (other diagonal)
======= ========== =========================

GSUB distinguishes this from GlyphWiki pairs because the first code point
is a real Unicode character, not an SPUA marker. There are **no**
``+0x40000`` / ``+0x80000`` / ``+0xD0000`` cmap offsets.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# BMP PUA — second half of every GlyphWiki / mirror ligature (6 400 slots)
BMP_PUA_START = 0xE000
BMP_PUA_END = 0xF8FF
BMP_PUA_COUNT = BMP_PUA_END - BMP_PUA_START + 1  # 6400

# Supplementary PUA — first half of every GlyphWiki ligature
SPUA_A_START = 0xF0000
SPUA_A_END = 0xFFFFD
SPUA_B_START = 0x100000
SPUA_B_END = 0x10FFFD

# Default SPUA marker used only in GlyphWiki docs / examples (not for mirrors)
DEFAULT_VS_MARKER = 0xFEFE0

# First SPUA marker used when packing GlyphWiki content (start of SPUA-A)
GLYPHWIKI_MARKER_START = SPUA_A_START


class MirrorVS:
    """D4 symmetries encoded as BMP PUA U+E000..U+E007 (VS01..VS08)."""

    IDENTITY = 0  # VS01 — U+E000
    ROT90 = 1  # VS02 — U+E001
    ROT180 = 2  # VS03 — U+E002
    ROT270 = 3  # VS04 — U+E003
    FLIP_X = 4  # VS05 — U+E004 — mx
    FLIP_Y = 5  # VS06 — U+E005 — my
    ROT90_MX = 6  # VS07 — U+E006
    ROT90_MY = 7  # VS08 — U+E007

    MODE_COUNT = 8

    @staticmethod
    def codepoint(mode: int) -> int:
        if not 0 <= mode < MirrorVS.MODE_COUNT:
            raise ValueError(
                f"D4 mode must be 0..{MirrorVS.MODE_COUNT - 1}, got {mode}"
            )
        return BMP_PUA_START + mode

    @staticmethod
    def mode_of(cp: int) -> int | None:
        if BMP_PUA_START <= cp < BMP_PUA_START + MirrorVS.MODE_COUNT:
            return cp - BMP_PUA_START
        return None


# (mode, rot90_quarters, flip_x, flip_y, name_suffix or None for identity)
D4_MODES: list[tuple[int, int, bool, bool, str | None]] = [
    (MirrorVS.IDENTITY, 0, False, False, None),
    (MirrorVS.ROT90, 1, False, False, "r90"),
    (MirrorVS.ROT180, 2, False, False, "r180"),
    (MirrorVS.ROT270, 3, False, False, "r270"),
    (MirrorVS.FLIP_X, 0, True, False, "mx"),
    (MirrorVS.FLIP_Y, 0, False, True, "my"),
    (MirrorVS.ROT90_MX, 1, True, False, "r90mx"),
    (MirrorVS.ROT90_MY, 1, False, True, "r90my"),
]

VS01 = MirrorVS.codepoint(MirrorVS.IDENTITY)
VS02 = MirrorVS.codepoint(MirrorVS.ROT90)
VS03 = MirrorVS.codepoint(MirrorVS.ROT180)
VS04 = MirrorVS.codepoint(MirrorVS.ROT270)
VS05 = MirrorVS.codepoint(MirrorVS.FLIP_X)
VS06 = MirrorVS.codepoint(MirrorVS.FLIP_Y)
VS07 = MirrorVS.codepoint(MirrorVS.ROT90_MX)
VS08 = MirrorVS.codepoint(MirrorVS.ROT90_MY)


# ---------------------------------------------------------------------------
# Predicates / iterators
# ---------------------------------------------------------------------------


def is_noncharacter(cp: int) -> bool:
    if 0xFDD0 <= cp <= 0xFDEF:
        return True
    return (cp & 0xFFFF) in (0xFFFE, 0xFFFF)


def is_spua(cp: int) -> bool:
    return SPUA_A_START <= cp <= SPUA_A_END or SPUA_B_START <= cp <= SPUA_B_END


def iter_spua_markers(start: int = GLYPHWIKI_MARKER_START) -> Iterator[int]:
    """Yield usable SPUA markers starting at ``start`` (A then B)."""
    ranges = (
        (SPUA_A_START, SPUA_A_END),
        (SPUA_B_START, SPUA_B_END),
    )
    started = False
    for lo, hi in ranges:
        for cp in range(lo, hi + 1):
            if not started:
                if cp < start:
                    continue
                started = True
            if not is_noncharacter(cp):
                yield cp


def marker_count() -> int:
    return sum(1 for _ in iter_spua_markers(SPUA_A_START))


def ligature_capacity() -> int:
    """Total GlyphWiki ligature slots (markers × 6400)."""
    return marker_count() * BMP_PUA_COUNT


# Back-compat alias used by older call sites
def pack_capacity() -> int:
    return ligature_capacity()


# ---------------------------------------------------------------------------
# Related-key sorting
# ---------------------------------------------------------------------------


def parse_related_key(related: str) -> tuple[int, str]:
    """Sort key for a GlyphWiki ``related`` field."""
    r = related.strip().lower()
    if r.startswith("u") and len(r) >= 2:
        hexpart = r[1:]
        if 4 <= len(hexpart) <= 6 and all(c in "0123456789abcdef" for c in hexpart):
            return (int(hexpart, 16), r)
    return (0x110000, r)


def related_codepoint(related: str) -> int | None:
    """Parse ``related`` as a Unicode scalar, or ``None`` if not ``uXXXX``."""
    cp, _ = parse_related_key(related)
    return None if cp >= 0x110000 else cp


# Same blocks as ``build_cjk.CHAR_RANGES``, plus GlyphWiki-only extras
# (radicals; GETA MARK used as related for unencoded / placeholder glyphs).
RELATED_EXTRA_RANGES: list[tuple[int, int, str]] = [
    (0x2E80, 0x2EFF, "CJK Radicals Supplement"),
    (0x2F00, 0x2FDF, "Kangxi Radicals"),
    (0x3013, 0x3013, "GETA MARK (GlyphWiki unencoded related)"),
]


def related_allow_ranges() -> list[tuple[int, int, str]]:
    """Inclusive ranges whose related code points are packed into GlyphWiki fonts."""
    from ..build_cjk import CHAR_RANGES

    return list(CHAR_RANGES) + list(RELATED_EXTRA_RANGES)


def related_allow_set(ranges: Sequence[tuple[int, int, str]] | None = None) -> set[int]:
    if ranges is None:
        ranges = related_allow_ranges()
    out: set[int] = set()
    for start, end, _name in ranges:
        out.update(range(start, end + 1))
    return out


def is_allowed_related(related: str, allow: set[int] | None = None) -> bool:
    """True if ``related`` is a uXXXX code point inside the allow set."""
    if allow is None:
        allow = related_allow_set()
    cp = related_codepoint(related)
    return cp is not None and cp in allow


def filter_related_entries(
    entries: Sequence[tuple[str, str]],
    allow: set[int] | None = None,
) -> list[tuple[str, str]]:
    """Drop GlyphWiki names whose related code point is outside the allow set."""
    if allow is None:
        allow = related_allow_set()
    return [(n, r) for n, r in entries if is_allowed_related(r, allow)]


# ---------------------------------------------------------------------------
# Name / overlay exclusion (annotation composites, not real forms)
# ---------------------------------------------------------------------------

# GlyphWiki names matched against these regexes (case-insensitive).
# Goal: keep mincho-style kanji forms; drop annotation overlays, alternate
# stroke styles, bitmaps, shapes, and calligraphy experiments.
GLYPH_EXCLUSION_NAME_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # HKCS annotation overlays / map composites
        r"^hkcs_digit",
        r"^hkcs_char-",
        r"^hkcs_u25a1$",
        r"^hkcs_m",
        # Bitmaps / raster / dotted reconstructions
        r"^kesuuko_dot",
        r"(^|[_-])(bitmap|pixel|pixelated|mosaic|raster)($|[_-])",
        # Sans / gothic / non-mincho font styles
        r"(^|[_-])sans($|[_-])",
        r"(^|[_-])gothic($|[_-])",
        r"(^|[_-])semiserif($|[_-])",
        r"heiti",
        r"simsun",
        r"(^|[_-])meiryo($|[_-])",
        # Calligraphy / handwriting / textbook styles
        r"^sosho-",
        r"(^|[_-])sosho($|[_-])",
        r"kyoukasho",
        r"(^|[_-])(tensho|reisho|gyosho|kaisho|sousho|caoshu|xingshu|zhuanshu)($|[_-])",
        r"(^|[_-])(brush|callig|handwrit|cursive)($|[_-]|raphy)",
        # Shapes / square compounds / decorative marks
        r"^kumimoji-",
        r"^shapez_",
        r"(^|[_-])(outline|hollow)($|[_-])",
        r"(^|[_-])(icon|emoji|logo|ornament)($|[_-])",
    )
)

# Unresolved dump stroke data still embeds these component names (type-99).
GLYPH_EXCLUSION_DATA_MARKERS: tuple[str, ...] = (
    "hkcs_digit",
    "hkcs_u25a1",
    "hkcs_char-2192",
    "hkcs_char-ff1d",
    "-sans",  # sans-serif component strokes (e.g. u0021-sans)
    "_sans",
)

# KAGE stroke types used by mincho (Serif) drawing. Values may be stored as
# ``type`` or ``type + 100*opt`` (e.g. 101 → type 1); compare ``t % 100``.
MINCHO_STROKE_TYPES: frozenset[int] = frozenset({1, 2, 3, 4, 6, 7, 12, 99})


def is_non_mincho_stroke_data(data: str) -> bool:
    """True if stroke data selects a non-mincho shotai or exotic stroke type.

    GlyphWiki marks gothic / other styles with a type-0 option stroke
    ``0:99:N`` (N≠0). Mincho is the default (no marker, or ``0:99:0``).
    """
    if not data:
        return False
    for seg in data.split("$"):
        if not seg:
            continue
        parts = seg.split(":")
        try:
            t = int(float(parts[0]))
        except ValueError:
            return True
        if t < 0:
            return True
        if t == 0:
            try:
                a2 = int(float(parts[1])) if len(parts) > 1 else 0
                a3 = int(float(parts[2])) if len(parts) > 2 else 0
            except ValueError:
                return True
            # ``0:-1:-1:-1`` nop / deleted segment
            if a2 == -1 and a3 == -1:
                continue
            # ``0:99:N`` shotai: 0=mincho, nonzero=gothic / sideways / other
            if a2 == 99 and a3 != 0:
                return True
            continue
        if (t % 100) not in MINCHO_STROKE_TYPES:
            return True
    return False


def is_excluded_glyph_name(name: str) -> bool:
    """True if the GlyphWiki name matches an exclusion regex."""
    n = name.strip()
    return any(rx.search(n) for rx in GLYPH_EXCLUSION_NAME_RES)


def is_excluded_glyph_data(data: str) -> bool:
    """True if stroke data embeds excluded components or non-mincho strokes."""
    if not data:
        return False
    d = data.lower()
    if any(m in d for m in GLYPH_EXCLUSION_DATA_MARKERS):
        return True
    return is_non_mincho_stroke_data(data)


def is_excluded_glyph(name: str, data: str | None = None) -> bool:
    """True if this glyph should not receive a PUA ligature slot."""
    if is_excluded_glyph_name(name):
        return True
    if data is not None and is_excluded_glyph_data(data):
        return True
    return False


def filter_excluded_entries(
    entries: Sequence[tuple[str, str]],
    glyphs: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Drop overlay / annotation glyphs (name regex + embedded HKCS markers)."""
    glyphs = glyphs or {}
    out: list[tuple[str, str]] = []
    for name, related in entries:
        data = glyphs.get(name)
        if is_excluded_glyph(name, data):
            continue
        out.append((name, related))
    return out


# ---------------------------------------------------------------------------
# Empty / alias / duplicate-stroke exclusion
# ---------------------------------------------------------------------------

EMPTY_STROKE_DATA = "0:-1:-1:-1"

_U_FORM_NAME = re.compile(r"^u[0-9a-f]+(-|$)", re.IGNORECASE)


def is_empty_stroke_data(data: str | None) -> bool:
    """True for missing/blank GlyphWiki placeholders (``0:-1:-1:-1``)."""
    if data is None:
        return True
    d = data.strip()
    return not d or d == EMPTY_STROKE_DATA or d.startswith(EMPTY_STROKE_DATA)


def is_unusable_stroke_data(data: str | None) -> bool:
    """True if data is empty or not valid KAGE stroke syntax."""
    if is_empty_stroke_data(data):
        return True
    assert data is not None
    for seg in data.split("$"):
        if not seg:
            continue
        parts = seg.split(":")
        try:
            typ = int(float(parts[0]))
        except ValueError:
            return True
        # Type-99 must carry a component name (col 7).
        if typ == 99 and (len(parts) < 8 or not parts[7].strip()):
            return True
    return False


def alias_target(data: str | None) -> str | None:
    """Return the target name if ``data`` is a single full-frame type-99 alias."""
    if not data:
        return None
    segs = [s for s in data.split("$") if s]
    if len(segs) != 1:
        return None
    parts = segs[0].split(":")
    try:
        if int(float(parts[0])) != 99:
            return None
        x1 = float(parts[3])
        y1 = float(parts[4])
        x2 = float(parts[5])
        y2 = float(parts[6])
    except (ValueError, IndexError):
        return None
    if (x1, y1, x2, y2) != (0.0, 0.0, 200.0, 200.0):
        return None
    ref = parts[7].strip() if len(parts) > 7 else ""
    return ref or None


def filter_empty_stroke_entries(
    entries: Sequence[tuple[str, str]],
    glyphs: Mapping[str, str],
) -> list[tuple[str, str]]:
    """Drop glyphs whose dump (or resolved) stroke data is empty/unusable."""
    return [(n, r) for n, r in entries if not is_unusable_stroke_data(glyphs.get(n))]


def filter_alias_entries(
    entries: Sequence[tuple[str, str]],
    glyphs: Mapping[str, str],
) -> list[tuple[str, str]]:
    """Drop full-frame aliases whose target is also a packed entry.

    If the alias target is not in ``entries``, the alias is kept so the
    outline is not lost.
    """
    names = {n for n, _ in entries}
    out: list[tuple[str, str]] = []
    for name, related in entries:
        target = alias_target(glyphs.get(name))
        if target is not None:
            base = target.split("@", 1)[0]
            if target in names or base in names:
                continue
        out.append((name, related))
    return out


def _duplicate_keep_key(
    name: str,
    related: str,
    *,
    raw: str,
    stroke_counts: Mapping[str, int],
) -> tuple:
    """Lower is better: prefer non-alias, ``uXXXX`` forms, then normal sort."""
    return (
        0 if alias_target(raw) is None else 1,
        0 if _U_FORM_NAME.match(name) else 1,
        *parse_related_key(related),
        stroke_counts.get(name, 0),
        name,
    )


def filter_duplicate_stroke_entries(
    entries: Sequence[tuple[str, str]],
    strokes: Mapping[str, str],
    *,
    raw_glyphs: Mapping[str, str] | None = None,
    stroke_counts: Mapping[str, int] | None = None,
) -> list[tuple[str, str]]:
    """Keep one glyph per unique resolved stroke string; drop empties.

    Among duplicates, prefer a non-alias ``uXXXX…`` form, then the usual
    related-CP / stroke-count / name order.
    """
    raw_glyphs = raw_glyphs or {}
    counts = stroke_counts or {}
    best: dict[str, tuple[str, str, tuple]] = {}
    empty = 0
    for name, related in entries:
        data = strokes.get(name)
        if is_unusable_stroke_data(data):
            empty += 1
            continue
        assert data is not None
        key = _duplicate_keep_key(
            name,
            related,
            raw=raw_glyphs.get(name, ""),
            stroke_counts=counts,
        )
        prev = best.get(data)
        if prev is None or key < prev[2]:
            best[data] = (name, related, key)
    # Stable output order: follow incoming entry order among survivors
    keep = {t[0] for t in best.values()}
    return [(n, r) for n, r in entries if n in keep]


def sort_glyph_entries(
    entries: Sequence[tuple[str, str]],
    stroke_counts: Mapping[str, int] | None = None,
) -> list[tuple[str, str]]:
    """Sort ``(name, related)`` by related CP, stroke count, then name."""
    counts = stroke_counts or {}
    return sorted(
        entries,
        key=lambda nr: (*parse_related_key(nr[1]), counts.get(nr[0], 0), nr[0]),
    )


# ---------------------------------------------------------------------------
# Ligature mapping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VsSequence:
    """A custom two-codepoint Private-Use ligature."""

    marker: int  # SPUA
    index: int  # BMP PUA

    @property
    def chars(self) -> str:
        return chr(self.marker) + chr(self.index)

    @property
    def pair(self) -> list[int]:
        return [self.marker, self.index]

    def __str__(self) -> str:
        return f"U+{self.marker:X} U+{self.index:X}"


@dataclass(frozen=True)
class GlyphMapping:
    """One GlyphWiki glyph → PUA ligature pair."""

    name: str
    related: str
    marker: int
    pua: int

    @property
    def vs(self) -> VsSequence:
        return VsSequence(marker=self.marker, index=self.pua)

    @property
    def pair(self) -> list[int]:
        return [self.marker, self.pua]


def make_vs(marker: int, pua_index: int) -> VsSequence:
    """Build a ligature from a SPUA marker and a 0-based PUA slot (0..6399)."""
    if not is_spua(marker) or is_noncharacter(marker):
        raise ValueError(f"marker U+{marker:X} is not a usable Supplementary PUA")
    if not 0 <= pua_index < BMP_PUA_COUNT:
        raise ValueError(f"pua_index out of range: {pua_index}")
    return VsSequence(marker=marker, index=BMP_PUA_START + pua_index)


def mirror_vs(mode: int) -> int:
    """Return VS01..VS08 (U+E000..U+E007) for bucket/GlyphWiki D4 variants.

    Used directly after a Unicode character — no SPUA marker.
    Example: ``<U+4E00><U+E001>`` → 90° CCW 一.
    """
    return MirrorVS.codepoint(mode)


def mirror_sequence(base_cp: int, mode: int) -> list[int]:
    """``[unicode_cp, VS0n]`` for a mirrored bucket-font character."""
    return [base_cp, mirror_vs(mode)]


def index_to_ligature(ordinal: int) -> VsSequence:
    """Map a 0-based ordinal to ``(marker, pua)`` in row-major order."""
    if ordinal < 0:
        raise ValueError("ordinal must be >= 0")
    marker_i, pua_i = divmod(ordinal, BMP_PUA_COUNT)
    markers = iter_spua_markers(GLYPHWIKI_MARKER_START)
    marker = None
    for i, m in enumerate(markers):
        if i == marker_i:
            marker = m
            break
    if marker is None:
        raise ValueError(
            f"ordinal {ordinal} exceeds ligature capacity {ligature_capacity()}"
        )
    return VsSequence(marker=marker, index=BMP_PUA_START + pua_i)


def assign_ligatures(
    entries: Sequence[tuple[str, str]],
    stroke_counts: Mapping[str, int] | None = None,
) -> list[GlyphMapping]:
    """Assign SPUA+BMP-PUA ligatures to ``(name, related)`` entries.

    Sorted by related code point, then stroke count, then name. Raises if
    the dump outgrows Supplementary PUA × 6400 (practically unreachable).
    """
    ordered = sort_glyph_entries(entries, stroke_counts)
    needed = len(ordered)
    capacity = ligature_capacity()
    if needed > capacity:
        raise ValueError(f"{needed:,} glyphs exceed ligature capacity {capacity:,}")

    markers = iter_spua_markers(GLYPHWIKI_MARKER_START)
    marker = next(markers)
    slot = 0
    out: list[GlyphMapping] = []
    for name, related in ordered:
        if slot >= BMP_PUA_COUNT:
            marker = next(markers)
            slot = 0
        out.append(
            GlyphMapping(
                name=name,
                related=related,
                marker=marker,
                pua=BMP_PUA_START + slot,
            )
        )
        slot += 1
    return out


# Back-compat name
assign_codepoints = assign_ligatures


def mapping_to_dict(mappings: Iterable[GlyphMapping]) -> dict[str, list[int]]:
    """``glyph_name → [marker, pua]``."""
    return {m.name: m.pair for m in mappings}


def mappings_from_cmap(
    cmap: dict[str, list[int] | tuple[int, int]],
    related: dict[str, str] | None = None,
) -> list[GlyphMapping]:
    """Rebuild ``GlyphMapping`` rows from a saved ``name → [marker, pua]`` cmap."""
    related = related or {}
    out: list[GlyphMapping] = []
    for name, pair in cmap.items():
        if len(pair) < 2:
            continue
        out.append(
            GlyphMapping(
                name=name,
                related=related.get(name, name),
                marker=int(pair[0]),
                pua=int(pair[1]),
            )
        )
    return out


def markers_needed(glyph_count: int) -> int:
    return math.ceil(glyph_count / BMP_PUA_COUNT) if glyph_count else 0
