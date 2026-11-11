"""Which residues move together: the dynamic cross-correlation map.

For every pair of the selection's atoms (the alpha carbons unless told
otherwise), how alike their displacements from their mean positions are
over the frames (Ichiye and Karplus, Proteins 11, 205 (1991))::

    C_ij = <dr_i . dr_j> / sqrt(<dr_i^2> <dr_j^2>)

It is 1 for two atoms that always move the same way by proportionate
amounts, -1 for two that always move opposite ways, and near 0 for two
whose motions have nothing to do with each other. Two domains closing on a
hinge show as two blocks, each positive within and negative between.

Four things to know before reading one.

**It is a property of the superposition.** Displacements are measured
after each frame is fitted onto the mean structure, found by fitting,
averaging and fitting again until the mean stops moving. Fitted on the
whole selection, a small mobile part is correlated against the frame the
large rigid part sets; fitted on one domain (``fit_selection``), the other
domain moves as one against it. Neither is wrong, and they are different
maps.

**It sees motion along one line only.** Two atoms moving in perfect
concert at right angles (the two ends of a turning lever) have a dot
product of zero and a correlation of zero. A map with no correlation
between two parts does not say they are independent (Lange and Grubmüller,
Proteins 62, 1053 (2006), whose mutual-information measure is not computed
here).

**Neighbours along the chain are always correlated**, being bonded. The
pairs listed as most correlated leave out those fewer than ``min_separation``
residues apart in one chain.

**A correlation needs sampling.** Each element is an average over the
frames, and a slow motion crossed once correlates everything it carries
with everything else. The map is made again from the first half of the
frames and from the second, each fitted to its own mean, and how well the
two agree (the correlation between their elements, the chain's neighbours
left out) is recorded: where they do not agree the run has not sampled the
motions the map shows, and the result says so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.analysis.pore_profile import _fitted
from fastmdxplora.refusals import StudyError

#: The most atoms a map is made of: the matrix is their number squared.
MOST_ATOMS = 6000
#: The fewest frames an average of products is taken over.
FEWEST_FRAMES = 20
#: How many of the strongest pairs each way are listed.
LISTED = 100
#: Below this agreement between the halves' maps, the map is said not to be
#: sampled.
AGREEMENT_WANTED = 0.5


def cross_correlation(displaced: np.ndarray) -> np.ndarray:
    """The correlation of every pair of atoms' displacements.

    ``displaced`` is (frames, atoms, 3), each atom's displacement from its
    mean position. An atom that does not move has no correlation with
    anything, and is given zero (one with itself).
    """
    frames, atoms = displaced.shape[:2]
    flat = displaced.reshape(frames, atoms * 3)
    products = np.zeros((atoms, atoms))
    for axis in range(3):
        along = flat[:, axis::3]
        products += along.T @ along
    size = np.sqrt(np.diag(products))
    still = size == 0
    size[still] = 1.0
    found = products / np.outer(size, size)
    found[still, :] = found[:, still] = 0.0
    np.fill_diagonal(found, 1.0)
    return np.clip(found, -1.0, 1.0)


def fitted_to_the_mean(xyz: np.ndarray, on: np.ndarray, rounds: int = 10) -> np.ndarray:
    """``xyz`` (frames, atoms, 3) with every frame fitted on the atoms ``on``
    to their mean structure: fitted to the first frame, averaged, and fitted
    to the average until it moves less than 1e-5 nm."""
    reference = np.asarray(xyz[0][on], dtype=np.float64)
    fitted = _fitted(xyz, on, reference)
    for _ in range(rounds):
        mean = fitted[:, on].mean(axis=0)
        moved = float(np.sqrt(((mean - reference) ** 2).sum(axis=1).mean()))
        reference = mean
        fitted = _fitted(xyz, on, reference)
        if moved < 1e-5:
            break
    return fitted


class CrossCorrelation(Analysis):
    """The dynamic cross-correlation of the selection's atoms.

    Options
    -------
    fit_selection : str, default "auto"
        The atoms each frame is fitted on before displacements are taken.
        ``auto`` is the selection itself; name one domain to see the rest
        move against it.
    min_separation : int, default 4
        Pairs fewer than this many residues apart in one chain are left out
        of the pairs listed and of the agreement between the halves.

    Output
    ------
    ``cross_correlation.dat`` -- comma-separated, the pairs most correlated
    and most anti-correlated (up to 100 each way): ``residue_i``,
    ``chain_i``, ``residue_j``, ``chain_j``, ``correlation``.
    ``cross_correlation.npz`` -- ``correlation`` (atoms, atoms), with
    ``residue``, ``chain`` and ``atom`` naming each row.
    """

    name = "cross_correlation"
    description = "Dynamic cross-correlation"
    default_selection = "name CA"
    min_atoms_to_align = 3
    #: A map costs the square of the protein, and what it is fitted on is
    #: the study's question.
    requires_naming = True

    def __init__(self, *, fit_selection: str = "auto", min_separation: int = 4,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fit_selection = str(fit_selection or "auto")
        self.min_separation = int(min_separation)
        if self.min_separation < 1:
            raise StudyError(
                f"`min_separation` is at least 1; it was {self.min_separation}.",
                code="analysis.option.out_of_range")
        self.options.update(fit_selection=self.fit_selection,
                            min_separation=self.min_separation)
        self._matrix: np.ndarray | None = None
        self._rows: dict[str, np.ndarray] | None = None

    # ------------------------------------------------------------------
    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        topology = traj.topology
        atoms = self.select_atoms(traj)
        if len(atoms) < 3:
            raise StudyError(
                f"The selection {self.selection!r} matched {len(atoms)} atom(s); a "
                "map of which move together needs at least three.",
                code="analysis.selection.empty")
        if len(atoms) > MOST_ATOMS:
            raise StudyError(
                f"The selection {self.selection!r} matched {len(atoms):,} atoms, and "
                f"the map is their number squared; {MOST_ATOMS:,} is the most it is "
                "made of. Name fewer, such as `name CA`, or one chain.",
                code="analysis.memory.matrix_too_large")
        if traj.n_frames < FEWEST_FRAMES:
            raise StudyError(
                f"A correlation is an average over the frames; there are "
                f"{traj.n_frames}, and {FEWEST_FRAMES} is the fewest it is taken over.",
                code="analysis.sampling.too_few_frames")
        if self.fit_selection == "auto":
            on = np.arange(len(atoms))
            fitted_on = f"the selection itself ({len(atoms)} atoms)"
        else:
            try:
                named = topology.select(self.fit_selection)
            except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
                raise StudyError(
                    f"`fit_selection` {self.fit_selection!r} is not a selection "
                    f"MDTraj reads: {exc}", code="analysis.selection.invalid") from exc
            place = {int(atom): k for k, atom in enumerate(atoms)}
            on = np.array([place[int(a)] for a in named if int(a) in place], dtype=int)
            if len(on) < 3:
                raise StudyError(
                    f"`fit_selection` {self.fit_selection!r} matched {len(on)} of the "
                    f"selection's atoms; a fit needs at least three, and they are "
                    "taken from the selection.", code="analysis.selection.empty")
            fitted_on = f"{self.fit_selection!r} ({len(on)} of the selection's atoms)"

        xyz = np.asarray(traj.xyz[:, atoms], dtype=np.float64)

        def map_of(frames: np.ndarray) -> np.ndarray:
            fitted = fitted_to_the_mean(frames, on)
            return cross_correlation(fitted - fitted.mean(axis=0))

        matrix = map_of(xyz)
        residue = np.array([topology.atom(int(a)).residue.index for a in atoms])
        chain = np.array([topology.atom(int(a)).residue.chain.index for a in atoms])
        apart = np.abs(residue[:, None] - residue[None, :])
        counted = (chain[:, None] != chain[None, :]) | (apart >= self.min_separation)
        upper = np.triu(counted, k=1)

        half = traj.n_frames // 2
        first, second = map_of(xyz[:half]), map_of(xyz[half:])
        if upper.sum() >= 3 and first[upper].std() > 0 and second[upper].std() > 0:
            agreement = float(np.corrcoef(first[upper], second[upper])[0, 1])
            differ = float(np.abs(first[upper] - second[upper]).mean())
        else:
            agreement, differ = float("nan"), float("nan")

        labels = np.array([f"{topology.atom(int(a)).residue.name}"
                           f"{topology.atom(int(a)).residue.resSeq}" for a in atoms])
        self._matrix = matrix
        self._rows = {"residue": labels, "chain": chain,
                      "atom": np.array([topology.atom(int(a)).name for a in atoms])}

        rows, columns = np.nonzero(upper)
        values = matrix[rows, columns]
        order = np.argsort(values)
        most = np.concatenate([order[::-1][:LISTED], order[:LISTED]]) if len(order) else order
        most = most[np.sort(np.unique(most, return_index=True)[1])]
        pairs = pd.DataFrame({
            "residue_i": labels[rows[most]], "chain_i": chain[rows[most]],
            "residue_j": labels[columns[most]], "chain_j": chain[columns[most]],
            "correlation": np.round(values[most], 4),
        })

        def said(k: int) -> dict[str, Any]:
            return {"between": f"{labels[rows[k]]} (chain {int(chain[rows[k]]) + 1}) and "
                               f"{labels[columns[k]]} (chain {int(chain[columns[k]]) + 1})",
                    "correlation": round(float(values[k]), 3)}

        self.findings["map"] = {
            "atoms": int(len(atoms)), "frames": int(traj.n_frames),
            "of": "the dot product of two atoms' displacements from their mean "
                  "positions, averaged over the frames and divided by their sizes "
                  "(Ichiye and Karplus, Proteins 11, 205 (1991))",
            "fitted_on": fitted_on,
            "to": "the mean structure, found by fitting and averaging until it "
                  "stops moving",
            "matrix": "cross_correlation.npz",
        }
        if len(order):
            self.findings["strongest"] = {
                "together": [said(k) for k in order[::-1][:5]],
                "opposed": [said(k) for k in order[:5]],
                "left_out": f"pairs fewer than {self.min_separation} residues apart "
                            "in one chain, which are correlated by their bonds",
            }
        self.findings["between_the_halves"] = {
            "agreement": None if not np.isfinite(agreement) else agreement,
            "mean_difference": None if not np.isfinite(differ) else differ,
            "meaning": "the correlation between the map of the first half of the "
                       "frames and the map of the second, each fitted to its own "
                       "mean, over the pairs counted; 1 is two halves that show "
                       "the same motions",
        }
        if np.isfinite(agreement) and agreement < AGREEMENT_WANTED:
            self.findings["not_sampled"] = (
                f"The maps of the two halves of the run agree to {agreement:.2f}, "
                f"under {AGREEMENT_WANTED}: the motions the map shows were not "
                "seen alike in both, as a slow motion crossed once is not. The "
                "map describes these frames and not the protein.")
        self.findings["blind_to"] = (
            "Motion in concert at right angles, whose dot product is zero: a "
            "correlation near zero does not say two parts are independent "
            "(Lange and Grubmüller, Proteins 62, 1053 (2006)).")
        return pairs

    # ------------------------------------------------------------------
    def save_data(self, result: pd.DataFrame, path: Path) -> Path:
        written = super().save_data(result, path)
        if self._matrix is not None and self._rows is not None:
            np.savez_compressed(path.with_suffix(".npz"),
                                correlation=self._matrix.astype(np.float32), **self._rows)
        return written

    def default_xlabel(self) -> str | None:
        return "Residue (in the selection's order)"

    def default_ylabel(self) -> str | None:
        return "Residue (in the selection's order)"

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        if self._matrix is None:
            return
        size = len(self._matrix)
        edges = np.arange(size + 1) + 0.5
        image = ax.pcolormesh(edges, edges, self._matrix, cmap="RdBu_r", vmin=-1.0,
                              vmax=1.0, shading="flat", rasterized=True)
        ax.set_aspect("equal")
        ax.set_xlim(0.5, size + 0.5)
        ax.set_ylim(0.5, size + 0.5)
        if self._rows is not None:
            ends = np.flatnonzero(np.diff(self._rows["chain"])) + 1.5
            for end in ends:
                ax.axhline(end, linewidth=0.5, color="0.3")
                ax.axvline(end, linewidth=0.5, color="0.3")
        ax.figure.colorbar(image, ax=ax, label="Correlation", fraction=0.046, pad=0.04)


register_analysis(CrossCorrelation.name, CrossCorrelation)
