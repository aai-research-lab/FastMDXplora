"""The inertia tensor, checked against arrangements solvable on paper."""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.moments_of_inertia import MomentsOfInertia
from fastmdxplora.refusals import StudyError

CARBON = md.element.carbon.mass


def _points(offsets, element=md.element.carbon, frames=3, box=None,
            residue="ALA"):
    """A single residue holding one atom per offset."""
    top = md.Topology()
    chain = top.add_chain()
    res = top.add_residue(residue, chain, resSeq=1)
    for i in range(len(offsets)):
        top.add_atom(f"C{i}", element, res)
    xyz = np.tile(np.asarray(offsets, dtype=np.float32), (frames, 1, 1))
    traj = md.Trajectory(xyz, top)
    if box is not None:
        traj.unitcell_lengths = np.tile([box, box, box], (frames, 1))
        traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (frames, 1))
    return traj


def _square(a=1.0):
    """Four equal masses in the xy plane at (+-a, 0) and (0, +-a).

    Solvable by hand: about x and y the moment is m*a^2 from each of the
    two out-of-axis masses, so 2*m*a^2; about z every mass is a away, so
    4*m*a^2. The three principal moments are therefore
    (2ma^2, 2ma^2, 4ma^2), ascending.
    """
    return _points([[a, 0, 0], [-a, 0, 0], [0, a, 0], [0, -a, 0]])


class TestAgainstArithmetic:
    def test_a_square_gives_the_moments_it_should(self):
        a = 1.0
        result = MomentsOfInertia(selection="all").compute(_square(a))

        expected = np.array([2, 2, 4], dtype=float) * CARBON * a ** 2
        assert result.shape == (3, 3)
        assert np.allclose(result[0], expected, rtol=1e-6)

    def test_a_line_has_one_vanishing_moment(self):
        """About its own axis a rod has no extent, so I1 is zero."""
        result = MomentsOfInertia(selection="all").compute(
            _points([[-1, 0, 0], [0, 0, 0], [1, 0, 0]]))

        i1, i2, i3 = result[0]
        assert i1 == pytest.approx(0.0, abs=1e-9)
        assert i2 == pytest.approx(i3, rel=1e-9)

    def test_a_cube_is_isotropic(self):
        """Every direction alike, so the three moments coincide."""
        corners = [[x, y, z] for x in (-1, 1) for y in (-1, 1)
                   for z in (-1, 1)]
        result = MomentsOfInertia(selection="all").compute(_points(corners))

        assert np.allclose(result[0], result[0][0], rtol=1e-9)

    def test_the_moments_are_ascending(self):
        result = MomentsOfInertia(selection="all").compute(_square())

        assert np.all(np.diff(result, axis=1) >= -1e-12)


class TestRotationInvariance:
    """The claim that alignment does not enter, checked rather than asserted.

    Every distance measured against a reference depends on the
    superposition that preceded it, which is why `superposed` exists. The
    eigenvalues of an inertia tensor do not, and a test is the difference
    between saying so and knowing it.
    """

    def test_rotating_the_molecule_changes_nothing(self):
        traj = _square()
        angle = 0.7
        c, s = np.cos(angle), np.sin(angle)
        rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        turned = md.Trajectory(
            (traj.xyz @ rotation.T).astype(np.float32), traj.topology)

        before = MomentsOfInertia(selection="all").compute(traj)
        after = MomentsOfInertia(selection="all").compute(turned)

        assert np.allclose(before, after, rtol=1e-6)

    def test_translating_the_molecule_changes_nothing(self):
        """Measured about the centre of mass, not the origin."""
        traj = _square()
        moved = md.Trajectory(
            (traj.xyz + np.array([5.0, -3.0, 2.0])).astype(np.float32),
            traj.topology)

        before = MomentsOfInertia(selection="all").compute(traj)
        after = MomentsOfInertia(selection="all").compute(moved)

        assert np.allclose(before, after, rtol=1e-6)


