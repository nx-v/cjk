#!/usr/bin/env python3
"""
Build Hangul fonts from Malgun Gothic.

Two families
------------
* ``panhangul`` — conjoining jamo (U+1100.., Ext-A/B) with Malgun
  ``ljmo`` / ``vjmo`` / ``tjmo`` shaping.
* ``panhanguls`` — precomposed syllables (U+AC00..D7A3) and compatibility
  jamo (U+3131..318E).

Glyphs use a **1000×1000 em square** (``--upem``, default 1000): full-width
advances are forced to ``upem``; composed V/T overlays stay zero-width.

VS1..VS4 (axis mirrors)
-----------------------
======= ========== ========== ================================
Name    PUA        Unicode    Transform
======= ========== ========== ================================
VS1     U+E000     U+FE00     identity (no subst)
VS2     U+E001     U+FE01     mx — negate X about contour bbox center
VS3     U+E002     U+FE02     my — negate Y about contour bbox center
VS4     U+E003     U+FE03     mxy — both axes
======= ========== ========== ================================

* **Jamo (``panhangul``):** VS may follow each jamo (``L+VS V+VS T+VS``).
  ``U+FE0n`` uses cmap-14 UVS (and ``ccmp`` liga for PUA). Roles:

  * **Choseong (initial) + VS** — bbox-flips that initial only (orientation).
  * **Jungseong (medial) + VS** — X/Y-flips about the **ideographic (typo)
    center** (zero-advance V forms use local pivot ``ideo_x - upem``). The
    choseong translates on **VS flip axes ∩ the medial's layout group**
    (X-group ``ㅏ`` → right only, never down; Y-group → down; XY →
    down-right), by amounts from that choseong's bounds. No rescale. When a
    batchim follows, L+V rise just enough to clear a per-glyph batchim band.
    Choseong orientation is only from a VS on the choseong itself.
  * **Jongseong (final) + VS** — bbox-flips the final independently.
  * **Final present** — raises L+V above a per-glyph batchim floor (Y-shifted
    choseong into the lower LV band, Y-flipped jungseong into the upper),
    translate only, so the final no longer collides with the initial/medial.

* **Syllables (``panhanguls``):** ``char + VS`` / cmap-14 UVS flips the
  whole precomposed (or compat) glyph about its bbox center.

Dakuten (combining marks)
-------------------------
Stack: mkanaplus → Nexsevka → JuliaMono → Constructium → Droid Sans →
Arial Unicode MS → Gentium. Marks are fixed-height and
left-/right-aligned to CJK cell corners. Same TR → BR → TL → BL slot order as
``panyi`` via GSUB + GPOS ``mark``/``abvm``. Every orientation / layout form
(identity + ``mx``/``my``/``mxy`` + ``.em*`` / ``.up`` chains) gets corner
anchors — no VS form is skipped. Installed in both families (zero-advance
V/T bases shift local X by ``-upem``).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import os
import sys
from typing import Dict, List, Optional, Sequence, Set, Tuple

from fontTools import subset
from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.otlLib.builder import (
    buildCoverage,
    buildLigatureSubstSubtable,
    buildLookup,
    buildSingleSubstSubtable,
)
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable, woff2
from fontTools.ttLib.tables import otTables as ot
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from shared_half_cells import (
    DEFAULT_UPEM,
    empty_glyph,
    ideographic_bounds,
    ideographic_center,
    variant_glyph_name,
)
from shared_diacritics import (
    add_dakuten_mark_glyphs,
    cjk_corner_anchors,
    DAKUTEN_SLOTS,
    dakuten_mark_stack_label,
    install_dakuten_gpos,
    install_dakuten_slot_gsub,
    load_dakuten_marks_from_stack,
    resolve_dakuten_mark_font_stack,
)
from sync_obsidian_panfonts import sync_dist_to_plugin
from cdn_fonts import dist_rel, format_src_line

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "hangul")

MALGUN_FILENAME = "malgun.ttf"
FAMILY_JAMO = "panhangul"
FAMILY_SYLL = "panhanguls"
# BBox-center trim after UPM fit. 1.0 keeps full Malgun ink so cells match the
# 1000×1000 CJK em square used by build_cjk / build_yi / GlyphWiki.
LOCAL_SCALE = 1.0
# Uniform Y translate after UPM fit (target-upem units). Malgun Hangul sits
# high vs CJK/kana/Yi (typo mid ~380); negative shifts down to match.
MALGUN_Y_SHIFT = -55
# Extra Y scale about the ideographic center after UPM fit (1.0 = none).
# Malgun Hangul syllables are ~935 tall vs CJK median ~901 → ~0.96.
MALGUN_Y_SCALE = 0.9
# VS1..VS4 — axis mirrors (PUA U+E000..E003; Unicode VS U+FE00..FE03).
HANGUL_MIRROR_MODES: List[Tuple[int, bool, bool, Optional[str]]] = [
    (0xE000, False, False, None),
    (0xE001, True, False, "mx"),
    (0xE002, False, True, "my"),
    (0xE003, True, True, "mxy"),
]
VS_BASE = HANGUL_MIRROR_MODES[0][0]
VS_LAST = HANGUL_MIRROR_MODES[-1][0]
UVS_BASE = 0xFE00
UVS_LAST = UVS_BASE + len(HANGUL_MIRROR_MODES) - 1
MIRROR_SUFFIXES: Tuple[str, ...] = ("mx", "my", "mxy")

# VS ligas: ``ccmp`` early (before Hangul) for browser/DirectWrite paths where
# mid-cluster marks break the Hangul FST; ``rlig``/``liga`` keep post-shape swap.
CLUSTER_VS_FEATURE_TAGS: Tuple[str, ...] = ("ccmp", "rlig", "liga")
# Whole-glyph VS on the syllables font may use early ``ccmp`` safely.
SYLL_VS_FEATURE_TAGS: Tuple[str, ...] = ("ccmp", "rlig", "liga")

LOOKUP_FLAG_IGNORE_MARKS = 0x0008
GDEF_CLASS_MARK = 3

JAMO_RANGES: List[Tuple[int, int, str]] = [
    (0x1100, 0x11FF, "Hangul Jamo"),
    (0xA960, 0xA97F, "Hangul Jamo Extended-A"),
    (0xD7B0, 0xD7FF, "Hangul Jamo Extended-B"),
]
SYLL_RANGES: List[Tuple[int, int, str]] = [
    (0x3131, 0x318E, "Hangul Compatibility Jamo"),
    (0xAC00, 0xD7A3, "Hangul Syllables"),
]

JamoClass = str  # "L" | "V" | "T" | "other"


def vs_glyph_name(vs_cp: int) -> str:
    if VS_BASE <= vs_cp <= VS_LAST:
        return f"vs{vs_cp - VS_BASE + 1:02d}"
    if UVS_BASE <= vs_cp <= UVS_LAST:
        return f"vs{vs_cp - UVS_BASE + 1:02d}"
    raise ValueError(f"not a hangul VS codepoint: U+{vs_cp:04X}")


def is_vs_codepoint(cp: int) -> bool:
    return (VS_BASE <= cp <= VS_LAST) or (UVS_BASE <= cp <= UVS_LAST)


def font_cmap(tt: TTFont) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    return cmap


def unicodes_in_ranges(
    cmap: Dict[int, str], ranges: Sequence[Tuple[int, int, str]]
) -> Set[int]:
    out: Set[int] = set()
    for start, end, _name in ranges:
        for cp in range(start, end + 1):
            if cp in cmap:
                out.add(cp)
    return out


def resolve_malgun_path(in_dir: str) -> str:
    path = os.path.join(in_dir, MALGUN_FILENAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing Hangul source font: {path}")
    return path


def subset_malgun(src_path: str, unicodes: Set[int]) -> TTFont:
    """Subset Malgun to unicodes + GSUB closure (keeps ljmo/vjmo/tjmo)."""
    tt = TTFont(src_path, fontNumber=0)
    options = subset.Options()
    options.layout_scripts = ["hang", "DFLT"]
    options.layout_features = ["*"]
    options.glyph_names = True
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_outline = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(tt)
    return tt


def classify_jamo_forms(tt: TTFont) -> Dict[str, JamoClass]:
    """Map glyph name → L/V/T from Hangul single-subst lookups nested by chains."""
    classes: Dict[str, JamoClass] = {}
    if "GSUB" not in tt:
        return classes
    gsub = tt["GSUB"].table
    feature_lookups: Dict[str, Set[int]] = {}
    for fr in gsub.FeatureList.FeatureRecord:
        feature_lookups.setdefault(fr.FeatureTag, set()).update(
            fr.Feature.LookupListIndex
        )

    def nested_singles(chain_indices: Set[int]) -> Set[int]:
        out: Set[int] = set()
        for li in chain_indices:
            if li >= len(gsub.LookupList.Lookup):
                continue
            lu = gsub.LookupList.Lookup[li]
            if lu.LookupType != 6:
                if lu.LookupType == 1:
                    out.add(li)
                continue
            for st in lu.SubTable:
                for rec in getattr(st, "SubstLookupRecord", []) or []:
                    out.add(rec.LookupListIndex)
        return out

    tag_to_class = {"ljmo": "L", "vjmo": "V", "tjmo": "T"}
    for tag, cls in tag_to_class.items():
        singles = nested_singles(feature_lookups.get(tag, set()))
        for li in singles:
            lu = gsub.LookupList.Lookup[li]
            if lu.LookupType != 1:
                continue
            for st in lu.SubTable:
                mapping = getattr(st, "mapping", None) or {}
                for src, dst in mapping.items():
                    classes.setdefault(src, cls)
                    classes[dst] = cls
    return classes


def copy_scaled_glyph(
    glyph_set,
    src_name: str,
    *,
    upem_scale: float,
    local_scale: float,
    y_shift: float = 0.0,
    y_scale: float = 1.0,
    target_upem: Optional[int] = None,
) -> Optional[TTGlyph]:
    try:
        rec = DecomposingRecordingPen(glyph_set)
        glyph_set[src_name].draw(rec)
    except Exception as e:
        print(f"  [!] draw failed {src_name}: {e}", file=sys.stderr)
        return None
    bpen = BoundsPen(None)
    try:
        rec.replay(bpen)
    except Exception as e:
        print(f"  [!] bounds failed {src_name}: {e}", file=sys.stderr)
        return None
    if bpen.bounds is None:
        pen = TTGlyphPen(None)
        return pen.glyph()
    x0, y0, x1, y1 = bpen.bounds
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    s = upem_scale * local_scale
    dx = upem_scale * (1.0 - local_scale) * cx
    dy0 = upem_scale * (1.0 - local_scale) * cy
    # Isotropic UPM+local, then optional Y squash about ideographic center,
    # then baseline shift — shared pivot keeps L/V/T composition aligned.
    sy_extra = float(y_scale)
    if abs(sy_extra - 1.0) > 1e-9 and target_upem is not None:
        _icx, icy = ideographic_center(target_upem)
        sx = s
        sy = s * sy_extra
        dy = sy_extra * dy0 + icy * (1.0 - sy_extra) + y_shift
    else:
        sx = sy = s
        dy = dy0 + y_shift
    pen = TTGlyphPen(None)
    try:
        rec.replay(TransformPen(pen, Transform(sx, 0, 0, sy, dx, dy)))
        out = pen.glyph()
    except Exception as e:
        print(f"  [!] replay failed {src_name}: {e}", file=sys.stderr)
        return None
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


# Jungseong (medial) layout axes from vowel shape:
#   x  = vertical (ㅏ…) — sits to the right of choseong
#   y  = horizontal (ㅗ/ㅜ…) — sits below choseong
#   xy = compound (ㅘ…) — both
VowelAxis = str  # "x" | "y" | "xy"

_JUNGSEONG_VERTICAL = frozenset({"A", "AE", "YA", "YAE", "EO", "E", "YEO", "YE", "I"})
_JUNGSEONG_HORIZONTAL = frozenset({"O", "YO", "U", "YU", "EU", "ARAEA", "SSANGARAEA"})
# Modern precomposed digraph names (already mix vertical+horizontal).
_JUNGSEONG_COMPOUND = frozenset({"WA", "WAE", "OE", "WEO", "WE", "WI", "YI"})
_JUNGSEONG_CP_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x1160, 0x11A7),  # Hangul Jamo medials (+ filler)
    (0xD7B0, 0xD7C6),  # Hangul Jamo Extended-B medials
)


def jungseong_axis_from_name(name: str) -> Optional[VowelAxis]:
    """Return layout axes for a ``HANGUL JUNGSEONG …`` Unicode name."""
    prefix = "HANGUL JUNGSEONG "
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix) :]
    if rest == "FILLER":
        return None
    parts = rest.split("-")
    if any(p in _JUNGSEONG_COMPOUND for p in parts):
        return "xy"
    has_v = any(p in _JUNGSEONG_VERTICAL for p in parts)
    has_h = any(p in _JUNGSEONG_HORIZONTAL for p in parts)
    if has_v and has_h:
        return "xy"
    if has_h:
        return "y"
    if has_v:
        return "x"
    return "xy"


def _build_vowel_axis_by_cp() -> Dict[int, VowelAxis]:
    import unicodedata

    out: Dict[int, VowelAxis] = {}
    for start, end in _JUNGSEONG_CP_RANGES:
        for cp in range(start, end + 1):
            try:
                name = unicodedata.name(chr(cp))
            except ValueError:
                continue
            axis = jungseong_axis_from_name(name)
            if axis is not None:
                out[cp] = axis
    return out


VOWEL_AXIS_BY_CP: Dict[int, VowelAxis] = _build_vowel_axis_by_cp()

# Medial VS suffix → layout axes requested for the choseong shift.
V_SUFFIX_AXES: Dict[str, Set[str]] = {
    "mx": {"x"},
    "my": {"y"},
    "mxy": {"x", "y"},
}

UP_SUFFIX = "up"  # fixed upward shift when a jongseong is present


def em_variant_name(base_name: str, suffix: str) -> str:
    """Choseong layout shift from medial VS (``.emmx`` / ``.emmy`` / ``.emmxy``)."""
    return f"{base_name}.em{suffix}"


def up_variant_name(base_name: str) -> str:
    """Fixed upward-shift form when a final is present (``.up``)."""
    return f"{base_name}.{UP_SUFFIX}"


def hangul_orientation_forms(base: str, glyphs: Dict[str, TTGlyph]) -> List[str]:
    """Identity + ``.mx``/``.my``/``.mxy`` (+ ``.em*`` / ``.up`` chains if present)."""
    out: List[str] = []
    stack = [base]
    seen: Set[str] = set()
    while stack:
        name = stack.pop()
        if name in seen or name not in glyphs:
            continue
        seen.add(name)
        out.append(name)
        for sfx in MIRROR_SUFFIXES:
            stack.append(variant_glyph_name(name, sfx))
            stack.append(em_variant_name(name, sfx))
        stack.append(up_variant_name(name))
    return out


def hangul_dakuten_bases(
    seed_names: Sequence[str],
    glyphs: Dict[str, TTGlyph],
) -> List[str]:
    """All orientation / layout forms reachable from ``seed_names``.

    Includes every ``mx`` / ``my`` / ``mxy`` (and ``.em*`` / ``.up``) variant
    present in ``glyphs`` so no VS form loses MarkToBase anchors.
    """
    names: List[str] = []
    seen: Set[str] = set()
    for seed in seed_names:
        for n in hangul_orientation_forms(seed, glyphs):
            if n not in seen:
                seen.add(n)
                names.append(n)
    return names


def collect_hangul_dakuten_base_anchors(
    base_names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    """Four CJK corners; zero-advance V/T forms shift anchors by ``-upem`` in X."""
    corners = cjk_corner_anchors(target_upem)
    class_xy = {i: corners[slot] for i, (slot, _suf) in enumerate(DAKUTEN_SLOTS)}
    anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for name in base_names:
        if name not in glyphs:
            continue
        adv = int(metrics.get(name, (target_upem, 0))[0])
        dx = -int(target_upem) if adv == 0 else 0
        anchors[name] = {cid: (xy[0] + dx, xy[1]) for cid, xy in class_xy.items()}
    return anchors


def prepare_hangul_dakuten(
    *,
    in_dir: str,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    seed_bases: Sequence[str],
    target_upem: int,
) -> Optional[Tuple[List[int], List[str], Dict[str, Dict[int, Tuple[int, int]]]]]:
    """Load mark-stack diacritics into the glyph set (before FontBuilder assemble)."""
    try:
        mark_fonts = resolve_dakuten_mark_font_stack(in_dir)
    except FileNotFoundError as exc:
        print(f"  Skipping dakuten marks: {exc}", flush=True)
        return None

    label = dakuten_mark_stack_label(mark_fonts)
    print(f"  Loading dakuten marks from {label}...", flush=True)
    mark_cps, mark_glyphs = load_dakuten_marks_from_stack(mark_fonts, target_upem)
    mark_names = add_dakuten_mark_glyphs(
        mark_cps,
        mark_glyphs,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        cmap=cmap,
    )
    bases = hangul_dakuten_bases(seed_bases, glyphs)
    base_anchors = collect_hangul_dakuten_base_anchors(
        bases,
        glyphs=glyphs,
        metrics=metrics,
        target_upem=target_upem,
    )
    print(
        f"  Dakuten: {len(mark_cps)} marks × 4 corners, "
        f"{len(base_anchors)} bases (TR→BR→TL→BL; fixed H, L/R align)",
        flush=True,
    )
    if not mark_names or not base_anchors:
        return None
    return mark_cps, mark_names, base_anchors


def compile_hangul_dakuten(
    font,
    *,
    mark_cps: Sequence[int],
    mark_names: Sequence[str],
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]],
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
) -> None:
    """Install dakuten slot GSUB + corner GPOS (call after Hangul/VS GSUB exists)."""
    print("  Compiling GSUB (dakuten corner slots TR→BR→TL→BL)...", flush=True)
    install_dakuten_slot_gsub(
        font,
        mark_cps,
        glyphs=glyphs,
        glyph_order=glyph_order,
        base_names=list(base_anchors),
    )
    print("  Compiling GPOS (dakuten mark @ CJK corners)...", flush=True)
    install_dakuten_gpos(
        font,
        base_anchors=base_anchors,
        mark_cps=mark_cps,
        mark_names=mark_names,
        glyph_order=glyph_order,
        glyphs=glyphs,
        extra_script_tags=("hang",),
    )


def pair_em_suffix(use_x: bool, use_y: bool) -> Optional[str]:
    if use_x and use_y:
        return "mxy"
    if use_x:
        return "mx"
    if use_y:
        return "my"
    return None


def _glyph_bounds(
    glyphs: Dict[str, TTGlyph], name: str
) -> Optional[Tuple[float, float, float, float]]:
    g = glyphs.get(name)
    if g is None:
        return None
    rec = RecordingPen()
    try:
        g.draw(rec, None)
    except Exception:
        return None
    bpen = BoundsPen(None)
    try:
        rec.replay(bpen)
    except Exception:
        return None
    if bpen.bounds is None:
        return None
    return bpen.bounds


def vowel_axis_from_bounds(
    bounds: Tuple[float, float, float, float],
) -> VowelAxis:
    x0, y0, x1, y1 = bounds
    w = max(x1 - x0, 1.0)
    h = max(y1 - y0, 1.0)
    if w > h * 1.35:
        return "y"
    if h > w * 1.35:
        return "x"
    return "xy"


def classify_vowel_axes(
    cmap: Dict[int, str],
    v_forms: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    tt: Optional[TTFont] = None,
) -> Dict[str, VowelAxis]:
    """Map each V glyph to layout axes from Unicode group + GSUB closure."""
    axes: Dict[str, VowelAxis] = {}
    for cp, axis in VOWEL_AXIS_BY_CP.items():
        gname = cmap.get(cp)
        if gname:
            axes[gname] = axis

    # Propagate through Hangul single substitutions (vjmo forms).
    if tt is not None and "GSUB" in tt:
        gsub = tt["GSUB"].table
        changed = True
        while changed:
            changed = False
            for lu in gsub.LookupList.Lookup:
                if lu.LookupType != 1:
                    continue
                for st in lu.SubTable:
                    mapping = getattr(st, "mapping", None) or {}
                    for src, dst in mapping.items():
                        if src in axes and dst not in axes:
                            axes[dst] = axes[src]
                            changed = True
                        if dst in axes and src not in axes:
                            axes[src] = axes[dst]
                            changed = True

    for name in v_forms:
        if name in axes:
            continue
        for sfx in MIRROR_SUFFIXES:
            base = name[: -len(sfx) - 1] if name.endswith(f".{sfx}") else None
            if base and base in axes:
                axes[name] = axes[base]
                break
        if name in axes:
            continue
        b = _glyph_bounds(glyphs, name)
        axes[name] = vowel_axis_from_bounds(b) if b else "xy"

    # BBox / identity mirrors inherit the base vowel's axes.
    for name in list(axes.keys()):
        for sfx in MIRROR_SUFFIXES:
            vn = variant_glyph_name(name, sfx)
            if vn in glyphs:
                axes.setdefault(vn, axes[name])
    return axes


def ideo_local_box(
    target_upem: int, *, advance: int
) -> Tuple[float, float, float, float]:
    """Axis-aligned ideographic square in this glyph's local coordinates.

    Full-advance glyphs (choseong) use the em cell ``[0, upem] × [bottom, top]``.
    Zero-advance V/T forms are drawn at pen x=upem, so the same absolute square
    is ``[-upem, 0] × [bottom, top]`` locally.
    """
    bottom, top, _h = ideographic_bounds(target_upem)
    if advance == 0:
        return (-float(target_upem), bottom, 0.0, top)
    return (0.0, bottom, float(target_upem), top)


def nudge_into_box(
    bounds: Tuple[float, float, float, float],
    box: Tuple[float, float, float, float],
) -> Transform:
    """Translate only (no scale) so ``bounds`` edges sit inside ``box`` when possible."""
    x0, y0, x1, y1 = bounds
    bx0, by0, bx1, by1 = box
    dx = 0.0
    dy = 0.0
    if x0 < bx0:
        dx = bx0 - x0
    elif x1 > bx1:
        dx = bx1 - x1
    if y0 < by0:
        dy = by0 - y0
    elif y1 > by1:
        dy = by1 - y1
    return Transform(1, 0, 0, 1, dx, dy)


def _replay_glyph(
    base_name: str,
    glyphs: Dict[str, TTGlyph],
    transform: Transform,
    *,
    reverse: bool = False,
) -> Optional[TTGlyph]:
    base = glyphs[base_name]
    rec = RecordingPen()
    try:
        if base.isComposite():
            for comp in base.components:
                name, (xx, xy, yx, yy, dx, dy) = comp.getComponentInfo()
                child = glyphs[name]
                child_rec = RecordingPen()
                child.draw(child_rec, None)
                child_rec.replay(TransformPen(rec, Transform(xx, xy, yx, yy, dx, dy)))
        else:
            base.draw(rec, None)
    except Exception:
        try:
            base.draw(rec, None)
        except Exception:
            return None
    pen = TTGlyphPen(None)
    dest: object = ReverseContourPen(pen) if reverse else pen
    rec.replay(TransformPen(dest, transform))
    out = pen.glyph()
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


def make_layout_shift(
    base_name: str,
    glyphs: Dict[str, TTGlyph],
    *,
    advance: int,
    lsb: int,
    target_upem: int,
    shift_x: bool,
    shift_y: bool,
) -> Tuple[TTGlyph, int, int]:
    """Translate choseong from its own bounds (no rescale).

    * X: reflect about ideo mid-x (left-side initial → right).
    * Y: drop just enough that the glyph's top sits on the ideographic
      center — clearing the upper band for a Y-flipped jungseong — while
      leaving a per-glyph batchim reserve below when possible.
    """
    bounds = _glyph_bounds(glyphs, base_name)
    if bounds is None:
        return empty_glyph(), advance, lsb
    x0, y0, x1, y1 = bounds
    gcx = (x0 + x1) / 2.0
    bottom, _top, _ideo_h = ideographic_bounds(target_upem)
    icx, icy = ideographic_center(target_upem)
    dx = 0.0
    dy = 0.0
    if shift_x:
        dx = 2.0 * (icx - gcx)
    if shift_y:
        # Just enough: top edge to ideo center (upper half kept for flipped V).
        # Do not nudge this back up — even if the bottom crosses the typo floor;
        # batchim ``.up`` lifts only enough to clear the final band.
        dy = icy - y1
    t = Transform(1, 0, 0, 1, dx, dy)
    nb = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
    box = ideo_local_box(target_upem, advance=advance)
    if shift_y:
        # X-only nudge so Y clearance against the flipped medial is preserved.
        t = nudge_into_box(nb, (box[0], -1e9, box[2], 1e9)).transform(t)
    else:
        t = nudge_into_box(nb, box).transform(t)
    out = _replay_glyph(base_name, glyphs, t)
    if out is None:
        return empty_glyph(), advance, lsb
    try:
        new_lsb = int(out.xMin)
    except Exception:
        new_lsb = lsb
    return out, advance, new_lsb


def batchim_lv_bands(
    target_upem: int, glyph_height: float
) -> Tuple[float, float, float, float]:
    """Return ``(batchim_floor, lv_split, gap, ideo_top)`` for packing L/V above T.

    ``batchim_floor`` clears typical jongseong ink; ``lv_split`` divides the
    remaining upper band (choseong below / Y-flipped jungseong above);
    ``gap`` is a small clearance so the medial sits a little above the initial.
    """
    bottom, top, ideo_h = ideographic_bounds(target_upem)
    # Finals like ㅀ reach ~y=310 at 1000upem; keep a small gap above that.
    floor = bottom + max(
        ideo_h * 0.44, min(ideo_h * 0.52, glyph_height * 0.45 + ideo_h * 0.28)
    )
    floor = min(floor, bottom + ideo_h * 0.55)
    # Higher split → more room for the choseong; medial packs above it.
    split = floor + (top - floor) * 0.55
    gap = min(ideo_h * 0.08, max(ideo_h * 0.045, glyph_height * 0.15))
    return floor, split, gap, top


def make_upward_squish(
    base_name: str,
    glyphs: Dict[str, TTGlyph],
    *,
    advance: int,
    lsb: int,
    target_upem: int,
) -> Tuple[TTGlyph, int, int]:
    """Raise glyph above the batchim band (translate only, no rescale).

    * Always clear ``batchim_floor`` so L/V do not sit on the final.
    * Y-shifted choseong (``.emmy`` / ``.emmxy``): pack into
      ``[floor, split - gap]`` when the glyph fits.
    * Y-flipped jungseong (``.my`` / ``.mxy``): pack into
      ``[split + gap, top]`` — a little above the initial.
    """
    bounds = _glyph_bounds(glyphs, base_name)
    if bounds is None:
        return empty_glyph(), advance, lsb
    x0, y0, x1, y1 = bounds
    gh = max(y1 - y0, 1.0)
    floor, split, gap, top = batchim_lv_bands(target_upem, gh)
    suffix = base_name.rsplit(".", 1)[-1] if "." in base_name else ""
    y_shifted_l = suffix in ("emmy", "emmxy")
    y_flipped_v = suffix in ("my", "mxy")
    l_top = split - gap
    v_bot = split + gap

    if y_shifted_l and gh <= (l_top - floor):
        # Pin into [floor, split - gap]: top just below the medial band.
        dy = l_top - y1
        if y0 + dy < floor:
            dy = floor - y0
    elif y_flipped_v and gh <= (top - v_bot):
        # Pin into [split + gap, top]: bottom a little above the initial.
        dy = v_bot - y0
        if y1 + dy > top:
            dy = top - y1
    else:
        # Clear the batchim floor; keep top inside the square.
        dy = max(0.0, floor - y0)
        if y1 + dy > top:
            dy = max(0.0, top - y1)
        # Tall Y-flipped V: still nudge up toward v_bot when possible.
        if y_flipped_v and y0 + dy < v_bot and y1 + (v_bot - y0) <= top:
            dy = v_bot - y0

    t = Transform(1, 0, 0, 1, 0, dy)
    nb = (x0, y0 + dy, x1, y1 + dy)
    box = ideo_local_box(target_upem, advance=advance)
    t = nudge_into_box(nb, box).transform(t)
    out = _replay_glyph(base_name, glyphs, t)
    if out is None:
        return empty_glyph(), advance, lsb
    try:
        new_lsb = int(out.xMin)
    except Exception:
        new_lsb = lsb
    return out, advance, new_lsb


def add_em_variant(
    base_name: str,
    suffix: str,
    *,
    negate_x: bool,
    negate_y: bool,
    advance: int,
    lsb: int,
    target_upem: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    overlay: bool = False,
) -> str:
    """Bake choseong layout shift (``.emmx`` / ``.emmy`` / ``.emmxy``) — translate only."""
    del overlay  # choseong shifts are always in L local space
    vname = em_variant_name(base_name, suffix)
    if vname not in glyphs:
        vg, vadv, vlsb = make_layout_shift(
            base_name,
            glyphs,
            advance=advance,
            lsb=lsb,
            target_upem=target_upem,
            shift_x=negate_x,
            shift_y=negate_y,
        )
        glyph_order.append(vname)
        glyphs[vname] = vg
        metrics[vname] = (vadv, vlsb)
    return vname


def add_up_variant(
    base_name: str,
    *,
    advance: int,
    lsb: int,
    target_upem: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
) -> str:
    """Bake per-glyph batchim-clearance form (``.up``)."""
    vname = up_variant_name(base_name)
    if vname not in glyphs:
        vg, vadv, vlsb = make_upward_squish(
            base_name,
            glyphs,
            advance=advance,
            lsb=lsb,
            target_upem=target_upem,
        )
        glyph_order.append(vname)
        glyphs[vname] = vg
        metrics[vname] = (vadv, vlsb)
    return vname


def make_bbox_mirror(
    base_name: str,
    glyphs: Dict[str, TTGlyph],
    *,
    advance: int,
    lsb: int,
    negate_x: bool,
    negate_y: bool,
    target_upem: Optional[int] = None,
    about_ideo: bool = False,
) -> Tuple[TTGlyph, int, int]:
    """Bake axis mirror; jungseong uses ideo pivots and per-glyph Y rise + fit."""
    base = glyphs[base_name]
    rec = RecordingPen()
    try:
        if base.isComposite():
            for comp in base.components:
                name, (xx, xy, yx, yy, dx, dy) = comp.getComponentInfo()
                child = glyphs[name]
                child_rec = RecordingPen()
                child.draw(child_rec, None)
                child_rec.replay(TransformPen(rec, Transform(xx, xy, yx, yy, dx, dy)))
        else:
            base.draw(rec, None)
    except Exception:
        try:
            base.draw(rec, None)
        except Exception:
            return empty_glyph(), advance, lsb

    bpen = BoundsPen(None)
    try:
        rec.replay(bpen)
    except Exception:
        bpen.bounds = None
    if bpen.bounds is None:
        return empty_glyph(), advance, lsb
    x0, y0, x1, y1 = bpen.bounds
    if about_ideo and target_upem is not None:
        icx, icy = ideographic_center(target_upem)
        cx = icx - (float(target_upem) if advance == 0 else 0.0)
        cy = icy
    else:
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
    sx = -1.0 if negate_x else 1.0
    sy = -1.0 if negate_y else 1.0
    t = Transform(sx, 0, 0, sy, cx * (1.0 - sx), cy * (1.0 - sy))
    # Post-flip bounds (mirror about cx,cy).
    nx0 = cx + (x0 - cx) * sx
    nx1 = cx + (x1 - cx) * sx
    ny0 = cy + (y0 - cy) * sy
    ny1 = cy + (y1 - cy) * sy
    if nx0 > nx1:
        nx0, nx1 = nx1, nx0
    if ny0 > ny1:
        ny0, ny1 = ny1, ny0
    if about_ideo and negate_y and target_upem is not None:
        bottom, top, ideo_h = ideographic_bounds(target_upem)
        _icx, icy = ideographic_center(target_upem)
        gh = max(ny1 - ny0, 1.0)
        reserve = min(ideo_h * 0.40, max(ideo_h * 0.18, gh * 0.55))
        floor = bottom + reserve
        rise = max(0.0, floor - ny0)
        t = Transform(1, 0, 0, 1, 0, rise).transform(t)
        ny0 += rise
        ny1 += rise
        # Prefer the upper band above ideo center (choseong clears below it).
        if ny1 - ny0 <= top - icy and ny0 < icy:
            lift = icy - ny0
            t = Transform(1, 0, 0, 1, 0, lift).transform(t)
            ny0 += lift
            ny1 += lift
        if ny1 > top:
            back = top - ny1
            t = Transform(1, 0, 0, 1, 0, back).transform(t)
            ny0 += back
            ny1 += back
    if about_ideo and target_upem is not None:
        box = ideo_local_box(target_upem, advance=advance)
        t = nudge_into_box((nx0, ny0, nx1, ny1), box).transform(t)
    pen = TTGlyphPen(None)
    dest = ReverseContourPen(pen) if (sx * sy) < 0 else pen
    rec.replay(TransformPen(dest, t))
    out = pen.glyph()
    try:
        out.recalcBounds(None)
        new_lsb = int(out.xMin)
    except Exception:
        new_lsb = lsb
    return out, advance, new_lsb


def add_mirror_variants(
    base_name: str,
    *,
    advance: int,
    lsb: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: Optional[int] = None,
    about_ideo: bool = False,
) -> List[Tuple[int, str, str]]:
    installed: List[Tuple[int, str, str]] = []
    for vs_cp, neg_x, neg_y, suffix in HANGUL_MIRROR_MODES:
        if suffix is None:
            continue
        vname = variant_glyph_name(base_name, suffix)
        if vname not in glyphs:
            vg, vadv, vlsb = make_bbox_mirror(
                base_name,
                glyphs,
                advance=advance,
                lsb=lsb,
                negate_x=neg_x,
                negate_y=neg_y,
                target_upem=target_upem,
                about_ideo=about_ideo,
            )
            glyph_order.append(vname)
            glyphs[vname] = vg
            metrics[vname] = (vadv, vlsb)
        installed.append((vs_cp, suffix, vname))
    return installed


def _inject_vs(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
) -> None:
    for mode_i, (pua_cp, _nx, _ny, _suffix) in enumerate(HANGUL_MIRROR_MODES):
        vname = vs_glyph_name(pua_cp)
        if vname not in glyphs:
            glyph_order.append(vname)
            glyphs[vname] = empty_glyph()
            metrics[vname] = (0, 0)
        cmap[pua_cp] = vname
        cmap[UVS_BASE + mode_i] = vname


def build_syllable_uvs_entries(
    cmap: Dict[int, str],
    glyphs: Dict[str, TTGlyph],
) -> List[Tuple[int, int, Optional[str]]]:
    """Cmap-14 UVS for precomposed syllables and compatibility jamo only."""
    rows: List[Tuple[int, int, Optional[str]]] = []
    for cp, gname in cmap.items():
        if is_vs_codepoint(cp):
            continue
        if not (0xAC00 <= cp <= 0xD7A3 or 0x3131 <= cp <= 0x318E):
            continue
        for mode_i, (_pua, _nx, _ny, suffix) in enumerate(HANGUL_MIRROR_MODES):
            if suffix is None:
                continue
            vname = variant_glyph_name(gname, suffix)
            if vname in glyphs:
                rows.append((cp, UVS_BASE + mode_i, vname))
    return rows


def build_jamo_uvs_entries(
    cmap: Dict[int, str],
    glyphs: Dict[str, TTGlyph],
) -> List[Tuple[int, int, Optional[str]]]:
    """Cmap-14 UVS for conjoining L/V/T — consumes ``U+FE0n`` before Hangul FST.

    Browsers often fail mid-cluster mark+liga shaping for Hangul; UVS applies the
    bbox mirror at cmap time so L/V/T stay contiguous for composition.
    """
    rows: List[Tuple[int, int, Optional[str]]] = []
    for cp, gname in cmap.items():
        if is_vs_codepoint(cp):
            continue
        kind = None
        if 0x1100 <= cp <= 0x115F or 0xA960 <= cp <= 0xA97F:
            kind = "L"
        elif 0x1160 <= cp <= 0x11A7 or 0xD7B0 <= cp <= 0xD7C6:
            kind = "V"
        elif 0x11A8 <= cp <= 0x11FF or 0xD7CB <= cp <= 0xD7FB:
            kind = "T"
        if kind is None:
            continue
        for mode_i, (_pua, _nx, _ny, suffix) in enumerate(HANGUL_MIRROR_MODES):
            if suffix is None:
                continue
            vname = variant_glyph_name(gname, suffix)
            if vname in glyphs:
                rows.append((cp, UVS_BASE + mode_i, vname))
    return rows


def extend_hangul_gsub_for_mirrors(
    font,
    glyph_names: Set[str],
    glyph_order: Sequence[str],
) -> None:
    """Add ``.mx``/``.my``/``.mxy`` parallels to Hangul chain coverages + singles.

    Required when VS is applied before Hangul (UVS or early ``ccmp`` liga).
    """
    if "GSUB" not in font:
        return
    gsub = font["GSUB"].table
    gid = {name: i for i, name in enumerate(glyph_order)}
    hangul_tags = {"ljmo", "vjmo", "tjmo"}
    lookup_indices: Set[int] = set()
    for fr in gsub.FeatureList.FeatureRecord:
        if fr.FeatureTag in hangul_tags:
            lookup_indices.update(fr.Feature.LookupListIndex)
    nested: Set[int] = set()
    for li in list(lookup_indices):
        if li >= len(gsub.LookupList.Lookup):
            continue
        lu = gsub.LookupList.Lookup[li]
        if lu.LookupType != 6:
            continue
        for st in lu.SubTable:
            for rec in getattr(st, "SubstLookupRecord", []) or []:
                nested.add(rec.LookupListIndex)
    lookup_indices |= nested

    def _mirror_names(name: str) -> List[str]:
        out = []
        for sfx in MIRROR_SUFFIXES:
            vn = variant_glyph_name(name, sfx)
            if vn in glyph_names:
                out.append(vn)
        return out

    def _sort_cov(names: Sequence[str]) -> List[str]:
        return sorted(names, key=lambda n: gid.get(n, 0xFFFFFF))

    for li in sorted(lookup_indices):
        lu = gsub.LookupList.Lookup[li]
        if lu.LookupType == 1:
            for st in lu.SubTable:
                mapping = getattr(st, "mapping", None)
                if not mapping:
                    continue
                extra = {}
                for src, dst in list(mapping.items()):
                    for sfx in MIRROR_SUFFIXES:
                        vs = variant_glyph_name(src, sfx)
                        vd = variant_glyph_name(dst, sfx)
                        if vs in glyph_names and vd in glyph_names:
                            extra[vs] = vd
                mapping.update(extra)
            continue
        if lu.LookupType != 6:
            continue
        for st in lu.SubTable:
            if getattr(st, "Format", None) != 3:
                continue
            for attr in ("BacktrackCoverage", "InputCoverage", "LookAheadCoverage"):
                covs = getattr(st, attr, None) or []
                for cov in covs:
                    glyphs = list(cov.glyphs)
                    add: List[str] = []
                    for g in glyphs:
                        add.extend(_mirror_names(g))
                    if add:
                        cov.glyphs = _sort_cov(set(glyphs) | set(add))
                    else:
                        cov.glyphs = _sort_cov(glyphs)


def mark_vs_glyphs_in_gdef(font, vs_names: Sequence[str]) -> None:
    if "GDEF" not in font:
        gdef_table = newTable("GDEF")
        gdef = ot.GDEF()
        gdef.Version = 0x00010000
        gdef.GlyphClassDef = None
        gdef.AttachList = None
        gdef.LigCaretList = None
        gdef.MarkAttachClassDef = None
        gdef_table.table = gdef
        font["GDEF"] = gdef_table
    gdef = font["GDEF"].table
    if gdef.GlyphClassDef is None:
        gdef.GlyphClassDef = ot.GlyphClassDef()
        gdef.GlyphClassDef.classDefs = {}
    for name in vs_names:
        gdef.GlyphClassDef.classDefs[name] = GDEF_CLASS_MARK


def hangul_lookups_ignore_marks(font) -> int:
    if "GSUB" not in font:
        return 0
    gsub = font["GSUB"].table
    hangul_tags = {"ljmo", "vjmo", "tjmo"}
    lookup_indices: Set[int] = set()
    for fr in gsub.FeatureList.FeatureRecord:
        if fr.FeatureTag in hangul_tags:
            lookup_indices.update(fr.Feature.LookupListIndex)
    nested: Set[int] = set()
    for li in list(lookup_indices):
        if li >= len(gsub.LookupList.Lookup):
            continue
        lu = gsub.LookupList.Lookup[li]
        if lu.LookupType != 6:
            continue
        for st in lu.SubTable:
            for rec in getattr(st, "SubstLookupRecord", []) or []:
                nested.add(rec.LookupListIndex)
    lookup_indices |= nested
    for li in lookup_indices:
        lu = gsub.LookupList.Lookup[li]
        lu.LookupFlag = int(lu.LookupFlag) | LOOKUP_FLAG_IGNORE_MARKS
    return len(lookup_indices)


def install_hangul_rclt(font) -> None:
    """Expose ljmo/vjmo/tjmo under ``rclt`` as an always-on fallback."""
    if "GSUB" not in font:
        return
    gsub = font["GSUB"].table
    hangul_tags = ("ljmo", "vjmo", "tjmo")
    indices: List[int] = []
    seen: Set[int] = set()
    for tag in hangul_tags:
        for fr in gsub.FeatureList.FeatureRecord:
            if fr.FeatureTag != tag:
                continue
            for li in fr.Feature.LookupListIndex:
                if li not in seen:
                    indices.append(li)
                    seen.add(li)
    if not indices:
        return
    fr = ot.FeatureRecord()
    fr.FeatureTag = "rclt"
    fr.Feature = ot.Feature()
    fr.Feature.FeatureParams = None
    fr.Feature.LookupListIndex = list(indices)
    fr.Feature.LookupCount = len(indices)
    gsub.FeatureList.FeatureRecord.append(fr)
    fi = len(gsub.FeatureList.FeatureRecord) - 1
    gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)
    for rec in gsub.ScriptList.ScriptRecord:
        if rec.ScriptTag not in ("hang", "DFLT"):
            continue
        ls = rec.Script.DefaultLangSys
        if ls is None:
            continue
        if fi not in ls.FeatureIndex:
            ls.FeatureIndex.append(fi)
            ls.FeatureCount = len(ls.FeatureIndex)


def _ensure_gsub(font) -> ot.GSUB:
    if "GSUB" not in font:
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
    return font["GSUB"].table


def _attach_features(
    gsub: ot.GSUB,
    lookup_indices: Sequence[int],
    feature_tags: Sequence[str],
    scripts: Sequence[str] = ("DFLT", "hang", "latn"),
) -> None:
    def _ensure_script(tag: str) -> ot.Script:
        for rec in gsub.ScriptList.ScriptRecord:
            if rec.ScriptTag == tag:
                return rec.Script
        rec = ot.ScriptRecord()
        rec.ScriptTag = tag
        rec.Script = ot.Script()
        rec.Script.DefaultLangSys = None
        rec.Script.LangSysCount = 0
        rec.Script.LangSysRecord = []
        gsub.ScriptList.ScriptRecord.append(rec)
        gsub.ScriptList.ScriptCount = len(gsub.ScriptList.ScriptRecord)
        return rec.Script

    def _ensure_langsys(script: ot.Script) -> ot.DefaultLangSys:
        if script.DefaultLangSys is None:
            ls = ot.DefaultLangSys()
            ls.ReqFeatureIndex = 0xFFFF
            ls.FeatureCount = 0
            ls.FeatureIndex = []
            script.DefaultLangSys = ls
        return script.DefaultLangSys

    feat_indices: List[int] = []
    for tag in feature_tags:
        fr = ot.FeatureRecord()
        fr.FeatureTag = tag
        fr.Feature = ot.Feature()
        fr.Feature.FeatureParams = None
        fr.Feature.LookupListIndex = list(lookup_indices)
        fr.Feature.LookupCount = len(lookup_indices)
        gsub.FeatureList.FeatureRecord.append(fr)
        feat_indices.append(len(gsub.FeatureList.FeatureRecord) - 1)
    gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)

    for tag in scripts:
        script = _ensure_script(tag)
        ls = _ensure_langsys(script)
        for fi in feat_indices:
            if fi not in ls.FeatureIndex:
                ls.FeatureIndex.append(fi)
        ls.FeatureCount = len(ls.FeatureIndex)


def install_vs_ligas(
    font,
    pairs: Sequence[Tuple[str, str, str]],
    *,
    feature_tags: Sequence[str] = SYLL_VS_FEATURE_TAGS,
) -> None:
    """``base + vs → variant`` ligas (whole-glyph / syllables font)."""
    if not pairs:
        return
    liga_map: Dict[Tuple[str, ...], str] = {(base, vs): var for base, vs, var in pairs}
    items = list(liga_map.items())
    chunk_size = 4000
    liga_lookups = []
    for i in range(0, len(items), chunk_size):
        chunk = dict(items[i : i + chunk_size])
        sub = buildLigatureSubstSubtable(chunk)
        lu = buildLookup([sub])
        lu.LookupType = 4
        liga_lookups.append(lu)

    gsub = _ensure_gsub(font)
    base_index = len(gsub.LookupList.Lookup)
    gsub.LookupList.Lookup.extend(liga_lookups)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    new_indices = list(range(base_index, base_index + len(liga_lookups)))
    _attach_features(gsub, new_indices, feature_tags)


def _build_reverse_chain(
    mapping: Dict[str, str],
    lookahead_groups: Sequence[Sequence[str]],
    glyph_map: Dict[str, int],
) -> ot.ReverseChainSingleSubst:
    glyphs = sorted(mapping.keys(), key=lambda g: glyph_map[g])
    st = ot.ReverseChainSingleSubst()
    st.Format = 1
    st.Coverage = buildCoverage(glyphs, glyph_map)
    st.BacktrackCoverage = []
    st.BacktrackGlyphCount = 0
    st.LookAheadCoverage = [
        buildCoverage(sorted(set(group), key=lambda g: glyph_map[g]), glyph_map)
        for group in lookahead_groups
    ]
    st.LookAheadGlyphCount = len(st.LookAheadCoverage)
    st.Substitute = [mapping[g] for g in glyphs]
    st.GlyphCount = len(st.Substitute)
    return st


def install_jamo_component_vs(
    font,
    *,
    l_forms: Sequence[str],
    v_forms: Sequence[str],
    t_forms: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    vowel_axes: Dict[str, VowelAxis],
) -> Tuple[int, int]:
    """BBox VS ligas; medial VS shifts choseong; final present shifts L+V up.

    Returns ``(liga_rule_count, layout_lookup_count)``.
    """
    liga_pairs: List[Tuple[str, str, str]] = []
    for forms in (l_forms, v_forms, t_forms):
        for base in forms:
            for vs_cp, _nx, _ny, suffix in HANGUL_MIRROR_MODES:
                if suffix is None:
                    continue
                vname = variant_glyph_name(base, suffix)
                if vname in glyphs:
                    liga_pairs.append((base, vs_glyph_name(vs_cp), vname))

    glyph_map = {n: i for i, n in enumerate(font.getGlyphOrder())}

    def _with_bbox(forms: Sequence[str]) -> List[str]:
        out = list(forms)
        for g in forms:
            for sfx in MIRROR_SUFFIXES:
                vn = variant_glyph_name(g, sfx)
                if vn in glyphs:
                    out.append(vn)
        return sorted(set(out), key=lambda n: glyph_map.get(n, 0))

    def _strip_mirror(name: str) -> str:
        for sfx in MIRROR_SUFFIXES:
            if name.endswith(f".{sfx}"):
                return name[: -len(sfx) - 1]
        return name

    def _v_axis_set(name: str) -> Set[str]:
        a = vowel_axes.get(name) or vowel_axes.get(_strip_mirror(name), "xy")
        return {"x", "y"} if a == "xy" else {a}

    l_bases = _with_bbox(l_forms)
    # Identity + bbox V forms (medial VS targets); also used as context.
    v_bases = _with_bbox(v_forms)
    t_all = _with_bbox(t_forms)

    # L forms that may need .up after em shift.
    l_for_up = list(l_bases)
    for L in l_bases:
        for sfx in MIRROR_SUFFIXES:
            en = em_variant_name(L, sfx)
            if en in glyphs:
                l_for_up.append(en)
    l_for_up = sorted(set(l_for_up), key=lambda n: glyph_map.get(n, 0))
    v_for_up = list(v_bases)

    gsub = _ensure_gsub(font)
    staged: List[ot.Lookup] = []
    liga_feature_indices: List[int] = []
    layout_feature_indices: List[int] = []

    if liga_pairs:
        liga_map: Dict[Tuple[str, ...], str] = {
            (base, vs): var for base, vs, var in liga_pairs
        }
        items = list(liga_map.items())
        chunk_size = 4000
        for i in range(0, len(items), chunk_size):
            chunk = dict(items[i : i + chunk_size])
            sub = buildLigatureSubstSubtable(chunk)
            lu = buildLookup([sub])
            lu.LookupType = 4
            liga_feature_indices.append(len(gsub.LookupList.Lookup) + len(staged))
            staged.append(lu)

    def _add_chain(
        mapping: Dict[str, str],
        input_groups: List[List[str]],
        *,
        sequence_index: int,
    ) -> None:
        nonlocal staged
        if not mapping or not all(input_groups):
            return
        single = buildSingleSubstSubtable(mapping)
        single_lu = buildLookup([single])
        single_lu.LookupType = 1
        single_index = len(gsub.LookupList.Lookup) + len(staged)
        staged.append(single_lu)
        chain = ot.ChainContextSubst()
        chain.Format = 3
        chain.BacktrackCoverage = []
        chain.BacktrackGlyphCount = 0
        chain.InputCoverage = [
            buildCoverage(sorted(g, key=lambda n: glyph_map[n]), glyph_map)
            for g in input_groups
        ]
        chain.InputGlyphCount = len(input_groups)
        chain.LookAheadCoverage = []
        chain.LookAheadGlyphCount = 0
        rec = ot.SubstLookupRecord()
        rec.SequenceIndex = sequence_index
        rec.LookupListIndex = single_index
        chain.SubstLookupRecord = [rec]
        chain.SubstCount = 1
        chain_lu = buildLookup([chain])
        chain_lu.LookupType = 6
        layout_feature_indices.append(len(gsub.LookupList.Lookup) + len(staged))
        staged.append(chain_lu)

    layout_count = 0

    # --- Medial VS → shift choseong on (VS flip axes ∩ medial group) ---
    # X-group (ㅏ…): X only — no downward L shift even if VS has Y.
    # Y-group (ㅗ/ㅜ…): Y only. XY-group (ㅘ…): both.
    for v_sfx, v_axes in V_SUFFIX_AXES.items():
        v_sfx_glyphs = sorted(
            (
                variant_glyph_name(v, v_sfx)
                for v in v_forms
                if variant_glyph_name(v, v_sfx) in glyphs
            ),
            key=lambda n: glyph_map.get(n, 0),
        )
        if not v_sfx_glyphs:
            continue
        buckets: Dict[Tuple[bool, bool], List[str]] = {}
        for V in v_sfx_glyphs:
            use_x = "x" in v_axes and "x" in _v_axis_set(V)
            use_y = "y" in v_axes and "y" in _v_axis_set(V)
            if not use_x and not use_y:
                continue
            buckets.setdefault((use_x, use_y), []).append(V)
        for (use_x, use_y), v_group in buckets.items():
            em_sfx = pair_em_suffix(use_x, use_y)
            if em_sfx is None:
                continue
            v_group = sorted(v_group, key=lambda n: glyph_map.get(n, 0))
            l_map = {
                L: em_variant_name(L, em_sfx)
                for L in l_bases
                if em_variant_name(L, em_sfx) in glyphs
            }
            if not l_map:
                continue
            # L' V.sfx -> L.em*  (choseong shifts; medial already bbox-flipped)
            _add_chain(
                l_map,
                [
                    sorted(l_map.keys(), key=lambda n: glyph_map[n]),
                    v_group,
                ],
                sequence_index=0,
            )
            layout_count += 1

    # --- Jongseong present → shift L+V upward (.up) ---
    if t_all:
        l_up_map = {
            L: up_variant_name(L) for L in l_for_up if up_variant_name(L) in glyphs
        }
        v_up_map = {
            V: up_variant_name(V) for V in v_for_up if up_variant_name(V) in glyphs
        }
        if l_up_map and v_up_map:
            # L' V T -> L.up
            _add_chain(
                l_up_map,
                [
                    sorted(l_up_map.keys(), key=lambda n: glyph_map[n]),
                    sorted(v_up_map.keys(), key=lambda n: glyph_map[n]),
                    t_all,
                ],
                sequence_index=0,
            )
            layout_count += 1
            l_up_after = sorted(set(l_up_map.values()), key=lambda n: glyph_map[n])
            # L.up V' T -> V.up
            _add_chain(
                v_up_map,
                [
                    l_up_after,
                    sorted(v_up_map.keys(), key=lambda n: glyph_map[n]),
                    t_all,
                ],
                sequence_index=1,
            )
            layout_count += 1

    if not staged:
        return 0, 0

    gsub.LookupList.Lookup.extend(staged)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    if liga_feature_indices:
        _attach_features(gsub, liga_feature_indices, ("ccmp",))
    late = list(liga_feature_indices) + list(layout_feature_indices)
    if late:
        _attach_features(gsub, late, ("rlig", "liga"))
    return len(liga_pairs), layout_count


def _scale_glyphs_from_subset(
    tt: TTFont,
    *,
    target_upem: int,
    src_upem: int,
    local_scale: float,
    y_shift: float = 0.0,
    y_scale: float = 1.0,
) -> Tuple[
    List[str],
    Dict[str, TTGlyph],
    Dict[str, Tuple[int, int]],
    Dict[int, str],
]:
    upem_scale = target_upem / float(src_upem)
    glyph_set = tt.getGlyphSet()
    hmtx = tt["hmtx"].metrics
    old_order = [n for n in tt.getGlyphOrder() if n != ".notdef"]

    glyph_order = [".notdef"]
    glyphs: Dict[str, TTGlyph] = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}

    for name in old_order:
        adv_src, _lsb_src = hmtx.get(name, (src_upem, 0))
        # Full-width → exact em-square advance (1000×1000 at default UPM).
        # Zero-advance V/T overlays stay 0 so L+V+T still advances one cell.
        if adv_src <= 0:
            advance = 0
            scale = upem_scale
        else:
            advance = target_upem
            # Map the source advance box onto the target em square.
            scale = target_upem / float(adv_src)
        g = copy_scaled_glyph(
            glyph_set,
            name,
            upem_scale=scale,
            local_scale=local_scale,
            y_shift=y_shift,
            y_scale=y_scale,
            target_upem=target_upem,
        )
        if g is None:
            continue
        try:
            g.recalcBounds(None)
            lsb = int(g.xMin)
        except Exception:
            lsb = 0
        glyph_order.append(name)
        glyphs[name] = g
        metrics[name] = (advance, lsb)

    cmap: Dict[int, str] = {}
    for cp, gname in font_cmap(tt).items():
        if gname in glyphs:
            cmap[cp] = gname
    return glyph_order, glyphs, metrics, cmap


def _save_font(
    fb: FontBuilder,
    out_dir: str,
    family: str,
    *,
    write_ttf: bool,
    write_woff2: bool,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{family}.ttf")
    fb.save(out_path)
    if write_woff2:
        print(f"  Compressing {family}.woff2...", flush=True)
        woff2.compress(out_path, out_path.replace(".ttf", ".woff2"))
    if not write_ttf:
        try:
            os.remove(out_path)
        except OSError:
            pass
    return out_path


def build_jamo_font(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    local_scale: float = LOCAL_SCALE,
    y_shift: float = MALGUN_Y_SHIFT,
    y_scale: float = MALGUN_Y_SCALE,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    src_path = resolve_malgun_path(in_dir)
    print(f"\n=== {FAMILY_JAMO} (conjoining jamo) ===", flush=True)
    print(f"Source: {src_path}", flush=True)
    src_tt = TTFont(src_path, fontNumber=0)
    try:
        src_cmap = font_cmap(src_tt)
        unicodes = unicodes_in_ranges(src_cmap, JAMO_RANGES)
        if limit is not None:
            unicodes = set(sorted(unicodes)[:limit])
            print(f"  Limiting to {len(unicodes)} codepoints (--limit)", flush=True)
        else:
            print(f"  Jamo codepoints in cmap: {len(unicodes)}", flush=True)
        src_upem = int(src_tt["head"].unitsPerEm)
    finally:
        src_tt.close()

    print("  Subsetting Malgun (jamo + GSUB closure)...", flush=True)
    tt = subset_malgun(src_path, unicodes)
    jamo_class = classify_jamo_forms(tt)
    n_l = sum(1 for c in jamo_class.values() if c == "L")
    n_v = sum(1 for c in jamo_class.values() if c == "V")
    n_t = sum(1 for c in jamo_class.values() if c == "T")
    print(f"  Jamo forms classified: L={n_l} V={n_v} T={n_t}", flush=True)

    print(
        f"  Scaling glyphs (upem {src_upem}->{target_upem}, local {local_scale:g}, "
        f"y_shift {y_shift:g}, y_scale {y_scale:g})...",
        flush=True,
    )
    glyph_order, glyphs, metrics, cmap = _scale_glyphs_from_subset(
        tt,
        target_upem=target_upem,
        src_upem=src_upem,
        local_scale=local_scale,
        y_shift=y_shift,
        y_scale=y_scale,
    )
    _inject_vs(glyph_order, glyphs, metrics, cmap)
    vs_names = [vs_glyph_name(m[0]) for m in HANGUL_MIRROR_MODES]

    l_forms = sorted(n for n, c in jamo_class.items() if c == "L" and n in glyphs)
    v_forms = sorted(n for n, c in jamo_class.items() if c == "V" and n in glyphs)
    t_forms = sorted(n for n, c in jamo_class.items() if c == "T" and n in glyphs)
    # Include cmap'd L/V/T that classification might miss as identity forms.
    for cp, gname in list(cmap.items()):
        if is_vs_codepoint(cp) or gname not in glyphs:
            continue
        kind = None
        if 0x1100 <= cp <= 0x115F or 0xA960 <= cp <= 0xA97F:
            kind = "L"
        elif 0x1160 <= cp <= 0x11A7 or 0xD7B0 <= cp <= 0xD7C6:
            kind = "V"
        elif 0x11A8 <= cp <= 0x11FF or 0xD7CB <= cp <= 0xD7FB:
            kind = "T"
        if kind == "L" and gname not in l_forms:
            l_forms.append(gname)
        elif kind == "V" and gname not in v_forms:
            v_forms.append(gname)
        elif kind == "T" and gname not in t_forms:
            t_forms.append(gname)
    l_forms = sorted(set(l_forms))
    v_forms = sorted(set(v_forms))
    t_forms = sorted(set(t_forms))

    print(
        "  Installing L/V/T bbox mirrors; per-jamo shifts; " "ideo-square fit...",
        flush=True,
    )
    for base in l_forms + t_forms:
        adv, lsb = metrics[base]
        add_mirror_variants(
            base,
            advance=adv,
            lsb=lsb,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
    # Jungseong: X and Y about ideographic center.
    for base in v_forms:
        adv, lsb = metrics[base]
        add_mirror_variants(
            base,
            advance=adv,
            lsb=lsb,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            about_ideo=True,
        )
    # Choseong layout shifts driven by medial VS (L.em* only).
    l_em_modes = [("mx", True, False), ("my", False, True), ("mxy", True, True)]
    for base in l_forms:
        bases = [base] + [
            variant_glyph_name(base, sfx)
            for sfx in MIRROR_SUFFIXES
            if variant_glyph_name(base, sfx) in glyphs
        ]
        for b in bases:
            adv, lsb = metrics[b]
            for suffix, neg_x, neg_y in l_em_modes:
                add_em_variant(
                    b,
                    suffix,
                    negate_x=neg_x,
                    negate_y=neg_y,
                    advance=adv,
                    lsb=lsb,
                    target_upem=target_upem,
                    glyph_order=glyph_order,
                    glyphs=glyphs,
                    metrics=metrics,
                    overlay=False,
                )
    # Fixed upward shift when a jongseong is present (L, L.em*, V, V.bbox*).
    l_up_sources: Set[str] = set()
    for base in l_forms:
        candidates = [base]
        for sfx in MIRROR_SUFFIXES:
            bn = variant_glyph_name(base, sfx)
            if bn in glyphs:
                candidates.append(bn)
        for c in candidates:
            l_up_sources.add(c)
            for em_sfx in MIRROR_SUFFIXES:
                en = em_variant_name(c, em_sfx)
                if en in glyphs:
                    l_up_sources.add(en)
    for b in sorted(l_up_sources):
        adv, lsb = metrics[b]
        add_up_variant(
            b,
            advance=adv,
            lsb=lsb,
            target_upem=target_upem,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
    for base in v_forms:
        bases = [base] + [
            variant_glyph_name(base, sfx)
            for sfx in MIRROR_SUFFIXES
            if variant_glyph_name(base, sfx) in glyphs
        ]
        for b in bases:
            adv, lsb = metrics[b]
            add_up_variant(
                b,
                advance=adv,
                lsb=lsb,
                target_upem=target_upem,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
            )

    vowel_axes = classify_vowel_axes(cmap, v_forms, glyphs, tt)
    n_x = sum(1 for a in vowel_axes.values() if a == "x")
    n_y = sum(1 for a in vowel_axes.values() if a == "y")
    n_xy = sum(1 for a in vowel_axes.values() if a == "xy")
    print(
        f"  Vowel axis groups: x={n_x} y={n_y} xy={n_xy}",
        flush=True,
    )

    dakuten = prepare_hangul_dakuten(
        in_dir=in_dir,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        cmap=cmap,
        seed_bases=l_forms + v_forms + t_forms,
        target_upem=target_upem,
    )

    uvs_rows = build_jamo_uvs_entries(cmap, glyphs)
    hangul_cps = [cp for cp in cmap if not is_vs_codepoint(cp)]
    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)

    print(
        f"  Assembling font ({len(glyphs) - 1} glyphs, {len(hangul_cps)} CPs, "
        f"{len(uvs_rows)} UVS)...",
        flush=True,
    )
    fb = FontBuilder(target_upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    # Empty uvs=[] still emits cmap format-14; Chromium OTS rejects that.
    if uvs_rows:
        fb.setupCharacterMap(cmap, uvs=uvs_rows)
    else:
        fb.setupCharacterMap(cmap)
    fb.setupNameTable(
        {
            "familyName": FAMILY_JAMO,
            "styleName": "Regular",
            "uniqueFontIdentifier": FAMILY_JAMO,
            "fullName": FAMILY_JAMO,
            "psName": FAMILY_JAMO,
            "version": "Version 1.000",
        }
    )
    fb.setupOS2(
        sTypoAscender=ascent,
        sTypoDescender=descent,
        sTypoLineGap=0,
        usWinAscent=ascent,
        usWinDescent=abs(descent),
        achVendID="pHg ",
    )
    fb.setupPost()

    if "GSUB" in tt:
        fb.font["GSUB"] = copy.deepcopy(tt["GSUB"])
        extend_hangul_gsub_for_mirrors(fb.font, set(glyphs), glyph_order)
        n_flagged = hangul_lookups_ignore_marks(fb.font)
        install_hangul_rclt(fb.font)
        print(
            f"  Ported ljmo/vjmo/tjmo; mirrored coverages; "
            f"IgnoreMarks on {n_flagged} lookups; rclt.",
            flush=True,
        )
    if "GDEF" in tt:
        try:
            fb.font["GDEF"] = copy.deepcopy(tt["GDEF"])
        except Exception:
            pass
    mark_vs_glyphs_in_gdef(fb.font, vs_names)

    n_liga, n_swap = install_jamo_component_vs(
        fb.font,
        l_forms=l_forms,
        v_forms=v_forms,
        t_forms=t_forms,
        glyphs=glyphs,
        vowel_axes=vowel_axes,
    )
    print(
        f"  Component VS: {n_liga} ligas; {n_swap} layout lookups",
        flush=True,
    )
    if dakuten is not None:
        mark_cps, mark_names, base_anchors = dakuten
        compile_hangul_dakuten(
            fb.font,
            mark_cps=mark_cps,
            mark_names=mark_names,
            base_anchors=base_anchors,
            glyphs=glyphs,
            glyph_order=glyph_order,
        )

    out_path = _save_font(
        fb, out_dir, FAMILY_JAMO, write_ttf=write_ttf, write_woff2=write_woff2
    )
    tt.close()
    return out_path, len(glyphs) - 1, sorted(cmap.keys())


def build_syllables_font(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    local_scale: float = LOCAL_SCALE,
    y_shift: float = MALGUN_Y_SHIFT,
    y_scale: float = MALGUN_Y_SCALE,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    src_path = resolve_malgun_path(in_dir)
    print(f"\n=== {FAMILY_SYLL} (syllables + compat jamo) ===", flush=True)
    print(f"Source: {src_path}", flush=True)
    src_tt = TTFont(src_path, fontNumber=0)
    try:
        src_cmap = font_cmap(src_tt)
        unicodes = unicodes_in_ranges(src_cmap, SYLL_RANGES)
        if limit is not None:
            unicodes = set(sorted(unicodes)[:limit])
            print(f"  Limiting to {len(unicodes)} codepoints (--limit)", flush=True)
        else:
            print(f"  Syllable/compat CPs in cmap: {len(unicodes)}", flush=True)
        src_upem = int(src_tt["head"].unitsPerEm)
    finally:
        src_tt.close()

    print("  Subsetting Malgun (syllables + compat)...", flush=True)
    tt = subset_malgun(src_path, unicodes)

    print(
        f"  Scaling glyphs (upem {src_upem}->{target_upem}, local {local_scale:g}, "
        f"y_shift {y_shift:g}, y_scale {y_scale:g})...",
        flush=True,
    )
    glyph_order, glyphs, metrics, cmap = _scale_glyphs_from_subset(
        tt,
        target_upem=target_upem,
        src_upem=src_upem,
        local_scale=local_scale,
        y_shift=y_shift,
        y_scale=y_scale,
    )
    # Drop any leftover jamo GSUB from subset — syllables font is whole-glyph only.
    if "GSUB" in tt:
        del tt["GSUB"]
    if "GDEF" in tt:
        del tt["GDEF"]

    _inject_vs(glyph_order, glyphs, metrics, cmap)
    vs_names = [vs_glyph_name(m[0]) for m in HANGUL_MIRROR_MODES]

    print("  Installing whole-glyph axis-mirror variants...", flush=True)
    liga_pairs: List[Tuple[str, str, str]] = []
    hangul_cps = [cp for cp in cmap if not is_vs_codepoint(cp)]
    for cp in hangul_cps:
        base = cmap[cp]
        if base not in glyphs:
            continue
        adv, lsb = metrics[base]
        installed = add_mirror_variants(
            base,
            advance=adv,
            lsb=lsb,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
        for vs_cp, _suffix, vname in installed:
            liga_pairs.append((base, vs_glyph_name(vs_cp), vname))

    syll_seeds = [cmap[cp] for cp in hangul_cps if cmap[cp] in glyphs]
    dakuten = prepare_hangul_dakuten(
        in_dir=in_dir,
        glyph_order=glyph_order,
        glyphs=glyphs,
        metrics=metrics,
        cmap=cmap,
        seed_bases=syll_seeds,
        target_upem=target_upem,
    )

    uvs_rows = build_syllable_uvs_entries(cmap, glyphs)
    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)

    print(
        f"  Assembling font ({len(glyphs) - 1} glyphs, {len(hangul_cps)} CPs)...",
        flush=True,
    )
    fb = FontBuilder(target_upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    # Empty uvs=[] still emits cmap format-14; Chromium OTS rejects that.
    if uvs_rows:
        fb.setupCharacterMap(cmap, uvs=uvs_rows)
    else:
        fb.setupCharacterMap(cmap)
    fb.setupNameTable(
        {
            "familyName": FAMILY_SYLL,
            "styleName": "Regular",
            "uniqueFontIdentifier": FAMILY_SYLL,
            "fullName": FAMILY_SYLL,
            "psName": FAMILY_SYLL,
            "version": "Version 1.000",
        }
    )
    fb.setupOS2(
        sTypoAscender=ascent,
        sTypoDescender=descent,
        sTypoLineGap=0,
        usWinAscent=ascent,
        usWinDescent=abs(descent),
        achVendID="pHg ",
    )
    fb.setupPost()

    mark_vs_glyphs_in_gdef(fb.font, vs_names)
    print(f"  Compiling VS ligas ({len(liga_pairs)} rules)...", flush=True)
    install_vs_ligas(fb.font, liga_pairs, feature_tags=SYLL_VS_FEATURE_TAGS)
    if dakuten is not None:
        mark_cps, mark_names, base_anchors = dakuten
        compile_hangul_dakuten(
            fb.font,
            mark_cps=mark_cps,
            mark_names=mark_names,
            base_anchors=base_anchors,
            glyphs=glyphs,
            glyph_order=glyph_order,
        )

    out_path = _save_font(
        fb, out_dir, FAMILY_SYLL, write_ttf=write_ttf, write_woff2=write_woff2
    )
    tt.close()
    return out_path, len(glyphs) - 1, sorted(cmap.keys())


def unicode_range_css(codepoints: Sequence[int]) -> str:
    cps = sorted(set(codepoints))
    if not cps:
        return ""
    runs: List[str] = []
    run_start = prev = cps[0]
    for cp in cps[1:]:
        if cp == prev + 1:
            prev = cp
            continue
        if run_start == prev:
            runs.append(f"U+{run_start:X}")
        else:
            runs.append(f"U+{run_start:X}-{prev:X}")
        run_start = prev = cp
    if run_start == prev:
        runs.append(f"U+{run_start:X}")
    else:
        runs.append(f"U+{run_start:X}-{prev:X}")
    return ", ".join(runs)


def write_css(
    out_dir: str,
    jamo_cps: Sequence[int],
    syll_cps: Sequence[int],
) -> None:
    css_path = os.path.join(out_dir, "panhangul.css")
    lines = [
        "/* Auto-generated Hangul fonts from Malgun Gothic */",
        "/* panhangul = conjoining jamo; panhanguls = syllables + compat */",
        "/* Local src first; GitHub raw as fallback. */",
        "",
    ]
    for family, cps in ((FAMILY_JAMO, jamo_cps), (FAMILY_SYLL, syll_cps)):
        urange = unicode_range_css(cps)
        lines += [
            "@font-face {",
            f"  font-family: '{family}';",
            format_src_line(
                dist_rel("hangul", f"{family}.woff2"),
                fmt="woff2",
                local=(
                    (f"./{family}.woff2", "woff2"),
                    (f"./{family}.ttf", "truetype"),
                ),
                indent="  ",
            ),
            "  font-weight: normal;",
            "  font-style: normal;",
            "  font-display: swap;",
        ]
        if urange:
            lines.append(f"  unicode-range: {urange};")
        lines += ["}", ""]

    with open(css_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {css_path}")

    fontlist_path = os.path.join(out_dir, "panhangul-fontlist.css")
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(
            "/* Hangul font families */\n"
            ":root {\n"
            f"  --font-panhangul: '{FAMILY_JAMO}', '{FAMILY_SYLL}';\n"
            f"  --font-panhangul-jamo: '{FAMILY_JAMO}';\n"
            f"  --font-panhanguls: '{FAMILY_SYLL}';\n"
            "}\n"
        )
    print(f"Wrote {fontlist_path}")


def build_all(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    local_scale: float = LOCAL_SCALE,
    y_shift: float = MALGUN_Y_SHIFT,
    y_scale: float = MALGUN_Y_SCALE,
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> None:
    print(f"Hangul source: {MALGUN_FILENAME}")
    print(
        f"  VS U+{VS_BASE:04X}-U+{VS_LAST:04X} / U+{UVS_BASE:04X}-U+{UVS_LAST:04X}: "
        "identity / mx / my / mxy"
    )
    print(
        f"  Jamo ({FAMILY_JAMO}): L VS=orientation; V VS=ideo-flip + per-jamo L shift; "
        f"T present=per-jamo L+V clearance; all fit in ideo square"
    )
    print(f"  Syllables ({FAMILY_SYLL}): whole-glyph VS / UVS")
    print(
        "  Dakuten: mkanaplus + Nexsevka + JuliaMono + Constructium + "
        "Droid Sans + Arial Unicode MS + Gentium \\p{M} @ CJK corners "
        "(TR→BR→TL→BL; fixed H, L/R align; both families)"
    )
    print(f"  Local scale: {local_scale:g} about bbox center")
    print(f"  Y shift: {y_shift:g} (align Malgun to CJK/Yi typo mid)")
    print(f"  Y scale: {y_scale:g} about ideo center (match CJK height)")
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"  Formats: {fmt_note}")
    print("  Building panhangul + panhanguls in parallel...", flush=True)

    # Two independent fonts — run concurrently (separate processes so
    # FontBuilder/CPU work is not serialized by the GIL).
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as ex:
        fut_jamo = ex.submit(
            build_jamo_font,
            in_dir,
            out_dir,
            target_upem,
            limit=limit,
            local_scale=local_scale,
            y_shift=y_shift,
            y_scale=y_scale,
            write_ttf=write_ttf,
            write_woff2=write_woff2,
        )
        fut_syll = ex.submit(
            build_syllables_font,
            in_dir,
            out_dir,
            target_upem,
            limit=limit,
            local_scale=local_scale,
            y_shift=y_shift,
            y_scale=y_scale,
            write_ttf=write_ttf,
            write_woff2=write_woff2,
        )
        jamo_path, jamo_count, jamo_cps = fut_jamo.result()
        syll_path, syll_count, syll_cps = fut_syll.result()

    if jamo_count or syll_count:
        write_css(out_dir, jamo_cps, syll_cps)
    print(
        f"\nDone: {jamo_path} ({jamo_count} glyphs); "
        f"{syll_path} ({syll_count} glyphs)",
        flush=True,
    )
    sync_dist_to_plugin("hangul", out_dir)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build panhangul (jamo) + panhanguls (syllables) from Malgun"
    )
    p.add_argument("--in", dest="in_dir", default=IN_DIR)
    p.add_argument("--out", dest="out_dir", default=OUT_DIR)
    p.add_argument("--upem", type=int, default=DEFAULT_UPEM)
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Use only the first N codepoints per font (smoke test)",
    )
    p.add_argument(
        "--local-scale",
        type=float,
        default=LOCAL_SCALE,
        help=f"BBox-center scale (default {LOCAL_SCALE})",
    )
    p.add_argument(
        "--y-shift",
        type=float,
        default=MALGUN_Y_SHIFT,
        help=f"Vertical shift in target-upem units after fit "
        f"(default {MALGUN_Y_SHIFT}; negative = down)",
    )
    p.add_argument(
        "--y-scale",
        type=float,
        default=MALGUN_Y_SCALE,
        help=f"Y squash about ideographic center after fit "
        f"(default {MALGUN_Y_SCALE}; match CJK ink height)",
    )
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument(
        "--ttf-only",
        "--no-woff2",
        action="store_true",
        help="Write TTF only (skip WOFF2)",
    )
    fmt.add_argument(
        "--woff2-only",
        action="store_true",
        help="Write WOFF2 only (drop intermediate TTF after compress)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_all(
        args.in_dir,
        args.out_dir,
        args.upem,
        limit=args.limit,
        local_scale=args.local_scale,
        y_shift=args.y_shift,
        y_scale=args.y_scale,
        write_ttf=not args.woff2_only,
        write_woff2=not args.ttf_only,
    )
