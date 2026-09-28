"""`python -m fastmdxplora` runs the command once.

The GUI started every study with `python -m fastmdxplora.cli.main`. The CLI
package imports `main` from that module, so runpy found it already imported
and ran it a second time as `__main__`, warning that this "may result in
unpredictable behaviour": every class the module defines existed twice, and
the warning opened every GUI-started run's log.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_runs_as_the_command_without_a_warning() -> None:
    done = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "fastmdxplora", "--version"],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")})
    assert done.returncode == 0, done.stderr
    assert "FastMDXplora" in done.stdout
    assert "RuntimeWarning" not in done.stderr


def test_the_gui_starts_a_study_that_way(tmp_path) -> None:
    from fastmdxplora.gui.run_from_config import prepare_run

    prepared = prepare_run(None, tmp_path, config={"systems": [{"system": "1UBQ"}]})
    assert prepared["ok"], prepared.get("error")
    assert prepared["command"][1:4] == ["-m", "fastmdxplora", "explore"]


def test_a_run_started_that_way_is_recognised_as_one(tmp_path) -> None:
    from fastmdxplora.gui.exploration import _command_line_is_a_run

    line = f"/opt/conda/bin/python -m fastmdxplora explore --config x.yml --output {tmp_path}"
    assert _command_line_is_a_run(line, tmp_path / "elsewhere")
