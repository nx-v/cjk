"""Reading marks for Pan-CJK subfonts.

Core marks from Plangothic P2: U+16FF0 (ca) / U+16FF1 (nhay) only.

Base face (`edenia cjk`)
--------------------------
**ca** (U+16FF0) sits in a **1/3** segment; the CJK outline occupies **2/3**.
**nhay** (U+16FF1) sits in a **1/4** segment; the CJK outline occupies **3/4**.
FE00–FE0F on the **clipped CJK** select mark position × axis-mirror
(Klein four-group only — no r90 / r270)::

    FE00  right, upright (no-op: same as bare MARK)
    FE01  right, mx
    FE02  right, my
    FE03  right, mxy / r180
    FE04–FE07  left  (id / mx / my / mxy)
    FE08–FE0B  up    (id / mx / my / mxy) — mark is r90 of LR upright
    FE0C–FE0F  down  (id / mx / my / mxy) — same `.T` outlines

    CJK  MARK (nhay)       → `base.dk_u16FF1`          (right, upright)
    CJK  MARK (ca)         → `base.dk.ca_u16FF0`       (right, upright)
    CJK  FE00  MARK        → same (explicit no-op)
    CJK  FE01  MARK        → `…_MARK.mx`
    CJK  FE08  MARK        → `base.dkt_MARK`           (up, upright)
    CJK  FE0C  MARK        → `base.dkb_MARK`           (down, upright)

Half face (`edenia cjk h`)
----------------------------
Half-cell segments are **slices** of already-baked fullwidth outlines.
`FE00` overlays; `FE08`–`FE0F` are halves / triangles. CJK D4 stays
on `FE01`–`FE07` (BMP PUA is edenia kana). Digraphs::

    A  FE08  FE00  B  FE09   →  A.top.ov + B.bot
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables as ot
from fontTools.ttLib.tables._g_l_y_f import (
    ROUND_XY_TO_GRID,
    UNSCALED_COMPONENT_OFFSET,
    USE_MY_METRICS,
    GlyphComponent,
)
from fontTools.ttLib.tables._g_l_y_f import (
    Glyph as TTGlyph,
)

from cape_weightor import (
    apply_height,
    apply_width,
    layer_from_ttglyph,
    ttglyph_from_layer,
)
from shared_cells import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    HALF_PLANE_INF_FRAC,
    OV_SELECTOR_CP,
    OV_SELECTOR_NAME,
    SLICE_LABELS,
    SLICE_PUA_CPS,
    TRANSFORM_MODES,
    UPRIGHT_COMPOSITE_SUFFIXES,
    _recording_from_glyph,
    add_overlay_forms,
    apply_transform,
    boolean_subtract_glyphs,
    boolean_subtract_named,
    build_chunked_ligature_subst_lookup,
    clip_glyph_to_polygon,
    clip_glyph_to_rect,
    empty_glyph,
    ideographic_bounds,
    install_derived_glyph,
    make_composite_variant,
    make_segment_slice_glyph,
    metrics_for_glyph,
    orientation_form_names,
    overlay_glyph_name,
    propagate_d4_segments,
    recording_bounds,
    triangle_clip_points,
    variant_glyph_name,
    vs_glyph_name,
)

PLANGOTHIC_P2_FILENAME = "PlangothicP2-Regular.ttf"
# Plangothic reading marks (always included when the face is present).
CA_MARK_CP = 0x16FF0
NHAY_MARK_CP = 0x16FF1
CORE_MARK_CPS: Tuple[int, ...] = (CA_MARK_CP, NHAY_MARK_CP)
# Runtime: ca/nhay only (updated by `prepare_marks`).
MARK_CPS: Tuple[int, ...] = CORE_MARK_CPS
CA_MARK_SEGMENT_FRAC = 1.0 / 3.0
NHAY_MARK_SEGMENT_FRAC = 1.0 / 4.0
CA_MARK_BASE_FRAC = 1.0 - CA_MARK_SEGMENT_FRAC
NHAY_MARK_BASE_FRAC = 1.0 - NHAY_MARK_SEGMENT_FRAC
MARK_SEGMENT_FRAC_BY_CP: Dict[int, float] = {
    CA_MARK_CP: CA_MARK_SEGMENT_FRAC,
    NHAY_MARK_CP: NHAY_MARK_SEGMENT_FRAC,
}
MARK_BASE_FRAC_BY_CP: Dict[int, float] = {
    CA_MARK_CP: CA_MARK_BASE_FRAC,
    NHAY_MARK_CP: NHAY_MARK_BASE_FRAC,
}
# nhay keeps plain `.dk` names; ca uses `.dk.ca` (and matching siblings).
CA_MARK_SQUISH_TAG = ".ca"
NHAY_MARK_SQUISH_TAG = ""
# Legacy aliases (nhay fractions).
MARK_SEGMENT_FRAC = NHAY_MARK_SEGMENT_FRAC
MARK_BASE_SQUISH_FACTOR = NHAY_MARK_BASE_FRAC
# Overlay + combining slices on the **h** face (FE00, FE08–FE0F).
# Geometric selector names match shared_cells; .dk* suffixes stay
# for occupancy clips (h = 1/2; base ca = 2/3, nhay = 3/4).
SQUISH_TOP_CP = 0xFE08
SQUISH_TOP_NAME = "vsTop"
SQUISH_BOT_CP = 0xFE09
SQUISH_BOT_NAME = "vsBot"
SQUISH_LEFT_CP = 0xFE0A
SQUISH_LEFT_NAME = "vsLeft"
SQUISH_RIGHT_CP = 0xFE0B
SQUISH_RIGHT_NAME = "vsRight"
SQUISH_TL_CP = 0xFE0C
SQUISH_TL_NAME = "vsTL"
SQUISH_BR_CP = 0xFE0D
SQUISH_BR_NAME = "vsBR"
SQUISH_TR_CP = 0xFE0E
SQUISH_TR_NAME = "vsTR"
SQUISH_BL_CP = 0xFE0F
SQUISH_BL_NAME = "vsBL"
# PUA mirrors of FE00 / FE08–FE0F (Blink drops unlisted Default_Ignorables).
SQUISH_PUA_CPS: Tuple[int, ...] = SLICE_PUA_CPS
SIDE_SELECTOR_CPS: Tuple[int, ...] = (
    OV_SELECTOR_CP,
    SQUISH_TOP_CP,
    SQUISH_BOT_CP,
    SQUISH_LEFT_CP,
    SQUISH_RIGHT_CP,
    SQUISH_TL_CP,
    SQUISH_BR_CP,
    SQUISH_TR_CP,
    SQUISH_BL_CP,
)
# Halves keep .dk* names (mark placement); triangles use geometric suffixes.
SQUISH_VS_SLOTS: Tuple[Tuple[int, str, str], ...] = (
    (SQUISH_LEFT_CP, SQUISH_LEFT_NAME, "dk"),
    (SQUISH_RIGHT_CP, SQUISH_RIGHT_NAME, "dkl"),
    (SQUISH_TOP_CP, SQUISH_TOP_NAME, "dkb"),
    (SQUISH_BOT_CP, SQUISH_BOT_NAME, "dkt"),
    (SQUISH_TL_CP, SQUISH_TL_NAME, "tl"),
    (SQUISH_BR_CP, SQUISH_BR_NAME, "br"),
    (SQUISH_TR_CP, SQUISH_TR_NAME, "tr"),
    (SQUISH_BL_CP, SQUISH_BL_NAME, "bl"),
)
# Base-face ca/nhay: FE00–FE0F = 4 positions × {id, mx, my, mxy}.
# (cp, selector name, mark position, mirror suffix or None)
MarkSlot = Tuple[int, str, str, Optional[str]]
_MARK_MIRRORS: Tuple[Optional[str], ...] = (None, "mx", "my", "r180")
_MARK_POSITIONS: Tuple[Tuple[str, str], ...] = (
    ("right", "R"),
    ("left", "L"),
    ("up", "U"),
    ("down", "D"),
)
MARK_SLOT_VS: Tuple[MarkSlot, ...] = tuple(
    (
        0xFE00 + pos_i * 4 + mir_i,
        f"vsMk{pos_code}" + ("" if mir is None else mir),
        pos,
        mir,
    )
    for pos_i, (pos, pos_code) in enumerate(_MARK_POSITIONS)
    for mir_i, mir in enumerate(_MARK_MIRRORS)
)
MARK_SLOT_PUA_BASE = 0xE008
MARK_POS_SEGMENT: Dict[str, str] = {
    "right": "dk",
    "left": "dkl",
    "up": "dkt",
    "down": "dkb",
}

# Full D4 (identity + VS02..VS08 / FE01..FE07), including r90my.
BASE_VS_MODE_COUNT = 8
# Fallback when mark size is unknown; normally computed from mark ink.
# Slightly over half so digraph halves meet with less middle gutter
# (exact 0.5 + half-pad left a wide TB seam).
SQUISH_FACTOR = 0.55
HALF_PAD_FRAC = 0.02  # inset inside the occupied half (was 0.04)


def mark_squish_tag(mark_cp: int) -> str:
    """Segment name suffix: nhay `""`, ca `".ca"`."""
    return CA_MARK_SQUISH_TAG if mark_cp == CA_MARK_CP else NHAY_MARK_SQUISH_TAG


def mark_segment_frac_for(mark_cp: int) -> float:
    return MARK_SEGMENT_FRAC_BY_CP[mark_cp]


def mark_base_frac_for(mark_cp: int) -> float:
    return MARK_BASE_FRAC_BY_CP[mark_cp]


def resolve_plangothic_p2(in_dir: str) -> str:
    path = os.path.join(in_dir, PLANGOTHIC_P2_FILENAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing Plangothic P2: {path}")
    return path


def squish_name(base_name: str, tag: str = "") -> str:
    """Left half-slice (right segment free) — clip of upright id."""
    return f"{base_name}.dk{tag}"


def squish_left_name(base_name: str, tag: str = "") -> str:
    """Right half-slice (left segment free); clip of upright id."""
    return f"{base_name}.dkl{tag}"


def squish_top_name(base_name: str, tag: str = "") -> str:
    """Bottom half-slice (top segment free); clip of upright id."""
    return f"{base_name}.dkt{tag}"


def squish_bot_name(base_name: str, tag: str = "") -> str:
    """Top half-slice (bottom segment free); clip of upright id."""
    return f"{base_name}.dkb{tag}"


def left_mark_name(mark_name: str) -> str:
    return f"{mark_name}.L"


def top_mark_name(mark_name: str) -> str:
    return f"{mark_name}.T"


def bottom_mark_name(mark_name: str) -> str:
    return f"{mark_name}.B"


def make_tb_mark_glyph(
    glyph: TTGlyph,
    *,
    target_upem: int,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    base_name: str = "_tb",
) -> Tuple[TTGlyph, int]:
    """TB mark = r90 of LR-fitted upright (pure rotation about origin).

    Upright ca/nhay already fills the LR half; 90° maps that box onto the TB
    half, so no extra stretch/normalize. `.B` / D4 TB aliases composite this.
    """
    rotated, _adv, _lsb = make_composite_variant(
        base_name,
        target_upem,
        rot90_quarters=1,
        advance=0,
        lsb=0,
        base_glyph=glyph,
        glyph_set=glyph_set,
        center=(0.0, 0.0),
        allow_2x2=False,
    )
    try:
        rotated.recalcBounds(None)
        return rotated, int(rotated.xMin)
    except Exception:
        return rotated, 0


def add_mark_mirror_composites(
    base_name: str,
    *,
    target_upem: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
) -> List[str]:
    """Install mx / my / r180 of a ca/nhay mark (no rotation)."""
    installed: List[str] = []
    adv, lsb = metrics[base_name]
    for _vs, rot, fx, fy, suffix in TRANSFORM_MODES:
        if suffix not in UPRIGHT_COMPOSITE_SUFFIXES:
            continue
        m_name = variant_glyph_name(base_name, suffix)
        if m_name in glyphs:
            installed.append(m_name)
            continue
        g, _a, l = make_composite_variant(
            base_name,
            target_upem,
            rot90_quarters=rot,
            flip_x=fx,
            flip_y=fy,
            advance=adv,
            lsb=lsb,
            base_glyph=glyphs[base_name],
            glyph_set=glyphs,
            center=(0.0, 0.0),
            allow_2x2=False,
        )
        glyph_order.append(m_name)
        glyphs[m_name] = g
        metrics[m_name] = (0, int(l))
        installed.append(m_name)
    return installed


def base_orientation_modes(
    modes: Optional[Sequence] = None,
) -> List:
    use = list(modes) if modes is not None else list(TRANSFORM_MODES)
    return use[:BASE_VS_MODE_COUNT]


def squishable_forms(
    cjk_bases: Sequence[str],
    *,
    modes=None,
) -> List[str]:
    """Identity + all D4 forms (VS01..VS08) that may take a reading mark."""
    names: List[str] = []
    for base in cjk_bases:
        names.extend(orientation_form_names(base, modes=base_orientation_modes(modes)))
    return names


def glyph_name_for_cp(cp: int) -> str:
    return f"u{cp:04X}" if cp <= 0xFFFF else f"u{cp:05X}"


def _bake_simple_glyph(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]],
) -> TTGlyph:
    if not glyph.isComposite():
        return glyph

    rec = _recording_from_glyph(glyph, glyph_set)
    pen = TTGlyphPen(None)
    rec.replay(pen)
    out = pen.glyph()
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


def _signed_area(glyph: TTGlyph) -> float:
    try:
        glyph.recalcBounds(None)
        coords = glyph.coordinates
    except Exception:
        return 0.0
    if not coords or not glyph.endPtsOfContours:
        return 0.0
    total = 0.0
    start = 0
    for end in glyph.endPtsOfContours:
        pts = [coords[j] for j in range(start, end + 1)]
        start = end + 1
        if len(pts) < 3:
            continue
        a = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            a += x1 * y2 - x2 * y1
        total += a
    return total * 0.5


def _normalize_winding(
    glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]] = None
) -> TTGlyph:
    """Ensure outer-CCW winding so Width-mode stem offset expands fill.

    Axis mirrors (`mx` / `my`) reverse contour orientation; without this,
    CAPE `apply_width` thins verticals instead of restoring them.
    """
    if glyph.isComposite():
        glyph = _bake_simple_glyph(glyph, glyph_set)
    if _signed_area(glyph) >= 0:
        return glyph
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.reverseContourPen import ReverseContourPen

    rec = RecordingPen()
    rev = ReverseContourPen(rec)
    try:
        glyph.draw(rev, glyph_set)
    except TypeError:
        glyph.draw(rev)
    pen = TTGlyphPen(None)
    rec.replay(pen)
    out = pen.glyph()
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


def make_mark_glyph(
    rec: RecordingPen,
    *,
    scale: float,
) -> Optional[TTGlyph]:
    """Scale mark outline and pin ink center to `(0, 0)` (GPOS attach)."""
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0 or scale <= 0:
        return None
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    t = Transform(scale, 0, 0, scale, -scale * cx, -scale * cy)
    glyph = apply_transform(rec, t)
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    return glyph


def load_core_marks(
    plangothic_path: str,
    target_upem: int,
    *,
    local_scale: float = 0.96,
    mark_cps: Sequence[int] = CORE_MARK_CPS,
) -> Tuple[List[int], Dict[int, TTGlyph]]:
    """Return `(codepoints, cp → zero-origin mark glyph)` from Plangothic P2."""
    tt = TTFont(plangothic_path, fontNumber=0)
    try:
        cmap: Dict[int, str] = {}
        for table in tt["cmap"].tables:
            if table.isUnicode():
                cmap.update(table.cmap)
        glyph_set = tt.getGlyphSet()
        src_upem = float(tt["head"].unitsPerEm)
        scale = (
            (float(target_upem) / src_upem) * float(local_scale) if src_upem else 1.0
        )

        cps: List[int] = []
        glyphs: Dict[int, TTGlyph] = {}
        for cp in mark_cps:
            gname = cmap.get(cp)
            if gname is None:
                continue
            rec = DecomposingRecordingPen(glyph_set)
            try:
                glyph_set[gname].draw(rec)
            except Exception:
                continue
            mark = make_mark_glyph(rec, scale=scale)
            if mark is None:
                continue
            cps.append(cp)
            glyphs[cp] = mark
        return cps, glyphs
    finally:
        tt.close()


def set_mark_cps(cps: Sequence[int]) -> Tuple[int, ...]:
    """Update module-level `MARK_CPS` (used by CSS unicode-range)."""
    global MARK_CPS
    MARK_CPS = tuple(cps)
    return MARK_CPS


def mark_attach_anchor(
    glyph: TTGlyph,
    *,
    side: str = "right",
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[int, int]:
    """Mark attach point at ink center (pairs with free-half-center base anchors).

    Edge attach previously pushed marks outside the ideographic cell when the
    base anchor was the free-half center.
    """
    del side  # class comes from mark form; attach is always ink center
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        cx = (float(glyph.xMin) + float(glyph.xMax)) / 2.0
        cy = (float(glyph.yMin) + float(glyph.yMax)) / 2.0
        return otRound(cx), otRound(cy)
    except Exception:
        return 0, 0


def add_mark_glyphs(
    mark_cps: Sequence[int],
    mark_glyphs: Dict[int, TTGlyph],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int,
    tb_glyphs: Optional[Dict[int, TTGlyph]] = None,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Install LR-fitted ca/nhay + mx/my/r180, and a TB `.T` family.

    `tb_glyphs` are origin-centered marks for up/down (r90 of the LR-fitted
    upright — not Height-stretched). Mirrors are applied *after* that rotation.
    Up and down slots share those TB outlines and differ only in composite
    offset. Returns `(right, left, top, bottom)` name lists for compat callers.
    """
    upright: List[str] = []
    for cp in mark_cps:
        g = mark_glyphs.get(cp)
        if g is None:
            continue
        name = glyph_name_for_cp(cp)
        if name not in glyphs:
            glyph_order.append(name)
            glyphs[name] = g
            try:
                g.recalcBounds(None)
                lsb = int(g.xMin)
            except Exception:
                lsb = 0
            metrics[name] = (0, lsb)
        cmap[cp] = name
        upright.append(name)
        add_mark_mirror_composites(
            name,
            target_upem=target_upem,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )

    top_names: List[str] = []
    for name, cp in ((glyph_name_for_cp(cp), cp) for cp in mark_cps):
        if name not in glyphs:
            continue
        tn = top_mark_name(name)
        if tn not in glyphs:
            if tb_glyphs is not None and cp in tb_glyphs:
                src = tb_glyphs[cp]
            else:
                src, _lsb = make_tb_mark_glyph(
                    glyphs[name],
                    target_upem=target_upem,
                    glyph_set=glyphs,
                    base_name=name,
                )
            try:
                src.recalcBounds(None)
                lsb = int(src.xMin)
            except Exception:
                lsb = 0
            glyph_order.append(tn)
            glyphs[tn] = src
            metrics[tn] = (0, lsb)
        add_mark_mirror_composites(
            tn,
            target_upem=target_upem,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
        top_names.append(tn)

    right_names = list(upright)
    left_names = list(upright)
    bottom_names = list(top_names)
    return right_names, left_names, top_names, bottom_names


def fit_mark_to_halfcell(
    glyph: TTGlyph,
    target_upem: int,
    *,
    axis: str = "x",
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    segment_frac: float = 0.5,
) -> TTGlyph:
    """Fit mark into one segment with uniform CAPE Width/Height.

    `axis="x"` → LR segment (`segment_frac` × width × full ideo height).
    `axis="y"` → TB segment (full width × `segment_frac` × ideo height).
    Ink is centered at the origin for GPOS attachment.
    Default `segment_frac=0.5` (half-cell); ca uses `1/3`, nhay `1/4`.
    """
    pin = "right" if axis == "x" else "bottom"
    src = _normalize_winding(_bake_simple_glyph(glyph, glyph_set), glyph_set)
    layer = layer_from_ttglyph(src, 0.0)
    if not layer.paths:
        return src

    x0, y0, x1, y1 = _half_slot_rect(
        float(target_upem), pin=pin, axis=axis, segment_frac=segment_frac
    )
    tw = max(x1 - x0, 1.0)
    th = max(y1 - y0, 1.0)
    b = layer.bounds
    bw = max(b.size.x, 1.0)
    bh = max(b.size.y, 1.0)
    fx = tw / bw
    fy = th / bh
    # Uniform scale only — no stem compensation.
    if abs(fx - 1.0) > 1e-4:
        apply_width(layer, fx, stem=0.0, center_x=0.0)
    if abs(fy - 1.0) > 1e-4:
        apply_height(layer, fy, stem=0.0, center_y=0.0)
    b = layer.bounds
    cx = b.origin.x + 0.5 * b.size.x
    cy = b.origin.y + 0.5 * b.size.y
    if abs(cx) > 1e-6 or abs(cy) > 1e-6:
        layer.applyTransform((1, 0, 0, 1, -cx, -cy))
    out, _a, _l = ttglyph_from_layer(layer)
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


def _half_slot_rect(
    target_upem: float,
    *,
    pin: str,
    axis: str,
    segment_frac: float = 0.5,
) -> Tuple[float, float, float, float]:
    """Return `(x0, y0, x1, y1)` for the occupied segment slot.

    `segment_frac` is the fraction of the cell the segment occupies (0.5 half,
    `1/3` ca / `2/3` base, `1/4` nhay / `3/4` base).
    """
    bot, top, _ = ideographic_bounds(int(target_upem))
    pad = target_upem * HALF_PAD_FRAC
    frac = float(segment_frac)
    match (axis, pin):
        case ("y", "top"):
            span = top - bot
            y0, y1 = top - span * frac + pad, top - pad
            return pad, y0, target_upem - pad, y1
        case ("y", _):
            span = top - bot
            y0, y1 = bot + pad, bot + span * frac - pad
            return pad, y0, target_upem - pad, y1
        case (_, "right"):
            x0, x1 = target_upem * (1.0 - frac) + pad, target_upem - pad
            return x0, bot + pad, x1, top - pad
        case _:
            x0, x1 = pad, target_upem * frac - pad
            return x0, bot + pad, x1, top - pad


def _occupied_plane_rect(
    target_upem: float,
    *,
    pin: str,
    axis: str,
    segment_frac: float = 0.5,
) -> Tuple[float, float, float, float]:
    """Half-plane covering the `pin` side; cut at `frac`, pad only outward.

    Used as the seed clip so the complementary segment can be `full − seed`
    without leftover slivers from inset padding on the cut.
    """
    bot, top, _ = ideographic_bounds(int(target_upem))
    inf = target_upem * HALF_PLANE_INF_FRAC
    frac = float(segment_frac)
    match (axis, pin):
        case ("y", "top"):
            span = top - bot
            cut = top - span * frac
            return -inf, cut, inf, inf
        case ("y", _):
            span = top - bot
            cut = bot + span * frac
            return -inf, -inf, inf, cut
        case (_, "right"):
            cut = target_upem * (1.0 - frac)
            return cut, -inf, inf, inf
        case _:
            cut = target_upem * frac
            return -inf, -inf, cut, inf


def place_glyph_in_half(
    glyph: TTGlyph,
    advance: int,
    *,
    pin: str = "left",
    axis: str = "x",
    target_upem: int = 1000,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    slot_frac: float = 0.5,
) -> Tuple[TTGlyph, int, int]:
    """Clip `glyph` to one segment slot (slice — no stretch / squish).

    Prefer `make_squished_glyph` when the upright segment can be built from a
    named base in `glyph_set`.
    """
    upem = float(target_upem)
    rect = _half_slot_rect(upem, pin=pin, axis=axis, segment_frac=slot_frac)
    clipped = clip_glyph_to_rect(glyph, rect, glyph_set=glyph_set)
    try:
        clipped.recalcBounds(None)
        lsb = int(clipped.xMin)
    except Exception:
        lsb = 0
    del advance
    return clipped, int(upem), lsb


def _translate_ink_to_half_center(
    glyph: TTGlyph,
    *,
    pin: str,
    axis: str,
    target_upem: int,
    slot_frac: float = 0.5,
) -> Tuple[TTGlyph, int, int]:
    """Translate only so ink center sits at the segment-slot center (no re-scale)."""
    upem = float(target_upem)
    x0, y0, x1, y1 = _half_slot_rect(upem, pin=pin, axis=axis, segment_frac=slot_frac)
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


def make_squished_glyph(
    base_name: str,
    advance: int,
    *,
    factor: float = SQUISH_FACTOR,
    pin: str = "left",
    axis: str = "x",
    target_upem: Optional[int] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    slot_frac: Optional[float] = None,
) -> Tuple[TTGlyph, int, int]:
    """Upright segment as a **slice** of `base_name` (clip; no stretch).

    `slot_frac` is the segment band width (0.5 half-cell, 0.75 mark-base, …).
    `factor` is kept for call-site compatibility and ignored (no scale).
    """
    if glyph_set is None:
        raise ValueError("make_squished_glyph requires glyph_set for slice bake")
    upem = int(
        target_upem if target_upem is not None else (advance if advance > 0 else 1000)
    )
    del factor
    occ = float(slot_frac) if slot_frac is not None else 0.5
    rect = _occupied_plane_rect(float(upem), pin=pin, axis=axis, segment_frac=occ)
    return make_segment_slice_glyph(
        base_name,
        advance=int(advance if advance > 0 else upem),
        rect=rect,
        glyph_set=glyph_set,
    )


def add_squish_forms(
    base_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    width_factor: float = SQUISH_FACTOR,
    height_factor: float = SQUISH_FACTOR,
    target_upem: int = 1000,
    slot_frac: Optional[float] = None,
    name_tag: str = "",
) -> List[str]:
    """Slice each identity form into the four half-cell segments.

    D4 copies of those segments are filled later by `propagate_d4_segments`
    (clip identity once, then `R(clip(g, R⁻¹(W)))`). Clip one side per
    axis; the opposite is `full − that side` (or, for a 3/4 mark-base
    slot, `full − the complementary sliver`).
    """
    # Half-cell digraphs keep a 0.5 slot; mark-base passes slot_frac (2/3 ca, 3/4 nhay).
    occ_x = float(slot_frac) if slot_frac is not None else 0.5
    occ_y = float(slot_frac) if slot_frac is not None else 0.5
    del width_factor, height_factor
    tag = str(name_tag)

    added: List[str] = []
    for name in base_names:
        if name not in glyphs:
            continue
        adv, _lsb = metrics.get(name, (target_upem, 0))
        left_n = squish_name(name, tag)
        right_n = squish_left_name(name, tag)
        bot_n = squish_top_name(name, tag)
        top_n = squish_bot_name(name, tag)

        def _put(out_name: str, gm: Tuple[TTGlyph, int, int]) -> None:
            install_derived_glyph(
                out_name,
                gm,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
            )

        def _minus_sliver(pin: str, axis: str, sliver_frac: float):
            rect = _occupied_plane_rect(
                float(target_upem), pin=pin, axis=axis, segment_frac=sliver_frac
            )
            cut, _, _ = make_segment_slice_glyph(
                name, advance=adv, rect=rect, glyph_set=glyphs
            )
            return metrics_for_glyph(
                boolean_subtract_glyphs(glyphs[name], cut, glyph_set=glyphs),
                adv,
            )

        if abs(occ_x - 0.5) < 1e-9:
            if left_n not in glyphs:
                _put(
                    left_n,
                    make_squished_glyph(
                        name,
                        adv,
                        glyph_set=glyphs,
                        pin="left",
                        axis="x",
                        target_upem=target_upem,
                        slot_frac=occ_x,
                    ),
                )
            if right_n not in glyphs:
                _put(
                    right_n,
                    boolean_subtract_named(
                        name,
                        left_n,
                        glyphs=glyphs,
                        metrics=metrics,
                        advance=adv,
                    ),
                )
        else:
            sliver = max(0.0, 1.0 - occ_x)
            if left_n not in glyphs:
                _put(left_n, _minus_sliver("right", "x", sliver))
            if right_n not in glyphs:
                _put(right_n, _minus_sliver("left", "x", sliver))

        if abs(occ_y - 0.5) < 1e-9:
            if top_n not in glyphs:
                _put(
                    top_n,
                    make_squished_glyph(
                        name,
                        adv,
                        glyph_set=glyphs,
                        pin="top",
                        axis="y",
                        target_upem=target_upem,
                        slot_frac=occ_y,
                    ),
                )
            if bot_n not in glyphs:
                _put(
                    bot_n,
                    boolean_subtract_named(
                        name,
                        top_n,
                        glyphs=glyphs,
                        metrics=metrics,
                        advance=adv,
                    ),
                )
        else:
            sliver = max(0.0, 1.0 - occ_y)
            if top_n not in glyphs:
                _put(top_n, _minus_sliver("bottom", "y", sliver))
            if bot_n not in glyphs:
                _put(bot_n, _minus_sliver("top", "y", sliver))

        if abs(occ_x - 0.5) < 1e-9 and abs(occ_y - 0.5) < 1e-9:
            inf = float(target_upem) * HALF_PLANE_INF_FRAC
            x0, y0, x1, y1 = 0.0, 0.0, float(target_upem), float(target_upem)
            bot, top, _ = ideographic_bounds(target_upem)
            y0, y1 = bot, top
            for first, second in (("tl", "br"), ("tr", "bl")):
                n1 = f"{name}.{first}"
                if n1 not in glyphs:
                    pts = triangle_clip_points(
                        first, x0=x0, y0=y0, x1=x1, y1=y1, inf=inf
                    )
                    clipped = clip_glyph_to_polygon(glyphs[name], pts, glyph_set=glyphs)
                    try:
                        clipped.recalcBounds(None)
                        lsb = int(clipped.xMin)
                    except Exception:
                        lsb = 0
                    _put(n1, (clipped, int(adv), lsb))
                n2 = f"{name}.{second}"
                if n2 not in glyphs:
                    _put(
                        n2,
                        boolean_subtract_named(
                            name,
                            n1,
                            glyphs=glyphs,
                            metrics=metrics,
                            advance=adv,
                        ),
                    )
        added.append(name)
    return added


def cjk_right_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    segment_frac: float = 0.5,
) -> Tuple[int, int]:
    """Center of the free right segment (mark sits here)."""
    del glyph, glyph_set
    bot, top, _ = ideographic_bounds(target_upem)
    right = float(advance) if advance > 0 else float(target_upem)
    return otRound(right * (1.0 - segment_frac / 2.0)), otRound((bot + top) / 2.0)


