"""Shared Edenia family / file / plugin names (replaces former pan-* branding)."""

from __future__ import annotations

import argparse
from typing import Iterable

# CSS / name-table families (spaces allowed).
FAMILY_KANA = "edenia kana"
FAMILY_KANA_H = "edenia kana h"
FAMILY_YI = "edenia yi"
FAMILY_YI_H = "edenia yi h"
FAMILY_HANGUL = "edenia hangul"
FAMILY_HANGULS = "edenia hanguls"


def stem(family: str) -> str:
    """Filesystem and PostScript token (spaces → hyphens)."""
    return family.replace(" ", "-")


PS_KANA = stem(FAMILY_KANA)
PS_YI = stem(FAMILY_YI)
PS_HANGUL = stem(FAMILY_HANGUL)
PS_HANGULS = stem(FAMILY_HANGULS)

CSS_KANA = f"{PS_KANA}.css"
CSS_YI = f"{PS_YI}.css"
CSS_HANGUL = f"{PS_HANGUL}.css"
CSS_CJK = "edenia-cjk.css"


# Segment tiling faces (kana / yi). CJK no longer ships these — only base + h.
#   ""   base
#   h    half-cells
#   t    thirds
#   q    2×2 corners + L 3/4 (VS41–48)
#   qv   quarter-cells (vertical / Y)
#   qh   quarter-cells (horizontal / X)
SEGMENT_FACE_VARIANTS: tuple[str, ...] = ("", "h", "t", "q", "qv", "qh")
SEGMENT_FACE_BUILD_ORDER: tuple[str, ...] = ("", "h", "q", "qv", "qh", "t")
# @font-face emission order (each variant is its own family).
SEGMENT_FACE_CSS_ORDER: tuple[str, ...] = ("q", "qv", "qh", "t", "h", "")
# Default CSS / theme stack (segment faces before base). Digraph runs must still
# pin one family (HTML mode face / Obsidian post-processor) — Blink picks fonts
# per codepoint, so a multi-face stack alone overlays full glyphs via FE00.
SEGMENT_FACE_STACK_ORDER: tuple[str, ...] = ("q", "qv", "qh", "t", "h", "")

# CJK: identity/base + half-cell digraphs only.
CJK_FACE_VARIANTS: tuple[str, ...] = ("", "h")
CJK_FACE_BUILD_ORDER: tuple[str, ...] = ("", "h")
CJK_FACE_CSS_ORDER: tuple[str, ...] = ("h", "")

# Longer suffixes first so ``qh``/``qv`` are not parsed as ``q``/``h``.
# Kept for kana/yi stems and legacy CJK filenames on disk.
_SEGMENT_FACE_SUFFIXES: tuple[str, ...] = ("qh", "qv", "q", "h", "t")

# CLI tokens → variant suffix (``""`` is the identity / base face).
_SEGMENT_FACE_TOKEN: dict[str, str] = {
    "base": "",
    "none": "",
    "plain": "",
    "id": "",
    "h": "h",
    "t": "t",
    "q": "q",
    "qv": "qv",
    "qh": "qh",
}


def segment_variant_from_token(token: str) -> str:
    """`base` / `h` / `t` / `q` / `qv` / `qh` → face suffix."""
    key = str(token).strip().lower()
    if key not in _SEGMENT_FACE_TOKEN:
        raise ValueError(
            f"unknown face {token!r}; use base, h, t, q, qv, qh"
        )
    return _SEGMENT_FACE_TOKEN[key]


# Back-compat alias (CJK CLI historically used this name).
cjk_variant_from_token = segment_variant_from_token


def ordered_segment_variants(variants: Iterable[str]) -> tuple[str, ...]:
    """Dedupe and order like :data:`SEGMENT_FACE_BUILD_ORDER`."""
    want = set(variants)
    unknown = want - set(SEGMENT_FACE_VARIANTS)
    if unknown:
        raise ValueError(f"unknown segment variants: {sorted(unknown)}")
    return tuple(v for v in SEGMENT_FACE_BUILD_ORDER if v in want)


def ordered_cjk_variants(variants: Iterable[str]) -> tuple[str, ...]:
    """Dedupe and order like :data:`CJK_FACE_BUILD_ORDER` (base + h only)."""
    want = set(variants)
    unknown = want - set(CJK_FACE_VARIANTS)
    if unknown:
        raise ValueError(
            f"CJK no longer builds {sorted(unknown)}; only base and h "
            f"(thirds/quarters are kana/yi)"
        )
    return tuple(v for v in CJK_FACE_BUILD_ORDER if v in want)


