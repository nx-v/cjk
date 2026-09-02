#!/usr/bin/env python3
"""
Build `edenia kana` (PUA D4 cmap + smalls + dakuten) and pigeonholed segment
faces (`h` / `t` / `qv` / `qh` / optional `q`), matching CJK.

Encoding
--------
BMP PUA `U+E000`..`U+F8FF` (full/small, 6400 CPs)::

    i        = L * 8 + o          # L = 0..219, o = 0..7 (D4_MODES order)
    full[i]  = 0xE000 + 2 * i     # even — full-size oriented form
    small[i] = 0xE000 + 2 * i + 1 # odd  — small: ideo-scale + Weight once, D4 @ ideo

Halfwidth companions (same `i`) in Supplementary PUA-A::

    hw_full[i]  = 0xF0000 + 2 * i
    hw_small[i] = 0xF0000 + 2 * i + 1

CAPE Width `0.5` holds the pre-squeeze stem thicknesses (match full-width
kana). Combining slices use the half-em cell (FE08–FE0F) plus FE00 overlay.

Chart (18 consonant rows × 6 vowels, then trailing marks)
---------------------------------------------------------
Row-major into logical `L` (hiragana block, then katakana block). Each cell
holds one **source** code point (Flop / mkanaplus PUA / GenSeki / LXGW); the
built face maps it to PUA `full` / `small` (and halfwidth companions).

Columns (`VOWELS`)::  a · i · u · e · o · ə

Rows (`CONSONANTS` — same order in `HIRAGANA_ROWS` and `KATAKANA_ROWS`)::

     0  ∅     vowel-only (あア …)
     1  k
     2  ng
     3  t
     4  ts
     5  ch
     6  sh
     7  s
     8  m
     9  n
    10  h
    11  y
    12  l
    13  r
    14  w
    15  f
    16  p
    17  ny

Logical indices::

    L 0..107     hiragana 18×6
    L 108..109   hiragana length · gemination
    L 110..217   katakana 18×6
    L 218..219   katakana length · gemination

(220 logical cells total; `i = L * 8 + o` with `L = 0..219`, `o = 0..7`.)

Source priority per cell: FlopDesignFONT, then mkanaplus (PUA/archaic +
overrides), then GenSeki Hentaigana, then LXGW (Clear Gothic / XiHei), then
Plangothic P1, then Sans Serif Collection, then Segoe UI Historic (last
resort). Glyphs from sources other than Flop / mkana that are smaller than the
average Flop kana ink size are stretched up on X and/or Y to that average;
strokes are thinned to compensate (CAPE restores pre-stretch stem weight).
Axes already at or above the average are left as-is.

Trailing marks (all D4)::

    hiragana  length U+301C 〜 · gemination U+309D ゝ
    katakana  length U+30FC ー · gemination U+30FD ヽ

Umlaut orientations are real cmap entries (no VS). Combining slices use
FE00 overlay + FE08–FE0F (halves and triangles). Dakuten GPOS uses eight
unique slots just outside each form's ink (after D4), including halfwidth.
"""

from __future__ import annotations

import argparse
import os
import pickle
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Optional, Sequence, Set, Tuple

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph

from cape_weightor import (
    bolden_ttglyph,
    estimate_horizontal_stem,
    estimate_vertical_stem,
    heighten_ttglyph,
    layer_from_ttglyph,
    widen_ttglyph,
)
from cdn_fonts import dist_rel, format_src_line
from edenia_names import (
    CSS_KANA,
    FAMILY_KANA,
    PS_KANA,
    SEGMENT_FACE_BUILD_ORDER,
    SEGMENT_FACE_CSS_ORDER,
    add_cjk_variant_arguments,
    bucket_face_id,
    family_kana_variant,
    h_bucket_face_id,
    parse_bucket_face_id,
    parse_h_bucket_face_id,
    ps_kana,
    resolve_kana_yi_variants,
)
from hangul_diacritics import (
    DAKUTEN_SLOT_CYCLE,
    DAKUTEN_SLOTS,
    add_dakuten_chain_mark_glyphs,
    add_dakuten_mark_glyphs,
    dakuten_mark_stack_label,
    install_dakuten_chain_gsub,
    install_dakuten_gpos,
    install_dakuten_mark_chain_gpos,
    install_dakuten_slot_gsub,
    is_dakuten_chain_glyph,
    load_dakuten_marks_from_stack,
    resolve_dakuten_mark_font_stack,
)
from kana_yi_diacritics import (
    collect_kana_dakuten_anchors,
    inherit_kana_dakuten_anchors,
    kana_coord_liga_names,
    kana_dakuten_placement_stems,
    kana_mark_center_anchor,
    kana_mark_chain_parent_anchor,
    kana_representative_mark_points,
)
from kana_yi_slice import (
    SLICE_SUFFIXES,
    add_slice_halves,
    half_glyph_name,
    inject_slice_marks,
    install_slice_gsub,
)
from segment_faces import (
    filter_segment_face_cmap,
    install_segment_face_gsub,
    keep_names_for_segment_face,
    oriented_forms,
    subset_tables,
)
from shared_cells import (
    DEFAULT_UPEM,
    QUARTER_FACE_GRID,
    QUARTER_FACE_H,
    QUARTER_FACE_V,
    TTF_GLYPH_LIMIT,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    YI_ORIENTATION_MODES,
    _bake_transformed_glyph,  # composite → plain outlines
    add_d4_variant_glyphs,
    add_overlay_forms,
    apply_transform,
    empty_glyph,
    fit_glyph_to_ideographic_cell,
    ideographic_bounds,
    ideographic_center,
    orientation_form_names,
    prepare_quarter_cells,
    prepare_third_cells,
    rebuild_sideways_from_r90,
    subset_glyph_tables,
    variant_glyph_name,
    variant_transform,
)
from shared_font_builder import load_ttfont, setup_head_timestamps
from shared_hinting import add_jobs_argument, add_no_hint_argument, finish_font_outputs
from sync_edenian_fonts import sync_dist_to_plugin

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
IN_DIR = os.path.join(SCRIPT_DIR, "src")
OUT_DIR = os.path.join(SCRIPT_DIR, "dist", "kana")

FAMILY_NAME = FAMILY_KANA
PS_NAME = PS_KANA

PUA_START = 0xE000
PUA_END = 0xF8FF  # inclusive; 6400 CPs
D4_COUNT = 8
SMALL_WIDTH_FACTOR = 0.75
# After uniform scale, CAPE Weight restores stroke thickness to match full-size.
SMALL_WEIGHT_FACTOR = 1.0 / SMALL_WIDTH_FACTOR
# Halfwidth: CAPE Width 0.5, stems held at the pre-squeeze (fixed) values.
HALF_WIDTH_FACTOR = 0.5
HW_PUA_START = 0xF0000  # Supplementary Private Use Area-A
HW_PUA_LAST = 0xFFFFD  # last assigned SPUA-A code point
# FlopDesignFONT is CFF (cubics). TrueType glyf needs quads.
CU2QU_MAX_ERR = 0.5

FLOP_FILENAMES: Tuple[str, ...] = (
    "FlopDesignFONT.otf",
    "FlopDesignFONT.ttf",
    "FlopDesignFont.otf",
)
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
PLANGOTHIC_P1_FILENAMES: Tuple[str, ...] = ("PlangothicP1-Regular.ttf",)
SANS_SERIF_COLLECTION_FILENAMES: Tuple[str, ...] = (
    "SansSerifCollection.ttf",
    "sansserifcollection.ttf",
)
SEGOE_UI_HISTORIC_FILENAMES: Tuple[str, ...] = (
    "seguihis.ttf",
    "SegoeUIHistoric.ttf",
    "SEGUIHIS.TTF",
)

# Source-shape overrides: always claim from mkanaplus when present.
MKANA_OVERRIDE_CHARS: frozenset[str] = frozenset(
    {
        "た",
        "な",
        "に",
        "こ",
        "ゑ",
        "く",
        "へ",
        "ア",
        "ソ",
        "ル",
        "ワ",
    }
)
MKANA_OVERRIDE_CPS: frozenset[int] = frozenset(map(ord, MKANA_OVERRIDE_CHARS))

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
    "ny",
)
VOWELS: Tuple[str, ...] = ("a", "i", "u", "e", "o", "ə")

