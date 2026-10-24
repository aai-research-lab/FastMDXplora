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

import csv
import io
import json
import math
import re
from pathlib import Path
from typing import Any

from fastmdxplora import record_access as _records
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
    # Backbone N-H generalised order parameters, one a residue (unitless).
    "order_parameters": ("Order parameter S\u00b2", "residue"),
}

#: More points than a line on a page can show; a longer series is thinned
#: evenly, and says so.
MOST_POINTS = 4000

NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_NAME = NAME


#: Why a refused record was not read, as `refused_said` and the pages say it.
NOT_READ_BECAUSE = {
    "outside": "it is a link to a file outside the study",
    "not a result": "it is a file of the study that is not one of its results",
    "leads to": "it leads to a file of the study that is not one of its results",
    "not a file": "it is not a regular file",
}


def why_not_read(root: Path, path: Path) -> str:
    """Why a confined read refused ``path`` (a key of `NOT_READ_BECAUSE`):
    a link out of the study ``root``, a file of the study that is not one
    of its results (hidden, say), or a link to such a file. Told from where
    the path leads, without opening it."""
    path = Path(path)
    try:
        resolved = path.resolve()
        resolved.relative_to(Path(root).resolve())
        itself = path.parent.resolve() / path.name
    except (OSError, RuntimeError, ValueError):
        return "outside"
    return "not a result" if resolved == itself else "leads to"


def refused_said(root: Path, path: Path) -> str:
    """A record a confined read refused (`record_access`), said as that."""
    try:
        named = Path(path).relative_to(root).as_posix()
    except ValueError:
        named = Path(path).name
    return f"{named} was not read: {NOT_READ_BECAUSE[why_not_read(root, path)]}"


def series_over_time(root: Path) -> dict[str, Any]:
    """The analyses of a study whose numbers run over its frames, each with
    its label and unit, for the Viewer to plot one under its transport: the
    ones listed in :data:`SERIES` first, in that order, then the rest."""
    folder = Path(root) / "analysis"
    found: list[dict[str, Any]] = []
    try:
        names = sorted(p.name for p in _records.iterdir(folder)
                       if _NAME.match(p.name) and _records.is_dir(p))
    except OSError:
        names = []
    order = list(SERIES)
    names.sort(key=lambda name: (order.index(name) if name in order else len(order), name))
    for name in names:
        if not _records.is_file(folder / name / f"{name}.dat"):
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
    record_path = folder / "options.json"
    for path in (data, record_path):
        if not _records.can_read(path):
            # Linked out of the study, or to a hidden file in it: not read,
            # and said so rather than as a series without its record.
            return {"ok": False, "unavailable": [path.relative_to(root).as_posix()],
                    "reason": f"{refused_said(Path(root), path)}, so {analysis} is not shown"}
    if not _records.is_file(data):
        return {"ok": False, "reason": f"{analysis} wrote no data file"}
    record = _json(record_path)
    found = (record.get("findings") or {}).get("mean")
    found = found if isinstance(found, dict) else None
    label, kind = SERIES.get(analysis, (analysis, "time" if found else ""))
    if not kind:
        return {"ok": False, "reason": f"{analysis} is not a series"}

    reference = None
    if analysis == "order_parameters":
        rows, reference = _order_parameter_rows(data)
    else:
        rows = _rows(data)
    if not rows:
        return {"ok": False, "reason": f"{analysis}'s data file holds no numbers"}
    unit = unit_of(analysis, found)
    if kind == "residue":
        options = record.get("options") if isinstance(record.get("options"), dict) else {}
        if analysis == "rmsf" and options.get("per_residue") is False:
            return _atoms(Path(root), analysis, label, unit, data, record, rows)
        payload = _residues(analysis, label, unit, rows)
        if analysis in {"rmsf", "order_parameters"}:
            entities = _profile_entities(Path(root), data, record, rows, "residue")
            payload["entities"] = entities
            for index, entity in enumerate(entities):
                if entity is None or not entity.get("icode"):
                    continue
                name = f"{entity['resi']}{entity['icode']}"
                if payload["labels"][index].startswith(f"{entity['chain']}:"):
                    name = f"{entity['chain']}:{name}"
                payload["labels"][index] = name
        if analysis == "order_parameters":
            payload["reference"] = reference
            payload["y_bounds"] = [0.0, 1.05]
            finding = (record.get("findings") or {}).get("order_parameters") or {}
            payload["notice"] = finding.get("not_a_measurement")
        return payload
    return _over_time(Path(root), analysis, label, unit, rows, found)


def series_column(path: Path) -> list[float]:
    """The values a time series' figure plots and its mean was recorded
    from: the last number of each data line. An analysis records a mean
    over its frames only where its result is one value a frame, written
    alone or after the frame number (``Analysis._record_what_the_mean_is_worth``),
    so that number is the series."""
    return [numbers[-1] for _, numbers in _rows(path)]


