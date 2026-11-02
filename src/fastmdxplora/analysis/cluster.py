"""Conformational clustering.

Clusters frames of the trajectory by structural similarity, producing
one cluster-membership labeling per requested method. Three methods are
supported:

  - **k-means** (default, fast, requires choosing ``n_clusters``)
  - **hierarchical** (agglomerative, average linkage by default)
  - **dbscan** (density-based, no pre-specified cluster count)

With ``features="rmsd"`` (the default) the methods work from the pairwise
RMSD matrix (MDTraj's QCP algorithm), the standard featurization for
conformational clustering: hierarchical and DBSCAN on the matrix itself,
k-means and Ward linkage on a classical MDS embedding of it, which they
need a Euclidean space for. With ``features="coordinates"`` they work on
the superposed Cartesian coordinates. Outputs per method:

  - ``cluster_<method>.dat``: per-frame integer cluster labels, by the
    frame of the trajectory given.
  - ``cluster_<method>.png`` — cluster labels as a function of time.
  - ``cluster_<method>_counts.png`` — cluster population bar chart.
  - ``cluster_hierarchical_dendrogram.png`` — hierarchical dendrogram
    when hierarchical clustering is requested and SciPy is available.
  - ``cluster_<method>_populations.csv``: each cluster's frames, its
    fraction of the frames clustered, and its medoid's frame and time.
  - ``cluster_<method>_medoid_<k>.pdb``: each cluster's medoid, the member
    with the least summed RMSD to the others, without its water.
  - ``cluster_rmsd_matrix.npz`` and ``cluster_rmsd_matrix.png``: the
    frame-to-frame RMSD the clustering used, as a map of time against time
    of at most :data:`MOST_MAP_CELLS` cells across, its rows and columns
    averaged in blocks of consecutive frames (the block recorded in the
    file, under ``findings.matrix`` and in the figure's title), with the
    frames' times. The figure, the GUI and the report read this one file.
  - ``hierarchical_linkage.npy``: the hierarchy that labelled the frames,
    which is all a dendrogram needs: for Ward, built on the same points
    (recorded under ``findings.hierarchical``).
  - ``cluster_rmsd_matrix_full.npy``: the whole frame-by-frame matrix, in
    float32, only where ``save_full_matrix`` asks for it; its size is said
    in the log before it is written. 31,000 frames make 3.8 GB of it.

Every frame is clustered by default, the equilibration from the starting
structure included; ``start`` begins later, ``stride`` takes every k-th
frame from there, and the record says how many frames were clustered and
whether the equilibration the RMSD detects is among them
(:mod:`fastmdxplora.analysis.starting_frame`).

**Memory is checked before the matrix is made.** The frames' pairwise
matrix grows with the square of their number (128,000 frames want 131 GB
of it in float64, before any copy a method makes), so its peak, the matrix
and the largest copy the methods asked for make of it, or the map plotted
once it is freed where that is more, is estimated (:func:`memory_needed`)
and compared with the memory this machine has available
(:func:`memory_available`). Past it, clustering is refused with the code
``analysis.memory.matrix_too_large``, naming the frames, the bytes needed
and available, and the stride that fits; the other analyses of the study
still run. Under an address-space limit, BLAS and OpenMP are held to one
thread while clustering (:func:`threads_held_to_one`). A method that needs
no matrix does not build one: k-means, Ward's linkage and DBSCAN on
``features: coordinates`` work from the frames' coordinates.

Because this analysis produces multiple files per run, it overrides the
base class's :meth:`save_data` and :meth:`_do_plot` methods.

References
----------
Lloyd, S. *IEEE Trans. Inf. Theory* **1982**, 28, 129 (k-means).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN, AgglomerativeClustering, KMeans

from fastmdxplora.protein_names import ALPHA_CARBONS
from fastmdxplora.analysis.base import Analysis, AnalysisResult, superposed
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.analysis.plotting import (
    category_style, close_figures_opened_since, figures_open, match_colorbar_font,
    new_figure, save_figure)
from fastmdxplora.analysis.rmsd import refuse_too_few_to_align
from fastmdxplora.analysis.starting_frame import first_frame, start_as_given
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import BackendUnavailable
from fastmdxplora.utils.logging import get_logger

logger = get_logger("analysis.cluster")


VALID_METHODS = ("kmeans", "hierarchical", "dbscan")

#: What the frames are compared in.
#:
#: ``rmsd`` measures every pair with its own optimal superposition, so the
#: distance is invariant to how the molecule happens to be placed. ``ward``
#: needs a Euclidean space and reaches one through a classical MDS embedding.
#:
#: ``coordinates`` superposes every frame onto the first and compares the
#: coordinates directly, which is the cheaper approximation and the space
#: ``ward`` and k-means were defined in. The coordinates are scaled by
#: 1/sqrt(n_atoms), so a distance in this space is an RMSD measured against
#: that one common alignment rather than a pairwise one: ``eps`` keeps its
#: meaning in nm, and the two feature spaces stay comparable.
#:
#: Which to use is a real choice and not a detail. A common alignment is exact
#: only where the optimal pairwise superposition is the same one; across a
#: large conformational change it is not, and the two will disagree.
VALID_FEATURES = ("rmsd", "coordinates")

#: The most cells the saved time-against-time map has across. Its rows and
#: columns are means over blocks of consecutive frames to reach it, so the
#: file is at most about 4 MB whatever the run's length; the GUI averages it
#: further, to the cells it shows.
MOST_MAP_CELLS = 1000

#: The share of this machine's available memory a clustering plans on, the
#: rest left for the trajectory already loaded, the figures and the machine.
MEMORY_SHARE = 0.75

#: About how much a block of distance rows read at a time may take, in bytes.
ROWS_BYTES = 64 * 2**20

#: What plotting the time-against-time map takes, per cell of the map
#: squared and once, in bytes: Matplotlib's mesh of the cells and its
#: rendering to PNG and SVG. Measured as the peak resident memory of a
#: process plotting it (about 132 bytes a cell at 1,000 cells across, 126
#: MiB; ``tests/test_a_clustering_fits_the_machine.py``), and planned on
#: beside the map's own numbers once the frames' matrix is freed.
FIGURE_BYTES_PER_CELL = 136
FIGURE_BYTES = 8 * 2**20


def builds_matrix(methods: Sequence[str], features: str, linkage: str) -> bool:
    """Whether a clustering makes the frames' n by n distance matrix.

    Every method on ``features: rmsd`` reads it (k-means and Ward through a
    classical MDS embedding of it). On ``features: coordinates`` only a
    linkage read from distances (average, complete, single) does; k-means,
    Ward and DBSCAN work from the points.
    """
    if features != "coordinates":
        return True
    return "hierarchical" in methods and linkage != "ward"


def memory_needed(n: int, methods: Sequence[str], features: str, linkage: str) -> int:
    """The peak bytes, of order n squared, that clustering ``n`` frames makes:
    the larger of what the clustering makes of their matrix
    (:func:`matrix_needed`) and what plotting its map makes once the matrix
    is freed (:func:`map_needed`)."""
    return max(matrix_needed(n, methods, features, linkage), map_needed(n))


def matrix_needed(n: int, methods: Sequence[str], features: str, linkage: str) -> int:
    """The peak bytes, of order n squared, the clustering of ``n`` frames
    makes of their matrix.

    The matrix, float64 (8 n^2 bytes), held from when it is made to the end,
    plus the largest of what each method makes of it while it runs (they run
    one after the other, each freeing what it made), read from the code they
    run and held by a test that measures the peak:

    - k-means and Ward on ``rmsd``: the classical MDS embedding's doubly
      centred matrix (8 n^2; :func:`_classical_mds` finds the leading
      eigenvectors in it, in place).
    - an average or complete linkage: scikit-learn's condensed copy in
      float64 and the index pairs it is read with, two int64 arrays of
      n^2/2, and the mask they are found with (13 n^2), and SciPy's own
      working copy of the condensed distances, made in compiled code where
      tracemalloc does not see it (4 n^2): 17 n^2. Single linkage's minimum
      spanning tree makes no such copy: 13 n^2. The dendrogram's linkage,
      made again after it, makes 8 n^2 of it.
    - Ward's linkage, on points: SciPy's condensed distances, 4 n^2 (after
      the MDS on ``rmsd``).
    - DBSCAN: the neighbours of every frame within ``eps``, int64, n^2 at
      most, where every frame is within it of every other (8 n^2); on the
      matrix, scikit-learn's block of its rows and the masks it reads them
      with as well (at most 1 GiB, its working memory, and 9 n^2) and its
      lists of neighbours, read again and copied (13 n^2, as measured).

    Making the matrix symmetric holds one block of it beside it at any n, in
    one buffer made once and given back to the system after
    (:func:`_pairwise_rmsd`): 8 n^2 up to about 2,900 frames, where the
    block is the whole matrix, and at most :data:`ROWS_BYTES` past them.
    That is within the least any method here counts beside the matrix,
    which is 8 n^2. On Python 3.13's NumPy, ``0.5 * (a + b.T)`` held two
    blocks, and the distances alone peaked at 24 n^2; past about 2,900
    frames, the last pass's block was held while the next was made, on any
    Python, and a block freed stayed resident in glibc's heap after.

    The map of the matrix (:func:`map_needed`) is plotted after the matrix
    is freed, so it is counted beside its own numbers and not beside the
    matrix (:func:`memory_needed`: whichever is larger). It was left to the
    share not planned on, and at 2,000 frames under a container's limit of
    about 100 MB a clustering the check allowed, and the stride its refusal
    gave, were killed plotting it (160 to 180 MiB the check did not count).

    Terms in n alone (the trajectory's frames, labels, a few rows of
    distances at a time) and the other figures are left to the share of
    memory not planned on (:data:`MEMORY_SHARE`). Measured on SciPy 1.17 and
    scikit-learn 1.9 as the peak resident memory of a process clustering
    (``tests/test_a_clustering_fits_the_machine.py``), which counts what
    compiled code takes as well.
    """
    n2 = float(n) * float(n)
    matrix = 8 * n2 if builds_matrix(methods, features, linkage) else 0.0
    each = [0.0]
    for method in methods:
        if method == "kmeans" and features != "coordinates":
            each.append(8 * n2)
        elif method == "hierarchical":
            if linkage in ("average", "complete"):
                each.append(17 * n2)
            elif linkage != "ward":
                each.append(13 * n2)
            else:
                each.append(8 * n2 if features != "coordinates" else 4 * n2)
        elif method == "dbscan":
            each.append(8 * n2 if features == "coordinates"
                        else 21 * n2 + min(2**30, 9 * n2))
    return int(matrix + max(each))


def map_block(n: int) -> int:
    """How many consecutive frames each cell of the map averages, so that it
    is at most :data:`MOST_MAP_CELLS` across."""
    return max(1, -(-int(n) // MOST_MAP_CELLS))


def map_needed(n: int) -> int:
    """The peak bytes of the map of ``n`` clustered frames, once their matrix
    is freed: its cells in float64 (8 bytes a cell), and Matplotlib's mesh
    of them and its rendering (:data:`FIGURE_BYTES_PER_CELL` a cell, and
    :data:`FIGURE_BYTES` once). 1,000 frames, and each whole multiple of
    them, make a map 1,000 cells across, about 145 MiB."""
    cells = -(-int(n) // map_block(n)) if n > 0 else 0
    return int(FIGURE_BYTES + (FIGURE_BYTES_PER_CELL + 8) * float(cells) * float(cells))


#: The address space a clustering maps past what :func:`memory_needed`
#: counts, in bytes, and the least it maps at any number of frames: glibc's
#: heap, which grows and is not given back, and each figure's canvas at 300
#: dpi, PNG and SVG. The libraries, and a figure plotted as the analyses
#: plot theirs, are mapped before the room is read (:func:`libraries_mapped`),
#: so they are in what the process holds and not here. Address space, not
#: resident memory: under ``ulimit -v`` it is what runs out. Measured from
#: the check in ``fastmdx analyze``'s own process, where ``ulimit -v`` is
#: set, as the least room under which the clustering ends with every figure
#: plotted (``RLIMIT_AS`` set at the check, BLAS on one thread), k-means and
#: average linkage on tri-alanine: 36 MiB at 500 frames and at 50 (every
#: 10th) after the RMSD, 53 and 48 run alone, against 42 and 8 MiB of
#: :func:`memory_needed`. A new process, whose heap no analysis before has
#: grown, maps more: 62, 83 and 186 MiB at 50, 500 and 2,000 frames, about
#: 17 MiB past this plan; a figure that runs out there is said with what to
#: set, and the rest saved (:meth:`Cluster._plotted`). Measured there, the
#: allowance (150 MiB) counted the libraries a second time in the command
#: line's process, and tri-alanine's 500 frames were refused at any stride
#: under ``ulimit -v`` 600000, where they ran with room to spare.
ADDRESS_BYTES = 18 * 2**20
ADDRESS_LEAST = 50 * 2**20

#: The share of the address space an address-space limit leaves that a
#: clustering or a projection plans on: all of it. The room is the limit
#: less what this process holds once the libraries are mapped, the
#: trajectory loaded among it, and what a run maps past that is planned on
#: as measured (:data:`ADDRESS_BYTES`), with the margin of what was
#: measured; the resident memory's share (:data:`MEMORY_SHARE`) is for
#: other processes taking memory as it runs. Planned on at 75% as well,
#: tri-alanine's 500 frames were refused at ``ulimit -v`` 600000, where
#: they ran with 18 MiB to spare.
ADDRESS_SHARE = 1.0


def address_space_needed(n: int, methods: Sequence[str], features: str,
                         linkage: str) -> int:
    """The address space clustering ``n`` frames maps at its peak, past what
    the process holds once the libraries are mapped: :func:`memory_needed`
    and :data:`ADDRESS_BYTES`, and :data:`ADDRESS_LEAST` at the least.
    Planned on where an address-space limit (``ulimit -v``) leaves less than
    the memory does: under it, a clustering the check planned as resident
    memory failed plotting its figures."""
    return max(memory_needed(n, methods, features, linkage) + ADDRESS_BYTES,
               ADDRESS_LEAST)


def address_besides(n: int, methods: Sequence[str], features: str, linkage: str) -> int:
    """What a clustering of ``n`` frames maps past :func:`memory_needed`,
    in bytes, as part of :func:`address_space_needed`."""
    return (address_space_needed(n, methods, features, linkage)
            - memory_needed(n, methods, features, linkage))


def address_room(available: int | None, read_from: str) -> tuple[int | None, str]:
    """The address space an address-space or data limit leaves this process,
    from the reading the memory was planned on where that limit was the
    least of them (one reading, not two), else read now
    (:func:`_rlimit_room`). None where no such limit is set."""
    if read_from.startswith("RLIMIT"):
        return available, read_from
    return _rlimit_room()


def resident_room(available: int | None, read_from: str) -> tuple[int | None, str]:
    """The resident memory a run can take, apart from the address space:
    the reading the memory was planned on, else, where an address-space or
    data limit was the least of them, the least of the machine's and the
    control group's (:func:`_machine_available`, :func:`_cgroup_room`).
    Planned on as the address space's room as well, a run whose address
    space fitted was refused at 75% of it."""
    if not read_from.startswith("RLIMIT"):
        return available, read_from
    found = [reading for reading in (_machine_available(), _cgroup_room())
             if reading[0] is not None]
    if not found:
        return None, "not read: no machine or control group reading"
    return min(found, key=lambda reading: reading[0])


def ran_out(error: BaseException) -> bool:
    """Whether ``error`` is the memory running out: NumPy's ``MemoryError``,
    Matplotlib's ``std::bad_alloc`` (raised as ``MemoryError``), or a mapping
    refused with ``ENOMEM`` (``mmap``)."""
    import errno

    return isinstance(error, MemoryError) or (
        isinstance(error, OSError) and error.errno == errno.ENOMEM)


def give_back_freed() -> None:
    """Give the pages glibc holds freed back to the system (``malloc_trim``),
    where glibc is the allocator; nothing elsewhere. DBSCAN's lists of
    neighbours are many small arrays from glibc's heap, about 58 MiB at
    2,000 frames, and a medoid's block of rows of one cluster of every
    frame 30 MiB: still resident when the map was plotted after them."""
    import ctypes
    import ctypes.util
    import sys

    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        trim = libc.malloc_trim
    except (OSError, AttributeError):  # musl, or no C library found
        return
    trim.argtypes = [ctypes.c_size_t]
    trim(0)


def _machine_available() -> tuple[int | None, str]:
    """The memory this machine can give a new allocation now, and where that
    was read: ``psutil`` where it is installed (it is not a dependency); else
    ``MemAvailable`` in ``/proc/meminfo`` (Linux: free memory and the caches
    the kernel can give back); else free pages from ``os.sysconf``, which
    leave the caches out and so read low; else the machine's total physical
    memory from ``os.sysconf`` (macOS without psutil), said as such."""
    try:
        import psutil  # type: ignore[import-not-found]

        return int(psutil.virtual_memory().available), "psutil, available memory"
    except Exception:  # noqa: BLE001 -- not installed, or no answer
        pass
    found = _proc_meminfo_available()
    if found:
        return found, "MemAvailable in /proc/meminfo"
    for name, said in (("SC_AVPHYS_PAGES", "free pages from os.sysconf"),
                       ("SC_PHYS_PAGES", "the machine's total memory from os.sysconf, "
                                         "its available memory not readable here")):
        found = _sysconf_bytes(name)
        if found:
            return found, said
    return None, "no psutil, /proc/meminfo or os.sysconf on this machine"


def _read_number(path: Path) -> int | None:
    """A cgroup file's one number; None where it is absent, unreadable or
    ``max`` (no limit)."""
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() else None


def _reclaimable(folder: Path, version: str) -> int | None:
    """The group's page cache the kernel takes back before it kills anything
    in the group: ``inactive_file`` in its ``memory.stat`` (v2), and
    ``total_inactive_file`` under v1 (the group and those under it), else
    v1's ``inactive_file``. None where none is readable."""
    try:
        lines = (folder / "memory.stat").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    found: dict[str, int] = {}
    for line in lines:
        words = line.split()
        if len(words) == 2 and words[1].isdigit():
            found[words[0]] = int(words[1])
    keys = ("inactive_file",) if version == "cgroup v2" else (
        "total_inactive_file", "inactive_file")
    return next((found[key] for key in keys if key in found), None)


