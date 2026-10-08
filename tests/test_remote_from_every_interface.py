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


# ---------------------------------------------------------------------------
# An AI app
# ---------------------------------------------------------------------------
STUDY = "systems:\n  - system: ghg.pdb\nsimulation:\n  duration_ns: 5\noutput: ghg_run\n"
ELICIT = {"elicitation": {"form": {}}}
YES = {"action": "accept", "content": {"go": True}}


@pytest.fixture
def app(machine, monkeypatch):
    """An MCP server on the study's folder, reaching the stand-in machine."""
    from fastmdxplora.mcp import App, Workspace
    from fastmdxplora.remote import api
    from fastmdxplora.remote import send as sending
    from tests._mcp_wire import Wire
    from tests.test_an_ai_app_reads_and_checks_studies import _structure

    root = machine.study.parent
    _structure(root / "ghg.pdb")
    (root / "ghg.yml").write_text(STUDY)
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(root.parent / "cache"))
    monkeypatch.setattr(api, "_link", lambda name, transport: machine.transport())
    monkeypatch.setattr(api, "this_code", lambda: RELEASE)
    monkeypatch.setattr(sending, "this_code", lambda: RELEASE)
    real = sending.run_here
    monkeypatch.setattr(sending, "run_here", lambda command, runner=None, **more:
                        real(command, runner=machine.local, **more))
    wire = Wire(App(Workspace.at(root)).server())
    wire.root, wire.machine = root, machine
    yield wire
    wire.close()


def _call(wire, tool, capabilities=None, **arguments):
    return wire.request("tools/call", {"name": tool, "arguments": arguments},
                        capabilities=capabilities)["result"]


def _text(result) -> str:
    return result["content"][0]["text"]


def _start(wire, capabilities=ELICIT, answer=None, config="ghg.yml"):
    from fastmdxplora.mcp.tools import plan_id_of

    arguments = {"config": config, "plan_id": plan_id_of(wire.root / config),
                 "machine": "box"}
    first = wire.request("tools/call", {"name": "start_study", "arguments": arguments},
                         capabilities=capabilities)["result"]
    if answer is None or first.get("resultType") != "input_required":
        return first, first
    done = wire.request("tools/call", {
        "name": "start_study", "arguments": arguments, "inputResponses": {"send": answer},
        "requestState": first["requestState"]}, capabilities=capabilities)["result"]
    return first, done


def _sent(wire) -> bool:
    return any(c.startswith("mkdir") for c in wire.machine.commands)


def _finished(wire):
    return travels._until_finished(wire.machine, "ghg_run")


