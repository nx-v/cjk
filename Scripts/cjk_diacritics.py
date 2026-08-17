"""Reading marks for Pan-CJK subfonts.

Core marks from Plangothic P2: U+16FF0 (ca) / U+16FF1 (nhay) only.

Half-cell niches are **slices** (clip to left / right / top / bottom — no
stretch). Same half-cell glyphs serve FE0C–FE0F access and ca/nhay placement.
ca/nhay **trigger** the matching niche: GSUB ligates
``base (+ niche VS)? + mark`` into a precomposed TT composite
(``base.dk_u16FF0``, …) with the mark baked into the free half. Zero-width
``.ov`` forms of those composites liga the same way for digraphs.

    CJK (D4)? FE0C MARK   → ``base.dk_MARK``   (left slice + mark right)
    CJK (D4)? FE0D MARK   → ``base.dkl_MARK``  (right slice + mark left)
    CJK (D4)? FE0E MARK   → ``base.dkb_MARK``  (top slice + mark bottom)
    CJK (D4)? FE0F MARK   → ``base.dkt_MARK``  (bottom slice + mark top)
    CJK (D4)? MARK        → ``base.dk_MARK``   (bare mark auto-slices)

Niche VS (``FE0C``–``FE0F``) selects which half; bare ca/nhay defaults to
``.dk``. Optional legacy ``FE08``/``FE09``/``FE0A`` still select mark-side
forms (L/T/B). Browser galleries prefer PUA ``E009``–``E00C`` mirrors of
FE0C–F.

Slice / overlay access (same ``.dk*`` glyphs; GSUB liga, not cmap-14 UVS)::

    CJK  ( D4 VS )?  FE0B              → zero-width ``.ov``
    CJK  ( D4 VS )?  FE0C..FE0F        → half-cell ``.dk`` / ``.dkl`` / ``.dkt`` / ``.dkb``
    CJK  ( D4 VS )?  FE0B FE0C..FE0F   → zero-width half-cell overlay
    (either order FE0B↔FE0C–F; UVS would eat FE0B and drop the niche VS)
    marked composites also get ``.ov`` for digraph first halves
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import otTables as ot
from fontTools.ttLib.tables._g_l_y_f import (
    ROUND_XY_TO_GRID,
    UNSCALED_COMPONENT_OFFSET,
    USE_MY_METRICS,
    Glyph as TTGlyph,
    GlyphComponent,
)

from cape_weightor import (
    apply_height,
    apply_width,
    estimate_horizontal_stem,
    estimate_vertical_stem,
    layer_from_ttglyph,
    offset_layer,
    ttglyph_from_layer,
)
from shared_diacritics import (
    CGJ_CP,
    MAX_DIACRITIC_FRAC,
    _glyph_ink_size,
    add_dakuten_mark_glyphs,
    collect_dakuten_base_anchors,
    dakuten_mark_name,
    dakuten_mark_stack_label,
    install_dakuten_gpos,
    install_dakuten_slot_gsub,
    iter_dakuten_codepoints,
    resolve_dakuten_mark_font_stack,
)
from shared_half_cells import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    TRANSFORM_MODES,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    add_overlay_forms,
    build_chain_context_format2,
    build_chunked_single_subst_lookup,
    build_ext_gsub_lookup,
    empty_glyph,
    ideographic_bounds,
    ideographic_center,
    orientation_form_names,
    overlay_glyph_name,
    recording_bounds,
    variant_glyph_name,
    vs_glyph_name,
)

PLANGOTHIC_P2_FILENAME = "PlangothicP2-Regular.ttf"
# Plangothic reading marks (always included when the face is present).
CORE_MARK_CPS: Tuple[int, ...] = (0x16FF0, 0x16FF1)
# Runtime: ca/nhay only (updated by ``prepare_marks``).
MARK_CPS: Tuple[int, ...] = CORE_MARK_CPS
LR_MARK_CPS: Tuple[int, ...] = CORE_MARK_CPS  # compat export
# Cap stem-match steps when fitting mark ink (squish niche sizing).
_MARK_STEM_STEP = 8.0
# Cache fitted shared marks (inventory helper / optional tools).
_SHARED_MARK_CACHE: Dict[
    Tuple[Tuple[str, ...], int, Tuple[int, int, int, int]],
    Tuple[Tuple[int, ...], Dict[int, TTGlyph]],
] = {}
# Compat / mark-niche selectors (FE08–FE0A) + squish/overlay access (FE0B–FE0F).
ALT_SELECTOR_CP = 0xFE08
ALT_SELECTOR_NAME = "vsLeft"
LEFT_SELECTOR_CP = ALT_SELECTOR_CP
LEFT_SELECTOR_NAME = ALT_SELECTOR_NAME
TOP_SELECTOR_CP = 0xFE09
TOP_SELECTOR_NAME = "vsTop"
BOT_SELECTOR_CP = 0xFE0A
BOT_SELECTOR_NAME = "vsBot"
OV_SELECTOR_CP = 0xFE0B
OV_SELECTOR_NAME = "vsOv"
SQUISH_RIGHT_CP = 0xFE0C
SQUISH_RIGHT_NAME = "vsDk"
SQUISH_LEFT_CP = 0xFE0D
SQUISH_LEFT_NAME = "vsDkl"
# FE0E/FE0F name the half the *base* occupies (top / bottom), not the free mark niche.
SQUISH_TOP_CP = 0xFE0E
SQUISH_TOP_NAME = "vsDkb"
SQUISH_BOT_CP = 0xFE0F
SQUISH_BOT_NAME = "vsDkt"
# Non-ignorable PUA mirrors of FE0B..FE0F for browser digraph shaping.
# Blink drops Default_Ignorable VS that are not cmap-14 UVS before GSUB;
# PUA U+E008..E00C maps to the same selector glyphs so liga still runs.
OV_PUA_CP = 0xE008
SQUISH_RIGHT_PUA_CP = 0xE009
SQUISH_LEFT_PUA_CP = 0xE00A
SQUISH_TOP_PUA_CP = 0xE00B
SQUISH_BOT_PUA_CP = 0xE00C
SQUISH_PUA_CPS: Tuple[int, ...] = (
    OV_PUA_CP,
    SQUISH_RIGHT_PUA_CP,
    SQUISH_LEFT_PUA_CP,
    SQUISH_TOP_PUA_CP,
    SQUISH_BOT_PUA_CP,
)
SIDE_SELECTOR_CPS: Tuple[int, ...] = (
    ALT_SELECTOR_CP,
    TOP_SELECTOR_CP,
    BOT_SELECTOR_CP,
    OV_SELECTOR_CP,
    SQUISH_RIGHT_CP,
    SQUISH_LEFT_CP,
    SQUISH_TOP_CP,
    SQUISH_BOT_CP,
)
# FE0C..FE0F → base half suffix (``.dk`` / ``.dkl`` / ``.dkb`` / ``.dkt``).
SQUISH_VS_SLOTS: Tuple[Tuple[int, str, str], ...] = (
    (SQUISH_RIGHT_CP, SQUISH_RIGHT_NAME, "dk"),
    (SQUISH_LEFT_CP, SQUISH_LEFT_NAME, "dkl"),
    (SQUISH_TOP_CP, SQUISH_TOP_NAME, "dkb"),
    (SQUISH_BOT_CP, SQUISH_BOT_NAME, "dkt"),
)
# PUA codepoint → same glyph as FE0B..FE0F (for digraph HTML / browser liga).
SQUISH_PUA_SLOTS: Tuple[Tuple[int, str], ...] = (
    (OV_PUA_CP, OV_SELECTOR_NAME),
    (SQUISH_RIGHT_PUA_CP, SQUISH_RIGHT_NAME),
    (SQUISH_LEFT_PUA_CP, SQUISH_LEFT_NAME),
    (SQUISH_TOP_PUA_CP, SQUISH_TOP_NAME),
    (SQUISH_BOT_PUA_CP, SQUISH_BOT_NAME),
)

# Full D4 (identity + VS02..VS08 / FE01..FE07), including r90my.
BASE_VS_MODE_COUNT = 8
# Fallback when mark size is unknown; normally computed from mark ink.
# Slightly over half so digraph halves meet with less middle gutter
# (exact 0.5 + half-pad left a wide TB seam).
SQUISH_FACTOR = 0.55
SQUISH_FACTOR_MIN = 0.55
SQUISH_FACTOR_MAX = 0.55
# Base face (ca/nhay): mark occupies 1/4; CJK squish fills the other 3/4.
MARK_NICHE_FRAC = 1.0 / 4.0
MARK_BASE_SQUISH_FACTOR = 1.0 - MARK_NICHE_FRAC  # 3/4
EDGE_PAD_FRAC = 0.03
GAP_FRAC = 0.02
HALF_PAD_FRAC = 0.02  # inset inside the occupied half (was 0.04)

GDEF_CLASS_BASE = 1
GDEF_CLASS_MARK = 3
MARK_CLASS_RIGHT = 0
MARK_CLASS_LEFT = 1
MARK_CLASS_TOP = 2
MARK_CLASS_BOTTOM = 3
MARK_FEATURE_TAGS: Tuple[str, ...] = ("mark", "abvm")


def resolve_plangothic_p2(in_dir: str) -> str:
    path = os.path.join(in_dir, PLANGOTHIC_P2_FILENAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing Plangothic P2: {path}")
    return path


def squish_name(base_name: str) -> str:
    """Left half-slice (right niche free) — clip of upright id."""
    return f"{base_name}.dk"


def squish_left_name(base_name: str) -> str:
    """Right half-slice (left niche free); clip of upright id."""
    return f"{base_name}.dkl"


def squish_top_name(base_name: str) -> str:
    """Bottom half-slice (top niche free); clip of upright id."""
    return f"{base_name}.dkt"


def squish_bot_name(base_name: str) -> str:
    """Top half-slice (bottom niche free); clip of upright id."""
    return f"{base_name}.dkb"


def left_mark_name(mark_name: str) -> str:
    return f"{mark_name}.L"


def top_mark_name(mark_name: str) -> str:
    return f"{mark_name}.T"


def bottom_mark_name(mark_name: str) -> str:
    return f"{mark_name}.B"


def _mark_root_name(name: str) -> str:
    return name.split(".", 1)[0]


def _mark_cp_from_name(name: str) -> Optional[int]:
    root = _mark_root_name(name)
    if not root.startswith("u") or len(root) < 2:
        return None
    try:
        return int(root[1:], 16)
    except ValueError:
        return None


@dataclass(frozen=True)
class MarkRef:
    """Ink envelope + stems that shared marks are fitted to (ca/nhay)."""

    width: float
    height: float
    vstem: float
    hstem: float


def measure_mark_ref(
    mark_glyphs: Dict[int, TTGlyph],
    mark_cps: Sequence[int] = CORE_MARK_CPS,
) -> Optional[MarkRef]:
    """Average width/height/stems over available core mark outlines."""
    widths: List[float] = []
    heights: List[float] = []
    vstems: List[float] = []
    hstems: List[float] = []
    for cp in mark_cps:
        g = mark_glyphs.get(cp)
        if g is None:
            continue
        layer = layer_from_ttglyph(g, 0.0)
        if not layer.paths:
            continue
        b = layer.bounds
        if b.size.x > 1.0 and b.size.y > 1.0:
            widths.append(float(b.size.x))
            heights.append(float(b.size.y))
        v = estimate_vertical_stem(layer)
        h = estimate_horizontal_stem(layer)
        if v > 0:
            vstems.append(v)
        if h > 0:
            hstems.append(h)
    if not widths or not heights:
        return None
    return MarkRef(
        width=sum(widths) / len(widths),
        height=sum(heights) / len(heights),
        vstem=(sum(vstems) / len(vstems)) if vstems else 0.0,
        hstem=(sum(hstems) / len(hstems)) if hstems else 0.0,
    )


def fit_mark_to_ref(glyph: TTGlyph, ref: MarkRef) -> TTGlyph:
    """Match ca/nhay width, height, and H/V stems; keep ink centered at origin."""
    layer = layer_from_ttglyph(glyph, 0.0)
    if not layer.paths:
        return glyph

    def _size_to_ref() -> None:
        # Direct axis scales hit the ca/nhay envelope exactly; stem match
        # afterward restores stroke weight (CAPE Width/Height undershoot on
        # sparse accent outlines).
        b = layer.bounds
        bw, bh = b.size.x, b.size.y
        cx = b.origin.x + 0.5 * bw
        cy = b.origin.y + 0.5 * bh
        sx = (ref.width / bw) if bw > 1.0 and ref.width > 1.0 else 1.0
        sy = (ref.height / bh) if bh > 1.0 and ref.height > 1.0 else 1.0
        if abs(sx - 1.0) < 1e-4 and abs(sy - 1.0) < 1e-4:
            return
        layer.applyTransform((sx, 0, 0, sy, cx - sx * cx, cy - sy * cy))

    _size_to_ref()

    # Stem match (polarity probe, same idea as Yi stem normalize).
    sign_x = 1.0
    sign_y = 1.0
    probe = 2.0
    v0 = estimate_vertical_stem(layer)
    if v0 > 0 and ref.vstem > 0:
        offset_layer(layer, -probe, 0.0)
        v1 = estimate_vertical_stem(layer)
        offset_layer(layer, probe, 0.0)
        if v1 + 1e-6 < v0:
            sign_x = -1.0
    h0 = estimate_horizontal_stem(layer)
    if h0 > 0 and ref.hstem > 0:
        offset_layer(layer, 0.0, probe)
        h1 = estimate_horizontal_stem(layer)
        offset_layer(layer, 0.0, -probe)
        if h1 + 1e-6 > h0:
            sign_y = -1.0

    for _ in range(3):
        cur_v = estimate_vertical_stem(layer)
        cur_h = estimate_horizontal_stem(layer)
        dx = dy = 0.0
        if ref.vstem > 0 and cur_v > 0:
            ratio = cur_v / ref.vstem
            if 0.5 <= ratio <= 1.85:
                dx = sign_x * (cur_v - ref.vstem) / 2.0
                dx = max(-_MARK_STEM_STEP, min(_MARK_STEM_STEP, dx))
        if ref.hstem > 0 and cur_h > 0:
            ratio = cur_h / ref.hstem
            if 0.5 <= ratio <= 1.85:
                dy = sign_y * (cur_h - ref.hstem) / 2.0
                dy = max(-_MARK_STEM_STEP, min(_MARK_STEM_STEP, dy))
        if abs(dx) < 0.25 and abs(dy) < 0.25:
            break
        offset_layer(layer, dx, dy)

    # Stem offset can inflate the bbox — snap size back to the niche envelope.
    _size_to_ref()

    b = layer.bounds
    cx = b.origin.x + 0.5 * b.size.x
    cy = b.origin.y + 0.5 * b.size.y
    if abs(cx) > 1e-6 or abs(cy) > 1e-6:
        layer.applyTransform((1, 0, 0, 1, -cx, -cy))

    out, _adv, _lsb = ttglyph_from_layer(layer)
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


_D4_SUFFIXES: frozenset[str] = frozenset(
    suffix for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES if suffix is not None
)
_D4_TRANSFORM: Dict[str, Tuple[int, bool, bool]] = {
    suffix: (rot, fx, fy)
    for _vs, rot, fx, fy, suffix in TRANSFORM_MODES
    if suffix is not None
}

# Cell niches. Oriented squish forms are D4(composites) of upright H/V bakes;
# under 90° maps, LR niches come from upright TB parents and vice versa.
_NICHE_NAMES: Tuple[str, ...] = ("right", "left", "top", "bottom")


def _niche_squish_name(root: str, niche: str) -> str:
    match niche:
        case "right":
            return squish_name(root)
        case "left":
            return squish_left_name(root)
        case "top":
            return squish_top_name(root)
        case "bottom":
            return squish_bot_name(root)
        case _:
            raise ValueError(niche)


def _d4_niche_forward(rot: int, fx: bool, fy: bool) -> Dict[str, str]:
    """Upright niche → niche after D4 (about origin)."""
    from shared_half_cells import variant_matrix

    (xx, xy), (yx, yy) = variant_matrix(rot90_quarters=rot, flip_x=fx, flip_y=fy)
    pts = {
        "right": [(1.0, y) for y in (-1.0, 0.0, 1.0)],
        "left": [(-1.0, y) for y in (-1.0, 0.0, 1.0)],
        "top": [(x, 1.0) for x in (-1.0, 0.0, 1.0)],
        "bottom": [(x, -1.0) for x in (-1.0, 0.0, 1.0)],
    }

    def _xf(x: float, y: float) -> Tuple[float, float]:
        return (xx * x + yx * y, xy * x + yy * y)

    out: Dict[str, str] = {}
    for side, samples in pts.items():
        mapped = [_xf(x, y) for x, y in samples]
        cx = sum(p[0] for p in mapped) / len(mapped)
        cy = sum(p[1] for p in mapped) / len(mapped)
        if abs(cx) >= abs(cy):
            out[side] = "right" if cx > 0 else "left"
        else:
            out[side] = "top" if cy > 0 else "bottom"
    return out


# needed_niche_on_oriented → upright_niche_parent
_ORIENTED_SQUISH_PARENT: Dict[str, Dict[str, str]] = {}
for _suf, (_rot, _fx, _fy) in _D4_TRANSFORM.items():
    fwd = _d4_niche_forward(_rot, _fx, _fy)
    _ORIENTED_SQUISH_PARENT[_suf] = {new: old for old, new in fwd.items()}


def _d4_suffix_of(name: str) -> Optional[str]:
    """Return D4 suffix (``r90``, ``mx``, …) or ``None`` for identity."""
    if "." not in name:
        return None
    suf = name.rsplit(".", 1)[1]
    return suf if suf in _D4_SUFFIXES else None


def _d4_root_name(name: str) -> str:
    suf = _d4_suffix_of(name)
    if suf is None:
        return name
    return name[: -(len(suf) + 1)]


def make_tb_mark_glyph(
    glyph: TTGlyph,
    *,
    target_upem: int,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    base_name: str = "_tb",
) -> Tuple[TTGlyph, int]:
    """TB mark = r90 of LR-fitted upright (pure rotation about origin).

    Upright ca/nhay already fills the LR half; 90° maps that box onto the TB
    half, so no extra stretch/normalize. ``.B`` / D4 TB aliases composite this.
    """
    from shared_half_cells import make_composite_variant

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


def add_mark_d4_composites(
    base_name: str,
    *,
    target_upem: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
) -> List[str]:
    """Install mark D4 forms as pure composites (incl. 2×2 r90)."""
    from shared_half_cells import SIDEWAYS_FROM_R90, make_composite_variant

    installed: List[str] = []
    adv, lsb = metrics[base_name]
    r90_name = variant_glyph_name(base_name, "r90")

    for _vs, rot, fx, fy, suffix in TRANSFORM_MODES:
        if suffix is None:
            continue
        m_name = variant_glyph_name(base_name, suffix)
        if m_name in glyphs:
            installed.append(m_name)
            continue

        if suffix in SIDEWAYS_FROM_R90 and r90_name in glyphs:
            rel_rot, rel_fx, rel_fy = SIDEWAYS_FROM_R90[suffix]
            parent = r90_name
            parent_adv, parent_lsb = metrics[parent]
            g, a, l = make_composite_variant(
                parent,
                target_upem,
                rot90_quarters=rel_rot,
                flip_x=rel_fx,
                flip_y=rel_fy,
                advance=parent_adv,
                lsb=parent_lsb,
                base_glyph=glyphs[parent],
                glyph_set=glyphs,
                center=(0.0, 0.0),
                allow_2x2=False,
            )
        else:
            g, a, l = make_composite_variant(
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
    from shared_half_cells import _recording_from_glyph

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

    Axis mirrors (``mx`` / ``my``) reverse contour orientation; without this,
    CAPE ``apply_width`` thins verticals instead of restoring them.
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
    """Scale mark outline and pin ink center to ``(0, 0)`` (GPOS attach)."""
    from shared_half_cells import apply_transform

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
    """Return ``(codepoints, cp → zero-origin mark glyph)`` from Plangothic P2."""
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


def load_shared_marks(
    font_paths: Sequence[str],
    target_upem: int,
    *,
    ref: MarkRef,
    exclude_cps: Sequence[int] = (),
) -> Tuple[List[int], Dict[int, TTGlyph]]:
    """Load stack ``\\p{M}`` marks; fit each to ca/nhay W/H/stems.

    Same inventory as ``shared_diacritics`` (all ``\\p{M}`` except variation
    selectors; oversized outlines dropped). Earlier fonts in ``font_paths``
    win per codepoint.
    """
    from copy import deepcopy

    paths_key = tuple(os.path.normpath(p) for p in font_paths)
    ref_key = (
        otRound(ref.width),
        otRound(ref.height),
        otRound(ref.vstem),
        otRound(ref.hstem),
    )
    cache_key = (paths_key, int(target_upem), ref_key)
    cached = _SHARED_MARK_CACHE.get(cache_key)
    if cached is not None:
        order_t, glyphs_t = cached
        excl = set(exclude_cps)
        order = [cp for cp in order_t if cp not in excl]
        return order, {cp: deepcopy(glyphs_t[cp]) for cp in order}

    excluded = set(exclude_cps)
    claimed: Dict[int, TTGlyph] = {}
    order: List[int] = []

    for font_path in font_paths:
        tt = TTFont(font_path, fontNumber=0)
        try:
            cmap: Dict[int, str] = {}
            for table in tt["cmap"].tables:
                if table.isUnicode():
                    cmap.update(table.cmap)
            glyph_set = tt.getGlyphSet()
            src_upem = float(tt["head"].unitsPerEm)
            max_ext = src_upem * MAX_DIACRITIC_FRAC
            scale = (float(target_upem) / src_upem) if src_upem else 1.0

            for cp in iter_dakuten_codepoints(cmap):
                if cp == CGJ_CP or cp in claimed:
                    continue
                gname = cmap[cp]
                try:
                    sized = _glyph_ink_size(glyph_set, gname)
                except Exception:
                    sized = None
                if sized is None:
                    continue
                w, h = sized
                if w > max_ext + 1e-6 and h > max_ext + 1e-6:
                    continue
                rec = DecomposingRecordingPen(glyph_set)
                try:
                    glyph_set[gname].draw(rec)
                except Exception:
                    continue
                mark = make_mark_glyph(rec, scale=scale)
                if mark is None:
                    continue
                mark = fit_mark_to_ref(mark, ref)
                claimed[cp] = mark
                order.append(cp)
        finally:
            tt.close()

    _SHARED_MARK_CACHE[cache_key] = (tuple(order), claimed)
    order_out = [cp for cp in order if cp not in excluded]
    return order_out, {cp: deepcopy(claimed[cp]) for cp in order_out}


def set_mark_cps(cps: Sequence[int]) -> Tuple[int, ...]:
    """Update module-level ``MARK_CPS`` (used by CSS unicode-range)."""
    global MARK_CPS, LR_MARK_CPS
    MARK_CPS = tuple(cps)
    LR_MARK_CPS = tuple(c for c in cps if c in CORE_MARK_CPS) or CORE_MARK_CPS
    return MARK_CPS


def inventory_shared_mark_cps(in_dir: str) -> List[int]:
    """Codepoints from the mark stack (no outlines) — gallery / CSS helpers."""
    try:
        paths = resolve_dakuten_mark_font_stack(in_dir)
    except FileNotFoundError:
        return []
    seen: set[int] = set()
    out: List[int] = []
    for font_path in paths:
        tt = TTFont(font_path, fontNumber=0)
        try:
            cmap: Dict[int, str] = {}
            for table in tt["cmap"].tables:
                if table.isUnicode():
                    cmap.update(table.cmap)
            for cp in iter_dakuten_codepoints(cmap):
                if cp == CGJ_CP or cp in seen or cp in CORE_MARK_CPS:
                    continue
                seen.add(cp)
                out.append(cp)
        finally:
            tt.close()
    return out


def mark_ink_width(
    glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]] = None
) -> float:
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        return float(glyph.xMax - glyph.xMin)
    except Exception:
        return 0.0


def mark_ink_height(
    glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]] = None
) -> float:
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        return float(glyph.yMax - glyph.yMin)
    except Exception:
        return 0.0


def squish_factor_for_marks(
    mark_names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    target_upem: int,
    axis: str = "x",
) -> float:
    """Width/height factor so one side niche fits the largest mark + pads."""
    if axis == "y":
        sizes = [mark_ink_height(glyphs[n], glyphs) for n in mark_names if n in glyphs]
    else:
        sizes = [mark_ink_width(glyphs[n], glyphs) for n in mark_names if n in glyphs]
    max_s = max((s for s in sizes if s > 1.0), default=0.0)
    if max_s <= 1.0:
        return SQUISH_FACTOR
    gap = target_upem * GAP_FRAC
    edge = target_upem * EDGE_PAD_FRAC
    side_pad = target_upem * 0.06
    niche = max_s + gap + edge
    usable = max(target_upem - side_pad - niche, target_upem * 0.35)
    factor = usable / max(target_upem - side_pad, 1.0)
    return float(min(SQUISH_FACTOR_MAX, max(SQUISH_FACTOR_MIN, factor)))


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


def _mark_slot_composite(base_name: str) -> TTGlyph:
    """Zero-offset composite alias (extra mark-class GID)."""
    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = base_name
    comp.x = 0
    comp.y = 0
    comp.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [comp]
    return g


def _install_mark_slot(
    slot_name: str,
    source_name: str,
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
) -> None:
    if slot_name in glyphs:
        return
    if source_name not in glyphs:
        return
    glyph_order.append(slot_name)
    glyphs[slot_name] = _mark_slot_composite(source_name)
    metrics[slot_name] = (0, metrics[source_name][1])


def add_mark_glyphs(
    mark_cps: Sequence[int],
    mark_glyphs: Dict[int, TTGlyph],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Install upright marks + D4 composites + FE08/FE09/FE0A slot aliases.

    Returns ``(right, left, top, bottom)`` mark form names.
    Bake upright + full-width ``.T`` (r90 then Width-stretch); ``.B``/``.L`` and
    mark D4 are composites / aliases.
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

    right_names: List[str] = list(upright)
    for name in upright:
        for vname in add_mark_d4_composites(
            name,
            target_upem=target_upem,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        ):
            right_names.append(vname)

    top_names: List[str] = []
    bottom_names: List[str] = []
    for name in upright:
        tn = top_mark_name(name)
        bn = bottom_mark_name(name)
        if tn not in glyphs:
            wide, lsb = make_tb_mark_glyph(
                glyphs[name],
                target_upem=target_upem,
                glyph_set=glyphs,
                base_name=name,
            )
            glyph_order.append(tn)
            glyphs[tn] = wide
            metrics[tn] = (0, lsb)
        _install_mark_slot(
            bn, tn, glyph_order=glyph_order, glyphs=glyphs, metrics=metrics
        )
        if tn in glyphs:
            top_names.append(tn)
        if bn in glyphs:
            bottom_names.append(bn)

    left_names: List[str] = []
    for name in list(right_names):
        ln = left_mark_name(name)
        _install_mark_slot(
            ln, name, glyph_order=glyph_order, glyphs=glyphs, metrics=metrics
        )
        if ln in glyphs:
            left_names.append(ln)

        if _d4_suffix_of(name) is None:
            continue
        root = _d4_root_name(name)
        wide = top_mark_name(root)
        if wide not in glyphs:
            continue
        tn = top_mark_name(name)
        bn = bottom_mark_name(name)
        _install_mark_slot(
            tn, wide, glyph_order=glyph_order, glyphs=glyphs, metrics=metrics
        )
        _install_mark_slot(
            bn, wide, glyph_order=glyph_order, glyphs=glyphs, metrics=metrics
        )
        if tn in glyphs:
            top_names.append(tn)
        if bn in glyphs:
            bottom_names.append(bn)

    return right_names, left_names, top_names, bottom_names


def _mark_forms_for_cps(
    mark_cps: Sequence[int], glyphs: Dict[str, TTGlyph]
) -> List[str]:
    forms: List[str] = []
    for cp in mark_cps:
        base = glyph_name_for_cp(cp)
        if base in glyphs:
            forms.append(base)
        for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            if suffix is None:
                continue
            vname = variant_glyph_name(base, suffix)
            if vname in glyphs:
                forms.append(vname)
    return forms


def mark_liga_rules(
    mark_cps: Sequence[int],
    glyphs: Dict[str, TTGlyph],
) -> List[str]:
    """FEA lines (compat/debug); prefer ``mark_liga_map`` + programmatic GSUB."""
    return [
        f"  sub {' '.join(comps)} by {out};"
        for comps, out in mark_liga_map(mark_cps, glyphs).items()
    ]


def fit_mark_to_halfcell(
    glyph: TTGlyph,
    target_upem: int,
    *,
    axis: str = "x",
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    niche_frac: float = 0.5,
) -> TTGlyph:
    """Fit mark into one niche with uniform CAPE Width/Height.

    ``axis="x"`` → LR niche (``niche_frac`` × width × full ideo height).
    ``axis="y"`` → TB niche (full width × ``niche_frac`` × ideo height).
    Ink is centered at the origin for GPOS attachment.
    Default ``niche_frac=0.5`` (half-cell); base face uses ``1/4``.
    """
    pin = "right" if axis == "x" else "bottom"
    src = _normalize_winding(_bake_simple_glyph(glyph, glyph_set), glyph_set)
    layer = layer_from_ttglyph(src, 0.0)
    if not layer.paths:
        return src

    x0, y0, x1, y1 = _half_slot_rect(
        float(target_upem), pin=pin, axis=axis, niche_frac=niche_frac
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
    niche_frac: float = 0.5,
) -> Tuple[float, float, float, float]:
    """Return ``(x0, y0, x1, y1)`` for the occupied niche slot.

    ``niche_frac`` is the fraction of the cell the niche occupies (0.5 half,
    ``1/4`` for ca/nhay on the base face).
    """
    bot, top, _ = ideographic_bounds(int(target_upem))
    pad = target_upem * HALF_PAD_FRAC
    frac = float(niche_frac)
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
    """Clip ``glyph`` to one niche slot (slice — no stretch / squish).

    Prefer ``make_squished_glyph`` when the upright niche can be built from a
    named base in ``glyph_set``.
    """
    from shared_half_cells import clip_glyph_to_rect

    upem = float(target_upem)
    rect = _half_slot_rect(upem, pin=pin, axis=axis, niche_frac=slot_frac)
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
    """Translate only so ink center sits at the niche-slot center (no re-scale)."""
    from shared_half_cells import apply_transform, _recording_from_glyph

    upem = float(target_upem)
    x0, y0, x1, y1 = _half_slot_rect(upem, pin=pin, axis=axis, niche_frac=slot_frac)
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
    """Upright niche as a **slice** of ``base_name`` (clip; no stretch).

    ``slot_frac`` is the niche band width (0.5 half-cell, 0.75 mark-base, …).
    ``factor`` is kept for call-site compatibility and ignored (no scale).
    """
    from shared_half_cells import make_niche_slice_glyph

    if glyph_set is None:
        raise ValueError("make_squished_glyph requires glyph_set for slice bake")
    upem = int(
        target_upem if target_upem is not None else (advance if advance > 0 else 1000)
    )
    del factor
    occ = float(slot_frac) if slot_frac is not None else 0.5
    rect = _half_slot_rect(float(upem), pin=pin, axis=axis, niche_frac=occ)
    return make_niche_slice_glyph(
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
) -> List[str]:
    """Upright H/V niches as slices of the identity; oriented via D4.

    Per identity base, bake **all four** niche clips from the upright glyph
    (do not mirror ``.dkl``/``.dkt`` into ``.dk``/``.dkb`` — that flips
    asymmetric ideographs). Oriented bases (``.r90``, …) pick the upright
    niche that maps to the needed cell niche under that D4, then composite
    with the same rotate/reflect as the base.
    """
    from shared_half_cells import contour_center, make_composite_variant

    identities = [n for n in base_names if _d4_suffix_of(n) is None and n in glyphs]
    oriented = [n for n in base_names if _d4_suffix_of(n) is not None and n in glyphs]
    # Half-cell digraphs keep a 0.5 slot; mark-base (3/4) passes slot_frac=factor.
    occ_x = float(slot_frac) if slot_frac is not None else 0.5
    occ_y = float(slot_frac) if slot_frac is not None else 0.5

    added: List[str] = []
    # (name_fn, pin, axis, factor, slot_frac)
    upright_slots = (
        (squish_name, "left", "x", width_factor, occ_x),  # .dk  — left ink
        (squish_left_name, "right", "x", width_factor, occ_x),  # .dkl — right ink
        (squish_top_name, "bottom", "y", height_factor, occ_y),  # .dkt — bottom ink
        (squish_bot_name, "top", "y", height_factor, occ_y),  # .dkb — top ink
    )
    for name in identities:
        adv, _lsb = metrics.get(name, (target_upem, 0))
        for name_fn, pin, axis, factor, occ in upright_slots:
            out_name = name_fn(name)
            if out_name in glyphs:
                continue
            sq, sq_adv, sq_lsb = make_squished_glyph(
                name,
                adv,
                glyph_set=glyphs,
                factor=factor,
                pin=pin,
                axis=axis,
                target_upem=target_upem,
                slot_frac=occ,
            )
            glyph_order.append(out_name)
            glyphs[out_name] = sq
            metrics[out_name] = (sq_adv, sq_lsb)
        added.append(name)

    for name in oriented:
        suf = _d4_suffix_of(name)
        assert suf is not None
        root = _d4_root_name(name)
        rot, fx, fy = _D4_TRANSFORM[suf]
        parent_for = _ORIENTED_SQUISH_PARENT[suf]
        # Same pivot as base → base.<suf> so squish stays locked to the cell.
        pivot = contour_center(glyphs[root], glyphs)
        for needed in _NICHE_NAMES:
            upright_niche = parent_for[needed]
            parent = _niche_squish_name(root, upright_niche)
            child = _niche_squish_name(name, needed)
            if child in glyphs or parent not in glyphs:
                continue
            p_adv, p_lsb = metrics[parent]
            g, a, l = make_composite_variant(
                parent,
                target_upem,
                rot90_quarters=rot,
                flip_x=fx,
                flip_y=fy,
                advance=p_adv,
                lsb=p_lsb,
                base_glyph=glyphs[parent],
                glyph_set=glyphs,
                center=pivot,
                allow_2x2=False,
            )
            glyph_order.append(child)
            glyphs[child] = g
            metrics[child] = (a, l)
        added.append(name)

    return added


def cjk_right_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    niche_frac: float = 0.5,
) -> Tuple[int, int]:
    """Center of the free right niche (mark sits here)."""
    del glyph, glyph_set
    bot, top, _ = ideographic_bounds(target_upem)
    right = float(advance) if advance > 0 else float(target_upem)
    return otRound(right * (1.0 - niche_frac / 2.0)), otRound((bot + top) / 2.0)


def cjk_left_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    niche_frac: float = 0.5,
) -> Tuple[int, int]:
    """Center of the free left niche (mark sits here)."""
    del glyph, glyph_set, advance
    bot, top, _ = ideographic_bounds(target_upem)
    return otRound(target_upem * (niche_frac / 2.0)), otRound((bot + top) / 2.0)


def cjk_top_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    niche_frac: float = 0.5,
) -> Tuple[int, int]:
    """Center of the free top niche (mark sits here)."""
    del glyph, glyph_set
    bot, top, _ = ideographic_bounds(target_upem)
    span = top - bot
    mid_free = top - span * (niche_frac / 2.0)
    right = float(advance) if advance > 0 else float(target_upem)
    return otRound(right * 0.5), otRound(mid_free)


def cjk_bottom_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    niche_frac: float = 0.5,
) -> Tuple[int, int]:
    """Center of the free bottom niche (mark sits here)."""
    del glyph, glyph_set
    bot, top, _ = ideographic_bounds(target_upem)
    span = top - bot
    mid_free = bot + span * (niche_frac / 2.0)
    right = float(advance) if advance > 0 else float(target_upem)
    return otRound(right * 0.5), otRound(mid_free)


def collect_niche_base_anchors(
    squishable_bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    """Map squish forms → ``{mark_class: (x, y)}`` (one niche each)."""
    anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for name in squishable_bases:
        mapping = (
            (squish_name(name), MARK_CLASS_RIGHT, cjk_right_anchor),
            (squish_left_name(name), MARK_CLASS_LEFT, cjk_left_anchor),
            (squish_top_name(name), MARK_CLASS_TOP, cjk_top_anchor),
            (squish_bot_name(name), MARK_CLASS_BOTTOM, cjk_bottom_anchor),
        )
        for form, cls, fn in mapping:
            if form not in glyphs:
                continue
            adv, _ = metrics.get(form, (target_upem, 0))
            anchors[form] = {cls: fn(glyphs[form], adv, target_upem, glyph_set=glyphs)}
    return anchors


def marked_form_name(squish_form: str, mark_root: str) -> str:
    """Precomposed squish+mark name, e.g. ``u4E00.dk_u16FF0``."""
    return f"{squish_form}_{mark_root}"


def _niche_mark_component(upright: str, niche_suf: str) -> str:
    """Mark glyph baked into the free half for ``niche_suf``."""
    match niche_suf:
        case "dk":
            return upright
        case "dkl":
            return left_mark_name(upright)
        case "dkt":
            return top_mark_name(upright)
        case "dkb":
            return bottom_mark_name(upright)
        case _:
            return upright


def _niche_anchor_fn(niche_suf: str):
    match niche_suf:
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


def _niche_squish_of(base: str, niche_suf: str) -> str:
    match niche_suf:
        case "dk":
            return squish_name(base)
        case "dkl":
            return squish_left_name(base)
        case "dkt":
            return squish_top_name(base)
        case "dkb":
            return squish_bot_name(base)
        case _:
            return squish_name(base)


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


# Niche suffix paired with FE0C–FE0F (same order as SQUISH_VS_SLOTS).
_MARKED_NICHE_SUFFIXES: Tuple[str, ...] = ("dk", "dkl", "dkb", "dkt")


def add_marked_composites(
    squishable_bases: Sequence[str],
    mark_cps: Sequence[int],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
    niche_frac: float = 0.5,
) -> List[str]:
    """Bake precomposed squish+ca/nhay composites (and return their names).

    One composite per ``(base form × niche × mark)``. Mark D4 is not expanded
    here — mark-side slots (``.L``/``.T``/``.B``) cover niche classes.
    """
    added: List[str] = []
    for base in squishable_bases:
        if base not in glyphs:
            continue
        for niche_suf in _MARKED_NICHE_SUFFIXES:
            sq = _niche_squish_of(base, niche_suf)
            if sq not in glyphs:
                continue
            adv, lsb = metrics.get(sq, (target_upem, 0))
            ax, ay = _niche_anchor_fn(niche_suf)(
                glyphs[sq],
                adv,
                target_upem,
                glyph_set=glyphs,
                niche_frac=niche_frac,
            )
            for cp in mark_cps:
                upright = glyph_name_for_cp(cp)
                if upright not in glyphs:
                    continue
                mark_comp = _niche_mark_component(upright, niche_suf)
                if mark_comp not in glyphs:
                    continue
                mx, my = mark_attach_anchor(glyphs[mark_comp], glyph_set=glyphs)
                out_name = marked_form_name(sq, upright)
                if out_name in glyphs:
                    added.append(out_name)
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
    return added


def marked_liga_map(
    squishable_bases: Sequence[str],
    mark_cps: Sequence[int],
    *,
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """Ligatures into precomposed marked forms (incl. ZW ``.ov`` + auto-squish).

    Lookup-friendly lengths::

        base + mark              → base.dk_mark          (bare auto-squish)
        base + niche + mark      → base.<niche>_mark
        squish + mark            → squish_mark
        squish.ov + mark         → squish_mark.ov
        base.ov + mark           → base.dk_mark.ov
        marked + FE0B            → marked.ov
    """
    liga: Dict[Tuple[str, ...], str] = {}
    niche_by_sel = {sel: suf for _cp, sel, suf in SQUISH_VS_SLOTS}
    vs01 = vs_glyph_name(TRANSFORM_MODES[0][0])

    for base in squishable_bases:
        if base not in glyphs:
            continue
        for cp in mark_cps:
            upright = glyph_name_for_cp(cp)
            if upright not in glyphs:
                continue

            # Bare mark → default right niche (.dk).
            dk = squish_name(base)
            default = marked_form_name(dk, upright)
            if default in glyphs:
                liga[(base, upright)] = default
                base_ov = overlay_glyph_name(base)
                default_ov = overlay_glyph_name(default)
                if base_ov in glyphs and default_ov in glyphs:
                    liga[(base_ov, upright)] = default_ov

            for sel_name, niche_suf in niche_by_sel.items():
                sq = _niche_squish_of(base, niche_suf)
                out = marked_form_name(sq, upright)
                if out not in glyphs:
                    continue
                mark_comp = _niche_mark_component(upright, niche_suf)
                # After niche liga: squish + mark (upright or class slot).
                liga[(sq, upright)] = out
                if mark_comp != upright and mark_comp in glyphs:
                    liga[(sq, mark_comp)] = out
                # One-pass: base + niche + mark.
                liga[(base, sel_name, upright)] = out
                liga[(base, vs01, sel_name, upright)] = out
                # ZW kanji half + mark → ZW precomposed.
                sq_ov = overlay_glyph_name(sq)
                out_ov = overlay_glyph_name(out)
                if sq_ov in glyphs and out_ov in glyphs:
                    liga[(sq_ov, upright)] = out_ov
                    if mark_comp != upright and mark_comp in glyphs:
                        liga[(sq_ov, mark_comp)] = out_ov
                    liga[(base, OV_SELECTOR_NAME, sel_name, upright)] = out_ov
                    liga[(base, sel_name, OV_SELECTOR_NAME, upright)] = out_ov
                    liga[(base, vs01, OV_SELECTOR_NAME, sel_name, upright)] = out_ov
                    liga[(base, vs01, sel_name, OV_SELECTOR_NAME, upright)] = out_ov
                # Precomposed + FE0B → ZW.
                if out_ov in glyphs:
                    liga[(out, OV_SELECTOR_NAME)] = out_ov
    return liga


def _langsys_with_features(feature_indices: Sequence[int]) -> ot.DefaultLangSys:
    ls = ot.DefaultLangSys()
    ls.ReqFeatureIndex = 0xFFFF
    ls.FeatureCount = len(feature_indices)
    ls.FeatureIndex = list(feature_indices)
    return ls


def _ensure_gpos(font, script_tags: Sequence[str]) -> ot.GPOS:
    if "GPOS" in font:
        gpos = font["GPOS"].table
        existing = {sr.ScriptTag for sr in (gpos.ScriptList.ScriptRecord or [])}
        for tag in script_tags:
            if tag in existing:
                continue
            rec = ot.ScriptRecord()
            rec.ScriptTag = tag
            rec.Script = ot.Script()
            rec.Script.DefaultLangSys = _langsys_with_features([])
            rec.Script.LangSysCount = 0
            rec.Script.LangSysRecord = []
            gpos.ScriptList.ScriptRecord.append(rec)
        gpos.ScriptList.ScriptCount = len(gpos.ScriptList.ScriptRecord)
        return gpos

    gpos = ot.GPOS()
    gpos.Version = 0x00010000
    gpos.ScriptList = ot.ScriptList()
    gpos.ScriptList.ScriptRecord = []
    for tag in script_tags:
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        rec.Script.DefaultLangSys = _langsys_with_features([])
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gpos.ScriptList.ScriptRecord.append(rec)
    gpos.ScriptList.ScriptCount = len(script_tags)
    gpos.FeatureList = ot.FeatureList()
    gpos.FeatureList.FeatureRecord = []
    gpos.FeatureList.FeatureCount = 0
    gpos.LookupList = ot.LookupList()
    gpos.LookupList.Lookup = []
    gpos.LookupList.LookupCount = 0
    table = newTable("GPOS")
    table.table = gpos
    font["GPOS"] = table
    return gpos


def _ensure_gdef_classes(
    font,
    *,
    bases: Iterable[str],
    marks: Iterable[str],
    glyph_order: Sequence[str],
) -> None:
    if "GDEF" in font:
        gdef = font["GDEF"].table
    else:
        gdef_table = newTable("GDEF")
        gdef = ot.GDEF()
        gdef.Version = 0x00010000
        gdef.GlyphClassDef = None
        gdef.AttachList = None
        gdef.LigCaretList = None
        gdef.MarkAttachClassDef = None
        gdef_table.table = gdef
        font["GDEF"] = gdef_table

    if gdef.GlyphClassDef is None:
        gdef.GlyphClassDef = ot.GlyphClassDef()
        gdef.GlyphClassDef.classDefs = {}

    class_defs = gdef.GlyphClassDef.classDefs
    order = set(glyph_order)
    for name in bases:
        if name in order:
            class_defs[name] = GDEF_CLASS_BASE
    for name in marks:
        if name in order:
            class_defs[name] = GDEF_CLASS_MARK


def install_squish_gsub(
    font,
    *,
    squishable_bases: Sequence[str],
    right_marks: Sequence[str],
    left_marks: Sequence[str],
    top_marks: Sequence[str],
    bottom_marks: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
) -> int:
    """Chain: base + mark-side → squished base (Format 2 + Extension)."""
    if "GSUB" not in font:
        return 0

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    bases = _gid_sort(
        [
            n
            for n in squishable_bases
            if n in glyphs
            and squish_name(n) in glyphs
            and squish_left_name(n) in glyphs
            and squish_top_name(n) in glyphs
            and squish_bot_name(n) in glyphs
        ]
    )
    marks_r = _gid_sort([n for n in right_marks if n in glyphs])
    marks_l = _gid_sort([n for n in left_marks if n in glyphs])
    marks_t = _gid_sort([n for n in top_marks if n in glyphs])
    marks_b = _gid_sort([n for n in bottom_marks if n in glyphs])
    if not bases or not (marks_r or marks_l or marks_t or marks_b):
        return 0

    gsub = font["GSUB"].table
    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0

    feature_lookup_idxs: List[int] = []

    def _append_chain(marks: Sequence[str], squish_fn) -> None:
        if not marks:
            return
        squish_map = {n: squish_fn(n) for n in bases}
        single_lu = build_chunked_single_subst_lookup(squish_map)
        st = build_chain_context_format2(
            coverage_glyphs=bases,
            input_classes={n: 1 for n in bases},
            input_class=1,
            lookahead_classes={n: 1 for n in marks},
            lookahead_seq=(1,),
        )
        chain_lu = build_ext_gsub_lookup([st])
        chain_index = gsub.LookupList.LookupCount
        single_index = chain_index + 1
        st.ChainSubClassSet[1].ChainSubClassRule[0].SubstLookupRecord[
            0
        ].LookupListIndex = single_index
        gsub.LookupList.Lookup.extend([chain_lu, single_lu])
        gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
        feature_lookup_idxs.append(chain_index)

    # Bare MARK no longer auto-squishes — FE0C–FE0F (or legacy FE08–A) required.
    # Keep L/T/B chains for legacy ``FE08/FE09/FE0A MARK`` mark-side aliases.
    del marks_r
    _append_chain(marks_l, squish_left_name)
    _append_chain(marks_t, squish_top_name)
    _append_chain(marks_b, squish_bot_name)

    tag_to_fr = {fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])}
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            continue
        idxs = list(fr.Feature.LookupListIndex or [])
        for li in feature_lookup_idxs:
            if li not in idxs:
                idxs.append(li)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)
    return len(bases)


def install_mark_side_from_niche_gsub(
    font,
    *,
    squishable_bases: Sequence[str],
    right_marks: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
) -> int:
    """After ``base+FE0D–F → .dkl/.dkt/.dkb``, map upright mark → L/T/B class.

    ``FE0C`` keeps the default right-class upright mark. Required so
    ``CJK FE0D MARK`` (etc.) gets a matching MarkToBase class.
    """
    if "GSUB" not in font:
        return 0

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    niche_forms = {
        "left": _gid_sort(
            [
                squish_left_name(n)
                for n in squishable_bases
                if squish_left_name(n) in glyphs
            ]
        ),
        "top": _gid_sort(
            [
                squish_top_name(n)
                for n in squishable_bases
                if squish_top_name(n) in glyphs
            ]
        ),
        "bottom": _gid_sort(
            [
                squish_bot_name(n)
                for n in squishable_bases
                if squish_bot_name(n) in glyphs
            ]
        ),
    }
    mark_maps = {
        "left": {
            m: left_mark_name(m)
            for m in right_marks
            if m in glyphs and left_mark_name(m) in glyphs
        },
        "top": {
            m: top_mark_name(m)
            for m in right_marks
            if m in glyphs and top_mark_name(m) in glyphs
        },
        "bottom": {
            m: bottom_mark_name(m)
            for m in right_marks
            if m in glyphs and bottom_mark_name(m) in glyphs
        },
    }
    if not any(niche_forms[k] and mark_maps[k] for k in niche_forms):
        return 0

    gsub = font["GSUB"].table
    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0

    feature_lookup_idxs: List[int] = []

    for key in ("left", "top", "bottom"):
        backs = niche_forms[key]
        mapping = mark_maps[key]
        if not backs or not mapping:
            continue
        coverage = _gid_sort(mapping.keys())
        single_lu = build_chunked_single_subst_lookup(mapping)
        st = build_chain_context_format2(
            coverage_glyphs=coverage,
            input_classes={n: 1 for n in coverage},
            input_class=1,
            backtrack_classes={n: 1 for n in backs},
            backtrack_seq=(1,),
        )
        chain_lu = build_ext_gsub_lookup([st])
        chain_index = gsub.LookupList.LookupCount
        single_index = chain_index + 1
        st.ChainSubClassSet[1].ChainSubClassRule[0].SubstLookupRecord[
            0
        ].LookupListIndex = single_index
        gsub.LookupList.Lookup.extend([chain_lu, single_lu])
        gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
        feature_lookup_idxs.append(chain_index)

    tag_to_fr = {fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])}
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            continue
        idxs = list(fr.Feature.LookupListIndex or [])
        for li in feature_lookup_idxs:
            if li not in idxs:
                idxs.append(li)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)
    return len(feature_lookup_idxs)


def install_niche_mark_gpos(
    font,
    *,
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]],
    right_marks: Sequence[str],
    left_marks: Sequence[str],
    top_marks: Sequence[str],
    bottom_marks: Sequence[str],
    glyph_order: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    base_chunk: int = 2048,
) -> int:
    """MarkToBase: R/L/T/B niche classes on squished bases."""
    if not base_anchors:
        return 0

    from fontTools.otlLib.builder import (
        buildAnchor,
        buildLookup,
        buildMarkBasePosSubtable,
    )

    script_tags: List[str] = []
    for line in COMPOSITION_LANGUAGE_SYSTEMS:
        parts = line.replace(";", "").split()
        if len(parts) >= 2 and parts[0] == "languagesystem":
            script_tags.append(parts[1].ljust(4)[:4])

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _sorted(names: Sequence[str]) -> List[str]:
        return [
            n
            for n in sorted(set(names), key=lambda x: order_index.get(x, 10**9))
            if n in order_index
        ]

    marks_r = _sorted(right_marks)
    marks_l = _sorted(left_marks)
    marks_t = _sorted(top_marks)
    marks_b = _sorted(bottom_marks)
    bases_sorted = _sorted(base_anchors)
    if not bases_sorted or not (marks_r or marks_l or marks_t or marks_b):
        return 0

    glyph_map = {n: i for i, n in enumerate(glyph_order)}
    marks = {}
    for n, side, cls in (
        *((n, "right", MARK_CLASS_RIGHT) for n in marks_r),
        *((n, "left", MARK_CLASS_LEFT) for n in marks_l),
        *((n, "top", MARK_CLASS_TOP) for n in marks_t),
        *((n, "bottom", MARK_CLASS_BOTTOM) for n in marks_b),
    ):
        mx, my = (
            mark_attach_anchor(glyphs[n], side=side, glyph_set=glyphs)
            if n in glyphs
            else (0, 0)
        )
        marks[n] = (cls, buildAnchor(mx, my))

    subs = []
    for i in range(0, len(bases_sorted), max(1, base_chunk)):
        chunk = bases_sorted[i : i + base_chunk]
        bases = {}
        for n in chunk:
            class_map = {
                cls: buildAnchor(ax, ay) for cls, (ax, ay) in base_anchors[n].items()
            }
            bases[n] = class_map
        subs.append(buildMarkBasePosSubtable(marks, bases, glyph_map))
    lookup = buildLookup(subs, table="GPOS", extension=True)

    gpos = _ensure_gpos(font, script_tags)
    if gpos.LookupList is None:
        gpos.LookupList = ot.LookupList()
        gpos.LookupList.Lookup = []
        gpos.LookupList.LookupCount = 0

    lookup_index = gpos.LookupList.LookupCount
    gpos.LookupList.Lookup.append(lookup)
    gpos.LookupList.LookupCount = len(gpos.LookupList.Lookup)

    if gpos.FeatureList is None:
        gpos.FeatureList = ot.FeatureList()
        gpos.FeatureList.FeatureRecord = []
        gpos.FeatureList.FeatureCount = 0

    tag_to_fr = {fr.FeatureTag: fr for fr in (gpos.FeatureList.FeatureRecord or [])}
    feature_indices: List[int] = []
    for tag in MARK_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            fr = ot.FeatureRecord()
            fr.FeatureTag = tag
            fr.Feature = ot.Feature()
            fr.Feature.FeatureParams = None
            fr.Feature.LookupListIndex = []
            fr.Feature.LookupCount = 0
            gpos.FeatureList.FeatureRecord.append(fr)
            gpos.FeatureList.FeatureCount = len(gpos.FeatureList.FeatureRecord)
            feature_index = gpos.FeatureList.FeatureCount - 1
            tag_to_fr[tag] = fr
        else:
            feature_index = next(
                i
                for i, rec in enumerate(gpos.FeatureList.FeatureRecord)
                if rec.FeatureTag == tag
            )
        idxs = list(fr.Feature.LookupListIndex or [])
        if lookup_index not in idxs:
            idxs.append(lookup_index)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)
        feature_indices.append(feature_index)

    for sr in gpos.ScriptList.ScriptRecord:
        ls = sr.Script.DefaultLangSys
        if ls is None:
            sr.Script.DefaultLangSys = _langsys_with_features(feature_indices)
        else:
            idxs = list(ls.FeatureIndex or [])
            for fi in feature_indices:
                if fi not in idxs:
                    idxs.append(fi)
            ls.FeatureIndex = idxs
            ls.FeatureCount = len(idxs)

    _ensure_gdef_classes(
        font,
        bases=bases_sorted,
        marks=list(marks.keys()),
        glyph_order=glyph_order,
    )
    return len(bases_sorted)


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


def _squish_form_name(base_form: str, niche_suffix: str) -> str:
    return f"{base_form}.{niche_suffix}"


def squish_vs_liga_map(
    squishable_bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """Ligature map: FE0B → ``.ov``; FE0C–FE0F → squish; FE0B+FE0C–F → squish ``.ov``.

    Also spells explicit identity with ``FE00``/``vs01`` so
    ``base FE00 FE0B FE0C`` matches the same outputs as ``base FE0B FE0C``
    without relying on a prior 2-glyph FE00 no-op alone.
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
    """FEA lines (compat/debug); prefer ``squish_vs_liga_map`` + programmatic GSUB."""
    rules: List[str] = []
    for comps, out in squish_vs_liga_map(squishable_bases, glyphs=glyphs).items():
        rules.append(f"  sub {' '.join(comps)} by {out};")
    return rules


