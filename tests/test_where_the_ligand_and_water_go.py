"""Where the ligand and the water go over the frames, and the water sites placed.

The Viewer showed one frame at a time: where a ligand sat over a run, and
where water collected beside it, were nowhere on the structure. Each is now
an occupancy map over the frames played, fitted on the pocket to the first
frame, written as OpenDX and rendered by Mol* as a surface; the sites
`water_sites` found are placed on the first frame played.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"
FRAMES = 20


def _turn(angle: float) -> np.ndarray:
    return np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0],
                     [0, 0, 1]])


def _trypsin_in_water(root: Path, *, water: bool = True) -> Path:
    """Trypsin with benzamidine and its crystal waters, turned through 90
    degrees and carried across a 9 nm box over the run: every water held
    where the crystal has it, with a little noise."""
    from fastmdxplora.gui.trajectory_frames import frames_info

    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    raw = root / "setup" / "input.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(raw))
    system = whole.atom_slice(whole.topology.select(
        "protein or resname BEN" + (" or water" if water else "")))
    rng = np.random.default_rng(3)
    middle = system.xyz[0].mean(axis=0)
    xyz = np.empty((FRAMES, system.n_atoms, 3), dtype=np.float32)
    for frame in range(FRAMES):
        moved = system.xyz[0] + rng.normal(0, 0.02, system.xyz[0].shape)
        xyz[frame] = (moved - middle) @ _turn(np.radians(90) * frame / (FRAMES - 1)).T \
            + [4.5 + 0.02 * frame, 4.5, 4.5]
    trajectory = md.Trajectory(xyz, system.topology,
                               unitcell_lengths=np.full((FRAMES, 3), 9.0, dtype=np.float32),
                               unitcell_angles=np.full((FRAMES, 3), 90.0, dtype=np.float32))
    system.save_pdb(str(root / "setup" / "topology.pdb"))
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    return root


@pytest.fixture(scope="module")
def trypsin(tmp_path_factory) -> Path:
    from fastmdxplora.analysis import AnalysisOrchestrator

    root = _trypsin_in_water(tmp_path_factory.mktemp("occupancy") / "study")
    simulation = root / "simulation"
    AnalysisOrchestrator(str(simulation / "production.dcd"),
                         str(simulation / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(
        include=["water_sites"], options={"water_sites": {"site_selection": "resname BEN",
                                                          "cutoff_nm": 0.6}})
    return root


def _read_dx(path: Path) -> tuple[np.ndarray, np.ndarray, float]:
    lines = path.read_text().splitlines()
    counts = [int(v) for v in lines[0].split()[-3:]]
    origin = np.array([float(v) for v in lines[1].split()[1:]])
    delta = float(lines[2].split()[1])
    values = []
    for line in lines[7:]:
        if line.startswith("attribute"):
            break
        values += [float(v) for v in line.split()]
    return np.array(values).reshape(counts), origin, delta


def _first_frame(root: Path) -> md.Trajectory:
    simulation = root / "simulation"
    return md.load_dcd(str(simulation / "frames.dcd"), top=str(simulation / "frames_topology.pdb"),
                       frame=0)


def _into_first_frame(root: Path) -> callable:
    """Crystal coordinates (nm) carried onto the first frame played."""
    from fastmdxplora.gui.occupancy import _fit

    crystal = md.load_pdb(str(root / "setup" / "topology.pdb"))
    first = _first_frame(root)
    alphas = first.topology.select("name CA")
    crystal_alphas = crystal.topology.select("name CA")
    return _fit(crystal.xyz[0, crystal_alphas].astype(float), first.xyz[0, alphas].astype(float))


def _value_at(grid: np.ndarray, origin: np.ndarray, delta: float, point: np.ndarray) -> float:
    cell = np.rint((point - origin) / delta).astype(int)
    return float(grid[tuple(cell)])


def test_where_the_ligand_was(trypsin):
    from fastmdxplora.gui.occupancy import occupancy

    said = occupancy(trypsin, "ligand", ligand="ben", cutoff_angstrom=5)
    assert said["ok"], said
    assert said["frames"] == FRAMES and said["atoms"] == 9 and said["file"] == (
        "occupancy_ligand_BEN_5.00.dx")
    assert said["peak"] == 1.0
    assert said["said"].startswith("Where BEN's 9 heavy atoms were over the 20 frames played, "
                                   "each fitted on the backbone of the ")
    grid, origin, delta = _read_dx(trypsin / "simulation" / said["file"])
    assert delta == 0.5 and grid.max() == 1.0 and grid.min() == 0.0
    # Every heavy atom of the first frame played sits where the map is full.
    first = _first_frame(trypsin)
    heavy = first.topology.select("resname BEN and not element H")
    assert min(_value_at(grid, origin, delta, p * 10.0) for p in first.xyz[0, heavy]) == 1.0
    # Asked again, it is read, not made again.
    stamp = (trypsin / "simulation" / said["file"]).stat().st_mtime_ns
    assert occupancy(trypsin, "ligand", ligand="BEN", cutoff_angstrom=5.0) == said
    assert (trypsin / "simulation" / said["file"]).stat().st_mtime_ns == stamp


def test_where_the_water_was(trypsin):
    from fastmdxplora.gui.occupancy import occupancy

    said = occupancy(trypsin, "water", ligand="BEN", cutoff_angstrom=5)
    assert said["ok"], said
    assert said["bulk"] == pytest.approx(0.32, abs=0.01)
    assert said["peak"] == 1.0
    assert "Bulk water would read about 0.32." in said["said"]
    grid, origin, delta = _read_dx(trypsin / "simulation" / said["file"])
    # Each crystal water within 5 angstroms of the ligand, held throughout,
    # is full in the map where it sits in the first frame played.
    crystal = md.load_pdb(str(trypsin / "setup" / "topology.pdb"))
    oxygens = crystal.topology.select("water")
    ligand = crystal.topology.select("resname BEN")
    near = [o for o in oxygens
            if np.linalg.norm(crystal.xyz[0, ligand] - crystal.xyz[0, o], axis=1).min() < 0.5]
    assert near
    into = _into_first_frame(trypsin)
    for oxygen in near:
        assert _value_at(grid, origin, delta, into(crystal.xyz[0, oxygen]) * 10.0) >= 0.9


def test_what_is_not_a_map_is_said(trypsin, tmp_path):
    from fastmdxplora.gui.occupancy import occupancy, occupancy_file

    assert occupancy_file("salt", "BEN", 5) == (None, "A map is of the ligand or of the water.")
    assert occupancy_file("water", "", 5)[1] == "A map is about a ligand: no ligand was named."
    assert occupancy_file("water", "../x", 5)[1] == "A map is about a ligand: no ligand was named."
    assert occupancy_file("water", "BEN", 40)[1] == "The pocket's cutoff is 1 to 20 Å."
    assert occupancy_file("water", "BEN", "near")[1] == "The pocket's cutoff is 1 to 20 Å."
    assert occupancy(tmp_path, "ligand", ligand="BEN") == {
        "ok": False, "reason": "There are no frames to map yet."}
    assert occupancy(trypsin, "ligand", ligand="XYZ")["reason"].startswith(
        "The map could not be made: There is no XYZ in the frames.")
    dry = _trypsin_in_water(tmp_path / "dry", water=False)
    said = occupancy(dry, "water", ligand="BEN")
    assert said == {"ok": False, "reason": (
        "The trajectory holds no water: `simulation.save_selection` defaults to `not water`. "
        "Set it to `all` for a study whose subject is the water.")}


def test_the_water_sites_are_placed_on_the_first_frame_played(trypsin):
    from fastmdxplora.gui.occupancy import water_sites_placed

    said = water_sites_placed(trypsin)
    assert said["ok"], said
    assert said["sites"], said
    assert said["said"].endswith("placed by fitting its 9 atoms in the first frame analysed "
                                 "onto the first frame played.")
    crystal = md.load_pdb(str(trypsin / "setup" / "topology.pdb"))
    into = _into_first_frame(trypsin)
    waters = into(crystal.xyz[0, crystal.topology.select("water")]) * 10.0
    for site in said["sites"]:
        point = np.array([site["x"], site["y"], site["z"]])
        # Within the noise the frames were given (0.2 Angstrom an atom) as
        # it reaches the fit of nine atoms and the crystal's onto the frame.
        assert np.linalg.norm(waters - point, axis=1).min() < 0.8
        assert site["bound"] and site["said"] == "one molecule, bound" and site["waters"] == 1


def test_sites_that_cannot_be_placed_say_why(trypsin, tmp_path):
    import shutil

    from fastmdxplora.gui.occupancy import water_sites_placed

    assert water_sites_placed(tmp_path)["reason"].startswith("The study has no water sites")
    old = tmp_path / "old"
    shutil.copytree(trypsin, old)
    options = old / "analysis" / "water_sites" / "options.json"
    record = json.loads(options.read_text())
    record["findings"].pop("fitted_on")
    options.write_text(json.dumps(record))
    assert water_sites_placed(old)["reason"].startswith(
        "These sites were found before each frame was put in the site's own frame")


def test_the_server_gives_the_maps_and_the_sites(trypsin):
    import urllib.error
    import urllib.request

    from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

    assert {"/api/occupancy", "/api/water-sites", "/structure/occupancy.dx"} <= (
        GETS_ANSWERED_BEYOND_LOOPBACK)
    session = start_dashboard_session(output=str(trypsin), host="127.0.0.1", port=0)

    def get(path):
        try:
            with urllib.request.urlopen(session.url + path, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""

    try:
        status, body = get("/api/occupancy?of=ligand&ligand=BEN&cutoff=5")
        said = json.loads(body)
        assert status == 200 and said["ok"]
        assert said["url"].startswith("/structure/occupancy.dx?of=ligand&ligand=BEN&cutoff=5&v=")
        status, grid = get(said["url"])
        assert status == 200
        assert grid == (trypsin / "simulation" / said["file"]).read_bytes()
        assert get("/structure/occupancy.dx?of=ligand&ligand=..%2F..&cutoff=5")[0] == 404
        assert get("/structure/occupancy.dx?of=water&ligand=ZZZ&cutoff=5")[0] == 404
        refused = json.loads(get("/api/occupancy?of=salt&ligand=BEN")[1])
        assert refused == {"ok": False, "reason": "A map is of the ligand or of the water."}
        sites = json.loads(get("/api/water-sites")[1])
        assert sites["ok"] and sites["sites"]
    finally:
        session.server.shutdown()


def test_the_viewer_shows_them_on_the_first_frame(trypsin):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(trypsin), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            # The Playback starts closed; these drive what is in it.
            page.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => !document.getElementById('side-occupancy').hidden"
                                   " && !document.getElementById('occ-sites-row').hidden")
            page.check("#occ-ligand")
            page.check("#occ-water")
            page.check("#occ-sites")
            page.wait_for_function(f"() => {state}.engine.volumesShown()"
                                   ".filter((v) => v.rendered).length === 2"
                                   f" && {state}.engine.sitesRendered")
            shown = {
                "superposed": page.evaluate(f"() => {state}.superposed"),
                "select": page.evaluate("() => document.getElementById('traj-superpose').value"),
                "note": page.inner_text("#occ-note"),
                "sites": page.locator("#occ-sites-list li").all_inner_texts(),
            }
            # A level changed is the surface's.
            page.eval_on_selector("#occ-ligand-level", "(input) => { input.value = '80';"
                                  " input.dispatchEvent(new Event('change')); }")
            page.wait_for_function(f"() => {state}.engine.volumesShown()"
                                   ".some((v) => v.key === 'ligand' && v.level === 0.8)")
            said = page.inner_text("#occ-ligand-level-said")
            # Frames as written are not fitted to the first: set aside.
            page.select_option("#traj-superpose", "none")
            page.wait_for_function("() => document.getElementById('sr-live').textContent"
                                   ".includes('What is placed on the first frame is hidden')")
            aside = page.evaluate(f"() => {state}.engine.volumesShown()"
                                  ".every((v) => !v.rendered)"
                                  f" && !{state}.engine.sitesRendered")
            page.select_option("#traj-superpose", "pocket")
            page.wait_for_function(f"() => {state}.engine.volumesShown()"
                                   ".every((v) => v.rendered)")
            # Unticked, each is gone.
            page.uncheck("#occ-water")
            page.uncheck("#occ-sites")
            page.wait_for_function(f"() => {state}.engine.volumesShown().length === 1"
                                   f" && !{state}.engine.sitesRef")
            left = page.locator("#occ-sites-list li").count()
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert shown["superposed"] == shown["select"] == "pocket"
    assert "Where BEN's 9 heavy atoms were over the 20 frames played" in shown["note"]
    assert "Bulk water would read about 0.32." in shown["note"]
    assert shown["sites"] and all(s.endswith("% of frames, one molecule, bound")
                                  for s in shown["sites"])
    assert said == "80%"
    assert aside
    assert left == 0


def test_sites_that_are_none_or_elsewhere(trypsin, tmp_path):
    import shutil

    from fastmdxplora.gui.occupancy import water_sites_placed

    copy = tmp_path / "copy"
    shutil.copytree(trypsin, copy)
    folder = copy / "analysis" / "water_sites"
    # The copy's analyses read the copy's trajectory, as a moved study's do.
    manifest = copy / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text())
    record["resolved"] = {**(record.get("resolved") or {}),
                          "trajectory": str(copy / "simulation" / "production.dcd")}
    manifest.write_text(json.dumps(record))
    options = json.loads((folder / "options.json").read_text())
    options["findings"]["site"]["selection"] = "water"
    (folder / "options.json").write_text(json.dumps(options))
    assert water_sites_placed(copy)["reason"] == (
        "The site's atoms are not among the atoms played.")
    header = (folder / "water_sites.dat").read_text().splitlines()[0]
    (folder / "water_sites.dat").write_text(header + "\n")
    options["findings"]["not_found"] = "No water came near."
    (folder / "options.json").write_text(json.dumps(options))
    assert water_sites_placed(copy) == {"ok": True, "sites": [], "said": "No water came near."}
    manifest = copy / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text())
    record["resolved"] = {"trajectory": str(tmp_path / "another.dcd")}
    record.pop("trajectory_input", None)
    manifest.write_text(json.dumps(record))
    (folder / "water_sites.dat").write_text((trypsin / "analysis" / "water_sites" /
                                             "water_sites.dat").read_text())
    assert water_sites_placed(copy)["reason"] == (
        "The water sites were found in a trajectory other than the one played.")
