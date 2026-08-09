#!/usr/bin/env python3
"""Build an HTML gallery for CJK squish digraph combinations.

Encoding (matches ``cjk_diac_marks`` / ``build_cjk``)::

    A FE00..FE07 FE0B FE0C–F   B FE00..FE07 FE0D–F

  Example::

    &#x660E;&#xFE00;&#xFE0B;&#xFE0C;&#x65E5;&#xFE02;&#xFE0D;
    (明 FE00 FE0B FE0C + 日 FE02 FE0D; each side picks its own D4)

  * First kanji is zero-width overlay (``FE0B`` + niche)
  * Niches oppose: FE0C↔FE0D (L↔R), FE0E↔FE0F (T↔B)
  * D4 orients are independent per character (FE00..FE07); GSUB liga only
  * Prefer pairs from different pancjk buckets (``cp >> 8``); seed 明日

Usage
-----
  python cjk_digraph_combinations_html.py
  python cjk_digraph_combinations_html.py --bucket 65 --bucket 66
  python cjk_digraph_combinations_html.py --pairs 48 --limit 128
  python cjk_digraph_combinations_html.py -o dist/subfonts/digraph-cjk.html
"""

from __future__ import annotations

import argparse
import json
import os
from typing import List, Sequence, Tuple

from build_cjk import IN_DIR, OUT_DIR as SUBFONTS_OUT
from cjk_diac_combinations_html import (
    BASE_ORIENT_LABEL,
    BASE_ORIENT_VS,
    DIGRAPH_NICHE_PAIRS,
    assigned_cps,
    digraph_pairs,
    opposing_orient_index,
    pancjk_font_stack,
    parse_range_spec,
)
from cjk_diac_marks import (
    OV_SELECTOR_CP,
    SQUISH_BOT_CP,
    SQUISH_LEFT_CP,
    SQUISH_RIGHT_CP,
    SQUISH_TOP_CP,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "subfonts", "digraph-cjk.html")


