"""Selections are typed, named, kept with the study and shown, as in PyMOL.

A selection typed in MDTraj's language is read in the very structure the
Viewer renders, so its atoms are the ones shown. What is selected (typed,
from the sequence, or clicked) can be named; a named selection is listed
with its colour, whether it is shown, a representation of its own and
labels, kept with the study, and found again in whatever the Viewer
renders: the structure with or without its solvent, or the frames.

Trypsin and benzamidine (3PTB).
"""

from __future__ import annotations

import gzip
import json
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.gui.viewer_selections import (  # noqa: E402
    MOST_SELECTIONS,
    atoms_selected,
    delete_selection,
    save_selection,
    selections_of,
)

from tests import viewer_hooks as hooks  # noqa: E402

DATA = Path(__file__).parent / "data" / "assemblies"
STATE = "window.FastMDXMoleculeViewer.STATE"
SELECTIONS = "window.FastMDXSelections"


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("selections") / "study"
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    raw = root / "setup" / "input.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(raw))
    complex_ = whole.atom_slice(whole.topology.select("protein or resname BEN or water"))
    complex_.save_pdb(str(root / "setup" / "topology.pdb"))
    solute = complex_.atom_slice(complex_.topology.select("not water"))
    xyz = np.repeat(solute.xyz, 3, axis=0)
    xyz += np.random.default_rng(1).normal(0, 0.003, xyz.shape).astype(np.float32)
    trajectory = md.Trajectory(xyz, solute.topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# Reading a typed selection
# --------------------------------------------------------------------------

def _pdb(path: Path) -> tuple[bytes, tuple[str, int, int]]:
    stat = path.stat()
    return path.read_bytes(), (str(path), stat.st_mtime_ns, stat.st_size)


def test_a_typed_selection_names_the_atoms_by_their_place(study):
    pdb, key = _pdb(study / "setup" / "topology.pdb")
    said = atoms_selected(pdb, "resSeq 189 to 195 and name CA", key=key)
    topology = md.load_topology(str(study / "setup" / "topology.pdb"))
    assert said["ok"] and said["atoms"] == list(topology.select("resSeq 189 to 195 and name CA"))
    assert said["residues"] == len(said["atoms"]) and said["n_atoms"] == topology.n_atoms
    assert atoms_selected(pdb, "resname XYZ", key=key)["atoms"] == []


@pytest.mark.parametrize("expression, reason", [
    ("", "Type a selection"),
    ("x" * 501, "at most 500 characters"),
    ("resSeq and and", "MDTraj could not read that selection"),
])
def test_what_cannot_be_read_is_said(study, expression, reason):
    pdb, key = _pdb(study / "setup" / "topology.pdb")
    said = atoms_selected(pdb, expression, key=key)
    assert not said["ok"] and reason in said["reason"]
    assert "no structure" in atoms_selected(None, "protein", key=key)["reason"]
    assert "could not read the structure" in atoms_selected(
        b"not a structure", "protein", key=("other", 0, 0))["reason"]


def test_each_structure_the_viewer_renders_is_read_as_sent(study, tmp_path):
    from fastmdxplora.gui.server import _display_structure_bytes, _viewer_structure

    topology = study / "setup" / "topology.pdb"
    assert _viewer_structure(study, "structure", with_solvent=True)[0] == topology.read_bytes()
    assert _viewer_structure(study, "structure", with_solvent=False)[0] \
        == _display_structure_bytes(topology)
    frames, key = _viewer_structure(study, "frames", with_solvent=False)
    assert frames is None and key == ("", 0, 0)
    (study / "simulation" / "live_frame.pdb").write_bytes(topology.read_bytes())
    try:
        live, key = _viewer_structure(study, "live", with_solvent=False)
        assert live == topology.read_bytes() and key[0].endswith("|live|False")
    finally:
        (study / "simulation" / "live_frame.pdb").unlink()
    assert _viewer_structure(tmp_path, "structure", with_solvent=False) == (None, ("", 0, 0))


def test_a_few_structures_are_kept_read(study):
    from fastmdxplora.gui import viewer_selections

    pdb, _ = _pdb(study / "setup" / "topology.pdb")
    for n in range(6):
        assert atoms_selected(pdb, "name CA", key=("same", n, 0))["ok"]
    assert len(viewer_selections._TOPOLOGIES) == 4
    assert ("same", 5, 0) in viewer_selections._TOPOLOGIES


def test_without_a_study_no_selection_is_named(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path / "nothing"), host="127.0.0.1",
                                      port=0)
    base = session.url.rstrip("/")
    try:
        request = urllib.request.Request(
            base + "/api/viewer-selections",
            data=json.dumps({"name": "one", "selection": {"kind": "expression",
                                                          "expression": "protein"}}).encode(),
            headers={"Content-Type": "application/json", "Origin": base}, method="POST")
        said = json.loads(urllib.request.urlopen(request, timeout=30).read())
    finally:
        session.server.shutdown()
    assert said == {"ok": False, "reason": "No study is open to save it in."}


