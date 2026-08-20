"use strict";

/**
 * Edenia font test Markdown generator.
 *
 * Covers Hangul / Kana / Yi / CJK (Han + Tangut + Khitan) with:
 *   - random prose (headings, lists, quotes)
 *   - systematic compound / ligature / diacritic catalogs
 *
 * Encoding (matches Scripts/*_html.py / build_*):
 *   Hangul jamo: L/V/T × FE00–FE03; T + FE04 batchim swap; bangjeom;
 *                combining marks (shared_diacritics inventory) + CGJ
 *   Yi:          base × FE01–FE07; digraph A FE08 FE00 B FE09 (etc.);
 *                same combining-mark + CGJ slot cycle
 *   Kana:        BMP PUA U+E000… (full/small D4) + halfwidth U+ED00…;
 *                slices FE00 overlay + FE08–FE0F; combining marks + CGJ slot skip
 *                (not Unicode Hiragana/Katakana/Kana Extended blocks)
 *   CJK (+Tangut/Khitan): D4 FE01–FE07 on h; base ca/nhay
 *                         CJK FE00–FE0F MARK (4 pos × id/mx/my/mxy);
 *                         digraph A FE08 FE00 B FE09 (etc.) on h;
 *                         ca/nhay U+16FF0/16FF1 only (no combining marks)
 *
 * Combining marks = ``\p{M}`` baked into Hangul/Yi/Kana (see
 * ``shared_diacritics.iter_dakuten_codepoints``), excluding variation
 * selectors; CGJ (U+034F) is a slot skip, not a random pick.
 *
 * Usage:
 *   node Scripts/generate_edenian_md.js
 *   node Scripts/generate_edenian_md.js --lines 64 --out Scripts/dist/Edenian-test.md
 */

const fs = require("fs");
const path = require("path");
const {spawnSync} = require("child_process");

const {ceil, floor, random, min} = Math;
const {keys, entries, fromEntries} = Object;

const SCRIPT_DIR = __dirname;
const REPO_ROOT = path.resolve(SCRIPT_DIR, "..");

const ARGS = (() => {
  const a = process.argv.slice(2);
  const get = (flag, dflt) => {
    const i = a.indexOf(flag);
    return i >= 0 && a[i + 1] != null ? a[i + 1] : dflt;
  };
  return {
    lines: Number(get("--lines", "512")) || 512,
    out: get("--out", path.join(SCRIPT_DIR, "dist", "Edenian-test.md")),
    catalogOnly: a.includes("--catalog-only"),
    proseOnly: a.includes("--prose-only"),
  };
})();

/** Fallback if no built font / Python inventory is available. */
const FALLBACK_COMBINING_MARKS = [
  ..."\u3099\u309a\uff9e\uff9f\u0308\u0301\u0300\u0302\u0304\u0306",
];

/**
 * Visible combining marks present in Edenia Hangul/Yi/Kana fonts
 * (``shared_diacritics`` inventory: ``\p{M}`` minus variation selectors,
 * minus CGJ). Prefer a built ``.woff2`` cmap so the list matches what
 * Obsidian actually ships.
 */
function loadCombiningMarks() {
  const fontCandidates = [
    path.join(SCRIPT_DIR, "dist", "hangul", "edenia-hangul.woff2"),
    path.join(SCRIPT_DIR, "dist", "yi", "edenia-yi.woff2"),
    path.join(SCRIPT_DIR, "dist", "kana", "edenia-kana.woff2"),
    path.join(
      SCRIPT_DIR,
      "obsidian-edenia",
      "edenia",
      "hangul",
      "edenia-hangul.woff2",
    ),
  ];
  const fontPath = fontCandidates.find(p => fs.existsSync(p));
  if (!fontPath) {
    console.warn(
      "[generate_edenian_md] no Edenia font for mark inventory; using fallback",
    );
    return FALLBACK_COMBINING_MARKS;
  }
  const py = `
import json, sys
from fontTools.ttLib import TTFont
from shared_diacritics import iter_dakuten_codepoints, visible_dakuten_cps
tt = TTFont(sys.argv[1])
cmap = {}
for table in tt["cmap"].tables:
    if table.isUnicode():
        cmap.update(table.cmap)
tt.close()
print(json.dumps(visible_dakuten_cps(iter_dakuten_codepoints(cmap))))
`.trim();
  const r = spawnSync("python", ["-c", py, fontPath], {
    encoding: "utf8",
    cwd: SCRIPT_DIR,
    env: {...process.env, PYTHONIOENCODING: "utf-8"},
  });
  if (r.status !== 0) {
    console.warn(
      "[generate_edenian_md] mark inventory failed:",
      (r.stderr || r.stdout || "").trim() || `exit ${r.status}`,
    );
    return FALLBACK_COMBINING_MARKS;
  }
  try {
    const cps = JSON.parse(r.stdout.trim());
    if (!Array.isArray(cps) || cps.length === 0) {
      console.warn(
        "[generate_edenian_md] empty mark inventory; using fallback",
      );
      return FALLBACK_COMBINING_MARKS;
    }
    console.log(
      `[generate_edenian_md] ${cps.length} combining marks from ${path.relative(REPO_ROOT, fontPath)}`,
    );
    return cps.map(cp => String.fromCodePoint(cp));
  } catch (err) {
    console.warn("[generate_edenian_md] bad mark JSON:", err.message);
    return FALLBACK_COMBINING_MARKS;
  }
}

const COMBINING_MARKS = loadCombiningMarks();

const pipe = (x, ...fns) => fns.reduce((v, f) => f(v), x);
const randomItem = arr => arr[floor(random() * arr.length)];
const randomInt = (minV, maxV) => floor(minV + random() * (maxV - minV));
const randomIntInclusive = (minV, maxV) =>
  floor(minV + random() * (maxV - minV + 1));
const reverseString = string => [...string].toReversed().join``;
const fromCodePoint = x => String.fromCodePoint(x);
const vs = n => fromCodePoint(0xfe00 + n);

