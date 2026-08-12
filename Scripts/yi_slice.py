"""Yi FE08–FE09 half-plane slice overlays (horizontal / vertical only).

Encoding
--------
* ``U+FE08`` horizontal — A top + B bottom
* ``U+FE09`` vertical   — A left + B right

Budget
------
Each identity / ``r90`` form stores **four** baked CJK-box half-plane clips
(all zero-advance). Other D4 orientations reuse those via TrueType composites
(same id/r90 split as full-glyph orientations). A shared ``sliceAdv`` empty
glyph carries the em advance after the second half.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from shared_half_cells import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    SIDEWAYS_FROM_R90,
    TRANSFORM_MODES,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    TransformMode,
    _recording_from_glyph,
    contour_center,
    empty_glyph,
    make_composite_variant,
    variant_glyph_name,
)

# FE08 / FE09 slice joiners.
SLICE_H_CP = 0xFE08
SLICE_V_CP = 0xFE09

SLICE_ADV_NAME = "sliceAdv"

# (codepoint, mark glyph name, first-slot half suffix, second-slot half suffix)
SLICE_MODES: Tuple[Tuple[int, str, str, str], ...] = (
    (SLICE_H_CP, "vsSliceH", "top", "bot"),
    (SLICE_V_CP, "vsSliceV", "left", "right"),
)

HALF_SUFFIXES: Tuple[str, ...] = ("top", "bot", "left", "right")

# Dest half → source half on the parent outline, for axis-aligned maps.
# Matches ``variant_matrix``: flip_x ⇒ sy=-1 (mx); flip_y ⇒ sx=-1 (my).
_AXIS_HALF_SOURCE: Dict[str, Dict[str, str]] = {
    "mx": {"top": "bot", "bot": "top", "left": "left", "right": "right"},
    "my": {"top": "top", "bot": "bot", "left": "right", "right": "left"},
    "r180": {"top": "bot", "bot": "top", "left": "right", "right": "left"},
}
# Relative maps applied to r90 halves (same flags as SIDEWAYS_FROM_R90).
_R90_HALF_SOURCE: Dict[str, Dict[str, str]] = {
    "r270": _AXIS_HALF_SOURCE["r180"],
    "r90mx": _AXIS_HALF_SOURCE["mx"],
    "r90my": _AXIS_HALF_SOURCE["my"],
}


def slice_mark_cps() -> List[int]:
    return [cp for cp, _n, _a, _b in SLICE_MODES]


def half_glyph_name(base_name: str, half: str) -> str:
    return f"{base_name}.{half}"


def cjk_box(
    target_upem: int,
    *,
    cell_width: Optional[float] = None,
    cell_x0: float = 0.0,
) -> Tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` of the CJK typo cell (or a narrower halfwidth cell)."""
    y1 = target_upem * TYPO_ASCENDER_FRAC
    y0 = target_upem * TYPO_DESCENDER_FRAC
    w = float(target_upem if cell_width is None else cell_width)
    return float(cell_x0), y0, float(cell_x0) + w, y1


def cjk_mid(
    target_upem: int,
    *,
    cell_width: Optional[float] = None,
    cell_x0: float = 0.0,
) -> Tuple[float, float]:
    x0, y0, x1, y1 = cjk_box(
        target_upem, cell_width=cell_width, cell_x0=cell_x0
    )
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def _rect_path(x0: float, y0: float, x1: float, y1: float):
    import pathops

    p = pathops.Path()
    p.moveTo(x0, y0)
    p.lineTo(x1, y0)
    p.lineTo(x1, y1)
    p.lineTo(x0, y1)
    p.close()
    return p


def _ttglyph_to_pathops(glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]]):
    import pathops

    rec = _recording_from_glyph(glyph, glyph_set)
    sk = pathops.Path()
    rec.replay(sk.getPen())
    return sk


def _pathops_to_ttglyph(path) -> TTGlyph:
    from fontTools.ttLib.removeOverlaps import ttfGlyphFromSkPath

    if path is None or not list(path.contours):
        return empty_glyph()
    g = ttfGlyphFromSkPath(path)
    try:
        g.recalcBounds(None)
    except Exception:
        pass
    return g


def clip_glyph_to_half(
    glyph: TTGlyph,
    half: str,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    cell_width: Optional[float] = None,
) -> TTGlyph:
    """Intersect ``glyph`` with one CJK-box half-plane (top/bot/left/right)."""
    import pathops

    x0, y0, x1, y1 = cjk_box(target_upem, cell_width=cell_width)
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    pad = target_upem * 0.05
    if half == "top":
        rx0, ry0, rx1, ry1 = x0 - pad, my, x1 + pad, y1 + pad
    elif half == "bot":
        rx0, ry0, rx1, ry1 = x0 - pad, y0 - pad, x1 + pad, my
    elif half == "left":
        rx0, ry0, rx1, ry1 = x0 - pad, y0 - pad, mx, y1 + pad
    elif half == "right":
        rx0, ry0, rx1, ry1 = mx, y0 - pad, x1 + pad, y1 + pad
    else:
        raise ValueError(f"unknown half-plane {half!r}")

    src = _ttglyph_to_pathops(glyph, glyph_set)
    clip = _rect_path(rx0, ry0, rx1, ry1)
    try:
        out = pathops.op(src, clip, pathops.PathOp.INTERSECTION, fix_winding=True)
    except Exception:
        return empty_glyph()
    return _pathops_to_ttglyph(out)


