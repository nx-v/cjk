"""
GlyphWiki Full Dump Resolver

Recursively resolves every glyph in the GlyphWiki dump files,
replacing all type-99 component references with actual stroke data.

For each resolved glyph, also:
  - Tracks original component references with their bounding boxes
  - Assigns a Private-Use ligature pair via ``kage.mapping`` (1→2)

Inputs:
  Scripts/dump/dump_all_versions.txt      (primary source; keeps highest @version)
  Scripts/dump/dump_newest_only.txt       (optional fill for still-missing refs)
  Scripts/dump/kanji-component-data.html  (optional component subset)

Outputs:
  Scripts/data/glyphwiki-resolved.json
  Scripts/data/glyphwiki-components.json
  Scripts/data/glyphwiki-cmap.json        (name → [SPUA marker, BMP PUA])
  Scripts/dist/glyphwiki/{marker}.ttf     (named after SPUA marker = first CP)

Usage:
  python -m Scripts.kage.extract_glyphwiki
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .build_glyphwiki_fonts import (
    build_marker_font,
    glyphs_per_file,
    group_mappings_by_marker,
)
from .engine import REFERENCE_STROKE, make_engine
from .mapping import (
    GlyphMapping,
    assign_ligatures,
    filter_alias_entries,
    filter_duplicate_stroke_entries,
    filter_empty_stroke_entries,
    filter_excluded_entries,
    filter_related_entries,
    is_empty_stroke_data,
    is_unusable_stroke_data,
    ligature_capacity,
    mapping_to_dict,
    mappings_from_cmap,
    markers_needed,
    related_allow_set,
    sort_glyph_entries,
)

ROOT = Path(__file__).resolve().parents[1]  # Scripts/
DUMP_DIR = ROOT / "dump"
DATA_DIR = ROOT / "data"
FONT_DIR = ROOT / "dist" / "glyphwiki"

HTML_PATH = DUMP_DIR / "kanji-component-data.html"
DUMP_PATH = DUMP_DIR / "dump_all_versions.txt"
NEWEST_PATH = DUMP_DIR / "dump_newest_only.txt"
RESOLVED_PATH = DATA_DIR / "glyphwiki-resolved.json"
COMPONENTS_PATH = DATA_DIR / "glyphwiki-components.json"
CMAP_PATH = DATA_DIR / "glyphwiki-cmap.json"

MAX_DEPTH = 20


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ComponentRef = dict[str, Any]  # {n: name, b: [x1,y1,x2,y2]}
ResolvedGlyph = dict[str, Any]  # {d, r, n, cp?}


# ---------------------------------------------------------------------------
# Step 1: optional component subset from HTML
# ---------------------------------------------------------------------------

def extract_component_names(html_path: Path) -> set[str]:
    if not html_path.is_file():
        print(f"  (no {html_path.name} — skipping component subset)")
        return set()

    try:
        from html.parser import HTMLParser
    except ImportError:
        return set()

    names: set[str] = set()

    class WikiLinkParser(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag != "a":
                return
            attr = dict(attrs)
            classes = (attr.get("class") or "").split()
            title = attr.get("title")
            if "wiki" in classes and title:
                names.add(title.strip())

    print("Parsing kanji-component-data.html for component subset...")
    WikiLinkParser().feed(html_path.read_text(encoding="utf-8"))
    print(f"  Found {len(names)} component names")
    return names


# ---------------------------------------------------------------------------
# Step 2: load dumps
# ---------------------------------------------------------------------------

def parse_versioned_name(raw_name: str) -> tuple[str, int]:
    """Split ``name\\@N`` / ``name@N`` into ``(base_name, version)``."""
    at_idx = raw_name.find("\\@")
    if at_idx >= 0:
        base = raw_name[:at_idx]
        try:
            return base, int(raw_name[at_idx + 2 :] or 0)
        except ValueError:
            return base, 0
    plain_at = raw_name.find("@")
    if plain_at >= 0:
        base = raw_name[:plain_at]
        try:
            return base, int(raw_name[plain_at + 1 :] or 0)
        except ValueError:
            return base, 0
    return raw_name, 0


def load_dump(
    path: Path,
    label: str,
    *,
    parse_versions: bool = False,
) -> dict[str, str]:
    print(f"\nLoading {label}...")
    glyphs: dict[str, str] = {}
    version_tracker: dict[str, int] | None = {} if parse_versions else None
    line_count = 0

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line_count += 1
            if line_count <= 2:
                continue  # header + separator

            first_pipe = line.find("|")
            if first_pipe < 0:
                continue
            raw_name = line[:first_pipe].strip()
            if not raw_name:
                continue
            second_pipe = line.find("|", first_pipe + 1)
            if second_pipe < 0:
                continue
            data = line[second_pipe + 1 :].strip()
            if not data:
                continue

            if parse_versions and version_tracker is not None:
                base_name, version = parse_versioned_name(raw_name)
                existing = version_tracker.get(base_name, -1)
                if version > existing:
                    version_tracker[base_name] = version
                    glyphs[base_name] = data
                versioned_key = f"{base_name}@{version}"
                glyphs.setdefault(versioned_key, data)
            else:
                glyphs[raw_name] = data

            if line_count % 500_000 == 0:
                print(f"\r  {line_count} lines ({len(glyphs)} glyphs)", end="", flush=True)

    print(f"\r  Done: {line_count} lines, {len(glyphs)} glyphs")
    return glyphs


def load_dump_subset(
    names: set[str],
    *,
    paths: Sequence[Path] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Stream dump files for ``names`` only → ``(raw_data, related)``."""
    need = set(names)
    glyphs: dict[str, str] = {}
    related: dict[str, str] = {}
    if not need:
        return glyphs, related
    scan = list(paths) if paths is not None else [NEWEST_PATH, DUMP_PATH]
    for path in scan:
        if not path.is_file() or not need:
            continue
        print(f"  Scanning {path.name} for {len(need):,} remaining names...")
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line_count, line in enumerate(fh, 1):
                if line_count <= 2:
                    continue
                first_pipe = line.find("|")
                if first_pipe < 0:
                    continue
                raw_name = line[:first_pipe].strip()
                if not raw_name:
                    continue
                base = raw_name.split("@", 1)[0]
                if base not in need and raw_name not in need:
                    continue
                second_pipe = line.find("|", first_pipe + 1)
                if second_pipe < 0:
                    continue
                rel = line[first_pipe + 1 : second_pipe].strip() or base
                data = line[second_pipe + 1 :].strip()
                if not data:
                    continue
                if base in need and base not in glyphs:
                    glyphs[base] = data
                    related[base] = rel
                    need.discard(base)
                if raw_name in need and raw_name not in glyphs:
                    glyphs[raw_name] = data
                    related[raw_name] = rel
                    need.discard(raw_name)
                if not need:
                    break
        print(f"    have {len(glyphs):,}; still missing {len(need):,}")
    return glyphs, related


