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
change. Distances here are taken under the minimum-image convention where
the trajectory carries a cell, which puts the ends back together.

**And minimum image has its own limit.** A chain genuinely extended beyond
half the box has an end-to-end distance the convention cannot represent: it
returns the short way round, and the number that comes out is smaller than
the truth while looking entirely ordinary. So the distance is compared
against half the box and the run is marked where it comes close, rather
than left to be noticed by whoever plots it.

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

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError


def _terminal_atoms(
    topology, atom_name: str | None, chain_index: int
) -> tuple[int, int]:
    """Indices of the two ends of one chain, by the stated convention."""
    chain = topology.chain(chain_index)
    residues = list(chain.residues)
    if len(residues) < 2:
        raise StudyError(
            f"Chain {chain_index} has {len(residues)} residue(s). An "
            "end-to-end distance needs two ends, and a single residue has "
            "one.",
            code="analysis.sampling.too_few_residues",
        )

    first, last = residues[0], residues[-1]
    if atom_name is None:
        first_atoms = list(first.atoms)
        last_atoms = list(last.atoms)
        if not first_atoms or not last_atoms:
            raise StudyError(
                f"A terminal residue of chain {chain_index} holds no atoms.",
                code="analysis.selection.empty",
            )
        return int(first_atoms[0].index), int(last_atoms[-1].index)

    ends: list[int] = []
    for residue in (first, last):
        named = [a for a in residue.atoms if a.name == atom_name]
        if not named:
            present = sorted({a.name for a in residue.atoms})
            raise StudyError(
                f"Residue {residue.name}{residue.resSeq} at the end of chain "
                f"{chain_index} has no atom named {atom_name!r}. It holds "
                f"{', '.join(present[:8])}"
                f"{' and others' if len(present) > 8 else ''}. Set `atom` to "
                "one of those, or `atom: null` to measure between the first "
                "and last atom of the terminal residues.",
                code="analysis.selection.empty",
            )
        ends.append(int(named[0].index))
    return ends[0], ends[1]


class EndToEndDistance(Analysis):
    """Distance between the two ends of a chain, per frame.

    Parameters
    ----------
    atom : str or None, default "CA"
        Which atom of each terminal residue marks the end. ``None`` takes
        the first atom of the first residue and the last atom of the last,
        for chains with no named backbone.
    by_chain : bool, default False
        Measure every chain separately and report one column each, in
        addition to the first chain's distance. With a single chain this
        changes nothing.
    selection : str, optional
        MDTraj atom selection applied before the chains are read. Defaults
        to ``"protein"``, so solvent does not present itself as a chain.

    Output
    ------
    ``end_to_end.dat`` -- one column, the distance in nm per frame; or
    ``frame, chain0, chain1, ...`` when ``by_chain=True``.
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
        by_chain: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.atom: str | None = None if atom is None else str(atom)
        self.by_chain: bool = bool(by_chain)
        self.options.update(atom=self.atom, by_chain=self.by_chain)

    def _distances(
        self, traj: md.Trajectory, pair: tuple[int, int]
    ) -> np.ndarray:
        periodic = traj.unitcell_lengths is not None
        return md.compute_distances(
            traj, np.array([pair]), periodic=periodic
        )[:, 0].astype(np.float64)

    def _note_if_the_convention_is_strained(
        self, traj: md.Trajectory, distances: np.ndarray
    ) -> None:
        """Say when the chain is long enough for minimum image to mislead."""
        if traj.unitcell_lengths is None:
            self.findings["periodic"] = (
                "This trajectory carries no unit cell, so distances are taken "
                "as written. A chain wrapped across a periodic boundary in a "
                "run whose box was stripped would read as fully extended."
            )
            return
        half_box = float(np.min(traj.unitcell_lengths)) / 2.0
        longest = float(np.max(distances))
        if longest > 0.8 * half_box:
            self.findings["not_a_measurement"] = (
                f"The chain reaches {longest:.3f} nm, against half the "
                f"smallest box dimension at {half_box:.3f} nm. The "
                "minimum-image convention returns the shorter of the two ways "
                "round, so an extension past half the box is reported as a "
                "contraction and the curve turns back on itself without any "
                "sign that it has. A larger box is what would change this."
            )

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

        if not self.by_chain and n_chains > 1:
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
        pairs: list[tuple[int, int]] = []
        for chain_index in range(n_chains if self.by_chain else 1):
            pair = _terminal_atoms(sub.topology, self.atom, chain_index)
            pairs.append(pair)
            columns.append(self._distances(sub, pair))

        result = (
            columns[0] if len(columns) == 1 else np.column_stack(columns)
        )
        self.findings["ends"] = {
            "atom": self.atom,
            "chains": len(columns),
            "atom_pairs": [[int(i), int(j)] for i, j in pairs],
        }
        self._note_if_the_convention_is_strained(
            sub, result if result.ndim == 1 else result.max(axis=1)
        )
        return result

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x = (
            self.frame_axis(self._traj_for_plot)[0]
            if self._traj_for_plot is not None
            else np.arange(result.shape[0])
        )
        if result.ndim == 1:
            ax.plot(x, result, linewidth=1.4)
        else:
            for i in range(result.shape[1]):
                ax.plot(x, result[:, i], linewidth=1.2, label=f"chain {i}")
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
