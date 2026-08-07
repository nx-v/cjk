"""Yi standalone glyph transforms (NuosuSIL).

Encoding
--------
* One font (``panyi``) covering the whole Yi inventory.
* Standalones: NuosuSIL is monospace (shared advance). Every glyph gets the
  **same** ``sx`` from that advance and the **same** ``sy`` from the tallest
  ink height, then headless CAPE Weightor Width-mode stretch (``cape_weightor``);
  Y is fitted to the CJK typo box (center at 0.38em).
* Orientations: D4 square symmetries on **VS01..VS08** (``U+E000``..``U+E007``,
  UVS ``U+FE00``..``U+FE07``), including ``r90my``. Identity needs no subst;
  the other seven are TrueType composites / baked outlines about the CJK
  typo center.
* Overlay: **``U+FE08``** superimposes preceding glyphs into one cell —
  everything but the **last** glyph before ``FE08`` becomes zero-advance
  (``.ov``); the last keeps the em advance. Chain with more ``FE08``
  (``A B FE08 C FE08`` → ``A.ov B.ov C``).
* No side-by-side digraph compounds. Full D4 (8 modes) remains available for
  build_subfonts / GlyphWiki via ``TRANSFORM_MODES``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from fontTools.misc.roundTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import (
    ROUND_XY_TO_GRID,
    UNSCALED_COMPONENT_OFFSET,
    USE_MY_METRICS,
    Glyph as TTGlyph,
    GlyphComponent,
)

try:
    from kage.mapping import D4_MODES, MirrorVS
except ImportError:  # Scripts.* import style
    from Scripts.kage.mapping import D4_MODES, MirrorVS

from cape_weightor import widen_ttglyph

# ---------- Constants ----------

YI_SYLLABLES = (0xA000, 0xA48C)
YI_RADICALS = (0xA490, 0xA4CF)

VS_BASE = 0xE000
# Full D4 set (8) — Yi orientations + build_subfonts / GlyphWiki.
VS_COUNT = MirrorVS.MODE_COUNT
VS_LAST = VS_BASE + VS_COUNT - 1  # U+E007

UVS_BASE = 0xFE00  # VS1..VS8 → identity..r90my
UVS_LAST = UVS_BASE + VS_COUNT - 1  # U+FE07
STACK_MARK_CP = 0xFE08  # Unicode VS9 — superimpose (not a D4 mode)

DEFAULT_UPEM = 1000
# Optional inset of the shared advance box inside the CJK cell (uniform).
STANDALONE_PAD = 0.0
HALFWIDTH_PAD = 0.0
COMPOUND_PAD = 0.0
# Standalone only: CAPE Weightor Width-mode factor after fit
# (0.15 → target outer width 115% of post-fit ink, stems preserved).
STANDALONE_CONTOUR_WIDEN = 0.15
# Inset from CJK typo top/bottom when fitting Y (fraction of em).
# Keeps short glyphs from sitting on the raw descent (-0.12em), which reads
# low next to CJK ink that usually rests nearer the baseline.
STANDALONE_VERT_PAD = 0.05

# Match build_yi / build_subfonts OS/2 + hhea (CJK ideographic body).
TYPO_ASCENDER_FRAC = 0.88
TYPO_DESCENDER_FRAC = -0.12

NUOSU_FILENAME = "NuosuSIL-Regular.ttf"

Bounds = Tuple[float, float, float, float]
GlyphMetrics = Tuple[TTGlyph, int, int]


def ideographic_center(target_upem: int) -> Tuple[float, float]:
    """Center of the CJK typo box (ascent 0.88em / descent -0.12em).

    Geometric em midpoint is ``(upem/2, upem/2)``; CJK ink after uniform
    UPM scale sits near ``(upem/2, 0.38·upem)``. Centering Yi there keeps
    mixed CJK+Yi lines vertically aligned.
    """
    bottom, top, _h = ideographic_bounds(target_upem)
    return target_upem / 2.0, (top + bottom) / 2.0


def ideographic_bounds(target_upem: int) -> Tuple[float, float, float]:
    """CJK typo box ``(bottom, top, height)`` using ascent 0.88 / descent -0.12."""
    top = target_upem * TYPO_ASCENDER_FRAC
    bottom = target_upem * TYPO_DESCENDER_FRAC
    return bottom, top, top - bottom


# (vs_cp, rot90_quarters, flip_x, flip_y, name_suffix or None for identity)
# Shared with build_subfonts / GlyphWiki via kage.mapping.D4_MODES.
TransformMode = Tuple[int, int, bool, bool, Optional[str]]

TRANSFORM_MODES: List[TransformMode] = [
    (MirrorVS.codepoint(mode), rot, fx, fy, suffix)
    for mode, rot, fx, fy, suffix in D4_MODES
]

# Yi uses the full D4 set (VS01..VS08), same as TRANSFORM_MODES.
YI_ORIENTATION_MODES: List[TransformMode] = TRANSFORM_MODES


def vs_glyph_name(vs_cp: int) -> str:
    if vs_cp == STACK_MARK_CP:
        return "vsStack"
    if VS_BASE <= vs_cp <= VS_LAST:
        return f"vs{vs_cp - VS_BASE + 1:02d}"
    raise ValueError(f"not a Yi VS/stack codepoint: U+{vs_cp:04X}")


def stack_glyph_name() -> str:
    return "vsStack"


def uvs_selector_for_mode(mode_index: int) -> int:
    """Unicode VS1..VS8 (U+FE00..) for D4 mode index 0..7."""
    return UVS_BASE + mode_index


def build_d4_uvs_entries(
    base_cp: int,
    base_glyph: str,
    *,
    glyphs: Dict[str, TTGlyph],
    modes: Optional[Sequence[TransformMode]] = None,
) -> List[Tuple[int, int, Optional[str]]]:
    """``(base_cp, U+FE0n, variantName)`` rows for ``setupCharacterMap(uvs=...)``.

    Identity (default glyph) is omitted: cmap format 14 default UVS ranges use a
    uint8 length, so >256 consecutive bases (e.g. full Yi) overflow on compile.
    Non-default mappings are stored one-per-record and have no such limit.
    """
    rows: List[Tuple[int, int, Optional[str]]] = []
    for mode_i, (_vs_cp, _r, _fx, _fy, suffix) in enumerate(
        modes if modes is not None else TRANSFORM_MODES
    ):
        if suffix is None:
            continue
        vname = variant_glyph_name(base_glyph, suffix)
        if vname in glyphs:
            rows.append((base_cp, uvs_selector_for_mode(mode_i), vname))
    return rows


def variant_glyph_name(base_name: str, suffix: str) -> str:
    return f"{base_name}.{suffix}"


def overlay_glyph_name(base_name: str) -> str:
    """Zero-advance form of ``base_name`` for FE08 superposition."""
    return f"{base_name}.ov"


def orientation_form_names(
    base_name: str,
    *,
    modes: Optional[Sequence[TransformMode]] = None,
) -> List[str]:
    """Identity + non-identity D4 variant names for ``base_name``."""
    names = [base_name]
    for _vs, _r, _fx, _fy, suffix in modes if modes is not None else TRANSFORM_MODES:
        if suffix is not None:
            names.append(variant_glyph_name(base_name, suffix))
    return names


def inject_stack_mark(
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    cmap: Dict[int, str],
) -> str:
    """Ensure FE08 stack mark glyph exists (zero-width) and is cmap'd."""
    sname = stack_glyph_name()
    if sname not in glyphs:
        glyph_order.append(sname)
        glyphs[sname] = empty_glyph()
        metrics[sname] = (0, 0)
    cmap[STACK_MARK_CP] = sname
    return sname


