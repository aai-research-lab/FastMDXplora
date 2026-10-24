"""Every frame's solute is checked against its own periodic images, as
``gmx mindist -pi`` checks it, and every analysis of those frames reads what
the check found.

A chignolin chain came to 0.261 nm of its own copy in a study whose check of
the chain's two ends passed it.
"""

from __future__ import annotations

import itertools
import json

import mdtraj as md
import numpy as np
import pytest
from scipy.spatial.distance import cdist

import fastmdxplora.analysis  # noqa: F401 -- registers the analyses
from fastmdxplora.analysis.image_check import (
    CLOSE_NM,
    CUTOFF_NM,
    SoluteImage,
    solute_image_record,
)


def _chain(path: np.ndarray, *, bonded: bool = True) -> md.Topology:
    """One residue per point of ``path``, a CB beside each CA and a hydrogen
    on it, the heavy atoms bonded along the chain."""
    top = md.Topology()
    chain = top.add_chain()
    previous = None
    for i in range(len(path)):
        residue = top.add_residue("ALA", chain, resSeq=i + 1)
        ca = top.add_atom("CA", md.element.carbon, residue)
        cb = top.add_atom("CB", md.element.carbon, residue)
        top.add_atom("HA", md.element.hydrogen, residue)
        if bonded:
            top.add_bond(ca, cb)
            if previous is not None:
                top.add_bond(previous, ca)
        previous = ca
    return top


def _positions(path: np.ndarray) -> np.ndarray:
    """CA on the path, CB 0.15 nm off it in z, HA 0.1 nm off it in -z."""
    xyz = []
    for point in path:
        xyz += [point, point + [0.0, 0.0, 0.15], point - [0.0, 0.0, 0.1]]
    return np.asarray(xyz, dtype=np.float64)


def _dodecahedron(a: float) -> np.ndarray:
    """OpenMM's reduced rhombic dodecahedron, as setup builds it."""
    return np.array([[a, 0, 0], [0, a, 0], [a / 2, a / 2, a * np.sqrt(2) / 2]])


def _octahedron(a: float) -> np.ndarray:
    """OpenMM's reduced truncated octahedron."""
    return np.array([[a, 0, 0], [a / 3, 2 * np.sqrt(2) * a / 3, 0],
                     [-a / 3, np.sqrt(2) * a / 3, np.sqrt(6) * a / 3]])


def _brute(points: np.ndarray, box: np.ndarray, cells: int = 3) -> float:
    """Every pair against every image within three cells, no shortcuts."""
    best = np.inf
    for shift in itertools.product(range(-cells, cells + 1), repeat=3):
        if shift == (0, 0, 0):
            continue
        image = points + np.asarray(shift, dtype=np.float64) @ box
        best = min(best, float(cdist(points, image).min()))
    return best


def _wandering(n_frames: int, box: np.ndarray, seed: int,
               bonded: bool = True) -> tuple[md.Trajectory, np.ndarray]:
    """A 14-residue chain, a random walk of 0.38 nm steps from compact to
    stretched along x, each frame turned at random, moved, and then wrapped
    atom by atom into the cell, as an engine may write it. With it, each
    frame's whole chain as the trajectory stores it: the wrapped coordinates
    put back by the whole cells each atom was moved, in the trajectory's own
    cell, in float64."""
    rng = np.random.default_rng(seed)
    frames = []
    for f in range(n_frames):
        steps = rng.normal(size=(14, 3))
        steps[:, 0] += 4.5 * f / max(n_frames - 1, 1)
        steps /= np.linalg.norm(steps, axis=1)[:, None]
        path = np.cumsum(0.38 * steps, axis=0)
        q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        frames.append(_positions(path) @ q.T + rng.uniform(-2, 2, 3))
    xyz = np.asarray(frames)
    boxes = np.repeat(box[None], n_frames, axis=0)
    cells = np.floor(np.einsum("fai,fij->faj", xyz, np.linalg.inv(boxes)))
    wrapped = xyz - np.einsum("faj,fji->fai", cells, boxes)
    traj = md.Trajectory(wrapped.astype(np.float32),
                         _chain(np.zeros((14, 3)), bonded=bonded))
    traj.unitcell_vectors = boxes.astype(np.float32)
    traj.time = np.arange(n_frames) * 25.0
    stored = np.asarray(traj.unitcell_vectors, dtype=np.float64)
    whole = traj.xyz.astype(np.float64) + np.einsum("faj,fji->fai", cells, stored)
    # Split across the cell's faces as stored, so making it whole is tested.
    assert np.linalg.norm(np.diff(traj.xyz[:, ::3], axis=1), axis=2).max() > 1.0
    return traj, whole


class TestTheDistanceIsTheBruteForceOne:
    @pytest.mark.parametrize("box", [
        np.diag([3.2, 3.2, 3.2]),
        _dodecahedron(3.4),
        _octahedron(3.6),
    ], ids=["cube", "dodecahedron", "octahedron"])
    def test_to_a_millionth_of_a_nanometre(self, box):
        traj, whole = _wandering(6, box, seed=1)
        heavy = traj.topology.select("not element H")
        distances, record = solute_image_record(traj, np.arange(traj.n_atoms))
        cells = np.asarray(traj.unitcell_vectors, dtype=np.float64)
        for f in range(traj.n_frames):
            expected = _brute(whole[f][heavy], cells[f])
            assert distances[f] == pytest.approx(expected, abs=1e-6), (f, expected)
        assert record["frames"] == 6
        assert record["made_whole"] == "along its bonds"
        assert record["nearest_nm"] == pytest.approx(distances.min())

    def test_hydrogens_are_not_counted(self):
        traj, whole = _wandering(3, np.diag([3.0, 3.0, 3.0]), seed=2)
        distances, record = solute_image_record(traj, np.arange(traj.n_atoms))
        heavy = traj.topology.select("not element H")
        assert record["heavy_atoms"] == heavy.size == 28
        cells = np.asarray(traj.unitcell_vectors, dtype=np.float64)
        for f in range(3):
            assert distances[f] == pytest.approx(_brute(whole[f][heavy], cells[f]), abs=1e-6)

    def test_without_bonds_the_chain_is_walked_in_order(self):
        traj, whole = _wandering(4, _dodecahedron(3.4), seed=3, bonded=False)
        distances, record = solute_image_record(traj, np.arange(traj.n_atoms))
        heavy = traj.topology.select("not element H")
        assert "atom order" in record["made_whole"]
        cells = np.asarray(traj.unitcell_vectors, dtype=np.float64)
        for f in range(4):
            assert distances[f] == pytest.approx(_brute(whole[f][heavy], cells[f]), abs=1e-6)


