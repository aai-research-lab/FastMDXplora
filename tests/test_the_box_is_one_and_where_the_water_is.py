"""The periodic box is one box, of the frame shown, where the water is.

Mol*'s unit cell was asked for every model the Viewer held, and the frames
are two (the topology and the trajectory built on it): two boxes, apart
once the box changed in NPT. Each was the parallelepiped of the box vectors
from the origin, and a rhombic dodecahedron's water, which setup (OpenMM's
Modeller) and the frames made whole put in the brick of the vectors' own
components, a by b by c along x, y and z, did not fill it.

The box is now one structure of its own: that brick, about the water shown
(or, in the frames, where they were centred), for the frame shown.

A peptide of adenylate kinase (1AKE) solvated in a rhombic dodecahedron,
its four frames each two per cent larger than the one before, as NPT would
make them.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
app = pytest.importorskip("openmm.app")
unit = pytest.importorskip("openmm.unit")
pytest.importorskip("pdbfixer")

DATA = Path(__file__).parent / "data" / "assemblies"
STATE = "window.FastMDXMoleculeViewer.STATE"
FRAMES = 4


def _solvated(source: Path, shape: str):
    from pdbfixer import PDBFixer

    fixer = PDBFixer(filename=str(source))
    fixer.missingResidues = {}
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    forcefield = app.ForceField("amber14-all.xml", "amber14/tip3pfb.xml")
    modeller = app.Modeller(fixer.topology, fixer.positions)
    modeller.addHydrogens(forcefield)
    modeller.addSolvent(forcefield, padding=0.8 * unit.nanometer, boxShape=shape,
                        ionicStrength=0.1 * unit.molar)
    return modeller


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("box") / "study"
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    raw = root / "setup" / "input.pdb"
    raw.write_bytes(gzip.decompress((DATA / "1AKE.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(raw))
    whole.atom_slice(whole.topology.select("chainid 0 and protein and resid 0 to 20")).save_pdb(
        str(root / "setup" / "peptide.pdb"))
    modeller = _solvated(root / "setup" / "peptide.pdb", "dodecahedron")
    for name in ("setup/topology.pdb", "simulation/trajectory_topology.pdb"):
        with open(root / name, "w", encoding="utf-8") as handle:
            app.PDBFile.writeFile(modeller.topology, modeller.positions, handle)
    first = md.load_pdb(str(root / "setup" / "topology.pdb"))
    scale = (1 + 0.02 * np.arange(FRAMES, dtype=np.float32))[:, None, None]
    trajectory = md.Trajectory(np.repeat(first.xyz, FRAMES, axis=0) * scale, first.topology)
    trajectory.unitcell_vectors = np.repeat(first.unitcell_vectors, FRAMES, axis=0) * scale
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    return root


def test_each_frame_carries_its_own_box(study):
    from fastmdxplora.gui.trajectory_frames import frames_info

    said = frames_info(study, force=True)
    trajectory = md.load_dcd(str(study / "simulation" / "production.dcd"),
                             top=str(study / "setup" / "topology.pdb"))
    assert said["frame_indices"] == list(range(FRAMES))
    expected = np.hstack([trajectory.unitcell_lengths * 10, trajectory.unitcell_angles])
    assert np.asarray(said["cells"]) == pytest.approx(expected, abs=1e-3)
    assert said["cells"][0][3:] == pytest.approx([60, 60, 90], abs=1e-3)
    assert said["cells"][-1][0] == pytest.approx(said["cells"][0][0] * 1.06, rel=1e-4)


def test_frames_with_no_box_carry_none(tmp_path):
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    said = frames_info(_helical_study(tmp_path / "study", analyse=False), force=True)
    assert said["available"] and said["cells"] is None


@pytest.mark.parametrize("shape", ["cube", "dodecahedron", "octahedron"])
def test_setup_puts_the_water_in_the_brick_of_the_box_vectors(study, shape):
    """What the box is rendered as: Modeller's water fills a by b by c."""
    modeller = _solvated(study / "setup" / "peptide.pdb", shape)
    vectors = np.asarray(modeller.topology.getPeriodicBoxVectors().value_in_unit(
        unit.angstrom))
    positions = np.asarray(modeller.positions.value_in_unit(unit.angstrom))
    oxygens = positions[[atom.index for atom in modeller.topology.atoms()
                         if atom.residue.name == "HOH" and atom.element.symbol == "O"]]
    span = oxygens.max(axis=0) - oxygens.min(axis=0)
    assert span == pytest.approx(np.diag(vectors), abs=0.5)