function* inclusiveRange(start, stop, step = 1) {
  if (stop == void 0) [start, stop] = [0, start];
  if (step > 0) while (start <= stop) (yield start, (start += step));
  else if (step < 0) while (start >= stop) (yield start, (start += step));
  else throw new RangeError("range() step argument invalid");
}

function sampleRange(start, end, n) {
  const out = [];
  const span = end - start + 1;
  for (let i = 0; i < n; i++) out.push(start + floor(random() * span));
  return out.map(fromCodePoint);
}

function getWeightedCategory(
  categories,
  usePrevious = false,
  previousCategory = "",
) {
  let currentCategory = "";
  let totalWeight = 0;
  for (const category in categories) totalWeight += categories[category];
  let randomValue = random() * totalWeight;
  for (const category in categories) {
    randomValue -= categories[category];
    if (randomValue <= 0) {
      currentCategory = category;
      break;
    }
  }
  if (usePrevious && previousCategory == currentCategory) {
    const newCategories = fromEntries(
      entries(categories).filter(([k]) => k != currentCategory),
    );
    if (keys(newCategories).length == 0) return currentCategory;
    return getWeightedCategory(newCategories, usePrevious, previousCategory);
  }
  return currentCategory;
}

const CHARACTERS = {
  // Edenia kana lives in BMP PUA / SPUA — not U+3040…/U+30A0… blocks.
  //   i = L*8 + o;  full=E000+2i; small=E000+2i+1; hw=ED00+2i / +1
  // Chart: 17×6 hiragana + length/gemination, then 17×6 katakana + marks.
  yi: [...inclusiveRange(0xa000, 0xa48c)].map(fromCodePoint),
  tangut: [
    ...inclusiveRange(0x17000, 0x187f7),
    ...inclusiveRange(0x18d00, 0x18d1e),
  ].map(fromCodePoint),
  khitan: [...inclusiveRange(0x18b00, 0x18cff)].map(fromCodePoint),

  cjkUnits: [
    ...inclusiveRange(0x32cc, 0x32cf),
    ...inclusiveRange(0x3371, 0x337a),
    ...inclusiveRange(0x3380, 0x33df),
    0x33ff,
  ].map(fromCodePoint),

  midParagraphPunctuation: [
    ..."\u3001\u3002\uff0c\uff01\uff1f\uff1a\uff1b\uff0f",
  ],
  endParagraphPunctuation: [..."\u3002\uff01\uff1f"],
  digits: [..."0123456789\u218a\u218b"],
  alphabet: [..."ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"],

  feD4: [...inclusiveRange(0xfe01, 0xfe07)].map(fromCodePoint),
  feHangulMirror: [...inclusiveRange(0xfe00, 0xfe03)].map(fromCodePoint),
  fe04: fromCodePoint(0xfe04),
  feOv: fromCodePoint(0xfe00),
  feSquish: {
    T: fromCodePoint(0xfe08),
    B: fromCodePoint(0xfe09),
    L: fromCodePoint(0xfe0a),
    R: fromCodePoint(0xfe0b),
    TL: fromCodePoint(0xfe0c),
    BR: fromCodePoint(0xfe0d),
    TR: fromCodePoint(0xfe0e),
    BL: fromCodePoint(0xfe0f),
  },
  ca: fromCodePoint(0x16ff0),
  nhay: fromCodePoint(0x16ff1),
  bangjeom: [..."\u302e\u302f"],
  cgj: fromCodePoint(0x034f),
  /** Visible ``\p{M}`` from Edenia fonts (CGJ is separate slot-skip). */
  dakutenMarks: COMBINING_MARKS,
};

const HANGUL_JAMO = {
  choseong:
    "\u11001\u11012\u11021\u11031\u11042\u11051\u11061\u11071\u11082\u11091\u110a2\u110b1\u110c1\u110d2\u110e1\u110f1\u11101\u11111\u11121\u11132\u11142\u11152\u11162\u11172\u11182\u11192\u111a2\u111b1\u111c2\u111d1\u111e2\u111f2\u11202\u11212\u11223\u11233\u11243\u11253\u11263\u11272\u11282\u11292\u112a2\u112b2\u112c2\u112d2\u112e2\u112f2\u11302\u11312\u11322\u11333\u11343\u11352\u11362\u11372\u11382\u11392\u113a2\u113b2\u113c1\u113d2\u113e1\u113f2\u11401\u11412\u11422\u11432\u11442\u11452\u11462\u11472\u11482\u11492\u114a2\u114b2\u114c1\u114d2\u114e1\u114f2\u11501\u11512\u11522\u11532\u11541\u11551\u11562\u11571\u11582\u11591\u115a2\u115b2\u115c2\u115d2\u115e2\ua9602\ua9612\ua9622\ua9632\ua9642\ua9653\ua9662\ua9673\ua9682\ua9692\ua96a3\ua96b2\ua96c2\ua96d2\ua96e2\ua96f2\ua9702\ua9712\ua9723\ua9732\ua9742\ua9753\ua9762\ua9772\ua9783\ua9792\ua97a2\ua97b2\ua97c2",
  jungseong:
    "\u11611\u11622\u11631\u11642\u11651\u11662\u11671\u11682\u11691\u116a2\u116b3\u116c2\u116d1\u116e1\u116f2\u11703\u11712\u11721\u11731\u11742\u11751\u11762\u11772\u11782\u11792\u117a2\u117b2\u117c2\u117d2\u117e2\u117f2\u11803\u11813\u11822\u11832\u11842\u11852\u11862\u11872\u11882\u11892\u118a2\u118b3\u118c3\u118d2\u118e2\u118f2\u11903\u11912\u11923\u11932\u11942\u11952\u11962\u11973\u11982\u11992\u119a2\u119b2\u119c2\u119d2\u119e1\u119f2\u11a02\u11a12\u11a22\u11a32\u11a42\u11a52\u11a62\u11a73\ud7b02\ud7b13\ud7b22\ud7b33\ud7b42\ud7b52\ud7b63\ud7b73\ud7b82\ud7b92\ud7ba2\ud7bb3\ud7bc2\ud7bd3\ud7be3\ud7bf2\ud7c03\ud7c12\ud7c22\ud7c32\ud7c42\ud7c52\ud7c63",
  jongseong:
    "\u11a81\u11a92\u11aa2\u11ab1\u11ac2\u11ad2\u11ae1\u11af1\u11b02\u11b12\u11b22\u11b32\u11b42\u11b52\u11b62\u11b71\u11b81\u11b92\u11ba1\u11bb2\u11bc1\u11bd1\u11be1\u11bf1\u11c01\u11c11\u11c21\u11c32\u11c43\u11c52\u11c62\u11c72\u11c82\u11c92\u11ca2\u11cb2\u11cc3\u11cd2\u11ce2\u11cf3\u11d02\u11d13\u11d23\u11d33\u11d43\u11d52\u11d63\u11d72\u11d82\u11d92\u11da2\u11db2\u11dc2\u11dd2\u11de3\u11df2\u11e02\u11e12\u11e21\u11e32\u11e42\u11e52\u11e61\u11e72\u11e82\u11e92\u11ea2\u11eb1\u11ec2\u11ed3\u11ee2\u11ef2\u11f01\u11f12\u11f22\u11f32\u11f41\u11f52\u11f62\u11f72\u11f82\u11f91\u11fa2\u11fb2\u11fc2\u11fd2\u11fe2\u11ff2\ud7cb2\ud7cc2\ud7cd2\ud7ce3\ud7cf2\ud7d02\ud7d13\ud7d22\ud7d32\ud7d42\ud7d53\ud7d63\ud7d73\ud7d83\ud7d93\ud7da3\ud7db2\ud7dc3\ud7dd1\ud7de2\ud7df3\ud7e02\ud7e13\ud7e22\ud7e32\ud7e43\ud7e52\ud7e62\ud7e73\ud7e82\ud7e92\ud7ea2\ud7eb2\ud7ec3\ud7ed2\ud7ee2\ud7ef2\ud7f02\ud7f12\ud7f22\ud7f32\ud7f42\ud7f52\ud7f62\ud7f72\ud7f83\ud7f92\ud7fa2\ud7fb2",
};

