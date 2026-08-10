#!/usr/bin/env python3
"""Refresh Obsidian theme.css pan-font blocks from GitHub CDN or local dist.

Each panCJK bucket keeps its own family name (``pancjk 4E``, …) matching the
name table inside the WOFF2. Digraphs and Obsidian both need those distinct
names — renaming every face to a shared ``pancjk`` does not work reliably
with ``FontFace`` / the theme stack.

Default: font files via **jsDelivr**.

``--bake``: Obsidian cannot resolve relative ``url(./…)`` (becomes
``app://obsidian.md/…``), blocks ``file://``, and truncates huge ``data:``
themes — so bake writes a tiny **plugin** that injects faces with
``FontFace`` + ``readBinary``.

Usage::

    python Scripts/update_obsidian_theme_fonts.py
    python Scripts/update_obsidian_theme_fonts.py --local
    python Scripts/update_obsidian_theme_fonts.py --bake
    python Scripts/update_obsidian_theme_fonts.py --bake --vault path/to/vault

Markers (inserted on first run if missing)::

    /* === BEGIN auto pan fonts (update_obsidian_theme_fonts.py) === */
    /* === END auto pan fonts === */
    /* === BEGIN auto pan font stack (update_obsidian_theme_fonts.py) === */
    /* === END auto pan font stack === */
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DIST_DIR = SCRIPT_DIR / "dist"
BAKE_FOLDERS = ("hangul", "yi", "subfonts")
PLUGIN_ID = "panfonts"
PLUGIN_DIR = SCRIPT_DIR / "obsidian-panfonts"

CDN_GH = "https://cdn.jsdelivr.net/gh/nexovolta/fonts@main/Scripts/dist"
CDN_BASE = CDN_GH
CSS_URLS = {
    "hangul": f"{CDN_GH}/hangul/panhangul.css",
    "yi": f"{CDN_GH}/yi/panyi.css",
    "cjk": f"{CDN_GH}/subfonts/pancjk.css",
}
CSS_URLS_FALLBACK = {
    "hangul": (
        "https://cdn.statically.io/gh/nexovolta/fonts@main/"
        "Scripts/dist/hangul/panhangul.css"
    ),
    "yi": (
        "https://cdn.statically.io/gh/nexovolta/fonts@main/" "Scripts/dist/yi/panyi.css"
    ),
    "cjk": (
        "https://cdn.statically.io/gh/nexovolta/fonts@main/"
        "Scripts/dist/subfonts/pancjk.css"
    ),
}
LOCAL_CSS = {
    "hangul": DIST_DIR / "hangul" / "panhangul.css",
    "yi": DIST_DIR / "yi" / "panyi.css",
    "cjk": DIST_DIR / "subfonts" / "pancjk.css",
}

_JSDELIVR_FONT = re.compile(
    r"https://(?:cdn|fastly)\.jsdelivr\.net/gh/nexovolta/fonts@[^/]+/" r"Scripts/dist/",
    re.I,
)
_RAW_GH_FONT = re.compile(
    r"https://raw\.githubusercontent\.com/nexovolta/fonts/[^/]+/" r"Scripts/dist/",
    re.I,
)
_STATICALLY_FONT = re.compile(
    r"https://cdn\.statically\.io/gh/nexovolta/fonts(?:@main|/main)/" r"Scripts/dist/",
    re.I,
)
_PANCJK_FAMILY = re.compile(r"font-family:\s*['\"](pancjk\s+[0-9A-Fa-f]+)['\"]")

MARK_FACES_BEGIN = "/* === BEGIN auto pan fonts (update_obsidian_theme_fonts.py) === */"
MARK_FACES_END = "/* === END auto pan fonts === */"
MARK_STACK_BEGIN = (
    "/* === BEGIN auto pan font stack (update_obsidian_theme_fonts.py) === */"
)
MARK_STACK_END = "/* === END auto pan font stack === */"

STACK_LATIN = (
    "Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono, "
    "FlopDesignFont, MKanaPlus"
)
STACK_CJK_TAIL = "panyi, panhangul, panhanguls, Plangothic P1, Plangothic P2"
STACK_TAIL = "monospace"


def fetch_text(url: str, timeout: float = 60.0) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "update-obsidian-theme-fonts/1"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def to_cdn_url(url: str) -> str:
    url = _JSDELIVR_FONT.sub(f"{CDN_GH}/", url)
    url = _RAW_GH_FONT.sub(f"{CDN_GH}/", url)
    url = _STATICALLY_FONT.sub(f"{CDN_GH}/", url)
    return url


def load_css(kind: str, *, local: bool) -> str:
    if local:
        path = LOCAL_CSS[kind]
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"  local {path.relative_to(REPO_ROOT)}")
        return path.read_text(encoding="utf-8")
    for label, url in (
        ("jsdelivr", CSS_URLS[kind]),
        ("statically", CSS_URLS_FALLBACK[kind]),
    ):
        print(f"  fetch [{label}] {url}")
        try:
            return fetch_text(url)
        except urllib.error.URLError as exc:
            print(f"  [!] {label} failed ({exc})")
    path = LOCAL_CSS[kind]
    if path.is_file():
        print(f"  [!] falling back to {path.name}")
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"no CSS source for {kind}")


def sync_woff2(dest_root: Path) -> int:
    """Copy hangul/yi/subfonts .woff2 into dest_root/{folder}/."""
    n = 0
    for folder in BAKE_FOLDERS:
        src_dir = DIST_DIR / folder
        dst_dir = dest_root / folder
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in sorted(src_dir.glob("*.woff2")):
            shutil.copy2(src, dst_dir / src.name)
            n += 1
    try:
        shown = dest_root.relative_to(REPO_ROOT)
    except ValueError:
        shown = dest_root
    print(f"  synced {n} .woff2 -> {shown}")
    return n


def pancjk_families_from_css(css: str) -> list[str]:
    """Ordered unique per-bucket family names from pancjk.css."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _PANCJK_FAMILY.finditer(css):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def css_family_token(name: str) -> str:
    """Quote family names that contain spaces."""
    if re.search(r"[\s,]", name):
        return f'"{name}"'
    return name


