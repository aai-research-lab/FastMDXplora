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
