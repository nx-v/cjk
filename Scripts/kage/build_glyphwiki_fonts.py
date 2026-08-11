"""Build GlyphWiki PUA ligature fonts (D4 + FE08 overlay per SPUA marker).

Each output TTF corresponds to one Supplementary PUA marker and contains:

* 6 400 BMP PUA selector glyphs (U+E000..U+F8FF, zero-width)
* 6 400 × 8 = 51 200 rendered outlines (identity + 7 unique D4 variants)
* FE08 overlay ``.ov`` forms (identity first; D4 variants if under 64k)

GSUB:

* ``marker + pua`` → identity outline
* ``identity + VS02..VS08`` / FE01..FE07 → D4 variant outlines
* ``A B FE08`` → ``A.ov`` (0-advance) + ``B`` (chain with more FE08)

Rendering uses the in-tree KAGE Serif renderer (filled SVG paths), then
Cu2Qu for TrueType. Contours are normalized to clockwise winding so
overlaps fill solidly under nonzero fill. Result glyph names are the
GlyphWiki canonical names (not ``g`` + hex).
"""

from __future__ import annotations

import re
import sys
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
    SHOTAI_STYLES,
    _path_has_spike,
    iter_outline_paths,
    make_engine,
    render_stroke_data,
)
from .mapping import (
    BMP_PUA_COUNT,
    BMP_PUA_END,
    BMP_PUA_START,
    D4_MODES,
    GlyphMapping,
    MirrorVS,
)

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from shared_half_cells import (  # noqa: E402
    STACK_MARK_CP,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    UVS_BASE,
    UVS_LAST,
    add_overlay_forms,
    center_glyph_in_cell,
    composition_fea,
    ideographic_center,
    inject_stack_mark,
    install_overlay_gsub,
    make_composite_variant,
)
from shared_hinting import autohint_ttf  # noqa: E402
from cdn_fonts import dist_rel, format_src_line  # noqa: E402

# One SPUA-marker font: 6400 PUA selectors + 6400*8 rendered D4 variants
PUA_SELECTORS = BMP_PUA_COUNT  # 6400
D4_FORMS = MirrorVS.MODE_COUNT  # identity + 7 unique symmetries
RENDERED_PER_MARKER = PUA_SELECTORS * D4_FORMS  # 51200
GLYPHS_PER_FILE = PUA_SELECTORS + RENDERED_PER_MARKER  # 57600
GLYPHS_PER_FILE_NO_MIRROR = PUA_SELECTORS + PUA_SELECTORS  # 12800


def glyphs_per_file(*, include_mirrors: bool = True) -> int:
    return GLYPHS_PER_FILE if include_mirrors else GLYPHS_PER_FILE_NO_MIRROR


DEFAULT_UPEM = 1000

# FEA / PostScript-safe: letters/digits/._ only; '-' and '@' → '_'.
_OT_NAME_BAD = re.compile(r"[^A-Za-z0-9._]+")


def empty_glyph() -> TTGlyph:
    g = TTGlyph()
    g.numberOfContours = 0
    g.xMin = g.yMin = g.xMax = g.yMax = 0
    return g