def collect_faces(css: str, *, folder: str) -> list[dict]:
    """Parse @font-face list for the panfonts plugin (keep CSS family names)."""
    out: list[dict] = []
    for m in re.finditer(r"@font-face\s*\{([^{}]*)\}", css, flags=re.S):
        block = m.group(1)
        fam_m = re.search(r"font-family:\s*['\"]([^'\"]+)['\"]", block)
        ur_m = re.search(r"unicode-range:\s*([^;]+);", block, flags=re.I)
        name_m = re.search(
            r"url\((['\"])(?:https:[^'\"]+/|\./)?([^'\"/]+\.woff2)\1\)",
            block,
        )
        if not (fam_m and ur_m and name_m):
            continue
        out.append(
            {
                "family": fam_m.group(1),
                "file": f"panfonts/{folder}/{name_m.group(2)}",
                "unicodeRange": ur_m.group(1).strip(),
            }
        )
    return out


def write_plugin(faces: list[dict]) -> None:
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": PLUGIN_ID,
        "name": "Pan Fonts",
        "version": "1.2.0",
        "minAppVersion": "1.5.0",
        "description": "Loads baked pancjk XX / panyi / panhangul via FontFace + readBinary.",
        "author": "nexovolta",
        "isDesktopOnly": False,
    }
    (PLUGIN_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    faces_json = json.dumps(faces, ensure_ascii=True)
    # Prefer vault-root panfonts/ (getResourcePath-friendly). Fall back to
    # files beside the plugin. Load with FontFace(ArrayBuffer) — app://
    # resource URLs under .obsidian/plugins often 404 for @font-face.
    main_js = f"""const {{ Plugin, normalizePath }} = require("obsidian");

/* Auto-generated by update_obsidian_theme_fonts.py --bake — do not edit. */
const FACES = {faces_json};
const BATCH = 24;

module.exports = class PanFontsPlugin extends Plugin {{
  async onload() {{
    const adapter = this.app.vault.adapter;
    const pluginRoot = normalizePath(
      this.manifest.dir ||
        `${{this.app.vault.configDir}}/plugins/${{this.manifest.id}}`
    );

    const resolve = async (file) => {{
      const vaultRel = normalizePath(file);
      if (await adapter.exists(vaultRel)) return vaultRel;
      const beside = normalizePath(`${{pluginRoot}}/${{file}}`);
      if (await adapter.exists(beside)) return beside;
      return null;
    }};

    let ok = 0;
    let missing = 0;
    let failed = 0;

    const loadOne = async (f) => {{
      const rel = await resolve(f.file);
      if (!rel) {{
        missing++;
        if (missing <= 5) console.warn(`[panfonts] missing ${{f.file}}`);
        return;
      }}
      try {{
        const buf = await adapter.readBinary(rel);
        const face = new FontFace(f.family, buf, {{
          style: "normal",
          weight: "normal",
          display: "swap",
          unicodeRange: f.unicodeRange,
        }});
        await face.load();
        document.fonts.add(face);
        ok++;
      }} catch (err) {{
        failed++;
        if (failed <= 5) console.warn(`[panfonts] fail ${{rel}}`, err);
      }}
    }};

    console.info(`[panfonts] loading ${{FACES.length}} faces…`);
    for (let i = 0; i < FACES.length; i += BATCH) {{
      await Promise.all(FACES.slice(i, i + BATCH).map(loadOne));
    }}
    console.info(
      `[panfonts] ready: ${{ok}} loaded, ${{missing}} missing, ${{failed}} failed`
    );
  }}
}};
"""
    (PLUGIN_DIR / "main.js").write_text(main_js, encoding="utf-8")
    print(f"  wrote plugin ({len(faces)} faces) -> {PLUGIN_DIR.relative_to(REPO_ROOT)}")


def install_to_vault(vault: Path) -> None:
    """Sync vault/panfonts + copy plugin js into .obsidian/plugins/obsidian-panfonts."""
    vault = vault.resolve()
    if not (vault / ".obsidian").is_dir():
        raise FileNotFoundError(f"not an Obsidian vault (no .obsidian): {vault}")
    sync_woff2(vault / "panfonts")
    plug = vault / ".obsidian" / "plugins" / "obsidian-panfonts"
    plug.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN_DIR / "main.js", plug / "main.js")
    shutil.copy2(PLUGIN_DIR / "manifest.json", plug / "manifest.json")
    # Optional: keep a copy under the plugin too (offline if vault panfonts deleted)
    sync_woff2(plug / "panfonts")
    print(f"  installed plugin -> {plug}")


