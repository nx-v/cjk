"""Shared corner diacritics (Yi / Hangul) from a multi-font mark stack.

Inventory: ``(\\p{M} ∩ stack) ∖ variation selectors`` (VS1–16, IVS,
Mongolian FVS). Oversized source outlines are still dropped. First font
in the stack wins per codepoint.

Stack (priority order)::

    mkanaplus → Nexsevka-Regular → JuliaMono-Regular → Constructium
    → Droid Sans → Arial Unicode MS → Gentium-Regular

Marks are normalized to a **fixed ink height**, then attach via GPOS
``mark`` / ``abvm`` at fixed CJK cell corners. Each mark’s matching ink
corner is the mark anchor so TR/BR are **right-aligned** and TL/BL
**left-aligned** (flush inside the ideograph, not centered past the edge).

Successive marks fill slots via GSUB cycling (corners then edge midpoints)::

    1st → top-right
    2nd → center-right
    3rd → bottom-right
    4th → top-middle
    5th → bottom-middle
    6th → top-left
    7th → center-left
    8th → bottom-left

``U+034F`` CGJ is an empty mark that occupies the next slot, so
``base + CGJ + mark`` attaches at CR, ``base + CGJ×2 + mark`` at BR, etc.
Interleaved CGJ skips the following slot.

Bases include Yi identity + all D4 orientations (VS02..VS08 / FE01..FE07,
including ``r90my``) and shared ``sliceAdv`` after FE08–FE09 expansion.
No left-squish forms.
"""

from __future__ import annotations

import os
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import otTables as ot
from fontTools.ttLib.tables._g_l_y_f import (
    ROUND_XY_TO_GRID,
    UNSCALED_COMPONENT_OFFSET,
    Glyph as TTGlyph,
    GlyphComponent,
)

from shared_half_cells import (
    COMPOSITION_FEATURE_TAGS,
    COMPOSITION_LANGUAGE_SYSTEMS,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    YI_ORIENTATION_MODES,
    apply_transform,
    orientation_form_names,
    recording_bounds,
)

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)

MKANAPLUS_FILENAMES: Tuple[str, ...] = ("mkanaplus.ttf", "mkanaplus-regular.ttf")
NEXSEVKA_FILENAME = "Nexsevka-Regular.ttf"
JULIAMONO_FILENAME = "JuliaMono-Regular.ttf"
CONSTRUCTIUM_FILENAMES: Tuple[str, ...] = ("Constructium.ttf", "constructium.ttf")
DROID_SANS_FILENAMES: Tuple[str, ...] = (
    "DroidSansFallbackFull.ttf",
    "DroidSansFallback.ttf",
    "DroidSans.ttf",
)
ARIAL_UNICODE_FILENAMES: Tuple[str, ...] = (
    "arial unicode ms.otf",
    "arial unicode ms.ttf",
    "ARIALUNI.TTF",
    "Arial Unicode MS.ttf",
    "ArialUnicodeMS.ttf",
)
GENTIUM_FILENAME = "Gentium-Regular.ttf"

# Unicode Mark = Mn | Mc | Me  (regex \p{M}).
MARK_CATS = frozenset({"Mn", "Mc", "Me"})

# Variation_Selector — Mn, but they drive GSUB slices / IVS, not dakuten.
VS_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x180B, 0x180D),  # Mongolian FVS 1–3
    (0x180F, 0x180F),  # Mongolian FVS 4
    (0xFE00, 0xFE0F),  # VS1–16
    (0xE0100, 0xE01EF),  # VS17–256
)

# Drop source outlines larger than this fraction of source UPM (either axis).
MAX_DIACRITIC_FRAC = 0.48

# Uniform ink height after load (fraction of target UPM).
DAKUTEN_MARK_HEIGHT_FRAC = 0.14

# Full D4: identity + VS02..VS08 (FE01..FE07), including r90my.
DAKUTEN_VS_MODE_COUNT = 8

DAKUTEN_EDGE_PAD_FRAC = 0.03

# Successive-mark slot order (mark class index = position in this tuple).
# Suffix None → cmap glyph ``uXXXX.mk`` (top-right); others are composites.
DAKUTEN_SLOTS: Tuple[Tuple[str, Optional[str]], ...] = (
    ("tr", None),
    ("cr", "cr"),
    ("br", "br"),
    ("tm", "tm"),
    ("bm", "bm"),
    ("tl", "tl"),
    ("cl", "cl"),
    ("bl", "bl"),
)
DAKUTEN_SLOT_LABELS: Tuple[str, ...] = tuple(slot.upper() for slot, _ in DAKUTEN_SLOTS)
DAKUTEN_SLOT_SUFFIXES = frozenset(suf for _slot, suf in DAKUTEN_SLOTS if suf)
DAKUTEN_SLOT_CYCLE = "→".join(DAKUTEN_SLOT_LABELS)
DAKUTEN_SLOT_COUNT = len(DAKUTEN_SLOTS)

