"""Yi standalone / compound glyph transforms (NuosuSIL).

Encoding
--------
* One font (``panyi``) covering the whole Yi inventory.
* Standalone / half-cells: fit into the CJK typo box (1000×1000 body with
  center at 0.38em) by **shifting outline points** independently on X and Y.
* Compounds: ``yi1 + CGJ (U+034F) + yi2 + VS0n`` (``rlig``) unpacks to a
  digraph of shared half-cell glyphs (left slot + zero-width right slot) so
  all N² pairs fit under the 64k glyph-ID limit — no per-pair glyphs and no
  outline merging. Identity uses VS01.
* Variants: the 8 unique square symmetries (D4) — 90° rotations and
  axis reflections — each with its own VS (``U+E000``..``U+E007``).
  Geometric duplicates are omitted (e.g. ``mxy === r180``).
  Only the identity form stores outlines; the other seven are one-component
  TrueType composites referencing identity about the CJK typo center.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
    USE_MY_METRICS,
    Glyph as TTGlyph,
    GlyphComponent,
)

try:
    from kage.mapping import D4_MODES, MirrorVS
except ImportError:  # Scripts.* import style
    from Scripts.kage.mapping import D4_MODES, MirrorVS

# ---------- Constants ----------

YI_SYLLABLES = (0xA000, 0xA48C)
YI_RADICALS = (0xA490, 0xA4CF)

# Combining Grapheme Joiner — joins yi₁ / yi₂ in compound ligatures.
CGJ_CP = 0x034F

VS_BASE = 0xE000
VS_COUNT = MirrorVS.MODE_COUNT
VS_LAST = VS_BASE + VS_COUNT - 1  # U+E007

DEFAULT_UPEM = 1000
STANDALONE_PAD = 0.08
HALFWIDTH_PAD = 0.08
COMPOUND_PAD = 0.06

# Match build_yi / build_subfonts OS/2 + hhea (CJK ideographic body).
TYPO_ASCENDER_FRAC = 0.88
TYPO_DESCENDER_FRAC = -0.12

NUOSU_FILENAME = "NuosuSIL-Regular.ttf"

Bounds = Tuple[float, float, float, float]
GlyphMetrics = Tuple[TTGlyph, int, int]


def ideographic_center(target_upem: int) -> Tuple[float, float]:
    """Center of the CJK typo box (ascent 0.88em / descent -0.12em).

    Geometric em midpoint is ``(upem/2, upem/2)``; CJK ink after uniform
    UPM scale sits near ``(upem/2, 0.38·upem)``. Centering Yi there keeps
    mixed CJK+Yi lines vertically aligned.
    """
    ascent = target_upem * TYPO_ASCENDER_FRAC
    descent = target_upem * TYPO_DESCENDER_FRAC
    return target_upem / 2.0, (ascent + descent) / 2.0


# (vs_cp, rot90_quarters, flip_x, flip_y, name_suffix or None for identity)
# Shared with build_subfonts / GlyphWiki via kage.mapping.D4_MODES.
TransformMode = Tuple[int, int, bool, bool, Optional[str]]

TRANSFORM_MODES: List[TransformMode] = [
    (MirrorVS.codepoint(mode), rot, fx, fy, suffix)
    for mode, rot, fx, fy, suffix in D4_MODES
]


def vs_glyph_name(vs_cp: int) -> str:
    return f"vs{vs_cp - VS_BASE + 1:02d}"


def cgj_glyph_name() -> str:
    return "cgj"


def variant_glyph_name(base_name: str, suffix: str) -> str:
    return f"{base_name}.{suffix}"


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
) -> GlyphMetrics:
    """One-component TT composite: ``base_name`` under a D4 map about typo center."""
    t = variant_transform(
        target_upem,
        rot90_quarters=rot90_quarters,
        flip_x=flip_x,
        flip_y=flip_y,
    )
    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = base_name
    comp.x = otRound(t.dx)
    comp.y = otRound(t.dy)
    comp.flags = USE_MY_METRICS | ROUND_XY_TO_GRID
    if (t.xx, t.xy, t.yx, t.yy) != (1.0, 0.0, 0.0, 1.0):
        # fontTools: ((xx, xy), (yx, yy)) with x' = xx·x + yx·y + dx
        comp.transform = ((t.xx, t.xy), (t.yx, t.yy))
    g.components = [comp]
    return g, advance, lsb


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
    left.flags = ROUND_XY_TO_GRID
    right = GlyphComponent()
    right.glyphName = right_name
    right.x = half
    right.y = 0
    right.flags = ROUND_XY_TO_GRID
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
    comp.flags = ROUND_XY_TO_GRID
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
        return YiInventory(
            source_path=source_path,
            src_cps=tuple(ordered),
            glyph_names=names,
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


def _axis_shift_fit(
    rec: RecordingPen,
    *,
    target_w: float,
    target_h: float,
    center_x: float,
    center_y: float,
) -> Optional[TTGlyph]:
    """Fit bbox into a target box by shifting points on each axis.

    Independently remaps X and Y so source ``[x0,x1]×[y0,y1]`` lands in a
    ``target_w×target_h`` box centered at ``(center_x, center_y)``. Each
    outline point (on- and off-curve) moves by an amount that varies with
    its position on that axis::

        x' = (center_x - target_w/2) + (x - x0) / (x1 - x0) * target_w
        y' = (center_y - target_h/2) + (y - y0) / (y1 - y0) * target_h
    """
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    bw = max(x1 - x0, 1e-6)
    bh = max(y1 - y0, 1e-6)
    sx = target_w / bw
    sy = target_h / bh
    # Equivalent shift form: x' = x + (sx-1)*(x-x0) + (left - x0)
    left = center_x - target_w / 2.0
    bottom = center_y - target_h / 2.0
    t = Transform(sx, 0, 0, sy, left - x0 * sx, bottom - y0 * sy)
    glyph = apply_transform(rec, t)
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    return glyph


def make_standalone_glyph(
    rec: RecordingPen,
    target_upem: int = DEFAULT_UPEM,
    *,
    pad: float = STANDALONE_PAD,
    stroke_weight: Optional[float] = None,  # unused; kept for call-site compat
) -> Optional[GlyphMetrics]:
    """Axis-shift fit into the CJK typo box (1000×1000 body, center at 0.38em)."""
    del stroke_weight
    inner = target_upem * (1.0 - 2.0 * pad)
    cx, cy = ideographic_center(target_upem)
    glyph = _axis_shift_fit(
        rec,
        target_w=inner,
        target_h=inner,
        center_x=cx,
        center_y=cy,
    )
    if glyph is None:
        return None
    glyph = center_glyph_in_cell(glyph, target_upem, center=(cx, cy))
    return glyph, target_upem, int(glyph.xMin)


def make_halfwidth_glyph(
    rec: RecordingPen,
    target_upem: int = DEFAULT_UPEM,
    *,
    pad: float = HALFWIDTH_PAD,
    stroke_weight: Optional[float] = None,  # unused; kept for call-site compat
) -> Optional[GlyphMetrics]:
    """Axis-shift fit into a half-em cell (same CJK vertical center as standalone)."""
    del stroke_weight
    adv = target_upem // 2
    _, cy = ideographic_center(target_upem)
    glyph = _axis_shift_fit(
        rec,
        target_w=adv * (1.0 - 2.0 * pad),
        target_h=target_upem * (1.0 - 2.0 * pad),
        center_x=adv / 2.0,
        center_y=cy,
    )
    if glyph is None:
        return None
    return glyph, adv, int(glyph.xMin)


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
    pad: float = COMPOUND_PAD,
) -> Optional[GlyphMetrics]:
    """Side-by-side pair via axis-shift fit of each half, then merge."""
    half = target_upem / 2.0
    cell_w = half * (1.0 - 2.0 * pad)
    cell_h = target_upem * (1.0 - 2.0 * pad)
    _, cy = ideographic_center(target_upem)

    ga = _axis_shift_fit(
        rec_a,
        target_w=cell_w,
        target_h=cell_h,
        center_x=half / 2.0,
        center_y=cy,
    )
    gb = _axis_shift_fit(
        rec_b,
        target_w=cell_w,
        target_h=cell_h,
        center_x=half / 2.0,  # left-slot; merge shifts right
        center_y=cy,
    )
    if ga is None or gb is None:
        return None
    return merge_halfcell_glyphs(ga, gb, target_upem)


def apply_variant_recording(
    rec: RecordingPen,
    advance: int,
    target_upem: int,
    *,
    rot90_quarters: int = 0,
    flip_x: bool = False,
    flip_y: bool = False,
) -> Optional[GlyphMetrics]:
    """Center in the CJK typo box, then D4-rotate/reflect about that center."""
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    cx, cy = ideographic_center(target_upem)
    # Center first so rotations/reflections orbit the CJK midpoint.
    sx = cx - (x0 + x1) / 2.0
    sy = cy - (y0 + y1) / 2.0
    centered = RecordingPen()
    if abs(sx) > 1e-6 or abs(sy) > 1e-6:
        rec.replay(TransformPen(centered, Transform(1, 0, 0, 1, sx, sy)))
    else:
        rec.replay(centered)
    t = variant_transform(
        target_upem,
        rot90_quarters=rot90_quarters,
        flip_x=flip_x,
        flip_y=flip_y,
        center=(cx, cy),
    )
    det = t.xx * t.yy - t.xy * t.yx
    glyph = apply_transform(centered, t, reverse_winding=det < 0)
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    # Asymmetric shapes can drift slightly after orthogonal maps.
    glyph = center_glyph_in_cell(glyph, target_upem, center=(cx, cy))
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
