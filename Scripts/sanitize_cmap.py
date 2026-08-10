"""Strip empty cmap format-14 subtables (OTS rejects them in Chromium)."""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from fontTools.ttLib import TTFont, woff2

ROOT = Path(__file__).resolve().parent / "dist"


def sanitize(path_str: str) -> tuple[str, str]:
    path = Path(path_str)
    try:
        t = TTFont(str(path))
        tables = t["cmap"].tables
        kept = []
        removed = 0
        for sub in tables:
            uvs = getattr(sub, "uvsDict", None)
            if sub.format == 14 and not uvs:
                removed += 1
                continue
            kept.append(sub)
        if not removed:
            t.close()
            return path.name, "ok"
        t["cmap"].tables = kept
        tmp_ttf = path.with_suffix(".tmp.ttf")
        t.save(str(tmp_ttf))
        t.close()
        tmp_w = path.with_suffix(".tmp.woff2")
        woff2.compress(str(tmp_ttf), str(tmp_w))
        os.replace(tmp_w, path)
        tmp_ttf.unlink(missing_ok=True)
        return path.name, "fixed"
    except Exception as e:
        return path.name, f"err:{e}"


def main() -> None:
    files: list[str] = []
    sub = ROOT / "subfonts"
    files.extend(sorted(str(p) for p in sub.glob("*.woff2") if not p.name.startswith("_")))
    yi = ROOT / "yi" / "panyi.woff2"
    if yi.exists():
        files.append(str(yi))
    print("files", len(files))
    fixed = ok = err = 0
    with ProcessPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(sanitize, f) for f in files]
        for fut in as_completed(futs):
            name, status = fut.result()
            if status == "fixed":
                fixed += 1
            elif status == "ok":
                ok += 1
            else:
                err += 1
                print(name, status)
    print(f"done fixed={fixed} ok={ok} err={err}")
    for p in sub.glob("_*.woff2"):
        p.unlink()
        print("rm", p.name)
    for p in sub.glob("*.tmp.*"):
        p.unlink()
        print("rm", p.name)


if __name__ == "__main__":
    main()