def _stretched(n_frames: int = 40, box: float = 4.0, reaching: int = 10,
               touching: int = 3) -> md.Trajectory:
    """A 10-residue chain, CA 0.38 nm apart along x in a cube: folded back on
    itself in most frames, stretched straight (3.42 nm, the CB at its ends
    0.58 nm from the image of the other end) in ``reaching`` of them, and in
    ``touching`` of those pushed to 0.4 nm."""
    straight = np.c_[0.38 * np.arange(10), np.zeros(10), np.zeros(10)]
    hairpin = np.r_[straight[:5], straight[:5][::-1] + [0.0, 0.5, 0.0]]
    frames = []
    for f in range(n_frames):
        if f < reaching:
            path = straight.copy()
            if f < touching:
                path[:, 0] *= (box - 0.4) / (9 * 0.38)
        else:
            path = hairpin
        frames.append(_positions(path) + 0.3)
    traj = md.Trajectory(np.asarray(frames, dtype=np.float32), _chain(straight))
    traj.unitcell_lengths = np.full((n_frames, 3), box, dtype=np.float32)
    traj.unitcell_angles = np.full((n_frames, 3), 90.0, dtype=np.float32)
    traj.time = np.arange(n_frames) * 10.0
    return traj


class TestAChainAcrossASmallBox:
    def test_the_frames_under_the_cutoff_are_counted(self):
        traj = _stretched()
        distances, record = solute_image_record(traj, np.arange(traj.n_atoms))
        assert record["reaches_its_image"] is True
        assert record["frames_under_cutoff"] == 10
        assert record["frames_under_close"] == 3
        assert record["fraction_under_cutoff"] == pytest.approx(0.25)
        assert record["nearest_nm"] == pytest.approx(0.4, abs=1e-5)
        assert record["nearest_frame"] in (0, 1, 2)
        assert record["nearest_time_ns"] == pytest.approx(record["nearest_frame"] / 100)
        assert {record["nearest_atoms"][0].split()[0],
                record["nearest_atoms"][1].split()[0]} == {"ALA1", "ALA10"}
        assert "sees its image, interacting with its copy" in record["said"]
        assert "10 of 40" in record["said"] and "in contact with it (under 0.5 nm) in 3" in record["said"]
        assert np.all(distances[10:] > CUTOFF_NM)

    def test_a_chain_clear_of_its_image_says_so(self):
        traj = _stretched(reaching=0, touching=0)
        _, record = solute_image_record(traj, np.arange(traj.n_atoms))
        assert record["reaches_its_image"] is False
        assert "no closer than" in record["said"]

    def test_it_is_a_series_with_its_figure(self, tmp_path):
        result = SoluteImage(output_dir=tmp_path).run(_stretched())
        assert result.status == "ok", result.message
        assert np.loadtxt(result.data_path).shape == (40,)
        assert result.figure_path.is_file()
        found = json.loads((tmp_path / "image_check" / "options.json").read_text())["findings"]
        assert found["solute_image"]["frames_under_cutoff"] == 10
        # No mean of the closest approach: its findings are the minimum and
        # the counts, and its figure is not given a mean line.
        assert "mean" not in found

    def test_the_close_threshold_is_half_a_nanometre(self):
        assert CLOSE_NM == 0.5 and CUTOFF_NM == 1.0


def _hairpin_reaching_its_image() -> md.Trajectory:
    """A hairpin whose two ends sit side by side (their images 2.6 nm away)
    and whose turn, 2.4 nm up, comes 0.6 nm from the image of its base: the
    ends pass, the chain does not."""
    up = np.c_[np.zeros(8), 0.343 * np.arange(8), np.zeros(8)]
    down = (up + [0.4, 0.0, 0.0])[::-1]
    path = np.r_[up, down]
    top = md.Topology()
    chain = top.add_chain()
    previous = None
    for i in range(len(path)):
        residue = top.add_residue("ALA", chain, resSeq=i + 1)
        atom = top.add_atom("CA", md.element.carbon, residue)
        if previous is not None:
            top.add_bond(previous, atom)
        previous = atom
    rng = np.random.default_rng(0)
    xyz = path[None] + 0.2 + rng.normal(0.0, 0.005, (200, len(path), 3))
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.unitcell_lengths = np.full((200, 3), 3.0, dtype=np.float32)
    traj.unitcell_angles = np.full((200, 3), 90.0, dtype=np.float32)
    traj.time = np.arange(200) * 10.0
    return traj


class TestTheEndToEndDistanceReadsIt:
    def test_a_chain_whose_ends_pass_is_marked_by_the_whole_chain(self, tmp_path):
        from fastmdxplora.analysis.end_to_end import EndToEndDistance

        analysis = EndToEndDistance(output_dir=tmp_path)
        assert analysis.run(_hairpin_reaching_its_image()).status == "ok"
        found = analysis.findings
        assert found["periodic"]["nearest_self_image_nm"] > 2.0     # the ends
        assert found["periodic"]["nearest_self_image_whole_chain_nm"] < 1.0
        assert "periodic image" in found["not_a_measurement"]
        assert not np.isfinite(found["mean"]["standard_error"])
        assert found["mean"]["error_withheld_because"] == "chain sees its periodic image"

    def test_handed_the_finding_it_reads_it_rather_than_its_own(self):
        from fastmdxplora.analysis.end_to_end import EndToEndDistance

        analysis = EndToEndDistance()
        traj = _stretched(reaching=0, touching=0)
        analysis.solute_image = {"reaches_its_image": True, "cutoff_nm": 1.0,
                                 "nearest_nm": 0.3}
        # Handed a series meeting the image in two frames, where the chain
        # measured here does not.
        analysis.solute_image_series = np.r_[np.full(traj.n_frames - 2, 3.0), [0.3, 0.6]]
        analysis.result = analysis.compute(traj)
        analysis._record_what_the_mean_is_worth(traj)
        assert "periodic image in 2 of" in analysis.findings["mean"]["not_a_measurement"]


@pytest.fixture(scope="module")
def analysed(tmp_path_factory):
    from fastmdxplora.analysis import AnalysisOrchestrator

    folder = tmp_path_factory.mktemp("study")
    traj = _hairpin_reaching_its_image()
    traj[0].save_pdb(str(folder / "top.pdb"))
    traj.save_dcd(str(folder / "traj.dcd"))
    orchestrator = AnalysisOrchestrator(str(folder / "traj.dcd"), str(folder / "top.pdb"),
                                        output_dir=folder / "analysis")
    results = orchestrator.run(include=["rg", "end_to_end", "cluster", "image_check"])
    return folder / "analysis", results


