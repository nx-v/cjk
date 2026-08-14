#!/usr/bin/env python3
"""Build an HTML gallery of edenia kana chart × D4 × smalls × slices × dakuten.

Encoding (matches ``build_kana``)::

    i        = L * 8 + o
    full[i]  = U+E000 + 2*i     # even
    small[i] = U+E000 + 2*i + 1 # odd
    hw_full[i]  = U+ED00 + 2*i
    hw_small[i] = U+ED00 + 2*i + 1

Orientations are real PUA codepoints (not VS). Slices use FE00 / FE01.
After each script block: length (h U+301C / k U+30FC) and gemination
(h U+309D / k U+30FD), all D4.

Usage
-----
  python kana_html.py
  python kana_html.py -o dist/kana/all-kana.html
"""

from __future__ import annotations

import argparse
import json
import os
import unicodedata
from typing import List

from fontTools.ttLib import TTFont

from build_kana import (
    CHART_ROWS,
    CONSONANTS,
    D4_COUNT,
    HIRAGANA_COUNT,
    HIRAGANA_PHONETIC_COUNT,
    HIRAGANA_ROWS,
    KANA_SLICE_H_CP,
    KANA_SLICE_V_CP,
    KATAKANA_ROWS,
    VOWELS,
    chart_source_cps,
    full_cp,
    hw_full_cp,
    hw_small_cp,
    pair_index,
    small_cp,
    trailing_mark_label,
)
from shared_diacritics import (
    DAKUTEN_SLOT_COUNT,
    DAKUTEN_SLOT_CYCLE,
    dakuten_count_options_html,
    dakuten_skip_options_html,
)
from shared_half_cells import YI_ORIENTATION_MODES

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "kana", "all-kana.html")
KANA_FONT = os.path.join(SCRIPT_DIR, "dist", "kana", "edenia-kana.woff2")
IN_DIR = os.path.join(SCRIPT_DIR, "src")

ORIENT_LABEL = [
    (suffix if suffix is not None else "id")
    for _vs, _r, _fx, _fy, suffix in YI_ORIENTATION_MODES
]

SLICE_MODES = [
    {"id": "none", "cp": None, "label": "none"},
    {"id": "H", "cp": KANA_SLICE_H_CP, "label": "H FE00 (top+bot)"},
    {"id": "V", "cp": KANA_SLICE_V_CP, "label": "V FE01 (left+right)"},
]


def kana_entries() -> List[dict]:
    """One entry per logical chart cell (hiragana then katakana)."""
    out: List[dict] = []
    src_cps = chart_source_cps()
    n_cons = len(CONSONANTS)
    n_vow = len(VOWELS)
    for logical, src_cp in enumerate(src_cps):
        if logical < HIRAGANA_COUNT:
            script = "h"
            local = logical
        else:
            script = "k"
            local = logical - HIRAGANA_COUNT
        trail = trailing_mark_label(logical)
        if trail is not None:
            cons = trail
            vow = ""
            label = f"{script}.{trail}"
        else:
            row = local // n_vow
            col = local % n_vow
            cons = CONSONANTS[row] if row < n_cons else "?"
            vow = VOWELS[col] if col < n_vow else "?"
            label = f"{script}.{cons or '∅'}{vow}"
        try:
            src_ch = chr(src_cp)
        except ValueError:
            src_ch = ""
        try:
            src_name = unicodedata.name(src_ch, f"U+{src_cp:04X}")
        except ValueError:
            src_name = f"U+{src_cp:04X}"
        ixs = [pair_index(logical, o) for o in range(D4_COUNT)]
        out.append(
            {
                "L": logical,
                "src": src_cp,
                "srcCh": src_ch,
                "srcName": src_name,
                "label": label,
                "script": script,
                "cons": cons or "∅",
                "vow": vow,
                "trail": trail,
                "full": [full_cp(i) for i in ixs],
                "small": [small_cp(i) for i in ixs],
                "hwFull": [hw_full_cp(i) for i in ixs],
                "hwSmall": [hw_small_cp(i) for i in ixs],
            }
        )
    return out


