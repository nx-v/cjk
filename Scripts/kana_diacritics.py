"""Kana dakuten placement: eight compass slots on an offset support contour.

Outlines are converted to pathops paths (quadratics/cubics preserved), offset
outward by the mark gap, then each slot ray from the ink centroid hits that
offset contour (Bezier-aware). The ring fills TR↔BL first, alternating inward
on both arcs. Marks 9+ stack outward on the BL anchor ray (``.ch``).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
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
    _ttglyph_to_pathops,
    orientation_form_names,
    overlay_glyph_name,
    slice_form_name,
)

Point = Tuple[float, float]
_Seg = Tuple[str, Tuple[Point, ...]]

# Air gap between kana ink and the nearest mark contour (fraction of dakuten H).
KANA_MARK_GAP_FRAC = 0.08
# Minimum center-to-center separation between marks (fraction of dakuten H).
KANA_MARK_SEP_FRAC = 1.05
# Outward stack step for 9th+ marks on the same compass ray (fraction of H).
KANA_CHAIN_RADIAL_FRAC = 1.05

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
    """Mark2 anchor: stack further out on the parent slot's compass ray."""
    del glyph, glyph_set
    ux, uy = KANA_SLOT_DIRS[parent_slot]
    h = (
        float(mark_height)
        if mark_height is not None and mark_height > 0
        else target_upem * DAKUTEN_MARK_HEIGHT_FRAC
    )
    d = h * KANA_CHAIN_RADIAL_FRAC
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


def _vadd(a: Point, b: Point) -> Point:
    return a[0] + b[0], a[1] + b[1]


def _vsub(a: Point, b: Point) -> Point:
    return a[0] - b[0], a[1] - b[1]


def _vmul(s: float, v: Point) -> Point:
    return s * v[0], s * v[1]


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _hypot(v: Point) -> float:
    return math.hypot(v[0], v[1])


def _unit(v: Point) -> Point:
    ln = _hypot(v)
    if ln < 1e-12:
        return 0.0, 1.0
    return v[0] / ln, v[1] / ln


def _outward_normal(tangent: Point, ccw: bool) -> Point:
    tx, ty = _unit(tangent)
    return (-ty, tx) if ccw else (ty, -tx)


def _parse_pathops_contour(contour) -> List[_Seg]:
    import pathops

    segs: List[_Seg] = []
    cur: Optional[Point] = None
    start: Optional[Point] = None
    for verb, pts in contour:
        match verb:
            case pathops.PathVerb.MOVE:
                cur = (float(pts[0][0]), float(pts[0][1]))
                start = cur
            case pathops.PathVerb.LINE:
                end = (float(pts[0][0]), float(pts[0][1]))
                if cur is not None:
                    segs.append(("line", (cur, end)))
                cur = end
            case pathops.PathVerb.QUAD:
                c1 = (float(pts[0][0]), float(pts[0][1]))
                end = (float(pts[1][0]), float(pts[1][1]))
                if cur is not None:
                    segs.append(("quad", (cur, c1, end)))
                cur = end
            case pathops.PathVerb.CUBIC:
                c1 = (float(pts[0][0]), float(pts[0][1]))
                c2 = (float(pts[1][0]), float(pts[1][1]))
                end = (float(pts[2][0]), float(pts[2][1]))
                if cur is not None:
                    segs.append(("cubic", (cur, c1, c2, end)))
                cur = end
            case pathops.PathVerb.CLOSE:
                if cur is not None and start is not None and cur != start:
                    segs.append(("line", (cur, start)))
                cur = start
    return segs


def _flatten_segments(segs: Sequence[_Seg], *, steps: int = 8) -> List[Point]:
    pts: List[Point] = []
    for kind, sp in segs:
        match kind:
            case "line":
                p0, p1 = sp
                if not pts:
                    pts.append(p0)
                pts.append(p1)
            case "quad":
                p0, p1, p2 = sp
                if not pts:
                    pts.append(p0)
                for i in range(1, steps + 1):
                    t = i / steps
                    u = 1.0 - t
                    x = u * u * p0[0] + 2.0 * u * t * p1[0] + t * t * p2[0]
                    y = u * u * p0[1] + 2.0 * u * t * p1[1] + t * t * p2[1]
                    pts.append((x, y))
            case "cubic":
                p0, p1, p2, p3 = sp
                if not pts:
                    pts.append(p0)
                for i in range(1, steps + 1):
                    t = i / steps
                    u = 1.0 - t
                    x = (
                        u * u * u * p0[0]
                        + 3.0 * u * u * t * p1[0]
                        + 3.0 * u * t * t * p2[0]
                        + t * t * t * p3[0]
                    )
                    y = (
                        u * u * u * p0[1]
                        + 3.0 * u * u * t * p1[1]
                        + 3.0 * u * t * t * p2[1]
                        + t * t * t * p3[1]
                    )
                    pts.append((x, y))
    return pts


