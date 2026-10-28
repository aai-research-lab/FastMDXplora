"""A clustering of a long run writes no frame-by-frame matrix by default, and
one too large for the machine is refused with the stride that fits.

A chignolin study extended to 128,000 frames was killed by the kernel while
it clustered them: the float64 matrix (131 GB) and its float32 copy (66 GB),
and every study before it saved that matrix twice beside its results.
"""

from __future__ import annotations

import json
import logging
import math
import sys
import tracemalloc

import mdtraj as md
import numpy as np
import pytest

import fastmdxplora.analysis  # noqa: F401 -- registers the analyses
from fastmdxplora.analysis import cluster as clustering
from fastmdxplora.analysis.cluster import (
    MEMORY_SHARE,
    MOST_MAP_CELLS,
    Cluster,
    _pairwise_rmsd,
    memory_needed,
)


def _chain(n_frames: int, n_atoms: int = 4, seed: int = 0) -> md.Trajectory:
    """Many frames of a few alpha carbons, wandering about a shape, with a
    clock of 10 ps a frame."""
    rng = np.random.default_rng(seed)
    top = md.Topology()
    chain = top.add_chain()
    for i in range(n_atoms):
        residue = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, residue)
    shape = rng.normal(0.0, 0.4, (n_atoms, 3))
    drift = np.cumsum(rng.normal(0.0, 0.01, (n_frames, n_atoms, 3)), axis=0)
    traj = md.Trajectory((shape[None] + drift).astype(np.float32), top)
    traj.time = np.arange(n_frames) * 10.0
    return traj


#: Past the bound, so the map is of blocks of two frames.
LONG = MOST_MAP_CELLS + 100


@pytest.fixture(scope="module")
def clustered(tmp_path_factory):
    out = tmp_path_factory.mktemp("long")
    traj = _chain(LONG)
    result = Cluster(output_dir=out).run(traj)
    return traj, result


class TestALongRunWritesNoMatrixOverTheBound:

    def test_nothing_of_the_frames_square_is_written(self, clustered):
        traj, result = clustered
        assert result.status == "ok", result.message
        folder = result.output_dir
        assert not (folder / "hierarchical_distance_matrix.npy").exists()
        assert not (folder / "cluster_rmsd_matrix_full.npy").exists()
        assert (folder / "hierarchical_linkage.npy").is_file()
        saved = np.load(folder / "cluster_rmsd_matrix.npz")
        cells = math.ceil(traj.n_frames / 2)
        assert int(saved["block"]) == 2
        assert saved["rmsd_nm"].shape == (cells, cells)
        assert max(saved["rmsd_nm"].shape) <= MOST_MAP_CELLS
        # Every clustered frame's index and time, for the axis.
        assert saved["frames"].shape == (traj.n_frames,)
        assert np.allclose(saved["time_ns"], traj.time / 1000.0)

    def test_the_map_is_the_block_means_of_the_matrix(self, clustered):
        traj, result = clustered
        saved = np.load(result.output_dir / "cluster_rmsd_matrix.npz")
        atoms = traj.topology.select("name CA")
        full = _pairwise_rmsd(traj, atoms)
        starts = np.arange(0, traj.n_frames, 2)
        sizes = np.diff(np.append(starts, traj.n_frames))
        expected = (np.add.reduceat(np.add.reduceat(full, starts, axis=0), starts, axis=1)
                    / np.outer(sizes, sizes))
        assert np.allclose(saved["rmsd_nm"], expected, atol=1e-6)

    def test_the_block_is_recorded_and_said_with_the_figure(self, clustered):
        _, result = clustered
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        assert found["matrix"]["block"] == 2
        assert "blocks of 2" in found["matrix"]["said"]
        assert found["memory"]["bytes_needed"] == memory_needed(
            LONG, ["kmeans", "hierarchical"], "rmsd", "average")

    def test_the_report_s_caption_says_the_block(self, clustered):
        from fastmdxplora.report.document import _caption_of_figure

        _, result = clustered
        caption = _caption_of_figure(result.output_dir, "cluster", "cluster_rmsd_matrix")
        assert "blocks of 2 frames" in caption

    def test_the_gui_reads_the_same_file(self, clustered, tmp_path):
        from fastmdxplora.gui.figure_data import MOST_CELLS, _rmsd_matrix

        _, result = clustered
        root = tmp_path / "study"
        folder = root / "analysis" / "cluster"
        folder.mkdir(parents=True)
        (folder / "cluster_rmsd_matrix.npz").write_bytes(
            (result.output_dir / "cluster_rmsd_matrix.npz").read_bytes())
        payload = _rmsd_matrix(root)
        saved = np.load(folder / "cluster_rmsd_matrix.npz")
        cells = saved["rmsd_nm"].shape[0]
        further = math.ceil(cells / MOST_CELLS)
        # Blocks of blocks: each cell is 2 x `further` frames.
        block = 2 * further
        assert payload["block"] == block
        assert len(payload["values"]) == math.ceil(cells / further)
        # Means over those frames, the last block's fewer frames weighed as
        # few: the same numbers as averaging the whole matrix so.
        traj, _ = clustered
        full = _pairwise_rmsd(traj, traj.topology.select("name CA"))
        starts = np.arange(0, traj.n_frames, block)
        sizes = np.diff(np.append(starts, traj.n_frames))
        expected = (np.add.reduceat(np.add.reduceat(full, starts, axis=0), starts, axis=1)
                    / np.outer(sizes, sizes))
        assert np.allclose(np.array(payload["values"], dtype=float), expected, rtol=1e-4)


