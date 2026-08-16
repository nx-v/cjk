"""CJK quarter-cell niches — two faces (vertical / horizontal).

Encoding
--------
* Standard CJK code points are used as-is.
* **Vertical face** (``qv``): Y-axis bands via VS13–14 + VS27–33.
* **Horizontal face** (``qh``): X-axis bands via VS15–16 + VS34–40.
  Label “top”/“bottom” on the horizontal face maps to **left**/**right**
  (r90 CCW: top→left, bottom→right).
* ``FE0B`` (and PUA ``U+E008``) → zero-width ``.ov`` for stacking.
* GSUB ``ccmp``/``rlig``/``liga`` only — no cmap-14 UVS.

Vertical (``qv``) — axis Y, bands 0=bottom … 3=top
======= ========== ========================= ========
VS      Code point Niche                     Suffix
======= ========== ========================= ========
VS13    U+FE0C     top half                  ``q4th``
VS14    U+FE0D     bottom half               ``q4bh``
VS27    U+E010A    top quarter               ``q4t``
VS28    U+E010B    near-top quarter          ``q4nt``
VS29    U+E010C    near-bottom quarter       ``q4nb``
VS30    U+E010D    bottom quarter            ``q4b``
VS31    U+E010E    top three-quarters        ``q4t3``
VS32    U+E010F    bottom three-quarters     ``q4b3``
VS33    U+E0110    middle half               ``q4mh``
======= ========== ========================= ========

Horizontal (``qh``) — axis X, same suffixes (top→left, bottom→right)
======= ========== ========================= ========
VS      Code point Niche                     Suffix
======= ========== ========================= ========
VS15    U+FE0E     top half (= left half)    ``q4th``
VS16    U+FE0F     bottom half (= right)     ``q4bh``
VS34    U+E0111    top quarter (= left)      ``q4t``
VS35    U+E0112    near-top (= near-left)    ``q4nt``
VS36    U+E0113    near-bottom (= near-right)``q4nb``
VS37    U+E0114    bottom quarter (= right)  ``q4b``
VS38    U+E0115    top 3/4 (= left 3/4)      ``q4t3``
VS39    U+E0116    bottom 3/4 (= right 3/4)  ``q4b3``
VS40    U+E0117    middle half               ``q4mh``
======= ========== ========================= ========

Niche composites / placers apply ``COMPOUND_CELL_SCALE`` (``shared_half_cells``)
so stacked quarters match standalone CJK size.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.misc.transform import Transform
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

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
    make_composite_variant,
    overlay_glyph_name,
    variant_glyph_name,
)

# FE0B zero-width overlay.
OV_SELECTOR_CP = 0xFE0B
OV_SELECTOR_NAME = "vsOv"
OV_PUA_CP = 0xE008

# Four bands along the niche axis.
QUARTER_BANDS = 4
QUARTER_PAD_FRAC = 0.02

# (vs_cp, selector name, suffix, band0, band1) — axis comes from the face.
QuarterSlot = Tuple[int, str, str, int, int]

# Vertical face: Y axis. band 0 = bottom, band 3 = top.
QUARTER_VS_SLOTS_V: Tuple[QuarterSlot, ...] = (
    (0xFE0C, "vs13", "q4th", 2, 3),  # top half
    (0xFE0D, "vs14", "q4bh", 0, 1),  # bottom half
    (0xE010A, "vs27", "q4t", 3, 3),  # top quarter
    (0xE010B, "vs28", "q4nt", 2, 2),  # near-top
    (0xE010C, "vs29", "q4nb", 1, 1),  # near-bottom
    (0xE010D, "vs30", "q4b", 0, 0),  # bottom quarter
    (0xE010E, "vs31", "q4t3", 1, 3),  # top 3/4
    (0xE010F, "vs32", "q4b3", 0, 2),  # bottom 3/4
    (0xE0110, "vs33", "q4mh", 1, 2),  # middle half
)

# Horizontal face: X axis. top→left (low X), bottom→right (high X).
# band 0 = left, band 3 = right.
QUARTER_VS_SLOTS_H: Tuple[QuarterSlot, ...] = (
    (0xFE0E, "vs15", "q4th", 0, 1),  # top half → left half
    (0xFE0F, "vs16", "q4bh", 2, 3),  # bottom half → right half
    (0xE0111, "vs34", "q4t", 0, 0),  # top quarter → left
    (0xE0112, "vs35", "q4nt", 1, 1),  # near-top → near-left
    (0xE0113, "vs36", "q4nb", 2, 2),  # near-bottom → near-right
    (0xE0114, "vs37", "q4b", 3, 3),  # bottom quarter → right
    (0xE0115, "vs38", "q4t3", 0, 2),  # top 3/4 → left 3/4
    (0xE0116, "vs39", "q4b3", 1, 3),  # bottom 3/4 → right 3/4
    (0xE0117, "vs40", "q4mh", 1, 2),  # middle half
)

QUARTER_FACE_V = "qv"
QUARTER_FACE_H = "qh"

_D4_SUFFIXES = frozenset({"r90", "r180", "r270", "mx", "my", "r90mx", "r90my"})
_D4_TRANSFORM: Dict[str, Tuple[int, bool, bool]] = {
    suf: (rot, fx, fy)
    for _vs, rot, fx, fy, suf in TRANSFORM_MODES
    if suf is not None
}


def quarter_slots_for_face(face: str) -> Tuple[QuarterSlot, ...]:
    match face:
        case "qv":
            return QUARTER_VS_SLOTS_V
        case "qh":
            return QUARTER_VS_SLOTS_H
        case _:
            raise ValueError(
                f"quarter face must be {QUARTER_FACE_V!r} or {QUARTER_FACE_H!r}"
            )


def quarter_axis_for_face(face: str) -> str:
    return "y" if face == QUARTER_FACE_V else "x"


def quarter_form_name(base_name: str, suffix: str) -> str:
    return f"{base_name}.{suffix}"


def _factor_for_bands(band0: int, band1: int) -> float:
    n = abs(band1 - band0) + 1
    return n / float(QUARTER_BANDS)


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
    x0, y0, x1, y1 = _quarter_slot_rect(
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
    from shared_half_cells import (
        COMPOUND_CELL_SCALE,
        STANDALONE_VERT_PAD,
        cjk_padded_floor,
    )

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

    x0, y0, x1, y1 = _quarter_slot_rect(
        upem, axis=axis, band0=band0, band1=band1
    )
    tw = max(x1 - x0, 1.0)
    th = max(y1 - y0, 1.0)
    g = float(COMPOUND_CELL_SCALE)
    sx = (tw / sw) * g
    sy = (th / sh) * g
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
    """Upright quarter niche as a scaled composite of ``base_name``."""
    from shared_half_cells import make_axis_niche_composite

    upem = int(
        target_upem if target_upem is not None else (advance if advance > 0 else 1000)
    )
    use = float(factor if factor is not None else _factor_for_bands(band0, band1))
    x0, y0, x1, y1 = _quarter_slot_rect(
        float(upem), axis=axis, band0=band0, band1=band1
    )
    return make_axis_niche_composite(
        base_name,
        advance=int(advance if advance > 0 else upem),
        axis=axis,
        factor=use,
        dest_x=(x0 + x1) / 2.0,
        dest_y=(y0 + y1) / 2.0,
        target_upem=upem,
        glyph_set=glyph_set,
    )


def _niche_center_xy(axis: str, band0: int, band1: int) -> Tuple[float, float]:
    lo = min(band0, band1)
    hi = max(band0, band1)
    mid = (lo + hi + 1) / 2.0 / float(QUARTER_BANDS)
    t = mid * 2.0 - 1.0
    if axis == "y":
        return 0.0, t
    return t, 0.0


def _d4_quarter_parent_suffix(
    needed_suf: str,
    rot: int,
    fx: bool,
    fy: bool,
    slots: Sequence[QuarterSlot],
    axis: str,
) -> str:
    from shared_half_cells import variant_matrix

    slot = next(s for s in slots if s[2] == needed_suf)
    _cp, _sel, _suf, b0, b1 = slot
    sx, sy = _niche_center_xy(axis, b0, b1)
    (xx, xy), (yx, yy) = variant_matrix(rot90_quarters=rot, flip_x=fx, flip_y=fy)
    best = needed_suf
    best_d = 1e9
    for _c, _s, suf, p0, p1 in slots:
        ux, uy = _niche_center_xy(axis, p0, p1)
        mx, my = xx * ux + yx * uy, xy * ux + yy * uy
        d = (mx - sx) ** 2 + (my - sy) ** 2
        if abs((p1 - p0) - (b1 - b0)) > 0:
            d += 0.5
        if d < best_d:
            best_d = d
            best = suf
    return best


def add_quarter_forms(
    base_names: Sequence[str],
    *,
    face: str,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int = 1000,
) -> List[str]:
    """Upright quarter niches as composites of the identity; D4 via composites."""
    slots = quarter_slots_for_face(face)
    axis = quarter_axis_for_face(face)
    identities = [n for n in base_names if _d4_suffix_of(n) is None and n in glyphs]
    oriented = [n for n in base_names if _d4_suffix_of(n) is not None and n in glyphs]

    added: List[str] = []
    for name in identities:
        adv, _lsb = metrics.get(name, (target_upem, 0))
        for _cp, _sel, suf, b0, b1 in slots:
            out_name = quarter_form_name(name, suf)
            if out_name in glyphs:
                continue
            g, a, l = make_quarter_glyph(
                name,
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
        for _cp, _sel, needed, _b0, _b1 in slots:
            parent_suf = _d4_quarter_parent_suffix(
                needed, rot, fx, fy, slots, axis
            )
            parent = quarter_form_name(root, parent_suf)
            child = quarter_form_name(name, needed)
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


def quarter_vs_liga_map(
    bases: Sequence[str],
    *,
    face: str,
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """``base + VS`` / ``FE0B`` → quarter niche and/or zero-width ``.ov``."""
    from shared_half_cells import vs_glyph_name

    slots = quarter_slots_for_face(face)
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
        for _vs_cp, sel_name, suf, _b0, _b1 in slots:
            out = quarter_form_name(form, suf)
            if out not in glyphs:
                continue
            if sel_name not in glyphs:
                continue
            liga[(form, sel_name)] = out
            liga[(form, vs01, sel_name)] = out
            out_ov = overlay_glyph_name(out)
            if out_ov not in glyphs or ov not in glyphs:
                continue
            liga[(form, ov, sel_name)] = out_ov
            liga[(form, sel_name, ov)] = out_ov
            liga[(form, vs01, ov, sel_name)] = out_ov
            liga[(form, vs01, sel_name, ov)] = out_ov
            liga[(out, ov)] = out_ov
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
    """Install face VS + FE0B, bake quarter niches and ``.ov`` overlays."""
    slots = quarter_slots_for_face(face)

    if OV_SELECTOR_NAME not in glyphs:
        glyph_order.append(OV_SELECTOR_NAME)
        glyphs[OV_SELECTOR_NAME] = empty_glyph()
        metrics[OV_SELECTOR_NAME] = (0, 0)
    cmap[OV_SELECTOR_CP] = OV_SELECTOR_NAME
    cmap[OV_PUA_CP] = OV_SELECTOR_NAME

    for vs_cp, sel_name, _suf, _b0, _b1 in slots:
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

    add_quarter_forms(
        forms,
        face=face,
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
        for _cp, _sel, suf, _b0, _b1 in slots:
            niche = quarter_form_name(form, suf)
            if niche in glyphs:
                ov_sources.append(niche)
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
    """Append quarter-cell VS + FE0B overlay ligatures to ``GSUB``."""
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
