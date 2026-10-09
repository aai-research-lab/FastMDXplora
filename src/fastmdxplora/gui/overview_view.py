"""What the Overview shows first: what a study determined, how its run went,
and its thermodynamics on the production's own clock.

The Overview opened on a health card that said "Completed" and "Ok" beside
it, then six charts the full width of the page plotted against the sample
index, seven cards repeating the health card's numbers, a table of phases
each marked "Ok", and, last, what the analyses determined. This gathers what
the page now leads with, from the records the Analysis page and the run
already keep:

- each mean the analyses recorded, as the Analysis page gives it
  (:func:`fastmdxplora.gui.analysis_overview.overview_of`), with the
  analysis whose series can be plotted beside it, and RMSF said over its
  residues;
- the production's start on the simulation's clock, so every plot reads
  time from the start of production, equilibration before 0, as the
  analyses' own times do;
- each thermodynamic quantity's mean over the production: the
  thermodynamics analysis's own, where it has recorded one, so the page
  gives one mean for one quantity; else taken from the live record as the
  analyses take theirs (:func:`fastmdxplora.statistics.mean_record`: its
  equilibrated part, its error from its own correlation, withheld where the
  production holds too few independent samples); no mean of a density
  where the box's volume was held;
- each phase's status and how long it took.

Read again only when a file it reads has changed.
"""

from __future__ import annotations

import csv
import json
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["overview_payload", "THERMODYNAMICS", "MOST_TILES", "thinned_metrics"]

#: The thermodynamic quantities the Overview plots, as the live record
#: names them, with what each is called and its unit.
THERMODYNAMICS: tuple[tuple[str, str, str], ...] = (
    ("potential_energy", "Potential energy", "kJ/mol"),
    ("total_energy", "Total energy", "kJ/mol"),
    ("temperature", "Temperature", "K"),
    ("density", "Density", "g/mL"),
)

#: The most means shown as tiles; the rest are named as more on the
#: Analysis page.
MOST_TILES = 9

#: What an analysis's own series is plotted from, where its mean is that
#: series': an analysis named here, whose quantity is its ``mean``.
_SERIES_OF = {"rmsd", "rg", "hbonds", "sasa", "qvalue", "ligand_rmsd"}

_CACHE: dict[str, tuple[tuple, dict[str, Any]]] = {}
_LOCK = threading.Lock()


def overview_payload(root: str | Path) -> dict[str, Any]:
    """The Overview's results, clock, thermodynamic means and phases."""
    root = Path(root)
    key = _stamp(root)
    with _LOCK:
        kept = _CACHE.get(str(root))
        if kept and kept[0] == key:
            return kept[1]
    payload = {"ok": True, **_tiles(root)}
    # A record a crash left damaged costs its part of the page, not the page.
    try:
        payload["thermodynamics"] = _thermodynamics(root)
    except Exception:  # noqa: BLE001 - a panel must never break the page
        payload["thermodynamics"] = {"production_start_ns": None,
                                     "target_temperature_K": None, "means": {}, "samples": 0}
    try:
        payload["phases"] = _phases(root)
    except Exception:  # noqa: BLE001
        payload["phases"] = []
    try:
        payload["speed_ns_per_day"] = _production_speed(root)
    except Exception:  # noqa: BLE001
        payload["speed_ns_per_day"] = None
    with _LOCK:
        if len(_CACHE) > 32:
            _CACHE.clear()
        _CACHE[str(root)] = (key, payload)
    return payload


def _production_speed(root: Path) -> float | None:
    """The speed production ran at, from its own steps and times, as the
    fix card prices a carry-on: the line beside it gave the live record's
    average from the run's start, setup and minimisation in it, half the
    price's."""
    from fastmdxplora.remedies import _speed

    pieces = sorted(root.glob("segment-*/simulation/live_status.json"))
    folder = pieces[-1].parent.parent if pieces else root
    # The one speed every price is read at: the run's cost record where it
    # wrote one, else its production's own steps and times.
    seconds, _platform = _speed(folder, root)
    return round(86400.0 / seconds, 4) if seconds and seconds > 0 else None


def _stamp(root: Path) -> tuple:
    """What the payload is read from, by size and time written: the
    analyses' manifest and records, the live record, the run's manifest."""
    paths = [root / "manifest.json", root / "simulation" / "live_metrics.csv",
             root / "simulation" / "live_status.json",
             root / "analysis" / "analysis_manifest.json"]
    # A study carried on in pieces: each piece's live record moves it on.
    paths += sorted(root.glob("segment-*/simulation/live_status.json"))
    paths += sorted(root.glob("segment-*/simulation/live_metrics.csv"))
    try:
        paths += sorted((root / "analysis").glob("*/options.json"))
    except OSError:
        pass
    stamp = []
    for path in paths:
        try:
            seen = path.stat()
            stamp.append((str(path), seen.st_size, seen.st_mtime_ns))
        except OSError:
            stamp.append((str(path), None, None))
    return tuple(stamp)


