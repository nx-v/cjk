"""KAGE SVG stroke renderer adapter → font outlines.

Uses the in-tree renderer under ``Scripts.kage.renderer`` (based on
HowardZorn/kage-engine, GPL-3.0).

D4 variants (rotate × reflect) transform **stroke skeletons** with tip
codes peeled off, then reattached after direction restore / L·R remap so
serifs keep calligraphic roles. Prefer ``transform_stroke_data`` +
``render_stroke_data`` over affine-flipping finished SVG contours.
"""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

from fontTools.misc.transform import Transform
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path

from .renderer import Kage as _RendererKage
from .renderer.fit_curve import fit_curve
from .renderer.font.serif import Serif

import svgwrite

REFERENCE_STROKE = 99

_TRANSFORM_RE = re.compile(
    r"(matrix|translate|scale|rotate)\s*\(\s*([^)]*)\s*\)",
    re.IGNORECASE,
)

# Contours with at least this many points are treated as flattened stroke
# ribbons eligible for Schneider curve fitting (small tip polygons skipped).
CURVE_FIT_MIN_POINTS = 8
DEFAULT_CURVE_FIT_ERROR = 4.0  # squared distance in font units


def make_engine(*, ignore_component_version: bool = False) -> _RendererKage:
    """Serif KAGE instance (filled polygon SVG paths)."""
    engine = _RendererKage(ignore_component_version=ignore_component_version)
    engine.font = Serif()
    return engine


def render_stroke_data(engine: _RendererKage, data: str) -> svgwrite.Drawing | None:
    """Render a (preferably resolved) KAGE stroke string to an SVG drawing."""
    if not data or not str(data).strip():
        return None
    canvas = svgwrite.Drawing(size=("200", "200"))
    try:
        result = engine.make_glyph_with_data(canvas, data)
    except Exception:
        return None
    return result if result is not None else canvas


def _fmt_stroke_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


# Head/tail tip bases that encode world-space left vs right (vertical strokes).
_LR_TIP_SWAP = {12: 22, 22: 12, 13: 23, 23: 13}


def _remap_tip_lr(tip: float) -> float:
    """Swap L/R tip forms, preserving option hundreds (e.g. 313 → 323)."""
    raw = int(round(tip))
    base = raw % 100
    swapped = _LR_TIP_SWAP.get(base)
    if swapped is None:
        return tip
    return float(raw - base + swapped)


def _d4_is_reflection(rot90_quarters: int, flip_x: bool, flip_y: bool) -> bool:
    """True when the D4 map reverses orientation (odd number of axis flips)."""
    del rot90_quarters  # rotations are proper; only flips affect parity
    return bool(flip_x) != bool(flip_y)


def _stroke_data_bounds(
    data: str,
) -> tuple[float, float, float, float] | None:
    """Axis-aligned bounds of all control points in resolved stroke data."""
    xs: list[float] = []
    ys: list[float] = []
    for seg in data.split("$"):
        if not seg:
            continue
        parts = seg.split(":")
        nums: list[float] = []
        ok = True
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                ok = False
                break
        if not ok or len(nums) < 7:
            continue
        if int(nums[0]) % 100 in (0, 99):
            continue
        for i in range(3, len(nums), 2):
            if i + 1 >= len(nums):
                break
            xs.append(nums[i])
            ys.append(nums[i + 1])
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _kage_d4_point_about(
    x: float,
    y: float,
    *,
    rot90_quarters: int,
    flip_x: bool,
    flip_y: bool,
    size: float,
    content_cx: float,
    content_cy: float,
) -> tuple[float, float]:
    """D4 about content center, then place that center at the design midpoint."""
    # Map into frame centered at content, apply design-space D4 about 0,
    # then shift so content center lands on size/2.
    q = rot90_quarters % 4
    dx, dy = x - content_cx, y - content_cy
    if q == 1:
        dx, dy = dy, -dx
    elif q == 2:
        dx, dy = -dx, -dy
    elif q == 3:
        dx, dy = -dy, dx
    if flip_y:
        dx = -dx
    if flip_x:
        dy = -dy
    cell = size / 2.0
    return cell + dx, cell + dy


