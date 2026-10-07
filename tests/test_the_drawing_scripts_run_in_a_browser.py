"""The GUI's drawing scripts, run in a browser on a study.

`molecule-viewer.js` is the largest script the GUI has, and nothing ran it:
the browser tests exercised the frame, the composer and the run builder, and
every check on the viewer read its source for a string. So a change that
left the canvas blank, drew the solvated system with no water in it, or
stopped playback on its second frame passed.

Here it is driven as a person would, with the bundled Mol*, on a study with
a protein, a ligand, water, ions and a short trajectory: the structure is
drawn without solvent, the water toggle fetches it, every representation,
colouring and ligand control runs, and playback steps through the frames
with the atoms moving. The charts are drawn from the study's own energy log,
read by the server as a finished run's is. A page error anywhere fails the
test.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("playwright.sync_api")

from tests import viewer_hooks as hooks  # noqa: E402

VIEWER = "window.FastMDXMoleculeViewer.STATE"
ENGINE = f"{VIEWER}.engine"
FRAMES = 20
#: Atoms the browser is sent without solvent: ten alanines and the ligand.
SOLUTE = 10 * 5 + 4


def _write_study(root: Path) -> Path:
    """A prepared system and a production trajectory, as a study leaves them."""
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    topology = md.Topology()
    chain = topology.add_chain()
    xyz = []
    for index in range(10):
        residue = topology.add_residue("ALA", chain, resSeq=index + 1)
        for offset, (name, element) in enumerate((
                ("N", md.element.nitrogen), ("CA", md.element.carbon),
                ("C", md.element.carbon), ("O", md.element.oxygen),
                ("CB", md.element.carbon))):
            topology.add_atom(name, element, residue)
            xyz.append([0.38 * index + 0.1 * offset, 0.05 * offset, 0.0])
    ligand = topology.add_residue("LIG", topology.add_chain(), resSeq=1)
    for n in range(4):
        topology.add_atom(f"C{n}", md.element.carbon, ligand)
        xyz.append([1.5 + 0.14 * n, 0.8, 0.0])
    solvent = topology.add_chain()
    for w in range(6):
        water = topology.add_residue("HOH", solvent, resSeq=w + 1)
        for name, element in (("O", md.element.oxygen), ("H1", md.element.hydrogen),
                              ("H2", md.element.hydrogen)):
            topology.add_atom(name, element, water)
            xyz.append([0.5 * w, 2.0, 0.1 * (name != "O")])
    for k in range(2):
        topology.add_atom("NA", md.element.sodium,
                          topology.add_residue("NA", solvent, resSeq=10 + k))
        xyz.append([float(k), -1.5, 0.0])
    whole = md.Trajectory(np.array(xyz)[None] + 2.0, topology,
                          unitcell_lengths=[[5.0, 5.0, 5.0]],
                          unitcell_angles=[[90.0, 90.0, 90.0]])
    whole.save_pdb(str(root / "setup" / "topology.pdb"))
    solute = whole.atom_slice(topology.select("not water"))
    moving = (np.repeat(solute.xyz, FRAMES, axis=0)
              + np.linspace(0.0, 0.3, FRAMES)[:, None, None])
    # The ligand moves in its pocket as well, 0.3 nm along z over the run:
    # the playback is centred on the protein, as the analyses read it, so
    # the drift of the whole is not seen.
    moving[:, solute.topology.select("resname LIG"), 2] += np.linspace(0.0, 0.3, FRAMES)[:, None]
    trajectory = md.Trajectory(moving, solute.topology,
                               unitcell_lengths=np.full((FRAMES, 3), 5.0),
                               unitcell_angles=np.full((FRAMES, 3), 90.0))
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    # OpenMM's reporter, as a run writes it, with a density it did not report.
    (root / "simulation" / "energy.csv").write_text(
        '#"Step","Time (ps)","Potential Energy (kJ/mole)","Total Energy (kJ/mole)",'
        '"Temperature (K)","Density (g/mL)","Speed (ns/day)"\n'
        + "".join(f"{500 * n},{n:.1f},{-506551.2 + 10 * n},{-400100.0 + n},"
                  f"{299.5 + 0.1 * n},,{120.5}\n" for n in range(6)),
        encoding="utf-8")
    # What a finished run's live telemetry says, which is what shows the
    # panels the charts are on.
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production", "current_step": 2500,
         "total_steps": 2500}), encoding="utf-8")
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps(
        {"parameters": {"trajectory_interval_steps": 500, "timestep_fs": 2}}),
        encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path_factory.mktemp("viewer") / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield session
    session.server.shutdown()


def _open(dashboard, where: str):
    """A browser on one page of the study, with every page error collected."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        opened = browser.new_page(viewport={"width": 1400, "height": 900})
        # The Playback starts closed; these drive what is in it.
        opened.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
        errors: list[str] = []
        opened.on("pageerror", lambda error: errors.append(str(error)))
        opened.on("console", lambda message: errors.append(message.text)
                  if message.type == "error" else None)
        opened.errors = errors
        opened.goto(dashboard.url + where, wait_until="domcontentloaded")
        yield opened
        browser.close()


