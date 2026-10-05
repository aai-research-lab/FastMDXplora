"""A folder that holds no study is not shown as one.

`fastmdx` typed alone in a repository checkout opened the checkout as the
study: the Overview said it had no live record, as of a run that stopped
before its simulation, and the side panel listed the repository's files as
the run's. It now opens with no study, as `fastmdx gui` does, and a folder
of other things opened any way at all (`--output`, or however else) is said
to hold no study. A folder a run has begun in, or an empty one about to be
a study, is still opened.
"""

from __future__ import annotations

import importlib
import json
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fastmdxplora.gui.browse import holds_no_study


def _checkout(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "README.md").write_text("# a project\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / ".git").mkdir()
    return root


def test_what_holds_no_study(tmp_path):
    checkout = _checkout(tmp_path / "checkout")
    assert holds_no_study(checkout)
    empty = tmp_path / "empty"
    empty.mkdir()
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / ".DS_Store").write_text("", encoding="utf-8")
    assert not holds_no_study(empty) and not holds_no_study(hidden)
    assert not holds_no_study(tmp_path / "absent")
    assert not holds_no_study(checkout / "README.md")
    # A folder of studies holds runs, and the Overview names them.
    runs = _checkout(tmp_path / "runs")
    (runs / "one").mkdir()
    (runs / "one" / "manifest.json").write_text("{}", encoding="utf-8")
    assert not holds_no_study(runs)
    for written in ("manifest.json", "resolved_config.yml", "exploration.log", "setup",
                    "simulation", "stopping.json"):
        begun = _checkout(tmp_path / f"with-{written.replace('.', '-')}")
        target = begun / written
        if "." in written:
            target.write_text("", encoding="utf-8")
        else:
            target.mkdir()
        assert not holds_no_study(begun), written


def test_bare_fastmdx_opens_with_no_study(monkeypatch, tmp_path):
    cli = importlib.import_module("fastmdxplora.cli.main")

    monkeypatch.chdir(_checkout(tmp_path / "checkout"))
    with patch("fastmdxplora.gui.server.serve_dashboard") as serve:
        assert cli._cmd_dashboard_home() == 0
    assert serve.call_args.kwargs["home_mode"] is True


def test_gui_given_such_a_folder_says_so_and_opens_with_none(tmp_path, capsys):
    cli = importlib.import_module("fastmdxplora.cli.main")

    checkout = _checkout(tmp_path / "checkout")
    args = SimpleNamespace(output=str(checkout), host="127.0.0.1", port=8765,
                           no_browser=True, ligand_resname=None,
                           binding_pocket_cutoff_A=None)
    with patch("fastmdxplora.gui.server.serve_dashboard") as serve:
        assert cli._cmd_gui(args) == 0
    assert serve.call_args.kwargs["home_mode"] is True
    assert "holds no FastMDXplora study" in capsys.readouterr().out
    study = tmp_path / "study"
    study.mkdir()
    args.output = str(study)
    with patch("fastmdxplora.gui.server.serve_dashboard") as serve:
        assert cli._cmd_gui(args) == 0
    assert serve.call_args.kwargs["home_mode"] is False


def test_opened_however_it_is_said_to_hold_no_study(tmp_path, monkeypatch):
    pytest.importorskip("mdtraj")
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    checkout = _checkout(tmp_path / "checkout")
    session = start_dashboard_session(output=str(checkout), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        state = json.loads(urllib.request.urlopen(base + "/api/app-state", timeout=30).read())
        files = json.loads(urllib.request.urlopen(base + "/api/files", timeout=30).read())
    finally:
        session.server.shutdown()
    assert state["mode"] == "home" and state["active_run"] is None
    assert Path(state["no_study_in"]) == checkout.resolve()
    assert files == {"artifacts": []}


def test_a_run_begun_in_it_is_still_shown(tmp_path):
    from fastmdxplora.gui.exploration import DashboardRuntime

    checkout = _checkout(tmp_path / "checkout")
    runtime = DashboardRuntime(workspace_root=tmp_path, exploration_root=tmp_path,
                                 active_root=checkout)
    assert runtime.snapshot()["no_study_in"] == str(checkout.resolve())
    assert runtime.data_root().name != checkout.name
    runtime.running_root = checkout.resolve()
    said = runtime.snapshot()
    assert said["no_study_in"] is None and said["active_run"] == str(checkout.resolve())
    assert runtime.data_root() == checkout.resolve()
    # A run this GUI started with no folder of its own, or whose log is
    # written in the folder, is that folder's.
    runtime.running_root = None
    runtime.process = SimpleNamespace(poll=lambda: None, returncode=None, pid=0)
    assert runtime.snapshot()["no_study_in"] is None
    runtime.process = None
    runtime.log_path = checkout.resolve() / "exploration.log"
    assert runtime.snapshot()["no_study_in"] is None


def test_a_folder_that_cannot_be_read_is_not_said_to_hold_none(tmp_path, monkeypatch):
    checkout = _checkout(tmp_path / "checkout")

    def refused(self):
        raise PermissionError("not readable")

    monkeypatch.setattr(Path, "iterdir", refused)
    assert not holds_no_study(checkout)


def test_no_folder_is_named_or_opened_for_no_study(tmp_path, monkeypatch):
    """`fastmdx gui` in such a folder printed "The file .../.fastmdxplora-no-
    current-run does not exist." twice on a Mac: the page named that never
    made folder as the study's, and Open the folder asked `open` for it."""
    pytest.importorskip("mdtraj")
    from fastmdxplora.gui import server
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    asked = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda argv, *a, **k: asked.append(argv))
    checkout = _checkout(tmp_path / "checkout")
    session = start_dashboard_session(output=str(checkout), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        results = json.loads(urllib.request.urlopen(base + "/api/results", timeout=30).read())
        opened = json.loads(urllib.request.urlopen(base + "/api/open-output", timeout=30).read())
    finally:
        session.server.shutdown()
    assert results["output_dir"] == "" and results["run_title"] == ""
    assert results["system"]["output_folder"] == ""
    assert opened == {"opened": False, "path": "", "detail": "No study is open."}
    assert asked == []
    # Nor anything that is not there, from any route.
    assert server._open_local_path(tmp_path / "gone") == (False, f"{tmp_path / 'gone'} does not exist.")
    assert asked == []


def test_the_overview_says_there_is_no_study_here(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    checkout = _checkout(tmp_path / "checkout")
    session = start_dashboard_session(output=str(checkout), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => document.getElementById('live-absent-title')"
                                   ".textContent === 'No study here'")
            body = page.text_content("#live-absent-body")
            offered = page.is_visible("#live-absent-actions")
            # The study menu offers no folder to open.
            folder = page.evaluate("""() => ['sidebar-output-folder', 'open-output']
                .map((id) => document.getElementById(id).hidden)""")
            browser.close()
    finally:
        session.server.shutdown()
    assert body == ("checkout holds no study: nothing in it was written by FastMDXplora. "
                    "Open one under All studies, or start one.")
    assert offered
    assert folder == [True, True]