# 18×6 hiragana, then 18×6 katakana (row-major). Values = source chars.
HIRAGANA_ROWS: Tuple[Tuple[str, ...], ...] = (
    ("あ", "い", "う", "え", "お", "ᜀ"),  # ∅
    ("か", "き", "く", "け", "こ", "ᜣ"),  # k
    ("", "", "", "", "", "ᜥ"),  # ng
    ("た", "", "𛁭", "て", "と", "ᜆ"),  # t
    ("", "", "つ", "", "", "𛁪"),  # ts
    ("", "ち", "", "", "", "𛁣"),  # ch
    ("𛁃", "", "", "", "", "ᜰ"),  # sh
    ("さ", "", "す", "せ", "そ", "ᜐ"),  # s
    ("ま", "み", "む", "め", "も", "ᜋ"),  # m
    ("な", "", "ぬ", "ね", "の", "ん"),  # n
    ("は", "ひ", "𛀹", "へ", "ほ", "ᜊ"),  # h
    ("や", "𛀆", "ゆ", "𛀁", "よ", "ᝬ"),  # y
    ("", "", "", "", "", "ᜮ"),  # l
    ("ら", "り", "る", "れ", "ろ", "ᜍ"),  # r
    ("わ", "ゐ", "𛄟", "ゑ", "を", "ᝏ"),  # w
    ("𛂦", "𛂫", "ふ", "𛂸", "𛂿", ""),  # f
    ("", "", "", "", "", "ᜉ"),  # p
    ("𛂁", "𛂊", "𛂙", "𛂔", "𛂜", "𑂖"),  # ny
)

KATAKANA_ROWS: Tuple[Tuple[str, ...], ...] = (
    ("ア", "イ", "ウ", "エ", "オ", "ㆾ"),  # ∅
    ("カ", "", "ク", "ケ", "コ", "ㄎ"),  # k
    ("", "", "", "", "", "ㄫ"),  # ng
    ("タ", "", "", "テ", "ト", "ㄉ"),  # t
    ("", "", "ツ", "", "", "ㄘ"),  # ts
    ("", "", "", "", "", "ㄔ"),  # ch
    ("", "", "", "", "", "ㄕ"),  # sh
    ("サ", "", "ス", "セ", "ソ", ""),  # s
    ("マ", "", "ム", "メ", "モ", ""),  # m
    ("ナ", "ニ", "ヌ", "ネ", "ノ", ""),  # n
    ("ハ", "ヒ", "", "ヘ", "ホ", "ㄏ"),  # h
    ("ヤ", "𛄠", "ユ", "𛄡", "ヨ", ""),  # y
    ("", "", "", "", "", "ㄌ"),  # l
    ("ラ", "リ", "ル", "レ", "", ""),  # r
    ("ワ", "ヰ", "𛄢", "ヱ", "ヲ", ""),  # w
    ("", "", "", "", "", ""),  # f
    ("", "", "", "", "", "ㄅ"),  # p
    ("", "", "", "", "", "ㄬ"),  # ny
)

# After each script's last phonetic cell: length, then gemination.
SCRIPT_TRAILING: Tuple[Tuple[str, str], ...] = (
    ("length", "〜"),
    ("gemination", "ゝ"),
)
KATAKANA_TRAILING: Tuple[Tuple[str, str], ...] = (
    ("length", "ー"),
    ("gemination", "ヽ"),
)
SCRIPT_TRAILING_COUNT = len(SCRIPT_TRAILING)

# Phonetic chart only (no trailing marks).
CHART_ROWS: Tuple[Tuple[str, ...], ...] = HIRAGANA_ROWS + KATAKANA_ROWS
HIRAGANA_PHONETIC_COUNT = sum(len(r) for r in HIRAGANA_ROWS)
KATAKANA_PHONETIC_COUNT = sum(len(r) for r in KATAKANA_ROWS)
HIRAGANA_COUNT = HIRAGANA_PHONETIC_COUNT + SCRIPT_TRAILING_COUNT
KATAKANA_COUNT = KATAKANA_PHONETIC_COUNT + SCRIPT_TRAILING_COUNT


def chart_cp(ch: str) -> int:
    """Single-character chart cell → Unicode scalar."""
    if len(ch) != 1:
        raise ValueError(f"chart cell must be one code point, got {ch!r}")
    return ord(ch)


def chart_source_cps() -> List[int]:
    """Row-major source CPs: hiragana (+marks) then katakana (+marks)."""
    out: List[int] = []
    for row in HIRAGANA_ROWS:
        out.extend(chart_cp(c) for c in row)
    out.extend(chart_cp(c) for _lab, c in SCRIPT_TRAILING)
    for row in KATAKANA_ROWS:
        out.extend(chart_cp(c) for c in row)
    out.extend(chart_cp(c) for _lab, c in KATAKANA_TRAILING)
    return out


def validate_chart_tables() -> None:
    """Raise if row tables or source CP inventory are inconsistent."""
    n_cons = len(CONSONANTS)
    n_vow = len(VOWELS)
    if len(HIRAGANA_ROWS) != n_cons:
        raise ValueError(
            f"HIRAGANA_ROWS ({len(HIRAGANA_ROWS)}) must match CONSONANTS ({n_cons})"
        )
    if len(KATAKANA_ROWS) != n_cons:
        raise ValueError(
            f"KATAKANA_ROWS ({len(KATAKANA_ROWS)}) must match CONSONANTS ({n_cons})"
        )
    for label, rows in (("HIRAGANA", HIRAGANA_ROWS), ("KATAKANA", KATAKANA_ROWS)):
        for ri, row in enumerate(rows):
            if len(row) != n_vow:
                raise ValueError(
                    f"{label}_ROWS[{ri}] has {len(row)} cells; expected {n_vow}"
                )
    seen: Dict[int, int] = {}
    for logical, cp in enumerate(chart_source_cps()):
        if cp in seen:
            prev = seen[cp]
            raise ValueError(
                f"Duplicate chart source U+{cp:04X} at L={logical} and L={prev}"
            )
        seen[cp] = logical


def trailing_mark_label(logical: int) -> Optional[str]:
    """`length` / `gemination` if `logical` is a script trailer, else None."""
    if HIRAGANA_PHONETIC_COUNT <= logical < HIRAGANA_COUNT:
        return SCRIPT_TRAILING[logical - HIRAGANA_PHONETIC_COUNT][0]
    kata0 = HIRAGANA_COUNT
    if kata0 + KATAKANA_PHONETIC_COUNT <= logical < kata0 + KATAKANA_COUNT:
        return KATAKANA_TRAILING[logical - kata0 - KATAKANA_PHONETIC_COUNT][0]
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


def resolve_flop_family_paths(in_dir: str) -> List[str]:
    """FlopDesignFONT faces. Prefer Scripts/src per name."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    found: List[str] = []
    seen: set[str] = set()
    for name in FLOP_FILENAMES:
        path = _first_existing(
            (
                os.path.join(src_dir, name),
                os.path.join(in_dir, name),
                os.path.join(REPO_ROOT, "CJK", name),
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
            f"FlopDesignFONT not found under "
            f"Scripts/src / {in_dir!r} / CJK / repo root"
        )
    return found


def resolve_mkana_path(in_dir: str) -> str:
    """Prefer Scripts/src (mkanaplus lives there), then in_dir / CJK / repo."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    candidates: List[str] = []
    # All names under Scripts/src first (user inventory lives there).
    for name in MKANA_FILENAMES:
        candidates.append(os.path.join(src_dir, name))
    for name in MKANA_FILENAMES:
        candidates.append(os.path.join(in_dir, name))
        candidates.append(os.path.join(REPO_ROOT, "CJK", name))
        candidates.append(os.path.join(REPO_ROOT, "Kana", name))
        candidates.append(os.path.join(REPO_ROOT, name))
    found = _first_existing(candidates)
    if found is None:
        raise FileNotFoundError(
            f"mkanaplus not found under Scripts/src / {in_dir!r} / CJK / repo root"
        )
    return found


def resolve_genseki_path(in_dir: str) -> str:
    """Prefer Scripts/src, then in_dir / CJK / repo root."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    candidates: List[str] = []
    for name in GENSEKI_FILENAMES:
        candidates.append(os.path.join(src_dir, name))
    for name in GENSEKI_FILENAMES:
        candidates.append(os.path.join(in_dir, name))
        candidates.append(os.path.join(REPO_ROOT, "CJK", name))
        candidates.append(os.path.join(REPO_ROOT, name))
    found = _first_existing(candidates)
    if found is None:
        raise FileNotFoundError(
            f"GenSeki Hentaigana not found under Scripts/src / {in_dir!r} / CJK / repo root"
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
                os.path.join(REPO_ROOT, "CJK", "LXGW", name),
                os.path.join(REPO_ROOT, "LXGW", name),
                os.path.join(REPO_ROOT, "CJK", name),
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
            f"Scripts/src / {in_dir!r} / CJK/LXGW / repo root"
        )
    return found


def resolve_plangothic_p1_path(in_dir: str) -> Optional[str]:
    """Last-resort kana outline source. Prefer Scripts/src; optional if missing."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    candidates: List[str] = []
    for name in PLANGOTHIC_P1_FILENAMES:
        candidates.append(os.path.join(src_dir, name))
    for name in PLANGOTHIC_P1_FILENAMES:
        candidates.append(os.path.join(in_dir, name))
        candidates.append(os.path.join(REPO_ROOT, "CJK", name))
        candidates.append(os.path.join(REPO_ROOT, name))
    return _first_existing(candidates)