def mark_liga_map(
    mark_cps: Sequence[int],
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """Mark D4 + FE08/FE09/FE0A side-slot ligatures."""
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
    for name in _mark_forms_for_cps(mark_cps, glyphs):
        ln = left_mark_name(name)
        if ln in glyphs:
            liga[(ALT_SELECTOR_NAME, name)] = ln
        tn = top_mark_name(name)
        if tn in glyphs:
            liga[(TOP_SELECTOR_NAME, name)] = tn
        bn = bottom_mark_name(name)
        if bn in glyphs:
            liga[(BOT_SELECTOR_NAME, name)] = bn
    return liga


def d4_liga_map(
    bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    fe00_forms: Optional[Sequence[str]] = None,
) -> Dict[Tuple[str, ...], str]:
    """``base + VS01..VS08`` (PUA / FE00..FE07) → orientation form.

    ``FE00`` / VS01 is a no-op (``glyph + vs01 → glyph``) on every identity
    base, every D4 form, and every squish/overlay form in ``fe00_forms`` so
    explicit id works in all squished positions.
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
                # Identity no-op — consumes FE00 without changing the base.
                liga[(base, sel)] = base
                continue
            vname = variant_glyph_name(base, suffix)
            if vname not in glyphs:
                continue
            liga[(base, sel)] = vname
    # FE00 no-op on squish / overlay forms (id in every niche / overlay).
    for form in fe00_forms or ():
        if form not in glyphs or form in base_set:
            continue
        liga[(form, vs01)] = form
    return liga


def _fe00_noop_form_names(
    squishable: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    mark_cps: Sequence[int] = (),
) -> List[str]:
    """Identity + D4 + ``.dk*`` + ``.ov`` + marked composites that accept FE00."""
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
            for _cp, _sel, suf in SQUISH_VS_SLOTS:
                marked = marked_form_name(_squish_form_name(form, suf), upright)
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
    """Programmatic ``ccmp``/``rlig``/``liga``: D4 + FE0B–FE0F + marked composites.

    Lookup order matters so ``B FE02 FE0D`` / ``A FE00 FE0B FE0C`` / marked
    digraphs work::

        1. D4 (incl. FE00 no-op on bases + squish/ov/marked forms)
        2. 5-glyph marked digraphs — ``A+FE00+FE0B+FE0C+MARK → A.dk_MARK.ov``
        3. 4-glyph identity squish+overlay / marked
        4. 3-glyph squish+overlay / ``base+niche+MARK``
        5. 2-glyph squish/overlay + mark niches + ``squish+MARK`` / auto-squish
    """
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    from shared_half_cells import build_chunked_ligature_subst_lookup

    del glyph_order  # reserved for future GID-ordered class builders
    forms = list(squishable) if squishable is not None else squishable_forms(cjk_bases)

    fe00_forms = _fe00_noop_form_names(forms, glyphs=glyphs, mark_cps=mark_cps)
    d4_map = d4_liga_map(cjk_bases, glyphs=glyphs, fe00_forms=fe00_forms)
    # Longer ligatures before shorter.
    by_len: Dict[int, Dict[Tuple[str, ...], str]] = {}
    for comps, out in squish_vs_liga_map(forms, glyphs=glyphs).items():
        by_len.setdefault(len(comps), {})[comps] = out
    if mark_cps:
        for comps, out in marked_liga_map(forms, mark_cps, glyphs=glyphs).items():
            by_len.setdefault(len(comps), {})[comps] = out
        # Mark D4 / FE08–A side slots stay in the 2-glyph lookup.
        by_len.setdefault(2, {}).update(mark_liga_map(mark_cps, glyphs))

    lookups: List = []
    if d4_map:
        lookups.append(build_chunked_ligature_subst_lookup(d4_map))
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
    """No cmap-14 UVS for FE0B–FE0F — access is GSUB liga (or PUA) only.

    UVS for ``FE0C``…``FE0F`` made browsers map ``base+FE0C`` → ``.dk`` after
    dropping ``FE0B``, so digraphs became two full-advance halves instead of
    ``.dk.ov`` + opposing niche. Plain squish / overlay / digraphs all use
    ``ccmp``/``rlig``/``liga``; galleries prefer PUA ``E008``…``E00C``.
    """
    del base_cp, base_glyph, glyphs
    return []


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
    base_cps: Optional[Sequence[int]] = None,
    in_dir: Optional[str] = None,
) -> List[str]:
    """Ensure squish forms + FE0B–FE0F overlay/squish access (liga + UVS).

    Returns the squishable form name list (identity + D4).
    """
    del in_dir  # kept for call-site compat

    for cp, name in (
        (OV_SELECTOR_CP, OV_SELECTOR_NAME),
        (SQUISH_RIGHT_CP, SQUISH_RIGHT_NAME),
        (SQUISH_LEFT_CP, SQUISH_LEFT_NAME),
        (SQUISH_TOP_CP, SQUISH_TOP_NAME),
        (SQUISH_BOT_CP, SQUISH_BOT_NAME),
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
    add_squish_forms(
        squishable,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        width_factor=width_factor,
        height_factor=height_factor,
        target_upem=target_upem,
        slot_frac=slot_frac,
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
    add_overlay_forms(
        ov_sources,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
    )

    # Ligatures installed programmatically in ``install_cjk_composition_gsub``.
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
    width_factor: float = MARK_BASE_SQUISH_FACTOR,
    height_factor: float = MARK_BASE_SQUISH_FACTOR,
    mark_niche_frac: float = MARK_NICHE_FRAC,
) -> Optional[Dict]:
    """Load ca/nhay + niche squish for the base CJK face.

    Default niche is **1/4** of the cell (mark) with the base occupying **3/4**.
    Half-cell digraph access (``.dk*`` at 0.55) lives on the ``h`` face instead.
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

    for cp in list(core_glyphs):
        # CAPE into the mark niche (default 1/4 of the cell).
        core_glyphs[cp] = fit_mark_to_halfcell(
            core_glyphs[cp],
            target_upem,
            axis="x",
            glyph_set=None,
            niche_frac=mark_niche_frac,
        )

    set_mark_cps(core_cps)

    for cp, name in (
        (ALT_SELECTOR_CP, ALT_SELECTOR_NAME),
        (TOP_SELECTOR_CP, TOP_SELECTOR_NAME),
        (BOT_SELECTOR_CP, BOT_SELECTOR_NAME),
    ):
        _ensure_side_selector(
            cp,
            name,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
        )

    right_marks, left_marks, top_marks, bottom_marks = add_mark_glyphs(
        core_cps,
        core_glyphs,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        cmap=cmap,
        target_upem=target_upem,
    )

    squishable = prepare_squish_vs_access(
        cjk_bases=cjk_bases,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        cmap=cmap,
        target_upem=target_upem,
        liga_rules=liga_rules,
        uvs_rows=uvs_rows,
        width_factor=width_factor,
        height_factor=height_factor,
        slot_frac=width_factor,
        in_dir=in_dir,
    )

    marked = add_marked_composites(
        squishable,
        core_cps,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
        niche_frac=mark_niche_frac,
    )
    if marked:
        add_overlay_forms(
            marked,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
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
        "width_factor": width_factor,
        "height_factor": height_factor,
        "mark_niche_frac": mark_niche_frac,
        "n_core": len(core_cps),
        "n_shared": 0,
        "shared_cps": [],
        "shared_mark_names": [],
        "shared_label": "",
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
    """Legacy FE08–A side-squish chains only; ca/nhay use precomposed ligas.

    Marked composites + their ligatures are installed in
    ``prepare_marks`` / ``install_cjk_composition_gsub``. Niche MarkToBase
    GPOS is no longer used.
    """
    del metrics, target_upem
    # Keep L/T/B chain squish for legacy ``FE08/FE09/FE0A MARK`` without niche VS.
    n = install_squish_gsub(
        font,
        squishable_bases=state["squishable"],
        right_marks=state.get("right_marks", []),
        left_marks=state.get("left_marks", []),
        top_marks=state.get("top_marks", []),
        bottom_marks=state.get("bottom_marks", []),
        glyphs=glyphs,
        glyph_order=glyph_order,
    )
    return n