class TestAnAIApp:
    def test_the_machines_are_listed_from_their_records(self, app):
        said = _text(_call(app, "list_machines"))
        assert "  box (workstation, no GPU found): not ready" in said
        assert app.machine.commands == []

    def test_a_study_is_sent_once_the_person_says_so(self, app):
        first, done = _start(app, answer=YES)
        asked = first["inputRequests"]["send"]["params"]["message"]
        assert asked.startswith("Send the study in ghg.yml to box and run it there?\n")
        assert "Sent with it:\n  ghg.pdb (" in asked
        assert "Results come back to ghg_run when fetched." in asked
        assert _text(done).startswith("Sent to box as job ghg_run (process ")
        sent = app.machine.home / "fastmdxplora-jobs" / "ghg_run"
        assert (sent / "inputs" / "ghg.pdb").is_file()
        assert "ghg_run on box" in _text(_call(app, "list_machines"))

    @pytest.mark.parametrize("answer", [{"action": "decline"},
                                        {"action": "accept", "content": {"go": False}}])
    def test_anything_but_a_yes_sends_nothing(self, app, answer):
        _, done = _start(app, answer=answer)
        assert _text(done) == "Not sent: the person did not go ahead."
        assert not _sent(app)

    def test_an_ai_app_that_cannot_ask_sends_nothing(self, app):
        _, done = _start(app, capabilities={})
        assert done["isError"]
        assert "cannot ask the person" in _text(done)
        assert "fastmdx remote send -c ghg.yml --machine box" in _text(done)
        assert not _sent(app)

    def test_a_machine_without_a_record_is_refused(self, app):
        from fastmdxplora.mcp.tools import plan_id_of

        result = _call(app, "start_study", config="ghg.yml",
                       plan_id=plan_id_of(app.root / "ghg.yml"), machine="gpu-box")
        assert result["isError"] and "gpu-box" in _text(result)
        assert app.machine.commands == []

    def test_a_file_outside_the_config_s_folder_is_not_sent(self, app):
        (app.root / "sub").mkdir()
        # Inside the workspace, and outside the folder the config is in.
        (app.root / "sub" / "far.yml").write_text(
            STUDY.replace("ghg.pdb", str(app.root / "ghg.pdb")))
        _, done = _start(app, config="sub/far.yml", answer=YES)
        assert done["isError"]
        assert "outside the study's folder" in _text(done)
        assert not _sent(app)

    def test_status_fetch_and_the_answer_kept(self, app):
        _start(app, answer=YES)
        _finished(app)
        asked = len(app.machine.commands)
        said = _text(_call(app, "remote_status", job="ghg_run"))
        assert said.startswith("  ghg_run on box: done")
        assert "fetch_study brings its results here." in said
        assert len(app.machine.commands) == asked   # under 30 s old: not asked again
        fetched = _text(_call(app, "fetch_study", job="ghg_run"))
        assert fetched.startswith("Fetched ghg_run into ghg_run (")
        assert (app.root / "ghg_run" / "manifest.json").is_file()

    def test_a_fetch_says_each_size_before_anything_moves(self, app):
        _start(app, answer=YES)
        _finished(app)
        first = _call(app, "fetch_study", capabilities=ELICIT, job="ghg_run")
        asked = first["inputRequests"]["fetch"]["params"]["message"]
        assert asked.startswith("Fetch ghg_run from box into ghg_run?\nResults: ")
        assert "Trajectories and checkpoints stay on box (1 files, " in asked
        assert not (app.root / "ghg_run").exists()

    def test_a_large_fetch_is_not_made_unasked(self, app, monkeypatch):
        monkeypatch.setattr("fastmdxplora.mcp.remote_tools.LARGE_BYTES", 0)
        _start(app, answer=YES)
        _finished(app)
        result = _call(app, "fetch_study", job="ghg_run")
        assert result["isError"] and "cannot ask the person first" in _text(result)
        assert not (app.root / "ghg_run").exists()

    def test_a_job_for_another_folder_is_not_reached(self, app, tmp_path):
        _send(app.machine)      # its results go to tmp_path/back, not the workspace
        for tool in ("remote_status", "fetch_study", "cancel_study"):
            result = _call(app, tool, job="trial")
            assert result["isError"] and "No job called 'trial'" in _text(result)

    def test_a_job_is_cancelled_once_the_person_agrees(self, app):
        app.machine.env["FAKE_SLEEP"] = "30"
        _start(app, answer=YES)
        first = _call(app, "cancel_study", capabilities=ELICIT, job="ghg_run")
        assert first["inputRequests"]["cancel"]["params"]["message"].startswith(
            "Stop ghg_run on box?")
        said = _text(_call(app, "cancel_study", job="ghg_run"))
        assert said.startswith("Stopped ghg_run on box.")

    def test_a_read_only_server_offers_the_looks_only(self, machine):
        from fastmdxplora.mcp import App, Workspace
        from tests._mcp_wire import Wire

        wire = Wire(App(Workspace.at(machine.study.parent), runs=False).server())
        try:
            names = {t["name"] for t in wire.request("tools/list")["result"]["tools"]}
        finally:
            wire.close()
        assert {"list_machines", "remote_status"} <= names
        assert not {"start_study", "fetch_study", "cancel_study"} & names