def resolve_sans_serif_collection_path(in_dir: str) -> Optional[str]:
    """Final kana outline fallback. Prefer Scripts/src; optional if missing."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    candidates: List[str] = []
    for name in SANS_SERIF_COLLECTION_FILENAMES:
        candidates.append(os.path.join(src_dir, name))
    for name in SANS_SERIF_COLLECTION_FILENAMES:
        candidates.append(os.path.join(in_dir, name))
        candidates.append(os.path.join(REPO_ROOT, "CJK", name))
        candidates.append(os.path.join(REPO_ROOT, name))
    return _first_existing(candidates)


def resolve_segoe_ui_historic_path(in_dir: str) -> Optional[str]:
    """Ultimate kana outline fallback. Prefer Scripts/src; optional if missing."""
    src_dir = os.path.join(SCRIPT_DIR, "src")
    candidates: List[str] = []
    for name in SEGOE_UI_HISTORIC_FILENAMES:
        candidates.append(os.path.join(src_dir, name))
    for name in SEGOE_UI_HISTORIC_FILENAMES:
        candidates.append(os.path.join(in_dir, name))
        candidates.append(os.path.join(REPO_ROOT, "CJK", name))
        candidates.append(os.path.join(REPO_ROOT, name))
        candidates.append(
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        )
    return _first_existing(candidates)


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
        self.tt = load_ttfont(path, fontNumber=0)
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
    flop: Sequence[SourceFont],
    mkana: SourceFont,
    genseki: SourceFont,
    lxgw: Sequence[SourceFont],
    plangothic: Sequence[SourceFont] = (),
    sans_serif: Sequence[SourceFont] = (),
    segoe_historic: Sequence[SourceFont] = (),
) -> Tuple[SourceFont, str]:
    """Return (source, glyph_name) for a chart source CP."""
    if src_cp in MKANA_OVERRIDE_CPS:
        head: Tuple[SourceFont, ...] = (mkana, *flop, genseki)
    else:
        head = (*flop, mkana, genseki)
    for src in (*head, *lxgw, *plangothic, *sans_serif, *segoe_historic):
        gname = src.cmap.get(src_cp)
        if gname is None:
            continue
        if is_empty_outline(src.tt, gname):
            continue
        return src, gname
    raise KeyError(
        f"No outline for U+{src_cp:04X} in "
        f"Flop/mkanaplus/genseki/lxgw/plangothic/sans-serif/seguihis"
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


def flop_average_ink_size(
    flop: Sequence[SourceFont],
    target_upem: int,
    source_cps: Sequence[int],
) -> Tuple[float, float]:
    """Mean Flop kana ink width / height over chart CPs present in Flop."""
    widths: List[float] = []
    heights: List[float] = []
    for cp in source_cps:
        for src in flop:
            gname = src.cmap.get(cp)
            if gname is None or is_empty_outline(src.tt, gname):
                continue
            copied = src.copy_fitted(gname, target_upem)
            if copied is None:
                break
            g = copied[0]
            w = _glyph_ink_width(g)
            h = _glyph_ink_height(g)
            if w > 1.0:
                widths.append(w)
            if h > 1.0:
                heights.append(h)
            break
    if not widths or not heights:
        raise RuntimeError(
            "Flop average ink size: no usable chart outlines "
            f"(W={len(widths)} H={len(heights)})"
        )
    return sum(widths) / len(widths), sum(heights) / len(heights)


def stretch_to_flop_average(
    glyph: TTGlyph,
    advance: int,
    *,
    avg_width: float,
    avg_height: float,
    target_upem: int,
    glyph_set: Dict[str, TTGlyph],
) -> Tuple[TTGlyph, int, int]:
    """Grow undersized axes to Flop averages; larger axes stay as-is.

    Growing thickens strokes geometrically, so CAPE Width/Height restore the
    pre-stretch stem (slightly thinner relative to the new size). If a stem
    cannot be measured, fall back to Weight lighten by `1/scale`.
    """
    baked, adv0, lsb = _bake_simple(glyph, int(advance), glyph_set)
    work_adv = float(adv0 if adv0 > 0 else target_upem)
    w = _glyph_ink_width(baked)
    h = _glyph_ink_height(baked)
    if w <= 1e-6 or h <= 1e-6:
        return baked, int(advance), lsb

    # Only enlarge undersized axes; never shrink.
    sx = float(avg_width) / w if w < float(avg_width) else 1.0
    sy = float(avg_height) / h if h < float(avg_height) else 1.0
    if abs(sx - 1.0) < 1e-3 and abs(sy - 1.0) < 1e-3:
        return baked, int(advance), lsb

    icx, icy = ideographic_center(target_upem)
    try:
        baked = _ensure_cape_expand_winding(baked)
        if abs(sx - 1.0) >= 1e-3:
            vstem = _fixed_vertical_stem(baked, work_adv)
            baked, _, lsb = widen_ttglyph(
                baked,
                sx,
                advance=work_adv,
                stem=vstem,
                center_x=icx,
            )
            # No stem → CAPE skips offset; lighten so the grow isn't bolder.
            if vstem is None and sx > 1.0:
                baked = _ensure_cape_expand_winding(baked)
                baked, _, lsb = bolden_ttglyph(baked, 1.0 / sx, advance=work_adv)
        if abs(sy - 1.0) >= 1e-3:
            # Remeasure height after Width (stem restore can nudge bbox).
            h2 = _glyph_ink_height(baked)
            if h2 > 1e-6 and h2 < float(avg_height):
                sy = float(avg_height) / h2
            else:
                sy = 1.0
            if abs(sy - 1.0) >= 1e-3:
                hstem = _fixed_horizontal_stem(baked, work_adv)
                baked, _, lsb = heighten_ttglyph(
                    baked,
                    sy,
                    advance=work_adv,
                    stem=hstem,
                    center_y=icy,
                )
                if hstem is None and sy > 1.0:
                    baked = _ensure_cape_expand_winding(baked)
                    baked, _, lsb = bolden_ttglyph(baked, 1.0 / sy, advance=work_adv)
    except Exception as exc:
        print(f"  [!] stretch-to-Flop-average failed: {exc}", file=sys.stderr)
        try:
            baked.recalcBounds(None)
            return baked, int(advance), int(baked.xMin)
        except Exception:
            return baked, int(advance), 0

    try:
        baked.recalcBounds(None)
        lsb = int(baked.xMin)
    except Exception:
        pass
    return baked, int(advance if advance > 0 else target_upem), lsb


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
    """Pin ink to typo floor and center horizontally; returns `(glyph, lsb)`."""
    typo_bot = target_upem * TYPO_DESCENDER_FRAC
    try:
        glyph.recalcBounds(None)
        x0, y0 = float(glyph.xMin), float(glyph.yMin)
        x1 = float(glyph.xMax)
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

    Independent of any glyph contour — same `(x, y)` for every kana.
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
    """Drop glyphs so the scaled ideo box sits on the typo floor (shared `dy`)."""
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
    must be derived from this identity via `add_d4_variant_glyphs` (ideo
    pivot) — do **not** run make_small on each orientation (that double-boldens
    and re-pins to contour bounds).

    Slice halves: same Weight as bodies; `pin_bottom=False` (keep half-planes).
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

    Upright halfwidth is X-squeezed (narrow + tall, advance ½em). Rotating
    that outline makes it ~1em wide in a ½em advance (overlap). Y-squeeze
    the unsqueezed source first so after r90 the glyph stays ½em wide.
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
    glyph_order: Optional[List[str]] = None,
    size_factor: float = 1.0,
    pivot: Optional[Tuple[float, float]] = None,
) -> None:
    """Install Y-squeezed `.r90` and re-bake r270 / r90mx / r90my from it.

    D4 sideways forms are baked outlines, not live composites — replacing
    `.r90` alone leaves the others as a 90° turn of the X-squeezed identity.
    """
    r90 = variant_glyph_name(hw_name, "r90")
    if r90 not in glyphs:
        if glyph_order is None:
            return
        glyph_order.append(r90)
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
    rebuild_sideways_from_r90(
        hw_name,
        target_upem=target_upem,
        glyphs=glyphs,
        metrics=metrics,
        pivot=(
            pivot if pivot is not None else halfwidth_center(target_upem, size_factor)
        ),
        modes=YI_ORIENTATION_MODES,
    )


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
            for half in SLICE_SUFFIXES:
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
                    final_advance=target_upem,
                )
                glyph_order.append(dst)
                glyphs[dst] = sg
                metrics[dst] = (int(target_upem), s_lsb)
                n += 1
    return n


def form_name_for_orient(base: str, orient: int) -> str:
    _vs, _r, _fx, _fy, suffix = YI_ORIENTATION_MODES[orient]
    if suffix is None:
        return base
    return variant_glyph_name(base, suffix)


def _kana_bases_in_bucket(
    full_bases: Sequence[str],
    cmap: Dict[int, str],
    bucket_id: int,
) -> List[str]:
    """Logical kana bases with any D4 form encoded on `bucket_id` page."""
    name_to_cp = {name: cp for cp, name in cmap.items()}
    out: List[str] = []
    for base in full_bases:
        for form in orientation_form_names(base, modes=YI_ORIENTATION_MODES):
            cp = name_to_cp.get(form)
            if cp is not None and (cp >> 8) == bucket_id:
                out.append(base)
                break
    return out


def _kana_bucket_has_bases(
    *,
    full_bases: Sequence[str],
    hw_full_bases: Sequence[str],
    cmap: Dict[int, str],
    bucket_id: int,
) -> bool:
    """True if this page has fullwidth and/or halfwidth kana stems."""
    return bool(
        _kana_bases_in_bucket(full_bases, cmap, bucket_id)
        or _kana_bases_in_bucket(hw_full_bases, cmap, bucket_id)
    )


def _kana_pigeonhole_pages(cmap: Dict[int, str]) -> List[int]:
    return sorted(
        {
            cp >> 8
            for cp in cmap
            if (PUA_START <= cp <= PUA_END) or (HW_PUA_START <= cp <= HW_PUA_LAST)
        }
    )


def _prepare_kana_segment_glyphs(
    *,
    full_bases: Sequence[str],
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    target_upem: int,
    variants: Set[str],
) -> None:
    """Bake third / quarter clips for ``full_bases`` (typically one bucket)."""
    if "t" in variants:
        prepare_third_cells(
            cjk_bases=full_bases,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
            target_upem=target_upem,
        )
    for face, key in (
        (QUARTER_FACE_GRID, "q"),
        (QUARTER_FACE_V, "qv"),
        (QUARTER_FACE_H, "qh"),
    ):
        if key not in variants:
            continue
        prepare_quarter_cells(
            face=face,
            cjk_bases=full_bases,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            cmap=cmap,
            target_upem=target_upem,
        )


def _save_kana_segment_face(
    *,
    face_id: str,
    variant: str,
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    bases: Sequence[str],
    out_dir: str,
    target_upem: int,
) -> Tuple[str, str, int, List[int]]:
    family = family_kana_variant(variant)
    ps = ps_kana(face_id)
    out_path = os.path.join(out_dir, f"{face_id}.ttf")
    n_glyphs = len(glyphs)
    print(
        f"  Assembling {family} / {face_id} "
        f"({n_glyphs - 1} glyphs, {len(bases)} bases)...",
        flush=True,
    )
    fb = FontBuilder(target_upem, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(
        ascent=otRound(target_upem * TYPO_ASCENDER_FRAC),
        descent=otRound(target_upem * TYPO_DESCENDER_FRAC),
    )
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
        sTypoAscender=otRound(target_upem * TYPO_ASCENDER_FRAC),
        sTypoDescender=otRound(target_upem * TYPO_DESCENDER_FRAC),
        sTypoLineGap=0,
        usWinAscent=otRound(target_upem * TYPO_ASCENDER_FRAC),
        usWinDescent=abs(otRound(target_upem * TYPO_DESCENDER_FRAC)),
        achVendID="pKa ",
    )
    fb.setupPost()
    slice_forms = oriented_forms(bases, glyphs) if variant == "q" else []
    print(f"  Compiling GSUB ({variant} segment VS)...", flush=True)
    install_segment_face_gsub(
        fb.font,
        variant=variant,
        bases=bases,
        glyphs=glyphs,
        glyph_order=glyph_order,
        slice_gsub_fn=install_slice_gsub,
        slice_forms=slice_forms,
    )
    os.makedirs(out_dir, exist_ok=True)
    setup_head_timestamps(fb)
    fb.save(out_path)
    return face_id, variant, n_glyphs - 1, sorted(cmap.keys())


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


KANA_H_FE = {0xFE00} | set(range(0xFE08, 0xFE10))
KANA_T_VS = {0xFE00} | set(range(0xE0100, 0xE010A))
KANA_QV_VS = {0xFE00, 0xFE08, 0xFE09} | set(range(0xE010A, 0xE0111))
KANA_QH_VS = {0xFE00, 0xFE0A, 0xFE0B} | set(range(0xE0111, 0xE0118))
# Grid VS only — do not claim FE08–FE0F (those are h / qv / qh half bands).
KANA_Q_VS = {0xFE00} | set(range(0xE0118, 0xE0120))


def _css_cps_for_kana_face(
    codepoints: Sequence[int], variant: str, *, mark_cps: Sequence[int]
) -> List[int]:
    """CSS unicode-range CPs for one kana face.

    Only ``h`` and the base face bake dakuten. Each ``h`` pigeonhole must
    cmap *and* claim ``mark_cps``: the last slice of a digraph often lives
    on another 256-CP ``h`` file, and ``base+FE09+marks`` has to shape there.
    q/qv/qh/t must not claim marks (they sort earlier and lack mark glyphs).
    """
    cps = {cp for cp in codepoints if not (0xFE00 <= cp <= 0xFE0F)}
    if variant == "h":
        cps |= KANA_H_FE
        cps |= set(mark_cps)
    elif variant == "t":
        cps |= KANA_T_VS
    elif variant == "qv":
        cps |= KANA_QV_VS
    elif variant == "qh":
        cps |= KANA_QH_VS
    elif variant == "q":
        cps |= KANA_Q_VS
    elif variant == "":
        cps |= set(mark_cps)
    return sorted(cps)


def write_css(out_dir: str, built: Sequence[Tuple[str, str, int, List[int]]]) -> None:
    """Write edenia-kana.css: `h` pigeonholes then the base face."""
    css_path = os.path.join(out_dir, CSS_KANA)
    mark_cps: set[int] = set()
    for face_id, _variant, _n, _cps in built:
        for stem_name in (f"{face_id}.woff2", f"{face_id}.ttf"):
            font_path = os.path.join(out_dir, stem_name)
            if not os.path.isfile(font_path):
                continue
            try:
                from hangul_diacritics import combining_mark_codepoints_from_font

                mark_cps |= set(combining_mark_codepoints_from_font(font_path))
            except Exception as exc:
                print(f"  [!] kana mark unicode-range ({face_id}): {exc}", flush=True)
            break
        if mark_cps:
            break

    def _face_sort(item: Tuple[str, str, int, List[int]]) -> Tuple[int, int, str]:
        face_id, variant, _n, _cps = item
        pri = (
            SEGMENT_FACE_CSS_ORDER.index(variant)
            if variant in SEGMENT_FACE_CSS_ORDER
            else len(SEGMENT_FACE_CSS_ORDER)
        )
        bucket, _ = parse_bucket_face_id(face_id)
        return (pri, bucket if bucket is not None else 999, face_id)

    lines: List[str] = [
        "/* Auto-generated Edenia kana: segment faces (h/t/q/qv/qh, pigeonholed)",
        "   then 'edenia kana' (PUA D4 + dakuten). Pin segment faces for VS/FE*.",
        "   Kana must not claim FE01–FE07 (CJK/Yi D4). */",
        "",
    ]

    def _emit(family: str, face_id: str, unicode_range: str) -> None:
        lines.append("@font-face {")
        lines.append(f"  font-family: '{family}';")
        lines.append(
            format_src_line(
                dist_rel("kana", f"{face_id}.woff2"),
                fmt="woff2",
                local=(
                    (f"./{face_id}.woff2", "woff2"),
                    (f"./{face_id}.ttf", "truetype"),
                ),
                indent="  ",
            )
        )
        if unicode_range:
            lines.append(f"  unicode-range: {unicode_range};")
        lines.extend(
            [
                "  font-weight: normal;",
                "  font-style: normal;",
                "  font-display: swap;",
                "}",
                "",
            ]
        )

    for face_id, variant, _n, codepoints in sorted(built, key=_face_sort):
        ur = unicode_range_css(
            _css_cps_for_kana_face(codepoints, variant, mark_cps=sorted(mark_cps))
        )
        _emit(family_kana_variant(variant), face_id, ur)

    with open(css_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {css_path}")

    from edenia_names import SEGMENT_FACE_STACK_ORDER

    has_base = any(v == "" for _fid, v, _n, _cps in built)
    stack_parts: List[str] = []
    for v in SEGMENT_FACE_STACK_ORDER:
        if v and any(fv == v for _fid, fv, _n, _cps in built):
            stack_parts.append(f"'{family_kana_variant(v)}'")
        elif not v and has_base:
            stack_parts.append(f"'{family_kana_variant('')}'")
    if not stack_parts and has_base:
        stack_parts.append(f"'{family_kana_variant('')}'")
    stack = ", ".join(stack_parts) or f"'{FAMILY_NAME}'"
    fontlist_path = os.path.join(out_dir, f"{PS_NAME}-fontlist.css")
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(
            "/* Default stack is h+base only (one face per digraph). "
            "Pin edenia kana t/q/qv/qh for those modes. */\n"
            f":root {{\n  --font-edenia-kana: {stack};\n}}\n"
        )
    print(f"Wrote {fontlist_path}")


def _add_kana_slices(
    *,
    full_bases: Sequence[str],
    small_bases: Sequence[str],
    hw_full_bases: Sequence[str],
    hw_small_bases: Sequence[str],
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict[str, Tuple[int, int]],
    target_upem: int,
) -> None:
    """Bake combining slices + overlays for bases present in `glyphs`."""
    full_pairs = [
        (f, s) for f, s in zip(full_bases, small_bases) if f in glyphs and s in glyphs
    ]
    full = [f for f, _s in full_pairs]
    small = [s for _f, s in full_pairs]
    if not full:
        full = [b for b in full_bases if b in glyphs]
    hw_full = [b for b in hw_full_bases if b in glyphs]
    hw_small = [b for b in hw_small_bases if b in glyphs]
    if full:
        print(
            "  Installing FE08–FE0F combining slices on full forms "
            "(identity clip; D4 via propagate)...",
            flush=True,
        )
        add_slice_halves(
            full,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            modes=YI_ORIENTATION_MODES,
        )
    if small and full:
        print(
            "  Small slice halves: ideo-scale + Weight "
            "(match bodies; keep half-planes)...",
            flush=True,
        )
        n_sm_halves = add_small_slice_halves_from_full(
            small,
            full,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            modes=YI_ORIENTATION_MODES,
        )
        print(f"  Small slice halves: {n_sm_halves}", flush=True)
        ov_sm: List[str] = []
        for b in small:
            for form in orientation_form_names(b, modes=YI_ORIENTATION_MODES):
                ov_sm.append(form)
                for suf in SLICE_SUFFIXES:
                    n = half_glyph_name(form, suf)
                    if n in glyphs:
                        ov_sm.append(n)
        add_overlay_forms(
            ov_sm, glyph_order=glyph_order, glyphs=glyphs, metrics=metrics
        )
        sm_half_pin: List[str] = []
        for b in small:
            for form in orientation_form_names(b, modes=YI_ORIENTATION_MODES):
                for half in SLICE_SUFFIXES:
                    sm_half_pin.append(half_glyph_name(form, half))
        apply_small_floor_pin(
            sm_half_pin,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
        )
    hw_cell = target_upem * HALF_WIDTH_FACTOR
    if hw_full or hw_small:
        print(
            f"  Halfwidth slice halves (cell {hw_cell:g}, "
            f"CAPE Width {HALF_WIDTH_FACTOR:g}, fixed stems)...",
            flush=True,
        )
    if hw_full:
        add_slice_halves(
            hw_full,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            modes=YI_ORIENTATION_MODES,
            cell_width=hw_cell,
        )
    if hw_small:
        add_slice_halves(
            hw_small,
            glyph_order=glyph_order,
            glyphs=glyphs,
            metrics=metrics,
            target_upem=target_upem,
            modes=YI_ORIENTATION_MODES,
            cell_width=hw_cell,
        )


def _install_kana_dakuten_layout(
    font,
    *,
    full_bases: Sequence[str],
    small_bases: Sequence[str],
    hw_full_bases: Sequence[str],
    hw_small_bases: Sequence[str],
    glyph_order: List[str],
    glyphs: Dict,
    mark_names: Sequence[str],
    mark_cps: Sequence[int],
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]],
    target_upem: int,
    mark_ink_height: Optional[float] = None,
    mark_contour_points: Optional[List[Tuple[float, float]]] = None,
) -> None:
    if not mark_names:
        return
    all_forms = kana_coord_liga_names(
        [*full_bases, *small_bases, *hw_full_bases, *hw_small_bases],
        glyphs=glyphs,
    )
    face_anchors = inherit_kana_dakuten_anchors(
        {k: v for k, v in base_anchors.items() if k in glyphs},
        all_forms,
    )
    if not face_anchors:
        return
    if all_forms:
        print(
            f"  Compiling GSUB (dakuten slots {DAKUTEN_SLOT_CYCLE})...",
            flush=True,
        )
        install_dakuten_slot_gsub(
            font,
            mark_cps,
            glyphs=glyphs,
            glyph_order=glyph_order,
            base_names=all_forms,
        )
        install_dakuten_chain_gsub(
            font,
            mark_cps,
            glyphs=glyphs,
            glyph_order=glyph_order,
        )
    face_marks = [
        n for n in mark_names if n in glyphs and not is_dakuten_chain_glyph(n)
    ]
    if face_marks and face_anchors:
        print(
            f"  Compiling GPOS (dakuten @ {len(face_anchors)} forms; "
            f"slots from full D4 stems)...",
            flush=True,
        )
        install_dakuten_gpos(
            font,
            base_anchors=face_anchors,
            mark_cps=mark_cps,
            mark_names=face_marks,
            glyph_order=glyph_order,
            glyphs=glyphs,
            mark_anchor_fn=kana_mark_center_anchor,
        )
        install_dakuten_mark_chain_gpos(
            font,
            mark_cps=mark_cps,
            glyphs=glyphs,
            glyph_order=glyph_order,
            mark_height=mark_ink_height,
            target_upem=target_upem,
            chain_parent_anchor_fn=kana_mark_chain_parent_anchor,
            chain_child_anchor_fn=kana_mark_center_anchor,
        )


def _save_kana_face(
    *,
    face_id: str,
    variant: str,
    glyph_order: List[str],
    glyphs: Dict,
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
    full_bases: Sequence[str],
    small_bases: Sequence[str],
    hw_full_bases: Sequence[str],
    hw_small_bases: Sequence[str],
    mark_names: Sequence[str],
    mark_cps: Sequence[int],
    base_anchors: Dict[str, Dict[int, Tuple[int, int]]],
    out_dir: str,
    target_upem: int,
    mark_ink_height: Optional[float],
    mark_contour_points: Optional[List[Tuple[float, float]]],
    slices: bool,
) -> Tuple[str, str, int, List[int]]:
    n_glyphs = len(glyphs)
    if n_glyphs > TTF_GLYPH_LIMIT:
        raise RuntimeError(
            f"{face_id}: {n_glyphs} glyphs exceeds TTF uint16 max ({TTF_GLYPH_LIMIT})"
        )
    family = family_kana_variant(variant)
    ps = ps_kana(face_id)
    out_path = os.path.join(out_dir, f"{face_id}.ttf")
    ascent = otRound(target_upem * TYPO_ASCENDER_FRAC)
    descent = otRound(target_upem * TYPO_DESCENDER_FRAC)
    n_logical = sum(1 for b in full_bases if b in glyphs)
    print(
        f"  Assembling {family} / {face_id} "
        f"({n_glyphs - 1} glyphs, {n_logical} logical)...",
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
        achVendID="pKa ",
    )
    fb.setupPost()

    if slices:
        full_forms: List[str] = []
        for b in full_bases:
            if b in glyphs:
                full_forms.extend(orientation_form_names(b, modes=YI_ORIENTATION_MODES))
        for b in small_bases:
            if b in glyphs:
                full_forms.extend(orientation_form_names(b, modes=YI_ORIENTATION_MODES))
        if full_forms:
            print("  Compiling GSUB (FE00 overlay + FE08–F slice)...", flush=True)
            install_slice_gsub(
                fb.font,
                full_forms,
                glyphs=glyphs,
                glyph_order=glyph_order,
            )
        hw_forms: List[str] = []
        for b in hw_full_bases:
            if b in glyphs:
                hw_forms.extend(orientation_form_names(b, modes=YI_ORIENTATION_MODES))
        for b in hw_small_bases:
            if b in glyphs:
                hw_forms.extend(orientation_form_names(b, modes=YI_ORIENTATION_MODES))
        if hw_forms:
            print("  Compiling GSUB (halfwidth FE00/FE08–F slice)...", flush=True)
            install_slice_gsub(
                fb.font,
                hw_forms,
                glyphs=glyphs,
                glyph_order=glyph_order,
            )

    _install_kana_dakuten_layout(
        fb.font,
        full_bases=full_bases,
        small_bases=small_bases,
        hw_full_bases=hw_full_bases,
        hw_small_bases=hw_small_bases,
        glyph_order=glyph_order,
        glyphs=glyphs,
        mark_names=mark_names,
        mark_cps=mark_cps,
        base_anchors=base_anchors,
        target_upem=target_upem,
        mark_ink_height=mark_ink_height,
        mark_contour_points=mark_contour_points,
    )

    os.makedirs(out_dir, exist_ok=True)
    setup_head_timestamps(fb)
    fb.save(out_path)
    return face_id, variant, n_glyphs - 1, sorted(cmap.keys())


_WORKER_MASTER: Optional[dict] = None
_WORKER_CACHE_DIR: Optional[str] = None


def _face_pkl_path(cache_dir: str, face_id: str) -> str:
    """One pickle per output font file (CJK-style face cache)."""
    return os.path.join(cache_dir, f"{face_id}.pkl")


def _init_kana_face_cache_worker(master_path: str, cache_dir: str) -> None:
    global _WORKER_MASTER, _WORKER_CACHE_DIR
    _WORKER_CACHE_DIR = cache_dir
    with open(master_path, "rb") as f:
        _WORKER_MASTER = pickle.load(f)


def _init_kana_face_ttf_worker(cache_dir: str) -> None:
    global _WORKER_CACHE_DIR
    _WORKER_CACHE_DIR = cache_dir


def _kana_face_anchors(
    master_anchors: Dict[str, Dict[int, Tuple[int, int]]],
    glyphs: Dict[str, TTGlyph],
) -> Dict[str, Dict[int, Tuple[int, int]]]:
    """Stem slots from master, transposed onto forms present in this face."""
    stems = {k: v for k, v in master_anchors.items() if k in glyphs}
    return inherit_kana_dakuten_anchors(stems, list(glyphs))


def _prepare_kana_face_state(
    spec: Tuple[str, Optional[int]],
    m: dict,
) -> dict:
    """Subset (+ bake segments/slices) into a pickle-able per-font state."""
    kind, bucket_id = spec
    glyph_order = m["glyph_order"]
    glyphs = m["glyphs"]
    metrics = m["metrics"]
    cmap = m["cmap"]
    target_upem = m["target_upem"]

    if kind in ("h", "t", "q", "qv", "qh"):
        assert bucket_id is not None
        full_b = _kana_bases_in_bucket(m["full_bases"], cmap, bucket_id)
        small_b = _kana_bases_in_bucket(m["small_bases"], cmap, bucket_id)
        hw_full_b = _kana_bases_in_bucket(m["hw_full_bases"], cmap, bucket_id)
        hw_small_b = _kana_bases_in_bucket(m["hw_small_bases"], cmap, bucket_id)
        # Stem names for keep/GSUB: fullwidth and/or halfwidth on this page.
        bases = list(dict.fromkeys([*full_b, *hw_full_b]))
        keep: Set[str] = {".notdef", *m.get("dakuten_keep", ())}
        for cp, name in cmap.items():
            if (cp >> 8) == bucket_id:
                keep.add(name)
        keep |= keep_names_for_segment_face(kind, bases, glyphs)
        keep |= keep_names_for_segment_face(
            kind, list(dict.fromkeys([*small_b, *hw_small_b])), glyphs
        )
        go, gl, mt, cm = subset_tables(glyph_order, glyphs, metrics, cmap, keep)
        # Include small/hw-small stems so their D4 PUA stays on the slice face.
        cm = filter_segment_face_cmap(
            kind,
            cm,
            list(dict.fromkeys([*bases, *small_b, *hw_small_b])),
            mark_cps=m.get("mark_cps"),
        )
        face_id = bucket_face_id(bucket_id, kind)
        if kind == "h":
            _add_kana_slices(
                full_bases=full_b,
                small_bases=small_b,
                hw_full_bases=hw_full_b,
                hw_small_bases=hw_small_b,
                glyph_order=go,
                glyphs=gl,
                metrics=mt,
                target_upem=target_upem,
            )
            inject_slice_marks(go, gl, mt, cm)
            return {
                "face_kind": "dakuten",
                "face_id": face_id,
                "variant": "h",
                "glyph_order": go,
                "glyphs": gl,
                "metrics": mt,
                "cmap": cm,
                "full_bases": full_b,
                "small_bases": small_b,
                "hw_full_bases": hw_full_b,
                "hw_small_bases": hw_small_b,
                "mark_names": m["mark_names"],
                "mark_cps": m["mark_cps"],
                "base_anchors": _kana_face_anchors(m["base_anchors"], gl),
                "out_dir": m["out_dir"],
                "target_upem": target_upem,
                "mark_ink_height": m.get("mark_ink_height"),
                "mark_contour_points": m.get("mark_contour_points"),
                "slices": True,
            }
        # Third/quarter clips: prefer fullwidth stems; else halfwidth-only page.
        segment_bases = full_b if full_b else hw_full_b
        _prepare_kana_segment_glyphs(
            full_bases=segment_bases,
            glyph_order=go,
            glyphs=gl,
            metrics=mt,
            cmap=cm,
            target_upem=target_upem,
            variants={kind},
        )
        return {
            "face_kind": "segment",
            "face_id": face_id,
            "variant": kind,
            "glyph_order": go,
            "glyphs": gl,
            "metrics": mt,
            "cmap": cm,
            "bases": bases,
            "out_dir": m["out_dir"],
            "target_upem": target_upem,
        }

    go, gl, mt, cm = subset_glyph_tables(
        glyph_order, glyphs, metrics, cmap, set(glyph_order)
    )
    return {
        "face_kind": "dakuten",
        "face_id": PS_NAME,
        "variant": "",
        "glyph_order": go,
        "glyphs": gl,
        "metrics": mt,
        "cmap": cm,
        "full_bases": m["full_bases"],
        "small_bases": m["small_bases"],
        "hw_full_bases": m["hw_full_bases"],
        "hw_small_bases": m["hw_small_bases"],
        "mark_names": m["mark_names"],
        "mark_cps": m["mark_cps"],
        "base_anchors": _kana_face_anchors(m["base_anchors"], gl),
        "out_dir": m["out_dir"],
        "target_upem": target_upem,
        "mark_ink_height": m.get("mark_ink_height"),
        "mark_contour_points": m.get("mark_contour_points"),
        "slices": False,
    }


def _kana_face_cache_task(spec: Tuple[str, Optional[int]]) -> str:
    """Build and pickle one face's glyf state (``{face_id}.pkl``)."""
    assert _WORKER_MASTER is not None
    assert _WORKER_CACHE_DIR is not None
    state = _prepare_kana_face_state(spec, _WORKER_MASTER)
    face_id = state["face_id"]
    path = _face_pkl_path(_WORKER_CACHE_DIR, face_id)
    with open(path, "wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
    n_glyf = len(state["glyphs"]) - 1
    print(f"  cached {face_id}.pkl ({n_glyf} glyphs)", flush=True)
    return face_id


def _emit_kana_face_from_state(state: dict) -> Tuple[str, str, int, List[int]]:
    if state["face_kind"] == "segment":
        return _save_kana_segment_face(
            face_id=state["face_id"],
            variant=state["variant"],
            glyph_order=state["glyph_order"],
            glyphs=state["glyphs"],
            metrics=state["metrics"],
            cmap=state["cmap"],
            bases=state["bases"],
            out_dir=state["out_dir"],
            target_upem=state["target_upem"],
        )
    return _save_kana_face(
        face_id=state["face_id"],
        variant=state["variant"],
        glyph_order=state["glyph_order"],
        glyphs=state["glyphs"],
        metrics=state["metrics"],
        cmap=state["cmap"],
        full_bases=state["full_bases"],
        small_bases=state["small_bases"],
        hw_full_bases=state["hw_full_bases"],
        hw_small_bases=state["hw_small_bases"],
        mark_names=state["mark_names"],
        mark_cps=state["mark_cps"],
        base_anchors=state["base_anchors"],
        out_dir=state["out_dir"],
        target_upem=state["target_upem"],
        mark_ink_height=state.get("mark_ink_height"),
        mark_contour_points=state.get("mark_contour_points"),
        slices=state["slices"],
    )


def _kana_face_ttf_task(face_id: str) -> Tuple[str, str, int, List[int], str]:
    """Load one face pickle and write its TTF."""
    assert _WORKER_CACHE_DIR is not None
    path = _face_pkl_path(_WORKER_CACHE_DIR, face_id)
    with open(path, "rb") as f:
        state = pickle.load(f)
    meta = _emit_kana_face_from_state(state)
    return (*meta, os.path.join(state["out_dir"], f"{meta[0]}.ttf"))


def build_edenia_kana_font(
    in_dir: str,
    out_dir: str,
    target_upem: int,
    *,
    limit: Optional[int] = None,
    write_ttf: bool = True,
    write_woff2: bool = True,
    hint: bool = True,
    variants: Sequence[str] = ("", "h"),
    jobs: int = 1,
) -> List[Tuple[str, str, int, List[int]]]:
    if not write_ttf and not write_woff2:
        raise ValueError("at least one of write_ttf / write_woff2 must be True")
    validate_chart_tables()
    want = {v for v in variants}

    source_cps = chart_source_cps()
    if limit is not None:
        source_cps = source_cps[: max(0, limit)]

    flop_paths = resolve_flop_family_paths(in_dir)
    mkana_path = resolve_mkana_path(in_dir)
    genseki_path = resolve_genseki_path(in_dir)
    lxgw_paths = resolve_lxgw_family_paths(in_dir)
    plangothic_path = resolve_plangothic_p1_path(in_dir)
    sans_serif_path = resolve_sans_serif_collection_path(in_dir)
    segoe_historic_path = resolve_segoe_ui_historic_path(in_dir)
    print(
        "  Flop: " + ", ".join(os.path.basename(p) for p in flop_paths),
        flush=True,
    )
    print(f"  mkanaplus: {mkana_path}", flush=True)
    print(f"  genseki: {genseki_path}", flush=True)
    print(
        "  lxgw: " + ", ".join(os.path.basename(p) for p in lxgw_paths),
        flush=True,
    )
    if plangothic_path is not None:
        print(f"  plangothic p1: {plangothic_path}", flush=True)
    else:
        print("  plangothic p1: (missing; skipped)", flush=True)
    if sans_serif_path is not None:
        print(f"  sans serif collection: {sans_serif_path}", flush=True)
    else:
        print("  sans serif collection: (missing; skipped)", flush=True)
    if segoe_historic_path is not None:
        print(f"  segoe ui historic: {segoe_historic_path}", flush=True)
    else:
        print("  segoe ui historic: (missing; skipped)", flush=True)

    flop = [SourceFont(path) for path in flop_paths]
    mkana = SourceFont(mkana_path)
    genseki = SourceFont(genseki_path)
    lxgw = [SourceFont(path) for path in lxgw_paths]
    plangothic = [SourceFont(plangothic_path)] if plangothic_path is not None else []
    sans_serif = [SourceFont(sans_serif_path)] if sans_serif_path is not None else []
    segoe_historic = (
        [SourceFont(segoe_historic_path)] if segoe_historic_path is not None else []
    )
    primary_path_set = {os.path.normcase(os.path.normpath(p)) for p in flop_paths}
    primary_path_set.add(os.path.normcase(os.path.normpath(mkana_path)))
    avg_w, avg_h = flop_average_ink_size(flop, target_upem, source_cps)
    print(
        f"  Size-fit (non-Flop/mkana): grow to Flop avg "
        f"W {avg_w:.1f} / H {avg_h:.1f} if smaller; thin stems to compensate "
        f"(upem {target_upem})",
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
            f"Installing {len(source_cps)} logical kana × {D4_COUNT} D4 "
            f"(no stem-normalize) + smalls "
            f"(ideo-scale {SMALL_WIDTH_FACTOR:g} + Weight once, "
            f"D4 @ post-scale ideo center)...",
            flush=True,
        )
        for logical, src_cp in enumerate(source_cps):
            try:
                src, gname = claim_source_cp(
                    src_cp,
                    flop,
                    mkana,
                    genseki,
                    lxgw,
                    plangothic,
                    sans_serif,
                    segoe_historic,
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
            if os.path.normcase(os.path.normpath(src.path)) not in primary_path_set:
                sa_glyph, sa_adv, sa_lsb = stretch_to_flop_average(
                    sa_glyph,
                    sa_adv,
                    avg_width=avg_w,
                    avg_height=avg_h,
                    target_upem=target_upem,
                    glyph_set={".tmp": sa_glyph},
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
            hw_pivot = halfwidth_center(target_upem)
            # Y-squeezed r90 before D4 so r270/r90mx/r90my are not a rotate of
            # the X-squeezed identity (~1em ink in a ½em advance).
            replace_halfwidth_r90(
                hw_base,
                glyphs[base],
                f_adv,
                target_upem,
                glyphs=glyphs,
                metrics=metrics,
                glyph_set=glyphs,
                glyph_order=glyph_order,
                pivot=hw_pivot,
            )
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
                pivot=hw_pivot,
            )
            rebuild_sideways_from_r90(
                hw_base,
                target_upem=target_upem,
                glyphs=glyphs,
                metrics=metrics,
                pivot=hw_pivot,
                modes=YI_ORIENTATION_MODES,
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
            hw_sm_pivot = halfwidth_center(target_upem, SMALL_WIDTH_FACTOR)
            replace_halfwidth_r90(
                hw_sm,
                glyphs[sm_base],
                sm_adv,
                target_upem,
                glyphs=glyphs,
                metrics=metrics,
                glyph_set=glyphs,
                glyph_order=glyph_order,
                size_factor=SMALL_WIDTH_FACTOR,
                pivot=hw_sm_pivot,
            )
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
                pivot=hw_sm_pivot,
            )
            rebuild_sideways_from_r90(
                hw_sm,
                target_upem=target_upem,
                glyphs=glyphs,
                metrics=metrics,
                pivot=hw_sm_pivot,
                modes=YI_ORIENTATION_MODES,
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
        pivot = small_ideo_center(target_upem)
        print(
            f"  Small D4 pivot (post-scale ideo center): "
            f"({pivot[0]:.1f}, {pivot[1]:.1f})",
            flush=True,
        )

        mark_names: List[str] = []
        mark_cps: List[int] = []
        mark_ink_h: Optional[float] = None
        mark_contour_pts: Optional[List[Tuple[float, float]]] = None
        base_anchors: Dict[str, Dict[int, Tuple[int, int]]] = {}
        workers = max(1, jobs)
        cache_dir = tempfile.mkdtemp(prefix="edenia-kana-")
        try:
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
                mark_contour_pts = kana_representative_mark_points(mark_glyphs)
                if mark_contour_pts:
                    ys = [y for _x, y in mark_contour_pts]
                    mark_ink_h = max(ys) - min(ys)
                mark_names = add_dakuten_mark_glyphs(
                    mark_cps,
                    mark_glyphs,
                    glyph_order=glyph_order,
                    glyphs=glyphs,
                    metrics=metrics,
                    cmap=cmap,
                )
                chain_names = add_dakuten_chain_mark_glyphs(
                    mark_cps,
                    glyph_order=glyph_order,
                    glyphs=glyphs,
                    metrics=metrics,
                )
                mark_names = list(mark_names) + chain_names

                dakuten_bases = [
                    *full_bases,
                    *small_bases,
                    *hw_full_bases,
                    *hw_small_bases,
                ]
                stem_names = kana_dakuten_placement_stems(dakuten_bases, glyphs=glyphs)
                n_logical = sum(1 for b in dakuten_bases if b in glyphs)
                print(
                    f"Dakuten anchors ({len(stem_names)} stems = "
                    f"{n_logical} bases × ≤8 D4; segments inherit; "
                    f"{workers} chunk workers)...",
                    flush=True,
                )
                t_anchors = time.perf_counter()
                base_anchors = collect_kana_dakuten_anchors(
                    dakuten_bases,
                    glyphs=glyphs,
                    glyph_set=glyphs,
                    target_upem=target_upem,
                    mark_ink_height=mark_ink_h,
                    mark_points=mark_contour_pts,
                    jobs=workers,
                    cache_dir=cache_dir,
                )
                print(
                    f"  dakuten done in {time.perf_counter() - t_anchors:.1f}s "
                    f"({len(base_anchors)} D4 stems placed)",
                    flush=True,
                )
                print(
                    f"  Dakuten: {len(mark_cps)} marks × {len(DAKUTEN_SLOTS)} slots "
                    f"(octagon ring + corner chain TR→BL; full/small/hw; "
                    f"dakuten H≈{mark_ink_h:.0f})",
                    flush=True,
                )
            except FileNotFoundError as exc:
                print(f"  Skipping dakuten marks: {exc}", flush=True)

            dakuten_keep = {n for n in glyph_order if ".mk" in n}
            # Third/quarter clips are baked per face pickle (bucket-local), not
            # on the shared master — keeps master.pkl small and parallelizable.

            built: List[Tuple[str, str, int, List[int]]] = []
            os.makedirs(out_dir, exist_ok=True)
            # Unsuffixed base face is assembled in-process (full inventory +
            # dakuten). Pigeonholed segment faces use the pickle pool.
            seg_specs: List[Tuple[str, Optional[int]]] = []
            pages = _kana_pigeonhole_pages(cmap)
            for seg in SEGMENT_FACE_BUILD_ORDER:
                if seg not in want or not seg:
                    continue
                for bucket_id in pages:
                    if _kana_bucket_has_bases(
                        full_bases=full_bases,
                        hw_full_bases=hw_full_bases,
                        cmap=cmap,
                        bucket_id=bucket_id,
                    ):
                        seg_specs.append((seg, bucket_id))
            if "" not in want and not seg_specs:
                return built

            master_path = os.path.join(cache_dir, "master.pkl")
            print(
                f"  Writing slim master.pkl ({len(glyphs) - 1} glyphs)...",
                flush=True,
            )
            t_cache = time.perf_counter()
            master_state = {
                "glyph_order": glyph_order,
                "glyphs": glyphs,
                "metrics": metrics,
                "cmap": cmap,
                "full_bases": full_bases,
                "small_bases": small_bases,
                "hw_full_bases": hw_full_bases,
                "hw_small_bases": hw_small_bases,
                "mark_names": mark_names,
                "mark_cps": mark_cps,
                "base_anchors": base_anchors,
                "mark_ink_height": mark_ink_h,
                "mark_contour_points": mark_contour_pts,
                "dakuten_keep": dakuten_keep,
                "out_dir": out_dir,
                "target_upem": target_upem,
            }
            with open(master_path, "wb") as f:
                pickle.dump(
                    master_state,
                    f,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            print(
                f"  master written in {time.perf_counter() - t_cache:.1f}s",
                flush=True,
            )

            ttf_paths: List[str] = []
            if "" in want:
                t0 = time.perf_counter()
                print(
                    f"Stage 1/4: base face {PS_NAME} (main process)...",
                    flush=True,
                )
                base_meta = _save_kana_face(
                    face_id=PS_NAME,
                    variant="",
                    glyph_order=glyph_order,
                    glyphs=glyphs,
                    metrics=metrics,
                    cmap=cmap,
                    full_bases=full_bases,
                    small_bases=small_bases,
                    hw_full_bases=hw_full_bases,
                    hw_small_bases=hw_small_bases,
                    mark_names=mark_names,
                    mark_cps=mark_cps,
                    base_anchors=_kana_face_anchors(base_anchors, glyphs),
                    out_dir=out_dir,
                    target_upem=target_upem,
                    mark_ink_height=mark_ink_h,
                    mark_contour_points=mark_contour_pts,
                    slices=False,
                )
                built.append(base_meta)
                ttf_paths.append(os.path.join(out_dir, f"{PS_NAME}.ttf"))
                print(
                    f"  base done in {time.perf_counter() - t0:.1f}s "
                    f"({base_meta[2]} glyphs)",
                    flush=True,
                )

            if seg_specs:
                pool_workers = min(workers, max(1, len(seg_specs)))
                t0 = time.perf_counter()
                print(
                    f"Stage 1/4: segment pickles ({len(seg_specs)} fonts, "
                    f"{pool_workers} workers)...",
                    flush=True,
                )
                with ProcessPoolExecutor(
                    max_workers=pool_workers,
                    initializer=_init_kana_face_cache_worker,
                    initargs=(master_path, cache_dir),
                ) as executor:
                    face_ids = list(executor.map(_kana_face_cache_task, seg_specs))
                print(
                    f"  segment pickles done in {time.perf_counter() - t0:.1f}s",
                    flush=True,
                )

                t0 = time.perf_counter()
                print(
                    f"Stage 2/4: segment TTFs ({len(face_ids)} jobs, "
                    f"{pool_workers} workers)...",
                    flush=True,
                )
                with ProcessPoolExecutor(
                    max_workers=pool_workers,
                    initializer=_init_kana_face_ttf_worker,
                    initargs=(cache_dir,),
                ) as executor:
                    results = list(executor.map(_kana_face_ttf_task, face_ids))
                    print(
                        f"  stage 2 done in {time.perf_counter() - t0:.1f}s",
                        flush=True,
                    )
                    ttf_paths.extend(r[4] for r in results)
                    built.extend((r[0], r[1], r[2], r[3]) for r in results)
                    finish_font_outputs(
                        ttf_paths,
                        hint=hint,
                        write_woff2=write_woff2,
                        write_ttf=write_ttf,
                        executor=executor,
                    )
            elif ttf_paths:
                with ProcessPoolExecutor(max_workers=1) as executor:
                    finish_font_outputs(
                        ttf_paths,
                        hint=hint,
                        write_woff2=write_woff2,
                        write_ttf=write_ttf,
                        executor=executor,
                    )
        finally:
            shutil.rmtree(cache_dir, ignore_errors=True)
        return built
    finally:
        mkana.close()
        genseki.close()
        for src in flop:
            src.close()
        for src in lxgw:
            src.close()
        for src in plangothic:
            src.close()
        for src in sans_serif:
            src.close()
        for src in segoe_historic:
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
    variants: Sequence[str] = ("", "h"),
    jobs: int = 1,
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
        f"  Slice (h face): U+FE00 overlay, U+FE08–FE0B halves, "
        "U+FE0C–FE0F triangles (pigeonholed like CJK h)"
    )
    print(
        "  Dakuten: full-size marks outside ink after D4 "
        f"({DAKUTEN_SLOT_CYCLE}; CGJ U+034F skips a slot; no .mk.sm scale)"
    )
    print(
        f"  Output: '{FAMILY_NAME}'"
        + (" + pigeonholed 'edenia kana h'" if "h" in variants else "")
    )
    fmt_note = (
        "ttf+woff2"
        if write_ttf and write_woff2
        else ("ttf only" if write_ttf else "woff2 only")
    )
    print(f"  Formats: {fmt_note}")
    print(f"  Jobs: {max(1, jobs)}")

    os.makedirs(out_dir, exist_ok=True)
    built = build_edenia_kana_font(
        in_dir,
        out_dir,
        target_upem,
        limit=limit,
        write_ttf=write_ttf,
        write_woff2=write_woff2,
        hint=hint,
        variants=variants,
        jobs=jobs,
    )
    if built:
        write_css(out_dir, built)
        keep_names = {f"{fid}.woff2" for fid, *_ in built}
        keep_names |= {f"{fid}.ttf" for fid, *_ in built}
        want_base = "" in variants
        for name in os.listdir(out_dir):
            stem, ext = os.path.splitext(name)
            if ext.lower() not in {".woff2", ".ttf"}:
                continue
            # Only prune base + h pigeonholes; never delete q/t/qv/qh here.
            if stem != PS_NAME and parse_h_bucket_face_id(stem) is None:
                continue
            # Never drop the unsuffixed base when this run requested it.
            if stem == PS_NAME and want_base:
                if name not in keep_names:
                    print(
                        f"  [!] expected base face missing from built: {name}",
                        flush=True,
                    )
                continue
            if name in keep_names:
                continue
            try:
                os.remove(os.path.join(out_dir, name))
            except OSError:
                continue
            print(f"  Removed stale {name}", flush=True)
    for face_id, variant, count, _cps in built:
        print(
            f"  {family_kana_variant(variant)} / {face_id}: {count} glyphs",
            flush=True,
        )
    print(f"\nDone: {len(built)} kana face(s), jobs={max(1, jobs)}", flush=True)
    if os.path.normcase(os.path.abspath(out_dir)) == os.path.normcase(
        os.path.abspath(OUT_DIR)
    ):
        sync_dist_to_plugin("kana", out_dir)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build edenia kana (PUA D4 + dakuten) and pigeonholed segment faces "
            "(h / t / q / qv / qh)"
        )
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
    add_cjk_variant_arguments(p)
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
    add_no_hint_argument(p)
    add_jobs_argument(p)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    variants = resolve_kana_yi_variants(args)
    build_all(
        args.in_dir,
        args.out_dir,
        args.upem,
        limit=args.limit,
        write_ttf=not args.woff2_only,
        write_woff2=not args.ttf_only,
        hint=not args.no_hint,
        variants=variants,
        jobs=max(1, args.jobs),
    )
