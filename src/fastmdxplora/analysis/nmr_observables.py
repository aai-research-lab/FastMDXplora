"""What an NMR experiment would see in the frames, beside what it saw.

Two observables that follow from the coordinates without a model of
anything else:

**Scalar couplings 3J(HN,HA)**, from each residue's backbone angle phi by a
Karplus equation, ``J = A cos^2(phi - phi0) + B cos(phi - phi0) + C``, with
the coefficients of Vögeli, Ying, Grishaev and Bax (J. Am. Chem. Soc. 129,
9377 (2007)) unless another set is named (MDTraj's ``Bax1997`` and
``Ruterjans1999``). The coupling is averaged over the frames, as the
experiment averages it. The coefficients are fitted to measured couplings,
and the scatter of that fit, about 0.4 to 0.9 Hz depending on the set, is
the least difference that says anything about the ensemble.

**NOE distances**, each pair's ``<r^-6>^(-1/6)`` over the frames, summing
``r^-6`` over the atoms of each side where a side names several (a methyl's
three protons, or two that cannot be told apart), as an NOE is averaged. A
pair whose distance is above its upper bound is a violation.

Each is compared with the experiment's own values where a file of them is
given (``j_couplings_file``: comma-separated ``residue`` (its number),
``j_hz`` and, where there are several chains, ``chain`` (from 1);
``noe_file``: ``selection_1``, ``selection_2``, ``upper_nm``).

What is not computed: a SAXS curve, which needs the hydration shell and the
excluded solvent modelled (as CRYSOL and Pepsi-SAXS do), without which a
curve from the protein's atoms alone would be compared with an experiment
that does not see them alone; and chemical shifts, which need a predictor
trained on structures (SPARTA+, SHIFTX2).
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
from fastmdxplora.refusals import StudyError

#: Each Karplus set's scatter against the couplings it was fitted to, Hz.
SCATTER_HZ = {"Bax2007": 0.39, "Bax1997": 0.73, "Ruterjans1999": 0.91}


def karplus(phi: np.ndarray, model: str) -> np.ndarray:
    """3J(HN,HA) in Hz from phi in radians, by MDTraj's coefficients."""
    from mdtraj.nmr.scalar_couplings import J3_HN_HA_coefficients

    c = J3_HN_HA_coefficients[model]
    angle = phi + c["phi0"]
    return c["A"] * np.cos(angle) ** 2 + c["B"] * np.cos(angle) + c["C"]


def noe_distance(traj: md.Trajectory, first: np.ndarray, second: np.ndarray) -> float:
    """``<sum r^-6>^(-1/6)`` over the frames, the sum over every atom of
    ``first`` with every atom of ``second``."""
    pairs = np.array([(a, b) for a in first for b in second if a != b], dtype=int)
    if not len(pairs):
        return float("nan")
    r = md.compute_distances(traj, pairs, periodic=False)
    return float(np.mean((r ** -6.0).sum(axis=1)) ** (-1.0 / 6.0))


