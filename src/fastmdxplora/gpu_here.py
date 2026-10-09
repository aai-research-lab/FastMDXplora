"""This computer's GPUs, shared by the studies started on it.

A study started here (the GUI's Run, an AI app's ``start_study``, the Agent)
runs on a GPU of this computer where it fits beside those already going, as a
study sent to a workstation does (:mod:`fastmdxplora.remote.gpu_room`): each
GPU's room read with ``nvidia-smi``, the GPU with the fewest studies from here
and then the most free memory chosen and pinned by its UUID, and the memory
one run needs learned from the runs here that ran alone on their GPU to the
end (kept in the settings as ``gpu_memory_here.json``, apart from the
machines'). A study on the CPU, and every study on a computer whose GPUs
``nvidia-smi`` does not read, keeps to one at a time (:mod:`fastmdxplora.runs_here`).

What a run holds is read while it runs by a small process of its own
(:func:`sample`, started with the run and outliving whatever started it),
which learns from it once the run has completed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastmdxplora.remote import gpu_room
from fastmdxplora.remote.gpu_room import HERE, Choice, Held, Room

__all__ = ["HERE", "room_here", "choice_here", "env_for", "start_sampler", "sample"]

logger = logging.getLogger(__name__)

#: How long ``nvidia-smi`` is given to answer.
ASKED_FOR_S = 20


def _answer(script: str) -> str:
    """What this computer's ``sh`` prints for ``script``; "" where it
    cannot be asked."""
    try:
        done = subprocess.run(["sh", "-s"], input=script, capture_output=True, text=True,
                              timeout=ASKED_FOR_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout


def room_here() -> Room | None:
    """This computer's GPUs and what holds them; ``None`` where
    ``nvidia-smi`` reads none (or is not installed, so nothing is asked)."""
    from fastmdxplora.remote.send import _read

    if shutil.which("nvidia-smi") is None:
        return None
    return gpu_room.room_from(_read(_answer(gpu_room.GPU_SCRIPT)))


def choice_here(config: dict[str, Any], folder: Path,
                held: list[Held]) -> tuple[Choice | None, list[str]]:
    """Where a study runs on this computer's GPUs beside the studies
    ``held`` on them, and what is said where they are not checked; ``None``
    where it runs on no GPU chosen here (the CPU, no simulation, GPUs not
    read). Paths in the config are read from ``folder``."""
    from fastmdxplora.remote.send import gpu_choice

    return gpu_choice(HERE, room_here, config, folder, held, cpus=os.cpu_count())


def env_for(choice: Choice | None) -> dict[str, str]:
    """What a run started with ``choice`` is given: the GPU it was chosen,
    by its UUID. A config naming its own GPUs keeps them as it means them."""
    if choice is None or choice.gpu is None:
        return {}
    return {"CUDA_VISIBLE_DEVICES": choice.gpu.uuid}


def gpu_record(choice: Choice) -> dict[str, Any]:
    """What a run's start record keeps of where it runs: the GPUs and what
    it was expected to need on each, which a study starting beside it
    keeps back for it until it holds that."""
    return {"uuids": list(choice.uuids), "wanted": dict(choice.wanted),
            "need_mb": max(choice.wanted.values(), default=None),
            "name": choice.gpu.name if choice.gpu is not None else "",
            "precision": choice.precision, "learn": choice.learn}


def held_from(records: list[dict[str, Any]]) -> list[Held]:
    """The GPUs the runs going here are on, from their start records, each
    with what it was expected to need there (0 where not known)."""
    held = []
    for record in records:
        gpu = record.get("gpu")
        pid = record.get("pid")
        if not isinstance(gpu, dict) or not isinstance(pid, int) or isinstance(pid, bool):
            continue
        wanted = gpu.get("wanted") if isinstance(gpu.get("wanted"), dict) else {}
        for uuid in gpu.get("uuids") or []:
            if not isinstance(uuid, str):
                continue
            mb = wanted.get(uuid, gpu.get("need_mb"))
            if not (isinstance(mb, int) and not isinstance(mb, bool) and 0 < mb < 10 ** 9):
                mb = 0
            held.append(Held(job=str(record.get("folder") or ""), uuid=uuid, need_mb=mb,
                             group=str(pid)))
    return held


def start_sampler(group: int, folder: Path, choice: Choice, since: float) -> None:
    """Start reading what the run whose process group is ``group`` holds on
    its GPU, in a process of its own that outlives this one."""
    command = [sys.executable, "-m", "fastmdxplora.gpu_here", "--group", str(group),
               "--folder", str(folder), "--since", repr(since),
               "--gpu", (choice.gpu.name if choice.gpu is not None else "")[:80],
               "--precision", choice.precision]
    if choice.learn:
        command.append("--learn")
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         close_fds=True)
    except OSError as exc:  # its run goes on; nothing is learned from it
        logger.warning("What %s holds on its GPU is not read: %s", folder, exc)


def _going(group: int) -> bool:
    """Whether the process group is still there, and its leader not a
    process ended and not yet reaped."""
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    said = _answer(f"ps -o stat= -p {int(group)} 2>/dev/null\n").strip()
    return not said.startswith("Z")


def _held_now(group: int) -> int:
    """The most GPU memory the group's processes hold on any one GPU now,
    in MB."""
    from fastmdxplora.remote.send import _read

    on: dict[str, int] = {}
    for line in _read(_answer(gpu_room.GPU_SCRIPT), lines=200).get("app", []):
        parts = line.split()
        if (len(parts) == 4 and parts[3] == str(group) and parts[2].isdigit()):
            on[parts[0]] = on.get(parts[0], 0) + int(parts[2])
    return max(on.values(), default=0)


def _completed(folder: Path) -> bool:
    from fastmdxplora.gui.telemetry import read_study_status

    return str(read_study_status(folder).get("status") or "").lower() == "completed"


def _runs_since(folder: Path, since: float) -> list[Any]:
    """The cost records the run wrote, none older than its start."""
    records = []
    for path in sorted(folder.rglob("cost.json"))[:200]:
        try:
            if path.stat().st_mtime < since:
                continue
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return records


def sample(group: int, folder: Path, since: float, *, learn: bool, gpu: str,
           precision: str, every_s: float | None = None) -> int:
    """Read what the run's process group holds on its GPU until it ends,
    and, where it ran alone on its GPU and completed, learn from it.
    Returns the most it held, in MB."""
    every = gpu_room.SAMPLE_EVERY_S if every_s is None else every_s
    peak = 0
    while _going(group):
        peak = max(peak, _held_now(group))
        time.sleep(every)
    if not (learn and peak > 0 and _completed(folder)):
        return peak
    said = gpu_room.what_runs_say(_runs_since(folder, since), precision)
    if said is not None:
        try:
            gpu_room.learn(HERE, job=f"{folder}@{since!r}", particles=said[0],
                           peak_mb=peak, gpu=gpu, precision=said[1])
        except OSError as exc:
            logger.warning("What %s held on its GPU was not recorded: %s", folder, exc)
    return peak


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fastmdxplora.gpu_here")
    parser.add_argument("--group", type=int, required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--since", type=float, required=True)
    parser.add_argument("--gpu", default="")
    parser.add_argument("--precision", default="mixed")
    parser.add_argument("--learn", action="store_true")
    args = parser.parse_args(argv)
    if args.group <= 1:
        return 2
    sample(args.group, Path(args.folder), args.since, learn=args.learn, gpu=args.gpu,
           precision=args.precision)
    return 0


if __name__ == "__main__":  # pragma: no cover - run as its own process
    raise SystemExit(main())
