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
from fastmdxplora.refusals import BackendDefect, StudyError


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

    raise BackendDefect(
        f"The surface-area calculation returned unwritten frames on all "
        f"{ATTEMPTS} attempts ({', '.join(str(n) for n in truncated)} frames "
        "each time). A molecule has surface, so a residue exposed in some "
        "frames and reading exactly zero in others was not computed -- that "
        "row was not written.\n\n"
        "This is a defect in the underlying library rather than in the "
        "trajectory, seen on Windows. On a platform where it occurs this "
        "often, solvent-accessible surface area cannot be computed reliably; "
        "run the analysis elsewhere, or omit it."
    , code="analysis.data.absent")


#: Theoretical maximum accessible surface area of each amino acid, in A^2,
#: from Tien, Meyer, Sydykova, Spielman and Wilke, "Maximum allowed solvent
#: accessibilites of residues in proteins", PLoS ONE 8 (2013) e80635, Table 1,
#: "Theor." column: the largest area DSSP gives residue X in a Gly-X-Gly
#: tripeptide over every allowed backbone conformation, with a 1.4 A probe.
#: A residue's relative SASA is its area divided by this.
MAX_ASA_TIEN_2013_A2: dict[str, float] = {
    "ALA": 129.0, "ARG": 274.0, "ASN": 195.0, "ASP": 193.0, "CYS": 167.0,
    "GLN": 225.0, "GLU": 223.0, "GLY": 104.0, "HIS": 224.0, "ILE": 197.0,
    "LEU": 201.0, "LYS": 236.0, "MET": 224.0, "PHE": 240.0, "PRO": 159.0,
    "SER": 155.0, "THR": 172.0, "TRP": 285.0, "TYR": 263.0, "VAL": 174.0,
}

#: Force-field names of the twenty amino acids, read as the residue they are:
#: AMBER's protonation and disulfide variants, CHARMM's histidines and
#: GROMACS's, as `protein_names.PROTEIN_VARIANTS` lists them.
_STANDARD_NAME = {
    "CYX": "CYS", "CYM": "CYS", "CYS1": "CYS", "CYS2": "CYS",
    "HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS", "HSE": "HIS",
    "HSP": "HIS", "HISD": "HIS", "HISE": "HIS", "HISH": "HIS",
    "ASH": "ASP", "ASPH": "ASP", "GLH": "GLU", "GLUH": "GLU",
    "LYN": "LYS", "LYSN": "LYS",
}


def max_asa_nm2(residue_name: str) -> float:
    """The Tien et al. 2013 theoretical maximum area of a residue, in nm^2,
    or NaN for anything that is not one of the twenty amino acids."""
    name = str(residue_name).strip().upper()
    value = MAX_ASA_TIEN_2013_A2.get(_STANDARD_NAME.get(name, name))
    return float("nan") if value is None else value / 100.0


#: How an atom's area is classed in the hydrophobic/polar split: carbon and
#: sulfur are hydrophobic (apolar), nitrogen and oxygen polar. A hydrogen
#: takes the class of the heavy atom it is bonded to; anything else (a
#: selenium, a phosphorus, a metal, a hydrogen with no bond recorded) is
#: "other", so the three add up to the total.
HYDROPHOBIC_ELEMENTS = frozenset({"C", "S"})
POLAR_ELEMENTS = frozenset({"N", "O"})


def surface_classes(topology: md.Topology) -> np.ndarray:
    """Each atom's class for the split: 0 hydrophobic, 1 polar, 2 other."""

    def by_element(atom) -> int:
        symbol = getattr(atom.element, "symbol", "")
        if symbol in HYDROPHOBIC_ELEMENTS:
            return 0
        if symbol in POLAR_ELEMENTS:
            return 1
        return 2

    classes = np.array([by_element(a) for a in topology.atoms], dtype=int)
    for first, second in topology.bonds:
        for hydrogen, heavy in ((first, second), (second, first)):
            if (getattr(hydrogen.element, "symbol", "") == "H"
                    and getattr(heavy.element, "symbol", "") != "H"):
                classes[hydrogen.index] = by_element(heavy)
    return classes


