#!/usr/bin/env python3
"""Build an HTML gallery of Yi orientations × slices × dakuten.

Inventory: NuosuSIL Yi syllables / radicals present in ``edenia yi``.

Combinations (rendered on demand):

  * Every Yi × {{∅, FE01..FE07}} orientation
  * Slice pairs: A × B × {{FE08 horizontal, FE09 vertical}}
  * Optional dakuten marks (0–8; TR→CR→BR→TM→BM→TL→CL→BL; CGJ skips a slot)

Usage
-----
  python yi_html.py
  python yi_html.py -o dist/yi/all-yi-vs.html
"""

from __future__ import annotations

from shared_diacritics import (
    DAKUTEN_SLOT_COUNT,
    DAKUTEN_SLOT_CYCLE,
    dakuten_count_options_html,
    dakuten_skip_options_html,
)
from shared_half_cells import TransformMode

import argparse
import json
import os
import unicodedata
from typing import List

from fontTools.ttLib import TTFont

from shared_half_cells import (
    YI_ORIENTATION_MODES,
    load_inventory,
    resolve_nuosu_path,
    uvs_selector_for_mode,
)
from yi_slice import SLICE_H_CP, SLICE_V_CP

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "yi", "all-yi-vs.html")
IN_DIR = os.path.join(SCRIPT_DIR, "src")
YI_FONT = os.path.join(SCRIPT_DIR, "dist", "yi", "edenia-yi.woff2")
JULIAMONO = os.path.join(SCRIPT_DIR, "src", "JuliaMono-Regular.ttf")

# Orientation UVS: FE00 = identity (omit), FE01..FE07 = non-id.
ORIENT_VS: List[int | None] = [None] + [
    uvs_selector_for_mode(i)
    for i, (_vs, _r, _fx, _fy, suffix) in enumerate[TransformMode](YI_ORIENTATION_MODES)
    if suffix is not None
]
ORIENT_MARK = ["", "¹", "²", "³", "⁴", "⁵", "⁶", "⁷"]
ORIENT_LABEL = ["id"] + [
    suffix for _vs, _r, _fx, _fy, suffix in YI_ORIENTATION_MODES if suffix is not None
]

SLICE_MODES = [
    {"id": "none", "cp": None, "label": "none"},
    {"id": "H", "cp": SLICE_H_CP, "label": "H FE08 (top+bot)"},
    {"id": "V", "cp": SLICE_V_CP, "label": "V FE09 (left+right)"},
]


def yi_entries() -> List[dict]:
    inv = load_inventory(resolve_nuosu_path(IN_DIR))
    out: List[dict] = []
    for cp in inv.src_cps:
        try:
            name = unicodedata.name(chr(cp), f"U+{cp:04X}")
        except ValueError:
            name = f"U+{cp:04X}"
        short = name.split()[-1] if name else f"{cp:04X}"
        out.append({"cp": cp, "ch": chr(cp), "name": name, "short": short})
    return out


