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
    assert "ubuntu-latest" in step["if"] and "'3.13'" in step["if"]


def test_the_coverage_leg_measures_with_sys_monitoring():
    """On 3.11 coverage's per-thread trace function raised in the GUI
    server's request thread and a page never loaded. The traced run sets
    the sys.monitoring core and fails if coverage fell back from it."""
    run = next(s for s in _steps() if s.get("name") == "Run tests")["run"]
    assert "export COVERAGE_CORE=sysmon" in run
    assert "core: SysMonitor" in run
    assert "--cov-branch" not in run


def test_before_the_tests_run():
    names = [s.get("name") for s in _steps()]
    assert names.index(STEP) < names.index("Run tests")


def test_every_leg_runs_the_rest_on_every_core_and_fails_on_either_run():
    """On one core the plain legs took up to 89 minutes, and two were
    cancelled at 90 with nothing failed (run 42699335287). Every leg runs
    the files that drive no browser through pytest-xdist and the browser
    files one at a time after them, and a failure in either fails the job."""
    steps = _steps()
    install = next(s for s in steps
                   if s.get("name") == "Install package and test dependencies")
    assert "if" not in install and "pytest-xdist" in install["run"]
    run = next(s for s in steps if s.get("name") == "Run tests")["run"]
    assert run.count("-n auto --dist loadfile") == 2
    assert run.count("$(printf -- '--ignore=%s ' $browser) || status=1") == 2
    assert run.rstrip().endswith("fi\nexit $status")
