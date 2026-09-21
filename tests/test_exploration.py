from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fastmdxplora.dependencies import MissingDependency
from fastmdxplora.gui.exploration import (
    DashboardRuntime,
    _json_mapping,
    build_exploration_command,
    exploration_environment_error,
    exploration_defaults,
    validate_exploration_payload,
)
from fastmdxplora.gui.server import start_dashboard_session, start_test_server


def _payload() -> dict:
    return {
        "system": "1L2Y",
        "run_name": "trpcage_test",
        "setup": {
            "ph": 7.4,
            "forcefield": "charmm36",
            "water_model": "auto",
            "ion_concentration_M": 0.15,
            "solvent_padding_nm": 1.0,
        },
        "simulation": {
            "minimize": True,
            "nvt_steps": 1000,
            "npt_steps": 1000,
            "production_steps": 10000,
            "timestep_fs": 2.0,
            "temperature_K": 300,
            "friction_per_ps": 1.0,
            "integrator": "langevin_middle",
            "platform": "CPU",
            "precision": "mixed",
            "trajectory_interval_steps": 100,
            "checkpoint_interval_steps": 1000,
            "telemetry_interval": 100,
        },
        "workflow": {
            "run_analysis": True,
            "run_report": True,
            "analyses": ["rmsd", "rg"],
            "report_document": True,
            "report_slides": True,
            "report_bundle": True,
        },
    }


def test_exploration_defaults_are_backend_derived() -> None:
    defaults = exploration_defaults()
    assert defaults["setup"]["forcefield"] == "auto"
    assert defaults["simulation"]["nvt_steps"] == 250_000
    assert "CPU" in defaults["choices"]["platforms"]


def test_exploration_validation_computes_durations() -> None:
    result = validate_exploration_payload(_payload())
    assert result["valid"] is True
    assert result["summary"]["production_ns"] == 0.02
    assert result["summary"]["trajectory_frames"] == 100


def test_exploration_validation_rejects_bad_values() -> None:
    payload = _payload()
    payload["system"] = ""
    payload["simulation"]["production_steps"] = 0
    result = validate_exploration_payload(payload)
    assert result["valid"] is False
    assert "system" in result["errors"]
    assert "simulation.production_steps" in result["errors"]


def test_exploration_command_uses_module_entrypoint(tmp_path: Path) -> None:
    result = validate_exploration_payload(_payload())
    command = build_exploration_command(result["config"], tmp_path / "out")
    assert command[1:4] == ["-m", "fastmdxplora.cli.main", "explore"]
    assert "--simulate-live-telemetry" in command
    assert "--dashboard" not in command
    assert "--analyze-analyses" in command


def test_exploration_environment_preflight_is_workflow_aware() -> None:
    missing = [MissingDependency("PDBFixer", "pdbfixer", "pdbfixer")]
    with patch("fastmdxplora.gui.exploration.missing_dependencies", return_value=missing):
        detail = exploration_environment_error(_payload())

    assert detail is not None
    assert "PDBFixer" in detail
    assert "MDTraj" not in detail

    payload = _payload()
    payload["workflow"]["run_analysis"] = False
    with patch("fastmdxplora.gui.exploration.missing_dependencies", return_value=missing) as probe:
        exploration_environment_error(payload)
    probe.assert_called_once_with(include_analysis=False)


def test_exploration_json_mapping_handles_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("not-json", encoding="utf-8")
    assert _json_mapping(path) == {}


def test_runtime_launches_without_shell(tmp_path: Path) -> None:
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
    )
    fake_process = SimpleNamespace(pid=42, poll=lambda: None, terminate=lambda: None)
    with (
        patch("fastmdxplora.gui.exploration.exploration_environment_error", return_value=None),
        patch("fastmdxplora.gui.exploration.subprocess.Popen", return_value=fake_process) as popen,
    ):
        result = runtime.launch(_payload(), dashboard_url="http://127.0.0.1:8765")
    assert result["launched"] is True
    kwargs = popen.call_args.kwargs
    assert kwargs["shell"] is False
    assert kwargs["env"]["FASTMDX_DASHBOARD_ACTIVE"] == "1"
    assert runtime.data_root().name == "trpcage_test"


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


def test_runtime_refuses_launch_when_simulation_dependencies_are_missing(
    tmp_path: Path,
) -> None:
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
    )
    detail = "Simulation dependencies are unavailable: OpenMM, PDBFixer."
    with (
        patch("fastmdxplora.gui.exploration.exploration_environment_error", return_value=detail),
        patch("fastmdxplora.gui.exploration.subprocess.Popen") as popen,
    ):
        result = runtime.launch(_payload())
    assert result["valid"] is False
    assert result["error"] == detail
    assert result["errors"]["run"] == detail
    popen.assert_not_called()
    assert not (runtime.exploration_root / "trpcage_test").exists()


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


