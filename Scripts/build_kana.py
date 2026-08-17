#!/usr/bin/env python3
"""
Build the single ``edenia kana`` font (PUA D4 cmap + smalls + FE00/FE01 slices + dakuten).

Encoding
--------
BMP PUA ``U+E000``..``U+F8FF`` (6400 CPs)::

    i        = L * 8 + o          # L = 0..399, o = 0..7 (D4_MODES order)
    full[i]  = 0xE000 + 2 * i     # even — full-size oriented form
    small[i] = 0xE000 + 2 * i + 1 # odd  — small: ideo-scale + Weight once, D4 @ ideo

Halfwidth companions (same ``i``) in SPUA-A::

    hw_full[i]  = 0xED00 + 2 * i
    hw_small[i] = 0xED00 + 2 * i + 1

CAPE Width ``0.5`` holds the pre-squeeze stem thicknesses (match full-width
kana). Slices use the half-em cell + ``sliceAdvHw``.

Initial fill: hiragana rows then length/gemination, then katakana rows
then length/gemination — row-major into ``L``. Sources: LXGW Fasmart Gothic,
then FlopDesignFONT, then mkanaplus (PUA/archaic + overrides), then GenSeki
Hentaigana, then LXGW (Clear Gothic / XiHei). Non-Fasmart glyphs whose ink
exceeds Fasmart's hiragana ふ (U+3075) width or katakana メ (U+30E1) height
are geometrically condensed to those caps (stroke weight scales with the
squish; no separate stem Weight match).

Trailing marks (all D4)::

    hiragana  length U+301C 〜 · gemination U+309D ゝ
    katakana  length U+30FC ー · gemination U+30FD ヽ

Umlaut orientations are real cmap entries (no VS). Ligatures use FE00/FE01
half-plane slices (Yi keeps FE08/FE09). Dakuten GPOS is contour-corner.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, woff2
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from cape_weightor import (
    bolden_ttglyph,
    estimate_horizontal_stem,
    estimate_vertical_stem,
    heighten_ttglyph,
    layer_from_ttglyph,
    widen_ttglyph,
)
from shared_diacritics import (
    CGJ_CP,
    DAKUTEN_EDGE_PAD_FRAC,
    DAKUTEN_MARK_HEIGHT_FRAC,
    DAKUTEN_SLOT_CYCLE,
    DAKUTEN_SLOTS,
    add_dakuten_mark_glyphs,
    add_dakuten_mark_scale_variants,
    dakuten_mark_stack_label,
    install_dakuten_gpos,
    install_dakuten_mark_variant_gsub,
    install_dakuten_slot_gsub,
    load_dakuten_marks_from_stack,
    resolve_dakuten_mark_font_stack,
)
from shared_half_cells import (
    DEFAULT_UPEM,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    YI_ORIENTATION_MODES,
    add_d4_variant_glyphs,
    apply_transform,
    empty_glyph,
    fit_glyph_to_ideographic_cell,
    ideographic_bounds,
    ideographic_center,
    orientation_form_names,
    variant_glyph_name,
    variant_transform,
)
from shared_half_cells import _bake_transformed_glyph  # composite → plain outlines
from yi_slice import (
    HALF_SUFFIXES,
    SLICE_ADV_NAME,
    add_slice_halves,
    half_glyph_name,
    inject_slice_marks,
    install_slice_gsub,
)
from edenia_names import CSS_KANA, FAMILY_KANA, PS_KANA
from sync_edenian_fonts import sync_dist_to_plugin
from cdn_fonts import dist_rel, format_src_line

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "kana")

FAMILY_NAME = FAMILY_KANA
PS_NAME = PS_KANA

PUA_START = 0xE000
PUA_END = 0xF8FF  # inclusive; 6400 CPs
D4_COUNT = 8
LOGICAL_CAPACITY = 400  # 400 * 8 * 2 = 6400
SMALL_WIDTH_FACTOR = 0.75
# After uniform scale, CAPE Weight restores stroke thickness to match full-size.
SMALL_WEIGHT_FACTOR = 1.0 / SMALL_WIDTH_FACTOR
# Halfwidth: CAPE Width 0.5, stems held at the pre-squeeze (fixed) values.
HALF_WIDTH_FACTOR = 0.5
HW_PUA_START = 0xED00
SLICE_ADV_HW_NAME = "sliceAdvHw"
# Pankana slices: FE00 H (top+bot), FE01 V (left+right). Not VS — D4 is PUA.
KANA_SLICE_H_CP = 0xFE00
KANA_SLICE_V_CP = 0xFE01
KANA_SLICE_MODES: Tuple[Tuple[int, str, str, str], ...] = (
    (KANA_SLICE_H_CP, "vsSliceH", "top", "bot"),
    (KANA_SLICE_V_CP, "vsSliceV", "left", "right"),
)
# FlopDesignFONT is CFF (cubics). TrueType glyf needs quads.
CU2QU_MAX_ERR = 0.5

FLOP_FILENAMES: Tuple[str, ...] = (
    "FlopDesignFONT.otf",
    "FlopDesignFONT.ttf",
    "FlopDesignFont.otf",
)
FASMART_FILENAMES: Tuple[str, ...] = (
    "LXGWFasmartGothic.ttf",
    "LXGWFasmartGothicMN.ttf",
    "LXGWFasmartGothicCL.ttf",
)
# Reference outer size for geometric Width/Height-cap on non-Fasmart sources.
FASMART_FU_CP = 0x3075  # ふ — max ink width
FASMART_ME_CP = 0x30E1  # メ — max ink height
MKANA_FILENAMES: Tuple[str, ...] = (
    "mkanaplus.ttf",
    "mkanaplus-regular.ttf",
)
GENSEKI_FILENAMES: Tuple[str, ...] = (
    "GenSekiHentaiganaGothic.ttf",
    "GensekiHentaiganaGothic.ttf",
    "GenSekiHentaigana.ttf",
)
# One LXGW family: Clear Gothic and XiHei are the same design, extra faces.
LXGW_FAMILY_FILENAMES: Tuple[str, ...] = (
    "LXGWClearGothic-Regular.ttf",
    "LXGWClearGothic-Book.ttf",
    "LXGWXiHeiMN.ttf",
    "LXGWXiHeiCL.ttf",
)

# Source-shape overrides: always claim from mkanaplus when present.
MKANA_OVERRIDE_CPS: frozenset[int] = frozenset(
    {
        0x304F,  # く
        0x3078,  # へ
        0x30A2,  # ア
        0x30BD,  # ソ
        0x30EB,  # ル
        0x30EF,  # ワ
    }
)
# Prefer FlopDesignFONT over Fasmart for these shapes.
FLOP_OVERRIDE_CPS: frozenset[int] = frozenset(
    {
        0x304B,  # か
        0x305D,  # そ
    }
)

# Shared consonant/vowel axes for both hiragana and katakana charts.
CONSONANTS: Tuple[str, ...] = (
    "",
    "k",
    "ng",
    "t",
    "ts",
    "ch",
    "sh",
    "s",
    "m",
    "n",
    "h",
    "y",
    "l",
    "r",
    "w",
    "f",
    "p",
)
VOWELS: Tuple[str, ...] = ("a", "i", "u", "e", "o", "ə")

# 17×6 hiragana, then 7×6 katakana (row-major). Values = source CPs.
HIRAGANA_ROWS: Tuple[Tuple[int, ...], ...] = (
    (0x3042, 0x3044, 0x3046, 0x3048, 0x304A, 0x1B015),  # ∅
    (0x304B, 0x304D, 0x304F, 0x3051, 0x3053, 0x1B02B),  # k
    (0xE020, 0xE021, 0xE022, 0xE023, 0xE024, 0x1B033),  # ng
    (0x305F, 0xED10, 0x1B06D, 0x3066, 0x3068, 0x1B077),  # t
    (0xED1C, 0xED1E, 0x3064, 0xED20, 0xED22, 0x1B06A),  # ts
    (0xED14, 0x3061, 0xED16, 0xED18, 0xED1A, 0x1B063),  # ch
    (0x1B043, 0x3057, 0xED0A, 0xED0C, 0xED0E, 0x1B044),  # sh
    (0x3055, 0xED01, 0x3059, 0x305B, 0x305D, 0x1B053),  # s
    (0x307E, 0x307F, 0x3080, 0x3081, 0x3082, 0x1B0DA),  # m
    (0x306A, 0x306B, 0x306C, 0x306D, 0x306E, 0x3093),  # n
    (0x306F, 0x3072, 0x1B039, 0x3078, 0x307B, 0x1B0C0),  # h
    (0x3084, 0x1B006, 0x3086, 0x1B001, 0x3088, 0x1B0E5),  # y
    (0xE0E0, 0xE0E1, 0xE0E2, 0xE0E3, 0xE0E4, 0x1B102),  # l
    (0x3089, 0x308A, 0x308B, 0x308C, 0x308D, 0x1B0EF),  # r
    (0x308F, 0x3090, 0x1B11F, 0x3091, 0x3092, 0x1B10C),  # w
    (0x1B0A6, 0x1B0AB, 0x3075, 0x1B0B8, 0x1B0BF, 0xECC1),  # f
    (0xE030, 0xE031, 0xE032, 0xE02A, 0xE034, 0x1B0AF),  # p
)

KATAKANA_ROWS: Tuple[Tuple[int, ...], ...] = (
    (0x30A2, 0x30A4, 0x30A6, 0x30A8, 0x30AA, 0x31A6),  # ∅
    (0x30AB, 0x30AD, 0x30AF, 0x30B1, 0x30B3, 0x310E),  # k
    (0xEDD3, 0xEC69, 0xEDCA, 0xEDC6, 0xEDD7, 0x312B),  # ng
    (0x30BF, 0xED50, 0xED52, 0x30C6, 0x30C8, 0x3109),  # t
    (0xED5C, 0xED5E, 0x30C4, 0xED60, 0xED62, 0x3118),  # ts
    (0xED54, 0x30C1, 0xED56, 0xED58, 0xED5A, 0x3114),  # ch
    (0xED48, 0x30B7, 0xED4A, 0xED4C, 0xED4E, 0x3115),  # sh
    (0x30B5, 0xED41, 0x30B9, 0x30BB, 0x30BD, 0x3112),  # s
    (0x30DE, 0x30DF, 0x30E0, 0x30E1, 0x30E2, 0x3107),  # m
    (0x30CA, 0x30CB, 0x30CC, 0x30CD, 0x30CE, 0x30F3),  # n
    (0x30CF, 0x30D2, 0xEE45, 0x30D8, 0x30DB, 0x310F),  # h
    (0x30E4, 0x1B120, 0x30E6, 0x1B121, 0x30E8, 0xEDCF),  # y
    (0xEDC3, 0xEDC8, 0xEDC0, 0xEDC5, 0xEDC1, 0x310C),  # l
    (0x30E9, 0x30EA, 0x30EB, 0x30EC, 0x30ED, 0xEDD7),  # r
    (0x30EF, 0x30F0, 0x1B122, 0x30F1, 0x30F2, 0x3129),  # w
    (0xEDCC, 0xEDCD, 0x30D5, 0xEDD8, 0xEDD4, 0x3108),  # f
    (0xEDCB, 0xEDCA, 0xEE69, 0xEDD0, 0xEDC4, 0x3105),  # p
)

# After each script's last phonetic cell: length, then gemination.
HIRAGANA_LENGTH_CP = 0x301C  # 〜 WAVE DASH
HIRAGANA_GEMINATION_CP = 0x309D  # ゝ HIRAGANA ITERATION MARK
KATAKANA_LENGTH_CP = 0x30FC  # ー KATAKANA-HIRAGANA PROLONGED SOUND MARK
KATAKANA_GEMINATION_CP = 0x30FD  # ヽ KATAKANA ITERATION MARK
SCRIPT_TRAILING_CPS: Tuple[Tuple[str, int], ...] = (
    ("length", HIRAGANA_LENGTH_CP),
    ("gemination", HIRAGANA_GEMINATION_CP),
)
KATAKANA_TRAILING_CPS: Tuple[Tuple[str, int], ...] = (
    ("length", KATAKANA_LENGTH_CP),
    ("gemination", KATAKANA_GEMINATION_CP),
)
SCRIPT_TRAILING_COUNT = len(SCRIPT_TRAILING_CPS)

# Phonetic chart only (no trailing marks).
CHART_ROWS: Tuple[Tuple[int, ...], ...] = HIRAGANA_ROWS + KATAKANA_ROWS
HIRAGANA_PHONETIC_COUNT = sum(len(r) for r in HIRAGANA_ROWS)
KATAKANA_PHONETIC_COUNT = sum(len(r) for r in KATAKANA_ROWS)
HIRAGANA_COUNT = HIRAGANA_PHONETIC_COUNT + SCRIPT_TRAILING_COUNT
KATAKANA_COUNT = KATAKANA_PHONETIC_COUNT + SCRIPT_TRAILING_COUNT


def chart_source_cps() -> List[int]:
    """Row-major source CPs: hiragana (+marks) then katakana (+marks)."""
    out: List[int] = []
    for row in HIRAGANA_ROWS:
        out.extend(row)
    out.extend(cp for _lab, cp in SCRIPT_TRAILING_CPS)
    for row in KATAKANA_ROWS:
        out.extend(row)
    out.extend(cp for _lab, cp in KATAKANA_TRAILING_CPS)
    return out


def trailing_mark_label(logical: int) -> Optional[str]:
    """``length`` / ``gemination`` if ``logical`` is a script trailer, else None."""
    if HIRAGANA_PHONETIC_COUNT <= logical < HIRAGANA_COUNT:
        return SCRIPT_TRAILING_CPS[logical - HIRAGANA_PHONETIC_COUNT][0]
    kata0 = HIRAGANA_COUNT
    if kata0 + KATAKANA_PHONETIC_COUNT <= logical < kata0 + KATAKANA_COUNT:
        return KATAKANA_TRAILING_CPS[logical - kata0 - KATAKANA_PHONETIC_COUNT][0]
    return None


def pair_index(logical: int, orient: int) -> int:
    return logical * D4_COUNT + orient


def full_cp(i: int) -> int:
    return PUA_START + 2 * i


def small_cp(i: int) -> int:
    return PUA_START + 2 * i + 1


def hw_full_cp(i: int) -> int:
    return HW_PUA_START + 2 * i


def hw_small_cp(i: int) -> int:
    return HW_PUA_START + 2 * i + 1


def glyph_name_for_cp(cp: int) -> str:
    return f"u{cp:04X}" if cp <= 0xFFFF else f"u{cp:05X}"


def logical_base_name(logical: int) -> str:
    return f"kL{logical:03d}"


def small_base_name(logical: int) -> str:
    return f"kL{logical:03d}.sm"


def hw_base_name(logical: int) -> str:
    return f"kL{logical:03d}.hw"


def hw_small_base_name(logical: int) -> str:
    return f"kL{logical:03d}.hw.sm"


def _first_existing(paths: Sequence[str]) -> Optional[str]:
    for path in paths:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


def resolve_flop_path(in_dir: str) -> str:
    candidates: List[str] = []
    for name in FLOP_FILENAMES:
        candidates.append(os.path.join(in_dir, name))
        candidates.append(os.path.join(SCRIPT_DIR, "src", name))
        candidates.append(os.path.join(REPO_ROOT, name))
    found = _first_existing(candidates)
    if found is None:
        raise FileNotFoundError(
            f"FlopDesignFONT not found under {in_dir!r} / Scripts/src / repo root"
        )
    return found


def resolve_fasmart_family_paths(in_dir: str) -> List[str]:
    """LXGW Fasmart Gothic faces. Prefer Scripts/src per name."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    found: List[str] = []
    seen: set[str] = set()
    for name in FASMART_FILENAMES:
        path = _first_existing(
            (
                os.path.join(src_dir, name),
                os.path.join(in_dir, name),
                os.path.join(REPO_ROOT, "LXGW", name),
                os.path.join(REPO_ROOT, name),
            )
        )
        if path is None:
            continue
        key = os.path.normcase(os.path.basename(path))
        if key in seen:
            continue
        seen.add(key)
        found.append(path)
    if not found:
        raise FileNotFoundError(
            f"LXGW Fasmart Gothic not found under "
            f"Scripts/src / {in_dir!r} / LXGW / repo root"
        )
    return found


