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

__all__ = ["stopping_payload"]


def _record_of(root: Path) -> dict[str, Any] | None:
    """The study's record: in its own folder, or, for one replica's folder
    (`runs/<id>`), in the study's."""
    from fastmdxplora.simulation.stopping import RECORD

    places = [root]
    if root.parent.name == "runs":
        places.append(root.parent.parent)
    for place in places:
        try:
            record = json.loads((place / RECORD).read_text(encoding="utf-8"))
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
            "to_ns": production + more}


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
                 else "not yet a measurement" if not last["measured"]
                 else "short")
        out.append({
            "analysis": name, "label": SERIES.get(name, (name.replace("_", " "), ""))[0],
            "unit": unit, "asked": _asked(target, unit),
            "relative": _finite(target.get("relative_error")) is not None,
            "points": points, "state": state,
            "projection": _projection(last) if last and last["measured"] else None,
        })
    return out


def _run_folder(where: Path, name: str) -> Path | None:
    """One of the record's runs: a child of the study's `runs/`, or the
    study itself for a study of one run. A name is data, never a path."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    folder = where / "runs" / name
    if folder.is_dir():
        return folder
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
    piece = {"more_ns": more, "to_ns": production + more, "seconds": None, "platform": ""}
    runs = [str(r) for r in record.get("runs") or []]
    first = next((f for f in (_run_folder(where, r) for r in runs) if f is not None), None)
    if first is not None:
        from fastmdxplora.simulation.sampling_ask import _time_here

        seconds, platform = _time_here(first, more)
        if seconds is not None:
            at_once = max(1, int(record.get("at_once") or 1))
            piece["seconds"] = seconds * math.ceil(len(runs) / at_once)
            piece["platform"] = platform
    return piece


def stopping_payload(root: Path | str) -> dict[str, Any]:
    """What the Overview's "Running until it is determined" card draws."""
    from fastmdxplora.simulation.stopping import _OUTCOME

    found = _record_of(Path(root))
    if found is None:
        return {"ok": False, "reason": "this study runs for a fixed length"}
    record, where = found["record"], found["where"]
    rounds = [r for r in record.get("rounds") or [] if isinstance(r, dict)]
    outcome = str(record.get("outcome") or "running")
    return {
        "ok": True,
        "outcome": outcome,
        "outcome_label": _OUTCOME.get(outcome, outcome.replace("_", " ")),
        "said": str(record.get("said") or ""),
        "ceiling_ns": _finite(record.get("max_duration_ns")),
        "independent_starts": str(record.get("independent_starts") or "required"),
        "runs": [str(r) for r in record.get("runs") or []],
        "production_ns": _finite(rounds[-1].get("production_ns")) if rounds else 0.0,
        "measures": _measures(record),
        "rounds": [{"round": n, "production_ns": _finite(r.get("production_ns")),
                    "decision": str(r.get("decision") or ""),
                    "more_ns": _finite(r.get("more_ns"))}
                   for n, r in enumerate(rounds, start=1)],
        "next": _next_piece(record, where),
        # Changes whenever the record does, so the page redraws only then.
        "version": f"{len(rounds)}:{outcome}:{_stamp(where)}",
    }


def _stamp(where: Path) -> int:
    from fastmdxplora.simulation.stopping import RECORD

    try:
        return (where / RECORD).stat().st_mtime_ns
    except OSError:
        return 0