def kage_to_font_transform(upem: int = DEFAULT_UPEM) -> Transform:
    """Affine map from KAGE 200×200 (y-down) onto the CJK typo box (y-up).

    The typo box matches ``build_yi`` / ``build_cjk`` metrics
    (ascender 0.88em, descender -0.12em), so GlyphWiki outlines share the
    same vertical band as Han/Yi rather than the geometric em midpoint.
    """
    ascent = upem * TYPO_ASCENDER_FRAC
    descent = upem * TYPO_DESCENDER_FRAC
    body = ascent - descent  # == upem with the default 0.88 / -0.12 fracs
    scale = body / 200.0
    # y_kage=0 (top) → ascent; y_kage=200 (bottom) → descent
    return Transform(scale, 0, 0, -scale, 0, ascent)


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
    flatten: bool = False,
    max_err: float = 0.5,
) -> TTGlyph | None:
    """Convert a KAGE SVG drawing to a TrueType glyph.

    Contours are forced to a uniform clockwise winding in font space so
    overlapping stroke ribbons fill solidly (nonzero winding). Opposite
    windings otherwise punch white holes at joints.

    D4 variants are produced later via ``make_composite_variant`` (axis-aligned
    composites; 2×2 rotates baked to outlines) and ``composition_fea`` GSUB.

    ``flatten`` (pathops.simplify) stays off by default: boolean union of
    Serif ribbons collapses to a solid black square over the glyph bbox.
    """
    if drawing is None:
        return None

    combined = kage_to_font_transform(upem)

    if flatten:
        try:
            import pathops
            from fontTools.ttLib.removeOverlaps import ttfGlyphFromSkPath
        except ImportError:
            flatten = False
        else:
            sk = pathops.Path()
            if not _draw_svg_normalized(drawing, sk.getPen(), combined, clockwise=True):
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
    """Draw SVG paths in font space, reversing any contour with wrong winding.

    Filled mincho ribbons are drawn directly. Gothic/rounded stroked centerlines
    are expanded with pathops before the KAGE→font affine is applied.
    """
    import pathops

    drew = False
    for d, local, stroke in iter_outline_paths(drawing):
        if _path_has_spike(d, local):
            continue
        piece = pathops.Path()
        try:
            # Parse in KAGE space (+ local transform) so stroke widths stay in
            # design units; font_transform is applied after expansion.
            parse_path(d, TransformPen(piece.getPen(), local))
        except Exception:
            continue
        if stroke is not None:
            width, cap, join = stroke
            try:
                piece.stroke(width, cap, join, 4.0)
                # Round caps/joins emit conics; convert before winding / Cu2Qu.
                # Returns None when the stroked path has no conics.
                converted = piece.convertConicsToQuads()
                if converted is not None:
                    piece = converted
            except Exception:
                continue
        else:
            try:
                piece.close()
            except Exception:
                pass
        if font_transform != Transform():
            try:
                piece = piece.transform(*font_transform)
            except Exception:
                # Fallback: replay through a transform pen
                xformed = pathops.Path()
                try:
                    piece.draw(TransformPen(xformed.getPen(), font_transform))
                except Exception:
                    continue
                piece = xformed
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


