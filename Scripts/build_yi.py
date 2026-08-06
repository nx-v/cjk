#!/usr/bin/env python3
"""
Build one Yi font (``panyi``) covering the whole inventory.

Contents:
* Standalone forms at real Unicode CPs (full CJK width) plus D4 variants

      yi VS0n   → variant   (ccmp/rlig/liga)   # VS01 = identity (no subst)

* Compounds via digraph unpack (no joiner, no per-pair glyphs — under 64k IDs):

      yi1 + yi2 + VS0n   →   yihL_i[.var] + yihR_j[.var]

  ``yihL`` is a half-cell in the left slot (advance = 1em); ``yihR`` is the
  same outline shifted +½em as a zero-width overlay. Axis-aligned D4 maps
  are TT composites; 2×2 rotates (r90/r270/diagonals) are baked outlines.
  A lone ``yi + VS`` (no second Yi before the VS) stays a standalone form.
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
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    DEFAULT_UPEM,
    NUOSU_FILENAME,
    TRANSFORM_MODES,
    VS_BASE,
    VS_LAST,
    YiInventory,
    add_d4_variant_glyphs,
    build_d4_uvs_entries,
    empty_glyph,
    halfcell_left_name,
    halfcell_right_name,
    load_inventory,
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


def _mode_target(base_name: str, suffix: Optional[str]) -> str:
    return base_name if suffix is None else variant_glyph_name(base_name, suffix)


def _mode_names(base_name: str) -> List[str]:
    """Identity plus all D4 variant glyph names for ``base_name``."""
    names = [base_name]
    for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES:
        if suffix is not None:
            names.append(variant_glyph_name(base_name, suffix))
    return names


def _coverage(glyphs: Sequence[str]) -> ot.Coverage:
    cov = ot.Coverage()
    cov.glyphs = list(glyphs)
    return cov


def _chain_context_format3(
    input_groups: Sequence[Sequence[str]],
    *,
    lookahead_groups: Sequence[Sequence[str]] = (),
    backtrack_groups: Sequence[Sequence[str]] = (),
    subst_lookup_index: int,
) -> ot.ChainContextSubst:
    """Chain rule: replace input[0] (via nested lookup) when context matches.

    ``backtrack_groups`` is in visual order (glyph immediately before input
    first); OpenType stores backtrack coverages in that same order.
    """
    st = ot.ChainContextSubst()
    st.Format = 3
    st.BacktrackGlyphCount = len(backtrack_groups)
    st.BacktrackCoverage = [_coverage(g) for g in backtrack_groups]
    st.InputGlyphCount = len(input_groups)
    st.InputCoverage = [_coverage(g) for g in input_groups]
    st.LookAheadGlyphCount = len(lookahead_groups)
    st.LookAheadCoverage = [_coverage(g) for g in lookahead_groups]
    rec = ot.SubstLookupRecord()
    rec.SequenceIndex = 0
    rec.LookupListIndex = subst_lookup_index
    st.SubstCount = 1
    st.SubstLookupRecord = [rec]
    return st


def install_rlig(
    font,
    yi_names: Sequence[str],
    *,
    compounds: bool = True,
) -> None:
    """Install GSUB digraph unpack + standalone VS (``ccmp``/``rlig``/``liga``).

    Compounds: ``yi1 + yi2 + VS`` → ``yihL + yihR`` (no joiner). The right-half
    liga is gated by a yihL backtrack so a lone ``yi + VS`` still forms the
    standalone variant instead of a half-cell. Pass ``compounds=False`` to
    emit only standalone VS ligatures.
    """
    if not yi_names:
        return

    yi_list = list(yi_names)
    left_chains: List = []
    left_singles: List = []
    right_chains: List = []
    right_ligas: List = []

    if compounds:
        left_names = [
            name
            for yi in yi_list
            for name in _mode_names(halfcell_left_name(int(yi[1:], 16)))
        ]

        # --- Left: SingleSubst yi → yihL[.var] (one per VS mode) ---
        for _vs_cp, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            mapping = {
                yi: _mode_target(halfcell_left_name(int(yi[1:], 16)), suffix)
                for yi in yi_list
            }
            sub = buildSingleSubstSubtable(mapping)
            lu = buildLookup([sub])
            lu.LookupType = 1
            left_singles.append(lu)

        # --- Left chain: yi' @yi vsXX → (left single) ---
        for mode_i, (vs_cp, _r, _fx, _fy, _suffix) in enumerate(TRANSFORM_MODES):
            vs = vs_glyph_name(vs_cp)
            st = _chain_context_format3(
                [yi_list],
                lookahead_groups=[yi_list, [vs]],
                subst_lookup_index=mode_i,
            )
            lu = buildLookup([st])
            lu.LookupType = 6
            left_chains.append(lu)

        # --- Right: Ligature yi+vs → yihR[.var] (nested; one per VS mode) ---
        for vs_cp, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            vs = vs_glyph_name(vs_cp)
            right_map = {
                (yi, vs): _mode_target(halfcell_right_name(int(yi[1:], 16)), suffix)
                for yi in yi_list
            }
            sub = buildLigatureSubstSubtable(right_map)
            lu = buildLookup([sub])
            lu.LookupType = 4
            right_ligas.append(lu)

        # --- Right chain: yihL yi' vsXX → (right liga starting at yi) ---
        for mode_i, (vs_cp, _r, _fx, _fy, _suffix) in enumerate(TRANSFORM_MODES):
            vs = vs_glyph_name(vs_cp)
            st = _chain_context_format3(
                [yi_list, [vs]],
                backtrack_groups=[left_names],
                subst_lookup_index=mode_i,
            )
            lu = buildLookup([st])
            lu.LookupType = 6
            right_chains.append(lu)

    # --- Standalone: yi + VS → variant (identity VS needs no subst) ---
    standalone_map: Dict[Tuple[str, ...], str] = {}
    for yi in yi_list:
        for vs_cp, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            if suffix is None:
                continue
            standalone_map[(yi, vs_glyph_name(vs_cp))] = variant_glyph_name(yi, suffix)
    standalone_lookups = []
    if standalone_map:
        sub = buildLigatureSubstSubtable(standalone_map)
        lu = buildLookup([sub])
        lu.LookupType = 4
        standalone_lookups.append(lu)

    n_left_c = len(left_chains)
    n_left_s = len(left_singles)
    n_right_c = len(right_chains)
    n_right_l = len(right_ligas)

    # Nested lookup indices (absolute within LookupList).
    for mode_i, lu in enumerate(left_chains):
        for st in lu.SubTable:
            st.SubstLookupRecord[0].LookupListIndex = n_left_c + mode_i
    right_liga_base = n_left_c + n_left_s + n_right_c
    for mode_i, lu in enumerate(right_chains):
        for st in lu.SubTable:
            st.SubstLookupRecord[0].LookupListIndex = right_liga_base + mode_i

    all_lookups = (
        left_chains + left_singles + right_chains + right_ligas + standalone_lookups
    )
    feature_indices = (
        list(range(n_left_c))
        + list(range(n_left_c + n_left_s, n_left_c + n_left_s + n_right_c))
        + list(
            range(
                n_left_c + n_left_s + n_right_c + n_right_l,
                n_left_c + n_left_s + n_right_c + n_right_l + len(standalone_lookups),
            )
        )
    )
    if not all_lookups:
        return

    def _langsys() -> ot.DefaultLangSys:
        ls = ot.DefaultLangSys()
        ls.ReqFeatureIndex = 0xFFFF
        ls.FeatureCount = len(COMPOSITION_FEATURE_TAGS)
        ls.FeatureIndex = list(range(len(COMPOSITION_FEATURE_TAGS)))
        return ls

    def _script_record(tag: str) -> ot.ScriptRecord:
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        rec.Script.DefaultLangSys = _langsys()
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        return rec

    # Parse "languagesystem X Y;" → OT script tags (yi/hani padded to 4 chars).
    script_tags: List[str] = []
    for line in COMPOSITION_LANGUAGE_SYSTEMS:
        parts = line.replace(";", "").split()
        if len(parts) >= 2 and parts[0] == "languagesystem":
            tag = parts[1]
            script_tags.append(tag.ljust(4)[:4])

    gsub = ot.GSUB()
    gsub.Version = 0x00010000
    gsub.ScriptList = ot.ScriptList()
    gsub.ScriptList.ScriptRecord = [_script_record(t) for t in script_tags]
    gsub.ScriptList.ScriptCount = len(script_tags)

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
    compounds: bool = True,
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

    what = "standalones + half-cells" if compounds else "standalones only"
    print(
        f"  Scaling {len(recs)} {what} "
        f"(sx {inv.source_advance}→{target_upem}, "
        f"sy maxH {inv.source_max_height:.0f}→{target_upem})...",
        flush=True,
    )
    standalones: Dict[int, Tuple] = {}
    halfcells: Dict[int, Tuple] = {}
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
        if compounds:
            hc = make_halfwidth_glyph(
                rec,
                target_upem,
                source_advance=inv.source_advance,
                source_center_y=inv.source_center_y,
                source_max_height=inv.source_max_height,
            )
            if hc is not None:
                halfcells[idx] = hc

    glyph_order = [".notdef"]
    glyphs = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}
    yi_names: List[str] = []
    uvs_rows: List[Tuple[int, int, Optional[str]]] = []

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
        add_d4_variant_glyphs(
            sa_name,
            advance=sa_adv,
            lsb=sa_lsb,
            target_upem=target_upem,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
        uvs_rows.extend(build_d4_uvs_entries(cp, sa_name, glyphs=glyphs))

    _inject_vs(glyph_order, glyphs, metrics, cmap)

    # --- Half-cells: left (1em adv) + right (0 adv overlay) + D4 ---
    if compounds:
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
            add_d4_variant_glyphs(
                left_name,
                advance=target_upem,
                lsb=h_lsb,
                target_upem=target_upem,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
            )

            right_name = halfcell_right_name(cp)
            r_glyph, r_adv, r_lsb = make_right_half_composite(
                left_name, target_upem, lsb=0
            )
            glyph_order.append(right_name)
            glyphs[right_name] = r_glyph
            metrics[right_name] = (r_adv, r_lsb)
            add_d4_variant_glyphs(
                right_name,
                advance=r_adv,
                lsb=r_lsb,
                target_upem=target_upem,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
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

    rlig_note = (
        "compound digraph + standalone VS" if compounds else "standalone VS only"
    )
    print(f"  Compiling rlig ({rlig_note})...", flush=True)
    install_rlig(fb.font, yi_names, compounds=compounds)

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
            "/* Yi font family */\n" f":root {{\n  --font-panyi: '{FAMILY_NAME}';\n}}\n"
        )
    print(f"Wrote {fontlist_path}")


def build_all(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    compounds: bool = True,
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

    if compounds:
        print("  Compounds: rlig  yi1 + yi2 + VS -> yihL + yihR digraph")
    else:
        print("  Compounds: excluded (--no-compounds)")
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
        compounds=compounds,
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
    p.add_argument(
        "--no-compounds",
        action="store_true",
        help="Skip half-cell digraphs and compound GSUB (standalones + D4 only)",
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
        compounds=not args.no_compounds,
        write_ttf=not args.woff2_only,
        write_woff2=not args.ttf_only,
    )