# Combining Grapheme Joiner: empty mark, same slot cycle as visible diacritics.
CGJ_CP = 0x034F

GDEF_CLASS_BASE = 1
GDEF_CLASS_MARK = 3
MARK_FEATURE_TAGS: Tuple[str, ...] = ("mark", "abvm")


def _first_existing(paths: Iterable[str]) -> Optional[str]:
    for path in paths:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


def _paths_for_names(in_dir: str, names: Sequence[str], *extra: str) -> Tuple[str, ...]:
    """Candidate paths: ``in_dir``, Scripts/src, repo root, then extras."""
    out: List[str] = []
    for name in names:
        out.append(os.path.join(in_dir, name))
        out.append(os.path.join(_SCRIPTS_DIR, "src", name))
        out.append(os.path.join(_REPO_ROOT, name))
    out.extend(extra)
    return tuple(out)


def resolve_dakuten_mark_font_stack(in_dir: str) -> List[str]:
    """Return existing mark-source paths in priority order.

    Priority: mkanaplus → Nexsevka → JuliaMono → Constructium →
    Droid Sans → Arial Unicode MS → Gentium.
    Looks under ``in_dir`` first, then well-known repo locations.
    """
    groups: Tuple[Tuple[str, ...], ...] = (
        _paths_for_names(
            in_dir,
            MKANAPLUS_FILENAMES,
            os.path.join(_REPO_ROOT, "Kana", "mkanaplus.ttf"),
            os.path.join(_REPO_ROOT, "mkanaplus-regular.ttf"),
        ),
        _paths_for_names(
            in_dir,
            (NEXSEVKA_FILENAME,),
            os.path.join(_REPO_ROOT, "Nexsevka", "TTF", NEXSEVKA_FILENAME),
        ),
        _paths_for_names(in_dir, (JULIAMONO_FILENAME,)),
        _paths_for_names(in_dir, CONSTRUCTIUM_FILENAMES),
        _paths_for_names(in_dir, DROID_SANS_FILENAMES),
        _paths_for_names(in_dir, ARIAL_UNICODE_FILENAMES),
        _paths_for_names(
            in_dir,
            (GENTIUM_FILENAME,),
            os.path.join(_REPO_ROOT, "Gentium", GENTIUM_FILENAME),
        ),
    )
    out: List[str] = []
    for candidates in groups:
        found = _first_existing(candidates)
        if found is not None:
            out.append(found)
    if not out:
        raise FileNotFoundError(
            "No shared-diacritic mark source fonts found "
            "(mkanaplus / Nexsevka / JuliaMono / Constructium / "
            "Droid Sans / Arial Unicode MS / Gentium; "
            f"in_dir={in_dir!r})"
        )
    return out


def dakuten_mark_stack_label(paths: Sequence[str]) -> str:
    return " + ".join(os.path.basename(p) for p in paths)


def resolve_juliamono_path(in_dir: str) -> str:
    """Back-compat: JuliaMono path from the mark stack (or raise)."""
    for path in (
        os.path.join(in_dir, JULIAMONO_FILENAME),
        os.path.join(_SCRIPTS_DIR, "src", JULIAMONO_FILENAME),
    ):
        if os.path.isfile(path):
            return os.path.normpath(path)
    raise FileNotFoundError(f"Missing JuliaMono source font under {in_dir!r}")


def is_variation_selector(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in VS_RANGES)


def _glyph_ink_size(glyph_set, glyph_name: str) -> Optional[Tuple[float, float]]:
    try:
        pen = BoundsPen(glyph_set)
        glyph_set[glyph_name].draw(pen)
    except Exception:
        return None
    if not pen.bounds:
        return None
    x0, y0, x1, y1 = pen.bounds
    return (x1 - x0), (y1 - y0)


def iter_dakuten_codepoints(cmap: Dict[int, str]) -> List[int]:
    """All ``\\p{M}`` in ``cmap`` except variation selectors."""
    out: List[int] = []
    for cp in sorted(cmap):
        if is_variation_selector(cp):
            continue
        if unicodedata.category(chr(cp)) not in MARK_CATS:
            continue
        out.append(cp)
    return out


def dakuten_mark_name(cp: int) -> str:
    return f"u{cp:04X}.mk" if cp <= 0xFFFF else f"u{cp:05X}.mk"


def dakuten_mark_slot_name(cp: int, slot_suffix: Optional[str]) -> str:
    base = dakuten_mark_name(cp)
    return base if not slot_suffix else f"{base}.{slot_suffix}"


