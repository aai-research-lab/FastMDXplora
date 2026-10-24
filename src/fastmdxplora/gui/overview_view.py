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

from fastmdxplora import record_access as _records

__all__ = ["overview_payload", "THERMODYNAMICS", "MOST_TILES", "thinned_metrics", "phase_status"]

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

_CACHE: dict[tuple[str, bool], tuple[tuple, dict[str, Any], frozenset[str]]] = {}
_LOCK = threading.Lock()


def overview_payload(root: str | Path, *, refused: set[str] | None = None) -> dict[str, Any]:
    """The Overview's results, clock, thermodynamic means and phases.

    ``refused`` is the set a confined read names the records it refused in
    (`record_access`); a payload kept from an earlier read names its own
    there again, as no record is read for it."""
    root = Path(root)
    key = _stamp(root)
    # A payload read confined is kept apart from one read without.
    place = (str(root), refused is not None)
    before = set(refused or ())
    with _LOCK:
        kept = _CACHE.get(place)
        if kept and kept[0] == key:
            if refused is not None:
                refused.update(kept[2])
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
        _CACHE[place] = (key, payload, frozenset(set(refused or ()) - before))
    return payload


def _production_speed(root: Path) -> float | None:
    """The speed production ran at, from its own steps and times, as the
    fix card prices a carry-on: the line beside it gave the live record's
    average from the run's start, setup and minimisation in it, half the
    price's."""
    import re

    from fastmdxplora.remedies import _speed

    # The last piece run, by its number: by name, a piece set aside
    # (`segment-002-stopped-before-its-checkpoint`) lists after the one run
    # in its place, and `segment-010` before `segment-9`. Read from the
    # folders' names, as this is asked at every poll of the Overview.
    pieces = []
    for folder in _records.glob(root, "segment-*"):
        number = re.fullmatch(r"segment-(\d+)", folder.name)
        if number and _records.is_file(folder / "simulation" / "live_status.json"):
            pieces.append((int(number.group(1)), folder))
    folder = max(pieces)[1] if pieces else root
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
    try:
        paths += sorted(_records.glob(root, "segment-*/simulation/live_status.json"))
        paths += sorted(_records.glob(root, "segment-*/simulation/live_metrics.csv"))
        paths += sorted(_records.glob(root / "analysis", "*/options.json"))
    except OSError:
        pass
    stamp = []
    for path in paths:
        try:
            seen = _records.stat(path)
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
        return {"tiles": [], "more": 0, "means": 0, "determined": 0, "not_read": 0,
                "analyses": 0, "judged_earlier": []}
    rows = overview.get("rows") or []
    tiles: list[dict[str, Any]] = []
    # A mean whose record was not read is neither determined nor not: it
    # is counted apart, as not read.
    means = determined = not_read = 0
    for row in rows:
        name = row.get("analysis")
        if name == "rmsf":
            refused = [q for q in row.get("quantities") or []
                       if isinstance(q, dict) and q.get("unavailable")]
            data = root / "analysis" / "rmsf" / "rmsf.dat"
            if not refused and not _records.can_read(data):
                from fastmdxplora.gui.series import refused_said

                refused = [{"why": refused_said(root, data) + "."}]
            if refused:
                not_read += 1
                tiles.append(_not_read(row, refused[0]))
                continue
            rmsf = _rmsf(root)
            if rmsf:
                tiles.append({**rmsf, "anchor": row.get("anchor") or "rmsf"})
            continue
        for quantity in row.get("quantities") or []:
            if isinstance(quantity, dict) and quantity.get("unavailable"):
                # Its record was refused (linked out of the study): said
                # so, not left out as though the analysis recorded none.
                not_read += 1
                tiles.append(_not_read(row, quantity))
                continue
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
                "reason": _in_a_line(quantity.get("why") or ""),
                "samples": quantity.get("samples"),
                "from_ns": quantity.get("from_ns"),
                "reweighted": bool(quantity.get("reweighted")),
                "series": series,
                # Its image note beside the verdict, which it does not change,
                # and the analysis's paragraph of it on hover.
                "noted": quantity.get("noted") or "",
                "noted_why": quantity.get("noted_why") or "",
            })
    shown = tiles[:MOST_TILES]
    # The means not shown, as the heading counts means: the RMSF tile is
    # not one, and "11 more not shown" was said of ten.
    more = sum(1 for tile in tiles[MOST_TILES:] if tile.get("kind") == "mean")
    return {"tiles": shown, "more": more, "means": means,
            "determined": determined, "not_read": not_read, "analyses": len(rows),
            "biased": bool(overview.get("biased")),
            "judged_earlier": list(overview.get("judged_earlier") or []),
            "lead": _lead(root, means, determined, not_read)}


