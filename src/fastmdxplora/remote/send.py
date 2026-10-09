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

**Studies share a workstation's GPUs, where they fit.** Each send asks the
GPUs how much memory they have free and goes to one it fits on, the one
with the fewest studies from here and then the most free memory; a config
that names its own GPUs keeps them, checked for room. A study that does
not fit is refused, with the numbers
(:mod:`fastmdxplora.remote.gpu_room`). The plan says what else sent from
here is running there. ``prepare`` notes a study that does not fit, so a
dry run still shows its plan; ``send`` asks again and refuses, holding a
lock for the machine from the check until the job is recorded. A cluster's
scheduler gives each job its GPU, so there nothing is asked.

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
import logging
import os
import re
import secrets
import shlex
import stat
import tempfile
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from fastmdxplora.refusals import StudyError
from fastmdxplora.remote.gpu_room import (
    GPU_SCRIPT,
    Choice,
    Held,
    Need,
    choose,
    learn,
    need_for,
    refused_on,
    room_from,
)
from fastmdxplora.remote.identity import CodeIdentity, same_code, this_code
from fastmdxplora.remote.inputs import Inputs, gather_inputs, link_out_of, private_in, size_of
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

logger = logging.getLogger(__name__)

__all__ = ["STATUS_KEPT_S", "TRAJECTORY_PATTERNS", "FetchSizes", "Sending",
           "cancel", "describe_sending", "fetch", "fetch_sizes", "job_line",
           "job_script", "prepare", "room_said", "send", "status"]

#: How long an answer about a job is kept for a caller that asks for it.
STATUS_KEPT_S = 30.0

#: The process's file-creation mask, read once while nothing else runs.
_UMASK = os.umask(0o022)
os.umask(_UMASK)

#: How much of a job's own log a fetch brings, from its end.
JOB_LOG_KEPT = 1 << 20

#: What of a machine's answer is kept: so many lines, each so long.
_SAID_LINES, _SAID_CHARS = 12, 300

#: Whether a job's process still works for a workstation job's folder (a
#: run cancelled stops at its next frame, which can take minutes; one whose
#: script was killed goes on): one carrying the folder as ``FMDX_JOB_DIR``,
#: or the job's script, its run or a worker the run started working in it
#: (a job sent before the marker). A shell, an editor or a reader left
#: there, or a command that only names the script, is not the job's. Says
#: ``in_folder`` where one is, read from ``/proc``; ``no_proc`` where the
#: machine has none to say; ``scanned`` or ``no_folder`` where none is. Any
#: other answer was cut short.
_STILL_WORKING = (
    "here=$(cd {where} 2>/dev/null && pwd -P) || {{ echo no_folder; exit 0; }}\n"
    "[ -d /proc/self ] && command -v readlink >/dev/null 2>&1 "
    "|| {{ echo no_proc; exit 0; }}\n"
    "for d in /proc/[0-9]*; do\n"
    "  [ -r \"$d/environ\" ] || continue\n"
    "  if tr '\\000' '\\n' < \"$d/environ\" 2>/dev/null "
    "| grep -qxF \"FMDX_JOB_DIR=$here\"; then echo in_folder; exit 0; fi\n"
    "  case \"$(readlink \"$d/cwd\" 2>/dev/null)\" in\n"
    "    \"$here\"|\"$here\"/*)\n"
    "      case \"$(tr '\\000' ' ' < \"$d/cmdline\" 2>/dev/null)\" in\n"
    "        *'sh job.sh '*|*'fastmdx explore '*|*--multiprocessing-fork*)\n"
    "          echo in_folder; exit 0 ;;\n"
    "      esac ;;\n"
    "  esac\n"
    "done\n"
    "echo scanned\n")

#: How long a stop asked of what a job's run started is let run before
#: another is sent: a second TERM more than 2 s after the first stops a run
#: at once, without its checkpoint, not at its next frame.
LEFT_STOPPING_S = 600


def _by_send(send_id: str, handle: str = "") -> str:
    """Shell that finds the processes a workstation job's run started, by
    the id its send gave the run (``FMDX_SEND_ID``, which the job's script
    and its GPU reader do not carry): only this send's (a later send of the
    same name has another), and only the account's own, by the real user
    in ``status`` (a process of another user's naming the id is not, even
    to root). Sets ``proc`` (1 where ``/proc`` says), ``n`` (how many),
    ``pids`` (all) and ``outside`` (those not in the group ``handle``, read
    from ``/proc``'s stat, not ``ps``), and ``ingroup`` (1 where one is in
    it). Both are checked values: 16 hexadecimal digits, a number."""
    assert _SEND_ID.fullmatch(send_id) and (not handle or handle.isdigit())
    return (
        "proc=0; n=0; pids=''; outside=''; ingroup=0\n"
        "if [ -d /proc/self ]; then proc=1; me=$(id -u)\n"
        "for d in /proc/[0-9]*; do\n"
        "  [ -r \"$d/environ\" ] || continue\n"
        "  grep -q \"^Uid:[[:space:]]*$me[[:space:]]\" \"$d/status\" 2>/dev/null || continue\n"
        "  tr '\\000' '\\n' 2>/dev/null < \"$d/environ\" "
        f"| grep -qxF 'FMDX_SEND_ID={send_id}' || continue\n"
        "  p=${d#/proc/}; s=$(cat \"$d/stat\" 2>/dev/null); s=${s##*') '}\n"
        "  [ -n \"$s\" ] || continue; set -- $s\n"
        "  n=$((n + 1)); pids=\"$pids $p\"\n"
        f"  if [ -n '{handle}' ] && [ \"${{3:-}}\" = '{handle}' ]; then ingroup=1; "
        "else outside=\"$outside $p\"; fi\n"
        "done\n"
        "fi\n")