def dakuten_mark_variant_name(cp: int, variant: str = "") -> str:
    """``uXXXX.mk`` or ``uXXXX.mk.<variant>`` (e.g. ``.sm`` for small kana)."""
    base = dakuten_mark_name(cp)
    return f"{base}.{variant}" if variant else base


def dakuten_mark_slot_variant_name(
    cp: int, slot_suffix: Optional[str], variant: str = ""
) -> str:
    base = dakuten_mark_variant_name(cp, variant)
    return base if not slot_suffix else f"{base}.{slot_suffix}"


def dakuten_mark_label(cp: int) -> Tuple[str, str, str]:
    """``(char, Unicode name, short picker label)``."""
    ch = chr(cp)
    if cp == CGJ_CP:
        return ch, "COMBINING GRAPHEME JOINER", "CGJ"
    try:
        name = unicodedata.name(ch)
    except ValueError:
        name = f"U+{cp:04X}"
    short = name.split()[-1].replace("-", "") if name else f"{cp:04X}"
    return ch, name, short


def visible_dakuten_cps(cps: Sequence[int]) -> List[int]:
    """Picker inventory — CGJ is a skip control, not a visible mark."""
    return [cp for cp in cps if cp != CGJ_CP]


def dakuten_count_options_html(indent: str = "      ") -> str:
    """``<option>`` list for mark-count (1..N, labels TR / +CR / …)."""
    lines: List[str] = []
    for i, lab in enumerate(DAKUTEN_SLOT_LABELS, 1):
        tag = lab if i == 1 else f"+{lab}"
        lines.append(f'<option value="{i}">{i} ({tag})</option>')
    return ("\n" + indent).join(lines)


def dakuten_skip_options_html(indent: str = "      ") -> str:
    """``<option>`` list for CGJ skip (0 starts TR, 1 starts CR, …)."""
    labs = DAKUTEN_SLOT_LABELS
    lines = [f'<option value="0">0 — start {labs[0]}</option>']
    for i, lab in enumerate(labs[1:], 1):
        lines.append(f'<option value="{i}">{i} — start {lab}</option>')
    return ("\n" + indent).join(lines)


def make_empty_mark_glyph() -> TTGlyph:
    """Zero-contour mark (CGJ skip slot)."""
    g = TTGlyph()
    g.numberOfContours = 0
    g.xMin = g.yMin = g.xMax = g.yMax = 0
    return g


def ensure_cgj_skip_mark(
    mark_cps: List[int],
    mark_glyphs: Dict[int, TTGlyph],
) -> None:
    """Force empty U+034F so each CGJ consumes the next dakuten slot."""
    mark_glyphs[CGJ_CP] = make_empty_mark_glyph()
    if CGJ_CP in mark_cps:
        mark_cps.remove(CGJ_CP)
    mark_cps.insert(0, CGJ_CP)


def dakuten_orientation_modes(
    modes: Optional[Sequence] = None,
) -> List:
    """Orientation modes that receive dakuten anchors (full D4 by default)."""
    use = list(modes) if modes is not None else list(YI_ORIENTATION_MODES)
    n = min(DAKUTEN_VS_MODE_COUNT, len(use))
    return use[:n]


def yi_forms_for_dakuten(
    yi_bases: Sequence[str],
    *,
    modes=None,
) -> List[str]:
    """Identity + VS02..VS08 forms (incl. ``r90my``) that may take dakuten."""
    names: List[str] = []
    for base in yi_bases:
        names.extend(
            orientation_form_names(base, modes=dakuten_orientation_modes(modes))
        )
    return names


def cjk_corner_anchors(target_upem: int) -> Dict[str, Tuple[int, int]]:
    """Fixed dakuten positions at CJK typo-box corners and edge midpoints."""
    edge = target_upem * DAKUTEN_EDGE_PAD_FRAC
    typo_top = target_upem * TYPO_ASCENDER_FRAC
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    x_r = otRound(target_upem - edge)
    x_l = otRound(edge)
    y_t = otRound(typo_top - edge)
    y_b = otRound(typo_bot + edge)
    x_m = otRound((x_l + x_r) / 2.0)
    y_m = otRound((y_t + y_b) / 2.0)
    return {
        "tr": (x_r, y_t),
        "cr": (x_r, y_m),
        "br": (x_r, y_b),
        "tm": (x_m, y_t),
        "bm": (x_m, y_b),
        "tl": (x_l, y_t),
        "cl": (x_l, y_m),
        "bl": (x_l, y_b),
    }


