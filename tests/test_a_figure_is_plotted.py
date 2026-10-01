"""A figure is plotted, a molecule is rendered; neither is drawn.

The software said a figure was "drawn", "drawn again", "drawn at a column's
width", that an analysis had "no surface to draw", and that the viewer's
representation was what the molecule was "Drawn as". A curve, a surface or
a chart is plotted; a structure in the viewer is rendered and its
representation chosen. "Drawn" stays where it is the statistical word: a
seed or velocities drawn at random, pairs drawn once from those available.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_what_is_computed_is_not_called_measured import PAGES, SRC, _strings_said

DRAWN = re.compile(r"\b(draw|draws|drawn|drawing|drew)\b", re.IGNORECASE)

#: Where "drawn" is the statistical word, or the idiom, and right.
RIGHT = (
    "a seed is drawn and recorded",
    ", drawn for this study",
    "velocities drawn afresh",
    "drawn once and held fixed across frames",
    "where the boundary between them is drawn is a convention",
)


def _wrong(text: str) -> bool:
    for right in RIGHT:
        text = text.replace(right, "")
    return DRAWN.search(text) is not None


@pytest.mark.parametrize("page", PAGES)
def test_no_page_draws_a_figure(page):
    text = (SRC / page).read_text(encoding="utf-8")
    shown = re.findall(r'"[^"\n]*"|\'[^\'\n]*\'|>[^<\n]+<', text)
    assert [s for s in shown if " " in s and _wrong(s)] == []
    if page.endswith(".html"):
        assert '<label class="select-field">Representation' in text


def test_no_message_draws_a_figure():
    said = [(str(path.relative_to(SRC)), s) for path in sorted(SRC.rglob("*.py"))
            for s in _strings_said(path) if " " in s and _wrong(s)]
    assert said == []


@pytest.mark.parametrize("page", sorted(
    p.name for p in (Path(__file__).resolve().parent.parent / "docs").glob("*.md")))
def test_no_page_of_the_docs_draws_a_figure(page):
    text = (Path(__file__).resolve().parent.parent / "docs" / page).read_text(encoding="utf-8")
    statistical = re.compile(r"\b(seed|noise|segments|thermostat draws|draws a seed|one is "
                             r"drawn|drawn and recorded|drawn from)\b", re.IGNORECASE)
    wrong = [line for line in text.splitlines()
             if DRAWN.search(line) and not statistical.search(line)]
    assert wrong == []
