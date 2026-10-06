"""The demo study, trypsin with benzamidine, opens before any study is run.

A first look needed a study run first. The demo is a finished study made on
a GPU from `demo/3ptb.yml` and packaged by `scripts/make_demo.py`; it is
copied into a folder the person chooses (`fastmdx gui --demo DIR`, or Open
the demo study on an empty page) with the paths its records hold made the
copy's, never over anything there.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

ROOT = Path(__file__).resolve().parent.parent


def _finished(root: Path) -> Path:
    study = _write_study(root)
    (study / "manifest.json").write_text(json.dumps({"phases": [
        {"name": phase, "status": "ok"} for phase in ("setup", "simulation", "analysis", "report")]}))
    (study / "analysis").mkdir()
    (study / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "trajectory_input": str(study / "simulation" / "production.dcd"),
        "resolved": {"trajectory": str(study / "simulation" / "production.dcd"),
                     "elsewhere": "/somewhere/else.dcd"}}))
    (study / "setup" / "system.xml").write_text("<System/>")
    (study / "simulation" / "checkpoint.chk").write_bytes(b"x" * 10)
    return study


def _packaged(tmp_path: Path) -> Path:
    out = tmp_path / "packaged"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_demo.py"),
                    str(_finished(tmp_path / "made")), "--out", str(out)],
                   check=True, capture_output=True, text=True)
    return out


def test_the_config_it_is_made_from_is_a_study() -> None:
    import yaml

    from fastmdxplora.config.loader import validate_config
    from fastmdxplora.demo import CONFIG

    config = yaml.safe_load(CONFIG.read_text())
    validate_config(config)
    sim = config["simulation"]
    frames = sim["duration_ns"] * 1e6 / sim["timestep_fs"] / sim["trajectory_interval_steps"]
    assert frames == 100 and sim["save_selection"] == "not water"


def test_packaged_with_what_the_gui_shows(tmp_path) -> None:
    out = _packaged(tmp_path)
    record = json.loads((out / "demo.json").read_text())
    from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES

    assert record["frames"] == FRAMES and record["made_in"] == str((tmp_path / "made").resolve())
    assert (out / "simulation" / "production.dcd").is_file()
    assert not (out / "setup" / "system.xml").exists()
    assert not (out / "simulation" / "checkpoint.chk").exists()
    assert "setup/system.xml" in record["left_out"]
    # The system as simulated, which the Viewer renders first and reads the
    # ligand from: prepared.pdb is the protein alone.
    assert (out / "setup" / "topology.pdb").is_file()


def test_a_figure_too_large_stops_it_rather_than_leaving_it_out(tmp_path) -> None:
    study = _finished(tmp_path / "made")
    (study / "report").mkdir()
    (study / "report" / "report.html").write_bytes(b"x" * 3000)
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_demo.py"), str(study),
                           "--out", str(tmp_path / "out"), "--most-kb", "1"],
                          capture_output=True, text=True)
    assert done.returncode != 0 and "report/report.html" in done.stderr
    assert not (tmp_path / "out").exists()


def test_an_unfinished_study_is_refused(tmp_path) -> None:
    study = _write_study(tmp_path / "unfinished")
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_demo.py"), str(study),
                           "--out", str(tmp_path / "out")], capture_output=True, text=True)
    assert done.returncode != 0 and "Not a finished study" in done.stderr


def test_copied_with_its_paths_made_the_copy_s(tmp_path) -> None:
    from fastmdxplora.demo import DEMO_NAME, copy_demo

    out = _packaged(tmp_path)
    first = copy_demo(tmp_path / "mine", source=out)
    second = copy_demo(tmp_path / "mine", source=out)
    assert first.name == DEMO_NAME and second.name == DEMO_NAME + "-2"
    said = json.loads((first / "analysis" / "analysis_manifest.json").read_text())
    assert said["trajectory_input"] == str(first.resolve() / "simulation" / "production.dcd")
    assert said["resolved"]["elsewhere"] == "/somewhere/else.dcd"
    # Its own record still says where it was made.
    assert json.loads((first / "demo.json").read_text())["made_in"] == str(
        (tmp_path / "made").resolve())
    from fastmdxplora.gui.series import _json, _of_the_played_trajectory

    assert _of_the_played_trajectory(first, _json(first / "analysis" / "analysis_manifest.json"))


def test_without_a_demo_installed_it_says_so(tmp_path, monkeypatch) -> None:
    import fastmdxplora.demo as demo

    monkeypatch.setattr(demo, "_PACKAGED", tmp_path / "none")
    assert demo.packaged() is None
    with pytest.raises(demo.DemoMissing, match="carries no demo study") as said:
        demo.copy_demo(tmp_path / "mine")
    assert said.value.refusal.code == "environment.demo.absent"
    from fastmdxplora.cli.main import main

    assert main(["gui", "--demo", str(tmp_path / "mine"), "--no-browser"]) == 2
    assert main(["gui", "--demo", "--output", str(tmp_path)]) == 2


def test_the_gui_opens_it(tmp_path, monkeypatch) -> None:
    import fastmdxplora.demo as demo
    from fastmdxplora.gui.server import start_dashboard_session

    out = _packaged(tmp_path)
    monkeypatch.setattr(demo, "_PACKAGED", out)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = start_dashboard_session(output=str(workspace), host="127.0.0.1", port=0)
    try:
        state = json.loads(urllib.request.urlopen(session.url + "/api/app-state", timeout=30).read())
        request = urllib.request.Request(session.url + "/api/demo", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
        opened = json.loads(urllib.request.urlopen(request, timeout=60).read())
    finally:
        session.server.shutdown()
    assert state["demo_available"] is True
    assert opened["ok"], opened
    # Where the GUI puts new studies.
    assert Path(opened["folder"]).parent == Path(state["exploration_root"])
    assert opened["state"]["active_run"] == opened["folder"]


def test_a_service_s_gui_does_not_copy_it() -> None:
    """A service's workspace is the person's, and each copy is another;
    the demo is for a GUI on one's own computer."""
    from types import SimpleNamespace

    from fastmdxplora.gui.exploration import DashboardRuntime

    said = DashboardRuntime.open_the_demo(SimpleNamespace(hosting=object(), snapshot=dict))
    assert said["ok"] is False and "your own computer" in said["error"]
