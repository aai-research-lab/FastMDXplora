"""The studies in a workspace, as cards, and two of them compared.

The GUI showed one study at a time, and another was opened by walking to
its folder. This finds the studies under a folder and says each in a card:
its structure, what kind of study it is, where it stands, when it was made,
and the means it recorded with their errors, with a figure it drew. Two
chosen are compared as `fastmdx diff` compares them, the settings in which
they differ with defaults filled in, and beside that the means both
recorded, a difference marked only past twice their combined error, the
rule the Analysis page and the written comparison use.

Everything is read from the studies' own records; nothing is written.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["studies_in", "card_of", "studies_compared", "thumbnail_of"]

#: A difference is marked past this many combined standard errors.
RESOLVED_AT = 2.0

#: How deep under the folder to look, and how many studies to say: a home
#: folder can hold thousands of folders, and the page has to answer.
DEEPEST = 3
MOST = 200

#: Folders a study keeps inside it that are never studies of their own.
_INSIDE_A_STUDY = {"runs", "superseded", "setup", "simulation", "analysis", "report",
                   "comparison", "shared_setup", "free_energy", "pmf"}

#: The measures a card names first, in order.
_FIRST = ("rmsd", "rg", "sasa", "rmsf", "hbonds", "ligand_rmsd")


def studies_in(root: Path | str, *, deepest: int = DEEPEST,
               most: int = MOST) -> dict[str, Any]:
    """The studies under ``root``, newest first, each as a card."""
    from fastmdxplora.gui.browse import is_study

    base = Path(root).expanduser()
    try:
        base = base.resolve()
    except OSError:
        pass
    if not base.is_dir():
        return {"ok": False, "reason": f"No such folder: {base}"}
    found: list[Path] = []
    more = False

    def walk(folder: Path, depth: int) -> None:
        nonlocal more
        if len(found) >= most:
            more = True
            return
        try:
            children = sorted(p for p in folder.iterdir()
                              if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return
        for child in children:
            if len(found) >= most:
                more = True
                return
            if child.name in _INSIDE_A_STUDY:
                continue
            if is_study(child):
                found.append(child)
            elif depth < deepest:
                walk(child, depth + 1)

    if is_study(base):
        found.append(base)
    else:
        walk(base, 1)
    cards = [card_of(folder) for folder in found]
    cards.sort(key=lambda card: card.get("when") or "", reverse=True)
    return {"ok": True, "root": str(base), "studies": cards, "more": more}


def card_of(folder: Path | str) -> dict[str, Any]:
    """What a card says of one study, from its records."""
    base = Path(folder)
    config = _lifted(_read_yaml(base / "resolved_config.yml")
                     or _read_yaml(base / "exploration.yml"))
    batch = _read_json(base / "batch_manifest.json")
    manifest = _read_json(base / "manifest.json")
    card: dict[str, Any] = {
        "path": str(base),
        "name": base.name,
        "system": _system_of(config, batch, manifest),
        "kind": _kind_of(config, batch),
        "state": _state_of(base, batch, manifest),
        "when": _when(base, manifest),
        "means": [],
        "thumbnail": bool(thumbnail_of(base)),
    }
    simulation = (config or {}).get("simulation") if isinstance(config, dict) else None
    if isinstance(simulation, dict) and simulation.get("duration_ns") is not None:
        card["production_ns"] = simulation.get("duration_ns")
    forcefield = ((config or {}).get("setup") or {}).get("forcefield") \
        if isinstance(config, dict) else None
    if forcefield and str(forcefield) != "auto":
        card["forcefield"] = str(forcefield)
    if not isinstance(batch, dict):
        card["means"] = [{"analysis": name, **mean} for name, mean in _means(base)[:3]]
    pmf = _read_json(base / "pmf.json")
    if isinstance(pmf, dict):
        # Whether the windows gave a free energy, and if not the first
        # sentence of why: the profile itself is on the study's pages.
        refused = str(pmf.get("refused") or "")
        card["free_energy"] = ({"refused": refused.split(". ")[0].rstrip(".") + "."}
                               if refused else {"recombined": True})
    return card


def thumbnail_of(folder: Path | str) -> Path | None:
    """A figure the study drew, for its card: the first of its measures'
    own figures, else the report's summary figure."""
    base = Path(folder)
    for name in _FIRST:
        figure = base / "analysis" / name / f"{name}.png"
        if figure.is_file():
            return figure
    summary = base / "report" / "analysis_summary.png"
    if summary.is_file():
        return summary
    # An umbrella study's own figure is its free energy profile.
    for profile in sorted((base / "pmf").glob("*.png")) if (base / "pmf").is_dir() else []:
        return profile
    analysis = base / "analysis"
    if analysis.is_dir():
        for figure in sorted(analysis.glob("*/*.png")):
            return figure
    return None


def studies_compared(first: Path | str, second: Path | str) -> dict[str, Any]:
    """Two studies side by side: the settings in which they differ, and the
    means both recorded, a difference marked only where it is resolved."""
    from fastmdxplora.config.diff import config_of, differences

    a, b = Path(first), Path(second)
    try:
        left, _ = config_of(a)
        right, _ = config_of(b)
    except Exception as exc:  # noqa: BLE001 - said, not raised
        from fastmdxplora.refusals import refusal_of

        return {"ok": False, "reason": refusal_of(exc).message}
    settings = [d.as_record() for d in differences(left, right) if not d.where_only]

    mine, theirs = dict(_means(a)), dict(_means(b))
    measures = []
    for name in _in_order(set(mine) | set(theirs)):
        one, other = mine.get(name), theirs.get(name)
        row: dict[str, Any] = {"analysis": name, "first": one, "second": other,
                               "label": (one or other or {}).get("label", name),
                               "unit": (one or other or {}).get("unit", "")}
        usable = [side for side in (one, other)
                  if side and side.get("mean") is not None and side.get("error") is not None]
        if len(usable) == 2:
            difference = other["mean"] - one["mean"]
            error = math.hypot(one["error"], other["error"])
            row["versus"] = {"difference": difference, "error": error,
                             "resolved": error > 0 and abs(difference) > RESOLVED_AT * error}
        measures.append(row)
    return {"ok": True, "first": card_of(a), "second": card_of(b),
            "settings": settings, "measures": measures, "resolved_at": RESOLVED_AT}


