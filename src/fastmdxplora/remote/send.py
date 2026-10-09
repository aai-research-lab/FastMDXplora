"""Sending a study to a machine, watching it, bringing it back, stopping it.

The machine runs the ordinary ``fastmdx explore -c`` on the config it is
sent, in the installation that holds this computer's code. Nothing about
the study changes in transit except where its input files are: they travel
under ``inputs/`` and the config's copy names them there.

**Nothing is sent to a machine that is not ready.** The machine is probed
again first -- cheaply, without asking each installation what it loads --
so a checkout that moved since the last inspection is caught here rather
than halfway through a run.

**The study is checked here before anything moves.** The config is read and
planned on this computer, so a refusal costs seconds, not a copy and a
queue wait.

**Only the study's own files travel** (:mod:`fastmdxplora.remote.inputs`),
and the links in a folder that travels are looked at again just before the
copy, which follows them.

**One study at a time on a workstation.** A job sent there holds its GPU,
not this computer's, so this computer's own one-at-a-time rule does not
cover it; a second send while one sent from here is waiting or running is
refused, the first asked about again before it is (``prepare`` notes it,
so a dry run still shows its plan; ``send`` refuses, holding a lock for
the machine from the check until the job is recorded). A cluster's
scheduler queues, so there any number may be sent.

**What comes back is written only into the job's own folder.** A link the
machine left in the run is not copied (``--no-links``), so nothing written
or read here afterwards follows one out of the folder.

**A machine is asked about a job at most every 30 s** where the caller says
so (every interface but the command line, where a person typed the
question): a page that refreshes, or an AI app asking in a loop, reads the
answer kept from the last time.

A job on a workstation runs as a detached process in its own process group,
so it outlives the connection and can be stopped whole. On a cluster it is
an ``sbatch`` job asking for one GPU. Either way it writes its exit code
beside itself when it ends, so ``status`` can tell a finished run from one
that was killed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from fastmdxplora.refusals import StudyError
from fastmdxplora.remote.identity import CodeIdentity, same_code, this_code
from fastmdxplora.remote.inputs import (Inputs, gather_inputs, link_out_of, private_in,
                                        size_of)
from fastmdxplora.remote.jobs import (
    ABANDONED,
    DONE,
    FAILED,
    FINISHED,
    READY,
    RUNNING,
    Job,
    check_job_name,
    held,
    load_job,
    save_job,
    usable_handle,
)
from fastmdxplora.remote.machines import (
    Machine,
    load_machine,
    now_utc,
    readiness,
    save_machine,
)
from fastmdxplora.remote.probe import PROBE_SCRIPT, Environment, parse_inspection
from fastmdxplora.remote.transport import Transport, run_here

__all__ = ["STATUS_KEPT_S", "TRAJECTORY_PATTERNS", "FetchSizes", "Sending",
           "cancel", "describe_sending", "fetch", "fetch_sizes", "job_line",
           "job_script", "prepare", "send", "status"]

#: How long an answer about a job is kept for a caller that asks for it.
STATUS_KEPT_S = 30.0

#: The process's file-creation mask, read once while nothing else runs.
_UMASK = os.umask(0o022)
os.umask(_UMASK)

#: How much of a job's own log a fetch brings, from its end.
JOB_LOG_KEPT = 1 << 20

#: Whether anything of a process group still runs: a member that has ended
#: and not been reaped yet (a zombie) does not count. Linux's and BSD's `ps`.
_STILL_GOING = ("ps -A -o pgid= -o stat= 2>/dev/null | "
                "awk '$1 == {group} && $2 !~ /^Z/ {{found = 1}} END {{exit !found}}'")

#: What of a machine's answer is kept: so many lines, each so long.
_SAID_LINES, _SAID_CHARS = 12, 300

#: What ``fetch`` leaves on the machine unless asked: trajectories and
#: checkpoints, which are most of a run's size and are not needed to read
#: its results. Their paths on the machine are recorded.
TRAJECTORY_PATTERNS = ("*.dcd", "*.xtc", "*.trr", "*.nc", "*.chk")

_SLURM_STATES = {
    "PENDING": READY, "CONFIGURING": READY, "REQUEUED": READY,
    "RUNNING": RUNNING, "COMPLETING": RUNNING, "SUSPENDED": RUNNING,
    "COMPLETED": DONE, "CANCELLED": ABANDONED, "FAILED": FAILED,
    "TIMEOUT": FAILED, "OUT_OF_MEMORY": FAILED, "NODE_FAIL": FAILED,
    "PREEMPTED": FAILED, "BOOT_FAIL": FAILED, "DEADLINE": FAILED,
}


@dataclass
class Sending:
    """Everything a send will do, worked out before any of it happens."""

    machine: Machine
    installation: Environment
    job_name: str
    remote_dir: str
    local_output: str
    scheduler: str
    config_text: str
    script: str
    inputs: Inputs
    force: bool = False
    name_from_time: bool = False
    notes: list[str] = field(default_factory=list)
    #: The job sent from here that the workstation is running, if any: the
    #: send is refused while it runs.
    busy: str = ""


def _refresh(machine: Machine, link: Transport) -> Machine:
    """The machine probed again, keeping what its installations reported."""
    answer = link.run(["sh", "-s"], stdin=PROBE_SCRIPT)
    found = parse_inspection(answer.stdout)
    if found is None:
        raise StudyError(
            f"{machine.name} answered the inspection with something that "
            "could not be read.",
            code="environment.service.machine_unreadable", machine=machine.name)
    machine.inspection = found
    machine.inspected_at = now_utc()
    save_machine(machine)
    return machine


def _runner_prefix(env: Environment, container: str) -> tuple[list[str], str]:
    """How the job calls fastmdx, and a PATH line for its environment."""
    if env.path.endswith(".sif"):
        return [container, "exec", "--nv", env.path, "fastmdx"], ""
    if env.path.endswith("/fastmdx"):
        return [env.path], ""
    return ([f"{env.path}/bin/fastmdx"],
            f'export PATH={shlex.quote(env.path + "/bin")}:"$PATH"')


def job_script(*, remote_dir: str, job_name: str, env: Environment,
               container: str, scheduler: str, force: bool,
               partition: str = "", time_limit: str = "") -> str:
    """The script the machine runs. Plain sh, and shown before it is sent."""
    command, path_line = _runner_prefix(env, container)
    command += ["explore", "-c", "study.yml", "--output", "run"]
    if force:
        command.append("--force-overwrite")
    # What runs there is the config sent, your defaults here already in it:
    # none found on the machine (another user's, up its folders) is laid over.
    command.append("--no-defaults")
    lines = ["#!/bin/sh"]
    if scheduler == "slurm":
        lines += [f"#SBATCH --job-name={job_name}",
                  f"#SBATCH --output={remote_dir}/job.log",
                  "#SBATCH --gres=gpu:1"]
        if partition:
            lines.append(f"#SBATCH --partition={partition}")
        if time_limit:
            lines.append(f"#SBATCH --time={time_limit}")
    lines.append(f"cd {shlex.quote(remote_dir)} || exit 1")
    if scheduler == "slurm":
        # A job the cluster runs again (requeued) starts with no exit code.
        lines.append("rm -f exit_code")
    if path_line:
        lines.append(path_line)
    lines += [shlex.join(command), "echo $? > exit_code", ""]
    return "\n".join(lines)


def prepare(config_path: str | Path, machine_name: str, *,
            output: str | None = None, force: bool = False,
            partition: str = "", time_limit: str = "",
            code: CodeIdentity | None = None,
            transport: Transport | None = None,
            require_ready: bool = True) -> Sending:
    """Check everything a send needs, and say what it would do."""
    from fastmdxplora.config import load_config_file
    from fastmdxplora.naming import default_output_name, system_of

    code = code or this_code()
    config_path = Path(config_path)
    # The same reading `explore -c` gives it: a config refused here would be
    # refused there, after the copy and the wait.
    loaded = load_config_file(str(config_path))
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    for said, value in (("--partition", partition), ("--time", time_limit)):
        if value and not re.fullmatch(r"[A-Za-z0-9_.,:+-]{1,64}", value):
            raise StudyError(
                f"{value!r} cannot be given as {said}: letters, digits and . , : + _ - "
                "only, as SLURM writes them.", code="config.option.not_permitted",
                setting=said)
    defaults_note = ""
    if isinstance(raw, dict):
        raw, defaults_note = _with_your_defaults(raw, config_path)
    # Before the machine is reached: a file that may not travel is refused
    # here, having connected to nothing.
    inputs = gather_inputs(raw, config_path.resolve().parent)

    machine = load_machine(machine_name)
    link = transport or Transport(machine_name)
    machine = _refresh(machine, link)
    verdict = readiness(machine, code)
    if require_ready and not verdict.ready:
        raise StudyError(
            f"{machine_name} is not ready for this computer's code: "
            f"{verdict.summary}. Run `fastmdx remote --machine {machine_name}` "
            "to see why, or `fastmdx remote install --machine "
            f"{machine_name}`.",
            code="remote.machine.not_ready",
            machine=machine_name, reason=verdict.summary,
        )
    busy = _busy_with(machine_name, link) if machine.inspection.kind != "slurm" else None
    candidates = (machine.inspection.holding(code)
                  or machine.inspection.installations())
    env = verdict.installation or (candidates[0] if candidates else None)
    if env is None:
        raise StudyError(
            f"{machine_name} has no FastMDXplora installation to run a study "
            "in.", code="remote.machine.not_ready",
            machine=machine_name, reason="no installation")

    if inputs.fetched and machine.inspection.internet != "yes":
        raise StudyError(
            f"{', '.join(inputs.fetched)} would be fetched from RCSB by "
            f"{machine_name}, which cannot reach the internet. Download the "
            "structure here and name the file in the config instead.",
            code="remote.input.not_available",
            given=inputs.fetched, machine=machine_name,
        )

    given = Path(output).name if output else (
        Path(str(raw.get("output"))).name if raw.get("output")
        else default_output_name(system_of(loaded)))
    try:
        name = check_job_name(given)
    except StudyError as exc:
        # The job is named after the results folder: say where that is set.
        raise StudyError(
            f"{exc} The job takes the name of the study's results folder: set "
            "`output` in the config (or give --output) to such a name.",
            code="remote.job.unusable_name", given=given) from None
    # Absolute, so the record says the same folder whatever folder a later
    # caller asks from.
    local_output = str((Path(output) if output else Path.cwd() / name).resolve())
    found = machine.inspection
    base = found.scratch or found.home
    remote_dir = f"{base}/fastmdxplora-jobs/{name}"
    scheduler = "slurm" if found.kind == "slurm" else "process"
    config_text = yaml.safe_dump(inputs.config, sort_keys=False)
    script = job_script(remote_dir=remote_dir, job_name=name, env=env,
                        container=found.container, scheduler=scheduler,
                        force=force, partition=partition,
                        time_limit=time_limit)
    sending = Sending(machine=machine, installation=env, job_name=name,
                      remote_dir=remote_dir, local_output=local_output,
                      scheduler=scheduler, config_text=config_text,
                      script=script, inputs=inputs, force=force)
    if defaults_note:
        sending.notes.append(defaults_note)
    if busy is not None:
        sending.busy = busy.name
        sending.notes.append(_busy_said(machine_name, busy))
    sending.name_from_time = not output and not raw.get("output")
    if sending.name_from_time:
        sending.notes.append(
            "The job's name comes from the time it is worked out, so a send "
            "made later gets another. Give --output to fix it.")
    if scheduler == "slurm" and not time_limit:
        sending.notes.append("No --time given, so the partition's default "
                             "limit applies.")
    return sending


def _with_your_defaults(raw: dict, config_path: Path) -> tuple[dict, str]:
    """The config with your defaults here filling what it leaves unset, as
    `explore -c` would fill it on this computer, and a line saying so."""
    from fastmdxplora.config.defaults_file import defaults_for, refused_with, with_defaults

    defaults = defaults_for(config_path.resolve().parent)
    if defaults is None:
        return raw, ""
    filled_config, filled = with_defaults(raw, defaults)
    refusal = refused_with(raw, filled_config, defaults)
    if refusal is not None:
        raise type(refusal)(str(refusal), code=refusal.code, **refusal.refusal.details)
    if not filled:
        return raw, ""
    return filled_config, (f"Your defaults ({defaults.path}) fill what the config "
                           f"leaves unset: {', '.join(filled)}.")


def _busy_with(machine_name: str, link: Transport) -> Job | None:
    """The job sent from here that a workstation is waiting on or running,
    each such record asked about there and then."""
    from fastmdxplora.remote.jobs import job_names

    for name in job_names():
        try:
            job = load_job(name)
        except (ValueError, TypeError, AttributeError):
            continue  # a record that cannot be read names no machine
        if job.machine != machine_name:
            continue
        if job.state == ABANDONED and job.scheduler == "process":
            # Cancelled, and given time to stop at a frame: busy while any of
            # its process group is still there.
            cancelled = job.extra.get("cancelled_at")
            if (isinstance(cancelled, (int, float)) and time.time() - cancelled < 3600
                    and link.run(["sh", "-c", _STILL_GOING.format(group=job.handle)]
                                 ).returncode == 0):
                return job
            continue
        if job.state not in (READY, RUNNING):
            continue
        job = status(name, transport=link)
        if job.state in (READY, RUNNING):
            return job
    return None


def _busy_said(machine_name: str, job: Job) -> str:
    if job.state == ABANDONED:
        return (f"{machine_name} is still stopping {job.name}, cancelled from here. "
                "One study runs on a workstation at a time: send again once it has "
                "stopped, in a minute or so.")
    return (f"{machine_name} is running {job.name}, sent from here"
            + (f" ({job.detail})" if job.detail else "")
            + ". One study runs on a workstation at a time: wait for it "
            f"(`fastmdx remote status {job.name}`), or stop it "
            f"(`fastmdx remote cancel {job.name}`).")


@contextmanager
def _sending_to(machine_name: str):
    """Held from the check that a workstation is free until the job is
    recorded, so two sends at once do not both find it free."""
    from fastmdxplora.remote.jobs import jobs_dir

    folder = jobs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # Windows: one sender at a time is not enforced
        yield
        return
    with open(folder / f".sending-{machine_name}.lock", "a+") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX)
        except OSError:
            # A file system that keeps no locks (an NFS home without its
            # lock service): the send goes ahead with the check alone.
            yield
            return
        try:
            yield
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)


def send(sending: Sending, *, transport: Transport | None = None,
         local_runner=None, code: CodeIdentity | None = None) -> Job:
    """Copy the study across and start it. Returns the job's record."""
    from fastmdxplora.remote.jobs import job_names

    code = code or this_code()
    link = transport or Transport(sending.machine.name)
    with _sending_to(sending.machine.name):
        return _send_held(sending, link, local_runner, code, job_names)


