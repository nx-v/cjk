/* global DATA */
const TEMPLATES = {
  v: [
    {
      group: "Halves",
      items: [
        { id: "1:1", kind: "half", face: "h", slots: ["T", "B"], diagram: "v-2", labels: ["1", "1"], name: "1:1" },
      ],
    },
    {
      group: "Thirds",
      items: [
        { id: "2:1", kind: "third", face: "t", slots: ["t3tm", "t3b"], diagram: "v-2u", labels: ["2", "1"], name: "2:1" },
        { id: "1:2", kind: "third", face: "t", slots: ["t3t", "t3mb"], diagram: "v-2d", labels: ["1", "2"], name: "1:2" },
        { id: "1:1:1", kind: "third", face: "t", slots: ["t3t", "t3m", "t3b"], diagram: "v-3", labels: ["1", "1", "1"], name: "1:1:1" },
      ],
    },
    {
      group: "Quarters",
      items: [
        { id: "3:1", kind: "quarter", face: "qv", slots: ["q4t3", "q4b"], diagram: "v-31", labels: ["3", "1"], name: "3:1" },
        { id: "1:3", kind: "quarter", face: "qv", slots: ["q4t", "q4b3"], diagram: "v-13", labels: ["1", "3"], name: "1:3" },
        { id: "2:1:1", kind: "quarter", face: "qv", slots: ["q4th", "q4nb", "q4b"], diagram: "v-211", labels: ["2", "1", "1"], name: "2:1:1" },
        { id: "1:2:1", kind: "quarter", face: "qv", slots: ["q4t", "q4mh", "q4b"], diagram: "v-121", labels: ["1", "2", "1"], name: "1:2:1" },
        { id: "1:1:2", kind: "quarter", face: "qv", slots: ["q4t", "q4nt", "q4bh"], diagram: "v-112", labels: ["1", "1", "2"], name: "1:1:2" },
        { id: "1:1:1:1", kind: "quarter", face: "qv", slots: ["q4t", "q4nt", "q4nb", "q4b"], diagram: "v-4", labels: ["1", "1", "1", "1"], name: "1:1:1:1" },
      ],
    },
  ],
  h: [
    {
      group: "Halves",
      items: [
        { id: "1:1", kind: "half", face: "h", slots: ["L", "R"], diagram: "h-2", labels: ["1", "1"], name: "1:1" },
      ],
    },
    {
      group: "Thirds",
      items: [
        { id: "2:1", kind: "third", face: "t", slots: ["t3lc", "t3r"], diagram: "h-2l", labels: ["2", "1"], name: "2:1" },
        { id: "1:2", kind: "third", face: "t", slots: ["t3l", "t3cr"], diagram: "h-2r", labels: ["1", "2"], name: "1:2" },
        { id: "1:1:1", kind: "third", face: "t", slots: ["t3l", "t3c", "t3r"], diagram: "h-3", labels: ["1", "1", "1"], name: "1:1:1" },
      ],
    },
    {
      group: "Quarters",
      items: [
        { id: "3:1", kind: "quarter", face: "qh", slots: ["q4t3", "q4b"], diagram: "h-31", labels: ["3", "1"], name: "3:1" },
        { id: "1:3", kind: "quarter", face: "qh", slots: ["q4t", "q4b3"], diagram: "h-13", labels: ["1", "3"], name: "1:3" },
        { id: "2:1:1", kind: "quarter", face: "qh", slots: ["q4th", "q4nb", "q4b"], diagram: "h-211", labels: ["2", "1", "1"], name: "2:1:1" },
        { id: "1:2:1", kind: "quarter", face: "qh", slots: ["q4t", "q4mh", "q4b"], diagram: "h-121", labels: ["1", "2", "1"], name: "1:2:1" },
        { id: "1:1:2", kind: "quarter", face: "qh", slots: ["q4t", "q4nt", "q4bh"], diagram: "h-112", labels: ["1", "1", "2"], name: "1:1:2" },
        { id: "1:1:1:1", kind: "quarter", face: "qh", slots: ["q4t", "q4nt", "q4nb", "q4b"], diagram: "h-4", labels: ["1", "1", "1", "1"], name: "1:1:1:1" },
      ],
    },
  ],
  g: [
    {
      group: "2×2",
      items: [
        { id: "2x2", kind: "grid", face: "q", slots: ["q2tl", "q2tr", "q2bl", "q2br"], diagram: "g-2x2", labels: ["1", "1", "1", "1"], name: "2×2" },
      ],
    },
    {
      group: "L + corner",
      items: [
        { id: "Ltl", kind: "grid", face: "q", slots: ["q2tl3", "q2br"], diagram: "g-2x2", labels: ["3", "3", "3", "1"], name: "L⌜ + br" },
        { id: "Ltr", kind: "grid", face: "q", slots: ["q2tr3", "q2bl"], diagram: "g-2x2", labels: ["3", "3", "1", "3"], name: "L⌝ + bl" },
        { id: "Lbl", kind: "grid", face: "q", slots: ["q2bl3", "q2tr"], diagram: "g-2x2", labels: ["3", "1", "3", "3"], name: "L⌞ + tr" },
        { id: "Lbr", kind: "grid", face: "q", slots: ["q2br3", "q2tl"], diagram: "g-2x2", labels: ["1", "3", "3", "3"], name: "L⌟ + tl" },
      ],
    },
    {
      group: "Adjacent",
      items: [
        { id: "top", kind: "grid", face: "q", slots: ["q2tl", "q2tr"], diagram: "h-2", labels: ["1", "1"], name: "top" },
        { id: "bot", kind: "grid", face: "q", slots: ["q2bl", "q2br"], diagram: "h-2", labels: ["1", "1"], name: "bottom" },
        { id: "left", kind: "grid", face: "q", slots: ["q2tl", "q2bl"], diagram: "v-2", labels: ["1", "1"], name: "left" },
        { id: "right", kind: "grid", face: "q", slots: ["q2tr", "q2br"], diagram: "v-2", labels: ["1", "1"], name: "right" },
      ],
    },
  ],
};

