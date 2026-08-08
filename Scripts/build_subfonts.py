#!/usr/bin/env python3
"""
Build Pan-CJK pigeonhole subfonts.

Claims CJK/Tangut codepoints from priority-ordered source fonts, buckets them
into 256-codepoint blocks (cp >> 8), and builds each TTF/WOFF2 from scratch by
copying (decomposed, scaled) glyphs one-by-one into a fresh FontBuilder font.

D4 variants for bucket fonts are emitted **in the same TTF**:
transformed outlines (2×2 rotates baked; axis mirrors as composites) plus
GSUB ``ccmp``/``rlig``/``liga`` for ``unicode + VS01..VS08``
(U+E000..U+E007 / UVS U+FE00..FE07) — the 8 unique square symmetries.
``U+FE08`` overlays the preceding pair (all but the last glyph become
zero-advance ``.ov`` forms; chain with more FE08). No Supplementary PUA
marker, no cmap offsets. GlyphWiki content uses SPUA+BMP-PUA ligatures
(see ``kage.mapping``).

Also writes pancjk.css (@font-face) and fontlist.css (CSS-safe stack).
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

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
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
from yi_halfwidth import (
    NUOSU_FILENAME,
    STACK_MARK_CP,
    TRANSFORM_MODES,
    UVS_BASE,
    UVS_LAST,
    VS_BASE,
    VS_LAST,
    add_d4_variant_glyphs,
    add_overlay_forms,
    build_d4_uvs_entries,
    center_glyph_in_cell,
    composition_fea,
    inject_stack_mark,
    install_overlay_gsub,
    is_yi_cp,
    load_inventory,
    make_standalone_glyph,
    orientation_form_names,
    record_glyph,
    vs_glyph_name,
)

# ---------- Directories ----------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "subfonts")

DEFAULT_UPEM = 1000

CSS_FAMILY = "pancjk"
CSS_FONT_URL_BASE = (
    "https://raw.githubusercontent.com/nexovolta/fonts/main/Scripts/dist/subfonts"
)

# ---------- Source priority (highest first) ----------
# Each entry: (filename, local_scale, weightor)
# * local_scale — isotropic scale about contour bbox center after UPM fit
#   (advance width unchanged).
# * weightor — CAPE Weightor Weight-mode factor after fit (>1 bolden, <1 lighten,
#   1.0 = none). Outer width/height are preserved.

PRIORITY_FONTS: List[Tuple[str, float, float]] = [
    ("YuGothM.ttc", 1.0, 1.3),
    ("msjh.ttc", 1.0, 1.3),
    ("NGULIM.TTF", 1.0, 1.15),
    ("Han-Nom Gothic 1.32.otf", 0.95, 1.0),
    ("msyh.ttc", 0.95, 0.95),
    ("LXGWClearGothic-Regular.ttf", 1.01, 0.975),
    ("LXGWXiHeiMN.ttf", 1.01, 0.975),
    ("LXGWXiHeiCL.ttf", 1.01, 0.975),
    ("LXGWNeoXiHeiPlus.ttf", 1.01, 0.975),
    ("ChironHeiHK-R.ttf", 0.96, 0.95),
    ("Gothic Nguyen Regular.ttf", 0.96, 0.95),
    ("YshiYuanGothicCleaned.ttf", 0.96, 0.95),
    ("ChocolateClassicalSans-Regular.ttf", 0.96, 0.95),
    ("SukimaGothic.ttf", 0.96, 1.0),
    ("NotoSerifTangut-Regular.ttf", 1.0, 1.05),
    ("PlangothicP1-Regular.ttf", 0.96, 0.95),
    ("PlangothicP2-Regular.ttf", 0.96, 0.95),
]

PRIORITY_FONT_NAMES: List[str] = [name for name, _scale, _w in PRIORITY_FONTS]
FONT_LOCAL_SCALE: Dict[str, float] = {name: scale for name, scale, _w in PRIORITY_FONTS}
FONT_WEIGHTOR: Dict[str, float] = {name: w for name, _scale, w in PRIORITY_FONTS}

# ---------- Unicode ranges (inclusive) ----------

CHAR_RANGES: List[Tuple[int, int, str]] = [
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
        ``weightor`` then boldens/lightens via CAPE Weightor (bounds preserved).
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
        # centered on x=0 (large negative LSB). Pan-CJK cells are full-em;
        # force the advance and recenter ink in the typo square.
        force_cell = advance <= 0
        if force_cell:
            advance = target_upem

        if abs(self.weightor - 1.0) > 1e-9:
            try:
                glyph, advance, lsb = bolden_ttglyph(
                    glyph, self.weightor, advance=float(advance)
                )
                if advance <= 0:
                    advance = target_upem
                    force_cell = True
            except Exception as e:
                print(
                    f"  [!] weightor failed {os.path.basename(self.path)}:{src_name}: {e}",
                    file=sys.stderr,
                )

        if force_cell:
            glyph = center_glyph_in_cell(glyph, target_upem)

        try:
            glyph.recalcBounds(None)
            lsb = int(glyph.xMin)
        except Exception:
            lsb = otRound(lsb_src * upem_scale)
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
    write_ttf: bool = True,
    write_woff2: bool = True,
) -> Tuple[str, int, List[int]]:
    """Build one pigeonhole font with in-font D4 variant ligatures.

    Returns (ttf_path, glyph_count, codepoints) where codepoints are the
    Unicode cmap keys (bases + VS01..VS08 + FE08 when present).
    """
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    hex_id = f"{bucket_id:X}"
    out_path = os.path.join(out_dir, f"{hex_id}.ttf")

    glyph_order = [".notdef"]
    glyphs: Dict[str, TTGlyph] = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}
    liga_rules: List[str] = []
    uvs_rows: List[Tuple[int, int, Optional[str]]] = []
    base_names: List[str] = []

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
            )
            if copied is None:
                continue
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

        installed = add_d4_variant_glyphs(
            gname,
            advance=advance,
            lsb=lsb,
            target_upem=target_upem,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
        )
        for vs_cp, _suffix, m_name in installed:
            liga_rules.append(f"  sub {gname} {vs_glyph_name(vs_cp)} by {m_name};")
        uvs_rows.extend(build_d4_uvs_entries(out_cp, gname, glyphs=glyphs))

    if len(cmap) == 0:
        return out_path, 0, []

    # Inject VS marks so D4 ligatures stay in-font (VS01..VS08 / FE00..FE07)
    for vs_cp, _rot, _fx, _fy, _suffix in TRANSFORM_MODES:
        vname = vs_glyph_name(vs_cp)
        if vname not in glyphs:
            glyph_order.append(vname)
            glyphs[vname] = empty_glyph()
            metrics[vname] = (0, 0)
        cmap[vs_cp] = vname
    inject_stack_mark(glyph_order, glyphs, metrics, cmap)

    # FE08 overlay forms (identity + all D4 variants)
    form_names: List[str] = []
    for base in base_names:
        form_names.extend(orientation_form_names(base))
    add_overlay_forms(
        form_names, glyph_order=glyph_order, glyphs=glyphs, metrics=metrics
    )

    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)
    family = f"pancjk {hex_id}"
    ps = f"pancjk-{hex_id}"

    fb = FontBuilder(target_upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    fb.setupCharacterMap(cmap, uvs=uvs_rows)
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

    if liga_rules:
        fea = composition_fea(liga_rules)
        if fea:
            addOpenTypeFeaturesFromString(fb.font, fea)
    install_overlay_gsub(fb.font, form_names, glyphs=glyphs, glyph_order=glyph_order)

    fb.save(out_path)

    if write_woff2:
        compress_woff2(out_path)
    if not write_ttf:
        _drop_ttf(out_path)

    return out_path, len(glyphs) - 1, sorted(cmap.keys())


# ---------- Parallel workers ----------

_WORKER_SOURCES: Optional[Dict[str, SourceFont]] = None
_WORKER_OUT_DIR: Optional[str] = None
_WORKER_UPEM: Optional[int] = None


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
        for attempt in range(5):
            try:
                woff2.compress(ttf_path, tmp_path)
                os.replace(tmp_path, woff2_path)
                return woff2_path
            except OSError as exc:
                last_err = exc
                time.sleep(0.05 * (2**attempt))
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


def _compress_woff2_task(ttf_path: str) -> None:
    compress_woff2(ttf_path)


def _init_build_worker(
    font_entries: List[Tuple[str, float, float]],
    out_dir: str,
    target_upem: int,
) -> None:
    """Load source fonts once per process worker."""
    global _WORKER_SOURCES, _WORKER_OUT_DIR, _WORKER_UPEM
    _WORKER_OUT_DIR = out_dir
    _WORKER_UPEM = target_upem
    _WORKER_SOURCES = {
        p: SourceFont(p, local_scale=s, weightor=w) for p, s, w in font_entries
    }


def _build_bucket_task(
    args: Tuple[int, List[BucketEntry]],
) -> Tuple[int, str, int, List[int]]:
    bucket_id, entries = args
    assert _WORKER_SOURCES is not None
    assert _WORKER_OUT_DIR is not None
    assert _WORKER_UPEM is not None
    # Always keep the TTF during the worker pass; WOFF2 / TTF retention
    # is handled after all workers finish (see build_all).
    path, count, codepoints = build_bucket_font(
        bucket_id,
        entries,
        _WORKER_SOURCES,
        _WORKER_OUT_DIR,
        _WORKER_UPEM,
        write_ttf=True,
        write_woff2=False,
    )
    return bucket_id, path, count, codepoints


def unicode_range_for_bucket(bucket_id: int, codepoints: List[int]) -> str:
    """CSS unicode-range for this bucket's CJK + UVS FE00..FE07 + FE08 overlay.

    PUA U+E000..E007 is intentionally *not* listed: in a multi-face stack every
    bucket used to advertise those codepoints, so the first face stole all VS
    and broke ``base+VS`` ligatures. Prefer cmap format-14 UVS (U+FE00..) which
    stays on the base character's face; keep PUA liga for single-family use
    (VS still in the font cmap). FE08 overlay must be listed so the stack mark
    loads from this face.
    """
    bucket_cps = {
        cp
        for cp in codepoints
        if not (VS_BASE <= cp <= VS_LAST) and cp != STACK_MARK_CP
    }
    cps = sorted(bucket_cps | set(range(UVS_BASE, UVS_LAST + 1)) | {STACK_MARK_CP})
    if not bucket_cps:
        start = bucket_id << 8
        end = start + 0xFF
        return f"U+{start:X}-{end:X}, U+{UVS_BASE:X}-{STACK_MARK_CP:X}"

    runs: List[str] = []
    run_start = cps[0]
    prev = cps[0]
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


def write_css(out_dir: str, built: List[Tuple[str, int, List[int]]]) -> None:
    """Write pancjk.css (@font-face) and fontlist.css (CSS-safe stack)."""
    css_path = os.path.join(out_dir, "pancjk.css")
    lines: List[str] = [
        "/* Auto-generated Pan-CJK pigeonhole @font-face rules */",
        "/* Local src first; GitHub raw as fallback. PUA VS omitted from",
        "   unicode-range so the multi-face stack does not steal UVS. */",
        "",
    ]
    family_names: List[str] = []
    for hex_id, _count, codepoints in built:
        bucket_id = int(hex_id, 16)
        family = f"pancjk {hex_id}"
        family_names.append(family)
        urange = unicode_range_for_bucket(bucket_id, codepoints)
        lines.append("@font-face {")
        lines.append(f"  font-family: '{family}';")
        lines.append(f"  src: url('./{hex_id}.woff2') format('woff2'),")
        lines.append(f"       url('./{hex_id}.ttf') format('truetype'),")
        lines.append(
            f"       url('{CSS_FONT_URL_BASE}/{hex_id}.woff2') format('woff2');"
        )
        lines.append("  font-weight: normal;")
        lines.append("  font-style: normal;")
        lines.append("  font-display: swap;")
        lines.append(f"  unicode-range: {urange};")
        lines.append("}")
        lines.append("")

    with open(css_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {css_path}")

    # CSS-safe quoted family list for stacks
    quoted = ", ".join(f"'{name}'" for name in family_names)
    fontlist_path = os.path.join(out_dir, "fontlist.css")
    fontlist = f"""/* src/scss/index.scss — Pan-CJK pigeonhole font stack */