class TestEveryAnalysisOfTheFramesReadsIt:
    def _found(self, folder, name):
        return json.loads((folder / name / "options.json").read_text())["findings"]

    def test_the_check_runs_first(self, analysed):
        _, results = analysed
        assert list(results)[0] == "image_check"
        assert all(r.status == "ok" for r in results.values()), {
            n: r.message for n, r in results.items()}

    def test_each_mean_keeps_its_error_and_carries_the_note(self, analysed):
        folder, _ = analysed
        found = self._found(folder, "rg")
        assert np.isfinite(found["mean"]["standard_error"])
        assert "error_withheld_because" not in found["mean"]
        assert "periodic image" not in str(found["mean"].get("not_a_measurement") or "")
        note = found["solute_image"]
        assert note["frames_used"] == 200 - found["mean"]["discard"]
        assert note["frames_under_cutoff"] == note["frames_used"]
        assert "shape" in note["said"] and note["may_bias"] == "shape"
        assert found["mean"]["solute_image"]["frames_under_cutoff"] == note["frames_under_cutoff"]
        assert "said" not in found["mean"]["solute_image"]

    def test_end_to_end_alone_withholds_its_error(self, analysed):
        folder, _ = analysed
        mean = self._found(folder, "end_to_end")["mean"]
        assert not np.isfinite(mean["standard_error"])
        assert mean["error_withheld_because"] == "chain sees its periodic image"
        assert self._found(folder, "end_to_end")["solute_image"]["said"] in mean["not_a_measurement"]

    def test_an_analysis_with_no_mean_says_it_too(self, analysed):
        folder, _ = analysed
        found = self._found(folder, "cluster")
        assert found["solute_image"]["frames_used"] == 200
        assert "sees its image" in found["solute_image"]["said"]

    def test_the_report_says_it(self, analysed):
        from fastmdxplora.report.document import _findings_notes

        folder, _ = analysed
        paragraph = self._found(folder, "image_check")["solute_image"]["said"]
        notes = _findings_notes(self._found(folder, "image_check"))
        assert notes.count(paragraph) == 1
        for name in ("rg", "cluster"):
            notes = _findings_notes(self._found(folder, name))
            assert not any(paragraph in note for note in notes), name
            pointers = [note for note in notes if "(see Image check)" in note]
            assert len(pointers) == 1, (name, notes)
            assert "may be biased" in pointers[0]

    def test_a_study_without_a_box_does_not_plan_it(self, tmp_path):
        from fastmdxplora.analysis import AnalysisOrchestrator

        traj = _hairpin_reaching_its_image()
        traj.unitcell_vectors = None
        traj[0].save_pdb(str(tmp_path / "top.pdb"))
        traj.save_dcd(str(tmp_path / "traj.dcd"))
        orchestrator = AnalysisOrchestrator(str(tmp_path / "traj.dcd"), str(tmp_path / "top.pdb"),
                                            output_dir=tmp_path / "analysis")
        assert "image_check" not in orchestrator._build_plan(None, None)

    def test_a_study_with_a_box_plans_it_first(self, analysed):
        from fastmdxplora.analysis import AnalysisOrchestrator

        folder, _ = analysed
        orchestrator = AnalysisOrchestrator(str(folder.parent / "traj.dcd"),
                                            str(folder.parent / "top.pdb"),
                                            output_dir=folder.parent / "again")
        assert orchestrator._build_plan(None, None)[0] == "image_check"


def _chains_spanning(span: float, box: np.ndarray, seed: int,
                     n_chains: int = 2) -> md.Trajectory:
    """``n_chains`` chains of 0.38 nm steps, one after the other, together
    spanning about ``span`` nm along a random direction, one frame, whole."""
    rng = np.random.default_rng(seed)
    direction = rng.normal(size=3)
    direction /= np.linalg.norm(direction)
    per_chain = 16
    top = md.Topology()
    xyz = []
    start = np.zeros(3)
    for _ in range(n_chains):
        chain = top.add_chain()
        previous = None
        steps = rng.normal(size=(per_chain, 3)) * 0.6 + direction * (
            span / (n_chains * per_chain * 0.38))
        steps = 0.38 * steps / np.linalg.norm(steps, axis=1)[:, None]
        path = start + np.cumsum(steps, axis=0)
        for i, point in enumerate(path):
            residue = top.add_residue("ALA", chain, resSeq=i + 1)
            atom = top.add_atom("CA", md.element.carbon, residue)
            if previous is not None:
                top.add_bond(previous, atom)
            previous = atom
            xyz.append(point)
        # The next chain begins beside where this one ended, in contact with
        # it (its first atom under 0.5 nm from this one's last), so the two
        # are one complex.
        start = path[-1] + 0.1 * direction
    traj = md.Trajectory(np.asarray(xyz, dtype=np.float32)[None], top)
    traj.unitcell_vectors = box[None].astype(np.float32)
    return traj


def _brute_far(points: np.ndarray, box: np.ndarray, cells: int) -> float:
    shifts = np.array([s for s in itertools.product(range(-cells, cells + 1), repeat=3)
                       if any(s)], dtype=np.float64) @ box
    best = np.inf
    for shift in shifts:
        best = min(best, float(cdist(points, points + shift).min()))
    return best


class TestEveryImageWithinReachIsSearched:
    """A solute spanning several cells is searched as far as it reaches: a
    fixed two cells either way left lattice vectors unsearched that are
    shorter than ones searched (in a dodecahedron 2.24 d against 4.47 d)."""

    @pytest.mark.parametrize("shape,cell", [
        ("cube", lambda d: np.diag([d, d, d])),
        ("dodecahedron", _dodecahedron),
        ("octahedron", _octahedron),
    ])
    def test_as_far_as_the_solute_spans(self, shape, cell):
        wrong = []
        for k, factor in enumerate((1.5, 1.8, 2.1, 2.4, 2.7, 3.0, 3.4, 3.8)):
            for seed in range(3):
                box = np.asarray(cell(2.0), dtype=np.float64)
                widths = 1.0 / np.sqrt((np.linalg.inv(box) ** 2).sum(axis=0))
                traj = _chains_spanning(factor * widths.min(), box, seed=100 * k + seed)
                found, _ = solute_image_record(traj, np.arange(traj.n_atoms))
                points = traj.xyz[0].astype(np.float64)
                cells = np.asarray(traj.unitcell_vectors[0], dtype=np.float64)
                # Every lattice vector shorter than |a| + 2r (|a| bounds the
                # closest approach) has |n_i| <= that times |column i| of
                # the inverse cell: brute force over all of those.
                radius = np.sqrt(((points - points.mean(axis=0)) ** 2).sum(axis=1).max())
                reach = np.linalg.norm(cells[0]) + 2 * radius
                far = int(np.ceil(reach * np.sqrt((np.linalg.inv(cells) ** 2).sum(axis=0)).max()))
                expected = _brute_far(points, cells, cells=far)
                if abs(found[0] - expected) > 1e-6:
                    wrong.append((factor, seed, float(found[0]), expected))
        assert not wrong, wrong


