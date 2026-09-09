"""Backbone N--H generalised order parameters, S^2.

The quantity NMR relaxation measures for each amide in a protein: how much
of a bond vector's orientation survives the fast internal motion, on a
scale where one is rigid and zero is freely reorienting. It is the most
direct comparison a trajectory has against a solution measurement, because
both describe the same picosecond-to-nanosecond reorientation of the same
bond, and unlike crystallographic B-factors it is not damped by a lattice.

S^2 is the plateau of the internal second-rank correlation function,

    C(t) = <P2(u(0) . u(t))>,     P2(x) = (3x^2 - 1) / 2

with the global tumbling removed. Computed here in the closed form that
plateau takes once the internal motion has decorrelated,

    S^2 = 3/2 * sum_ab <u_a u_b>^2 - 1/2

over the Cartesian components of the unit bond vector. \\cite{lipari1982}
The two routes agree to numerical precision on a trajectory long enough
for the correlation function to have a plateau at all, and the test suite
checks them against each other rather than trusting either alone.

**Global tumbling has to go first, and how it goes changes the answer.**
The measurement is of motion relative to the molecule, so the trajectory
is superposed before the vectors are taken. The atoms used for that
superposition are a choice, recorded with the result: aligning on a
flexible terminus drags the frame around with it and depresses S^2
everywhere else.

**A short trajectory reports S^2 too high, not too noisy.** Motion slower
than the run is motion the run never saw, and unsampled motion looks like
rigidity. That failure is one-sided, so it cannot be spotted by looking at
the scatter. It is checked here by computing the same order parameters
from each half of the trajectory: if the halves disagree, the number is
reported with a statement that it is an upper bound rather than presented
as a measurement.

References
----------
Lipari, G.; Szabo, A. Model-free approach to the interpretation of nuclear
magnetic resonance relaxation in macromolecules. 1. *J. Am. Chem. Soc.*
**1982**, 104, 4546.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis, superposed
from fastmdxplora.analysis.orchestrator import register_analysis


def read_reference(path: "str | Path") -> "dict[int, float]":
    """Per-residue reference order parameters from a text table.

    Two or three whitespace- or comma-separated columns -- residue number,
    S^2, and an optional uncertainty this ignores -- with `#` comments and
    any non-numeric header line skipped. Deliberately not a bespoke format:
    an experimental set arrives as whatever the depositing group wrote, and
    a loader that insists on one layout is a loader nobody can feed.
    """
    values: dict[int, float] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.replace(",", " ").split()
        if len(parts) < 2:
            continue
        try:
            residue, s2 = int(float(parts[0])), float(parts[1])
        except ValueError:
            continue          # a header row, or a comment without its hash
        values[residue] = s2
    if not values:
        raise ValueError(
            f"{path} holds no usable rows. Expected two columns, residue "
            "number and S^2, with anything else on a `#` line."
        )
    return values


def parse_residues(spec: "str | None") -> "set[int]":
    """Residue numbers from a spec like `1,72-76`.

    A comparison's residue set is a scientific choice and belongs in the
    configuration where it can be pre-registered, not in a post-hoc filter
    applied to a table after the correlation has been seen.
    """
    chosen: set[int] = set()
    for piece in str(spec or "").replace(" ", "").split(","):
        if not piece:
            continue
        if "-" in piece[1:]:
            # Split at the separating hyphen, not the first one: a
            # deposited structure can number an expression tag at or below
            # zero, and `partition` on "-2--1" hands back an empty lower
            # bound.
            cut = piece.index("-", 1)
            chosen.update(
                range(int(piece[:cut]), int(piece[cut + 1:]) + 1))
        else:
            chosen.add(int(piece))
    return chosen


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    from scipy.stats import spearmanr
    return float(spearmanr(a, b).statistic)

#: Amide hydrogen names in the force fields this software builds systems
#: with. ``H`` is the AMBER and PDB v3 name; ``HN`` is CHARMM's; ``H1``
#: appears on the first residue, where it is one of three and not an amide
#: proton at all, which is why the N-terminus is excluded below.
AMIDE_H_NAMES = ("H", "HN")

#: How far the two halves of a trajectory may disagree before the order
#: parameters are called an upper bound rather than a measurement. Chosen
#: against what the comparison is for: published S^2 sets are quoted to
#: about 0.02, so a disagreement larger than that is larger than the
#: difference anyone would be trying to detect.
HALVES_TOLERANCE = 0.02


def amide_pairs(topology: md.Topology,
                atom_indices: Any = None) -> list[tuple[int, int, int]]:
    """Backbone N and its amide hydrogen, per residue that has one.

    Returns (nitrogen index, hydrogen index, residue index).

    Proline is absent by chemistry rather than by convention: its backbone
    nitrogen is in the ring and carries no hydrogen, so there is no vector
    to measure and no experimental value to compare against. The first
    residue is absent for a different reason, that its nitrogen carries a
    charged amino group whose hydrogens are not the amide proton the
    measurement is about.
    """
    eligible = (None if atom_indices is None
                else {int(i) for i in atom_indices})
    pairs: list[tuple[int, int, int]] = []
    for residue in topology.residues:
        if residue.name == "PRO":
            continue
        if residue.index == 0:
            continue
        nitrogen = None
        hydrogen = None
        for atom in residue.atoms:
            if atom.name == "N":
                nitrogen = atom.index
            elif atom.name in AMIDE_H_NAMES:
                hydrogen = atom.index
        if nitrogen is None or hydrogen is None:
            continue
        if eligible is not None and not (
                nitrogen in eligible and hydrogen in eligible):
            continue
        pairs.append((nitrogen, hydrogen, residue.index))
    return pairs


def order_parameters(vectors: np.ndarray) -> np.ndarray:
    """S^2 from unit bond vectors of shape (frames, bonds, 3).

    The closed form of the correlation-function plateau. Summing the nine
    products of components rather than fitting a decay avoids choosing
    where a plateau begins, which on a noisy correlation function is a
    choice that moves the answer.
    """
    # <u_a u_b> for each bond: (bonds, 3, 3)
    products = np.einsum("fba,fbc->bac", vectors, vectors) / vectors.shape[0]
    return 1.5 * np.sum(products ** 2, axis=(1, 2)) - 0.5


def correlation_plateau(vectors: np.ndarray, *, lag_fraction: float = 0.5
                        ) -> np.ndarray:
    """S^2 the long way, as the tail of C(t) = <P2(u(0).u(t))>.

    Kept because it is the definition, and the closed form above is an
    identity that holds only once the internal motion has decorrelated.
    Where the two disagree, the trajectory is too short for the plateau to
    exist, which is worth knowing and is what the test suite uses this for.
    """
    n_frames = vectors.shape[0]
    start = int(n_frames * lag_fraction)
    lags = range(start, n_frames)
    totals = np.zeros(vectors.shape[1])
    count = 0
    for lag in lags:
        dot = np.einsum(
            "fba,fba->fb", vectors[:n_frames - lag], vectors[lag:])
        totals += np.mean(1.5 * dot ** 2 - 0.5, axis=0)
        count += 1
    return totals / max(count, 1)


class OrderParameters(Analysis):
    """Backbone N--H order parameters against residue number.

    Parameters
    ----------
    align_selection : str, default "name CA"
        Atoms used to remove global tumbling. Recorded with the result,
        because it is a choice that changes the answer.
    ref : int, default 0
        Frame the superposition is made onto.
    reference : str, optional
        Path to a table of measured order parameters -- residue number and
        S^2, whitespace or comma separated, `#` for comments. Given one,
        the analysis reports Pearson and Spearman correlations, the mean
        absolute deviation and the RMSD against it, and writes the measured
        values beside the computed ones. Without one it computes S^2 and
        makes no comparison.
    reference_exclude : str, optional
        Residues to leave out of that comparison, as `1,72-76`. A residue
        set is a scientific choice and belongs in the configuration where
        it can be pre-registered; chosen after the correlation has been
        seen, it is how this kind of comparison is made to pass.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``order_parameters.dat`` -- residue number and S^2, plus the reference
    value where one was given, so any residue subset can be recomputed from
    the deposited file rather than taken on trust.
    ``order_parameters.png`` -- S^2 against residue number.
    """

    #: Superposition needs a frame to define, and three atoms is the
    #: fewest that define one. Declared rather than only checked, because
    #: the orchestrator reads it at plan time and leaves the analysis out
    #: of a molecule too small to align, instead of running it to failure.
    min_atoms_to_align = 3

    #: A system without amide hydrogens poses no question here, rather
    #: than posing one this fails to answer. See the water gate in the
    #: orchestrator for the category this belongs to.
    requires_amide_hydrogens = True

    name = "order_parameters"
    description = "Backbone N--H order parameters (S^2)"
    default_selection = "protein"
    time_series = False

    def __init__(
        self,
        *,
        align_selection: str = "name CA",
        ref: int = 0,
        reference: "str | None" = None,
        reference_exclude: "str | None" = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.align_selection = str(align_selection)
        self.ref = int(ref)
        self.reference = str(reference) if reference else None
        self.reference_exclude = (
            str(reference_exclude) if reference_exclude else None)
        self.options.update(
            align_selection=self.align_selection,
            ref=self.ref,
            reference=self.reference,
            reference_exclude=self.reference_exclude,
            definition=(
                "Lipari-Szabo generalised order parameter, from the "
                "closed form of the internal second-rank correlation "
                "plateau after superposition"
            ),
        )

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        # The scope selection restricts which amides are measured, which is
        # how a study asks for one chain of several. The alignment set is a
        # separate choice, because what is measured and what the frame is
        # defined by are different questions.
        pairs = amide_pairs(traj.topology, self.select_atoms(traj))
        if not pairs:
            raise ValueError(
                "No backbone amide N--H pairs were found, so there are no "
                "order parameters to compute. A structure prepared without "
                "hydrogens does this, and so does a united-atom model: the "
                "measurement is of a bond vector, and the bond has to be "
                "present. Prepare the system with hydrogens, or compare "
                "fluctuations instead."
            )

        align_idx = traj.topology.select(self.align_selection)
        if len(align_idx) < 3:
            raise ValueError(
                f"The alignment selection {self.align_selection!r} matched "
                f"{len(align_idx)} atoms. Removing global tumbling needs at "
                "least three, and in practice a rigid core rather than the "
                "smallest set that satisfies the arithmetic."
            )

        n_frames = traj.n_frames
        ref = self.ref if self.ref >= 0 else n_frames + self.ref
        if not (0 <= ref < n_frames):
            raise ValueError(
                f"Reference frame {self.ref} is out of range for trajectory "
                f"with {n_frames} frames."
            )

        aligned = superposed(traj, frame=ref, atom_indices=align_idx)
        nitrogen = np.array([p[0] for p in pairs])
        hydrogen = np.array([p[1] for p in pairs])
        residues = np.array([p[2] for p in pairs])

        bonds = aligned.xyz[:, hydrogen, :] - aligned.xyz[:, nitrogen, :]
        lengths = np.linalg.norm(bonds, axis=2, keepdims=True)
        unit = bonds / np.where(lengths > 0, lengths, 1.0)

        s2 = order_parameters(unit)

        # The one-sided failure: motion slower than the run is invisible,
        # and invisible motion reads as rigidity. Halves disagreeing is the
        # evidence that the run has not seen everything it is averaging over.
        half = n_frames // 2
        self._halves_gap = None
        if half >= 2:
            first = order_parameters(unit[:half])
            second = order_parameters(unit[half:])
            self._halves_gap = float(np.max(np.abs(first - second)))

        labels = np.array([
            traj.topology.residue(int(i)).resSeq for i in residues
        ], dtype=np.int64)

        record: dict[str, Any] = {
            "vectors": len(pairs),
            "mean": float(np.mean(s2)),
            "minimum": float(np.min(s2)),
            "alignment": self.align_selection,
        }
        if self._halves_gap is not None:
            record["halves_max_difference"] = self._halves_gap
            if self._halves_gap > HALVES_TOLERANCE:
                record["not_a_measurement"] = (
                    f"The two halves of this trajectory give order "
                    f"parameters differing by up to {self._halves_gap:.3f}, "
                    f"against the {HALVES_TOLERANCE} that published sets are "
                    "quoted to. Motion slower than the run is motion the run "
                    "did not see, and unsampled motion is indistinguishable "
                    "from rigidity, so these values are an upper bound "
                    "rather than a measurement and the error is in one "
                    "direction. A longer run is the only remedy."
                )
        self._matched: "np.ndarray | None" = None
        if self.reference:
            self._matched = self._compare(labels, s2, record)

        self.findings["order_parameters"] = record

        if self._matched is not None:
            return np.column_stack(
                [labels.astype(float), s2, self._matched])
        return np.column_stack([labels.astype(float), s2])

    def _compare(self, labels: np.ndarray, s2: np.ndarray,
                 record: dict) -> np.ndarray:
        """Agreement with a measured set, reported without a verdict.

        The analysis computed S^2 and stopped there, so the manuscript's
        claim that trajectories are tested against NMR order parameters had
        no machinery behind it: the run produced the simulated numbers and
        nothing compared them to anything. This follows the pattern
        `bfactor_comparison` already uses -- match by residue, report the
        agreement, and say plainly what the agreement is not.

        Pearson **and** Spearman, because they answer different questions
        and the second is the robust one: a rank correlation asks whether
        the flexible residues are the same residues, which is what a
        correlation between a simulation and an experiment can support,
        while Pearson is moved by a handful of low-S^2 termini.
        """
        reference = read_reference(self.reference)
        excluded = parse_residues(self.reference_exclude)

        rows, missing = [], 0
        for residue, value in zip(labels.tolist(), s2.tolist()):
            residue = int(residue)
            if residue in excluded:
                continue
            measured = reference.get(residue)
            if measured is None:
                missing += 1
                continue
            rows.append((residue, float(value), float(measured)))

        aligned = np.full(labels.shape[0], np.nan, dtype=float)
        by_residue = {r: m for r, _, m in rows}
        for position, residue in enumerate(labels.tolist()):
            aligned[position] = by_residue.get(int(residue), np.nan)

        if len(rows) < 3:
            record["reference_comparison"] = {
                "refused": (
                    f"Only {len(rows)} residues matched between the "
                    f"trajectory and {Path(self.reference).name}, which is "
                    "too few to compare. The usual cause is a renumbering "
                    "during preparation, so the residues no longer line up "
                    "with the deposited set."),
                "source": str(self.reference),
            }
            return aligned

        table = np.array([(a, b) for _, a, b in rows], dtype=float)
        simulated, measured = table[:, 0], table[:, 1]
        difference = simulated - measured
        record["reference_comparison"] = {
            "source": str(self.reference),
            "residues_compared": len(rows),
            "residues_unmatched": missing,
            "residues_excluded": sorted(excluded) or None,
            "pearson_r": float(np.corrcoef(simulated, measured)[0, 1]),
            "spearman_rho": _spearman(simulated, measured),
            "mean_absolute_deviation": float(np.mean(np.abs(difference))),
            "rmsd": float(np.sqrt(np.mean(difference ** 2))),
            "mean_ratio": float(np.mean(simulated / np.where(
                measured != 0.0, measured, np.nan))),
            "mean_simulated": float(np.mean(simulated)),
            "mean_measured": float(np.mean(measured)),
            "what_this_is_not": (
                "An accuracy. A measured order parameter is a model-free "
                "fit to relaxation rates at one field and temperature, and "
                "a computed one is a plateau of a correlation function over "
                "whatever motion this trajectory length sampled -- so the "
                "two are the same quantity only to the extent that the run "
                "saw the motions the experiment averaged over. Slower "
                "motion missing from the run raises S^2 here and not there, "
                "which is a disagreement in one direction; see "
                "`halves_max_difference` for whether this run has that "
                "problem. Reported as a property of the force field as "
                "implemented, not as a pass or a failure."),
        }
        return aligned

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        ax.plot(result[:, 0], result[:, 1], marker="o", markersize=2.5,
                linewidth=1.0, label="simulation")
        if result.shape[1] > 2 and np.isfinite(result[:, 2]).any():
            ax.plot(result[:, 0], result[:, 2], marker="s", markersize=2.5,
                    linewidth=0.0, color=colour("SERIES"), label="measured")
            ax.legend(loc="lower right", fontsize="small")
        ax.set_ylim(0.0, 1.05)
        ax.axhline(1.0, color=colour("GUIDE"), linestyle=":", linewidth=0.8)

    def save_data(self, result: np.ndarray, path: Path) -> Path:
        """Three named columns where a reference was compared.

        The base writer stamps `no column header` on any array, which is
        true of two columns whose meaning the analysis name gives away and
        false of three."""
        if result.ndim != 2 or result.shape[1] < 3:
            return super().save_data(result, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savetxt(
            path, result, fmt="%.8e",
            header=("order_parameters: whitespace-delimited.\n"
                    "columns: residue  S2_simulated  S2_measured\n"
                    "S2_measured is nan where the reference has no value "
                    "for that residue, or where the residue was excluded "
                    "from the comparison."),
        )
        self._data_format = {
            "layout": "whitespace-delimited, columns named in the header",
            "read_with": "np.loadtxt(path)",
            "columns": ["residue", "S2_simulated", "S2_measured"],
        }
        return path

    def default_xlabel(self) -> str | None:
        return "Residue"

    def default_ylabel(self) -> str | None:
        return "S$^2$ (N--H order parameter)"


register_analysis(OrderParameters.name, OrderParameters)