class NMRObservables(Analysis):
    """3J(HN,HA) couplings and NOE distances, with the experiment's beside them.

    Options
    -------
    karplus : str, default "Bax2007"
        The Karplus coefficients: ``Bax2007``, ``Bax1997`` or ``Ruterjans1999``.
    j_couplings_file : str, default ""
        Measured couplings, comma-separated: ``residue``, ``j_hz`` and, with
        several chains, ``chain`` (from 1).
    noe_file : str, default ""
        NOE upper bounds, comma-separated: ``selection_1``, ``selection_2``
        (MDTraj selections) and ``upper_nm``.

    Output
    ------
    ``nmr_observables.dat`` -- comma-separated, one row per residue with a
    phi: ``residue``, ``chain``, ``j_hz`` (the mean over the frames),
    ``sd_hz`` and, where measured, ``measured_hz``.
    ``nmr_noes.dat`` -- where an NOE file is given: ``selection_1``,
    ``selection_2``, ``upper_nm``, ``distance_nm`` and ``violation_nm``.
    """

    name = "nmr_observables"
    description = "NMR couplings and NOEs"
    default_selection = "protein"
    #: Asked for where there is an experiment to set beside it.
    requires_naming = True

    MODELS = tuple(SCATTER_HZ)

    def __init__(self, *, karplus: str = "Bax2007", j_couplings_file: str = "",
                 noe_file: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if karplus not in self.MODELS:
            raise StudyError(
                f"`karplus` is one of {', '.join(self.MODELS)}; it was {karplus!r}.",
                code="analysis.option.inapplicable")
        self.karplus = karplus
        self.j_couplings_file, self.noe_file = str(j_couplings_file or ""), str(noe_file or "")
        self.options.update(karplus=karplus, j_couplings_file=self.j_couplings_file,
                            noe_file=self.noe_file)
        self._noes: pd.DataFrame | None = None

    # ------------------------------------------------------------------
    def _read(self, given: str, columns: tuple[str, ...]) -> pd.DataFrame:
        path = Path(given)
        if not path.is_absolute() and self.study_dir is not None:
            path = self.study_dir / path
        if not path.is_file():
            raise StudyError(f"{given} is not there.", code="analysis.data.absent")
        table = pd.read_csv(path, comment="#")
        missing = [c for c in columns if c not in table.columns]
        if missing:
            raise StudyError(
                f"{path.name} has no column {', '.join(missing)}; it has "
                f"{', '.join(map(str, table.columns))}.", code="analysis.data.absent")
        return table

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        topology = traj.topology
        chosen = set(int(a) for a in self.select_atoms(traj))
        indices, phi = md.compute_phi(traj, periodic=False)
        keep = [k for k, four in enumerate(indices) if int(four[1]) in chosen]
        if not keep:
            raise StudyError(
                f"{self.selection!r} has no residue with a backbone angle phi, so "
                "there is no coupling to compute.", code="analysis.selection.empty")
        indices, phi = indices[keep], phi[:, keep]
        coupling = karplus(phi, self.karplus)
        residues = [topology.atom(int(four[2])).residue for four in indices]
        table = pd.DataFrame({
            "residue": [r.resSeq for r in residues],
            "name": [r.name for r in residues],
            "chain": [r.chain.index + 1 for r in residues],
            "j_hz": np.round(coupling.mean(axis=0), 4),
            "sd_hz": np.round(coupling.std(axis=0), 4),
        })
        scatter = SCATTER_HZ[self.karplus]
        self.findings["couplings"] = {
            "residues": len(table),
            "karplus": self.karplus,
            "karplus_scatter_hz": scatter,
            "averaged": "the coupling of each frame, averaged over the frames",
            "glycine": "a glycine has two alpha protons and two couplings; the one "
                       "given is to the proton a Karplus set for other residues "
                       "describes, and is not compared",
        }
        if self.j_couplings_file:
            measured = self._read(self.j_couplings_file, ("residue", "j_hz"))
            if "chain" not in measured.columns:
                if table["chain"].nunique() > 1:
                    raise StudyError(
                        f"The protein has {table['chain'].nunique()} chains and "
                        f"{self.j_couplings_file} names none: give a `chain` column.",
                        code="analysis.data.absent")
                measured = measured.assign(chain=int(table["chain"].iloc[0]))
            merged = table.merge(
                measured[["residue", "chain", "j_hz"]].rename(columns={"j_hz": "measured_hz"}),
                on=["residue", "chain"], how="left")
            table = merged
            compared = table[table["measured_hz"].notna() & (table["name"] != "GLY")]
            if not len(compared):
                raise StudyError(
                    f"No residue of {self.j_couplings_file} is among the protein's "
                    "residues with a phi (glycines are not compared).",
                    code="analysis.data.not_this_system")
            difference = compared["j_hz"] - compared["measured_hz"]
            rms = float(np.sqrt((difference ** 2).mean()))
            self.findings["against_the_measured_couplings"] = {
                "residues": len(compared),
                "rms_difference_hz": rms,
                "mean_difference_hz": float(difference.mean()),
                "beyond_the_karplus_scatter": bool(rms > scatter),
                "largest": [
                    {"residue": f"{row.name}{row.residue} (chain {row.chain})",
                     "computed_hz": float(row.j_hz), "measured_hz": float(row.measured_hz)}
                    for row in compared.assign(off=difference.abs())
                    .sort_values("off", ascending=False).head(5).itertuples()],
                "meaning": (
                    f"An RMS difference within the Karplus set's own scatter "
                    f"({scatter} Hz) says the frames' backbone angles are as the "
                    "experiment's; one beyond it says some are not, or that the "
                    "experiment's conditions are not the run's."),
            }
        if self.noe_file:
            bounds = self._read(self.noe_file, ("selection_1", "selection_2", "upper_nm"))
            rows = []
            for row in bounds.itertuples():
                try:
                    first = topology.select(str(row.selection_1))
                    second = topology.select(str(row.selection_2))
                except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
                    raise StudyError(f"An NOE's selection MDTraj cannot read: {exc}",
                                     code="analysis.selection.invalid") from exc
                if not len(first) or not len(second):
                    raise StudyError(
                        f"The NOE between {row.selection_1!r} and {row.selection_2!r} "
                        "names atoms the frames do not have (hydrogens are in the frames "
                        "only where the study stored them).",
                        code="analysis.selection.empty")
                distance = noe_distance(traj, first, second)
                rows.append({"selection_1": row.selection_1, "selection_2": row.selection_2,
                             "upper_nm": float(row.upper_nm),
                             "distance_nm": round(distance, 4),
                             "violation_nm": round(max(0.0, distance - float(row.upper_nm)), 4)})
            self._noes = pd.DataFrame(rows)
            violated = self._noes[self._noes["violation_nm"] > 0]
            self.findings["noes"] = {
                "pairs": len(self._noes),
                "violated": len(violated),
                "violated_by_more_than_0.05_nm": int((violated["violation_nm"] > 0.05).sum()),
                "largest_violation_nm": float(self._noes["violation_nm"].max()),
                "averaged": "<sum r^-6>^(-1/6) over the frames, r^-6 summed over the "
                            "atoms of each side",
            }
        self.findings["not_computed"] = (
            "A SAXS curve, which needs the hydration shell and excluded solvent "
            "modelled (CRYSOL, Pepsi-SAXS), and chemical shifts, which need a trained "
            "predictor (SPARTA+, SHIFTX2).")
        return table

    # ------------------------------------------------------------------
    def save_data(self, result: pd.DataFrame, path: Path) -> Path:
        written = super().save_data(result, path)
        if self._noes is not None:
            self._noes.to_csv(path.with_name("nmr_noes.dat"), index=False)
        return written

    def default_xlabel(self) -> str | None:
        return "Residue"

    def default_ylabel(self) -> str | None:
        return "3J(HN,HA) (Hz)"

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        x = np.arange(len(result))
        ax.errorbar(x, result["j_hz"], yerr=result["sd_hz"], fmt="o", markersize=3,
                    linewidth=0.8, label="from the frames")
        if "measured_hz" in result.columns:
            ax.plot(x, result["measured_hz"], "s", markersize=3, fillstyle="none",
                    label="measured")
            ax.legend(loc="best", fontsize=7.5)


register_analysis(NMRObservables.name, NMRObservables)