@pytest.fixture
def page(dashboard):
    """The viewer page, with the structure drawn."""
    for opened in _open(dashboard, "#viewer"):
        if not opened.evaluate(
                "() => !!document.createElement('canvas').getContext('webgl')"):
            pytest.skip("this browser has no WebGL, so the viewer cannot render")
        opened.wait_for_function(hooks.RENDERED, timeout=60000)
        yield opened


@pytest.fixture
def overview(dashboard):
    """The overview, where the charts are."""
    for opened in _open(dashboard, "#overview"):
        opened.wait_for_function(
            "() => document.querySelector('[data-chart-value=\"temperature\"]')"
            ".textContent !== '\u2014'", timeout=60000)
        yield opened


def _atoms(page, **selection) -> int:
    return len(hooks.atoms(page, **selection))


def test_the_structure_is_drawn_without_its_solvent(page) -> None:
    # Read once the atoms are there: on CI's coverage job the model was set
    # while the engine held no atoms, a structure being rendered again.
    page.wait_for_function(f"(n) => {ENGINE}.find({{}}).length === n", arg=SOLUTE,
                           timeout=60000)
    assert _atoms(page) == SOLUTE
    assert _atoms(page, resn="LIG") == 4
    assert page.evaluate(f"() => {VIEWER}.ligandResname") == "LIG"
    assert page.get_attribute("#viewer-canvas-frame", "data-ready") is not None
    assert page.errors == []


def test_the_water_toggle_fetches_the_water(page) -> None:
    page.click('.chip-toggle input[data-vis="water"]')
    page.wait_for_function(
        f"() => {VIEWER}.model && {ENGINE}.find({{resn: 'HOH'}}).length", timeout=60000)
    assert _atoms(page, resn="HOH") == 18
    page.click('.chip-toggle input[data-vis="water"]')
    page.wait_for_function(
        f"() => {VIEWER}.model && !{ENGINE}.find({{resn: 'HOH'}}).length", timeout=60000)
    assert page.errors == []


def test_every_style_and_ligand_control_runs(page) -> None:
    for listed in ("#viewer-rep", "#viewer-color"):
        for value in page.eval_on_selector_all(f"{listed} option", "os => os.map(o => o.value)"):
            page.select_option(listed, value)
    # Each ligand button is wired whatever it looks like: Center, a chip
    # until it was a line icon, lost its click while the wiring looked for
    # chips.
    page.evaluate(f"""() => {{
        const engine = {ENGINE};
        const own = engine.focusPart;
        window.__focused = [];
        engine.focusPart = (...asked) => {{ window.__focused.push(asked[0]); return own.apply(engine, asked); }};
    }}""")
    for selector, tool in ((".chip-btn[data-cam]", "side-view"),
                           ("[data-ligand]", "side-ligand")):
        hooks.tool(page, tool)
        for button in page.query_selector_all(selector):
            if button.is_visible():
                button.click()
    hooks.tool(page, "side-ligand")
    page.evaluate("() => { window.__focused = []; }")
    page.click('[data-ligand="center"]')
    assert page.evaluate("() => window.__focused") == ["ligand"]
    # One icon saves a view or a scene file, named for the one being named.
    hooks.tool(page, "side-saved")
    page.click("#viewer-scene-save")
    for_a_scene = page.get_attribute("#viewer-view-keep", "aria-label")
    page.click("#viewer-view-save")
    for_a_view = page.get_attribute("#viewer-view-keep", "aria-label")
    assert (for_a_scene, for_a_view) == ("Save the scene", "Save the view")
    # Spin is one button: pressed once above, so spinning; pressed again, not.
    hooks.tool(page, "side-view")
    spin = page.locator('[data-cam="spin"]')
    assert spin.get_attribute("aria-pressed") == "true"
    spin.click()
    assert spin.get_attribute("aria-pressed") == "false"
    assert page.evaluate(f"() => {VIEWER}.spinning") is False
    assert _atoms(page) == SOLUTE
    assert page.errors == []