def resolve_mkana_path(in_dir: str) -> str:
    """Prefer Scripts/src (mkanaplus lives there), then in_dir / Kana / repo."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    candidates: List[str] = []
    # All names under Scripts/src first (user inventory lives there).
    for name in MKANA_FILENAMES:
        candidates.append(os.path.join(src_dir, name))
    for name in MKANA_FILENAMES:
        candidates.append(os.path.join(in_dir, name))
        candidates.append(os.path.join(REPO_ROOT, "Kana", name))
        candidates.append(os.path.join(REPO_ROOT, name))
    found = _first_existing(candidates)
    if found is None:
        raise FileNotFoundError(
            f"mkanaplus not found under Scripts/src / {in_dir!r} / Kana / repo root"
        )
    return found


def resolve_genseki_path(in_dir: str) -> str:
    """Prefer Scripts/src, then in_dir / repo root."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    candidates: List[str] = []
    for name in GENSEKI_FILENAMES:
        candidates.append(os.path.join(src_dir, name))
    for name in GENSEKI_FILENAMES:
        candidates.append(os.path.join(in_dir, name))
        candidates.append(os.path.join(REPO_ROOT, name))
    found = _first_existing(candidates)
    if found is None:
        raise FileNotFoundError(
            f"GenSeki Hentaigana not found under Scripts/src / {in_dir!r} / repo root"
        )
    return found