const HANGUL_VS = [null, 0xfe01, 0xfe02, 0xfe03];
const STACK =
  '"edenia hangul", "edenia hanguls", "edenia kana h", "edenia kana", "edenia yi h", "edenia yi"';

const cjkState = {
  chars: [
    { hex: "4E00", orient: 0 },
    { hex: "4E8C", orient: 0 },
    { hex: "", orient: 0 },
    { hex: "", orient: 0 },
  ],
  axis: "v",
  templateId: "1:1",
};

function parseCp(raw) {
  const s = String(raw || "").trim();
  if (!s) return null;
  if (/^[0-9a-fA-F]{4,6}$/.test(s)) return parseInt(s, 16);
  if (/^U\+[0-9a-fA-F]{4,6}$/i.test(s)) return parseInt(s.slice(2), 16);
  if (/^0x[0-9a-fA-F]{1,6}$/i.test(s)) return parseInt(s.slice(2), 16);
  const cp = s.codePointAt(0);
  return Number.isFinite(cp) ? cp : null;
}

function cjkAvailable(face) {
  const a = DATA.AVAILABLE_CJK;
  if (!a || !a.length) return true;
  return a.indexOf(face) >= 0;
}

function cjkFamily(face) {
  return face ? "edenia cjk " + face : "edenia cjk";
}

function editorStack(face) {
  return STACK + ", '" + cjkFamily(face) + "', Georgia, serif";
}

function vsFor(kind, face, slot) {
  if (kind === "half") return DATA.HALF_VS[slot];
  if (kind === "third") return DATA.THIRD_VS[slot];
  if (kind === "grid" || face === "q") return DATA.Q_VS[slot];
  if (face === "qh") return DATA.QH_VS[slot];
  return DATA.QV_VS[slot];
}

function findTemplate(id, axis) {
  const ax = axis || cjkState.axis;
  for (const group of TEMPLATES[ax] || []) {
    for (const item of group.items) {
      if (item.id === id) return item;
    }
  }
  return null;
}

function activeCjk() {
  return cjkState.chars
    .map((c) => ({ ...c, cp: parseCp(c.hex) }))
    .filter((c) => c.cp != null);
}

