"""Shared cell geometry: half-, third-, and quarter-cell segments.

Half cells (``h`` face)
-----------------------
* ``U+FE08``–``U+FE0B`` half-planes and ``U+FE0C``–``U+FE0F`` diagonal triangles.
* ``U+FE00`` zero-width overlay (``.ov``) for digraph / trigraph stacking.
* D4 orientations on ``U+FE01``–``U+FE07``; clip/boolean pipeline with hole-
  winding repair after every cut.

Third cells (``t`` face)
------------------------
* ``VS17``–``VS26`` (``U+E0100``–``U+E0109``): vertical / horizontal thirds.

Quarter cells (``q`` / ``qv`` / ``qh`` faces)
---------------------------------------------
* Grid ``q``: ``VS41``–``VS48`` (2×2 corners + L 3/4).
* Vertical ``qv``: ``VS9``–``VS10``, ``VS27``–``VS33``.
* Horizontal ``qh``: ``VS11``–``VS12``, ``VS34``–``VS40``.

All segment forms are **slices** of baked outlines (clip + heal; never
``full − piece`` alone). See each section below for VS tables.
"""

from __future__ import annotations

import copy
import hashlib
import math
import os
import random
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import (
    ROUND_XY_TO_GRID,
    UNSCALED_COMPONENT_OFFSET,
    USE_MY_METRICS,
    GlyphComponent,
    GlyphCoordinates,
)
from fontTools.ttLib.tables._g_l_y_f import (
    Glyph as TTGlyph,
)

try:
    from kage.mapping import D4_MODES, MirrorVS
except ImportError:  # Scripts.* import style
    from Scripts.kage.mapping import D4_MODES, MirrorVS

from cape_weightor import (
    apply_width,
    bolden_horizontal_ttglyph,
    bolden_ttglyph,
    estimate_horizontal_stem,
    estimate_vertical_stem,
    layer_from_ttglyph,
    offset_layer,
    ttglyph_from_layer,
    widen_ttglyph,
)

# ---------- Constants ----------

YI_SYLLABLES = (0xA000, 0xA48C)
YI_RADICALS = (0xA490, 0xA4CF)

VS_BASE = 0xE000
# Full D4 set (8) — Yi orientations + build_cjk / GlyphWiki.
VS_COUNT = MirrorVS.MODE_COUNT
VS_LAST = VS_BASE + VS_COUNT - 1  # U+E007

# FE00 overlay; FE01–FE07 = r90..r90my. Identity has no FE* selector.
OV_SELECTOR_CP = 0xFE00
OV_SELECTOR_NAME = "vsOv"
OV_PUA_CP = 0xE008
STACK_MARK_CP = OV_SELECTOR_CP  # alias — GlyphWiki / older call sites
UVS_BASE = 0xFE01  # r90 .. r90my
UVS_LAST = 0xFE07

# Combining slices: preceding glyph occupies that segment.
# (cp, selector glyph name, suffix)
SliceSlot = Tuple[int, str, str]
SLICE_VS_SLOTS: Tuple[SliceSlot, ...] = (
    (0xFE08, "vsTop", "top"),
    (0xFE09, "vsBot", "bot"),
    (0xFE0A, "vsLeft", "left"),
    (0xFE0B, "vsRight", "right"),
    (0xFE0C, "vsTL", "tl"),  # top-left triangle (anti-diagonal)
    (0xFE0D, "vsBR", "br"),  # bottom-right triangle
    (0xFE0E, "vsTR", "tr"),  # top-right triangle (main diagonal)
    (0xFE0F, "vsBL", "bl"),  # bottom-left triangle
)
SLICE_HALF_SLOTS: Tuple[SliceSlot, ...] = SLICE_VS_SLOTS[:4]
SLICE_TRI_SLOTS: Tuple[SliceSlot, ...] = SLICE_VS_SLOTS[4:]
HALF_SUFFIXES: Tuple[str, ...] = ("top", "bot", "left", "right")
TRI_SUFFIXES: Tuple[str, ...] = ("tl", "br", "tr", "bl")
SLICE_SUFFIXES: Tuple[str, ...] = HALF_SUFFIXES + TRI_SUFFIXES
# PUA mirrors of overlay + eight slices (Blink drops unlisted VS before GSUB).
# Legacy BMP PUA mirrors (optional ``pua=True`` only). Kana owns U+E000–F8FF.
# E008 = overlay; E009–E010 = FE08–FE0F.
SLICE_PUA_SLOTS: Tuple[Tuple[int, str], ...] = (
    (OV_PUA_CP, OV_SELECTOR_NAME),
    (0xE009, "vsTop"),
    (0xE00A, "vsBot"),
    (0xE00B, "vsLeft"),
    (0xE00C, "vsRight"),
    (0xE00D, "vsTL"),
    (0xE00E, "vsBR"),
    (0xE00F, "vsTR"),
    (0xE010, "vsBL"),
)
SLICE_PUA_CPS: Tuple[int, ...] = tuple(cp for cp, _n in SLICE_PUA_SLOTS)
SLICE_LABELS: Dict[str, FrozenSet[str]] = {
    "top": frozenset({"tl", "tr"}),
    "bot": frozenset({"bl", "br"}),
    "left": frozenset({"tl", "bl"}),
    "right": frozenset({"tr", "br"}),
    "tl": frozenset({"tl", "tr", "bl"}),
    "br": frozenset({"tr", "br", "bl"}),
    "tr": frozenset({"tl", "tr", "br"}),
    "bl": frozenset({"tl", "bl", "br"}),
}

DEFAULT_UPEM = 1000
# Optional inset of the shared advance box inside the CJK cell (uniform).
STANDALONE_PAD = 0.0
HALFWIDTH_PAD = 0.0
COMPOUND_PAD = 0.0
# Standalone only: CAPE Weightor Width-mode factor after fit
# (0.15 → target outer width 115% of post-fit ink, stems preserved).
# Contour widen for ``make_standalone_glyph`` (CAPE Width). Default off —
# only kana opts into CAPE Weightor; Yi / Hangul / CJK standalones stay affine.
STANDALONE_CONTOUR_WIDEN = 0.0
# Inset from CJK typo top/bottom when fitting Y (fraction of em).
# Keeps short glyphs from sitting on the raw descent (-0.12em), which reads
# low next to CJK ink that usually rests nearer the baseline.
STANDALONE_VERT_PAD = 0.05
# After stretch / stem-normalize + CJK floor pin: uniform scale about the
# ideographic center so Yi ink occupies ~98% of the cell (still centered).
STANDALONE_CELL_SCALE = 0.98
# Anisotropic CJK-cell fit (shared sx ≠ sy) thins horizontal strokes.
# Y-only Weight-mode factor after that fit (1.25 = 125% horizontal stem).
STANDALONE_HORIZONTAL_WEIGHT = 1.25
# Legacy: was an extra shrink on scaled segment composites. Half / third /
# quarter segments are now **slices** (clip, no stretch); this constant is
# unused by that path and kept only for any external callers.
COMPOUND_CELL_SCALE = 0.90

# Match build_yi / build_cjk OS/2 + hhea (CJK ideographic body).
TYPO_ASCENDER_FRAC = 0.88
TYPO_DESCENDER_FRAC = -0.12

NUOSU_FILENAME = "NuosuSIL-Regular.ttf"

Bounds = Tuple[float, float, float, float]
GlyphMetrics = Tuple[TTGlyph, int, int]

# Keep GSUB subst subtables under Offset16 limits (and avoid hb.repack
# trying — and failing — to split ChainContext type 6).
GSUB_SUBST_CHUNK = 2048


def build_ext_gsub_lookup(subtables: Sequence) -> object:
    """GSUB lookup wrapped as Extension (type 7) for 32-bit offsets."""
    from fontTools.otlLib.builder import buildLookup

    return buildLookup(list(subtables), table="GSUB", extension=True)


def build_class_def(glyph_to_class: Dict[str, int]):
    """`ClassDef` from glyph→class map (class 0 omitted)."""
    from fontTools.ttLib.tables import otTables as ot

    cd = ot.ClassDef()
    cd.classDefs = {g: c for g, c in glyph_to_class.items() if c}
    return cd


def build_chain_context_format2(
    *,
    coverage_glyphs: Sequence[str],
    input_classes: Dict[str, int],
    input_class: int,
    backtrack_classes: Optional[Dict[str, int]] = None,
    lookahead_classes: Optional[Dict[str, int]] = None,
    backtrack_seq: Sequence[int] = (),
    lookahead_seq: Sequence[int] = (),
):
    """Compact class-based ChainContextSubst (Format 2), single input glyph.

    `backtrack_seq` is closest-to-input first (OpenType backtrack order).
    `SubstLookupRecord.LookupListIndex` is left 0 for the caller to patch.
    """
    from fontTools.ttLib.tables import otTables as ot

    st = ot.ChainContextSubst()
    st.Format = 2
    cov = ot.Coverage()
    cov.glyphs = list(coverage_glyphs)
    st.Coverage = cov
    st.BacktrackClassDef = build_class_def(backtrack_classes or {})
    st.InputClassDef = build_class_def(input_classes)
    st.LookAheadClassDef = build_class_def(lookahead_classes or {})

    max_in = max(input_classes.values(), default=0)
    class_sets: List[Optional[object]] = [None] * (max_in + 1)

    rule = ot.ChainSubClassRule()
    rule.Backtrack = list(backtrack_seq)
    rule.BacktrackGlyphCount = len(backtrack_seq)
    rule.Input = []
    rule.InputGlyphCount = 1
    rule.LookAhead = list(lookahead_seq)
    rule.LookAheadGlyphCount = len(lookahead_seq)
    rec = ot.SubstLookupRecord()
    rec.SequenceIndex = 0
    rec.LookupListIndex = 0
    rule.SubstLookupRecord = [rec]
    rule.SubstCount = 1

    cset = ot.ChainSubClassSet()
    cset.ChainSubClassRule = [rule]
    cset.ChainSubClassRuleCount = 1
    class_sets[input_class] = cset

    st.ChainSubClassSet = class_sets
    st.ChainSubClassSetCount = len(class_sets)
    return st


def build_chunked_single_subst_lookup(
    mapping: Dict[str, str], *, chunk: int = GSUB_SUBST_CHUNK
):
    from fontTools.otlLib.builder import buildSingleSubstSubtable

    items = list(mapping.items())
    subs = [
        buildSingleSubstSubtable(dict(items[i : i + chunk]))
        for i in range(0, len(items), chunk)
    ]
    return build_ext_gsub_lookup(subs)


def build_chunked_ligature_subst_lookup(
    mapping: Dict[Tuple[str, ...], str], *, chunk: int = GSUB_SUBST_CHUNK
):
    """Chunked GSUB LookupType 4 (ligature), Extension-wrapped."""
    from fontTools.otlLib.builder import buildLigatureSubstSubtable

    items = list(mapping.items())
    subs = [
        buildLigatureSubstSubtable(dict(items[i : i + chunk]))
        for i in range(0, len(items), chunk)
    ]
    return build_ext_gsub_lookup(subs)


def build_chunked_multiple_subst_lookup(
    mapping: Dict[str, List[str]], *, chunk: int = GSUB_SUBST_CHUNK
):
    from fontTools.otlLib.builder import buildMultipleSubstSubtable

    items = list(mapping.items())
    subs = [
        buildMultipleSubstSubtable(dict(items[i : i + chunk]))
        for i in range(0, len(items), chunk)
    ]
    return build_ext_gsub_lookup(subs)


def ideographic_center(target_upem: int) -> Tuple[float, float]:
    """Center of the CJK typo box (ascent 0.88em / descent -0.12em).

    Geometric em midpoint is `(upem/2, upem/2)`; CJK ink after uniform
    UPM scale sits near `(upem/2, 0.38·upem)`. Centering Yi there keeps
    mixed CJK+Yi lines vertically aligned.
    """
    bottom, top, _h = ideographic_bounds(target_upem)
    return target_upem / 2.0, (top + bottom) / 2.0


def ideographic_bounds(target_upem: int) -> Tuple[float, float, float]:
    """CJK typo box `(bottom, top, height)` using ascent 0.88 / descent -0.12."""
    top = target_upem * TYPO_ASCENDER_FRAC
    bottom = target_upem * TYPO_DESCENDER_FRAC
    return bottom, top, top - bottom


# (vs_cp, rot90_quarters, flip_x, flip_y, name_suffix or None for identity)
# Shared with build_cjk / GlyphWiki via kage.mapping.D4_MODES.
TransformMode = Tuple[int, int, bool, bool, Optional[str]]

TRANSFORM_MODES: List[TransformMode] = [
    (MirrorVS.codepoint(mode), rot, fx, fy, suffix)
    for mode, rot, fx, fy, suffix in D4_MODES
]

# Yi uses the full D4 set (VS01..VS08), same as TRANSFORM_MODES.
YI_ORIENTATION_MODES: List[TransformMode] = TRANSFORM_MODES

# 90°/270° orientations (incl. diagonals).
SIDEWAYS_SUFFIXES = frozenset({"r90", "r270", "r90mx", "r90my"})
# Only ``r90`` is baked; these are axis-aligned TT composites of that outline.
# ``r270`` = r180(r90); ``r90mx`` = reflect-Y(r90); ``r90my`` = reflect-X(r90).
SIDEWAYS_FROM_R90: Dict[str, Tuple[int, bool, bool]] = {
    "r270": (2, False, False),
    "r90mx": (0, True, False),
    "r90my": (0, False, True),
}
# Axis-aligned composites of the upright (id) outline — never re-baked.
UPRIGHT_COMPOSITE_SUFFIXES = frozenset({"r180", "mx", "my"})


def ink_width(glyph: TTGlyph) -> float:
    try:
        glyph.recalcBounds(None)
        return float(glyph.xMax - glyph.xMin)
    except Exception:
        return 0.0


def ink_height(glyph: TTGlyph) -> float:
    try:
        glyph.recalcBounds(None)
        return float(glyph.yMax - glyph.yMin)
    except Exception:
        return 0.0


def measure_upright_stems(glyph: TTGlyph, advance: float) -> Tuple[float, float]:
    """`(vertical_stem, horizontal_stem)` from an upright Yi standalone."""
    layer = layer_from_ttglyph(glyph, advance)
    return estimate_vertical_stem(layer), estimate_horizontal_stem(layer)


def average_ink_width(glyphs: Sequence[TTGlyph]) -> float:
    widths = [ink_width(g) for g in glyphs]
    widths = [w for w in widths if w > 1.0]
    if not widths:
        return 0.0
    return sum(widths) / len(widths)


def average_ink_height(glyphs: Sequence[TTGlyph]) -> float:
    heights = [ink_height(g) for g in glyphs]
    heights = [h for h in heights if h > 1.0]
    if not heights:
        return 0.0
    return sum(heights) / len(heights)


# Fixed post-transform stem targets (target-UPM units @ 1000).
REFERENCE_VERTICAL_STEM = 70.0  # match U+4E28-like vertical weight
REFERENCE_HORIZONTAL_STEM = 60.0  # match U+4E00-like horizontal weight

# Post-offset contour heal: snap near-coincident on-curve points (UPM@1000).
CONTOUR_SNAP_EPSILON = 1.5
# Cap each stem-offset axis step so large estimate errors don't shred joins.
MAX_STEM_OFFSET_STEP = 10.0
# After normalize, reject if a stem falls below this fraction of its reference
# (or of the pre-normalize stem).
MIN_NORM_STEM_FRAC = 0.4
# Pseudorandom stem-target probes, then binary search toward the reference.
NORM_RANDOM_PROBES = 16
NORM_BINARY_ITERS = 8
NORM_SCALE_LO = 0.5
NORM_SCALE_HI = 1.5

# CJK single-stroke references (optional measure; builds use fixed targets above).
CJK_REF_HORIZONTAL_CP = 0x4E00  # 一 — horizontal stroke weight
CJK_REF_VERTICAL_CP = 0x4E28  # 丨 — vertical stroke weight
CJK_STEM_REF_FONT_CANDIDATES: Tuple[str, ...] = (
    "PlangothicP1-Regular.ttf",
    "PlangothicP2-Regular.ttf",
    "LXGWNeoXiHei.ttf",
    "LXGWClearGothic-Regular.ttf",
    "ChocolateClassicalSans-Regular.ttf",
    "I.Ming-8.10.ttf",
    "Microsoft-JhengHei.ttf",
)


def _nudge_near_points_on_glyph(
    glyph: TTGlyph,
    *,
    epsilon: float = CONTOUR_SNAP_EPSILON,
) -> TTGlyph:
    """Snap near-coincident on-curve points so broken joins re-meet."""
    if glyph.isComposite() or glyph.numberOfContours <= 0:
        return glyph
    try:
        coords = list(glyph.coordinates)
        end_pts = list(glyph.endPtsOfContours)
        flags = list(glyph.flags)
    except Exception:
        return glyph

    n = len(coords)
    if n < 2:
        return glyph

    # Collect on-curve indices (bit 0 of TrueType flags).
    on_curve = [i for i in range(n) if (flags[i] & 0x01) != 0]
    if len(on_curve) < 2:
        return glyph

    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    eps2 = epsilon * epsilon
    for i_idx, i in enumerate(on_curve):
        xi, yi = float(coords[i][0]), float(coords[i][1])
        for j in on_curve[i_idx + 1 :]:
            xj, yj = float(coords[j][0]), float(coords[j][1])
            dx, dy = xi - xj, yi - yj
            if dx * dx + dy * dy <= eps2:
                union(i, j)

    start = 0
    for end in end_pts:
        contour_on = [i for i in range(start, end + 1) if (flags[i] & 0x01) != 0]
        for k, i in enumerate(contour_on):
            j = contour_on[(k + 1) % len(contour_on)]
            if i == j:
                continue
            xi, yi = float(coords[i][0]), float(coords[i][1])
            xj, yj = float(coords[j][0]), float(coords[j][1])
            if (xi - xj) ** 2 + (yi - yj) ** 2 <= eps2:
                union(i, j)
        start = end + 1

    clusters: Dict[int, List[int]] = {}
    for i in on_curve:
        clusters.setdefault(find(i), []).append(i)

    changed = False
    for members in clusters.values():
        if len(members) < 2:
            continue
        ax = sum(float(coords[i][0]) for i in members) / len(members)
        ay = sum(float(coords[i][1]) for i in members) / len(members)
        sx, sy = otRound(ax), otRound(ay)
        for i in members:
            if coords[i] != (sx, sy):
                coords[i] = (sx, sy)
                changed = True
    if not changed:
        return glyph

    glyph.coordinates = GlyphCoordinates(coords)
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    return glyph


def _collapse_spike_points_on_glyph(
    glyph: TTGlyph,
    *,
    sharp_cos: float = 0.55,
    min_protrusion: float = 10.0,
    max_protrusion: float = 120.0,
) -> TTGlyph:
    """Pull back needle-like miter spikes toward the neighbor chord.

    Only true tips: angle APB acute (`cos > sharp_cos` ≈ <60°) and P far
    from chord AB relative to |AB|. Ordinary corners / smooth curves are left
    alone — an earlier looser threshold melted whole outlines.
    """
    import math

    if glyph.isComposite() or glyph.numberOfContours <= 0:
        return glyph
    try:
        coords = list(glyph.coordinates)
        end_pts = list(glyph.endPtsOfContours)
        flags = list(glyph.flags)
    except Exception:
        return glyph

    changed = False
    start = 0
    for end in end_pts:
        on_idx = [i for i in range(start, end + 1) if (flags[i] & 0x01) != 0]
        m = len(on_idx)
        if m < 3:
            start = end + 1
            continue
        for k, i in enumerate(on_idx):
            ia = on_idx[k - 1]
            ib = on_idx[(k + 1) % m]
            ax, ay = float(coords[ia][0]), float(coords[ia][1])
            px, py = float(coords[i][0]), float(coords[i][1])
            bx, by = float(coords[ib][0]), float(coords[ib][1])
            vax, vay = ax - px, ay - py
            vbx, vby = bx - px, by - py
            la = math.hypot(vax, vay)
            lb = math.hypot(vbx, vby)
            if la < 1e-6 or lb < 1e-6:
                continue
            # Spike tip: A and B both behind P → acute APB → cos → +1.
            cos_t = (vax * vbx + vay * vby) / (la * lb)
            if cos_t < sharp_cos:
                continue
            abx, aby = bx - ax, by - ay
            ab_len = math.hypot(abx, aby)
            if ab_len < 1e-6:
                continue
            dist = abs((px - ax) * aby - (py - ay) * abx) / ab_len
            # Needle: sticks out farther than the base width between A and B.
            if dist < min_protrusion or dist > max_protrusion or dist < 0.75 * ab_len:
                continue
            t = ((px - ax) * abx + (py - ay) * aby) / (ab_len * ab_len)
            t = max(0.0, min(1.0, t))
            sx, sy = otRound(ax + t * abx), otRound(ay + t * aby)
            if coords[i] != (sx, sy):
                coords[i] = (sx, sy)
                changed = True
        start = end + 1

    if not changed:
        return glyph
    glyph.coordinates = GlyphCoordinates(coords)
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    return glyph


def cleanup_ttglyph_contours(
    glyph: TTGlyph,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    upem: int = DEFAULT_UPEM,
    snap_epsilon: float = CONTOUR_SNAP_EPSILON,
) -> TTGlyph:
    """Nudge broken joins after stem offset (spike collapse + near-point snap).

    Avoids boolean union/simplify here: those shred self-intersecting ribbons
    into odd-even holes on Yi outlines. Only needle miter spikes are pulled
    back; near-coincident on-curve points are snapped so joins re-meet.
    """
    del glyph_set, upem  # reserved for future pathops path
    if glyph.numberOfContours == 0:
        return glyph
    out = _collapse_spike_points_on_glyph(glyph)
    out = _nudge_near_points_on_glyph(out, epsilon=snap_epsilon)
    return out


def resolve_cjk_stem_reference_font(in_dir: str) -> str:
    """First font under `in_dir` that has both U+4E00 and U+4E28."""
    for name in CJK_STEM_REF_FONT_CANDIDATES:
        path = os.path.join(in_dir, name)
        if not os.path.isfile(path):
            continue
        tt = TTFont(path, fontNumber=0)
        try:
            cmap: Dict[int, str] = {}
            for table in tt["cmap"].tables:
                if table.isUnicode():
                    cmap.update(table.cmap)
            if CJK_REF_HORIZONTAL_CP in cmap and CJK_REF_VERTICAL_CP in cmap:
                return os.path.normpath(path)
        finally:
            tt.close()
    raise FileNotFoundError(
        f"No CJK stem-reference font with U+4E00/U+4E28 under {in_dir!r}"
    )


