#!/usr/bin/env python3
"""Build an HTML gallery of Yi orientations × slices × dakuten.

Inventory: NuosuSIL Yi syllables / radicals present in `edenia yi`.
Segment faces (`h` / `t` / `qv` / `qh`, plus base) stack via CSS unicode-range.

Combinations (rendered on demand):

  * Every Yi × {{∅, FE01..FE07}} orientation
  * Slice pairs: half FE08–FE0F, third VS17–26, quarter VS on qv/qh
    (first segment + FE00 overlay, then opposing segment)
  * Optional dakuten marks (0–8; TR→CR→BR→TM→BM→TL→CL→BL; CGJ skips a slot)

Usage
-----
  python yi_html.py
  python yi_html.py -o dist/yi/all-yi-vs.html
"""

from __future__ import annotations

from hangul_diacritics import (
    DAKUTEN_SLOT_COUNT,
    DAKUTEN_SLOT_CYCLE,
    dakuten_count_options_html,
    dakuten_skip_options_html,
)
from shared_half_cells import (
    OV_SELECTOR_CP,
    YI_ORIENTATION_MODES,
    load_inventory,
    resolve_nuosu_path,
    uvs_selector_for_mode,
)
from yi_slice import (
    SLICE_BL_CP,
    SLICE_BOT_CP,
    SLICE_BR_CP,
    SLICE_LEFT_CP,
    SLICE_RIGHT_CP,
    SLICE_TL_CP,
    SLICE_TOP_CP,
    SLICE_TR_CP,
)
from shared_third_cells import THIRD_VS_SLOTS
from shared_quarter_cells import QUARTER_VS_SLOTS_H, QUARTER_VS_SLOTS_V
from edenia_names import SEGMENT_FACE_STACK_ORDER, family_yi_variant

import argparse
import json
import os
import unicodedata
from typing import List

from fontTools.ttLib import TTFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "yi", "all-yi-vs.html")
IN_DIR = os.path.join(SCRIPT_DIR, "src")
YI_FONT = os.path.join(SCRIPT_DIR, "dist", "yi", "edenia-yi.woff2")
JULIAMONO = os.path.join(SCRIPT_DIR, "src", "JuliaMono-Regular.ttf")

# Orientation UVS: bare = identity, FE01..FE07 = non-id.
ORIENT_VS: List[int | None] = [
    uvs_selector_for_mode(i) for i, _mode in enumerate(YI_ORIENTATION_MODES)
]
ORIENT_MARK = ["", "¹", "²", "³", "⁴", "⁵", "⁶", "⁷"]
ORIENT_LABEL = [
    ("id" if suffix is None else suffix)
    for _vs, _r, _fx, _fy, suffix in YI_ORIENTATION_MODES
]

# Default stack is h+base. Slice modes pin a single face (see mode["face"]).
YI_FONT_STACK = ", ".join(
    f"'{family_yi_variant(v)}'" for v in SEGMENT_FACE_STACK_ORDER
)
YI_FACE_FAMILY = {
    v: family_yi_variant(v) for v in ("", "h", "t", "q", "qv", "qh")
}


def _vs_by_suffix(slots) -> dict:
    return {suf: cp for cp, _sel, suf, *_rest in slots}


def _slice_mode(
    id_: str, parts: list[int | None], label: str, *, face: str = "h"
) -> dict:
    cps = [p for p in parts if p is not None]
    return {
        "id": id_,
        "face": face,
        "parts": cps,
        "a": cps[0] if len(cps) > 0 else None,
        "b": cps[1] if len(cps) > 1 else None,
        "c": cps[2] if len(cps) > 2 else None,
        "d": cps[3] if len(cps) > 3 else None,
        "arity": len(cps),
        "label": label,
    }


def _cover_modes(
    *,
    prefix: str,
    covers: list[tuple[str, ...]],
    by_suf: dict,
) -> list[dict]:
    out: list[dict] = []
    for sufs in covers:
        cps = [by_suf[s] for s in sufs]
        bits: list[str] = []
        for i, cp in enumerate(cps):
            bits.append(f"U+{cp:X}")
            if i < len(cps) - 1:
                bits.append("FE00")
        out.append(
            _slice_mode(
                f"{prefix}:{('+'.join(sufs))}",
                cps,
                f"{prefix} {'/'.join(sufs)} ({' '.join(bits)})",
                face=prefix,
            )
        )
    return out


_T_BY = _vs_by_suffix(THIRD_VS_SLOTS)
_QV_BY = _vs_by_suffix(QUARTER_VS_SLOTS_V)
_QH_BY = _vs_by_suffix(QUARTER_VS_SLOTS_H)