def cjk_top_right_anchor(target_upem: int) -> Tuple[int, int]:
    """Back-compat: fixed CJK typo-box top-right."""
    return cjk_corner_anchors(target_upem)["tr"]


def make_dakuten_mark_glyph(
    rec: RecordingPen,
    *,
    target_height: float,
) -> Optional[TTGlyph]:
    """Scale mark to ``target_height`` and pin ink center to ``(0, 0)``.

    GPOS uses per-slot corner anchors on this outline (not the center).
    """
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0 or target_height <= 0:
        return None
    scale = float(target_height) / h
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    t = Transform(scale, 0, 0, scale, -scale * cx, -scale * cy)
    glyph = apply_transform(rec, t)
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    return glyph


def mark_corner_anchor(
    glyph: TTGlyph,
    slot: str,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> Tuple[int, int]:
    """Ink point of the mark that pins to the matching CJK cell slot.

    Right slots right-align, left slots left-align, top/bottom likewise.
    Mid-edge slots (``cr``/``cl``/``tm``/``bm``) pin the mark's center on
    that axis so the diacritic sits on the cell edge rather than straddling it.
    """
    try:
        if glyph.isComposite() and glyph_set is not None:
            glyph.recalcBounds(glyph_set)
        else:
            glyph.recalcBounds(None)
        x0, y0 = float(glyph.xMin), float(glyph.yMin)
        x1, y1 = float(glyph.xMax), float(glyph.yMax)
    except Exception:
        return 0, 0
    if slot in ("tr", "cr", "br"):
        x = x1
    elif slot in ("tl", "cl", "bl"):
        x = x0
    else:
        x = (x0 + x1) / 2.0
    if slot in ("tr", "tm", "tl"):
        y = y1
    elif slot in ("br", "bm", "bl"):
        y = y0
    else:
        y = (y0 + y1) / 2.0
    return otRound(x), otRound(y)


def _mark_slot_composite(base_name: str) -> TTGlyph:
    """Zero-offset composite alias of ``base_name`` (extra mark-class GID)."""
    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = base_name
    comp.x = 0
    comp.y = 0
    comp.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [comp]
    return g


def load_dakuten_marks(
    font_path: str,
    target_upem: int,
) -> Tuple[List[int], Dict[int, TTGlyph]]:
    """Load all ``\\p{M}`` marks except variation selectors; fixed ink height."""
    tt = TTFont(font_path, fontNumber=0)
    try:
        cmap: Dict[int, str] = {}
        for table in tt["cmap"].tables:
            if table.isUnicode():
                cmap.update(table.cmap)
        glyph_set = tt.getGlyphSet()
        src_upem = float(tt["head"].unitsPerEm)
        max_ext = src_upem * MAX_DIACRITIC_FRAC
        target_h = float(target_upem) * DAKUTEN_MARK_HEIGHT_FRAC
        max_w = float(target_upem) * MAX_DIACRITIC_FRAC

        cps: List[int] = []
        glyphs: Dict[int, TTGlyph] = {}
        for cp in iter_dakuten_codepoints(cmap):
            if cp == CGJ_CP:
                continue
            gname = cmap[cp]
            sized = _glyph_ink_size(glyph_set, gname)
            if sized is None:
                continue
            w, h = sized
            if max(w, h) > max_ext + 1e-6:
                continue
            rec = DecomposingRecordingPen(glyph_set)
            try:
                glyph_set[gname].draw(rec)
            except Exception:
                continue
            mark = make_dakuten_mark_glyph(rec, target_height=target_h)
            if mark is None:
                continue
            try:
                mark.recalcBounds(None)
                if float(mark.xMax) - float(mark.xMin) > max_w + 1e-6:
                    continue
            except Exception:
                pass
            cps.append(cp)
            glyphs[cp] = mark
        return cps, glyphs
    finally:
        tt.close()


def load_dakuten_marks_from_stack(
    font_paths: Sequence[str],
    target_upem: int,
) -> Tuple[List[int], Dict[int, TTGlyph]]:
    """Union marks across ``font_paths``; earlier fonts win per codepoint."""
    claimed: Dict[int, TTGlyph] = {}
    order: List[int] = []
    for path in font_paths:
        cps, glyphs = load_dakuten_marks(path, target_upem)
        for cp in cps:
            if cp == CGJ_CP or cp in claimed:
                continue
            claimed[cp] = glyphs[cp]
            order.append(cp)
    ensure_cgj_skip_mark(order, claimed)
    return order, claimed


def scale_dakuten_mark_glyph(
    glyph: TTGlyph,
    scale: float,
    *,
    weight_factor: float = 1.0,
) -> Optional[TTGlyph]:
    """Uniform scale about origin, optional CAPE Weight bolden, re-center at 0."""
    if scale <= 0:
        return None
    if getattr(glyph, "numberOfContours", 0) == 0 and not (
        hasattr(glyph, "isComposite") and glyph.isComposite()
    ):
        return make_empty_mark_glyph()
    try:
        rec = RecordingPen()
        glyph.draw(rec, None)
    except Exception:
        return None
    if abs(scale - 1.0) > 1e-9:
        out = apply_transform(rec, Transform(scale, 0, 0, scale, 0, 0))
    else:
        out = apply_transform(rec, Transform())
    if abs(weight_factor - 1.0) > 1e-9:
        try:
            from cape_weightor import bolden_ttglyph

            out, _, _ = bolden_ttglyph(out, weight_factor, advance=0.0)
        except Exception:
            pass
    try:
        out.recalcBounds(None)
        cx = (float(out.xMin) + float(out.xMax)) / 2.0
        cy = (float(out.yMin) + float(out.yMax)) / 2.0
        if abs(cx) > 1e-6 or abs(cy) > 1e-6:
            rec2 = RecordingPen()
            out.draw(rec2, None)
            out = apply_transform(rec2, Transform(1, 0, 0, 1, -cx, -cy))
            out.recalcBounds(None)
    except Exception:
        pass
    return out


def add_dakuten_mark_glyphs(
    mark_cps: Sequence[int],
    mark_glyphs: Dict[int, TTGlyph],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
) -> List[str]:
    """Install cmap ``.mk`` glyphs plus per-slot composites (``.cr``…``.bl``).

    Returns every mark glyph name (all slots) for GPOS / GDEF.
    """
    names: List[str] = []
    for cp in mark_cps:
        g = mark_glyphs.get(cp)
        if g is None:
            continue
        base = dakuten_mark_name(cp)
        if base not in glyphs:
            glyph_order.append(base)
            glyphs[base] = g
            try:
                g.recalcBounds(None)
                lsb = int(g.xMin)
            except Exception:
                lsb = 0
            metrics[base] = (0, lsb)
        cmap[cp] = base
        names.append(base)
        for _slot, suffix in DAKUTEN_SLOTS:
            if not suffix:
                continue
            sname = dakuten_mark_slot_name(cp, suffix)
            if sname in glyphs:
                names.append(sname)
                continue
            glyph_order.append(sname)
            glyphs[sname] = _mark_slot_composite(base)
            metrics[sname] = (0, metrics[base][1])
            names.append(sname)
    return names


def add_dakuten_mark_scale_variants(
    mark_cps: Sequence[int],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    scale: float,
    weight_factor: float = 1.0,
    variant: str = "sm",
) -> List[str]:
    """Install scaled ``.mk.<variant>`` outlines + slot composites (no cmap)."""
    if not variant:
        raise ValueError("variant must be non-empty (e.g. 'sm')")
    names: List[str] = []
    for cp in mark_cps:
        src = glyphs.get(dakuten_mark_name(cp))
        if src is None:
            continue
        scaled = scale_dakuten_mark_glyph(src, scale, weight_factor=weight_factor)
        if scaled is None:
            continue
        base = dakuten_mark_variant_name(cp, variant)
        if base not in glyphs:
            glyph_order.append(base)
            glyphs[base] = scaled
            try:
                scaled.recalcBounds(None)
                lsb = int(scaled.xMin)
            except Exception:
                lsb = 0
            metrics[base] = (0, lsb)
        names.append(base)
        for _slot, suffix in DAKUTEN_SLOTS:
            if not suffix:
                continue
            sname = dakuten_mark_slot_variant_name(cp, suffix, variant)
            if sname in glyphs:
                names.append(sname)
                continue
            glyph_order.append(sname)
            glyphs[sname] = _mark_slot_composite(base)
            metrics[sname] = (0, metrics[base][1])
            names.append(sname)
    return names


def collect_dakuten_base_anchors(
    base_names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    target_upem: int,
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    """Map bases → ``{mark_class: (x, y)}`` for all dakuten box slots."""
    corners = cjk_corner_anchors(target_upem)
    class_xy = {i: corners[slot] for i, (slot, _suf) in enumerate(DAKUTEN_SLOTS)}
    anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for name in base_names:
        if name in glyphs:
            anchors[name] = dict(class_xy)
    return anchors


def _langsys_with_features(feature_indices: Sequence[int]) -> ot.DefaultLangSys:
    ls = ot.DefaultLangSys()
    ls.ReqFeatureIndex = 0xFFFF
    ls.FeatureCount = len(feature_indices)
    ls.FeatureIndex = list(feature_indices)
    return ls


def _ensure_gpos(font, script_tags: Sequence[str]) -> ot.GPOS:
    if "GPOS" in font:
        return font["GPOS"].table

    gpos = ot.GPOS()
    gpos.Version = 0x00010000
    gpos.ScriptList = ot.ScriptList()
    gpos.ScriptList.ScriptRecord = []
    for tag in script_tags:
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        rec.Script.DefaultLangSys = _langsys_with_features([])
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gpos.ScriptList.ScriptRecord.append(rec)
    gpos.ScriptList.ScriptCount = len(script_tags)
    gpos.FeatureList = ot.FeatureList()
    gpos.FeatureList.FeatureRecord = []
    gpos.FeatureList.FeatureCount = 0
    gpos.LookupList = ot.LookupList()
    gpos.LookupList.Lookup = []
    gpos.LookupList.LookupCount = 0
    table = newTable("GPOS")
    table.table = gpos
    font["GPOS"] = table
    return gpos


def _ensure_gdef_classes(
    font,
    *,
    bases: Iterable[str],
    marks: Iterable[str],
    glyph_order: Sequence[str],
) -> None:
    if "GDEF" in font:
        gdef = font["GDEF"].table
    else:
        gdef_table = newTable("GDEF")
        gdef = ot.GDEF()
        gdef.Version = 0x00010000
        gdef.GlyphClassDef = None
        gdef.AttachList = None
        gdef.LigCaretList = None
        gdef.MarkAttachClassDef = None
        gdef_table.table = gdef
        font["GDEF"] = gdef_table

    if gdef.GlyphClassDef is None:
        gdef.GlyphClassDef = ot.GlyphClassDef()
        gdef.GlyphClassDef.classDefs = {}

    class_defs = gdef.GlyphClassDef.classDefs
    order = set(glyph_order)
    for name in bases:
        if name in order:
            class_defs[name] = GDEF_CLASS_BASE
    for name in marks:
        if name in order:
            class_defs[name] = GDEF_CLASS_MARK


def install_dakuten_mark_variant_gsub(
    font,
    mark_cps: Sequence[int],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
    base_names: Sequence[str],
    variant: str = "sm",
) -> int:
    """After ``base_names`` (or prior ``.<variant>`` marks), ``.mk`` → ``.mk.<variant>``.

    Lets successive marks after a small base all pick up the scaled outline
    before slot cycling (``.mk.sm`` → ``.mk.sm.cr`` → …).
    """
    from shared_half_cells import (
        build_chain_context_format2,
        build_chunked_single_subst_lookup,
        build_ext_gsub_lookup,
    )

    if not variant:
        return 0

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    mapping = {
        dakuten_mark_name(cp): dakuten_mark_variant_name(cp, variant)
        for cp in mark_cps
        if dakuten_mark_name(cp) in glyphs
        and dakuten_mark_variant_name(cp, variant) in glyphs
    }
    if not mapping:
        return 0

    inputs = _gid_sort(list(mapping))
    bases = _gid_sort([n for n in base_names if n in glyphs])
    prior_sm: List[str] = []
    for cp in mark_cps:
        for _slot, suf in DAKUTEN_SLOTS:
            n = dakuten_mark_slot_variant_name(cp, suf, variant)
            if n in glyphs:
                prior_sm.append(n)
    prior_sm = _gid_sort(prior_sm)
    triggers = _gid_sort(bases + prior_sm)
    if not inputs or not triggers:
        return 0

    if "GSUB" not in font:
        return 0
    gsub = font["GSUB"].table
    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0

    single_lu = build_chunked_single_subst_lookup(mapping)
    bt_cls = {n: 1 for n in triggers}
    st = build_chain_context_format2(
        coverage_glyphs=inputs,
        input_classes={n: 1 for n in inputs},
        input_class=1,
        backtrack_classes=bt_cls,
        backtrack_seq=(1,),
    )
    chain_lu = build_ext_gsub_lookup([st])
    base = gsub.LookupList.LookupCount
    chain_i = base
    single_i = base + 1
    st.ChainSubClassSet[1].ChainSubClassRule[0].SubstLookupRecord[
        0
    ].LookupListIndex = single_i
    gsub.LookupList.Lookup.extend([chain_lu, single_lu])
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)

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
        idxs.append(chain_i)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)

    return 1