def _stop_by_send(send_id: str, where: str, kill: str) -> str:
    """Shell that asks to stop what carries ``send_id``, keeping the
    ledger of stops on the machine, in the job's folder: each stop is a
    directory ``.fmdx-stop-<id>-<k>``, made (atomically) before its TERM
    with hangups ignored, so a stop whose answer was lost is never sent
    again, and one that never ran is sent as the first. Within
    :data:`LEFT_STOPPING_S` of the last, by the machine's clock, nothing is
    sent (a second TERM stops a run at once, without its checkpoint).
    Expects ``n`` (what carries the id) set; ``kill`` is what sends the
    TERM. Says ``fmdx:stop=`` ``sent`` (with ``fmdx:stop_k``), ``stopping``,
    ``none`` (nothing carries the id) or ``nofolder``."""
    minutes = max(LEFT_STOPPING_S // 60, 1)
    mark = f".fmdx-stop-{send_id}"
    return (
        "trap '' HUP\n"
        f"if ! cd {shlex.quote(where)} 2>/dev/null; then echo fmdx:stop=nofolder\n"
        'elif [ "$n" = 0 ] && [ "${went:-0}" = 0 ]; then echo fmdx:stop=none\n'
        f"elif [ -n \"$(find . -maxdepth 1 -name '{mark}-*' -mmin -{minutes} "
        "2>/dev/null | head -n 1)\" ]; then echo fmdx:stop=stopping\n"
        "else\n"
        f"  k=1; while [ -e {mark}-$k ]; do k=$((k + 1)); done\n"
        f"  if ! mkdir {mark}-$k 2>/dev/null; then echo fmdx:stop=stopping\n"
        # Another cancel's stop made since this one looked: it is that one.
        f"  elif [ \"$k\" -gt 1 ] && [ -n \"$(find . -maxdepth 1 -name {mark}-$((k - 1)) "
        f"-mmin -{minutes} 2>/dev/null)\" ]; then echo fmdx:stop=stopping\n"
        f"  else {kill}\n"
        '    echo fmdx:stop=sent; echo "fmdx:stop_k=$k"\n'
        "  fi\n"
        "fi\n")


def _the_job_going(send_id: str, handle: str) -> str:
    """Shell that sets ``went`` to 1 where the job is still going: its
    script (``sh job.sh <id>``, not another process given its number after
    a restart) or a process of its run in its group. Where the machine has
    no ``/proc``, its script's number or group, as before ids."""
    return (_by_send(send_id, handle)
            + "went=0\n"
            "if [ \"$proc\" = 1 ]; then\n"
            f"  grep -q \"^Uid:[[:space:]]*$me[[:space:]]\" /proc/{handle}/status 2>/dev/null "
            f"&& case \"$(tr '\\000' ' ' 2>/dev/null < /proc/{handle}/cmdline)\" in\n"
            f"    *'job.sh {send_id} '*) went=1 ;;\n"
            "  esac\n"
            "  [ \"$ingroup\" = 1 ] && went=1\n"
            f"elif {{ kill -0 {handle} || kill -0 -{handle}; }} 2>/dev/null; then went=1\n"
            "fi\n")


#: A send's id: so many hexadecimal digits.
_SEND_ID = re.compile(r"[0-9a-f]{16}")


def _send_id(job: Job) -> str:
    """The id a workstation job's send gave its processes, or "" (a job
    sent before there were ids, or a record that says something else)."""
    given = job.extra.get("send_id")
    return given if isinstance(given, str) and _SEND_ID.fullmatch(given) else ""


#: Whether a job's process group is still there, for a machine with no
#: ``/proc``: ``alive`` where it is.
_GROUP_GOING = ("ps -A -o pgid= -o stat= 2>/dev/null | awk '$1 == {group} && "
                "$2 !~ /^Z/ {{found = 1}} END {{exit !found}}' && echo alive\n")

#: What ``fetch`` leaves on the machine unless asked: trajectories and
#: checkpoints, which are most of a run's size and are not needed to read
#: its results. Their paths on the machine are recorded.
TRAJECTORY_PATTERNS = ("*.dcd", "*.xtc", "*.trr", "*.nc", "*.chk")

#: States in which a cluster's job waits for its turn: whatever an earlier
#: run of it left in its folder says nothing of this one.
_SLURM_WAITING = frozenset({"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_HOLD",
                            "REQUEUE_FED", "RESV_DEL_HOLD", "SPECIAL_EXIT"})

#: Every state SLURM gives a job, as squeue and sacct write them.
_SLURM_WORDS = frozenset({
    "PENDING", "RUNNING", "SUSPENDED", "COMPLETED", "COMPLETING", "CANCELLED",
    "FAILED", "TIMEOUT", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE",
    "OUT_OF_MEMORY", "CONFIGURING", "RESIZING", "REQUEUED", "REQUEUE_FED",
    "REQUEUE_HOLD", "RESV_DEL_HOLD", "REVOKED", "SIGNALING", "SPECIAL_EXIT",
    "STAGE_OUT", "STOPPED", "LAUNCH_FAILED", "POWER_UP_NODE", "UPDATE_DB",
    "RECONFIG_FAIL"})

_SLURM_STATES = {
    **{state: READY for state in _SLURM_WAITING},
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
    #: Jobs sent from here the workstation is running, which this one shares
    #: it with.
    running: list[str] = field(default_factory=list)
    #: Where it runs on the workstation's GPUs, where they were asked.
    gpu: Choice | None = None
    #: What is said of the workstation's GPUs where they are not checked:
    #: their room, and why.
    room_notes: list[str] = field(default_factory=list)

    @property
    def no_room(self) -> str:
        """Why the study does not fit on the workstation's GPUs now, or ""."""
        return self.gpu.refused if self.gpu is not None else ""


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
               partition: str = "", time_limit: str = "",
               gpu: Choice | None = None) -> str:
    """The script the machine runs. Plain sh, and shown before it is sent.

    With ``gpu`` (a workstation's GPUs, asked): pinned to the GPU chosen,
    or numbered as ``nvidia-smi`` numbers them where the config names its
    own; checked again for room as it starts; and its GPU memory read every
    :data:`~fastmdxplora.remote.gpu_room.SAMPLE_EVERY_S` s, the most kept
    in ``gpu_peak``.
    """
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
                  "#SBATCH --gres=gpu:1",
                  # Run again after a preemption it would find its own run
                  # folder and stop: it ends instead, and is resumed.
                  "#SBATCH --no-requeue"]
        if partition:
            lines.append(f"#SBATCH --partition={partition}")
        if time_limit:
            lines.append(f"#SBATCH --time={time_limit}")
    lines.append(f"cd {shlex.quote(remote_dir)} || exit 1")
    if scheduler == "slurm":
        # A job started again by hand there begins with no exit code.
        lines.append("rm -f exit_code")
    if path_line:
        lines.append(path_line)
    if gpu is not None and scheduler != "slurm":
        lines += _gpu_lines(gpu)
    # Out of the folder before its exit code is there: once a job reads
    # ended, nothing of it works in the folder a send sent again may take.
    # The run and every process it starts carry the job's folder in their
    # environment: what works there is known as the job's, wherever it
    # works from.
    # The send's id, given as the script's argument: only the run carries it.
    send_id = 'FMDX_SEND_ID="${1:-}" ' if scheduler != "slurm" else ""
    lines += ['FMDX_JOB_DIR="$(pwd -P)" ' + send_id + shlex.join(command),
              f'rc=$?; cd / && echo "$rc" > {shlex.quote(remote_dir + "/exit_code")}', ""]
    return "\n".join(lines)


def _gpu_lines(gpu: Choice) -> list[str]:
    from fastmdxplora.remote import gpu_room

    # Pinned by UUID where the plan chose the GPU; where the config names
    # its own, they are left as the config means them.
    lines = [f"export CUDA_VISIBLE_DEVICES={gpu.gpu.uuid}"] if gpu.gpu is not None else []
    # Asked again as it starts: a run sent from elsewhere since the send
    # may have taken the room.
    for uuid, wanted in gpu.wanted.items():
        lines += [
            f"free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits "
            f"-i {uuid} 2>/dev/null | head -n 1 | tr -d ' ')",
            f'case "$free" in ""|*[!0-9]*) ;; *) if [ "$free" -lt {wanted} ]; '
            f'then echo "{uuid} $free {wanted}" > no_room; echo 75 > exit_code; '
            "exit 75; fi ;; esac"]
    # The most GPU memory this job's processes hold on any one GPU, read
    # until it ends: what the next study's need is learned from. It works
    # from /, so the job's folder is free of it once the job has ended.
    lines += [
        "fmdx_peak() {",
        "  d=$(pwd); cd / || return",
        "  while [ ! -f \"$d/exit_code\" ] && kill -0 \"$1\" 2>/dev/null; do",
        "    nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory "
        "--format=csv,noheader,nounits 2>/dev/null |",
        "    while IFS=', ' read -r uuid pid used rest; do",
        "      [ \"$(ps -o pgid= -p \"$pid\" 2>/dev/null | tr -d ' ')\" = \"$1\" ] "
        "&& echo \"$uuid $used\"",
        "    done | awk -v peak=\"$(cat \"$d/gpu_peak\" 2>/dev/null)\" "
        "'$2 ~ /^[0-9]+$/ {s[$1] += $2} END {peak += 0; "
        "for (u in s) if (s[u] > peak) peak = s[u]; print peak}' "
        "> \"$d/gpu_peak.part\" && mv \"$d/gpu_peak.part\" \"$d/gpu_peak\"",
        f"    sleep {gpu_room.SAMPLE_EVERY_S}",
        "  done",
        "}",
        "fmdx_peak $$ &",
    ]
    return lines


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
    running: list[Job] = []
    gpu: Choice | None = None
    room_notes: list[str] = []
    if machine.inspection.kind != "slurm":
        # An answer about a running job kept under 30 s serves here: a caller
        # asking again in a loop does not ask the machine each time. The
        # send asks again, under its lock.
        running = _running_from_here(machine_name, link, max_age_s=STATUS_KEPT_S)
        gpu, room_notes = _gpu_for(machine, link, raw, config_path.resolve().parent,
                                   running)
    else:
        room_notes = ["A cluster's scheduler gives each job its GPU, so no GPU memory is "
                      "checked here."]
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
                        time_limit=time_limit, gpu=gpu)
    sending = Sending(machine=machine, installation=env, job_name=name,
                      remote_dir=remote_dir, local_output=local_output,
                      scheduler=scheduler, config_text=config_text,
                      script=script, inputs=inputs, force=force,
                      running=[job.name for job in running], gpu=gpu,
                      room_notes=room_notes)
    if defaults_note:
        sending.notes.append(defaults_note)
    if sending.no_room:
        sending.notes.append(sending.no_room)
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


