"""Yi standalone glyph transforms (NuosuSIL).

Encoding
--------
* One font (``panyi``) covering the whole Yi inventory.
* Standalones: NuosuSIL is monospace (shared advance). Every glyph gets the
  **same** ``sx`` from that advance and the **same** ``sy`` from the tallest
  ink height, then headless CAPE Weightor Width-mode stretch (``cape_weightor``);
  Y is fitted to the CJK typo box (center at 0.38em).
* Orientations: D4 square symmetries on **VS01..VS08** (``U+E000``..``U+E007``,
  UVS ``U+FE00``..``U+FE07``), including ``r90my``. Pipeline for the two
  outline sources: **transform / reorient first**, then stem-normalize
  (``id`` = identity; ``r90`` = rotate from the un-normalized upright).
  Stem normalize retries smaller/larger targets until strokes stay thick and
  non-self-intersecting; only then falls back to the un-normalized transform.
  Other D4 forms are TT composites of those two (``r180`` / ``mx`` / ``my`` ←
  id; ``r270`` / ``r90mx`` / ``r90my`` ← r90). After each outline and each
  composite, ink is re-pinned to the padded CJK floor.
* Overlay (GlyphWiki / build_subfonts): **``U+FE08``** superimposes preceding
  glyphs into one cell — everything but the **last** glyph before ``FE08``
  becomes zero-advance (``.ov``); the last keeps the em advance.
* panyi slice overlays use ``U+FE08``/``U+FE09`` instead (see ``yi_slice``):
  horizontal / vertical half-plane joins.
* No side-by-side digraph compounds. Full D4 (8 modes) remains available for
  build_subfonts / GlyphWiki via ``TRANSFORM_MODES``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

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
    Glyph as TTGlyph,
    GlyphComponent,
    GlyphCoordinates,
)

try:
    from kage.mapping import D4_MODES, MirrorVS
except ImportError:  # Scripts.* import style
    from Scripts.kage.mapping import D4_MODES, MirrorVS

from cape_weightor import (
    apply_width,
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
# Full D4 set (8) — Yi orientations + build_subfonts / GlyphWiki.
VS_COUNT = MirrorVS.MODE_COUNT
VS_LAST = VS_BASE + VS_COUNT - 1  # U+E007

UVS_BASE = 0xFE00  # VS1..VS8 → identity..r90my
UVS_LAST = UVS_BASE + VS_COUNT - 1  # U+FE07
STACK_MARK_CP = 0xFE08  # Unicode VS9 — superimpose (not a D4 mode)

DEFAULT_UPEM = 1000
# Optional inset of the shared advance box inside the CJK cell (uniform).
STANDALONE_PAD = 0.0
HALFWIDTH_PAD = 0.0
COMPOUND_PAD = 0.0
# Standalone only: CAPE Weightor Width-mode factor after fit
# (0.15 → target outer width 115% of post-fit ink, stems preserved).
STANDALONE_CONTOUR_WIDEN = 0.15
# Inset from CJK typo top/bottom when fitting Y (fraction of em).
# Keeps short glyphs from sitting on the raw descent (-0.12em), which reads
# low next to CJK ink that usually rests nearer the baseline.
STANDALONE_VERT_PAD = 0.05

# Match build_yi / build_subfonts OS/2 + hhea (CJK ideographic body).
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
    """``ClassDef`` from glyph→class map (class 0 omitted)."""
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

    ``backtrack_seq`` is closest-to-input first (OpenType backtrack order).
    ``SubstLookupRecord.LookupListIndex`` is left 0 for the caller to patch.
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

    Geometric em midpoint is ``(upem/2, upem/2)``; CJK ink after uniform
    UPM scale sits near ``(upem/2, 0.38·upem)``. Centering Yi there keeps
    mixed CJK+Yi lines vertically aligned.
    """
    bottom, top, _h = ideographic_bounds(target_upem)
    return target_upem / 2.0, (top + bottom) / 2.0


def ideographic_bounds(target_upem: int) -> Tuple[float, float, float]:
    """CJK typo box ``(bottom, top, height)`` using ascent 0.88 / descent -0.12."""
    top = target_upem * TYPO_ASCENDER_FRAC
    bottom = target_upem * TYPO_DESCENDER_FRAC
    return bottom, top, top - bottom


# (vs_cp, rot90_quarters, flip_x, flip_y, name_suffix or None for identity)
# Shared with build_subfonts / GlyphWiki via kage.mapping.D4_MODES.
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
    """``(vertical_stem, horizontal_stem)`` from an upright Yi standalone."""
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
REFERENCE_VERTICAL_STEM = 80.0  # match U+4E28-like vertical weight
REFERENCE_HORIZONTAL_STEM = 60.0  # match U+4E00-like horizontal weight