def _cgroup_room(proc: Path = Path("/proc/self/cgroup"),
                 root: Path = Path("/sys/fs/cgroup")) -> tuple[int | None, str]:
    """What this process's control group (a SLURM job's, a container's) still
    lets it take: its limit less its usage, the least over the group and the
    groups above it (a limit holds for every group under it):
    ``memory.max`` and ``memory.current`` under cgroup v2,
    ``memory.limit_in_bytes`` and ``memory.usage_in_bytes`` under v1. The
    usage counts the group's page cache, which the kernel reclaims before it
    kills anything in the group (a job that has just read a large
    trajectory holds it there), so its reclaimable part (:func:`_reclaimable`,
    ``inactive_file``) is not counted as used, as the kernel's working set
    and kubelet count it. None where no limit is set or none can be read."""
    try:
        lines = proc.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError):
        return None, ""
    best: tuple[int | None, str] = (None, "")
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy, controllers, where = parts
        if hierarchy == "0" and controllers == "":
            base, names, version = root, ("memory.max", "memory.current"), "cgroup v2"
        elif "memory" in controllers.split(","):
            base = root / "memory"
            names, version = ("memory.limit_in_bytes", "memory.usage_in_bytes"), "cgroup v1"
        else:
            continue
        folder = base.joinpath(*[part for part in where.strip().split("/") if part])
        while True:
            limit = _read_number(folder / names[0])
            usage = _read_number(folder / names[1])
            # v1 writes an unlimited group as a number near 2^63.
            if limit is not None and usage is not None and limit < 2**60:
                cache = _reclaimable(folder, version)
                room = max(limit - (usage - min(cache or 0, usage)), 0)
                if best[0] is None or room < best[0]:
                    cached = (f"its reclaimable page cache, {_gigabytes(cache)} of "
                              "inactive_file in memory.stat, not counted as used"
                              if cache is not None else
                              "its page cache counted as used, no memory.stat read")
                    best = (room, f"the {version} limit in {folder / names[0]} less "
                                  f"its usage, {cached}")
            if folder == base or base not in folder.parents:
                break
            folder = folder.parent
    return best


