"""The potential of mean force from an umbrella study.

The free energy along the pulled coordinate, assembled from the umbrella
windows. Minima are the states the system prefers; the height between two
minima is the cost of crossing, in kJ/mol, and is the number an umbrella
study is run to obtain. Windows must overlap for the profile to mean
anything: where they do not, the gap is reported as a gap rather than
bridged, and bins nobody sampled stay blank in the drawing rather than
becoming zeroes that would read as a spurious minimum. Only an umbrella
study has windows to read; on any other run this has nothing to say, and
says so.

This reads what the simulation phase already computed rather than recomputing
it -- the stitching is delicate, and doing it twice would invite two answers
to the same question.

(Why this analysis exists at all: for an umbrella run the free energy along
the coordinate *is* the study, and it was once written to `pmf.json` and
stopped there -- no figure, no manifest entry, no mention in the report,
while sixteen lesser curves each got all three.)
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


class PMF(Analysis):
    """The free energy along an umbrella study's coordinate."""

    name = "pmf"
    description = "Potential of mean force"
    #: There is nothing to select: the coordinate was chosen when the windows
    #: were planned, and a selection here would describe a different question.
    honours_selection = False
    default_selection = None
    #: Not per frame. The x axis is the collective variable, not time.
    time_series = False
    #: Only where an umbrella study was run, which is what `pmf.json` records.
    requires_umbrella = True

    def _pmf_path(self) -> Path | None:
        """Where the umbrella phase left its result.

        The study writes it beside the runs rather than inside any one of
        them, because it is the answer from all the windows together.
        """
        output = Path(self.output_dir)
        for candidate in (
            output.parent / "pmf.json",
            output.parent.parent / "pmf.json",
            output / "pmf.json",
        ):
            if candidate.is_file():
                return candidate
        return None

    def compute(self, traj: Any) -> dict[str, Any]:
        """Read the curve, ignoring the trajectory.

        The trajectory of any one window is not the study: it is a system
        held at one position by a spring, and its distribution says nothing
        about the free energy on its own.
        """
        path = self._pmf_path()
        if path is None:
            raise FileNotFoundError(
                "No pmf.json beside this run, so there is no umbrella result "
                "to draw. This analysis reports what the umbrella phase "
                "computed; it does not recompute it, because stitching the "
                "windows twice would invite two answers to one question."
            )
        record = json.loads(path.read_text(encoding="utf-8"))
        curve = record.get("pmf") or {}
        coordinate = np.asarray(curve.get("coordinate", []), dtype=float)
        # `null` where a bin held no samples: kept as NaN so the drawing shows
        # a gap. Turned into zero it would read as a minimum.
        energy = np.asarray(
            [np.nan if value is None else float(value)
             for value in curve.get("free_energy_kjmol", [])],
            dtype=float)

        self._overlaps = record.get("overlaps") or []
        self._refused = record.get("refused")
        self._unsampled = int(record.get("unsampled_bins") or 0)

        # The barrier and the minima, said rather than left to whoever opens
        # the file. Computing them by hand is how the curve gets misread: the
        # grid spans wherever the coordinate went and the windows covered a
        # part of it, so a minimum taken across the whole grid references a
        # region nothing visited.
        summary = record.get("summary")
        if not summary:
            from fastmdxplora.simulation.umbrella import describe_pmf

            covered = record.get("covered")
            summary = describe_pmf(
                coordinate, energy,
                tuple(covered) if covered else None)
        self._summary = summary
        return {"coordinate": coordinate, "free_energy_kjmol": energy,
                "summary": summary}

    def plot(self, result: dict[str, Any], ax: plt.Axes) -> None:
        coordinate = result["coordinate"]
        energy = result["free_energy_kjmol"]
        ax.plot(coordinate, energy, linewidth=1.6)

        sampled = np.isfinite(energy)
        if sampled.any():
            lowest = int(np.nanargmin(np.where(sampled, energy, np.inf)))
            ax.axvline(coordinate[lowest], color=colour("GUIDE"), linestyle=":",
                       linewidth=1.0,
                       label=f"minimum at {coordinate[lowest]:.3g}")
            ax.legend(loc="best")

        # The windows, so a reader can see where the curve came from and
        # judge a feature sitting on a seam between two of them.
        for overlap in getattr(self, "_overlaps", []):
            for centre in overlap.get("centres", []):
                ax.axvline(float(centre), color=colour("FAINT"), linewidth=0.6,
                           zorder=0)

        if getattr(self, "_unsampled", 0):
            ax.set_title(
                f"{self.figure_title()} "
                f"({self._unsampled} bins unsampled)")

    def default_ylabel(self) -> str:
        return "Free energy (kJ/mol)"

    def default_xlabel(self) -> str:
        return "Collective variable"

    def save_data(self, result: dict[str, Any], path: Path) -> Path:
        """Two columns, with unsampled bins left as `nan` rather than zeroed.

        `nan` rather than blank, because a blank field makes the row
        two-column in some places and one-column in others, and every reader
        of a whitespace table then disagrees about what it holds.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# coordinate free_energy_kjmol"]
        for x, y in zip(result["coordinate"], result["free_energy_kjmol"]):
            lines.append(f"{x:.6f} {'nan' if np.isnan(y) else f'{y:.6f}'}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


register_analysis(PMF.name, PMF)
