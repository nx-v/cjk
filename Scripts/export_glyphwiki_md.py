#!/usr/bin/env python3
"""Export GlyphWiki font preview markdown files (PUA ligature sequences).

Reads `Scripts/data/glyphwiki-cmap.json` and writes one `.md` per SPUA
marker font (same basename as the TTF, e.g. `F0000.md`). Each file lists
the OpenType input sequences that render that font's glyphs ? identity
(`marker + PUA`) and D4 variants (`marker + PUA + VS02..VS08`) ? chunked
32 glyphs per line like `CJK Unified Ideographs.md`.

Examples:
  python Scripts/export_glyphwiki_md.py
  python Scripts/export_glyphwiki_md.py --no-mirrors -o Scripts/glyphwiki_md
  python Scripts/export_glyphwiki_md.py --markers 2
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Scripts.kage.mapping import (  # noqa: E402
    D4_MODES,
    MirrorVS,
    mappings_from_cmap,
)

DEFAULT_CMAP = SCRIPT_DIR / "data" / "glyphwiki-cmap.json"
DEFAULT_OUT = SCRIPT_DIR / "dist" / "glyphwiki"

# Glyphs per markdown line (same as the JS `.chunk(32)` exporter).
CHUNK_SIZE = 32

# (section heading, trailing VS code point or None for identity)
D4_SECTIONS: list[tuple[str, int | None]] = [
    (suffix or "Identity", None if suffix is None else MirrorVS.codepoint(mode))
    for mode, _rot, _fx, _fy, suffix in D4_MODES
]


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def ligature_sequence(marker: int, pua: int, vs: int | None = None) -> str:
    """Code-point string that GSUB maps to one outline in the marker font."""
    s = chr(marker) + chr(pua)
    if vs is not None:
        s += chr(vs)
    return s


def format_section(sequences: list[str]) -> str:
    """Join sequences into markdown lines (32 glyphs, trailing `  ` breaks)."""
    if not sequences:
        return ""
    lines: list[str] = []
    for chunk in chunked(sequences, CHUNK_SIZE):
        lines.append("".join(chunk) + "  ")
    return "\n".join(lines)


def markdown_for_marker(
    marker: int,
    pairs: list[tuple[int, str]],
    *,
    include_mirrors: bool = True,
) -> str:
    """Build markdown body for one marker font.

    `pairs` is `(pua, glyph_name)` sorted by PUA.
    """
    parts: list[str] = [f"# U+{marker:X}", ""]
    sections = D4_SECTIONS if include_mirrors else D4_SECTIONS[:1]
    for heading, vs in sections:
        seqs = [ligature_sequence(marker, pua, vs) for pua, _name in pairs]
        parts.append(f"# {heading}")
        parts.append("")
        body = format_section(seqs)
        if body:
            parts.append(body)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def load_cmap(path: Path) -> dict[str, list[int]]:
    with path.open(encoding="utf-8-sig") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise SystemExit(f"Fatal: expected object in {path}")
    return obj


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Write per-marker markdown previews of GlyphWiki PUA ligature "
            "sequences (identity + D4 variants)"
        ),
    )
    p.add_argument(
        "--cmap",
        type=Path,
        default=DEFAULT_CMAP,
        help=f"glyphwiki-cmap.json (default: {DEFAULT_CMAP})",
    )
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output directory (default: {DEFAULT_OUT})",
    )
    p.add_argument(
        "--no-mirrors",
        action="store_true",
        help="Only emit Identity sections (omit D4 variants)",
    )
    p.add_argument(
        "--markers",
        type=int,
        default=0,
        help="Only write the first N marker files (0 = all)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.cmap.is_file():
        print(f"Fatal: missing cmap {args.cmap}", file=sys.stderr)
        print("Run build_glyphwiki.py --cmap-only first.", file=sys.stderr)
        return 1

    print(f"Loading {args.cmap}...")
    cmap = load_cmap(args.cmap)
    mappings = mappings_from_cmap(cmap)
    by_marker: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for m in mappings:
        by_marker[m.marker].append((m.pua, m.name))

    markers = sorted(by_marker)
    if args.markers > 0:
        markers = markers[: args.markers]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    include_mirrors = not args.no_mirrors
    print(
        f"Writing {len(markers)} markdown file(s) -> {args.out_dir} "
        f"({'identity + D4' if include_mirrors else 'identity only'})"
    )

    for i, marker in enumerate(markers, 1):
        pairs = sorted(by_marker[marker], key=lambda t: t[0])
        text = markdown_for_marker(marker, pairs, include_mirrors=include_mirrors)
        out = args.out_dir / f"{marker:X}.md"
        out.write_text(text, encoding="utf-8")
        print(f"  [{i}/{len(markers)}] {out.name} ({len(pairs):,} glyphs)")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