def remap_cmap_drop_empty_alias_dups(*, no_filters: bool = False) -> int:
    """Re-filter the existing cmap using resolved strokes (+ dump for aliases).

    Faster than a full ``--cmap-only`` dump load: only touches cmap names.
    With ``no_filters``, only drop redundant aliases (skip exclusion/empty/dedupe).
    """
    if not CMAP_PATH.is_file() or not RESOLVED_PATH.is_file():
        print(
            f"Fatal: need {CMAP_PATH.name} and {RESOLVED_PATH.name}",
            file=sys.stderr,
        )
        return 1

    print(f"Loading {CMAP_PATH.name}...")
    cmap_obj = load_json_object(CMAP_PATH)
    names = set(cmap_obj)
    print(f"  {len(names):,} cmap entries")

    strokes = load_stroke_data_for_names(RESOLVED_PATH, names)
    print(f"Scanning dump for raw data / related ({len(names):,} names)...")
    raw_glyphs, related_map = load_dump_subset(names)
    # Prefer dump raw for alias detection; fall back to resolved string.
    for n in names:
        raw_glyphs.setdefault(n, strokes.get(n, ""))
        # Never invent a fake ``uXXXX`` related from the glyph name — that
        # hijacks ligature sort order (e.g. name ``u0378`` → CP U+0378).
        if n not in related_map:
            related_map[n] = n if not n.lower().startswith("u") else f"zz:{n}"

    entries = [(n, related_map.get(n, n)) for n in names]
    if no_filters:
        before = len(entries)
        entries = filter_alias_entries(entries, raw_glyphs)
        alias_n = before - len(entries)
        print(
            f"After alias filter (--no-filters): {len(entries):,} kept "
            f"(alias {alias_n:,}; exclusion/empty/dedupe skipped)"
        )
        empty_n = dup_n = 0
    else:
        before = len(entries)
        entries = filter_excluded_entries(entries, raw_glyphs)
        print(
            f"After name/overlay exclusion: {len(entries):,} kept, "
            f"{before - len(entries):,} excluded"
        )

        before = len(entries)
        entries = filter_empty_stroke_entries(entries, strokes)
        # Also drop dump-empty when resolved missing
        entries = [
            (n, r)
            for n, r in entries
            if not is_unusable_stroke_data(strokes.get(n, raw_glyphs.get(n)))
        ]
        empty_n = before - len(entries)

        before = len(entries)
        entries = filter_alias_entries(entries, raw_glyphs)
        alias_n = before - len(entries)

        stroke_counts = {
            n: count_strokes(strokes[n])
            for n, _ in entries
            if n in strokes and not is_unusable_stroke_data(strokes.get(n))
        }
        before = len(entries)
        entries = filter_duplicate_stroke_entries(
            entries,
            strokes,
            raw_glyphs=raw_glyphs,
            stroke_counts=stroke_counts,
        )
        dup_n = before - len(entries)
        print(
            f"After empty/alias/duplicate filter: {len(entries):,} kept "
            f"(empty {empty_n:,}, alias {alias_n:,}, dup/empty-resolved {dup_n:,})"
        )

    print(f"  SPUA markers needed: {markers_needed(len(entries)):,}")

    if not entries:
        print("Fatal: no glyphs left after dedupe", file=sys.stderr)
        return 1

    stroke_counts = {
        n: count_strokes(strokes[n])
        for n, _ in entries
        if n in strokes and not is_unusable_stroke_data(strokes.get(n))
    }
    print(f"\nAssigning PUA ligatures to {len(entries)} glyphs...")
    try:
        mappings = assign_ligatures(entries, stroke_counts)
    except ValueError as exc:
        print(f"Fatal: {exc}", file=sys.stderr)
        return 1
    cmap = mapping_to_dict(mappings)
    write_json_object(CMAP_PATH, cmap)
    first, last = mappings[0], mappings[-1]
    print(
        f"  First: {first.name} -> U+{first.marker:X} U+{first.pua:X} "
        f"(related {first.related}, strokes {stroke_counts.get(first.name, 0)})"
    )
    print(
        f"  Last:  {last.name} -> U+{last.marker:X} U+{last.pua:X} "
        f"(related {last.related}, strokes {stroke_counts.get(last.name, 0)})"
    )
    print("\n=== cmap remap (empty/alias/dedupe) complete ===")
    print(f"  Cmap: {CMAP_PATH}")
    return 0


# ---------------------------------------------------------------------------
# KAGE coordinate transformation (resolver-local; matches upstream dump tool)
# ---------------------------------------------------------------------------

