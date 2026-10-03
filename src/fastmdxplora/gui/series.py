"""An analysis's numbers, for the GUI to draw and tie to the trajectory.

The Analysis page showed each analysis as the figure it wrote: a picture of a
line, from which a reader could take neither a value nor the frame it came
from. This reads the same data file the figure was drawn from and says, for
each point, where it sits in time and which frame of the trajectory it is,
together with the mean the analysis determined and what that mean rests on,
so the page can draw the series, give its values, and open the frame.

Only what the analysis wrote is used. The frame of each point is worked out
the way the trajectory was loaded for the analysis (its stride and first
frame, from the analysis manifest), and the time as the loader set it: frame
``k`` of the file was written at ``(k + 1)`` saving intervals.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from fastmdxplora.gui.report_dashboard import _numbers_in, unit_of

#: What each series is called and whether it runs over time or residues.
#: A name not listed is drawn over time if its analysis recorded a mean over
#: the frames, which is what marks a time series.
SERIES: dict[str, tuple[str, str]] = {
    "rmsd": ("RMSD", "time"),
    "rg": ("Radius of gyration", "time"),
    "hbonds": ("Hydrogen bonds", "time"),
    "sasa": ("Total SASA", "time"),
    "qvalue": ("Fraction of native contacts", "time"),
    "ligand_rmsd": ("Ligand RMSD", "time"),
    "rmsf": ("RMSF", "residue"),
}

#: More points than a line on a page can show; a longer series is thinned
#: evenly, and says so.
MOST_POINTS = 4000

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def series_over_time(root: Path) -> dict[str, Any]:
    """The analyses of a study whose numbers run over its frames, each with
    its label and unit, for the Viewer to plot one under its transport: the
    ones listed in :data:`SERIES` first, in that order, then the rest."""
    folder = Path(root) / "analysis"
    found: list[dict[str, Any]] = []
    try:
        names = sorted(p.name for p in folder.iterdir() if p.is_dir() and _NAME.match(p.name))
    except OSError:
        names = []
    order = list(SERIES)
    names.sort(key=lambda name: (order.index(name) if name in order else len(order), name))
    for name in names:
        if not (folder / name / f"{name}.dat").is_file():
            continue
        findings = _json(folder / name / "options.json").get("findings")
        mean = findings.get("mean") if isinstance(findings, dict) else None
        mean = mean if isinstance(mean, dict) else None
        label, kind = SERIES.get(name, (name, "time" if mean else ""))
        if kind == "time":
            found.append({"analysis": name, "label": label, "unit": unit_of(name, mean)})
    return {"ok": True, "series": found}


def series_payload(root: Path, analysis: str) -> dict[str, Any]:
    """The series of one analysis, or why there is none."""
    if not _NAME.match(analysis or ""):
        return {"ok": False, "reason": "not an analysis name"}
    folder = Path(root) / "analysis" / analysis
    data = folder / f"{analysis}.dat"
    if not data.is_file():
        return {"ok": False, "reason": f"{analysis} wrote no data file"}
    record = _json(folder / "options.json")
    found = (record.get("findings") or {}).get("mean")
    found = found if isinstance(found, dict) else None
    label, kind = SERIES.get(analysis, (analysis, "time" if found else ""))
    if not kind:
        return {"ok": False, "reason": f"{analysis} is not a series"}

    rows = _rows(data)
    if not rows:
        return {"ok": False, "reason": f"{analysis}'s data file holds no numbers"}
    unit = unit_of(analysis, found)
    if kind == "residue":
        return _residues(analysis, label, unit, rows)
    return _over_time(Path(root), analysis, label, unit, rows, found)


def _over_time(root: Path, analysis: str, label: str, unit: str,
               rows: list[tuple[list[str], list[float]]],
               found: dict[str, Any] | None) -> dict[str, Any]:
    values = [numbers[-1] for _, numbers in rows]
    n = len(values)
    manifest = _json(root / "analysis" / "analysis_manifest.json")
    loaded = manifest.get("load_kwargs") if isinstance(manifest.get("load_kwargs"), dict) else {}
    stride = _positive_int(loaded.get("stride")) or 1
    first = _non_negative_int(loaded.get("first")) or 0
    interval = loaded.get("saving_interval_ps")
    frames = [(first + i) * stride for i in range(n)]
    if isinstance(interval, (int, float)) and interval > 0:
        # Rounded to well past the clock's own precision, so 0.0003 ns is
        # not sent as 0.00030000000000000003.
        x = [round((frame + 1) * float(interval) / 1000.0, 12) for frame in frames]
        x_label = "Time (ns)"
    else:
        x = [float(frame) for frame in frames]
        x_label = "Frame"

    mean = None
    if found is not None and int(found.get("n_frames") or n) == n:
        discard = _non_negative_int(found.get("discard")) or 0
        mean = {
            "value": _finite(found.get("mean")),
            "error": _finite(found.get("standard_error")),
            "effective_samples": _finite(found.get("effective_samples")),
            "discard": discard,
            "from_x": x[discard] if discard < n else None,
            "not_a_measurement": found.get("not_a_measurement") or None,
        }

    every = max(1, math.ceil(n / MOST_POINTS))
    keep = list(range(0, n, every))
    return {
        "ok": True, "analysis": analysis, "label": label, "unit": unit, "kind": "time",
        "x_label": x_label,
        "x": [x[i] for i in keep], "y": [values[i] for i in keep],
        "frames": [frames[i] for i in keep],
        "thinned": every if every > 1 else None,
        "linked": _of_the_played_trajectory(root, manifest),
        "mean": mean,
    }


def _residues(analysis: str, label: str, unit: str,
              rows: list[tuple[list[str], list[float]]]) -> dict[str, Any]:
    """A profile over residues: one point each, named as the table names it
    (``A:13`` where there are several chains)."""
    labels: list[str] = []
    residues: list[dict[str, Any]] = []
    values: list[float] = []
    for words, numbers in rows:
        # A column of values alone is a profile numbered from one.
        value = numbers[-1]
        number = int(numbers[0]) if len(numbers) >= 2 else len(values) + 1
        chain = words[0] if words else None
        labels.append(f"{chain}:{number}" if chain else str(number))
        residues.append({"chain": chain, "resi": number})
        values.append(value)
    return {
        "ok": True, "analysis": analysis, "label": label, "unit": unit, "kind": "residue",
        "x_label": "Residue", "x": list(range(1, len(values) + 1)), "y": values,
        "labels": labels, "residues": residues, "linked": False, "mean": None,
    }


def _of_the_played_trajectory(root: Path, manifest: dict[str, Any]) -> bool:
    """Whether the analysed trajectory is the one the viewer plays once a run
    has finished, so that a point's frame is a frame the viewer can show: the
    joined trajectory of an extended study, or the production trajectory."""
    resolved = manifest.get("resolved") if isinstance(manifest.get("resolved"), dict) else {}
    analysed = resolved.get("trajectory") or manifest.get("trajectory_input")
    if not isinstance(analysed, str) or not analysed:
        return False
    joined = root / "joined" / "production.dcd"
    played = joined if (root / "joined" / "joined.json").is_file() and joined.is_file() \
        else root / "simulation" / "production.dcd"
    try:
        return Path(analysed).resolve() == played.resolve()
    except OSError:
        return False


def _rows(path: Path) -> list[tuple[list[str], list[float]]]:
    """Each data line as its text fields and its numbers; comments and a
    header (a line with no number in it) left out."""
    rows: list[tuple[list[str], list[float]]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        numbers = _numbers_in(text)
        if not numbers:
            continue
        words = [part for part in text.replace(",", " ").split() if _is_word(part)]
        rows.append((words, numbers))
    return rows


def _is_word(part: str) -> bool:
    try:
        float(part)
    except ValueError:
        return True
    return False


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _non_negative_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