def test_home_server_exposes_exploration_apis(tmp_path: Path) -> None:
    server, url = start_test_server(tmp_path / "workspace", home_mode=True)
    try:
        with urllib.request.urlopen(url + "/") as response:
            html = response.read().decode("utf-8")
        assert "Builder" in html  # the run page, renamed with its nav entry
        # The builder that page carried has been retired; the run page took
        # its place and is what the dashboard now loads.
        assert "/static/run-builder.js" in html
        with urllib.request.urlopen(url + "/api/app-state") as response:
            state = json.load(response)
        assert state["active_run"] is None
        with urllib.request.urlopen(url + "/api/explore/defaults") as response:
            defaults = json.load(response)
        assert defaults["simulation"]["temperature_K"] == 300.0

        with urllib.request.urlopen(url + "/api/status") as response:
            status = json.load(response)
        assert status["status"] == {}
        with urllib.request.urlopen(url + "/api/structure-info") as response:
            structure = json.load(response)
        assert structure["valid"] is False

        request = urllib.request.Request(
            url + "/api/explore/validate",
            data=json.dumps(_payload()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            validated = json.load(response)
        assert validated["valid"] is True
        assert "command" in validated
    finally:
        server.shutdown()
        server.server_close()


def test_home_validation_reports_missing_backend_install_command(tmp_path: Path) -> None:
    server, url = start_test_server(tmp_path / "workspace", home_mode=True)
    try:
        request = urllib.request.Request(
            url + "/api/explore/validate",
            data=json.dumps(_payload()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        missing = [MissingDependency("OpenMM", "openmm.app", "openmm")]
        with patch(
            "fastmdxplora.gui.server.missing_dependencies",
            return_value=missing,
        ):
            with urllib.request.urlopen(request) as response:
                validated = json.load(response)
        assert validated["valid"] is True
        assert "conda install -c conda-forge openmm" in validated["environment_error"]
    finally:
        server.shutdown()
        server.server_close()


def test_remote_dashboard_disables_workflow_control_and_path_leak(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "result.txt").write_text("ok", encoding="utf-8")
    session = start_dashboard_session(output=run, host="0.0.0.0", port=0)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{session.port}/api/explore/start",
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
class TestBuildConfigYaml:
    """The GUI builder can emit a config file instead of launching.

    This is what makes a study designed in the browser usable on a cluster,
    where the GUI itself cannot run.
    """

    @staticmethod
    def _config(*, setup=None, simulation=None, workflow=None, **top):
        from fastmdxplora.gui.exploration import validate_exploration_payload

        payload = {"system": "1L2Y", **top}
        if setup:
            payload["setup"] = setup
        if simulation:
            payload["simulation"] = simulation
        if workflow:
            payload["workflow"] = workflow
        result = validate_exploration_payload(payload)
        assert not result.get("errors"), result.get("errors")
        return result["config"]

    def test_generated_yaml_loads_through_the_config_loader(self, tmp_path: Path) -> None:
        from fastmdxplora.config.loader import load_config_file
        from fastmdxplora.gui.exploration import build_config_yaml

        text = build_config_yaml(self._config(run_name="round trip"), tmp_path / "out")
        target = tmp_path / "study.yml"
        target.write_text(text, encoding="utf-8")

        loaded = load_config_file(target)
        assert loaded["systems"][0]["system"] == "1L2Y"
        assert loaded["systems"][0]["id"] == "round_trip"
        assert loaded["setup"]["ph"] == 7.0
        assert "production_steps" in loaded["simulation"]

    def test_phases_follow_the_workflow_selection(self, tmp_path: Path) -> None:
        import yaml

        from fastmdxplora.gui.exploration import build_config_yaml

        full = yaml.safe_load(build_config_yaml(self._config(), tmp_path))
        assert full["include_phase"] == ["setup", "simulation", "analysis", "report"]

        sim_only = yaml.safe_load(
            build_config_yaml(
                self._config(
                    workflow={"run_analysis": False, "run_report": False}
                ),
                tmp_path,
            )
        )
        assert sim_only["include_phase"] == ["setup", "simulation"]
        assert "analysis" not in sim_only
        assert "report" not in sim_only

    def test_non_default_options_are_written(self, tmp_path: Path) -> None:
        import yaml

        from fastmdxplora.gui.exploration import build_config_yaml

        config = self._config(
            setup={
                "ph": 6.5,
                "forcefield": "amber14",
                "keep_heterogens": True,
                "keep_water": True,
            },
            simulation={"minimize": False},
            workflow={"analyses": ["rmsd", "rg"], "report_slides": False},
        )
        doc = yaml.safe_load(build_config_yaml(config, tmp_path))
        assert doc["setup"]["ph"] == 6.5
        assert doc["setup"]["forcefield"] == "amber14"
        assert doc["setup"]["keep_heterogens"] is True
        assert doc["setup"]["keep_water"] is True
        assert doc["simulation"]["minimize"] is False
        assert doc["analysis"]["include"] == ["rmsd", "rg"]
        assert doc["report"]["slides"] is False

    def test_header_documents_how_to_run_it(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.exploration import build_config_yaml

        text = build_config_yaml(self._config(), tmp_path)
        assert text.startswith("# FastMDXplora study configuration")
        assert "fastmdx explore --config" in text

    def test_run_name_cannot_escape_the_output_root(self, tmp_path: Path) -> None:
        import yaml

        from fastmdxplora.gui.exploration import build_config_yaml

        config = self._config(run_name="../../etc/passwd")
        doc = yaml.safe_load(build_config_yaml(config, tmp_path / "runs"))
        assert ".." not in doc["systems"][0]["id"]


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


def test_the_run_outlives_the_server():
    # Without start_new_session the run sat in the terminal's process
    # group, and Ctrl-C on the server sent SIGINT to the run as well: a
    # day-long simulation died mid-step, not by any decision but because
    # the terminal delivers the signal to the whole group.
    import inspect

    from fastmdxplora.gui import exploration

    source = inspect.getsource(exploration.DashboardRuntime._spawn)
    assert "start_new_session=True" in source


def test_stopping_the_server_says_what_is_still_running():
    import inspect

    from fastmdxplora.gui import server

    source = inspect.getsource(server.serve_dashboard)
    assert "is still running (pid" in source
    assert "kill {proc.pid}" in source


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
