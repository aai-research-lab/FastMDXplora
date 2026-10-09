"""How much GPU memory a study needs, and whether a workstation's GPUs have it.

Studies sent to a workstation share its GPUs, so a send asks the GPUs how
much memory each has free now (``nvidia-smi``, over the connection already
open) and goes to one it fits on, the one with the fewest studies from here
and then the most free memory, pinned there by its UUID in
``CUDA_VISIBLE_DEVICES``. A config that names its own GPUs keeps them,
checked for room. A study that would not fit is refused before anything is
copied, with the numbers.

**What a study needs is learned, not guessed.** Each run on a GPU has its
memory read every 15 s by its own job script, and the most it held is
recorded with the particles its system had and its precision
(``cost.json``). From the runs in a study's precision on that machine its
need is worked out (:func:`_fitted`), then 15% more. Until such a run has
finished there, the need is not known, and the plan says so: a GPU is
chosen and nothing is refused.

**A run just started holds little yet.** Setup can take minutes before the
simulation takes its memory, so each study sent from here that does not
yet hold what it was expected to keeps back the difference.

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
from contextlib import contextmanager
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

#: Free memory within this of the most a GPU has free counts as alike, so
#: the GPU chosen does not move on noise.
_ALIKE_MB = 256

#: The runs kept for each machine, the latest.
_KEPT = 20

#: A GPU's UUID as ``nvidia-smi`` gives it, the only form put in a job script.
_UUID = re.compile(r"GPU-[0-9A-Fa-f-]{8,64}")

#: Each GPU, each process using one with the process group it is in, the
#: GPUs the account's own environment gives it, where it names them, and the
#: order CUDA numbers them in there.
GPU_SCRIPT = (
    "command -v nvidia-smi >/dev/null 2>&1 || { echo fmdx:gpus=none; exit 0; }\n"
    "[ -n \"${CUDA_VISIBLE_DEVICES+x}\" ] "
    "&& printf 'fmdx:visible=%s\\n' \"$CUDA_VISIBLE_DEVICES\"\n"
    "printf 'fmdx:order=%s\\n' \"${CUDA_DEVICE_ORDER:-}\"\n"
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
    #: ``None`` where the machine does not split its memory by process
    #: (``[N/A]``, as in a container or WSL).
    used_mb: int | None
    #: Its process group, or "" where the machine did not say.
    group: str


@dataclass(frozen=True)
class Room:
    """A workstation's GPUs and what uses them, now."""

    gpus: tuple[Gpu, ...]
    apps: tuple[App, ...] = ()
    #: The account's own ``CUDA_VISIBLE_DEVICES`` there, as shown, where it
    #: is set; ``gpus`` are then only those it gives.
    visible: str | None = None
    #: Whether CUDA numbers the GPUs as ``nvidia-smi`` does: all alike, or
    #: ``CUDA_DEVICE_ORDER=PCI_BUS_ID`` there (CUDA puts the fastest first
    #: otherwise).
    numbered_alike: bool = True

    def used_by(self, group: str, uuid: str) -> int:
        return sum(app.used_mb or 0 for app in self.apps
                   if group and app.group == group and app.uuid == uuid)

    def unsplit(self, group: str, uuid: str) -> bool:
        """Whether ``group`` has a process on ``uuid`` whose memory the
        machine does not give: it holds memory, counted only as used."""
        return any(app.used_mb is None for app in self.apps
                   if group and app.group == group and app.uuid == uuid)


def _number(text: str) -> int | None:
    text = text.strip()
    return int(text) if re.fullmatch(r"[0-9]{1,9}", text) else None


def room_from(found: dict[str, list[str]]) -> Room | None:
    """The GPUs in a machine's answer to :data:`GPU_SCRIPT`, or ``None``
    where it has no ``nvidia-smi`` or no GPU could be read. Where the
    account's ``CUDA_VISIBLE_DEVICES`` is set, only the GPUs it gives, none
    where it names one that is not read here."""
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
        name = "".join(c if c.isprintable() else "?" for c in ", ".join(parts[2:-4]))
        gpus.append(Gpu(index=index, uuid=parts[1], name=name[:80] or "GPU",
                        total_mb=total, used_mb=used, free_mb=free,
                        busy_pct=_number(parts[-1])))
    apps = []
    for line in found.get("app", []):
        parts = line.split(" ")
        used = _number(parts[2]) if len(parts) > 2 else None
        if (used is None and not (len(parts) > 2 and parts[2] in ("[N/A]", "N/A"))
                or not _UUID.fullmatch(parts[0])):
            continue
        group = parts[3] if len(parts) > 3 and _number(parts[3]) is not None else ""
        apps.append(App(uuid=parts[0], used_mb=used, group=group))
    if not gpus:
        return None
    alike = (len({gpu.name for gpu in gpus}) == 1
             or (found.get("order") or [""])[0].strip() == "PCI_BUS_ID")
    if "visible" not in found:
        return Room(gpus=tuple(gpus), apps=tuple(apps), numbered_alike=alike)
    given = found["visible"][0]
    shown = re.sub(r"[^A-Za-z0-9,._-]", "?", given)[:120]
    return Room(gpus=_visible(gpus, given, by_number=alike), apps=tuple(apps),
                visible=shown or '""', numbered_alike=alike)