# ---------------------------------------------------------------------------
def _means(base: Path) -> list[tuple[str, dict[str, Any]]]:
    """(analysis, {mean, error, unit, withheld}) for each mean the study
    recorded, the measures a card names first leading."""
    from fastmdxplora.batch.aggregate import read_member_findings
    from fastmdxplora.gui.report_dashboard import unit_of
    from fastmdxplora.gui.series import SERIES

    try:
        found = read_member_findings(base)
    except Exception:  # noqa: BLE001 - a card stands without them
        return []
    out = []
    for name in _in_order(found):
        record = found.get(name) or {}
        mean = record.get("mean") if isinstance(record.get("mean"), dict) else None
        if mean is None:
            continue
        withheld = mean.get("not_a_measurement") or None
        out.append((name, {
            "label": SERIES.get(name, (name.replace("_", " "), ""))[0],
            "mean": _finite(mean.get("mean")),
            "error": None if withheld else _finite(mean.get("standard_error")),
            "unit": unit_of(name, mean),
            "withheld": str(withheld) if withheld else None,
        }))
    return out


def _in_order(names: Any) -> list[str]:
    names = list(names)
    return sorted(names, key=lambda n: (_FIRST.index(n) if n in _FIRST else len(_FIRST), n))


def _system_of(config: Any, batch: Any, manifest: Any) -> str:
    for source in (config, batch):
        systems = source.get("systems") if isinstance(source, dict) else None
        if isinstance(systems, list) and systems and isinstance(systems[0], dict):
            named = {str(s.get("system")) for s in systems
                     if isinstance(s, dict) and s.get("system")}
            if len(named) == 1:
                return _short(next(iter(named)))
            if named:
                return f"{len(named)} systems"
    if isinstance(manifest, dict) and manifest.get("system"):
        return _short(str(manifest["system"]))
    return ""


def _short(system: str) -> str:
    """A structure by its identifier, or a file by its name."""
    return Path(system).name if ("/" in system or "\\" in system) else system


def _kind_of(config: Any, batch: Any) -> str:
    if isinstance(batch, dict):
        planned = batch.get("planned") or []
        runs = int(batch.get("n_runs") or len(planned) or 0)
        if not runs:
            return "several runs"
        if any(isinstance(p, dict) and ((p.get("options") or {}).get("simulation") or {})
               .get("umbrella") for p in planned):
            return f"umbrella sampling, {runs} windows"
        sweep = batch.get("sweep") if isinstance(batch.get("sweep"), dict) else {}
        axes = sorted(sweep)
        if axes and all(axis.endswith("random_seed") for axis in axes):
            return f"{runs} replicas"
        if axes:
            return f"{runs} runs across {', '.join(a.split('.')[-1] for a in axes)}"
        return f"{runs} runs"
    phases = (config or {}).get("include_phase") if isinstance(config, dict) else None
    if isinstance(phases, list) and phases and "simulation" not in phases:
        return "a trajectory analysed" if "analysis" in phases else "prepared"
    simulation = (config or {}).get("simulation") if isinstance(config, dict) else None
    if isinstance(simulation, dict):
        for key, said in (("metadynamics", "metadynamics"), ("steered", "steered pulling")):
            if simulation.get(key):
                return said
    return "one run"


def _state_of(base: Path, batch: Any, manifest: Any) -> str:
    try:
        from fastmdxplora.simulation.resume import _still_running

        if _still_running(base):
            return "running"
    except Exception:  # noqa: BLE001 - a record, not a verdict
        pass
    if isinstance(batch, dict):
        from fastmdxplora.gui.exploration import runs_of_a_study

        runs = runs_of_a_study(base) or []
        states = [run["state"] for run in runs]
        if states and all(state == "completed" for state in states):
            return "completed"
        if any(state == "failed" for state in states):
            return "stopped" if _stopped_in(batch) else "failed"
        return "incomplete"
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    if not isinstance(phases, list) or not phases:
        return "not started"
    for phase in phases:
        if isinstance(phase, dict) and phase.get("status") == "error":
            code = str((phase.get("refusal") or {}).get("code") or "")
            return "stopped" if code == "simulation.run.stopped" else "failed"
    return "completed"


def _stopped_in(batch: dict[str, Any]) -> bool:
    return any(isinstance(run, dict) and run.get("error_type") == "Stopped"
               for run in batch.get("runs") or [])


def _when(base: Path, manifest: Any) -> str:
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    if isinstance(phases, list):
        for phase in phases:
            if isinstance(phase, dict) and phase.get("started_at"):
                return str(phase["started_at"])
    try:
        stamp = os.path.getmtime(base)
    except OSError:
        return ""
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")


def _lifted(config: Any) -> Any:
    """A study of several runs writes its phase settings under `options`;
    lifted beside the rest, so one reading serves both."""
    if isinstance(config, dict) and isinstance(config.get("options"), dict):
        return {**config, **config["options"]}
    return config


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_yaml(path: Path) -> Any:
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None
