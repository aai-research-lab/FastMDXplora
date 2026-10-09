"""Remote machines reached from every interface, under one set of rules.

The rules the command line kept by having a person at a terminal hold in
the engine, for the Python API and an AI app alike: a study sent to a
workstation only where its GPUs have room for it, a machine asked about a
job at most every 30 s by a caller that loops, machines only from records
made at a terminal, and no prompt nobody can answer.
"""

from __future__ import annotations

import json
import time

import pytest

from fastmdxplora.refusals import refusal_of
from fastmdxplora.remote.send import STATUS_KEPT_S, cancel, describe_sending, prepare, send, status
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
# Studies share a workstation's GPUs, where they fit
# ---------------------------------------------------------------------------
UUID_0 = "GPU-00000000-1111-2222-3333-444444444444"
UUID_1 = "GPU-11111111-1111-2222-3333-444444444444"


def _gpus(machine, *rows: tuple[int, str, int, int]) -> None:
    """GPUs on the stand-in machine, each (index, uuid, total, free) in MB,
    read by a stand-in ``nvidia-smi``; the processes using them are the
    lines of ``$HOME/apps.csv``."""
    lines = [f"{index}, {uuid}, Stand-in GPU, {total}, {total - free}, {free}, 7"
             for index, uuid, total, free in rows]
    (machine.home / "gpus.csv").write_text("\n".join(lines) + "\n")
    tool = machine.home / "gpu-bin" / "nvidia-smi"
    if not tool.exists():
        travels._tool(tool, '''
uuid=""; prev=""
for arg in "$@"; do [ "$prev" = -i ] && uuid=$arg; prev=$arg; done
echo "$*" >> "$HOME/nvidia-smi.calls"
case "$*" in
  *--query-gpu=memory.free*) awk -F', ' -v u="$uuid" '$2 == u {print $6}' "$HOME/gpus.csv" ;;
  *--query-gpu=*) cat "$HOME/gpus.csv" ;;
  *--query-compute-apps=*) cat "$HOME/apps.csv" 2>/dev/null ;;
esac''')
        machine.env["PATH"] = f"{tool.parent}:{machine.env['PATH']}"


def _holding(machine, megabytes: int, particles: int = 2000) -> None:
    """The stand-in run takes ``megabytes`` on the GPU it was given, as a
    process of its own group, and says its particles as a run does."""
    env = machine.home / ".conda" / "envs" / "fastmdx-1.0" / "bin" / "fastmdx"
    travels._tool(env, f'''
out=run; while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done
mkdir -p "$out/simulation"
echo '{{"version": "1.0"}}' > "$out/manifest.json"
echo "$CUDA_VISIBLE_DEVICES" > "$HOME/given_gpu"
echo "$CUDA_VISIBLE_DEVICES, $$, {megabytes}" >> "$HOME/apps.csv"
echo '{{"particles": {particles}, "steps": 10, "seconds": 1}}' > "$out/simulation/cost.json"
sleep "$FAKE_SLEEP"
exit "$FAKE_EXIT"''')


def _peptide(path) -> None:
    """Three alanines, whose particles setup's estimate works out."""
    lines = []
    for residue in range(1, 4):
        for k, (name, element, dx) in enumerate(
                (("N", "N", 0.0), ("CA", "C", 1.4), ("C", "C", 2.4), ("O", "O", 3.2))):
            serial = (residue - 1) * 4 + k + 1
            lines.append(f"ATOM  {serial:5d}  {name:<3s} ALA A{residue:4d}    "
                         f"{(residue - 1) * 3.8 + dx:8.3f}{0.0:8.3f}{0.0:8.3f}"
                         f"  1.00  0.00          {element:>2s}")
    path.write_text("\n".join([*lines, "END"]) + "\n")


def _second(machine, tmp_path, name="second"):
    return prepare(machine.study, "box", output=str(tmp_path / "back" / name),
                   code=RELEASE, transport=machine.transport())


class TestGpusShared:
    def test_a_second_study_shares_the_machine_and_the_plan_says_so(
            self, machine, tmp_path):
        machine.env["FAKE_SLEEP"] = "30"
        _gpus(machine, (0, UUID_0, 24000, 23000))
        first = _send(machine)
        try:
            second = _second(machine, tmp_path)
            assert second.running == [first.name] and second.no_room == ""
            said = "\n".join(describe_sending(second))
            assert "box is busy: trial sent from here is running there" in said
            assert "GPU 0 (Stand-in GPU): 23,000 MB free of 24,000 MB, 7% busy" in said
            job = send(second, transport=machine.transport(), local_runner=machine.local,
                       code=RELEASE)
            assert status(job.name, transport=machine.transport()).state == "running"
        finally:
            for name in ("trial", "second"):
                cancel(name, transport=machine.transport())

    def test_the_gpu_with_the_most_free_memory_is_the_one_used(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 2000), (1, UUID_1, 24000, 20000))
        _holding(machine, 1500)
        sending = _second(machine, tmp_path)
        assert f"export CUDA_VISIBLE_DEVICES={UUID_1}" in sending.script
        assert "Runs on GPU 1, where it fits with the fewest studies" in "\n".join(
            describe_sending(sending))
        job = send(sending, transport=machine.transport(), local_runner=machine.local,
                   code=RELEASE)
        travels._until_finished(machine, job.name)
        assert (machine.home / "given_gpu").read_text().strip() == UUID_1

    def test_what_a_study_needs_is_learned_from_a_run_measured_there(
            self, machine, tmp_path, monkeypatch):
        from fastmdxplora.remote import gpu_room

        monkeypatch.setattr(gpu_room, "SAMPLE_EVERY_S", 0.2)
        machine.env["FAKE_SLEEP"] = "2"
        _gpus(machine, (0, UUID_0, 24000, 23000))
        _holding(machine, 1500, particles=2000)
        _peptide(machine.study.parent / "top.pdb")   # its particles worked out here
        before = _second(machine, tmp_path, "trial")
        assert before.gpu.need.mb is None
        assert "The memory one run needs is not known yet" in "\n".join(
            describe_sending(before))
        job = send(before, transport=machine.transport(), local_runner=machine.local,
                   code=RELEASE)
        job = travels._until_finished(machine, job.name)
        assert job.state == "done" and job.extra["gpu_peak_mb"] == 1500
        status(job.name, transport=machine.transport())  # learned once, not twice
        assert [(r["particles"], r["peak_mb"]) for r in gpu_room.measured("box")] == [
            (2000, 1500)]
        after = _second(machine, tmp_path)
        # The estimate for the peptide is under the 2,000 particles measured,
        # so the most that run held, never scaled down, and 15% more.
        assert after.gpu.need.mb == 1725
        assert after.gpu.need.how.startswith(
            "from 1 run in mixed precision measured on box, for about ")

    def test_a_study_that_does_not_fit_is_refused_with_the_numbers(self, machine, tmp_path):
        from fastmdxplora.remote.gpu_room import learn

        _gpus(machine, (0, UUID_0, 24000, 8000))
        learn("box", job="before", particles=2000, peak_mb=20000, gpu="Stand-in GPU")
        sending = _second(machine, tmp_path)
        assert "needs about 23,000 MB of GPU memory" in sending.no_room
        assert "GPU 0 on box (Stand-in GPU) has 8,000 MB free for it now" in sending.no_room
        assert sending.no_room in sending.notes      # a dry run still shows its plan
        with pytest.raises(ValueError) as caught:
            send(sending, transport=machine.transport(), local_runner=machine.local,
                 code=RELEASE)
        assert refusal_of(caught.value).code == "remote.machine.no_room"
        assert not (machine.home / "fastmdxplora-jobs" / "second").exists()

    def test_more_than_any_gpu_holds_is_said_so(self, machine, tmp_path):
        from fastmdxplora.remote.gpu_room import learn

        _gpus(machine, (0, UUID_0, 16000, 16000))
        learn("box", job="before", particles=2000, peak_mb=20000, gpu="Stand-in GPU")
        assert "more than GPU 0 on box (Stand-in GPU) has at all (16,000 MB)" in _second(
            machine, tmp_path).no_room

    def test_a_study_from_here_still_starting_keeps_its_room(self, machine, tmp_path):
        from fastmdxplora.remote.gpu_room import learn

        machine.env["FAKE_SLEEP"] = "30"
        _gpus(machine, (0, UUID_0, 24000, 10000))
        learn("box", job="before", particles=2000, peak_mb=6000, gpu="Stand-in GPU")
        first = _send(machine)                      # holds nothing on the GPU yet
        try:
            second = _second(machine, tmp_path)
            assert "6,900 MB of it kept for 1 run from here not yet holding" in "\n".join(
                second.gpu.lines)
            assert "has 3,100 MB free for it now" in second.no_room
        finally:
            cancel(first.name, transport=machine.transport())

    def test_two_sends_at_once_do_not_both_take_the_room_for_one(self, machine, tmp_path):
        import threading

        from fastmdxplora.remote.gpu_room import learn

        machine.env["FAKE_SLEEP"] = "30"
        _gpus(machine, (0, UUID_0, 24000, 10000))
        learn("box", job="before", particles=2000, peak_mb=6000, gpu="Stand-in GPU")
        planned = [_second(machine, tmp_path, f"s{i}") for i in range(2)]
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
            assert outcomes.count("remote.machine.no_room") == 1
        finally:
            for job in sent:
                cancel(job.name, transport=machine.transport())

    def test_the_room_is_asked_again_as_the_run_starts(self, machine, tmp_path, monkeypatch):
        from fastmdxplora.remote import send as sending_module
        from fastmdxplora.remote.gpu_room import learn

        _gpus(machine, (0, UUID_0, 24000, 10000))
        learn("box", job="before", particles=2000, peak_mb=6000, gpu="Stand-in GPU")
        sending = _second(machine, tmp_path)
        assert sending.no_room == ""
        # Taken from elsewhere between the send's own check and the start.
        monkeypatch.setattr(sending_module, "_room_now", lambda *args: None)
        _gpus(machine, (0, UUID_0, 24000, 1000))
        job = send(sending, transport=machine.transport(), local_runner=machine.local,
                   code=RELEASE)
        job = travels._until_finished(machine, job.name)
        assert job.state == "failed"
        assert job.detail == ("not started: its GPU had 1,000 MB free as it started, "
                              "and it needs about 6,900 MB")
        assert not (machine.home / "fastmdxplora-jobs" / "second" / "run").exists()

    def test_a_study_on_the_cpu_does_not_ask_the_gpus(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 10))
        machine.study.write_text(machine.study.read_text()
                                 + "simulation:\n  platform: CPU\n")
        sending = _second(machine, tmp_path)
        assert sending.gpu is None and "CUDA_VISIBLE_DEVICES" not in sending.script
        calls = machine.home / "nvidia-smi.calls"   # the inspection's own is not this
        assert "utilization.gpu" not in (calls.read_text() if calls.exists() else "")

    def test_gpus_the_config_names_are_the_ones_checked(self, machine, tmp_path):
        from fastmdxplora.remote.gpu_room import learn

        _gpus(machine, (0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 3000))
        learn("box", job="before", particles=2000, peak_mb=6000, gpu="Stand-in GPU")
        text = machine.study.read_text()
        machine.study.write_text(text + "simulation:\n  device_index: '1'\n")
        sending = _second(machine, tmp_path)
        assert "CUDA_DEVICE_ORDER" not in sending.script     # as the config means it
        assert "CUDA_VISIBLE_DEVICES" not in sending.script
        assert "GPU 1 on box (Stand-in GPU) has 3,000 MB free" in sending.no_room
        machine.study.write_text(text + "simulation:\n  device_index: '3'\n")
        assert "The config names GPU 3, and box has GPU 0, 1." == _second(
            machine, tmp_path).no_room

    def test_runs_side_by_side_are_not_learned_from(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 23000))
        (machine.study.parent / "b.pdb").write_text("ATOM\n")
        machine.study.write_text("systems:\n  - system: top.pdb\n  - system: b.pdb\n"
                                 "include_phase: [analysis]\nanalysis:\n  topology: top.pdb\n"
                                 "execution:\n  workers: 2\n")
        sending = _second(machine, tmp_path)
        assert sending.gpu.learn is False and sending.gpu.at_once == {UUID_0: 2}

    def test_a_machine_without_nvidia_smi_is_sent_to_and_said(self, machine, tmp_path):
        sending = _second(machine, tmp_path)
        assert sending.gpu is None and sending.no_room == ""
        assert any("no GPU that nvidia-smi reads" in note for note in sending.notes)

    def test_a_damaged_gpu_record_is_read_as_none(self, machine, tmp_path):
        from fastmdxplora.remote.jobs import load_job, save_job

        machine.env["FAKE_SLEEP"] = "30"
        _gpus(machine, (0, UUID_0, 24000, 23000))
        first = _send(machine)
        try:
            job = load_job(first.name)
            job.extra["gpu"] = {"uuids": [UUID_0], "need_mb": "lots"}
            save_job(job)
            assert status(first.name, transport=machine.transport()).state == "running"
            assert _second(machine, tmp_path).no_room == ""
        finally:
            cancel(first.name, transport=machine.transport())