def dakuten_mark_entries(limit: int = 64) -> List[dict]:
    cps: List[int] = []
    if os.path.isfile(YI_FONT):
        tt = TTFont(YI_FONT)
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
    yi = yi_entries()
    marks = dakuten_mark_entries(limit=mark_limit)
    n = len(yi)
    n_orient = n * len(ORIENT_VS)
    n_pair = n * n * len(ORIENT_VS) * len(ORIENT_VS)
    total = n_orient + n_pair

    payload = {
        "YI": yi,
        "MARKS": marks,
        "ORIENT_VS": ORIENT_VS,
        "ORIENT_MARK": ORIENT_MARK,
        "ORIENT_LABEL": ORIENT_LABEL,
        "SLICE_MODES": SLICE_MODES,
        "n": n,
        "n_orient": n_orient,
        "n_pair": n_pair,
        "total": total,
        "SLOT_COUNT": DAKUTEN_SLOT_COUNT,
    }

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            f"""<!doctype html>
<html lang="ii">
<head>
<meta charset="utf-8"/>
<title>edenia yi — Yi × orientations × slices × dakuten</title>
<link rel="stylesheet" href="./edenia-yi.css"/>
<style>
  :root {{ color-scheme: dark; }}
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
  select, button, input[type=number] {{
    font: inherit; font-size: 14px; padding: 6px 10px;
    background: #222; color: #eee; border: 1px solid #444; border-radius: 4px;
  }}
  button {{ cursor: pointer; background: #2a4a3a; border-color: #3a6a5a; }}
  button:hover {{ background: #355a48; }}
  button.danger {{ background: #4a2a2a; border-color: #6a3a3a; }}
  #status {{ font-size: 13px; color: #8af; margin: 8px 0 16px; min-height: 1.2em; }}
  #out {{
    font-family: 'edenia yi', serif;
    font-size: {font_size}px;
    line-height: 1.35;
    display: flex; flex-wrap: wrap; gap: 2px;
  }}
  .cell {{
    display: inline-flex; flex-direction: column; align-items: center;
    min-width: 1.2em; padding: 4px 2px;
    border-bottom: 1px solid #222;
  }}
  .glyph {{ line-height: 1; }}
  .tag {{
    font-family: system-ui, sans-serif; font-size: 9px; color: #666;
    max-width: 8em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
  h2 {{
    font-size: 14px; color: #aaa; width: 100%; margin: 20px 0 8px;
    border-bottom: 1px solid #333; padding-bottom: 4px;
    font-family: system-ui, sans-serif; font-weight: 600;
  }}
</style>
</head>
<body>
<h1>edenia yi — Yi × orientations × slices × dakuten</h1>
<p class="meta">
  {n:,} Yi · {len(ORIENT_VS)} orientations (FE00 id / FE01..FE07) ·
  slices FE08 horizontal / FE09 vertical · dakuten {len(marks)} (sample).<br/>
  Orientation gallery: {n_orient:,} · pairwise slices: {n_pair:,} each mode
  (on demand). Diacritics optional: 1–{DAKUTEN_SLOT_COUNT} marks →
  {DAKUTEN_SLOT_CYCLE}; CGJ skips a slot.
</p>

<div class="controls">
  <label>First Yi (A)
    <select id="selA"></select>
  </label>
  <label>A orientation
    <select id="orientA"></select>
  </label>
  <label>Second Yi (B)
    <select id="selB"></select>
  </label>
  <label>B orientation
    <select id="orientB"></select>
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
  <button type="button" id="btnOrientA">All orientations of A</button>
  <button type="button" id="btnAllOrient">All orientations (all Yi)</button>
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
let pickMark = document.getElementById('pickMark');
const SLOT_N = DATA.SLOT_COUNT || 8;

function fillYiSelect(sel) {{
  DATA.YI.forEach((y, i) => {{
    let o = document.createElement('option');
    o.value = String(i);
    o.textContent = y.ch + ' U+' + y.cp.toString(16).toUpperCase() + ' ' + y.short;
    sel.appendChild(o);
  }});
}}
function fillOrient(sel) {{
  DATA.ORIENT_LABEL.forEach((lab, i) => {{
    let o = document.createElement('option');
    o.value = String(i);
    o.textContent = lab + (DATA.ORIENT_VS[i] != null
      ? ' (FE' + (DATA.ORIENT_VS[i] - 0xFE00).toString(16).toUpperCase().padStart(2, '0') + ')'
      : '');
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
fillYiSelect(selA);
fillYiSelect(selB);
fillOrient(orientA);
fillOrient(orientB);
fillSlice();
fillMarks();
selA.value = '0';
selB.value = '1';
orientA.value = '0';
orientB.value = '0';
sliceMode.value = '1'; // default H slice
pickMark.value = '0';

function vsChar(vs) {{
  return vs == null ? '' : String.fromCodePoint(vs);
}}
function yiPiece(idx, orientIdx) {{
  let y = DATA.YI[idx];
  return y.ch + vsChar(DATA.ORIENT_VS[orientIdx]);
}}
function tagFor(idx, orientIdx) {{
  let y = DATA.YI[idx];
  let mark = DATA.ORIENT_MARK[orientIdx] || '';
  return y.short + mark;
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

function renderOrientations(indices) {{
  clearOut();
  out.appendChild(heading('Orientations (id / FE01..FE07)'
    + (document.getElementById('wantMarks').checked ? ' + dakuten' : '')));
  let n = 0;
  let ms = markSuffix();
  let mt = markTag();
  for (let i of indices) {{
    for (let o = 0; o < DATA.ORIENT_VS.length; o++) {{
      out.appendChild(cell(yiPiece(i, o) + ms, tagFor(i, o) + mt));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' orientation cells');
}}

function sliceText(ai, ao, bi, bo, mode) {{
  let text = yiPiece(ai, ao) + yiPiece(bi, bo);
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
  for (let oa = 0; oa < DATA.ORIENT_VS.length; oa++) {{
    for (let ob = 0; ob < DATA.ORIENT_VS.length; ob++) {{
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
    setStatus('Pick FE08 or FE09 slice mode first');
    return;
  }}
  out.appendChild(heading('A=' + tagFor(ai, ao) + ' × every B · ' + mode.label));
  let n = 0;
  for (let bi = 0; bi < DATA.YI.length; bi++) {{
    let s = sliceText(ai, ao, bi, 0, mode);
    out.appendChild(cell(s.text, s.tag));
    n++;
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' slices for A');
}}

function renderEverything() {{
  if (!confirm('Render orientations + identity pairwise H and V slices?')) return;
  clearOut();
  let n = 0;
  let ms = markSuffix();
  let mt = markTag();
  out.appendChild(heading('All orientations'));
  for (let i = 0; i < DATA.YI.length; i++) {{
    for (let o = 0; o < DATA.ORIENT_VS.length; o++) {{
      out.appendChild(cell(yiPiece(i, o) + ms, tagFor(i, o) + mt));
      n++;
    }}
  }}
  for (let mode of DATA.SLICE_MODES) {{
    if (mode.cp == null) continue;
    out.appendChild(heading('All pairwise ' + mode.label + ' (identity×identity)'));
    for (let ai = 0; ai < DATA.YI.length; ai++) {{
      for (let bi = 0; bi < DATA.YI.length; bi++) {{
        let s = sliceText(ai, 0, bi, 0, mode);
        out.appendChild(cell(s.text, s.tag));
        n++;
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' cells');
}}

document.getElementById('btnOrientA').onclick = () =>
  renderOrientations([+selA.value]);
document.getElementById('btnAllOrient').onclick = () =>
  renderOrientations(DATA.YI.map((_, i) => i));
document.getElementById('btnSlice').onclick = () =>
  renderSlice(+selA.value, +orientA.value, +selB.value, +orientB.value);
document.getElementById('btnSliceOrientGrid').onclick = () =>
  renderSlice(+selA.value, +orientA.value, +selB.value, +orientB.value);
document.getElementById('btnSlicesForA').onclick = () =>
  renderSlicesForA(+selA.value, +orientA.value);
document.getElementById('btnEverything').onclick = renderEverything;

renderSlice(0, 0, 1, 0);
</script>
</body>
</html>
"""
        )
    print(
        f"Yi: N={n} marks={len(marks)}  orientations={n_orient:,}  "
        f"pairwise={n_pair:,}  -> {path}"
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build Yi orientation/slice/dakuten HTML gallery"
    )
    p.add_argument("-o", "--out", default=DEFAULT_OUT)
    p.add_argument("--font-size", type=int, default=48)
    p.add_argument("--mark-limit", type=int, default=64)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    write_html(args.out, font_size=args.font_size, mark_limit=args.mark_limit)
