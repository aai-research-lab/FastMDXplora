"""The GUI's browser tests run in CI, on one job.

They drive Chromium through Playwright and skip where it is absent. Before a
step installed it they skipped on every job, so a change that broke the page
passed CI. A step that disappears, moves to another job or runs after the
tests would put them back to skipping without a failure anywhere, so this
holds it in place.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STEP = "Install a browser for the GUI tests"


def _steps():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["test"]["steps"]


def test_a_step_installs_the_browser():
    step = next((s for s in _steps() if s.get("name") == STEP), None)
    assert step is not None, f"no step named {STEP!r}"
    assert "playwright install" in step["run"] and "chromium" in step["run"]


def test_on_the_job_coverage_is_measured_on():
    step = next(s for s in _steps() if s.get("name") == STEP)
    assert "ubuntu-latest" in step["if"] and "'3.11'" in step["if"]


def test_before_the_tests_run():
    names = [s.get("name") for s in _steps()]
    assert names.index(STEP) < names.index("Run tests")
