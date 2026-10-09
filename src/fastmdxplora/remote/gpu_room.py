"""How much GPU memory a study needs, and whether a workstation's GPUs have it.

Studies sent to a workstation share its GPUs, so a send asks the GPUs how
much memory each has free now (``nvidia-smi``, over the connection already
open) and goes to the one with the most, pinned there by its UUID in
``CUDA_VISIBLE_DEVICES``. A study that would not fit is refused before
anything is copied, with the numbers.

**What a study needs is learned, not guessed.** Each run on a GPU has its
memory read every 15 s by its own job script, and the most it held is
recorded with the particles its system had (``cost.json``). From those
runs on that machine the need of the next is worked out: from one run, its
memory scaled up by particles (never down); from two or more of different
sizes, a straight line through them, a fixed part (the CUDA context and
OpenMM's kernels) and a part per particle. Then 15% more. Until a run on
that machine has finished, the need is not known, and the plan says so:
the GPU with the most free memory is chosen and nothing is refused.

**A run just started holds little yet.** Setup can take minutes before the
simulation takes its memory, so each study sent from here and still running
keeps back what it was expected to need less what it holds now.

Clusters are not asked: their scheduler gives each job its GPU.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["GPU_SCRIPT", "MARGIN", "SAMPLE_EVERY_S", "Choice", "Gpu", "Held", "Need",
           "Room", "choose", "learn", "measured", "need_for", "room_from",
           "still_fits"]

#: How often a job script reads its own GPU memory.
SAMPLE_EVERY_S = 15

#: Kept above what the runs measured on that machine say a study needs.
MARGIN = 1.15

#: The runs kept for each machine, the latest.
_KEPT = 20

#: A GPU's UUID as ``nvidia-smi`` gives it, the only form put in a job script.
_UUID = re.compile(r"GPU-[0-9A-Fa-f-]{8,64}")

#: Each GPU, and each process using one with the process group it is in.
GPU_SCRIPT = (
    "command -v nvidia-smi >/dev/null 2>&1 || { echo fmdx:gpus=none; exit 0; }\n"
    "nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,memory.free,"
    "utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -n 50 "
    "| sed 's/^/fmdx:gpu=/'\n"
    "nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory "
    "--format=csv,noheader,nounits 2>/dev/null | head -n 200 "
    "| while IFS=', ' read -r uuid pid used rest; do\n"
    "  echo \"fmdx:app=$uuid $pid $used $(ps -o pgid= -p \"$pid\" 2>/dev/null "
    "| tr -d ' ')\"\n"
    "done\n")


@dataclass(frozen=True)
class Gpu:
    """One GPU as ``nvidia-smi`` gave it."""

    index: int
    uuid: str
    name: str
    total_mb: int
    used_mb: int
    free_mb: int
    #: How busy its cores were, in percent; ``None`` where it does not say.
    busy_pct: int | None


@dataclass(frozen=True)
class App:
    """A process using a GPU."""

    uuid: str
    used_mb: int
    #: Its process group, or "" where the machine did not say.
    group: str


@dataclass(frozen=True)
class Room:
    """A workstation's GPUs and what uses them, now."""

    gpus: tuple[Gpu, ...]
    apps: tuple[App, ...] = ()

    def used_by(self, group: str, uuid: str) -> int:
        return sum(app.used_mb for app in self.apps
                   if group and app.group == group and app.uuid == uuid)


def _number(text: str) -> int | None:
    text = text.strip()
    return int(text) if re.fullmatch(r"[0-9]{1,9}", text) else None


def room_from(found: dict[str, list[str]]) -> Room | None:
    """The GPUs in a machine's answer to :data:`GPU_SCRIPT`, or ``None``
    where it has no ``nvidia-smi`` or no GPU could be read."""
    if "gpus" in found:
        return None
    gpus = []
    for line in found.get("gpu", []):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 7:
            continue
        index, total, used, free = (_number(parts[0]), _number(parts[-4]),
                                    _number(parts[-3]), _number(parts[-2]))
        if (index is None or total is None or used is None or free is None
                or not _UUID.fullmatch(parts[1])):
            continue
        gpus.append(Gpu(index=index, uuid=parts[1],
                        name=", ".join(parts[2:-4])[:80] or "GPU",
                        total_mb=total, used_mb=used, free_mb=free,
                        busy_pct=_number(parts[-1])))
    apps = []
    for line in found.get("app", []):
        parts = line.split(" ")
        used = _number(parts[2]) if len(parts) > 2 else None
        if used is None or not _UUID.fullmatch(parts[0]):
            continue
        group = parts[3] if len(parts) > 3 and _number(parts[3]) is not None else ""
        apps.append(App(uuid=parts[0], used_mb=used, group=group))
    return Room(gpus=tuple(gpus), apps=tuple(apps)) if gpus else None


