"""A distance between two groups, including the box everyone forgets."""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.pair_distance import PairDistance
from fastmdxplora.refusals import StudyError


def _two_groups(offsets_a, offsets_b, box=None, frames=3):
    """Two residues, ``LIGA`` and ``LIGB``, holding the given atoms."""
    top = md.Topology()
    chain = top.add_chain()
    positions = []
    for name, offsets in (("LGA", offsets_a), ("LGB", offsets_b)):
        res = top.add_residue(name, chain, resSeq=len(positions) + 1)
        for i, offset in enumerate(offsets):
            top.add_atom(f"C{i}", md.element.carbon, res)
            positions.append(offset)
    xyz = np.tile(np.asarray(positions, dtype=np.float32), (frames, 1, 1))
    traj = md.Trajectory(xyz, top)
    if box is not None:
        traj.unitcell_lengths = np.tile([box, box, box], (frames, 1))
        traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (frames, 1))
    return traj


class TestTheDistance:
    def test_two_atoms_measure_their_separation(self):
        result = PairDistance(
            selection_a="resname LGA", selection_b="resname LGB").compute(
                _two_groups([[0, 0, 0]], [[1.5, 0, 0]]))

        assert result.shape == (3,)
        assert np.allclose(result, 1.5)

    def test_com_and_closest_answer_different_questions(self):
        """Two long groups can have distant centres and touching edges."""
        a = [[x, 0.0, 0.0] for x in np.linspace(0.0, 1.0, 5)]
        b = [[x, 0.0, 0.0] for x in np.linspace(1.1, 2.1, 5)]
        traj = _two_groups(a, b)

        com = PairDistance(
            selection_a="resname LGA", selection_b="resname LGB",
            measure="com").compute(traj)
        closest = PairDistance(
            selection_a="resname LGA", selection_b="resname LGB",
            measure="closest").compute(traj)

        assert np.allclose(com, 1.1)      # centres at 0.5 and 1.6
        assert np.allclose(closest, 0.1)  # nearest atoms at 1.0 and 1.1

    def test_the_centre_is_mass_weighted_where_masses_exist(self):
        analysis = PairDistance(
            selection_a="resname LGA", selection_b="resname LGB")
        analysis.compute(_two_groups([[0, 0, 0]], [[1.0, 0, 0]]))

        assert analysis.findings["centres"]["mass_weighted"] is True


class TestThePeriodicBoundary:
    def test_two_groups_across_a_face_are_adjacent_not_a_box_apart(self):
        """The whole reason a distance needs the cell."""
        result = PairDistance(
            selection_a="resname LGA", selection_b="resname LGB").compute(
                _two_groups([[0.1, 2.0, 2.0]], [[3.9, 2.0, 2.0]], box=4.0))

        assert np.allclose(result, 0.2)

    def test_the_same_pair_without_a_box_reads_as_far_apart(self):
        """Absent a cell there is nothing to fold the separation into."""
        result = PairDistance(
            selection_a="resname LGA", selection_b="resname LGB").compute(
                _two_groups([[0.1, 2.0, 2.0]], [[3.9, 2.0, 2.0]], box=None))

        assert np.allclose(result, 3.8)

    def test_closest_approach_is_periodic_too(self):
        result = PairDistance(
            selection_a="resname LGA", selection_b="resname LGB",
            measure="closest").compute(
                _two_groups([[0.1, 2.0, 2.0]], [[3.9, 2.0, 2.0]], box=4.0))

        assert np.allclose(result, 0.2)

    def test_a_separation_near_half_the_box_is_marked(self):
        analysis = PairDistance(
            selection_a="resname LGA", selection_b="resname LGB")
        analysis.compute(
            _two_groups([[0.2, 2.0, 2.0]], [[2.1, 2.0, 2.0]], box=4.0))

        assert "not_a_measurement" in analysis.findings
        assert "folds back" in analysis.findings["not_a_measurement"]

    def test_a_trajectory_without_a_box_says_so(self):
        analysis = PairDistance(
            selection_a="resname LGA", selection_b="resname LGB")
        analysis.compute(_two_groups([[0, 0, 0]], [[1.0, 0, 0]]))

        assert "no unit cell" in analysis.findings["periodic"]


class TestWhatItRefuses:
    def test_a_missing_selection_is_refused(self):
        with pytest.raises(StudyError) as raised:
            PairDistance(selection_a="resname LGA")

        assert "is required" in str(raised.value)
        assert raised.value.code == "analysis.option.missing_companion"

    def test_an_unknown_measure_is_refused(self):
        with pytest.raises(StudyError) as raised:
            PairDistance(selection_a="resname LGA",
                         selection_b="resname LGB", measure="centroid")

        assert raised.value.code == "analysis.option.not_permitted"

    def test_overlapping_selections_are_refused(self):
        """Shared atoms pull a centre-of-mass separation toward zero."""
        with pytest.raises(StudyError) as raised:
            PairDistance(
                selection_a="resname LGA or resname LGB",
                selection_b="resname LGB").compute(
                    _two_groups([[0, 0, 0]], [[1.0, 0, 0]]))

        assert "share" in str(raised.value)
        assert raised.value.code == "analysis.selection.arity"

    def test_an_empty_selection_is_refused(self):
        with pytest.raises(StudyError) as raised:
            PairDistance(
                selection_a="resname NOPE",
                selection_b="resname LGB").compute(
                    _two_groups([[0, 0, 0]], [[1.0, 0, 0]]))

        assert raised.value.code == "analysis.selection.empty"


class TestItJoinsTheRegisters:
    def test_the_schema_names_it(self):
        from fastmdxplora.config.schema import ANALYSIS_NAMES

        assert "pair_distance" in ANALYSIS_NAMES

    def test_it_declares_that_it_ignores_the_scope_selection(self):
        assert PairDistance.honours_selection is False

    def test_it_declares_itself_a_time_series(self):
        assert PairDistance.time_series is True
