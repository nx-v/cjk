"""Pigeonholed third / quarter segment faces (shared by kana, yi, cjk builders)."""

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Sequence, Set, Tuple

from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from cjk_diacritics import MARK_CPS, SQUISH_VS_SLOTS
from shared_half_cells import OV_SELECTOR_CP, TRANSFORM_MODES, variant_glyph_name
from shared_quarter_cells import (
    GRID_VS_SLOTS,
    QUARTER_FACE_GRID,
    QUARTER_FACE_H,
    QUARTER_FACE_V,
    quarter_form_name,
    quarter_slot_parts,
    quarter_slots_for_face,
)
from shared_third_cells import THIRD_VS_SLOTS

_D4_SUFFIXES: Tuple[str, ...] = tuple(
    suf for _vs, _r, _fx, _fy, suf in TRANSFORM_MODES if suf is not None
)
_HALF_SUFFIXES: Tuple[str, ...] = tuple(suf for _cp, _sel, suf in SQUISH_VS_SLOTS)
_THIRD_SUFFIXES: Tuple[str, ...] = tuple(
    suf for _cp, _sel, suf, _a, _b0, _b1 in THIRD_VS_SLOTS
)

SEGMENT_BUCKET_SUFFIXES: Tuple[str, ...] = ("qh", "qv", "q", "h", "t")


def oriented_forms(bases: Sequence[str], glyphs: Dict[str, TTGlyph]) -> List[str]:
    """Identity + D4 orientation names present in `glyphs`."""
    forms: List[str] = []
    seen: set[str] = set()
    for base in bases:
        if base not in glyphs or base in seen:
            continue
        forms.append(base)
        seen.add(base)
        for suf in _D4_SUFFIXES:
            name = variant_glyph_name(base, suf)
            if name in glyphs and name not in seen:
                forms.append(name)
                seen.add(name)
    return forms


def close_component_names(keep: Set[str], glyphs: Dict[str, TTGlyph]) -> Set[str]:
    stack = list(keep)
    out: Set[str] = set(keep)
    while stack:
        name = stack.pop()
        glyph = glyphs.get(name)
        if glyph is None:
            continue
        try:
            if not glyph.isComposite():
                continue
            comps = glyph.components
        except Exception:
            continue
        for comp in comps:
            child = getattr(comp, "glyphName", None)
            if child and child not in out:
                out.add(child)
                stack.append(child)
    return out


def _add_stem_family(keep: Set[str], stem: str, glyphs: Dict[str, TTGlyph]) -> None:
    if stem in glyphs:
        keep.add(stem)
    ov = f"{stem}.ov"
    if ov in glyphs:
        keep.add(ov)


def keep_names_for_segment_face(
    variant: str,
    bases: Sequence[str],
    glyphs: Dict[str, TTGlyph],
) -> Set[str]:
    """Glyph closure for one pigeonholed segment face (`h` / `t` / `q` / `qv` / `qh`)."""
    keep: Set[str] = {".notdef"}
    for name in glyphs:
        if name.startswith("vs"):
            keep.add(name)
    for base in bases:
        _add_stem_family(keep, base, glyphs)
        if not variant:
            continue
        oriented = [base] + [
            variant_glyph_name(base, suf)
            for suf in _D4_SUFFIXES
            if variant_glyph_name(base, suf) in glyphs
        ]
        for stem in oriented:
            _add_stem_family(keep, stem, glyphs)
            if variant in ("h", "q"):
                for hs in _HALF_SUFFIXES:
                    _add_stem_family(keep, f"{stem}.{hs}", glyphs)
            if variant == "t":
                for ts in _THIRD_SUFFIXES:
                    _add_stem_family(keep, f"{stem}.{ts}", glyphs)
            if variant == "q":
                for gs in (s[2] for s in GRID_VS_SLOTS):
                    _add_stem_family(keep, quarter_form_name(stem, gs), glyphs)
            if variant in ("qv", "qh"):
                for slot in quarter_slots_for_face(variant):
                    suf = quarter_slot_parts(slot)[2]
                    _add_stem_family(
                        keep,
                        quarter_form_name(stem, suf, face=variant),
                        glyphs,
                    )
    return close_component_names(keep, glyphs)