class TestTheFullMatrixOnlyWhenAsked:
    def test_it_is_written_with_its_size_said_first(self, tmp_path, caplog):
        traj = _chain(120)
        with caplog.at_level(logging.INFO, logger="fastmdx.analysis.cluster"):
            result = Cluster(output_dir=tmp_path, save_full_matrix=True).run(traj)
        assert result.status == "ok", result.message
        path = result.output_dir / "cluster_rmsd_matrix_full.npy"
        assert path in result.artifacts
        written = np.load(path)
        assert written.dtype == np.float32 and written.shape == (120, 120)
        assert np.allclose(written, _pairwise_rmsd(traj, traj.topology.select("name CA")),
                           atol=1e-6)
        said = [r.getMessage() for r in caplog.records if "whole" in r.getMessage()]
        assert said and "120 by 120" in said[0] and "0.1 MB" in said[0]

    def test_the_setting_is_offered(self):
        from fastmdxplora.analysis.describe import describe_analysis

        names = {option.name for option in describe_analysis("cluster")}
        assert {"save_full_matrix", "stride"} <= names


class TestTheMemoryCheck:
    N = 600

    def _refused(self, tmp_path, monkeypatch, available: int):
        monkeypatch.setattr(clustering, "memory_available", lambda: (available, "mocked"))
        return Cluster(output_dir=tmp_path).run(_chain(self.N))

    def test_past_the_memory_it_is_refused_with_the_stride_that_fits(self, tmp_path, monkeypatch):
        from fastmdxplora.refusals import CODES

        methods = ["kmeans", "hierarchical"]
        available = memory_needed(self.N, methods, "rmsd", "average") // 5
        result = self._refused(tmp_path, monkeypatch, available)

        assert result.status == "error"
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        assert found["refused"]["code"] == "analysis.memory.matrix_too_large"
        assert "analysis.memory.matrix_too_large" in {code.id for code in CODES}
        stride = found["memory"]["stride_that_fits"]
        fits = math.ceil(self.N / stride)
        budget = MEMORY_SHARE * available
        assert memory_needed(fits, methods, "rmsd", "average") <= budget
        assert memory_needed(math.ceil(self.N / (stride - 1)), methods, "rmsd",
                             "average") > budget
        # Said with the frames, the bytes and the setting.
        assert f"{self.N:,} frames" in result.message
        assert f"stride: {stride}" in result.message
        assert "available" in result.message
        assert found["memory"]["bytes_available"] == available

        # And the stride it names does fit.
        again = Cluster(output_dir=tmp_path / "again", stride=stride).run(_chain(self.N))
        assert again.status == "ok", again.message
        labels = np.loadtxt(again.output_dir / "cluster_kmeans.dat", delimiter=",",
                            skiprows=1)
        assert labels.shape[0] == fits
        assert list(labels[:3, 0]) == [0, stride, 2 * stride]

    def test_the_refusal_leads_with_the_stride_and_is_logged(self, tmp_path, monkeypatch,
                                                              caplog):
        """The row gives a refusal's first sentence: it gave the bytes and a
        reading three brackets deep, the stride behind the chip; and the
        refusal was not in the log."""
        available = memory_needed(self.N, ["kmeans", "hierarchical"], "rmsd", "average") // 5
        with caplog.at_level(logging.ERROR, logger="fastmdx.analysis.cluster"):
            result = self._refused(tmp_path, monkeypatch, available)
        said = result.message.split(": ", 1)[1]
        first = said.split(". ")[0]
        assert first.startswith("Clustering every ") and first.endswith(
            f"fits in the memory, where clustering {self.N:,} frames does not")
        assert ": " not in first and said.endswith("Read as mocked.")
        assert any("Analysis 'cluster' failed" in r.getMessage() for r in caplog.records)

    def test_the_refusal_carries_its_numbers(self, monkeypatch):
        from fastmdxplora.refusals import StudyError, refusal_of

        monkeypatch.setattr(clustering, "memory_available", lambda: (10**6, "mocked"))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(self.N))
        refusal = refusal_of(raised.value)
        assert refusal.code == "analysis.memory.matrix_too_large"
        assert refusal.details["frames"] == self.N
        assert refusal.details["bytes_available"] == 10**6
        assert refusal.details["stride"] > 1

    def test_within_the_memory_it_clusters(self, tmp_path, monkeypatch):
        result = self._refused(tmp_path, monkeypatch, 10**12)
        assert result.status == "ok", result.message

    def test_the_other_analyses_of_the_study_still_run(self, tmp_path, monkeypatch):
        from fastmdxplora.analysis import AnalysisOrchestrator

        traj = _chain(self.N)
        traj[0].save_pdb(str(tmp_path / "top.pdb"))
        traj.save_dcd(str(tmp_path / "traj.dcd"))
        monkeypatch.setattr(clustering, "memory_available", lambda: (10**6, "mocked"))
        results = AnalysisOrchestrator(str(tmp_path / "traj.dcd"), str(tmp_path / "top.pdb"),
                                       output_dir=tmp_path / "analysis").run(
            include=["rg", "cluster"])
        assert results["cluster"].status == "error"
        assert "stride:" in results["cluster"].message
        assert results["rg"].status == "ok"

    def test_where_memory_cannot_be_read_it_says_so_and_clusters(self, tmp_path, monkeypatch):
        monkeypatch.setattr(clustering, "memory_available", lambda: (None, "not read"))
        result = Cluster(output_dir=tmp_path).run(_chain(80))
        assert result.status == "ok"
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        assert found["memory"]["bytes_available"] is None

    def test_memory_is_read_from_the_machine(self):
        available, where = clustering.memory_available()
        assert available is None or available > 0
        assert where

    def test_where_only_the_total_is_readable_it_is_said_as_the_total(self, monkeypatch):
        """macOS without psutil: no /proc/meminfo and no free pages."""
        import sys

        monkeypatch.setitem(sys.modules, "psutil", None)
        monkeypatch.setattr(clustering, "_cgroup_room", lambda: (None, ""))
        monkeypatch.setattr(clustering, "_rlimit_room", lambda: (None, ""))
        monkeypatch.setattr(clustering, "_proc_meminfo_available", lambda: None)
        monkeypatch.setattr(clustering, "_sysconf_bytes",
                            lambda name: 16 * 10**9 if name == "SC_PHYS_PAGES" else None)
        available, where = clustering.memory_available()
        assert available == 16 * 10**9
        assert "total" in where and "not readable" in where