class TestWhatAStudyNeeds:
    @pytest.fixture(autouse=True)
    def settings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))

    def test_one_run_is_scaled_up_by_particles_and_never_down(self):
        from fastmdxplora.remote.gpu_room import learn, need_for

        learn("box", job="a", particles=10_000, peak_mb=1000, gpu="g")
        assert need_for("box", 20_000).mb == 2300
        assert need_for("box", 5_000).mb == 1150

    def test_runs_of_different_sizes_give_a_line(self):
        from fastmdxplora.remote.gpu_room import learn, need_for

        learn("box", job="a", particles=10_000, peak_mb=600, gpu="g")
        learn("box", job="b", particles=110_000, peak_mb=1600, gpu="g")
        # 500 MB, and 10 MB per 1,000 particles: 1,100 MB, and 15% more.
        assert need_for("box", 60_000).mb == 1265
        assert need_for("box", 0).mb == 690          # never below the least measured

    def test_another_machine_has_its_own_runs(self):
        from fastmdxplora.remote.gpu_room import learn, need_for

        learn("box", job="a", particles=10_000, peak_mb=1000, gpu="g")
        assert need_for("other", 10_000).mb is None

    def test_a_machine_s_answer_is_read_only_where_plain(self):
        from fastmdxplora.remote.gpu_room import room_from

        room = room_from({
            "gpu": [f"0, {UUID_0}, A, B, 24000, 1000, 23000, [N/A]",
                    "1, GPU-x; rm -rf ~, Bad, 24000, 0, 24000, 0",
                    f"2, {UUID_1}, C, 24000, x, 23000, 0"],
            "app": [f"{UUID_0} 77 500 77", f"{UUID_0} 78 lots 78", "GPU-$(id) 1 1 1"]})
        assert [(g.index, g.name, g.busy_pct) for g in room.gpus] == [(0, "A, B", None)]
        assert room.used_by("77", UUID_0) == 500 and len(room.apps) == 1
        assert room_from({"gpus": ["none"]}) is None and room_from({}) is None

    def test_a_record_of_runs_that_cannot_be_read_gives_none(self, tmp_path):
        from fastmdxplora.remote.gpu_room import measured, need_for

        folder = tmp_path / "settings" / "gpu_memory"
        folder.mkdir(parents=True)
        (folder / "box.json").write_text('{"runs": [{"particles": "x", "peak_mb": 1}, 3]}')
        assert measured("box") == [] and need_for("box", 10).mb is None
        (folder / "box.json").write_text("not json")
        assert measured("box") == []


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
    server = App(Workspace.at(root)).server()
    wire = Wire(server)
    wire.root, wire.machine, wire.server = root, machine, server
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

        save_job(Job("old", "box", "/x", "process", "4242", "t", {}, "relative/old",
                     state="running"))
        assert [j.name for j in api.jobs(under=tmp_path)] == []

    def test_records_written_at_once_are_written_whole(self, machine):
        import threading

        from fastmdxplora.remote.jobs import Job, load_job, save_job

        job = Job("many", "box", "/x", "process", "4242", "t", {}, "/tmp/many")
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
            first, done = _start(app, answer=YES)
            asked = first["inputRequests"]["send"]["params"]["message"]
            assert "box is busy: 1 other study sent from here is running there." in asked
            assert other.name not in asked
            assert _text(done).startswith("Sent to box as job ghg_run")
        finally:
            for name in (other.name, "ghg_run"):
                cancel(name, transport=app.machine.transport())

    def test_a_study_that_does_not_fit_is_refused_before_the_person_is_asked(self, app):
        from fastmdxplora.remote.gpu_room import learn

        _gpus(app.machine, (0, UUID_0, 24000, 1000))
        learn("box", job="before", particles=2000, peak_mb=6000, gpu="Stand-in GPU")
        first, _ = _start(app, answer=YES)
        assert first["isError"] and "needs about 6,900 MB" in _text(first)
        assert not _sent(app)

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
        closed.chmod(0o555)  # readable there, as a user's own run is
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

    def test_a_job_cancelled_is_not_said_to_be_running(self, machine, tmp_path):
        machine.env["FAKE_SLEEP"] = "30"
        first = _send(machine)
        cancel(first.name, transport=machine.transport())
        assert _second(machine, tmp_path).running == []

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

        save_job(Job("ghg_run", "box", "/x", "process", "4242", "t", {}, "/elsewhere/ghg_run",
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
        assert sending.running == []
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
                       transport=machine.transport()).running == []

    def test_a_planted_fetch_folder_does_not_come_back(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / f".fetching-{job.name}" / "run").mkdir(parents=True)
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        assert not (machine.back / f".fetching-{job.name}").exists()


# ---------------------------------------------------------------------------
# The sixth review's cases
# ---------------------------------------------------------------------------
class TestSixthReview:
    def _done(self, machine):
        job = _send(machine)
        travels._until_finished(machine, job.name)
        return job

    def test_a_link_where_the_run_has_a_folder_is_refused_before_anything_moves(
            self, machine, tmp_path):
        from fastmdxplora.remote.send import fetch

        job = self._done(machine)
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        moved = tmp_path / "moved-analysis"
        (machine.back / "analysis").rename(moved)
        (machine.back / "analysis").symlink_to(moved)
        with pytest.raises(ValueError) as caught:
            fetch(job.name, transport=machine.transport(), local_runner=machine.local,
                  code=RELEASE)
        assert "is a link here" in str(caught.value)
        assert (machine.back / "analysis").is_symlink()

    def test_a_folder_that_cannot_be_written_is_refused_before_anything_moves(
            self, machine, monkeypatch):
        import os

        from fastmdxplora.remote.send import fetch

        job = self._done(machine)
        (machine.back / "simulation").mkdir(parents=True)
        real = os.access
        monkeypatch.setattr(os, "access", lambda p, mode: False if str(p).endswith(
            "simulation") and mode == os.W_OK else real(p, mode))
        with pytest.raises(ValueError) as caught:
            fetch(job.name, transport=machine.transport(), local_runner=machine.local,
                  code=RELEASE)
        assert "cannot be written to" in str(caught.value)
        assert not (machine.back / "manifest.json").exists()

    def test_a_copy_kept_from_a_fetch_with_other_options_is_not_used(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = self._done(machine)
        (Path(job.run_dir) / "simulation" / "production.dcd").write_bytes(b"x" * 5000)

        def copies_then_fails(command, **kwargs):
            machine.local(command, **kwargs)
            return type("Done", (), {"returncode": 23})()

        with pytest.raises(ValueError):
            fetch(job.name, with_trajectory=True, transport=machine.transport(),
                  local_runner=copies_then_fails, code=RELEASE)
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        assert not (machine.back / "simulation" / "production.dcd").exists()

    def test_every_trajectory_left_is_counted(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = self._done(machine)
        for i in range(204):
            (Path(job.run_dir) / f"piece-{i}.xtc").write_bytes(b"x")
        _, warnings = fetch(job.name, transport=machine.transport(),
                            local_runner=machine.local, code=RELEASE)
        assert any(w.startswith("205 trajectory") for w in warnings)

    def test_a_job_log_that_is_a_link_is_not_read(self, machine, tmp_path):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = self._done(machine)
        secret = tmp_path / "elsewhere.txt"
        secret.write_text("theirs")
        log = Path(job.remote_dir) / "job.log"
        log.unlink()
        log.symlink_to(secret)
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        assert not (machine.back / "remote_job.log").exists()

    def test_what_comes_back_takes_this_computer_s_mask(self, machine):
        import stat
        from pathlib import Path

        import fastmdxplora.remote.send as sending
        from fastmdxplora.remote.send import fetch

        job = self._done(machine)
        (Path(job.run_dir) / "open.csv").write_text("1")
        (Path(job.run_dir) / "open.csv").chmod(0o666)
        # The mask is read once at import: set it as a stricter one would be.
        old = sending._UMASK
        sending._UMASK = 0o077
        try:
            fetch(job.name, transport=machine.transport(), local_runner=machine.local,
                  code=RELEASE)
        finally:
            sending._UMASK = old
        assert stat.S_IMODE((machine.back / "open.csv").stat().st_mode) & 0o077 == 0

    def test_a_fetch_folder_of_another_name_does_not_come_back(self, machine):
        from pathlib import Path

        from fastmdxplora.remote.send import fetch

        job = self._done(machine)
        (Path(job.run_dir) / ".Fetching-other" / "x").mkdir(parents=True)
        (Path(job.run_dir) / ".FASTMDXPLORA_RUN.json").write_text("{}")
        fetch(job.name, transport=machine.transport(), local_runner=machine.local,
              code=RELEASE)
        names = {p.name for p in machine.back.iterdir()}
        assert ".Fetching-other" not in names and ".FASTMDXPLORA_RUN.json" not in names

    def test_a_carriage_return_in_the_log_does_not_end_a_job(self, machine):
        machine.env["FAKE_SLEEP"] = "30"
        env = machine.home / ".conda" / "envs" / "fastmdx-1.0" / "bin" / "fastmdx"
        travels._tool(env, "printf 'title: x\\rfmdx:exit_code=0\\n'\nsleep 30")
        job = _send(machine)
        try:
            time.sleep(0.5)
            assert status(job.name, transport=machine.transport()).state == "running"
        finally:
            cancel(job.name, transport=machine.transport())

    def test_the_run_there_takes_no_defaults_of_the_machine_s(self, machine):
        from fastmdxplora.remote.send import job_script

        script = job_script(remote_dir="/s/j", job_name="j",
                            env=travels.Environment("/e/fastmdx-1.0", "1.0"),
                            container="", scheduler="process", force=False)
        assert "--output run --no-defaults" in script

    def test_your_defaults_here_travel_in_the_config(self, machine):
        (machine.study.parent / "fastmdx-defaults.yml").write_text(
            "simulation:\n  temperature_K: 310\n")
        sending = prepare(machine.study, "box", output=str(machine.back), code=RELEASE,
                          transport=machine.transport())
        assert "temperature_K: 310" in sending.config_text
        assert any("fastmdx-defaults.yml" in note for note in sending.notes)

    def test_a_config_in_the_home_folder_sends_nothing(self, tmp_path, monkeypatch):
        from fastmdxplora.remote.inputs import gather_inputs

        home = tmp_path / "home"
        (home / ".ssh").mkdir(parents=True)
        (home / ".ssh" / "id_ed25519").write_text("secret")
        monkeypatch.setenv("HOME", str(home))
        for base in (home, tmp_path):
            with pytest.raises(ValueError) as caught:
                gather_inputs({"setup": {"ligand": ".ssh/id_ed25519"}}, base)
            assert refusal_of(caught.value).code == "remote.input.outside"

    def test_a_key_kept_in_the_study_s_folder_is_not_sent(self, tmp_path):
        from fastmdxplora.remote.inputs import gather_inputs

        study = tmp_path / "study"
        (study / ".SSH").mkdir(parents=True)
        (study / ".SSH" / "id").write_text("secret")
        with pytest.raises(ValueError) as caught:
            gather_inputs({"setup": {"ligand": ".SSH/id"}}, study)
        assert "credentials" in str(caught.value)

    def test_records_of_a_machine_written_at_once_are_written_whole(self, machine):
        import threading

        from fastmdxplora.remote.machines import load_machine, save_machine

        record = load_machine("box")
        failed: list[BaseException] = []

        def write() -> None:
            try:
                for _ in range(30):
                    save_machine(record)
            except BaseException as exc:  # noqa: BLE001 - collected for the assert
                failed.append(exc)

        threads = [threading.Thread(target=write) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert failed == []

    @pytest.mark.parametrize("given", [{"partition": "gpu\nrm -rf ~"},
                                       {"time_limit": "1:00\n#x"}])
    def test_a_partition_or_time_with_a_line_break_is_refused(self, machine, given):
        with pytest.raises(ValueError):
            prepare(machine.study, "box", output=str(machine.back), code=RELEASE,
                    transport=machine.transport(), **given)

    def test_a_job_still_running_is_not_replaced(self, machine):
        machine.env["FAKE_SLEEP"] = "30"
        job = _send(machine)
        try:
            cancel_free = prepare(machine.study, "box", output=str(machine.back),
                                  code=RELEASE, transport=machine.transport(), force=True)
            cancel_free.scheduler = "slurm"  # on a cluster too
            with pytest.raises(ValueError) as caught:
                send(cancel_free, transport=machine.transport(),
                     local_runner=machine.local, code=RELEASE)
            assert "replaces a job once it has ended" in str(caught.value)
        finally:
            cancel(job.name, transport=machine.transport())

    def test_no_terminal_means_no_prompt_through_a_jump_host_either(self):
        import subprocess

        from fastmdxplora.remote.transport import Transport

        seen: list[dict] = []

        def runner(command, **kwargs):
            seen.append(kwargs)
            return subprocess.CompletedProcess(command, 0, "", "")

        Transport("box", runner=runner, interactive=False).run(["true"])
        assert seen[0]["start_new_session"] is True
        assert seen[0]["env"]["SSH_ASKPASS_REQUIRE"] == "never"


class TestSeventhReview:
    def test_the_send_s_copy_is_run_where_nobody_can_be_asked(self, machine):
        import subprocess

        seen: list[dict] = []

        def spied(command, **kwargs):
            if command[0] == "rsync":
                seen.append(kwargs)
            return machine.local(command, **kwargs)

        sending = prepare(machine.study, "box", output=str(machine.back), code=RELEASE,
                          transport=machine.transport())
        job = send(sending, transport=machine.transport(), local_runner=spied,
                   code=RELEASE)
        travels._until_finished(machine, job.name)
        assert seen and seen[0]["env"]["SSH_ASKPASS_REQUIRE"] == "never"
        assert seen[0]["start_new_session"] is True
        assert seen[0]["stdin"] is subprocess.DEVNULL

    @pytest.mark.parametrize("inside", [".ssh/id_ed25519", ".config/gh/hosts.yml",
                                        "deeper/.GnuPG/key"])
    def test_a_folder_holding_keys_does_not_travel(self, tmp_path, inside):
        from fastmdxplora.remote.inputs import gather_inputs

        study = tmp_path / "study"
        (study / "data" / inside).parent.mkdir(parents=True)
        (study / "data" / inside).write_text("secret")
        for named in ("data", "."):
            with pytest.raises(ValueError) as caught:
                gather_inputs({"note": named}, study)
            assert refusal_of(caught.value).code == "remote.input.outside"
            assert "credentials" in str(caught.value)

    def test_a_link_to_a_key_kept_in_the_folder_does_not_travel(self, tmp_path):
        from fastmdxplora.remote.inputs import gather_inputs

        study = tmp_path / "study"
        (study / ".ssh").mkdir(parents=True)
        (study / ".ssh" / "id_ed25519").write_text("secret")
        (study / "data").mkdir()
        (study / "data" / "k").symlink_to(study / ".ssh" / "id_ed25519")
        with pytest.raises(ValueError) as caught:
            gather_inputs({"note": "data"}, study)
        assert "credentials" in str(caught.value)

    def test_a_config_in_a_settings_folder_sends_none_of_it(self, tmp_path):
        from fastmdxplora.remote.inputs import gather_inputs

        settings = tmp_path / ".config"
        (settings / "gh").mkdir(parents=True)
        (settings / "gh" / "hosts.yml").write_text("oauth_token: x")
        with pytest.raises(ValueError):
            gather_inputs({"note": "."}, settings)

    def test_fastmdxplora_s_own_settings_do_not_travel(self, tmp_path, monkeypatch):
        from fastmdxplora.remote.inputs import gather_inputs

        study = tmp_path / "study"
        (study / "settings" / "machines").mkdir(parents=True)
        (study / "settings" / "agent.json").write_text('{"key": "x"}')
        monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(study / "settings"))
        with pytest.raises(ValueError) as caught:
            gather_inputs({"note": "settings"}, study)
        assert "credentials" in str(caught.value)

    def test_a_prepared_study_whose_manifest_is_the_home_folder_s_is_refused(
            self, tmp_path, monkeypatch):
        from fastmdxplora.remote.inputs import gather_inputs

        home = tmp_path / "home"
        (home / "proj").mkdir(parents=True)
        (home / "setup").mkdir()
        (home / "manifest.json").write_text('{"phases": []}')
        monkeypatch.setenv("HOME", str(home))
        with pytest.raises(ValueError) as caught:
            gather_inputs({"simulation": {"setup_from": "../setup"}}, home / "proj")
        assert refusal_of(caught.value).code == "remote.input.outside"

    def test_a_key_put_in_a_folder_after_the_check_is_not_sent(self, machine):
        (machine.study.parent / "ff").mkdir()
        (machine.study.parent / "ff" / "a.xml").write_text("<ff/>")
        machine.study.write_text("systems:\n  - system: top.pdb\n"
                                 "setup:\n  forcefield_files: [ff]\n")
        sending = prepare(machine.study, "box", output=str(machine.back), code=RELEASE,
                          transport=machine.transport())
        (machine.study.parent / "ff" / ".aws").mkdir()
        (machine.study.parent / "ff" / ".aws" / "credentials").write_text("secret")
        with pytest.raises(ValueError) as caught:
            send(sending, transport=machine.transport(), local_runner=machine.local,
                 code=RELEASE)
        assert "credentials" in str(caught.value)
        assert not any("mkdir" in c for c in machine.commands)


class TestSeventhReviewRecords:
    def _record(self, machine, **changed):
        import json

        from fastmdxplora.remote.jobs import jobs_dir

        job = _send(machine)
        travels._until_finished(machine, job.name)
        record = json.loads((jobs_dir() / f"{job.name}.json").read_text())
        record.update(changed, name="other")
        (jobs_dir() / "other.json").write_text(json.dumps(record))
        return job

    @pytest.mark.parametrize("changed", [{"local_output": None}, {"local_output": 5},
                                         {"extra": []}, {"code": "x"}])
    def test_a_damaged_record_is_left_out_of_the_jobs(self, machine, changed):
        from fastmdxplora.remote import api

        job = self._record(machine, **changed)
        assert [j.name for j in api.jobs(under=machine.back.parent)] == [job.name]
        assert [j.name for j in api.jobs()] == [job.name]

    @pytest.mark.parametrize("changed", [{"local_output": None}, {"extra": []}])
    def test_a_damaged_record_is_refused_by_name(self, machine, changed):
        from fastmdxplora.remote import api

        self._record(machine, **changed)
        for asked in (api.status, api.cancel, api.fetch_sizes):
            with pytest.raises(ValueError) as caught:
                asked("other", transport=machine.transport())
            assert refusal_of(caught.value).code == "remote.job.unknown"

    def test_a_record_of_a_name_only_is_refused(self, machine):
        from fastmdxplora.remote import api
        from fastmdxplora.remote.jobs import jobs_dir

        jobs_dir().mkdir(parents=True, exist_ok=True)
        (jobs_dir() / "trial.json").write_text('{"name": "trial"}')
        with pytest.raises(ValueError) as caught:
            api.status("trial", transport=machine.transport())
        assert refusal_of(caught.value).code == "remote.job.unknown"

    def test_a_damaged_machine_record_is_left_out(self, machine):
        from fastmdxplora.remote import api
        from fastmdxplora.remote.machines import machines_dir

        (machines_dir() / "broken.json").write_text("[]")
        assert [m.name for m in api.machines(code=RELEASE)] == ["box"]

    @pytest.mark.parametrize("name", ["a/b", "../x", "typo"])
    def test_fetching_a_name_never_sent_is_refused_and_leaves_nothing(self, machine,
                                                                       name):
        from fastmdxplora.remote.jobs import jobs_dir
        from fastmdxplora.remote.send import fetch

        jobs_dir().mkdir(parents=True, exist_ok=True)
        with pytest.raises(ValueError) as caught:
            fetch(name, transport=machine.transport(), local_runner=machine.local)
        assert refusal_of(caught.value).code == "remote.job.unknown"
        assert not list(jobs_dir().glob(".fetching-*"))

    def test_a_job_name_that_cannot_be_used_says_where_it_is_set(self, machine):
        with pytest.raises(ValueError) as caught:
            prepare(machine.study, "box", output=str(machine.back.parent / "my study"),
                    code=RELEASE, transport=machine.transport())
        assert "`output`" in str(caught.value)

    def test_a_damaged_record_leaves_an_ai_app_s_tools_working(self, app):
        import json

        from fastmdxplora.remote.jobs import jobs_dir

        _start(app, answer=YES)
        _finished(app)
        record = json.loads((jobs_dir() / "ghg_run.json").read_text())
        record.update(name="other", local_output=None)
        (jobs_dir() / "other.json").write_text(json.dumps(record))
        for tool, arguments in (("list_machines", {}), ("remote_status", {}),
                                ("remote_status", {"job": "ghg_run"})):
            result = _call(app, tool, **arguments)
            assert not result.get("isError"), _text(result)


class TestSeventhReviewAskedLess:
    def test_a_send_nobody_can_confirm_asks_the_machine_nothing(self, app):
        for _ in range(5):
            first, _ = _start(app, capabilities=None)
            assert first["isError"] and "cannot ask the person" in _text(first)
        assert app.machine.commands == []

    def test_a_cancel_left_unanswered_asks_the_machine_once(self, app):
        app.machine.env["FAKE_SLEEP"] = "30"
        _start(app, answer=YES)
        try:
            before = len(app.machine.commands)
            for _ in range(5):
                first = _call(app, "cancel_study", capabilities=ELICIT, job="ghg_run")
                assert first.get("resultType") == "input_required"
            assert len(app.machine.commands) - before <= 1
        finally:
            _call(app, "cancel_study", job="ghg_run")

    def test_a_fetch_left_unanswered_asks_the_machine_for_sizes_once(self, app):
        _start(app, answer=YES)
        _finished(app)
        before = len(app.machine.commands)
        for _ in range(5):
            first = _call(app, "fetch_study", capabilities=ELICIT, job="ghg_run")
            assert first.get("resultType") == "input_required"
        assert len(app.machine.commands) - before <= 1

    def test_a_job_that_ended_since_it_was_asked_about_is_said_as_ended(self, app):
        import json
        import time

        from fastmdxplora.remote.jobs import jobs_dir

        _start(app, answer=YES)
        _finished(app)
        path = jobs_dir() / "ghg_run.json"
        record = json.loads(path.read_text())
        record["state"] = "running"
        record["extra"]["asked_at"] = time.time()
        path.write_text(json.dumps(record))
        said = _text(_call(app, "cancel_study", job="ghg_run"))
        assert said.startswith("ghg_run had ended already (done)")


class TestSeventhReviewWhatComesBack:
    def test_a_defaults_file_from_the_machine_is_left_out(self, machine):
        from pathlib import Path

        from fastmdxplora.config.defaults_file import find_defaults
        from fastmdxplora.remote.send import fetch

        job = _send(machine)
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / "Fastmdx-Defaults.yml").write_text(
            "simulation:\n  temperature_K: 450\n")
        (Path(job.run_dir) / "trial").mkdir()
        (Path(job.run_dir) / "trial" / "fastmdx-defaults.yml").write_text("x: 1\n")
        _, warnings = fetch(job.name, transport=machine.transport(),
                            local_runner=machine.local, code=RELEASE)
        assert find_defaults(machine.back / "trial" / "again",
                             home=machine.back.parent) is None
        assert any("fastmdx-defaults.yml" in w for w in warnings)

    @pytest.mark.parametrize("handle", ["١٢", "12; rm -rf ~", "-1"])
    def test_a_record_whose_process_is_no_number_is_refused(self, machine, handle):
        import json

        from fastmdxplora.remote import api
        from fastmdxplora.remote.jobs import jobs_dir

        job = _send(machine)
        travels._until_finished(machine, job.name)
        path = jobs_dir() / f"{job.name}.json"
        record = json.loads(path.read_text())
        record.update(handle=handle, state="running")
        path.write_text(json.dumps(record))
        with pytest.raises(ValueError) as caught:
            api.cancel(job.name, transport=machine.transport())
        assert refusal_of(caught.value).code == "remote.job.unknown"


class TestEighthReview:
    @staticmethod
    def _slurm_job(state: str):
        from fastmdxplora.remote.jobs import Job, save_job

        job = Job(name="queued", machine="box", remote_dir="/scratch/queued",
                  scheduler="slurm", handle="4242", submitted_at="2026-10-08T00:00:00Z",
                  code={}, local_output="/tmp/queued", state=state)
        save_job(job)
        return job

    @staticmethod
    def _cluster(answers: list[bytes]):
        import subprocess

        from fastmdxplora.remote.transport import Transport

        asked: list[str] = []

        def runner(command, input=None, **kwargs):
            asked.append(command[-1] if input is None else input.decode())
            said = answers.pop(0) if answers else b""
            return subprocess.CompletedProcess(command, 0, said, b"")

        return Transport("box", runner=runner, interactive=False), asked

    def test_a_queue_that_does_not_answer_leaves_the_job_as_it_was(self, machine):
        self._slurm_job("running")
        link, _ = self._cluster([b""])
        job = status("queued", transport=link)
        assert job.state == "running" and "did not answer" in job.detail

    def test_a_cluster_job_read_as_failed_is_still_cancelled(self, machine):
        self._slurm_job("failed")
        link, asked = self._cluster([b"fmdx:slurm=RUNNING\n"])
        job = cancel("queued", transport=link)
        assert any("scancel" in line for line in asked)
        assert job.state == "abandoned"

    def test_a_settings_folder_above_the_study_does_not_refuse_it(
            self, tmp_path, monkeypatch):
        from fastmdxplora.remote.inputs import gather_inputs

        work = tmp_path / "work"
        study = work / "studies" / "lyso"
        study.mkdir(parents=True)
        (study / "top.pdb").write_text("ATOM\n")
        (work / "machines").mkdir()
        (work / "model.json").write_text('{"api_key": "x"}')
        monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(work))
        assert "top.pdb" in gather_inputs({"system": "top.pdb"}, study).files
        with pytest.raises(ValueError):
            gather_inputs({"note": "../../model.json"}, study)

    def test_words_that_start_with_a_tilde_are_not_a_path(self, tmp_path, monkeypatch):
        from pathlib import Path

        from fastmdxplora.remote.inputs import gather_inputs

        study = tmp_path / "study"
        study.mkdir()

        def no_home(self):
            raise RuntimeError("Could not determine home directory.")

        monkeypatch.setattr(Path, "expanduser", no_home)
        found = gather_inputs({"report": {"title": "~100 ns of ubiquitin"}}, study)
        assert found.files == {}

    def test_sizes_taken_while_a_job_ran_are_asked_again_once_it_ended(self, machine):
        from pathlib import Path

        from fastmdxplora.remote import api

        machine.env["FAKE_SLEEP"] = "1"
        job = _send(machine)
        early = api.fetch_sizes(job.name, max_age_s=STATUS_KEPT_S,
                                transport=machine.transport())
        travels._until_finished(machine, job.name)
        (Path(job.run_dir) / "analysis" / "results.npz").write_bytes(b"x" * 3000)
        later = api.fetch_sizes(job.name, max_age_s=STATUS_KEPT_S,
                                transport=machine.transport())
        assert later.results >= early.results + 3000

    def test_the_api_asks_for_sizes_afresh_by_default(self, machine):
        from pathlib import Path

        from fastmdxplora.remote import api

        job = _send(machine)
        travels._until_finished(machine, job.name)
        first = api.fetch_sizes(job.name, transport=machine.transport())
        (Path(job.run_dir) / "analysis" / "more.npz").write_bytes(b"x" * 5000)
        assert api.fetch_sizes(job.name, transport=machine.transport()).results == (
            first.results + 5000)

    def test_a_made_up_answer_does_not_reach_the_machine(self, app):
        from fastmdxplora.mcp.tools import plan_id_of

        arguments = {"config": "ghg.yml", "plan_id": plan_id_of(app.root / "ghg.yml"),
                     "machine": "box"}
        for state in ("x", "a.b"):
            result = app.request("tools/call", {
                "name": "start_study", "arguments": arguments,
                "inputResponses": {"send": YES}, "requestState": state},
                capabilities={})["result"]
            assert result["isError"] and "cannot ask the person" in _text(result)
        assert app.machine.commands == []

    def test_a_config_changed_after_its_check_is_not_offered(self, app, monkeypatch):
        from fastmdxplora.remote import api

        real = api.plan_send

        def changed_meanwhile(file, *args, **kwargs):
            planned = real(file, *args, **kwargs)
            (app.root / "ghg.yml").write_text(STUDY.replace("5", "6"))
            return planned

        monkeypatch.setattr(api, "plan_send", changed_meanwhile)
        first, _ = _start(app)
        assert first["isError"] and "changed after it was checked" in _text(first)

    @pytest.mark.parametrize("changed", [{"name": "other-job"}, {"handle": "1"},
                                         {"handle": "0123"},
                                         {"extra": {"log_tail": 5}},
                                         {"extra": {"inputs": ["a"]}}])
    def test_a_record_that_is_not_the_job_s_is_refused(self, machine, changed):
        import json

        from fastmdxplora.remote.jobs import jobs_dir, load_job

        job = _send(machine)
        travels._until_finished(machine, job.name)
        path = jobs_dir() / f"{job.name}.json"
        record = json.loads(path.read_text())
        record.update(changed)
        path.write_text(json.dumps(record))
        with pytest.raises(ValueError) as caught:
            load_job(job.name)
        assert refusal_of(caught.value).code == "remote.job.unknown"

    def test_a_machine_record_naming_another_machine_is_refused(self, machine):
        import json

        from fastmdxplora.remote.machines import load_machine, machines_dir

        path = machines_dir() / "box.json"
        record = json.loads(path.read_text())
        record["name"] = "other-host"
        path.write_text(json.dumps(record))
        with pytest.raises(ValueError):
            load_machine("box")


# ---------------------------------------------------------------------------
# The eleventh review's cases (the queue, the case of letters)
# ---------------------------------------------------------------------------
class TestEleventhReview:
    @staticmethod
    def _queued(machine, squeue: str, sacct: str):
        from fastmdxplora.remote.jobs import Job, save_job

        tools = machine.home / "slurm-bin"
        travels._tool(tools / "squeue", squeue)
        travels._tool(tools / "sacct", sacct)
        machine.env["PATH"] = f"{tools}:{machine.env['PATH']}"
        where = machine.home / "fastmdxplora-jobs" / "queued"
        (where / "run").mkdir(parents=True)
        (where / "job.log").write_text(
            "slurmstepd: error: *** JOB 4242 CANCELLED DUE TO TIME LIMIT ***\n")
        save_job(Job(name="queued", machine="box", remote_dir=str(where),
                     scheduler="slurm", handle="4242",
                     submitted_at="2026-10-08T00:00:00Z", code={},
                     local_output=str(machine.back), state="running"))

    NO_ACCOUNTING = 'echo "sacct: error: accounting storage is disabled" >&2; exit 1'

    def test_a_job_the_queue_no_longer_knows_has_ended(self, machine):
        self._queued(machine,
                     'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1',
                     self.NO_ACCOUNTING)
        job = status("queued", transport=machine.transport())
        assert job.state == "failed"
        assert job.detail.startswith("no longer in the cluster's queue")

    def test_a_queue_that_answers_without_the_job_has_let_it_go(self, machine):
        self._queued(machine, "exit 0", self.NO_ACCOUNTING)
        assert status("queued", transport=machine.transport()).state == "failed"

    def test_a_queue_that_times_out_still_says_nothing_of_the_job(self, machine):
        self._queued(machine,
                     'echo "slurm_load_jobs error: Socket timed out on send/recv" >&2; '
                     "exit 1", self.NO_ACCOUNTING)
        job = status("queued", transport=machine.transport())
        assert job.state == "running" and "did not answer" in job.detail

    def test_accounting_still_says_how_a_job_gone_from_the_queue_ended(self, machine):
        self._queued(machine,
                     'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1',
                     'echo "TIMEOUT"')
        job = status("queued", transport=machine.transport())
        assert job.state == "failed" and job.detail == "timeout"

    def test_settings_named_in_another_case_are_kept_where_the_disk_ignores_case(
            self, tmp_path, monkeypatch):
        from fastmdxplora.remote import inputs

        monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "Settings"))
        monkeypatch.setattr(inputs.sys, "platform", "darwin")
        kept = inputs._settings_kept()
        assert inputs._private(tmp_path / "settings" / "Model.json", kept)
        assert inputs._private(tmp_path / "SETTINGS" / "machines" / "x.json", kept)
        assert not inputs._private(tmp_path / "settings" / "study.yml", kept)
        monkeypatch.setattr(inputs.sys, "platform", "linux")
        assert not inputs._private(tmp_path / "settings" / "Model.json", kept)


