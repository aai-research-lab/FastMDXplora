"""Radius of Gyration (Rg).

Per-frame radius of gyration, a measure of overall molecular size and
compactness. For a single-chain protein, Rg typically tracks folding
state — unfolded conformations have higher Rg, compact native states
have lower Rg.

The formula::

    Rg(t) = sqrt( sum_i m_i * |r_i(t) - r_cm(t)|^2  /  sum_i m_i )

The radius of gyration is mass-weighted, which is what GROMACS's
``gyrate``, cpptraj's ``radgyr`` and a published Rg all report::

    Rg = sqrt( sum_i m_i |r_i - R|^2 / sum_i m_i ),   R = sum_i m_i r_i / sum_i m_i

It is computed here rather than by :func:`mdtraj.compute_rg`, which weights
every atom equally unless told otherwise -- and which, when given masses,
still measures from the geometric centre rather than the centre of mass.
Weighting equally counts each hydrogen for as much as each carbon, which on a
protein is a few per cent away from the mass-weighted value and enough to
disagree with a number someone is comparing against. Pass
``mass_weighted=False`` for the unweighted quantity.

A virtual site (element ``VS``, such as a TIP4P water's charge site) has no
mass and is given no weight. MDTraj gives that element to every atom whose
element it cannot read, so the atoms given no weight are named in a
finding. Where no atom has a mass (a bead model built without elements), or
an atom carries no element at all, the radius is computed with every atom
weighted equally, ``mass_weighted`` is recorded as false, and a finding says
why: one such atom had turned the whole radius unweighted while the option
still read true.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis


def _atom_weights(topology) -> "tuple[np.ndarray | None, list[str], list[str]]":
    """Atomic masses with a virtual site weighing nothing, or None; the names
    of the atoms whose mass is not known; the names of the virtual sites."""
    masses = []
    unknown: list[str] = []
    virtual: list[str] = []
    for atom in topology.atoms:
        element = getattr(atom, "element", None)
        mass = getattr(element, "mass", None)
        if getattr(element, "symbol", None) == "VS":
            masses.append(0.0)
            virtual.append(atom.name)
        elif not mass:
            unknown.append(atom.name)
        else:
            masses.append(float(mass))
    if unknown or not any(masses):
        return None, unknown or virtual, virtual
    return np.asarray(masses, dtype=np.float64), [], virtual


class Rg(Analysis):
    """Per-frame radius of gyration.

    Parameters
    ----------
    mass_weighted : bool, default True
        Weight each atom by its mass and measure from the centre of mass, as
        the conventional definition does. False weights every atom equally.
    by_chain : bool, default False
        If True, compute Rg separately for each chain in the topology
        (in addition to the whole-system Rg). Useful for multi-chain
        complexes where you want to track the compactness of each subunit.
    selection : str, optional
        MDTraj atom selection. Defaults to ``None`` which means "use all
        atoms" — appropriate for Rg of the entire system. For a protein
        Rg in a solvated system, pass ``selection="protein"``.
    **kwargs
        Standard base-class options.

    Output
    ------
    Single-column ``rg.dat`` (Rg in nm per frame). With ``by_chain=True``
    and several chains, a comma-separated table with a header, one row per
    frame: ``total``, then ``chain A``, ``chain B``, ... named by chain ID
    (by place among the polymer chains where the topology carries none), as
    ``end_to_end`` writes its chains.
    """

    name = "rg"
    time_series = True
    #: The column is read only from the table ``by_chain`` writes; the
    #: one-column default is the series itself.
    reweightable = ("total", "Radius of gyration (nm)")
    description = "Radius of gyration"
    default_selection = None  # use all atoms by default

    def __init__(
        self,
        *,
        by_chain: bool = False,
        mass_weighted: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.by_chain: bool = bool(by_chain)
        self.mass_weighted: bool = bool(mass_weighted)
        self.options.update(
            by_chain=self.by_chain, mass_weighted=self.mass_weighted
        )

    def _weights(self, topology) -> np.ndarray:
        """Each atom's weight, decided once for the whole selection and
        recorded: its mass, a virtual site's zero, or one for every atom
        where masses are not known."""
        self.findings.pop("weights", None)
        self.options["mass_weighted"] = self.mass_weighted
        n = topology.n_atoms
        if not self.mass_weighted:
            return np.ones(n, dtype=np.float64)
        weights, unknown, virtual = _atom_weights(topology)
        if weights is None:
            self.options["mass_weighted"] = False
            named = sorted(set(unknown))
            self.findings["weights"] = (
                f"The masses of {len(unknown)} atom(s) are not known (no element "
                f"for {', '.join(named[:8])}{' and others' if len(named) > 8 else ''}), "
                "so every atom is weighted equally and the radius of gyration is "
                "the geometric one, not the mass-weighted one asked for. A "
                "topology read from a PDB usually carries elements; one built by "
                "hand often does not."
            )
            return np.ones(n, dtype=np.float64)
        if virtual:
            named = sorted(set(virtual))
            self.findings["weights"] = (
                f"{len(virtual)} atom(s) have no mass and are given no "
                f"weight: {', '.join(named[:8])}"
                f"{' and others' if len(named) > 8 else ''}. MDTraj reads "
                "a virtual site this way, such as a TIP4P water's charge "
                "site, and also any atom whose element it does not know."
            )
        return weights

    @staticmethod
    def _rg(traj: md.Trajectory, weights: np.ndarray) -> np.ndarray:
        """Radius of gyration per frame, by the definition above. A part of
        the selection whose atoms all weigh nothing is weighted equally."""
        xyz = traj.xyz.astype(np.float64)
        if weights.sum() <= 0.0:
            weights = np.ones_like(weights)
        weights = weights / weights.sum()

        centre = (xyz * weights[None, :, None]).sum(axis=1)
        squared = ((xyz - centre[:, None, :]) ** 2).sum(axis=2)
        return np.sqrt((squared * weights[None, :]).sum(axis=1))

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        """Compute Rg per frame.

        Returns
        -------
        np.ndarray or pandas.DataFrame
            If ``by_chain=False``: shape (n_frames,), Rg in nm. If
            ``by_chain=True`` with several chains: a table with columns
            ``total`` and ``chain <ID>`` for each chain, one row per frame;
            with one chain, shape (n_frames, 1), the total.
        """
        atom_idx = self.select_atoms(traj)

        # Sub-trajectory on the selected atoms, so compute_rg uses the right mass
        if len(atom_idx) < traj.n_atoms:
            sub = traj.atom_slice(atom_idx)
        else:
            sub = traj

        weights = self._weights(sub.topology)
        rg_total = self._rg(sub, weights)

        if not self.by_chain:
            return rg_total

        # Per-chain breakdown on the selected sub-topology
        n_chains = sub.topology.n_chains
        if n_chains <= 1:
            # No useful breakdown; still return the column for consistency.
            return rg_total.reshape(-1, 1)

        import pandas as pd

        from fastmdxplora.analysis.residues import chain_name

        table = {"total": rg_total}
        for chain in sub.topology.chains:
            chain_atoms = [a.index for a in chain.atoms]
            if not chain_atoms:
                continue
            residues = list(chain.residues)
            label = f"chain {chain_name(residues[0]) if residues else chain.index}"
            if label in table:
                label = f"{label} ({chain.index})"
            table[label] = self._rg(sub.atom_slice(chain_atoms), weights[chain_atoms])

        self._n_chains = len(table) - 1
        return pd.DataFrame(table)

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x, _ = self.frame_axis_for_plot(self._traj_for_plot, result)

        if hasattr(result, "columns"):
            for label in result.columns:
                ax.plot(x, result[label].to_numpy(),
                        linewidth=1.6 if label == "total" else 1.0, label=label)
            ax.legend(loc="best")
        elif result.ndim == 1:
            ax.plot(x, result, linewidth=1.4, label="total")
        else:
            ax.plot(x, result[:, 0], linewidth=1.6, label="total")

    # Same trajectory caching pattern as RMSD so the plot can read it.
    _traj_for_plot: md.Trajectory | None = None

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def frame_axis_for_plot(
        self, traj: md.Trajectory | None, result: np.ndarray
    ) -> tuple[np.ndarray, str]:
        if traj is None:
            n = result.shape[0] if result.ndim > 0 else len(result)
            return np.arange(n), "Frame"
        return self.frame_axis(traj)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        _, label = self.frame_axis(self._traj_for_plot)
        return label

    def default_ylabel(self) -> str | None:
        return "Rg (nm)"


register_analysis(Rg.name, Rg)
