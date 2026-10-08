"""Other machines from a program: the Python API.

What ``fastmdx remote`` does at a terminal, for a program and for an AI app
(``fastmdx mcp``), under the same rules (docs/remote.md):

- **Machines only from records** a person made at a terminal with
  ``fastmdx remote --machine NAME``. Nothing here inspects a machine it has
  no record of or takes ``user@host``.
- **No prompt, ever.** Every connection is made with ``BatchMode``, so a
  machine whose ``ssh`` asks for a password or a second factor fails at once
  rather than waiting on a question nobody here can answer; a key, an agent,
  or the connection ``fastmdx remote`` keeps open for ten minutes after a
  sign-in at a terminal works.
- **Confirming is the caller's.** :func:`plan_send` and :func:`fetch_sizes`
  say what a send or a fetch would do and move, so the caller can ask before
  calling :func:`send_planned` or :func:`fetch`. An install runs only with a
  yes to the exact plan :func:`install_plan` showed.
- **Only the study's own files travel**, one study runs at a time on a
  workstation, and a job is asked about at most every 30 s
  (:mod:`fastmdxplora.remote.send`).

Every function takes the job or machine by name and returns the records
:mod:`fastmdxplora.remote.jobs` keeps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastmdxplora.remote.identity import CodeIdentity, this_code
from fastmdxplora.remote.installer import (
    InstallOutcome,
    confirmed_by,
    plan_yes,
)
from fastmdxplora.remote.installer import install as _install
from fastmdxplora.remote.jobs import Job, job_names, load_job
from fastmdxplora.remote.machines import load_machine, machine_names, readiness
from fastmdxplora.remote.plan import InstallPlan
from fastmdxplora.remote.send import (
    STATUS_KEPT_S,
    FetchSizes,
    Sending,
    prepare,
)
from fastmdxplora.remote.send import cancel as _cancel
from fastmdxplora.remote.send import fetch as _fetch
from fastmdxplora.remote.send import fetch_sizes as _fetch_sizes
from fastmdxplora.remote.send import send as _send
from fastmdxplora.remote.send import status as _status
from fastmdxplora.remote.transport import Transport

__all__ = ["THIS_MACHINE", "PlannedInstall", "RunTarget", "cancel", "fetch",
           "fetch_sizes", "install", "install_plan", "jobs", "machines",
           "plan_send", "run_targets", "send", "send_planned", "status"]

#: The name the machine a program runs on goes by among the run targets.
THIS_MACHINE = "this machine"


@dataclass(frozen=True)
class RunTarget:
    """Somewhere a study can run, as last recorded: nothing is asked."""

    name: str
    #: ``here``, ``workstation`` or ``slurm``.
    kind: str
    ready: bool
    #: Why it is or is not ready, in the words ``fastmdx remote`` uses.
    summary: str
    #: Its GPUs, such as ``1x NVIDIA GeForce RTX 4090``.
    gpus: str = ""
    #: When it was last inspected, in UTC; empty for this machine.
    inspected_at: str = ""


def _link(name: str, transport: Transport | None) -> Transport:
    """The connection to ``name``, never waiting on a prompt."""
    return transport or Transport(name, interactive=False)


def _gpus(found) -> str:
    names: dict[str, int] = {}
    for gpu in found.gpus:
        names[gpu.name] = names.get(gpu.name, 0) + 1
    return ", ".join(f"{count}x {name}" for name, count in names.items())


def machines(*, code: CodeIdentity | None = None) -> list[RunTarget]:
    """The machines inspected from this computer, as recorded. Connects to
    nothing; ``fastmdx remote --machine NAME`` at a terminal adds one."""
    code = code or this_code()
    found = []
    for name in machine_names():
        machine = load_machine(name)
        verdict = readiness(machine, code)
        found.append(RunTarget(name=name, kind=machine.inspection.kind,
                               ready=verdict.ready, summary=verdict.summary,
                               gpus=_gpus(machine.inspection),
                               inspected_at=machine.inspected_at))
    return found


def run_targets(*, hosted: bool = False,
                code: CodeIdentity | None = None) -> list[RunTarget]:
    """Where a study can run: this machine, then each machine inspected.

    ``hosted`` leaves the machines out: a hosted server's ``ssh`` reaches
    its operator's machines, not its visitor's.
    """
    here = RunTarget(name=THIS_MACHINE, kind="here", ready=True,
                     summary="runs where this program runs")
    return [here] if hosted else [here, *machines(code=code)]


def plan_send(config: str | Path, machine: str, *, output: str | Path | None = None,
              partition: str = "", time_limit: str = "", force: bool = False,
              code: CodeIdentity | None = None,
              transport: Transport | None = None) -> Sending:
    """Check a study and a machine for a send, and say what it would do.

    The Config is read as ``explore -c`` reads it and the files it names are
    gathered (only those in its own folder); the machine is asked whether
    it is ready and free. Nothing is copied. :func:`describe_sending` in
    :mod:`fastmdxplora.remote.send` gives the lines to show.
    """
    load_machine(machine)  # a record, or a refusal naming the ones there are
    return prepare(config, machine, output=str(output) if output else None,
                   force=force, partition=partition, time_limit=time_limit,
                   code=code, transport=_link(machine, transport))


def send_planned(sending: Sending, *, code: CodeIdentity | None = None,
                 transport: Transport | None = None, local_runner=None) -> Job:
    """Copy a planned send across and start it."""
    return _send(sending, transport=_link(sending.machine.name, transport),
                 local_runner=local_runner, code=code)


def send(config: str | Path, machine: str, *, output: str | Path | None = None,
         partition: str = "", time_limit: str = "", force: bool = False,
         code: CodeIdentity | None = None, transport: Transport | None = None,
         local_runner=None) -> Job:
    """:func:`plan_send`, then :func:`send_planned`: for a caller that has
    already asked, or a script whose author is the person."""
    sending = plan_send(config, machine, output=output, partition=partition,
                        time_limit=time_limit, force=force, code=code,
                        transport=transport)
    return send_planned(sending, code=code, transport=transport,
                        local_runner=local_runner)


def jobs(*, under: str | Path | None = None) -> list[Job]:
    """The jobs sent from this computer, as last recorded; only those whose
    results come back inside ``under``, where given."""
    found = []
    for name in job_names():
        try:
            found.append(load_job(name))
        except (ValueError, TypeError, AttributeError):
            continue  # a record that cannot be read is not a job to offer
    if under is None:
        return found
    root = Path(under).resolve()
    # A record written before outputs were kept absolute names a folder
    # relative to wherever it was sent from, which is not known: left out.
    return [job for job in found if Path(job.local_output).is_absolute()
            and (root == Path(job.local_output).resolve()
                 or root in Path(job.local_output).resolve().parents)]


def status(job: str, *, max_age_s: float = STATUS_KEPT_S,
           transport: Transport | None = None) -> Job:
    """How ``job`` is doing: asked of its machine, unless asked less than
    ``max_age_s`` ago."""
    return _status(job, transport=_link(load_job(job).machine, transport),
                   max_age_s=max_age_s)


def fetch_sizes(job: str, *, transport: Transport | None = None) -> FetchSizes:
    """How much :func:`fetch` would bring, with and without trajectories."""
    return _fetch_sizes(job, transport=_link(load_job(job).machine, transport))


def fetch(job: str, *, with_trajectory: bool = False,
          most_bytes: int | None = None, transport: Transport | None = None,
          local_runner=None,
          code: CodeIdentity | None = None) -> tuple[Job, list[str]]:
    """Bring a finished job's results into its own output folder; the
    job's record and anything worth saying about what came back.

    ``most_bytes`` caps any one file brought, for a caller that showed the
    sizes :func:`fetch_sizes` gave before asking.
    """
    return _fetch(job, with_trajectory=with_trajectory,
                  transport=_link(load_job(job).machine, transport),
                  local_runner=local_runner, code=code, most_bytes=most_bytes)


def cancel(job: str, *, transport: Transport | None = None) -> Job:
    """Stop a job. Its folder on the machine stays."""
    return _cancel(job, transport=_link(load_job(job).machine, transport))


@dataclass(frozen=True)
class PlannedInstall:
    """An install plan, freshly made, and the yes that runs exactly it."""

    plan: InstallPlan
    #: Ready already: there is nothing to install.
    ready: bool
    #: Hand to :func:`install` once the person has said yes to ``plan``;
    #: empty where there is nothing to run.
    yes: str


def install_plan(machine: str, *, code: CodeIdentity | None = None,
                 transport: Transport | None = None) -> PlannedInstall:
    """Inspect a recorded machine again and give the plan that would make it
    ready, to show the person, with the yes for it."""
    from fastmdxplora.remote.describe import plan_for
    from fastmdxplora.remote.survey import inspect_machine

    load_machine(machine)
    code = code or this_code()
    found = inspect_machine(machine, transport=_link(machine, transport), code=code)
    plan = plan_for(found, code)
    if readiness(found, code).ready:
        return PlannedInstall(plan=plan, ready=True, yes="")
    return PlannedInstall(plan=plan, ready=False,
                          yes=plan_yes(machine, plan) if plan.possible else "")


def install(machine: str, yes: str, *, code: CodeIdentity | None = None,
            transport: Transport | None = None) -> InstallOutcome:
    """Run the plan :func:`install_plan` gave, once the person said yes.

    The machine is inspected again first; if its plan is not the one shown,
    nothing runs (``remote.install.unconfirmed``).
    """
    load_machine(machine)
    return _install(machine, confirm=confirmed_by(machine, yes),
                    transport=_link(machine, transport), code=code)
