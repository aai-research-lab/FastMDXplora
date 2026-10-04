"""Another study beside this one in the Viewer: a wild type and its mutant.

Two studies were compared by their means, and their structures one window
at a time. The other study's frames are now played beside this one's: its
residues paired by sequence, the substituted ones listed, fitted on the
paired alpha carbons to this study's first frame, and timed by simulation
time; this study's protein can be coloured by the difference in RMSF.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"


_UNPACKED: list[Path] = []


def _protein() -> md.Trajectory:
    if not _UNPACKED:
        import tempfile

        raw = Path(tempfile.mkdtemp()) / "3PTB.pdb"
        raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
        _UNPACKED.append(raw)
    whole = md.load_pdb(str(_UNPACKED[0]))
    return whole.atom_slice(whole.topology.select("protein"))


def _study(root: Path, protein: md.Trajectory, frames: int, *, seed: int, turn: float,
           wobble: float) -> Path:
    """One nanosecond of ``protein`` in ``frames`` frames, turned by ``turn``
    radians about z and moved, its first fifty residues wobbling by
    ``wobble`` nm along x."""
    from fastmdxplora.analysis import AnalysisOrchestrator
    from fastmdxplora.gui.trajectory_frames import frames_info

    (root / "simulation").mkdir(parents=True)
    rng = np.random.default_rng(seed)
    moving = protein.topology.select("resSeq 16 to 65")
    rotation = np.array([[np.cos(turn), -np.sin(turn), 0], [np.sin(turn), np.cos(turn), 0],
                         [0, 0, 1]])
    xyz = np.repeat(protein.xyz, frames, axis=0)
    for frame in range(frames):
        xyz[frame, moving, 0] += wobble * np.sin(2 * np.pi * frame / frames)
    xyz += rng.normal(0, 0.01, xyz.shape)
    xyz = xyz @ rotation.T + [2.0, 0.5, 0.0]
    trajectory = md.Trajectory(xyz.astype(np.float32), protein.topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps(
        {"duration_ns_actual": 1.0}), encoding="utf-8")
    (root / "resolved_config.yml").write_text("systems:\n  - system: 3PTB\n", encoding="utf-8")
    assert frames_info(root, simulation_time_ns_total=1.0)["available"]
    AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                         str(root / "simulation" / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(include=["rmsf"])
    return root


@pytest.fixture(scope="module")
def pair(tmp_path_factory) -> tuple[Path, Path]:
    work = tmp_path_factory.mktemp("beside")
    wild = _study(work / "wild", _protein(), 40, seed=1, turn=0.0, wobble=0.1)
    protein = _protein()
    protein = protein.atom_slice(protein.topology.select("not resSeq 100"))
    leucine = next(r for r in protein.topology.residues if r.resSeq == 105)
    leucine.name = "ALA"
    mutant = _study(work / "mutant", protein, 20, seed=2, turn=0.8, wobble=0.3)
    return wild, mutant


def test_residues_are_paired_by_sequence():
    from fastmdxplora.gui.beside import paired

    def residues(letters):
        return [{"letter": letter} for letter in letters]

    pairs, mutations, unpaired = paired(residues("ACDEFGHIK"), residues("ACDQFGIK"))
    assert mutations == [(3, 3)]
    assert unpaired == 1
    assert (8, 7) in pairs and len(pairs) == 8


def test_the_mutant_beside_the_wild_type(pair):
    from fastmdxplora.gui.beside import BESIDE, beside

    wild, mutant = pair
    said = beside(wild, mutant, most_frames=2000)
    assert said["ok"], said
    assert said["name"] == "mutant" and said["frames"] == 40
    assert said["unpaired"] == 1
    residue = next(r for r in _protein().topology.residues if r.resSeq == 105)
    assert said["mutations"] == [{"chain": "A", "resi": 105, "icode": "", "from": residue.name,
                                  "to": "ALA", "theirs": 105}]
    assert "each frame beside the other's nearest it in simulation time" in said["said"]
    assert said["said"].startswith(f"mutant, its {said['paired']:,} residues paired with this "
                                   "study's by sequence (1 differ, 1 unpaired)")
    # Fitted on the paired alpha carbons to this study's first frame.
    key = said["key"]
    fitted = md.load_dcd(str(wild / BESIDE / f"{key}.dcd"), top=str(wild / BESIDE / f"{key}.pdb"))
    first = md.load_dcd(str(wild / "simulation" / "frames.dcd"),
                        top=str(wild / "simulation" / "frames_topology.pdb"), frame=0)
    mine = first.xyz[0, first.topology.select("name CA and not resSeq 16 to 65 "
                                              "and not resSeq 100")]
    theirs = fitted.xyz[0, fitted.topology.select("name CA and not resSeq 16 to 65")]
    assert np.sqrt(((mine - theirs) ** 2).sum(axis=1).mean()) * 10 < 0.6
    # Timed: the mutant saved half as many frames over the same nanosecond.
    assert fitted.n_frames == 40
    # Its first fifty residues wobble three times as far: they move more.
    difference = said["property"]
    assert difference["label"] == "RMSF, mutant less this study" and difference["unit"] == "nm"
    rows = {row[1]: row[3] for row in difference["values"]}
    assert np.median([rows[n] for n in range(16, 66) if n in rows]) > 0.05
    assert difference["low"] == -difference["high"]
    # Written once.
    stamp = (wild / BESIDE / f"{key}.dcd").stat().st_mtime_ns
    assert beside(wild, mutant, most_frames=2000) == said
    assert (wild / BESIDE / f"{key}.dcd").stat().st_mtime_ns == stamp


def test_what_cannot_be_beside_is_said(pair, tmp_path):
    from fastmdxplora.gui.beside import beside, beside_file

    wild, mutant = pair
    assert beside(wild, tmp_path, most_frames=2000)["reason"] == f"{tmp_path} is not a study."
    assert beside(wild, wild, most_frames=2000)["reason"] == "That is the study shown."
    empty = tmp_path / "empty"
    (empty / "simulation").mkdir(parents=True)
    (empty / "resolved_config.yml").write_text("systems: []\n")
    assert beside(wild, empty, most_frames=2000)["reason"] == "empty has no frames to play yet."
    assert beside(empty, wild, most_frames=2000)["reason"] == (
        "This study has no frames to play beside yet.")
    assert beside_file("../../x", ".dcd") is None
    assert beside_file("0123456789abcdef", ".exe") is None
    assert beside_file("0123456789abcdef", ".pdb") == "0123456789abcdef.pdb"


def test_the_server_gives_it_on_loopback_only(pair):
    import urllib.error
    import urllib.parse
    import urllib.request

    from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

    assert "/api/beside" not in GETS_ANSWERED_BEYOND_LOOPBACK
    wild, mutant = pair
    session = start_dashboard_session(output=str(wild), host="127.0.0.1", port=0)

    def get(path):
        try:
            with urllib.request.urlopen(session.url + path, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""

    try:
        said = json.loads(get("/api/beside?" + urllib.parse.urlencode({"path": str(mutant)}))[1])
        assert said["ok"]
        status, frames = get(said["coordinates"])
        assert status == 200 and frames == (wild / "viewer_beside" / f"{said['key']}.dcd") \
            .read_bytes()
        assert get(said["topology"])[0] == 200
        assert get("/structure/beside.dcd?key=nothing")[0] == 404
        assert json.loads(get("/api/beside")[1]) == {"ok": False, "reason": "No study was named."}
    finally:
        session.server.shutdown()


def test_the_viewer_plays_them_together(pair):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    wild, mutant = pair
    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(wild), host="127.0.0.1", port=0)
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
            page.wait_for_function("() => !document.getElementById('side-beside').hidden")
            offered = page.locator("#beside-study option").all_inner_texts()
            page.click("#beside-show")
            page.wait_for_function(f"() => {state}.engine.runsShown('beside').length === 1"
                                   f" && {state}.engine.runsShown('beside')[0].rendered")
            mutations = page.locator("#beside-mutations li").all_inner_texts()
            page.check("#beside-colour")
            page.wait_for_function(f"() => {state}.colorMode === 'result:rmsf-difference'")
            legend = page.inner_text("#viewer-legend").splitlines()[0]
            page.click("#beside-remove")
            page.wait_for_function(f"() => {state}.engine.runsShown('beside').length === 0"
                                   f" && {state}.colorMode !== 'result:rmsf-difference'")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert offered == ["mutant (3PTB)"]
    assert len(mutations) == 1 and mutations[0].endswith("105 → ALA")
    assert legend == "RMSF, mutant less this study (nm)"


def test_without_a_clock_or_an_rmsf(pair, tmp_path):
    import shutil

    from fastmdxplora.gui.beside import _difference, _residues, _timed, paired
    from fastmdxplora.gui.trajectory_frames import _atom_lines, _read

    chosen, said = _timed({"frame_times_ns": [None] * 5, "n_frames_browser": 5},
                          {"frame_times_ns": [None] * 3, "n_frames_browser": 3})
    assert chosen == [0, 0, 1, 2, 2] and said.endswith("since a run's clock was not recorded")
    wild, mutant = pair
    bare = tmp_path / "bare"
    shutil.copytree(mutant, bare)
    shutil.rmtree(bare / "analysis")
    mine = _residues(_atom_lines(_read(wild / "simulation" / "frames_topology.pdb")))
    theirs = _residues(_atom_lines(_read(bare / "simulation" / "frames_topology.pdb")))
    pairs, _, _ = paired(mine, theirs)
    assert _difference(wild, bare, "bare", mine, theirs, pairs) is None
