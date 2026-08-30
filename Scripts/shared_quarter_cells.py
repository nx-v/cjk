"""Quarter-cell segments — three faces (2×2 grid / vertical / horizontal).

Encoding
--------
* Standard CJK / kana / yi code points are used as-is.
* **Grid face** (`q`): 2×2 corners + L-shaped 3/4 via VS41–48.
* **Vertical face** (`qv`): Y-axis bands via VS9–10 + VS27–33.
* **Horizontal face** (`qh`): X-axis bands via VS11–12 + VS34–40.
  Label “top”/“bottom” on the horizontal face maps to **left**/**right**
  (r90 CCW: top→left, bottom→right); glyph suffixes are distinct (`q4l*`).
* `FE00` → zero-width `.ov` for stacking.
* GSUB `ccmp`/`rlig`/`liga` only — no cmap-14 UVS.

Unicode VS numbers: VS1–16 = U+FE00–FE0F; VS17+ = U+E0100+.

Grid (`q`) — 2×2; L for a corner is the 3/4 that includes that corner
======= ========== ========================= ========
VS      Code point Segment                     Suffix
======= ========== ========================= ========
VS41    U+E0118    top-left quarter          `q2tl`
VS42    U+E0119    top-right quarter         `q2tr`
VS43    U+E011A    bottom-left quarter       `q2bl`
VS44    U+E011B    bottom-right quarter      `q2br`
VS45    U+E011C    L at top-left (top∪left)  `q2tl3`
VS46    U+E011D    L at top-right            `q2tr3`
VS47    U+E011E    L at bottom-left          `q2bl3`
VS48    U+E011F    L at bottom-right         `q2br3`
======= ========== ========================= ========

Vertical (`qv`) — axis Y, bands 0=bottom … 3=top
======= ========== ========================= ========
VS      Code point Segment                     Suffix
======= ========== ========================= ========
VS9     U+FE08     top half                  `q4th`
VS10    U+FE09     bottom half               `q4bh`
VS27    U+E010A    top quarter               `q4t`
VS28    U+E010B    near-top quarter          `q4nt`
VS29    U+E010C    near-bottom quarter       `q4nb`
VS30    U+E010D    bottom quarter            `q4b`
VS31    U+E010E    top three-quarters        `q4t3`
VS32    U+E010F    bottom three-quarters     `q4b3`
VS33    U+E0110    middle half               `q4mh`
======= ========== ========================= ========

Horizontal (`qh`) — axis X, bands 0=left … 3=right (distinct suffixes)
======= ========== ========================= ========
VS      Code point Segment                     Suffix
======= ========== ========================= ========
VS11    U+FE0A     left half                 `q4lh`
VS12    U+FE0B     right half                `q4rh`
VS34    U+E0111    left quarter              `q4l`
VS35    U+E0112    near-left quarter         `q4nl`
VS36    U+E0113    near-right quarter        `q4nr`
VS37    U+E0114    right quarter             `q4r`
VS38    U+E0115    left three-quarters       `q4l3`
VS39    U+E0116    right three-quarters      `q4r3`
VS40    U+E0117    middle half               `q4mc`
======= ========== ========================= ========

Segment forms are **slices** of already-baked fullwidth / half-cell outlines.
Each qv/qh band is clipped to its slot rect (never `full − piece` — pathops
difference leaves cut-line spikes). Grid corners are clipped to quadrant
rects; L 3/4 shapes are unions of two clean halves. Zero-width `.ov` forms
are composites of those fullwidth slices.

Geometry around every cut (clip / union)::

    Before: decompose → spike/snap → strip crumbs → safe winding simplify
    After:  strip → heal joins → safe simplify → strip → final snap
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from fontTools.misc.transform import Transform
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from shared_half_cells import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    TRANSFORM_MODES,
    _recording_from_glyph,
    add_overlay_forms,
    apply_transform,
    boolean_union_named,
    build_chunked_ligature_subst_lookup,
    copy_named_glyph,
    empty_glyph,
    finalize_slice_metrics,
    half_plane_rect,
    ideographic_bounds,
    install_derived_glyph,
    make_segment_slice_glyph,
    overlay_glyph_name,
    variant_glyph_name,
    HALF_PLANE_INF_FRAC,
    propagate_d4_segments,
    OV_SELECTOR_CP,
    OV_SELECTOR_NAME,
)

# FE00 zero-width overlay.

# Four bands along the segment axis.
QUARTER_BANDS = 4
QUARTER_PAD_FRAC = 0.02

# (vs_cp, selector name, suffix, band0, band1) — axis comes from the face.
QuarterSlot = Tuple[int, str, str, int, int]

# Vertical face: Y axis. band 0 = bottom, band 3 = top.
# Selector names match Unicode VS indices (VS9 = FE08, VS27 = E010A, …).
QUARTER_VS_SLOTS_V: Tuple[QuarterSlot, ...] = (
    (0xFE08, "vs09", "q4th", 2, 3),  # top half
    (0xFE09, "vs10", "q4bh", 0, 1),  # bottom half
    (0xE010A, "vs27", "q4t", 3, 3),  # top quarter
    (0xE010B, "vs28", "q4nt", 2, 2),  # near-top
    (0xE010C, "vs29", "q4nb", 1, 1),  # near-bottom
    (0xE010D, "vs30", "q4b", 0, 0),  # bottom quarter
    (0xE010E, "vs31", "q4t3", 1, 3),  # top 3/4
    (0xE010F, "vs32", "q4b3", 0, 2),  # bottom 3/4
    (0xE0110, "vs33", "q4mh", 1, 2),  # middle half
)

# Horizontal face: X axis. band 0 = left, band 3 = right.
# Distinct suffixes (not shared with qv).
QUARTER_VS_SLOTS_H: Tuple[QuarterSlot, ...] = (
    (0xFE0A, "vs11", "q4lh", 0, 1),  # left half
    (0xFE0B, "vs12", "q4rh", 2, 3),  # right half
    (0xE0111, "vs34", "q4l", 0, 0),  # left quarter
    (0xE0112, "vs35", "q4nl", 1, 1),  # near-left
    (0xE0113, "vs36", "q4nr", 2, 2),  # near-right
    (0xE0114, "vs37", "q4r", 3, 3),  # right quarter
    (0xE0115, "vs38", "q4l3", 0, 2),  # left 3/4
    (0xE0116, "vs39", "q4r3", 1, 3),  # right 3/4
    (0xE0117, "vs40", "q4mc", 1, 2),  # middle half
)

# 2×2 grid face. VS41–44 corners tl,tr,bl,br; VS45–48 L 3/4 for the same corners.
# (vs_cp, selector name, suffix) — no band indices.
GridSlot = Tuple[int, str, str]
GRID_VS_SLOTS: Tuple[GridSlot, ...] = (
    (0xE0118, "vs41", "q2tl"),
    (0xE0119, "vs42", "q2tr"),
    (0xE011A, "vs43", "q2bl"),
    (0xE011B, "vs44", "q2br"),
    (0xE011C, "vs45", "q2tl3"),
    (0xE011D, "vs46", "q2tr3"),
    (0xE011E, "vs47", "q2bl3"),
    (0xE011F, "vs48", "q2br3"),
)

# Discrete 2×2 cells for D4 remapping of corners / L 3/4.
GRID_CELL_LABELS: Dict[str, FrozenSet[str]] = {
    "q2tl": frozenset({"tl"}),
    "q2tr": frozenset({"tr"}),
    "q2bl": frozenset({"bl"}),
    "q2br": frozenset({"br"}),
    "q2tl3": frozenset({"tl", "tr", "bl"}),
    "q2tr3": frozenset({"tl", "tr", "br"}),
    "q2bl3": frozenset({"tl", "bl", "br"}),
    "q2br3": frozenset({"tr", "bl", "br"}),
}

QUARTER_FACE_V = "qv"
QUARTER_FACE_H = "qh"
QUARTER_FACE_GRID = "q"
QUARTER_FACES = (QUARTER_FACE_GRID, QUARTER_FACE_V, QUARTER_FACE_H)


def quarter_slots_for_face(face: str) -> Tuple:
    match face:
        case "qv":
            return QUARTER_VS_SLOTS_V
        case "qh":
            return QUARTER_VS_SLOTS_H
        case "q":
            return GRID_VS_SLOTS
        case _:
            raise ValueError(
                f"quarter face must be one of {QUARTER_FACES}, got {face!r}"
            )


def quarter_axis_for_face(face: str) -> str:
    if face == QUARTER_FACE_GRID:
        raise ValueError("grid face q has no single segment axis")
    return "y" if face == QUARTER_FACE_V else "x"


def quarter_slot_parts(slot: Tuple) -> Tuple[int, str, str]:
    """`(vs_cp, selector name, suffix)` from a 3- or 5-tuple slot."""
    return slot[0], slot[1], slot[2]


def quarter_form_name(base_name: str, suffix: str, *, face: str = "") -> str:
    """Segment glyph name (`base.q4t`, `base.q4l`, …).

    `face` is accepted for call-site compatibility; qv/qh suffixes are
    already distinct so no face infix is required.
    """
    del face
    return f"{base_name}.{suffix}"


def _factor_for_bands(band0: int, band1: int) -> float:
    n = abs(band1 - band0) + 1
    return n / float(QUARTER_BANDS)


def _quarter_slot_rect(
    target_upem: float,
    *,
    axis: str,
    band0: int,
    band1: int,
) -> Tuple[float, float, float, float]:
    bot, top, _ = ideographic_bounds(int(target_upem))
    pad = target_upem * QUARTER_PAD_FRAC
    lo_b = min(band0, band1)
    hi_b = max(band0, band1)
    n = float(QUARTER_BANDS)
    if axis == "y":
        span = top - bot
        y0 = bot + span * (lo_b / n) + pad
        y1 = bot + span * ((hi_b + 1) / n) - pad
        return pad, y0, target_upem - pad, y1
    x0 = target_upem * (lo_b / n) + pad
    x1 = target_upem * ((hi_b + 1) / n) - pad
    return x0, bot + pad, x1, top - pad


def quarter_segment_windows(
    face: str, target_upem: int
) -> Dict[str, Tuple[float, float, float, float]]:
    """Finite AABBs for qv/qh band suffixes (D4 matching)."""
    axis = quarter_axis_for_face(face)
    out: Dict[str, Tuple[float, float, float, float]] = {}
    for slot in quarter_slots_for_face(face):
        _cp, _sel, suf, b0, b1 = slot
        out[suf] = _quarter_slot_rect(float(target_upem), axis=axis, band0=b0, band1=b1)
    return out


def _bake_simple_glyph(
    glyph: TTGlyph, glyph_set: Optional[Dict[str, TTGlyph]]
) -> TTGlyph:
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    rec = _recording_from_glyph(glyph, glyph_set)
    pen = TTGlyphPen(None)
    rec.replay(pen)
    out = pen.glyph()
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


def _translate_ink_to_quarter_center(
    glyph: TTGlyph,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: int,
) -> Tuple[TTGlyph, int, int]:
    upem = float(target_upem)
    x0, y0, x1, y1 = _quarter_slot_rect(upem, axis=axis, band0=band0, band1=band1)
    dst_cx = (x0 + x1) / 2.0
    dst_cy = (y0 + y1) / 2.0
    try:
        glyph.recalcBounds(None)
        src_cx = (float(glyph.xMin) + float(glyph.xMax)) / 2.0
        src_cy = (float(glyph.yMin) + float(glyph.yMax)) / 2.0
    except Exception:
        return glyph, int(upem), int(getattr(glyph, "xMin", 0) or 0)
    dx = dst_cx - src_cx
    dy = dst_cy - src_cy
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        try:
            return glyph, int(upem), int(glyph.xMin)
        except Exception:
            return glyph, int(upem), 0
    rec = _recording_from_glyph(glyph, None)
    out = apply_transform(rec, Transform(1, 0, 0, 1, dx, dy))
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    return out, int(upem), lsb


def place_glyph_in_quarter(
    glyph: TTGlyph,
    advance: int,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: int = 1000,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[TTGlyph, int, int]:
    """Clip `glyph` to a quarter / half / 3/4 slot (slice — no stretch).

    Geometry is healed before and after the cut (see module docstring).
    """
    from shared_half_cells import clip_glyph_to_rect, finalize_slice_metrics

    upem = float(target_upem)
    rect = _quarter_slot_rect(upem, axis=axis, band0=band0, band1=band1)
    clipped = clip_glyph_to_rect(glyph, rect, glyph_set=glyph_set)
    return finalize_slice_metrics(
        (clipped, int(upem), 0), glyph_set=glyph_set, upem=int(upem)
    )


def make_quarter_glyph(
    base_name: str,
    advance: int,
    *,
    axis: str,
    band0: int,
    band1: int,
    target_upem: Optional[int] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
    factor: Optional[float] = None,
) -> Tuple[TTGlyph, int, int]:
    """Upright quarter segment as a slice of `base_name` (clip; no stretch)."""
    from shared_half_cells import make_segment_slice_glyph

    if glyph_set is None:
        raise ValueError("make_quarter_glyph requires glyph_set for slice bake")
    upem = int(
        target_upem if target_upem is not None else (advance if advance > 0 else 1000)
    )
    del factor
    rect = _quarter_slot_rect(float(upem), axis=axis, band0=band0, band1=band1)
    return make_segment_slice_glyph(
        base_name,
        advance=int(advance if advance > 0 else upem),
        rect=rect,
        glyph_set=glyph_set,
    )


def add_quarter_forms(
    base_names: Sequence[str],
    *,
    face: str,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int = 1000,
) -> List[str]:
    """Slice each baked form by clipping every quarter / half / 3/4 slot rect.

    ``qv``/``qh`` still reuse CJK ``.dkb`` / ``.dkt`` / ``.dk`` / ``.dkl`` halves when
    present (already clean clips); every other band is clipped from the base.
    """
    axis = quarter_axis_for_face(face)
    if face == QUARTER_FACE_V:
        inherit = {"q4th": "dkb", "q4bh": "dkt"}
    else:
        inherit = {"q4lh": "dk", "q4rh": "dkl"}
    slots = quarter_slots_for_face(face)
    added: List[str] = []
    for name in base_names:
        if name not in glyphs:
            continue
        adv, _lsb = metrics.get(name, (target_upem, 0))

        def _put(out_name: str, gm: Tuple[TTGlyph, int, int]) -> None:
            gm = finalize_slice_metrics(gm, glyph_set=glyphs, upem=target_upem)
            install_derived_glyph(
                out_name,
                gm,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
            )

        for _cp, _sel, suf, b0, b1 in slots:
            out = quarter_form_name(name, suf, face=face)
            if out in glyphs:
                continue
            src_half = inherit.get(suf)
            if src_half is not None:
                src = f"{name}.{src_half}"
                if src in glyphs:
                    _put(
                        out,
                        copy_named_glyph(
                            src, glyphs=glyphs, metrics=metrics, advance=adv
                        ),
                    )
                    continue
            _put(
                out,
                make_segment_slice_glyph(
                    name,
                    advance=adv,
                    rect=_quarter_slot_rect(
                        float(target_upem), axis=axis, band0=b0, band1=b1
                    ),
                    glyph_set=glyphs,
                ),
            )
        added.append(name)
    return added


def add_grid_forms(
    base_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int = 1000,
) -> List[str]:
    """2×2 corners and L 3/4 from CJK half slices (`.dk` / `.dkl` / `.dkb` / `.dkt`).

    Corners are clipped to quadrant half-planes (not `half − half`).
    L 3/4 shapes are unions of two clean halves.
    """
    bot, top, _ = ideographic_bounds(target_upem)
    mid_y = (bot + top) / 2.0
    mid_x = float(target_upem) * 0.5
    inf = float(target_upem) * HALF_PLANE_INF_FRAC
    added: List[str] = []

    def _plane(axis: str, keep: str, cut: float) -> Tuple[float, float, float, float]:
        return half_plane_rect(cut, axis=axis, keep=keep, inf=inf)

    for name in base_names:
        if name not in glyphs:
            continue
        adv, _lsb = metrics.get(name, (target_upem, 0))

        def _put(out_name: str, gm: Tuple[TTGlyph, int, int]) -> None:
            gm = finalize_slice_metrics(gm, glyph_set=glyphs, upem=target_upem)
            install_derived_glyph(
                out_name,
                gm,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
            )

        def _ensure_half(suf: str, axis: str, keep: str, cut: float) -> str:
            hname = f"{name}.{suf}"
            if hname not in glyphs:
                _put(
                    hname,
                    make_segment_slice_glyph(
                        name,
                        advance=adv,
                        rect=_plane(axis, keep, cut),
                        glyph_set=glyphs,
                    ),
                )
            return hname

        left = _ensure_half("dk", "x", "lo", mid_x)
        right = _ensure_half("dkl", "x", "hi", mid_x)
        top_h = _ensure_half("dkb", "y", "hi", mid_y)
        bot_h = _ensure_half("dkt", "y", "lo", mid_y)

        # Quadrant clips: [x-keep] ∩ [y-keep] as a single AABB (clean cut).
        corners = (
            ("q2tl", -inf, mid_y, mid_x, inf),
            ("q2tr", mid_x, mid_y, inf, inf),
            ("q2bl", -inf, -inf, mid_x, mid_y),
            ("q2br", mid_x, -inf, inf, mid_y),
        )
        for suf, x0, y0, x1, y1 in corners:
            out = quarter_form_name(name, suf)
            if out not in glyphs:
                _put(
                    out,
                    make_segment_slice_glyph(
                        name,
                        advance=adv,
                        rect=(x0, y0, x1, y1),
                        glyph_set=glyphs,
                    ),
                )

        ells = (
            ("q2tl3", top_h, left),
            ("q2tr3", top_h, right),
            ("q2bl3", bot_h, left),
            ("q2br3", bot_h, right),
        )
        for suf, a, b in ells:
            out = quarter_form_name(name, suf)
            if out not in glyphs:
                _put(
                    out,
                    boolean_union_named(
                        [a, b], glyphs=glyphs, metrics=metrics, advance=adv
                    ),
                )
        added.append(name)
    return added


def quarter_vs_liga_map(
    bases: Sequence[str],
    *,
    face: str,
    glyphs: Dict[str, TTGlyph],
) -> Dict[Tuple[str, ...], str]:
    """`base + VS` / `FE00` → quarter segment and/or zero-width `.ov`.

    Includes residual ``base.ov + VS → segment.ov`` so segment ligas still
    fire when a prior lookup (half-cell GSUB on face ``q``) already consumed
    ``FE00``.
    """
    from shared_half_cells import vs_glyph_name

    slots = quarter_slots_for_face(face)
    vs01 = vs_glyph_name(TRANSFORM_MODES[0][0])
    has_vs01 = vs01 in glyphs
    ov = OV_SELECTOR_NAME
    liga: Dict[Tuple[str, ...], str] = {}
    for form in bases:
        if form not in glyphs:
            continue
        form_ov = overlay_glyph_name(form)
        if form_ov in glyphs and ov in glyphs:
            liga[(form, ov)] = form_ov
            if has_vs01:
                liga[(form, vs01, ov)] = form_ov
        for slot in slots:
            _vs_cp, sel_name, suf = quarter_slot_parts(slot)
            out = quarter_form_name(form, suf, face=face)
            if out not in glyphs:
                continue
            if sel_name not in glyphs:
                continue
            liga[(form, sel_name)] = out
            if has_vs01:
                liga[(form, vs01, sel_name)] = out
            out_ov = overlay_glyph_name(out)
            if out_ov not in glyphs or ov not in glyphs:
                continue
            liga[(form, ov, sel_name)] = out_ov
            liga[(form, sel_name, ov)] = out_ov
            if has_vs01:
                liga[(form, vs01, ov, sel_name)] = out_ov
                liga[(form, vs01, sel_name, ov)] = out_ov
            liga[(out, ov)] = out_ov
            # On face `q`, half-cell GSUB runs first and may already have
            # turned `form + FE00` into `form.ov` before these lookups see
            # the segment selector (L 3/4 and corners). Residual ligas:
            if form_ov in glyphs:
                liga[(form_ov, sel_name)] = out_ov
                if has_vs01:
                    liga[(form_ov, vs01, sel_name)] = out_ov
    return liga


def prepare_quarter_cells(
    *,
    face: str,
    cjk_bases: Sequence[str],
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int = 1000,
) -> List[str]:
    """Install face VS + FE00, bake quarter segments and `.ov` overlays."""
    slots = quarter_slots_for_face(face)

    if OV_SELECTOR_NAME not in glyphs:
        glyph_order.append(OV_SELECTOR_NAME)
        glyphs[OV_SELECTOR_NAME] = empty_glyph()
        metrics[OV_SELECTOR_NAME] = (0, 0)
    cmap[OV_SELECTOR_CP] = OV_SELECTOR_NAME

    for slot in slots:
        vs_cp, sel_name, _suf = quarter_slot_parts(slot)
        if sel_name not in glyphs:
            glyph_order.append(sel_name)
            glyphs[sel_name] = empty_glyph()
            metrics[sel_name] = (0, 0)
        cmap[vs_cp] = sel_name

    forms: List[str] = []
    seen: set = set()
    for base in cjk_bases:
        if base not in glyphs or base in seen:
            continue
        forms.append(base)
        seen.add(base)
        for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES:
            if suffix is None:
                continue
            vname = variant_glyph_name(base, suffix)
            if vname in glyphs and vname not in seen:
                forms.append(vname)
                seen.add(vname)

    if face == QUARTER_FACE_GRID:
        add_grid_forms(
            list(cjk_bases),
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )
        propagate_d4_segments(
            cjk_bases,
            suffixes=tuple(s[2] for s in GRID_VS_SLOTS),
            form_name=lambda form, suf: quarter_form_name(form, suf, face=face),
            windows={},
            labels=GRID_CELL_LABELS,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )
    else:
        add_quarter_forms(
            list(cjk_bases),
            face=face,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )
        propagate_d4_segments(
            cjk_bases,
            suffixes=tuple(quarter_slot_parts(s)[2] for s in slots),
            form_name=lambda form, suf: quarter_form_name(form, suf, face=face),
            windows=quarter_segment_windows(face, target_upem),
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )

    ov_sources: List[str] = []
    for form in forms:
        if form not in glyphs:
            continue
        ov_sources.append(form)
        for slot in slots:
            _cp, _sel, suf = quarter_slot_parts(slot)
            segment = quarter_form_name(form, suf, face=face)
            if segment in glyphs:
                ov_sources.append(segment)
    add_overlay_forms(
        ov_sources,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
    )
    return forms


def install_quarter_cell_gsub(
    font,
    *,
    face: str,
    bases: Sequence[str],
    glyphs: Dict[str, TTGlyph],
) -> int:
    """Append quarter-cell VS + FE00 overlay ligatures to `GSUB`."""
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    liga = quarter_vs_liga_map(bases, face=face, glyphs=glyphs)
    if not liga:
        return 0

    by_len: Dict[int, Dict[Tuple[str, ...], str]] = {}
    for comps, out in liga.items():
        by_len.setdefault(len(comps), {})[comps] = out
    lookups = [
        build_chunked_ligature_subst_lookup(by_len[length])
        for length in sorted(by_len.keys(), reverse=True)
    ]

    if "GSUB" in font:
        gsub = font["GSUB"].table
    else:
        gsub = ot.GSUB()
        gsub.Version = 0x00010000
        gsub.ScriptList = ot.ScriptList()
        gsub.ScriptList.ScriptRecord = []
        gsub.ScriptList.ScriptCount = 0
        gsub.FeatureList = ot.FeatureList()
        gsub.FeatureList.FeatureRecord = []
        gsub.FeatureList.FeatureCount = 0
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0
        table = newTable("GSUB")
        table.table = gsub
        font["GSUB"] = table

    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0
    if gsub.FeatureList is None:
        gsub.FeatureList = ot.FeatureList()
        gsub.FeatureList.FeatureRecord = []
        gsub.FeatureList.FeatureCount = 0
    if gsub.ScriptList is None:
        gsub.ScriptList = ot.ScriptList()
        gsub.ScriptList.ScriptRecord = []
        gsub.ScriptList.ScriptCount = 0

    existing_scripts = {sr.ScriptTag for sr in (gsub.ScriptList.ScriptRecord or [])}
    script_tags: List[str] = []
    for line in COMPOSITION_LANGUAGE_SYSTEMS:
        parts = line.replace(";", "").split()
        if len(parts) >= 2 and parts[0] == "languagesystem":
            script_tags.append(parts[1].ljust(4)[:4])
    for tag in script_tags:
        if tag in existing_scripts:
            continue
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        ls = ot.DefaultLangSys()
        ls.ReqFeatureIndex = 0xFFFF
        ls.FeatureCount = 0
        ls.FeatureIndex = []
        rec.Script.DefaultLangSys = ls
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gsub.ScriptList.ScriptRecord.append(rec)
        existing_scripts.add(tag)
    gsub.ScriptList.ScriptCount = len(gsub.ScriptList.ScriptRecord)

    li = gsub.LookupList.LookupCount
    gsub.LookupList.Lookup.extend(lookups)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    new_indices = list(range(li, li + len(lookups)))

    tag_to_fr = {fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])}
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            fr = ot.FeatureRecord()
            fr.FeatureTag = tag
            fr.Feature = ot.Feature()
            fr.Feature.FeatureParams = None
            fr.Feature.LookupListIndex = []
            fr.Feature.LookupCount = 0
            gsub.FeatureList.FeatureRecord.append(fr)
            gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)
            tag_to_fr[tag] = fr
            for sr in gsub.ScriptList.ScriptRecord:
                ls = sr.Script.DefaultLangSys
                if ls is None:
                    continue
                fi = list(ls.FeatureIndex or [])
                new_i = gsub.FeatureList.FeatureCount - 1
                if new_i not in fi:
                    fi.append(new_i)
                    ls.FeatureIndex = fi
                    ls.FeatureCount = len(fi)
        idxs = list(fr.Feature.LookupListIndex or [])
        idxs.extend(new_indices)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)

    return len(lookups)
