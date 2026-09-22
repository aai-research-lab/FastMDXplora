"""The documentation, the code and the tests describe this software on its own
terms. The name of the chat assistant whose models the Agent can use appears
only where it is functional -- a model identifier, such as the lower-case names
in `agent/models.py`, or the address a key is issued at -- and never as prose:
not as a comparison, and not about that product's users.

Four such sentences were written and taken out. This keeps them out, whoever
writes the next one. The word is built rather than written, so this file does
not trip itself.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORD = re.compile(r"\b" + "Cl" + "aude" + r"\b")
SCANNED = ("docs", "src", "tests", "scripts")
TEXT = {".py", ".md", ".js", ".css", ".html", ".yml", ".yaml", ".toml", ".txt",
        ".cfg", ".json", ".rst", ".cff"}


def _named_as_prose() -> list[str]:
    paths = [p for folder in SCANNED for p in (ROOT / folder).rglob("*") if p.is_file()]
    paths += [p for p in ROOT.glob("*") if p.is_file()]
    found = []
    for path in paths:
        if path.suffix.lower() not in TEXT or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if WORD.search(line):
                found.append(f"{path.relative_to(ROOT)}:{number}")
    return found


def test_it_is_named_only_where_it_is_functional():
    assert _named_as_prose() == []
