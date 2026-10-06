"""What holds the ligand is shown frame by frame, under the frames and in 3D.

The interactions analysis said which contacts hold the ligand and how often
each is present, in a table apart from the structure. It now records the
frames each contact was present in, as runs; the Viewer plots each contact
as a row of a timeline under the frames played, and shows the contacts
present in the frame shown as dashed lines between their atoms, which
follow the frames and leave the person's measurements alone.

Trypsin and benzamidine (3PTB), the benzamidine pulled out of its pocket in
frames 3 and 4: the contacts that hold it break there and others form.
"""

from __future__ import annotations

import gzip
import json
import urllib.request
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("rdkit")

from fastmdxplora.analysis.interaction_summary import episodes_of  # noqa: E402
from fastmdxplora.analysis.interactions import Contact  # noqa: E402
from fastmdxplora.gui.interactions_over_frames import interactions_over_frames  # noqa: E402

DATA = Path(__file__).parent / "data" / "assemblies"
OUT = (3, 4)


def test_each_contact_s_frames_are_kept_as_runs():
    contacts = [Contact(kind="hydrophobic", frame=frame, ligand_atom=0, protein_atom=1,
                        distance_nm=0.3) for frame in (0, 1, 2, 5, 7)]
    contacts += [Contact(kind="hydrogen_bond", frame=4, ligand_atom=2, protein_atom=3,
                         distance_nm=0.28)]
    assert episodes_of(contacts, 8) == {("hydrophobic", 0, 1): [[0, 2], [5, 5], [7, 7]],
                                        ("hydrogen_bond", 2, 3): [[4, 4]]}
    assert episodes_of(contacts, 0) == {}


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from fastmdxplora.analysis import AnalysisOrchestrator
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = tmp_path_factory.mktemp("held") / "study"
    (root / "simulation").mkdir(parents=True)
    (root / "setup").mkdir()
    raw = root / "setup" / "input.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(raw))
    complex_ = whole.atom_slice(whole.topology.select("protein or resname BEN"))
    ligand = complex_.topology.select("resname BEN")
    xyz = np.repeat(complex_.xyz, 6, axis=0)
    for frame in OUT:
        xyz[frame, ligand] += np.array([0.9, 0.0, 0.0], dtype=np.float32)
    xyz += np.random.default_rng(0).normal(0, 0.003, xyz.shape).astype(np.float32)
    trajectory = md.Trajectory(xyz, complex_.topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory[0].save_pdb(str(root / "setup" / "topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                         str(root / "simulation" / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(
        include=["pl_interactions"],
        options={"pl_interactions": {"ligand_resname": "BEN",
                                     "kinds": ["hydrophobic", "hydrogen_bond"]}})
    assert frames_info(root)["available"]
    return root


def test_the_analysis_records_the_frames_of_each_contact(study):
    folder = study / "analysis" / "pl_interactions"
    record = json.loads((folder / "pl_interactions_frames.json").read_text(encoding="utf-8"))
    import pandas as pd

    table = pd.read_csv(folder / "pl_interactions.dat")
    assert record["n_frames"] == 6
    assert len(record["pairs"]) == len(table)
    for pair, row in zip(record["pairs"], table.itertuples()):
        assert (pair["kind"], pair["ligand_atom"], pair["protein_atom"]) == (
            row.kind, row.ligand_atom, row.protein_atom)
        present = sum(b - a + 1 for a, b in pair["episodes"])
        assert present == row.frames_present
        assert pair["occupancy"] == pytest.approx(row.occupancy)
    # The pocket's contacts hold in and break out of it, and others form outside.
    inside = {p["residue"] for p in record["pairs"] if p["episodes"] == [[0, 2], [5, 5]]}
    outside = {p["residue"] for p in record["pairs"] if p["episodes"] == [[3, 4]]}
    assert inside and outside and not inside & outside


def test_the_viewer_is_given_them_on_its_clock_and_atoms(study):
    said = interactions_over_frames(study)
    assert said["ok"] and said["linked"] and said["frames"] == list(range(6))
    assert said["x_label"] == "Frame" and len(said["pairs"]) == said["total_pairs"] <= 40
    frames = md.load_pdb(str(study / "simulation" / "frames_topology.pdb")).topology
    for pair in said["pairs"]:
        protein, ligand = pair["atoms"]
        assert frames.atom(ligand).residue.name == "BEN"
        assert f"{frames.atom(protein).residue.name}{frames.atom(protein).residue.resSeq}" \
            == pair["residue"]
        assert pair["atoms_said"] == f"{frames.atom(protein).name}-{frames.atom(ligand).name}"
    assert interactions_over_frames(study, most=3)["pairs"] == said["pairs"][:3]


def test_atoms_that_are_not_the_ones_named_are_not_placed(study, tmp_path):
    copy = _copy(study, tmp_path)
    record_file = copy / "analysis" / "pl_interactions" / "pl_interactions_frames.json"
    record = json.loads(record_file.read_text(encoding="utf-8"))
    record["pairs"][0]["ligand_atom_name"] = "XX"
    record_file.write_text(json.dumps(record), encoding="utf-8")
    said = interactions_over_frames(copy)
    assert said["pairs"][0]["atoms"] is None and said["pairs"][1]["atoms"] is not None


def _copy(study: Path, tmp_path: Path) -> Path:
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    manifest = copy / "analysis" / "analysis_manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(str(study), str(copy)),
                        encoding="utf-8")
    return copy


@pytest.mark.parametrize("change", ["no frames", "unreadable frames", "frames of another",
                                    "unreadable topology", "an atom not a number"])
def test_where_the_atoms_cannot_be_placed_the_timeline_stands(study, tmp_path, change):
    copy = _copy(study, tmp_path)
    index = copy / "simulation" / "frames_index.json"
    record_file = copy / "analysis" / "pl_interactions" / "pl_interactions_frames.json"
    if change == "no frames":
        index.unlink()
    elif change == "unreadable frames":
        index.write_text("{", encoding="utf-8")
    elif change == "frames of another":
        said = json.loads(index.read_text(encoding="utf-8"))
        index.write_text(json.dumps(dict(said, source_topology="setup/topology.pdb")),
                         encoding="utf-8")
    elif change == "unreadable topology":
        (copy / "simulation" / "trajectory_topology.pdb").write_text("not a structure",
                                                                     encoding="utf-8")
    else:
        record = json.loads(record_file.read_text(encoding="utf-8"))
        record["pairs"] = [dict(record["pairs"][0], protein_atom="x")] + record["pairs"][1:]
        record_file.write_text(json.dumps(record), encoding="utf-8")
    said = interactions_over_frames(copy)
    assert said["ok"] and len(said["pairs"]) == said["total_pairs"]
    placed = [pair["atoms"] is not None for pair in said["pairs"]]
    assert placed == ([False] + [True] * (len(placed) - 1) if change == "an atom not a number"
                      else [False] * len(placed))


def test_a_record_of_no_frames_is_said(study, tmp_path):
    copy = _copy(study, tmp_path)
    record_file = copy / "analysis" / "pl_interactions" / "pl_interactions_frames.json"
    record_file.write_text(json.dumps({"n_frames": 0, "pairs": []}), encoding="utf-8")
    assert interactions_over_frames(copy)["reason"] == "The interactions analysis read no frames."


def test_what_is_said_where_there_are_none(study, tmp_path):
    assert interactions_over_frames(tmp_path)["reason"] == \
        "No protein-ligand interactions were analysed."
    folder = tmp_path / "analysis" / "pl_interactions"
    folder.mkdir(parents=True)
    (folder / "pl_interactions.dat").write_text("kind\n", encoding="utf-8")
    assert "run the pl_interactions analysis again" in interactions_over_frames(tmp_path)["reason"]


def test_they_are_plotted_under_the_frames_and_shown_in_the_structure(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests import viewer_hooks as hooks

    said = interactions_over_frames(study)
    expected = {frame: sum(1 for p in said["pairs"]
                           if any(a <= frame <= b for a, b in p["episodes"]))
                for frame in range(6)}
    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        listed = json.loads(urllib.request.urlopen(
            session.url.rstrip("/") + "/api/interactions-over-frames", timeout=30).read())
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            # The Playback starts closed; these drive what is in it.
            page.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => window.FastMDXMoleculeViewer && {state}.model")
            page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                          "await (await fetch('/api/frames-info')).json())")
            page.wait_for_selector("#frame-interactions:not([hidden]) .frame-interactions-run")
            rows = page.eval_on_selector_all(".frame-interactions-svg .frame-interactions-kind",
                                             "(all) => all.length")
            # A measurement of the person's, which the contacts leave alone.
            hooks.tool(page, "side-view")
            page.click('[data-action="measure"]')
            assert hooks.click(page, resi=189, atom="CA")
            assert hooks.click(page, resi=195, atom="CA")
            page.wait_for_function(f"() => {state}.engine.measurementCount() === 1")
            shown = {}
            for frame in (0, 3, 5):
                page.evaluate(f"() => window.dispatchEvent(new CustomEvent("
                              f"'dashboard:trajectory-seek', {{detail: {{frame: {frame}}}}}))")
                page.wait_for_function(
                    f"() => document.getElementById('frame-interactions-chart')"
                    f".getAttribute('data-frame') === '{frame}'")
                page.wait_for_function(f"(n) => {state}.engine.interactionCount() === n",
                                       arg=expected[frame])
                shown[frame] = {
                    "lines": page.evaluate(f"() => {state}.engine.interactionCount()"),
                    "measured": page.evaluate(f"() => {state}.engine.measurementCount()"),
                    "said": page.text_content("#frame-interactions-now"),
                }
            page.uncheck("#frame-interactions-shown")
            page.wait_for_function(f"() => {state}.engine.interactionCount() === 0")
            measured_after = page.evaluate(f"() => {state}.engine.measurementCount()")
            browser.close()
    finally:
        session.server.shutdown()
    assert listed == json.loads(json.dumps(said))
    assert rows == min(12, len(said["pairs"]))
    for frame, seen in shown.items():
        assert seen["lines"] == expected[frame]
        assert seen["measured"] == 1
        assert seen["said"] == f"{expected[frame]} of {said['total_pairs']} present in this frame"
    assert expected[0] != expected[3]
    assert measured_after == 1
    assert errors == []
