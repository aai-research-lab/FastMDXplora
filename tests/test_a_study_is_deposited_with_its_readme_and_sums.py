"""A study is packed for a data repository: the files ticked, a README
written from its records, and the SHA-256 of every file.

What goes in by what it is (the inputs, the configuration, the trajectory
with the topology it is read with, the final state, the analyses' numbers,
one kind of figure, the report, the records); never the Viewer's scratch,
what `--rerun` set aside, the project bundle or an earlier deposit; the
checkpoint only when asked for.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from fastmdxplora.deposit import SETS, deposit_plan, write_deposit
from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.server import _artifact_records, make_handler


def _write(root: Path, rel: str, text: str = "x") -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture()
def study(tmp_path: Path) -> Path:
    root = tmp_path / "trypsin"
    _write(root, "manifest.json", json.dumps({
        "system": "3PTB_apo.pdb", "version": "2.6.0", "source": {"commit": "abc1234"},
        "environment": {"openmm": "8.6.1", "mdtraj": "1.11.0", "python": "3.11.9"},
        "citation": "Aina, A.; Kwan, D. FastMDAnalysis. J. Comput. Chem. 2026.",
        "doi": "10.1002/jcc.70350"}))
    for rel in ("setup/input.pdb", "setup/solvated.pdb", "setup/system.xml", "setup/state.xml",
                "setup/setup_parameters.json", "resolved_config.yml",
                "simulation/production.dcd", "simulation/trajectory_topology.pdb",
                "simulation/topology.pdb", "simulation/state_final.xml",
                "simulation/state_minimized.xml", "simulation/checkpoint.chk",
                "simulation/energy.csv", "simulation/simulation.log",
                "analysis/rmsd/rmsd.dat", "analysis/rmsd/rmsd.png", "analysis/rmsd/rmsd.svg",
                "analysis/rmsd/options.json", "analysis/analysis_manifest.json",
                "report/report.md", "report/slides.pptx", "report/project_bundle.zip",
                "simulation/live_frames/frame_1.pdb", "simulation/frames.dcd",
                "previous/analysis/rmsd/rmsd.dat", "scenes/pocket.mvsx"):
        _write(root, rel, f"contents of {rel}\n")
    return root.resolve()


def _names(archive: zipfile.ZipFile) -> set[str]:
    return {name.split("/", 1)[1] for name in archive.namelist()}


def test_the_plan_says_what_each_set_holds(study):
    plan = deposit_plan(study, _artifact_records(study))
    sets = {s["key"]: s for s in plan["sets"]}
    assert [s["key"] for s in plan["sets"]] == [key for key, *_ in SETS]
    assert sets["trajectory"]["files"] == 2  # production.dcd and trajectory_topology.pdb
    assert sets["inputs"]["files"] == 5      # setup's structures and system, the solvated topology
    assert sets["state"]["files"] == 2
    assert sets["figures"]["files"] == 1     # SVG only, to begin with
    assert sets["checkpoint"]["on"] is False and sets["checkpoint"]["files"] == 1
    assert sets["saved"]["on"] is False and sets["saved"]["files"] == 1
    assert plan["left_out"]["files"] == 4    # scratch twice, set aside, the bundle
    assert plan["where"].startswith("deposit/3PTB_deposit_") and plan["where"].endswith(".zip")
    png = {s["key"]: s for s in deposit_plan(study, _artifact_records(study), figures="png")["sets"]}
    assert png["figures"]["files"] == 1


def test_the_deposit_carries_the_files_a_readme_and_their_sums(study):
    said = write_deposit(study, _artifact_records(study),
                         sets=[key for key, _, _, on in SETS if on], figures="svg")
    assert said["ok"] and said["path"].startswith("deposit/3PTB_deposit_")
    archive = zipfile.ZipFile(study / said["path"])
    top = {name.split("/", 1)[0] for name in archive.namelist()}
    assert top == {Path(said["path"]).stem}
    names = _names(archive)
    assert {"README.md", "SHA256SUMS", "simulation/production.dcd",
            "simulation/trajectory_topology.pdb", "analysis/rmsd/rmsd.svg",
            "analysis/rmsd/rmsd.dat", "resolved_config.yml", "report/report.md"} <= names
    for never in ("simulation/live_frames/frame_1.pdb", "simulation/frames.dcd",
                  "previous/analysis/rmsd/rmsd.dat", "report/project_bundle.zip",
                  "analysis/rmsd/rmsd.png", "simulation/checkpoint.chk", "scenes/pocket.mvsx"):
        assert never not in names, never
    # Every file's sum, as `sha256sum -c` reads it, and every sum right.
    stem = Path(said["path"]).stem
    lines = archive.read(f"{stem}/SHA256SUMS").decode().splitlines()
    assert len(lines) == len(names) - 1
    for line in lines:
        digest, _, rel = line.partition("  ")
        assert hashlib.sha256(archive.read(f"{stem}/{rel}")).hexdigest() == digest, rel
    readme = archive.read(f"{stem}/README.md").decode()
    assert "A molecular dynamics study of 3PTB_apo.pdb" in readme
    assert "FastMDXplora 2.6.0, commit abc1234, OpenMM 8.6.1, MDTraj 1.11.0, Python 3.11.9." in readme
    assert ('trajectory = md.load("simulation/production.dcd", '
            'top="simulation/trajectory_topology.pdb")') in readme
    assert "| `simulation/production.dcd` |" in readme
    assert "sha256sum -c SHA256SUMS" in readme
    assert "https://doi.org/10.1002/jcc.70350" in readme
    # The deposit is a file of the study, never in the next one.
    again = write_deposit(study, _artifact_records(study), sets=["records"])
    assert again["path"].endswith("-2.zip")
    assert not any(n.startswith("deposit/") for n in _names(zipfile.ZipFile(study / again["path"])))


def test_the_checkpoint_and_png_when_asked(study):
    said = write_deposit(study, _artifact_records(study), sets=["checkpoint", "figures"],
                         figures="png")
    names = _names(zipfile.ZipFile(study / said["path"]))
    assert names == {"README.md", "SHA256SUMS", "simulation/checkpoint.chk",
                     "analysis/rmsd/rmsd.png"}


def test_a_figure_without_a_twin_and_the_report_s_pictures_are_kept(study):
    _write(study, "report/structure_region_highlights.png", "a rendered structure, no SVG")
    _write(study, "analysis/rg/rg.svg", "only the vector figure")
    _write(study, "report/report.md", "# A study\n\n![rmsd](../analysis/rmsd/rmsd.png)\n"
                                      '<img alt="x" src="structure_region_highlights.png">\n')
    names = _names(zipfile.ZipFile(study / write_deposit(
        study, _artifact_records(study), sets=["figures", "report"], figures="svg")["path"]))
    # SVG chosen: a picture with no SVG twin is kept, and the report's pictures with it.
    assert {"analysis/rmsd/rmsd.svg", "analysis/rg/rg.svg", "analysis/rmsd/rmsd.png",
            "report/structure_region_highlights.png", "report/report.md"} <= names
    names = _names(zipfile.ZipFile(study / write_deposit(
        study, _artifact_records(study), sets=["figures"], figures="png")["path"]))
    # PNG chosen: rg has only its SVG, kept; rmsd's SVG is its PNG's twin, left.
    assert "analysis/rg/rg.svg" in names and "analysis/rmsd/rmsd.svg" not in names


def test_a_joined_study_deposits_its_trajectory_once(study):
    _write(study, "joined/joined.json", "{}")
    _write(study, "joined/production.dcd", "the whole trajectory")
    _write(study, "segment-002/simulation/production.dcd", "a piece")
    plan = {s["key"]: s for s in deposit_plan(study, _artifact_records(study))["sets"]}
    assert plan["trajectory"]["files"] == 2 and plan["trajectory"]["on"]
    assert plan["segments"]["files"] == 2 and not plan["segments"]["on"]
    names = _names(zipfile.ZipFile(study / write_deposit(
        study, _artifact_records(study), sets=["trajectory"])["path"]))
    assert names == {"README.md", "SHA256SUMS", "joined/production.dcd",
                     "simulation/trajectory_topology.pdb"}


def test_one_deposit_at_a_time_and_none_while_running(study, monkeypatch):
    import fastmdxplora.deposit as deposit

    assert deposit._WRITING.acquire(blocking=False)
    try:
        said = write_deposit(study, _artifact_records(study), sets=["records"])
        assert not said["ok"] and "being written already" in said["error"]
    finally:
        deposit._WRITING.release()
    _write(study, "simulation/live_status.json", json.dumps({"status": "running"}))
    said = write_deposit(study, _artifact_records(study), sets=["records"])
    assert not said["ok"] and said["error"].startswith("The study is still running")
    assert not (study / "deposit").exists()


def test_a_deposit_that_fails_leaves_nothing_half_written(study, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(zipfile.ZipInfo, "from_file", broken)
    with pytest.raises(RuntimeError):
        write_deposit(study, _artifact_records(study), sets=["records"])
    assert [p.name for p in (study / "deposit").iterdir()] == []
    import fastmdxplora.deposit as deposit

    assert deposit._WRITING.acquire(blocking=False)
    deposit._WRITING.release()


def test_nothing_ticked_writes_nothing(study):
    assert write_deposit(study, _artifact_records(study), sets=[])["ok"] is False
    assert write_deposit(study, _artifact_records(study), sets=["nonsense"])["ok"] is False
    assert not (study / "deposit").exists()


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


def _ask(url: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_the_server_plans_and_writes_it_on_this_computer(study):
    with _serving(study) as url:
        status, body = _ask(url + "/api/files/deposit?figures=svg&sets=trajectory,config")
        plan = json.loads(body)
        assert status == 200 and "| `resolved_config.yml` |" in plan["readme"]
        assert "| `setup/input.pdb` |" not in plan["readme"]
        status, body = _ask(url + "/api/files/deposit", {"sets": ["trajectory"], "figures": "svg"})
        said = json.loads(body)
        assert status == 200 and said["ok"] and said["files"] == 2
        assert said["href"].startswith("/artifacts/deposit/") and said["href"].endswith("?download=1")
        status, body = _ask(url + said["href"].split("?")[0])
        assert status == 200 and zipfile.ZipFile(io.BytesIO(body)).testzip() is None
        page = json.loads(_ask(url + "/api/files-page")[1])
        assert page["can"]["deposit"] and 'data-phase="deposit"' in page["html"]
    with _serving(study, allow_control=False) as url:
        assert _ask(url + "/api/files/deposit")[0] == 403
        assert _ask(url + "/api/files/deposit", {"sets": ["trajectory"]})[0] == 403
        assert "data-deposit" not in json.loads(_ask(url + "/api/files-page")[1])["html"]


def test_the_dialog_writes_it_in_a_browser(study):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(session.url + "#files", wait_until="domcontentloaded")
            page.wait_for_selector("[data-deposit]", timeout=30000)
            page.click("[data-deposit]")
            page.wait_for_function("() => !document.getElementById('files-deposit-go').disabled",
                                   timeout=30000)
            assert page.inner_text("#files-deposit-readme").startswith("# 3PTB deposit")
            # The README scrolls, so it can be reached and scrolled from the keyboard.
            assert page.get_attribute("#files-deposit-readme", "tabindex") == "0"
            ticked = page.eval_on_selector_all("#files-deposit-sets input[data-set]:checked",
                                               "els => els.map(e => e.getAttribute('data-set'))")
            assert ticked == ["inputs", "config", "trajectory", "state", "analysis", "figures",
                              "report", "records"]
            page.uncheck('#files-deposit-sets input[data-set="inputs"]')
            page.wait_for_timeout(800)
            assert "setup/input.pdb" not in page.inner_text("#files-deposit-readme")
            page.click("#files-deposit-go")
            page.wait_for_function(
                "() => document.getElementById('files-deposit-done').textContent.startsWith('Written')",
                timeout=60000)
            browser.close()
    finally:
        session.server.shutdown()
    written = list((study / "deposit").glob("*.zip"))
    assert len(written) == 1
    assert "setup/input.pdb" not in _names(zipfile.ZipFile(written[0]))
