#!/usr/bin/env python3
"""
Export each glyph from font files as SVGs named after their code points;
fallback to glyph name if codepoint is missing.
Extracted SVG path data is simplified to use relative commands where possible.
Usage: Adjust IN_DIR and OUT_DIR as needed, then run the script.
Extracted SVGs are zipped and original directories are removed thereafter.
"""

import concurrent.futures
import json
import os
import shutil
from typing import Dict, List

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

# ---------- Directories ----------

IN_DIR = r"c://users/admin/fonts/scripts/src"
OUT_DIR = r"c://users/admin/fonts/scripts/dist"

# ---------- Font Helpers ----------


def list_input_fonts(in_dir: str) -> List[str]:
    """List all font files in the input directory."""
    return sorted(
        [
            os.path.join(in_dir, fn)
            for fn in os.listdir(in_dir)
            if fn.lower().endswith((".ttf", ".otf", ".ttc"))
        ],
        key=lambda p: os.path.basename(p).lower(),
    )


def font_cmap(tt: TTFont) -> Dict[int, str]:
    """Build a Unicode codepoint -> glyph name map."""
    cmap = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    return cmap


# ---------- SVG Export ----------


# find shortest curve data using relative instead of absolute, and use all drawing types wherever possible (h, v, l, q, t, c, s, a)
def simplify_path(path_data: str) -> str:
    import re

    # Regex to split path into commands and their parameters
    commands = re.findall(
        r"([MmZzLlHhVvCcSsQqTtAa])([^MmZzLlHhVvCcSsQqTtAa]*)", path_data
    )
    result = []
    current_x, current_y = 0, 0
    start_x, start_y = 0, 0
    last_cx, last_cy = None, None  # For smooth commands

    for cmd, params in commands:
        params = params.strip()
        coords = re.findall(r"[+-]?\d*\.?\d+", params)

        match cmd:
            case "M" | "m":
                # Move to - keep first as absolute, others as relative
                if not result:  # First command
                    current_x = float(coords[0])
                    current_y = float(coords[1])
                    start_x, start_y = current_x, current_y
                    result.append(f"M {current_x} {current_y}")
                else:
                    dx = float(coords[0]) - current_x
                    dy = float(coords[1]) - current_y
                    current_x += dx
                    current_y += dy
                    start_x, start_y = current_x, current_y
                    result.append(f"m {dx} {dy}")
                # Implicit lines
                for i in range(2, len(coords), 2):
                    x = float(coords[i]) if cmd == "M" else current_x + float(coords[i])
                    y = (
                        float(coords[i + 1])
                        if cmd == "M"
                        else current_y + float(coords[i + 1])
                    )
                    dx = x - current_x
                    dy = y - current_y
                    match (dx == 0, dy == 0):
                        case (True, _):
                            result.append(f"v {dy}")
                        case (_, True):
                            result.append(f"h {dx}")
                        case _:
                            result.append(f"l {dx} {dy}")
                    current_x, current_y = x, y
            case "L" | "l":
                for i in range(0, len(coords), 2):
                    x = float(coords[i]) if cmd == "L" else current_x + float(coords[i])
                    y = (
                        float(coords[i + 1])
                        if cmd == "L"
                        else current_y + float(coords[i + 1])
                    )
                    dx = x - current_x
                    dy = y - current_y
                    match (dx == 0, dy == 0):
                        case (True, _):
                            result.append(f"v {dy}")
                        case (_, True):
                            result.append(f"h {dx}")
                        case _:
                            result.append(f"l {dx} {dy}")
                    current_x, current_y = x, y
            case "H" | "h":
                for val in coords:
                    match cmd:
                        case "H":
                            x = float(val)
                            dx = x - current_x
                        case _:
                            dx = float(val)
                            x = current_x + dx
                    result.append(f"h {dx}")
                    current_x = x
            case "V" | "v":
                for val in coords:
                    match cmd:
                        case "V":
                            y = float(val)
                            dy = y - current_y
                        case _:
                            dy = float(val)
                            y = current_y + dy
                    result.append(f"v {dy}")
                    current_y = y
            case "C" | "c":
                for i in range(0, len(coords), 6):
                    if cmd == "C":
                        c1x = float(coords[i])
                        c1y = float(coords[i + 1])
                        c2x = float(coords[i + 2])
                        c2y = float(coords[i + 3])
                        x = float(coords[i + 4])
                        y = float(coords[i + 5])
                    else:
                        c1x = current_x + float(coords[i])
                        c1y = current_y + float(coords[i + 1])
                        c2x = current_x + float(coords[i + 2])
                        c2y = current_y + float(coords[i + 3])
                        x = current_x + float(coords[i + 4])
                        y = current_y + float(coords[i + 5])
                    dc1x = c1x - current_x
                    dc1y = c1y - current_y
                    dc2x = c2x - current_x
                    dc2y = c2y - current_y
                    dx = x - current_x
                    dy = y - current_y
                    result.append(f"c {dc1x} {dc1y} {dc2x} {dc2y} {dx} {dy}")
                    last_cx, last_cy = c2x, c2y  # For smooth
                    current_x, current_y = x, y
            case "S" | "s":
                for i in range(0, len(coords), 4):
                    if cmd == "S":
                        c2x = float(coords[i])
                        c2y = float(coords[i + 1])
                        x = float(coords[i + 2])
                        y = float(coords[i + 3])
                    else:
                        c2x = current_x + float(coords[i])
                        c2y = current_y + float(coords[i + 1])
                        x = current_x + float(coords[i + 2])
                        y = current_y + float(coords[i + 3])
                    if last_cx is not None:
                        c1x = 2 * current_x - last_cx
                        c1y = 2 * current_y - last_cy
                    else:
                        c1x = current_x
                        c1y = current_y
                    dc1x = c1x - current_x
                    dc1y = c1y - current_y
                    dc2x = c2x - current_x
                    dc2y = c2y - current_y
                    dx = x - current_x
                    dy = y - current_y
                    result.append(f"s {dc2x} {dc2y} {dx} {dy}")
                    last_cx, last_cy = c2x, c2y
                    current_x, current_y = x, y
            case "Q" | "q":
                for i in range(0, len(coords), 4):
                    if cmd == "Q":
                        cx = float(coords[i])
                        cy = float(coords[i + 1])
                        x = float(coords[i + 2])
                        y = float(coords[i + 3])
                    else:
                        cx = current_x + float(coords[i])
                        cy = current_y + float(coords[i + 1])
                        x = current_x + float(coords[i + 2])
                        y = current_y + float(coords[i + 3])
                    dcx = cx - current_x
                    dcy = cy - current_y
                    dx = x - current_x
                    dy = y - current_y
                    result.append(f"q {dcx} {dcy} {dx} {dy}")
                    last_cx, last_cy = cx, cy
                    current_x, current_y = x, y
            case "T" | "t":
                for i in range(0, len(coords), 2):
                    if cmd == "T":
                        x = float(coords[i])
                        y = float(coords[i + 1])
                    else:
                        x = current_x + float(coords[i])
                        y = current_y + float(coords[i + 1])
                    if last_cx is not None:
                        cx = 2 * current_x - last_cx
                        cy = 2 * current_y - last_cy
                    else:
                        cx = current_x
                        cy = current_y
                    dcx = cx - current_x
                    dcy = cy - current_y
                    dx = x - current_x
                    dy = y - current_y
                    result.append(f"t {dx} {dy}")
                    last_cx, last_cy = cx, cy
                    current_x, current_y = x, y
            case "A" | "a":
                for i in range(0, len(coords), 7):
                    if cmd == "A":
                        rx = float(coords[i])
                        ry = float(coords[i + 1])
                        x_axis_rotation = float(coords[i + 2])
                        large_arc_flag = coords[i + 3]
                        sweep_flag = coords[i + 4]
                        x = float(coords[i + 5])
                        y = float(coords[i + 6])
                    else:
                        rx = float(coords[i])
                        ry = float(coords[i + 1])
                        x_axis_rotation = float(coords[i + 2])
                        large_arc_flag = coords[i + 3]
                        sweep_flag = coords[i + 4]
                        x = current_x + float(coords[i + 5])
                        y = current_y + float(coords[i + 6])
                    dx = x - current_x
                    dy = y - current_y
                    result.append(
                        f"a {rx} {ry} {x_axis_rotation} {large_arc_flag} {sweep_flag} {dx} {dy}"
                    )
                    current_x, current_y = x, y
            case "Z" | "z":
                result.append("z")
                current_x, current_y = start_x, start_y
            case _:
                result.append(cmd + params)
    return "".join(result)