def _running_from_here(machine_name: str, link: Transport, *,
                       max_age_s: float = 0) -> list[Job]:
    """The jobs sent from here that a workstation is waiting on or running,
    each such record asked about (an answer under ``max_age_s`` old kept)."""
    from fastmdxplora.remote.jobs import job_names

    running = []
    for name in job_names():
        try:
            job = load_job(name)
        except (ValueError, TypeError, AttributeError):
            continue  # a record that cannot be read names no machine
        if job.machine != machine_name or job.state not in (READY, RUNNING):
            continue
        job = status(name, transport=link, max_age_s=max_age_s)
        if job.state in (READY, RUNNING):
            running.append(job)
    return running


def _gpu_of(job: Job) -> dict:
    """What the job's record says of its GPUs, where it says it plainly."""
    gpu = job.extra.get("gpu")
    if not isinstance(gpu, dict):
        return {}
    uuids = gpu.get("uuids")
    need = gpu.get("need_mb")
    if (not isinstance(uuids, list) or not all(isinstance(u, str) for u in uuids)
            or not (need is None or (isinstance(need, int) and not isinstance(need, bool)
                                     and 0 <= need < 10 ** 9))):
        return {}
    return gpu


def _held(running: list[Job]) -> list[Held]:
    """Each GPU a running job from here is on, with what it was expected to
    need there (0 where that was not known)."""
    held = []
    for job in running:
        gpu = _gpu_of(job)
        wanted = gpu.get("wanted")
        for uuid in gpu.get("uuids", []):
            mb = wanted.get(uuid) if isinstance(wanted, dict) else gpu.get("need_mb")
            if not (isinstance(mb, int) and not isinstance(mb, bool) and 0 < mb < 10 ** 9):
                mb = 0
            held.append(Held(job=job.name, uuid=uuid, need_mb=mb, group=job.handle))
    return held


@dataclass
class _Runs:
    """The runs a study makes, as its config gives them: what the GPU
    check needs to know of them."""

    #: Each run's system and its setup and simulation settings.
    each: list[tuple[str, dict, dict]] = field(default_factory=list)
    #: Runs at a time.
    at_once: int = 1
    #: The GPUs the config names (``nvidia-smi``'s numbers, CUDA's where the
    #: GPUs are alike), each with its runs at a time; None where it names none.
    named: Counter | None = None
    #: Why where it runs on the GPUs cannot be worked out here, or "".
    unread: str = ""
    #: Whether a simulation runs at all (setup holds a GPU briefly at most).
    simulates: bool = True


def _runs_of(config: dict, cpus: int | None) -> _Runs:
    """The study's runs, expanded as the explorer expands them."""
    import copy

    from fastmdxplora.batch.sweep import expand_runs, normalize_sweep, normalize_systems
    from fastmdxplora.config import validate_config
    from fastmdxplora.config.loader import phase_options

    continuation = _Runs(unread="a continuation runs on the GPU its study's record "
                               "names, which is not read here")
    simulation = config.get("simulation")
    if isinstance(simulation, dict) and simulation.get("resume_from"):
        return continuation
    data = copy.deepcopy(config)
    try:
        validate_config(data, require_systems=True)
        specs = expand_runs(
            systems=normalize_systems(data["systems"]),
            sweep=normalize_sweep(data["sweep"]) if data.get("sweep") is not None else None,
            base_options=phase_options(data))
    except (ValueError, TypeError, KeyError):
        return _Runs(unread="its runs could not be worked out here")
    runs = _Runs(each=[(str(spec.system), dict(spec.options.get("setup") or {}),
                        dict(spec.options.get("simulation") or {})) for spec in specs])
    include, exclude = data.get("include_phase"), data.get("exclude_phase")
    runs.simulates = (("simulation" in include) if isinstance(include, list) and include
                      else not (isinstance(exclude, list) and "simulation" in exclude))
    phases = ((set(include) if isinstance(include, list) and include
               else {"setup", "simulation", "analysis", "report"})
              - set(exclude if isinstance(exclude, list) else []))
    # The explorer pulls once to seed an umbrella's windows from the system
    # it prepares, or one the study names, unless asked only to prepare.
    # As the explorer decides it: an umbrella study (its windows expanded,
    # however the config wrote them) with a steered block pulls from a
    # prepared system it names, or from the one system it prepares for all
    # its windows (windows prepared apart, their setup swept, are seeded by
    # no pull), unless asked only to prepare.
    simulation = data.get("simulation") if isinstance(data.get("simulation"), dict) else {}
    one_setup = len({json.dumps(setup, sort_keys=True, default=str)
                     for _, setup, _ in runs.each}) <= 1
    pulls = bool(simulation.get("steered") and _an_umbrella(data) and phases != {"setup"}
                 and (simulation.get("setup_from") or simulation.get("prepared_from")
                      or ("setup" in phases and one_setup)))
    if not runs.simulates and pulls:
        # The windows do not run, and the steered pull that seeds them does,
        # as the windows are prepared: one run, on the devices the study's
        # own simulation block names, else on the GPU chosen.
        runs.simulates, runs.each = True, runs.each[:1]
        if simulation.get("device_index") in (None, ""):
            return runs
        runs.named = Counter()
        return _with_the_pull(runs, simulation, pulls)
    count = len(runs.each)
    execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    workers, devices = execution.get("workers"), execution.get("devices")
    parallel = count > 1 and (execution.get("mode") or (
        "parallel" if workers or devices else "sequential")) == "parallel"
    if parallel:
        # As the explorer resolves its workers, with the machine's cores.
        if isinstance(workers, int) and workers > 0:
            slots = workers
        elif isinstance(devices, list) and devices:
            slots = len(devices)
        else:
            slots = cpus or count
        runs.at_once = min(slots, count)
    if any(each.get("resume_from") for _, _, each in runs.each):
        continuation.at_once = runs.at_once
        return continuation
    index = re.compile(r"[0-9]{1,3}")
    if devices:
        listed = [str(d).strip() for d in devices] if isinstance(devices, list) else []
        if not listed or not all(index.fullmatch(d) for d in listed):
            runs.unread = "the config's `execution.devices` is not read here"
            return runs
        if not parallel:
            # One at a time, each on the first device listed.
            runs.named = Counter({int(listed[0]): 1})
        else:
            # As the explorer places them: each run on the listed device
            # least used for its slots (a device listed twice has two), the
            # first listed on a tie; the first runs at once are the most any
            # holds.
            slots = Counter(int(d) for d in listed)
            on_gpu = [_on_a_gpu(each) for _, _, each in runs.each]
            placed: Counter = Counter()
            taken: Counter = Counter()
            for i in range(runs.at_once):
                device = min(slots, key=lambda d: taken[d] / slots[d])
                taken[device] += 1
                # All its runs at once: each where it was placed, and one on
                # the CPU holding none of that GPU's memory. Where later runs
                # take the places of earlier ones, any may be on any.
                if on_gpu[i] or count > runs.at_once:
                    placed[device] += 1
            if any(each.get("stop_when") for _, _, each in runs.each):
                # Each round of a study run until it is determined puts run
                # i on device i of the list, round and round, as many at
                # once as the first round; the runs it extends are numbered
                # afresh, so a GPU run may take any place.
                by_place = Counter(int(listed[i % len(listed)]) for i in range(count))
                for device, there in by_place.items():
                    placed[device] = max(placed[device], min(runs.at_once, there))
            runs.named = +placed
        return _with_the_pull(runs, simulation, pulls)
    named: Counter = Counter()
    unnamed = 0
    for _, _, each in runs.each:
        if not _on_a_gpu(each):
            continue            # no GPU memory, wherever it is numbered
        given = each.get("device_index")
        if given in (None, ""):
            unnamed += 1
            continue
        parts = [part.strip() for part in str(given).split(",")]
        if not all(index.fullmatch(part) for part in parts):
            runs.unread = "the config's `device_index` is not read here"
            return runs
        for part in dict.fromkeys(parts):
            named[int(part)] += 1
    if named:
        if unnamed:
            named[0] += unnamed     # CUDA's first device, where none is named
        runs.named = Counter({i: min(n, runs.at_once) for i, n in named.items()})
        return _with_the_pull(runs, simulation, pulls)
    return runs