def cjk_left_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    segment_frac: float = 0.5,
) -> Tuple[int, int]:
    """Center of the free left segment (mark sits here)."""
    del glyph, glyph_set, advance
    bot, top, _ = ideographic_bounds(target_upem)
    return otRound(target_upem * (segment_frac / 2.0)), otRound((bot + top) / 2.0)


def cjk_top_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    segment_frac: float = 0.5,
) -> Tuple[int, int]:
    """Center of the free top segment (mark sits here)."""
    del glyph, glyph_set
    bot, top, _ = ideographic_bounds(target_upem)
    span = top - bot
    mid_free = top - span * (segment_frac / 2.0)
    right = float(advance) if advance > 0 else float(target_upem)
    return otRound(right * 0.5), otRound(mid_free)


def cjk_bottom_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    segment_frac: float = 0.5,
) -> Tuple[int, int]:
    """Center of the free bottom segment (mark sits here)."""
    del glyph, glyph_set
    bot, top, _ = ideographic_bounds(target_upem)
    span = top - bot
    mid_free = bot + span * (segment_frac / 2.0)
    right = float(advance) if advance > 0 else float(target_upem)
    return otRound(right * 0.5), otRound(mid_free)


def marked_form_name(squish_form: str, mark_root: str) -> str:
    """Precomposed squish+mark name, e.g. `u4E00.dk_u16FF0`."""
    return f"{squish_form}_{mark_root}"