def _send_held(sending: Sending, link: Transport, local_runner,
               code: CodeIdentity, job_names) -> Job:
    name, where = sending.job_name, sending.remote_dir
    if name in job_names() and sending.force:
        before = status(name, transport=link)
        if before.state in (READY, RUNNING):
            raise StudyError(
                f"{name} is still {before.state} on {before.machine}; "
                "--force-overwrite replaces a job once it has ended. Cancel it first "
                f"(`fastmdx remote cancel {name}`).",
                code="environment.path.exists", path=name)
    if name in job_names() and not sending.force:
        raise StudyError(
            f"A job called {name} was already sent from here. Give another "
            "--output, or --force-overwrite to replace it.",
            code="environment.path.exists", path=name)
    busy = (_busy_with(sending.machine.name, link)
            if sending.scheduler != "slurm" else None)
    if busy is not None:
        raise StudyError(_busy_said(sending.machine.name, busy),
                         code="remote.machine.busy",
                         machine=sending.machine.name, job=busy.name)
    exists = link.run(["test", "-e", f"{where}/run"]).returncode == 0
    if exists and not sending.force:
        raise StudyError(
            f"{sending.machine.name} already holds {where}/run. Give another "
            "--output, or --force-overwrite to replace it.",
            code="environment.path.exists", path=f"{where}/run")

    # Before anything is made there, and again just before the copy: a link
    # made since `prepare` would be followed by it.
    _still_its_own(sending)
    with tempfile.TemporaryDirectory() as staging:
        stage = Path(staging)
        (stage / "study.yml").write_text(sending.config_text, encoding="utf-8")
        (stage / "job.sh").write_text(sending.script, encoding="utf-8")
        if sending.inputs.files:
            (stage / "inputs").mkdir()
            for travelled, source in sending.inputs.files.items():
                os.symlink(source, stage / "inputs" / travelled)
        link.run(["mkdir", "-p", where])
        _still_its_own(sending)
        # -L sends what the links point at: the inputs travel as files.
        copied = run_here(
            link.rsync_command(f"{staging}/", f":{where}/", "-L"),
            runner=local_runner, what="sending a study", **link.quiet_here())
        if copied != 0:
            raise StudyError(
                f"Copying the study to {sending.machine.name} failed "
                f"(rsync exit {copied}).",
                code="environment.service.machine_unreachable",
                machine=sending.machine.name, reason=f"rsync exit {copied}")

    if sending.scheduler == "slurm":
        started = link.run(["sh", "-c",
                            f"cd {shlex.quote(where)} && rm -f exit_code && "
                            "sbatch --parsable job.sh"])
        handle = started.stdout.strip().split(";")[0]
    else:
        started = link.run(["sh", "-c", (
            f"cd {shlex.quote(where)} || exit 1; rm -f exit_code; "
            "if command -v setsid >/dev/null 2>&1; then "
            "setsid nohup sh job.sh > job.log 2>&1 < /dev/null & "
            "else nohup sh job.sh > job.log 2>&1 < /dev/null & fi; echo $!")])
        handle = started.stdout.strip().splitlines()[-1] if started.stdout.strip() else ""
    if started.returncode != 0 or not usable_handle(handle):
        raise StudyError(
            f"{sending.machine.name} did not start the job: "
            f"{(started.stderr or started.stdout).strip()[-_SAID_CHARS:] or 'no answer'}",
            code="environment.service.machine_unreachable",
            machine=sending.machine.name, reason="the job did not start")

    job = Job(name=name, machine=sending.machine.name, remote_dir=where,
              scheduler=sending.scheduler, handle=handle,
              submitted_at=now_utc(),
              code={"version": code.version, "commit": code.commit,
                    "dirty": code.dirty, "checkout": code.checkout},
              local_output=sending.local_output,
              state=READY if sending.scheduler == "slurm" else RUNNING,
              extra={"installation": sending.installation.path,
                     # Where each input came from here, so the results can
                     # be tied back to it: a prepared system sent as
                     # `inputs/<name>` is recorded under that name there.
                     "inputs": {travelled: str(source) for travelled, source
                                in sending.inputs.files.items()}})
    save_job(job)
    return job