def _an_umbrella(expanded: dict) -> bool:
    """Whether the explorer reads a validated config as an umbrella study."""
    from fastmdxplora.simulation.umbrella import plan_from_expanded

    try:
        return plan_from_expanded(expanded) is not None
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _on_a_gpu(simulation: dict) -> bool:
    """Whether a run's simulation takes GPU memory: not on the CPU or HIP."""
    return str(simulation.get("platform") or "auto").upper() not in ("CPU", "HIP")


def _with_the_pull(runs: _Runs, simulation: object, pulls: bool) -> _Runs:
    """``runs`` with the steered pull that seeds an umbrella's windows
    counted where it runs (``pulls``: where the explorer runs one): in the
    explorer, before the windows, on the devices the study's own simulation
    block names (CUDA's first where none), whatever a system or
    ``execution.devices`` gives the windows."""
    if not pulls or not isinstance(simulation, dict) or runs.named is None:
        return runs
    given = simulation.get("device_index")
    parts = ["0"] if given in (None, "") else [p.strip() for p in str(given).split(",")]
    if not all(re.fullmatch(r"[0-9]{1,3}", part) for part in parts):
        runs.unread = "the config's `device_index` is not read here"
        runs.named = None
        return runs
    for part in dict.fromkeys(parts):
        runs.named[int(part)] = max(runs.named[int(part)], 1)
    return runs


def _gpu_for(machine: Machine, link: Transport, raw: object, folder: Path,
             running: list[Job]) -> tuple[Choice | None, list[str]]:
    """Where the study runs on the workstation's GPUs, and what is said of
    them where they are not checked."""
    name = machine.name
    config = raw if isinstance(raw, dict) else {}
    runs = _runs_of(config, machine.inspection.cpus)
    on_gpu = [each for each in runs.each if _on_a_gpu(each[2])]
    if not runs.simulates:
        return None, ["The study runs no simulation, so no GPU memory is checked "
                      "(setup holds a GPU briefly at most, placing hydrogens)."]
    if runs.each and not on_gpu:
        return None, ["The study runs on the CPU, so no GPU memory is checked."]
    if on_gpu:
        # A run on the CPU takes a slot, and no GPU memory.
        runs.at_once = min(runs.at_once, len(on_gpu))
        if runs.named is not None:
            runs.named = Counter({i: min(n, len(on_gpu)) for i, n in runs.named.items()})
    room = room_from(_read(link.run(["sh", "-s"], stdin=GPU_SCRIPT).stdout))
    if room is None:
        return None, [f"{name} has no GPU that nvidia-smi reads, so no GPU memory is "
                      "checked."]
    if not room.gpus:
        how = ("" if room.numbered_alike else
               " (where the GPUs differ, CUDA numbers them fastest first)")
        return None, [f"The account's CUDA_VISIBLE_DEVICES on {name} is {room.visible}, "
                      f"which is not read here as GPUs nvidia-smi lists{how}, so no GPU "
                      "is chosen and no GPU memory is checked."]
    held = _held(running)
    if runs.unread:
        free_lines = choose(name, room, Need(None, ""), held).lines
        return None, [*free_lines, f"The study's GPUs are its own: {runs.unread}, so no "
                      "GPU is chosen and its memory is not checked."]
    precisions = sorted({str(each[2].get("precision") or "mixed") for each in on_gpu})
    particles = _particles(on_gpu, folder)
    not_learned = ("on GPUs the config names" if runs.named is not None else
                   "with runs side by side" if runs.at_once > 1 else
                   "in more than one precision" if len(precisions) > 1 else "")
    learn = not not_learned
    needs = [need_for(name, particles, precision=p, not_learned=not_learned)
             for p in precisions]
    unknown = [n for n in needs if n.mb is None]
    need = unknown[0] if unknown else max(needs, key=lambda n: n.mb)
    if runs.named is not None:
        if room.visible is not None:
            free_lines = choose(name, room, Need(None, ""), held).lines
            return None, [*free_lines, "The config names its GPUs by number, and the "
                          f"account's CUDA_VISIBLE_DEVICES on {name} ({room.visible}) "
                          "numbers them its own way, so their memory is not checked."]
        if not room.numbered_alike:
            free_lines = choose(name, room, Need(None, ""), held).lines
            return None, [*free_lines, "The config names its GPUs by number, and the GPUs "
                          f"of {name} differ, so which card each number means is CUDA's "
                          "to say: its memory is not checked."]
        gpu = choose(name, room, need, held, named=runs.named)
    else:
        gpu = choose(name, room, need, held, at_once=runs.at_once)
        gpu.learn = learn
    gpu.precision = precisions[0]
    return gpu, []


def _particles(each: list[tuple[str, dict, dict]], folder: Path) -> int | None:
    """The most particles any of the runs is expected to simulate: those of
    the prepared system a run starts from (``setup_from``), else what setup
    is expected to build from its structure file here; ``None`` where
    either cannot be read here."""
    from fastmdxplora.setup.estimate import estimate_system

    kinds = {(system, json.dumps(setup, sort_keys=True, default=str),
              str(simulation.get("setup_from") or simulation.get("prepared_from") or ""))
             for system, setup, simulation in each}
    if not kinds or len(kinds) > 20:
        return None
    counts = []
    for system, setup, prepared in kinds:
        try:
            path = Path(prepared or system).expanduser()
        except RuntimeError:
            return None
        path = path if path.is_absolute() else folder / path
        if prepared:
            counted = _atoms_prepared(path)
            if counted is None:
                return None
            counts.append(counted)
            continue
        if not path.is_file() or path.suffix.lower() not in (".pdb", ".ent"):
            return None
        try:
            counts.append(estimate_system(path, json.loads(setup)).particles)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            return None
    return max(counts)


def _atoms_prepared(named: Path) -> int | None:
    """The atoms in the prepared system a study names, found where the
    simulation phase finds it; ``None`` where there is none to read."""
    from fastmdxplora.simulation.pipeline import where_a_prepared_system_sits

    try:
        topology = where_a_prepared_system_sits(named) / "topology.pdb"
        with topology.open(encoding="utf-8", errors="replace") as lines:
            atoms = sum(1 for line in lines if line.startswith(("ATOM", "HETATM")))
    except (OSError, ValueError, RuntimeError):
        return None
    return atoms or None


@contextmanager
def _sending_to(machine_name: str):
    """Held from the check that a workstation's GPU has room until the job
    is recorded, so two sends at once do not both take the room for one:
    in this program always, and across programs where the file system
    keeps locks (not on Windows)."""
    with _SENDERS_LOCK:
        here = _SENDERS.setdefault(machine_name, threading.Lock())
    with here, _sending_to_from_any_program(machine_name):
        yield


#: One send to a machine at a time in this program, by machine.
_SENDERS: dict[str, threading.Lock] = {}
_SENDERS_LOCK = threading.Lock()