const BRACKETS = [
  {open: "\uff08", close: "\uff09"},
  {open: "\uff5f", close: "\uff60"},
  {open: "\uff3b", close: "\uff3d"},
  {open: "\uff5b", close: "\uff5d"},
  {open: "\u300c", close: "\u300d"},
  {open: "\u300e", close: "\u300f"},
  {open: "\u3016", close: "\u3017"},
  {open: "\u3014", close: "\u3015"},
  {open: "\u3018", close: "\u3019"},
];

const DIGRAPH_NICHE_PAIRS = [
  ["T", "B"],
  ["B", "T"],
  ["L", "R"],
  ["R", "L"],
  ["TL", "BR"],
  ["BR", "TL"],
  ["TR", "BL"],
  ["BL", "TR"],
];

// ---------- Edenia kana PUA chart (build_kana.py) ----------
const KANA_PUA_START = 0xe000;
const KANA_HW_PUA_START = 0xed00;
const KANA_D4_COUNT = 8;
const KANA_ROWS = 17;
const KANA_COLS = 6;
const KANA_PHONETIC = KANA_ROWS * KANA_COLS; // 102
const KANA_TRAILING = 2; // length + gemination
const HIRAGANA_COUNT = KANA_PHONETIC + KANA_TRAILING; // 104
const KATAKANA_COUNT = KANA_PHONETIC + KANA_TRAILING; // 104
const KANA_LOGICAL_TOTAL = HIRAGANA_COUNT + KATAKANA_COUNT; // 208

function kanaPairIndex(logical, orient) {
  return logical * KANA_D4_COUNT + (orient & 7);
}
function kanaFullCp(i) {
  return KANA_PUA_START + 2 * i;
}
function kanaSmallCp(i) {
  return KANA_PUA_START + 2 * i + 1;
}
function kanaHwFullCp(i) {
  return KANA_HW_PUA_START + 2 * i;
}
function kanaHwSmallCp(i) {
  return KANA_HW_PUA_START + 2 * i + 1;
}

/** Random logical index in hiragana or katakana chart (phonetic-biased). */
function randomKanaLogical(script /* 'hira' | 'kata' */) {
  const base = script === "kata" ? HIRAGANA_COUNT : 0;
  const count = script === "kata" ? KATAKANA_COUNT : HIRAGANA_COUNT;
  // Prefer phonetic cells; occasionally length/gemination trailers.
  if (random() < 0.92) return base + randomInt(0, KANA_PHONETIC);
  return base + KANA_PHONETIC + randomInt(0, KANA_TRAILING);
}

/**
 * One Edenia kana codepoint from PUA / halfwidth SPUA.
 * Orientations are real cmap entries (no VS).
 */
function kanaPuaChar({
  script = random() < 0.45 ? "kata" : "hira",
  small = null,
  halfwidth = null,
  orient = null,
} = {}) {
  const logical = randomKanaLogical(script);
  const o =
    orient == null
      ? random() < 0.55
        ? 0
        : randomInt(0, KANA_D4_COUNT)
      : orient & 7;
  const i = kanaPairIndex(logical, o);
  const useSmall = small == null ? random() < 0.18 : !!small;
  const useHw = halfwidth == null ? random() < 0.12 : !!halfwidth;
  let cpV;
  if (useHw) cpV = useSmall ? kanaHwSmallCp(i) : kanaHwFullCp(i);
  else cpV = useSmall ? kanaSmallCp(i) : kanaFullCp(i);
  return fromCodePoint(cpV);
}

/** Build arrays of identity (o=0) full PUA cps for catalog samples. */
function kanaPuaScriptRange(script) {
  const base = script === "kata" ? HIRAGANA_COUNT : 0;
  const out = [];
  for (let L = 0; L < KANA_PHONETIC; L++) {
    out.push(fromCodePoint(kanaFullCp(kanaPairIndex(base + L, 0))));
  }
  return out;
}