def _face_src(block: str, *, folder: str) -> str:
    """One absolute jsDelivr src (drop ./ and legacy CDNs)."""

    def repl(m: re.Match[str]) -> str:
        chunk = m.group(0)
        urls = re.findall(r"url\((['\"])(https:[^'\"]+)\1\)", chunk)
        if urls:
            name = urls[0][1].rsplit("/", 1)[-1]
        else:
            rel = re.findall(r"url\((['\"])\./([^'\"]+\.woff2)\1\)", chunk)
            if not rel:
                return chunk
            name = rel[0][1]
        pick = f"{CDN_GH}/{folder}/{name}"
        return f'src: url("{pick}") format("woff2");'

    return re.sub(
        r"src:\s*(?:url\([^)]+\)(?:\s*format\([^)]+\))?\s*,?\s*)+;?",
        repl,
        block,
        flags=re.I | re.S,
    )


def transform_face_css(css: str, *, folder: str) -> str:
    """Keep per-bucket family names; rewrite src URLs to jsDelivr."""
    out = to_cdn_url(css)

    def face_fix(m: re.Match[str]) -> str:
        block = m.group(0)
        block = _face_src(block, folder=folder)
        return _double_quotes(block)

    out = re.sub(r"@font-face\s*\{[^{}]*\}", face_fix, out, flags=re.S)
    return out.strip() + "\n"


