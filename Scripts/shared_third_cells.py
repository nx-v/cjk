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

Upright and D4 bases are **slices** of the already-baked fullwidth outline:
clip the two end thirds per axis; middle and two-thirds bands are
``full − end`` / ``(full − end) − other end``. Zero-width ``.ov``
forms are composites of those fullwidth slices.
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
    boolean_subtract_named,
    build_chunked_ligature_subst_lookup,
    empty_glyph,
    half_plane_rect,
    ideographic_bounds,
    install_derived_glyph,
    make_niche_slice_glyph,
    overlay_glyph_name,
    variant_glyph_name,
    HALF_PLANE_INF_FRAC,
    propagate_d4_niches,
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

# Single-third ≈ 1/3; double-third ≈ 2/3 (composite scale factor).
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
    """Clip ``glyph`` to a third / two-thirds slot (slice — no stretch)."""
    from shared_half_cells import clip_glyph_to_rect

    upem = float(target_upem)
    rect = _third_slot_rect(upem, axis=axis, band0=band0, band1=band1)
    clipped = clip_glyph_to_rect(glyph, rect, glyph_set=glyph_set)
    try:
        clipped.recalcBounds(None)
        lsb = int(clipped.xMin)
    except Exception:
        lsb = 0
    del advance
    return clipped, int(upem), lsb


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
    """Upright third niche as a slice of ``base_name`` (clip; no stretch)."""
    from shared_half_cells import make_niche_slice_glyph

    if glyph_set is None:
        raise ValueError("make_third_glyph requires glyph_set for slice bake")
    upem = int(
        target_upem if target_upem is not None else (advance if advance > 0 else 1000)
    )
    del factor
    rect = _third_slot_rect(float(upem), axis=axis, band0=band0, band1=band1)
    return make_niche_slice_glyph(
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
    """Slice each baked form: clip end thirds; derive mid / 2/3 by subtract."""
    added: List[str] = []
    end_slots = (
        ("t3t", "y", 2, 2),
        ("t3b", "y", 0, 0),
        ("t3l", "x", 0, 0),
        ("t3r", "x", 2, 2),
    )
    for name in base_names:
        if name not in glyphs:
            continue
        adv, _lsb = metrics.get(name, (target_upem, 0))

        def _put(out_name: str, gm: Tuple[TTGlyph, int, int]) -> None:
            install_derived_glyph(
                out_name,
                gm,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
            )

        for suf, axis, b0, b1 in end_slots:
            out_name = third_form_name(name, suf)
            if out_name in glyphs:
                continue
            bot, top, _ = ideographic_bounds(target_upem)
            inf = float(target_upem) * HALF_PLANE_INF_FRAC
            if axis == "y":
                span = top - bot
                if b0 == 2:  # top third: y >= 2/3
                    rect = half_plane_rect(
                        bot + span * (2.0 / 3.0),
                        axis="y",
                        keep="hi",
                        inf=inf,
                    )
                else:  # bottom third: y <= 1/3
                    rect = half_plane_rect(
                        bot + span * (1.0 / 3.0),
                        axis="y",
                        keep="lo",
                        inf=inf,
                    )
            elif b0 == 0:  # left third
                rect = half_plane_rect(
                    float(target_upem) / 3.0, axis="x", keep="lo", inf=inf
                )
            else:  # right third
                rect = half_plane_rect(
                    float(target_upem) * (2.0 / 3.0),
                    axis="x",
                    keep="hi",
                    inf=inf,
                )
            _put(
                out_name,
                make_niche_slice_glyph(
                    name,
                    advance=adv,
                    rect=rect,
                    glyph_set=glyphs,
                ),
            )

        t = third_form_name(name, "t3t")
        b = third_form_name(name, "t3b")
        l = third_form_name(name, "t3l")
        r = third_form_name(name, "t3r")
        tm = third_form_name(name, "t3tm")
        mb = third_form_name(name, "t3mb")
        m = third_form_name(name, "t3m")
        lc = third_form_name(name, "t3lc")
        cr = third_form_name(name, "t3cr")
        c = third_form_name(name, "t3c")
        _put(
            mb,
            boolean_subtract_named(
                name, t, glyphs=glyphs, metrics=metrics, advance=adv
            ),
        )
        _put(
            tm,
            boolean_subtract_named(
                name, b, glyphs=glyphs, metrics=metrics, advance=adv
            ),
        )
        _put(
            m,
            boolean_subtract_named(
                mb, b, glyphs=glyphs, metrics=metrics, advance=adv
            ),
        )
        _put(
            cr,
            boolean_subtract_named(
                name, l, glyphs=glyphs, metrics=metrics, advance=adv
            ),
        )
        _put(
            lc,
            boolean_subtract_named(
                name, r, glyphs=glyphs, metrics=metrics, advance=adv
            ),
        )
        _put(
            c,
            boolean_subtract_named(
                lc, l, glyphs=glyphs, metrics=metrics, advance=adv
            ),
        )
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
        list(cjk_bases),
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )
    third_windows = {
        suf: _third_slot_rect(
            float(target_upem), axis=axis, band0=b0, band1=b1
        )
        for _cp, _sel, suf, axis, b0, b1 in THIRD_VS_SLOTS
    }
    propagate_d4_niches(
        cjk_bases,
        suffixes=tuple(suf for _cp, _sel, suf, _a, _b0, _b1 in THIRD_VS_SLOTS),
        form_name=third_form_name,
        windows=third_windows,
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
