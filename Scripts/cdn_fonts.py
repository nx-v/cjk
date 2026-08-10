"""CDN URL chains for GitHub-hosted fonts (spread load across mirrors).

Order: GitHub raw first, then statically / jsDelivr mirrors, then local.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

# Default repo used by build scripts (nexovolta/fonts @ main).
REPO = "nexovolta/fonts"
REF = "main"


def github_mirror_urls(owner_repo: str, ref: str, path: str) -> List[str]:
    """CDN fallbacks for ``owner/repo`` at ``ref`` with repo-relative ``path``."""
    owner_repo = owner_repo.strip("/")
    path = path.lstrip("/")
    ref = ref.strip("/")
    return [
        f"https://raw.githubusercontent.com/{owner_repo}/{ref}/{path}",
        f"https://cdn.statically.io/gh/{owner_repo}@{ref}/{path}",
        f"https://cdn.jsdelivr.net/gh/{owner_repo}@{ref}/{path}",
        f"https://fastly.jsdelivr.net/gh/{owner_repo}@{ref}/{path}",
        f"https://gcore.jsdelivr.net/gh/{owner_repo}@{ref}/{path}",
    ]


def remote_urls(repo_relpath: str) -> List[str]:
    """Absolute CDN URLs for a path inside nexovolta/fonts (no leading slash)."""
    return github_mirror_urls(REPO, REF, repo_relpath)


def format_src_urls(
    urls: Sequence[str],
    *,
    fmt: str,
    local: Optional[Sequence[Tuple[str, str]]] = None,
    indent: str = "",
) -> str:
    """Build a CSS ``src:`` line from an ordered URL list (+ optional locals)."""
    pad = indent
    cont = indent + (" " * 5 if indent else "     ")
    parts: List[str] = [f'url("{u}") format("{fmt}")' for u in urls]
    if local:
        for url, local_fmt in local:
            parts.append(f'url("{url}") format("{local_fmt}")')
    if len(parts) == 1:
        return f"{pad}src: {parts[0]};"
    lines = [f"{pad}src: {parts[0]},"]
    for p in parts[1:-1]:
        lines.append(f"{cont}{p},")
    lines.append(f"{cont}{parts[-1]};")
    return "\n".join(lines)


def format_src_line(
    repo_relpath: str,
    *,
    fmt: str = "woff2",
    local: Optional[Sequence[Tuple[str, str]]] = None,
    indent: str = "",
) -> str:
    """Build a CSS ``src:`` for a path inside nexovolta/fonts."""
    return format_src_urls(
        remote_urls(repo_relpath), fmt=fmt, local=local, indent=indent
    )


def format_github_src(
    owner_repo: str,
    ref: str,
    path: str,
    *,
    fmt: str = "woff2",
    local: Optional[Sequence[Tuple[str, str]]] = None,
    indent: str = "",
) -> str:
    """Build a CSS ``src:`` for any GitHub ``owner/repo@ref/path``."""
    return format_src_urls(
        github_mirror_urls(owner_repo, ref, path),
        fmt=fmt,
        local=local,
        indent=indent,
    )


def dist_rel(*parts: str) -> str:
    """``Scripts/dist/...`` path under nexovolta/fonts."""
    return "/".join(("Scripts", "dist", *parts))