@pytest.fixture(scope="module")
def page(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            opened = browser.new_page(viewport={"width": 1440, "height": 900})
            opened.set_default_timeout(60000)
            opened.errors = []
            opened.on("pageerror", lambda error: opened.errors.append(str(error)))
            opened.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not opened.evaluate("() => !!document.createElement('canvas')"
                                   ".getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            opened.wait_for_function(f"() => window.FastMDXMoleculeViewer && {STATE}.model")
            yield opened
            browser.close()
    finally:
        session.server.shutdown()


def _box(page) -> np.ndarray | None:
    text = page.evaluate(f"() => {STATE}.engine.boxText")
    if not text:
        return None
    corners = [[float(line[at:at + 8]) for at in (30, 38, 46)]
               for line in text.splitlines() if line.startswith("HETATM")]
    edges = [line for line in text.splitlines() if line.startswith("CONECT")]
    assert len(corners) == 8 and len(edges) == 12
    return np.asarray(corners)


def _structures(page) -> dict:
    return page.evaluate(f"""() => {{
        const hierarchy = {STATE}.engine.plugin.managers.structure.hierarchy.current;
        return {{structures: hierarchy.structures.length,
                 unitcells: hierarchy.models.filter((m) => m.unitcell).length}};
    }}""")


def _oxygens(page) -> np.ndarray:
    atoms = page.evaluate(f"() => {STATE}.engine.find({{resn: 'HOH', atom: 'O'}})")
    return np.asarray([[a["x"], a["y"], a["z"]] for a in atoms])


def _seek(page, frame: int) -> None:
    page.evaluate(f"() => window.dispatchEvent(new CustomEvent("
                  f"'dashboard:trajectory-seek', {{detail: {{frame: {frame}}}}}))")
    page.wait_for_function(f"(n) => {STATE}.engine.frame() === n", arg=frame)


def test_the_structure_is_in_one_box_its_water_fills(page, study):
    page.check('[data-vis="water"]')
    page.wait_for_function(f"() => {STATE}.engine.find({{resn: 'HOH'}}).length > 0")
    page.check('[data-vis="box"]')
    page.wait_for_function(f"() => !!{STATE}.engine.boxText")
    corners = _box(page)
    vectors = md.load_pdb(str(study / "setup" / "topology.pdb")).unitcell_vectors[0] * 10
    assert corners.max(axis=0) - corners.min(axis=0) == pytest.approx(np.diag(vectors),
                                                                       abs=2e-3)
    # One box, and none of Mol*'s.
    assert _structures(page) == {"structures": 2, "unitcells": 0}
    oxygens = _oxygens(page)
    assert len(oxygens) > 500
    # Every water's oxygen in it, and the box no bigger than the water.
    assert (oxygens >= corners.min(axis=0) - 0.5).all()
    assert (oxygens <= corners.max(axis=0) + 0.5).all()
    assert oxygens.min(axis=0) - corners.min(axis=0) == pytest.approx(0, abs=1.0)


def test_a_click_on_the_box_names_no_atom(page):
    page.wait_for_function(f"() => !!{STATE}.engine.boxText")
    named = page.evaluate(f"""() => {{
        const engine = {STATE}.engine;
        const cells = engine.plugin.state.data.cells;
        const box = cells.get(engine.boxRef).obj.data;
        const main = engine.structure();
        const seen = [];
        engine.listeners.click.push((atom) => seen.push(atom));
        const first = (s) => engine.lib.structure.Structure.toStructureElementLoci(s);
        engine.plugin.behaviors.interaction.click.next({{current: {{loci: first(box)}},
            buttons: 1, button: 1, modifiers: {{}}}});
        engine.plugin.behaviors.interaction.click.next({{current: {{loci: first(main)}},
            buttons: 1, button: 1, modifiers: {{}}}});
        engine.listeners.click.pop();
        return seen.map((atom) => atom && atom.resn);
    }}""")
    assert named[0] is None and named[1] is not None and named[1] != "BOX"


def test_the_frames_box_follows_the_frame_about_the_protein(page, study):
    page.uncheck('[data-vis="water"]')
    page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                  "await (await fetch('/api/frames-info')).json())")
    page.wait_for_function(f"(n) => {STATE}.mode === 'playback' "
                           f"&& {STATE}.engine.frameCount() === n", arg=FRAMES)
    cells = json.loads((study / "simulation" / "frames_index.json").read_text())["cells"]
    frames = md.load_dcd(str(study / "simulation" / "frames.dcd"),
                         top=str(study / "simulation" / "frames_topology.pdb"))
    protein = frames.topology.select("protein")
    sizes = []
    for frame in range(FRAMES):
        _seek(page, frame)
        page.wait_for_function(f"(a) => {{ const t = {STATE}.engine.boxText; return !!t && "
                               f"Math.abs(parseFloat(t.split('\\n')[1].slice(30, 38)) - a) "
                               f"< 1e-2; }}", arg=cells[frame][0])
        corners = _box(page)
        sizes.append(corners.max(axis=0) - corners.min(axis=0))
        # About the protein, where the frames made whole centred it.
        assert (corners.min(axis=0) + corners.max(axis=0)) / 2 == pytest.approx(
            frames.xyz[frame, protein].mean(axis=0) * 10, abs=0.01)
        assert _structures(page) == {"structures": 2, "unitcells": 0}
    assert [size[0] for size in sizes] == pytest.approx([cell[0] for cell in cells], abs=2e-3)
    assert sizes[-1][0] == pytest.approx(sizes[0][0] * 1.06, rel=1e-4)
    assert sizes[0][2] == pytest.approx(cells[0][0] / np.sqrt(2), abs=2e-3)


def test_the_box_is_where_the_water_shown_is(page):
    page.check('[data-vis="water"]')
    page.wait_for_function(f"() => {STATE}.engine.environmentRef && "
                           f"{STATE}.engine.boxText && "
                           f"!{STATE}.engine.boxText.includes('   0.000   0.000   0.000')")
    corners = _box(page)
    middle = page.evaluate(f"""() => {{
        const engine = {STATE}.engine;
        const shift = engine.environmentShift;
        return engine.environmentMiddle.map((v, i) => v + shift[i]);
    }}""")
    assert (corners.min(axis=0) + corners.max(axis=0)) / 2 == pytest.approx(middle, abs=2e-3)


def test_frames_turned_to_fit_are_shown_without_a_box(page):
    page.select_option("#traj-superpose", "backbone")
    page.wait_for_function(f"() => {STATE}.superposed === 'backbone' && !{STATE}.engine.boxText")
    assert page.evaluate(f"() => {STATE}.engine.boxRef") is None
    assert _structures(page)["unitcells"] == 0
    assert page.text_content("#sr-live").endswith(
        "The periodic box is not shown in frames turned to fit.")
    page.select_option("#traj-superpose", "none")
    page.wait_for_function(f"() => {STATE}.superposed === 'none' && !!{STATE}.engine.boxText")
    assert page.errors == []