# ---------------------------------------------------------------------------
# The twelfth review's cases (the queue, a cancel, a cluster job sent again)
# ---------------------------------------------------------------------------
NO_ACCOUNTING_H = 'echo "sacct: error: accounting storage is disabled" >&2; exit 1'
QUEUE_WARNING = 'echo "squeue: error: _parse_next_key: Parsing error at unrecognized key: X" >&2'


def _queued_h(machine, squeue: str, sacct: str, state: str = "running",
            exit_code: str | None = None, scancel: str = "exit 0",
            local_output: str | None = None):
    from fastmdxplora.remote.jobs import Job, save_job

    tools = machine.home / "slurm-bin"
    travels._tool(tools / "squeue", squeue)
    travels._tool(tools / "sacct", sacct)
    travels._tool(tools / "scancel", scancel)
    machine.env["PATH"] = f"{tools}:{machine.env['PATH']}"
    where = machine.home / "fastmdxplora-jobs" / "queued"
    (where / "run").mkdir(parents=True)
    (where / "job.log").write_text(
        "slurmstepd: error: *** JOB 4242 CANCELLED DUE TO TIME LIMIT ***\n")
    if exit_code is not None:
        (where / "exit_code").write_text(exit_code + "\n")
    save_job(Job(name="queued", machine="box", remote_dir=str(where),
                 scheduler="slurm", handle="4242",
                 submitted_at="2026-10-08T00:00:00Z", code={},
                 local_output=local_output or str(machine.back), state=state))