function precomputeCumulativeWeights(array) {
  const cumulativeWeights = [];
  let totalWeight = 0;
  for (const item of array) {
    if (typeof item.weight != "number" || item.weight < 0)
      throw new Error("Weights must be non-negative numbers.");
    totalWeight += item.weight;
    cumulativeWeights.push(totalWeight);
  }
  return {cumulativeWeights, totalWeight};
}

function randomItemPrecomputed(array, cumulativeWeights, totalWeight) {
  if (!array || array.length == 0) return;
  const randomValue = random() * totalWeight;
  let low = 0;
  let high = cumulativeWeights.length - 1;
  while (low <= high) {
    const mid = floor((low + high) / 2);
    if (randomValue <= cumulativeWeights[mid]) high = mid - 1;
    else low = mid + 1;
  }
  return array[low];
}

function loadCjkData() {
  const candidates = [
    path.join(SCRIPT_DIR, "data", "decomposeCJK.json"),
    path.join(REPO_ROOT, "data", "decomposeCJK.json"),
    path.join(REPO_ROOT, "Code", "data", "decomposeCJK.json"),
  ];
  for (const p of candidates) {
    if (!fs.existsSync(p)) continue;
    const raw = JSON.parse(fs.readFileSync(p, "utf8"));
    console.log(`CJK_DATA: ${raw.length} rows from ${p}`);
    return raw;
  }
  console.warn(
    "CJK_DATA: decomposeCJK.json not found — sampling Unicode ranges",
  );
  const rows = [];
  const pushRange = (a, b, w) => {
    for (let i = 0; i < 400; i++) {
      const c = a + floor(random() * (b - a + 1));
      rows.push({character: fromCodePoint(c), weight: w});
    }
  };
  pushRange(0x4e00, 0x9fff, 8);
  pushRange(0x3400, 0x4dbf, 2);
  pushRange(0x20000, 0x2a6df, 2);
  pushRange(0x17000, 0x187f7, 2);
  pushRange(0x18b00, 0x18cff, 2);
  return rows;
}

const CJK_DATA = loadCjkData();
for (const ch of sampleRange(0x17000, 0x187f7, 200))
  CJK_DATA.push({character: ch, weight: 2});
for (const ch of sampleRange(0x18d00, 0x18d1e, 40))
  CJK_DATA.push({character: ch, weight: 2});
for (const ch of sampleRange(0x18b00, 0x18cff, 120))
  CJK_DATA.push({character: ch, weight: 2});

const {cumulativeWeights: CJK_WEIGHTS, totalWeight: CJK_TOTAL} =
  precomputeCumulativeWeights(CJK_DATA);
console.log(CJK_DATA.length, "CJK-set characters (Han+Tangut+Khitan)");

function getCJKCharacter() {
  return randomItemPrecomputed(CJK_DATA, CJK_WEIGHTS, CJK_TOTAL).character;
}

function getIdeographLike() {
  const r = random();
  if (r < 0.08) return randomItem(CHARACTERS.tangut);
  if (r < 0.12) return randomItem(CHARACTERS.khitan);
  return getCJKCharacter();
}

const HANGUL_WEIGHTS = [6, 3, 1];
const choseong = HANGUL_JAMO.choseong
  .match(/../g)
  .map(x => x.split``)
  .flatMap(([char, length]) => Array(HANGUL_WEIGHTS[length - 1]).fill(char));
const jungseong = HANGUL_JAMO.jungseong
  .match(/../g)
  .map(x => x.split``)
  .flatMap(([char, length]) => Array(HANGUL_WEIGHTS[length - 1]).fill(char));
const jongseong = HANGUL_JAMO.jongseong
  .match(/../g)
  .map(x => x.split``)
  .flatMap(([char, length]) => Array(HANGUL_WEIGHTS[length - 1]).fill(char));

function maybeHangulVs() {
  return random() < 0.35 ? randomItem(CHARACTERS.feHangulMirror) : "";
}

/**
 * Conjoining Hangul syllable: L (+VS) V (+VS) [T (+VS)] [FE04] [bangjeom].
 * ``withFe04`` forces a batchim then appends ``U+FE04`` (top-swap GPOS).
 */
function generateHangulSyllableJamo({
  withVs = false,
  withFe04 = false,
  withBatchim = null,
} = {}) {
  let L = randomItem(choseong);
  let V = randomItem(jungseong);
  const wantT =
    withFe04 || (withBatchim == null ? random() < 0.45 : !!withBatchim);
  let T = wantT ? randomItem(jongseong) : "";
  if (withVs || random() < 0.4) {
    L += maybeHangulVs() || (withVs ? vs(randomIntInclusive(1, 3)) : "");
    V += maybeHangulVs() || (withVs ? vs(randomIntInclusive(1, 3)) : "");
    if (T) T += maybeHangulVs() || (withVs ? vs(randomIntInclusive(1, 3)) : "");
  }
  let s = L + V + T;
  // FE04 is a syllable-final mark after T (open syllables ignore it).
  if (T && (withFe04 || random() < 0.25)) s += CHARACTERS.fe04;
  if (random() < 0.15) s += randomItem(CHARACTERS.bangjeom);
  return s;
}

function generateHangul(numberSyllables = 1, opts = {}) {
  return [...Array(numberSyllables)].map(() => generateHangulSyllableJamo(opts))
    .join``;
}

function attachDakuten(base, n = 0) {
  let s = base;
  const count = n || randomIntInclusive(1, 3);
  for (let i = 0; i < count; i++) {
    if (random() < 0.15) s += CHARACTERS.cgj;
    s += randomItem(CHARACTERS.dakutenMarks);
  }
  return s;
}

function cjkWithD4(ch) {
  return ch + (random() < 0.5 ? randomItem(CHARACTERS.feD4) : "");
}