@contextmanager
def _sending_to_from_any_program(machine_name: str):
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
    before: Job | None = None
    if name in job_names() and not sending.force:
        raise StudyError(
            f"A job called {name} was already sent from here. Give another "
            "--output, or --force-overwrite to replace it.",
            code="environment.path.exists", path=name)
    if name in job_names():
        before = load_job(name)
        if before.machine != sending.machine.name:
            # Asked about only over its own machine's connection: this one
            # knows nothing of it, and would read it gone. A job cancelled
            # there within the hour may still stop at its next frame, in a
            # folder the two machines can share.
            cancelled = before.extra.get("cancelled_at")
            if before.state in (READY, RUNNING) or (
                    before.state == ABANDONED and isinstance(cancelled, (int, float))
                    and time.time() - cancelled < 3600):
                raise StudyError(
                    f"{name} was sent to {before.machine}, and was last read "
                    f"{before.state} there; --force-overwrite replaces a job once it "
                    f"has ended. See `fastmdx remote status {name}`, cancel it first, "
                    "or give another --output.",
                    code="environment.path.exists", path=name)
            before = None
        else:
            before = status(name, transport=link)
            if before.state in (READY, RUNNING):
                raise StudyError(
                    f"{name} is still {before.state} on {before.machine}; "
                    "--force-overwrite replaces a job once it has ended. Cancel it "
                    f"first (`fastmdx remote cancel {name}`).",
                    code="environment.path.exists", path=name)
    if sending.scheduler != "slurm" and _still_working(where, before, link):
        # Its run would still be writing where the new one starts, whatever
        # the record here says of it, or with no record here at all (a job
        # of that name sent from another computer, still starting).
        raise StudyError(
            f"A process still works in {where} on {sending.machine.name}, or that "
            "could not be asked to the end" + _working_said(name, before)
            + ". Send it again once nothing works there, or give another --output.",
            code="environment.path.exists", path=name)
    if sending.scheduler == "slurm":
        _queue_free_of(sending, link)
    if sending.gpu is not None:
        _room_now(sending, link)
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
                            # The last run's exit code is set aside, and put
                            # back where the cluster does not take the job.
                            f"cd {shlex.quote(where)} || exit 1; "
                            "mv -f exit_code .exit_code.before 2>/dev/null; "
                            "if out=$(sbatch --parsable job.sh); then "
                            "rm -f .exit_code.before; echo \"$out\"; else said=$?; "
                            "mv -f .exit_code.before exit_code 2>/dev/null; "
                            "exit \"$said\"; fi"])
        handle = started.stdout.strip().split(";")[0]
    else:
        send_id = secrets.token_hex(8)
        started = link.run(["sh", "-c", (
            f"cd {shlex.quote(where)} || exit 1; "
            # The machine's own time it was sent, which only this send's
            # runs are newer than, whatever this computer's clock says.
            "rm -f exit_code no_room gpu_peak gpu_peak.part; touch .fmdx-sent; "
            # The run and every process it starts carry this send's id: what
            # it leaves going is found by it, and nothing of another send.
            "if command -v setsid >/dev/null 2>&1; then "
            f"setsid nohup sh job.sh {send_id} > job.log 2>&1 < /dev/null & "
            f"else nohup sh job.sh {send_id} > job.log 2>&1 < /dev/null & fi; echo $!")])
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
    if sending.scheduler != "slurm":
        job.extra["send_id"] = send_id
    if sending.gpu is not None and sending.scheduler != "slurm":
        # What it was expected to need on each GPU, kept back for it while it
        # starts; and whether what it holds says what one study needs.
        job.extra["gpu"] = {
            "uuids": list(sending.gpu.uuids),
            "need_mb": max(sending.gpu.wanted.values(), default=None),
            "wanted": dict(sending.gpu.wanted),
            "name": sending.gpu.gpu.name if sending.gpu.gpu is not None else "",
            "precision": sending.gpu.precision,
            "learn": sending.gpu.learn}
    save_job(job)
    return job


def left_stop_said(job: Job) -> str:
    """Where a stop of what an ended job's run left going stands: ``""``
    where none was asked from here; ``stopping`` within
    :data:`LEFT_STOPPING_S` of the last asked (another TERM now would stop
    the run at once, without its checkpoint, so none is sent); ``overdue``
    after (one sent now stops it at once, and is said so)."""
    stopped = job.extra.get("left_stopped_at")
    if not isinstance(stopped, (int, float)):
        return ""
    return "stopping" if 0 <= time.time() - stopped < LEFT_STOPPING_S else "overdue"


def left_stop_done(job: Job, asked_at: float) -> str:
    """What a stop by a job's send id asked at ``asked_at`` did, as the
    machine said: ``sent`` (asked to stop at the next frame), ``now`` (a
    later stop: at once, without a checkpoint), ``stopping`` (one was asked
    within :data:`LEFT_STOPPING_S`: nothing sent), ``none`` (nothing
    carried its id), or ``""`` (no such stop was asked)."""
    last = job.extra.get("last_stop")
    if (isinstance(last, dict) and isinstance(last.get("at"), (int, float))
            and last["at"] >= asked_at and last.get("outcome") in (
                "sent", "now", "stopping", "none")):
        return last["outcome"]
    return ""


def _working_said(name: str, before: Job | None) -> str:
    """Why a process may still work in a job's folder, as far as the
    record of the job sent from here says."""
    if before is None:
        return " (a run of a job sent there, from here or elsewhere)"
    if before.state == ABANDONED:
        return f" ({name}, cancelled from here, stops at its run's next frame)"
    if left_stop_said(before) == "stopping":
        return (f" (what {name} left going was asked to stop from here, and stops at "
                "its run's next frame)")
    if left_stop_said(before) == "overdue" and before.extra.get("left_going"):
        return (f" (what {name} left going was asked to stop from here more than "
                f"{LEFT_STOPPING_S // 60} minutes ago and still works: `fastmdx remote "
                f"cancel {name}` stops it now, without a checkpoint)")
    if before.extra.get("left_going"):
        return (f" ({name} has ended, and processes it started still work there: "
                f"`fastmdx remote cancel {name}` stops them)")
    return " (a run of a job sent there, from here or elsewhere)"


def _queue_free_of(sending: Sending, link: Transport) -> None:
    """Refuses a send to a cluster while its queue holds a job of this
    account's waiting or running in the job's folder (sent from another
    computer, or one this computer's record has lost): both would run
    there."""
    name = sending.job_name
    # A folder reached through a link (a scratch or home folder on another
    # file system) is the queue's as sbatch resolved it: each folder is
    # compared as the machine resolves it now.
    asked = link.run(["sh", "-c", (
        f"here=$(cd {shlex.quote(sending.remote_dir)} 2>/dev/null && pwd -P); "
        f"echo \"fmdx-here=$here\"; "
        f"out=$(squeue -h -u \"$(id -un)\" -n {shlex.quote(name)} -o '%i %T %Z' "
        "2>/dev/null); rc=$?; "
        "printf '%s\\n' \"$out\" | while read -r id state dir; do "
        "[ -n \"$id\" ] || continue; "
        "real=$(cd \"$dir\" 2>/dev/null && pwd -P); "
        "printf '%s %s %s\\n' \"$id\" \"$state\" \"${real:-$dir}\"; done; "
        "echo fmdx-rc=$rc")]).stdout
    lines = [line.split(None, 2) for line in asked.splitlines()]
    answered = [line for line in lines if line and line[0].startswith("fmdx-rc=")]
    here = next((line[len("fmdx-here="):] for line in asked.splitlines()
                 if line.startswith("fmdx-here=")), "")
    folders = {sending.remote_dir.rstrip("/")} | ({here.rstrip("/")} if here else set())
    if not answered or answered[-1][0] != "fmdx-rc=0":
        raise StudyError(
            f"The queue of {sending.machine.name} did not answer, so whether a job "
            f"called {name} is waiting or running there is not known. Send it again "
            "in a minute, or give another --output.",
            code="environment.path.exists", path=name)
    # Only a job working in this job's folder: one of that name elsewhere
    # is another's business.
    going = [line for line in lines if len(line) == 3 and line[1] in _SLURM_WORDS
             and _SLURM_STATES.get(line[1], RUNNING) in (READY, RUNNING)
             and line[2].rstrip("/") in folders]
    if going:
        number = going[0][0]
        raise StudyError(
            f"The queue of {sending.machine.name} holds a job called {name} "
            f"({going[0][1].lower()}) in its folder there, {sending.remote_dir}; a "
            "job is sent there again once that one has ended. Cancel it there ("
            + (f"`scancel {number}`" if usable_handle(number) else "`scancel` with its number")
            + "), or give another --output.",
            code="environment.path.exists", path=name)


