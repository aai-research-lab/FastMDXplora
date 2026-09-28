"""A binding free energy says where its reference state does not hold.

The standard-state conversion assumes the ligand in bulk has the whole shell
(or cap) at its radius, in isotropic solvent, reached along a path the bound
state leaves open, with its orientation sampled on the way. C1 (trypsin and
benzamidine) met the first: the cap was 18% to 58% open across the outer
range, about 3 kJ/mol, and the curve was smooth. Each is measured here on
windows written as a study writes them, around a protein of known shape.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.simulation.reference_state import (  # noqa: E402
    CONTACT_NM,
    check_the_reference,
    fibonacci_directions,
    kabsch,
    obstacles_around,
    open_share,
    orientation_samples,
    principal_axis,
    within_cap,
)

BOX_NM = 12.0
CENTRE = np.full(3, BOX_NM / 2)
#: A ball of protein 1 nm across the centre of the box, with its pocket on
#: top.
PROTEIN_RADIUS = 1.0
POCKET = CENTRE + np.array([0.0, 0.0, PROTEIN_RADIUS])
ROD = np.array([[-0.21, 0, 0], [-0.07, 0, 0], [0.07, 0, 0], [0.21, 0, 0]])


def _protein_points(spacing: float = 0.3) -> np.ndarray:
    axis = np.arange(-PROTEIN_RADIUS, PROTEIN_RADIUS + 1e-9, spacing)
    grid = np.array([[x, y, z] for x in axis for y in axis for z in axis])
    inside = grid[np.linalg.norm(grid, axis=1) <= PROTEIN_RADIUS] + CENTRE
    # The residues nearest the pocket first, so the site is `resid 0 to 5`.
    return inside[np.argsort(np.linalg.norm(inside - POCKET, axis=1))]


def _topology(n_residues: int, *, lipids: int = 0) -> "md.Topology":
    topology = md.Topology()
    chain = topology.add_chain()
    carbon, nitrogen, oxygen = (md.element.carbon, md.element.nitrogen,
                                md.element.oxygen)
    for _ in range(n_residues):
        residue = topology.add_residue("ALA", chain)
        for name, element in (("N", nitrogen), ("CA", carbon), ("C", carbon),
                              ("O", oxygen)):
            topology.add_atom(name, element, residue)
    ligand = topology.add_residue("LIG", topology.add_chain())
    for n in range(len(ROD)):
        topology.add_atom(f"C{n}", carbon, ligand)
    if lipids:
        membrane = topology.add_chain()
        for _ in range(lipids):
            topology.add_atom("P", md.element.phosphorus,
                              topology.add_residue("POPC", membrane))
    return topology


def _protein_atoms(points: np.ndarray) -> np.ndarray:
    offsets = np.array([[-0.07, 0, 0], [0, 0.07, 0], [0.07, 0, 0], [0, -0.07, 0]])
    return (points[:, None, :] + offsets[None, :, :]).reshape(-1, 3)


def _rotation(rng: np.random.Generator) -> np.ndarray:
    q, r = np.linalg.qr(rng.normal(size=(3, 3)))
    return q * np.sign(np.diag(r))


def _write_window(directory: Path, frames: np.ndarray, topology: "md.Topology") -> None:
    simulation = directory / "simulation"
    simulation.mkdir(parents=True)
    trajectory = md.Trajectory(
        frames, topology,
        unitcell_lengths=np.full((len(frames), 3), BOX_NM),
        unitcell_angles=np.full((len(frames), 3), 90.0))
    trajectory[0].save_pdb(str(simulation / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(simulation / "production.dcd"))


def _study(root: Path, centres: list[float], *, n_frames: int = 200,
           lipids: int = 0, through: "set[int] | None" = None,
           stuck: "set[int] | None" = None, seed: int = 3) -> tuple[dict, dict]:
    """Windows at ``centres`` from the site, the ligand anywhere open above
    the protein and turned at random, unless the window is ``stuck`` (turning
    slowly) or ``through`` (placed inside the protein)."""
    rng = np.random.default_rng(seed)
    points = _protein_points()
    protein = _protein_atoms(points)
    topology = _topology(len(points), lipids=lipids)
    site = protein[: 6 * 4].mean(axis=0)
    membrane = (np.column_stack((rng.uniform(0, BOX_NM, lipids),
                                 rng.uniform(0, BOX_NM, lipids),
                                 np.full(lipids, 1.0))) if lipids else np.zeros((0, 3)))
    directories, where = {}, {}
    for index, centre in enumerate(centres):
        frames = []
        turned = _rotation(rng)
        for _ in range(n_frames):
            if index in (through or set()):
                position = CENTRE.copy()
            else:
                upward = rng.normal(size=3)
                upward[2] = abs(upward[2]) + 1.0
                position = site + centre * upward / np.linalg.norm(upward)
            if index in (stuck or set()):
                turned = turned @ _rotation_by(rng, 0.01)
            elif index not in (through or set()):
                turned = _rotation(rng)
            ligand = position + ROD @ turned
            frames.append(np.vstack((protein, ligand, membrane)))
        directory = root / f"window-{index:02d}"
        _write_window(directory, np.array(frames, dtype=np.float32), topology)
        directories[index], where[index] = directory, centre
    return directories, where


def _rotation_by(rng: np.random.Generator, angle: float) -> np.ndarray:
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]],
                  [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * k @ k


def _check(directories, centres, *, bulk_from: float, cone=None) -> dict:
    return check_the_reference(
        directories, centres, ligand_resname="LIG", site_selection="resid 0 to 5",
        bound_at_nm=min(centres.values()), bulk_from_nm=bulk_from,
        temperature_K=300.0, cone=cone, allowed_kjmol=0.6)


class TestTheGeometry:

    def test_nothing_in_the_way_is_all_open(self) -> None:
        assert open_share(np.zeros((0, 3)), 2.0, ROD, fibonacci_directions()) == 1.0

    def test_a_wall_through_the_centre_closes_what_is_behind_it(self) -> None:
        """A half-space of atoms below the site: a one-atom ligand at radius
        r is free where it is more than the contact distance above it."""
        axis = np.arange(-3.0, 3.0001, 0.1)
        wall = np.array([[x, y, z] for x in axis for y in axis
                         for z in np.arange(-3.0, 0.0001, 0.1)])
        share = open_share(wall, 2.0, np.zeros((1, 3)), fibonacci_directions())
        assert share == pytest.approx((1.0 - CONTACT_NM / 2.0) / 2.0, abs=0.02)

    def test_a_copy_across_the_boundary_is_in_the_way(self) -> None:
        cell = np.eye(3) * 5.0
        found = obstacles_around(np.array([0.2, 2.5, 2.5]),
                                 np.array([[4.9, 2.5, 2.5]]), cell, reach=1.0)
        assert np.allclose(found, [[-0.3, 0.0, 0.0]])

    def test_a_cap_holds_its_share_of_the_sphere(self) -> None:
        directions = fibonacci_directions()
        cap = within_cap(directions, np.array([0.0, 0.0, 2.0]), 60.0)
        assert len(cap) / len(directions) == pytest.approx(0.25, abs=0.01)

    def test_a_superposition_is_undone(self) -> None:
        rng = np.random.default_rng(1)
        points = rng.normal(size=(20, 3))
        turned = points @ _rotation(rng) + 3.0
        rotation, a, b = kabsch(turned, points)
        assert np.allclose((turned - a) @ rotation + b, points, atol=1e-8)

    def test_a_rod_has_a_longest_axis_and_a_ball_has_none(self) -> None:
        assert abs(principal_axis(ROD) @ [1, 0, 0]) == pytest.approx(1.0)
        tetrahedron = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]])
        assert principal_axis(tetrahedron) is None


class TestTheOrientation:

    def test_orientations_drawn_afresh_are_all_independent(self) -> None:
        rng = np.random.default_rng(2)
        axes = rng.normal(size=(500, 3))
        said = orientation_samples(axes / np.linalg.norm(axes, axis=1)[:, None])
        assert said["resolved"] and said["effective_samples"] > 200

    def test_an_axis_that_wanders_slowly_is_few_samples(self) -> None:
        rng = np.random.default_rng(2)
        axis, axes = np.array([1.0, 0.0, 0.0]), []
        for _ in range(500):
            axis = _rotation_by(rng, 0.01) @ axis
            axes.append(axis)
        said = orientation_samples(np.array(axes))
        assert not said["resolved"] or said["effective_samples"] < 10


class TestTheStudy:

    def test_an_open_shell_passes_and_says_so(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 1.0, 1.8, 3.0, 3.3, 3.6])
        checked = _check(directories, centres, bulk_from=3.0)
        assert checked["refused"] is None, checked["refused"]
        assert checked["shell"]["least_open"]["open_share"] == pytest.approx(1.0)
        assert checked["warnings"] == []

    def test_a_shell_the_protein_fills_is_refused_with_its_cost(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 0.8, 1.2, 1.4, 1.6])
        checked = _check(directories, centres, bulk_from=1.2)
        least = checked["shell"]["least_open"]
        assert least["open_share"] < 0.8
        assert checked["shell"]["error_bound_kjmol"] == pytest.approx(
            0.0083144626 * 300 * math.log(1 / least["open_share"]), rel=1e-6)
        assert "is not open where the curve is taken as bulk" in checked["refused"]

    def test_a_cap_pointing_into_the_solvent_is_open(self, tmp_path) -> None:
        """The same windows, under a cone pointing away from the protein: the
        cap is measured, not the shell."""
        from fastmdxplora.simulation.umbrella import Cone

        directories, centres = _study(tmp_path, [0.4, 0.8, 1.2, 1.4, 1.6])
        checked = _check(directories, centres, bulk_from=1.2,
                         cone=Cone(half_angle_deg=30.0, axis_selection="protein"))
        assert checked["shell"]["within_cone"]
        assert checked["refused"] is None, checked["refused"]

    def test_a_membrane_without_a_cone_is_refused(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 1.0, 3.0, 3.3], lipids=25)
        checked = _check(directories, centres, bulk_from=3.0)
        assert checked["membrane"]
        assert "bilayer" in checked["refused"]

    def test_a_pull_through_the_protein_is_said(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 1.0, 1.8, 3.0, 3.3, 3.6],
                                      through={1})
        checked = _check(directories, centres, bulk_from=3.0)
        assert [w["window"] for w in checked["path"]["through_backbone"]] == [1]
        assert any("through the protein" in w for w in checked["warnings"])

    def test_an_orientation_not_sampled_is_said(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 1.0, 1.8, 3.0, 3.3, 3.6],
                                      stuck={2}, n_frames=400)
        checked = _check(directories, centres, bulk_from=3.0)
        assert [w["window"] for w in checked["orientation"]["unsampled"]] == [2]
        assert any("orientation was not sampled in window 2:" in w
                   for w in checked["warnings"])

    def test_too_few_frames_to_judge_an_orientation_by(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 1.0, 1.8, 3.0, 3.3, 3.6],
                                      stuck={2}, n_frames=60)
        checked = _check(directories, centres, bulk_from=3.0)
        assert checked["orientation"]["unsampled"] == []


class TestTheStudySaysIt:

    def test_the_number_and_its_warnings_are_printed(self) -> None:
        from fastmdxplora.batch.explorer import _what_the_binding_says

        said = _what_the_binding_says({
            "delta_g_kjmol": -20.04, "delta_g_standard_error_kjmol": 1.23,
            "reference": {"warnings": ["The pull took the ligand through."]}})
        assert said == ["Binding:        -20.0 +/- 1.2 kJ/mol (standard state, 1 M)",
                        "Warning:        The pull took the ligand through."]

    def test_a_withheld_number_says_why(self) -> None:
        from fastmdxplora.batch.explorer import _what_the_binding_says

        said = _what_the_binding_says({"delta_g_kjmol": None, "refused": (
            "The shell around the site is not open. No length of run changes this.")})
        assert said == ["Binding:        not reported -- The shell around the "
                        "site is not open."]

    def test_a_refusal_withholds_a_number_already_computed(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.batch import explorer
        from fastmdxplora.simulation import reference_state
        from fastmdxplora.simulation.umbrella import UmbrellaPlan, Window

        monkeypatch.setattr(reference_state, "check_the_reference",
                            lambda *a, **k: {"refused": "Not open.", "warnings": []})
        plan = UmbrellaPlan(windows=(Window(0, 0.4, 1000.0), Window(1, 3.0, 1000.0)),
                            collective_variable="ligand_distance")
        spec = SimpleNamespace(options={"simulation": {"umbrella": {
            "index": 0, "ligand_resname": "LIG", "site_selection": "resid 0"}}})
        this = SimpleNamespace(run_specs=[spec])
        binding = {"delta_g_kjmol": -30.0, "delta_g_kcalmol": -7.2,
                   "minimum_at_nm": 0.4, "bulk_from_nm": 3.0}
        answer = explorer.BatchExplorer._with_the_reference_checked(
            this, binding, {"coordinate": [0.4, 3.0], "free_energy_kjmol": [0.0, 5.0]},
            plan, {0: tmp_path, 1: tmp_path}, temperature=300.0, cone=None)
        assert answer["delta_g_kjmol"] is None and "delta_g_kcalmol" not in answer
        assert answer["refused"] == "Not open."


class TestWhatIsNotJudged:

    def test_the_edges_of_the_geometry(self) -> None:
        assert math.isnan(open_share(np.zeros((1, 3)), 1.0, ROD, np.zeros((0, 3))))
        assert obstacles_around(np.zeros(3), np.zeros((0, 3)), None, 1.0).shape == (0, 3)
        near = obstacles_around(np.zeros(3), np.array([[0.5, 0, 0], [3.0, 0, 0]]), None, 1.0)
        assert np.allclose(near, [[0.5, 0, 0]])
        assert principal_axis(ROD[:2]) is None
        assert orientation_samples(np.ones((2, 3)))["effective_samples"] is None

    def test_no_windows_and_no_trajectories(self, tmp_path) -> None:
        assert check_the_reference(
            {}, {}, ligand_resname="LIG", site_selection="resid 0", bound_at_nm=0.4,
            bulk_from_nm=1.0, temperature_K=300.0, allowed_kjmol=0.6) == {
                "refused": None, "warnings": []}
        (tmp_path / "w").mkdir()
        checked = _check({0: tmp_path / "w"}, {0: 0.4}, bulk_from=1.0)
        assert "not on disk" in checked["not_checked"]

    def test_a_ligand_that_is_not_there(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 3.0], n_frames=5)
        checked = check_the_reference(
            directories, centres, ligand_resname="XYZ", site_selection="resid 0 to 5",
            bound_at_nm=0.4, bulk_from_nm=3.0, temperature_K=300.0, allowed_kjmol=0.6)
        assert "matches no atom" in checked["not_checked"]

    def test_a_window_without_its_trajectory_is_left_out(self, tmp_path) -> None:
        import shutil

        directories, centres = _study(tmp_path, [0.4, 1.0, 3.0, 3.3])
        shutil.rmtree(directories[3] / "simulation")
        (directories[3] / "simulation").mkdir()
        shutil.rmtree(directories[1] / "simulation")
        (directories[1] / "simulation").mkdir()
        checked = _check(directories, centres, bulk_from=3.0)
        assert [s["window"] for s in checked["shell"]["windows"]] == [2]

    def test_a_bound_window_without_its_trajectory_skips_the_path(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 1.0, 3.0, 3.3])
        (directories[1] / "simulation" / "production.dcd").unlink()
        checked = check_the_reference(
            directories, centres, ligand_resname="LIG", site_selection="resid 0 to 5",
            bound_at_nm=1.0, bulk_from_nm=3.0, temperature_K=300.0, allowed_kjmol=0.6)
        assert "path" not in checked and checked["shell"]["windows"]


class TestTheConesAxisAtoms:
    """The cone records its axis as the whole system numbers it, and the
    trajectory, saved without water, numbers it again."""

    @staticmethod
    def _prepared(window: Path, waters: int) -> list[int]:
        """The whole system as setup wrote it, water first, beside the
        window; returns the protein's indices in it."""
        saved = md.load(str(window / "simulation" / "trajectory_topology.pdb"))
        full = md.Topology()
        chain = full.add_chain()
        for _ in range(waters):
            residue = full.add_residue("HOH", chain)
            full.add_atom("O", md.element.oxygen, residue)
        full = full.join(saved.topology)
        xyz = np.vstack((np.zeros((waters, 3)), saved.xyz[0]))[None]
        setup = window / "setup"
        setup.mkdir()
        md.Trajectory(xyz, full).save_pdb(str(setup / "topology.pdb"))
        (setup / "setup_parameters.json").write_text("{}", encoding="utf-8")
        return [waters + int(i) for i in saved.topology.select("protein")]

    def test_they_are_found_through_the_prepared_system(self, tmp_path) -> None:
        from fastmdxplora.simulation.umbrella import Cone

        directories, centres = _study(tmp_path, [0.4, 0.8, 1.2, 1.4, 1.6])
        protein = self._prepared(directories[0], waters=3)
        checked = _check(directories, centres, bulk_from=1.2, cone=Cone(
            half_angle_deg=30.0, axis_selection="nothing", axis_atoms=tuple(protein)))
        assert checked["shell"]["within_cone"] and checked["refused"] is None

    def test_where_they_cannot_be_found_the_shell_is_measured_and_said(self, tmp_path) -> None:
        from fastmdxplora.simulation.umbrella import Cone

        directories, centres = _study(tmp_path, [0.4, 0.8, 1.2, 1.4, 1.6])
        checked = _check(directories, centres, bulk_from=1.2, cone=Cone(
            half_angle_deg=30.0, axis_selection="resname NOPE", axis_atoms=(0, 1, 2)))
        assert not checked["shell"]["within_cone"]
        assert any("axis atoms could not be found" in w for w in checked["warnings"])


