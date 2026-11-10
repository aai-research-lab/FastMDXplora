"""Parallel workers start clean, and a test that hangs is named, not waited on.

Three CI runs in a row walled silently at the first parallel batch test --
the streamed log ends at the last scheduling test, because pytest writes a
test's name without a newline until it finishes, so the hanging test is the
one you cannot see. Each wall was a six-hour squat on a runner until a job
timeout existed, and together they wore the disguise of "tremendous CI
delays". The walls began the day OpenMM landed in CI, which is the day the
parallel tests started doing real work in worker processes.

The mechanism: ProcessPoolExecutor without an mp_context forks on Linux,
and a forked worker inherits the parent as it stood -- including thread
pools that numpy, mdtraj and OpenMM had started over the preceding four
hundred tests, whose threads do not survive the fork but whose locks do.
It never reproduced on the one-core machine used to develop the fix,
because on one core those pools are never spun up and the parent is
thread-free at fork time. macOS and Windows spawn and never hung.

So: spawn everywhere, pinned here; and a per-test timeout in the suite's
own configuration, so the next wall -- whatever causes it -- fails as a
named test with every thread's stack attached instead of hanging a runner.

That is the run of one test at a time. CI's runs on every core (its "Run
tests" step) use pytest-timeout's signal method instead, because the
thread method ends a pytest-xdist worker and xdist ran the test again on
each new one: there the test fails at 600 s with its stack, and if its
cleanup then waits on what hung, faulthandler writes every thread's stack
at 660 s and ends the worker, which names the test.
"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path

from fastmdxplora.batch import explorer as explorer_module
from fastmdxplora.batch.explorer import BatchExplorer

EXPLORER = Path("src/fastmdxplora/batch/explorer.py").read_text(
    encoding="utf-8")


class TestTheChoiceIsWrittenWhereThePoolIsMade:
    def test_the_pool_is_given_spawn(self):
        assert 'mp_context=get_context("spawn")' in EXPLORER

    def test_the_suite_has_a_ceiling_per_test(self):
        ini = Path("pytest.ini").read_text(encoding="utf-8")
        assert "--timeout=" in ini
        # The thread method is the one that dumps every stack, which is the
        # difference between "a job was canceled" and knowing which test
        # stood at the wall and what every thread was holding.
        assert "--timeout-method=thread" in ini

    def test_the_ceiling_is_declared_where_ci_installs(self):
        """Parsed as TOML rather than scanned for the next `]`.

        The regex version stopped at the first closing bracket after
        `test = [`, so a comment inside the list containing one -- a prose
        mention of `[dev]`, which is exactly what got written there -- cut
        the extract short and the assertion failed on a table that was
        correct. Same shape as `_numeric_column` taking its delimiter from a
        line that later grew a comma: a text scanner tripped by punctuation
        inside prose. The file is TOML and there is a parser for it.
        """
        from tests._toml import load_toml

        pyproject = load_toml(Path("pyproject.toml"))
        test_extra = pyproject["project"]["optional-dependencies"]["test"]
        assert any(name.startswith("pytest-timeout") for name in test_extra)


class _RecordingPool:
    """Stands in for ProcessPoolExecutor and remembers how it was made.

    Submissions run inline and come back as completed futures, so
    _run_parallel drains normally -- this test is about the wiring of the
    pool, not the chemistry inside the workers.
    """

    made_with: dict = {}

    def __init__(self, max_workers=None, mp_context=None, initializer=None,
                 initargs=()):
        type(self).made_with = {
            "max_workers": max_workers,
            "mp_context": mp_context,
        }

    def submit(self, fn, *args, **kwargs):
        fut: Future = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 - delivered, like the real pool
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait=True, cancel_futures=False):
        pass


class TestTheWiringCarriesSpawnToThePool:
    def test_run_parallel_hands_the_pool_a_spawn_context(
            self, tmp_path, monkeypatch):
        pdb = tmp_path / "protein.pdb"
        pdb.write_text(
            "ATOM      1  N   ALA A   1       0.000   0.000   0.000"
            "  1.00  0.00           N\n"
            "END\n")
        config = tmp_path / "batch.yml"
        config.write_text(f"""
output: {tmp_path / 'out'}
include: [setup]
systems:
  - {{id: a, system: {pdb}}}
  - {{id: b, system: {pdb}}}
execution:
  mode: parallel
  workers: 2
""")
        monkeypatch.setattr(explorer_module, "ProcessPoolExecutor",
                            _RecordingPool)
        results = BatchExplorer(config=str(config)).run()

        # Two runs came back through the recorded pool -- their status is
        # the chemistry's business (a one-atom PDB may well fail setup),
        # and the scheduler's business is that both were dispatched and
        # collected.
        assert len(results) == 2

        context = _RecordingPool.made_with["mp_context"]
        assert context is not None, "the pool was made without an mp_context"
        assert context.get_start_method() == "spawn"
