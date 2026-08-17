#!/usr/bin/env python3
"""
Build Pan-CJK pigeonhole subfonts.

Claims CJK/Tangut codepoints from priority-ordered source fonts, buckets them
into 256-codepoint blocks (cp >> 8), and builds each TTF/WOFF2 from scratch by
copying (decomposed, scaled) glyphs one-by-one into a fresh FontBuilder font.

Five faces per bucket (filename / family stem = ``{hex}`` / ``{hex}h`` /
``{hex}t`` / ``{hex}qv`` / ``{hex}qh``)::

    (none)  base forms + ca/nhay (all mark orientations); mark niche = 1/4
                (base occupies 3/4)
    h       base forms + D4 + half-cell slices (FE0B–FE0F)
    t       base forms + D4 + third-cell niches (VS17–VS26; FE0B zero-width)
    qv      base forms + D4 + vertical quarter niches (VS13–14, VS27–33)
    qh      base forms + D4 + horizontal quarter niches (VS15–16, VS34–40)

Builds run as variant waves: all buckets for one face in parallel, then the
next face (base → h → t → qv → qh).

Also writes edenia-cjk.css (@font-face) and fontlist.css (CSS-safe stack).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
import tempfile
import time
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, woff2
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from cape_weightor import bolden_ttglyph
from cjk_diacritics import (
    PLANGOTHIC_P2_FILENAME,
    MARK_CPS,
    MARK_BASE_SQUISH_FACTOR,
    MARK_NICHE_FRAC,
    SIDE_SELECTOR_CPS,
    SQUISH_FACTOR,
    SQUISH_PUA_CPS,
    compile_marks_layout,
    install_cjk_composition_gsub,
    prepare_marks,
    prepare_squish_vs_access,
)
from shared_half_cells import (
    NUOSU_FILENAME,
    TRANSFORM_MODES,
    UVS_BASE,
    UVS_LAST,
    VS_BASE,
    VS_LAST,
    add_d4_variant_glyphs,
    fit_glyph_to_ideographic_cell,
    is_yi_cp,
    load_inventory,
    make_standalone_glyph,
    record_glyph,
    uvs_selector_for_mode,
    vs_glyph_name,
)
from shared_third_cells import (
    install_third_cell_gsub,
    prepare_third_cells,
)
from shared_quarter_cells import (
    QUARTER_FACE_H,
    QUARTER_FACE_V,
    install_quarter_cell_gsub,
    prepare_quarter_cells,
)
from edenia_names import (
    CJK_FACE_CSS_ORDER,
    CJK_FACE_VARIANTS,
    CSS_CJK,
    STACK_CJK_TAIL,
    cjk_face_id,
    family_cjk,
    ps_cjk,
    split_cjk_face_id,
)
from sync_edenian_fonts import sync_dist_to_plugin
from cdn_fonts import dist_rel, format_src_line

# ---------- Directories ----------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
CJK_FOLDER = "cjk"
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", CJK_FOLDER)

DEFAULT_UPEM = 1000

CSS_FAMILY = "edenia cjk"

# ---------- Source priority (highest first) ----------
# Each entry: (filename, local_scale, weightor)
# * local_scale — isotropic scale about contour bbox center after UPM fit
#   (advance width unchanged).
# * weightor — CAPE Weightor **Weight** mode only after fit (>1 bolden,
#   <1 lighten, 1.0 = none). Outer width/height are preserved. Do **not** use
#   Width-mode / niche CAPE here (CJK niches are composites; Width is kana).

# Harmony target @ 1000 UPM (median of these sources): ink ≈ 874, stem ≈ 73.
# local_scale = target_ink / native_ink; weightor = target_stem / (stem * scale).
PRIORITY_FONTS: List[Tuple[str, float, float]] = [
    ("NGULIM.ttf", 1.10, 1.2),
    ("Han-Nom Gothic 1.32.otf", 0.95, 1.05),
    ("msyh.ttc", 0.95, 1.05),
    ("LXGWClearGothic-Regular.ttf", 1.0, 1.0),
    ("LXGWXiHeiMN.ttf", 1.0, 1.0),
    ("LXGWXiHeiCL.ttf", 1.0, 1.0),
    ("LXGWNeoXiHeiPlus.ttf", 1.0, 1.0),
    ("LXGWNeoXiHeiScreenFull.ttf", 1.0, 1.0),
    ("ChironHeiHK-R.ttf", 0.95, 1.05),
    ("SukimaGothic.ttf", 0.95, 1.05),
    ("YshiYuanGothicCleaned.ttf", 0.95, 1.05),
    ("ChocolateClassicalSans-Regular.ttf", 0.95, 1.05),
    ("Gothic Nguyen Regular.ttf", 0.95, 1.05),
    ("PlangothicP1-Regular.ttf", 0.95, 1.05),
    ("PlangothicP2-Regular.ttf", 0.95, 1.05),
]

PRIORITY_FONT_NAMES: List[str] = [name for name, _scale, _w in PRIORITY_FONTS]
FONT_LOCAL_SCALE: Dict[str, float] = {name: scale for name, scale, _w in PRIORITY_FONTS}
FONT_WEIGHTOR: Dict[str, float] = {name: w for name, _scale, w in PRIORITY_FONTS}

# ---------- Unicode ranges (inclusive) ----------

CHAR_RANGES: List[Tuple[int, int, str]] = [
    (0x2E80, 0x2EFF, "CJK Radicals Supplement"),
    (0x2F00, 0x2FDF, "Kangxi Radicals"),
    (0x04E00, 0x09FFF, "CJK URO"),
    (0x03400, 0x04DBF, "CJK Ext A"),
    (0x20000, 0x2A6DF, "CJK Ext B"),
    (0x2A700, 0x2B73F, "CJK Ext C"),
    (0x2B740, 0x2B81F, "CJK Ext D"),
    (0x2B820, 0x2CEAF, "CJK Ext E"),
    (0x2CEB0, 0x2EBEF, "CJK Ext F"),
    (0x30000, 0x3134F, "CJK Ext G"),
    (0x31350, 0x323AF, "CJK Ext H"),
    (0x2EBF0, 0x2EE5F, "CJK Ext I"),
    (0x323B0, 0x3347F, "CJK Ext J"),
    (0x0FA00, 0x0FAFF, "CJK Compat"),
    (0x2F800, 0x2FA1F, "CJK Compat Supplement"),
    (0x17000, 0x187FF, "Tangut"),
    (0x18D00, 0x18D7F, "Tangut Supplement"),
    (0x18800, 0x18AFF, "Tangut Components"),
    (0x18D80, 0x18DFF, "Tangut Components Supplement"),
    (0x18B00, 0x18CFF, "Khitan Small Script"),
]

# (out_cp, source_path, src_cp) — base Unicode claims only; D4 variants in-font
BucketEntry = Tuple[int, str, int]


def ranges_to_set(ranges: Iterable[Tuple[int, int, str]]) -> Set[int]:
    s: Set[int] = set()
    for start, end, _name in ranges:
        s.update(range(start, end + 1))
    return s


def font_cmap(tt: TTFont) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    return cmap


def is_empty_outline(tt: TTFont, glyph_name: str) -> bool:
    if "glyf" in tt:
        if glyph_name not in tt["glyf"]:
            return True
        g = tt["glyf"][glyph_name]
        if g.isComposite():
            return False
        return g.numberOfContours <= 0
    if "CFF " in tt:
        top = tt["CFF "].cff.topDictIndex[0]
        cs = top.CharStrings
        if glyph_name in cs:
            return len(cs[glyph_name].program) == 0
        return True
    if "CFF2" in tt:
        top = tt["CFF2"].cff.topDictIndex[0]
        cs = top.CharStrings
        if glyph_name in cs:
            return len(cs[glyph_name].program) == 0
        return True
    return False


def is_empty_glyph(tt: TTFont, glyph_name: str) -> bool:
    if glyph_name in {".notdef", ".null", "nonmarkingreturn"}:
        return True
    return is_empty_outline(tt, glyph_name)


def glyph_name_for_cp(cp: int) -> str:
    return f"u{cp:04X}" if cp <= 0xFFFF else f"u{cp:05X}"


def empty_glyph() -> TTGlyph:
    g = TTGlyph()
    g.numberOfContours = 0
    g.xMin = g.yMin = g.xMax = g.yMax = 0
    return g


def _scale_glyph_about_bounds_center(glyph: TTGlyph, factor: float) -> TTGlyph:
    """Isotropic scale about contour bbox center (advance unchanged by caller)."""
    if abs(factor - 1.0) < 1e-9:
        return glyph
    try:
        glyph.recalcBounds(None)
        cx = (glyph.xMin + glyph.xMax) / 2.0
        cy = (glyph.yMin + glyph.yMax) / 2.0
    except Exception:
        return glyph
    rec = RecordingPen()
    glyph.draw(rec, None)
    pen = TTGlyphPen(None)
    t = Transform(factor, 0, 0, factor, (1.0 - factor) * cx, (1.0 - factor) * cy)
    rec.replay(TransformPen(pen, t))
    out = pen.glyph()
    try:
        out.recalcBounds(None)
    except Exception:
        pass
    return out


class SourceFont:
    """Lazy-open source font with cmap and drawing helpers."""

    def __init__(
        self,
        path: str,
        local_scale: float = 1.0,
        weightor: float = 1.0,
    ):
        self.path = path
        self.local_scale = float(local_scale)
        self.weightor = float(weightor)
        self.tt = TTFont(path, fontNumber=0)
        self.upem = int(self.tt["head"].unitsPerEm)
        self.cmap = font_cmap(self.tt)
        self.glyph_set = self.tt.getGlyphSet()
        self.hmtx = self.tt["hmtx"].metrics

    def close(self) -> None:
        try:
            self.tt.close()
        except Exception:
            pass

    def copy_glyph(
        self,
        src_name: str,
        target_upem: int,
        flip_x: bool = False,
        flip_y: bool = False,
    ) -> Optional[Tuple[TTGlyph, int, int]]:
        """Decompose + UPM scale + optional local scale / weightor / mirrors.

        ``local_scale`` (per source font) scales outlines about the contour
        bounding-box center; advance width stays the UPM-scaled source advance.
        ``weightor`` then boldens/lightens via CAPE Weightor Weight mode only
        (bounds preserved). Width-mode CAPE is not used for CJK.
        Mirrors also flip about that same contour center.
        """
        if is_empty_outline(self.tt, src_name):
            return None

        upem_scale = target_upem / self.upem
        ls = self.local_scale
        advance_src, lsb_src = self.hmtx[src_name]
        advance = otRound(advance_src * upem_scale)

        try:
            rec = DecomposingRecordingPen(self.glyph_set)
            self.glyph_set[src_name].draw(rec)
        except Exception as e:
            print(
                f"  [!] draw failed {os.path.basename(self.path)}:{src_name}: {e}",
                file=sys.stderr,
            )
            return None

        need_center = abs(ls - 1.0) > 1e-9 or flip_x or flip_y
        cx = cy = 0.0
        if need_center:
            bpen = BoundsPen(None)
            try:
                rec.replay(bpen)
            except Exception as e:
                print(
                    f"  [!] bounds failed {os.path.basename(self.path)}:{src_name}: {e}",
                    file=sys.stderr,
                )
                return None
            if bpen.bounds is None:
                return None
            x_min, y_min, x_max, y_max = bpen.bounds
            cx = (x_min + x_max) / 2.0
            cy = (y_min + y_max) / 2.0

        # p' = upem * (sign * ls * (p - c) + c)
        # flip_y reflects X; flip_x reflects Y (about contour center).
        sign_x = -1.0 if flip_y else 1.0
        sign_y = -1.0 if flip_x else 1.0
        sx = upem_scale * ls * sign_x
        sy = upem_scale * ls * sign_y
        dx = upem_scale * cx * (1.0 - ls * sign_x)
        dy = upem_scale * cy * (1.0 - ls * sign_y)

        pen = TTGlyphPen(None)
        # A single-axis flip makes det(transform) < 0 and reverses winding;
        # reverse contours so TrueType non-zero fill (holes) stays correct.
        dest = ReverseContourPen(pen) if (sx * sy) < 0 else pen
        tpen = TransformPen(dest, (sx, 0, 0, sy, dx, dy))
        try:
            rec.replay(tpen)
            glyph = pen.glyph()
        except Exception as e:
            print(
                f"  [!] replay failed {os.path.basename(self.path)}:{src_name}: {e}",
                file=sys.stderr,
            )
            return None

        if glyph.numberOfContours == 0 and not glyph.isComposite():
            return None

        # Some Ext-B sources ship inked glyphs with hmtx advance 0 and ink
        # centered on x=0 (large negative LSB). Pan-CJK cells are full-em.
        if advance <= 0:
            advance = target_upem

        if abs(self.weightor - 1.0) > 1e-9:
            try:
                glyph, advance, lsb = bolden_ttglyph(
                    glyph, self.weightor, advance=float(advance)
                )
                if advance <= 0:
                    advance = target_upem
            except Exception as e:
                print(
                    f"  [!] weightor failed {os.path.basename(self.path)}:{src_name}: {e}",
                    file=sys.stderr,
                )

        # Uniform shrink-to-fit + X-center; keep source optical Y (日/月 sit
        # lower than 木 — bbox-centering made short glyphs float).
        glyph, advance, lsb = fit_glyph_to_ideographic_cell(
            glyph,
            advance if advance > 0 else target_upem,
            target_upem,
            align_y="source",
        )
        return glyph, advance, lsb


def resolve_priority_fonts(in_dir: str) -> List[Tuple[str, float, float]]:
    """Return ``[(path, local_scale, weightor), ...]`` for fonts under ``in_dir``."""
    found: List[Tuple[str, float, float]] = []
    for name, scale, weightor in PRIORITY_FONTS:
        path = os.path.join(in_dir, name)
        if not os.path.isfile(path):
            print(f"[!] Missing priority font: {name}", file=sys.stderr)
            continue
        found.append((path, scale, weightor))
    return found


def resolve_priority_paths(in_dir: str) -> List[str]:
    return [path for path, _scale, _w in resolve_priority_fonts(in_dir)]


def claim_codepoints(sources: List[SourceFont], target: Set[int]) -> Dict[int, str]:
    """Map codepoint -> owning source path. Higher-priority fonts claim first."""
    owner: Dict[int, str] = {}
    for src in sources:
        base = os.path.basename(src.path)
        print(f"Scanning {base}...")
        claimed = 0
        for cp, gname in src.cmap.items():
            if cp not in target or cp in owner:
                continue
            if is_empty_glyph(src.tt, gname):
                continue
            owner[cp] = src.path
            claimed += 1
        print(f"  Claimed {claimed} new codepoints (total owned: {len(owner)})")
    return owner


def expand_entries(owner: Dict[int, str]) -> List[BucketEntry]:
    """One bucket entry per claimed Unicode code point."""
    return [(cp, path, cp) for cp, path in owner.items()]


def bucket_codepoints(entries: List[BucketEntry]) -> Dict[int, List[BucketEntry]]:
    """bucket_id -> sorted list of bucket entries."""
    buckets: Dict[int, List[BucketEntry]] = defaultdict(list)
    for entry in entries:
        buckets[entry[0] >> 8].append(entry)
    for bid in buckets:
        buckets[bid].sort(key=lambda t: t[0])
    return buckets


def vs_glyph_name(vs_cp: int) -> str:
    return f"vs{vs_cp - 0xE000 + 1:02d}"


# Cached Nuosu layout (advance, typo mid, max ink height) for Yi standalone scale.
_yi_layout_cache: Dict[str, Tuple[int, float, float]] = {}


def _yi_layout_for_source(path: str) -> Tuple[int, float, float]:
    cached = _yi_layout_cache.get(path)
    if cached is not None:
        return cached
    inv = load_inventory(path)
    layout = (inv.source_advance, inv.source_center_y, inv.source_max_height)
    _yi_layout_cache[path] = layout
    return layout


def build_bucket_font(
    bucket_id: int,
    entries: List[BucketEntry],
    sources: Dict[str, SourceFont],
    out_dir: str,
    target_upem: int,
    *,
    variant: str = "",
    write_ttf: bool = True,
    write_woff2: bool = True,
    hint: bool = True,
) -> Tuple[str, int, List[int]]:
    """Build one pigeonhole face for a bucket.

    ``variant``::

        ""   identity bases + ca/nhay (mark niche 1/4, base 3/4); no CJK D4
        "h"  bases + D4 + half-cell slice/overlay
        "t"  bases + D4 + third-cell niches (VS17–VS26 on standard CPs)
        "qv" bases + D4 + vertical quarter niches (VS13–14, VS27–33)
        "qh" bases + D4 + horizontal quarter niches (VS15–16, VS34–40)

    Returns (ttf_path, glyph_count, codepoints).
    """
    if variant not in CJK_FACE_VARIANTS:
        raise ValueError(f"variant must be one of {CJK_FACE_VARIANTS}, got {variant!r}")
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    bucket_hex = f"{bucket_id:X}"
    face_id = cjk_face_id(bucket_hex, variant)
    out_path = os.path.join(out_dir, f"{face_id}.ttf")

    glyph_order = [".notdef"]
    glyphs: Dict[str, TTGlyph] = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}
    uvs_rows: List[Tuple[int, int, Optional[str]]] = []
    base_names: List[str] = []
    with_d4 = variant in ("h", "t", "qv", "qh")

    for out_cp, path, src_cp in entries:
        src = sources[path]
        src_name = src.cmap.get(src_cp)
        if src_name is None:
            continue

        # Yi from NuosuSIL: shared sx (advance) + sy (max ink height).
        use_yi_standalone = os.path.basename(path) == NUOSU_FILENAME and is_yi_cp(
            src_cp
        )
        if use_yi_standalone:
            rec = record_glyph(src.tt, src_name)
            if rec is None:
                continue
            src_adv, src_cy, src_max_h = _yi_layout_for_source(path)
            copied = make_standalone_glyph(
                rec,
                target_upem,
                source_advance=src_adv,
                source_center_y=src_cy,
                source_max_height=src_max_h,
                widen=0.0,  # no CAPE Width; Weight bolden below if needed
            )
            if copied is None:
                continue
            g, adv, _lsb = copied
            if abs(src.local_scale - 1.0) > 1e-9:
                g = _scale_glyph_about_bounds_center(g, src.local_scale)
            if abs(src.weightor - 1.0) > 1e-9:
                g, adv, _lsb = bolden_ttglyph(
                    g, src.weightor, advance=float(adv)
                )
            try:
                g.recalcBounds(None)
                copied = (g, adv, int(g.xMin))
            except Exception:
                copied = (g, adv, _lsb)
        else:
            copied = src.copy_glyph(src_name, target_upem, flip_x=False, flip_y=False)
            if copied is None:
                continue

        glyph, advance, lsb = copied
        gname = glyph_name_for_cp(out_cp)
        glyph_order.append(gname)
        glyphs[gname] = glyph
        metrics[gname] = (advance, lsb)
        cmap[out_cp] = gname
        base_names.append(gname)

        if with_d4:
            add_d4_variant_glyphs(
                gname,
                advance=advance,
                lsb=lsb,
                target_upem=target_upem,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                anchor="cell",
            )

    if len(cmap) == 0:
        return out_path, 0, []

    # FE00..FE07 selectors (mark D4 on base face; CJK D4 on h/t faces).
    for mode_i, (vs_cp, _rot, _fx, _fy, _suffix) in enumerate(TRANSFORM_MODES):
        vname = vs_glyph_name(vs_cp)
        if vname not in glyphs:
            glyph_order.append(vname)
            glyphs[vname] = empty_glyph()
            metrics[vname] = (0, 0)
        cmap[uvs_selector_for_mode(mode_i)] = vname

    in_dir = os.path.dirname(next(iter(sources.keys()))) if sources else IN_DIR
    mark_scale = FONT_LOCAL_SCALE.get(PLANGOTHIC_P2_FILENAME, 0.96)
    _liga_unused: List[str] = []
    mark_state: Optional[Dict] = None
    squishable: List[str] = []
    mark_cps: List[int] = []
    third_forms: List[str] = []
    quarter_forms: List[str] = []
    quarter_face: Optional[str] = None

    match variant:
        case "":
            # Base face: ca/nhay with 1/4 mark niche (base occupies 3/4).
            mark_state = prepare_marks(
                in_dir=in_dir,
                cjk_bases=base_names,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                cmap=cmap,
                target_upem=target_upem,
                liga_rules=_liga_unused,
                uvs_rows=uvs_rows,
                local_scale=mark_scale,
                width_factor=MARK_BASE_SQUISH_FACTOR,
                height_factor=MARK_BASE_SQUISH_FACTOR,
                mark_niche_frac=MARK_NICHE_FRAC,
            )
            if mark_state is None:
                # No Plangothic — still emit FE0C–F niches at 3/4 for consistency.
                squishable = prepare_squish_vs_access(
                    cjk_bases=base_names,
                    glyph_order=glyph_order,
                    glyphs=glyphs,
                    metrics=metrics,
                    cmap=cmap,
                    target_upem=target_upem,
                    liga_rules=_liga_unused,
                    uvs_rows=uvs_rows,
                    width_factor=MARK_BASE_SQUISH_FACTOR,
                    height_factor=MARK_BASE_SQUISH_FACTOR,
                    slot_frac=MARK_BASE_SQUISH_FACTOR,
                    in_dir=in_dir,
                )
            else:
                squishable = mark_state["squishable"]
                mark_cps = list(mark_state.get("core_cps") or [])
        case "h":
            squishable = prepare_squish_vs_access(
                cjk_bases=base_names,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                cmap=cmap,
                target_upem=target_upem,
                liga_rules=_liga_unused,
                uvs_rows=uvs_rows,
                width_factor=SQUISH_FACTOR,
                height_factor=SQUISH_FACTOR,
                in_dir=in_dir,
            )
        case "t":
            third_forms = prepare_third_cells(
                cjk_bases=base_names,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                cmap=cmap,
                target_upem=target_upem,
            )
        case "qv" | "qh":
            quarter_face = variant
            quarter_forms = prepare_quarter_cells(
                face=variant,
                cjk_bases=base_names,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                cmap=cmap,
                target_upem=target_upem,
            )

    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)
    family = family_cjk(face_id)
    ps = ps_cjk(face_id)

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
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": ps,
            "fullName": family,
            "psName": ps,
            "version": "Version 1.000",
        }
    )
    fb.setupOS2(
        sTypoAscender=ascent,
        sTypoDescender=descent,
        sTypoLineGap=0,
        usWinAscent=ascent,
        usWinDescent=abs(descent),
        achVendID="pCJK",
    )
    fb.setupPost()

    match variant:
        case "" | "h":
            install_cjk_composition_gsub(
                fb.font,
                cjk_bases=base_names,
                glyphs=glyphs,
                glyph_order=glyph_order,
                squishable=squishable,
                mark_cps=mark_cps,
            )
        case _:
            # D4 only, then niche-face VS ligatures.
            install_cjk_composition_gsub(
                fb.font,
                cjk_bases=base_names,
                glyphs=glyphs,
                glyph_order=glyph_order,
                squishable=[],
                mark_cps=[],
            )
            match variant:
                case "t":
                    install_third_cell_gsub(
                        fb.font,
                        bases=third_forms,
                        glyphs=glyphs,
                    )
                case "qv" | "qh" if quarter_face is not None:
                    install_quarter_cell_gsub(
                        fb.font,
                        face=quarter_face,
                        bases=quarter_forms,
                        glyphs=glyphs,
                    )
    if mark_state is not None:
        compile_marks_layout(
            fb.font,
            mark_state,
            glyphs=glyphs,
            metrics=metrics,
            glyph_order=glyph_order,
            target_upem=target_upem,
        )

    fb.save(out_path)
    from shared_hinting import autohint_ttf

    autohint_ttf(out_path, enabled=hint)

    if write_woff2:
        compress_woff2(out_path)
    if not write_ttf:
        _drop_ttf(out_path)

    return out_path, len(glyphs) - 1, sorted(cmap.keys())


def build_bucket_faces(
    bucket_id: int,
    entries: List[BucketEntry],
    sources: Dict[str, SourceFont],
    out_dir: str,
    target_upem: int,
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
    hint: bool = True,
) -> List[Tuple[str, int, List[int]]]:
    """Build every face for one bucket sequentially (tests / single-bucket use)."""
    built: List[Tuple[str, int, List[int]]] = []
    for variant in CJK_FACE_VARIANTS:
        _path, count, codepoints = build_bucket_font(
            bucket_id,
            entries,
            sources,
            out_dir,
            target_upem,
            variant=variant,
            write_ttf=write_ttf,
            write_woff2=write_woff2,
            hint=hint,
        )
        if count == 0:
            continue
        face_id = cjk_face_id(f"{bucket_id:X}", variant)
        built.append((face_id, count, codepoints))
    return built


# ---------- Parallel workers ----------

_WORKER_SOURCES: Optional[Dict[str, SourceFont]] = None
_WORKER_OUT_DIR: Optional[str] = None
_WORKER_UPEM: Optional[int] = None
_WORKER_WRITE_TTF: bool = True
_WORKER_WRITE_WOFF2: bool = True
_WORKER_HINT: bool = True


def compress_woff2(ttf_path: str, woff2_path: Optional[str] = None) -> str:
    """Compress TTF→WOFF2 via a temp file (avoids Windows/OneDrive errno 22 races)."""
    if woff2_path is None:
        woff2_path = os.path.splitext(ttf_path)[0] + ".woff2"
    out_dir = os.path.dirname(os.path.abspath(woff2_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".woff2", dir=out_dir)
    os.close(fd)
    try:
        last_err: Optional[BaseException] = None
        for attempt in range(8):
            try:
                woff2.compress(ttf_path, tmp_path)
                os.replace(tmp_path, woff2_path)
                return woff2_path
            except OSError as exc:
                last_err = exc
                time.sleep(0.05 * (2 ** min(attempt, 6)))
        assert last_err is not None
        raise last_err
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _drop_ttf(ttf_path: str) -> None:
    try:
        os.remove(ttf_path)
    except OSError:
        pass


def _init_build_worker(
    font_entries: List[Tuple[str, float, float]],
    out_dir: str,
    target_upem: int,
    write_ttf: bool,
    write_woff2: bool,
    hint: bool = True,
) -> None:
    """Load source fonts once per process worker."""
    global _WORKER_SOURCES, _WORKER_OUT_DIR, _WORKER_UPEM
    global _WORKER_WRITE_TTF, _WORKER_WRITE_WOFF2, _WORKER_HINT
    _WORKER_OUT_DIR = out_dir
    _WORKER_UPEM = target_upem
    _WORKER_WRITE_TTF = write_ttf
    _WORKER_WRITE_WOFF2 = write_woff2
    _WORKER_HINT = hint
    _WORKER_SOURCES = {
        p: SourceFont(p, local_scale=s, weightor=w) for p, s, w in font_entries
    }


def _build_bucket_variant_task(
    args: Tuple[int, List[BucketEntry], str],
) -> Tuple[int, str, Optional[Tuple[str, int, List[int]]]]:
    """Build one ``(bucket, variant)`` face; returns ``None`` face when empty."""
    bucket_id, entries, variant = args
    assert _WORKER_SOURCES is not None
    assert _WORKER_OUT_DIR is not None
    assert _WORKER_UPEM is not None
    _path, count, codepoints = build_bucket_font(
        bucket_id,
        entries,
        _WORKER_SOURCES,
        _WORKER_OUT_DIR,
        _WORKER_UPEM,
        variant=variant,
        write_ttf=_WORKER_WRITE_TTF,
        write_woff2=_WORKER_WRITE_WOFF2,
        hint=_WORKER_HINT,
    )
    if count == 0:
        return bucket_id, variant, None
    face_id = cjk_face_id(f"{bucket_id:X}", variant)
    return bucket_id, variant, (face_id, count, codepoints)


def _face_sort_key(face_id: str) -> Tuple[int, int]:
    """Sort faces as bucket then qv / qh / t / h / base (CSS stack order)."""
    core, variant = split_cjk_face_id(face_id)
    order = {v: i for i, v in enumerate(CJK_FACE_CSS_ORDER)}
    try:
        return int(core, 16), order.get(variant, 9)
    except ValueError:
        return 0, 9


def parse_cjk_face_id(face_id: str) -> Optional[Tuple[int, str]]:
    """Parse ``4E`` / ``4Eh`` / ``4Eqv`` → ``(bucket_id, variant)``."""
    core, variant = split_cjk_face_id(face_id)
    if not core:
        return None
    try:
        return int(core, 16), variant
    except ValueError:
        return None


def unicode_range_for_bucket(
    bucket_id: int,
    codepoints: List[int],
    *,
    include_marks: bool = False,
    include_fe0: bool = True,
) -> str:
    """CSS ``unicode-range`` for one bucket face.

    Ideograph cps from the bucket, plus CJK VS when ``include_fe0`` (default):
    ``U+FE00–FE07`` (D4) and ``U+FE0B–FE0F`` (digraph niches). Blink drops
    unclaimed Default_Ignorables, so both sets must be listed. ``FE08–FE0A``
    stay out so Yi digraph/slice selectors are not claimed by CJK.

    Base faces may add U+16FF0/16FF1 (ca/nhay) via ``include_marks``.
    """
    side_sels = set(SIDE_SELECTOR_CPS)
    fe0_sels = set(range(0xFE00, 0xFE10))
    # D4 (FE00–FE07) + digraph niches (FE0B–FE0F); not FE08–FE0A (Yi).
    fe0_cjk = set(range(0xFE00, 0xFE08)) | set(range(0xFE0B, 0xFE10))
    bucket_cps = {
        cp
        for cp in codepoints
        if not (VS_BASE <= cp <= VS_LAST)
        and cp not in SQUISH_PUA_CPS
        and cp not in side_sels
        and cp not in fe0_sels
        and cp not in MARK_CPS
    }
    cps: set = set(bucket_cps)
    if include_marks:
        cps |= set(MARK_CPS)
    if include_fe0:
        cps |= fe0_cjk
    if not bucket_cps:
        start = bucket_id << 8
        cps |= set(range(start, start + 0x100))
        if include_marks:
            cps |= set(MARK_CPS)
        if include_fe0:
            cps |= fe0_cjk
    if not cps:
        start = bucket_id << 8
        cps = set(range(start, start + 0x100))
        if include_fe0:
            cps |= fe0_cjk

    ordered = sorted(cps)
    runs: List[str] = []
    run_start = ordered[0]
    prev = ordered[0]
    for cp in ordered[1:]:
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


def write_css(out_dir: str, built: List[Tuple[str, int, List[int]]]) -> None:
    """Write edenia-cjk.css (@font-face) and fontlist.css (CSS-safe stack).

    Each variant shares one family (``edenia cjk`` / ``… h`` / ``… t`` / …)
    with per-bucket ``unicode-range`` (ideographs + ``U+FE00–FE07,FE0B–FE0F``). Niche
    GSUB is selected with ``font-family: 'edenia cjk h'`` (etc.).
    """
    from edenia_names import family_cjk_variant

    css_path = os.path.join(out_dir, CSS_CJK)
    lines: List[str] = [
        "/* Auto-generated Edenia CJK pigeonhole @font-face rules */",
        "/* Shared families: 'edenia cjk' / h / t / qv / qh.",
        "   Per-file unicode-range = bucket ideographs + U+FE00-FE07, U+FE0B-FE0F.",
        "   Digraphs: font-family: 'edenia cjk h' — one run, cross-bucket OK. */",
        "",
    ]

    def _face(family: str, face_id: str, unicode_range: str) -> None:
        lines.append("@font-face {")
        lines.append(f"  font-family: '{family}';")
        lines.append(
            format_src_line(
                dist_rel(CJK_FOLDER, f"{face_id}.woff2"),
                fmt="woff2",
                local=(
                    (f"./{face_id}.woff2", "woff2"),
                    (f"./{face_id}.ttf", "truetype"),
                ),
                indent="  ",
            )
        )
        lines.append(f"  unicode-range: {unicode_range};")
        lines.append("  font-weight: normal;")
        lines.append("  font-style: normal;")
        lines.append("  font-display: swap;")
        lines.append("}")
        lines.append("")

    ordered = sorted(built, key=lambda t: _face_sort_key(t[0]))
    for face_id, _count, codepoints in ordered:
        parsed = parse_cjk_face_id(face_id)
        if parsed is None:
            continue
        bucket_id, variant = parsed
        family = family_cjk(face_id)
        ur = unicode_range_for_bucket(
            bucket_id,
            codepoints,
            include_marks=(variant == ""),
        )
        _face(family, face_id, ur)

    with open(css_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {css_path}")

    base_fam = family_cjk_variant("")
    fontlist_path = os.path.join(out_dir, "fontlist.css")
    fontlist = f"""/* src/scss/index.scss — Edenia CJK pigeonhole font stack */
