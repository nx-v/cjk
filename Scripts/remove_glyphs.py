#!/usr/bin/env python3
"""
Edenia Gothic font
Merges glyphs from multiple Source Han Sans-derived fonts into three fonts, following a priority given by the file names.

- Edenia A:
  - CJK URO, Ext A/G/H/I
  - Compatibility Ideographs
  - assorted fullwidth characters and symbols
- Edenia B:
  - CJK B-F
  - Compatibility Supplement
- Edenia C:
  - Kana (hiragana, katakana, hentaigana)
  - Bopomofo
  - Hangul jamo and syllables, with voicing diacritics and extra letters
"""

from fontTools.ttLib import TTFont
from fontTools.subset import Subsetter, Options

# Hardcoded input and output file paths
INPUT_FONT = "c:/Users/Admin/fonts/Scripts/YshiYuanGothic.ttf"
OUTPUT_FONT = "c:/Users/Admin/fonts/Scripts/YshiYuanGothic_Cleaned.ttf"

# Note: Font is split into 3 fonts, one for each plane.

# CJK ranges.
CJK_RANGES = [
    (0x4E00, 0x9FFF),  # CJK URO
    (0x3400, 0x4DBF),  # Extension A
    (0x20000, 0x2A6DF),  # Extension B
    (0x2A700, 0x2B739),  # Extension C
    (0x2B740, 0x2B81D),  # Extension D
    (0x2B820, 0x2CEA1),  # Extension E
    (0x2CEB0, 0x2EBE0),  # Extension F
    (0x2EBF0, 0x2EE5D),  # Extension G
    (0x30000, 0x3134A),  # Extension H
    (0x31350, 0x323AF),  # Extension I
    (0xFA00, 0xFAD9),  # Compatibility Ideographs
    (0x2F800, 0x2FA1D),  # Compatibility Supplement
]

def codepoint_in_cjk(cp):
    for start, end in CJK_RANGES:
        if start <= cp <= end:
            return True
    return False

def is_empty_glyph_truetype(font, glyph_name):
    glyf = font["glyf"]
    g = glyf[glyph_name]
    if g.isComposite():
        return False
    return g.numberOfContours == 0

def is_empty_glyph_cff(font, glyph_name):
    if "CFF " in font:
        top = font["CFF "].cff.topDictIndex[0]
        cs = top.CharStrings
        if glyph_name in cs:
            return len(cs[glyph_name].program) == 0
    if "CFF2" in font:
        top = font["CFF2"].cff.topDictIndex[0]
        cs = top.CharStrings
        if glyph_name in cs:
            return len(cs[glyph_name].program) == 0
    return False

def gather_empty_glyphs(font):
    mapped = {}
    for sub in font["cmap"].tables:
        if sub.isUnicode():
            for cp, gn in sub.cmap.items():
                if codepoint_in_cjk(cp):
                    mapped[cp] = gn

    empty_glyphs = set()
    for cp, gn in mapped.items():
        if gn in {".notdef", ".null", "nonmarkingreturn"}:
            continue
        if "glyf" in font and is_empty_glyph_truetype(font, gn):
            empty_glyphs.add(gn)
        elif ("CFF " in font or "CFF2" in font) and is_empty_glyph_cff(font, gn):
            empty_glyphs.add(gn)
    return empty_glyphs

def subset_drop_glyphs(font, drop_glyphs):
    all_glyphs = set(font.getGlyphOrder())
    keep_glyphs = all_glyphs - set(drop_glyphs)

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

def main():
    font = TTFont(INPUT_FONT)

    empty_glyphs = gather_empty_glyphs(font)
    print(f"Found {len(empty_glyphs)} empty glyphs in CJK ranges.")
    if empty_glyphs:
        subset_drop_glyphs(font, empty_glyphs)
        font.save(OUTPUT_FONT)
        print(f"Saved cleaned font → {OUTPUT_FONT}")
    else:
        font.save(OUTPUT_FONT)
        print("No empty CJK glyphs found. Font copied unchanged.")


if __name__ == "__main__":
    main()
