"""Running an install plan, once a person has said yes to it.

The plan is the one ``fastmdx remote --machine`` prints, from the same
function, so what runs here is exactly what was shown. Three rules:

**A person confirms, every time.** Installing on someone's account is not
something to do by accident, so there is no flag that skips the question,
and with no terminal to ask at, nothing runs.

**The machine is inspected again first.** A plan made from a record that
is a day old can be wrong about what is there now.

**It stops at the first step that fails,** says which, and leaves what the
earlier steps did in place: a half-made conda environment is removed with
one ``conda env remove``, and guessing what to undo is worse than saying.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from fastmdxplora.refusals import StudyError
from fastmdxplora.remote.describe import plan_for
from fastmdxplora.remote.identity import CodeIdentity, this_code
from fastmdxplora.remote.machines import Machine, Readiness, readiness
from fastmdxplora.remote.plan import InstallPlan
from fastmdxplora.remote.survey import inspect_machine
from fastmdxplora.remote.transport import Transport, run_here

__all__ = ["InstallOutcome", "install"]

#: A long conda solve and download on a slow link can take this long.
STEP_TIMEOUT_S = 3 * 3600


@dataclass
class InstallOutcome:
    """What an install did."""

    plan: InstallPlan
    ran: int
    failed_step: str = ""
    machine: Machine | None = None
    verdict: Readiness | None = None


def install(name: str, *, confirm: Callable[[InstallPlan], bool],
            transport: Transport | None = None,
            code: CodeIdentity | None = None,
            local_runner=None) -> InstallOutcome:
    """Inspect ``name``, show its plan to ``confirm``, and run it if agreed.

    ``confirm`` is shown the plan and says whether to go ahead; the command
    line asks a person. Nothing runs unless it returns ``True``.
    """
    code = code or this_code()
    link = transport or Transport(name)
    machine = inspect_machine(name, transport=link, code=code)
    verdict = readiness(machine, code)
    plan = plan_for(machine, code)
    if verdict.ready:
        return InstallOutcome(plan=plan, ran=0, machine=machine, verdict=verdict)
    if not plan.possible:
        raise StudyError(
            f"{name} has no install route. {plan.blocked}",
            code="remote.machine.not_ready",
            machine=name, reason=plan.blocked,
        )
    if not confirm(plan):
        raise StudyError(
            f"Nothing was installed on {name}: the plan was not confirmed.",
            code="remote.install.unconfirmed",
            machine=name,
        )

    ran = 0
    for step in plan.steps:
        print(f"\n[{'this computer' if step.where == 'here' else name}] "
              f"{step.command}", flush=True)
        if step.where == "here":
            returncode = run_here(["sh", "-c", step.command],
                                  runner=local_runner, timeout=STEP_TIMEOUT_S)
        else:
            returncode = link.run(["sh", "-c", step.command],
                                  timeout=STEP_TIMEOUT_S, show=True).returncode
        if returncode != 0:
            return InstallOutcome(plan=plan, ran=ran, failed_step=step.command)
        ran += 1

    machine = inspect_machine(name, transport=link, code=code)
    return InstallOutcome(plan=plan, ran=ran, machine=machine,
                          verdict=readiness(machine, code))


def ask_a_person(plan: InstallPlan) -> bool:
    """The confirmation the command line uses: a terminal, and a yes."""
    if not sys.stdin.isatty():
        raise StudyError(
            "Installing needs a person to confirm it, and there is no "
            "terminal here to ask at. Run the plan's commands yourself, or "
            "run this from a terminal.",
            code="remote.install.unconfirmed",
        )
    try:
        answer = input("\nInstall now? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")
