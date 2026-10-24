"""A study run until it is determined, as the Overview draws it.

`simulation.stop_when` runs a study in pieces and judges it after each
(`fastmdxplora.simulation.stopping`); `stopping.json` records every round.
The question a person watching it has is the one the rule asks: is the
answer determined yet? This reads the record into what the page draws for
each measure: the error after each round against the error asked for,
the mean with the replicas' own means beside it, and, while the error is
still above the target, the production at which it would reach it if it
keeps falling as one over the root of the frames, which is the estimate
the next piece is sized by.

Only the record is read, never recomputed, so the page and the report say
the same thing.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from fastmdxplora import record_access as _records

__all__ = ["stopping_payload"]


def _record_of(root: Path, refused: list[Path] | None = None) -> dict[str, Any] | None:
    """The study's record: in its own folder, or, for one replica's folder
    (`runs/<id>`), in the study's. Each one linked out of the study, and so
    not read (`record_access`), is added to ``refused``."""
    from fastmdxplora.refusals import OutsideWorkspace
    from fastmdxplora.simulation.stopping import RECORD

    places = [root]
    if root.parent.name == "runs":
        places.append(root.parent.parent)
    for place in places:
        try:
            # Through `record_access`, held to the study where the read is.
            record = json.loads(_records.read_text(place / RECORD, encoding="utf-8"))
        except OutsideWorkspace:
            if refused is not None:
                refused.append(place / RECORD)
            continue
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("targets"), list):
            return {"record": record, "where": place}
    return None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _asked(target: dict[str, Any], unit: str) -> str:
    if _finite(target.get("standard_error")) is not None:
        return f"±{target['standard_error']:g}" + (f" {unit}" if unit else "")
    return f"±{100 * float(target.get('relative_error') or 0):g}%"


def _said(ns: float | None) -> str:
    """A length as every page says one, in ps under 1 ns (`length_said`)."""
    from fastmdxplora.simulation.sampling_ask import length_said

    return length_said(ns) if ns is not None else ""


def _projection(point: dict[str, Any]) -> dict[str, Any] | None:
    """Where the error would reach the target, if it falls as one over the
    root of the frames kept after equilibration: the arithmetic the next
    piece was sized by (`stopping._more_for`), inverted for the kept
    production it assumed."""
    error, allowed = point.get("error"), point.get("allowed")
    more, production = point.get("more_ns"), point.get("production_ns")
    if None in (error, allowed, more, production) or point.get("agree") is False:
        return None
    if not (error > allowed > 0) or not more > 0:
        return None
    kept = more / ((error / allowed) ** 2 - 1.0)
    return {"from_ns": production, "kept_ns": kept, "error": error, "allowed": allowed,
            "to_ns": production + more, "to_said": _said(production + more)}


def _measures(record: dict[str, Any]) -> list[dict[str, Any]]:
    from fastmdxplora.gui.report_dashboard import unit_of
    from fastmdxplora.gui.series import SERIES

    rounds = record.get("rounds") if isinstance(record.get("rounds"), list) else []
    out = []
    for target in record.get("targets") or []:
        if not isinstance(target, dict) or not target.get("analysis"):
            continue
        name = str(target["analysis"])
        points = []
        unit = ""
        for number, entry in enumerate(rounds, start=1):
            verdicts = {v.get("analysis"): v for v in entry.get("verdicts") or []
                        if isinstance(v, dict)}
            v = verdicts.get(name)
            if v is None:
                continue
            unit = unit or str(v.get("unit") or "")
            value, error = _finite(v.get("value")), _finite(v.get("error"))
            points.append({
                "round": number, "production_ns": _finite(entry.get("production_ns")),
                "production_said": _said(_finite(entry.get("production_ns"))),
                "value": value, "error": error, "allowed": _finite(v.get("allowed")),
                "met": bool(v.get("met")), "agree": v.get("agree"),
                "more_ns": _finite(v.get("more_ns")),
                "measured": value is not None and error is not None,
                "unrecorded": bool(v.get("unrecorded")),
                "said": str(v.get("said") or ""),
                "replicas": [
                    {"run": str(r.get("run")), "mean": _finite(r.get("mean")),
                     "standard_error": _finite(r.get("standard_error"))}
                    for r in v.get("replicas") or [] if isinstance(r, dict)],
            })
        unit = unit or unit_of(name)
        last = points[-1] if points else None
        state = ("waiting" if last is None
                 else "met" if last["met"]
                 else "no mean" if last["unrecorded"]
                 else "disagree" if last["agree"] is False
                 else "not yet determined" if not last["measured"]
                 else "short")
        out.append({
            "analysis": name, "label": SERIES.get(name, (name.replace("_", " "), ""))[0],
            "unit": unit, "asked": _asked(target, unit),
            "relative": _finite(target.get("relative_error")) is not None,
            "points": points, "state": state,
            "projection": _projection(last) if last and last["measured"] else None,
        })
    return out


def _run_folder(where: Path, name: str, *, alone: bool = False) -> Path | None:
    """One of the record's runs: a child of the study's `runs/`, or the
    study itself for a study of one run, whatever its folder is called now
    (``alone``: the record's one run, and no `runs/` folder), as the
    Timeline finds it (`study_timeline._run_folder`). A name is data, never
    a path."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    folder = where / "runs" / name
    if folder.is_dir():
        return folder
    if alone and not (where / "runs").is_dir():
        return where
    return where if where.name == name else None


def _next_piece(record: dict[str, Any], where: Path) -> dict[str, Any] | None:
    """The piece now running, where the last round asked for one, with
    what it takes at the speed the study has run."""
    rounds = record.get("rounds") or []
    if record.get("outcome") != "running" or not rounds:
        return None
    last = rounds[-1]
    more = _finite(last.get("more_ns"))
    if last.get("decision") != "extend" or more is None:
        return None
    production = _finite(last.get("production_ns")) or 0.0
    piece = {"more_ns": more, "to_ns": production + more, "more_said": _said(more),
             "to_said": _said(production + more), "seconds": None, "platform": ""}
    runs = [str(r) for r in record.get("runs") or []]
    first = next((f for f in (_run_folder(where, r, alone=len(runs) == 1) for r in runs)
                  if f is not None), None)
    if first is not None:
        from fastmdxplora.simulation.sampling_ask import _time_here

        seconds, platform = _time_here(first, more)
        if seconds is not None:
            at_once = max(1, int(record.get("at_once") or 1))
            piece["seconds"] = seconds * math.ceil(len(runs) / at_once)
            piece["platform"] = platform
    return piece


def _ended_without_finishing(record: dict[str, Any], where: Path) -> dict[str, Any] | None:
    """A study whose record still says it is running, though the run it was
    extending ended without writing its end (its process gone, or a day of
    silence: `telemetry.status_as_it_stands`, as its header and Timeline
    read it): said stopped without finishing, never still running. Every
    run of it that ended so is named, as every page names a run
    (`exploration.run_label`, through the Timeline's list of its runs), and
    how each ended where they ended differently."""
    from fastmdxplora.gui.study_timeline import ENDED_HOW, _runs_of
    from fastmdxplora.gui.telemetry import status_as_it_stands

    if str(record.get("outcome") or "running") != "running":
        return None
    runs = [str(r) for r in record.get("runs") or []]
    places = [where] + [f for f in (_run_folder(where, r, alone=len(runs) == 1) for r in runs)
                        if f is not None and f != where]
    ended = []
    for place in places:
        try:
            status = status_as_it_stands(place, include_campaign=False)
        except Exception:  # noqa: BLE001 - said as the record says it
            continue
        if isinstance(status, dict) and str(status.get("status") or "") == "interrupted":
            ended.append((place, ENDED_HOW.get(str(status.get("ended_by") or ""), "")))
    if not ended:
        return None
    labels = {run["run_id"]: run["label"] for run in _runs_of(where) or []}
    named = [(labels.get(place.name, place.name), how) for place, how in ended if place != where]
    # How each run ended, where they ended differently; else the one way.
    hows = [how for _name, how in named if how] or [how for _place, how in ended if how]
    if len(set(hows)) > 1:
        how = "; ".join(f"in {name}, {how}" for name, how in named if how)
    else:
        how = hows[0] if hows else ""
    return {"runs": [name for name, _how in named], "how": how}


def _known_live(record: dict[str, Any], where: Path) -> bool:
    """Whether the study is known to be running now: the process record of
    the study, of one of its runs, or of the piece one is carried on in
    names a process here that is its run (`telemetry._what_its_process_says`).
    A record still saying "running" does not tell a live study from one
    killed, and a process that cannot be asked here is not known live."""
    from fastmdxplora.gui.telemetry import _what_its_process_says, read_study_status
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    if str(record.get("outcome") or "running") != "running":
        return False
    runs = [str(r) for r in record.get("runs") or []]
    places = [where] + [f for f in (_run_folder(where, r, alone=len(runs) == 1) for r in runs)
                        if f is not None and f != where]
    for place in places:
        try:
            status = read_study_status(place)
        except Exception:  # noqa: BLE001 - not known live
            status = {}
        piece = status.get("piece") if isinstance(status, dict) else None
        roots = [place]
        if isinstance(piece, str) and piece.startswith("segment-") and "/" not in piece \
                and _records.is_dir(place / piece):
            roots.insert(0, place / piece)
        for root in roots:
            try:
                process = json.loads(_records.read_text(root / RUN_PROCESS_FILE,
                                                        encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(process, dict) and _what_its_process_says(root, process) == "running":
                return True
    return False


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def stopping_payload(root: Path | str) -> dict[str, Any]:
    """What the Overview's "Running until it is determined" card draws."""
    from fastmdxplora.simulation.stopping import _OUTCOME, round_decided, study_said

    refused: list[Path] = []
    found = _record_of(Path(root), refused)
    if found is None:
        # A record linked out of the study is not read, and not said to be
        # a study of a fixed length.
        return {"ok": False, "reason": ("its record of its rounds was not read" if refused
                                        else "this study runs for a fixed length")}
    record, where = found["record"], found["where"]
    rounds = [r for r in record.get("rounds") or [] if isinstance(r, dict)]
    outcome = str(record.get("outcome") or "running")
    said = study_said(record)
    ended = _ended_without_finishing(record, where)
    shown = outcome
    if ended is not None:
        # Its record was written while it ran: said as it stands.
        shown = "interrupted"
        runs = f", in {_listed(ended['runs'])}" if ended["runs"] else ""
        if rounds:
            going = round_decided(rounds[-1], last=True, record=record)["said"]
            said = f"Stopped without finishing{runs}, in round {len(rounds)}: {going}"
        else:
            # Killed before any round was judged: in its first piece.
            said = f"Stopped without finishing in its first piece{runs}"
        said += (f"; {ended['how']}" if ended["how"] else "") + "."
    production = _finite(rounds[-1].get("production_ns")) if rounds else 0.0
    ceiling = _finite(record.get("max_duration_ns"))
    # Said running only where its process is known to be (`_known_live`):
    # otherwise as the record stood when it was written.
    live = ended is None and _known_live(record, where)
    return {
        "ok": True,
        "outcome": shown,
        "recorded_outcome": outcome,
        "live": live,
        "outcome_label": ("Stopped without finishing" if ended is not None
                          else "Running" if live
                          else _OUTCOME.get(outcome, outcome.replace("_", " "))),
        "said": said,
        "ceiling_ns": ceiling,
        "ceiling_said": _said(ceiling),
        "independent_starts": str(record.get("independent_starts") or "required"),
        "runs": [str(r) for r in record.get("runs") or []],
        "production_ns": production,
        "production_said": _said(production),
        "measures": _measures(record),
        "rounds": [{"round": n, "production_ns": _finite(r.get("production_ns")),
                    "production_said": _said(_finite(r.get("production_ns"))),
                    "decision": str(r.get("decision") or ""),
                    "more_ns": _finite(r.get("more_ns")),
                    # As the report and the Timeline say it.
                    "decided": round_decided(r, last=n == len(rounds), record=record)["said"]}
                   for n, r in enumerate(rounds, start=1)],
        "next": _next_piece(record, where) if ended is None else None,
        # Changes whenever the record does, so the page redraws only then.
        "version": f"{len(rounds)}:{shown}:{live}:{_stamp(where)}",
    }


def _stamp(where: Path) -> int:
    from fastmdxplora.simulation.stopping import RECORD

    try:
        return (where / RECORD).stat().st_mtime_ns
    except OSError:
        return 0
