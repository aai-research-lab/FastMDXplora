"""What happened to a study, newest first, from its own records.

A study's folder holds its history in pieces: the Manifest's record of each
phase as it last ran (when, how long, by which version on which computer),
the record and the output of a phase a run again replaced
(`previous/<phase>`, :mod:`fastmdxplora.replaced`), and the pieces an
extended study was carried on in (`segment-*`). This reads them into one
list, an editor's Timeline of a file: each step with when it happened, how
long it took, what made it, and what it changed, where the records say: the
means an analysis run again moved, the settings a piece was carried on
with. Nothing is said that the records do not hold: which person or Agent
asked for a step is not recorded, so it is not said.

Every record is read through `record_access`, so the route's read held to
the study (`server._confined`) refuses one linked out of it, and names it
as not read: a mean whose record was not read is never said added or
dropped, nor a piece's settings as the study's.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastmdxplora import record_access

__all__ = ["timeline_of"]

#: What each phase is said as, done.
DONE = {"setup": "Set up", "simulation": "Simulated", "analysis": "Analysed",
        "report": "Report written"}

#: A mean is said to have moved past this fraction of itself (or of its
#: error, where it has one): a value read again to the last digit is the
#: same value.
MOVED = 1e-9


def timeline_of(root: str | Path) -> dict[str, Any]:
    """The study's steps, newest first."""
    base = Path(root)
    if not record_access.is_dir(base):
        return {"ok": False, "reason": "No study is open."}
    runs = _runs_of(base)
    if runs is not None:
        return _of_its_runs(base, runs)
    events: list[dict[str, Any]] = []
    manifest = _json(base / "manifest.json")
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    for record in phases if isinstance(phases, list) else []:
        if isinstance(record, dict) and record.get("name") in DONE:
            events.append(_phase(base, record, replaced=False))
    previous = base / "previous"
    ran_again = {e["phase"] for e in events}
    for phase in DONE:
        record = _json(previous / phase / "phase_record.json")
        if isinstance(record, dict) and record.get("name") == phase:
            event = _phase(base, record, replaced=True, again=phase in ran_again)
            events.append(event)
            if phase == "analysis":
                again = next((e for e in events if e["phase"] == "analysis" and not e["replaced"]),
                             None)
                if again is not None:
                    again["changes"] = _means_moved(previous / "analysis", base / "analysis")
                    again["again"] = True
    events += _pieces(base)
    events += _rounds(base)
    ended = _ended(base)
    if ended is not None:
        events.append(ended)
    # In the order the records give them: those without a time are said
    # newest first by it, the Manifest's last phase first.
    for order, event in enumerate(events):
        event["_order"] = order
    events.sort(key=_newest_first, reverse=True)
    for event in events:
        del event["_order"]
    return {"ok": True, "events": events}


def _newest_first(event: dict[str, Any]) -> tuple:
    """Newest first by what is known: those with a time by it, then those
    without (a Manifest may record a phase's name and status only), by the
    order their records give them. Never a time the records do not hold."""
    when = _when(event)
    if when is not None:
        return (1, *when)
    return (0, "", event.get("_order", 0))


def _when(event: dict[str, Any]) -> tuple[Any, ...] | None:
    """Where a step falls in time: by the time its record gives it; a round,
    whose record gives none, by the recorded times of the steps it came
    between, just before the pieces it began or just after the last step
    before it (`_rounds`). None where neither is recorded."""
    if event.get("at"):
        return (str(event["at"]), 1)
    placed = event.get("placed")
    if placed:
        # Rounds placed at one time are in the order they were judged, and
        # the rule's end said after its rounds (no round of its own) last.
        number = event.get("round")
        return (str(placed["at"]), 0 if placed.get("before") else 2,
                number if number is not None else math.inf)
    return None