def _still_its_own(sending: Sending) -> None:
    """Refuses what travels if a link in it now leads out of its folder, or
    a place keys are kept is now in it."""
    for travelled, source in sending.inputs.files.items():
        held_to = sending.inputs.held_to.get(travelled, source)
        leading_out = link_out_of(source, held_to)
        if leading_out is not None:
            raise StudyError(
                f"{leading_out} is a link leading out of {held_to}, so "
                f"{source} is not sent: what it points at would travel with "
                "the study. Copy the file in its place.",
                code="remote.input.outside", given=str(source),
                where=f"inputs/{travelled}", folder=str(held_to))
        kept = private_in(source)
        if kept is not None:
            raise StudyError(
                f"{kept} is a place keys and credentials are kept, so {source} "
                "is not sent.", code="remote.input.outside", given=str(source),
                where=f"inputs/{travelled}", folder=str(held_to))


def _status_script(job: Job) -> str:
    where = shlex.quote(job.remote_dir)
    if job.scheduler == "slurm":
        # A queue that answers without the job, or says it knows no such
        # job, has let it go; one that does not answer (a busy controller
        # times out) says nothing of it.
        # Its state is a word in capitals on its own line: a warning the
        # queue prints (a version mismatch, a setting it does not know) is
        # not one, and is read apart.
        alive = (f'asked=$(squeue -h -j {job.handle} -o %T 2>.fmdx-queue); answered=$?\n'
                 'state=$(printf \'%s\\n\' "$asked" | grep -E \'^[A-Z_]+$\' | head -n 1)\n'
                 'if grep -q "Invalid job id" .fmdx-queue 2>/dev/null; then '
                 'echo fmdx:slurm_gone=1; '
                 'elif [ "$answered" -eq 0 ] && [ -z "$state" ]; then echo fmdx:slurm_gone=1; fi\n'
                 'rm -f .fmdx-queue\n'
                 f'[ -z "$state" ] && state=$(sacct -n -X -j {job.handle} '
                 f'-o State%30 2>/dev/null | grep -E \'^ *[A-Z_]+\' | head -n 1 '
                 '| awk \'{print $1}\')\n'
                 'echo "fmdx:slurm=$state"\n')
    else:
        alive = f"kill -0 {job.handle} 2>/dev/null && echo fmdx:alive=1\n"
    return (f"cd {where} 2>/dev/null || {{ echo fmdx:gone=1; exit 0; }}\n"
            "[ -f exit_code ] && echo \"fmdx:exit_code=$(cat exit_code)\"\n"
            + alive +
            "[ -f run/simulation/live_status.json ] && printf 'fmdx:live=%s\\n' "
            "\"$(tr -d '\\n' < run/simulation/live_status.json)\"\n"
            "[ -f job.log ] && tail -n 12 job.log | sed 's/^/fmdx:log=/'\n")


