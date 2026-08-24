"""Rewrite GitHub-hosted font urls in theme.css to multi-CDN fallback chains.

Covers nexovolta/fonts and any other `raw.githubusercontent.com` /
`cdn.jsdelivr.net/gh/…` / `cdn.statically.io/gh/…` font `src:` lines.
Leaves googleapis / donation / forum links alone.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from cdn_fonts import format_github_src, format_src_line, remote_urls

THEME = Path(__file__).resolve().parent.parent / "theme.css"

_SRC = re.compile(
    r"src:\s*((?:url\([^)]+\)(?:\s*format\([^)]+\))?\s*,?\s*)+);",
    re.I | re.S,
)

# Parse any GitHub font mirror into (owner/repo, ref, path).
_GH_URL = re.compile(
    r"https://(?:"
    r"raw\.githubusercontent\.com/(?P<raw_repo>[^/]+/[^/]+)/(?P<raw_ref>[^/]+)/(?P<raw_path>.+)"
    r"|"
    r"(?:cdn\.statically\.io|(?:cdn|fastly|gcore)\.jsdelivr\.net)/gh/"
    r"(?P<gh_repo>[^/@]+)(?:/(?P<gh_repo2>[^/@]+))?@(?P<gh_ref>[^/]+)/(?P<gh_path>.+)"
    r")",
    re.I,
)


def _fmt_for(path: str) -> str:
    lower = path.rsplit(".", 1)[-1].lower().split("?", 1)[0]
    match lower:
        case "woff2":
            return "woff2"
        case "woff":
            return "woff"
        case "otf":
            return "opentype"
        case "ttf" | "ttc":
            return "truetype"
        case _:
            return "truetype"


def _parse_github(url: str) -> tuple[str, str, str] | None:
    m = _GH_URL.match(url.rstrip("/"))
    if not m:
        return None
    if m.group("raw_repo"):
        return m.group("raw_repo"), m.group("raw_ref"), m.group("raw_path")
    repo = m.group("gh_repo")
    repo2 = m.group("gh_repo2")
    if repo2:
        repo = f"{repo}/{repo2}"
    return repo, m.group("gh_ref"), m.group("gh_path")


def _locals_from_chunk(chunk: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for loc in re.findall(
        r"url\((['\"])(\./[^'\"]+)\1\)(?:\s*format\((['\"])([^'\"]+)\3\))?",
        chunk,
    ):
        url = loc[1]
        fmt = loc[3] if loc[3] else _fmt_for(url)
        out.append((url, fmt))
    return out


def rewrite_src(match: re.Match[str]) -> str:
    chunk = match.group(0)
    # First GitHub font URL in the src list wins (already-expanded chains
    # re-parse to the same owner/repo/ref/path).
    urls = re.findall(r"url\((['\"])(https:[^'\"]+)\1\)", chunk)
    parsed = None
    for _q, url in urls:
        parsed = _parse_github(url)
        if parsed:
            break
    if not parsed:
        return chunk
    owner_repo, ref, path = parsed
    locals_ = _locals_from_chunk(chunk)
    if owner_repo == "nexovolta/fonts":
        return format_src_line(path, fmt=_fmt_for(path), local=tuple(locals_) or None)
    return format_github_src(
        owner_repo,
        ref,
        path,
        fmt=_fmt_for(path),
        local=tuple(locals_) or None,
    )


def main() -> int:
    text = THEME.read_text(encoding="utf-8")
    new, n = _SRC.subn(rewrite_src, text)
    THEME.write_text(new, encoding="utf-8")

    # Count remaining single-host GitHub fonts (should be ~0 for src faces).
    leftover = []
    for m in _SRC.finditer(new):
        chunk = m.group(0)
        if "raw.githubusercontent.com" in chunk and "cdn.statically.io" not in chunk:
            leftover.append(chunk[:120])
        elif (
            "cdn.jsdelivr.net/gh/" in chunk
            and "raw.githubusercontent.com" not in chunk
            and "nexovolta" not in chunk
        ):
            leftover.append(chunk[:120])

    print(f"rewrote theme.css src blocks~{n}")
    print("nexovolta raw refs:", new.count("raw.githubusercontent.com/nexovolta/fonts"))
    print(
        "other raw refs:",
        len(re.findall(r"raw\.githubusercontent\.com/(?!nexovolta/fonts)", new)),
    )
    print("leftover single-host samples:", len(leftover))
    for s in leftover[:8]:
        print(" ", s.replace("\n", " "))
    print("sample nexo:", remote_urls("Nexsevka/TTF/Nexsevka-Regular.ttf")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
