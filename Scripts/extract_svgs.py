#!/usr/bin/env python3
"""
Edenia Gothic SVG exporter builder

Merges glyphs from multiple Source Han Sans–derived fonts into three fonts, following
a priority given by alphabetical order of source font filenames.

- Edenia A:
  - CJK Unified Ideographs URO, Ext A/G/H/I
  - Compatibility Ideographs
  - CJK Symbols & Punctuation, Fullwidth forms (assorted)
- Edenia B:
  - CJK Extensions B–F
  - CJK Compatibility Ideographs Supplement
- Edenia C:
  - Kana (hiragana, katakana, small/kana ext, hentaigana ranges covered)
  - Bopomofo (+ Extended)
  - Hangul jamo (basic/extended) and syllables (+ compatibility jamo)

Note:
- Input fonts are read from IN_DIR, output fonts written to OUT_DIR.
- Priority is alphabetical: earlier font filename means
  higher priority and would be exported first.

Export each glyph as SVGs named after their code points.
"""

import argparse
import json
import os
from typing import Iterable, List, Set, Tuple

from fontTools.merge import Merger
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

# --- Hardcoded directories ---

IN_DIR = r"c:/users/admin/fonts/scripts/src"
OUT_DIR = r"c:/users/admin/fonts/scripts/dist"

# ---------- Unicode range helpers ----------


def ranges_to_set(ranges: Iterable[Tuple[int, int]]) -> Set[int]:
    s = set()
    for a, b in ranges:
        s.update(range(a, b + 1))
    return s


def list_input_fonts(in_dir: str) -> List[str]:
    files = []
    for fn in os.listdir(in_dir):
        if fn.lower().endswith((".ttf", ".otf", ".ttc")):
            files.append(os.path.join(in_dir, fn))
    files.sort(key=lambda p: os.path.basename(p).lower())
    return files


def font_has_codepoints(tt: TTFont) -> Set[int]:
    """Return set of unicode codepoints available in the font's cmap."""
    res = set()
    for table in tt["cmap"].tables:
        if table.isUnicode():
            res.update(table.cmap.keys())
    return res


def subset_font(in_path: str, codepoints: Iterable[int], out_path: str) -> None:
    tt = TTFont(in_path)
    options = Options()
    options.recalcBounds = True
    options.recalcTimestamp = False
    options.notdef_glyph = True
    options.retain_gids = False
    s = Subsetter(options=options)
    s.populate(unicodes=list(codepoints))
    s.subset(tt)
    tt.save(out_path)


def merge_font_files(font_paths: List[str]) -> TTFont:
    fonts = [TTFont(p) for p in font_paths]
    merger = Merger()
    merged = merger.merge(fonts)
    return merged


def export_glyph_svgs(tt: TTFont, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    units = tt["head"].unitsPerEm
    ascent = int(getattr(tt["hhea"], "ascent", units))
    glyphSet = tt.getGlyphSet()
    # build reverse cmap: codepoint -> glyphName
    cmap = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            for cp, g in table.cmap.items():
                cmap[cp] = g

    for cp, glyphName in cmap.items():
        try:
            pen = SVGPathPen(glyphSet)
            # transform: translate(0, ascent) scale(1, -1)
            transform = (1, 0, 0, -1, 0, ascent)
            tpen = TransformPen(pen, transform)
            glyph = glyphSet[glyphName]
            glyph.draw(tpen)
            pathdata = pen.getCommands()
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{units}" height="{units}" '
                f'viewBox="0 0 {units} {units}">\n'
                f'<path d="{pathdata}" fill="black" />\n'
                f"</svg>"
            )
        except Exception:
            # fallback: empty svg
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{units}" height="{units}" '
                f'viewBox="0 0 {units} {units}"></svg>'
            )

        fname = f"U+{cp:04X}.svg"
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write(svg)