class TestThePeriodicBoundary:
    def test_a_selection_as_wide_as_its_box_is_marked(self):
        """What a molecule split across the boundary looks like."""
        analysis = MomentsOfInertia(selection="all")
        analysis.compute(_points([[-1.9, 0, 0], [0, 0, 0], [1.9, 0, 0]],
                                 box=4.0))

        assert "not_a_measurement" in analysis.findings
        assert "wrapped across a periodic boundary" in (
            analysis.findings["not_a_measurement"])

    def test_a_whole_protein_in_a_dodecahedron_is_not_marked(self, tmp_path):
        """1AKE, 8.54 nm across, in the dodecahedron setup's padding rule
        gives it: every box vector 10.39 nm long, the narrowest width 7.35.
        Its extent against the box marked it broken; no bond is."""
        import gzip
        from pathlib import Path

        source = tmp_path / "1AKE.pdb"
        source.write_bytes(gzip.decompress(
            (Path(__file__).parent / "data" / "assemblies" / "1AKE.pdb.gz").read_bytes()))
        structure = md.load(str(source))
        protein = structure.atom_slice(structure.topology.select("protein"))
        x = protein.xyz[0]
        a = 2 * float(np.linalg.norm(x - x.mean(axis=0), axis=1).max()) + 1.0
        traj = md.Trajectory(protein.xyz, protein.topology)
        traj.unitcell_vectors = np.array(
            [[[a, 0, 0], [0, a, 0], [a / 2, a / 2, a * np.sqrt(2) / 2]]],
            dtype=np.float32)
        assert float(np.ptp(x, axis=0).max()) > 0.8 * a * np.sqrt(2) / 2

        analysis = MomentsOfInertia()
        analysis.compute(traj)

        assert "not_a_measurement" not in analysis.findings

    def test_a_bonded_molecule_split_by_the_boundary_is_marked(self):
        """Half of a bonded chain moved by a box vector, as an engine writes
        a molecule it did not keep whole."""
        top = md.Topology()
        residue = top.add_residue("ALA", top.add_chain(), resSeq=1)
        atoms = [top.add_atom(f"C{i}", md.element.carbon, residue) for i in range(6)]
        for first, second in zip(atoms, atoms[1:]):
            top.add_bond(first, second)
        xyz = np.array([[[0.15 * i, 0.0, 0.0] for i in range(6)]], dtype=np.float32)
        xyz[0, 3:, 0] += 3.0
        traj = md.Trajectory(xyz, top)
        traj.unitcell_lengths = np.array([[3.0, 3.0, 3.0]], dtype=np.float32)
        traj.unitcell_angles = np.array([[90.0, 90.0, 90.0]], dtype=np.float32)

        analysis = MomentsOfInertia(selection="all")
        analysis.compute(traj)

        assert "wrapped across a periodic boundary" in (
            analysis.findings["not_a_measurement"])
        assert "C2 to" in analysis.findings["not_a_measurement"]

    def test_a_compact_molecule_is_not_marked(self):
        analysis = MomentsOfInertia(selection="all")
        analysis.compute(_points([[-0.2, 0, 0], [0, 0, 0], [0.2, 0, 0]],
                                 box=8.0))

        assert "not_a_measurement" not in analysis.findings


class TestWhatItRefuses:
    def test_a_topology_without_masses_is_refused(self):
        """Equal weighting would give a tensor with the same units."""
        traj = _points([[1, 0, 0], [0, 1, 0], [0, 0, 1]], element=None)

        with pytest.raises(StudyError) as raised:
            MomentsOfInertia(selection="all").compute(traj)

        assert "defined by mass" in str(raised.value)
        assert raised.value.code == "analysis.data.absent"

    def test_fewer_than_three_atoms_is_refused(self):
        with pytest.raises(StudyError) as raised:
            MomentsOfInertia(selection="all").compute(
                _points([[0, 0, 0], [1, 0, 0]]))

        assert raised.value.code == "analysis.selection.arity"

    def test_an_empty_selection_is_refused(self):
        with pytest.raises(StudyError) as raised:
            MomentsOfInertia(selection="resname NOPE").compute(_square())

        assert raised.value.code == "analysis.selection.empty"


class TestItJoinsTheRegisters:
    def test_the_schema_names_it(self):
        from fastmdxplora.config.schema import ANALYSIS_NAMES

        assert "moments_of_inertia" in ANALYSIS_NAMES

    def test_it_offers_no_single_reweighted_mean(self):
        """Three numbers per frame; picking one of them would be a choice."""
        assert MomentsOfInertia.reweightable is None

    def test_it_leaves_the_solvent_out_by_default(self):
        assert MomentsOfInertia.default_selection == "protein"


class TestEveryReaderIsTold:
    def test_a_broken_molecule_is_said_where_the_mean_is_read(self, tmp_path):
        """The finding sat at the top level, which no reader opens."""
        import json

        from fastmdxplora.report.document import _findings_notes

        top = md.Topology()
        residue = top.add_residue("ALA", top.add_chain(), resSeq=1)
        atoms = [top.add_atom(f"C{i}", md.element.carbon, residue) for i in range(6)]
        for first, second in zip(atoms, atoms[1:]):
            top.add_bond(first, second)
        xyz = np.tile(np.array([[0.15 * i, 0.0, 0.0] for i in range(6)],
                               dtype=np.float32), (20, 1, 1))
        xyz[:, 3:, 0] += 3.0
        traj = md.Trajectory(xyz, top)
        traj.unitcell_lengths = np.full((20, 3), 3.0, dtype=np.float32)
        traj.unitcell_angles = np.full((20, 3), 90.0, dtype=np.float32)

        MomentsOfInertia(selection="all", output_dir=tmp_path).run(traj)
        found = json.loads(
            (tmp_path / "moments_of_inertia" / "options.json").read_text())["findings"]

        assert "wrapped across a periodic boundary" in found["mean"]["not_a_measurement"]
        assert any("wrapped across" in note for note in _findings_notes(found))


