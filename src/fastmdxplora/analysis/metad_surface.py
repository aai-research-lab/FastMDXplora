"""The free energy surface from a metadynamics run.

The free energy over the chosen collective variable, reconstructed from the
bias the run deposited. Read it like a landscape: basins are the states the
system visits, and the walls between them are the barriers, in kJ/mol. The
surface arrives with its own convergence evidence -- basin transitions
counted, drift measured -- and a run whose bias has not converged still gets
its picture, drawn and clearly labelled provisional, with a note beside it
saying exactly what is missing. Only a metadynamics run deposits a bias to
read; elsewhere this has nothing to say, and says so.

The frames themselves are not averaged: a metadynamics trajectory is
deliberately not a Boltzmann ensemble -- that is the point of the method --
so the surface comes from the hills the simulation phase recorded, and this
reads that record rather than trying to recompute it, which would invite two
answers to the same question.

(Why this analysis exists at all: the surface was once written to
`metadynamics_surface.json` and stopped there -- no figure, no manifest
entry, no mention in the report -- the same gap the umbrella study had.)
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


class MetadynamicsSurface(Analysis):
    """The free energy along a metadynamics run's collective variable."""

    name = "metad_surface"
    description = "Metadynamics free energy surface"
    #: The coordinate was chosen when the bias was planned; a selection here
    #: would describe a different question.
    honours_selection = False
    default_selection = None
    time_series = False
    #: Only where a metadynamics run produced a record.
    requires_metadynamics = True

    def _record_path(self) -> Path | None:
        """Where the simulation phase left the surface it computed."""
        # `self.output_dir` already has this analysis's own name appended,
        # so the run's directory is two levels up: <run>/analysis/<name>.
        output = Path(self.output_dir)
        for candidate in (
            output.parent.parent / "simulation" / "metadynamics_surface.json",
            output.parent.parent / "metadynamics_surface.json",
            output.parent / "simulation" / "metadynamics_surface.json",
            output.parent / "metadynamics_surface.json",
        ):
            if candidate.is_file():
                return candidate
        return None

    def compute(self, traj: Any) -> dict[str, Any]:
        path = self._record_path()
        if path is None:
            raise FileNotFoundError(
                "No metadynamics_surface.json beside this run, so there is "
                "no surface to draw. This analysis reports what the "
                "simulation phase computed from the hills; it does not "
                "recompute it."
            )
        record = json.loads(path.read_text(encoding="utf-8"))
        self._refused = record.get("refused")
        self._provisional = bool(record.get("provisional"))
        self._evidence = record.get("evidence") or {}
        dimensions = int(record.get("dimensions") or 1)
        self._dimensions = dimensions

        if dimensions == 1:
            grid = np.asarray(record.get("grid") or [], dtype=float)
            energy = np.asarray(
                [np.nan if value is None else float(value)
                 for value in (record.get("free_energy_kjmol") or [])],
                dtype=float)
            if len(grid) and len(energy):
                return {"coordinate": grid, "free_energy_kjmol": energy}
        elif dimensions == 2:
            axes = tuple(np.asarray(axis, dtype=float)
                         for axis in (record.get("axes") or []))
            energy = np.asarray(
                record.get("free_energy_kjmol") or [], dtype=float)
            expected = tuple(len(axis) for axis in axes)
            if energy.size and (
                    len(axes) != dimensions or energy.shape != expected):
                raise ValueError(
                    "The metadynamics record says it is two-dimensional, "
                    f"but its axes have lengths {expected} and its free-energy "
                    f"array has shape {energy.shape}. A surface needs one "
                    "array dimension per collective-variable axis."
                )
            if energy.size and np.isfinite(energy).any():
                per_dimension = self._evidence.get("per_dimension") or []
                self._axis_names = tuple(
                    str(per_dimension[index].get("name", f"cv{index + 1}"))
                    if index < len(per_dimension) else f"cv{index + 1}"
                    for index in range(dimensions))
                return {
                    "dimensions": dimensions,
                    "axes": axes,
                    "axis_names": self._axis_names,
                    "free_energy_kjmol": energy,
                }
        else:
            raise ValueError(
                f"The metadynamics record has {dimensions} dimensions; this "
                "analysis can draw one- and two-dimensional surfaces."
            )

        raise ValueError(
            "The metadynamics record holds no surface: "
            + (str(self._refused) if self._refused
               else "the coordinate did not move, so there is nothing "
                    "along it to draw.")
        )

    def _band(self) -> "np.ndarray | None":
        """The per-point convergence band, where the run computed one.

        Absent from records written before the band existed, and absent from
        a run with too few hills to cut, so every reader of it is optional.
        """
        record = getattr(self, "_evidence", None) or {}
        convergence = record.get("convergence") or {}
        values = convergence.get("band_kjmol")
        if not values:
            return None
        band = np.asarray(values, dtype=float)
        return band if np.isfinite(band).any() else None

    def plot(self, result: dict[str, Any], ax: plt.Axes) -> None:
        if result.get("dimensions") == 2:
            first, second = result["axes"]
            energy = result["free_energy_kjmol"]
            image = ax.contourf(first, second, energy.T, levels=24)
            ax.figure.colorbar(image, ax=ax, label="Free energy (kJ/mol)")
            lowest = np.unravel_index(np.nanargmin(energy), energy.shape)
            ax.plot(first[lowest[0]], second[lowest[1]], marker="o",
                    markersize=4, color="white", markeredgecolor=colour("ACCENT"))
            if self._provisional:
                ax.set_title(f"{self.figure_title()} (provisional)")
            return

        coordinate = result["coordinate"]
        energy = result["free_energy_kjmol"]
        ax.plot(coordinate, energy, linewidth=1.6)

        # Shaded, and labelled for what it is. A band drawn round a free
        # energy is read as an error bar unless the legend says otherwise,
        # and this one is a statement about whether the bias stopped moving,
        # not about how far the answer would move in a second run. The
        # simulation phase writes the same disclaimer into the manifest.
        band = self._band()
        if band is not None and band.shape == energy.shape:
            ax.fill_between(coordinate, energy - band, energy + band,
                            alpha=0.18, linewidth=0, color=colour("BAND"),
                            label="convergence band (not a standard error)")

        sampled = np.isfinite(energy)
        if sampled.any():
            lowest = int(np.nanargmin(np.where(sampled, energy, np.inf)))
            ax.axvline(coordinate[lowest], color=colour("GUIDE"), linestyle=":",
                       linewidth=1.0,
                       label=f"minimum at {coordinate[lowest]:.3g}")

        # Where the drift was judged, because the surface above it is read
        # off the shape rather than trusted point by point: those regions are
        # visited rarely and their estimate moves by several kJ/mol however
        # long the run.
        ceiling = self._evidence.get("drift_ceiling_kjmol")
        if ceiling and sampled.any():
            floor = float(np.nanmin(energy))
            ax.axhline(floor + float(ceiling), color=colour("FAINT"),
                       linestyle="--", linewidth=0.8,
                       label=f"judged below {ceiling:g} kJ/mol")
        if sampled.any():
            ax.legend(loc="best")

        if self._provisional:
            ax.set_title(f"{self.figure_title()} (provisional)")

    def default_ylabel(self) -> str:
        if getattr(self, "_dimensions", 1) == 2:
            return getattr(self, "_axis_names", ("cv1", "cv2"))[1]
        return "Free energy (kJ/mol)"

    def default_xlabel(self) -> str:
        if getattr(self, "_dimensions", 1) == 2:
            return getattr(self, "_axis_names", ("cv1", "cv2"))[0]
        return "Collective variable"

    def save_data(self, result: dict[str, Any], path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if result.get("dimensions") == 2:
            first, second = result["axes"]
            first_name, second_name = result["axis_names"]
            header = f"# {first_name} {second_name} free_energy_kjmol"
            if self._provisional:
                header += ("\n# provisional: the bias had not converged "
                           "when this was cut")
            lines = [header]
            energy = result["free_energy_kjmol"]
            for i, x in enumerate(first):
                for j, y in enumerate(second):
                    value = energy[i, j]
                    shown = "nan" if np.isnan(value) else f"{value:.6f}"
                    lines.append(f"{x:.6f} {y:.6f} {shown}")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return path

        coordinate = result["coordinate"]
        energy = result["free_energy_kjmol"]
        band = self._band()
        if band is not None and band.shape != np.asarray(energy).shape:
            band = None

        header = "# coordinate free_energy_kjmol"
        if band is not None:
            header += " convergence_band_kjmol"
        if self._provisional:
            header += "\n# provisional: the bias had not converged when this was cut"
        if band is not None:
            # Spelled out in the file, because a third column beside a free
            # energy is read as its error bar and this one is not that. The
            # column is the spread of the surface over the later part of its
            # own deposition -- whether the bias converged here -- and it
            # cannot see how far the surface would move in an independent
            # run. Replicas are the statistical error.
            header += (
                "\n# convergence_band_kjmol: spread across cumulative cuts "
                "through the later deposition. A convergence indicator, NOT "
                "a standard error: the cuts are nested and share one "
                "trajectory, so this is a lower bound on run-to-run spread. "
                "Independent replicas are the statistical error.")
        lines = [header]
        for index, (x, y) in enumerate(zip(coordinate, energy)):
            shown = "nan" if np.isnan(y) else f"{y:.6f}"
            row = f"{x:.6f} {shown}"
            if band is not None:
                value = band[index]
                row += f" {'nan' if np.isnan(value) else f'{value:.6f}'}"
            lines.append(row)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


register_analysis(MetadynamicsSurface.name, MetadynamicsSurface)
