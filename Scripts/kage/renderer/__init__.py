"""In-tree KAGE stroke renderer (Serif / Sans / Round SVG drawers)."""

from .kage import Kage
from . import components
from . import font
from . import stroke
from . import util
from . import vec2

__all__ = [
    "Kage",
    "components",
    "font",
    "stroke",
    "util",
    "vec2",
]