def _still_working(where: str, job: Job | None, link: Transport) -> bool:
    """Whether a process still works in the job folder ``where`` on the
    machine ``link`` reaches, read from ``/proc``. Where the machine has
    none: whether ``job``, cancelled from here within the hour, still has
    its process group there (later, its number may be another's)."""
    said = link.run(["sh", "-s"], stdin=_STILL_WORKING.format(
        where=shlex.quote(where))).stdout.split()
    if "in_folder" in said or not {"no_proc", "scanned", "no_folder"} & set(said):
        # Found, or not asked to the end: nothing says the folder is free.
        return True
    cancelled = job.extra.get("cancelled_at") if job is not None else None
    if ("no_proc" not in said or job is None or job.state != ABANDONED
            or not usable_handle(job.handle) or not isinstance(cancelled, (int, float))
            or time.time() - cancelled >= 3600):
        return False
    return "alive" in link.run(["sh", "-s"], stdin=_GROUP_GOING.format(
        group=job.handle)).stdout.split()


def _room_now(sending: Sending, link: Transport) -> None:
    """Refuses a study whose GPUs do not have room for it now, asked again:
    a study sent since the plan may have taken it. The GPUs are the plan's,
    which the script names."""
    gpu = sending.gpu
    name = sending.machine.name
    if gpu is None:
        return
    # What did not fit, as the refusal says it: one run's need times the
    # runs on a GPU at once.
    wanted = max(gpu.wanted.values(), default=gpu.need.mb)
    if not gpu.uuids:
        # The plan's own refusal (a GPU the config names is not there).
        raise StudyError(gpu.refused or f"No GPU of {name} was chosen for this study.",
                         code="remote.machine.no_room", machine=name, need_mb=wanted)
    room = room_from(_read(link.run(["sh", "-s"], stdin=GPU_SCRIPT).stdout))
    if room is None:
        raise StudyError(
            f"The GPUs of {name} did not answer as the copy was to start, so the room "
            "planned for this study could not be asked again. Send it again.",
            code="remote.machine.no_room", machine=name, need_mb=wanted)
    # A kept answer serves: a job read as running only keeps its room.
    refused, short = refused_on(
        name, room, gpu, _held(_running_from_here(name, link, max_age_s=STATUS_KEPT_S)),
        again=True)
    if refused:
        # What did not fit on the GPU that refused it, as its message says.
        raise StudyError(refused, code="remote.machine.no_room", machine=name,
                         need_mb=short if short is not None else wanted)


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
        # Asked once, its output and complaints together: a state is one of
        # SLURM's words at the start of a line (a warning the queue prints is
        # not one), with the reason it gives. A queue that answers without
        # the job, or says it knows no such job, has let it go; one that
        # does not answer (a busy controller times out) says nothing of it.
        # Accounting is asked only where the queue gave no state, and its
        # word is said apart: it says only how the job was last seen.
        words = "|".join(sorted(_SLURM_WORDS))
        alive = (f'asked=$(squeue -h -j {job.handle} -o "%T %r" 2>&1; echo "fmdx-rc=$?")\n'
                 'answered=$(printf \'%s\\n\' "$asked" | sed -n \'s/^fmdx-rc=//p\' '
                 '| tail -n 1)\n'
                 f'line=$(printf \'%s\\n\' "$asked" | grep -E \'^({words})( |$)\' '
                 '| head -n 1)\n'
                 'state=$(printf \'%s\' "$line" | awk \'{print $1}\')\n'
                 'if [ "$answered" = 0 ]; then [ -z "$state" ] && echo fmdx:slurm_gone=1; '
                 'elif printf \'%s\\n\' "$asked" | grep -q "Invalid job id"; then '
                 'echo fmdx:slurm_gone=1; fi\n'
                 'echo "fmdx:slurm=$state"\n'
                 'echo "fmdx:slurm_reason=$(printf \'%s\' "$line" | awk \'{print $2}\')"\n'
                 f'[ -z "$state" ] && echo "fmdx:account=$(sacct -n -X -j {job.handle} '
                 f'-o State%30 2>/dev/null | grep -E \'^ *[A-Z_]+\' | head -n 1 '
                 '| awk \'{print $1}\')"\n')
    else:
        # The job's script, or its process group: an explorer whose script
        # was killed works on, and is the job still.
        if _send_id(job):
            # Its script by its id, or its run in its group; and once it has
            # ended, whatever its run started that works on (an explorer
            # that died leaves its runs going, in its group or out of it).
            alive = (_the_job_going(_send_id(job), job.handle)
                     + '[ "$went" = 1 ] && echo fmdx:alive=1\n'
                     'if [ "$proc" = 1 ] && { [ "$went" = 0 ] || [ -f exit_code ]; }; '
                     'then echo "fmdx:left=$n"; fi\n')
        else:
            alive = (f"{{ kill -0 {job.handle} || kill -0 -{job.handle}; }} 2>/dev/null "
                     "&& echo fmdx:alive=1\n")
    after = ""
    if _gpu_of(job):
        # Why it did not start, and the most GPU memory it held; once it has
        # ended, the particles each of its systems had. Read after the exit
        # code: the GPU reader's last reading is in by then.
        after = ("[ -f no_room ] && echo \"fmdx:no_room=$(head -c 200 no_room "
                  "| tr -d '\\n')\"\n"
                  "[ -f gpu_peak ] && echo \"fmdx:gpu_peak=$(head -c 20 gpu_peak "
                  "| tr -d '\\n')\"\n"
                  # Only this send's runs: a folder sent to again keeps others.
                  "[ -f exit_code ] && [ -d run ] && [ -f .fmdx-sent ] && "
                  "find run -name cost.json -type f -newer .fmdx-sent 2>/dev/null | head -n 50 | while IFS= read -r f; do "
                  "printf 'fmdx:cost=%s\\n' \"$(head -c 2000 \"$f\" | tr -d '\\n')\"; "
                  "done\n")
    # Whether it is going is asked before its exit code is read: a job that
    # ends between the two is then read ended with its code, never as gone
    # without one.
    return (f"cd {where} 2>/dev/null || {{ echo fmdx:gone=1; exit 0; }}\n"
            + alive +
            "[ -f exit_code ] && echo \"fmdx:exit_code=$(head -c 20 exit_code "
            "| tr -d '\\n')\"\n"
            + after +
            "[ -f run/simulation/live_status.json ] && printf 'fmdx:live=%s\\n' "
            "\"$(tr -d '\\n' < run/simulation/live_status.json)\"\n"
            "[ -f job.log ] && tail -n 12 job.log | sed 's/^/fmdx:log=/'\n")


#: Keys a machine's answer may give more lines of: the processes on its
#: GPUs, as many as ``GPU_SCRIPT`` asks for.
_MOST_LINES = {"app": 200}


def _read(text: str, *, lines: int = 50) -> dict[str, list[str]]:
    """The ``fmdx:`` lines of a machine's answer, bounded: each value at
    most 4 kB, each key at most ``lines`` lines, whatever the machine sends."""
    found: dict[str, list[str]] = {}
    # Lines end at a line feed only: a carriage return or another separator
    # Python counts as one, written into a log, does not start a line.
    for line in text.split("\n"):
        if line.startswith("fmdx:"):
            key, _, value = line[5:].partition("=")
            values = found.setdefault(key[:40], [])
            if len(values) < (_MOST_LINES.get(key[:40], lines)):
                values.append(value[:4096])
    return found