def install_dakuten_slot_gsub(
    font,
    mark_cps: Sequence[int],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
    base_names: Sequence[str],
    variant: str = "",
) -> int:
    """Cycle successive marks through ``DAKUTEN_SLOTS`` (TR→…→BL).

    Transitions::

        (base, TR) + TR  →  next slot
        <slot i> + TR    →  slot i+1

    CGJ (empty ``u034F.mk``) uses the same cycle, so each CGJ skips one
    slot for the following mark.

    ``variant`` (e.g. ``\"sm\"``) selects ``uXXXX.mk.sm`` / ``.sm.br`` names.
    Uses Format 2 ChainContext + Extension lookups (compact; no type-6 split).
    """
    from shared_half_cells import (
        build_chain_context_format2,
        build_chunked_single_subst_lookup,
        build_ext_gsub_lookup,
    )

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    def _slot_name(cp: int, suf: Optional[str]) -> str:
        return dakuten_mark_slot_variant_name(cp, suf, variant)

    def _tr_name(cp: int) -> str:
        return dakuten_mark_variant_name(cp, variant)

    slot_lists: List[List[str]] = [[] for _ in DAKUTEN_SLOTS]
    for cp in mark_cps:
        names = [_slot_name(cp, suf) for _slot, suf in DAKUTEN_SLOTS]
        if not all(n in glyphs for n in names):
            continue
        for i, n in enumerate(names):
            slot_lists[i].append(n)
    slot_lists = [_gid_sort(lst) for lst in slot_lists]
    bases = _gid_sort([n for n in base_names if n in glyphs])
    if not slot_lists[0] or not bases:
        return 0

    if "GSUB" not in font:
        return 0
    gsub = font["GSUB"].table
    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0

    feature_lookup_idxs: List[int] = []
    input_cls = {n: 1 for n in slot_lists[0]}

    for i in range(len(DAKUTEN_SLOTS) - 1):
        _slot, next_suf = DAKUTEN_SLOTS[i + 1]
        mapping = {
            _tr_name(cp): _slot_name(cp, next_suf)
            for cp in mark_cps
            if _tr_name(cp) in glyphs and _slot_name(cp, next_suf) in glyphs
        }
        if not mapping or not slot_lists[i]:
            continue
        single_lu = build_chunked_single_subst_lookup(mapping)

        if i == 0:
            # Closest backtrack = TR mark (class 1), then a dakuten base (class 2).
            bt_cls = {**{n: 1 for n in slot_lists[0]}, **{n: 2 for n in bases}}
            bt_seq = (1, 2)
        else:
            bt_cls = {n: 1 for n in slot_lists[i]}
            bt_seq = (1,)

        st = build_chain_context_format2(
            coverage_glyphs=slot_lists[0],
            input_classes=input_cls,
            input_class=1,
            backtrack_classes=bt_cls,
            backtrack_seq=bt_seq,
        )
        chain_lu = build_ext_gsub_lookup([st])

        base = gsub.LookupList.LookupCount
        chain_i = base
        single_i = base + 1
        st.ChainSubClassSet[1].ChainSubClassRule[0].SubstLookupRecord[
            0
        ].LookupListIndex = single_i
        gsub.LookupList.Lookup.extend([chain_lu, single_lu])
        gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
        feature_lookup_idxs.append(chain_i)

    if not feature_lookup_idxs:
        return 0

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
        for li in feature_lookup_idxs:
            idxs.append(li)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)

    return len(feature_lookup_idxs)


