"""TrueType autohint helper via ``ttfautohint-py`` (fontTools ecosystem).

Install: ``pip install ttfautohint-py``

Wheels bundle a ``ttfautohint`` binary; no separate system install needed.
Core ``fontTools`` cannot invent TrueType bytecode — this package is the
supported integration.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Union

PathLike = Union[str, os.PathLike]


def autohint_ttf(ttf_path: PathLike, *, enabled: bool = True) -> None:
    """Autohint ``ttf_path`` in place with ttfautohint-py.

    Reads the TTF into memory, hints, writes a sibling temp file, then
    ``os.replace`` with retries (Windows AV / OneDrive often lock the
    destination briefly under parallel builds). No-op when ``enabled`` is
    false. Raises ``RuntimeError`` if the package is missing or hinting fails.
    """
    if not enabled:
        return

    try:
        from ttfautohint import ttfautohint
        from ttfautohint.errors import TAError
    except ImportError as exc:
        raise RuntimeError(
            "ttfautohint-py is required for font hinting; "
            "install with: pip install ttfautohint-py"
        ) from exc

    path = os.fspath(ttf_path)
    print(f"  Hinting with ttfautohint-py: {os.path.basename(path)}...", flush=True)

    with open(path, "rb") as src_f:
        src_bytes = src_f.read()

    # Prefer bytes return (avoid ttfautohint-py opening out_file in text mode).
    # CJK/Hangul/Yi subfonts often lack Latin "standard" glyphs; retry with
    # symbol=True (--symbol) when ttfautohint cannot derive stem metrics.
    common = dict(
        in_buffer=src_bytes,
        no_info=True,
        ignore_restrictions=True,
        default_script="latn",
        fallback_script="none",
    )
    try:
        hinted = ttfautohint(**common)
    except TAError as first:
        err = str(first)
        if "standard character" not in err and "standard width" not in err:
            raise RuntimeError(f"ttfautohint failed for {path}: {first}") from first
        try:
            hinted = ttfautohint(**common, symbol=True)
        except TAError as exc:
            raise RuntimeError(f"ttfautohint failed for {path}: {exc}") from exc

    if not hinted:
        raise RuntimeError(f"ttfautohint produced empty output for {path}")

    out_dir = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".ttf", dir=out_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(hinted)
        last_err: BaseException | None = None
        for attempt in range(8):
            try:
                os.replace(tmp_path, path)
                return
            except OSError as exc:
                last_err = exc
                time.sleep(0.05 * (2 ** min(attempt, 6)))
        assert last_err is not None
        raise last_err
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
