"""Reading marks for Pan-CJK subfonts (Plangothic P2).

Vietnamese U+16FF0 (ca) / U+16FF1 (nhay) only::

    CJK  ( VS )?  MARK  ( VS )?
        → right; base ``.dk``; upright mark

    CJK  ( VS )?  FE08  MARK  ( VS )?
        → left; base ``.dkl``; mark ``.L``

    CJK  ( VS )?  FE09  MARK  ( VS )?
        → top; base ``.dkt``; mark ``.T`` (r90 of ca/nhay)

    CJK  ( VS )?  FE0A  MARK  ( VS )?
        → bottom; base ``.dkb``; mark ``.B`` (r90 of ca/nhay)

One niche only (no FE08 overlay in panCJK). CAPE Width/Height preserve
cross-axis stems. Identity marks and FE08–FE0A slots ligate into one
precomposed glyph; mark D4 still uses chain-squish + GPOS as fallback.
"""

from __future__ import annotations

import os
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
    ttglyph_from_layer,
)
from yi_halfwidth import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    TRANSFORM_MODES,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    add_d4_variant_glyphs,
    build_ext_gsub_lookup,
    build_chunked_single_subst_lookup,
    build_chain_context_format2,
    empty_glyph,
    orientation_form_names,
    recording_bounds,
    variant_glyph_name,
    vs_glyph_name,
)

PLANGOTHIC_P2_FILENAME = "PlangothicP2-Regular.ttf"
VIET_MARK_CPS: Tuple[int, ...] = (0x16FF0, 0x16FF1)
VIET_LR_MARK_CPS: Tuple[int, ...] = VIET_MARK_CPS  # compat export
# Side selectors (FE08–FE0A; panCJK no longer uses FE08 overlay).
VIET_ALT_SELECTOR_CP = 0xFE08
VIET_ALT_SELECTOR_NAME = "vsLeft"  # left niche
VIET_LEFT_SELECTOR_CP = VIET_ALT_SELECTOR_CP  # compat export
VIET_LEFT_SELECTOR_NAME = VIET_ALT_SELECTOR_NAME
VIET_TOP_SELECTOR_CP = 0xFE09
VIET_TOP_SELECTOR_NAME = "vsTop"
VIET_BOT_SELECTOR_CP = 0xFE0A
VIET_BOT_SELECTOR_NAME = "vsBot"
VIET_SIDE_SELECTOR_CPS: Tuple[int, ...] = (
    VIET_ALT_SELECTOR_CP,
    VIET_TOP_SELECTOR_CP,
    VIET_BOT_SELECTOR_CP,
)

# Full D4 (identity + VS02..VS08 / FE01..FE07), including r90my.
VIET_BASE_VS_MODE_COUNT = 8
# Fallback when mark size is unknown; normally computed from mark ink.
VIET_SQUISH_FACTOR = 0.72
VIET_SQUISH_FACTOR_MIN = 0.52
VIET_SQUISH_FACTOR_MAX = 0.88
VIET_EDGE_PAD_FRAC = 0.03
VIET_GAP_FRAC = 0.02
# Top/bottom marks span nearly the full ideograph cell (not the narrow side niche).
VIET_TB_MARK_WIDTH_FRAC = 0.90

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


def viet_squish_name(base_name: str) -> str:
    """Left-squished form (right niche) — ``my`` composite of ``.dkl``."""
    return f"{base_name}.dk"


def viet_squish_left_name(base_name: str) -> str:
    """Right-squished form (left niche); canonical LR bake."""
    return f"{base_name}.dkl"


def viet_squish_top_name(base_name: str) -> str:
    """Bottom-squished form (top niche); canonical TB bake."""
    return f"{base_name}.dkt"


def viet_squish_bot_name(base_name: str) -> str:
    """Top-squished form (bottom niche) — ``mx`` composite of ``.dkt``."""
    return f"{base_name}.dkb"


def viet_left_mark_name(mark_name: str) -> str:
    return f"{mark_name}.L"


def viet_top_mark_name(mark_name: str) -> str:
    return f"{mark_name}.T"


def viet_bottom_mark_name(mark_name: str) -> str:
    return f"{mark_name}.B"