_LIBRARIES_MAPPED = False


def libraries_mapped() -> None:
    """Map, once, what clustering and the projections map the first time
    they run: SciPy's linkage, distances and eigensolver, OpenBLAS's buffer
    (one product) and Matplotlib's canvas, fonts and writers (a figure as the
    analyses plot theirs, rendered to PNG at 300 dpi and to SVG, in memory).
    Called before the room under an address-space limit is read, so what
    the libraries map is in what the process holds, and the plan
    (:data:`ADDRESS_BYTES`) counts only what a run maps past them. Planned on as an allowance measured in a new
    process, the libraries were counted twice in the command line's own,
    where the analyses before had mapped them: tri-alanine's 500 frames were
    refused at any stride under ``ulimit -v`` 600000, where all of them ran.
    Where the memory runs out doing it, the room read after says so."""
    global _LIBRARIES_MAPPED
    if _LIBRARIES_MAPPED:
        return
    _LIBRARIES_MAPPED = True
    import io

    try:
        import scipy.cluster.hierarchy  # noqa: F401
        import scipy.linalg
        import scipy.spatial.distance  # noqa: F401
        from threadpoolctl import threadpool_limits

        square = np.ones((64, 64))
        # On one thread, as the run is (:func:`threads_held_to_one`): each
        # further OpenBLAS thread started here held its stack and buffer.
        with threadpool_limits(limits=1):
            scipy.linalg.eigh(square @ square, subset_by_index=[62, 63])
        opened = figures_open()
        try:
            # A figure as the analyses plot theirs, at their size and the
            # house style: a line, a scatter with its colour bar, a mesh, a
            # title, labels and a legend, saved as save_figure saves.
            fig, ax = new_figure(title="Frames (k-means)", xlabel="Time (ns)",
                                 ylabel="RMSD (nm)")
            steps = np.linspace(0.0, 1.0, 64)
            ax.plot(steps, steps, label="RMSD")
            dots = ax.scatter(steps, steps[::-1], c=steps, s=10)
            fig.colorbar(dots, ax=ax, label="Frame")
            ax.pcolormesh(np.outer(steps[:16], steps[:16]), alpha=0.2)
            ax.legend()
            fig.tight_layout()
            for kind in ("png", "svg"):
                fig.savefig(io.BytesIO(), format=kind, dpi=300, bbox_inches="tight")
        finally:
            close_figures_opened_since(opened)
    except (MemoryError, OSError) as error:
        if not ran_out(error):
            raise


def _rlimit_room() -> tuple[int | None, str]:
    """What ``ulimit -v`` (``RLIMIT_AS``) or ``RLIMIT_DATA`` still lets this
    process take: the limit less what it holds (``VmSize``, ``VmData`` in
    ``/proc/self/status``), read once the libraries a run maps are mapped
    (:func:`libraries_mapped`). None where neither is set."""
    try:
        import resource
    except ImportError:  # not on Windows
        return None, ""
    limits = []
    for name, field in (("RLIMIT_AS", "VmSize"), ("RLIMIT_DATA", "VmData")):
        which = getattr(resource, name, None)
        if which is None:
            continue
        try:
            soft, _hard = resource.getrlimit(which)
        except (OSError, ValueError):
            continue
        if soft in (resource.RLIM_INFINITY, -1) or soft <= 0:
            continue
        limits.append((name, field, int(soft)))
    if not limits:
        return None, ""
    libraries_mapped()
    held = _process_status()
    best: tuple[int | None, str] = (None, "")
    for name, field, soft in limits:
        room = max(soft - held.get(field, 0), 0)
        if best[0] is None or room < best[0]:
            best = (room, f"{name} less what this process holds ({field})")
    return best


def _process_status() -> dict[str, int]:
    """This process's ``Vm*`` sizes from ``/proc/self/status``, in bytes."""
    found: dict[str, int] = {}
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                name, _, value = line.partition(":")
                words = value.split()
                if name.startswith("Vm") and len(words) == 2 and words[1] == "kB":
                    found[name] = int(words[0]) * 1024
    except (OSError, ValueError):
        pass
    return found


@contextmanager
def threads_held_to_one(findings: dict[str, Any]) -> Iterator[None]:
    """BLAS and OpenMP held to one thread, where the process has an
    address-space or data limit (``ulimit -v``), and said in ``findings``
    (``memory.threads``).

    Each OpenBLAS thread maps a buffer of its own the first time it works,
    address space that is not resident memory until written; under the
    limit, a thread whose buffer could not be mapped retried for ever, and a
    clustering the check allowed hung in k-means with the process idle. One
    thread maps none past the one it started with, already among what the
    process holds; what the run maps besides is planned on
    (:func:`address_space_needed`)."""
    room, said = _rlimit_room()
    if room is None:
        yield
        return
    from threadpoolctl import threadpool_limits

    limit = said.split(" less ")[0]
    findings.setdefault("memory", {})["threads"] = (
        f"BLAS and OpenMP held to one thread, as {limit} "
        f"({'ulimit -v' if limit == 'RLIMIT_AS' else 'ulimit -d'}) limits this process")
    with threadpool_limits(limits=1):
        yield


def memory_available() -> tuple[int | None, str]:
    """The memory a clustering can take now, in bytes, and where that was
    read: the least of what the machine has available
    (:func:`_machine_available`), what this process's control group still
    allows (:func:`_cgroup_room`: a SLURM job's or a container's limit, past
    which the kernel kills the worker), and what its address-space or data
    limit still allows (:func:`_rlimit_room`: ``ulimit -v``, past which the
    allocation fails). The record says which was least and what each read.
    None where none answers, and the check is then not made, which the
    record says.

    Several workers clustering at once each read the same memory: one's
    matrix is not seen until its pages are written. Planning for that is
    not done here.
    """
    readings = [_machine_available(), _cgroup_room(), _rlimit_room()]
    found = [(size, said) for size, said in readings if size is not None]
    if not found:
        return None, f"not read: {readings[0][1]}"
    least, said = min(found, key=lambda reading: reading[0])
    others = [other for size, other in found if other != said]
    if others:
        said += ", the least of that and " + " and ".join(others)
    return least, said


def _proc_meminfo_available() -> int | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _sysconf_bytes(name: str) -> int | None:
    import os

    try:
        pages, size = os.sysconf(name), os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        return None
    return int(pages) * int(size) if pages > 0 and size > 0 else None


def _gigabytes(n_bytes: float, places: int = 1) -> str:
    """A size in GB from 1 GB, else in MB: 152 MB was said as 0.2 GB."""
    return (f"{n_bytes / 1e9:,.{places}f} GB" if n_bytes >= 1e9
            else f"{n_bytes / 1e6:,.{places}f} MB")


def sizes_said(needed: float, available: float, share: float = MEMORY_SHARE,
               also: Sequence[float] = ()) -> tuple[str, ...]:
    """The need, the room and the share of it planned on, said in one unit
    (MB where each is under 1 GB, else GB) to one decimal, or to as many
    more (up to six) as it takes for the three to read in the order they
    are in, sizes that differ differently; the share is computed from the
    room as said, so the two shown agree. A refusal said a need of 0.4 GB
    against a room of 0.4 GB, and "0.1 GB, of which ... 75%, 87.0 MB"; and
    a need of 1.6 GB refused against a share of 1.7 GB, of a room of 2.2 GB
    (its share of 2.165 GB is 1.624 GB). Sizes in ``also`` are said after
    them, in the same unit and places."""
    unit, scale = ("GB", 1e9) if max(needed, available, *also) >= 1e9 else ("MB", 1e6)
    exact = (float(needed), float(available), share * float(available))

    def order(a: float, b: float) -> int:
        return (a > b) - (a < b)

    for places in range(1, 7):
        need = round(exact[0] / scale, places)
        room = round(exact[1] / scale, places)
        planned = round(share * room, places)
        shown = (need, room, planned)
        if all(order(shown[i], shown[j]) == order(exact[i], exact[j])
               for i in range(3) for j in range(i + 1, 3)):
            break
    return tuple(f"{value:,.{places}f} {unit}"
                 for value in (*shown, *(round(size / scale, places) for size in also)))


