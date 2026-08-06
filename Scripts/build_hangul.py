#!/usr/bin/env python3
"""
Build Hangul fonts from Malgun Gothic.

Two families
------------
* ``panhangul`` — conjoining jamo (U+1100.., Ext-A/B) with Malgun
  ``ljmo`` / ``vjmo`` / ``tjmo`` shaping.
* ``panhanguls`` — precomposed syllables (U+AC00..D7A3) and compatibility
  jamo (U+3131..318E).

VS1..VS4 (axis mirrors)
-----------------------
======= ========== ========== ================================
Name    PUA        Unicode    Transform
======= ========== ========== ================================
VS1     U+E000     U+FE00     identity (no subst)
VS2     U+E001     U+FE01     mx — negate X about contour bbox center
VS3     U+E002     U+FE02     my — negate Y about contour bbox center
VS4     U+E003     U+FE03     mxy — both axes
======= ========== ========== ================================

* **Jamo (``panhangul``):** place VS *after the jongseong* (end of LVT).
  One selector flips **both** choseong and jongseong (jungseong unchanged).
  Open syllables (LV + VS) flip choseong only. Contours flip about each
  glyph's own bbox center (baked outlines).
* **Syllables (``panhanguls``):** ``char + VS`` / cmap-14 UVS flips the
  whole precomposed (or compat) glyph.
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from typing import Dict, List, Optional, Sequence, Set, Tuple

from fontTools import subset
from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.otlLib.builder import (
    buildCoverage,
    buildLigatureSubstSubtable,
    buildLookup,
)
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable, woff2
from fontTools.ttLib.tables import otTables as ot
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from yi_halfwidth import (
    DEFAULT_UPEM,
    empty_glyph,
    variant_glyph_name,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "hangul")

MALGUN_FILENAME = "malgun.ttf"
FAMILY_JAMO = "panhangul"
FAMILY_SYLL = "panhanguls"
LOCAL_SCALE = 0.95

CSS_FONT_URL_BASE = (
    "https://raw.githubusercontent.com/nexovolta/fonts/main/Scripts/dist/hangul"
)

# VS1..VS4 — axis mirrors (PUA U+E000..E003; Unicode VS U+FE00..FE03).
HANGUL_MIRROR_MODES: List[Tuple[int, bool, bool, Optional[str]]] = [
    (0xE000, False, False, None),
    (0xE001, True, False, "mx"),
    (0xE002, False, True, "my"),
    (0xE003, True, True, "mxy"),
]
VS_BASE = HANGUL_MIRROR_MODES[0][0]
VS_LAST = HANGUL_MIRROR_MODES[-1][0]
UVS_BASE = 0xFE00
UVS_LAST = UVS_BASE + len(HANGUL_MIRROR_MODES) - 1
MIRROR_SUFFIXES: Tuple[str, ...] = ("mx", "my", "mxy")

# Cluster VS (after Hangul) — not ``ccmp`` (that stage is too early).
CLUSTER_VS_FEATURE_TAGS: Tuple[str, ...] = ("rlig", "liga")
# Whole-glyph VS on the syllables font may use early ``ccmp`` safely.
SYLL_VS_FEATURE_TAGS: Tuple[str, ...] = ("ccmp", "rlig", "liga")

LOOKUP_FLAG_IGNORE_MARKS = 0x0008
GDEF_CLASS_MARK = 3

JAMO_RANGES: List[Tuple[int, int, str]] = [
    (0x1100, 0x11FF, "Hangul Jamo"),
    (0xA960, 0xA97F, "Hangul Jamo Extended-A"),
    (0xD7B0, 0xD7FF, "Hangul Jamo Extended-B"),
]
SYLL_RANGES: List[Tuple[int, int, str]] = [
    (0x3131, 0x318E, "Hangul Compatibility Jamo"),
    (0xAC00, 0xD7A3, "Hangul Syllables"),
]

JamoClass = str  # "L" | "V" | "T" | "other"


def vs_glyph_name(vs_cp: int) -> str:
    if VS_BASE <= vs_cp <= VS_LAST:
        return f"vs{vs_cp - VS_BASE + 1:02d}"
    if UVS_BASE <= vs_cp <= UVS_LAST:
        return f"vs{vs_cp - UVS_BASE + 1:02d}"
    raise ValueError(f"not a hangul VS codepoint: U+{vs_cp:04X}")


def is_vs_codepoint(cp: int) -> bool:
    return (VS_BASE <= cp <= VS_LAST) or (UVS_BASE <= cp <= UVS_LAST)


def font_cmap(tt: TTFont) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    return cmap


def unicodes_in_ranges(
    cmap: Dict[int, str], ranges: Sequence[Tuple[int, int, str]]
) -> Set[int]:
    out: Set[int] = set()
    for start, end, _name in ranges:
        for cp in range(start, end + 1):
            if cp in cmap:
                out.add(cp)
    return out


def resolve_malgun_path(in_dir: str) -> str:
    path = os.path.join(in_dir, MALGUN_FILENAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing Hangul source font: {path}")
    return path


def subset_malgun(src_path: str, unicodes: Set[int]) -> TTFont:
    """Subset Malgun to unicodes + GSUB closure (keeps ljmo/vjmo/tjmo)."""
    tt = TTFont(src_path, fontNumber=0)
    options = subset.Options()
    options.layout_scripts = ["hang", "DFLT"]
    options.layout_features = ["*"]
    options.glyph_names = True
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_outline = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(tt)
    return tt


def classify_jamo_forms(tt: TTFont) -> Dict[str, JamoClass]:
    """Map glyph name → L/V/T from Hangul single-subst lookups nested by chains."""
    classes: Dict[str, JamoClass] = {}
    if "GSUB" not in tt:
        return classes
    gsub = tt["GSUB"].table
    feature_lookups: Dict[str, Set[int]] = {}
    for fr in gsub.FeatureList.FeatureRecord:
        feature_lookups.setdefault(fr.FeatureTag, set()).update(
            fr.Feature.LookupListIndex
        )

    def nested_singles(chain_indices: Set[int]) -> Set[int]:
        out: Set[int] = set()
        for li in chain_indices:
            if li >= len(gsub.LookupList.Lookup):
                continue
            lu = gsub.LookupList.Lookup[li]
            if lu.LookupType != 6:
                if lu.LookupType == 1:
                    out.add(li)
                continue
            for st in lu.SubTable:
                for rec in getattr(st, "SubstLookupRecord", []) or []:
                    out.add(rec.LookupListIndex)
        return out

    tag_to_class = {"ljmo": "L", "vjmo": "V", "tjmo": "T"}
    for tag, cls in tag_to_class.items():
        singles = nested_singles(feature_lookups.get(tag, set()))
        for li in singles:
            lu = gsub.LookupList.Lookup[li]
            if lu.LookupType != 1:
                continue
            for st in lu.SubTable:
                mapping = getattr(st, "mapping", None) or {}
                for src, dst in mapping.items():
                    classes.setdefault(src, cls)
                    classes[dst] = cls
    return classes


def copy_scaled_glyph(
    glyph_set,
    src_name: str,
    *,
    upem_scale: float,
    local_scale: float,
) -> Optional[TTGlyph]:
    try:
        rec = DecomposingRecordingPen(glyph_set)
        glyph_set[src_name].draw(rec)
    except Exception as e:
        print(f"  [!] draw failed {src_name}: {e}", file=sys.stderr)
        return None
    bpen = BoundsPen(None)
    try:
        rec.replay(bpen)
    except Exception as e:
        print(f"  [!] bounds failed {src_name}: {e}", file=sys.stderr)
        return None
    if bpen.bounds is None:
        pen = TTGlyphPen(None)
        return pen.glyph()
    x0, y0, x1, y1 = bpen.bounds
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    s = upem_scale * local_scale
    dx = upem_scale * (1.0 - local_scale) * cx
    dy = upem_scale * (1.0 - local_scale) * cy
    pen = TTGlyphPen(None)
    try:
        rec.replay(TransformPen(pen, Transform(s, 0, 0, s, dx, dy)))
        out = pen.glyph()
    except Exception as e:
        print(f"  [!] replay failed {src_name}: {e}", file=sys.stderr)
        return None
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


def make_bbox_mirror(
    base_name: str,
    glyphs: Dict[str, TTGlyph],
    *,
    advance: int,
    lsb: int,
    negate_x: bool,
    negate_y: bool,
) -> Tuple[TTGlyph, int, int]:
    """Bake axis mirror about the base glyph's contour bbox center."""
    base = glyphs[base_name]
    rec = RecordingPen()
    try:
        if base.isComposite():
            for comp in base.components:
                name, (xx, xy, yx, yy, dx, dy) = comp.getComponentInfo()
                child = glyphs[name]
                child_rec = RecordingPen()
                child.draw(child_rec, None)
                child_rec.replay(
                    TransformPen(rec, Transform(xx, xy, yx, yy, dx, dy))
                )
        else:
            base.draw(rec, None)
    except Exception:
        try:
            base.draw(rec, None)
        except Exception:
            return empty_glyph(), advance, lsb

    bpen = BoundsPen(None)
    try:
        rec.replay(bpen)
    except Exception:
        bpen.bounds = None
    if bpen.bounds is None:
        return empty_glyph(), advance, lsb
    x0, y0, x1, y1 = bpen.bounds
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    sx = -1.0 if negate_x else 1.0
    sy = -1.0 if negate_y else 1.0
    t = Transform(sx, 0, 0, sy, cx * (1.0 - sx), cy * (1.0 - sy))
    pen = TTGlyphPen(None)
    dest = ReverseContourPen(pen) if (sx * sy) < 0 else pen
    rec.replay(TransformPen(dest, t))
    out = pen.glyph()
    try:
        out.recalcBounds(None)
        new_lsb = int(out.xMin)
    except Exception:
        new_lsb = lsb
    return out, advance, new_lsb


