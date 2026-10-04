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
    def test_a_chain_longer_than_half_the_box_reads_its_length(self):
        """Minimum image between the ends folded it: 3.420 nm read 1.200.

        A straight 10-residue chain, CA 0.38 nm apart, in a 4.62 nm cube as
        the loader leaves it (made whole). Its length is 9 * 0.38 by
        construction.
        """
        from fastmdxplora.analysis.loading import _made_whole

        traj = _made_whole(_straight_chains(n_residues=10, spacing=0.38, box=4.62))
        result = EndToEndDistance().compute(traj)

        assert np.allclose(result, 9 * 0.38, atol=1e-5)

    def test_a_chain_stored_wrapped_into_the_box_reads_the_same(self):
        """Each atom wrapped into the cell, as an engine may write it."""
        traj = _straight_chains(n_residues=10, spacing=0.38, box=4.62)
        traj.xyz = np.mod(traj.xyz + 2.0, 4.62).astype(np.float32)
        # The chain really is split: its last residues sit at the near face.
        assert traj.xyz[0, -1, 0] < traj.xyz[0, 0, 0]

        result = EndToEndDistance().compute(traj)

        assert np.allclose(result, 9 * 0.38, atol=1e-5)

    def test_in_a_dodecahedron_too(self):
        """The cell setup builds by default, in OpenMM's reduced form."""
        a = 4.62
        traj = _straight_chains(n_residues=10, spacing=0.38)
        traj.unitcell_vectors = np.tile(np.array(
            [[a, 0, 0], [0, a, 0], [a / 2, a / 2, a * np.sqrt(2) / 2]],
            dtype=np.float32), (traj.n_frames, 1, 1))
        traj.xyz = traj.xyz + np.float32(3.0)  # off-centre, partly outside

        analysis = EndToEndDistance()
        result = analysis.compute(traj)

        assert np.allclose(result, 9 * 0.38, atol=1e-5)
        assert analysis.findings["periodic"]["narrowest_width_nm"] == pytest.approx(
            a * np.sqrt(2) / 2, rel=1e-5)

    def test_a_chain_reaching_its_own_image_is_marked(self):
        """3.42 nm in a 4.0 nm cube puts the end 0.58 nm from the image of
        the start: the chain is interacting with its own copy."""
        analysis = EndToEndDistance()
        result = analysis.compute(
            _straight_chains(n_residues=10, spacing=0.38, box=4.0))

        assert np.allclose(result, 9 * 0.38, atol=1e-5)
        assert "not_a_measurement" in analysis.findings
        assert "its own copy" in analysis.findings["not_a_measurement"]
        assert analysis.findings["periodic"]["nearest_self_image_nm"] == pytest.approx(
            4.0 - 9 * 0.38, abs=1e-4)

    def test_a_chain_past_half_the_box_but_clear_of_its_image_is_not_marked(self):
        """1.9 nm in a 4.0 nm cube: past half the box, which no longer
        matters, and 2.1 nm from the image of its start."""
        analysis = EndToEndDistance()
        result = analysis.compute(
            _straight_chains(n_residues=6, spacing=0.38, box=4.0))

        assert np.allclose(result, 5 * 0.38, atol=1e-5)
        assert "not_a_measurement" not in analysis.findings

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
    def test_one_distance_across_several_chains_is_refused(self):
        """First chain's start to last chain's end describes neither."""
        with pytest.raises(StudyError) as raised:
            EndToEndDistance(by_chain=False).compute(_straight_chains(chains=3))

        assert "spans 3 chains" in str(raised.value)
        assert raised.value.code == "analysis.selection.arity"

    def test_by_default_several_chains_are_measured_each(self):
        """A default run of a dimer failed here, in every study of one."""
        result = EndToEndDistance().compute(_straight_chains(chains=3))
        assert result.shape[1] == 3
        assert list(result.columns) == ["chain 0", "chain 1", "chain 2"]
        assert np.allclose(result, EndToEndDistance(by_chain=True).compute(
            _straight_chains(chains=3)))

    def test_several_chains_are_drawn_and_saved_by_name(self, tmp_path):
        result = EndToEndDistance(output_dir=tmp_path).run(_straight_chains(chains=2))
        assert result.status == "ok", result.message
        assert result.data_path.read_text().splitlines()[0] == "chain 0,chain 1"
        assert result.figure_path.is_file()

    def test_several_chains_are_not_reweighted_as_one(self):
        """The reweighting read the first column of a table as the run's
        distance; a table naming the chains is not read so."""
        from fastmdxplora.analysis.reweighted_averages import frame_series

        result = EndToEndDistance().compute(_straight_chains(chains=3))
        assert frame_series(result, EndToEndDistance.reweightable, len(result)) is None

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


class TestEveryReaderIsTold:
    """The warning sat in a finding no reader opens, under a mean printed
    with its error bar."""

    def _reaching_its_image(self):
        traj = _straight_chains(n_residues=10, spacing=0.38, box=4.0, frames=400)
        rng = np.random.default_rng(0)
        traj.xyz = (traj.xyz + rng.normal(0.0, 0.01, traj.xyz.shape)).astype(np.float32)
        traj.time = np.arange(400) * 10.0
        return traj

    def test_the_mean_carries_the_reason_and_no_error_bar(self, tmp_path):
        import json

        analysis = EndToEndDistance(output_dir=tmp_path)
        assert analysis.run(self._reaching_its_image()).status == "ok"
        found = json.loads((tmp_path / "end_to_end" / "options.json").read_text())["findings"]

        assert "its own copy" in found["mean"]["not_a_measurement"]
        assert found["mean"]["mean"] == pytest.approx(9 * 0.38, abs=0.01)
        assert not np.isfinite(found["mean"]["standard_error"])

    def test_the_report_says_it(self, tmp_path):
        import json

        from fastmdxplora.report.document import _findings_notes

        EndToEndDistance(output_dir=tmp_path).run(self._reaching_its_image())
        found = json.loads((tmp_path / "end_to_end" / "options.json").read_text())["findings"]
        notes = _findings_notes(found)

        assert any("its own copy" in note for note in notes)
        assert not any("±" in note or "+/-" in note for note in notes)

    def test_a_chain_clear_of_its_image_keeps_its_record(self, tmp_path):
        analysis = EndToEndDistance(output_dir=tmp_path)
        traj = self._reaching_its_image()
        traj.unitcell_lengths = np.full((traj.n_frames, 3), 8.0, dtype=np.float32)
        analysis.run(traj)

        assert "not_a_measurement" not in analysis.findings
        assert "error_withheld_because" not in analysis.findings["mean"]