class TestALongLatticeVectorIsSearched:
    """A chain laid along the lattice vector 3a + b, its end 0.2 nm short of
    its own image there: the closest image is three cells out along a, which
    a fixed two cells either way never searched, in any cell shape."""

    @pytest.mark.parametrize("shape,cell", [
        ("cube", lambda d: np.diag([d, d, d])),
        ("dodecahedron", _dodecahedron),
        ("octahedron", _octahedron),
    ])
    def test_three_cells_out(self, shape, cell):
        box = np.asarray(cell(2.0), dtype=np.float64)
        vector = 3 * box[0] + box[1]
        length = float(np.linalg.norm(vector))
        steps = int(np.ceil((length - 0.2) / 0.3))
        path = np.outer(np.linspace(0.0, length - 0.2, steps + 1), vector / length)
        top = _chain(path, bonded=True)
        traj = md.Trajectory(_positions(path)[None].astype(np.float32), top)
        traj.unitcell_vectors = box[None].astype(np.float32)
        found, record = solute_image_record(traj, np.arange(traj.n_atoms))
        heavy = [a.index for a in top.atoms if a.element.symbol != "H"]
        points = traj.xyz[0].astype(np.float64)[heavy]
        cells = np.asarray(traj.unitcell_vectors[0], dtype=np.float64)
        expected = _brute_far(points, cells, cells=4)
        assert abs(found[0] - expected) < 1e-6, (shape, float(found[0]), expected)
        assert record["most_cells_searched"] >= 3


def _study_with_setup(folder, setup: dict | None, traj: md.Trajectory):
    """A study folder: its trajectory, and setup's record where given."""
    if setup is not None:
        (folder / "setup").mkdir()
        (folder / "setup" / "setup_parameters.json").write_text(json.dumps(setup))
    traj[0].save_pdb(str(folder / "top.pdb"))
    traj.save_dcd(str(folder / "traj.dcd"))
    return folder


def _ca_line(box: float = 4.52, n_frames: int = 40, reaching: int = 10) -> md.Trajectory:
    """Ten alpha carbons 0.38 nm apart (as a PDB without bonds reads back,
    walked in order): straight along x in ``reaching`` frames, the ends
    then ``box - 3.42`` nm from each other's image, folded back otherwise."""
    straight = np.c_[0.38 * np.arange(10), np.zeros(10), np.zeros(10)]
    hairpin = np.r_[straight[:5], straight[:5][::-1] + [0.0, 0.5, 0.0]]
    top = md.Topology()
    chain = top.add_chain()
    for i in range(10):
        top.add_atom("CA", md.element.carbon, top.add_residue("ALA", chain, resSeq=i + 1))
    xyz = np.asarray([straight if f < reaching else hairpin for f in range(n_frames)]) + 0.3
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.unitcell_lengths = np.full((n_frames, 3), box, dtype=np.float32)
    traj.unitcell_angles = np.full((n_frames, 3), 90.0, dtype=np.float32)
    traj.time = np.arange(n_frames) * 10.0
    return traj


class TestTheRunsOwnCutoff:
    """A CHARMM36 run is cut off at 1.2 nm: a solute 1.1 nm from its image
    is within it, which a check at 1.0 nm called outside."""

    def _checked(self, folder, setup):
        from fastmdxplora.analysis import AnalysisOrchestrator

        # Stretched, the chain's ends come 1.1 nm from each other's image.
        study = _study_with_setup(folder, setup, _ca_line())
        AnalysisOrchestrator(str(study / "traj.dcd"), str(study / "top.pdb"),
                             output_dir=study / "analysis").run(include=["image_check"])
        return json.loads((study / "analysis" / "image_check" / "options.json")
                          .read_text())["findings"]["solute_image"]

    def test_as_the_run_set_it(self, tmp_path):
        found = self._checked(tmp_path, {"parameters": {"nonbonded_cutoff_nm": 1.2}})
        assert found["cutoff_nm"] == 1.2
        assert found["frames_under_cutoff"] == 10
        assert "setup/setup_parameters.json" in found["cutoff_from"]
        assert "as set" in found["cutoff_from"]

    def test_as_setup_resolved_it(self, tmp_path):
        found = self._checked(tmp_path, {"parameters": {"nonbonded_cutoff_nm": None},
                                         "resolved": {"nonbonded_cutoff_nm": 1.2}})
        assert found["cutoff_nm"] == 1.2 and found["frames_under_cutoff"] == 10
        assert "resolved" in found["cutoff_from"]

    def test_from_the_force_field_where_no_cutoff_is_recorded(self, tmp_path):
        found = self._checked(tmp_path, {"parameters": {"forcefield": "charmm36"}})
        assert found["cutoff_nm"] == 1.2
        assert "force field" in found["cutoff_from"]

    def test_a_shorter_cutoff_counts_fewer(self, tmp_path):
        found = self._checked(tmp_path, {"parameters": {"nonbonded_cutoff_nm": 0.9}})
        assert found["cutoff_nm"] == 0.9 and found["frames_under_cutoff"] == 0

    def test_without_a_record_one_nanometre_said_as_taken(self, tmp_path):
        found = self._checked(tmp_path, None)
        assert found["cutoff_nm"] == 1.0 and found["frames_under_cutoff"] == 0
        assert found["cutoff_from"].startswith("taken because")
        assert f"The cutoff is 1 nm, {found['cutoff_from']}." in found["said"]

    def test_a_run_given_setup_from_reads_the_record_every_reader_finds(self, tmp_path):
        """A run given `setup_from` prepared nothing: its system's record is
        where its manifest says setup was taken from, as
        `setup_records_of` finds it for the report and the GUI."""
        from fastmdxplora.analysis import AnalysisOrchestrator
        from fastmdxplora.simulation.pipeline import setup_records_of

        system = tmp_path / "system" / "setup"
        system.mkdir(parents=True)
        (system / "setup_parameters.json").write_text(json.dumps(
            {"parameters": {"nonbonded_cutoff_nm": None},
             "resolved": {"nonbonded_cutoff_nm": 1.2}}))
        run = tmp_path / "run1"
        run.mkdir()
        (run / "manifest.json").write_text(json.dumps(
            {"phases": [{"name": "setup", "taken_from": str(system)}]}))
        study = _study_with_setup(run, None, _ca_line())
        assert setup_records_of(study) == system
        AnalysisOrchestrator(str(study / "traj.dcd"), str(study / "top.pdb"),
                             output_dir=study / "analysis").run(include=["image_check"])
        found = json.loads((study / "analysis" / "image_check" / "options.json")
                           .read_text())["findings"]["solute_image"]
        assert found["cutoff_nm"] == 1.2 and found["frames_under_cutoff"] == 10
        assert "setup_parameters.json" in found["cutoff_from"]
        assert "The cutoff is 1.2 nm, read from" in found["said"]

    def test_one_set_for_the_check_wins(self, tmp_path):
        from fastmdxplora.analysis import AnalysisOrchestrator

        study = _study_with_setup(tmp_path, {"parameters": {"nonbonded_cutoff_nm": 1.2}},
                                  _ca_line())
        AnalysisOrchestrator(str(study / "traj.dcd"), str(study / "top.pdb"),
                             output_dir=study / "analysis").run(
            include=["image_check"], options={"image_check": {"cutoff_nm": 1.05}})
        found = json.loads((study / "analysis" / "image_check" / "options.json")
                           .read_text())["findings"]["solute_image"]
        assert found["cutoff_nm"] == 1.05 and "set for this check" in found["cutoff_from"]