def viet_liga_name(base_name: str, mark_name: str) -> str:
    """Precomposed ``base + mark`` ligature glyph name."""
    return f"{base_name}_{mark_name}"


def _is_upright_liga_mark(name: str) -> bool:
    """Identity mark or FE08/FE09/FE0A slot — not mark D4 variants."""
    if "." not in name:
        return name.startswith("u")
    base, suf = name.rsplit(".", 1)
    return "." not in base and suf in ("L", "T", "B")


def make_viet_tb_mark_glyph(
    glyph: TTGlyph,
    *,
    target_upem: int,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[TTGlyph, int]:
    """r90 of upright ca/nhay, Width-expanded to nearly full ideograph width.

    Side D4 ``r90`` forms stay niche-narrow; TB slots need the mark to read as
    a full-width top/bottom diacritic. Ink center stays at the origin.
    """
    from yi_halfwidth import make_composite_variant

    target_w = float(target_upem) * VIET_TB_MARK_WIDTH_FRAC
    rotated, _adv, _lsb = make_composite_variant(
        "_tb",
        target_upem,
        rot90_quarters=1,
        advance=0,
        lsb=0,
        base_glyph=glyph,
        glyph_set=glyph_set,
        center=(0.0, 0.0),
    )
    layer = layer_from_ttglyph(rotated, 0.0)
    if not layer.paths:
        try:
            rotated.recalcBounds(None)
            return rotated, int(rotated.xMin)
        except Exception:
            return rotated, 0

    bw = layer.bounds.size.x
    if bw > 1.0 and target_w > 1.0:
        factor = target_w / bw
        if abs(factor - 1.0) > 1e-6:
            vstem = estimate_vertical_stem(layer)
            apply_width(
                layer,
                factor,
                stem=vstem if vstem > 0 else None,
                center_x=0.0,
            )

    b = layer.bounds
    cx = b.origin.x + 0.5 * b.size.x
    cy = b.origin.y + 0.5 * b.size.y
    if abs(cx) > 1e-6 or abs(cy) > 1e-6:
        layer.applyTransform((1, 0, 0, 1, -cx, -cy))

    out, _a, out_lsb = ttglyph_from_layer(layer)
    return out, int(out_lsb)


def _liga_mark_component(
    mark_name: str,
    *,
    side: str,
    ax: int,
    ay: int,
    glyphs: Dict[str, TTGlyph],
) -> GlyphComponent:
    mx, my = mark_attach_anchor(glyphs[mark_name], side=side, glyph_set=glyphs)
    comp = GlyphComponent()
    comp.glyphName = mark_name
    comp.x = int(ax - mx)
    comp.y = int(ay - my)
    comp.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    return comp


def make_viet_liga_glyph(
    squish_name: str,
    mark_name: str,
    *,
    side: str,
    ax: int,
    ay: int,
    glyphs: Dict[str, TTGlyph],
) -> TTGlyph:
    """Composite: squished base (metrics) + mark at GPOS-equivalent offset."""
    g = TTGlyph()
    g.numberOfContours = -1
    base = GlyphComponent()
    base.glyphName = squish_name
    base.x = 0
    base.y = 0
    base.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET | USE_MY_METRICS
    g.components = [
        base,
        _liga_mark_component(
            mark_name, side=side, ax=ax, ay=ay, glyphs=glyphs
        ),
    ]
    return g


def viet_base_orientation_modes(
    modes: Optional[Sequence] = None,
) -> List:
    use = list(modes) if modes is not None else list(TRANSFORM_MODES)
    return use[:VIET_BASE_VS_MODE_COUNT]


def viet_squishable_forms(
    cjk_bases: Sequence[str],
    *,
    modes=None,
) -> List[str]:
    """Identity + all D4 forms (VS01..VS08) that may take a Viet mark."""
    names: List[str] = []
    for base in cjk_bases:
        names.extend(
            orientation_form_names(base, modes=viet_base_orientation_modes(modes))
        )
    return names


def glyph_name_for_cp(cp: int) -> str:
    return f"u{cp:04X}" if cp <= 0xFFFF else f"u{cp:05X}"


def _bake_simple_glyph(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]],
) -> TTGlyph:
    if not glyph.isComposite():
        return glyph
    from yi_halfwidth import _recording_from_glyph

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


