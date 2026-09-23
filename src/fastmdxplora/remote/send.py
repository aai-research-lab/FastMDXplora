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

A job on a workstation runs as a detached process in its own process group,
so it outlives the connection and can be stopped whole. On a cluster it is
an ``sbatch`` job asking for one GPU. Either way it writes its exit code
beside itself when it ends, so ``status`` can tell a finished run from one
that was killed.
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from fastmdxplora.refusals import StudyError
from fastmdxplora.remote.identity import CodeIdentity, same_code, this_code
from fastmdxplora.remote.inputs import Inputs, gather_inputs
from fastmdxplora.remote.jobs import (
    ABANDONED,
    DONE,
    FAILED,
    FINISHED,
    READY,
    RUNNING,
    Job,
    check_job_name,
    load_job,
    save_job,
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

__all__ = ["TRAJECTORY_PATTERNS", "Sending", "cancel", "describe_sending",
           "fetch", "job_line", "job_script", "prepare", "send", "status"]

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
    notes: list[str] = field(default_factory=list)


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
    candidates = (machine.inspection.holding(code)
                  or machine.inspection.installations())
    env = verdict.installation or (candidates[0] if candidates else None)
    if env is None:
        raise StudyError(
            f"{machine_name} has no FastMDXplora installation to run a study "
            "in.", code="remote.machine.not_ready",
            machine=machine_name, reason="no installation")

    inputs = gather_inputs(raw, config_path.resolve().parent)
    if inputs.fetched and machine.inspection.internet != "yes":
        raise StudyError(
            f"{', '.join(inputs.fetched)} would be fetched from RCSB by "
            f"{machine_name}, which cannot reach the internet. Download the "
            "structure here and name the file in the config instead.",
            code="remote.input.not_available",
            given=inputs.fetched, machine=machine_name,
        )

    name = check_job_name(Path(output).name if output else (
        Path(str(raw.get("output"))).name if raw.get("output")
        else default_output_name(system_of(loaded))))
    local_output = str(Path(output) if output else Path.cwd() / name)
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
    if scheduler == "slurm" and not time_limit:
        sending.notes.append("No --time given, so the partition's default "
                             "limit applies.")
    return sending


def send(sending: Sending, *, transport: Transport | None = None,
         local_runner=None, code: CodeIdentity | None = None) -> Job:
    """Copy the study across and start it. Returns the job's record."""
    from fastmdxplora.remote.jobs import job_names

    code = code or this_code()
    name, where = sending.job_name, sending.remote_dir
    link = transport or Transport(sending.machine.name)
    if name in job_names() and not sending.force:
        raise StudyError(
            f"A job called {name} was already sent from here. Give another "
            "--output, or --force-overwrite to replace it.",
            code="environment.path.exists", path=name)
    exists = link.run(["test", "-e", f"{where}/run"]).returncode == 0
    if exists and not sending.force:
        raise StudyError(
            f"{sending.machine.name} already holds {where}/run. Give another "
            "--output, or --force-overwrite to replace it.",
            code="environment.path.exists", path=f"{where}/run")

    with tempfile.TemporaryDirectory() as staging:
        stage = Path(staging)
        (stage / "study.yml").write_text(sending.config_text, encoding="utf-8")
        (stage / "job.sh").write_text(sending.script, encoding="utf-8")
        if sending.inputs.files:
            (stage / "inputs").mkdir()
            for travelled, source in sending.inputs.files.items():
                os.symlink(source, stage / "inputs" / travelled)
        link.run(["mkdir", "-p", where])
        # -L sends what the links point at: the inputs travel as files.
        copied = run_here(
            link.rsync_command(f"{staging}/", f":{where}/", "-L"),
            runner=local_runner, what="sending a study")
        if copied != 0:
            raise StudyError(
                f"Copying the study to {sending.machine.name} failed "
                f"(rsync exit {copied}).",
                code="environment.service.machine_unreachable",
                machine=sending.machine.name, reason=f"rsync exit {copied}")

    if sending.scheduler == "slurm":
        started = link.run(["sh", "-c",
                            f"cd {shlex.quote(where)} && sbatch --parsable job.sh"])
        handle = started.stdout.strip().split(";")[0]
    else:
        started = link.run(["sh", "-c", (
            f"cd {shlex.quote(where)} || exit 1; rm -f exit_code; "
            "if command -v setsid >/dev/null 2>&1; then "
            "setsid nohup sh job.sh > job.log 2>&1 < /dev/null & "
            "else nohup sh job.sh > job.log 2>&1 < /dev/null & fi; echo $!")])
        handle = started.stdout.strip().splitlines()[-1] if started.stdout.strip() else ""
    if started.returncode != 0 or not handle.isdigit():
        raise StudyError(
            f"{sending.machine.name} did not start the job: "
            f"{(started.stderr or started.stdout).strip() or 'no answer'}",
            code="environment.service.machine_unreachable",
            machine=sending.machine.name, reason="the job did not start")

    job = Job(name=name, machine=sending.machine.name, remote_dir=where,
              scheduler=sending.scheduler, handle=handle,
              submitted_at=now_utc(),
              code={"version": code.version, "commit": code.commit,
                    "dirty": code.dirty, "checkout": code.checkout},
              local_output=sending.local_output,
              state=READY if sending.scheduler == "slurm" else RUNNING,
              extra={"installation": sending.installation.path})
    save_job(job)
    return job


def _status_script(job: Job) -> str:
    where = shlex.quote(job.remote_dir)
    if job.scheduler == "slurm":
        alive = (f'state=$(squeue -h -j {job.handle} -o %T 2>/dev/null | head -n 1)\n'
                 f'[ -z "$state" ] && state=$(sacct -n -X -j {job.handle} '
                 f'-o State%30 2>/dev/null | head -n 1 | awk \'{{print $1}}\')\n'
                 'echo "fmdx:slurm=$state"\n')
    else:
        alive = f"kill -0 {job.handle} 2>/dev/null && echo fmdx:alive=1\n"
    return (f"cd {where} 2>/dev/null || {{ echo fmdx:gone=1; exit 0; }}\n"
            "[ -f exit_code ] && echo \"fmdx:exit_code=$(cat exit_code)\"\n"
            + alive +
            "[ -f run/simulation/live_status.json ] && printf 'fmdx:live=%s\\n' "
            "\"$(tr -d '\\n' < run/simulation/live_status.json)\"\n"
            "[ -f job.log ] && tail -n 3 job.log | sed 's/^/fmdx:log=/'\n")


def _read(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("fmdx:"):
            key, _, value = line[5:].partition("=")
            found.setdefault(key, []).append(value)
    return found


def status(name: str, *, transport: Transport | None = None) -> Job:
    """Ask the machine how a job is doing, and record the answer."""
    job = load_job(name)
    if job.state == ABANDONED:
        return job
    link = transport or Transport(job.machine)
    found = _read(link.run(["sh", "-s"], stdin=_status_script(job)).stdout)
    if "gone" in found:
        job.state, job.detail = FAILED, f"{job.remote_dir} is no longer there"
        save_job(job)
        return job
    exit_code = (found.get("exit_code") or [""])[0].strip()
    slurm = (found.get("slurm") or [""])[0].strip().split(" ")[0].rstrip("+")
    if exit_code:
        job.state = DONE if exit_code == "0" else FAILED
        job.detail = "" if exit_code == "0" else f"exit code {exit_code}"
    elif job.scheduler == "slurm" and slurm:
        job.state = _SLURM_STATES.get(slurm, RUNNING)
        job.detail = slurm.lower()
    elif job.scheduler == "process" and "alive" in found:
        job.state, job.detail = RUNNING, ""
    else:
        job.state = FAILED
        job.detail = "ended without recording an exit code (killed, or the machine restarted)"
    live = (found.get("live") or [""])[0]
    if job.state in (RUNNING, READY) and live:
        try:
            record = json.loads(live)
        except ValueError:
            record = {}
        percent = record.get("progress_percent")
        stage = record.get("stage") or record.get("phase") or ""
        if percent is not None:
            job.detail = f"{stage + ' ' if stage else ''}{float(percent):.0f}%"
    job.extra["log_tail"] = found.get("log", [])
    save_job(job)
    return job


def fetch(name: str, *, with_trajectory: bool = False,
          transport: Transport | None = None, local_runner=None,
          code: CodeIdentity | None = None) -> tuple[Job, list[str]]:
    """Bring a job's run folder back. Returns the job and any warnings."""
    job = load_job(name)
    link = transport or Transport(job.machine)
    target = Path(job.local_output)
    target.mkdir(parents=True, exist_ok=True)
    excludes: list[str] = []
    left: list[str] = []
    if not with_trajectory:
        for pattern in TRAJECTORY_PATTERNS:
            excludes += ["--exclude", pattern]
        found = link.run(["find", job.run_dir, "-type", "f", "(",
                          *" -o ".join(f"-name {p}" for p in TRAJECTORY_PATTERNS
                                       ).split(), ")"])
        left = [line for line in found.stdout.splitlines() if line.strip()]
    copied = run_here(link.rsync_command(f":{job.run_dir}/", f"{target}/", *excludes),
                      runner=local_runner, what="fetching a study")
    if copied != 0:
        raise StudyError(
            f"Fetching {name} from {job.machine} failed (rsync exit {copied}).",
            code="environment.service.machine_unreachable",
            machine=job.machine, reason=f"rsync exit {copied}")
    run_here(link.rsync_command(f":{job.remote_dir}/job.log",
                                f"{target}/remote_job.log"),
             runner=local_runner, what="fetching a study")

    warnings: list[str] = []
    if left:
        job.extra["left_on_machine"] = left
        warnings.append(
            f"{len(left)} trajectory and checkpoint file(s) stayed on "
            f"{job.machine} in {job.run_dir}; fetch again with "
            "--with-trajectory to bring them.")
    manifest = target / "manifest.json"
    if manifest.is_file():
        try:
            record = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError:
            record = {}
        source = record.get("source") or {}
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
    save_job(job)
    return job, warnings


def cancel(name: str, *, transport: Transport | None = None) -> Job:
    """Stop a job. Its folder on the machine is left as it is."""
    job = load_job(name)
    if job.state in FINISHED:
        return job
    link = transport or Transport(job.machine)
    if job.scheduler == "slurm":
        link.run(["scancel", job.handle])
    else:
        link.run(["sh", "-c", f"kill -TERM -{job.handle} 2>/dev/null || "
                              f"kill -TERM {job.handle} 2>/dev/null; true"])
    job.state, job.detail = ABANDONED, f"cancelled {now_utc()}"
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
            f"{src} as inputs/{name}" for name, src in sending.inputs.files.items()))
    if sending.inputs.fetched:
        lines.append(f"  fetched there {', '.join(sending.inputs.fetched)} (from RCSB)")
    lines += ["", "job.sh:"] + [f"  {line}" for line in sending.script.splitlines()]
    lines += [f"  {note}" for note in sending.notes]
    return lines


def job_line(job: Job) -> str:
    detail = f", {job.detail}" if job.detail else ""
    fetched = "fetched" if job.fetched_at else "not fetched"
    return (f"  {job.name}  {job.machine}  {job.state}{detail}  "
            f"({fetched}; sent {job.submitted_at.replace('T', ' ').replace('Z', ' UTC')})")
