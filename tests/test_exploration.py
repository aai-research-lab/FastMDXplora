from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _the_stack_is_taken_as_installed(monkeypatch):
    """These tests launch and then stop a child at once, so they never
    reached the phase that would have failed without OpenMM. The launch
    now refuses up front where the chemistry stack is missing, as it
    should for a person; here the stack is declared present so a launch
    test tests the launch, on the CI legs without OpenMM as well. The
    preflight itself is tested in test_exploration.py."""
    from fastmdxplora.gui import exploration

    real = exploration.exploration_environment_error
    monkeypatch.setattr(exploration, "exploration_environment_error", lambda _config: None)
    return real

from fastmdxplora.gui.exploration import (
    DashboardRuntime,
    _json_mapping,
)
from fastmdxplora.gui.server import start_dashboard_session, start_test_server


def test_exploration_json_mapping_handles_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("not-json", encoding="utf-8")
    assert _json_mapping(path) == {}


def test_config_launch_clears_stale_data_state(tmp_path: Path) -> None:
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
        data_stale=True,
    )
    fake_process = SimpleNamespace(pid=43, poll=lambda: None)
    (tmp_path / "new-run").mkdir()
    prepared = {
        "ok": True,
        "command": ["python", "-m", "fastmdxplora.cli.main", "explore"],
        "config_path": str(tmp_path / "run.yml"),
    }
    with (
        patch("fastmdxplora.gui.run_from_config.prepare_run", return_value=prepared),
        patch("fastmdxplora.gui.exploration.subprocess.Popen", return_value=fake_process),
    ):
        result = runtime.launch_from_config(
            {"output": str(tmp_path / "new-run")},
            dashboard_url="http://127.0.0.1:8765",
        )

    assert result["ok"] is True
    assert runtime.data_stale is False
    assert runtime.snapshot()["active_run"] == str(tmp_path / "new-run")


def test_runtime_ignores_malformed_telemetry_timestamps(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "run"
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
        active_root=run,
        process_started_at="2026-08-19T05:07:35+00:00",
    )
    status = run / "simulation" / "live_status.json"
    status.parent.mkdir(parents=True)
    status.write_text('{"run_started_at": "not-a-timestamp"}', encoding="utf-8")

    assert runtime._telemetry_predates_process() is False


def test_runtime_failure_message_survives_unreadable_log(tmp_path: Path) -> None:
    log_path = tmp_path / "unreadable-log"
    log_path.mkdir()
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
        log_path=log_path,
        process_returncode=2,
    )

    message = runtime._process_failure_message()

    assert "exited with code 2" in message
    assert str(log_path) in message


def test_existing_config_launch_refuses_nonempty_output_directory(tmp_path: Path) -> None:
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
    )
    output = tmp_path / "existing-output"
    output.mkdir()
    (output / "manifest.json").write_text("{}", encoding="utf-8")
    checked = {"ok": True, "path": str(tmp_path / "run.yml")}

    with (
        patch("fastmdxplora.gui.config_builder.check_config_file", return_value=checked),
        patch("fastmdxplora.gui.exploration.subprocess.Popen") as popen,
    ):
        result = runtime.launch_existing_config(str(tmp_path / "run.yml"), output=str(output))

    assert result["ok"] is False
    assert "already exists and is not empty" in result["error"]
    assert result["next_action"] == "Choose a new output folder; the previous run was preserved."
    assert runtime.data_stale is True
    popen.assert_not_called()


def test_runtime_stop_escalates_after_terminate_timeout(tmp_path: Path) -> None:
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
    )
    calls: list[str] = []
    wait_calls = 0

    def wait(*, timeout: int) -> None:
        nonlocal wait_calls
        wait_calls += 1
        calls.append(f"wait:{timeout}")
        if wait_calls == 1:
            raise subprocess.TimeoutExpired("fake", timeout)

    runtime.process = SimpleNamespace(
        poll=lambda: None,
        terminate=lambda: calls.append("terminate"),
        wait=wait,
        kill=lambda: calls.append("kill"),
    )

    result = runtime.stop()

    assert result["stopped"] is True
    assert calls == ["terminate", "wait:5", "kill", "wait:5"]