def add_mirror_variants(
    base_name: str,
    *,
    advance: int,
    lsb: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
) -> List[Tuple[int, str, str]]:
    installed: List[Tuple[int, str, str]] = []
    for vs_cp, neg_x, neg_y, suffix in HANGUL_MIRROR_MODES:
        if suffix is None:
            continue
        vname = variant_glyph_name(base_name, suffix)
        if vname not in glyphs:
            vg, vadv, vlsb = make_bbox_mirror(
                base_name,
                glyphs,
                advance=advance,
                lsb=lsb,
                negate_x=neg_x,
                negate_y=neg_y,
            )
            glyph_order.append(vname)
            glyphs[vname] = vg
            metrics[vname] = (vadv, vlsb)
        installed.append((vs_cp, suffix, vname))
    return installed


def _inject_vs(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
) -> None:
    for mode_i, (pua_cp, _nx, _ny, _suffix) in enumerate(HANGUL_MIRROR_MODES):
        vname = vs_glyph_name(pua_cp)
        if vname not in glyphs:
            glyph_order.append(vname)
            glyphs[vname] = empty_glyph()
            metrics[vname] = (0, 0)
        cmap[pua_cp] = vname
        cmap[UVS_BASE + mode_i] = vname


def build_syllable_uvs_entries(
    cmap: Dict[int, str],
    glyphs: Dict[str, TTGlyph],
) -> List[Tuple[int, int, Optional[str]]]:
    """Cmap-14 UVS for precomposed syllables and compatibility jamo only."""
    rows: List[Tuple[int, int, Optional[str]]] = []
    for cp, gname in cmap.items():
        if is_vs_codepoint(cp):
            continue
        if not (0xAC00 <= cp <= 0xD7A3 or 0x3131 <= cp <= 0x318E):
            continue
        for mode_i, (_pua, _nx, _ny, suffix) in enumerate(HANGUL_MIRROR_MODES):
            if suffix is None:
                continue
            vname = variant_glyph_name(gname, suffix)
            if vname in glyphs:
                rows.append((cp, UVS_BASE + mode_i, vname))
    return rows


