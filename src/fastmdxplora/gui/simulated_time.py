"""How long a study simulated: its production first, its equilibration beside.

The page gave one number, the simulated time with equilibration included
("0.11 ns" for 100 ps of production after 5 ps of NVT and 5 of NPT), in
the card where a reader looks for how long the system was sampled, and a
card further down said "0.1 ns". The production is what the analyses
average and what a study is said to have run for; equilibration is how it
got there, said as such.

Read from what the run recorded: its plan in the live record while it runs
(the steps of each stage, written as it starts), and its own record of what
production ran once it has finished. A study recorded before the plan was
kept in the live record, still running, has its total alone, and says so.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

#: Stages before production.
EQUILIBRATING = ("loading", "minimization", "nvt", "npt")


def say_length(ns: float | None) -> str:
    """A simulated length in the unit it reads in: picoseconds under one
    nanosecond (100 ps, not 0.1 ns), nanoseconds from there; no trailing
    zeros, to a tenth of a picosecond at most."""
    if ns is None:
        return "—"
    value = float(ns)
    if abs(value) < 1.0:
        ps = round(value * 1000.0, 1)
        text = f"{ps:.1f}".rstrip("0").rstrip(".")
        return f"{text} ps"
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{text} ns"


def _record(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / "simulation" / "simulation_parameters.json").read_text(
            encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def simulated_times(root: str | Path, status: dict[str, Any]) -> dict[str, Any]:
    """The study's production and equilibration, done and planned, in ns.

    ``production_ns`` is what production has run (all of it, once the run
    has finished); ``equilibrating`` is true while the run is before its
    production. A value nobody recorded is None.
    """
    root = Path(root)
    record = _record(root)
    resolved = record.get("resolved") if isinstance(record.get("resolved"), dict) else {}
    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    dt = _number(status.get("timestep_fs")) or _number(parameters.get("timestep_fs"))

    def steps_to_ns(steps: float | None) -> float | None:
        return None if steps is None or dt is None else steps * dt / 1_000_000.0

    nvt = _number(status.get("nvt_steps_planned"))
    npt = _number(status.get("npt_steps_planned"))
    production_planned = _number(status.get("production_steps_planned"))
    if nvt is None and npt is None and production_planned is None and resolved:
        nvt, npt = _number(resolved.get("nvt_steps")), _number(resolved.get("npt_steps"))
        production_planned = _number(resolved.get("production_steps"))
    equilibration = None if nvt is None and npt is None else (nvt or 0.0) + (npt or 0.0)

    stage = str(status.get("stage") or "").lower()
    running = str(status.get("status") or "").lower() in {"running", "starting", "paused"}
    step = _number(status.get("current_step"))
    production_ns = None
    equilibration_ns = None
    actual = _number(record.get("duration_ns_actual"))
    if actual is not None and not running:
        production_ns = actual
        equilibration_ns = steps_to_ns(equilibration)
    elif step is not None and equilibration is not None:
        production_ns = steps_to_ns(max(0.0, step - equilibration))
        equilibration_ns = steps_to_ns(min(step, equilibration))
    # A study carried on in pieces ran its production across them; its own
    # record is the first piece's.
    pieces = 1
    if not running:
        from fastmdxplora.simulation.resume import extended_production

        extended = extended_production(root)
        if extended:
            production_ns, pieces = extended
    equilibrating = running and stage in EQUILIBRATING and not production_ns
    # Ended before its production began: its equilibration was not all done,
    # and saying "0 ps production after 10 ps of equilibration" said it was.
    ended_in = (stage if not running and stage in EQUILIBRATING + ("setup",)
                and not production_ns and pieces == 1 else None)
    if not running and ended_in is None and not production_ns and pieces == 1:
        # A failure in NVT was recorded at the stage "production", every
        # stage after it marked failed too: the first stage that did not
        # complete is where it ended, and "after 4 ps of equilibration (2 ps
        # NVT, 2 ps NPT)" said it had all run.
        states = status.get("stage_states") if isinstance(status.get("stage_states"), dict) else {}
        for name in ("minimization", "nvt", "npt"):
            state = str(states.get(name) or "").lower()
            if state in ("failed", "stopped", "interrupted"):
                ended_in = name
                break
            if state not in ("completed", "skipped"):
                break
    return {
        "ended_in": ended_in,
        "production_ns": production_ns,
        "production_planned_ns": steps_to_ns(production_planned),
        "equilibration_ns": equilibration_ns,
        "equilibration_planned_ns": steps_to_ns(equilibration),
        "nvt_ns": steps_to_ns(nvt),
        "npt_ns": steps_to_ns(npt),
        "equilibrating": equilibrating,
        "pieces": pieces,
        # Once it has stopped, how long its phases took, each its own time.
        "wall_s": None if running else phases_wall_seconds(root),
        "stage": stage or None,
        # What the live record says with equilibration included, for a run
        # that recorded nothing else.
        "simulated_ns": _number(status.get("simulation_time_completed_ns")),
    }


def equilibration_said(times: dict[str, Any]) -> str:
    """"after 10 ps of equilibration (5 ps NVT, 5 ps NPT)", "in 3 pieces"
    for a study carried on, or "" where there was no equilibration, or
    nothing is known of it."""
    if (times.get("pieces") or 1) > 1:
        return f"in {times['pieces']} pieces"
    if times.get("ended_in"):
        return ended_before_production(times)
    total = times.get("equilibration_planned_ns")
    if not total:
        return ""
    parts = [f"{say_length(times[key])} {name}" for key, name in (("nvt_ns", "NVT"), ("npt_ns", "NPT"))
             if times.get(key)]
    tail = f" ({', '.join(parts)})" if len(parts) > 1 else ""
    return f"after {say_length(total)} of equilibration{tail}"


#: A stage before production, as a sentence says it.
STAGE_SAID = {"setup": "setup", "loading": "setup", "minimization": "minimisation",
              "nvt": "NVT equilibration", "npt": "NPT equilibration"}


def ended_before_production(times: dict[str, Any]) -> str:
    """"ended in NVT equilibration, 2 ps of the 10 ps planned" for a run
    that ended before its production began."""
    said = f"ended in {STAGE_SAID.get(times.get('ended_in') or '', 'its preparation')}"
    done, planned = times.get("equilibration_ns"), times.get("equilibration_planned_ns")
    if planned:
        said += f", {say_length(done or 0.0)} of the {say_length(planned)} of equilibration planned"
    return said


def _when(value: Any) -> datetime:
    # Python 3.10 reads no "Z".
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _phases_of(manifest: Path) -> list[Any]:
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    phases = record.get("phases") if isinstance(record, dict) else None
    return phases if isinstance(phases, list) else []


def _phase_seconds(phases: list[Any]) -> int | None:
    seconds, timed = 0, False
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        try:
            start, finish = _when(phase["started_at"]), _when(phase["finished_at"])
            seconds += max(0, int((finish - start).total_seconds()))
            timed = True
        except (KeyError, ValueError, TypeError):
            continue
    return seconds if timed else None


def phases_wall_seconds(root: str | Path, phases: list[Any] | None = None) -> int | None:
    """How long a study's phases took, each its own time, added, with the
    pieces a study was carried on in; None where no phase was timed.

    From the first phase's start to the last one's finish it counted the
    hours between a run and its analysis run again later: 2h 57m for a run
    of 18 minutes analysed again that evening. ``phases`` are the study's
    own, where the caller holds them; else its Manifest's.
    """
    root = Path(root)
    found = [_phase_seconds(_phases_of(root / "manifest.json") if phases is None else phases)]
    try:
        from fastmdxplora.analysis.joining import survey_segments

        found += [_phase_seconds(_phases_of(piece.directory / "manifest.json"))
                  for piece in survey_segments(root)
                  if piece.directory.resolve() != root.resolve()]
    except OSError:
        pass
    timed = [seconds for seconds in found if seconds is not None]
    return sum(timed) if timed else None
