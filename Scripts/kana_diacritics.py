"""Kana dakuten placement: eight compass slots hugging the ink contour.

Each of the first eight marks sits on its compass ray from the ink centroid,
as close to the outline as the mark gap allows; separation bumps only when
marks would overlap. Marks 9+ stack outward on TR→CR→…→BL in sequence, each
GPOS-chained to its matching slot diacritic.
"""

from __future__ import annotations

import glob
import math
import os
import pickle
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Optional, Sequence, Set, Tuple

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

Point = Tuple[float, float]

# Air gap between kana ink and the nearest mark contour (fraction of dakuten H).
KANA_MARK_GAP_FRAC = 0.08
# Minimum center-to-center separation between marks (fraction of dakuten H).
KANA_MARK_SEP_FRAC = 1.05
# Outward stack step for 9th+ marks on the same compass ray (fraction of H).
KANA_CHAIN_RADIAL_FRAC = 1.05
# Curve flattening steps when turning an ink outline into a polyline.
KANA_CONTOUR_FLATTEN_STEPS = 8
# Inward slide step when hugging the contour along a slot ray (fraction of H).
KANA_SLOT_SLIDE_FRAC = 0.02

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


class _PolylinePen(BasePen):
    """Flatten a glyph outline into closed contour polylines."""

    def __init__(self, glyph_set):
        super().__init__(glyphSet=glyph_set)
        self.contours: List[List[Point]] = []
        self._current: List[Point] = []
        self._start: Optional[Point] = None

    def _moveTo(self, pt):
        self._current = [(float(pt[0]), float(pt[1]))]
        self._start = self._current[0]

    def _lineTo(self, pt):
        self._current.append((float(pt[0]), float(pt[1])))

    def _curveToOne(self, pt1, pt2):
        p0 = self._current[-1]
        c1 = (float(pt1[0]), float(pt1[1]))
        p2 = (float(pt2[0]), float(pt2[1]))
        steps = KANA_CONTOUR_FLATTEN_STEPS
        for i in range(1, steps + 1):
            t = i / steps
            u = 1.0 - t
            x = u * u * p0[0] + 2.0 * u * t * c1[0] + t * t * p2[0]
            y = u * u * p0[1] + 2.0 * u * t * c1[1] + t * t * p2[1]
            self._current.append((x, y))

    def _curveToThree(self, pt1, pt2, pt3):
        p0 = self._current[-1]
        c1 = (float(pt1[0]), float(pt1[1]))
        c2 = (float(pt2[0]), float(pt2[1]))
        p3 = (float(pt3[0]), float(pt3[1]))
        steps = KANA_CONTOUR_FLATTEN_STEPS
        for i in range(1, steps + 1):
            t = i / steps
            u = 1.0 - t
            x = (
                u * u * u * p0[0]
                + 3.0 * u * u * t * c1[0]
                + 3.0 * u * t * t * c2[0]
                + t * t * t * p3[0]
            )
            y = (
                u * u * u * p0[1]
                + 3.0 * u * u * t * c1[1]
                + 3.0 * u * t * t * c2[1]
                + t * t * t * p3[1]
            )
            self._current.append((x, y))

    def _closePath(self):
        if self._start is not None and self._current and self._current[-1] != self._start:
            self._current.append(self._start)
        if len(self._current) >= 2:
            self.contours.append(self._current)
        self._current = []
        self._start = None

    def _endPath(self):
        if len(self._current) >= 2:
            self.contours.append(self._current)
        self._current = []
        self._start = None


