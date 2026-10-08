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


def _stopped(job) -> None:
    deadline = time.monotonic() + 30
    while travels._alive(job.handle):
        assert time.monotonic() < deadline, "the job never stopped"
        time.sleep(0.05)


def _ended_there(job) -> None:
    """Wait for the job to write its exit code on the machine, its record
    left as it was."""
    from pathlib import Path

    deadline = time.monotonic() + 30
    while not (Path(job.remote_dir) / "exit_code").is_file():
        assert time.monotonic() < deadline, "the job never ended"
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# One study at a time on a workstation
# ---------------------------------------------------------------------------
class TestOneAtATime:
    def test_a_second_study_is_refused_while_the_first_runs(self, machine, tmp_path):
        machine.env["FAKE_SLEEP"] = "30"
        first = _send(machine)
        try:
            # Planned, so a dry run still shows what it would do, and said.
            second = prepare(machine.study, "box",
                             output=str(tmp_path / "back" / "second"),
                             code=RELEASE, transport=machine.transport())
            assert second.busy == first.name
            assert any("fastmdx remote cancel trial" in note for note in second.notes)
            with pytest.raises(ValueError) as caught:
                send(second, transport=machine.transport(), local_runner=machine.local,
                     code=RELEASE)
            found = refusal_of(caught.value)
            assert found.code == "remote.machine.busy"
            assert found.details["job"] == first.name
            assert not (machine.home / "fastmdxplora-jobs" / "second").exists()
        finally:
            cancel(first.name, transport=machine.transport())
        # Busy while the cancelled job is still stopping, then free.
        _stopped(first)
        again = prepare(machine.study, "box", output=str(tmp_path / "back" / "second"),
                        code=RELEASE, transport=machine.transport())
        assert again.busy == ""

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
        machine.env["FAKE_SLEEP"] = "5"
        first = _send(machine)
        # An answer kept from while it ran, under 30 s old, is not the answer.
        assert status(first.name, transport=machine.transport(),
                      max_age_s=STATUS_KEPT_S).state == "running"
        _ended_there(first)
        second = prepare(machine.study, "box", output=str(tmp_path / "back" / "second"),
                         code=RELEASE, transport=machine.transport())
        assert second.busy == ""


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


# ---------------------------------------------------------------------------
# What comes back stays in the job's folder; records under many callers
# ---------------------------------------------------------------------------
class TestWhatComesBack:
    def test_a_link_the_machine_left_is_neither_copied_nor_written_through(
            self, machine, tmp_path):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        victim = tmp_path / "victim.txt"
        victim.write_text("mine\n")
        folder = tmp_path / "victim-folder"
        folder.mkdir()
        job = _send(machine)
        travels._until_finished(machine, job.name)
        run = Path(job.run_dir)
        (run / "fetched.json").symlink_to(victim)
        (run / "remote_job.log").symlink_to(folder)
        (run / "secret").symlink_to(victim)
        _, warnings = fetch(job.name, transport=machine.transport(),
                            local_runner=machine.local, code=RELEASE)
        assert victim.read_text() == "mine\n"
        assert list(folder.iterdir()) == []
        assert not (machine.back / "secret").exists()
        assert not (machine.back / "fetched.json").is_symlink()
        assert (machine.back / "remote_job.log").read_text().strip() == "working"

    def test_no_file_larger_than_said_is_brought(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / "huge.bin").write_bytes(b"x" * 200_000)
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE, most_bytes=100_000)
        assert (machine.back / "manifest.json").is_file()
        assert not (machine.back / "huge.bin").exists()

    def test_a_job_that_wrote_no_run_has_no_sizes_to_give(self, machine):
        import shutil
        from pathlib import Path

        from fastmdxplora.remote import api

        job = _send(machine)
        travels._until_finished(machine, job.name)
        shutil.rmtree(Path(job.run_dir))
        sizes = api.fetch_sizes(job.name, transport=machine.transport())
        assert sizes.run_written is False and sizes.bringing(True) == 0

    def test_a_job_that_ended_unseen_is_not_signalled(self, machine):
        job = _send(machine)
        _ended_there(job)
        asked = len(machine.commands)
        stopped = cancel(job.name, transport=machine.transport())
        assert stopped.state == "done"
        assert not any("kill" in c for c in machine.commands[asked:])

    def test_the_results_folder_is_recorded_whole(self, machine, monkeypatch, tmp_path):
        from pathlib import Path

        from fastmdxplora.remote import api

        monkeypatch.chdir(tmp_path)
        sending = prepare(machine.study, "box", output="back/relative", code=RELEASE,
                          transport=machine.transport())
        assert sending.local_output == str(tmp_path / "back" / "relative")
        assert Path(sending.local_output).is_absolute()
        # A record from before, relative, is not taken for any folder's.
        from fastmdxplora.remote.jobs import Job, save_job

        save_job(Job("old", "box", "/x", "process", "1", "t", {}, "relative/old",
                     state="running"))
        assert [j.name for j in api.jobs(under=tmp_path)] == []

    def test_records_written_at_once_are_written_whole(self, machine):
        import threading

        from fastmdxplora.remote.jobs import Job, load_job, save_job

        job = Job("many", "box", "/x", "process", "1", "t", {}, "/tmp/many")
        failed: list[BaseException] = []

        def write(n: int) -> None:
            try:
                for i in range(20):
                    save_job(Job(**{**job.__dict__, "detail": f"{n}-{i}"}))
            except BaseException as exc:  # noqa: BLE001 - collected for the assert
                failed.append(exc)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert failed == []
        assert load_job("many").detail.endswith("-19")


