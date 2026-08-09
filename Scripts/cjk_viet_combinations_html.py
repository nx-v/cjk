#!/usr/bin/env python3
"""Build an HTML gallery of CJK × VS1–8 × reading marks.

Encoding (matches ``cjk_viet_marks`` / ``build_subfonts``)::

    U+16FF0/16FF1 (ca/nhay):
      MARK       → right  (``.dk``)
      FE09 MARK  → left   (``.dkl``)
      FE0A MARK  → top    (``.dkt``, r90 mark)
      FE0B MARK  → bottom (``.dkb``, r90 mark)

One niche only — never two sides at once.

Usage
-----
  python cjk_viet_combinations_html.py
  python cjk_viet_combinations_html.py --limit 256
  python cjk_viet_combinations_html.py --range URO --limit 0
  python cjk_viet_combinations_html.py --bucket 4E
  python cjk_viet_combinations_html.py --range 4E00-4FFF -o dist/subfonts/viet-cjk.html
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from build_subfonts import CHAR_RANGES, OUT_DIR as SUBFONTS_OUT
from cjk_viet_marks import (
    VIET_BOT_SELECTOR_CP,
    VIET_LEFT_SELECTOR_CP,
    VIET_MARK_CPS,
    VIET_TOP_SELECTOR_CP,
)
from yi_halfwidth import TRANSFORM_MODES, uvs_selector_for_mode

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "subfonts", "viet-cjk.html")

# Named subsets → inclusive ranges (aligned with build_subfonts.CHAR_RANGES).
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

# Base + marks: full D4 (identity + FE01..FE07).
BASE_ORIENT_VS: List[Optional[int]] = [None] + [
    uvs_selector_for_mode(i)
    for i, (_vs, _r, _fx, _fy, suffix) in enumerate(TRANSFORM_MODES)
    if suffix is not None
]
BASE_ORIENT_LABEL = ["id"] + [
    suffix for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES if suffix is not None
]
MARK_ORIENT_VS: List[Optional[int]] = [None] + [
    uvs_selector_for_mode(i)
    for i, (_vs, _r, _fx, _fy, suffix) in enumerate(TRANSFORM_MODES)
    if suffix is not None
]
MARK_ORIENT_LABEL = ["id"] + [
    suffix for _vs, _r, _fx, _fy, suffix in TRANSFORM_MODES if suffix is not None
]

VIET_MARK_LABEL = {
    0x16FF0: "ca",
    0x16FF1: "nhay",
}


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


def assigned_cps(ranges: Sequence[Tuple[int, int]], *, limit: int) -> List[dict]:
    out: List[dict] = []
    for a, b in ranges:
        for cp in range(a, b + 1):
            try:
                ch = chr(cp)
                name = unicodedata.name(ch)
            except (ValueError, OverflowError):
                continue
            short = name.split()[-1].replace("-", "") if name else f"{cp:04X}"
            out.append({"cp": cp, "ch": ch, "name": name, "short": short})
            if limit > 0 and len(out) >= limit:
                return out
    return out


def pancjk_font_stack(
    font_dir: str,
    *,
    ranges: Optional[Sequence[Tuple[int, int]]] = None,
) -> str:
    """Quoted ``'pancjk XX'`` stack from fonts on disk, else from CHAR_RANGES.

    When ``ranges`` covers a single bucket, use only that face so reading marks
    (also in that face's unicode-range) are not stolen by an earlier bucket or
    by Plangothic's spacing outlines.
    """
    if ranges:
        buckets: set = set()
        for a, b in ranges:
            buckets.update(range(a >> 8, (b >> 8) + 1))
        if len(buckets) == 1:
            hex_id = f"{next(iter(buckets)):X}"
            return f"'pancjk {hex_id}'"

    ids: List[str] = []
    if os.path.isdir(font_dir):
        seen = set()
        for name in sorted(os.listdir(font_dir)):
            if not (name.endswith(".woff2") or name.endswith(".ttf")):
                continue
            hex_id = os.path.splitext(name)[0]
            if hex_id.startswith("_") or hex_id in seen:
                continue
            try:
                int(hex_id, 16)
            except ValueError:
                continue
            seen.add(hex_id)
            ids.append(hex_id)
    if not ids:
        buckets_set = set()
        for a, b, _n in CHAR_RANGES:
            for bid in range(a >> 8, (b >> 8) + 1):
                buckets_set.add(bid)
        ids = [f"{b:X}" for b in sorted(buckets_set)]
    return ", ".join(f"'pancjk {i}'" for i in ids)


def write_html(
    path: str,
    *,
    ranges: Sequence[Tuple[int, int]],
    limit: int,
    font_size: int,
    font_dir: str,
) -> None:
    cjk = assigned_cps(ranges, limit=limit)
    n = len(cjk)
    n_base_o = len(BASE_ORIENT_VS)
    n_mark_o = len(MARK_ORIENT_VS)
    n_marks = len(VIET_MARK_CPS)
    # Galleries (on-demand counts)
    n_plain = n
    n_with_mark = n * n_marks
    n_base_vs_mark = n * n_base_o * n_marks
    n_mark_vs = n * n_marks * n_mark_o
    total = n_plain + n_with_mark + n_base_vs_mark + n_mark_vs

    stack = pancjk_font_stack(font_dir, ranges=ranges)
    marks = [
        {
            "cp": cp,
            "ch": chr(cp),
            "label": VIET_MARK_LABEL.get(cp, f"{cp:04X}"),
        }
        for cp in VIET_MARK_CPS
    ]

    payload = {
        "CJK": cjk,
        "MARKS": marks,
        "BASE_ORIENT_VS": BASE_ORIENT_VS,
        "BASE_ORIENT_LABEL": BASE_ORIENT_LABEL,
        "MARK_ORIENT_VS": MARK_ORIENT_VS,
        "MARK_ORIENT_LABEL": MARK_ORIENT_LABEL,
        "LEFT_SEL": VIET_LEFT_SELECTOR_CP,
        "TOP_SEL": VIET_TOP_SELECTOR_CP,
        "BOT_SEL": VIET_BOT_SELECTOR_CP,
        "n": n,
        "total": total,
    }

    range_note = ", ".join(f"U+{a:X}–{b:X}" for a, b in ranges)
    if limit > 0:
        range_note += f" (first {n:,})"

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            f"""<!doctype html>
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
.glyph {{ line-height: 1; }}
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
  <h1>pancjk — CJK × VS1–8 × reading marks</h1>
  <p class="meta">
    Range: {range_note} · {n:,} characters embedded<br/>
    Base VS: identity / FE01..FE07 (full D4) · mark D4: FE01..FE07<br/>
    U+16FF0/16FF1: right <code>.dk</code> · FE09 left <code>.dkl</code> ·
    FE0A top <code>.dkt</code> (r90) · FE0B bottom <code>.dkb</code> (r90) ·
    one niche · gallery ≈ {total:,} (on demand)
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
    <label>Mark
      <select id="markSel"></select>
    </label>
    <label>Mark orient
      <select id="markOrient"></select>
    </label>
    <button type="button" id="btnSlice">Render slice</button>
    <button type="button" id="btnPlain">Plain CJK</button>
    <button type="button" id="btnMarks">+ right</button>
    <button type="button" id="btnLeft">+ left (FE09)</button>
    <button type="button" id="btnTop">+ top (FE0A)</button>
    <button type="button" id="btnBot">+ bottom (FE0B)</button>
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
fillOrient(baseOrient, DATA.BASE_ORIENT_LABEL, DATA.BASE_ORIENT_VS);
fillOrient(markOrient, DATA.MARK_ORIENT_LABEL, DATA.MARK_ORIENT_VS);
fillMarks(markSel);
baseOrient.value = '0';
markOrient.value = '0';
markSel.value = 'all';

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
function cjkPiece(idx, baseOi) {{
  const c = DATA.CJK[idx];
  return c.ch + vsChar(DATA.BASE_ORIENT_VS[baseOi]);
}}
function markPiece(mi, markOi) {{
  const m = DATA.MARKS[mi];
  return m.ch + vsChar(DATA.MARK_ORIENT_VS[markOi]);
}}
function sideMarkPiece(side, mi, markOi) {{
  const sel = side === 'L' ? DATA.LEFT_SEL
    : side === 'T' ? DATA.TOP_SEL
    : side === 'B' ? DATA.BOT_SEL
    : null;
  return (sel != null ? String.fromCodePoint(sel) : '') + markPiece(mi, markOi);
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

const SIDE_LABEL = {{
  R: 'right /.dk',
  L: 'left FE09 /.dkl',
  T: 'top FE0A /.dkt',
  B: 'bottom FE0B /.dkb',
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

function renderSide(indices, baseOi, markIndices, markOi, side) {{
  clearOut();
  out.appendChild(heading('CJK + mark (' + SIDE_LABEL[side] + ')'));
  let n = 0;
  for (const i of indices) {{
    for (const mi of markIndices) {{
      const text = cjkPiece(i, baseOi) + sideMarkPiece(side, mi, markOi);
      out.appendChild(cell(text, tagFor(i, baseOi, mi, markOi, side)));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' ' + side + '-side mark cells');
}}

function renderBaseGrid(indices, markIndices) {{
  clearOut();
  out.appendChild(heading('Base VS1–8 × mark (mark identity, right)'));
  let n = 0;
  for (const i of indices) {{
    for (let bo = 0; bo < DATA.BASE_ORIENT_VS.length; bo++) {{
      for (const mi of markIndices) {{
        const text = cjkPiece(i, bo) + markPiece(mi, 0);
        out.appendChild(cell(text, tagFor(i, bo, mi, 0, 'R')));
        n++;
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' base-orient × mark cells');
}}

function renderMarkGrid(indices, baseOi, markIndices) {{
  clearOut();
  out.appendChild(heading('Mark D4 (base orient fixed, right)'));
  let n = 0;
  for (const i of indices) {{
    for (const mi of markIndices) {{
      for (let mo = 0; mo < DATA.MARK_ORIENT_VS.length; mo++) {{
        const text = cjkPiece(i, baseOi) + markPiece(mi, mo);
        out.appendChild(cell(text, tagFor(i, baseOi, mi, mo, 'R')));
        n++;
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' mark-orient cells');
}}

function renderEverything() {{
  const indices = DATA.CJK.map((_, i) => i);
  if (!confirm('Render ~' + (DATA.total * 2).toLocaleString() +
      ' cells for ' + DATA.n.toLocaleString() + ' CJK? This can lock the tab.')) return;
  clearOut();
  let n = 0;
  out.appendChild(heading('Plain'));
  for (const i of indices) {{
    out.appendChild(cell(DATA.CJK[i].ch, DATA.CJK[i].short));
    n++;
  }}
  for (const side of ['R', 'L', 'T', 'B']) {{
    out.appendChild(heading(SIDE_LABEL[side]));
    for (const i of indices) {{
      for (let mi = 0; mi < DATA.MARKS.length; mi++) {{
        out.appendChild(cell(
          cjkPiece(i, 0) + sideMarkPiece(side, mi, 0),
          tagFor(i, 0, mi, 0, side)));
        n++;
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' cells');
}}

document.getElementById('btnPlain').onclick = () =>
  renderPlain(sliceIndices());
document.getElementById('btnMarks').onclick = () =>
  renderSide(sliceIndices(), +baseOrient.value, markList(), +markOrient.value, 'R');
document.getElementById('btnLeft').onclick = () =>
  renderSide(sliceIndices(), +baseOrient.value, markList(), +markOrient.value, 'L');
document.getElementById('btnTop').onclick = () =>
  renderSide(sliceIndices(), +baseOrient.value, markList(), +markOrient.value, 'T');
document.getElementById('btnBot').onclick = () =>
  renderSide(sliceIndices(), +baseOrient.value, markList(), +markOrient.value, 'B');
document.getElementById('btnSlice').onclick = () =>
  renderSide(sliceIndices(), +baseOrient.value, markList(), +markOrient.value, 'R');
document.getElementById('btnBaseGrid').onclick = () =>
  renderBaseGrid(sliceIndices(), markList());
document.getElementById('btnMarkGrid').onclick = () =>
  renderMarkGrid(sliceIndices(), +baseOrient.value, markList());
document.getElementById('btnEverything').onclick = renderEverything;

renderSide(sliceIndices(), 0, DATA.MARKS.map((_, i) => i), 0, 'R');
</script>
</body>
</html>
"""
        )
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
    return p.parse_args()


def main() -> None:
    args = parse_args()
    specs: List[str] = []
    if args.range:
        specs.extend(args.range)
    if args.bucket:
        specs.extend(args.bucket)
    if not specs:
        specs = ["URO"]
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
    )


if __name__ == "__main__":
    main()