def need_and_room_said(need: float, room: float, room_from: str, mapped: bool,
                       who: str, besides: float | None = None, made: str = "") -> str:
    """A refusal's need, what it is for (``made``), and the room it was
    refused against, in one unit (:func:`sizes_said`). Under an
    address-space limit the need is of address space, ``besides`` of it
    what a run maps past the libraries, said as part of it, and all of the
    room is planned on (:data:`ADDRESS_SHARE`); else ``who`` plans on its
    share of the memory (:data:`MEMORY_SHARE`), said as a number. "157.3 MB
    the libraries and figures map besides" read as more than the need, of
    which it was part."""
    share = ADDRESS_SHARE if mapped else MEMORY_SHARE
    said = sizes_said(need, room, share, also=(besides,) if besides is not None else ())
    need_said, room_shown, planned_said = said[:3]
    text = need_said + (" of address space" if mapped else "") + (f" {made}" if made else "")
    if mapped and besides is not None:
        text += (f" ({said[3]} of it what the run maps past the libraries: its figures "
                 "and the heap)")
    text += f", and {room_said(int(room), room_from, room_shown)}"
    if share < 1:
        text += f", of which {who} plans on {share:.0%}, {planned_said}"
    return text


def more_said(need: float, room: float, mapped: bool, room_from: str = "") -> str:
    """Where a refused run would fit: a ``ulimit -v`` or a container
    allowing so many MB more, or so much more memory free, rounded up as
    said. "A larger `ulimit -v`" said no size."""
    import math

    more = max(need / (ADDRESS_SHARE if mapped else MEMORY_SHARE) - room, 0.0)
    scale = 1e8 if more >= 1e9 else 1e5
    amount = _gigabytes(math.ceil(more / scale) * scale)
    if mapped:
        return f"where `ulimit -v` allows {amount} more"
    if room_from.startswith("the cgroup"):
        return f"where the container allows {amount} more"
    return f"where {amount} more memory is free"


def room_said(available: int, read_from: str, said: str | None = None) -> str:
    """The memory planned on, said by the reading that was least: "the
    container allows" (a control group's limit, a container's or a SLURM
    job's), "the process may use" (``ulimit -v`` or a data limit), else
    "this machine has ... available". ``said`` is the size as said beside
    others (:func:`sizes_said`). What the process may use is read once as
    each analysis starts, and said so: it falls as the analyses before it
    take address space, and one log said 132 MB for clustering and 0.4 MB
    for MDS after it."""
    said = said or _gigabytes(available)
    if read_from.startswith("the cgroup"):
        return f"the container allows {said}"
    if read_from.startswith("RLIMIT"):
        return f"the process may use {said} as this analysis starts"
    return f"this machine has {said} available"


def _ordinal(k: int) -> str:
    """2nd, 3rd, 11th, 22nd."""
    if 10 <= k % 100 <= 20:
        return f"{k}th"
    return f"{k}{({1: 'st', 2: 'nd', 3: 'rd'}).get(k % 10, 'th')}"