def _normalize_winding(glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]] = None) -> TTGlyph:
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


def make_viet_mark_glyph(
    rec: RecordingPen,
    *,
    scale: float,
) -> Optional[TTGlyph]:
    """Scale mark outline and pin ink center to ``(0, 0)`` (GPOS mark anchor)."""
    from yi_halfwidth import apply_transform

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


def load_viet_marks(
    plangothic_path: str,
    target_upem: int,
    *,
    local_scale: float = 0.96,
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
        scale = (float(target_upem) / src_upem) * float(local_scale) if src_upem else 1.0

        cps: List[int] = []
        glyphs: Dict[int, TTGlyph] = {}
        for cp in VIET_MARK_CPS:
            gname = cmap.get(cp)
            if gname is None:
                continue
            rec = DecomposingRecordingPen(glyph_set)
            try:
                glyph_set[gname].draw(rec)
            except Exception:
                continue
            mark = make_viet_mark_glyph(rec, scale=scale)
            if mark is None:
                continue
            cps.append(cp)
            glyphs[cp] = mark
        return cps, glyphs
    finally:
        tt.close()


def mark_ink_width(glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]] = None) -> float:
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        return float(glyph.xMax - glyph.xMin)
    except Exception:
        return 0.0


def mark_ink_height(glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]] = None) -> float:
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        return float(glyph.yMax - glyph.yMin)
    except Exception:
        return 0.0


def viet_squish_factor_for_marks(
    mark_names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    target_upem: int,
    axis: str = "x",
) -> float:
    """Width/height factor so one side niche fits the largest mark + pads."""
    if axis == "y":
        sizes = [
            mark_ink_height(glyphs[n], glyphs) for n in mark_names if n in glyphs
        ]
    else:
        sizes = [
            mark_ink_width(glyphs[n], glyphs) for n in mark_names if n in glyphs
        ]
    max_s = max((s for s in sizes if s > 1.0), default=0.0)
    if max_s <= 1.0:
        return VIET_SQUISH_FACTOR
    gap = target_upem * VIET_GAP_FRAC
    edge = target_upem * VIET_EDGE_PAD_FRAC
    side_pad = target_upem * 0.06
    niche = max_s + gap + edge
    usable = max(target_upem - side_pad - niche, target_upem * 0.35)
    factor = usable / max(target_upem - side_pad, 1.0)
    return float(
        min(VIET_SQUISH_FACTOR_MAX, max(VIET_SQUISH_FACTOR_MIN, factor))
    )