def _over_time(root: Path, analysis: str, label: str, unit: str,
               rows: list[tuple[list[str], list[float]]],
               found: dict[str, Any] | None) -> dict[str, Any]:
    values = [numbers[-1] for _, numbers in rows]
    n = len(values)
    manifest = _json(root / "analysis" / "analysis_manifest.json")
    frames, x, x_label = analysed_axis(root, n)

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
            # Its image note beside its verdict, as the table says it.
            "noted": _noted(found) or None,
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


def _noted(found: dict[str, Any]) -> str:
    """A mean's image note in a line (:func:`fastmdxplora.image_note.said_short`)."""
    from fastmdxplora.image_note import note_of, said_short

    return said_short(note_of(found))


def analysed_axis(root: Path, n: int) -> tuple[list[int], list[float], str]:
    """The trajectory frame of each of ``n`` analysed frames, its time in ns
    (or the frame, where no clock is recorded) and the axis's name, as the
    analysis loaded the trajectory: its stride and first frame, and frame
    ``k`` of the file written at ``(k + 1)`` saving intervals."""
    manifest = _json(Path(root) / "analysis" / "analysis_manifest.json")
    loaded = manifest.get("load_kwargs") if isinstance(manifest.get("load_kwargs"), dict) else {}
    stride = _positive_int(loaded.get("stride")) or 1
    first = _non_negative_int(loaded.get("first")) or 0
    from fastmdxplora.simulation.runner import chose_its_own_step

    # A run whose integrator chose its own step has no clock, whatever an
    # analysis made before that was known recorded.
    interval = (None if chose_its_own_step(Path(root) / "simulation")
                else loaded.get("saving_interval_ps"))
    recorded = manifest.get("frame_times_ps")
    if (isinstance(recorded, list) and len(recorded) == n and isinstance(interval, (int, float))
            and interval > 0 and all(isinstance(t, (int, float)) for t in recorded)):
        # Several files loaded at a stride are not evenly spaced (MDTraj
        # strides each file on its own); the loader's own times, kept by
        # the orchestrator, say where each frame was.
        frames = [int(round(float(t) / float(interval))) - 1 for t in recorded]
        return frames, [round(float(t) / 1000.0, 12) for t in recorded], "Time (ns)"
    frames = [(first + i) * stride for i in range(n)]
    if isinstance(interval, (int, float)) and interval > 0:
        # Rounded to well past the clock's own precision, so 0.0003 ns is
        # not sent as 0.00030000000000000003.
        return frames, [round((frame + 1) * float(interval) / 1000.0, 12)
                        for frame in frames], "Time (ns)"
    return frames, [float(frame) for frame in frames], "Frame"


def of_the_played_trajectory(root: Path) -> bool:
    """Whether the frames an analysis read are the trajectory the Viewer plays."""
    return _of_the_played_trajectory(Path(root),
                                      _json(Path(root) / "analysis" / "analysis_manifest.json"))


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


def _atoms(root: Path, analysis: str, label: str, unit: str, path: Path,
           record: dict[str, Any], rows: list[tuple[list[str], list[float]]]
           ) -> dict[str, Any]:
    """An atom RMSF profile, with an action only where its atom is known."""
    # A row whose atom serial is not a finite number names no atom (a "nan"
    # or "inf" in the file made the whole series a server error).
    rows = [(words, numbers) for words, numbers in rows if math.isfinite(numbers[0])]
    values = [numbers[-1] for _, numbers in rows]
    serials = [int(numbers[0]) for _, numbers in rows]
    return {
        "ok": True, "analysis": analysis, "label": label, "unit": unit, "kind": "atom",
        "x_label": "Atom", "x": serials, "y": values,
        "labels": [f"Atom {serial}" for serial in serials],
        "entities": _profile_entities(root, path, record, rows, "atom"),
        "linked": False, "mean": None,
    }


