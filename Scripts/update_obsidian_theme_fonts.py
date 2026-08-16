#!/usr/bin/env python3
"""Refresh Obsidian theme.css Edenia font blocks from GitHub CDN or local dist.

CJK faces share ``edenia cjk`` / ``edenia cjk h`` / … with per-bucket
``unicode-range``. ``U+FE00–FE0F`` must be listed on each face — Blink drops
Default_Ignorable selectors that match no range, which breaks digraph GSUB.

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

from cdn_fonts import dist_rel, format_src_line, remote_urls
from edenia_names import (
    CSS_CJK,
    CSS_HANGUL,
    CSS_KANA,
    CSS_YI,
    PLUGIN_ASSET,
    PLUGIN_CLASS,
    PLUGIN_DIR_NAME,
    PLUGIN_DISPLAY_NAME,
    PLUGIN_ID,
    STACK_CJK_TAIL,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DIST_DIR = SCRIPT_DIR / "dist"
BAKE_FOLDERS = ("hangul", "yi", "kana", "cjk")
PLUGIN_DIR = SCRIPT_DIR / PLUGIN_DIR_NAME

# CSS fetch order: GitHub raw → statically → jsDelivr (avoid overloading one host).
_CSS_REL = {
    "hangul": f"Scripts/dist/hangul/{CSS_HANGUL}",
    "yi": f"Scripts/dist/yi/{CSS_YI}",
    "kana": f"Scripts/dist/kana/{CSS_KANA}",
    "cjk": f"Scripts/dist/cjk/{CSS_CJK}",
}
CSS_URLS = {k: remote_urls(rel)[0] for k, rel in _CSS_REL.items()}
CSS_URLS_FALLBACK = {k: remote_urls(rel)[1:] for k, rel in _CSS_REL.items()}
LOCAL_CSS = {
    "hangul": DIST_DIR / "hangul" / CSS_HANGUL,
    "yi": DIST_DIR / "yi" / CSS_YI,
    "kana": DIST_DIR / "kana" / CSS_KANA,
    "cjk": DIST_DIR / "cjk" / CSS_CJK,
}

_ANY_NEXOVOLTA_DIST = re.compile(
    r"https://(?:"
    r"raw\.githubusercontent\.com/nexovolta/fonts/[^/]+|"
    r"cdn\.statically\.io/gh/nexovolta/fonts(?:@main|/main)|"
    r"(?:cdn|fastly|gcore)\.jsdelivr\.net/gh/nexovolta/fonts@[^/]+"
    r")/"
    r"Scripts/dist/",
    re.I,
)
_PANCJK_FAMILY = re.compile(
    r"font-family:\s*['\"](edenia cjk(?:\s+(?:qh|qv|[ht]))?)['\"]"
)

MARK_FACES_BEGIN = "/* === BEGIN auto pan fonts (update_obsidian_theme_fonts.py) === */"
MARK_FACES_END = "/* === END auto pan fonts === */"
MARK_STACK_BEGIN = (
    "/* === BEGIN auto pan font stack (update_obsidian_theme_fonts.py) === */"
)
MARK_STACK_END = "/* === END auto pan font stack === */"

STACK_LATIN = "Caesium, Cascadia, Cascadia Code, Nexsevka, JuliaMono"
STACK_TAIL = "monospace"


def fetch_text(url: str, timeout: float = 60.0) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "update-obsidian-theme-fonts/1"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def to_cdn_url(url: str) -> str:
    """Normalize any known mirror URL to GitHub raw (primary)."""
    return _ANY_NEXOVOLTA_DIST.sub(
        "https://raw.githubusercontent.com/nexovolta/fonts/main/Scripts/dist/",
        url,
    )


def load_css(kind: str, *, local: bool) -> str:
    if local:
        path = LOCAL_CSS[kind]
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"  local {path.relative_to(REPO_ROOT)}")
        return path.read_text(encoding="utf-8")
    sources: list[tuple[str, str]] = [("raw", CSS_URLS[kind])]
    for i, url in enumerate(CSS_URLS_FALLBACK[kind]):
        label = ("statically", "jsdelivr", "fastly", "gcore")[min(i, 3)]
        sources.append((label, url))
    for label, url in sources:
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
    """Copy hangul/yi/kana/cjk .woff2 into dest_root/{folder}/."""
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
    """Ordered unique shared family names from edenia-cjk.css."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _PANCJK_FAMILY.finditer(css):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def pancjk_stack_families(css: str) -> list[str]:
    """Body stack CJK families: ``h`` first (digraph/FE0B–F GSUB), then base.

    Niche ligas live on the half-cell face. Base alone cannot merge compounds.
    ``t`` / ``qv`` / ``qh`` stay opt-in (explicit pin), not in the body stack.
    """
    families = pancjk_families_from_css(css)
    out: list[str] = []
    for name in ("edenia cjk h", "edenia cjk"):
        if name in families:
            out.append(name)
    if out:
        return out
    # Older CSS listed per-bucket ``edenia cjk XX`` — keep those for stack.
    return families


