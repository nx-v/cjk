"""Yi corner \"dakuten\" diacritics from JuliaMono.

Inventory: ``(\\p{M} ∩ JuliaMono) ∖ {names containing \"letter\"}``, then drop
enclosing / overlay marks and oversized outlines.

Marks attach via GPOS ``mark`` / ``abvm`` at **fixed CJK cell corners**.
Successive marks fill slots in order via GSUB cycling::

    1st → top-right
    2nd → bottom-right
    3rd → top-left
    4th → bottom-left

Bases include Yi identity + VS01..VS07 orientations and shared ``sliceAdv``
after FE08–FE09 expansion. No left-squish forms.
"""

from __future__ import annotations

import os
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import otTables as ot
from fontTools.ttLib.tables._g_l_y_f import (
    ROUND_XY_TO_GRID,
    UNSCALED_COMPONENT_OFFSET,
    Glyph as TTGlyph,
    GlyphComponent,
)

from yi_halfwidth import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    YI_ORIENTATION_MODES,
    apply_transform,
    orientation_form_names,
    recording_bounds,
)

JULIAMONO_FILENAME = "JuliaMono-Regular.ttf"

# Unicode Mark = Mn | Mc | Me  (regex \p{M}).
MARK_CATS = frozenset({"Mn", "Mc", "Me"})

MAX_DIACRITIC_FRAC = 0.48

# VS01..VS07 (modes 0..6); VS08 / r90my is not a dakuten base.
DAKUTEN_VS_MODE_COUNT = 7

DAKUTEN_EDGE_PAD_FRAC = 0.03

# Successive-mark corner order (mark class index = position in this tuple).
# Suffix None → cmap glyph ``uXXXX.mk`` (top-right); others are composites.
DAKUTEN_SLOTS: Tuple[Tuple[str, Optional[str]], ...] = (
    ("tr", None),
    ("br", "br"),
    ("tl", "tl"),
    ("bl", "bl"),
)

GDEF_CLASS_BASE = 1
GDEF_CLASS_MARK = 3
MARK_FEATURE_TAGS: Tuple[str, ...] = ("mark", "abvm")