class TestTheStudyRecordsTheChecks:

    @staticmethod
    def _checked(tmp_path, monkeypatch, said, *, cone=None, variable="ligand_distance",
                 block=None):
        from fastmdxplora.batch import explorer
        from fastmdxplora.simulation import reference_state
        from fastmdxplora.simulation.umbrella import UmbrellaPlan, Window

        monkeypatch.setattr(reference_state, "check_the_reference", lambda *a, **k: said)
        plan = UmbrellaPlan(windows=(Window(0, 0.4, 1000.0), Window(1, 3.0, 1000.0)),
                            collective_variable=variable)
        spec = SimpleNamespace(options={"simulation": {"umbrella": block or {
            "index": 0, "ligand_resname": "LIG", "site_selection": "resid 0"}}})
        return explorer.BatchExplorer._with_the_reference_checked(
            SimpleNamespace(run_specs=[spec]),
            {"delta_g_kjmol": -30.0, "minimum_at_nm": 0.4, "bulk_from_nm": 3.0},
            {"coordinate": [0.4, 3.0], "free_energy_kjmol": [0.0, 5.0]},
            plan, {0: tmp_path, 1: tmp_path}, temperature=300.0, cone=cone)

    def test_a_number_that_passes_keeps_its_checks_and_the_cones_exit(self, tmp_path,
                                                                      monkeypatch) -> None:
        answer = self._checked(tmp_path, monkeypatch, {"refused": None, "warnings": []},
                               cone=object())
        assert answer["delta_g_kjmol"] == -30.0
        assert "chose one way out" in answer["reference"]["exit"]

    def test_a_distance_between_two_selections_is_not_checked(self, tmp_path,
                                                             monkeypatch) -> None:
        answer = self._checked(tmp_path, monkeypatch, {}, variable="distance")
        assert "not from a ligand" in answer["reference"]["not_checked"]

    def test_a_ligand_not_named_is_found_from_the_trajectory(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.simulation import reference_state

        directories, _ = _study(tmp_path, [0.4, 3.0], n_frames=3)
        seen = {}

        def record(*_a, **k):
            seen.update(k)
            return {"refused": None, "warnings": []}

        monkeypatch.setattr(reference_state, "check_the_reference", record)
        from fastmdxplora.batch import explorer
        from fastmdxplora.simulation.umbrella import UmbrellaPlan, Window

        plan = UmbrellaPlan(windows=(Window(0, 0.4, 1000.0), Window(1, 3.0, 1000.0)),
                            collective_variable="ligand_distance")
        spec = SimpleNamespace(options={"simulation": {"umbrella": {
            "index": 0, "site_selection": "resid 0"}}})
        explorer.BatchExplorer._with_the_reference_checked(
            SimpleNamespace(run_specs=[spec]), {"minimum_at_nm": 0.4, "bulk_from_nm": 3.0},
            {"coordinate": [0.4, 3.0], "free_energy_kjmol": [0.0, 5.0]}, plan,
            directories, temperature=300.0, cone=None)
        assert seen["ligand_resname"] == "LIG"

    def test_a_check_that_fails_leaves_the_number_and_says_so(self, tmp_path,
                                                              monkeypatch) -> None:
        from fastmdxplora.simulation import reference_state

        def broken(*_a, **_k):
            raise RuntimeError("no")

        monkeypatch.setattr(reference_state, "check_the_reference", broken)
        from fastmdxplora.batch import explorer
        from fastmdxplora.simulation.umbrella import UmbrellaPlan, Window

        plan = UmbrellaPlan(windows=(Window(0, 0.4, 1000.0), Window(1, 3.0, 1000.0)),
                            collective_variable="ligand_distance")
        spec = SimpleNamespace(options={"simulation": {"umbrella": {
            "index": 0, "ligand_resname": "LIG", "site_selection": "resid 0"}}})
        answer = explorer.BatchExplorer._with_the_reference_checked(
            SimpleNamespace(run_specs=[spec]),
            {"delta_g_kjmol": -30.0, "minimum_at_nm": 0.4, "bulk_from_nm": 3.0},
            {"coordinate": [0.4, 3.0], "free_energy_kjmol": [0.0, 5.0]},
            plan, {0: tmp_path}, temperature=300.0, cone=None)
        assert answer["delta_g_kjmol"] == -30.0
        assert answer["reference"] == {"not_checked": "no"}