def mark_vs_glyphs_in_gdef(font, vs_names: Sequence[str]) -> None:
    if "GDEF" not in font:
        gdef_table = newTable("GDEF")
        gdef = ot.GDEF()
        gdef.Version = 0x00010000
        gdef.GlyphClassDef = None
        gdef.AttachList = None
        gdef.LigCaretList = None
        gdef.MarkAttachClassDef = None
        gdef_table.table = gdef
        font["GDEF"] = gdef_table
    gdef = font["GDEF"].table
    if gdef.GlyphClassDef is None:
        gdef.GlyphClassDef = ot.GlyphClassDef()
        gdef.GlyphClassDef.classDefs = {}
    for name in vs_names:
        gdef.GlyphClassDef.classDefs[name] = GDEF_CLASS_MARK


def hangul_lookups_ignore_marks(font) -> int:
    if "GSUB" not in font:
        return 0
    gsub = font["GSUB"].table
    hangul_tags = {"ljmo", "vjmo", "tjmo"}
    lookup_indices: Set[int] = set()
    for fr in gsub.FeatureList.FeatureRecord:
        if fr.FeatureTag in hangul_tags:
            lookup_indices.update(fr.Feature.LookupListIndex)
    nested: Set[int] = set()
    for li in list(lookup_indices):
        if li >= len(gsub.LookupList.Lookup):
            continue
        lu = gsub.LookupList.Lookup[li]
        if lu.LookupType != 6:
            continue
        for st in lu.SubTable:
            for rec in getattr(st, "SubstLookupRecord", []) or []:
                nested.add(rec.LookupListIndex)
    lookup_indices |= nested
    for li in lookup_indices:
        lu = gsub.LookupList.Lookup[li]
        lu.LookupFlag = int(lu.LookupFlag) | LOOKUP_FLAG_IGNORE_MARKS
    return len(lookup_indices)


