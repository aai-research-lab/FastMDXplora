"""Running studies on another machine, reached over SSH.

What exists so far is the first half: naming a machine, finding out what it
has, and saying how FastMDXplora would be installed there. Sending a study,
watching it and bringing the results back come next, and build on this.

A study's config never names a machine. The config is what runs anywhere;
which machine it runs on is a fact about this user and this computer, and is
kept with their other per-user settings.
"""

from __future__ import annotations

from fastmdxplora.remote.machines import (
    Machine,
    Readiness,
    UnknownMachine,
    forget_machine,
    load_machine,
    machine_names,
    machines_dir,
    readiness,
    save_machine,
)
from fastmdxplora.remote.plan import (
    DEFAULT_CUDA_VERSION,
    InstallPlan,
    Step,
    cuda_pin,
    environment_name,
    install_plan,
    is_release,
)
from fastmdxplora.remote.probe import (
    PROBE_SCRIPT,
    Environment,
    Gpu,
    Inspection,
    parse_inspection,
)
from fastmdxplora.remote.survey import inspect_machine
from fastmdxplora.remote.transport import Answer, Transport, check_machine_name

__all__ = [
    "DEFAULT_CUDA_VERSION",
    "PROBE_SCRIPT",
    "Answer",
    "Environment",
    "Gpu",
    "InstallPlan",
    "Inspection",
    "Machine",
    "Readiness",
    "Step",
    "Transport",
    "UnknownMachine",
    "check_machine_name",
    "cuda_pin",
    "environment_name",
    "forget_machine",
    "inspect_machine",
    "install_plan",
    "is_release",
    "load_machine",
    "machine_names",
    "machines_dir",
    "parse_inspection",
    "readiness",
    "save_machine",
]
