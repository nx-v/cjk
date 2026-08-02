#!/usr/bin/env python3
"""
Build GlyphWiki PUA ligature fonts.

Resolves the GlyphWiki dump under Scripts/dump/, assigns SPUA+BMP-PUA
ligatures (related code points limited to build_subfonts.CHAR_RANGES plus
CJK/Kangxi radicals), and writes one TTF/WOFF2 per SPUA marker into
Scripts/dist/glyphwiki/.

Each font is named after its SPUA marker (the first ligature code point),
e.g. F0000.ttf. Glyphs are rendered with the in-tree KAGE Serif renderer,
then flattened for TrueType.

After a font build, also writes ``glyphwiki.css`` (@font-face) and inserts
``glyphwiki …`` families into ``dist/subfonts/fontlist.css`` immediately
after the pancjk pigeonhole stack.

Examples:
  python Scripts/build_glyphwiki.py
  python Scripts/build_glyphwiki.py --limit 64 --font-markers 1
  python Scripts/build_glyphwiki.py --from-resolved --no-mirrors -j 8
  python Scripts/build_glyphwiki.py --parallel -j 8 --no-mirrors
  python Scripts/build_glyphwiki.py --cmap-only
  python Scripts/build_glyphwiki.py --from-resolved --cmap-only
  python Scripts/build_glyphwiki.py --cmap-only --no-filters
  python Scripts/build_glyphwiki.py --css-only
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Scripts.kage.extract_glyphwiki import (  # noqa: E402
    CMAP_PATH,
    DATA_DIR,
    DUMP_PATH,
    FONT_DIR,
    RESOLVED_PATH,
    main as extract_main,
)
from Scripts.kage.mapping import BMP_PUA_END, BMP_PUA_START  # noqa: E402

SUBFONTS_DIR = SCRIPT_DIR / "dist" / "subfonts"
FONTLIST_CSS = SUBFONTS_DIR / "fontlist.css"
GLYPHWIKI_CSS = FONT_DIR / "glyphwiki.css"
CSS_FONT_URL_BASE = (
    "https://raw.githubusercontent.com/nexovolta/fonts/main/Scripts/dist/glyphwiki"
)
CSS_FAMILY_PREFIX = "glyphwiki"

# Strip previously injected glyphwiki lines (idempotent re-runs).
_GLYPHWIKI_STACK_LINE = re.compile(r"\n[ \t]*'glyphwiki [^']+',")
# Last pancjk entry in each stack sits immediately before the fallback fonts.
_PANCJK_BEFORE_FALLBACK = re.compile(
    r"('pancjk [^']+',)(\n[ \t]*Malgun Gothic)",
)


def discover_marker_hexes(font_dir: Path = FONT_DIR) -> list[str]:
    """Marker hex ids that have a built woff2 (else ttf), sorted numerically."""
    if not font_dir.is_dir():
        return []
    woff = {p.stem.upper() for p in font_dir.glob("*.woff2")}
    ttf = {p.stem.upper() for p in font_dir.glob("*.ttf")}
    stems = woff | ttf
    out: list[tuple[int, str]] = []
    for stem in stems:
        try:
            out.append((int(stem, 16), stem))
        except ValueError:
            continue
    out.sort(key=lambda t: t[0])
    return [stem for _cp, stem in out]


def write_glyphwiki_css(
    *,
    font_dir: Path = FONT_DIR,
    css_path: Path = GLYPHWIKI_CSS,
    fontlist_path: Path = FONTLIST_CSS,
    markers: list[str] | None = None,
) -> list[str]:
    """Write glyphwiki.css and insert families into fontlist.css after pancjk.

    Returns the ``glyphwiki …`` family names that were written.
    """
    hex_ids = markers if markers is not None else discover_marker_hexes(font_dir)
    if not hex_ids:
        print(
            f"No GlyphWiki fonts in {font_dir}; skipping CSS update",
            file=sys.stderr,
        )
        return []

    families = [f"{CSS_FAMILY_PREFIX} {h}" for h in hex_ids]
    pua_range = f"U+{BMP_PUA_START:X}-{BMP_PUA_END:X}"

    lines: list[str] = [
        "/* Auto-generated GlyphWiki PUA ligature @font-face rules */",
        "",
    ]
    for hex_id in hex_ids:
        family = f"{CSS_FAMILY_PREFIX} {hex_id}"
        url = f"{CSS_FONT_URL_BASE}/{hex_id}.woff2"
        # Marker + full BMP PUA (liga needs both CPs from the same face).
        urange = f"U+{hex_id}, {pua_range}"
        lines.append("@font-face {")
        lines.append(f"  font-family: '{family}';")
        lines.append(f"  src: url('{url}') format('woff2');")
        lines.append("  font-weight: normal;")
        lines.append("  font-style: normal;")
        lines.append("  font-display: swap;")
        lines.append(f"  unicode-range: {urange};")
        lines.append("}")
        lines.append("")

    css_path.parent.mkdir(parents=True, exist_ok=True)
    css_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {css_path} ({len(hex_ids)} faces)")

    if not fontlist_path.is_file():
        print(
            f"Warning: {fontlist_path} missing; wrote @font-face only",
            file=sys.stderr,
        )
        return families

    text = fontlist_path.read_text(encoding="utf-8")
    text = _GLYPHWIKI_STACK_LINE.sub("", text)
    inject = "".join(f"\n    '{name}'," for name in families)
    updated, n = _PANCJK_BEFORE_FALLBACK.subn(rf"\1{inject}\2", text)
    if n == 0:
        print(
            f"Warning: could not find pancjk→Malgun insertion point in "
            f"{fontlist_path}; left stack unchanged",
            file=sys.stderr,
        )
        return families
    fontlist_path.write_text(updated, encoding="utf-8")
    print(
        f"Updated {fontlist_path} "
        f"(+{len(families)} glyphwiki families in {n} stack(s))"
    )
    return families


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build GlyphWiki PUA ligature fonts (32k glyphs per SPUA marker)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only pack the first N filtered glyphs (smoke test; 0 = all)",
    )
    p.add_argument(
        "--font-markers",
        type=int,
        default=0,
        help="Only build the first N marker TTF files (0 = all ~192)",
    )
    p.add_argument(
        "--from-resolved",
        action="store_true",
        help=(
            "Skip dump load/resolve; build from existing "
            f"{CMAP_PATH.name} + {RESOLVED_PATH.name}"
        ),
    )
    p.add_argument(
        "--no-mirrors",
        action="store_true",
        help="Omit D4 variant outlines and rlig (identity forms only; 12800 glyphs/file)",
    )
    p.add_argument(
        "--curve-fit",
        action="store_true",
        help=(
            "Schneider-fit long polygonal stroke ribbons to cubics "
            "(default: keep renderer polygons)"
        ),
    )
    p.add_argument(
        "--no-filters",
        action="store_true",
        help=(
            "Skip related-CP, overlay/name, empty, and duplicate filters; "
            "still drop full-frame aliases whose target is also packed"
        ),
    )
    p.add_argument(
        "--parallel",
        action="store_true",
        help=(
            "Overlap per-marker resolve with font builds and build multiple "
            "fonts at once (default workers = CPU count unless --jobs set)"
        ),
    )
    p.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=0,
        help=(
            "Worker processes for font builds (0 = CPU count with --parallel, "
            "else serial). Implies parallel builds when > 1."
        ),
    )
    p.add_argument(
        "--cmap-only",
        action="store_true",
        help="Write ligature cmap JSON only (no stroke resolve / fonts)",
    )
    p.add_argument(
        "--skip-fonts",
        action="store_true",
        help="Resolve JSON under Scripts/data/ but skip TTF/WOFF2 output",
    )
    p.add_argument(
        "--skip-newest-fallback",
        action="store_true",
        help="Do not gap-fill from dump_newest_only.txt",
    )
    p.add_argument(
        "--css-only",
        action="store_true",
        help=(
            "Only write glyphwiki.css and patch fontlist.css from existing "
            "dist/glyphwiki fonts (no dump/resolve/build)"
        ),
    )
    p.add_argument(
        "--skip-css",
        action="store_true",
        help="Do not update glyphwiki.css / fontlist.css after a font build",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.css_only:
        write_glyphwiki_css()
        return 0

    print("GlyphWiki font build")
    print(f"  dump:   {DUMP_PATH}")
    print(f"  data:   {DATA_DIR}")
    print(f"  fonts:  {FONT_DIR}")
    if args.parallel or args.jobs:
        jobs = args.jobs if args.jobs > 0 else max(1, os.cpu_count() or 4)
        print(
            f"  jobs:   {jobs}" + (" (parallel)" if args.parallel or args.jobs else "")
        )
    print()

    if args.from_resolved:
        if not CMAP_PATH.is_file() or not RESOLVED_PATH.is_file():
            print(
                f"Fatal: --from-resolved needs {CMAP_PATH} and {RESOLVED_PATH}",
                file=sys.stderr,
            )
            return 1
    elif not DUMP_PATH.is_file():
        print(f"Fatal: missing dump file {DUMP_PATH}", file=sys.stderr)
        print(
            "Place dump_all_versions.txt under Scripts/dump/ first.",
            file=sys.stderr,
        )
        return 1

    forwarded: list[str] = []
    if args.limit:
        forwarded += ["--limit", str(args.limit)]
    if args.font_markers:
        forwarded += ["--font-markers", str(args.font_markers)]
    if args.from_resolved:
        forwarded.append("--from-resolved")
    if args.no_mirrors:
        forwarded.append("--no-mirrors")
    if args.curve_fit:
        forwarded.append("--curve-fit")
    if args.no_filters:
        forwarded.append("--no-filters")
    if args.parallel:
        forwarded.append("--parallel")
    if args.jobs:
        forwarded += ["--jobs", str(args.jobs)]
    if args.cmap_only:
        forwarded.append("--cmap-only")
    if args.skip_fonts:
        forwarded.append("--skip-fonts")
    if args.skip_newest_fallback:
        forwarded.append("--skip-newest-fallback")

    rc = extract_main(forwarded)
    if (
        rc == 0
        and not args.skip_css
        and not args.cmap_only
        and not args.skip_fonts
    ):
        write_glyphwiki_css()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
