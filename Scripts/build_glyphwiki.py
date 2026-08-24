#!/usr/bin/env python3
"""
Build GlyphWiki PUA ligature fonts.

Resolves the GlyphWiki dump under Scripts/dump/, assigns SPUA+BMP-PUA
ligatures (related code points limited to build_cjk.CHAR_RANGES plus
CJK/Kangxi radicals), and writes TTF/WOFF2 per SPUA marker × KAGE style into
Scripts/dist/glyphwiki/{mincho,gothic,rounded}/.

Each font is named after its SPUA marker (the first ligature code point),
e.g. mincho/F0000.ttf. Styles default to all three; pass `--mincho`,
`--gothic`, and/or `--rounded` to build a subset.

Examples:
  python Scripts/build_glyphwiki.py
  python Scripts/build_glyphwiki.py --gothic --rounded
  python Scripts/build_glyphwiki.py --mincho --limit 64 --font-markers 1
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
from Scripts.shared_hinting import add_no_hint_argument  # noqa: E402


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
        "--css-only",
        action="store_true",
        help="Only regenerate pangw.css / fontlist.css from existing fonts",
    )
    p.add_argument(
        "--no-mirrors",
        action="store_true",
        help="Omit D4 variant outlines and rlig (identity forms only; 12800 glyphs/file)",
    )
    p.add_argument(
        "--mincho",
        action="store_true",
        help="Build mincho (serif); if none of the style flags are set, all three are built",
    )
    p.add_argument(
        "--gothic",
        action="store_true",
        help="Build gothic (sans); combine with --mincho/--rounded as needed",
    )
    p.add_argument(
        "--rounded",
        action="store_true",
        help="Build rounded; combine with --mincho/--gothic as needed",
    )
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument(
        "--ttf-only",
        action="store_true",
        help="Write TTF only (skip WOFF2)",
    )
    fmt.add_argument(
        "--woff2-only",
        action="store_true",
        help="Write WOFF2 only (drop intermediate TTF after compress)",
    )
    add_no_hint_argument(p)
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
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

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

    if args.css_only:
        return extract_main(["--css-only"])

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
    if args.css_only:
        forwarded.append("--css-only")
    if args.no_mirrors:
        forwarded.append("--no-mirrors")
    if args.mincho:
        forwarded.append("--mincho")
    if args.gothic:
        forwarded.append("--gothic")
    if args.rounded:
        forwarded.append("--rounded")
    if args.ttf_only:
        forwarded.append("--ttf-only")
    if args.woff2_only:
        forwarded.append("--woff2-only")
    if args.no_hint:
        forwarded.append("--no-hint")
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

    return extract_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