def _runs_of(base: Path) -> list[dict[str, Any]] | None:
    """A study of several runs: each run its batch manifest plans, by its
    folder and by what sets it apart among them ("seed 1"), as every page
    names a run (`exploration.run_label`). None for a study of one run."""
    from fastmdxplora.gui.exploration import axes_that_differ, run_label

    manifest = _json(base / "batch_manifest.json")
    if not isinstance(manifest, dict):
        return None
    planned = manifest.get("planned") or manifest.get("runs")
    if not isinstance(planned, list):
        return None
    runs = [{"run_id": str(entry["run_id"]),
             "values": entry.get("sweep_values") if isinstance(entry.get("sweep_values"), dict)
             else {}}
            for entry in planned if isinstance(entry, dict) and entry.get("run_id")]
    axes = axes_that_differ(runs)
    for run in runs:
        run["label"] = run_label(run["values"], axes) or run["run_id"]
    return runs


def _of_its_runs(base: Path, runs: list[dict[str, Any]]) -> dict[str, Any]:
    """A study of several runs has no steps of its own: each run's steps,
    newest first, each named by the run that took it. Its runs are listed
    too, with how many steps each recorded, so the page can say a run that
    recorded none."""
    events: list[dict[str, Any]] = []
    listed = []
    for run in runs:
        folder = base / "runs" / run["run_id"]
        mine = timeline_of(folder) if record_access.is_dir(folder) else {"ok": False}
        steps = (mine.get("events") or []) if mine.get("ok") else []
        for event in steps:
            event["run"] = run["label"]
            event["run_path"] = str(folder)
        events += steps
        listed.append({"label": run["label"], "path": str(folder), "steps": len(steps)})
    # The study's own rounds, where it ran its runs until what it asks is
    # determined: the study's steps, named by no run.
    rounds = _rounds(base)
    events += rounds
    # Those with a time newest first; those without after them: the study's
    # rounds, newest first, as they came after its runs' steps; then run by
    # run in the study's order, each run's as its own Timeline orders them.
    timed = sorted((e for e in events if _when(e) is not None), key=_when, reverse=True)
    timeless = ([e for e in reversed(rounds) if _when(e) is None]
                + [e for e in events if _when(e) is None and e not in rounds])
    return {"ok": True, "events": timed + timeless, "runs": listed}


def _phase(base: Path, record: dict[str, Any], *, replaced: bool,
           again: bool = False) -> dict[str, Any]:
    from fastmdxplora.gui.overview_view import phase_status

    name = str(record.get("name"))
    made = record.get("produced_by") if isinstance(record.get("produced_by"), dict) else {}
    status = phase_status(record)
    started, finished = record.get("started_at"), record.get("finished_at")
    said = DONE[name] if status in ("ok", "completed", "") else f"{name.capitalize()} {status}"
    event = {
        "kind": "phase", "phase": name, "said": said, "status": status,
        "at": finished or started, "started": started,
        "seconds": _seconds(started, finished),
        "version": str(made.get("version") or ""), "host": str(made.get("host") or ""),
        "replaced": replaced, "again": False, "changes": None,
    }
    if replaced:
        # Kept by a run again, which moved it to previous/: its output is
        # read there, never as the study's. Replaced where it ran again;
        # set aside where it was written from what ran again and has not
        # been written since.
        event["said"] = said + (", then replaced" if again
                                else ", then set aside: written from what was run again")
        event["where"] = f"previous/{name}"
    detail = _detail(base / "previous" / name if replaced else base / name, name)
    if detail:
        event["detail"] = detail
    return event


def _detail(folder: Path, phase: str) -> str:
    """What a phase's record says it covered, in a few words."""
    if phase == "analysis":
        plan = _json(folder / "analysis_manifest.json")
        plan = plan.get("plan") if isinstance(plan, dict) else None
        if isinstance(plan, list) and plan:
            return f"{len(plan)} analys{'is' if len(plan) == 1 else 'es'}: " + ", ".join(
                map(str, plan[:6])) + (", ..." if len(plan) > 6 else "")
    return ""