class TestTheEstimateIsWhatTheMethodsMake:
    """The bytes the check counts against the peak the methods traced: never
    under it by more than terms in n alone, and not far over it."""

    @pytest.mark.parametrize("options", [
        {},
        {"methods": ["kmeans"]},
        {"methods": ["hierarchical"], "linkage": "ward"},
        {"methods": ["dbscan"], "eps": 5.0},
        {"features": "coordinates", "methods": ["hierarchical"]},
    ])
    def test_the_peak_is_within_the_estimate(self, options):
        n = 700
        traj = _chain(n)
        analysis = Cluster(**options)
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            analysis.compute(traj)
            peak = tracemalloc.get_traced_memory()[1] - before
        finally:
            tracemalloc.stop()
        estimate = memory_needed(n, analysis.methods, analysis.features, analysis.linkage)
        assert peak <= estimate + 2000 * n, (peak / n**2, estimate / n**2)
        assert estimate <= 1.35 * peak, (peak / n**2, estimate / n**2)

    def test_k_means_on_coordinates_makes_no_matrix(self):
        analysis = Cluster(features="coordinates", methods=["kmeans", "dbscan"],
                           linkage="ward")
        analysis.compute(_chain(300))
        assert analysis._distances is None
        assert memory_needed(10**6, ["kmeans"], "coordinates", "average") == 0