def resolve_lxgw_family_paths(in_dir: str) -> List[str]:
    """All LXGW Clear Gothic / XiHei faces. Prefer Scripts/src per name."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    found: List[str] = []
    seen: set[str] = set()
    for name in LXGW_FAMILY_FILENAMES:
        path = _first_existing(
            (
                os.path.join(src_dir, name),
                os.path.join(in_dir, name),
                os.path.join(REPO_ROOT, "LXGW", name),
                os.path.join(REPO_ROOT, name),
            )
        )
        if path is None:
            continue
        key = os.path.normcase(os.path.basename(path))
        if key in seen:
            continue
        seen.add(key)
        found.append(path)
    if not found:
        raise FileNotFoundError(
            f"LXGW family (Clear Gothic / XiHei) not found under "
            f"Scripts/src / {in_dir!r} / LXGW / repo root"
        )
    return found


def font_cmap(tt: TTFont) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    return cmap


def _cff_program_empty(charstring) -> bool:
    """CFF T2CharString.program is [] until decompile(); don't treat that as empty."""
    if getattr(charstring, "needsDecompilation", False):
        try:
            charstring.decompile()
        except Exception:
            return True
    program = getattr(charstring, "program", None) or []
    return len(program) == 0


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
        return glyph_name not in cs or _cff_program_empty(cs[glyph_name])
    if "CFF2" in tt:
        top = tt["CFF2"].cff.topDictIndex[0]
        cs = top.CharStrings
        return glyph_name not in cs or _cff_program_empty(cs[glyph_name])
    return True


def _quadratic_glyph_from_recording(
    rec: RecordingPen,
    transform: Transform,
    *,
    max_err: float = CU2QU_MAX_ERR,
) -> TTGlyph:
    """Replay CFF cubics through Cu2Qu so the glyf table stays format 0."""
    pen = TTGlyphPen(None)
    rec.replay(TransformPen(Cu2QuPen(pen, max_err), transform))
    glyph = pen.glyph()
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    return glyph


class SourceFont:
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

    def copy_fitted(
        self, src_name: str, target_upem: int
    ) -> Optional[Tuple[TTGlyph, int, int]]:
        if is_empty_outline(self.tt, src_name):
            return None
        upem_scale = target_upem / self.upem
        advance_src, _lsb_src = self.hmtx[src_name]
        advance = otRound(advance_src * upem_scale)
        try:
            rec = DecomposingRecordingPen(self.glyph_set)
            self.glyph_set[src_name].draw(rec)
        except Exception as exc:
            print(
                f"  [!] draw failed {os.path.basename(self.path)}:{src_name}: {exc}",
                file=sys.stderr,
            )
            return None
        t = Transform(upem_scale, 0, 0, upem_scale, 0, 0)
        glyph = _quadratic_glyph_from_recording(rec, t)
        if glyph.numberOfContours == 0 and not glyph.isComposite():
            return None
        if advance <= 0:
            advance = target_upem
        return fit_glyph_to_ideographic_cell(glyph, advance, target_upem)


def claim_source_cp(
    src_cp: int,
    fasmart: Sequence[SourceFont],
    flop: SourceFont,
    mkana: SourceFont,
    genseki: SourceFont,
    lxgw: Sequence[SourceFont],
) -> Tuple[SourceFont, str]:
    """Return (source, glyph_name) for a chart source CP."""
    if src_cp in MKANA_OVERRIDE_CPS:
        head: Tuple[SourceFont, ...] = (mkana, *fasmart, flop, genseki)
    elif src_cp in FLOP_OVERRIDE_CPS:
        head = (flop, *fasmart, mkana, genseki)
    else:
        head = (*fasmart, flop, mkana, genseki)
    for src in (*head, *lxgw):
        gname = src.cmap.get(src_cp)
        if gname is None:
            continue
        if is_empty_outline(src.tt, gname):
            continue
        return src, gname
    raise KeyError(
        f"No outline for U+{src_cp:04X} in fasmart/Flop/mkanaplus/genseki/lxgw"
    )


def _glyph_ink_width(glyph: TTGlyph) -> float:
    try:
        glyph.recalcBounds(None)
        return float(glyph.xMax) - float(glyph.xMin)
    except Exception:
        return 0.0


def _glyph_ink_height(glyph: TTGlyph) -> float:
    try:
        glyph.recalcBounds(None)
        return float(glyph.yMax) - float(glyph.yMin)
    except Exception:
        return 0.0


def _fasmart_fitted_glyph(
    fasmart: SourceFont, src_cp: int, target_upem: int, *, label: str
) -> TTGlyph:
    gname = fasmart.cmap.get(src_cp)
    if gname is None:
        raise KeyError(
            f"Fasmart missing {label} U+{src_cp:04X} "
            f"in {os.path.basename(fasmart.path)}"
        )
    copied = fasmart.copy_fitted(gname, target_upem)
    if copied is None:
        raise RuntimeError(
            f"Fasmart {label} U+{src_cp:04X} empty in "
            f"{os.path.basename(fasmart.path)}"
        )
    return copied[0]


def fasmart_size_caps(fasmart: SourceFont, target_upem: int) -> Tuple[float, float]:
    """Fitted Fasmart ふ ink width and メ ink height (geometric size caps)."""
    fu = _fasmart_fitted_glyph(fasmart, FASMART_FU_CP, target_upem, label="hiragana fu")
    me = _fasmart_fitted_glyph(fasmart, FASMART_ME_CP, target_upem, label="katakana me")
    max_w = _glyph_ink_width(fu)
    max_h = _glyph_ink_height(me)
    if max_w <= 1e-6:
        raise RuntimeError("Fasmart fu ink width is zero")
    if max_h <= 1e-6:
        raise RuntimeError("Fasmart me ink height is zero")
    return max_w, max_h


def cape_cap_to_max_width(
    glyph: TTGlyph,
    advance: int,
    *,
    max_width: float,
    target_upem: int,
    glyph_set: Dict[str, TTGlyph],
) -> Tuple[TTGlyph, int, int]:
    """Geometrically X-condense if ink wider than ``max_width`` (stems scale too)."""
    w = _glyph_ink_width(glyph)
    if w <= max_width + 1e-6:
        try:
            glyph.recalcBounds(None)
            lsb = int(glyph.xMin)
        except Exception:
            lsb = 0
        return glyph, int(advance), lsb
    factor = float(max_width) / w
    baked, adv0, _ = _bake_simple(glyph, int(advance), glyph_set)
    icx, _icy = ideographic_center(target_upem)
    try:
        out, _adv, lsb = widen_ttglyph(
            baked,
            factor,
            advance=float(adv0 if adv0 > 0 else target_upem),
            stem=0.0,
            center_x=icx,
        )
    except Exception as exc:
        print(f"  [!] geometric Width-cap failed: {exc}", file=sys.stderr)
        try:
            baked.recalcBounds(None)
            return baked, int(advance), int(baked.xMin)
        except Exception:
            return baked, int(advance), 0
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        pass
    return out, int(advance if advance > 0 else target_upem), lsb