# ---------------------------------------------------------------------------
# What a study needs, from the runs measured on that machine
# ---------------------------------------------------------------------------
_LEARNING = threading.Lock()


def _store(machine: str) -> Path:
    from fastmdxplora.remote.transport import check_machine_name
    from fastmdxplora.user_dir import user_config_dir

    return user_config_dir() / "gpu_memory" / f"{check_machine_name(machine)}.json"


def measured(machine: str) -> list[dict[str, Any]]:
    """The runs measured on ``machine``: each one's particles and the most
    GPU memory it held, in MB. A record that cannot be read gives none."""
    try:
        record = json.loads(_store(machine).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    runs = record.get("runs") if isinstance(record, dict) else None
    return [run for run in runs or []
            if isinstance(run, dict)
            and isinstance(run.get("particles"), int) and run["particles"] > 0
            and isinstance(run.get("peak_mb"), int) and run["peak_mb"] > 0][-_KEPT:]


def learn(machine: str, *, job: str, particles: int, peak_mb: int, gpu: str) -> None:
    """Record what a run on ``machine`` held, once per job."""
    if particles <= 0 or peak_mb <= 0:
        return
    target = _store(machine)
    with _LEARNING:
        runs = [run for run in measured(machine) if run.get("job") != job]
        runs.append({"job": job, "particles": particles, "peak_mb": peak_mb,
                     "gpu": gpu[:80]})
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, scratch = tempfile.mkstemp(dir=target.parent, prefix=f".{machine}.",
                                           suffix=".part")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(json.dumps({"machine": machine, "runs": runs[-_KEPT:]},
                                     indent=2) + "\n")
            os.replace(scratch, target)
        except BaseException:
            Path(scratch).unlink(missing_ok=True)
            raise


@dataclass(frozen=True)
class Need:
    """The memory a study is expected to hold on one GPU, and how that was
    worked out; ``mb`` is ``None`` where it is not known."""

    mb: int | None
    how: str


def need_for(machine: str, particles: int | None, *, at_once: int = 1) -> Need:
    """What a study of ``particles`` (``None`` where it could not be worked
    out here) is expected to hold on a GPU of ``machine``, ``at_once`` of
    its runs side by side on it."""
    runs = measured(machine)
    if not runs:
        return Need(None, f"not known yet: no run sent from here has finished on "
                          f"{machine} with its GPU memory measured; this one is")
    counted = f"{len(runs)} run{'s' if len(runs) != 1 else ''} measured on {machine}"
    peaks = [(run["particles"], run["peak_mb"]) for run in runs]
    if particles is None:
        expected = max(peak for _, peak in peaks)
        how = (f"the most any of {counted} held, since this study's size could "
               "not be worked out here")
    else:
        expected = _fitted(peaks, particles)
        how = f"from {counted}, for about {particles:,} particles"
    mb = math.ceil(round(expected * MARGIN, 6)) * at_once
    if at_once > 1:
        how += f", {at_once} runs side by side"
    return Need(mb, how)


def _fitted(peaks: list[tuple[int, int]], particles: int) -> float:
    """Memory for ``particles``: a line through runs of different sizes, or
    from one size scaled up by particles; never below the least measured."""
    sizes = {n for n, _ in peaks}
    if len(sizes) >= 2:
        mean_n = sum(n for n, _ in peaks) / len(peaks)
        mean_p = sum(p for _, p in peaks) / len(peaks)
        spread = sum((n - mean_n) ** 2 for n, _ in peaks)
        slope = sum((n - mean_n) * (p - mean_p) for n, p in peaks) / spread
        if slope > 0:
            return max(mean_p + slope * (particles - mean_n), min(p for _, p in peaks))
    return max(p * max(1.0, particles / n) for n, p in peaks)


# ---------------------------------------------------------------------------
# Which GPU
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Held:
    """A study sent from here and still running on a GPU of the machine:
    what it was expected to need there, which it may not hold yet."""

    job: str
    uuid: str
    need_mb: int
    group: str


@dataclass
class Choice:
    """Where a study runs on a workstation's GPUs, and what the plan says."""

    #: The GPU it is pinned to; ``None`` where the config names its own.
    gpu: Gpu | None = None
    #: The GPUs it will use, by UUID.
    uuids: tuple[str, ...] = ()
    #: What it needs on each, by UUID, where that is known.
    wanted: dict[str, int] = field(default_factory=dict)
    need: Need = field(default_factory=lambda: Need(None, ""))
    lines: list[str] = field(default_factory=list)
    #: Why it does not fit now, or "".
    refused: str = ""
    #: Whether its run is alone on its GPU, so what it holds is learned from.
    learn: bool = False


def _kept_back(room: Room, gpu: Gpu, held: list[Held]) -> tuple[int, list[str]]:
    """Memory on ``gpu`` kept for studies from here that may not hold all
    they need yet, and their names."""
    kept, names = 0, []
    for each in held:
        if each.uuid != gpu.uuid:
            continue
        short = max(0, each.need_mb - room.used_by(each.group, gpu.uuid))
        if short:
            kept += short
            names.append(each.job)
    return kept, names


def _said(gpu: Gpu, kept: int, names: list[str]) -> str:
    line = (f"GPU {gpu.index} ({gpu.name}): {gpu.free_mb:,} MB free of "
            f"{gpu.total_mb:,} MB")
    if gpu.busy_pct is not None:
        line += f", {gpu.busy_pct}% busy"
    if kept:
        line += f", {kept:,} MB of it kept for {', '.join(names)}, still starting"
    return line


def _free(room: Room, held: list[Held]) -> tuple[dict[str, int], list[str]]:
    """Each GPU's free memory less what studies from here are still to
    take, by UUID, and a line saying each."""
    free, lines = {}, []
    for gpu in room.gpus:
        kept, names = _kept_back(room, gpu, held)
        free[gpu.uuid] = gpu.free_mb - kept
        lines.append(_said(gpu, kept, names))
    return free, lines


def choose(machine: str, room: Room, need: Need, held: list[Held], *,
           named: list[int] | None = None) -> Choice:
    """The GPU with the most free memory, less what studies from here are
    still to take, and whether ``need`` fits on it; or, where the config
    names its GPUs (``named``, as ``nvidia-smi`` numbers them, once for each
    run on it at a time), whether it fits on each of those."""
    free, lines = _free(room, held)
    by_index = {gpu.index: gpu for gpu in room.gpus}
    if named is not None:
        missing = [i for i in dict.fromkeys(named) if i not in by_index]
        if missing:
            return Choice(need=need, lines=lines, refused=(
                f"The config names GPU {', '.join(map(str, missing))}, and "
                f"{machine} has GPU {', '.join(str(g.index) for g in room.gpus)}."))
        shares = Counter(named)
        using = [by_index[i] for i in shares]
        choice = Choice(uuids=tuple(g.uuid for g in using), need=need, lines=lines)
        if need.mb is not None:
            choice.wanted = {g.uuid: need.mb * shares[g.index] for g in using}
    else:
        best = max(room.gpus, key=lambda g: (free[g.uuid], -g.index))
        choice = Choice(gpu=best, uuids=(best.uuid,), need=need, lines=lines,
                        wanted={best.uuid: need.mb} if need.mb is not None else {})
    choice.refused = still_fits(machine, room, choice, held, free=free)
    return choice


def still_fits(machine: str, room: Room, choice: Choice, held: list[Held], *,
               free: dict[str, int] | None = None) -> str:
    """Why ``choice`` does not fit on ``room`` now, or "": each of its GPUs
    still there, with room for what it needs on it."""
    if free is None:
        free, _ = _free(room, held)
    by_uuid = {gpu.uuid: gpu for gpu in room.gpus}
    for uuid in choice.uuids:
        gpu = by_uuid.get(uuid)
        if gpu is None:
            return f"A GPU of {machine} the plan chose ({uuid}) is no longer there."
        wanted = choice.wanted.get(uuid)
        if wanted is not None and wanted > free[uuid]:
            return _no_room(machine, gpu, wanted, free[uuid], choice.need,
                            most=choice.gpu is not None and len(room.gpus) > 1)
    return ""


def _no_room(machine: str, gpu: Gpu, wanted: int, free: int, need: Need, *,
             most: bool = False) -> str:
    if wanted > gpu.total_mb:
        return (f"This study needs about {wanted:,} MB of GPU memory ({need.how}), "
                f"more than GPU {gpu.index} on {machine} ({gpu.name}) has at all "
                f"({gpu.total_mb:,} MB).")
    return (f"This study needs about {wanted:,} MB of GPU memory ({need.how}), and "
            f"{'the most free on any GPU of' if most else 'GPU ' + str(gpu.index) + ' on'} "
            f"{machine} is {max(free, 0):,} MB"
            + (f" (GPU {gpu.index}, {gpu.name})" if most else f" ({gpu.name})")
            + ". Send it once a run there has ended, or to another machine.")