def _visible(gpus: list[Gpu], given: str, *, by_number: bool) -> tuple[Gpu, ...]:
    """The GPUs ``CUDA_VISIBLE_DEVICES`` gives, each by its number or its
    UUID (or the start of one), in its order; none where any of it is not
    one of those (CUDA takes none past an entry it cannot read), or is a
    number where CUDA does not number the GPUs as ``nvidia-smi`` does
    (``by_number`` false)."""
    kept: list[Gpu] = []
    for entry in (part.strip() for part in given.split(",")):
        if re.fullmatch(r"[0-9]{1,3}", entry) and by_number:
            found = [gpu for gpu in gpus if gpu.index == int(entry)]
        elif re.fullmatch(r"GPU-[0-9A-Fa-f-]{1,64}", entry):
            found = [gpu for gpu in gpus if gpu.uuid.lower().startswith(entry.lower())]
        else:
            return ()
        if len(found) != 1 or found[0] in kept:
            return ()
        kept.append(found[0])
    return tuple(kept)


# ---------------------------------------------------------------------------
# What a study needs, from the runs measured on that machine
# ---------------------------------------------------------------------------
_LEARNING = threading.Lock()

#: A count kept: a run's particles or its memory in MB.
_MOST = 10 ** 9


def _store(machine: str) -> Path:
    from fastmdxplora.remote.transport import check_machine_name
    from fastmdxplora.user_dir import user_config_dir

    return user_config_dir() / "gpu_memory" / f"{check_machine_name(machine)}.json"


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < _MOST


