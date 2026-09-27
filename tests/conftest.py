"""Shared fixtures for the suite.

There is deliberately nothing here about optional chemistry backends. An
autouse fixture used to monkeypatch `cli.main._missing_chemistry_backends`
to `lambda: []` for every test, described as neutralising "an early
dependency preflight" that the CLI performs. The CLI does not perform one:
the decision not to was taken in `main()`, the two functions implementing
the other policy were left behind with no caller, and one of them
referenced a name defined nowhere in the tree. The fixture was protecting
the suite from code that could not run.

If a real preflight is added, its test isolation belongs beside it, and it
should be opt-in per test rather than autouse over the whole suite -- an
autouse fixture that disables a guard is indistinguishable from a guard
that does not work.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _logging_state_is_restored():
    """Give each test back the logging state it started with.

    `main()` calls `setup_console()`, which configures the package logger for
    a command-line session: it sets `propagate = False` so records are not
    printed twice, sets the level, and installs a console handler cached in a
    module-level global. That is right for a process that is a run of the
    tool, and wrong for a process that is a test suite, because it persists
    for every test that follows.

    The visible failure is a `caplog` assertion on a message the code did
    emit: `caplog` reads through propagation to the root logger, so once any
    earlier test has invoked the CLI, every later test asserting on log text
    sees an empty string. It is silent, it depends on collection order, and
    it points at the test that reads the log rather than the one that
    invoked the CLI.

    This restores state rather than suppressing behaviour: a test that
    configures the console still gets a configured console, and only the
    tests after it are protected. The distinction matters here -- the note
    above about autouse fixtures is about ones that switch a guard off, and
    a guard that is off is indistinguishable from a guard that does not
    work. Nothing is switched off below.
    """
    from fastmdxplora.utils import logging as fastmdx_logging

    base = logging.getLogger("fastmdx")
    propagate, level = base.propagate, base.level
    handlers = list(base.handlers)
    console = fastmdx_logging._console_handler
    try:
        yield
    finally:
        base.propagate = propagate
        base.setLevel(level)
        base.handlers[:] = handlers
        fastmdx_logging._console_handler = console


@pytest.fixture(autouse=True)
def _a_test_closes_the_figures_it_opens():
    """Figures a test opened and left open are closed after it, as
    matplotlib's own suite does. Only the test's own: one left open before
    it is not its to close. Tests that a failure leaves no figure open look
    inside their own body, so this cannot hide a leak from them."""
    import matplotlib.pyplot as plt

    before = set(plt.get_fignums())
    yield
    for number in set(plt.get_fignums()) - before:
        plt.close(number)


@pytest.fixture(autouse=True)
def _no_run_outlives_the_test_that_started_it(monkeypatch):
    """A study the GUI's runtime started during a test is stopped after it.

    `_spawn` starts `fastmdx explore` in a session of its own, so a run
    outlives the server that started it; that is the point of it, and it
    meant a test that launched one left it running. Tests that check what
    a launch writes started real studies of 1UAO and 1L2Y and let them
    run: eight were found still going an hour after the suite, taking most
    of a two-core machine and roughly doubling the suite's time. The runs
    a test started are stopped here, their whole process group, and a test
    that needs one running still has it while it runs.
    """
    import os
    import signal

    from fastmdxplora.gui.exploration import DashboardRuntime

    import functools

    started = []
    spawn = DashboardRuntime._spawn

    # Wrapped, so a test reading the method's source still reads its own.
    @functools.wraps(spawn)
    def spawn_and_remember(self, *args, **kwargs):
        result = spawn(self, *args, **kwargs)
        if getattr(self, "process", None) is not None:
            started.append(self.process)
        return result

    monkeypatch.setattr(DashboardRuntime, "_spawn", spawn_and_remember)
    yield
    for process in started:
        if process.poll() is not None:
            continue
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except ProcessLookupError:
            continue
        except Exception:  # noqa: BLE001 - it must not outlive the suite either way
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
