"""What the Viewer's timeline marks on the frames of the run it plays.

The timeline under the Viewer's molecule (viewer-timeline.js) marks the
moments a person may want to go to: each cluster's medoid, the frame its
clustering points to as the one most like the rest of its cluster; and each
time a study run until it is determined judged it (`stopping.json`), at the
production it had reached. The series under it mark where each analysis
began averaging; those come with the series (`/api/series`).

Only records are read: a medoid's frame as the clustering wrote it in its
populations table, a judgement's production as the stopping record keeps
it. Frames are the trajectory's own numbers, as `/api/frames-info` gives
them; a time is production's clock. The table numbers a medoid among the
frames analysed, so it is mapped as the figures map it (`figure_data._clock`,
an analysis's stride and first frame). Every record is read through
`record_access`, so `/api/timeline` holds its reads to the study.
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any

from fastmdxplora import record_access as _records

__all__ = ["timeline_payload"]

#: The clustering's tables of populations, one for each method it ran.
_POPULATIONS = re.compile(r"cluster_(?P<method>[a-z0-9_]+)_populations\.csv")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _share(share: float) -> str:
    """A cluster's share of the frames: one place below 1%, so a cluster of
    one frame in two hundred is not said none of them."""
    if share >= 0.01 or share == 0:
        return f"{share:.0%}"
    return f"{share:.1%}" if share >= 0.001 else "under 0.1%"


def _medoids(root: Path) -> list[dict[str, Any]]:
    from fastmdxplora.gui.figure_data import _clock

    marks: list[dict[str, Any]] = []
    folder = root / "analysis" / "cluster"
    if not _records.is_dir(folder):
        return marks
    for table in sorted(_records.glob(folder, "cluster_*_populations.csv")):
        named = _POPULATIONS.fullmatch(table.name)
        if not named:
            continue
        method = named.group("method").replace("_", " ")
        try:
            with _records.open_text(table, encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            continue
        for row in rows:
            frame = _number(row.get("medoid_frame"))
            share = _number(row.get("fraction"))
            if frame is None or frame < 0:
                continue
            # Numbered among the frames analysed: the trajectory's frame, and
            # its time on production's clock where the run keeps one.
            frames, times, axis = _clock(root, [int(frame)])
            marks.append({
                "kind": "medoid",
                "frame": frames[0],
                "time_ns": times[0] if axis == "Time (ns)" else _number(row.get("medoid_time_ns")),
                "label": (f"The medoid of {method} cluster {row.get('cluster', '?')}"
                          + (f", {_share(share)} of the frames" if share is not None else "")),
            })
    return marks


def _judgements(root: Path) -> list[dict[str, Any]]:
    from fastmdxplora.gui.stopping_view import _record_of
    from fastmdxplora.simulation.stopping import round_decided

    found = _record_of(root)
    if not found:
        return []
    record = found["record"]
    rounds = [entry for entry in record.get("rounds") or [] if isinstance(entry, dict)]
    marks = []
    for number, entry in enumerate(rounds, start=1):
        at = _number(entry.get("production_ns"))
        if at is None:
            continue
        # As every page and the report say a round (`stopping.round_decided`):
        # a stopped one by what stopped it, the last still said running as
        # extending, lengths in ps under 1 ns.
        decided = round_decided(entry, last=number == len(rounds), record=record)["said"]
        marks.append({"kind": "check", "frame": None, "time_ns": at,
                      "label": f"Judged after round {number}: {decided}"})
    return marks


def timeline_payload(root: str | Path) -> dict[str, Any]:
    """The marks of the run at ``root``, each with its frame or its time."""
    base = Path(root)
    return {"ok": True, "marks": _medoids(base) + _judgements(base)}