def resolve_juliamono_path(in_dir: str) -> str:
    path = os.path.join(in_dir, JULIAMONO_FILENAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing JuliaMono source font: {path}")
    return path


def _name_has_letter(name: str) -> bool:
    return "letter" in name.lower()


def _is_superimposed_mark(name: str, cat: str) -> bool:
    if cat == "Me":
        return True
    u = name.upper()
    return "OVERLAY" in u or "ENCLOSING" in u


def _glyph_ink_size(glyph_set, glyph_name: str) -> Optional[Tuple[float, float]]:
    try:
        pen = BoundsPen(glyph_set)
        glyph_set[glyph_name].draw(pen)
    except Exception:
        return None
    if not pen.bounds:
        return None
    x0, y0, x1, y1 = pen.bounds
    return (x1 - x0), (y1 - y0)


def iter_dakuten_codepoints(cmap: Dict[int, str]) -> List[int]:
    """``(\\p{M} ∩ cmap) − {names containing letter}``."""
    out: List[int] = []
    for cp in sorted(cmap):
        ch = chr(cp)
        cat = unicodedata.category(ch)
        if cat not in MARK_CATS:
            continue
        name = unicodedata.name(ch, "")
        if _name_has_letter(name):
            continue
        out.append(cp)
    return out


def dakuten_mark_name(cp: int) -> str:
    return f"u{cp:04X}.mk" if cp <= 0xFFFF else f"u{cp:05X}.mk"


def dakuten_mark_slot_name(cp: int, slot_suffix: Optional[str]) -> str:
    base = dakuten_mark_name(cp)
    return base if not slot_suffix else f"{base}.{slot_suffix}"


def dakuten_orientation_modes(
    modes: Optional[Sequence] = None,
) -> List:
    use = list(modes) if modes is not None else list(YI_ORIENTATION_MODES)
    return use[:DAKUTEN_VS_MODE_COUNT]


def yi_forms_for_dakuten(
    yi_bases: Sequence[str],
    *,
    modes=None,
) -> List[str]:
    """Identity + VS01..VS07 forms that may take dakuten."""
    names: List[str] = []
    for base in yi_bases:
        names.extend(
            orientation_form_names(base, modes=dakuten_orientation_modes(modes))
        )
    return names


def cjk_corner_anchors(target_upem: int) -> Dict[str, Tuple[int, int]]:
    """Fixed dakuten positions at the four CJK typo-box corners."""
    edge = target_upem * DAKUTEN_EDGE_PAD_FRAC
    typo_top = target_upem * TYPO_ASCENDER_FRAC
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    x_r = otRound(target_upem - edge)
    x_l = otRound(edge)
    y_t = otRound(typo_top - edge)
    y_b = otRound(typo_bot + edge)
    return {
        "tr": (x_r, y_t),
        "br": (x_r, y_b),
        "tl": (x_l, y_t),
        "bl": (x_l, y_b),
    }


def cjk_top_right_anchor(target_upem: int) -> Tuple[int, int]:
    """Back-compat: fixed CJK typo-box top-right."""
    return cjk_corner_anchors(target_upem)["tr"]


def make_dakuten_mark_glyph(
    rec: RecordingPen,
    *,
    scale: float,
) -> Optional[TTGlyph]:
    """Scale mark outline and pin ink center to ``(0, 0)`` (GPOS mark anchor)."""
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


def _mark_slot_composite(base_name: str) -> TTGlyph:
    """Zero-offset composite alias of ``base_name`` (extra mark-class GID)."""
    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = base_name
    comp.x = 0
    comp.y = 0
    comp.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [comp]
    return g


def load_dakuten_marks(
    juliamono_path: str,
    target_upem: int,
) -> Tuple[List[int], Dict[int, TTGlyph]]:
    tt = TTFont(juliamono_path, fontNumber=0)
    try:
        cmap: Dict[int, str] = {}
        for table in tt["cmap"].tables:
            if table.isUnicode():
                cmap.update(table.cmap)
        glyph_set = tt.getGlyphSet()
        src_upem = float(tt["head"].unitsPerEm)
        scale = float(target_upem) / src_upem if src_upem else 1.0
        max_ext = src_upem * MAX_DIACRITIC_FRAC

        cps: List[int] = []
        glyphs: Dict[int, TTGlyph] = {}
        for cp in iter_dakuten_codepoints(cmap):
            ch = chr(cp)
            cat = unicodedata.category(ch)
            name = unicodedata.name(ch, "")
            if _is_superimposed_mark(name, cat):
                continue
            gname = cmap[cp]
            sized = _glyph_ink_size(glyph_set, gname)
            if sized is None:
                continue
            w, h = sized
            if max(w, h) > max_ext + 1e-6:
                continue
            rec = DecomposingRecordingPen(glyph_set)
            try:
                glyph_set[gname].draw(rec)
            except Exception:
                continue
            mark = make_dakuten_mark_glyph(rec, scale=scale)
            if mark is None:
                continue
            cps.append(cp)
            glyphs[cp] = mark
        return cps, glyphs
    finally:
        tt.close()


def add_dakuten_mark_glyphs(
    mark_cps: Sequence[int],
    mark_glyphs: Dict[int, TTGlyph],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
) -> List[str]:
    """Install cmap ``.mk`` glyphs plus ``.br``/``.tl``/``.bl`` slot composites.

    Returns every mark glyph name (all slots) for GPOS / GDEF.
    """
    names: List[str] = []
    for cp in mark_cps:
        g = mark_glyphs.get(cp)
        if g is None:
            continue
        base = dakuten_mark_name(cp)
        if base not in glyphs:
            glyph_order.append(base)
            glyphs[base] = g
            try:
                g.recalcBounds(None)
                lsb = int(g.xMin)
            except Exception:
                lsb = 0
            metrics[base] = (0, lsb)
        cmap[cp] = base
        names.append(base)
        for _slot, suffix in DAKUTEN_SLOTS:
            if not suffix:
                continue
            sname = dakuten_mark_slot_name(cp, suffix)
            if sname in glyphs:
                names.append(sname)
                continue
            glyph_order.append(sname)
            glyphs[sname] = _mark_slot_composite(base)
            metrics[sname] = (0, metrics[base][1])
            names.append(sname)
    return names


def collect_dakuten_base_anchors(
    base_names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    target_upem: int,
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    """Map bases → ``{mark_class: (x, y)}`` for all four CJK corners."""
    corners = cjk_corner_anchors(target_upem)
    class_xy = {
        i: corners[slot] for i, (slot, _suf) in enumerate(DAKUTEN_SLOTS)
    }
    anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for name in base_names:
        if name in glyphs:
            anchors[name] = dict(class_xy)
    return anchors


def _langsys_with_features(feature_indices: Sequence[int]) -> ot.DefaultLangSys:
    ls = ot.DefaultLangSys()
    ls.ReqFeatureIndex = 0xFFFF
    ls.FeatureCount = len(feature_indices)
    ls.FeatureIndex = list(feature_indices)
    return ls


def _ensure_gpos(font, script_tags: Sequence[str]) -> ot.GPOS:
    if "GPOS" in font:
        return font["GPOS"].table

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


def install_dakuten_slot_gsub(
    font,
    mark_cps: Sequence[int],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
    base_names: Sequence[str],
) -> int:
    """Cycle successive ``.mk`` marks into ``.br`` / ``.tl`` / ``.bl`` slots.

    Transitions::

        (base, .mk) + .mk  →  .br     # needs base so a later TR-class mark
                                      # (e.g. 3rd before TL rewrite) cannot
                                      # falsely trigger TR→BR again
        .br + .mk          →  .tl
        .tl + .mk          →  .bl

    Uses Format 2 ChainContext + Extension lookups (compact; no type-6 split).
    """
    from yi_halfwidth import (
        build_chain_context_format2,
        build_chunked_single_subst_lookup,
        build_ext_gsub_lookup,
    )

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    slot_lists: List[List[str]] = [[] for _ in DAKUTEN_SLOTS]
    for cp in mark_cps:
        names = [dakuten_mark_slot_name(cp, suf) for _slot, suf in DAKUTEN_SLOTS]
        if not all(n in glyphs for n in names):
            continue
        for i, n in enumerate(names):
            slot_lists[i].append(n)
    slot_lists = [_gid_sort(lst) for lst in slot_lists]
    bases = _gid_sort([n for n in base_names if n in glyphs])
    if not slot_lists[0] or not bases:
        return 0

    if "GSUB" not in font:
        return 0
    gsub = font["GSUB"].table
    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0

    feature_lookup_idxs: List[int] = []
    input_cls = {n: 1 for n in slot_lists[0]}

    for i in range(len(DAKUTEN_SLOTS) - 1):
        _slot, next_suf = DAKUTEN_SLOTS[i + 1]
        mapping = {
            dakuten_mark_name(cp): dakuten_mark_slot_name(cp, next_suf)
            for cp in mark_cps
            if dakuten_mark_name(cp) in glyphs
            and dakuten_mark_slot_name(cp, next_suf) in glyphs
        }
        if not mapping or not slot_lists[i]:
            continue
        single_lu = build_chunked_single_subst_lookup(mapping)

        if i == 0:
            # Closest backtrack = TR mark (class 1), then a dakuten base (class 2).
            bt_cls = {**{n: 1 for n in slot_lists[0]}, **{n: 2 for n in bases}}
            bt_seq = (1, 2)
        else:
            bt_cls = {n: 1 for n in slot_lists[i]}
            bt_seq = (1,)

        st = build_chain_context_format2(
            coverage_glyphs=slot_lists[0],
            input_classes=input_cls,
            input_class=1,
            backtrack_classes=bt_cls,
            backtrack_seq=bt_seq,
        )
        chain_lu = build_ext_gsub_lookup([st])

        base = gsub.LookupList.LookupCount
        chain_i = base
        single_i = base + 1
        st.ChainSubClassSet[1].ChainSubClassRule[0].SubstLookupRecord[
            0
        ].LookupListIndex = single_i
        gsub.LookupList.Lookup.extend([chain_lu, single_lu])
        gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
        feature_lookup_idxs.append(chain_i)

    if not feature_lookup_idxs:
        return 0

    tag_to_fr = {
        fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])
    }
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