def test_a_structure_asked_for_twice_while_it_loads_is_loaded_once(page) -> None:
    """The structure's state comes twice as the page opens, and both asked
    for the structure while the first load had not yet set the model: it
    was loaded and rendered twice, the engine empty between the two, and a
    test that read the atoms then found none (the pocket, the solvent-free
    count; once in five or six openings here)."""
    # The engine's loads counted (its own function, read past the queue
    # that orders its calls), for the structure asked for once, then for it
    # asked for twice at once.
    loads = page.evaluate("""async () => {
        const state = window.FastMDXMoleculeViewer.STATE;
        const info = await (await fetch('/api/structure-info', {cache: 'no-store'})).json();
        let holder = state.engine;
        while (holder && !Object.getOwnPropertyDescriptor(holder, 'loadStructure')) {
          holder = Object.getPrototypeOf(holder);
        }
        const own = Object.getOwnPropertyDescriptor(holder, 'loadStructure').value;
        let loads = 0;
        state.engine.loadStructure = function (...args) {
          loads += 1;
          return own.apply(this, args);
        };
        const askedFor = async (times) => {
          loads = 0;
          state.model = null;
          for (let i = 0; i < times; i += 1) {
            window.dispatchEvent(new CustomEvent('dashboard:structure-updated', {detail: info}));
          }
          const started = performance.now();
          while (!state.model && performance.now() - started < 60000) {
            await new Promise((done) => setTimeout(done, 20));
          }
          await new Promise((done) => setTimeout(done, 1500));
          return loads;
        };
        return [await askedFor(1), await askedFor(2)];
    }""")
    assert loads == [1, 1]
    assert page.evaluate(f"() => {ENGINE}.find({{resn: 'LIG'}}).length") == 4
    assert page.errors == []


def test_the_pocket_is_the_residues_within_the_cutoff(page, dashboard) -> None:
    """The residues with a heavy atom within the cutoff of the ligand's,
    centre to centre, as MDTraj finds them. Mol*'s own "within" took in
    atoms a whole angstrom past it, so the pocket is worked out here."""
    from urllib.request import urlopen

    text = urlopen(dashboard.url.rstrip("/") + "/structure/topology.pdb").read().decode()
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".pdb", delete=False) as handle:
        handle.write(text)
    shown = md.load_pdb(handle.name)
    Path(handle.name).unlink()
    ligand = shown.topology.select("resname LIG and not element H")
    protein = shown.topology.select("protein and not element H")
    for cutoff in (5.0, 6.0, 7.5, 12.0):
        near = md.compute_neighbors(shown, cutoff / 10 + 1e-6, ligand, haystack_indices=protein)[0]
        residues = {shown.topology.atom(int(i)).residue.index for i in near}
        expected = sorted(atom.index for atom in shown.topology.atoms
                          if atom.residue.index in residues)
        # Asked once the atoms are there: in a full suite the structure was
        # rendered again between two cutoffs, and the engine held none.
        page.wait_for_function(f"(n) => {ENGINE}.find({{}}).length === n", arg=SOLUTE,
                               timeout=60000)
        found = page.evaluate("(cutoff) => window.FastMDXMoleculeViewer.STATE.engine"
                              ".pocketAtoms(['LIG'], cutoff)", cutoff)
        assert found == expected, cutoff
    # One that takes in a residue is rendered, and follows the cutoff asked.
    hooks.tool(page, "side-ligand")
    page.fill("#pocket-cutoff", "7.5")
    page.dispatch_event("#pocket-cutoff", "change")
    page.wait_for_function(f"() => ({ENGINE}.rendered || []).includes('pocket')")
    assert page.errors == []