def css_family_token(name: str) -> str:
    """Quote family names that contain spaces."""
    if re.search(r"[\s,]", name):
        return f'"{name}"'
    return name


_FE0_RANGE = "U+FE00-FE0F"


def _ensure_fe0_unicode_range(ur: str) -> str:
    """Blink drops FE0* when no FontFace unicode-range claims them."""
    if re.search(r"U\+FE0[0-9A-Fa-f]", ur):
        return ur
    return f"{ur}, {_FE0_RANGE}" if ur else _FE0_RANGE


def collect_faces(css: str, *, folder: str) -> list[dict]:
    """Parse @font-face list for the Edenia plugin (family + unicode-range)."""
    out: list[dict] = []
    for m in re.finditer(r"@font-face\s*\{([^{}]*)\}", css, flags=re.S):
        block = m.group(1)
        fam_m = re.search(r"font-family:\s*['\"]([^'\"]+)['\"]", block)
        name_m = re.search(
            r"url\((['\"])(?:https:[^'\"]+/|\./)?([^'\"/]+\.woff2)\1\)",
            block,
        )
        if not (fam_m and name_m):
            continue
        face: dict = {
            "family": fam_m.group(1),
            "file": f"{PLUGIN_ASSET}/{folder}/{name_m.group(2)}",
        }
        ur_m = re.search(r"unicode-range:\s*([^;]+);", block)
        if ur_m:
            ur = " ".join(ur_m.group(1).split())
            # CJK pigeonholes need FE0* in-range or digraph/mark GSUB never runs.
            if folder == "cjk":
                ur = _ensure_fe0_unicode_range(ur)
            face["unicodeRange"] = ur
        out.append(face)
    return out