# ---------------------------------------------------------------------------
# H1: squeue's stderr is read as the job's state (2>&1 in _status_script)
# ---------------------------------------------------------------------------
class TestTwelfthReviewSqueueWarnings:
    def test_a_job_gone_from_a_queue_that_warns_is_not_left_running(self, machine):
        # squeue answers (exit 0) with no job, after a warning on stderr.
        _queued_h(machine, f"{QUEUE_WARNING}; exit 0", NO_ACCOUNTING_H)
        job = status("queued", transport=machine.transport())
        assert job.state == "failed", (job.state, job.detail)

    def test_accounting_is_still_asked_when_the_queue_warns(self, machine):
        _queued_h(machine, f"{QUEUE_WARNING}; exit 0", 'echo "TIMEOUT"')
        job = status("queued", transport=machine.transport())
        assert (job.state, job.detail) == ("failed", "timeout"), (job.state, job.detail)

    def test_a_job_that_started_is_read_as_running_when_the_queue_warns(self, machine):
        _queued_h(machine, f'{QUEUE_WARNING}; echo RUNNING; exit 0', NO_ACCOUNTING_H,
                state="ready")
        job = status("queued", transport=machine.transport())
        assert job.state == "running", (job.state, job.detail)
        assert "did not answer" not in job.detail


