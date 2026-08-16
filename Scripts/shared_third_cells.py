"""CJK third-cell niches (VS17–VS26) + FE0B zero-width overlay.

Encoding
--------
* Standard CJK (etc.) code points are used as-is.
* ``VS17``–``VS26`` (``U+E0100``–``U+E0109``) select which third niche the
  preceding base occupies.
* ``FE0B`` (and PUA ``U+E008``) makes the preceding form zero-width
  (``.ov``) for trigraph / digraph stacking — same as half-cell overlays.
* Access is GSUB ``ccmp``/``rlig``/``liga`` only — no cmap-14 UVS.

======= ========== ================================ ========
VS      Code point Niche                            Suffix
======= ========== ================================ ========
VS17    U+E0100    top third                        ``t3t``
VS18    U+E0101    top + middle third               ``t3tm``
VS19    U+E0102    middle third                     ``t3m``
VS20    U+E0103    middle + bottom third            ``t3mb``
VS21    U+E0104    bottom third                     ``t3b``
VS22    U+E0105    left third                       ``t3l``
VS23    U+E0106    left + center third              ``t3lc``
VS24    U+E0107    center third                     ``t3c``
VS25    U+E0108    center + right third             ``t3cr``
VS26    U+E0109    right third                      ``t3r``
======= ========== ================================ ========

Upright bases get CAPE Width/Height squish into the niche, then a translate
into the matching third (or two-thirds) slot. Oriented (D4) bases reuse the
upright niche via TrueType composites (same pattern as half-cell ``.dk*``).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.misc.transform import Transform
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from cape_weightor import (
    apply_height,
    apply_width,
    layer_from_ttglyph,
    ttglyph_from_layer,
)
from shared_half_cells import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    TRANSFORM_MODES,
    _recording_from_glyph,
    add_overlay_forms,
    apply_transform,
    build_chunked_ligature_subst_lookup,
    contour_center,
    empty_glyph,
    ideographic_bounds,
    ideographic_center,
    make_composite_variant,
    overlay_glyph_name,
    variant_glyph_name,
)

# FE0B zero-width overlay (same glyph name as half-cell digraphs).
OV_SELECTOR_CP = 0xFE0B
OV_SELECTOR_NAME = "vsOv"
# Blink drops Default_Ignorable VS before GSUB; PUA mirror keeps liga alive.
OV_PUA_CP = 0xE008

# ---------- VS17–VS26 ----------

# Unicode Variation Selectors Supplement: VS17 = U+E0100.
VS17_CP = 0xE0100
VS26_CP = 0xE0109
THIRD_VS_BASE = VS17_CP
THIRD_VS_COUNT = 10
THIRD_VS_LAST = VS26_CP

# Single-third ≈ 1/3; double-third ≈ 2/3 (CAPE outer-size factor).
THIRD_FACTOR = 1.0 / 3.0
TWO_THIRD_FACTOR = 2.0 / 3.0
THIRD_PAD_FRAC = 0.02

# (vs_cp, selector glyph name, niche suffix, axis, band0, band1)
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

_D4_SUFFIXES = frozenset({"r90", "r180", "r270", "mx", "my", "r90mx", "r90my"})
_D4_TRANSFORM: Dict[str, Tuple[int, bool, bool]] = {
    suf: (rot, fx, fy)
    for _vs, rot, fx, fy, suf in TRANSFORM_MODES
    if suf is not None
}


def third_vs_glyph_name(vs_cp: int) -> str:
    if not (THIRD_VS_BASE <= vs_cp <= THIRD_VS_LAST):
        raise ValueError(f"not a third-cell VS: U+{vs_cp:X}")
    return f"vs{vs_cp - THIRD_VS_BASE + 17}"


def third_form_name(base_name: str, suffix: str) -> str:
    return f"{base_name}.{suffix}"


def _d4_suffix_of(name: str) -> Optional[str]:
    if "." not in name:
        return None
    suf = name.rsplit(".", 1)[1]
    return suf if suf in _D4_SUFFIXES else None


def _d4_root_name(name: str) -> str:
    suf = _d4_suffix_of(name)
    if suf is None:
        return name
    return name[: -(len(suf) + 1)]


def _third_slot_rect(
    target_upem: float,
    *,
    axis: str,
    band0: int,
    band1: int,
) -> Tuple[float, float, float, float]:
    """Return ``(x0, y0, x1, y1)`` for bands ``band0..band1`` (inclusive)."""
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
    x0, y0, x1, y1 = _third_slot_rect(
        upem, axis=axis, band0=band0, band1=band1
    )
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
    """Affine map full ideographic frame → third / two-thirds slot (no CAPE)."""
    from shared_half_cells import STANDALONE_VERT_PAD, cjk_padded_floor

    upem = float(target_upem)
    src = glyph
    try:
        is_comp = bool(glyph.isComposite())
    except Exception:
        is_comp = False
    if is_comp:
        src = _bake_simple_glyph(glyph, glyph_set)

    bottom, top, _ = cjk_padded_floor(int(target_upem), pad=STANDALONE_VERT_PAD)
    inset = upem * STANDALONE_VERT_PAD
    sx0, sx1 = inset, upem - inset
    sy0, sy1 = bottom, top
    sw = max(sx1 - sx0, 1.0)
    sh = max(sy1 - sy0, 1.0)

    x0, y0, x1, y1 = _third_slot_rect(
        upem, axis=axis, band0=band0, band1=band1
    )
    tw = max(x1 - x0, 1.0)
    th = max(y1 - y0, 1.0)
    sx = tw / sw
    sy = th / sh
    src_cx = (sx0 + sx1) / 2.0
    src_cy = (sy0 + sy1) / 2.0
    dst_cx = (x0 + x1) / 2.0
    dst_cy = (y0 + y1) / 2.0
    t = Transform(sx, 0, 0, sy, dst_cx - sx * src_cx, dst_cy - sy * src_cy)
    rec = _recording_from_glyph(src, None)
    out = apply_transform(rec, t)
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    del advance
    return out, int(upem), lsb


def make_third_glyph(
    glyph: TTGlyph,
    advance: int,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: Optional[int] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    factor: Optional[float] = None,
) -> Tuple[TTGlyph, int, int]:
    """Uniform CAPE Width/Height into a third niche, then translate."""
    from cjk_diacritics import _normalize_winding

    upem = int(
        target_upem if target_upem is not None else (advance if advance > 0 else 1000)
    )
    use = float(factor if factor is not None else _factor_for_bands(band0, band1))
    simple = _normalize_winding(_bake_simple_glyph(glyph, glyph_set), glyph_set)
    layer = layer_from_ttglyph(simple, float(advance if advance > 0 else upem))
    if not layer.paths:
        return place_glyph_in_third(
            simple,
            advance if advance > 0 else upem,
            axis=axis,
            band0=band0,
            band1=band1,
            target_upem=upem,
            glyph_set=None,
        )

    # CJK niches: uniform scale only (no stem compensation / outline offset).
    cell_cx, cell_cy = ideographic_center(upem)
    if axis == "y":
        apply_height(layer, use, stem=0.0, center_y=cell_cy)
    else:
        apply_width(layer, use, stem=0.0, center_x=cell_cx)

    out, _adv, _lsb = ttglyph_from_layer(layer)
    return _translate_ink_to_third_center(
        out, axis=axis, band0=band0, band1=band1, target_upem=upem
    )


def _niche_center_xy(axis: str, band0: int, band1: int) -> Tuple[float, float]:
    """Unit-square niche center for D4 remapping (origin at cell center)."""
    lo = min(band0, band1)
    hi = max(band0, band1)
    mid = (lo + hi + 1) / 2.0 / 3.0  # 0..1 along axis from start
    # Map 0..1 → -1..+1 about center.
    t = mid * 2.0 - 1.0
    if axis == "y":
        # band 0 = bottom (−1), band 2 = top (+1)
        return 0.0, t
    return t, 0.0


def _d4_third_parent_suffix(needed_suf: str, rot: int, fx: bool, fy: bool) -> str:
    """Upright niche suffix that maps to ``needed_suf`` under D4."""
    from shared_half_cells import variant_matrix

    slot = next(s for s in THIRD_VS_SLOTS if s[2] == needed_suf)
    _cp, _sel, _suf, axis, b0, b1 = slot
    sx, sy = _niche_center_xy(axis, b0, b1)
    (xx, xy), (yx, yy) = variant_matrix(rot90_quarters=rot, flip_x=fx, flip_y=fy)
    # Inverse map: needed on oriented ← upright parent.
    # Forward: upright → oriented; we want parent such that F(parent) ≈ needed.
    # Solve approx by testing all upright niches.
    best = needed_suf
    best_d = 1e9
    for _c, _s, suf, a, p0, p1 in THIRD_VS_SLOTS:
        ux, uy = _niche_center_xy(a, p0, p1)
        mx, my = xx * ux + yx * uy, xy * ux + yy * uy
        d = (mx - sx) ** 2 + (my - sy) ** 2
        # Prefer matching span width.
        if abs((p1 - p0) - (b1 - b0)) > 0:
            d += 0.5
        if d < best_d:
            best_d = d
            best = suf
    return best


def add_third_forms(
    base_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int = 1000,
) -> List[str]:
    """Bake upright third niches; oriented bases composite from upright parents."""
    identities = [n for n in base_names if _d4_suffix_of(n) is None and n in glyphs]
    oriented = [n for n in base_names if _d4_suffix_of(n) is not None and n in glyphs]

    added: List[str] = []
    for name in identities:
        adv, _lsb = metrics.get(name, (target_upem, 0))
        src = glyphs[name]
        for _cp, _sel, suf, axis, b0, b1 in THIRD_VS_SLOTS:
            out_name = third_form_name(name, suf)
            if out_name in glyphs:
                continue
            g, a, l = make_third_glyph(
                src,
                adv,
                axis=axis,
                band0=b0,
                band1=b1,
                target_upem=target_upem,
                glyph_set=glyphs,
            )
            glyph_order.append(out_name)
            glyphs[out_name] = g
            metrics[out_name] = (a, l)
        added.append(name)

    for name in oriented:
        suf = _d4_suffix_of(name)
        assert suf is not None
        root = _d4_root_name(name)
        rot, fx, fy = _D4_TRANSFORM[suf]
        pivot = contour_center(glyphs[root], glyphs)
        for _cp, _sel, needed, _axis, _b0, _b1 in THIRD_VS_SLOTS:
            parent_suf = _d4_third_parent_suffix(needed, rot, fx, fy)
            parent = third_form_name(root, parent_suf)
            child = third_form_name(name, needed)
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
                allow_2x2=True,
            )
            glyph_order.append(child)
            glyphs[child] = g
            metrics[child] = (a, l)
        added.append(name)

    return added


def third_vs_liga_map(
    bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """``base + VS17..VS26`` / ``FE0B`` → third niche and/or zero-width ``.ov``.

    Mirrors half-cell overlay spelling::

        base FE0B              → base.ov
        base VS17              → base.t3t
        base FE0B VS17         → base.t3t.ov   (either FE0B↔VS order)
        base.t3t FE0B          → base.t3t.ov
        (+ optional FE00 no-op after the base)
    """
    from shared_half_cells import vs_glyph_name

    vs01 = vs_glyph_name(TRANSFORM_MODES[0][0])
    ov = OV_SELECTOR_NAME
    liga: Dict[Tuple[str, ...], str] = {}
    for form in bases:
        if form not in glyphs:
            continue
        form_ov = overlay_glyph_name(form)
        if form_ov in glyphs and ov in glyphs:
            liga[(form, ov)] = form_ov
            liga[(form, vs01, ov)] = form_ov
        for vs_cp, sel_name, suf, _axis, _b0, _b1 in THIRD_VS_SLOTS:
            out = third_form_name(form, suf)
            if out not in glyphs:
                continue
            sel = sel_name if sel_name in glyphs else third_vs_glyph_name(vs_cp)
            if sel not in glyphs:
                continue
            liga[(form, sel)] = out
            liga[(form, vs01, sel)] = out
            out_ov = overlay_glyph_name(out)
            if out_ov not in glyphs or ov not in glyphs:
                continue
            liga[(form, ov, sel)] = out_ov
            liga[(form, sel, ov)] = out_ov
            liga[(form, vs01, ov, sel)] = out_ov
            liga[(form, vs01, sel, ov)] = out_ov
            liga[(out, ov)] = out_ov
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
    """Install VS17–26 + FE0B marks, bake third niches and ``.ov`` overlays.

    Returns the form list that accepts third-cell VS (identity + D4).
    """
    # FE0B (+ PUA mirror) zero-width overlay selector.
    if OV_SELECTOR_NAME not in glyphs:
        glyph_order.append(OV_SELECTOR_NAME)
        glyphs[OV_SELECTOR_NAME] = empty_glyph()
        metrics[OV_SELECTOR_NAME] = (0, 0)
    cmap[OV_SELECTOR_CP] = OV_SELECTOR_NAME
    cmap[OV_PUA_CP] = OV_SELECTOR_NAME

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
        forms,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )

    # Zero-width overlays for bases and every third niche (trigraph stacking).
    ov_sources: List[str] = []
    for form in forms:
        if form not in glyphs:
            continue
        ov_sources.append(form)
        for _cp, _sel, suf, _axis, _b0, _b1 in THIRD_VS_SLOTS:
            niche = third_form_name(form, suf)
            if niche in glyphs:
                ov_sources.append(niche)
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
    """Append third-cell VS + FE0B overlay ligatures to existing ``GSUB``."""
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    liga = third_vs_liga_map(bases, glyphs=glyphs)
    if not liga:
        return 0

    # Longer ligatures before shorter (FE0B+VS before VS alone).
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
    existing_scripts = {
        sr.ScriptTag for sr in (gsub.ScriptList.ScriptRecord or [])
    }
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