def _segments_signed_area(segs: Sequence[_Seg]) -> float:
    loop = _flatten_segments(segs, steps=6)
    if len(loop) < 3:
        return 0.0
    a = 0.0
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _largest_path_contour(path):
    best = None
    best_area = 0.0
    for contour in path.contours:
        segs = _parse_pathops_contour(contour)
        if not segs:
            continue
        area = abs(_segments_signed_area(segs))
        if area > best_area:
            best_area = area
            best = contour
    return best


def _bezier_eval_quad(p0: Point, p1: Point, p2: Point, t: float) -> Point:
    u = 1.0 - t
    x = u * u * p0[0] + 2.0 * u * t * p1[0] + t * t * p2[0]
    y = u * u * p0[1] + 2.0 * u * t * p1[1] + t * t * p2[1]
    return x, y


def _bezier_eval_cubic(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    u = 1.0 - t
    x = (
        u * u * u * p0[0]
        + 3.0 * u * u * t * p1[0]
        + 3.0 * u * t * t * p2[0]
        + t * t * t * p3[0]
    )
    y = (
        u * u * u * p0[1]
        + 3.0 * u * u * t * p1[1]
        + 3.0 * u * t * t * p2[1]
        + t * t * t * p3[1]
    )
    return x, y


def _bezier_deriv_quad(p0: Point, p1: Point, p2: Point, t: float) -> Point:
    u = 1.0 - t
    x = 2.0 * u * (p1[0] - p0[0]) + 2.0 * t * (p2[0] - p1[0])
    y = 2.0 * u * (p1[1] - p0[1]) + 2.0 * t * (p2[1] - p1[1])
    return x, y


def _bezier_deriv_cubic(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    u = 1.0 - t
    x = (
        3.0 * u * u * (p1[0] - p0[0])
        + 6.0 * u * t * (p2[0] - p1[0])
        + 3.0 * t * t * (p3[0] - p2[0])
    )
    y = (
        3.0 * u * u * (p1[1] - p0[1])
        + 6.0 * u * t * (p2[1] - p1[1])
        + 3.0 * t * t * (p3[1] - p2[1])
    )
    return x, y


def _seg_normal_at(kind: str, pts: Tuple[Point, ...], t: float, ccw: bool) -> Point:
    match kind:
        case "line":
            return _outward_normal(_vsub(pts[1], pts[0]), ccw)
        case "quad":
            tan = _bezier_deriv_quad(pts[0], pts[1], pts[2], t)
            return _outward_normal(tan, ccw)
        case _:
            tan = _bezier_deriv_cubic(pts[0], pts[1], pts[2], pts[3], t)
            return _outward_normal(tan, ccw)


def _offset_segment(kind: str, pts: Tuple[Point, ...], d: float, ccw: bool) -> _Seg:
    match kind:
        case "line":
            p0, p1 = pts
            n = _outward_normal(_vsub(p1, p0), ccw)
            return ("line", (_vadd(p0, _vmul(d, n)), _vadd(p1, _vmul(d, n))))
        case "quad":
            p0, p1, p2 = pts
            samples = (0.0, 0.5, 1.0)
            op: List[Point] = []
            for t in samples:
                pt = _bezier_eval_quad(p0, p1, p2, t)
                n = _seg_normal_at("quad", pts, t, ccw)
                op.append(_vadd(pt, _vmul(d, n)))
            return ("quad", (op[0], op[1], op[2]))
        case _:
            p0, p1, p2, p3 = pts
            samples = (0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0)
            op = []
            for t in samples:
                pt = _bezier_eval_cubic(p0, p1, p2, p3, t)
                n = _seg_normal_at("cubic", pts, t, ccw)
                op.append(_vadd(pt, _vmul(d, n)))
            return ("cubic", (op[0], op[1], op[2], op[3]))


def _offset_pathops_contour(contour, distance: float):
    import pathops

    segs = _parse_pathops_contour(contour)
    if not segs or distance <= 0.0:
        return None
    ccw = _segments_signed_area(segs) >= 0.0
    off_segs = [_offset_segment(kind, pts, distance, ccw) for kind, pts in segs]
    out = pathops.Path()
    first = True
    prev_end: Optional[Point] = None
    for kind, pts in off_segs:
        if first:
            out.moveTo(pts[0][0], pts[0][1])
            first = False
        elif prev_end is not None and _hypot(_vsub(prev_end, pts[0])) > 1e-6:
            out.lineTo(pts[0][0], pts[0][1])
        match kind:
            case "line":
                out.lineTo(pts[1][0], pts[1][1])
                prev_end = pts[1]
            case "quad":
                out.quadTo(pts[1][0], pts[1][1], pts[2][0], pts[2][1])
                prev_end = pts[2]
            case _:
                out.curveTo(
                    pts[1][0],
                    pts[1][1],
                    pts[2][0],
                    pts[2][1],
                    pts[3][0],
                    pts[3][1],
                )
                prev_end = pts[3]
    out.close()
    try:
        return pathops.simplify(out, fix_winding=True)
    except Exception:
        return out


def _glyph_to_pathops(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
):
    g = _baked_glyph(glyph, glyph_set)
    if g is None or g.numberOfContours <= 0:
        return None
    try:
        return _ttglyph_to_pathops(g, glyph_set)
    except Exception:
        return None


def _offset_contour_segments(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
    distance: float,
) -> Tuple[List[_Seg], bool]:
    path = _glyph_to_pathops(glyph, glyph_set)
    if path is None:
        return [], True
    contour = _largest_path_contour(path)
    if contour is None:
        return [], True
    ink_segs = _parse_pathops_contour(contour)
    ccw = _segments_signed_area(ink_segs) >= 0.0
    off = _offset_pathops_contour(contour, distance)
    if off is None:
        return [], ccw
    segs: List[_Seg] = []
    for off_contour in off.contours:
        segs.extend(_parse_pathops_contour(off_contour))
    return segs, ccw


def _line_line_intersection(
    p1: Point,
    d1: Point,
    p2: Point,
    d2: Point,
) -> Optional[Point]:
    denom = _cross(d1, d2)
    if abs(denom) < 1e-12:
        return None
    t = _cross(_vsub(p2, p1), d2) / denom
    return _vadd(p1, _vmul(t, d1))


def _ray_line_hit(
    origin: Point,
    direction: Point,
    p0: Point,
    p1: Point,
) -> Optional[Tuple[float, Point, Point]]:
    seg = _vsub(p1, p0)
    hit = _line_line_intersection(origin, direction, p0, seg)
    if hit is None:
        return None
    proj = _dot(_vsub(hit, origin), direction)
    if proj < 0.0:
        return None
    s = _dot(_vsub(hit, p0), seg) / (_dot(seg, seg) or 1.0)
    if s < -1e-6 or s > 1.0 + 1e-6:
        return None
    tan = seg
    return proj, hit, tan


def _lerp(a: Point, b: Point, t: float) -> Point:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _split_quad(p0: Point, p1: Point, p2: Point) -> Tuple[_Seg, _Seg]:
    m01 = _lerp(p0, p1, 0.5)
    m12 = _lerp(p1, p2, 0.5)
    mid = _lerp(m01, m12, 0.5)
    return ("quad", (p0, m01, mid)), ("quad", (mid, m12, p2))


def _split_cubic(p0: Point, p1: Point, p2: Point, p3: Point) -> Tuple[_Seg, _Seg]:
    m01 = _lerp(p0, p1, 0.5)
    m12 = _lerp(p1, p2, 0.5)
    m23 = _lerp(p2, p3, 0.5)
    m012 = _lerp(m01, m12, 0.5)
    m123 = _lerp(m12, m23, 0.5)
    mid = _lerp(m012, m123, 0.5)
    return ("cubic", (p0, m01, m012, mid)), ("cubic", (mid, m123, m23, p3))


def _ray_flat_curve_hits(
    origin: Point,
    direction: Point,
    kind: str,
    pts: Tuple[Point, ...],
    *,
    depth: int = 0,
) -> List[Tuple[float, Point, Point]]:
    match kind:
        case "quad":
            p0, p1, p2 = pts
            end = p2
        case _:
            p0, p1, p2, p3 = pts
            end = p3
    if depth >= 14 or _hypot(_vsub(end, p0)) < 0.5:
        hit = _ray_line_hit(origin, direction, p0, end)
        return [hit] if hit else []
    match kind:
        case "quad":
            left, right = _split_quad(p0, p1, p2)
        case _:
            left, right = _split_cubic(p0, p1, p2, p3)
    return _ray_flat_curve_hits(
        origin, direction, left[0], left[1], depth=depth + 1
    ) + _ray_flat_curve_hits(origin, direction, right[0], right[1], depth=depth + 1)


def _ray_segment_hit(
    origin: Point,
    direction: Point,
    kind: str,
    pts: Tuple[Point, ...],
) -> Optional[Tuple[float, Point, Point]]:
    match kind:
        case "line":
            return _ray_line_hit(origin, direction, pts[0], pts[1])
        case _:
            hits = _ray_flat_curve_hits(origin, direction, kind, pts)
            best: Optional[Tuple[float, Point, Point]] = None
            for hit in hits:
                if hit is None:
                    continue
                if best is None or hit[0] > best[0]:
                    best = hit
            return best


def _ray_farthest_contour_hit(
    origin: Point,
    direction: Point,
    segs: Sequence[_Seg],
    ccw: bool,
) -> Optional[Tuple[float, float, float, float]]:
    ux, uy = _unit(direction)
    best_proj = -1e30
    best_xy: Optional[Tuple[float, float, float, float]] = None
    for kind, pts in segs:
        hit = _ray_segment_hit(origin, (ux, uy), kind, pts)
        if hit is None:
            continue
        proj, xy, tan = hit
        if proj <= best_proj:
            continue
        nx, ny = _outward_normal(tan, ccw)
        best_proj = proj
        best_xy = (xy[0], xy[1], nx, ny)
    return best_xy


def _baked_glyph(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
) -> Optional[TTGlyph]:
    match glyph.isComposite():
        case True:
            try:
                baked, _, _ = _bake_transformed_glyph(
                    glyph, Transform(), 0, glyph_set=glyph_set
                )
                return baked
            except Exception:
                return None
        case False:
            return glyph


def _ink_centroid(
    points: Sequence[Tuple[float, float]],
) -> Tuple[float, float]:
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return sum(xs) / len(xs), sum(ys) / len(ys)


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


def _ink_bbox(
    points: Sequence[Tuple[float, float]],
) -> Tuple[float, float, float, float]:
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


# Slot fill order: TR and BL first, then alternating inward on both arcs.
_KANA_RING_FILL_ORDER: Tuple[str, ...] = (
    "tr",
    "bl",
    "cr",
    "cl",
    "br",
    "tl",
    "tm",
    "bm",
)


def _offset_contour_slot_positions(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
    origin: Point,
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
) -> Optional[List[Tuple[float, float]]]:
    """Place eight slots on a Bezier offset contour (TR↔BL zigzag validation)."""
    min_sep_sq = min_sep * min_sep
    extra_step = max(1.0, mark_h * 0.04)
    slot_xy: Dict[str, Tuple[float, float]] = {}

    for attempt in range(64):
        offset_d = gap + attempt * extra_step
        segs, ccw = _offset_contour_segments(glyph, glyph_set, offset_d)
        if not segs:
            continue
        placed: List[Tuple[float, float]] = []
        ok = True
        slot_xy.clear()
        for slot in _KANA_RING_FILL_ORDER:
            ux, uy = KANA_SLOT_DIRS[slot]
            hit = _ray_farthest_contour_hit(origin, (ux, uy), segs, ccw)
            if hit is None:
                ok = False
                break
            px, py, nx, ny = hit
            ext = _mark_extent_along_normal(mark_points, mark_h, nx, ny)
            cx = px + nx * ext
            cy = py + ny * ext
            if _conflicts(cx, cy, mx0, my0, mx1, my1, ink_rect, placed, min_sep_sq):
                ok = False
                break
            placed.append((cx, cy))
            slot_xy[slot] = (cx, cy)
        if ok and len(slot_xy) == len(DAKUTEN_SLOTS):
            return [slot_xy[slot] for slot, _suf in DAKUTEN_SLOTS]
    return None


def kana_slot_anchors(
    glyph: TTGlyph,
    *,
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_points: Optional[Sequence[Tuple[float, float]]] = None,
    mark_ink_height: Optional[float] = None,
    mark_scale: float = 1.0,
) -> Optional[Dict[str, Tuple[int, int]]]:
    """Eight anchors on a Bezier offset contour (TR↔BL zigzag fill)."""
    del mark_scale
    path = _glyph_to_pathops(glyph, glyph_set)
    if path is None:
        flat = _contour_ink_points_fallback(glyph, glyph_set)
        if len(flat) < 2:
            return None
        origin = _ink_centroid(flat)
        ink_rect = _ink_bbox(flat)
    else:
        contour = _largest_path_contour(path)
        if contour is None:
            return None
        segs = _parse_pathops_contour(contour)
        flat = _flatten_segments(segs)
        if len(flat) < 2:
            return None
        origin = _ink_centroid(flat)
        ink_rect = _ink_bbox(flat)

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
    mx0, my0, mx1, my1 = _mark_bbox(mpts, mark_h)

    positions = _offset_contour_slot_positions(
        glyph,
        glyph_set,
        origin,
        gap=gap,
        mark_h=mark_h,
        mark_points=mpts,
        mx0=mx0,
        my0=my0,
        mx1=mx1,
        my1=my1,
        ink_rect=ink_rect,
        min_sep=min_sep,
    )
    if not positions:
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