def transform_stroke_data(
    data: str,
    *,
    rot90_quarters: int = 0,
    flip_x: bool = False,
    flip_y: bool = False,
    size: float = 200.0,
) -> str:
    """Apply a D4 transform to resolved KAGE strokes, preserving serifs.

    For each real stroke:

    1. **Peel** head/tail tip codes (``a2``/``a3``) off the skeleton.
    2. Transform control points about the glyph's content bbox center, then
       place that center at the design-frame midpoint so the result stays
       cell-centered. Point order is kept so each tip stays on its endpoint.
    3. On reflection, remap L/R tip bases (12↔22, 13↔23).
    4. **Reattach** tip codes; re-render with ``render_stroke_data``.

    Types 0 and 99 only move coordinates (no tip surgery).
    """
    q = rot90_quarters % 4
    if not data or (q == 0 and not flip_x and not flip_y):
        return data

    reflects = _d4_is_reflection(q, flip_x, flip_y)
    bounds = _stroke_data_bounds(data)
    if bounds is None:
        content_cx = content_cy = size / 2.0
    else:
        x0, y0, x1, y1 = bounds
        content_cx = (x0 + x1) / 2.0
        content_cy = (y0 + y1) / 2.0

    out_segs: list[str] = []
    for seg in data.split("$"):
        if not seg:
            continue
        parts = seg.split(":")
        nums: list[float] = []
        ok = True
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                ok = False
                break
        if not ok or len(nums) < 7:
            out_segs.append(seg)
            continue

        stroke_type = int(nums[0]) % 100
        head_tip = nums[1]
        tail_tip = nums[2]

        # Peel tips while the skeleton moves.
        if stroke_type not in (0, 99):
            nums[1] = 0.0
            nums[2] = 0.0

        for i in range(3, len(nums), 2):
            if i + 1 >= len(nums):
                break
            nums[i], nums[i + 1] = _kage_d4_point_about(
                nums[i],
                nums[i + 1],
                rot90_quarters=q,
                flip_x=flip_x,
                flip_y=flip_y,
                size=size,
                content_cx=content_cx,
                content_cy=content_cy,
            )

        if stroke_type not in (0, 99):
            if reflects:
                head_tip = _remap_tip_lr(head_tip)
                tail_tip = _remap_tip_lr(tail_tip)
            nums[1] = head_tip
            nums[2] = tail_tip

        out_segs.append(":".join(_fmt_stroke_num(v) for v in nums))
    return "$".join(out_segs)


def mirror_stroke_data(
    data: str,
    *,
    flip_x: bool = False,
    flip_y: bool = False,
    size: float = 200.0,
) -> str:
    """Mirror resolved KAGE stroke skeletons (D4 flips only).

    ``flip_x`` → ``y → size - y``; ``flip_y`` → ``x → size - x``.
    See ``transform_stroke_data`` for tip peel / reattach.
    """
    return transform_stroke_data(
        data, rot90_quarters=0, flip_x=flip_x, flip_y=flip_y, size=size
    )


def kage_mirror_transform(flip_x: bool, flip_y: bool) -> Transform:
    """Mirror affine in KAGE 200×200 space (y-down). Prefer ``mirror_stroke_data``.

    ``flip_x`` → ``y → 200 - y``; ``flip_y`` → ``x → 200 - x``.
    """
    t = Transform()
    if flip_y:
        t = t.transform(Transform(-1, 0, 0, 1, 200, 0))
    if flip_x:
        t = t.transform(Transform(1, 0, 0, -1, 0, 200))
    return t


def _parse_svg_transform(spec: str | None) -> Transform:
    """Parse a subset of SVG transform lists into a fontTools Transform."""
    t = Transform()
    if not spec:
        return t
    for kind, argstr in _TRANSFORM_RE.findall(spec):
        nums = [float(x) for x in re.split(r"[,\s]+", argstr.strip()) if x]
        kind_l = kind.lower()
        if kind_l == "translate":
            tx = nums[0] if nums else 0.0
            ty = nums[1] if len(nums) > 1 else 0.0
            t = t.transform(Transform(1, 0, 0, 1, tx, ty))
        elif kind_l == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            t = t.transform(Transform(sx, 0, 0, sy, 0, 0))
        elif kind_l == "rotate":
            angle = math.radians(nums[0] if nums else 0.0)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                t = t.transform(Transform(1, 0, 0, 1, cx, cy))
                t = t.transform(Transform().rotate(angle))
                t = t.transform(Transform(1, 0, 0, 1, -cx, -cy))
            else:
                t = t.transform(Transform().rotate(angle))
        elif kind_l == "matrix" and len(nums) >= 6:
            t = t.transform(Transform(*nums[:6]))
    return t


def _path_d(element: Any) -> str:
    if hasattr(element, "attribs") and element.attribs.get("d"):
        return str(element.attribs["d"])
    commands = getattr(element, "commands", None)
    if commands:
        return "".join(str(c) for c in commands)
    return ""


def iter_filled_paths(drawing: svgwrite.Drawing):
    """Yield ``(d, local_transform)`` for filled path elements."""
    for el in getattr(drawing, "elements", []) or []:
        if type(el).__name__ != "Path":
            continue
        d = _path_d(el)
        if not d.strip():
            continue
        attribs = getattr(el, "attribs", {}) or {}
        fill = str(attribs.get("fill", "black")).lower()
        if fill in ("none", "transparent"):
            continue
        yield d, _parse_svg_transform(attribs.get("transform"))