def cape_cap_to_max_height(
    glyph: TTGlyph,
    advance: int,
    *,
    max_height: float,
    target_upem: int,
    glyph_set: Dict[str, TTGlyph],
) -> Tuple[TTGlyph, int, int]:
    """Geometrically Y-condense if ink taller than ``max_height`` (stems scale too)."""
    h = _glyph_ink_height(glyph)
    if h <= max_height + 1e-6:
        try:
            glyph.recalcBounds(None)
            lsb = int(glyph.xMin)
        except Exception:
            lsb = 0
        return glyph, int(advance), lsb
    factor = float(max_height) / h
    baked, adv0, _ = _bake_simple(glyph, int(advance), glyph_set)
    _icx, icy = ideographic_center(target_upem)
    try:
        out, _adv, lsb = heighten_ttglyph(
            baked,
            factor,
            advance=float(adv0 if adv0 > 0 else target_upem),
            stem=0.0,
            center_y=icy,
        )
    except Exception as exc:
        print(f"  [!] geometric Height-cap failed: {exc}", file=sys.stderr)
        try:
            baked.recalcBounds(None)
            return baked, int(advance), int(baked.xMin)
        except Exception:
            return baked, int(advance), 0
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        pass
    return out, int(advance if advance > 0 else target_upem), lsb


def geometric_cap_to_fasmart(
    glyph: TTGlyph,
    advance: int,
    *,
    max_width: float,
    max_height: float,
    target_upem: int,
) -> Tuple[TTGlyph, int, int]:
    """X/Y geometric condense to Fasmart ふ/メ caps; stroke scales with the squish."""
    tmp_set: Dict[str, TTGlyph] = {".tmp": glyph}
    g, adv, lsb = cape_cap_to_max_width(
        glyph,
        advance,
        max_width=max_width,
        target_upem=target_upem,
        glyph_set=tmp_set,
    )
    tmp_set[".tmp"] = g
    return cape_cap_to_max_height(
        g,
        adv,
        max_height=max_height,
        target_upem=target_upem,
        glyph_set=tmp_set,
    )


def _bake_simple(
    glyph: TTGlyph,
    advance: int,
    glyph_set: Dict[str, TTGlyph],
) -> Tuple[TTGlyph, int, int]:
    """Decompose composites to a plain TT glyph for scale / CAPE Weight."""
    return _bake_transformed_glyph(
        glyph, Transform(), int(advance), glyph_set=glyph_set
    )


def _bottom_center_glyph(
    glyph: TTGlyph,
    target_upem: int,
) -> Tuple[TTGlyph, int]:
    """Pin ink to typo floor and center horizontally; returns ``(glyph, lsb)``."""
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    try:
        glyph.recalcBounds(None)
        x0, y0 = float(glyph.xMin), float(glyph.yMin)
        x1, y1 = float(glyph.xMax), float(glyph.yMax)
        cx = (x0 + x1) / 2.0
        dx = (target_upem / 2.0) - cx
        dy = typo_bot - y0
        rec = RecordingPen()
        glyph.draw(rec, None)
        glyph = apply_transform(rec, Transform(1, 0, 0, 1, dx, dy))
        glyph.recalcBounds(None)
        return glyph, int(glyph.xMin)
    except Exception:
        return glyph, 0


def small_ideo_transform(target_upem: int, size_factor: float) -> Transform:
    """Uniform scale about the full ideographic center (CJK typo box)."""
    s = float(size_factor)
    if abs(s - 1.0) < 1e-9:
        return Transform()
    cx, cy = ideographic_center(target_upem)
    return Transform(s, 0, 0, s, cx * (1.0 - s), cy * (1.0 - s))


def small_floor_pin_dy(target_upem: int, size_factor: float) -> float:
    """Translate so the scaled typo-floor sits on the real typo-floor."""
    s = float(size_factor)
    if abs(s - 1.0) < 1e-9:
        return 0.0
    _cx, cy = ideographic_center(target_upem)
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    return (1.0 - s) * (typo_bot - cy)


def small_ideo_center(
    target_upem: int,
    size_factor: float = SMALL_WIDTH_FACTOR,
) -> Tuple[float, float]:
    """Fixed pivot for all small D4: center of the scaled+floor-pinned ideo box.

    Independent of any glyph contour — same ``(x, y)`` for every kana.
    """
    bot, _top, height = ideographic_bounds(target_upem)
    s = float(size_factor)
    return target_upem / 2.0, bot + s * height / 2.0


def apply_small_floor_pin(
    names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
    size_factor: float = SMALL_WIDTH_FACTOR,
) -> None:
    """Drop glyphs so the scaled ideo box sits on the typo floor (shared ``dy``)."""
    dy = small_floor_pin_dy(target_upem, size_factor)
    if abs(dy) < 1e-6:
        return
    t = Transform(1, 0, 0, 1, 0, dy)
    for name in names:
        g = glyphs.get(name)
        if g is None:
            continue
        try:
            if g.isComposite():
                for c in g.components:
                    c.y = int(round(float(c.y) + dy))
                g.recalcBounds(glyphs)
            else:
                rec = RecordingPen()
                g.draw(rec, None)
                g = apply_transform(rec, t)
                g.recalcBounds(None)
                glyphs[name] = g
            metrics[name] = (metrics[name][0], int(g.xMin))
        except Exception:
            continue


def _outer_contour_area(glyph: TTGlyph) -> float:
    """Signed area of the largest-|area| contour (Y-up shoelace)."""
    try:
        if glyph.isComposite() or glyph.numberOfContours <= 0:
            return 0.0
        coords = glyph.coordinates
        ends = list(glyph.endPtsOfContours)
    except Exception:
        return 0.0
    areas: List[float] = []
    start = 0
    for end in ends:
        pts = [coords[i] for i in range(start, end + 1)]
        start = end + 1
        if len(pts) < 3:
            continue
        a = 0.0
        n = len(pts)
        for i in range(n):
            x1, y1 = float(pts[i][0]), float(pts[i][1])
            x2, y2 = float(pts[(i + 1) % n][0]), float(pts[(i + 1) % n][1])
            a += x1 * y2 - x2 * y1
        areas.append(0.5 * a)
    if not areas:
        return 0.0
    return max(areas, key=abs)


def _ensure_cape_expand_winding(glyph: TTGlyph) -> TTGlyph:
    """Make outer contour CCW so CAPE Weight offsets expand fill.

    CAPE's OffsetCurve uses the right-normal of travel and expands
    outer-CCW / hole-CW. TrueType sources are usually outer-CW, so Weight
    would *thin* them. Reflections (det < 0) already flip winding and were
    the only forms that boldened — normalize everyone to CCW-outer first.
    """
    outer = _outer_contour_area(glyph)
    if outer >= 0:
        return glyph  # already CCW (or empty)
    try:
        rec = RecordingPen()
        glyph.draw(rec, None)
        out = apply_transform(rec, Transform(), reverse_winding=True)
        out.recalcBounds(None)
        return out
    except Exception:
        return glyph


def make_small_glyph(
    full_glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Dict[str, TTGlyph],
    size_factor: float = SMALL_WIDTH_FACTOR,
    weight_factor: float = SMALL_WEIGHT_FACTOR,
    pin_bottom: bool = False,
    final_advance: Optional[int] = None,
) -> Tuple[TTGlyph, int, int]:
    """Ideographic-space scale → optional CAPE Weight (once).

    Scale is about the CJK typo-box center (not contour bbox). D4 variants
    must be derived from this identity via ``add_d4_variant_glyphs`` (ideo
    pivot) — do **not** run make_small on each orientation (that double-boldens
    and re-pins to contour bounds).

    Slice halves: same Weight as bodies; ``pin_bottom=False`` (keep half-planes).
    """
    baked, adv, _ = _bake_simple(full_glyph, advance, glyph_set)
    small = baked
    work_adv = float(adv if adv > 0 else target_upem)

    try:
        t = small_ideo_transform(target_upem, size_factor)
        if t != Transform():
            rec = RecordingPen()
            small.draw(rec, None)
            small = apply_transform(rec, t)
    except Exception as exc:
        print(f"  [!] small ideo scale failed: {exc}", file=sys.stderr)

    if abs(weight_factor - 1.0) > 1e-9:
        try:
            small = _ensure_cape_expand_winding(small)
            small, work_adv, _ = bolden_ttglyph(
                small, weight_factor, advance=float(work_adv)
            )
        except Exception as exc:
            print(f"  [!] CAPE Weight failed: {exc}", file=sys.stderr)

    if pin_bottom:
        small, lsb = _bottom_center_glyph(small, target_upem)
    else:
        try:
            small.recalcBounds(None)
            lsb = int(small.xMin)
        except Exception:
            lsb = 0
    out_adv = int(target_upem if final_advance is None else final_advance)
    return small, out_adv, lsb