function buildCjkSequence(tpl, chars) {
  const ov = DATA.OV;
  const parts = [];
  const debug = [];
  for (let i = 0; i < tpl.slots.length; i++) {
    const ch = chars[i];
    const slot = tpl.slots[i];
    const vs = vsFor(tpl.kind, tpl.face, slot);
    const oVs = DATA.ORIENTs[ch.orient] || 0;
    let chunk = String.fromCodePoint(ch.cp);
    let d = "U+" + ch.cp.toString(16).toUpperCase();
    if (oVs) {
      chunk += String.fromCodePoint(oVs);
      d += " + " + DATA.ORIENT_LABELS[ch.orient];
    }
    if (i < tpl.slots.length - 1) {
      chunk += String.fromCodePoint(ov) + String.fromCodePoint(vs);
      d += " + FE00 + " + slot;
    } else {
      chunk += String.fromCodePoint(vs);
      d += " + " + slot;
    }
    parts.push(chunk);
    debug.push(d);
  }
  return { text: parts.join(""), debug, family: cjkFamily(tpl.face), face: tpl.face };
}

function insertText(text, family) {
  const editor = document.getElementById("editor");
  editor.focus();
  const sel = window.getSelection();
  if (!sel.rangeCount || !editor.contains(sel.anchorNode)) {
    const range = document.createRange();
    range.selectNodeContents(editor);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  }
  const span = document.createElement("span");
  span.style.fontFamily = family ? "'" + family + "', " + STACK : editor.style.fontFamily;
  span.textContent = text;
  const range = sel.getRangeAt(0);
  range.deleteContents();
  range.insertNode(span);
  const after = document.createTextNode("\u200b");
  span.after(after);
  range.setStartAfter(after);
  range.collapse(true);
  sel.removeAllRanges();
  sel.addRange(range);
  updateStatus();
}

function editorPlain() {
  return document.getElementById("editor").innerText.replace(/\u200b/g, "");
}

function toHex(text) {
  return Array.from(text)
    .map((ch) => {
      const cp = ch.codePointAt(0);
      return "U+" + cp.toString(16).toUpperCase().padStart(4, "0");
    })
    .join(" ");
}

function fillSelect(sel, items, labelFn, valueFn) {
  sel.innerHTML = "";
  items.forEach((it, i) => {
    const opt = document.createElement("option");
    opt.value = valueFn ? valueFn(it, i) : String(i);
    opt.textContent = labelFn(it, i);
    sel.appendChild(opt);
  });
}

function renderCjkChars() {
  const root = document.getElementById("cjkChars");
  root.innerHTML = "";
  cjkState.chars.forEach((c, i) => {
    const card = document.createElement("div");
    card.className = "char-card";
    const cp = parseCp(c.hex);
    card.innerHTML =
      '<div class="idx">Character ' +
      (i + 1) +
      '</div><div class="char-preview" id="cjkp' +
      i +
      '">' +
      (cp != null ? String.fromCodePoint(cp) : "") +
      '</div><input data-i="' +
      i +
      '" data-k="hex" value="' +
      (c.hex || "") +
      '" spellcheck="false" placeholder="4E00 or 字"/><select data-i="' +
      i +
      '" data-k="orient"></select>';
    const sel = card.querySelector("select");
    DATA.ORIENT_LABELS.forEach((lab, oi) => {
      const opt = document.createElement("option");
      opt.value = String(oi);
      opt.textContent = lab;
      if (oi === c.orient) opt.selected = true;
      sel.appendChild(opt);
    });
    root.appendChild(card);
  });
  root.querySelectorAll("input, select").forEach((el) => {
    el.addEventListener("input", onCjkEdit);
    el.addEventListener("change", onCjkEdit);
  });
}

function onCjkEdit(ev) {
  const el = ev.target;
  const i = +el.dataset.i;
  if (el.dataset.k === "hex") {
    cjkState.chars[i].hex = el.value.trim();
    const cp = parseCp(el.value);
    document.getElementById("cjkp" + i).textContent =
      cp != null ? String.fromCodePoint(cp) : "";
  } else {
    cjkState.chars[i].orient = +el.value;
  }
  renderCjkTemplates();
  renderCjkPreview();
}

