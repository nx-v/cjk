"""Build GlyphWiki PUA ligature fonts (32 000 glyphs per SPUA marker).

Each output TTF corresponds to one Supplementary PUA marker and contains:

* 6 400 BMP PUA selector glyphs (U+E000..U+F8FF, zero-width)
* 6 400 × 4 = 25 600 rendered outlines (identity + 3 mirrors)

Total = 32 000 glyphs. GSUB:

* ``marker + pua`` → identity outline
* ``identity + VS02..VS04`` → mirrored outlines

Rendering uses the in-tree KAGE Serif renderer (filled SVG paths), then
Cu2Qu for TrueType. Contours are normalized to clockwise winding so
overlaps fill solidly under nonzero fill.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path
from fontTools.ttLib import woff2
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from .engine import (
    Kage,
    _path_has_spike,
    iter_filled_paths,
    kage_mirror_transform,
    make_engine,
    render_stroke_data,
)
from .mapping import (
    BMP_PUA_COUNT,
    BMP_PUA_START,
    GlyphMapping,
    MirrorVS,
)

# One SPUA-marker font: 6400 PUA selectors + 6400*4 rendered variants
PUA_SELECTORS = BMP_PUA_COUNT  # 6400
MIRROR_FORMS = 4  # identity, mx, my, mxy
RENDERED_PER_MARKER = PUA_SELECTORS * MIRROR_FORMS  # 25600
GLYPHS_PER_FILE = PUA_SELECTORS + RENDERED_PER_MARKER  # 32000
GLYPHS_PER_FILE_NO_MIRROR = PUA_SELECTORS + PUA_SELECTORS  # 12800


def glyphs_per_file(*, include_mirrors: bool = True) -> int:
    return GLYPHS_PER_FILE if include_mirrors else GLYPHS_PER_FILE_NO_MIRROR

DEFAULT_UPEM = 1000

# Mirror forms: (mode, flip_x, flip_y, name suffix or None for identity)
MIRROR_FORMS_SPEC: list[tuple[int, bool, bool, str | None]] = [
    (MirrorVS.IDENTITY, False, False, None),
    (MirrorVS.FLIP_X, True, False, "mx"),
    (MirrorVS.FLIP_Y, False, True, "my"),
    (MirrorVS.FLIP_BOTH, True, True, "mxy"),
]


def empty_glyph() -> TTGlyph:
    g = TTGlyph()
    g.numberOfContours = 0
    g.xMin = g.yMin = g.xMax = g.yMax = 0
    return g


def kage_to_font_transform(upem: int = DEFAULT_UPEM) -> Transform:
    """Affine map from KAGE 200×200 (y-down) to font space (y-up, ``upem``)."""
    scale = upem / 200.0
    return Transform(scale, 0, 0, -scale, 0, upem)


def _round_skia_path(path, round_fn=otRound):
    """Round pathops path coords (helps skia-pathops simplify)."""
    import pathops

    rounded = pathops.Path()
    for contour in path.contours:
        for verb, pts in contour:
            rpts = [(round_fn(x), round_fn(y)) for x, y in pts]
            if verb == pathops.PathVerb.MOVE:
                rounded.moveTo(*rpts[0])
            elif verb == pathops.PathVerb.LINE:
                rounded.lineTo(*rpts[0])
            elif verb == pathops.PathVerb.QUAD:
                rounded.quadTo(*rpts[0], *rpts[1])
            elif verb == pathops.PathVerb.CUBIC:
                rounded.cubicTo(*rpts[0], *rpts[1], *rpts[2])
            elif verb == pathops.PathVerb.CLOSE:
                rounded.close()
    return rounded


def _drop_spike_contours(path, *, max_edge_ratio: float = 8.0, upem: int = 1000):
    """Drop contours dominated by one absurdly long edge (flatten artifacts)."""
    import math
    import pathops

    kept = pathops.Path()
    kept_any = False
    for contour in path.contours:
        pts: list[tuple[float, float]] = []
        for verb, vpts in contour:
            pts.extend(vpts)
        if len(pts) < 3:
            continue
        edges = []
        for i in range(len(pts)):
            a = pts[i]
            b = pts[(i + 1) % len(pts)]
            edges.append(math.hypot(b[0] - a[0], b[1] - a[1]))
        longest = max(edges)
        median = sorted(edges)[len(edges) // 2] or 1.0
        if longest > max(upem * 0.55, median * max_edge_ratio):
            continue
        for verb, vpts in contour:
            if verb == pathops.PathVerb.MOVE:
                kept.moveTo(*vpts[0])
            elif verb == pathops.PathVerb.LINE:
                kept.lineTo(*vpts[0])
            elif verb == pathops.PathVerb.QUAD:
                kept.quadTo(*vpts[0], *vpts[1])
            elif verb == pathops.PathVerb.CUBIC:
                kept.cubicTo(*vpts[0], *vpts[1], *vpts[2])
            elif verb == pathops.PathVerb.CLOSE:
                kept.close()
        kept_any = True
    return kept if kept_any else path


def svg_drawing_to_ttglyph(
    drawing,
    upem: int = DEFAULT_UPEM,
    *,
    flip_x: bool = False,
    flip_y: bool = False,
    flatten: bool = False,
    max_err: float = 0.5,
) -> TTGlyph | None:
    """Convert a KAGE SVG drawing to a TrueType glyph.

    Contours are forced to a uniform clockwise winding in font space so
    overlapping stroke ribbons fill solidly (nonzero winding). Opposite
    windings otherwise punch white holes at joints.

    ``flatten`` (pathops.simplify) stays off by default: boolean union of
    Serif ribbons collapses to a solid black square over the glyph bbox.
    """
    if drawing is None:
        return None

    to_font = kage_to_font_transform(upem)
    mirror = kage_mirror_transform(flip_x, flip_y)
    combined = to_font.transform(mirror)

    if flatten:
        try:
            import pathops
            from fontTools.ttLib.removeOverlaps import ttfGlyphFromSkPath
        except ImportError:
            flatten = False
        else:
            sk = pathops.Path()
            if not _draw_svg_normalized(
                drawing, sk.getPen(), combined, clockwise=True
            ):
                return None
            try:
                sk = pathops.simplify(sk, fix_winding=True, clockwise=False)
            except pathops.PathOpsError:
                try:
                    sk = pathops.simplify(
                        _round_skia_path(sk), fix_winding=True, clockwise=False
                    )
                except pathops.PathOpsError:
                    pass
            sk = _drop_spike_contours(sk, upem=upem)
            pen = TTGlyphPen(None)
            try:
                sk.draw(Cu2QuPen(pen, max_err))
                glyph = pen.glyph()
            except Exception:
                glyph = ttfGlyphFromSkPath(sk)
            if glyph.numberOfContours == 0:
                return None
            try:
                glyph.recalcBounds(None)
            except Exception:
                pass
            return glyph

    pen = TTGlyphPen(None)
    if not _draw_svg_normalized(
        drawing, Cu2QuPen(pen, max_err), combined, clockwise=True
    ):
        return None
    glyph = pen.glyph()
    if glyph.numberOfContours == 0:
        return None
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    return glyph


def _draw_svg_normalized(
    drawing,
    pen,
    font_transform: Transform,
    *,
    clockwise: bool = True,
) -> bool:
    """Draw SVG paths in font space, reversing any contour with wrong winding."""
    import pathops

    drew = False
    for d, local in iter_filled_paths(drawing):
        if _path_has_spike(d, local):
            continue
        piece = pathops.Path()
        composed = font_transform.transform(local)
        try:
            parse_path(d, TransformPen(piece.getPen(), composed))
        except Exception:
            continue
        try:
            piece.close()
        except Exception:
            pass
        if len(list(piece.contours)) == 0:
            continue
        try:
            if bool(piece.clockwise) != clockwise:
                piece.reverse()
        except Exception:
            pass
        try:
            piece.draw(pen)
        except Exception:
            continue
        drew = True
    return drew


def pua_glyph_name(pua: int) -> str:
    return f"pua{pua:04X}"


def marker_glyph_name(marker: int) -> str:
    return f"mk{marker:05X}"


def result_glyph_name(pua: int, suffix: str | None = None) -> str:
    base = f"g{pua:04X}"
    return f"{base}.{suffix}" if suffix else base


def vs_glyph_name(mode: int) -> str:
    return f"vs{mode + 1:02d}"


def build_marker_font(
    marker: int,
    mappings: Sequence[GlyphMapping],
    stroke_data: dict[str, str],
    out_path: Path,
    *,
    upem: int = DEFAULT_UPEM,
    kage: Kage | None = None,
    write_woff2: bool = True,
    include_mirrors: bool = True,
) -> tuple[int, int]:
    """Build one font for a single SPUA ``marker``.

    ``mappings`` must all share ``marker`` and cover up to 6400 PUA slots.
    ``stroke_data`` maps glyph name → resolved KAGE stroke string.

    With ``include_mirrors`` (default), each slot gets identity + 3 mirrors
    (32 000 glyphs). Without mirrors, only identity outlines are stored
    (12 800 glyphs).

    Returns ``(rendered_count, total_glyphs)``.
    """
    if kage is None:
        kage = make_engine()

    by_pua = {m.pua: m for m in mappings if m.marker == marker}
    if not by_pua:
        return 0, 0

    glyph_order = [".notdef"]
    glyphs: dict[str, TTGlyph] = {".notdef": empty_glyph()}
    metrics: dict[str, tuple[int, int]] = {".notdef": (upem // 2, 0)}
    cmap: dict[int, str] = {}
    liga_rules: list[str] = []
    rlig_rules: list[str] = []

    # SPUA marker (zero-width)
    mk_name = marker_glyph_name(marker)
    glyph_order.append(mk_name)
    glyphs[mk_name] = empty_glyph()
    metrics[mk_name] = (0, 0)
    cmap[marker] = mk_name

    # All 6400 BMP PUA selectors (zero-width), including VS01..VS04 slots
    for i in range(BMP_PUA_COUNT):
        pua = BMP_PUA_START + i
        pname = pua_glyph_name(pua)
        glyph_order.append(pname)
        glyphs[pname] = empty_glyph()
        metrics[pname] = (0, 0)
        cmap[pua] = pname

    rendered = 0
    advance = upem  # full em square for CJK-like cells

    for i in range(BMP_PUA_COUNT):
        pua = BMP_PUA_START + i
        mapping = by_pua.get(pua)
        data = stroke_data.get(mapping.name) if mapping else None

        drawing = None
        if data:
            try:
                drawing = render_stroke_data(kage, data)
            except Exception as exc:
                print(f"  [!] render failed {mapping.name if mapping else pua}: {exc}")
                drawing = None

        identity_name = result_glyph_name(pua)
        if identity_name not in glyphs:
            glyph_order.append(identity_name)
            if drawing is not None:
                try:
                    ttg = svg_drawing_to_ttglyph(drawing, upem)
                except Exception as exc:
                    print(
                        f"  [!] outline failed "
                        f"{mapping.name if mapping else pua}: {exc}"
                    )
                    ttg = None
                if ttg is not None:
                    glyphs[identity_name] = ttg
                    try:
                        lsb = int(ttg.xMin)
                    except Exception:
                        lsb = 0
                    metrics[identity_name] = (advance, lsb)
                    rendered += 1
                else:
                    glyphs[identity_name] = empty_glyph()
                    metrics[identity_name] = (advance, 0)
            else:
                glyphs[identity_name] = empty_glyph()
                metrics[identity_name] = (advance, 0)

        pua_name = pua_glyph_name(pua)
        liga_rules.append(f"  sub {mk_name} {pua_name} by {identity_name};")

        if not include_mirrors:
            continue

        for mode, flip_x, flip_y, suffix in MIRROR_FORMS_SPEC:
            if suffix is None:
                continue
            m_name = result_glyph_name(pua, suffix)
            if m_name in glyphs:
                continue
            glyph_order.append(m_name)
            if drawing is not None:
                try:
                    ttg = svg_drawing_to_ttglyph(
                        drawing, upem, flip_x=flip_x, flip_y=flip_y
                    )
                except Exception as exc:
                    print(
                        f"  [!] outline failed "
                        f"{mapping.name if mapping else pua}.{suffix}: {exc}"
                    )
                    ttg = None
                if ttg is not None:
                    glyphs[m_name] = ttg
                    try:
                        lsb = int(ttg.xMin)
                    except Exception:
                        lsb = 0
                    metrics[m_name] = (advance, lsb)
                    rendered += 1
                else:
                    glyphs[m_name] = empty_glyph()
                    metrics[m_name] = (advance, 0)
            else:
                glyphs[m_name] = empty_glyph()
                metrics[m_name] = (advance, 0)

            vs_pua = MirrorVS.codepoint(mode)
            vs_sel = pua_glyph_name(vs_pua)
            rlig_rules.append(f"  sub {identity_name} {vs_sel} by {m_name};")

    ascent = otRound(upem * 0.88)
    descent = otRound(upem * -0.12)
    hex_id = f"{marker:X}"
    family = f"glyphwiki {hex_id}"
    ps = f"glyphwiki-{hex_id}"

    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    fb.setupCharacterMap(cmap)
    fb.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": f"{ps}-1.0",
            "fullName": f"{family} Regular",
            "psName": ps,
            "version": "Version 1.000",
        }
    )
    fb.setupOS2(
        sTypoAscender=ascent,
        sTypoDescender=descent,
        sTypoLineGap=0,
        usWinAscent=ascent,
        usWinDescent=-descent,
        sxHeight=otRound(upem * 0.5),
        sCapHeight=ascent,
    )
    fb.setupPost()

    fea = ["languagesystem DFLT dflt;", "feature liga {"]
    fea.extend(liga_rules)
    fea.append("} liga;")
    if rlig_rules:
        fea.append("feature rlig {")
        fea.extend(rlig_rules)
        fea.append("} rlig;")
    addOpenTypeFeaturesFromString(fb.font, "\n".join(fea))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(out_path))
    if write_woff2:
        woff_path = out_path.with_suffix(".woff2")
        woff2.compress(str(out_path), str(woff_path))

    total = len(glyph_order) - 1  # exclude .notdef
    return rendered, total


def group_mappings_by_marker(
    mappings: Iterable[GlyphMapping],
) -> dict[int, list[GlyphMapping]]:
    grouped: dict[int, list[GlyphMapping]] = {}
    for m in mappings:
        grouped.setdefault(m.marker, []).append(m)
    return grouped