def test_runtime_rejects_zero_exit_without_simulation_outputs(tmp_path: Path) -> None:
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
    )
    run_root = runtime.exploration_root / "incomplete"
    (run_root / "setup").mkdir(parents=True)
    (run_root / "simulation").mkdir()
    (run_root / "setup" / "setup_parameters.json").write_text(
        json.dumps({"notes": ["PDBFixer unavailable"]}),
        encoding="utf-8",
    )
    runtime.active_root = run_root
    runtime.log_path = run_root / "exploration.log"
    runtime.command = ["python", "-m", "fastmdxplora.cli.main", "explore"]
    runtime.process = SimpleNamespace(poll=lambda: 0)

    state = runtime.snapshot()

    assert state["status"] == "failed"
    assert state["returncode"] == 0
    assert "Setup did not produce" in state["error"]
    live_status = json.loads(
        (run_root / "simulation" / "live_status.json").read_text(encoding="utf-8")
    )
    assert live_status["status"] == "failed"
    assert live_status["stage_states"]["setup"] == "failed"
    assert live_status["stage_states"]["production"] == "skipped"
    assert live_status["stage_states"]["analysis"] == "skipped"
    assert live_status["stage_states"]["report"] == "skipped"


def test_runtime_accepts_zero_exit_with_completed_simulation(tmp_path: Path) -> None:
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
    )
    run_root = runtime.exploration_root / "complete"
    setup_dir = run_root / "setup"
    simulation_dir = run_root / "simulation"
    setup_dir.mkdir(parents=True)
    simulation_dir.mkdir()
    for name in ("system.xml", "state.xml", "topology.pdb"):
        (setup_dir / name).write_text("ready", encoding="utf-8")
    (simulation_dir / "state_final.xml").write_text("<State />", encoding="utf-8")
    (simulation_dir / "simulation_parameters.json").write_text(
        json.dumps({"platform_used": "CPU", "duration_ns_actual": 0.02}),
        encoding="utf-8",
    )
    runtime.active_root = run_root
    runtime.command = ["python", "-m", "fastmdxplora.cli.main", "explore"]
    runtime.process = SimpleNamespace(poll=lambda: 0)

    state = runtime.snapshot()

    assert state["status"] == "completed"
    assert state["error"] is None


def test_home_server_exposes_the_run_page_and_its_apis(tmp_path: Path) -> None:
    server, url = start_test_server(tmp_path / "workspace", home_mode=True)
    try:
        with urllib.request.urlopen(url + "/") as response:
            html = response.read().decode("utf-8")
        assert "Builder" in html  # the run page, renamed with its nav entry
        assert "/static/run-builder.js" in html
        with urllib.request.urlopen(url + "/api/app-state") as response:
            state = json.load(response)
        assert state["active_run"] is None
        with urllib.request.urlopen(url + "/api/status") as response:
            status = json.load(response)
        assert status["status"] == {}
        with urllib.request.urlopen(url + "/api/structure-info") as response:
            structure = json.load(response)
        assert structure["valid"] is False
        # The form's own routes: the schema it is built from, and the config
        # it builds. The explore/* family they replaced is gone.
        with urllib.request.urlopen(url + "/api/schema") as response:
            assert "phases" in json.load(response)
        for gone in ("/api/explore/defaults", "/api/explore/validate", "/api/explore/config",
                     "/api/explore/start"):
            request = urllib.request.Request(url + gone, data=b"{}",
                                             headers={"Content-Type": "application/json"},
                                             method="POST")
            try:
                urllib.request.urlopen(request)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404, gone
            else:
                raise AssertionError(f"{gone} still answers")
    finally:
        server.shutdown()


def test_remote_dashboard_disables_workflow_control_and_path_leak(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "result.txt").write_text("ok", encoding="utf-8")
    session = start_dashboard_session(output=run, host="0.0.0.0", port=0)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{session.port}/api/run",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request)
        assert exc_info.value.code == 403

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{session.port}/api/open-output")
        assert exc_info.value.code == 403

        with urllib.request.urlopen(
            f"http://127.0.0.1:{session.port}/api/artifacts"
        ) as response:
            payload = json.load(response)
        assert all("absolute_path" not in item for item in payload["artifacts"])
    finally:
        session.stop()