def _read(text: str) -> dict[str, list[str]]:
    """The ``fmdx:`` lines of a machine's answer, bounded: each value at
    most 4 kB, each key at most 50 lines, whatever the machine sends."""
    found: dict[str, list[str]] = {}
    # Lines end at a line feed only: a carriage return or another separator
    # Python counts as one, written into a log, does not start a line.
    for line in text.split("\n"):
        if line.startswith("fmdx:"):
            key, _, value = line[5:].partition("=")
            values = found.setdefault(key[:40], [])
            if len(values) < 50:
                values.append(value[:4096])
    return found


def _progress(live: str) -> str:
    """Where the simulation is, from its own live_status.json.

    The file gives the current stage and the step reached out of every step
    planned across the stages, so the percentage is of the whole run, and
    says so, rather than of the stage it names.
    """
    try:
        record = json.loads(live)
    except ValueError:
        return ""
    if not isinstance(record, dict):
        return ""
    stage = str(record.get("stage") or "")
    step, total = record.get("current_step"), record.get("total_planned_steps")
    if isinstance(step, (int, float)) and isinstance(total, (int, float)) and total > 0:
        overall = f"{100 * step / total:.0f}% of all steps"
        return f"{stage}, {overall}" if stage else overall
    return stage


def _telling(lines: list[str]) -> list[str]:
    """The log lines that say something: not blank, not the banner.

    The banner is drawn in punctuation, so a line with no two letters
    together is art; the tagline under it is dropped by name.
    """
    from fastmdxplora.utils.presenter import SessionPresenter

    tagline = " ".join(SessionPresenter._TAGLINE.split())
    return [line for line in lines
            if re.search(r"[A-Za-z]{2}", line)
            and " ".join(line.split()) != tagline]


def status(name: str, *, transport: Transport | None = None,
           max_age_s: float = 0) -> Job:
    """Ask the machine how a job is doing, and record the answer.

    With ``max_age_s``, an answer recorded less than that long ago is
    returned as it is, and the machine is not asked.
    """
    if max_age_s > 0:
        # A kept answer is read without waiting on a fetch of the same job.
        job = load_job(name)
        asked_at = job.extra.get("asked_at")
        if (job.state != ABANDONED and isinstance(asked_at, (int, float))
                and 0 <= time.time() - asked_at < max_age_s):
            return job
    with held(name):
        return _status(name, transport, max_age_s)