def _scaled_source_glyph(
    tt: TTFont,
    glyph_name: str,
    *,
    scale: float,
) -> Tuple[TTGlyph, float]:
    """Decompose `glyph_name` and scale uniformly to target UPM space."""
    glyph_set = tt.getGlyphSet()
    rec = DecomposingRecordingPen(glyph_set)
    glyph_set[glyph_name].draw(rec)
    pen = TTGlyphPen(None)
    rec.replay(TransformPen(pen, Transform(scale, 0, 0, scale, 0, 0)))
    glyph = pen.glyph()
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    adv = float(tt["hmtx"].metrics[glyph_name][0]) * scale
    return glyph, adv


def measure_cjk_reference_stems(
    font_path: str,
    target_upem: int,
) -> Tuple[float, float]:
    """Fixed `(vertical, horizontal)` stroke weights from U+4E28 / U+4E00.

    Single-stroke CJK radicals are measured by ink bbox thickness after a
    uniform `target_upem / source_upem` scale (scanline stem estimators
    reject these glyphs as “too thick” relative to their own bbox).
    """
    tt = TTFont(font_path, fontNumber=0)
    try:
        cmap: Dict[int, str] = {}
        for table in tt["cmap"].tables:
            if table.isUnicode():
                cmap.update(table.cmap)
        h_name = cmap.get(CJK_REF_HORIZONTAL_CP)
        v_name = cmap.get(CJK_REF_VERTICAL_CP)
        if not h_name or not v_name:
            raise KeyError(
                f"{os.path.basename(font_path)} missing U+4E00 and/or U+4E28"
            )
        src_upem = float(tt["head"].unitsPerEm) or float(target_upem)
        scale = float(target_upem) / src_upem
        g_h, _adv_h = _scaled_source_glyph(tt, h_name, scale=scale)
        g_v, _adv_v = _scaled_source_glyph(tt, v_name, scale=scale)
        try:
            g_h.recalcBounds(None)
            horizontal = float(g_h.yMax) - float(g_h.yMin)
        except Exception:
            horizontal = 0.0
        try:
            g_v.recalcBounds(None)
            vertical = float(g_v.xMax) - float(g_v.xMin)
        except Exception:
            vertical = 0.0
        if vertical <= 1.0 or horizontal <= 1.0:
            raise ValueError(
                f"degenerate 4E00/4E28 stems in {os.path.basename(font_path)}: "
                f"V={vertical:g} H={horizontal:g}"
            )
        return vertical, horizontal
    finally:
        tt.close()


def normalize_glyph_stems_after_transform(
    glyph: TTGlyph,
    advance: int,
    *,
    vertical_stem: float,
    horizontal_stem: float,
    target_ink_width: Optional[float] = None,
    center_x: Optional[float] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    stem_passes: int = 4,
) -> GlyphMetrics:
    """Measure post-conversion H/V stems, then match reference weights on both axes.

    1. Bake composites to outlines if needed (the glyph after conversion).
    2. Measure current vertical / horizontal stroke thickness.
    3. Contour-offset X and Y so both match `vertical_stem` / `horizontal_stem`
       (typically from `measure_cjk_reference_stems`: U+4E28 / U+4E00).
    4. Optionally Width-fit outer ink (legacy; unused by the D4 path).

    Stem matching is iterated a few times because a single offset undershoots
    on complex outlines.
    """
    if glyph.isComposite():
        if glyph_set is None:
            raise ValueError(
                "normalize_glyph_stems_after_transform needs glyph_set "
                "to bake composite inputs"
            )
        glyph, advance, _ = _bake_transformed_glyph(
            glyph, Transform(), int(advance), glyph_set=glyph_set
        )

    try:
        glyph.recalcBounds(None)
        cx0 = (glyph.xMin + glyph.xMax) / 2.0
        cy0 = (glyph.yMin + glyph.yMax) / 2.0
    except Exception:
        cx0 = float(advance) / 2.0 if advance else 500.0
        cy0 = 380.0
    if center_x is None:
        center_x = cx0

    layer = layer_from_ttglyph(glyph, float(advance))
    # Offset polarity depends on winding. Reflections reverse contours, so
    # probe once after convert: default is “−X thickens V / +Y thins H”.
    sign_x = 1.0
    sign_y = 1.0
    probe = 2.0
    v0 = estimate_vertical_stem(layer)
    if v0 > 0:
        offset_layer(layer, -probe, 0.0)
        v1 = estimate_vertical_stem(layer)
        offset_layer(layer, probe, 0.0)
        if v1 + 1e-6 < v0:
            # −X thinned V → flip X polarity.
            sign_x = -1.0
    h0 = estimate_horizontal_stem(layer)
    if h0 > 0:
        offset_layer(layer, 0.0, probe)
        h1 = estimate_horizontal_stem(layer)
        offset_layer(layer, 0.0, -probe)
        if h1 + 1e-6 > h0:
            # +Y thickened H → flip Y polarity.
            sign_y = -1.0

    def _axis_delta(cur: float, target: float, sign: float) -> float:
        """Offset for one axis; skip when the stem read looks unreliable."""
        if target <= 0 or cur <= 0:
            return 0.0
        # Complex curves (bowls, zigzags) often report a span that isn't a
        # stroke — yanking those to the reference shreds joins into spikes.
        ratio = cur / target
        if ratio < 0.5 or ratio > 1.85:
            return 0.0
        d = sign * (cur - target) / 2.0
        return max(-MAX_STEM_OFFSET_STEP, min(MAX_STEM_OFFSET_STEP, d))

    for _ in range(max(1, stem_passes)):
        cur_v = estimate_vertical_stem(layer)
        cur_h = estimate_horizontal_stem(layer)
        dx = _axis_delta(cur_v, vertical_stem, sign_x)
        dy = _axis_delta(cur_h, horizontal_stem, sign_y)
        if abs(dx) < 0.25 and abs(dy) < 0.25:
            break
        offset_layer(layer, dx, dy)

    if target_ink_width is not None and target_ink_width > 1.0:
        bw = layer.bounds.size.x
        if bw > 1.0:
            factor = target_ink_width / bw
            stem = vertical_stem if vertical_stem > 0 else None
            apply_width(layer, factor, stem=stem, center_x=center_x)
            # Width-fit can drift stems — re-match both axes once.
            cur_v = estimate_vertical_stem(layer)
            cur_h = estimate_horizontal_stem(layer)
            dx = _axis_delta(cur_v, vertical_stem, sign_x)
            dy = _axis_delta(cur_h, horizontal_stem, sign_y)
            if abs(dx) > 0.25 or abs(dy) > 0.25:
                offset_layer(layer, dx, dy)

    # Keep the pre-fit contour center.
    try:
        b = layer.bounds
        cx1 = b.origin.x + 0.5 * b.size.x
        cy1 = b.origin.y + 0.5 * b.size.y
        mid_x = cx0 - cx1
        mid_y = cy0 - cy1
        if abs(mid_x) > 1e-6 or abs(mid_y) > 1e-6:
            layer.applyTransform((1, 0, 0, 1, mid_x, mid_y))
    except Exception:
        pass

    out, _out_adv, out_lsb = ttglyph_from_layer(layer)
    # Stem offset often leaves self-intersections / spikes at sharp joins —
    # union+simplify and nudge near points so fills stay solid.
    out = cleanup_ttglyph_contours(out, upem=DEFAULT_UPEM)
    try:
        out.recalcBounds(None)
        out_lsb = int(out.xMin)
    except Exception:
        pass
    return out, int(advance), int(out_lsb)


def fit_sideways_yi_glyph(
    glyph: TTGlyph,
    advance: int,
    *,
    target_ink_width: float,
    vertical_stem: float,
    horizontal_stem: float,
    center_x: Optional[float] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> GlyphMetrics:
    """Normalize stems then Width-mode squash to `target_ink_width`.

    Thin wrapper around `normalize_glyph_stems_after_transform` for the
    sideways (r90-family) path.
    """
    return normalize_glyph_stems_after_transform(
        glyph,
        advance,
        vertical_stem=vertical_stem,
        horizontal_stem=horizontal_stem,
        target_ink_width=target_ink_width,
        center_x=center_x,
        glyph_set=glyph_set,
    )


def _measure_glyph_stems(
    glyph: TTGlyph,
    advance: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[float, float]:
    """`(vertical_stem, horizontal_stem)`; bakes composites first."""
    g = glyph
    adv = int(advance)
    if g.isComposite():
        if glyph_set is None:
            return 0.0, 0.0
        g, adv, _ = _bake_transformed_glyph(g, Transform(), adv, glyph_set=glyph_set)
    layer = layer_from_ttglyph(g, float(adv))
    return estimate_vertical_stem(layer), estimate_horizontal_stem(layer)


def _glyph_self_intersects(
    glyph: TTGlyph,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> bool:
    """Heuristic: pathops simplify explodes/empties self-intersecting ribbons."""
    try:
        import pathops
    except ImportError:
        return False
    try:
        sk = pathops.Path()
        _recording_from_glyph(glyph, glyph_set).replay(sk.getPen())
        raw_contours = list(sk.contours)
        if not raw_contours:
            return False
        try:
            simp = pathops.simplify(sk, fix_winding=True, clockwise=True)
        except Exception:
            return True
        simp_contours = list(simp.contours)
        if not simp_contours:
            return True
        # Self-intersecting offset ribbons often shatter into many shreds.
        if len(simp_contours) > len(raw_contours) + 3:
            return True
        rb = sk.bounds
        sb = simp.bounds
        raw_area = max(0.0, (rb[2] - rb[0]) * (rb[3] - rb[1]))
        simp_area = max(0.0, (sb[2] - sb[0]) * (sb[3] - sb[1]))
        if raw_area > 1.0 and simp_area < raw_area * 0.45:
            return True
    except Exception:
        return False
    return False


def _norm_result_ok(
    glyph: TTGlyph,
    advance: int,
    *,
    pre_v: float,
    pre_h: float,
    target_v: float,
    target_h: float,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    abs_min_stem: float = 18.0,
) -> bool:
    """Accept normalize only when stems stay thick and outlines stay simple."""
    post_v, post_h = _measure_glyph_stems(glyph, advance, glyph_set=glyph_set)
    for post, pre, tgt in (
        (post_v, pre_v, target_v),
        (post_h, pre_h, target_h),
    ):
        if post <= 0:
            continue
        if post < abs_min_stem:
            return False
        # Collapsed vs pre-normalize stem.
        if pre > 0 and post < pre * MIN_NORM_STEM_FRAC:
            return False
        # Badly undershot this attempt's target.
        if tgt > 0 and post < tgt * MIN_NORM_STEM_FRAC:
            return False
    if _glyph_self_intersects(glyph, glyph_set=glyph_set):
        return False
    return True


def _norm_seed_from_glyph(glyph: TTGlyph) -> int:
    """Stable RNG seed from outline samples (reproducible across rebuilds)."""
    h = hashlib.blake2b(digest_size=8)
    try:
        if glyph.isComposite() or glyph.numberOfContours <= 0:
            h.update(b"empty")
        else:
            h.update(str(glyph.numberOfContours).encode())
            # Sample a few coordinates — enough entropy, cheap.
            coords = glyph.coordinates
            step = max(1, len(coords) // 32)
            for i in range(0, len(coords), step):
                x, y = coords[i]
                h.update(f"{int(x)}:{int(y)};".encode())
    except Exception:
        h.update(b"fallback")
    return int.from_bytes(h.digest(), "little")


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _random_stem_target(
    rng: random.Random,
    pre_v: float,
    pre_h: float,
    ref_v: float,
    ref_h: float,
) -> Tuple[float, float]:
    """One pseudorandom (V, H) stem target in a plausible search band."""
    match rng.randrange(3):
        case 0:
            # Shared blend from pre → ref.
            t = rng.uniform(0.05, 1.0)
            tv = _lerp(pre_v, ref_v, t) if pre_v > 0 else ref_v * t
            th = _lerp(pre_h, ref_h, t) if pre_h > 0 else ref_h * t
        case 1:
            # Independent blends per axis.
            tv = (
                _lerp(pre_v, ref_v, rng.uniform(0.05, 1.0))
                if pre_v > 0
                else ref_v * rng.uniform(0.05, 1.0)
            )
            th = (
                _lerp(pre_h, ref_h, rng.uniform(0.05, 1.0))
                if pre_h > 0
                else ref_h * rng.uniform(0.05, 1.0)
            )
        case _:
            # Absolute scales of the reference (larger / smaller).
            tv = ref_v * rng.uniform(NORM_SCALE_LO, NORM_SCALE_HI)
            th = ref_h * rng.uniform(NORM_SCALE_LO, NORM_SCALE_HI)
    return max(1.0, tv), max(1.0, th)


def normalize_glyph_stems_with_retry(
    glyph: TTGlyph,
    advance: int,
    *,
    vertical_stem: float,
    horizontal_stem: float,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> GlyphMetrics:
    """Transform-ready glyph → probe stem targets, then binary-search to ref.

    1. Try the full reference once.
    2. Pseudorandomly sample targets around pre/ref (deterministic seed).
    3. From the working sample closest to the reference, binary-search toward
       the full reference for the strongest still-valid normalize.
    4. If nothing works, keep the un-normalized outline.
    """
    if glyph.isComposite():
        if glyph_set is None:
            raise ValueError(
                "normalize_glyph_stems_with_retry needs glyph_set "
                "to bake composite inputs"
            )
        glyph, advance, _ = _bake_transformed_glyph(
            glyph, Transform(), int(advance), glyph_set=glyph_set
        )

    pre_v, pre_h = _measure_glyph_stems(glyph, advance, glyph_set=glyph_set)
    try:
        glyph.recalcBounds(None)
        raw_lsb = int(glyph.xMin)
    except Exception:
        raw_lsb = 0

    def _try(tv: float, th: float) -> Optional[GlyphMetrics]:
        norm_g, norm_a, norm_l = normalize_glyph_stems_after_transform(
            glyph,
            advance,
            vertical_stem=tv,
            horizontal_stem=th,
            target_ink_width=None,
            glyph_set=glyph_set,
        )
        if _norm_result_ok(
            norm_g,
            norm_a,
            pre_v=pre_v,
            pre_h=pre_h,
            target_v=tv,
            target_h=th,
            glyph_set=glyph_set,
        ):
            return norm_g, norm_a, norm_l
        return None

    def _ref_dist(tv: float, th: float) -> float:
        return abs(tv - vertical_stem) + abs(th - horizontal_stem)

    # 1) Full reference.
    hit = _try(vertical_stem, horizontal_stem)
    if hit is not None:
        return hit

    # 2) Pseudorandom probes (stable per glyph).
    rng = random.Random(_norm_seed_from_glyph(glyph))
    best_tv = best_th = 0.0
    best_gm: Optional[GlyphMetrics] = None
    best_dist = float("inf")
    for _ in range(max(1, NORM_RANDOM_PROBES)):
        tv, th = _random_stem_target(rng, pre_v, pre_h, vertical_stem, horizontal_stem)
        got = _try(tv, th)
        if got is None:
            continue
        d = _ref_dist(tv, th)
        if d < best_dist:
            best_dist = d
            best_tv, best_th = tv, th
            best_gm = got

    if best_gm is None:
        return glyph, int(advance), raw_lsb

    # 3) Binary-search from the best working probe toward the full reference.
    lo_tv, lo_th = best_tv, best_th
    hi_tv, hi_th = vertical_stem, horizontal_stem
    result = best_gm
    for _ in range(max(1, NORM_BINARY_ITERS)):
        mid_tv = 0.5 * (lo_tv + hi_tv)
        mid_th = 0.5 * (lo_th + hi_th)
        if abs(mid_tv - lo_tv) < 0.25 and abs(mid_th - lo_th) < 0.25:
            break
        got = _try(mid_tv, mid_th)
        if got is not None:
            lo_tv, lo_th = mid_tv, mid_th
            result = got
        else:
            hi_tv, hi_th = mid_tv, mid_th

    return result


def add_d4_variant_glyphs(
    base_name: str,
    *,
    advance: int,
    lsb: int,
    target_upem: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    modes: Optional[Sequence[TransformMode]] = None,
    sideways_target_width: Optional[float] = None,
    sideways_center_x: Optional[float] = None,
    reference_vertical_stem: Optional[float] = None,
    reference_horizontal_stem: Optional[float] = None,
    anchor: str = "floor",
    pivot: Optional[Tuple[float, float]] = None,
) -> List[Tuple[int, str, str]]:
    """Create D4 forms from two outlines: id + `r90` (transform, then normalize).

    Pipeline for each outline source::

        1. transform / reorient (id = identity; r90 = rotate from **un-normalized** upright)
        2. stem-normalize, retrying smaller/larger targets until strokes stay
           thick and non-self-intersecting (else keep step-1)
        3. place ink: `anchor="floor"` pins to padded CJK floor then
           downscales ~98% about the ideographic center (Yi);
           `anchor="cell"` centers in the padded cell, shrink-only (CJK)

    Other orientations are baked rotate/reflect outlines of `id` or `r90`
    (no further stem pass)::

        id  →  r180 / mx / my
        r90 →  r270 / r90mx / r90my

    `pivot` overrides the rotation/reflection center (e.g. post-scale small
    ideographic center). Default: ideographic center when `anchor="cell"`.

    `sideways_target_width` / `sideways_center_x` are unused (compat only).

    Returns `[(vs_cp, suffix, variant_glyph_name), ...]` for GSUB wiring.
    """
    del sideways_target_width, sideways_center_x
    if anchor not in ("floor", "cell"):
        raise ValueError(f"anchor must be 'floor' or 'cell', got {anchor!r}")

    installed: List[Tuple[int, str, str]] = []
    use_modes = modes if modes is not None else TRANSFORM_MODES
    r90_name = variant_glyph_name(base_name, "r90")
    use_ref = (
        reference_vertical_stem is not None
        and reference_horizontal_stem is not None
        and reference_vertical_stem > 0
        and reference_horizontal_stem > 0
    )
    ref_v = float(reference_vertical_stem or 0.0)
    ref_h = float(reference_horizontal_stem or 0.0)
    need_r90 = any(
        suffix == "r90" or suffix in SIDEWAYS_FROM_R90
        for _vs, _r, _fx, _fy, suffix in use_modes
        if suffix is not None
    )
    match pivot:
        case (px, py):
            cell_mid = (float(px), float(py))
        case None if anchor == "cell":
            cell_mid = ideographic_center(target_upem)
        case _:
            cell_mid = None

    def _install(name: str, glyph: TTGlyph, adv: int, glyph_lsb: int) -> None:
        if name in glyphs:
            glyphs[name] = glyph
            metrics[name] = (adv, glyph_lsb)
            return
        glyph_order.append(name)
        glyphs[name] = glyph
        metrics[name] = (adv, glyph_lsb)

    def _place(glyph: TTGlyph, adv: int) -> GlyphMetrics:
        if anchor == "cell":
            return fit_glyph_to_ideographic_cell(
                glyph, adv, target_upem, glyph_set=glyphs
            )
        pinned, pinned_adv, _ = pin_glyph_ink_to_cjk_floor(
            glyph, adv, target_upem, glyph_set=glyphs
        )
        g, a, l = scale_glyph_in_ideographic_cell(
            pinned,
            pinned_adv,
            target_upem,
            scale=STANDALONE_CELL_SCALE,
            glyph_set=glyphs,
        )
        g = center_glyph_ink_x(g, target_upem)
        try:
            g.recalcBounds(glyphs if g.isComposite() else None)
            l = int(g.xMin)
        except Exception:
            pass
        return g, int(a), int(l)

    def _composite_from(
        parent_name: str,
        parent_glyph: TTGlyph,
        parent_adv: int,
        parent_lsb: int,
        *,
        rot90_quarters: int,
        flip_x: bool,
        flip_y: bool,
    ) -> GlyphMetrics:
        return make_composite_variant(
            parent_name,
            target_upem,
            rot90_quarters=rot90_quarters,
            flip_x=flip_x,
            flip_y=flip_y,
            advance=parent_adv,
            lsb=parent_lsb,
            base_glyph=parent_glyph,
            glyph_set=glyphs,
            center=cell_mid,
            # Bake every orientation to outlines (see make_composite_variant).
            allow_2x2=False,
        )

    def _keep(glyph: TTGlyph, adv: int) -> GlyphMetrics:
        """Identity metrics refresh (no second ideo-square map)."""
        try:
            if glyph.isComposite():
                glyph.recalcBounds(glyphs)
            else:
                glyph.recalcBounds(None)
            return glyph, int(adv), int(glyph.xMin)
        except Exception:
            return glyph, int(adv), 0

    def _transform_then_normalize(
        transformed: TTGlyph,
        adv: int,
    ) -> GlyphMetrics:
        """Reorient first, then normalize with target retries."""
        if transformed.isComposite():
            baked, adv, _ = _bake_transformed_glyph(
                transformed, Transform(), int(adv), glyph_set=glyphs
            )
        else:
            baked = transformed
        if not use_ref:
            # CJK: caller already cell-fitted; Yi: floor-pin.
            return _keep(baked, adv) if anchor == "cell" else _place(baked, adv)

        norm_g, norm_a, _norm_l = normalize_glyph_stems_with_retry(
            baked,
            adv,
            vertical_stem=ref_v,
            horizontal_stem=ref_h,
            glyph_set=glyphs,
        )
        return _keep(norm_g, norm_a) if anchor == "cell" else _place(norm_g, norm_a)

    # Keep the un-normalized upright as the transform source for r90.
    src_glyph = glyphs[base_name]
    src_adv, src_lsb = int(metrics[base_name][0]), int(metrics[base_name][1])

    # 1) id: identity transform, then place (floor-pin or keep cell fit).
    g0, a0, l0 = _transform_then_normalize(src_glyph, src_adv)
    glyphs[base_name] = g0
    metrics[base_name] = (int(a0), int(l0))
    advance, lsb = int(a0), int(l0)

    # 2) r90: from un-normalized upright (Yi) or fitted upright (CJK cell).
    if need_r90 and r90_name not in glyphs:
        if anchor == "cell":
            # Pure rotate about cell center — do not re-map the ideo square.
            r90_glyph, r90_adv, r90_lsb = _composite_from(
                base_name,
                glyphs[base_name],
                advance,
                lsb,
                rot90_quarters=1,
                flip_x=False,
                flip_y=False,
            )
            r90_glyph, r90_adv, r90_lsb = _keep(r90_glyph, r90_adv)
        else:
            r90_raw, r90_adv, _r90_lsb = _composite_from(
                base_name,
                src_glyph,
                src_adv,
                src_lsb,
                rot90_quarters=1,
                flip_x=False,
                flip_y=False,
            )
            r90_glyph, r90_adv, r90_lsb = _transform_then_normalize(r90_raw, r90_adv)
        _install(r90_name, r90_glyph, r90_adv, r90_lsb)

    for vs_cp, rot, flip_x, flip_y, suffix in use_modes:
        if suffix is None:
            continue
        m_name = variant_glyph_name(base_name, suffix)
        if m_name not in glyphs:
            match suffix:
                case "r90":
                    installed.append((vs_cp, suffix, m_name))
                    continue
                case _ if suffix in SIDEWAYS_FROM_R90 and r90_name in glyphs:
                    rel_rot, rel_fx, rel_fy = SIDEWAYS_FROM_R90[suffix]
                    parent = r90_name
                    m_glyph, m_adv, m_lsb = _composite_from(
                        parent,
                        glyphs[parent],
                        metrics[parent][0],
                        metrics[parent][1],
                        rot90_quarters=rel_rot,
                        flip_x=rel_fx,
                        flip_y=rel_fy,
                    )
                case _:
                    m_glyph, m_adv, m_lsb = _composite_from(
                        base_name,
                        glyphs[base_name],
                        metrics[base_name][0],
                        metrics[base_name][1],
                        rot90_quarters=rot,
                        flip_x=flip_x,
                        flip_y=flip_y,
                    )
            # Floor-pin after flips; cell-fill composites stay locked to the square.
            if anchor == "floor":
                m_glyph, m_adv, m_lsb = _place(m_glyph, m_adv)
            else:
                try:
                    m_glyph.recalcBounds(glyphs)
                    m_lsb = int(m_glyph.xMin)
                except Exception:
                    pass
            _install(m_name, m_glyph, m_adv, m_lsb)
        installed.append((vs_cp, suffix, m_name))
    return installed


def rebuild_sideways_from_r90(
    base_name: str,
    *,
    target_upem: int,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    pivot: Optional[Tuple[float, float]] = None,
    modes: Optional[Sequence[TransformMode]] = None,
) -> None:
    """Re-bake r270 / r90mx / r90my from the current `.r90` outline.

    `add_d4_variant_glyphs` bakes those from r90 (`allow_2x2=False`), so
    replacing `.r90` later does not update them. Call this after any r90
    rewrite (halfwidth Y-squeeze-then-rotate).
    """
    r90_name = variant_glyph_name(base_name, "r90")
    if r90_name not in glyphs or r90_name not in metrics:
        return
    parent_glyph = glyphs[r90_name]
    parent_adv, parent_lsb = int(metrics[r90_name][0]), int(metrics[r90_name][1])
    cell_mid = pivot if pivot is not None else ideographic_center(target_upem)
    use_modes = modes if modes is not None else TRANSFORM_MODES
    wanted = {
        suffix for _vs, _r, _fx, _fy, suffix in use_modes if suffix in SIDEWAYS_FROM_R90
    }
    for suffix in wanted:
        m_name = variant_glyph_name(base_name, suffix)
        if m_name not in glyphs:
            continue
        rel_rot, rel_fx, rel_fy = SIDEWAYS_FROM_R90[suffix]
        m_glyph, m_adv, m_lsb = make_composite_variant(
            r90_name,
            target_upem,
            rot90_quarters=rel_rot,
            flip_x=rel_fx,
            flip_y=rel_fy,
            advance=parent_adv,
            lsb=parent_lsb,
            base_glyph=parent_glyph,
            glyph_set=glyphs,
            center=cell_mid,
            allow_2x2=False,
        )
        glyphs[m_name] = m_glyph
        metrics[m_name] = (int(m_adv), int(m_lsb))


def vs_glyph_name(vs_cp: int) -> str:
    match vs_cp:
        case _ if vs_cp == OV_SELECTOR_CP:
            return OV_SELECTOR_NAME
        case _ if VS_BASE <= vs_cp <= VS_LAST:
            return f"vs{vs_cp - VS_BASE + 1:02d}"
        case _:
            raise ValueError(f"not a Yi VS/overlay codepoint: U+{vs_cp:04X}")


def stack_glyph_name() -> str:
    return OV_SELECTOR_NAME


def uvs_selector_for_mode(mode_index: int) -> Optional[int]:
    """FE01–FE07 for D4 mode 1–7. Identity (mode 0) has no FE* selector."""
    if mode_index <= 0:
        return None
    return 0xFE00 + mode_index


def build_d4_uvs_entries(
    base_cp: int,
    base_glyph: str,
    *,
    glyphs: Dict[str, TTGlyph],
    modes: Optional[Sequence[TransformMode]] = None,
) -> List[Tuple[int, int, Optional[str]]]:
    """`(base_cp, U+FE0n, variantName)` rows for `setupCharacterMap(uvs=...)`.

    Identity (default glyph) is omitted: cmap format 14 default UVS ranges use a
    uint8 length, so >256 consecutive bases (e.g. full Yi) overflow on compile.
    Non-default mappings are stored one-per-record and have no such limit.
    """
    rows: List[Tuple[int, int, Optional[str]]] = []
    for mode_i, (_vs_cp, _r, _fx, _fy, suffix) in enumerate(
        modes if modes is not None else TRANSFORM_MODES
    ):
        if suffix is None:
            continue
        vname = variant_glyph_name(base_glyph, suffix)
        sel = uvs_selector_for_mode(mode_i)
        if vname in glyphs and sel is not None:
            rows.append((base_cp, sel, vname))
    return rows


def variant_glyph_name(base_name: str, suffix: str) -> str:
    return f"{base_name}.{suffix}"


def overlay_glyph_name(base_name: str) -> str:
    """Zero-advance form of `base_name` for FE00 superposition."""
    return f"{base_name}.ov"


def slice_form_name(base_name: str, suffix: str) -> str:
    return f"{base_name}.{suffix}"


def orientation_form_names(
    base_name: str,
    *,
    modes: Optional[Sequence[TransformMode]] = None,
) -> List[str]:
    """Identity + non-identity D4 variant names for `base_name`."""
    names = [base_name]
    for _vs, _r, _fx, _fy, suffix in modes if modes is not None else TRANSFORM_MODES:
        if suffix is not None:
            names.append(variant_glyph_name(base_name, suffix))
    return names


def inject_stack_mark(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    *,
    pua: bool = False,
) -> str:
    """Ensure FE00 overlay mark exists (zero-width) and is cmap'd.

    `pua=True` also maps BMP `U+E008` (legacy); leave off so kana owns PUA.
    """
    sname = stack_glyph_name()
    if sname not in glyphs:
        glyph_order.append(sname)
        glyphs[sname] = empty_glyph()
        metrics[sname] = (0, 0)
    cmap[OV_SELECTOR_CP] = sname
    if pua:
        cmap[OV_PUA_CP] = sname
    return sname


def inject_slice_selectors(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    *,
    pua: bool = False,
    slots: Sequence[SliceSlot] = SLICE_VS_SLOTS,
) -> List[str]:
    """Cmap FE00 overlay + FE08–FE0F slice VS (optional legacy BMP PUA mirrors)."""
    names = [inject_stack_mark(glyph_order, glyphs, metrics, cmap, pua=pua)]
    for cp, gname, _suf in slots:
        if gname not in glyphs:
            glyph_order.append(gname)
            glyphs[gname] = empty_glyph()
            metrics[gname] = (0, 0)
        cmap[cp] = gname
        names.append(gname)
    if pua:
        for pua_cp, gname in SLICE_PUA_SLOTS:
            if gname in glyphs:
                cmap[pua_cp] = gname
    return names


TTF_GLYPH_LIMIT = 65535


def close_component_names(keep: Set[str], glyphs: Dict[str, TTGlyph]) -> Set[str]:
    """Expand `keep` with TrueType composite component names."""
    stack = list(keep)
    out = set(keep)
    while stack:
        name = stack.pop()
        glyph = glyphs.get(name)
        if glyph is None:
            continue
        try:
            if not glyph.isComposite():
                continue
            comps = glyph.components
        except Exception:
            continue
        for comp in comps:
            child = getattr(comp, "glyphName", None)
            if child and child not in out:
                out.add(child)
                stack.append(child)
    return out


def subset_glyph_tables(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    keep: Set[str],
    *,
    copy_glyphs: bool = True,
) -> Tuple[List[str], Dict[str, TTGlyph], Dict[str, Tuple[int, int]], Dict[int, str]]:
    """Copy (or alias) a named subset of glyf / hmtx / cmap."""
    keep = close_component_names(keep, glyphs)
    order = [n for n in glyph_order if n in keep]
    for name in keep:
        if name not in order and name in glyphs:
            order.append(name)
    if copy_glyphs:
        out_glyphs = {n: copy.deepcopy(glyphs[n]) for n in order if n in glyphs}
    else:
        out_glyphs = {n: glyphs[n] for n in order if n in glyphs}
    out_metrics = {n: metrics[n] for n in order if n in metrics}
    out_cmap = {cp: name for cp, name in cmap.items() if name in out_glyphs}
    return order, out_glyphs, out_metrics, out_cmap


def slice_overlay_liga_map(
    forms: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    slots: Sequence[SliceSlot] = SLICE_VS_SLOTS,
    form_name: Optional[Callable[[str, str], str]] = None,
    include_vs01: bool = True,
) -> Dict[Tuple[str, ...], str]:
    """`base + FE00/FE08–F` → overlay and/or slice (either order).

    Longer sequences (overlay+slice) are included so the caller can sort by
    length. Optional `vs01` prefixes keep legacy `base + vs01 + FE08`
    sequences (glyph-name only; BMP PUA is not cmap'd).
    """
    name_of = form_name if form_name is not None else slice_form_name
    vs01 = vs_glyph_name(TRANSFORM_MODES[0][0]) if include_vs01 else None
    if vs01 is not None and vs01 not in glyphs:
        vs01 = None
    ov = OV_SELECTOR_NAME
    liga: Dict[Tuple[str, ...], str] = {}
    for form in forms:
        if form not in glyphs:
            continue
        form_ov = overlay_glyph_name(form)
        if form_ov in glyphs and ov in glyphs:
            liga[(form, ov)] = form_ov
            if vs01 is not None:
                liga[(form, vs01, ov)] = form_ov
        for _cp, sel_name, suf in slots:
            out = name_of(form, suf)
            if out not in glyphs or sel_name not in glyphs:
                continue
            liga[(form, sel_name)] = out
            if vs01 is not None:
                liga[(form, vs01, sel_name)] = out
            out_ov = overlay_glyph_name(out)
            if out_ov not in glyphs or ov not in glyphs:
                continue
            liga[(form, ov, sel_name)] = out_ov
            liga[(form, sel_name, ov)] = out_ov
            if vs01 is not None:
                liga[(form, vs01, ov, sel_name)] = out_ov
                liga[(form, vs01, sel_name, ov)] = out_ov
            liga[(out, ov)] = out_ov
    return liga


def add_overlay_forms(
    form_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    limit: Optional[int] = None,
) -> List[str]:
    """Create zero-advance `.ov` composites of each fullwidth `form_names`.

    Overlay glyphs inherit outlines from the matching fullwidth counterpart
    (identity, D4, or segment slice) — they are not independently baked.
    `limit` caps how many new `.ov` glyphs are added (GlyphWiki 64k budget).
    Returns the list of base form names that received an `.ov`.
    """
    added_bases: List[str] = []
    for name in form_names:
        if limit is not None and len(added_bases) >= limit:
            break
        if name not in glyphs:
            continue
        ov_name = overlay_glyph_name(name)
        if ov_name in glyphs:
            added_bases.append(name)
            continue
        _adv, lsb = metrics.get(name, (0, 0))
        ov_glyph, ov_adv, ov_lsb = make_overlay_composite(name, lsb=lsb)
        glyph_order.append(ov_name)
        glyphs[ov_name] = ov_glyph
        metrics[ov_name] = (ov_adv, ov_lsb)
        added_bases.append(name)
    return added_bases


def install_overlay_gsub(
    font,
    full_forms: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
    max_stack: int = 8,
) -> int:
    """Install `glyph + FE00 → glyph.ov` ligatures into `font` GSUB.

    `max_stack` is kept for call-site compatibility and ignored — each
    preceding glyph is zeroed independently, so `A FE00 B FE00 C` stacks
    without repeating lookups.
    """
    del max_stack, glyph_order
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    liga = slice_overlay_liga_map(
        full_forms, glyphs=glyphs, slots=(), include_vs01=False
    )
    if not liga:
        return 0
    lookup = build_chunked_ligature_subst_lookup(liga)

    if "GSUB" in font:
        gsub = font["GSUB"].table
    else:

        def _langsys() -> ot.DefaultLangSys:
            ls = ot.DefaultLangSys()
            ls.ReqFeatureIndex = 0xFFFF
            ls.FeatureCount = 0
            ls.FeatureIndex = []
            return ls

        script_tags: List[str] = []
        for line in COMPOSITION_LANGUAGE_SYSTEMS:
            parts = line.replace(";", "").split()
            if len(parts) >= 2 and parts[0] == "languagesystem":
                script_tags.append(parts[1].ljust(4)[:4])

        gsub = ot.GSUB()
        gsub.Version = 0x00010000
        gsub.ScriptList = ot.ScriptList()
        gsub.ScriptList.ScriptRecord = []
        for tag in script_tags:
            srec = ot.ScriptRecord()
            srec.ScriptTag = tag
            srec.Script = ot.Script()
            srec.Script.DefaultLangSys = _langsys()
            srec.Script.LangSysCount = 0
            srec.Script.LangSysRecord = []
            gsub.ScriptList.ScriptRecord.append(srec)
        gsub.ScriptList.ScriptCount = len(script_tags)
        gsub.FeatureList = ot.FeatureList()
        gsub.FeatureList.FeatureRecord = []
        gsub.FeatureList.FeatureCount = 0
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0
        table = newTable("GSUB")
        table.table = gsub
        font["GSUB"] = table

    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0

    li = gsub.LookupList.LookupCount
    gsub.LookupList.Lookup.append(lookup)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)

    tag_to_fr = {fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])}
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            fr = ot.FeatureRecord()
            fr.FeatureTag = tag
            fr.Feature = ot.Feature()
            fr.Feature.FeatureParams = None
            fr.Feature.LookupListIndex = []
            fr.Feature.LookupCount = 0
            gsub.FeatureList.FeatureRecord.append(fr)
            gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)
            tag_to_fr[tag] = fr
            for sr in gsub.ScriptList.ScriptRecord:
                ls = sr.Script.DefaultLangSys
                if ls is None:
                    continue
                fi = list(ls.FeatureIndex or [])
                new_i = gsub.FeatureList.FeatureCount - 1
                if new_i not in fi:
                    fi.append(new_i)
                    ls.FeatureIndex = fi
                    ls.FeatureCount = len(fi)
        idxs = list(fr.Feature.LookupListIndex or [])
        idxs.append(li)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)
    return 1


