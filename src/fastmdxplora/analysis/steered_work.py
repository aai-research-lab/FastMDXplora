"""The work done by a steered pull.

The cumulative work done on the system as the anchor moves, drawn against
the pulled distance. The shape of the curve matters more than the total: a
pull that accumulates work smoothly has met resistance all the way, while
one that accumulates it in a step has snapped past something -- an unbinding
event, a broken contact -- and the total is the same in both cases, so the
total alone hides which happened. Only a steered run records a pull to read;
elsewhere this has nothing to say, and says so.

What this is not is a free energy. The work depends on how fast the anchor
moved -- a fast pull works against solvent and strain as well as the
interactions being measured. Jarzynski's equality recovers a free energy
from *many* pulls, and its average is dominated by rare low-work
trajectories, so it needs many more than feels reasonable. One pull gives a
pathway, and the figure says so.

(Why this analysis exists at all: the work was once written to
`steered_work.json` and stopped there -- no figure, no manifest entry, no
mention in the report -- the same gap the umbrella study and the
metadynamics surface had.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis


class SteeredWork(Analysis):
    """Work against the coordinate, for a steered run."""

    name = "steered_work"
    description = "Work done by the pull"
    honours_selection = False
    default_selection = None
    time_series = False
    #: Only where a steered run produced a record.
    requires_steered = True

    def _record_path(self) -> Path | None:
        """`self.output_dir` already carries this analysis's own name, so the
        run's directory is two levels up: <run>/analysis/<name>."""
        output = Path(self.output_dir)
        for candidate in (
            output.parent.parent / "simulation" / "steered_work.json",
            output.parent.parent / "steered_work.json",
            output.parent / "simulation" / "steered_work.json",
        ):
            if candidate.is_file():
                return candidate
        return None

    def compute(self, traj: Any) -> dict[str, Any]:
        path = self._record_path()
        if path is None:
            raise FileNotFoundError(
                "No steered_work.json beside this run, so there is no pull "
                "to draw. This analysis reports what the simulation phase "
                "recorded; it does not recompute it."
            )
        record = json.loads(path.read_text(encoding="utf-8"))
        trajectory = record.get("trajectory") or {}
        coordinate = np.asarray(trajectory.get("coordinate") or [], dtype=float)
        work = np.asarray(trajectory.get("work_kjmol") or [], dtype=float)
        if not len(coordinate) or len(coordinate) != len(work):
            raise ValueError(
                "The steered record holds no pull to draw: the coordinate "
                "and the work do not line up."
            )

        self._total = float(record.get("work_kjmol") or work[-1])
        self._rate = record.get("pull_rate_per_ns")
        self._requested = record.get("requested_to")
        return {"coordinate": coordinate, "work_kjmol": work}

    def plot(self, result: dict[str, Any], ax: plt.Axes) -> None:
        coordinate = result["coordinate"]
        work = result["work_kjmol"]
        ax.plot(coordinate, work, linewidth=1.6)

        # Where the pull was asked to reach, against where it got to. The
        # anchor travels the whole way and the system lags behind it; that
        # lag is the dissipation, and seeing the gap is seeing how much of
        # the work went into it. A tripeptide asked to 1.0 nm reached 0.769.
        reached = float(coordinate[-1])
        if self._requested is not None:
            requested = float(self._requested)
            if abs(requested - reached) > 1e-6:
                ax.axvline(requested, color=colour("WARN"), linestyle="--",
                           linewidth=1.0,
                           label=f"asked for {requested:.3g}, reached "
                                 f"{reached:.3g}")
                ax.legend(loc="best")

        rate = f", {self._rate:.3g} per ns" if self._rate else ""
        ax.set_title(
            f"{self.figure_title()}: {self._total:.1f} kJ/mol{rate} "
            "(a pathway, not a free energy)")

    def default_ylabel(self) -> str:
        return "Work (kJ/mol)"

    def default_xlabel(self) -> str:
        return "Collective variable"

    def save_data(self, result: dict[str, Any], path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# coordinate work_kjmol",
            "# The work depends on how fast the anchor moved. This is a "
            "pathway, not a free energy.",
        ]
        for x, y in zip(result["coordinate"], result["work_kjmol"]):
            lines.append(f"{x:.6f} {y:.6f}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


register_analysis(SteeredWork.name, SteeredWork)