function cjkChuNom(ch) {
  if (random() >= 0.08) return ch;
  const slot = random() < 0.35 ? "" : randomItem(
    [...inclusiveRange(0xfe00, 0xfe0f)].map(fromCodePoint),
  );
  return ch + slot + (random() < 0.5 ? CHARACTERS.ca : CHARACTERS.nhay);
}

function combiningSliceDigraph(a, b, {withD4 = false} = {}) {
  const [sideA, sideB] = randomItem(DIGRAPH_NICHE_PAIRS);
  const d4a = withD4 && random() < 0.4 ? randomItem(CHARACTERS.feD4) : "";
  const d4b = withD4 && random() < 0.4 ? randomItem(CHARACTERS.feD4) : "";
  return (
    a
    + d4a
    + CHARACTERS.feSquish[sideA]
    + CHARACTERS.feOv
    + b
    + d4b
    + CHARACTERS.feSquish[sideB]
  );
}

function cjkHalfDigraph(a = getIdeographLike(), b = getIdeographLike()) {
  return combiningSliceDigraph(a, b, {withD4: true});
}

function yiWithD4(ch = randomItem(CHARACTERS.yi)) {
  return ch + (random() < 0.5 ? randomItem(CHARACTERS.feD4) : "");
}

function yiSliceDigraph(
  a = randomItem(CHARACTERS.yi),
  b = randomItem(CHARACTERS.yi),
) {
  return combiningSliceDigraph(a, b, {withD4: true});
}

function kanaSyllable(script) {
  const useKata =
    script === "katakana" || script === "kata"
      ? true
      : script === "hiragana" || script === "hira"
        ? false
        : random() < 0.45;
  let s = kanaPuaChar({script: useKata ? "kata" : "hira"});
  // Yoon-ish: follow with a small (odd) PUA cell from the same script.
  if (random() < 0.12) {
    s += kanaPuaChar({
      script: useKata ? "kata" : "hira",
      small: true,
      orient: 0,
    });
  }
  // Occasional preceding small (sokuon-like).
  if (random() < 0.1) {
    s =
      kanaPuaChar({script: useKata ? "kata" : "hira", small: true, orient: 0})
      + s;
  }
  if (random() < 0.2) s += randomItem(CHARACTERS.dakutenMarks);
  return s;
}

/** Kana combining slice: A FE08 FE00 B FE09 (etc.) — PUA bases. */
function kanaSliceDigraph(script) {
  return combiningSliceDigraph(kanaSyllable(script), kanaSyllable(script));
}

function generateNumber(
  maxSigFigs,
  digits = "0123456789abcdefghijklmnopqrstuvwxyz",
) {
  let result = "";
  const [_0 = "0", _1 = "1"] = [...digits];
  const sigFigs = randomIntInclusive(1, maxSigFigs);
  const exponentDigits = randomIntInclusive(1, 3);
  const mantissaSign = random() < 0.5 ? "-" : "";
  const exponentSign = random() < 0.5 ? "-" : "";
  let mantissa = [...Array(sigFigs)].map(() => randomItem(digits)).join``;
  let exponent = [...Array(exponentDigits)].map(() => randomItem(digits))
    .join``;
  if (random() < 0.5) {
    const index = randomInt(0, mantissa.length);
    mantissa = `${mantissa.slice(0, index)}.${mantissa.slice(index)}`;
    if (mantissa.startsWith`.`) mantissa = mantissa.replace(/^\./, `${_0}.`);
  }
  mantissa = mantissa.replace(RegExp(`^${_0}+(?=[^.])`, "g"), "");
  let [integerPart = "", fractionPart = ""] = mantissa.split`.`;
  integerPart = pipe(
    integerPart,
    reverseString,
    string => string.match(/.{3}|.*/g).join`,`,
    reverseString,
  );
  fractionPart = fractionPart.match(/.{3}|.*/g).join`,`;
  integerPart = integerPart.replace(/^,/, "");
  fractionPart = fractionPart.replace(/,$/, "");
  mantissa = integerPart + (fractionPart ? "." + fractionPart : "");
  result += mantissaSign + mantissa;
  if (random() < 0.1)
    result += `\xd7${_1}${_0}<sup>${exponentSign}${exponent}</sup>`;
  return result;
}

const USED_READINGS = {};
const LENGTH_WEIGHTS = [128, 64, 32, 16, 8, 4, 2, 1];

function getWeightedLength() {
  const totalWeight = LENGTH_WEIGHTS.reduce((sum, weight) => sum + weight, 0);
  const randomValue = random() * totalWeight;
  let cumulativeWeight = 0;
  for (let i = 0; i < LENGTH_WEIGHTS.length; i++) {
    cumulativeWeight += LENGTH_WEIGHTS[i];
    if (randomValue < cumulativeWeight) return i + 1;
  }
  return 1;
}

/** Ruby `<rt>` from Hangul / Yi / Kana (not CJK — the three other scripts). */
function generateRubyReading(numberSyllables = 1) {
  const script = randomItem(["hangul", "yi", "kana"]);
  if (script === "hangul") {
    return generateHangul(numberSyllables, {withVs: random() < 0.35});
  }
  if (script === "yi") {
    let s = "";
    for (let i = 0; i < numberSyllables; i++) {
      if (random() < 0.22) s += yiSliceDigraph();
      else s += yiWithD4();
      if (random() < 0.12) s = attachDakuten(s, 1);
    }
    return s;
  }
  const kind = random() < 0.55 ? "hira" : "kata";
  let s = "";
  for (let i = 0; i < numberSyllables; i++) {
    if (random() < 0.18) s += kanaSliceDigraph(kind);
    else s += kanaSyllable(kind);
  }
  return s;
}