def _polymer_in_water(resname: str, atom: str, element, *, bonded: bool,
                      n_frames: int = 20) -> md.Trajectory:
    """Ten residues of ``resname`` (one heavy atom each, 0.38 nm apart),
    straight along x in a 4.0 nm cube in the first 5 frames (ends 0.58 nm
    from each other's image) and folded otherwise; a water and a sodium ion
    beside it, the ion 0.3 nm from its own image in every frame."""
    top = md.Topology()
    chain = top.add_chain()
    previous = None
    for i in range(10):
        here = top.add_atom(atom, element, top.add_residue(resname, chain, resSeq=i + 1))
        if bonded and previous is not None:
            top.add_bond(previous, here)
        previous = here
    water = top.add_residue("HOH", top.add_chain(), resSeq=1)
    top.add_atom("O", md.element.oxygen, water)
    ion = top.add_residue("NA", top.add_chain(), resSeq=1)
    top.add_atom("NA", md.element.sodium, ion)
    straight = np.c_[0.38 * np.arange(10), np.zeros(10), np.zeros(10)]
    folded = np.r_[straight[:5], straight[:5][::-1] + [0.0, 0.5, 0.0]]
    frames = []
    for f in range(n_frames):
        chain_xyz = (straight if f < 5 else folded) + 0.3
        frames.append(np.r_[chain_xyz, [[2.0, 2.0, 2.0]], [[3.85, 2.5, 2.5]]])
    traj = md.Trajectory(np.asarray(frames, dtype=np.float32), top)
    traj.unitcell_lengths = np.full((n_frames, 3), 4.0, dtype=np.float32)
    traj.unitcell_angles = np.full((n_frames, 3), 90.0, dtype=np.float32)
    traj.time = np.arange(n_frames) * 10.0
    return traj


class TestTheSoluteIsTheMacromolecules:
    """An RNA, or a peptide of residues MDTraj does not call protein, is a
    solute as a protein is; water and ions are not."""

    @pytest.mark.parametrize("resname,atom,element,bonded", [
        ("U", "P", md.element.phosphorus, False),
        ("XAA", "CA", md.element.carbon, True),
    ], ids=["rna", "non-standard peptide"])
    def test_it_is_checked(self, tmp_path, resname, atom, element, bonded):
        traj = _polymer_in_water(resname, atom, element, bonded=bonded)
        # As the orchestrator hands it the scope's solute, "protein".
        analysis = SoluteImage(output_dir=tmp_path, selection="protein")
        result = analysis.run(traj)
        assert result.status == "ok", result.message
        found = analysis.findings["solute_image"]
        assert found["heavy_atoms"] == 10
        assert found["frames_under_cutoff"] == 5
        assert found["nearest_nm"] == pytest.approx(0.58, abs=1e-5)
        assert "water, ions and lipids left out" in found["solute"]

    def test_the_orchestrator_plans_it(self, tmp_path):
        from fastmdxplora.analysis import AnalysisOrchestrator

        traj = _polymer_in_water("U", "P", md.element.phosphorus, bonded=False)
        traj[0].save_pdb(str(tmp_path / "top.pdb"))
        traj.save_dcd(str(tmp_path / "traj.dcd"))
        orchestrator = AnalysisOrchestrator(str(tmp_path / "traj.dcd"), str(tmp_path / "top.pdb"),
                                            output_dir=tmp_path / "analysis")
        assert "image_check" in orchestrator._build_plan(None, None)

    def test_a_ligand_counts_only_in_the_analyses_solute(self):
        from fastmdxplora.analysis.image_check import solute_atoms

        traj = _polymer_in_water("ALA", "CA", md.element.carbon, bonded=True)
        top = traj.topology
        assert len(solute_atoms(top, "protein")[0]) == 10
        # The ion, named in a selection, is still not the solute.
        assert len(solute_atoms(top, "protein or resname NA")[0]) == 10
        top = top.copy()
        top.add_atom("C1", md.element.carbon, top.add_residue("LIG", top.add_chain(), resSeq=1))
        assert len(solute_atoms(top, "protein")[0]) == 10
        atoms, said = solute_atoms(top, "protein or resname LIG")
        assert len(atoms) == 11 and "LIG" in said


class TestItIsInEveryPlan:
    """A study that lists its analyses (as the GUI writes them) was never
    checked: the check runs with any plan where there is a box and a solute,
    and excluded by name, that is said."""

    def _study(self, folder):
        traj = _hairpin_reaching_its_image()
        traj[0].save_pdb(str(folder / "top.pdb"))
        traj.save_dcd(str(folder / "traj.dcd"))
        return folder

    def test_a_listed_plan_runs_it_first(self, tmp_path):
        from fastmdxplora.analysis import AnalysisOrchestrator

        study = self._study(tmp_path)
        orchestrator = AnalysisOrchestrator(str(study / "traj.dcd"), str(study / "top.pdb"),
                                            output_dir=study / "analysis")
        results = orchestrator.run(include=["rg"])
        assert list(results) == ["image_check", "rg"]
        found = json.loads((study / "analysis" / "rg" / "options.json").read_text())["findings"]
        assert found["solute_image"]["frames_under_cutoff"] > 0

    def test_without_a_box_a_listed_plan_is_as_listed(self, tmp_path):
        from fastmdxplora.analysis import AnalysisOrchestrator

        traj = _hairpin_reaching_its_image()
        traj.unitcell_vectors = None
        traj[0].save_pdb(str(tmp_path / "top.pdb"))
        traj.save_dcd(str(tmp_path / "traj.dcd"))
        orchestrator = AnalysisOrchestrator(str(tmp_path / "traj.dcd"), str(tmp_path / "top.pdb"),
                                            output_dir=tmp_path / "analysis")
        assert orchestrator._build_plan(["rg"], None) == ["rg"]

    def test_excluded_by_name_it_is_said(self, tmp_path):
        from fastmdxplora.analysis import AnalysisOrchestrator
        from fastmdxplora.analysis.orchestrator import IMAGE_CHECK_EXCLUDED
        from fastmdxplora.report.document import _results_section

        study = self._study(tmp_path)
        orchestrator = AnalysisOrchestrator(str(study / "traj.dcd"), str(study / "top.pdb"),
                                            output_dir=study / "analysis")
        everything_else = [n for n in fastmdxplora.analysis.available_analyses()
                           if n not in ("rg",)]
        results = orchestrator.run(exclude=everything_else)
        assert "image_check" not in results
        manifest = json.loads((study / "analysis" / "analysis_manifest.json").read_text())
        assert manifest["image_check_left_out"] == IMAGE_CHECK_EXCLUDED
        assert "not checked against its own periodic images" in IMAGE_CHECK_EXCLUDED
        assert "not checked against its own periodic images" in _results_section(study)
        # The setting as code, not escaped into its characters.
        assert "`exclude: [image_check]`" in _results_section(study)

    def test_the_setting_s_help_says_it(self):
        from fastmdxplora.config.schema import ANALYSIS

        help_of = next(f for f in ANALYSIS.fields if f.name == "exclude").help
        assert "image_check" in help_of and "listed one too" in help_of


