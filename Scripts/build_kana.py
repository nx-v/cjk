#!/usr/bin/env python3
"""
Build `edenia kana` (PUA D4 cmap + smalls + dakuten) and pigeonholed
`edenia kana h` (FE00 overlay + FE08–FE0F slices), matching CJK base vs `h`.

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
overrides), then GenSeki Hentaigana, then LXGW (Clear Gothic / XiHei). Glyphs
from sources other than Flop / mkana that are smaller than the average Flop kana
ink size are stretched up on X and/or Y to that average; strokes are thinned to
compensate (CAPE restores pre-stretch stem weight). Axes already at or above the
average are left as-is.

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
from kana_yi_diacritics import (
    collect_kana_dakuten_anchors,
    kana_coord_liga_names,
    kana_mark_center_anchor,
    kana_mark_chain_parent_anchor,
    kana_representative_mark_points,
)
from hangul_diacritics import (
    DAKUTEN_SLOT_CYCLE,
    DAKUTEN_SLOTS,
    add_dakuten_mark_glyphs,
    add_dakuten_chain_mark_glyphs,
    dakuten_mark_stack_label,
    is_dakuten_chain_glyph,
    install_dakuten_chain_gsub,
    install_dakuten_gpos,
    install_dakuten_mark_chain_gpos,
    install_dakuten_slot_gsub,
    load_dakuten_marks_from_stack,
    resolve_dakuten_mark_font_stack,
)
from shared_half_cells import (
    DEFAULT_UPEM,
    TTF_GLYPH_LIMIT,
    TYPO_ASCENDER_FRAC,
    TYPO_DESCENDER_FRAC,
    YI_ORIENTATION_MODES,
    add_d4_variant_glyphs,
    add_overlay_forms,
    apply_transform,
    empty_glyph,
    fit_glyph_to_ideographic_cell,
    ideographic_bounds,
    ideographic_center,
    orientation_form_names,
    rebuild_sideways_from_r90,
    subset_glyph_tables,
    variant_glyph_name,
    variant_transform,
)
from shared_half_cells import _bake_transformed_glyph  # composite → plain outlines
from yi_slice import (
    SLICE_SUFFIXES,
    add_slice_halves,
    half_glyph_name,
    inject_slice_marks,
    install_slice_gsub,
)
from edenia_names import (
    CSS_KANA,
    FAMILY_KANA,
    PS_KANA,
    family_kana_variant,
    h_bucket_face_id,
    parse_h_bucket_face_id,
    ps_kana,
)
from sync_edenian_fonts import sync_dist_to_plugin
from cdn_fonts import dist_rel, format_src_line
from shared_font_builder import load_ttfont, setup_head_timestamps
from shared_hinting import add_jobs_argument, add_no_hint_argument, finish_font_outputs

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

# Source-shape overrides: always claim from mkanaplus when present.
MKANA_OVERRIDE_CPS: frozenset[int] = frozenset(
    {
        0x0305F,  # た
        0x0306A,  # な
        0x0306B,  # に
        0x03053,  # こ
        0x0304F,  # く
        0x03078,  # へ
        0x030A2,  # ア
        0x030BD,  # ソ
        0x030EB,  # ル
        0x030EF,  # ワ
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
    "ny",
)
VOWELS: Tuple[str, ...] = ("a", "i", "u", "e", "o", "ə")

# 18×6 hiragana, then 18×6 katakana (row-major). Values = source CPs.
HIRAGANA_ROWS: Tuple[Tuple[int, ...], ...] = (
    (0x03042, 0x03044, 0x03046, 0x03048, 0x0304A, 0x1B015),  # ∅
    (0x0304B, 0x0304D, 0x0304F, 0x03051, 0x03053, 0x1B02B),  # k
    (0x0E020, 0x0E021, 0x0E022, 0x0E023, 0x0E024, 0x1B033),  # ng
    (0x0305F, 0x0ED10, 0x1B06D, 0x03066, 0x03068, 0x1B077),  # t
    (0x0ED1C, 0x0ED1E, 0x03064, 0x0ED20, 0x0ED22, 0x1B06A),  # ts
    (0x0ED14, 0x03061, 0x0ED16, 0x0EE27, 0x0ED1A, 0x1B063),  # ch
    (0x1B043, 0x0EBC8, 0x0ED0A, 0x0ED0C, 0x0ED0E, 0x1B044),  # sh
    (0x03055, 0x0ED01, 0x03059, 0x0305B, 0x0305D, 0x1B053),  # s
    (0x0307E, 0x0307F, 0x03080, 0x03081, 0x03082, 0x1B0DA),  # m
    (0x0306A, 0x0EBD0, 0x0306C, 0x0306D, 0x0306E, 0x03093),  # n
    (0x0306F, 0x03072, 0x1B039, 0x03078, 0x0307B, 0x1B0C0),  # h
    (0x03084, 0x1B006, 0x03086, 0x1B001, 0x03088, 0x1B0E5),  # y
    (0x0E0E0, 0x0E0E1, 0x0E0E2, 0x0E0E3, 0x0E0E4, 0x1B102),  # l
    (0x03089, 0x0308A, 0x0308B, 0x0308C, 0x0308D, 0x1B0EF),  # r
    (0x0308F, 0x03090, 0x1B11F, 0x03091, 0x03092, 0x1B10C),  # w
    (0x1B0A6, 0x1B0AB, 0x03075, 0x1B0B8, 0x1B0BF, 0x0ECC1),  # f
    (0x0E030, 0x0E031, 0x0E032, 0x0E02A, 0x0E034, 0x1B0AF),  # p
    (0x1B081, 0x1B08A, 0x1B099, 0x1B094, 0x1B09C, 0x1B08C),  # ny
)

KATAKANA_ROWS: Tuple[Tuple[int, ...], ...] = (
    (0x030A2, 0x030A4, 0x030A6, 0x030A8, 0x030AA, 0x031BE),  # ∅
    (0x030AB, 0x0EBE1, 0x030AF, 0x030B1, 0x030B3, 0x0310E),  # k
    (0x0EDD3, 0x0EC69, 0x0EDCA, 0x0EDC6, 0x0EDD7, 0x0312B),  # ng
    (0x030BF, 0x0ED50, 0x0ED52, 0x030C6, 0x030C8, 0x03109),  # t
    (0x0ED5C, 0x0ED5E, 0x030C4, 0x0ED60, 0x0ED62, 0x03118),  # ts
    (0x0ED54, 0x0EBEC, 0x0ED76, 0x0ED58, 0x0ED5A, 0x03114),  # ch
    (0x0ED48, 0x0EBE8, 0x0ED4A, 0x0ED4C, 0x0ED4E, 0x03115),  # sh
    (0x030B5, 0x0ED41, 0x030B9, 0x030BB, 0x030BD, 0x03112),  # s
    (0x030DE, 0x0EBF4, 0x030E0, 0x030E1, 0x030E2, 0x0F47F),  # m
    (0x030CA, 0x030CB, 0x030CC, 0x030CD, 0x030CE, 0x0EBF0),  # n
    (0x030CF, 0x030D2, 0x0EE45, 0x030D8, 0x030DB, 0x0310F),  # h
    (0x030E4, 0x1B120, 0x030E6, 0x1B121, 0x030E8, 0x0EDCF),  # y
    (0x0EDC3, 0x0EDC8, 0x0EDC0, 0x0EDC5, 0x0EDC1, 0x0310C),  # l
    (0x030E9, 0x030EA, 0x030EB, 0x030EC, 0x0EC66, 0x0EDD1),  # r
    (0x030EF, 0x030F0, 0x1B122, 0x030F1, 0x030F2, 0x0ED64),  # w
    (0x0EDCC, 0x0EDCD, 0x0ED7A, 0x0EDD8, 0x0EDD4, 0x0EDC7),  # f
    (0x0EDCB, 0x0EDC9, 0x0EE69, 0x0EDD0, 0x0EDC4, 0x03105),  # p
    (0x0EBE0, 0x0EDD2, 0x0ECC2, 0x0ECC3, 0x0ECC4, 0x0312C),  # ny
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
            f"FlopDesignFONT not found under " f"Scripts/src / {in_dir!r} / CJK / repo root"
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
) -> Tuple[SourceFont, str]:
    """Return (source, glyph_name) for a chart source CP."""
    if src_cp in MKANA_OVERRIDE_CPS:
        head: Tuple[SourceFont, ...] = (mkana, *flop, genseki)
    else:
        head = (*flop, mkana, genseki)
    for src in (*head, *lxgw):
        gname = src.cmap.get(src_cp)
        if gname is None:
            continue
        if is_empty_outline(src.tt, gname):
            continue
        return src, gname
    raise KeyError(f"No outline for U+{src_cp:04X} in Flop/mkanaplus/genseki/lxgw")


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


def _css_cps_for_kana_face(
    codepoints: Sequence[int], variant: str, *, mark_cps: Sequence[int]
) -> List[int]:
    cps = {cp for cp in codepoints if not (0xFE00 <= cp <= 0xFE0F)}
    if variant == "h":
        cps |= KANA_H_FE
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
        if variant == "h":
            bid = parse_h_bucket_face_id(face_id)
            return (0, bid if bid is not None else 0, face_id)
        return (1, 0, face_id)

    lines: List[str] = [
        "/* Auto-generated Edenia kana: 'edenia kana h' (slices, pigeonholed)",
        "   then 'edenia kana' (PUA D4 + dakuten). Pin h for FE00/FE08–F.",
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

    has_h = any(v == "h" for _fid, v, _n, _cps in built)
    has_base = any(v == "" for _fid, v, _n, _cps in built)
    stack_parts: List[str] = []
    if has_h:
        stack_parts.append(f"'{family_kana_variant('h')}'")
    if has_base:
        stack_parts.append(f"'{family_kana_variant('')}'")
    stack = ", ".join(stack_parts) or f"'{FAMILY_NAME}'"
    fontlist_path = os.path.join(out_dir, f"{PS_NAME}-fontlist.css")
    with open(fontlist_path, "w", encoding="utf-8") as f:
        f.write(
            "/* Kana font families (h = slices; base = PUA D4 + dakuten) */\n"
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
    face_anchors = {k: v for k, v in base_anchors.items() if k in glyphs}
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
            f"  Compiling GPOS (dakuten @ {len(face_anchors)} contour "
            f"forms, incl. overlay/slice ligas)...",
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


_WORKER_CACHE: Optional[dict] = None


def _init_kana_worker(cache_dir: str) -> None:
    global _WORKER_CACHE
    manifest_path = os.path.join(cache_dir, "manifest.pkl")
    glyf_path = os.path.join(cache_dir, "glyf.pkl")
    with open(manifest_path, "rb") as f:
        manifest = pickle.load(f)
    with open(glyf_path, "rb") as f:
        glyf = pickle.load(f)
    _WORKER_CACHE = {**manifest, **glyf}


def _kana_face_task(
    spec: Tuple[str, Optional[int]],
) -> Tuple[str, str, int, List[int], str]:
    """Process-pool worker: subset + slices + TTF for one kana face."""
    assert _WORKER_CACHE is not None
    m = _WORKER_CACHE
    kind, bucket_id = spec
    glyph_order = m["glyph_order"]
    glyphs = m["glyphs"]
    metrics = m["metrics"]
    cmap = m["cmap"]
    if kind == "h":
        assert bucket_id is not None
        keep: Set[str] = {".notdef", *m["dakuten_keep"]}
        for cp, name in cmap.items():
            if (cp >> 8) == bucket_id:
                keep.add(name)
        go, gl, mt, cm = subset_glyph_tables(glyph_order, glyphs, metrics, cmap, keep)
        print(
            f"  Slice face {h_bucket_face_id(bucket_id)} "
            f"({sum(1 for cp in cm if (cp >> 8) == bucket_id)} CPs)...",
            flush=True,
        )
        _add_kana_slices(
            full_bases=m["full_bases"],
            small_bases=m["small_bases"],
            hw_full_bases=m["hw_full_bases"],
            hw_small_bases=m["hw_small_bases"],
            glyph_order=go,
            glyphs=gl,
            metrics=mt,
            target_upem=m["target_upem"],
        )
        inject_slice_marks(go, gl, mt, cm)
        face_id = h_bucket_face_id(bucket_id)
        variant = "h"
        slices = True
    else:
        go, gl, mt, cm = subset_glyph_tables(
            glyph_order, glyphs, metrics, cmap, set(glyph_order)
        )
        face_id = PS_NAME
        variant = ""
        slices = False
    meta = _save_kana_face(
        face_id=face_id,
        variant=variant,
        glyph_order=go,
        glyphs=gl,
        metrics=mt,
        cmap=cm,
        full_bases=m["full_bases"],
        small_bases=m["small_bases"],
        hw_full_bases=m["hw_full_bases"],
        hw_small_bases=m["hw_small_bases"],
        mark_names=m["mark_names"],
        mark_cps=m["mark_cps"],
        base_anchors=m["base_anchors"],
        out_dir=m["out_dir"],
        target_upem=m["target_upem"],
        mark_ink_height=m.get("mark_ink_height"),
        mark_contour_points=m.get("mark_contour_points"),
        slices=slices,
    )
    return (*meta, os.path.join(m["out_dir"], f"{meta[0]}.ttf"))


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

    flop = [SourceFont(path) for path in flop_paths]
    mkana = SourceFont(mkana_path)
    genseki = SourceFont(genseki_path)
    lxgw = [SourceFont(path) for path in lxgw_paths]
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
            f"Stage 1/4: installing {len(source_cps)} logical kana × {D4_COUNT} D4 "
            f"(no stem-normalize) + smalls "
            f"(ideo-scale {SMALL_WIDTH_FACTOR:g} + Weight once, "
            f"D4 @ post-scale ideo center)...",
            flush=True,
        )
        for logical, src_cp in enumerate(source_cps):
            try:
                src, gname = claim_source_cp(src_cp, flop, mkana, genseki, lxgw)
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

                anchor_names = kana_coord_liga_names(
                    [
                        *full_bases,
                        *small_bases,
                        *hw_full_bases,
                        *hw_small_bases,
                    ],
                    glyphs=glyphs,
                )
                print(
                    f"Stage 1/4: dakuten anchors ({len(anchor_names)} forms, "
                    f"{workers} chunk workers, sharded pickle cache)...",
                    flush=True,
                )
                t_anchors = time.perf_counter()
                base_anchors = collect_kana_dakuten_anchors(
                    anchor_names,
                    glyphs=glyphs,
                    glyph_set=glyphs,
                    target_upem=target_upem,
                    mark_ink_height=mark_ink_h,
                    mark_points=mark_contour_pts,
                    jobs=workers,
                    cache_dir=cache_dir,
                )
                print(
                    f"  stage 1 done in {time.perf_counter() - t_anchors:.1f}s "
                    f"({len(base_anchors)} bases)",
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

            built: List[Tuple[str, str, int, List[int]]] = []
            os.makedirs(out_dir, exist_ok=True)
            face_specs: List[Tuple[str, Optional[int]]] = []
            if "" in want:
                face_specs.append(("", None))
            dakuten_keep = {n for n in glyph_order if ".mk" in n}
            if "h" in want:
                pages = sorted(
                    {
                        cp >> 8
                        for cp in cmap
                        if (PUA_START <= cp <= PUA_END)
                        or (HW_PUA_START <= cp <= HW_PUA_LAST)
                    }
                )
                for bucket_id in pages:
                    face_specs.append(("h", bucket_id))
            if not face_specs:
                return built

            glyf_path = os.path.join(cache_dir, "glyf.pkl")
            manifest_path = os.path.join(cache_dir, "manifest.pkl")
            print("  Writing glyf + manifest cache...", flush=True)
            t_cache = time.perf_counter()
            with open(glyf_path, "wb") as f:
                pickle.dump(
                    {
                        "glyph_order": glyph_order,
                        "glyphs": glyphs,
                        "metrics": metrics,
                        "cmap": cmap,
                    },
                    f,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            with open(manifest_path, "wb") as f:
                pickle.dump(
                    {
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
                    },
                    f,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            print(
                f"  cache written in {time.perf_counter() - t_cache:.1f}s",
                flush=True,
            )
            t0 = time.perf_counter()
            pool_workers = min(workers, max(1, len(face_specs)))
            print(
                f"Stage 2/4: face TTFs ({len(face_specs)} jobs, "
                f"{pool_workers} workers)...",
                flush=True,
            )
            with ProcessPoolExecutor(
                max_workers=pool_workers,
                initializer=_init_kana_worker,
                initargs=(cache_dir,),
            ) as executor:
                results = list(executor.map(_kana_face_task, face_specs))
                print(
                    f"  stage 2 done in {time.perf_counter() - t0:.1f}s",
                    flush=True,
                )
                ttf_paths = [r[4] for r in results]
                built = [(r[0], r[1], r[2], r[3]) for r in results]
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
        for name in os.listdir(out_dir):
            stem, ext = os.path.splitext(name)
            if ext.lower() not in {".woff2", ".ttf"}:
                continue
            if stem != PS_NAME and parse_h_bucket_face_id(stem) is None:
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
            "Build edenia kana (PUA D4 + dakuten) and edenia kana h "
            "(pigeonholed FE00/FE08–F slices)"
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
    p.add_argument(
        "--base-only",
        action="store_true",
        help="Build only the PUA D4 face (skip slice h pigeonholes)",
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
    add_no_hint_argument(p)
    add_jobs_argument(p)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    variants: Tuple[str, ...] = ("",) if args.base_only else ("", "h")
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
