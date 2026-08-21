#!/usr/bin/env python3
"""
Build Pan-CJK pigeonhole subfonts.

Claims CJK/Tangut codepoints from priority-ordered source fonts, buckets them
into 256-codepoint blocks (cp >> 8), and builds each TTF/WOFF2 from scratch by
copying (decomposed, scaled) glyphs one-by-one into a fresh FontBuilder font.

Six faces per bucket (filename / family stem = ``{hex}`` / ``{hex}h`` /
``{hex}t`` / ``{hex}q`` / ``{hex}qv`` / ``{hex}qh``)::

    (none)  base forms + ca/nhay (all mark orientations); mark niche = 1/4
                (base occupies 3/4)
    h       base forms + D4 + half-cell slices (FE00 overlay, FE08–FE0F)
    t       base forms + D4 + third-cell niches (VS17–VS26; FE00 zero-width)
    q       2×2 corners + L 3/4 (VS41–48), derived from ``h`` halves
    qv      vertical quarter niches (VS13–14, VS27–33), derived from ``h``
    qh      horizontal quarter niches (VS15–16, VS34–40), derived from ``h``

Every bucket is a process-pool job (``--jobs`` defaults to all CPUs). Build runs
in four parallel stages across all workers: master glyf cache, face TTFs,
autohint, WOFF2. D4 niche copies are transforms of identity clips, not
re-slices.

Also writes edenia-cjk.css (@font-face) and fontlist.css (CSS-safe stack).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import os
import pickle
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
    SQUISH_VS_SLOTS,
    compile_marks_layout,
    install_cjk_composition_gsub,
    prepare_marks,
    prepare_squish_vs_access,
    squishable_forms,
)
from shared_half_cells import (
    NUOSU_FILENAME,
    OV_SELECTOR_CP,
    OV_SELECTOR_NAME,
    TRANSFORM_MODES,
    UVS_BASE,
    UVS_LAST,
    VS_BASE,
    VS_LAST,
    add_d4_variant_glyphs,
    fit_glyph_to_ideographic_cell,
    grow_undersize_to_average_ideo,
    is_yi_cp,
    load_inventory,
    make_standalone_glyph,
    normalize_axes_to_average_ideo,
    record_glyph,
    uvs_selector_for_mode,
    variant_glyph_name,
    vs_glyph_name,
)
from shared_third_cells import (
    THIRD_VS_SLOTS,
    install_third_cell_gsub,
    prepare_third_cells,
)
from shared_quarter_cells import (
    GRID_VS_SLOTS,
    QUARTER_FACE_GRID,
    QUARTER_FACE_H,
    QUARTER_FACE_V,
    QUARTER_FACES,
    quarter_form_name,
    quarter_slot_parts,
    quarter_slots_for_face,
    install_quarter_cell_gsub,
    prepare_quarter_cells,
)
from edenia_names import (
    CJK_FACE_BUILD_ORDER,
    CJK_FACE_CSS_ORDER,
    CJK_FACE_VARIANTS,
    CSS_CJK,
    STACK_CJK_TAIL,
    add_cjk_variant_arguments,
    cjk_face_id,
    family_cjk,
    ordered_cjk_variants,
    ps_cjk,
    resolve_cjk_variants,
    split_cjk_face_id,
)
from sync_edenian_fonts import sync_dist_to_plugin
from cdn_fonts import dist_rel, format_src_line
from shared_hinting import add_hint_mode_arguments, _parse_jobs

# ---------- Directories ----------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
CJK_FOLDER = "cjk"
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", CJK_FOLDER)

DEFAULT_UPEM = 1000
# Parallel glyph copies during master build (pathops releases the GIL).
IMPORT_THREADS = min(32, max(4, (os.cpu_count() or 4)))

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
# After import: undersized glyphs grow uniformly to this average (geometric
# scale → thicker strokes). Overflow is geometric shrink into the ideo box
# (→ thinner). New Gulim + Microsoft YaHei then stretch/squash X and Y
# independently so each glyph's ink W and H match this mean (still geometric;
# no CAPE stem hold), except CJK radical blocks and few-stroke glyphs (either
# axis ≪ mean) which keep uniform grow only. Other sources keep uniform grow.
AVERAGE_IDEO_INK = 874.0  # square target width/height @ 1000 UPM
PRIORITY_FONTS: List[Tuple[str, float, float]] = [
    ("NGULIM.ttf", 1.25, 1.2),
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
# Independent X/Y normalize to AVERAGE_IDEO_INK (stretch and squash).
# If either ink axis is below this fraction of the mean, skip independent
# stretch entirely (uniform grow only). Also always skipped for radical CPs.
AXIS_NORMALIZE_FONTS = frozenset({"ngulim.ttf", "msyh.ttc"})
AXIS_NORMALIZE_SPARSE_FRAC = 0.75


def is_cjk_radical_cp(cp: int) -> bool:
    """Kangxi / CJK radical blocks — keep designed proportions, no axis stretch."""
    return (0x2E80 <= cp <= 0x2EFF) or (0x2F00 <= cp <= 0x2FDF)

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


_RANGE_PAIR = re.compile(
    r"(?:U\+)?([0-9A-Fa-f]+)\s*(?:-|\.\.|–|—)\s*(?:U\+)?([0-9A-Fa-f]+)",
    re.I,
)
_RANGE_ONE = re.compile(r"(?:U\+)?([0-9A-Fa-f]+)$", re.I)


def parse_unicode_range_spec(spec: str) -> List[Tuple[int, int]]:
    """``U+2F00-9FFF`` / ``4E00-4FFF`` / ``4E`` (bucket) / ``U+4E00`` (one CP)."""
    raw = spec.strip()
    if not raw:
        raise argparse.ArgumentTypeError("empty --range")
    out: List[Tuple[int, int]] = []
    for part in re.split(r"\s*,\s*", raw):
        part = part.strip()
        if not part:
            continue
        compact = part.replace(" ", "")
        m = _RANGE_PAIR.fullmatch(compact) or _RANGE_PAIR.fullmatch(part)
        if m:
            a, b = int(m.group(1), 16), int(m.group(2), 16)
            if a > b:
                a, b = b, a
            out.append((a, b))
            continue
        m = _RANGE_ONE.fullmatch(compact)
        if m:
            token = m.group(1)
            n = int(token, 16)
            if len(token) <= 3:
                start = n << 8
                out.append((start, start + 0xFF))
            else:
                out.append((n, n))
            continue
        raise argparse.ArgumentTypeError(
            f"bad --range {part!r}; try U+2F00-9FFF, 4E00-4FFF, or 4E"
        )
    if not out:
        raise argparse.ArgumentTypeError(f"bad --range {spec!r}")
    return out


def codepoints_from_range_specs(
    spans: Sequence[Sequence[Tuple[int, int]]],
) -> Set[int]:
    cps: Set[int] = set()
    for spec in spans:
        for start, end in spec:
            cps.update(range(start, end + 1))
    return cps


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
        self.axis_normalize = os.path.basename(path).casefold() in AXIS_NORMALIZE_FONTS
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
        *,
        codepoint: Optional[int] = None,
    ) -> Optional[Tuple[TTGlyph, int, int]]:
        """Decompose + UPM scale + optional local scale / weightor / mirrors.

        ``local_scale`` (per source font) scales outlines about the contour
        bounding-box center; advance width stays the UPM-scaled source advance.
        ``weightor`` then boldens/lightens via CAPE Weightor Weight mode only
        (bounds preserved). Width-mode CAPE is not used for CJK niches.
        Mirrors also flip about that same contour center.
        New Gulim / Microsoft YaHei then stretch or squash X and Y independently
        so ink width and height match the average ideograph — except radical
        CPs and few-stroke glyphs (either axis sparse), which use uniform grow
        only. Overflow then shrinks geometrically into the padded cell.
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

        cell_adv = advance if advance > 0 else target_upem
        avg = AVERAGE_IDEO_INK * (float(target_upem) / float(DEFAULT_UPEM))
        do_axis = self.axis_normalize and not (
            codepoint is not None and is_cjk_radical_cp(codepoint)
        )
        if do_axis:
            # BBox under ~75% of mean on either axis → uniform grow only.
            try:
                glyph.recalcBounds(None)
                ink_w = float(glyph.xMax) - float(glyph.xMin)
                ink_h = float(glyph.yMax) - float(glyph.yMin)
            except Exception:
                ink_w = ink_h = 0.0
            sparse = AXIS_NORMALIZE_SPARSE_FRAC
            if ink_w < avg * sparse or ink_h < avg * sparse:
                do_axis = False
        if do_axis:
            glyph, advance, lsb = normalize_axes_to_average_ideo(
                glyph,
                cell_adv,
                target_upem,
                avg_width=avg,
                avg_height=avg,
                sparse_frac=AXIS_NORMALIZE_SPARSE_FRAC,
            )
            cell_adv = advance if advance > 0 else target_upem
        glyph, advance, lsb = grow_undersize_to_average_ideo(
            glyph,
            cell_adv,
            target_upem,
            avg_width=avg,
            avg_height=avg,
            align_y="source",
        )
        cell_adv = advance if advance > 0 else target_upem
        glyph, advance, lsb = fit_glyph_to_ideographic_cell(
            glyph,
            cell_adv,
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


def _import_one_bucket_entry(
    entry: BucketEntry,
    sources: Dict[str, SourceFont],
    target_upem: int,
    *,
    with_d4: bool,
) -> Optional[
    Tuple[
        int,
        str,
        List[str],
        Dict[str, TTGlyph],
        Dict[str, Tuple[int, int]],
    ]
]:
    """Copy one claimed codepoint (+ optional D4) into local glyf tables."""
    out_cp, path, src_cp = entry
    src = sources[path]
    src_name = src.cmap.get(src_cp)
    if src_name is None:
        return None

    use_yi_standalone = os.path.basename(path) == NUOSU_FILENAME and is_yi_cp(src_cp)
    if use_yi_standalone:
        rec = record_glyph(src.tt, src_name)
        if rec is None:
            return None
        src_adv, src_cy, src_max_h = _yi_layout_for_source(path)
        copied = make_standalone_glyph(
            rec,
            target_upem,
            source_advance=src_adv,
            source_center_y=src_cy,
            source_max_height=src_max_h,
            widen=0.0,
        )
        if copied is None:
            return None
        g, adv, _lsb = copied
        if abs(src.local_scale - 1.0) > 1e-9:
            g = _scale_glyph_about_bounds_center(g, src.local_scale)
        if abs(src.weightor - 1.0) > 1e-9:
            g, adv, _lsb = bolden_ttglyph(g, src.weightor, advance=float(adv))
        try:
            g.recalcBounds(None)
            copied = (g, adv, int(g.xMin))
        except Exception:
            copied = (g, adv, _lsb)
    else:
        copied = src.copy_glyph(
            src_name,
            target_upem,
            flip_x=False,
            flip_y=False,
            codepoint=src_cp,
        )
        if copied is None:
            return None

    glyph, advance, lsb = copied
    gname = glyph_name_for_cp(out_cp)
    local_order = [gname]
    local_glyphs: Dict[str, TTGlyph] = {gname: glyph}
    local_metrics: Dict[str, Tuple[int, int]] = {gname: (advance, lsb)}
    if with_d4:
        add_d4_variant_glyphs(
            gname,
            advance=advance,
            lsb=lsb,
            target_upem=target_upem,
            glyph_order=local_order,
            glyphs=local_glyphs,
            metrics=local_metrics,
            anchor="cell",
        )
    return out_cp, gname, local_order, local_glyphs, local_metrics


def _import_bucket_glyphs(
    entries: List[BucketEntry],
    sources: Dict[str, SourceFont],
    target_upem: int,
    *,
    with_d4: bool,
    timings: Optional[Dict[str, float]] = None,
) -> Tuple[
    List[str],
    Dict[str, TTGlyph],
    Dict[str, Tuple[int, int]],
    Dict[int, str],
    List[str],
]:
    """Copy claimed sources into a fresh glyf once (optional CJK D4)."""
    glyph_order = [".notdef"]
    glyphs: Dict[str, TTGlyph] = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}
    base_names: List[str] = []
    t0 = time.perf_counter()

    chunks: List[
        Tuple[
            int,
            str,
            List[str],
            Dict[str, TTGlyph],
            Dict[str, Tuple[int, int]],
        ]
    ] = []
    workers = min(IMPORT_THREADS, len(entries)) if len(entries) > 1 else 1
    if workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for chunk in pool.map(
                lambda e: _import_one_bucket_entry(
                    e, sources, target_upem, with_d4=with_d4
                ),
                entries,
            ):
                if chunk is not None:
                    chunks.append(chunk)
        chunks.sort(key=lambda c: c[0])
    else:
        for entry in entries:
            chunk = _import_one_bucket_entry(
                entry, sources, target_upem, with_d4=with_d4
            )
            if chunk is not None:
                chunks.append(chunk)

    t_import = time.perf_counter() - t0
    for out_cp, gname, local_order, local_glyphs, local_metrics in chunks:
        for name in local_order:
            if name not in glyphs:
                glyph_order.append(name)
            glyphs[name] = local_glyphs[name]
            metrics[name] = local_metrics[name]
        cmap[out_cp] = gname
        if gname not in base_names:
            base_names.append(gname)

    if timings is not None:
        timings["import"] = t_import
        timings["d4"] = 0.0

    for mode_i, (vs_cp, _rot, _fx, _fy, _suffix) in enumerate(TRANSFORM_MODES):
        vname = vs_glyph_name(vs_cp)
        if vname not in glyphs:
            glyph_order.append(vname)
            glyphs[vname] = empty_glyph()
            metrics[vname] = (0, 0)
        uvs = uvs_selector_for_mode(mode_i)
        if uvs is not None:
            cmap[uvs] = vname
        cmap[vs_cp] = vname
    if OV_SELECTOR_NAME not in glyphs:
        glyph_order.append(OV_SELECTOR_NAME)
        glyphs[OV_SELECTOR_NAME] = empty_glyph()
        metrics[OV_SELECTOR_NAME] = (0, 0)
    cmap[OV_SELECTOR_CP] = OV_SELECTOR_NAME

    return glyph_order, glyphs, metrics, cmap, base_names


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
        "q"  2×2 / L niches (VS41–48); multi-face path subsets the master
        "qv" vertical quarter niches; multi-face path subsets the master
        "qh" horizontal quarter niches; multi-face path subsets the master

    ``_build_all_bucket_faces`` copies sources **once** (with D4 + halves) and
    subsets base / t / q / qv / qh from that seed. This function is the
    single-variant fallback and still walks sources for the requested face.

    Returns (ttf_path, glyph_count, codepoints).
    """
    if variant not in CJK_FACE_VARIANTS:
        raise ValueError(f"variant must be one of {CJK_FACE_VARIANTS}, got {variant!r}")
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    bucket_hex = f"{bucket_id:X}"
    face_id = cjk_face_id(bucket_hex, variant)
    out_path = os.path.join(out_dir, f"{face_id}.ttf")

    with_d4 = variant in ("h", "t", "q", "qv", "qh")
    glyph_order, glyphs, metrics, cmap, base_names = _import_bucket_glyphs(
        entries, sources, target_upem, with_d4=with_d4
    )
    uvs_rows: List[Tuple[int, int, Optional[str]]] = []

    if len(cmap) == 0 or not base_names:
        return out_path, 0, []

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
                # No Plangothic — still emit FE08–F slice niches at 3/4.
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
        case "q":
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
        case "q":
            install_cjk_composition_gsub(
                fb.font,
                cjk_bases=base_names,
                glyphs=glyphs,
                glyph_order=glyph_order,
                squishable=squishable,
                mark_cps=[],
            )
            install_quarter_cell_gsub(
                fb.font,
                face=QUARTER_FACE_GRID,
                bases=quarter_forms,
                glyphs=glyphs,
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


def unpack_built_ttf(
    ttf_path: str,
) -> Tuple[
    TTFont,
    List[str],
    Dict[str, TTGlyph],
    Dict[str, Tuple[int, int]],
    Dict[int, str],
    int,
]:
    """Load glyph order / glyf / hmtx / cmap / upem from a finished TTF.

    The returned ``TTFont`` must stay open until the glyph objects have been
    copied into a new ``FontBuilder``.
    """
    tt = TTFont(ttf_path)
    glyph_order = list(tt.getGlyphOrder())
    glyf = tt["glyf"]
    glyphs: Dict[str, TTGlyph] = {}
    for name in glyph_order:
        g = glyf[name]
        if g.isComposite():
            _ = g.components
        elif getattr(g, "numberOfContours", 0):
            _ = g.coordinates
        glyphs[name] = g
    metrics = {n: (int(tt["hmtx"][n][0]), int(tt["hmtx"][n][1])) for n in glyph_order}
    cmap: Dict[int, str] = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    upem = int(tt["head"].unitsPerEm)
    return tt, glyph_order, glyphs, metrics, cmap, upem


def _identity_cjk_bases(
    cmap: Dict[int, str],
    glyphs: Dict[str, TTGlyph],
) -> List[str]:
    """Cmap names with no ``.``, not selectors / ``.notdef``."""
    names: List[str] = []
    seen: set = set()
    for _cp, name in sorted(cmap.items()):
        if name in seen or name not in glyphs:
            continue
        if name == ".notdef" or name.startswith("vs") or "." in name:
            continue
        seen.add(name)
        names.append(name)
    return names


def derive_face_from_half(
    bucket_id: int,
    variant: str,
    half_ttf: str,
    out_dir: str,
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
    hint: bool = True,
) -> Tuple[str, int, List[int]]:
    """Slice ``q`` / ``qv`` / ``qh`` from a completed half-cell TTF.

    ``qv`` splits top/bottom halves; ``qh`` splits left/right; ``q`` uses all
    appends VS41–48; ``qv``/``qh`` rebuild D4 + their own quarter ligas
    (``qv`` FE08–9 / ``qh`` FE0A–B overlap half-cell CPs on ``h``).
    """
    if variant not in QUARTER_FACES:
        raise ValueError(
            f"derived face must be one of {QUARTER_FACES}, got {variant!r}"
        )
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    bucket_hex = f"{bucket_id:X}"
    face_id = cjk_face_id(bucket_hex, variant)
    out_path = os.path.join(out_dir, f"{face_id}.ttf")
    if not os.path.isfile(half_ttf):
        return out_path, 0, []

    src_tt, glyph_order, glyphs, metrics, cmap, target_upem = unpack_built_ttf(half_ttf)
    try:
        if len(cmap) == 0:
            return out_path, 0, []
        base_names = _identity_cjk_bases(cmap, glyphs)
        if not base_names:
            return out_path, 0, []

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

        if variant == QUARTER_FACE_GRID:
            install_cjk_composition_gsub(
                fb.font,
                cjk_bases=base_names,
                glyphs=glyphs,
                glyph_order=glyph_order,
                squishable=squishable_forms(base_names),
                mark_cps=[],
            )
        else:
            install_cjk_composition_gsub(
                fb.font,
                cjk_bases=base_names,
                glyphs=glyphs,
                glyph_order=glyph_order,
                squishable=[],
                mark_cps=[],
            )
        install_quarter_cell_gsub(
            fb.font,
            face=variant,
            bases=quarter_forms,
            glyphs=glyphs,
        )

        fb.save(out_path)
    finally:
        src_tt.close()

    from shared_hinting import autohint_ttf

    autohint_ttf(out_path, enabled=hint)
    if write_woff2:
        compress_woff2(out_path)
    if not write_ttf:
        _drop_ttf(out_path)
    return out_path, len(glyphs) - 1, sorted(cmap.keys())


_D4_SUFFIXES: Tuple[str, ...] = tuple(
    suf for _vs, _r, _fx, _fy, suf in TRANSFORM_MODES if suf is not None
)
_HALF_SUFFIXES: Tuple[str, ...] = tuple(suf for _cp, _sel, suf in SQUISH_VS_SLOTS)
_THIRD_SUFFIXES: Tuple[str, ...] = tuple(
    suf for _cp, _sel, suf, _a, _b0, _b1 in THIRD_VS_SLOTS
)


def _oriented_forms(bases: Sequence[str], glyphs: Dict[str, TTGlyph]) -> List[str]:
    forms: List[str] = []
    seen: set = set()
    for base in bases:
        if base not in glyphs or base in seen:
            continue
        forms.append(base)
        seen.add(base)
        for suf in _D4_SUFFIXES:
            name = variant_glyph_name(base, suf)
            if name in glyphs and name not in seen:
                forms.append(name)
                seen.add(name)
    return forms


def _close_component_names(keep: set, glyphs: Dict[str, TTGlyph]) -> set:
    stack = list(keep)
    out = set(keep)
    while stack:
        name = stack.pop()
        glyph = glyphs.get(name)
        if glyph is None:
            continue
        try:
            if not glyph.isComposite():
                continue
            comps = glyph.components
        except Exception:
            continue
        for comp in comps:
            child = getattr(comp, "glyphName", None)
            if child and child not in out:
                out.add(child)
                stack.append(child)
    return out


def _add_stem_family(keep: set, stem: str, glyphs: Dict[str, TTGlyph]) -> None:
    if stem in glyphs:
        keep.add(stem)
    ov = f"{stem}.ov"
    if ov in glyphs:
        keep.add(ov)


def _keep_names_for_face(
    variant: str,
    bases: Sequence[str],
    glyphs: Dict[str, TTGlyph],
) -> set:
    keep: set = {".notdef"}
    for name in glyphs:
        if name.startswith("vs"):
            keep.add(name)
    for base in bases:
        _add_stem_family(keep, base, glyphs)
        if not variant:
            continue
        oriented = [base] + [
            variant_glyph_name(base, suf)
            for suf in _D4_SUFFIXES
            if variant_glyph_name(base, suf) in glyphs
        ]
        for stem in oriented:
            _add_stem_family(keep, stem, glyphs)
            if variant in ("h", "q"):
                for hs in _HALF_SUFFIXES:
                    _add_stem_family(keep, f"{stem}.{hs}", glyphs)
            if variant == "t":
                for ts in _THIRD_SUFFIXES:
                    _add_stem_family(keep, f"{stem}.{ts}", glyphs)
            if variant == "q":
                for gs in (s[2] for s in GRID_VS_SLOTS):
                    _add_stem_family(keep, quarter_form_name(stem, gs), glyphs)
            if variant in ("qv", "qh"):
                for slot in quarter_slots_for_face(variant):
                    suf = quarter_slot_parts(slot)[2]
                    _add_stem_family(
                        keep,
                        quarter_form_name(stem, suf, face=variant),
                        glyphs,
                    )
    return _close_component_names(keep, glyphs)


def _subset_master_tables(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    keep: set,
    *,
    copy_glyphs: bool = True,
) -> Tuple[List[str], Dict[str, TTGlyph], Dict[str, Tuple[int, int]], Dict[int, str]]:
    keep = _close_component_names(keep, glyphs)
    order = [n for n in glyph_order if n in keep]
    for name in keep:
        if name not in order and name in glyphs:
            order.append(name)
    if copy_glyphs:
        out_glyphs = {n: copy.deepcopy(glyphs[n]) for n in order if n in glyphs}
    else:
        out_glyphs = {n: glyphs[n] for n in order if n in glyphs}
    out_metrics = {n: metrics[n] for n in order if n in metrics}
    out_cmap = {cp: name for cp, name in cmap.items() if name in out_glyphs}
    return order, out_glyphs, out_metrics, out_cmap


def _filter_face_cmap(
    variant: str, cmap: Dict[int, str], bases: Sequence[str]
) -> Dict[int, str]:
    """Drop other faces' VS pages from a shared master cmap."""
    base_set = set(bases)
    vs_page = {
        "t": set(range(0xE0100, 0xE010A)),
        "qv": set(range(0xE010A, 0xE0111)),
        "qh": set(range(0xE0111, 0xE0118)),
        "q": set(range(0xE0118, 0xE0120)),
    }.get(variant, set())
    fe_ok = {OV_SELECTOR_CP, 0xE008}
    if variant in ("h", "t", "q", "qv", "qh"):
        fe_ok |= set(range(0xFE01, 0xFE08)) | set(range(VS_BASE, VS_LAST + 1))
    if variant in ("h", "q"):
        fe_ok |= set(range(0xFE08, 0xFE10)) | set(range(0xE009, 0xE011))
    elif variant == "qv":
        fe_ok |= {0xFE08, 0xFE09}
    elif variant == "qh":
        fe_ok |= {0xFE0A, 0xFE0B}
    if variant == "":
        fe_ok |= set(range(0xFE00, 0xFE10)) | set(range(0xE008, 0xE018))
        fe_ok |= set(MARK_CPS)
    out: Dict[int, str] = {}
    for cp, name in cmap.items():
        if name in base_set or cp in fe_ok or cp in vs_page:
            out[cp] = name
    return out