def add_cjk_variant_arguments(parser: argparse.ArgumentParser) -> None:
    """`--base-only`, `--faces`, and `--h` / `--t` / `--q` / `--qv` / `--qh`.

    CJK resolves only ``base``/``h``; kana/yi accept the full segment set.
    """
    g = parser.add_argument_group("faces")
    g.add_argument(
        "--base-only",
        action="store_true",
        help="Build only identity/base faces (no segment faces)",
    )
    g.add_argument(
        "--faces",
        metavar="LIST",
        help="Exact comma list: base,h[,t,q,qv,qh] (overrides --h/--t/…)",
    )
    g.add_argument(
        "--h",
        dest="want_h",
        action="store_true",
        help="Include half-cell faces (implies base)",
    )
    g.add_argument(
        "--t",
        dest="want_t",
        action="store_true",
        help="Include third-cell faces (implies base; kana/yi only)",
    )
    g.add_argument(
        "--q",
        dest="want_q",
        action="store_true",
        help="Include 2×2 / L faces (implies base; kana/yi only)",
    )
    g.add_argument(
        "--qv",
        dest="want_qv",
        action="store_true",
        help="Include vertical quarter faces (implies base; kana/yi only)",
    )
    g.add_argument(
        "--qh",
        dest="want_qh",
        action="store_true",
        help="Include horizontal quarter faces (implies base; kana/yi only)",
    )


# Default segment faces for kana / yi when no CLI flags (full set).
KANA_YI_DEFAULT_VARIANTS: tuple[str, ...] = ("", "h", "t", "q", "qv", "qh")


def resolve_kana_yi_variants(args: argparse.Namespace) -> tuple[str, ...]:
    """Selected kana/yi suffixes from CLI (default: :data:`KANA_YI_DEFAULT_VARIANTS`)."""
    extras = [
        v
        for v, flag in (
            ("h", getattr(args, "want_h", False)),
            ("t", getattr(args, "want_t", False)),
            ("q", getattr(args, "want_q", False)),
            ("qv", getattr(args, "want_qv", False)),
            ("qh", getattr(args, "want_qh", False)),
        )
        if flag
    ]
    faces = getattr(args, "faces", None)
    base_only = getattr(args, "base_only", False)
    if base_only and (faces or extras):
        raise ValueError(
            "--base-only cannot be combined with --faces / --h / --t / --q"
        )
    if base_only:
        return ("",)
    if faces:
        if extras:
            raise ValueError("use either --faces or --h/--t/--q/--qv/--qh, not both")
        got = [
            segment_variant_from_token(p)
            for p in str(faces).split(",")
            if p.strip()
        ]
        if not got:
            raise ValueError("--faces is empty")
        return ordered_segment_variants(got)
    if extras:
        return ordered_segment_variants(["", *extras])
    return KANA_YI_DEFAULT_VARIANTS


def resolve_cjk_variants(args: argparse.Namespace) -> tuple[str, ...]:
    """Selected CJK suffixes from CLI flags (default: base + h only)."""
    extras = [
        v
        for v, flag in (
            ("h", getattr(args, "want_h", False)),
            ("t", getattr(args, "want_t", False)),
            ("q", getattr(args, "want_q", False)),
            ("qv", getattr(args, "want_qv", False)),
            ("qh", getattr(args, "want_qh", False)),
        )
        if flag
    ]
    faces = getattr(args, "faces", None)
    base_only = getattr(args, "base_only", False)
    if base_only and (faces or extras):
        raise ValueError(
            "--base-only cannot be combined with --faces / --h / --t / --q"
        )
    if base_only:
        return ("",)
    if faces:
        if extras:
            raise ValueError("use either --faces or --h/--t/--q/--qv/--qh, not both")
        got = [
            segment_variant_from_token(p)
            for p in str(faces).split(",")
            if p.strip()
        ]
        if not got:
            raise ValueError("--faces is empty")
        return ordered_cjk_variants(got)
    if extras:
        return ordered_cjk_variants(["", *extras])
    return CJK_FACE_BUILD_ORDER


def family_cjk(face_id: str) -> str:
    """CSS / name-table family for a face file stem.

    All buckets of one variant share a family so cross-bucket digraphs can
    shape in one run::

        `4E` / `66`  → `edenia cjk`
        `4Eh` / `66h` → `edenia cjk h`

    Per-bucket coverage is applied with `unicode-range` on each `@font-face`.
    """
    _core, variant = split_cjk_face_id(face_id)
    return family_cjk_variant(variant)


def family_cjk_variant(variant: str = "") -> str:
    """CSS family for a CJK face variant (`''` / `h`)."""
    if variant and variant not in CJK_FACE_VARIANTS:
        raise ValueError(
            f"CJK face variant must be one of {CJK_FACE_VARIANTS}, got {variant!r}"
        )
    if not variant:
        return "edenia cjk"
    return f"edenia cjk {variant}"