def _mark_component_for_slot(upright: str, position: str, mirror: Optional[str]) -> str:
    """LR-fitted mark for right/left; r90 `.T` for up/down; then mirror."""
    root = upright if position in ("right", "left") else top_mark_name(upright)
    if mirror is None:
        return root
    return variant_glyph_name(root, mirror)


def _segment_anchor_fn(segment_suf: str):
    match segment_suf:
        case "dk":
            return cjk_right_anchor
        case "dkl":
            return cjk_left_anchor
        case "dkt":
            return cjk_top_anchor
        case "dkb":
            return cjk_bottom_anchor
        case _:
            return cjk_right_anchor


def _segment_squish_of(
    base: str,
    segment_suf: str,
    *,
    mark_cp: Optional[int] = None,
) -> str:
    tag = mark_squish_tag(mark_cp) if mark_cp is not None else NHAY_MARK_SQUISH_TAG
    match segment_suf:
        case "dk":
            return squish_name(base, tag)
        case "dkl":
            return squish_left_name(base, tag)
        case "dkt":
            return squish_top_name(base, tag)
        case "dkb":
            return squish_bot_name(base, tag)
        case _:
            return squish_name(base, tag)


def make_marked_composite(
    squish_form: str,
    mark_name: str,
    *,
    mark_dx: int,
    mark_dy: int,
) -> TTGlyph:
    """TT composite: full-advance squish + mark offset into the free half."""
    g = TTGlyph()
    g.numberOfContours = -1
    base = GlyphComponent()
    base.glyphName = squish_form
    base.x = 0
    base.y = 0
    base.flags = USE_MY_METRICS | ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    mark = GlyphComponent()
    mark.glyphName = mark_name
    mark.x = mark_dx
    mark.y = mark_dy
    mark.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [base, mark]
    return g


