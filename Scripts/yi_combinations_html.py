#!/usr/bin/env python3
"""Build an HTML gallery of Yi orientations + FE08 overlays.

Inventory: NuosuSIL Yi syllables / radicals present in ``panyi``.

Combinations available (rendered on demand — full static dump is huge):

  * Every Yi × {∅, FE01..FE07} orientation
  * Every ordered pair A×B with orientations, joined by FE08 (superimpose)
  * Longer FE08 chains (A B FE08 C FE08 …)

Usage
-----
  python yi_combinations_html.py
  python yi_combinations_html.py -o dist/yi/all-yi-vs.html
"""

from __future__ import annotations

import argparse
import json
import os
import unicodedata
from typing import List

from yi_halfwidth import (
    STACK_MARK_CP,
    YI_ORIENTATION_MODES,
    load_inventory,
    resolve_nuosu_path,
    uvs_selector_for_mode,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "yi", "all-yi-vs.html")
IN_DIR = os.path.join(SCRIPT_DIR, "src")

# Orientation UVS: FE00 = identity (omit from string), FE01..FE07 = non-id.
ORIENT_VS: List[int | None] = [None] + [
    uvs_selector_for_mode(i)
    for i, (_vs, _r, _fx, _fy, suffix) in enumerate(YI_ORIENTATION_MODES)
    if suffix is not None
]
ORIENT_MARK = ["", "¹", "²", "³", "⁴", "⁵", "⁶", "⁷"]
ORIENT_LABEL = ["id"] + [
    suffix for _vs, _r, _fx, _fy, suffix in YI_ORIENTATION_MODES if suffix is not None
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


def write_html(path: str, *, font_size: int) -> None:
    yi = yi_entries()
    n = len(yi)
    n_orient = n * len(ORIENT_VS)
    n_pair = n * n * len(ORIENT_VS) * len(ORIENT_VS)
    total = n_orient + n_pair

    payload = {
        "YI": yi,
        "ORIENT_VS": ORIENT_VS,
        "ORIENT_MARK": ORIENT_MARK,
        "ORIENT_LABEL": ORIENT_LABEL,
        "STACK": STACK_MARK_CP,
        "n": n,
        "n_orient": n_orient,
        "n_pair": n_pair,
        "total": total,
    }

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            f"""<!doctype html>
<html lang="ii">
<head>
<meta charset="utf-8"/>
<title>panyi — Yi orientations × FE08 overlays</title>
<link rel="stylesheet" href="./panyi.css"/>
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
  select, button, input[type=number] {{
    font: inherit; font-size: 14px; padding: 6px 10px;
    background: #222; color: #eee; border: 1px solid #444; border-radius: 4px;
  }}
  button {{ cursor: pointer; background: #2a4a3a; border-color: #3a6a5a; }}
  button:hover {{ background: #355a48; }}
  button.danger {{ background: #4a2a2a; border-color: #6a3a3a; }}
  #status {{ font-size: 13px; color: #8af; margin: 8px 0 16px; min-height: 1.2em; }}
  #out {{
    font-family: panyi, serif;
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
    max-width: 7em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
  h2 {{
    font-size: 14px; color: #aaa; width: 100%; margin: 20px 0 8px;
    border-bottom: 1px solid #333; padding-bottom: 4px;
    font-family: system-ui, sans-serif; font-weight: 600;
  }}
</style>
</head>
<body>
<h1>panyi — Yi × orientations × FE08 overlays</h1>
<p class="meta">
  {n:,} Yi characters · {len(ORIENT_VS)} orientations (FE00 identity / FE01..FE07 D4) ·
  FE08: all but the last glyph before it become zero-width; chain with more FE08.<br/>
  Orientation gallery: {n_orient:,} · pairwise overlays: {n_pair:,}
  (render on demand — full static dump would be multi-GB).
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
  <label>Third Yi (C, optional)
    <select id="selC"><option value="">—</option></select>
  </label>
  <label>C orientation
    <select id="orientC"></select>
  </label>
  <button type="button" id="btnPair">Render this overlay</button>
  <button type="button" id="btnOrientA">All orientations of A</button>
  <button type="button" id="btnAllOrient">All orientations (all Yi)</button>
  <button type="button" id="btnPairsForA">All B overlays for A</button>
  <button type="button" id="btnEverything" class="danger">Render everything</button>
</div>
<div id="status"></div>
<div id="out"></div>

<script type="application/json" id="data">{json.dumps(payload, ensure_ascii=False)}</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const out = document.getElementById('out');
const status = document.getElementById('status');
const selA = document.getElementById('selA');
const selB = document.getElementById('selB');
const selC = document.getElementById('selC');
const orientA = document.getElementById('orientA');
const orientB = document.getElementById('orientB');
const orientC = document.getElementById('orientC');

function fillYiSelect(sel, withEmpty) {{
  if (withEmpty) {{
    const o = document.createElement('option');
    o.value = ''; o.textContent = '—';
    sel.appendChild(o);
  }}
  DATA.YI.forEach((y, i) => {{
    const o = document.createElement('option');
    o.value = String(i);
    o.textContent = y.ch + ' U+' + y.cp.toString(16).toUpperCase() + ' ' + y.short;
    sel.appendChild(o);
  }});
}}
function fillOrient(sel) {{
  DATA.ORIENT_LABEL.forEach((lab, i) => {{
    const o = document.createElement('option');
    o.value = String(i);
    o.textContent = lab + (DATA.ORIENT_VS[i] != null
      ? ' (FE0' + (DATA.ORIENT_VS[i] - 0xFE00).toString(16).toUpperCase() + ')'
      : '');
    sel.appendChild(o);
  }});
}}
fillYiSelect(selA, false);
fillYiSelect(selB, false);
fillYiSelect(selC, true);
fillOrient(orientA);
fillOrient(orientB);
fillOrient(orientC);
selA.value = '0';
selB.value = '1';
orientA.value = '0';
orientB.value = '0';
orientC.value = '0';

function vsChar(vs) {{
  return vs == null ? '' : String.fromCodePoint(vs);
}}
function yiPiece(idx, orientIdx) {{
  const y = DATA.YI[idx];
  return y.ch + vsChar(DATA.ORIENT_VS[orientIdx]);
}}
function tagFor(idx, orientIdx) {{
  const y = DATA.YI[idx];
  const mark = DATA.ORIENT_MARK[orientIdx] || '';
  return y.short + mark;
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

function renderOrientations(indices) {{
  clearOut();
  out.appendChild(heading('Orientations (id / FE01..FE07)'));
  let n = 0;
  for (const i of indices) {{
    for (let o = 0; o < DATA.ORIENT_VS.length; o++) {{
      out.appendChild(cell(yiPiece(i, o), tagFor(i, o)));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' orientation cells');
}}

function renderPair(ai, ao, bi, bo, ci, co) {{
  clearOut();
  const stack = String.fromCodePoint(DATA.STACK);
  let text = yiPiece(ai, ao) + yiPiece(bi, bo) + stack;
  let tag = tagFor(ai, ao) + '+' + tagFor(bi, bo) + '+FE08';
  if (ci != null && ci !== '') {{
    text += yiPiece(+ci, co) + stack;
    tag += '+' + tagFor(+ci, co) + '+FE08';
  }}
  out.appendChild(heading('Overlay'));
  out.appendChild(cell(text, tag));
  // Also show each orientation of the pair for context
  out.appendChild(heading('Same pair · all orientation combos'));
  let n = 0;
  for (let oa = 0; oa < DATA.ORIENT_VS.length; oa++) {{
    for (let ob = 0; ob < DATA.ORIENT_VS.length; ob++) {{
      const t = yiPiece(ai, oa) + yiPiece(bi, ob) + stack;
      out.appendChild(cell(t, tagFor(ai, oa) + '+' + tagFor(bi, ob)));
      n++;
    }}
  }}
  setStatus('Rendered overlay + ' + n + ' orientation combos for this pair');
}}

function renderPairsForA(ai, ao) {{
  clearOut();
  const stack = String.fromCodePoint(DATA.STACK);
  out.appendChild(heading('A=' + tagFor(ai, ao) + ' × every B (identity orient)'));
  let n = 0;
  for (let bi = 0; bi < DATA.YI.length; bi++) {{
    const t = yiPiece(ai, ao) + yiPiece(bi, 0) + stack;
    out.appendChild(cell(t, tagFor(ai, ao) + '+' + tagFor(bi, 0)));
    n++;
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' overlays for A');
}}

function renderEverything() {{
  if (!confirm('Render all ' + DATA.total.toLocaleString() +
      ' cells? This will stress the browser.')) return;
  clearOut();
  const stack = String.fromCodePoint(DATA.STACK);
  out.appendChild(heading('All orientations'));
  let n = 0;
  for (let i = 0; i < DATA.YI.length; i++) {{
    for (let o = 0; o < DATA.ORIENT_VS.length; o++) {{
      out.appendChild(cell(yiPiece(i, o), tagFor(i, o)));
      n++;
    }}
  }}
  out.appendChild(heading('All pairwise FE08 overlays (identity×identity)'));
  for (let ai = 0; ai < DATA.YI.length; ai++) {{
    for (let bi = 0; bi < DATA.YI.length; bi++) {{
      const t = yiPiece(ai, 0) + yiPiece(bi, 0) + stack;
      out.appendChild(cell(t, tagFor(ai, 0) + '+' + tagFor(bi, 0)));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() +
    ' cells (identity pairwise only; full orient×orient skipped)');
}}

document.getElementById('btnOrientA').onclick = () =>
  renderOrientations([+selA.value]);
document.getElementById('btnAllOrient').onclick = () =>
  renderOrientations(DATA.YI.map((_, i) => i));
document.getElementById('btnPair').onclick = () =>
  renderPair(+selA.value, +orientA.value, +selB.value, +orientB.value,
             selC.value, +orientC.value);
document.getElementById('btnPairsForA').onclick = () =>
  renderPairsForA(+selA.value, +orientA.value);
document.getElementById('btnEverything').onclick = renderEverything;

// Default smoke: first pair overlay
renderPair(0, 0, 1, 0, '', 0);
</script>
</body>
</html>
"""
        )
    print(
        f"Yi: N={n}  orientations={n_orient:,}  "
        f"pairwise={n_pair:,}  → {path}"
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build Yi orientation/overlay HTML gallery")
    p.add_argument("-o", "--out", default=DEFAULT_OUT)
    p.add_argument("--font-size", type=int, default=48)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    write_html(args.out, font_size=args.font_size)