class TestTheBudgetIsTheProcesss:
    """A SLURM job's cgroup, a container's, or `ulimit -v` gives a process
    less than the machine has: under `ulimit -v` 1.3 GB the check read 5.5 GB
    available and the clustering failed allocating instead of being refused
    with a stride, and under a cgroup the kernel kills the worker."""

    def _cgroup(self, tmp_path, *, v2: bool, limit: str, usage: int,
                parent_limit: str | None = None, stat: str | None = None):
        root = tmp_path / "sys"
        if v2:
            (tmp_path / "cgroup").write_text("0::/job/step\n")
            group, names = root / "job" / "step", ("memory.max", "memory.current")
            parent = root / "job"
        else:
            (tmp_path / "cgroup").write_text("4:memory:/job/step\n1:cpu:/\n")
            group = root / "memory" / "job" / "step"
            names = ("memory.limit_in_bytes", "memory.usage_in_bytes")
            parent = root / "memory" / "job"
        group.mkdir(parents=True)
        (group / names[0]).write_text(limit + "\n")
        (group / names[1]).write_text(f"{usage}\n")
        if stat is not None:
            (group / "memory.stat").write_text(stat)
        if parent_limit is not None:
            (parent / names[0]).write_text(parent_limit + "\n")
            (parent / names[1]).write_text(f"{usage}\n")
        return clustering._cgroup_room(tmp_path / "cgroup", root)

    def test_a_v2_limit_less_its_usage(self, tmp_path):
        room, said = self._cgroup(tmp_path, v2=True, limit=str(4 * 10**9), usage=10**9)
        assert room == 3 * 10**9 and "cgroup v2" in said and "memory.max" in said

    def test_a_v1_limit_less_its_usage(self, tmp_path):
        room, said = self._cgroup(tmp_path, v2=False, limit=str(2 * 10**9), usage=5 * 10**8)
        assert room == 15 * 10**8 and "cgroup v1" in said

    def test_a_v2_group_s_reclaimable_cache_is_not_counted_as_used(self, tmp_path):
        """A job that has just read a large trajectory holds it in its
        group's page cache, which the kernel takes back before it kills."""
        room, said = self._cgroup(
            tmp_path, v2=True, limit=str(4 * 10**9), usage=3 * 10**9,
            stat=f"anon 900000000\nfile 2100000000\nactive_file 600000000\n"
                 f"inactive_file {15 * 10**8}\n")
        assert room == 25 * 10**8
        assert "inactive_file" in said and "not counted as used" in said
        # No bracket inside the bracket a refusal puts it in.
        assert "(" not in said

    def test_a_v1_group_s_reclaimable_cache_is_not_counted_as_used(self, tmp_path):
        room, said = self._cgroup(
            tmp_path, v2=False, limit=str(6 * 10**9), usage=44 * 10**8,
            stat=f"cache 3120000000\nrss 960000000\ninactive_file 5\n"
                 f"total_cache 3120000000\ntotal_inactive_file {10**9}\n")
        assert room == 26 * 10**8 and "cgroup v1" in said

    def test_without_memory_stat_the_cache_is_said_counted(self, tmp_path):
        room, said = self._cgroup(tmp_path, v2=True, limit=str(4 * 10**9), usage=10**9)
        assert room == 3 * 10**9 and "no memory.stat read" in said

    @pytest.mark.parametrize("read_from, said", [
        ("the cgroup v2 limit less its usage", "the container allows"),
        ("RLIMIT_AS less what this process holds (VmSize)", "the process may use"),
        ("MemAvailable in /proc/meminfo", "this machine has"),
    ])
    def test_the_refusal_names_the_reading_that_was_least(self, monkeypatch, read_from, said):
        from fastmdxplora.refusals import StudyError

        monkeypatch.setattr(clustering, "memory_available", lambda: (10**6, read_from))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(200))
        assert f"and {said} " in str(raised.value)
        assert read_from in str(raised.value)

    def test_a_tighter_limit_above_the_group_holds(self, tmp_path):
        room, _ = self._cgroup(tmp_path, v2=True, limit="max", usage=10**9,
                               parent_limit=str(3 * 10**9))
        assert room == 2 * 10**9

    def test_no_limit_is_no_room_to_plan_on(self, tmp_path):
        assert self._cgroup(tmp_path, v2=True, limit="max", usage=10**9)[0] is None
        (tmp_path / "v1").mkdir()
        # v1 writes an unlimited group as a number near 2^63.
        assert self._cgroup(tmp_path / "v1", v2=False, limit=str(9223372036854771712),
                            usage=10**9)[0] is None

    @pytest.mark.parametrize("name,field", [("RLIMIT_AS", "VmSize"), ("RLIMIT_DATA", "VmData")])
    def test_a_process_limit_subtracts_what_is_held(self, monkeypatch, name, field):
        resource = pytest.importorskip("resource")
        which = getattr(resource, name)
        monkeypatch.setattr(clustering, "_process_status", lambda: {field: 200 * 2**20})
        monkeypatch.setattr(resource, "getrlimit", lambda asked: (
            (500 * 2**20, 600 * 2**20) if asked == which
            else (resource.RLIM_INFINITY, resource.RLIM_INFINITY)))
        room, said = clustering._rlimit_room()
        assert room == 300 * 2**20
        assert said == f"{name} less what this process holds ({field})"

    @pytest.mark.skipif(sys.platform != "linux", reason="reads Linux /proc/self/status")
    def test_an_address_space_limit_is_read(self, tmp_path):
        import subprocess
        import sys
        from pathlib import Path

        script = (
            "import resource, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from fastmdxplora.analysis import cluster\n"
            "held = cluster._process_status().get('VmSize', 0)\n"
            "_, hard = resource.getrlimit(resource.RLIMIT_AS)\n"
            "limit = held + 300 * 2**20\n"
            "if hard != resource.RLIM_INFINITY: limit = min(limit, hard)\n"
            "resource.setrlimit(resource.RLIMIT_AS, (limit, hard))\n"
            "room, said = cluster.memory_available()\n"
            "print(room, said)\n")
        src = str(Path(clustering.__file__).resolve().parents[2])
        done = subprocess.run([sys.executable, "-c", script, src], capture_output=True,
                              text=True, timeout=300, cwd=tmp_path)
        assert done.returncode == 0, done.stderr[-2000:]
        room, said = done.stdout.split(" ", 1)
        assert int(room) <= 300 * 2**20
        assert said.startswith("RLIMIT_AS")

    def test_the_least_is_planned_on_and_each_said(self, monkeypatch):
        monkeypatch.setattr(clustering, "_machine_available", lambda: (8 * 10**9, "machine"))
        monkeypatch.setattr(clustering, "_cgroup_room", lambda: (2 * 10**9, "the group"))
        monkeypatch.setattr(clustering, "_rlimit_room", lambda: (None, ""))
        available, said = clustering.memory_available()
        assert available == 2 * 10**9
        assert said.startswith("the group") and "machine" in said

    def test_average_and_complete_linkage_count_scipy_s_copy(self):
        n = 1000
        assert memory_needed(n, ["hierarchical"], "rmsd", "average") == 25 * n * n
        assert memory_needed(n, ["hierarchical"], "rmsd", "complete") == 25 * n * n
        assert memory_needed(n, ["hierarchical"], "rmsd", "single") == 21 * n * n