def _status(name: str, transport: Transport | None, max_age_s: float) -> Job:
    job = load_job(name)
    if job.state == ABANDONED:
        return job
    asked_at = job.extra.get("asked_at")
    if (max_age_s > 0 and isinstance(asked_at, (int, float))
            and 0 <= time.time() - asked_at < max_age_s):
        return job
    link = transport or Transport(job.machine)
    found = _read(link.run(["sh", "-s"], stdin=_status_script(job)).stdout)
    job.extra["asked_at"] = time.time()
    job.extra.pop("queue_silent", None)
    if "gone" in found:
        job.state, job.detail = FAILED, f"{job.remote_dir} is no longer there"
        save_job(job)
        return job
    ended_with = (found.get("exit_code") or [""])[0].strip()
    if not re.fullmatch(r"-?[0-9]{1,6}", ended_with):
        ended_with = "unreadable" if ended_with else ""
    slurm = (found.get("slurm") or [""])[0].strip().split(" ")[0].rstrip("+")
    if not re.fullmatch(r"[A-Z_]{1,40}", slurm):
        slurm = ""
    if ended_with:
        job.state = DONE if ended_with == "0" else FAILED
        job.detail = "" if ended_with == "0" else f"exit code {ended_with}"
    elif job.scheduler == "slurm" and slurm:
        job.state = _SLURM_STATES.get(slurm, RUNNING)
        job.detail = slurm.lower()
    elif job.scheduler == "process" and "alive" in found:
        job.state, job.detail = RUNNING, ""
    elif job.scheduler == "slurm" and "slurm_gone" in found:
        job.state = FAILED
        job.detail = ("no longer in the cluster's queue, and the cluster keeps no "
                      "record of how it ended (a time limit, or cancelled there); "
                      "its log says more")
    elif job.scheduler == "slurm":
        # Neither the queue nor its accounting answered (a busy controller
        # times out): that says nothing of the job, so its state stays.
        job.extra["queue_silent"] = True
        job.detail = "the cluster's queue did not answer; asked again next time"
    else:
        job.state = FAILED
        job.detail = "ended without recording an exit code (killed, or the machine restarted)"
    live = (found.get("live") or [""])[0]
    if job.state in (RUNNING, READY) and live:
        job.detail = (_progress(live) or job.detail)[:_SAID_CHARS]
    job.extra["log_tail"] = [line[:_SAID_CHARS] for line in
                             _telling(found.get("log", []))[-_SAID_LINES:]]
    save_job(job)
    return job


@dataclass(frozen=True)
class FetchSizes:
    """What a fetch would bring, in bytes, as the machine gives each file's size."""

    results: int
    trajectory: int
    trajectory_files: int
    #: False where the job ended before its run wrote anything: its folder
    #: holds only the job's log, which says why.
    run_written: bool = True

    def bringing(self, with_trajectory: bool) -> int:
        return self.results + (self.trajectory if with_trajectory else 0)


def _sizes_script(job: Job) -> str:
    names = " -o ".join(f"-name '{p}'" for p in TRAJECTORY_PATTERNS)
    return (f"cd {shlex.quote(job.remote_dir)} 2>/dev/null || {{ echo fmdx:gone=1; exit 0; }}\n"
            "cd run 2>/dev/null || { echo fmdx:norun=1; exit 0; }\n"
            # Bytes, from each file's own size: `du` counts the blocks it
            # takes, less than its size on a compressed or sparse file.
            "echo \"fmdx:total=$(find . -type f -exec ls -ln {} + 2>/dev/null "
            "| awk '{s+=$5} END {printf \"%.0f\\n\", s}')\"\n"
            f"echo \"fmdx:trajectory=$(find . -type f \\( {names} \\) -exec ls -ln {{}} + "
            "2>/dev/null | awk '{s+=$5} END {printf \"%.0f\\n\", s}')\"\n"
            f"echo \"fmdx:files=$(find . -type f \\( {names} \\) | wc -l | tr -d ' ')\"\n")


#: A job (its name, when, where and as what it was sent) -> when its sizes
#: were asked, and them, for callers that ask again soon. Kept only for a
#: job that had ended: a running one's sizes still grow.
_SIZES_KEPT: dict[tuple[str, str, str, str], tuple[float, FetchSizes]] = {}


def fetch_sizes(name: str, *, transport: Transport | None = None,
                max_age_s: float = 0) -> FetchSizes:
    """How much a fetch of ``name`` would bring, asked of the machine, so a
    caller can say each size before anything moves. With ``max_age_s``,
    sizes asked less than that long ago are given again."""
    job = load_job(name)
    which = (job.name, job.submitted_at, job.remote_dir, job.handle)
    kept = _SIZES_KEPT.get(which)
    if max_age_s > 0 and kept is not None and 0 <= time.time() - kept[0] < max_age_s:
        return kept[1]
    sizes = _fetch_sizes(name, transport)
    if job.state in FINISHED:
        _SIZES_KEPT[which] = (time.time(), sizes)
    else:
        _SIZES_KEPT.pop(which, None)
    return sizes


def _fetch_sizes(name: str, transport: Transport | None) -> FetchSizes:
    job = load_job(name)
    link = transport or Transport(job.machine)
    found = _read(link.run(["sh", "-s"], stdin=_sizes_script(job)).stdout)
    if "gone" in found:
        raise StudyError(
            f"{job.remote_dir} is not on {job.machine} any more, so {name} has "
            "nothing to fetch.",
            code="remote.job.gone", given=name, machine=job.machine)
    if "norun" in found:
        return FetchSizes(results=0, trajectory=0, trajectory_files=0,
                          run_written=False)

    def number(key: str) -> int:
        text = (found.get(key) or ["0"])[0].strip()
        return int(text) if re.fullmatch(r"[0-9]{1,18}", text) else 0

    trajectory = number("trajectory")
    return FetchSizes(results=max(number("total") - trajectory, 0),
                      trajectory=trajectory, trajectory_files=number("files"))


def fetch(name: str, *, with_trajectory: bool = False,
          transport: Transport | None = None, local_runner=None,
          code: CodeIdentity | None = None,
          most_bytes: int | None = None) -> tuple[Job, list[str]]:
    """Bring a job's run folder back. Returns the job and any warnings.

    ``most_bytes``, where given, is the most any one file brought may be:
    the sizes a caller showed before asking, so a machine that said less
    than it holds cannot send more in one file than the whole was said to
    be.
    """
    sent = load_job(name)  # a name never sent is refused before anything is made
    # What it said is no longer what is there once fetched.
    _SIZES_KEPT.pop((sent.name, sent.submitted_at, sent.remote_dir, sent.handle), None)
    with held(name), _fetching(name):
        return _fetch(name, with_trajectory, transport, local_runner, code,
                      most_bytes)


