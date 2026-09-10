"""Density, energy and temperature, from the record the run already kept.

The simulation phase writes a state table every few hundred steps: step,
time, potential and kinetic energy, temperature, volume and density. Nothing
read it back. It is the only place the ensemble itself is visible, and the
quantities in it are the ones with published values to check against: the
density of a water model is a number with one right answer, and a mean
temperature that misses the thermostat's setpoint says the run was not doing
what the configuration said.

Each column is treated as the correlated time series it is, with the same
equilibration and effective-sample machinery every other observable here gets, so
a density arrives with an error that reflects how many independent
observations stand behind it rather than how many lines were written.

**Density means nothing at constant volume.** Under NVT the box does not
move, so the density is a constant, its variance is zero, and a mean with an
error bar on it would be a statement about arithmetic rather than about the
system. The ensemble is read from the record rather than assumed, and where
the volume never changed this says so instead of quoting a spread of zero as
a precise measurement.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.statistics import summarise

#: The columns worth reporting, and what each is called in the record
#: OpenMM writes. Matched on a substring because the header carries units
#: ("Density (g/mL)") and the units are part of the name rather than
#: something to parse off it.
COLUMNS: dict[str, tuple[str, str]] = {
    "density": ("Density", "g/mL"),
    "potential_energy": ("Potential Energy", "kJ/mol"),
    "kinetic_energy": ("Kinetic Energy", "kJ/mol"),
    "total_energy": ("Total Energy", "kJ/mol"),
    "temperature": ("Temperature", "K"),
    "volume": ("Box Volume", "nm^3"),
}

#: How much the box must vary before the run counts as constant-pressure.
#: A relative standard deviation; anything below this is a fixed box with
#: floating-point noise on it.
VOLUME_VARIES_ABOVE = 1e-6


def read_state_table(path: str | Path) -> dict[str, np.ndarray]:
    """The state record, by column, with the units left in the header.

    Read with the csv module rather than by splitting on commas: OpenMM's
    header quotes its field names, and several of them contain a comma
    inside the quotes.
    """
    rows: list[list[str]] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle):
            if row:
                rows.append(row)
    if len(rows) < 2:
        return {}

    header = [cell.lstrip("#").strip().strip('"') for cell in rows[0]]
    table: dict[str, np.ndarray] = {}
    for index, name in enumerate(header):
        values = []
        for row in rows[1:]:
            if index >= len(row):
                continue
            try:
                values.append(float(row[index]))
            except ValueError:
                continue
        if values:
            table[name] = np.asarray(values, dtype=float)
    return table


class Thermodynamics(Analysis):
    """Ensemble observables from the simulation's own state record.

    Parameters
    ----------
    state_csv : str, optional
        The state record to read. Discovered beside the run when not
        given, which is where the simulation phase writes it.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``thermodynamics.dat`` -- one row per observable, with its mean after equilibration,
    the error on it, and the number of independent observations behind it.
    """

    name = "thermodynamics"
    description = "Density, energy and temperature from the state record"
    #: The state record is of the whole box, so an atom selection would
    #: describe a different quantity than the one written.
    honours_selection = False
    default_selection = None
    time_series = False
    #: Only where the simulation phase left a state record.
    requires_state_record = True

    def __init__(self, *, state_csv: str | None = None,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state_csv = state_csv
        self.options.update(state_csv=self.state_csv)

    def _state_path(self) -> Path | None:
        if self.state_csv:
            given = Path(self.state_csv)
            return given if given.is_file() else None
        here = Path(self.output_dir)
        for parent in (here.parent.parent, here.parent, here):
            for candidate in (
                parent / "simulation" / "energy.csv",
                parent / "simulation" / "state_data.csv",
                parent / "state_data.csv",
                parent / "simulation" / "production_state.csv",
            ):
                if candidate.is_file():
                    return candidate
        return None

    def compute(self, traj: Any) -> np.ndarray:
        path = self._state_path()
        if path is None:
            raise FileNotFoundError(
                "No state record beside this run, so there is nothing to "
                "report the ensemble from. The simulation phase writes one; "
                "a trajectory imported from elsewhere brings its "
                "coordinates and not its thermodynamics."
            )

        table = read_state_table(path)
        if not table:
            raise ValueError(
                f"{path} holds no rows, so the run recorded no state. A run "
                "that stopped before its first report does this."
            )

        found = {}
        for key, (needle, _units) in COLUMNS.items():
            for name, values in table.items():
                if needle.lower() in name.lower():
                    found[key] = values
                    break

        volume = found.get("volume")
        constant_volume = (
            volume is not None and volume.size > 1
            and float(np.std(volume) / max(abs(np.mean(volume)), 1e-30))
            < VOLUME_VARIES_ABOVE
        )

        rows: list[tuple[float, float, float, float]] = []
        labels: list[str] = []
        record: dict[str, Any] = {
            "source": str(path),
            "samples": int(len(next(iter(found.values())))) if found else 0,
            "ensemble": ("constant volume" if constant_volume
                         else "constant pressure"),
        }

        for key, (_needle, units) in COLUMNS.items():
            values = found.get(key)
            if values is None or values.size < 2:
                continue
            if key == "density" and constant_volume:
                record["density"] = {
                    "not_a_measurement": (
                        "The box volume did not change over this run, so the "
                        "density is a constant the setup fixed rather than "
                        "something the simulation sampled. A mean and an "
                        "error on it would describe the arithmetic and not "
                        "the system. Compare a density against a published "
                        "value from a constant-pressure run."
                    ),
                    "value": float(np.mean(values)),
                    "units": units,
                }
                continue

            equilibrated, reason = summarise(values)
            entry: dict[str, Any] = {"units": units}
            if equilibrated is not None:
                entry.update(equilibrated.as_record())
                labels.append(key)
                rows.append((
                    float(equilibrated.mean),
                    float(equilibrated.standard_error),
                    float(equilibrated.effective_samples),
                    float(equilibrated.standard_deviation),
                ))
            if reason is not None:
                entry["not_a_measurement"] = reason
            record[key] = entry

        self.findings["thermodynamics"] = record
        self._labels = labels
        if not rows:
            return np.empty((0, 4))
        return np.array(rows, dtype=float)

    def save_data(self, result: np.ndarray, path: Path) -> Path:
        import pandas as pd

        frame = pd.DataFrame(
            result,
            columns=["mean", "standard_error", "effective_samples",
                     "standard_deviation"],
        )
        frame.insert(0, "observable", getattr(self, "_labels", []))
        frame.to_csv(path, index=False)
        return path

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        labels = getattr(self, "_labels", [])
        if not labels:
            ax.text(0.5, 0.5, "no equilibrated observables",
                    ha="center", va="center", transform=ax.transAxes)
            return
        position = np.arange(len(labels))
        # Each observable on its own scale: an energy and a density share
        # no axis, so the bars show how tight each mean is rather than how
        # large it is.
        relative = np.array([
            (row[1] / abs(row[0]) if row[0] else 0.0) for row in result])
        ax.barh(position, relative * 100.0)
        ax.set_yticks(position)
        ax.set_yticklabels(labels)

    def default_xlabel(self) -> str | None:
        return "Standard error, per cent of the mean"


register_analysis(Thermodynamics.name, Thermodynamics)