SLICE_MODES: list[dict] = [
    _slice_mode("none", [], "none", face=""),
    _slice_mode(
        "TB",
        [SLICE_TOP_CP, SLICE_BOT_CP],
        "h FE08 FE00 / FE09 (top+bot)",
        face="h",
    ),
    _slice_mode(
        "LR",
        [SLICE_LEFT_CP, SLICE_RIGHT_CP],
        "h FE0A FE00 / FE0B (left+right)",
        face="h",
    ),
    _slice_mode(
        "TLBR",
        [SLICE_TL_CP, SLICE_BR_CP],
        "h FE0C FE00 / FE0D (tl+br Δ)",
        face="h",
    ),
    _slice_mode(
        "TRBL",
        [SLICE_TR_CP, SLICE_BL_CP],
        "h FE0E FE00 / FE0F (tr+bl Δ)",
        face="h",
    ),
    *_cover_modes(
        prefix="t",
        covers=[
            ("t3t", "t3mb"),
            ("t3tm", "t3b"),
            ("t3l", "t3cr"),
            ("t3lc", "t3r"),
            ("t3t", "t3m", "t3b"),
            ("t3l", "t3c", "t3r"),
        ],
        by_suf=_T_BY,
    ),
    *_cover_modes(
        prefix="qv",
        covers=[
            ("q4th", "q4bh"),
            ("q4t", "q4b3"),
            ("q4t3", "q4b"),
            ("q4th", "q4nb", "q4b"),
            ("q4bh", "q4t", "q4nt"),
            ("q4t", "q4mh", "q4b"),
            ("q4t", "q4nt", "q4nb", "q4b"),
        ],
        by_suf=_QV_BY,
    ),
    *_cover_modes(
        prefix="qh",
        covers=[
            ("q4lh", "q4rh"),
            ("q4l", "q4r3"),
            ("q4l3", "q4r"),
            ("q4lh", "q4nr", "q4r"),
            ("q4rh", "q4l", "q4nl"),
            ("q4l", "q4mc", "q4r"),
            ("q4l", "q4nl", "q4nr", "q4r"),
        ],
        by_suf=_QH_BY,
    ),
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
        from hangul_diacritics import (
            load_dakuten_marks_from_stack,
            resolve_dakuten_mark_font_stack,
        )

        try:
            stack = resolve_dakuten_mark_font_stack(IN_DIR)
            kept, _ = load_dakuten_marks_from_stack(stack, 1000)
            cps = kept
        except Exception:
            pass

    from hangul_diacritics import dakuten_mark_label, visible_dakuten_cps

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
        "FACE_FAMILY": YI_FACE_FAMILY,
        "FONT_STACK": YI_FONT_STACK,
        "OV": OV_SELECTOR_CP,
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
    font-family: {YI_FONT_STACK}, serif;
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
  {n:,} Yi · {len(ORIENT_VS)} orientations (bare / FE01..FE07) ·
  slices h/t/qv/qh (FE00 + FE08–F / VS17–26 / quarter VS) ·
  dakuten {len(marks)} (sample).<br/>
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
  <label>Third Yi (C)
    <select id="selC"></select>
  </label>
  <label>C orientation
    <select id="orientC"></select>
  </label>
  <label>Fourth Yi (D)
    <select id="selD"></select>
  </label>
  <label>D orientation
    <select id="orientD"></select>
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
  <button type="button" id="btnSlice">Render slice A×B×C×D</button>
  <button type="button" id="btnSlicesForA">All B slices for A</button>
  <button type="button" id="btnSliceOrientGrid">A×B all orients (2-way)</button>
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
let selC = document.getElementById('selC');
let selD = document.getElementById('selD');
let orientA = document.getElementById('orientA');
let orientB = document.getElementById('orientB');
let orientC = document.getElementById('orientC');
let orientD = document.getElementById('orientD');
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
    o.textContent = (m.arity ? m.arity + '-way · ' : '') + m.label;
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
fillYiSelect(selC);
fillYiSelect(selD);
fillOrient(orientA);
fillOrient(orientB);
fillOrient(orientC);
fillOrient(orientD);
fillSlice();
fillMarks();
selA.value = '0';
selB.value = '1';
selC.value = '2';
selD.value = '3';
orientA.value = '0';
orientB.value = '0';
orientC.value = '0';
orientD.value = '0';
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
function faceFamily(face) {{
  if (face == null || face === '') return DATA.FONT_STACK;
  let fam = (DATA.FACE_FAMILY && DATA.FACE_FAMILY[face]) || null;
  if (!fam) return DATA.FONT_STACK;
  let base = (DATA.FACE_FAMILY && DATA.FACE_FAMILY['']) || 'edenia yi';
  return "'" + fam + "', '" + base + "'";
}}
function applySliceFace(mode) {{
  out.style.fontFamily = faceFamily(mode && mode.face) + ', serif';
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
  applySliceFace({{face: 'h'}});
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

function cpTag(cp) {{
  return 'U+' + cp.toString(16).toUpperCase();
}}
function selectedSlots() {{
  return {{
    idx: [+selA.value, +selB.value, +selC.value, +selD.value],
    orient: [+orientA.value, +orientB.value, +orientC.value, +orientD.value],
  }};
}}
function sliceText(indices, orients, mode) {{
  let parts = mode.parts || [];
  if (!parts.length && mode.a != null) {{
    parts = [mode.a, mode.b].filter(p => p != null);
  }}
  let text = '';
  let tag = '';
  if (!parts.length) {{
    text = yiPiece(indices[0], orients[0]);
    tag = tagFor(indices[0], orients[0]);
  }} else {{
    for (let i = 0; i < parts.length; i++) {{
      if (i) tag += '+';
      text += yiPiece(indices[i], orients[i]);
      tag += tagFor(indices[i], orients[i]);
      text += String.fromCodePoint(parts[i]);
      tag += '+' + cpTag(parts[i]);
      if (i < parts.length - 1) {{
        text += String.fromCodePoint(DATA.OV);
        tag += '+FE00';
      }}
    }}
  }}
  text += markSuffix();
  tag += markTag();
  return {{text, tag}};
}}

function renderSlice() {{
  clearOut();
  let mode = currentSlice();
  applySliceFace(mode);
  let slots = selectedSlots();
  out.appendChild(heading('Slice: ' + mode.label));
  let one = sliceText(slots.idx, slots.orient, mode);
  out.appendChild(cell(one.text, one.tag));
  let arity = mode.arity || (mode.parts ? mode.parts.length : 2);
  if (arity !== 2) {{
    setStatus('Rendered ' + arity + '-way slice (orient grid is 2-way only)');
    return;
  }}
  out.appendChild(heading('Same pair · all orientation combos'));
  let n = 0;
  let ai = slots.idx[0], bi = slots.idx[1];
  for (let oa = 0; oa < DATA.ORIENT_VS.length; oa++) {{
    for (let ob = 0; ob < DATA.ORIENT_VS.length; ob++) {{
      let s = sliceText([ai, bi], [oa, ob], mode);
      out.appendChild(cell(s.text, s.tag));
      n++;
    }}
  }}
  setStatus('Rendered slice + ' + n + ' orientation combos');
}}

function renderSlicesForA() {{
  clearOut();
  let mode = currentSlice();
  applySliceFace(mode);
  let slots = selectedSlots();
  if (!(mode.parts && mode.parts.length) && mode.a == null) {{
    setStatus('Pick a slice mode first');
    return;
  }}
  let ai = slots.idx[0], ao = slots.orient[0];
  out.appendChild(heading('A=' + tagFor(ai, ao) + ' × every B · ' + mode.label));
  let n = 0;
  for (let bi = 0; bi < DATA.YI.length; bi++) {{
    let idx = slots.idx.slice();
    let ori = slots.orient.slice();
    idx[1] = bi;
    ori[1] = 0;
    let s = sliceText(idx, ori, mode);
    out.appendChild(cell(s.text, s.tag));
    n++;
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' slices for A');
}}

function renderEverything() {{
  if (!confirm('Render orientations + identity pairwise slices for all modes?')) return;
  clearOut();
  let n = 0;
  let ms = markSuffix();
  let mt = markTag();
  applySliceFace({{face: 'h'}});
  out.appendChild(heading('All orientations'));
  for (let i = 0; i < DATA.YI.length; i++) {{
    for (let o = 0; o < DATA.ORIENT_VS.length; o++) {{
      out.appendChild(cell(yiPiece(i, o) + ms, tagFor(i, o) + mt));
      n++;
    }}
  }}
  for (let mode of DATA.SLICE_MODES) {{
    if (!(mode.parts && mode.parts.length)) continue;
    if ((mode.arity || mode.parts.length) !== 2) continue;
    applySliceFace(mode);
    out.appendChild(heading('All pairwise ' + mode.label + ' (identity×identity)'));
    for (let ai = 0; ai < DATA.YI.length; ai++) {{
      for (let bi = 0; bi < DATA.YI.length; bi++) {{
        let s = sliceText([ai, bi], [0, 0], mode);
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
document.getElementById('btnSlice').onclick = () => renderSlice();
document.getElementById('btnSliceOrientGrid').onclick = () => renderSlice();
document.getElementById('btnSlicesForA').onclick = () => renderSlicesForA();
document.getElementById('btnEverything').onclick = renderEverything;

renderSlice();
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