def _in_a_line(why: str) -> str:
    """Why a mean was not determined, in a line for its tile; the whole is
    there on hover. The two reasons a run too short gives are said by what
    they come to; any other by its first sentence."""
    import re

    text = " ".join(str(why or "").split())
    if not text:
        return ""
    fewer = re.match(r"[\d.]+ independent samples? in \d+ frames.*?Below (\d+)", text)
    if fewer:
        return f"Fewer than {fewer.group(1)} independent samples: the run has to be longer."
    if text.startswith("This run is not long against its own correlation time"):
        return "Short against its own correlation time: a longer run, or replicas."
    first = re.match(r"(.+?[.;:])(\s|$)", text)
    line = first.group(1) if first else text
    return line[:-1] + "." if line.endswith((";", ":")) else line


def _lead(root: Path, means: int, determined: int, not_read: int = 0) -> dict[str, Any] | None:
    """The sentence the Overview of one run leads with: how many of its
    means are determined and, where they are not, how much longer the run
    would have to be and what that costs here, as the analyses asked for it
    (:func:`fastmdxplora.simulation.sampling_ask.sampling_asked_for`: the
    largest ask, since a run long enough for the slowest mean is long enough
    for the rest, at this run's own speed). Where the records do not say
    how much longer, that is said, never guessed; a result whose record
    was not read is counted apart, and a record the ask was not read from
    named (`record_access`)."""
    unread = ("" if not not_read else
              f" {not_read} result{' was' if not_read == 1 else 's were'} not read: "
              f"{'it says' if not_read == 1 else 'each says'} why below.")
    if not means:
        if not unread:
            return None
        return {"said": unread.strip(), "undetermined": 0, "means": 0, "more_ns": None,
                "seconds": None, "platform": "", "lower_bound": False}
    from fastmdxplora.image_note import determined_said, longer_run_said, recorded_finding

    if determined >= means:
        said = ("Its mean is determined." if means == 1
                else f"All {means} means are determined.")
        # Determined, and still biased where the solute met its own image
        # in every frame: never said determined alone.
        image = determined_said(recorded_finding(root), means)
        said += f" {image}" if image else ""
        return {"said": said + unread, "undetermined": 0, "means": means, "more_ns": None,
                "seconds": None, "platform": "", "lower_bound": False}
    from fastmdxplora import record_access
    from fastmdxplora.simulation.sampling_ask import (duration_said, length_said,
                                                      sampling_asked_for)

    undetermined = means - determined
    counted = ("Its mean is not determined" if means == 1
               else "Neither mean is determined" if means == 2 and not determined
               else f"None of its {means} means is determined" if not determined
               else f"{undetermined} of {means} means "
                    f"{'is' if undetermined == 1 else 'are'} not determined")
    before = record_access.refused()
    try:
        ask = sampling_asked_for(root)
    except Exception:  # noqa: BLE001 - said as not known, never guessed
        ask = None
    # A record the ask would be read from, refused: said so, never as a
    # record the run does not have. The analyses' own records are looked
    # at whether or not an earlier read (the tiles') refused them first.
    refused_now = record_access.refused() - before
    refused = sorted(refused_now | {name for name in _analyses_not_read(root)
                                    if not any(seen.endswith(name) for seen in refused_now)})
    them = "it" if undetermined == 1 else "them"
    if ask is None:
        why = (f"is not said: {', '.join(refused)} {'was' if len(refused) == 1 else 'were'} "
               f"not read." if refused else "is not in the records: each says why below.")
        lifted = longer_run_said(recorded_finding(root))
        why += f" {lifted}" if lifted else ""
        return {"said": f"{counted}. How much longer would determine {them} {why}" + unread,
                "undetermined": undetermined, "means": means, "more_ns": None,
                "seconds": None, "platform": "", "lower_bound": False}
    asking = len(ask.analyses)
    who = them if asking >= undetermined \
        else f"the {asking} withheld for want of sampling"
    amount = ("at least " if ask.lower_bound else "about ") + length_said(ask.more_ns)
    said = f"{counted}: {amount} more production should determine {who}"
    if ask.seconds is not None:
        on = f" on {ask.platform}" if ask.platform else ""
        said += f", {duration_said(ask.seconds)} at this run's speed{on}"
    said += "."
    if ask.lower_bound:
        said += (" A floor: the run is too short to resolve its own correlation time, "
                 "so run that much and estimate again.")
    if refused:
        said += (f" {', '.join(refused)} {'was' if len(refused) == 1 else 'were'} not read, "
                 f"so what {'it says' if len(refused) == 1 else 'they say'} is not counted "
                 f"here.")
    # Not a longer run alone, where the solute met its image in every frame.
    lifted = longer_run_said(recorded_finding(root))
    said += (f" {lifted}" if lifted else "") + unread
    return {"said": said, "undetermined": undetermined, "means": means,
            "more_ns": ask.more_ns, "seconds": ask.seconds, "platform": ask.platform,
            "lower_bound": ask.lower_bound}