def _slurm_word(found: dict[str, list[str]], key: str) -> str:
    """A state SLURM gave under ``key``, as a word of capitals, or ""."""
    said = (found.get(key) or [""])[0].strip().split(" ")[0].rstrip("+")
    return said if re.fullmatch(r"[A-Z_]{1,40}", said) else ""


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
        job.extra.pop("left_going", None)
        job.state, job.detail = FAILED, f"{job.remote_dir} is no longer there"
        save_job(job)
        return job
    ended_with = (found.get("exit_code") or [""])[0].strip()
    if not re.fullmatch(r"-?[0-9]{1,6}", ended_with):
        ended_with = "unreadable" if ended_with else ""
    slurm = _slurm_word(found, "slurm")         # the queue's, now
    account = _slurm_word(found, "account")     # accounting's, as last seen
    gone = "slurm_gone" in found
    if job.scheduler == "slurm" and slurm in _SLURM_WAITING:
        # Waiting again (requeued): an exit code there is the last run's.
        # Why it waits is said, a hold above all, which ends only by hand.
        reason = (found.get("slurm_reason") or [""])[0].strip()
        held = reason.startswith("JobHeld")
        job.state = READY
        job.detail = slurm.lower() + (
            f", held ({reason}): it starts only once released"
            if held else f" ({reason})" if re.fullmatch(r"[A-Za-z_]{1,40}", reason)
            and reason != "None" else "")
    elif ended_with:
        job.state = DONE if ended_with == "0" else FAILED
        job.detail = "" if ended_with == "0" else f"exit code {ended_with}"
    elif job.scheduler == "slurm" and slurm:
        job.state = _SLURM_STATES.get(slurm, RUNNING)
        job.detail = slurm.lower()
    elif job.scheduler == "process" and "alive" in found:
        job.state, job.detail = RUNNING, ""
    elif job.scheduler == "slurm" and account and _SLURM_STATES.get(
            account, RUNNING) not in (READY, RUNNING):
        # Ended, as accounting saw it end.
        job.state, job.detail = _SLURM_STATES[account], account.lower()
    elif job.scheduler == "slurm" and gone and account:
        # The queue does not know it, so accounting that says it is going
        # is a record left behind (a runaway job), not the job.
        job.state = FAILED
        job.detail = ("no longer in the cluster's queue, though its accounting "
                      f"still says {account.lower()}; its log says more")
    elif job.scheduler == "slurm" and gone:
        job.state = FAILED
        job.detail = ("no longer in the cluster's queue, and the cluster keeps no "
                      "record of how it ended (a time limit, a preemption or a node "
                      "failure, or cancelled there); its log says more")
    elif job.scheduler == "slurm":
        # The queue did not answer (a busy controller times out): that says
        # nothing of the job, so its state stays.
        job.extra["queue_silent"] = True
        if job.state not in FINISHED:   # how an ended job ended stays said
            job.detail = "the cluster's queue did not answer; asked again next time"
    else:
        job.state = FAILED
        job.detail = "ended without recording an exit code (killed, or the machine restarted)"
    left = (found.get("left") or [""])[0].strip() if _send_id(job) else ""
    job.extra.pop("left_going", None)
    if job.scheduler == "process" and job.state in FINISHED and re.fullmatch(
            r"[1-9][0-9]{0,5}", left):
        job.extra["left_going"] = int(left)
        many = left != "1"
        said = (f"{left} process{'es' if many else ''} it started still "
                f"work{'' if many else 's'} there")
        them = "them" if many else "it"
        job.detail = ((job.detail + "; ") if job.detail else "") + {
            "stopping": f"{said}, asked to stop from here at its run's next frame",
            "overdue": (f"{said}, asked to stop from here more than "
                        f"{LEFT_STOPPING_S // 60} minutes ago (`fastmdx remote cancel "
                        f"{job.name}` stops {them} now, without a checkpoint)"),
        }.get(left_stop_said(job),
              f"{said} (`fastmdx remote cancel {job.name}` stops {them})")
    live = (found.get("live") or [""])[0]
    # Progress only of a run going: one waiting, or suspended, has a live
    # record only from an earlier run.
    if (job.state == RUNNING and live and slurm != "SUSPENDED"
            and not job.extra.get("queue_silent")):
        job.detail = (_progress(live) or job.detail)[:_SAID_CHARS]
    if _gpu_of(job):
        _gpu_said(job, found, ended_with)
    job.extra["log_tail"] = [line[:_SAID_CHARS] for line in
                             _telling(found.get("log", []))[-_SAID_LINES:]]
    save_job(job)
    return job


