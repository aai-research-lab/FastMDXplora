"""Whether the words an AI model quotes are the paper's, and where.

A value read from a paper reaches a config only with the words it was read
from, and those words are looked for here in what was read: the AI model
can misread, and it can write a sentence the paper does not contain. A
quote is found when, letter for letter, it is in the paper once both are
reduced to the same form: accents, case, spacing, line breaks and hyphens
set aside, since a PDF's text breaks words across lines and spells
``Å`` as ``A˚``. A quote with an ellipsis is found when each piece is, in
order and close together. What is found is said with the part it is in
(``Methods``, ``p. 4``).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from fastmdxplora.paper.text import PaperText

__all__ = ["Found", "QuoteIndex", "squash", "MOST_GAP", "LEAST_LETTERS"]

#: A quote shorter than this, reduced, says too little to be found as the
#: paper's: "300 K" is in many papers and in many places of one.
LEAST_LETTERS = 8

#: How far apart, in reduced letters, the pieces of a quote with an
#: ellipsis may be: about a paragraph.
MOST_GAP = 1200

_DASHES = set("-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d")
_DROPPED = _DASHES | set(" \t\r\n\f\v   ​­'\"`’‘“”´")


def squash(text: str) -> tuple[str, list[int]]:
    """``text`` reduced for matching, and for each reduced letter the index
    in ``text`` it came from."""
    out: list[str] = []
    where: list[int] = []
    for index, char in enumerate(text):
        for piece in unicodedata.normalize("NFKD", char):
            if piece in _DROPPED or unicodedata.combining(piece):
                continue
            category = unicodedata.category(piece)
            if category == "Sk" or category.startswith("Z") or category == "Cc":
                continue
            if piece == "×":
                piece = "x"
            for letter in piece.casefold():
                out.append(letter)
                where.append(index)
    return "".join(out), where


@dataclass(frozen=True)
class Found:
    """Where a quote is: the part's label and kind, and the words as the
    paper has them."""

    label: str
    kind: str
    words: str
    context: str = ""


class QuoteIndex:
    """A paper's parts, reduced once, for finding quotes in."""

    def __init__(self, paper: PaperText) -> None:
        self._parts = paper.parts
        self._squashed = [squash(part.text) for part in paper.parts]

    def find(self, quote: str) -> Found | None:
        """Where ``quote`` is in the paper, or None. A quote split by an
        ellipsis is found when every piece is, in order, within
        :data:`MOST_GAP` of the one before, in one part."""
        pieces = [piece for piece in _split_ellipsis(quote)]
        reduced = [squash(piece)[0] for piece in pieces]
        reduced = [piece for piece in reduced if piece]
        if not reduced or sum(len(piece) for piece in reduced) < LEAST_LETTERS:
            return None
        for part, (hay, where) in zip(self._parts, self._squashed):
            start = 0
            while True:
                first = hay.find(reduced[0], start)
                if first < 0:
                    break
                end = self._rest(hay, reduced, first + len(reduced[0]))
                if end is not None:
                    begin_at = where[first]
                    end_at = where[end - 1] + 1
                    if not _cuts_a_number(part.text, begin_at, end_at):
                        return Found(part.label, part.kind, part.text[begin_at:end_at],
                                     part.text)
                start = first + 1
        return None

    @staticmethod
    def _rest(hay: str, pieces: list[str], after: int) -> int | None:
        for piece in pieces[1:]:
            at = hay.find(piece, after)
            if at < 0 or at - after > MOST_GAP:
                return None
            after = at + len(piece)
        return after


def _cuts_a_number(text: str, begin: int, end: int) -> bool:
    """Whether the words ``text[begin:end]`` start or end inside a number:
    "5 µs" found in "1.5 µs", "50 ns" in "150 ns". Read so, the quote would
    hold a value the paper does not state."""
    def digit_at(index: int) -> bool:
        return 0 <= index < len(text) and text[index].isdigit()

    if digit_at(begin):
        before = begin - 1
        if digit_at(before) or (before >= 0 and text[before] in ".," and digit_at(before - 1)):
            return True
    if digit_at(end - 1):
        if digit_at(end) or (end < len(text) and text[end] in ".," and digit_at(end + 1)):
            return True
    return False


def _split_ellipsis(quote: str) -> list[str]:
    text = str(quote or "").replace("…", "...")
    for mark in ("[...]", "(...)"):
        text = text.replace(mark, "...")
    return [piece for piece in text.split("...") if piece.strip()]
