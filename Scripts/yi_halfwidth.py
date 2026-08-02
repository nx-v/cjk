"""Yi standalone / compound glyph transforms (NuosuSIL).

Encoding
--------
* One font per Yi syllable/radical, **named by its standalone code point**
  (e.g. ``A000.ttf`` for U+A000).
* Standalone / compounds: fit into the target box by **shifting outline
  points** independently on X and Y (linear remap of each axis).
* Compounds: ordered pairs ``(this, j)`` as flattened merged outlines,
  cmap'd at ``U+40000+j`` (half-cells are build-only, not emitted).
* Variants: the 8 unique square symmetries (D4) — 90° rotations and
  axis reflections — each with its own VS (``U+E000``..``U+E007``).
  Geometric duplicates are omitted (e.g. ``mxy === r180``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

try:
    from kage.mapping import D4_MODES, MirrorVS
except ImportError:  # Scripts.* import style
    from Scripts.kage.mapping import D4_MODES, MirrorVS

# ---------- Constants ----------

YI_SYLLABLES = (0xA000, 0xA48C)
YI_RADICALS = (0xA490, 0xA4CF)

HALFWIDTH_BASE = 0x40000

VS_BASE = 0xE000
VS_COUNT = MirrorVS.MODE_COUNT
VS_LAST = VS_BASE + VS_COUNT - 1  # U+E007

DEFAULT_UPEM = 1000
STANDALONE_PAD = 0.06
HALFWIDTH_PAD = 0.06
COMPOUND_PAD = 0.04

NUOSU_FILENAME = "NuosuSIL-Regular.ttf"

Bounds = Tuple[float, float, float, float]
GlyphMetrics = Tuple[TTGlyph, int, int]

# (vs_cp, rot90_quarters, flip_x, flip_y, name_suffix or None for identity)
# Shared with build_subfonts / GlyphWiki via kage.mapping.D4_MODES.
TransformMode = Tuple[int, int, bool, bool, Optional[str]]

TRANSFORM_MODES: List[TransformMode] = [
    (MirrorVS.codepoint(mode), rot, fx, fy, suffix)
    for mode, rot, fx, fy, suffix in D4_MODES
]


def vs_glyph_name(vs_cp: int) -> str:
    return f"vs{vs_cp - VS_BASE + 1:02d}"


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
    """D4 map that sends ``center`` (default: em midpoint) to the em midpoint."""
    if rot90_quarters % 4 == 0 and not flip_x and not flip_y:
        return Transform()
    m = variant_matrix(rot90_quarters=rot90_quarters, flip_x=flip_x, flip_y=flip_y)
    (xx, xy), (yx, yy) = m
    cell = target_upem / 2.0
    cx, cy = center if center is not None else (cell, cell)
    # p' = M·(p - c) + cell_center
    dx = cell - xx * cx - yx * cy
    dy = cell - xy * cx - yy * cy
    return Transform(xx, xy, yx, yy, dx, dy)


def center_glyph_in_cell(
    glyph: TTGlyph,
    target_upem: int,
) -> TTGlyph:
    """Translate ``glyph`` so its bbox center sits at the em midpoint."""
    try:
        glyph.recalcBounds(None)
        x_min, y_min, x_max, y_max = glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax
    except Exception:
        return glyph
    cell = target_upem / 2.0
    sx = cell - (x_min + x_max) / 2.0
    sy = cell - (y_min + y_max) / 2.0
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

    def hw_cp(self, index: int) -> int:
        return HALFWIDTH_BASE + index

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
    """Axis-shift fit into the full CJK em."""
    del stroke_weight
    inner = target_upem * (1.0 - 2.0 * pad)
    glyph = _axis_shift_fit(
        rec,
        target_w=inner,
        target_h=inner,
        center_x=target_upem / 2.0,
        center_y=target_upem / 2.0,
    )
    if glyph is None:
        return None
    return glyph, target_upem, int(glyph.xMin)


def make_halfwidth_glyph(
    rec: RecordingPen,
    target_upem: int = DEFAULT_UPEM,
    *,
    pad: float = HALFWIDTH_PAD,
    stroke_weight: Optional[float] = None,  # unused; kept for call-site compat
) -> Optional[GlyphMetrics]:
    """Axis-shift fit into a half-em cell."""
    del stroke_weight
    adv = target_upem // 2
    glyph = _axis_shift_fit(
        rec,
        target_w=adv * (1.0 - 2.0 * pad),
        target_h=target_upem * (1.0 - 2.0 * pad),
        center_x=adv / 2.0,
        center_y=target_upem / 2.0,
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

    ga = _axis_shift_fit(
        rec_a,
        target_w=cell_w,
        target_h=cell_h,
        center_x=half / 2.0,
        center_y=target_upem / 2.0,
    )
    gb = _axis_shift_fit(
        rec_b,
        target_w=cell_w,
        target_h=cell_h,
        center_x=half / 2.0,  # left-slot; merge shifts right
        center_y=target_upem / 2.0,
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
    """Center the glyph, then apply a D4 rotation/reflection about em mid."""
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    cell = target_upem / 2.0
    # Center first so rotations/reflections orbit the em midpoint.
    sx = cell - (x0 + x1) / 2.0
    sy = cell - (y0 + y1) / 2.0
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
    )
    det = t.xx * t.yy - t.xy * t.yx
    glyph = apply_transform(centered, t, reverse_winding=det < 0)
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    # Asymmetric shapes can drift slightly after orthogonal maps.
    glyph = center_glyph_in_cell(glyph, target_upem)
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
