"""How a signal could cross the protein: the residue interaction network.

Each residue is a node, and two residues are joined where they are in
contact (any heavy atoms within ``contact_cutoff_nm``) in at least
``contact_share`` of the frames, neighbours along the chain left out. Each
join is weighted by how much the two residues' alpha carbons move together,
``w = -log |C_ij|`` from the dynamic cross-correlation, so a path through
residues that move in concert is short (Sethi, Eargle, Black and
Luthey-Schulten, PNAS 106, 6620 (2009), the method of VMD's NetworkView).

From the network:

- each residue's **betweenness**, the share of the shortest paths between
  every pair of residues that pass through it. Residues high in it are the
  network's bottlenecks, through which communication between its parts
  would run;
- the **shortest path** between two named sites (``source`` and ``sink``),
  and its length, where both are named.

What it is: a picture of which contacts persist and which residues move
together, read as paths. What it is not: a demonstration that a signal
travels along them. Two residues move together because of everything
between them; the network says where coupling is strongest, not that it is
causal. The correlation is the cross-correlation's, so it carries that
measure's blindness to motion at right angles, and its dependence on the
superposition (see :mod:`fastmdxplora.analysis.cross_correlation`).

Contacts are looked for within each frame as stored, the frames made whole
and centred on the solute; a contact across the periodic boundary is not.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.cross_correlation import (
    FEWEST_FRAMES,
    MOST_ATOMS,
    cross_correlation,
    fitted_to_the_mean,
)
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError

#: The weakest correlation a join is given: below it -log|C| would be
#: infinite, and two residues in contact that do not move together are
#: still joined, at a great length.
_WEAKEST = 1e-3


def contact_shares(traj: md.Trajectory, residues: list, cutoff_nm: float) -> np.ndarray:
    """The share of frames each pair of ``residues`` (MDTraj residues) is in
    contact, any heavy atoms within ``cutoff_nm``."""
    from scipy.spatial import cKDTree

    heavy, owner = [], []
    for k, residue in enumerate(residues):
        for atom in residue.atoms:
            if atom.element is not None and atom.element.symbol != "H":
                heavy.append(atom.index)
                owner.append(k)
    heavy, owner = np.array(heavy, dtype=int), np.array(owner, dtype=int)
    counts = np.zeros((len(residues), len(residues)))
    for frame in range(traj.n_frames):
        pairs = cKDTree(traj.xyz[frame, heavy]).query_pairs(cutoff_nm, output_type="ndarray")
        if not len(pairs):
            continue
        a, b = owner[pairs[:, 0]], owner[pairs[:, 1]]
        apart = a != b
        touching = np.zeros_like(counts, dtype=bool)
        touching[a[apart], b[apart]] = True
        touching |= touching.T
        counts += touching
    return counts / traj.n_frames


def betweenness(length: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each node's share of the shortest paths between the other pairs, and
    the predecessors of the shortest paths, for the weighted graph whose
    joins have ``length`` (0 where there is none)."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra

    nodes = len(length)
    _, before = dijkstra(csr_matrix(length), directed=False, return_predecessors=True)
    through = np.zeros(nodes)
    pairs = 0
    for source in range(nodes):
        for sink in range(source + 1, nodes):
            step = before[source, sink]
            if step < 0:
                continue
            pairs += 1
            while step != source:
                through[step] += 1
                step = before[source, step]
    return (through / pairs if pairs else through), before


class ResidueNetwork(Analysis):
    """The residue interaction network: its bottlenecks, and the shortest
    path between two sites.

    Options
    -------
    contact_cutoff_nm : float, default 0.45
        Two residues are in contact where any of their heavy atoms are this
        close.
    contact_share : float, default 0.75
        The share of frames two residues have to be in contact to be joined.
    source, sink : str, default ""
        Two sites, as selections, between which the shortest path is found:
        both, or neither.

    Output
    ------
    ``residue_network.dat`` -- comma-separated, one row per residue:
    ``residue``, ``chain``, ``joins`` and ``betweenness``.
    ``residue_network.npz`` -- ``length`` (residues, residues), each join's
    -log|C| (0 where there is none), with ``residue`` and ``chain``.
    """

    name = "residue_network"
    description = "Residue interaction network"
    default_selection = "protein"
    #: Which sites communicate is the study's question, and the network
    #: costs a correlation map and a contact search over every frame.
    requires_naming = True

    def __init__(self, *, contact_cutoff_nm: float = 0.45, contact_share: float = 0.75,
                 source: str = "", sink: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.contact_cutoff_nm = float(contact_cutoff_nm)
        self.contact_share = float(contact_share)
        self.source, self.sink = str(source or ""), str(sink or "")
        if not 0.2 <= self.contact_cutoff_nm <= 1.0:
            raise StudyError(
                f"`contact_cutoff_nm` is from 0.2 to 1.0 nm; it was "
                f"{self.contact_cutoff_nm:g}.", code="analysis.option.out_of_range")
        if not 0.0 < self.contact_share <= 1.0:
            raise StudyError(
                f"`contact_share` is a share of the frames, above 0 and at most 1; "
                f"it was {self.contact_share:g}.", code="analysis.option.out_of_range")
        if bool(self.source) != bool(self.sink):
            raise StudyError(
                "`source` and `sink` are the two ends of a path: name both, or "
                "neither.", code="analysis.option.missing_companion")
        self.options.update(contact_cutoff_nm=self.contact_cutoff_nm,
                            contact_share=self.contact_share,
                            source=self.source, sink=self.sink)
        self._network: dict[str, np.ndarray] | None = None

    # ------------------------------------------------------------------
    def _site(self, traj: md.Trajectory, said: str, residues: list) -> list[int]:
        try:
            atoms = traj.topology.select(said)
        except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
            raise StudyError(f"{said!r} is not a selection MDTraj reads: {exc}",
                             code="analysis.selection.invalid") from exc
        wanted = {traj.topology.atom(int(a)).residue.index for a in atoms}
        found = [k for k, residue in enumerate(residues) if residue.index in wanted]
        if not found:
            raise StudyError(
                f"{said!r} holds no residue of the network (residues of "
                f"{self.selection!r} with an alpha carbon).",
                code="analysis.selection.empty")
        return found

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra

        topology = traj.topology
        chosen = {int(a) for a in self.select_atoms(traj)}
        residues = [r for r in topology.residues
                    if any(a.index in chosen for a in r.atoms)
                    and any(a.name == "CA" for a in r.atoms)]
        if len(residues) < 3:
            raise StudyError(
                f"{self.selection!r} holds {len(residues)} residue(s) with an alpha "
                "carbon; a network needs three at least.",
                code="analysis.selection.empty")
        if len(residues) > MOST_ATOMS:
            raise StudyError(
                f"{len(residues):,} residues: the network is their number squared, "
                f"and {MOST_ATOMS:,} is the most it is made of.",
                code="analysis.memory.matrix_too_large")
        if traj.n_frames < FEWEST_FRAMES:
            raise StudyError(
                f"The correlations are averages over the frames; there are "
                f"{traj.n_frames}, and {FEWEST_FRAMES} is the fewest they are taken over.",
                code="analysis.sampling.too_few_frames")
        ends = None
        if self.source:
            ends = (self._site(traj, self.source, residues),
                    self._site(traj, self.sink, residues))

        alpha = np.array([next(a.index for a in r.atoms if a.name == "CA") for r in residues])
        fitted = fitted_to_the_mean(np.asarray(traj.xyz[:, alpha], dtype=np.float64),
                                    np.arange(len(alpha)))
        correlation = cross_correlation(fitted - fitted.mean(axis=0))
        shares = contact_shares(traj, residues, self.contact_cutoff_nm)

        chain = np.array([r.chain.index for r in residues])
        number = np.array([r.index for r in residues])
        neighbours = (chain[:, None] == chain[None, :]) & (
            np.abs(number[:, None] - number[None, :]) <= 1)
        joined = (shares >= self.contact_share) & ~neighbours
        np.fill_diagonal(joined, False)
        length = np.where(joined, -np.log(np.clip(np.abs(correlation), _WEAKEST, 1.0)), 0.0)
        # A join of length zero would read as no join at all.
        length[joined & (length == 0)] = 1e-9

        through, _ = betweenness(length)
        labels = np.array([f"{r.name}{r.resSeq}" for r in residues])
        degree = joined.sum(axis=1)
        table = pd.DataFrame({"residue": labels, "chain": chain + 1, "joins": degree,
                              "betweenness": np.round(through, 6)})
        self._network = {"length": length.astype(np.float32), "residue": labels,
                         "chain": chain}

        from scipy.sparse.csgraph import connected_components

        parts, _ = connected_components(csr_matrix(joined), directed=False)
        ranked = np.argsort(-through)[:10]
        self.findings["network"] = {
            "residues": len(residues), "joins": int(joined.sum() // 2),
            "parts": int(parts),
            "joined_where": f"heavy atoms within {self.contact_cutoff_nm:g} nm in at "
                            f"least {self.contact_share:.0%} of the frames, neighbours "
                            "along the chain left out",
            "length": "-log|C| of the alpha carbons' cross-correlation, each frame "
                      "fitted to the mean structure (Sethi et al., PNAS 106, 6620 (2009))",
        }
        self.findings["bottlenecks"] = [
            {"residue": str(labels[k]), "chain": int(chain[k]) + 1,
             "betweenness": round(float(through[k]), 4)} for k in ranked if through[k] > 0]
        if parts > 1:
            self.findings["apart"] = (
                f"The network falls into {parts} parts with no join between them: no "
                "path runs between residues in different parts.")
        if ends is not None:
            distance, before = dijkstra(csr_matrix(length), directed=False,
                                        indices=ends[0], return_predecessors=True)
            row, sink = np.unravel_index(
                np.argmin(distance[:, ends[1]]), (len(ends[0]), len(ends[1])))
            far = float(distance[row, ends[1][sink]])
            if not np.isfinite(far):
                self.findings["path"] = {
                    "source": self.source, "sink": self.sink,
                    "found": False,
                    "why": "the two sites are in parts of the network with no join "
                           "between them"}
            else:
                step, path = ends[1][sink], []
                while step >= 0:
                    path.append(int(step))
                    step = before[row, step]
                path.reverse()
                self.findings["path"] = {
                    "source": self.source, "sink": self.sink, "found": True,
                    "residues": [f"{labels[k]} (chain {int(chain[k]) + 1})" for k in path],
                    "length": far,
                    "meaning": "the sum of -log|C| along it: the shorter, the more "
                               "strongly its residues move together"}
        self.findings["not_causal"] = (
            "Paths run where residues in lasting contact move together; that a signal "
            "travels along them is not shown by it.")
        return table

    # ------------------------------------------------------------------
    def save_data(self, result: pd.DataFrame, path):
        written = super().save_data(result, path)
        if self._network is not None:
            np.savez_compressed(path.with_suffix(".npz"), **self._network)
        return written

    def default_xlabel(self) -> str | None:
        return "Residue (in the selection's order)"

    def default_ylabel(self) -> str | None:
        return "Betweenness"

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        ax.bar(np.arange(1, len(result) + 1), result["betweenness"], width=1.0)
        ax.set_xlim(0.5, len(result) + 0.5)


register_analysis(ResidueNetwork.name, ResidueNetwork)