# Occupancy suffixes for the four mark positions (triangles skip ca/nhay).
_MARKED_SEGMENT_SUFFIXES: Tuple[str, ...] = ("dk", "dkl", "dkb", "dkt")


def add_marked_composites(
    squishable_bases: Sequence[str],
    mark_cps: Sequence[int],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
) -> List[str]:
    """Bake precomposed CJK-clip + ca/nhay (4 positions × 4 mirrors).

    One composite per `(base × MARK_SLOT_VS × mark)`. Identity CJK only
    is typical on the base face (no CJK D4).
    """
    added: List[str] = []
    seen: set = set()
    for base in squishable_bases:
        if base not in glyphs:
            continue
        for _cp, _sel, position, mirror in MARK_SLOT_VS:
            for cp in mark_cps:
                segment_suf = MARK_POS_SEGMENT[position]
                sq = _segment_squish_of(base, segment_suf, mark_cp=cp)
                if sq not in glyphs:
                    continue
                adv, lsb = metrics.get(sq, (target_upem, 0))
                seg_frac = mark_segment_frac_for(cp)
                ax, ay = _segment_anchor_fn(segment_suf)(
                    glyphs[sq],
                    adv,
                    target_upem,
                    glyph_set=glyphs,
                    segment_frac=seg_frac,
                )
                upright = glyph_name_for_cp(cp)
                if upright not in glyphs:
                    continue
                mark_comp = _mark_component_for_slot(upright, position, mirror)
                if mark_comp not in glyphs:
                    continue
                mx, my = mark_attach_anchor(glyphs[mark_comp], glyph_set=glyphs)
                out_name = marked_form_name(sq, mark_comp)
                if out_name in glyphs:
                    if out_name not in seen:
                        added.append(out_name)
                        seen.add(out_name)
                    continue
                g = make_marked_composite(
                    sq,
                    mark_comp,
                    mark_dx=ax - mx,
                    mark_dy=ay - my,
                )
                try:
                    g.recalcBounds(glyphs)
                    out_lsb = int(g.xMin)
                except Exception:
                    out_lsb = lsb
                glyph_order.append(out_name)
                glyphs[out_name] = g
                metrics[out_name] = (adv, out_lsb)
                added.append(out_name)
                seen.add(out_name)
    return added