def export_font_svgs(
    fontpath: str, out_dir: str, overwrite: bool = False, manifest: dict = None
) -> None:
    """Export glyph SVGs for a single font file into out_dir.

    If overwrite is False, existing files are left untouched. If a manifest dict is
    provided, it will be updated with entries mapping codepoint integer -> source font basename
    for every file actually written (or overwritten).
    """
    tt = TTFont(fontpath)
    try:
        os.makedirs(out_dir, exist_ok=True)
        units = tt["head"].unitsPerEm
        ascent = int(getattr(tt["hhea"], "ascent", units))
        glyphSet = tt.getGlyphSet()
        cmap = {}
        for table in tt["cmap"].tables:
            if table.isUnicode():
                for cp, g in table.cmap.items():
                    cmap[cp] = g

        source_name = os.path.basename(fontpath)
        for cp, glyphName in cmap.items():
            fname = f"U+{cp:04X}.svg"
            out_path = os.path.join(out_dir, fname)
            if (not overwrite) and os.path.exists(out_path):
                # skip existing file
                continue
            try:
                pen = SVGPathPen(glyphSet)
                transform = (1, 0, 0, -1, 0, ascent)
                tpen = TransformPen(pen, transform)
                glyph = glyphSet[glyphName]
                glyph.draw(tpen)
                pathdata = pen.getCommands()
                svg = (
                    f'<svg xmlns="http://www.w3.org/2000/svg" width="{units}" height="{units}" '
                    f'viewBox="0 0 {units} {units}">\n'
                    f'<path d="{pathdata}" fill="black" />\n'
                    f"</svg>"
                )
            except Exception:
                svg = (
                    f'<svg xmlns="http://www.w3.org/2000/svg" width="{units}" height="{units}" '
                    f'viewBox="0 0 {units} {units}"></svg>'
                )

            with open(out_path, "w", encoding="utf-8") as f:
                f.write(svg)
            if manifest is not None:
                manifest[int(cp)] = source_name
    finally:
        try:
            tt.close()
        except Exception:
            pass


def build_all(
    in_dir: str,
    out_dir: str,
    export_svgs: bool = True,
    overwrite: bool = False,
    manifest_name: str = "manifest.json",
) -> None:
    fonts = list_input_fonts(in_dir)
    if not fonts:
        print("No input fonts found in", in_dir)
        return

    os.makedirs(out_dir, exist_ok=True)
    manifest = {} if export_svgs else None

    for fontpath in fonts:
        base = os.path.splitext(os.path.basename(fontpath))[0]
        font_out_dir = os.path.join(out_dir, base)
        print(
            f"Exporting glyphs from {os.path.basename(fontpath)} into {font_out_dir} (overwrite={overwrite})"
        )
        if export_svgs:
            export_font_svgs(
                fontpath, font_out_dir, overwrite=overwrite, manifest=manifest
            )
            print("  Done")

    # write manifest
    if export_svgs and manifest is not None:
        manifest_path = os.path.join(out_dir, manifest_name)
        try:
            with open(manifest_path, "w", encoding="utf-8") as mf:
                # convert keys to U+XXXX strings for readability
                out_manifest = {f"U+{k:04X}": v for k, v in sorted(manifest.items())}
                json.dump(out_manifest, mf, ensure_ascii=False, indent=2)
            print("Wrote manifest to", manifest_path)
        except Exception as e:
            print("Failed to write manifest:", e)


def parse_args():
    p = argparse.ArgumentParser(description="Build Edenia fonts and export glyph SVGs")
    p.add_argument("--in", dest="in_dir", default=IN_DIR, help="Input fonts directory")
    p.add_argument("--out", dest="out_dir", default=OUT_DIR, help="Output directory")
    p.add_argument(
        "--no-svgs", dest="no_svgs", action="store_true", help="Do not export SVG files"
    )
    p.add_argument(
        "--overwrite",
        dest="overwrite",
        action="store_true",
        help="Overwrite existing SVG files",
    )
    p.add_argument(
        "--manifest",
        dest="manifest",
        default="manifest.json",
        help="Write manifest JSON filename in out dir",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_all(
        args.in_dir,
        args.out_dir,
        export_svgs=not args.no_svgs,
        overwrite=args.overwrite,
        manifest_name=args.manifest,
    )