def add_overlay_forms(
    form_names: Sequence[str],
    *,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    limit: Optional[int] = None,
) -> List[str]:
    """Create zero-advance ``.ov`` composites for each name in ``form_names``.

    ``limit`` caps how many new ``.ov`` glyphs are added (GlyphWiki 64k budget).
    Returns the list of base form names that received an ``.ov``.
    """
    added_bases: List[str] = []
    for name in form_names:
        if limit is not None and len(added_bases) >= limit:
            break
        if name not in glyphs:
            continue
        ov_name = overlay_glyph_name(name)
        if ov_name in glyphs:
            added_bases.append(name)
            continue
        _adv, lsb = metrics.get(name, (0, 0))
        ov_glyph, ov_adv, ov_lsb = make_overlay_composite(name, lsb=lsb)
        glyph_order.append(ov_name)
        glyphs[ov_name] = ov_glyph
        metrics[ov_name] = (ov_adv, ov_lsb)
        added_bases.append(name)
    return added_bases


def install_overlay_gsub(
    font,
    full_forms: Sequence[str],
    *,
    glyphs: Dict[str, TTGlyph],
    glyph_order: Sequence[str],
    max_stack: int = 8,
) -> int:
    """Install FE08 overlay lookups into ``font`` GSUB (create or append).

    ``A B FE08`` → ``A.ov`` (0-advance) + ``B`` (keeps advance); FE08 consumed.
    Longer stacks (``A B FE08 C FE08`` …) work by repeating the zero+consume
    pair in the feature list (``max_stack`` times).

    Requires ``.ov`` forms already present for entries in ``full_forms``.
    Returns number of feature-attached lookup slots added.
    """
    from fontTools.otlLib.builder import (  # local import keeps module import light
        buildLigatureSubstSubtable,
        buildLookup,
        buildSingleSubstSubtable,
    )
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables as ot

    order_index = {n: i for i, n in enumerate(glyph_order)}

    def _gid_sort(names: Sequence[str]) -> List[str]:
        return sorted(set(names), key=lambda n: order_index.get(n, 10**9))

    def _coverage(names: Sequence[str]) -> ot.Coverage:
        cov = ot.Coverage()
        cov.glyphs = list(names)
        return cov

    forms = _gid_sort([n for n in full_forms if n in glyphs])
    overlayable = _gid_sort(
        [n for n in forms if overlay_glyph_name(n) in glyphs]
    )
    if not overlayable:
        return 0

    stack = stack_glyph_name()
    if stack not in glyphs:
        return 0

    # 1) A' with lookahead B FE08 → A.ov  (stack not consumed yet)
    overlay_single = {name: overlay_glyph_name(name) for name in overlayable}
    single_sub = buildSingleSubstSubtable(overlay_single)
    single_lu = buildLookup([single_sub])
    single_lu.LookupType = 1

    st = ot.ChainContextSubst()
    st.Format = 3
    st.BacktrackGlyphCount = 0
    st.BacktrackCoverage = []
    st.InputGlyphCount = 1
    st.InputCoverage = [_coverage(overlayable)]
    st.LookAheadGlyphCount = 2
    st.LookAheadCoverage = [_coverage(forms), _coverage([stack])]
    st.SubstCount = 1
    rec = ot.SubstLookupRecord()
    rec.SequenceIndex = 0
    rec.LookupListIndex = 0  # patched
    st.SubstLookupRecord = [rec]
    chain_lu = buildLookup([st])
    chain_lu.LookupType = 6

    # 2) B + FE08 → B  (consume stack; runs after zeroing)
    consume_map = {(name, stack): name for name in forms}
    consume_sub = buildLigatureSubstSubtable(consume_map)
    consume_lu = buildLookup([consume_sub])
    consume_lu.LookupType = 4

    if "GSUB" in font:
        gsub = font["GSUB"].table
    else:
        def _langsys() -> ot.DefaultLangSys:
            ls = ot.DefaultLangSys()
            ls.ReqFeatureIndex = 0xFFFF
            ls.FeatureCount = 0
            ls.FeatureIndex = []
            return ls

        script_tags: List[str] = []
        for line in COMPOSITION_LANGUAGE_SYSTEMS:
            parts = line.replace(";", "").split()
            if len(parts) >= 2 and parts[0] == "languagesystem":
                script_tags.append(parts[1].ljust(4)[:4])

        gsub = ot.GSUB()
        gsub.Version = 0x00010000
        gsub.ScriptList = ot.ScriptList()
        gsub.ScriptList.ScriptRecord = []
        for tag in script_tags:
            srec = ot.ScriptRecord()
            srec.ScriptTag = tag
            srec.Script = ot.Script()
            srec.Script.DefaultLangSys = _langsys()
            srec.Script.LangSysCount = 0
            srec.Script.LangSysRecord = []
            gsub.ScriptList.ScriptRecord.append(srec)
        gsub.ScriptList.ScriptCount = len(script_tags)
        gsub.FeatureList = ot.FeatureList()
        gsub.FeatureList.FeatureRecord = []
        gsub.FeatureList.FeatureCount = 0
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0
        table = newTable("GSUB")
        table.table = gsub
        font["GSUB"] = table

    if gsub.LookupList is None:
        gsub.LookupList = ot.LookupList()
        gsub.LookupList.Lookup = []
        gsub.LookupList.LookupCount = 0

    base = gsub.LookupList.LookupCount
    # Order: chain, nested single, consume. Feature only lists chain + consume
    # (repeated so A B FE08 C FE08 … can apply multiple times).
    chain_index = base
    single_index = base + 1
    consume_index = base + 2
    st.SubstLookupRecord[0].LookupListIndex = single_index
    gsub.LookupList.Lookup.extend([chain_lu, single_lu, consume_lu])
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)

    feature_lookup_idxs: List[int] = []
    for _ in range(max(1, max_stack)):
        feature_lookup_idxs.append(chain_index)
        feature_lookup_idxs.append(consume_index)

    tag_to_fr = {
        fr.FeatureTag: fr for fr in (gsub.FeatureList.FeatureRecord or [])
    }
    for tag in COMPOSITION_FEATURE_TAGS:
        fr = tag_to_fr.get(tag)
        if fr is None:
            fr = ot.FeatureRecord()
            fr.FeatureTag = tag
            fr.Feature = ot.Feature()
            fr.Feature.FeatureParams = None
            fr.Feature.LookupListIndex = []
            fr.Feature.LookupCount = 0
            gsub.FeatureList.FeatureRecord.append(fr)
            gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)
            tag_to_fr[tag] = fr
            for sr in gsub.ScriptList.ScriptRecord:
                ls = sr.Script.DefaultLangSys
                if ls is None:
                    continue
                fi = list(ls.FeatureIndex or [])
                new_i = gsub.FeatureList.FeatureCount - 1
                if new_i not in fi:
                    fi.append(new_i)
                    ls.FeatureIndex = fi
                    ls.FeatureCount = len(fi)
        idxs = list(fr.Feature.LookupListIndex or [])
        for li in feature_lookup_idxs:
            idxs.append(li)
        fr.Feature.LookupListIndex = idxs
        fr.Feature.LookupCount = len(idxs)

    return len(feature_lookup_idxs)