class TestTheShapeDescriptors:
    """Asphericity, acylindricity and relative shape anisotropy (Theodorou
    and Suter 1985), from the gyration tensor's eigenvalues."""

    @staticmethod
    def _independent(xyz, masses):
        """The gyration tensor eigenvalues by an SVD of the mass-weighted
        coordinates, rather than by diagonalising the tensor, and the three
        descriptors from them by the second form of kappa^2."""
        out = []
        for frame in np.asarray(xyz, dtype=np.float64):
            w = masses / masses.sum()
            r = frame - (w[:, None] * frame).sum(axis=0)
            singular = np.linalg.svd(np.sqrt(w)[:, None] * r, compute_uv=False)
            l1, l2, l3 = np.sort(singular ** 2)
            first = l1 * l2 + l2 * l3 + l3 * l1
            out.append([l3 - (l1 + l2) / 2, l2 - l1, 1 - 3 * first / (l1 + l2 + l3) ** 2])
        return np.array(out)

    def test_they_agree_with_an_independent_computation(self):
        rng = np.random.default_rng(4)
        top = md.Topology()
        residue = top.add_residue("ALA", top.add_chain(), resSeq=1)
        elements = [md.element.carbon, md.element.nitrogen, md.element.oxygen,
                    md.element.hydrogen, md.element.sulfur] * 6
        for i, element in enumerate(elements):
            top.add_atom(f"X{i}", element, residue)
        xyz = rng.normal(size=(5, len(elements), 3)) * np.array([1.5, 0.7, 0.3])
        traj = md.Trajectory(xyz.astype(np.float32), top)

        analysis = MomentsOfInertia(selection="all")
        analysis.compute(traj)
        masses = np.array([e.mass for e in elements])

        assert np.allclose(analysis._shape, self._independent(traj.xyz, masses),
                           rtol=1e-6, atol=1e-9)

    def test_a_rod_has_an_anisotropy_of_one(self):
        analysis = MomentsOfInertia(selection="all")
        analysis.compute(_points([[x, 0, 0] for x in np.linspace(-2, 2, 11)]))
        b, c, kappa2 = analysis._shape[0]

        assert kappa2 == pytest.approx(1.0, abs=1e-6)
        assert c == pytest.approx(0.0, abs=1e-6)
        # All the spread is on one axis: b is that variance, 1.6 nm^2.
        assert b == pytest.approx(np.var(np.linspace(-2, 2, 11)), rel=1e-5)

    def test_a_cube_has_none(self):
        corners = [[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        analysis = MomentsOfInertia(selection="all")
        analysis.compute(_points(corners))

        assert np.allclose(analysis._shape[0], 0.0, atol=1e-6)

    def test_a_spherical_cloud_has_almost_none(self):
        rng = np.random.default_rng(0)
        cloud = rng.normal(size=(4000, 3))
        analysis = MomentsOfInertia(selection="all")
        analysis.compute(_points(cloud, frames=1))

        assert analysis._shape[0, 2] < 0.005

    def test_a_disc_has_a_quarter(self):
        """Two equal eigenvalues and a third of zero: kappa^2 = 1/4."""
        analysis = MomentsOfInertia(selection="all")
        analysis.compute(_square())
        b, c, kappa2 = analysis._shape[0]

        # Eigenvalues (0, 0.5, 0.5) nm^2: b = 0.5 - 0.25, c = 0.5 - 0.
        assert kappa2 == pytest.approx(0.25, abs=1e-6)
        assert b == pytest.approx(0.25, abs=1e-6)
        assert c == pytest.approx(0.5, abs=1e-6)

    def test_they_are_written_with_their_means(self, tmp_path):
        import json

        import pandas as pd

        rng = np.random.default_rng(3)
        rod = np.array([[x, 0, 0] for x in np.linspace(-2, 2, 11)])
        frames = rod[None] + rng.normal(scale=0.05, size=(200, 11, 3))
        traj = md.Trajectory(frames.astype(np.float32), _points(rod).topology)
        traj.time = np.arange(1, 201) * 10.0

        assert MomentsOfInertia(selection="all", output_dir=tmp_path).run(traj).status == "ok"
        folder = tmp_path / "moments_of_inertia"
        table = pd.read_csv(folder / "moments_of_inertia_shape.dat")
        found = json.loads((folder / "options.json").read_text())["findings"]

        assert list(table.columns) == ["asphericity_nm2", "acylindricity_nm2",
                                       "relative_shape_anisotropy"]
        assert len(table) == 200
        record = found["relative_shape_anisotropy"]
        assert record["mean"] == pytest.approx(
            table["relative_shape_anisotropy"].iloc[record["discard"]:].mean(), rel=1e-9)
        assert 0.98 < record["mean"] <= 1.0
        assert record["n_frames"] == 200
        assert found["asphericity"]["unit"] == "nm²"
        assert np.loadtxt(folder / "moments_of_inertia.dat").shape == (200, 3)