function generateReading(cjkArray) {
  let rubyResult = "";
  let reading = "";
  for (let [index, cjkChar] of entries(cjkArray)) {
    const readingLength = (getWeightedLength() % 3) + 1;
    if (index > 0 && cjkChar != "\u3005")
      reading =
        USED_READINGS[cjkChar]
        || (USED_READINGS[cjkChar] = generateRubyReading(readingLength));
    else reading = reading || generateRubyReading(readingLength);
    rubyResult += `${cjkChar}<rt>${reading}</rt>`;
  }
  return `<ruby>${rubyResult}</ruby>`;
}

const CHARACTER_CATEGORIES = {
  cjk: 48,
  yi: 12,
  hangul: 16,
  hiragana: 20,
  katakana: 10,
  acronym: 3,
  number: 6,
};

function generateString(maxLength = 1000) {
  let result = "";
  let previousCategory = "";
  let addOkurigana = false;

  while ([...result].length <= maxLength) {
    let category = getWeightedCategory(
      CHARACTER_CATEGORIES,
      true,
      previousCategory,
    );
    previousCategory = category;
    const runLength = getWeightedLength();

    switch (category) {
      case "hangul": {
        let chunk = generateHangul(runLength, {withVs: random() < 0.5});
        if (random() < 0.2) chunk = attachDakuten(chunk);
        result += chunk;
        addOkurigana = false;
        break;
      }
      case "yi": {
        let chunk = "";
        for (let i = 0; i < ceil(runLength / 2); i++) {
          if (random() < 0.25) chunk += yiSliceDigraph();
          else chunk += yiWithD4();
          if (random() < 0.15) chunk = attachDakuten(chunk, 1);
        }
        result += chunk;
        addOkurigana = false;
        break;
      }
      case "cjk": {
        let chars = Array(ceil(runLength / 2))
          .fill(null)
          .map((_, index) => {
            let character = getIdeographLike();
            for (let i = 1; i <= index; i++)
              if (random() < 0.05 ** i) character += "\u3005";
            character = cjkChuNom(character);
            if (random() < 0.25) character = cjkWithD4(character);
            return character;
          });
        if (chars.length >= 2 && random() < 0.3) {
          const i = randomInt(0, chars.length - 1);
          chars.splice(i, 2, cjkHalfDigraph(chars[i], chars[i + 1]));
        }
        let charArray = chars.join``;
        if (random() < 0.55) charArray = generateReading([...charArray]);
        result += charArray;
        addOkurigana = true;
        break;
      }
      case "hiragana":
      case "katakana": {
        let chunk = "";
        for (let i = 0; i < ceil(runLength / 2); i++) {
          if (random() < 0.2) chunk += kanaSliceDigraph(category);
          else chunk += kanaSyllable(category);
        }
        result += chunk;
        addOkurigana = false;
        break;
      }
      case "number": {
        result += generateNumber(randomIntInclusive(1, 8), CHARACTERS.digits);
        if (random() < 0.1) result += randomItem(CHARACTERS.cjkUnits);
        addOkurigana = false;
        break;
      }
      case "acronym": {
        const upperOrLower = random() < 0.5 ? "toUpperCase" : "toLowerCase";
        result += Array(randomInt(1, 4))
          .fill(null)
          .map(() => randomItem(CHARACTERS.alphabet)).join``[upperOrLower]();
        addOkurigana = false;
        break;
      }
    }

    // Okurigana after CJK: Edenia hiragana PUA (not Bopomofo).
    if (addOkurigana && random() < 0.5) {
      const n = randomIntInclusive(1, 2);
      let syllable = "";
      for (let i = 0; i < n; i++) syllable += kanaSyllable("hira");
      result += syllable;
    }

    let remainingLength = maxLength - [...result].length;
    let lastAddedElement = "content";
    let usedBrackets = new Set();
    while (remainingLength > 2) {
      const addPunctuation =
        random() < 0.05 && lastAddedElement != "punctuation";
      const addBracket = random() < 0.005 && lastAddedElement != "bracket";
      const addFormatting = random() < 0.0005;
      if (addPunctuation) {
        result += randomItem(CHARACTERS.midParagraphPunctuation);
        lastAddedElement = "punctuation";
        remainingLength--;
      } else if (addBracket) {
        const availableBrackets = BRACKETS.filter(b => !usedBrackets.has(b));
        if (availableBrackets.length > 0) {
          const nestBrackets = level => {
            if (level > 2 || random() < 0.5)
              return generateString(remainingLength - 2);
            const bracketPair = randomItem(availableBrackets);
            usedBrackets.add(bracketPair);
            const innerContent = nestBrackets(level + 1);
            remainingLength -= 2;
            return bracketPair.open + innerContent + bracketPair.close;
          };
          result += nestBrackets(1);
          lastAddedElement = "bracket";
        }
      } else if (addFormatting) {
        const formatOptions = [
          {length: 2, format: s => `_${s}_`},
          {length: 4, format: s => `**${s}**`},
          {length: 4, format: s => `~~${s}~~`},
          {length: 4, format: s => `==${s}==`},
          {length: 7, format: s => `<u>${s}</u>`},
        ].filter(option => remainingLength >= option.length);
        if (formatOptions.length > 0) {
          const selectedFormat = randomItem(formatOptions);
          const segmentLength = min(
            randomIntInclusive(1, 10),
            remainingLength - selectedFormat.length,
          );
          result += selectedFormat.format(generateString(segmentLength));
          remainingLength -= segmentLength + selectedFormat.length;
          lastAddedElement = "content";
        }
      } else break;
    }

    result = result
      .replace(/<\/ruby><ruby>/g, "")
      .replace(/[\0\ufffc\ufffd]|[\ud800-\udbff](?![\udc00-\udfff])/g, "");
  }
  return result;
}

function catalogBlock(title, lines) {
  return `## ${title}\n\n${lines.filter(Boolean).join("\n\n")}\n`;
}