_PEAK_SCRIPT = r"""
import json, sys
import numpy as np
import mdtraj as md
sys.path.insert(0, sys.argv[1])
import fastmdxplora.analysis  # noqa: F401
from fastmdxplora.analysis.cluster import Cluster

def status(name):
    for line in open("/proc/self/status"):
        if line.startswith(name + ":"):
            return int(line.split()[1]) * 1024
    raise SystemExit("no " + name)

n, options = int(sys.argv[2]), json.loads(sys.argv[3])
rng = np.random.default_rng(0)
top = md.Topology()
chain = top.add_chain()
for i in range(4):
    top.add_atom("CA", md.element.carbon, top.add_residue("ALA", chain, resSeq=i + 1))
xyz = rng.normal(0.0, 0.4, (4, 3))[None] + np.cumsum(rng.normal(0.0, 0.01, (n, 4, 3)), axis=0)
traj = md.Trajectory(xyz.astype(np.float32), top)
analysis = Cluster(**options)
analysis.compute(traj[:50])  # every library loaded before the baseline
with open("/proc/self/clear_refs", "w") as refs:  # the peak counted from here
    refs.write("5")
before = status("VmRSS")
analysis.compute(traj)
print(json.dumps({"peak": status("VmHWM") - before, "methods": analysis.methods,
                  "features": analysis.features, "linkage": analysis.linkage}))
"""