/* Base family only. Digraphs/thirds/quarters: 'edenia cjk h' / t / qv / qh. */
body {{
  --font-editor-theme: '';
  --font-editor: var(--font-editor-theme), var(--font-text);
  --font-text-theme:
    Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, '{base_fam}', {STACK_CJK_TAIL}, monospace;
  --font-interface-theme:
    Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, '{base_fam}', {STACK_CJK_TAIL}, monospace;
  --font-monospace-theme:
    Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, '{base_fam}', {STACK_CJK_TAIL}, monospace;
}}
"""
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(fontlist)
    print(f"Wrote {fontlist_path}")


def regenerate_css_from_dist(out_dir: str) -> None:
    """Rewrite edenia-cjk.css / fontlist.css from existing ``*.woff2`` / ``*.ttf``."""
    seen: Dict[str, None] = {}
    built: List[Tuple[str, int, List[int]]] = []
    for name in sorted(os.listdir(out_dir)):
        if not (name.endswith(".woff2") or name.endswith(".ttf")):
            continue
        face_id = os.path.splitext(name)[0]
        if face_id in seen:
            continue
        parsed = parse_cjk_face_id(face_id)
        if parsed is None:
            continue
        bucket_id, variant = parsed
        if variant not in CJK_FACE_VARIANTS:
            continue
        seen[face_id] = None
        built.append((face_id, 0, []))
        _ = bucket_id
    if not built:
        print(f"No bucket fonts found under {out_dir}", file=sys.stderr)
        sys.exit(1)
    built.sort(key=lambda t: _face_sort_key(t[0]))
    print(f"Regenerating CSS for {len(built)} faces from {out_dir}")
    write_css(out_dir, built)
    sync_dist_to_plugin(CJK_FOLDER, out_dir)


def build_all(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    jobs: int,
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
    hint: bool = True,
) -> None:
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    font_entries = resolve_priority_fonts(in_dir)
    if not font_entries:
        print("No priority fonts found in", in_dir, file=sys.stderr)
        sys.exit(1)

    target = ranges_to_set(CHAR_RANGES)
    print(f"Target range size: {len(target)} codepoints")
    print(f"Source fonts: {len(font_entries)}")
    for path, scale, weightor in font_entries:
        notes: List[str] = []
        if abs(scale - 1.0) > 1e-9:
            notes.append(f"local_scale {scale:g}")
        if abs(weightor - 1.0) > 1e-9:
            notes.append(f"weightor {weightor:g}")
        if notes:
            print(f"  {', '.join(notes)}: {os.path.basename(path)}")
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"Output formats: {fmt_note}")
    print(
        "Faces/bucket: qv/qh, t, h, base (CSS); build waves base→h→t→qv→qh"
    )

    sources_list = [
        SourceFont(p, local_scale=s, weightor=w) for p, s, w in font_entries
    ]
    try:
        owner = claim_codepoints(sources_list, target)
    finally:
        for s in sources_list:
            s.close()

    if not owner:
        print("No codepoints claimed.", file=sys.stderr)
        sys.exit(1)

    per_source: Dict[str, int] = defaultdict(int)
    for path in owner.values():
        per_source[os.path.basename(path)] += 1
    print("\nClaimed per source:")
    for name in PRIORITY_FONT_NAMES:
        if name in per_source:
            print(f"  {name}: {per_source[name]}")

    all_entries = expand_entries(owner)
    print(f"\nGlyphs to build: {len(all_entries)}")

    buckets = bucket_codepoints(all_entries)
    os.makedirs(out_dir, exist_ok=True)

    used_paths = sorted(set(owner.values()))
    params_by_path = {p: (s, w) for p, s, w in font_entries}
    used_entries = [(p, *params_by_path.get(p, (1.0, 1.0))) for p in used_paths]
    workers = max(1, jobs)
    bucket_ids = sorted(buckets.keys())
    n_buckets = len(bucket_ids)
    n_variants = len(CJK_FACE_VARIANTS)
    print(
        f"\nBuilding {n_variants} variants × {n_buckets} buckets "
        f"(one variant at a time; {workers} workers) -> {out_dir}",
        flush=True,
    )

    written = 0
    glyph_total = 0
    skipped = 0
    built: List[Tuple[str, int, List[int]]] = []
    fmt_tag = (
        "ttf+woff2" if write_ttf and write_woff2 else ("ttf" if write_ttf else "woff2")
    )

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_build_worker,
        initargs=(used_entries, out_dir, target_upem, write_ttf, write_woff2, hint),
    ) as executor:
        for vi, variant in enumerate(CJK_FACE_VARIANTS, start=1):
            label = variant if variant else "base"
            print(
                f"\n── variant {vi}/{n_variants}: {label} "
                f"({n_buckets} buckets) ──",
                flush=True,
            )
            futures = [
                executor.submit(
                    _build_bucket_variant_task,
                    (bid, buckets[bid], variant),
                )
                for bid in bucket_ids
            ]
            done = 0
            for fut in concurrent.futures.as_completed(futures):
                bucket_id, _var, face = fut.result()
                done += 1
                hex_id = f"{bucket_id:X}"
                if face is None:
                    skipped += 1
                    print(
                        f"  [{done}/{n_buckets}] {hex_id}{variant} skipped (empty)",
                        flush=True,
                    )
                    continue
                face_id, count, codepoints = face
                written += 1
                glyph_total += count
                built.append((face_id, count, codepoints))
                print(
                    f"  [{done}/{n_buckets}] {face_id} ({fmt_tag}; {count})",
                    flush=True,
                )

    built.sort(key=lambda t: _face_sort_key(t[0]))
    write_css(out_dir, built)

    print(
        f"\nDone: {written} fonts, {glyph_total} glyphs, "
        f"{skipped} empty skipped, UPM={target_upem}, jobs={workers}",
        flush=True,
    )
    sync_dist_to_plugin(CJK_FOLDER, out_dir)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build Pan-CJK pigeonhole fonts (build_cjk)"
    )
    p.add_argument("--in", dest="in_dir", default=IN_DIR, help="Input fonts directory")
    p.add_argument("--out", dest="out_dir", default=OUT_DIR, help="Output directory")
    p.add_argument(
        "--upem",
        dest="upem",
        type=int,
        default=DEFAULT_UPEM,
        help=f"Target unitsPerEm (default {DEFAULT_UPEM})",
    )
    p.add_argument(
        "--jobs",
        "-j",
        dest="jobs",
        type=int,
        default=max(1, os.cpu_count() or 4),
        help="Parallel workers per variant wave (default: CPU count)",
    )
    p.add_argument(
        "--css-only",
        action="store_true",
        help="Only regenerate edenia-cjk.css / fontlist.css from existing fonts",
    )
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument(
        "--ttf-only",
        action="store_true",
        help="Write TTF only (skip WOFF2)",
    )
    fmt.add_argument(
        "--woff2-only",
        action="store_true",
        help="Write WOFF2 only (drop intermediate TTF after compress)",
    )
    p.add_argument(
        "--no-hint",
        action="store_true",
        help="Skip ttfautohint-py TrueType autohint step",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.css_only:
        regenerate_css_from_dist(args.out_dir)
    else:
        build_all(
            args.in_dir,
            args.out_dir,
            args.upem,
            args.jobs,
            write_ttf=not args.woff2_only,
            write_woff2=not args.ttf_only,
            hint=not args.no_hint,
        )
