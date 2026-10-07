"""The Files page is the study in the order it ran, the files most come for first.

It was five cards of every file, grouped by kind (a figure here, its data
there), two across: 9,700 pixels for a study of 435 files, 1,780 buttons
and links, the file that loads the trajectory unlabelled in the folded
record, and a summary card that said "Figures 19" over 86 listed.
"""

from __future__ import annotations

import contextlib
import gzip
import http.client
import io
import json
import re
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.files_page import FOLDED, Links, files_model, render, render_folders
from fastmdxplora.gui.server import _artifact_records, make_handler

_PDB_SAVED = "".join(
    f"ATOM  {i:5d}  CA  ALA A{i:4d}       0.000   0.000   0.000  1.00  0.00           C\n"
    for i in range(1, 4)) + "HETATM    4 CA    CA A 300       0.000   0.000   0.000  1.00  0.00          CA\n"
_PDB_SOLVATED = _PDB_SAVED + "".join(
    f"HETATM{i:5d}  O   HOH W{i:4d}       0.000   0.000   0.000  1.00  0.00           O\n"
    for i in range(5, 9))


def _write(root: Path, rel: str, text: str = "x") -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _study(root: Path) -> Path:
    for rel in ("setup/input.pdb", "setup/prepared.pdb", "setup/system.xml",
                "simulation/production.dcd", "simulation/state_final.xml",
                "simulation/energy.csv", "simulation/live_status.json",
                "analysis/rmsd/rmsd.dat", "analysis/rmsd/rmsd.png", "analysis/rmsd/rmsd.svg",
                "analysis/rmsd/options.json", "analysis/rg/rg.dat", "analysis/rg/rg.png",
                "report/report.md", "report/project_bundle.zip", "resolved_config.yml",
                "previous/analysis/rmsd/rmsd.dat", "viewer_views.json"):
        _write(root, rel)
    _write(root, "manifest.json", "{}")
    _write(root, "setup/solvated.pdb", _PDB_SOLVATED)
    _write(root, "simulation/topology.pdb", _PDB_SOLVATED)
    _write(root, "simulation/trajectory_topology.pdb", _PDB_SAVED)
    _write(root, "simulation/simulation_parameters.json",
           json.dumps({"n_production_frames": 200, "duration_ns_actual": 0.1}))
    for i in range(12):
        _write(root, f"simulation/live_frames/frame_{i:06d}_production_{i:012d}.pdb")
    _write(root, "simulation/frames.dcd")
    _write(root, "analysis/analysis_manifest.json", json.dumps({"results": {
        "rmsd": {"status": "ok", "figure_path": f"{root.name}/analysis/rmsd/rmsd.png"},
        "rg": {"status": "failed", "message": "rg: no frames"}}}))
    _write(root, "report/not_produced.json", json.dumps([
        {"artifact": "report.pdf", "reason": "Rendering the report as a PDF needs WeasyPrint. More."}]))
    return root


@pytest.fixture()
def study(tmp_path: Path) -> Path:
    return _study(tmp_path / "trypsin")


def _model(study: Path, **kw):
    return files_model(study, _artifact_records(study), **kw)


def test_each_file_is_placed_in_the_phase_that_wrote_it(study):
    phases = {f["path"]: f["phase"] for f in _model(study)["files"]}
    assert phases["setup/input.pdb"] == "setup"
    assert phases["simulation/production.dcd"] == "simulation"
    assert phases["analysis/rmsd/rmsd.dat"] == "analysis"
    assert phases["previous/analysis/rmsd/rmsd.dat"] == "previous"
    assert phases["simulation/live_frames/frame_000000_production_000000000000.pdb"] == "scratch"
    assert phases["viewer_views.json"] == "saved"
    assert phases["manifest.json"] == "record"


def test_the_trajectory_comes_first_with_the_topology_it_is_read_with(study):
    keys = {tile["key"]: tile for tile in _model(study)["keys"]}
    trajectory = keys["trajectory"]
    assert trajectory["paths"] == ["simulation/production.dcd",
                                   "simulation/trajectory_topology.pdb"]
    assert trajectory["what"] == "200 frames over 0.1 ns · one every 0.5 ps · 4 atoms, no water"
    assert trajectory["load"] == ('md.load("simulation/production.dcd", '
                                  'top="simulation/trajectory_topology.pdb")')
    assert keys["system"]["what"] == "8 atoms, solvated, with the force field as OpenMM built it"
    assert keys["system"]["paths"] == ["setup/solvated.pdb", "setup/system.xml"]
    assert keys["report"]["what"].endswith(
        "no PDF: Rendering the report as a PDF needs WeasyPrint")
    assert [tile["key"] for tile in _model(study)["keys"]] == [
        "trajectory", "report", "bundle", "system", "state", "config"]