def _build_tables_font(
    *,
    face_id: str,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int,
) -> TTFont:
    """In-memory TTF (no GSUB yet) so callers can install layout before first save."""
    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)
    family = family_cjk(face_id)
    ps = ps_cjk(face_id)
    fb = FontBuilder(target_upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
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
    return fb.font


def _install_face_gsub(
    font,
    *,
    variant: str,
    base_names: Sequence[str],
    glyphs: Dict[str, TTGlyph],
    glyph_order: List[str],
    squishable: Sequence[str],
    mark_cps: Sequence[int],
    third_forms: Sequence[str],
    quarter_forms: Sequence[str],
    quarter_face: Optional[str],
) -> None:
    match variant:
        case "" | "h":
            install_cjk_composition_gsub(
                font,
                cjk_bases=base_names,
                glyphs=glyphs,
                glyph_order=glyph_order,
                squishable=squishable,
                mark_cps=list(mark_cps),
            )
        case "q":
            install_cjk_composition_gsub(
                font,
                cjk_bases=base_names,
                glyphs=glyphs,
                glyph_order=glyph_order,
                squishable=squishable,
                mark_cps=[],
            )
            install_quarter_cell_gsub(
                font,
                face=QUARTER_FACE_GRID,
                bases=quarter_forms,
                glyphs=glyphs,
            )
        case _:
            install_cjk_composition_gsub(
                font,
                cjk_bases=base_names,
                glyphs=glyphs,
                glyph_order=glyph_order,
                squishable=[],
                mark_cps=[],
            )
            match variant:
                case "t":
                    install_third_cell_gsub(font, bases=third_forms, glyphs=glyphs)
                case "qv" | "qh" if quarter_face is not None:
                    install_quarter_cell_gsub(
                        font,
                        face=quarter_face,
                        bases=quarter_forms,
                        glyphs=glyphs,
                    )


def _build_bucket_master_state(
    entries: List[BucketEntry],
    sources: Dict[str, SourceFont],
    target_upem: int,
    *,
    variants: Sequence[str] = CJK_FACE_BUILD_ORDER,
    timings: Optional[Dict[str, float]] = None,
) -> Optional[Dict]:
    """Import + niche clips for one bucket; return pickle-able master glyf state."""
    want = set(variants)
    with_d4 = bool(want - {""})
    glyph_order, glyphs, metrics, cmap, base_names = _import_bucket_glyphs(
        entries, sources, target_upem, with_d4=with_d4, timings=timings
    )
    if not base_names:
        return None

    in_dir = os.path.dirname(next(iter(sources.keys()))) if sources else IN_DIR
    need_halves = bool(want & {"h", "q"})
    if need_halves:
        t0 = time.perf_counter()
        prepare_squish_vs_access(
            cjk_bases=base_names,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
            target_upem=target_upem,
            liga_rules=[],
            uvs_rows=[],
            width_factor=SQUISH_FACTOR,
            height_factor=SQUISH_FACTOR,
            in_dir=in_dir,
        )
        if timings is not None:
            timings["halves"] = time.perf_counter() - t0
    if "t" in want:
        t0 = time.perf_counter()
        prepare_third_cells(
            cjk_bases=base_names,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
            target_upem=target_upem,
        )
        if timings is not None:
            timings["thirds"] = time.perf_counter() - t0
    for qface, qkey in (
        (QUARTER_FACE_GRID, "q"),
        (QUARTER_FACE_V, "qv"),
        (QUARTER_FACE_H, "qh"),
    ):
        if qkey not in want:
            continue
        t0 = time.perf_counter()
        prepare_quarter_cells(
            face=qface,
            cjk_bases=base_names,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
            target_upem=target_upem,
        )
        if timings is not None:
            timings[qkey] = time.perf_counter() - t0
    return {
        "glyph_order": glyph_order,
        "glyphs": glyphs,
        "metrics": metrics,
        "cmap": cmap,
        "target_upem": target_upem,
        "in_dir": in_dir,
    }


def _build_face_ttf_from_state(
    bucket_id: int,
    variant: str,
    state: Dict,
    out_dir: str,
) -> Tuple[Optional[Tuple[str, int, List[int]]], Optional[str]]:
    """Subset master glyf, install GSUB, write one face TTF."""
    bucket_hex = f"{bucket_id:X}"
    glyph_order: List[str] = state["glyph_order"]
    glyphs: Dict[str, TTGlyph] = state["glyphs"]
    metrics: Dict[str, Tuple[int, int]] = state["metrics"]
    cmap: Dict[int, str] = state["cmap"]
    target_upem: int = state["target_upem"]
    in_dir: str = state["in_dir"]

    base_names = _identity_cjk_bases(cmap, glyphs)
    if not base_names:
        return None, None
    oriented = _oriented_forms(base_names, glyphs)
    mark_scale = FONT_LOCAL_SCALE.get(PLANGOTHIC_P2_FILENAME, 0.96)

    face_id = cjk_face_id(bucket_hex, variant)
    out_path = os.path.join(out_dir, f"{face_id}.ttf")
    keep = _keep_names_for_face(variant, base_names, glyphs)
    go, gl, mt, cm = _subset_master_tables(
        glyph_order,
        glyphs,
        metrics,
        cmap,
        keep,
        copy_glyphs=(variant == ""),
    )
    cm = _filter_face_cmap(variant, cm, base_names)
    mark_state: Optional[Dict] = None
    mark_cps: List[int] = []
    squishable: List[str] = []
    third_forms: List[str] = []
    quarter_forms: List[str] = []
    quarter_face: Optional[str] = None
    _liga: List[str] = []
    _uvs: List = []

    if variant == "":
        mark_state = prepare_marks(
            in_dir=in_dir,
            cjk_bases=base_names,
            glyph_order=go,
            glyphs=gl,
            metrics=mt,
            cmap=cm,
            target_upem=target_upem,
            liga_rules=_liga,
            uvs_rows=_uvs,
            local_scale=mark_scale,
            width_factor=MARK_BASE_SQUISH_FACTOR,
            height_factor=MARK_BASE_SQUISH_FACTOR,
            mark_niche_frac=MARK_NICHE_FRAC,
        )
        if mark_state is None:
            squishable = prepare_squish_vs_access(
                cjk_bases=base_names,
                glyph_order=go,
                glyphs=gl,
                metrics=mt,
                cmap=cm,
                target_upem=target_upem,
                liga_rules=_liga,
                uvs_rows=_uvs,
                width_factor=MARK_BASE_SQUISH_FACTOR,
                height_factor=MARK_BASE_SQUISH_FACTOR,
                slot_frac=MARK_BASE_SQUISH_FACTOR,
                in_dir=in_dir,
            )
        else:
            squishable = mark_state["squishable"]
            mark_cps = list(mark_state.get("core_cps") or [])
    elif variant in ("h", "q"):
        squishable = squishable_forms(base_names)
    if variant == "t":
        third_forms = oriented
    if variant in ("q", "qv", "qh"):
        quarter_face = variant
        quarter_forms = oriented

    fb_font = _build_tables_font(
        face_id=face_id,
        glyph_order=go,
        glyphs=gl,
        metrics=mt,
        cmap=cm,
        target_upem=target_upem,
    )
    try:
        _install_face_gsub(
            fb_font,
            variant=variant,
            base_names=base_names,
            glyphs=gl,
            glyph_order=go,
            squishable=squishable,
            mark_cps=mark_cps,
            third_forms=third_forms,
            quarter_forms=quarter_forms,
            quarter_face=quarter_face,
        )
        if mark_state is not None:
            compile_marks_layout(
                fb_font,
                mark_state,
                glyphs=gl,
                metrics=mt,
                glyph_order=go,
                target_upem=target_upem,
            )
        fb_font.save(out_path)
    finally:
        fb_font.close()
    return (face_id, len(gl) - 1, sorted(cm.keys())), out_path


def _master_cache_path(cache_dir: str, bucket_id: int) -> str:
    return os.path.join(cache_dir, f"{bucket_id:X}.pkl")


def _acquire_master_lock(cache_path: str) -> str:
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
    raise TimeoutError(f"timed out waiting for master lock: {cache_path}")


def _release_master_lock(lock_path: str) -> None:
    if lock_path:
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _print_bucket_profile(bucket_hex: str, times: Dict[str, float]) -> None:
    keys = (
        "import",
        "d4",
        "halves",
        "thirds",
        "q",
        "qv",
        "qh",
        "faces",
        "hint",
        "woff",
        "total",
    )
    parts = " ".join(f"{k}={times[k]:.2f}" for k in keys if k in times)
    print(f"  {bucket_hex} profile: {parts}", flush=True)


def _build_all_bucket_faces(
    bucket_id: int,
    entries: List[BucketEntry],
    sources: Dict[str, SourceFont],
    out_dir: str,
    target_upem: int,
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
    hint: bool = True,
    hint_base_only: bool = False,
    variants: Sequence[str] = CJK_FACE_BUILD_ORDER,
) -> List[Tuple[str, Optional[Tuple[str, int, List[int]]]]]:
    """Build selected faces for one bucket (tests / single-bucket use)."""
    from shared_hinting import autohint_ttf

    variants = ordered_cjk_variants(variants)
    bucket_hex = f"{bucket_id:X}"
    os.makedirs(out_dir, exist_ok=True)
    empty_row = [(v, None) for v in variants]
    times: Dict[str, float] = {}
    t_all = time.perf_counter()
    state = _build_bucket_master_state(
        entries, sources, target_upem, variants=variants, timings=times
    )
    if state is None:
        return empty_row

    rows: List[Tuple[str, Optional[Tuple[str, int, List[int]]]]] = []
    ttf_paths: List[str] = []
    hint_paths: List[str] = []
    t0 = time.perf_counter()
    for variant in variants:
        face, out_path = _build_face_ttf_from_state(bucket_id, variant, state, out_dir)
        rows.append((variant, face))
        if out_path is not None:
            ttf_paths.append(out_path)
            if hint and (not hint_base_only or variant == ""):
                hint_paths.append(out_path)
    times["faces"] = time.perf_counter() - t0

    workers = max(1, min(len(variants), len(hint_paths) or len(ttf_paths)))
    try:
        t0 = time.perf_counter()
        if hint_paths:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(lambda p: autohint_ttf(p, enabled=True), hint_paths))
        times["hint"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        if write_woff2 and ttf_paths:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(compress_woff2, ttf_paths))
        times["woff"] = time.perf_counter() - t0
    finally:
        if not write_ttf:
            for path in ttf_paths:
                _drop_ttf(path)

    times["total"] = time.perf_counter() - t_all
    _print_bucket_profile(bucket_hex, times)
    return rows


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
    hint_base_only: bool = False,
    variants: Sequence[str] = CJK_FACE_BUILD_ORDER,
) -> List[Tuple[str, int, List[int]]]:
    """Build selected faces for one bucket sequentially (tests / single-bucket use)."""
    built: List[Tuple[str, int, List[int]]] = []
    for _variant, face in _build_all_bucket_faces(
        bucket_id,
        entries,
        sources,
        out_dir,
        target_upem,
        write_ttf=write_ttf,
        write_woff2=write_woff2,
        hint=hint,
        hint_base_only=hint_base_only,
        variants=variants,
    ):
        if face is not None:
            built.append(face)
    return built


