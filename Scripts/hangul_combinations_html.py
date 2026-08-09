#!/usr/bin/env python3
"""Build one HTML gallery of *all* Unicode Hangul conjoining jamo × VS × dakuten.

Jamo inventories (Unicode, excluding fillers / unassigned):
  Choseong  U+1100..115E, U+A960..A97C
  Jungseong U+1161..11A7, U+D7B0..D7C6
  Jongseong U+11A8..11FF, U+D7CB..D7FB

Every L × {∅,FE01,FE02,FE03} × V × {∅,FE01,FE02,FE03} × (∅ | T × VS)
is available, optionally followed by 0–4 dakuten marks (TR→BR→TL→BL).

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

from fontTools.ttLib import TTFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "hangul", "all-jamo-vs.html")
HANGUL_FONT = os.path.join(SCRIPT_DIR, "dist", "hangul", "panhangul.woff2")
JULIAMONO = os.path.join(SCRIPT_DIR, "src", "JuliaMono-Regular.ttf")

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
            short = name.split()[-1].replace("-", "")
            out.append({"cp": cp, "ch": chr(cp), "name": name, "short": short})
    return out


def dakuten_mark_entries(limit: int = 64) -> List[dict]:
    """Marks installed in panhangul (``.mk`` cmap), else JuliaMono inventory."""
    cps: List[int] = []
    if os.path.isfile(HANGUL_FONT):
        tt = TTFont(HANGUL_FONT)
        try:
            cmap = tt.getBestCmap() or {}
            cps = sorted(
                cp for cp, name in cmap.items() if str(name).endswith(".mk")
            )
        finally:
            tt.close()
    if not cps and os.path.isfile(JULIAMONO):
        from yi_dakuten import iter_dakuten_codepoints, load_dakuten_marks

        tt = TTFont(JULIAMONO, fontNumber=0)
        try:
            cmap = {}
            for table in tt["cmap"].tables:
                if table.isUnicode():
                    cmap.update(table.cmap)
            cps = iter_dakuten_codepoints(cmap)
        finally:
            tt.close()
        # Prefer marks that survive load filters.
        try:
            kept, _ = load_dakuten_marks(JULIAMONO, 1000)
            cps = kept
        except Exception:
            pass

    out: List[dict] = []
    for cp in cps[: max(0, limit) or None]:
        ch = chr(cp)
        try:
            name = unicodedata.name(ch)
        except ValueError:
            name = f"U+{cp:04X}"
        short = name.split()[-1].replace("-", "") if name else f"{cp:04X}"
        out.append({"cp": cp, "ch": ch, "name": name, "short": short})
    return out


def write_html(path: str, *, font_size: int, mark_limit: int) -> None:
    L = assigned_cps(L_RANGES)
    V = assigned_cps(V_RANGES)
    T = assigned_cps(T_RANGES)
    marks = dakuten_mark_entries(limit=mark_limit)
    n_open = len(L) * 4 * len(V) * 4
    n_closed = len(L) * 4 * len(V) * 4 * len(T) * 4
    total = n_open + n_closed

    payload = {
        "L": L,
        "V": V,
        "T": T,
        "MARKS": marks,
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
<title>panhangul — all Unicode Hangul jamo × VS × dakuten</title>
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
  <h1>All Unicode Hangul jamo × VS × dakuten</h1>
  <p class="meta">
    Choseong {len(L)} · Jungseong {len(V)} · Jongseong {len(T)} ·
    dakuten marks {len(marks)} (sample)
    (<a href="https://en.wikipedia.org/wiki/List_of_Hangul_jamo" style="color:#6af">List of Hangul jamo</a>).
    Syllable combos: <strong>{total:,}</strong>
    = L×VS×V×VS×(∅ | T×VS). Toggle diacritics to append 1–4 marks
    (corners TR→BR→TL→BL). ¹ FE01 · ² FE02 · ³ FE03.
  </p>
  <div class="controls">
    <label>Choseong
      <select id="pickL"></select>
    </label>
    <label>Jungseong
      <select id="pickV"></select>
    </label>
    <label><input type="checkbox" id="wantT" checked/> batchim</label>
    <label><input type="checkbox" id="wantVS" checked/> VS</label>
    <label><input type="checkbox" id="wantMarks"/> diacritics</label>
    <label>Mark
      <select id="pickMark"></select>
    </label>
    <label>Mark count
      <select id="markCount">
        <option value="1">1 (TR)</option>
        <option value="2">2 (+BR)</option>
        <option value="3">3 (+TL)</option>
        <option value="4">4 (+BL)</option>
      </select>
    </label>
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

function markSuffix() {{
  if (!document.getElementById("wantMarks").checked) return [];
  if (!DATA.MARKS.length) return [];
  const mi = +document.getElementById("pickMark").value;
  const m = DATA.MARKS[mi];
  if (!m) return [];
  const n = Math.max(1, Math.min(4, +document.getElementById("markCount").value || 1));
  return Array(n).fill(m.cp);
}}

function buildSeq(L, li, V, vi, T, ti) {{
  const cps = [L.cp];
  if (DATA.VS[li] != null) cps.push(DATA.VS[li]);
  cps.push(V.cp);
  if (DATA.VS[vi] != null) cps.push(DATA.VS[vi]);
  if (T) {{
    cps.push(T.cp);
    if (DATA.VS[ti] != null) cps.push(DATA.VS[ti]);
  }}
  cps.push(...markSuffix());
  return cps;
}}

function labelFor(L, li, V, vi, T, ti) {{
  let s = L.ch + mark(li) + "+" + V.ch + mark(vi);
  if (T) s += "+" + T.ch + mark(ti);
  const ms = markSuffix();
  if (ms.length) {{
    const m = DATA.MARKS[+document.getElementById("pickMark").value];
    s += "+" + (m ? m.short : "mk") + "×" + ms.length;
  }}
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
  const pickMark = document.getElementById("pickMark");
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
  if (!DATA.MARKS.length) {{
    const o = document.createElement("option");
    o.value = "0"; o.textContent = "(no marks in font)";
    pickMark.appendChild(o);
  }} else {{
    DATA.MARKS.forEach((m, i) => {{
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = m.ch + " U+" + m.cp.toString(16).toUpperCase() + " " + m.short;
      pickMark.appendChild(o);
    }});
  }}
  pickL.value = "4369";
  pickV.value = "4462";
  pickMark.value = "0";
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
        f"Jamo: L={len(L)} V={len(V)} T={len(T)} marks={len(marks)}  "
        f"combinations={total:,}  -> {path}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", default=DEFAULT_OUT)
    ap.add_argument("--font-size", type=int, default=40)
    ap.add_argument(
        "--mark-limit",
        type=int,
        default=64,
        help="Max dakuten marks to list in the Mark select (default 64)",
    )
    args = ap.parse_args()
    write_html(args.output, font_size=args.font_size, mark_limit=args.mark_limit)


if __name__ == "__main__":
    main()
