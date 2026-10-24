"""The distance between the two ends of a chain, per frame.

For a polymer or an unstructured peptide this is the coarsest description
of extension there is, and the one polymer theory is written in: the
mean square end-to-end distance is what a Gaussian chain's statistics
predict, and its ratio to the radius of gyration is a shape descriptor that
does not depend on chain length.

Two hazards, both silent.

**A molecule can be broken across the periodic boundary.** A chain whose
two halves have been wrapped to opposite faces of the cell has a raw
end-to-end distance of nearly a box length, which is not a conformational
change.

**And the minimum-image convention has its own limit.** Taken between the
two ends directly it returns the shorter of the two ways round the cell, so
a chain extended past half the cell's narrowest width reads shorter than it
is while looking entirely ordinary: a straight 10-residue chain, 3.420 nm
from end to end in a 4.62 nm cube, read 1.200 nm.

Both are answered by walking the chain. The end-to-end vector is the sum of
the minimum-image steps from one residue to the next along the chain, from
the first end through one atom of each residue to the last::

    R = sum_k mic(x_{k+1} - x_k),   distance = |R|

Each step is a few tenths of a nanometre, far inside the convention's
limit, so the sum is the same for a chain stored whole and one wrapped
into the cell, and it is not bounded by the box.

What the box still bounds is whether the chain is an isolated chain. A
chain any part of which comes within a nonbonded cutoff (taken as 1.0 nm,
the cutoff setup uses unless told otherwise) of a periodic image of itself
is interacting with its own copy, and its extension then says as much about
the cell as about the chain. That is the image check's finding
(:mod:`fastmdxplora.analysis.image_check`), the one every analysis of the
frames reads: the whole chain, not its two ends, whose check passed a chain
that came to 0.261 nm of its copy. Where it falls short the run is marked
and the mean is given without an error bar. The ends' own distance to the
other end's image is still recorded (``findings.periodic``).

Which atoms are "the ends" is stated rather than inferred. ``atom="CA"``
-- the default -- measures between the alpha carbons of the first and last
residue, which is what an end-to-end distance means for a protein.
``atom=None`` takes the first atom of the first residue and the last atom
of the last, which is what it means for a bead chain that has no CA.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis, narrowest_width
from fastmdxplora.analysis.image_check import (
    CUTOFF_NM,
    run_cutoff,
    solute_atoms,
    solute_image_record,
)
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError


def _end_residues(topology, atom_name: str | None, chain_index: int) -> tuple[int, int]:
    """Positions in the chain of its two end residues: the first and last
    residue holding the named atom, or the chain's first and last residue
    where no atom is named. A cap with no alpha carbon (ACE, NME, NHE,
    NH2) is passed over, so the distance is between the chain's first and
    last alpha carbons, as it was before the caps were counted as protein
    (an NHE- or NH2-capped peptide was refused then)."""
    residues = list(topology.chain(chain_index).residues)
    if len(residues) < 2:
        raise StudyError(
            f"Chain {chain_index} has {len(residues)} residue(s). An "
            "end-to-end distance needs two ends, and a single residue has "
            "one.",
            code="analysis.sampling.too_few_residues",
        )
    if atom_name is None:
        return 0, len(residues) - 1
    holding = [i for i, residue in enumerate(residues)
               if any(a.name == atom_name for a in residue.atoms)]
    if len(holding) < 2:
        residue = residues[0] if not holding or holding[0] != 0 else residues[-1]
        present = sorted({a.name for a in residue.atoms})
        said = (f"Chain {chain_index} has no atom named {atom_name!r}" if not holding
                else f"Chain {chain_index} has one residue with an atom named {atom_name!r}")
        raise StudyError(
            f"{said}, and an end-to-end distance needs two ends. "
            f"Residue {residue.name}{residue.resSeq} holds "
            f"{', '.join(present[:8])}{' and others' if len(present) > 8 else ''}. "
            "Set `atom` to one of those, or `atom: null` to measure between "
            "the first and last atom of the terminal residues.",
            code="analysis.selection.empty",
        )
    return holding[0], holding[-1]


def _terminal_atoms(
    topology, atom_name: str | None, chain_index: int
) -> tuple[int, int]:
    """Indices of the two ends of one chain, by the stated convention."""
    residues = list(topology.chain(chain_index).residues)
    start, end = _end_residues(topology, atom_name, chain_index)
    first, last = residues[start], residues[end]
    if atom_name is None:
        first_atoms = list(first.atoms)
        last_atoms = list(last.atoms)
        if not first_atoms or not last_atoms:
            raise StudyError(
                f"A terminal residue of chain {chain_index} holds no atoms.",
                code="analysis.selection.empty",
            )
        return int(first_atoms[0].index), int(last_atoms[-1].index)
    return tuple(int(next(a for a in residue.atoms if a.name == atom_name).index)
                 for residue in (first, last))  # type: ignore[return-value]


#: How close the chain may come to a periodic image of itself before it is
#: taken to be interacting with its own copy: the image check's cutoff, the
#: nonbonded cutoff setup uses unless a force field or the person sets another.
SELF_IMAGE_NM = CUTOFF_NM


def _path(topology, atom_name: str | None, chain_index: int,
          ends: tuple[int, int]) -> list[int]:
    """The atoms walked from one end to the other, one per residue.

    The named atom of each residue between the ends, or its first atom
    where it has no atom of that name (or no name is set). Consecutive
    entries are then a residue apart, which is what keeps every step inside
    the minimum-image convention's limit.
    """
    residues = list(topology.chain(chain_index).residues)
    start, end = _end_residues(topology, atom_name, chain_index)
    path = [ends[0]]
    for residue in residues[start + 1:end]:
        atoms = list(residue.atoms)
        if not atoms:
            continue
        named = [a for a in atoms if atom_name is not None and a.name == atom_name]
        path.append(int((named or atoms)[0].index))
    path.append(ends[1])
    return path


def _nearest_self_image(vectors: np.ndarray, box: np.ndarray) -> np.ndarray:
    """Per frame, the distance from the end to the nearest periodic image of
    the start other than the start itself: ``min |R - L|`` over the nonzero
    lattice vectors ``L``, searched over two cells either side of the image
    the fractional rounding picks, which covers the reduced cells OpenMM
    writes."""
    shifts = np.array(list(np.ndindex(5, 5, 5)), dtype=np.float64) - 2.0
    inverse = np.linalg.inv(box)
    guess = np.rint(np.einsum("fi,fij->fj", vectors, inverse))
    offsets = guess[:, None, :] + shifts[None, :, :]                  # (F, 125, 3)
    lattice = np.einsum("fkj,fji->fki", offsets, box)
    distance = np.linalg.norm(vectors[:, None, :] - lattice, axis=2)
    distance[np.all(offsets == 0.0, axis=2)] = np.inf                  # the start itself
    return distance.min(axis=1)


class EndToEndDistance(Analysis):
    """Distance between the two ends of a chain, per frame.

    Parameters
    ----------
    atom : str or None, default "CA"
        Which atom of each terminal residue marks the end. ``None`` takes
        the first atom of the first residue and the last atom of the last,
        for chains with no named backbone.
    by_chain : bool, default True
        Measure every chain separately and report one column each; a
        selection of one chain gives its one distance either way. The
        default, so a run of a dimer measures each copy rather than failing.
        ``False`` asks for one distance, and is refused for several chains.
    selection : str, optional
        MDTraj atom selection applied before the chains are read. Defaults
        to ``"protein"``, so solvent does not present itself as a chain.

    Output
    ------
    ``end_to_end.dat`` -- one column, the distance in nm per frame; or, for
    several chains, a table with one column per chain, named by chain.
    """

    name = "end_to_end"
    description = "End-to-end distance"
    time_series = True
    reweightable = (None, "End-to-end distance (nm)")
    default_selection = "protein"

    def __init__(
        self,
        *,
        atom: str | None = "CA",
        by_chain: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.atom: str | None = None if atom is None else str(atom)
        self.by_chain: bool = bool(by_chain)
        self.options.update(atom=self.atom, by_chain=self.by_chain)

    def _vectors(self, traj: md.Trajectory, path: list[int]) -> tuple[np.ndarray, float]:
        """The end-to-end vector per frame, as the sum of the steps along
        ``path``, and the longest single step."""
        if len(path) < 2:
            return np.zeros((traj.n_frames, 3)), 0.0
        pairs = np.array([[path[i], path[i + 1]] for i in range(len(path) - 1)])
        steps = md.compute_displacements(
            traj, pairs, periodic=traj.unitcell_vectors is not None
        ).astype(np.float64)
        longest = float(np.max(np.linalg.norm(steps, axis=2)))
        return steps.sum(axis=1), longest

    def _note_if_the_box_bounds_the_chain(
        self, traj: md.Trajectory, vectors: list[np.ndarray], longest_step: float
    ) -> None:
        """Say when the chain reaches a periodic image of itself, as the image
        check found it: handed to this analysis with the frames, or, run on
        its own, found here by the same check of the chain measured."""
        if traj.unitcell_vectors is None:
            self.findings["periodic"] = (
                "This trajectory carries no unit cell, so distances are taken "
                "as written. A chain wrapped across a periodic boundary in a "
                "run whose box was stripped would read as fully extended."
            )
            return
        box = np.asarray(traj.unitcell_vectors, dtype=np.float64)
        narrowest = float(np.min(narrowest_width(traj)))
        nearest = float(min(np.min(_nearest_self_image(v, box)) for v in vectors))
        longest = float(max(np.max(np.linalg.norm(v, axis=1)) for v in vectors))
        self.findings["periodic"] = {
            "narrowest_width_nm": narrowest,
            "longest_end_to_end_nm": longest,
            "nearest_self_image_nm": nearest,
            "longest_step_nm": longest_step,
        }
        image = self.solute_image
        if not isinstance(image, dict) or "reaches_its_image" not in image \
                or self.solute_image_series is None:
            try:
                # Its own check, the image check not having run (excluded by
                # name, or this analysis run alone): against the run's own
                # cutoff, read as the image check reads it, and said as its own.
                cutoff, cutoff_from = run_cutoff(getattr(self, "study_dir", None))
                self.solute_image_series, image = solute_image_record(
                    traj, solute_atoms(traj.topology, self.selection)[0],
                    cutoff_nm=cutoff, cutoff_from=cutoff_from)
                image["checked_by"] = "end_to_end"
                self.solute_image = image
            except StudyError as exc:  # a chain of beads with no elements
                image = {"said": str(exc)}
        self.findings["periodic"]["nearest_self_image_whole_chain_nm"] = image.get("nearest_nm")
        self._chain_said = (
            f"The chain reaches {longest:.3f} nm from end to end (the cell's "
            f"narrowest width is {narrowest:.3f} nm, and an end comes within "
            f"{nearest:.3f} nm of a periodic image of the other end). In the "
            "frames it sees its own image, within the cutoff, it interacts with "
            "its own copy, and its extension describes the cell as much as the "
            "chain.")
        reasons = []
        if longest_step > 0.5 * narrowest:
            reasons.append(
                f"A step between consecutive residues along the chain reaches "
                f"{longest_step:.3f} nm, more than half the cell's narrowest "
                f"width of {narrowest:.3f} nm, so the minimum-image convention "
                "cannot say which way round that step went and the summed "
                "distance may be wrong. A chain with a break in it does this."
            )
        self._step_reasons = list(reasons)
        # Over every frame here; over the frames the mean uses once it is
        # recorded (`_record_what_the_mean_is_worth`).
        self._say_the_image(self._image_note_for(traj, None))

    def _say_the_image(self, note: dict[str, Any] | None) -> None:
        """The chain's reasons, with the image's note where it has one."""
        reasons = list(getattr(self, "_step_reasons", []))
        if note:
            # The note first: what the Overview gives of a reason is its
            # first sentence, and that is the one with the frames.
            reasons.append(f"{note['said']} {getattr(self, '_chain_said', '')}".strip())
        if reasons:
            self.findings["not_a_measurement"] = " ".join(reasons)
        else:
            self.findings.pop("not_a_measurement", None)

    def _record_what_the_mean_is_worth(self, traj: md.Trajectory) -> None:
        """The base class's record, qualified where the chain cannot be
        measured across the box or met its own image in the frames the mean
        uses: the reason goes where every reader of the mean looks, and the
        mean carries no error bar.

        End-to-end alone withholds its error for the image, as it did before
        the whole chain was checked (then where an end came within 1.0 nm of
        the other end's image), now from the one finding every analysis
        reads (:meth:`_image_note_for`), over the frames its mean uses and
        against the run's cutoff. The ends are part of the chain, so a run
        whose end met the other end's image in those frames is withheld as
        it was. Every other analysis keeps its error and carries the note."""
        super()._record_what_the_mean_is_worth(traj)
        steps = " ".join(getattr(self, "_step_reasons", []))
        if steps:
            self._withhold_the_error(traj, steps,
                                     because="a step along the chain spans half the cell")
        mean = self.findings.get("mean")
        note = self._image_note_for(traj, mean if isinstance(mean, dict) else None)
        self._say_the_image(note)
        if note:
            said = f"{note['said']} {getattr(self, '_chain_said', '')}".strip()
            if said not in str((mean or {}).get("not_a_measurement") or ""):
                self._withhold_the_error(traj, said,
                                         because="chain sees its periodic image")

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        atom_idx = self.select_atoms(traj)
        sub = traj.atom_slice(atom_idx) if len(atom_idx) < traj.n_atoms else traj

        n_chains = sub.topology.n_chains
        if n_chains == 0:
            raise StudyError(
                f"Selection {self.selection!r} left no chains to measure "
                "between.",
                code="analysis.selection.empty",
            )

        by_chain = self.by_chain
        if not by_chain and n_chains > 1:
            raise StudyError(
                f"Selection {self.selection!r} spans {n_chains} chains, and "
                "an end-to-end distance is a property of one chain. "
                "Measuring from the first chain's start to the last chain's "
                "end would return a number describing neither. Set "
                "`by_chain: true` for one column per chain, or narrow the "
                "selection to a single chain.",
                code="analysis.selection.arity",
            )

        columns: list[np.ndarray] = []
        vectors: list[np.ndarray] = []
        pairs: list[tuple[int, int]] = []
        longest_step = 0.0
        for chain_index in range(n_chains if by_chain else 1):
            pair = _terminal_atoms(sub.topology, self.atom, chain_index)
            pairs.append(pair)
            vector, step = self._vectors(
                sub, _path(sub.topology, self.atom, chain_index, pair))
            longest_step = max(longest_step, step)
            vectors.append(vector)
            columns.append(np.linalg.norm(vector, axis=1))

        if len(columns) == 1:
            result = columns[0]
        else:
            # A table naming each chain, rather than bare columns: nothing
            # reads the first of several chains' distances as the run's
            # end-to-end distance, which the reweighting and the mean both
            # did with a column of numbers.
            import pandas as pd

            result = pd.DataFrame({
                f"chain {getattr(sub.topology.chain(i), 'chain_id', None) or i}": column
                for i, column in enumerate(columns)})
        self.findings["ends"] = {
            "atom": self.atom,
            "chains": len(columns),
            "atom_pairs": [[int(i), int(j)] for i, j in pairs],
            "computed_as": ("sum of minimum-image steps between consecutive "
                            "residues along the chain"),
        }
        self._note_if_the_box_bounds_the_chain(sub, vectors, longest_step)
        return result

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x = (
            self.frame_axis(self._traj_for_plot)[0]
            if self._traj_for_plot is not None
            else np.arange(result.shape[0])
        )
        if np.ndim(result) == 1:
            ax.plot(x, result, linewidth=1.4)
        else:
            for label in result.columns:
                ax.plot(x, result[label].to_numpy(), linewidth=1.2, label=label)
            ax.legend(loc="best")

    _traj_for_plot: md.Trajectory | None = None

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        return self.frame_axis(self._traj_for_plot)[1]

    def default_ylabel(self) -> str | None:
        return "End-to-end distance (nm)"


register_analysis(EndToEndDistance.name, EndToEndDistance)
