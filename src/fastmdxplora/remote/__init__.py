"""Running studies on another machine, reached over SSH.

Inspecting a machine, installing FastMDXplora there with the user's
confirmation, and sending a study's config to run, watching it, fetching
the results and stopping it (:mod:`fastmdxplora.remote.send`).

A study's config never names a machine. The config is what runs anywhere;
which machine it runs on is a fact about this user and this computer, and is
kept with their other per-user settings.
"""

from __future__ import annotations

from fastmdxplora.remote.identity import CodeIdentity, same_code, this_code
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
    "CodeIdentity",
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
    "same_code",
    "save_machine",
    "this_code",
]