# ---------------------------------------------------------------------------
# H2: cancelling a cluster job that failed rewrites its failure as "abandoned"
# ---------------------------------------------------------------------------
class TestTwelfthReviewCancelOfAFailedClusterJob:
    def test_a_failed_cluster_job_keeps_its_exit_code(self, machine):
        _queued_h(machine,
                'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1',
                NO_ACCOUNTING_H, state="failed", exit_code="3")
        assert status("queued", transport=machine.transport()).detail == "exit code 3"
        job = cancel("queued", transport=machine.transport())
        assert (job.state, job.detail) == ("failed", "exit code 3"), (job.state, job.detail)
        assert not any("scancel" in c for c in machine.commands)

    def test_a_cluster_job_killed_by_its_time_limit_keeps_saying_so(self, machine):
        _queued_h(machine,
                'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1',
                'echo "TIMEOUT"', state="running")
        assert status("queued", transport=machine.transport()).detail == "timeout"
        job = cancel("queued", transport=machine.transport())
        assert job.state == "failed", (job.state, job.detail)

    def test_an_ai_app_does_not_offer_to_stop_a_cluster_job_that_failed(self, app):
        _queued_h(app.machine,
                'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1',
                NO_ACCOUNTING_H, state="failed", exit_code="3",
                local_output=str(app.root / "queued"))
        assert status("queued", transport=app.machine.transport()).detail == "exit code 3"
        first = _call(app, "cancel_study", capabilities=ELICIT, job="queued")
        if first.get("resultType") == "input_required":
            asked = first["inputRequests"]["cancel"]["params"]["message"]
            done = app.request("tools/call", {
                "name": "cancel_study", "arguments": {"job": "queued"},
                "inputResponses": {"cancel": YES},
                "requestState": first["requestState"]},
                capabilities=ELICIT)["result"]
            pytest.fail(f"asked {asked!r}; then said {_text(done)!r}")
        assert "ended already (failed)" in _text(first)


# ---------------------------------------------------------------------------
# H3: a cluster job sent again with --force-overwrite reads the last run's
# exit code while it waits in the queue
# ---------------------------------------------------------------------------
class TestTwelfthReviewResentClusterJob:
    def test_a_queued_job_sent_again_is_not_read_from_the_last_run(self, machine):
        from fastmdxplora.remote.send import job_script

        tools = machine.home / "slurm-bin"
        machine.env["FAKE_EXIT"] = "1"
        # The first run: sbatch runs it there and then.
        travels._tool(tools / "sbatch", "sh job.sh > job.log 2>&1; echo 4242")
        travels._tool(tools / "squeue",
                      'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1')
        travels._tool(tools / "sacct", NO_ACCOUNTING_H)

        def slurm_sending():
            sending = prepare(machine.study, "box", output=str(machine.back), force=True,
                              code=RELEASE, transport=machine.transport())
            sending.scheduler = "slurm"
            sending.script = job_script(remote_dir=sending.remote_dir,
                                        job_name=sending.job_name,
                                        env=sending.installation, container="",
                                        scheduler="slurm", force=True)
            return sending

        first = slurm_sending()
        machine.env["PATH"] = f"{tools}:{machine.env['PATH']}"
        job = send(first, transport=machine.transport(), local_runner=machine.local,
                   code=RELEASE)
        assert status(job.name, transport=machine.transport()).state == "failed"

        # Sent again: this time it waits in the queue.
        travels._tool(tools / "sbatch", "echo 4243")
        # The queue knows the new job only; the last one has gone from it.
        travels._tool(tools / "squeue",
                      '[ "$3" = 4243 ] && echo PENDING && exit 0; '
                      'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1')
        machine.env["PATH"] = machine.env["PATH"].split(":", 1)[1]
        again = slurm_sending()
        machine.env["PATH"] = f"{tools}:{machine.env['PATH']}"
        job = send(again, transport=machine.transport(), local_runner=machine.local,
                   code=RELEASE)
        assert job.handle == "4243"
        job = status(job.name, transport=machine.transport())
        assert job.state == "ready", (job.state, job.detail)


# ---------------------------------------------------------------------------
# H4: one damaged job record stops `fastmdx remote status` and `fastmdx remote`
# ---------------------------------------------------------------------------
class TestTwelfthReviewCliWithADamagedRecord:
    @pytest.mark.parametrize("argv", [["remote", "status"], ["remote"]])
    def test_other_jobs_are_still_listed(self, machine, argv, capsys):
        import json

        from fastmdxplora.cli.main import main
        from fastmdxplora.remote.jobs import jobs_dir

        job = travels._send(machine)
        travels._until_finished(machine, job.name)
        record = json.loads((jobs_dir() / f"{job.name}.json").read_text())
        record.update(name="other", handle="0123")
        (jobs_dir() / "other.json").write_text(json.dumps(record))
        try:
            code = main(argv)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{argv} raised {type(exc).__name__}: {exc}")
        said = capsys.readouterr().out
        assert code == 0 and "! The record of 'other'" in said, said


class TestTwelfthReviewWhatTheMachineSays:
    def test_a_handle_of_thousands_of_digits_is_unusable_not_an_error(self):
        from fastmdxplora.remote.jobs import usable_handle

        assert usable_handle("1" * 5000) is False

    def test_a_state_given_out_for_another_tool_does_not_reach_the_machine(self, app):
        from fastmdxplora.mcp.tools import plan_id_of

        state = app.server.state_for(
            "tools/call", "ask_agent:0123", {"replies": [], "asked": "x",
                                             "key": "fastmdx-sample-0", "kept": {}})
        arguments = {"config": "ghg.yml", "plan_id": plan_id_of(app.root / "ghg.yml"),
                     "machine": "box"}
        for _ in range(3):
            result = app.request("tools/call", {
                "name": "start_study", "arguments": arguments,
                "inputResponses": {"fastmdx-sample-0": {"content": {
                    "type": "text", "text": "hi"}}},
                "requestState": state}, capabilities={"sampling": {}})["result"]
            assert "Sent to" not in _text(result)
        assert app.machine.commands == []


# ---------------------------------------------------------------------------
# The thirteenth review's cases (a cancel, requeues, held jobs, records)
# ---------------------------------------------------------------------------
SCANCEL_TIMES_OUT = ('echo "scancel: error: Kill job error on job id 4242: '
                     'Socket timed out on send/recv operation" >&2; exit 1')


class TestThirteenthReviewACancelNotTaken:
    def test_a_cancel_the_cluster_did_not_take_is_refused_and_asked_again(
            self, machine):
        from fastmdxplora.remote.jobs import load_job

        _queued_h(machine, "echo RUNNING", NO_ACCOUNTING_H, scancel=SCANCEL_TIMES_OUT)
        with pytest.raises(ValueError) as caught:
            cancel("queued", transport=machine.transport())
        assert "did not take the cancel" in str(caught.value)
        assert load_job("queued").state == "running"
        before = len(machine.commands)
        assert status("queued", transport=machine.transport()).state == "running"
        assert len(machine.commands) == before + 1

    def test_one_sent_while_the_queue_is_silent_is_not_taken_as_done(self, machine):
        from fastmdxplora.remote.jobs import load_job

        _queued_h(machine,
                  'echo "slurm_load_jobs error: Socket timed out on send/recv" >&2; '
                  "exit 1", NO_ACCOUNTING_H, state="failed", scancel=SCANCEL_TIMES_OUT)
        with pytest.raises(ValueError):
            cancel("queued", transport=machine.transport())
        assert load_job("queued").state != "abandoned"

    def test_an_ai_app_is_not_told_it_stopped(self, app):
        _queued_h(app.machine, "echo RUNNING", NO_ACCOUNTING_H, scancel=SCANCEL_TIMES_OUT,
                  local_output=str(app.root / "queued"))
        first = _call(app, "cancel_study", capabilities=ELICIT, job="queued")
        done = app.request("tools/call", {
            "name": "cancel_study", "arguments": {"job": "queued"},
            "inputResponses": {"cancel": YES}, "requestState": first["requestState"]},
            capabilities=ELICIT)["result"]
        assert done.get("isError") and "did not take the cancel" in _text(done)


class TestThirteenthReviewTheQueue:
    def test_a_job_waiting_again_is_not_read_from_its_last_exit_code(self, machine):
        _queued_h(machine, "echo PENDING", NO_ACCOUNTING_H, state="running",
                  exit_code="1")
        job = status("queued", transport=machine.transport())
        assert (job.state, job.detail) == ("ready", "pending")

    def test_a_waiting_job_is_not_shown_with_an_earlier_run_s_progress(self, machine):
        import json

        _queued_h(machine, "echo PENDING", NO_ACCOUNTING_H, state="ready")
        live = machine.home / "fastmdxplora-jobs" / "queued" / "run" / "simulation"
        live.mkdir(parents=True)
        (live / "live_status.json").write_text(json.dumps(
            {"stage": "production", "current_step": 1000, "total_planned_steps": 1000}))
        assert status("queued", transport=machine.transport()).detail == "pending"

    def test_a_suspended_job_says_so(self, machine):
        import json

        _queued_h(machine, "echo SUSPENDED", NO_ACCOUNTING_H)
        live = machine.home / "fastmdxplora-jobs" / "queued" / "run" / "simulation"
        live.mkdir(parents=True)
        (live / "live_status.json").write_text(json.dumps(
            {"stage": "production", "current_step": 400, "total_planned_steps": 1000}))
        assert status("queued", transport=machine.transport()).detail == "suspended"

    @pytest.mark.parametrize("held", ["REQUEUE_HOLD", "SPECIAL_EXIT", "RESV_DEL_HOLD"])
    def test_a_held_job_reads_as_waiting(self, machine, held):
        _queued_h(machine, f"echo {held}", NO_ACCOUNTING_H, state="ready")
        assert status("queued", transport=machine.transport()).state == "ready"

    def test_accounting_left_behind_does_not_keep_a_job_running(self, machine):
        _queued_h(machine,
                  'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1',
                  'echo "   RUNNING "')
        job = status("queued", transport=machine.transport())
        assert job.state == "failed" and "accounting still says running" in job.detail

    def test_a_cluster_job_asks_not_to_be_requeued(self):
        from fastmdxplora.remote.probe import Environment
        from fastmdxplora.remote.send import job_script

        script = job_script(remote_dir="/scratch/me/fastmdxplora-jobs/x", job_name="x",
                            env=Environment(path="/opt/envs/fmdx", version="1.0"),
                            container="", scheduler="slurm", force=False)
        assert "#SBATCH --no-requeue" in script
        assert "\nrm -f exit_code\n" in script     # a job started again by hand

    def test_a_job_sent_again_that_the_cluster_refuses_leaves_the_last_as_it_ended(
            self, machine):
        from fastmdxplora.remote.send import job_script, prepare, send

        tools = machine.home / "slurm-bin"
        travels._tool(tools / "sbatch", "sh job.sh > job.log 2>&1; echo 4242")
        travels._tool(tools / "squeue",
                      'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1')
        travels._tool(tools / "sacct", NO_ACCOUNTING_H)

        def slurm_sending():
            sending = prepare(machine.study, "box", output=str(machine.back), force=True,
                              code=RELEASE, transport=machine.transport())
            sending.scheduler = "slurm"
            sending.script = job_script(remote_dir=sending.remote_dir,
                                        job_name=sending.job_name,
                                        env=sending.installation, container="",
                                        scheduler="slurm", force=True)
            return sending

        first = slurm_sending()
        machine.env["PATH"] = f"{tools}:{machine.env['PATH']}"
        job = send(first, transport=machine.transport(), local_runner=machine.local,
                   code=RELEASE)
        assert status(job.name, transport=machine.transport()).state == "done"
        travels._tool(tools / "sbatch",
                      'echo "sbatch: error: Batch job submission failed: Invalid '
                      'partition name specified" >&2; exit 1')
        machine.env["PATH"] = machine.env["PATH"].split(":", 1)[1]
        again = slurm_sending()
        machine.env["PATH"] = f"{tools}:{machine.env['PATH']}"
        with pytest.raises(ValueError):
            send(again, transport=machine.transport(), local_runner=machine.local,
                 code=RELEASE)
        assert status(job.name, transport=machine.transport()).state == "done"