# ---------- Parallel workers ----------

_WORKER_SOURCES: Optional[Dict[str, SourceFont]] = None
_WORKER_OUT_DIR: Optional[str] = None
_WORKER_CACHE_DIR: Optional[str] = None
_WORKER_UPEM: Optional[int] = None
_WORKER_WRITE_TTF: bool = True
_WORKER_WRITE_WOFF2: bool = True
_WORKER_HINT: bool = True
_WORKER_HINT_BASE_ONLY: bool = False
_WORKER_VARIANTS: Tuple[str, ...] = CJK_FACE_BUILD_ORDER


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
    cache_dir: str,
    target_upem: int,
    write_ttf: bool,
    write_woff2: bool,
    hint: bool = True,
    variants: Tuple[str, ...] = CJK_FACE_BUILD_ORDER,
    hint_base_only: bool = False,
) -> None:
    """Load source fonts once per process worker."""
    global _WORKER_SOURCES, _WORKER_OUT_DIR, _WORKER_CACHE_DIR, _WORKER_UPEM
    global _WORKER_WRITE_TTF, _WORKER_WRITE_WOFF2, _WORKER_HINT, _WORKER_VARIANTS
    global _WORKER_HINT_BASE_ONLY
    _WORKER_OUT_DIR = out_dir
    _WORKER_CACHE_DIR = cache_dir
    _WORKER_UPEM = target_upem
    _WORKER_WRITE_TTF = write_ttf
    _WORKER_WRITE_WOFF2 = write_woff2
    _WORKER_HINT = hint
    _WORKER_HINT_BASE_ONLY = hint_base_only
    _WORKER_VARIANTS = tuple(variants)
    _WORKER_SOURCES = {
        p: SourceFont(p, local_scale=s, weightor=w) for p, s, w in font_entries
    }


