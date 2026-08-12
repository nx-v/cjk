"""Regenerate hangul/yi/cjk CSS CDN chains + rewrite theme.css."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from cdn_fonts import dist_rel, format_src_line

SCRIPT = Path(__file__).resolve().parent


def rewrite_dist_css(path: Path, folder: str) -> None:
    text = path.read_text(encoding="utf-8")

    def repl_face(m: re.Match[str]) -> str:
        block = m.group(0)
        fam = re.search(r"font-family:\s*'([^']+)'", block)
        if not fam:
            return block
        name = fam.group(1)
        wo = re.search(r"/([^/']+\.woff2)", block)
        fname = wo.group(1) if wo else f"{name}.woff2"
        stem = fname[:-6]
        src = format_src_line(
            dist_rel(folder, fname),
            fmt="woff2",
            local=(
                (f"./{fname}", "woff2"),
                (f"./{stem}.ttf", "truetype"),
            ),
        )
        return re.sub(
            r"src:\s*(?:url\([^)]+\)(?:\s*format\([^)]+\))?\s*,?\s*)+;?",
            src,
            block,
            count=1,
            flags=re.S,
        )

    new = re.sub(r"@font-face\s*\{[^{}]*\}", repl_face, text, flags=re.S)
    path.write_text(new, encoding="utf-8")
    print(
        f"rewrote {path.relative_to(SCRIPT)} raw={new.count('raw.githubusercontent')}"
    )


def main() -> int:
    subprocess.check_call(
        [sys.executable, str(SCRIPT / "build_cjk.py"), "--css-only"],
        cwd=str(SCRIPT),
    )
    rewrite_dist_css(SCRIPT / "dist" / "hangul" / "edenia-hangul.css", "hangul")
    rewrite_dist_css(SCRIPT / "dist" / "yi" / "edenia-yi.css", "yi")
    rewrite_dist_css(SCRIPT / "dist" / "kana" / "edenia-kana.css", "kana")
    # edenia-cjk already rewritten by --css-only
    subprocess.check_call([sys.executable, str(SCRIPT / "rewrite_theme_cdns.py")])
    sample = (SCRIPT / "dist" / "cjk" / "edenia-cjk.css").read_text(encoding="utf-8")
    print("--- edenia-cjk sample ---")
    print("\n".join(sample.splitlines()[5:22]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