class TestANoteNotAWithheldError:
    """Meeting its own copy may bias a mean; it does not make the mean's
    error unknown. Every mean keeps its error, and carries a note counted
    over the frames it uses."""

    def _rg(self, n: int = 100):
        from fastmdxplora.analysis.rg import Rg

        traj = _ca_line(n_frames=n, reaching=0)
        analysis = Rg()
        analysis.solute_image = {"reaches_its_image": True, "cutoff_nm": 1.2}
        analysis.findings["mean"] = {"n_frames": n, "discard": 40, "mean": 1.0,
                                     "standard_error": 0.01, "unit": "nm"}
        return analysis, traj

    def test_counted_over_the_frames_the_mean_uses(self):
        analysis, traj = self._rg()
        # Within the cutoff in 10 frames of the equilibration and 5 after it.
        series = np.full(100, 2.0)
        series[[1, 5, 9, 12, 20, 22, 25, 30, 33, 39]] = 0.9
        series[[50, 60, 70, 80, 90]] = 1.1
        series[60] = 0.4
        analysis.solute_image_series = series
        analysis._qualify_by_the_solute_image(traj)
        note = analysis.findings["solute_image"]
        assert note["frames_used"] == 60 and note["frames_under_cutoff"] == 5
        assert note["nearest_nm"] == pytest.approx(0.4) and note["nearest_frame"] == 60
        assert "in 5 of 60 frames used (8%)" in note["said"]
        mean = analysis.findings["mean"]
        assert mean["standard_error"] == 0.01 and "not_a_measurement" not in mean
        assert mean["solute_image"]["frames_under_cutoff"] == 5

    def test_none_where_only_the_discarded_frames_met_it(self):
        analysis, traj = self._rg()
        series = np.full(100, 2.0)
        series[:40] = 0.3
        analysis.solute_image_series = series
        analysis._qualify_by_the_solute_image(traj)
        assert "solute_image" not in analysis.findings
        assert "solute_image" not in analysis.findings["mean"]

    def test_the_time_is_in_ps_under_a_nanosecond(self):
        analysis, traj = self._rg()
        series = np.full(100, 2.0)
        series[50] = 0.5
        analysis.solute_image_series = series
        analysis._qualify_by_the_solute_image(traj)
        assert "frame 50, 500 ps" in analysis.findings["solute_image"]["said"]

    def test_the_run_s_ensemble_gets_the_plain_note(self):
        from fastmdxplora.analysis.image_check import note_on_frames

        finding = {"reaches_its_image": True, "cutoff_nm": 1.0}
        plain = note_on_frames(finding, [0.5, 2.0], analysis="thermodynamics")
        shape = note_on_frames(finding, [0.5, 2.0], analysis="rmsf")
        assert plain["said"].endswith(
            "so a mean over such frames may be biased by it.")
        assert "shape" not in plain["said"] and plain["may_bias"] == "mean"
        assert "shape" in shape["said"] and shape["may_bias"] == "shape"

    def test_within_the_cutoff_it_sees_its_image_and_touches_it_only_under_half_a_nm(self):
        from fastmdxplora.analysis.image_check import note_on_frames

        finding = {"reaches_its_image": True, "cutoff_nm": 1.0}
        seen = note_on_frames(finding, [0.9, 0.8, 2.0], analysis="rg")
        assert "sees its image" in seen["said"] and "contact" not in seen["said"]
        assert "meet" not in seen["said"]
        touching = note_on_frames(finding, [0.9, 0.4, 2.0], analysis="rg")
        assert "in contact with it (under 0.5 nm) in 1;" in touching["said"]

    def test_every_mean_record_of_an_analysis_carries_it(self, tmp_path):
        """SASA's hydrophobic and polar means and the moments' shape
        descriptors kept their errors and said nothing, beside the main mean
        given without one: the same number shown twice, two verdicts."""
        from fastmdxplora.analysis import AnalysisOrchestrator

        traj = _hairpin_reaching_its_image()
        traj[0].save_pdb(str(tmp_path / "top.pdb"))
        traj.save_dcd(str(tmp_path / "traj.dcd"))
        AnalysisOrchestrator(str(tmp_path / "traj.dcd"), str(tmp_path / "top.pdb"),
                             output_dir=tmp_path / "analysis").run(
            include=["sasa", "moments_of_inertia"])
        for name in ("sasa", "moments_of_inertia"):
            found = json.loads((tmp_path / "analysis" / name / "options.json")
                               .read_text())["findings"]
            means = {key: value for key, value in found.items()
                     if isinstance(value, dict) and "n_frames" in value and "mean" in value}
            assert len(means) > 1, (name, list(found))
            for key, record in means.items():
                assert record["solute_image"]["frames_under_cutoff"] > 0, (name, key)
                # Withheld for nothing of the image (a molecule with no bonds
                # past half the cell is another matter, said as that).
                assert "image" not in str(record.get("error_withheld_because")), (name, key)

    def test_the_reweighted_averages_carry_it(self, tmp_path, monkeypatch):
        from fastmdxplora.analysis import reweighted_averages as rw
        from fastmdxplora.analysis.orchestrator import _REGISTRY
        from fastmdxplora.analysis.reweight import Weights

        n = 200
        rng = np.random.default_rng(0)
        series = 1.0 + 0.05 * rng.normal(size=n)
        monkeypatch.setattr(rw, "biasing_method", lambda _: "metadynamics")
        monkeypatch.setattr(rw, "weights_for_run", lambda *_: (
            Weights(values=np.ones(n), effective_sample_size=float(n), converged=True),
            {"cv_statistical_inefficiency": 1.0}))

        class Result:
            status = "ok"
            data = {"total": series}

        import pandas as pd

        Result.data = pd.DataFrame({"total": series})
        distances = np.full(n, 2.0)
        distances[150:160] = 0.8
        record = rw.reweight_results(
            {"rg": Result()}, _REGISTRY, n_frames=n,
            frame_times_ps=np.arange(n) * 10.0, output_dir=tmp_path,
            solute_image={"reaches_its_image": True, "cutoff_nm": 1.0},
            solute_image_series=distances)
        item = record["quantities"][0]
        assert item["solute_image"]["frames_under_cutoff"] == 10
        assert item["solute_image"]["frames_used"] == n
        assert "sees its image" in item["solute_image"]["said"]


