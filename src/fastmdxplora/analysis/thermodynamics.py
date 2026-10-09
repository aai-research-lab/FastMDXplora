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
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import MissingResultError

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


def _interval_ns(table: dict[str, np.ndarray]) -> float | None:
    """The spacing of the state record's rows in nanoseconds, from its own
    time column, or None where it has none."""
    for name, values in table.items():
        if "time" in name.lower() and "(ps)" in name.lower() and values.size > 1:
            steps = np.diff(values[np.isfinite(values)])
            steps = steps[steps > 0]
            if steps.size:
                return float(np.median(steps)) / 1000.0
    return None


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


def _rows_of(path: Path) -> tuple[list[str], list[list[float | None]]]:
    """The state record's header and its rows, a cell that is not a number
    None, so a row stays a row; a NaN written as such is kept, as
    :func:`read_state_table` keeps it."""
    rows: list[list[str]] = []
    # A damaged byte makes its cell unreadable, not the record.
    with Path(path).open(encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            if row:
                rows.append(row)
    if not rows:
        return [], []
    header = [cell.lstrip("#").strip().strip('"') for cell in rows[0]]
    values: list[list[float | None]] = []
    for row in rows[1:]:
        parsed: list[float | None] = []
        for index in range(len(header)):
            try:
                parsed.append(float(row[index]))
            except (IndexError, ValueError):
                parsed.append(None)
        values.append(parsed)
    return header, values


def _column_named(header: list[str], word: str) -> int | None:
    for index, name in enumerate(header):
        if name.lower().split(" (")[0] == word:
            return index
    return None


def state_record_pieces(root: str | Path) -> list[dict[str, Any]] | None:
    """The state record of a study carried on in pieces, piece by piece, as
    its joined trajectory holds them; None for a study of one run.

    An extended study is analysed over its joined trajectory, and its
    thermodynamics read only the first piece's `energy.csv`: the means of a
    run of two pieces were the first piece's, beside every other analysis's
    over both. Each piece keeps its rows up to the checkpoint the next one
    went on from (a stopped piece's later rows were run again), and each
    piece's own clock, which starts again at production, is carried on from
    where the piece before it ended.

    Each entry gives the file (``path``), the last step it keeps
    (``last_step``, None for all), and the steps and picoseconds before it
    (``steps_before``, ``ps_before``).
    """
    import json

    from fastmdxplora.analysis.analyze import study_trajectory
    from fastmdxplora.analysis.joining import survey_segments
    from fastmdxplora.simulation.runner import chose_its_own_step, read_checkpoint_sidecar

    base = Path(root)
    joined, _topology = study_trajectory(base)
    if joined is None:
        return None
    try:
        record = json.loads((base / "joined" / "joined.json").read_text(encoding="utf-8"))
        segments = [int(index) for index in record.get("segments") or []]
        folders = {piece.index: piece.directory for piece in survey_segments(base)}
    except (OSError, ValueError, TypeError, AttributeError, StudyError):
        return None
    if len(segments) < 2:
        return None
    trimmed = {str(key) for key in (record.get("trimmed") or {})}

    pieces: list[dict[str, Any]] = []
    steps_before = 0.0
    ps_before = 0.0
    for place, index in enumerate(segments):
        folder = folders.get(index)
        name = "the study's own run" if index == 0 else f"segment {index}"
        path = (folder / "simulation" / "energy.csv") if folder is not None else None
        if path is None or not path.is_file():
            raise MissingResultError(
                f"The study was carried on in {len(segments)} pieces and {name} has no "
                "state record (`simulation/energy.csv`), so its energy, temperature "
                "and density over the whole run cannot be read: the pieces that "
                "have one would be averaged as if they were all of it.",
                code="analysis.data.absent")
        side = read_checkpoint_sidecar(folder / "simulation" / "checkpoint.chk") or {}
        last = side.get("step")
        last_step = (float(last) if isinstance(last, (int, float)) and last >= 0
                     else None)
        goes_on = place < len(segments) - 1
        if last_step is None and str(index) in trimmed:
            raise MissingResultError(
                f"{name.capitalize()} was stopped and its checkpoint records no step, "
                "so the state rows the next piece ran again cannot be told from the "
                "ones it did not.",
                code="analysis.data.absent")
        keeps = last_step if (goes_on or str(index) in trimmed) else None
        pieces.append({"path": path, "last_step": keeps,
                       "steps_before": steps_before, "ps_before": ps_before})
        if not goes_on:
            break
        # Where the next piece's clock starts: the checkpoint it went on
        # from, in this piece's steps and time; its rows read only where the
        # sidecar does not say it.
        timestep = side.get("timestep_fs")
        fixed_step = (isinstance(timestep, (int, float)) and timestep > 0
                      and not chose_its_own_step(folder / "simulation"))
        if keeps is not None and fixed_step:
            steps_before += float(keeps)
            ps_before += float(keeps) * float(timestep) / 1000.0
            continue
        header, rows = _rows_of(path)
        step_at = _column_named(header, "step")
        time_at = _column_named(header, "time")
        kept = [row for row in rows if step_at is None or keeps is None
                or row[step_at] is None or row[step_at] <= keeps]
        end_step = keeps if keeps is not None else (
            (kept[-1][step_at] or 0.0) if kept and step_at is not None else 0.0)
        # An integrator that chose its own step took no fixed time a step
        # (`chose_its_own_step`): the time is the last row kept's own.
        if fixed_step:
            end_ps = end_step * float(timestep) / 1000.0
        else:
            end_ps = (kept[-1][time_at] or 0.0) if kept and time_at is not None else 0.0
        steps_before += float(end_step)
        ps_before += float(end_ps)
    return pieces


def read_pieces_state_table(pieces: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    """One state table of a study's pieces (:func:`state_record_pieces`),
    each piece's rows to its last step, its step and time carried on."""
    header0: list[str] = []
    columns: dict[str, list[float | None]] = {}
    for piece in pieces:
        header, rows = _rows_of(piece["path"])
        if not header0:
            header0 = header
            columns = {name: [] for name in header}
        step_at = _column_named(header, "step")
        time_at = _column_named(header, "time")
        for row in rows:
            if (piece["last_step"] is not None and step_at is not None
                    and row[step_at] is not None and row[step_at] > piece["last_step"]):
                continue
            for index, name in enumerate(header):
                if name not in columns:
                    continue
                value = row[index]
                if value is not None and index == step_at:
                    value += piece["steps_before"]
                elif value is not None and index == time_at:
                    value += piece["ps_before"]
                columns[name].append(value)
    table: dict[str, np.ndarray] = {}
    for name, values in columns.items():
        kept = [value for value in values if value is not None]
        if kept:
            table[name] = np.asarray(kept, dtype=float)
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
            raise MissingResultError(
                "No state record beside this run, so there is nothing to "
                "report the ensemble from. The simulation phase writes one; "
                "a trajectory imported from elsewhere brings its "
                "coordinates and not its thermodynamics."
            , code="analysis.data.absent")

        # A study carried on in pieces is analysed over all of them, its
        # state record too.
        pieces = None
        if (not self.state_csv and path.name == "energy.csv"
                and path.parent.name == "simulation"):
            pieces = state_record_pieces(path.parent.parent)
        table = read_pieces_state_table(pieces) if pieces else read_state_table(path)
        if not table:
            raise StudyError(
                f"{path} holds no rows, so the run recorded no state. A run "
                "that stopped before its first report does this."
            , code="analysis.data.absent")

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

        interval_ns = _interval_ns(table)
        rows: list[tuple[float, float, float, float]] = []
        labels: list[str] = []
        record: dict[str, Any] = {
            "source": str(path),
            **({"pieces": [str(piece["path"]) for piece in pieces]} if pieces else {}),
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
            if key == "volume" and constant_volume:
                # Read through `summarise`, a series that never changes is
                # one observation, and was refused as "not long against its
                # own correlation time": the remedy it named, a longer run,
                # would never have changed it.
                record["volume"] = {
                    "not_a_measurement": (
                        "The box volume did not change over this run: the "
                        "ensemble held it constant, so it is a value the "
                        "setup fixed rather than something the simulation "
                        "sampled, and it has no mean or error to report."
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
                # And how much longer, as every other analysis's mean says
                # it: "what they need" named the trajectory's analyses and
                # left out the energy, temperature and density withheld.
                from fastmdxplora.statistics import mean_record

                shortfall = mean_record(values, frame_interval_ns=interval_ns).get("shortfall")
                if shortfall:
                    entry["shortfall"] = shortfall
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
        record = self.findings.get("thermodynamics") or {}
        # Only the means determined: a withheld mean has no error to plot,
        # and its bar said one beside a table saying "not determined".
        kept = [i for i, label in enumerate(labels)
                if not (record.get(label) or {}).get("not_a_measurement")
                and np.isfinite(result[i][1])]
        if not kept:
            ax.text(0.5, 0.5, "no mean determined" if labels else "no equilibrated observables",
                    ha="center", va="center", transform=ax.transAxes)
            return
        position = np.arange(len(kept))
        # Each observable on its own scale: an energy and a density share
        # no axis, so the bars show how tight each mean is rather than how
        # large it is.
        relative = np.array([
            (result[i][1] / abs(result[i][0]) if result[i][0] else 0.0) for i in kept])
        ax.barh(position, relative * 100.0)
        ax.set_yticks(position)
        ax.set_yticklabels([labels[i] for i in kept])

    def default_xlabel(self) -> str | None:
        return "Standard error, per cent of the mean"


register_analysis(Thermodynamics.name, Thermodynamics)