class TestThePeakIsMeasuredAsResidentMemory:
    """tracemalloc does not see what compiled code takes with malloc (SciPy's
    working copy of the condensed distances): the peak measured as resident
    memory of a process, the estimate never under it by more than terms in
    n alone."""

    @pytest.mark.parametrize("options", [
        {},
        {"methods": ["hierarchical"], "linkage": "complete"},
        {"methods": ["hierarchical"], "linkage": "single"},
        {"methods": ["kmeans"]},
        {"methods": ["dbscan"], "eps": 5.0},
    ], ids=["default", "complete", "single", "kmeans", "dbscan"])
    def test_the_estimate_is_not_under_the_resident_peak(self, options, tmp_path):
        import subprocess
        import sys
        from pathlib import Path

        if not Path("/proc/self/clear_refs").exists():
            pytest.skip("the peak resident memory is read from Linux's /proc")
        n = 2500
        src = str(Path(clustering.__file__).resolve().parents[2])
        done = subprocess.run([sys.executable, "-c", _PEAK_SCRIPT, src, str(n),
                               json.dumps(options)], capture_output=True, text=True,
                              timeout=600, cwd=tmp_path)
        assert done.returncode == 0, done.stderr[-2000:]
        found = json.loads(done.stdout.strip().splitlines()[-1])
        estimate = memory_needed(n, found["methods"], found["features"], found["linkage"])
        # Terms in n alone, and the allocator's own pages: 20 MB.
        assert found["peak"] <= estimate + 20 * 2**20, (found["peak"] / n**2, estimate / n**2)
        assert estimate <= 1.5 * found["peak"] + 20 * 2**20, (found["peak"] / n**2,
                                                             estimate / n**2)


class TestAnOlderStudysWholeMatrixIsReadInBlocks:
    """An older study kept the whole matrix in `cluster_rmsd_matrix.npz`; the
    GUI's read of it peaked at three times its size (a weights array for
    weights all one, and two products), 11.5 GB for a 31,000-frame study."""

    def test_its_map_is_the_block_means_read_a_few_rows_at_a_time(self, tmp_path):
        from fastmdxplora.gui.figure_data import MOST_CELLS, _rmsd_matrix

        n = 2400
        rng = np.random.default_rng(1)
        half = rng.uniform(0.0, 1.0, (n, n)).astype(np.float32)
        matrix = (half + half.T) / 2
        folder = tmp_path / "analysis" / "cluster"
        folder.mkdir(parents=True)
        np.savez_compressed(folder / "cluster_rmsd_matrix.npz", rmsd_nm=matrix,
                            frames=np.arange(n, dtype=np.int64),
                            time_ns=np.arange(1, n + 1) * 0.01,
                            distance=np.array("RMSD after superposing each pair"))
        block = math.ceil(n / MOST_CELLS)
        starts = np.arange(0, n, block)
        expected = np.add.reduceat(np.add.reduceat(matrix.astype(np.float64), starts, axis=0),
                                   starts, axis=1)
        sizes = np.diff(np.append(starts, n))
        expected /= np.outer(sizes, sizes)
        del half

        tracemalloc.start()
        try:
            payload = _rmsd_matrix(tmp_path)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert payload is not None and payload["block"] == block
        assert np.allclose(np.asarray(payload["values"], dtype=float), expected, atol=1e-4)
        # Not the matrix, nor a copy of it: a few rows and the map.
        assert peak < 0.5 * matrix.nbytes, peak / matrix.nbytes


