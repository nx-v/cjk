"""Yi FE08–FE09 half-plane slice overlays (horizontal / vertical only).

Encoding
--------
* ``U+FE08`` horizontal — A top + B bottom
* ``U+FE09`` vertical   — A left + B right

Budget
------
Each Yi form stores **four** CJK-box half-plane clips (all zero-advance). A single
shared ``sliceAdv`` empty glyph carries the em advance after the second half.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from yi_halfwidth import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    _recording_from_glyph,
    empty_glyph,
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


def slice_mark_cps() -> List[int]:
    return [cp for cp, _n, _a, _b in SLICE_MODES]


def half_glyph_name(base_name: str, half: str) -> str:
    return f"{base_name}.{half}"


def cjk_box(target_upem: int) -> Tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` of the CJK typo cell."""
    y1 = target_upem * TYPO_ASCENDER_FRAC
    y0 = target_upem * TYPO_DESCENDER_FRAC
    return 0.0, y0, float(target_upem), y1


def cjk_mid(target_upem: int) -> Tuple[float, float]:
    x0, y0, x1, y1 = cjk_box(target_upem)
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
) -> TTGlyph:
    """Intersect ``glyph`` with one CJK-box half-plane (top/bot/left/right)."""
    import pathops

    mx, my = cjk_mid(target_upem)
    x0, y0, x1, y1 = cjk_box(target_upem)
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
) -> str:
    """Shared empty glyph that carries the em advance after the second half."""
    if SLICE_ADV_NAME not in glyphs:
        glyph_order.append(SLICE_ADV_NAME)
        glyphs[SLICE_ADV_NAME] = empty_glyph()
        metrics[SLICE_ADV_NAME] = (int(target_upem), 0)
    return SLICE_ADV_NAME


def add_slice_halves(
    form_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
    limit: Optional[int] = None,
) -> List[str]:
    """Install four zero-advance half-plane clips per form (+ shared ``sliceAdv``).

    Returns the list of base form names that received halves.
    """
    ensure_slice_adv(glyph_order, glyphs, metrics, target_upem)
    added: List[str] = []
    for name in form_names:
        if limit is not None and len(added) >= limit:
            break
        if name not in glyphs:
            continue
        for half in HALF_SUFFIXES:
            hname = half_glyph_name(name, half)
            if hname in glyphs:
                continue
            clipped = clip_glyph_to_half(
                glyphs[name], half, target_upem, glyph_set=glyphs
            )
            try:
                clipped.recalcBounds(None)
                h_lsb = int(clipped.xMin)
            except Exception:
                h_lsb = 0
            glyph_order.append(hname)
            glyphs[hname] = clipped
            metrics[hname] = (0, h_lsb)
        added.append(name)
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

    from yi_halfwidth import (
        build_chain_context_format2,
        build_chunked_multiple_subst_lookup,
        build_chunked_single_subst_lookup,
        build_ext_gsub_lookup,
    )

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    forms = _gid_sort([n for n in full_forms if n in glyphs])
    if not forms or SLICE_ADV_NAME not in glyphs:
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
        (SLICE_ADV_NAME, mname): SLICE_ADV_NAME
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
            name: [half_glyph_name(name, second_half), SLICE_ADV_NAME] for name in forms
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