def extract_glyphs_as_svgs(
    font_path: str,
    out_dir: str,
    overwrite: bool = False,
    manifest: Dict[int, str] = None,
) -> None:
    """Export glyphs from a font file into individual SVGs."""
    font_name = os.path.basename(font_path)
    print(f"Processing {font_name}...")

    try:
        tt = TTFont(font_path)
        glyph_set = tt.getGlyphSet()
        cmap = font_cmap(tt)
        reverse_cmap = {v: k for k, v in cmap.items()}
        units_per_em = tt["head"].unitsPerEm
        ascent = int(getattr(tt["hhea"], "ascent", units_per_em))

        os.makedirs(out_dir, exist_ok=True)

        for glyph_name in glyph_set.keys():
            # Determine codepoint or fallback
            codepoint = reverse_cmap.get(glyph_name)
            if codepoint is not None:
                file_name = f"U+{codepoint:04X}.svg"
            else:
                file_name = f"{glyph_name}.svg"

            out_path = os.path.join(out_dir, file_name)

            if not overwrite and os.path.exists(out_path):
                continue  # Skip existing

            try:
                pen = SVGPathPen(glyph_set)
                tpen = TransformPen(pen, (1, 0, 0, -1, 0, ascent))
                glyph_set[glyph_name].draw(tpen)
                path_data = pen.getCommands()
                path_data = simplify_path(path_data)

                svg = (
                    f'<svg xmlns="http://www.w3.org/2000/svg" '
                    f'width="{units_per_em}" height="{units_per_em}" '
                    f'viewBox="0 0 {units_per_em} {units_per_em}">\n'
                    f'  <path d="{path_data}" fill="black"/>\n'
                    f"</svg>"
                )
            except Exception as e:
                print(f"[!] Failed to draw {glyph_name}: {e}")
                svg = (
                    f'<svg xmlns="http://www.w3.org/2000/svg" '
                    f'width="{units_per_em}" height="{units_per_em}" '
                    f'viewBox="0 0 {units_per_em} {units_per_em}"></svg>'
                )

            with open(out_path, "w", encoding="utf-8") as f:
                f.write(svg)
                print(f"  Wrote {out_path}")

            if manifest is not None and codepoint is not None:
                manifest[codepoint] = font_name

    finally:
        try:
            tt.close()
        except Exception:
            pass


def process_font(font_path: str, manifest: Dict[int, str]) -> None:
    font_name = os.path.splitext(os.path.basename(font_path))[0]
    output_dir = os.path.join(OUT_DIR, f"{font_name}_svgs")
    extract_glyphs_as_svgs(font_path, output_dir, overwrite=False, manifest=manifest)
    # Zip the SVGs and remove the directory
    print(f"Zipping SVGs for {font_name}...")
    shutil.make_archive(output_dir, "zip", output_dir)
    print(f"Removing temporary directory {output_dir}...")
    shutil.rmtree(output_dir)


# ---------- Main ----------


def main():
    input_fonts = list_input_fonts(IN_DIR)
    manifest = {}

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(process_font, font_path, manifest)
            for font_path in input_fonts
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    # Optionally write manifest
    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nManifest written to {manifest_path}")


if __name__ == "__main__":
    main()