class TestTheReportPageSaysWhy:
    """The GUI's Report page called every qualified mean "too short to be
    determined", the reason the record gives being the image."""

    def _found(self, tmp_path, name, findings):
        from fastmdxplora.gui.report_dashboard import _what_the_analysis_found

        folder = tmp_path / "analysis" / name
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps({"findings": findings}))
        (folder / f"{name}.dat").write_text("1.0\n")
        return _what_the_analysis_found(folder / f"{name}.dat")

    def test_a_withheld_mean_says_the_record_s_reason(self, tmp_path):
        said = self._found(tmp_path, "end_to_end", {"mean": {
            "n_frames": 100, "mean": 2.5, "standard_error": float("nan"),
            "not_a_measurement": "The chain ... meeting its copy.",
            "error_withheld_because": "chain sees its periodic image"}})
        assert "too short" not in said
        assert said.startswith("mean 2.5") and "not determined: chain sees its periodic image" in said

    def test_a_noted_mean_keeps_its_error_and_says_it_may_be_biased(self, tmp_path):
        said = self._found(tmp_path, "rg", {
            "mean": {"n_frames": 100, "mean": 1.0, "standard_error": 0.01,
                     "effective_samples": 40.0},
            "solute_image": {"frames_used": 60, "frames_under_cutoff": 5,
                             "cutoff_nm": 1.0, "may_bias": "shape", "said": "..."}})
        assert "±" in said
        assert said.endswith("; may be biased: in 5 of 60 frames used (8%) the solute was "
                             "within 1 nm of its own image")


class TestHowItIsSaid:
    def test_a_time_under_a_nanosecond_is_in_picoseconds(self):
        traj = _stretched()
        _, record = solute_image_record(traj, np.arange(traj.n_atoms))
        frame = record["nearest_frame"]
        assert f"frame {frame}, {frame * 10} ps" in record["said"]
        assert " ns" not in record["said"]

    def test_the_first_sentence_says_what_matters_whole(self):
        """A page giving a reason's first sentence (the Overview's tiles) cut
        it inside its bracket, at the colon: "(frame 299."."""
        from fastmdxplora.gui.overview_view import _in_a_line

        traj = _stretched()
        _, record = solute_image_record(traj, np.arange(traj.n_atoms))
        first = _in_a_line(record["said"])
        assert first.count("(") == first.count(")")
        assert "10 of 40 frames" in first and "cutoff" in first
        assert "heavy atoms" in record["said"]

    def test_end_to_end_s_reason_leads_with_the_frames(self, tmp_path):
        from fastmdxplora.analysis.end_to_end import EndToEndDistance
        from fastmdxplora.gui.overview_view import _in_a_line

        analysis = EndToEndDistance(output_dir=tmp_path)
        assert analysis.run(_hairpin_reaching_its_image()).status == "ok"
        first = _in_a_line(analysis.findings["mean"]["not_a_measurement"])
        assert first.startswith("The solute came within") and "frames used" in first
        assert first.count("(") == first.count(")")

    def test_the_report_s_heading(self, analysed):
        from fastmdxplora.report.document import _results_section

        folder, _ = analysed
        text = _results_section(folder.parent)
        assert "### Image check" in text and "Image\\_Check" not in text


class TestTheMapsLastCell:
    def test_is_drawn_its_own_width(self):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from fastmdxplora.analysis.cluster import _plot_distance_matrix

        fig, ax = plt.subplots()
        # 2,001 frames in blocks of 2: the last block holds one frame.
        frames = np.arange(2001)
        _plot_distance_matrix(ax, np.zeros((1001, 1001)), frames, None, block=2)
        mesh = ax.collections[0]
        xs = mesh.get_coordinates()[0, :, 0]
        assert xs[0] == pytest.approx(-0.5) and xs[-1] == pytest.approx(2000.5)
        assert xs[-1] - xs[-2] == pytest.approx(1.0)
        plt.close(fig)


def _copies(starts: np.ndarray, length: float = 2.2, n: int = 10,
            box: float = 6.0, n_frames: int = 3) -> md.Trajectory:
    """One straight chain of ``n`` alpha carbons, ``length`` nm along x,
    from each of ``starts``, each its own molecule (bonded within), wrapped
    into a cube of side ``box`` atom by atom as an engine may write it."""
    top = md.Topology()
    xyz = []
    for start in starts:
        chain = top.add_chain()
        previous = None
        for i in range(n):
            atom = top.add_atom("CA", md.element.carbon,
                                top.add_residue("ALA", chain, resSeq=i + 1))
            if previous is not None:
                top.add_bond(previous, atom)
            previous = atom
            xyz.append(start + [length * i / (n - 1), 0.0, 0.0])
    xyz = np.asarray(xyz) % box
    traj = md.Trajectory(np.repeat(xyz[None], n_frames, axis=0).astype(np.float32), top)
    traj.unitcell_vectors = np.repeat(np.diag([box] * 3)[None], n_frames,
                                      axis=0).astype(np.float32)
    traj.time = np.arange(n_frames) * 10.0
    return traj