def _master_cache_task(
    args: Tuple[int, List[BucketEntry]],
) -> Tuple[int, bool]:
    """Build and pickle master glyf for one bucket (deduped via file lock)."""
    bucket_id, entries = args
    assert _WORKER_SOURCES is not None
    assert _WORKER_CACHE_DIR is not None
    assert _WORKER_UPEM is not None
    cache_path = _master_cache_path(_WORKER_CACHE_DIR, bucket_id)
    if os.path.isfile(cache_path):
        return bucket_id, True
    lock = _acquire_master_lock(cache_path)
    try:
        if os.path.isfile(cache_path):
            return bucket_id, True
        state = _build_bucket_master_state(
            entries,
            _WORKER_SOURCES,
            _WORKER_UPEM,
            variants=_WORKER_VARIANTS,
        )
        if state is None:
            return bucket_id, False
        with open(cache_path, "wb") as f:
            pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
        return bucket_id, True
    finally:
        _release_master_lock(lock)


def _face_ttf_task(
    args: Tuple[int, str],
) -> Tuple[int, str, Optional[Tuple[str, int, List[int]]], Optional[str]]:
    """Subset cached master glyf and write one face TTF."""
    bucket_id, variant = args
    assert _WORKER_OUT_DIR is not None
    assert _WORKER_CACHE_DIR is not None
    cache_path = _master_cache_path(_WORKER_CACHE_DIR, bucket_id)
    if not os.path.isfile(cache_path):
        return bucket_id, variant, None, None
    with open(cache_path, "rb") as f:
        state = pickle.load(f)
    face, out_path = _build_face_ttf_from_state(
        bucket_id, variant, state, _WORKER_OUT_DIR
    )
    return bucket_id, variant, face, out_path


