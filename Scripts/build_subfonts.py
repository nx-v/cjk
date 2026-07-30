#!/usr/bin/env python3
"""
Build Pan-CJK pigeonhole subfonts.

Claims CJK/Tangut codepoints from priority-ordered source fonts, buckets them
into 256-codepoint blocks (cp >> 8), and builds each TTF/WOFF2 from scratch by
copying (decomposed, scaled) glyphs one-by-one into a fresh FontBuilder font.

Also emits mirrored Private-Use-style variants for every claimed glyph:
  +0x40000  mirror on Y axis (horizontal flip)
  +0x80000  mirror on X axis (vertical flip)
  +0xC0000  mirror on both axes

Also writes pancjk.css (@font-face) and fontlist.css (CSS-safe stack).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, woff2
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

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

PRIORITY_FONTS = [
    "NGULIM.TTF",
    "Microsoft-JhengHei.ttf",
    "malgun.ttf",
    "LXGWZhiSongMN.ttf",
    "LXGWNeoZhiSongPlus.ttf",
    "HuayingMinchoT.ttf",
    "I.MingVarCP-8.10.ttf",
    "simsunb.ttf",
    "SimsunExtG.ttf",
    "NazoMin-Classic.ttf",
    "NazoMin+-Classic.ttf",
    "NotoSerifTangut-Regular.ttf",
]

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
    (0x0AC00, 0x0D7AF, "Hangul Syllables"),
    (0x03130, 0x0318F, "Hangul Compatibility Jamo"),
    (0x17000, 0x187FF, "Tangut"),
    (0x18D00, 0x18D7F, "Tangut Supplement"),
    (0x18800, 0x18AFF, "Tangut Components"),
    (0x18D80, 0x18DFF, "Tangut Components Supplement"),
]

# Mirrored variants: (codepoint offset, flip across X, flip across Y)
# flip_x → negate Y (mirror on X axis); flip_y → negate X (mirror on Y axis)
MIRROR_VARIANTS: List[Tuple[int, bool, bool]] = [
    (0x40000, True, False),   # X-axis mirror
    (0x80000, False, True),   # Y-axis mirror
    (0xD0000, True, True),    # both axes. Shifted to avoid conflict with variant selectors.
]

# (out_cp, source_path, src_cp, flip_x, flip_y)
BucketEntry = Tuple[int, str, int, bool, bool]


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


class SourceFont:
    """Lazy-open source font with cmap and drawing helpers."""

    def __init__(self, path: str):
        self.path = path
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
        """Decompose + scale (+ optional axis mirrors). Returns (glyph, advance, lsb).

        Mirrors flip around the contour bounding-box center so the glyph stays
        in place (does not reflect across the em-box / advance midline).
        """
        if is_empty_outline(self.tt, src_name):
            return None

        scale = target_upem / self.upem
        advance_src, lsb_src = self.hmtx[src_name]
        advance = otRound(advance_src * scale)

        try:
            rec = DecomposingRecordingPen(self.glyph_set)
            self.glyph_set[src_name].draw(rec)
        except Exception as e:
            print(
                f"  [!] draw failed {os.path.basename(self.path)}:{src_name}: {e}",
                file=sys.stderr,
            )
            return None

        sx = scale
        sy = scale
        dx = 0.0
        dy = 0.0
        if flip_x or flip_y:
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
            # Reflect across contour center, then scale into target UPM.
            # x' = scale * (2*cx - x) = -scale*x + 2*cx*scale  (flip_y)
            if flip_y:
                sx = -scale
                dx = 2.0 * cx * scale
            if flip_x:
                sy = -scale
                dy = 2.0 * cy * scale

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

        try:
            glyph.recalcBounds(None)
            lsb = int(glyph.xMin)
        except Exception:
            lsb = otRound(lsb_src * scale)
        return glyph, advance, lsb


def resolve_priority_paths(in_dir: str) -> List[str]:
    paths: List[str] = []
    for name in PRIORITY_FONTS:
        path = os.path.join(in_dir, name)
        if not os.path.isfile(path):
            print(f"[!] Missing priority font: {name}", file=sys.stderr)
            continue
        paths.append(path)
    return paths


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


def expand_with_mirrors(owner: Dict[int, str]) -> List[BucketEntry]:
    """Original claims plus PUA-offset mirrored variants."""
    entries: List[BucketEntry] = []
    for cp, path in owner.items():
        entries.append((cp, path, cp, False, False))
        for offset, flip_x, flip_y in MIRROR_VARIANTS:
            out_cp = cp + offset
            if out_cp > 0x10FFFF:
                continue
            entries.append((out_cp, path, cp, flip_x, flip_y))
    return entries


def bucket_codepoints(entries: List[BucketEntry]) -> Dict[int, List[BucketEntry]]:
    """bucket_id -> sorted list of bucket entries."""
    buckets: Dict[int, List[BucketEntry]] = defaultdict(list)
    for entry in entries:
        buckets[entry[0] >> 8].append(entry)
    for bid in buckets:
        buckets[bid].sort(key=lambda t: t[0])
    return buckets


def build_bucket_font(
    bucket_id: int,
    entries: List[BucketEntry],
    sources: Dict[str, SourceFont],
    out_dir: str,
    target_upem: int,
) -> Tuple[str, int, List[int]]:
    """Build one pigeonhole font from scratch. Returns (ttf_path, count, codepoints)."""
    hex_id = f"{bucket_id:X}"
    out_path = os.path.join(out_dir, f"{hex_id}.ttf")

    glyph_order = [".notdef"]
    glyphs: Dict[str, TTGlyph] = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}

    for out_cp, path, src_cp, flip_x, flip_y in entries:
        src = sources[path]
        src_name = src.cmap.get(src_cp)
        if src_name is None:
            continue
        copied = src.copy_glyph(src_name, target_upem, flip_x=flip_x, flip_y=flip_y)
        if copied is None:
            continue
        glyph, advance, lsb = copied
        gname = glyph_name_for_cp(out_cp)
        glyph_order.append(gname)
        glyphs[gname] = glyph
        metrics[gname] = (advance, lsb)
        cmap[out_cp] = gname

    if len(cmap) == 0:
        return out_path, 0, []

    ascent = otRound(target_upem * 0.88)
    descent = otRound(target_upem * -0.12)
    family = f"pancjk {hex_id}"
    ps = f"pancjk-{hex_id}"

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
    fb.save(out_path)

    woff2_path = os.path.join(out_dir, f"{hex_id}.woff2")
    woff2.compress(out_path, woff2_path)

    return out_path, len(cmap), sorted(cmap.keys())


# ---------- Parallel workers ----------

_WORKER_SOURCES: Optional[Dict[str, SourceFont]] = None
_WORKER_OUT_DIR: Optional[str] = None
_WORKER_UPEM: Optional[int] = None


def _init_build_worker(font_paths: List[str], out_dir: str, target_upem: int) -> None:
    """Load source fonts once per process worker."""
    global _WORKER_SOURCES, _WORKER_OUT_DIR, _WORKER_UPEM
    _WORKER_OUT_DIR = out_dir
    _WORKER_UPEM = target_upem
    _WORKER_SOURCES = {p: SourceFont(p) for p in font_paths}


def _build_bucket_task(
    args: Tuple[int, List[BucketEntry]],
) -> Tuple[int, str, int, List[int]]:
    bucket_id, entries = args
    assert _WORKER_SOURCES is not None
    assert _WORKER_OUT_DIR is not None
    assert _WORKER_UPEM is not None
    path, count, codepoints = build_bucket_font(
        bucket_id, entries, _WORKER_SOURCES, _WORKER_OUT_DIR, _WORKER_UPEM
    )
    return bucket_id, path, count, codepoints


def unicode_range_for_bucket(bucket_id: int, codepoints: List[int]) -> str:
    """CSS unicode-range covering present glyphs (merged contiguous runs)."""
    if not codepoints:
        start = bucket_id << 8
        end = start + 0xFF
        return f"U+{start:X}-{end:X}"

    runs: List[str] = []
    run_start = codepoints[0]
    prev = codepoints[0]
    for cp in codepoints[1:]:
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
        "",
    ]
    family_names: List[str] = []
    for hex_id, _count, codepoints in built:
        bucket_id = int(hex_id, 16)
        family = f"pancjk {hex_id}"
        family_names.append(family)
        urange = unicode_range_for_bucket(bucket_id, codepoints)
        url = f"{CSS_FONT_URL_BASE}/{hex_id}.woff2"
        lines.append("@font-face {")
        lines.append(f"  font-family: '{family}';")
        lines.append(f"  src: url('{url}') format('woff2');")
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
    quoted = ",\n    ".join(f"'{name}'" for name in family_names)
    fontlist_path = os.path.join(out_dir, "fontlist.css")
    fontlist = f"""/* src/scss/index.scss — Pan-CJK pigeonhole font stack */
