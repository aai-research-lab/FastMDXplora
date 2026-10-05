"""A study of replicas coloured by their mean, with the spread between them.

A study of several runs offered nothing under "Coloured by": each run's
per-residue results were inside the run, and the study's root has none.
Replicas are now coloured by the mean of the runs of the run played's
atoms, and a residue's standard error across them and each run's own value
are in the Selection tab; runs that differ by more than their seed are not
averaged, and the run played's own values are said to be its.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")


def _analysed(root: Path) -> Path:
    from fastmdxplora.analysis import AnalysisOrchestrator
    from tests.test_the_runs_of_a_study_play_together import _replicas

    study = _replicas(root)
    for run in sorted((study / "runs").iterdir()):
        simulation = run / "simulation"
        AnalysisOrchestrator(str(simulation / "production.dcd"),
                             str(simulation / "trajectory_topology.pdb"),
                             output_dir=str(run / "analysis")).run(include=["rmsf"])
    return study


@pytest.fixture(scope="module")
def replicas(tmp_path_factory) -> Path:
    return _analysed(tmp_path_factory.mktemp("mean") / "study")


def _own(run: Path) -> dict[tuple, float]:
    from fastmdxplora.gui.by_residue import values_by_residue

    found = values_by_residue(run)["properties"]
    rmsf = next(entry for entry in found if entry["key"] == "rmsf")
    return {(row[0], row[1], row[2]): row[3] for row in rmsf["values"]}


def test_the_replicas_mean_with_its_error(replicas):
    from fastmdxplora.gui.by_residue import values_by_residue

    found = values_by_residue(replicas)["properties"]
    assert [entry["key"] for entry in found] == ["rmsf"]
    rmsf = found[0]
    # The fourth run has other atoms (no ligand): it is not a replica of
    # the run played, and its values are not in the mean.
    assert rmsf["label"] == "RMSF, mean of 3 runs"
    assert rmsf["runs"] == ["random_seed 1", "random_seed 2", "random_seed 3"]
    assert rmsf["source"] == "runs/*/analysis/rmsf"
    assert "The mean over 3 replicas (random_seed 1, random_seed 2, random_seed 3)" in (
        rmsf["about"])
    own = [_own(replicas / "runs" / f"s1__random-seed-{i}") for i in (1, 2, 3)]
    assert len(rmsf["values"]) == len(own[0]) > 200
    for chain, number, code, mean, error, each in rmsf["values"]:
        said = [values[(chain, number, code)] for values in own]
        assert each == pytest.approx(said)
        assert mean == pytest.approx(np.mean(said))
        assert error == pytest.approx(np.std(said, ddof=1) / np.sqrt(3))
    assert rmsf["low"] == 0.0
    assert rmsf["high"] == pytest.approx(max(row[3] for row in rmsf["values"]))


def test_runs_that_are_not_replicas_are_not_averaged(replicas, tmp_path):
    from fastmdxplora.gui.by_residue import values_by_residue

    sweep = tmp_path / "sweep"
    shutil.copytree(replicas, sweep)
    manifest = json.loads((sweep / "batch_manifest.json").read_text())
    text = json.dumps(manifest).replace("simulation.random_seed", "simulation.temperature_K")
    (sweep / "batch_manifest.json").write_text(text)
    rmsf = values_by_residue(sweep)["properties"][0]
    assert rmsf["label"] == "RMSF"
    assert rmsf["source"] == "runs/s1__random-seed-1/analysis/rmsf"
    assert rmsf["about"].endswith("Of temperature_K 1, the run played: the runs differ by "
                                  "more than their seed, so their values are not averaged.")
    assert {(r[0], r[1], r[2]): r[3] for r in rmsf["values"]} == _own(
        sweep / "runs" / "s1__random-seed-1")
    # A result only one replica has is that run's, and said to be.
    only = tmp_path / "only"
    shutil.copytree(replicas, only)
    for run in ("s1__random-seed-2", "s1__random-seed-3"):
        shutil.rmtree(only / "runs" / run / "analysis")
    rmsf = values_by_residue(only)["properties"][0]
    assert rmsf["label"] == "RMSF"
    assert rmsf["about"].endswith("Of random_seed 1, the run played: no other run has this "
                                  "result yet.")
    # No run analysed: nothing to colour by.
    shutil.rmtree(only / "runs" / "s1__random-seed-1" / "analysis")
    assert values_by_residue(only) == {"properties": []}


def test_a_contact_a_run_never_made_counts_as_none(replicas):
    from fastmdxplora.gui.by_residue import _mean_of

    run = {"run_id": "a"}, {"run_id": "b"}
    entries = [(run[0], {"key": "pl_contacts", "analysis": "pl_contacts", "label": "Contact",
                         "about": "Contact.", "source": "analysis/pl_contacts", "low": 0.0,
                         "high": 1.0, "absent": 0.0,
                         "values": [[None, 10, "", 0.5], [None, 11, "", 1.0]]}),
               (run[1], {"key": "pl_contacts", "analysis": "pl_contacts", "label": "Contact",
                         "about": "Contact.", "source": "analysis/pl_contacts", "low": 0.0,
                         "high": 1.0, "absent": 0.0, "values": [[None, 10, "", 0.3]]})]
    said = _mean_of("pl_contacts", entries, {"a": "one", "b": "two"})
    assert said["low"] == 0.0 and said["high"] == 1.0
    rows = {row[1]: row for row in said["values"]}
    assert rows[10][3] == pytest.approx(0.4) and rows[10][5] == [0.5, 0.3]
    assert rows[11][3] == pytest.approx(0.5) and rows[11][5] == [1.0, 0.0]


def test_the_selection_tab_gives_the_spread(replicas):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(replicas), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => document.getElementById('viewer-color-results')")
            offered = page.eval_on_selector_all(
                "#viewer-color-results option", "o => o.map((x) => [x.value, x.textContent])")
            page.select_option("#viewer-color", "result:rmsf")
            page.wait_for_function("() => !document.getElementById('viewer-legend').hidden")
            legend = page.inner_text("#viewer-legend").splitlines()[0]
            # The structure can be loading again for a moment after it was
            # first rendered (its atoms then not found): the atom is looked
            # for until it is there, and described in the same turn.
            page.evaluate("""async () => { const v = window.FastMDXMoleculeViewer;
                const t0 = performance.now();
                let atom = v.atoms({resi: 60, atom: 'CA'})[0];
                while (!atom && performance.now() - t0 < 60000) {
                  await new Promise((done) => setTimeout(done, 50));
                  atom = v.atoms({resi: 60, atom: 'CA'})[0];
                }
                v.byResidue.describe(atom); }""")
            rows = dict(page.eval_on_selector_all(
                "#selection-tab-tbody tr",
                "rows => rows.map((r) => [r.cells[0].textContent, r.cells[1].textContent])"))
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert offered == [["result:rmsf", "RMSF, mean of 3 runs"]]
    assert legend == "RMSF, mean of 3 runs (nm)"
    from fastmdxplora.gui.by_residue import values_by_residue

    row = next(r for r in values_by_residue(replicas)["properties"][0]["values"] if r[1] == 60)
    shown = rows["RMSF, mean of 3 runs"]
    assert "±" in shown and shown.endswith(" nm")
    assert rows["Each run"].startswith("random_seed 1: ")
    assert rows["Each run"].count(";") == 2
    assert len(row[5]) == 3


def test_a_colour_chosen_before_the_runs_arrive_is_kept(replicas):
    """The runs played together come with the frames, and "Run" is chosen
    for the colour the first time they are offered. A colour the person
    chose before they came is theirs: CI's loaded runner chose the mean
    RMSF first, and the runs arriving after took it back to "Run"."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(replicas), host="127.0.0.1", port=0)
    held: list = []
    released: list = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # The frames, and with them the runs, kept back until the
            # colour is chosen.
            page.route("**/api/frames-info*",
                       lambda route: route.continue_() if released else held.append(route))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(
                "() => document.querySelector('#viewer-color-results option')")
            page.select_option("#viewer-color", "result:rmsf")
            page.wait_for_function(f"() => {state}.colorMode === 'result:rmsf'")
            released.append(True)
            while held:
                held.pop().continue_()
            # The runs offered, the structure rendered, and the colour
            # either kept (its bar shown) or taken.
            page.wait_for_function(
                "() => document.querySelector('#viewer-color option[value=\"run\"]')"
                f" && {state}.model && ({state}.colorMode !== 'result:rmsf'"
                " || !document.getElementById('viewer-legend').hidden)")
            chosen = page.evaluate(f"() => [document.getElementById('viewer-color').value,"
                                   f" {state}.colorMode]")
            legend = page.inner_text("#viewer-legend").splitlines()[0]
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert chosen == ["result:rmsf", "result:rmsf"]
    assert legend == "RMSF, mean of 3 runs (nm)"


def test_a_scene_of_replicas_is_coloured_by_their_mean(replicas):
    from fastmdxplora.gui.runs_together import run_shown
    from fastmdxplora.scenes import write_scene

    said = write_scene(replicas, "mean", {"frame": 1, "colour": "result:rmsf"},
                       source=run_shown(replicas))
    assert said["ok"]
    assert any(note.startswith("Coloured by RMSF, mean of 3 runs: blue 0.0 to red")
               for note in said["notes"])


def test_a_result_only_another_run_has_is_that_run_s(replicas, tmp_path):
    import shutil

    from fastmdxplora.gui.by_residue import values_by_residue

    copy = tmp_path / "copy"
    shutil.copytree(replicas, copy)
    for run in ("s1__random-seed-1", "s1__random-seed-3"):
        shutil.rmtree(copy / "runs" / run / "analysis")
    rmsf = values_by_residue(copy)["properties"][0]
    assert rmsf["source"] == "runs/s1__random-seed-2/analysis/rmsf"
    assert rmsf["about"].endswith("Of random_seed 2 alone: no other run has this result yet.")