def _hint_ttf_task(ttf_path: str) -> str:
    from shared_hinting import autohint_ttf

    autohint_ttf(ttf_path, enabled=True)
    return ttf_path


def _woff2_face_task(ttf_path: str) -> str:
    compress_woff2(ttf_path)
    if not _WORKER_WRITE_TTF:
        _drop_ttf(ttf_path)
    return ttf_path


def _build_bucket_variant(
    bucket_id: int,
    entries: List[BucketEntry],
    variant: str,
) -> Tuple[int, str, Optional[Tuple[str, int, List[int]]]]:
    """Build one face; returns ``None`` face when empty."""
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
        hint=_WORKER_HINT and (not _WORKER_HINT_BASE_ONLY or variant == ""),
    )
    if count == 0:
        return bucket_id, variant, None
    face_id = cjk_face_id(f"{bucket_id:X}", variant)
    return bucket_id, variant, (face_id, count, codepoints)


def _face_sort_key(face_id: str) -> Tuple[int, int]:
    """Sort faces as bucket then q / qv / qh / t / h / base (CSS stack order)."""
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


# Extra VS pages claimed per niche face (not in the bucket ideograph block).
# ``t`` VS17–26, ``qv`` VS27–33, ``qh`` VS34–40, ``q`` VS41–48.
_CJK_FACE_VS_EXTRA: Dict[str, range] = {
    "t": range(0xE0100, 0xE010A),
    "qv": range(0xE010A, 0xE0111),
    "qh": range(0xE0111, 0xE0118),
    "q": range(0xE0118, 0xE0120),
}


