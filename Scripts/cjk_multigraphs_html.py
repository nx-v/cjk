#!/usr/bin/env python3
"""Build a graphic CJK half-cell digraph composer.

Interactive HTML:

  • up to 4 character codes + D4 orientation each
  • H / V axis (horizontal vs vertical halves)
  • clickable templates for half-cell digraphs

Usage
-----
  python cjk_multigraphs_html.py
  python cjk_multigraphs_html.py --bucket 65
  python cjk_multigraphs_html.py -o dist/cjk/multigraph-cjk.html
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Sequence, Tuple

from build_cjk import OUT_DIR as CJK_OUT
from cjk_diacritics import (
    OV_SELECTOR_CP,
    SQUISH_BOT_CP,
    SQUISH_LEFT_CP,
    SQUISH_RIGHT_CP,
    SQUISH_TOP_CP,
)
from cjk_diacritics_html import (
    BASE_ORIENT_LABEL,
    BASE_ORIENT_VS,
    assigned_cps,
    edenia_cjk_font_stack,
    parse_range_spec,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "dist", "cjk", "multigraph-cjk.html")


def write_html(
    path: str,
    *,
    ranges: Sequence[Tuple[int, int]],
    limit: int,
    font_size: int,
    font_dir: str,
) -> None:
    cjk = assigned_cps(ranges, limit=limit)
    sample_cps = [int(e["cp"]) for e in cjk[:4]]
    while len(sample_cps) < 4:
        sample_cps.append(0x4E00 + len(sample_cps))
    sample = sample_cps

    abs_font = os.path.abspath(font_dir)
    try:
        rel_font = os.path.relpath(
            abs_font, os.path.dirname(os.path.abspath(path))
        ).replace("\\", "/")
    except ValueError:
        rel_font = abs_font.replace("\\", "/")

    face_css: List[str] = []
    if os.path.isdir(font_dir):
        from edenia_names import family_cjk, split_cjk_face_id

        for name in sorted(os.listdir(font_dir)):
            if not (name.endswith(".woff2") or name.endswith(".ttf")):
                continue
            face_id = os.path.splitext(name)[0]
            _core, var = split_cjk_face_id(face_id)
            if var not in ("", "h"):
                continue
            face_css.append(
                f"@font-face{{font-family:'{family_cjk(face_id)}';"
                f"src:url('{rel_font}/{name}');}}"
            )

    stack_all = edenia_cjk_font_stack(font_dir, ranges=ranges)
    stack_h = edenia_cjk_font_stack(font_dir, ranges=ranges, variants=("h",))

    half_vs = {
        "T": SQUISH_TOP_CP,
        "B": SQUISH_BOT_CP,
        "L": SQUISH_LEFT_CP,
        "R": SQUISH_RIGHT_CP,
    }
    data = {
        "OV": OV_SELECTOR_CP,
        "ORIENTs": list(BASE_ORIENT_VS),
        "ORIENT_LABELS": list(BASE_ORIENT_LABEL),
        "HALF_VS": half_vs,
        "FACES": {
            "h": stack_h,
        },
        "SAMPLE": [f"{cp:04X}" for cp in sample],
    }

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CJK segment composer</title>
<style>
{chr(10).join(face_css)}
:root {{
  --ink: #1a1c1a;
  --muted: #5c635c;
  --line: #c8cdc6;
  --paper: #f3f1eb;
  --panel: #fffdf8;
  --accent: #2f5d3a;
  --accent-soft: #dce8df;
  --slot: #e8ebe4;
  --preview-bg: #f7f5ef;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; min-height: 100%; }}
body {{
  font: 14px/1.45 "Segoe UI", system-ui, sans-serif;
  color: var(--ink);
  background:
    radial-gradient(ellipse 80% 50% at 10% -10%, #e4ebe2, transparent 55%),
    radial-gradient(ellipse 70% 45% at 100% 0%, #ebe6dc, transparent 50%),
    var(--paper);
}}
.app {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 1.25rem 1.25rem 2.5rem;
  display: grid;
  gap: 1rem;
}}
h1 {{
  margin: 0;
  font: 600 1.35rem/1.2 Georgia, "Times New Roman", serif;
  letter-spacing: 0.01em;
}}
.lead {{ margin: 0.15rem 0 0; color: var(--muted); font-size: 0.92rem; }}
.panel {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0.9rem 1rem;
}}
.row {{ display: flex; flex-wrap: wrap; gap: 0.65rem; align-items: end; }}
.field {{ display: grid; gap: 0.25rem; min-width: 0; }}
.field label {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }}
.chars {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.65rem; }}
@media (max-width: 720px) {{ .chars {{ grid-template-columns: 1fr 1fr; }} }}
.char-card {{
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0.55rem 0.6rem;
  background: #fbfaf6;
  display: grid;
  gap: 0.4rem;
}}
.char-card .idx {{ font-size: 0.7rem; color: var(--muted); }}
.char-card input, .char-card select, .axis select {{
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0.4rem 0.45rem;
  font: inherit;
  background: #fff;
}}
.char-preview {{
  height: 2.4rem;
  display: grid;
  place-items: center;
  font-family: {stack_all};
  font-size: 1.6rem;
  background: var(--slot);
  border-radius: 6px;
}}
.toolbar {{ display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: end; justify-content: space-between; }}
.axis {{ display: flex; gap: 0.75rem; align-items: end; }}
.layout {{
  display: grid;
  grid-template-columns: minmax(240px, 340px) minmax(0, 1fr);
  gap: 1rem;
}}
@media (max-width: 860px) {{ .layout {{ grid-template-columns: 1fr; }} }}
.templates h2, .stage h2 {{
  margin: 0 0 0.65rem;
  font: 600 0.8rem/1.2 system-ui, sans-serif;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--muted);
}}
.tpl-groups {{ display: grid; gap: 0.85rem; }}
.tpl-group-label {{
  font-size: 0.72rem;
  color: var(--muted);
  margin-bottom: 0.35rem;
}}
.tpl-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(88px, 1fr));
  gap: 0.45rem;
}}
.tpl {{
  appearance: none;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  padding: 0.4rem 0.35rem 0.45rem;
  cursor: pointer;
  text-align: center;
  color: inherit;
}}
.tpl:hover {{ border-color: var(--accent); }}
.tpl.active {{
  border-color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent);
}}
.tpl.disabled {{ opacity: 0.35; pointer-events: none; }}
.tpl .name {{ display: block; font-size: 0.68rem; margin-top: 0.3rem; color: var(--muted); }}
.diagram {{
  width: 54px;
  height: 54px;
  margin: 0 auto;
  border: 1.5px solid var(--ink);
  border-radius: 3px;
  display: grid;
  overflow: hidden;
  background: #fff;
}}
.diagram span {{
  display: grid;
  place-items: center;
  font-size: 0.62rem;
  font-weight: 600;
  border: 0 solid var(--ink);
}}
.diagram.v-2 {{ grid-template-rows: 1fr 1fr; }}
.diagram.v-2 span + span {{ border-top-width: 1px; }}
.diagram.h-2 {{ grid-template-columns: 1fr 1fr; }}
.diagram.h-2 span + span {{ border-left-width: 1px; }}
.diagram.v-3 {{ grid-template-rows: 1fr 1fr 1fr; }}
.diagram.v-3 span + span {{ border-top-width: 1px; }}
.diagram.h-3 {{ grid-template-columns: 1fr 1fr 1fr; }}
.diagram.h-3 span + span {{ border-left-width: 1px; }}
.diagram.v-4 {{ grid-template-rows: 1fr 1fr 1fr 1fr; }}
.diagram.v-4 span + span {{ border-top-width: 1px; }}
.diagram.h-4 {{ grid-template-columns: 1fr 1fr 1fr 1fr; }}
.diagram.h-4 span + span {{ border-left-width: 1px; }}
.diagram.v-2u {{ grid-template-rows: 2fr 1fr; }}
.diagram.v-2u span + span {{ border-top-width: 1px; }}
.diagram.v-2d {{ grid-template-rows: 1fr 2fr; }}
.diagram.v-2d span + span {{ border-top-width: 1px; }}
.diagram.h-2l {{ grid-template-columns: 2fr 1fr; }}
.diagram.h-2l span + span {{ border-left-width: 1px; }}
.diagram.h-2r {{ grid-template-columns: 1fr 2fr; }}
.diagram.h-2r span + span {{ border-left-width: 1px; }}
.diagram.v-31 {{ grid-template-rows: 3fr 1fr; }}
.diagram.v-31 span + span {{ border-top-width: 1px; }}
.diagram.v-13 {{ grid-template-rows: 1fr 3fr; }}
.diagram.v-13 span + span {{ border-top-width: 1px; }}
.diagram.h-31 {{ grid-template-columns: 3fr 1fr; }}
.diagram.h-31 span + span {{ border-left-width: 1px; }}
.diagram.h-13 {{ grid-template-columns: 1fr 3fr; }}
.diagram.h-13 span + span {{ border-left-width: 1px; }}
.diagram.v-211 {{ grid-template-rows: 2fr 1fr 1fr; }}
.diagram.v-211 span + span {{ border-top-width: 1px; }}
.diagram.v-121 {{ grid-template-rows: 1fr 2fr 1fr; }}
.diagram.v-121 span + span {{ border-top-width: 1px; }}
.diagram.v-112 {{ grid-template-rows: 1fr 1fr 2fr; }}
.diagram.v-112 span + span {{ border-top-width: 1px; }}
.diagram.h-211 {{ grid-template-columns: 2fr 1fr 1fr; }}
.diagram.h-211 span + span {{ border-left-width: 1px; }}
.diagram.h-121 {{ grid-template-columns: 1fr 2fr 1fr; }}
.diagram.h-121 span + span {{ border-left-width: 1px; }}
.diagram.h-112 {{ grid-template-columns: 1fr 1fr 2fr; }}
.diagram.h-112 span + span {{ border-left-width: 1px; }}
.diagram.g-2x2 {{
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
}}
.diagram.g-2x2 span:nth-child(2n) {{ border-left-width: 1px; }}
.diagram.g-2x2 span:nth-child(n+3) {{ border-top-width: 1px; }}
.stage-body {{
  min-height: 280px;
  display: grid;
  place-items: center;
  background:
    linear-gradient(90deg, transparent 49.5%, #ddd8ce 49.5%, #ddd8ce 50.5%, transparent 50.5%),
    linear-gradient(#ddd8ce 49.5%, #ddd8ce 50.5%, transparent 50.5%),
    var(--preview-bg);
  background-size: 48px 48px, 48px 48px, auto;
  border: 1px dashed var(--line);
  border-radius: 8px;
}}
.glyph {{
  font-size: {font_size}px;
  line-height: 1;
  padding: 0.5rem 1rem;
  background: rgba(255,253,248,0.88);
  border-radius: 8px;
  border: 1px solid var(--line);
}}
.meta {{
  margin-top: 0.75rem;
  display: grid;
  gap: 0.35rem;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.78rem;
  color: var(--muted);
  word-break: break-all;
}}
.meta strong {{ color: var(--ink); font-weight: 600; }}
.empty {{ color: var(--muted); font-size: 0.95rem; text-align: center; padding: 1rem; }}
</style>
</head>
<body>
<div class="app">
  <header>
    <h1>CJK segment composer</h1>
    <p class="lead">Enter up to four characters, set orientations, pick H/V, then a half-cell template. Uses <code>edenia cjk h</code> so cross-bucket stacks liga in one run.</p>
  </header>

  <section class="panel">
    <div class="chars" id="chars"></div>
    <div class="toolbar" style="margin-top:0.85rem">
      <div class="axis">
        <div class="field">
          <label for="axis">Template axis</label>
          <select id="axis">
            <option value="v">V — top → bottom</option>
            <option value="h">H — left → right</option>
          </select>
        </div>
      </div>
      <div class="field" style="min-width:12rem">
        <label>Active slots</label>
        <div id="slotHint" style="padding:0.4rem 0;color:var(--muted)">0 characters</div>
      </div>
    </div>
  </section>

  <div class="layout">
    <section class="panel templates">
      <h2>Templates</h2>
      <div class="tpl-groups" id="templates"></div>
    </section>
    <section class="panel stage">
      <h2>Preview</h2>
      <div class="stage-body"><div id="preview" class="empty">Choose characters and a template</div></div>
      <div class="meta" id="meta"></div>
    </section>
  </div>
</div>
<script>
const DATA = {json.dumps(data, ensure_ascii=False)};

/* Ratio templates → segment slots (canonical start→end along axis). */
const TEMPLATES = {{
  v: [
    {{
      group: "Halves",
      items: [
        {{ id: "1:1", kind: "half", face: "h", slots: ["T","B"], diagram: "v-2", labels: ["1","1"], name: "1:1" }},
      ],
    }},
  ],
  h: [
    {{
      group: "Halves",
      items: [
        {{ id: "1:1", kind: "half", face: "h", slots: ["L","R"], diagram: "h-2", labels: ["1","1"], name: "1:1" }},
      ],
    }},
  ],
}};

const state = {{
  chars: DATA.SAMPLE.map((hex, i) => ({{ hex, orient: 0, enabled: i < 2 }})),
  axis: "v",
  templateId: "1:1",
}};

function parseCp(raw) {{
  const s = String(raw || "").trim();
  if (!s) return null;
  if (/^[0-9a-fA-F]{{4,6}}$/.test(s)) return parseInt(s, 16);
  if (/^U\\+[0-9a-fA-F]{{4,6}}$/i.test(s)) return parseInt(s.slice(2), 16);
  if (/^0x[0-9a-fA-F]{{1,6}}$/i.test(s)) return parseInt(s.slice(2), 16);
  const cp = s.codePointAt(0);
  return Number.isFinite(cp) ? cp : null;
}}

function activeChars() {{
  return state.chars
    .map((c, i) => ({{ ...c, i, cp: parseCp(c.hex) }}))
    .filter((c) => c.cp != null);
}}

function faceFamily(face) {{
  return face ? ("edenia cjk " + face) : "edenia cjk";
}}

function vsFor(kind, face, slot) {{
  return DATA.HALF_VS[slot];
}}

function findTemplate(id, axis) {{
  const ax = axis || state.axis;
  for (const group of TEMPLATES[ax] || []) {{
    for (const item of group.items) {{
      if (item.id === id) return item;
    }}
  }}
  return null;
}}

function buildSequence(tpl, chars) {{
  const ov = DATA.OV;
  const parts = [];
  const debug = [];
  for (let i = 0; i < tpl.slots.length; i++) {{
    const ch = chars[i];
    const slot = tpl.slots[i];
    const vs = vsFor(tpl.kind, tpl.face, slot);
    const oVs = DATA.ORIENTs[ch.orient] || 0;
    let chunk = String.fromCodePoint(ch.cp);
    let d = "U+" + ch.cp.toString(16).toUpperCase();
    if (oVs) {{
      chunk += String.fromCodePoint(oVs);
      d += " + " + DATA.ORIENT_LABELS[ch.orient];
    }}
    if (i < tpl.slots.length - 1) {{
      chunk += String.fromCodePoint(ov) + String.fromCodePoint(vs);
      d += " + FE00 + " + slot;
    }} else {{
      chunk += String.fromCodePoint(vs);
      d += " + " + slot;
    }}
    parts.push(chunk);
    debug.push(d);
  }}
  return {{ text: parts.join(""), debug, family: faceFamily(tpl.face) }};
}}

function renderChars() {{
  const root = document.getElementById("chars");
  root.innerHTML = "";
  state.chars.forEach((c, i) => {{
    const card = document.createElement("div");
    card.className = "char-card";
    const cp = parseCp(c.hex);
    const shown = cp != null ? String.fromCodePoint(cp) : "";
    card.innerHTML =
      '<div class="idx">Character ' + (i + 1) + '</div>' +
      '<div class="char-preview" id="cp' + i + '">' + shown + '</div>' +
      '<div class="field"><label>Code</label>' +
      '<input data-i="' + i + '" data-k="hex" value="' + (c.hex || "") + '" spellcheck="false" placeholder="4E00 or 字"/></div>' +
      '<div class="field"><label>Orientation</label>' +
      '<select data-i="' + i + '" data-k="orient"></select></div>';
    const sel = card.querySelector("select");
    DATA.ORIENT_LABELS.forEach((lab, oi) => {{
      const opt = document.createElement("option");
      opt.value = String(oi);
      opt.textContent = lab;
      if (oi === c.orient) opt.selected = true;
      sel.appendChild(opt);
    }});
    root.appendChild(card);
  }});
  root.querySelectorAll("input, select").forEach((el) => {{
    el.addEventListener("input", onCharEdit);
    el.addEventListener("change", onCharEdit);
  }});
}}

function onCharEdit(ev) {{
  const el = ev.target;
  const i = +el.dataset.i;
  const k = el.dataset.k;
  if (k === "hex") {{
    state.chars[i].hex = el.value.trim();
    const cp = parseCp(el.value);
    const box = document.getElementById("cp" + i);
    box.textContent = cp != null ? String.fromCodePoint(cp) : "";
  }} else if (k === "orient") {{
    state.chars[i].orient = +el.value;
  }}
  updateSlotHint();
  renderTemplates();
  renderPreview();
}}

function updateSlotHint() {{
  const n = activeChars().length;
  document.getElementById("slotHint").textContent =
    n + " character" + (n === 1 ? "" : "s") + " · templates need matching slot count";
}}

function renderTemplates() {{
  const root = document.getElementById("templates");
  root.innerHTML = "";
  const n = activeChars().length;
  const groups = TEMPLATES[state.axis] || [];
  let firstFit = null;
  groups.forEach((group) => {{
    const wrap = document.createElement("div");
    wrap.innerHTML = '<div class="tpl-group-label">' + group.group + "</div>";
    const grid = document.createElement("div");
    grid.className = "tpl-grid";
    group.items.forEach((item) => {{
      const need = item.slots.length;
      const ok = n >= need;
      if (ok && !firstFit) firstFit = item.id;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tpl" + (state.templateId === item.id ? " active" : "") + (ok ? "" : " disabled");
      btn.dataset.id = item.id;
      btn.innerHTML =
        '<div class="diagram ' + item.diagram + '">' +
        item.labels.map((lab) => "<span>" + lab + "</span>").join("") +
        '</div><span class="name">' + item.name + "</span>";
      if (ok) {{
        btn.addEventListener("click", () => {{
          state.templateId = item.id;
          renderTemplates();
          renderPreview();
        }});
      }}
      grid.appendChild(btn);
    }});
    wrap.appendChild(grid);
    root.appendChild(wrap);
  }});
  const cur = findTemplate(state.templateId);
  const stillValid = cur && groups.some((g) =>
    g.items.some((it) => it.id === cur.id && it.slots.length <= n));
  if (!stillValid && firstFit) {{
    state.templateId = firstFit;
    root.querySelectorAll(".tpl").forEach((b) =>
      b.classList.toggle("active", b.dataset.id === firstFit));
  }}
}}

function renderPreview() {{
  const box = document.getElementById("preview");
  const meta = document.getElementById("meta");
  const chars = activeChars();
  const tpl = findTemplate(state.templateId);
  if (!tpl || chars.length < tpl.slots.length) {{
    box.className = "empty";
    box.textContent = "Choose characters and a template";
    meta.innerHTML = "";
    return;
  }}
  const used = chars.slice(0, tpl.slots.length);
  const seq = buildSequence(tpl, used);
  box.className = "glyph";
  box.style.fontFamily = seq.family;
  box.textContent = seq.text;
  const buckets = [...new Set(used.map((c) => (c.cp >> 8).toString(16).toUpperCase()))];
  meta.innerHTML =
    "<div><strong>family</strong> " + seq.family +
    " · <strong>axis</strong> " + state.axis.toUpperCase() +
    " · <strong>template</strong> " + tpl.name +
    " · <strong>buckets</strong> " + buckets.join(", ") + "</div>" +
    "<div><strong>sequence</strong> " + seq.debug.join("  ·  ") + "</div>";
}}

document.getElementById("axis").addEventListener("change", (ev) => {{
  state.axis = ev.target.value;
  const groups = TEMPLATES[state.axis];
  const n = activeChars().length;
  let pick = null;
  for (const g of groups) {{
    for (const it of g.items) {{
      if (it.slots.length <= n) {{ pick = it.id; break; }}
    }}
    if (pick) break;
  }}
  state.templateId = pick || groups[0].items[0].id;
  renderTemplates();
  renderPreview();
}});

renderChars();
updateSlotHint();
renderTemplates();
renderPreview();
</script>
</body>
</html>
"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {path}")


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", default=DEFAULT_OUT)
    ap.add_argument("--font-dir", default=CJK_OUT)
    ap.add_argument("--font-size", type=int, default=96)
    ap.add_argument(
        "--limit", type=int, default=4096, help="CJK sample pool for default seeds"
    )
    ap.add_argument(
        "--range",
        action="append",
        default=[],
        help="Unicode range like 4E00-9FFF (repeatable)",
    )
    ap.add_argument(
        "--bucket",
        action="append",
        default=[],
        type=lambda s: int(s, 16),
        help="Bucket id as hex (e.g. 65); seeds defaults from that page",
    )
    args = ap.parse_args(argv)

    ranges: List[Tuple[int, int]] = []
    for spec in args.range:
        ranges.extend(parse_range_spec(spec))
    for bid in args.bucket:
        lo = bid << 8
        ranges.append((lo, lo + 0xFF))
    if not ranges:
        ranges = [(0x4E00, 0x9FFF)]

    write_html(
        args.output,
        ranges=ranges,
        limit=args.limit,
        font_size=args.font_size,
        font_dir=args.font_dir,
    )


if __name__ == "__main__":
    main()
