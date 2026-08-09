#!/usr/bin/env python3
"""
Build one Yi font (``panyi``) covering the whole inventory.

Contents
--------
* Standalone forms at real Unicode CPs (full CJK width) plus D4 orientations:

      yi + VS02..VS08 / FE01..FE07   →   oriented variant
      (VS01 / FE00 = identity, no subst)

* Slice overlays via FE08–FE09 (half-plane clips + shared ``sliceAdv`` advance):

      A B FE08  →  A.top  + B.bot   sliceAdv   # horizontal
      A B FE09  →  A.left + B.right sliceAdv   # vertical

* Dakuten marks (shared stack ``\\p{M}`` minus letter / overlay / oversized):
  GPOS ``mark`` at fixed CJK corners on VS01..VS07 forms and ``sliceAdv``.
  Successive marks fill TR → BR → TL → BL. No left-squish ``.dk`` forms.
"""

from __future__ import annotations

import argparse
import os
from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.ttLib import TTFont, woff2

from shared_diacritics import (
    add_dakuten_mark_glyphs,
    collect_dakuten_base_anchors,
    dakuten_mark_stack_label,
    install_dakuten_gpos,
    install_dakuten_slot_gsub,
    load_dakuten_marks_from_stack,
    resolve_dakuten_mark_font_stack,
    yi_forms_for_dakuten,
)
from yi_halfwidth import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    DEFAULT_UPEM,
    NUOSU_FILENAME,
    YI_ORIENTATION_MODES,
    YiInventory,
    add_d4_variant_glyphs,
    build_d4_uvs_entries,
    empty_glyph,
    load_inventory,
    make_standalone_glyph,
    REFERENCE_HORIZONTAL_STEM,
    REFERENCE_VERTICAL_STEM,
    orientation_form_names,
    record_glyph,
    resolve_nuosu_path,
    variant_glyph_name,
    vs_glyph_name,
)
from yi_slice import (
    SLICE_ADV_NAME,
    SLICE_H_CP,
    SLICE_V_CP,
    add_slice_halves,
    inject_slice_marks,
    install_slice_gsub,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "yi")

FAMILY_NAME = "panyi"
PS_NAME = "panyi"

CSS_FONT_URL_BASE = (
    "https://raw.githubusercontent.com/nexovolta/fonts/main/Scripts/dist/yi"
)


def glyph_name_for_cp(cp: int) -> str:
    return f"u{cp:04X}" if cp <= 0xFFFF else f"u{cp:05X}"


def _inject_vs(
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict,
    cmap: Dict[int, str],
) -> None:
    for vs_cp, _rot, _fx, _fy, _suffix in YI_ORIENTATION_MODES:
        vname = vs_glyph_name(vs_cp)
        if vname not in glyphs:
            glyph_order.append(vname)
            glyphs[vname] = empty_glyph()
            metrics[vname] = (0, 0)
        cmap[vs_cp] = vname
    inject_slice_marks(glyph_order, glyphs, metrics, cmap)