# ---------------------------------------------------------------------------
# What the analyses determined
# ---------------------------------------------------------------------------

def _tiles(root: Path) -> dict[str, Any]:
    from fastmdxplora.gui.analysis_overview import overview_of

    try:
        overview = overview_of(root)
    except Exception:  # noqa: BLE001 - a panel must never break the page
        return {"tiles": [], "more": 0, "means": 0, "determined": 0, "analyses": 0}
    rows = overview.get("rows") or []
    tiles: list[dict[str, Any]] = []
    means = determined = 0
    for row in rows:
        name = row.get("analysis")
        if name == "rmsf":
            rmsf = _rmsf(root)
            if rmsf:
                tiles.append({**rmsf, "anchor": row.get("anchor") or "rmsf"})
            continue
        for quantity in row.get("quantities") or []:
            if not isinstance(quantity, dict) or quantity.get("value") is None:
                continue
            means += 1
            determined += bool(quantity.get("determined"))
            series = name if (quantity.get("key") == "mean" and name in _SERIES_OF) else None
            tiles.append({
                "kind": "mean",
                "analysis": name,
                "anchor": row.get("anchor") or name,
                "label": quantity.get("label") or row.get("title") or name,
                "said": quantity.get("said") or "",
                "unit": quantity.get("unit") or "",
                "determined": bool(quantity.get("determined")),
                "why": quantity.get("why") or "",
                "samples": quantity.get("samples"),
                "from_ns": quantity.get("from_ns"),
                "reweighted": bool(quantity.get("reweighted")),
                "series": series,
            })
    shown = tiles[:MOST_TILES]
    # The means not shown, as the heading counts means: the RMSF tile is
    # not one, and "11 more not shown" was said of ten.
    more = sum(1 for tile in tiles[MOST_TILES:] if tile.get("kind") == "mean")
    return {"tiles": shown, "more": more, "means": means,
            "determined": determined, "analyses": len(rows),
            "biased": bool(overview.get("biased"))}


def _rmsf(root: Path) -> dict[str, Any] | None:
    """RMSF over its residues (or atoms): their mean, range and values; it
    is one value a residue, not a series over time, so it has no error."""
    folder = root / "analysis" / "rmsf"
    values: list[float] = []
    try:
        for line in (folder / "rmsf.dat").read_text(encoding="utf-8").splitlines():
            parts = line.replace(",", " ").split()
            if not parts or line.lstrip().startswith("#"):
                continue
            try:
                values.append(float(parts[-1]))
            except ValueError:
                continue
    except OSError:
        return None
    values = [v for v in values if math.isfinite(v)]
    if not values:
        return None
    try:
        asked = (json.loads((folder / "options.json").read_text(encoding="utf-8"))
                 .get("options") or {})
    except (OSError, ValueError, AttributeError):
        asked = {}
    each = "residue" if not isinstance(asked, dict) or asked.get("per_residue", True) else "atom"
    from fastmdxplora.gui.report_dashboard import _format_metric_value

    return {
        "kind": "per_residue",
        "analysis": "rmsf",
        "label": "RMSF",
        "each": each,
        "said": f"{_format_metric_value(min(values))} to {_format_metric_value(max(values))} nm",
        "mean_said": f"{_format_metric_value(sum(values) / len(values))} nm",
        "count": len(values),
        "values": [round(v, 5) for v in values[:2000]],
    }


# ---------------------------------------------------------------------------
# The production's clock and its thermodynamics
# ---------------------------------------------------------------------------