# Shared across build_yi / build_subfonts / GlyphWiki.
COMPOSITION_FEATURE_TAGS: Tuple[str, ...] = ("ccmp", "rlig", "liga")
COMPOSITION_LANGUAGE_SYSTEMS: Tuple[str, ...] = (
    "languagesystem DFLT dflt;",
    "languagesystem latn dflt;",
    "languagesystem yi dflt;",
    "languagesystem hani dflt;",
)


def composition_fea(*rule_groups: Sequence[str]) -> str:
    """FEA for mandatory composition: ``ccmp`` + ``rlig`` + ``liga`` on common scripts.

    Each ``rule_groups`` entry is a sequence of already-indented ``sub ...;`` lines.
    Empty groups are skipped. Returns ``\"\"`` when there are no rules.
    """
    body_lines: List[str] = []
    for group in rule_groups:
        for line in group:
            if line is None:
                continue
            s = line if line.endswith("\n") else line
            s = s.rstrip("\n")
            if s.strip():
                body_lines.append(s if s.startswith(" ") else f"  {s.lstrip()}")
    if not body_lines:
        return ""
    body = "\n".join(body_lines)
    parts = list(COMPOSITION_LANGUAGE_SYSTEMS) + [""]
    for tag in COMPOSITION_FEATURE_TAGS:
        parts.append(f"feature {tag} {{")
        parts.append(body)
        parts.append(f"}} {tag};")
        parts.append("")
    return "\n".join(parts)