def build_faces_block(hangul: str, yi: str, cjk: str, *, bake: bool) -> str:
    if bake:
        return "\n".join(
            [
                MARK_FACES_BEGIN,
                "/* Hangul + Yi + Pan-CJK: loaded by the panfonts Obsidian plugin",
                "   (Scripts/obsidian-panfonts). Relative/data URLs do not work. */",
                MARK_FACES_END,
                "",
            ]
        )
    parts = [
        MARK_FACES_BEGIN,
        "/* Hangul + Yi + Pan-CJK via jsDelivr (per-bucket pancjk XX families). */",
        "",
        transform_face_css(hangul, folder="hangul").rstrip(),
        "",
        transform_face_css(yi, folder="yi").rstrip(),
        "",
        transform_face_css(cjk, folder="subfonts").rstrip(),
        "",
        MARK_FACES_END,
        "",
    ]
    return "\n".join(parts)


def _double_quotes(css: str) -> str:
    return css.replace("'", '"')


def build_stack_block(*, pancjk_families: list[str]) -> str:
    if not pancjk_families:
        raise ValueError("no pancjk XX families found in CSS")
    cjk = ", ".join(
        [css_family_token(n) for n in pancjk_families] + [STACK_CJK_TAIL]
    )
    stack = f"{STACK_LATIN}, {cjk}, {STACK_TAIL}"
    return "\n".join(
        [
            MARK_STACK_BEGIN,
            "/* Force --font-text: one entry per panCJK bucket (matches name tables). */",
            "body {",
            f"  --font-text-theme: {stack};",
            f"  --font-interface-theme: {stack};",
            f"  --font-monospace-theme: {stack};",
            f"  --font-text: {stack} !important;",
            f"  --font-interface: {stack} !important;",
            f"  --font-monospace: {stack} !important;",
            '  --font-editor-theme: "";',
            "  --font-editor: var(--font-editor-theme), var(--font-text);",
            "}",
            MARK_STACK_END,
            "",
        ]
    )


def _replace_marked(text: str, begin: str, end: str, new_block: str) -> str:
    pattern = re.compile(
        re.escape(begin) + r".*?" + re.escape(end) + r"\n?",
        re.S,
    )
    if pattern.search(text):
        return pattern.sub(new_block.rstrip() + "\n", text, count=1)
    return text


def _replace_legacy_faces(text: str, new_block: str) -> str:
    pattern = re.compile(
        r"/\* Auto-generated Hangul fonts from Malgun Gothic \*/.*?"
        r"(?=\n/\* src/scss/index\.scss[^\n]*Pan-CJK pigeonhole font stack \*/"
        r"|\n" + re.escape(MARK_STACK_BEGIN) + r")",
        re.S,
    )
    if pattern.search(text):
        return pattern.sub(new_block, text, count=1)
    pattern2 = re.compile(
        r"/\* Auto-generated Pan-CJK pigeonhole @font-face rules \*/.*?"
        r"(?=\n/\* src/scss/index\.scss[^\n]*Pan-CJK pigeonhole font stack \*/"
        r"|\n" + re.escape(MARK_STACK_BEGIN) + r")",
        re.S,
    )
    if pattern2.search(text):
        return pattern2.sub(new_block, text, count=1)
    raise RuntimeError("could not find hangul/pancjk @font-face region in theme")


def _replace_legacy_stack(text: str, new_block: str) -> str:
    pattern = re.compile(
        r"/\* src/scss/index\.scss[^\n]*Pan-CJK pigeonhole font stack \*/\s*"
        r"body\s*\{.*?\n\}\s*",
        re.S,
    )
    if pattern.search(text):
        return pattern.sub(new_block, text, count=1)
    # Drop a lone shared "pancjk" left by older theme patches.
    collapsed = re.sub(
        r'(?<=,\s)pancjk(?=\s*,)|(?<=,\s)"pancjk"(?=\s*,)',
        "",
        text,
    )
    collapsed = re.sub(r",\s*,", ", ", collapsed)
    if collapsed != text:
        return collapsed
    raise RuntimeError("could not find pan font stack body block in theme")