# Post-offset contour heal: snap near-coincident on-curve points (UPM@1000).
CONTOUR_SNAP_EPSILON = 1.5
# Cap each stem-offset axis step so large estimate errors don't shred joins.
MAX_STEM_OFFSET_STEP = 10.0
# After normalize, reject if a stem falls below this fraction of its reference
# (or of the pre-normalize stem). Retry other target scales first.
MIN_NORM_STEM_FRAC = 0.4
# Blend weights toward the reference (1 = full match, 0 = no change).
NORM_BLEND_STEPS: Tuple[float, ...] = (1.0, 0.85, 0.7, 0.55, 0.4, 0.25, 0.15)
# Extra absolute scale factors on the reference stems (larger / smaller).
NORM_REF_SCALES: Tuple[float, ...] = (1.15, 0.85, 1.3, 0.7, 1.45, 0.55)

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

    Only true tips: angle APB acute (``cos > sharp_cos`` ≈ <60°) and P far
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
            if (
                dist < min_protrusion
                or dist > max_protrusion
                or dist < 0.75 * ab_len
            ):
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
    """First font under ``in_dir`` that has both U+4E00 and U+4E28."""
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
    """Decompose ``glyph_name`` and scale uniformly to target UPM space."""
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
    """Fixed ``(vertical, horizontal)`` stroke weights from U+4E28 / U+4E00.

    Single-stroke CJK radicals are measured by ink bbox thickness after a
    uniform ``target_upem / source_upem`` scale (scanline stem estimators
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
    3. Contour-offset X and Y so both match ``vertical_stem`` / ``horizontal_stem``
       (typically from ``measure_cjk_reference_stems``: U+4E28 / U+4E00).
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
    """Normalize stems then Width-mode squash to ``target_ink_width``.

    Thin wrapper around ``normalize_glyph_stems_after_transform`` for the
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
    """``(vertical_stem, horizontal_stem)``; bakes composites first."""
    g = glyph
    adv = int(advance)
    if g.isComposite():
        if glyph_set is None:
            return 0.0, 0.0
        g, adv, _ = _bake_transformed_glyph(
            g, Transform(), adv, glyph_set=glyph_set
        )
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


def _stem_target_candidates(
    pre_v: float,
    pre_h: float,
    ref_v: float,
    ref_h: float,
) -> List[Tuple[float, float]]:
    """Ordered (V, H) stem targets: full ref, blends, then larger/smaller refs."""
    seen: set[Tuple[int, int]] = set()
    out: List[Tuple[float, float]] = []

    def _add(tv: float, th: float) -> None:
        if tv <= 0 and th <= 0:
            return
        key = (int(round(tv * 10)), int(round(th * 10)))
        if key in seen:
            return
        seen.add(key)
        out.append((tv, th))

    # 1) Full reference, then gentler blends back toward the pre-norm stems.
    for t in NORM_BLEND_STEPS:
        tv = pre_v + t * (ref_v - pre_v) if pre_v > 0 else ref_v * t
        th = pre_h + t * (ref_h - pre_h) if pre_h > 0 else ref_h * t
        _add(tv, th)

    # 2) Larger / smaller absolute references (same scale on both axes).
    for s in NORM_REF_SCALES:
        _add(ref_v * s, ref_h * s)

    # 3) Per-axis larger/smaller around the reference (one axis at a time).
    for s in (1.2, 0.8, 1.35, 0.65):
        _add(ref_v * s, ref_h)
        _add(ref_v, ref_h * s)

    return out