def test_playback_steps_through_the_frames_and_the_atoms_move(page) -> None:
    page.click('.ctl-btn[data-action="next-frame"]')
    page.wait_for_function(f"() => {VIEWER}.playbackLoaded", timeout=60000)
    page.wait_for_function("() => document.getElementById('traj-slider').value === '1'",
                           timeout=60000)
    page.wait_for_function(f"() => {ENGINE}.frame() === 1", timeout=60000)
    first_z = hooks.atoms(page, resn="LIG")[0]["z"]
    assert page.evaluate(f"() => {VIEWER}.playbackFrames") == FRAMES
    # Stepping a frame is choosing one: following the run stops, or the first
    # press lands on the newest frame and "next" goes nowhere.
    assert not page.is_checked("#traj-follow")
    for _ in range(9):
        page.click('.ctl-btn[data-action="next-frame"]')
    page.wait_for_function("() => document.getElementById('traj-current').textContent === '10'",
                           timeout=60000)
    page.wait_for_function(f"() => {ENGINE}.frame() === 10", timeout=60000)
    moved = hooks.atoms(page, resn="LIG")[0]["z"] - first_z
    # The ligand moves 0.3 nm along z from the protein over the run; frames
    # 1 to 10 of 20 are 9/19 of it, and Mol* works in angstroms.
    assert moved == pytest.approx(3.0 * 9 / (FRAMES - 1), abs=0.05)
    page.click('.ctl-btn[data-action="prev-frame"]')
    page.wait_for_function("() => document.getElementById('traj-current').textContent === '9'",
                           timeout=60000)
    assert page.errors == []


def test_water_during_playback_is_an_overlay_that_follows_the_frames(page) -> None:
    page.click('.ctl-btn[data-action="next-frame"]')
    page.wait_for_function(f"() => {VIEWER}.playbackLoaded", timeout=60000)
    page.click('.chip-toggle input[data-vis="water"]')
    page.wait_for_function(f"() => {ENGINE}.environmentCount('HOH')", timeout=60000)
    assert page.evaluate(f"() => {ENGINE}.environmentCount('HOH')") == 18
    # The animated solute stays the playback model.
    assert page.evaluate(f"() => {VIEWER}.mode") == "playback"
    page.click('.ctl-btn[data-action="next-frame"]')
    page.wait_for_function("() => document.getElementById('traj-current').textContent === '2'",
                           timeout=60000)
    assert page.errors == []


def test_the_charts_draw_the_energy_log(overview) -> None:
    latest = overview.evaluate(
        "() => Object.fromEntries([...document.querySelectorAll('[data-chart-value]')]"
        ".map(cell => [cell.dataset.chartValue, cell.textContent]))")
    # The last row, grouped where it is large, and a gap where the run did not
    # sample: an empty cell is not a zero.
    assert latest == {"potential_energy": "-506,501", "total_energy": "-400,095",
                      "temperature": "300.00", "density": "\u2014", "speed": "120.50"}
    drawn = overview.evaluate(
        "() => { const c = document.querySelector('canvas[data-chart=\"temperature\"]');"
        " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
        " let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; }")
    assert drawn > 0
    assert overview.is_hidden("#chart-empty") or overview.evaluate(
        "() => getComputedStyle(document.getElementById('chart-empty')).display") == "none"
    assert overview.errors == []


def test_a_chart_chosen_alone_is_the_only_one_shown(overview) -> None:
    overview.select_option("#chart-metric-select", "temperature")
    shown = overview.evaluate(
        "() => [...document.querySelectorAll('.chart-row')].filter(r => !r.hidden)"
        ".map(r => r.querySelector('canvas').dataset.chart)")
    assert shown == ["temperature"]
    overview.click("#chart-reset")
    assert overview.evaluate(
        "() => [...document.querySelectorAll('.chart-row')].every(r => !r.hidden)")
    assert overview.errors == []