def add_d4_variant_glyphs(
    base_name: str,
    *,
    advance: int,
    lsb: int,
    target_upem: int,
    glyph_order: List[str],
    glyphs: Dict[str, TTGlyph],
    metrics: Dict[str, Tuple[int, int]],
    modes: Optional[Sequence[TransformMode]] = None,
) -> List[Tuple[int, str, str]]:
    """Create non-identity D4 forms for ``base_name`` (bake 2×2, composite otherwise).

    Returns ``[(vs_cp, suffix, variant_glyph_name), ...]`` for GSUB wiring.
    """
    installed: List[Tuple[int, str, str]] = []
    use_modes = modes if modes is not None else TRANSFORM_MODES
    for vs_cp, rot, flip_x, flip_y, suffix in use_modes:
        if suffix is None:
            continue
        m_name = variant_glyph_name(base_name, suffix)
        if m_name not in glyphs:
            m_glyph, m_adv, m_lsb = make_composite_variant(
                base_name,
                target_upem,
                rot90_quarters=rot,
                flip_x=flip_x,
                flip_y=flip_y,
                advance=advance,
                lsb=lsb,
                base_glyph=glyphs[base_name],
                glyph_set=glyphs,
            )
            glyph_order.append(m_name)
            glyphs[m_name] = m_glyph
            metrics[m_name] = (m_adv, m_lsb)
        installed.append((vs_cp, suffix, m_name))
    return installed


def _rot90_matrix(quarters: int) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    q = quarters % 4
    if q == 0:
        return ((1.0, 0.0), (0.0, 1.0))
    if q == 1:
        return ((0.0, 1.0), (-1.0, 0.0))
    if q == 2:
        return ((-1.0, 0.0), (0.0, -1.0))
    return ((0.0, -1.0), (1.0, 0.0))


