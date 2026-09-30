"""How often an interaction was there, and how much watching that rests on.

A contact seen in 3 frames of 500 and a contact seen in 450 are both "present".
Only one of them means anything, and reporting both as an occupancy -- 0.6 per
cent and 90 per cent -- hides that the first is three observations and the
second is four hundred and fifty.

That distinction is the whole of this module. Occupancy is easy; saying how
much observation it rests on is what stops a number being read as more than it
is. The same mistake in a different guise ran through this software's hydrogen
bond count for a long time: bonds present in few frames were dropped from every
frame, and the plot said one thing while the trajectory said another.

Two further cautions are built in rather than left to the reader.

**Frames are not independent.** Consecutive frames of a trajectory are
correlated -- a contact present at 10 ps is very likely present at 10.1 ps --
so the naive standard error of a mean over frames is too small, often by a
large factor. The number of *independent* observations is closer to the number
of times the contact formed or broke than to the number of frames, and that is
what is reported.

**A transition rate needs transitions.** A matrix built from three observed
switches is arithmetic, not kinetics. Where too few were seen, the count is
given and the rate is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = ["Occupancy", "occupancies", "residue_occupancies",
           "binding_modes", "mode_transitions"]


#: Below this many independent observations, a fraction is a count wearing a
#: percentage sign. The value is a judgement rather than a derivation: it is
#: the point at which the confidence interval on a proportion is wider than
#: the proportion is useful.
_ENOUGH_OBSERVATIONS = 5


@dataclass(frozen=True)
class Occupancy:
    """How often one interaction was present, and how well that is known."""

    kind: str
    ligand_atom: int
    protein_atom: int
    #: Frames in which it was present, of the frames examined.
    frames_present: int
    frames_total: int
    #: How many times it appeared after being absent. Consecutive frames are
    #: correlated, so this is much closer to the number of independent
    #: observations than the frame count is.
    episodes: int

    @property
    def fraction(self) -> float:
        return self.frames_present / self.frames_total if self.frames_total else 0.0

    @property
    def is_well_sampled(self) -> bool:
        """Whether the fraction rests on enough independent observation.

        A contact that formed once and stayed was observed once, however many
        frames it then occupied.
        """
        return self.episodes >= _ENOUGH_OBSERVATIONS

    @property
    def uncertainty(self) -> float:
        """Standard error of the fraction, counting episodes not frames.

        Using frames would give a number several times too small: a contact
        present in 450 consecutive frames has not been measured 450 times.
        """
        if self.episodes < 2:
            return float("nan")
        p = self.fraction
        return float(np.sqrt(max(p * (1.0 - p), 0.0) / self.episodes))

    def as_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ligand_atom": self.ligand_atom,
            "protein_atom": self.protein_atom,
            "frames_present": self.frames_present,
            "frames_total": self.frames_total,
            "fraction": round(self.fraction, 4),
            "episodes": self.episodes,
            "standard_error": (None if np.isnan(self.uncertainty)
                               else round(self.uncertainty, 4)),
            "well_sampled": self.is_well_sampled,
        }


def _episodes(present: np.ndarray) -> int:
    """How many times a contact appeared after being absent."""
    if present.size == 0:
        return 0
    started = np.diff(present.astype(np.int8), prepend=0) == 1
    return int(started.sum())


def occupancies(contacts: Any, n_frames: int) -> list[Occupancy]:
    """One entry per interaction, with the observation behind it.

    Interactions are grouped by what they are and which atoms they join, so a
    hydrogen bond that came and went is one entry with an occupancy rather
    than many entries with none.
    """
    if n_frames <= 0:
        return []

    seen: dict[tuple[str, int, int], np.ndarray] = {}
    for contact in contacts:
        key = (contact.kind, contact.ligand_atom, contact.protein_atom)
        if key not in seen:
            seen[key] = np.zeros(n_frames, dtype=bool)
        seen[key][contact.frame] = True

    found = [
        Occupancy(
            kind=kind,
            ligand_atom=ligand_atom,
            protein_atom=protein_atom,
            frames_present=int(present.sum()),
            frames_total=n_frames,
            episodes=_episodes(present),
        )
        for (kind, ligand_atom, protein_atom), present in seen.items()
    ]
    found.sort(key=lambda o: (-o.frames_present, o.kind))
    return found


def residue_occupancies(
    contacts: Any, n_frames: int, label_of: Any
) -> list[dict[str, Any]]:
    """Exact per-residue occupancy: the union of its pairs' frames.

    A residue usually touches a ligand through several atom pairs, and how
    often *the residue* is in contact is the number of frames in which any
    of them is. That is a union, and a union cannot be recovered from a
    table of per-pair occupancies: pairs that fire in the same frames give
    the maximum, pairs that never coincide give the sum, and any real case
    lies between. Exported as a bracket, comparing this software's numbers
    against another tool's meant comparing an interval against a point, and
    residues touched through many atoms carried intervals wide enough to
    swallow the difference under test.

    The masks that answer it exist while the contacts are being counted and
    were being discarded there. This keeps them long enough to take the
    union, so a residue occupancy leaves this software as a number rather
    than a range.

    ``label_of`` maps a protein atom index to the residue label it belongs
    to.
    """
    if n_frames <= 0:
        return []

    seen: dict[tuple[str, str], np.ndarray] = {}
    pairs: dict[tuple[str, str], int] = {}
    for contact in contacts:
        key = (str(label_of(contact.protein_atom)), contact.kind)
        if key not in seen:
            seen[key] = np.zeros(n_frames, dtype=bool)
            pairs[key] = 0
        seen[key][contact.frame] = True

    # Counted separately, because a pair contributes to the count whether or
    # not it added a frame the union did not already have.
    distinct: dict[tuple[str, str], set] = {}
    for contact in contacts:
        key = (str(label_of(contact.protein_atom)), contact.kind)
        distinct.setdefault(key, set()).add(
            (contact.ligand_atom, contact.protein_atom))

    rows = [
        {
            "residue": residue,
            "kind": kind,
            "frames_present": int(present.sum()),
            "frames_total": int(n_frames),
            "occupancy": float(present.sum()) / float(n_frames),
            "episodes": _episodes(present),
            "atom_pairs": len(distinct.get((residue, kind), ())),
        }
        for (residue, kind), present in seen.items()
    ]
    rows.sort(key=lambda r: (-r["frames_present"], r["residue"], r["kind"]))
    return rows


def binding_modes(
    contacts: Any, n_frames: int, *, minimum_occupancy: float = 0.1
) -> dict[str, Any]:
    """Which combinations of interactions occurred together, and how often.

    A binding mode is the set of interactions present in a frame. Frames
    sharing a set are in the same mode, and the modes are what a ligand moves
    between.

    Interactions too rare to be part of a mode are left out first, or every
    frame becomes its own mode: a single fleeting contact would otherwise
    split one arrangement into two.
    """
    if n_frames <= 0:
        return {"modes": [], "per_frame": [], "considered": []}

    keep = {
        (o.kind, o.ligand_atom, o.protein_atom)
        for o in occupancies(contacts, n_frames)
        if o.fraction >= minimum_occupancy
    }

    per_frame: list[frozenset] = [frozenset() for _ in range(n_frames)]
    building: list[set] = [set() for _ in range(n_frames)]
    for contact in contacts:
        key = (contact.kind, contact.ligand_atom, contact.protein_atom)
        if key in keep:
            building[contact.frame].add(key)
    per_frame = [frozenset(s) for s in building]

    counts: dict[frozenset, int] = {}
    for state in per_frame:
        counts[state] = counts.get(state, 0) + 1

    modes = [
        {
            "interactions": sorted(f"{k}:{l}-{p}" for k, l, p in state),
            "frames": count,
            "fraction": round(count / n_frames, 4),
        }
        for state, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    return {
        "modes": modes,
        "per_frame": [sorted(f"{k}:{l}-{p}" for k, l, p in s) for s in per_frame],
        "considered": sorted(f"{k}:{l}-{p}" for k, l, p in keep),
    }


def mode_transitions(
    per_frame: Any, *, minimum_transitions: int = 10
) -> dict[str, Any]:
    """How the ligand moved between binding modes, where that can be said.

    A transition matrix from a handful of observed switches is arithmetic
    rather than kinetics: three switches give a probability with an
    uncertainty larger than itself. So the switches are counted first, and the
    probabilities are reported only where there were enough of them.

    This is the check other tools do not make. Computing the matrix is easy;
    knowing whether the trajectory supports it is the part that decides
    whether the answer means anything.
    """
    states = [tuple(sorted(s)) for s in per_frame]
    if len(states) < 2:
        return {"observed_transitions": 0, "supported": False,
                "reason": "a transition needs at least two frames",
                "probabilities": None}

    switches: dict[tuple, dict[tuple, int]] = {}
    observed = 0
    for before, after in zip(states, states[1:]):
        switches.setdefault(before, {})
        switches[before][after] = switches[before].get(after, 0) + 1
        if before != after:
            observed += 1

    if observed < minimum_transitions:
        return {
            "observed_transitions": observed,
            "supported": False,
            "reason": (
                f"only {observed} change(s) of mode were seen, and a rate "
                f"estimated from fewer than {minimum_transitions} carries an "
                "uncertainty larger than itself. The counts are given; the "
                "probabilities are not, because they would read as an "
                "estimate of kinetics that this trajectory cannot support."
            ),
            "counts": {str(k): {str(kk): vv for kk, vv in v.items()}
                       for k, v in switches.items()},
            "probabilities": None,
        }

    probabilities = {}
    for before, targets in switches.items():
        total = sum(targets.values())
        probabilities[str(before)] = {
            str(after): round(count / total, 4) for after, count in targets.items()
        }
    return {
        "observed_transitions": observed,
        "supported": True,
        "reason": None,
        "counts": {str(k): {str(kk): vv for kk, vv in v.items()}
                   for k, v in switches.items()},
        "probabilities": probabilities,
    }