# ---------------------------------------------------------------------------
# Config-file generation (GUI "Save config file")
# ---------------------------------------------------------------------------
def _switch_runtime(tmp_path):
    from fastmdxplora.gui.exploration import DashboardRuntime

    rt = DashboardRuntime(tmp_path / "w", tmp_path / "e")
    return rt, tmp_path / "e"


def _make_run(base, name):
    run = base / name
    (run / "simulation").mkdir(parents=True)
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    return run


def test_switch_between_run_folders(tmp_path):
    # The GUI was bound to the one folder given at launch; switch_to makes
    # loading another study a control in the page.
    rt, base = _switch_runtime(tmp_path)
    a, b = _make_run(base, "a"), _make_run(base, "b")
    assert rt.switch_to(a)["ok"]
    assert Path(rt.active_root) == a.resolve()
    assert rt.switch_to(b)["ok"]
    assert Path(rt.active_root) == b.resolve()


def test_switch_forgets_the_previous_run(tmp_path):
    rt, base = _switch_runtime(tmp_path)
    rt.completion_error = "old failure"
    rt.process_returncode = 1
    rt.switch_to(_make_run(base, "a"))
    assert rt.completion_error is None
    assert rt.process_returncode is None
    assert rt.process is None


def test_switch_refuses_a_folder_that_is_not_a_run(tmp_path):
    rt, base = _switch_runtime(tmp_path)
    plain = base / "notes"
    plain.mkdir()
    answer = rt.switch_to(plain)
    assert not answer["ok"]
    assert "does not look like" in answer["error"]


def test_switch_refuses_a_missing_folder(tmp_path):
    rt, base = _switch_runtime(tmp_path)
    assert not rt.switch_to(base / "nope")["ok"]


def test_switch_while_running_views_another_and_keeps_the_process(tmp_path):
    # A two-day run should not lock a person out of their other studies.
    # The process keeps running where it is and stays stoppable; the
    # viewed study is idle and the snapshot says where the live one is.
    rt, base = _switch_runtime(tmp_path)
    a, b = _make_run(base, "a"), _make_run(base, "b")

    class Proc:
        def poll(self):
            return None

    rt.active_root = a.resolve()
    rt.running_root = a.resolve()
    rt.process = Proc()
    rt.process_started_at = "2026-01-01T00:00:00+00:00"
    answer = rt.switch_to(b)
    assert answer["ok"]
    assert rt.process is not None
    assert Path(rt.running_root) == a.resolve()
    snap = rt.snapshot()
    assert snap["status"] == "idle"
    assert snap["process_running"] is False
    assert snap["running_elsewhere"] == str(a.resolve())
    # Back to the running one: its process state returns.
    rt.switch_to(a)
    snap = rt.snapshot()
    assert snap["status"] == "running"
    assert snap["running_elsewhere"] is None


