#!/usr/bin/env python3
"""Build one HTML gallery of *all* Unicode Hangul conjoining jamo × VS.

Jamo inventories (Unicode, excluding fillers / unassigned):
  Choseong  U+1100..115E, U+A960..A97C
  Jungseong U+1161..11A7, U+D7B0..D7C6
  Jongseong U+11A8..11FF, U+D7CB..D7FB

Every L × {∅,FE01,FE02,FE03} × V × {∅,FE01,FE02,FE03} × (∅ | T × VS)
is available. The page embeds the codepoint lists and renders sections on
demand in the browser (full static expansion would be ~100M cells / multi-GB).

See: https://en.wikipedia.org/wiki/List_of_Hangul_jamo

Usage
-----
  python hangul_combinations_html.py
  python hangul_combinations_html.py -o dist/hangul/all-jamo-vs.html
"""

from __future__ import annotations

import argparse
import json
import os
import unicodedata
from typing import List, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "hangul", "all-jamo-vs.html")

L_RANGES = ((0x1100, 0x115E), (0xA960, 0xA97C))
V_RANGES = ((0x1161, 0x11A7), (0xD7B0, 0xD7C6))
T_RANGES = ((0x11A8, 0x11FF), (0xD7CB, 0xD7FB))

VS = [None, 0xFE01, 0xFE02, 0xFE03]
VS_MARK = ["", "¹", "²", "³"]


def assigned_cps(ranges: Tuple[Tuple[int, int], ...]) -> List[dict]:
    out: List[dict] = []
    for a, b in ranges:
        for cp in range(a, b + 1):
            try:
                name = unicodedata.name(chr(cp))
            except ValueError:
                continue
            if "FILLER" in name:
                continue
            # Short label: last token of Unicode name, or the character itself.
            short = name.split()[-1].replace("-", "")
            out.append({"cp": cp, "ch": chr(cp), "name": name, "short": short})
    return out