def subset_tables(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    keep: Set[str],
    *,
    copy_glyphs: bool = True,
) -> Tuple[List[str], Dict[str, TTGlyph], Dict[str, Tuple[int, int]], Dict[int, str]]:
    keep = close_component_names(keep, glyphs)
    order = [n for n in glyph_order if n in keep]
    for name in keep:
        if name not in order and name in glyphs:
            order.append(name)
    if copy_glyphs:
        out_glyphs = {n: copy.deepcopy(glyphs[n]) for n in order if n in glyphs}
    else:
        out_glyphs = {n: glyphs[n] for n in order if n in glyphs}
    out_metrics = {n: metrics[n] for n in order if n in metrics}
    out_cmap = {cp: name for cp, name in cmap.items() if name in out_glyphs}
    return order, out_glyphs, out_metrics, out_cmap


def _cmap_name_in_base_families(name: str, bases: Sequence[str]) -> bool:
    """True for identity base or any dotted form (D4 / slice / overlay / small)."""
    for base in bases:
        if name == base or name.startswith(base + "."):
            return True
    return False


def filter_segment_face_cmap(
    variant: str,
    cmap: Dict[int, str],
    bases: Sequence[str],
    *,
    mark_cps: Optional[Sequence[int]] = None,
) -> Dict[int, str]:
    """Drop other faces' VS pages from a shared master cmap.

    Keeps every codepoint whose glyph belongs to a base family (identity **and**
    D4 / small / slice / ``.ov`` forms). Stripping oriented PUA broke kana
    overlays: Blink picked the base face for ``U+E002`` and the ``h`` face for
    ``FE00``/``FE08``, so GSUB could not ligate across fonts.

    ``h`` also keeps ``mark_cps`` when provided: last-slice dakuten sit on the
    second digraph member, which may be a different pigeonhole file.
    """
    vs_page = {
        "t": set(range(0xE0100, 0xE010A)),
        "qv": set(range(0xE010A, 0xE0111)),
        "qh": set(range(0xE0111, 0xE0118)),
        "q": set(range(0xE0118, 0xE0120)),
    }.get(variant, set())
    fe_ok = {OV_SELECTOR_CP}
    if variant in ("h", "t", "q", "qv", "qh"):
        fe_ok |= set(range(0xFE01, 0xFE08))
    if variant in ("h", "q"):
        fe_ok |= set(range(0xFE08, 0xFE10))
    elif variant == "qv":
        fe_ok |= {0xFE08, 0xFE09}
    elif variant == "qh":
        fe_ok |= {0xFE0A, 0xFE0B}
    if variant == "":
        fe_ok |= set(range(0xFE00, 0xFE10))
        if mark_cps:
            fe_ok |= set(mark_cps)
        else:
            fe_ok |= set(MARK_CPS)
    elif variant == "h" and mark_cps:
        # Last-slice marks attach to the second digraph member, which may live
        # on another `h` pigeonhole. That file must cmap the marks (GPOS is
        # already copied via dakuten_keep); CSS unicode-range follows cmap.
        fe_ok |= set(mark_cps)
    out: Dict[int, str] = {}
    for cp, name in cmap.items():
        if _cmap_name_in_base_families(name, bases) or cp in fe_ok or cp in vs_page:
            out[cp] = name
    return out


def install_segment_face_gsub(
    font,
    *,
    variant: str,
    bases: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    glyph_order: List[str],
    slice_gsub_fn,
    slice_forms: Sequence[str],
) -> None:
    """Third / quarter GSUB for one segment face (`t` / `q` / `qv` / `qh`)."""
    from shared_quarter_cells import install_quarter_cell_gsub
    from shared_third_cells import install_third_cell_gsub

    oriented = oriented_forms(bases, glyphs)
    match variant:
        case "t":
            install_third_cell_gsub(font, bases=oriented, glyphs=glyphs)
        case "q":
            if slice_forms:
                slice_gsub_fn(
                    font,
                    list(slice_forms),
                    glyphs=glyphs,
                    glyph_order=glyph_order,
                )
            install_quarter_cell_gsub(
                font,
                face=QUARTER_FACE_GRID,
                bases=oriented,
                glyphs=glyphs,
            )
        case "qv":
            install_quarter_cell_gsub(
                font,
                face=QUARTER_FACE_V,
                bases=oriented,
                glyphs=glyphs,
            )
        case "qh":
            install_quarter_cell_gsub(
                font,
                face=QUARTER_FACE_H,
                bases=oriented,
                glyphs=glyphs,
            )
