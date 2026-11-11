"""How the replicas of a replica exchange run mixed.

Replica exchange is only as good as its swaps: the replica at the
conditions of interest crosses a barrier by being swapped up the ladder to
where the barrier is low and back down again. This reads the record the
simulation phase wrote (``replica_exchange.json``, see
:mod:`fastmdxplora.simulation.replica_exchange`) and plots, for each pair of
neighbouring conditions, the share of attempted swaps accepted, and says:

- where neighbours accepted fewer than 10% of swaps, the ladder has a gap,
  and the replicas above it rarely reach the bottom;
- how many round trips (bottom to top and back) each replica made; with
  none, full traversal of the ladder has not been demonstrated.

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


class ReplicaMixing(Analysis):
    """The swaps accepted between neighbouring replicas, and their round trips.

    Output
    ------
    ``replica_mixing.dat`` -- comma-separated, one row per pair of
    neighbouring conditions: ``pair`` and ``acceptance``.
    """

    name = "replica_mixing"
    description = "Replica exchange mixing"
    honours_selection = False
    default_selection = None
    #: Only where a replica exchange run left its record.
    requires_replica_exchange = True

    def _record_path(self) -> Path | None:
        output = Path(self.output_dir)
        for candidate in (output.parent.parent / "simulation" / "replica_exchange.json",
                          output.parent.parent / "replica_exchange.json",
                          output.parent / "simulation" / "replica_exchange.json",
                          output.parent / "replica_exchange.json"):
            if candidate.is_file():
                return candidate
        return None

    def compute(self, traj: Any) -> pd.DataFrame:
        path = self._record_path()
        if path is None:
            raise StudyError(
                "No replica_exchange.json beside this run, so there is no mixing to "
                "report. This analysis reads what the simulation phase recorded.",
                code="analysis.data.absent")
        record = json.loads(path.read_text(encoding="utf-8"))
        acceptance = record.get("neighbour_acceptance") or []
        table = pd.DataFrame({
            "pair": [f"{k}-{k + 1}" for k in range(len(acceptance))],
            "acceptance": [np.nan if a is None else float(a) for a in acceptance]})
        self.findings["mixing"] = {
            key: record.get(key) for key in (
                "method", "replicas", "iterations", "conditions", "neighbour_acceptance",
                "round_trips", "round_trips_total", "trajectory")}
        for key in ("gaps", "no_round_trip"):
            if record.get(key):
                self.findings[key] = record[key]
        return table

    def default_xlabel(self) -> str | None:
        return "Neighbouring conditions"

    def default_ylabel(self) -> str | None:
        return "Swaps accepted"

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        x = np.arange(len(result))
        ax.bar(x, result["acceptance"], width=0.7)
        ax.axhline(0.1, color="0.4", linewidth=0.8, linestyle="--")
        ax.set_xticks(x)
        ax.set_xticklabels(result["pair"], fontsize=7)
        ax.set_ylim(0, 1)


register_analysis(ReplicaMixing.name, ReplicaMixing)
