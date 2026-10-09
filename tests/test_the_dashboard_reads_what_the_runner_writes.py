"""The dashboard reads what the runner writes, and reads it safely.

Two couplings from the playback-overlay work, each fixed and pinned here.

`_telemetry_predates_process` decides whether the telemetry on disk belongs
to the process that is running or to the one before it. It parsed both
timestamps inline and caught `ValueError` only, so a *naive* timestamp --
which is what an older run, or any writer that did not use
`datetime.now(timezone.utc)`, leaves in `live_status.json` -- reached the
comparison and raised `TypeError: can't compare offset-naive and
offset-aware datetimes`, inside the dashboard's polling path.
`gui/telemetry.py` already had `_parse_iso_datetime`, which normalises a
naive value to UTC and returns None instead of raising.

And the explanation shown for the commonest failure was matched by a string
literal written out twice, once where it is emitted and once where it is
read. A reworded message would have stopped being recognised with nothing
failing anywhere.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fastmdxplora.batch.explorer import ALREADY_HOLD_RESULTS
from fastmdxplora.gui.exploration import DashboardRuntime


def _runtime(tmp_path, process_started_at):
    runtime = DashboardRuntime(tmp_path / "workspace", tmp_path / "runs")
    runtime.active_root = tmp_path / "run"
    (runtime.active_root / "simulation").mkdir(parents=True)
    runtime.process_started_at = process_started_at
    return runtime


def _write_status(runtime, run_started_at):
    import json
    path = runtime.active_root / "simulation" / "live_status.json"
    path.write_text(json.dumps({"run_started_at": run_started_at}))


class TestANaiveTimestampDoesNotRaise:
    # The times are taken as the test runs, not as the suite is collected:
    # a suite of over an hour made "an hour later" the past by its turn.
    @pytest.mark.parametrize("hours, expected", [
        # Naive, an hour earlier: telemetry is from the previous run.
        (-1, True),
        # Naive, an hour later: it belongs to this one.
        (1, False),
    ])
    def test_naive_values_are_compared_as_utc(self, tmp_path, hours, expected):
        now = datetime.now(timezone.utc)
        recorded = (now + timedelta(hours=hours)).replace(tzinfo=None).isoformat()
        runtime = _runtime(tmp_path, now.isoformat())
        _write_status(runtime, recorded)

        assert runtime._telemetry_predates_process() is expected

    def test_an_aware_value_still_works(self, tmp_path):
        runtime = _runtime(tmp_path, datetime.now(timezone.utc).isoformat())
        _write_status(
            runtime,
            (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())

        assert runtime._telemetry_predates_process() is True

    def test_an_unparseable_value_is_not_a_verdict(self, tmp_path):
        """Returning False leaves the run's data on show, which is the
        recoverable direction: hiding good telemetry is worse than showing
        stale telemetry the person can see the timestamp of."""
        runtime = _runtime(tmp_path, datetime.now(timezone.utc).isoformat())
        _write_status(runtime, "not a timestamp")

        assert runtime._telemetry_predates_process() is False


class TestTheExplanationTracksTheMessage:
    def test_the_runner_emits_the_constant(self):
        """If this is reworded, the dashboard follows it automatically."""
        from fastmdxplora.batch import explorer

        assert ALREADY_HOLD_RESULTS in explorer.ALREADY_HOLD_RESULTS

    def test_the_failure_note_quotes_the_log(self, tmp_path):
        log = tmp_path / "exploration.log"
        log.write_text(
            "starting\n"
            f"{ALREADY_HOLD_RESULTS}\n"
            "  runs/alpha\n"
            "exiting\n")
        runtime = DashboardRuntime(tmp_path / "workspace", tmp_path / "runs")
        runtime.log_path = log
        runtime.process_returncode = 2

        message = runtime._process_failure_message()

        assert ALREADY_HOLD_RESULTS in message
        assert "runs/alpha" in message

    def test_without_that_line_the_last_line_is_used(self, tmp_path):
        log = tmp_path / "exploration.log"
        log.write_text("starting\nsomething else went wrong\n")
        runtime = DashboardRuntime(tmp_path / "workspace", tmp_path / "runs")
        runtime.log_path = log
        runtime.process_returncode = 1

        assert "something else went wrong" in runtime._process_failure_message()