class TestSeparateCopiesAreNotOneComplex:
    """Eight 2.2 nm peptides on a 2 by 2 by 2 grid in a 6 nm cube: each
    comes no closer than 3.80 nm to its own images, while the eight taken as
    one complex came 0.80 nm to theirs. A copy meeting another copy's image
    is contact between molecules in the box, not a self-image."""

    def test_eight_copies_are_each_checked_against_their_own_images(self):
        from fastmdxplora.analysis.image_check import solute_atoms

        starts = np.array([[0.4 + 3 * i, 1.5 + 3 * j, 1.5 + 3 * k]
                           for i in range(2) for j in range(2) for k in range(2)])
        traj = _copies(starts)
        atoms, solute = solute_atoms(traj.topology, None)
        series, found = solute_image_record(traj, atoms, solute_said=solute)
        assert found["complexes"] == 8 and found["molecules"] == 8
        assert np.allclose(series, 3.8, atol=1e-5)
        assert found["frames_under_cutoff"] == 0 and not found["reaches_its_image"]
        assert "8 complexes of 1 molecule each" in found["said"]
        # Said of each complex, not of the eight taken together.
        assert found["said"].startswith("No complex of the solute came closer than 3.800 nm "
                                        "to its own periodic image, outside the 1 nm cutoff")
        # What the solute is, then how it was grouped, then what is left out.
        assert found["said"].endswith("not counted; water, ions and lipids left out.")
        assert "not a self-image" in found["said"]
        # Taken together, they would have come 0.80 nm to their images.
        union = traj.xyz[0].astype(np.float64)
        assert abs(_brute(union, np.diag([6.0] * 3), cells=1) - 0.8) < 1e-5

    def test_the_grouping_says_it_is_not_made_again_as_the_run_goes(self):
        """Grouped once, in the first frame read: a dimer that parts is
        still checked as one, and an oligomer formed later as its molecules,
        which the finding said nowhere."""
        from fastmdxplora.analysis.image_check import _grouping_said

        for groups in ([[0, 1]], [[0], [1]], [[0, 1], [2]]):
            said = _grouping_said(groups)
            assert ("in the first frame read being one complex for the whole run, so a "
                    "complex that parts or a ligand that unbinds may still be noted, and "
                    "molecules that join later are checked apart") in said, said
            # One bracket deep.
            assert said.count("(") == 1, said
        assert _grouping_said([[0]]) == "checked as one molecule against its own images"

    def test_a_dimer_in_contact_is_one_complex(self):
        """Two 2.2 nm chains end to end, 0.1 nm apart, in a 5 nm cube: one
        complex, checked as the union is, through each frame, though wrapped
        apart as stored. The union comes 0.5 nm from its own image and
        neither chain alone within 2.8 nm of its own, so only the union
        checked gives the series."""
        from fastmdxplora.analysis.image_check import solute_atoms

        starts = np.array([[0.3, 2.0, 2.0], [2.6, 2.0, 2.0]])
        traj = _copies(starts, length=2.2, box=5.0, n_frames=4)
        # Drift the dimer across the boundary, frame by frame, together.
        for f in range(4):
            traj.xyz[f] = (traj.xyz[0] + [0.9 * f, 0.9 * f, 0.0]) % 5.0
        atoms, solute = solute_atoms(traj.topology, None)
        series, found = solute_image_record(traj, atoms, solute_said=solute)
        assert found["complexes"] == 1 and found["molecules"] == 2
        assert "one complex of 2 molecules" in found["said"]
        line = 2.2 * np.arange(10) / 9
        chains = [np.c_[start + line, np.full(10, 2.0), np.full(10, 2.0)]
                  for start in (0.3, 2.6)]
        box = np.diag([5.0] * 3)
        expected = _brute(np.r_[chains[0], chains[1]], box, cells=1)
        assert abs(expected - 0.5) < 1e-5
        # Each chain alone stays outside the cutoff of its own image.
        assert all(abs(_brute(chain, box, cells=1) - 2.8) < 1e-5 for chain in chains)
        assert np.allclose(series, expected, atol=1e-5)
        assert found["frames_under_cutoff"] == 4


class TestEndToEndWithTheCheckExcluded:
    """With the image check excluded by name, the end-to-end distance checks
    its own chain: against the run's cutoff (0.9 nm in a study it said 1 nm
    of), and pointing to no Image check section, which is not there."""

    def test_it_reads_the_run_s_cutoff_and_points_nowhere(self, tmp_path):
        from fastmdxplora.analysis import AnalysisOrchestrator
        from fastmdxplora.analysis.orchestrator import _REGISTRY
        from fastmdxplora.report.document import _findings_notes

        study = _study_with_setup(tmp_path, {"parameters": {"nonbonded_cutoff_nm": 1.2}},
                                  _ca_line())
        results = AnalysisOrchestrator(str(study / "traj.dcd"), str(study / "top.pdb"),
                                       output_dir=study / "analysis").run(
            exclude=[name for name in _REGISTRY if name != "end_to_end"])
        assert list(results) == ["end_to_end"], list(results)
        found = json.loads((study / "analysis" / "end_to_end" / "options.json")
                           .read_text())["findings"]
        note = found["solute_image"]
        assert note["cutoff_nm"] == 1.2 and note["read_from"] == "end_to_end"
        # At 1.0 nm none of the frames would count: the ends are 1.1 nm apart.
        discard = found["mean"]["discard"]
        assert note["frames_under_cutoff"] == 10 - discard > 0
        assert "The cutoff is 1.2 nm, read from" in found["mean"]["not_a_measurement"] or \
            note["said"] in found["mean"]["not_a_measurement"]
        notes = _findings_notes(found)
        assert not any("see Image check" in line for line in notes), notes
        assert sum("of its own periodic image in" in line for line in notes) == 1, notes

    def test_with_the_check_its_line_is_said_once(self, analysed):
        from fastmdxplora.report.document import _findings_notes

        folder, _ = analysed
        found = json.loads((folder / "end_to_end" / "options.json").read_text())["findings"]
        notes = _findings_notes(found)
        assert sum("of its own periodic image in" in line for line in notes) == 1, notes


class TestTheReportSaysWhatTheImageLeaves:
    """The report's Convergence part listed RMSD, Rg and SASA as wanting
    only a longer run, for a run under 0.5 nm of its image in every frame."""

    def test_a_run_in_contact_in_every_frame_supports_no_shape_claim(self, tmp_path):
        from fastmdxplora.analysis import AnalysisOrchestrator
        from fastmdxplora.report.document import _convergence_section

        # Ends 0.18 nm from each other's image in every frame.
        study = _study_with_setup(tmp_path, None, _ca_line(box=3.6, n_frames=200, reaching=200))
        AnalysisOrchestrator(str(study / "traj.dcd"), str(study / "top.pdb"),
                             output_dir=study / "analysis").run(include=["rg"])
        finding = json.loads((study / "analysis" / "image_check" / "options.json")
                             .read_text())["findings"]["solute_image"]
        assert finding["frames_under_close"] == finding["frames"] == 200
        text = _convergence_section(study)
        assert "in contact with it (under 0.5 nm) in 200: a mean above" in text
        assert "(see Image check)" in text
        assert "Any claim about the solute's shape" in text
        assert "A larger box, in a new study" in text

    def test_its_figure_s_time_is_in_ps_under_a_nanosecond(self):
        traj = _hairpin_reaching_its_image()
        traj.time = np.arange(traj.n_frames) * 1.0
        check = SoluteImage()
        check._traj_for_plot = traj
        x, label = check._axis()
        assert label == "Time (ps)"
        assert x[-1] == pytest.approx(float(traj.time[-1]))
