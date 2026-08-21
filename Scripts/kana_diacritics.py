"""Kana dakuten placement: eight unique slots around each transformed glyph.

Unlike Yi/Hangul (fixed ideographic-cell corners), kana marks sit just
outside the **actual ink** of that form — after D4 rotate/reflect, and
for full / small / halfwidth-large / halfwidth-small alike. Coordinate
ligatures (FE00 overlay ``.ov``, FE08–FE0F slices, and ``.ov`` of slices)
are measured from **their** ink, not copied from the identity — including
when the stacked partners come from different source fonts.

Slots (same GSUB cycle as ``shared_diacritics.DAKUTEN_SLOTS``)::

    TR  CR  BR  TM  BM  TL  CL  BL
    NE  E   SE  N   S   NW  W   SW

Each slot is the support of the outline in that direction, then offset
outward by a gap plus the mark's half-extent so the mark body does not
overlap the kana. Centers are then pushed further until neighboring marks
also clear each other. Mark GPOS pins the mark **center** (glyphs are already
centered at origin). Marks stay full size on small / halfwidth forms too.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from shared_diacritics import (
    DAKUTEN_MARK_HEIGHT_FRAC,
    DAKUTEN_SLOTS,
)
from shared_half_cells import (
    SLICE_SUFFIXES,
    YI_ORIENTATION_MODES,
    orientation_form_names,
    overlay_glyph_name,
    slice_form_name,
)

# Compass unit vectors for the eight slots (pre-normalized diagonals).
_INV_SQRT2 = 1.0 / math.sqrt(2.0)
KANA_SLOT_DIRS: Dict[str, Tuple[float, float]] = {
    "tr": (_INV_SQRT2, _INV_SQRT2),
    "cr": (1.0, 0.0),
    "br": (_INV_SQRT2, -_INV_SQRT2),
    "tm": (0.0, 1.0),
    "bm": (0.0, -1.0),
    "tl": (-_INV_SQRT2, _INV_SQRT2),
    "cl": (-1.0, 0.0),
    "bl": (-_INV_SQRT2, -_INV_SQRT2),
}

# Air gap between kana ink and the mark body, as a fraction of mark height.
KANA_MARK_GAP_FRAC = 0.28
# Minimum center-to-center separation between marks (fraction of mark height)
# so adjacent slot footprints do not overlap.
KANA_MARK_SEP_FRAC = 1.08


def kana_d4_form_names(bases: Sequence[str]) -> List[str]:
    """Identity + all D4 orientation names for each base."""
    names: List[str] = []
    for base in bases:
        names.extend(orientation_form_names(base, modes=YI_ORIENTATION_MODES))
    return names


def kana_coord_liga_names(
    bases: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
) -> List[str]:
    """D4 forms plus overlay / slice / slice-overlay ligature glyphs that exist.

    Overlay and slice outlines differ from the identity (and from each other),
    so dakuten slots must be recomputed on these names — not inherited.
    """
    names: List[str] = []
    seen: set[str] = set()
    for form in kana_d4_form_names(bases):
        candidates = [form, overlay_glyph_name(form)]
        for suf in SLICE_SUFFIXES:
            sl = slice_form_name(form, suf)
            candidates.append(sl)
            candidates.append(overlay_glyph_name(sl))
        for name in candidates:
            if name in glyphs and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def kana_mark_center_anchor(
    glyph: TTGlyph,
    slot: str,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[int, int]:
    """Pin the mark center (dakuten glyphs are normalized about origin)."""
    del glyph, slot, glyph_set
    return 0, 0


def _iter_simple_points(glyph: TTGlyph) -> Iterable[Tuple[float, float]]:
    try:
        for x, y in glyph.coordinates:
            yield float(x), float(y)
    except Exception:
        return


def _iter_ink_points(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
    *,
    dx: float = 0.0,
    dy: float = 0.0,
    _seen: Optional[set] = None,
) -> Iterable[Tuple[float, float]]:
    """On- and off-curve points, composites expanded (translate only)."""
    seen = _seen if _seen is not None else set()
    if glyph.isComposite():
        try:
            comps = list(glyph.components)
        except Exception:
            return
        for comp in comps:
            name = getattr(comp, "glyphName", None)
            if not name or name in seen:
                continue
            child = glyph_set.get(name)
            if child is None:
                continue
            seen.add(name)
            cx = dx + float(getattr(comp, "x", 0) or 0)
            cy = dy + float(getattr(comp, "y", 0) or 0)
            yield from _iter_ink_points(
                child, glyph_set, dx=cx, dy=cy, _seen=seen
            )
        return
    for x, y in _iter_simple_points(glyph):
        yield x + dx, y + dy


def _support_point(
    points: Sequence[Tuple[float, float]],
    ux: float,
    uy: float,
    *,
    tol: float = 2.0,
) -> Tuple[float, float]:
    """Farthest ink in direction ``u``; average a flat supporting edge."""
    best_dot = points[0][0] * ux + points[0][1] * uy
    for x, y in points[1:]:
        d = x * ux + y * uy
        if d > best_dot:
            best_dot = d
    xs: List[float] = []
    ys: List[float] = []
    for x, y in points:
        if x * ux + y * uy >= best_dot - tol:
            xs.append(x)
            ys.append(y)
    return sum(xs) / len(xs), sum(ys) / len(ys)


def kana_slot_anchors(
    glyph: TTGlyph,
    *,
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_scale: float = 1.0,
) -> Optional[Dict[str, Tuple[int, int]]]:
    """Eight unique coordinates just outside this glyph's ink.

    Marks are always full-size (``mark_scale`` reserved for callers that still
    pass it; kana builds use ``1.0``). Each center is pushed outward until its
    footprint clears the kana and every previously placed mark.
    """
    try:
        if glyph.isComposite():
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
    except Exception:
        pass
    points = list(_iter_ink_points(glyph, glyph_set))
    if len(points) < 2:
        try:
            x0, y0 = float(glyph.xMin), float(glyph.yMin)
            x1, y1 = float(glyph.xMax), float(glyph.yMax)
        except Exception:
            return None
        if x1 <= x0 or y1 <= y0:
            return None
        points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    mark_h = float(target_upem) * DAKUTEN_MARK_HEIGHT_FRAC * float(mark_scale)
    half = mark_h * 0.5
    gap = mark_h * KANA_MARK_GAP_FRAC
    min_sep = mark_h * KANA_MARK_SEP_FRAC
    min_sep_sq = min_sep * min_sep
    step = max(1.0, mark_h * 0.05)

    out: Dict[str, Tuple[int, int]] = {}
    used: set[Tuple[int, int]] = set()
    placed: List[Tuple[float, float]] = []
    for slot, _suf in DAKUTEN_SLOTS:
        ux, uy = KANA_SLOT_DIRS[slot]
        sx, sy = _support_point(points, ux, uy)
        # Axis-aligned mark box extent along ``u``.
        extent = half * (abs(ux) + abs(uy))
        dist = gap + extent
        cx = cy = 0.0
        ax = ay = 0
        for _ in range(512):
            cx, cy = sx + ux * dist, sy + uy * dist
            ax, ay = otRound(cx), otRound(cy)
            if (ax, ay) in used:
                dist += step
                continue
            conflict = False
            for px, py in placed:
                dx, dy = cx - px, cy - py
                if dx * dx + dy * dy < min_sep_sq:
                    conflict = True
                    break
            if not conflict:
                break
            dist += step
        used.add((ax, ay))
        placed.append((cx, cy))
        out[slot] = (ax, ay)
    return out


def collect_kana_dakuten_anchors(
    base_names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_scale: float = 1.0,
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    """Per-glyph ``{mark_class: (x, y)}`` for every named form that exists."""
    anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for name in base_names:
        g = glyphs.get(name)
        if g is None:
            continue
        slots = kana_slot_anchors(
            g,
            glyph_set=glyph_set,
            target_upem=target_upem,
            mark_scale=mark_scale,
        )
        if not slots:
            continue
        anchors[name] = {
            i: slots[slot] for i, (slot, _suf) in enumerate(DAKUTEN_SLOTS)
        }
    return anchors