def unicode_range_for_bucket(
    bucket_id: int,
    codepoints: List[int],
    *,
    include_marks: bool = False,
    include_fe0: bool = True,
    variant: str = "",
) -> str:
    """CSS ``unicode-range`` for one bucket face.

    Ideograph cps from the bucket, plus CJK VS when ``include_fe0`` (default):
    ``U+FE00–FE0F`` (base: ca/nhay slots; ``h``: overlay + D4 + slices).
    Blink drops unclaimed Default_Ignorables, so the sets must be listed.

    Niche faces also list their VS17+ page (``t`` E0100–E0109, ``qv``
    E010A–E0110, ``qh`` E0111–E0117, ``q`` E0118–E011F) even when
    ``codepoints`` is empty (``--css-only``).

    Base faces may add U+16FF0/16FF1 (ca/nhay) via ``include_marks``.
    """
    side_sels = set(SIDE_SELECTOR_CPS)
    fe0_sels = set(range(0xFE00, 0xFE10))
    # Overlay (FE00) + D4 (FE01–FE07) + slices (FE08–FE0F).
    fe0_cjk = set(range(0xFE00, 0xFE10))
    vs17_page = set(range(0xE0100, 0xE01F0))
    bucket_cps = {
        cp
        for cp in codepoints
        if not (VS_BASE <= cp <= VS_LAST)
        and cp not in SQUISH_PUA_CPS
        and cp not in side_sels
        and cp not in fe0_sels
        and cp not in MARK_CPS
        and cp not in vs17_page
    }
    cps: set = set(bucket_cps)
    if include_marks:
        cps |= set(MARK_CPS)
    if include_fe0:
        cps |= fe0_cjk
    extra_vs = _CJK_FACE_VS_EXTRA.get(variant)
    if extra_vs is not None:
        cps |= set(extra_vs)
    if not bucket_cps:
        start = bucket_id << 8
        cps |= set(range(start, start + 0x100))
        if include_marks:
            cps |= set(MARK_CPS)
        if include_fe0:
            cps |= fe0_cjk
        if extra_vs is not None:
            cps |= set(extra_vs)
    if not cps:
        start = bucket_id << 8
        cps = set(range(start, start + 0x100))
        if include_fe0:
            cps |= fe0_cjk
        if extra_vs is not None:
            cps |= set(extra_vs)

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
    with per-bucket ``unicode-range`` (ideographs + ``U+FE00–FE0F``). Niche
    GSUB is selected with ``font-family: 'edenia cjk h'`` (etc.).
    """
    from edenia_names import family_cjk_variant

    css_path = os.path.join(out_dir, CSS_CJK)
    lines: List[str] = [
        "/* Auto-generated Edenia CJK pigeonhole @font-face rules */",
        "/* Shared families: 'edenia cjk' / q / qv / qh / t / h.",
        "   Per-file unicode-range = bucket ideographs + U+FE00-FE0F",
        "   plus that face's VS17+ page. Digraphs: 'edenia cjk h'. */",
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
            variant=variant,
        )
        _face(family, face_id, ur)

    with open(css_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {css_path}")

    built_vars = [
        parsed[1]
        for fid, *_ in ordered
        if (parsed := parse_cjk_face_id(fid)) is not None
    ]
    stack_variant = ""
    if built_vars and "" not in built_vars:
        stack_variant = next(
            (v for v in CJK_FACE_CSS_ORDER if v in built_vars), built_vars[0]
        )
    base_fam = family_cjk_variant(stack_variant)
    fontlist_path = os.path.join(out_dir, "fontlist.css")
    fontlist = f"""/* src/scss/index.scss — Edenia CJK pigeonhole font stack */