class TestThirteenthReviewRecords:
    @staticmethod
    def _broken():
        import json

        from fastmdxplora.remote.machines import machines_dir

        (machines_dir() / "broken.json").write_text(json.dumps(
            {"name": "broken", "inspected_at": "", "inspection": {"images": 5}}))

    def test_a_machine_record_of_the_wrong_kinds_is_refused_by_name(self, machine):
        from fastmdxplora.remote import api
        from fastmdxplora.remote.machines import load_machine

        self._broken()
        with pytest.raises(ValueError) as caught:
            load_machine("broken")
        assert "broken" in str(caught.value)
        assert [m.name for m in api.machines(code=RELEASE)] == ["box"]

    def test_the_command_line_lists_the_rest_and_says_which(self, machine, capsys):
        from fastmdxplora.cli.main import main

        self._broken()
        assert main(["remote"]) == 0
        said = capsys.readouterr().out
        assert "box" in said and "! The record for 'broken'" in said

    def test_an_ai_app_still_lists_machines(self, app):
        self._broken()
        assert not _call(app, "list_machines").get("isError")

    def test_a_state_never_used_up_does_not_reach_the_machine_each_call(self, app):
        from fastmdxplora.mcp.tools import plan_id_of

        arguments = {"config": "ghg.yml", "plan_id": plan_id_of(app.root / "ghg.yml"),
                     "machine": "box"}
        first = app.request("tools/call", {"name": "start_study",
                                           "arguments": arguments},
                            capabilities=ELICIT)["result"]
        before = len(app.machine.commands)
        for _ in range(3):
            app.request("tools/call", {
                "name": "start_study", "arguments": arguments, "inputResponses": {},
                "requestState": first["requestState"]}, capabilities={})
        assert app.machine.commands[before:] == []


# ---------------------------------------------------------------------------
# The fourteenth review's cases (accounting apart from the queue, records)
# ---------------------------------------------------------------------------
GONE_K = 'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1'
SCANCEL_GONE_K = ('echo "scancel: error: Kill job error on job id 4242: '
                'Invalid job id specified" >&2; exit 1')


class TestFourteenthReviewKRunawayPendingAccounting:
    """The queue says it does not know the job; accounting left behind (a
    runaway record) still says PENDING; the job wrote exit code 0."""

    def test_status_is_not_waiting_forever(self, machine):
        _queued_h(machine, GONE_K, 'echo "   PENDING "', state="ready", exit_code="0")
        job = status("queued", transport=machine.transport())
        assert job.state != "ready", (job.state, job.detail)

    def test_it_can_be_cancelled_or_is_ended(self, machine):
        _queued_h(machine, GONE_K, 'echo "   PENDING "', state="ready",
                  scancel=SCANCEL_GONE_K)
        try:
            job = cancel("queued", transport=machine.transport())
        except ValueError as exc:
            pytest.fail(f"cancel refused: {exc}")
        assert job.state != "ready"

    def test_its_results_can_be_fetched(self, machine):
        from fastmdxplora.refusals import refusal_of
        from fastmdxplora.remote.send import fetch

        _queued_h(machine, GONE_K, 'echo "   PENDING "', state="ready", exit_code="0")
        try:
            fetch("queued", transport=machine.transport(), local_runner=machine.local)
        except ValueError as exc:
            assert refusal_of(exc).code != "remote.job.unfinished", str(exc)


class TestFourteenthReviewKTheQueueReadThroughAFileInTheJobFolder:
    """The queue's complaints are read with its answer, through no file in
    the job's folder: a folder of that name there changes nothing."""

    def test_a_job_gone_from_the_queue_is_not_left_running(self, machine):
        _queued_h(machine, GONE_K, NO_ACCOUNTING_H, state="running")
        (machine.home / "fastmdxplora-jobs" / "queued" / ".fmdx-queue").mkdir()
        job = status("queued", transport=machine.transport())
        assert job.state == "failed", (job.state, job.detail)


class TestFourteenthReviewKAMachineRecordOfTheWrongKinds:
    """load_machine checks a record by reading it for code "0", which holds
    no installation, so the fields read only for an installation that holds
    this computer's code (its `info`), and the GPUs the listings count, are
    never looked at: such a record passes, and stops the listings."""

    @staticmethod
    def _write(record):
        import json

        from fastmdxplora.remote.machines import machines_dir

        (machines_dir() / "broken.json").write_text(json.dumps(record))

    @pytest.mark.parametrize("kind", ["info", "gpus"])
    def test_the_listings_go_on(self, machine, kind, capsys):
        from fastmdxplora.remote import api
        from tests.test_a_study_travels_and_comes_back import RELEASE

        record = {"name": "broken", "inspected_at": "",
                  "inspection": {"environments": [
                      {"path": "/opt/fmdx", "version": RELEASE.version}]},
                  "info": {}}
        if kind == "info":
            record["info"] = {"/opt/fmdx": "not a mapping"}
        else:
            record["inspection"]["gpus"] = [
                {"name": ["A100"], "memory_mb": 1, "driver": "x"}]
        self._write(record)
        try:
            names = [m.name for m in api.machines(code=RELEASE)]
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"api.machines raised {type(exc).__name__}: {exc}")
        assert names == ["box"]

    def test_an_ai_app_still_lists_machines(self, app):
        from tests.test_a_study_travels_and_comes_back import RELEASE
        from tests.test_remote_from_every_interface import _call, _text

        self._write({"name": "broken", "inspected_at": "",
                     "inspection": {"environments": [
                         {"path": "/opt/fmdx", "version": RELEASE.version}]},
                     "info": {"/opt/fmdx": "not a mapping"}})
        result = _call(app, "list_machines")
        assert not result.get("isError"), _text(result)


GONE_L = 'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1'
NO_ACCOUNTING_L = 'echo "sacct: error: accounting storage is disabled" >&2; exit 1'
QUEUE_TIMES_OUT_L = 'echo "slurm_load_jobs error: Socket timed out on send/recv" >&2; exit 1'


# ---------------------------------------------------------------------------
# M1: accounting left behind that says PENDING is believed, though the queue
# no longer knows the job, and it outranks the job's own exit code.
# ---------------------------------------------------------------------------
class TestFourteenthReviewLStalePendingAccounting:
    def test_a_job_that_ended_with_0_is_not_read_as_waiting(self, machine):
        # Gone from the queue (MinJobAge), the run wrote exit_code 0, and
        # the accounting record left behind still says PENDING.
        _queued_h(machine, GONE_L, 'echo "   PENDING "', state="running", exit_code="0")
        job = status("queued", transport=machine.transport())
        assert job.state == "done", (job.state, job.detail)

    def test_a_job_gone_with_pending_accounting_is_not_left_waiting(self, machine):
        # As RUNNING left behind is not believed (TestThirteenthReviewTheQueue),
        # neither is PENDING: the job is no longer anywhere to wait.
        _queued_h(machine, GONE_L, 'echo "   PENDING "', state="ready")
        job = status("queued", transport=machine.transport())
        assert job.state == "failed", (job.state, job.detail)


# ---------------------------------------------------------------------------
# L1: a damaged machine record that holds this computer's code passes
# load_machine (checked against CodeIdentity("0") only) and then breaks the
# listings that read it against the real code.
# ---------------------------------------------------------------------------
class TestFourteenthReviewLDamagedMachineRecordHoldingThisCode:
    def _damaged(self, machine):
        from fastmdxplora.remote.machines import machines_dir

        env = machine.home / ".conda" / "envs" / "fastmdx-1.0"
        path = machines_dir() / "box.json"
        record = json.loads(path.read_text())
        record["inspection"]["environments"] = [{"path": str(env), "version": "1.0"}]
        record["info"] = {str(env): "not a mapping"}
        path.write_text(json.dumps(record))

    def test_the_api_listing_skips_it(self, machine):
        from fastmdxplora.remote import api

        self._damaged(machine)
        try:
            found = api.machines(code=RELEASE)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"api.machines raised {type(exc).__name__}: {exc}")
        assert [m.name for m in found] == []

    def test_load_machine_refuses_it_by_name(self, machine):
        from fastmdxplora.remote.machines import load_machine, readiness

        self._damaged(machine)
        with pytest.raises(ValueError) as caught:
            readiness(load_machine("box"), RELEASE)
        assert "The record for 'box'" in str(caught.value)


# ---------------------------------------------------------------------------
# L2: while the queue is silent, the run's live record overwrites the detail
# that says so: a job whose node died reads as running, with progress.
# ---------------------------------------------------------------------------
class TestFourteenthReviewLSilentQueueDetail:
    def test_a_silent_queue_is_still_said(self, machine):
        _queued_h(machine, QUEUE_TIMES_OUT_L, NO_ACCOUNTING_L, state="running")
        live = machine.home / "fastmdxplora-jobs" / "queued" / "run" / "simulation"
        live.mkdir(parents=True)
        (live / "live_status.json").write_text(json.dumps(
            {"stage": "production", "current_step": 400, "total_planned_steps": 1000}))
        job = status("queued", transport=machine.transport())
        assert job.state == "running"
        assert "did not answer" in job.detail, job.detail


# ---------------------------------------------------------------------------
# L3: Slurm's scancel exits 0 for a job it no longer knows (SIGKILL, the
# default, ignores ESLURM_INVALID_JOB_ID / ESLURM_ALREADY_DONE). A job read
# as failed with a time limit, cancelled while the queue is silent, is
# rewritten as abandoned: how it ended is lost.
# ---------------------------------------------------------------------------
class TestFourteenthReviewLCancelOfAnEndedJobWhileTheQueueIsSilent:
    def test_how_it_ended_is_kept(self, machine):
        from fastmdxplora.remote.jobs import load_job

        _queued_h(machine, GONE_L, 'echo "TIMEOUT"', state="running")
        assert status("queued", transport=machine.transport()).detail == "timeout"
        # Now the controller is busy for squeue and sacct; scancel, a moment
        # later, reaches it and is told the job is gone, which it ignores.
        travels._tool(machine.home / "slurm-bin" / "squeue", QUEUE_TIMES_OUT_L)
        travels._tool(machine.home / "slurm-bin" / "sacct", NO_ACCOUNTING_L)
        job = cancel("queued", transport=machine.transport())
        assert (job.state, job.detail) == ("failed", "timeout")
        assert load_job("queued").state == "failed"


# ---------------------------------------------------------------------------
# L4: a cancel the cluster did not take is refused as
# environment.service.machine_unreachable, which refusals.md defines as "ssh
# could not reach a machine", and the AI app adds sign-in advice to it,
# though ssh reached the machine and scancel answered.
# ---------------------------------------------------------------------------
SCANCEL_TIMES_OUT_L = ('echo "scancel: error: Kill job error on job id 4242: '
                     'Socket timed out on send/recv operation" >&2; exit 1')


class TestFourteenthReviewLACancelNotTakenIsNotAnSshFailure:
    def test_the_refusal_is_not_said_as_an_unreachable_machine(self, machine):
        from fastmdxplora.refusals import refusal_of

        _queued_h(machine, "echo RUNNING", NO_ACCOUNTING_L, scancel=SCANCEL_TIMES_OUT_L)
        with pytest.raises(ValueError) as caught:
            cancel("queued", transport=machine.transport())
        assert refusal_of(caught.value).code != "environment.service.machine_unreachable"

    def test_an_ai_app_is_not_told_to_sign_in(self, app):
        from tests.test_remote_from_every_interface import ELICIT, YES, _call, _text

        _queued_h(app.machine, "echo RUNNING", NO_ACCOUNTING_L, scancel=SCANCEL_TIMES_OUT_L,
                  local_output=str(app.root / "queued"))
        first = _call(app, "cancel_study", capabilities=ELICIT, job="queued")
        done = app.request("tools/call", {
            "name": "cancel_study", "arguments": {"job": "queued"},
            "inputResponses": {"cancel": YES}, "requestState": first["requestState"]},
            capabilities=ELICIT)["result"]
        assert done.get("isError")
        assert "second factor" not in _text(done), _text(done)