def _polyline_signed_area(points: Sequence[Point]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return 0.5 * area


def _glyph_contour_polylines(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> List[List[Point]]:
    g = _baked_glyph(glyph, glyph_set) if glyph_set is not None else glyph
    if g is None or g.numberOfContours <= 0:
        return []
    try:
        pen = _PolylinePen(glyph_set or {})
        g.draw(pen, None)
        return pen.contours
    except Exception:
        return []


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
    seg_len_sq = _dot(seg, seg)
    if seg_len_sq < 1e-18:
        return None
    s = _dot(_vsub(hit, p0), seg) / seg_len_sq
    if s < -1e-6 or s > 1.0 + 1e-6:
        return None
    return proj, hit, seg


def _ray_polyline_farthest(
    origin: Point,
    direction: Point,
    points: Sequence[Point],
    ccw: bool,
) -> Optional[Tuple[float, float, float, float]]:
    if len(points) < 2:
        return None
    ux, uy = _unit(direction)
    best_proj = -1e30
    best_xy: Optional[Tuple[float, float, float, float]] = None
    n = len(points)
    for i in range(n):
        p0 = points[i]
        p1 = points[(i + 1) % n]
        hit = _ray_line_hit(origin, (ux, uy), p0, p1)
        if hit is None:
            continue
        proj, xy, tan = hit
        if proj <= best_proj:
            continue
        nx, ny = _outward_normal(tan, ccw)
        best_proj = proj
        best_xy = (xy[0], xy[1], nx, ny)
    return best_xy


def _ray_ink_reach(
    origin: Point,
    direction: Point,
    polylines: Sequence[Sequence[Point]],
) -> float:
    """Farthest ink hit along `direction` from `origin` (font units)."""
    ux, uy = _unit(direction)
    best = 0.0
    for poly in polylines:
        if len(poly) < 2:
            continue
        ccw = _polyline_signed_area(poly) >= 0.0
        hit = _ray_polyline_farthest(origin, direction, poly, ccw)
        if hit is None:
            continue
        px, py, _, _ = hit
        proj = _dot(_vsub((px, py), origin), (ux, uy))
        if proj > best:
            best = proj
    return best


def _closest_center_radius(
    origin: Point,
    direction: Point,
    polylines: Sequence[Sequence[Point]],
    *,
    gap: float,
    mark_h: float,
    mark_points: Sequence[Tuple[float, float]],
) -> float:
    """Minimum center distance along `direction` clearing ink by `gap`."""
    ux, uy = _unit(direction)
    reach = _ray_ink_reach(origin, direction, polylines)
    if mark_points:
        inward = min(px * ux + py * uy for px, py in mark_points)
        return reach + gap - inward
    return reach + gap + mark_h * 0.5


def _closest_slot_on_ray(
    origin: Point,
    direction: Point,
    polylines: Sequence[Sequence[Point]],
    *,
    gap: float,
    mark_h: float,
    mark_points: Sequence[Tuple[float, float]],
    placed: Sequence[Tuple[float, float]],
    min_sep_sq: float,
) -> Optional[Tuple[float, float]]:
    """Place a slot mark as close to the ink as gap and separation allow."""
    ux, uy = _unit(direction)
    r = _closest_center_radius(
        origin,
        direction,
        polylines,
        gap=gap,
        mark_h=mark_h,
        mark_points=mark_points,
    )
    step = max(0.5, mark_h * KANA_SLOT_SLIDE_FRAC)
    for _attempt in range(64):
        cx = origin[0] + ux * r
        cy = origin[1] + uy * r
        if not _conflicts(cx, cy, placed, min_sep_sq):
            return cx, cy
        r += step
    return None


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


def _mark_height(mark_points: Sequence[Tuple[float, float]]) -> float:
    if not mark_points:
        return 0.0
    ys = [y for _x, y in mark_points]
    return max(ys) - min(ys)


def _conflicts(
    cx: float,
    cy: float,
    placed: Sequence[Tuple[float, float]],
    min_sep_sq: float,
) -> bool:
    """True when `(cx, cy)` is too close to an already-placed slot center."""
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


def _octagon_slot_positions(
    glyph: TTGlyph,
    glyph_set: Dict[str, TTGlyph],
    origin: Point,
    *,
    gap: float,
    mark_h: float,
    mark_points: Sequence[Tuple[float, float]],
    min_sep: float,
) -> Optional[List[Tuple[float, float]]]:
    """Eight compass slots, each as close to the ink as its ray allows (TR↔BL zigzag)."""
    baked = _baked_glyph(glyph, glyph_set)
    if baked is None:
        return None
    polylines = _glyph_contour_polylines(baked)
    if not polylines:
        polylines = [_contour_ink_points_fallback(glyph, glyph_set)]
    polylines = [p for p in polylines if len(p) >= 2]
    if not polylines:
        return None

    min_sep_sq = min_sep * min_sep
    slot_xy: Dict[str, Tuple[float, float]] = {}
    placed: List[Tuple[float, float]] = []

    for slot in _KANA_RING_FILL_ORDER:
        xy = _closest_slot_on_ray(
            origin,
            KANA_SLOT_DIRS[slot],
            polylines,
            gap=gap,
            mark_h=mark_h,
            mark_points=mark_points,
            placed=placed,
            min_sep_sq=min_sep_sq,
        )
        if xy is None:
            return None
        placed.append(xy)
        slot_xy[slot] = xy

    if len(slot_xy) == len(DAKUTEN_SLOTS):
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
    """Eight anchors hugging the ink along each compass ray."""
    del mark_scale
    flat = _contour_ink_points_fallback(glyph, glyph_set)
    if len(flat) < 2:
        return None
    origin = _ink_centroid(flat)

    mpts = list(mark_points) if mark_points else []
    mark_h = _mark_height(mpts)
    if mark_ink_height is not None and mark_ink_height > 0:
        mark_h = max(mark_h, float(mark_ink_height))
    if mark_h <= 0:
        mark_h = float(target_upem * DAKUTEN_MARK_HEIGHT_FRAC)
    gap = mark_h * KANA_MARK_GAP_FRAC
    min_sep = mark_h * KANA_MARK_SEP_FRAC

    positions = _octagon_slot_positions(
        glyph,
        glyph_set,
        origin,
        gap=gap,
        mark_h=mark_h,
        mark_points=mpts,
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


_ANCHOR_WORKER_CACHE_DIR: Optional[str] = None


def _anchor_chunk_input_path(cache_dir: str, chunk_id: int) -> str:
    return os.path.join(cache_dir, f"anchor_input_{chunk_id:04d}.pkl")


def _anchor_chunk_result_path(cache_dir: str, chunk_id: int) -> str:
    return os.path.join(cache_dir, f"anchors_{chunk_id:04d}.pkl")


def _partition_anchor_names(names: Sequence[str], n_chunks: int) -> List[List[str]]:
    n_chunks = max(1, min(n_chunks, len(names)))
    size = (len(names) + n_chunks - 1) // n_chunks
    return [list(names[i : i + size]) for i in range(0, len(names), size) if names[i : i + size]]


def _acquire_cache_lock(cache_path: str) -> str:
    lock_path = cache_path + ".lock"
    for _attempt in range(600):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock_path
        except FileExistsError:
            if os.path.isfile(cache_path):
                return ""
            time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for cache lock: {cache_path}")


def _release_cache_lock(lock_path: str) -> None:
    if lock_path:
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _write_anchor_chunk_input(
    cache_dir: str,
    chunk_id: int,
    chunk_names: Sequence[str],
    *,
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_points: Optional[Sequence[Tuple[float, float]]],
    mark_ink_height: Optional[float],
    mark_scale: float,
) -> str:
    path = _anchor_chunk_input_path(cache_dir, chunk_id)
    if os.path.isfile(path):
        return path
    lock = _acquire_cache_lock(path)
    try:
        if os.path.isfile(path):
            return path
        subset = _glyph_subset_for_names(chunk_names, glyph_set)
        state = {
            "names": list(chunk_names),
            "glyphs": subset,
            "target_upem": target_upem,
            "mark_points": tuple(mark_points) if mark_points else None,
            "mark_ink_height": mark_ink_height,
            "mark_scale": mark_scale,
        }
        with open(path, "wb") as f:
            pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
        return path
    finally:
        _release_cache_lock(lock)


def _write_anchor_chunk_result(
    cache_dir: str,
    chunk_id: int,
    anchors: Dict[str, Dict[int, Tuple[int, int]]],
) -> None:
    path = _anchor_chunk_result_path(cache_dir, chunk_id)
    if os.path.isfile(path):
        return
    lock = _acquire_cache_lock(path)
    try:
        if os.path.isfile(path):
            return
        with open(path, "wb") as f:
            pickle.dump(anchors, f, protocol=pickle.HIGHEST_PROTOCOL)
    finally:
        _release_cache_lock(lock)


def _load_merged_anchor_chunks(
    cache_dir: str,
    names: Sequence[str],
    *,
    require_all: bool = False,
) -> Optional[Dict[str, Dict[int, Tuple[int, int]]]]:
    paths = sorted(glob.glob(os.path.join(cache_dir, "anchors_*.pkl")))
    if not paths:
        legacy = os.path.join(cache_dir, "anchors.pkl")
        if os.path.isfile(legacy):
            paths = [legacy]
        else:
            return None
    merged: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for path in paths:
        with open(path, "rb") as f:
            merged.update(pickle.load(f))
    out = {name: merged[name] for name in names if name in merged}
    if not out:
        return None
    if require_all and len(out) != len(names):
        return None
    return out


def _glyph_subset_for_names(
    names: Sequence[str],
    glyph_set: Dict[str, TTGlyph],
) -> Dict[str, TTGlyph]:
    """Composite closure of `names` (minimal dict for worker pickling)."""
    needed: Set[str] = set()
    stack = [n for n in names if n in glyph_set]
    while stack:
        name = stack.pop()
        if name in needed:
            continue
        needed.add(name)
        glyph = glyph_set[name]
        if not glyph.isComposite():
            continue
        for comp in glyph.components:
            child = comp.glyphName
            if child not in needed:
                stack.append(child)
    return {n: glyph_set[n] for n in needed if n in glyph_set}


def _init_kana_anchor_worker(cache_dir: str) -> None:
    global _ANCHOR_WORKER_CACHE_DIR
    _ANCHOR_WORKER_CACHE_DIR = cache_dir


def _kana_anchor_chunk_task(chunk_id: int) -> int:
    cache_dir = _ANCHOR_WORKER_CACHE_DIR
    if cache_dir is None:
        return chunk_id
    result_path = _anchor_chunk_result_path(cache_dir, chunk_id)
    if os.path.isfile(result_path):
        return chunk_id
    input_path = _anchor_chunk_input_path(cache_dir, chunk_id)
    with open(input_path, "rb") as f:
        state = pickle.load(f)
    glyphs = state["glyphs"]
    anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for name in state["names"]:
        slot_map = _slot_map_for_glyph(
            name,
            glyphs=glyphs,
            glyph_set=glyphs,
            target_upem=state["target_upem"],
            mark_points=state["mark_points"],
            mark_ink_height=state["mark_ink_height"],
            mark_scale=state["mark_scale"],
        )
        if slot_map is not None:
            anchors[name] = slot_map
    _write_anchor_chunk_result(cache_dir, chunk_id, anchors)
    return chunk_id


def _slot_map_for_glyph(
    name: str,
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_points: Optional[Sequence[Tuple[float, float]]],
    mark_ink_height: Optional[float],
    mark_scale: float,
) -> Optional[Dict[int, Tuple[int, int]]]:
    glyph = glyphs.get(name)
    if glyph is None:
        return None
    slots = kana_slot_anchors(
        glyph,
        glyph_set=glyph_set,
        target_upem=target_upem,
        mark_points=mark_points,
        mark_ink_height=mark_ink_height,
        mark_scale=mark_scale,
    )
    if not slots:
        return None
    return {i: slots[slot] for i, (slot, _suf) in enumerate(DAKUTEN_SLOTS)}


def _collect_kana_dakuten_anchors_parallel(
    names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_points: Optional[Sequence[Tuple[float, float]]],
    mark_ink_height: Optional[float],
    mark_scale: float,
    jobs: int,
    cache_dir: str,
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    n_chunks = min(jobs, len(names))
    chunks = _partition_anchor_names(names, n_chunks)
    chunk_jobs: List[int] = []
    for chunk_id, chunk_names in enumerate(chunks):
        _write_anchor_chunk_input(
            cache_dir,
            chunk_id,
            chunk_names,
            glyph_set=glyph_set,
            target_upem=target_upem,
            mark_points=mark_points,
            mark_ink_height=mark_ink_height,
            mark_scale=mark_scale,
        )
        chunk_jobs.append(chunk_id)
    workers = min(jobs, len(chunk_jobs))
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_kana_anchor_worker,
        initargs=(cache_dir,),
    ) as pool:
        list(pool.map(_kana_anchor_chunk_task, chunk_jobs))
    merged = _load_merged_anchor_chunks(cache_dir, names)
    return merged or {}


def _collect_kana_dakuten_anchors_sequential(
    names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_points: Optional[Sequence[Tuple[float, float]]],
    mark_ink_height: Optional[float],
    mark_scale: float,
    cache_dir: Optional[str],
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for name in names:
        slot_map = _slot_map_for_glyph(
            name,
            glyphs=glyphs,
            glyph_set=glyph_set,
            target_upem=target_upem,
            mark_points=mark_points,
            mark_ink_height=mark_ink_height,
            mark_scale=mark_scale,
        )
        if slot_map is not None:
            anchors[name] = slot_map
    if cache_dir:
        _write_anchor_chunk_result(cache_dir, 0, anchors)
    return anchors


def collect_kana_dakuten_anchors(
    base_names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_points: Optional[Sequence[Tuple[float, float]]] = None,
    mark_ink_height: Optional[float] = None,
    mark_scale: float = 1.0,
    jobs: int = 1,
    cache_dir: Optional[str] = None,
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    """Per-glyph `{mark_class: (x, y)}` for every named form that exists."""
    names = [n for n in base_names if n in glyphs]
    if not names:
        return {}
    if cache_dir:
        cached = _load_merged_anchor_chunks(cache_dir, names, require_all=True)
        if cached is not None:
            return cached
    parallel = jobs > 1 and len(names) >= max(4, jobs // 2)
    if parallel:
        if not cache_dir:
            cache_dir = tempfile.mkdtemp(prefix="edenia-kana-anchors-")
        return _collect_kana_dakuten_anchors_parallel(
            names,
            glyphs=glyphs,
            glyph_set=glyph_set,
            target_upem=target_upem,
            mark_points=mark_points,
            mark_ink_height=mark_ink_height,
            mark_scale=mark_scale,
            jobs=jobs,
            cache_dir=cache_dir,
        )
    return _collect_kana_dakuten_anchors_sequential(
        names,
        glyphs=glyphs,
        glyph_set=glyph_set,
        target_upem=target_upem,
        mark_points=mark_points,
        mark_ink_height=mark_ink_height,
        mark_scale=mark_scale,
        cache_dir=cache_dir,
    )