def write_plugin(faces: list[dict]) -> None:
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": PLUGIN_ID,
        "name": PLUGIN_DISPLAY_NAME,
        "version": "1.3.0",
        "minAppVersion": "1.5.0",
        "description": "Loads baked edenia cjk / yi / kana / hangul via FontFace + local files.",
        "author": "nexovolta",
        "isDesktopOnly": False,
    }
    (PLUGIN_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    faces_json = json.dumps(faces, ensure_ascii=True)
    # Fonts live under .obsidian/plugins/obsidian-edenia/edenia/ only (not vault
    # root). Load with Node fs (vault.adapter often misses plugin-dir reads) and
    # pass unicodeRange so shared CJK families do not collide.
    main_js = f"""const {{ Plugin, normalizePath }} = require("obsidian");
const fs = require("fs");
const path = require("path");

/* Auto-generated by update_obsidian_theme_fonts.py --bake — do not edit. */
const FACES = {faces_json};
const BATCH = 24;
const PLUGIN_FOLDER = {json.dumps(PLUGIN_DIR_NAME)};

module.exports = class {PLUGIN_CLASS} extends Plugin {{
  async onload() {{
    const adapter = this.app.vault.adapter;
    const base =
      typeof adapter.getBasePath === "function"
        ? adapter.getBasePath()
        : adapter.basePath || "";
    const relPlugin = normalizePath(
      this.manifest.dir ||
        `${{this.app.vault.configDir}}/plugins/${{PLUGIN_FOLDER}}`
    );
    const pluginAbs = base ? path.join(base, relPlugin) : relPlugin;

    const resolveAbs = (file) => {{
      const abs = path.join(pluginAbs, file);
      try {{
        if (fs.existsSync(abs)) return abs;
      }} catch (_) {{}}
      return null;
    }};

    let ok = 0;
    let missing = 0;
    let failed = 0;

    const loadOne = async (f) => {{
      const abs = resolveAbs(f.file);
      if (!abs) {{
        missing++;
        if (missing <= 5) console.warn(`[edenia] missing ${{f.file}}`);
        return;
      }}
      try {{
        const nodeBuf = fs.readFileSync(abs);
        const buf = nodeBuf.buffer.slice(
          nodeBuf.byteOffset,
          nodeBuf.byteOffset + nodeBuf.byteLength
        );
        const descriptors = {{
          style: "normal",
          weight: "normal",
          display: "swap",
        }};
        if (f.unicodeRange) descriptors.unicodeRange = f.unicodeRange;
        const face = new FontFace(f.family, buf, descriptors);
        await face.load();
        document.fonts.add(face);
        ok++;
      }} catch (err) {{
        failed++;
        if (failed <= 5) console.warn(`[edenia] fail ${{f.file}}`, err);
      }}
    }};

    console.info(`[edenia] loading ${{FACES.length}} faces from ${{relPlugin}}…`);
    for (let i = 0; i < FACES.length; i += BATCH) {{
      await Promise.all(FACES.slice(i, i + BATCH).map(loadOne));
    }}
    console.info(
      `[edenia] ready: ${{ok}} loaded, ${{missing}} missing, ${{failed}} failed`
    );
  }}
}};
"""
    (PLUGIN_DIR / "main.js").write_text(main_js, encoding="utf-8")
    print(f"  wrote plugin ({len(faces)} faces) -> {PLUGIN_DIR.relative_to(REPO_ROOT)}")


def install_to_vault(vault: Path) -> None:
    """Copy plugin + fonts only into ``.obsidian/plugins/obsidian-edenia``."""
    vault = vault.resolve()
    if not (vault / ".obsidian").is_dir():
        raise FileNotFoundError(f"not an Obsidian vault (no .obsidian): {vault}")
    plug = vault / ".obsidian" / "plugins" / PLUGIN_DIR_NAME
    plug.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN_DIR / "main.js", plug / "main.js")
    shutil.copy2(PLUGIN_DIR / "manifest.json", plug / "manifest.json")
    sync_woff2(plug / PLUGIN_ASSET)
    for stale in (
        vault / PLUGIN_ASSET,  # never keep fonts at Dropbox/vault root
        vault / "panfonts",
        vault / ".obsidian" / "plugins" / "obsidian-panfonts",
        vault / ".obsidian" / "plugins" / "edenia",  # old id/folder mismatch
    ):
        if stale.is_dir():
            shutil.rmtree(stale)
            print(f"  removed stale {stale}")
    # Keep community-plugins id in sync with manifest.id / folder name.
    cp = vault / ".obsidian" / "community-plugins.json"
    if cp.is_file():
        try:
            enabled = json.loads(cp.read_text(encoding="utf-8"))
            if isinstance(enabled, list):
                changed = False
                if "edenia" in enabled:
                    enabled = [x for x in enabled if x != "edenia"]
                    changed = True
                if PLUGIN_ID not in enabled:
                    enabled.append(PLUGIN_ID)
                    changed = True
                if changed:
                    cp.write_text(
                        json.dumps(enabled, indent=2) + "\n", encoding="utf-8"
                    )
                    print(f"  updated community-plugins.json -> enable {PLUGIN_ID}")
        except Exception as exc:
            print(f"  [!] could not patch community-plugins.json: {exc}")
    print(f"  installed plugin -> {plug}")


def _face_src(block: str, *, folder: str) -> str:
    """Rewrite src to raw → statically → jsDelivr mirrors (+ keep local ./)."""

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
        locals_: list[tuple[str, str]] = [
            (f"./{name}", "woff2"),
        ]
        ttf = name[:-6] + ".ttf" if name.endswith(".woff2") else None
        if ttf and f"./{ttf}" in chunk.replace("'", '"'):
            locals_.append((f"./{ttf}", "truetype"))
        return format_src_line(
            dist_rel(folder, name),
            fmt="woff2",
            local=tuple(locals_),
        )

    return re.sub(
        r"src:\s*(?:url\([^)]+\)(?:\s*format\([^)]+\))?\s*,?\s*)+;?",
        repl,
        block,
        flags=re.I | re.S,
    )


def transform_face_css(css: str, *, folder: str) -> str:
    """Keep per-bucket family names; rewrite src URLs to CDN chain."""
    out = to_cdn_url(css)

    def face_fix(m: re.Match[str]) -> str:
        block = m.group(0)
        block = _face_src(block, folder=folder)
        return _double_quotes(block)

    out = re.sub(r"@font-face\s*\{[^{}]*\}", face_fix, out, flags=re.S)
    return out.strip() + "\n"