def test_the_sidebar_says_where_the_live_run_is():
    import pathlib

    import fastmdxplora.gui as gui

    page = (pathlib.Path(gui.__file__).parent / "templates"
            / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="study-elsewhere"' in page
    assert 'id="study-elsewhere-view"' in page


def test_the_sidebar_has_the_load_control():
    import pathlib

    import fastmdxplora.gui as gui

    page = (pathlib.Path(gui.__file__).parent / "templates"
            / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="load-study"' in page
    # In the study block, not the controls row: which study this is, is
    # that block's whole question, and the row holds three.
    study = page[page.index('class="sidebar-study"'):page.index('class="sidebar-progress"')]
    assert 'id="load-study"' in study
    row = page[page.index('class="sidebar-controls"'):]
    row = row[:row.index("</div>")]
    assert row.count("<button") == 3
    # No data-picks on the hidden input: the picker would attach a second
    # "Browse" button for the same action.
    assert 'id="load-study-path" data-picks' not in page
    frame = (pathlib.Path(gui.__file__).parent / "static"
             / "frame.js").read_text(encoding="utf-8")
    assert 'fetch("/api/explore/switch"' in frame


def test_the_running_study_reports_its_progress_when_viewed_from_elsewhere(tmp_path):
    # The sidebar's Running line shows the system, its fraction complete,
    # and a View button. The fraction is read from the running study's
    # own telemetry; nothing is guessed.
    import json

    rt, base = _switch_runtime(tmp_path)
    a, b = _make_run(base, "fastmdxplora_1UAO_study_20260919022007"), _make_run(base, "b")
    (a / "simulation" / "live_status.json").write_text(json.dumps(
        {"stage": "production", "current_step": 175000, "total_planned_steps": 350000}),
        encoding="utf-8")

    class Proc:
        def poll(self):
            return None

    rt.active_root = a.resolve()
    rt.running_root = a.resolve()
    rt.process = Proc()
    rt.process_started_at = "2026-01-01T00:00:00+00:00"
    rt.switch_to(b)
    snap = rt.snapshot()
    assert snap["running_elsewhere_progress"] == {"stage": "production", "percent": 50.0}
    rt.switch_to(a)
    assert rt.snapshot()["running_elsewhere_progress"] is None


def test_the_sidebar_reads_top_down():
    import pathlib

    import fastmdxplora.gui as gui

    page = (pathlib.Path(gui.__file__).parent / "templates"
            / "dashboard.html").read_text(encoding="utf-8")
    study = page[page.index('class="sidebar-study"'):page.index('class="sidebar-progress"')]
    # Status row, then Running, then Load available study.
    assert study.index('id="topbar-status-text"') < study.index('id="study-elsewhere"') < study.index('id="load-study"')
    assert 'id="study-elsewhere-pct"' in study
    assert ">Load available study<" in study


def test_the_run_outlives_the_server(tmp_path: Path) -> None:
    # Without start_new_session the run sat in the terminal's process
    # group, and Ctrl-C on the server sent SIGINT to the run as well: a
    # day-long simulation died mid-step, not by any decision but because
    # the terminal delivers the signal to the whole group. A run started
    # here is in a process group of its own.
    import os
    import sys

    if not hasattr(os, "getpgid"):
        pytest.skip("process groups are a POSIX notion")
    runtime = DashboardRuntime(workspace_root=tmp_path / "workspace",
                               exploration_root=tmp_path / "runs")
    (tmp_path / "run").mkdir()
    runtime._spawn([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "run", None)
    try:
        assert os.getpgid(runtime.process.pid) != os.getpgid(0)
    finally:
        runtime.process.kill()
        runtime.process.wait(timeout=10)

def test_stopping_the_server_says_what_is_still_running(tmp_path: Path, monkeypatch,
                                                        capsys) -> None:
    # Ctrl-C on the server stops the server and not the run, and says which
    # study is still going and how to stop it; a run that has finished is
    # not mentioned.
    from fastmdxplora.gui import server

    for still_going, pid in ((True, 4242), (False, 4243)):
        process = SimpleNamespace(pid=pid, poll=lambda _g=still_going: None if _g else 0)
        session = SimpleNamespace(
            url="http://127.0.0.1:1/", port_was_changed=False, root=tmp_path,
            runtime=SimpleNamespace(process=process, running_root=tmp_path / "study",
                                    active_root=None),
            wait_forever=lambda: (_ for _ in ()).throw(KeyboardInterrupt()), stop=lambda: None)
        monkeypatch.setattr(server, "start_dashboard_session", lambda _s=session, **kw: _s)
        server.serve_dashboard(output=tmp_path, host="127.0.0.1", port=0)
        said = capsys.readouterr().out
        if still_going:
            assert f"The study in {tmp_path / 'study'} is still running (pid {pid})" in said
            assert f"kill {pid}" in said
        else:
            assert "still running" not in said

def test_a_new_session_child_leaves_the_terminals_group():
    import os
    import subprocess
    import sys
    import time

    import pytest

    if not hasattr(os, "getpgid"):
        pytest.skip("process groups are a POSIX notion; Windows has no getpgid")

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                             start_new_session=True)
    try:
        time.sleep(0.2)
        assert os.getpgid(child.pid) != os.getpgid(os.getpid())
    finally:
        child.terminate()
        child.wait()


def _fake_run(study):
    """A process whose command line names the study, as fastmdx's does."""
    import subprocess
    import sys
    import time

    script = study.parent / "fake_fastmdx.py"
    script.write_text("import time; time.sleep(60)\n", encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script), "explore", "--output", str(study)],
                             start_new_session=True)
    time.sleep(0.3)
    return child