@contextmanager
def _fetching(name: str):
    """One fetch of a job at a time, across processes too: two would share
    the job's private folder."""
    from fastmdxplora.remote.jobs import jobs_dir

    folder = jobs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        yield
        return
    with open(folder / f".fetching-{name}.lock", "a+") as held_open:
        try:
            fcntl.flock(held_open, fcntl.LOCK_EX)
        except OSError:
            yield
            return
        try:
            yield
        finally:
            fcntl.flock(held_open, fcntl.LOCK_UN)


def _fetch(name: str, with_trajectory: bool, transport: Transport | None,
           local_runner, code: CodeIdentity | None,
           most_bytes: int | None) -> tuple[Job, list[str]]:
    link = transport or Transport(load_job(name).machine)
    # A running study is still writing, and rotating its live frames, so a
    # copy of it is a copy of nothing in particular. Asked again rather than
    # trusting the record, which may be an hour old.
    job = status(name, transport=link)
    if job.state in (READY, RUNNING):
        raise StudyError(
            f"{name} is still {job.state}"
            + (f" ({job.detail})" if job.detail else "")
            + ". Fetch it once `fastmdx remote status` says done or failed; "
            "to watch it live, tunnel to the GUI there instead.",
            code="remote.job.unfinished", given=name, state=job.state)
    target = Path(job.local_output)
    target.mkdir(parents=True, exist_ok=True)
    # A link the machine left is not copied: written through or read here,
    # it would lead out of the job's folder.
    excludes: list[str] = ["--no-links"]
    if most_bytes is not None:
        excludes.append(f"--max-size={max(int(most_bytes), 1)}")
    left: list[str] = []
    every_left: list[str] = []
    if not with_trajectory:
        for pattern in TRAJECTORY_PATTERNS:
            excludes += ["--exclude", pattern]
        found = link.run(["find", job.run_dir, "-type", "f", "(",
                          *" -o ".join(f"-name {p}" for p in TRAJECTORY_PATTERNS
                                       ).split(), ")"])
        every_left = [line for line in found.stdout.split("\n") if line.strip()]
        left = [line[:_SAID_CHARS] for line in every_left[:200]]
    over: list[str] = []
    if most_bytes is not None:
        # What the cap leaves behind is said, never left out unsaid;
        # trajectories left behind are said as such, not as over the cap.
        found_over = link.run(["find", job.run_dir, "-type", "f", "-size",
                               f"+{max(int(most_bytes), 1)}c"])
        over = [_shown(line) for line in found_over.stdout.splitlines()
                if line.strip() and line[:_SAID_CHARS] not in set(left)]

    warnings: list[str] = []
    # Copied into a folder of its own first, which only this user can enter,
    # and looked over there: nothing reaches the results folder until all of
    # it has been. Only what differs from the results folder is copied
    # (--compare-dest), and the folder is kept when a copy fails, so a fetch
    # again goes on from what came.
    try:
        staging = _private_folder(target / f".fetching-{job.name}")
        # A copy kept from a fetch with other options is not this one's.
        options = json.dumps({"excludes": excludes})
        kept = staging / ".options"
        if kept.is_file() and not kept.is_symlink() and kept.read_text() != options:
            _removed(staging)
            staging = _private_folder(target / f".fetching-{job.name}")
        _written_into(staging, ".options", options)
        arrived = staging / "run"
        arrived.mkdir(exist_ok=True)
    except OSError as exc:
        raise StudyError(
            f"{target} cannot be fetched into ({exc.strerror or exc}).",
            code="environment.path.exists", path=str(target)) from exc
    copied = run_here(link.rsync_command(f":{job.run_dir}/", f"{arrived}/",
                                         f"--compare-dest={target.resolve()}/",
                                         *excludes),
                      runner=local_runner, what="fetching a study",
                      **link.quiet_here())
    # 24 is rsync's "some source files vanished": a file removed between
    # listing and copying. Not a failed copy of what is there.
    if copied not in (0, 24):
        raise StudyError(
            f"Fetching {name} from {job.machine} failed (rsync exit {copied}); "
            "what came is kept aside, and a fetch again goes on from it.",
            code="environment.service.machine_unreachable",
            machine=job.machine, reason=f"rsync exit {copied}")
    try:
        arrived.chmod(stat.S_IRWXU)
        warnings += _only_files_and_folders(arrived)
        log_said = _job_log(job, link)
        if log_said is not None:
            _written_into(arrived, "remote_job.log", log_said)
        _clashes_refused(arrived, target)
        _moved_into(arrived, target)
    except OSError as exc:
        raise StudyError(
            f"What was fetched could not be put in {target} ({exc.strerror or exc}).",
            code="environment.path.exists", path=str(target)) from exc
    finally:
        _removed(staging)
    if over:
        warnings.append(
            f"{len(over)} file(s) larger than the whole fetch was said to be stayed "
            f"on {job.machine}: {', '.join(over[:5])}"
            + (" and more" if len(over) > 5 else "") + ".")
    if left:
        job.extra["left_on_machine"] = left
        job.extra["left_on_machine_count"] = len(every_left)
        warnings.append(
            f"{len(every_left)} trajectory and checkpoint file(s) stayed on "
            f"{job.machine} in {job.run_dir}; fetch again with "
            "--with-trajectory to bring them.")
    manifest = target / "manifest.json"
    if manifest.is_file():
        try:
            record = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError:
            record = {}
        if not isinstance(record, dict):
            record = {}
        source = record.get("source") or {}
        if not isinstance(source, dict):
            source = {}
        ran = CodeIdentity(version=str(record.get("version", "")),
                           commit=str(source.get("commit") or ""),
                           dirty=source.get("dirty", False))
        sent = CodeIdentity(**{k: job.code.get(k) for k in
                               ("version", "commit", "dirty", "checkout")})
        agreed, why = same_code(sent, ran)
        if not agreed:
            warnings.append(f"The run's manifest does not name the code that "
                            f"sent it: {why}.")
    job.fetched_at = now_utc()
    warnings += _tie_back_to_its_inputs(job, target)
    save_job(job)
    return job, warnings