def _sample_std(values: np.ndarray) -> np.ndarray:
    """Each column's sample standard deviation over the frames (ddof=1),
    accumulated in double; NaN where there is a single frame, which has no
    spread to estimate."""
    if values.shape[0] < 2:
        return np.full(values.shape[1:], np.nan)
    return values.std(axis=0, ddof=1, dtype=np.float64)


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
    ligand_resname : str, optional
        The ligand's residue name. Supplied by the analysis phase where the
        study has a ligand. With ``with_ligand`` false it only lets the
        findings say that the surface reported is the protein's without it.
    with_ligand : bool, default False
        Compute the surface of the selection in the presence of the ligand:
        Shrake-Rupley is run on the selection and the ligand together, and
        only the selection's atoms are reported, so a residue the ligand
        covers reads as buried. False gives the selection's surface on its
        own, the apo surface where the selection is the protein: on trypsin
        with benzamidine bound, SER190 reads 0.110 nm2 without the ligand
        and 0.001 nm2 with it. Needs ``ligand_resname``.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``sasa.dat`` — CSV. Either ``frame, sasa_nm2`` (total) or
    ``frame, residue, sasa_nm2`` (per residue, long format), or
    ``residue, mean_sasa_nm2, std_sasa_nm2`` (average_residue).
    ``sasa_average_per_residue.csv``: written beside a per-residue run, with
    the same mean and spread columns as average_residue.
    ``sasa.png`` — Time series (total) or heatmap (residue).

    ``std_sasa_nm2`` is the sample standard deviation over the frames,
    dividing by n_frames - 1, in both places it is written.

    Both per-residue summaries carry ``mean_relative_sasa``: the mean area
    divided by the residue's theoretical maximum from Tien et al. 2013
    (:data:`MAX_ASA_TIEN_2013_A2`), so 0 is buried and 1 as exposed as the
    residue can be in a Gly-X-Gly tripeptide. NaN for a residue that is not
    one of the twenty amino acids. The maxima were computed by DSSP on heavy
    atoms with a 1.4 A probe, so the ratio is on that footing at the default
    ``probe_radius``; with hydrogens present the areas here include them,
    which on trypsin moves an exposed residue's area by a median of 4 per
    cent.

    A ``total`` run also writes ``sasa_polar_split.csv``, the total in each
    frame split into ``hydrophobic_sasa_nm2`` (carbon and sulfur atoms),
    ``polar_sasa_nm2`` (nitrogen and oxygen atoms) and ``other_sasa_nm2``
    (any other element), each hydrogen counted with the heavy atom it is
    bonded to; the three add up to ``sasa_nm2``. Their means after
    equilibration are in the findings under ``hydrophobic_sasa`` and
    ``polar_sasa``.

    References
    ----------
    Tien, M. Z.; Meyer, A. G.; Sydykova, D. K.; Spielman, S. J.; Wilke, C. O.
    Maximum allowed solvent accessibilites of residues in proteins.
    *PLoS ONE* **2013**, 8, e80635.
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
        ligand_resname: str | None = None,
        with_ligand: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.ligand_resname: str | None = (
            str(ligand_resname).strip() or None if ligand_resname else None)
        self.with_ligand: bool = bool(with_ligand)
        if self.with_ligand and not self.ligand_resname:
            raise StudyError(
                "SASA with `with_ligand: true` computes the surface in the "
                "presence of the ligand, and needs `ligand_resname` to know "
                "which residue that is. None was given or detected."
            , code="analysis.option.missing_companion",
                analysis="sasa", requires="ligand_resname")
        mode = str(mode).lower()
        if mode not in VALID_MODES:
            raise StudyError(
                f"SASA mode must be one of {VALID_MODES}; got {mode!r}"
            , code="analysis.option.not_permitted")
        self.mode: str = mode
        self.probe_radius: float = float(probe_radius)
        self.n_sphere_points: int = int(n_sphere_points)
        self.options.update(
            mode=self.mode,
            probe_radius=self.probe_radius,
            n_sphere_points=self.n_sphere_points,
            ligand_resname=self.ligand_resname,
            with_ligand=self.with_ligand,
        )

    def _ligand_atoms(self, traj: md.Trajectory, selected: np.ndarray) -> np.ndarray:
        """The ligand's atoms that are not already in the selection."""
        if not self.ligand_resname:
            return np.empty(0, dtype=int)
        try:
            ligand = traj.topology.select(f"resname {self.ligand_resname}")
        except Exception:  # noqa: BLE001 - a name the language cannot parse
            ligand = np.empty(0, dtype=int)
        return np.setdiff1d(ligand, selected)

    def _say_which_surface(self, ligand: np.ndarray) -> None:
        """Record whether the surface reported is with the ligand or without."""
        if self.with_ligand:
            self.findings["ligand"] = (
                f"The surface is of the selection ({self.selection!r}) in the "
                f"presence of the ligand {self.ligand_resname} ({ligand.size} "
                "atoms): Shrake-Rupley was run on both together and only the "
                "selection's atoms are reported, so residues the ligand covers "
                "read as buried."
            )
        elif ligand.size:
            self.findings["ligand"] = (
                f"The surface is of the selection ({self.selection!r}) without "
                f"the ligand {self.ligand_resname} ({ligand.size} atoms), which "
                "was left out of the calculation, so residues the ligand covers "
                "read as exposed: the apo surface in the bound conformation. "
                "Set `with_ligand: true` for the surface in the presence of "
                "the ligand."
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
        ligand = self._ligand_atoms(traj, atom_idx)
        if self.with_ligand and ligand.size == 0:
            raise StudyError(
                f"SASA with `with_ligand: true` found no atoms of the ligand "
                f"{self.ligand_resname!r} outside the selection "
                f"{self.selection!r}, so there is no ligand to compute the "
                "surface in the presence of."
            , code="analysis.selection.empty",
                expression=f"resname {self.ligand_resname}", role="ligand")
        self._say_which_surface(ligand)

        if self.with_ligand:
            # Shrake-Rupley on the selection and the ligand together, keeping
            # the selection's atoms: an atom's area is what the probe reaches
            # with the ligand in place. Summed by residue here, as MDTraj's
            # residue mode sums its atoms.
            both = np.union1d(atom_idx, ligand)
            areas, retried = _areas_that_were_written(
                traj.atom_slice(both),
                probe_radius=self.probe_radius,
                n_sphere_points=self.n_sphere_points,
                mode="atom",
            )
            areas = areas[:, np.isin(both, atom_idx)]
            traj = traj.atom_slice(atom_idx)
            if self.mode == "total":
                sasa = areas
            else:
                owner = np.array([a.residue.index for a in traj.topology.atoms])
                sasa = np.zeros((traj.n_frames, traj.n_residues), dtype=areas.dtype)
                np.add.at(sasa.T, owner, areas.T)
        else:
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
            self._record_polar_split(traj, sasa)
            return pd.DataFrame(
                {"frame": np.arange(traj.n_frames), "sasa_nm2": total}
            )

        self._remember_maxima(traj)

        if self.mode == "average_residue":
            # The mean exposure of each residue over the whole run, which is
            # the summary somebody actually reads: which residues are buried
            # and which are on the surface. The per-frame matrix contains it,
            # but reading it off a heatmap by eye is not the same as having
            # it. Version 1 wrote all three from one run.
            from fastmdxplora.analysis.residues import columns

            return pd.DataFrame({
                # With a chain column where there are several chains: the
                # number alone named four residues at once on a tetramer.
                **columns(list(traj.topology.residues), traj.topology),
                # Accumulated in double. `numpy.mean` on a float32 array
                # sums in float32, so a long run loses digits the per-frame
                # route does not -- pandas groups in double and the two
                # answers then differ in their last figures for no reason
                # anyone reading them could guess.
                "mean_sasa_nm2": sasa.mean(axis=0, dtype=np.float64),
                # The spread matters: a residue at 1.0 every frame and one
                # alternating between 0 and 2 have the same mean and are not
                # the same thing. The sample standard deviation, dividing by
                # n_frames - 1, as the per-residue run's summary computes it:
                # the two had divided by n and by n - 1 under one name.
                "std_sasa_nm2": _sample_std(sasa),
            })

        # Per-residue: build a long-form table. Residue labels = resSeq
        # (PDB numbering) when available.
        from fastmdxplora.analysis.residues import columns

        n_frames, n_res = sasa.shape
        named = columns(list(traj.topology.residues), traj.topology)
        return pd.DataFrame(
            {
                "frame": np.repeat(np.arange(n_frames), n_res),
                **{key: np.tile(values, n_frames) for key, values in named.items()},
                "sasa_nm2": sasa.flatten(),
            }
        )

    _polar_split: pd.DataFrame | None = None
    _maxima: pd.DataFrame | None = None

    def _record_polar_split(self, traj: md.Trajectory, areas: np.ndarray) -> None:
        """Split each frame's total by atom class and record the means."""
        from fastmdxplora.analysis.base import _frame_interval_ns
        from fastmdxplora.statistics import mean_record

        classes = surface_classes(traj.topology)
        split = np.stack([areas[:, classes == k].sum(axis=1, dtype=np.float64)
                          for k in range(3)], axis=1)
        self._polar_split = pd.DataFrame({
            "frame": np.arange(traj.n_frames),
            "hydrophobic_sasa_nm2": split[:, 0],
            "polar_sasa_nm2": split[:, 1],
            "other_sasa_nm2": split[:, 2],
        })
        interval = _frame_interval_ns(traj)
        for k, name in enumerate(("hydrophobic_sasa", "polar_sasa")):
            record = mean_record(split[:, k], frame_interval_ns=interval)
            record["unit"] = "nm²"
            self.findings[name] = record
        self.findings["surface_classes"] = (
            "Hydrophobic is the area of carbon and sulfur atoms, polar that of "
            "nitrogen and oxygen atoms, each hydrogen counted with the heavy "
            "atom it is bonded to; other is any remaining element. "
            f"{int(np.sum(classes == 2))} of {classes.size} atoms are other."
        )

    def _remember_maxima(self, traj: md.Trajectory) -> None:
        """Each residue's theoretical maximum area, keyed as the tables are."""
        from fastmdxplora.analysis.residues import columns

        residues = list(traj.topology.residues)
        self._maxima = pd.DataFrame({
            **columns(residues, traj.topology),
            "max_asa_nm2": [max_asa_nm2(r.name) for r in residues],
        })
        self.findings["relative_sasa"] = (
            "mean_relative_sasa is each residue's mean area over its "
            "theoretical maximum, Tien et al. 2013 (PLoS ONE 8, e80635), "
            "computed with a 1.4 A probe"
            + ("." if abs(self.probe_radius - 0.14) < 1e-9 else
               f"; this run used {self.probe_radius:g} nm, so the ratio is "
               "not on the footing of those maxima.")
        )

    def _with_relative(self, summary: pd.DataFrame) -> pd.DataFrame:
        """The summary with ``mean_relative_sasa`` beside its mean area."""
        if self._maxima is None:
            return summary
        keys = [k for k in ("chain", "residue", "insertion") if k in summary]
        maxima = self._maxima.drop_duplicates(subset=keys)
        if "insertion" in keys:
            maxima = maxima.assign(insertion=maxima["insertion"].fillna("").astype(str))
            summary = summary.assign(insertion=summary["insertion"].fillna("").astype(str))
        joined = summary.merge(maxima, on=keys, how="left")
        joined["mean_relative_sasa"] = joined["mean_sasa_nm2"] / joined["max_asa_nm2"]
        return joined.drop(columns="max_asa_nm2")

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        if self.mode == "total":
            x, _ = self.frame_axis_for_plot(self._traj_for_plot, len(result))
            ax.plot(x, result["sasa_nm2"].to_numpy(), linewidth=1.4)
            ax.fill_between(x, 0, result["sasa_nm2"].to_numpy(), alpha=0.15)
        elif self.mode == "average_residue" and "chain" in result:
            # One line per chain on the deposited numbering, the spread as a
            # band: bars at the same numbers would stand on one another.
            from fastmdxplora.analysis.residues import plot_by_chain

            plot_by_chain(ax, result, "mean_sasa_nm2", spread="std_sasa_nm2", linewidth=1.2)
            ax.set_ylim(bottom=0)
        elif self.mode == "average_residue":
            # A bar per residue, with the spread over the run drawn on it. A
            # residue at 1.0 every frame and one alternating between 0 and 2
            # have the same mean, and a bar chart without the spread says they
            # are the same.
            #
            # One bar per row, placed by its position in the table and named
            # by its residue: placed at the residue number, trypsin's 184A
            # and 184 stood at the same x and one hid the other.
            means = result["mean_sasa_nm2"].to_numpy()
            position = np.arange(len(result))
            ax.bar(position, means,
                   yerr=result["std_sasa_nm2"].to_numpy(),
                   color=colour("SERIES"), error_kw={"ecolor": colour("ACCENT"),
                                              "elinewidth": 0.8, "capsize": 2})
            names = [f"{number}{code}" for number, code in zip(
                result["residue"].tolist(),
                (result["insertion"].fillna("").astype(str).tolist()
                 if "insertion" in result else [""] * len(result)))]
            # About forty labels fit across a page-width figure.
            every = max(1, int(np.ceil(len(names) / 40)))
            ax.set_xticks(position[::every])
            ax.set_xticklabels(names[::every], rotation=90, fontsize="x-small")
            ax.set_xlim(-0.5, len(result) - 0.5)
            ax.set_ylim(bottom=0)
        else:
            # Per-residue heatmap: pivot long-form -> (residue × frame)
            rows = [c for c in ("chain", "residue", "insertion") if c in result]
            grid = result.pivot(
                index=rows, columns="frame", values="sasa_nm2"
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
        if self.mode == "average_residue":
            self._with_relative(result).to_csv(path, index=False)
        else:
            result.to_csv(path, index=False)
        if self.mode == "total" and self._polar_split is not None:
            self._polar_split.to_csv(path.parent / f"{self.name}_polar_split.csv",
                                     index=False)

        if self.mode == "residue":
            # By chain and insertion code as well as number, where the table
            # has them: grouped by the number alone, the copies of a residue
            # in a structure of several chains were averaged together, and
            # 184 and 184A of one chain with them.
            keys = [key for key in ("chain", "residue", "insertion") if key in result]
            # pandas' std is the sample standard deviation (ddof=1), the
            # same definition average_residue uses.
            summary = (
                result.groupby(keys, dropna=False)["sasa_nm2"]
                .agg(mean_sasa_nm2="mean", std_sasa_nm2="std")
                .reset_index()
            )
            self._with_relative(summary).to_csv(
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
