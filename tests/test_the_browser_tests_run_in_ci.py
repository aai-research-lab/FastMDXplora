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


def _run_tests() -> str:
    return next(s for s in _steps() if s.get("name") == "Run tests")["run"]


def _parallel() -> str:
    """The options both runs on every core take, as the step sets them."""
    said = [line.strip() for line in _run_tests().splitlines()
            if line.strip().startswith("parallel=")]
    assert len(said) == 2, said
    return " ".join(said)


def test_every_leg_runs_the_rest_on_every_core_and_fails_on_either_run():
    """On one core the plain legs took up to 89 minutes, and two were
    cancelled at 90 with nothing failed (run 42699335287). Every leg runs
    the files that drive no browser through pytest-xdist and the browser
    files one at a time after them, and a failure in either fails the job."""
    install = next(s for s in _steps()
                   if s.get("name") == "Install package and test dependencies")
    assert "if" not in install and "pytest-xdist" in install["run"]
    run = _run_tests()
    assert "-n auto --dist loadfile" in _parallel()
    assert run.count("pytest -rfEs $parallel ") == 2
    assert run.count("$(printf -- '--ignore=%s ' $browser) || status=1") == 2
    assert run.rstrip().endswith("fi\nexit $status")


def test_a_hang_on_every_core_fails_once_with_its_stack():
    """pytest.ini's thread method ends the process. Under xdist that is the
    worker, and xdist ran the hung test again on each new worker until it
    stopped replacing them, with no stack shown. The signal fails the test
    alone, and no crashed worker is replaced."""
    parallel = _parallel()
    assert "--timeout-method=signal" in parallel and "--max-worker-restart=0" in parallel


def test_a_hang_in_its_cleanup_is_named_and_ends_its_worker():
    """The signal fires once, so a test whose `finally` then waits on what
    hung (a pool shut down with a worker that never answers) held its
    worker with nothing said. faulthandler writes every thread's stack after
    the signal's 600 s and ends the worker; pytest 9 is the first to end it."""
    parallel = _parallel()
    assert "-o faulthandler_timeout=660" in parallel
    assert "-o faulthandler_exit_on_timeout=true" in parallel
    install = next(s for s in _steps()
                   if s.get("name") == "Install package and test dependencies")
    assert '"pytest>=9"' in install["run"]


def test_this_pytest_can_end_a_hung_worker(pytestconfig):
    """An option pytest does not know is only warned of, so the run on every
    core would wait on a hung cleanup again with nothing failing."""
    # Unknown, getini raises ValueError; the run on every core sets it true.
    assert isinstance(pytestconfig.getini("faulthandler_exit_on_timeout"), bool)


def test_a_browser_grep_that_finds_nothing_says_so():
    run = _run_tests()
    assert "tests/*.py || true)" in run
    lines = [line.strip() for line in run.splitlines()]
    at = lines.index('if [ -z "$browser" ]; then')
    assert lines[at + 1].startswith('echo "::error::'), lines[at:at + 4]
    assert lines[at + 2:at + 4] == ["exit 1", "fi"], lines[at:at + 4]


def test_the_coverage_leg_runs_its_browser_files_on_two_workers():
    """One at a time the coverage leg's browser files took 1 h 56 min after
    23 min on every core (run 42759782805), against its 180 minutes. They
    run on two workers there, a file on one, with the options a hang needs;
    four browsers on four cores timed out on waits that pass alone, and the
    plain legs, whose browser tests skip, run those files one at a time."""
    run = _run_tests()
    two = [line.strip() for line in run.splitlines() if line.strip().startswith("two=")]
    assert two == ['two="-n 2 ${parallel#-n auto }"'], two
    assert _parallel().split()[:2] == ["parallel=\"-n", "auto"]
    covered, plain = run.split("else\n", 1)
    assert "pytest -rfEs $two --cov=fastmdxplora --cov-append" in covered
    assert "pytest -rfEs $browser || status=1" in [line.strip() for line in plain.splitlines()]
    assert "$two" not in plain