def halfwidth_center(
    target_upem: int,
    size_factor: float = 1.0,
) -> Tuple[float, float]:
    """D4 pivot for halfwidth forms: center of the half-em (optionally small) box."""
    half = float(target_upem) * HALF_WIDTH_FACTOR
    bot, _top, height = ideographic_bounds(target_upem)
    s = float(size_factor)
    return half / 2.0, bot + s * height / 2.0


def _fixed_vertical_stem(glyph: TTGlyph, advance: float) -> Optional[float]:
    """Measured vertical stem — the fixed thickness CAPE Width must restore."""
    try:
        layer = layer_from_ttglyph(glyph, float(advance))
        stem = estimate_vertical_stem(layer)
    except Exception:
        return None
    return stem if stem and stem > 0 else None


def _fixed_horizontal_stem(glyph: TTGlyph, advance: float) -> Optional[float]:
    """Measured horizontal stem — the fixed thickness CAPE Height must restore."""
    try:
        layer = layer_from_ttglyph(glyph, float(advance))
        stem = estimate_horizontal_stem(layer)
    except Exception:
        return None
    return stem if stem and stem > 0 else None


def make_halfwidth_r90_glyph(
    src_glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Dict[str, TTGlyph],
    stem: Optional[float] = None,
    size_factor: float = 1.0,
) -> Tuple[TTGlyph, int, int]:
    """Halfwidth r90/r270 source: CAPE Height 0.5, then rotate 90°.

    Upright halfwidth is X-squeezed. Sideways forms must Y-squeeze the
    unsqueezed outline first so after r90 they are wide-and-short (half
    height → half width), not tall-and-narrow.
    """
    baked, adv, _ = _bake_simple(src_glyph, advance, glyph_set)
    half_adv = otRound(target_upem * HALF_WIDTH_FACTOR)
    hw_cx, cy = halfwidth_center(target_upem, size_factor)
    full_cx = float(target_upem) / 2.0
    work_adv = float(adv if adv > 0 else target_upem)
    hstem = stem if stem is not None else _fixed_horizontal_stem(baked, work_adv)
    try:
        baked = _ensure_cape_expand_winding(baked)
        baked, _, _ = heighten_ttglyph(
            baked,
            HALF_WIDTH_FACTOR,
            advance=work_adv,
            stem=hstem,
            center_y=cy,
        )
    except Exception as exc:
        print(f"  [!] CAPE Height halfwidth r90 failed: {exc}", file=sys.stderr)
        try:
            rec = RecordingPen()
            baked.draw(rec, None)
            s = HALF_WIDTH_FACTOR
            baked = apply_transform(rec, Transform(1, 0, 0, s, 0, cy * (1.0 - s)))
        except Exception:
            pass
    rec = RecordingPen()
    baked.draw(rec, None)
    t = variant_transform(
        target_upem,
        rot90_quarters=1,
        flip_x=False,
        flip_y=False,
        center=(full_cx, cy),
    )
    det = t.xx * t.yy - t.xy * t.yx
    baked = apply_transform(rec, t, reverse_winding=det < 0)
    rec = RecordingPen()
    baked.draw(rec, None)
    baked = apply_transform(rec, Transform(1, 0, 0, 1, hw_cx - full_cx, 0))
    try:
        baked.recalcBounds(None)
        lsb = int(baked.xMin)
    except Exception:
        lsb = 0
    return baked, half_adv, lsb


def replace_halfwidth_r90(
    hw_name: str,
    src_glyph: TTGlyph,
    src_advance: int,
    target_upem: int,
    *,
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    glyph_set: Dict[str, TTGlyph],
    size_factor: float = 1.0,
) -> None:
    """Overwrite ``.r90`` (r270 / r90mx / r90my stay composites of it)."""
    r90 = variant_glyph_name(hw_name, "r90")
    if r90 not in glyphs:
        return
    hstem = _fixed_horizontal_stem(src_glyph, float(src_advance))
    g, adv, lsb = make_halfwidth_r90_glyph(
        src_glyph,
        src_advance,
        target_upem,
        glyph_set=glyph_set,
        stem=hstem,
        size_factor=size_factor,
    )
    glyphs[r90] = g
    metrics[r90] = (adv, lsb)


def make_halfwidth_kana_glyph(
    src_glyph: TTGlyph,
    advance: int,
    target_upem: int,
    *,
    glyph_set: Dict[str, TTGlyph],
    stem: Optional[float] = None,
) -> Tuple[TTGlyph, int, int]:
    """Condense to half-em via CAPE Width; keep the source's vertical stem."""
    baked, adv, _ = _bake_simple(src_glyph, advance, glyph_set)
    half_adv = otRound(target_upem * HALF_WIDTH_FACTOR)
    cx = half_adv / 2.0
    work_adv = float(adv if adv > 0 else target_upem)
    vstem = stem if stem is not None else _fixed_vertical_stem(baked, work_adv)
    try:
        baked = _ensure_cape_expand_winding(baked)
        baked, _, _ = widen_ttglyph(
            baked,
            HALF_WIDTH_FACTOR,
            advance=work_adv,
            stem=vstem,
            center_x=cx,
        )
    except Exception as exc:
        print(f"  [!] CAPE Width halfwidth failed: {exc}", file=sys.stderr)
        try:
            rec = RecordingPen()
            baked.draw(rec, None)
            s = HALF_WIDTH_FACTOR
            full_cx = float(target_upem) / 2.0
            baked = apply_transform(rec, Transform(s, 0, 0, 1, cx - s * full_cx, 0))
        except Exception:
            pass
    try:
        baked.recalcBounds(None)
        lsb = int(baked.xMin)
    except Exception:
        lsb = 0
    return baked, half_adv, lsb


def add_small_slice_halves_from_full(
    small_bases: Sequence[str],
    full_bases: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
    modes=None,
) -> int:
    """Downscale full-form slice halves in ideographic space (slice first).

    Same ideo-scale + CAPE Weight as small bodies; no per-half bottom-center
    so top/bot and left/right stay in their half-planes.
    """
    use_modes = list(modes) if modes is not None else list(YI_ORIENTATION_MODES)
    n = 0
    for sm_base, full_base in zip(small_bases, full_bases):
        for _vs, _r, _fx, _fy, suffix in use_modes:
            full_form = (
                full_base if suffix is None else variant_glyph_name(full_base, suffix)
            )
            sm_form = sm_base if suffix is None else variant_glyph_name(sm_base, suffix)
            if full_form not in glyphs or sm_form not in glyphs:
                continue
            for half in HALF_SUFFIXES:
                src = half_glyph_name(full_form, half)
                dst = half_glyph_name(sm_form, half)
                if src not in glyphs or dst in glyphs:
                    continue
                sg, _adv, s_lsb = make_small_glyph(
                    glyphs[src],
                    0,
                    target_upem,
                    glyph_set=glyphs,
                    pin_bottom=False,
                    final_advance=0,
                )
                glyph_order.append(dst)
                glyphs[dst] = sg
                metrics[dst] = (0, s_lsb)
                n += 1
    return n


def form_name_for_orient(base: str, orient: int) -> str:
    _vs, _r, _fx, _fy, suffix = YI_ORIENTATION_MODES[orient]
    if suffix is None:
        return base
    return variant_glyph_name(base, suffix)


def _clamp(v: float, lo: float, hi: float) -> float:
    if lo > hi:
        return (lo + hi) / 2.0
    return max(lo, min(hi, v))


