"""A residue's occupancy is a union, and a union is not a bracket."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from fastmdxplora.analysis.interaction_summary import (
    occupancies,
    residue_occupancies,
)


@dataclass(frozen=True)
class _Contact:
    kind: str
    ligand_atom: int
    protein_atom: int
    frame: int


def _label(index: int) -> str:
    return {10: "LEU118", 11: "LEU118", 12: "LEU118", 20: "ALA99"}[index]


class TestTheUnionCannotBeReadOffThePairs:
    """The two ends of the bracket, and why neither is the answer.

    Pairs firing in the same frames make the residue's occupancy the
    largest single pair. Pairs that never coincide make it their sum. The
    pair table is identical in the two cases apart from which frames each
    pair holds, which it does not record, so anything reading it can only
    give the interval.
    """

    def test_pairs_that_fire_together_give_the_maximum(self):
        # Three atoms of one residue, all in contact in the same 40 frames.
        contacts = [
            _Contact("hydrophobic", 1, atom, frame)
            for atom in (10, 11, 12) for frame in range(40)
        ]
        rows = residue_occupancies(contacts, 100, _label)
        assert len(rows) == 1
        assert rows[0]["frames_present"] == 40
        assert rows[0]["occupancy"] == pytest.approx(0.40)
        assert rows[0]["atom_pairs"] == 3

        # What the pair table alone would support.
        per_pair = [o.frames_present for o in occupancies(contacts, 100)]
        assert max(per_pair) == 40          # the lower end of the bracket
        assert sum(per_pair) == 120         # the upper end, above 100

    def test_pairs_that_never_coincide_give_the_sum(self):
        contacts = (
            [_Contact("hydrophobic", 1, 10, f) for f in range(0, 20)]
            + [_Contact("hydrophobic", 1, 11, f) for f in range(20, 40)]
            + [_Contact("hydrophobic", 1, 12, f) for f in range(40, 60)]
        )
        rows = residue_occupancies(contacts, 100, _label)
        assert rows[0]["frames_present"] == 60
        assert rows[0]["occupancy"] == pytest.approx(0.60)

        per_pair = [o.frames_present for o in occupancies(contacts, 100)]
        assert max(per_pair) == 20
        assert sum(per_pair) == 60

    def test_the_ordinary_case_lies_between_and_is_neither(self):
        """Which is the case every real trajectory is in."""
        contacts = (
            [_Contact("hydrophobic", 1, 10, f) for f in range(0, 40)]
            + [_Contact("hydrophobic", 1, 11, f) for f in range(20, 60)]
        )
        rows = residue_occupancies(contacts, 100, _label)
        exact = rows[0]["frames_present"]
        per_pair = [o.frames_present for o in occupancies(contacts, 100)]

        assert exact == 60
        assert max(per_pair) < exact < sum(per_pair), (
            "the bracket brackets it, and says nothing more")


class TestTheTableItself:
    def test_kinds_are_kept_apart(self):
        """A hydrogen bond and a hydrophobic contact at one residue are two
        measurements, not one residue's occupancy."""
        contacts = (
            [_Contact("hydrophobic", 1, 10, f) for f in range(0, 30)]
            + [_Contact("hbond", 2, 11, f) for f in range(10, 25)]
        )
        rows = residue_occupancies(contacts, 100, _label)
        by_kind = {r["kind"]: r for r in rows}
        assert by_kind["hydrophobic"]["frames_present"] == 30
        assert by_kind["hbond"]["frames_present"] == 15

    def test_residues_are_kept_apart(self):
        contacts = (
            [_Contact("hydrophobic", 1, 10, f) for f in range(0, 30)]
            + [_Contact("hydrophobic", 1, 20, f) for f in range(0, 5)]
        )
        rows = residue_occupancies(contacts, 100, _label)
        by_residue = {r["residue"]: r for r in rows}
        assert by_residue["LEU118"]["frames_present"] == 30
        assert by_residue["ALA99"]["frames_present"] == 5

    def test_episodes_count_arrivals_not_frames(self):
        """Sixty frames in fifty episodes is grazing; in one, it is binding.

        The distinction settled a negative control that a summed occupancy
        had convicted, so it travels with the residue table too.
        """
        grazing = [_Contact("hydrophobic", 1, 10, f) for f in range(0, 60, 2)]
        staying = [_Contact("hydrophobic", 1, 10, f) for f in range(0, 30)]

        assert residue_occupancies(grazing, 100, _label)[0]["episodes"] == 30
        assert residue_occupancies(staying, 100, _label)[0]["episodes"] == 1

    def test_no_contacts_is_an_empty_table_not_an_error(self):
        assert residue_occupancies([], 100, _label) == []
        assert residue_occupancies([], 0, _label) == []


class TestTheErrorOfAnOccupancy:
    """B-D6: the error of a contact's occupancy against the spread of that
    occupancy over independent replicas of a two-state contact with known
    rates. The episode formula sqrt(p(1-p)/episodes) was 1.4 to 2.3 times
    the spread; sqrt(p(1-p) g / N) is within a fifth of it."""

    @staticmethod
    def _telegraph(rng, n, on, off):
        out = np.empty(n, dtype=bool)
        state = rng.random() < on / (on + off)
        at = 0
        while at < n:
            stay = int(rng.geometric(off if state else on))
            out[at:at + stay] = state
            at += stay
            state = not state
        return out

    @pytest.mark.parametrize("on,off", [(0.02, 0.02), (0.002, 0.018), (0.05, 0.005)])
    def test_the_error_is_the_spread_over_replicas(self, on, off) -> None:
        from fastmdxplora.analysis.interactions import Contact

        rng = np.random.default_rng(11)
        n_frames, replicas = 4000, 120
        fractions, errors = [], []
        for _ in range(replicas):
            present = self._telegraph(rng, n_frames, on, off)
            contacts = [Contact("hydrophobic", int(f), 1, 2, 0.35)
                        for f in np.flatnonzero(present)]
            found = occupancies(contacts, n_frames)
            if not found:
                continue
            fractions.append(found[0].fraction)
            errors.append(found[0].uncertainty)
        ratio = np.nanmedian(errors) / np.std(fractions, ddof=1)
        assert 0.8 <= ratio <= 1.2, ratio

    @pytest.mark.parametrize("on,off,n_frames", [(0.002, 0.002, 2000), (0.01, 0.01, 500)])
    def test_a_contact_slow_against_the_run_has_no_error(self, on, off, n_frames) -> None:
        """Where the frames hold fewer than 25 independent samples of the
        presence by its own estimate, the error is withheld, as every mean's
        is: given, it read 0.72 to 0.76 of the spread over replicas."""
        from fastmdxplora.analysis.interactions import Contact
        from fastmdxplora.statistics import RESOLVED_SAMPLES, statistical_inefficiency

        rng = np.random.default_rng(13)
        given = 0
        for _ in range(60):
            present = self._telegraph(rng, n_frames, on, off)
            contacts = [Contact("hydrophobic", int(f), 1, 2, 0.35)
                        for f in np.flatnonzero(present)]
            found = occupancies(contacts, n_frames)
            if not found:
                continue
            error = found[0].uncertainty
            g = statistical_inefficiency(present.astype(float))
            if np.isfinite(error):
                given += 1
                assert g < 2.0 or n_frames / g >= RESOLVED_SAMPLES
        assert given <= 6