# Shared across build_yi / build_cjk / GlyphWiki.
COMPOSITION_FEATURE_TAGS: Tuple[str, ...] = ("ccmp", "rlig", "liga")
COMPOSITION_LANGUAGE_SYSTEMS: Tuple[str, ...] = (
    "languagesystem DFLT dflt;",
    "languagesystem latn dflt;",
    "languagesystem yi dflt;",
    "languagesystem hani dflt;",
)


def composition_fea(*rule_groups: Sequence[str]) -> str:
    """FEA for mandatory composition: `ccmp` + `rlig` + `liga` on common scripts.

    Each `rule_groups` entry is a sequence of already-indented `sub ...;` lines.
    Empty groups are skipped. Returns `\"\"` when there are no rules.
    """
    body_lines: List[str] = []
    for group in rule_groups:
        for line in group:
            if line is None:
                continue
            s = line if line.endswith("\n") else line
            s = s.rstrip("\n")
            if s.strip():
                body_lines.append(s if s.startswith(" ") else f"  {s.lstrip()}")
    if not body_lines:
        return ""
    body = "\n".join(body_lines)
    parts = list(COMPOSITION_LANGUAGE_SYSTEMS) + [""]
    for tag in COMPOSITION_FEATURE_TAGS:
        parts.append(f"feature {tag} {{")
        parts.append(body)
        parts.append(f"}} {tag};")
        parts.append("")
    return "\n".join(parts)


def _rot90_matrix(quarters: int) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    match quarters % 4:
        case 0:
            return ((1.0, 0.0), (0.0, 1.0))
        case 1:
            return ((0.0, 1.0), (-1.0, 0.0))
        case 2:
            return ((-1.0, 0.0), (0.0, -1.0))
        case _:
            return ((0.0, -1.0), (1.0, 0.0))


