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

**Where there is no terminal, the yes is to one plan.** A caller that shows
the plan in a window of its own takes a yes for it from :func:`plan_yes`:
bound to the machine and to the plan's every command, used once, and gone
after ten minutes. :func:`confirmed_by` accepts it only for the plan the
machine's fresh inspection gives, so a plan that changed between being
shown and being run is not run. The yes lives in the process that showed
the plan, and nothing on disk stands for it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastmdxplora.refusals import StudyError
from fastmdxplora.remote.describe import plan_for
from fastmdxplora.remote.identity import CodeIdentity, this_code
from fastmdxplora.remote.machines import Machine, Readiness, readiness
from fastmdxplora.remote.plan import InstallPlan
from fastmdxplora.remote.survey import inspect_machine
from fastmdxplora.remote.transport import Transport, run_here

__all__ = ["PLAN_YES_S", "InstallOutcome", "confirmed_by", "install",
           "plan_digest", "plan_yes"]

#: A long conda solve and download on a slow link can take this long.
STEP_TIMEOUT_S = 3 * 3600

#: How long a yes to a plan shown in a window holds.
PLAN_YES_S = 600


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
            # A step here that copies to the machine (rsync) reaches it as
            # every other command does: its kept connection, and no prompt
            # where nobody is there to answer one.
            returncode = run_here(["sh", "-c", step.command],
                                  runner=local_runner, timeout=STEP_TIMEOUT_S,
                                  env={**os.environ, "RSYNC_RSH": link.rsync_shell()})
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


def plan_digest(plan: InstallPlan, machine: str) -> str:
    """The plan's every command and where each runs, as one SHA-256."""
    said = {"machine": machine, "version": plan.version, "route": plan.route,
            "steps": [[step.where, step.command] for step in plan.steps],
            "check": ([plan.check.where, plan.check.command]
                      if plan.check is not None else None)}
    return hashlib.sha256(json.dumps(said, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class _Yes:
    machine: str
    digest: str
    until: float


_YESES: dict[str, _Yes] = {}
_YESES_LOCK = threading.Lock()


def plan_yes(name: str, plan: InstallPlan) -> str:
    """A yes to ``plan`` on ``name``, to hand back to :func:`confirmed_by`."""
    if not plan.possible:
        raise StudyError(
            f"{name} has no install route. {plan.blocked}",
            code="remote.machine.not_ready", machine=name, reason=plan.blocked)
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _YESES_LOCK:
        for old in [t for t, yes in _YESES.items() if yes.until <= now]:
            del _YESES[old]
        _YESES[token] = _Yes(name, plan_digest(plan, name), now + PLAN_YES_S)
    return token


def confirmed_by(name: str, token: str) -> Callable[[InstallPlan], bool]:
    """The confirmation for :func:`install` that a yes from
    :func:`plan_yes` gives: for that machine and that plan only, once."""

    def confirm(plan: InstallPlan) -> bool:
        with _YESES_LOCK:
            yes = _YESES.pop(token, None)
        if yes is None or yes.until <= time.monotonic():
            why = "the yes given was used already, has expired or was never given"
        elif yes.machine != name:
            why = f"the yes given was for {yes.machine}"
        elif not hmac.compare_digest(yes.digest, plan_digest(plan, name)):
            why = ("the plan changed since it was shown, so the yes given is "
                   "not to this plan")
        else:
            return True
        raise StudyError(
            f"Nothing was installed on {name}: {why}. Show the plan again "
            "and confirm that one.",
            code="remote.install.unconfirmed", machine=name)

    return confirm
