"""The machines a study can be sent to, as this user has inspected them.

A machine is kept per user, never in a study. The name is an alias from
this person's ``~/.ssh/config``, meaningless in anyone else's copy of the
config, and a study that named it would stop being something that runs
anywhere. So the records sit beside the model choice and the cost
calibration, in :func:`~fastmdxplora.user_dir.user_config_dir`, one file
per machine under ``machines/``.

A record holds what the last inspection found and when. It does not hold
a verdict. Whether a machine is ready depends on the version running on
*this* computer, which changes on every upgrade, so readiness is worked out
when it is asked for, from the record and the version then.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError
from fastmdxplora.remote.probe import Inspection
from fastmdxplora.remote.transport import check_machine_name
from fastmdxplora.user_dir import user_config_dir

__all__ = [
    "Machine",
    "Readiness",
    "UnknownMachine",
    "forget_machine",
    "load_machine",
    "machine_names",
    "machines_dir",
    "readiness",
    "save_machine",
]

#: Backends a machine must load for a study to run there: the ones the
#: simulation and ligand paths reach for. Import names, as ``fastmdx info
#: --json`` reports them.
REQUIRED_BACKENDS = ("openmm", "pdbfixer", "openff.toolkit",
                     "openmmforcefields", "rdkit", "propka")


def machines_dir() -> Path:
    """Where machine records live."""
    return user_config_dir() / "machines"


@dataclass
class Machine:
    """One machine, as last inspected."""

    name: str
    inspected_at: str
    inspection: Inspection
    #: ``fastmdx info --json`` from the installation matching this computer's
    #: version at the time, keyed by that version. Empty where there was none.
    info: dict[str, Any] = field(default_factory=dict)
    #: Where that installation is: an environment or an image path.
    installation: str = ""

    def as_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "inspected_at": self.inspected_at,
            "inspection": self.inspection.as_record(),
            "info": self.info,
            "installation": self.installation,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Machine:
        return cls(
            name=str(record["name"]),
            inspected_at=str(record.get("inspected_at", "")),
            inspection=Inspection.from_record(record.get("inspection") or {}),
            info=dict(record.get("info") or {}),
            installation=str(record.get("installation", "")),
        )


def _path_for(name: str) -> Path:
    return machines_dir() / f"{check_machine_name(name)}.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_machine(machine: Machine) -> Path:
    """Write the record, replacing any earlier one for the same machine."""
    target = _path_for(machine.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_suffix(".json.part")
    scratch.write_text(json.dumps(machine.as_record(), indent=2) + "\n",
                       encoding="utf-8")
    scratch.replace(target)
    return target


def machine_names() -> list[str]:
    """Every machine with a record, in name order."""
    folder = machines_dir()
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.json"))


class UnknownMachine(StudyError):
    """A machine named that has no record on this computer."""

    default_code = "remote.machine.unknown"


def _unknown_message(name: str) -> str:
    known = machine_names()
    listed = ", ".join(known) if known else "none yet"
    return (f"No machine called {name!r} has been inspected here (known: "
            f"{listed}). Inspect it first with "
            f"`fastmdx remote --machine {name}`.")


def load_machine(name: str) -> Machine:
    """The record for ``name``; refuses one never inspected."""
    target = _path_for(name)
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UnknownMachine(_unknown_message(name), given=name,
                             permitted=machine_names()) from None
    except ValueError as exc:
        raise UnknownMachine(
            f"The record for {name!r} at {target} could not be read ({exc}). "
            f"Inspect the machine again to rewrite it.",
            given=name, permitted=machine_names(),
        ) from exc
    return Machine.from_record(record)


def forget_machine(name: str) -> Path:
    """Remove the record. Nothing on the machine itself is touched."""
    target = _path_for(name)
    if not target.is_file():
        raise UnknownMachine(_unknown_message(name), given=name,
                             permitted=machine_names())
    target.unlink()
    return target


@dataclass(frozen=True)
class Readiness:
    """Whether a study from this computer can run on the machine, and why."""

    ready: bool
    summary: str


def readiness(machine: Machine, version: str) -> Readiness:
    """Whether the machine holds exactly ``version`` with its backends.

    Exactly, because two versions can resolve a study's defaults
    differently, and a run that resolved differently from the config it
    was sent is the failure the environment comparison exists to catch.
    """
    found = machine.inspection
    matching = found.environment_for(version) or (
        found.on_path if found.on_path and found.on_path.version == version
        else None)
    image = found.image_for(version)
    if matching is None and not image:
        others = sorted({e.version for e in found.environments if e.version})
        if found.on_path and found.on_path.version:
            others = sorted(set(others) | {found.on_path.version})
        held = f"holds {', '.join(others)}" if others else "has no FastMDXplora"
        return Readiness(False, f"needs {version} ({held})")
    backends = (machine.info.get(version) or {}).get("backends") or {}
    if not backends:
        return Readiness(False, f"{version} found, backends not checked")
    missing = [(backends.get(name) or {}).get("name", name)
               for name in REQUIRED_BACKENDS
               if (backends.get(name) or {}).get("state") != "installed"]
    if missing:
        return Readiness(False, f"{version} found, missing {', '.join(missing)}")
    return Readiness(True, f"{version}, backends load")