function generateFeatureCatalog() {
  const sections = [];

  {
    const lines = [];
    lines.push(
      "**Jamo × FE00–FE03 mirrors** (font: `edenia hangul`) — L/V/T each may take VS",
    );
    lines.push(
      [...Array(12)]
        .map(() => generateHangulSyllableJamo({withVs: true}))
        .join("　"),
    );
    lines.push(
      "**FE04 batchim top-swap** — same closed syllable ± `U+FE04` (GPOS raises T / lowers LV)",
    );
    lines.push(
      [...Array(10)]
        .map(() => {
          // Shared L V T; only the second copy gets FE04.
          const L = randomItem(choseong);
          const V = randomItem(jungseong);
          const T = randomItem(jongseong);
          const base = L + V + T;
          return `${base}　${base}${CHARACTERS.fe04}`;
        })
        .join("　·　"),
    );
    lines.push("**Mirrors + FE04** (T required; FE04 after optional T×VS)");
    lines.push(
      [...Array(10)]
        .map(() => generateHangulSyllableJamo({withVs: true, withFe04: true}))
        .join("　"),
    );
    lines.push("**Canonical sample** (FE03 on L/V/T + FE04)");
    lines.push("ᄒ︃ᅮ︃ᆫ︂︄");
    lines.push("**With combining marks / CGJ**");
    lines.push(
      [...Array(8)]
        .map(() =>
          attachDakuten(
            generateHangulSyllableJamo({
              withVs: true,
              withFe04: random() < 0.5,
            }),
            2,
          ),
        )
        .join("　"),
    );
    lines.push(
      `**Mark inventory** (${CHARACTERS.dakutenMarks.length} visible · shared_diacritics)`,
    );
    lines.push(
      [...Array(24)].map(() => randomItem(CHARACTERS.dakutenMarks)).join("　"),
    );
    sections.push(catalogBlock("Hangul — ligatures & diacritics", lines));
  }

  {
    const lines = [];
    lines.push("**D4 orientations** bare + FE01–FE07 (font: `edenia yi`)");
    const base = sampleRange(0xa000, 0xa48c, 8);
    lines.push(
      base.map(ch => ["", ...CHARACTERS.feD4].map(v => ch + v).join("")).join("　"),
    );
    lines.push("**Slice digraphs** `A FE08 FE00 B FE09` (halves / triangles)");
    lines.push([...Array(10)].map(() => yiSliceDigraph()).join("　"));
    lines.push("**Yi + combining marks**");
    lines.push(
      [...Array(6)].map(() => attachDakuten(yiWithD4(), 2)).join("　"),
    );
    sections.push(catalogBlock("Yi — orientations, slices, diacritics", lines));
  }

  {
    const lines = [];
    lines.push(
      "**Edenia kana PUA** (not Unicode Hiragana/Katakana blocks) — font: `edenia kana`",
    );
    lines.push(
      "`i = L×8+o` · full `U+E000+2i` · small `U+E000+2i+1` · halfwidth `U+ED00+2i` / `+1`",
    );
    lines.push(
      `Chart: ${KANA_ROWS}×${KANA_COLS} hiragana + length/gemination (L=0…${HIRAGANA_COUNT - 1}), then katakana (L=${HIRAGANA_COUNT}…${KANA_LOGICAL_TOTAL - 1})`,
    );
    lines.push("**Hiragana PUA row sample** (identity o=0, full)");
    lines.push(kanaPuaScriptRange("hira").slice(0, 24).join(""));
    lines.push("**Katakana PUA row sample** (identity o=0, full)");
    lines.push(kanaPuaScriptRange("kata").slice(0, 24).join(""));
    lines.push("**D4 orientations** (one logical × o=0…7, full then small)");
    {
      const L = randomInt(0, KANA_PHONETIC);
      const row = [];
      for (let o = 0; o < KANA_D4_COUNT; o++) {
        const i = kanaPairIndex(L, o);
        row.push(fromCodePoint(kanaFullCp(i)) + fromCodePoint(kanaSmallCp(i)));
      }
      lines.push(row.join("　"));
    }
    lines.push("**Halfwidth PUA** (`U+ED00…`)");
    lines.push(
      [...Array(12)]
        .map(() =>
          kanaPuaChar({
            script: random() < 0.5 ? "hira" : "kata",
            halfwidth: true,
            small: random() < 0.3,
            orient: 0,
          }),
        )
        .join(""),
    );
    lines.push("**Plain + combining marks**");
    lines.push(
      [...Array(16)]
        .map(() => {
          let s = kanaSyllable(random() < 0.5 ? "hiragana" : "katakana");
          if (random() < 0.5) s += randomItem(CHARACTERS.dakutenMarks);
          return s;
        })
        .join(""),
    );
    lines.push(
      "**Slice digraphs** `A FE08 FE00 B FE09` (halves / triangles) on PUA bases",
    );
    lines.push(
      [...Array(10)]
        .map(() => kanaSliceDigraph(random() < 0.5 ? "hiragana" : "katakana"))
        .join("　"),
    );
    lines.push("**Mark stack + CGJ skip**");
    lines.push(
      [...Array(8)].map(() => attachDakuten(kanaSyllable(), 3)).join("　"),
    );
    sections.push(
      catalogBlock("Kana — PUA chart, D4, slices & diacritics", lines),
    );
  }

  {
    const lines = [];
    lines.push(
      "**CJK set** = Han + **Tangut** + **Khitan** (`edenia cjk` / `edenia cjk h`)",
    );
    lines.push("**Tangut sample**");
    lines.push(sampleRange(0x17000, 0x187f7, 24).join(""));
    lines.push("**Khitan Small Script sample**");
    lines.push(sampleRange(0x18b00, 0x18cff, 24).join(""));
    lines.push("**D4** bare + FE01–FE07 on ideographs");
    const ideos = [...Array(6)].map(() => getIdeographLike());
    lines.push(
      ideos.map(ch => ["", ...CHARACTERS.feD4].map(v => ch + v).join("")).join("　"),
    );
    lines.push("**ca / nhay** (`U+16FF0` / `U+16FF1`) — base face `CJK FE00–F MARK`");
    lines.push(
      [...Array(10)]
        .map(() => {
          const ch = getIdeographLike();
          const slot = randomItem(
            ["", ...[...inclusiveRange(0xfe00, 0xfe0f)].map(fromCodePoint)],
          );
          return ch + slot + CHARACTERS.ca + " " + ch + slot + CHARACTERS.nhay;
        })
        .join("　"),
    );
    lines.push(
      "**Slice digraphs** `A FE08 FE00 B FE09` (halves / triangles; needs `edenia cjk h`)",
    );
    const classic = cjkHalfDigraph("\u660e", "\u65e5");
    lines.push(
      [
        classic,
        ...Array(11)
          .fill(0)
          .map(() => cjkHalfDigraph()),
      ].join("　"),
    );
    lines.push("**All niche pairings on 明/日** (bare / FE01–FE03)");
    const nicheLines = [];
    for (const [sa, sb] of DIGRAPH_NICHE_PAIRS) {
      for (const d4 of ["", vs(1), vs(2), vs(3)]) {
        nicheLines.push(
          "\u660e"
            + d4
            + CHARACTERS.feSquish[sa]
            + CHARACTERS.feOv
            + "\u65e5"
            + CHARACTERS.feSquish[sb],
        );
      }
    }
    lines.push(nicheLines.join("　"));
    sections.push(
      catalogBlock(
        "CJK set (Han · Tangut · Khitan) — compounds & marks",
        lines,
      ),
    );
  }

  {
    const mix = [
      generateHangul(3, {withVs: true}),
      yiSliceDigraph(),
      kanaSliceDigraph(),
      cjkHalfDigraph(),
      sampleRange(0x18b00, 0x18cff, 4).join(""),
      sampleRange(0x17000, 0x17100, 4).join(""),
    ].join("｜");
    sections.push(
      catalogBlock("Mixed one-liner (all scripts)", [
        mix,
        attachDakuten(mix, 2),
      ]),
    );
  }

  return (
    "# Edenia feature catalog\n\n"
    + "_Auto-generated ligature / compound / diacritic checklist. "
    + "Stack: Hangul → Kana → Yi → `edenia cjk h` → `edenia cjk`._\n\n"
    + sections.join("\n")
  );
}

