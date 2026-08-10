"""List non-nexovolta HTTPS URLs in theme.css."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

t = Path(__file__).resolve().parent.parent.joinpath("theme.css").read_text(encoding="utf-8")
urls = re.findall(r"https://[^\"')\s]+", t)
hosts: Counter[str] = Counter()
non_nexo: list[str] = []
for u in urls:
    m = re.match(r"https://([^/]+)/", u)
    hosts[m.group(1) if m else "?"] += 1
    if "nexovolta" not in u:
        non_nexo.append(u)
print("hosts:", hosts.most_common(40))
print("non-nexovolta unique:", len(set(non_nexo)))
for u in sorted(set(non_nexo)):
    print(u)
