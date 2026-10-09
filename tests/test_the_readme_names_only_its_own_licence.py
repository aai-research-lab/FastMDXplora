"""The README names FastMDXplora's own licence and no other.

Its License section also gave the licences of the interface's fonts and of
the bundled Mol*, which belong beside those files, not on the project's
front page.
"""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"

# Where the README speaks of FastMDXplora's own licence, or of its own
# contributor agreement.
OWN = (
    "License: MIT",
    "License-MIT",
    "opensource.org/licenses/MIT",
    "## License",
    "FastMDXplora is released under the MIT License.",
    "[LICENSE](LICENSE)",
    "Contributor License Agreement",
)

OTHERS = re.compile(
    r"open font|\bOFL\b|apache|GPL|\bBSD\b|0BSD|\bISC\b|\bMPL\b|mozilla public"
    r"|creative commons|\bCC[- ]BY\b|\bCC0\b|unlicense|artistic licen"
    r"|its own licen|their own licen",
    re.IGNORECASE,
)


def _text() -> str:
    return " ".join(README.read_text(encoding="utf-8").split())


def test_no_other_licence_is_named() -> None:
    found = sorted({m.group(0) for m in OTHERS.finditer(_text())})
    assert not found, f"the README names other licences: {found}"


def test_every_licence_mention_is_fastmdxplora_s_own() -> None:
    text = _text()
    for phrase in OWN:
        text = text.replace(phrase, "")
    left = [text[max(0, m.start() - 60):m.end() + 60]
            for m in re.finditer(r"licen[cs]", text, re.IGNORECASE)]
    assert not left, f"the README speaks of a licence not its own: {left}"


def test_its_own_licence_is_still_said() -> None:
    text = _text()
    assert "FastMDXplora is released under the MIT License." in text
    assert "[LICENSE](LICENSE)" in text