def normalize_glyph_stems_with_retry(
    glyph: TTGlyph,
    advance: int,
    *,
    vertical_stem: float,
    horizontal_stem: float,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> GlyphMetrics:
    """Transform-ready glyph → try stem targets until thick and non-intersecting.

    Tries the full reference, then smaller blends toward the pre-normalize
    stems, then larger/smaller absolute targets. Returns the first acceptable
    result, or the un-normalized glyph if every attempt fails.
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

    for tv, th in _stem_target_candidates(pre_v, pre_h, vertical_stem, horizontal_stem):
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

    # Give up — keep the reoriented outline as-is.
    return glyph, int(advance), raw_lsb


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
) -> List[Tuple[int, str, str]]:
    """Create D4 forms from two outlines: id + ``r90`` (transform, then normalize).

    Pipeline for each outline source::

        1. transform / reorient (id = identity; r90 = rotate from **un-normalized** upright)
        2. stem-normalize, retrying smaller/larger targets until strokes stay
           thick and non-self-intersecting (else keep step-1)
        3. pin ink to the padded CJK floor

    Other orientations are simple rotate/reflect composites (no further stem
    offset)::

        id  →  r180 / mx / my
        r90 →  r270 / r90mx / r90my

    ``sideways_target_width`` / ``sideways_center_x`` are unused (compat only).

    Returns ``[(vs_cp, suffix, variant_glyph_name), ...]`` for GSUB wiring.
    """
    del sideways_target_width, sideways_center_x

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

    def _install(name: str, glyph: TTGlyph, adv: int, glyph_lsb: int) -> None:
        if name in glyphs:
            glyphs[name] = glyph
            metrics[name] = (adv, glyph_lsb)
            return
        glyph_order.append(name)
        glyphs[name] = glyph
        metrics[name] = (adv, glyph_lsb)

    def _pin(glyph: TTGlyph, adv: int) -> GlyphMetrics:
        return pin_glyph_ink_to_cjk_floor(
            glyph, adv, target_upem, glyph_set=glyphs
        )

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
        )

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
            return _pin(baked, adv)

        norm_g, norm_a, _norm_l = normalize_glyph_stems_with_retry(
            baked,
            adv,
            vertical_stem=ref_v,
            horizontal_stem=ref_h,
            glyph_set=glyphs,
        )
        return _pin(norm_g, norm_a)

    # Keep the un-normalized upright as the transform source for r90.
    src_glyph = glyphs[base_name]
    src_adv, src_lsb = int(metrics[base_name][0]), int(metrics[base_name][1])

    # 1) id: identity transform, then normalize (or keep source if too thin).
    g0, a0, l0 = _transform_then_normalize(src_glyph, src_adv)
    glyphs[base_name] = g0
    metrics[base_name] = (int(a0), int(l0))
    advance, lsb = int(a0), int(l0)

    # 2) r90: rotate from **un-normalized** upright, then normalize.
    if need_r90 and r90_name not in glyphs:
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
            if suffix == "r90":
                installed.append((vs_cp, suffix, m_name))
                continue
            if suffix in SIDEWAYS_FROM_R90 and r90_name in glyphs:
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
            else:
                m_glyph, m_adv, m_lsb = _composite_from(
                    base_name,
                    glyphs[base_name],
                    metrics[base_name][0],
                    metrics[base_name][1],
                    rot90_quarters=rot,
                    flip_x=flip_x,
                    flip_y=flip_y,
                )
            # Flips about contour center drop floor-pinned ink below baseline.
            m_glyph, m_adv, m_lsb = _pin(m_glyph, m_adv)
            _install(m_name, m_glyph, m_adv, m_lsb)
        installed.append((vs_cp, suffix, m_name))
    return installed


def vs_glyph_name(vs_cp: int) -> str:
    if vs_cp == STACK_MARK_CP:
        return "vsStack"
    if VS_BASE <= vs_cp <= VS_LAST:
        return f"vs{vs_cp - VS_BASE + 1:02d}"
    raise ValueError(f"not a Yi VS/stack codepoint: U+{vs_cp:04X}")


def stack_glyph_name() -> str:
    return "vsStack"


def uvs_selector_for_mode(mode_index: int) -> int:
    """Unicode VS1..VS8 (U+FE00..) for D4 mode index 0..7."""
    return UVS_BASE + mode_index


def build_d4_uvs_entries(
    base_cp: int,
    base_glyph: str,
    *,
    glyphs: Dict[str, TTGlyph],
    modes: Optional[Sequence[TransformMode]] = None,
) -> List[Tuple[int, int, Optional[str]]]:
    """``(base_cp, U+FE0n, variantName)`` rows for ``setupCharacterMap(uvs=...)``.

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
        if vname in glyphs:
            rows.append((base_cp, uvs_selector_for_mode(mode_i), vname))
    return rows


def variant_glyph_name(base_name: str, suffix: str) -> str:
    return f"{base_name}.{suffix}"


def overlay_glyph_name(base_name: str) -> str:
    """Zero-advance form of ``base_name`` for FE08 superposition."""
    return f"{base_name}.ov"


def orientation_form_names(
    base_name: str,
    *,
    modes: Optional[Sequence[TransformMode]] = None,
) -> List[str]:
    """Identity + non-identity D4 variant names for ``base_name``."""
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
) -> str:
    """Ensure FE08 stack mark glyph exists (zero-width) and is cmap'd."""
    sname = stack_glyph_name()
    if sname not in glyphs:
        glyph_order.append(sname)
        glyphs[sname] = empty_glyph()
        metrics[sname] = (0, 0)
    cmap[STACK_MARK_CP] = sname
    return sname


