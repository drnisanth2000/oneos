"""Icon sprite geometry and module-icon matching. Presentation only."""
import math
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_NUMBER = re.compile(r"-?\d*\.?\d+")


def _segments(path: str) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Absolute (start, end) of each `m dx dy dx dy` subpath (relative moveto + lineto)."""
    segments = []
    x = y = 0.0
    for command, args in re.findall(r"([mM])([^mM]*)", path):
        numbers = [float(n) for n in _NUMBER.findall(args)]
        assert len(numbers) == 4, (command, numbers)
        dx, dy, lx, ly = numbers
        start = (dx, dy) if command == "M" else (x + dx, y + dy)
        end = (start[0] + lx, start[1] + ly)
        segments.append((start, end))
        x, y = end
    return segments


def test_lifebuoy_spokes_run_between_the_inner_and_outer_ring():
    sprite = (TEMPLATES / "_icon_sprite.html").read_text(encoding="utf-8")
    symbol = re.search(r'<symbol id="i-lifebuoy".*?</symbol>', sprite, re.S).group(0)
    path = re.search(r'<path d="([^"]+)"', symbol).group(1)
    spokes = _segments(path)
    assert len(spokes) == 4
    for start, end in spokes:
        radii = sorted(math.hypot(px - 12, py - 12) for px, py in (start, end))
        assert radii[0] == pytest.approx(4, abs=0.1), (start, end)
        assert radii[1] == pytest.approx(10, abs=0.1), (start, end)


_ENV = Environment(loader=FileSystemLoader(str(TEMPLATES)))
_RENDER = _ENV.from_string(
    '{% from "_icon.html" import block_icon %}{{ block_icon(block, module) }}'
)


def _icon(module: str, block: str = "") -> str:
    html = _RENDER.render(block=block, module=module)
    return re.search(r'href="#i-([a-z-]+)"', html).group(1)


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("00-inbox", "inbox"),
        ("12-archive", "archive"),
        ("03-assets", "block-build"),
        ("05-operations", "wrench"),
        ("06-communications", "message"),
        ("09-marketing", "megaphone"),
        ("10-support", "lifebuoy"),
        ("11-knowledge", "book"),
        ("13-analytics", "chart"),
        ("14-legal", "scale"),
        ("15-self", "user"),
        ("07-finance", "block-finance"),
        ("20-sales-assets", "block-growth"),
    ],
)
def test_module_icon_follows_the_whole_module_word(module, expected):
    assert _icon(module) == expected


def test_unknown_module_falls_back_to_its_block_then_a_folder():
    assert _icon("16-widgets", "govern") == "block-govern"
    assert _icon("16-widgets", "mystery") == "folder"
    assert _icon("16-widgets") == "folder"


@pytest.mark.parametrize("module", ["16-myself", "16-reassets", "16-selfish", "16-dissupport"])
def test_module_icon_ignores_keys_inside_other_words(module):
    assert _icon(module) == "folder"
