#!/usr/bin/env python3
"""
Build one Yi font per syllable/radical, named by its standalone code point.

Example: U+A000 → ``A000.ttf`` / font-family ``A000``.

Each font contains:
* Standalone form at the real Unicode CP (full CJK width)
  plus VS01..VS08 rotation × reflection variants
* All ordered pairs ``(this, j)`` as flattened merged-outline compounds,
  cmap'd at unique contiguous ``U+40000 + i·N + j``, plus VS variants

    glyph VS0n   → variant   (rlig)

Half-cell glyphs are build-only intermediates (not emitted). Pair identities
are flattened outlines; D4 variants (VS02–08) are one-component TrueType
composites of the identity glyph. No shared compound codepoint reuse across
fonts.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.otlLib.builder import buildLigatureSubstSubtable, buildLookup
from fontTools.ttLib import TTFont, newTable, woff2
from fontTools.ttLib.tables import otTables as ot

from yi_halfwidth import (
    DEFAULT_UPEM,
    HALFWIDTH_BASE,
    NUOSU_FILENAME,
    TRANSFORM_MODES,
    VS_BASE,
    VS_LAST,
    YiInventory,
    center_glyph_in_cell,
    empty_glyph,
    load_inventory,
    make_composite_variant,
    make_halfwidth_glyph,
    make_standalone_glyph,
    merge_halfcell_glyphs,
    record_glyph,
    resolve_nuosu_path,
    variant_glyph_name,
    vs_glyph_name,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "yi")

CSS_FONT_URL_BASE = (
    "https://raw.githubusercontent.com/nexovolta/fonts/main/Scripts/dist/yi"
)


def glyph_name_for_cp(cp: int) -> str:
    return f"u{cp:04X}" if cp <= 0xFFFF else f"u{cp:05X}"


def compound_glyph_name(i: int, j: int) -> str:
    return f"yic_{i:04X}_{j:04X}"


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


def _add_variants(
    base_name: str,
    advance: int,
    lsb: int,
    target_upem: int,
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict,
    liga_map: Dict[Tuple[str, ...], str],
) -> None:
    """Attach D4 variants as one-component composites of ``base_name``."""
    for vs_cp, rot, flip_x, flip_y, suffix in TRANSFORM_MODES:
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
        liga_map[(base_name, vs_glyph_name(vs_cp))] = m_name


def _install_rlig(font, liga_map: Dict[Tuple[str, ...], str]) -> None:
    if not liga_map:
        return
    sub = buildLigatureSubstSubtable(liga_map)
    lookup = buildLookup([sub])
    lookup.LookupType = 4

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
    fr.Feature.LookupCount = 1
    fr.Feature.LookupListIndex = [0]
    gsub.FeatureList = ot.FeatureList()
    gsub.FeatureList.FeatureRecord = [fr]
    gsub.FeatureList.FeatureCount = 1

    gsub.LookupList = ot.LookupList()
    gsub.LookupList.Lookup = [lookup]
    gsub.LookupList.LookupCount = 1

    table = newTable("GSUB")
    table.table = gsub
    font["GSUB"] = table


def _save_font(
    out_path: str,
    target_upem: int,
    family: str,
    ps: str,
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict,
    cmap: Dict[int, str],
    liga_map: Dict[Tuple[str, ...], str],
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    if not cmap:
        return out_path, 0, []
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")

    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)

    fb = FontBuilder(target_upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    fb.setupCharacterMap(cmap)
    fb.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": ps,
            "fullName": family,
            "psName": ps,
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
    _install_rlig(fb.font, liga_map)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fb.save(out_path)
    if write_woff2:
        woff2.compress(out_path, out_path.replace(".ttf", ".woff2"))
    if not write_ttf:
        try:
            os.remove(out_path)
        except OSError:
            pass
    return out_path, len(glyphs) - 1, sorted(cmap.keys())


def build_cp_font(
    inv: YiInventory,
    index: int,
    standalones: Dict[int, Tuple],
    halfcells: Dict[int, Tuple],
    out_dir: str,
    target_upem: int,
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    """Build the font for inventory ``index`` (named by standalone CP).

    ``halfcells`` are build-only intermediates used to merge pair outlines;
    they are not emitted. Each pair ``(index, j)`` is cmap'd at the unique
    contiguous PUA ``U+40000 + index·N + j``.
    """
    src_cp = inv.src_cps[index]
    font_id = inv.font_id(index)
    out_path = os.path.join(out_dir, f"{font_id}.ttf")

    if index not in standalones or index not in halfcells:
        return out_path, 0, []

    glyph_order = [".notdef"]
    glyphs = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}
    liga_map: Dict[Tuple[str, ...], str] = {}

    # --- Standalone at real Unicode CP ---
    sa = standalones[index]
    sa_glyph, sa_adv, sa_lsb = sa
    sa_name = glyph_name_for_cp(src_cp)
    glyph_order.append(sa_name)
    glyphs[sa_name] = sa_glyph
    metrics[sa_name] = (sa_adv, sa_lsb)
    cmap[src_cp] = sa_name
    _add_variants(
        sa_name,
        sa_adv,
        sa_lsb,
        target_upem,
        glyph_order,
        glyphs,
        metrics,
        liga_map,
    )

    # --- All pairs (this, j): unique contiguous PUA U+40000 + i·N + j ---
    left_glyph = halfcells[index][0]
    for j, hc in halfcells.items():
        made = merge_halfcell_glyphs(left_glyph, hc[0], target_upem)
        if made is None:
            continue
        c_glyph, c_adv, c_lsb = made
        if c_glyph.isComposite():
            raise RuntimeError(f"composite leaked in {font_id} pair ({index},{j})")
        c_glyph = center_glyph_in_cell(c_glyph, target_upem)
        try:
            c_glyph.recalcBounds(None)
            c_lsb = int(c_glyph.xMin)
        except Exception:
            pass
        c_name = compound_glyph_name(index, j)
        glyph_order.append(c_name)
        glyphs[c_name] = c_glyph
        metrics[c_name] = (c_adv, c_lsb)
        cmap[inv.compound_cp(index, j)] = c_name
        _add_variants(
            c_name,
            c_adv,
            c_lsb,
            target_upem,
            glyph_order,
            glyphs,
            metrics,
            liga_map,
        )

    _inject_vs(glyph_order, glyphs, metrics, cmap)
    return _save_font(
        out_path,
        target_upem,
        family=font_id,
        ps=f"panyi-{font_id}",
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        cmap=cmap,
        liga_map=liga_map,
        write_ttf=write_ttf,
        write_woff2=write_woff2,
    )


# ---------- Workers ----------

_WORKER_INV: Optional[YiInventory] = None
_WORKER_SA: Optional[Dict[int, Tuple]] = None
_WORKER_HC: Optional[Dict[int, Tuple]] = None
_WORKER_OUT: Optional[str] = None
_WORKER_UPEM: Optional[int] = None
_WORKER_WOFF2: bool = False


def _init_worker(
    source_path: str,
    src_cps: Tuple[int, ...],
    glyph_names: Dict[int, str],
    out_dir: str,
    upem: int,
    write_woff2: bool,
) -> None:
    """Load sources; precompute standalones + half-cells (merge intermediates)."""
    global _WORKER_INV, _WORKER_SA, _WORKER_HC, _WORKER_OUT, _WORKER_UPEM, _WORKER_WOFF2
    _WORKER_INV = YiInventory(source_path, src_cps, glyph_names)
    _WORKER_OUT = out_dir
    _WORKER_UPEM = upem
    _WORKER_WOFF2 = write_woff2

    tt = TTFont(source_path, fontNumber=0)
    try:
        recs = {}
        for idx, cp in enumerate(src_cps):
            rec = record_glyph(tt, glyph_names[cp])
            if rec is not None:
                recs[idx] = rec
    finally:
        tt.close()

    standalones: Dict[int, Tuple] = {}
    halfcells: Dict[int, Tuple] = {}
    for idx, rec in recs.items():
        sa = make_standalone_glyph(rec, upem)
        if sa is not None:
            standalones[idx] = sa
        hc = make_halfwidth_glyph(rec, upem)
        if hc is not None:
            halfcells[idx] = hc
    _WORKER_SA = standalones
    _WORKER_HC = halfcells
    print(
        f"  worker cache: {len(standalones)} standalones, "
        f"{len(halfcells)} half-cells (build-only)",
        flush=True,
    )


def _cp_task(index: int) -> Tuple[str, int, List[int], str]:
    assert _WORKER_INV is not None
    assert _WORKER_SA is not None
    assert _WORKER_HC is not None
    assert _WORKER_OUT is not None
    assert _WORKER_UPEM is not None
    # Always keep the TTF during the worker pass; WOFF2 / TTF retention
    # is handled after all workers finish (see build_all).
    path, count, cps = build_cp_font(
        _WORKER_INV,
        index,
        _WORKER_SA,
        _WORKER_HC,
        _WORKER_OUT,
        _WORKER_UPEM,
        write_ttf=True,
        write_woff2=False,
    )
    return _WORKER_INV.font_id(index), count, cps, path


def _compress_woff2(ttf_path: str) -> None:
    woff2.compress(ttf_path, ttf_path.replace(".ttf", ".woff2"))


def _drop_ttf(ttf_path: str) -> None:
    try:
        os.remove(ttf_path)
    except OSError:
        pass


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


def write_css(out_dir: str, built: List[Tuple[str, int, List[int]]]) -> None:
    css_path = os.path.join(out_dir, "panyi.css")
    lines = [
        "/* Auto-generated Yi per-codepoint fonts (name = standalone CP) */",
        "",
    ]
    family_names: List[str] = []
    for font_id, _count, codepoints in built:
        family_names.append(font_id)
        urange = unicode_range_css(codepoints)
        url = f"{CSS_FONT_URL_BASE}/{font_id}.woff2"
        lines.append("@font-face {")
        lines.append(f"  font-family: '{font_id}';")
        lines.append(f"  src: url('{url}') format('woff2');")
        lines.append("  font-weight: normal;")
        lines.append("  font-style: normal;")
        lines.append("  font-display: swap;")
        if urange:
            lines.append(f"  unicode-range: {urange};")
        lines.append("}")
        lines.append("")

    with open(css_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {css_path}")

    quoted = ", ".join(f"'{n}'" for n in family_names)
    fontlist_path = os.path.join(out_dir, "panyi-fontlist.css")
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(
            "/* Yi per-codepoint font stack (family name = standalone CP hex) */\n"
            f":root {{\n  --font-panyi: {quoted};\n}}\n"
        )
    print(f"Wrote {fontlist_path}")


def build_all(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    jobs: int,
    *,
    limit: Optional[int] = None,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> None:
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    source = resolve_nuosu_path(in_dir)
    inv = load_inventory(source)
    print(f"Yi inventory: {inv.count} glyphs from {NUOSU_FILENAME}")
    compound_last = HALFWIDTH_BASE + inv.count * inv.count - 1
    print(
        f"  Compound cmap: U+{HALFWIDTH_BASE:X}–U+{compound_last:X} "
        f"({inv.count}² unique; pair (i,j) → U+40000 + i·N + j)"
    )
    lo, hi = inv.compound_range(0)
    print(
        f"  Per-font slice example (i=0): U+{lo:X}–U+{hi:X}"
    )
    print(f"  Transform VS: U+{VS_BASE:X}–U+{VS_LAST:X} (8 unique D4 symmetries)")
    print(f"  One font per CP, named by standalone code point")
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"  Output: {fmt_note}")

    os.makedirs(out_dir, exist_ok=True)
    indices = list(range(inv.count))
    if limit is not None:
        indices = indices[:limit]
        print(f"  Building first {len(indices)} fonts only (--limit)")

    print(f"\nBuilding {len(indices)} fonts with {jobs} workers...", flush=True)
    built: List[Tuple[str, int, List[int]]] = []
    ttf_paths: List[str] = []
    done = 0

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=max(1, jobs),
        initializer=_init_worker,
        initargs=(
            inv.source_path,
            inv.src_cps,
            dict(inv.glyph_names),
            out_dir,
            target_upem,
            False,  # woff2 after all TTFs
        ),
    ) as ex:
        futs = [ex.submit(_cp_task, i) for i in indices]
        for fut in concurrent.futures.as_completed(futs):
            font_id, count, cps, path = fut.result()
            done += 1
            if count:
                built.append((font_id, count, cps))
                ttf_paths.append(path)
            print(
                f"  [{done}/{len(indices)}] {font_id}.ttf ({count} glyphs)",
                flush=True,
            )

    if write_woff2 and ttf_paths:
        print(f"\nCompressing {len(ttf_paths)} WOFF2...", flush=True)
        with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, jobs)) as ex:
            list(ex.map(_compress_woff2, ttf_paths))

    if not write_ttf and ttf_paths:
        print(f"Removing {len(ttf_paths)} intermediate TTFs...", flush=True)
        with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, jobs)) as ex:
            list(ex.map(_drop_ttf, ttf_paths))

    # Sort by code point numeric value
    built.sort(key=lambda t: int(t[0], 16))
    write_css(out_dir, built)
    print(f"\nDone: {len(built)} Yi fonts -> {out_dir}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build per-codepoint Yi fonts (name = standalone CP)"
    )
    p.add_argument("--in", dest="in_dir", default=IN_DIR)
    p.add_argument("--out", dest="out_dir", default=OUT_DIR)
    p.add_argument("--upem", type=int, default=DEFAULT_UPEM)
    p.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=max(1, (os.cpu_count() or 4) // 2),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Build only the first N codepoint fonts (smoke test)",
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
        args.jobs,
        limit=args.limit,
        write_ttf=not args.woff2_only,
        write_woff2=not args.ttf_only,
    )
