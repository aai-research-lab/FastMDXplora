"""A link in the Agent's reply ends where the address does.

"Save https://files.rcsb.org/ligands/download/BNZ_ideal.sdf." was linked to
"BNZ_ideal.sdf." with the sentence's full stop inside it, which is not a
file RCSB has. Punctuation that closes a sentence or a clause is left after
the link; an escaped ampersand's semicolon belongs to the address.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent / "src" / "fastmdxplora" / "gui"
          / "static" / "agent-panel.js")


def _prose(texts: list[str]) -> list[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to run the page's own function")
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("  function prose(text) {")
    end = source.index("\n  }\n", start) + len("\n  }\n")
    program = (source[start:end] + f"\nconsole.log(JSON.stringify({json.dumps(texts)}.map(prose)));\n")
    done = subprocess.run([node, "-e", program], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_sentence_keeps_its_punctuation():
    url = "https://files.rcsb.org/ligands/download/BNZ_ideal.sdf"
    ended, clause, question, plain = _prose([
        f"Save {url}. Then tell me.", f"at {url}, then", f"Is it {url}?", f"see {url}"])
    link = f'<a href="{url}" target="_blank" rel="noopener">{url}</a>'
    assert ended == f"Save {link}. Then tell me."
    assert clause == f"at {link}, then"
    assert question == f"Is it {link}?"
    assert plain == f"see {link}"


def test_an_address_keeps_its_own_characters():
    (query,) = _prose(["https://example.org/a?b=1&c=2."])
    assert 'href="https://example.org/a?b=1&amp;c=2"' in query and query.endswith("</a>.")
    (entity,) = _prose(["https://example.org/a?b=1&"])
    assert 'href="https://example.org/a?b=1&amp;"' in entity