function renderCjkTemplates() {
  const root = document.getElementById("templates");
  root.innerHTML = "";
  const n = activeCjk().length;
  const groups = TEMPLATES[cjkState.axis] || [];
  let firstFit = null;
  groups.forEach((group) => {
    const wrap = document.createElement("div");
    wrap.innerHTML = '<div class="tpl-group-label">' + group.group + "</div>";
    const grid = document.createElement("div");
    grid.className = "tpl-grid";
    group.items.forEach((item) => {
      const ok = n >= item.slots.length && cjkAvailable(item.face);
      if (ok && !firstFit) firstFit = item.id;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "tpl" +
        (cjkState.templateId === item.id ? " active" : "") +
        (ok ? "" : " disabled");
      btn.dataset.id = item.id;
      btn.innerHTML =
        '<div class="diagram ' +
        item.diagram +
        '">' +
        item.labels.map((lab) => "<span>" + lab + "</span>").join("") +
        '</div><span class="name">' +
        item.name +
        "</span>";
      if (ok) {
        btn.addEventListener("click", () => {
          cjkState.templateId = item.id;
          renderCjkTemplates();
          renderCjkPreview();
        });
      }
      grid.appendChild(btn);
    });
    wrap.appendChild(grid);
    root.appendChild(wrap);
  });
  const cur = findTemplate(cjkState.templateId);
  const stillValid =
    cur &&
    groups.some((g) =>
      g.items.some((it) => it.id === cur.id && it.slots.length <= n)
    );
  if (!stillValid && firstFit) {
    cjkState.templateId = firstFit;
    root.querySelectorAll(".tpl").forEach((b) =>
      b.classList.toggle("active", b.dataset.id === firstFit)
    );
  }
}

function currentCjkSeq() {
  const chars = activeCjk();
  const tpl = findTemplate(cjkState.templateId);
  if (!tpl || chars.length < tpl.slots.length) return null;
  return buildCjkSequence(tpl, chars.slice(0, tpl.slots.length));
}

function renderCjkPreview() {
  const box = document.getElementById("cjkPreview");
  const meta = document.getElementById("cjkMeta");
  const seq = currentCjkSeq();
  if (!seq) {
    box.textContent = "";
    meta.textContent = "Need characters matching the template slot count.";
    return;
  }
  box.style.fontFamily = "'" + seq.family + "'";
  box.textContent = seq.text;
  meta.textContent = seq.family + " · " + seq.debug.join("  ·  ");
}

function hangSeq() {
  const L = DATA.HANGUL.L[+document.getElementById("hangL").value];
  const V = DATA.HANGUL.V[+document.getElementById("hangV").value];
  if (!L || !V) return "";
  const cps = [L.cp];
  const lv = HANGUL_VS[+document.getElementById("hangLv").value];
  if (lv) cps.push(lv);
  cps.push(V.cp);
  const vv = HANGUL_VS[+document.getElementById("hangVv").value];
  if (vv) cps.push(vv);
  if (document.getElementById("hangWantT").checked) {
    const T = DATA.HANGUL.T[+document.getElementById("hangT").value];
    if (T) {
      cps.push(T.cp);
      const tv = HANGUL_VS[+document.getElementById("hangTv").value];
      if (tv) cps.push(tv);
      if (document.getElementById("hangSwap").checked) cps.push(DATA.HANGUL.SWAP);
    }
  }
  return String.fromCodePoint(...cps);
}

function renderHang() {
  document.getElementById("hangPreview").textContent = hangSeq();
}

function yiChunk(id, oid) {
  const cp = parseCp(document.getElementById(id).value);
  if (cp == null) return { text: "", debug: "" };
  const oi = +document.getElementById(oid).value;
  const vs = DATA.ORIENTs[oi] || 0;
  let text = String.fromCodePoint(cp);
  let debug = "U+" + cp.toString(16).toUpperCase();
  if (vs) {
    text += String.fromCodePoint(vs);
    debug += " + " + DATA.ORIENT_LABELS[oi];
  }
  return { text, debug };
}