class TestMDSIsCheckedAsClusteringIs:
    """Dimensionality reduction's MDS made the same n by n matrix with no
    check, and five more of its size."""

    def test_past_the_memory_it_is_refused_with_the_stride_that_fits(self, tmp_path, monkeypatch):
        from fastmdxplora.analysis.dimred import DimRed, mds_memory_needed

        n = 600
        available = mds_memory_needed(n) // 5
        monkeypatch.setattr(clustering, "memory_available", lambda: (available, "mocked"))
        result = DimRed(output_dir=tmp_path, methods=["mds"]).run(_chain(n))
        assert result.status == "error"
        assert "matrix" in result.message and "stride:" in result.message
        from fastmdxplora.refusals import StudyError, refusal_of

        with pytest.raises(StudyError) as raised:
            DimRed(methods=["mds"]).compute(_chain(n))
        refusal = refusal_of(raised.value)
        assert refusal.code == "analysis.memory.matrix_too_large"
        stride = refusal.details["stride"]
        assert mds_memory_needed(math.ceil(n / stride)) <= MEMORY_SHARE * available
        assert mds_memory_needed(math.ceil(n / (stride - 1))) > MEMORY_SHARE * available

    def test_refused_before_pca_runs_and_pca_is_kept(self, tmp_path, monkeypatch):
        """MDS refused inside the methods' loop lost the PCA computed before
        it: checked first, PCA is run and its projection saved, and the refusal
        recorded."""
        import json

        from fastmdxplora.analysis import dimred as module
        from fastmdxplora.analysis.dimred import DimRed, mds_memory_needed

        n = 600
        monkeypatch.setattr(clustering, "memory_available",
                            lambda: (mds_memory_needed(n) // 5, "mocked"))
        order = []
        real = module._refuse_mds_past_memory
        monkeypatch.setattr(module, "_refuse_mds_past_memory",
                            lambda self, frames: (order.append("checked"), real(self, frames)))
        monkeypatch.setattr(module, "PCA", lambda **kw: (order.append("pca"),
                                                        __import__("sklearn.decomposition",
                                                                   fromlist=["PCA"]).PCA(**kw))[1])
        result = DimRed(output_dir=tmp_path, methods=["pca", "mds"]).run(_chain(n))
        assert order[:2] == ["checked", "pca"]
        assert result.status == "ok", result.message
        assert (result.output_dir / "dimred_pca.dat").is_file()
        assert not (result.output_dir / "dimred_mds.dat").exists()
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        assert found["mds_refused"]["code"] == "analysis.memory.matrix_too_large"
        assert found["mds_refused"]["kept"] == ["pca"]
        assert "MDS refused" in result.message
        from fastmdxplora.report.document import _findings_notes

        said = [note for note in _findings_notes(found) if note.startswith("MDS was not run")]
        assert len(said) == 1 and said[0].endswith("PCA was run and its projection saved.")

    def test_the_methods_run_beside_it_are_named_as_the_docs_name_them(self):
        """"TSNE was run and kept": t-SNE, and its projection saved."""
        from fastmdxplora.report.document import _findings_notes

        notes = _findings_notes({"mds_refused": {"said": "Too large.",
                                                 "kept": ["pca", "tsne"]}})
        assert notes == ["MDS was not run: Too large. PCA and t-SNE were run and their "
                         "projections saved."]

    def test_the_stride_is_said_as_an_ordinal(self, monkeypatch):
        from fastmdxplora.analysis.dimred import DimRed, mds_memory_needed
        from fastmdxplora.refusals import StudyError

        n = 600
        monkeypatch.setattr(clustering, "memory_available",
                            lambda: (int(mds_memory_needed(n) * 0.5), "mocked"))
        with pytest.raises(StudyError) as raised:
            DimRed(methods=["mds"]).compute(_chain(n))
        assert "every 2nd frame" in str(raised.value), str(raised.value)

    def test_within_it_the_projection_is_classical_mds(self, monkeypatch):
        from fastmdxplora.analysis.dimred import _classical_mds, _pairwise_rmsd

        traj = _chain(120)
        distances = _pairwise_rmsd(traj, traj.topology.select("name CA"))
        embedded = _classical_mds(distances, 2)
        # Classical MDS by its textbook formula, the centring matrix written out.
        n = distances.shape[0]
        centring = np.eye(n) - np.ones((n, n)) / n
        values, vectors = np.linalg.eigh(-0.5 * centring @ distances ** 2 @ centring)
        top = vectors[:, ::-1][:, :2] * np.sqrt(np.clip(values[::-1][:2], 0, None))
        for k in range(2):
            assert np.allclose(np.abs(embedded[:, k]), np.abs(top[:, k]), atol=1e-6)
