"""Every script the GUI serves parses.

The browser tests run on one CI job; a syntax error in a script they do not
open would pass everywhere else and break the page for everyone. Node reads
each file without running it, which is quick enough for every job that has
Node, and GitHub's runners do.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import fastmdxplora.gui as gui

STATIC = Path(gui.__file__).parent / "static"
#: Minified and vendored, not ours to check.
VENDORED = {"3Dmol-min.js"}
SCRIPTS = sorted(path for path in STATIC.glob("*.js") if path.name not in VENDORED)


def test_there_are_scripts_to_check() -> None:
    assert {"molecule-viewer.js", "dashboard.js", "charts.js"} <= {p.name for p in SCRIPTS}


@pytest.mark.parametrize("script", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_it_parses(script: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    done = subprocess.run([node, "--check", str(script)], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
