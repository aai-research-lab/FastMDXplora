"""A chain's extension, on chains whose length is known by construction."""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.end_to_end import EndToEndDistance
from fastmdxplora.refusals import StudyError


def _straight_chains(n_residues=6, spacing=0.38, chains=1, box=None, frames=4):
    """Chains laid along x with CA atoms ``spacing`` apart.

    The end-to-end distance is then ``(n_residues - 1) * spacing`` exactly,
    which is an answer arrived at by counting rather than by comparison.
    """
    top = md.Topology()
    positions = []
    for c in range(chains):
        chain = top.add_chain()
        for i in range(n_residues):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            top.add_atom("N", md.element.nitrogen, res)
            top.add_atom("CA", md.element.carbon, res)
            top.add_atom("C", md.element.carbon, res)
            base = np.array([i * spacing, c * 1.5, 0.0])
            positions.extend([
                base + [-0.05, 0.0, 0.0],
                base,
                base + [+0.05, 0.0, 0.0],
            ])
    xyz = np.tile(np.asarray(positions, dtype=np.float32), (frames, 1, 1))
    traj = md.Trajectory(xyz, top)
    if box is not None:
        traj.unitcell_lengths = np.tile([box, box, box], (frames, 1))
        traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (frames, 1))
    return traj


class TestTheDistance:
    def test_a_straight_chain_measures_its_own_length(self):
        result = EndToEndDistance().compute(
            _straight_chains(n_residues=6, spacing=0.38))

        assert result.shape == (4,)
        assert np.allclose(result, 5 * 0.38)

    def test_it_measures_between_alpha_carbons_by_default(self):
        """N and C sit either side of each CA, so the default is checkable."""
        analysis = EndToEndDistance()
        traj = _straight_chains(n_residues=4, spacing=0.4)
        analysis.compute(traj)

        first, last = analysis.findings["ends"]["atom_pairs"][0]
        assert traj.topology.atom(first).name == "CA"
        assert traj.topology.atom(last).name == "CA"

    def test_atom_none_takes_the_outermost_atoms(self):
        """For a bead chain with no backbone names."""
        result = EndToEndDistance(atom=None).compute(
            _straight_chains(n_residues=4, spacing=0.4))

        # First atom of residue 1 is at -0.05, last atom of residue 4 at
        # 3*0.4 + 0.05, so the span is 0.1 nm longer than CA to CA.
        assert np.allclose(result, 3 * 0.4 + 0.1)

    def test_by_chain_reports_one_column_each(self):
        result = EndToEndDistance(by_chain=True).compute(
            _straight_chains(n_residues=5, spacing=0.3, chains=3))

        assert result.shape == (4, 3)
        assert np.allclose(result, 4 * 0.3)


class TestThePeriodicBoundary:
    def test_a_chain_approaching_half_the_box_is_marked(self):
        """Minimum image folds a longer chain back without saying so."""
        analysis = EndToEndDistance()
        # 5 * 0.38 = 1.9 nm against a half-box of 2.0 nm.
        analysis.compute(_straight_chains(n_residues=6, spacing=0.38, box=4.0))

        assert "not_a_measurement" in analysis.findings
        assert "shorter of the two ways round" in (
            analysis.findings["not_a_measurement"])

    def test_a_short_chain_in_a_large_box_is_not_marked(self):
        analysis = EndToEndDistance()
        analysis.compute(_straight_chains(n_residues=3, spacing=0.3, box=8.0))

        assert "not_a_measurement" not in analysis.findings

    def test_a_trajectory_without_a_box_says_so(self):
        analysis = EndToEndDistance()
        analysis.compute(_straight_chains(box=None))

        assert "periodic" in analysis.findings
        assert "no unit cell" in analysis.findings["periodic"]


class TestWhatItRefuses:
    def test_several_chains_without_by_chain_is_refused(self):
        """First chain's start to last chain's end describes neither."""
        with pytest.raises(StudyError) as raised:
            EndToEndDistance().compute(_straight_chains(chains=3))

        assert "spans 3 chains" in str(raised.value)
        assert raised.value.code == "analysis.selection.arity"

    def test_a_single_residue_has_only_one_end(self):
        with pytest.raises(StudyError) as raised:
            EndToEndDistance().compute(_straight_chains(n_residues=1))

        assert raised.value.code == "analysis.sampling.too_few_residues"

    def test_a_missing_atom_name_says_what_is_there(self):
        with pytest.raises(StudyError) as raised:
            EndToEndDistance(atom="P").compute(_straight_chains())

        message = str(raised.value)
        assert "no atom named 'P'" in message
        assert "CA" in message  # what it could have used instead

    def test_an_empty_selection_is_refused(self):
        with pytest.raises(StudyError) as raised:
            EndToEndDistance(selection="resname NOPE").compute(
                _straight_chains())

        assert raised.value.code == "analysis.selection.empty"


class TestItJoinsTheRegisters:
    def test_the_schema_names_it(self):
        from fastmdxplora.config.schema import ANALYSIS_NAMES

        assert "end_to_end" in ANALYSIS_NAMES

    def test_it_declares_itself_a_time_series(self):
        assert EndToEndDistance.time_series is True

    def test_it_leaves_the_solvent_out_by_default(self):
        """The ends of a water box are not a chain's ends."""
        assert EndToEndDistance.default_selection == "protein"
