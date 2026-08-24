"""Sync Scripts/dist/<folder> into Scripts/obsidian-edenia/edenia/<folder>.

Used by build_cjk / build_hangul / build_yi / build_kana so the Obsidian plugin tree
stays current after each build. Layout matches update_obsidian_theme_fonts
`sync_woff2(PLUGIN_DIR / PLUGIN_ASSET)`.

CJK `.woff2` copies follow `edenia-cjk.css` (so `--base-only` / `--faces`
do not push unused segment files into the plugin). Yi/kana pigeonholes follow
their CSS the same way.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional, Set

from edenia_names import PLUGIN_ASSET, PLUGIN_DIR_NAME

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_EDENIA = SCRIPT_DIR / PLUGIN_DIR_NAME / PLUGIN_ASSET

_WOFF2_IN_CSS = re.compile(r"""['"](?:[^'"]*/)?([^'"]+\.woff2)['"]""")


def woff2_names_from_css_dir(src: Path) -> Optional[Set[str]]:
    """Basenames referenced by CSS under `src`, or `None` if none found."""
    names: Set[str] = set()
    for css in src.glob("*.css"):
        try:
            text = css.read_text(encoding="utf-8")
        except OSError:
            continue
        names.update(_WOFF2_IN_CSS.findall(text))
    return names or None


def sync_dist_to_plugin(folder: str, src_dir: str | Path | None = None) -> int:
    """Copy artifacts from dist/<folder> (or src_dir) into the plugin tree.

    Returns the number of files copied.
    """
    src = Path(src_dir) if src_dir is not None else SCRIPT_DIR / "dist" / folder
    if not src.is_dir():
        print(f"  [!] skip plugin sync: missing {src}", flush=True)
        return 0
    dst = PLUGIN_EDENIA / folder
    dst.mkdir(parents=True, exist_ok=True)
    allow = woff2_names_from_css_dir(src) if folder in ("cjk", "yi", "kana") else None
    n = 0
    for path in sorted(src.iterdir()):
        if not path.is_file():
            continue
        # Skip build leftovers / non-artifacts
        if path.name.startswith("tmp") or ".tmp." in path.name:
            continue
        suf = path.suffix.lower()
        if suf not in {".woff2", ".css"}:
            continue
        if suf == ".woff2" and allow is not None and path.name not in allow:
            continue
        shutil.copy2(path, dst / path.name)
        n += 1
    if allow is not None and dst.is_dir():
        for stale in dst.glob("*.woff2"):
            if stale.name not in allow:
                try:
                    stale.unlink()
                except OSError:
                    pass
    try:
        shown = dst.relative_to(SCRIPT_DIR.parent)
    except ValueError:
        shown = dst
    print(f"  synced {n} files -> {shown}", flush=True)
    return n


if __name__ == "__main__":
    total = 0
    for folder in ("hangul", "yi", "kana", "cjk"):
        total += sync_dist_to_plugin(folder)
    print(f"synced {total} files into {PLUGIN_EDENIA}")