# --------------------------------------------------------------------------
# Named selections kept with the study
# --------------------------------------------------------------------------

def test_a_named_selection_is_kept_checked(tmp_path):
    said = save_selection(tmp_path, " pocket ", {
        "kind": "residues", "residues": [["A", 189, "", "ASP"], ["A", 184, "A", "GLY"]],
        "colour": "#E69F00", "shown": False, "representation": "surface", "labelled": True,
        "extra": "not kept"})
    assert said["ok"]
    assert selections_of(tmp_path)["selections"] == [{
        "name": "pocket", "kind": "residues",
        "residues": [["A", 189, "", "ASP"], ["A", 184, "A", "GLY"]], "colour": "#e69f00",
        "shown": False, "representation": "surface", "labelled": True}]
    save_selection(tmp_path, "pocket", {"kind": "expression", "expression": " resSeq 190 "})
    assert selections_of(tmp_path)["selections"] == [{
        "name": "pocket", "kind": "expression", "expression": "resSeq 190", "colour": None,
        "shown": True, "representation": "none", "labelled": False}]
    assert delete_selection(tmp_path, "pocket")["selections"] == []
    assert not delete_selection(tmp_path, "pocket")["ok"]


@pytest.mark.parametrize("selection", [
    None, {}, {"kind": "atoms"}, {"kind": "expression", "expression": ""},
    {"kind": "expression", "expression": "x" * 501}, {"kind": "residues", "residues": []},
    {"kind": "residues", "residues": [["A", "189", "", "ASP"]]},
    {"kind": "residues", "residues": [["A", 189, "AB", "ASP"]]},
    {"kind": "residues", "residues": [["A", 189, "", ""]]},
    {"kind": "residues", "residues": [["A", True, "", "ASP"]]},
    {"kind": "residues", "residues": ["A 189"]},
])
def test_what_is_not_a_selection_is_refused(tmp_path, selection):
    said = save_selection(tmp_path, "one", selection)
    assert not said["ok"] and said["reason"] == "That is not a selection the Viewer can show."


def test_names_and_how_many_are_bounded(tmp_path):
    assert "1 to 40 characters" in save_selection(tmp_path, "", {
        "kind": "expression", "expression": "protein"})["reason"]
    assert not save_selection(tmp_path, "x" * 41, {"kind": "expression",
                                                    "expression": "protein"})["ok"]
    for n in range(MOST_SELECTIONS):
        assert save_selection(tmp_path, f"s{n}", {"kind": "expression",
                                                   "expression": "protein"})["ok"]
    assert "at most" in save_selection(tmp_path, "more", {"kind": "expression",
                                                           "expression": "protein"})["reason"]
    # One of the same name is replaced, not added.
    assert save_selection(tmp_path, "s0", {"kind": "expression", "expression": "water"})["ok"]
    (tmp_path / "viewer_selections.json").write_text("{", encoding="utf-8")
    assert selections_of(tmp_path)["selections"] == []