def ot_glyph_name(name: str) -> str:
    """Map a GlyphWiki canonical name to an OpenType-safe glyph name."""
    # '@' is FEA class syntax; '-' is common in GlyphWiki aliases — both → '_'.
    cleaned = name.replace("@", "_").replace("-", "_")
    cleaned = _OT_NAME_BAD.sub("_", cleaned)
    if not cleaned:
        cleaned = "gw"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def result_glyph_name(
    mapping: GlyphMapping | None,
    pua: int,
    suffix: str | None = None,
) -> str:
    """Identity / D4 result name: GlyphWiki canonical, else ``gXXXX`` fallback."""
    if mapping is not None:
        base = ot_glyph_name(mapping.name)
    else:
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
    style: str = "mincho",
    write_ttf: bool = True,
    write_woff2: bool = True,
    include_mirrors: bool = True,
    hint: bool = True,
) -> tuple[int, int]:
    """Build one font for a single SPUA ``marker``.

    ``mappings`` must all share ``marker`` and cover up to 6400 PUA slots.
    ``stroke_data`` maps glyph name → resolved KAGE stroke string.
    ``style`` is the KAGE shotai (``mincho`` / ``gothic`` / ``rounded``).

    With ``include_mirrors`` (default), each slot gets identity + 7 D4
    variants (57 600 glyphs). Without mirrors, only identity outlines are
    stored (12 800 glyphs).

    Returns ``(rendered_count, total_glyphs)``.
    """
    style_key = style.lower().strip()
    if style_key not in SHOTAI_STYLES:
        raise ValueError(f"unknown style {style!r}; expected one of {SHOTAI_STYLES}")
    if kage is None:
        kage = make_engine(style=style_key)

    by_pua = {m.pua: m for m in mappings if m.marker == marker}
    if not by_pua:
        return 0, 0

    glyph_order = [".notdef"]
    glyphs: dict[str, TTGlyph] = {".notdef": empty_glyph()}
    metrics: dict[str, tuple[int, int]] = {".notdef": (upem // 2, 0)}
    cmap: dict[int, str] = {}
    liga_rules: list[str] = []
    rlig_rules: list[str] = []
    used_names: set[str] = {".notdef"}
    identity_names: list[str] = []
    variant_names: list[str] = []

    # SPUA marker (zero-width)
    mk_name = marker_glyph_name(marker)
    glyph_order.append(mk_name)
    glyphs[mk_name] = empty_glyph()
    metrics[mk_name] = (0, 0)
    cmap[marker] = mk_name
    used_names.add(mk_name)

    # All 6400 BMP PUA selectors (zero-width), including VS01..VS08 slots
    for i in range(BMP_PUA_COUNT):
        pua = BMP_PUA_START + i
        pname = pua_glyph_name(pua)
        glyph_order.append(pname)
        glyphs[pname] = empty_glyph()
        metrics[pname] = (0, 0)
        cmap[pua] = pname
        used_names.add(pname)

    rendered = 0
    advance = upem  # full em square for CJK-like cells

    def unique_result_name(
        mapping: GlyphMapping | None, pua: int, suffix: str | None
    ) -> str:
        name = result_glyph_name(mapping, pua, suffix)
        if name not in used_names:
            return name
        # Collision (rare): fall back to PUA-based unique id.
        fallback = result_glyph_name(None, pua, suffix)
        if fallback not in used_names:
            return fallback
        n = 2
        while f"{fallback}_{n}" in used_names:
            n += 1
        return f"{fallback}_{n}"

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

        identity_name = unique_result_name(mapping, pua, None)
        if identity_name not in glyphs:
            glyph_order.append(identity_name)
            used_names.add(identity_name)
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
                    # Align with Han/Yi: bbox center at CJK typo mid (y≈0.38em).
                    ttg = center_glyph_in_cell(
                        ttg, upem, center=ideographic_center(upem)
                    )
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
        identity_names.append(identity_name)

        if not include_mirrors:
            continue

        id_glyph = glyphs[identity_name]
        has_outline = (
            getattr(id_glyph, "numberOfContours", 0) > 0 or id_glyph.isComposite()
        )
        id_lsb = metrics[identity_name][1]

        for mode, rot, flip_x, flip_y, suffix in D4_MODES:
            if suffix is None:
                continue
            m_name = unique_result_name(mapping, pua, suffix)
            if m_name in glyphs:
                variant_names.append(m_name)
                vs_pua = MirrorVS.codepoint(mode)
                vs_sel = pua_glyph_name(vs_pua)
                rlig_rules.append(f"  sub {identity_name} {vs_sel} by {m_name};")
                continue
            glyph_order.append(m_name)
            used_names.add(m_name)
            if has_outline:
                ttg, m_adv, m_lsb = make_composite_variant(
                    identity_name,
                    upem,
                    rot90_quarters=rot,
                    flip_x=flip_x,
                    flip_y=flip_y,
                    advance=advance,
                    lsb=id_lsb,
                    base_glyph=id_glyph,
                    glyph_set=glyphs,
                )
                glyphs[m_name] = ttg
                metrics[m_name] = (m_adv, m_lsb)
                rendered += 1
            else:
                glyphs[m_name] = empty_glyph()
                metrics[m_name] = (advance, 0)

            variant_names.append(m_name)
            vs_pua = MirrorVS.codepoint(mode)
            vs_sel = pua_glyph_name(vs_pua)
            rlig_rules.append(f"  sub {identity_name} {vs_sel} by {m_name};")

    # FE08 overlay mark + .ov forms (identity first; D4 if under budget).
    # post format 2.0 stores 258+extraNameIndex as uint16, so all-custom
    # name fonts must stay under ~65278 glyphs (not the full 65535 maxp).
    inject_stack_mark(glyph_order, glyphs, metrics, cmap)
    ordered_forms = list(dict.fromkeys(identity_names + variant_names))
    post_safe_max = 65000
    budget = max(0, post_safe_max - len(glyph_order))
    add_overlay_forms(
        ordered_forms,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        limit=budget,
    )

    ascent = otRound(upem * TYPO_ASCENDER_FRAC)
    descent = otRound(upem * TYPO_DESCENDER_FRAC)
    hex_id = f"{marker:X}"
    family = f"glyphwiki {style_key} {hex_id}"
    ps = f"glyphwiki-{style_key}-{hex_id}"

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

    fea = composition_fea(liga_rules, rlig_rules)
    if fea:
        addOpenTypeFeaturesFromString(fb.font, fea)
    # Overlay GSUB after marker/D4 ligas (appends to existing GSUB).
    full_forms = [
        n
        for n, (adv, _) in metrics.items()
        if adv > 0 and n in glyphs and not n.endswith(".ov")
    ]
    install_overlay_gsub(
        fb.font, full_forms, glyphs=glyphs, glyph_order=glyph_order
    )

    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(out_path))
    autohint_ttf(str(out_path), enabled=hint)
    if write_woff2:
        woff_path = out_path.with_suffix(".woff2")
        woff2.compress(str(out_path), str(woff_path))
    if not write_ttf:
        out_path.unlink(missing_ok=True)

    total = len(glyph_order) - 1  # exclude .notdef
    return rendered, total


def group_mappings_by_marker(
    mappings: Iterable[GlyphMapping],
) -> dict[int, list[GlyphMapping]]:
    grouped: dict[int, list[GlyphMapping]] = {}
    for m in mappings:
        grouped.setdefault(m.marker, []).append(m)
    return grouped


def unicode_range_for_marker(marker: int) -> str:
    """CSS unicode-range: SPUA marker + BMP PUA + UVS FE00..FE07 + FE08.

    Marker + BMP PUA must share a face for ligatures. FE08 must be listed so
    the overlay mark loads from this face (not a sibling in a stack).
    """
    return (
        f"U+{marker:X}, U+{BMP_PUA_START:X}-{BMP_PUA_END:X}, "
        f"U+{UVS_BASE:X}-{UVS_LAST:X}, U+{STACK_MARK_CP:X}"
    )


def write_css(
    font_dir: Path,
    built: Sequence[tuple[str, str]],
) -> None:
    """Write pangw.css (@font-face) under ``font_dir``.

    ``built`` entries are ``(style, hex_id)`` for fonts already on disk
    (e.g. ``("mincho", "F0000")`` → ``mincho/F0000.woff2``).
    """
    font_dir = Path(font_dir)
    font_dir.mkdir(parents=True, exist_ok=True)
    css_path = font_dir / "pangw.css"
    lines: list[str] = [
        "/* Auto-generated GlyphWiki marker @font-face rules */",
        "/* Local src first; GitHub raw as fallback. */",
        "",
    ]
    family_names: list[str] = []
    for style, hex_id in built:
        family = f"glyphwiki {style} {hex_id}"
        family_names.append(family)
        marker = int(hex_id, 16)
        urange = unicode_range_for_marker(marker)
        rel = f"./{style}/{hex_id}"
        lines.append("@font-face {")
        lines.append(f"  font-family: '{family}';")
        lines.append(
            format_src_line(
                dist_rel("glyphwiki", style, f"{hex_id}.woff2"),
                fmt="woff2",
                local=(
                    (f"{rel}.woff2", "woff2"),
                    (f"{rel}.ttf", "truetype"),
                ),
                indent="  ",
            )
        )
        lines.append("  font-weight: normal;")
        lines.append("  font-style: normal;")
        lines.append("  font-display: swap;")
        lines.append(f"  unicode-range: {urange};")
        lines.append("}")
        lines.append("")

    css_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {css_path}")

    quoted = ", ".join(f"'{n}'" for n in family_names)
    fontlist_path = font_dir / "fontlist.css"
    fontlist_path.write_text(
        "/* GlyphWiki marker font stack */\n"
        ":root {\n"
        f"  --font-pangw: {quoted};\n"
        "}\n",
        encoding="utf-8",
    )
    print(f"Wrote {fontlist_path}")


def regenerate_css_from_dist(font_dir: Path) -> None:
    """Scan ``font_dir/{{style}}/*.woff2|ttf`` and rewrite pangw.css."""
    font_dir = Path(font_dir)
    built: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not font_dir.is_dir():
        print(f"No GlyphWiki fonts under {font_dir}", file=sys.stderr)
        return
    for style_dir in sorted(p for p in font_dir.iterdir() if p.is_dir()):
        style = style_dir.name
        for path in sorted(style_dir.iterdir()):
            if path.suffix.lower() not in {".woff2", ".ttf"}:
                continue
            hex_id = path.stem
            key = (style, hex_id)
            if key in seen:
                continue
            try:
                int(hex_id, 16)
            except ValueError:
                continue
            seen.add(key)
            built.append(key)
    if not built:
        print(f"No marker fonts found under {font_dir}", file=sys.stderr)
        return
    print(f"Regenerating CSS for {len(built)} GlyphWiki fonts from {font_dir}")
    write_css(font_dir, built)