def measured(machine: str) -> list[dict[str, Any]]:
    """The runs measured on ``machine``: each one's particles, the most GPU
    memory it held in MB, and its precision. A record that cannot be read
    gives none."""
    try:
        record = json.loads(_store(machine).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    runs = record.get("runs") if isinstance(record, dict) else None
    if not isinstance(runs, list):
        return []
    return [run for run in runs
            if isinstance(run, dict) and _count(run.get("particles"))
            and _count(run.get("peak_mb"))
            and isinstance(run.get("precision", "mixed"), str)][-_KEPT:]


@contextmanager
def _one_learner(target: Path):
    """One writer of a machine's record at a time, in this process and
    across processes (a terminal and an AI app learning at once)."""
    with _LEARNING:
        try:
            import fcntl
        except ImportError:  # Windows: one process at a time is not enforced
            yield
            return
        with open(target.with_name(f".{target.name}.lock"), "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
            except OSError:
                yield
                return
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def learn(machine: str, *, job: str, particles: int, peak_mb: int, gpu: str,
          precision: str = "mixed") -> None:
    """Record what a run on ``machine`` held, once per job."""
    if not (_count(particles) and _count(peak_mb)):
        return
    target = _store(machine)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _one_learner(target):
        runs = [run for run in measured(machine) if run.get("job") != job]
        runs.append({"job": job, "particles": particles, "peak_mb": peak_mb,
                     "gpu": gpu[:80], "precision": precision[:20]})
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
    """The memory one run of a study is expected to hold on its GPU, and how
    that was worked out; ``mb`` is ``None`` where it is not known."""

    mb: int | None
    how: str


def need_for(machine: str, particles: int | None, *, precision: str = "mixed",
             not_learned: str = "") -> Need:
    """What one run of ``particles`` (``None`` where they could not be
    worked out here) in ``precision`` is expected to hold on a GPU of
    ``machine``, from the runs measured there in that precision; not known
    where they do not reach it. ``not_learned``: why this study's run is
    not learned from, or "" where it is."""
    this_one = f", and this one, {not_learned}, is not" if not_learned else "; this one is"
    runs = [run for run in measured(machine)
            if run.get("precision", "mixed") == precision]
    if not runs:
        return Need(None, f"not known yet: no run in {precision} precision sent from here "
                          f"has finished on {machine} with its GPU memory measured"
                          + this_one)
    counted = (f"{len(runs)} run{'s' if len(runs) != 1 else ''} in {precision} precision "
               f"measured on {machine}")
    if particles is None:
        return Need(None, "not known: this study's size could not be worked out here "
                          f"(a structure fetched by its identifier, a file other than "
                          f"PDB, a membrane), and {counted} say nothing of another size"
                          + this_one)
    peaks = [(run["particles"], run["peak_mb"]) for run in runs]
    expected = _fitted(peaks, particles)
    if expected is None:
        largest = max(n for n, _ in peaks)
        return Need(None, f"not known yet: about {particles:,} particles is past what "
                          f"{counted} (up to {largest:,} particles) can say"
                          + this_one)
    return Need(math.ceil(round(expected * MARGIN, 6)),
                f"from {counted}, for about {particles:,} particles")


def _fitted(peaks: list[tuple[int, int]], particles: int) -> float | None:
    """Memory for ``particles``, or ``None`` where the runs measured do not
    say. A run holds a fixed part (the CUDA context, OpenMM's kernels and
    FFT plans) and a part per particle, so a size is never scaled from
    another by particles alone.

    No larger than a larger run measured held, and no smaller than a run
    of its size or smaller held. From runs of two sizes or more whose
    memory grows with size, a straight line through them, its fixed part
    included; trusted past the largest size by as far again as the sizes
    measured span, and not past that. From one size alone, nothing past it.
    """
    sizes = sorted({n for n, _ in peaks})
    smallest, largest = sizes[0], sizes[-1]
    at_or_below = [p for n, p in peaks if n <= particles]
    floor = max(at_or_below) if at_or_below else max(
        p for n, p in peaks if n == smallest)
    if particles < smallest:
        return floor          # a smaller system holds no more than the smallest did
    line = None
    if len(sizes) >= 2:
        # Through the most each size held: runs of one size that held less
        # (another method, another card) do not pull the line down.
        most = [(n, max(p for m, p in peaks if m == n)) for n in sizes]
        mean_n = sum(n for n, _ in most) / len(most)
        mean_p = sum(p for _, p in most) / len(most)
        spread = sum((n - mean_n) ** 2 for n, _ in most)
        slope = sum((n - mean_n) * (p - mean_p) for n, p in most) / spread
        if slope > 0:
            line = mean_p + slope * (particles - mean_n)
    if particles <= largest:
        above = max(p for n, p in peaks if n >= particles)
        return max(min(line, above), floor) if line is not None else max(above, floor)
    if line is None or particles > largest + (largest - smallest):
        return None
    return max(line, floor)


# ---------------------------------------------------------------------------
# Which GPU
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Held:
    """A study sent from here and still running on a GPU of the machine:
    what it was expected to need there (0 where not known), which it may not
    hold yet."""

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
    #: What one run needs.
    need: Need = field(default_factory=lambda: Need(None, ""))
    #: Runs side by side on each GPU it uses, by UUID.
    at_once: dict[str, int] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    #: Why it does not fit now, or "".
    refused: str = ""
    #: Whether its run is alone on its GPU, so what it holds is learned from.
    learn: bool = False
    #: Its precision, recorded with what it held.
    precision: str = "mixed"


def _kept_back(room: Room, gpu: Gpu, held: list[Held]) -> tuple[int, int]:
    """Memory on ``gpu`` kept for studies from here that do not hold what
    they were expected to yet, and how many there are."""
    kept = count = 0
    for each in held:
        if each.uuid != gpu.uuid or not each.need_mb or room.unsplit(each.group, gpu.uuid):
            continue
        holds = room.used_by(each.group, gpu.uuid)
        # Expected, without the margin kept above it: a run holding that has
        # taken its memory.
        if math.ceil(round(holds * MARGIN, 6)) < each.need_mb:
            kept += each.need_mb - holds
            count += 1
    return kept, count


def _said(gpu: Gpu, kept: int, count: int) -> str:
    line = (f"GPU {gpu.index} ({gpu.name}): {gpu.free_mb:,} MB free of "
            f"{gpu.total_mb:,} MB")
    if gpu.busy_pct is not None:
        line += f", {gpu.busy_pct}% busy"
    if kept:
        line += (f"; {kept:,} MB kept back for {count} "
                 f"{'studies' if count != 1 else 'study'} from here not yet holding "
                 f"what {'they need' if count != 1 else 'it needs'}")
    return line


def _free(room: Room, held: list[Held]) -> tuple[dict[str, int], list[str]]:
    """Each GPU's free memory less what studies from here are still to
    take, by UUID, and a line saying each."""
    free, lines = {}, []
    for gpu in room.gpus:
        kept, count = _kept_back(room, gpu, held)
        free[gpu.uuid] = gpu.free_mb - kept
        lines.append(_said(gpu, kept, count))
    return free, lines


def choose(machine: str, room: Room, need: Need, held: list[Held], *,
           at_once: int = 1, named: Counter | None = None) -> Choice:
    """Where a study goes. With ``named`` (the GPUs the config names, as
    ``nvidia-smi`` numbers them, each with the runs on it at a time),
    whether it fits on each of those. Otherwise the GPU it fits on with the
    fewest studies from here, then the most free memory (less what studies
    from here are still to take), ``at_once`` of its runs side by side."""
    free, lines = _free(room, held)
    if named is not None:
        by_index = {gpu.index: gpu for gpu in room.gpus}
        missing = [i for i in named if i not in by_index]
        if missing:
            return Choice(need=need, lines=lines, refused=(
                f"The config names GPU {', '.join(map(str, missing))}, and "
                f"{machine} has GPU {', '.join(str(g.index) for g in room.gpus)}."))
        using = [by_index[i] for i in named]
        choice = Choice(uuids=tuple(g.uuid for g in using), need=need, lines=lines,
                        at_once={g.uuid: named[g.index] for g in using})
    else:
        from_here = Counter(each.uuid for each in held)
        wanted = need.mb * at_once if need.mb is not None else 0

        def tier(g: Gpu) -> tuple:
            fits = free[g.uuid] >= wanted
            return (fits, -from_here[g.uuid] if fits else 0)

        top = max(tier(g) for g in room.gpus)
        level = [g for g in room.gpus if tier(g) == top]
        most = max(free[g.uuid] for g in level)
        # Among GPUs it fits on, those within a little of the most free are
        # alike: a few MB taken by someone else's process while a person
        # reads the plan does not move the study to another GPU, and ask
        # them again. Where it fits on none, the one with the most room.
        close = _ALIKE_MB if top[0] else 0
        best = min((g for g in level if free[g.uuid] >= most - close),
                   key=lambda g: g.index)
        choice = Choice(gpu=best, uuids=(best.uuid,), need=need, lines=lines,
                        at_once={best.uuid: at_once})
    if need.mb is not None:
        choice.wanted = {uuid: need.mb * n for uuid, n in choice.at_once.items()}
    choice.refused = still_fits(machine, room, choice, held, free=free)
    return choice


def still_fits(machine: str, room: Room, choice: Choice, held: list[Held], *,
               free: dict[str, int] | None = None, again: bool = False) -> str:
    """Why ``choice`` does not fit on ``room`` now, or "": each of its GPUs
    still there, with room for what it needs on it. ``again``: asked as the
    send starts, of the GPUs the plan chose."""
    if free is None:
        free, _ = _free(room, held)
    by_uuid = {gpu.uuid: gpu for gpu in room.gpus}
    for uuid in choice.uuids:
        gpu = by_uuid.get(uuid)
        if gpu is None:
            return (f"A GPU of {machine} the plan chose ({uuid}) is no longer there. "
                    "Plan the send again.")
        wanted = choice.wanted.get(uuid)
        if wanted is None or wanted <= free[uuid]:
            continue
        said = _no_room(machine, gpu, wanted, free[uuid], choice.need,
                        choice.at_once.get(uuid, 1), chosen=again)
        if again and choice.gpu is not None:
            others = [g for g in room.gpus if g.uuid != uuid and free[g.uuid] >= wanted]
            if others:
                said += (f" GPU {others[0].index} has {free[others[0].uuid]:,} MB free: "
                         "plan the send again to go there.")
        return said
    return ""


def _no_room(machine: str, gpu: Gpu, wanted: int, free: int, need: Need,
             at_once: int, *, chosen: bool = False) -> str:
    side = f", {at_once} runs side by side" if at_once > 1 else ""
    said = f"This study needs about {wanted:,} MB of GPU memory ({need.how}{side})"
    if wanted > gpu.total_mb:
        return (f"{said}, more than GPU {gpu.index} on {machine} ({gpu.name}) has at all "
                f"({gpu.total_mb:,} MB). The runs that is worked out from are kept in "
                f"{_store(machine)}; one that does not stand for this study can be "
                "taken out of it.")
    where = (f"GPU {gpu.index} on {machine} ({gpu.name}), chosen when the send was "
             "planned, has" if chosen else
             f"GPU {gpu.index} on {machine} ({gpu.name}) has")
    return (f"{said}, and {where} {max(free, 0):,} MB free for it now. Send it once a "
            "run there has ended, or to another machine.")
