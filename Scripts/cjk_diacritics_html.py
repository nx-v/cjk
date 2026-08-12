#!/usr/bin/env python3
"""Build an HTML gallery of CJK × VS1–8 × reading marks.

Encoding (matches ``cjk_diacritics`` / ``build_cjk``)::

    U+16FF0/16FF1 (ca/nhay) — niche VS required::

      CJK (D4)? FE0C MARK → base ``.dk``  + mark right
      CJK (D4)? FE0D MARK → base ``.dkl`` + mark left
      CJK (D4)? FE0E MARK → base ``.dkb`` (top)    + mark bottom
      CJK (D4)? FE0F MARK → base ``.dkt`` (bottom) + mark top

    Gallery text uses the same FE0* selectors (BMP PUA is pankana).

    Squish = occupy one half of the ideographic area (same ``.dk*`` glyphs):
      FE0B       → zero-width ``.ov``
      FE0C–FE0F  → ``.dk`` / ``.dkl`` / ``.dkb`` / ``.dkt`` (L/R/T/B)
      FE0B+FE0C–F → zero-width half-cell overlay

    Squish digraph (two kanji, often different pancjk buckets)::

      A (D4)? FE0B FE0C–F   B (D4)? FE0D–F
      e.g. ``明`` FE0B FE0C + ``日`` FE0D
      First is zero-width overlay; niches oppose (FE0C↔FE0D, FE0E↔FE0F).

Usage
-----
  python cjk_diacritics_html.py
  python cjk_diacritics_html.py --limit 256
  python cjk_diacritics_html.py --range URO --limit 0
  python cjk_diacritics_html.py --bucket 4E --bucket 4F
  python cjk_diacritics_html.py --range 4E00-4FFF -o dist/subfonts/diac-cjk.html
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from collections import defaultdict

from build_cjk import CHAR_RANGES, IN_DIR, OUT_DIR as SUBFONTS_OUT
from cjk_diacritics import (
    CORE_MARK_CPS,
    OV_SELECTOR_CP,
    SQUISH_BOT_CP,
    SQUISH_LEFT_CP,
    SQUISH_RIGHT_CP,
    SQUISH_TOP_CP,
)
from shared_half_cells import TRANSFORM_MODES, uvs_selector_for_mode

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "subfonts", "diac-cjk.html")

# Named subsets → inclusive ranges (aligned with build_cjk.CHAR_RANGES).
NAMED_RANGES: Dict[str, Tuple[Tuple[int, int], ...]] = {
    "URO": ((0x4E00, 0x9FFF),),
    "ExtA": ((0x3400, 0x4DBF),),
    "ExtB": ((0x20000, 0x2A6DF),),
    "ExtC": ((0x2A700, 0x2B73F),),
    "ExtD": ((0x2B740, 0x2B81F),),
    "ExtE": ((0x2B820, 0x2CEAF),),
    "ExtF": ((0x2CEB0, 0x2EBEF),),
    "ExtG": ((0x30000, 0x3134F),),
    "ExtH": ((0x31350, 0x323AF),),
    "ExtI": ((0x2EBF0, 0x2EE5F),),
    "ExtJ": ((0x323B0, 0x3347F),),
    "Compat": ((0xFA00, 0xFAFF),),
    "CompatSup": ((0x2F800, 0x2FA1F),),
    "Tangut": ((0x17000, 0x187FF), (0x18D00, 0x18D7F)),
    "ALL": tuple((a, b) for a, b, _n in CHAR_RANGES),
}

# Base + marks: full D4. FE00 = identity no-op liga (helps FE0B–FE0F follow).
BASE_ORIENT_VS: List[Optional[int]] = [
    uvs_selector_for_mode(i) for i, _mode in enumerate(TRANSFORM_MODES)
]
BASE_ORIENT_LABEL = [
    ("id" if suffix is None else suffix)
    for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES
]
MARK_ORIENT_VS: List[Optional[int]] = list(BASE_ORIENT_VS)
MARK_ORIENT_LABEL = list(BASE_ORIENT_LABEL)

MARK_LABEL = {
    0x16FF0: "ca",
    0x16FF1: "nhay",
}

# Opposing half niches: first (FE0B + FE0C–F) zero-width, second (FE0D–F) advance.
# Labels match squishPiece keys (R=FE0C/.dk, L=FE0D/.dkl, T=FE0E/.dkb, B=FE0F/.dkt).
DIGRAPH_NICHE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("R", "L"),  # FE0C (.dk left)   + FE0D (.dkl right)
    ("L", "R"),  # FE0D (.dkl right)  + FE0C (.dk left)
    ("T", "B"),  # FE0E (.dkb top)    + FE0F (.dkt bottom)
    ("B", "T"),  # FE0F (.dkt bottom) + FE0E (.dkb top)
)

# Canonical demo digraph (cross-bucket 66 + 65): 明 FE0B FE0C + 日 FE0D.
DIGRAPH_SEED_CPS: Tuple[Tuple[int, int], ...] = ((0x660E, 0x65E5),)  # 明日

# Opposing D4 labels (second orient faces the first).
OPPOSING_ORIENT: Dict[str, str] = {
    "id": "r180",
    "r90": "r270",
    "r180": "id",
    "r270": "r90",
    "mx": "my",
    "my": "mx",
    "r90mx": "r90my",
    "r90my": "r90mx",
}


def digraph_pairs(
    cjk: Sequence[dict],
    *,
    max_pairs: int = 24,
) -> List[Tuple[int, int, bool]]:
    """Index pairs ``(i, j, cross_bucket)`` preferring different ``cp>>8`` fonts."""
    by_bucket: Dict[int, List[int]] = defaultdict(list)
    index_by_cp = {int(c["cp"]): i for i, c in enumerate(cjk)}
    for i, c in enumerate(cjk):
        by_bucket[int(c["cp"]) >> 8].append(i)
    buckets = sorted(by_bucket)
    pairs: List[Tuple[int, int, bool]] = []
    seen_ij: set = set()
    for ca, cb in DIGRAPH_SEED_CPS:
        ia, ib = index_by_cp.get(ca), index_by_cp.get(cb)
        if ia is None or ib is None or ia == ib:
            continue
        key = (ia, ib)
        if key in seen_ij:
            continue
        seen_ij.add(key)
        cross = (ca >> 8) != (cb >> 8)
        pairs.append((ia, ib, cross))
        if len(pairs) >= max_pairs:
            return pairs
    if len(buckets) >= 2:
        bi = 0
        while len(pairs) < max_pairs and bi < max_pairs * len(buckets):
            b0 = buckets[bi % len(buckets)]
            b1 = buckets[(bi + len(buckets) // 2) % len(buckets)]
            if b0 == b1:
                b1 = buckets[(bi + 1) % len(buckets)]
            la, lb = by_bucket[b0], by_bucket[b1]
            if la and lb and b0 != b1:
                ia = la[len(pairs) % len(la)]
                ib = lb[(len(pairs) * 3) % len(lb)]
                key = (ia, ib)
                if ia != ib and key not in seen_ij:
                    seen_ij.add(key)
                    pairs.append((ia, ib, True))
            bi += 1
    if len(pairs) < max_pairs:
        for i in range(0, len(cjk) - 1):
            if len(pairs) >= max_pairs:
                break
            j = i + 1
            key = (i, j)
            if key in seen_ij:
                continue
            seen_ij.add(key)
            cross = (int(cjk[i]["cp"]) >> 8) != (int(cjk[j]["cp"]) >> 8)
            pairs.append((i, j, cross))
    return pairs


def opposing_orient_index(label: str) -> int:
    want = OPPOSING_ORIENT.get(label, "r180")
    try:
        return BASE_ORIENT_LABEL.index(want)
    except ValueError:
        return (
            0
            if label != "id"
            else (BASE_ORIENT_LABEL.index("r180") if "r180" in BASE_ORIENT_LABEL else 0)
        )


def parse_range_spec(spec: str) -> List[Tuple[int, int]]:
    """``URO`` / ``ExtA`` / ``4E00-4FFF`` / ``U+4E00..U+4E7F`` / hex bucket ``4E``."""
    s = spec.strip()
    key = s.upper().replace(" ", "")
    if key in NAMED_RANGES:
        return list(NAMED_RANGES[key])
    # Single bucket id (cp >> 8), e.g. 4E → U+4E00..4EFF
    if re.fullmatch(r"[0-9A-Fa-f]{1,5}", s) and len(s) <= 3:
        bid = int(s, 16)
        start = bid << 8
        return [(start, start + 0xFF)]
    m = re.fullmatch(
        r"(?:U\+)?([0-9A-Fa-f]+)\s*(?:-|\.\.|–|—)\s*(?:U\+)?([0-9A-Fa-f]+)",
        s,
        re.I,
    )
    if m:
        return [(int(m.group(1), 16), int(m.group(2), 16))]
    raise argparse.ArgumentTypeError(
        f"bad --range {spec!r}; try URO, ExtA, 4E, or 4E00-4FFF"
    )


def _cjk_entry(cp: int) -> Optional[dict]:
    try:
        ch = chr(cp)
        name = unicodedata.name(ch)
    except (ValueError, OverflowError):
        return None
    short = name.split()[-1].replace("-", "") if name else f"{cp:04X}"
    return {"cp": cp, "ch": ch, "name": name, "short": short}


def assigned_cps(ranges: Sequence[Tuple[int, int]], *, limit: int) -> List[dict]:
    """Collect CJK entries; with multiple ranges, round-robin so digraphs can cross buckets."""
    per_range: List[List[dict]] = []
    covered: List[Tuple[int, int]] = list(ranges)
    for a, b in covered:
        chunk: List[dict] = []
        for cp in range(a, b + 1):
            entry = _cjk_entry(cp)
            if entry is not None:
                chunk.append(entry)
        if chunk:
            per_range.append(chunk)
    if not per_range:
        return []

    def _in_ranges(cp: int) -> bool:
        return any(a <= cp <= b for a, b in covered)

    # Always keep digraph seed CPs when they fall in the requested ranges.
    seeds: List[dict] = []
    seen_seed = set()
    for ca, cb in DIGRAPH_SEED_CPS:
        for cp in (ca, cb):
            if cp in seen_seed or not _in_ranges(cp):
                continue
            entry = _cjk_entry(cp)
            if entry is None:
                continue
            seen_seed.add(cp)
            seeds.append(entry)

    if len(per_range) == 1:
        out = list(per_range[0])
        if limit > 0:
            out = out[:limit]
        for s in seeds:
            if s["cp"] not in {c["cp"] for c in out}:
                if limit > 0 and len(out) >= limit:
                    out[-1] = s
                else:
                    out.append(s)
        # Prefer seeds at the front for digraph_pairs.
        if seeds:
            seed_cps = {s["cp"] for s in seeds}
            out = [c for c in out if c["cp"] in seed_cps] + [
                c for c in out if c["cp"] not in seed_cps
            ]
        return out

    # Round-robin across ranges for mixed-bucket digraph coverage.
    out: List[dict] = []
    seen = set()
    for s in seeds:
        seen.add(s["cp"])
        out.append(s)
    idx = [0] * len(per_range)
    while True:
        progressed = False
        for ri, chunk in enumerate(per_range):
            while idx[ri] < len(chunk):
                c = chunk[idx[ri]]
                idx[ri] += 1
                if c["cp"] in seen:
                    continue
                seen.add(c["cp"])
                out.append(c)
                progressed = True
                break
            if limit > 0 and len(out) >= limit:
                return out
        if not progressed:
            break
    return out


def pancjk_font_stack(
    font_dir: str,
    *,
    ranges: Optional[Sequence[Tuple[int, int]]] = None,
    force_all: bool = False,
) -> str:
    """Quoted font stack for galleries (per-bucket ``'pancjk XX'`` only)."""
    buckets: List[int] = []
    if ranges:
        seen: set = set()
        for a, b in ranges:
            for bid in range(a >> 8, (b >> 8) + 1):
                if bid not in seen:
                    seen.add(bid)
                    buckets.append(bid)
        if not force_all and len(buckets) == 1:
            return f"'pancjk {buckets[0]:X}'"
        if buckets and not force_all:
            return ", ".join(f"'pancjk {bid:X}'" for bid in buckets)

    # All faces present in font_dir (or ranges when force_all).
    del force_all
    if not buckets and os.path.isdir(font_dir):
        for name in sorted(os.listdir(font_dir)):
            if not (name.endswith(".woff2") or name.endswith(".ttf")):
                continue
            hex_id = os.path.splitext(name)[0]
            try:
                buckets.append(int(hex_id, 16))
            except ValueError:
                continue
        # unique preserve order
        seen2: set = set()
        uniq: List[int] = []
        for bid in buckets:
            if bid not in seen2:
                seen2.add(bid)
                uniq.append(bid)
        buckets = uniq
    if not buckets:
        return "'pancjk 4E'"
    return ", ".join(f"'pancjk {bid:X}'" for bid in buckets)


def write_html(
    path: str,
    *,
    ranges: Sequence[Tuple[int, int]],
    limit: int,
    font_size: int,
    font_dir: str,
    in_dir: str = IN_DIR,
) -> None:
    cjk = assigned_cps(ranges, limit=limit)
    n = len(cjk)
    n_base_o = len(BASE_ORIENT_VS)
    n_mark_o = len(MARK_ORIENT_VS)

    mark_cps: List[int] = list(CORE_MARK_CPS)

    n_marks = len(mark_cps)
    pairs = digraph_pairs(cjk, max_pairs=min(24, max(1, n // 2)))
    n_cross = sum(1 for _a, _b, cross in pairs if cross)
    # Galleries (on-demand counts)
    n_plain = n
    n_with_mark = n * n_marks
    n_base_vs_mark = n * n_base_o * n_marks
    n_mark_vs = n * n_marks * n_mark_o
    n_digraph = len(pairs) * len(DIGRAPH_NICHE_PAIRS) * n_base_o
    total = n_plain + n_with_mark + n_base_vs_mark + n_mark_vs + n_digraph

    force_stack = n_cross > 0 or len({c["cp"] >> 8 for c in cjk}) > 1
    stack = pancjk_font_stack(font_dir, ranges=ranges, force_all=force_stack)
    marks = [
        {
            "cp": cp,
            "ch": chr(cp),
            "label": MARK_LABEL.get(
                cp, unicodedata.name(chr(cp), f"{cp:04X}").split()[-1][:12]
            ),
        }
        for cp in mark_cps
    ]

    opposing_oi = [opposing_orient_index(lab) for lab in BASE_ORIENT_LABEL]

    payload = {
        "CJK": cjk,
        "MARKS": marks,
        "BASE_ORIENT_VS": BASE_ORIENT_VS,
        "BASE_ORIENT_LABEL": BASE_ORIENT_LABEL,
        "MARK_ORIENT_VS": MARK_ORIENT_VS,
        "MARK_ORIENT_LABEL": MARK_ORIENT_LABEL,
        "OV_SEL": OV_SELECTOR_CP,
        "SQUISH_R": SQUISH_RIGHT_CP,
        "SQUISH_L": SQUISH_LEFT_CP,
        "SQUISH_T": SQUISH_TOP_CP,
        "SQUISH_B": SQUISH_BOT_CP,
        "DIGRAPH_PAIRS": [{"a": a, "b": b, "cross": cross} for a, b, cross in pairs],
        "DIGRAPH_NICHES": [{"a": a, "b": b} for a, b in DIGRAPH_NICHE_PAIRS],
        "OPPOSING_ORIENT_OI": opposing_oi,
        "n": n,
        "total": total,
    }

    range_note = ", ".join(f"U+{a:X}–{b:X}" for a, b in ranges)
    if limit > 0:
        range_note += f" (first {n:,})"

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"/>
<title>pancjk — CJK × VS1–7 × Viet marks</title>
<link rel="stylesheet" href="./pancjk.css"/>
<style>
:root {{ --fs: {font_size}px; color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: #111; color: #eee;
  font-family: system-ui, sans-serif;
}}
header {{
  position: sticky; top: 0; z-index: 5;
  background: #111e; backdrop-filter: blur(6px);
  border-bottom: 1px solid #333; padding: 12px 20px 14px;
}}
h1 {{ font-size: 18px; font-weight: 600; color: #ccc; margin: 0 0 6px; }}
.meta {{ font-size: 12px; color: #777; margin: 0 0 10px; line-height: 1.45; }}
.controls {{
  display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: end;
  font-size: 12px; color: #aaa;
}}
.controls label {{ display: flex; flex-direction: column; gap: 3px; }}
.controls select, .controls button, .controls input {{
  background: #1a1a1a; color: #ddd; border: 1px solid #444;
  border-radius: 4px; padding: 5px 8px; font-size: 12px;
}}
.controls button {{ cursor: pointer; background: #2a4a3a; border-color: #3a6a5a; }}
.controls button:hover {{ background: #355a48; }}
.controls button.danger {{ background: #4a2a2a; border-color: #6a3a3a; }}
#status {{ font-size: 12px; color: #8af; margin: 10px 20px 0; min-height: 1.2em; }}
main {{ padding: 12px 20px 80px; }}
#out {{
  font-family: {stack}, serif;
  font-size: var(--fs);
  line-height: 1.35;
  display: flex; flex-wrap: wrap; gap: 2px 4px;
}}
.cell {{
  display: inline-flex; flex-direction: column; align-items: center;
  min-width: 1.15em; padding: 4px 2px;
  border-bottom: 1px solid #222;
}}
.glyph {{
  line-height: 1;
  font-feature-settings: "ccmp" 1, "rlig" 1, "liga" 1, "mark" 1, "mkmk" 1;
}}
.tag {{
  font-family: system-ui, sans-serif; font-size: 9px; color: #666;
  max-width: 8em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}
h2 {{
  font-size: 13px; color: #aaa; width: 100%; margin: 18px 0 8px;
  border-bottom: 1px solid #333; padding-bottom: 4px;
  font-family: system-ui, sans-serif; font-weight: 600;
}}
</style>
</head>
<body>
<header>
  <h1>pancjk — CJK × VS × marks × squish digraphs</h1>
  <p class="meta">
    Range: {range_note} · {n:,} characters embedded<br/>
    Base VS: identity / FE01..FE07 (full D4)<br/>
    ca/nhay: <code>CJK FE0C–F MARK</code> (PUA E009–C; mark in free half)<br/>
    Squish digraph: <code>A FE0B FE0C–F</code> + <code>B FE0D–F</code> (e.g. 明&#xFE0B;&#xFE0C;日&#xFE0D;)<br/>
    Digraph pairs: {len(pairs)} ({n_cross} cross-bucket) · gallery ≈ {total:,}
  </p>
  <div class="controls">
    <label>CJK start index
      <input type="number" id="idxStart" min="0" max="{max(0, n - 1)}" value="0"/>
    </label>
    <label>Count
      <input type="number" id="idxCount" min="1" max="{max(1, n)}" value="{min(64, max(1, n))}"/>
    </label>
    <label>Base orient
      <select id="baseOrient"></select>
    </label>
    <label>Mark niche
      <select id="nicheSel"></select>
    </label>
    <label>Mark
      <select id="markSel"></select>
    </label>
    <label>Mark orient
      <select id="markOrient"></select>
    </label>
    <button type="button" id="btnSlice">Render slice</button>
    <button type="button" id="btnPlain">Plain CJK</button>
    <button type="button" id="btnMarks">+ ca/nhay</button>
    <button type="button" id="btnDk">FE0C .dk</button>
    <button type="button" id="btnDkl">FE0D .dkl</button>
    <button type="button" id="btnDkt">FE0E .dkb (top)</button>
    <button type="button" id="btnDkb">FE0F .dkt (bottom)</button>
    <button type="button" id="btnDigraph">Squish digraphs</button>
    <button type="button" id="btnBaseGrid">Base VS × mark</button>
    <button type="button" id="btnMarkGrid">Mark D4 grid</button>
    <button type="button" id="btnEverything" class="danger">Render everything</button>
  </div>
</header>
<div id="status"></div>
<main><div id="out"></div></main>

<script type="application/json" id="data">{json.dumps(payload, ensure_ascii=False)}</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const out = document.getElementById('out');
const status = document.getElementById('status');
const idxStart = document.getElementById('idxStart');
const idxCount = document.getElementById('idxCount');
const nicheSel = document.getElementById('nicheSel');
const baseOrient = document.getElementById('baseOrient');
const markSel = document.getElementById('markSel');
const markOrient = document.getElementById('markOrient');

function fillOrient(sel, labels, vs) {{
  labels.forEach((lab, i) => {{
    const o = document.createElement('option');
    o.value = String(i);
    const v = vs[i];
    o.textContent = lab + (v != null
      ? ' (FE' + (v - 0xFE00).toString(16).toUpperCase().padStart(2, '0') + ')'
      : '');
    sel.appendChild(o);
  }});
}}
function fillMarks(sel) {{
  const all = document.createElement('option');
  all.value = 'all'; all.textContent = 'all marks';
  sel.appendChild(all);
  DATA.MARKS.forEach((m, i) => {{
    const o = document.createElement('option');
    o.value = String(i);
    o.textContent = m.label + ' U+' + m.cp.toString(16).toUpperCase();
    sel.appendChild(o);
  }});
}}
function fillNiches(sel) {{
  for (const [side, lab] of [['R','FE0C .dk (left)'],['L','FE0D .dkl (right)'],['T','FE0E .dkb (top)'],['B','FE0F .dkt (bottom)']]) {{
    const o = document.createElement('option');
    o.value = side;
    o.textContent = lab;
    sel.appendChild(o);
  }}
}}
fillOrient(baseOrient, DATA.BASE_ORIENT_LABEL, DATA.BASE_ORIENT_VS);
fillOrient(markOrient, DATA.MARK_ORIENT_LABEL, DATA.MARK_ORIENT_VS);
fillMarks(markSel);
fillNiches(nicheSel);
baseOrient.value = '0';
markOrient.value = '0';
markSel.value = 'all';
nicheSel.value = 'R';

function vsChar(vs) {{ return vs == null ? '' : String.fromCodePoint(vs); }}
function sliceIndices() {{
  const start = Math.max(0, Math.min(DATA.n - 1, +idxStart.value || 0));
  const count = Math.max(1, +idxCount.value || 1);
  const out = [];
  for (let i = start; i < Math.min(DATA.n, start + count); i++) out.push(i);
  return out;
}}
function markList() {{
  if (markSel.value === 'all') return DATA.MARKS.map((_, i) => i);
  return [+markSel.value];
}}
function selectedNiche() {{
  const s = nicheSel.value;
  return (s === 'R' || s === 'L' || s === 'T' || s === 'B') ? s : 'R';
}}
function d4Sel(oi) {{
  const fe = DATA.BASE_ORIENT_VS[oi];
  if (fe == null) return '';
  return String.fromCodePoint(fe);
}}
function cjkPiece(idx, baseOi) {{
  const c = DATA.CJK[idx];
  return c.ch + d4Sel(baseOi);
}}
function markPiece(mi, markOi) {{
  const m = DATA.MARKS[mi];
  // No mark FE00 no-op liga — omit identity D4 so vs01 is not left hanging.
  const d4 = (DATA.MARK_ORIENT_LABEL[markOi] || 'id') === 'id' ? '' : d4Sel(markOi);
  return m.ch + d4;
}}
function squishPiece(side) {{
  const sel = side === 'R' ? DATA.SQUISH_R
    : side === 'L' ? DATA.SQUISH_L
    : side === 'T' ? DATA.SQUISH_T
    : side === 'B' ? DATA.SQUISH_B
    : null;
  return sel != null ? String.fromCodePoint(sel) : '';
}}
function markedCjk(idx, baseOi, mi, markOi, side) {{
  // Niche VS selects half; bare MARK also auto-squishes to .dk via liga.
  return cjkPiece(idx, baseOi) + squishPiece(side) + markPiece(mi, markOi);
}}
function digraphFirst(idx, oi, side) {{
  return cjkPiece(idx, oi)
    + String.fromCodePoint(DATA.OV_SEL)
    + squishPiece(side);
}}
function digraphSecond(idx, oi, side) {{
  return cjkPiece(idx, oi) + squishPiece(side);
}}
function squishHex(side) {{
  const sel = side === 'R' ? DATA.SQUISH_R
    : side === 'L' ? DATA.SQUISH_L
    : side === 'T' ? DATA.SQUISH_T
    : side === 'B' ? DATA.SQUISH_B
    : null;
  return sel != null ? sel.toString(16).toUpperCase() : side;
}}
function tagFor(idx, baseOi, mi, markOi, side) {{
  const c = DATA.CJK[idx];
  const bo = DATA.BASE_ORIENT_LABEL[baseOi] || 'id';
  let t = c.short + (bo === 'id' ? '' : '.' + bo);
  if (side) t += '[' + side + ']';
  if (mi != null) {{
    const m = DATA.MARKS[mi];
    const mo = DATA.MARK_ORIENT_LABEL[markOi] || 'id';
    t += '+' + m.label + (mo === 'id' ? '' : '.' + mo);
  }}
  return t;
}}
function digraphTag(ia, oia, sa, ib, oib, sb, cross) {{
  const a = DATA.CJK[ia], b = DATA.CJK[ib];
  const va = DATA.BASE_ORIENT_VS[oia];
  const vb = DATA.BASE_ORIENT_VS[oib];
  let left = a.cp.toString(16).toUpperCase();
  if (va != null) left += ' FE' + (va - 0xFE00).toString(16).toUpperCase().padStart(2, '0');
  left += ' FE0B ' + squishHex(sa);
  let right = b.cp.toString(16).toUpperCase();
  if (vb != null) right += ' FE' + (vb - 0xFE00).toString(16).toUpperCase().padStart(2, '0');
  right += ' ' + squishHex(sb);
  return left + ' + ' + right + (cross ? ' ⇄font' : '');
}}
function cell(text, tag) {{
  const d = document.createElement('div');
  d.className = 'cell';
  const g = document.createElement('div');
  g.className = 'glyph';
  g.textContent = text;
  const t = document.createElement('div');
  t.className = 'tag';
  t.textContent = tag;
  d.appendChild(g);
  d.appendChild(t);
  return d;
}}
function heading(s) {{
  const h = document.createElement('h2');
  h.textContent = s;
  return h;
}}
function clearOut() {{ out.replaceChildren(); }}
function setStatus(s) {{ status.textContent = s; }}

const SQUISH_LABEL = {{
  R: 'FE0C /.dk',
  L: 'FE0D /.dkl',
  T: 'FE0E /.dkb (top)',
  B: 'FE0F /.dkt (bottom)',
}};

function renderPlain(indices) {{
  clearOut();
  out.appendChild(heading('Plain CJK'));
  let n = 0;
  for (const i of indices) {{
    out.appendChild(cell(DATA.CJK[i].ch, DATA.CJK[i].short));
    n++;
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' plain cells');
}}

function renderMarks(indices, baseOi, markIndices, markOi, side) {{
  const niche = side || selectedNiche();
  clearOut();
  out.appendChild(heading('CJK + ' + SQUISH_LABEL[niche] + ' + ca/nhay'));
  let n = 0;
  for (const i of indices) {{
    for (const mi of markIndices) {{
      const text = markedCjk(i, baseOi, mi, markOi, niche);
      out.appendChild(cell(text, tagFor(i, baseOi, mi, markOi, niche)));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' mark cells');
}}

function renderSquish(indices, baseOi, side) {{
  clearOut();
  out.appendChild(heading('CJK squish (' + SQUISH_LABEL[side] + ')'));
  let n = 0;
  for (const i of indices) {{
    const text = cjkPiece(i, baseOi) + squishPiece(side);
    out.appendChild(cell(text, tagFor(i, baseOi, null, 0, side)));
    n++;
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' squish cells');
}}

function renderDigraphs(orientOi) {{
  clearOut();
  const pairs = DATA.DIGRAPH_PAIRS || [];
  const niches = DATA.DIGRAPH_NICHES || [];
  const opp = DATA.OPPOSING_ORIENT_OI || [];
  out.appendChild(heading(
    'Squish digraphs — first FE0B(+niche) zero-width · second niche · opposing orient'));
  let n = 0;
  for (const p of pairs) {{
    const oia = orientOi;
    const oib = opp[oia] != null ? opp[oia] : oia;
    for (const niche of niches) {{
      const text = digraphFirst(p.a, oia, niche.a)
        + digraphSecond(p.b, oib, niche.b);
      out.appendChild(cell(
        text,
        digraphTag(p.a, oia, niche.a, p.b, oib, niche.b, !!p.cross)));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' digraph cells ('
    + pairs.filter(p => p.cross).length + ' cross-bucket pairs)');
}}

function renderDigraphGrid() {{
  clearOut();
  const pairs = DATA.DIGRAPH_PAIRS || [];
  const niches = DATA.DIGRAPH_NICHES || [];
  const opp = DATA.OPPOSING_ORIENT_OI || [];
  out.appendChild(heading('Squish digraphs × all base orients (opposing)'));
  let n = 0;
  for (let oia = 0; oia < DATA.BASE_ORIENT_VS.length; oia++) {{
    const oib = opp[oia] != null ? opp[oia] : oia;
    const lab = (DATA.BASE_ORIENT_LABEL[oia] || 'id')
      + ' ↔ ' + (DATA.BASE_ORIENT_LABEL[oib] || 'id');
    out.appendChild(heading(lab));
    for (const p of pairs) {{
      for (const niche of niches) {{
        const text = digraphFirst(p.a, oia, niche.a)
          + digraphSecond(p.b, oib, niche.b);
        out.appendChild(cell(
          text,
          digraphTag(p.a, oia, niche.a, p.b, oib, niche.b, !!p.cross)));
        n++;
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' digraph×orient cells');
}}

function renderBaseGrid(indices, markIndices) {{
  const niche = selectedNiche();
  clearOut();
  out.appendChild(heading('Base VS1–8 × ' + SQUISH_LABEL[niche] + ' × mark'));
  let n = 0;
  for (const i of indices) {{
    for (let bo = 0; bo < DATA.BASE_ORIENT_VS.length; bo++) {{
      for (const mi of markIndices) {{
        const text = markedCjk(i, bo, mi, 0, niche);
        out.appendChild(cell(text, tagFor(i, bo, mi, 0, niche)));
        n++;
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' base-orient × mark cells');
}}

function renderMarkGrid(indices, baseOi, markIndices) {{
  const niche = selectedNiche();
  clearOut();
  out.appendChild(heading('Mark D4 grid · ' + SQUISH_LABEL[niche]));
  let n = 0;
  for (const i of indices) {{
    for (const mi of markIndices) {{
      for (let mo = 0; mo < DATA.MARK_ORIENT_VS.length; mo++) {{
        const text = markedCjk(i, baseOi, mi, mo, niche);
        out.appendChild(cell(text, tagFor(i, baseOi, mi, mo, niche)));
        n++;
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' mark-orient cells');
}}

function renderEverything() {{
  const indices = sliceIndices();
  clearOut();
  let n = 0;
  out.appendChild(heading('Plain'));
  for (const i of indices) {{
    out.appendChild(cell(DATA.CJK[i].ch, DATA.CJK[i].short));
    n++;
  }}
  for (const side of ['R', 'L', 'T', 'B']) {{
    out.appendChild(heading('ca/nhay · ' + SQUISH_LABEL[side]));
    for (const i of indices) {{
      for (let mi = 0; mi < DATA.MARKS.length; mi++) {{
        out.appendChild(cell(
          markedCjk(i, 0, mi, 0, side),
          tagFor(i, 0, mi, 0, side)));
        n++;
      }}
    }}
  }}
  for (const side of ['R', 'L', 'T', 'B']) {{
    out.appendChild(heading(SQUISH_LABEL[side]));
    for (const i of indices) {{
      out.appendChild(cell(
        cjkPiece(i, 0) + squishPiece(side),
        tagFor(i, 0, null, 0, side)));
      n++;
    }}
  }}
  out.appendChild(heading('Squish digraphs (id ↔ r180)'));
  const pairs = DATA.DIGRAPH_PAIRS || [];
  const niches = DATA.DIGRAPH_NICHES || [];
  const opp = DATA.OPPOSING_ORIENT_OI || [];
  const oib = opp[0] != null ? opp[0] : 0;
  for (const p of pairs) {{
    for (const niche of niches) {{
      out.appendChild(cell(
        digraphFirst(p.a, 0, niche.a) + digraphSecond(p.b, oib, niche.b),
        digraphTag(p.a, 0, niche.a, p.b, oib, niche.b, !!p.cross)));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' cells');
}}

document.getElementById('btnPlain').onclick = () =>
  renderPlain(sliceIndices());
document.getElementById('btnMarks').onclick = () =>
  renderMarks(sliceIndices(), +baseOrient.value, markList(), +markOrient.value);
document.getElementById('btnDk').onclick = () => {{
  nicheSel.value = 'R';
  renderSquish(sliceIndices(), +baseOrient.value, 'R');
}};
document.getElementById('btnDkl').onclick = () => {{
  nicheSel.value = 'L';
  renderSquish(sliceIndices(), +baseOrient.value, 'L');
}};
document.getElementById('btnDkt').onclick = () => {{
  nicheSel.value = 'T';
  renderSquish(sliceIndices(), +baseOrient.value, 'T');
}};
document.getElementById('btnDkb').onclick = () => {{
  nicheSel.value = 'B';
  renderSquish(sliceIndices(), +baseOrient.value, 'B');
}};
document.getElementById('btnDigraph').onclick = () =>
  renderDigraphs(+baseOrient.value);
document.getElementById('btnSlice').onclick = () =>
  renderMarks(sliceIndices(), +baseOrient.value, markList(), +markOrient.value);
document.getElementById('btnBaseGrid').onclick = () =>
  renderBaseGrid(sliceIndices(), markList());
document.getElementById('btnMarkGrid').onclick = () =>
  renderMarkGrid(sliceIndices(), +baseOrient.value, markList());
document.getElementById('btnEverything').onclick = renderEverything;

renderMarks(sliceIndices(), 0, markList(), 0, 'R');
</script>
</body>
</html>
""")

    print(f"CJK: N={n:,}  range={range_note}  gallery~{total:,}  -> {path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build CJK × Viet diacritic HTML gallery (pancjk)"
    )
    p.add_argument("-o", "--out", default=DEFAULT_OUT)
    p.add_argument(
        "--font-dir",
        default=SUBFONTS_OUT,
        help="Directory with pancjk bucket fonts / pancjk.css (default: dist/subfonts)",
    )
    p.add_argument(
        "--range",
        action="append",
        default=None,
        help="URO | ExtA | 4E | 4E00-4FFF (repeatable). Default: URO",
    )
    p.add_argument(
        "--bucket",
        action="append",
        default=None,
        help="Alias for --range <hex bucket id> (repeatable)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=512,
        help="Max characters to embed (0 = no limit). Default: 512",
    )
    p.add_argument("--font-size", type=int, default=48)
    p.add_argument(
        "--in-dir",
        default=IN_DIR,
        help="Source font dir for shared-mark inventory (default: Scripts/src)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    specs: List[str] = []
    if args.range:
        specs.extend(args.range)
    if args.bucket:
        specs.extend(args.bucket)
    if not specs:
        # Two buckets → digraphs can pull different pancjk faces.
        specs = ["4E", "4F"]
    ranges: List[Tuple[int, int]] = []
    for spec in specs:
        ranges.extend(parse_range_spec(spec))
    # Place HTML next to pancjk.css when using default font-dir.
    out = args.out
    write_html(
        out,
        ranges=ranges,
        limit=args.limit,
        font_size=args.font_size,
        font_dir=args.font_dir,
        in_dir=args.in_dir,
    )


if __name__ == "__main__":
    main()
