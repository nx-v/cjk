#!/usr/bin/env python3
"""Edenia font test Markdown — random prose paragraphs only.

Japanese-like clause structure with Edenian role map:
  hiragana → kana (one script: both chart halves mix freely; full/hw = separate runs)
  hiragana-frequency yi → yi (separate script; never mixed with kana)
  katakana → hangul (conjoining jamo: upright `edenia hangul`; sideways
             `edenia hanguls` with U+FE05 per cluster)
  kanji    → Han + Tangut + Khitan + precomposed Hangul (`build_cjk` ranges;
             optional U+FE05 per cluster when sideways faces enabled)

Word kinds:
  • inflected core — 1+ kanji/hangul components (phrasal-verb style); each may
    take fullwidth kana/yi okurigana (~92%) via prefix / suffix / circumfix / infix
  • kanji·hangul sequence — CJK priority; precomposed Hangul (`edenia cjk`,
    sporadic FE05) and conjoining jamo (`edenia hangul` / `edenia hanguls`)
  • particle — short standalone fullwidth kana or yi run
  • halfwidth kana — own sticky run (never okurigana / particles / yi mix)

Usage:
  python Scripts/generate_edenian_md.py
  python Scripts/generate_edenian_md.py --lines 64 --seed 1 --out Scripts/dist/Edenian-test.md
  python Scripts/generate_edenian_md.py --sentences 1 3 --phrases 2 5 --words 3 10
  python Scripts/generate_edenian_md.py --base
  python Scripts/generate_edenian_md.py --kana --h
  python Scripts/generate_edenian_md.py --hangul --hangul-s --lines 32
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, TypeVar

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_cjk import CHAR_RANGES, HANGUL_SYLLABLE_RANGES  # noqa: E402
from build_hangul import SIDEWAYS_CP  # noqa: E402
from build_kana import (  # noqa: E402
    D4_COUNT,
    HIRAGANA_COUNT,
    HIRAGANA_PHONETIC_COUNT,
    KATAKANA_PHONETIC_COUNT,
    VOWELS,
    full_cp,
    hw_full_cp,
    hw_small_cp,
    pair_index,
    small_cp,
)
from edenia_names import (  # noqa: E402
    ordered_cjk_variants,
    ordered_segment_variants,
    segment_variant_from_token,
)
from hangul_diacritics import (  # noqa: E402
    CGJ_CP,
    DAKUTEN_SLOT_COUNT,
    iter_dakuten_codepoints,
    visible_dakuten_cps,
)
from shared_cells import OV_SELECTOR_CP  # noqa: E402

# ---------------------------------------------------------------------------
# Constants / weights (JP-like targets)
# ---------------------------------------------------------------------------

WEIGHT_KANJI_ROLE = 30.0
WEIGHT_KATAKANA_ROLE = 8.0
WEIGHT_SYMBOL_ISLAND = 5.0

# JP-hiragana-like budget (~40), split into separate kana vs yi scripts
WEIGHT_HIRA_LIKE = 40.0
KANA_SHARE_OF_HIRA_LIKE = 0.88  # kana ≫ yi; scripts never share a run
YI_SHARE_OF_HIRA_LIKE = 0.12

# Word kinds (phrase atoms) — okurigana cores ≫ particles ≫ kanji compounds
WEIGHT_WORD_INFLECTED = 70.0  # kanji/hangul core ± okurigana
WEIGHT_WORD_SEQUENCE = 32.0  # kanji-priority · hangul-sporadic sequence
WEIGHT_WORD_PARTICLE = 38.0  # short standalone fullwidth kana / yi

# Okurigana attaches to most lexical cores
OKURI_P = 0.92

# Small follow / length / gemination on phonetic kana syllables
KANA_SMALL_P = 0.05  # large may be followed by one small
KANA_LENGTH_P = 0.10  # length trailer after small (or large if no small)
KANA_GEMINATION_P = 0.07  # gemination before large if onset ≠ ∅

# Halfwidth vs fullwidth kana runs: hw is 90% less frequent → weight 0.1 vs 1.0
KANA_HW_REL_WEIGHT = 0.1

WEIGHT_YI = WEIGHT_HIRA_LIKE * YI_SHARE_OF_HIRA_LIKE
WEIGHT_KANA_TOTAL = WEIGHT_HIRA_LIKE * KANA_SHARE_OF_HIRA_LIKE
WEIGHT_KANA_FULL = WEIGHT_KANA_TOTAL * (1.0 / (1.0 + KANA_HW_REL_WEIGHT))
WEIGHT_KANA_HW = WEIGHT_KANA_TOTAL * (KANA_HW_REL_WEIGHT / (1.0 + KANA_HW_REL_WEIGHT))
# Halfwidth kana is its own run (never mixed into okurigana / particles)
WEIGHT_WORD_KANA_HW = WEIGHT_KANA_HW

# Half-slice digraph chance when base+h are both enabled (else plain or always-half).
SLICE_HALF_P = 0.12

# Script-island length: each extra syllable continues with this probability
# (geometric / exponential falloff — longer runs are rarer).
SCRIPT_RUN_RATIO = 0.42
SCRIPT_RUN_MAX = 8  # max sticky run length for any script island

# Generator faces: identity/base and/or half-cell slices (FE08–FE0F). No t/q.
FACE_DEFAULTS = frozenset(("", "h"))

# Hangul faces: `""` upright (edenia hangul); `s` sideways (+ FE05, edenia hanguls).
HANGUL_DEFAULT_FACES = frozenset({"", "s"})
HANGUL_SIDEWAYS_P = 0.30  # when both faces enabled in one run
# Precomposed Hangul (`edenia cjk`) in kanji·hangul sequences when `--cjk`.
# May append U+FE05 per cluster when `--hangul-s` / sideways faces enabled.
CJK_PRECOMPOSED_HANGUL_P = 0.12

FALLBACK_MARKS = tuple("\u3099\u309a\uff9e\uff9f\u0308\u0301\u0300\u0302\u0304\u0306")

FE_D4 = tuple(chr(c) for c in range(0xFE01, 0xFE08))
# Hangul axis mirrors: FE01–FE03 only. U+FE00 is reserved exclusively as the
# overlay joiner inside half digraphs (kana / yi / CJK FE08–FE0F) —
# never identity VS, never unsliced cluster glue, never stacked on itself.
FE_HANGUL_MIRROR = tuple(chr(c) for c in range(0xFE01, 0xFE04))
FE04 = "\ufe04"
FE05 = chr(SIDEWAYS_CP)  # sideways jamo cluster selector (edenia hanguls)
CA = "\U00016ff0"
NHAY = "\U00016ff1"
CGJ = chr(CGJ_CP)
OV = chr(OV_SELECTOR_CP)
ZWSP = "\u200b"  # zero-width space — prefixes every slice compound

# ---------------------------------------------------------------------------
# Half-cell slice tilings (kana / yi / kanji) — FE08–FE0F only
#
# Plain syllable: [cp][d4]? [diacritics]{0,8}
# Half compound (always a complementary 2-tile cover of the unit square):
#   ZWSP [cp][d4]?[slice] FE00 [cp][d4]?[slice] [diacritics]{0,8}
#
# FE00 only ever follows a half/triangle slice selector — never a bare glyph.
# Third (t) and quarter (q/qv/qh) faces are not emitted by this generator.
# ---------------------------------------------------------------------------

# Complementary Δ pairs (h face)
TRIANGLE_TILES: Tuple[Tuple[int, ...], ...] = (
    (0xFE0C, 0xFE0D),  # TL | BR
    (0xFE0E, 0xFE0F),  # TR | BL
)

# Axis half pairs (h face) — also used for kanji digraphs
HALF_RECT_TILES: Tuple[Tuple[int, ...], ...] = (
    (0xFE08, 0xFE09),  # top | bottom
    (0xFE0A, 0xFE0B),  # left | right
)

# Weighted half-cover families (both on face `h`)
_HALF_COVERS: Tuple[Tuple[str, Tuple[Tuple[int, ...], ...], float], ...] = (
    ("h", HALF_RECT_TILES, 0.60),
    ("tri", TRIANGLE_TILES, 0.40),
)

SUZHOU = tuple("〇〡〢〣〤〥〦〧〨〩〸〹〺")
IDS_BINARY = tuple("⿰⿱⿴⿵⿶⿷⿸⿹⿺⿻")
IDS_TERNARY = tuple("⿲⿳")

# ---------------------------------------------------------------------------
# Punctuation — each mark / pair has its own absolute (sporadic) probability.
# When several of a class fire, one is chosen weighted by those probabilities.
# ---------------------------------------------------------------------------

# Between phrases (clause joiners)
MID_CLAUSE_P: Tuple[Tuple[str, float], ...] = (
    ("、", 0.48),
    ("，", 0.07),
    ("．", 0.04),
    ("・", 0.03),
    ("；", 0.015),
    ("：", 0.012),
)

# Sentence / paragraph terminators
TERMINATOR_P: Tuple[Tuple[str, float], ...] = (
    ("。", 0.78),
    ("！", 0.055),
    ("？", 0.045),
    ("‼", 0.008),
    ("⁇", 0.006),
    ("⁈", 0.005),
    ("⁉", 0.005),
)

# Bracket pairs (open, close, p) — wrap a word/island when they fire
BRACKET_P: Tuple[Tuple[str, str, float], ...] = (
    ("「", "」", 0.045),
    ("『", "』", 0.012),
    ("（", "）", 0.025),
    ("〈", "〉", 0.010),
    ("《", "》", 0.008),
    ("【", "】", 0.010),
    ("〔", "〕", 0.006),
    ("〖", "〗", 0.003),
    ("〘", "〙", 0.003),
    ("〚", "〛", 0.002),
    ("［", "］", 0.008),
    ("｛", "｝", 0.005),
    ("｟", "｠", 0.002),
)

# Phrase-leading marks (checked independently)
PHRASE_LEAD_P: Tuple[Tuple[str, float], ...] = (
    ("〽", 0.035),
    ("＃", 0.012),
)

IDEO_SPACE = "\u3000"
IDEO_SPACE_P = 0.55  # between sentences inside a paragraph

# Sets for strip / membership checks
TERMINATORS = frozenset(m for m, _p in TERMINATOR_P)
MID_CLAUSE_MARKS = frozenset(m for m, _p in MID_CLAUSE_P)

FW_DIGITS = tuple("０１２３４５６７８９")
FW_UPPER = tuple(chr(c) for c in range(0xFF21, 0xFF3B))
FW_LOWER = tuple(chr(c) for c in range(0xFF41, 0xFF5B))

CJK_UNITS = tuple(
    [chr(c) for c in range(0x32CC, 0x32D0)]
    + [chr(c) for c in range(0x3371, 0x337B)]
    + [chr(c) for c in range(0x3380, 0x33E0)]
    + ["\u33ff"]
)

ENCLOSED_ORDINAL = tuple(chr(c) for c in range(0x2460, 0x2474))  # ①–⑳
ENCLOSED_MONTH = tuple(chr(c) for c in range(0x32C0, 0x32CC))  # ㋀–㋋
ENCLOSED_IDEO_LABEL = tuple(chr(c) for c in range(0x3220, 0x322A))  # ㈠–㈩
SIGNAGE = tuple(chr(c) for c in range(0x1F200, 0x1F220))  # sparse sample

YI_RANGE = (0xA000, 0xA48C)


# Single source of truth: Scripts/build_cjk.py CHAR_RANGES (incl. precomposed Hangul).
def _cjk_range_pick_weight(label: str, n_chars: int) -> float:
    """Relative chance to draw from a CHAR_RANGES block × assigned size."""
    lab = label.casefold()
    if "uro" in lab:
        base = 48.0
    elif "ext a" in lab:
        base = 10.0
    elif "ext b" in lab:
        base = 6.0
    elif "ext " in lab:
        base = 3.0
    elif "radical" in lab or "kangxi" in lab:
        base = 2.5
    elif "hangul syll" in lab:
        base = 8.0
    elif "hangul compat" in lab or "compatibility jamo" in lab:
        base = 6.0
    elif "tangut" in lab:
        base = 5.0
    elif "khitan" in lab:
        base = 2.5
    elif "compat" in lab:
        base = 2.0
    elif "kanbun" in lab or "stroke" in lab:
        base = 0.8
    else:
        base = 2.0
    return base * float(n_chars)


# Assigned filtering uses UCD 18 (stdlib unicodedata is older).
UCD_VERSION = "18.0.0"
UCD_UNICODEDATA_PATH = SCRIPT_DIR / "data" / f"UnicodeData-{UCD_VERSION}.txt"
UCD_UNICODEDATA_URL = (
    f"https://www.unicode.org/Public/{UCD_VERSION}/ucd/UnicodeData.txt"
)


def _ensure_unicode_data() -> Path:
    path = UCD_UNICODEDATA_PATH
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[generate_edenian_md] fetching UnicodeData.txt {UCD_VERSION}…")
    urllib.request.urlretrieve(UCD_UNICODEDATA_URL, path)
    return path


def _load_ucd_assigned_intervals(path: Path) -> List[Tuple[int, int]]:
    """Inclusive assigned intervals from UnicodeData.txt (incl. First/Last runs)."""
    intervals: List[Tuple[int, int]] = []
    pending_first: Optional[int] = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            fields = line.split(";")
            if len(fields) < 2:
                continue
            cp = int(fields[0], 16)
            name = fields[1]
            if name.endswith(", First>"):
                pending_first = cp
            elif name.endswith(", Last>"):
                if pending_first is None:
                    raise ValueError(f"UCD Last without First at U+{cp:04X}")
                intervals.append((pending_first, cp))
                pending_first = None
            else:
                intervals.append((cp, cp))
    if pending_first is not None:
        raise ValueError(f"UCD First without Last at U+{pending_first:04X}")
    return intervals


def _assigned_cps_in_range(
    start: int, end: int, intervals: Sequence[Tuple[int, int]]
) -> Tuple[int, ...]:
    out: List[int] = []
    for a, b in intervals:
        lo = max(a, start)
        hi = min(b, end)
        if lo <= hi:
            out.extend(range(lo, hi + 1))
    return tuple(out)


_UCD_INTERVALS = _load_ucd_assigned_intervals(_ensure_unicode_data())

# (assigned codepoints, pick weight) — empty/unassigned slots dropped
CJK_SAMPLE_POOLS: Tuple[Tuple[Tuple[int, ...], float], ...] = tuple(
    (cps, _cjk_range_pick_weight(name, len(cps)))
    for start, end, name in CHAR_RANGES
    for cps in (_assigned_cps_in_range(start, end, _UCD_INTERVALS),)
    if cps
)
CJK_ASSIGNED_COUNT = sum(len(cps) for cps, _ in CJK_SAMPLE_POOLS)

# Precomposed Hangul only (syllables U+AC00..D7A3 + compat jamo U+3131..318E).
CJK_HANGUL_SAMPLE_POOLS: Tuple[Tuple[Tuple[int, ...], float], ...] = tuple(
    (cps, _cjk_range_pick_weight(name, len(cps)))
    for start, end, name in HANGUL_SYLLABLE_RANGES
    for cps in (_assigned_cps_in_range(start, end, _UCD_INTERVALS),)
    if cps
)
CJK_HANGUL_ASSIGNED_COUNT = sum(len(cps) for cps, _ in CJK_HANGUL_SAMPLE_POOLS)

# Hangul jamo: packed as char+length digit (same as JS)
_HANGUL_CHO = (
    "ᄀ1ᄁ2ᄂ1ᄃ1ᄄ2ᄅ1ᄆ1ᄇ1ᄈ2ᄉ1"
    "ᄊ2ᄋ1ᄌ1ᄍ2ᄎ1ᄏ1ᄐ1ᄑ1ᄒ1ᄓ2"
    "ᄔ2ᄕ2ᄖ2ᄗ2ᄘ2ᄙ2ᄚ2ᄛ1ᄜ2ᄝ1"
    "ᄞ2ᄟ2ᄠ2ᄡ2ᄢ3ᄣ3ᄤ3ᄥ3ᄦ3ᄧ2"
    "ᄨ2ᄩ2ᄪ2ᄫ2ᄬ2ᄭ2ᄮ2ᄯ2ᄰ2ᄱ2"
    "ᄲ2ᄳ3ᄴ3ᄵ2ᄶ2ᄷ2ᄸ2ᄹ2ᄺ2ᄻ2"
    "ᄼ1ᄽ2ᄾ1ᄿ2ᅀ1ᅁ2ᅂ2ᅃ2ᅄ2ᅅ2"
    "ᅆ2ᅇ2ᅈ2ᅉ2ᅊ2ᅋ2ᅌ1ᅍ2ᅎ1ᅏ2"
    "ᅐ1ᅑ2ᅒ2ᅓ2ᅔ1ᅕ1ᅖ2ᅗ1ᅘ2ᅙ1"
    "ᅚ2ᅛ2ᅜ2ᅝ2ᅞ2ꥠ2ꥡ2ꥢ2ꥣ2ꥤ2"
    "ꥥ3ꥦ2ꥧ3ꥨ2ꥩ2ꥪ3ꥫ2ꥬ2ꥭ2ꥮ2"
    "ꥯ2ꥰ2ꥱ2ꥲ3ꥳ2ꥴ2ꥵ3ꥶ2ꥷ2ꥸ3"
    "ꥹ2ꥺ2ꥻ2ꥼ2ᅟ1"
)
_HANGUL_JUNG = (
    "ᅡ1ᅢ2ᅣ1ᅤ2ᅥ1ᅦ2ᅧ1ᅨ2ᅩ1ᅪ2"
    "ᅫ3ᅬ2ᅭ1ᅮ1ᅯ2ᅰ3ᅱ2ᅲ1ᅳ1ᅴ2"
    "ᅵ1ᅶ2ᅷ2ᅸ2ᅹ2ᅺ2ᅻ2ᅼ2ᅽ2ᅾ2"
    "ᅿ2ᆀ3ᆁ3ᆂ2ᆃ2ᆄ2ᆅ2ᆆ2ᆇ2ᆈ2"
    "ᆉ2ᆊ2ᆋ3ᆌ3ᆍ2ᆎ2ᆏ2ᆐ3ᆑ2ᆒ3"
    "ᆓ2ᆔ2ᆕ2ᆖ2ᆗ3ᆘ2ᆙ2ᆚ2ᆛ2ᆜ2"
    "ᆝ2ᆞ1ᆟ2ᆠ2ᆡ2ᆢ2ᆣ2ᆤ2ᆥ2ᆦ2"
    "ᆧ3ힰ2ힱ3ힲ2ힳ3ힴ2ힵ2ힶ3ힷ3ힸ2"
    "ힹ2ힺ2ힻ3ힼ2ힽ3ힾ3ힿ2ퟀ3ퟁ2ퟂ2"
    "ퟃ2ퟄ2ퟅ2ퟆ3ᅠ1"
)
_HANGUL_JONG = (
    "ᆨ1ᆩ2ᆪ2ᆫ1ᆬ2ᆭ2ᆮ1ᆯ1ᆰ2ᆱ2"
    "ᆲ2ᆳ2ᆴ2ᆵ2ᆶ2ᆷ1ᆸ1ᆹ2ᆺ1ᆻ2"
    "ᆼ1ᆽ1ᆾ1ᆿ1ᇀ1ᇁ1ᇂ1ᇃ2ᇄ3ᇅ2"
    "ᇆ2ᇇ2ᇈ2ᇉ2ᇊ2ᇋ2ᇌ3ᇍ2ᇎ2ᇏ3"
    "ᇐ2ᇑ3ᇒ3ᇓ3ᇔ3ᇕ2ᇖ3ᇗ2ᇘ2ᇙ2"
    "ᇚ2ᇛ2ᇜ2ᇝ2ᇞ3ᇟ2ᇠ2ᇡ2ᇢ1ᇣ2"
    "ᇤ2ᇥ2ᇦ1ᇧ2ᇨ2ᇩ2ᇪ2ᇫ1ᇬ2ᇭ3"
    "ᇮ2ᇯ2ᇰ1ᇱ2ᇲ2ᇳ2ᇴ1ᇵ2ᇶ2ᇷ2"
    "ᇸ2ᇹ1ᇺ2ᇻ2ᇼ2ᇽ2ᇾ2ᇿ2ퟋ2ퟌ2"
    "ퟍ2ퟎ3ퟏ2ퟐ2ퟑ3ퟒ2ퟓ2ퟔ2ퟕ3ퟖ3"
    "ퟗ3ퟘ3ퟙ3ퟚ3ퟛ2ퟜ3ퟝ1ퟞ2ퟟ3ퟠ2"
    "ퟡ3ퟢ2ퟣ2ퟤ3ퟥ2ퟦ2ퟧ3ퟨ2ퟩ2ퟪ2"
    "ퟫ2ퟬ3ퟭ2ퟮ2ퟯ2ퟰ2ퟱ2ퟲ2ퟳ2ퟴ2"
    "ퟵ2ퟶ2ퟷ2ퟸ3ퟹ2ퟺ2ퟻ2"
)
_HANGUL_W = (6, 3, 1)
# Choseong / jungseong fillers (U+115F / U+1160) — packed length digit 1
FILLER_L = "\u115f"
FILLER_V = "\u1160"
_HANGUL_LEN1_W = _HANGUL_W[0]  # digit "1" → weight 6


def _expand_jamo(packed: str) -> List[str]:
    out: List[str] = []
    i = 0
    while i + 1 < len(packed):
        ch, dig = packed[i], packed[i + 1]
        w = _HANGUL_W[int(dig) - 1]
        out.extend([ch] * w)
        i += 2
    return out


def _with_filler(pool: List[str], filler: str) -> List[str]:
    """Ensure `filler` is present at length-1 weight even if `pool` was empty."""
    if not pool:
        return [filler] * _HANGUL_LEN1_W
    if filler not in pool:
        return list(pool) + [filler] * _HANGUL_LEN1_W
    return pool


CHOSEONG = _with_filler(_expand_jamo(_HANGUL_CHO), FILLER_L)
JUNGSEONG = _with_filler(_expand_jamo(_HANGUL_JUNG), FILLER_V)
JONGSEONG = _expand_jamo(_HANGUL_JONG)


# ---------------------------------------------------------------------------
# Structure bounds / RNG helpers
# ---------------------------------------------------------------------------


T = TypeVar("T")


@dataclass(frozen=True)
class StructureBounds:
    """Discrete sentence → phrase → word counts (inclusive min/max)."""

    sentences_min: int = 2
    sentences_max: int = 6
    phrases_min: int = 1
    phrases_max: int = 4
    words_min: int = 2
    words_max: int = 8

    def validate(self) -> None:
        pairs = (
            ("sentences", self.sentences_min, self.sentences_max),
            ("phrases", self.phrases_min, self.phrases_max),
            ("words", self.words_min, self.words_max),
        )
        for name, lo, hi in pairs:
            if lo < 1:
                raise SystemExit(f"--{name} min must be >= 1 (got {lo})")
            if hi < lo:
                raise SystemExit(f"--{name} max ({hi}) must be >= min ({lo})")


@dataclass(frozen=True)
class GenOptions:
    """Script / face filters (same idea as `run.ps1` switches)."""

    yi: bool = True
    kana: bool = True
    hangul: bool = True
    cjk: bool = True
    hangul_faces: frozenset[str] = frozenset(HANGUL_DEFAULT_FACES)
    # `""` = plain glyph (± diacritics); `h` = FE08–FE0F half compounds.
    kana_faces: frozenset[str] = frozenset(FACE_DEFAULTS)
    yi_faces: frozenset[str] = frozenset(FACE_DEFAULTS)
    cjk_faces: frozenset[str] = frozenset(FACE_DEFAULTS)

    def has_half(self, faces: frozenset[str]) -> bool:
        return "h" in faces


class Gen:
    def __init__(
        self,
        rng: random.Random,
        marks: Sequence[str],
        bounds: Optional[StructureBounds] = None,
        opts: Optional[GenOptions] = None,
    ) -> None:
        self.rng = rng
        self.marks = list(marks) if marks else list(FALLBACK_MARKS)
        self.bounds = bounds or StructureBounds()
        self.opts = opts or GenOptions()
        print(
            f"{len(CJK_SAMPLE_POOLS)} CJK ranges / {CJK_ASSIGNED_COUNT:,} assigned "
            f"(Han+Tangut+Khitan+precomp Hangul; Unicode {UCD_VERSION}; "
            f"Hangul syll+compat {CJK_HANGUL_ASSIGNED_COUNT:,})"
        )
        scripts = [
            s
            for s, on in (
                ("yi", self.opts.yi),
                ("kana", self.opts.kana),
                ("hangul", self.opts.hangul),
                ("cjk", self.opts.cjk),
            )
            if on
        ]
        print(
            f"[generate_edenian_md] scripts={'+'.join(scripts) or '(none)'} "
            f"kana_faces={_faces_label(self.opts.kana_faces)} "
            f"yi_faces={_faces_label(self.opts.yi_faces)} "
            f"cjk_faces={_faces_label(self.opts.cjk_faces)} "
            f"hangul_faces={_hangul_faces_label(self.opts.hangul_faces)}"
        )

    def choice(self, seq: Sequence):
        return self.rng.choice(seq)

    def random(self) -> float:
        return self.rng.random()

    def randint(self, a: int, b: int) -> int:
        return self.rng.randint(a, b)

    def island_len(self, lo: int, hi: int, *, ratio: float = SCRIPT_RUN_RATIO) -> int:
        """Script-island length in `[lo, hi]` with exponential falloff.

        Starts at `lo`; each extra unit continues with probability `ratio`.
        """
        if hi < lo:
            raise ValueError(f"island_len: hi ({hi}) < lo ({lo})")
        n = lo
        while n < hi and self.chance(ratio):
            n += 1
        return n

    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def pick_hangul_sideways(self) -> bool:
        """Pick sideways jamo (`edenia hanguls` + FE05) vs upright."""
        faces = self.opts.hangul_faces
        if not faces or faces == frozenset({""}):
            return False
        if faces == frozenset({"s"}):
            return True
        return self.chance(HANGUL_SIDEWAYS_P)

    def weighted_choice(self, items: Sequence[Tuple[T, float]]) -> T:
        total = sum(w for _, w in items)
        if total <= 0:
            raise ValueError("weighted_choice: total weight must be > 0")
        x = self.rng.random() * total
        for val, w in items:
            x -= w
            if x <= 0:
                return val
        return items[-1][0]

    def pick_sporadic(self, items: Sequence[Tuple[T, float]]) -> Optional[T]:
        """Each item fires with its own probability; pick among firers by weight.

        Returns `None` when nothing fires (punctuation stays absent).
        """
        hits = [(val, p) for val, p in items if p > 0 and self.chance(p)]
        if not hits:
            return None
        return self.weighted_choice(hits)

    def ideograph(self) -> str:
        return chr(self.choice(self.weighted_choice(CJK_SAMPLE_POOLS)))

    def precomposed_hangul(self) -> str:
        """One precomposed syllable or compat jamo from `edenia cjk` inventory."""
        if not CJK_HANGUL_SAMPLE_POOLS:
            return self.ideograph()
        return chr(self.choice(self.weighted_choice(CJK_HANGUL_SAMPLE_POOLS)))


def _faces_label(faces: frozenset[str]) -> str:
    parts = [
        ("base" if v == "" else v) for v in sorted(faces, key=lambda x: (x != "", x))
    ]
    return ",".join(parts) if parts else "-"


def _hangul_faces_label(faces: frozenset[str]) -> str:
    parts = [
        ("base" if v == "" else "s" if v == "s" else v)
        for v in sorted(faces, key=lambda x: (x != "", x))
    ]
    return ",".join(parts) if parts else "-"


def load_combining_marks() -> List[str]:
    candidates = [
        SCRIPT_DIR / "dist" / "hangul" / "edenia-hangul.woff2",
        SCRIPT_DIR / "dist" / "hangul" / "edenia-hanguls.woff2",
        SCRIPT_DIR / "dist" / "yi" / "edenia-yi.woff2",
        SCRIPT_DIR / "dist" / "kana" / "edenia-kana.woff2",
        SCRIPT_DIR / "obsidian-edenia" / "edenia" / "hangul" / "edenia-hangul.woff2",
    ]
    font_path = next((p for p in candidates if p.is_file()), None)
    if font_path is None:
        print("[generate_edenian_md] no Edenia font for mark inventory; using fallback")
        return list(FALLBACK_MARKS)
    try:
        from fontTools.ttLib import TTFont

        tt = TTFont(str(font_path))
        cmap: dict[int, str] = {}
        for table in tt["cmap"].tables:
            if table.isUnicode():
                cmap.update(table.cmap)
        tt.close()
        cps = visible_dakuten_cps(iter_dakuten_codepoints(cmap))
        if not cps:
            print("[generate_edenian_md] empty mark inventory; using fallback")
            return list(FALLBACK_MARKS)
        print(
            f"[generate_edenian_md] {len(cps)} combining marks from "
            f"{font_path.relative_to(REPO_ROOT)}"
        )
        return [chr(cp) for cp in cps]
    except Exception as exc:  # noqa: BLE001
        print(f"[generate_edenian_md] mark inventory failed: {exc}")
        return list(FALLBACK_MARKS)


# ---------------------------------------------------------------------------
# Diacritics / slices
# ---------------------------------------------------------------------------


def attach_diacritics(g: Gen, base: str) -> str:
    """0–8 slots: visible marks + CGJ skips (builder semantics)."""
    if not g.chance(0.28):
        return base
    # Bias low counts: geometric over 1..8
    slots = 1
    while slots < DAKUTEN_SLOT_COUNT and g.chance(0.35):
        slots += 1
    s = base
    used = 0
    while used < slots:
        if g.chance(0.18) and used < slots:
            s += CGJ
            used += 1
            continue
        if used < slots:
            s += g.choice(g.marks)
            used += 1
    return s


def _slice_tile(base: str, vs: int) -> str:
    """One compound tile: unsliced base + slice selector (never bare `cp FE00`)."""
    base = base.replace(OV, "")
    if not base:
        raise ValueError("slice tile base must be non-empty")
    return base + chr(vs)


def _stack_segments(parts: Sequence[Tuple[str, int]]) -> str:
    """Compose a complementary half-cell digraph (sole FE00 emitter).

    Format: `ZWSP tile FE00 tile` where each tile is `[cp][d4]?[slice]`.
    FE00 only ever follows a half/triangle slice — never a bare full glyph.
    """
    if len(parts) != 2:
        raise ValueError(f"half cover must have exactly 2 tiles, got {len(parts)}")

    tiles = [_slice_tile(base, vs) for base, vs in parts]
    return ZWSP + tiles[0] + OV + tiles[1]


def _tiled_multigraph(g: Gen, mk: Callable[[], str], vs_tile: Sequence[int]) -> str:
    """Build a half digraph from a complementary 2-tile cover (order shuffled)."""
    if len(vs_tile) != 2:
        raise ValueError(f"half cover must have 2 tiles, got {len(vs_tile)}")
    tiles = list(vs_tile)
    for _ in range(12):
        g.rng.shuffle(tiles)
        try:
            return _stack_segments([(mk(), vs) for vs in tiles])
        except ValueError:
            continue
    return _stack_segments([(mk(), vs) for vs in tiles])


def _want_half_compound(g: Gen, faces: frozenset[str]) -> bool:
    """True → emit a complementary FE08–FE0F digraph; False → plain glyph."""
    allow_plain = "" in faces
    allow_half = g.opts.has_half(faces)
    if not allow_half:
        return False
    if not allow_plain:
        return True
    return g.chance(SLICE_HALF_P)


def _pick_half_cover(g: Gen) -> Sequence[int]:
    """Pick a complementary half/triangle cover (always 2 tiles on face `h`)."""
    names_weights = [(n, w) for n, _c, w in _HALF_COVERS]
    pick = g.weighted_choice(tuple(names_weights))
    for name, covers, _w in _HALF_COVERS:
        if name == pick:
            return g.choice(covers)
    return g.choice(_HALF_COVERS[0][1])


def _slice_multigraph(
    g: Gen,
    mk: Callable[[], str],
    *,
    faces: frozenset[str],
    plain: Optional[Callable[[], str]] = None,
) -> str:
    """Plain glyph (± diacritics) or complementary half digraph + diacritics.

    `plain` defaults to `mk`; kana passes `kana_cluster` so length/gem
    markers stay on unsliced syllables only.
    """
    if not _want_half_compound(g, faces):
        return attach_diacritics(g, (plain or mk)())
    return attach_diacritics(g, _tiled_multigraph(g, mk, _pick_half_cover(g)))


# ---------------------------------------------------------------------------
# Script engines
# ---------------------------------------------------------------------------


def hangul_syllable(
    g: Gen,
    *,
    with_vs: Optional[bool] = None,
    sideways: Optional[bool] = None,
) -> str:
    # L and V always present: real jamo or fillers (never an empty onset/nucleus)
    L = g.choice(CHOSEONG) if CHOSEONG else FILLER_L
    V = g.choice(JUNGSEONG) if JUNGSEONG else FILLER_V
    want_t = g.chance(0.45)
    T = g.choice(JONGSEONG) if want_t and JONGSEONG else ""
    use_vs = g.chance(0.4) if with_vs is None else with_vs
    if use_vs:
        if g.chance(0.35):
            L += g.choice(FE_HANGUL_MIRROR)
        if g.chance(0.35):
            V += g.choice(FE_HANGUL_MIRROR)
        if T and g.chance(0.35):
            T += g.choice(FE_HANGUL_MIRROR)
    s = L + V + T
    if T and g.chance(0.25):
        s += FE04
    if sideways if sideways is not None else g.pick_hangul_sideways():
        s += FE05
    return attach_diacritics(g, s)


def hangul_word(
    g: Gen, n: Optional[int] = None, *, sideways: Optional[bool] = None
) -> str:
    n = n or g.island_len(1, SCRIPT_RUN_MAX)
    if sideways is None:
        sideways = g.pick_hangul_sideways()
    return "".join(hangul_syllable(g, sideways=sideways) for _ in range(n))


def yi_base(g: Gen) -> str:
    ch = chr(g.randint(*YI_RANGE))
    if g.chance(0.45):
        ch += g.choice(FE_D4)
    return ch


def _kana_orient(g: Gen) -> int:
    return 0 if g.chance(0.55) else g.randint(0, D4_COUNT - 1)


def _kana_cp(
    logical: int,
    orient: int,
    *,
    halfwidth: bool,
    small: bool,
) -> str:
    i = pair_index(logical, orient)
    if halfwidth:
        return chr(hw_small_cp(i) if small else hw_full_cp(i))
    return chr(small_cp(i) if small else full_cp(i))


def _pick_phonetic_logical(g: Gen) -> Tuple[int, bool]:
    """Uniform phonetic cell; returns (logical, is_hiragana_half)."""
    total = HIRAGANA_PHONETIC_COUNT + KATAKANA_PHONETIC_COUNT
    idx = g.randint(0, total - 1)
    if idx < HIRAGANA_PHONETIC_COUNT:
        return idx, True
    return HIRAGANA_COUNT + (idx - HIRAGANA_PHONETIC_COUNT), False


def _is_null_onset(logical: int) -> bool:
    """True for ∅-row vowels (first consonant row of either chart half)."""
    n_vow = len(VOWELS)
    if 0 <= logical < HIRAGANA_PHONETIC_COUNT:
        return (logical // n_vow) == 0
    kata0 = HIRAGANA_COUNT
    if kata0 <= logical < kata0 + KATAKANA_PHONETIC_COUNT:
        return ((logical - kata0) // n_vow) == 0
    return False


def _trailer_logical(hira: bool, kind: str) -> int:
    """Logical index of length (0) or gemination (1) on the given chart half."""
    off = 0 if kind == "length" else 1
    if hira:
        return HIRAGANA_PHONETIC_COUNT + off
    return HIRAGANA_COUNT + KATAKANA_PHONETIC_COUNT + off


def kana_base(
    g: Gen,
    *,
    halfwidth: bool = False,
    small: bool = False,
    logical: Optional[int] = None,
) -> str:
    """One phonetic Edenia kana CP (large or small). No length/gem trailers."""
    if logical is None:
        logical, _hira = _pick_phonetic_logical(g)
    return _kana_cp(logical, _kana_orient(g), halfwidth=halfwidth, small=small)


def kana_cluster(g: Gen, *, halfwidth: bool = False) -> str:
    """Phonetic syllable cluster::

        [gemination]?  large  [small]?  [length]?

    Gemination only if large is not null-onset (∅ row). Length follows small
    when present. Rates: small≈5%, length≈10%, gemination≈7%.
    """
    logical, hira = _pick_phonetic_logical(g)
    parts: List[str] = []

    if not _is_null_onset(logical) and g.chance(KANA_GEMINATION_P):
        parts.append(
            _kana_cp(
                _trailer_logical(hira, "gemination"),
                _kana_orient(g),
                halfwidth=halfwidth,
                small=False,
            )
        )

    parts.append(_kana_cp(logical, _kana_orient(g), halfwidth=halfwidth, small=False))

    if g.chance(KANA_SMALL_P):
        # One small follow on the same chart half
        if hira:
            sm_logical = g.randint(0, HIRAGANA_PHONETIC_COUNT - 1)
        else:
            sm_logical = HIRAGANA_COUNT + g.randint(0, KATAKANA_PHONETIC_COUNT - 1)
        parts.append(
            _kana_cp(sm_logical, _kana_orient(g), halfwidth=halfwidth, small=True)
        )

    if g.chance(KANA_LENGTH_P):
        parts.append(
            _kana_cp(
                _trailer_logical(hira, "length"),
                _kana_orient(g),
                halfwidth=halfwidth,
                small=False,
            )
        )

    return "".join(parts)


def kana_syllable(g: Gen, *, halfwidth: bool = False) -> str:
    """One kana syllable (hira/kata mix freely; never yi)."""
    return _slice_multigraph(
        g,
        lambda: kana_base(g, halfwidth=halfwidth),
        faces=g.opts.kana_faces,
        plain=lambda: kana_cluster(g, halfwidth=halfwidth),
    )


def yi_syllable(g: Gen) -> str:
    """One yi syllable (never kana)."""
    return _slice_multigraph(g, lambda: yi_base(g), faces=g.opts.yi_faces)


def kana_run(
    g: Gen,
    n: Optional[int] = None,
    *,
    halfwidth: bool = False,
) -> str:
    """Kana run: both chart halves mix freely. No yi."""
    n = n or g.island_len(1, SCRIPT_RUN_MAX)
    return "".join(kana_syllable(g, halfwidth=halfwidth) for _ in range(n))


def yi_run(g: Gen, n: Optional[int] = None) -> str:
    """Sticky yi run — no kana."""
    n = n or g.island_len(1, SCRIPT_RUN_MAX)
    return "".join(yi_syllable(g) for _ in range(n))


def kanji_cluster(g: Gen) -> str:
    """One ideograph cluster (D4 / ca·nhay). No FE00 — that is slice-only."""
    a = g.ideograph()
    if g.chance(0.25):
        a += g.choice(FE_D4)
    if g.chance(0.08):
        # ca/nhay MARK slots: identity or FE01–FE07 only (not slice FE08–FE0F)
        slot = "" if g.chance(0.35) else g.choice(FE_D4)
        a += slot + (CA if g.chance(0.5) else NHAY)
    return a


def precomposed_hangul_cluster(
    g: Gen,
    *,
    sideways: Optional[bool] = None,
) -> str:
    """Precomposed Hangul cluster (syllable or compat jamo; `edenia cjk`)."""
    a = g.precomposed_hangul()
    if g.chance(0.25):
        a += g.choice(FE_D4)
    if g.chance(0.08):
        slot = "" if g.chance(0.35) else g.choice(FE_D4)
        a += slot + (CA if g.chance(0.5) else NHAY)
    s = a
    if sideways if sideways is not None else g.pick_hangul_sideways():
        s += FE05
    return s


def precomposed_hangul_word(
    g: Gen,
    n: Optional[int] = None,
    *,
    sideways: Optional[bool] = None,
) -> str:
    n = n or g.island_len(1, SCRIPT_RUN_MAX)
    if sideways is None:
        sideways = g.pick_hangul_sideways()
    return "".join(precomposed_hangul_cluster(g, sideways=sideways) for _ in range(n))


def kanji_half_digraph(g: Gen) -> str:
    """CJK slice compound: complementary half/triangle tiles + FE00 joiner."""
    if "h" not in g.opts.cjk_faces:
        return kanji_cluster(g)
    tile = g.choice(TRIANGLE_TILES if g.chance(0.35) else HALF_RECT_TILES)
    left = g.ideograph()
    right = g.ideograph()
    if g.chance(0.4):
        left += g.choice(FE_D4)
    if g.chance(0.4):
        right += g.choice(FE_D4)
    tiles = list(tile)
    g.rng.shuffle(tiles)
    return _stack_segments([(left, tiles[0]), (right, tiles[1])])


def _ids_tree(g: Gen, depth: int = 0) -> str:
    """Valid prefix IDS expression over kanji clusters."""
    if depth >= 2 or g.chance(0.55):
        return kanji_cluster(g)
    if g.chance(0.15):
        op = g.choice(IDS_TERNARY)
        return (
            op
            + _ids_tree(g, depth + 1)
            + _ids_tree(g, depth + 1)
            + _ids_tree(g, depth + 1)
        )
    op = g.choice(IDS_BINARY)
    return op + _ids_tree(g, depth + 1) + _ids_tree(g, depth + 1)


def kanji_word(g: Gen) -> str:
    """Bare kanji compound (no okurigana) — used by symbol islands."""
    n = g.island_len(1, SCRIPT_RUN_MAX)
    if n >= 2 and g.chance(0.12):
        word = _ids_tree(g)
    else:
        parts: List[str] = []
        for i in range(n):
            if i > 0 and g.chance(0.08):
                parts.append(g.choice(SUZHOU))
            if g.chance(0.04):
                parts.append("〓")
            else:
                parts.append(_kanji_piece(g, prefer_multi=(n >= 2)))
        word = "".join(parts)
    if g.chance(0.04 + 0.06 * max(0, n - 1)):
        word += "々"
    return word


def fw_digits(g: Gen) -> str:
    n = g.randint(1, 6)
    s = "".join(g.choice(FW_DIGITS) for _ in range(n))
    if g.chance(0.15) and n >= 2:
        i = g.randint(1, n - 1)
        s = s[:i] + "．" + s[i:]
    return s


def fw_letters(g: Gen) -> str:
    n = g.randint(2, 6)
    alphabet = FW_UPPER if g.chance(0.5) else FW_LOWER
    parts = [g.choice(alphabet) for _ in range(n)]
    if g.chance(0.2) and n >= 3:
        i = g.randint(1, n - 1)
        parts.insert(i, "・")
    return "".join(parts)


def digit_expr(g: Gen) -> str:
    """Role-bound numeric / currency / arithmetic island."""
    kind = g.weighted_choice(
        [
            ("plain", 0.35),
            ("pct", 0.12),
            ("currency", 0.18),
            ("hash", 0.10),
            ("postal", 0.08),
            ("arith", 0.10),
            ("unit", 0.07),
        ]
    )
    a = fw_digits(g)
    if kind == "pct":
        return a + "％"
    if kind == "currency":
        cur = g.choice(("￥", "￦", "＄", "￡", "￠"))
        if cur in "￥￦" and g.chance(0.6):
            return a + cur
        return cur + a
    if kind == "hash":
        return "＃" + a
    if kind == "postal":
        mark = g.choice(("〒", "〒", "〒", "〠", "〶"))
        return mark + a
    if kind == "unit":
        u = g.choice(CJK_UNITS) if CJK_UNITS else "％"
        if g.chance(0.3):
            return a + "〼"
        return a + u
    if kind == "arith":
        b = fw_digits(g)
        op = g.choice(("＋", "－", "＊", "／", "＾", "～", "＜", "＝", "＞"))
        return a + op + b
    if g.chance(0.08):
        return "￣" + a
    return a


def symbol_island(g: Gen) -> str:
    kind = g.weighted_choice(
        [
            ("digits", 0.45),
            ("letters", 0.25),
            ("join", 0.12),
            ("enclosed", 0.10),
            ("signage", 0.05),
            ("badge", 0.03),
        ]
    )
    if kind == "digits":
        return digit_expr(g)
    if kind == "letters":
        s = fw_letters(g)
        if g.chance(0.15):
            s = fw_letters(g) + "＠" + s
        return s
    if kind == "join":
        makers: List[Callable[[], str]] = []
        if g.opts.kana:
            makers.append(lambda: kana_run(g, 2))
        if g.opts.yi:
            makers.append(lambda: yi_run(g, 2))
        if g.opts.cjk:
            makers.append(lambda: kanji_word(g))
            if CJK_HANGUL_SAMPLE_POOLS:
                makers.append(lambda: precomposed_hangul_word(g, 2))
        if g.opts.hangul:
            makers.append(lambda: hangul_word(g, 2))
        makers.append(lambda: fw_letters(g))
        left = g.choice(makers)()
        right = g.choice(makers)()
        op = g.choice(("＆", "＠", "／", "￤"))
        return left + op + right
    if kind == "enclosed":
        return g.choice(ENCLOSED_ORDINAL + ENCLOSED_MONTH + ENCLOSED_IDEO_LABEL)
    if kind == "signage":
        return g.choice(SIGNAGE)
    return "〄" + (fw_letters(g) if g.chance(0.5) else kanji_word(g))


def maybe_bracket(g: Gen, content: str) -> str:
    """Wrap with any bracket pairs that fire (nested if several).

    Each pair has its own probability. Common pairs nest inside rarer ones.
    """
    if not content:
        return content
    hits = [(o, c, p) for o, c, p in BRACKET_P if p > 0 and g.chance(p)]
    if not hits:
        return content
    hits.sort(key=lambda t: t[2])  # ascending p → wrap first = inner
    out = content
    for open_, close, _p in hits:
        out = open_ + out + close
    return out


def okurigana_run(g: Gen, n: Optional[int] = None) -> str:
    """Short fullwidth kana or yi okurigana (never halfwidth)."""
    n = n or g.island_len(1, SCRIPT_RUN_MAX)
    choices: List[Tuple[str, float]] = []
    if g.opts.kana:
        choices.append(("kana", WEIGHT_KANA_FULL))
    if g.opts.yi:
        choices.append(("yi", WEIGHT_YI))
    if not choices:
        return ""
    if g.weighted_choice(tuple(choices)) == "yi":
        return yi_run(g, n)
    return kana_run(g, n, halfwidth=False)


def _kanji_piece(g: Gen, *, prefer_multi: bool = False) -> str:
    """One CJK atom: plain cluster and/or half digraph per `cjk_faces`."""
    faces = g.opts.cjk_faces
    allow_base = "" in faces
    allow_h = "h" in faces
    if not allow_base and not allow_h:
        return ""
    if allow_h and (not allow_base or (prefer_multi and g.chance(0.20))):
        return kanji_half_digraph(g)
    if allow_base:
        return kanji_cluster(g)
    return kanji_half_digraph(g)


def _kanji_stem_segments(g: Gen) -> List[str]:
    """Stem inside one phrasal component: usually 1 cluster, sometimes 2."""
    n = g.island_len(1, 2)
    parts: List[str] = []
    for i in range(n):
        if i > 0 and g.chance(0.06):
            parts.append(g.choice(SUZHOU))
        if g.chance(0.03):
            parts.append("〓")
        else:
            parts.append(_kanji_piece(g, prefer_multi=(n >= 2)))
    if n == 1 and g.chance(0.05):
        parts[-1] = parts[-1] + "々"
    return parts


def _hangul_stem_segments(g: Gen) -> List[str]:
    """Stem inside one hangul phrasal component."""
    n = g.island_len(1, 2)
    return [hangul_word(g, 1) for _ in range(n)]


def inflect_component(g: Gen, stem: Sequence[str]) -> str:
    """One phrasal component: stem ± okurigana (prefix/suffix/circumfix/infix).

    Okurigana attaches about `OKURI_P` of the time. Infix within a
    multi-segment stem; 〆 may hinge an infixed okuri run.
    """
    segs = [s for s in stem if s]
    if not segs:
        return okurigana_run(g, 1)

    if not g.chance(OKURI_P):
        return "".join(segs)

    want_prefix = g.chance(0.22)
    want_suffix = g.chance(0.70)  # JP-like: mostly trailing okurigana
    want_circum = g.chance(0.08)
    want_infix = g.chance(0.18) and len(segs) >= 2

    if not (want_prefix or want_suffix or want_circum or want_infix):
        want_suffix = True

    body_parts = list(segs)
    if want_infix:
        out: List[str] = [body_parts[0]]
        for piece in body_parts[1:]:
            hinge = "〆" if g.chance(0.18) else ""
            out.append(hinge + okurigana_run(g, g.randint(1, 2)))
            out.append(piece)
        body_parts = out

    body = "".join(body_parts)
    if want_circum:
        return okurigana_run(g) + body + okurigana_run(g)
    if want_prefix:
        body = okurigana_run(g) + body
    if want_suffix:
        body = body + okurigana_run(g)
    return body


def word_inflected(g: Gen) -> str:
    """Kanji or hangul core of 1+ independently inflected components.

    Multi-component cores mirror JP phrasal verbs (e.g. stem+okuri · stem+okuri).
    Script is sticky for the whole word (all kanji stems or all hangul stems).
    """
    # 1 component usual; 2–3 like 複合動詞 / phrasal compounds (exp falloff)
    n_comp = g.island_len(1, 3)

    hangul_p = WEIGHT_KATAKANA_ROLE / (WEIGHT_KANJI_ROLE + WEIGHT_KATAKANA_ROLE)
    if g.opts.hangul and g.opts.cjk:
        use_hangul = g.chance(hangul_p)
    elif g.opts.hangul:
        use_hangul = True
    elif g.opts.cjk:
        use_hangul = False
    else:
        return ""

    parts: List[str] = []
    for _ in range(n_comp):
        stem = _hangul_stem_segments(g) if use_hangul else _kanji_stem_segments(g)
        parts.append(inflect_component(g, stem))
    return "".join(parts)


def word_kanji_hangul_sequence(g: Gen) -> str:
    """Sequence of CJK and Hangul; CJK priority, Hangul sporadic.

    Precomposed syllables + compat jamo (`edenia cjk`; sporadic FE05) when `--cjk`.
    Conjoining jamo (`edenia hangul` / `edenia hanguls`) when `--hangul`.
    """
    n = g.island_len(1, 5)
    conj_hangul_p = (
        0.12 if (g.opts.hangul and g.opts.cjk) else (1.0 if g.opts.hangul else 0.0)
    )
    parts: List[str] = []
    for i in range(n):
        if i > 0 and g.chance(0.06):
            parts.append("・")
        choices: List[Tuple[str, float]] = []
        if g.opts.cjk:
            choices.append(("cjk", 1.0))
            if CJK_HANGUL_SAMPLE_POOLS:
                choices.append(("precomp", CJK_PRECOMPOSED_HANGUL_P))
        if g.opts.hangul and conj_hangul_p > 0:
            choices.append(("conj", conj_hangul_p))
        if not choices:
            break
        kind = g.weighted_choice(tuple(choices))
        if kind == "precomp":
            parts.append(precomposed_hangul_word(g, g.island_len(1, SCRIPT_RUN_MAX)))
        elif kind == "conj":
            parts.append(hangul_word(g, g.island_len(1, SCRIPT_RUN_MAX)))
        else:
            parts.append(_kanji_piece(g, prefer_multi=(n >= 2)))
    return "".join(parts)


def word_particle(g: Gen) -> str:
    """Standalone particle: short fullwidth kana or yi (never halfwidth)."""
    n = g.island_len(1, SCRIPT_RUN_MAX)
    choices: List[Tuple[str, float]] = []
    if g.opts.kana:
        choices.append(("kana", WEIGHT_KANA_FULL))
    if g.opts.yi:
        choices.append(("yi", WEIGHT_YI))
    if not choices:
        return ""
    if g.weighted_choice(tuple(choices)) == "yi":
        return yi_run(g, n)
    return kana_run(g, n, halfwidth=False)


def word_kana_hw(g: Gen) -> str:
    """Halfwidth kana sticky run — never mixed with fullwidth / yi / okurigana."""
    return kana_run(g, g.island_len(1, SCRIPT_RUN_MAX), halfwidth=True)


def generate_word(g: Gen) -> str:
    """One phrase atom: inflected | sequence | particle | halfwidth kana."""
    kinds: List[Tuple[str, float]] = []
    if g.opts.cjk or g.opts.hangul:
        kinds.append(("inflected", WEIGHT_WORD_INFLECTED))
        kinds.append(("sequence", WEIGHT_WORD_SEQUENCE))
    if g.opts.kana or g.opts.yi:
        kinds.append(("particle", WEIGHT_WORD_PARTICLE))
    if g.opts.kana:
        kinds.append(("kana_hw", WEIGHT_WORD_KANA_HW))
    if not kinds:
        return symbol_island(g)
    kind = g.weighted_choice(tuple(kinds))
    if kind == "inflected":
        word = word_inflected(g)
    elif kind == "sequence":
        word = word_kanji_hangul_sequence(g)
    elif kind == "kana_hw":
        word = word_kana_hw(g)
    else:
        word = word_particle(g)
    return maybe_bracket(g, word)


# ---------------------------------------------------------------------------
# Sentence / phrase builder
# ---------------------------------------------------------------------------


def mid_clause_punct(g: Gen) -> str:
    """Sporadic mid-clause mark (may be empty)."""
    mark = g.pick_sporadic(MID_CLAUSE_P)
    return mark or ""


def terminator_punct(g: Gen) -> str:
    """Sporadic sentence terminator (may be empty)."""
    mark = g.pick_sporadic(TERMINATOR_P)
    return mark or ""


def generate_phrase(g: Gen) -> str:
    """N words (no inter-word spaces); rare symbol / lead-mark islands."""
    n = g.randint(g.bounds.words_min, g.bounds.words_max)
    parts: List[str] = []
    for mark, p in PHRASE_LEAD_P:
        if g.chance(p):
            parts.append(mark)
    if g.chance(0.05):
        parts.append(g.choice(ENCLOSED_ORDINAL))
    if g.chance(
        WEIGHT_SYMBOL_ISLAND
        / (
            WEIGHT_WORD_INFLECTED
            + WEIGHT_WORD_SEQUENCE
            + WEIGHT_WORD_PARTICLE
            + WEIGHT_WORD_KANA_HW
            + WEIGHT_SYMBOL_ISLAND
        )
    ):
        parts.append(maybe_bracket(g, symbol_island(g)))
        n = max(1, n - 1)
    for _ in range(n):
        parts.append(generate_word(g))
    return "".join(parts)


def generate_sentence(g: Gen) -> str:
    """M phrases joined by sporadic mid-clause punct, optional terminator."""
    m = g.randint(g.bounds.phrases_min, g.bounds.phrases_max)
    chunks: List[str] = []
    for i in range(m):
        phrase = generate_phrase(g)
        if not phrase:
            continue
        chunks.append(phrase)
        if i < m - 1:
            sep = mid_clause_punct(g)
            if sep:
                chunks.append(sep)
    text = "".join(chunks).rstrip("".join(MID_CLAUSE_MARKS) + IDEO_SPACE)
    term = terminator_punct(g)
    if term:
        text += term
    elif text and text[-1] not in TERMINATORS:
        # Soft fallback so paragraphs stay sentence-shaped most of the time
        if g.chance(0.65):
            text += "。"
    return text


def generate_paragraph(g: Gen) -> str:
    """K sentences; optional U+3000 between sentences."""
    k = g.randint(g.bounds.sentences_min, g.bounds.sentences_max)
    chunks: List[str] = []
    for i in range(k):
        chunks.append(generate_sentence(g))
        if i < k - 1 and g.chance(IDEO_SPACE_P):
            chunks.append(IDEO_SPACE)
    text = "".join(chunks).rstrip("".join(MID_CLAUSE_MARKS) + IDEO_SPACE)
    if text and text[-1] not in TERMINATORS:
        term = terminator_punct(g)
        text += term if term else "。"
    return text.replace(" ", "").replace("\u00a0", "")


def generate_markdown(g: Gen, num_lines: int) -> str:
    paras = [generate_paragraph(g) for _ in range(num_lines)]
    body = "\n\n".join(paras)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def build_document(g: Gen, num_lines: int, seed: int) -> str:
    b = g.bounds
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [
        "---",
        "title: Edenian script test",
        f"generated: {now}",
        f"seed: {seed}",
        f"sentences: {b.sentences_min}-{b.sentences_max}",
        f"phrases: {b.phrases_min}-{b.phrases_max}",
        f"words: {b.words_min}-{b.words_max}",
    ]
    if g.opts.hangul:
        parts.append(f"hangul_faces: {_hangul_faces_label(g.opts.hangul_faces)}")
    parts += [
        "---",
        "",
        "# Random prose",
        "",
        generate_markdown(g, num_lines),
        "",
    ]
    text = "\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lines", type=int, default=512, help="paragraph count")
    p.add_argument(
        "--out",
        type=Path,
        default=SCRIPT_DIR / "dist" / "Edenian-test.md",
        help="output markdown path",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed (default: random seed from OS)",
    )
    p.add_argument(
        "--sentences",
        nargs=2,
        type=int,
        metavar=("MIN", "MAX"),
        default=[2, 6],
        help="sentences per paragraph (min max); default 2 6",
    )
    p.add_argument(
        "--phrases",
        nargs=2,
        type=int,
        metavar=("MIN", "MAX"),
        default=[1, 4],
        help="phrases per sentence (min max); default 1 4",
    )
    p.add_argument(
        "--words",
        nargs=2,
        type=int,
        metavar=("MIN", "MAX"),
        default=[2, 8],
        help="words per phrase (min max); default 2 8",
    )

    # Script filters — omit all → everything (same as run.ps1).
    scripts = p.add_argument_group("scripts")
    scripts.add_argument(
        "--yi", action="store_true", help="Include Yi (alone or with other scripts)"
    )
    scripts.add_argument("--kana", action="store_true", help="Include kana")
    scripts.add_argument("--hangul", action="store_true", help="Include hangul")
    scripts.add_argument(
        "--cjk", action="store_true", help="Include Han / Tangut / Khitan"
    )

    # Shared face filters: --base (plain) and/or --h (FE08–FE0F half digraphs).
    # --h implies base. Per-script --*-base / --*-h override the shared pair.
    faces = p.add_argument_group("faces")
    faces.add_argument(
        "--base",
        action="store_true",
        help="Identity/base only when alone; always included with --h",
    )
    faces.add_argument(
        "--h",
        action="store_true",
        help="Allow half / triangle digraphs FE08–FE0F (implies --base)",
    )
    faces.add_argument(
        "--cjk-base",
        action="store_true",
        help="CJK: identity/base only when alone; implied by --cjk-h / --h",
    )
    faces.add_argument(
        "--cjk-faces",
        metavar="LIST",
        help="CJK: exact comma list base,h (overrides other CJK face flags)",
    )
    faces.add_argument(
        "--cjk-h",
        action="store_true",
        help="CJK: include half-cell digraphs (implies base)",
    )
    faces.add_argument(
        "--kana-base",
        action="store_true",
        help="Kana: identity/base only when alone; implied by --kana-h",
    )
    faces.add_argument(
        "--kana-h",
        action="store_true",
        help="Kana: half / triangle digraphs (implies base)",
    )
    faces.add_argument(
        "--yi-base",
        action="store_true",
        help="Yi: identity/base only when alone; implied by --yi-h",
    )
    faces.add_argument(
        "--yi-h",
        action="store_true",
        help="Yi: half / triangle digraphs (implies base)",
    )
    faces.add_argument(
        "--hangul-base",
        action="store_true",
        help="Hangul: upright jamo only when alone; implied by --hangul-s",
    )
    faces.add_argument(
        "--hangul-s",
        action="store_true",
        help="Hangul: include sideways jamo (U+FE05, edenia hanguls; implies base)",
    )
    return p.parse_args(argv)


def _resolve_base_h_faces(*, base: bool, want_h: bool) -> frozenset[str]:
    """`--h` → base+h. `--base` alone → base. No flags → :data:`FACE_DEFAULTS`."""
    if not base and not want_h:
        return frozenset(FACE_DEFAULTS)
    selected: List[str] = [""]  # --h implies base
    if want_h:
        selected.append("h")
    return frozenset(ordered_segment_variants(selected))


def _resolve_cjk_face_flags(
    *,
    base: bool,
    faces: Optional[str],
    want_h: bool,
) -> frozenset[str]:
    """CJK: base and/or h only."""
    if faces:
        if base or want_h:
            raise SystemExit("use either --cjk-faces or --base/--h/--cjk-*, not both")
        got = [
            segment_variant_from_token(p) for p in str(faces).split(",") if p.strip()
        ]
        if not got:
            raise SystemExit("--cjk-faces is empty")
        return frozenset(ordered_cjk_variants(got))
    return _resolve_base_h_faces(base=base, want_h=want_h)


def _resolve_hangul_face_flags(*, base: bool, want_s: bool) -> frozenset[str]:
    """Hangul: `""` upright; `s` sideways (+ FE05). Default: both."""
    if not base and not want_s:
        return frozenset(HANGUL_DEFAULT_FACES)
    if base and not want_s:
        return frozenset({""})
    selected: List[str] = []
    if base or want_s:
        selected.append("")
    if want_s:
        selected.append("s")
    return frozenset(selected)


def options_from_args(args: argparse.Namespace) -> GenOptions:
    any_script = args.yi or args.kana or args.hangul or args.cjk
    return GenOptions(
        yi=(not any_script) or args.yi,
        kana=(not any_script) or args.kana,
        hangul=(not any_script) or args.hangul,
        cjk=(not any_script) or args.cjk,
        kana_faces=_resolve_base_h_faces(
            base=args.base or args.kana_base,
            want_h=args.h or args.kana_h,
        ),
        yi_faces=_resolve_base_h_faces(
            base=args.base or args.yi_base,
            want_h=args.h or args.yi_h,
        ),
        cjk_faces=_resolve_cjk_face_flags(
            base=args.base or args.cjk_base,
            faces=args.cjk_faces,
            want_h=args.h or args.cjk_h,
        ),
        hangul_faces=_resolve_hangul_face_flags(
            base=args.hangul_base,
            want_s=args.hangul_s,
        ),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    bounds = StructureBounds(
        sentences_min=args.sentences[0],
        sentences_max=args.sentences[1],
        phrases_min=args.phrases[0],
        phrases_max=args.phrases[1],
        words_min=args.words[0],
        words_max=args.words[1],
    )
    bounds.validate()
    opts = options_from_args(args)
    if not (opts.yi or opts.kana or opts.hangul or opts.cjk):
        raise SystemExit("no scripts enabled")
    seed = (
        args.seed
        if args.seed is not None
        else random.SystemRandom().randint(0, 2**31 - 1)
    )
    rng = random.Random(seed)
    print(f"[generate_edenian_md] seed={seed}")
    print(
        f"[generate_edenian_md] sentences={bounds.sentences_min}-{bounds.sentences_max} "
        f"phrases={bounds.phrases_min}-{bounds.phrases_max} "
        f"words={bounds.words_min}-{bounds.words_max}"
    )
    marks = load_combining_marks()
    g = Gen(rng, marks, bounds, opts)
    text = build_document(g, args.lines, seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text)} code points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
