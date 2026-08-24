#!/usr/bin/env python3
"""
Build Yi fonts: `edenia yi` (D4 + dakuten) and pigeonholed `edenia yi h`
(D4 + FE00 overlay + FE08–FE0F slices), matching CJK base vs `h`.

Contents
--------
* Standalone forms at real Unicode CPs (full CJK width) plus D4 orientations:

      yi + VS02..VS08 / FE01..FE07   →   oriented variant
      (bare yi = identity; U+FE00 = overlay, on the `h` face)

* Combining slices live on `edenia yi h` (full cell advance) + overlay::

      A FE08          →  A.top
      A FE08 FE00 B FE09  →  A.top.ov + B.bot
      FE08–FE0B halves; FE0C–FE0F triangles

  `h` is one file per `cp>>8` page so D4 × 8 slices × overlays stay
  under the TTF 65535-glyph cap.

  Standalone fit: shared `sx` from NuosuSIL monospace advance → em,
  shared `sy` from inventory max ink height, Y centered in padded typo box,
  horizontal stems at 125% (Y-only Weight), then ~98% ideographic inset.

* Dakuten marks (shared stack `\\p{M}` minus letter / overlay / oversized):
  GPOS `mark` at fixed CJK corners on VS01..VS07 forms.
  Successive marks fill TR → BR → TL → BL. No left-squish `.dk` forms.
"""

from __future__ import annotations

import argparse
import os
import pickle
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Dict, List, Optional, Sequence, Set, Tuple

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.ttLib import TTFont

from shared_diacritics import (
    DAKUTEN_SLOT_CYCLE,
    DAKUTEN_SLOT_COUNT,
    add_dakuten_mark_glyphs,
    add_dakuten_chain_mark_glyphs,
    collect_dakuten_base_anchors,
    dakuten_mark_stack_label,
    install_dakuten_chain_gsub,
    install_dakuten_gpos,
    install_dakuten_mark_chain_gpos,
    is_dakuten_chain_glyph,
    install_dakuten_slot_gsub,
    load_dakuten_marks_from_stack,
    resolve_dakuten_mark_font_stack,
    yi_forms_for_dakuten,
)
from edenia_names import (
    CSS_YI,
    FAMILY_YI,
    PS_YI,
    family_yi_variant,
    h_bucket_face_id,
    parse_h_bucket_face_id,
    ps_yi,
)
from shared_half_cells import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    DEFAULT_UPEM,
    NUOSU_FILENAME,
    STANDALONE_CELL_SCALE,
    STANDALONE_VERT_PAD,
    TTF_GLYPH_LIMIT,
    YI_ORIENTATION_MODES,
    YiInventory,
    add_d4_variant_glyphs,
    build_d4_uvs_entries,
    empty_glyph,
    load_inventory,
    make_standalone_glyph,
    orientation_form_names,
    record_glyph,
    resolve_nuosu_path,
    subset_glyph_tables,
    variant_glyph_name,
    uvs_selector_for_mode,
    vs_glyph_name,
)
from yi_slice import (
    add_slice_halves,
    inject_slice_marks,
    install_slice_gsub,
)
from sync_edenian_fonts import sync_dist_to_plugin
from cdn_fonts import dist_rel, format_src_line
from shared_font_builder import setup_head_timestamps
from shared_hinting import add_jobs_argument, add_no_hint_argument, finish_font_outputs

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "yi")

FAMILY_NAME = FAMILY_YI
PS_NAME = PS_YI

# After shared sx/sy fit: uniform horizontal-stem Weight (Y-only CAPE).
YI_HORIZONTAL_STEM_WEIGHT = 1.4


def glyph_name_for_cp(cp: int) -> str:
    return f"u{cp:04X}" if cp <= 0xFFFF else f"u{cp:05X}"