def _thermodynamics(root: Path) -> dict[str, Any]:
    from fastmdxplora.gui.simulated_time import simulated_times
    from fastmdxplora.gui.telemetry import status_as_it_stands

    # As the sidebar and the health card read it: a run that ended without
    # saying so, and a study carried on in pieces, read as one run.
    status = status_as_it_stands(root)
    rows = _metric_rows(root)
    try:
        times = simulated_times(root, status)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        times = {}
    start = _production_start_ns(rows, times)
    production = [row for row in rows if _is_production(row)]
    recorded = _recorded_thermodynamics(root)
    # Production's own energy file, where it wrote one: the series the
    # thermodynamics analysis and the report's convergence table average,
    # so a mean the analysis did not record is the report's, not one taken
    # from the live record's coarser samples, which gave another number.
    averaged = _production_energies(root) or production
    volumes = [v for v in (_number(row.get("volume")) for row in averaged) if v is not None]
    held = len(volumes) > 1 and max(volumes) - min(volumes) <= 1e-9 * max(abs(max(volumes)), 1e-30)
    means: dict[str, Any] = {}
    interval = _spacing_ns(averaged)
    for key, _label, unit in THERMODYNAMICS:
        entry = recorded.get(key)
        if isinstance(entry, dict) and ("mean" in entry or "not_a_measurement" in entry):
            means[key] = _said(entry, unit, _count(recorded.get("samples")))
            continue
        values = [v for v in (_number(row.get(key)) for row in averaged) if v is not None]
        if key == "density" and held and len(values) > 1:
            means[key] = _said({"value": sum(values) / len(values),
                                "not_a_measurement": HELD_DENSITY}, unit, len(values))
            continue
        means[key] = _mean_of(values, unit, interval)
    target = _number(status.get("target_temperature_K"))
    return {"production_start_ns": start, "target_temperature_K": target,
            "npt_from_ns": _npt_from_ns(times, start),
            "means": means, "samples": len(production)}


def _npt_from_ns(times: dict[str, Any], start: float | None) -> float | None:
    """Where NPT began on the production's clock: the NVT the run planned
    after the start of the record. The live record samples every few ps, so
    its first NPT sample came that much after the change."""
    nvt, npt = _number(times.get("nvt_ns")), _number(times.get("npt_ns"))
    if start is None or nvt is None or not npt:
        return None
    return nvt - start


def _metric_rows(root: Path) -> list[dict[str, Any]]:
    try:
        with (root / "simulation" / "live_metrics.csv").open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


def _production_energies(root: Path) -> list[dict[str, Any]]:
    """The production's samples from OpenMM's ``energy.csv``, by the live
    record's names (`telemetry._read_energy_rows`)."""
    from fastmdxplora.gui.telemetry import _read_energy_rows

    try:
        return _read_energy_rows(root / "simulation" / "energy.csv")
    except Exception:  # noqa: BLE001 - the live record stands in
        return []


#: Why a density is given no mean where the box's volume was held, as the
#: thermodynamics analysis says it.
HELD_DENSITY = ("The box volume did not change over this run, so the density is a "
                "constant the setup fixed rather than something the simulation sampled.")


def _recorded_thermodynamics(root: Path) -> dict[str, Any]:
    """What the thermodynamics analysis recorded of the run, if it has."""
    try:
        found = json.loads((root / "analysis" / "thermodynamics" / "options.json").read_text(
            encoding="utf-8")).get("findings") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    record = found.get("thermodynamics") if isinstance(found, dict) else None
    return record if isinstance(record, dict) else {}