def install_yi_gsub(
    font, yi_bases: Sequence[str], glyphs: Dict, glyph_order: Sequence[str]
) -> None:
    """Install orientation VS ligas + FE08–FE09 slice (``ccmp``/``rlig``/``liga``)."""
    if not yi_bases:
        return

    from fontTools.otlLib.builder import buildLigatureSubstSubtable
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    from yi_halfwidth import build_ext_gsub_lookup

    standalone_map: Dict[Tuple[str, ...], str] = {}
    for yi in yi_bases:
        for vs_cp, _r, _fx, _fy, suffix in YI_ORIENTATION_MODES:
            if suffix is None:
                continue
            standalone_map[(yi, vs_glyph_name(vs_cp))] = variant_glyph_name(yi, suffix)

    lookups: List = []
    if standalone_map:
        # Chunk ligatures to keep each subtable under Offset16 limits.
        items = list(standalone_map.items())
        chunk = 2048
        subs = [
            buildLigatureSubstSubtable(dict(items[i : i + chunk]))
            for i in range(0, len(items), chunk)
        ]
        lookups.append(build_ext_gsub_lookup(subs))

    def _langsys() -> ot.DefaultLangSys:
        ls = ot.DefaultLangSys()
        ls.ReqFeatureIndex = 0xFFFF
        ls.FeatureCount = len(COMPOSITION_FEATURE_TAGS)
        ls.FeatureIndex = list(range(len(COMPOSITION_FEATURE_TAGS)))
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
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        rec.Script.DefaultLangSys = _langsys()
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gsub.ScriptList.ScriptRecord.append(rec)
    gsub.ScriptList.ScriptCount = len(script_tags)

    feature_indices = list(range(len(lookups)))
    gsub.FeatureList = ot.FeatureList()
    gsub.FeatureList.FeatureRecord = []
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = ot.FeatureRecord()
        fr.FeatureTag = tag
        fr.Feature = ot.Feature()
        fr.Feature.FeatureParams = None
        fr.Feature.LookupCount = len(feature_indices)
        fr.Feature.LookupListIndex = list(feature_indices)
        gsub.FeatureList.FeatureRecord.append(fr)
    gsub.FeatureList.FeatureCount = len(COMPOSITION_FEATURE_TAGS)

    gsub.LookupList = ot.LookupList()
    gsub.LookupList.Lookup = lookups
    gsub.LookupList.LookupCount = len(lookups)

    table = newTable("GSUB")
    table.table = gsub
    font["GSUB"] = table

    full_forms: List[str] = []
    for yi in yi_bases:
        full_forms.extend(orientation_form_names(yi, modes=YI_ORIENTATION_MODES))
    install_slice_gsub(font, full_forms, glyphs=glyphs, glyph_order=glyph_order)