def _inject_d4_vs(
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict,
    cmap: Dict[int, str],
) -> None:
    """Cmap FE01–FE07 for D4 (not overlay / slices). BMP PUA is edenia kana."""
    for mode_i, (vs_cp, _rot, _fx, _fy, _suffix) in enumerate(YI_ORIENTATION_MODES):
        vname = vs_glyph_name(vs_cp)
        if vname not in glyphs:
            glyph_order.append(vname)
            glyphs[vname] = empty_glyph()
            metrics[vname] = (0, 0)
        uvs = uvs_selector_for_mode(mode_i)
        if uvs is not None:
            cmap[uvs] = vname


def _inject_vs(
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict,
    cmap: Dict[int, str],
    *,
    slices: bool = True,
) -> None:
    _inject_d4_vs(glyph_order, glyphs, metrics, cmap)
    if slices:
        inject_slice_marks(glyph_order, glyphs, metrics, cmap)


def install_yi_gsub(
    font, yi_bases: Sequence[str], glyphs: Dict, glyph_order: Sequence[str],
    *,
    slices: bool = True,
) -> None:
    """Install orientation VS ligas; optionally FE00/FE08–F slice ligas."""
    if not yi_bases:
        return

    from fontTools.otlLib.builder import buildLigatureSubstSubtable
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    from shared_half_cells import build_ext_gsub_lookup

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
    if slices:
        install_slice_gsub(font, full_forms, glyphs=glyphs, glyph_order=glyph_order)


def _dakuten_keep_names(
    glyph_order: Sequence[str], mark_names: Sequence[str]
) -> Set[str]:
    keep = {n for n in mark_names if n}
    keep.update(n for n in glyph_order if ".mk" in n)
    return keep