def install_hangul_rclt(font) -> None:
    """Expose ljmo/vjmo/tjmo under ``rclt`` as an always-on fallback."""
    if "GSUB" not in font:
        return
    gsub = font["GSUB"].table
    hangul_tags = ("ljmo", "vjmo", "tjmo")
    indices: List[int] = []
    seen: Set[int] = set()
    for tag in hangul_tags:
        for fr in gsub.FeatureList.FeatureRecord:
            if fr.FeatureTag != tag:
                continue
            for li in fr.Feature.LookupListIndex:
                if li not in seen:
                    indices.append(li)
                    seen.add(li)
    if not indices:
        return
    fr = ot.FeatureRecord()
    fr.FeatureTag = "rclt"
    fr.Feature = ot.Feature()
    fr.Feature.FeatureParams = None
    fr.Feature.LookupListIndex = list(indices)
    fr.Feature.LookupCount = len(indices)
    gsub.FeatureList.FeatureRecord.append(fr)
    fi = len(gsub.FeatureList.FeatureRecord) - 1
    gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)
    for rec in gsub.ScriptList.ScriptRecord:
        if rec.ScriptTag not in ("hang", "DFLT"):
            continue
        ls = rec.Script.DefaultLangSys
        if ls is None:
            continue
        if fi not in ls.FeatureIndex:
            ls.FeatureIndex.append(fi)
            ls.FeatureCount = len(ls.FeatureIndex)


def _ensure_gsub(font) -> ot.GSUB:
    if "GSUB" not in font:
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
    return font["GSUB"].table


def _attach_features(
    gsub: ot.GSUB,
    lookup_indices: Sequence[int],
    feature_tags: Sequence[str],
    scripts: Sequence[str] = ("DFLT", "hang", "latn"),
) -> None:
    def _ensure_script(tag: str) -> ot.Script:
        for rec in gsub.ScriptList.ScriptRecord:
            if rec.ScriptTag == tag:
                return rec.Script
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        rec.Script.DefaultLangSys = None
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gsub.ScriptList.ScriptRecord.append(rec)
        gsub.ScriptList.ScriptCount = len(gsub.ScriptList.ScriptRecord)
        return rec.Script

    def _ensure_langsys(script: ot.Script) -> ot.DefaultLangSys:
        if script.DefaultLangSys is None:
            ls = ot.DefaultLangSys()
            ls.ReqFeatureIndex = 0xFFFF
            ls.FeatureCount = 0
            ls.FeatureIndex = []
            script.DefaultLangSys = ls
        return script.DefaultLangSys

    feat_indices: List[int] = []
    for tag in feature_tags:
        fr = ot.FeatureRecord()
        fr.FeatureTag = tag
        fr.Feature = ot.Feature()
        fr.Feature.FeatureParams = None
        fr.Feature.LookupListIndex = list(lookup_indices)
        fr.Feature.LookupCount = len(lookup_indices)
        gsub.FeatureList.FeatureRecord.append(fr)
        feat_indices.append(len(gsub.FeatureList.FeatureRecord) - 1)
    gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)

    for tag in scripts:
        script = _ensure_script(tag)
        ls = _ensure_langsys(script)
        for fi in feat_indices:
            if fi not in ls.FeatureIndex:
                ls.FeatureIndex.append(fi)
        ls.FeatureCount = len(ls.FeatureIndex)


def install_vs_ligas(
    font,
    pairs: Sequence[Tuple[str, str, str]],
    *,
    feature_tags: Sequence[str] = SYLL_VS_FEATURE_TAGS,
) -> None:
    """``base + vs → variant`` ligas (whole-glyph / syllables font)."""
    if not pairs:
        return
    liga_map: Dict[Tuple[str, ...], str] = {
        (base, vs): var for base, vs, var in pairs
    }
    items = list(liga_map.items())
    chunk_size = 4000
    liga_lookups = []
    for i in range(0, len(items), chunk_size):
        chunk = dict(items[i : i + chunk_size])
        sub = buildLigatureSubstSubtable(chunk)
        lu = buildLookup([sub])
        lu.LookupType = 4
        liga_lookups.append(lu)

    gsub = _ensure_gsub(font)
    base_index = len(gsub.LookupList.Lookup)
    gsub.LookupList.Lookup.extend(liga_lookups)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    new_indices = list(range(base_index, base_index + len(liga_lookups)))
    _attach_features(gsub, new_indices, feature_tags)