function yiSeq() {
  const a = yiChunk("yiA", "yiAo");
  const slice = document.getElementById("yiSlice").value;
  if (!slice) return a;
  const b = yiChunk("yiB", "yiBo");
  if (!b.text) return a;
  const pair = DATA.YI.SLICE[slice];
  const ov = DATA.YI.OV || DATA.OV;
  return {
    text: a.text + String.fromCodePoint(pair[0]) + String.fromCodePoint(ov) + b.text + String.fromCodePoint(pair[1]),
    debug: a.debug + " · FE" + (pair[0] - 0xfe00).toString(16).toUpperCase().padStart(2, "0") + " FE00 · " + b.debug + " + FE" + (pair[1] - 0xfe00).toString(16).toUpperCase().padStart(2, "0"),
  };
}

function renderYi() {
  const s = yiSeq();
  document.getElementById("yiPreview").textContent = s.text;
}

function kanaSeq() {
  const a = parseCp(document.getElementById("kanaA").value);
  if (a == null) return "";
  let text = String.fromCodePoint(a);
  const slice = document.getElementById("kanaSlice").value;
  if (slice) {
    const pair = DATA.KANA.SLICE[slice];
    const ov = DATA.KANA.OV || DATA.OV;
    text += String.fromCodePoint(pair[0]) + String.fromCodePoint(ov);
    const b = parseCp(document.getElementById("kanaB").value);
    if (b != null) {
      text += String.fromCodePoint(b) + String.fromCodePoint(pair[1]);
    }
  }
  return text;
}

function renderKana() {
  document.getElementById("kanaPreview").textContent = kanaSeq();
}

function applyFace() {
  const face = document.getElementById("face").value;
  const editor = document.getElementById("editor");
  editor.style.fontFamily = editorStack(face);
  document.documentElement.style.setProperty("--cjk", "'" + cjkFamily(face) + "'");
}

function updateStatus() {
  const t = editorPlain();
  document.getElementById("statusLeft").textContent =
    t.length + " char" + (t.length === 1 ? "" : "s");
  const sel = window.getSelection();
  const snippet = sel && sel.toString() ? sel.toString() : t.slice(-24);
  document.getElementById("statusRight").textContent = snippet
    ? toHex(snippet).slice(0, 80)
    : "";
}

function bootUi() {
  if (DATA.missing && DATA.missing.length) {
    const el = document.getElementById("bootWarn");
    el.hidden = false;
    el.textContent =
      "Missing font CSS: " +
      DATA.missing.join(", ") +
      ". Run the build scripts (or copy dist/*.css + woff2) then refresh.";
  }

  const faceSel = document.getElementById("face");
  [...faceSel.options].forEach((opt) => {
    opt.hidden = !cjkAvailable(opt.value);
    opt.disabled = opt.hidden;
  });
  if (!cjkAvailable(faceSel.value)) {
    const first = [...faceSel.options].find((o) => !o.disabled);
    if (first) faceSel.value = first.value;
  }

  renderCjkChars();
  renderCjkTemplates();
  renderCjkPreview();

  fillSelect(
    document.getElementById("hangL"),
    DATA.HANGUL.L,
    (it) => it.ch + " " + it.s
  );
  fillSelect(
    document.getElementById("hangV"),
    DATA.HANGUL.V,
    (it) => it.ch + " " + it.s
  );
  fillSelect(
    document.getElementById("hangT"),
    DATA.HANGUL.T,
    (it) => it.ch + " " + it.s
  );
  renderHang();

  const yiAo = document.getElementById("yiAo");
  const yiBo = document.getElementById("yiBo");
  DATA.ORIENT_LABELS.forEach((lab, i) => {
    [yiAo, yiBo].forEach((sel) => {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = lab;
      sel.appendChild(opt);
    });
  });
  document.getElementById("yiA").value = "A1B8";

  const slotSel = document.getElementById("cjkMarkSlot");
  if (slotSel && DATA.CJK_MARK_SLOTS) {
    DATA.CJK_MARK_SLOTS.forEach((s, i) => {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = s.label;
      slotSel.appendChild(opt);
    });
  }
  const mk = document.getElementById("cjkMarks");
  DATA.CJK_MARKS.forEach((m) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = m.ch + " " + m.label;
    b.addEventListener("click", () => {
      document.getElementById("face").value = "";
      applyFace();
      const slot = DATA.CJK_MARK_SLOTS && DATA.CJK_MARK_SLOTS[+slotSel.value];
      let text = "";
      if (slot && !(slot.pos === "right" && slot.mirror === "id")) {
        text += String.fromCodePoint(slot.cp);
      }
      text += String.fromCodePoint(m.cp);
      insertText(text, cjkFamily(""));
    });
    mk.appendChild(b);
  });
  fillSelect(
    document.getElementById("combining"),
    DATA.COMBINING,
    (it) => it.ch + " U+" + it.cp.toString(16).toUpperCase() + " " + (it.s || "")
  );

  applyFace();
  updateStatus();
}