def _profile_entities(root: Path, path: Path, record: dict[str, Any],
                      rows: list[tuple[list[str], list[float]]], kind: str
                      ) -> list[dict[str, Any] | None]:
    """Resolve profile rows against the topology that analysis actually read.

    A chart point remains value-only when its saved label cannot be mapped
    uniquely. In particular, residue numbers alone never choose between
    chains or insertion codes, and atom serials must resolve to one atom.
    """
    from fastmdxplora.gui.selection import topology_the_analyses_read

    topology_path = topology_the_analyses_read(root)
    if topology_path is None:
        return [None] * len(rows)
    try:
        from fastmdxplora.analysis.residues import (
            chain_name,
            insertion_code,
            number,
            remember_insertion_codes,
        )
        from fastmdxplora.utils.native_output import topology_quietly

        remember_insertion_codes(topology_path)
        topology = topology_quietly(topology_path)
        selection = record.get("selection")
        selected = (list(range(topology.n_atoms)) if not isinstance(selection, str)
                    else [int(index) for index in topology.select(selection)])
        if kind == "atom":
            by_label: dict[int, list[Any]] = {}
            for index in selected:
                atom = topology.atom(index)
                label = int(atom.serial) if atom.serial is not None else int(atom.index) + 1
                by_label.setdefault(label, []).append(atom)
            result: list[dict[str, Any] | None] = []
            for _, numbers in rows:
                matches = by_label.get(int(numbers[0]), [])
                if len(matches) != 1:
                    result.append(None)
                    continue
                atom = matches[0]
                residue = atom.residue
                result.append({"kind": "atom", "chain": chain_name(residue),
                               "resi": number(residue),
                               "icode": insertion_code(residue),
                               "resn": str(residue.name), "atom": str(atom.name)})
            return result

        ordered: dict[int, Any] = {}
        for index in selected:
            atom = topology.atom(index)
            ordered[atom.residue.index] = atom.residue
        residues = [ordered[index] for index in sorted(ordered)]
        explicit = _profile_table_rows(path)
        if explicit and len(explicit) != len(rows):
            return [None] * len(rows)
        result = []
        for row_index, (words, values) in enumerate(rows):
            requested = explicit[row_index] if explicit and row_index < len(explicit) else {}
            number_value = _finite(requested.get("residue"))
            number_value = int(number_value) if number_value is not None else int(values[0])
            chain = str(requested.get("chain") or "").strip() or None
            code = requested.get("insertion")
            if code is None and not requested and words:
                # Older human-written profile files use a chain word before
                # the number (the native DataFrame format is parsed above).
                chain = words[0]
            code = str(code or "").strip() if code is not None else None
            matches = [residue for residue in residues if number(residue) == number_value
                       and (chain is None or chain_name(residue) == chain)
                       and (code is None or insertion_code(residue) == code)]
            if len(matches) != 1:
                result.append(None)
                continue
            residue = matches[0]
            result.append({"kind": "residue", "chain": chain_name(residue),
                           "resi": number(residue), "icode": insertion_code(residue),
                           "resn": str(residue.name)})
        return result
    except (OSError, ValueError, TypeError, IndexError, KeyError, OverflowError):
        return [None] * len(rows)


def _profile_table_rows(path: Path) -> list[dict[str, str]]:
    """Read identity columns from the analyses' comma-table form."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    first = next((line for line in text.splitlines()
                  if line.strip() and not line.lstrip().startswith("#")), "")
    if "," not in first:
        return []
    try:
        reader = csv.DictReader(io.StringIO("\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#"))))
        fields = {str(name or "").strip().lower() for name in reader.fieldnames or ()}
        if "residue" not in fields or not fields.intersection({"rmsf_nm", "s2"}):
            return []
        return [{str(key or "").strip().lower(): str(value or "").strip()
                 for key, value in row.items()} for row in reader]
    except (csv.Error, TypeError):
        return []


def _order_parameter_rows(
        path: Path) -> tuple[list[tuple[list[str], list[float]]], list[float | None]]:
    """Simulated S² is column two; an optional measured value stays separate.

    The chain-qualified CSV uses named columns, including insertion codes.
    The older numeric table has residue, simulated S² and optional measured S².
    """
    table = _profile_table_rows(path)
    try:
        if table:
            fields = ([row.get("chain", ""), row.get("residue", ""),
                       row.get("s2", ""), row.get("measured", "nan")] for row in table)
        else:
            fields = (["", *line.split()[:3]]
                      for line in path.read_text(encoding="utf-8").splitlines()
                      if line.strip() and not line.lstrip().startswith("#"))
        rows: list[tuple[list[str], list[float]]] = []
        reference: list[float | None] = []
        for row in fields:
            try:
                residue, value = float(row[1]), float(row[2])
            except (ValueError, IndexError):
                continue
            if not math.isfinite(residue) or not residue.is_integer() or not math.isfinite(value):
                continue
            rows.append(([row[0]] if row[0] else [], [residue, value]))
            try:
                measured = float(row[3])
            except (ValueError, IndexError):
                measured = float("nan")
            reference.append(measured if math.isfinite(measured) else None)
        return rows, reference
    except OSError:
        return [], []


def _of_the_played_trajectory(root: Path, manifest: dict[str, Any]) -> bool:
    """Whether the analysed trajectory is the one the viewer plays once a run
    has finished, so that a point's frame is a frame the viewer can show: the
    joined trajectory of an extended study, or the production trajectory."""
    resolved = manifest.get("resolved") if isinstance(manifest.get("resolved"), dict) else {}
    analysed = resolved.get("trajectory") or manifest.get("trajectory_input")
    if not isinstance(analysed, str) or not analysed:
        return False
    joined = root / "joined" / "production.dcd"
    played = joined if _records.is_file(root / "joined" / "joined.json") \
        and _records.is_file(joined) else root / "simulation" / "production.dcd"
    try:
        if Path(analysed).resolve() == played.resolve():
            return True
        # A study moved or shared names the trajectory where it was read:
        # the same place in the study, the folder before it gone.
        named = Path(analysed)
        return (not named.exists() and _records.is_file(played)
                and named.parts[-2:] == played.relative_to(root).parts[-2:])
    except (OSError, ValueError):
        return False


def _rows(path: Path) -> list[tuple[list[str], list[float]]]:
    """Each data line as its text fields and its numbers; comments and a
    header (a line with no number in it) left out."""
    rows: list[tuple[list[str], list[float]]] = []
    try:
        lines = _records.read_text(path, encoding="utf-8").splitlines()
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
        data = json.loads(_records.read_text(path, encoding="utf-8"))
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
