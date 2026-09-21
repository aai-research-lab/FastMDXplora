"""An experimental anchor that computes one side and never compares.

`order_parameters` produced S^2 per residue and stopped. The manuscript's
validation table says trajectories are tested against "NMR backbone order
parameters", and the registered-analyses table files this under "External
anchors -- quantities checkable against measurement", but nothing in the
package compared the computed values to a measured set. A 100 ns run
produced the simulated half of a comparison whose other half had nowhere to
go, and the gap would have surfaced when the numbers were wanted rather than
when the run was planned.

`bfactor_comparison` had the pattern already: read a reference, match by
residue, report the agreement, and state plainly what the agreement is not.
This extends the same shape to the stronger anchor -- the one with a
published range to test against -- and adds the rank correlation to both.

**Both statistics, because they answer different questions.** Pearson is
moved by the termini, which are the largest values on both sides and the
least comparable: a crystallographic tail is disordered where a solution
tail is mobile. Spearman asks whether the flexible residues are the same
residues, which is what a simulation-versus-experiment correlation can
support.

The residue set is an option rather than a filter applied afterwards.
Choosing which residues to compare after seeing the correlation is how this
kind of comparison is made to pass, so the choice belongs in the
configuration where it can be pre-registered and read back.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.analysis.order_parameters import (
    parse_residues,
    read_reference,
)


class TestTheReferenceLoaderTakesWhatArrives:
    """An experimental set comes as whatever the depositing group wrote. A
    loader that insists on one layout is a loader nobody can feed."""

    def test_two_columns_of_whitespace(self, tmp_path: Path) -> None:
        path = tmp_path / "s2.dat"
        path.write_text("2 0.78\n3 0.82\n4 0.85\n", encoding="utf-8")
        assert read_reference(path) == {2: 0.78, 3: 0.82, 4: 0.85}

    def test_commas_a_third_column_and_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "s2.csv"
        path.write_text(
            "# Tjandra et al. 1995, 27 C, 360 and 600 MHz\n"
            "residue,S2,uncertainty\n"
            "2,0.78,0.02\n"
            "3,0.82,0.03\n"
            "\n"
            "4,0.85,0.02\n", encoding="utf-8")
        assert read_reference(path) == {2: 0.78, 3: 0.82, 4: 0.85}

    def test_a_header_without_a_hash_is_skipped_not_parsed(
            self, tmp_path: Path) -> None:
        """Published tables often lead with a bare column-name row."""
        path = tmp_path / "s2.dat"
        path.write_text("Residue  S2\n2  0.78\n3  0.82\n4 0.9\n",
                        encoding="utf-8")
        assert read_reference(path) == {2: 0.78, 3: 0.82, 4: 0.9}

    def test_a_file_with_nothing_usable_says_so(self, tmp_path: Path) -> None:
        path = tmp_path / "s2.dat"
        path.write_text("# only a comment\n\n", encoding="utf-8")
        with pytest.raises(ValueError, match="no usable rows"):
            read_reference(path)


class TestTheResidueSetIsDeclaredNotFiltered:

    @pytest.mark.parametrize("spec, expected", [
        ("72-76", {72, 73, 74, 75, 76}),
        ("1,72-76", {1, 72, 73, 74, 75, 76}),
        ("1, 2 ,3", {1, 2, 3}),
        ("", set()),
        (None, set()),
    ])
    def test_a_spec_becomes_a_set(self, spec, expected) -> None:
        assert parse_residues(spec) == expected

    def test_a_negative_lower_bound_is_still_a_range(self) -> None:
        """Residue numbering is not guaranteed to start at one; a deposited
        structure can carry an expression tag at zero or below."""
        assert parse_residues("-2--1") == {-2, -1}


class _Fake:
    """The comparison, exercised without a trajectory.

    `_compare` is a method on the analysis, and constructing the analysis
    means constructing an mdtraj trajectory with amide hydrogens. What is
    under test here is the matching and the arithmetic, so the method is
    called against an object carrying only the two attributes it reads.
    """

    def __init__(self, reference, exclude=None):
        self.reference = str(reference)
        self.reference_exclude = exclude

    _compare = None  # bound below


def _comparison(tmp_path, simulated, measured, exclude=None):
    from fastmdxplora.analysis.order_parameters import OrderParameters
    path = tmp_path / "reference.dat"
    path.write_text(
        "\n".join(f"{r} {v}" for r, v in measured.items()) + "\n",
        encoding="utf-8")
    holder = _Fake(path, exclude)
    record: dict = {}
    labels = np.array(sorted(simulated), dtype=np.int64)
    values = np.array([simulated[int(r)] for r in labels], dtype=float)
    aligned = OrderParameters._compare(holder, labels, values, record)
    return record.get("reference_comparison"), aligned


class TestTheComparisonReportsBothStatistics:

    def test_a_perfect_match_is_perfect_both_ways(self, tmp_path) -> None:
        values = {2: 0.80, 3: 0.85, 4: 0.90, 5: 0.70, 6: 0.60}
        got, _ = _comparison(tmp_path, values, values)
        assert got["pearson_r"] == pytest.approx(1.0)
        assert got["spearman_rho"] == pytest.approx(1.0)
        assert got["mean_absolute_deviation"] == pytest.approx(0.0)
        assert got["rmsd"] == pytest.approx(0.0)
        assert got["residues_compared"] == 5

    def test_rank_survives_what_pearson_does_not(self, tmp_path) -> None:
        """One terminal residue with a large, differently-scaled value moves
        Pearson and leaves the ordering intact. That is the case the rank
        correlation exists for, and the reason both are reported."""
        measured = {2: 0.80, 3: 0.82, 4: 0.84, 5: 0.86, 6: 0.88, 76: 0.20}
        simulated = {2: 0.81, 3: 0.83, 4: 0.85, 5: 0.87, 6: 0.89, 76: 0.05}

        got, _ = _comparison(tmp_path, simulated, measured)

        assert got["spearman_rho"] == pytest.approx(1.0)
        assert got["pearson_r"] < got["spearman_rho"]

    def test_the_excluded_residues_are_recorded_not_merely_applied(
            self, tmp_path) -> None:
        """A reader has to be able to see which residues the correlation was
        taken over. Silently dropping them is how the number stops being
        checkable."""
        measured = {2: 0.80, 3: 0.82, 4: 0.84, 75: 0.30, 76: 0.20}
        simulated = {2: 0.80, 3: 0.82, 4: 0.84, 75: 0.90, 76: 0.95}

        got, _ = _comparison(tmp_path, simulated, measured, exclude="72-76")

        assert got["residues_compared"] == 3
        assert got["residues_excluded"] == [72, 73, 74, 75, 76]
        assert got["pearson_r"] == pytest.approx(1.0)

    def test_residues_the_reference_lacks_are_counted_not_dropped_silently(
            self, tmp_path) -> None:
        measured = {2: 0.80, 3: 0.82, 4: 0.84}
        simulated = {2: 0.80, 3: 0.82, 4: 0.84, 5: 0.5, 6: 0.5}

        got, _ = _comparison(tmp_path, simulated, measured)

        assert got["residues_compared"] == 3
        assert got["residues_unmatched"] == 2

    def test_too_few_matches_refuses_rather_than_correlating_two_points(
            self, tmp_path) -> None:
        """Two points always correlate perfectly. A renumbering during
        preparation is the usual cause, and the refusal says so."""
        got, _ = _comparison(tmp_path, {2: 0.8, 3: 0.9},
                             {500: 0.8, 501: 0.9})

        assert "refused" in got
        assert "renumbering" in got["refused"]
        assert "pearson_r" not in got

    def test_the_aligned_column_lines_up_with_the_residues(
            self, tmp_path) -> None:
        """The third data column is the reference beside its own residue, so
        any subset can be recomputed from the deposited file."""
        measured = {2: 0.80, 4: 0.84}
        simulated = {2: 0.70, 3: 0.75, 4: 0.79}

        _, aligned = _comparison(tmp_path, simulated, measured)

        assert aligned[0] == pytest.approx(0.80)
        assert np.isnan(aligned[1])
        assert aligned[2] == pytest.approx(0.84)


class TestItIsNotSoldAsAnAccuracy:

    def test_the_record_says_what_the_agreement_is_not(self, tmp_path) -> None:
        values = {2: 0.80, 3: 0.85, 4: 0.90, 5: 0.70}
        got, _ = _comparison(tmp_path, values, values)

        note = got["what_this_is_not"].lower()
        assert "not" in note
        assert "force field" in note

    def test_it_names_the_direction_of_the_expected_disagreement(
            self, tmp_path) -> None:
        """Motion slower than the run is invisible, and invisible motion
        reads as rigidity, so an under-sampled run's S^2 is too high. A
        reader comparing against experiment needs that direction stated
        before they interpret a discrepancy."""
        values = {2: 0.80, 3: 0.85, 4: 0.90, 5: 0.70}
        got, _ = _comparison(tmp_path, values, values)

        note = got["what_this_is_not"]
        assert "one direction" in note
        assert "halves_max_difference" in note

    def test_the_source_travels_with_the_numbers(self, tmp_path) -> None:
        values = {2: 0.80, 3: 0.85, 4: 0.90, 5: 0.70}
        got, _ = _comparison(tmp_path, values, values)
        assert got["source"].endswith("reference.dat")


class TestTheBFactorAnchorGainedTheSameStatistic:

    def test_the_bfactor_comparison_reports_a_rank_correlation(self, tmp_path) -> None:
        """Both anchors are per-residue comparisons against a measurement
        whose relationship to a simulated ensemble is model-dependent, and
        one reported only Pearson while the other reported none.

        The tests above are the order-parameter anchor's. This is the
        B-factor one, run: each residue fluctuates more than the one before,
        and the deposited B-factors rise exponentially along the chain, so
        the two agree in order and not in shape. Spearman is then exactly
        one and Pearson is not -- which Pearson reported under Spearman's
        name would not give."""
        import mdtraj as md

        from fastmdxplora.analysis.bfactor_comparison import BFactorComparison

        # A stable core to align on, as a real protein has, and mobile
        # residues to compare. Aligned on the mobile residues themselves, the
        # most mobile would dominate the fit and reshuffle the rest.
        core, mobile, n_frames = 12, 8, 400
        topology = md.Topology()
        chain = topology.add_chain()
        for index in range(core + mobile):
            residue = topology.add_residue("ALA", chain, resSeq=index + 1)
            topology.add_atom("CA", md.element.carbon, residue)
        rng = np.random.default_rng(0)
        turn = np.linspace(0, 3 * np.pi, core + mobile)
        rest = np.column_stack([np.cos(turn), np.sin(turn), np.linspace(0, 2.0, core + mobile)])
        spread = np.r_[np.full(core, 0.005), 0.02 * (1 + np.arange(mobile))]
        xyz = rest + rng.normal(0, 1, (n_frames, core + mobile, 3)) * spread[None, :, None]
        traj = md.Trajectory(xyz, topology)

        # B-factors for the mobile residues only, rising exponentially: the
        # core has none, so it is left out of the comparison.
        deposited = tmp_path / "deposited.pdb"
        deposited.write_text("".join(
            f"ATOM  {i + 1:5d}  CA  ALA A{i + 1:4d}    "
            f"{10 * rest[i, 0]:8.3f}{10 * rest[i, 1]:8.3f}{10 * rest[i, 2]:8.3f}"
            f"  1.00{5.0 * 2.0 ** (i - core):6.2f}           C\n"
            for i in range(core, core + mobile)) + "END\n", encoding="utf-8")

        analysis = BFactorComparison(output_dir=tmp_path / "bfactor", structure=str(deposited),
                                     align_selection=f"index 0 to {core - 1}")
        analysis.compute(traj)
        got = analysis.findings["bfactor_comparison"]
        assert got["residues_compared"] == mobile
        assert got["spearman_rho"] == pytest.approx(1.0)
        assert got["pearson_r"] < 0.99