def test_a_fresh_server_adopts_a_running_study_and_can_stop_it(tmp_path):
    # A GUI reopened on a running study could watch it but not stop it,
    # and its sidebar called it idle. The run records its PID; a server
    # opened on the folder adopts it.
    import json

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    study = _make_run(tmp_path, "fastmdxplora_1UAO_study_20260919120000")
    child = _fake_run(study)
    try:
        (study / RUN_PROCESS_FILE).write_text(json.dumps(
            {"pid": child.pid, "argv": ["fastmdx", "explore"], "started_at": "2026-09-19T12:00:00+00:00"}),
            encoding="utf-8")
        rt = DashboardRuntime(workspace_root=study, exploration_root=tmp_path, active_root=study)
        # Adopted once its identity is confirmed. On a loaded Windows runner
        # the first PowerShell lookup can miss its timeout; the retry then
        # adopts it, so this waits inside the retry window rather than
        # requiring the first answer to be the one.
        assert _until(lambda: rt.snapshot()["status"] == "running", seconds=45)
        snap = rt.snapshot()
        assert snap["process_running"] is True
        assert rt.process.pid == child.pid
        assert rt.stop()["stopped"] is True
        child.wait(timeout=5)
        assert rt.snapshot()["status"] in {"failed", "completed"}
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_a_stale_pid_record_is_not_adopted(tmp_path):
    import json

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    study = _make_run(tmp_path, "s")
    (study / RUN_PROCESS_FILE).write_text(json.dumps({"pid": 2 ** 22 - 7, "argv": []}),
                                          encoding="utf-8")
    rt = DashboardRuntime(workspace_root=study, exploration_root=tmp_path, active_root=study)
    assert rt.process is None
    assert rt.snapshot()["status"] == "idle"


def test_a_process_that_is_not_this_run_is_not_adopted(tmp_path):
    # The PID is alive but its command line names neither the study nor
    # the program: the OS reused the number for something else.
    import json
    import subprocess
    import sys
    import time

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    study = _make_run(tmp_path, "s")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.2)
        (study / RUN_PROCESS_FILE).write_text(json.dumps({"pid": other.pid, "argv": []}),
                                              encoding="utf-8")
        rt = DashboardRuntime(workspace_root=study, exploration_root=tmp_path, active_root=study)
        assert rt.process is None
    finally:
        other.kill()
        other.wait()


def test_load_study_adopts_a_running_one(tmp_path):
    import json

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    study = _make_run(tmp_path, "fastmdxplora_1L2Y_study_20260919120000")
    child = _fake_run(study)
    try:
        (study / RUN_PROCESS_FILE).write_text(json.dumps({"pid": child.pid, "argv": []}),
                                              encoding="utf-8")
        rt = DashboardRuntime(workspace_root=tmp_path / "w", exploration_root=tmp_path, active_root=None)
        assert rt.switch_to(study)["ok"]
        assert _until(lambda: rt.snapshot()["status"] == "running", seconds=45)
    finally:
        child.kill()
        child.wait()


def test_the_orchestrator_records_its_pid_and_removes_it_on_exit(tmp_path):
    import subprocess
    import sys

    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    out = tmp_path / "study"
    src = str(Path(__file__).resolve().parents[1] / "src")
    code = "\n".join([
        f"import sys; sys.path.insert(0, {src!r})",
        "from fastmdxplora.orchestrator import _record_run_process",
        "from pathlib import Path",
        f"_record_run_process(Path({str(out)!r}))",
        f"import json, os; print(json.load(open(Path({str(out)!r}) / {RUN_PROCESS_FILE!r}))['pid'] == os.getpid())",
    ])
    out.mkdir()
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert result.stdout.strip().endswith("True"), result.stderr
    # Gone once the process has exited.
    assert not (out / RUN_PROCESS_FILE).exists()


