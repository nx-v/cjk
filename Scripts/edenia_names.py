"""Shared Edenia family / file / plugin names (replaces former pan-* branding)."""

from __future__ import annotations

import argparse
from typing import Iterable

# CSS / name-table families (spaces allowed).
FAMILY_KANA = "edenia kana"
FAMILY_YI = "edenia yi"
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


# Per-bucket face suffixes:
#   ""   base + ca/nhay
#   h    half-cells
#   t    thirds
#   q    2×2 corners + L 3/4 (VS41–48)
#   qv   quarter-cells (vertical / Y)
#   qh   quarter-cells (horizontal / X)
CJK_FACE_VARIANTS: tuple[str, ...] = ("", "h", "t", "q", "qv", "qh")
# Sequential order *inside* each bucket worker. Base, then half TTF, then
# q/qv/qh derived from that half font, then thirds.
CJK_FACE_BUILD_ORDER: tuple[str, ...] = ("", "h", "q", "qv", "qh", "t")
# @font-face emission order (niche faces before base). Body stacks use the
# shared ``edenia cjk`` family (base) only — pin ``edenia cjk h`` / ``t`` /
# ``q`` / ``qv`` / ``qh`` for niche GSUB. CJK unicode-range lists FE00–FE0F
# (overlay, D4, halves, triangles); Hangul/Kana/Yi faces restrict unicode-range
# so bare cmap FE* does not steal those selectors.
CJK_FACE_CSS_ORDER: tuple[str, ...] = ("q", "qv", "qh", "t", "h", "")

# Longer suffixes first so ``qh``/``qv`` are not parsed as ``q``/``h``.
_CJK_FACE_SUFFIXES: tuple[str, ...] = ("qh", "qv", "q", "h", "t")

# CLI tokens → variant suffix (``""`` is the identity / base face).
_CJK_FACE_TOKEN: dict[str, str] = {
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


def cjk_variant_from_token(token: str) -> str:
    """``base`` / ``h`` / ``t`` / ``q`` / ``qv`` / ``qh`` → face suffix."""
    key = str(token).strip().lower()
    if key not in _CJK_FACE_TOKEN:
        raise ValueError(f"unknown CJK face {token!r}; use base, h, t, q, qv, qh")
    return _CJK_FACE_TOKEN[key]


def ordered_cjk_variants(variants: Iterable[str]) -> tuple[str, ...]:
    """Dedupe and order like :data:`CJK_FACE_BUILD_ORDER`."""
    want = set(variants)
    unknown = want - set(CJK_FACE_VARIANTS)
    if unknown:
        raise ValueError(f"unknown CJK variants: {sorted(unknown)}")
    return tuple(v for v in CJK_FACE_BUILD_ORDER if v in want)


def add_cjk_variant_arguments(parser: argparse.ArgumentParser) -> None:
    """``--base-only``, ``--faces``, and ``--h`` / ``--t`` / ``--q`` / ``--qv`` / ``--qh``."""
    g = parser.add_argument_group("CJK faces")
    g.add_argument(
        "--base-only",
        action="store_true",
        help="Build only identity/base faces (no h/t/q/qv/qh)",
    )
    g.add_argument(
        "--faces",
        metavar="LIST",
        help="Exact comma list: base,h,t,q,qv,qh (overrides --h/--t/…)",
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
        help="Include third-cell faces (implies base)",
    )
    g.add_argument(
        "--q",
        dest="want_q",
        action="store_true",
        help="Include 2×2 / L faces (implies base)",
    )
    g.add_argument(
        "--qv",
        dest="want_qv",
        action="store_true",
        help="Include vertical quarter faces (implies base)",
    )
    g.add_argument(
        "--qh",
        dest="want_qh",
        action="store_true",
        help="Include horizontal quarter faces (implies base)",
    )


def resolve_cjk_variants(args: argparse.Namespace) -> tuple[str, ...]:
    """Selected CJK suffixes from CLI flags (default: all)."""
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
        got = [cjk_variant_from_token(p) for p in str(faces).split(",") if p.strip()]
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

        ``4E`` / ``66``  → ``edenia cjk``
        ``4Eh`` / ``66h`` → ``edenia cjk h``
        ``4Et``           → ``edenia cjk t``
        ``4Eq``           → ``edenia cjk q``
        ``4Eqv``          → ``edenia cjk qv``
        ``4Eqh``          → ``edenia cjk qh``

    Per-bucket coverage is applied with ``unicode-range`` on each ``@font-face``.
    """
    _core, variant = split_cjk_face_id(face_id)
    return family_cjk_variant(variant)


def family_cjk_variant(variant: str = "") -> str:
    """CSS family for a CJK face variant (``''`` / ``h`` / ``t`` / ``q`` / ``qv`` / ``qh``)."""
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
    """Filename / family stem: ``4E``, ``4Eh``, ``4Et``, ``4Eq``, ``4Eqv``, ``4Eqh``."""
    if variant and variant not in CJK_FACE_VARIANTS:
        raise ValueError(
            f"CJK face variant must be one of {CJK_FACE_VARIANTS}, got {variant!r}"
        )
    return f"{bucket_hex}{variant}"


def split_cjk_face_id(face_id: str) -> tuple[str, str]:
    """Split ``4Eqv`` → ``('4E', 'qv')``; ``4Eq`` → ``('4E', 'q')``."""
    for suf in _CJK_FACE_SUFFIXES:
        if face_id.endswith(suf):
            return face_id[: -len(suf)], suf
    return face_id, ""


# Stack after Latin: Hangul / Kana / Yi before CJK. Script faces use
# unicode-range so FE00–FE09 GSUB is not stolen; CJK lists D4 + digraph VS.
STACK_CJK_TAIL = (
    f'"{FAMILY_HANGUL}", "{FAMILY_HANGULS}", "{FAMILY_KANA}", "{FAMILY_YI}", '
    "FlopDesignFont, MKanaPlus, Plangothic P1, Plangothic P2"
)

PLUGIN_ID = "obsidian-edenia"
PLUGIN_DIR_NAME = "obsidian-edenia"
PLUGIN_ASSET = "edenia"
PLUGIN_DISPLAY_NAME = "Edenia"
PLUGIN_CLASS = "EdeniaPlugin"