def _analyses_not_read(root: Path) -> set[str]:
    """The analyses' records of the run that a held read refuses, named in
    the run (``analysis/rmsd/options.json``); none outside a held read."""
    import os

    from fastmdxplora import record_access

    folder = root / "analysis"
    try:
        names = [entry.name for entry in record_access.iterdir(folder)]
    except OSError:
        return set()
    refused = set()
    for name in names:
        record = folder / name / "options.json"
        if os.path.lexists(record) and not record_access.can_read(record):
            refused.add(f"analysis/{name}/options.json")
    return refused


def _not_read(row: dict[str, Any], quantity: dict[str, Any]) -> dict[str, Any]:
    """The tile of a mean whose record a confined read refused: whether it
    was determined is not known (``determined`` None)."""
    name = row.get("analysis")
    return {"kind": "mean", "analysis": name, "anchor": row.get("anchor") or name,
            "label": quantity.get("label") or row.get("title") or name,
            "said": "record unavailable", "unit": "", "determined": None,
            "why": quantity.get("why") or "", "samples": None, "from_ns": None,
            "reweighted": False, "series": None, "unavailable": True}


def _rmsf(root: Path) -> dict[str, Any] | None:
    """RMSF over its residues (or atoms): their mean, range and values; it
    is one value a residue, not a series over time, so it has no error."""
    folder = root / "analysis" / "rmsf"
    values: list[float] = []
    try:
        for line in _records.read_text(folder / "rmsf.dat", encoding="utf-8").splitlines():
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
        asked = (json.loads(_records.read_text(folder / "options.json", encoding="utf-8"))
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
    # The thermodynamics analysis's record refused: its means were not
    # read, and are not taken from the energy file in its place (refused
    # is not missing).
    not_read: list[str] = []
    if isinstance(recorded, NotRead):
        not_read, recorded = list(recorded.names), {}
    # Production's own energy file, where it wrote one: the series the
    # thermodynamics analysis and the report's convergence table average,
    # so a mean the analysis did not record is the report's, not one taken
    # from the live record's coarser samples, which gave another number.
    energies = _production_energies(root)
    # Records of the study's pieces this read refused: the means over its
    # production were not read, which is not the means of the piece read.
    if isinstance(energies, NotRead):
        not_read, energies = not_read + energies.names, None
    # A study carried on in pieces with a piece's record missing has no
    # means of its energy (the thermodynamics analysis refuses it), not the
    # live record's.
    averaged = [] if energies is None else (energies or production)
    volumes = [v for v in (_number(row.get("volume")) for row in averaged) if v is not None]
    held = len(volumes) > 1 and max(volumes) - min(volumes) <= 1e-9 * max(abs(max(volumes)), 1e-30)
    means: dict[str, Any] = {}
    interval = _spacing_ns(averaged)
    # A quantity the analysis ran on and gave no mean for, with why ("2
    # usable frame(s): there is nothing to average"): not one unrecorded.
    no_mean: dict[str, str] = {}
    for key, _label, unit in THERMODYNAMICS:
        entry = recorded.get(key)
        if isinstance(entry, dict) and ("mean" in entry or "not_a_measurement" in entry):
            means[key] = _said(entry, unit, _count(recorded.get("samples")))
            if means[key] is not None:
                # Its image note beside its verdict, as every page says it.
                from fastmdxplora.image_note import note_of, paragraph_beside, said_short

                short = said_short(note_of(entry))
                means[key]["noted"] = short or None
                # The analysis's paragraph, said as its where it counts the
                # union of the rows, not this mean's.
                means[key]["noted_why"] = (paragraph_beside(note_of(entry), {
                    "solute_image": recorded.get("solute_image_of_analysis")}) or None
                    if short else None)
            if means[key] is None and entry.get("not_a_measurement"):
                why = entry["not_a_measurement"]
                if isinstance(why, dict):
                    why = why.get("message") or why.get("reason") or ""
                no_mean[key] = str(why or "not determined")
            continue
        if not_read:
            means[key] = None
            continue
        values = [v for v in (_number(row.get(key)) for row in averaged) if v is not None]
        if key == "density" and held and len(values) > 1:
            means[key] = _said({"value": sum(values) / len(values),
                                "not_a_measurement": HELD_DENSITY}, unit, len(values))
            continue
        means[key] = _mean_of(values, unit, interval)
    target = _number(status.get("target_temperature_K"))
    said = {"production_start_ns": start, "target_temperature_K": target,
            "npt_from_ns": _npt_from_ns(times, start),
            "means": means, "samples": len(production)}
    if no_mean:
        said["no_mean"] = no_mean
    if not_read and any(value is None for value in means.values()):
        # Named for the card to say its means were not read, and why.
        said["not_read"] = not_read
    return said


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
        with _records.open_text(root / "simulation" / "live_metrics.csv", newline="",
                                encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


class NotRead:
    """Records a held read refused (`record_access`), named from the study:
    what they would have given was not read, not found missing."""

    def __init__(self, names: list[str]) -> None:
        self.names = names


def _refused_names(root: Path, refusal: OSError) -> list[str]:
    """The record a refusal names, from the study ``root``."""
    named = getattr(refusal, "filename", None)
    if named:
        try:
            return [Path(named).absolute().relative_to(root.absolute()).as_posix()]
        except ValueError:
            pass
    return ["joined/joined.json"]


def _production_energies(root: Path) -> list[dict[str, Any]] | NotRead | None:
    """The production's samples from OpenMM's ``energy.csv``, by the live
    record's names (`telemetry._read_energy_rows`); None where a study
    carried on in pieces lacks a piece's record, and `NotRead` where a
    record of its join or its pieces was refused (a joined study whose
    record of the join is not read is not one run)."""
    from fastmdxplora.analysis.thermodynamics import state_record_pieces
    from fastmdxplora.gui.telemetry import _read_energy_rows
    from fastmdxplora.refusals import MissingResultError, OutsideWorkspace
    from fastmdxplora.simulation.pipeline import _refuse

    try:
        pieces = state_record_pieces(root)
    except OutsideWorkspace as refusal:
        return NotRead(_refused_names(root, refusal))
    except MissingResultError:
        return None
    except Exception:  # noqa: BLE001 - an unreadable piece: no means
        return None
    try:
        if not pieces:
            # Refused, it is not read, and the live record's coarser samples
            # do not stand in for it.
            _refuse(root / "simulation" / "energy.csv")
            return _read_energy_rows(root / "simulation" / "energy.csv")
        # A study carried on in pieces: every piece, as the thermodynamics
        # analysis reads them, not the first piece alone.
        rows: list[dict[str, Any]] = []
        for piece in pieces:
            for row in _read_energy_rows(piece["path"]):
                step = _number(row.get("step"))
                if (piece["last_step"] is not None and step is not None
                        and step > piece["last_step"]):
                    continue
                if step is not None:
                    row["step"] = str(step + piece["steps_before"])
                time_ns = _number(row.get("simulation_time_ns"))
                if time_ns is not None:
                    row["simulation_time_ns"] = str(time_ns + piece["ps_before"] / 1000.0)
                rows.append(row)
        return rows
    except OutsideWorkspace as refusal:
        return NotRead(_refused_names(root, refusal))
    except Exception:  # noqa: BLE001 - the live record stands in for one run
        return None if pieces else []


#: Why a density is given no mean where the box's volume was held, as the
#: thermodynamics analysis says it.
HELD_DENSITY = ("The box volume did not change over this run, so the density is a "
                "constant the setup fixed rather than something the simulation sampled.")


def _recorded_thermodynamics(root: Path) -> dict[str, Any] | NotRead:
    """What the thermodynamics analysis recorded of the run, if it has;
    `NotRead` where its record is there and a held read refused it (a link
    out of the study), which is not a run without it."""
    from fastmdxplora.refusals import OutsideWorkspace

    path = root / "analysis" / "thermodynamics" / "options.json"
    try:
        found = json.loads(_records.read_text(path, encoding="utf-8")).get("findings") or {}
    except OutsideWorkspace as refusal:
        return NotRead(_refused_names(root, refusal) if getattr(refusal, "filename", None)
                       else ["analysis/thermodynamics/options.json"])
    except (OSError, ValueError, AttributeError):
        return {}
    record = found.get("thermodynamics") if isinstance(found, dict) else None
    if not isinstance(record, dict):
        return {}
    from fastmdxplora.image_note import paragraph_of

    # The analysis's paragraph of its image note, said where a mean's is.
    return {**record, "solute_image_said": paragraph_of(found) or None,
            "solute_image_of_analysis": found.get("solute_image")}


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


def phase_status(phase: dict[str, Any]) -> str:
    """A phase's status as its Manifest records it, "stopped" where it
    ended in error by its own refusal code for a run asked to stop: as the
    health card beside it says, where it read "Simulation failed" beside
    "Stopped". Every page that says a phase reads it here."""
    status = str(phase.get("status") or "")
    refusal = phase.get("refusal") if isinstance(phase.get("refusal"), dict) else {}
    if status == "error" and str(refusal.get("code") or "") == "simulation.run.stopped":
        return "stopped"
    return status


def _phases(root: Path) -> list[dict[str, Any]]:
    """Each phase the study recorded, in order, with its status and the
    seconds it took."""
    try:
        manifest = json.loads(_records.read_text(root / "manifest.json", encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = [p for p in (manifest.get("phases") or []) if isinstance(p, dict) and p.get("name")]
    # A study being carried on in a piece: its Manifest still says how the
    # first piece ended ("Simulation stopped" the whole carry-on through).
    carried_on = None
    waiting = False
    if any(_records.glob(root, "segment-*")):
        from fastmdxplora.gui.telemetry import status_as_it_stands

        going = status_as_it_stands(root)
        production = str((going.get("stage_states") or {}).get("production") or "").lower()
        if going.get("between_rounds"):
            # Its study extends it again in its next round, then analyses and
            # reports it again: done so far, not done.
            waiting = True
        elif going.get("piece"):
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
        status = phase_status(phase)
        if carried_on and phase.get("name") == "simulation" and status in ("stopped", "error"):
            status = carried_on
        if waiting and phase.get("name") in ("simulation", "analysis", "report"):
            status = "waiting"
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