def test_a_run_is_judged_by_what_it_runs_not_where_the_interpreter_lives():
    # "fastmdx" as a substring matched the whole command line, and on a
    # Mac whose conda environment is named fastmdxplora every Python
    # process carried it in its interpreter path: a stale PID reused by
    # any Python would have been adopted, and Stop would have killed it.
    from fastmdxplora.gui.exploration import _command_line_is_a_run

    study = Path("/Users/someone/lab/fastmdxplora_1UAO_study_x")
    env = "/Users/someone/.conda/envs/fastmdxplora/bin/python3"
    assert not _command_line_is_a_run(f"{env} -c import time; time.sleep(30)", study)
    assert not _command_line_is_a_run(f"{env} -m jupyter notebook", study)
    assert _command_line_is_a_run(f"{env} /Users/someone/.conda/envs/fastmdxplora/bin/fastmdx explore", study)
    assert _command_line_is_a_run("/usr/bin/python3 -m fastmdxplora.cli.main explore --output /tmp/s", study)
    assert _command_line_is_a_run(f"/usr/bin/python3 fake.py --output {study}", study)
    assert not _command_line_is_a_run("/usr/bin/python3 fake.py --output /Users/someone/lab/fastmdxplora_1UAO_study_y", study)
    # Windows: quoted tokens, backslashes, an .exe entry point. Judged the
    # same way, once the quotes are off and the separators agree.
    win = Path(r"C:\Users\r\Temp\x\fastmdxplora_1UAO_study_1")
    assert _command_line_is_a_run(
        r'"C:\py\python.exe" "C:\x\fake.py" explore --output C:\Users\r\Temp\x\fastmdxplora_1UAO_study_1', win)
    assert _command_line_is_a_run(r'"C:\envs\fastmdxplora\Scripts\fastmdx.exe" explore', win)
    assert not _command_line_is_a_run(r'"C:\envs\fastmdxplora\python.exe" -c "import time"', win)


def test_liveness_never_touches_the_process():
    # On Windows os.kill(pid, 0) is TerminateProcess: the POSIX liveness
    # check killed the process it was checking. The primitive must leave a
    # live process alive and report it so.
    import subprocess
    import sys
    import time

    from fastmdxplora.gui.exploration import _process_alive

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.3)
        assert _process_alive(child.pid) is True
        time.sleep(0.3)
        assert child.poll() is None, "checking liveness must not end the process"
    finally:
        child.kill()
        child.wait()
    assert _process_alive(child.pid) is False


def test_an_unreadable_command_line_is_not_trusted(monkeypatch):
    # Alive, but no way to see what it is: not adopted. Adopting it would
    # mean Stop could kill a process the OS reused the number for.
    import os

    from fastmdxplora.gui import exploration

    monkeypatch.setattr(exploration, "_command_line_of", lambda pid: None)
    assert exploration._process_is_this_run(os.getpid(), Path("/nowhere")) is False



# ---------------------------------------------------------------------------
# A run whose identity cannot be read yet is asked about again.
#
# On Windows the command line is read by starting PowerShell, which on a
# loaded machine can miss its ten-second timeout the first time and answer
# the next. Adoption was tried once, so a running study opened on such a
# machine read `idle`, with no Stop, until it was reopened -- the problem
# adoption was built to fix, surviving on the slow path. These pin the
# retry without depending on PowerShell's timing, which is what made the
# original test flaky.
# ---------------------------------------------------------------------------


def _adoption_runtime(tmp_path, monkeypatch, answers):
    """A study with a live run, whose command line answers from `answers`."""
    import json

    from fastmdxplora.gui import exploration
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    monkeypatch.setattr(exploration, "ADOPTION_RETRY_INTERVAL", 0.05)
    monkeypatch.setattr(exploration, "ADOPTION_RETRY_SECONDS", 5.0)
    study = _make_run(tmp_path, "fastmdxplora_1UAO_study_20260919120000")
    child = _fake_run(study)
    (study / RUN_PROCESS_FILE).write_text(json.dumps(
        {"pid": child.pid, "argv": ["fastmdx", "explore"],
         "started_at": "2026-09-19T12:00:00+00:00"}), encoding="utf-8")
    replies = iter(answers)
    last = [None]

    def command_line(pid):
        try:
            last[0] = next(replies)
        except StopIteration:
            pass
        return last[0](study) if callable(last[0]) else last[0]

    monkeypatch.setattr(exploration, "_command_line_of", command_line)
    return study, child