class Cluster(Analysis):
    """Conformational clustering by pairwise RMSD.

    Parameters
    ----------
    methods : sequence of str, default ``("kmeans", "hierarchical")``
        Which clustering algorithms to run. Each produces its own output
        files. Valid values: ``"kmeans"``, ``"hierarchical"``, ``"dbscan"``.
        Two are run by default because they disagree in useful ways: k-means
        insists every frame joins a cluster, while hierarchical linkage shows
        how the clusters nest, and a conformational split that both find is
        worth more than one only either sees.
    n_clusters : int, default 5
        Number of clusters (used by k-means and hierarchical).
    eps : float, default 0.2
        DBSCAN distance threshold in nm.
    min_samples : int, default 5
        DBSCAN minimum samples per cluster.
    features : {"rmsd", "coordinates"}, default "rmsd"
        What the frames are compared in. ``"rmsd"`` measures every pair with
        its own optimal superposition, so the distance does not depend on how
        the molecule happens to be placed. ``"coordinates"`` superposes every
        frame onto the first and compares coordinates directly, scaled so a
        distance is still an RMSD in nm -- the cheaper approximation, exact
        only where one alignment serves every pair. The choice changes the
        answer more than any parameter here does, so it is worth stating.
    random_state : int, default 42
        The seed k-means starts from. K-means finds a local optimum, so a
        different seed can find a different clustering: one that survives a
        change of seed is a finding, and one that does not is an artefact of
        where the algorithm happened to start. Fixed by default so a run
        repeats, and settable so that can be tested -- it was written into the
        code, which made every run agree and hid the question.
    n_init : int, default 10
        How many starts k-means makes, keeping the best. More costs time and
        buys robustness against a poor start.
    linkage : {"ward", "complete", "average", "single"}, default "average"
        Hierarchical linkage method. ``"ward"`` needs the frames as points
        rather than as distances: in the coordinate space they are, and from
        an RMSD matrix a classical MDS embedding in up to ten dimensions
        stands in for them. The saved dendrogram and linkage are built on
        the same points as the labels.
    start : float or "equilibrated", default 0
        Where in the trajectory the clustering begins, in ns. At 0 every frame is
        clustered, the equilibration from the starting structure included,
        as before; a relaxation can then come out as a cluster of its own. A
        time in ns begins at the first frame at or after it, and
        ``"equilibrated"`` at the end of the equilibration Chodera's method
        detects in the RMSD of the selected atoms from the first frame. How
        many frames were clustered, from where, and whether the equilibration
        is among them is recorded under ``findings.frames`` either way.
    stride : int, default 1
        Cluster every k-th frame from ``start``. Every frame by default. The
        pairwise matrix grows with the square of the frames, so this is what
        a clustering refused for memory asks for (it names the stride that
        fits); the other analyses keep every frame. Frames closer together
        than the correlation time add little to a clustering.
    save_full_matrix : bool, default False
        Also write the whole frame-by-frame RMSD as
        ``cluster_rmsd_matrix_full.npy`` (float32, n by n), its size said in
        the log first. The map and everything that reads it need only the
        averaged matrix, so this is for reading the pairs yourself.
    selection : str, optional
        MDTraj atom selection for the RMSD calculation. Defaults to
        ``"protein and name CA"`` (CA-only is fast and capture the global fold well).
    **kwargs
        Standard base-class options.

    Output
    ------
    Per method, in ``<output_dir>/cluster/``:
      - ``cluster_<method>.dat`` — CSV with ``frame, cluster`` columns.
      - ``cluster_<method>.png`` — Cluster timeline figure.
      - ``cluster_<method>_populations.csv`` and one
        ``cluster_<method>_medoid_<k>.pdb`` per cluster.
    Once per run: ``cluster_rmsd_matrix.npz`` and ``cluster_rmsd_matrix.png``,
    the matrix averaged in blocks to at most :data:`MOST_MAP_CELLS` across.
    """

    name = "cluster"
    #: Populations are weighted counts, so a biased run's cluster occupancies
    #: are recoverable. Which clusters exist is not: see the base class.
    reweightable_populations = True
    description = "Conformational clustering"
    default_selection = ALPHA_CARBONS
    #: A superposition needs three atoms to be defined. Without this,
    #: MDTraj returns identity rotations and the frames are compared
    #: unaligned -- a real run clustered a capped alanine and found one
    #: distinct cluster where five were asked for, and reported ok.
    min_atoms_to_align = 3

    def __init__(
        self,
        *,
        methods: Sequence[str] = ("kmeans", "hierarchical"),
        features: str = "rmsd",
        n_clusters: int = 5,
        eps: float = 0.2,
        min_samples: int = 5,
        linkage: str = "average",
        random_state: int = 42,
        n_init: int = 10,
        start: float | str = 0.0,
        stride: int = 1,
        save_full_matrix: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # Declared as a tuple rather than filled in from None, so the default
        # is visible to anything that reads the signature -- a form drawing a
        # control for it could not otherwise say what it would do.
        methods = list(methods) if methods else ["kmeans", "hierarchical"]
        methods = [m.lower() for m in methods]
        unknown = [m for m in methods if m not in VALID_METHODS]
        if unknown:
            raise StudyError(
                f"Unknown clustering method(s): {unknown}. Valid: {VALID_METHODS}"
            , code="analysis.option.not_permitted")
        self.methods: list[str] = methods
        features = str(features).lower()
        if features not in VALID_FEATURES:
            raise StudyError(
                f"Unknown clustering features {features!r}. "
                f"Valid: {', '.join(VALID_FEATURES)}"
            , code="analysis.option.not_permitted")
        self.features: str = features
        self.n_clusters: int = int(n_clusters)
        self.eps: float = float(eps)
        self.min_samples: int = int(min_samples)
        self.linkage: str = str(linkage)
        self.random_state: int = int(random_state)
        self.n_init: int = int(n_init)
        if isinstance(stride, bool) or int(stride) != stride or int(stride) < 1:
            raise StudyError(
                f"`stride` is the k of every k-th frame, a whole number of 1 or "
                f"more; {stride!r} was given.",
                code="analysis.option.out_of_range", option="stride",
                given=stride, frames=None)
        self.stride: int = int(stride)
        self.save_full_matrix: bool = bool(save_full_matrix)
        self.options.update(
            methods=self.methods,
            features=self.features,
            n_clusters=self.n_clusters,
            eps=self.eps,
            min_samples=self.min_samples,
            linkage=self.linkage,
            random_state=self.random_state,
            n_init=self.n_init,
            stride=self.stride,
            save_full_matrix=self.save_full_matrix,
        )
        self.start: float | str | None = start_as_given(start)
        if self.start is not None:
            self.options["start"] = self.start

    def compute(self, traj: md.Trajectory) -> dict[str, np.ndarray]:
        """Run all requested clustering methods.

        Returns
        -------
        dict
            Maps method name → array of per-frame cluster labels (int).
            DBSCAN's ``-1`` label indicates "noise" (unclustered frames).
        """
        atom_idx = self.select_atoms(traj)
        refuse_too_few_to_align(self, traj, atom_idx)
        first, record = first_frame(traj, atom_idx, self.start)
        if self.stride > 1:
            clustered = -(-(traj.n_frames - first) // self.stride)
            record["stride"] = self.stride
            record["n_frames_clustered"] = clustered
            record["said"] = (f"{record['said']} Every {_ordinal(self.stride)} frame "
                              f"of those was clustered (`stride: {self.stride}`), "
                              f"{clustered} frames.")
        self.findings["frames"] = record
        self._first_frame = first
        if first or self.stride > 1:
            traj = traj[first::self.stride]
        # Kept for the medoids and the frame times written beside the labels.
        self._clustered = traj

        # k-means and hierarchical clustering need at least as many frames
        # (samples) as requested clusters. Fail with an actionable message
        # rather than letting scikit-learn raise an opaque internals error.
        n_frames = traj.n_frames
        partitioning = [m for m in self.methods if m in ("kmeans", "hierarchical")]
        if partitioning and n_frames < self.n_clusters:
            raise StudyError(
                f"Clustering needs at least n_clusters={self.n_clusters} "
                f"frames, but the trajectory has only {n_frames}. Use a longer "
                f"trajectory, or set a smaller n_clusters (e.g. n_clusters="
                f"{max(2, n_frames)} or fewer)."
            , code="analysis.sampling.too_few_frames")

        # Before anything of the frames' square is made.
        self._refuse_past_memory(n_frames, first, record)
        with threads_held_to_one(self.findings):
            try:
                return self._clustered_by_each_method(traj, atom_idx)
            except (MemoryError, OSError) as error:
                if not ran_out(error):
                    raise
                self._distances = self._embedding = None
                raise StudyError(
                    f"Clustering {n_frames:,} frames ran out of memory ({error}), "
                    "where the check planned it would fit: the memory left fell while "
                    f"it ran. Set `stride: {2 * self.stride}` under the analysis's "
                    "`options: {cluster: ...}` to cluster half as many frames; the "
                    "other analyses keep every frame.",
                    code="analysis.memory.matrix_too_large", frames=n_frames,
                    bytes_needed=self.findings["memory"].get("bytes_needed"),
                    bytes_available=self.findings["memory"].get("bytes_available"),
                    stride=2 * self.stride) from error

    def _clustered_by_each_method(self, traj: md.Trajectory,
                                  atom_idx: np.ndarray) -> dict[str, np.ndarray]:
        """The frames' distances, and each method's labels of them."""
        if self.features == "coordinates":
            embedding = _superposed_coordinates(traj, atom_idx)
            # Only a linkage read from distances needs them all at once;
            # k-means, Ward and DBSCAN work from the points.
            distances = (_euclidean_matrix(embedding)
                         if builds_matrix(self.methods, self.features, self.linkage)
                         else None)
        else:
            embedding = None
            distances = _pairwise_rmsd(traj, atom_idx)
        self._distances = distances  # cached for the medoids and the map
        self._embedding = embedding

        results: dict[str, np.ndarray] = {}
        for method in self.methods:
            if method == "kmeans":
                results[method] = _cluster_kmeans(
                    distances, self.n_clusters, embedding=embedding,
                    random_state=self.random_state, n_init=self.n_init)
            elif method == "hierarchical":
                results[method] = _cluster_hierarchical(
                    distances, self.n_clusters, self.linkage,
                    embedding=embedding,
                )
                self._hierarchy_points = None
                if self.linkage == "ward":
                    # Kept so the saved hierarchy is built on the points the
                    # labels came from, and said which they were.
                    self._hierarchy_points = _ward_points(distances, embedding)
                    self.findings["hierarchical"] = {
                        "ward_points": (
                            "the superposed coordinates, scaled to RMSD"
                            if embedding is not None else
                            "a classical MDS embedding of the pairwise RMSD"),
                        "ward_dimensions": int(self._hierarchy_points.shape[1]),
                    }
            elif method == "dbscan":
                results[method] = _cluster_dbscan(
                    distances, self.eps, self.min_samples, points=embedding,
                )
                # Its lists of neighbours, freed, given back before the
                # figures and the map are plotted.
                give_back_freed()

        return results

    def _refuse_past_memory(self, n: int, first: int, record: dict[str, Any]) -> None:
        """Refuse to cluster ``n`` frames where their peak would not fit in
        the memory this process can take, with the stride that fits; record
        the check either way.

        Two rooms, from one reading: the resident memory the machine and a
        container leave (:func:`memory_available`), planned on with
        :func:`memory_needed`, and the address space ``ulimit -v`` leaves
        (:func:`address_room`), planned on with :func:`address_space_needed`.
        Each must hold its need in its share (:data:`MEMORY_SHARE`); the one
        that holds it least is said."""
        methods, features, linkage = self.methods, self.features, self.linkage
        needed = memory_needed(n, methods, features, linkage)
        available, read_from = memory_available()
        address, address_from = address_room(available, read_from)
        available, read_from = resident_room(available, read_from)
        check: dict[str, Any] = {
            "frames": n, "bytes_needed": needed,
            "bytes_matrix": matrix_needed(n, methods, features, linkage),
            "bytes_map": map_needed(n),
            "bytes_available": available, "available_read_from": read_from,
            "share_usable": MEMORY_SHARE,
            # The address space a limit leaves, apart from the resident
            # memory, and what clustering maps there.
            "bytes_address_space": address, "address_share_usable": ADDRESS_SHARE,
            "bytes_address_needed": (address_space_needed(n, methods, features, linkage)
                                     if address is not None else None),
        }
        self.findings["memory"] = check

        def short(m: int) -> list[tuple[int, int, str, bool]]:
            """Each room ``m`` frames do not fit in: need, room, where read,
            and whether it is address space."""
            found = []
            if available is not None and memory_needed(
                    m, methods, features, linkage) > MEMORY_SHARE * available:
                found.append((memory_needed(m, methods, features, linkage),
                              available, read_from, False))
            if address is not None and address_space_needed(
                    m, methods, features, linkage) > ADDRESS_SHARE * address:
                found.append((address_space_needed(m, methods, features, linkage),
                              address, address_from, True))
            return sorted(found, key=lambda each: each[0] / max(each[1], 1)
                          / (ADDRESS_SHARE if each[3] else MEMORY_SHARE))

        over = short(n)
        if not over:
            return
        # Refused before anything of it ran, as its card says.
        self._refused_before_it_ran = True
        need, room, room_from, mapped = over[-1]
        given = int(record["n_frames_analysed"])
        stride = self.stride
        while stride < given and short(-(-given // stride)):
            stride += 1
        fits = -(-given // stride)
        starting = (f" from frame {first}" if first else "")
        if short(fits):
            # Even a frame or two: the map's figure, or what the figures
            # map, alone is more than the room planned on.
            least = min(given, max(2, self.n_clusters))
            least_need, room, room_from, mapped = short(least)[-1]
            raise StudyError(
                f"Clustering does not fit in the memory at any stride. Clustering "
                f"{n:,} frames{starting}, or as few as there are clusters, takes about "
                + need_and_room_said(least_need, room, room_from, mapped, "clustering",
                                     besides=(address_besides(least, methods, features, linkage)
                                              if mapped else None))
                + f". Run it {more_said(least_need, room, mapped, room_from)}, or leave `cluster` "
                f"out of the analyses. Read as {room_from}.",
                code="analysis.memory.matrix_too_large", frames=n,
                bytes_needed=least_need, bytes_available=room, stride=None)
        check["stride_that_fits"] = stride
        made = (f"for their frame-by-frame RMSD matrix and the copies "
                f"{', '.join(methods)} make of it"
                if check["bytes_matrix"] >= check["bytes_map"] else
                "to plot the map of their RMSD matrix, once the matrix is freed")
        # What to do first, in a sentence a row gives alone; where the memory
        # was read last, as the record keeps it (`available_read_from`).
        raise StudyError(
            f"Clustering every {_ordinal(stride)} frame, {fits:,} frames, fits in the "
            f"memory, where clustering {n:,} frames{starting} does not. "
            f"Set `stride: {stride}` under the analysis's `options: {{cluster: ...}}` "
            "for it; the other analyses keep every frame. "
            f"The {n:,} frames need about "
            + need_and_room_said(need, room, room_from, mapped, "clustering",
                                 besides=(address_besides(n, methods, features, linkage)
                                          if mapped else None),
                                 made=made)
            + f". Read as {room_from}.",
            code="analysis.memory.matrix_too_large", frames=n,
            bytes_needed=need, bytes_available=room, stride=stride)

    _refused_before_it_ran = False

    # --------------------------------------------------------------
    # Override run() to handle multi-output (one file pair per method)
    # --------------------------------------------------------------
    def run(self, traj: md.Trajectory) -> AnalysisResult:
        from datetime import datetime, timezone

        # Its figures are closed if it fails part-way, as base.run's are.
        opened = figures_open()
        started = datetime.now(timezone.utc).isoformat()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        options_path = self._write_options_manifest()

        try:
            self.result = self.compute(traj)
            # Written again, as the base class does, so what the clustering
            # found out about its own run is kept beside what it was told.
            options_path = self._write_options_manifest()
            artifacts: list[Path] = []
            for method, labels in self.result.items():
                # Data file: frame, cluster
                data_path = self.output_dir / f"cluster_{method}.dat"
                # The frame in the trajectory given, so a clustering begun
                # later than the first frame says which frames it labelled.
                df = pd.DataFrame(
                    {"frame": self._frames_given(len(labels)),
                     "cluster": labels}
                )
                df.to_csv(data_path, index=False)
                artifacts.append(data_path)

                # Figure
                fig_path = self.output_dir / f"cluster_{method}.png"

                def timeline(method: str = method, labels: np.ndarray = labels,
                             path: Path = fig_path) -> None:
                    fig, ax = new_figure(
                        title=f"{self.figure_title()} ({method})",
                        figsize=self._user_figsize,
                    )
                    _plot_cluster_timeline(ax, labels, method,
                                           frames=self._frames_given(len(labels)))
                    ax.set_xlabel(self._user_xlabel or "Frame")
                    ax.set_ylabel(self._user_ylabel or "Cluster")
                    save_figure(fig, path)

                artifacts.extend(self._plotted(fig_path, timeline))

                counts_path = self.output_dir / f"cluster_{method}_counts.png"

                def counts(method: str = method, labels: np.ndarray = labels,
                           path: Path = counts_path) -> None:
                    fig_counts, ax_counts = new_figure(
                        title=f"Cluster populations ({method})",
                        figsize=(5.8, 3.6),
                    )
                    _plot_cluster_counts(ax_counts, labels)
                    save_figure(fig_counts, path)

                artifacts.extend(self._plotted(counts_path, counts))

                artifacts.extend(self._write_states(method, labels))

                if method == "hierarchical":
                    dendro_path = self.output_dir / "cluster_hierarchical_dendrogram.png"
                    skip_path = self.output_dir / "cluster_hierarchical_dendrogram_skipped.json"
                    try:
                        linkage_matrix = _hierarchical_linkage_matrix(
                            self._distances,
                            self.linkage,
                            points=getattr(self, "_hierarchy_points", None),
                        )
                        linkage_path = self.output_dir / "hierarchical_linkage.npy"
                        np.save(linkage_path, linkage_matrix)
                        artifacts.append(linkage_path)
                        before_the_dendrogram = figures_open()
                        fig_den, ax_den = new_figure(
                            title="Hierarchical clustering dendrogram",
                            figsize=(6.5, 3.8),
                        )
                        _plot_hierarchical_dendrogram_from_linkage(ax_den, linkage_matrix)
                        save_figure(fig_den, dendro_path)
                        artifacts.append(dendro_path)
                        dendro_svg_path = dendro_path.with_suffix(".svg")
                        if dendro_svg_path.is_file():
                            artifacts.append(dendro_svg_path)
                        if skip_path.exists():
                            skip_path.unlink()
                    except Exception as dendro_exc:  # noqa: BLE001
                        close_figures_opened_since(before_the_dendrogram)
                        reason = (self._not_plotted(dendro_path, dendro_exc)
                                  if ran_out(dendro_exc) else str(dendro_exc))
                        skip_path.write_text(
                            json.dumps(
                                {
                                    "artifact": dendro_path.name,
                                    "status": "skipped",
                                    "reason": reason,
                                },
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        artifacts.append(skip_path)

            artifacts.extend(self._write_distance_matrix())
            # Again, with how the map's blocks were made.
            options_path = self._write_options_manifest()
            finished = datetime.now(timezone.utc).isoformat()

            # Use the first method's outputs as the "primary" data/figure
            # paths in the AnalysisResult; all are listed in the artifacts
            # field of the analysis manifest.
            primary_method = self.methods[0]
            return AnalysisResult(
                name=self.name,
                status="ok",
                data=self.result,
                output_dir=self.output_dir,
                figure_path=self.output_dir / f"cluster_{primary_method}.png",
                data_path=self.output_dir / f"cluster_{primary_method}.dat",
                options_path=options_path,
                artifacts=[options_path, *artifacts],
                message=f"{self.name}: ok ({', '.join(self.methods)})" + "".join(
                    f"; {said}, the memory ran out: " + ", ".join(self.findings[key])
                    for key, said in (("figures_not_plotted", "not plotted"),
                                      ("not_saved", "not saved"))
                    if self.findings.get(key)),
                started_at=started,
                finished_at=finished,
            )
        except Exception as exc:  # noqa: BLE001
            close_figures_opened_since(opened)
            finished = datetime.now(timezone.utc).isoformat()
            # In the log, as every other analysis's failure is (`base.run`).
            logger.error("Analysis '%s' failed", self.name, exc_info=True,
                         extra={"to_console": False})
            if self.findings:
                # What it found before it stopped: the memory it checked, and
                # the refusal's numbers with it.
                code = getattr(exc, "code", None)
                if code:
                    # Whether it was refused before it ran, or stopped part-way.
                    self.findings["refused"] = {
                        "code": code, "said": str(exc),
                        "before_it_ran": self._refused_before_it_ran}
                try:
                    options_path = self._write_options_manifest()
                except Exception:  # noqa: BLE001 -- the failure matters more
                    pass
            return AnalysisResult(
                name=self.name,
                status="error",
                output_dir=self.output_dir,
                options_path=options_path,
                message=f"{self.name}: {exc}",
                started_at=started,
                finished_at=finished,
            )

    _first_frame: int = 0
    _clustered: md.Trajectory | None = None
    _distances: np.ndarray | None = None
    _embedding: np.ndarray | None = None

    def _times_ns(self) -> np.ndarray | None:
        """The clustered frames' times in ns, or None where there is no clock."""
        traj = self._clustered
        if traj is None:
            return None
        time_ps = np.asarray(traj.time, dtype=float)
        if time_ps.size != traj.n_frames or not np.all(np.isfinite(time_ps)):
            return None
        if time_ps.size > 1 and not np.all(np.diff(time_ps) > 0):
            return None
        return time_ps / 1000.0

    def _distance_name(self) -> str:
        return ("RMSD after superposing each pair" if self.features == "rmsd"
                else "RMSD under one superposition onto the first frame")

    def _frames_given(self, n: int) -> np.ndarray:
        """Each clustered frame's index in the trajectory given."""
        return self._first_frame + self.stride * np.arange(n)

    def _distance_rows(self, rows: np.ndarray | slice,
                       columns: np.ndarray | None = None) -> np.ndarray:
        """The distances from the frames ``rows`` to ``columns`` (every
        clustered frame where None), from the matrix where one was made, else
        from the points, a block at a time. Rows given as a slice, to every
        frame, are read from the matrix in place, not copied."""
        distances = getattr(self, "_distances", None)
        if distances is not None:
            if columns is None:
                return distances[rows]
            return distances[np.ix_(rows, columns)]
        from scipy.spatial.distance import cdist

        points = self._embedding
        return cdist(points[rows], points if columns is None else points[columns])

    def _rows_at_a_time(self, n: int) -> int:
        """How many rows of distances to n frames make about 64 MB."""
        return max(1, int(ROWS_BYTES // (8 * max(n, 1))))

    def _write_distance_matrix(self) -> list[Path]:
        """The frame-to-frame distances the clustering used, as data and a map.

        ``cluster_rmsd_matrix.npz`` holds ``rmsd_nm`` (nm, float32), the
        matrix with its rows and columns averaged in blocks of ``block``
        consecutive clustered frames (the last block takes what is left) so
        that it is at most :data:`MOST_MAP_CELLS` across; ``block``;
        ``frames`` (every clustered frame's index in the trajectory given)
        and ``time_ns`` (NaN where the trajectory has no clock); and
        ``distance`` naming which RMSD it is. The map is plotted from this
        array, and the GUI and the report read this file, so all three show
        the same numbers. A study written before kept the whole matrix here,
        with no ``block``, and is read as it was.

        The whole matrix is written only when ``save_full_matrix`` asks. It is
        freed before the map is plotted, which plots from the averaged one
        (:func:`map_needed`): plotted beside the whole matrix, the map was
        more than the room planned on at 2,000 frames under a container's
        limit of about 100 MB, and the process was killed.
        """
        distances = getattr(self, "_distances", None)
        if distances is None and getattr(self, "_embedding", None) is None:
            return []
        n = distances.shape[0] if distances is not None else self._embedding.shape[0]
        del distances
        frames = self._frames_given(n)
        times = self._times_ns()
        block = map_block(n)
        averaged = self._averaged_in_blocks(n, block)
        self.findings["matrix"] = {
            "frames": int(n), "block": int(block), "cells": int(averaged.shape[0]),
            "said": (f"The map's rows and columns are means over blocks of {block} "
                     f"consecutive clustered frames, {averaged.shape[0]} across."
                     if block > 1 else "The map shows every pair of clustered frames."),
        }
        data_path = self.output_dir / "cluster_rmsd_matrix.npz"
        written = []
        try:
            np.savez_compressed(
                data_path, rmsd_nm=averaged.astype(np.float32),
                block=np.int64(block),
                frames=frames.astype(np.int64),
                time_ns=(times if times is not None else np.full(n, np.nan)),
                distance=np.array(self._distance_name()))
            written.append(data_path)
        except (MemoryError, OSError) as error:
            # Its zip's buffer: the CLI ended on "Unable to allocate output
            # buffer." with no setting. Said, and the map still plotted.
            if not ran_out(error):
                raise
            data_path.unlink(missing_ok=True)
            self._not_plotted(data_path, error, saving=True)
        if self.save_full_matrix:
            written.append(self._write_full_matrix(n))
        # Nothing reads the whole matrix past here. Freed, and given back
        # with what was freed before it (DBSCAN's lists of neighbours, the
        # medoids' blocks of rows): glibc's heap held them resident, the
        # matrix too once its threshold for a mapping of its own had risen,
        # and the map was plotted beside them.
        self._distances = None
        give_back_freed()

        figure_path = self.output_dir / "cluster_rmsd_matrix.png"
        title = ("RMSD between frames" if block == 1
                 else f"RMSD between frames, means over blocks of {block}")

        def the_map() -> None:
            fig, ax = new_figure(title=title, figsize=(5.6, 4.8))
            _plot_distance_matrix(ax, averaged, frames, times, block=block)
            save_figure(fig, figure_path)

        return written + self._plotted(figure_path, the_map)

    def _plotted(self, path: Path, plot: Any) -> list[Path]:
        """The figure ``plot`` saves at ``path``, and its SVG. Where the
        memory runs out plotting it, none: said in the record
        (``figures_not_plotted``, :meth:`_not_plotted`), and the other
        figures and files are still saved. Under ``ulimit -v`` a figure
        clustering planned for ended the analysis with ``std::bad_alloc``,
        and the files after it were lost."""
        opened = figures_open()
        try:
            plot()
        except (MemoryError, OSError) as error:
            if not ran_out(error):
                raise
            close_figures_opened_since(opened)
            for leftover in (path, path.with_suffix(".svg")):
                leftover.unlink(missing_ok=True)
            self._not_plotted(path, error)
            return []
        return [path] + ([path.with_suffix(".svg")]
                         if path.with_suffix(".svg").is_file() else [])

    def _not_plotted(self, path: Path, error: BaseException, saving: bool = False) -> str:
        """What a figure the memory ran out plotting says, or the map's data
        it ran out saving (``saving``), recorded and logged: the rest saved,
        and what to set."""
        if saving:
            said = (f"{path.name} was not saved: the memory ran out saving it "
                    f"({error}). The labels and the other figures are saved. Set "
                    f"`stride: {2 * self.stride}` under the analysis's "
                    "`options: {cluster: ...}` to save the map of half as many frames.")
            self.findings.setdefault("not_saved", {})[path.name] = said
            logger.warning(said)
            return said
        said = (f"{path.name} was not plotted: the memory ran out plotting it "
                f"({error}). Its data is saved. Set `stride: {2 * self.stride}` under "
                "the analysis's `options: {cluster: ...}` to plot half as many "
                "frames.")
        self.findings.setdefault("figures_not_plotted", {})[path.name] = said
        logger.warning(said)
        return said

    def _averaged_in_blocks(self, n: int, block: int) -> np.ndarray:
        """The matrix's rows and columns averaged in blocks of ``block``
        frames, read a few rows at a time, in float64."""
        starts = np.arange(0, n, block)
        sizes = np.diff(np.append(starts, n)).astype(np.float64)
        if block == 1 and getattr(self, "_distances", None) is not None:
            # At most MOST_MAP_CELLS frames: the matrix is the map.
            return np.asarray(self._distances, dtype=np.float64)
        summed = np.zeros((starts.size, starts.size), dtype=np.float64)
        step = max(block, self._rows_at_a_time(n) // block * block)
        for low in range(0, n, step):
            high = min(low + step, n)
            part = np.add.reduceat(self._distance_rows(slice(low, high)), starts, axis=1)
            summed[low // block:((high - 1) // block) + 1] += np.add.reduceat(
                part, np.arange(0, high - low, block), axis=0)
            del part
        summed /= sizes[:, None]
        summed /= sizes[None, :]
        return summed

    def _write_full_matrix(self, n: int) -> Path:
        """Every pair, in float32, written a block of rows at a time; its size
        said in the log first."""
        path = self.output_dir / "cluster_rmsd_matrix_full.npy"
        logger.info("Writing the whole %d by %d RMSD matrix, %s, to %s "
                    "(`save_full_matrix: true`)", n, n, _gigabytes(4 * n * n), path)
        out = np.lib.format.open_memmap(path, mode="w+", dtype=np.float32, shape=(n, n))
        step = self._rows_at_a_time(n)
        for low in range(0, n, step):
            rows = np.arange(low, min(low + step, n))
            out[low:low + rows.size] = self._distance_rows(rows)
        out.flush()
        del out
        self.findings["matrix_full"] = {"path": path.name, "bytes": int(4 * n * n),
                                        "dtype": "float32"}
        return path

    def _write_states(self, method: str, labels: np.ndarray) -> list[Path]:
        """Each cluster's share, its medoid, and the medoid as a structure.

        The medoid is the member with the least summed distance to the
        cluster's other members, in the distances the clustering used. Frames
        DBSCAN calls noise belong to no cluster and have no row. The
        structure is that frame of the trajectory given, without its water.
        """
        traj = self._clustered
        if traj is None or (getattr(self, "_distances", None) is None
                            and getattr(self, "_embedding", None) is None):
            return []
        times = self._times_ns()
        n = len(labels)
        rows = []
        written: list[Path] = []
        solute = traj.topology.select("not water")
        if solute.size == 0:
            solute = np.arange(traj.n_atoms)
        for label in sorted(set(int(k) for k in labels) - {-1}):
            members = np.nonzero(labels == label)[0]
            # A block of members at a time: a cluster of most of the frames
            # would otherwise copy most of the matrix.
            step = self._rows_at_a_time(members.size)
            summed = np.concatenate([
                self._distance_rows(members[low:low + step], members).sum(axis=1)
                for low in range(0, members.size, step)])
            medoid = int(members[int(np.argmin(summed))])
            structure_path = self.output_dir / f"cluster_{method}_medoid_{label}.pdb"
            traj[medoid].atom_slice(solute).save_pdb(str(structure_path))
            written.append(structure_path)
            rows.append({
                "cluster": label,
                "frames": int(members.size),
                "fraction": float(members.size / n),
                "medoid_frame": int(self._frames_given(n)[medoid]),
                "medoid_time_ns": float(times[medoid]) if times is not None else float("nan"),
            })
        table_path = self.output_dir / f"cluster_{method}_populations.csv"
        pd.DataFrame(rows, columns=["cluster", "frames", "fraction", "medoid_frame",
                                    "medoid_time_ns"]).to_csv(table_path, index=False)
        written.insert(0, table_path)
        return written

    # Required by the ABC but not used (run() is overridden)
    def plot(self, result: dict[str, np.ndarray], ax: plt.Axes) -> None:
        # Plot the first method on the supplied axes — used only by
        # external callers who instantiate the figure themselves.
        primary = next(iter(result))
        _plot_cluster_timeline(ax, result[primary], primary)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def _pairwise_rmsd(traj: md.Trajectory, atom_idx: np.ndarray) -> np.ndarray:
    """Compute the n_frames × n_frames pairwise RMSD matrix.

    Uses MDTraj's QCP-based ``md.rmsd`` row by row, which aligns each
    frame to the reference before measuring. Returns a symmetric matrix
    in nm.
    """
    n = traj.n_frames
    dist = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        # Aligning frame i against the full trajectory once gives row i
        dist[i] = md.rmsd(traj, traj, frame=i, atom_indices=atom_idx)
    _symmetrise(dist)
    np.fill_diagonal(dist, 0.0)
    return dist


def _symmetrise(dist: np.ndarray) -> None:
    """Make ``dist`` symmetric (MDTraj's RMSD is symmetric up to float
    precision), the mean of each pair, a block at a time and in place.

    ``0.5 * (dist + dist.T)`` made two more matrices. Each block's mean is
    made in one buffer, made once in a mapping of its own and given back to
    the system at the end, so one block is held beside the matrix at any n
    and none is left after: ``0.5 * (a + b.T)`` held two on Python 3.13's
    NumPy, which does not reuse the sum for the product there, and up to
    about 2,900 frames, where the block is the whole matrix, that was 24 n^2
    where 16 n^2 were counted; past them, a new block for each pass was
    taken from glibc's heap, and the last one freed stayed resident after
    the matrix was made.
    """
    import mmap

    n = dist.shape[0]
    step = min(n, max(1, int(ROWS_BYTES // (8 * max(n, 1)))))
    if n == 0:
        return
    mapped = mmap.mmap(-1, 8 * step * step)
    try:
        buffer = np.frombuffer(mapped, dtype=np.float64).reshape(step, step)
        for low in range(0, n, step):
            high = min(low + step, n)
            for left in range(low, n, step):
                right = min(left + step, n)
                mean = buffer[:high - low, :right - left]
                np.add(dist[low:high, left:right], dist[left:right, low:high].T, out=mean)
                mean *= 0.5
                dist[low:high, left:right] = mean
                dist[left:right, low:high] = mean.T
        del mean, buffer
    finally:
        try:
            mapped.close()
        except BufferError:  # a view of it still held, by a traceback: freed with it
            pass


def _superposed_coordinates(
    traj: md.Trajectory, atom_idx: np.ndarray
) -> np.ndarray:
    """Frames as vectors, aligned to the first and scaled to RMSD units.

    Superposing matters. Without it the leading differences between frames are
    where the molecule drifted and how it turned, not how it changed shape, and
    two identical conformations in different orientations come out as far apart
    as anything in the trajectory.

    Dividing by sqrt(n_atoms) makes a Euclidean distance here equal to the RMSD
    between those frames under this one alignment, so distances stay in nm and
    mean the same thing they do in the other feature space.
    """
    aligned = superposed(traj, frame=0, atom_indices=atom_idx)
    coords = aligned.xyz[:, atom_idx, :].reshape(traj.n_frames, -1)
    return coords / np.sqrt(len(atom_idx))


def _euclidean_matrix(embedding: np.ndarray) -> np.ndarray:
    """Pairwise distances in the coordinate space, for a linkage read from
    distances. SciPy's, which makes no n by n by coordinates array of the
    differences, as subtracting every pair at once did."""
    from scipy.spatial.distance import cdist

    points = np.asarray(embedding, dtype=np.float64)
    return cdist(points, points)


def _cluster_kmeans(
    distances: np.ndarray,
    n_clusters: int,
    embedding: np.ndarray | None = None,
    *,
    random_state: int = 42,
    n_init: int = 10,
) -> np.ndarray:
    """K-means, which needs frames as points rather than as distances.

    The seed was fixed at 42 inside this function, which made every run agree
    with every other and hid the thing worth knowing: k-means finds a local
    optimum, and a different seed can find a different one. A clustering that
    survives a change of seed is a finding; one that does not is an artefact
    of where the algorithm happened to start, and no amount of rerunning with
    the same seed distinguishes them.
    """
    if embedding is None:
        # Project the distance matrix into a Euclidean space via classical MDS.
        embedding = _classical_mds(
            distances, n_components=min(10, distances.shape[0] - 1)
        )
    model = KMeans(n_clusters=n_clusters, n_init=n_init, random_state=random_state)
    return model.fit_predict(embedding).astype(int)


def _cluster_hierarchical(
    distances: np.ndarray,
    n_clusters: int,
    linkage: str,
    embedding: np.ndarray | None = None,
) -> np.ndarray:
    """Agglomerative hierarchical clustering.

    Every linkage but ward works from the distances directly. Ward is defined
    by variance within a cluster, so it needs the frames as points: in the
    coordinate feature space they already are, and from a distance matrix a
    classical MDS embedding stands in for them.
    """
    if linkage == "ward":
        model = AgglomerativeClustering(n_clusters=n_clusters, linkage="ward")
        return model.fit_predict(_ward_points(distances, embedding)).astype(int)

    model = AgglomerativeClustering(
        n_clusters=n_clusters,
        metric="precomputed",
        linkage=linkage,
    )
    return model.fit_predict(distances).astype(int)


def _ward_points(distances: np.ndarray, embedding: np.ndarray | None) -> np.ndarray:
    """The points Ward's linkage is computed on: the coordinates where the
    frames are compared in them, else a classical MDS embedding of the
    distances in up to ten dimensions."""
    if embedding is not None:
        return np.asarray(embedding, dtype=np.float64)
    return _classical_mds(distances, n_components=min(10, distances.shape[0] - 1))


def _cluster_dbscan(
    distances: np.ndarray | None, eps: float, min_samples: int,
    points: np.ndarray | None = None,
) -> np.ndarray:
    """DBSCAN on the precomputed RMSD matrix, or on the points where the
    frames are compared in coordinates (the same distances, without the
    matrix).

    Frames classified as noise receive label -1. The number of clusters
    is determined by the algorithm based on ``eps`` and ``min_samples``.
    """
    if points is not None:
        model = DBSCAN(eps=eps, min_samples=min_samples)
        return model.fit_predict(np.asarray(points, dtype=np.float64)).astype(int)
    model = DBSCAN(eps=eps, min_samples=min_samples, metric="precomputed")
    return model.fit_predict(distances).astype(int)


def _classical_mds(distances: np.ndarray, n_components: int) -> np.ndarray:
    """Classical multidimensional scaling on a distance matrix.

    Returns an (n_samples, n_components) embedding in which Euclidean
    distance approximates the input distance. Standard cmdscale formula:
        B = -0.5 * H * D^2 * H,  where H = I - 1/n
    eigendecompose B; coordinates = U * sqrt(lambda).
    """
    from scipy.linalg import eigh

    n = distances.shape[0]
    # H D^2 H by its row and column means, in one n by n array: the centring
    # matrix, written out, and its products made four more.
    b = np.square(distances, dtype=np.float64)
    means = b.mean(axis=0)
    b -= means[None, :]
    b -= means[:, None]
    b += means.mean()
    b *= -0.5
    # The top-k eigenpairs only, in place: B is symmetric, so its transpose
    # is the same matrix in the Fortran order LAPACK takes without a copy.
    k = int(min(n_components, n))
    eigvals, eigvecs = eigh(b.T, subset_by_index=[n - k, n - 1], overwrite_a=True,
                            check_finite=False)
    # Take the top-k positive eigenvalues
    idx = np.argsort(eigvals)[::-1][:n_components]
    keep_vals = np.maximum(eigvals[idx], 0)
    return eigvecs[:, idx] * np.sqrt(keep_vals)


def _plot_cluster_timeline(ax: plt.Axes, labels: np.ndarray, method: str,
                           first: int = 0, frames: np.ndarray | None = None) -> None:
    """Plot per-frame cluster labels as a scatter / step plot, against the
    frame of the trajectory given (``frames``, else ``first`` onward)."""
    if frames is None:
        frames = first + np.arange(len(labels))
    unique = sorted(set(labels))
    # The figures' own palette, so a greyscale copy is grey: Tableau's
    # colours were written in here and came out in colour in both. Noise
    # (-1) is a light grey dot either way.
    for k in unique:
        mask = labels == k
        color, marker = ("#BBBBBB", "o") if k == -1 else category_style(int(k))
        ax.scatter(
            frames[mask],
            labels[mask],
            s=8,
            c=[color],
            marker=marker,
            edgecolor="none",
            label=f"cluster {k}" if k != -1 else "noise",
        )
    ax.set_yticks(unique)
    if len(unique) <= 10:
        ax.legend(loc="best", fontsize=8, ncol=2)


def _plot_distance_matrix(ax: plt.Axes, distances: np.ndarray, frames: np.ndarray,
                          times_ns: np.ndarray | None, block: int = 1) -> None:
    """The frame-to-frame RMSD as a map, time against time, in nm.

    Each cell is one pair of frames, or the mean over two blocks of
    ``block`` frames, so blocks along the diagonal are spells the structure
    held, and an off-diagonal block that is dark is a return to a structure
    visited before. ``frames`` and ``times_ns`` are every clustered frame's;
    each cell is ``block`` frames wide, and the last one the frames it holds,
    drawn its own width.
    """
    cells = distances.shape[0]
    if times_ns is not None and len(times_ns) > 1 and np.all(np.isfinite(times_ns)):
        step = float(np.median(np.diff(times_ns)))
        low = float(times_ns[0]) - step / 2
        label = "Time (ns)"
    else:
        step = float(np.median(np.diff(frames))) if len(frames) > 1 else 1.0
        low = float(frames[0]) - step / 2
        label = "Frame"
    # Each cell's edges by the frames it holds: the last block holds what is
    # left, and is drawn that wide, not a whole block past the last frame.
    edges = low + step * np.minimum(np.arange(cells + 1) * block, len(frames))
    image = ax.pcolormesh(edges, edges, distances, cmap="viridis", vmin=0.0,
                          shading="flat", rasterized=True)
    ax.set_aspect("equal")
    bar = ax.figure.colorbar(image, ax=ax, shrink=0.85)
    bar.set_label("RMSD (nm)")
    match_colorbar_font(bar, ax)
    ax.set_xlabel(label)
    ax.set_ylabel(label)


def _plot_cluster_counts(ax: plt.Axes, labels: np.ndarray) -> None:
    unique, counts = np.unique(labels, return_counts=True)
    colors = ["#BBBBBB" if k == -1 else category_style(int(k))[0] for k in unique]
    ax.bar(unique, counts, color=colors, width=0.75)
    ax.set_xlabel("Cluster")
    ax.set_ylabel("Frames")
    ax.set_xticks(unique)


def _plot_hierarchical_dendrogram(
    ax: plt.Axes,
    distances: np.ndarray,
    linkage_method: str,
) -> None:
    z = _hierarchical_linkage_matrix(distances, linkage_method)
    _plot_hierarchical_dendrogram_from_linkage(ax, z)


def _hierarchical_linkage_matrix(
    distances: np.ndarray,
    linkage_method: str,
    points: np.ndarray | None = None,
) -> np.ndarray:
    """The hierarchy behind a hierarchical clustering, as SciPy's linkage.

    Ward's linkage is computed on the same points the labels were
    (:func:`_ward_points`); passing ``points`` uses those exactly. It was
    computed by average linkage on the distances instead, so the saved
    dendrogram and ``hierarchical_linkage.npy`` described another clustering:
    cut at the number of clusters asked for, they agreed with the labels to
    an adjusted Rand index of 0.64 on a drifting trajectory.
    """
    try:
        from scipy.cluster.hierarchy import linkage
        from scipy.spatial.distance import squareform
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise BackendUnavailable("SciPy is required for dendrogram generation",
                                 packages=["scipy"], code="environment.backend.missing") from exc

    frames = (distances if distances is not None else points).shape[0]
    if frames < 2:
        raise StudyError("at least two frames are required for a dendrogram", code="analysis.sampling.too_few_frames")
    if linkage_method == "ward":
        if points is None:
            points = _ward_points(distances, None)
        return linkage(np.asarray(points, dtype=np.float64), method="ward")
    condensed = squareform(distances, checks=False)
    return linkage(condensed, method=linkage_method)


def _plot_hierarchical_dendrogram_from_linkage(
    ax: plt.Axes,
    linkage_matrix: np.ndarray,
) -> None:
    try:
        from scipy.cluster.hierarchy import dendrogram
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise BackendUnavailable("SciPy is required for dendrogram generation",
                                 packages=["scipy"], code="environment.backend.missing") from exc

    dendrogram(linkage_matrix, ax=ax, no_labels=True, color_threshold=None)
    ax.set_xlabel("Frame")
    # The linkage's distance: RMSD, or Ward's between the points, in nm.
    ax.set_ylabel("Distance (nm)")
    ax.set_ylim(bottom=0)


register_analysis(Cluster.name, Cluster)