# ---------------------------------------------------------------------------
# Coverage gap (not a defect today): no test fails when the send stops
# setting the last exit code aside once sbatch takes the new job. With the
# queue silent while the new job waits, the last run's exit code is read.
# ---------------------------------------------------------------------------
class TestFourteenthReviewLResentJobWhileTheQueueIsSilent:
    def test_the_last_run_s_exit_code_is_not_read(self, machine):
        from fastmdxplora.remote.send import job_script, prepare, send

        tools = machine.home / "slurm-bin"
        machine.env["FAKE_EXIT"] = "1"
        travels._tool(tools / "sbatch", "sh job.sh > job.log 2>&1; echo 4242")
        travels._tool(tools / "squeue", GONE_L)
        travels._tool(tools / "sacct", NO_ACCOUNTING_L)

        def slurm_sending():
            sending = prepare(machine.study, "box", output=str(machine.back), force=True,
                              code=RELEASE, transport=machine.transport())
            sending.scheduler = "slurm"
            sending.script = job_script(remote_dir=sending.remote_dir,
                                        job_name=sending.job_name,
                                        env=sending.installation, container="",
                                        scheduler="slurm", force=True)
            return sending

        first = slurm_sending()
        machine.env["PATH"] = f"{tools}:{machine.env['PATH']}"
        job = send(first, transport=machine.transport(), local_runner=machine.local,
                   code=RELEASE)
        assert status(job.name, transport=machine.transport()).state == "failed"
        travels._tool(tools / "sbatch", "echo 4243")
        machine.env["PATH"] = machine.env["PATH"].split(":", 1)[1]
        again = slurm_sending()
        machine.env["PATH"] = f"{tools}:{machine.env['PATH']}"
        job = send(again, transport=machine.transport(), local_runner=machine.local,
                   code=RELEASE)
        travels._tool(tools / "squeue", QUEUE_TIMES_OUT_L)
        job = status(job.name, transport=machine.transport())
        assert job.state == "ready", (job.state, job.detail)


class TestFourteenthReviewSilenceIsForgotten:
    def test_a_queue_silent_once_does_not_let_a_cancel_rewrite_how_a_job_ended(
            self, machine):
        from pathlib import Path

        _queued_h(machine,
                  'echo "slurm_load_jobs error: Socket timed out on send/recv" >&2; '
                  "exit 1", NO_ACCOUNTING_H)
        assert status("queued", transport=machine.transport()).extra["queue_silent"]
        where = Path(machine.home / "fastmdxplora-jobs" / "queued")
        (where / "exit_code").write_text("3\n")
        travels._tool(machine.home / "slurm-bin" / "squeue",
                      'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1')
        job = status("queued", transport=machine.transport())
        assert (job.state, job.detail) == ("failed", "exit code 3")
        job = cancel("queued", transport=machine.transport())
        assert (job.state, job.detail) == ("failed", "exit code 3")
        assert not any("scancel" in command for command in machine.commands)

    def test_the_command_line_says_nothing_was_stopped(self, machine, capsys,
                                                      monkeypatch):
        from fastmdxplora.cli.main import main
        from fastmdxplora.remote import send as sending

        monkeypatch.setattr(sending, "Transport", lambda *a, **k: machine.transport())

        _queued_h(machine,
                  'echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1',
                  'echo "TIMEOUT"')
        assert main(["remote", "cancel", "queued"]) == 0
        assert "had ended already (failed, timeout); nothing was stopped" in (
            capsys.readouterr().out)


# ---------------------------------------------------------------------------
# The fifteenth review's cases (a cancel or fetch while the queue is silent)
# ---------------------------------------------------------------------------
QUEUE_SILENT = 'echo "slurm_load_jobs error: Socket timed out on send/recv" >&2; exit 1'


def _scancel_sent(machine) -> bool:
    return any(c.startswith("scancel") for c in machine.commands)


class TestFifteenthReviewWhileTheQueueIsSilent:
    def test_the_command_line_stops_nothing_and_says_why(self, machine, capsys,
                                                        monkeypatch):
        from fastmdxplora.cli.main import main
        from fastmdxplora.remote import send as sending

        monkeypatch.setattr(sending, "Transport", lambda *a, **k: machine.transport())
        # A record read as failed (as v2.5.8 read a silent queue), the job
        # possibly still going, the queue silent still.
        _queued_h(machine, QUEUE_SILENT, NO_ACCOUNTING_H, state="failed")
        assert main(["remote", "cancel", "queued"]) == 1
        said = capsys.readouterr()
        assert not _scancel_sent(machine)
        assert "whether queued is still going is not known" in said.out + said.err
        assert "Nothing was stopped" in said.out + said.err

    def test_an_ai_app_is_told_so_without_asking_the_person(self, app):
        _queued_h(app.machine, QUEUE_SILENT, NO_ACCOUNTING_H, state="failed",
                  local_output=str(app.root / "queued"))
        first = _call(app, "cancel_study", capabilities=ELICIT, job="queued")
        assert first.get("resultType") != "input_required"
        assert "Nothing was stopped" in _text(first) and not _scancel_sent(app.machine)

    def test_an_ai_app_cancelling_in_a_loop_sends_nothing(self, app):
        _queued_h(app.machine, QUEUE_SILENT, NO_ACCOUNTING_H, state="failed",
                  local_output=str(app.root / "queued"))
        for _ in range(4):
            _call(app, "cancel_study", job="queued")
        assert not _scancel_sent(app.machine)

    def test_the_api_cancelling_an_ended_job_in_a_loop_asks_at_most_every_30_s(
            self, machine):
        from fastmdxplora.remote import api

        _queued_h(machine, 'echo "slurm_load_jobs error: Invalid job id specified" >&2; '
                  "exit 1", NO_ACCOUNTING_H, state="failed", exit_code="3")
        status("queued", transport=machine.transport())
        before = len(machine.commands)
        for _ in range(4):
            assert api.cancel("queued", transport=machine.transport()).detail == \
                "exit code 3"
        assert len(machine.commands) - before == 0

    def test_a_job_that_may_still_be_going_is_not_fetched(self, machine):
        from fastmdxplora.remote.send import fetch

        _queued_h(machine, QUEUE_SILENT, NO_ACCOUNTING_H, state="failed")
        with pytest.raises(ValueError) as caught:
            fetch("queued", transport=machine.transport(), local_runner=machine.local)
        assert refusal_of(caught.value).code == "remote.job.unfinished"

    def test_a_silent_queue_is_asked_once(self, machine):
        log = machine.home / "squeue-calls"
        _queued_h(machine, f'echo x >> "{log}"; {QUEUE_SILENT}', NO_ACCOUNTING_H)
        job = status("queued", transport=machine.transport())
        assert job.state == "running" and "did not answer" in job.detail
        assert len(log.read_text().splitlines()) == 1


class TestFifteenthReviewAHeldJob:
    def test_a_held_job_says_it_is_held(self, machine):
        squeue = ('case "$*" in *%r*) echo "PENDING JobHeldUser";; '
                  '*) echo PENDING;; esac; exit 0')
        _queued_h(machine, squeue, NO_ACCOUNTING_H, state="ready")
        job = status("queued", transport=machine.transport())
        assert (job.state, job.detail) == (
            "ready", "pending, held (JobHeldUser): it starts only once released")

    def test_a_waiting_job_says_why(self, machine):
        _queued_h(machine, 'echo "PENDING Priority"', NO_ACCOUNTING_H, state="ready")
        assert status("queued", transport=machine.transport()).detail == \
            "pending (Priority)"


class TestFifteenthReviewTheSendGate:
    def test_a_local_start_s_state_does_not_reach_the_machine(self, app):
        from fastmdxplora.mcp.tools import plan_id_of

        file = app.root / "ghg.yml"
        state = app.server.state_for(
            "tools/call", f"start_study:{file}:{plan_id_of(file)}:{app.root / 'other'}")
        arguments = {"config": "ghg.yml", "plan_id": plan_id_of(file), "machine": "box"}
        for _ in range(3):
            result = app.request("tools/call", {
                "name": "start_study", "arguments": arguments,
                "inputResponses": {"send": YES}, "requestState": state},
                capabilities={})["result"]
            assert "Sent to" not in _text(result)
        assert app.machine.commands == []

# The ninth review's cases (shared GPUs)
# ---------------------------------------------------------------------------
def _plan(machine, tmp_path, name="second", **kwargs):
    return prepare(machine.study, "box", output=str(tmp_path / "back" / name),
                   code=RELEASE, transport=machine.transport(), **kwargs)


def _go(machine, sending):
    return send(sending, transport=machine.transport(), local_runner=machine.local,
                code=RELEASE)


def _systems(machine, *names, more=""):
    for name in names[1:]:
        (machine.study.parent / name).write_text("ATOM\n")
    machine.study.write_text(
        "systems:\n" + "".join(f"  - system: {name}\n" for name in names)
        + "include_phase: [analysis]\nanalysis:\n  topology: top.pdb\n" + more)


def _measured(machine, mb=6000):
    from fastmdxplora.remote.gpu_room import learn

    learn("box", job="before", particles=2000, peak_mb=mb, gpu="Stand-in GPU")


class TestNinthReviewWhichGpu:
    def test_sequential_devices_keep_their_gpu(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 20000))
        machine.study.write_text(machine.study.read_text()
                                 + "execution:\n  mode: sequential\n  devices: [1]\n")
        sending = _plan(machine, tmp_path)
        assert "CUDA_VISIBLE_DEVICES" not in sending.script
        assert sending.gpu.uuids == (UUID_1,)

    def test_a_system_s_own_device_index_keeps_its_gpu(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 20000))
        machine.study.write_text(
            "systems:\n  - system: top.pdb\n    simulation:\n      device_index: '1'\n"
            "include_phase: [analysis]\nanalysis:\n  topology: top.pdb\n")
        sending = _plan(machine, tmp_path)
        assert "CUDA_VISIBLE_DEVICES" not in sending.script
        assert sending.gpu.uuids == (UUID_1,)

    def test_a_sweep_over_the_device_keeps_each_run_s_gpu(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 20000))
        machine.study.write_text(machine.study.read_text()
                                 + "sweep:\n  simulation.device_index: ['0', '1']\n")
        sending = _plan(machine, tmp_path)
        assert "CUDA_VISIBLE_DEVICES" not in sending.script
        assert set(sending.gpu.uuids) == {UUID_0, UUID_1}

    def test_a_continuation_is_not_pinned(self, machine, tmp_path):
        # A continuation runs with its study's recorded settings, the GPU
        # it ran on among them, which are not read here.
        _gpus(machine, (0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 20000))
        parent = machine.study.parent / "earlier"
        parent.mkdir()
        (parent / "resolved_config.yml").write_text(
            "systems:\n  - system: top.pdb\nsimulation:\n  device_index: '1'\n")
        (parent / "manifest.json").write_text('{"version": "1.0"}')
        machine.study.write_text("simulation:\n  resume_from: earlier\n  extra_ns: 1\n")
        sending = _plan(machine, tmp_path)
        assert "CUDA_VISIBLE_DEVICES" not in sending.script and sending.gpu is None
        assert any("a continuation runs on the GPU its study's record names" in note
                   for note in sending.notes)

    def test_gpus_of_different_kinds_named_by_number_are_not_checked(
            self, machine, tmp_path):
        (machine.home / "gpus.csv").parent.mkdir(exist_ok=True)
        _gpus(machine, (0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 100))
        text = (machine.home / "gpus.csv").read_text().replace(
            "1, " + UUID_1 + ", Stand-in GPU", "1, " + UUID_1 + ", Other GPU")
        (machine.home / "gpus.csv").write_text(text)
        _measured(machine)
        machine.study.write_text(machine.study.read_text()
                                 + "simulation:\n  device_index: '1'\n")
        sending = _plan(machine, tmp_path)
        assert sending.gpu is None and sending.no_room == ""
        assert any("which card each number means is CUDA's" in n for n in sending.notes)

    def test_a_named_gpu_the_machine_lacks_is_not_sent(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 20000))
        machine.study.write_text(machine.study.read_text()
                                 + "simulation:\n  device_index: '3'\n")
        for measured in (False, True):
            if measured:
                _measured(machine, 1000)
            sending = _plan(machine, tmp_path)
            assert sending.no_room == "The config names GPU 3, and box has GPU 0, 1."
            with pytest.raises(ValueError) as caught:
                _go(machine, sending)
            assert refusal_of(caught.value).code == "remote.machine.no_room"

    def test_a_second_send_before_any_run_is_measured_takes_the_idle_gpu(
            self, machine, tmp_path):
        machine.env["FAKE_SLEEP"] = "30"
        _gpus(machine, (0, UUID_0, 24000, 24000), (1, UUID_1, 24000, 24000))
        first = _send(machine)
        try:
            assert first.extra["gpu"]["uuids"] == [UUID_0]
            assert _plan(machine, tmp_path).gpu.gpu.uuid == UUID_1
        finally:
            cancel(first.name, transport=machine.transport())

    def test_one_run_over_two_gpus_is_not_learned_from(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 20000))
        machine.study.write_text(machine.study.read_text()
                                 + "simulation:\n  device_index: '0,1'\n")
        sending = _plan(machine, tmp_path)
        assert sending.gpu.gpu is None and sending.gpu.learn is False
        assert set(sending.gpu.uuids) == {UUID_0, UUID_1}


