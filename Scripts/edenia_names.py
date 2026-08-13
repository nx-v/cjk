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


def family_cjk(hex_id: str) -> str:
    return f"edenia cjk {hex_id}"


def ps_cjk(hex_id: str) -> str:
    return f"edenia-cjk-{hex_id}"


# Stack tail after per-bucket CJK faces.
STACK_CJK_TAIL = (
    f'"{FAMILY_KANA}", "{FAMILY_YI}", "{FAMILY_HANGUL}", "{FAMILY_HANGULS}", '
    "FlopDesignFont, MKanaPlus, Plangothic P1, Plangothic P2"
)

PLUGIN_ID = "edenia"
PLUGIN_DIR_NAME = "obsidian-edenia"
PLUGIN_ASSET = "edenia"
PLUGIN_DISPLAY_NAME = "Edenia"
PLUGIN_CLASS = "EdeniaPlugin"
