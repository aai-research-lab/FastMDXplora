"""The way through a channel: its radius along its axis, and in the Viewer.

VMD users measure a pore with HOLE: the largest sphere among the protein's
atoms at each height along the pore. `pore_profile` measures the same over a
study's frames, each put in the protein's own frame first, and the Viewer
plots it and renders the mean pore. Held here against pores whose radius is
known by construction, and against KcsA (1BL8), whose selectivity filter and
cavity are in every textbook.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest
from tests import viewer_hooks as hooks

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"
FRAMES = 12
CARBON = 0.17


def _tube(wall, length: float = 4.0, between: float = 0.05) -> np.ndarray:
    """Atoms on a surface of revolution about z, ``wall(z)`` from the axis,
    about ``between`` nm apart."""
    rng = np.random.default_rng(0)
    rings = []
    for z in np.arange(-length / 2, length / 2 + 1e-9, between):
        count = max(12, int(2 * np.pi * wall(z) / between))
        angle = np.linspace(0, 2 * np.pi, count, endpoint=False) + rng.uniform(0, 1)
        rings.append(np.c_[wall(z) * np.cos(angle), wall(z) * np.sin(angle),
                           np.full(count, z)])
    return np.concatenate(rings)


def _turn(angle: float) -> np.ndarray:
    return np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0],
                     [0, 0, 1]])


def _tip(angle: float) -> np.ndarray:
    return np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)],
                     [0, np.sin(angle), np.cos(angle)]])


# ---------------------------------------------------------------------------
# The search, against pores of known radius
# ---------------------------------------------------------------------------

def test_a_cylinder_is_as_wide_as_it_was_built():
    from fastmdxplora.analysis.pore_profile import widest_path

    xyz = _tube(lambda z: 0.5)
    heights = np.round(np.arange(-1.5, 1.5001, 0.05), 6)
    # Started off the axis: the centre is found, not given.
    radius, centre = widest_path(xyz[None], np.full(len(xyz), CARBON), heights,
                                 np.array([0.12, -0.08, 0.0]), 1.0)
    assert np.isfinite(radius).all()
    assert np.allclose(radius, 0.5 - CARBON, atol=0.004)
    assert np.abs(centre).max() < 0.01


def test_an_hourglass_is_narrowest_at_its_waist():
    from fastmdxplora.analysis.pore_profile import widest_path

    slope = 0.15
    xyz = _tube(lambda z: 0.3 + slope * abs(z))
    heights = np.round(np.arange(-1.5, 1.5001, 0.05), 6)
    radius, _ = widest_path(xyz[None], np.full(len(xyz), CARBON), heights, np.zeros(3), 1.0)
    waist = int(np.argmin(radius[0]))
    assert heights[waist] == 0.0
    assert radius[0, waist] == pytest.approx(0.3 - CARBON, abs=0.004)
    # On a cone a sphere touches the wall where it is nearest, off its plane.
    cone = (0.3 + slope * np.abs(heights)) * np.cos(np.arctan(slope)) - CARBON
    away = np.abs(heights) > 0.2
    assert np.allclose(radius[0, away], cone[away], atol=0.005)


def test_frames_are_searched_apart_and_alike():
    """Frames share a tree; none answers for another."""
    from fastmdxplora.analysis.pore_profile import widest_path

    narrow, wide = _tube(lambda z: 0.4), _tube(lambda z: 0.6)
    assert len(narrow) != len(wide)
    heights = np.round(np.arange(-1.0, 1.0001, 0.1), 6)
    alone = [widest_path(xyz[None], np.full(len(xyz), CARBON), heights, np.zeros(3), 1.0)[0]
             for xyz in (narrow, wide)]
    # 70 frames of one, more than are searched at once.
    many = widest_path(np.repeat(narrow[None], 70, axis=0), np.full(len(narrow), CARBON),
                       heights, np.zeros(3), 1.0)[0]
    assert np.allclose(many, alone[0][0][None], atol=1e-9)
    assert np.allclose(alone[1], 0.6 - CARBON, atol=0.004)


def test_the_path_ends_where_the_sphere_reaches_bulk():
    from fastmdxplora.analysis.pore_profile import widest_path

    # A tube 2 nm long, searched over 4 nm: beyond its mouths is the open.
    xyz = _tube(lambda z: 0.4, length=2.0)
    heights = np.round(np.arange(-2.0, 2.0001, 0.1), 6)
    radius, centre = widest_path(xyz[None], np.full(len(xyz), CARBON), heights,
                                 np.zeros(3), 0.6)
    inside = np.abs(heights) <= 0.9
    assert np.isfinite(radius[0, inside]).all()
    assert np.isnan(radius[0, np.abs(heights) >= 1.9]).all()
    assert np.isnan(centre[0, np.abs(heights) >= 1.9]).all()
    assert np.nanmax(radius) < 0.6


def test_surfaces_that_meet_are_a_radius_of_zero():
    from fastmdxplora.analysis.pore_profile import widest_path

    # A tube whose wall atoms overlap across it: closed, and never negative.
    xyz = _tube(lambda z: 0.1, length=1.0)
    heights = np.round(np.arange(-0.4, 0.4001, 0.1), 6)
    radius, _ = widest_path(xyz[None], np.full(len(xyz), CARBON), heights, np.zeros(3), 1.0)
    assert (radius == 0.0).all()


# ---------------------------------------------------------------------------
# The analysis, on KcsA
# ---------------------------------------------------------------------------

def _kcsa() -> "md.Trajectory":
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as folder:
        path = Path(folder) / "1BL8.pdb"
        path.write_bytes(gzip.decompress((DATA / "1BL8.pdb.gz").read_bytes()))
        whole = md.load_pdb(str(path))
    return whole.atom_slice(whole.topology.select("protein"))


def _carried(system: "md.Trajectory", frames: int = FRAMES) -> "md.Trajectory":
    """The channel turned, tipped and carried across a 12 nm box over the
    run, with a little noise: a protein drifting in its bilayer."""
    rng = np.random.default_rng(5)
    middle = system.xyz[0].mean(axis=0)
    xyz = np.empty((frames, system.n_atoms, 3), dtype=np.float32)
    for frame in range(frames):
        moved = system.xyz[0] + rng.normal(0, 0.003, system.xyz[0].shape)
        share = frame / max(1, frames - 1)
        xyz[frame] = (moved - middle) @ (_turn(np.radians(70) * share)
                                         @ _tip(np.radians(25) * share)).T \
            + [6.0 + 0.05 * frame, 6.0, 6.0]
    return md.Trajectory(xyz, system.topology,
                         unitcell_lengths=np.full((frames, 3), 12.0, dtype=np.float32),
                         unitcell_angles=np.full((frames, 3), 90.0, dtype=np.float32))


@pytest.fixture(scope="module")
def kcsa() -> "md.Trajectory":
    return _kcsa()


@pytest.fixture(scope="module")
def analysed(kcsa, tmp_path_factory):
    from fastmdxplora.analysis.pore_profile import PoreProfile

    analysis = PoreProfile(output_dir=tmp_path_factory.mktemp("pore"))
    result = analysis.run(_carried(kcsa))
    assert result.status == "ok", result.message
    return analysis, result


def test_kcsa_is_closed_at_its_filter_and_wide_at_its_cavity(analysed):
    import pandas as pd

    analysis, result = analysed
    found = analysis.findings
    # The fourfold axis, found from the tetramer's own shape.
    assert found["axis"]["along"].startswith("the principal axis of the selection")
    narrowest = found["narrowest"]
    # The selectivity filter: the four subunits' TVGYG glycines, too narrow
    # for a water (potassium passes it stripped of its own).
    assert narrowest["mean_radius_nm"] < 0.115
    lining = {(one["residue"], one["chain"]) for one in narrowest["lined_by"]}
    assert {("GLY77", chain) for chain in range(4)} <= lining
    assert found["open_to_water"]["share_of_frames"] == 0.0
    profile = pd.read_csv(analysis.output_dir / "pore_radius.dat")
    assert list(profile.columns) == ["height_nm", "mean_nm", "sd_nm", "min_nm", "max_nm",
                                     "frames"]
    # The water-filled cavity under the filter, about 5 angstroms in radius
    # (Doyle et al., Science 280, 69 (1998)).
    followed = profile[(profile["height_nm"] >= found["pore"]["from_nm"])
                       & (profile["height_nm"] <= found["pore"]["to_nm"])]
    # The filter is the narrow stretch; the cavity is past its inner end,
    # away from the mouth the pore opens to outside the cell.
    mouth = followed["height_nm"].iloc[[0, -1]].to_numpy()
    inward = 1.0 if (abs(mouth[0] - narrowest["height_nm"])
                     < abs(mouth[1] - narrowest["height_nm"])) else -1.0
    past = (followed["height_nm"] - narrowest["height_nm"]) * inward
    cavity = followed[(past > 0.3) & (past < 2.2)]
    assert 0.40 <= cavity["mean_nm"].max() <= 0.60
    assert {path.name for path in result.artifacts} >= {
        "pore_profile.dat", "pore_radius.dat", "pore_path.npz", "pore_radius.png",
        "options.json"}


def test_a_channel_that_drifts_and_turns_keeps_its_pore(analysed, kcsa, tmp_path):
    """Each frame is put in the protein's own frame: the same pore, wherever
    the box has carried it."""
    from fastmdxplora.analysis.pore_profile import PoreProfile

    analysis, result = analysed
    series = np.asarray(result.data)
    assert series.shape == (FRAMES,) and np.isfinite(series).all()
    still = PoreProfile(output_dir=tmp_path)
    alone = still.run(kcsa)
    assert alone.status == "ok"
    assert np.allclose(series, np.asarray(alone.data)[0], atol=0.01)
    assert analysis.findings["fitted_on"]["of"] == "its alpha carbons"
    with np.load(analysis.output_dir / "pore_path.npz") as path:
        assert path["radius_nm"].shape == (FRAMES, len(path["height_nm"]))
        assert path["centre_nm"].shape == path["radius_nm"].shape + (3,)
        centres = path["centre_nm"]
    # The centre line is the first frame's, in every frame.
    spread = np.nanstd(centres, axis=0)
    assert np.nanmedian(spread) < 0.02
    assert analysis.findings["mean"]["unit"] == "nm"


def test_it_runs_only_where_a_study_names_it():
    from fastmdxplora.analysis import available_analyses
    from fastmdxplora.analysis.pore_profile import PoreProfile
    from fastmdxplora.config.schema import ANALYSIS_NAMES

    assert "pore_profile" in available_analyses()
    assert "pore_profile" in ANALYSIS_NAMES
    # Whether a protein is a channel is not in its frames.
    assert PoreProfile.requires_naming is True
    assert PoreProfile.time_series is True


def test_a_start_in_the_open_is_refused_with_what_to_name(tmp_path):
    from fastmdxplora.analysis.pore_profile import PoreProfile

    # A flat sheet of alpha carbons encloses nothing: beside it is the open.
    topology = md.Topology()
    chain = topology.add_chain()
    side = np.arange(-1.0, 1.0001, 0.25)
    sheet = np.array([[x, y, 0.0] for x in side for y in side], dtype=np.float32)
    for number in range(len(sheet)):
        residue = topology.add_residue("ALA", chain, resSeq=number + 1)
        topology.add_atom("CA", md.element.carbon, residue)
    analysis = PoreProfile(output_dir=tmp_path, axis="x")
    result = analysis.run(md.Trajectory(sheet[None] + 5.0, topology))
    assert result.status == "error"
    assert "No pore was found" in result.message
    assert "`start_selection`" in result.message


def test_options_that_mean_nothing_are_refused(tmp_path):
    from fastmdxplora.analysis.pore_profile import PoreProfile
    from fastmdxplora.refusals import StudyError

    for options, word in (({"axis": "w"}, "`axis`"), ({"step_nm": 0.0}, "`step_nm`"),
                          ({"end_radius_nm": 50.0}, "`end_radius_nm`")):
        with pytest.raises(StudyError) as refused:
            PoreProfile(output_dir=tmp_path, **options)
        assert word in str(refused.value)


def test_the_start_can_be_named(kcsa, tmp_path):
    from fastmdxplora.analysis.pore_profile import PoreProfile

    named = PoreProfile(output_dir=tmp_path / "named", start_selection="resSeq 77 and name CA")
    result = named.run(kcsa)
    assert result.status == "ok", result.message
    assert named.findings["narrowest"]["mean_radius_nm"] < 0.115
    nothing = PoreProfile(output_dir=tmp_path / "nothing", start_selection="resname ZZZ")
    assert "matched no atoms" in nothing.run(kcsa).message


# ---------------------------------------------------------------------------
# The Viewer
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def study(kcsa, tmp_path_factory) -> Path:
    from fastmdxplora.analysis import AnalysisOrchestrator
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = tmp_path_factory.mktemp("channel") / "study"
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    trajectory = _carried(kcsa)
    kcsa.save_pdb(str(root / "setup" / "input.pdb"))
    kcsa.save_pdb(str(root / "setup" / "topology.pdb"))
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    simulation = root / "simulation"
    results = AnalysisOrchestrator(str(simulation / "production.dcd"),
                                   str(simulation / "trajectory_topology.pdb"),
                                   output_dir=str(root / "analysis")).run(
        include=["pore_profile"])
    assert [r.status for r in results.values() if r.name == "pore_profile"] == ["ok"]
    return root


def _read_dx(text: str) -> tuple[np.ndarray, np.ndarray, float]:
    lines = text.splitlines()
    counts = [int(v) for v in lines[0].split()[-3:]]
    origin = np.array([float(v) for v in lines[1].split()[1:]])
    spacing = float(lines[2].split()[1])
    start = next(i for i, line in enumerate(lines) if "data follows" in line) + 1
    values = []
    for line in lines[start:]:
        if line.startswith("attribute"):
            break
        values.extend(float(v) for v in line.split())
    return np.array(values).reshape(counts), origin, spacing


def test_the_profile_is_given_in_angstroms_frame_by_frame(study):
    from fastmdxplora.gui.pore import pore_profile

    said = pore_profile(study)
    assert said["ok"], said
    assert (said["narrow"], said["one_water"]) == (1.15, 2.3)
    assert len(said["heights"]) == len(said["mean"]) == len(said["low"]) == len(said["high"])
    assert said["narrowest"]["radius"] < 1.15
    assert any(one.startswith("GLY77 (chain ") for one in said["narrowest"]["lined_by"])
    assert all(low <= mean <= high for low, mean, high
               in zip(said["low"], said["mean"], said["high"]) if mean is not None)
    # Every frame played has the profile of the frame analysed nearest it.
    assert len(said["profiles"]) == FRAMES
    assert all(len(profile) == len(said["heights"]) for profile in said["profiles"])
    assert "which is room for one and not conduction" in said["said"]


def test_the_mean_pore_is_placed_on_the_first_frame_played(study):
    from fastmdxplora.gui.occupancy import _frame_xyz
    from fastmdxplora.gui.pore import pore_surface

    text, reason = pore_surface(study)
    assert text is not None, reason
    depth, origin, spacing = _read_dx(text)
    inside = np.argwhere(depth > 0) * spacing + origin
    assert len(inside) > 100
    # In the pore, there is no protein: no atom of the first frame played
    # is inside the surface by more than the grid allows.
    from scipy.spatial import cKDTree

    atoms = _frame_xyz(study, 0) * 10.0
    nearest, _ = cKDTree(atoms).query(inside)
    deep = depth[depth > 0]
    assert (nearest - deep > 1.7 - 1.0).all()
    # And it lies along the channel: within the protein's own extent, but
    # for the spheres at its mouths, none wider than where the path ends.
    assert (inside.min(axis=0) > atoms.min(axis=0) - 10).all()
    assert (inside.max(axis=0) < atoms.max(axis=0) + 10).all()


def test_a_study_without_the_analysis_says_how_to_have_it(tmp_path):
    from fastmdxplora.gui.pore import pore_profile, pore_surface

    said = pore_profile(tmp_path)
    assert not said["ok"]
    assert said["reason"].startswith("The study has no pore profile")
    assert "`analysis.include`" in said["reason"]
    assert pore_surface(tmp_path)[0] is None


def test_the_server_gives_the_profile_and_the_pore(study):
    import urllib.error
    import urllib.request

    from fastmdxplora.gui.server import (_READ_FROM_THE_RUN_SHOWN,
                                         GETS_ANSWERED_BEYOND_LOOPBACK,
                                         start_dashboard_session)

    assert {"/api/pore-profile", "/structure/pore.dx"} <= GETS_ANSWERED_BEYOND_LOOPBACK
    assert {"/api/pore-profile", "/structure/pore.dx"} <= _READ_FROM_THE_RUN_SHOWN
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)

    def get(path):
        try:
            with urllib.request.urlopen(session.url + path, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""

    try:
        status, body = get("/api/pore-profile")
        assert status == 200 and json.loads(body)["ok"]
        status, grid = get("/structure/pore.dx")
        assert status == 200
        assert grid.startswith(b"object 1 class gridpositions counts ")
    finally:
        session.server.shutdown()


def test_the_viewer_plots_the_pore_and_shows_it(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => !document.getElementById('side-pore').hidden")
            hooks.tool(page, "side-pore")
            page.wait_for_function("() => !!window.FastMDXPore.state.data")
            note = page.inner_text("#pore-note")
            now = page.inner_text("#pore-now")
            key = page.locator("#pore-key li").all_inner_texts()
            # Something is drawn: the plot is not blank.
            inked = page.evaluate(
                "() => { const c = document.getElementById('pore-canvas');"
                " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
                " let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n += 1;"
                " return n; }")
            page.check("#pore-shown")
            page.wait_for_function(f"() => {state}.engine.volumesShown()"
                                   ".some((v) => v.key === 'pore' && v.rendered)")
            superposed = page.evaluate(f"() => {state}.superposed")
            # Frames as written are not fitted to the first: set aside.
            page.select_option("#traj-superpose", "none")
            page.wait_for_function(f"() => {state}.engine.volumesShown()"
                                   ".every((v) => !v.rendered)")
            page.select_option("#traj-superpose", "backbone")
            page.wait_for_function(f"() => {state}.engine.volumesShown()"
                                   ".every((v) => v.rendered)")
            page.uncheck("#pore-shown")
            page.wait_for_function(f"() => {state}.engine.volumesShown().length === 0")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert "Narrowest on average" in note and "GLY77" in note
    assert "too narrow for a water." in now
    assert len(key) == 3 and key[1] == "The frame shown"
    assert inked > 500
    assert superposed == "backbone"
