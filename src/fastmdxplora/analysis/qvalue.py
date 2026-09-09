"""Q-value: fraction of native contacts retained per frame.

The Q-value (or Q-fraction) is the canonical folding-state metric in
protein dynamics. For each frame, it reports the fraction of the
reference structure's residue-residue contacts that are still present.
Q ≈ 1 means the fold is intact; Q ≈ 0 means it is fully unfolded.

Both the contacts and the measure follow Best, Hummer, & Eaton (PNAS 2013).
The set S is over *pairs of heavy atoms* whose residues lie at least
``min_seq_separation`` apart in sequence (default 4, which excludes
covalent and local contacts that do not probe the tertiary fold) and which
are within a cutoff (default 0.45 nm) in the reference frame.

That S is over atom pairs, not residue pairs, is the whole of the
definition and not a detail. Counting one closest-heavy distance per
residue pair is a different measure: it gives every contacting pair of
residues the same weight whether they touch at one atom or at eight, and
it produces a different |S|, a different denominator, and different
numbers. Both are defensible measures; only one is the published one, and
Q is quoted across papers as though it were a single quantity. Measured
against the reference implementation shipped with MDTraj on a peeling
hairpin, the residue-pair reading of this formula stood 0.263 away from
the published one at its worst frame, on a scale that runs zero to one.
The ``scheme`` option selects between them and defaults to the paper.

Each native contact then contributes through a switching function rather
than a threshold::

    Q(X) = 1/|S| * sum over (i,j) in S of
           1 / (1 + exp[beta * (r_ij(X) - lambda * r0_ij)])

with beta = 50 per nm and lambda = 1.8. What that buys is a contact judged
against the distance it had natively, not against one number shared by every
pair: a contact formed at 0.20 nm and one formed at 0.44 nm are each allowed
to stretch by the same factor before they stop counting, and each stops
counting gradually rather than at a step. A single threshold applied to every
pair reports a fold coming apart while the paper's measure still reads it as
intact.

References
----------
Best, R. B.; Hummer, G.; Eaton, W. A. Native contacts determine protein
folding mechanisms in atomistic simulations. *PNAS* **2013**, 110, 17874.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
from scipy.special import expit

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis


def native_contact_pairs(
    traj: md.Trajectory,
    *,
    ref: int = 0,
    cutoff: float = 0.45,
    min_seq_separation: int = 4,
    atom_indices: Any = None,
) -> tuple[np.ndarray, np.ndarray]:
    """The published S: heavy-atom pairs in contact in the reference frame.

    Returns the pairs in ``traj``'s own atom indexing, together with the
    distance each pair had in the reference.

    This is module-level rather than a method because two callers need the
    same answer: the analysis that reports Q, and the collective variable
    that biases it. A CV built from a separately written contact set would
    bias one quantity and report another, and the disagreement would look
    like a sampling problem rather than a definition problem.

    Built from a neighbour search rather than from every pair of heavy
    atoms. The reference implementation enumerates all combinations and
    then discards those beyond the cutoff, which on a real protein means
    holding millions of pairs to keep some thousands; only pairs already
    within the cutoff can be native contacts, so the search asks for those
    directly and the enumeration never happens.
    """
    top = traj.topology
    # A separation no pair of residues in this chain can satisfy is a
    # configuration error, not an unfolded reference, and the two have to
    # be told apart: returning nothing for both would answer "there is no
    # fold here" to a question that was never asked properly.
    if traj.n_residues <= min_seq_separation:
        raise ValueError(
            f"No residue pairs satisfy min_seq_separation="
            f"{min_seq_separation} in a trajectory with "
            f"{traj.n_residues} residues."
        )

    eligible = (set(int(i) for i in atom_indices)
                if atom_indices is not None else None)
    heavy = np.array(
        [a.index for a in top.atoms
         if a.element.symbol != "H"
         and (eligible is None or a.index in eligible)],
        dtype=int,
    )
    if len(heavy) < 2:
        return np.empty((0, 2), dtype=int), np.empty(0)

    residue_of = np.array(
        [top.atom(int(i)).residue.index for i in heavy], dtype=int)
    frame = traj[ref]

    pairs: list[tuple[int, int]] = []
    # compute_neighbors works one query set at a time; asking per atom keeps
    # the memory flat and is fast enough for a single frame.
    for pos, atom in enumerate(heavy):
        candidates = heavy[pos + 1:]
        if len(candidates) == 0:
            break
        far_enough = (
            np.abs(residue_of[pos + 1:] - residue_of[pos])
            >= min_seq_separation
        )
        candidates = candidates[far_enough]
        if len(candidates) == 0:
            continue
        probe = np.column_stack([np.full(len(candidates), atom), candidates])
        within = md.compute_distances(frame, probe)[0] < cutoff
        pairs.extend((int(atom), int(other)) for other in candidates[within])

    if not pairs:
        return np.empty((0, 2), dtype=int), np.empty(0)
    pair_idx = np.array(pairs, dtype=int)
    return pair_idx, md.compute_distances(frame, pair_idx)[0]


class QValue(Analysis):
    """Fraction of native contacts retained per frame.

    Parameters
    ----------
    ref : int, default 0
        Reference frame defining the "native" state. The contacts present
        in this frame become the denominator of the Q calculation.
    scheme : {"heavy-atom-pairs", "residue-closest-heavy"}, default
        "heavy-atom-pairs"
        Which set S to build. ``heavy-atom-pairs`` is the published
        definition: every pair of heavy atoms within ``cutoff`` in the
        reference, subject to the sequence separation.
        ``residue-closest-heavy`` uses one closest-heavy distance per
        residue pair instead, which is a coarser measure that is not
        comparable with published Q values.
    cutoff : float, default 0.45
        Heavy-atom-contact cutoff in nm, used to decide which pairs are
        natively in contact in the reference frame. It does not decide
        whether a contact is present later: that is what the switching
        function is for.
    beta : float, default 50.0
        Steepness of the switching function, in 1/nm. The paper's value.
        Larger values approach a hard threshold.
    lambda_factor : float, default 1.8
        How far a contact may stretch relative to its own native distance
        before it stops counting. The paper's value.
    min_seq_separation : int, default 4
        Minimum |i - j| in sequence for a pair to be considered. The
        default 4 excludes local contacts (i±1, i±2, i±3) that don't
        probe the global fold.
    selection : str, default "protein"
        Which atoms may form contacts. Hydrogens are excluded from S
        whatever is selected, because the published definition is over
        heavy atoms, so the useful choices narrow it further:

        - ``"protein"`` (the default) gives the published measure, every
          heavy atom of the chain.
        - ``"backbone"`` reports on the fold's topology alone and is
          insensitive to side-chain repacking, which is what makes it
          the choice when the question is whether the chain has changed
          course rather than whether a core has loosened.
        - ``"name CA"`` gives the coarse-grained measure familiar from
          Go-model work. It is a different quantity from the other two
          and is not comparable with a Q quoted from an all-atom study.

        The choice moves the number as much as the ``scheme`` does, so it
        belongs in the record with it, and it is written to
        ``options.json`` for that reason.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``qvalue.dat`` — single-column file of Q per frame (range [0, 1]).
    ``qvalue.png`` — Time series of Q vs. frame/time.
    """

    #: The shortest chain this can say anything about.
    #:
    #: Q measures how much of a fold is intact, from contacts between
    #: residues far enough apart in sequence to probe tertiary structure. A
    #: chain shorter than `min_seq_separation + 1` residues has no such pair,
    #: and no fold for them to describe. Raising there recorded a failed
    #: analysis for a peptide that simply has no tertiary structure -- the
    #: same category error `requires_water` exists to avoid, where "there is
    #: no water here" was a failed phase rather than a question that did not
    #: apply.
    requires_tertiary_structure = True

    name = "qvalue"
    time_series = True
    reweightable = (None, "Fraction of native contacts")
    description = "Native contact fraction (Q)"
    #: Q is a statement about a fold, and solvent has none. Left at the
    #: whole trajectory, S came out of a solvated system as 98% water-water
    #: pairs on a small test case, and the number reported would have been
    #: how much of the water's starting arrangement survived. A full run
    #: passes a scope selection and never saw this; a direct call from a
    #: notebook is the ordinary way to meet it.
    default_selection = "protein"

    #: The two readings of S, and what each one counts.
    SCHEMES = ("heavy-atom-pairs", "residue-closest-heavy")

    def __init__(
        self,
        *,
        ref: int = 0,
        cutoff: float = 0.45,
        beta: float = 50.0,
        lambda_factor: float = 1.8,
        min_seq_separation: int = 4,
        scheme: str = "heavy-atom-pairs",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if scheme not in self.SCHEMES:
            raise ValueError(
                f"scheme must be one of {self.SCHEMES}, not {scheme!r}."
            )
        self.ref: int = int(ref)
        self.cutoff: float = float(cutoff)
        self.beta: float = float(beta)
        self.lambda_factor: float = float(lambda_factor)
        self.min_seq_separation: int = int(min_seq_separation)
        self.scheme: str = str(scheme)
        self.options.update(
            ref=self.ref,
            cutoff=self.cutoff,
            beta=self.beta,
            lambda_factor=self.lambda_factor,
            min_seq_separation=self.min_seq_separation,
            scheme=self.scheme,
        )

    def _native_atom_pairs(
        self, traj: md.Trajectory, ref: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """This analysis's S, from the shared definition."""
        return native_contact_pairs(
            traj,
            ref=ref,
            cutoff=self.cutoff,
            min_seq_separation=self.min_seq_separation,
        )

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        """Compute Q per frame.

        Returns
        -------
        np.ndarray of shape (n_frames,)
            Q values in [0, 1]. NaN if the reference has zero contacts
            (the calculation is undefined — e.g. an unfolded reference).
        """
        # Restrict to the selected atoms (e.g. protein/solute) BEFORE
        # enumerating residue pairs. On a solvated system the full trajectory
        # has thousands of water residues; building all-vs-all pairs over them
        # is both meaningless and catastrophically slow (n_res^2). Slicing
        # first drops n_res from ~thousands to the protein's ~hundreds.
        atom_idx = self.select_atoms(traj)
        if len(atom_idx) < traj.n_atoms:
            traj = traj.atom_slice(atom_idx)

        # Resolve negative reference indices before anything reads the frame.
        n_frames = traj.n_frames
        ref = self.ref if self.ref >= 0 else n_frames + self.ref
        if not (0 <= ref < n_frames):
            raise ValueError(
                f"Reference frame {self.ref} is out of range for trajectory "
                f"with {n_frames} frames."
            )

        if self.scheme == "heavy-atom-pairs":
            pair_idx, r0 = self._native_atom_pairs(traj, ref)
            if len(pair_idx) == 0:
                return np.full(n_frames, np.nan)
            native_distances = md.compute_distances(traj, pair_idx)
            n_native = len(pair_idx)
        else:
            n_res = traj.n_residues
            ii, jj = np.triu_indices(n_res, k=self.min_seq_separation)
            if len(ii) == 0:
                raise ValueError(
                    f"No residue pairs satisfy "
                    f"min_seq_separation={self.min_seq_separation} in a "
                    f"trajectory with {n_res} residues."
                )
            distances, _ = md.compute_contacts(
                traj, contacts=np.column_stack([ii, jj]),
                scheme="closest-heavy")
            native_mask = distances[ref] < self.cutoff
            n_native = int(native_mask.sum())
            if n_native == 0:
                return np.full(n_frames, np.nan)
            native_distances = distances[:, native_mask]
            r0 = distances[ref][native_mask]

        # Each native contact is measured against the distance it had in the
        # reference, through the paper's switching function. Counting instead
        # how many are still inside a single cutoff judges a contact formed at
        # 0.20 nm by the same standard as one formed at 0.44 nm, and reports a
        # fold coming apart well before it has.
        #
        # expit(-x) is 1/(1+exp(x)) evaluated without overflowing. Written the
        # direct way, a contact stretched much past its native distance sends
        # the exponent over what a float can hold: the answer is still zero,
        # but the warning arrives exactly when someone is watching something
        # come apart, which is when they are least in the mood for it.
        q = np.mean(
            expit(-self.beta * (native_distances - self.lambda_factor * r0)),
            axis=1,
        )
        self._n_native = n_native
        return q.astype(np.float64)

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x, _ = self.frame_axis_for_plot(self._traj_for_plot, len(result))
        ax.plot(x, result, linewidth=1.4)
        ax.axhline(1.0, color=colour("GUIDE"), linestyle=":", linewidth=0.8)
        ax.set_ylim(-0.02, 1.05)
        # Annotate the number of native contacts in the legend
        n = getattr(self, "_n_native", None)
        if n is not None:
            ax.legend(
                [f"Q (n_native = {n})"],
                loc="best",
            )

    _traj_for_plot: md.Trajectory | None = None

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def frame_axis_for_plot(
        self, traj: md.Trajectory | None, n_points: int
    ) -> tuple[np.ndarray, str]:
        if traj is None:
            return np.arange(n_points), "Frame"
        return self.frame_axis(traj)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        _, label = self.frame_axis(self._traj_for_plot)
        return label

    def default_ylabel(self) -> str | None:
        return "Q (fraction native contacts)"


register_analysis(QValue.name, QValue)