def _tie_back_to_its_inputs(job: Job, target: Path) -> list[str]:
    """Record where the study's inputs are on this computer, and say which
    prepared systems its runs cannot find here.

    A run given a prepared system records it as it was named there,
    `inputs/<name>`, which on this computer names nothing: re-analysis and
    the report found no setup record for a study fetched back, and nothing
    said so. `fetched.json` maps each input to the file or folder it was
    sent from, and the run's own record, digest included, decides whether
    what is there is still it.
    """
    from fastmdxplora.refusals import CodedError
    from fastmdxplora.simulation.pipeline import FETCHED_RECORD, setup_records_of

    _written_into(target, FETCHED_RECORD, json.dumps({
        "job": job.name, "machine": job.machine, "remote_dir": job.remote_dir,
        "fetched_at": job.fetched_at, "inputs": job.extra.get("inputs") or {},
    }, indent=2))

    runs = [target] + sorted(p for p in (target / "runs").glob("*") if p.is_dir())
    warnings: list[str] = []
    for run in runs:
        record = run / "simulation" / "simulation_parameters.json"
        try:
            named = json.loads(record.read_text(encoding="utf-8")).get("prepared_system")
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(named, dict):
            continue
        where = run.relative_to(target).as_posix() if run != target else job.name
        try:
            found = setup_records_of(run)
        except CodedError as exc:
            warnings.append(f"{where}: {exc}")
            continue
        if found is None:
            warnings.append(
                f"{where} simulated the prepared system {named.get('given')!r} "
                f"from {job.machine}, and it is not on this computer: no input "
                "was sent from here under that name, or it has since moved. "
                "Re-analysis and the report read its setup record from there.")
    return warnings


def _job_log(job: Job, link: Transport) -> str | None:
    """The end of the job's own log, as text: at most its last MiB, a
    regular file only, whatever the machine answers."""
    log = shlex.quote(f"{job.remote_dir}/job.log")
    try:
        said = link.run(["sh", "-c", f"[ -f {log} ] && [ ! -L {log} ] && "
                                     f"tail -c {JOB_LOG_KEPT} {log}"])
    except StudyError:
        return None  # the results came; the log is not worth failing them
    if said.returncode != 0:
        return None
    return said.stdout[-JOB_LOG_KEPT:]


def _only_files_and_folders(arrived: Path) -> list[str]:
    """Of a fetch's copy, take out anything but files and folders (a named
    pipe would stop what reads it), clear set-id, sticky and others-may-write
    bits, and give every folder its owner's read, write and search, so
    nothing in it is left unseen; say what was taken out.

    Fails closed: a folder that still cannot be read is a refusal, since
    what is in it could not be looked at.
    """
    from fastmdxplora.config.defaults_file import DEFAULTS_FILE
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE
    from fastmdxplora.runs_here import RUNS_FILE, STARTING_FILE

    # A run's own records of where it runs are the machine's: here they
    # would name a process on this computer by the machine's number.
    theirs = {n.casefold() for n in (RUN_PROCESS_FILE, RUNS_FILE, STARTING_FILE)}
    # A defaults file would fill the settings of any study made in or below
    # the results folder here: not the machine's to set.
    defaults = DEFAULTS_FILE.casefold()
    dropped: list[str] = []
    loose = (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX | stat.S_IWOTH
             | (_UMASK & 0o077))
    removed: list[str] = []
    for _ in range(64):
        unread: list[str] = []
        opened = False
        for top, dirs, files in os.walk(arrived, followlinks=False,
                                        onerror=lambda e, into=unread: into.append(e.filename)):
            for name in dirs + files:
                entry = Path(top) / name
                try:
                    mode = entry.lstat().st_mode
                except OSError:
                    continue
                if stat.S_ISREG(mode) and name.casefold() in theirs:
                    entry.unlink(missing_ok=True)
                    continue
                if name.casefold() == defaults and not stat.S_ISDIR(mode):
                    entry.unlink(missing_ok=True)
                    dropped.append(_shown(str(entry.relative_to(arrived))))
                    continue
                if Path(top) == arrived and name.casefold().startswith(".fetching-"):
                    # The name a fetch here keeps its own copy under.
                    if stat.S_ISDIR(mode):
                        _removed(entry)
                        dirs.remove(name)
                    else:
                        entry.unlink(missing_ok=True)
                    continue
                if stat.S_ISDIR(mode):
                    wanted = (stat.S_IMODE(mode) | stat.S_IRWXU) & ~loose
                elif stat.S_ISREG(mode):
                    wanted = (stat.S_IMODE(mode) | stat.S_IRUSR | stat.S_IWUSR) & ~loose
                else:
                    entry.unlink(missing_ok=True)
                    removed.append(_shown(str(entry.relative_to(arrived))))
                    continue
                if wanted != stat.S_IMODE(mode):
                    try:
                        entry.chmod(wanted)
                        # Opened only where the mode did change: a file
                        # system that keeps its own modes is walked once.
                        if stat.S_ISDIR(mode) and stat.S_IMODE(entry.lstat().st_mode) == wanted:
                            opened = True
                    except OSError:
                        pass
        if not unread and not opened:
            break
        if unread and not opened:
            raise StudyError(
                f"{unread[0]} came back from the fetch and cannot be read here, so "
                "what is in it could not be checked; nothing was fetched.",
                code="environment.path.exists", path=str(unread[0]))
    said = []
    if removed:
        said.append(f"{len(removed)} entr{'y' if len(removed) == 1 else 'ies'} that "
                    f"{'is' if len(removed) == 1 else 'are'} neither a file nor a "
                    f"folder came back and were removed: {', '.join(removed[:5])}.")
    if dropped:
        said.append(f"{', '.join(dropped[:5])} came back and was left out: a "
                    f"{DEFAULTS_FILE} there would fill the settings of studies made "
                    "below it here.")
    return said


def _shown(text: str) -> str:
    """A name from the machine as it can be said: bounded, and with any
    byte that is not UTF-8 replaced."""
    return text[:_SAID_CHARS].encode("utf-8", "replace").decode("utf-8")


