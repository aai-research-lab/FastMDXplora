"""Root-Mean-Square Fluctuation (RMSF).

Per-residue (default) or per-atom RMSF over the trajectory. The
trajectory is first superposed onto a reference (frame 0 or a user-chosen
reference) using the selected atom subset to remove rigid-body motion;
the per-atom RMSF is then the root-mean-square deviation of each atom's
position about its mean::

    MSF_i = < |r_i(t) - <r_i>|^2 >_t,   RMSF_i = sqrt(MSF_i)

The per-residue RMSF is the square root of the residue's mass-weighted
mean MSF, which is what GROMACS ``gmx rmsf -res`` reports::

    RMSF_res = sqrt( sum_{i in res} m_i MSF_i / sum_{i in res} m_i )

This is the standard "flexibility profile" plot used in nearly every MD
publication: peaks indicate flexible loops and termini, troughs rigid
secondary structure.

**Only the equilibrated frames.** A fluctuation about the mean position is
a property of the equilibrium ensemble, and a relaxation away from the
starting structure is not one: averaged in, a loop that moves 0.4 nm in the
first fifth of a run reads 0.082 nm against the 0.052 it fluctuates by
afterwards. So the first frames are left out, as many as the package's own
equilibration detection (:func:`fastmdxplora.statistics.summarise`, the one
every per-frame mean uses) finds on the RMSD of the fitted atoms from the
reference frame. ``equilibrated_from`` gives the start instead, and ``0``
keeps every frame. What was left out is recorded as ``findings["discard"]``
and said on the figure.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.protein_names import ALPHA_CARBONS
from fastmdxplora.analysis.base import Analysis, superposed
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError


def _atom_labels(atoms) -> "np.ndarray":
    """Serial numbers for the atom axis, or positions where there are none.

    ``Atom.serial`` is filled in by the file the topology came from. A
    trajectory built in memory -- which the Python API allows and the tests
    use -- has none, and ``None`` became NaN in the column, then a very large
    negative integer once the plot cast it. The saved data carried the NaN.
    """
    return np.array([
        atom.serial if atom.serial is not None else atom.index + 1
        for atom in atoms
    ], dtype=np.int64)


class RMSF(Analysis):
    """Per-residue root-mean-square fluctuation.

    Parameters
    ----------
    ref : int, default 0
        Reference frame for the alignment (superposition) step, and the
        frame the RMSD that locates the equilibration is taken from.
        Fluctuations are about each atom's mean position over the frames
        kept (see ``equilibrated_from``).
    per_residue : bool, default True
        If True, collapse the per-atom RMSF to one value per residue, the
        square root of the mass-weighted mean of its atoms' squared
        fluctuations, sqrt(sum m_i MSF_i / sum m_i), as GROMACS
        ``gmx rmsf -res`` gives it. With the alpha-carbon default each
        residue has one atom and this is that atom's RMSF. If False, return
        the per-atom array (one value per selected atom).
    equilibrated_from : int or None, default None
        The first analysed frame the fluctuations are computed over. ``None``
        finds it from the RMSD of the selected atoms from ``ref``, by the
        equilibration detection the per-frame means use; ``0`` uses every
        frame; a positive number is that frame. At least two frames must
        remain.
    selection : str, optional
        MDTraj atom selection. Defaults to ``"protein and name CA"`` (alpha carbons)
        for protein analysis.
    **kwargs
        Standard base-class options.

    Output
    ------
    A two-column ``rmsf.dat`` (residue_index, rmsf_nm) when ``per_residue``
    is True, or (atom_index, rmsf_nm) when False.

    Examples
    --------
    Standard per-residue plot for proteins::

        rmsf = RMSF()
        rmsf.run(trajectory)

    Per-atom on backbone heavy atoms::

        rmsf = RMSF(per_residue=False, selection="backbone and not element H")
    """

    name = "rmsf"
    description = "Root-mean-square fluctuation"
    default_selection = ALPHA_CARBONS
    #: A superposition needs three atoms to be defined.
    min_atoms_to_align = 3

    def __init__(
        self,
        *,
        ref: int = 0,
        per_residue: bool = True,
        equilibrated_from: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.ref: int = int(ref)
        self.per_residue: bool = bool(per_residue)
        self.equilibrated_from: int | None = (
            None if equilibrated_from is None else int(equilibrated_from))
        self.options.update(ref=self.ref, per_residue=self.per_residue,
                            equilibrated_from=self.equilibrated_from)
        if self.per_residue:
            self.options["per_residue_average"] = (
                "sqrt(sum_i m_i MSF_i / sum_i m_i) over the residue's selected "
                "atoms, as gmx rmsf -res")

    def _start(self, traj: md.Trajectory, ref: int, atom_idx: np.ndarray) -> int:
        """The first frame the fluctuations are taken over, recorded.

        Detected on the RMSD of the fitted atoms from the reference frame,
        by :func:`fastmdxplora.statistics.summarise`, the detection behind
        every per-frame mean, unless ``equilibrated_from`` gives it.
        """
        from fastmdxplora.analysis.base import _frame_interval_ns

        n = traj.n_frames
        if self.equilibrated_from is not None:
            start = self.equilibrated_from
            if not 0 <= start <= n - 2:
                raise StudyError(
                    f"equilibrated_from is {start}, and this trajectory has {n} "
                    f"frames: it must lie from 0 to {n - 2}, so that at least "
                    "two frames remain to fluctuate.",
                    code="analysis.option.out_of_range")
            how = "given by equilibrated_from"
        else:
            from fastmdxplora.statistics import summarise

            rmsd = md.rmsd(traj, traj, frame=ref, atom_indices=atom_idx)
            equilibrated, _ = summarise(np.asarray(rmsd, dtype=float))
            start = int(equilibrated.discard) if equilibrated is not None else 0
            how = (f"detected on the RMSD of the selected atoms from frame {ref}, "
                   "as for the per-frame means")
        interval = _frame_interval_ns(traj)
        self.findings["discard"] = {
            "frames": int(start),
            "of_frames": int(n),
            "ns": float(start * interval) if interval is not None else None,
            "how": how,
        }
        return start

    def _weights(self, atoms: list) -> np.ndarray:
        """Each atom's mass for the per-residue average, in amu.

        A virtual site (element ``VS``, mass zero) has no mass and so no
        weight. Where any other atom's mass is unknown, every atom is
        weighted equally, and the finding says so.
        """
        masses = []
        unknown: set[str] = set()
        for atom in atoms:
            element = getattr(atom, "element", None)
            mass = getattr(element, "mass", None)
            if mass is None or (not mass and getattr(element, "symbol", "") != "VS"):
                unknown.add(atom.name)
                masses.append(1.0)
            else:
                masses.append(float(mass))
        if unknown:
            self.findings["per_residue_weights"] = (
                f"No element, and so no mass, for {len(unknown)} atom name(s) "
                f"({', '.join(sorted(unknown)[:8])}), so the atoms of each "
                "residue are weighted equally rather than by mass."
            )
            return np.ones(len(atoms), dtype=np.float64)
        return np.asarray(masses, dtype=np.float64)

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        """Compute the RMSF.

        Returns
        -------
        np.ndarray, shape (N, 2)
            Two columns: index (residue or atom number) and RMSF in nm.
        """
        atom_idx = self.select_atoms(traj)

        # Align the trajectory onto the reference using the selected atoms.
        # This removes rigid-body translation and rotation so the residual
        # variance is purely conformational fluctuation.
        n = traj.n_frames
        ref = self.ref if self.ref >= 0 else n + self.ref
        if not (0 <= ref < n):
            raise StudyError(
                f"Reference frame {self.ref} is out of range for trajectory "
                f"with {n} frames."
            , code="analysis.option.out_of_range")

        if n < 2:
            raise StudyError(
                f"{n} frame: a fluctuation is a spread over frames, and one "
                "frame has none, so every RMSF would read zero. At least two "
                "frames are needed.",
                code="analysis.sampling.too_few_frames", found=n, needed=2)

        start = self._start(traj, ref, atom_idx)
        aligned = superposed(traj[start:], frame=ref, atom_indices=atom_idx,
                             reference=traj)

        # Per-atom RMSF on the selected atoms only:
        # rmsf[i] = sqrt(mean over frames of ||r_i(t) - <r_i>||^2)
        xyz = aligned.xyz[:, atom_idx, :]
        mean_xyz = xyz.mean(axis=0)
        disp = xyz - mean_xyz
        per_atom = np.sqrt(np.mean(np.sum(disp * disp, axis=2), axis=0))

        if not self.per_residue:
            # Return atom_serial, rmsf
            atoms = [traj.topology.atom(int(i)) for i in atom_idx]
            atom_serials = _atom_labels(atoms)
            return np.column_stack([atom_serials, per_atom]).astype(np.float64)

        # Collapse to per-residue: the square root of the mass-weighted mean
        # MSF of the residue's atoms in the selection, which is what
        # `gmx rmsf -res` reports. Averaging the RMSF instead reads low
        # wherever a residue has one mobile atom among several rigid ones
        # (0.1985 nm against 0.2951 on one floppy atom in four), and
        # weighting the atoms equally counts a hydrogen for as much as a
        # carbon. The "protein and name CA" default is one atom per residue,
        # where all three expressions coincide.
        atoms = [traj.topology.atom(int(i)) for i in atom_idx]
        weights = self._weights(atoms)
        residues: dict[int, list[tuple[float, float]]] = {}
        for atom, val, weight in zip(atoms, per_atom, weights):
            residues.setdefault(atom.residue.index, []).append((float(val), float(weight)))

        # Use residue.resSeq (PDB numbering) for the x axis when available;
        # fall back to topology index otherwise.
        from fastmdxplora.analysis.residues import columns, distinct, number

        rows: list[tuple[int, float]] = []
        for ridx in sorted(residues):
            res = traj.topology.residue(ridx)
            label = number(res)
            values = np.array([v for v, _ in residues[ridx]], dtype=float)
            mass = np.array([w for _, w in residues[ridx]], dtype=float)
            if mass.sum() <= 0.0:
                mass = np.ones_like(values)
            rows.append((label, float(np.sqrt(np.sum(mass * values ** 2) / mass.sum()))))

        ordered = [traj.topology.residue(ridx) for ridx in sorted(residues)]
        if not distinct(traj.topology):
            # A table naming each residue's chain: as the two-column array,
            # the numbers of four copies stood in one column and the line
            # through them doubled back on itself three times.
            import pandas as pd

            return pd.DataFrame({**columns(ordered, traj.topology),
                                 "rmsf_nm": [value for _, value in rows]})
        return np.array(rows, dtype=np.float64)

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        if hasattr(result, "columns"):
            from fastmdxplora.analysis.residues import plot_by_chain

            plot_by_chain(ax, result, "rmsf_nm", linewidth=1.4, marker="o",
                          markersize=3, markeredgewidth=0)
        else:
            x = result[:, 0]
            y = result[:, 1]
            ax.plot(x, y, linewidth=1.4, marker="o", markersize=3, markeredgewidth=0)
            ax.fill_between(x, 0, y, alpha=0.12)
        said = self._which_frames()
        if said:
            ax.text(0.01, 0.98, said, transform=ax.transAxes, ha="left",
                    va="top", fontsize="small")

    def _which_frames(self) -> str | None:
        """The frames the fluctuations are over, for the figure."""
        discard = self.findings.get("discard")
        if not isinstance(discard, dict):
            return None
        frames, n = discard["frames"], discard["of_frames"]
        if not frames:
            return f"over all {n:,} frames"
        span = f" ({discard['ns']:.4g} ns)" if discard.get("ns") is not None else ""
        return (f"over frames {frames:,} to {n - 1:,}; the first {frames:,}"
                f"{span} left out as equilibration")

    def default_xlabel(self) -> str | None:
        return "Residue" if self.per_residue else "Atom serial"

    def default_ylabel(self) -> str | None:
        return "RMSF (nm)"


register_analysis(RMSF.name, RMSF)