def build_panyi_font(
    inv: YiInventory,
    out_dir: str,
    target_upem: int,
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    """Build the single ``panyi`` font (standalones + D4 + FE08–FE09 slices)."""
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")

    out_path = os.path.join(out_dir, f"{FAMILY_NAME}.ttf")

    print("  Recording source outlines...", flush=True)
    tt = TTFont(inv.source_path, fontNumber=0)
    try:
        recs: Dict[int, object] = {}
        for idx, cp in enumerate(inv.src_cps):
            rec = record_glyph(tt, inv.glyph_names[cp])
            if rec is not None:
                recs[idx] = rec
    finally:
        tt.close()

    print(
        f"  Scaling {len(recs)} standalones "
        f"(sx {inv.source_advance}→{target_upem}, "
        f"sy maxH {inv.source_max_height:.0f}→{target_upem})...",
        flush=True,
    )
    standalones: Dict[int, Tuple] = {}
    for idx, rec in recs.items():
        sa = make_standalone_glyph(
            rec,
            target_upem,
            source_advance=inv.source_advance,
            source_center_y=inv.source_center_y,
            source_max_height=inv.source_max_height,
        )
        if sa is not None:
            standalones[idx] = sa

    # Scale fixed stem targets if building at a non-default UPM.
    stem_scale = float(target_upem) / float(DEFAULT_UPEM)
    ref_v_stem = REFERENCE_VERTICAL_STEM * stem_scale
    ref_h_stem = REFERENCE_HORIZONTAL_STEM * stem_scale
    print(
        f"  Stem refs: V={ref_v_stem:.0f} / H={ref_h_stem:.0f} "
        f"(measure after convert, adjust both axes)",
        flush=True,
    )

    print(
        "  Orientations: transform id+r90, then stem-normalize "
        f"(V={ref_v_stem:.0f} / H={ref_h_stem:.0f}; "
        "pseudorandom probes + binary search toward ref); other D4 = composites",
        flush=True,
    )

    glyph_order = [".notdef"]
    glyphs = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}
    yi_names: List[str] = []
    uvs_rows: List[Tuple[int, int, Optional[str]]] = []

    print("  Installing standalones + VS01..VS08 orientations...", flush=True)
    for idx, cp in enumerate(inv.src_cps):
        if idx not in standalones:
            continue
        sa_glyph, sa_adv, sa_lsb = standalones[idx]
        sa_name = glyph_name_for_cp(cp)
        glyph_order.append(sa_name)
        glyphs[sa_name] = sa_glyph
        metrics[sa_name] = (sa_adv, sa_lsb)
        cmap[cp] = sa_name
        yi_names.append(sa_name)
        add_d4_variant_glyphs(
            sa_name,
            advance=sa_adv,
            lsb=sa_lsb,
            target_upem=target_upem,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            modes=YI_ORIENTATION_MODES,
            reference_vertical_stem=ref_v_stem,
            reference_horizontal_stem=ref_h_stem,
        )
        uvs_rows.extend(
            build_d4_uvs_entries(cp, sa_name, glyphs=glyphs, modes=YI_ORIENTATION_MODES)
        )

    print(
        "  Installing FE08–FE09 slice halves "
        "(bake id+r90; composite other D4 forms)...",
        flush=True,
    )
    add_slice_halves(
        yi_names,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
        modes=YI_ORIENTATION_MODES,
    )

    _inject_vs(glyph_order, glyphs, metrics, cmap)

    if not yi_names:
        return out_path, 0, []

    mark_names: List[str] = []
    mark_cps: List[int] = []
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    try:
        mark_fonts = resolve_dakuten_mark_font_stack(
            os.path.dirname(inv.source_path)
        )
        print(
            f"  Loading dakuten marks from "
            f"{dakuten_mark_stack_label(mark_fonts)}...",
            flush=True,
        )
        mark_cps, mark_glyphs = load_dakuten_marks_from_stack(
            mark_fonts, target_upem
        )
        mark_names = add_dakuten_mark_glyphs(
            mark_cps,
            mark_glyphs,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
        )
        dakuten_bases = yi_forms_for_dakuten(yi_names, modes=YI_ORIENTATION_MODES)
        # Marks attach to full forms and to shared sliceAdv (after FE0x expansion).
        anchor_bases = list(dakuten_bases)
        if SLICE_ADV_NAME in glyphs:
            anchor_bases.append(SLICE_ADV_NAME)
        base_anchors = collect_dakuten_base_anchors(
            anchor_bases,
            glyphs=glyphs,
            target_upem=target_upem,
        )
        n_unique = len(mark_cps)
        print(
            f"  Dakuten: {n_unique} marks × 4 corners, "
            f"{len(base_anchors)} bases "
            f"(TR→BR→TL→BL; fixed H, L/R align)",
            flush=True,
        )
    except FileNotFoundError as exc:
        print(f"  Skipping dakuten marks: {exc}", flush=True)

    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)

    print(
        f"  Assembling font ({len(glyphs) - 1} glyphs, {len(yi_names)} Yi CPs)...",
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
        achVendID="pYi ",
    )
    fb.setupPost()

    print("  Compiling GSUB (orientations + FE08–FE09 slice)...", flush=True)
    install_yi_gsub(fb.font, yi_names, glyphs, glyph_order)

    if mark_names and base_anchors:
        print("  Compiling GSUB (dakuten corner slots TR→BR→TL→BL)...", flush=True)
        install_dakuten_slot_gsub(
            fb.font,
            mark_cps,
            glyphs=glyphs,
            glyph_order=glyph_order,
            base_names=list(base_anchors),
        )
        print("  Compiling GPOS (dakuten mark @ CJK corners)...", flush=True)
        install_dakuten_gpos(
            fb.font,
            base_anchors=base_anchors,
            mark_cps=mark_cps,
            mark_names=mark_names,
            glyph_order=glyph_order,
            glyphs=glyphs,
        )

    os.makedirs(out_dir, exist_ok=True)
    fb.save(out_path)
    if write_woff2:
        print("  Compressing WOFF2...", flush=True)
        woff2.compress(out_path, out_path.replace(".ttf", ".woff2"))
    if not write_ttf:
        try:
            os.remove(out_path)
        except OSError:
            pass

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
    css_path = os.path.join(out_dir, "panyi.css")
    # UVS FE00..FE07 + slice FE08..FE09 + PUA VS mirrors.
    extra = set(range(0xFE00, SLICE_V_CP + 1)) | set(range(0xE000, 0xE007 + 1))
    urange = unicode_range_css(sorted(set(codepoints) | extra))
    lines = [
        "/* Auto-generated single Yi font */",
        "",
        "@font-face {",
        f"  font-family: '{FAMILY_NAME}';",
        f"  src: url('./{FAMILY_NAME}.woff2') format('woff2'),",
        f"       url('./{FAMILY_NAME}.ttf') format('truetype'),",
        f"       url('{CSS_FONT_URL_BASE}/{FAMILY_NAME}.woff2') format('woff2');",
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

    fontlist_path = os.path.join(out_dir, "panyi-fontlist.css")
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(
            "/* Yi font family */\n" f":root {{\n  --font-panyi: '{FAMILY_NAME}';\n}}\n"
        )
    print(f"Wrote {fontlist_path}")


def build_all(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> None:
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    source = resolve_nuosu_path(in_dir)
    inv = load_inventory(source)
    if limit is not None:
        inv = YiInventory(
            inv.source_path,
            inv.src_cps[:limit],
            {cp: inv.glyph_names[cp] for cp in inv.src_cps[:limit]},
            inv.source_advance,
            inv.source_center_y,
            inv.source_max_height,
        )
        print(f"Yi inventory: first {inv.count} glyphs (--limit)")
    else:
        print(f"Yi inventory: {inv.count} glyphs from {NUOSU_FILENAME}")

    print(
        "  Orientations: VS01..VS08 / FE00..FE07 "
        f"(transform→normalize id+r90 → V={REFERENCE_VERTICAL_STEM:g} "
        f"H={REFERENCE_HORIZONTAL_STEM:g}; retry scales; other D4 = composites)"
    )
    print(
        f"  Slice: U+{SLICE_H_CP:04X}..U+{SLICE_V_CP:04X} "
        "(H / V half-planes + shared sliceAdv)"
    )
    print(
        "  Dakuten: mkanaplus + Nexsevka + JuliaMono + Constructium + "
        "Droid Sans + Arial Unicode MS + Gentium \\p{M} @ CJK corners "
        "(TR→BR→TL→BL; fixed H, L/R align; all D4 incl. r90my + sliceAdv)"
    )
    print(f"  Output: single font '{FAMILY_NAME}'")
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"  Formats: {fmt_note}")

    os.makedirs(out_dir, exist_ok=True)
    path, count, cps = build_panyi_font(
        inv,
        out_dir,
        target_upem,
        write_ttf=write_ttf,
        write_woff2=write_woff2,
    )
    if count:
        write_css(out_dir, cps)
    print(f"\nDone: {path} ({count} glyphs)", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build the single panyi Yi font (D4 + FE08–FE09 slice + dakuten)"
    )
    p.add_argument("--in", dest="in_dir", default=IN_DIR)
    p.add_argument("--out", dest="out_dir", default=OUT_DIR)
    p.add_argument("--upem", type=int, default=DEFAULT_UPEM)
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Use only the first N inventory codepoints (smoke test)",
    )
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument(
        "--ttf-only",
        "--no-woff2",
        action="store_true",
        help="Write TTF only (skip WOFF2); --no-woff2 is an alias",
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
        write_ttf=not args.woff2_only,
        write_woff2=not args.ttf_only,
    )