def _private_folder(folder: Path) -> Path:
    """``folder``, made or kept as a folder only this user can enter."""
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        folder.unlink()
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(stat.S_IRWXU)
    return folder


def _clashes_refused(arrived: Path, target: Path) -> None:
    """Refuse before anything moves where what the fetch brings cannot be put
    in the results folder whole: a file where a folder is, a folder where a
    link or a file is (the copy compared against what the link leads to, so
    the folder would come back part filled), or a folder of yours that
    cannot be written to."""
    for top, dirs, files in os.walk(arrived, followlinks=False):
        here = target / Path(top).relative_to(arrived)
        if here.is_dir() and not here.is_symlink() and not os.access(here, os.W_OK):
            raise StudyError(
                f"{here} cannot be written to, so the results cannot be put in it; "
                "nothing was put in place.", code="environment.path.exists",
                path=str(here))
        for name in files:
            place = here / name
            if place.is_dir() and not place.is_symlink():
                raise StudyError(
                    f"{place} is a folder here, and the fetch brings a file of that "
                    "name; nothing was put in place. Move the folder aside and fetch "
                    "again.", code="environment.path.exists", path=str(place))
        for name in dirs:
            place = here / name
            if place.is_symlink() or (place.exists() and not place.is_dir()):
                raise StudyError(
                    f"{place} is a {'link' if place.is_symlink() else 'file'} here, and "
                    "the fetch brings a folder of that name; nothing was put in place. "
                    "Move it aside and fetch again.", code="environment.path.exists",
                    path=str(place))


def _moved_into(arrived: Path, target: Path) -> None:
    """Everything under ``arrived`` moved into the same place under
    ``target``: a file replaces what is there (a link of that name is
    replaced, never written through), a folder joins a folder there."""
    for top, dirs, files in os.walk(arrived, followlinks=False):
        here = target / Path(top).relative_to(arrived)
        for name in dirs:
            place = here / name
            if place.is_symlink() or (place.exists() and not place.is_dir()):
                place.unlink()
            place.mkdir(exist_ok=True)
        for name in files:
            source = Path(top) / name
            if stat.S_ISREG(source.lstat().st_mode):  # nothing else after the sweep
                os.replace(source, here / name)


def _removed(folder: Path) -> None:
    """A fetch's own folder removed, whatever modes came back in it."""
    import shutil

    def opened(function, path, _info) -> None:
        try:
            os.chmod(os.path.dirname(path), stat.S_IRWXU)
            os.chmod(path, stat.S_IRWXU)
        except OSError:
            pass
        try:
            function(path)
        except OSError:
            pass

    shutil.rmtree(folder, onerror=opened)


def _written_into(folder: Path, name: str, text: str) -> None:
    """``text`` as the file ``name`` in ``folder``, written beside it and
    moved into place: a link of that name is replaced, never written
    through."""
    handle, scratch = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".part")
    try:
        # As any file written here would be, not mkstemp's owner-only mode.
        os.fchmod(handle, 0o666 & ~_UMASK)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(scratch, folder / name)
    except OSError as exc:
        Path(scratch).unlink(missing_ok=True)
        raise StudyError(
            f"{folder / name} could not be written ({exc.strerror or exc}): "
            "something else of that name is there.",
            code="environment.path.exists", path=str(folder / name)) from exc


def cancel(name: str, *, transport: Transport | None = None) -> Job:
    """Stop a job. Its folder on the machine is left as it is.

    The machine is asked first: a job that ended since its record was
    written is not signalled, since its process number may by now be
    another's.
    """
    with held(name):
        return _cancel(name, transport)


def _cancel(name: str, transport: Transport | None) -> Job:
    job = load_job(name)
    # A cluster's job read as failed may only have gone unanswered: asked
    # again below, as one still going is.
    if job.state in FINISHED and not (job.scheduler == "slurm" and job.state == FAILED):
        return job
    link = transport or Transport(job.machine)
    job = _status(name, link, 0)
    # A job the machine says has ended is let be: a process's number may be
    # another's by now, and an ended job's record keeps how it ended. Only a
    # cluster's job read as failed whose queue did not answer now is
    # stopped all the same, as it may still be going.
    if job.state in FINISHED and not (job.scheduler == "slurm" and job.state == FAILED
                                      and job.extra.get("queue_silent")):
        return job
    if job.scheduler == "slurm":
        link.run(["scancel", job.handle])
    else:
        link.run(["sh", "-c", f"kill -TERM -{job.handle} 2>/dev/null || "
                              f"kill -TERM {job.handle} 2>/dev/null; true"])
    job.state, job.detail = ABANDONED, f"cancelled {now_utc()}"
    job.extra["cancelled_at"] = time.time()
    save_job(job)
    return job


def describe_sending(sending: Sending) -> list[str]:
    """What a send will do, in the order it will do it."""
    lines = [f"Sending {sending.job_name} to {sending.machine.name}",
             f"  runs in      {sending.installation.path} "
             f"({sending.installation.identity.describe()})",
             f"  folder       {sending.remote_dir}",
             f"  scheduler    {'SLURM' if sending.scheduler == 'slurm' else 'a detached process'}",
             f"  results to   {sending.local_output} (with fetch)"]
    if sending.inputs.files:
        lines.append("  inputs       " + ", ".join(
            f"{src} as inputs/{name} ({_megabytes(size_of(src))})"
            for name, src in sending.inputs.files.items()))
    if sending.inputs.fetched:
        lines.append(f"  fetched there {', '.join(sending.inputs.fetched)} (from RCSB)")
    lines += ["", "job.sh:"] + [f"  {line}" for line in sending.script.splitlines()]
    lines += [f"  {note}" for note in sending.notes]
    return lines


def _megabytes(size: int) -> str:
    return f"{size / 1e6:.1f} MB" if size >= 1_000_000 else f"{size / 1e3:.0f} kB"


def job_line(job: Job) -> str:
    detail = f", {job.detail}" if job.detail else ""
    fetched = "fetched" if job.fetched_at else "not fetched"
    return (f"  {job.name}  {job.machine}  {job.state}{detail}  "
            f"({fetched}; sent {job.submitted_at.replace('T', ' ').replace('Z', ' UTC')})")
