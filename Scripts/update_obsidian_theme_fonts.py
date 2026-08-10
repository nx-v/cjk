#!/usr/bin/env python3
"""Refresh Obsidian theme.css pan-font blocks from GitHub CDN or local dist.

Obsidian needs a *single* ``pancjk`` family with many ``@font-face`` +
``unicode-range`` slices (Google Fonts style). Digraph galleries keep
per-bucket ``'pancjk XX'`` names in ``pancjk.css``; this script is for the
theme only.

Font files are loaded from **jsDelivr** (statically.io throttles large
parallel ``@font-face`` loads to minutes per file in Obsidian).

Usage::

    python Scripts/update_obsidian_theme_fonts.py
    python Scripts/update_obsidian_theme_fonts.py --local
    python Scripts/update_obsidian_theme_fonts.py --theme path/to/theme.css

Markers (inserted on first run if missing)::

    /* === BEGIN auto pan fonts (update_obsidian_theme_fonts.py) === */
    /* === END auto pan fonts === */
    /* === BEGIN auto pan font stack (update_obsidian_theme_fonts.py) === */
    /* === END auto pan font stack === */
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

CDN_GH = "https://cdn.jsdelivr.net/gh/nexovolta/fonts@main/Scripts/dist"
CDN_BASE = CDN_GH
# Prefer jsDelivr for CSS; fall back to statically then local.
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
        "https://cdn.statically.io/gh/nexovolta/fonts@main/"
        "Scripts/dist/yi/panyi.css"
    ),
    "cjk": (
        "https://cdn.statically.io/gh/nexovolta/fonts@main/"
        "Scripts/dist/subfonts/pancjk.css"
    ),
}
LOCAL_CSS = {
    "hangul": SCRIPT_DIR / "dist" / "hangul" / "panhangul.css",
    "yi": SCRIPT_DIR / "dist" / "yi" / "panyi.css",
    "cjk": SCRIPT_DIR / "dist" / "subfonts" / "pancjk.css",
}

# Rewrite legacy CDN / raw GitHub dist URLs → jsDelivr @main
_JSDELIVR_FONT = re.compile(
    r"https://(?:cdn|fastly)\.jsdelivr\.net/gh/nexovolta/fonts@[^/]+/" r"Scripts/dist/",
    re.I,
)
_RAW_GH_FONT = re.compile(
    r"https://raw\.githubusercontent\.com/nexovolta/fonts/[^/]+/" r"Scripts/dist/",
    re.I,
)
_STATICALLY_FONT = re.compile(
    r"https://cdn\.statically\.io/gh/nexovolta/fonts(?:@main|/main)/"
    r"Scripts/dist/",
    re.I,
)

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
STACK_CJK = "pancjk, panyi, panhangul, panhanguls, Plangothic P1, Plangothic P2"
STACK_TAIL = "monospace"


def fetch_text(url: str, timeout: float = 60.0) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "update-obsidian-theme-fonts/1"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def to_cdn_url(url: str) -> str:
    """Map raw GitHub / statically / jsDelivr URLs onto jsDelivr @main."""
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


def _cdn_only_src(block: str, *, folder: str) -> str:
    """Keep one absolute jsDelivr src (drop ./ and legacy CDNs)."""

    def repl(m: re.Match[str]) -> str:
        chunk = m.group(0)
        urls = re.findall(r"url\((['\"])(https:[^'\"]+)\1\)", chunk)
        if urls:
            pick = to_cdn_url(urls[0][1])
        else:
            rel = re.findall(r"url\((['\"])\./([^'\"]+\.woff2)\1\)", chunk)
            if not rel:
                return chunk
            pick = f"{CDN_GH}/{folder}/{rel[0][1]}"
        return f'src: url("{pick}") format("woff2");'

    return re.sub(
        r"src:\s*(?:url\([^)]+\)(?:\s*format\([^)]+\))?\s*,?\s*)+;?",
        repl,
        block,
        flags=re.I | re.S,
    )


def transform_face_css(css: str, *, shared_pancjk: bool, folder: str) -> str:
    """CDN-only jsDelivr src; optional merge of pancjk XX → pancjk."""
    out = to_cdn_url(css)
    if shared_pancjk:
        out = re.sub(
            r"font-family:\s*['\"]pancjk\s+[0-9A-Fa-f]+['\"]",
            'font-family: "pancjk"',
            out,
        )
        out = re.sub(
            r"/\* One family per bucket.*?\*/",
            '/* Obsidian: one family name "pancjk", unicode-range per bucket. */',
            out,
            count=1,
            flags=re.S,
        )

    def face_fix(m: re.Match[str]) -> str:
        block = m.group(0)
        block = _cdn_only_src(block, folder=folder)
        block = _double_quotes(block)
        return block

    out = re.sub(r"@font-face\s*\{[^{}]*\}", face_fix, out, flags=re.S)
    return out.strip() + "\n"


def build_faces_block(hangul: str, yi: str, cjk: str) -> str:
    parts = [
        MARK_FACES_BEGIN,
        "/* Hangul + Yi + Pan-CJK via jsDelivr (shared pancjk family). */",
        "",
        transform_face_css(hangul, shared_pancjk=False, folder="hangul").rstrip(),
        "",
        transform_face_css(yi, shared_pancjk=False, folder="yi").rstrip(),
        "",
        transform_face_css(cjk, shared_pancjk=True, folder="subfonts").rstrip(),
        "",
        MARK_FACES_END,
        "",
    ]
    return "\n".join(parts)


def _double_quotes(css: str) -> str:
    return css.replace("'", '"')


def build_stack_block() -> str:
    # Latin first for UI; pancjk before Plangothic. Direct --font-text with
    # !important beats Appearance / Style Settings caches that still list
    # obsolete 'pancjk XX' family names (those no longer have @font-face).
    stack = f"{STACK_LATIN}, {STACK_CJK}, {STACK_TAIL}"
    return "\n".join(
        [
            MARK_STACK_BEGIN,
            "/* Force --font-text: Style Settings / Appearance often keep a cached",
            "   stack of dead 'pancjk XX' names after the shared-family rename. */",
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
    """First-run: replace hangul→end of pancjk faces before fontlist body."""
    # From hangul auto comment through last pancjk @font-face, before fontlist.
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
    # Collapse any remaining "pancjk XX" lists inside --font-*-theme
    collapsed = re.sub(
        r'(["\']pancjk\s+[0-9A-Fa-f]+["\']\s*,\s*)+["\']pancjk\s+[0-9A-Fa-f]+["\']',
        "pancjk",
        text,
    )
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
    n_faces = len(re.findall(r'font-family:\s*"pancjk"', text))
    n_bucket_faces = len(re.findall(r'font-family:\s*"pancjk\s+[0-9A-Fa-f]+"', text))
    n_bucket_stack = len(re.findall(r'["\']pancjk\s+[0-9A-Fa-f]+["\']', text))
    print(
        f"Wrote {theme_path} "
        f"(shared pancjk @font-face={n_faces}, "
        f"bucket @font-face leftover={n_bucket_faces}, "
        f"bucket name refs={n_bucket_stack})"
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
        "--also-private",
        action="store_true",
        help="Also patch Scripts/private/theme.css",
    )
    args = ap.parse_args(argv)

    themes: list[Path] = list(args.theme or [REPO_ROOT / "theme.css"])
    if args.also_private:
        themes.append(SCRIPT_DIR / "private" / "theme.css")

    print("Loading pan font CSS…")
    hangul = load_css("hangul", local=args.local)
    yi = load_css("yi", local=args.local)
    cjk = load_css("cjk", local=args.local)

    faces = build_faces_block(hangul, yi, cjk)
    stack = build_stack_block()
    n = len(re.findall(r"@font-face", faces))
    print(f"Built Obsidian face block ({n} @font-face)")

    for path in themes:
        if not path.is_file():
            print(f"[!] skip missing {path}", file=sys.stderr)
            continue
        patch_theme(path, faces, stack)
    print(
        "Note: if CJK still missing in Obsidian, reset Appearance / Style Settings "
        "text font (cached stacks may still list old 'pancjk XX' names)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