def _means_moved(before: Path, after: Path) -> dict[str, Any]:
    """The means an analysis run again changed: each analysis in both whose
    mean moved, or became determined or not; those added and those dropped.
    One whose record was not read on either side is said not read, never
    added, dropped or kept."""
    from fastmdxplora.gui.report_dashboard import unit_of

    (old, unread_before), (new, unread_after) = _means(before), _means(after)
    not_read = sorted(unread_before | unread_after)
    for name in not_read:
        old.pop(name, None)
        new.pop(name, None)
    moved = []
    for name in sorted(set(old) & set(new)):
        a, b = old[name], new[name]
        value_a, value_b = _finite(a.get("mean")), _finite(b.get("mean"))
        error_a, error_b = _finite(a.get("standard_error")), _finite(b.get("standard_error"))
        determined_a = error_a is not None and not a.get("not_a_measurement")
        determined_b = error_b is not None and not b.get("not_a_measurement")
        scale = max(abs(value_a or 0.0), abs(value_b or 0.0), error_a or 0.0, error_b or 0.0, 1e-300)
        changed = (value_a is None) != (value_b is None) or (
            value_a is not None and abs(value_b - value_a) > MOVED * scale)
        if changed or determined_a != determined_b:
            moved.append({"analysis": name, "unit": unit_of(name, b),
                          "before": {"mean": value_a, "error": error_a if determined_a else None},
                          "after": {"mean": value_b, "error": error_b if determined_b else None}})
    return {"moved": moved, "added": sorted(set(new) - set(old)),
            "dropped": sorted(set(old) - set(new)),
            "kept": len(set(old) & set(new)) - len(moved), "not_read": not_read}


