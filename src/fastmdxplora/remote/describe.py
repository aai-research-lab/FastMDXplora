"""What ``fastmdx remote`` prints, as lines, kept apart from the command.

Separate so the words can be tested without a terminal, and so another
interface can show the same machine the same way.
"""

from __future__ import annotations

from fastmdxplora.remote.identity import CodeIdentity
from fastmdxplora.remote.machines import Machine, Readiness, readiness, unloadable
from fastmdxplora.remote.plan import InstallPlan, install_plan
from fastmdxplora.remote.probe import Environment, Inspection

__all__ = ["describe_machine", "describe_plan", "describe_unloadable",
           "overview", "plan_for"]


def _gb(kb: int | None) -> str:
    return "" if kb is None else f"{kb / 1024 / 1024:.0f} GB"


def _hardware(found: Inspection) -> str:
    parts = [f"{found.host or '?'} ({found.os} {found.arch})".strip()]
    if found.cpus:
        parts.append(f"{found.cpus} CPUs")
    if found.memory_kb:
        parts.append(_gb(found.memory_kb))
    return ", ".join(parts)


def _gpus(found: Inspection) -> str:
    if found.gpus:
        names: dict[str, int] = {}
        for gpu in found.gpus:
            names[gpu.name] = names.get(gpu.name, 0) + 1
        listed = ", ".join(f"{count}x {name}" for name, count in names.items())
        ceiling = (f", CUDA up to {found.cuda_driver}" if found.cuda_driver
                   else "")
        return f"{listed} (driver {found.gpus[0].driver}{ceiling})"
    if found.kind == "slurm":
        gres = sorted({p["gres"] for p in found.partitions
                       if p.get("gres") and p["gres"] != "(null)"})
        held = f"; partitions offer {', '.join(gres)}" if gres else ""
        return f"none on this node{held}"
    return "none found"


def _short(kind: Inspection) -> str:
    if kind.gpus:
        return f"{len(kind.gpus)} GPU" + ("s" if len(kind.gpus) > 1 else "")
    return "no GPU here" if kind.kind == "slurm" else "no GPU"


def _installation_line(env: Environment) -> str:
    line = f"{env.path}: {env.identity.describe()}"
    return line + (f" (source {env.checkout})" if env.checkout else "")


def describe_machine(machine: Machine, code: CodeIdentity) -> list[str]:
    """The machine as inspected, and the code this computer runs."""
    found = machine.inspection
    conda = next(iter(found.conda.values()), "") or (
        found.conda_roots[0] if found.conda_roots else "not found")
    held = [_installation_line(env) for env in found.installations()]
    scheduler = "SLURM" if found.kind == "slurm" else "none"
    if found.partitions:
        scheduler += " (partitions " + ", ".join(
            f"{p['name']} {p['time_limit']}" for p in found.partitions) + ")"
    scratch = (f"{found.scratch} ({_gb(found.scratch_free_kb)} free)"
               if found.scratch else "not found")
    rows = [
        ("host", _hardware(found)),
        ("GPUs", _gpus(found)),
        ("conda", conda),
        ("apptainer", found.container or "not found"),
        ("scheduler", scheduler),
        ("scratch", scratch),
        ("home", f"{_gb(found.home_free_kb)} free" if found.home_free_kb
         is not None else "?"),
        ("internet", found.internet),
        ("fastmdx", held[0] if held else "not installed"),
    ]
    rows += [("", more) for more in held[1:]]
    here = code.describe()
    if code.checkout:
        here += f" ({code.checkout})"
    rows.append(("this computer", here))
    lines = [f"{machine.name}   {found.kind}"]
    lines += [f"  {label:<13} {value}" for label, value in rows]
    return lines


def describe_plan(plan: InstallPlan, machine: str) -> list[str]:
    """How to install, in the words someone would copy from."""
    if plan.blocked:
        return ["No install route:", f"  {plan.blocked}"]
    what = ("To bring it to this computer's commit" if plan.route == "checkout"
            else f"Install plan ({plan.route}, inside your account only)")
    lines = [f"{what}:"]
    for step in plan.steps:
        where = "on this computer" if step.where == "here" else f"on {machine}"
        lines.append(f"  [{where}]")
        lines.append(f"    {step.command}")
    if plan.check is not None:
        lines.append("  then check it:")
        lines.append(f"    {plan.check.command}")
    for note in plan.notes:
        lines.append(f"  {note}")
    lines += ["", "Run these yourself, then inspect the machine again:",
              f"  fastmdx remote --machine {machine}"]
    return lines


def plan_for(machine: Machine, code: CodeIdentity) -> InstallPlan:
    return install_plan(machine.inspection, code, machine.name)


def describe_unloadable(machine: Machine, verdict: Readiness) -> list[str]:
    """What an installation holding this code cannot load, and how to add it.

    Empty where no installation holds the code, in which case the install
    plan is the answer instead.
    """
    env = verdict.installation
    if env is None:
        return []
    if env.path not in machine.info:
        return [f"{env.path} holds this code and did not say what it can "
                "load. Run `fastmdx info` there to see why."]
    rows = [f"  {entry.get('name', entry['import_name']):<20} "
            f"{entry.get('state', 'unknown'):<9} "
            f"{entry.get('install', '')}".rstrip()
            for entry in unloadable(machine, env)]
    if not rows:
        return []
    return [f"{env.path} holds this code and cannot load:", *rows,
            "Install those into that environment, then inspect again."]


def overview(machines: list[Machine], code: CodeIdentity) -> list[str]:
    """One line per machine, from what was recorded. Connects to nothing."""
    if not machines:
        return ["No machines yet. Inspect one from your ~/.ssh/config:",
                "  fastmdx remote --machine <alias>"]
    width = max(len(m.name) for m in machines)
    lines = ["Machines"]
    for machine in machines:
        verdict = readiness(machine, code)
        mark = "ready" if verdict.ready else "not ready"
        when = machine.inspected_at.replace("T", " ").replace("Z", " UTC")
        lines.append(
            f"  {machine.name:<{width}}  {machine.inspection.kind:<11} "
            f"{_short(machine.inspection):<11} {mark}: {verdict.summary}"
            f"  (inspected {when})")
    lines += ["", f"This computer runs {code.describe()}. The list is as last "
              "inspected; inspect a machine again to refresh it."]
    return lines