# --------------------------------------------------------------------------
# In the Viewer
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def page(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            opened = browser.new_page(viewport={"width": 1440, "height": 1000})
            opened.set_default_timeout(60000)
            opened.errors = []
            opened.on("pageerror", lambda error: opened.errors.append(str(error)))
            opened.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not opened.evaluate("() => !!document.createElement('canvas')"
                                   ".getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            opened.wait_for_function("() => window.FastMDXSequence"
                                     " && window.FastMDXSequence.state.residues.length > 0")
            opened.session_url = session.url
            yield opened
            browser.close()
    finally:
        session.server.shutdown()


def _type(page, expression: str) -> None:
    hooks.tool(page, "side-selections")
    page.fill("#sel-expression", expression)
    page.click("#sel-form button[type=submit]")


def _components(page) -> list[str]:
    return page.evaluate(f"""() => {{
        const engine = {STATE}.engine;
        const main = engine.plugin.managers.structure.hierarchy.current.structures
            .find((s) => s.cell.transform.ref === engine.mainRef);
        return main ? main.components.map((c) => String(c.key)) : [];
    }}""")


def _component_atoms(page, key: str) -> int:
    return page.evaluate(f"""(key) => {{
        const engine = {STATE}.engine;
        const main = engine.plugin.managers.structure.hierarchy.current.structures
            .find((s) => s.cell.transform.ref === engine.mainRef);
        const c = main && main.components.find((x) => String(x.key).endsWith(key));
        return c && c.cell.obj ? c.cell.obj.data.elementCount : 0;
    }}""", key)


def _paints(page) -> dict:
    """The overpaint and transparency layers on the structure's representations."""
    return page.evaluate(f"""() => {{
        const cells = [...{STATE}.engine.plugin.state.data.cells.values()];
        const tagged = cells.filter((c) => (c.transform.tags || []).includes('fastmdx-paint'));
        const name = (c) => c.transform.transformer.definition.name;
        return {{
            overpaint: tagged.filter((c) => /overpaint/i.test(name(c)))
                .map((c) => c.transform.params.layers.length),
            transparency: tagged.filter((c) => /transparency/i.test(name(c)))
                .map((c) => c.transform.params.layers.length),
        }};
    }}""")


def test_a_typed_selection_selects_the_atoms_shown(page, study):
    _type(page, "resSeq 189 to 195 and name CA")
    page.wait_for_function(f"() => {STATE}.selection && {STATE}.selection.expression")
    pdb = urllib.request.urlopen(page.session_url + "/structure/topology.pdb").read()
    expected = atoms_selected(pdb, "resSeq 189 to 195 and name CA",
                              key=("served", 0, len(pdb)))["atoms"]
    assert page.evaluate(f"() => {STATE}.selection.atoms") == expected
    assert page.evaluate(f"() => {STATE}.engine.atoms({STATE}.selection.atoms)"
                         ".map((a) => a.atom)") \
        == ["CA"] * len(expected)
    hooks.tool(page, "side-selections")
    assert page.text_content("#sel-said") == (
        f"Selected by resSeq 189 to 195 and name CA: {len(expected)} atoms "
        f"in {len(expected)} residues.")
    # The sequence marks the residues holding them.
    marked = page.evaluate("() => [...window.FastMDXSequence.state.selected]"
                           ".map((k) => window.FastMDXSequence.state.residues[k].resi)")
    assert sorted(marked) == sorted(
        a["resi"] for a in page.evaluate(f"() => {STATE}.engine.atoms({STATE}.selection.atoms)"))
    _type(page, "resname XYZ")
    page.wait_for_function("() => document.getElementById('sel-said').textContent"
                           ".startsWith('No atom')")
    _type(page, "resSeq and and")
    page.wait_for_function("() => document.getElementById('sel-said').textContent"
                           ".startsWith('MDTraj could not read that selection')")


def test_a_selection_named_is_listed_kept_and_rendered(page, study):
    _type(page, "resname BEN")
    page.wait_for_function(f"() => {STATE}.selection"
                           f" && {STATE}.selection.expression === 'resname BEN'")
    ligand = page.evaluate(f"() => {STATE}.selection.atoms.length")
    hooks.tool(page, "side-selections")
    page.fill("#sel-name", "ligand")
    page.click("#sel-keep")
    page.wait_for_selector('.sel-item[data-name="ligand"]')
    page.wait_for_function(f"() => {SELECTIONS}.state.atoms.get('ligand')")
    kept = json.loads((study / "viewer_selections.json").read_text(encoding="utf-8"))
    assert kept["selections"] == [{"name": "ligand", "kind": "expression",
                                   "expression": "resname BEN", "colour": "#e69f00",
                                   "shown": True, "representation": "sticks",
                                   "labelled": False}]
    assert page.text_content('.sel-item[data-name="ligand"] .sel-count') == f"{ligand} atoms"
    page.wait_for_function(f"() => {STATE}.engine.components && "
                           f"{STATE}.engine.plugin.managers.structure.hierarchy.current.structures"
                           f".some((s) => s.components.some("
                           f"(c) => String(c.key).endsWith('selection-0')))")
    assert _component_atoms(page, "fastmdx-selection-0") == ligand
    assert _paints(page)["overpaint"] and set(_paints(page)["overpaint"]) == {1}


def test_residues_from_the_sequence_are_named_and_hidden(page, study):
    page.evaluate("() => window.FastMDXSequence.select([10, 11, 12, 13, 14])")
    page.wait_for_function(f"() => {STATE}.selection && {STATE}.selection.residues.length === 5")
    hooks.tool(page, "side-selections")
    page.click("#sel-keep")
    page.wait_for_selector('.sel-item[data-name="sele1"]')
    page.wait_for_function(f"() => {SELECTIONS}.state.atoms.get('sele1')")
    residues = page.evaluate("() => window.FastMDXSequence.state.residues.slice(10, 15)"
                             ".map((r) => [r.chain, r.resi, r.icode, r.resn])")
    kept = json.loads((study / "viewer_selections.json").read_text(encoding="utf-8"))
    assert kept["selections"][1]["kind"] == "residues"
    assert kept["selections"][1]["residues"] == residues
    atoms = page.evaluate(f"() => {SELECTIONS}.state.atoms.get('sele1').length")
    assert atoms == page.evaluate("() => window.FastMDXSequence.state.residues.slice(10, 15)"
                                  ".reduce((n, r) => n + r.last - r.first + 1, 0)")
    # Hidden: transparent everywhere it is rendered, and no sticks of its own.
    item = page.locator('.sel-item[data-name="sele1"]')
    item.locator(".sel-eye").click()
    page.wait_for_function(f"() => {SELECTIONS}.state.named[1].shown === false")
    page.wait_for_function(f"""() => [...{STATE}.engine.plugin.state.data.cells.values()]
        .some((c) => (c.transform.tags || []).includes('fastmdx-paint')
             && /transparency/i.test(c.transform.transformer.definition.name))""")
    assert not any(key.endswith("selection-1") for key in _components(page))
    assert item.locator(".sel-eye").get_attribute("aria-pressed") == "false"
    item.locator(".sel-eye").click()
    page.wait_for_function(f"() => {SELECTIONS}.state.named[1].shown === true")
    # A representation and labels of its own, and its colour changed.
    item.locator(".sel-shape").select_option("spheres")
    item.locator(".sel-labels").check()
    page.wait_for_function(f"() => {SELECTIONS}.state.named[1].labelled")
    page.wait_for_function(f"""() => {STATE}.engine.plugin.managers.structure.hierarchy.current
        .structures.some((s) => s.components.some(
            (c) => String(c.key).endsWith('selection-1-labels')))""")
    assert _component_atoms(page, "fastmdx-selection-1") == atoms
    item.locator(".sel-colour").evaluate(
        "(input) => { input.value = '#0072b2'; input.dispatchEvent(new Event('change')); }")
    item.locator(".sel-tinted").uncheck()
    page.wait_for_function(f"() => {SELECTIONS}.state.named[1].colour === null")
    kept = json.loads((study / "viewer_selections.json").read_text(encoding="utf-8"))
    assert kept["selections"][1] | {"residues": None} == {
        "name": "sele1", "kind": "residues", "residues": None, "colour": None,
        "shown": True, "representation": "spheres", "labelled": True}


def test_named_selections_are_found_again_in_the_frames(page, study):
    named = page.evaluate(f"() => {SELECTIONS}.state.named.map((s) => s.name)")
    assert named == ["ligand", "sele1"]
    before = page.evaluate(f"() => Object.fromEntries({SELECTIONS}.state.atoms)")
    page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                  "await (await fetch('/api/frames-info')).json())")
    page.wait_for_function(f"() => {STATE}.mode === 'playback' && {STATE}.model.of === 'frames'")
    page.wait_for_function(f"() => {SELECTIONS}.state.signature"
                           f" === window.FastMDXMoleculeViewer.modelSignature()")
    after = page.evaluate(f"() => Object.fromEntries({SELECTIONS}.state.atoms)")
    frames = md.load_topology(str(study / "simulation" / "frames_topology.pdb"))
    assert after["ligand"] == list(frames.select("resname BEN"))
    # The same residues, by their atoms in the frames.
    names = page.evaluate(f"(atoms) => {STATE}.engine.atoms(atoms).map((a) => a.resi)",
                          after["sele1"])
    assert len(after["sele1"]) == len(before["sele1"]) and len(set(names)) == 5