def test_each_analysis_is_one_row_under_what_it_studies(study):
    analyses = {a["folder"]: a for a in _model(study, values={"rmsd": "0.1 ± 0.01 nm"})["analyses"]}
    rmsd = analyses["rmsd"]
    assert rmsd["title"] == "RMSD" and rmsd["theme"] == "Structure and stability"
    assert sorted(rmsd["files"]) == ["analysis/rmsd/options.json", "analysis/rmsd/rmsd.dat",
                                     "analysis/rmsd/rmsd.png", "analysis/rmsd/rmsd.svg"]
    # The figure its record names; the value only as recorded.
    assert rmsd["figure"] == "analysis/rmsd/rmsd.png"
    assert rmsd["value"] == "0.1 ± 0.01 nm"
    assert analyses["rg"]["status"] == "failed" and analyses["rg"]["message"] == "rg: no frames"
    assert analyses["rg"]["value"] == ""


def test_the_filters_count_what_they_show(study):
    model = _model(study)
    assert sum(model["counts"].values()) == len(model["files"])
    assert model["counts"]["trajectory"] == 2  # production.dcd and the Viewer's frames.dcd
    assert model["counts"]["figure"] == 3


def test_the_page_reads_in_the_order_the_study_ran(study):
    html = render(_model(study), Links())
    order = [html.index(f'data-phase="{p}"') for p in
             ("setup", "simulation", "analysis", "report", "saved", "record", "previous", "scratch")]
    assert order == sorted(order)
    assert html.index("What you came for") < html.index('data-phase="setup"')
    # What is kept beside the phases is folded.
    for phase in FOLDED:
        section = html[html.index(f'data-phase="{phase}"'):]
        assert 'aria-expanded="false"' in section[:section.index("</button>")]
    # The trajectory says what reads it, and the whole system says it does not.
    assert "read with trajectory_topology.pdb" in html
    assert "not the trajectory&#x27;s" in html or "not the trajectory's" in html


def test_the_live_view_s_snapshots_are_one_row(study):
    html = render(_model(study), Links())
    scratch = html[html.index('data-phase="scratch"'):]
    assert "Live view snapshots" in scratch
    assert scratch.count('class="files-arow files-group"') == 1


def test_a_row_says_what_a_file_is_above_where_it_is(study):
    html = render(_model(study), Links())
    row = html[html.index('data-path="setup/input.pdb"'):]
    row = row[:row.index("</div></div>") + 12]
    assert row.index("Deposited structure, as given") < row.index("setup/input.pdb</div>")


def test_the_path_on_this_computer_is_given_only_when_it_is_known(study):
    records = _artifact_records(study)
    assert "data-where" not in render(files_model(study, records), Links())
    for record in records:
        record["absolute_path"] = str(study / record["path"])
    html = render(files_model(study, records), Links())
    assert f'data-where="{study / "setup" / "input.pdb"}"' in html


def test_the_folder_view_lists_every_folder(study):
    html = render_folders(_model(study), Links())
    assert html.index("The study&#x27;s folder") < html.index("analysis/rmsd/")
    assert len(re.findall(r'class="files-row[" ]', html)) == len(_model(study)["files"])


def test_what_the_page_offers_follows_what_the_server_can_do(study):
    html = render(_model(study), Links())
    assert "/api/files/zip" not in html and "data-deposit" not in html
    html = render(_model(study), Links(can={"zip": True}))
    assert "Download the pair" in html and "/api/files/zip?name=trajectory" in html


