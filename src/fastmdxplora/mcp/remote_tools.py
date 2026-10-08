"""The tools an AI app reaches other machines with.

Over :mod:`fastmdxplora.remote.api`, so under the rules every interface
keeps: machines only from records made at a terminal, never a prompt, only
the files in a study's own folder sent, one study at a time on a
workstation, a job asked about at most every 30 s. And two of this server's
own, since what is sent leaves this computer and what comes back can be
large:

- **A study is sent only once the person says so here.** Where the AI app
  cannot put the question to them, nothing is sent: its own approval of the
  call is not enough, as it is for a study started on this machine.
- **Nothing large comes back unasked.** A fetch says each size first; where
  the AI app cannot ask, only one under 100 MB is fetched.

Jobs are those whose results come back into the workspace, so an AI app
given one folder never reads or stops a job sent for another.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

from fastmdxplora.mcp.tools import (
    Context,
    Tool,
    ToolError,
    _plan_lines,
    _unused,
    _went_ahead,
    plan_id_of,
)
from fastmdxplora.refusals import refusal_of

__all__ = ["LARGE_BYTES", "LIST_MACHINES", "REMOTE_TOOLS", "start_on_machine"]

#: What a fetch brings without asking, where the AI app cannot ask.
LARGE_BYTES = 100 * 1000 * 1000


def _size(size: int) -> str:
    if size >= 1e9:
        return f"{size / 1e9:.1f} GB"
    return f"{size / 1e6:.1f} MB" if size >= 1_000_000 else f"{size / 1e3:.0f} kB"


def _said_there(exc: BaseException, machine: str = "") -> tuple[str, str]:
    """A refusal from the remote code, with its fix, and its code."""
    found = refusal_of(exc)
    said = found.message
    if found.code == "environment.service.machine_unreachable" and machine:
        # The AI app cannot type a password, and is never given one: a
        # person signs in at a terminal, and the connection kept then is
        # used while it lasts.
        said += (f" A machine that asks for a password or a second factor is "
                 "reached from here only while FastMDXplora's own connection to it "
                 "is open: the person runs `fastmdx remote --machine "
                 f"{machine}` in a terminal and signs in there, which keeps it "
                 "open for ten minutes, then asks again within that time.")
    return said, found.code


def _refuse_there(exc: BaseException, machine: str = "") -> NoReturn:
    """Raise a refusal from the remote code as the tool's own."""
    said, code = _said_there(exc, machine)
    raise ToolError(said, code=code) from None


def _job_here(ctx: Context, given: str):
    """The job named, if its results come back into the workspace."""
    from fastmdxplora.remote import api

    for job in api.jobs(under=ctx.workspace.root):
        if job.name == given:
            return job
    raise ToolError(f"No job called {given!r} was sent with its results coming back "
                    "into this workspace. list_machines lists the jobs here.",
                    code="remote.job.unknown")


def _job_line(ctx: Context, job) -> str:
    detail = f", {job.detail}" if job.detail else ""
    fetched = ("fetched" if job.fetched_at else "not fetched")
    return (f"  {job.name} on {job.machine}: {job.state}{detail} (results to "
            f"{ctx.workspace.shown(job.local_output)}, {fetched}; sent "
            f"{job.submitted_at.replace('T', ' ').replace('Z', ' UTC')})")