def patch_theme(theme_path: Path, faces: str, stack: str) -> None:
    text = theme_path.read_text(encoding="utf-8")
    if MARK_FACES_BEGIN in text:
        text = _replace_marked(text, MARK_FACES_BEGIN, MARK_FACES_END, faces)
    else:
        text = _replace_legacy_faces(text, faces)

    if MARK_STACK_BEGIN in text:
        text = _replace_marked(text, MARK_STACK_BEGIN, MARK_STACK_END, stack)
    else:
        text = _replace_legacy_stack(text, stack)

    theme_path.write_text(text, encoding="utf-8")
    n_unique = len(set(re.findall(r'["\']pancjk\s+[0-9A-Fa-f]+["\']', text)))
    size_mb = theme_path.stat().st_size / (1024 * 1024)
    print(
        f"Wrote {theme_path} (pancjk XX families~{n_unique}, {size_mb:.1f} MiB)"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--theme",
        type=Path,
        action="append",
        help="theme.css path (repeatable). Default: repo theme.css",
    )
    ap.add_argument(
        "--local",
        action="store_true",
        help="Read Scripts/dist CSS instead of jsDelivr",
    )
    ap.add_argument(
        "--bake",
        action="store_true",
        help=(
            "Build Scripts/obsidian-panfonts plugin with local .woff2 "
            "(implies --local). Theme keeps the font stack only."
        ),
    )
    ap.add_argument(
        "--vault",
        type=Path,
        help=(
            "With --bake: install into this Obsidian vault "
            "(sync <vault>/panfonts + plugin under .obsidian/plugins/)"
        ),
    )
    ap.add_argument(
        "--also-private",
        action="store_true",
        help="Also patch Scripts/private/theme.css",
    )
    args = ap.parse_args(argv)

    if args.bake:
        args.local = True

    themes: list[Path] = list(args.theme or [REPO_ROOT / "theme.css"])
    if args.also_private:
        themes.append(SCRIPT_DIR / "private" / "theme.css")

    print("Loading pan font CSS…")
    hangul = load_css("hangul", local=args.local)
    yi = load_css("yi", local=args.local)
    cjk = load_css("cjk", local=args.local)

    if args.bake:
        print("Baking Obsidian panfonts plugin…")
        sync_woff2(PLUGIN_DIR / "panfonts")
        faces_meta = (
            collect_faces(hangul, folder="hangul")
            + collect_faces(yi, folder="yi")
            + collect_faces(cjk, folder="subfonts")
        )
        write_plugin(faces_meta)
        if args.vault:
            install_to_vault(args.vault)
        for stale in (REPO_ROOT / "panfonts", SCRIPT_DIR / "private" / "panfonts"):
            if stale.is_dir():
                shutil.rmtree(stale)
                print(f"  removed stale {stale.relative_to(REPO_ROOT)}")

    pancjk_families = pancjk_families_from_css(cjk)
    print(f"  pancjk families: {len(pancjk_families)}")
    faces = build_faces_block(hangul, yi, cjk, bake=args.bake)
    stack = build_stack_block(pancjk_families=pancjk_families)
    if not args.bake:
        n = len(re.findall(r"@font-face", faces))
        print(f"Built Obsidian face block ({n} @font-face)")
    else:
        print("Theme face block: plugin stub (no @font-face URLs)")

    for path in themes:
        if not path.is_file():
            print(f"[!] skip missing {path}", file=sys.stderr)
            continue
        patch_theme(path, faces, stack)
    print(
        "Note: if CJK still missing in Obsidian, reset Appearance / Style Settings "
        "text font (cached stacks may still list a lone shared 'pancjk')."
    )
    if args.bake and not args.vault:
        print(
            f"Install: python Scripts/update_obsidian_theme_fonts.py --bake "
            f"--vault <vault-root>"
        )
    elif args.bake:
        print(
            "Reload Obsidian; console should show "
            f"[panfonts] ready: {3 + len(pancjk_families)} loaded."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
