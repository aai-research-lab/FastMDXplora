"""Inspecting a machine: the probe, then the installation it found.

Two steps over one connection. The probe says what the machine has. Then,
where it holds the exact version running on this computer, that
installation is asked what it can load, with ``fastmdx info --json`` -- the
same answer ``fastmdx info`` gives a person there. An installation that is
present and cannot load OpenMM is not ready, and only asking it tells.
"""

from __future__ import annotations

import json
from typing import Any

from fastmdxplora import __version__
from fastmdxplora.refusals import StudyError
from fastmdxplora.remote.machines import Machine, now_utc, save_machine
from fastmdxplora.remote.probe import PROBE_SCRIPT, Inspection, parse_inspection
from fastmdxplora.remote.transport import Transport

__all__ = ["inspect_machine", "info_command"]

#: Seconds allowed for ``fastmdx info --json`` on the machine. It imports
#: every backend in a subprocess, and a cold conda environment on a network
#: filesystem is slow to import from.
INFO_TIMEOUT_S = 300


def info_command(inspection: Inspection, version: str) -> tuple[list[str], str]:
    """The command asking the installation of ``version`` what it can load.

    Returns the command and where the installation is, or an empty command
    where the machine holds no such installation.
    """
    env = inspection.environment_for(version)
    if env is not None:
        return [f"{env.path}/bin/fastmdx", "info", "--json"], env.path
    if inspection.on_path is not None and inspection.on_path.version == version:
        return [inspection.on_path.path, "info", "--json"], inspection.on_path.path
    image = inspection.image_for(version)
    if image and inspection.container:
        return ([inspection.container, "exec", image, "fastmdx", "info", "--json"],
                image)
    return [], ""


def _json_in(text: str) -> dict[str, Any] | None:
    """The JSON object in ``text``, ignoring anything printed before it."""
    start = text.find("\n{")
    start = 0 if text.startswith("{") else (start + 1 if start >= 0 else -1)
    if start < 0:
        return None
    try:
        value = json.loads(text[start:])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def inspect_machine(name: str, *, transport: Transport | None = None,
                    version: str = __version__, save: bool = True) -> Machine:
    """Inspect ``name``, record what it has, and return the record."""
    link = transport or Transport(name)
    answer = link.run(["sh", "-s"], stdin=PROBE_SCRIPT)
    inspection = parse_inspection(answer.stdout)
    if inspection is None:
        said = (answer.stderr or "").strip().splitlines()
        raise StudyError(
            f"{name} answered the inspection with something that could not "
            f"be read (exit code {answer.returncode}"
            + (f": {said[-1]}" if said else "") + "). Its shell may not be "
            "a POSIX sh, or the connection closed early.",
            code="environment.service.machine_unreadable",
            machine=name,
        )

    machine = Machine(name=name, inspected_at=now_utc(), inspection=inspection)
    command, where = info_command(inspection, version)
    if command:
        reply = link.run(command, timeout=INFO_TIMEOUT_S)
        info = _json_in(reply.stdout) if reply.returncode == 0 else None
        machine.installation = where
        if info is not None:
            machine.info = {version: info}
    if save:
        save_machine(machine)
    return machine
