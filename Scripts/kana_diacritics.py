"""Kana dakuten placement: eight unique slots hugging each glyph contour.

Marks sit just outside the baked outline (quadratic segments flattened), using
outward normals at convex and concave vertices. Slots fill in GSUB order
(TR→CR→BR→TM→BM→TL→CL→BL) by walking clockwise from the top-right contour
region until eight non-overlapping positions are found. Marks 9+ use GPOS
mark-to-mark (``.mk.ch``) chained off the previous mark, not the base slots.

Coordinate ligatures (FE00 overlay ``.ov``, FE08–FE0F slices, and ``.ov`` of
slices) are measured from **their** ink, not copied from the identity.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.basePen import BasePen
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

# Air gap between kana ink and the nearest mark contour (fraction of dakuten H).
KANA_MARK_GAP_FRAC = 0.08
# Minimum center-to-center separation between marks (fraction of dakuten H).
KANA_MARK_SEP_FRAC = 1.05
# Arc-length step when searching for the next contour slot.
KANA_CONTOUR_ARC_STEP_FRAC = 0.04
# Polyline flattening step along the outline (fraction of dakuten H).
KANA_CONTOUR_SAMPLE_FRAC = 0.12

_INV_SQRT2 = 1.0 / math.sqrt(2.0)
_TR_DIR = (_INV_SQRT2, _INV_SQRT2)
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
    """D4 forms plus overlay / slice / slice-overlay ligature glyphs that exist."""
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


def kana_mark_chain_parent_anchor(
    glyph: TTGlyph,
    parent_slot: str,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    mark_height: Optional[float] = None,
    target_upem: int = 1000,
) -> Tuple[int, int]:
    """Mark2 anchor for 9th+ marks: offset along the slot cycle, not stacked."""
    del glyph, glyph_set
    idx = next(i for i, (slot, _suf) in enumerate(DAKUTEN_SLOTS) if slot == parent_slot)
    next_slot = DAKUTEN_SLOTS[(idx + 1) % len(DAKUTEN_SLOTS)][0]
    ux, uy = KANA_SLOT_DIRS[next_slot]
    h = (
        float(mark_height)
        if mark_height is not None and mark_height > 0
        else target_upem * DAKUTEN_MARK_HEIGHT_FRAC
    )
    d = h * KANA_MARK_SEP_FRAC
    return otRound(ux * d), otRound(uy * d)


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


class _OutlinePolylinePen(BasePen):
    """Flatten TrueType outlines to polylines (quadratics subdivided)."""

    def __init__(self) -> None:
        super().__init__(None)
        self.contours: List[List[Tuple[float, float]]] = []
        self._pts: List[Tuple[float, float]] = []

    def _moveTo(self, pt) -> None:
        self._pts = [(float(pt[0]), float(pt[1]))]

    def _lineTo(self, pt) -> None:
        self._pts.append((float(pt[0]), float(pt[1])))

    def _qCurveToOne(self, p1, p2) -> None:
        p0 = self._pts[-1]
        c1 = (float(p1[0]), float(p1[1]))
        p2f = (float(p2[0]), float(p2[1]))
        for i in range(1, 9):
            t = i / 8.0
            u = 1.0 - t
            x = u * u * p0[0] + 2.0 * u * t * c1[0] + t * t * p2f[0]
            y = u * u * p0[1] + 2.0 * u * t * c1[1] + t * t * p2f[1]
            self._pts.append((x, y))

    def _curveToOne(self, p1, p2, p3) -> None:
        p0 = self._pts[-1]
        c1 = (float(p1[0]), float(p1[1]))
        c2 = (float(p2[0]), float(p2[1]))
        p3f = (float(p3[0]), float(p3[1]))
        for i in range(1, 9):
            t = i / 8.0
            u = 1.0 - t
            x = (
                u * u * u * p0[0]
                + 3.0 * u * u * t * c1[0]
                + 3.0 * u * t * t * c2[0]
                + t * t * t * p3f[0]
            )
            y = (
                u * u * u * p0[1]
                + 3.0 * u * u * t * c1[1]
                + 3.0 * u * t * t * c2[1]
                + t * t * t * p3f[1]
            )
            self._pts.append((x, y))

    def _closePath(self) -> None:
        if len(self._pts) >= 2:
            self.contours.append(self._pts)

    def _endPath(self) -> None:
        if len(self._pts) >= 2:
            self.contours.append(self._pts)


def _baked_glyph(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
) -> Optional[TTGlyph]:
    if glyph.isComposite():
        try:
            baked, _, _ = _bake_transformed_glyph(
                glyph, Transform(), 0, glyph_set=glyph_set
            )
            return baked
        except Exception:
            return None
    return glyph


def _outline_polylines(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
) -> List[List[Tuple[float, float]]]:
    g = _baked_glyph(glyph, glyph_set)
    if g is None or g.numberOfContours <= 0:
        return []
    pen = _OutlinePolylinePen()
    try:
        g.draw(pen, glyph_set)
    except TypeError:
        try:
            g.draw(pen)
        except Exception:
            return []
    except Exception:
        return []
    return [c for c in pen.contours if len(c) >= 2]


def _polyline_perimeter(loop: Sequence[Tuple[float, float]]) -> float:
    total = 0.0
    n = len(loop)
    for i in range(n):
        j = (i + 1) % n
        dx = loop[j][0] - loop[i][0]
        dy = loop[j][1] - loop[i][1]
        total += math.hypot(dx, dy)
    return total


def _loop_centroid(loop: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _signed_area(loop: Sequence[Tuple[float, float]]) -> float:
    a = 0.0
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _segment_left_normal(
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> Tuple[float, float]:
    tx, ty = bx - ax, by - ay
    ln = math.hypot(tx, ty)
    if ln < 1e-9:
        return 0.0, 1.0
    return -ty / ln, tx / ln


def _outward_normal(
    loop: Sequence[Tuple[float, float]],
    index: int,
    *,
    ccw: bool,
) -> Tuple[float, float]:
    n = len(loop)
    i0 = (index - 1) % n
    i1 = index % n
    i2 = (index + 1) % n
    n1 = _segment_left_normal(loop[i0][0], loop[i0][1], loop[i1][0], loop[i1][1])
    n2 = _segment_left_normal(loop[i1][0], loop[i1][1], loop[i2][0], loop[i2][1])
    nx = n1[0] + n2[0]
    ny = n1[1] + n2[1]
    ln = math.hypot(nx, ny)
    if ln < 1e-9:
        nx, ny = n1
        ln = math.hypot(nx, ny) or 1.0
    else:
        nx /= ln
        ny /= ln
    if not ccw:
        nx, ny = -nx, -ny
    return nx, ny


def _mark_bbox(
    mark_points: Sequence[Tuple[float, float]],
    mark_h: float,
) -> Tuple[float, float, float, float]:
    if mark_points:
        xs = [x for x, _y in mark_points]
        ys = [y for _x, y in mark_points]
        return min(xs), min(ys), max(xs), max(ys)
    half = mark_h * 0.5
    return -half, -half, half, half


def _mark_height(mark_points: Sequence[Tuple[float, float]]) -> float:
    if not mark_points:
        return 0.0
    ys = [y for _x, y in mark_points]
    return max(ys) - min(ys)


def _mark_extent_along_normal(
    mark_points: Sequence[Tuple[float, float]],
    mark_h: float,
    nx: float,
    ny: float,
) -> float:
    if mark_points:
        return max(px * nx + py * ny for px, py in mark_points)
    return mark_h * 0.5


def _ink_bbox(points: Sequence[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _mark_rect(
    cx: float,
    cy: float,
    mx0: float,
    my0: float,
    mx1: float,
    my1: float,
) -> Tuple[float, float, float, float]:
    return cx + mx0, cy + my0, cx + mx1, cy + my1


def _rects_overlap(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0


def _conflicts(
    cx: float,
    cy: float,
    mx0: float,
    my0: float,
    mx1: float,
    my1: float,
    ink_rect: Tuple[float, float, float, float],
    placed: Sequence[Tuple[float, float]],
    min_sep_sq: float,
) -> bool:
    if _rects_overlap(_mark_rect(cx, cy, mx0, my0, mx1, my1), ink_rect):
        return True
    for px, py in placed:
        dx, dy = cx - px, cy - py
        if dx * dx + dy * dy < min_sep_sq:
            return True
    return False


def _densify_loop(
    loop: Sequence[Tuple[float, float]],
    *,
    step: float,
) -> List[Tuple[float, float, float, float, float]]:
    """Return ``(arc_s, x, y, nx, ny)`` samples along a closed polyline."""
    n = len(loop)
    if n < 2:
        return []
    ccw = _signed_area(loop) >= 0.0
    samples: List[Tuple[float, float, float, float, float]] = []
    arc = 0.0
    for i in range(n):
        ax, ay = loop[i]
        bx, by = loop[(i + 1) % n]
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len < 1e-9:
            continue
        nx, ny = _segment_left_normal(ax, ay, bx, by)
        if not ccw:
            nx, ny = -nx, -ny
        steps = max(1, int(math.ceil(seg_len / step)))
        for k in range(steps):
            t = k / steps
            x = ax + (bx - ax) * t
            y = ay + (by - ay) * t
            vi = int(round(i + t)) % n
            vnx, vny = _outward_normal(loop, vi, ccw=ccw)
            # Blend segment and vertex normals so concave corners stay tight.
            mx = 0.65 * nx + 0.35 * vnx
            my = 0.65 * ny + 0.35 * vny
            ml = math.hypot(mx, my) or 1.0
            mx /= ml
            my /= ml
            samples.append((arc, x, y, mx, my))
        arc += seg_len
    return samples


def _place_at_sample(
    x: float,
    y: float,
    nx: float,
    ny: float,
    *,
    gap: float,
    mark_points: Sequence[Tuple[float, float]],
    mark_h: float,
    mx0: float,
    my0: float,
    mx1: float,
    my1: float,
    ink_rect: Tuple[float, float, float, float],
    placed: Sequence[Tuple[float, float]],
    min_sep_sq: float,
    normal_step: float,
    max_normal_steps: int = 24,
) -> Optional[Tuple[float, float]]:
    base = gap + _mark_extent_along_normal(mark_points, mark_h, nx, ny)
    for nstep in range(max_normal_steps):
        dist = base + nstep * normal_step
        cx, cy = x + nx * dist, y + ny * dist
        if not _conflicts(cx, cy, mx0, my0, mx1, my1, ink_rect, placed, min_sep_sq):
            return cx, cy
    return None


def _ensure_outward(
    nx: float,
    ny: float,
    px: float,
    py: float,
    cx: float,
    cy: float,
) -> Tuple[float, float]:
    if (px - cx) * nx + (py - cy) * ny < 0.0:
        return -nx, -ny
    return nx, ny


def _contour_slot_positions(
    loop: Sequence[Tuple[float, float]],
    *,
    gap: float,
    mark_h: float,
    mark_points: Sequence[Tuple[float, float]],
    mx0: float,
    my0: float,
    mx1: float,
    my1: float,
    ink_rect: Tuple[float, float, float, float],
    min_sep: float,
    sample_step: float,
    arc_step: float,
    slot_count: int = 8,
) -> List[Tuple[float, float]]:
    del arc_step
    samples = _densify_loop(loop, step=sample_step)
    if not samples:
        return []
    perimeter = samples[-1][0] + math.hypot(
        samples[-1][1] - samples[0][1],
        samples[-1][2] - samples[0][2],
    )
    if perimeter < 1e-6:
        return []

    centroid = _loop_centroid(loop)
    ccw = _signed_area(loop) >= 0.0
    tr_x, tr_y = _TR_DIR
    start_i = max(
        range(len(samples)),
        key=lambda i: samples[i][1] * tr_x + samples[i][2] * tr_y,
    )
    start_s = samples[start_i][0]

    min_sep_sq = min_sep * min_sep
    normal_step = max(1.0, mark_h * 0.03)

    # Clockwise from TR: forward arc on CW outers, backward on CCW outers.
    if ccw:
        order = sorted(
            range(len(samples)),
            key=lambda i: (start_s - samples[i][0]) % perimeter,
        )
    else:
        order = sorted(
            range(len(samples)),
            key=lambda i: (samples[i][0] - start_s) % perimeter,
        )

    placed: List[Tuple[float, float]] = []
    positions: List[Tuple[float, float]] = []
    cursor = 0
    for _slot in range(slot_count):
        found: Optional[Tuple[float, float]] = None
        while cursor < len(order):
            i = order[cursor]
            cursor += 1
            _s, x, y, nx, ny = samples[i]
            nx, ny = _ensure_outward(nx, ny, x, y, centroid[0], centroid[1])
            hit = _place_at_sample(
                x,
                y,
                nx,
                ny,
                gap=gap,
                mark_points=mark_points,
                mark_h=mark_h,
                mx0=mx0,
                my0=my0,
                mx1=mx1,
                my1=my1,
                ink_rect=ink_rect,
                placed=placed,
                min_sep_sq=min_sep_sq,
                normal_step=normal_step,
            )
            if hit is not None:
                found = hit
                break
        if found is None:
            break
        placed.append(found)
        positions.append(found)
    if len(positions) < slot_count:
        # Tighter spacing pass for crowded outlines.
        cursor = 0
        relax_sq = (min_sep * 0.55) ** 2
        while len(positions) < slot_count and cursor < len(order):
            i = order[cursor]
            cursor += 1
            _s, x, y, nx, ny = samples[i]
            nx, ny = _ensure_outward(nx, ny, x, y, centroid[0], centroid[1])
            hit = _place_at_sample(
                x,
                y,
                nx,
                ny,
                gap=gap,
                mark_points=mark_points,
                mark_h=mark_h,
                mx0=mx0,
                my0=my0,
                mx1=mx1,
                my1=my1,
                ink_rect=ink_rect,
                placed=placed,
                min_sep_sq=relax_sq,
                normal_step=normal_step,
            )
            if hit is not None:
                placed.append(hit)
                positions.append(hit)
    return positions


def kana_slot_anchors(
    glyph: TTGlyph,
    *,
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_points: Optional[Sequence[Tuple[float, float]]] = None,
    mark_ink_height: Optional[float] = None,
    mark_scale: float = 1.0,
) -> Optional[Dict[str, Tuple[int, int]]]:
    """Eight coordinates hugging this glyph's outline (TR→BL clockwise fill)."""
    del mark_scale
    polylines = _outline_polylines(glyph, glyph_set)
    if not polylines:
        points = _contour_ink_points_fallback(glyph, glyph_set)
        if len(points) < 2:
            return None
        polylines = [points]

    loop = max(polylines, key=lambda p: abs(_signed_area(p)))
    flat_ink = [p for contour in polylines for p in contour]

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
    sample_step = max(2.0, mark_h * KANA_CONTOUR_SAMPLE_FRAC)
    arc_step = max(1.0, mark_h * KANA_CONTOUR_ARC_STEP_FRAC)

    mx0, my0, mx1, my1 = _mark_bbox(mpts, mark_h)
    ix0, iy0, ix1, iy1 = _ink_bbox(flat_ink)
    ink_rect = (ix0, iy0, ix1, iy1)

    positions = _contour_slot_positions(
        loop,
        gap=gap,
        mark_h=mark_h,
        mark_points=mpts,
        mx0=mx0,
        my0=my0,
        mx1=mx1,
        my1=my1,
        ink_rect=ink_rect,
        min_sep=min_sep,
        sample_step=sample_step,
        arc_step=arc_step,
        slot_count=len(DAKUTEN_SLOTS),
    )
    if len(positions) < len(DAKUTEN_SLOTS):
        return None

    out: Dict[str, Tuple[int, int]] = {}
    for (slot, _suf), (cx, cy) in zip(DAKUTEN_SLOTS, positions):
        out[slot] = (otRound(cx), otRound(cy))
    return out


def _contour_ink_points_fallback(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
) -> List[Tuple[float, float]]:
    g = _baked_glyph(glyph, glyph_set)
    if g is None:
        return []
    try:
        coords = g.coordinates
        if coords is None or len(coords) == 0:
            return []
        return [(float(x), float(y)) for x, y in coords]
    except Exception:
        return []


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