function generateMarkdown(numLines) {
  const LINE_CATEGORIES = {
    paragraph: 0.7,
    heading: 0.2,
    list: 0.5,
    quote: 0.2,
    ordered_list: 0.5,
  };

  const generateIndent = function* (numberOfIndents) {
    let currentIndent = 0;
    yield currentIndent;
    while (true) {
      currentIndent++;
      if (currentIndent >= numberOfIndents || random() < 0.5) {
        currentIndent -= ceil(random() * numberOfIndents);
        if (currentIndent <= 0) currentIndent = 0;
      }
      yield currentIndent;
    }
  };

  let result = "";
  let previousCategory = "";
  const headings = generateIndent(5);
  const orderedListCounters = {};

  for (let i = 0; i < numLines; i++) {
    let content = "";
    let category = getWeightedCategory(LINE_CATEGORIES, true, previousCategory);
    previousCategory = category;
    if (i == 0) category = "heading";
    if (i > 0 && random() < 0.25) category = "paragraph";

    switch (category) {
      case "paragraph": {
        content = `${generateString(randomIntInclusive(256, 4096))}${randomItem(
          CHARACTERS.endParagraphPunctuation,
        )}\n`;
        break;
      }
      case "heading": {
        const headingLevel = headings.next().value + 1;
        content = `${"#".repeat(headingLevel)} ${generateString(
          randomIntInclusive(32, 256),
        )}\n`;
        break;
      }
      case "quote": {
        const quoteIndentSequence = generateIndent(2);
        for (let j = 0; j < randomInt(1, 5); j++) {
          const quoteIndentLevel = quoteIndentSequence.next().value;
          content += `${"> ".repeat(quoteIndentLevel)}> ${generateString(
            randomIntInclusive(128, 1024),
          )}${randomItem(CHARACTERS.endParagraphPunctuation)}\n`;
        }
        break;
      }
      case "list": {
        const listType = randomItem("-+");
        const listIndentSequence = generateIndent(6);
        for (let j = 0; j < randomInt(1, 10); j++) {
          const listIndentLevel = listIndentSequence.next().value;
          content += `${"  ".repeat(listIndentLevel)}${listType} ${generateString(
            randomIntInclusive(64, 512),
          )}\n`;
        }
        break;
      }
      case "ordered_list": {
        const listIndentSequence = generateIndent(6);
        let previousIndentLevel = -1;
        for (let j = 0; j < randomInt(1, 10); j++) {
          const listIndentLevel = listIndentSequence.next().value;
          if (listIndentLevel > previousIndentLevel)
            orderedListCounters[listIndentLevel] = 1;
          const counter = orderedListCounters[listIndentLevel] || 1;
          content += `${"  ".repeat(listIndentLevel)}${counter}. ${generateString(
            randomIntInclusive(64, 512),
          )}\n`;
          orderedListCounters[listIndentLevel] = counter + 1;
          previousIndentLevel = listIndentLevel;
        }
        for (const level in orderedListCounters)
          if (parseInt(level) > previousIndentLevel)
            delete orderedListCounters[level];
        break;
      }
    }
    result += content + "\n\n";
  }
  return result.replace(/\n{3,}/g, "\n\n").trim();
}

function main() {
  const parts = [];
  parts.push("---");
  parts.push("title: Edenian script test");
  parts.push(`generated: ${new Date().toISOString()}`);
  parts.push("---\n");

  if (!ARGS.proseOnly) parts.push(generateFeatureCatalog());
  if (!ARGS.catalogOnly) {
    parts.push("\n# Random prose\n");
    parts.push(generateMarkdown(ARGS.lines));
  }

  const text =
    parts
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim() + "\n";
  fs.mkdirSync(path.dirname(ARGS.out), {recursive: true});
  fs.writeFileSync(ARGS.out, text, "utf8");
  console.log(`wrote ${ARGS.out} (${[...text].length} code points)`);
}

main();