body {{
  --font-editor-theme: '';
  --font-editor: var(--font-editor-theme), var(--font-text);
  --font-text-theme:
    Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, FlopDesignFont,
    MKanaPlus, Noto Sans Devanagari, Noto Sans Bengali, Noto Sans Gurmukhi,
    Noto Sans Gujarati, Noto Sans Gunjala Gondi, Noto Sans Nandinagari,
    Noto Sans Newa, Noto Sans Sharada,
    {quoted},
    Malgun Gothic, Plangothic P1, Plangothic P2, monospace;
  --font-interface-theme:
    Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, FlopDesignFont,
    MKanaPlus, Noto Sans Devanagari, Noto Sans Bengali, Noto Sans Gurmukhi,
    Noto Sans Gujarati, Noto Sans Gunjala Gondi, Noto Sans Nandinagari,
    Noto Sans Newa, Noto Sans Sharada,
    {quoted},
    Malgun Gothic, Plangothic P1, Plangothic P2, monospace;
  --font-monospace-theme:
    Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, FlopDesignFont,
    MKanaPlus, Noto Sans Devanagari, Noto Sans Bengali, Noto Sans Gurmukhi,
    Noto Sans Gujarati, Noto Sans Gunjala Gondi, Noto Sans Nandinagari,
    Noto Sans Newa, Noto Sans Sharada,
    {quoted},
    Malgun Gothic, Plangothic P1, Plangothic P2, monospace;
}}
"""
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(fontlist)
    print(f"Wrote {fontlist_path}")


def build_all(in_dir: str, out_dir: str, target_upem: int, jobs: int) -> None:
    font_paths = resolve_priority_paths(in_dir)
    if not font_paths:
        print("No priority fonts found in", in_dir, file=sys.stderr)
        sys.exit(1)

    target = ranges_to_set(CHAR_RANGES)
    print(f"Target range size: {len(target)} codepoints")
    print(f"Source fonts: {len(font_paths)}")

    sources_list = [SourceFont(p) for p in font_paths]
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
    for name in PRIORITY_FONTS:
        if name in per_source:
            print(f"  {name}: {per_source[name]}")

    all_entries = expand_with_mirrors(owner)
    mirror_count = len(all_entries) - len(owner)
    print(
        f"\nExpanded with mirrors: {len(owner)} base + {mirror_count} mirrored "
        f"= {len(all_entries)} glyphs"
    )

    buckets = bucket_codepoints(all_entries)
    os.makedirs(out_dir, exist_ok=True)

    used_paths = sorted(set(owner.values()))
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
    done = 0

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_build_worker,
        initargs=(used_paths, out_dir, target_upem),
    ) as executor:
        futures = [executor.submit(_build_bucket_task, task) for task in tasks]
        for fut in concurrent.futures.as_completed(futures):
            bucket_id, _path, count, codepoints = fut.result()
            done += 1
            hex_id = f"{bucket_id:X}"
            if count == 0:
                skipped += 1
                print(
                    f"  [{done}/{total}] {hex_id}.ttf skipped (empty)",
                    flush=True,
                )
                continue
            written += 1
            glyph_total += count
            built.append((hex_id, count, codepoints))
            print(
                f"  [{done}/{total}] {hex_id}.ttf/.woff2 ({count} glyphs)",
                flush=True,
            )

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
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_all(args.in_dir, args.out_dir, args.upem, args.jobs)