def _save_yi_face(
    *,
    face_id: str,
    variant: str,
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    uvs_rows: List[Tuple[int, int, Optional[str]]],
    yi_names: Sequence[str],
    mark_names: Sequence[str],
    mark_cps: Sequence[int],
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]],
    out_dir: str,
    target_upem: int,
    slices: bool,
) -> Tuple[str, str, int, List[int]]:
    n_glyphs = len(glyphs)
    if n_glyphs > TTF_GLYPH_LIMIT:
        raise RuntimeError(
            f"{face_id}: {n_glyphs} glyphs exceeds TTF uint16 max ({TTF_GLYPH_LIMIT})"
        )
    family = family_yi_variant(variant)
    ps = ps_yi(face_id)
    out_path = os.path.join(out_dir, f"{face_id}.ttf")
    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)
    print(
        f"  Assembling {family} / {face_id} "
        f"({n_glyphs - 1} glyphs, {len(yi_names)} Yi CPs)...",
        flush=True,
    )
    fb = FontBuilder(target_upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    if uvs_rows:
        fb.setupCharacterMap(cmap, uvs=uvs_rows)
    else:
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

    gsub_note = "orientations + FE00/FE08–F slice" if slices else "orientations"
    print(f"  Compiling GSUB ({gsub_note})...", flush=True)
    install_yi_gsub(fb.font, yi_names, glyphs, glyph_order, slices=slices)

    face_anchors = {k: v for k, v in base_anchors.items() if k in glyphs}
    face_marks = [
        n for n in mark_names if n in glyphs and not is_dakuten_chain_glyph(n)
    ]
    if face_marks and face_anchors:
        print(f"  Compiling GSUB (dakuten slots {DAKUTEN_SLOT_CYCLE})...", flush=True)
        install_dakuten_slot_gsub(
            fb.font,
            mark_cps,
            glyphs=glyphs,
            glyph_order=glyph_order,
            base_names=list(face_anchors),
        )
        install_dakuten_chain_gsub(
            fb.font,
            mark_cps,
            glyphs=glyphs,
            glyph_order=glyph_order,
        )
        print("  Compiling GPOS (dakuten mark @ CJK corners)...", flush=True)
        install_dakuten_gpos(
            fb.font,
            base_anchors=face_anchors,
            mark_cps=mark_cps,
            mark_names=face_marks,
            glyph_order=glyph_order,
            glyphs=glyphs,
        )
        install_dakuten_mark_chain_gpos(
            fb.font,
            mark_cps=mark_cps,
            glyphs=glyphs,
            glyph_order=glyph_order,
            target_upem=target_upem,
        )

    os.makedirs(out_dir, exist_ok=True)
    setup_head_timestamps(fb)
    fb.save(out_path)
    return face_id, variant, n_glyphs - 1, sorted(cmap.keys())


_WORKER_CACHE: Optional[dict] = None


def _init_yi_worker(cache_path: str) -> None:
    global _WORKER_CACHE
    with open(cache_path, "rb") as f:
        _WORKER_CACHE = pickle.load(f)


def _yi_standalone_task(
    payload: Tuple[int, object, int, float, float, float],
) -> Tuple[int, Optional[Tuple]]:
    idx, rec, target_upem, source_advance, source_center_y, source_max_height = payload
    sa = make_standalone_glyph(
        rec,
        target_upem,
        source_advance=source_advance,
        source_center_y=source_center_y,
        source_max_height=source_max_height,
        widen=0.0,
        horizontal_weight=YI_HORIZONTAL_STEM_WEIGHT,
    )
    return idx, sa


def _yi_face_task(
    spec: Tuple[str, Optional[int]],
) -> Tuple[str, str, int, List[int], str]:
    """Process-pool worker: subset + slices + TTF for one Yi face."""
    assert _WORKER_CACHE is not None
    m = _WORKER_CACHE
    kind, bucket_id = spec
    glyph_order = m["glyph_order"]
    glyphs = m["glyphs"]
    metrics = m["metrics"]
    cmap = m["cmap"]
    if kind == "h":
        assert bucket_id is not None
        bases = [n for n in m["yi_names"] if (m["yi_cps"][n] >> 8) == bucket_id]
        keep: Set[str] = {".notdef", *m["dakuten_keep"], *m["vs_keep"]}
        for base in bases:
            keep.update(orientation_form_names(base, modes=YI_ORIENTATION_MODES))
        go, gl, mt, cm = subset_glyph_tables(
            glyph_order, glyphs, metrics, cmap, keep
        )
        print(
            f"  Installing FE08–FE0F slices on {h_bucket_face_id(bucket_id)} "
            f"({len(bases)} Yi CPs)...",
            flush=True,
        )
        add_slice_halves(
            bases,
            glyph_order=go,
            glyphs=gl,
            metrics=mt,
            target_upem=m["target_upem"],
            modes=YI_ORIENTATION_MODES,
        )
        inject_slice_marks(go, gl, mt, cm)
        base_cps = {m["yi_cps"][n] for n in bases}
        face_uvs = [
            row for row in m["uvs_rows"] if row[0] in base_cps and row[2] in gl
        ]
        face_id = h_bucket_face_id(bucket_id)
        variant = "h"
        slices = True
        yi_names = bases
    else:
        go, gl, mt, cm = subset_glyph_tables(
            glyph_order, glyphs, metrics, cmap, set(glyph_order)
        )
        face_uvs = list(m["uvs_rows"])
        face_id = PS_NAME
        variant = ""
        slices = False
        yi_names = m["yi_names"]
    meta = _save_yi_face(
        face_id=face_id,
        variant=variant,
        glyph_order=go,
        glyphs=gl,
        metrics=mt,
        cmap=cm,
        uvs_rows=face_uvs,
        yi_names=yi_names,
        mark_names=m["mark_names"],
        mark_cps=m["mark_cps"],
        base_anchors=m["base_anchors"],
        out_dir=m["out_dir"],
        target_upem=m["target_upem"],
        slices=slices,
    )
    return (*meta, os.path.join(m["out_dir"], f"{meta[0]}.ttf"))


def build_edenia_yi_font(
    inv: YiInventory,
    out_dir: str,
    target_upem: int,
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
    hint: bool = True,
    variants: Sequence[str] = ("", "h"),
    jobs: int = 1,
) -> List[Tuple[str, str, int, List[int]]]:
    """Build `edenia yi` and/or pigeonholed `edenia yi h` slice faces."""
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    want = {v for v in variants}

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

    workers = max(1, jobs)
    sx = target_upem / float(inv.source_advance)
    sy = target_upem / float(inv.source_max_height)
    print(
        f"Stage 1/4: scaling {len(recs)} standalones "
        f"(sx {inv.source_advance}→{target_upem} = {sx:.4g}×, "
        f"sy maxH {inv.source_max_height:.0f}→{target_upem} = {sy:.4g}×, "
        f"horizontal stems ×{YI_HORIZONTAL_STEM_WEIGHT:g}, "
        f"cell {STANDALONE_CELL_SCALE:g}, vert pad {STANDALONE_VERT_PAD:g}, "
        f"{workers} workers)...",
        flush=True,
    )
    payloads = [
        (
            idx,
            rec,
            target_upem,
            inv.source_advance,
            inv.source_center_y,
            inv.source_max_height,
        )
        for idx, rec in recs.items()
    ]
    standalones: Dict[int, Tuple] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for idx, sa in pool.map(_yi_standalone_task, payloads):
            if sa is not None:
                standalones[idx] = sa

    print(
        "  Orientations: transform id+r90 (no stem-normalize); "
        "other D4 = composites",
        flush=True,
    )

    glyph_order = [".notdef"]
    glyphs = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}
    yi_names: List[str] = []
    yi_cps: Dict[str, int] = {}
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
        yi_cps[sa_name] = cp
        add_d4_variant_glyphs(
            sa_name,
            advance=sa_adv,
            lsb=sa_lsb,
            target_upem=target_upem,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            modes=YI_ORIENTATION_MODES,
            anchor="cell",
        )
        uvs_rows.extend(
            build_d4_uvs_entries(cp, sa_name, glyphs=glyphs, modes=YI_ORIENTATION_MODES)
        )

    if not yi_names:
        return []

    mark_names: List[str] = []
    mark_cps: List[int] = []
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    try:
        mark_fonts = resolve_dakuten_mark_font_stack(os.path.dirname(inv.source_path))
        print(
            f"  Loading dakuten marks from "
            f"{dakuten_mark_stack_label(mark_fonts)}...",
            flush=True,
        )
        mark_cps, mark_glyphs = load_dakuten_marks_from_stack(mark_fonts, target_upem)
        mark_names = add_dakuten_mark_glyphs(
            mark_cps,
            mark_glyphs,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
        )
        chain_names = add_dakuten_chain_mark_glyphs(
            mark_cps,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
        mark_names = list(mark_names) + chain_names
        dakuten_bases = yi_forms_for_dakuten(yi_names, modes=YI_ORIENTATION_MODES)
        anchor_bases = list(dakuten_bases)
        base_anchors = collect_dakuten_base_anchors(
            anchor_bases,
            glyphs=glyphs,
            target_upem=target_upem,
        )
        n_unique = len(mark_cps)
        print(
            f"  Dakuten: {n_unique} marks × {DAKUTEN_SLOT_COUNT} slots, "
            f"{len(base_anchors)} bases "
            f"({DAKUTEN_SLOT_CYCLE}; mark-to-mark chain; fixed H, L/R/mid align)",
            flush=True,
        )
    except FileNotFoundError as exc:
        print(f"  Skipping dakuten marks: {exc}", flush=True)

    _inject_d4_vs(glyph_order, glyphs, metrics, cmap)

    built: List[Tuple[str, str, int, List[int]]] = []
    os.makedirs(out_dir, exist_ok=True)
    face_specs: List[Tuple[str, Optional[int]]] = []
    if "" in want:
        face_specs.append(("", None))
    dakuten_keep = _dakuten_keep_names(glyph_order, mark_names)
    vs_keep = {n for n in glyph_order if n.startswith("vs")}
    if "h" in want:
        buckets: Dict[int, List[str]] = {}
        for name in yi_names:
            cp = yi_cps[name]
            buckets.setdefault(cp >> 8, []).append(name)
        for bucket_id in sorted(buckets):
            face_specs.append(("h", bucket_id))
    if not face_specs:
        return built

    cache_dir = tempfile.mkdtemp(prefix="edenia-yi-")
    try:
        cache_path = os.path.join(cache_dir, "master.pkl")
        with open(cache_path, "wb") as f:
            pickle.dump(
                {
                    "glyph_order": glyph_order,
                    "glyphs": glyphs,
                    "metrics": metrics,
                    "cmap": cmap,
                    "uvs_rows": uvs_rows,
                    "yi_names": yi_names,
                    "yi_cps": yi_cps,
                    "mark_names": mark_names,
                    "mark_cps": mark_cps,
                    "base_anchors": base_anchors,
                    "dakuten_keep": dakuten_keep,
                    "vs_keep": vs_keep,
                    "out_dir": out_dir,
                    "target_upem": target_upem,
                },
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        t0 = time.perf_counter()
        pool_workers = min(workers, max(1, len(face_specs)))
        print(
            f"Stage 2/4: face TTFs ({len(face_specs)} jobs, {pool_workers} workers)...",
            flush=True,
        )
        with ProcessPoolExecutor(
            max_workers=pool_workers,
            initializer=_init_yi_worker,
            initargs=(cache_path,),
        ) as executor:
            results = list(executor.map(_yi_face_task, face_specs))
            print(
                f"  stage 2 done in {time.perf_counter() - t0:.1f}s",
                flush=True,
            )
            ttf_paths = [r[4] for r in results]
            built = [(r[0], r[1], r[2], r[3]) for r in results]
            finish_font_outputs(
                ttf_paths,
                hint=hint,
                write_woff2=write_woff2,
                write_ttf=write_ttf,
                executor=executor,
            )
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)
    return built


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


YI_BASE_FE = set(range(0xFE01, 0xFE08))
YI_H_FE = set(range(0xFE00, 0xFE10))
YI_PUA_SELECTORS = set(range(0xE000, 0xE011))


def _css_cps_for_yi_face(
    codepoints: Sequence[int], variant: str, *, mark_cps: Sequence[int]
) -> List[int]:
    fe = YI_H_FE if variant == "h" else YI_BASE_FE
    cps = {
        cp
        for cp in codepoints
        if cp not in YI_PUA_SELECTORS and not (0xFE00 <= cp <= 0xFE0F)
    }
    cps |= fe
    cps |= set(mark_cps)
    return sorted(cps)


def write_css(
    out_dir: str, built: Sequence[Tuple[str, str, int, List[int]]]
) -> None:
    """Write edenia-yi.css: `h` pigeonholes then the base face."""
    css_path = os.path.join(out_dir, CSS_YI)
    mark_cps: set[int] = set()
    for face_id, _variant, _n, _cps in built:
        for stem_name in (f"{face_id}.woff2", f"{face_id}.ttf"):
            font_path = os.path.join(out_dir, stem_name)
            if not os.path.isfile(font_path):
                continue
            try:
                from shared_diacritics import combining_mark_codepoints_from_font

                mark_cps |= set(combining_mark_codepoints_from_font(font_path))
            except Exception as exc:
                print(f"  [!] yi mark unicode-range ({face_id}): {exc}", flush=True)
            break
        if mark_cps:
            break

    def _face_sort(item: Tuple[str, str, int, List[int]]) -> Tuple[int, int, str]:
        face_id, variant, _n, _cps = item
        if variant == "h":
            bid = parse_h_bucket_face_id(face_id)
            return (0, bid if bid is not None else 0, face_id)
        return (1, 0, face_id)

    lines: List[str] = [
        "/* Auto-generated Edenia Yi: 'edenia yi h' (slices, pigeonholed)",
        "   then 'edenia yi' (D4 + dakuten). Pin h for FE00/FE08–F GSUB. */",
        "",
    ]

    def _emit(family: str, face_id: str, unicode_range: str) -> None:
        lines.append("@font-face {")
        lines.append(f"  font-family: '{family}';")
        lines.append(
            format_src_line(
                dist_rel("yi", f"{face_id}.woff2"),
                fmt="woff2",
                local=(
                    (f"./{face_id}.woff2", "woff2"),
                    (f"./{face_id}.ttf", "truetype"),
                ),
                indent="  ",
            )
        )
        if unicode_range:
            lines.append(f"  unicode-range: {unicode_range};")
        lines.extend(
            [
                "  font-weight: normal;",
                "  font-style: normal;",
                "  font-display: swap;",
                "}",
                "",
            ]
        )

    for face_id, variant, _n, codepoints in sorted(built, key=_face_sort):
        ur = unicode_range_css(
            _css_cps_for_yi_face(codepoints, variant, mark_cps=sorted(mark_cps))
        )
        _emit(family_yi_variant(variant), face_id, ur)

    with open(css_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {css_path}")

    has_h = any(v == "h" for _fid, v, _n, _cps in built)
    has_base = any(v == "" for _fid, v, _n, _cps in built)
    stack_parts: List[str] = []
    if has_h:
        stack_parts.append(f"'{family_yi_variant('h')}'")
    if has_base:
        stack_parts.append(f"'{family_yi_variant('')}'")
    stack = ", ".join(stack_parts) or f"'{FAMILY_NAME}'"
    fontlist_path = os.path.join(out_dir, f"{PS_NAME}-fontlist.css")
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(
            "/* Yi font families (h = slices; base = D4 + dakuten) */\n"
            f":root {{\n  --font-edenia-yi: {stack};\n}}\n"
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
    hint: bool = True,
    variants: Sequence[str] = ("", "h"),
    jobs: int = 1,
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
        "  Orientations: FE01..FE07 "
        "(bare = identity; FE00 = overlay on h; BMP PUA = kana)"
    )
    print("  Slice (h face): U+FE08–FE0B halves, U+FE0C–FE0F triangles")
    print(
        "  Dakuten: LXGWNeoXiHeiScreenFull + mkanaplus + Nexsevka + JuliaMono + "
        "Constructium + Droid Sans + Arial Unicode MS + Gentium \\p{M} @ CJK corners "
        f"({DAKUTEN_SLOT_CYCLE}; CGJ skips a slot; fixed H, L/R/mid align; "
        "all D4 incl. r90my)"
    )
    print(
        f"  Output: '{FAMILY_NAME}'"
        + (" + pigeonholed 'edenia yi h'" if "h" in variants else "")
        + (" --base-only" if variants == ("",) or list(variants) == [""] else "")
    )
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"  Formats: {fmt_note}")
    print(f"  Jobs: {max(1, jobs)}")

    os.makedirs(out_dir, exist_ok=True)
    built = build_edenia_yi_font(
        inv,
        out_dir,
        target_upem,
        write_ttf=write_ttf,
        write_woff2=write_woff2,
        hint=hint,
        variants=variants,
        jobs=jobs,
    )
    if built:
        write_css(out_dir, built)
    for face_id, variant, count, _cps in built:
        print(
            f"  {family_yi_variant(variant)} / {face_id}: {count} glyphs",
            flush=True,
        )
    print(f"\nDone: {len(built)} Yi face(s), jobs={max(1, jobs)}", flush=True)
    if os.path.normcase(os.path.abspath(out_dir)) == os.path.normcase(
        os.path.abspath(OUT_DIR)
    ):
        sync_dist_to_plugin("yi", out_dir)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build edenia yi (D4 + dakuten) and edenia yi h "
            "(pigeonholed FE00/FE08–F slices)"
        )
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
    p.add_argument(
        "--base-only",
        action="store_true",
        help="Build only the identity/D4 face (skip slice h pigeonholes)",
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
    add_no_hint_argument(p)
    add_jobs_argument(p)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    variants: Tuple[str, ...] = ("",) if args.base_only else ("", "h")
    build_all(
        args.in_dir,
        args.out_dir,
        args.upem,
        limit=args.limit,
        write_ttf=not args.woff2_only,
        write_woff2=not args.ttf_only,
        hint=not args.no_hint,
        variants=variants,
        jobs=max(1, args.jobs),
    )