class TestAnAIAppAndOtherFolders:
    def test_a_busy_machine_is_said_without_another_folder_s_job(self, app):
        app.machine.env["FAKE_SLEEP"] = "30"
        other = _send(app.machine)          # results go outside the workspace
        try:
            _, done = _start(app, answer=YES)
            assert done["isError"]
            said = _text(done)
            assert said.startswith("box is running a study sent from this computer.")
            assert other.name not in said
        finally:
            cancel(other.name, transport=app.machine.transport())

    def test_a_job_that_wrote_nothing_says_why_from_its_log(self, app):
        import shutil
        from pathlib import Path

        _start(app, answer=YES)
        job = _finished(app)
        shutil.rmtree(Path(job.run_dir))
        said = _text(_call(app, "fetch_study", job="ghg_run"))
        assert "before its run wrote anything on box" in said
        assert "working" in said

    def test_a_machine_that_cannot_be_reached_says_how_to_sign_in(self, app, monkeypatch):
        from fastmdxplora.refusals import StudyError
        from fastmdxplora.remote import api

        def unreachable(*args, **kwargs):
            raise StudyError("Could not reach box over ssh: Permission denied.",
                             code="environment.service.machine_unreachable",
                             machine="box", reason="denied")

        monkeypatch.setattr(api, "plan_send", unreachable)
        _, done = _start(app, answer=YES)
        assert "`fastmdx remote --machine box` in a terminal" in _text(done)


