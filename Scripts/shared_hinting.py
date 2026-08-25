"""TrueType autohint helper via `ttfautohint-py` (fontTools ecosystem).

Install: `pip install ttfautohint-py`

Wheels bundle a `ttfautohint` binary; no separate system install needed.
Core `fontTools` cannot invent TrueType bytecode — this package is the
supported integration.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from typing import Sequence, Tuple, Union

from fontTools.ttLib import woff2

PathLike = Union[str, os.PathLike]

NO_HINT_HELP = "Skip ttfautohint-py TrueType autohint step"
HINT_BASE_ONLY_HELP = (
    "Autohint only base faces (skip segment / slice faces such as h/t/q)"
)


def add_no_hint_argument(parser: argparse.ArgumentParser) -> None:
    """`--no-hint` / `--no-hinting` on a build-script parser."""
    parser.add_argument(
        "--no-hint",
        "--no-hinting",
        action="store_true",
        help=NO_HINT_HELP,
    )


def add_jobs_argument(parser: argparse.ArgumentParser) -> None:
    """`--jobs` / `-j` parallel workers (default: all CPUs)."""
    parser.add_argument(
        "--jobs",
        "-j",
        dest="jobs",
        type=_parse_jobs,
        default=max(1, os.cpu_count() or 4),
        help=(
            "Parallel workers per stage (default: all CPUs); "
            "stages: face TTF, hint, WOFF2. ``-j -61`` is the same as ``-j 61``."
        ),
    )


def _parse_jobs(value: str) -> int:
    """Worker count; negative is abs (`-j -61` → 61, not one worker)."""
    n = int(value)
    return max(1, abs(n))


def add_hint_mode_arguments(parser: argparse.ArgumentParser) -> None:
    """Mutually exclusive `--no-hint` / `--hint-base-only`."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--no-hint",
        "--no-hinting",
        action="store_true",
        help=NO_HINT_HELP,
    )
    group.add_argument(
        "--hint-base-only",
        action="store_true",
        help=HINT_BASE_ONLY_HELP,
    )


def _sfnt_magic(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(4)


def _ensure_raw_ttf(path: str) -> None:
    """If `path` is WOFF/WOFF2 mislabeled as ``.ttf``, rewrite as SFNT TTF."""
    magic = _sfnt_magic(path)
    if magic in (b"\x00\x01\x00\x00", b"true", b"typ1"):
        return
    if magic not in (b"wOF2", b"wOFF"):
        return
    print(
        f"  [!] {os.path.basename(path)} is {magic.decode('ascii', 'replace')} "
        f"(expected TTF); converting before hint…",
        flush=True,
    )
    from fontTools.ttLib import TTFont

    tt = TTFont(path)
    tt.flavor = None
    out_dir = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".ttf", dir=out_dir)
    os.close(fd)
    try:
        tt.save(tmp_path)
        tt.close()
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def autohint_ttf(
    ttf_path: PathLike, *, enabled: bool = True, soft: bool = True
) -> bool:
    """Autohint `ttf_path` in place with ttfautohint-py.

    Reads the TTF into memory, hints, writes a sibling temp file, then
    `os.replace` with retries (Windows AV / OneDrive often lock the
    destination briefly under parallel builds). No-op when `enabled` is
    false.

    Returns True if hinted (or skipped because disabled). When `soft` is
    True, hint failures print a warning and return False instead of raising
    (segment faces can trip ttfautohint; builds should still emit WOFF2).
    """
    if not enabled:
        return True

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

    try:
        _ensure_raw_ttf(path)
    except Exception as exc:
        msg = f"ttfautohint prep failed for {path}: {exc}"
        if soft:
            print(f"  [!] {msg} — continuing unhinted", flush=True)
            return False
        raise RuntimeError(msg) from exc

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
        try:
            hinted = ttfautohint(**common)
        except TAError as first:
            err = str(first).lower()
            retry_symbol = (
                "standard character" in err
                or "standard width" in err
                or "0x02" in err
                or "unknown file format" in err
            )
            if not retry_symbol:
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
                    return True
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
    except Exception as exc:
        if soft:
            print(
                f"  [!] ttfautohint failed for {os.path.basename(path)}: {exc} "
                f"— continuing unhinted",
                flush=True,
            )
            return False
        raise


def compress_woff2(ttf_path: str, woff2_path: str | None = None) -> str:
    """Compress TTF→WOFF2 via a temp file (avoids Windows/OneDrive errno 22 races)."""
    if woff2_path is None:
        woff2_path = os.path.splitext(ttf_path)[0] + ".woff2"
    out_dir = os.path.dirname(os.path.abspath(woff2_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".woff2", dir=out_dir)
    os.close(fd)
    try:
        last_err: BaseException | None = None
        for attempt in range(8):
            try:
                woff2.compress(ttf_path, tmp_path)
                os.replace(tmp_path, woff2_path)
                return woff2_path
            except OSError as exc:
                last_err = exc
                time.sleep(0.05 * (2 ** min(attempt, 6)))
        assert last_err is not None
        raise last_err
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def drop_ttf(ttf_path: str) -> None:
    try:
        os.remove(ttf_path)
    except OSError:
        pass


def hint_ttf_task(ttf_path: str) -> str:
    """Process-pool worker: autohint `ttf_path` in place."""
    autohint_ttf(ttf_path, enabled=True)
    return ttf_path


def woff2_face_task(item: Tuple[str, bool]) -> str:
    """Process-pool worker: TTF→WOFF2; drop TTF when `write_ttf` is false."""
    ttf_path, write_ttf = item
    print(
        f"  Compressing {os.path.basename(ttf_path).replace('.ttf', '.woff2')}...",
        flush=True,
    )
    compress_woff2(ttf_path)
    if not write_ttf:
        drop_ttf(ttf_path)
    return ttf_path


def finish_font_outputs(
    ttf_paths: Sequence[str],
    *,
    hint: bool,
    write_woff2: bool,
    write_ttf: bool,
    executor,
) -> None:
    """Stages 3–4: parallel hint then WOFF2 on an existing executor."""
    paths = list(ttf_paths)
    if hint and paths:
        t0 = time.perf_counter()
        print(f"Stage 3/4: hint ({len(paths)} TTFs)...", flush=True)
        list(executor.map(hint_ttf_task, paths))
        print(f"  stage 3 done in {time.perf_counter() - t0:.1f}s", flush=True)
    if write_woff2 and paths:
        t0 = time.perf_counter()
        print(f"Stage 4/4: WOFF2 ({len(paths)} TTFs)...", flush=True)
        list(executor.map(woff2_face_task, [(p, write_ttf) for p in paths]))
        print(f"  stage 4 done in {time.perf_counter() - t0:.1f}s", flush=True)
    elif not write_ttf:
        for path in paths:
            drop_ttf(path)