def _gpu_said(job: Job, found: dict[str, list[str]], ended_with: str) -> None:
    """What the machine said of the job's GPU: why it did not start, the
    most memory it held, and, once it has run alone on its GPU to the end,
    that memory against its particles, learned from."""
    from fastmdxplora.remote import gpu_room

    gpu = job.extra["gpu"]
    no_room = (found.get("no_room") or [""])[0].split()
    if (ended_with == "75" and len(no_room) == 3
            and all(re.fullmatch(r"[0-9]{1,9}", n) for n in no_room[1:])):
        job.detail = (f"not started: its GPU had {int(no_room[1]):,} MB free as it "
                      f"started, and it needs about {int(no_room[2]):,} MB")
    peak = (found.get("gpu_peak") or [""])[0].strip()
    if re.fullmatch(r"[0-9]{1,7}", peak) and int(peak) > 0:
        job.extra["gpu_peak_mb"] = int(peak)
    if job.state != DONE or gpu.get("learn") is not True or gpu.get("learned"):
        return
    particles, precisions, short = [], set(), False
    for text in found.get("cost", []):
        try:
            record = json.loads(text)
        except ValueError:
            continue
        # A run on the CPU holds no GPU memory: its particles say nothing
        # of what the GPU held.
        if not (isinstance(record, dict) and isinstance(record.get("particles"), int)
                and 0 < record["particles"] < 100_000_000
                and record.get("platform") in ("CUDA", "OpenCL")):
            continue
        seconds = record.get("seconds")
        # A run shorter than a few readings may have been read only while
        # its context was being made, or may have held the peak read
        # without its size being kept: nothing of the send is learned.
        short |= not (isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                      and seconds >= 3 * gpu_room.SAMPLE_EVERY_S)
        particles.append(record["particles"])
        precisions.add(str(record.get("precision") or gpu.get("precision") or "mixed"))
    held_mb = job.extra.get("gpu_peak_mb")
    # Learned only where every run says one precision: a peak is one size's.
    if particles and not short and isinstance(held_mb, int) and len(precisions) == 1:
        # Once per job sent: a name sent again is another run. A record here
        # that cannot be written leaves the job's state to be said.
        try:
            learn(job.machine, job=f"{job.name}@{job.submitted_at}",
                  particles=max(particles), peak_mb=held_mb,
                  gpu=str(gpu.get("name") or ""), precision=precisions.pop()[:20])
        except OSError as exc:
            logger.warning("What %s held on its GPU was not recorded: %s", job.name, exc)
            return
        gpu["learned"] = True


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
    if job.extra.get("queue_silent"):
        # Read as ended before, and the queue does not answer now: it may
        # still be writing what would be copied.
        raise StudyError(
            f"The cluster's queue did not answer, so whether {name} is still going is "
            f"not known (last read {job.state}). Fetch it once `fastmdx remote status "
            f"{name}` says it has ended.",
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
    # A cluster's job read as failed may have been misread (a queue that
    # did not answer read as an end, before that was told apart): asked
    # again below, at most every 30 s, as one still going is.
    if job.state in FINISHED and job.scheduler == "process":
        # What a workstation's ended job left going is stopped by its send's
        # id alone: its process number, and its group's, may be another's.
        # A cancelled job's run that still works is too, once the first
        # stop has had its time (the machine keeps when that was).
        if _send_id(job) and (job.extra.get("left_going") or job.state == ABANDONED):
            return _stop_left(job, transport or Transport(job.machine))
        return job
    if job.state in FINISHED and not (job.scheduler == "slurm" and job.state == FAILED):
        return job
    link = transport or Transport(job.machine)
    job = _status(name, link, STATUS_KEPT_S if job.state == FAILED else 0)
    if job.state in FINISHED and job.scheduler == "slurm" and job.extra.get("queue_silent"):
        # Read as failed, and the queue does not answer now: nothing says
        # whether it is still going, so nothing is signalled.
        raise StudyError(
            f"The cluster's queue did not answer, so whether {job.name} is still going "
            f"is not known (last read {job.state}"
            + (f", {job.detail}" if job.detail and "did not answer" not in job.detail
               else "")
            + "). Nothing was stopped: cancel it again in a minute, or with "
            f"`scancel {job.handle}` there.",
            code="remote.job.cancel_not_taken", job=job.name, machine=job.machine)
    # A job the machine says has ended is let be: a process's number may be
    # another's by now, and an ended job's record keeps how it ended. What it
    # left going is stopped by its send's id, which no other send carries.
    if job.state in FINISHED:
        if job.scheduler == "process" and job.extra.get("left_going") and _send_id(job):
            return _stop_left(job, link)
        return job
    if job.scheduler == "slurm":
        stopped = link.run(["scancel", job.handle])
        if stopped.returncode != 0:
            # Not stopped as far as anyone knows: its record keeps saying
            # what was last known, and it is asked about again.
            said = (stopped.stderr or stopped.stdout).strip()[-_SAID_CHARS:]
            raise StudyError(
                f"The cluster did not take the cancel of {job.name}"
                + (f": {said}" if said else "") + f". It may still be {job.state}; "
                f"cancel it again, or with `scancel {job.handle}` there.",
                code="remote.job.cancel_not_taken", job=job.name, machine=job.machine)
    elif _send_id(job):
        # Its group, where it is still the job's (its script by its id, or
        # its run in it), and whatever its run started outside it: all at
        # once, each once, the stop kept on the machine.
        said = _read(link.run(["sh", "-s"], stdin=(
            _the_job_going(_send_id(job), job.handle)
            + _stop_by_send(_send_id(job), job.remote_dir, (
                f'if [ "$went" = 1 ]; then kill -TERM -{job.handle} 2>/dev/null '
                f"|| kill -TERM {job.handle} 2>/dev/null; fi; "
                '[ -n "$outside" ] && kill -TERM $outside 2>/dev/null;')))).stdout)
        job = _stop_said(job, said)
        if job.state not in FINISHED:
            # Nothing of it was going by then: it ended since it was asked.
            return _status(job.name, link, 0)
    else:
        link.run(["sh", "-c", f"kill -TERM -{job.handle} 2>/dev/null || "
                              f"kill -TERM {job.handle} 2>/dev/null; true"])
    if job.state == ABANDONED:
        return job
    job.state, job.detail = ABANDONED, f"cancelled {now_utc()}"
    job.extra["cancelled_at"] = time.time()
    save_job(job)
    return job


def _stop_left(job: Job, link: Transport) -> Job:
    """Asks every process carrying a job's send id to stop, all at once,
    the stop kept on the machine (:func:`_stop_by_send`): within
    :data:`LEFT_STOPPING_S` of a stop asked before, nothing is sent; after
    it, one is, and stops them at once. How the job ended is kept."""
    said = _read(link.run(["sh", "-s"], stdin=(
        _by_send(_send_id(job))
        + '[ "$proc" = 1 ] || { echo fmdx:stop=unknown; exit 0; }\n'
        + _stop_by_send(_send_id(job), job.remote_dir,
                        '[ -n "$pids" ] && kill -TERM $pids 2>/dev/null;'))).stdout)
    return _stop_said(job, said)


def _stop_said(job: Job, said: dict[str, list[str]]) -> Job:
    """The record of a stop as the machine said it went: ``last_stop``
    (when here, and what: ``sent``, ``now`` for a second or later one,
    which stops a run at once, ``stopping`` or ``none``). An answer that
    does not say is refused, the record left as it was."""
    stop = (said.get("stop") or [""])[0].strip()
    k = (said.get("stop_k") or [""])[0].strip()
    if job.state == ABANDONED and stop in ("unknown", "nofolder"):
        # Asked again where nothing can be found (no /proc, its folder
        # gone): nothing was sent, and it stays as cancelled.
        return job
    if stop not in ("sent", "stopping", "none") or (
            stop == "sent" and not re.fullmatch(r"[1-9][0-9]{0,3}", k)):
        raise StudyError(
            f"{job.machine} did not say whether the stop of {job.name} was sent"
            + (" (its folder there is gone)" if stop == "nofolder" else
               " (it has no /proc to find the run's processes by)" if stop == "unknown"
               else "")
            + f". Ask `fastmdx remote status {job.name}`, and cancel it again.",
            code="remote.job.cancel_not_taken", job=job.name, machine=job.machine)
    outcome = "now" if stop == "sent" and int(k) > 1 else stop
    job.extra["last_stop"] = {"at": time.time(), "outcome": outcome}
    clause = next((part for part in job.detail.split("; ")
                   if "it started still work" in part), "")
    base = "; ".join(part for part in job.detail.split("; ") if part and part != clause)
    if job.state in (DONE, FAILED):
        # Ended: how it ended is kept, and what its run left is said.
        job.extra.pop("left_going", None)
        if outcome in ("sent", "now"):
            job.extra["left_stopped_at"] = time.time()
        elif outcome == "stopping":
            job.extra.setdefault("left_stopped_at", time.time())
        job.detail = base if outcome == "none" else (((base + "; ") if base else "") + (
            f"what it left going was asked to stop within the last "
            f"{LEFT_STOPPING_S // 60} minutes" if outcome == "stopping" else
            f"what it left going was stopped at once {now_utc()}" if outcome == "now" else
            f"what it left going was asked to stop {now_utc()}"))[:_SAID_CHARS]
    elif job.state == ABANDONED:
        if outcome == "now":
            job.detail = f"stopped at once {now_utc()}"
    elif outcome != "none":
        job.state = ABANDONED
        job.detail = (f"stopped at once {now_utc()}" if outcome == "now"
                      else f"cancelled {now_utc()}")
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
    lines += [f"  {line}" for line in room_said(sending)]
    lines += ["", "job.sh:"] + [f"  {line}" for line in sending.script.splitlines()]
    lines += [f"  {note}" for note in sending.notes]
    return lines


def room_said(sending: Sending, *, running: str = "") -> list[str]:
    """What else from here runs on the workstation, its GPUs' room, and
    where the study goes on them. ``running`` says the jobs another way
    (an AI app names only the ones it may know of)."""
    lines = []
    if sending.running:
        count = len(sending.running)
        lines.append(
            f"{sending.machine.name} is busy: {running or ', '.join(sending.running)} "
            f"sent from here {'is' if count == 1 else 'are'} running there. This study "
            "shares the machine, and each runs slower than alone.")
    gpu = sending.gpu
    if gpu is None:
        return lines + sending.room_notes
    lines += gpu.lines
    if gpu.gpu is not None and gpu.refused:
        where = f"Fits on no GPU now; GPU {gpu.gpu.index} has the most room"
    elif gpu.gpu is not None:
        where = f"Runs on GPU {gpu.gpu.index}" + (
            "" if len(gpu.lines) < 2 else
            ", where it fits with the fewest studies from here, then the most free "
            "memory" if gpu.need.mb is not None else
            ", with the fewest studies from here, then the most free memory")
    elif gpu.refused:
        where = "The GPUs the config names cannot take it now"
    else:
        where = "Runs on the GPUs the config names"
    side = max(gpu.at_once.values(), default=1)
    per_run = f"One run needs about {gpu.need.mb:,} MB ({gpu.need.how})" if (
        gpu.need.mb is not None) else f"The memory one run needs is {gpu.need.how}"
    lines += [f"{where}.", per_run + (f"; {side} run side by side on a GPU, "
                                      f"about {gpu.need.mb * side:,} MB"
                                      if side > 1 and gpu.need.mb is not None else "")
              + "."]
    if side > 1:
        lines[-1] = lines[-1].replace(" run side by side", " runs side by side")
    return lines


def _megabytes(size: int) -> str:
    return f"{size / 1e6:.1f} MB" if size >= 1_000_000 else f"{size / 1e3:.0f} kB"


def job_line(job: Job) -> str:
    detail = f", {job.detail}" if job.detail else ""
    fetched = "fetched" if job.fetched_at else "not fetched"
    return (f"  {job.name}  {job.machine}  {job.state}{detail}  "
            f"({fetched}; sent {job.submitted_at.replace('T', ' ').replace('Z', ' UTC')})")
