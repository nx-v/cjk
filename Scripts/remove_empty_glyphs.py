#!/usr/bin/env python3
"""
Remove empty (no-contour) glyphs from Makinas 4 Flat and Square,
keeping space characters. Writes cleaned fonts to dist/cleaned/.
"""

from __future__ import annotations

import os
import unicodedata
from typing import Dict, Set

from fontTools.ttLib import TTFont
from fontTools.subset import Subsetter, Options

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "cleaned")

INPUT_FONTS = [
    "Makinas-4-Flat.otf",
    "Makinas-4-Square.otf",
]

KEEP_GLYPHS = {".notdef", ".null", "nonmarkingreturn", "space"}


def is_space_codepoint(cp: int) -> bool:
    if cp in (0x0020, 0x00A0, 0x1680, 0x202F, 0x205F, 0x3000):
        return True
    if 0x2000 <= cp <= 0x200B:
        return True
    try:
        return unicodedata.category(chr(cp)) == "Zs"
    except ValueError:
        return False


def font_cmap(font: TTFont) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    for sub in font["cmap"].tables:
        if sub.isUnicode():
            cmap.update(sub.cmap)
    return cmap


def is_empty_glyph_truetype(font: TTFont, glyph_name: str) -> bool:
    glyf = font["glyf"]
    if glyph_name not in glyf:
        return True
    g = glyf[glyph_name]
    if g.isComposite():
        return False
    return g.numberOfContours <= 0


def is_empty_glyph_cff(font: TTFont, glyph_name: str) -> bool:
    if "CFF " in font:
        top = font["CFF "].cff.topDictIndex[0]
        cs = top.CharStrings
        if glyph_name in cs:
            return len(cs[glyph_name].program) == 0
        return True
    if "CFF2" in font:
        top = font["CFF2"].cff.topDictIndex[0]
        cs = top.CharStrings
        if glyph_name in cs:
            return len(cs[glyph_name].program) == 0
        return True
    return False


def is_empty_glyph(font: TTFont, glyph_name: str) -> bool:
    if "glyf" in font:
        return is_empty_glyph_truetype(font, glyph_name)
    if "CFF " in font or "CFF2" in font:
        return is_empty_glyph_cff(font, glyph_name)
    return False


def gather_empty_glyphs(font: TTFont) -> Set[str]:
    cmap = font_cmap(font)
    space_glyphs = {gn for cp, gn in cmap.items() if is_space_codepoint(cp)}
    protected = KEEP_GLYPHS | space_glyphs

    empty_glyphs: Set[str] = set()
    for gn in font.getGlyphOrder():
        if gn in protected:
            continue
        if is_empty_glyph(font, gn):
            empty_glyphs.add(gn)
    return empty_glyphs


def subset_drop_glyphs(font: TTFont, drop_glyphs: Set[str]) -> None:
    keep_glyphs = set(font.getGlyphOrder()) - set(drop_glyphs)

    options = Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.glyph_names = True
    options.notdef_glyph = True
    options.recalc_bounds = True
    options.recalc_timestamp = True

    subsetter = Subsetter(options=options)
    subsetter.populate(glyphs=keep_glyphs)
    subsetter.subset(font)


def clean_font(input_path: str, output_path: str) -> None:
    font = TTFont(input_path)

    empty_glyphs = gather_empty_glyphs(font)
    print(f"  Found {len(empty_glyphs)} empty non-space glyphs.")
    if empty_glyphs:
        subset_drop_glyphs(font, empty_glyphs)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    font.save(output_path)
    print(f"  Saved → {output_path}")
    font.close()


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    for filename in INPUT_FONTS:
        input_path = os.path.join(IN_DIR, filename)
        output_path = os.path.join(OUT_DIR, filename)
        if not os.path.isfile(input_path):
            print(f"Skip (missing): {input_path}")
            continue
        print(f"Cleaning {filename}...")
        clean_font(input_path, output_path)


if __name__ == "__main__":
    main()