# ---------------------------------------------------------------------------
# The second review's cases
# ---------------------------------------------------------------------------
class TestSecondReview:
    def test_a_file_s_size_is_its_bytes_not_its_blocks(self, machine):
        from pathlib import Path

        from fastmdxplora.remote import api

        job = _send(machine)
        travels._until_finished(machine, job.name)
        with open(Path(job.run_dir) / "sparse.csv", "wb") as out:
            out.truncate(5_000_000)          # 5 MB said, almost no blocks
        sizes = api.fetch_sizes(job.name, transport=machine.transport())
        assert sizes.results >= 5_000_000

    def test_what_a_cap_leaves_behind_is_said(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / "huge.bin").write_bytes(b"x" * 200_000)
        _, warnings = fetch(job.name, transport=machine.transport(),
                            local_runner=machine.local, code=RELEASE, most_bytes=100_000)
        assert any("larger than the whole fetch" in w and "huge.bin" in w
                   for w in warnings)

    def test_a_named_pipe_that_comes_back_is_taken_out_before_anything_reads(
            self, machine):
        import os
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        if not hasattr(os, "mkfifo"):
            pytest.skip("no named pipes here")
        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / "manifest.json").unlink()
        os.mkfifo(Path(job.run_dir) / "manifest.json")
        (Path(job.run_dir) / "open.sh").write_text("x")
        (Path(job.run_dir) / "open.sh").chmod(0o4777)
        _, warnings = fetch(job.name, transport=machine.transport(),
                            local_runner=machine.local, code=RELEASE)
        assert not (machine.back / "manifest.json").exists()
        assert any("neither a file nor a folder" in w for w in warnings)
        assert (machine.back / "open.sh").stat().st_mode & 0o4002 == 0

    def test_a_home_that_keeps_no_locks_still_sends(self, machine, monkeypatch):
        import errno

        import fcntl

        def no_locks(handle, how):
            raise OSError(errno.ENOLCK, "No locks available")

        monkeypatch.setattr(fcntl, "flock", no_locks)
        assert _send(machine).state == "running"

    def test_a_kept_answer_is_read_while_a_fetch_holds_the_job(self, machine):
        import threading

        from fastmdxplora.remote.jobs import held

        machine.env["FAKE_SLEEP"] = "30"
        job = _send(machine)
        try:
            status(job.name, transport=machine.transport(), max_age_s=STATUS_KEPT_S)
            release = threading.Event()

            def hold() -> None:
                with held(job.name):
                    release.wait(10)

            holder = threading.Thread(target=hold)
            holder.start()
            started = time.monotonic()
            assert status(job.name, transport=machine.transport(),
                          max_age_s=STATUS_KEPT_S).state == "running"
            assert time.monotonic() - started < 2
            release.set()
            holder.join()
        finally:
            cancel(job.name, transport=machine.transport())

    def test_nothing_outside_the_workspace_is_sent_from_an_ai_app(self, app, tmp_path):
        import json

        # A link in the workspace, and a value that folds `link/..` away.
        reference = app.root.parent / "elsewhere" / "secret"
        (reference / "setup").mkdir(parents=True)
        (reference / "manifest.json").write_text(json.dumps({"phases": []}))
        (app.root / "lnk").symlink_to(app.root.parent / "elsewhere")
        (app.root / "more.yml").write_text(
            "systems:\n  - system: ghg.pdb\nsimulation:\n"
            "  setup_from: lnk/../elsewhere/secret\noutput: more_run\n")
        _, done = _start(app, config="more.yml", answer=YES)
        assert done["isError"]
        assert "outside the workspace" in _text(done)
        assert not _sent(app)

    def test_a_study_beside_but_outside_the_workspace_is_not_sent(self, app):
        import json

        reference = app.root.parent / "reference"
        (reference / "setup").mkdir(parents=True)
        (reference / "manifest.json").write_text(json.dumps({"phases": []}))
        (app.root / "beside.yml").write_text(
            "systems:\n  - system: ghg.pdb\nsimulation:\n"
            f"  setup_from: {reference}\noutput: beside_run\n")
        _, done = _start(app, config="beside.yml", answer=YES)
        assert done["isError"] and "outside the workspace" in _text(done)
        assert not _sent(app)