def draw_svg_drawing_to_pen(
    drawing: svgwrite.Drawing,
    pen,
    *,
    extra: Transform | None = None,
) -> bool:
    """Replay filled SVG paths onto a pen (KAGE coordinates).

    Skips paths whose local points land far outside the design frame
    (engine spikes from bad tip math).
    """
    drew = False
    base = extra or Transform()
    for d, local in iter_filled_paths(drawing):
        xf = base.transform(local)
        if _path_has_spike(d, local):
            continue
        target = pen if xf == Transform() else TransformPen(pen, xf)
        try:
            parse_path(d, target)
        except Exception:
            continue
        try:
            target.closePath()
        except Exception:
            try:
                pen.closePath()
            except Exception:
                pass
        drew = True
    return drew


def _path_has_spike(d: str, local: Transform) -> bool:
    """True if any point is far outside KAGE 200×200 (serifs may go to ~-10)."""
    from fontTools.pens.recordingPen import RecordingPen

    rp = RecordingPen()
    try:
        parse_path(d, TransformPen(rp, local) if local != Transform() else rp)
    except Exception:
        return False
    for _op, pts in rp.value:
        for x, y in pts:
            if x < -100 or y < -100 or x > 400 or y > 400:
                return True
    return False


def _recording_contours(
    d: str, local: Transform
) -> list[tuple[list[tuple[float, float]], bool]]:
    """Extract closed polyline contours ``(points, only_lines)`` from a path."""
    from fontTools.pens.recordingPen import RecordingPen

    rp = RecordingPen()
    try:
        parse_path(d, TransformPen(rp, local) if local != Transform() else rp)
    except Exception:
        return []

    contours: list[tuple[list[tuple[float, float]], bool]] = []
    pts: list[tuple[float, float]] = []
    only_lines = True
    for op, args in rp.value:
        if op == "moveTo":
            if pts:
                contours.append((pts, only_lines))
            pts = [args[0]]
            only_lines = True
        elif op == "lineTo":
            pts.append(args[0])
        elif op in ("qCurveTo", "curveTo"):
            only_lines = False
            pts.extend(args)
        elif op == "closePath":
            if pts:
                contours.append((pts, only_lines))
            pts = []
            only_lines = True
        elif op == "endPath":
            if pts:
                contours.append((pts, only_lines))
            pts = []
            only_lines = True
    if pts:
        contours.append((pts, only_lines))
    return contours


def fit_polyline_contour(
    points: Sequence[Sequence[float]],
    *,
    max_error: float = DEFAULT_CURVE_FIT_ERROR,
    min_points: int = CURVE_FIT_MIN_POINTS,
) -> list[list[list[float]]] | None:
    """Fit a closed polygonal contour to cubics, or ``None`` to keep polygon.

    Small tip polygons (few points) are left alone so serifs stay sharp.
    """
    pts = [list(p) for p in points]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < min_points:
        return None
    # Closed ring: repeat first point so the fit covers the seam.
    ring = pts + [pts[0]]
    try:
        curves = fit_curve(ring, max_error)
    except Exception:
        return None
    return curves if curves else None


def draw_path_with_optional_curve_fit(
    d: str,
    local: Transform,
    pen,
    *,
    curve_fit: bool = False,
    max_error: float = DEFAULT_CURVE_FIT_ERROR,
    min_points: int = CURVE_FIT_MIN_POINTS,
) -> bool:
    """Replay a filled SVG path onto ``pen``, optionally curve-fitting polygons."""
    if not curve_fit:
        target = pen if local == Transform() else TransformPen(pen, local)
        try:
            parse_path(d, target)
        except Exception:
            return False
        try:
            target.closePath()
        except Exception:
            try:
                pen.closePath()
            except Exception:
                pass
        return True

    drew = False
    for pts, only_lines in _recording_contours(d, local):
        if not pts:
            continue
        curves = (
            fit_polyline_contour(pts, max_error=max_error, min_points=min_points)
            if only_lines
            else None
        )
        try:
            if curves:
                pen.moveTo((curves[0][0][0], curves[0][0][1]))
                for bez in curves:
                    # [p0, c1, c2, p1]
                    pen.curveTo(
                        (bez[1][0], bez[1][1]),
                        (bez[2][0], bez[2][1]),
                        (bez[3][0], bez[3][1]),
                    )
                pen.closePath()
            else:
                pen.moveTo(pts[0])
                for p in pts[1:]:
                    pen.lineTo(p)
                pen.closePath()
            drew = True
        except Exception:
            continue
    return drew


Kage = _RendererKage
