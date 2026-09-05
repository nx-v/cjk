"""Combining half-plane and triangle slices for Yi / kana.

Encoding
--------
Preceding glyph + selector occupies that segment (full cell advance):

    U+FE08  top half          `.top`
    U+FE09  bottom half       `.bot`
    U+FE0A  left half         `.left`
    U+FE0B  right half        `.right`
    U+FE0C  top-left Δ        `.tl`
    U+FE0D  bottom-right Δ    `.br`
    U+FE0E  top-right Δ       `.tr`
    U+FE0F  bottom-left Δ     `.bl`

`U+FE00` makes the preceding form zero-width (`.ov`) so the next
glyph stacks in the same cell:

    A FE08 FE00 B FE09   →  A.top.ov + B.bot

Identity + D4 forms store eight slices: **clip each side** of a complementary
pair to its half-plane / triangle (never `full − piece` — pathops difference
leaves cut-line spikes). Other orientations are D4 of the identity clips
(`propagate_d4_segments`).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from shared_cells import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    HALF_PLANE_INF_FRAC,
    HALF_SUFFIXES,
    OV_SELECTOR_NAME,
    SLICE_LABELS,
    SLICE_SUFFIXES,
    SLICE_VS_SLOTS,
    TRANSFORM_MODES,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    TransformMode,
    add_overlay_forms,
    build_chunked_ligature_subst_lookup,
    clip_glyph_to_polygon,
    clip_glyph_to_rect,
    ideographic_center,
    inject_slice_selectors,
    install_derived_glyph,
    propagate_d4_segments,
    slice_form_name,
    slice_overlay_liga_map,
    triangle_clip_points,
    variant_glyph_name,
)

# Compat aliases — same combining selectors as CJK.
SLICE_TOP_CP, SLICE_BOT_CP, SLICE_LEFT_CP, SLICE_RIGHT_CP = (
    SLICE_VS_SLOTS[0][0],
    SLICE_VS_SLOTS[1][0],
    SLICE_VS_SLOTS[2][0],
    SLICE_VS_SLOTS[3][0],
)
SLICE_TL_CP, SLICE_BR_CP, SLICE_TR_CP, SLICE_BL_CP = (
    SLICE_VS_SLOTS[4][0],
    SLICE_VS_SLOTS[5][0],
    SLICE_VS_SLOTS[6][0],
    SLICE_VS_SLOTS[7][0],
)
# Former pair-joiners (H = top+bot, V = left+right).
SLICE_H_CP = SLICE_TOP_CP
SLICE_V_CP = SLICE_LEFT_CP

# Unused: combining slices keep the cell advance; overlay zeros the first.
SLICE_ADV_NAME = "sliceAdv"

half_glyph_name = slice_form_name


def slice_mark_cps() -> List[int]:
    return [cp for cp, _n, _s in SLICE_VS_SLOTS]


def cjk_box(
    target_upem: int,
    *,
    cell_width: Optional[float] = None,
    cell_x0: float = 0.0,
) -> Tuple[float, float, float, float]:
    """`(x0, y0, x1, y1)` of the CJK typo cell (or a narrower halfwidth cell)."""
    y1 = target_upem * TYPO_ASCENDER_FRAC
    y0 = target_upem * TYPO_DESCENDER_FRAC
    w = float(target_upem if cell_width is None else cell_width)
    return float(cell_x0), y0, float(cell_x0) + w, y1


def clip_glyph_to_half(
    glyph: TTGlyph,
    half: str,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    cell_width: Optional[float] = None,
) -> TTGlyph:
    """Intersect `glyph` with one CJK-box half-plane (top/bot/left/right)."""
    x0, y0, x1, y1 = cjk_box(target_upem, cell_width=cell_width)
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    inf = target_upem * HALF_PLANE_INF_FRAC
    match half:
        case "top":
            rect = (-inf, my, inf, inf)
        case "bot":
            rect = (-inf, -inf, inf, my)
        case "left":
            rect = (-inf, -inf, mx, inf)
        case "right":
            rect = (mx, -inf, inf, inf)
        case _:
            raise ValueError(f"unknown half-plane {half!r}")
    return clip_glyph_to_rect(glyph, rect, glyph_set=glyph_set)


def clip_glyph_to_triangle(
    glyph: TTGlyph,
    kind: str,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    cell_width: Optional[float] = None,
) -> TTGlyph:
    """Intersect `glyph` with one diagonal half-plane (tl/br/tr/bl)."""
    x0, y0, x1, y1 = cjk_box(target_upem, cell_width=cell_width)
    inf = target_upem * HALF_PLANE_INF_FRAC
    pts = triangle_clip_points(kind, x0=x0, y0=y0, x1=x1, y1=y1, inf=inf)
    return clip_glyph_to_polygon(glyph, pts, glyph_set=glyph_set)


def _put_slice(
    name: str,
    glyph: TTGlyph,
    *,
    advance: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
) -> None:
    try:
        glyph.recalcBounds(None)
        lsb = int(glyph.xMin)
    except Exception:
        lsb = 0
    install_derived_glyph(
        name,
        (glyph, int(advance), lsb),
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        advance=int(advance),
    )


def _bake_slices_for_form(
    form_name: str,
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
    cell_width: Optional[float] = None,
) -> None:
    """Clip every half / triangle to its keep region (clean cut, no subtract)."""
    adv = int(target_upem if cell_width is None else cell_width)

    def _clip(kind: str) -> TTGlyph:
        if kind in HALF_SUFFIXES:
            return clip_glyph_to_half(
                glyphs[form_name],
                kind,
                target_upem,
                glyph_set=glyphs,
                cell_width=cell_width,
            )
        return clip_glyph_to_triangle(
            glyphs[form_name],
            kind,
            target_upem,
            glyph_set=glyphs,
            cell_width=cell_width,
        )

    for kind in SLICE_SUFFIXES:
        n = half_glyph_name(form_name, kind)
        if n in glyphs:
            continue
        _put_slice(
            n,
            _clip(kind),
            advance=adv,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )


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
    overlays: bool = True,
) -> List[str]:
    """Bake identity slices + D4 copies; optionally add `.ov` overlays.

    `slice_adv_name` is ignored (combining slices keep the cell advance).
    `base_names` are identity glyph names (not pre-expanded orientations).
    Returns every form name (id + variants) that received a full slice set.
    """
    del modes, slice_adv_name
    adv = int(target_upem if cell_width is None else cell_width)
    added: List[str] = []
    identity: List[str] = []

    for base in base_names:
        if limit is not None and len(added) >= limit:
            break
        if base not in glyphs:
            continue
        _bake_slices_for_form(
            base,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            cell_width=cell_width,
        )
        if all(half_glyph_name(base, h) in glyphs for h in SLICE_SUFFIXES):
            added.append(base)
            identity.append(base)

    cx = (float(adv) / 2.0, ideographic_center(target_upem)[1])
    if identity:
        propagate_d4_segments(
            identity,
            suffixes=SLICE_SUFFIXES,
            form_name=half_glyph_name,
            windows={},
            labels=SLICE_LABELS,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            center=cx,
        )

    forms: List[str] = []
    seen: set = set()
    for base in identity:
        for name in (
            base,
            *(
                variant_glyph_name(base, suf)
                for _vs, _r, _fx, _fy, suf in TRANSFORM_MODES
                if suf is not None
            ),
        ):
            if name in glyphs and name not in seen:
                if all(half_glyph_name(name, h) in glyphs for h in SLICE_SUFFIXES):
                    forms.append(name)
                    seen.add(name)
                    if name not in added:
                        added.append(name)

    if overlays:
        ov_sources: List[str] = []
        for form in forms:
            ov_sources.append(form)
            for suf in SLICE_SUFFIXES:
                sliced = half_glyph_name(form, suf)
                if sliced in glyphs:
                    ov_sources.append(sliced)
        add_overlay_forms(
            ov_sources,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
    return added


# Back-compat alias used by build_yi.
add_slice_quadrants_and_roles = add_slice_halves


def inject_slice_marks(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    *,
    modes: Optional[Sequence[Tuple[int, str, str, str]]] = None,
    pua: bool = False,
) -> List[str]:
    """Ensure overlay + slice-mark glyphs exist and are cmap'd."""
    del modes
    return inject_slice_selectors(glyph_order, glyphs, metrics, cmap, pua=pua)


def install_slice_gsub(
    font,
    full_forms: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
    max_stack: int = 8,
    slice_adv_name: str = SLICE_ADV_NAME,
    modes: Optional[Sequence[Tuple[int, str, str, str]]] = None,
) -> int:
    """Install combining slice + FE00 overlay ligatures.

    ::

        A  FE08       →  A.top
        A  FE00       →  A.ov
        A  FE08 FE00  →  A.top.ov   (either order)
    """
    del max_stack, slice_adv_name, modes, glyph_order
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    forms = [
        n
        for n in full_forms
        if n in glyphs and all(half_glyph_name(n, h) in glyphs for h in SLICE_SUFFIXES)
    ]
    if not forms or OV_SELECTOR_NAME not in glyphs:
        return 0

    liga = slice_overlay_liga_map(forms, glyphs=glyphs, include_vs01=True)
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
