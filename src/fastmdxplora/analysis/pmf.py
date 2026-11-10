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
from fastmdxplora.refusals import MissingResultError


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
            raise MissingResultError(
                "No pmf.json beside this run, so there is no umbrella result "
                "to plot. This analysis reports what the umbrella phase "
                "computed; it does not recompute it, because stitching the "
                "windows twice would invite two answers to one question."
            , code="analysis.data.absent")
        record = json.loads(path.read_text(encoding="utf-8"))
        curve = record.get("pmf") or {}
        coordinate = np.asarray(curve.get("coordinate", []), dtype=float)
        # `null` where a bin held no samples: kept as NaN so the drawing shows
        # a gap. Turned into zero it would read as a minimum.
        energy = np.asarray(
            [np.nan if value is None else float(value)
             for value in curve.get("free_energy_kjmol", [])],
            dtype=float)

        mean_force, recomputed = _potential_of_mean_force(record, coordinate,
                                                          energy)

        self._overlaps = record.get("overlaps") or []
        self._refused = record.get("refused")
        self._unsampled = int(record.get("unsampled_bins") or 0)

        # The barrier and the minima, said rather than left to whoever opens
        # the file. Computing them by hand is how the curve gets misread: the
        # grid spans wherever the coordinate went and the windows covered a
        # part of it, so a minimum taken across the whole grid references a
        # region nothing visited.
        # A summary written before a barrier was a peak (no `highest_kjmol`
        # in it), or before a peak within its error was said
        # (`barrier_multiplier`, the peaks judged as a family), or before a
        # radial curve's rises were judged against W's resamples
        # (`resamples_on`), is read again by today's rule, so one curve has
        # one barrier.
        summary = record.get("summary")
        if (not summary or recomputed or "highest_kjmol" not in summary
                or "barrier_multiplier" not in summary
                or "resamples_on" not in summary):
            from fastmdxplora.simulation.umbrella import (
                PERIODIC_VARIABLES, SHELL_COORDINATES, describe_pmf,
                resamples_on_the_curve, turn_is_tiled, variable_unit)

            covered = record.get("covered")
            plan = record.get("plan") or {}
            periodic = plan.get("collective_variable") in PERIODIC_VARIABLES
            # The windows that had samples, as the recombination counted.
            count = record.get("n_windows") or plan.get("n_windows")
            spread = curve.get("uncertainty") or {}
            summary = describe_pmf(
                coordinate, energy if mean_force is None else mean_force,
                tuple(covered) if covered else None,
                periodic=periodic,
                errors=spread.get("standard_error"),
                resamples=resamples_on_the_curve(
                    spread.get("resample_curves_kjmol"), list(energy), mean_force),
                closed=bool(periodic and covered and isinstance(count, int)
                            and turn_is_tiled(covered[0], covered[1], count)),
                unit=("nm" if plan.get("collective_variable") in SHELL_COORDINATES
                      else variable_unit(plan.get("collective_variable"))))
            summary["read_off"] = ("potential_of_mean_force_kjmol"
                                   if mean_force is not None
                                   else "free_energy_kjmol")
            summary["resamples_on"] = summary["read_off"]
        self._summary = summary
        self._variable = (record.get("plan") or {}).get("collective_variable")
        temperature = record.get("temperature_K")
        self._temperature = (float(temperature) if isinstance(temperature, (int, float))
                             and not isinstance(temperature, bool) else None)
        self._mean_force = mean_force is not None
        return {"coordinate": coordinate, "free_energy_kjmol": energy,
                "potential_of_mean_force_kjmol": mean_force,
                "summary": summary}

    def plot(self, result: dict[str, Any], ax: plt.Axes) -> None:
        coordinate = result["coordinate"]
        # Along a distance, W(r): the curve a reader means by a potential of
        # mean force, flat once the two centres no longer interact. The
        # distribution's free energy, which the binding integral uses, is in
        # the data file beside it.
        energy = (result["potential_of_mean_force_kjmol"]
                  if result.get("potential_of_mean_force_kjmol") is not None
                  else result["free_energy_kjmol"])
        radial = result.get("potential_of_mean_force_kjmol") is not None
        covered = (result.get("summary") or {}).get("covered")
        if radial and covered:
            # Between the outermost windows' centres, plotted in full; beyond
            # them a bin is read from one window's tail and the end bin
            # holds the extreme sample, so it is plotted faint and dashed and
            # does not read as a well.
            between = ((coordinate >= float(covered[0]))
                       & (coordinate <= float(covered[1])))
            if not between.all():
                ax.plot(coordinate, energy, linewidth=1.0, linestyle="--",
                        color=colour("FAINT"),
                        label="beyond the outermost windows' centres")
            ax.plot(coordinate, np.where(between, energy, np.nan), linewidth=1.6,
                    label="W(r) = A(r) + 2kT ln r")
        else:
            ax.plot(coordinate, energy, linewidth=1.6,
                    label="W(r) = A(r) + 2kT ln r" if radial else None)

        # The minimum the summary gives: the lowest bin inside the range the
        # windows covered. Taken over the whole grid it marked 2.2 nm on a
        # profile whose well was at 0.5 nm inside a covered 0.35 to 1.6 nm,
        # because the grid runs on past the last window into bins few
        # samples reached.
        sampled = np.isfinite(energy)
        covered = (result.get("summary") or {}).get("covered")
        if covered:
            sampled &= ((coordinate >= float(covered[0]))
                        & (coordinate <= float(covered[1])))
        if sampled.any():
            lowest = int(np.argmin(np.where(sampled, energy, np.inf)))
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
        if getattr(self, "_mean_force", False):
            return "W(r), potential of mean force (kJ/mol)"
        return "Free energy (kJ/mol)"

    def default_xlabel(self) -> str:
        from fastmdxplora.simulation.umbrella import SHELL_COORDINATES, variable_unit

        variable = getattr(self, "_variable", None)
        if variable in SHELL_COORDINATES:
            return "r, distance between the centres (nm)"
        unit = variable_unit(variable)
        if isinstance(variable, str) and variable:
            return f"{variable.replace('_', ' ').capitalize()}" + (f" ({unit})" if unit else "")
        return "Collective variable"

    def save_data(self, result: dict[str, Any], path: Path) -> Path:
        """Two columns, with unsampled bins left as `nan` rather than zeroed.

        `nan` rather than blank, because a blank field makes the row
        two-column in some places and one-column in others, and every reader
        of a whitespace table then disagrees about what it holds.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        mean_force = result.get("potential_of_mean_force_kjmol")

        def said(value: float) -> str:
            return "nan" if np.isnan(value) else f"{value:.6f}"

        if mean_force is None:
            from fastmdxplora.simulation.umbrella import variable_unit

            variable = getattr(self, "_variable", None)
            unit = variable_unit(variable)
            lines = ["# coordinate free_energy_kjmol"]
            if isinstance(variable, str) and variable:
                lines.append(
                    f"# coordinate is the {variable.replace('_', ' ')}"
                    + (f" in {unit}" if unit else
                       " (no unit)" if unit == "" else "")
                    + "; free_energy_kjmol in kJ/mol, zero at its lowest bin.")
            for x, y in zip(result["coordinate"], result["free_energy_kjmol"]):
                lines.append(f"{x:.6f} {said(y)}")
        else:
            from fastmdxplora.simulation.umbrella import KB_KJ

            temperature = getattr(self, "_temperature", None)
            at = ("" if temperature is None else
                  f", kT = {KB_KJ * temperature:.4f} kJ/mol at {temperature:g} K")
            lines = [
                "# coordinate free_energy_kjmol potential_of_mean_force_kjmol",
                "# coordinate is r in nm; both curves in kJ/mol; A(r) is zero"
                " at its lowest bin, W(r) at its lowest bin between the"
                " outermost windows' centres (below zero beyond them).",
                "# free_energy_kjmol is A(r) = -kT ln P(r), the distance's"
                " distribution, shell included; the binding free energy reads"
                " it.",
                "# potential_of_mean_force_kjmol is W(r) = A(r) + 2kT ln r"
                f"{at}, flat where nothing interacts; plotted.",
            ]
            for x, y, w in zip(result["coordinate"], result["free_energy_kjmol"],
                               mean_force):
                lines.append(f"{x:.6f} {said(y)} {said(w)}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def _potential_of_mean_force(record: dict[str, Any], coordinate: np.ndarray,
                             energy: np.ndarray) -> "tuple[np.ndarray | None, bool]":
    """W(r) for a curve along a distance between centres, and whether it was
    worked out here: the record's own where it carries one, else from the
    distribution's free energy and the record's temperature, for a study
    recombined before the record carried it. None for any other coordinate."""
    from fastmdxplora.simulation.umbrella import KB_KJ, SHELL_COORDINATES

    curve = record.get("pmf") or {}
    stored = curve.get("potential_of_mean_force_kjmol")
    if stored is not None:
        # Zeroed again between the outermost windows' centres: a study saved
        # before that was the rule has its W zeroed at the end bin, and its
        # figure would sit apart from a new one's. Nothing changes for a
        # record saved by today's rule.
        return _zeroed_between(np.asarray(
            [np.nan if v is None else float(v) for v in stored], dtype=float),
            coordinate, record.get("covered")), False
    variable = (record.get("plan") or {}).get("collective_variable")
    temperature = record.get("temperature_K")
    if (variable not in SHELL_COORDINATES or not isinstance(temperature, (int, float))
            or coordinate.size == 0 or np.any(coordinate <= 0.0)
            or not np.any(np.isfinite(energy))):
        return None, False
    mean_force = energy + 2.0 * KB_KJ * float(temperature) * np.log(coordinate)
    return _zeroed_between(mean_force, coordinate, record.get("covered")), True


def _zeroed_between(mean_force: np.ndarray, coordinate: np.ndarray,
                    covered: Any) -> np.ndarray:
    """W zero at its lowest bin between the outermost windows' centres, as
    `compute_pmf` saves it; at its lowest bin anywhere where no range is
    known or nothing between holds a value."""
    if not np.any(np.isfinite(mean_force)):
        return mean_force
    if covered and mean_force.shape == coordinate.shape:
        between = (coordinate >= float(covered[0])) & (coordinate <= float(covered[1]))
        if np.any(np.isfinite(mean_force[between])):
            return mean_force - np.nanmin(mean_force[between])
    return mean_force - np.nanmin(mean_force)


register_analysis(PMF.name, PMF)