def dakuten_mark_entries(limit: int = 64) -> List[dict]:
    cps: List[int] = []
    if os.path.isfile(KANA_FONT):
        tt = TTFont(KANA_FONT)
        try:
            cmap = tt.getBestCmap() or {}
            cps = sorted(cp for cp, name in cmap.items() if str(name).endswith(".mk"))
        finally:
            tt.close()
    if not cps:
        from shared_diacritics import (
            load_dakuten_marks_from_stack,
            resolve_dakuten_mark_font_stack,
        )

        try:
            stack = resolve_dakuten_mark_font_stack(IN_DIR)
            kept, _ = load_dakuten_marks_from_stack(stack, 1000)
            cps = kept
        except Exception:
            pass

    from shared_diacritics import dakuten_mark_label, visible_dakuten_cps

    out: List[dict] = []
    for cp in visible_dakuten_cps(cps)[: max(0, limit) or None]:
        ch, name, short = dakuten_mark_label(cp)
        out.append({"cp": cp, "ch": ch, "name": name, "short": short})
    return out


def write_html(path: str, *, font_size: int, mark_limit: int) -> None:
    kana = kana_entries()
    marks = dakuten_mark_entries(limit=mark_limit)
    n = len(kana)
    n_orient = n * D4_COUNT
    n_pair = n * n * D4_COUNT * D4_COUNT
    n_rows = len(CHART_ROWS)
    n_cols = len(VOWELS)
    h_rows = len(HIRAGANA_ROWS)
    row_labels = [f"h.{c or '∅'}" for c in CONSONANTS[:h_rows]] + [
        f"k.{c or '∅'}" for c in CONSONANTS[: len(KATAKANA_ROWS)]
    ]

    payload = {
        "KANA": kana,
        "MARKS": marks,
        "ORIENT_LABEL": ORIENT_LABEL,
        "SLICE_MODES": SLICE_MODES,
        "CONSONANTS": [c or "∅" for c in CONSONANTS],
        "ROW_LABELS": row_labels,
        "VOWELS": list(VOWELS),
        "HIRAGANA_COUNT": HIRAGANA_COUNT,
        "HIRAGANA_PHONETIC_COUNT": HIRAGANA_PHONETIC_COUNT,
        "h_rows": h_rows,
        "n": n,
        "n_orient": n_orient,
        "n_pair": n_pair,
        "n_rows": n_rows,
        "n_cols": n_cols,
        "SLOT_COUNT": DAKUTEN_SLOT_COUNT,
    }

    font_bust = 0
    if os.path.isfile(KANA_FONT):
        font_bust = int(os.path.getmtime(KANA_FONT))

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8"/>
<title>edenia kana — chart × D4 × smalls × slices × dakuten</title>
<link rel="stylesheet" href="./edenia-kana.css"/>
<style>
@font-face {{
  font-family: 'edenia-kana-local';
  src: url("./edenia-kana.woff2?v={font_bust}") format("woff2"),
       url("./edenia-kana.ttf?v={font_bust}") format("truetype");
  font-weight: normal;
  font-style: normal;
  font-display: block;
}}
:root {{ color-scheme: dark; --fs: {font_size}px; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 24px;
  background: #111; color: #eee;
  font-family: system-ui, sans-serif;
}}
h1 {{ font-size: 20px; font-weight: 600; margin: 0 0 8px; }}
.meta {{ color: #888; font-size: 13px; margin-bottom: 20px; line-height: 1.5; }}
.controls {{
  display: flex; flex-wrap: wrap; gap: 12px; align-items: end;
  margin-bottom: 16px; padding: 12px; background: #1a1a1a; border-radius: 8px;
}}
label {{ display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #aaa; }}
label.row {{ flex-direction: row; align-items: center; gap: 6px; }}
select, button {{
  font: inherit; font-size: 14px; padding: 6px 10px;
  background: #222; color: #eee; border: 1px solid #444; border-radius: 4px;
}}
button {{ cursor: pointer; background: #2a4a3a; border-color: #3a6a5a; }}
button:hover {{ background: #355a48; }}
button.danger {{ background: #4a2a2a; border-color: #6a3a3a; }}
#status {{ font-size: 13px; color: #8af; margin: 8px 0 16px; min-height: 1.2em; }}
#out {{
  font-family: edenia-kana-local, 'edenia kana', sans-serif;
  font-size: var(--fs);
  line-height: 1.35;
  display: flex; flex-wrap: wrap; gap: 2px;
  font-feature-settings: "rlig" 1, "liga" 1, "ccmp" 1;
}}
.cell {{
  display: inline-flex; flex-direction: column; align-items: center;
  min-width: 1.2em; padding: 4px 2px;
  border-bottom: 1px solid #222;
}}
.glyph {{ line-height: 1; }}
.tag {{
  font-family: system-ui, sans-serif; font-size: 9px; color: #666;
  max-width: 9em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}
h2 {{
  font-size: 14px; color: #aaa; width: 100%; margin: 20px 0 8px;
  border-bottom: 1px solid #333; padding-bottom: 4px;
  font-family: system-ui, sans-serif; font-weight: 600;
}}
.chart {{
  display: grid;
  grid-template-columns: auto repeat({n_cols}, minmax(2.2em, 1fr));
  gap: 2px 4px;
  width: 100%;
  font-family: edenia-kana-local, 'edenia kana', sans-serif;
  font-size: var(--fs);
  margin-bottom: 12px;
}}
.chart .hdr, .chart .rowlab {{
  font-family: system-ui, sans-serif; font-size: 11px; color: #888;
  display: flex; align-items: center; justify-content: center;
}}
.chart .g {{
  display: flex; align-items: center; justify-content: center;
  min-height: 1.4em; border: 1px solid #222; padding: 2px;
}}
</style>
</head>
<body>
<h1>edenia kana — chart × D4 × smalls × slices × dakuten</h1>
<p class="meta">
  {n:,} logical ({HIRAGANA_COUNT} hiragana + {n - HIRAGANA_COUNT} katakana,
  phonetic rows + length/gemination each) · {D4_COUNT} D4 orientations as PUA
  (even=full, odd=small @ U+E000…; halfwidth @ U+ED00…) · slices FE00 / FE01 ·
  dakuten {len(marks)} (sample).<br/>
  Orientation gallery: {n_orient:,} · pairwise slices: {n_pair:,} each mode
  (on demand). Diacritics optional: 1–{DAKUTEN_SLOT_COUNT} marks →
  {DAKUTEN_SLOT_CYCLE} (contour anchors); CGJ (U+034F) skips a slot.
</p>

<div class="controls">
  <label>Kana A
    <select id="selA"></select>
  </label>
  <label>A orientation
    <select id="orientA"></select>
  </label>
  <label>Kana B
    <select id="selB"></select>
  </label>
  <label>B orientation
    <select id="orientB"></select>
  </label>
  <label>Size
    <select id="sizeMode">
      <option value="full">full (even)</option>
      <option value="small">small (odd)</option>
      <option value="hw">halfwidth (U+ED00 even)</option>
      <option value="hw-small">halfwidth small (U+ED00 odd)</option>
    </select>
  </label>
  <label>Slice
    <select id="sliceMode"></select>
  </label>
  <label class="row"><input type="checkbox" id="wantMarks"/> diacritics</label>
  <label>Mark
    <select id="pickMark"></select>
  </label>
  <label>Mark count
    <select id="markCount">
      {dakuten_count_options_html()}
    </select>
  </label>
  <label>Skip (CGJ)
    <select id="skipSlots">
      {dakuten_skip_options_html()}
    </select>
  </label>
  <button type="button" id="btnChart">Chart grid (selected orient)</button>
  <button type="button" id="btnOrientA">All orientations of A</button>
  <button type="button" id="btnAllOrient">All orientations (all kana)</button>
  <button type="button" id="btnSlice">Render slice A×B</button>
  <button type="button" id="btnSlicesForA">All B slices for A</button>
  <button type="button" id="btnSliceOrientGrid">A×B all orients (slice)</button>
  <button type="button" id="btnEverything" class="danger">Render everything</button>
</div>
<div id="status"></div>
<div id="out"></div>

<script type="application/json" id="data">{json.dumps(payload, ensure_ascii=False)}</script>
<script>
let DATA = JSON.parse(document.getElementById('data').textContent);
let out = document.getElementById('out');
let status = document.getElementById('status');
let selA = document.getElementById('selA');
let selB = document.getElementById('selB');
let orientA = document.getElementById('orientA');
let orientB = document.getElementById('orientB');
let sliceMode = document.getElementById('sliceMode');
let sizeMode = document.getElementById('sizeMode');
let pickMark = document.getElementById('pickMark');
const SLOT_N = DATA.SLOT_COUNT || 8;

function fillKanaSelect(sel) {{
  DATA.KANA.forEach((k, i) => {{
    let o = document.createElement('option');
    o.value = String(i);
    o.textContent = k.label + '  ' + k.srcCh + '  src U+' + k.src.toString(16).toUpperCase();
    sel.appendChild(o);
  }});
}}
function fillOrient(sel) {{
  DATA.ORIENT_LABEL.forEach((lab, i) => {{
    let o = document.createElement('option');
    o.value = String(i);
    o.textContent = lab + ' (o=' + i + ')';
    sel.appendChild(o);
  }});
}}
function fillSlice() {{
  DATA.SLICE_MODES.forEach((m, i) => {{
    let o = document.createElement('option');
    o.value = String(i);
    o.textContent = m.label;
    sliceMode.appendChild(o);
  }});
}}
function fillMarks() {{
  if (!DATA.MARKS.length) {{
    let o = document.createElement('option');
    o.value = '0'; o.textContent = '(no marks in font)';
    pickMark.appendChild(o);
    return;
  }}
  DATA.MARKS.forEach((m, i) => {{
    let o = document.createElement('option');
    o.value = String(i);
    o.textContent = m.ch + ' U+' + m.cp.toString(16).toUpperCase() + ' ' + m.short;
    pickMark.appendChild(o);
  }});
}}
fillKanaSelect(selA);
fillKanaSelect(selB);
fillOrient(orientA);
fillOrient(orientB);
fillSlice();
fillMarks();
selA.value = '0';
selB.value = '1';
orientA.value = '0';
orientB.value = '0';
sliceMode.value = '1';
pickMark.value = '0';
sizeMode.value = 'full';

function sizeKind() {{ return sizeMode.value; }}
function useSmall() {{
  let s = sizeKind();
  return s === 'small' || s === 'hw-small';
}}
function useHw() {{
  let s = sizeKind();
  return s === 'hw' || s === 'hw-small';
}}
function cpFor(idx, orientIdx) {{
  let k = DATA.KANA[idx];
  if (sizeKind() === 'hw-small') return k.hwSmall[orientIdx];
  if (sizeKind() === 'hw') return k.hwFull[orientIdx];
  return useSmall() ? k.small[orientIdx] : k.full[orientIdx];
}}
function kanaChar(idx, orientIdx) {{
  return String.fromCodePoint(cpFor(idx, orientIdx));
}}
function tagFor(idx, orientIdx) {{
  let k = DATA.KANA[idx];
  let lab = DATA.ORIENT_LABEL[orientIdx] || ('o' + orientIdx);
  let sz = (useHw() ? 'ₕ' : '') + (useSmall() ? 'ₛ' : '');
  return k.label + sz + '.' + lab;
}}
function skipPrefix() {{
  let n = Math.max(0, Math.min(SLOT_N - 1, +document.getElementById('skipSlots').value || 0));
  return String.fromCodePoint(0x034F).repeat(n);
}}
function markSuffix() {{
  if (!document.getElementById('wantMarks').checked) return '';
  if (!DATA.MARKS.length) return '';
  let m = DATA.MARKS[+pickMark.value];
  if (!m) return '';
  let n = Math.max(1, Math.min(SLOT_N, +document.getElementById('markCount').value || 1));
  return skipPrefix() + m.ch.repeat(n);
}}
function markTag() {{
  if (!document.getElementById('wantMarks').checked) return '';
  if (!DATA.MARKS.length) return '';
  let m = DATA.MARKS[+pickMark.value];
  let n = Math.max(1, Math.min(SLOT_N, +document.getElementById('markCount').value || 1));
  if (!m) return '';
  let skip = Math.max(0, Math.min(SLOT_N - 1, +document.getElementById('skipSlots').value || 0));
  return (skip ? '+CGJ×' + skip : '') + '+' + m.short + '×' + n;
}}
function currentSlice() {{
  return DATA.SLICE_MODES[+sliceMode.value] || DATA.SLICE_MODES[0];
}}
function cell(text, tag) {{
  let d = document.createElement('div');
  d.className = 'cell';
  let g = document.createElement('div');
  g.className = 'glyph';
  g.textContent = text;
  let t = document.createElement('div');
  t.className = 'tag';
  t.textContent = tag;
  d.appendChild(g);
  d.appendChild(t);
  return d;
}}
function heading(s) {{
  let h = document.createElement('h2');
  h.textContent = s;
  return h;
}}
function clearOut() {{ out.replaceChildren(); }}
function setStatus(s) {{ status.textContent = s; }}

function chartIdx(r, c) {{
  // Trailing length/gemination sit after each script block; skip them in the grid.
  if (r < DATA.h_rows) return r * DATA.n_cols + c;
  return DATA.HIRAGANA_COUNT + (r - DATA.h_rows) * DATA.n_cols + c;
}}

function renderChart(orientIdx) {{
  clearOut();
  let sz = sizeKind();
  out.appendChild(heading('Chart · ' + DATA.ORIENT_LABEL[orientIdx] + ' · ' + sz
    + (document.getElementById('wantMarks').checked ? ' + dakuten' : '')));
  let grid = document.createElement('div');
  grid.className = 'chart';
  grid.appendChild(Object.assign(document.createElement('div'), {{className:'hdr', textContent:''}}));
  DATA.VOWELS.forEach(v => {{
    grid.appendChild(Object.assign(document.createElement('div'), {{className:'hdr', textContent:v}}));
  }});
  let ms = markSuffix();
  for (let r = 0; r < DATA.n_rows; r++) {{
    grid.appendChild(Object.assign(document.createElement('div'), {{
      className:'rowlab', textContent: DATA.ROW_LABELS[r]
    }}));
    for (let c = 0; c < DATA.n_cols; c++) {{
      let idx = chartIdx(r, c);
      let g = document.createElement('div');
      g.className = 'g';
      g.textContent = kanaChar(idx, orientIdx) + ms;
      g.title = tagFor(idx, orientIdx) + ' U+' + cpFor(idx, orientIdx).toString(16).toUpperCase();
      grid.appendChild(g);
    }}
  }}
  out.appendChild(grid);
  setStatus('Chart ' + DATA.n_rows + '×' + DATA.n_cols + ' @ o=' + orientIdx + ' (' + sz + ')');
}}

function renderOrientations(indices) {{
  clearOut();
  let sz = sizeKind();
  out.appendChild(heading('Orientations (PUA D4) · ' + sz
    + (document.getElementById('wantMarks').checked ? ' + dakuten' : '')));
  let n = 0;
  let ms = markSuffix();
  let mt = markTag();
  for (let i of indices) {{
    for (let o = 0; o < DATA.ORIENT_LABEL.length; o++) {{
      out.appendChild(cell(kanaChar(i, o) + ms, tagFor(i, o) + mt));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' orientation cells');
}}

function sliceText(ai, ao, bi, bo, mode) {{
  let text = kanaChar(ai, ao) + kanaChar(bi, bo);
  let tag = tagFor(ai, ao) + '+' + tagFor(bi, bo);
  if (mode.cp != null) {{
    text += String.fromCodePoint(mode.cp);
    tag += '+' + mode.id;
  }}
  text += markSuffix();
  tag += markTag();
  return {{text, tag}};
}}

function renderSlice(ai, ao, bi, bo) {{
  clearOut();
  let mode = currentSlice();
  out.appendChild(heading('Slice: ' + mode.label));
  let one = sliceText(ai, ao, bi, bo, mode);
  out.appendChild(cell(one.text, one.tag));
  out.appendChild(heading('Same pair · all orientation combos'));
  let n = 0;
  for (let oa = 0; oa < DATA.ORIENT_LABEL.length; oa++) {{
    for (let ob = 0; ob < DATA.ORIENT_LABEL.length; ob++) {{
      let s = sliceText(ai, oa, bi, ob, mode);
      out.appendChild(cell(s.text, s.tag));
      n++;
    }}
  }}
  setStatus('Rendered slice + ' + n + ' orientation combos');
}}

function renderSlicesForA(ai, ao) {{
  clearOut();
  let mode = currentSlice();
  if (mode.cp == null) {{
    setStatus('Pick FE00 or FE01 slice mode first');
    return;
  }}
  out.appendChild(heading('A=' + tagFor(ai, ao) + ' × every B · ' + mode.label));
  let n = 0;
  for (let bi = 0; bi < DATA.KANA.length; bi++) {{
    let s = sliceText(ai, ao, bi, 0, mode);
    out.appendChild(cell(s.text, s.tag));
    n++;
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' slices for A');
}}

function renderEverything() {{
  if (!confirm('Render chart + all orientations + identity pairwise H/V slices?')) return;
  clearOut();
  let n = 0;
  let ms = markSuffix();
  let mt = markTag();
  renderChart(0);
  // renderChart clears; rebuild manually for everything path
  clearOut();
  out.appendChild(heading('Chart · id · ' + sizeKind()));
  let grid = document.createElement('div');
  grid.className = 'chart';
  grid.appendChild(Object.assign(document.createElement('div'), {{className:'hdr', textContent:''}}));
  DATA.VOWELS.forEach(v => {{
    grid.appendChild(Object.assign(document.createElement('div'), {{className:'hdr', textContent:v}}));
  }});
  for (let r = 0; r < DATA.n_rows; r++) {{
    grid.appendChild(Object.assign(document.createElement('div'), {{
      className:'rowlab', textContent: DATA.ROW_LABELS[r]
    }}));
    for (let c = 0; c < DATA.n_cols; c++) {{
      let idx = chartIdx(r, c);
      let g = document.createElement('div');
      g.className = 'g';
      g.textContent = kanaChar(idx, 0) + ms;
      grid.appendChild(g);
      n++;
    }}
  }}
  out.appendChild(grid);
  out.appendChild(heading('All orientations'));
  for (let i = 0; i < DATA.KANA.length; i++) {{
    for (let o = 0; o < DATA.ORIENT_LABEL.length; o++) {{
      out.appendChild(cell(kanaChar(i, o) + ms, tagFor(i, o) + mt));
      n++;
    }}
  }}
  for (let mode of DATA.SLICE_MODES) {{
    if (mode.cp == null) continue;
    out.appendChild(heading('All pairwise ' + mode.label + ' (identity×identity)'));
    for (let ai = 0; ai < DATA.KANA.length; ai++) {{
      for (let bi = 0; bi < DATA.KANA.length; bi++) {{
        let s = sliceText(ai, 0, bi, 0, mode);
        out.appendChild(cell(s.text, s.tag));
        n++;
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' cells');
}}

document.getElementById('btnChart').onclick = () =>
  renderChart(+orientA.value);
document.getElementById('btnOrientA').onclick = () =>
  renderOrientations([+selA.value]);
document.getElementById('btnAllOrient').onclick = () =>
  renderOrientations(DATA.KANA.map((_, i) => i));
document.getElementById('btnSlice').onclick = () =>
  renderSlice(+selA.value, +orientA.value, +selB.value, +orientB.value);
document.getElementById('btnSliceOrientGrid').onclick = () =>
  renderSlice(+selA.value, +orientA.value, +selB.value, +orientB.value);
document.getElementById('btnSlicesForA').onclick = () =>
  renderSlicesForA(+selA.value, +orientA.value);
document.getElementById('btnEverything').onclick = renderEverything;

renderChart(0);
</script>
</body>
</html>
""")
    print(
        f"Kana: N={n} marks={len(marks)}  orientations={n_orient:,}  "
        f"pairwise={n_pair:,}  -> {path}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-o", "--out", default=DEFAULT_OUT)
    p.add_argument("--font-size", type=int, default=48)
    p.add_argument("--mark-limit", type=int, default=64)
    args = p.parse_args()
    write_html(args.out, font_size=args.font_size, mark_limit=args.mark_limit)


if __name__ == "__main__":
    main()