def _ensure_gpos_scripts(gpos: ot.GPOS, script_tags: Sequence[str]) -> None:
    """Ensure each ``script_tags`` entry exists on ``gpos.ScriptList``."""
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
        existing.add(tag)
    gpos.ScriptList.ScriptCount = len(gpos.ScriptList.ScriptRecord)


def install_dakuten_gpos(
    font,
    *,
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]],
    mark_cps: Sequence[int],
    mark_names: Sequence[str],
    glyph_order: Sequence[str],
    extra_script_tags: Sequence[str] = (),
    base_chunk: int = 2048,
) -> int:
    """Install ``mark``/``abvm`` MarkToBase at four CJK corners.

    ``extra_script_tags`` (e.g. ``hang``) are merged into the GPOS script list
    alongside ``COMPOSITION_LANGUAGE_SYSTEMS``. Large base inventories are
    split into Extension MarkToBase subtables.
    """
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
    for tag in extra_script_tags:
        t = tag.ljust(4)[:4]
        if t not in script_tags:
            script_tags.append(t)

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
    marks: Dict[str, Tuple[int, object]] = {}
    for cp in mark_cps:
        for class_id, (_slot, suf) in enumerate(DAKUTEN_SLOTS):
            name = dakuten_mark_slot_name(cp, suf)
            if name not in order_index:
                continue
            marks[name] = (class_id, buildAnchor(0, 0))
    if not marks:
        return 0

    subs = []
    for i in range(0, len(bases_sorted), max(1, base_chunk)):
        chunk = bases_sorted[i : i + base_chunk]
        bases = {
            n: {
                class_id: buildAnchor(xy[0], xy[1])
                for class_id, xy in base_anchors[n].items()
            }
            for n in chunk
        }
        subs.append(buildMarkBasePosSubtable(marks, bases, glyph_map))
    lookup = buildLookup(subs, table="GPOS", extension=True)

    gpos = _ensure_gpos(font, script_tags)
    _ensure_gpos_scripts(gpos, script_tags)
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
    feature_indices_for_scripts: List[int] = []
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
        feature_indices_for_scripts.append(feature_index)
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
        for feature_index in feature_indices_for_scripts:
            if feature_index not in fi:
                fi.append(feature_index)
        ls.FeatureIndex = fi
        ls.FeatureCount = len(fi)

    _ensure_gdef_classes(
        font,
        bases=bases_sorted,
        marks=list(marks),
        glyph_order=glyph_order,
    )
    return len(bases_sorted)