class TestNinthReviewRunsAtOnce:
    def test_parallel_without_workers_counts_every_run_at_once(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 10000))
        _measured(machine)
        _systems(machine, "top.pdb", "b.pdb", "c.pdb", more="execution:\n  mode: parallel\n")
        sending = _plan(machine, tmp_path)
        # As many at once as the machine has cores, up to the three runs.
        side = min(3, sending.machine.inspection.cpus or 3)
        assert side > 1 and sending.gpu.at_once == {UUID_0: side}
        assert f"needs about {6900 * side:,} MB" in sending.no_room
        assert f"{side} runs side by side" in sending.no_room

    def test_the_machine_s_cores_bound_the_runs_at_once(self):
        from fastmdxplora.remote.send import _runs_of

        config = {"systems": [{"system": f"{n}.pdb"} for n in "abc"],
                  "execution": {"mode": "parallel"}}
        assert _runs_of(config, 2).at_once == 2
        assert _runs_of(config, None).at_once == 3
        assert _runs_of({**config, "execution": {"workers": 8}}, 2).at_once == 3

    def test_workers_beyond_the_runs_are_not_counted(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 20000))
        _measured(machine)
        machine.study.write_text(machine.study.read_text() + "execution:\n  workers: 4\n")
        sending = _plan(machine, tmp_path)
        assert sending.no_room == "" and sending.gpu.at_once == {UUID_0: 1}

    def test_a_device_listed_twice_says_two_runs_side_by_side(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 10000))
        _measured(machine)
        _systems(machine, "top.pdb", "b.pdb",
                 more="execution:\n  workers: 2\n  devices: [0, 0]\n")
        sending = _plan(machine, tmp_path)
        assert "needs about 13,800 MB" in sending.no_room
        assert "2 runs side by side" in sending.no_room


class TestNinthReviewWhatIsLearned:
    def test_a_double_precision_study_is_not_sized_from_mixed_runs(
            self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 23000))
        _measured(machine)
        machine.study.write_text(machine.study.read_text()
                                 + "simulation:\n  precision: double\n")
        sending = _plan(machine, tmp_path)
        assert sending.gpu.need.mb is None
        assert "no run in double precision" in sending.gpu.need.how
        assert sending.gpu.precision == "double"

    def test_a_sweep_that_grows_the_box_is_sized_for_the_largest(self, machine, tmp_path):
        import gzip
        from pathlib import Path

        from fastmdxplora.remote.gpu_room import learn
        from fastmdxplora.setup.estimate import estimate_system

        source = Path(travels.__file__).parent / "data" / "assemblies" / "3PTB.pdb.gz"
        pdb = machine.study.parent / "3PTB.pdb"
        pdb.write_bytes(gzip.decompress(source.read_bytes()))
        small = estimate_system(pdb, {}).particles
        large = estimate_system(pdb, {"solvent_padding_nm": 2.5}).particles
        _gpus(machine, (0, UUID_0, 24000, 23000))
        learn("box", job="before", particles=small, peak_mb=1000, gpu="Stand-in GPU")
        machine.study.write_text(
            "systems:\n  - system: 3PTB.pdb\n"
            "sweep:\n  setup.solvent_padding_nm: [1.0, 2.5]\n")
        assert f"{large:,} particles" in _plan(machine, tmp_path).gpu.need.how

    def test_the_need_is_not_below_a_run_of_the_same_size(self, tmp_path, monkeypatch):
        from fastmdxplora.remote.gpu_room import learn, need_for

        monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
        learn("box", job="a", particles=20_000, peak_mb=900, gpu="g")
        learn("box", job="b", particles=20_000, peak_mb=2000, gpu="g")
        learn("box", job="c", particles=60_000, peak_mb=1500, gpu="g")
        assert need_for("box", 20_000).mb >= 2000

    def test_past_the_largest_run_is_never_below_it_scaled(self, tmp_path, monkeypatch):
        from fastmdxplora.remote.gpu_room import learn, need_for

        monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
        learn("box", job="a", particles=10_000, peak_mb=600, gpu="g")
        learn("box", job="b", particles=11_000, peak_mb=601, gpu="g")
        # 601 MB for 11,000, scaled to 100,000, and 15% more.
        assert need_for("box", 100_000).mb >= 6283

    @pytest.mark.parametrize("runs", ["5", "true", "1.5", '[{"particles": 1000, '
                                      '"peak_mb": 1' + "0" * 400 + "}]"])
    def test_a_damaged_record_of_runs_is_read_as_none(self, tmp_path, monkeypatch, runs):
        from fastmdxplora.remote.gpu_room import measured, need_for

        monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
        folder = tmp_path / "settings" / "gpu_memory"
        folder.mkdir(parents=True)
        (folder / "box.json").write_text(f'{{"runs": {runs}}}')
        assert measured("box") == [] and need_for("box", 2000).mb is None

    def test_a_job_sent_again_learns_its_own_peak_not_the_last_one(
            self, machine, tmp_path, monkeypatch):
        from fastmdxplora.remote import gpu_room

        monkeypatch.setattr(gpu_room, "SAMPLE_EVERY_S", 0.2)
        machine.env["FAKE_SLEEP"] = "1.5"
        _gpus(machine, (0, UUID_0, 24000, 23000))
        _holding(machine, 5000, particles=2000)
        job = travels._until_finished(machine, _go(machine, _plan(machine, tmp_path)).name)
        assert job.extra["gpu_peak_mb"] == 5000
        (machine.home / "apps.csv").unlink()
        _holding(machine, 1000, particles=4000)
        job = _go(machine, _plan(machine, tmp_path, force=True))
        job = travels._until_finished(machine, job.name)
        status(job.name, transport=machine.transport())
        assert job.extra["gpu_peak_mb"] == 1000
        assert [(r["particles"], r["peak_mb"]) for r in gpu_room.measured("box")] == [
            (2000, 5000), (4000, 1000)]

    def test_a_job_sent_again_over_one_refused_at_start_is_not_said_not_started(
            self, machine, tmp_path, monkeypatch):
        from fastmdxplora.remote import send as sending_module

        _gpus(machine, (0, UUID_0, 24000, 10000))
        _measured(machine)
        first = _plan(machine, tmp_path)
        with monkeypatch.context() as patched:
            patched.setattr(sending_module, "_room_now", lambda *args: None)
            _gpus(machine, (0, UUID_0, 24000, 1000))
            job = travels._until_finished(machine, _go(machine, first).name)
        assert job.detail.startswith("not started")
        _gpus(machine, (0, UUID_0, 24000, 23000))
        job = _go(machine, _plan(machine, tmp_path, force=True))
        job = travels._until_finished(machine, job.name)
        assert job.state == "done" and "not started" not in job.detail

    def test_a_run_holding_what_it_was_measured_to_hold_is_not_kept_for(
            self, machine, tmp_path):
        machine.env["FAKE_SLEEP"] = "30"
        _gpus(machine, (0, UUID_0, 24000, 23000))
        _measured(machine)
        _holding(machine, 6000)
        first = _go(machine, _plan(machine, tmp_path, "trial"))
        try:
            deadline = time.monotonic() + 10
            while not (machine.home / "apps.csv").exists():
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert "kept for" not in "\n".join(_plan(machine, tmp_path).gpu.lines)
        finally:
            cancel(first.name, transport=machine.transport())


class TestNinthReviewWhatTheMachineSays:
    def test_a_no_room_file_with_other_digits_does_not_crash_status(self, machine):
        from pathlib import Path

        machine.env["FAKE_EXIT"] = "75"
        _gpus(machine, (0, UUID_0, 24000, 23000))
        job = _send(machine)
        _ended_there(job)
        (Path(job.remote_dir) / "no_room").write_text(f"{UUID_0} ² ³\n")
        assert status(job.name, transport=machine.transport()).state == "failed"

    def test_a_plan_whose_gpus_do_not_answer_again_is_not_sent(self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 23000))
        _measured(machine)
        sending = _plan(machine, tmp_path)
        assert sending.no_room == ""
        (machine.home / "gpus.csv").write_text("")
        with pytest.raises(ValueError) as caught:
            _go(machine, sending)
        assert refusal_of(caught.value).code == "remote.machine.no_room"
        assert not (machine.home / "fastmdxplora-jobs" / "second").exists()

    def test_a_refusal_at_the_send_names_the_gpu_planned_and_the_one_with_room(
            self, machine, tmp_path):
        _gpus(machine, (0, UUID_0, 24000, 2000), (1, UUID_1, 24000, 20000))
        _measured(machine)
        sending = _plan(machine, tmp_path)
        assert sending.gpu.uuids == (UUID_1,) and sending.no_room == ""
        _gpus(machine, (0, UUID_0, 24000, 15000), (1, UUID_1, 24000, 1000))
        with pytest.raises(ValueError) as caught:
            _go(machine, sending)
        said = str(caught.value)
        assert "GPU 1 on box (Stand-in GPU), chosen when the send was planned, has 1,000" \
            in said
        assert "GPU 0 has 15,000 MB free: plan the send again" in said

    def test_a_sampler_stops_with_its_job_where_no_exit_code_is_written(self):
        from fastmdxplora.remote.gpu_room import Choice, Gpu
        from fastmdxplora.remote.send import _gpu_lines

        gpu = Gpu(0, UUID_0, "g", 1, 0, 1, None)
        said = "\n".join(_gpu_lines(Choice(gpu=gpu, uuids=(UUID_0,))))
        assert 'while [ ! -f exit_code ] && kill -0 "$1"' in said


class TestNinthReviewAnAIApp:
    def test_another_folder_s_job_is_not_named_in_the_gpu_lines(self, app):
        app.machine.env["FAKE_SLEEP"] = "30"
        _gpus(app.machine, (0, UUID_0, 24000, 24000))
        _measured(app.machine)
        other = _send(app.machine)          # results go outside the workspace
        try:
            first, _ = _start(app)
            asked = first["inputRequests"]["send"]["params"]["message"]
            assert "1 other study sent from here" in asked
            assert "kept for 1 run from here" in asked
            assert other.name not in asked
        finally:
            cancel(other.name, transport=app.machine.transport())

    def test_asking_to_start_again_does_not_ask_about_a_job_each_time(
            self, app, monkeypatch):
        from fastmdxplora.remote import send as sending_module

        app.machine.env["FAKE_SLEEP"] = "30"
        _gpus(app.machine, (0, UUID_0, 24000, 24000))
        other = _send(app.machine)
        asked: list[str] = []
        real = sending_module._status_script
        monkeypatch.setattr(sending_module, "_status_script",
                            lambda job: asked.append(job.name) or real(job))
        try:
            for _ in range(3):
                _start(app)
            assert asked.count(other.name) <= 1, asked
        finally:
            monkeypatch.setattr(sending_module, "_status_script", real)
            cancel(other.name, transport=app.machine.transport())