def marked_liga_map(
    squishable_bases: Sequence[str],
    mark_cps: Sequence[int],
    *,
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """Base-face ca/nhay ligatures: `CJK (+ FE00–FE0F) + MARK`.

    ca clips use `.dk.ca` (2/3 base); nhay uses plain `.dk` (3/4 base).
    """
    liga: Dict[Tuple[str, ...], str] = {}
    for base in squishable_bases:
        if base not in glyphs:
            continue
        for vs_cp, sel_name, position, mirror in MARK_SLOT_VS:
            if sel_name not in glyphs:
                continue
            segment_suf = MARK_POS_SEGMENT[position]
            nhay_sq = _segment_squish_of(base, segment_suf, mark_cp=NHAY_MARK_CP)
            if nhay_sq in glyphs and not (vs_cp == 0xFE00 and mirror is None):
                liga[(base, sel_name)] = nhay_sq
            for cp in mark_cps:
                sq = _segment_squish_of(base, segment_suf, mark_cp=cp)
                if sq not in glyphs:
                    continue
                upright = glyph_name_for_cp(cp)
                if upright not in glyphs:
                    continue
                mark_comp = _mark_component_for_slot(upright, position, mirror)
                out = marked_form_name(sq, mark_comp)
                if out not in glyphs:
                    continue
                liga[(base, sel_name, upright)] = out
                liga[(sq, mark_comp)] = out
                if position == "right" and mirror is None:
                    liga[(base, upright)] = out
                    liga[(sq, upright)] = out
    return liga


def _ensure_side_selector(
    cp: int,
    name: str,
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
) -> None:
    if name not in glyphs:
        glyph_order.append(name)
        glyphs[name] = empty_glyph()
        metrics[name] = (0, 0)
    cmap[cp] = name


def _squish_form_name(base_form: str, segment_suffix: str) -> str:
    return f"{base_form}.{segment_suffix}"


def squish_vs_liga_map(
    squishable_bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """Ligature map: FE00 → `.ov`; FE08–FE0F → slice; FE00+FE08–F → slice `.ov`.

    Also spells explicit identity with PUA `VS01` so
    `base E000 FE00 FE08` matches the same outputs as `base FE00 FE08`.
    """
    vs01 = vs_glyph_name(TRANSFORM_MODES[0][0])
    liga: Dict[Tuple[str, ...], str] = {}
    for form in squishable_bases:
        if form not in glyphs:
            continue
        ov = overlay_glyph_name(form)
        if ov in glyphs:
            liga[(form, OV_SELECTOR_NAME)] = ov
            liga[(form, vs01, OV_SELECTOR_NAME)] = ov
        for _cp, sel_name, suf in SQUISH_VS_SLOTS:
            sq = _squish_form_name(form, suf)
            if sq not in glyphs:
                continue
            liga[(form, sel_name)] = sq
            liga[(form, vs01, sel_name)] = sq
            sq_ov = overlay_glyph_name(sq)
            if sq_ov not in glyphs:
                continue
            # Longer sequences first in a separate lookup; map holds all.
            liga[(form, OV_SELECTOR_NAME, sel_name)] = sq_ov
            liga[(form, sel_name, OV_SELECTOR_NAME)] = sq_ov
            liga[(form, vs01, OV_SELECTOR_NAME, sel_name)] = sq_ov
            liga[(form, vs01, sel_name, OV_SELECTOR_NAME)] = sq_ov
            liga[(sq, OV_SELECTOR_NAME)] = sq_ov
    return liga


def squish_vs_liga_rules(
    squishable_bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
) -> List[str]:
    """FEA lines (compat/debug); prefer `squish_vs_liga_map` + programmatic GSUB."""
    rules: List[str] = []
    for comps, out in squish_vs_liga_map(squishable_bases, glyphs=glyphs).items():
        rules.append(f"  sub {' '.join(comps)} by {out};")
    return rules


def mark_liga_map(
    mark_cps: Sequence[int],
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """Mark D4 ligatures (PUA VS02–VS08)."""
    liga: Dict[Tuple[str, ...], str] = {}
    for cp in mark_cps:
        base = glyph_name_for_cp(cp)
        if base not in glyphs:
            continue
        for vs_cp, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            if suffix is None:
                continue
            vname = variant_glyph_name(base, suffix)
            if vname not in glyphs:
                continue
            liga[(base, vs_glyph_name(vs_cp))] = vname
    return liga


def d4_liga_map(
    bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    vs01_forms: Optional[Sequence[str]] = None,
) -> Dict[Tuple[str, ...], str]:
    """`base + vs02..vs08` (FE01–FE07) → orientation form.

    Glyph names still follow the historical VS01..VS08 scheme (`vs01` =
    identity). Identity is the bare character; optional `vs01` no-op ligas
    remain for internal forms. Access is FE* cmap only (not BMP PUA).
    """
    liga: Dict[Tuple[str, ...], str] = {}
    vs01 = vs_glyph_name(TRANSFORM_MODES[0][0])
    base_set = set(bases)
    for base in bases:
        if base not in glyphs:
            continue
        for vs_cp, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            sel = vs_glyph_name(vs_cp)
            if suffix is None:
                # Identity no-op — consumes vs01 without changing the base.
                liga[(base, sel)] = base
                continue
            vname = variant_glyph_name(base, suffix)
            if vname not in glyphs:
                continue
            liga[(base, sel)] = vname
    # vs01 no-op on squish / overlay forms.
    for form in vs01_forms or ():
        if form not in glyphs or form in base_set:
            continue
        liga[(form, vs01)] = form
    return liga


def _vs01_noop_form_names(
    squishable: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    mark_cps: Sequence[int] = (),
) -> List[str]:
    """Identity + D4 + `.dk*` + `.ov` + marked composites that accept PUA VS01."""
    out: List[str] = []
    seen: set = set()
    for form in squishable:
        for name in (
            form,
            overlay_glyph_name(form),
            *(
                n
                for _cp, _sel, suf in SQUISH_VS_SLOTS
                for n in (
                    _squish_form_name(form, suf),
                    overlay_glyph_name(_squish_form_name(form, suf)),
                )
            ),
        ):
            if name in glyphs and name not in seen:
                seen.add(name)
                out.append(name)
        for cp in mark_cps:
            upright = glyph_name_for_cp(cp)
            for segment_suf in _MARKED_SEGMENT_SUFFIXES:
                sq = _segment_squish_of(form, segment_suf, mark_cp=cp)
                marked = marked_form_name(sq, upright)
                for name in (marked, overlay_glyph_name(marked)):
                    if name in glyphs and name not in seen:
                        seen.add(name)
                        out.append(name)
    return out


def install_cjk_composition_gsub(
    font,
    *,
    cjk_bases: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
    squishable: Optional[Sequence[str]] = None,
    mark_cps: Sequence[int] = (),
) -> int:
    """Programmatic `ccmp`/`rlig`/`liga`.

    Base face (`mark_cps` set): ca/nhay position×mirror on FE00–FE0F.
    Half face: D4 + FE00 overlay + FE08–FE0F slices.
    """
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    del glyph_order  # reserved for future GID-ordered class builders
    forms = list(squishable) if squishable is not None else squishable_forms(cjk_bases)

    by_len: Dict[int, Dict[Tuple[str, ...], str]] = {}
    lookups: List = []
    if mark_cps:
        for comps, out in marked_liga_map(forms, mark_cps, glyphs=glyphs).items():
            by_len.setdefault(len(comps), {})[comps] = out
    else:
        vs01_forms = _vs01_noop_form_names(forms, glyphs=glyphs, mark_cps=())
        d4_map = d4_liga_map(cjk_bases, glyphs=glyphs, vs01_forms=vs01_forms)
        if d4_map:
            lookups.append(build_chunked_ligature_subst_lookup(d4_map))
        for comps, out in squish_vs_liga_map(forms, glyphs=glyphs).items():
            by_len.setdefault(len(comps), {})[comps] = out
    for length in sorted(by_len.keys(), reverse=True):
        lookups.append(build_chunked_ligature_subst_lookup(by_len[length]))
    if not lookups:
        return 0

    def _langsys() -> ot.DefaultLangSys:
        ls = ot.DefaultLangSys()
        ls.ReqFeatureIndex = 0xFFFF
        ls.FeatureCount = len(COMPOSITION_FEATURE_TAGS)
        ls.FeatureIndex = list(range(len(COMPOSITION_FEATURE_TAGS)))
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
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        rec.Script.DefaultLangSys = _langsys()
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gsub.ScriptList.ScriptRecord.append(rec)
    gsub.ScriptList.ScriptCount = len(script_tags)

    feature_indices = list(range(len(lookups)))
    gsub.FeatureList = ot.FeatureList()
    gsub.FeatureList.FeatureRecord = []
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = ot.FeatureRecord()
        fr.FeatureTag = tag
        fr.Feature = ot.Feature()
        fr.Feature.FeatureParams = None
        fr.Feature.LookupCount = len(feature_indices)
        fr.Feature.LookupListIndex = list(feature_indices)
        gsub.FeatureList.FeatureRecord.append(fr)
    gsub.FeatureList.FeatureCount = len(COMPOSITION_FEATURE_TAGS)

    gsub.LookupList = ot.LookupList()
    gsub.LookupList.Lookup = lookups
    gsub.LookupList.LookupCount = len(lookups)

    table = newTable("GSUB")
    table.table = gsub
    font["GSUB"] = table
    return len(lookups)


def build_squish_vs_uvs_entries(
    base_cp: int,
    base_glyph: str,
    *,
    glyphs: Dict[str, TTGlyph],
) -> List[Tuple[int, int, Optional[str]]]:
    """No cmap-14 UVS for FE00 / FE08–FE0F — access is GSUB liga only.

    UVS for slice selectors made browsers map `base+FE08` after dropping
    overlay, so digraphs became two full-advance halves instead of
    `.dk.ov` + opposing segment. Overlay / slice / digraphs all use
    `ccmp`/`rlig`/`liga` on `U+FE00` / `U+FE08`–`U+FE0F`.
    """
    del base_cp, base_glyph, glyphs
    return []


def _squish_segment_form_name(base: str, suffix: str, *, name_tag: str = "") -> str:
    """Occupancy clip name: `.dk` / `.dk.ca` and triangle `.tl` siblings."""
    if suffix in MARK_POS_SEGMENT.values():
        mark_cp = CA_MARK_CP if name_tag == CA_MARK_SQUISH_TAG else NHAY_MARK_CP
        return _segment_squish_of(base, suffix, mark_cp=mark_cp)
    return f"{base}.{suffix}{name_tag}"


def _add_occupancy_squish_clips(
    cjk_bases: Sequence[str],
    *,
    slot_frac: float,
    name_tag: str,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
) -> None:
    """Mark-base occupancy clips (ca 2/3, nhay 3/4) + D4 segment copies."""
    add_squish_forms(
        cjk_bases,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
        slot_frac=slot_frac,
        name_tag=name_tag,
    )
    occ = float(slot_frac)

    tag = str(name_tag)
    half_windows = {
        "dk": _half_slot_rect(
            float(target_upem), pin="left", axis="x", segment_frac=occ
        ),
        "dkl": _half_slot_rect(
            float(target_upem), pin="right", axis="x", segment_frac=occ
        ),
        "dkb": _half_slot_rect(
            float(target_upem), pin="top", axis="y", segment_frac=occ
        ),
        "dkt": _half_slot_rect(
            float(target_upem), pin="bottom", axis="y", segment_frac=occ
        ),
    }
    propagate_d4_segments(
        cjk_bases,
        suffixes=("dk", "dkl", "dkb", "dkt", "tl", "br", "tr", "bl"),
        form_name=lambda form, suf: _squish_segment_form_name(form, suf, name_tag=tag),
        windows=half_windows,
        labels={
            "dk": SLICE_LABELS["left"],
            "dkl": SLICE_LABELS["right"],
            "dkb": SLICE_LABELS["top"],
            "dkt": SLICE_LABELS["bot"],
            "tl": SLICE_LABELS["tl"],
            "br": SLICE_LABELS["br"],
            "tr": SLICE_LABELS["tr"],
            "bl": SLICE_LABELS["bl"],
        },
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )


def prepare_squish_vs_access(
    *,
    cjk_bases: Sequence[str],
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int,
    liga_rules: List[str],
    uvs_rows: Optional[List[Tuple[int, int, Optional[str]]]] = None,
    width_factor: float = SQUISH_FACTOR,
    height_factor: float = SQUISH_FACTOR,
    slot_frac: Optional[float] = None,
    name_tag: str = "",
    base_cps: Optional[Sequence[int]] = None,
    in_dir: Optional[str] = None,
    cmap_access: bool = True,
    add_overlays: bool = True,
) -> List[str]:
    """Ensure squish forms + FE00 / FE08–FE0F overlay/slice access (liga).

    Returns the squishable form name list (identity + D4).
    `cmap_access=False` skips overlay/slice cmap (base-face mark VS).
    """
    del in_dir  # kept for call-site compat

    if cmap_access:
        for cp, name in (
            (OV_SELECTOR_CP, OV_SELECTOR_NAME),
            *[(cp, sel) for cp, sel, _suf in SQUISH_VS_SLOTS],
        ):
            _ensure_side_selector(
                cp,
                name,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                cmap=cmap,
            )
    squishable = squishable_forms(cjk_bases)
    tag = str(name_tag)
    add_squish_forms(
        cjk_bases,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        width_factor=width_factor,
        height_factor=height_factor,
        target_upem=target_upem,
        slot_frac=slot_frac,
        name_tag=tag,
    )
    occ = float(slot_frac) if slot_frac is not None else 0.5

    half_windows = {
        "dk": _half_slot_rect(
            float(target_upem), pin="left", axis="x", segment_frac=occ
        ),
        "dkl": _half_slot_rect(
            float(target_upem), pin="right", axis="x", segment_frac=occ
        ),
        "dkb": _half_slot_rect(
            float(target_upem), pin="top", axis="y", segment_frac=occ
        ),
        "dkt": _half_slot_rect(
            float(target_upem), pin="bottom", axis="y", segment_frac=occ
        ),
    }
    propagate_d4_segments(
        cjk_bases,
        suffixes=("dk", "dkl", "dkb", "dkt", "tl", "br", "tr", "bl"),
        form_name=lambda form, suf: _squish_segment_form_name(form, suf, name_tag=tag),
        windows=half_windows,
        labels={
            "dk": SLICE_LABELS["left"],
            "dkl": SLICE_LABELS["right"],
            "dkb": SLICE_LABELS["top"],
            "dkt": SLICE_LABELS["bot"],
            "tl": SLICE_LABELS["tl"],
            "br": SLICE_LABELS["br"],
            "tr": SLICE_LABELS["tr"],
            "bl": SLICE_LABELS["bl"],
        },
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )

    ov_sources: List[str] = []
    for form in squishable:
        if form not in glyphs:
            continue
        ov_sources.append(form)
        for _cp, _sel, suf in SQUISH_VS_SLOTS:
            sq = _squish_form_name(form, suf)
            if sq in glyphs:
                ov_sources.append(sq)
    if add_overlays:
        add_overlay_forms(
            ov_sources,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )

    # Ligatures installed programmatically in `install_cjk_composition_gsub`.
    del liga_rules

    if uvs_rows is not None:
        cp_by_name: Dict[str, int] = {}
        if base_cps is not None:
            for cp in base_cps:
                cp_by_name[glyph_name_for_cp(cp)] = cp
        else:
            for cp, gname in cmap.items():
                if gname in cjk_bases:
                    cp_by_name[gname] = cp
        for base in cjk_bases:
            cp = cp_by_name.get(base)
            if cp is None:
                continue
            uvs_rows.extend(build_squish_vs_uvs_entries(cp, base, glyphs=glyphs))
    return squishable


def inject_mark_slot_selectors(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    *,
    pua: bool = False,
) -> List[str]:
    """Cmap FE00–FE0F as ca/nhay position×mirror VS (optional legacy BMP PUA)."""
    names: List[str] = []
    for i, (cp, name, _pos, _mir) in enumerate(MARK_SLOT_VS):
        _ensure_side_selector(
            cp,
            name,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
        )
        if pua:
            cmap[MARK_SLOT_PUA_BASE + i] = name
        names.append(name)
    return names


def prepare_marks(
    *,
    in_dir: str,
    cjk_bases: Sequence[str],
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int,
    liga_rules: List[str],
    uvs_rows: Optional[List[Tuple[int, int, Optional[str]]]] = None,
    local_scale: float = 0.96,
) -> Optional[Dict]:
    """Load ca/nhay + per-mark segment squish for the base CJK face.

    ca mark = **1/3** (base **2/3**); nhay mark = **1/4** (base **3/4**).
    Half-cell digraph access (`.dk*` at 0.55) lives on the `h` face instead.
    """
    try:
        path = resolve_plangothic_p2(in_dir)
    except FileNotFoundError:
        return None

    core_cps, core_glyphs = load_core_marks(
        path, target_upem, local_scale=local_scale, mark_cps=CORE_MARK_CPS
    )
    if not core_cps:
        return None

    lr_glyphs: Dict[int, TTGlyph] = {}
    tb_glyphs: Dict[int, TTGlyph] = {}
    for cp, raw in list(core_glyphs.items()):
        seg_frac = mark_segment_frac_for(cp)
        lr = fit_mark_to_halfcell(
            raw,
            target_upem,
            axis="x",
            glyph_set=None,
            segment_frac=seg_frac,
        )
        lr_glyphs[cp] = lr
        tb_glyphs[cp], _ = make_tb_mark_glyph(
            lr,
            target_upem=target_upem,
            glyph_set=None,
            base_name=glyph_name_for_cp(cp),
        )

    set_mark_cps(core_cps)

    right_marks, left_marks, top_marks, bottom_marks = add_mark_glyphs(
        core_cps,
        lr_glyphs,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        cmap=cmap,
        target_upem=target_upem,
        tb_glyphs=tb_glyphs,
    )

    squishable = squishable_forms(cjk_bases)
    del liga_rules, uvs_rows
    for slot_frac, tag in (
        (NHAY_MARK_BASE_FRAC, NHAY_MARK_SQUISH_TAG),
        (CA_MARK_BASE_FRAC, CA_MARK_SQUISH_TAG),
    ):
        _add_occupancy_squish_clips(
            cjk_bases,
            slot_frac=slot_frac,
            name_tag=tag,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )
    inject_mark_slot_selectors(glyph_order, glyphs, metrics, cmap)

    marked = add_marked_composites(
        squishable,
        core_cps,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )

    return {
        "core_cps": list(core_cps),
        "mark_cps": list(core_cps),
        "right_marks": list(right_marks),
        "left_marks": list(left_marks),
        "top_marks": list(top_marks),
        "bottom_marks": list(bottom_marks),
        "squishable": list(squishable),
        "marked": list(marked),
        "n_core": len(core_cps),
    }


def compile_marks_layout(
    font,
    state: Dict,
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    glyph_order: Sequence[str],
    target_upem: int,
) -> int:
    """Marked composites + their ligatures are installed in
    `prepare_marks` / `install_cjk_composition_gsub`.
    """
    del font, state, glyphs, metrics, glyph_order, target_upem
    return 0
