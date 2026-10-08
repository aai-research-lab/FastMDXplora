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


# ---------------------------------------------------------------------------
# The Python API
# ---------------------------------------------------------------------------
class TestThePythonAPI:
    def test_machines_are_the_records_and_nothing_is_asked(self, machine):
        from fastmdxplora.remote import api

        found = api.machines(code=RELEASE)
        assert [m.name for m in found] == ["box"]
        assert found[0].kind == "workstation" and found[0].inspected_at
        assert machine.commands == []
        assert [t.name for t in api.run_targets(code=RELEASE)] == [api.THIS_MACHINE, "box"]
        assert [t.name for t in api.run_targets(hosted=True)] == [api.THIS_MACHINE]

    @pytest.mark.parametrize("named", ["gpu-box", "me@elsewhere.org"])
    def test_a_machine_without_a_record_is_refused_unasked(self, machine, named):
        from fastmdxplora.remote import api

        with pytest.raises(ValueError) as caught:
            api.plan_send(machine.study, named, code=RELEASE, transport=machine.transport())
        assert refusal_of(caught.value).code == "remote.machine.unknown"
        assert machine.commands == []

    def test_no_prompt_is_waited_on_even_from_a_terminal(self, machine, monkeypatch):
        from fastmdxplora.remote import api

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        assert "BatchMode=yes" in api._link("box", None).ssh_options()

    def test_a_study_is_sent_watched_sized_and_fetched(self, machine, tmp_path):
        from fastmdxplora.remote import api

        sending = api.plan_send(machine.study, "box", output=machine.back, code=RELEASE,
                                transport=machine.transport())
        job = api.send_planned(sending, code=RELEASE, transport=machine.transport(),
                               local_runner=machine.local)
        for _ in range(100):
            job = api.status(job.name, max_age_s=0, transport=machine.transport())
            if job.state == "done":
                break
            time.sleep(0.1)
        assert job.state == "done"
        sizes = api.fetch_sizes(job.name, transport=machine.transport())
        assert sizes.trajectory_files == 1 and sizes.results > 0
        assert sizes.bringing(True) >= sizes.bringing(False) == sizes.results
        job, _ = api.fetch(job.name, transport=machine.transport(),
                           local_runner=machine.local, code=RELEASE)
        assert (machine.back / "manifest.json").is_file()
        assert [j.name for j in api.jobs(under=tmp_path)] == ["trial"]
        assert api.jobs(under=machine.study.parent) == []

    def test_the_sizes_of_a_job_gone_from_its_machine(self, machine):
        import shutil

        from fastmdxplora.remote import api

        job = _send(machine)
        travels._until_finished(machine, job.name)
        shutil.rmtree(job.remote_dir)
        with pytest.raises(ValueError) as caught:
            api.fetch_sizes(job.name, transport=machine.transport())
        assert refusal_of(caught.value).code == "remote.job.gone"