def build_faces_block(
    hangul: str, yi: str, kana: str, cjk: str, *, bake: bool
) -> str:
    if bake:
        return "\n".join(
            [
                MARK_FACES_BEGIN,
                "/* Hangul + Yi + Kana + Edenia CJK: loaded by the edenia Obsidian plugin",
                f"   (Scripts/{PLUGIN_DIR_NAME}). Relative/data URLs do not work. */",
                MARK_FACES_END,
                "",
            ]
        )
    parts = [
        MARK_FACES_BEGIN,
        "/* Hangul + Yi + Kana + Pan-CJK via CDN chain (raw → statically → jsDelivr). */",
        "",
        transform_face_css(hangul, folder="hangul").rstrip(),
        "",
        transform_face_css(yi, folder="yi").rstrip(),
        "",
        transform_face_css(kana, folder="kana").rstrip(),
        "",
        transform_face_css(cjk, folder="cjk").rstrip(),
        "",
        MARK_FACES_END,
        "",
    ]
    return "\n".join(parts)


def _double_quotes(css: str) -> str:
    return css.replace("'", '"')


def build_stack_block(*, pancjk_families: list[str]) -> str:
    if not pancjk_families:
        raise ValueError("no edenia cjk families found in CSS")
    cjk = ", ".join([css_family_token(n) for n in pancjk_families] + [STACK_CJK_TAIL])
    stack = f"{STACK_LATIN}, {cjk}, {STACK_TAIL}"
    return "\n".join(
        [
            MARK_STACK_BEGIN,
            "/* Edenia CJK: 'edenia cjk h' before base so FE0B–F digraphs shape. */",
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
    n_unique = len(
        set(re.findall(r'["\']edenia cjk(?:\s+(?:qh|qv|[ht]))?["\']', text))
    )
    size_mb = theme_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {theme_path} (edenia cjk families~{n_unique}, {size_mb:.1f} MiB)")


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
            f"Build Scripts/{PLUGIN_DIR_NAME} plugin with local .woff2 "
            "(implies --local). Theme keeps the font stack only."
        ),
    )
    ap.add_argument(
        "--vault",
        type=Path,
        help=(
            "With --bake: install into this Obsidian vault "
            f"(plugin + {PLUGIN_ASSET}/ under .obsidian/plugins/{PLUGIN_DIR_NAME}/ only)"
        ),
    )
    ap.add_argument(
        "--also-private",
        "--private-only",
        dest="also_private",
        action="store_true",
        help="Also patch Scripts/private/theme.css",
    )
    args = ap.parse_args(argv)

    if args.bake:
        args.local = True

    themes: list[Path] = list(args.theme or [REPO_ROOT / "theme.css"])
    if args.also_private:
        themes.append(SCRIPT_DIR / "private" / "theme.css")
    # With --vault, also patch installed Obsidian themes that already carry
    # the auto pan markers (Sanctum / Origami / …).
    if args.vault:
        themes_dir = Path(args.vault) / ".obsidian" / "themes"
        if themes_dir.is_dir():
            for theme_css in sorted(themes_dir.glob("*/theme.css")):
                try:
                    text = theme_css.read_text(encoding="utf-8")
                except OSError:
                    continue
                if MARK_STACK_BEGIN in text or MARK_FACES_BEGIN in text:
                    themes.append(theme_css)

    print("Loading Edenia font CSS…")
    hangul = load_css("hangul", local=args.local)
    yi = load_css("yi", local=args.local)
    kana = load_css("kana", local=args.local)
    cjk = load_css("cjk", local=args.local)

    if args.bake:
        print("Baking Obsidian edenia plugin…")
        sync_woff2(PLUGIN_DIR / PLUGIN_ASSET)
        faces_meta = (
            collect_faces(hangul, folder="hangul")
            + collect_faces(yi, folder="yi")
            + collect_faces(kana, folder="kana")
            + collect_faces(cjk, folder="cjk")
        )
        write_plugin(faces_meta)
        if args.vault:
            install_to_vault(args.vault)
        for stale in (
            REPO_ROOT / "panfonts",
            SCRIPT_DIR / "private" / "panfonts",
            REPO_ROOT / PLUGIN_ASSET,
            SCRIPT_DIR / "private" / PLUGIN_ASSET,
        ):
            if stale.is_dir():
                shutil.rmtree(stale)
                print(f"  removed stale {stale.relative_to(REPO_ROOT)}")

    pancjk_families = pancjk_stack_families(cjk)
    print(f"  edenia cjk stack families: {pancjk_families}")
    faces = build_faces_block(hangul, yi, kana, cjk, bake=args.bake)
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
        "text font (cached stacks may still list a lone shared 'edenia cjk')."
    )
    if args.bake and not args.vault:
        print(
            f"Install: python Scripts/update_obsidian_theme_fonts.py --bake "
            f"--vault <vault-root>"
        )
    elif args.bake:
        print(
            "Reload Obsidian; DevTools console should show "
            f"[edenia] ready: ~{len(faces_meta)} loaded (with unicodeRange)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