def _ensure_gpos_scripts(gpos: ot.GPOS, script_tags: Sequence[str]) -> None:
    """Ensure each ``script_tags`` entry exists on ``gpos.ScriptList``."""
    existing = {sr.ScriptTag for sr in (gpos.ScriptList.ScriptRecord or [])}
    for tag in script_tags:
        if tag in existing:
            continue
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        rec.Script.DefaultLangSys = _langsys_with_features([])
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gpos.ScriptList.ScriptRecord.append(rec)
        existing.add(tag)
    gpos.ScriptList.ScriptCount = len(gpos.ScriptList.ScriptRecord)


def install_dakuten_gpos(
    font,
    *,
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]],
    mark_cps: Sequence[int],
    mark_names: Sequence[str],
    glyph_order: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    extra_script_tags: Sequence[str] = (),
    base_chunk: int = 2048,
) -> int:
    """Install ``mark``/``abvm`` MarkToBase at CJK box corners and edge mids.

    Mark anchors are the matching ink points (left-/right-/center-aligned).
    Base anchors stay at the padded cell slots. ``extra_script_tags`` (e.g.
    ``hang``) merge into the GPOS script list alongside
    ``COMPOSITION_LANGUAGE_SYSTEMS``. Large base inventories are split into
    Extension MarkToBase subtables.
    """
    if not base_anchors or not mark_names:
        return 0

    from fontTools.otlLib.builder import (
        buildAnchor,
        buildLookup,
        buildMarkBasePosSubtable,
    )

    script_tags: List[str] = []
    for line in COMPOSITION_LANGUAGE_SYSTEMS:
        parts = line.replace(";", "").split()
        if len(parts) >= 2 and parts[0] == "languagesystem":
            script_tags.append(parts[1].ljust(4)[:4])
    for tag in extra_script_tags:
        t = tag.ljust(4)[:4]
        if t not in script_tags:
            script_tags.append(t)

    order_index = {n: i for i, n in enumerate(glyph_order)}
    marks_sorted = [
        n
        for n in sorted(set(mark_names), key=lambda n: order_index.get(n, 10**9))
        if n in order_index
    ]
    bases_sorted = [
        n
        for n in sorted(base_anchors, key=lambda n: order_index.get(n, 10**9))
        if n in order_index
    ]
    if not marks_sorted or not bases_sorted:
        return 0

    glyph_map = {n: i for i, n in enumerate(glyph_order)}
    marks: Dict[str, Tuple[int, object]] = {}
    # Full marks (``""``) plus scaled variants already installed (e.g. ``sm``).
    seen_vars = {""}
    for cp in mark_cps:
        prefix = dakuten_mark_name(cp) + "."
        for n in glyphs:
            if not n.startswith(prefix):
                continue
            tok = n[len(prefix) :].split(".", 1)[0]
            if tok and tok not in DAKUTEN_SLOT_SUFFIXES:
                seen_vars.add(tok)
    variants = sorted(seen_vars, key=lambda v: (v != "", v))

    for variant in variants:
        for cp in mark_cps:
            base_name = dakuten_mark_variant_name(cp, variant)
            base_glyph = glyphs.get(base_name)
            if base_glyph is None:
                continue
            for class_id, (slot, suf) in enumerate(DAKUTEN_SLOTS):
                name = dakuten_mark_slot_variant_name(cp, suf, variant)
                if name not in order_index:
                    continue
                ax, ay = mark_corner_anchor(base_glyph, slot, glyph_set=glyphs)
                marks[name] = (class_id, buildAnchor(ax, ay))
    if not marks:
        return 0

    subs = []
    for i in range(0, len(bases_sorted), max(1, base_chunk)):
        chunk = bases_sorted[i : i + base_chunk]
        bases = {
            n: {
                class_id: buildAnchor(xy[0], xy[1])
                for class_id, xy in base_anchors[n].items()
            }
            for n in chunk
        }
        subs.append(buildMarkBasePosSubtable(marks, bases, glyph_map))
    lookup = buildLookup(subs, table="GPOS", extension=True)

    gpos = _ensure_gpos(font, script_tags)
    _ensure_gpos_scripts(gpos, script_tags)
    if gpos.LookupList is None:
        gpos.LookupList = ot.LookupList()
        gpos.LookupList.Lookup = []
        gpos.LookupList.LookupCount = 0

    lookup_index = gpos.LookupList.LookupCount
    gpos.LookupList.Lookup.append(lookup)
    gpos.LookupList.LookupCount = len(gpos.LookupList.Lookup)

    if gpos.FeatureList is None:
        gpos.FeatureList = ot.FeatureList()
        gpos.FeatureList.FeatureRecord = []
        gpos.FeatureList.FeatureCount = 0

    tag_to_fr = {fr.FeatureTag: fr for fr in (gpos.FeatureList.FeatureRecord or [])}
    feature_indices_for_scripts: List[int] = []
    for tag in MARK_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            fr = ot.FeatureRecord()
            fr.FeatureTag = tag
            fr.Feature = ot.Feature()
            fr.Feature.FeatureParams = None
            fr.Feature.LookupListIndex = []
            fr.Feature.LookupCount = 0
            gpos.FeatureList.FeatureRecord.append(fr)
            gpos.FeatureList.FeatureCount = len(gpos.FeatureList.FeatureRecord)
            feature_index = gpos.FeatureList.FeatureCount - 1
            tag_to_fr[tag] = fr
        else:
            feature_index = next(
                i
                for i, rec in enumerate(gpos.FeatureList.FeatureRecord)
                if rec.FeatureTag == tag
            )
        feature_indices_for_scripts.append(feature_index)
        idxs = list(fr.Feature.LookupListIndex or [])
        if lookup_index not in idxs:
            idxs.append(lookup_index)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)

    for sr in gpos.ScriptList.ScriptRecord:
        ls = sr.Script.DefaultLangSys
        if ls is None:
            ls = _langsys_with_features([])
            sr.Script.DefaultLangSys = ls
        fi = list(ls.FeatureIndex or [])
        for feature_index in feature_indices_for_scripts:
            if feature_index not in fi:
                fi.append(feature_index)
        ls.FeatureIndex = fi
        ls.FeatureCount = len(fi)

    _ensure_gdef_classes(
        font,
        bases=bases_sorted,
        marks=list(marks),
        glyph_order=glyph_order,
    )
    return len(bases_sorted)
