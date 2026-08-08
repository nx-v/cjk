"""Vietnamese combining marks U+16FF0 / U+16FF1 for Pan-CJK subfonts.

Sourced from Plangothic P2. Marks attach on **one** side only (never both)::

    CJK  ( VS01..VS08 )?  ( U+16FF0 | U+16FF1 )  ( VS01..VS08 )?
        → right attach; ideograph left-squished (``.dk``)

    CJK  ( VS01..VS08 )?  FE09  ( U+16FF0 | U+16FF1 )  ( VS01..VS08 )?
        → left attach; ideograph right-squished (``.dkl``)

* CAPE Weightor Width mode preserves vertical stem thickness.
* Marks take full D4: ``r90`` Weightor-fit about the mark origin; other
  sideways forms are composites of that ``r90``.
* GPOS: right marks hang in the niche on the right of ``.dk``; left marks
  hang in the niche on the left of ``.dkl``.
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
    Glyph as TTGlyph,
    GlyphComponent,
)

from cape_weightor import (
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
    average_ink_width,
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
# Left-only side switch (VS10); FE08 is already overlay in subfonts.
VIET_LEFT_SELECTOR_CP = 0xFE09
VIET_LEFT_SELECTOR_NAME = "vsLeft"

# Full D4 (identity + VS02..VS08 / FE01..FE07), including r90my.
VIET_BASE_VS_MODE_COUNT = 8
# Fallback when mark width is unknown; normally computed from mark ink.
VIET_SQUISH_FACTOR = 0.72
VIET_SQUISH_FACTOR_MIN = 0.52
VIET_SQUISH_FACTOR_MAX = 0.88
VIET_EDGE_PAD_FRAC = 0.03
VIET_GAP_FRAC = 0.02

GDEF_CLASS_BASE = 1
GDEF_CLASS_MARK = 3
MARK_CLASS_RIGHT = 0
MARK_CLASS_LEFT = 1
MARK_FEATURE_TAGS: Tuple[str, ...] = ("mark", "abvm")


def resolve_plangothic_p2(in_dir: str) -> str:
    path = os.path.join(in_dir, PLANGOTHIC_P2_FILENAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing Plangothic P2: {path}")
    return path


def viet_squish_name(base_name: str) -> str:
    """Left-squished form (right niche)."""
    return f"{base_name}.dk"


def viet_squish_left_name(base_name: str) -> str:
    """Right-squished form (left niche)."""
    return f"{base_name}.dkl"


def viet_left_mark_name(mark_name: str) -> str:
    return f"{mark_name}.L"


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


def viet_squish_factor_for_marks(
    mark_names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    target_upem: int,
) -> float:
    """Width factor so one side niche fits the widest mark + pads."""
    widths = [
        mark_ink_width(glyphs[n], glyphs) for n in mark_names if n in glyphs
    ]
    max_w = max((w for w in widths if w > 1.0), default=0.0)
    if max_w <= 1.0:
        return VIET_SQUISH_FACTOR
    gap = target_upem * VIET_GAP_FRAC
    edge = target_upem * VIET_EDGE_PAD_FRAC
    side_pad = target_upem * 0.06
    niche = max_w + gap + edge
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
    """Mark GPOS anchor.

    * ``side="right"`` — left ink edge (mark hangs to the right of the base).
    * ``side="left"`` — right ink edge (mark hangs to the left of the base).
    """
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        ax = float(glyph.xMin if side == "right" else glyph.xMax)
        ay = (float(glyph.yMin) + float(glyph.yMax)) / 2.0
        return otRound(ax), otRound(ay)
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


def add_viet_mark_glyphs(
    mark_cps: Sequence[int],
    mark_glyphs: Dict[int, TTGlyph],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int,
) -> Tuple[List[str], List[str]]:
    """Install upright marks + D4 + left-slot ``.L`` aliases.

    Returns ``(right_mark_names, left_mark_names)``.
    """
    upright: List[str] = []
    upright_glyphs: List[TTGlyph] = []
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
        upright_glyphs.append(g)

    avg_w = average_ink_width(upright_glyphs)
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
            sideways_target_width=avg_w if avg_w > 1.0 else None,
            sideways_center_x=0.0,
        )
        for _vs, _sfx, vname in installed:
            metrics[vname] = (0, metrics[vname][1])
            right_names.append(vname)

    left_names: List[str] = []
    for name in list(right_names):
        ln = viet_left_mark_name(name)
        if ln in glyphs:
            left_names.append(ln)
            continue
        glyph_order.append(ln)
        glyphs[ln] = _mark_slot_composite(name)
        metrics[ln] = (0, metrics[name][1])
        left_names.append(ln)
    return right_names, left_names


def viet_mark_liga_rules(mark_cps: Sequence[int], glyphs: Dict[str, TTGlyph]) -> List[str]:
    """FEA ``sub mark vsNN by mark.suffix`` + ``sub vsLeft mark by mark.L``."""
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
    # FE09 + any right mark → left-slot mark (left-only encoding).
    right_forms: List[str] = []
    for cp in mark_cps:
        base = glyph_name_for_cp(cp)
        if base in glyphs:
            right_forms.append(base)
        for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            if suffix is None:
                continue
            vname = variant_glyph_name(base, suffix)
            if vname in glyphs:
                right_forms.append(vname)
    for name in right_forms:
        ln = viet_left_mark_name(name)
        if ln in glyphs:
            rules.append(f"  sub {VIET_LEFT_SELECTOR_NAME} {name} by {ln};")
    return rules


def make_viet_squished_glyph(
    glyph: TTGlyph,
    advance: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    factor: float = VIET_SQUISH_FACTOR,
    pin: str = "left",
) -> Tuple[TTGlyph, int, int]:
    """Width-condense with CAPE; keep em advance.

    ``pin="left"`` / ``"right"`` pins that ink edge after squish.
    """
    simple = _normalize_winding(_bake_simple_glyph(glyph, glyph_set), glyph_set)
    layer = layer_from_ttglyph(simple, float(advance))
    if not layer.paths:
        return simple, int(advance), int(getattr(simple, "xMin", 0) or 0)

    _ = estimate_horizontal_stem(layer)
    vstem = estimate_vertical_stem(layer)
    b0 = layer.bounds
    left0 = b0.origin.x
    right0 = b0.origin.x + b0.size.x

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
    factor: float = VIET_SQUISH_FACTOR,
    target_upem: int = 1000,
) -> List[str]:
    """Create ``.dk`` (left-squish) and ``.dkl`` (right-squish) forms."""
    added: List[str] = []
    for name in base_names:
        if name not in glyphs:
            continue
        adv, _lsb = metrics.get(name, (target_upem, 0))
        src = glyphs[name]

        dk = viet_squish_name(name)
        if dk not in glyphs:
            sq, sq_adv, sq_lsb = make_viet_squished_glyph(
                src, adv, glyph_set=glyphs, factor=factor, pin="left"
            )
            glyph_order.append(dk)
            glyphs[dk] = sq
            metrics[dk] = (sq_adv, sq_lsb)

        dkl = viet_squish_left_name(name)
        if dkl not in glyphs:
            sq, sq_adv, sq_lsb = make_viet_squished_glyph(
                src, adv, glyph_set=glyphs, factor=factor, pin="right"
            )
            glyph_order.append(dkl)
            glyphs[dkl] = sq
            metrics[dkl] = (sq_adv, sq_lsb)

        added.append(name)
    return added


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
        dk = viet_squish_name(name)
        dkl = viet_squish_left_name(name)
        if dk in glyphs:
            adv, _ = metrics.get(dk, (target_upem, 0))
            anchors[dk] = {
                MARK_CLASS_RIGHT: cjk_right_anchor(
                    glyphs[dk], adv, target_upem, glyph_set=glyphs
                )
            }
        if dkl in glyphs:
            adv, _ = metrics.get(dkl, (target_upem, 0))
            anchors[dkl] = {
                MARK_CLASS_LEFT: cjk_left_anchor(
                    glyphs[dkl], adv, target_upem, glyph_set=glyphs
                )
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


def install_viet_squish_gsub(
    font,
    *,
    squishable_bases: Sequence[str],
    right_marks: Sequence[str],
    left_marks: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
) -> int:
    """One-side squish (Format 2 chain + Extension).

    * ``base' markR → base.dk``  (right attach)
    * ``base' markL → base.dkl`` (left attach)
    """
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
        ]
    )
    marks_r = _gid_sort([n for n in right_marks if n in glyphs])
    marks_l = _gid_sort([n for n in left_marks if n in glyphs])
    if not bases or (not marks_r and not marks_l):
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

    # base' + markR → .dk
    if marks_r:
        _append_chain(
            bases,
            {n: 1 for n in bases},
            1,
            {n: viet_squish_name(n) for n in bases},
            lookahead_classes={n: 1 for n in marks_r},
            lookahead_seq=(1,),
        )
    # base' + markL → .dkl
    if marks_l:
        _append_chain(
            bases,
            {n: 1 for n in bases},
            1,
            {n: viet_squish_left_name(n) for n in bases},
            lookahead_classes={n: 1 for n in marks_l},
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
    glyph_order: Sequence[str],
    base_chunk: int = 2048,
) -> int:
    """MarkToBase: class 0 = right niche, class 1 = left niche."""
    if not base_anchors or (not right_marks and not left_marks):
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
    marks_r = [
        n
        for n in sorted(set(right_marks), key=lambda n: order_index.get(n, 10**9))
        if n in order_index
    ]
    marks_l = [
        n
        for n in sorted(set(left_marks), key=lambda n: order_index.get(n, 10**9))
        if n in order_index
    ]
    bases_sorted = [
        n
        for n in sorted(base_anchors, key=lambda n: order_index.get(n, 10**9))
        if n in order_index
    ]
    if (not marks_r and not marks_l) or not bases_sorted:
        return 0

    glyph_map = {n: i for i, n in enumerate(glyph_order)}
    glyf = font["glyf"] if "glyf" in font else {}
    marks = {}
    for n in marks_r:
        mx, my = (
            mark_attach_anchor(glyf[n], side="right", glyph_set=glyf)
            if n in glyf
            else (0, 0)
        )
        marks[n] = (MARK_CLASS_RIGHT, buildAnchor(mx, my))
    for n in marks_l:
        mx, my = (
            mark_attach_anchor(glyf[n], side="left", glyph_set=glyf)
            if n in glyf
            else (0, 0)
        )
        marks[n] = (MARK_CLASS_LEFT, buildAnchor(mx, my))

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
        marks=marks_r + marks_l,
        glyph_order=glyph_order,
    )
    return len(bases_sorted)


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
    """Load marks + squish forms and append mark D4 / FE09 ligas.

    Returns state for ``compile_viet_marks_layout``, or ``None`` if skipped.
    """
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

    # FE09 left-only selector (zero-advance).
    if VIET_LEFT_SELECTOR_NAME not in glyphs:
        glyph_order.append(VIET_LEFT_SELECTOR_NAME)
        glyphs[VIET_LEFT_SELECTOR_NAME] = empty_glyph()
        metrics[VIET_LEFT_SELECTOR_NAME] = (0, 0)
    cmap[VIET_LEFT_SELECTOR_CP] = VIET_LEFT_SELECTOR_NAME

    right_marks, left_marks = add_viet_mark_glyphs(
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
    factor = viet_squish_factor_for_marks(
        right_marks, glyphs=glyphs, target_upem=target_upem
    )
    add_viet_squish_forms(
        squishable,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        factor=factor,
        target_upem=target_upem,
    )
    return {
        "right_marks": list(right_marks),
        "left_marks": list(left_marks),
        "squishable": list(squishable),
        "squish_factor": factor,
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
    """Install squish GSUB + left/right MarkToBase GPOS (after other GSUB)."""
    right_marks = state["right_marks"]
    left_marks = state["left_marks"]
    squishable = state["squishable"]
    install_viet_squish_gsub(
        font,
        squishable_bases=squishable,
        right_marks=right_marks,
        left_marks=left_marks,
        glyphs=glyphs,
        glyph_order=glyph_order,
    )
    anchors = collect_viet_base_anchors(
        squishable,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )
    return install_viet_mark_gpos(
        font,
        base_anchors=anchors,
        right_marks=right_marks,
        left_marks=left_marks,
        glyph_order=glyph_order,
    )