def test_a_study_of_several_runs_offers_each(tmp_path: Path):
    root = tmp_path / "study"
    _write(root, "batch_manifest.json", json.dumps({"planned": [
        {"run_id": "r1", "sweep_values": {"seed": 1}}, {"run_id": "r2", "sweep_values": {"seed": 2}}]}))
    for run in ("r1", "r2"):
        _write(root, f"runs/{run}/simulation/production.dcd")
        _write(root, f"runs/{run}/simulation/trajectory_topology.pdb", _PDB_SAVED)
        _write(root, f"runs/{run}/analysis/rmsd/rmsd.dat")
    model = files_model(root, _artifact_records(root))
    assert model["runs"] == [{"id": "r1", "label": "seed 1"}, {"id": "r2", "label": "seed 2"}]
    assert [(t["key"], t["run"]) for t in model["keys"]] == [("trajectory", "r1"), ("trajectory", "r2")]
    assert [(a["run"], a["folder"]) for a in model["analyses"]] == [("r1", "rmsd"), ("r2", "rmsd")]
    html = render(model, Links())
    assert '<option value="r2">seed 2</option>' in html
    assert 'data-run="r2"' in html


# ---------------------------------------------------------------------------
# Served
# ---------------------------------------------------------------------------

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


def _get(url: str, headers: dict | None = None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_the_page_is_served_rendered_and_compressed(study):
    with _serving(study.resolve()) as url:
        status, headers, body = _get(url + "/api/files-page", {"Accept-Encoding": "gzip"})
        assert status == 200 and headers.get("Content-Encoding") == "gzip"
        said = json.loads(gzip.decompress(body))
        assert said["ok"] and said["view"] == "phases" and said["can"] == {"zip": True}
        assert "What you came for" in said["html"]
        assert f'data-where="{study.resolve() / "setup" / "input.pdb"}"' in said["html"]
        folders = json.loads(_get(url + "/api/files-page?view=folders")[2])
        assert folders["view"] == "folders" and 'data-body="folders"' in folders["html"]


def test_files_are_zipped_as_they_are_sent(study):
    with _serving(study.resolve()) as url:
        status, headers, body = _get(
            url + "/api/files/zip?name=trajectory&path=simulation/production.dcd"
                  "&path=simulation/trajectory_topology.pdb")
        assert status == 200
        assert headers["Content-Disposition"] == 'attachment; filename="trajectory.zip"'
        archive = zipfile.ZipFile(io.BytesIO(body))
        assert archive.namelist() == ["simulation/production.dcd",
                                      "simulation/trajectory_topology.pdb"]
        assert archive.read("simulation/trajectory_topology.pdb").decode() == _PDB_SAVED
        assert _get(url + "/api/files/zip?path=../outside.txt")[0] == 403
        assert _get(url + "/api/files/zip?path=simulation/nothing.dcd")[0] == 404
        assert _get(url + "/api/files/zip")[0] == 404


def test_a_zip_names_no_path_in_its_refusal_and_reads_a_name_once(study):
    _write(study, "analysis/rmsd/a%41.dat", "percent")
    with _serving(study.resolve()) as url:
        status, headers, _ = _get(url + "/api/files/zip?path=%0d%0aX-Injected:%20yes")
        assert status == 404 and "X-Injected" not in headers
        status, _, body = _get(url + "/api/files/zip?path=analysis/rmsd/a%2541.dat")
        assert status == 200
        assert zipfile.ZipFile(io.BytesIO(body)).read("analysis/rmsd/a%41.dat") == b"percent"


def test_a_zip_that_fails_partway_is_not_a_finished_download(study, monkeypatch):
    import fastmdxplora.gui.files_page as files_page

    def halfway(out, entries):
        out.write(b"PK\x03\x04 part of a zip")
        out.flush()
        raise OSError("the disk went away")

    monkeypatch.setattr(files_page, "zip_entries", halfway)
    with _serving(study.resolve()) as url:
        with pytest.raises(Exception) as raised:
            _get(url + "/api/files/zip?path=setup/input.pdb")
    assert isinstance(raised.value, (ConnectionError, urllib.error.URLError, OSError,
                                     http.client.HTTPException))


def test_beyond_loopback_the_page_offers_no_zip_and_the_zip_is_refused(study):
    with _serving(study.resolve(), allow_control=False) as url:
        said = json.loads(_get(url + "/api/files-page")[2])
        assert said["can"] == {"zip": False}
        assert "/api/files/zip" not in said["html"] and "data-where" not in said["html"]
        assert _get(url + "/api/files/zip?path=setup/input.pdb")[0] == 403


# ---------------------------------------------------------------------------
# In a browser
# ---------------------------------------------------------------------------

def test_the_page_is_found_filtered_sorted_and_folded_in_a_browser(study):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    shown = "els => els.filter(e => e.offsetParent !== null).length"
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(session.url + "#files", wait_until="domcontentloaded")
            page.wait_for_selector(".files-toolbar", timeout=30000)
            # Folded: the record, what was set aside, the scratch.
            assert page.eval_on_selector('[data-phase="scratch"] .files-fold',
                                         "e => e.getAttribute('aria-expanded')") == "false"
            page.fill("[data-find]", "rmsd")
            page.wait_for_timeout(100)
            paths = page.eval_on_selector_all(
                ".files-row", "els => els.filter(e => e.offsetParent !== null)"
                              ".map(e => e.getAttribute('data-path'))")
            assert paths and all("rmsd" in p for p in paths)
            assert "previous/analysis/rmsd/rmsd.dat" in paths  # found where it is folded
            assert page.eval_on_selector(".files-keys", "e => e.hidden")
            page.fill("[data-find]", "")
            page.click('.files-filter[data-filter="figure"]')
            page.wait_for_timeout(100)
            kinds = page.eval_on_selector_all(
                ".files-row", "els => els.filter(e => e.offsetParent !== null)"
                              ".map(e => e.getAttribute('data-filter'))")
            assert kinds and set(kinds) == {"figure"}
            page.click('.files-filter[data-filter=""]')
            # A fold opened stays open when the page is rendered again.
            page.click('[data-phase="record"] .files-fold')
            page.evaluate("window.FastMDXFiles.refresh()")
            page.wait_for_timeout(1500)
            assert page.eval_on_selector('[data-phase="record"] .files-fold',
                                         "e => e.getAttribute('aria-expanded')") == "true"
            page.select_option("[data-sort]", "name")
            names = page.eval_on_selector_all(
                '[data-phase="setup"] > .files-rows > .files-row .files-name',
                "els => els.map(e => e.textContent.toLowerCase())")
            assert names == sorted(names)
            page.click('.files-arow[data-key="/rmsd"] .files-expand')
            assert page.eval_on_selector_all('.files-arow[data-key="/rmsd"] .files-row', shown) == 4
            # Nothing runs out of the page, wide or on a phone.
            for width in (1400, 390):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(200)
                assert page.evaluate("document.documentElement.scrollWidth") <= width
            browser.close()
    finally:
        session.server.shutdown()


def test_a_run_is_chosen_in_a_browser(tmp_path: Path):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path / "study"
    _write(root, "batch_manifest.json", json.dumps({"planned": [
        {"run_id": "r1", "sweep_values": {"seed": 1}}, {"run_id": "r2", "sweep_values": {"seed": 2}}]}))
    for run in ("r1", "r2"):
        _write(root, f"runs/{run}/simulation/production.dcd")
        _write(root, f"runs/{run}/simulation/trajectory_topology.pdb", _PDB_SAVED)
        _write(root, f"runs/{run}/analysis/rmsd/rmsd.dat")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    shown = ("els => els.filter(e => e.offsetParent !== null)"
             ".map(e => e.getAttribute('data-path') || e.getAttribute('data-key'))")
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(session.url + "#files", wait_until="domcontentloaded")
            page.wait_for_selector("[data-run-pick]", timeout=30000)
            # Every run: the first run's key files, each run's rows.
            assert page.eval_on_selector_all(".files-tile", shown) == ["trajectory"]
            assert page.eval_on_selector_all(".files-tile", "els => els.filter(e => e.offsetParent"
                                             " !== null).map(e => e.dataset.run)") == ["r1"]
            page.select_option("[data-run-pick]", "r2")
            page.wait_for_timeout(100)
            assert page.eval_on_selector_all(".files-tile", "els => els.filter(e => e.offsetParent"
                                             " !== null).map(e => e.dataset.run)") == ["r2"]
            rows = page.eval_on_selector_all('[data-phase="simulation"] .files-row', shown)
            assert rows and all(p.startswith("runs/r2/") for p in rows)
            assert page.inner_text("[data-usage-total]").endswith("in this run")
            browser.close()
    finally:
        session.server.shutdown()