def _mul2(
    a: Tuple[Tuple[float, float], Tuple[float, float]],
    b: Tuple[Tuple[float, float], Tuple[float, float]],
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    (axx, axy), (ayx, ayy) = a
    (bxx, bxy), (byx, byy) = b
    return (
        (axx * bxx + ayx * bxy, axy * bxx + ayy * bxy),
        (axx * byx + ayx * byy, axy * byx + ayy * byy),
    )


def _apply_mat(
    m: Tuple[Tuple[float, float], Tuple[float, float]], x: float, y: float
) -> Tuple[float, float]:
    (xx, xy), (yx, yy) = m
    return xx * x + yx * y, xy * x + yy * y


def variant_matrix(
    *,
    rot90_quarters: int,
    flip_x: bool,
    flip_y: bool,
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    r = _rot90_matrix(rot90_quarters)
    sx = -1.0 if flip_y else 1.0
    sy = -1.0 if flip_x else 1.0
    s: Tuple[Tuple[float, float], Tuple[float, float]] = ((sx, 0.0), (0.0, sy))
    return _mul2(s, r)


def contour_center(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Optional[Tuple[float, float]]:
    """Axis-aligned ink bbox midpoint (contour center)."""
    try:
        if glyph.isComposite():
            if glyph_set is None:
                return None
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        return (glyph.xMin + glyph.xMax) / 2.0, (glyph.yMin + glyph.yMax) / 2.0
    except Exception:
        return None


def variant_transform(
    target_upem: int,
    *,
    rot90_quarters: int,
    flip_x: bool,
    flip_y: bool,
    center: Optional[Tuple[float, float]] = None,
) -> Transform:
    """D4 map rotating/reflecting about `center` (default: ideographic mid)."""
    if rot90_quarters % 4 == 0 and not flip_x and not flip_y:
        return Transform()
    m = variant_matrix(rot90_quarters=rot90_quarters, flip_x=flip_x, flip_y=flip_y)
    (xx, xy), (yx, yy) = m
    cx, cy = center if center is not None else ideographic_center(target_upem)
    # p' = M·(p - c) + c
    dx = cx - xx * cx - yx * cy
    dy = cy - xy * cx - yy * cy
    return Transform(xx, xy, yx, yy, dx, dy)


BoundsRect = Tuple[float, float, float, float]


def transform_aabb(rect: BoundsRect, t: Transform) -> BoundsRect:
    """Axis-aligned bounds of `rect`'s corners after `t`."""
    x0, y0, x1, y1 = rect
    xs, ys = [], []
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        px, py = t.transformPoint((x, y))
        xs.append(px)
        ys.append(py)
    return min(xs), min(ys), max(xs), max(ys)


def aabb_iou(a: BoundsRect, b: BoundsRect) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0.0:
        return 0.0
    union = (
        max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
        + max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
        - inter
    )
    return inter / union if union > 0.0 else 0.0


def _map_corner_label(lab: str, t: Transform, center: Tuple[float, float]) -> str:
    """Send a tl/tr/bl/br label through `t` about `center`."""
    cx, cy = center
    dx = -1.0 if "l" in lab else 1.0
    dy = 1.0 if lab.startswith("t") else -1.0
    x, y = t.transformPoint((cx + dx, cy + dy))
    return ("t" if y >= cy else "b") + ("l" if x < cx else "r")


def match_d4_source_suffix(
    dest_suffix: str,
    t_inv: Transform,
    *,
    windows: Dict[str, BoundsRect],
    labels: Optional[Dict[str, FrozenSet[str]]] = None,
    center: Tuple[float, float],
) -> Optional[str]:
    """Identity segment whose window/labels map to `dest_suffix` under `t_inv`."""
    if labels and dest_suffix in labels:
        src_labs = frozenset(
            _map_corner_label(lab, t_inv, center) for lab in labels[dest_suffix]
        )
        for suf, labs in labels.items():
            if labs == src_labs:
                return suf
        return None
    dest_w = windows.get(dest_suffix)
    if dest_w is None:
        return None
    want = transform_aabb(dest_w, t_inv)
    best: Optional[str] = None
    best_iou = 0.0
    for suf, win in windows.items():
        iou = aabb_iou(want, win)
        if iou > best_iou:
            best, best_iou = suf, iou
    return best if best_iou >= 0.55 else None


def propagate_d4_segments(
    identity_bases: Sequence[str],
    *,
    suffixes: Sequence[str],
    form_name: Callable[[str, str], str],
    windows: Dict[str, BoundsRect],
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
    labels: Optional[Dict[str, FrozenSet[str]]] = None,
    center: Optional[Tuple[float, float]] = None,
) -> None:
    """Fill D4 × segment glyphs from identity clips: `R(clip(g, R⁻¹(W)))`.

    Identity segments (`form_name(base, suffix)`) must already exist. Each
    oriented form is a cell-centered D4 of the matching identity segment —
    no second pathops clip of the rotated outline.
    """
    if center is None:
        center = ideographic_center(target_upem)
    for base in identity_bases:
        if base not in glyphs:
            continue
        adv, _lsb = metrics.get(base, (target_upem, 0))
        for _vs, rot, fx, fy, d4suf in TRANSFORM_MODES:
            if d4suf is None:
                continue
            oriented = variant_glyph_name(base, d4suf)
            if oriented not in glyphs:
                continue
            t = variant_transform(
                target_upem,
                rot90_quarters=rot,
                flip_x=fx,
                flip_y=fy,
                center=center,
            )
            try:
                t_inv = t.inverse()
            except Exception:
                continue
            for nsuf in suffixes:
                dest = form_name(oriented, nsuf)
                if dest in glyphs:
                    continue
                src_suf = match_d4_source_suffix(
                    nsuf,
                    t_inv,
                    windows=windows,
                    labels=labels,
                    center=center,
                )
                src = form_name(base, src_suf) if src_suf else ""
                if src and src in glyphs:
                    gm = _bake_transformed_glyph(
                        glyphs[src], t, int(adv), glyph_set=glyphs
                    )
                elif nsuf in windows:
                    piece, _, _ = make_segment_slice_glyph(
                        base,
                        advance=int(adv),
                        rect=transform_aabb(windows[nsuf], t_inv),
                        glyph_set=glyphs,
                    )
                    gm = _bake_transformed_glyph(piece, t, int(adv))
                else:
                    continue
                install_derived_glyph(
                    dest,
                    gm,
                    glyph_order=glyph_order,
                    glyphs=glyphs,
                    metrics=metrics,
                )


def make_composite_variant(
    base_name: str,
    target_upem: int,
    *,
    rot90_quarters: int = 0,
    flip_x: bool = False,
    flip_y: bool = False,
    advance: int,
    lsb: int = 0,
    base_glyph: Optional[TTGlyph] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    center: Optional[Tuple[float, float]] = None,
    allow_2x2: bool = False,
) -> GlyphMetrics:
    """D4 variant of `base_name` about the contour bounding-box center.

    Every non-identity orientation (r90 / r180 / r270 / mx / my / diagonals)
    is **baked to outlines** by default — TT composites (axis-aligned or
    `WE_HAVE_A_TWO_BY_TWO`) are mishandled by some viewers. Pass
    `allow_2x2=True` only when a true composite is required.
    """
    src = base_glyph
    if src is None and glyph_set is not None:
        src = glyph_set.get(base_name)
    pivot = center
    if pivot is None and src is not None:
        pivot = contour_center(src, glyph_set)
    t = variant_transform(
        target_upem,
        rot90_quarters=rot90_quarters,
        flip_x=flip_x,
        flip_y=flip_y,
        center=pivot,
    )
    oriented = (rot90_quarters % 4 != 0) or flip_x or flip_y
    if oriented and not allow_2x2:
        if src is None:
            raise ValueError(
                f"baked variant of {base_name!r} needs base_glyph or glyph_set"
            )
        return _bake_transformed_glyph(src, t, advance, glyph_set=glyph_set)

    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = base_name
    comp.x = otRound(t.dx)
    comp.y = otRound(t.dy)
    # Explicit MS/OT offset rule — without this, Apple-style scaled offsets
    # wreck 90°/270° placements when a 2x2 composite is used.
    comp.flags = USE_MY_METRICS | ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    if (t.xx, t.xy, t.yx, t.yy) != (1.0, 0.0, 0.0, 1.0):
        # fontTools: ((xx, xy), (yx, yy)) with x' = xx·x + yx·y + dx
        comp.transform = ((t.xx, t.xy), (t.yx, t.yy))
    g.components = [comp]
    out_lsb = lsb
    if glyph_set is not None:
        try:
            g.recalcBounds(glyph_set)
            out_lsb = int(g.xMin)
        except Exception:
            pass
    return g, advance, out_lsb


def _rect_pathops(x0: float, y0: float, x1: float, y1: float):
    import pathops

    p = pathops.Path()
    p.moveTo(x0, y0)
    p.lineTo(x1, y0)
    p.lineTo(x1, y1)
    p.lineTo(x0, y1)
    p.close()
    return p


def _ttglyph_to_pathops(glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]] = None):
    import pathops

    rec = _recording_from_glyph(glyph, glyph_set)
    sk = pathops.Path()
    rec.replay(sk.getPen())
    return sk


def _pathops_to_ttglyph(path) -> TTGlyph:
    from fontTools.ttLib.removeOverlaps import ttfGlyphFromSkPath

    if path is None or not list(path.contours):
        return empty_glyph()
    g = ttfGlyphFromSkPath(path)
    try:
        g.recalcBounds(None)
    except Exception:
        pass
    return g


def boolean_union_glyphs(
    parts: Sequence[TTGlyph],
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> TTGlyph:
    """Boolean **union** of `parts` (decomposed via `glyph_set`)."""
    import pathops

    acc = None
    for glyph in parts:
        piece = _prepare_pathops_for_slice(glyph, glyph_set)
        if acc is None:
            acc = piece
            continue
        try:
            acc = pathops.op(acc, piece, pathops.PathOp.UNION, fix_winding=True)
        except Exception:
            continue
    if acc is None:
        return empty_glyph()
    return _finalize_sliced_ttglyph(acc)


def boolean_subtract_glyphs(
    minuend: TTGlyph,
    subtrahend: TTGlyph,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    clamp_rect: Optional[Tuple[float, float, float, float]] = None,
    clamp_polygon: Optional[Sequence[Tuple[float, float]]] = None,
) -> TTGlyph:
    """Boolean **difference** `minuend − subtrahend`.

    Both operands are healed + artefact-stripped first; the result is finalized
    the same way. Optional ``clamp_rect`` / ``clamp_polygon`` re-intersects the
    difference with the intended keep region so pathops crumbs past the cut
    cannot survive (prefer a direct clip when the keep region is known).
    """
    import pathops

    a = _prepare_pathops_for_slice(minuend, glyph_set)
    b = _prepare_pathops_for_slice(subtrahend, glyph_set)
    try:
        out = pathops.op(a, b, pathops.PathOp.DIFFERENCE, fix_winding=True)
    except Exception:
        return empty_glyph()
    if clamp_rect is not None:
        x0, y0, x1, y1 = (float(v) for v in clamp_rect)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        try:
            out = pathops.op(
                out,
                _rect_pathops(x0, y0, x1, y1),
                pathops.PathOp.INTERSECTION,
                fix_winding=True,
            )
        except Exception:
            return empty_glyph()
    elif clamp_polygon is not None:
        if len(clamp_polygon) < 3:
            raise ValueError("clamp_polygon needs at least 3 points")
        try:
            out = pathops.op(
                out,
                _polygon_pathops(clamp_polygon),
                pathops.PathOp.INTERSECTION,
                fix_winding=True,
            )
        except Exception:
            return empty_glyph()
    return _finalize_sliced_ttglyph(out)


def _lsb_of(glyph: TTGlyph) -> int:
    try:
        glyph.recalcBounds(None)
        return int(glyph.xMin)
    except Exception:
        return 0


def boolean_union_named(
    names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    advance: Optional[int] = None,
) -> GlyphMetrics:
    """Union named glyphs; advance defaults to the first component's."""
    parts = [glyphs[n] for n in names if n in glyphs]
    if not parts:
        return empty_glyph(), 0, 0
    out = boolean_union_glyphs(parts, glyph_set=glyphs)
    adv = int(advance if advance is not None else metrics[names[0]][0])
    return out, adv, _lsb_of(out)


def boolean_subtract_named(
    keep: str,
    cut: str,
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    advance: Optional[int] = None,
    clamp_rect: Optional[Tuple[float, float, float, float]] = None,
    clamp_polygon: Optional[Sequence[Tuple[float, float]]] = None,
) -> GlyphMetrics:
    """`keep − cut`; advance defaults to `keep`."""
    if keep not in glyphs:
        return empty_glyph(), 0, 0
    if cut not in glyphs:
        g = glyphs[keep]
        adv, lsb = metrics.get(keep, (0, 0))
        return g, int(advance if advance is not None else adv), int(lsb)
    out = boolean_subtract_glyphs(
        glyphs[keep],
        glyphs[cut],
        glyph_set=glyphs,
        clamp_rect=clamp_rect,
        clamp_polygon=clamp_polygon,
    )
    adv = int(advance if advance is not None else metrics[keep][0])
    return out, adv, _lsb_of(out)


def metrics_for_glyph(glyph: TTGlyph, advance: int) -> GlyphMetrics:
    return glyph, int(advance), _lsb_of(glyph)


def copy_named_glyph(
    src: str,
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    advance: Optional[int] = None,
) -> GlyphMetrics:
    """Independent outline copy of `src` (pathops round-trip)."""
    if src not in glyphs:
        return empty_glyph(), 0, 0
    out = boolean_union_glyphs([glyphs[src]], glyph_set=glyphs)
    adv, _lsb = metrics.get(src, (0, 0))
    return out, int(advance if advance is not None else adv), _lsb_of(out)


def install_derived_glyph(
    name: str,
    gm: GlyphMetrics,
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    advance: Optional[int] = None,
) -> None:
    """Append `name` unless it already exists."""
    if name in glyphs:
        return
    g, a, l = gm
    glyph_order.append(name)
    glyphs[name] = g
    metrics[name] = (int(advance if advance is not None else a), int(l))


HALF_PLANE_INF_FRAC = 8.0


def half_plane_rect(
    cut: float,
    *,
    axis: str,
    keep: str,
    inf: float,
) -> Tuple[float, float, float, float]:
    """Unbounded half-plane on `axis`: `keep` `'lo'` or `'hi'` of `cut`."""
    if keep not in ("lo", "hi"):
        raise ValueError(f"keep must be 'lo' or 'hi', got {keep!r}")
    if axis == "y":
        if keep == "hi":
            return -inf, cut, inf, inf
        return -inf, -inf, inf, cut
    if axis != "x":
        raise ValueError(f"axis must be 'x' or 'y', got {axis!r}")
    if keep == "hi":
        return cut, -inf, inf, inf
    return -inf, -inf, cut, inf


def _pathops_strip_artefacts(path, *, upem: int = DEFAULT_UPEM):
    """Drop specks, empty loops, hairlines, and runaway spike contours.

    Stem offset / pathops clip leave needle contours and crumbs that render as
    floating dots and slivers. Legitimate CJK/Yi strokes stay inside ~1em and
    have real thickness; artefacts are near-zero area, paper-thin, or shoot
    far outside the cell.
    """
    import pathops

    pad = float(upem) * 2.0
    min_area = max(16.0, float(upem) * 0.02)
    min_span = 2.5
    min_thickness = 3.0
    max_edge = float(upem) * 1.5

    kept = pathops.Path()
    kept_any = False
    for contour in path.contours:
        verbs = list(contour)
        pts: List[Tuple[float, float]] = []
        for _verb, vpts in verbs:
            pts.extend(vpts)
        if len(pts) < 3:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        w = x1 - x0
        h = y1 - y0
        if w < min_span and h < min_span:
            continue
        # Hairline slivers / single-axis crumbs from clip leftovers.
        if min(w, h) < min_thickness:
            continue
        if x0 < -pad or y0 < -pad or x1 > pad or y1 > pad:
            continue
        area = 0.0
        for i, (x, y) in enumerate(pts):
            x2, y2 = pts[(i + 1) % len(pts)]
            area += x * y2 - x2 * y
        if abs(area) * 0.5 < min_area:
            continue
        longest = 0.0
        for i, (x, y) in enumerate(pts):
            x2, y2 = pts[(i + 1) % len(pts)]
            dx, dy = x2 - x, y2 - y
            d2 = dx * dx + dy * dy
            if d2 > longest:
                longest = d2
        if longest**0.5 > max_edge:
            continue
        for verb, vpts in verbs:
            if verb == pathops.PathVerb.MOVE:
                kept.moveTo(*vpts[0])
            elif verb == pathops.PathVerb.LINE:
                kept.lineTo(*vpts[0])
            elif verb == pathops.PathVerb.QUAD:
                kept.quadTo(*vpts[0], *vpts[1])
            elif verb == pathops.PathVerb.CUBIC:
                kept.cubicTo(*vpts[0], *vpts[1], *vpts[2])
            elif verb == pathops.PathVerb.CLOSE:
                kept.close()
        kept_any = True
    return kept if kept_any else pathops.Path()


def _safe_simplify_pathops(path, *, upem: int = DEFAULT_UPEM):
    """``pathops.simplify`` when it does not shred the outline; else ``path``.

    Self-intersecting offset ribbons explode under simplify — detect that via
    contour-count / bounds heuristics and keep the unsimplified path.
    """
    import pathops

    raw = list(path.contours)
    if not raw:
        return path
    try:
        simp = pathops.simplify(path, fix_winding=True, clockwise=True)
    except Exception:
        return path
    simp_c = list(simp.contours)
    if not simp_c:
        return path
    if len(simp_c) > len(raw) + 3:
        return path
    try:
        rb = path.bounds
        sb = simp.bounds
        raw_area = max(0.0, (rb[2] - rb[0]) * (rb[3] - rb[1]))
        simp_area = max(0.0, (sb[2] - sb[0]) * (sb[3] - sb[1]))
        if raw_area > 1.0 and simp_area < raw_area * 0.45:
            return path
    except Exception:
        pass
    return _pathops_strip_artefacts(simp, upem=upem)


def _contour_ranges(glyph: TTGlyph) -> List[Tuple[int, int]]:
    """Inclusive ``(start, end)`` index pairs per glyf contour."""
    if glyph.numberOfContours <= 0:
        return []
    ends = list(glyph.endPtsOfContours)
    starts = [0] + [e + 1 for e in ends[:-1]]
    return list(zip(starts, ends))


def _contour_signed_area(
    coords,
    start: int,
    end: int,
) -> float:
    """Shoelace signed area (Y-up); CW outer is negative in TrueType space."""
    pts = [coords[i] for i in range(start, end + 1)]
    if len(pts) < 3:
        return 0.0
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[(i + 1) % n][0]), float(pts[(i + 1) % n][1])
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _contour_representative(
    coords,
    flags,
    start: int,
    end: int,
) -> Tuple[float, float]:
    """On-curve centroid, else bbox center."""
    xs: List[float] = []
    ys: List[float] = []
    for i in range(start, end + 1):
        if (flags[i] & 0x01) != 0:
            xs.append(float(coords[i][0]))
            ys.append(float(coords[i][1]))
    if xs:
        return sum(xs) / len(xs), sum(ys) / len(ys)
    pts = [coords[i] for i in range(start, end + 1)]
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return (min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5


def _point_in_closed_polygon(x: float, y: float, pts: Sequence[Tuple[float, float]]) -> bool:
    """Ray casting; ``pts`` is a closed ring (first point need not repeat)."""
    if len(pts) < 3:
        return False
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-30) + x1
        ):
            inside = not inside
    return inside


def _reverse_ttglyph_contours(glyph: TTGlyph, indices: Set[int]) -> TTGlyph:
    """Reverse selected contour(s) in-place (points + flags)."""
    if not indices:
        return glyph
    try:
        coords = glyph.coordinates.copy()
        flags = list(glyph.flags)
    except Exception:
        return glyph
    for ci, (start, end) in enumerate(_contour_ranges(glyph)):
        if ci not in indices:
            continue
        idx = list(range(start, end + 1))
        rev = list(reversed(idx))
        new_coords = [coords[i] for i in rev]
        new_flags = [flags[i] for i in rev]
        for k, i in enumerate(idx):
            coords[i] = new_coords[k]
            flags[i] = new_flags[k]
    glyph.coordinates = coords
    glyph.flags = flags
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    return glyph


def fix_ttglyph_hole_winding(glyph: TTGlyph) -> TTGlyph:
    """Fix TrueType fill after slice/boolean cuts.

    Non-zero glyf fill expects outer contours clockwise and holes counter-
    clockwise (Y-up shoelace: outer area < 0, holes > 0). pathops clips
    often leave inner loops with the same winding as the outer boundary,
    which fills holes solid. Nested islands alternate by containment depth.
    """
    if glyph.isComposite() or glyph.numberOfContours <= 1:
        return glyph
    try:
        coords = glyph.coordinates
        flags = list(glyph.flags)
    except Exception:
        return glyph
    ranges = _contour_ranges(glyph)
    if len(ranges) <= 1:
        return glyph

    areas: List[float] = []
    reps: List[Tuple[float, float]] = []
    polys: List[List[Tuple[float, float]]] = []
    for start, end in ranges:
        areas.append(_contour_signed_area(coords, start, end))
        reps.append(_contour_representative(coords, flags, start, end))
        polys.append(
            [
                (float(coords[i][0]), float(coords[i][1]))
                for i in range(start, end + 1)
            ]
        )

    outer_i = max(range(len(areas)), key=lambda i: abs(areas[i]))
    outer_area = areas[outer_i]
    if outer_area == 0:
        return glyph
    outer_sign = 1 if outer_area > 0 else -1

    order = sorted(range(len(areas)), key=lambda i: abs(areas[i]), reverse=True)
    depths: Dict[int, int] = {}
    for rank, i in enumerate(order):
        depth = 0
        px, py = reps[i]
        for j in order[:rank]:
            if _point_in_closed_polygon(px, py, polys[j]):
                depth += 1
        depths[i] = depth

    reverse: Set[int] = set()
    for i, area in enumerate(areas):
        if area == 0:
            continue
        want = outer_sign if depths[i] % 2 == 0 else -outer_sign
        have = 1 if area > 0 else -1
        if have != want:
            reverse.add(i)

    return _reverse_ttglyph_contours(glyph, reverse)


def _finalize_sliced_ttglyph(
    path,
    *,
    upem: int = DEFAULT_UPEM,
) -> TTGlyph:
    """Post-boolean/clip finalize: strip → heal joins → safe simplify → strip."""
    cleaned = _pathops_strip_artefacts(path, upem=upem)
    glyph = fix_ttglyph_hole_winding(_pathops_to_ttglyph(cleaned))
    glyph = cleanup_ttglyph_contours(glyph, upem=upem)
    # Second pass through pathops so snap/spike fixes don't leave crumbs.
    sk = _ttglyph_to_pathops(glyph)
    sk = _safe_simplify_pathops(sk, upem=upem)
    sk = _pathops_strip_artefacts(sk, upem=upem)
    glyph = fix_ttglyph_hole_winding(_pathops_to_ttglyph(sk))
    return cleanup_ttglyph_contours(glyph, upem=upem)


def heal_sliced_glyph(
    glyph: TTGlyph,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    upem: int = DEFAULT_UPEM,
) -> TTGlyph:
    """Heal geometry around a slice cut (before + after pipeline).

    Before: decompose → spike/snap → strip crumbs → safe winding simplify
    After:  strip → heal joins → safe simplify → strip → final snap
    """
    if glyph is None:
        return empty_glyph()
    try:
        if glyph.numberOfContours == 0 and not glyph.isComposite():
            return glyph
    except Exception:
        pass
    sk = _prepare_pathops_for_slice(glyph, glyph_set, upem=upem)
    return _finalize_sliced_ttglyph(sk, upem=upem)


def finalize_slice_metrics(
    gm: GlyphMetrics,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    upem: int = DEFAULT_UPEM,
) -> GlyphMetrics:
    """Run :func:`heal_sliced_glyph` on a ``(glyph, advance, lsb)`` triple."""
    g, adv, _lsb = gm
    out = heal_sliced_glyph(g, glyph_set=glyph_set, upem=upem)
    return out, int(adv), _lsb_of(out)


def _prepare_pathops_for_slice(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    *,
    upem: int = DEFAULT_UPEM,
):
    """Pre-slice: decompose, heal broken joins, strip artefacts, fix winding."""
    sk = _ttglyph_to_pathops(glyph, glyph_set)
    tmp = _pathops_to_ttglyph(sk)
    tmp = cleanup_ttglyph_contours(tmp, upem=upem)
    sk = _ttglyph_to_pathops(tmp)
    sk = _pathops_strip_artefacts(sk, upem=upem)
    return _safe_simplify_pathops(sk, upem=upem)


