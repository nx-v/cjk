"""Sync Scripts/dist/<folder> into Scripts/obsidian-panfonts/panfonts/<folder>.

Used by build_cjk / build_hangul / build_yi so the Obsidian plugin tree
stays current after each build. Layout matches update_obsidian_theme_fonts
``sync_woff2(PLUGIN_DIR / "panfonts")``.
"""
from __future__ import annotations

import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_PANFONTS = SCRIPT_DIR / "obsidian-panfonts" / "panfonts"


def sync_dist_to_plugin(folder: str, src_dir: str | Path | None = None) -> int:
    """Copy every file from dist/<folder> (or src_dir) into the plugin tree.

    Returns the number of files copied.
    """
    src = Path(src_dir) if src_dir is not None else SCRIPT_DIR / "dist" / folder
    if not src.is_dir():
        print(f"  [!] skip plugin sync: missing {src}", flush=True)
        return 0
    dst = PLUGIN_PANFONTS / folder
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for path in sorted(src.iterdir()):
        if not path.is_file():
            continue
        # Skip build leftovers / non-artifacts
        if path.name.startswith("tmp") or ".tmp." in path.name:
            continue
        if path.suffix.lower() not in {".woff2", ".css"}:
            continue
        shutil.copy2(path, dst / path.name)
        n += 1
    try:
        shown = dst.relative_to(SCRIPT_DIR.parent)
    except ValueError:
        shown = dst
    print(f"  synced {n} files -> {shown}", flush=True)
    return n