# ---------------------------------------------------------------------------
# The third review's cases
# ---------------------------------------------------------------------------
class TestThirdReview:
    def test_a_size_past_two_gigabytes_is_read_as_a_number(self, machine):
        from pathlib import Path

        from fastmdxplora.remote import api

        job = _send(machine)
        travels._until_finished(machine, job.name)
        with open(Path(job.run_dir) / "big.csv", "wb") as out:
            out.truncate(3_000_000_000)     # an awk that prints 3e+09 read 0
        assert api.fetch_sizes(job.name, transport=machine.transport()).results \
            >= 3_000_000_000

    def test_trajectories_left_behind_are_not_said_to_be_over_the_cap(self, machine):
        from pathlib import Path

        from fastmdxplora.remote import api
        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / "simulation" / "production.dcd").write_bytes(b"x" * 300_000)
        sizes = api.fetch_sizes(job.name, transport=machine.transport())
        _, warnings = fetch(job.name, transport=machine.transport(),
                            local_runner=machine.local, code=RELEASE,
                            most_bytes=sizes.bringing(False))
        assert not any("larger than the whole fetch" in w for w in warnings)
        assert any("1 trajectory" in w for w in warnings)

    def test_what_was_in_the_results_folder_before_is_left_alone(self, machine, tmp_path):
        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        machine.back.mkdir(parents=True)
        mine = tmp_path / "notes.txt"
        mine.write_text("mine")
        (machine.back / "my-notes").symlink_to(mine)
        _, warnings = fetch(job.name, transport=machine.transport(),
                            local_runner=machine.local, code=RELEASE)
        assert (machine.back / "my-notes").is_symlink()
        assert not any("neither a file nor a folder" in w for w in warnings)

    def test_a_folder_that_comes_back_closed_is_opened_and_looked_in(self, machine):
        import os
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        closed = Path(job.run_dir) / "closed"
        closed.mkdir()
        os.mkfifo(closed / "pipe")
        closed.chmod(0o355)
        _, warnings = fetch(job.name, transport=machine.transport(),
                            local_runner=machine.local, code=RELEASE)
        here = machine.back / "closed"
        assert here.stat().st_mode & 0o700 == 0o700
        assert not (here / "pipe").exists()
        assert any("closed/pipe" in w for w in warnings)

    def test_the_job_s_log_comes_back_bounded_and_only_as_a_file(self, machine):
        import shutil
        from pathlib import Path

        from fastmdxplora.remote.send import JOB_LOG_KEPT, fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        log = Path(job.remote_dir) / "job.log"
        log.write_text("x" * (2 * JOB_LOG_KEPT) + "\nthe end\n")
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        kept = (machine.back / "remote_job.log").read_text()
        assert len(kept) <= JOB_LOG_KEPT and kept.endswith("the end\n")
        shutil.rmtree(machine.back)
        log.unlink()
        (log / "deep").mkdir(parents=True)
        (log / "deep" / "big.dcd").write_bytes(b"x" * 1000)
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        assert not (machine.back / "remote_job.log").exists()

    def test_a_job_cancelled_and_still_stopping_keeps_the_machine_busy(
            self, machine, tmp_path):
        env = machine.home / ".conda" / "envs" / "fastmdx-1.0" / "bin" / "fastmdx"
        travels._tool(env, "trap '' TERM\n: > \"$HOME/trapped\"\nsleep 6")
        first = _send(machine)
        deadline = time.monotonic() + 30
        while not (machine.home / "trapped").exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        cancel(first.name, transport=machine.transport())
        second = prepare(machine.study, "box", output=str(tmp_path / "back" / "second"),
                         code=RELEASE, transport=machine.transport())
        assert second.busy == first.name
        assert any("still stopping" in note for note in second.notes)
        _stopped(first)
        again = prepare(machine.study, "box", output=str(tmp_path / "back" / "second"),
                        code=RELEASE, transport=machine.transport())
        assert again.busy == ""

    @pytest.mark.parametrize("name", ["trial\n", "box\n"])
    def test_a_name_ending_in_a_line_break_is_refused(self, name):
        from fastmdxplora.remote.jobs import check_job_name
        from fastmdxplora.remote.transport import check_machine_name

        for check in (check_job_name, check_machine_name):
            with pytest.raises(ValueError):
                check(name)

    def test_a_folder_that_cannot_be_listed_is_not_sent(self, tmp_path, monkeypatch):
        import os

        from fastmdxplora.remote.inputs import gather_inputs

        study = tmp_path / "study"
        (study / "ff" / "shut").mkdir(parents=True)
        real = os.scandir

        def scandir(path="."):
            if str(path).endswith("shut"):
                raise PermissionError(13, "Permission denied", str(path))
            return real(path)

        monkeypatch.setattr(os, "scandir", scandir)
        with pytest.raises(ValueError) as caught:
            gather_inputs({"setup": {"forcefield_files": ["ff"]}}, study)
        assert refusal_of(caught.value).code == "remote.input.outside"

    def test_an_ai_app_is_told_of_a_name_used_before_it_asks(self, app):
        from fastmdxplora.remote.jobs import Job, save_job

        save_job(Job("ghg_run", "box", "/x", "process", "1", "t", {}, "/elsewhere/ghg_run",
                     state="done"))
        first, _ = _start(app, answer=YES)
        assert first["isError"]
        assert "Set `output` in the config to a new folder name" in _text(first)
        assert not _sent(app)