def stretch(dp: float, sp: float, p: float, _min: float, _max: float) -> float:
    if dp == 0 and sp == 0:
        return p
    if dp >= sp:
        if p < sp + 100:
            d1 = int((dp - sp) // (sp + 100))
            d2 = (dp - sp) % (sp + 100)
            return p + d1 + 1 if p < d2 else p + d1
        return p + dp - sp
    if p > sp + 100:
        return p + dp - sp
    d3 = int(((sp + 100 - p) * (sp - dp)) / (sp + 100))
    return p - d3


def get_bounding_box(strokes: list[list[float]]) -> dict[str, float]:
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for s in strokes:
        for i in range(3, min(len(s), 11), 2):
            x = s[i]
            y = s[i + 1] if i + 1 < len(s) else None
            if x is None or y is None:
                continue
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
    if min_x == float("inf"):
        return {"minX": 0, "minY": 0, "maxX": 200, "maxY": 200}
    return {"minX": min_x, "minY": min_y, "maxX": max_x, "maxY": max_y}


def transform_stroke(
    cols: list[float],
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    sx: float,
    sy: float,
    sx2: float,
    sy2: float,
    box: dict[str, float],
) -> list[float]:
    t = list(cols)
    if sx != 0 or sy != 0:
        local_sx, local_sy = sx, sy
        local_sx2, local_sy2 = sx2, sy2
        if local_sx > 100:
            local_sx -= 200
        else:
            local_sx2 = 0
            local_sy2 = 0
        for i in range(3, min(len(t), 10), 2):
            t[i] = stretch(local_sx, local_sx2, t[i], box["minX"], box["maxX"])
        for i in range(4, min(len(t), 11), 2):
            t[i] = stretch(local_sy, local_sy2, t[i], box["minY"], box["maxY"])

    for i in range(3, min(len(t), 10), 2):
        t[i] = round(x1 + (t[i] * (x2 - x1)) / 200)
    for i in range(4, min(len(t), 11), 2):
        t[i] = round(y1 + (t[i] * (y2 - y1)) / 200)
    return t


# ---------------------------------------------------------------------------
# Stroke parsing / resolution
# ---------------------------------------------------------------------------

def parse_strokes(data: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for segment in data.split("$"):
        if not segment:
            continue
        parts = segment.split(":")
        try:
            typ = int(float(parts[0]))
        except (ValueError, IndexError):
            continue
        if typ == REFERENCE_STROKE:
            def num(i: int) -> float:
                try:
                    return float(parts[i]) if i < len(parts) else 0.0
                except ValueError:
                    return 0.0

            out.append(
                {
                    "cols": [
                        99,
                        num(1),
                        num(2),
                        num(3),
                        num(4),
                        num(5),
                        num(6),
                        0,
                        0,
                        num(9),
                        num(10),
                    ],
                    "refName": parts[7] if len(parts) > 7 else None,
                }
            )
        else:
            cols: list[float] = []
            for p in parts:
                try:
                    cols.append(float(p))
                except ValueError:
                    cols.append(0.0)
            out.append({"cols": cols})
    return out


def extract_component_refs(data: str) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    for segment in data.split("$"):
        if not segment:
            continue
        parts = segment.split(":")
        try:
            if int(float(parts[0])) != REFERENCE_STROKE:
                continue
        except (ValueError, IndexError):
            continue
        name = parts[7] if len(parts) > 7 else ""
        if not name:
            continue

        def num(i: int) -> float:
            try:
                return float(parts[i]) if i < len(parts) else 0.0
            except ValueError:
                return 0.0

        refs.append(
            {"n": name, "b": [num(3), num(4), num(5), num(6)]}
        )
    return refs


def collect_component_closure(
    seeds: set[str],
    all_glyphs: dict[str, str],
) -> set[str]:
    """Transitive type-99 dependency closure of ``seeds`` inside ``all_glyphs``."""
    needed: set[str] = set()
    stack = list(seeds)
    while stack:
        name = stack.pop()
        if name in needed:
            continue
        data = all_glyphs.get(name)
        if data is None:
            continue
        needed.add(name)
        for ref in extract_component_refs(data):
            rn = ref["n"]
            if rn not in needed and rn in all_glyphs:
                stack.append(rn)
    return needed


def resolve_glyph(
    data: str,
    all_glyphs: dict[str, str],
    depth: int = 0,
    visited: set[str] | None = None,
    missing_refs: set[str] | None = None,
) -> list[list[float]]:
    if depth > MAX_DEPTH:
        return []
    if visited is None:
        visited = set()

    result: list[list[float]] = []
    for item in parse_strokes(data):
        cols: list[float] = item["cols"]
        ref_name: str | None = item.get("refName")
        if cols[0] != REFERENCE_STROKE:
            result.append(cols)
            continue
        if not ref_name or ref_name in visited:
            continue
        buhin_data = all_glyphs.get(ref_name)
        if not buhin_data:
            if missing_refs is not None:
                missing_refs.add(ref_name)
            continue
        visited.add(ref_name)
        inner = resolve_glyph(
            buhin_data, all_glyphs, depth + 1, set(visited), missing_refs
        )
        if not inner:
            continue
        box = get_bounding_box(inner)
        x1, y1, x2, y2 = cols[3], cols[4], cols[5], cols[6]
        sx, sy, sx2, sy2 = cols[1], cols[2], cols[9], cols[10]
        for stroke in inner:
            result.append(
                transform_stroke(stroke, x1, y1, x2, y2, sx, sy, sx2, sy2, box)
            )
    return result


def strokes_to_string(strokes: list[list[float]]) -> str:
    parts: list[str] = []
    for s in strokes:
        end = len(s) - 1
        while end > 6 and s[end] == 0:
            end -= 1
        parts.append(
            ":".join(
                str(int(v)) if float(v).is_integer() else str(v)
                for v in s[: end + 1]
            )
        )
    return "$".join(parts)


def count_strokes(data: str) -> int:
    return sum(1 for seg in data.split("$") if seg)


def count_expanded_strokes(
    name: str,
    all_glyphs: dict[str, str],
    cache: dict[str, int] | None = None,
    visiting: set[str] | None = None,
) -> int:
    """Leaf stroke count after expanding type-99 refs (no geometry)."""
    if cache is not None and name in cache:
        return cache[name]
    if visiting is None:
        visiting = set()
    if name in visiting:
        return 0
    data = all_glyphs.get(name)
    if not data:
        if cache is not None:
            cache[name] = 0
        return 0
    visiting.add(name)
    n = 0
    for item in parse_strokes(data):
        cols = item["cols"]
        if cols and int(cols[0]) == REFERENCE_STROKE:
            ref = item.get("refName")
            if ref:
                n += count_expanded_strokes(ref, all_glyphs, cache, set(visiting))
        else:
            n += 1
    visiting.discard(name)
    if cache is not None:
        cache[name] = n
    return n


def stroke_counts_for_names(
    names: Iterable[str],
    all_glyphs: dict[str, str],
) -> dict[str, int]:
    """Expanded stroke counts for cmap assignment / sort keys."""
    cache: dict[str, int] = {}
    return {n: count_expanded_strokes(n, all_glyphs, cache) for n in names}


# ---------------------------------------------------------------------------
# Resolve all + iterative missing-ref fill
# ---------------------------------------------------------------------------

def resolve_all_glyphs(
    all_glyphs: dict[str, str],
) -> tuple[dict[str, ResolvedGlyph], set[str], list[tuple[str, str]]]:
    """Returns resolved map, missing refs, and (name, related) pairs for cmap."""
    resolved: dict[str, ResolvedGlyph] = {}
    global_missing: set[str] = set()
    # related is recovered while loading — stash empty; filled by caller via
    # a parallel related map if available. Here we keep name-only order input
    # as (name, name) fallback; main() passes real relateds.
    name_related: list[tuple[str, str]] = []

    total = len(all_glyphs)
    done = resolved_ok = fallback_count = 0
    print(f"\nResolving {total} glyphs...")

    for name, raw_data in all_glyphs.items():
        done += 1
        refs = extract_component_refs(raw_data)
        local_missing: set[str] = set()
        strokes = resolve_glyph(raw_data, all_glyphs, 0, None, local_missing)
        global_missing |= local_missing

        if strokes:
            stroke_data = strokes_to_string(strokes)
            resolved_ok += 1
        else:
            stroke_data = raw_data
            fallback_count += 1

        resolved[name] = {
            "d": stroke_data,
            "r": refs,
            "n": count_strokes(stroke_data),
        }
        name_related.append((name, name))  # placeholder; overwritten in main

        if done % 5000 == 0:
            print(
                f"\r  {done}/{total} ({resolved_ok} ok, {fallback_count} fallback)",
                end="",
                flush=True,
            )

    print(
        f"\r  Done: {resolved_ok} resolved, {fallback_count} fallback, "
        f"{len(global_missing)} missing refs"
    )
    return resolved, global_missing, name_related


def iteratively_resolve_missing(
    all_glyphs: dict[str, str],
    resolved: dict[str, ResolvedGlyph],
    missing_refs: set[str],
) -> None:
    max_iterations = 5
    for iteration in range(1, max_iterations + 1):
        if not missing_refs:
            break
        print(
            f"\nIteration {iteration}: scanning newest-only for "
            f"{len(missing_refs)} missing refs..."
        )
        if not NEWEST_PATH.is_file():
            print("  dump_newest_only.txt not found — stopping")
            break

        newest = load_dump(
            NEWEST_PATH,
            f"newest-only (iter {iteration}, targeted)",
            parse_versions=False,
        )
        added = 0
        for name in list(missing_refs):
            data = newest.get(name)
            if data and name not in all_glyphs:
                all_glyphs[name] = data
                added += 1
        if added == 0:
            print("  No new entries found — stopping")
            break
        print(f"  Added {added} new entries, re-resolving affected glyphs...")

        new_missing: set[str] = set()
        improved = 0
        for name, raw_data in all_glyphs.items():
            entry = resolved.get(name)
            if not entry:
                continue
            had_missing = any(ref["n"] in missing_refs for ref in entry["r"])
            if not had_missing and "99:" not in entry["d"]:
                continue
            local_missing: set[str] = set()
            strokes = resolve_glyph(raw_data, all_glyphs, 0, None, local_missing)
            new_missing |= local_missing
            if strokes:
                new_data = strokes_to_string(strokes)
                if new_data != entry["d"]:
                    resolved[name] = {
                        "d": new_data,
                        "r": entry["r"],
                        "n": count_strokes(new_data),
                    }
                    improved += 1
        print(f"  Improved {improved} glyphs, {len(new_missing)} still missing")
        brand_new = {m for m in new_missing if m not in all_glyphs}
        if not brand_new:
            print("  No new reference names — stopping")
            break
        missing_refs = brand_new


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_json_object(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting {len(obj)} entries to {path}...")
    with path.open("w", encoding="utf-8") as fh:
        fh.write("{\n")
        keys = sorted(obj)
        for i, key in enumerate(keys):
            line = f"{json.dumps(key, ensure_ascii=False)}:{json.dumps(obj[key], ensure_ascii=False)}"
            fh.write(line)
            fh.write(",\n" if i < len(keys) - 1 else "\n")
        fh.write("}\n")
    print(f"  Done: {path}")


def load_json_object(path: Path) -> dict[str, Any]:
    print(f"\nLoading {path}...")
    with path.open(encoding="utf-8") as fh:
        obj = json.load(fh)
    print(f"  Done: {len(obj):,} entries")
    return obj


def load_stroke_data_for_names(
    resolved_path: Path,
    names: set[str],
) -> dict[str, str]:
    """Load ``name → stroke string`` for ``names`` from resolved JSON.

    Streams the custom one-entry-per-line format when possible; falls back
    to a full ``json.load`` if the file is a single compact blob.
    """
    print(f"\nLoading stroke data for {len(names):,} cmap names from {resolved_path}...")
    out: dict[str, str] = {}
    # Fast path: line-oriented object written by write_json_object
    with resolved_path.open(encoding="utf-8") as fh:
        first = fh.readline()
        if first.strip() != "{":
            fh.seek(0)
            full = json.load(fh)
            for name in names:
                entry = full.get(name)
                if isinstance(entry, dict) and "d" in entry:
                    out[name] = entry["d"]
            print(f"  Done: {len(out):,} strokes (full JSON)")
            return out
        for line in fh:
            line = line.strip()
            if not line or line == "}":
                continue
            if line.endswith(","):
                line = line[:-1]
            try:
                key_json, _, rest = line.partition(":")
                key = json.loads(key_json)
                if key not in names:
                    continue
                entry = json.loads(rest)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and "d" in entry:
                out[key] = entry["d"]
                if len(out) >= len(names):
                    break
    print(f"  Done: {len(out):,} strokes")
    return out


def resolve_names_to_strokes(
    names: Iterable[str],
    all_glyphs: dict[str, str],
) -> dict[str, str]:
    """Resolve type-99 refs for ``names`` using ``all_glyphs`` as the pool."""
    out: dict[str, str] = {}
    for name in names:
        raw = all_glyphs.get(name)
        if raw is None:
            continue
        strokes = resolve_glyph(raw, all_glyphs)
        out[name] = strokes_to_string(strokes) if strokes else raw
    return out


def strokes_for_entries(
    entries: Sequence[tuple[str, str]],
    all_glyphs: dict[str, str],
    *,
    resolved_path: Path | None = None,
) -> dict[str, str]:
    """Resolved stroke strings for cmap candidates (cache file + live resolve)."""
    names = {n for n, _ in entries}
    strokes: dict[str, str] = {}
    path = resolved_path if resolved_path is not None else RESOLVED_PATH
    if path.is_file():
        strokes = load_stroke_data_for_names(path, names)
    missing = names - strokes.keys()
    if missing:
        print(f"  Resolving {len(missing):,} glyphs missing from cache...")
        strokes.update(resolve_names_to_strokes(missing, all_glyphs))
    return strokes


def dedupe_pack_entries(
    entries: list[tuple[str, str]],
    all_glyphs: dict[str, str],
    *,
    resolved_path: Path | None = None,
    aliases_only: bool = False,
) -> list[tuple[str, str]]:
    """Drop empties, redundant aliases, and duplicate resolved outlines.

    With ``aliases_only``, skip empty/duplicate filters and only drop
    full-frame aliases whose target is also packed.
    """
    if not aliases_only:
        before = len(entries)
        entries = filter_empty_stroke_entries(entries, all_glyphs)
        empty_dump = before - len(entries)
    else:
        empty_dump = 0

    before = len(entries)
    entries = filter_alias_entries(entries, all_glyphs)
    aliases = before - len(entries)

    if aliases_only:
        print(
            f"After alias filter (--no-filters): {len(entries):,} kept "
            f"(alias {aliases:,}; empty/dedupe skipped)"
        )
        return entries

    print(
        f"Loading resolved strokes for dedupe ({len(entries):,} candidates)..."
    )
    strokes = strokes_for_entries(
        entries, all_glyphs, resolved_path=resolved_path
    )
    stroke_counts = {
        n: count_strokes(strokes[n])
        for n, _ in entries
        if n in strokes and not is_unusable_stroke_data(strokes.get(n))
    }
    before = len(entries)
    entries = filter_duplicate_stroke_entries(
        entries,
        strokes,
        raw_glyphs=all_glyphs,
        stroke_counts=stroke_counts,
    )
    dups = before - len(entries)
    print(
        f"After empty/alias/duplicate filter: {len(entries):,} kept "
        f"(empty dump {empty_dump:,}, alias {aliases:,}, "
        f"empty/dup resolved {dups:,})"
    )
    return entries


def _build_marker_task(
    marker: int,
    mappings: list[GlyphMapping],
    stroke_data: dict[str, str],
    out_path: str,
    include_mirrors: bool,
    curve_fit: bool = False,
) -> tuple[int, int, int]:
    """Process-pool worker: build one marker font. Returns (marker, rendered, total)."""
    rendered, total = build_marker_font(
        marker,
        mappings,
        stroke_data,
        Path(out_path),
        include_mirrors=include_mirrors,
        curve_fit=curve_fit,
    )
    return marker, rendered, total


def build_fonts_from_mappings(
    mappings: list[GlyphMapping],
    stroke_data: dict[str, str],
    *,
    font_markers: int = 0,
    include_mirrors: bool = True,
    curve_fit: bool = False,
    jobs: int = 1,
) -> None:
    per_file = glyphs_per_file(include_mirrors=include_mirrors)
    mirror_note = "with mirrors" if include_mirrors else "no mirrors"
    fit_note = "curve-fit" if curve_fit else "polygon"
    grouped = group_mappings_by_marker(mappings)
    markers = sorted(grouped)
    if font_markers > 0:
        markers = markers[: font_markers]
    FONT_DIR.mkdir(parents=True, exist_ok=True)

    jobs = max(1, jobs)
    print(
        f"\nBuilding GlyphWiki fonts ({per_file} glyphs each, {mirror_note}, "
        f"{fit_note}, jobs={jobs}) -> {FONT_DIR}"
    )

    if jobs == 1:
        kage = make_engine()
        for i, marker in enumerate(markers, 1):
            out = FONT_DIR / f"{marker:X}.ttf"
            print(
                f"  [{i}/{len(markers)}] U+{marker:X} "
                f"({len(grouped[marker])} ligatures) -> {out.name}"
            )
            rendered, total = build_marker_font(
                marker,
                grouped[marker],
                stroke_data,
                out,
                kage=kage,
                include_mirrors=include_mirrors,
                curve_fit=curve_fit,
            )
            print(f"      rendered {rendered}, glyphs in file {total}")
        return

    from concurrent.futures import ProcessPoolExecutor, as_completed

    tasks = []
    for marker in markers:
        # Only ship strokes this marker needs (keeps pickles smaller)
        names = {m.name for m in grouped[marker]}
        subset = {n: stroke_data[n] for n in names if n in stroke_data}
        out = str(FONT_DIR / f"{marker:X}.ttf")
        tasks.append(
            (marker, list(grouped[marker]), subset, out, include_mirrors, curve_fit)
        )

    done = 0
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(_build_marker_task, *task): task[0] for task in tasks
        }
        for fut in as_completed(futures):
            done += 1
            marker, rendered, total = fut.result()
            print(
                f"  [{done}/{len(markers)}] U+{marker:X} done — "
                f"rendered {rendered}, glyphs in file {total}",
                flush=True,
            )


def resolve_and_build_pipelined(
    mappings: list[GlyphMapping],
    all_glyphs: dict[str, str],
    *,
    font_markers: int = 0,
    include_mirrors: bool = True,
    curve_fit: bool = False,
    jobs: int = 1,
) -> dict[str, str]:
    """Resolve each marker's glyphs, overlapping font builds when ``jobs > 1``.

    Returns stroke data for all cmap names that were processed.
    """
    per_file = glyphs_per_file(include_mirrors=include_mirrors)
    mirror_note = "with mirrors" if include_mirrors else "no mirrors"
    fit_note = "curve-fit" if curve_fit else "polygon"
    grouped = group_mappings_by_marker(mappings)
    markers = sorted(grouped)
    if font_markers > 0:
        markers = markers[: font_markers]
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    jobs = max(1, jobs)

    print(
        f"\nPipelined resolve+build ({per_file} glyphs each, {mirror_note}, "
        f"{fit_note}, jobs={jobs}) -> {FONT_DIR}"
    )

    all_strokes: dict[str, str] = {}

    if jobs == 1:
        kage = make_engine()
        for i, marker in enumerate(markers, 1):
            names = {m.name for m in grouped[marker]}
            print(
                f"  [{i}/{len(markers)}] resolve U+{marker:X} ({len(names)} glyphs)...",
                flush=True,
            )
            strokes = resolve_names_to_strokes(names, all_glyphs)
            all_strokes.update(strokes)
            out = FONT_DIR / f"{marker:X}.ttf"
            print(f"      build {out.name}...", flush=True)
            rendered, total = build_marker_font(
                marker,
                grouped[marker],
                strokes,
                out,
                kage=kage,
                include_mirrors=include_mirrors,
                curve_fit=curve_fit,
            )
            print(f"      rendered {rendered}, glyphs in file {total}", flush=True)
        return all_strokes

    from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED

    pending: dict = {}
    finished = 0
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for i, marker in enumerate(markers, 1):
            names = {m.name for m in grouped[marker]}
            print(
                f"  [{i}/{len(markers)}] resolve U+{marker:X} ({len(names)} glyphs)...",
                flush=True,
            )
            strokes = resolve_names_to_strokes(names, all_glyphs)
            all_strokes.update(strokes)
            out = str(FONT_DIR / f"{marker:X}.ttf")
            fut = pool.submit(
                _build_marker_task,
                marker,
                list(grouped[marker]),
                strokes,
                out,
                include_mirrors,
                curve_fit,
            )
            pending[fut] = marker
            print(f"      queued build {Path(out).name} ({len(pending)} in flight)", flush=True)

            while len(pending) >= jobs:
                done_set, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                for fut in done_set:
                    mk = pending.pop(fut)
                    _marker, rendered, total = fut.result()
                    finished += 1
                    print(
                        f"  [build {finished}/{len(markers)}] U+{mk:X} done — "
                        f"rendered {rendered}, glyphs {total}",
                        flush=True,
                    )

        while pending:
            done_set, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
            for fut in done_set:
                mk = pending.pop(fut)
                _marker, rendered, total = fut.result()
                finished += 1
                print(
                    f"  [build {finished}/{len(markers)}] U+{mk:X} done — "
                    f"rendered {rendered}, glyphs {total}",
                    flush=True,
                )

    return all_strokes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_dump_with_related(
    path: Path,
    label: str,
    *,
    parse_versions: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """Like load_dump, but also returns name → related."""
    print(f"\nLoading {label}...")
    glyphs: dict[str, str] = {}
    related: dict[str, str] = {}
    version_tracker: dict[str, int] | None = {} if parse_versions else None
    line_count = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line_count += 1
            if line_count <= 2:
                continue
            first = line.find("|")
            if first < 0:
                continue
            second = line.find("|", first + 1)
            if second < 0:
                continue
            raw_name = line[:first].strip()
            rel = line[first + 1 : second].strip()
            data = line[second + 1 :].strip()
            if not raw_name or not data:
                continue

            if parse_versions and version_tracker is not None:
                base_name, version = parse_versioned_name(raw_name)
                existing = version_tracker.get(base_name, -1)
                if version > existing:
                    version_tracker[base_name] = version
                    glyphs[base_name] = data
                    related[base_name] = rel or base_name
                versioned_key = f"{base_name}@{version}"
                glyphs.setdefault(versioned_key, data)
                related.setdefault(versioned_key, rel or base_name)
            else:
                glyphs[raw_name] = data
                related[raw_name] = rel or raw_name

            if line_count % 500_000 == 0:
                print(f"\r  {line_count} lines ({len(glyphs)} glyphs)", end="", flush=True)
    print(f"\r  Done: {line_count} lines, {len(glyphs)} glyphs")
    return glyphs, related


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only resolve the first N glyphs (smoke test)",
    )
    parser.add_argument(
        "--skip-newest-fallback",
        action="store_true",
        help="Do not fill missing refs from dump_newest_only.txt",
    )
    parser.add_argument(
        "--cmap-only",
        action="store_true",
        help="Only build the ligature cmap (no resolution / fonts)",
    )
    parser.add_argument(
        "--skip-fonts",
        action="store_true",
        help="Resolve JSON only; do not build per-marker TTFs",
    )
    parser.add_argument(
        "--font-markers",
        type=int,
        default=0,
        help="Only build the first N SPUA-marker font files (0 = all)",
    )
    parser.add_argument(
        "--from-resolved",
        action="store_true",
        help=(
            "Skip dump load/resolve; build fonts from existing "
            "glyphwiki-cmap.json + glyphwiki-resolved.json"
        ),
    )
    parser.add_argument(
        "--no-mirrors",
        action="store_true",
        help="Omit D4 variant outlines and rlig (identity forms only)",
    )
    parser.add_argument(
        "--curve-fit",
        action="store_true",
        help=(
            "Schneider-fit long polygonal stroke ribbons to cubics before "
            "TrueType conversion (small tip polygons stay sharp)"
        ),
    )
    parser.add_argument(
        "--no-filters",
        action="store_true",
        help=(
            "Skip related-CP, overlay/name, empty, and duplicate filters; "
            "still drop full-frame aliases whose target is also packed"
        ),
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help=(
            "Overlap per-marker resolve with font builds, and build multiple "
            "marker fonts concurrently (see --jobs)"
        ),
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=0,
        help=(
            "Worker processes for font builds (default: 1, or CPU count with "
            "--parallel). Use 0 to mean CPU count."
        ),
    )
    args = parser.parse_args(argv)
    include_mirrors = not args.no_mirrors
    curve_fit = bool(args.curve_fit)
    per_file = glyphs_per_file(include_mirrors=include_mirrors)

    import os

    if args.jobs < 0:
        print("Fatal: --jobs must be >= 0", file=sys.stderr)
        return 1
    if args.parallel:
        jobs = args.jobs if args.jobs > 0 else max(1, os.cpu_count() or 4)
    elif args.jobs > 0:
        jobs = args.jobs
        args.parallel = True  # explicit -j implies parallel builds
    else:
        jobs = 1

    print("=== GlyphWiki Full Dump Resolver ===\n")
    print(f"Ligature capacity: {ligature_capacity():,} pairs (SPUA x {6400} BMP PUA)")
    print(
        f"Per-marker font size: {per_file:,} glyphs "
        f"({'6400 PUA + 6400 identity' if args.no_mirrors else '6400 PUA + 6400*8 D4 variants'})"
    )
    print(f"Parallel: {'yes' if args.parallel else 'no'} (jobs={jobs})")
    print("Bucket D4: unicode + VS01..VS08 (U+E000..U+E007), no SPUA marker")
    print(
        f"Curve fit: {'on (Schneider ribbons)' if curve_fit else 'off (keep polygons)'}"
    )
    if args.no_filters:
        print("Filters: aliases only (--no-filters)")

    # --- fast path: fonts from already-resolved JSON ---
    if args.from_resolved:
        if not CMAP_PATH.is_file() or not RESOLVED_PATH.is_file():
            print(
                f"Fatal: --from-resolved needs {CMAP_PATH.name} and "
                f"{RESOLVED_PATH.name} under {DATA_DIR}",
                file=sys.stderr,
            )
            return 1
        if args.cmap_only:
            # Re-apply empty/alias/duplicate filters without a full dump load.
            return remap_cmap_drop_empty_alias_dups(no_filters=args.no_filters)
        cmap_obj = load_json_object(CMAP_PATH)
        mappings = mappings_from_cmap(cmap_obj)
        if args.limit > 0:
            mappings = sorted(
                mappings,
                key=lambda m: (m.marker, m.pua),
            )[: args.limit]
            print(f"Limited to {len(mappings)} cmap entries")
        if not mappings:
            print("Fatal: cmap is empty", file=sys.stderr)
            return 1
        first, last = mappings[0], mappings[-1]
        print(
            f"  First: {first.name} -> U+{first.marker:X} U+{first.pua:X}"
        )
        print(
            f"  Last:  {last.name} -> U+{last.marker:X} U+{last.pua:X}"
        )
        if args.skip_fonts:
            print("\n=== from-resolved: nothing to build (--skip-fonts) ===")
            return 0
        names = {m.name for m in mappings}
        stroke_data = load_stroke_data_for_names(RESOLVED_PATH, names)
        build_fonts_from_mappings(
            mappings,
            stroke_data,
            font_markers=args.font_markers,
            include_mirrors=include_mirrors,
            curve_fit=curve_fit,
            jobs=jobs,
        )
        print("\n=== Font build from resolved data complete ===")
        print(f"  Cmap:  {CMAP_PATH}")
        print(f"  Fonts: {FONT_DIR}")
        return 0

    component_names = extract_component_names(HTML_PATH)

    if not DUMP_PATH.is_file():
        print(f"Fatal: missing {DUMP_PATH}", file=sys.stderr)
        return 1

    all_glyphs, related_map = load_dump_with_related(
        DUMP_PATH, "dump_all_versions.txt (primary)", parse_versions=True
    )

    if not args.skip_newest_fallback and NEWEST_PATH.is_file():
        newest = load_dump(NEWEST_PATH, "dump_newest_only.txt (gap fill)")
        filled = 0
        for name, data in newest.items():
            if name not in all_glyphs:
                all_glyphs[name] = data
                related_map.setdefault(name, name)
                filled += 1
        print(f"\nFilled {filled} gaps from newest-only")
        newest.clear()
    else:
        print("\n(skipping newest-only gap fill)")

    print(f"Total glyphs in resolution pool: {len(all_glyphs)}")

    # Cmap only latest base names (no ``name@N`` keys). Versioned keys stay in
    # all_glyphs so type-99 refs like ``u209f4@3`` still resolve.
    base_names = {n for n in all_glyphs if "@" not in n}
    print(f"Base names in dump: {len(base_names):,}")

    entries_all = [(name, related_map.get(name, name)) for name in base_names]
    if args.no_filters:
        entries = list(entries_all)
        print(
            f"Skipping related-CP / overlay filters (--no-filters): "
            f"{len(entries):,} base names kept"
        )
    else:
        # --- related-codepoint filter (CJK/Tangut ranges + radicals) ---
        allow = related_allow_set()
        entries = filter_related_entries(entries_all, allow)
        excluded_cp = len(entries_all) - len(entries)
        print(
            f"After related-CP filter: {len(entries):,} kept, {excluded_cp:,} excluded "
            f"(CHAR_RANGES + radicals + U+3013)"
        )

        # --- overlay exclusion (HKCS □ / digits / arrows, etc.) ---
        before_overlay = len(entries)
        entries = filter_excluded_entries(entries, all_glyphs)
        excluded_overlay = before_overlay - len(entries)
        print(
            f"After overlay exclusion: {len(entries):,} kept, "
            f"{excluded_overlay:,} excluded (non-mincho / annotation name regexes)"
        )

    # --- aliases (always); empty + duplicate outlines unless --no-filters ---
    entries = dedupe_pack_entries(
        entries, all_glyphs, aliases_only=args.no_filters
    )
    print(f"  SPUA markers needed: {markers_needed(len(entries)):,}")

    if args.limit > 0:
        # Limit cmap/font membership only — keep component deps for resolution
        stroke_counts = stroke_counts_for_names(
            (n for n, _ in entries), all_glyphs
        )
        entries = sort_glyph_entries(entries, stroke_counts)[: args.limit]
        seeds = {n for n, _ in entries}
        for n in all_glyphs:
            if "@" in n and n.split("@", 1)[0] in seeds:
                seeds.add(n)
        closure = collect_component_closure(seeds, all_glyphs)
        all_glyphs = {k: all_glyphs[k] for k in closure}
        related_map = {k: related_map[k] for k in all_glyphs if k in related_map}
        print(
            f"Limited to {len(entries)} base glyphs "
            f"({len(all_glyphs)} in resolution pool with deps)"
        )

    if not entries:
        print("Fatal: no glyphs left after filtering", file=sys.stderr)
        return 1

    # --- ligature assignment (related CP, stroke count, name) ---
    print(f"\nAssigning PUA ligatures to {len(entries)} glyphs...")
    stroke_counts = stroke_counts_for_names((n for n, _ in entries), all_glyphs)
    try:
        mappings: list[GlyphMapping] = assign_ligatures(entries, stroke_counts)
    except ValueError as exc:
        print(f"Fatal: {exc}", file=sys.stderr)
        return 1
    cmap = mapping_to_dict(mappings)
    write_json_object(CMAP_PATH, cmap)
    first, last = mappings[0], mappings[-1]
    print(
        f"  First: {first.name} -> U+{first.marker:X} U+{first.pua:X} "
        f"(related {first.related}, strokes {stroke_counts.get(first.name, 0)})"
    )
    print(
        f"  Last:  {last.name} -> U+{last.marker:X} U+{last.pua:X} "
        f"(related {last.related}, strokes {stroke_counts.get(last.name, 0)})"
    )

    if args.cmap_only:
        print("\n=== cmap-only complete ===")
        return 0

    # --- pipelined resolve+build (skip full-dump resolve when building fonts) ---
    if args.parallel and not args.skip_fonts:
        stroke_data = resolve_and_build_pipelined(
            mappings,
            all_glyphs,
            font_markers=args.font_markers,
            include_mirrors=include_mirrors,
            curve_fit=curve_fit,
            jobs=jobs,
        )
        # Persist cmap-coverage strokes for --from-resolved later
        resolved_lite = {
            name: {
                "d": data,
                "r": extract_component_refs(all_glyphs.get(name, "")),
                "n": count_strokes(data),
                "lig": cmap[name],
            }
            for name, data in stroke_data.items()
            if name in cmap
        }
        write_json_object(RESOLVED_PATH, resolved_lite)
        print("\n=== Extraction complete (parallel) ===")
        print(f"  Cmap strokes: {len(resolved_lite):,}")
        print(f"  Cmap:         {CMAP_PATH}")
        print(f"  Full output:  {RESOLVED_PATH}")
        print(f"  Fonts:        {FONT_DIR}")
        return 0

    resolved, missing_refs, _ = resolve_all_glyphs(all_glyphs)
    if missing_refs and not args.skip_newest_fallback:
        iteratively_resolve_missing(all_glyphs, resolved, missing_refs)

    # Attach ligature pairs
    for name, entry in resolved.items():
        if name in cmap:
            entry["lig"] = cmap[name]

    refs_count = sum(1 for e in resolved.values() if e["r"])
    print(f"\n  {refs_count} glyphs have component references")

    write_json_object(RESOLVED_PATH, resolved)

    if component_names:
        comp_obj = {
            name: {
                "d": resolved[name]["d"],
                "lig": resolved[name].get("lig"),
            }
            for name in sorted(component_names)
            if name in resolved
        }
        write_json_object(COMPONENTS_PATH, comp_obj)
        print(f"  Components written: {len(comp_obj)}")

    if not args.skip_fonts:
        stroke_data = {
            name: entry["d"]
            for name, entry in resolved.items()
            if "@" not in name
        }
        build_fonts_from_mappings(
            mappings,
            stroke_data,
            font_markers=args.font_markers,
            include_mirrors=include_mirrors,
            curve_fit=curve_fit,
            jobs=jobs,
        )

    print("\n=== Extraction complete ===")
    print(f"  Total glyphs: {len(resolved)}")
    print(f"  Cmap:         {CMAP_PATH}")
    print(f"  Full output:  {RESOLVED_PATH}")
    if not args.skip_fonts:
        print(f"  Fonts:        {FONT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
