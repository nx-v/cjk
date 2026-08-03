#!/usr/bin/env python3
"""
Build one Yi font (``panyi``) covering the whole inventory.

Contents:
* Standalone forms at real Unicode CPs (full CJK width) plus D4 variants

      yi VS0n   → variant   (rlig)   # VS01 = identity (no subst)

* Compounds via digraph unpack (no per-pair glyphs — stays under 64k IDs):

      yi1 + CGJ + yi2 + VS0n   →   yihL_i[.var] + yihR_j[.var]   (rlig)

  ``yihL`` is a half-cell in the left slot (advance = 1em); ``yihR`` is the
  same outline shifted +½em as a zero-width overlay. D4 variants are
  one-component composites about the CJK typo center.
"""

from __future__ import annotations

import argparse
import os
from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.otlLib.builder import (
    buildLigatureSubstSubtable,
    buildLookup,
    buildSingleSubstSubtable,
)
from fontTools.ttLib import TTFont, newTable, woff2
from fontTools.ttLib.tables import otTables as ot

from yi_halfwidth import (
    CGJ_CP,
    DEFAULT_UPEM,
    NUOSU_FILENAME,
    TRANSFORM_MODES,
    VS_BASE,
    VS_LAST,
    YiInventory,
    cgj_glyph_name,
    empty_glyph,
    halfcell_left_name,
    halfcell_right_name,
    load_inventory,
    make_composite_variant,
    make_halfwidth_glyph,
    make_right_half_composite,
    make_standalone_glyph,
    record_glyph,
    resolve_nuosu_path,
    variant_glyph_name,
    vs_glyph_name,
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
    for vs_cp, _rot, _fx, _fy, _suffix in TRANSFORM_MODES:
        vname = vs_glyph_name(vs_cp)
        if vname not in glyphs:
            glyph_order.append(vname)
            glyphs[vname] = empty_glyph()
            metrics[vname] = (0, 0)
        cmap[vs_cp] = vname


def _inject_cgj(
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict,
    cmap: Dict[int, str],
) -> None:
    name = cgj_glyph_name()
    if name not in glyphs:
        glyph_order.append(name)
        glyphs[name] = empty_glyph()
        metrics[name] = (0, 0)
    cmap[CGJ_CP] = name


def _add_variants(
    base_name: str,
    advance: int,
    lsb: int,
    target_upem: int,
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict,
) -> None:
    """Attach D4 variants as one-component composites of ``base_name``."""
    for _vs_cp, rot, flip_x, flip_y, suffix in TRANSFORM_MODES:
        if suffix is None:
            continue
        m_name = variant_glyph_name(base_name, suffix)
        if m_name in glyphs:
            continue
        m_glyph, m_adv, m_lsb = make_composite_variant(
            base_name,
            target_upem,
            rot90_quarters=rot,
            flip_x=flip_x,
            flip_y=flip_y,
            advance=advance,
            lsb=lsb,
        )
        glyph_order.append(m_name)
        glyphs[m_name] = m_glyph
        metrics[m_name] = (m_adv, m_lsb)


def _mode_target(base_name: str, suffix: Optional[str]) -> str:
    return base_name if suffix is None else variant_glyph_name(base_name, suffix)


def _coverage(glyphs: Sequence[str]) -> ot.Coverage:
    cov = ot.Coverage()
    cov.glyphs = list(glyphs)
    return cov


def _chain_context_format3(
    input_glyphs: Sequence[str],
    lookahead_groups: Sequence[Sequence[str]],
    subst_lookup_index: int,
) -> ot.ChainContextSubst:
    """One-input chain rule: replace input[0] when lookahead matches."""
    st = ot.ChainContextSubst()
    st.Format = 3
    st.BacktrackGlyphCount = 0
    st.BacktrackCoverage = []
    st.InputGlyphCount = 1
    st.InputCoverage = [_coverage(input_glyphs)]
    st.LookAheadGlyphCount = len(lookahead_groups)
    st.LookAheadCoverage = [_coverage(g) for g in lookahead_groups]
    rec = ot.SubstLookupRecord()
    rec.SequenceIndex = 0
    rec.LookupListIndex = subst_lookup_index
    st.SubstCount = 1
    st.SubstLookupRecord = [rec]
    return st


def install_rlig(font, yi_names: Sequence[str]) -> None:
    """Install compact ``rlig``: digraph unpack + standalone VS.

    Left half uses 8 chain-context rules (one per VS) with shared Yi coverage
    and a SingleSubst — O(N) tables, not O(N²) FEA expansion via ``@yi``.
    """
    if not yi_names:
        return

    cgj = cgj_glyph_name()
    yi_list = list(yi_names)

    # --- Lookups: SingleSubst yi → yihL[.var] (one per VS mode) ---
    single_lookups = []
    for _vs_cp, _r, _fx, _fy, suffix in TRANSFORM_MODES:
        mapping = {}
        for yi in yi_list:
            cp = int(yi[1:], 16)
            mapping[yi] = _mode_target(halfcell_left_name(cp), suffix)
        sub = buildSingleSubstSubtable(mapping)
        lu = buildLookup([sub])
        lu.LookupType = 1
        single_lookups.append(lu)

    n_single = len(single_lookups)

    # --- ChainContext: yi' cgj @yi vsXX → (SingleSubst above) ---
    chain_lookups = []
    for mode_i, (vs_cp, _r, _fx, _fy, _suffix) in enumerate(TRANSFORM_MODES):
        vs = vs_glyph_name(vs_cp)
        st = _chain_context_format3(
            yi_list,
            [[cgj], yi_list, [vs]],
            subst_lookup_index=mode_i,  # absolute index in final LookupList
        )
        lu = buildLookup([st])
        lu.LookupType = 6
        chain_lookups.append(lu)

    # Fix subst lookup indices: singles come after chains in the list below,
    # so rewrite to n_chain + mode_i. We'll order as: chains first, then singles,
    # then ligatures — chain records must point at singles.
    n_chain = len(chain_lookups)
    for mode_i, lu in enumerate(chain_lookups):
        for st in lu.SubTable:
            st.SubstLookupRecord[0].LookupListIndex = n_chain + mode_i

    # --- Ligatures (split so each subtable stays under the 64KB OT limit) ---
    # All compound-right ligatures share first glyph ``cgj``; packing every
    # VS into one LigatureSet overflows (~N*8*10 bytes) and fontTools' split
    # path is extremely slow. One lookup per VS keeps each set at ~N entries.
    liga_lookups = []
    for vs_cp, _r, _fx, _fy, suffix in TRANSFORM_MODES:
        vs = vs_glyph_name(vs_cp)
        right_map: Dict[Tuple[str, ...], str] = {}
        for yi in yi_list:
            cp = int(yi[1:], 16)
            right_map[(cgj, yi, vs)] = _mode_target(halfcell_right_name(cp), suffix)
        sub = buildLigatureSubstSubtable(right_map)
        lu = buildLookup([sub])
        lu.LookupType = 4
        liga_lookups.append(lu)

    standalone_map: Dict[Tuple[str, ...], str] = {}
    for yi in yi_list:
        for vs_cp, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            if suffix is None:
                continue
            standalone_map[(yi, vs_glyph_name(vs_cp))] = variant_glyph_name(yi, suffix)
    if standalone_map:
        sub = buildLigatureSubstSubtable(standalone_map)
        lu = buildLookup([sub])
        lu.LookupType = 4
        liga_lookups.append(lu)

    all_lookups = chain_lookups + single_lookups + liga_lookups
    # Feature: chains then ligatures (singles are only reached from chains).
    feature_indices = list(range(n_chain)) + [
        n_chain + n_single + i for i in range(len(liga_lookups))
    ]

    gsub = ot.GSUB()
    gsub.Version = 0x00010000

    script = ot.ScriptRecord()
    script.ScriptTag = "DFLT"
    script.Script = ot.Script()
    script.Script.DefaultLangSys = ot.DefaultLangSys()
    script.Script.DefaultLangSys.ReqFeatureIndex = 0xFFFF
    script.Script.DefaultLangSys.FeatureCount = 1
    script.Script.DefaultLangSys.FeatureIndex = [0]
    script.Script.LangSysCount = 0
    script.Script.LangSysRecord = []
    gsub.ScriptList = ot.ScriptList()
    gsub.ScriptList.ScriptRecord = [script]
    gsub.ScriptList.ScriptCount = 1

    fr = ot.FeatureRecord()
    fr.FeatureTag = "rlig"
    fr.Feature = ot.Feature()
    fr.Feature.FeatureParams = None
    fr.Feature.LookupCount = len(feature_indices)
    fr.Feature.LookupListIndex = feature_indices
    gsub.FeatureList = ot.FeatureList()
    gsub.FeatureList.FeatureRecord = [fr]
    gsub.FeatureList.FeatureCount = 1

    gsub.LookupList = ot.LookupList()
    gsub.LookupList.Lookup = all_lookups
    gsub.LookupList.LookupCount = len(all_lookups)

    table = newTable("GSUB")
    table.table = gsub
    font["GSUB"] = table


def build_panyi_font(
    inv: YiInventory,
    out_dir: str,
    target_upem: int,
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    """Build the single ``panyi`` font for the whole inventory."""
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
        f"  Fitting {len(recs)} standalones + half-cells...",
        flush=True,
    )
    standalones: Dict[int, Tuple] = {}
    halfcells: Dict[int, Tuple] = {}
    for idx, rec in recs.items():
        sa = make_standalone_glyph(rec, target_upem)
        if sa is not None:
            standalones[idx] = sa
        hc = make_halfwidth_glyph(rec, target_upem)
        if hc is not None:
            halfcells[idx] = hc

    glyph_order = [".notdef"]
    glyphs = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}
    yi_names: List[str] = []

    # --- Standalones + D4 ---
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
        _add_variants(
            sa_name, sa_adv, sa_lsb, target_upem, glyph_order, glyphs, metrics
        )

    _inject_cgj(glyph_order, glyphs, metrics, cmap)
    _inject_vs(glyph_order, glyphs, metrics, cmap)

    # --- Half-cells: left (1em adv) + right (0 adv overlay) + D4 ---
    print("  Installing half-cell digraph components...", flush=True)
    for idx, cp in enumerate(inv.src_cps):
        if idx not in halfcells:
            continue
        h_glyph, _h_adv, h_lsb = halfcells[idx]
        left_name = halfcell_left_name(cp)
        glyph_order.append(left_name)
        glyphs[left_name] = h_glyph
        # Full-em advance so digraph overlay advances one cell.
        metrics[left_name] = (target_upem, h_lsb)
        _add_variants(
            left_name,
            target_upem,
            h_lsb,
            target_upem,
            glyph_order,
            glyphs,
            metrics,
        )

        right_name = halfcell_right_name(cp)
        r_glyph, r_adv, r_lsb = make_right_half_composite(
            left_name, target_upem, lsb=0
        )
        glyph_order.append(right_name)
        glyphs[right_name] = r_glyph
        metrics[right_name] = (r_adv, r_lsb)
        _add_variants(
            right_name, r_adv, r_lsb, target_upem, glyph_order, glyphs, metrics
        )

    if not yi_names:
        return out_path, 0, []

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
        achVendID="pYi ",
    )
    fb.setupPost()

    print("  Compiling rlig (compound digraph + standalone VS)...", flush=True)
    install_rlig(fb.font, yi_names)

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
    urange = unicode_range_css(codepoints)
    url = f"{CSS_FONT_URL_BASE}/{FAMILY_NAME}.woff2"
    lines = [
        "/* Auto-generated single Yi font */",
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

    fontlist_path = os.path.join(out_dir, "panyi-fontlist.css")
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(
            "/* Yi font family */\n"
            f":root {{\n  --font-panyi: '{FAMILY_NAME}';\n}}\n"
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
        )
        print(f"Yi inventory: first {inv.count} glyphs (--limit)")
    else:
        print(f"Yi inventory: {inv.count} glyphs from {NUOSU_FILENAME}")

    print(
        f"  Compounds: rlig  yi1 + CGJ (U+{CGJ_CP:04X}) + yi2 + VS "
        "-> yihL + yihR digraph"
    )
    print(f"  Transform VS: U+{VS_BASE:X}-U+{VS_LAST:X} (8 unique D4 symmetries)")
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
    p = argparse.ArgumentParser(description="Build the single panyi Yi font")
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