def ps_cjk(face_id: str) -> str:
    """Unique PostScript name per file (buckets stay distinct)."""
    return f"edenia-cjk-{face_id}"


def cjk_face_id(bucket_hex: str, variant: str = "") -> str:
    """Filename / family stem: `4E`, `4Eh`."""
    if variant and variant not in CJK_FACE_VARIANTS:
        raise ValueError(
            f"CJK face variant must be one of {CJK_FACE_VARIANTS}, got {variant!r}"
        )
    return f"{bucket_hex}{variant}"


def split_cjk_face_id(face_id: str) -> tuple[str, str]:
    """Split `4Eh` → `('4E', 'h')`; also parses legacy `t`/`q`/`qv`/`qh` stems."""
    for suf in _SEGMENT_FACE_SUFFIXES:
        if face_id.endswith(suf):
            return face_id[: -len(suf)], suf
    return face_id, ""


def family_yi_variant(variant: str = "") -> str:
    """CSS family: `''` → `edenia yi`; `h` / `t` / `q` / `qv` / `qh` → suffixed."""
    if variant and variant not in SEGMENT_FACE_VARIANTS:
        raise ValueError(
            f"Yi face variant must be one of {SEGMENT_FACE_VARIANTS}, got {variant!r}"
        )
    if not variant:
        return FAMILY_YI
    return f"{FAMILY_YI} {variant}"


def family_kana_variant(variant: str = "") -> str:
    """CSS family: `''` → `edenia kana`; `h` / `t` / `q` / `qv` / `qh` → suffixed."""
    if variant and variant not in SEGMENT_FACE_VARIANTS:
        raise ValueError(
            f"Kana face variant must be one of {SEGMENT_FACE_VARIANTS}, got {variant!r}"
        )
    if not variant:
        return FAMILY_KANA
    return f"{FAMILY_KANA} {variant}"


def bucket_face_id(bucket_id: int, variant: str) -> str:
    """Pigeonhole stem: `A0h`, `E0t`, `E0qv` (`{page:02X}{variant}`)."""
    if not variant or variant not in SEGMENT_FACE_VARIANTS:
        raise ValueError(
            f"bucket face variant must be one of {SEGMENT_FACE_VARIANTS[1:]}, "
            f"got {variant!r}"
        )
    return f"{bucket_id:02X}{variant}"


def h_bucket_face_id(bucket_id: int) -> str:
    """Pigeonhole half-cell face stem (`A0h`, `E0h`)."""
    return bucket_face_id(bucket_id, "h")


def parse_bucket_face_id(face_id: str) -> tuple[int | None, str]:
    """`E0h` → `(0xE0, 'h')`; base stems → `(None, '')`."""
    for suf in _SEGMENT_FACE_SUFFIXES:
        if face_id.endswith(suf):
            core = face_id[: -len(suf)]
            if core and all(c in "0123456789abcdefABCDEF" for c in core):
                return int(core, 16), suf
    return None, ""


def parse_h_bucket_face_id(face_id: str) -> int | None:
    """`A0h` → `0xA0`; non-bucket stems (`edenia-yi`) → `None`."""
    bucket_id, variant = parse_bucket_face_id(face_id)
    if variant != "h":
        return None
    return bucket_id


def ps_yi(face_id: str) -> str:
    """PostScript name: base `edenia-yi`, slice files `edenia-yi-A0h`."""
    if not face_id or face_id == PS_YI:
        return PS_YI
    return f"{PS_YI}-{face_id}"


def ps_kana(face_id: str) -> str:
    """PostScript name: base `edenia-kana`, slice files `edenia-kana-E0h`."""
    if not face_id or face_id == PS_KANA:
        return PS_KANA
    return f"{PS_KANA}-{face_id}"


# Default stack after Latin: Hangul + kana/yi h+base before CJK. Other segment
# families are still @font-face'd; pin them for t/q/qv/qh digraphs.
STACK_CJK_TAIL = (
    f'"{FAMILY_HANGUL}", "{FAMILY_HANGULS}", '
    + ", ".join(f'"{family_kana_variant(v)}"' for v in SEGMENT_FACE_STACK_ORDER)
    + ", "
    + ", ".join(f'"{family_yi_variant(v)}"' for v in SEGMENT_FACE_STACK_ORDER)
    + ', "FlopDesignFont", "MKanaPlus", "Plangothic P1", "Plangothic P2"'
)

PLUGIN_ID = "obsidian-edenia"
PLUGIN_DIR_NAME = "obsidian-edenia"
PLUGIN_ASSET = "edenia"
PLUGIN_DISPLAY_NAME = "Edenia"
PLUGIN_CLASS = "EdeniaPlugin"
