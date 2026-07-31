"""KAGE SVG stroke renderer adapter → font outlines.

Uses the in-tree renderer under ``Scripts.kage.renderer`` (based on
HowardZorn/kage-engine, GPL-3.0).
"""

from __future__ import annotations

import math
import re
from typing import Any

from fontTools.misc.transform import Transform
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path

from .renderer import Kage as _RendererKage
from .renderer.font.serif import Serif

import svgwrite

REFERENCE_STROKE = 99

_TRANSFORM_RE = re.compile(
    r"(matrix|translate|scale|rotate)\s*\(\s*([^)]*)\s*\)",
    re.IGNORECASE,
)


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


def _reverse_stroke_points(nums: list[float]) -> None:
    """Reverse control-point order only (keep head/tail tip roles)."""
    if len(nums) < 7:
        return
    coords = nums[3:]
    if len(coords) % 2 == 1:
        coords.append(0.0)
    pairs = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]
    pairs.reverse()
    flat = [c for p in pairs for c in p]
    nums[3:] = flat[: len(nums) - 3]


def _reverse_stroke_nums(nums: list[float]) -> None:
    """Reverse stroke direction: swap tip forms and control-point order.

    For horizontal / diagonal strokes after a single-axis flip, this restores
    left/right relative to travel. Do not use alone on vertical strokes whose
    tips are head-only (12/22) vs tail-only (13/23).
    """
    if len(nums) < 7:
        return
    nums[1], nums[2] = nums[2], nums[1]
    _reverse_stroke_points(nums)


def _stroke_endpoints(nums: list[float]) -> tuple[float, float, float, float]:
    """First and last control points (x1, y1, x2, y2)."""
    coords = nums[3:]
    if len(coords) % 2 == 1:
        coords = coords + [0.0]
    return coords[0], coords[1], coords[-2], coords[-1]


def mirror_stroke_data(
    data: str,
    *,
    flip_x: bool = False,
    flip_y: bool = False,
    size: float = 200.0,
) -> str:
    """Mirror resolved KAGE stroke skeletons in design space (y-down).

    ``flip_x`` → ``y → size - y`` (mirror across horizontal axis).
    ``flip_y`` → ``x → size - x`` (mirror across vertical axis).

    After flipping coordinates:

    * Horizontal / diagonal (single-axis): reverse direction (swap ``a2``/``a3``
      and control points) so connectors stay travel-relative.
    * Vertical-ish: keep head/tail tip roles. Y-mirror reverses points only
      (restore top→bottom). X-mirror leaves direction alone and remaps L/R tip
      codes (12↔22, 13↔23). Both-axes: restore top→bottom, then L/R remap.

    Re-render with ``render_stroke_data`` (do not affine-flip SVG contours).
    """
    if not data or not (flip_x or flip_y):
        return data
    single_axis = flip_x != flip_y
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
        # cols: type, a2, a3, x1,y1, x2,y2, [x3,y3, x4,y4, ...]
        for i in range(3, len(nums), 2):
            if flip_y:
                nums[i] = size - nums[i]
            if i + 1 < len(nums) and flip_x:
                nums[i + 1] = size - nums[i + 1]
        stroke_type = int(nums[0]) % 100
        if stroke_type not in (0, 99):
            x1, y1, x2, y2 = _stroke_endpoints(nums)
            vertical = abs(y2 - y1) >= abs(x2 - x1)
            if vertical:
                # Head tips (12/22) must stay on start; heel tips (13/23) on end.
                if flip_x and y1 > y2:
                    # Y-mirror (or both) turned the stem upward — restore top→bottom.
                    _reverse_stroke_points(nums)
                # X-mirror alone: direction already top→bottom; L/R remap below.
            elif single_axis:
                _reverse_stroke_nums(nums)
            if flip_y:
                nums[1] = _remap_tip_lr(nums[1])
                nums[2] = _remap_tip_lr(nums[2])
        out_segs.append(":".join(_fmt_stroke_num(v) for v in nums))
    return "$".join(out_segs)


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


Kage = _RendererKage