def _until(predicate, seconds=5.0):
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_a_run_that_cannot_be_identified_yet_is_asked_again(tmp_path, monkeypatch):
    from fastmdxplora.gui.exploration import DashboardRuntime

    ours = lambda study: f"python -m fastmdxplora.cli explore --output {study}"  # noqa: E731
    study, child = _adoption_runtime(tmp_path, monkeypatch, [None, None, ours])
    try:
        rt = DashboardRuntime(workspace_root=study, exploration_root=tmp_path, active_root=study)
        # Not adopted on "cannot tell" -- the safety rule is unchanged.
        assert rt.process is None
        assert _until(lambda: rt.snapshot()["status"] == "running")
        assert rt.process.pid == child.pid
    finally:
        child.kill(); child.wait()


def test_a_run_found_not_to_be_ours_is_never_adopted(tmp_path, monkeypatch):
    # The reason the rule exists: a PID the OS has reused for something
    # else. A slow first answer must not turn into adopting it.
    from fastmdxplora.gui.exploration import DashboardRuntime

    study, child = _adoption_runtime(tmp_path, monkeypatch,
                                     [None, "notepad.exe C:\\notes.txt"])
    try:
        rt = DashboardRuntime(workspace_root=study, exploration_root=tmp_path, active_root=study)
        assert _until(lambda: rt._adoption_thread is not None
                      and not rt._adoption_thread.is_alive())
        assert rt.process is None
        assert rt.snapshot()["status"] == "idle"
    finally:
        child.kill(); child.wait()


def test_a_run_that_stays_unidentifiable_is_given_up_on(tmp_path, monkeypatch):
    from fastmdxplora.gui import exploration
    from fastmdxplora.gui.exploration import DashboardRuntime

    study, child = _adoption_runtime(tmp_path, monkeypatch, [None])
    # After the helper, which sets its own window, and before the runtime
    # starts the retry that reads it.
    monkeypatch.setattr(exploration, "ADOPTION_RETRY_SECONDS", 0.3)
    try:
        rt = DashboardRuntime(workspace_root=study, exploration_root=tmp_path, active_root=study)
        assert _until(lambda: not rt._adoption_thread.is_alive(), seconds=3)
        assert rt.process is None
    finally:
        child.kill(); child.wait()


def test_opening_another_study_cancels_the_retry(tmp_path, monkeypatch):
    # A retry must never adopt the run of a study the person has left.
    from fastmdxplora.gui.exploration import DashboardRuntime

    ours = lambda study: f"python -m fastmdxplora.cli explore --output {study}"  # noqa: E731
    study, child = _adoption_runtime(tmp_path, monkeypatch, [None] * 5 + [ours])
    other = _make_run(tmp_path, "fastmdxplora_1L2Y_study_20260919130000")
    try:
        rt = DashboardRuntime(workspace_root=study, exploration_root=tmp_path, active_root=study)
        rt.switch_to(other)
        assert _until(lambda: not rt._adoption_thread.is_alive())
        assert rt.process is None
    finally:
        child.kill(); child.wait()


def test_a_slow_answer_does_not_hold_up_the_page(tmp_path, monkeypatch):
    # The retry reads the command line outside the lock, so the page's own
    # requests do not wait behind a PowerShell that takes its time.
    import time

    def slow(study):
        time.sleep(1.5)
        return None

    from fastmdxplora.gui.exploration import DashboardRuntime

    study, child = _adoption_runtime(tmp_path, monkeypatch, [None, slow])
    try:
        rt = DashboardRuntime(workspace_root=study, exploration_root=tmp_path, active_root=study)
        time.sleep(0.2)  # let the retry reach its slow read
        started = time.monotonic()
        rt.snapshot()
        assert time.monotonic() - started < 0.5, "snapshot waited behind the retry"
    finally:
        child.kill(); child.wait()


