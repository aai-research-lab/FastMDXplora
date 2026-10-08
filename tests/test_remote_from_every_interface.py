"""Remote machines reached from every interface, under one set of rules.

The rules the command line kept by having a person at a terminal hold in
the engine, for the Python API and an AI app alike: one study at a time on
a workstation, a machine asked about a job at most every 30 s by a caller
that loops, machines only from records made at a terminal, and no prompt
nobody can answer.
"""

from __future__ import annotations

import time

import pytest

from fastmdxplora.refusals import refusal_of
from fastmdxplora.remote.send import STATUS_KEPT_S, cancel, prepare, send, status
from tests import test_a_study_travels_and_comes_back as travels
from tests.test_a_study_travels_and_comes_back import RELEASE, _send

machine = travels.machine
machine_path = travels.machine_path


# ---------------------------------------------------------------------------
# One study at a time on a workstation
# ---------------------------------------------------------------------------
class TestOneAtATime:
    def test_a_second_study_is_refused_while_the_first_runs(self, machine, tmp_path):
        machine.env["FAKE_SLEEP"] = "30"
        first = _send(machine)
        with pytest.raises(ValueError) as caught:
            prepare(machine.study, "box", output=str(tmp_path / "back" / "second"),
                    code=RELEASE, transport=machine.transport())
        found = refusal_of(caught.value)
        assert found.code == "remote.machine.busy"
        assert found.details["job"] == first.name
        assert "fastmdx remote cancel trial" in found.message
        cancel(first.name, transport=machine.transport())
        prepare(machine.study, "box", output=str(tmp_path / "back" / "second"),
                code=RELEASE, transport=machine.transport())

    def test_one_prepared_before_the_first_was_sent_is_refused_at_the_send(
            self, machine, tmp_path):
        machine.env["FAKE_SLEEP"] = "30"
        second = prepare(machine.study, "box", output=str(tmp_path / "back" / "second"),
                         code=RELEASE, transport=machine.transport())
        first = _send(machine)
        try:
            with pytest.raises(ValueError) as caught:
                send(second, transport=machine.transport(), local_runner=machine.local,
                     code=RELEASE)
            assert refusal_of(caught.value).code == "remote.machine.busy"
        finally:
            cancel(first.name, transport=machine.transport())

    def test_a_job_that_ended_is_asked_about_and_frees_the_machine(self, machine, tmp_path):
        _send(machine)
        time.sleep(0.5)
        # Its record still says running; the machine is asked, and says done.
        prepare(machine.study, "box", output=str(tmp_path / "back" / "second"),
                code=RELEASE, transport=machine.transport())


# ---------------------------------------------------------------------------
# Asked about at most every 30 s
# ---------------------------------------------------------------------------
class TestAskedNotTooOften:
    def test_a_caller_that_loops_reads_the_answer_kept(self, machine):
        machine.env["FAKE_SLEEP"] = "30"
        job = _send(machine)
        try:
            asked = len(machine.commands)
            status(job.name, transport=machine.transport(), max_age_s=STATUS_KEPT_S)
            once = len(machine.commands)
            assert once == asked + 1
            for _ in range(5):
                status(job.name, transport=machine.transport(), max_age_s=STATUS_KEPT_S)
            assert len(machine.commands) == once
            # The command line asks there and then.
            status(job.name, transport=machine.transport())
            assert len(machine.commands) == once + 1
        finally:
            cancel(job.name, transport=machine.transport())

    def test_an_answer_older_than_that_is_asked_again(self, machine, monkeypatch):
        machine.env["FAKE_SLEEP"] = "30"
        job = _send(machine)
        try:
            status(job.name, transport=machine.transport(), max_age_s=STATUS_KEPT_S)
            asked = len(machine.commands)
            later = time.time() + STATUS_KEPT_S + 1
            with monkeypatch.context() as patched:
                patched.setattr("fastmdxplora.remote.send.time.time", lambda: later)
                status(job.name, transport=machine.transport(), max_age_s=STATUS_KEPT_S)
            assert len(machine.commands) == asked + 1
        finally:
            cancel(job.name, transport=machine.transport())