def _build_reverse_chain(
    mapping: Dict[str, str],
    lookahead_groups: Sequence[Sequence[str]],
    glyph_map: Dict[str, int],
) -> ot.ReverseChainSingleSubst:
    glyphs = sorted(mapping.keys(), key=lambda g: glyph_map[g])
    st = ot.ReverseChainSingleSubst()
    st.Format = 1
    st.Coverage = buildCoverage(glyphs, glyph_map)
    st.BacktrackCoverage = []
    st.BacktrackGlyphCount = 0
    st.LookAheadCoverage = [
        buildCoverage(sorted(set(group), key=lambda g: glyph_map[g]), glyph_map)
        for group in lookahead_groups
    ]
    st.LookAheadGlyphCount = len(st.LookAheadCoverage)
    st.Substitute = [mapping[g] for g in glyphs]
    st.GlyphCount = len(st.Substitute)
    return st


def install_cluster_vs(
    font,
    *,
    l_forms: Sequence[str],
    v_forms: Sequence[str],
    t_forms: Sequence[str],
    glyphs: Dict[str, TTGlyph],
) -> int:
    """VS after jongseong flips choseong + jongseong; LV+VS flips choseong.

    Reverse-chain singles run in ``rlig``/``liga`` *after* Hangul jamo features:
    ``L V T vs → L.sfx`` then ``T vs → T.sfx`` (same ``vs`` / suffix).
    """
    glyph_map = {n: i for i, n in enumerate(font.getGlyphOrder())}
    vs_lookups = []

    for vs_cp, _nx, _ny, suffix in HANGUL_MIRROR_MODES:
        if suffix is None:
            continue
        vs_name = vs_glyph_name(vs_cp)
        l_map = {
            g: variant_glyph_name(g, suffix)
            for g in l_forms
            if variant_glyph_name(g, suffix) in glyphs
        }
        t_map = {
            g: variant_glyph_name(g, suffix)
            for g in t_forms
            if variant_glyph_name(g, suffix) in glyphs
        }
        if not l_map and not t_map:
            continue

        # L' + V + T + vs → L.sfx  (closed syllable)
        if l_map and v_forms and t_forms:
            st = _build_reverse_chain(
                l_map, [v_forms, t_forms, [vs_name]], glyph_map
            )
            lu = buildLookup([st])
            lu.LookupType = 8
            vs_lookups.append(lu)

        # L' + V + vs → L.sfx  (open syllable)
        if l_map and v_forms:
            st = _build_reverse_chain(l_map, [v_forms, [vs_name]], glyph_map)
            lu = buildLookup([st])
            lu.LookupType = 8
            vs_lookups.append(lu)

        # T' + vs → T.sfx
        if t_map:
            st = _build_reverse_chain(t_map, [[vs_name]], glyph_map)
            lu = buildLookup([st])
            lu.LookupType = 8
            vs_lookups.append(lu)

        # Consume trailing VS (PUA is not default-ignorable).
        consume: Dict[Tuple[str, ...], str] = {}
        for base, var in t_map.items():
            consume[(var, vs_name)] = var
        for v in v_forms:
            consume[(v, vs_name)] = v
        if consume:
            sub = buildLigatureSubstSubtable(consume)
            lu = buildLookup([sub])
            lu.LookupType = 4
            vs_lookups.append(lu)

    if not vs_lookups:
        return 0

    gsub = _ensure_gsub(font)
    base_index = len(gsub.LookupList.Lookup)
    gsub.LookupList.Lookup.extend(vs_lookups)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    new_indices = list(range(base_index, base_index + len(vs_lookups)))
    _attach_features(gsub, new_indices, CLUSTER_VS_FEATURE_TAGS)
    return len(vs_lookups)


def _scale_glyphs_from_subset(
    tt: TTFont,
    *,
    target_upem: int,
    src_upem: int,
    local_scale: float,
) -> Tuple[
    List[str],
    Dict[str, TTGlyph],
    Dict[str, Tuple[int, int]],
    Dict[int, str],
]:
    upem_scale = target_upem / float(src_upem)
    glyph_set = tt.getGlyphSet()
    hmtx = tt["hmtx"].metrics
    old_order = [n for n in tt.getGlyphOrder() if n != ".notdef"]

    glyph_order = [".notdef"]
    glyphs: Dict[str, TTGlyph] = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}

    for name in old_order:
        g = copy_scaled_glyph(
            glyph_set, name, upem_scale=upem_scale, local_scale=local_scale
        )
        if g is None:
            continue
        adv_src, _lsb_src = hmtx.get(name, (src_upem, 0))
        advance = otRound(adv_src * upem_scale)
        try:
            g.recalcBounds(None)
            lsb = int(g.xMin)
        except Exception:
            lsb = 0
        glyph_order.append(name)
        glyphs[name] = g
        metrics[name] = (advance, lsb)

    cmap: Dict[int, str] = {}
    for cp, gname in font_cmap(tt).items():
        if gname in glyphs:
            cmap[cp] = gname
    return glyph_order, glyphs, metrics, cmap