def write_html(
    path: str,
    *,
    ranges: Sequence[Tuple[int, int]],
    limit: int,
    max_pairs: int,
    font_size: int,
    font_dir: str,
) -> None:
    cjk = assigned_cps(ranges, limit=limit)
    n = len(cjk)
    if n < 2:
        raise SystemExit("need at least 2 CJK characters for digraphs")

    pairs = digraph_pairs(cjk, max_pairs=max(1, max_pairs))
    n_cross = sum(1 for _a, _b, cross in pairs if cross)
    n_orients = len(BASE_ORIENT_VS)
    # Independent D4 on each side: A_orient × B_orient × niches × pairs
    n_gallery = len(pairs) * len(DIGRAPH_NICHE_PAIRS) * n_orients * n_orients

    force_stack = n_cross > 0 or len({c["cp"] >> 8 for c in cjk}) > 1
    stack = pancjk_font_stack(font_dir, ranges=ranges, force_all=force_stack)
    opposing_oi = [opposing_orient_index(lab) for lab in BASE_ORIENT_LABEL]

    payload = {
        "CJK": cjk,
        "BASE_ORIENT_VS": BASE_ORIENT_VS,
        "BASE_ORIENT_LABEL": BASE_ORIENT_LABEL,
        "OV_SEL": OV_SELECTOR_CP,
        "SQUISH_R": SQUISH_RIGHT_CP,
        "SQUISH_L": SQUISH_LEFT_CP,
        "SQUISH_T": SQUISH_TOP_CP,
        "SQUISH_B": SQUISH_BOT_CP,
        "DIGRAPH_PAIRS": [
            {"a": a, "b": b, "cross": cross} for a, b, cross in pairs
        ],
        "DIGRAPH_NICHES": [{"a": a, "b": b} for a, b in DIGRAPH_NICHE_PAIRS],
        "OPPOSING_ORIENT_OI": opposing_oi,
        "n": n,
    }

    range_note = ", ".join(f"U+{a:X}–{b:X}" for a, b in ranges)
    if limit > 0:
        range_note += f" (embedded {n:,})"

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"/>
<title>pancjk — CJK squish digraphs</title>
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
code {{ color: #9cf; font-size: 11px; }}
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
  min-width: 1.15em; padding: 6px 4px;
  border-bottom: 1px solid #222;
}}
.glyph {{
  line-height: 1;
  /* Two half-cells share one advance cell visually */
  outline: 1px solid #2a2a2a;
  min-width: 1em; min-height: 1em;
  display: flex; align-items: center; justify-content: center;
}}
.tag {{
  font-family: system-ui, sans-serif; font-size: 9px; color: #666;
  max-width: 14em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}
.tag.cross {{ color: #8af; }}
h2 {{
  font-size: 13px; color: #aaa; width: 100%; margin: 18px 0 8px;
  border-bottom: 1px solid #333; padding-bottom: 4px;
  font-family: system-ui, sans-serif; font-weight: 600;
}}
</style>
</head>
<body>
<header>
  <h1>pancjk — CJK squish digraphs</h1>
  <p class="meta">
    Range: {range_note}<br/>
    Encoding: <code>A FE00..FE07 FE0B FE0C–F</code> + <code>B FE00..FE07 FE0D–F</code>
    (each side picks its own D4; niches oppose)<br/>
    First half zero-width · niches FE0C↔FE0D / FE0E↔FE0F · D4 independent<br/>
    Pairs: {len(pairs)} ({n_cross} cross-bucket) · full A×B orient gallery ≈ {n_gallery:,}
  </p>
  <div class="controls">
    <label>Orient A
      <select id="orientA"></select>
    </label>
    <label>Orient B
      <select id="orientB"></select>
    </label>
    <label>Niche pair
      <select id="nicheSel"></select>
    </label>
    <label>Pair start
      <input type="number" id="pairStart" min="0" max="{max(0, len(pairs) - 1)}" value="0"/>
    </label>
    <label>Pair count
      <input type="number" id="pairCount" min="1" max="{max(1, len(pairs))}" value="{min(32, max(1, len(pairs)))}"/>
    </label>
    <button type="button" id="btnDigraph">Render digraphs</button>
    <button type="button" id="btnOpposeB">B = opposing A</button>
    <button type="button" id="btnOrientGrid">All A×B orients</button>
    <button type="button" id="btnNicheGrid">All niche pairs</button>
    <button type="button" id="btnEverything" class="danger">Everything</button>
  </div>
</header>
<div id="status"></div>
<main><div id="out"></div></main>

<script type="application/json" id="data">{json.dumps(payload, ensure_ascii=False)}</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const out = document.getElementById('out');
const status = document.getElementById('status');
const orientA = document.getElementById('orientA');
const orientB = document.getElementById('orientB');
const nicheSel = document.getElementById('nicheSel');
const pairStart = document.getElementById('pairStart');
const pairCount = document.getElementById('pairCount');

const NICHE_LABEL = {{
  R: 'FE0C/.dk',
  L: 'FE0D/.dkl',
  T: 'FE0E/.dkt',
  B: 'FE0F/.dkb',
}};

function fillOrientSelect(sel) {{
  DATA.BASE_ORIENT_LABEL.forEach((lab, i) => {{
    const o = document.createElement('option');
    o.value = String(i);
    const v = DATA.BASE_ORIENT_VS[i];
    o.textContent = lab
      + (v != null ? ' (FE' + (v - 0xFE00).toString(16).toUpperCase().padStart(2, '0') + ')' : '');
    sel.appendChild(o);
  }});
  sel.value = '0';
}}
fillOrientSelect(orientA);
fillOrientSelect(orientB);

{{
  const all = document.createElement('option');
  all.value = 'all';
  all.textContent = 'all niches (L↔R, T↔B)';
  nicheSel.appendChild(all);
  DATA.DIGRAPH_NICHES.forEach((n, i) => {{
    const o = document.createElement('option');
    o.value = String(i);
    o.textContent = NICHE_LABEL[n.a] + ' + ' + NICHE_LABEL[n.b];
    nicheSel.appendChild(o);
  }});
  nicheSel.value = 'all';
}}

function vsChar(vs) {{ return vs == null ? '' : String.fromCodePoint(vs); }}
function cjkPiece(idx, oi) {{
  return DATA.CJK[idx].ch + vsChar(DATA.BASE_ORIENT_VS[oi]);
}}
function squishPiece(side) {{
  const sel = side === 'R' ? DATA.SQUISH_R
    : side === 'L' ? DATA.SQUISH_L
    : side === 'T' ? DATA.SQUISH_T
    : side === 'B' ? DATA.SQUISH_B
    : null;
  return sel != null ? String.fromCodePoint(sel) : '';
}}
function digraphFirst(idx, oi, side) {{
  // A FE00..FE07 FE0B FE0C–F — zero-width half overlay
  return cjkPiece(idx, oi)
    + String.fromCodePoint(DATA.OV_SEL)
    + squishPiece(side);
}}
function digraphSecond(idx, oi, side) {{
  // B FE00..FE07 FE0D–F — independent D4, opposing niche, keeps advance
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
function selectedPairs() {{
  const start = Math.max(0, Math.min(DATA.DIGRAPH_PAIRS.length - 1, +pairStart.value || 0));
  const count = Math.max(1, +pairCount.value || 1);
  return DATA.DIGRAPH_PAIRS.slice(start, start + count);
}}
function selectedNiches() {{
  if (nicheSel.value === 'all') return DATA.DIGRAPH_NICHES;
  return [DATA.DIGRAPH_NICHES[+nicheSel.value]];
}}
function selectedOrients() {{
  return {{
    oia: Math.max(0, Math.min(DATA.BASE_ORIENT_VS.length - 1, +orientA.value || 0)),
    oib: Math.max(0, Math.min(DATA.BASE_ORIENT_VS.length - 1, +orientB.value || 0)),
  }};
}}
function orientLabel(oia, oib) {{
  return (DATA.BASE_ORIENT_LABEL[oia] || 'id')
    + ' × ' + (DATA.BASE_ORIENT_LABEL[oib] || 'id');
}}
function cell(text, tag, cross) {{
  const d = document.createElement('div');
  d.className = 'cell';
  const g = document.createElement('div');
  g.className = 'glyph';
  g.textContent = text;
  const t = document.createElement('div');
  t.className = 'tag' + (cross ? ' cross' : '');
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

function renderDigraphs() {{
  clearOut();
  const pairs = selectedPairs();
  const niches = selectedNiches();
  const {{ oia, oib }} = selectedOrients();
  out.appendChild(heading('Digraphs · ' + orientLabel(oia, oib)));
  let n = 0;
  for (const p of pairs) {{
    for (const niche of niches) {{
      const text = digraphFirst(p.a, oia, niche.a)
        + digraphSecond(p.b, oib, niche.b);
      out.appendChild(cell(
        text,
        digraphTag(p.a, oia, niche.a, p.b, oib, niche.b, !!p.cross),
        !!p.cross));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' digraph cells ('
    + pairs.filter(p => p.cross).length + ' cross-bucket pairs in slice)');
}}

function renderOrientGrid() {{
  clearOut();
  const pairs = selectedPairs();
  const niches = selectedNiches();
  out.appendChild(heading('All A×B orients (independent D4)'));
  let n = 0;
  for (let oia = 0; oia < DATA.BASE_ORIENT_VS.length; oia++) {{
    for (let oib = 0; oib < DATA.BASE_ORIENT_VS.length; oib++) {{
      out.appendChild(heading(orientLabel(oia, oib)));
      for (const p of pairs) {{
        for (const niche of niches) {{
          const text = digraphFirst(p.a, oia, niche.a)
            + digraphSecond(p.b, oib, niche.b);
          out.appendChild(cell(
            text,
            digraphTag(p.a, oia, niche.a, p.b, oib, niche.b, !!p.cross),
            !!p.cross));
          n++;
        }}
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' digraph×orient cells');
}}

function renderNicheGrid() {{
  clearOut();
  const pairs = selectedPairs();
  const {{ oia, oib }} = selectedOrients();
  out.appendChild(heading('All niche pairs · ' + orientLabel(oia, oib)));
  let n = 0;
  for (const niche of DATA.DIGRAPH_NICHES) {{
    out.appendChild(heading(NICHE_LABEL[niche.a] + ' + ' + NICHE_LABEL[niche.b]));
    for (const p of pairs) {{
      const text = digraphFirst(p.a, oia, niche.a)
        + digraphSecond(p.b, oib, niche.b);
      out.appendChild(cell(
        text,
        digraphTag(p.a, oia, niche.a, p.b, oib, niche.b, !!p.cross),
        !!p.cross));
      n++;
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' digraph×niche cells');
}}

function renderEverything() {{
  clearOut();
  const pairs = selectedPairs();
  out.appendChild(heading('Everything (A×B orients × niches × pairs)'));
  let n = 0;
  for (let oia = 0; oia < DATA.BASE_ORIENT_VS.length; oia++) {{
    for (let oib = 0; oib < DATA.BASE_ORIENT_VS.length; oib++) {{
      out.appendChild(heading(orientLabel(oia, oib)));
      for (const niche of DATA.DIGRAPH_NICHES) {{
        for (const p of pairs) {{
          const text = digraphFirst(p.a, oia, niche.a)
            + digraphSecond(p.b, oib, niche.b);
          out.appendChild(cell(
            text,
            digraphTag(p.a, oia, niche.a, p.b, oib, niche.b, !!p.cross),
            !!p.cross));
          n++;
        }}
      }}
    }}
  }}
  setStatus('Rendered ' + n.toLocaleString() + ' cells');
}}

document.getElementById('btnDigraph').onclick = () => renderDigraphs();
document.getElementById('btnOpposeB').onclick = () => {{
  const oia = +orientA.value || 0;
  orientB.value = String(DATA.OPPOSING_ORIENT_OI[oia] ?? oia);
  renderDigraphs();
}};
document.getElementById('btnOrientGrid').onclick = () => renderOrientGrid();
document.getElementById('btnNicheGrid').onclick = () => renderNicheGrid();
document.getElementById('btnEverything').onclick = () => renderEverything();

renderDigraphs();
</script>
</body>
</html>
"""
        )
    print(f"Wrote {path}")
    print(f"  characters: {n}")
    print(f"  digraph pairs: {len(pairs)} ({n_cross} cross-bucket)")
    print(f"  niches: {len(DIGRAPH_NICHE_PAIRS)} · orients: {n_orients}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build CJK squish digraph HTML gallery (pancjk)"
    )
    p.add_argument("-o", "--out", default=DEFAULT_OUT)
    p.add_argument(
        "--font-dir",
        default=SUBFONTS_OUT,
        help="Directory with pancjk bucket fonts / pancjk.css",
    )
    p.add_argument(
        "--range",
        action="append",
        default=None,
        help="URO | ExtA | 4E | 4E00-4FFF (repeatable)",
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
        default=256,
        help="Max characters to embed (0 = no limit). Default: 256",
    )
    p.add_argument(
        "--pairs",
        type=int,
        default=32,
        help="Max digraph pairs (prefer cross-bucket). Default: 32",
    )
    p.add_argument("--font-size", type=int, default=56)
    p.add_argument("--in-dir", default=IN_DIR, help=argparse.SUPPRESS)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    specs: List[str] = []
    if args.range:
        specs.extend(args.range)
    if args.bucket:
        specs.extend(args.bucket)
    if not specs:
        # Default covers 日 (U+65E5) + 明 (U+660E) digraph demo.
        specs = ["65", "66"]
    ranges: List[Tuple[int, int]] = []
    for spec in specs:
        ranges.extend(parse_range_spec(spec))
    write_html(
        args.out,
        ranges=ranges,
        limit=args.limit,
        max_pairs=args.pairs,
        font_size=args.font_size,
        font_dir=args.font_dir,
    )


if __name__ == "__main__":
    main()
