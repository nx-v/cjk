"""FontBuilder helpers shared by Edenia build scripts."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.timeTools import timestampNow
from fontTools.ttLib import TTFont

_HEAD_LOG = logging.getLogger("fontTools.ttLib.tables._h_e_a_d")


@contextmanager
def _quiet_head_timestamp_warnings():
    """Suppress benign ``head`` timestamp warnings on third-party source fonts."""
    prev = _HEAD_LOG.level
    _HEAD_LOG.setLevel(logging.ERROR)
    try:
        yield
    finally:
        _HEAD_LOG.setLevel(prev)


def load_ttfont(path: str, **kwargs: Any) -> TTFont:
    """Open a source font without logging legacy ``head`` timestamp warnings."""
    with _quiet_head_timestamp_warnings():
        return TTFont(path, **kwargs)


def setup_head_timestamps(fb: FontBuilder) -> None:
    """Set ``head.created`` / ``head.modified`` (FontBuilder defaults are 0).

    fontTools warns on load when these look like unset Mac-era values; stamp
    them before the first save so hint/WOFF2 stages stay quiet.
    """
    now = timestampNow()
    fb.setupHead(created=now, modified=now)