def _count(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _is_production(row: dict[str, Any]) -> bool:
    return str(row.get("stage") or "").strip().lower().startswith("production")


def _production_start_ns(rows: list[dict[str, Any]], times: dict[str, Any]) -> float | None:
    """Where production began on the simulation's clock: after the
    equilibration the run planned, where it recorded one; else the last
    time the live record wrote before its first production sample; else,
    with no equilibration recorded, at the record's start."""
    planned = _number(times.get("equilibration_planned_ns"))
    if planned is not None:
        return planned
    before = None
    for row in rows:
        if _is_production(row):
            return before if before is not None else _number(row.get("simulation_time_ns"))
        moment = _number(row.get("simulation_time_ns"))
        if moment is not None:
            before = moment
    return None


def _mean_of(values: list[float], unit: str,
             interval_ns: float | None = None) -> dict[str, Any] | None:
    """A quantity's mean over the production, as an analysis records one,
    with how much longer a withheld one needs where the samples' spacing is
    known (a study analysed without its thermodynamics recorded none, and
    "what they need" left these means out)."""
    if len(values) < 2:
        return None
    import numpy as np

    from fastmdxplora.statistics import mean_record

    record = mean_record(np.asarray(values, dtype=float), frame_interval_ns=interval_ns)
    if _number(record.get("mean")) is None:
        record["value"] = float(np.mean(values))
    said = _said(record, unit, len(values))
    if said is not None and isinstance(record.get("shortfall"), dict):
        said["shortfall"] = record["shortfall"]
    return said


def _spacing_ns(rows: list[dict[str, Any]]) -> float | None:
    """The samples' spacing on the simulation's clock, where every one has
    its time and they are evenly spaced."""
    times = [_number(row.get("simulation_time_ns")) for row in rows]
    if len(times) < 3 or any(t is None for t in times):
        return None
    steps = [b - a for a, b in zip(times, times[1:])]
    first = steps[0]
    if first <= 0 or any(abs(step - first) > 1e-6 * max(first, 1e-12) for step in steps):
        return None
    return float(first)


def _said(record: dict[str, Any], unit: str, of: int | None) -> dict[str, Any] | None:
    """A mean as an analysis records one, said with its error where it
    has one, and else why not."""
    from fastmdxplora.statistics import with_its_error

    mean = _number(record.get("mean"))
    if mean is None:
        mean = _number(record.get("value"))
    if mean is None:
        return None
    error = _number(record.get("standard_error"))
    reason = record.get("not_a_measurement")
    determined = not reason and error is not None
    said = with_its_error(mean, error) if determined else _four_figures(mean)
    if isinstance(reason, dict):
        reason = reason.get("message") or reason.get("reason") or ""
    return {
        "mean": mean, "error": error if determined else None,
        "said": f"{said} {unit}".strip(), "number": said, "determined": determined,
        "why": "" if determined else str(getattr(reason, "message", None) or reason or ""),
        "samples": _number(record.get("effective_samples")),
        "discard": int(_number(record.get("discard")) or 0), "of": of,
    }


def _four_figures(value: float) -> str:
    """A value to four significant figures, grouped: -45,472 rather than
    -4.547e+04, as every page gives it (`statistics.four_figures`)."""
    from fastmdxplora.statistics import four_figures

    return four_figures(value)


# ---------------------------------------------------------------------------
# The phases
# ---------------------------------------------------------------------------

_PHASES = ("setup", "simulation", "analysis", "report")


def _phases(root: Path) -> list[dict[str, Any]]:
    """Each phase the study recorded, in order, with its status and the
    seconds it took."""
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = [p for p in (manifest.get("phases") or []) if isinstance(p, dict) and p.get("name")]
    # A study being carried on in a piece: its Manifest still says how the
    # first piece ended ("Simulation stopped" the whole carry-on through).
    carried_on = None
    if any(root.glob("segment-*")):
        from fastmdxplora.gui.telemetry import status_as_it_stands

        going = status_as_it_stands(root)
        production = str((going.get("stage_states") or {}).get("production") or "").lower()
        if going.get("piece"):
            # Its simulation finished in the piece, while the joined
            # trajectory is analysed and reported ("Simulation stopped"
            # read until the end); or still going in it.
            carried_on = ("ok" if production == "completed" else "running"
                          if str(going.get("status") or "").lower() in ("running", "starting")
                          else None)
    order = {name: i for i, name in enumerate(_PHASES)}
    found.sort(key=lambda p: order.get(str(p.get("name")), len(order)))
    phases = []
    for phase in found:
        seconds = None
        try:
            began = datetime.fromisoformat(str(phase["started_at"]).replace("Z", "+00:00"))
            ended = datetime.fromisoformat(str(phase["finished_at"]).replace("Z", "+00:00"))
            seconds = max(0.0, (ended - began).total_seconds())
        except (KeyError, ValueError, TypeError):
            pass
        status = str(phase.get("status") or "")
        if status == "error" and str((phase.get("refusal") or {}).get("code") or "") \
                == "simulation.run.stopped":
            # Asked to stop, as the health card beside it says: it read
            # "Simulation failed" beside "Stopped".
            status = "stopped"
        if carried_on and phase.get("name") == "simulation" and status in ("stopped", "error"):
            status = carried_on
        phases.append({"name": str(phase["name"]), "status": status, "seconds": seconds})
    return phases


# ---------------------------------------------------------------------------
# The live record over the whole run, for the charts
# ---------------------------------------------------------------------------

def thinned_metrics(rows: list[dict[str, Any]], most: int) -> list[dict[str, Any]]:
    """The live record thinned evenly over the whole run to at most about
    ``most`` rows, keeping its first and last and each row where the stage
    changes, so a plot of it shows the equilibration and the whole
    production, not the newest few hundred samples alone."""
    n = len(rows)
    if n <= most or most < 2:
        return rows
    keep = {round(i * (n - 1) / (most - 1)) for i in range(most)}
    for i in range(1, n):
        if str(rows[i].get("stage") or "") != str(rows[i - 1].get("stage") or ""):
            keep.update((i - 1, i))
    return [rows[i] for i in sorted(keep)]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
