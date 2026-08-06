#!/usr/bin/env python3
"""
Build one Hangul font (``panhangul``) from Malgun Gothic.

Preserves Malgun ``ljmo`` / ``vjmo`` / ``tjmo`` contextual jamo shaping on
script ``hang``, and adds axis-mirror variants via VS1..VS4 (U+E000..E003):

======= ========== ================================
Name    Code point Transform
======= ========== ================================
VS1     U+E000     identity (no subst)
VS2     U+E001     mx — negate X about contour bbox center
VS3     U+E002     my — negate Y about contour bbox center
VS4     U+E003     mxy — both axes
======= ========== ================================

* Whole cmap glyphs (syllables, compat jamo, conjoining jamo): ``char + VS2..4``
  → flipped outline (``ccmp`` / ``rlig`` / ``liga``).
* Choseong / jongseong forms (including ``ljmo`` / ``tjmo`` alternates): same
  VS ligas; jungseong / ``vjmo`` targets get no VS variants.
* Contours flip about each glyph's own bbox center — advance / slot unchanged.

HarfBuzz may decompose precomposed syllables before liga; whole-syllable VS
ligas still apply when the precomposed form remains. NFD jamo + VS on L/T is
the reliable component path.
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
from fontTools.otlLib.builder import buildLigatureSubstSubtable, buildLookup
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable, woff2
from fontTools.ttLib.tables import otTables as ot
from fontTools.ttLib.tables._g_l_y_f import (
    ROUND_XY_TO_GRID,
    UNSCALED_COMPONENT_OFFSET,
    USE_MY_METRICS,
    Glyph as TTGlyph,
    GlyphComponent,
)

from yi_halfwidth import (
    COMPOSITION_FEATURE_TAGS,
    DEFAULT_UPEM,
    empty_glyph,
    variant_glyph_name,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "hangul")

MALGUN_FILENAME = "malgun.ttf"
FAMILY_NAME = "panhangul"
PS_NAME = "panhangul"
LOCAL_SCALE = 0.95

CSS_FONT_URL_BASE = (
    "https://raw.githubusercontent.com/nexovolta/fonts/main/Scripts/dist/hangul"
)

# VS1..VS4 — axis mirrors only (U+E000..E003).
# (vs_cp, negate_x, negate_y, suffix or None)
HANGUL_MIRROR_MODES: List[Tuple[int, bool, bool, Optional[str]]] = [
    (0xE000, False, False, None),
    (0xE001, True, False, "mx"),
    (0xE002, False, True, "my"),
    (0xE003, True, True, "mxy"),
]
VS_BASE = HANGUL_MIRROR_MODES[0][0]
VS_LAST = HANGUL_MIRROR_MODES[-1][0]

HANGUL_RANGES: List[Tuple[int, int, str]] = [
    (0x1100, 0x11FF, "Hangul Jamo"),
    (0x3131, 0x318E, "Hangul Compatibility Jamo"),
    (0xA960, 0xA97F, "Hangul Jamo Extended-A"),
    (0xAC00, 0xD7A3, "Hangul Syllables"),
    (0xD7B0, 0xD7FF, "Hangul Jamo Extended-B"),
]

JamoClass = str  # "L" | "V" | "T" | "other"


def vs_glyph_name(vs_cp: int) -> str:
    return f"vs{vs_cp - VS_BASE + 1:02d}"


def font_cmap(tt: TTFont) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    return cmap


def hangul_unicodes(cmap: Dict[int, str]) -> Set[int]:
    out: Set[int] = set()
    for start, end, _name in HANGUL_RANGES:
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
    """Subset Malgun to Hangul unicodes + GSUB closure (keeps ljmo/vjmo/tjmo)."""
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
    # Malgun (and typical): ljmo chains → choseong singles; vjmo → vowel; tjmo → final.
    # Classify by which feature ultimately references the nested single-subst.
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
        # empty / space glyph
        pen = TTGlyphPen(None)
        return pen.glyph()
    x0, y0, x1, y1 = bpen.bounds
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    s = upem_scale * local_scale
    # p' = s*(p - c) + c*upem_scale  — keep bbox center at scaled em position
    # Center in source space maps to upem_scale * c in target (without local),
    # then local scale about that point:
    # p1 = upem_scale * p
    # p2 = local*(p1 - upem_scale*c) + upem_scale*c = s*p + upem_scale*(1-local)*c
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


def make_bbox_mirror_composite(
    base_name: str,
    glyphs: Dict[str, TTGlyph],
    *,
    advance: int,
    lsb: int,
    negate_x: bool,
    negate_y: bool,
) -> Tuple[TTGlyph, int, int]:
    """One-component TT composite: axis mirror about base contour bbox center."""
    base = glyphs[base_name]
    try:
        base.recalcBounds(None)
        cx = (base.xMin + base.xMax) / 2.0
        cy = (base.yMin + base.yMax) / 2.0
    except Exception:
        cx = cy = 0.0
    sx = -1.0 if negate_x else 1.0
    sy = -1.0 if negate_y else 1.0
    dx = cx * (1.0 - sx)
    dy = cy * (1.0 - sy)
    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = base_name
    comp.x = otRound(dx)
    comp.y = otRound(dy)
    comp.flags = USE_MY_METRICS | ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    if (sx, sy) != (1.0, 1.0):
        # fontTools: ((xx, xy), (yx, yy)); x' = xx·x + yx·y + dx
        comp.transform = ((sx, 0.0), (0.0, sy))
    g.components = [comp]
    return g, advance, lsb


def add_mirror_variants(
    base_name: str,
    *,
    advance: int,
    lsb: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
) -> List[Tuple[int, str, str]]:
    """Install .mx / .my / .mxy composites. Returns [(vs_cp, suffix, name), ...]."""
    installed: List[Tuple[int, str, str]] = []
    for vs_cp, neg_x, neg_y, suffix in HANGUL_MIRROR_MODES:
        if suffix is None:
            continue
        vname = variant_glyph_name(base_name, suffix)
        if vname not in glyphs:
            vg, vadv, vlsb = make_bbox_mirror_composite(
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
    for vs_cp, _nx, _ny, _suffix in HANGUL_MIRROR_MODES:
        vname = vs_glyph_name(vs_cp)
        if vname not in glyphs:
            glyph_order.append(vname)
            glyphs[vname] = empty_glyph()
            metrics[vname] = (0, 0)
        cmap[vs_cp] = vname


def install_vs_ligas(
    font,
    pairs: Sequence[Tuple[str, str, str]],
) -> None:
    """Append ``base + vs → variant`` ligas under ccmp/rlig/liga on hang+DFLT+latn.

    ``pairs`` is ``(base_glyph, vs_glyph, variant_glyph)``.
    """
    if not pairs:
        return
    liga_map: Dict[Tuple[str, ...], str] = {
        (base, vs): var for base, vs, var in pairs
    }
    # Split to stay under OT 64KB subtable limit (~11k syllables × 3 ≈ ok in a few).
    items = list(liga_map.items())
    chunk_size = 4000
    liga_lookups = []
    for i in range(0, len(items), chunk_size):
        chunk = dict(items[i : i + chunk_size])
        sub = buildLigatureSubstSubtable(chunk)
        lu = buildLookup([sub])
        lu.LookupType = 4
        liga_lookups.append(lu)

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

    gsub = font["GSUB"].table
    base_index = len(gsub.LookupList.Lookup)
    gsub.LookupList.Lookup.extend(liga_lookups)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    new_indices = list(range(base_index, base_index + len(liga_lookups)))

    # Ensure scripts hang / DFLT / latn exist with default langsys.
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

    # Add ccmp/rlig/liga feature records pointing at new lookups; share one feature
    # index set appended to each langsys.
    feat_indices: List[int] = []
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = ot.FeatureRecord()
        fr.FeatureTag = tag
        fr.Feature = ot.Feature()
        fr.Feature.FeatureParams = None
        fr.Feature.LookupListIndex = list(new_indices)
        fr.Feature.LookupCount = len(new_indices)
        gsub.FeatureList.FeatureRecord.append(fr)
        feat_indices.append(len(gsub.FeatureList.FeatureRecord) - 1)
    gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)

    for tag in ("DFLT", "hang", "latn"):
        script = _ensure_script(tag)
        ls = _ensure_langsys(script)
        for fi in feat_indices:
            if fi not in ls.FeatureIndex:
                ls.FeatureIndex.append(fi)
        ls.FeatureCount = len(ls.FeatureIndex)


def build_panhangul_font(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    local_scale: float = LOCAL_SCALE,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")

    src_path = resolve_malgun_path(in_dir)
    print(f"Source: {src_path}", flush=True)
    src_tt = TTFont(src_path, fontNumber=0)
    try:
        src_cmap = font_cmap(src_tt)
        unicodes = hangul_unicodes(src_cmap)
        if limit is not None:
            unicodes = set(sorted(unicodes)[:limit])
            print(f"  Limiting to {len(unicodes)} codepoints (--limit)", flush=True)
        else:
            print(f"  Hangul codepoints in cmap: {len(unicodes)}", flush=True)
        src_upem = int(src_tt["head"].unitsPerEm)
    finally:
        src_tt.close()

    print("  Subsetting Malgun (Hangul + jamo GSUB closure)...", flush=True)
    tt = subset_malgun(src_path, unicodes)
    jamo_class = classify_jamo_forms(tt)
    n_l = sum(1 for c in jamo_class.values() if c == "L")
    n_v = sum(1 for c in jamo_class.values() if c == "V")
    n_t = sum(1 for c in jamo_class.values() if c == "T")
    print(f"  Jamo forms classified: L={n_l} V={n_v} T={n_t}", flush=True)

    upem_scale = target_upem / float(src_upem)
    glyph_set = tt.getGlyphSet()
    hmtx = tt["hmtx"].metrics
    old_order = [n for n in tt.getGlyphOrder() if n != ".notdef"]

    print(
        f"  Scaling {len(old_order)} glyphs "
        f"(upem {src_upem}→{target_upem}, local {local_scale:g})...",
        flush=True,
    )
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

    # Cmap: keep Hangul mappings from subset font (same glyph names).
    cmap: Dict[int, str] = {}
    sub_cmap = font_cmap(tt)
    for cp, gname in sub_cmap.items():
        if gname in glyphs:
            cmap[cp] = gname

    _inject_vs(glyph_order, glyphs, metrics, cmap)

    print("  Installing axis-mirror variants (whole + L/T)...", flush=True)
    liga_pairs: List[Tuple[str, str, str]] = []
    mirror_bases: Set[str] = set(cmap[cp] for cp in cmap if cp < VS_BASE or cp > VS_LAST)
    for name, cls in jamo_class.items():
        if cls in ("L", "T") and name in glyphs:
            mirror_bases.add(name)

    for base in sorted(mirror_bases):
        if base not in glyphs:
            continue
        # Jungseong bases that only appear as V: skip if classified V and not cmap'd
        # for whole-glyph — cmap'd V jamo still get whole-glyph mirrors.
        cls = jamo_class.get(base)
        is_cmap_base = base in set(cmap.values())
        if cls == "V" and not is_cmap_base:
            continue
        # Component VS only for L/T; whole cmap always.
        want_component = cls in ("L", "T")
        if not is_cmap_base and not want_component:
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
            if is_cmap_base or want_component:
                liga_pairs.append((base, vs_glyph_name(vs_cp), vname))

    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)

    print(
        f"  Assembling font ({len(glyphs) - 1} glyphs, {len(cmap) - 4} Hangul CPs)...",
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
            "familyName": FAMILY_NAME,
            "styleName": "Regular",
            "uniqueFontIdentifier": PS_NAME,
            "fullName": FAMILY_NAME,
            "psName": PS_NAME,
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

    # Port Hangul GSUB from subset font (glyph names unchanged).
    if "GSUB" in tt:
        fb.font["GSUB"] = copy.deepcopy(tt["GSUB"])
        print("  Ported ljmo/vjmo/tjmo GSUB from Malgun.", flush=True)
    if "GDEF" in tt:
        # GDEF may reference dropped glyphs; copy cautiously.
        try:
            fb.font["GDEF"] = copy.deepcopy(tt["GDEF"])
        except Exception:
            pass

    print(f"  Compiling VS ligas ({len(liga_pairs)} rules)...", flush=True)
    install_vs_ligas(fb.font, liga_pairs)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{FAMILY_NAME}.ttf")
    fb.save(out_path)
    if write_woff2:
        print("  Compressing WOFF2...", flush=True)
        woff2.compress(out_path, out_path.replace(".ttf", ".woff2"))
    if not write_ttf:
        try:
            os.remove(out_path)
        except OSError:
            pass

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


def write_css(out_dir: str, codepoints: Sequence[int]) -> None:
    css_path = os.path.join(out_dir, "panhangul.css")
    urange = unicode_range_css(codepoints)
    url = f"{CSS_FONT_URL_BASE}/{FAMILY_NAME}.woff2"
    lines = [
        "/* Auto-generated Hangul font from Malgun Gothic */",
        "",
        "@font-face {",
        f"  font-family: '{FAMILY_NAME}';",
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
            "/* Hangul font family */\n"
            f":root {{\n  --font-panhangul: '{FAMILY_NAME}';\n}}\n"
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
        f"  VS U+{VS_BASE:04X}-U+{VS_LAST:04X}: identity / mx / my / mxy "
        "(choseong+jongseong+whole; no jungseong component VS)"
    )
    print(f"  Local scale: {local_scale:g} about bbox center")
    print(f"  Output: single font '{FAMILY_NAME}'")
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"  Formats: {fmt_note}")

    path, count, cps = build_panhangul_font(
        in_dir,
        out_dir,
        target_upem,
        limit=limit,
        local_scale=local_scale,
        write_ttf=write_ttf,
        write_woff2=write_woff2,
    )
    if count:
        write_css(out_dir, cps)
    print(f"\nDone: {path} ({count} glyphs)", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build the single panhangul font from Malgun Gothic"
    )
    p.add_argument("--in", dest="in_dir", default=IN_DIR)
    p.add_argument("--out", dest="out_dir", default=OUT_DIR)
    p.add_argument("--upem", type=int, default=DEFAULT_UPEM)
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Use only the first N Hangul codepoints (smoke test)",
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