def kana_dakuten_corner_anchors(
    ink: Tuple[float, float, float, float],
    target_upem: int,
    *,
    mark_scale: float = 1.0,
) -> Dict[str, Tuple[int, int]]:
    """Dakuten anchors nestled against ink, inside the ideographic cell.

    MarkToBase pins the mark's matching slot here, so the mark body extends
    *inward* from each edge (TR → left+down, CR → left, TM → down, etc.).
    Place each anchor just outside the ink by ~mark size, then clamp so the
    whole mark footprint stays in the padded CJK cell.

    ``mark_scale`` shrinks the assumed mark footprint (e.g. ``SMALL_WIDTH_FACTOR``
    for small kana so anchors track scaled ``.mk.sm`` marks).
    """
    ix0, iy0, ix1, iy1 = ink
    edge = float(target_upem) * DAKUTEN_EDGE_PAD_FRAC
    cell_l = edge
    cell_r = float(target_upem) - edge
    cell_t = float(target_upem) * TYPO_ASCENDER_FRAC - edge
    cell_b = float(target_upem) * TYPO_DESCENDER_FRAC + edge

    # Mark outline is normalized to this height; treat footprint as ~square.
    mark_h = float(target_upem) * DAKUTEN_MARK_HEIGHT_FRAC * float(mark_scale)
    mark_w = mark_h
    half_w = mark_w * 0.5
    half_h = mark_h * 0.5
    # Small air gap between ink and mark body.
    gap = mark_h * 0.12
    x_mid = (ix0 + ix1) / 2.0
    y_mid = (iy0 + iy1) / 2.0

    def _tr() -> Tuple[int, int]:
        ax = _clamp(ix1 + gap, cell_l + mark_w, cell_r)
        ay = _clamp(iy1 + gap, cell_b + mark_h, cell_t)
        return otRound(ax), otRound(ay)

    def _cr() -> Tuple[int, int]:
        ax = _clamp(ix1 + gap, cell_l + mark_w, cell_r)
        ay = _clamp(y_mid, cell_b + half_h, cell_t - half_h)
        return otRound(ax), otRound(ay)

    def _br() -> Tuple[int, int]:
        ax = _clamp(ix1 + gap, cell_l + mark_w, cell_r)
        ay = _clamp(iy0 - gap, cell_b, cell_t - mark_h)
        return otRound(ax), otRound(ay)

    def _tm() -> Tuple[int, int]:
        ax = _clamp(x_mid, cell_l + half_w, cell_r - half_w)
        ay = _clamp(iy1 + gap, cell_b + mark_h, cell_t)
        return otRound(ax), otRound(ay)

    def _bm() -> Tuple[int, int]:
        ax = _clamp(x_mid, cell_l + half_w, cell_r - half_w)
        ay = _clamp(iy0 - gap, cell_b, cell_t - mark_h)
        return otRound(ax), otRound(ay)

    def _tl() -> Tuple[int, int]:
        ax = _clamp(ix0 - gap, cell_l, cell_r - mark_w)
        ay = _clamp(iy1 + gap, cell_b + mark_h, cell_t)
        return otRound(ax), otRound(ay)

    def _cl() -> Tuple[int, int]:
        ax = _clamp(ix0 - gap, cell_l, cell_r - mark_w)
        ay = _clamp(y_mid, cell_b + half_h, cell_t - half_h)
        return otRound(ax), otRound(ay)

    def _bl() -> Tuple[int, int]:
        ax = _clamp(ix0 - gap, cell_l, cell_r - mark_w)
        ay = _clamp(iy0 - gap, cell_b, cell_t - mark_h)
        return otRound(ax), otRound(ay)

    return {
        "tr": _tr(),
        "cr": _cr(),
        "br": _br(),
        "tm": _tm(),
        "bm": _bm(),
        "tl": _tl(),
        "cl": _cl(),
        "bl": _bl(),
    }


