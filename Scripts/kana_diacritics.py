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

The eight directions are rotated ``KANA_SLOT_ROTATION_DEG`` clockwise as a
ring. Each slot is the contour support of the base outline in that direction,
offset outward by a small gap plus the representative dakuten mark's own
contour extent along that axis (not bbox / stack max height).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from shared_diacritics import (
    DAKUTEN_MARK_HEIGHT_FRAC,
    DAKUTEN_SLOTS,
    CGJ_CP,
)
from shared_half_cells import (
    SLICE_SUFFIXES,
    YI_ORIENTATION_MODES,
    _bake_transformed_glyph,
    orientation_form_names,
    overlay_glyph_name,
    slice_form_name,
)
from fontTools.misc.transform import Transform

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
# Clockwise rotation applied to the whole eight-slot ring (degrees).
KANA_SLOT_ROTATION_DEG = 15.0

# Air gap between kana ink and the nearest mark contour (fraction of dakuten H).
KANA_MARK_GAP_FRAC = 0.06
# Minimum center-to-center separation between marks (fraction of dakuten H).
KANA_MARK_SEP_FRAC = 1.05

# Primary marks for contour-based gap / extent (Japanese voicing).
_KANA_DAKUTEN_MARK_CPS: Tuple[int, ...] = (
    0x3099,
    0x309A,
    0x309B,
    0x309C,
    0xFF9E,
    0xFF9F,
)


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


def _mark_contour_points(glyph: TTGlyph) -> List[Tuple[float, float]]:
    """On- and off-curve coordinates for a baked mark (centered at origin)."""
    try:
        glyph.recalcBounds(None)
        coords = glyph.coordinates
        if coords is None or len(coords) == 0:
            return []
        return [(float(x), float(y)) for x, y in coords]
    except Exception:
        return []


def kana_representative_mark_points(
    mark_glyphs: Dict[int, TTGlyph],
) -> List[Tuple[float, float]]:
    """Contour of a typical voicing mark (3099/309A, …) for slot gap math."""
    for cp in _KANA_DAKUTEN_MARK_CPS:
        g = mark_glyphs.get(cp)
        if g is None:
            continue
        pts = _mark_contour_points(g)
        if len(pts) >= 2:
            return pts
    best: List[Tuple[float, float]] = []
    best_h = 1e9
    for cp, g in mark_glyphs.items():
        if cp == CGJ_CP:
            continue
        pts = _mark_contour_points(g)
        if len(pts) < 2:
            continue
        ys = [y for _x, y in pts]
        h = max(ys) - min(ys)
        if 0 < h < best_h:
            best_h = h
            best = pts
    return best


def _contour_ink_points(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
) -> List[Tuple[float, float]]:
    """Outline coordinates only — composites baked with full transforms."""
    src = glyph
    if glyph.isComposite():
        try:
            src, _, _ = _bake_transformed_glyph(
                glyph, Transform(), 0, glyph_set=glyph_set
            )
        except Exception:
            return []
    try:
        coords = src.coordinates
        if coords is None or len(coords) == 0:
            return []
        return [(float(x), float(y)) for x, y in coords]
    except Exception:
        return []


def _mark_radius_along(
    mark_points: Sequence[Tuple[float, float]],
    ux: float,
    uy: float,
) -> float:
    """Outer contour radius of a centered mark along ``u``."""
    if not mark_points:
        return 0.0
    return max(x * ux + y * uy for x, y in mark_points)


def _mark_height(mark_points: Sequence[Tuple[float, float]]) -> float:
    if not mark_points:
        return 0.0
    ys = [y for _x, y in mark_points]
    return max(ys) - min(ys)


def _slot_dir(slot: str) -> Tuple[float, float]:
    """Unit vector for ``slot``, rotated ``KANA_SLOT_ROTATION_DEG`` clockwise."""
    ux, uy = KANA_SLOT_DIRS[slot]
    deg = KANA_SLOT_ROTATION_DEG
    if deg == 0.0:
        return ux, uy
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return ux * c + uy * s, -ux * s + uy * c


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
    mark_points: Optional[Sequence[Tuple[float, float]]] = None,
    mark_ink_height: Optional[float] = None,
    mark_scale: float = 1.0,
) -> Optional[Dict[str, Tuple[int, int]]]:
    """Eight unique coordinates just outside this glyph's ink contours.

    Base support uses baked outline points only (no bbox / advance box).
    Mark offset uses the representative dakuten **contour** along each slot
    direction, not a global max mark height from the full stack.
    """
    del mark_scale
    points = _contour_ink_points(glyph, glyph_set)
    if len(points) < 2:
        return None

    mpts = list(mark_points) if mark_points else []
    mark_h = _mark_height(mpts)
    if mark_h <= 0:
        mark_h = float(
            mark_ink_height
            if mark_ink_height is not None and mark_ink_height > 0
            else target_upem * DAKUTEN_MARK_HEIGHT_FRAC
        )
    gap = mark_h * KANA_MARK_GAP_FRAC
    min_sep = mark_h * KANA_MARK_SEP_FRAC
    min_sep_sq = min_sep * min_sep
    step = max(1.0, mark_h * 0.04)

    out: Dict[str, Tuple[int, int]] = {}
    used: set[Tuple[int, int]] = set()
    placed: List[Tuple[float, float]] = []
    for slot, _suf in DAKUTEN_SLOTS:
        ux, uy = _slot_dir(slot)
        sx, sy = _support_point(points, ux, uy)
        mark_r = _mark_radius_along(mpts, ux, uy) if mpts else mark_h * 0.5
        dist = gap + mark_r
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
    mark_points: Optional[Sequence[Tuple[float, float]]] = None,
    mark_ink_height: Optional[float] = None,
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
            mark_points=mark_points,
            mark_ink_height=mark_ink_height,
            mark_scale=mark_scale,
        )
        if not slots:
            continue
        anchors[name] = {i: slots[slot] for i, (slot, _suf) in enumerate(DAKUTEN_SLOTS)}
    return anchors
