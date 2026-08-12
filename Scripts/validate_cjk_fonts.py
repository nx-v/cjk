"""Validate panCJK woff2 for load-blocking issues (cmap/OTS/OpenType)."""

from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent / "dist" / "cjk"


def check(path: Path) -> tuple[str, list[str], str]:
    issues: list[str] = []
    meta = ""
    try:
        if path.stat().st_size < 100:
            return path.name, ["empty/tiny file"], meta
        t = TTFont(str(path))
    except Exception as e:
        return path.name, [f"open:{e}"], meta
    try:
        order_list = t.getGlyphOrder()
        n = len(order_list)
        order = set(order_list)
        marked = sum(1 for name in order if "_u16FF" in name)
        has_gsub = "GSUB" in t
        has_gpos = "GPOS" in t
        meta = f"glyphs={n} marked={marked} gsub={has_gsub} gpos={has_gpos}"
        if n > 65535:
            issues.append(f"glyph_count={n}>65535")
        if "cmap" not in t:
            issues.append("no cmap")
        else:
            for sub in t["cmap"].tables:
                if sub.format == 14 and not getattr(sub, "uvsDict", None):
                    issues.append("empty cmap fmt14")
        for req in (
            "head",
            "hhea",
            "maxp",
            "hmtx",
            "glyf",
            "loca",
            "name",
            "OS/2",
            "post",
        ):
            if req not in t:
                issues.append(f"missing {req}")
        for tag in ("GSUB", "GPOS", "GDEF"):
            if tag in t:
                try:
                    t[tag].compile(t)
                except Exception as e:
                    issues.append(f"{tag}.compile:{type(e).__name__}:{e}")
        glyf = t["glyf"]
        bad_comp = 0
        for name in order_list:
            g = glyf[name]
            if g.isComposite():
                for c in g.components:
                    if c.glyphName not in order:
                        bad_comp += 1
                        break
        if bad_comp:
            issues.append(f"broken_components={bad_comp}")
        # GSUB must exist for D4/squish/marks
        if marked and not has_gsub:
            issues.append("marked_without_GSUB")
    finally:
        t.close()
    return path.name, issues, meta


def main() -> int:
    files = sorted(
        p for p in ROOT.glob("*.woff2") if re.fullmatch(r"[0-9A-Fa-f]+", p.stem)
    )
    print(f"scanning {len(files)} woff2 under {ROOT}")
    bad: list[tuple[str, list[str], str]] = []
    ok = 0
    old_style = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(check, p) for p in files]
        for fut in as_completed(futs):
            name, issues, meta = fut.result()
            if issues:
                bad.append((name, issues, meta))
            else:
                ok += 1
                if "marked=0" in meta:
                    old_style += 1
    print(f"ok={ok} bad={len(bad)} old_no_marked={old_style}")
    for name, issues, meta in sorted(bad)[:50]:
        print(f"  FAIL {name}: {issues} | {meta}")
    if len(bad) > 50:
        print(f"  ... +{len(bad) - 50} more")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