def _means(folder: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Each analysis's mean over the trajectory, as its record keeps it; and
    the analyses whose record is there but was not read."""
    found: dict[str, dict[str, Any]] = {}
    not_read: set[str] = set()
    try:
        folders = [f for f in record_access.iterdir(folder) if record_access.is_dir(f)]
    except OSError:
        return found, not_read
    for each in folders:
        path = each / "options.json"
        if os.path.lexists(path) and not record_access.can_read(path):
            not_read.add(each.name)
    try:
        records = sorted(record_access.glob(folder, "*/options.json"))
    except OSError:
        return found, not_read
    for path in records:
        record = _json(path)
        findings = record.get("findings") if isinstance(record, dict) else None
        mean = findings.get("mean") if isinstance(findings, dict) else None
        if isinstance(mean, dict):
            found[path.parent.name] = mean
    return found, not_read


def _pieces(base: Path) -> list[dict[str, Any]]:
    """Each piece an extended study was carried on in, with the settings it
    was carried on with that differ from the study's own."""
    import re

    events = []
    study = _config(base)
    for folder in sorted(record_access.glob(base, "segment-*")):
        number = re.fullmatch(r"segment-(\d+)", folder.name)
        if not number or not record_access.is_dir(folder):
            continue
        manifest = _json(folder / "manifest.json")
        phases = manifest.get("phases") if isinstance(manifest, dict) else None
        simulated = next((p for p in phases or [] if isinstance(p, dict)
                          and p.get("name") == "simulation"), None)
        if simulated is None:
            continue
        made = simulated.get("produced_by") if isinstance(simulated.get("produced_by"), dict) else {}
        started, finished = simulated.get("started_at"), simulated.get("finished_at")
        settings = []
        piece = _config(folder)
        # Its settings, or the study's, not read: said so, never as a piece
        # carried on with the study's own.
        settings_not_read = any(
            os.path.lexists(where / "resolved_config.yml")
            and not record_access.can_read(where / "resolved_config.yml")
            for where in (base, folder))
        if study is not None and piece is not None:
            from fastmdxplora.config.diff import differences
            from fastmdxplora.gui.exploration import lengths_said

            try:
                settings = [lengths_said(d.as_record()) for d in differences(study, piece)
                            if not d.where_only and not d.unrecorded
                            and d.setting not in _CARRYING][:12]
            except Exception:  # noqa: BLE001 - the step is said without them
                settings = []
        events.append({
            "kind": "piece", "phase": "simulation",
            "said": f"Carried on, piece {int(number.group(1))}",
            "status": str(simulated.get("status") or ""),
            "at": finished or started, "started": started,
            "seconds": _seconds(started, finished),
            "version": str(made.get("version") or ""), "host": str(made.get("host") or ""),
            "replaced": False, "again": False, "changes": None, "settings": settings,
            "settings_not_read": settings_not_read, "where": folder.name,
        })
    return events


#: What carrying a study on writes into its piece's settings, the same for
#: every piece: where it was prepared and resumes from, no minimisation or
#: equilibration again, and only the simulation run. Not what the piece was
#: carried on with.
_CARRYING = frozenset({
    "include_phase", "simulation.setup_from", "simulation.prepared_from",
    "simulation.resume_from", "simulation.resume_unsealed", "simulation.minimize",
    "simulation.nvt_steps", "simulation.npt_steps", "simulation.nvt_duration_ns",
    "simulation.npt_duration_ns",
})


#: How a run that ended without writing its end is known to have ended
#: (`telemetry.status_as_it_stands`'s ``ended_by``), as the pages say it.
ENDED_HOW = {"process": "its process is gone, and it wrote no end",
             "silence": "nothing written for a day, and no end written"}


def _ended(base: Path) -> dict[str, Any] | None:
    """A run that stopped without finishing: its live record says it was
    going, and it is known to have ended without writing its end (its
    process gone, or a day of silence: `telemetry.status_as_it_stands`).
    Said as stopped without finishing, at the last time it wrote, with how
    far it had got; never as running, nor as the step before it finished.
    Its own run alone is read, never its study's records outside it."""
    from fastmdxplora.gui.telemetry import status_as_it_stands

    try:
        status = status_as_it_stands(base, include_campaign=False)
    except Exception:  # noqa: BLE001 - the list stands without it
        return None
    if not isinstance(status, dict) or str(status.get("status") or "") != "interrupted":
        return None
    stage = str(status.get("stage") or "")
    step, total = status.get("current_step"), status.get("total_planned_steps")
    import re

    piece = status.get("piece") if isinstance(status.get("piece"), str) else ""
    # A piece as every other step says it: "piece 1", not its folder's name.
    number = re.fullmatch(r"segment-(\d+)", piece)
    where = ((f"in piece {int(number.group(1))}'s " if number else "in ") + stage
             if stage else "")
    if (where and isinstance(step, (int, float)) and isinstance(total, (int, float))
            and not isinstance(step, bool) and total > 0):
        where += f", at step {int(step):,} of {int(total):,}"
    how = ENDED_HOW.get(str(status.get("ended_by") or ""), "")
    return {"kind": "ended", "phase": "simulation" if stage else "",
            "said": "Stopped without finishing" + (f", {where}" if where else ""),
            "status": "interrupted", "at": status.get("last_update_timestamp") or None,
            "started": None, "seconds": None, "version": "", "host": "",
            "replaced": False, "again": False, "changes": None,
            "detail": how + ("; the time is when it last wrote" if how else "")}


def _rounds(base: Path) -> list[dict[str, Any]]:
    """Each round of a study run until what it asks is determined
    (`simulation.stop_when`), as its record (`stopping.json`) gives it:
    the production judged, what was decided, and what stopped it, from
    what the record holds; "not recorded" where it holds no reason.

    A round's record gives no time. It is placed by the recorded times of
    the steps it came between: just before the pieces it began (each run's
    piece of the length the round asked for, the next in the run's order),
    or, where it began none, just after the last step before the pieces a
    later round began: the analyses and report it judged, which each piece
    is followed by (`resume.extend_study`). A study of one run writes its
    report again once its rounds end (`explorer._report_again`), so its
    report is not a step before its last round unless its record still
    says it is running."""
    from fastmdxplora.simulation.sampling_ask import length_said
    from fastmdxplora.simulation.stopping import RECORD, record_said, study_ended

    record = _json(base / RECORD)
    if not isinstance(record, dict) or not isinstance(record.get("targets"), list):
        return []
    rounds = record.get("rounds") if isinstance(record.get("rounds"), list) else []
    runs = [str(name) for name in record.get("runs") or [] if isinstance(name, (str, int))]
    folders = [folder for folder in (_run_folder(base, name, alone=len(runs) == 1)
                                     for name in runs) if folder]
    # Each run's own steps, as its Manifest last recorded them, and its
    # pieces in order.
    steps = [step for folder in folders for step in _steps_of(folder)]
    pieces = {folder: _timed_pieces(folder) for folder in folders}
    times = steps + [(time, "piece") for each in pieces.values() for piece in each
                     for time in (piece["started"], piece["finished"]) if time]
    taken = {folder: 0 for folder in folders}
    said = str(record.get("said") or "")
    began_by: list[list[str]] = []
    for entry in rounds:
        entry = entry if isinstance(entry, dict) else {}
        more = _finite(entry.get("more_ns"))
        began: list[dict[str, Any]] = []
        if more is not None and more > 0:
            for folder in folders:
                found = next((i for i in range(taken[folder], len(pieces[folder]))
                              if _same_length(pieces[folder][i]["duration_ns"], more)), None)
                if found is not None:
                    began.append(pieces[folder][found])
                    taken[folder] = found + 1
        began_by.append([p["started"] for p in began if p["started"]])
    written_again = len(runs) == 1 and str(record.get("outcome") or "") != "running"

    def after_the_steps_before(later: list[list[str]], *, last: bool) -> dict[str, Any] | None:
        bound = min((t for starts in later for t in starts), default=None)
        before = [str(time) for time, phase in times
                  if (bound is None or str(time) < str(bound))
                  and not (last and written_again and phase == "report")]
        return {"at": max(before), "before": False} if before else None

    events: list[dict[str, Any]] = []
    for number, entry in enumerate(rounds, start=1):
        entry = entry if isinstance(entry, dict) else {}
        last = number == len(rounds)
        starts = began_by[number - 1]
        placed = ({"at": min(starts), "before": True} if starts
                  else after_the_steps_before(began_by[number:], last=last))
        event = _round(number, entry, record, last=last)
        event["placed"] = placed
        production = _finite(entry.get("production_ns"))
        event["production_said"] = length_said(production) if production is not None else ""
        events.append(event)
    ended = study_ended(record) if said else ""
    if ended:
        # No round judged since the study was started or resumed: why, as
        # the record says it, the study's own sentence and none of its
        # rounds'.
        asked = ended == "asked"
        again = f"resumed after round {len(rounds)}, then " if rounds else ""
        events.append({
            **_step("round", "stopping", "stopped" if asked else "not_applied"),
            "said": "The stopping rule: " + again + (
                "stopped when asked (Ctrl+C or a signal), before it was "
                + ("applied again" if rounds else "first applied") if asked
                else "not applied" + (" again" if rounds else "")),
            "detail": record_said(said), "stopped_by": "asked" if asked else "",
            "placed": after_the_steps_before([], last=False),
            "production_said": "", "round": None})
    return events


def _round(number: int, entry: dict[str, Any], record: dict[str, Any], *,
           last: bool) -> dict[str, Any]:
    """A round said by what was decided on it, as the report says it
    (`stopping.round_decided`): a stopped one by what stopped it, an
    analysis that recorded no mean, a request to stop, or an extension that
    failed, and "not recorded" where the record holds none of these; the
    last, still said running, as extending, never as done."""
    from fastmdxplora.simulation.stopping import round_decided

    verdicts = [v for v in entry.get("verdicts") or [] if isinstance(v, dict)]
    detail = "; ".join(str(v.get("said")) for v in verdicts if v.get("said"))
    decided = round_decided(entry, last=last, record=record)
    if decided["theirs"] and not decided["going"]:
        # The round's own sentence, never the study's.
        detail = decided["sentence"] or detail
    return {**_step("round", "stopping", str(entry.get("decision") or "")),
            "said": f"Round {number}: {decided['said']}", "detail": detail,
            "stopped_by": decided["stopped_by"], "going": decided["going"], "round": number}


def _step(kind: str, phase: str, status: str) -> dict[str, Any]:
    """What every step of the list carries, none of it recorded for this
    one: no time, length, version or computer of its own."""
    return {"kind": kind, "phase": phase, "status": status, "at": None, "started": None,
            "seconds": None, "version": "", "host": "", "replaced": False, "again": False,
            "changes": None, "where": "stopping.json"}


def _run_folder(base: Path, name: str, *, alone: bool = False) -> Path | None:
    """One of the record's runs: a child of the study's `runs/`, or the
    study itself for a study of one run. The record names a study of one
    run by its folder's name when it was written, so with the record's one
    run (``alone``) and no `runs/` folder the study's folder is the run's,
    whatever it is called now (renamed, copied, or unpacked under another
    name). A name is data, never a path."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    folder = base / "runs" / name
    if record_access.is_dir(folder):
        return folder
    if alone and not record_access.is_dir(base / "runs"):
        return base
    return base if base.name == name else None


def _steps_of(folder: Path) -> list[tuple[str, str]]:
    """When a run's own simulation, analyses and report began and ended, as
    its Manifest last recorded them, each with its phase."""
    manifest = _json(folder / "manifest.json")
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    return [(str(p[key]), str(p["name"])) for p in phases or [] if isinstance(p, dict)
            and p.get("name") in ("simulation", "analysis", "report")
            for key in ("started_at", "finished_at") if p.get(key)]


def _timed_pieces(folder: Path) -> list[dict[str, Any]]:
    """A run's pieces in the order they were made, each with when its
    simulation began and ended and the length it was asked to run."""
    import re

    found = []
    for piece in record_access.glob(folder, "segment-*"):
        number = re.fullmatch(r"segment-(\d+)", piece.name)
        if not number or not record_access.is_dir(piece):
            continue
        manifest = _json(piece / "manifest.json")
        phases = manifest.get("phases") if isinstance(manifest, dict) else None
        simulated = next((p for p in phases or [] if isinstance(p, dict)
                          and p.get("name") == "simulation"), None) or {}
        config = _config(piece) or {}
        simulation = config.get("simulation") if isinstance(config.get("simulation"), dict) else {}
        found.append({"index": int(number.group(1)),
                      "started": simulated.get("started_at") or None,
                      "finished": simulated.get("finished_at") or None,
                      "duration_ns": _finite(simulation.get("duration_ns"))})
    return sorted(found, key=lambda p: p["index"])


def _same_length(duration: float | None, asked: float) -> bool:
    """A piece's length is the round's ask, as the piece was written with
    it (rounded to a millionth of a nanosecond, `resume.continuation_of`)."""
    return duration is not None and abs(duration - round(asked, 6)) <= 1e-6 * max(1.0, asked)


def _config(folder: Path) -> dict[str, Any] | None:
    from fastmdxplora.config.diff import config_of

    try:
        config, _ = config_of(folder)
    except Exception:  # noqa: BLE001 - none recorded
        return None
    return config if isinstance(config, dict) else None


def _seconds(started: Any, finished: Any) -> float | None:
    try:
        a = datetime.fromisoformat(str(started))
        b = datetime.fromisoformat(str(finished))
    except (TypeError, ValueError):
        return None
    seconds = (b - a).total_seconds()
    return seconds if seconds >= 0 else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _json(path: Path) -> Any:
    try:
        return json.loads(record_access.read_text(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
