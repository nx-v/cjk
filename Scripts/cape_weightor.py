# -*- coding: utf-8 -*-
"""CAPE Weightor — headless Width / Weight ops for font builds.

Ports the algorithms from the GlyphsApp CAPE Weightor plugin (v1.212,
Cape Arcona / Thomas Schostok) without vanilla/AppKit UI.

* **Width mode** — horizontal stretch/condense with stem compensation
* **Weight mode** — bolden/lighten via OffsetCurve, then restore outer box

``GlyphsFilterOffsetCurve`` is replaced by contour-normal point offsets
(TrueType winding-aware). Build scripts use::

    apply_width(layer, factor)   # factor = target/original outer width
    apply_weight(layer, factor)  # factor > 1 bolden, < 1 lighten

Convert with ``layer_from_ttglyph`` / ``ttglyph_from_layer``.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from fontTools.pens.basePen import BasePen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

VERSION = "1.212-headless"

Point = Tuple[float, float]
Affine = Tuple[float, float, float, float, float, float]  # a b c d e f (SVG)


# ── Minimal Glyphs-like types ───────────────────────────────────────────────


@dataclass
class NSPoint:
    x: float = 0.0
    y: float = 0.0

    def __iter__(self):
        yield self.x
        yield self.y


NODE_LINE = "line"
NODE_CURVE = "curve"
NODE_OFFCURVE = "offcurve"


@dataclass
class GSNode:
    position: NSPoint = field(default_factory=NSPoint)
    type: str = NODE_LINE  # line | curve | offcurve

    @property
    def x(self) -> float:
        return self.position.x

    @x.setter
    def x(self, v: float) -> None:
        self.position.x = float(v)

    @property
    def y(self) -> float:
        return self.position.y

    @y.setter
    def y(self, v: float) -> None:
        self.position.y = float(v)

    def copy(self) -> "GSNode":
        return GSNode(NSPoint(self.x, self.y), self.type)


@dataclass
class GSAnchor:
    name: str = ""
    position: NSPoint = field(default_factory=NSPoint)

    @property
    def x(self) -> float:
        return self.position.x

    @x.setter
    def x(self, v: float) -> None:
        self.position.x = float(v)

    @property
    def y(self) -> float:
        return self.position.y

    @y.setter
    def y(self, v: float) -> None:
        self.position.y = float(v)


@dataclass
class GSGuide:
    position: NSPoint = field(default_factory=NSPoint)
    angle: float = 0.0
    name: str = ""


@dataclass
class GSPath:
    nodes: List[GSNode] = field(default_factory=list)
    closed: bool = True

    def copy(self) -> "GSPath":
        p = GSPath(closed=self.closed)
        p.nodes = [n.copy() for n in self.nodes]
        return p


@dataclass
class _Bounds:
    origin: NSPoint
    size: NSPoint  # size.x = width, size.y = height


class _ShapeList:
    """``layer.shapes`` façade: append/remove paths (components ignored)."""

    def __init__(self, layer: "GSLayer") -> None:
        self._layer = layer

    def append(self, obj) -> None:
        if isinstance(obj, GSPath):
            self._layer.paths.append(obj)

    def remove(self, obj) -> None:
        if isinstance(obj, GSPath) and obj in self._layer.paths:
            self._layer.paths.remove(obj)

    def __iter__(self):
        return iter(self._layer.paths)


class GSLayer:
    """Minimal layer: paths, anchors, guides, metrics, applyTransform, bounds."""

    def __init__(self) -> None:
        self.paths: List[GSPath] = []
        self.anchors: List[GSAnchor] = []
        self.guides: List[GSGuide] = []
        self.components: List[object] = []
        self._width: float = 0.0
        self._lsb: float = 0.0
        self.background = None
        self.backgroundImage = None
        self.parent = None
        self.associatedMasterId = None
        self.selection = None

    @property
    def shapes(self) -> _ShapeList:
        return _ShapeList(self)

    @property
    def width(self) -> float:
        return self._width

    @width.setter
    def width(self, v: float) -> None:
        self._width = float(v)

    @property
    def LSB(self) -> float:
        return self._lsb

    @LSB.setter
    def LSB(self, v: float) -> None:
        self._lsb = float(v)

    @property
    def RSB(self) -> float:
        try:
            b = self.bounds
            ink_right = b.origin.x + b.size.x
        except Exception:
            ink_right = 0.0
        return self._width - ink_right

    @RSB.setter
    def RSB(self, v: float) -> None:
        try:
            b = self.bounds
            ink_right = b.origin.x + b.size.x
        except Exception:
            ink_right = 0.0
        self._width = ink_right + float(v)

    @property
    def bounds(self) -> _Bounds:
        xs: List[float] = []
        ys: List[float] = []
        for path in self.paths:
            for n in path.nodes:
                xs.append(n.x)
                ys.append(n.y)
        if not xs:
            return _Bounds(NSPoint(0, 0), NSPoint(0, 0))
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        return _Bounds(NSPoint(x0, y0), NSPoint(x1 - x0, y1 - y0))

    def applyTransform(self, t: Affine) -> None:
        a, b, c, d, e, f = (float(x) for x in t)

        def _xf(x: float, y: float) -> Point:
            return (a * x + c * y + e, b * x + d * y + f)

        for path in self.paths:
            for n in path.nodes:
                nx, ny = _xf(n.x, n.y)
                n.x, n.y = nx, ny
        for anc in self.anchors:
            nx, ny = _xf(anc.x, anc.y)
            anc.x, anc.y = nx, ny
        for g in self.guides:
            nx, ny = _xf(g.position.x, g.position.y)
            g.position.x, g.position.y = nx, ny


# ── TTGlyph ↔ GSLayer ───────────────────────────────────────────────────────


class _LayerBuildPen(BasePen):
    """Record TrueType outlines into GSPath lists (quadratic → on/off nodes)."""

    def __init__(self, layer: GSLayer) -> None:
        super().__init__(None)
        self.layer = layer
        self._path: Optional[GSPath] = None
        self._start: Optional[Point] = None

    def _moveTo(self, pt) -> None:
        self._path = GSPath(closed=False)
        self._start = (float(pt[0]), float(pt[1]))
        self._path.nodes.append(GSNode(NSPoint(*self._start), NODE_LINE))

    def _lineTo(self, pt) -> None:
        if self._path is None:
            return
        self._path.nodes.append(GSNode(NSPoint(float(pt[0]), float(pt[1])), NODE_LINE))

    def _qCurveToOne(self, b, c) -> None:
        if self._path is None:
            return
        self._path.nodes.append(
            GSNode(NSPoint(float(b[0]), float(b[1])), NODE_OFFCURVE)
        )
        self._path.nodes.append(GSNode(NSPoint(float(c[0]), float(c[1])), NODE_CURVE))

    def _curveToOne(self, b, c, d) -> None:
        if self._path is None:
            return
        a = (self._path.nodes[-1].x, self._path.nodes[-1].y)
        b = (float(b[0]), float(b[1]))
        c = (float(c[0]), float(c[1]))
        d = (float(d[0]), float(d[1]))
        mid = (
            0.125 * a[0] + 0.375 * b[0] + 0.375 * c[0] + 0.125 * d[0],
            0.125 * a[1] + 0.375 * b[1] + 0.375 * c[1] + 0.125 * d[1],
        )
        c1 = (
            a[0] + 0.75 * (b[0] - a[0]),
            a[1] + 0.75 * (b[1] - a[1]),
        )
        c2 = (
            d[0] + 0.75 * (c[0] - d[0]),
            d[1] + 0.75 * (c[1] - d[1]),
        )
        self._path.nodes.append(GSNode(NSPoint(*c1), NODE_OFFCURVE))
        self._path.nodes.append(GSNode(NSPoint(*mid), NODE_CURVE))
        self._path.nodes.append(GSNode(NSPoint(*c2), NODE_OFFCURVE))
        self._path.nodes.append(GSNode(NSPoint(*d), NODE_CURVE))

    def _closePath(self) -> None:
        if self._path is None:
            return
        self._path.closed = True
        if len(self._path.nodes) >= 2:
            first, last = self._path.nodes[0], self._path.nodes[-1]
            if (
                last.type != NODE_OFFCURVE
                and abs(first.x - last.x) < 1e-6
                and abs(first.y - last.y) < 1e-6
            ):
                self._path.nodes.pop()
        self.layer.paths.append(self._path)
        self._path = None
        self._start = None

    def _endPath(self) -> None:
        if self._path is not None:
            self._path.closed = False
            self.layer.paths.append(self._path)
            self._path = None
            self._start = None


def layer_from_ttglyph(glyph: TTGlyph, advance: float) -> GSLayer:
    """Decompose a TrueType glyph into a ``GSLayer`` (no components kept)."""
    layer = GSLayer()
    layer.width = float(advance)
    pen = _LayerBuildPen(layer)
    try:
        glyph.draw(pen, None)
    except Exception:
        return layer
    try:
        glyph.recalcBounds(None)
        layer.LSB = float(glyph.xMin)
    except Exception:
        b = layer.bounds
        layer.LSB = b.origin.x
    return layer


def ttglyph_from_layer(layer: GSLayer) -> Tuple[TTGlyph, int, int]:
    """Build a TT glyph from layer paths; return ``(glyph, advance, lsb)``."""
    pen = TTGlyphPen(None)
    for path in layer.paths:
        nodes = path.nodes
        if not nodes:
            continue
        i0 = 0
        while i0 < len(nodes) and nodes[i0].type == NODE_OFFCURVE:
            i0 += 1
        if i0 >= len(nodes):
            continue
        ordered = nodes[i0:] + nodes[:i0]
        pen.moveTo((ordered[0].x, ordered[0].y))
        i = 1
        while i < len(ordered):
            n = ordered[i]
            if n.type == NODE_OFFCURVE:
                offs = []
                while i < len(ordered) and ordered[i].type == NODE_OFFCURVE:
                    offs.append((ordered[i].x, ordered[i].y))
                    i += 1
                if i < len(ordered):
                    end = (ordered[i].x, ordered[i].y)
                    pen.qCurveTo(*(offs + [end]))
                    i += 1
                else:
                    pen.qCurveTo(*(offs + [(ordered[0].x, ordered[0].y)]))
            else:
                pen.lineTo((n.x, n.y))
                i += 1
        if path.closed:
            pen.closePath()
        else:
            pen.endPath()
    glyph = pen.glyph()
    try:
        glyph.recalcBounds(None)
        lsb = int(round(glyph.xMin))
    except Exception:
        lsb = int(round(layer.LSB))
    advance = int(round(layer.width))
    return glyph, advance, lsb


# ── Stem estimate ───────────────────────────────────────────────────────────


def estimate_vertical_stem(
    layer: GSLayer,
    *,
    samples: int = 48,
    max_frac: float = 0.35,
) -> float:
    """Median thin odd–even scanline span — proxy for vertical stem thickness."""
    b = layer.bounds
    width, height = b.size.x, b.size.y
    if width <= 1e-6 or height <= 1e-6:
        return 0.0
    y0 = b.origin.y

    edges: List[Tuple[Point, Point]] = []
    for path in layer.paths:
        pts = [(n.x, n.y) for n in path.nodes]
        if len(pts) < 2:
            continue
        for i in range(len(pts)):
            a = pts[i]
            c = (
                pts[(i + 1) % len(pts)]
                if path.closed
                else (pts[i + 1] if i + 1 < len(pts) else None)
            )
            if c is None:
                break
            edges.append((a, c))

    spans: List[float] = []
    for i in range(1, samples + 1):
        y = y0 + height * i / (samples + 1)
        xs: List[float] = []
        for (ax, ay), (bx, by) in edges:
            if abs(ay - by) < 1e-9:
                continue
            lo, hi = (ay, by) if ay < by else (by, ay)
            if y < lo or y > hi:
                continue
            t = (y - ay) / (by - ay)
            xs.append(ax + t * (bx - ax))
        xs.sort()
        for j in range(0, len(xs) - 1, 2):
            w = xs[j + 1] - xs[j]
            if 0.0 < w < max_frac * width:
                spans.append(w)
    if not spans:
        return 0.0
    return float(statistics.median(spans))


# ── OffsetCurve stand-in (contour-normal) ───────────────────────────────────


def _unit(dx: float, dy: float) -> Point:
    L = math.hypot(dx, dy)
    if L < 1e-12:
        return (0.0, 0.0)
    return (dx / L, dy / L)


def _offset_path(path: GSPath, offset_x: float, offset_y: float) -> None:
    """Move each node along averaged on-curve normals (Glyphs-like OffsetCurve)."""
    nodes = path.nodes
    n = len(nodes)
    if n < 2:
        return

    pts = [(nd.x, nd.y) for nd in nodes]
    on_idx = [i for i, nd in enumerate(nodes) if nd.type != NODE_OFFCURVE]
    if len(on_idx) < 2:
        on_idx = list(range(n))

    def _fill_expand_normal(dx: float, dy: float) -> Point:
        """Right normal of travel — expands fill for outer-CCW / hole-CW glyf."""
        ux, uy = _unit(dx, dy)
        return (uy, -ux)

    on_off: dict[int, Point] = {}
    m = len(on_idx)
    for k, i in enumerate(on_idx):
        if path.closed:
            ip = on_idx[k - 1]
            inn = on_idx[(k + 1) % m]
        else:
            ip = on_idx[max(k - 1, 0)]
            inn = on_idx[min(k + 1, m - 1)]
        d0 = _fill_expand_normal(pts[i][0] - pts[ip][0], pts[i][1] - pts[ip][1])
        d1 = _fill_expand_normal(pts[inn][0] - pts[i][0], pts[inn][1] - pts[i][1])
        ux, uy = _unit(d0[0] + d1[0], d0[1] + d1[1])
        if ux == 0.0 and uy == 0.0:
            ux, uy = d1 if (d1[0] or d1[1]) else d0
        on_off[i] = (ux * offset_x, uy * offset_y)

    new_pts: List[Point] = []
    for i in range(n):
        if i in on_off:
            ox, oy = on_off[i]
        else:
            prev_on = next(
                (on_idx[k] for k in range(m - 1, -1, -1) if on_idx[k] < i), None
            )
            next_on = next((on_idx[k] for k in range(m) if on_idx[k] > i), None)
            if path.closed:
                if prev_on is None:
                    prev_on = on_idx[-1]
                if next_on is None:
                    next_on = on_idx[0]
            if prev_on is None and next_on is None:
                ox = oy = 0.0
            elif prev_on is None:
                ox, oy = on_off[next_on]
            elif next_on is None:
                ox, oy = on_off[prev_on]
            else:
                span = next_on - prev_on
                t = (i - prev_on) / span if span else 0.5
                a, b = on_off[prev_on], on_off[next_on]
                ox = a[0] * (1 - t) + b[0] * t
                oy = a[1] * (1 - t) + b[1] * t
        new_pts.append((pts[i][0] + ox, pts[i][1] + oy))

    for i, nd in enumerate(nodes):
        nd.x, nd.y = new_pts[i]


def offset_layer(
    layer: GSLayer,
    offset_x: float,
    offset_y: float,
    position: float = 0.5,
) -> None:
    """Symmetric OffsetCurve stand-in (``position`` kept for API parity)."""
    del position
    if abs(offset_x) < 1e-9 and abs(offset_y) < 1e-9:
        return
    for path in layer.paths:
        _offset_path(path, offset_x, offset_y)


# ── Width / Weight closed forms ─────────────────────────────────────────────


def width_scale_params(
    width: float,
    factor: float,
    stem: float,
) -> Tuple[bool, float, float]:
    """Return ``(do_scale, s, offset_per_side)`` for Width mode."""
    if width <= 0 or abs(factor - 1.0) < 1e-9:
        return False, 1.0, 0.0
    w_target = width * factor
    if stem <= 0:
        return True, factor, 0.0
    if (width - stem) <= 0:
        return False, 1.0, 0.0
    s = (w_target - stem) / (width - stem)
    if s < 0.05:
        s = 0.05
    offset_per_side = stem * (1.0 - s) / 2.0
    return True, s, offset_per_side


def apply_width(
    layer: GSLayer,
    factor: float,
    *,
    stem: Optional[float] = None,
    preserve_sidebearings: bool = False,
    center_x: Optional[float] = None,
) -> None:
    """Width mode: stretch/condense horizontally, keep vertical stem thickness."""
    if abs(factor - 1.0) < 1e-9:
        return
    b = layer.bounds
    up_x, up_w = b.origin.x, b.size.x
    if up_w <= 1e-6:
        return

    orig_lsb, orig_rsb = layer.LSB, layer.RSB
    n = float(stem) if stem is not None else estimate_vertical_stem(layer)
    do_scale, s, offset_per_side = width_scale_params(up_w, factor, n)
    if not do_scale:
        return

    tx = up_x * (1.0 - s)
    layer.applyTransform((s, 0, 0, 1, tx, 0))
    if abs(offset_per_side) > 1e-6:
        # Headless right-normal OffsetCurve: positive X expands fill. Weightor's
        # offset_per_side is Glyphs-signed (negative when expanding); flip it.
        offset_layer(layer, -offset_per_side, 0.0)

    if preserve_sidebearings:
        layer.LSB = orig_lsb
        layer.RSB = orig_rsb
    else:
        layer.LSB = orig_lsb * factor
        layer.RSB = orig_rsb * factor

    if center_x is not None:
        nb = layer.bounds
        mid = nb.origin.x + 0.5 * nb.size.x
        dx = center_x - mid
        if abs(dx) > 1e-6:
            layer.applyTransform((1, 0, 0, 1, dx, 0))
            layer.LSB = layer.LSB + dx


def apply_weight(
    layer: GSLayer,
    factor: float,
    *,
    stem: Optional[float] = None,
    preserve_width: bool = True,
    preserve_height: bool = True,
) -> None:
    """Weight mode: bolden (``factor > 1``) or lighten (``factor < 1``)."""
    if abs(factor - 1.0) < 1e-9:
        return
    b = layer.bounds
    orig_x, orig_y = b.origin.x, b.origin.y
    orig_w, orig_h = b.size.x, b.size.y
    if orig_w <= 1e-6 or orig_h <= 1e-6:
        return

    n = float(stem) if stem is not None else estimate_vertical_stem(layer)
    if n <= 0:
        n = 0.1 * min(orig_w, orig_h)
    offset = n * (factor - 1.0) / 2.0
    if abs(offset) < 1e-6:
        return

    offset_layer(layer, offset, offset)

    if preserve_height or preserve_width:
        nb = layer.bounds
        nw, nh = nb.size.x, nb.size.y
        if nw <= 1e-6 or nh <= 1e-6:
            return
        sx = orig_w / nw if preserve_width else 1.0
        sy = orig_h / nh if preserve_height else 1.0
        tx = orig_x - nb.origin.x * sx if preserve_width else 0.0
        ty = orig_y - nb.origin.y * sy if preserve_height else 0.0
        if (
            abs(sx - 1.0) > 1e-9
            or abs(sy - 1.0) > 1e-9
            or abs(tx) > 1e-6
            or abs(ty) > 1e-6
        ):
            layer.applyTransform((sx, 0, 0, sy, tx, ty))

    if preserve_width:
        try:
            layer.LSB = layer.bounds.origin.x
        except Exception:
            pass


def widen_ttglyph(
    glyph: TTGlyph,
    factor: float,
    *,
    advance: Optional[float] = None,
    stem: Optional[float] = None,
    center_x: Optional[float] = None,
) -> Tuple[TTGlyph, int, int]:
    """Width-mode widen a TT glyph; returns ``(glyph, advance, lsb)``."""
    if advance is None:
        try:
            glyph.recalcBounds(None)
            advance = float(glyph.xMax) if glyph.xMax > 0 else 1000.0
        except Exception:
            advance = 1000.0
    layer = layer_from_ttglyph(glyph, advance)
    apply_width(layer, factor, stem=stem, center_x=center_x)
    return ttglyph_from_layer(layer)


def bolden_ttglyph(
    glyph: TTGlyph,
    factor: float,
    *,
    advance: Optional[float] = None,
    stem: Optional[float] = None,
) -> Tuple[TTGlyph, int, int]:
    """Weight-mode bolden/lighten a TT glyph; returns ``(glyph, advance, lsb)``."""
    if advance is None:
        try:
            glyph.recalcBounds(None)
            advance = float(max(glyph.xMax, 0)) + 100.0
        except Exception:
            advance = 1000.0
    layer = layer_from_ttglyph(glyph, advance)
    adv0 = layer.width
    apply_weight(layer, factor, stem=stem)
    layer.width = adv0
    return ttglyph_from_layer(layer)