/* Base family only. Digraphs/thirds/quarters: 'edenia cjk h' / t / q / qv / qh. */
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


def regenerate_css_from_dist(
    out_dir: str,
    *,
    variants: Optional[Sequence[str]] = None,
) -> None:
    """Rewrite edenia-cjk.css / fontlist.css from existing ``*.woff2`` / ``*.ttf``."""
    want = set(ordered_cjk_variants(variants)) if variants is not None else None
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
        if want is not None and variant not in want:
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
    hint_base_only: bool = False,
    variants: Sequence[str] = CJK_FACE_BUILD_ORDER,
    cp_filter: Optional[Set[int]] = None,
) -> None:
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    variants = ordered_cjk_variants(variants)
    labels = ["base" if v == "" else v for v in variants]
    font_entries = resolve_priority_fonts(in_dir)
    if not font_entries:
        print("No priority fonts found in", in_dir, file=sys.stderr)
        sys.exit(1)

    target = ranges_to_set(CHAR_RANGES)
    if cp_filter is not None:
        before = len(target)
        target &= cp_filter
        print(
            f"Target range size: {len(target)} codepoints "
            f"(--range; {before} inventory, {len(cp_filter)} requested)",
            flush=True,
        )
        if not target:
            print("No inventory codepoints in --range.", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Target range size: {len(target)} codepoints")
    print(f"Source fonts: {len(font_entries)}")
    for path, scale, weightor in font_entries:
        notes: List[str] = []
        if abs(scale - 1.0) > 1e-9:
            notes.append(f"local_scale {scale:g}")
        if abs(weightor - 1.0) > 1e-9:
            notes.append(f"weightor {weightor:g}")
        if os.path.basename(path).casefold() in AXIS_NORMALIZE_FONTS:
            notes.append(
                f"axis-normalize mean W×H "
                f"(skip radicals / sparse <{AXIS_NORMALIZE_SPARSE_FRAC:g}×)"
            )
        if notes:
            print(f"  {', '.join(notes)}: {os.path.basename(path)}")
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"Output formats: {fmt_note}")
    hint_note = "none" if not hint else ("base only" if hint_base_only else "all faces")
    print(f"Hinting: {hint_note}")
    print(
        "Faces: "
        + ", ".join(labels)
        + "; four stages: master glyf → TTF → hint → WOFF2 (all parallel)"
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
    workers = max(1, abs(jobs))
    bucket_ids = sorted(buckets.keys())
    n_buckets = len(bucket_ids)
    n_variants = len(variants)
    n_faces = n_buckets * n_variants
    if cp_filter is not None:
        print(
            "  --range buckets: " + ", ".join(f"{bid:X}" for bid in bucket_ids),
            flush=True,
        )
    print(
        f"\nBuilding {n_faces} faces "
        f"({n_variants} variants × {n_buckets} buckets, {workers} workers, "
        f"4 parallel stages) -> {out_dir}",
        flush=True,
    )

    written = 0
    glyph_total = 0
    skipped = 0
    built: List[Tuple[str, int, List[int]]] = []
    fmt_tag = (
        "ttf+woff2" if write_ttf and write_woff2 else ("ttf" if write_ttf else "woff2")
    )

    cache_dir = tempfile.mkdtemp(prefix="edenia_cjk_master_", dir=out_dir)
    try:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_build_worker,
            initargs=(
                used_entries,
                out_dir,
                cache_dir,
                target_upem,
                write_ttf,
                write_woff2,
                hint,
                variants,
                hint_base_only,
            ),
        ) as executor:
            master_jobs = [(bid, buckets[bid]) for bid in bucket_ids]
            t0 = time.perf_counter()
            print(
                f"\nStage 1/4: master glyf ({len(master_jobs)} buckets)...",
                flush=True,
            )
            list(executor.map(_master_cache_task, master_jobs))
            print(
                f"  stage 1 done in {time.perf_counter() - t0:.1f}s",
                flush=True,
            )

            face_jobs = [(bid, variant) for bid in bucket_ids for variant in variants]
            t0 = time.perf_counter()
            print(
                f"Stage 2/4: face TTFs ({len(face_jobs)} jobs)...",
                flush=True,
            )
            face_results = list(executor.map(_face_ttf_task, face_jobs))
            ttf_paths: List[str] = []
            hint_paths: List[str] = []
            done_faces = 0
            for bucket_id, variant, face, out_path in face_results:
                done_faces += 1
                hex_id = f"{bucket_id:X}"
                if face is None:
                    skipped += 1
                    print(
                        f"  [{done_faces}/{n_faces}] "
                        f"{hex_id}{variant} skipped (empty)",
                        flush=True,
                    )
                    continue
                face_id, count, codepoints = face
                written += 1
                glyph_total += count
                built.append((face_id, count, codepoints))
                if out_path is not None:
                    ttf_paths.append(out_path)
                    if hint and (not hint_base_only or variant == ""):
                        hint_paths.append(out_path)
                print(
                    f"  [{done_faces}/{n_faces}] {face_id} ({fmt_tag}; {count})",
                    flush=True,
                )
            print(
                f"  stage 2 done in {time.perf_counter() - t0:.1f}s",
                flush=True,
            )

            if hint_paths:
                t0 = time.perf_counter()
                print(
                    f"Stage 3/4: hint ({len(hint_paths)} TTFs"
                    + (f"; base only of {len(ttf_paths)}" if hint_base_only else "")
                    + ")...",
                    flush=True,
                )
                list(executor.map(_hint_ttf_task, hint_paths))
                print(
                    f"  stage 3 done in {time.perf_counter() - t0:.1f}s",
                    flush=True,
                )

            if write_woff2 and ttf_paths:
                t0 = time.perf_counter()
                print(
                    f"Stage 4/4: WOFF2 ({len(ttf_paths)} TTFs)...",
                    flush=True,
                )
                list(executor.map(_woff2_face_task, ttf_paths))
                print(
                    f"  stage 4 done in {time.perf_counter() - t0:.1f}s",
                    flush=True,
                )
            elif not write_ttf:
                for path in ttf_paths:
                    _drop_ttf(path)
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)

    built.sort(key=lambda t: _face_sort_key(t[0]))
    if cp_filter is not None:
        print(
            "  --range: merging CSS from all faces in dist " "(other buckets kept)",
            flush=True,
        )
        regenerate_css_from_dist(out_dir, variants=None)
    else:
        write_css(out_dir, built)
        sync_dist_to_plugin(CJK_FOLDER, out_dir)

    print(
        f"\nDone: {written} fonts, {glyph_total} glyphs, "
        f"{skipped} empty skipped, UPM={target_upem}, jobs={workers}",
        flush=True,
    )


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
        type=_parse_jobs,
        default=max(1, os.cpu_count() or 4),
        help=(
            "Parallel workers per stage (default: all CPUs); 4 stages: "
            "master TTF, hint, WOFF2. ``-j -61`` is the same as ``-j 61``."
        ),
    )
    p.add_argument(
        "--css-only",
        action="store_true",
        help="Only regenerate edenia-cjk.css / fontlist.css from existing fonts",
    )
    p.add_argument(
        "--range",
        dest="ranges",
        action="append",
        type=parse_unicode_range_spec,
        metavar="SPAN",
        help=(
            "Rebuild only these Unicode spans (repeatable, comma-ok). "
            "Examples: U+2F00-9FFF, 4E00-4FFF, 4E (one 256-CP bucket). "
            "Intersected with the CJK inventory; CSS is merged from dist."
        ),
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
    add_hint_mode_arguments(p)
    add_cjk_variant_arguments(p)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        variants = resolve_cjk_variants(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    if args.css_only:
        regenerate_css_from_dist(
            args.out_dir,
            variants=None if variants == CJK_FACE_BUILD_ORDER else variants,
        )
    else:
        build_all(
            args.in_dir,
            args.out_dir,
            args.upem,
            args.jobs,
            write_ttf=not args.woff2_only,
            write_woff2=not args.ttf_only,
            hint=not args.no_hint,
            hint_base_only=bool(args.hint_base_only),
            variants=variants,
            cp_filter=(
                codepoints_from_range_specs(args.ranges) if args.ranges else None
            ),
        )