def clip_glyph_to_rect(
    glyph: TTGlyph,
    rect: Tuple[float, float, float, float],
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> TTGlyph:
    """Intersect `glyph` with axis-aligned `(x0, y0, x1, y1)` — no scale.

    Used for CJK half / third / quarter segments and combining slices: ink
    outside the band is dropped; ink inside keeps its original size and place.
    Complementary bands should also be clipped to their keep region — do not
    derive them with `boolean_subtract` alone (pathops difference leaves
    cut-line spikes). Geometry is healed and artefacts stripped before and after.
    """
    import pathops

    x0, y0, x1, y1 = (float(v) for v in rect)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    src = _prepare_pathops_for_slice(glyph, glyph_set)
    clip = _rect_pathops(x0, y0, x1, y1)
    try:
        out = pathops.op(src, clip, pathops.PathOp.INTERSECTION, fix_winding=True)
    except Exception:
        return empty_glyph()
    return _finalize_sliced_ttglyph(out)


def _polygon_pathops(points: Sequence[Tuple[float, float]]):
    import pathops

    p = pathops.Path()
    x0, y0 = points[0]
    p.moveTo(float(x0), float(y0))
    for x, y in points[1:]:
        p.lineTo(float(x), float(y))
    p.close()
    return p


def clip_glyph_to_polygon(
    glyph: TTGlyph,
    points: Sequence[Tuple[float, float]],
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> TTGlyph:
    """Intersect `glyph` with a closed polygon (no scale)."""
    import pathops

    if len(points) < 3:
        raise ValueError("polygon clip needs at least 3 points")
    src = _prepare_pathops_for_slice(glyph, glyph_set)
    clip = _polygon_pathops(points)
    try:
        out = pathops.op(src, clip, pathops.PathOp.INTERSECTION, fix_winding=True)
    except Exception:
        return empty_glyph()
    return _finalize_sliced_ttglyph(out)


def triangle_clip_points(
    kind: str,
    *,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    inf: float,
) -> Tuple[Tuple[float, float], ...]:
    """Huge triangle covering one diagonal half-plane of the cell.

    `tl`/`br` share the anti-diagonal (TR–BL); `tr`/`bl` share the
    main diagonal (TL–BR). `inf` extends the clip past cell-overflow ink.
    """
    match kind:
        case "tl":
            return (
                (x0 - inf, y1 + inf),
                (x1 + inf, y1 + inf),
                (x0 - inf, y0 - inf),
            )
        case "br":
            return (
                (x1 + inf, y0 - inf),
                (x1 + inf, y1 + inf),
                (x0 - inf, y0 - inf),
            )
        case "tr":
            return (
                (x0 - inf, y1 + inf),
                (x1 + inf, y1 + inf),
                (x1 + inf, y0 - inf),
            )
        case "bl":
            return (
                (x0 - inf, y1 + inf),
                (x0 - inf, y0 - inf),
                (x1 + inf, y0 - inf),
            )
        case _:
            raise ValueError(f"unknown triangle {kind!r}")


def make_segment_slice_glyph(
    base_name: str,
    *,
    advance: int,
    rect: Tuple[float, float, float, float],
    glyph_set: Dict[str, TTGlyph],
) -> GlyphMetrics:
    """Bake `base_name` clipped to `rect` (slice segment; full advance)."""
    if base_name not in glyph_set:
        raise KeyError(f"segment slice needs base glyph {base_name!r}")
    clipped = clip_glyph_to_rect(glyph_set[base_name], rect, glyph_set=glyph_set)
    try:
        clipped.recalcBounds(None)
        lsb = int(clipped.xMin)
    except Exception:
        lsb = 0
    return clipped, int(advance), lsb


def _recording_from_glyph(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> RecordingPen:
    """Expand `glyph` (including shallow composites) to a recording."""
    rec = RecordingPen()
    if not glyph.isComposite():
        glyph.draw(rec, None)
        return rec
    if glyph_set is None:
        raise ValueError("composite glyph needs glyph_set to bake transforms")
    for comp in glyph.components:
        name, (xx, xy, yx, yy, dx, dy) = comp.getComponentInfo()
        child = glyph_set[name]
        child_rec = _recording_from_glyph(child, glyph_set)
        child_rec.replay(TransformPen(rec, Transform(xx, xy, yx, yy, dx, dy)))
    return rec


def _bake_transformed_glyph(
    glyph: TTGlyph,
    t: Transform,
    advance: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> GlyphMetrics:
    rec = _recording_from_glyph(glyph, glyph_set)
    det = t.xx * t.yy - t.xy * t.yx
    out = apply_transform(rec, t, reverse_winding=det < 0)
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    return out, advance, lsb


def make_composite_pair(
    left_name: str,
    right_name: str,
    target_upem: int = DEFAULT_UPEM,
    *,
    lsb: int = 0,
) -> GlyphMetrics:
    """Two-component TT composite: left half-cell + right half-cell (shift +½em)."""
    half = target_upem // 2
    g = TTGlyph()
    g.numberOfContours = -1
    left = GlyphComponent()
    left.glyphName = left_name
    left.x = 0
    left.y = 0
    left.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    right = GlyphComponent()
    right.glyphName = right_name
    right.x = half
    right.y = 0
    right.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [left, right]
    return g, target_upem, lsb


def make_right_half_composite(
    left_name: str,
    target_upem: int = DEFAULT_UPEM,
    *,
    lsb: int = 0,
) -> GlyphMetrics:
    """Zero-width right-slot composite: `left_name` shifted +½em (digraph overlay)."""
    half = target_upem // 2
    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = left_name
    comp.x = half
    comp.y = 0
    comp.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [comp]
    return g, 0, lsb


def make_overlay_composite(
    base_name: str,
    *,
    lsb: int = 0,
) -> GlyphMetrics:
    """Zero-advance composite of `base_name` (inherits the fullwidth outline)."""
    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = base_name
    comp.x = 0
    comp.y = 0
    comp.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [comp]
    return g, 0, lsb


def _cp_hex(cp: int) -> str:
    return f"{cp:04X}" if cp <= 0xFFFF else f"{cp:05X}"


def halfcell_left_name(cp: int) -> str:
    return f"yihL{_cp_hex(cp)}"


def halfcell_right_name(cp: int) -> str:
    return f"yihR{_cp_hex(cp)}"


def halfcell_glyph_name(cp: int) -> str:
    """Alias for the left-slot half-cell name."""
    return halfcell_left_name(cp)


def center_glyph_in_cell(
    glyph: TTGlyph,
    target_upem: int,
    *,
    center: Optional[Tuple[float, float]] = None,
) -> TTGlyph:
    """Translate `glyph` so its bbox center sits at the CJK typo midpoint."""
    try:
        glyph.recalcBounds(None)
        x_min, y_min, x_max, y_max = glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax
    except Exception:
        return glyph
    cx, cy = center if center is not None else ideographic_center(target_upem)
    sx = cx - (x_min + x_max) / 2.0
    sy = cy - (y_min + y_max) / 2.0
    if abs(sx) < 1e-6 and abs(sy) < 1e-6:
        return glyph
    rec = RecordingPen()
    glyph.draw(rec, None)
    return apply_transform(rec, Transform(1, 0, 0, 1, sx, sy))


def center_glyph_ink_x(
    glyph: TTGlyph,
    target_upem: int,
    *,
    center_x: Optional[float] = None,
) -> TTGlyph:
    """Translate so ink bbox center X matches the ideographic midline."""
    try:
        glyph.recalcBounds(None)
        x_min, x_max = float(glyph.xMin), float(glyph.xMax)
    except Exception:
        return glyph
    cx = float(center_x if center_x is not None else ideographic_center(target_upem)[0])
    dx = cx - (x_min + x_max) / 2.0
    if abs(dx) < 0.5:
        return glyph
    rec = RecordingPen()
    glyph.draw(rec, None)
    return apply_transform(rec, Transform(1, 0, 0, 1, dx, 0))


def center_glyph_ink_in_cell(
    glyph: TTGlyph,
    target_upem: int,
    *,
    pad: float = STANDALONE_VERT_PAD,
) -> TTGlyph:
    """Center ink bbox in the padded ideographic cell (X midline + typo inset Y)."""
    bottom, top, _ = cjk_padded_floor(target_upem, pad=pad)
    cx, _ = ideographic_center(target_upem)
    cy = (bottom + top) / 2.0
    return center_glyph_in_cell(glyph, target_upem, center=(cx, cy))


@dataclass(frozen=True)
class YiInventory:
    source_path: str
    src_cps: Tuple[int, ...]
    glyph_names: Dict[int, str]
    # Shared monospace advance, typographic Y center, and tallest ink height.
    source_advance: int
    source_center_y: float
    source_max_height: float

    @property
    def count(self) -> int:
        return len(self.src_cps)

    def font_id(self, index: int) -> str:
        """Filename / family stem = standalone code point hex (e.g. A000)."""
        return f"{self.src_cps[index]:X}"


def is_yi_cp(cp: int) -> bool:
    return (YI_SYLLABLES[0] <= cp <= YI_SYLLABLES[1]) or (
        YI_RADICALS[0] <= cp <= YI_RADICALS[1]
    )


def font_cmap(tt: TTFont) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    return cmap


def source_layout_metrics(tt: TTFont, sample_glyph: str) -> Tuple[int, float]:
    """Monospace advance + typographic Y center from the source face."""
    advance = int(tt["hmtx"][sample_glyph][0])
    os2 = tt["OS/2"]
    center_y = (os2.sTypoAscender + os2.sTypoDescender) / 2.0
    return advance, center_y


def inventory_max_ink_height(tt: TTFont, glyph_names: Sequence[str]) -> float:
    """Tallest outline height among `glyph_names` (design units)."""
    max_h = 0.0
    for gname in glyph_names:
        rec = record_glyph(tt, gname)
        if rec is None:
            continue
        bounds = recording_bounds(rec)
        if bounds is None:
            continue
        _x0, y0, _x1, y1 = bounds
        max_h = max(max_h, y1 - y0)
    return max_h


def load_inventory(source_path: str) -> YiInventory:
    tt = TTFont(source_path, fontNumber=0)
    try:
        cmap = font_cmap(tt)
        ordered: List[int] = []
        names: Dict[int, str] = {}
        for start, end in (YI_SYLLABLES, YI_RADICALS):
            for cp in range(start, end + 1):
                gname = cmap.get(cp)
                if gname is None:
                    continue
                glyf = tt["glyf"]
                if gname not in glyf:
                    continue
                g = glyf[gname]
                if not g.isComposite() and getattr(g, "numberOfContours", 0) <= 0:
                    continue
                ordered.append(cp)
                names[cp] = gname
        if not ordered:
            raise ValueError(f"No Yi glyphs found in {source_path}")
        adv, cy = source_layout_metrics(tt, names[ordered[0]])
        max_h = inventory_max_ink_height(tt, [names[cp] for cp in ordered])
        if max_h <= 0:
            raise ValueError(f"No ink bounds in {source_path}")
        return YiInventory(
            source_path=source_path,
            src_cps=tuple(ordered),
            glyph_names=names,
            source_advance=adv,
            source_center_y=cy,
            source_max_height=max_h,
        )
    finally:
        tt.close()


def record_glyph(tt: TTFont, glyph_name: str) -> Optional[RecordingPen]:
    glyf = tt["glyf"]
    if glyph_name not in glyf:
        return None
    g = glyf[glyph_name]
    if not g.isComposite() and getattr(g, "numberOfContours", 0) <= 0:
        return None
    glyph_set = tt.getGlyphSet()
    rec = DecomposingRecordingPen(glyph_set)
    try:
        glyph_set[glyph_name].draw(rec)
    except Exception:
        return None
    return rec


def recording_bounds(rec: RecordingPen) -> Optional[Bounds]:
    bpen = BoundsPen(None)
    try:
        rec.replay(bpen)
    except Exception:
        return None
    return bpen.bounds


def apply_transform(
    rec: RecordingPen,
    transform: Transform,
    *,
    reverse_winding: bool = False,
) -> TTGlyph:
    pen = TTGlyphPen(None)
    dest = ReverseContourPen(pen) if reverse_winding else pen
    rec.replay(TransformPen(dest, transform))
    glyph = pen.glyph()
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    return glyph


def _uniform_place(
    rec: RecordingPen,
    *,
    scale_x: float,
    scale_y: float,
    src_cx: float,
    src_cy: float,
    dst_cx: float,
    dst_cy: float,
) -> Optional[TTGlyph]:
    """Axis scales `(sx, sy)`, mapping `(src_cx, src_cy)` → destination center."""
    if scale_x <= 0 or scale_y <= 0:
        return None
    t = Transform(
        scale_x,
        0,
        0,
        scale_y,
        dst_cx - scale_x * src_cx,
        dst_cy - scale_y * src_cy,
    )
    glyph = apply_transform(rec, t)
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    return glyph


def cjk_padded_floor(
    target_upem: int, *, pad: float = STANDALONE_VERT_PAD
) -> Tuple[float, float, float]:
    """Padded CJK cell `(floor, ceiling, height)` for Yi ink placement."""
    typo_bottom, typo_top, _ = ideographic_bounds(target_upem)
    inset = target_upem * max(pad, 0.0)
    bottom = typo_bottom + inset
    top = typo_top - inset
    cell_h = top - bottom
    if cell_h <= 1e-6:
        return typo_bottom, typo_top, typo_top - typo_bottom
    return bottom, top, cell_h


def average_ideo_ink(
    target_upem: int,
    *,
    pad: float = STANDALONE_VERT_PAD,
) -> float:
    """Target square ink width/height for CJK harmony sizing.

    Defaults to the padded ideographic cell (`STANDALONE_VERT_PAD` = 5% →
    900 @ 1000 UPM). CJK build passes a tighter `pad` (2% → 960) so harmony
    targets sit above the old median (~874) without over-squishing.
    """
    inset = float(target_upem) * max(pad, 0.0)
    cell_w = float(target_upem) - 2.0 * inset
    _bottom, _top, cell_h = cjk_padded_floor(target_upem, pad=pad)
    return min(cell_w, cell_h)


def ngulim_largest_touch_params(
    tt: TTFont,
    target_upem: int,
    *,
    pad: float = 0.0,
    pre_scale: float = 1.0,
    target_avg_em: float = 920.0,
) -> Tuple[float, float, float]:
    """Ngulim sizing params from a fast glyf-bounds scan.

    Finds the largest bbox-area outline (UPM × ``pre_scale``), returns:

    * ``grow_scale`` — uniform factor so that glyph kisses the ideographic
      cell (``pad=0`` → full em edge).
    * ``mean_area`` — mean bbox area **after** that grow (``grow² × mean``).
    * ``post_scale`` — further uniform scale so mean ``max(w, h)`` after
      grow + mean-area cap lands near ``target_avg_em``.

    Uses stored glyf ``xMin``/``xMax``/… for simple glyphs; BoundsPen only for
    composites. Avoids redrawing every cmap glyph.
    """
    try:
        source_upem = float(tt["head"].unitsPerEm)
    except Exception:
        return 1.0, 0.0, 1.0
    if source_upem <= 0:
        return 1.0, 0.0, 1.0
    upem_scale = float(target_upem) / source_upem
    geom = max(float(pre_scale), 1e-9)
    inset = float(target_upem) * max(pad, 0.0)
    cell_w = max(float(target_upem) - 2.0 * inset, 1.0)
    _bottom, _top, cell_h = cjk_padded_floor(target_upem, pad=pad)
    cell_h = max(float(cell_h), 1.0)

    names: Set[str] = set()
    try:
        for table in tt["cmap"].tables:
            if table.isUnicode():
                names.update(table.cmap.values())
    except Exception:
        return 1.0, 0.0, 1.0

    glyph_set = tt.getGlyphSet()
    glyf = tt["glyf"] if "glyf" in tt else None
    sizes: List[Tuple[float, float]] = []
    max_area = 0.0
    max_wh = (1.0, 1.0)

    for name in names:
        if name in {".notdef", ".null", "nonmarkingreturn"}:
            continue
        try:
            w = h = 0.0
            if glyf is not None and name in glyf:
                g = glyf[name]
                if g.isComposite():
                    bpen = BoundsPen(glyph_set)
                    glyph_set[name].draw(bpen)
                    if bpen.bounds is None:
                        continue
                    x0, y0, x1, y1 = bpen.bounds
                    w = (float(x1) - float(x0)) * upem_scale * geom
                    h = (float(y1) - float(y0)) * upem_scale * geom
                elif getattr(g, "numberOfContours", 0) > 0:
                    w = (float(g.xMax) - float(g.xMin)) * upem_scale * geom
                    h = (float(g.yMax) - float(g.yMin)) * upem_scale * geom
                else:
                    continue
            else:
                bpen = BoundsPen(glyph_set)
                glyph_set[name].draw(bpen)
                if bpen.bounds is None:
                    continue
                x0, y0, x1, y1 = bpen.bounds
                w = (float(x1) - float(x0)) * upem_scale * geom
                h = (float(y1) - float(y0)) * upem_scale * geom
            if w < 1.0 or h < 1.0:
                continue
            area = w * h
            sizes.append((w, h))
            if area > max_area:
                max_area = area
                max_wh = (w, h)
        except Exception:
            continue

    if not sizes:
        return 1.0, 0.0, 1.0
    wm, hm = max_wh
    grow = min(cell_w / max(wm, 1.0), cell_h / max(hm, 1.0))
    areas_grown = [(w * grow) * (h * grow) for w, h in sizes]
    mean_area = sum(areas_grown) / float(len(areas_grown))

    # Simulate mean-area cap, then measure mean max-extent.
    extents: List[float] = []
    for w, h in sizes:
        gw, gh = w * grow, h * grow
        area = gw * gh
        if mean_area > 1.0 and area > mean_area:
            s = math.sqrt(mean_area / area)
            gw *= s
            gh *= s
        extents.append(max(gw, gh))
    mean_extent = sum(extents) / float(len(extents))
    post = 1.0
    if mean_extent > 1.0 and float(target_avg_em) > 0:
        post = float(target_avg_em) / mean_extent
    return float(grow), float(mean_area), float(post)


def fit_glyph_to_ideographic_cell(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    pad: float = STANDALONE_VERT_PAD,
    grow: bool = False,
    align_y: str = "center",
) -> GlyphMetrics:
    """Proportionally fit contour ink inside the padded ideo cell.

    Uniform `s = min(cell_w / ink_w, cell_h / ink_h)` about the ink center,
    then place horizontally at the cell mid-X. Overflowing glyphs shrink;
    aspect ratio is preserved. Composites bake once.

    `align_y`::

        `"center"` — move ink mid-Y to the cell mid (kana / D4 re-fit).
        `"source"` — keep mid-Y (CJK optical seat: 日/月 stay lower than 木).

    By default `grow=False` so under-full ideographs (dots, ticks, sparse
    radicals) keep their designed stem weight — upscaling them to fill the
    cell made simple glyphs look bloated, and squish forms inherited that.
    Pass `grow=True` only when intentionally filling the cell.
    """
    bottom, top, _ = cjk_padded_floor(target_upem, pad=pad)
    inset = float(target_upem) * max(pad, 0.0)
    x0, x1 = inset, float(target_upem) - inset
    y0, y1 = bottom, top
    adv = int(advance if advance > 0 else target_upem)

    src = glyph
    try:
        if glyph.isComposite():
            src, adv, _ = _bake_transformed_glyph(
                glyph, Transform(), adv, glyph_set=glyph_set
            )
    except Exception:
        pass

    try:
        src.recalcBounds(None)
        sx0 = float(src.xMin)
        sy0 = float(src.yMin)
        sx1 = float(src.xMax)
        sy1 = float(src.yMax)
    except Exception:
        return src, int(target_upem), int(getattr(src, "xMin", 0) or 0)

    sw = max(sx1 - sx0, 1.0)
    sh = max(sy1 - sy0, 1.0)
    tw = max(x1 - x0, 1.0)
    th = max(y1 - y0, 1.0)
    s = min(tw / sw, th / sh)
    if not grow:
        s = min(s, 1.0)
    src_cx = (sx0 + sx1) / 2.0
    src_cy = (sy0 + sy1) / 2.0
    dst_cx = (x0 + x1) / 2.0
    if align_y == "source":
        dst_cy = src_cy
    else:
        dst_cy = (y0 + y1) / 2.0
    t = Transform(s, 0, 0, s, dst_cx - s * src_cx, dst_cy - s * src_cy)
    rec = _recording_from_glyph(src, None)
    out = apply_transform(rec, t)
    if abs(s - 1.0) >= 1e-3:
        out, adv, _ = compensate_stems_after_geometric_scale(out, adv, scale_x=s)

    # Source-Y shrink can still clip a low/high glyph; nudge into the cell.
    if align_y == "source":
        try:
            out.recalcBounds(None)
            oy0, oy1 = float(out.yMin), float(out.yMax)
            dy = 0.0
            if oy0 < y0:
                dy = y0 - oy0
            if oy1 + dy > y1:
                dy = y1 - oy1
            if abs(dy) > 1e-6:
                rec2 = _recording_from_glyph(out, None)
                out = apply_transform(rec2, Transform(1, 0, 0, 1, 0, dy))
        except Exception:
            pass

    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    return out, int(target_upem), lsb


def clamp_overflow_axes_to_cell(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    pad: float = STANDALONE_VERT_PAD,
    align_y: str = "source",
    uniform: bool = False,
) -> GlyphMetrics:
    """Shrink overflowing ink so it fits the padded ideo cell.

    By default `sx = min(1, cell_w / ink_w)`, `sy = min(1, cell_h / ink_h)`
    (per-axis). Pass `uniform=True` to use `min(sx, sy)` on both axes so
    sparse glyphs keep their aspect. Never grows. Then centers X; `align_y`
    matches `fit_glyph_to_ideographic_cell`. Finally pins any remaining
    overhang into the cell.
    """
    bottom, top, _ = cjk_padded_floor(target_upem, pad=pad)
    inset = float(target_upem) * max(pad, 0.0)
    x0, x1 = inset, float(target_upem) - inset
    y0, y1 = bottom, top
    adv = int(advance if advance > 0 else target_upem)

    src = glyph
    try:
        if glyph.isComposite():
            src, adv, _ = _bake_transformed_glyph(
                glyph, Transform(), adv, glyph_set=glyph_set
            )
    except Exception:
        pass

    try:
        src.recalcBounds(None)
        sx0 = float(src.xMin)
        sy0 = float(src.yMin)
        sx1 = float(src.xMax)
        sy1 = float(src.yMax)
    except Exception:
        return src, int(target_upem), int(getattr(src, "xMin", 0) or 0)

    sw = max(sx1 - sx0, 1.0)
    sh = max(sy1 - sy0, 1.0)
    tw = max(x1 - x0, 1.0)
    th = max(y1 - y0, 1.0)
    sx = min(1.0, tw / sw)
    sy = min(1.0, th / sh)
    if uniform:
        s = min(sx, sy)
        sx = sy = s
    src_cx = (sx0 + sx1) / 2.0
    src_cy = (sy0 + sy1) / 2.0
    dst_cx = (x0 + x1) / 2.0
    if align_y == "source":
        dst_cy = src_cy
    else:
        dst_cy = (y0 + y1) / 2.0
    t = Transform(sx, 0, 0, sy, dst_cx - sx * src_cx, dst_cy - sy * src_cy)
    rec = _recording_from_glyph(src, None)
    out = apply_transform(rec, t)
    scale_x, scale_y = sx, sy

    # Pin into the cell: translate first; if still oversized, uniform shrink.
    try:
        out.recalcBounds(None)
        ox0, oy0 = float(out.xMin), float(out.yMin)
        ox1, oy1 = float(out.xMax), float(out.yMax)
        dx = dy = 0.0
        if ox0 < x0:
            dx = x0 - ox0
        if ox1 + dx > x1:
            dx = x1 - ox1
        if oy0 < y0:
            dy = y0 - oy0
        if oy1 + dy > y1:
            dy = y1 - oy1
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            rec2 = _recording_from_glyph(out, None)
            out = apply_transform(rec2, Transform(1, 0, 0, 1, dx, dy))
            out.recalcBounds(None)
            ox0, oy0 = float(out.xMin), float(out.yMin)
            ox1, oy1 = float(out.xMax), float(out.yMax)
        ow = max(ox1 - ox0, 1.0)
        oh = max(oy1 - oy0, 1.0)
        if ow > tw + 1e-3 or oh > th + 1e-3:
            s = min(tw / ow, th / oh, 1.0)
            cx = (ox0 + ox1) / 2.0
            cy = (oy0 + oy1) / 2.0
            dcx = (x0 + x1) / 2.0
            dcy = (y0 + y1) / 2.0
            rec3 = _recording_from_glyph(out, None)
            out = apply_transform(
                rec3, Transform(s, 0, 0, s, dcx - s * cx, dcy - s * cy)
            )
            scale_x *= s
            scale_y *= s
    except Exception:
        pass

    if abs(scale_x - 1.0) >= 1e-3 or abs(scale_y - 1.0) >= 1e-3:
        out, adv, _ = compensate_stems_after_geometric_scale(
            out, adv, scale_x=scale_x, scale_y=scale_y
        )

    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    return out, int(target_upem), lsb


def _scale_xy_about_ink_center(glyph: TTGlyph, sx: float, sy: float) -> TTGlyph:
    """Geometric scale about ink mid; axes are independent (stems scale with size)."""
    if abs(sx - 1.0) < 1e-3 and abs(sy - 1.0) < 1e-3:
        return glyph
    try:
        glyph.recalcBounds(None)
        cx = (float(glyph.xMin) + float(glyph.xMax)) / 2.0
        cy = (float(glyph.yMin) + float(glyph.yMax)) / 2.0
    except Exception:
        return glyph
    rec = _recording_from_glyph(glyph, None)
    return apply_transform(
        rec, Transform(sx, 0, 0, sy, cx * (1.0 - sx), cy * (1.0 - sy))
    )


def _uniform_scale_about_ink_center(glyph: TTGlyph, factor: float) -> TTGlyph:
    """Isotropic geometric scale about ink mid (stems scale with size)."""
    return _scale_xy_about_ink_center(glyph, factor, factor)


def compensate_stems_after_geometric_scale(
    glyph: TTGlyph,
    advance: int,
    *,
    scale_x: float,
    scale_y: Optional[float] = None,
) -> GlyphMetrics:
    """CAPE Weight after geometric scale: thin if grown, thicken if shrunk.

    Geometric scale multiplies stem thickness by `s`. Weight `1/s` restores
    optical weight (outer box preserved by CAPE). For anisotropic scale use the
    geometric mean of the two factors.
    """
    sy = float(scale_x) if scale_y is None else float(scale_y)
    sx = float(scale_x)
    s = math.sqrt(max(sx, 1e-9) * max(sy, 1e-9))
    try:
        glyph.recalcBounds(None)
        lsb = int(glyph.xMin)
    except Exception:
        lsb = 0
    adv = int(advance if advance > 0 else 1000)
    if abs(s - 1.0) < 1e-3:
        return glyph, adv, lsb
    factor = 1.0 / s
    # Keep compensation mild on extreme fits (avoid CAPE shredding joins).
    factor = max(0.75, min(1.35, factor))
    if abs(factor - 1.0) < 1e-3:
        return glyph, adv, lsb
    try:
        out, _a, lsb = bolden_ttglyph(glyph, factor, advance=float(adv))
        return out, adv, int(lsb)
    except Exception:
        return glyph, adv, lsb


def center_glyph_ink_in_advance(
    glyph: TTGlyph,
    advance: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> GlyphMetrics:
    """Translate so ink center X sits at `advance / 2`.

    Some Ext-B / supplemental sources ship `hmtx` advance 0 with outlines
    centered on the origin (large negative `lsb`). Pan-CJK cells are full-em;
    without this step the ink stays shifted left/right in the cell.
    """
    adv = int(advance if advance > 0 else DEFAULT_UPEM)
    src = glyph
    try:
        if glyph.isComposite():
            src, adv, _ = _bake_transformed_glyph(
                glyph, Transform(), adv, glyph_set=glyph_set
            )
    except Exception:
        pass
    try:
        src.recalcBounds(None)
        x0, x1 = float(src.xMin), float(src.xMax)
    except Exception:
        return glyph, adv, int(getattr(glyph, "xMin", 0) or 0)
    dx = (adv / 2.0) - (x0 + x1) / 2.0
    if abs(dx) < 0.5:
        return src, adv, int(src.xMin)
    rec = _recording_from_glyph(src, None)
    out = apply_transform(rec, Transform(1, 0, 0, 1, dx, 0))
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    return out, adv, lsb


def grow_undersize_to_average_ideo(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    avg_width: float,
    avg_height: float,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    pad: float = STANDALONE_VERT_PAD,
    align_y: str = "source",
) -> GlyphMetrics:
    """Uniform geometric grow until width or height touches average ideograph ink.

    `s = min(avg_w / ink_w, avg_h / ink_h)`; never below 1 (no shrink here)
    and never past the padded cell. Geometric grow thickens strokes, so CAPE
    Weight `1/s` thins them back. Placement is left to the caller.
    """
    del align_y
    bottom, top, _ = cjk_padded_floor(target_upem, pad=pad)
    inset = float(target_upem) * max(pad, 0.0)
    x0, x1 = inset, float(target_upem) - inset
    adv = int(advance if advance > 0 else target_upem)

    src = glyph
    try:
        if glyph.isComposite():
            src, adv, _ = _bake_transformed_glyph(
                glyph, Transform(), adv, glyph_set=glyph_set
            )
    except Exception:
        pass

    try:
        src.recalcBounds(None)
        sx0 = float(src.xMin)
        sy0 = float(src.yMin)
        sx1 = float(src.xMax)
        sy1 = float(src.yMax)
        lsb = int(src.xMin)
    except Exception:
        return src, adv, int(getattr(src, "xMin", 0) or 0)

    sw = max(sx1 - sx0, 1.0)
    sh = max(sy1 - sy0, 1.0)
    tw = max(x1 - x0, 1.0)
    th = max(top - bottom, 1.0)
    s = min(float(avg_width) / sw, float(avg_height) / sh)
    s = min(s, tw / sw, th / sh)
    if s <= 1.0 + 1e-3:
        return src, adv, lsb

    src = _uniform_scale_about_ink_center(src, s)
    src, adv, lsb = compensate_stems_after_geometric_scale(src, adv, scale_x=s)
    return src, adv, lsb


def cap_oversize_bbox_area(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    avg_width: float,
    avg_height: float,
    area_floor: float = 0.60,
    area_ceil: float = 0.80,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> GlyphMetrics:
    """Shrink oversize ink; leave sparse glyphs unchanged.

    Mean size is `(avg_width, avg_height)`. Relative area
    `(ink_w * ink_h) / (avg_w * avg_h)`:

    * if `ink_w < area_floor × avg_w` **or** `ink_h < area_floor × avg_h`
      → unchanged (sparse on either axis — never grow/stretch)
    * if relative area `> area_ceil` → isotropic shrink to `area_ceil` × mean
    * otherwise → unchanged

    Cell overflow is left to the caller (`clamp_overflow_axes_to_cell` /
    `fit_glyph_to_ideographic_cell`).
    """
    adv = int(advance if advance > 0 else target_upem)
    src = glyph
    try:
        if glyph.isComposite():
            src, adv, _ = _bake_transformed_glyph(
                glyph, Transform(), adv, glyph_set=glyph_set
            )
    except Exception:
        pass

    try:
        src.recalcBounds(None)
        sx0 = float(src.xMin)
        sy0 = float(src.yMin)
        sx1 = float(src.xMax)
        sy1 = float(src.yMax)
        lsb = int(src.xMin)
    except Exception:
        return src, adv, int(getattr(src, "xMin", 0) or 0)

    sw = max(sx1 - sx0, 1.0)
    sh = max(sy1 - sy0, 1.0)
    avg_w = max(float(avg_width), 1.0)
    avg_h = max(float(avg_height), 1.0)
    floor = max(0.0, float(area_floor))
    ceil = max(0.0, float(area_ceil))

    # Sparse on either axis: keep designed proportions (no grow / no area shrink).
    if sw < floor * avg_w or sh < floor * avg_h:
        return src, adv, lsb

    mean_area = avg_w * avg_h
    ratio = (sw * sh) / mean_area
    if ratio <= ceil + 1e-9:
        return src, adv, lsb

    # Scale so new_area / mean_area == ceil.
    s = math.sqrt(ceil / ratio)
    if s >= 1.0 - 1e-3:
        return src, adv, lsb
    src = _uniform_scale_about_ink_center(src, s)
    src, adv, lsb = compensate_stems_after_geometric_scale(src, adv, scale_x=s)
    return src, adv, lsb


def is_sparse_ideo_axes(
    glyph: TTGlyph,
    *,
    avg_width: float,
    avg_height: float,
    sparse_frac: float,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> bool:
    """True if either ink axis is below `sparse_frac` × the mean."""
    src = glyph
    try:
        if glyph.isComposite():
            src, _, _ = _bake_transformed_glyph(
                glyph, Transform(), 0, glyph_set=glyph_set
            )
    except Exception:
        pass
    try:
        src.recalcBounds(None)
        sw = max(float(src.xMax) - float(src.xMin), 1.0)
        sh = max(float(src.yMax) - float(src.yMin), 1.0)
    except Exception:
        return False
    floor = max(0.0, float(sparse_frac))
    return sw < floor * float(avg_width) or sh < floor * float(avg_height)


def _glyph_outline_points(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> List[Tuple[float, float]]:
    """On-curve and off-curve coordinates (baked if composite)."""
    src = glyph
    try:
        if glyph.isComposite():
            src, _, _ = _bake_transformed_glyph(
                glyph, Transform(), 0, glyph_set=glyph_set
            )
        src.recalcBounds(None)
        coords = src.coordinates
        if coords is None or len(coords) == 0:
            return []
        return [(float(x), float(y)) for x, y in coords]
    except Exception:
        return []


def flat_horizontal_caps(
    glyph: TTGlyph,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    tol_frac: float = 0.035,
    min_span_frac: float = 0.20,
) -> Tuple[bool, bool]:
    """Return `(flat_top, flat_bottom)` when a horizontal edge spans the ink."""
    pts = _glyph_outline_points(glyph, glyph_set)
    if len(pts) < 4:
        return False, False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    iw = max(x1 - x0, 1.0)
    ih = max(y1 - y0, 1.0)
    tol = max(ih * tol_frac, 6.0)

    def _flat_at(y_ref: float) -> bool:
        band = [(x, y) for x, y in pts if abs(y - y_ref) <= tol]
        if len(band) < 2:
            return False
        span = max(x for x, _y in band) - min(x for x, _y in band)
        return span >= iw * min_span_frac

    return _flat_at(y1), _flat_at(y0)


def flat_vertical_sides(
    glyph: TTGlyph,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    tol_frac: float = 0.035,
    min_span_frac: float = 0.20,
) -> Tuple[bool, bool]:
    """Return `(flat_right, flat_left)` when a vertical edge spans the ink."""
    pts = _glyph_outline_points(glyph, glyph_set)
    if len(pts) < 4:
        return False, False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    iw = max(x1 - x0, 1.0)
    ih = max(y1 - y0, 1.0)
    tol = max(iw * tol_frac, 6.0)

    def _flat_at(x_ref: float) -> bool:
        band = [(x, y) for x, y in pts if abs(x - x_ref) <= tol]
        if len(band) < 2:
            return False
        span = max(y for _x, y in band) - min(y for _x, y in band)
        return span >= ih * min_span_frac

    return _flat_at(x1), _flat_at(x0)


# Ideographs that read wide even in source masters (Ngulim square-block squish).
# 冂…冋, 凵…凿, 厂…厲, 囗…圞, 門…闧, 辶…邐 — enclosure / cliff / gate / walk shapes.
SQUARE_BLOCK_CODEPOINT_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x5182, 0x518B),  # 冂 … 冋 (incl. 内, 円, 冈, 冉, 冊, …)
    (0x51F5, 0x51FF),  # 凵 … 凿 (incl. 凶, 凸, 凹, 出, 击, 函, …)
    (0x5382, 0x53B2),  # 厂 … 厲 (cliff / 厂-radical cluster)
    (0x56D7, 0x571E),  # 囗 … 圞 (enclosure / 囗-radical cluster)
    (0x9580, 0x95E7),  # 門 … 闧 (gate radical cluster)
    (0x8FB6, 0x9090),  # 辶 … 邐 (walk / 辶-radical cluster)
)


def cp_in_square_block_ranges(codepoint: Optional[int]) -> bool:
    if codepoint is None:
        return False
    for lo, hi in SQUARE_BLOCK_CODEPOINT_RANGES:
        if lo <= int(codepoint) <= hi:
            return True
    return False


def open_enclosure_frame(
    glyph: TTGlyph,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> bool:
    """Partial/open enclosure frames (冂 open bottom, 凵 open top, 内 C-channel)."""
    flat_top, flat_bottom = flat_horizontal_caps(glyph, glyph_set=glyph_set)
    flat_right, flat_left = flat_vertical_sides(glyph, glyph_set=glyph_set)
    if flat_top and (flat_left or flat_right):
        return True
    if flat_bottom and flat_left and flat_right:
        return True
    if flat_top and flat_bottom and (flat_left or flat_right):
        return True
    return False


def _baked_ink_size(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[TTGlyph, float, float]:
    """Return `(glyph, ink_width, ink_height)` with composites baked."""
    src = glyph
    try:
        if glyph.isComposite():
            src, _, _ = _bake_transformed_glyph(
                glyph, Transform(), 0, glyph_set=glyph_set
            )
    except Exception:
        pass
    try:
        src.recalcBounds(None)
        sw = max(float(src.xMax) - float(src.xMin), 1.0)
        sh = max(float(src.yMax) - float(src.yMin), 1.0)
        return src, sw, sh
    except Exception:
        return glyph, 1.0, 1.0


def square_block_ideo(
    glyph: TTGlyph,
    *,
    avg_width: float,
    avg_height: float,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    codepoint: Optional[int] = None,
    aspect_lo: float = 0.82,
    aspect_hi: float = 1.22,
    open_aspect_lo: float = 0.68,
    open_aspect_hi: float = 1.35,
    fill_frac: float = 0.88,
    open_fill_frac: float = 0.82,
    full_frac: float = 0.94,
    cp_fill_frac: float = 0.75,
) -> bool:
    """Blocky, nearly square ideographs (画, 囗, 冂, 凵, 内, 凸, …).

    These often read wide even before Ngulim area-cap. Curved sides (己/已/巳)
    still qualify when the ink bbox is square-ish and fills the mean cell.
    Open/partial enclosures (冂, 凵, 内, 凶, …), cliff radicals (厂…厲),
    full enclosures (囗…圞), gate radicals (門…闧), and walk radicals
    (辶…邐) match on frame geometry or explicit
    `SQUARE_BLOCK_CODEPOINT_RANGES`.
    """
    _src, sw, sh = _baked_ink_size(glyph, glyph_set)
    if cp_in_square_block_ranges(codepoint) and sw >= float(avg_width) * cp_fill_frac:
        return True

    open_frame = open_enclosure_frame(glyph, glyph_set=glyph_set)
    aspect = sw / max(sh, 1.0)
    if open_frame and sw >= float(avg_width) * open_fill_frac:
        if open_aspect_lo <= aspect <= open_aspect_hi:
            return True

    if not (aspect_lo <= aspect <= aspect_hi):
        return False
    if sw < float(avg_width) * fill_frac:
        return False

    flat_top, flat_bottom = flat_horizontal_caps(glyph, glyph_set=glyph_set)
    flat_right, flat_left = flat_vertical_sides(glyph, glyph_set=glyph_set)
    flat_count = sum((flat_top, flat_bottom, flat_right, flat_left))
    if flat_top and flat_bottom:
        return True
    if flat_right and flat_left:
        return True
    if flat_count >= 3:
        return True
    if open_frame:
        return True
    # Curved-outline blocks that still fill the em square.
    if sw >= float(avg_width) * full_frac and sh >= float(avg_height) * (
        full_frac - 0.06
    ):
        return True
    return False


def squish_flat_cap_ink(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    avg_width: float,
    avg_height: float,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    codepoint: Optional[int] = None,
    ink_frac: float = 0.96,
    square_width_frac: float = 0.88,
    square_height_frac: float = 0.92,
    min_oversize_frac: float = 0.998,
) -> GlyphMetrics:
    """Axis shrink when flat caps/sides leave ink wider/taller than the mean.

    Ngulim glyphs with horizontal roof/floor bars or vertical enclosure
    sides often fill the padded cell after area-cap. Pull each oversize axis
    down to the mean ink size and thicken stems to match.

    `square_block_ideo` glyphs (画, 囗, 冂, 凵, 内, 凸, …) use tighter
    `square_*_frac` targets — especially on width.
    """
    flat_top, flat_bottom = flat_horizontal_caps(glyph, glyph_set=glyph_set)
    flat_right, flat_left = flat_vertical_sides(glyph, glyph_set=glyph_set)
    square = square_block_ideo(
        glyph,
        avg_width=avg_width,
        avg_height=avg_height,
        glyph_set=glyph_set,
        codepoint=codepoint,
    )
    need_y = flat_top or flat_bottom or square
    need_x = flat_right or flat_left or square
    if not (need_x or need_y):
        adv = int(advance if advance > 0 else target_upem)
        try:
            glyph.recalcBounds(None)
            return glyph, adv, int(glyph.xMin)
        except Exception:
            return glyph, adv, 0

    adv = int(advance if advance > 0 else target_upem)
    src, sw, sh = _baked_ink_size(glyph, glyph_set)
    try:
        src.recalcBounds(None)
        cx = (float(src.xMin) + float(src.xMax)) / 2.0
        cy = (float(src.yMin) + float(src.yMax)) / 2.0
        lsb = int(src.xMin)
    except Exception:
        return glyph, adv, int(getattr(glyph, "xMin", 0) or 0)

    sx = 1.0
    sy = 1.0
    target_w = float(avg_width) * float(ink_frac)
    target_h = float(avg_height) * float(ink_frac)
    if square:
        target_w = float(avg_width) * float(square_width_frac)
        if flat_top or flat_bottom:
            target_h = float(avg_height) * float(square_height_frac)
    if need_x and sw > target_w * min_oversize_frac:
        sx = min(sx, target_w / sw)
    if need_y and sh > target_h * min_oversize_frac:
        sy = min(sy, target_h / sh)
    if sx >= 1.0 - 1e-3 and sy >= 1.0 - 1e-3:
        return src, adv, lsb

    rec = _recording_from_glyph(src, None)
    out = apply_transform(
        rec,
        Transform(sx, 0, 0, sy, cx * (1.0 - sx), cy * (1.0 - sy)),
    )
    out, adv, lsb = compensate_stems_after_geometric_scale(
        out, adv, scale_x=sx, scale_y=sy
    )
    return out, adv, lsb


def squish_flat_cap_height(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    avg_height: float,
    avg_width: Optional[float] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    min_oversize_frac: float = 1.01,
) -> GlyphMetrics:
    """Compat wrapper — prefer `squish_flat_cap_ink`."""
    w = float(avg_width if avg_width is not None else avg_height)
    return squish_flat_cap_ink(
        glyph,
        advance,
        target_upem,
        avg_width=w,
        avg_height=avg_height,
        glyph_set=glyph_set,
        min_oversize_frac=min_oversize_frac,
    )


def normalize_axes_to_average_ideo(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    avg_width: float,
    avg_height: float,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    pad: float = STANDALONE_VERT_PAD,
    sparse_frac: float = 0.75,
) -> GlyphMetrics:
    """Independent X/Y geometric scale so ink W and H match the averages.

    Stretch or squash each axis separately about the ink center
    (`sx = avg_w / ink_w`, `sy = avg_h / ink_h`). Each factor is clamped
    so the result still fits the padded ideographic cell. Stems scale with
    the axis (no CAPE). Hairline / empty axes are left alone.

    Few-stroke / intentionally sparse axes (ink below `sparse_frac` of the
    average on that axis — e.g. 一 short, 丨 narrow) are **not stretched**;
    they may still squash if they overflow the cell. Callers can follow with
    `grow_undersize_to_average_ideo` for overall-small glyphs.
    """
    bottom, top, _ = cjk_padded_floor(target_upem, pad=pad)
    inset = float(target_upem) * max(pad, 0.0)
    x0, x1 = inset, float(target_upem) - inset
    adv = int(advance if advance > 0 else target_upem)

    src = glyph
    try:
        if glyph.isComposite():
            src, adv, _ = _bake_transformed_glyph(
                glyph, Transform(), adv, glyph_set=glyph_set
            )
    except Exception:
        pass

    try:
        src.recalcBounds(None)
        sx0 = float(src.xMin)
        sy0 = float(src.yMin)
        sx1 = float(src.xMax)
        sy1 = float(src.yMax)
        lsb = int(src.xMin)
    except Exception:
        return src, adv, int(getattr(src, "xMin", 0) or 0)

    sw = sx1 - sx0
    sh = sy1 - sy0
    tw = max(x1 - x0, 1.0)
    th = max(top - bottom, 1.0)
    avg_w = float(avg_width)
    avg_h = float(avg_height)
    sparse = max(0.0, min(1.0, float(sparse_frac)))
    sx = 1.0
    sy = 1.0
    if sw > 1e-3:
        # Sparse width (sticks / radicals): squash only, never stretch.
        if sw < avg_w * sparse:
            sx = min(1.0, tw / sw)
        else:
            sx = min(avg_w / sw, tw / sw)
    if sh > 1e-3:
        if sh < avg_h * sparse:
            sy = min(1.0, th / sh)
        else:
            sy = min(avg_h / sh, th / sh)
    if abs(sx - 1.0) < 1e-3 and abs(sy - 1.0) < 1e-3:
        return src, adv, lsb

    src = _scale_xy_about_ink_center(src, sx, sy)
    try:
        src.recalcBounds(None)
        lsb = int(src.xMin)
    except Exception:
        pass
    return src, adv, lsb


def _fit_glyph_to_cjk_height(
    glyph: TTGlyph,
    target_upem: int,
    *,
    pad: float = STANDALONE_VERT_PAD,
    align: str = "floor",
) -> TTGlyph:
    """Match ink to a vertically padded CJK typo box.

    * Taller than the padded box → squash Y to that height.
    * Shorter (or equal) → `align="floor"` pins the ink bottom to the padded
      floor; `align="center"` centers ink mid-Y in the padded cell.

    `pad` is a fraction of em inset from typo ascent/descent.
    """
    if align not in ("floor", "center"):
        raise ValueError(f"align must be 'floor' or 'center', got {align!r}")
    bottom, top, cell_h = cjk_padded_floor(target_upem, pad=pad)
    cell_mid_y = (bottom + top) / 2.0
    try:
        glyph.recalcBounds(None)
        y0, y1 = float(glyph.yMin), float(glyph.yMax)
    except Exception:
        return glyph
    h = y1 - y0
    if h <= 1e-6:
        return glyph
    rec = RecordingPen()
    glyph.draw(rec, None)
    if h > cell_h + 1e-6:
        sy = cell_h / h
        ink_mid = (y0 + y1) / 2.0
        if align == "center":
            # y' = cell_mid + sy·(y - ink_mid)
            ty = cell_mid_y - sy * ink_mid
            return apply_transform(rec, Transform(1, 0, 0, sy, 0, ty))
        # floor: y' = bottom + sy·(y - y0)
        return apply_transform(rec, Transform(1, 0, 0, sy, 0, bottom - sy * y0))
    if align == "center":
        dy = cell_mid_y - (y0 + y1) / 2.0
    else:
        dy = bottom - y0
    if abs(dy) < 1e-6:
        return glyph
    return apply_transform(rec, Transform(1, 0, 0, 1, 0, dy))


def scale_glyph_in_ideographic_cell(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    scale: float = STANDALONE_CELL_SCALE,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    center: Optional[Tuple[float, float]] = None,
) -> GlyphMetrics:
    """Uniformly scale about the ideographic center (post floor-pin inset).

    Used after stretch / stem-normalize and CJK floor pin so Yi ink sits at
    ~`scale` of the cell while remaining centered in that space.
    """
    adv = int(advance if advance > 0 else target_upem)
    if scale <= 0:
        raise ValueError(f"scale must be > 0, got {scale!r}")
    if abs(scale - 1.0) < 1e-9:
        try:
            if glyph.isComposite():
                if glyph_set is None:
                    raise ValueError("scale_glyph_in_ideographic_cell needs glyph_set")
                glyph.recalcBounds(glyph_set)
            else:
                glyph.recalcBounds(None)
            return glyph, adv, int(glyph.xMin)
        except Exception:
            return glyph, adv, int(getattr(glyph, "xMin", 0) or 0)

    src = glyph
    try:
        if glyph.isComposite():
            src, adv, _ = _bake_transformed_glyph(
                glyph, Transform(), adv, glyph_set=glyph_set
            )
    except Exception:
        src = glyph

    cx, cy = center if center is not None else ideographic_center(target_upem)
    t = Transform(scale, 0, 0, scale, cx * (1.0 - scale), cy * (1.0 - scale))
    rec = _recording_from_glyph(src, None)
    out = apply_transform(rec, t)
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    return out, adv, lsb


def pin_glyph_ink_to_cjk_floor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    pad: float = STANDALONE_VERT_PAD,
) -> GlyphMetrics:
    """Translate (or squash) so ink bottom sits on the padded CJK floor.

    Upright standalones are floor-pinned; D4 rotate/reflect about the contour
    center, so flipped or sideways forms often hang below the baseline.
    Composites stay composites when only a Y translation is needed.
    """

    bottom, _top, cell_h = cjk_padded_floor(target_upem, pad=pad)
    try:
        if glyph.isComposite():
            if glyph_set is None:
                raise ValueError("pin_glyph_ink_to_cjk_floor needs glyph_set")
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        y0, y1 = float(glyph.yMin), float(glyph.yMax)
    except Exception:
        return glyph, int(advance), 0

    h = y1 - y0
    if h <= 1e-6:
        try:
            lsb = int(glyph.xMin)
        except Exception:
            lsb = 0
        return glyph, int(advance), lsb

    if h > cell_h + 1e-6:
        # Too tall for the padded cell — bake composites, then squash to floor.
        if glyph.isComposite():
            glyph, advance, _ = _bake_transformed_glyph(
                glyph, Transform(), int(advance), glyph_set=glyph_set
            )
        glyph = _fit_glyph_to_cjk_height(glyph, target_upem, pad=pad)
        try:
            glyph.recalcBounds(None)
            lsb = int(glyph.xMin)
        except Exception:
            lsb = 0
        return glyph, int(advance), lsb

    dy = bottom - y0
    if abs(dy) < 0.5:
        try:
            lsb = int(glyph.xMin)
        except Exception:
            lsb = 0
        return glyph, int(advance), lsb

    if glyph.isComposite():
        for comp in glyph.components:
            comp.y = otRound(comp.y + dy)
        try:
            glyph.recalcBounds(glyph_set)
            lsb = int(glyph.xMin)
        except Exception:
            lsb = 0
        return glyph, int(advance), lsb

    glyph = _fit_glyph_to_cjk_height(glyph, target_upem, pad=pad)
    try:
        glyph.recalcBounds(None)
        lsb = int(glyph.xMin)
    except Exception:
        lsb = 0
    return glyph, int(advance), lsb


def make_standalone_glyph(
    rec: RecordingPen,
    target_upem: int = DEFAULT_UPEM,
    *,
    source_advance: int,
    source_center_y: float,
    source_max_height: float,
    pad: float = STANDALONE_PAD,
    widen: float = STANDALONE_CONTOUR_WIDEN,
    vert_pad: float = STANDALONE_VERT_PAD,
    cell_scale: float = STANDALONE_CELL_SCALE,
    horizontal_weight: float = STANDALONE_HORIZONTAL_WEIGHT,
    stroke_weight: Optional[float] = None,  # unused; kept for call-site compat
) -> Optional[GlyphMetrics]:
    """Shared `sx` from advance, shared `sy` from inventory max ink height.

    `sx = cell / source_advance` — one factor for the whole inventory, mapping
    the monospace advance box to the ideographic em. `sy` uses the tallest ink
    height the same way. Ink bbox center is mapped to the ideographic midline; Y
    is then fitted inside the padded typo box (squash if taller, else centered).
    Horizontal stems are Weight-boldened uniformly (Y-only offset, outer box
    restored) by `horizontal_weight` (default 125%). Finally uniform
    `cell_scale` (~98%) about the ideographic center keeps the syllable
    inset and centered.
    """
    del stroke_weight
    del source_center_y  # retained for call-site compat / inventory symmetry
    if source_advance <= 0 or source_max_height <= 0:
        return None
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    _x0, y0, _x1, y1 = bounds
    x0 = float(_x0)
    x1 = float(_x1)
    cell = target_upem * (1.0 - 2.0 * pad)
    scale_x = cell / float(source_advance)
    scale_y = cell / float(source_max_height)
    dst_cx, dst_cy = ideographic_center(target_upem)
    glyph = _uniform_place(
        rec,
        scale_x=scale_x,
        scale_y=scale_y,
        src_cx=(x0 + x1) / 2.0,
        src_cy=(y0 + y1) / 2.0,
        dst_cx=dst_cx,
        dst_cy=dst_cy,
    )
    if glyph is None:
        return None
    if widen > 0:
        glyph, _adv, _lsb = widen_ttglyph(
            glyph,
            1.0 + widen,
            advance=float(target_upem),
            center_x=dst_cx,
        )
    glyph = _fit_glyph_to_cjk_height(glyph, target_upem, pad=vert_pad, align="center")
    if abs(horizontal_weight - 1.0) > 1e-9:
        try:
            glyph, _hadv, _hlsb = bolden_horizontal_ttglyph(
                glyph,
                horizontal_weight,
                advance=float(target_upem),
            )
        except Exception:
            pass
    glyph = center_glyph_ink_in_cell(glyph, target_upem, pad=vert_pad)
    return scale_glyph_in_ideographic_cell(
        glyph, target_upem, target_upem, scale=cell_scale
    )


def make_halfwidth_glyph(
    rec: RecordingPen,
    target_upem: int = DEFAULT_UPEM,
    *,
    source_advance: int,
    source_center_y: float,
    source_max_height: float,
    pad: float = HALFWIDTH_PAD,
    stroke_weight: Optional[float] = None,  # unused; kept for call-site compat
) -> Optional[GlyphMetrics]:
    """Half-em `sx`; same inventory-wide `sy` as standalones (full cell height)."""
    del stroke_weight
    del source_center_y
    if source_advance <= 0 or source_max_height <= 0:
        return None
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    _x0, y0, _x1, y1 = bounds
    adv = target_upem // 2
    cell_w = adv * (1.0 - 2.0 * pad)
    cell_h = target_upem * (1.0 - 2.0 * pad)
    scale_x = cell_w / float(source_advance)
    scale_y = cell_h / float(source_max_height)
    _, dst_cy = ideographic_center(target_upem)
    glyph = _uniform_place(
        rec,
        scale_x=scale_x,
        scale_y=scale_y,
        src_cx=source_advance / 2.0,
        src_cy=(y0 + y1) / 2.0,
        dst_cx=adv / 2.0,
        dst_cy=dst_cy,
    )
    if glyph is None:
        return None
    glyph = _fit_glyph_to_cjk_height(glyph, target_upem)
    if abs(STANDALONE_HORIZONTAL_WEIGHT - 1.0) > 1e-9:
        try:
            glyph, _hadv, _hlsb = bolden_horizontal_ttglyph(
                glyph,
                STANDALONE_HORIZONTAL_WEIGHT,
                advance=float(adv),
            )
        except Exception:
            pass
    try:
        glyph.recalcBounds(None)
        lsb = int(glyph.xMin)
    except Exception:
        lsb = 0
    return glyph, adv, lsb


def merge_halfcell_glyphs(
    left: TTGlyph,
    right: TTGlyph,
    target_upem: int = DEFAULT_UPEM,
) -> Optional[GlyphMetrics]:
    """Place two pre-fit half-cell glyphs side by side (decomposed merge)."""
    half = target_upem // 2
    pen = TTGlyphPen(None)
    ra = RecordingPen()
    left.draw(ra, None)
    ra.replay(pen)
    rb = RecordingPen()
    right.draw(rb, None)
    rb.replay(TransformPen(pen, Transform(1, 0, 0, 1, half, 0)))
    glyph = pen.glyph()
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    return glyph, target_upem, int(glyph.xMin)


def make_compound_glyph(
    rec_a: RecordingPen,
    rec_b: RecordingPen,
    target_upem: int = DEFAULT_UPEM,
    *,
    source_advance: int,
    source_center_y: float,
    source_max_height: float,
    pad: float = COMPOUND_PAD,
) -> Optional[GlyphMetrics]:
    """Side-by-side pair via shared half-cell scales, then merge."""
    ga = make_halfwidth_glyph(
        rec_a,
        target_upem,
        source_advance=source_advance,
        source_center_y=source_center_y,
        source_max_height=source_max_height,
        pad=pad,
    )
    gb = make_halfwidth_glyph(
        rec_b,
        target_upem,
        source_advance=source_advance,
        source_center_y=source_center_y,
        source_max_height=source_max_height,
        pad=pad,
    )
    if ga is None or gb is None:
        return None
    return merge_halfcell_glyphs(ga[0], gb[0], target_upem)


def apply_variant_recording(
    rec: RecordingPen,
    advance: int,
    target_upem: int,
    *,
    rot90_quarters: int = 0,
    flip_x: bool = False,
    flip_y: bool = False,
) -> Optional[GlyphMetrics]:
    """D4-rotate/reflect about the recording's contour bbox center."""
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    t = variant_transform(
        target_upem,
        rot90_quarters=rot90_quarters,
        flip_x=flip_x,
        flip_y=flip_y,
        center=(cx, cy),
    )
    det = t.xx * t.yy - t.xy * t.yx
    glyph = apply_transform(rec, t, reverse_winding=det < 0)
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    try:
        glyph.recalcBounds(None)
        lsb = int(glyph.xMin)
    except Exception:
        lsb = 0
    return glyph, advance, lsb


def recording_from_metrics(gm: GlyphMetrics) -> RecordingPen:
    glyph, _adv, _lsb = gm
    rec = RecordingPen()
    glyph.draw(rec, None)
    return rec


def empty_glyph() -> TTGlyph:
    g = TTGlyph()
    g.numberOfContours = 0
    g.xMin = g.yMin = g.xMax = g.yMax = 0
    return g


def resolve_nuosu_path(in_dir: str) -> str:
    path = os.path.join(in_dir, NUOSU_FILENAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing Yi source font: {path}")
    return path


# =============================================================================
# Third-cell segments (VS17–VS26)
# =============================================================================

# FE00 zero-width overlay (same glyph name as half-cell digraphs).

# ---------- VS17–VS26 ----------

# Unicode Variation Selectors Supplement: VS17 = U+E0100.
VS17_CP = 0xE0100
VS26_CP = 0xE0109
THIRD_VS_BASE = VS17_CP
THIRD_VS_COUNT = 10
THIRD_VS_LAST = VS26_CP

# Single-third ≈ 1/3; double-third ≈ 2/3 (composite scale factor).
THIRD_FACTOR = 1.0 / 3.0
TWO_THIRD_FACTOR = 2.0 / 3.0
THIRD_PAD_FRAC = 0.02

# (vs_cp, selector glyph name, segment suffix, axis, band0, band1)
# Bands are thirds along the axis: 0 = start (bottom/left), 1 = mid, 2 = end
# (top/right). ``band0..band1`` inclusive occupy that span.
ThirdSlot = Tuple[int, str, str, str, int, int]

THIRD_VS_SLOTS: Tuple[ThirdSlot, ...] = (
    (0xE0100, "vs17", "t3t", "y", 2, 2),  # top
    (0xE0101, "vs18", "t3tm", "y", 1, 2),  # top + middle
    (0xE0102, "vs19", "t3m", "y", 1, 1),  # middle
    (0xE0103, "vs20", "t3mb", "y", 0, 1),  # middle + bottom
    (0xE0104, "vs21", "t3b", "y", 0, 0),  # bottom
    (0xE0105, "vs22", "t3l", "x", 0, 0),  # left
    (0xE0106, "vs23", "t3lc", "x", 0, 1),  # left + center
    (0xE0107, "vs24", "t3c", "x", 1, 1),  # center
    (0xE0108, "vs25", "t3cr", "x", 1, 2),  # center + right
    (0xE0109, "vs26", "t3r", "x", 2, 2),  # right
)

THIRD_VS_CPS: Tuple[int, ...] = tuple(cp for cp, *_ in THIRD_VS_SLOTS)


def third_vs_glyph_name(vs_cp: int) -> str:
    if not (THIRD_VS_BASE <= vs_cp <= THIRD_VS_LAST):
        raise ValueError(f"not a third-cell VS: U+{vs_cp:X}")
    return f"vs{vs_cp - THIRD_VS_BASE + 17}"


def third_form_name(base_name: str, suffix: str) -> str:
    return f"{base_name}.{suffix}"


def _third_slot_rect(
    target_upem: float,
    *,
    axis: str,
    band0: int,
    band1: int,
) -> Tuple[float, float, float, float]:
    """Return `(x0, y0, x1, y1)` for bands `band0..band1` (inclusive)."""
    bot, top, _ = ideographic_bounds(int(target_upem))
    pad = target_upem * THIRD_PAD_FRAC
    lo_b = min(band0, band1)
    hi_b = max(band0, band1)
    if axis == "y":
        # band 0 = bottom, band 2 = top
        span = top - bot
        y0 = bot + span * (lo_b / 3.0) + pad
        y1 = bot + span * ((hi_b + 1) / 3.0) - pad
        return pad, y0, target_upem - pad, y1
    # band 0 = left, band 2 = right
    x0 = target_upem * (lo_b / 3.0) + pad
    x1 = target_upem * ((hi_b + 1) / 3.0) - pad
    return x0, bot + pad, x1, top - pad


def _factor_for_bands(band0: int, band1: int) -> float:
    n = abs(band1 - band0) + 1
    return TWO_THIRD_FACTOR if n >= 2 else THIRD_FACTOR


def _bake_simple_glyph(
    glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]]
) -> TTGlyph:
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    rec = _recording_from_glyph(glyph, glyph_set)
    pen = TTGlyphPen(None)
    rec.replay(pen)
    out = pen.glyph()
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


def _translate_ink_to_third_center(
    glyph: TTGlyph,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: int,
) -> Tuple[TTGlyph, int, int]:
    upem = float(target_upem)
    x0, y0, x1, y1 = _third_slot_rect(upem, axis=axis, band0=band0, band1=band1)
    dst_cx = (x0 + x1) / 2.0
    dst_cy = (y0 + y1) / 2.0
    try:
        glyph.recalcBounds(None)
        src_cx = (float(glyph.xMin) + float(glyph.xMax)) / 2.0
        src_cy = (float(glyph.yMin) + float(glyph.yMax)) / 2.0
    except Exception:
        return glyph, int(upem), int(getattr(glyph, "xMin", 0) or 0)
    dx = dst_cx - src_cx
    dy = dst_cy - src_cy
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        try:
            return glyph, int(upem), int(glyph.xMin)
        except Exception:
            return glyph, int(upem), 0
    rec = _recording_from_glyph(glyph, None)
    out = apply_transform(rec, Transform(1, 0, 0, 1, dx, dy))
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    return out, int(upem), lsb


def place_glyph_in_third(
    glyph: TTGlyph,
    advance: int,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: int = 1000,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[TTGlyph, int, int]:
    """Clip `glyph` to a third / two-thirds slot (slice — no stretch).

    Geometry is healed before and after the cut (see module docstring).
    """

    upem = float(target_upem)
    rect = _third_slot_rect(upem, axis=axis, band0=band0, band1=band1)
    clipped = clip_glyph_to_rect(glyph, rect, glyph_set=glyph_set)
    return finalize_slice_metrics(
        (clipped, int(upem), 0), glyph_set=glyph_set, upem=int(upem)
    )


def make_third_glyph(
    base_name: str,
    advance: int,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: Optional[int] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    factor: Optional[float] = None,
) -> Tuple[TTGlyph, int, int]:
    """Upright third segment as a slice of `base_name` (clip; no stretch)."""

    if glyph_set is None:
        raise ValueError("make_third_glyph requires glyph_set for slice bake")
    upem = int(
        target_upem if target_upem is not None else (advance if advance > 0 else 1000)
    )
    del factor
    rect = _third_slot_rect(float(upem), axis=axis, band0=band0, band1=band1)
    return make_segment_slice_glyph(
        base_name,
        advance=int(advance if advance > 0 else upem),
        rect=rect,
        glyph_set=glyph_set,
    )


def add_third_forms(
    base_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int = 1000,
) -> List[str]:
    """Slice each baked form by clipping every third / two-thirds slot rect."""
    added: List[str] = []
    for name in base_names:
        if name not in glyphs:
            continue
        adv, _lsb = metrics.get(name, (target_upem, 0))

        def _put(out_name: str, gm: Tuple[TTGlyph, int, int]) -> None:
            gm = finalize_slice_metrics(gm, glyph_set=glyphs, upem=target_upem)
            install_derived_glyph(
                out_name,
                gm,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
            )

        for _cp, _sel, suf, axis, b0, b1 in THIRD_VS_SLOTS:
            out_name = third_form_name(name, suf)
            if out_name in glyphs:
                continue
            _put(
                out_name,
                make_segment_slice_glyph(
                    name,
                    advance=adv,
                    rect=_third_slot_rect(
                        float(target_upem), axis=axis, band0=b0, band1=b1
                    ),
                    glyph_set=glyphs,
                ),
            )
        added.append(name)
    return added


def third_vs_liga_map(
    bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """`base + VS17..VS26` / `FE00` → third segment and/or zero-width `.ov`.

    Mirrors half-cell overlay spelling::

        base FE00              → base.ov
        base VS17              → base.t3t
        base FE00 VS17         → base.t3t.ov   (either FE00↔VS order)
        base.t3t FE00          → base.t3t.ov
        (+ optional PUA VS01 no-op after the base)
    """

    vs01 = vs_glyph_name(TRANSFORM_MODES[0][0])
    has_vs01 = vs01 in glyphs
    ov = OV_SELECTOR_NAME
    liga: Dict[Tuple[str, ...], str] = {}
    for form in bases:
        if form not in glyphs:
            continue
        form_ov = overlay_glyph_name(form)
        if form_ov in glyphs and ov in glyphs:
            liga[(form, ov)] = form_ov
            if has_vs01:
                liga[(form, vs01, ov)] = form_ov
        for vs_cp, sel_name, suf, _axis, _b0, _b1 in THIRD_VS_SLOTS:
            out = third_form_name(form, suf)
            if out not in glyphs:
                continue
            sel = sel_name if sel_name in glyphs else third_vs_glyph_name(vs_cp)
            if sel not in glyphs:
                continue
            liga[(form, sel)] = out
            if has_vs01:
                liga[(form, vs01, sel)] = out
            out_ov = overlay_glyph_name(out)
            if out_ov not in glyphs or ov not in glyphs:
                continue
            liga[(form, ov, sel)] = out_ov
            liga[(form, sel, ov)] = out_ov
            if has_vs01:
                liga[(form, vs01, ov, sel)] = out_ov
                liga[(form, vs01, sel, ov)] = out_ov
            liga[(out, ov)] = out_ov
            # Residual when a prior lookup already applied FE00 → `.ov`
            # (same pattern as quarter grid face vs half-cell GSUB order).
            if form_ov in glyphs:
                liga[(form_ov, sel)] = out_ov
                if has_vs01:
                    liga[(form_ov, vs01, sel)] = out_ov
    return liga


def prepare_third_cells(
    *,
    cjk_bases: Sequence[str],
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int = 1000,
) -> List[str]:
    """Install VS17–26 + FE00 marks, bake third segments and `.ov` overlays.

    Returns the form list that accepts third-cell VS (identity + D4).
    """
    # FE00 zero-width overlay selector (BMP PUA is edenia kana).
    if OV_SELECTOR_NAME not in glyphs:
        glyph_order.append(OV_SELECTOR_NAME)
        glyphs[OV_SELECTOR_NAME] = empty_glyph()
        metrics[OV_SELECTOR_NAME] = (0, 0)
    cmap[OV_SELECTOR_CP] = OV_SELECTOR_NAME

    for vs_cp, sel_name, _suf, _axis, _b0, _b1 in THIRD_VS_SLOTS:
        if sel_name not in glyphs:
            glyph_order.append(sel_name)
            glyphs[sel_name] = empty_glyph()
            metrics[sel_name] = (0, 0)
        cmap[vs_cp] = sel_name

    # Identity + D4 orientations already present for each base.
    forms: List[str] = []
    seen: set = set()
    for base in cjk_bases:
        if base not in glyphs or base in seen:
            continue
        forms.append(base)
        seen.add(base)
        for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            if suffix is None:
                continue
            vname = variant_glyph_name(base, suffix)
            if vname in glyphs and vname not in seen:
                forms.append(vname)
                seen.add(vname)

    add_third_forms(
        list(cjk_bases),
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )
    third_windows = {
        suf: _third_slot_rect(float(target_upem), axis=axis, band0=b0, band1=b1)
        for _cp, _sel, suf, axis, b0, b1 in THIRD_VS_SLOTS
    }
    propagate_d4_segments(
        cjk_bases,
        suffixes=tuple(suf for _cp, _sel, suf, _a, _b0, _b1 in THIRD_VS_SLOTS),
        form_name=third_form_name,
        windows=third_windows,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )

    # Zero-width overlays for bases and every third segment (trigraph stacking).
    ov_sources: List[str] = []
    for form in forms:
        if form not in glyphs:
            continue
        ov_sources.append(form)
        for _cp, _sel, suf, _axis, _b0, _b1 in THIRD_VS_SLOTS:
            segment = third_form_name(form, suf)
            if segment in glyphs:
                ov_sources.append(segment)
    add_overlay_forms(
        ov_sources,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
    )
    return forms


def install_third_cell_gsub(
    font,
    *,
    bases: Sequence[str],
    glyphs: Dict[str, TTGlyph],
) -> int:
    """Append third-cell VS + FE00 overlay ligatures to existing `GSUB`."""
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    liga = third_vs_liga_map(bases, glyphs=glyphs)
    if not liga:
        return 0

    # Longer ligatures before shorter (FE00+VS before VS alone).
    by_len: Dict[int, Dict[Tuple[str, ...], str]] = {}
    for comps, out in liga.items():
        by_len.setdefault(len(comps), {})[comps] = out
    lookups = [
        build_chunked_ligature_subst_lookup(by_len[length])
        for length in sorted(by_len.keys(), reverse=True)
    ]

    if "GSUB" in font:
        gsub = font["GSUB"].table
    else:
        gsub = ot.GSUB()
        gsub.Version = 0x00010000
        gsub.ScriptList = ot.ScriptList()
        gsub.ScriptList.ScriptRecord = []
        gsub.ScriptList.ScriptCount = 0
        gsub.FeatureList = ot.FeatureList()
        gsub.FeatureList.FeatureRecord = []
        gsub.FeatureList.FeatureCount = 0
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0
        table = newTable("GSUB")
        table.table = gsub
        font["GSUB"] = table

    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0
    if gsub.FeatureList is None:
        gsub.FeatureList = ot.FeatureList()
        gsub.FeatureList.FeatureRecord = []
        gsub.FeatureList.FeatureCount = 0
    if gsub.ScriptList is None:
        gsub.ScriptList = ot.ScriptList()
        gsub.ScriptList.ScriptRecord = []
        gsub.ScriptList.ScriptCount = 0

    # Ensure common script tags exist.
    existing_scripts = {sr.ScriptTag for sr in (gsub.ScriptList.ScriptRecord or [])}
    script_tags: List[str] = []
    for line in COMPOSITION_LANGUAGE_SYSTEMS:
        parts = line.replace(";", "").split()
        if len(parts) >= 2 and parts[0] == "languagesystem":
            script_tags.append(parts[1].ljust(4)[:4])
    for tag in script_tags:
        if tag in existing_scripts:
            continue
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        ls = ot.DefaultLangSys()
        ls.ReqFeatureIndex = 0xFFFF
        ls.FeatureCount = 0
        ls.FeatureIndex = []
        rec.Script.DefaultLangSys = ls
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gsub.ScriptList.ScriptRecord.append(rec)
        existing_scripts.add(tag)
    gsub.ScriptList.ScriptCount = len(gsub.ScriptList.ScriptRecord)

    li = gsub.LookupList.LookupCount
    gsub.LookupList.Lookup.extend(lookups)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    new_indices = list(range(li, li + len(lookups)))

    tag_to_fr = {fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])}
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            fr = ot.FeatureRecord()
            fr.FeatureTag = tag
            fr.Feature = ot.Feature()
            fr.Feature.FeatureParams = None
            fr.Feature.LookupListIndex = []
            fr.Feature.LookupCount = 0
            gsub.FeatureList.FeatureRecord.append(fr)
            gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)
            tag_to_fr[tag] = fr
            for sr in gsub.ScriptList.ScriptRecord:
                ls = sr.Script.DefaultLangSys
                if ls is None:
                    continue
                fi = list(ls.FeatureIndex or [])
                new_i = gsub.FeatureList.FeatureCount - 1
                if new_i not in fi:
                    fi.append(new_i)
                    ls.FeatureIndex = fi
                    ls.FeatureCount = len(fi)
        idxs = list(fr.Feature.LookupListIndex or [])
        idxs.extend(new_indices)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)

    return len(lookups)


# =============================================================================
# Quarter-cell segments (q / qv / qh)
# =============================================================================

# FE00 zero-width overlay.

# Four bands along the segment axis.
QUARTER_BANDS = 4
QUARTER_PAD_FRAC = 0.02

# (vs_cp, selector name, suffix, band0, band1) — axis comes from the face.
QuarterSlot = Tuple[int, str, str, int, int]

# Vertical face: Y axis. band 0 = bottom, band 3 = top.
# Selector names match Unicode VS indices (VS9 = FE08, VS27 = E010A, …).
QUARTER_VS_SLOTS_V: Tuple[QuarterSlot, ...] = (
    (0xFE08, "vs09", "q4th", 2, 3),  # top half
    (0xFE09, "vs10", "q4bh", 0, 1),  # bottom half
    (0xE010A, "vs27", "q4t", 3, 3),  # top quarter
    (0xE010B, "vs28", "q4nt", 2, 2),  # near-top
    (0xE010C, "vs29", "q4nb", 1, 1),  # near-bottom
    (0xE010D, "vs30", "q4b", 0, 0),  # bottom quarter
    (0xE010E, "vs31", "q4t3", 1, 3),  # top 3/4
    (0xE010F, "vs32", "q4b3", 0, 2),  # bottom 3/4
    (0xE0110, "vs33", "q4mh", 1, 2),  # middle half
)

# Horizontal face: X axis. band 0 = left, band 3 = right.
# Distinct suffixes (not shared with qv).
QUARTER_VS_SLOTS_H: Tuple[QuarterSlot, ...] = (
    (0xFE0A, "vs11", "q4lh", 0, 1),  # left half
    (0xFE0B, "vs12", "q4rh", 2, 3),  # right half
    (0xE0111, "vs34", "q4l", 0, 0),  # left quarter
    (0xE0112, "vs35", "q4nl", 1, 1),  # near-left
    (0xE0113, "vs36", "q4nr", 2, 2),  # near-right
    (0xE0114, "vs37", "q4r", 3, 3),  # right quarter
    (0xE0115, "vs38", "q4l3", 0, 2),  # left 3/4
    (0xE0116, "vs39", "q4r3", 1, 3),  # right 3/4
    (0xE0117, "vs40", "q4mc", 1, 2),  # middle half
)

# 2×2 grid face. VS41–44 corners tl,tr,bl,br; VS45–48 L 3/4 for the same corners.
# (vs_cp, selector name, suffix) — no band indices.
GridSlot = Tuple[int, str, str]
GRID_VS_SLOTS: Tuple[GridSlot, ...] = (
    (0xE0118, "vs41", "q2tl"),
    (0xE0119, "vs42", "q2tr"),
    (0xE011A, "vs43", "q2bl"),
    (0xE011B, "vs44", "q2br"),
    (0xE011C, "vs45", "q2tl3"),
    (0xE011D, "vs46", "q2tr3"),
    (0xE011E, "vs47", "q2bl3"),
    (0xE011F, "vs48", "q2br3"),
)

# Discrete 2×2 cells for D4 remapping of corners / L 3/4.
GRID_CELL_LABELS: Dict[str, FrozenSet[str]] = {
    "q2tl": frozenset({"tl"}),
    "q2tr": frozenset({"tr"}),
    "q2bl": frozenset({"bl"}),
    "q2br": frozenset({"br"}),
    "q2tl3": frozenset({"tl", "tr", "bl"}),
    "q2tr3": frozenset({"tl", "tr", "br"}),
    "q2bl3": frozenset({"tl", "bl", "br"}),
    "q2br3": frozenset({"tr", "bl", "br"}),
}

QUARTER_FACE_V = "qv"
QUARTER_FACE_H = "qh"
QUARTER_FACE_GRID = "q"
QUARTER_FACES = (QUARTER_FACE_GRID, QUARTER_FACE_V, QUARTER_FACE_H)


def quarter_slots_for_face(face: str) -> Tuple:
    match face:
        case "qv":
            return QUARTER_VS_SLOTS_V
        case "qh":
            return QUARTER_VS_SLOTS_H
        case "q":
            return GRID_VS_SLOTS
        case _:
            raise ValueError(
                f"quarter face must be one of {QUARTER_FACES}, got {face!r}"
            )


def quarter_axis_for_face(face: str) -> str:
    if face == QUARTER_FACE_GRID:
        raise ValueError("grid face q has no single segment axis")
    return "y" if face == QUARTER_FACE_V else "x"


def quarter_slot_parts(slot: Tuple) -> Tuple[int, str, str]:
    """`(vs_cp, selector name, suffix)` from a 3- or 5-tuple slot."""
    return slot[0], slot[1], slot[2]


def quarter_form_name(base_name: str, suffix: str, *, face: str = "") -> str:
    """Segment glyph name (`base.q4t`, `base.q4l`, …).

    `face` is accepted for call-site compatibility; qv/qh suffixes are
    already distinct so no face infix is required.
    """
    del face
    return f"{base_name}.{suffix}"


def _factor_for_bands(band0: int, band1: int) -> float:
    n = abs(band1 - band0) + 1
    return n / float(QUARTER_BANDS)


def _quarter_slot_rect(
    target_upem: float,
    *,
    axis: str,
    band0: int,
    band1: int,
) -> Tuple[float, float, float, float]:
    bot, top, _ = ideographic_bounds(int(target_upem))
    pad = target_upem * QUARTER_PAD_FRAC
    lo_b = min(band0, band1)
    hi_b = max(band0, band1)
    n = float(QUARTER_BANDS)
    if axis == "y":
        span = top - bot
        y0 = bot + span * (lo_b / n) + pad
        y1 = bot + span * ((hi_b + 1) / n) - pad
        return pad, y0, target_upem - pad, y1
    x0 = target_upem * (lo_b / n) + pad
    x1 = target_upem * ((hi_b + 1) / n) - pad
    return x0, bot + pad, x1, top - pad


def quarter_segment_windows(
    face: str, target_upem: int
) -> Dict[str, Tuple[float, float, float, float]]:
    """Finite AABBs for qv/qh band suffixes (D4 matching)."""
    axis = quarter_axis_for_face(face)
    out: Dict[str, Tuple[float, float, float, float]] = {}
    for slot in quarter_slots_for_face(face):
        _cp, _sel, suf, b0, b1 = slot
        out[suf] = _quarter_slot_rect(float(target_upem), axis=axis, band0=b0, band1=b1)
    return out


def _bake_simple_glyph(
    glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]]
) -> TTGlyph:
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    rec = _recording_from_glyph(glyph, glyph_set)
    pen = TTGlyphPen(None)
    rec.replay(pen)
    out = pen.glyph()
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


def _translate_ink_to_quarter_center(
    glyph: TTGlyph,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: int,
) -> Tuple[TTGlyph, int, int]:
    upem = float(target_upem)
    x0, y0, x1, y1 = _quarter_slot_rect(upem, axis=axis, band0=band0, band1=band1)
    dst_cx = (x0 + x1) / 2.0
    dst_cy = (y0 + y1) / 2.0
    try:
        glyph.recalcBounds(None)
        src_cx = (float(glyph.xMin) + float(glyph.xMax)) / 2.0
        src_cy = (float(glyph.yMin) + float(glyph.yMax)) / 2.0
    except Exception:
        return glyph, int(upem), int(getattr(glyph, "xMin", 0) or 0)
    dx = dst_cx - src_cx
    dy = dst_cy - src_cy
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        try:
            return glyph, int(upem), int(glyph.xMin)
        except Exception:
            return glyph, int(upem), 0
    rec = _recording_from_glyph(glyph, None)
    out = apply_transform(rec, Transform(1, 0, 0, 1, dx, dy))
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    return out, int(upem), lsb


def place_glyph_in_quarter(
    glyph: TTGlyph,
    advance: int,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: int = 1000,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[TTGlyph, int, int]:
    """Clip `glyph` to a quarter / half / 3/4 slot (slice — no stretch).

    Geometry is healed before and after the cut (see module docstring).
    """

    upem = float(target_upem)
    rect = _quarter_slot_rect(upem, axis=axis, band0=band0, band1=band1)
    clipped = clip_glyph_to_rect(glyph, rect, glyph_set=glyph_set)
    return finalize_slice_metrics(
        (clipped, int(upem), 0), glyph_set=glyph_set, upem=int(upem)
    )


def make_quarter_glyph(
    base_name: str,
    advance: int,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: Optional[int] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    factor: Optional[float] = None,
) -> Tuple[TTGlyph, int, int]:
    """Upright quarter segment as a slice of `base_name` (clip; no stretch)."""

    if glyph_set is None:
        raise ValueError("make_quarter_glyph requires glyph_set for slice bake")
    upem = int(
        target_upem if target_upem is not None else (advance if advance > 0 else 1000)
    )
    del factor
    rect = _quarter_slot_rect(float(upem), axis=axis, band0=band0, band1=band1)
    return make_segment_slice_glyph(
        base_name,
        advance=int(advance if advance > 0 else upem),
        rect=rect,
        glyph_set=glyph_set,
    )


def add_quarter_forms(
    base_names: Sequence[str],
    *,
    face: str,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int = 1000,
) -> List[str]:
    """Slice each baked form by clipping every quarter / half / 3/4 slot rect.

    ``qv``/``qh`` still reuse CJK ``.dkb`` / ``.dkt`` / ``.dk`` / ``.dkl`` halves when
    present (already clean clips); every other band is clipped from the base.
    """
    axis = quarter_axis_for_face(face)
    if face == QUARTER_FACE_V:
        inherit = {"q4th": "dkb", "q4bh": "dkt"}
    else:
        inherit = {"q4lh": "dk", "q4rh": "dkl"}
    slots = quarter_slots_for_face(face)
    added: List[str] = []
    for name in base_names:
        if name not in glyphs:
            continue
        adv, _lsb = metrics.get(name, (target_upem, 0))

        def _put(out_name: str, gm: Tuple[TTGlyph, int, int]) -> None:
            gm = finalize_slice_metrics(gm, glyph_set=glyphs, upem=target_upem)
            install_derived_glyph(
                out_name,
                gm,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
            )

        for _cp, _sel, suf, b0, b1 in slots:
            out = quarter_form_name(name, suf, face=face)
            if out in glyphs:
                continue
            src_half = inherit.get(suf)
            if src_half is not None:
                src = f"{name}.{src_half}"
                if src in glyphs:
                    _put(
                        out,
                        copy_named_glyph(
                            src, glyphs=glyphs, metrics=metrics, advance=adv
                        ),
                    )
                    continue
            _put(
                out,
                make_segment_slice_glyph(
                    name,
                    advance=adv,
                    rect=_quarter_slot_rect(
                        float(target_upem), axis=axis, band0=b0, band1=b1
                    ),
                    glyph_set=glyphs,
                ),
            )
        added.append(name)
    return added


def add_grid_forms(
    base_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int = 1000,
) -> List[str]:
    """2×2 corners and L 3/4 from CJK half slices (`.dk` / `.dkl` / `.dkb` / `.dkt`).

    Corners are clipped to quadrant half-planes (not `half − half`).
    L 3/4 shapes are unions of two clean halves.
    """
    bot, top, _ = ideographic_bounds(target_upem)
    mid_y = (bot + top) / 2.0
    mid_x = float(target_upem) * 0.5
    inf = float(target_upem) * HALF_PLANE_INF_FRAC
    added: List[str] = []

    def _plane(axis: str, keep: str, cut: float) -> Tuple[float, float, float, float]:
        return half_plane_rect(cut, axis=axis, keep=keep, inf=inf)

    for name in base_names:
        if name not in glyphs:
            continue
        adv, _lsb = metrics.get(name, (target_upem, 0))

        def _put(out_name: str, gm: Tuple[TTGlyph, int, int]) -> None:
            gm = finalize_slice_metrics(gm, glyph_set=glyphs, upem=target_upem)
            install_derived_glyph(
                out_name,
                gm,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
            )

        def _ensure_half(suf: str, axis: str, keep: str, cut: float) -> str:
            hname = f"{name}.{suf}"
            if hname not in glyphs:
                _put(
                    hname,
                    make_segment_slice_glyph(
                        name,
                        advance=adv,
                        rect=_plane(axis, keep, cut),
                        glyph_set=glyphs,
                    ),
                )
            return hname

        left = _ensure_half("dk", "x", "lo", mid_x)
        right = _ensure_half("dkl", "x", "hi", mid_x)
        top_h = _ensure_half("dkb", "y", "hi", mid_y)
        bot_h = _ensure_half("dkt", "y", "lo", mid_y)

        # Quadrant clips: [x-keep] ∩ [y-keep] as a single AABB (clean cut).
        corners = (
            ("q2tl", -inf, mid_y, mid_x, inf),
            ("q2tr", mid_x, mid_y, inf, inf),
            ("q2bl", -inf, -inf, mid_x, mid_y),
            ("q2br", mid_x, -inf, inf, mid_y),
        )
        for suf, x0, y0, x1, y1 in corners:
            out = quarter_form_name(name, suf)
            if out not in glyphs:
                _put(
                    out,
                    make_segment_slice_glyph(
                        name,
                        advance=adv,
                        rect=(x0, y0, x1, y1),
                        glyph_set=glyphs,
                    ),
                )

        ells = (
            ("q2tl3", top_h, left),
            ("q2tr3", top_h, right),
            ("q2bl3", bot_h, left),
            ("q2br3", bot_h, right),
        )
        for suf, a, b in ells:
            out = quarter_form_name(name, suf)
            if out not in glyphs:
                _put(
                    out,
                    boolean_union_named(
                        [a, b], glyphs=glyphs, metrics=metrics, advance=adv
                    ),
                )
        added.append(name)
    return added


def quarter_vs_liga_map(
    bases: Sequence[str],
    *,
    face: str,
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """`base + VS` / `FE00` → quarter segment and/or zero-width `.ov`.

    Includes residual ``base.ov + VS → segment.ov`` so segment ligas still
    fire when a prior lookup (half-cell GSUB on face ``q``) already consumed
    ``FE00``.
    """

    slots = quarter_slots_for_face(face)
    vs01 = vs_glyph_name(TRANSFORM_MODES[0][0])
    has_vs01 = vs01 in glyphs
    ov = OV_SELECTOR_NAME
    liga: Dict[Tuple[str, ...], str] = {}
    for form in bases:
        if form not in glyphs:
            continue
        form_ov = overlay_glyph_name(form)
        if form_ov in glyphs and ov in glyphs:
            liga[(form, ov)] = form_ov
            if has_vs01:
                liga[(form, vs01, ov)] = form_ov
        for slot in slots:
            _vs_cp, sel_name, suf = quarter_slot_parts(slot)
            out = quarter_form_name(form, suf, face=face)
            if out not in glyphs:
                continue
            if sel_name not in glyphs:
                continue
            liga[(form, sel_name)] = out
            if has_vs01:
                liga[(form, vs01, sel_name)] = out
            out_ov = overlay_glyph_name(out)
            if out_ov not in glyphs or ov not in glyphs:
                continue
            liga[(form, ov, sel_name)] = out_ov
            liga[(form, sel_name, ov)] = out_ov
            if has_vs01:
                liga[(form, vs01, ov, sel_name)] = out_ov
                liga[(form, vs01, sel_name, ov)] = out_ov
            liga[(out, ov)] = out_ov
            # On face `q`, half-cell GSUB runs first and may already have
            # turned `form + FE00` into `form.ov` before these lookups see
            # the segment selector (L 3/4 and corners). Residual ligas:
            if form_ov in glyphs:
                liga[(form_ov, sel_name)] = out_ov
                if has_vs01:
                    liga[(form_ov, vs01, sel_name)] = out_ov
    return liga


def prepare_quarter_cells(
    *,
    face: str,
    cjk_bases: Sequence[str],
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int = 1000,
) -> List[str]:
    """Install face VS + FE00, bake quarter segments and `.ov` overlays."""
    slots = quarter_slots_for_face(face)

    if OV_SELECTOR_NAME not in glyphs:
        glyph_order.append(OV_SELECTOR_NAME)
        glyphs[OV_SELECTOR_NAME] = empty_glyph()
        metrics[OV_SELECTOR_NAME] = (0, 0)
    cmap[OV_SELECTOR_CP] = OV_SELECTOR_NAME

    for slot in slots:
        vs_cp, sel_name, _suf = quarter_slot_parts(slot)
        if sel_name not in glyphs:
            glyph_order.append(sel_name)
            glyphs[sel_name] = empty_glyph()
            metrics[sel_name] = (0, 0)
        cmap[vs_cp] = sel_name

    forms: List[str] = []
    seen: set = set()
    for base in cjk_bases:
        if base not in glyphs or base in seen:
            continue
        forms.append(base)
        seen.add(base)
        for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            if suffix is None:
                continue
            vname = variant_glyph_name(base, suffix)
            if vname in glyphs and vname not in seen:
                forms.append(vname)
                seen.add(vname)

    if face == QUARTER_FACE_GRID:
        add_grid_forms(
            list(cjk_bases),
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )
        propagate_d4_segments(
            cjk_bases,
            suffixes=tuple(s[2] for s in GRID_VS_SLOTS),
            form_name=lambda form, suf: quarter_form_name(form, suf, face=face),
            windows={},
            labels=GRID_CELL_LABELS,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )
    else:
        add_quarter_forms(
            list(cjk_bases),
            face=face,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )
        propagate_d4_segments(
            cjk_bases,
            suffixes=tuple(quarter_slot_parts(s)[2] for s in slots),
            form_name=lambda form, suf: quarter_form_name(form, suf, face=face),
            windows=quarter_segment_windows(face, target_upem),
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )

    ov_sources: List[str] = []
    for form in forms:
        if form not in glyphs:
            continue
        ov_sources.append(form)
        for slot in slots:
            _cp, _sel, suf = quarter_slot_parts(slot)
            segment = quarter_form_name(form, suf, face=face)
            if segment in glyphs:
                ov_sources.append(segment)
    add_overlay_forms(
        ov_sources,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
    )
    return forms


def install_quarter_cell_gsub(
    font,
    *,
    face: str,
    bases: Sequence[str],
    glyphs: Dict[str, TTGlyph],
) -> int:
    """Append quarter-cell VS + FE00 overlay ligatures to `GSUB`."""
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    liga = quarter_vs_liga_map(bases, face=face, glyphs=glyphs)
    if not liga:
        return 0

    by_len: Dict[int, Dict[Tuple[str, ...], str]] = {}
    for comps, out in liga.items():
        by_len.setdefault(len(comps), {})[comps] = out
    lookups = [
        build_chunked_ligature_subst_lookup(by_len[length])
        for length in sorted(by_len.keys(), reverse=True)
    ]

    if "GSUB" in font:
        gsub = font["GSUB"].table
    else:
        gsub = ot.GSUB()
        gsub.Version = 0x00010000
        gsub.ScriptList = ot.ScriptList()
        gsub.ScriptList.ScriptRecord = []
        gsub.ScriptList.ScriptCount = 0
        gsub.FeatureList = ot.FeatureList()
        gsub.FeatureList.FeatureRecord = []
        gsub.FeatureList.FeatureCount = 0
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0
        table = newTable("GSUB")
        table.table = gsub
        font["GSUB"] = table

    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0
    if gsub.FeatureList is None:
        gsub.FeatureList = ot.FeatureList()
        gsub.FeatureList.FeatureRecord = []
        gsub.FeatureList.FeatureCount = 0
    if gsub.ScriptList is None:
        gsub.ScriptList = ot.ScriptList()
        gsub.ScriptList.ScriptRecord = []
        gsub.ScriptList.ScriptCount = 0

    existing_scripts = {sr.ScriptTag for sr in (gsub.ScriptList.ScriptRecord or [])}
    script_tags: List[str] = []
    for line in COMPOSITION_LANGUAGE_SYSTEMS:
        parts = line.replace(";", "").split()
        if len(parts) >= 2 and parts[0] == "languagesystem":
            script_tags.append(parts[1].ljust(4)[:4])
    for tag in script_tags:
        if tag in existing_scripts:
            continue
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        ls = ot.DefaultLangSys()
        ls.ReqFeatureIndex = 0xFFFF
        ls.FeatureCount = 0
        ls.FeatureIndex = []
        rec.Script.DefaultLangSys = ls
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gsub.ScriptList.ScriptRecord.append(rec)
        existing_scripts.add(tag)
    gsub.ScriptList.ScriptCount = len(gsub.ScriptList.ScriptRecord)

    li = gsub.LookupList.LookupCount
    gsub.LookupList.Lookup.extend(lookups)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    new_indices = list(range(li, li + len(lookups)))

    tag_to_fr = {fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])}
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            fr = ot.FeatureRecord()
            fr.FeatureTag = tag
            fr.Feature = ot.Feature()
            fr.Feature.FeatureParams = None
            fr.Feature.LookupListIndex = []
            fr.Feature.LookupCount = 0
            gsub.FeatureList.FeatureRecord.append(fr)
            gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)
            tag_to_fr[tag] = fr
            for sr in gsub.ScriptList.ScriptRecord:
                ls = sr.Script.DefaultLangSys
                if ls is None:
                    continue
                fi = list(ls.FeatureIndex or [])
                new_i = gsub.FeatureList.FeatureCount - 1
                if new_i not in fi:
                    fi.append(new_i)
                    ls.FeatureIndex = fi
                    ls.FeatureCount = len(fi)
        idxs = list(fr.Feature.LookupListIndex or [])
        idxs.extend(new_indices)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)

    return len(lookups)