def ensure_slice_adv(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
    *,
    name: str = SLICE_ADV_NAME,
    advance: Optional[int] = None,
) -> str:
    """Shared empty glyph that carries the cell advance after the second half."""
    if name not in glyphs:
        glyph_order.append(name)
        glyphs[name] = empty_glyph()
        metrics[name] = (int(target_upem if advance is None else advance), 0)
    return name


def _bake_halves_for_form(
    form_name: str,
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
    cell_width: Optional[float] = None,
) -> None:
    """Clip ``form_name`` into four baked zero-advance half-plane glyphs."""
    for half in HALF_SUFFIXES:
        hname = half_glyph_name(form_name, half)
        if hname in glyphs:
            continue
        clipped = clip_glyph_to_half(
            glyphs[form_name],
            half,
            target_upem,
            glyph_set=glyphs,
            cell_width=cell_width,
        )
        try:
            clipped.recalcBounds(None)
            h_lsb = int(clipped.xMin)
        except Exception:
            h_lsb = 0
        glyph_order.append(hname)
        glyphs[hname] = clipped
        metrics[hname] = (0, h_lsb)


def _composite_halves_for_form(
    form_name: str,
    parent_name: str,
    *,
    rot90_quarters: int,
    flip_x: bool,
    flip_y: bool,
    half_source: Dict[str, str],
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
) -> None:
    """Build half glyphs as composites of ``parent`` halves (same pivot as form)."""
    parent = glyphs[parent_name]
    pivot = contour_center(parent, glyphs)
    for half in HALF_SUFFIXES:
        hname = half_glyph_name(form_name, half)
        if hname in glyphs:
            continue
        src_name = half_glyph_name(parent_name, half_source[half])
        if src_name not in glyphs:
            continue
        m_glyph, _adv, m_lsb = make_composite_variant(
            src_name,
            target_upem,
            rot90_quarters=rot90_quarters,
            flip_x=flip_x,
            flip_y=flip_y,
            advance=0,
            lsb=metrics[src_name][1],
            base_glyph=glyphs[src_name],
            glyph_set=glyphs,
            center=pivot,
        )
        glyph_order.append(hname)
        glyphs[hname] = m_glyph
        metrics[hname] = (0, m_lsb)


def add_slice_halves(
    base_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
    modes: Optional[Sequence[TransformMode]] = None,
    limit: Optional[int] = None,
    cell_width: Optional[float] = None,
    slice_adv_name: str = SLICE_ADV_NAME,
) -> List[str]:
    """Install slice halves: bake id + r90; composite the other D4 forms.

    ``base_names`` are identity glyph names (not pre-expanded orientations).
    Returns every form name (id + variants) that received a full half set.
    """
    adv = int(target_upem if cell_width is None else cell_width)
    ensure_slice_adv(
        glyph_order,
        glyphs,
        metrics,
        target_upem,
        name=slice_adv_name,
        advance=adv,
    )
    use_modes = list(modes) if modes is not None else list(TRANSFORM_MODES)
    added: List[str] = []

    for base in base_names:
        if limit is not None and len(added) >= limit:
            break
        if base not in glyphs:
            continue

        # Bake halves for the two outline sources only.
        _bake_halves_for_form(
            base,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            cell_width=cell_width,
        )
        r90_name = variant_glyph_name(base, "r90")
        if r90_name in glyphs:
            _bake_halves_for_form(
                r90_name,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                target_upem=target_upem,
                cell_width=cell_width,
            )

        if all(half_glyph_name(base, h) in glyphs for h in HALF_SUFFIXES):
            added.append(base)

        for _vs, rot, flip_x, flip_y, suffix in use_modes:
            if suffix is None:
                continue
            form = variant_glyph_name(base, suffix)
            if form not in glyphs:
                continue
            if suffix == "r90":
                if all(half_glyph_name(form, h) in glyphs for h in HALF_SUFFIXES):
                    added.append(form)
                continue
            if suffix in SIDEWAYS_FROM_R90:
                if r90_name not in glyphs:
                    continue
                rel_rot, rel_fx, rel_fy = SIDEWAYS_FROM_R90[suffix]
                _composite_halves_for_form(
                    form,
                    r90_name,
                    rot90_quarters=rel_rot,
                    flip_x=rel_fx,
                    flip_y=rel_fy,
                    half_source=_R90_HALF_SOURCE[suffix],
                    glyph_order=glyph_order,
                    glyphs=glyphs,
                    metrics=metrics,
                    target_upem=target_upem,
                )
            elif suffix in _AXIS_HALF_SOURCE:
                _composite_halves_for_form(
                    form,
                    base,
                    rot90_quarters=rot,
                    flip_x=flip_x,
                    flip_y=flip_y,
                    half_source=_AXIS_HALF_SOURCE[suffix],
                    glyph_order=glyph_order,
                    glyphs=glyphs,
                    metrics=metrics,
                    target_upem=target_upem,
                )
            else:
                # Unknown suffix — bake from the form outline.
                _bake_halves_for_form(
                    form,
                    glyph_order=glyph_order,
                    glyphs=glyphs,
                    metrics=metrics,
                    target_upem=target_upem,
                    cell_width=cell_width,
                )
            if all(half_glyph_name(form, h) in glyphs for h in HALF_SUFFIXES):
                added.append(form)

    return added


