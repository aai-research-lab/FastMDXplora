"""The scratch the Viewer and the live view write again is cleared, after asking.

Trypsin's study held 61 MB of it beside a 7.8 MB trajectory: the live
view's 200 snapshots, the frames sent to the Viewer and what it computed
from them. Clearing it is offered on the person's own computer only, and
never takes what a run still going or a study with no trajectory needs.
"""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.files_page import clear_scratch
from fastmdxplora.gui.server import _artifact_records, make_handler


def _write(root: Path, rel: str, text: str = "x" * 100) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _study(root: Path, *, status: str = "completed", trajectory: bool = True) -> Path:
    _write(root, "manifest.json", "{}")
    _write(root, "simulation/live_status.json", json.dumps({"status": status}))
    _write(root, "simulation/live_metrics.csv")
    _write(root, "simulation/live_frame.pdb")
    if trajectory:
        _write(root, "simulation/production.dcd")
    for i in range(3):
        _write(root, f"simulation/live_frames/frame_{i}.pdb")
    _write(root, "simulation/live_frame_history.json")
    _write(root, "simulation/frames.dcd")
    _write(root, "simulation/frames_topology.pdb")
    _write(root, "simulation/contact_map.json")
    _write(root, "viewer_runs/run_1.dcd")
    return root.resolve()


def _left(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_what_is_written_again_goes_and_the_run_s_records_stay(tmp_path):
    root = _study(tmp_path / "study")
    plan = clear_scratch(root, _artifact_records(root), running=False, dry=True)
    assert plan == {"ok": True, "files": 8, "bytes": 800, "snapshots": 4, "kept": []}
    assert len(_left(root)) == 13  # nothing went
    done = clear_scratch(root, _artifact_records(root), running=False)
    assert done["files"] == 8 and done["bytes"] == 800
    assert _left(root) == {"manifest.json", "simulation/live_status.json",
                           "simulation/live_metrics.csv", "simulation/live_frame.pdb",
                           "simulation/production.dcd"}
    assert not (root / "simulation" / "live_frames").exists()
    assert not (root / "viewer_runs").exists()


def test_a_run_still_going_keeps_its_snapshots(tmp_path):
    root = _study(tmp_path / "study", status="running")
    plan = clear_scratch(root, _artifact_records(root), running=False, dry=True)
    assert plan["kept"] == [{"run": "", "why": "it is still running"}]
    # The Viewer's own, written again from the trajectory, still go.
    assert plan["files"] == 4 and plan["snapshots"] == 0


@pytest.mark.parametrize("status", ["failed", "stopped"])
def test_a_run_that_did_not_finish_keeps_its_snapshots(tmp_path, status):
    """Its trajectory may be cut short, and the Viewer plays the snapshots
    where it cannot read the trajectory."""
    root = _study(tmp_path / "study", status=status)
    plan = clear_scratch(root, _artifact_records(root), running=False, dry=True)
    assert plan["kept"][0]["why"].startswith(f"the run was {status}, and its trajectory may be cut short")
    assert plan["snapshots"] == 0


def test_a_run_that_ended_without_saying_so_keeps_its_snapshots(tmp_path):
    root = _study(tmp_path / "study", status="running")
    _write(root, "simulation/live_status.json", json.dumps(
        {"status": "running", "last_update_timestamp": "2020-01-01T00:00:00+00:00"}))
    plan = clear_scratch(root, _artifact_records(root), running=False, dry=True)
    assert plan["kept"][0]["why"].startswith("the run was interrupted")


def test_a_segment_still_going_keeps_its_snapshots(tmp_path):
    root = _study(tmp_path / "study")
    _write(root, "segment-002/simulation/live_status.json", json.dumps({"status": "running"}))
    _write(root, "segment-002/simulation/live_frames/frame_9.pdb")
    plan = clear_scratch(root.resolve(), _artifact_records(root.resolve()), running=False, dry=True)
    assert {"run": "segment-002", "why": "it is still running"} in plan["kept"]
    clear_scratch(root.resolve(), _artifact_records(root.resolve()), running=False)
    assert (root / "segment-002" / "simulation" / "live_frames" / "frame_9.pdb").exists()
    assert not (root / "simulation" / "live_frames").exists()


def test_a_link_is_removed_as_a_link(tmp_path):
    root = _study(tmp_path / "study")
    (root / "simulation" / "frames.dcd").unlink()
    (root / "simulation" / "frames.dcd").symlink_to(root / "simulation" / "production.dcd")
    clear_scratch(root, _artifact_records(root), running=False)
    assert (root / "simulation" / "production.dcd").read_text() == "x" * 100
    assert not (root / "simulation" / "frames.dcd").is_symlink()


def test_a_study_with_no_trajectory_keeps_the_only_frames_it_has(tmp_path):
    root = _study(tmp_path / "study", trajectory=False)
    plan = clear_scratch(root, _artifact_records(root), running=False, dry=True)
    assert plan["kept"][0]["why"].startswith("it has no trajectory")


def test_nothing_goes_while_the_study_runs(tmp_path):
    root = _study(tmp_path / "study")
    said = clear_scratch(root, _artifact_records(root), running=True)
    assert not said["ok"] and len(_left(root)) == 13


def test_each_run_of_a_study_of_several_is_judged_alone(tmp_path):
    root = tmp_path / "study"
    _study(root / "runs" / "done")
    _study(root / "runs" / "going", status="production")
    _write(root, "batch_manifest.json", json.dumps({"planned": [{"run_id": "done"},
                                                                 {"run_id": "going"}]}))
    root = root.resolve()
    clear_scratch(root, _artifact_records(root), running=False)
    left = _left(root)
    assert "runs/done/simulation/frames.dcd" not in left
    assert "runs/going/simulation/live_frames/frame_0.pdb" in left


@contextlib.contextmanager
def _serving(study: Path, *, allow_control: bool = True):
    runtime = DashboardRuntime(workspace_root=study, exploration_root=study.parent,
                               active_root=study)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                make_handler(study, runtime=runtime, allow_control=allow_control))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post(url: str, body: dict):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_it_is_asked_of_the_server_on_this_computer_only(tmp_path):
    root = _study(tmp_path / "study")
    with _serving(root, allow_control=False) as url:
        assert _post(url + "/api/files/clear-scratch", {})[0] == 403
    assert len(_left(root)) == 13
    with _serving(root) as url:
        status, plan = _post(url + "/api/files/clear-scratch", {"dry": True})
        assert status == 200 and plan["files"] == 8
        status, done = _post(url + "/api/files/clear-scratch", {})
        assert status == 200 and done["files"] == 8
    assert "simulation/frames.dcd" not in _left(root)


def test_the_page_asks_first_in_a_browser(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(session.url + "#files", wait_until="domcontentloaded")
            page.wait_for_selector('[data-phase="scratch"]', timeout=30000)
            page.click('.files-usage [data-clear]')
            page.wait_for_function("() => !document.getElementById('files-clear-go').disabled",
                                   timeout=20000)
            said = page.inner_text("#files-clear-said")
            assert said.startswith("8 files, 800 B: ") and "4 of the live view's snapshots" in said
            assert (root / "simulation" / "frames.dcd").exists()  # asked, not done
            page.click("#files-clear-go")
            page.wait_for_selector('[data-phase="scratch"]', state="detached", timeout=20000)
            assert page.eval_on_selector("#files-clear-dialog", "e => e.hidden")
            browser.close()
    finally:
        session.server.shutdown()
    left = _left(root)
    assert "simulation/live_frames/frame_0.pdb" not in left