async function main() {
  window.DATA = await (await fetch("/api/data.json")).json();
  bootUi();

  document.getElementById("tabs").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-tab]");
    if (!btn) return;
    document.querySelectorAll(".tabs button").forEach((b) =>
      b.classList.toggle("active", b === btn)
    );
    document.querySelectorAll(".pane").forEach((p) =>
      p.classList.toggle("active", p.id === "pane-" + btn.dataset.tab)
    );
  });
  document.getElementById("axis").addEventListener("change", (ev) => {
    cjkState.axis = ev.target.value;
    renderCjkTemplates();
    renderCjkPreview();
  });
  document.getElementById("btnInsertCjk").addEventListener("click", () => {
    const seq = currentCjkSeq();
    if (!seq) return;
    document.getElementById("face").value = seq.face;
    applyFace();
    insertText(seq.text, seq.family);
  });
  ["hangL", "hangLv", "hangV", "hangVv", "hangT", "hangTv", "hangWantT", "hangSwap"].forEach(
    (id) => document.getElementById(id).addEventListener("input", renderHang)
  );
  document.getElementById("btnInsertHang").addEventListener("click", () => {
    insertText(hangSeq(), "edenia hangul");
  });
  ["yiA", "yiAo", "yiB", "yiBo", "yiSlice"].forEach((id) =>
    document.getElementById(id).addEventListener("input", renderYi)
  );
  document.getElementById("btnInsertYi").addEventListener("click", () => {
    insertText(yiSeq().text, (DATA.FAMILIES && DATA.FAMILIES.yi_h) || "edenia yi h");
  });
  ["kanaA", "kanaB", "kanaSlice"].forEach((id) =>
    document.getElementById(id).addEventListener("input", renderKana)
  );
  document.getElementById("btnInsertKana").addEventListener("click", () => {
    insertText(kanaSeq(), (DATA.FAMILIES && DATA.FAMILIES.kana_h) || "edenia kana h");
  });
  document.getElementById("btnCgj").addEventListener("click", () => {
    insertText("\u034f");
  });
  document.getElementById("btnMark").addEventListener("click", () => {
    const m = DATA.COMBINING[+document.getElementById("combining").value];
    if (m) insertText(String.fromCodePoint(m.cp));
  });
  document.getElementById("face").addEventListener("change", applyFace);
  document.getElementById("size").addEventListener("input", (ev) => {
    document.documentElement.style.setProperty("--editor-size", ev.target.value + "px");
  });
  document.getElementById("btnCopy").addEventListener("click", async () => {
    await navigator.clipboard.writeText(editorPlain());
    document.getElementById("statusLeft").textContent = "copied text";
  });
  document.getElementById("btnHex").addEventListener("click", async () => {
    await navigator.clipboard.writeText(toHex(editorPlain()));
    document.getElementById("statusLeft").textContent = "copied hex";
  });
  document.getElementById("btnClear").addEventListener("click", () => {
    document.getElementById("editor").innerHTML = "";
    updateStatus();
  });
  document.getElementById("editor").addEventListener("keyup", updateStatus);
  document.getElementById("editor").addEventListener("mouseup", updateStatus);
}

main().catch((err) => {
  const el = document.getElementById("bootWarn");
  el.hidden = false;
  el.textContent = "Failed to load /api/data.json: " + err;
});