# Back-compat alias used by build_yi.
add_slice_quadrants_and_roles = add_slice_halves


def inject_slice_marks(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
) -> List[str]:
    """Ensure FE08/FE09 zero-width mark glyphs exist and are cmap'd."""
    names: List[str] = []
    for cp, gname, _a, _b in SLICE_MODES:
        if gname not in glyphs:
            glyph_order.append(gname)
            glyphs[gname] = empty_glyph()
            metrics[gname] = (0, 0)
        cmap[cp] = gname
        names.append(gname)
    return names


def install_slice_gsub(
    font,
    full_forms: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
    max_stack: int = 8,
    slice_adv_name: str = SLICE_ADV_NAME,
) -> int:
    """Install FE08/FE09 slice lookups (single/multiple-subst + mark consume).

    For each mark::

        A'  B  mark  →  A.half   (0-width)
        B'  mark     →  B.half  sliceAdv
        sliceAdv + mark → sliceAdv

    Chain contexts use Format 2 (class-based) and Extension lookups so the
    GSUB table stays within Offset16 limits without hb.repack splitting.
    """
    from fontTools.otlLib.builder import buildLigatureSubstSubtable
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    from shared_half_cells import (
        build_chain_context_format2,
        build_chunked_multiple_subst_lookup,
        build_chunked_single_subst_lookup,
        build_ext_gsub_lookup,
    )

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    forms = _gid_sort([n for n in full_forms if n in glyphs])
    if not forms or slice_adv_name not in glyphs:
        return 0

    forms = _gid_sort(
        [
            n
            for n in forms
            if all(half_glyph_name(n, h) in glyphs for h in HALF_SUFFIXES)
        ]
    )
    if not forms:
        return 0

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

    feature_lookup_idxs: List[int] = []

    consume_map = {
        (slice_adv_name, mname): slice_adv_name
        for _cp, mname, _a, _b in SLICE_MODES
        if mname in glyphs
    }
    consume_sub = buildLigatureSubstSubtable(consume_map) if consume_map else None
    consume_lu = build_ext_gsub_lookup([consume_sub]) if consume_sub else None
    consume_index_holder: List[int] = []

    # Shared class maps: forms=1; each mark gets its own lookahead class.
    form_cls = {n: 1 for n in forms}

    for _cp, mark_name, first_half, second_half in SLICE_MODES:
        if mark_name not in glyphs:
            continue

        first_map = {name: half_glyph_name(name, first_half) for name in forms}
        second_map = {
            name: [half_glyph_name(name, second_half), slice_adv_name] for name in forms
        }

        first_lu = build_chunked_single_subst_lookup(first_map)
        second_lu = build_chunked_multiple_subst_lookup(second_map)

        # A' with lookahead B(form) mark → A.half
        st_a = build_chain_context_format2(
            coverage_glyphs=forms,
            input_classes=form_cls,
            input_class=1,
            lookahead_classes={**form_cls, mark_name: 2},
            lookahead_seq=(1, 2),
        )
        chain_a = build_ext_gsub_lookup([st_a])

        # B' with lookahead mark → B.half + sliceAdv
        st_b = build_chain_context_format2(
            coverage_glyphs=forms,
            input_classes=form_cls,
            input_class=1,
            lookahead_classes={mark_name: 1},
            lookahead_seq=(1,),
        )
        chain_b = build_ext_gsub_lookup([st_b])

        base = gsub.LookupList.LookupCount
        chain_a_i = base
        first_i = base + 1
        chain_b_i = base + 2
        second_i = base + 3
        st_a.ChainSubClassSet[1].ChainSubClassRule[0].SubstLookupRecord[
            0
        ].LookupListIndex = first_i
        st_b.ChainSubClassSet[1].ChainSubClassRule[0].SubstLookupRecord[
            0
        ].LookupListIndex = second_i
        gsub.LookupList.Lookup.extend([chain_a, first_lu, chain_b, second_lu])
        gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)

        if consume_lu is not None and not consume_index_holder:
            consume_index_holder.append(gsub.LookupList.LookupCount)
            gsub.LookupList.Lookup.append(consume_lu)
            gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)

        for _ in range(max(1, max_stack)):
            feature_lookup_idxs.append(chain_a_i)
            feature_lookup_idxs.append(chain_b_i)
            if consume_index_holder:
                feature_lookup_idxs.append(consume_index_holder[0])

    if not feature_lookup_idxs:
        return 0

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
