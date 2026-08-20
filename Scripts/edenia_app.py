#!/usr/bin/env python3
"""Local Edenia composer — type and build VS sequences in a browser.

Serves ``Scripts/dist`` fonts with rewritten ``@font-face`` URLs so the
browser lazy-loads pigeonhole WOFF2s via ``unicode-range`` (unlike the
Obsidian plugin, which loaded every face into memory).

Usage
-----
  python Scripts/edenia_app.py
  python Scripts/edenia_app.py --port 8765 --no-browser
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import unquote

from edenia_names import (
    CSS_CJK,
    CSS_HANGUL,
    CSS_KANA,
    CSS_YI,
    FAMILY_HANGUL,
    FAMILY_HANGULS,
    FAMILY_KANA,
    FAMILY_YI,
    family_cjk_variant,
)
from cjk_diacritics import (
    CORE_MARK_CPS,
    MARK_SLOT_VS,
    OV_SELECTOR_CP,
    SQUISH_BOT_CP,
    SQUISH_LEFT_CP,
    SQUISH_RIGHT_CP,
    SQUISH_TOP_CP,
)
from cjk_diacritics_html import BASE_ORIENT_LABEL, BASE_ORIENT_VS
from shared_quarter_cells import GRID_VS_SLOTS, QUARTER_VS_SLOTS_H, QUARTER_VS_SLOTS_V
from shared_third_cells import THIRD_VS_SLOTS
from hangul_html import L_RANGES, T_RANGES, V_RANGES, assigned_cps
from yi_slice import (
    SLICE_BL_CP,
    SLICE_BOT_CP,
    SLICE_BR_CP,
    SLICE_LEFT_CP,
    SLICE_RIGHT_CP,
    SLICE_TL_CP,
    SLICE_TOP_CP,
    SLICE_TR_CP,
)

SCRIPT_DIR = Path(__file__).resolve().parent
WEB_DIR = SCRIPT_DIR / "edenia_web"
DIST_DIR = SCRIPT_DIR / "dist"

CSS_MAP = {
    "cjk": ("cjk", CSS_CJK),
    "hangul": ("hangul", CSS_HANGUL),
    "yi": ("yi", CSS_YI),
    "kana": ("kana", CSS_KANA),
}
FONT_FOLDERS = frozenset(v[0] for v in CSS_MAP.values())
_URL_RE = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)")
_CJK_FAMILY_RE = re.compile(r"font-family:\s*'edenia cjk(?: (qh|qv|q|[ht]))?'")


def available_cjk_variants() -> List[str]:
    """Face suffixes actually listed in dist edenia-cjk.css."""
    path = DIST_DIR / "cjk" / CSS_CJK
    order = ["", "h", "t", "q", "qv", "qh"]
    if not path.is_file():
        return order
    text = path.read_text(encoding="utf-8")
    found = {m.group(1) or "" for m in _CJK_FAMILY_RE.finditer(text)}
    return [v for v in order if v in found] or [""]


def _compact_jamo(ranges) -> List[dict]:
    return [
        {"cp": e["cp"], "ch": e["ch"], "s": e["short"]} for e in assigned_cps(ranges)
    ]


def _combining_marks(limit: int = 48) -> List[dict]:
    marks: List[dict] = []
    font = DIST_DIR / "hangul" / "edenia-hangul.woff2"
    if font.is_file():
        try:
            from fontTools.ttLib import TTFont
            from shared_diacritics import iter_dakuten_codepoints, visible_dakuten_cps

            tt = TTFont(str(font))
            try:
                cmap: Dict[int, str] = {}
                for table in tt["cmap"].tables:
                    if table.isUnicode():
                        cmap.update(table.cmap)
            finally:
                tt.close()
            for cp in visible_dakuten_cps(iter_dakuten_codepoints(cmap))[:limit]:
                marks.append({"cp": cp, "ch": chr(cp), "s": f"{cp:04X}"})
        except Exception:
            marks = []
    if not marks:
        for cp in (0x3099, 0x309A, 0x0301, 0x0300, 0x0308, 0x0304):
            marks.append({"cp": cp, "ch": chr(cp), "s": f"{cp:04X}"})
    return marks


def build_data() -> dict:
    missing = [
        name
        for name, (folder, css) in CSS_MAP.items()
        if not (DIST_DIR / folder / css).is_file()
    ]
    return {
        "missing": missing,
        "AVAILABLE_CJK": available_cjk_variants(),
        "OV": OV_SELECTOR_CP,
        "ORIENTs": list(BASE_ORIENT_VS),
        "ORIENT_LABELS": list(BASE_ORIENT_LABEL),
        "HALF_VS": {
            "T": SQUISH_TOP_CP,
            "B": SQUISH_BOT_CP,
            "L": SQUISH_LEFT_CP,
            "R": SQUISH_RIGHT_CP,
        },
        "THIRD_VS": {suf: cp for cp, _sel, suf, _a, _b0, _b1 in THIRD_VS_SLOTS},
        "QV_VS": {slot[2]: slot[0] for slot in QUARTER_VS_SLOTS_V},
        "QH_VS": {slot[2]: slot[0] for slot in QUARTER_VS_SLOTS_H},
        "Q_VS": {slot[2]: slot[0] for slot in GRID_VS_SLOTS},
        "FAMILIES": {
            "": family_cjk_variant(""),
            "h": family_cjk_variant("h"),
            "t": family_cjk_variant("t"),
            "q": family_cjk_variant("q"),
            "qv": family_cjk_variant("qv"),
            "qh": family_cjk_variant("qh"),
            "hangul": FAMILY_HANGUL,
            "hanguls": FAMILY_HANGULS,
            "kana": FAMILY_KANA,
            "yi": FAMILY_YI,
        },
        "CJK_MARK_SLOTS": [
            {
                "cp": cp,
                "pos": pos,
                "mirror": mirror or "id",
                "label": f"FE{cp - 0xFE00:02X} {pos} {mirror or 'id'}",
            }
            for cp, _sel, pos, mirror in MARK_SLOT_VS
        ],
        "CJK_MARKS": [
            {"cp": 0x16FF0, "ch": chr(0x16FF0), "label": "ca"},
            {"cp": 0x16FF1, "ch": chr(0x16FF1), "label": "nhay"},
        ],
        "HANGUL": {
            "L": _compact_jamo(L_RANGES),
            "V": _compact_jamo(V_RANGES),
            "T": _compact_jamo(T_RANGES),
            "SWAP": 0xFE04,
        },
        "YI": {
            "OV": OV_SELECTOR_CP,
            "SLICE": {
                "TB": [SLICE_TOP_CP, SLICE_BOT_CP],
                "LR": [SLICE_LEFT_CP, SLICE_RIGHT_CP],
                "TLBR": [SLICE_TL_CP, SLICE_BR_CP],
                "TRBL": [SLICE_TR_CP, SLICE_BL_CP],
            },
        },
        "KANA": {
            "OV": OV_SELECTOR_CP,
            "SLICE": {
                "TB": [SLICE_TOP_CP, SLICE_BOT_CP],
                "LR": [SLICE_LEFT_CP, SLICE_RIGHT_CP],
                "TLBR": [SLICE_TL_CP, SLICE_BR_CP],
                "TRBL": [SLICE_TR_CP, SLICE_BL_CP],
            },
        },
        "COMBINING": _combining_marks(),
        "CORE_MARKS": list(CORE_MARK_CPS),
    }


def rewrite_css(text: str, folder: str) -> str:
    """Point every url(...) at /fonts/<folder>/<basename> (local lazy load)."""

    def repl(m: re.Match[str]) -> str:
        name = unquote(m.group(2)).replace("\\", "/").rsplit("/", 1)[-1]
        if not name:
            return m.group(0)
        return f'url("/fonts/{folder}/{name}")'

    return _URL_RE.sub(repl, text)


class EdeniaHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path.split("?", 1)[0])
        if path in ("/", "/index.html"):
            return self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/app.css":
            return self._send_file(WEB_DIR / "app.css", "text/css; charset=utf-8")
        if path == "/app.js":
            return self._send_file(WEB_DIR / "app.js", "text/javascript; charset=utf-8")
        if path == "/api/data.json":
            body = json.dumps(build_data(), ensure_ascii=False).encode("utf-8")
            return self._send_bytes(body, "application/json; charset=utf-8")
        if path.startswith("/css/") and path.endswith(".css"):
            key = path[len("/css/") : -len(".css")]
            mapped = CSS_MAP.get(key)
            if not mapped:
                self.send_error(404)
                return
            folder, css_name = mapped
            src = DIST_DIR / folder / css_name
            if not src.is_file():
                self.send_error(404, f"missing {src.name} — build that face first")
                return
            text = rewrite_css(src.read_text(encoding="utf-8"), folder)
            return self._send_bytes(text.encode("utf-8"), "text/css; charset=utf-8")
        if path.startswith("/fonts/"):
            rest = path[len("/fonts/") :]
            parts = rest.split("/", 1)
            if len(parts) != 2 or parts[0] not in FONT_FOLDERS:
                self.send_error(404)
                return
            folder, name = parts
            if "/" in name or "\\" in name or name.startswith("."):
                self.send_error(404)
                return
            src = DIST_DIR / folder / name
            if not src.is_file():
                self.send_error(404)
                return
            ctype = {
                ".woff2": "font/woff2",
                ".ttf": "font/ttf",
                ".otf": "font/otf",
                ".css": "text/css; charset=utf-8",
            }.get(src.suffix.lower(), "application/octet-stream")
            return self._send_file(src, ctype)
        self.send_error(404)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self._send_bytes(data, content_type)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Edenia local composer webapp")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not WEB_DIR.is_dir():
        sys.exit(f"missing {WEB_DIR}")
    httpd = ThreadingHTTPServer((args.host, args.port), EdeniaHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Edenia composer at {url}", flush=True)
    print(f"Fonts from {DIST_DIR}", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
        httpd.server_close()


if __name__ == "__main__":
    os.chdir(SCRIPT_DIR)
    main()