def _save_font(
    fb: FontBuilder,
    out_dir: str,
    family: str,
    *,
    write_ttf: bool,
    write_woff2: bool,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{family}.ttf")
    fb.save(out_path)
    if write_woff2:
        print(f"  Compressing {family}.woff2...", flush=True)
        woff2.compress(out_path, out_path.replace(".ttf", ".woff2"))
    if not write_ttf:
        try:
            os.remove(out_path)
        except OSError:
            pass
    return out_path


def build_jamo_font(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    local_scale: float = LOCAL_SCALE,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    src_path = resolve_malgun_path(in_dir)
    print(f"\n=== {FAMILY_JAMO} (conjoining jamo) ===", flush=True)
    print(f"Source: {src_path}", flush=True)
    src_tt = TTFont(src_path, fontNumber=0)
    try:
        src_cmap = font_cmap(src_tt)
        unicodes = unicodes_in_ranges(src_cmap, JAMO_RANGES)
        if limit is not None:
            unicodes = set(sorted(unicodes)[:limit])
            print(f"  Limiting to {len(unicodes)} codepoints (--limit)", flush=True)
        else:
            print(f"  Jamo codepoints in cmap: {len(unicodes)}", flush=True)
        src_upem = int(src_tt["head"].unitsPerEm)
    finally:
        src_tt.close()

    print("  Subsetting Malgun (jamo + GSUB closure)...", flush=True)
    tt = subset_malgun(src_path, unicodes)
    jamo_class = classify_jamo_forms(tt)
    n_l = sum(1 for c in jamo_class.values() if c == "L")
    n_v = sum(1 for c in jamo_class.values() if c == "V")
    n_t = sum(1 for c in jamo_class.values() if c == "T")
    print(f"  Jamo forms classified: L={n_l} V={n_v} T={n_t}", flush=True)

    print(
        f"  Scaling glyphs (upem {src_upem}→{target_upem}, local {local_scale:g})...",
        flush=True,
    )
    glyph_order, glyphs, metrics, cmap = _scale_glyphs_from_subset(
        tt, target_upem=target_upem, src_upem=src_upem, local_scale=local_scale
    )
    _inject_vs(glyph_order, glyphs, metrics, cmap)
    vs_names = [vs_glyph_name(m[0]) for m in HANGUL_MIRROR_MODES]

    l_forms = sorted(
        n for n, c in jamo_class.items() if c == "L" and n in glyphs
    )
    v_forms = sorted(
        n for n, c in jamo_class.items() if c == "V" and n in glyphs
    )
    t_forms = sorted(
        n for n, c in jamo_class.items() if c == "T" and n in glyphs
    )
    # Include cmap'd L/V/T that classification might miss as identity forms.
    for cp, gname in list(cmap.items()):
        if is_vs_codepoint(cp) or gname not in glyphs:
            continue
        kind = None
        if 0x1100 <= cp <= 0x115F or 0xA960 <= cp <= 0xA97F:
            kind = "L"
        elif 0x1160 <= cp <= 0x11A7 or 0xD7B0 <= cp <= 0xD7C6:
            kind = "V"
        elif 0x11A8 <= cp <= 0x11FF or 0xD7CB <= cp <= 0xD7FB:
            kind = "T"
        if kind == "L" and gname not in l_forms:
            l_forms.append(gname)
        elif kind == "V" and gname not in v_forms:
            v_forms.append(gname)
        elif kind == "T" and gname not in t_forms:
            t_forms.append(gname)
    l_forms = sorted(set(l_forms))
    v_forms = sorted(set(v_forms))
    t_forms = sorted(set(t_forms))

    print("  Installing L/T axis-mirror variants...", flush=True)
    for base in l_forms + t_forms:
        adv, lsb = metrics[base]
        add_mirror_variants(
            base,
            advance=adv,
            lsb=lsb,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )

    hangul_cps = [cp for cp in cmap if not is_vs_codepoint(cp)]
    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)

    print(
        f"  Assembling font ({len(glyphs) - 1} glyphs, {len(hangul_cps)} CPs)...",
        flush=True,
    )
    fb = FontBuilder(target_upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    fb.setupCharacterMap(cmap)
    fb.setupNameTable(
        {
            "familyName": FAMILY_JAMO,
            "styleName": "Regular",
            "uniqueFontIdentifier": FAMILY_JAMO,
            "fullName": FAMILY_JAMO,
            "psName": FAMILY_JAMO,
            "version": "Version 1.000",
        }
    )
    fb.setupOS2(
        sTypoAscender=ascent,
        sTypoDescender=descent,
        sTypoLineGap=0,
        usWinAscent=ascent,
        usWinDescent=abs(descent),
        achVendID="pHg ",
    )
    fb.setupPost()

    if "GSUB" in tt:
        fb.font["GSUB"] = copy.deepcopy(tt["GSUB"])
        n_flagged = hangul_lookups_ignore_marks(fb.font)
        install_hangul_rclt(fb.font)
        print(
            f"  Ported ljmo/vjmo/tjmo; IgnoreMarks on {n_flagged} lookups; rclt.",
            flush=True,
        )
    if "GDEF" in tt:
        try:
            fb.font["GDEF"] = copy.deepcopy(tt["GDEF"])
        except Exception:
            pass
    mark_vs_glyphs_in_gdef(fb.font, vs_names)

    n_vs = install_cluster_vs(
        fb.font,
        l_forms=l_forms,
        v_forms=v_forms,
        t_forms=t_forms,
        glyphs=glyphs,
    )
    print(f"  Cluster VS lookups: {n_vs} (VS after jongseong -> L+T)", flush=True)

    out_path = _save_font(
        fb, out_dir, FAMILY_JAMO, write_ttf=write_ttf, write_woff2=write_woff2
    )
    tt.close()
    return out_path, len(glyphs) - 1, sorted(cmap.keys())


def build_syllables_font(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    local_scale: float = LOCAL_SCALE,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    src_path = resolve_malgun_path(in_dir)
    print(f"\n=== {FAMILY_SYLL} (syllables + compat jamo) ===", flush=True)
    print(f"Source: {src_path}", flush=True)
    src_tt = TTFont(src_path, fontNumber=0)
    try:
        src_cmap = font_cmap(src_tt)
        unicodes = unicodes_in_ranges(src_cmap, SYLL_RANGES)
        if limit is not None:
            unicodes = set(sorted(unicodes)[:limit])
            print(f"  Limiting to {len(unicodes)} codepoints (--limit)", flush=True)
        else:
            print(f"  Syllable/compat CPs in cmap: {len(unicodes)}", flush=True)
        src_upem = int(src_tt["head"].unitsPerEm)
    finally:
        src_tt.close()

    print("  Subsetting Malgun (syllables + compat)...", flush=True)
    tt = subset_malgun(src_path, unicodes)

    print(
        f"  Scaling glyphs (upem {src_upem}→{target_upem}, local {local_scale:g})...",
        flush=True,
    )
    glyph_order, glyphs, metrics, cmap = _scale_glyphs_from_subset(
        tt, target_upem=target_upem, src_upem=src_upem, local_scale=local_scale
    )
    # Drop any leftover jamo GSUB from subset — syllables font is whole-glyph only.
    if "GSUB" in tt:
        del tt["GSUB"]
    if "GDEF" in tt:
        del tt["GDEF"]

    _inject_vs(glyph_order, glyphs, metrics, cmap)
    vs_names = [vs_glyph_name(m[0]) for m in HANGUL_MIRROR_MODES]

    print("  Installing whole-glyph axis-mirror variants...", flush=True)
    liga_pairs: List[Tuple[str, str, str]] = []
    hangul_cps = [cp for cp in cmap if not is_vs_codepoint(cp)]
    for cp in hangul_cps:
        base = cmap[cp]
        if base not in glyphs:
            continue
        adv, lsb = metrics[base]
        installed = add_mirror_variants(
            base,
            advance=adv,
            lsb=lsb,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
        for vs_cp, _suffix, vname in installed:
            liga_pairs.append((base, vs_glyph_name(vs_cp), vname))

    uvs_rows = build_syllable_uvs_entries(cmap, glyphs)
    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)

    print(
        f"  Assembling font ({len(glyphs) - 1} glyphs, {len(hangul_cps)} CPs)...",
        flush=True,
    )
    fb = FontBuilder(target_upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    fb.setupCharacterMap(cmap, uvs=uvs_rows)
    fb.setupNameTable(
        {
            "familyName": FAMILY_SYLL,
            "styleName": "Regular",
            "uniqueFontIdentifier": FAMILY_SYLL,
            "fullName": FAMILY_SYLL,
            "psName": FAMILY_SYLL,
            "version": "Version 1.000",
        }
    )
    fb.setupOS2(
        sTypoAscender=ascent,
        sTypoDescender=descent,
        sTypoLineGap=0,
        usWinAscent=ascent,
        usWinDescent=abs(descent),
        achVendID="pHg ",
    )
    fb.setupPost()

    mark_vs_glyphs_in_gdef(fb.font, vs_names)
    print(f"  Compiling VS ligas ({len(liga_pairs)} rules)...", flush=True)
    install_vs_ligas(fb.font, liga_pairs, feature_tags=SYLL_VS_FEATURE_TAGS)

    out_path = _save_font(
        fb, out_dir, FAMILY_SYLL, write_ttf=write_ttf, write_woff2=write_woff2
    )
    tt.close()
    return out_path, len(glyphs) - 1, sorted(cmap.keys())


def unicode_range_css(codepoints: Sequence[int]) -> str:
    cps = sorted(set(codepoints))
    if not cps:
        return ""
    runs: List[str] = []
    run_start = prev = cps[0]
    for cp in cps[1:]:
        if cp == prev + 1:
            prev = cp
            continue
        if run_start == prev:
            runs.append(f"U+{run_start:X}")
        else:
            runs.append(f"U+{run_start:X}-{prev:X}")
        run_start = prev = cp
    if run_start == prev:
        runs.append(f"U+{run_start:X}")
    else:
        runs.append(f"U+{run_start:X}-{prev:X}")
    return ", ".join(runs)


def write_css(
    out_dir: str,
    jamo_cps: Sequence[int],
    syll_cps: Sequence[int],
) -> None:
    css_path = os.path.join(out_dir, "panhangul.css")
    lines = [
        "/* Auto-generated Hangul fonts from Malgun Gothic */",
        "/* panhangul = conjoining jamo; panhanguls = syllables + compat */",
        "",
    ]
    for family, cps in ((FAMILY_JAMO, jamo_cps), (FAMILY_SYLL, syll_cps)):
        urange = unicode_range_css(cps)
        url = f"{CSS_FONT_URL_BASE}/{family}.woff2"
        lines += [
            "@font-face {",
            f"  font-family: '{family}';",
            f"  src: url('{url}') format('woff2');",
            "  font-weight: normal;",
            "  font-style: normal;",
            "  font-display: swap;",
        ]
        if urange:
            lines.append(f"  unicode-range: {urange};")
        lines += ["}", ""]

    with open(css_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {css_path}")

    fontlist_path = os.path.join(out_dir, "panhangul-fontlist.css")
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(
            "/* Hangul font families */\n"
            ":root {\n"
            f"  --font-panhangul: '{FAMILY_JAMO}', '{FAMILY_SYLL}';\n"
            f"  --font-panhangul-jamo: '{FAMILY_JAMO}';\n"
            f"  --font-panhanguls: '{FAMILY_SYLL}';\n"
            "}\n"
        )
    print(f"Wrote {fontlist_path}")


def build_all(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    local_scale: float = LOCAL_SCALE,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> None:
    print(f"Hangul source: {MALGUN_FILENAME}")
    print(
        f"  VS U+{VS_BASE:04X}-U+{VS_LAST:04X} / U+{UVS_BASE:04X}-U+{UVS_LAST:04X}: "
        "identity / mx / my / mxy"
    )
    print(
        f"  Jamo ({FAMILY_JAMO}): VS after jongseong flips choseong+jongseong"
    )
    print(f"  Syllables ({FAMILY_SYLL}): whole-glyph VS / UVS")
    print(f"  Local scale: {local_scale:g} about bbox center")
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"  Formats: {fmt_note}")

    jamo_path, jamo_count, jamo_cps = build_jamo_font(
        in_dir,
        out_dir,
        target_upem,
        limit=limit,
        local_scale=local_scale,
        write_ttf=write_ttf,
        write_woff2=write_woff2,
    )
    syll_path, syll_count, syll_cps = build_syllables_font(
        in_dir,
        out_dir,
        target_upem,
        limit=limit,
        local_scale=local_scale,
        write_ttf=write_ttf,
        write_woff2=write_woff2,
    )
    if jamo_count or syll_count:
        write_css(out_dir, jamo_cps, syll_cps)
    print(
        f"\nDone: {jamo_path} ({jamo_count} glyphs); "
        f"{syll_path} ({syll_count} glyphs)",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build panhangul (jamo) + panhanguls (syllables) from Malgun"
    )
    p.add_argument("--in", dest="in_dir", default=IN_DIR)
    p.add_argument("--out", dest="out_dir", default=OUT_DIR)
    p.add_argument("--upem", type=int, default=DEFAULT_UPEM)
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Use only the first N codepoints per font (smoke test)",
    )
    p.add_argument(
        "--local-scale",
        type=float,
        default=LOCAL_SCALE,
        help=f"BBox-center scale (default {LOCAL_SCALE})",
    )
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument(
        "--ttf-only",
        "--no-woff2",
        action="store_true",
        help="Write TTF only (skip WOFF2)",
    )
    fmt.add_argument(
        "--woff2-only",
        action="store_true",
        help="Write WOFF2 only (drop intermediate TTF after compress)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_all(
        args.in_dir,
        args.out_dir,
        args.upem,
        limit=args.limit,
        local_scale=args.local_scale,
        write_ttf=not args.woff2_only,
        write_woff2=not args.ttf_only,
    )