def collect_contour_dakuten_anchors(
    base_names: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_set: Dict[str, TTGlyph],
    target_upem: int,
    mark_scale: float = 1.0,
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    """Per-glyph slot anchors: near ink, inside ideographic cell."""
    anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
    for name in base_names:
        g = glyphs.get(name)
        if g is None:
            continue
        try:
            if g.isComposite():
                g.recalcBounds(glyph_set)
            else:
                g.recalcBounds(None)
            x0, y0 = float(g.xMin), float(g.yMin)
            x1, y1 = float(g.xMax), float(g.yMax)
        except Exception:
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        corners = kana_dakuten_corner_anchors(
            (x0, y0, x1, y1), target_upem, mark_scale=mark_scale
        )
        anchors[name] = {
            i: corners[slot] for i, (slot, _suf) in enumerate(DAKUTEN_SLOTS)
        }
    return anchors


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


def write_css(out_dir: str, codepoints: Sequence[int]) -> None:
    css_path = os.path.join(out_dir, CSS_KANA)
    # PUA D4 bases + FE00/FE01 slice selectors; omit other FE*.
    cps_for_ur = {cp for cp in codepoints if not (0xFE02 <= cp <= 0xFE0F)}
    cps_for_ur |= {0xFE00, 0xFE01}
    ur = unicode_range_css(cps_for_ur)
    lines = [
        "/* Auto-generated single kana font (PUA D4 + smalls + halfwidth + slices) */",
        "",
        "@font-face {",
        f"  font-family: '{FAMILY_NAME}';",
        format_src_line(
            dist_rel("kana", f"{PS_NAME}.woff2"),
            fmt="woff2",
            local=(
                (f"./{PS_NAME}.woff2", "woff2"),
                (f"./{PS_NAME}.ttf", "truetype"),
            ),
            indent="  ",
        ),
        "  font-weight: normal;",
        "  font-style: normal;",
    ]
    if ur:
        lines.append(f"  unicode-range: {ur};")
    lines += [
        "  font-display: swap;",
        "}",
        "",
    ]
    with open(css_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {css_path}")

    fontlist_path = os.path.join(out_dir, f"{PS_NAME}-fontlist.css")
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(
            "/* Kana font family */\n"
            f":root {{\n  --font-edenia-kana: '{FAMILY_NAME}';\n}}\n"
        )
    print(f"Wrote {fontlist_path}")


def build_pankana_font(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    write_ttf: bool = True,
    write_woff2: bool = True,
    hint: bool = True,
) -> Tuple[str, int, List[int]]:
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")

    out_path = os.path.join(out_dir, f"{PS_NAME}.ttf")
    source_cps = chart_source_cps()
    if limit is not None:
        source_cps = source_cps[: max(0, limit)]

    flop_path = resolve_flop_path(in_dir)
    fasmart_paths = resolve_fasmart_family_paths(in_dir)
    mkana_path = resolve_mkana_path(in_dir)
    genseki_path = resolve_genseki_path(in_dir)
    lxgw_paths = resolve_lxgw_family_paths(in_dir)
    print(
        "  fasmart: " + ", ".join(os.path.basename(p) for p in fasmart_paths),
        flush=True,
    )
    print(f"  Flop: {flop_path}", flush=True)
    print(f"  mkanaplus: {mkana_path}", flush=True)
    print(f"  genseki: {genseki_path}", flush=True)
    print(
        "  lxgw: " + ", ".join(os.path.basename(p) for p in lxgw_paths),
        flush=True,
    )

    fasmart = [SourceFont(path) for path in fasmart_paths]
    flop = SourceFont(flop_path)
    mkana = SourceFont(mkana_path)
    genseki = SourceFont(genseki_path)
    lxgw = [SourceFont(path) for path in lxgw_paths]
    fasmart_path_set = {os.path.normcase(os.path.normpath(p)) for p in fasmart_paths}
    max_w, max_h = fasmart_size_caps(fasmart[0], target_upem)
    print(
        f"  Size-cap (non-Fasmart): geometric ≤ Fasmart ふ W {max_w:.1f} / "
        f"メ H {max_h:.1f} (upem {target_upem})",
        flush=True,
    )

    glyph_order = [".notdef"]
    glyphs: Dict[str, TTGlyph] = {".notdef": empty_glyph()}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (target_upem // 2, 0)}
    cmap: Dict[int, str] = {}
    full_bases: List[str] = []
    small_bases: List[str] = []
    hw_full_bases: List[str] = []
    hw_small_bases: List[str] = []
    src_counts: Dict[str, int] = {}

    try:
        print(
            f"  Installing {len(source_cps)} logical kana × {D4_COUNT} D4 "
            f"(no stem-normalize) + smalls "
            f"(ideo-scale {SMALL_WIDTH_FACTOR:g} + Weight once, "
            f"D4 @ post-scale ideo center)...",
            flush=True,
        )
        for logical, src_cp in enumerate(source_cps):
            try:
                src, gname = claim_source_cp(
                    src_cp, fasmart, flop, mkana, genseki, lxgw
                )
            except KeyError as exc:
                print(f"  [!] skip L={logical}: {exc}", file=sys.stderr)
                continue
            tag = os.path.basename(src.path)
            src_counts[tag] = src_counts.get(tag, 0) + 1
            copied = src.copy_fitted(gname, target_upem)
            if copied is None:
                print(
                    f"  [!] skip L={logical} U+{src_cp:04X}: empty copy",
                    file=sys.stderr,
                )
                continue
            sa_glyph, sa_adv, sa_lsb = copied
            if os.path.normcase(os.path.normpath(src.path)) not in fasmart_path_set:
                sa_glyph, sa_adv, sa_lsb = geometric_cap_to_fasmart(
                    sa_glyph,
                    sa_adv,
                    max_width=max_w,
                    max_height=max_h,
                    target_upem=target_upem,
                )
            base = logical_base_name(logical)
            glyph_order.append(base)
            glyphs[base] = sa_glyph
            metrics[base] = (sa_adv, sa_lsb)
            full_bases.append(base)

            add_d4_variant_glyphs(
                base,
                advance=sa_adv,
                lsb=sa_lsb,
                target_upem=target_upem,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                modes=YI_ORIENTATION_MODES,
                anchor="cell",
            )

            # Small: ideo-scale + Weight once, floor-pin, then D4 about the
            # fixed post-scale ideographic center (same for every kana).
            sm_base = small_base_name(logical)
            small_bases.append(sm_base)
            f_adv, f_lsb = metrics[base]
            sg, s_adv, s_lsb = make_small_glyph(
                glyphs[base], f_adv, target_upem, glyph_set=glyphs
            )
            glyph_order.append(sm_base)
            glyphs[sm_base] = sg
            metrics[sm_base] = (s_adv, s_lsb)
            apply_small_floor_pin(
                [sm_base],
                glyphs=glyphs,
                metrics=metrics,
                target_upem=target_upem,
            )
            s_lsb = metrics[sm_base][1]
            add_d4_variant_glyphs(
                sm_base,
                advance=s_adv,
                lsb=s_lsb,
                target_upem=target_upem,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                modes=YI_ORIENTATION_MODES,
                anchor="cell",
                pivot=small_ideo_center(target_upem),
            )
            for orient in range(D4_COUNT):
                fname = form_name_for_orient(base, orient)
                sname = form_name_for_orient(sm_base, orient)
                if fname not in glyphs or sname not in glyphs:
                    continue
                i = pair_index(logical, orient)
                cmap[full_cp(i)] = fname
                cmap[small_cp(i)] = sname

            # Halfwidth: CAPE Width 0.5, stem locked to the full/small identity.
            f_adv, _f_lsb = metrics[base]
            hw_stem = _fixed_vertical_stem(glyphs[base], float(f_adv))
            hw_base = hw_base_name(logical)
            hw_g, hw_adv, hw_lsb = make_halfwidth_kana_glyph(
                glyphs[base],
                f_adv,
                target_upem,
                glyph_set=glyphs,
                stem=hw_stem,
            )
            glyph_order.append(hw_base)
            glyphs[hw_base] = hw_g
            metrics[hw_base] = (hw_adv, hw_lsb)
            hw_full_bases.append(hw_base)
            add_d4_variant_glyphs(
                hw_base,
                advance=hw_adv,
                lsb=hw_lsb,
                target_upem=target_upem,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                modes=YI_ORIENTATION_MODES,
                anchor="cell",
                pivot=halfwidth_center(target_upem),
            )
            replace_halfwidth_r90(
                hw_base,
                glyphs[base],
                f_adv,
                target_upem,
                glyphs=glyphs,
                metrics=metrics,
                glyph_set=glyphs,
            )

            sm_adv, _sm_lsb = metrics[sm_base]
            hw_sm_stem = _fixed_vertical_stem(glyphs[sm_base], float(sm_adv))
            hw_sm = hw_small_base_name(logical)
            hwsg, hws_adv, hws_lsb = make_halfwidth_kana_glyph(
                glyphs[sm_base],
                sm_adv,
                target_upem,
                glyph_set=glyphs,
                stem=hw_sm_stem,
            )
            glyph_order.append(hw_sm)
            glyphs[hw_sm] = hwsg
            metrics[hw_sm] = (hws_adv, hws_lsb)
            hw_small_bases.append(hw_sm)
            add_d4_variant_glyphs(
                hw_sm,
                advance=hws_adv,
                lsb=hws_lsb,
                target_upem=target_upem,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                modes=YI_ORIENTATION_MODES,
                anchor="cell",
                pivot=halfwidth_center(target_upem, SMALL_WIDTH_FACTOR),
            )
            replace_halfwidth_r90(
                hw_sm,
                glyphs[sm_base],
                sm_adv,
                target_upem,
                glyphs=glyphs,
                metrics=metrics,
                glyph_set=glyphs,
                size_factor=SMALL_WIDTH_FACTOR,
            )
            for orient in range(D4_COUNT):
                hfname = form_name_for_orient(hw_base, orient)
                hsname = form_name_for_orient(hw_sm, orient)
                if hfname not in glyphs or hsname not in glyphs:
                    continue
                i = pair_index(logical, orient)
                cmap[hw_full_cp(i)] = hfname
                cmap[hw_small_cp(i)] = hsname

        print(
            "  Sources: "
            + (", ".join(f"{name}={n}" for name, n in src_counts.items()) or "none"),
            flush=True,
        )
        print(
            "  Installing FE00–FE01 slice halves on full forms "
            "(bake id+r90; composite other D4)...",
            flush=True,
        )
        add_slice_halves(
            full_bases,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            modes=YI_ORIENTATION_MODES,
        )
        print(
            "  Small slice halves: ideo-scale + Weight (match bodies; keep half-planes)...",
            flush=True,
        )
        n_sm_halves = add_small_slice_halves_from_full(
            small_bases,
            full_bases,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            modes=YI_ORIENTATION_MODES,
        )
        print(f"  Small slice halves: {n_sm_halves}", flush=True)

        hw_cell = target_upem * HALF_WIDTH_FACTOR
        print(
            f"  Halfwidth slice halves (cell {hw_cell:g}, "
            f"CAPE Width {HALF_WIDTH_FACTOR:g}, fixed stems)...",
            flush=True,
        )
        add_slice_halves(
            hw_full_bases,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            modes=YI_ORIENTATION_MODES,
            cell_width=hw_cell,
            slice_adv_name=SLICE_ADV_HW_NAME,
        )
        add_slice_halves(
            hw_small_bases,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            modes=YI_ORIENTATION_MODES,
            cell_width=hw_cell,
            slice_adv_name=SLICE_ADV_HW_NAME,
        )
        print(
            f"  Halfwidth: {len(hw_full_bases)} full + {len(hw_small_bases)} small "
            f"@ U+{HW_PUA_START:05X}",
            flush=True,
        )

        # Floor-pin halves only (bodies already pinned before D4).
        sm_half_pin: List[str] = []
        for b in small_bases:
            for form in orientation_form_names(b, modes=YI_ORIENTATION_MODES):
                for half in HALF_SUFFIXES:
                    sm_half_pin.append(half_glyph_name(form, half))
        apply_small_floor_pin(
            sm_half_pin,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )
        pivot = small_ideo_center(target_upem)
        print(
            f"  Small D4 pivot (post-scale ideo center): "
            f"({pivot[0]:.1f}, {pivot[1]:.1f})",
            flush=True,
        )

        inject_slice_marks(glyph_order, glyphs, metrics, cmap, modes=KANA_SLICE_MODES)

        mark_names: List[str] = []
        mark_cps: List[int] = []
        base_anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
        try:
            mark_fonts = resolve_dakuten_mark_font_stack(in_dir)
            print(
                f"  Loading dakuten marks from "
                f"{dakuten_mark_stack_label(mark_fonts)}...",
                flush=True,
            )
            mark_cps, mark_glyphs = load_dakuten_marks_from_stack(
                mark_fonts, target_upem
            )
            mark_names = add_dakuten_mark_glyphs(
                mark_cps,
                mark_glyphs,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                cmap=cmap,
            )
            sm_mark_names = add_dakuten_mark_scale_variants(
                mark_cps,
                glyph_order=glyph_order,
                glyphs=glyphs,
                metrics=metrics,
                scale=SMALL_WIDTH_FACTOR,
                weight_factor=SMALL_WEIGHT_FACTOR,
                variant="sm",
            )
            mark_names = list(mark_names) + list(sm_mark_names)

            full_dakuten: List[str] = []
            for b in full_bases:
                full_dakuten.extend(
                    orientation_form_names(b, modes=YI_ORIENTATION_MODES)
                )
            if SLICE_ADV_NAME in glyphs:
                full_dakuten.append(SLICE_ADV_NAME)

            small_dakuten: List[str] = []
            for b in small_bases:
                small_dakuten.extend(
                    orientation_form_names(b, modes=YI_ORIENTATION_MODES)
                )

            base_anchors = collect_contour_dakuten_anchors(
                full_dakuten,
                glyphs=glyphs,
                glyph_set=glyphs,
                target_upem=target_upem,
                mark_scale=1.0,
            )
            base_anchors.update(
                collect_contour_dakuten_anchors(
                    small_dakuten,
                    glyphs=glyphs,
                    glyph_set=glyphs,
                    target_upem=target_upem,
                    mark_scale=SMALL_WIDTH_FACTOR,
                )
            )
            print(
                f"  Dakuten: {len(mark_cps)} marks × {len(DAKUTEN_SLOTS)} contour slots "
                f"(near ink, clamped to ideo cell; .sm @ "
                f"{SMALL_WIDTH_FACTOR:g}), "
                f"{len(base_anchors)} bases",
                flush=True,
            )
        except FileNotFoundError as exc:
            print(f"  Skipping dakuten marks: {exc}", flush=True)

        ascent = otRound(target_upem * TYPO_ASCENDER_FRAC)
        descent = otRound(target_upem * TYPO_DESCENDER_FRAC)

        print(
            f"  Assembling font ({len(glyphs) - 1} glyphs, "
            f"{len(full_bases)} logical)...",
            flush=True,
        )
        fb = FontBuilder(target_upem, isTTF=True)
        fb.setupGlyphOrder(glyph_order)
        fb.setupGlyf(glyphs)
        fb.setupHorizontalMetrics(metrics)
        fb.setupHorizontalHeader(ascent=ascent, descent=descent)
        fb.setupCharacterMap(cmap)
        fb.setupNameTable(
            {
                "familyName": FAMILY_NAME,
                "styleName": "Regular",
                "uniqueFontIdentifier": PS_NAME,
                "fullName": FAMILY_NAME,
                "psName": PS_NAME,
                "version": "Version 1.000",
            }
        )
        fb.setupOS2(
            sTypoAscender=ascent,
            sTypoDescender=descent,
            sTypoLineGap=0,
            usWinAscent=ascent,
            usWinDescent=abs(descent),
            achVendID="pKa ",
        )
        fb.setupPost()

        print("  Compiling GSUB (FE00–FE01 slice)...", flush=True)
        full_forms: List[str] = []
        for b in full_bases:
            full_forms.extend(orientation_form_names(b, modes=YI_ORIENTATION_MODES))
        for b in small_bases:
            full_forms.extend(orientation_form_names(b, modes=YI_ORIENTATION_MODES))
        install_slice_gsub(
            fb.font,
            full_forms,
            glyphs=glyphs,
            glyph_order=glyph_order,
            modes=KANA_SLICE_MODES,
        )
        hw_forms: List[str] = []
        for b in hw_full_bases:
            hw_forms.extend(orientation_form_names(b, modes=YI_ORIENTATION_MODES))
        for b in hw_small_bases:
            hw_forms.extend(orientation_form_names(b, modes=YI_ORIENTATION_MODES))
        if hw_forms:
            print("  Compiling GSUB (FE00–FE01 halfwidth slice)...", flush=True)
            install_slice_gsub(
                fb.font,
                hw_forms,
                glyphs=glyphs,
                glyph_order=glyph_order,
                slice_adv_name=SLICE_ADV_HW_NAME,
                modes=KANA_SLICE_MODES,
            )

        if mark_names and base_anchors:
            # Scaled marks after small bases, then sm / full slot cycles.
            small_forms: List[str] = []
            for b in small_bases:
                small_forms.extend(
                    orientation_form_names(b, modes=YI_ORIENTATION_MODES)
                )
            full_forms_dak: List[str] = []
            for b in full_bases:
                full_forms_dak.extend(
                    orientation_form_names(b, modes=YI_ORIENTATION_MODES)
                )
            if SLICE_ADV_NAME in glyphs:
                full_forms_dak.append(SLICE_ADV_NAME)

            print(
                "  Compiling GSUB (dakuten .mk→.mk.sm after small bases)...",
                flush=True,
            )
            install_dakuten_mark_variant_gsub(
                fb.font,
                mark_cps,
                glyphs=glyphs,
                glyph_order=glyph_order,
                base_names=small_forms,
                variant="sm",
            )
            print(
                f"  Compiling GSUB (dakuten .sm slots {DAKUTEN_SLOT_CYCLE})...",
                flush=True,
            )
            install_dakuten_slot_gsub(
                fb.font,
                mark_cps,
                glyphs=glyphs,
                glyph_order=glyph_order,
                base_names=small_forms,
                variant="sm",
            )
            print(
                f"  Compiling GSUB (dakuten slots {DAKUTEN_SLOT_CYCLE})...",
                flush=True,
            )
            install_dakuten_slot_gsub(
                fb.font,
                mark_cps,
                glyphs=glyphs,
                glyph_order=glyph_order,
                base_names=full_forms_dak,
            )
            print(
                "  Compiling GPOS (dakuten @ contour slots)...",
                flush=True,
            )
            install_dakuten_gpos(
                fb.font,
                base_anchors=base_anchors,
                mark_cps=mark_cps,
                mark_names=mark_names,
                glyph_order=glyph_order,
                glyphs=glyphs,
            )

        os.makedirs(out_dir, exist_ok=True)
        fb.save(out_path)
        from shared_hinting import autohint_ttf

        autohint_ttf(out_path, enabled=hint)
        if write_woff2:
            print("  Compressing WOFF2...", flush=True)
            woff2.compress(out_path, out_path.replace(".ttf", ".woff2"))
        if not write_ttf:
            try:
                os.remove(out_path)
            except OSError:
                pass

        return out_path, len(glyphs) - 1, sorted(cmap.keys())
    finally:
        flop.close()
        mkana.close()
        genseki.close()
        for src in fasmart:
            src.close()
        for src in lxgw:
            src.close()


def build_all(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    write_ttf: bool = True,
    write_woff2: bool = True,
    hint: bool = True,
) -> None:
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    n = (
        len(chart_source_cps())
        if limit is None
        else min(limit, len(chart_source_cps()))
    )
    print(
        f"Kana inventory: {n} logical (row-major chart)" + (" --limit" if limit else "")
    )
    print(
        f"  PUA: U+{PUA_START:04X}..U+{PUA_END:04X} "
        f"(even=full, odd=small; i=L*{D4_COUNT}+o)"
    )
    print(
        f"  Halfwidth SPUA: U+{HW_PUA_START:05X}+ "
        f"(even=full, odd=small; CAPE Width {HALF_WIDTH_FACTOR:g}, fixed stems)"
    )
    print("  D4: 8 orientations mapped to odd/even CPs (no VS umlaut)")
    print(
        f"  Small: ideo-scale {SMALL_WIDTH_FACTOR:g} + Weight {SMALL_WEIGHT_FACTOR:g} "
        f"once; D4 about fixed post-scale ideo center "
        f"{small_ideo_center(DEFAULT_UPEM)}; "
        f"slice full first; halves ideo-scale + floor-pin"
    )
    print(
        f"  Slice: U+{KANA_SLICE_H_CP:04X} (H) / U+{KANA_SLICE_V_CP:04X} (V) "
        f"(em + half-em; not VS)"
    )
    print(
        "  Dakuten: contour GPOS (near ink, inside ideo cell; "
        f"{DAKUTEN_SLOT_CYCLE}; CGJ U+034F skips a slot)"
    )
    print(f"  Output: single font '{FAMILY_NAME}'")
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"  Formats: {fmt_note}")

    os.makedirs(out_dir, exist_ok=True)
    path, count, cps = build_pankana_font(
        in_dir,
        out_dir,
        target_upem,
        limit=limit,
        write_ttf=write_ttf,
        write_woff2=write_woff2,
        hint=hint,
    )
    if count:
        write_css(out_dir, cps)
    print(f"\nDone: {path} ({count} glyphs)", flush=True)
    sync_dist_to_plugin("kana", out_dir)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build edenia kana (PUA D4 + smalls + halfwidth + FE00/FE01 slices + dakuten)"
    )
    p.add_argument("--in", dest="in_dir", default=IN_DIR)
    p.add_argument("--out", dest="out_dir", default=OUT_DIR)
    p.add_argument("--upem", type=int, default=DEFAULT_UPEM)
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Use only the first N logical chart cells (smoke test)",
    )
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument(
        "--ttf-only",
        "--no-woff2",
        action="store_true",
        help="Write TTF only (skip WOFF2); --no-woff2 is an alias",
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
    build_all(
        args.in_dir,
        args.out_dir,
        args.upem,
        limit=args.limit,
        write_ttf=not args.woff2_only,
        write_woff2=not args.ttf_only,
        hint=not args.no_hint,
    )
