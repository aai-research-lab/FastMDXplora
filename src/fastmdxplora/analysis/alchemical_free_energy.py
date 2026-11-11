"""The free energy an alchemical run found, and whether its states overlap.

Reads the record the simulation phase wrote (``alchemical.json``, see
:mod:`fastmdxplora.simulation.alchemical`): the free energy of decoupling
the group, MBAR's uncertainty, the restraint's standard-state correction
where the leg was restrained, and the overlap between each pair of
neighbouring states, plotted. Below 0.03 the estimate between two states
rests on almost no shared samples, and it is said.

It does not recompute anything from the trajectory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError


class AlchemicalFreeEnergy(Analysis):
    """The decoupling free energy and the overlap between neighbouring states.

    Output
    ------
    ``alchemical_free_energy.dat`` -- comma-separated, one row per pair of
    neighbouring states: ``pair`` and ``overlap``.
    """

    name = "alchemical_free_energy"
    description = "Alchemical free energy"
    honours_selection = False
    default_selection = None
    #: Only where an alchemical run left its record.
    requires_alchemical = True

    def _record_path(self) -> Path | None:
        output = Path(self.output_dir)
        for candidate in (output.parent.parent / "simulation" / "alchemical.json",
                          output.parent.parent / "alchemical.json",
                          output.parent / "simulation" / "alchemical.json",
                          output.parent / "alchemical.json"):
            if candidate.is_file():
                return candidate
        return None

    def compute(self, traj: Any) -> pd.DataFrame:
        path = self._record_path()
        if path is None:
            raise StudyError(
                "No alchemical.json beside this run, so there is no free energy to "
                "report. This analysis reads what the simulation phase recorded.",
                code="analysis.data.absent")
        record = json.loads(path.read_text(encoding="utf-8"))
        overlap = record.get("neighbour_overlap") or []
        self.findings["free_energy"] = {
            key: record.get(key) for key in (
                "group", "dG_decouple_kj_per_mol", "uncertainty_kj_per_mol",
                "dG_decouple_kcal_per_mol", "meaning", "estimated_by", "states",
                "iterations", "trajectory")}
        if record.get("restraint"):
            self.findings["restraint"] = record["restraint"]
        if record.get("thin_overlap"):
            self.findings["thin_overlap"] = record["thin_overlap"]
        return pd.DataFrame({"pair": [f"{k}-{k + 1}" for k in range(len(overlap))],
                             "overlap": [float(v) for v in overlap]})

    def default_xlabel(self) -> str | None:
        return "Neighbouring states"

    def default_ylabel(self) -> str | None:
        return "Overlap"

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        x = np.arange(len(result))
        ax.bar(x, result["overlap"], width=0.7)
        ax.axhline(0.03, color="0.4", linewidth=0.8, linestyle="--")
        ax.set_xticks(x)
        ax.set_xticklabels(result["pair"], fontsize=7, rotation=90)


register_analysis(AlchemicalFreeEnergy.name, AlchemicalFreeEnergy)
