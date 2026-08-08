"""Vietnamese combining marks U+16FF0 / U+16FF1 for Pan-CJK subfonts.

Sourced from Plangothic P2. Encoding::

    CJK  ( VS01..VS07 )?  ( U+16FF0 | U+16FF1 )  ( VS01..VS08 )?

* When a Viet mark follows, the CJK form (identity or VS1–7) is Width-squished
  left (``.dk``) so a niche opens on the right — CAPE Weightor preserves
  vertical stem thickness.
* Marks themselves take full D4; sideways forms (r90 / r270 / r90mx / r90my)
  restore H/V contrast and Width-fit to the average upright mark ink width.
* GPOS ``mark``/``abvm`` pins every mark form to a fixed CJK **right-side**
  anchor (marks stay on the right regardless of their own orientation).
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
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

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
    orientation_form_names,
    recording_bounds,
    variant_glyph_name,
    vs_glyph_name,
)

PLANGOTHIC_P2_FILENAME = "PlangothicP2-Regular.ttf"
VIET_MARK_CPS: Tuple[int, ...] = (0x16FF0, 0x16FF1)

# VS01..VS07 only (identity + six non-identity); VS08 / r90my excluded.
VIET_BASE_VS_MODE_COUNT = 7
VIET_SQUISH_FACTOR = 0.88
VIET_EDGE_PAD_FRAC = 0.03
VIET_GAP_FRAC = 0.02

GDEF_CLASS_BASE = 1
GDEF_CLASS_MARK = 3
MARK_FEATURE_TAGS: Tuple[str, ...] = ("mark", "abvm")


def resolve_plangothic_p2(in_dir: str) -> str:
    path = os.path.join(in_dir, PLANGOTHIC_P2_FILENAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing Plangothic P2: {path}")
    return path


def viet_squish_name(base_name: str) -> str:
    return f"{base_name}.dk"


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
    """Identity + VS01..VS07 forms that may take a Viet mark."""
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


def add_viet_mark_glyphs(
    mark_cps: Sequence[int],
    mark_glyphs: Dict[int, TTGlyph],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int,
) -> List[str]:
    """Install upright marks + full D4 (sideways Width-fit). Returns all mark names."""
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
    all_names: List[str] = list(upright)
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
        )
        for _vs, _sfx, vname in installed:
            # Oriented marks stay zero-advance.
            metrics[vname] = (0, metrics[vname][1])
            all_names.append(vname)
    return all_names


def viet_mark_liga_rules(mark_cps: Sequence[int], glyphs: Dict[str, TTGlyph]) -> List[str]:
    """FEA ``sub mark vsNN by mark.suffix`` lines for mark D4."""
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
    return rules


def make_viet_squished_glyph(
    glyph: TTGlyph,
    advance: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    factor: float = VIET_SQUISH_FACTOR,
) -> Tuple[TTGlyph, int, int]:
    """Left-condense with CAPE Width mode; keep em advance; pin left ink edge."""
    simple = _bake_simple_glyph(glyph, glyph_set)
    layer = layer_from_ttglyph(simple, float(advance))
    if not layer.paths:
        return simple, int(advance), int(getattr(simple, "xMin", 0) or 0)

    _ = estimate_horizontal_stem(layer)
    vstem = estimate_vertical_stem(layer)
    left = layer.bounds.origin.x
    apply_width(layer, factor, stem=vstem if vstem > 0 else None)
    nb = layer.bounds
    dx = left - nb.origin.x
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
) -> List[str]:
    """Create ``.dk`` left-squished forms. Returns base names that received ``.dk``."""
    added: List[str] = []
    for name in base_names:
        if name not in glyphs:
            continue
        dk = viet_squish_name(name)
        if dk in glyphs:
            added.append(name)
            continue
        adv, _lsb = metrics.get(name, (1000, 0))
        sq, sq_adv, sq_lsb = make_viet_squished_glyph(
            glyphs[name],
            adv,
            glyph_set=glyphs,
            factor=factor,
        )
        glyph_order.append(dk)
        glyphs[dk] = sq
        metrics[dk] = (sq_adv, sq_lsb)
        added.append(name)
    return added


def cjk_right_anchor(
    glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[int, int]:
    """Right-side attach point (niche after squish, clamped to CJK cell)."""
    typo_top = target_upem * TYPO_ASCENDER_FRAC
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    right = float(advance) if advance > 0 else float(target_upem)
    gap = target_upem * VIET_GAP_FRAC
    edge = target_upem * VIET_EDGE_PAD_FRAC
    mid_y = (typo_top + typo_bot) / 2.0

    x1 = right * 0.85
    y_mid = mid_y
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        x1 = float(glyph.xMax)
        y_mid = (float(glyph.yMin) + float(glyph.yMax)) / 2.0
    except Exception:
        pass

    ax = min(max(x1 + gap, edge), right - edge)
    ay = min(max(y_mid, typo_bot + edge), typo_top - edge)
    return otRound(ax), otRound(ay)


def collect_viet_base_anchors(
    squishable_bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
) -> Dict[str, Tuple[int, int]]:
    """Map ``.dk`` forms → right-side anchors."""
    anchors: Dict[str, Tuple[int, int]] = {}
    for name in squishable_bases:
        dk = viet_squish_name(name)
        if dk not in glyphs:
            continue
        adv, _lsb = metrics.get(dk, (target_upem, 0))
        anchors[dk] = cjk_right_anchor(
            glyphs[dk], adv, target_upem, glyph_set=glyphs
        )
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
    mark_names: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
) -> int:
    """``base' mark → base.dk`` (Format 2 chain + Extension)."""
    if "GSUB" not in font:
        return 0

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    bases = _gid_sort(
        [n for n in squishable_bases if n in glyphs and viet_squish_name(n) in glyphs]
    )
    marks = _gid_sort([n for n in mark_names if n in glyphs])
    if not bases or not marks:
        return 0

    squish_map = {n: viet_squish_name(n) for n in bases}
    single_lu = build_chunked_single_subst_lookup(squish_map)

    st = build_chain_context_format2(
        coverage_glyphs=bases,
        input_classes={n: 1 for n in bases},
        input_class=1,
        lookahead_classes={n: 1 for n in marks},
        lookahead_seq=(1,),
    )
    chain_lu = build_ext_gsub_lookup([st])

    gsub = font["GSUB"].table
    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0

    chain_index = gsub.LookupList.LookupCount
    single_index = chain_index + 1
    st.ChainSubClassSet[1].ChainSubClassRule[0].SubstLookupRecord[
        0
    ].LookupListIndex = single_index
    gsub.LookupList.Lookup.extend([chain_lu, single_lu])
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)

    tag_to_fr = {fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])}
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            continue
        idxs = list(fr.Feature.LookupListIndex or [])
        if chain_index not in idxs:
            idxs.append(chain_index)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)
    return len(bases)