def _mul2(
    a: Tuple[Tuple[float, float], Tuple[float, float]],
    b: Tuple[Tuple[float, float], Tuple[float, float]],
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    (axx, axy), (ayx, ayy) = a
    (bxx, bxy), (byx, byy) = b
    return (
        (axx * bxx + ayx * bxy, axy * bxx + ayy * bxy),
        (axx * byx + ayx * byy, axy * byx + ayy * byy),
    )


def _apply_mat(
    m: Tuple[Tuple[float, float], Tuple[float, float]], x: float, y: float
) -> Tuple[float, float]:
    (xx, xy), (yx, yy) = m
    return xx * x + yx * y, xy * x + yy * y


def variant_matrix(
    *,
    rot90_quarters: int,
    flip_x: bool,
    flip_y: bool,
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    r = _rot90_matrix(rot90_quarters)
    sx = -1.0 if flip_y else 1.0
    sy = -1.0 if flip_x else 1.0
    s: Tuple[Tuple[float, float], Tuple[float, float]] = ((sx, 0.0), (0.0, sy))
    return _mul2(s, r)


def variant_transform(
    target_upem: int,
    *,
    rot90_quarters: int,
    flip_x: bool,
    flip_y: bool,
    center: Optional[Tuple[float, float]] = None,
) -> Transform:
    """D4 map rotating/reflecting about ``center`` (default: ideographic mid)."""
    if rot90_quarters % 4 == 0 and not flip_x and not flip_y:
        return Transform()
    m = variant_matrix(rot90_quarters=rot90_quarters, flip_x=flip_x, flip_y=flip_y)
    (xx, xy), (yx, yy) = m
    cx, cy = center if center is not None else ideographic_center(target_upem)
    # p' = M·(p - c) + c
    dx = cx - xx * cx - yx * cy
    dy = cy - xy * cx - yy * cy
    return Transform(xx, xy, yx, yy, dx, dy)


def make_composite_variant(
    base_name: str,
    target_upem: int,
    *,
    rot90_quarters: int = 0,
    flip_x: bool = False,
    flip_y: bool = False,
    advance: int,
    lsb: int = 0,
    base_glyph: Optional[TTGlyph] = None,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> GlyphMetrics:
    """D4 variant of ``base_name`` about the CJK typo center.

    Axis-aligned maps (r180 / mx / my) stay one-component TT composites.
    Rotations that need a full 2×2 matrix (r90 / r270 / diagonals) are baked
    to outlines — many viewers mishandle ``WE_HAVE_A_TWO_BY_TWO``, which is
    why those cells looked empty.
    """
    t = variant_transform(
        target_upem,
        rot90_quarters=rot90_quarters,
        flip_x=flip_x,
        flip_y=flip_y,
    )
    needs_2x2 = abs(t.xy) > 1e-9 or abs(t.yx) > 1e-9
    if needs_2x2:
        src = base_glyph
        if src is None and glyph_set is not None:
            src = glyph_set.get(base_name)
        if src is None:
            raise ValueError(
                f"2x2 variant of {base_name!r} needs base_glyph or glyph_set"
            )
        return _bake_transformed_glyph(src, t, advance, glyph_set=glyph_set)

    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = base_name
    comp.x = otRound(t.dx)
    comp.y = otRound(t.dy)
    # Explicit MS/OT offset rule — without this, Apple-style scaled offsets
    # wreck 90°/270° placements when a 2x2 composite is used.
    comp.flags = USE_MY_METRICS | ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    if (t.xx, t.xy, t.yx, t.yy) != (1.0, 0.0, 0.0, 1.0):
        # fontTools: ((xx, xy), (yx, yy)) with x' = xx·x + yx·y + dx
        comp.transform = ((t.xx, t.xy), (t.yx, t.yy))
    g.components = [comp]
    return g, advance, lsb


def _recording_from_glyph(
    glyph: TTGlyph,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> RecordingPen:
    """Expand ``glyph`` (including shallow composites) to a recording."""
    rec = RecordingPen()
    if not glyph.isComposite():
        glyph.draw(rec, None)
        return rec
    if glyph_set is None:
        raise ValueError("composite glyph needs glyph_set to bake transforms")
    for comp in glyph.components:
        name, (xx, xy, yx, yy, dx, dy) = comp.getComponentInfo()
        child = glyph_set[name]
        child_rec = _recording_from_glyph(child, glyph_set)
        child_rec.replay(TransformPen(rec, Transform(xx, xy, yx, yy, dx, dy)))
    return rec


def _bake_transformed_glyph(
    glyph: TTGlyph,
    t: Transform,
    advance: int,
    *,
    glyph_set: Optional[Dict[str, TTGlyph]] = None,
) -> GlyphMetrics:
    rec = _recording_from_glyph(glyph, glyph_set)
    det = t.xx * t.yy - t.xy * t.yx
    out = apply_transform(rec, t, reverse_winding=det < 0)
    try:
        out.recalcBounds(None)
        lsb = int(out.xMin)
    except Exception:
        lsb = 0
    return out, advance, lsb


def make_composite_pair(
    left_name: str,
    right_name: str,
    target_upem: int = DEFAULT_UPEM,
    *,
    lsb: int = 0,
) -> GlyphMetrics:
    """Two-component TT composite: left half-cell + right half-cell (shift +½em)."""
    half = target_upem // 2
    g = TTGlyph()
    g.numberOfContours = -1
    left = GlyphComponent()
    left.glyphName = left_name
    left.x = 0
    left.y = 0
    left.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    right = GlyphComponent()
    right.glyphName = right_name
    right.x = half
    right.y = 0
    right.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [left, right]
    return g, target_upem, lsb


def make_right_half_composite(
    left_name: str,
    target_upem: int = DEFAULT_UPEM,
    *,
    lsb: int = 0,
) -> GlyphMetrics:
    """Zero-width right-slot composite: ``left_name`` shifted +½em (digraph overlay)."""
    half = target_upem // 2
    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = left_name
    comp.x = half
    comp.y = 0
    comp.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [comp]
    return g, 0, lsb


def make_overlay_composite(
    base_name: str,
    *,
    lsb: int = 0,
) -> GlyphMetrics:
    """Zero-advance same-cell overlay of ``base_name`` (FE08 superposition)."""
    g = TTGlyph()
    g.numberOfContours = -1
    comp = GlyphComponent()
    comp.glyphName = base_name
    comp.x = 0
    comp.y = 0
    comp.flags = ROUND_XY_TO_GRID | UNSCALED_COMPONENT_OFFSET
    g.components = [comp]
    return g, 0, lsb


def _cp_hex(cp: int) -> str:
    return f"{cp:04X}" if cp <= 0xFFFF else f"{cp:05X}"


def halfcell_left_name(cp: int) -> str:
    return f"yihL{_cp_hex(cp)}"


def halfcell_right_name(cp: int) -> str:
    return f"yihR{_cp_hex(cp)}"


def halfcell_glyph_name(cp: int) -> str:
    """Alias for the left-slot half-cell name."""
    return halfcell_left_name(cp)


def center_glyph_in_cell(
    glyph: TTGlyph,
    target_upem: int,
    *,
    center: Optional[Tuple[float, float]] = None,
) -> TTGlyph:
    """Translate ``glyph`` so its bbox center sits at the CJK typo midpoint."""
    try:
        glyph.recalcBounds(None)
        x_min, y_min, x_max, y_max = glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax
    except Exception:
        return glyph
    cx, cy = center if center is not None else ideographic_center(target_upem)
    sx = cx - (x_min + x_max) / 2.0
    sy = cy - (y_min + y_max) / 2.0
    if abs(sx) < 1e-6 and abs(sy) < 1e-6:
        return glyph
    rec = RecordingPen()
    glyph.draw(rec, None)
    return apply_transform(rec, Transform(1, 0, 0, 1, sx, sy))


@dataclass(frozen=True)
class YiInventory:
    source_path: str
    src_cps: Tuple[int, ...]
    glyph_names: Dict[int, str]
    # Shared monospace advance, typographic Y center, and tallest ink height.
    source_advance: int
    source_center_y: float
    source_max_height: float

    @property
    def count(self) -> int:
        return len(self.src_cps)

    def font_id(self, index: int) -> str:
        """Filename / family stem = standalone code point hex (e.g. A000)."""
        return f"{self.src_cps[index]:X}"


def is_yi_cp(cp: int) -> bool:
    return (YI_SYLLABLES[0] <= cp <= YI_SYLLABLES[1]) or (
        YI_RADICALS[0] <= cp <= YI_RADICALS[1]
    )


def font_cmap(tt: TTFont) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    for table in tt["cmap"].tables:
        if table.isUnicode():
            cmap.update(table.cmap)
    return cmap


def source_layout_metrics(tt: TTFont, sample_glyph: str) -> Tuple[int, float]:
    """Monospace advance + typographic Y center from the source face."""
    advance = int(tt["hmtx"][sample_glyph][0])
    os2 = tt["OS/2"]
    center_y = (os2.sTypoAscender + os2.sTypoDescender) / 2.0
    return advance, center_y


def inventory_max_ink_height(tt: TTFont, glyph_names: Sequence[str]) -> float:
    """Tallest outline height among ``glyph_names`` (design units)."""
    max_h = 0.0
    for gname in glyph_names:
        rec = record_glyph(tt, gname)
        if rec is None:
            continue
        bounds = recording_bounds(rec)
        if bounds is None:
            continue
        _x0, y0, _x1, y1 = bounds
        max_h = max(max_h, y1 - y0)
    return max_h


def load_inventory(source_path: str) -> YiInventory:
    tt = TTFont(source_path, fontNumber=0)
    try:
        cmap = font_cmap(tt)
        ordered: List[int] = []
        names: Dict[int, str] = {}
        for start, end in (YI_SYLLABLES, YI_RADICALS):
            for cp in range(start, end + 1):
                gname = cmap.get(cp)
                if gname is None:
                    continue
                glyf = tt["glyf"]
                if gname not in glyf:
                    continue
                g = glyf[gname]
                if not g.isComposite() and getattr(g, "numberOfContours", 0) <= 0:
                    continue
                ordered.append(cp)
                names[cp] = gname
        if not ordered:
            raise ValueError(f"No Yi glyphs found in {source_path}")
        adv, cy = source_layout_metrics(tt, names[ordered[0]])
        max_h = inventory_max_ink_height(tt, [names[cp] for cp in ordered])
        if max_h <= 0:
            raise ValueError(f"No ink bounds in {source_path}")
        return YiInventory(
            source_path=source_path,
            src_cps=tuple(ordered),
            glyph_names=names,
            source_advance=adv,
            source_center_y=cy,
            source_max_height=max_h,
        )
    finally:
        tt.close()


def record_glyph(tt: TTFont, glyph_name: str) -> Optional[RecordingPen]:
    glyf = tt["glyf"]
    if glyph_name not in glyf:
        return None
    g = glyf[glyph_name]
    if not g.isComposite() and getattr(g, "numberOfContours", 0) <= 0:
        return None
    glyph_set = tt.getGlyphSet()
    rec = DecomposingRecordingPen(glyph_set)
    try:
        glyph_set[glyph_name].draw(rec)
    except Exception:
        return None
    return rec


def recording_bounds(rec: RecordingPen) -> Optional[Bounds]:
    bpen = BoundsPen(None)
    try:
        rec.replay(bpen)
    except Exception:
        return None
    return bpen.bounds


def apply_transform(
    rec: RecordingPen,
    transform: Transform,
    *,
    reverse_winding: bool = False,
) -> TTGlyph:
    pen = TTGlyphPen(None)
    dest = ReverseContourPen(pen) if reverse_winding else pen
    rec.replay(TransformPen(dest, transform))
    glyph = pen.glyph()
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    return glyph


def _uniform_place(
    rec: RecordingPen,
    *,
    scale_x: float,
    scale_y: float,
    src_cx: float,
    src_cy: float,
    dst_cx: float,
    dst_cy: float,
) -> Optional[TTGlyph]:
    """Axis scales ``(sx, sy)``, mapping ``(src_cx, src_cy)`` → destination center."""
    if scale_x <= 0 or scale_y <= 0:
        return None
    t = Transform(
        scale_x,
        0,
        0,
        scale_y,
        dst_cx - scale_x * src_cx,
        dst_cy - scale_y * src_cy,
    )
    glyph = apply_transform(rec, t)
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    return glyph


def _fit_glyph_to_cjk_height(
    glyph: TTGlyph,
    target_upem: int,
    *,
    pad: float = STANDALONE_VERT_PAD,
) -> TTGlyph:
    """Match ink to a vertically padded CJK typo box.

    * Taller than the padded box → squash Y to that height, bottom on the floor.
    * Shorter (or equal) → translate so the ink bottom sits on the floor.

    ``pad`` is a fraction of em inset from typo ascent/descent so Yi does not
    hang on the raw -0.12em descent (which sits below typical CJK ink).
    """
    typo_bottom, typo_top, _ = ideographic_bounds(target_upem)
    inset = target_upem * max(pad, 0.0)
    bottom = typo_bottom + inset
    top = typo_top - inset
    cell_h = top - bottom
    if cell_h <= 1e-6:
        bottom, top, cell_h = typo_bottom, typo_top, typo_top - typo_bottom
    try:
        glyph.recalcBounds(None)
        y0, y1 = float(glyph.yMin), float(glyph.yMax)
    except Exception:
        return glyph
    h = y1 - y0
    if h <= 1e-6:
        return glyph
    rec = RecordingPen()
    glyph.draw(rec, None)
    if h > cell_h + 1e-6:
        sy = cell_h / h
        # y' = bottom + sy·(y - y0)
        return apply_transform(rec, Transform(1, 0, 0, sy, 0, bottom - sy * y0))
    dy = bottom - y0
    if abs(dy) < 1e-6:
        return glyph
    return apply_transform(rec, Transform(1, 0, 0, 1, 0, dy))


def make_standalone_glyph(
    rec: RecordingPen,
    target_upem: int = DEFAULT_UPEM,
    *,
    source_advance: int,
    source_center_y: float,
    source_max_height: float,
    pad: float = STANDALONE_PAD,
    widen: float = STANDALONE_CONTOUR_WIDEN,
    vert_pad: float = STANDALONE_VERT_PAD,
    stroke_weight: Optional[float] = None,  # unused; kept for call-site compat
) -> Optional[GlyphMetrics]:
    """Shared ``sx`` from advance, shared ``sy`` from inventory max ink height.

    X is placed from the monospace advance center (side bearings preserved).
    Contours are then stretched with CAPE Weightor Width mode (``widen``,
    default +15% outer width, vertical stems compensated). Then Y is fitted
    to a padded CJK typo box: squash if taller, otherwise pin the ink bottom
    to the padded floor (above raw descent).
    """
    del stroke_weight
    del source_center_y  # retained for call-site compat / inventory symmetry
    if source_advance <= 0 or source_max_height <= 0:
        return None
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    _x0, y0, _x1, y1 = bounds
    cell = target_upem * (1.0 - 2.0 * pad)
    scale_x = cell / float(source_advance)
    scale_y = cell / float(source_max_height)
    dst_cx, dst_cy = ideographic_center(target_upem)
    glyph = _uniform_place(
        rec,
        scale_x=scale_x,
        scale_y=scale_y,
        src_cx=source_advance / 2.0,
        src_cy=(y0 + y1) / 2.0,
        dst_cx=dst_cx,
        dst_cy=dst_cy,
    )
    if glyph is None:
        return None
    if widen > 0:
        glyph, _adv, _lsb = widen_ttglyph(
            glyph,
            1.0 + widen,
            advance=float(target_upem),
            center_x=dst_cx,
        )
    glyph = _fit_glyph_to_cjk_height(glyph, target_upem, pad=vert_pad)
    try:
        glyph.recalcBounds(None)
        lsb = int(glyph.xMin)
    except Exception:
        lsb = 0
    return glyph, target_upem, lsb


def make_halfwidth_glyph(
    rec: RecordingPen,
    target_upem: int = DEFAULT_UPEM,
    *,
    source_advance: int,
    source_center_y: float,
    source_max_height: float,
    pad: float = HALFWIDTH_PAD,
    stroke_weight: Optional[float] = None,  # unused; kept for call-site compat
) -> Optional[GlyphMetrics]:
    """Half-em ``sx``; same inventory-wide ``sy`` as standalones (full cell height)."""
    del stroke_weight
    del source_center_y
    if source_advance <= 0 or source_max_height <= 0:
        return None
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    _x0, y0, _x1, y1 = bounds
    adv = target_upem // 2
    cell_w = adv * (1.0 - 2.0 * pad)
    cell_h = target_upem * (1.0 - 2.0 * pad)
    scale_x = cell_w / float(source_advance)
    scale_y = cell_h / float(source_max_height)
    _, dst_cy = ideographic_center(target_upem)
    glyph = _uniform_place(
        rec,
        scale_x=scale_x,
        scale_y=scale_y,
        src_cx=source_advance / 2.0,
        src_cy=(y0 + y1) / 2.0,
        dst_cx=adv / 2.0,
        dst_cy=dst_cy,
    )
    if glyph is None:
        return None
    glyph = _fit_glyph_to_cjk_height(glyph, target_upem)
    try:
        glyph.recalcBounds(None)
        lsb = int(glyph.xMin)
    except Exception:
        lsb = 0
    return glyph, adv, lsb


def merge_halfcell_glyphs(
    left: TTGlyph,
    right: TTGlyph,
    target_upem: int = DEFAULT_UPEM,
) -> Optional[GlyphMetrics]:
    """Place two pre-fit half-cell glyphs side by side (decomposed merge)."""
    half = target_upem // 2
    pen = TTGlyphPen(None)
    ra = RecordingPen()
    left.draw(ra, None)
    ra.replay(pen)
    rb = RecordingPen()
    right.draw(rb, None)
    rb.replay(TransformPen(pen, Transform(1, 0, 0, 1, half, 0)))
    glyph = pen.glyph()
    try:
        glyph.recalcBounds(None)
    except Exception:
        pass
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    return glyph, target_upem, int(glyph.xMin)


def make_compound_glyph(
    rec_a: RecordingPen,
    rec_b: RecordingPen,
    target_upem: int = DEFAULT_UPEM,
    *,
    source_advance: int,
    source_center_y: float,
    source_max_height: float,
    pad: float = COMPOUND_PAD,
) -> Optional[GlyphMetrics]:
    """Side-by-side pair via shared half-cell scales, then merge."""
    ga = make_halfwidth_glyph(
        rec_a,
        target_upem,
        source_advance=source_advance,
        source_center_y=source_center_y,
        source_max_height=source_max_height,
        pad=pad,
    )
    gb = make_halfwidth_glyph(
        rec_b,
        target_upem,
        source_advance=source_advance,
        source_center_y=source_center_y,
        source_max_height=source_max_height,
        pad=pad,
    )
    if ga is None or gb is None:
        return None
    return merge_halfcell_glyphs(ga[0], gb[0], target_upem)


def apply_variant_recording(
    rec: RecordingPen,
    advance: int,
    target_upem: int,
    *,
    rot90_quarters: int = 0,
    flip_x: bool = False,
    flip_y: bool = False,
) -> Optional[GlyphMetrics]:
    """Center in the CJK typo box, then D4-rotate/reflect about that center."""
    bounds = recording_bounds(rec)
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    cx, cy = ideographic_center(target_upem)
    # Center first so rotations/reflections orbit the CJK midpoint.
    sx = cx - (x0 + x1) / 2.0
    sy = cy - (y0 + y1) / 2.0
    centered = RecordingPen()
    if abs(sx) > 1e-6 or abs(sy) > 1e-6:
        rec.replay(TransformPen(centered, Transform(1, 0, 0, 1, sx, sy)))
    else:
        rec.replay(centered)
    t = variant_transform(
        target_upem,
        rot90_quarters=rot90_quarters,
        flip_x=flip_x,
        flip_y=flip_y,
        center=(cx, cy),
    )
    det = t.xx * t.yy - t.xy * t.yx
    glyph = apply_transform(centered, t, reverse_winding=det < 0)
    if glyph.numberOfContours == 0 and not glyph.isComposite():
        return None
    # Asymmetric shapes can drift slightly after orthogonal maps.
    glyph = center_glyph_in_cell(glyph, target_upem, center=(cx, cy))
    try:
        glyph.recalcBounds(None)
        lsb = int(glyph.xMin)
    except Exception:
        lsb = 0
    return glyph, advance, lsb


def recording_from_metrics(gm: GlyphMetrics) -> RecordingPen:
    glyph, _adv, _lsb = gm
    rec = RecordingPen()
    glyph.draw(rec, None)
    return rec


def empty_glyph() -> TTGlyph:
    g = TTGlyph()
    g.numberOfContours = 0
    g.xMin = g.yMin = g.xMax = g.yMax = 0
    return g


def resolve_nuosu_path(in_dir: str) -> str:
    path = os.path.join(in_dir, NUOSU_FILENAME)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing Yi source font: {path}")
    return path