body {{
  --font-editor-theme: '';
  --font-editor: var(--font-editor-theme), var(--font-text);
  --font-text-theme:
    Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, FlopDesignFont, MKanaPlus, {quoted}, Malgun Gothic, Plangothic P1, Plangothic P2, monospace;
  --font-interface-theme:
    Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, FlopDesignFont, MKanaPlus, {quoted}, Malgun Gothic, Plangothic P1, Plangothic P2, monospace;
  --font-monospace-theme:
    Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, FlopDesignFont, MKanaPlus, {quoted}, Malgun Gothic, Plangothic P1, Plangothic P2, monospace;
}}
"""
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(fontlist)
    print(f"Wrote {fontlist_path}")


def regenerate_css_from_dist(out_dir: str) -> None:
    """Rewrite pancjk.css / fontlist.css from existing ``*.woff2`` / ``*.ttf``."""
    seen: Dict[str, None] = {}
    built: List[Tuple[str, int, List[int]]] = []
    for name in sorted(os.listdir(out_dir)):
        if not (name.endswith(".woff2") or name.endswith(".ttf")):
            continue
        hex_id = os.path.splitext(name)[0]
        if hex_id in seen:
            continue
        try:
            bucket_id = int(hex_id, 16)
        except ValueError:
            continue
        seen[hex_id] = None
        # Bucket-range unicode-range is enough for CSS (no need to open fonts).
        built.append((hex_id, 0, []))
        _ = bucket_id
    if not built:
        print(f"No bucket fonts found under {out_dir}", file=sys.stderr)
        sys.exit(1)
    print(f"Regenerating CSS for {len(built)} buckets from {out_dir}")
    write_css(out_dir, built)


def build_all(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    jobs: int,
    *,
    write_ttf: bool = True,
    write_woff2: bool = True,
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
    print(
        f"\nBuilding {len(buckets)} subfonts (glyph-by-glyph, {workers} workers) "
        f"-> {out_dir}",
        flush=True,
    )

    tasks = [(bid, buckets[bid]) for bid in sorted(buckets.keys())]
    total = len(tasks)
    written = 0
    glyph_total = 0
    skipped = 0
    built: List[Tuple[str, int, List[int]]] = []
    ttf_paths: List[str] = []
    done = 0

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_build_worker,
        initargs=(used_entries, out_dir, target_upem),
    ) as executor:
        futures = [executor.submit(_build_bucket_task, task) for task in tasks]
        for fut in concurrent.futures.as_completed(futures):
            bucket_id, path, count, codepoints = fut.result()
            done += 1
            hex_id = f"{bucket_id:X}"
            if count == 0:
                skipped += 1
                print(
                    f"  [{done}/{total}] {hex_id} skipped (empty)",
                    flush=True,
                )
                continue
            written += 1
            glyph_total += count
            built.append((hex_id, count, codepoints))
            ttf_paths.append(path)
            print(
                f"  [{done}/{total}] {hex_id} (ttf, {count} glyphs)",
                flush=True,
            )

    if write_woff2 and ttf_paths:
        print(f"\nCompressing {len(ttf_paths)} WOFF2...", flush=True)
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_compress_woff2_task, ttf_paths))

    if not write_ttf and ttf_paths:
        print(f"Removing {len(ttf_paths)} intermediate TTFs...", flush=True)
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_drop_ttf, ttf_paths))

    built.sort(key=lambda t: int(t[0], 16))
    write_css(out_dir, built)

    print(
        f"\nDone: {written} fonts, {glyph_total} glyphs, "
        f"{skipped} empty skipped, UPM={target_upem}, jobs={workers}",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build Pan-CJK pigeonhole subfonts")
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
        help="Parallel workers for bucket builds (default: CPU count)",
    )
    p.add_argument(
        "--css-only",
        action="store_true",
        help="Only regenerate pancjk.css / fontlist.css from existing fonts",
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
        )