def install_viet_mark_gpos(
    font,
    *,
    base_anchors: Dict[str, Tuple[int, int]],
    mark_names: Sequence[str],
    glyph_order: Sequence[str],
    base_chunk: int = 2048,
) -> int:
    """MarkToBase at right-side anchors (Extension + chunked subtables)."""
    if not base_anchors or not mark_names:
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
    marks_sorted = [
        n
        for n in sorted(set(mark_names), key=lambda n: order_index.get(n, 10**9))
        if n in order_index
    ]
    bases_sorted = [
        n
        for n in sorted(base_anchors, key=lambda n: order_index.get(n, 10**9))
        if n in order_index
    ]
    if not marks_sorted or not bases_sorted:
        return 0

    glyph_map = {n: i for i, n in enumerate(glyph_order)}
    marks = {n: (0, buildAnchor(0, 0)) for n in marks_sorted}

    subs = []
    for i in range(0, len(bases_sorted), max(1, base_chunk)):
        chunk = bases_sorted[i : i + base_chunk]
        bases = {
            n: {0: buildAnchor(base_anchors[n][0], base_anchors[n][1])}
            for n in chunk
        }
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
        marks=marks_sorted,
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
) -> Optional[Dict[str, List[str]]]:
    """Load marks + ``.dk`` forms and append mark D4 ligas (before FontBuilder).

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

    mark_names = add_viet_mark_glyphs(
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
    add_viet_squish_forms(
        squishable,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
    )
    return {
        "mark_names": list(mark_names),
        "squishable": list(squishable),
    }


def compile_viet_marks_layout(
    font,
    state: Dict[str, List[str]],
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    glyph_order: Sequence[str],
    target_upem: int,
) -> int:
    """Install squish GSUB + right-side MarkToBase GPOS (after other GSUB)."""
    mark_names = state["mark_names"]
    squishable = state["squishable"]
    install_viet_squish_gsub(
        font,
        squishable_bases=squishable,
        mark_names=mark_names,
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
        mark_names=mark_names,
        glyph_order=glyph_order,
    )
