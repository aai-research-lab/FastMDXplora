"""Solvent-Accessible Surface Area (SASA).

Per-frame SASA computed with the Shrake-Rupley rolling-sphere algorithm
(MDTraj's :func:`mdtraj.shrake_rupley`), for the whole molecule, for each
residue per frame, or as each residue's mean over the run -- which is the
summary that says which residues are buried. Outputs the total SASA time
series and, optionally, a per-residue heatmap that shows which residues
become exposed/buried over the simulation.

SASA is a sensitive probe of conformational changes that involve burial
or exposure of hydrophobic surfaces — it can detect folding/unfolding
events, partial unfolding of loops, and binding/unbinding transitions
that don't necessarily show up in RMSD.

References
----------
Shrake, A.; Rupley, J. A. *J. Mol. Biol.* **1973**, 79, 351.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis


#: What a SASA run reports. ``total`` is the whole molecule per frame,
#: ``residue`` every residue per frame, and ``average_residue`` each residue's
#: mean over the run -- which is the summary somebody reads to find out what
#: is buried.
VALID_MODES = ("total", "residue", "average_residue")


#: How far above the run's usual proportion of zeros a frame must sit before
#: it is taken as unwritten. The fault puts most of a row at zero at once,
#: against a median of none; a real protein sits at a steady tenth or so,
#: because the residues that are buried stay buried.
UNWRITTEN_MARGIN = 0.4


def _unwritten_frames(sasa: np.ndarray) -> np.ndarray:
    """Frames the surface-area calculation did not finish writing.

    The signature is a frame in which far more residues read exactly zero
    than in any other. On Windows the fault leaves a row like
    ``[0.5354027, 0, 0, 0, 0]`` -- a partial value where the write stopped,
    then nothing -- so four fifths of that row is zero against none in the
    frames around it.

    Compared against the run's own median rather than against an absolute
    count, because a real protein has buried residues whose surface area is
    exactly zero, and they flicker between zero and a little above it as the
    structure breathes. A first version asked whether a residue was exposed in
    some frames and zero in others, which is true of every buried residue in
    every protein: it refused a solvated T4 lysozyme outright, five attempts
    and eighty seconds before giving up, on a run that was perfectly good.
    """
    if sasa.ndim != 2 or sasa.shape[0] < 3 or sasa.shape[1] == 0:
        return np.empty(0, dtype=int)

    zero_fraction = (sasa == 0.0).sum(axis=1) / sasa.shape[1]
    usual = float(np.median(zero_fraction))
    return np.flatnonzero(zero_fraction > usual + UNWRITTEN_MARGIN)


#: How many times to ask before giving up. Two was not enough: CI returned
#: answers truncated on both attempts. How often a call is truncated, and
#: whether attempts are independent, is not yet known -- see the
#: characterisation test in the suite -- so this is a number chosen to be
#: cheap rather than one derived from a measured rate.
ATTEMPTS = 5


def _areas_that_were_written(
    traj: Any, *, probe_radius: float, n_sphere_points: int, mode: str
) -> tuple[np.ndarray, str | None]:
    """Surface areas, asking again while the answer comes back truncated.

    On Windows, MDTraj's ``shrake_rupley`` returns frames that were not fully
    written: a partial value followed by zeros. It is not confined to the last
    frame -- frames 0, 2 and 5 have all been seen -- and a second call is not
    reliably clean, so this asks up to ``ATTEMPTS`` times and refuses if none
    of them is.

    Asking again is a workaround, not a rescue, and the distinction is the one
    this package draws elsewhere: a failed simulation is diagnosed and stopped
    because a salvaged trajectory looks exactly like one that never needed
    salvaging. Here the wrong answer identifies itself -- a residue exposed in
    five frames and reading exactly zero in the sixth was not measured -- and a
    correct one may be one call away.

    Returns the areas and, where more than one attempt was needed, a note for
    the findings.
    """
    truncated: list[int] = []
    for attempt in range(1, ATTEMPTS + 1):
        areas = md.shrake_rupley(
            traj, probe_radius=probe_radius,
            n_sphere_points=n_sphere_points, mode=mode)
        empty = _unwritten_frames(areas)
        if empty.size == 0:
            if attempt == 1:
                return areas, None
            return areas, (
                f"The surface-area calculation returned unwritten frames on "
                f"{attempt - 1} of {attempt} attempts and completed on the "
                "last. This is a known defect in the underlying library on "
                "some platforms, not a property of this trajectory; the areas "
                "reported are from the complete result."
            )
        truncated.append(int(empty.size))

    raise RuntimeError(
        f"The surface-area calculation returned unwritten frames on all "
        f"{ATTEMPTS} attempts ({', '.join(str(n) for n in truncated)} frames "
        "each time). A molecule has surface, so a residue exposed in some "
        "frames and reading exactly zero in others was not measured -- that "
        "row was not written.\n\n"
        "This is a defect in the underlying library rather than in the "
        "trajectory, seen on Windows. On a platform where it occurs this "
        "often, solvent-accessible surface area cannot be computed reliably; "
        "run the analysis elsewhere, or omit it."
    )


class SASA(Analysis):
    """Solvent-accessible surface area.

    Parameters
    ----------
    mode : {"total", "residue", "average_residue"}, default "total"
        ``"total"`` returns one value per frame (sum over all atoms).
        ``"residue"`` returns a per-residue SASA matrix (n_frames × n_residues).
    probe_radius : float, default 0.14
        Probe (solvent) radius in nm. The default 0.14 nm is the water
        radius and is the standard choice for biomolecular SASA.
    n_sphere_points : int, default 960
        Number of points on the unit sphere for the Shrake-Rupley rolling
        ball. Higher is more accurate but slower. 960 is MDTraj's default
        and provides ~1% precision.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``sasa.dat`` — CSV. Either ``frame, sasa_nm2`` (total) or
    ``frame, residue, sasa_nm2`` (per residue, long format).
    ``sasa.png`` — Time series (total) or heatmap (residue).
    """

    name = "sasa"
    time_series = True
    reweightable = ("sasa_nm2", "SASA (nm²)")
    description = "Solvent-accessible surface area"
    #: A surface accessible to solvent, computed with the solvent present,
    #: is occluded by the very water whose access it measures: the number
    #: is not large and slow to reach, it is wrong. A run through the
    #: orchestrator never met this because the scope selection resolves to
    #: the solute, but a direct call from a notebook did, and paid for it
    #: twice -- on a small test with 1,500 waters the whole system took 5.9
    #: times as long as the protein alone, and a solvated box carries ten
    #: times that many.
    default_selection = "protein"

    def __init__(
        self,
        *,
        mode: str = "total",
        probe_radius: float = 0.14,
        n_sphere_points: int = 960,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        mode = str(mode).lower()
        if mode not in VALID_MODES:
            raise ValueError(
                f"SASA mode must be one of {VALID_MODES}; got {mode!r}"
            )
        self.mode: str = mode
        self.probe_radius: float = float(probe_radius)
        self.n_sphere_points: int = int(n_sphere_points)
        self.options.update(
            mode=self.mode,
            probe_radius=self.probe_radius,
            n_sphere_points=self.n_sphere_points,
        )

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        """Compute SASA per frame.

        Returns
        -------
        pandas.DataFrame
            ``mode="total"``: columns ``frame, sasa_nm2``.
            ``mode="residue"``: columns ``frame, residue, sasa_nm2`` (long form).
        """
        # Restrict to the selected atoms (e.g. protein/solute) before
        # computing SASA — solvent should not contribute to the solute's
        # accessible surface area.
        atom_idx = self.select_atoms(traj)
        if len(atom_idx) < traj.n_atoms:
            traj = traj.atom_slice(atom_idx)

        mode_arg = "atom" if self.mode == "total" else "residue"
        sasa, retried = _areas_that_were_written(
            traj,
            probe_radius=self.probe_radius,
            n_sphere_points=self.n_sphere_points,
            mode=mode_arg,
        )
        if retried:
            self.findings["recomputed"] = retried

        if self.mode == "total":
            total = sasa.sum(axis=1)
            return pd.DataFrame(
                {"frame": np.arange(traj.n_frames), "sasa_nm2": total}
            )

        if self.mode == "average_residue":
            # The mean exposure of each residue over the whole run, which is
            # the summary somebody actually reads: which residues are buried
            # and which are on the surface. The per-frame matrix contains it,
            # but reading it off a heatmap by eye is not the same as having
            # it. Version 1 wrote all three from one run.
            residues = list(traj.topology.residues)
            try:
                labels = np.array([int(r.resSeq) for r in residues])
            except (AttributeError, TypeError):
                labels = np.array([r.index for r in residues])
            return pd.DataFrame({
                "residue": labels,
                # Accumulated in double. `numpy.mean` on a float32 array
                # sums in float32, so a long run loses digits the per-frame
                # route does not -- pandas groups in double and the two
                # answers then differ in their last figures for no reason
                # anyone reading them could guess.
                "mean_sasa_nm2": sasa.mean(axis=0, dtype=np.float64),
                # The spread matters: a residue at 1.0 every frame and one
                # alternating between 0 and 2 have the same mean and are not
                # the same thing.
                "std_sasa_nm2": sasa.std(axis=0, dtype=np.float64),
            })

        # Per-residue: build a long-form table. Residue labels = resSeq
        # (PDB numbering) when available.
        residues = list(traj.topology.residues)
        try:
            labels = np.array([int(r.resSeq) for r in residues])
        except (AttributeError, TypeError):
            labels = np.array([r.index for r in residues])

        n_frames, n_res = sasa.shape
        frames = np.repeat(np.arange(n_frames), n_res)
        residue_col = np.tile(labels, n_frames)
        return pd.DataFrame(
            {
                "frame": frames,
                "residue": residue_col,
                "sasa_nm2": sasa.flatten(),
            }
        )

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        if self.mode == "total":
            x, _ = self.frame_axis_for_plot(self._traj_for_plot, len(result))
            ax.plot(x, result["sasa_nm2"].to_numpy(), linewidth=1.4)
            ax.fill_between(x, 0, result["sasa_nm2"].to_numpy(), alpha=0.15)
        elif self.mode == "average_residue":
            # A bar per residue, with the spread over the run drawn on it. A
            # residue at 1.0 every frame and one alternating between 0 and 2
            # have the same mean, and a bar chart without the spread says they
            # are the same.
            residues = result["residue"].to_numpy()
            means = result["mean_sasa_nm2"].to_numpy()
            ax.bar(residues, means,
                   yerr=result["std_sasa_nm2"].to_numpy(),
                   color=colour("SERIES"), error_kw={"ecolor": colour("ACCENT"),
                                              "elinewidth": 0.8, "capsize": 2})
            ax.set_ylim(bottom=0)
        else:
            # Per-residue heatmap: pivot long-form -> (residue × frame)
            grid = result.pivot(
                index="residue", columns="frame", values="sasa_nm2"
            ).to_numpy()
            im = ax.imshow(
                grid,
                aspect="auto",
                origin="lower",
                cmap="viridis",
                interpolation="nearest",
            )
            ax.figure.colorbar(im, ax=ax, label="SASA (nm²)", shrink=0.85)

    def save_data(self, result: pd.DataFrame, path) -> Any:
        """Write the table, and for a per-residue run the average beside it.

        That run already contains every number the average needs, so computing
        the surface a second time to get it would cost minutes for arithmetic.
        Version 1 wrote all three outputs from one run; this writes two, and
        the third mode exists for anyone who wants only the summary.
        """
        from pathlib import Path

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(path, index=False)

        if self.mode == "residue":
            summary = (
                result.groupby("residue")["sasa_nm2"]
                .agg(mean_sasa_nm2="mean", std_sasa_nm2="std")
                .reset_index()
            )
            summary.to_csv(
                path.parent / f"{self.name}_average_per_residue.csv",
                index=False,
            )
        return path

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
        if self.mode == "average_residue":
            return "Residue"
        if self.mode == "residue":
            return "Frame"
        if self._traj_for_plot is None:
            return "Frame"
        _, label = self.frame_axis(self._traj_for_plot)
        return label

    def default_ylabel(self) -> str | None:
        if self.mode == "average_residue":
            return "Mean SASA (nm²)"
        if self.mode == "residue":
            return "Residue (index in topology)"
        return "SASA (nm²)"


register_analysis(SASA.name, SASA)