def test_a_named_selection_is_picked_centred_and_forgotten(page, study):
    page.click('.sel-item[data-name="ligand"] .sel-pick')
    page.wait_for_function(f"() => {STATE}.selection"
                           f" && {STATE}.selection.expression === 'resname BEN'")
    page.click('.sel-item[data-name="ligand"] .sel-centre')
    page.click('.sel-item[data-name="ligand"] .sel-forget')
    page.wait_for_function("() => !document.querySelector('.sel-item[data-name=\"ligand\"]')")
    kept = json.loads((study / "viewer_selections.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in kept["selections"]] == ["sele1"]
    listed = json.loads(urllib.request.urlopen(page.session_url + "/api/viewer-selections").read())
    assert [s["name"] for s in listed["selections"]] == ["sele1"]
    answer = json.loads(urllib.request.urlopen(
        page.session_url + "/api/viewer-atoms?of=frames&expression="
        + urllib.parse.quote("resname BEN")).read())
    assert answer["ok"] and len(answer["atoms"]) == 9
    assert page.errors == []


def test_a_ligand_clicked_is_selected_though_not_in_the_sequence(page):
    atom = hooks.click(page, resn="BEN", atom="C1")
    assert atom
    page.wait_for_function(f"() => {STATE}.selection && {STATE}.selection.residues.length === 1"
                           f" && {STATE}.selection.residues[0].resn === 'BEN'")
    assert page.evaluate(f"() => {STATE}.selection.atoms.length") == 9
    assert page.evaluate("() => window.FastMDXSequence.state.selected.size") == 0