# ---------------------------------------------------------------------------
# Looking
# ---------------------------------------------------------------------------
def _list_machines(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.remote import api

    found = api.machines()
    lines = ["Machines a study can be sent to, as last inspected (nothing was asked "
             "of them now):"]
    if not found:
        lines = ["No machine has been inspected from this computer. The person adds "
                 "one at a terminal: `fastmdx remote --machine <alias from "
                 "~/.ssh/config>`."]
    for machine in found:
        state = "ready" if machine.ready else "not ready"
        gpus = machine.gpus or ("no GPU on the login node" if machine.kind == "slurm"
                                else "no GPU found")
        when = machine.inspected_at.replace("T", " ").replace("Z", " UTC")
        lines.append(f"  {machine.name} ({machine.kind}, {gpus}): {state}: "
                     f"{machine.summary} (inspected {when})")
        if not machine.ready:
            lines.append(f"    The person makes it ready at a terminal: `fastmdx remote "
                         f"install --machine {machine.name}`.")
    jobs = api.jobs(under=ctx.workspace.root)
    if jobs:
        lines += ["", "Jobs sent with their results coming back here (as last asked; "
                      "remote_status asks again):"]
        lines += [_job_line(ctx, job) for job in jobs]
    if found and ctx.runs:
        lines += ["", "start_study with `machine` sends a checked study to a ready "
                      "machine, once the person agrees."]
    return "\n".join(lines)


def _remote_status(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.remote import api
    from fastmdxplora.remote.jobs import FINISHED

    if args.get("job"):
        names = [_job_here(ctx, args["job"]).name]
    else:
        names = [job.name for job in api.jobs(under=ctx.workspace.root)
                 if job.state not in FINISHED]
        if not names:
            return ("No job sent from here is waiting or running. list_machines lists "
                    "every job whose results come back into this workspace.")
    lines = []
    for name in names:
        try:
            job = api.status(name)
        except Exception as exc:  # noqa: BLE001 - one machine down, the rest said
            lines.append(f"  {name}: could not be asked: {_said_there(exc)[0]}")
            continue
        lines.append(_job_line(ctx, job))
        lines += [f"      {line}" for line in job.extra.get("log_tail", [])[-4:]]
        if job.state == "done" and not job.fetched_at:
            lines.append("    fetch_study brings its results here.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Acting
# ---------------------------------------------------------------------------
def start_on_machine(ctx: Context, file: Path, config: dict[str, Any], plan_id: str,
                     machine: str) -> str:
    """`start_study` with `machine`: the checked file sent to run there."""
    from fastmdxplora.remote import api

    requested = str(config.get("output") or file.with_suffix(""))
    where = ctx.workspace.inside(requested)
    if where is None:
        raise ToolError(f"The results folder {requested} is outside the workspace.")
    _unused(ctx, where)
    try:
        sending = api.plan_send(file, machine, output=where)
    except Exception as exc:  # noqa: BLE001 - a refusal, said as one
        _refuse_there(exc, machine)
    if sending.busy:
        # Named only where its results come back here: a job sent for
        # another folder is not this AI app's to know of.
        ours = any(job.name == sending.busy for job in api.jobs(under=ctx.workspace.root))
        raise ToolError(
            (f"{machine} is running {sending.busy}, sent from here; remote_status "
             "says when it ends, and cancel_study stops it." if ours else
             f"{machine} is running a study sent from this computer.")
            + " One study runs on a workstation at a time.",
            code="remote.machine.busy")

    shown = ctx.workspace.shown(where)
    travels = [f"  {ctx.workspace.shown(source)} ({_size(_bytes(source))})"
               for source in sending.inputs.files.values()]
    message = "\n".join([
        f"Send the study in {ctx.workspace.shown(file)} to {machine} and run it there?",
        *_plan_lines(config),
        f"Runs in: {sending.installation.path} on {machine}"
        f" ({'SLURM' if sending.scheduler == 'slurm' else 'a detached process'})",
        *(["Sent with it:", *travels] if travels else []),
        *([f"Fetched there from RCSB: {', '.join(sending.inputs.fetched)}"]
          if sending.inputs.fetched else []),
        f"Results come back to {shown} when fetched.",
    ])
    agreed = _went_ahead(ctx, "send", message,
                         f"start_study:{file}:{plan_id}:{machine}:{where}")
    if agreed is None:
        raise ToolError(
            "This AI app cannot ask the person, and a study is sent to another machine "
            "only once they agree to it here. They can send it from a terminal: "
            f"`fastmdx remote send -c {ctx.workspace.shown(file)} --machine {machine} "
            f"--output {shown}`.", code="remote.send.unconfirmed")
    if agreed is False:
        return "Not sent: the person did not go ahead."
    if ctx.call is not None and ctx.call.cancelled:
        return "Not sent: the call was cancelled."
    if plan_id_of(file) != plan_id:
        raise ToolError(f"{ctx.workspace.shown(file)} changed while the person was "
                        "asked. check_study it again and show the person that plan.")
    _unused(ctx, where)
    try:
        job = api.send_planned(sending)
    except Exception as exc:  # noqa: BLE001 - a refusal, said as one
        _refuse_there(exc, machine)
    how = "SLURM job" if job.scheduler == "slurm" else "process"
    return (f"Sent to {machine} as job {job.name} ({how} {job.handle}); it runs there "
            "on its own, whether or not this AI app stays open. remote_status says how "
            f"it is doing; fetch_study brings its results to {shown} once it has "
            f"finished; cancel_study stops it. Its folder there: {job.remote_dir}.")


def _bytes(path: Path) -> int:
    from fastmdxplora.remote.inputs import size_of

    return size_of(path)


def _fetch_study(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.remote import api

    job = _job_here(ctx, args["job"])
    with_trajectory = bool(args.get("with_trajectory"))
    try:
        job = api.status(job.name, max_age_s=0)
        if job.state in ("ready", "running"):
            return (f"{job.name} is still {job.state}"
                    + (f" ({job.detail})" if job.detail else "")
                    + ". It can be fetched once remote_status says done or failed.")
        sizes = api.fetch_sizes(job.name)
    except Exception as exc:  # noqa: BLE001 - a refusal, said as one
        _refuse_there(exc, job.machine)
    if not sizes.run_written:
        tail = job.extra.get("log_tail") or []
        return "\n".join([
            f"{job.name} ended ({job.state}"
            + (f", {job.detail}" if job.detail else "")
            + f") before its run wrote anything on {job.machine}, so there is nothing "
            "to fetch. The end of its log:", *[f"  {line}" for line in tail[-8:]]])
    bringing = sizes.bringing(with_trajectory)
    shown = ctx.workspace.shown(job.local_output)
    staying = (f"Trajectories and checkpoints come too ({sizes.trajectory_files} "
               f"files, {_size(sizes.trajectory)})." if with_trajectory else
               f"Trajectories and checkpoints stay on {job.machine} "
               f"({sizes.trajectory_files} files, {_size(sizes.trajectory)}).")
    message = (f"Fetch {job.name} from {job.machine} into {shown}?\n"
               f"Results: {_size(sizes.results)}. {staying}\n"
               f"In all: {_size(bringing)}.")
    agreed = _went_ahead(ctx, "fetch", message,
                         f"fetch_study:{job.name}:{with_trajectory}:{bringing}")
    if agreed is None and bringing >= LARGE_BYTES:
        raise ToolError(
            f"Fetching {job.name} brings {_size(bringing)}, and this AI app cannot ask "
            "the person first, so it is not fetched from here. They can fetch it from a "
            f"terminal: `fastmdx remote fetch {job.name}`"
            + (" --with-trajectory" if with_trajectory else "") + ".",
            code="remote.fetch.unconfirmed")
    if agreed is False:
        return "Not fetched: the person did not go ahead."
    if ctx.call is not None and ctx.call.cancelled:
        return "Not fetched: the call was cancelled."
    try:
        # No file larger than the whole was said to be, and a margin.
        job, warnings = api.fetch(job.name, with_trajectory=with_trajectory,
                                  most_bytes=bringing + bringing // 10 + 1_000_000)
    except Exception as exc:  # noqa: BLE001 - a refusal, said as one
        _refuse_there(exc, job.machine)
    return "\n".join([f"Fetched {job.name} into {shown} ({_size(bringing)}). "
                      f"read_study reads it.", *warnings])


def _cancel_study(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.remote import api
    from fastmdxplora.remote.jobs import FINISHED

    job = _job_here(ctx, args["job"])
    try:
        # Asked now: a record that still says running may be an hour old,
        # and an ended job's process number may be another's by now.
        job = api.status(job.name, max_age_s=0)
    except Exception as exc:  # noqa: BLE001 - a refusal, said as one
        _refuse_there(exc, job.machine)
    if job.state in FINISHED:
        return f"{job.name} has ended already ({job.state})."
    agreed = _went_ahead(ctx, "cancel", (
        f"Stop {job.name} on {job.machine}? It stops where it is; its folder there "
        "stays."), f"cancel_study:{job.name}:{job.handle}")
    if agreed is False:
        return "Not stopped: the person did not go ahead."
    if ctx.call is not None and ctx.call.cancelled:
        return "Not stopped: the call was cancelled."
    try:
        job = api.cancel(job.name)
    except Exception as exc:  # noqa: BLE001 - a refusal, said as one
        _refuse_there(exc, job.machine)
    return (f"Stopped {job.name} on {job.machine}. Its folder there stays at "
            f"{job.remote_dir}.")


_JOB = {"type": "string", "description": (
    "A job sent from here, by its name (its results folder's name), as list_machines "
    "lists it.")}

#: Listed after `stop_study`, in the order an AI app reaches for them.
REMOTE_TOOLS: tuple[Tool, ...] = (
    Tool("remote_status", "How a study on another machine is doing",
         "Ask the machine how a job sent from here is doing (waiting, running with its "
         "progress, done, failed with why) and the last lines of its log; every job "
         "still waiting or running, if none is named. An answer under 30 s old is "
         "given again rather than asked anew.",
         {"job": _JOB}, (),
         {"readOnlyHint": True, "openWorldHint": True}, _remote_status),
    Tool("fetch_study", "Fetch a study from another machine",
         "Bring a finished job's results into its results folder in the workspace, once "
         "the person agrees to what it brings: each size is said first. Trajectories "
         "and checkpoints stay on the machine unless asked for. Where the person cannot "
         "be asked, only a fetch under 100 MB goes ahead.",
         {"job": _JOB,
          "with_trajectory": {"type": "boolean", "description": (
              "Bring trajectories and checkpoints too (default false).")}},
         ("job",),
         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
          "openWorldHint": True}, _fetch_study, acts=True),
    Tool("cancel_study", "Stop a study on another machine",
         "Stop a job sent from here. Where the AI app can ask, the person is asked "
         "first; where it cannot, its own approval of the call is the gate. Its folder "
         "on the machine stays.",
         {"job": _JOB}, ("job",),
         {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True,
          "openWorldHint": True}, _cancel_study, acts=True),
)

#: Listed before `start_study`: where a study can run.
LIST_MACHINES = Tool(
    "list_machines", "List the machines a study can be sent to",
    "The machines inspected from this computer, as last recorded, each with its GPUs "
    "and whether it is ready for this computer's code; and the jobs sent from here "
    "whose results come back into the workspace. Nothing is asked of the machines. "
    "A machine is added, or made ready, by the person at a terminal.",
    {}, (), {"readOnlyHint": True, "openWorldHint": False}, _list_machines)