def write_html(path: str, *, font_size: int) -> None:
    L = assigned_cps(L_RANGES)
    V = assigned_cps(V_RANGES)
    T = assigned_cps(T_RANGES)
    n_open = len(L) * 4 * len(V) * 4
    n_closed = len(L) * 4 * len(V) * 4 * len(T) * 4
    total = n_open + n_closed

    payload = {
        "L": L,
        "V": V,
        "T": T,
        "VS": [None, 0xFE01, 0xFE02, 0xFE03],
        "VS_MARK": VS_MARK,
        "total": total,
    }

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<title>panhangul — all Unicode Hangul jamo × VS</title>
<link rel="stylesheet" href="./panhangul.css"/>
<style>
:root {{ --fs: {font_size}px; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: #111; color: #eee;
  font-family: panhangul, panhanguls, sans-serif;
  font-size: var(--fs);
}}
header {{
  position: sticky; top: 0; z-index: 5;
  background: #111e; backdrop-filter: blur(6px);
  border-bottom: 1px solid #333; padding: 12px 20px 14px;
  font-family: system-ui, sans-serif;
}}
h1 {{ font-size: 18px; font-weight: 600; color: #ccc; margin: 0 0 6px; }}
.meta {{ font-size: 12px; color: #777; margin: 0 0 10px; line-height: 1.4; }}
.controls {{
  display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center;
  font-size: 12px; color: #aaa;
}}
.controls label {{ display: inline-flex; align-items: center; gap: 4px; }}
.controls select, .controls button {{
  background: #1a1a1a; color: #ddd; border: 1px solid #444;
  border-radius: 4px; padding: 4px 8px; font-size: 12px;
}}
.controls button {{ cursor: pointer; }}
.controls button:hover {{ background: #252525; }}
#stats {{ margin-left: auto; color: #666; }}
main {{ padding: 12px 20px 80px; }}
section {{ margin: 0 0 20px; }}
section h2 {{
  font: 600 13px system-ui, sans-serif; color: #9a9a9a;
  margin: 0 0 8px; padding: 6px 0; border-bottom: 1px solid #2a2a2a;
  position: sticky; top: 88px; background: #111; z-index: 2;
}}
.grid {{
  display: flex; flex-wrap: wrap; gap: 3px 8px; align-items: flex-end;
}}
.cell {{
  display: flex; flex-direction: column; align-items: center;
  line-height: 1.15; min-width: 1.1em;
}}
.cell i {{
  font: 8px/1.1 system-ui, sans-serif; color: #555; font-style: normal;
  white-space: nowrap; margin-bottom: 1px;
}}
.cell b {{
  font-weight: normal; border: 1px solid #1e1e1e; padding: 2px 4px;
  border-radius: 2px;
}}
.empty {{ font: 13px system-ui, sans-serif; color: #555; padding: 24px 0; }}
</style>
</head>
<body>
<header>
  <h1>All Unicode Hangul jamo × VS</h1>
  <p class="meta">
    Choseong {len(L)} · Jungseong {len(V)} · Jongseong {len(T)}
    (conjoining jamo from Unicode / 
    <a href="https://en.wikipedia.org/wiki/List_of_Hangul_jamo" style="color:#6af">List of Hangul jamo</a>).
    Combinations: <strong>{total:,}</strong>
    = L×VS×V×VS×(∅ | T×VS). Sections render on demand — open a pair below.
    ¹ FE01 · ² FE02 · ³ FE03. Hard-refresh after rebuilding fonts.
  </p>
  <div class="controls">
    <label>Choseong
      <select id="pickL"></select>
    </label>
    <label>Jungseong
      <select id="pickV"></select>
    </label>
    <label><input type="checkbox" id="wantT" checked/> include batchim</label>
    <label><input type="checkbox" id="wantVS" checked/> include VS</label>
    <label><input type="checkbox" id="labels" checked/> labels</label>
    <button type="button" id="btnOne">Render this L+V</button>
    <button type="button" id="btnAllL">Render all V for this L</button>
    <button type="button" id="btnDump">Render everything (huge)</button>
    <span id="stats"></span>
  </div>
</header>
<main id="main"><p class="empty">Choose a choseong + jungseong, then Render.</p></main>
<script>
const DATA = {json.dumps(payload, ensure_ascii=False, separators=(",", ":"))};

function cpChars(cps) {{
  return cps.map(c => String.fromCodePoint(c)).join("");
}}
function vsList(want) {{
  return want ? DATA.VS : [null];
}}
function mark(i) {{ return DATA.VS_MARK[i] || ""; }}

function buildSeq(L, li, V, vi, T, ti) {{
  const cps = [L.cp];
  if (DATA.VS[li] != null) cps.push(DATA.VS[li]);
  cps.push(V.cp);
  if (DATA.VS[vi] != null) cps.push(DATA.VS[vi]);
  if (T) {{
    cps.push(T.cp);
    if (DATA.VS[ti] != null) cps.push(DATA.VS[ti]);
  }}
  return cps;
}}

function labelFor(L, li, V, vi, T, ti) {{
  let s = L.ch + mark(li) + "+" + V.ch + mark(vi);
  if (T) s += "+" + T.ch + mark(ti);
  return s;
}}

function renderPair(L, V, {{wantT, wantVS, labels}}) {{
  const sec = document.createElement("section");
  const h = document.createElement("h2");
  h.textContent = L.ch + " + " + V.ch + "  (U+" + L.cp.toString(16).toUpperCase()
    + " + U+" + V.cp.toString(16).toUpperCase() + ")";
  sec.appendChild(h);
  const grid = document.createElement("div");
  grid.className = "grid";
  const Lvs = vsList(wantVS);
  const Vvs = vsList(wantVS);
  const Tvs = wantT ? vsList(wantVS) : [null];
  let n = 0;
  const frag = document.createDocumentFragment();
  for (let li = 0; li < Lvs.length; li++) {{
    for (let vi = 0; vi < Vvs.length; vi++) {{
      // open
      {{
        const cell = document.createElement("div");
        cell.className = "cell";
        if (labels) {{
          const i = document.createElement("i");
          i.textContent = labelFor(L, li, V, vi, null, 0);
          cell.appendChild(i);
        }}
        const b = document.createElement("b");
        b.textContent = cpChars(buildSeq(L, li, V, vi, null, 0));
        cell.appendChild(b);
        frag.appendChild(cell);
        n++;
      }}
      if (!wantT) continue;
      for (const T of DATA.T) {{
        for (let ti = 0; ti < Tvs.length; ti++) {{
          const cell = document.createElement("div");
          cell.className = "cell";
          if (labels) {{
            const i = document.createElement("i");
            i.textContent = labelFor(L, li, V, vi, T, ti);
            cell.appendChild(i);
          }}
          const b = document.createElement("b");
          b.textContent = cpChars(buildSeq(L, li, V, vi, T, ti));
          cell.appendChild(b);
          frag.appendChild(cell);
          n++;
        }}
      }}
    }}
  }}
  grid.appendChild(frag);
  sec.appendChild(grid);
  return {{sec, n}};
}}

function fillSelects() {{
  const pickL = document.getElementById("pickL");
  const pickV = document.getElementById("pickV");
  for (const x of DATA.L) {{
    const o = document.createElement("option");
    o.value = x.cp;
    o.textContent = x.ch + " U+" + x.cp.toString(16).toUpperCase();
    pickL.appendChild(o);
  }}
  for (const x of DATA.V) {{
    const o = document.createElement("option");
    o.value = x.cp;
    o.textContent = x.ch + " U+" + x.cp.toString(16).toUpperCase();
    pickV.appendChild(o);
  }}
  // Default to ᄑ + ᅮ (common smoke pair) when present.
  pickL.value = "4369"; // U+1111
  pickV.value = "4462"; // U+116E
}}

function opts() {{
  return {{
    wantT: document.getElementById("wantT").checked,
    wantVS: document.getElementById("wantVS").checked,
    labels: document.getElementById("labels").checked,
  }};
}}

function find(list, cp) {{
  return list.find(x => x.cp === cp);
}}

document.getElementById("btnOne").onclick = () => {{
  const L = find(DATA.L, +document.getElementById("pickL").value);
  const V = find(DATA.V, +document.getElementById("pickV").value);
  const main = document.getElementById("main");
  main.textContent = "";
  const {{sec, n}} = renderPair(L, V, opts());
  main.appendChild(sec);
  document.getElementById("stats").textContent = n.toLocaleString() + " cells";
}};

document.getElementById("btnAllL").onclick = () => {{
  const L = find(DATA.L, +document.getElementById("pickL").value);
  const main = document.getElementById("main");
  main.textContent = "";
  let total = 0;
  const o = opts();
  for (const V of DATA.V) {{
    const {{sec, n}} = renderPair(L, V, o);
    main.appendChild(sec);
    total += n;
  }}
  document.getElementById("stats").textContent = total.toLocaleString() + " cells";
}};

document.getElementById("btnDump").onclick = () => {{
  const msg = "Render ALL " + DATA.total.toLocaleString()
    + " combinations? The page will become very large and may freeze the browser.";
  if (!confirm(msg)) return;
  const main = document.getElementById("main");
  main.textContent = "";
  let total = 0;
  const o = opts();
  const note = document.createElement("p");
  note.className = "empty";
  note.textContent = "Building…";
  main.appendChild(note);
  let li = 0;
  function pump() {{
    const t0 = performance.now();
    while (li < DATA.L.length && performance.now() - t0 < 40) {{
      const L = DATA.L[li++];
      for (const V of DATA.V) {{
        const {{sec, n}} = renderPair(L, V, o);
        main.appendChild(sec);
        total += n;
      }}
      document.getElementById("stats").textContent =
        total.toLocaleString() + " cells (" + li + "/" + DATA.L.length + " L)";
    }}
    if (li < DATA.L.length) {{
      requestAnimationFrame(pump);
    }} else {{
      note.remove();
      document.getElementById("stats").textContent = total.toLocaleString() + " cells";
    }}
  }}
  requestAnimationFrame(pump);
}};

fillSelects();
document.getElementById("btnOne").click();
</script>
</body>
</html>
"""
        )
    print(
        f"Jamo: L={len(L)} V={len(V)} T={len(T)}  "
        f"combinations={total:,}  → {path}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", default=DEFAULT_OUT)
    ap.add_argument("--font-size", type=int, default=40)
    args = ap.parse_args()
    write_html(args.output, font_size=args.font_size)


if __name__ == "__main__":
    main()
