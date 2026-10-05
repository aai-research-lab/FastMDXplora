"""What a phase run again in a study's folder replaces, and what it leaves stale.

A phase runs into a folder that already holds its output only when asked to
(`--force-overwrite`, `--rerun`; refused otherwise, `_refuse_to_overwrite`).
Its output is then replaced, and so is the output of every phase after it
that this run does not do again: a report written from the analyses before,
or analyses of the frames a simulation run again no longer has, would sit
beside results they contradict. `--force-overwrite` removes them;
`--rerun` keeps them in the study's `previous/<phase>`, in place of what was
kept there before, so one copy of each is kept and nothing piles up. Either
way, a phase left stale is said, with the command that writes it again, and
its record leaves the study's manifest until it is.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable, Iterable

#: The phases in the order each reads the one before.
ORDER = ("setup", "simulation", "analysis", "report")
#: Where `--rerun` keeps what it replaced, one copy of each phase.
PREVIOUS = "previous"
#: The command that runs each phase alone.
COMMAND = {"setup": "setup", "simulation": "simulate", "analysis": "analyze",
           "report": "report"}


def outputs_of(root: Path, phase: str) -> list[Path]:
    """What ``phase`` has written in a study's folder, where it has: its own
    folder, and for the simulation the pieces of an extended study, their
    join, and the record of a study run until a quantity was determined."""
    root = Path(root)
    found = [root / phase]
    if phase == "simulation":
        found += [root / "joined", root / "stopping.json",
                  *sorted(root.glob("segment-*"))]
    return [path for path in found if _holds_something(path)]


def _holds_something(path: Path) -> bool:
    if path.is_dir():
        return any(path.iterdir())
    return path.is_file()


def make_way(root: Path, plan: Iterable[str], *, keep: bool,
             say: Callable[[str], Any] = print) -> list[str]:
    """Clear what ``plan`` will write and what it leaves stale; the phases
    cleared, in order. ``keep`` moves them to ``previous/<phase>``."""
    root = Path(root)
    planned = [phase for phase in ORDER if phase in set(plan)]
    if not planned:
        return []
    cleared: list[str] = []
    for phase in ORDER[ORDER.index(planned[0]):]:
        found = outputs_of(root, phase)
        if not found:
            continue
        record = _drop_from_the_manifest(root, phase)
        if keep:
            where = root / PREVIOUS / phase
            if where.exists():
                shutil.rmtree(where)
            where.parent.mkdir(exist_ok=True)
            own = root / phase
            if own in found:
                own.rename(where)
            else:
                where.mkdir()
            for path in found:
                if path != own:
                    path.rename(where / path.name)
            if record is not None:
                (where / "phase_record.json").write_text(
                    json.dumps(record, indent=2), encoding="utf-8")
            done = f"kept in {PREVIOUS}/{phase}"
        else:
            for path in found:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            done = "removed"
        if phase in planned:
            say(f"{phase}: what it wrote before is {done}.")
        else:
            say(f"{phase}: written from the {_before(phase, planned)} before, so {done}; "
                f"`fastmdx {COMMAND[phase]} --output {root}` writes it again.")
        cleared.append(phase)
    return cleared


def _before(phase: str, planned: list[str]) -> str:
    """The last phase run again before ``phase``, as the stale one reads it."""
    earlier = [name for name in planned if ORDER.index(name) < ORDER.index(phase)]
    return {"setup": "preparation", "simulation": "simulation",
            "analysis": "analyses", "report": "report"}[earlier[-1]]


def _drop_from_the_manifest(root: Path, phase: str) -> dict[str, Any] | None:
    """Take ``phase``'s record out of the study's manifest, and return it:
    until the phase runs again the study does not hold what it recorded."""
    path = root / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    if not isinstance(phases, list):
        return None
    kept = [entry for entry in phases
            if not (isinstance(entry, dict) and entry.get("name") == phase)]
    if len(kept) == len(phases):
        return None
    dropped = next(entry for entry in phases
                   if isinstance(entry, dict) and entry.get("name") == phase)
    manifest["phases"] = kept
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dropped