# ---------------------------------------------------------------------------
# The fourth review's cases
# ---------------------------------------------------------------------------
class TestFourthReview:
    def test_the_results_folder_does_not_take_the_machine_s_mode(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        Path(job.run_dir).chmod(0o3777)
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        assert machine.back.stat().st_mode & 0o7002 == 0
        assert (machine.back / "manifest.json").is_file()

    def test_a_fetch_that_fails_leaves_nothing_of_itself(self, machine):
        import os
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        os.mkfifo(Path(job.run_dir) / "pipe")

        def copies_then_fails(command, **kwargs):
            machine.local(command, **kwargs)
            return type("Done", (), {"returncode": 23})()

        with pytest.raises(ValueError):
            fetch(job.name, transport=machine.transport(),
                  local_runner=copies_then_fails, code=RELEASE)
        # Only the fetch's own folder, which only this user can enter, kept
        # for the fetch again to go on from.
        kept = machine.back / f".fetching-{job.name}"
        assert list(machine.back.iterdir()) == [kept]
        assert kept.stat().st_mode & 0o077 == 0
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        assert not kept.exists() and not (machine.back / "pipe").exists()
        assert (machine.back / "manifest.json").is_file()

    def test_a_log_cut_inside_a_character_is_still_read(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import JOB_LOG_KEPT, fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        # Three bytes a tick: the last MiB starts inside one.
        (Path(job.remote_dir) / "job.log").write_text("✓" * (JOB_LOG_KEPT // 3 + 2),
                                                      encoding="utf-8")
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        assert (machine.back / "remote_job.log").read_text(encoding="utf-8").endswith(
            "✓")

    def test_an_exit_code_that_is_not_one_is_not_kept_whole(self, machine):
        from pathlib import Path

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.remote_dir) / "exit_code").write_text("x" * 200_000)
        job = status(job.name, transport=machine.transport())
        assert job.state == "failed" and len(job.detail) < 100

    def test_a_record_that_cannot_be_read_stops_nothing(self, machine, tmp_path):
        from fastmdxplora.remote import api
        from fastmdxplora.remote.jobs import jobs_dir

        jobs_dir().mkdir(parents=True, exist_ok=True)
        (jobs_dir() / "broken.json").write_text("{not json")
        (jobs_dir() / "partial.json").write_text('{"name": "partial"}')
        sending = prepare(machine.study, "box", output=str(tmp_path / "back" / "x"),
                          code=RELEASE, transport=machine.transport())
        assert sending.busy == ""
        assert api.jobs(under=tmp_path) == []

    def test_what_a_fetch_writes_is_readable_as_any_file_here(self, machine):
        import os
        import stat

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        mask = os.umask(0o022)
        try:
            fetch(job.name, transport=machine.transport(), local_runner=machine.local,
                  code=RELEASE)
        finally:
            os.umask(mask)
        for name in ("fetched.json", "remote_job.log"):
            assert stat.S_IMODE((machine.back / name).stat().st_mode) & 0o044 == 0o044

    def test_two_sends_at_once_put_one_study_on_a_workstation(self, machine, tmp_path):
        import threading

        machine.env["FAKE_SLEEP"] = "30"
        planned = [prepare(machine.study, "box", output=str(tmp_path / "back" / f"s{i}"),
                           code=RELEASE, transport=machine.transport()) for i in range(2)]
        outcomes: list[object] = []

        def go(sending) -> None:
            try:
                outcomes.append(send(sending, transport=machine.transport(),
                                     local_runner=machine.local, code=RELEASE))
            except ValueError as exc:
                outcomes.append(refusal_of(exc).code)

        threads = [threading.Thread(target=go, args=(p,)) for p in planned]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        sent = [o for o in outcomes if not isinstance(o, str)]
        try:
            assert len(sent) == 1
            assert outcomes.count("remote.machine.busy") == 1
        finally:
            for job in sent:
                cancel(job.name, transport=machine.transport())


# ---------------------------------------------------------------------------
# The fifth review's cases
# ---------------------------------------------------------------------------
class TestFifthReview:
    def test_a_run_record_from_the_machine_is_not_kept_here(self, machine):
        import json
        from pathlib import Path

        from fastmdxplora.orchestrator import RUN_PROCESS_FILE
        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / RUN_PROCESS_FILE).write_text(json.dumps({"pid": 1}))
        (Path(job.run_dir) / "runs" / "a").mkdir(parents=True)
        (Path(job.run_dir) / "runs" / "a" / RUN_PROCESS_FILE).write_text("{}")
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        assert not list(machine.back.rglob(RUN_PROCESS_FILE))

    def test_nothing_of_this_program_s_input_reaches_the_machine(self):
        import subprocess

        from fastmdxplora.remote.transport import Transport

        seen: list[dict] = []

        def runner(command, **kwargs):
            seen.append(kwargs)
            return subprocess.CompletedProcess(command, 0, "", "")

        Transport("box", runner=runner, interactive=False).run(["true"])
        assert seen[0].get("stdin") is subprocess.DEVNULL and "input" not in seen[0]

    @pytest.mark.parametrize("live", ["[1, 2]", "null", '"text"'])
    def test_a_live_record_that_is_not_one_is_read_as_nothing(self, live):
        from fastmdxplora.remote.send import _progress

        assert _progress(live) == ""

    def test_a_manifest_that_says_odd_things_does_not_stop_the_fetch(self, machine):
        import json
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / "manifest.json").write_text(json.dumps({"source": "x"}))
        job, _ = fetch(job.name, transport=machine.transport(),
                       local_runner=machine.local, code=RELEASE)
        assert job.fetched_at

    def test_a_size_that_is_not_a_number_reads_as_none(self, machine, monkeypatch):
        from fastmdxplora.remote import api
        from fastmdxplora.remote import send as sending

        job = _send(machine)
        travels._until_finished(machine, job.name)
        monkeypatch.setattr(sending, "_sizes_script", lambda job: (
            "echo fmdx:total=" + "9" * 400 + "\necho fmdx:trajectory=\u0663\n"
            "echo fmdx:files=1\n"))
        sizes = api.fetch_sizes(job.name, transport=machine.transport())
        assert sizes.results == 0 and sizes.trajectory == 0

    def test_a_fetch_again_copies_only_what_changed(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        commands: list[list[str]] = []

        def runner(command, **kwargs):
            commands.append(list(command))
            return machine.local(command, **kwargs)

        fetch(job.name, transport=machine.transport(), local_runner=runner,
              code=RELEASE)
        first = (machine.back / "manifest.json").stat().st_ino
        (Path(job.run_dir) / "analysis" / "new.csv").write_text("1\n")
        fetch(job.name, transport=machine.transport(), local_runner=runner,
              code=RELEASE)
        assert any(a.startswith("--compare-dest=") for a in commands[-1])
        assert (machine.back / "manifest.json").stat().st_ino == first
        assert (machine.back / "analysis" / "new.csv").is_file()

    def test_a_clash_is_refused_before_anything_moves(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / "zz.txt").write_text("z")
        (machine.back / "manifest.json").mkdir(parents=True)
        with pytest.raises(ValueError) as caught:
            fetch(job.name, transport=machine.transport(),
                  local_runner=machine.local, code=RELEASE)
        assert "nothing was put in place" in str(caught.value)
        assert sorted(p.name for p in machine.back.iterdir()) == ["manifest.json"]

    def test_a_record_that_is_not_an_object_is_skipped(self, machine, tmp_path):
        from fastmdxplora.remote import api
        from fastmdxplora.remote.jobs import jobs_dir

        jobs_dir().mkdir(parents=True, exist_ok=True)
        (jobs_dir() / "listy.json").write_text("[]")
        assert api.jobs() == []
        assert prepare(machine.study, "box", output=str(tmp_path / "x"), code=RELEASE,
                       transport=machine.transport()).busy == ""

    def test_a_planted_fetch_folder_does_not_come_back(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / f".fetching-{job.name}" / "run").mkdir(parents=True)
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        assert not (machine.back / f".fetching-{job.name}").exists()
