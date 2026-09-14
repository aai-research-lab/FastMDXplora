"""Simulated fluctuations against crystallographic B-factors.

The oldest comparison between a trajectory and an experiment, and the one
most often quoted without its caveats. A refined B-factor and a simulated
RMSF are related through

    B = (8 pi^2 / 3) <u^2>,

so a per-residue B converts to the fluctuation amplitude a crystal implies,
in the same units the trajectory reports. What comes out is a correlation,
and this analysis reports it with the reasons it is not an accuracy.

**A crystallographic B is not only motion.** It absorbs static disorder
across the molecules in the lattice, the refinement's own restraints, and
whatever TLS or occupancy model was used; and the lattice itself damps the
loop excursions a solution trajectory is free to make. The result is that
B-factors bound loop amplitudes from below, so a simulation that agrees
everywhere may be reporting a protein held too tightly, and one that
exceeds them in loops may be right. The correlation is worth having and a
regression slope is not, which is why only the first is reported.

**mdtraj does not carry B-factors**, so they are read from the deposited
file rather than from the trajectory's topology. That also keeps the
comparison against the structure as deposited rather than against the
prepared system, whose B column is whatever the preparation left there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis, superposed
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import MissingResultError

#: B = (8 pi^2 / 3) <u^2>, so <u^2> = 3B / (8 pi^2), in the file's units of
#: square Angstroms.
B_TO_MSF = 3.0 / (8.0 * np.pi ** 2)


def bfactors_from_pdb(path: str | Path) -> dict[tuple[int, int], float]:
    """Per-residue CA B-factors, keyed by (chain order, residue number).

    Read by column rather than by splitting on whitespace, because the PDB
    format is fixed-width and a five-figure atom serial or a four-character
    residue name runs its fields together: split on spaces and the B column
    becomes whatever happened to be next to it.

    Chains are keyed by the order their identifiers first appear, which is
    what survives preparation: PDBFixer keeps chain order while it may not
    keep the identifiers themselves.
    """
    order: dict[str, int] = {}
    out: dict[tuple[int, int], float] = {}
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("ATOM"):
            continue
        if line[12:16].strip() != "CA":
            continue
        altloc = line[16]
        if altloc not in (" ", "A"):
            continue
        chain = line[21]
        if chain not in order:
            order[chain] = len(order)
        try:
            resseq = int(line[22:26])
            value = float(line[60:66])
        except ValueError:
            continue
        out[(order[chain], resseq)] = value
    return out


def has_crystallographic_bfactors(path: str | Path) -> bool:
    """Whether a file carries B-factors that mean anything.

    A minimised or generated structure writes zeros, and a comparison
    against a column of zeros is not a comparison. One non-zero value is
    not enough either: a file where a handful of atoms carry a placeholder
    would pass, so this asks that most of them do.
    """
    try:
        values = list(bfactors_from_pdb(path).values())
    except OSError:
        return False
    if len(values) < 3:
        return False
    return float(np.mean(np.array(values) > 0.0)) > 0.5


class BFactorComparison(Analysis):
    """Per-residue RMSF against the fluctuations a crystal implies.

    Parameters
    ----------
    structure : str, optional
        The deposited file to read B-factors from. Discovered from the run
        directory when not given.
    align_selection : str, default "name CA"
        Atoms used to remove rigid-body motion before fluctuations are
        measured, as in the RMSF analysis.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``bfactor_comparison.dat`` -- residue number, simulated RMSF in nm, and
    the RMSF implied by the deposited B-factor.
    """

    #: As for every analysis that superposes: three atoms is the fewest
    #: that define a frame, and declaring it lets the orchestrator leave
    #: this out of a molecule too small rather than run it to failure.
    min_atoms_to_align = 3

    #: Only where a deposited structure with real B-factors was found. A
    #: run built from a generated or minimised coordinate file poses no
    #: question here rather than failing to answer one.
    requires_crystallographic_bfactors = True

    name = "bfactor_comparison"
    description = "Simulated RMSF against crystallographic B-factors"
    default_selection = "protein"
    time_series = False

    def __init__(
        self,
        *,
        structure: str | None = None,
        align_selection: str = "name CA",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.structure = structure
        self.align_selection = str(align_selection)
        self.options.update(
            structure=self.structure,
            align_selection=self.align_selection,
            conversion="B = (8 pi^2 / 3) <u^2>, isotropic",
        )

    def _structure_path(self) -> Path | None:
        if self.structure:
            path = Path(self.structure)
            return path if path.is_file() else None
        here = Path(self.output_dir)
        for parent in (here.parent.parent, here.parent, here):
            for candidate in (
                parent / "setup" / "input.pdb",
                parent / "setup" / "structure.pdb",
                parent / "input.pdb",
            ):
                if candidate.is_file() and has_crystallographic_bfactors(
                        candidate):
                    return candidate
        return None

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        path = self._structure_path()
        if path is None:
            raise MissingResultError(
                "No deposited structure with B-factors was found beside this "
                "run, so there is nothing to compare fluctuations against. "
                "Give `structure` pointing at the file the study started "
                "from. A prepared or minimised coordinate file will not do: "
                "its B column is whatever the preparation wrote there."
            , code="analysis.data.absent")

        crystal = bfactors_from_pdb(path)
        align_idx = traj.topology.select(self.align_selection)
        if len(align_idx) < 3:
            raise StudyError(
                f"The alignment selection {self.align_selection!r} matched "
                f"{len(align_idx)} atoms, and removing rigid-body motion "
                "needs at least three."
            , code="analysis.selection.arity", expression=self.align_selection)

        aligned = superposed(traj, frame=0, atom_indices=align_idx)
        # Alpha carbons within the scope selection: comparing a chain the
        # study excluded would report a correlation over residues it was
        # not asked about.
        scope = set(int(i) for i in self.select_atoms(traj))
        alpha = np.array([
            int(i) for i in traj.topology.select("name CA and protein")
            if int(i) in scope
        ], dtype=int)
        if len(alpha) == 0:
            raise StudyError(
                "No alpha carbons in the selection, so there is no "
                "per-residue fluctuation to compare."
            , code="analysis.sampling.too_few_frames")

        xyz = aligned.xyz[:, alpha, :]
        rmsf = np.sqrt(np.mean(
            np.sum((xyz - xyz.mean(axis=0)) ** 2, axis=2), axis=0))

        rows: list[tuple[float, float, float]] = []
        missing = 0
        for position, atom_index in enumerate(alpha):
            residue = traj.topology.atom(int(atom_index)).residue
            key = (residue.chain.index, residue.resSeq)
            value = crystal.get(key)
            if value is None or value <= 0.0:
                missing += 1
                continue
            # Angstroms in the file, nanometres in the trajectory.
            implied_nm = float(np.sqrt(B_TO_MSF * value)) / 10.0
            rows.append((float(residue.resSeq),
                         float(rmsf[position]), implied_nm))

        if len(rows) < 3:
            raise StudyError(
                f"Only {len(rows)} residues could be matched between the "
                f"trajectory and {path.name}, which is too few to compare. "
                "The usual cause is that the study renumbered or renamed "
                "chains during preparation, so the residues no longer line "
                "up with the deposited file."
            , code="analysis.sampling.too_few_frames")

        table = np.array(rows, dtype=float)
        simulated, implied = table[:, 1], table[:, 2]
        correlation = float(np.corrcoef(simulated, implied)[0, 1])
        # Rank as well as value. Pearson on this pair is moved by the
        # termini, which are the largest fluctuations on both sides and the
        # least comparable: a crystallographic tail is disordered where a
        # solution tail is mobile, and those are different statements. The
        # rank correlation asks the question the comparison can actually
        # answer -- whether the flexible residues are the same residues --
        # and published values for this comparison are quoted both ways.
        from scipy.stats import spearmanr
        rank_correlation = float(spearmanr(simulated, implied).statistic)

        self.findings["bfactor_comparison"] = {
            "residues_compared": len(rows),
            "residues_unmatched": missing,
            "pearson_r": correlation,
            "spearman_rho": rank_correlation,
            "source": str(path),
            "mean_simulated_nm": float(np.mean(simulated)),
            "mean_implied_nm": float(np.mean(implied)),
            "what_this_is_not": (
                "A correlation, not an accuracy. A refined B-factor carries "
                "static disorder across the lattice, the refinement's own "
                "restraints and its TLS or occupancy model, and the lattice "
                "damps the loop excursions a solution trajectory is free to "
                "make. B-factors therefore bound loop amplitudes from "
                "below: agreement everywhere can mean a protein held too "
                "tightly, and exceeding them in loops can be correct. No "
                "regression slope is reported, because the quantities are "
                "not the same measurement."
            ),
        }
        return table

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        ax.plot(result[:, 0], result[:, 1], linewidth=1.2, label="simulated")
        ax.plot(result[:, 0], result[:, 2], linewidth=1.2,
                linestyle="--", label="from B-factors")
        ax.legend(loc="best", fontsize="small")

    def default_xlabel(self) -> str | None:
        return "Residue"

    def default_ylabel(self) -> str | None:
        return "RMSF (nm)"


register_analysis(BFactorComparison.name, BFactorComparison)