def add_overlay_forms(
    form_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    limit: Optional[int] = None,
) -> List[str]:
    """Create zero-advance ``.ov`` composites for each name in ``form_names``.

    ``limit`` caps how many new ``.ov`` glyphs are added (GlyphWiki 64k budget).
    Returns the list of base form names that received an ``.ov``.
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
    """Install FE08 overlay lookups into ``font`` GSUB (create or append).

    ``A B FE08`` → ``A.ov`` (0-advance) + ``B`` (keeps advance); FE08 consumed.
    Longer stacks (``A B FE08 C FE08`` …) work by repeating the zero+consume
    pair in the feature list (``max_stack`` times).

    Requires ``.ov`` forms already present for entries in ``full_forms``.
    Returns number of feature-attached lookup slots added.
    """
    from fontTools.otlLib.builder import (  # local import keeps module import light
        buildLigatureSubstSubtable,
        buildLookup,
        buildSingleSubstSubtable,
    )
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    def _coverage(names: Sequence[str]) -> ot.Coverage:
        cov = ot.Coverage()
        cov.glyphs = list(names)
        return cov

    forms = _gid_sort([n for n in full_forms if n in glyphs])
    overlayable = _gid_sort([n for n in forms if overlay_glyph_name(n) in glyphs])
    if not overlayable:
        return 0

    stack = stack_glyph_name()
    if stack not in glyphs:
        return 0

    # 1) A' with lookahead B FE08 → A.ov  (stack not consumed yet)
    overlay_single = {name: overlay_glyph_name(name) for name in overlayable}
    single_sub = buildSingleSubstSubtable(overlay_single)
    single_lu = buildLookup([single_sub])
    single_lu.LookupType = 1

    st = ot.ChainContextSubst()
    st.Format = 3
    st.BacktrackGlyphCount = 0
    st.BacktrackCoverage = []
    st.InputGlyphCount = 1
    st.InputCoverage = [_coverage(overlayable)]
    st.LookAheadGlyphCount = 2
    st.LookAheadCoverage = [_coverage(forms), _coverage([stack])]
    st.SubstCount = 1
    rec = ot.SubstLookupRecord()
    rec.SequenceIndex = 0
    rec.LookupListIndex = 0  # patched
    st.SubstLookupRecord = [rec]
    chain_lu = buildLookup([st])
    chain_lu.LookupType = 6

    # 2) B + FE08 → B  (consume stack; runs after zeroing)
    consume_map = {(name, stack): name for name in forms}
    consume_sub = buildLigatureSubstSubtable(consume_map)
    consume_lu = buildLookup([consume_sub])
    consume_lu.LookupType = 4

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

    base = gsub.LookupList.LookupCount
    # Order: chain, nested single, consume. Feature only lists chain + consume
    # (repeated so A B FE08 C FE08 … can apply multiple times).
    chain_index = base
    single_index = base + 1
    consume_index = base + 2
    st.SubstLookupRecord[0].LookupListIndex = single_index
    gsub.LookupList.Lookup.extend([chain_lu, single_lu, consume_lu])
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)

    feature_lookup_idxs: List[int] = []
    for _ in range(max(1, max_stack)):
        feature_lookup_idxs.append(chain_index)
        feature_lookup_idxs.append(consume_index)

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
        for li in feature_lookup_idxs:
            idxs.append(li)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)

    return len(feature_lookup_idxs)


# Shared across build_yi / build_subfonts / GlyphWiki.
COMPOSITION_FEATURE_TAGS: Tuple[str, ...] = ("ccmp", "rlig", "liga")
COMPOSITION_LANGUAGE_SYSTEMS: Tuple[str, ...] = (
    "languagesystem DFLT dflt;",
    "languagesystem latn dflt;",
    "languagesystem yi dflt;",
    "languagesystem hani dflt;",
)


def composition_fea(*rule_groups: Sequence[str]) -> str:
    """FEA for mandatory composition: ``ccmp`` + ``rlig`` + ``liga`` on common scripts.

    Each ``rule_groups`` entry is a sequence of already-indented ``sub ...;`` lines.
    Empty groups are skipped. Returns ``\"\"`` when there are no rules.
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
    q = quarters % 4
    if q == 0:
        return ((1.0, 0.0), (0.0, 1.0))
    if q == 1:
        return ((0.0, 1.0), (-1.0, 0.0))
    if q == 2:
        return ((-1.0, 0.0), (0.0, -1.0))
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
    """D4 map rotating/reflecting about ``center`` (default: ideographic mid)."""
    if rot90_quarters % 4 == 0 and not flip_x and not flip_y:
        return Transform()
    m = variant_matrix(rot90_quarters=rot90_quarters, flip_x=flip_x, flip_y=flip_y)
    (xx, xy), (yx, yy) = m
    cx, cy = center if center is not None else ideographic_center(target_upem)
    # p' = M·(p - c) + c
    dx = cx - xx * cx - yx * cy
    dy = cy - xy * cx - yy * cy
    return Transform(xx, xy, yx, yy, dx, dy)


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
) -> GlyphMetrics:
    """D4 variant of ``base_name`` about the contour bounding-box center.

    Axis-aligned maps (r180 / mx / my) stay one-component TT composites.
    Rotations that need a full 2×2 matrix (r90 / r270 / diagonals) are baked
    to outlines — many viewers mishandle ``WE_HAVE_A_TWO_BY_TWO``, which is
    why those cells looked empty.
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
    needs_2x2 = abs(t.xy) > 1e-9 or abs(t.yx) > 1e-9
    if needs_2x2:
        if src is None:
            raise ValueError(
                f"2x2 variant of {base_name!r} needs base_glyph or glyph_set"
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


def _recording_from_glyph(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> RecordingPen:
    """Expand ``glyph`` (including shallow composites) to a recording."""
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
    """Zero-width right-slot composite: ``left_name`` shifted +½em (digraph overlay)."""
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
    """Zero-advance same-cell overlay of ``base_name`` (FE08 superposition)."""
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
    """Translate ``glyph`` so its bbox center sits at the CJK typo midpoint."""
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
    """Tallest outline height among ``glyph_names`` (design units)."""
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
    """Axis scales ``(sx, sy)``, mapping ``(src_cx, src_cy)`` → destination center."""
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
    """Padded CJK cell ``(floor, ceiling, height)`` for Yi ink placement."""
    typo_bottom, typo_top, _ = ideographic_bounds(target_upem)
    inset = target_upem * max(pad, 0.0)
    bottom = typo_bottom + inset
    top = typo_top - inset
    cell_h = top - bottom
    if cell_h <= 1e-6:
        return typo_bottom, typo_top, typo_top - typo_bottom
    return bottom, top, cell_h


def _fit_glyph_to_cjk_height(
    glyph: TTGlyph,
    target_upem: int,
    *,
    pad: float = STANDALONE_VERT_PAD,
) -> TTGlyph:
    """Match ink to a vertically padded CJK typo box.

    * Taller than the padded box → squash Y to that height, bottom on the floor.
    * Shorter (or equal) → translate so the ink bottom sits on the floor.

    ``pad`` is a fraction of em inset from typo ascent/descent so Yi does not
    hang on the raw -0.12em descent (which sits below typical CJK ink).
    """
    bottom, _top, cell_h = cjk_padded_floor(target_upem, pad=pad)
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
        # y' = bottom + sy·(y - y0)
        return apply_transform(rec, Transform(1, 0, 0, sy, 0, bottom - sy * y0))
    dy = bottom - y0
    if abs(dy) < 1e-6:
        return glyph
    return apply_transform(rec, Transform(1, 0, 0, 1, 0, dy))


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
    stroke_weight: Optional[float] = None,  # unused; kept for call-site compat
) -> Optional[GlyphMetrics]:
    """Shared ``sx`` from advance, shared ``sy`` from inventory max ink height.

    X is placed from the monospace advance center (side bearings preserved).
    Contours are then stretched with CAPE Weightor Width mode (``widen``,
    default +15% outer width, vertical stems compensated). Then Y is fitted
    to a padded CJK typo box: squash if taller, otherwise pin the ink bottom
    to the padded floor (above raw descent).
    """
    del stroke_weight
    del source_center_y  # retained for call-site compat / inventory symmetry
    if source_advance <= 0 or source_max_height <= 0:
        return None
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    _x0, y0, _x1, y1 = bounds
    cell = target_upem * (1.0 - 2.0 * pad)
    scale_x = cell / float(source_advance)
    scale_y = cell / float(source_max_height)
    dst_cx, dst_cy = ideographic_center(target_upem)
    glyph = _uniform_place(
        rec,
        scale_x=scale_x,
        scale_y=scale_y,
        src_cx=source_advance / 2.0,
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
    glyph = _fit_glyph_to_cjk_height(glyph, target_upem, pad=vert_pad)
    try:
        glyph.recalcBounds(None)
        lsb = int(glyph.xMin)
    except Exception:
        lsb = 0
    return glyph, target_upem, lsb


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
    """Half-em ``sx``; same inventory-wide ``sy`` as standalones (full cell height)."""
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
