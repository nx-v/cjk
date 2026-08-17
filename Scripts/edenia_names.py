"""Shared Edenia family / file / plugin names (replaces former pan-* branding)."""

from __future__ import annotations

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
#   qv   quarter-cells (vertical / Y)
#   qh   quarter-cells (horizontal / X)
# Build waves (base → niche faces).
CJK_FACE_VARIANTS: tuple[str, ...] = ("", "h", "t", "qv", "qh")
# @font-face emission order (niche faces before base). Body stacks use the
# shared ``edenia cjk`` family (base) only — pin ``edenia cjk h`` / ``t`` /
# ``qv`` / ``qh`` for niche GSUB. CJK unicode-range lists FE00–FE07 (D4) and
# FE0B–FE0F (digraphs); Hangul/Kana/Yi faces restrict unicode-range so bare
# cmap FE* does not steal those selectors.
CJK_FACE_CSS_ORDER: tuple[str, ...] = ("qv", "qh", "t", "h", "")

# Longer suffixes first so ``qh`` is not parsed as ``h``.
_CJK_FACE_SUFFIXES: tuple[str, ...] = ("qh", "qv", "h", "t")


def family_cjk(face_id: str) -> str:
    """CSS / name-table family for a face file stem.

    All buckets of one variant share a family so cross-bucket digraphs can
    shape in one run::

        ``4E`` / ``66``  → ``edenia cjk``
        ``4Eh`` / ``66h`` → ``edenia cjk h``
        ``4Et``           → ``edenia cjk t``
        ``4Eqv``          → ``edenia cjk qv``
        ``4Eqh``          → ``edenia cjk qh``

    Per-bucket coverage is applied with ``unicode-range`` on each ``@font-face``.
    """
    _core, variant = split_cjk_face_id(face_id)
    return family_cjk_variant(variant)


def family_cjk_variant(variant: str = "") -> str:
    """CSS family for a CJK face variant (``''`` / ``h`` / ``t`` / ``qv`` / ``qh``)."""
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
    """Filename / family stem: ``4E``, ``4Eh``, ``4Et``, ``4Eqv``, ``4Eqh``."""
    if variant and variant not in CJK_FACE_VARIANTS:
        raise ValueError(
            f"CJK face variant must be one of {CJK_FACE_VARIANTS}, got {variant!r}"
        )
    return f"{bucket_hex}{variant}"


def split_cjk_face_id(face_id: str) -> tuple[str, str]:
    """Split ``4Eqv`` → ``('4E', 'qv')``."""
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