def mark_attach_anchor(
    glyph: TTGlyph,
    *,
    side: str = "right",
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[int, int]:
    """Mark GPOS anchor on the edge toward the base niche."""
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        cx = (float(glyph.xMin) + float(glyph.xMax)) / 2.0
        cy = (float(glyph.yMin) + float(glyph.yMax)) / 2.0
        if side == "right":
            return otRound(float(glyph.xMin)), otRound(cy)
        if side == "left":
            return otRound(float(glyph.xMax)), otRound(cy)
        if side == "top":
            return otRound(cx), otRound(float(glyph.yMin))
        if side == "bottom":
            return otRound(cx), otRound(float(glyph.yMax))
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


def add_viet_mark_glyphs(
    mark_cps: Sequence[int],
    mark_glyphs: Dict[int, TTGlyph],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Install upright marks + D4 + FE08/FE09/FE0A slot aliases.

    Returns ``(right, left, top, bottom)`` mark form names.
    Top/bottom slots are full-width r90 of upright ca/nhay (not niche-narrow
    side ``r90``).
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
        installed = add_d4_variant_glyphs(
            name,
            advance=0,
            lsb=metrics[name][1],
            target_upem=target_upem,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
        for _vs, _sfx, vname in installed:
            metrics[vname] = (0, metrics[vname][1])
            right_names.append(vname)

    # Full-width TB outlines once per upright mark; D4 slots alias them.
    upright_tb: Dict[str, str] = {}
    top_names: List[str] = []
    bottom_names: List[str] = []
    for name in upright:
        tn = viet_top_mark_name(name)
        bn = viet_bottom_mark_name(name)
        if tn not in glyphs:
            wide, lsb = make_viet_tb_mark_glyph(
                glyphs[name], target_upem=target_upem, glyph_set=glyphs
            )
            glyph_order.append(tn)
            glyphs[tn] = wide
            metrics[tn] = (0, lsb)
        _install_mark_slot(
            bn, tn, glyph_order=glyph_order, glyphs=glyphs, metrics=metrics
        )
        upright_tb[name] = tn
        if tn in glyphs:
            top_names.append(tn)
        if bn in glyphs:
            bottom_names.append(bn)

    left_names: List[str] = []
    for name in list(right_names):
        ln = viet_left_mark_name(name)
        _install_mark_slot(
            ln, name, glyph_order=glyph_order, glyphs=glyphs, metrics=metrics
        )
        if ln in glyphs:
            left_names.append(ln)

        if name in upright_tb:
            continue
        root = name.split(".", 1)[0]
        wide = upright_tb.get(root)
        if wide is None:
            continue
        tn = viet_top_mark_name(name)
        bn = viet_bottom_mark_name(name)
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


def viet_mark_liga_rules(
    mark_cps: Sequence[int],
    glyphs: Dict[str, TTGlyph],
) -> List[str]:
    """FEA mark D4 ligas + FE08/FE09/FE0A → side slots."""
    rules: List[str] = []
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
            rules.append(f"  sub {base} {vs_glyph_name(vs_cp)} by {vname};")
    for name in _mark_forms_for_cps(mark_cps, glyphs):
        ln = viet_left_mark_name(name)
        if ln in glyphs:
            rules.append(f"  sub {VIET_ALT_SELECTOR_NAME} {name} by {ln};")
        tn = viet_top_mark_name(name)
        if tn in glyphs:
            rules.append(f"  sub {VIET_TOP_SELECTOR_NAME} {name} by {tn};")
        bn = viet_bottom_mark_name(name)
        if bn in glyphs:
            rules.append(f"  sub {VIET_BOT_SELECTOR_NAME} {name} by {bn};")
    return rules


def make_viet_squished_glyph(
    glyph: TTGlyph,
    advance: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    factor: float = VIET_SQUISH_FACTOR,
    pin: str = "left",
    axis: str = "x",
) -> Tuple[TTGlyph, int, int]:
    """Condense with CAPE Width (``axis="x"``) or Height (``axis="y"``).

    ``pin`` is ``left``/``right`` (X) or ``top``/``bottom`` (Y).
    """
    simple = _normalize_winding(_bake_simple_glyph(glyph, glyph_set), glyph_set)
    layer = layer_from_ttglyph(simple, float(advance))
    if not layer.paths:
        return simple, int(advance), int(getattr(simple, "xMin", 0) or 0)

    hstem = estimate_horizontal_stem(layer)
    vstem = estimate_vertical_stem(layer)
    b0 = layer.bounds
    left0 = b0.origin.x
    right0 = b0.origin.x + b0.size.x
    bot0 = b0.origin.y
    top0 = b0.origin.y + b0.size.y

    if axis == "y":
        apply_height(layer, factor, stem=hstem if hstem > 0 else None)
        nb = layer.bounds
        if pin == "top":
            dy = top0 - (nb.origin.y + nb.size.y)
        else:
            dy = bot0 - nb.origin.y
        if abs(dy) > 1e-6:
            layer.applyTransform((1, 0, 0, 1, 0, dy))
    else:
        apply_width(layer, factor, stem=vstem if vstem > 0 else None)
        nb = layer.bounds
        if pin == "right":
            dx = right0 - (nb.origin.x + nb.size.x)
        else:
            dx = left0 - nb.origin.x
        if abs(dx) > 1e-6:
            layer.applyTransform((1, 0, 0, 1, dx, 0))
            layer.LSB = layer.LSB + dx

    out, _adv, lsb = ttglyph_from_layer(layer)
    return out, int(advance), int(lsb)


def add_viet_squish_forms(
    base_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    width_factor: float = VIET_SQUISH_FACTOR,
    height_factor: float = VIET_SQUISH_FACTOR,
    target_upem: int = 1000,
) -> List[str]:
    """Create squish forms: bake ``.dkl``/``.dkt``; mirror to ``.dk``/``.dkb``.

    Left (``.dkl``) and top (``.dkt``) are the CAPE-baked defaults. Right
    (``.dk``) is a ``my`` composite of ``.dkl``; bottom (``.dkb``) is an ``mx``
    composite of ``.dkt``.
    """
    from yi_halfwidth import make_composite_variant

    added: List[str] = []
    for name in base_names:
        if name not in glyphs:
            continue
        adv, _lsb = metrics.get(name, (target_upem, 0))
        src = glyphs[name]

        left_name = viet_squish_left_name(name)
        right_name = viet_squish_name(name)
        top_name = viet_squish_top_name(name)
        bot_name = viet_squish_bot_name(name)

        if left_name not in glyphs:
            sq, sq_adv, sq_lsb = make_viet_squished_glyph(
                src,
                adv,
                glyph_set=glyphs,
                factor=width_factor,
                pin="right",
                axis="x",
            )
            glyph_order.append(left_name)
            glyphs[left_name] = sq
            metrics[left_name] = (sq_adv, sq_lsb)

        if right_name not in glyphs and left_name in glyphs:
            # my: reflect X about ideographic center (left↔right niche).
            mirrored, m_adv, m_lsb = make_composite_variant(
                left_name,
                target_upem,
                flip_y=True,
                advance=metrics[left_name][0],
                lsb=metrics[left_name][1],
                base_glyph=glyphs[left_name],
                glyph_set=glyphs,
            )
            glyph_order.append(right_name)
            glyphs[right_name] = mirrored
            metrics[right_name] = (m_adv, m_lsb)

        if top_name not in glyphs:
            sq, sq_adv, sq_lsb = make_viet_squished_glyph(
                src,
                adv,
                glyph_set=glyphs,
                factor=height_factor,
                pin="bottom",
                axis="y",
            )
            glyph_order.append(top_name)
            glyphs[top_name] = sq
            metrics[top_name] = (sq_adv, sq_lsb)

        if bot_name not in glyphs and top_name in glyphs:
            # mx: reflect Y about ideographic center (top↔bottom niche).
            mirrored, m_adv, m_lsb = make_composite_variant(
                top_name,
                target_upem,
                flip_x=True,
                advance=metrics[top_name][0],
                lsb=metrics[top_name][1],
                base_glyph=glyphs[top_name],
                glyph_set=glyphs,
            )
            glyph_order.append(bot_name)
            glyphs[bot_name] = mirrored
            metrics[bot_name] = (m_adv, m_lsb)

        added.append(name)
    return added


def add_viet_liga_forms(
    squishable_bases: Sequence[str],
    *,
    right_marks: Sequence[str],
    left_marks: Sequence[str],
    top_marks: Sequence[str],
    bottom_marks: Sequence[str],
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
) -> Dict[Tuple[str, str], str]:
    """Build precomposed ``base + mark`` composites (identity / FE08–FE0A slots).

    Returns ``{(base, mark): liga_name}`` for GSUB LigatureSubst.
    """
    side_groups = (
        (right_marks, viet_squish_name, "right", cjk_right_anchor),
        (left_marks, viet_squish_left_name, "left", cjk_left_anchor),
        (top_marks, viet_squish_top_name, "top", cjk_top_anchor),
        (bottom_marks, viet_squish_bot_name, "bottom", cjk_bottom_anchor),
    )
    liga_map: Dict[Tuple[str, str], str] = {}
    for mark_names, squish_fn, side, anchor_fn in side_groups:
        marks = [n for n in mark_names if _is_upright_liga_mark(n) and n in glyphs]
        if not marks:
            continue
        for base in squishable_bases:
            sq = squish_fn(base)
            if base not in glyphs or sq not in glyphs:
                continue
            adv, _ = metrics.get(sq, (target_upem, 0))
            ax, ay = anchor_fn(glyphs[sq], adv, target_upem, glyph_set=glyphs)
            sq_adv, sq_lsb = metrics.get(sq, (target_upem, 0))
            for mark in marks:
                liga = viet_liga_name(base, mark)
                if liga not in glyphs:
                    glyph_order.append(liga)
                    glyphs[liga] = make_viet_liga_glyph(
                        sq,
                        mark,
                        side=side,
                        ax=ax,
                        ay=ay,
                        glyphs=glyphs,
                    )
                    metrics[liga] = (sq_adv, sq_lsb)
                liga_map[(base, mark)] = liga
    return liga_map


def cjk_right_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[int, int]:
    """Right-side attach point just past squished ink."""
    typo_top = target_upem * TYPO_ASCENDER_FRAC
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    right = float(advance) if advance > 0 else float(target_upem)
    gap = target_upem * VIET_GAP_FRAC
    edge = target_upem * VIET_EDGE_PAD_FRAC
    mid_y = (typo_top + typo_bot) / 2.0

    x1 = right * 0.65
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        x1 = float(glyph.xMax)
    except Exception:
        pass

    ax = min(max(x1 + gap, edge), right - edge)
    ay = min(max(mid_y, typo_bot + edge), typo_top - edge)
    return otRound(ax), otRound(ay)


def cjk_left_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[int, int]:
    """Left-side attach point just before squished ink."""
    typo_top = target_upem * TYPO_ASCENDER_FRAC
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    right = float(advance) if advance > 0 else float(target_upem)
    gap = target_upem * VIET_GAP_FRAC
    edge = target_upem * VIET_EDGE_PAD_FRAC
    mid_y = (typo_top + typo_bot) / 2.0

    x0 = right * 0.35
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        x0 = float(glyph.xMin)
    except Exception:
        pass

    ax = min(max(x0 - gap, edge), right - edge)
    ay = min(max(mid_y, typo_bot + edge), typo_top - edge)
    return otRound(ax), otRound(ay)


def cjk_top_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[int, int]:
    """Top-side attach point just above squished ink."""
    typo_top = target_upem * TYPO_ASCENDER_FRAC
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    right = float(advance) if advance > 0 else float(target_upem)
    gap = target_upem * VIET_GAP_FRAC
    edge = target_upem * VIET_EDGE_PAD_FRAC
    mid_x = right / 2.0

    y1 = typo_top * 0.65
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        y1 = float(glyph.yMax)
        mid_x = (float(glyph.xMin) + float(glyph.xMax)) / 2.0
    except Exception:
        pass

    ax = min(max(mid_x, edge), right - edge)
    ay = min(max(y1 + gap, typo_bot + edge), typo_top - edge)
    return otRound(ax), otRound(ay)


def cjk_bottom_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[int, int]:
    """Bottom-side attach point just below squished ink."""
    typo_top = target_upem * TYPO_ASCENDER_FRAC
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    right = float(advance) if advance > 0 else float(target_upem)
    gap = target_upem * VIET_GAP_FRAC
    edge = target_upem * VIET_EDGE_PAD_FRAC
    mid_x = right / 2.0

    y0 = typo_bot + (typo_top - typo_bot) * 0.35
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        y0 = float(glyph.yMin)
        mid_x = (float(glyph.xMin) + float(glyph.xMax)) / 2.0
    except Exception:
        pass

    ax = min(max(mid_x, edge), right - edge)
    ay = min(max(y0 - gap, typo_bot + edge), typo_top - edge)
    return otRound(ax), otRound(ay)


def collect_viet_base_anchors(
    squishable_bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    """Map squish forms → ``{mark_class: (x, y)}`` (one side each)."""
    anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for name in squishable_bases:
        mapping = (
            (viet_squish_name(name), MARK_CLASS_RIGHT, cjk_right_anchor),
            (viet_squish_left_name(name), MARK_CLASS_LEFT, cjk_left_anchor),
            (viet_squish_top_name(name), MARK_CLASS_TOP, cjk_top_anchor),
            (viet_squish_bot_name(name), MARK_CLASS_BOTTOM, cjk_bottom_anchor),
        )
        for form, cls, fn in mapping:
            if form not in glyphs:
                continue
            adv, _ = metrics.get(form, (target_upem, 0))
            anchors[form] = {
                cls: fn(glyphs[form], adv, target_upem, glyph_set=glyphs)
            }
    return anchors


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


def install_viet_mark_liga_gsub(
    font,
    liga_map: Dict[Tuple[str, str], str],
    *,
    glyph_order: Sequence[str],
    chunk_size: int = 4000,
) -> int:
    """``base + mark → precomposed`` ligatures in ``ccmp``/``rlig``/``liga``."""
    if not liga_map or "GSUB" not in font:
        return 0

    from fontTools.otlLib.builder import buildLigatureSubstSubtable, buildLookup

    order_index = {n: i for i, n in enumerate(glyph_order)}
    items = sorted(
        liga_map.items(),
        key=lambda kv: (
            order_index.get(kv[0][0], 10**9),
            order_index.get(kv[0][1], 10**9),
        ),
    )
    gsub = font["GSUB"].table
    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0

    feature_lookup_idxs: List[int] = []
    for i in range(0, len(items), max(1, chunk_size)):
        chunk = {pair: out for pair, out in items[i : i + chunk_size]}
        sub = buildLigatureSubstSubtable(chunk)
        lu = buildLookup([sub])
        lu.LookupType = 4
        idx = gsub.LookupList.LookupCount
        gsub.LookupList.Lookup.append(lu)
        gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
        feature_lookup_idxs.append(idx)

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
    return len(liga_map)


def install_viet_squish_gsub(
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
    """One-side squish (Format 2 chain + Extension) for LR and TB."""
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
            and viet_squish_name(n) in glyphs
            and viet_squish_left_name(n) in glyphs
            and viet_squish_top_name(n) in glyphs
            and viet_squish_bot_name(n) in glyphs
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

    def _append_chain(coverage, input_cls_map, input_cls, mapping, **chain_kw):
        if not coverage or not mapping:
            return
        single_lu = build_chunked_single_subst_lookup(mapping)
        st = build_chain_context_format2(
            coverage_glyphs=coverage,
            input_classes=input_cls_map,
            input_class=input_cls,
            **chain_kw,
        )
        chain_lu = build_ext_gsub_lookup([st])
        base_i = gsub.LookupList.LookupCount
        chain_i = base_i
        single_i = base_i + 1
        st.ChainSubClassSet[input_cls].ChainSubClassRule[0].SubstLookupRecord[
            0
        ].LookupListIndex = single_i
        gsub.LookupList.Lookup.extend([chain_lu, single_lu])
        gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
        feature_lookup_idxs.append(chain_i)

    pairs = (
        (marks_r, viet_squish_name),
        (marks_l, viet_squish_left_name),
        (marks_t, viet_squish_top_name),
        (marks_b, viet_squish_bot_name),
    )
    for marks, name_fn in pairs:
        if not marks:
            continue
        _append_chain(
            bases,
            {n: 1 for n in bases},
            1,
            {n: name_fn(n) for n in bases},
            lookahead_classes={n: 1 for n in marks},
            lookahead_seq=(1,),
        )

    if not feature_lookup_idxs:
        return 0

    tag_to_fr = {fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])}
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            continue
        idxs = list(fr.Feature.LookupListIndex or [])
        for chain_index in feature_lookup_idxs:
            if chain_index not in idxs:
                idxs.append(chain_index)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)
    return len(bases)


def install_viet_mark_gpos(
    font,
    *,
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]],
    right_marks: Sequence[str],
    left_marks: Sequence[str],
    top_marks: Sequence[str],
    bottom_marks: Sequence[str],
    glyph_order: Sequence[str],
    base_chunk: int = 2048,
) -> int:
    """MarkToBase: R/L/T/B mark classes."""
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
    glyf = font["glyf"] if "glyf" in font else {}
    marks = {}
    for n, side, cls in (
        *((n, "right", MARK_CLASS_RIGHT) for n in marks_r),
        *((n, "left", MARK_CLASS_LEFT) for n in marks_l),
        *((n, "top", MARK_CLASS_TOP) for n in marks_t),
        *((n, "bottom", MARK_CLASS_BOTTOM) for n in marks_b),
    ):
        mx, my = (
            mark_attach_anchor(glyf[n], side=side, glyph_set=glyf)
            if n in glyf
            else (0, 0)
        )
        marks[n] = (cls, buildAnchor(mx, my))

    subs = []
    for i in range(0, len(bases_sorted), max(1, base_chunk)):
        chunk = bases_sorted[i : i + base_chunk]
        bases = {}
        for n in chunk:
            class_map = {}
            for cls, (ax, ay) in base_anchors[n].items():
                class_map[cls] = buildAnchor(ax, ay)
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
        feature_indices.append(feature_index)
        idxs = list(fr.Feature.LookupListIndex or [])
        if lookup_index not in idxs:
            idxs.append(lookup_index)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)

    for sr in gpos.ScriptList.ScriptRecord:
        ls = sr.Script.DefaultLangSys
        if ls is None:
            ls = _langsys_with_features([])
            sr.Script.DefaultLangSys = ls
        fi = list(ls.FeatureIndex or [])
        for feature_index in feature_indices:
            if feature_index not in fi:
                fi.append(feature_index)
        ls.FeatureIndex = fi
        ls.FeatureCount = len(fi)

    _ensure_gdef_classes(
        font,
        bases=bases_sorted,
        marks=marks_r + marks_l + marks_t + marks_b,
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


def prepare_viet_marks(
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
    """Load ca/nhay + squish/liga forms; FE08/FE09/FE0A side selectors."""
    from yi_halfwidth import build_d4_uvs_entries

    try:
        path = resolve_plangothic_p2(in_dir)
    except FileNotFoundError:
        return None

    mark_cps, mark_glyphs = load_viet_marks(
        path, target_upem, local_scale=local_scale
    )
    if not mark_cps:
        return None

    for cp, name in (
        (VIET_ALT_SELECTOR_CP, VIET_ALT_SELECTOR_NAME),
        (VIET_TOP_SELECTOR_CP, VIET_TOP_SELECTOR_NAME),
        (VIET_BOT_SELECTOR_CP, VIET_BOT_SELECTOR_NAME),
    ):
        _ensure_side_selector(
            cp,
            name,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
        )

    right_marks, left_marks, top_marks, bottom_marks = add_viet_mark_glyphs(
        mark_cps,
        mark_glyphs,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        cmap=cmap,
        target_upem=target_upem,
    )

    liga_rules.extend(viet_mark_liga_rules(mark_cps, glyphs))
    if uvs_rows is not None:
        for cp in mark_cps:
            uvs_rows.extend(
                build_d4_uvs_entries(cp, glyph_name_for_cp(cp), glyphs=glyphs)
            )

    squishable = viet_squishable_forms(cjk_bases)
    width_factor = viet_squish_factor_for_marks(
        right_marks, glyphs=glyphs, target_upem=target_upem, axis="x"
    )
    height_factor = viet_squish_factor_for_marks(
        top_marks or right_marks, glyphs=glyphs, target_upem=target_upem, axis="y"
    )
    add_viet_squish_forms(
        squishable,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        width_factor=width_factor,
        height_factor=height_factor,
        target_upem=target_upem,
    )
    liga_map = add_viet_liga_forms(
        squishable,
        right_marks=right_marks,
        left_marks=left_marks,
        top_marks=top_marks,
        bottom_marks=bottom_marks,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )
    return {
        "right_marks": list(right_marks),
        "left_marks": list(left_marks),
        "top_marks": list(top_marks),
        "bottom_marks": list(bottom_marks),
        "squishable": list(squishable),
        "width_factor": width_factor,
        "height_factor": height_factor,
        "liga_map": liga_map,
    }


def compile_viet_marks_layout(
    font,
    state: Dict,
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    glyph_order: Sequence[str],
    target_upem: int,
) -> int:
    """Install base+mark ligatures (primary); chain-squish + GPOS for mark D4."""
    n_liga = install_viet_mark_liga_gsub(
        font,
        state.get("liga_map") or {},
        glyph_order=glyph_order,
    )
    install_viet_squish_gsub(
        font,
        squishable_bases=state["squishable"],
        right_marks=state.get("right_marks", []),
        left_marks=state.get("left_marks", []),
        top_marks=state.get("top_marks", []),
        bottom_marks=state.get("bottom_marks", []),
        glyphs=glyphs,
        glyph_order=glyph_order,
    )
    anchors = collect_viet_base_anchors(
        state["squishable"],
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )
    install_viet_mark_gpos(
        font,
        base_anchors=anchors,
        right_marks=state.get("right_marks", []),
        left_marks=state.get("left_marks", []),
        top_marks=state.get("top_marks", []),
        bottom_marks=state.get("bottom_marks", []),
        glyph_order=glyph_order,
    )
    return n_liga