def test_a_study_is_retried_by_one_thread_not_many(tmp_path, monkeypatch):
    # Asking again for a study already being retried starts nothing new.
    # Without this, reopening a slow study repeatedly would pile up
    # threads, each starting its own PowerShell.
    from fastmdxplora.gui.exploration import DashboardRuntime

    study, child = _adoption_runtime(tmp_path, monkeypatch, [None])
    try:
        rt = DashboardRuntime(workspace_root=study, exploration_root=tmp_path, active_root=study)
        first = rt._adoption_thread
        assert first is not None and first.is_alive()
        rt._retry_adoption(study.resolve())
        assert rt._adoption_thread is first
    finally:
        child.kill(); child.wait()


def test_the_launch_is_without_a_shell_and_carries_the_dashboard_flag(tmp_path: Path) -> None:
    runtime = DashboardRuntime(workspace_root=tmp_path / "workspace",
                               exploration_root=tmp_path / "runs")
    fake_process = SimpleNamespace(pid=42, poll=lambda: None, terminate=lambda: None)
    (tmp_path / "new-run").mkdir()
    (tmp_path / "run.yml").write_text("systems:\n  - system: 1UBQ\n", encoding="utf-8")
    prepared = {"ok": True, "command": ["python", "-m", "fastmdxplora.cli.main", "explore"],
                "config_path": str(tmp_path / "run.yml")}
    with (
        patch("fastmdxplora.gui.run_from_config.prepare_run", return_value=prepared),
        patch("fastmdxplora.gui.exploration.exploration_environment_error", return_value=None),
        patch("fastmdxplora.gui.exploration.subprocess.Popen", return_value=fake_process) as popen,
    ):
        result = runtime.launch_from_config({"output": str(tmp_path / "new-run")},
                                            dashboard_url="http://127.0.0.1:8765")
    assert result["ok"] is True
    kwargs = popen.call_args.kwargs
    assert kwargs["shell"] is False
    assert kwargs["env"]["FASTMDX_DASHBOARD_ACTIVE"] == "1"


def test_a_launch_is_refused_before_a_process_when_the_stack_is_missing(tmp_path: Path) -> None:
    """The phase would fail inside the run with the same message, minutes
    later and off screen. The old launch path checked this; the one the
    form and the Agent use did not."""
    runtime = DashboardRuntime(workspace_root=tmp_path / "workspace",
                               exploration_root=tmp_path / "runs")
    (tmp_path / "new-run").mkdir()
    (tmp_path / "run.yml").write_text("systems:\n  - system: 1UBQ\n", encoding="utf-8")
    prepared = {"ok": True, "command": ["python", "-m", "fastmdxplora.cli.main", "explore"],
                "config_path": str(tmp_path / "run.yml")}
    detail = "Simulation dependencies are unavailable: OpenMM, PDBFixer."
    with (
        patch("fastmdxplora.gui.run_from_config.prepare_run", return_value=prepared),
        patch("fastmdxplora.gui.exploration.exploration_environment_error", return_value=detail),
        patch("fastmdxplora.gui.exploration.subprocess.Popen") as popen,
    ):
        result = runtime.launch_from_config({"output": str(tmp_path / "new-run")})
    assert result["ok"] is False and result["error"] == detail
    popen.assert_not_called()


def test_the_preflight_reads_the_plan_from_the_config_itself(
        _the_stack_is_taken_as_installed) -> None:
    # The real check, handed back by the fixture that stands it down for
    # the launch tests around it.
    preflight = _the_stack_is_taken_as_installed
    asked = []
    with patch("fastmdxplora.gui.exploration.missing_dependencies",
               side_effect=lambda include_analysis: asked.append(include_analysis) or []):
        preflight({"systems": [{"system": "x"}]})
        preflight({"include_phase": ["setup", "simulation"]})
        preflight({"include_phase": ["analysis"]})
    assert asked == [True, False, True]
