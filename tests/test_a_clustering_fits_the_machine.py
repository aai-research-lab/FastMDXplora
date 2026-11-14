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

import matplotlib.pyplot as plt
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


def _room_for(n: int, methods=("kmeans", "hierarchical")) -> int:
    """Memory in which clustering ``n`` frames, and no more, is planned."""
    return int(memory_needed(n, list(methods), "rmsd", "average") / MEMORY_SHARE) + 1


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
    #: The map of 1,000 frames, planned at 169.2 MiB, over the least
    #: planned at any (``RESIDENT_LEAST``), which half as many are planned
    #: on: refused in nine tenths of the room it is planned in, with a stride.
    N = 1000

    def _refused(self, tmp_path, monkeypatch, available: int):
        monkeypatch.setattr(clustering, "memory_available", lambda: (available, "mocked"))
        return Cluster(output_dir=tmp_path).run(_chain(self.N))

    def test_past_the_memory_it_is_refused_with_the_stride_that_fits(self, tmp_path, monkeypatch):
        from fastmdxplora.refusals import CODES

        methods = ["kmeans", "hierarchical"]
        available = memory_needed(self.N, methods, "rmsd", "average") * 9 // 10
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
        available = memory_needed(self.N, ["kmeans", "hierarchical"], "rmsd",
                                  "average") * 9 // 10
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

        room = _room_for(self.N // 2)
        monkeypatch.setattr(clustering, "memory_available", lambda: (room, "mocked"))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(self.N))
        refusal = refusal_of(raised.value)
        assert refusal.code == "analysis.memory.matrix_too_large"
        assert refusal.details["frames"] == self.N
        assert refusal.details["bytes_available"] == room
        assert refusal.details["stride"] > 1

    def test_within_the_memory_it_clusters(self, tmp_path, monkeypatch):
        result = self._refused(tmp_path, monkeypatch, 10**12)
        assert result.status == "ok", result.message

    def test_the_other_analyses_of_the_study_still_run(self, tmp_path, monkeypatch):
        from fastmdxplora.analysis import AnalysisOrchestrator

        traj = _chain(self.N)
        traj[0].save_pdb(str(tmp_path / "top.pdb"))
        traj.save_dcd(str(tmp_path / "traj.dcd"))
        room = _room_for(self.N // 2)
        monkeypatch.setattr(clustering, "memory_available", lambda: (room, "mocked"))
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
        {"methods": ["hierarchical"], "linkage": "single"},
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
        estimate = clustering.matrix_needed(n, analysis.methods, analysis.features,
                                            analysis.linkage)
        assert peak <= estimate + 2000 * n, (peak / n**2, estimate / n**2)
        assert estimate <= 1.35 * peak, (peak / n**2, estimate / n**2)

    @pytest.mark.parametrize("n, step", [(700, None), (2000, None), (2000, 1000)],
                             ids=["700", "2000", "2000-blocks-of-1000"])
    def test_the_matrix_is_made_symmetric_with_one_block_beside_it(self, n, step,
                                                                  monkeypatch):
        """`0.5 * (a + b.T)` held two blocks on Python 3.13's NumPy, which
        does not reuse the sum for the product: up to about 2,900 frames the
        block is the whole matrix, and the distances alone peaked at 24 n^2,
        more than the 16 n^2 counted for k-means and Ward, and the 21 n^2 for
        single linkage. Past them, where the block is smaller than the matrix,
        the last pass's block was held while the next was made."""
        if step is not None:
            monkeypatch.setattr(clustering, "ROWS_BYTES", 8 * n * step)
        traj = _chain(n)
        atoms = traj.topology.select("name CA")
        step = min(n, max(1, clustering.ROWS_BYTES // (8 * n)))
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            _pairwise_rmsd(traj, atoms)
            peak = tracemalloc.get_traced_memory()[1] - before
        finally:
            tracemalloc.stop()
        # The matrix and one block of it, and terms in n alone.
        assert peak <= 8 * n * n + 8 * step * step + 2000 * n, peak / n**2

    def test_k_means_on_coordinates_makes_no_matrix(self):
        analysis = Cluster(features="coordinates", methods=["kmeans", "dbscan"],
                           linkage="ward")
        analysis.compute(_chain(300))
        assert analysis._distances is None
        assert clustering.matrix_needed(10**6, ["kmeans"], "coordinates", "average") == 0



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
        assert clustering.matrix_needed(n, ["hierarchical"], "rmsd", "average") == 25 * n * n
        assert clustering.matrix_needed(n, ["hierarchical"], "rmsd", "complete") == 25 * n * n
        assert clustering.matrix_needed(n, ["hierarchical"], "rmsd", "single") == 21 * n * n


class TestTheHierarchyIsMadeOnce:
    """The dendrogram's linkage was made again from the distances after the
    clustering, a condensed copy and SciPy's working one (8 n^2): on Python
    3.13 it raised the resident memory by 28 to 31 MiB at 2,000 frames,
    held in glibc's heap until the map. The hierarchy saved and plotted is
    the tree the labels were cut from: SciPy's linkage of what they were
    cut from, the distances or Ward's points, on either feature space, and
    cut to the clusters asked for it gives the labels."""

    @pytest.mark.parametrize("features", ["rmsd", "coordinates"])
    @pytest.mark.parametrize("linkage", ["average", "complete", "single", "ward"])
    def test_one_linkage_is_made_and_saved(self, tmp_path, monkeypatch, linkage,
                                           features):
        import pandas as pd
        from scipy.cluster import hierarchy
        from scipy.spatial.distance import squareform
        from sklearn.metrics import adjusted_rand_score

        real = hierarchy.linkage
        made, inside = [], []

        def counted(function, name):
            """Each linkage made, by its method; one made inside another
            (``ward`` calling ``linkage``) counted once."""
            def call(*args, **kwargs):
                if not inside:
                    made.append(name or kwargs.get("method", args[1] if len(args) > 1
                                                   else "single"))
                inside.append(1)
                try:
                    return function(*args, **kwargs)
                finally:
                    inside.pop()
            return call

        monkeypatch.setattr(hierarchy, "linkage", counted(real, None))
        monkeypatch.setattr(hierarchy, "ward", counted(hierarchy.ward, "ward"))
        traj = _chain(300)
        result = Cluster(methods=["hierarchical"], linkage=linkage, features=features,
                         n_clusters=5, output_dir=tmp_path).run(traj)
        assert result.status == "ok", result.message
        assert made == [linkage], made
        folder = tmp_path / "cluster"
        saved = np.load(folder / "hierarchical_linkage.npy")
        atoms = np.arange(traj.n_atoms)
        if features == "coordinates":
            points = clustering._superposed_coordinates(traj, atoms)
            distances = clustering._euclidean_matrix(points)
        else:
            points = None
            distances = _pairwise_rmsd(traj, atoms)
        if linkage == "ward":
            expected = real(clustering._ward_points(distances, points), method="ward")
        else:
            expected = real(squareform(distances, checks=False), method=linkage)
        np.testing.assert_allclose(saved, expected, rtol=1e-12, atol=0.0)
        labels = pd.read_csv(folder / "cluster_hierarchical.dat")["cluster"].to_numpy()
        cut = hierarchy.fcluster(saved, 5, criterion="maxclust")
        assert adjusted_rand_score(labels, cut) == 1.0


#: glibc's malloc settings read from the environment. A machine that sets
#: them measures another allocator than CI's and a user's: the one these
#: peaks were counted on fixed the threshold for a mapping of its own at
#: 64 MiB and the trim threshold at 256 MiB (``MALLOC_MMAP_THRESHOLD_``,
#: ``MALLOC_TRIM_THRESHOLD_``), and its subprocesses took them. Counted
#: with glibc's defaults.
_MALLOC_SETTINGS = ("MALLOC_ARENA_MAX", "MALLOC_ARENA_TEST", "MALLOC_CHECK_",
                    "MALLOC_MMAP_MAX_", "MALLOC_MMAP_THRESHOLD_", "MALLOC_PERTURB_",
                    "MALLOC_TOP_PAD_", "MALLOC_TRIM_THRESHOLD_", "GLIBC_TUNABLES")


def _glibc_defaults() -> dict:
    """This process's environment without glibc's malloc settings
    (:data:`_MALLOC_SETTINGS`), for a subprocess whose memory is counted."""
    import os

    return {key: value for key, value in os.environ.items()
            if key not in _MALLOC_SETTINGS}


_PEAK_SCRIPT = r"""
import json, sys
import numpy as np
import mdtraj as md
sys.path.insert(0, sys.argv[1])
import fastmdxplora.analysis  # noqa: F401
from fastmdxplora.analysis.cluster import Cluster, give_back_freed

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
# From a clean start: what the warm-up left unreachable collected, and
# glibc's freed pages given back.
import gc
gc.collect()
give_back_freed()
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
                              timeout=600, cwd=tmp_path, env=_glibc_defaults())
        assert done.returncode == 0, done.stderr[-2000:]
        found = json.loads(done.stdout.strip().splitlines()[-1])
        estimate = clustering.matrix_needed(n, found["methods"], found["features"],
                                            found["linkage"])
        # Terms in n alone, and the allocator's own pages: 20 MB.
        assert found["peak"] <= estimate + 20 * 2**20, (found["peak"] / n**2, estimate / n**2)
        assert estimate <= 1.5 * found["peak"] + 20 * 2**20, (found["peak"] / n**2,
                                                             estimate / n**2)


_CHAIN_SCRIPT = r"""
import json, os, sys
import numpy as np
import mdtraj as md
sys.path.insert(0, sys.argv[1])
import fastmdxplora.analysis  # noqa: F401
from fastmdxplora.analysis import cluster as clustering

def status(name):
    for line in open("/proc/self/status"):
        if line.startswith(name + ":"):
            return int(line.split()[1]) * 1024
    raise SystemExit("no " + name)

def chain(n):
    rng = np.random.default_rng(0)
    top = md.Topology()
    c = top.add_chain()
    for i in range(4):
        top.add_atom("CA", md.element.carbon, top.add_residue("ALA", c, resSeq=i + 1))
    xyz = rng.normal(0.0, 0.4, (4, 3))[None] + np.cumsum(rng.normal(0.0, 0.01, (n, 4, 3)), axis=0)
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.time = np.arange(n) * 10.0
    return traj
"""

_RUN_SCRIPT = _CHAIN_SCRIPT + r"""
n, out, room = int(sys.argv[2]), sys.argv[3], json.loads(sys.argv[4])
options = json.loads(sys.argv[5]) if len(sys.argv) > 5 else {}
traj = chain(n)
clustering.Cluster(output_dir=os.path.join(out, "warm"), **options).run(traj[:300])
stride = 1
if room is not None:
    # The room a container leaves, read as the check reads it.
    clustering.memory_available = lambda: (room, "the cgroup v2 limit, mocked")
    refused = clustering.Cluster(output_dir=os.path.join(out, "refused"), **options).run(traj)
    found = json.load(open(os.path.join(out, "refused", "cluster", "options.json")))
    stride = found["findings"]["memory"].get("stride_that_fits")
    if refused.status != "error" or not stride:
        raise SystemExit("not refused: " + refused.message)
# From a clean start: what the warm-up left unreachable (its figures, closed)
# collected, and glibc's freed pages given back. Counted from where they
# were held, the peak was what the run reused of them: within the plan on
# Python 3.11, where the collector had not yet run, and over it on 3.13.
import gc
gc.collect()
clustering.give_back_freed()
with open("/proc/self/clear_refs", "w") as refs:  # the peak counted from here
    refs.write("5")
before = status("VmRSS")
# What was held as the map began, and the peak before it, said with a peak
# over the plan: what ran before the map, or the map itself.
plot_the_map, at_the_map = clustering._plot_distance_matrix, {}

def the_map(*args, **kwargs):
    at_the_map.update(held=status("VmRSS") - before, peak=status("VmHWM") - before)
    return plot_the_map(*args, **kwargs)

clustering._plot_distance_matrix = the_map
analysis = clustering.Cluster(output_dir=os.path.join(out, "run"), stride=stride, **options)
result = analysis.run(traj)
frames = -(-n // stride)
print(json.dumps({"peak": status("VmHWM") - before, "status": result.status,
                  "message": result.message, "stride": stride, "frames": frames,
                  "held_at_the_map": at_the_map.get("held"),
                  "peak_before_the_map": at_the_map.get("peak"),
                  "needed": clustering.memory_needed(frames, analysis.methods,
                                                     analysis.features, analysis.linkage)}))
"""


def _peak_of_a_run(tmp_path, n: int, room: int | None = None,
                   options: dict | None = None) -> dict:
    import subprocess
    from pathlib import Path

    if not Path("/proc/self/clear_refs").exists():
        pytest.skip("the peak resident memory is read from Linux's /proc")
    src = str(Path(clustering.__file__).resolve().parents[2])
    done = subprocess.run([sys.executable, "-c", _RUN_SCRIPT, src, str(n), str(tmp_path),
                           json.dumps(room), json.dumps(options or {})],
                          capture_output=True, text=True, timeout=900, cwd=tmp_path,
                          env=_glibc_defaults())
    assert done.returncode == 0, done.stderr[-2000:] + done.stdout[-500:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["status"] == "ok", found["message"]
    return found


class TestTheFiguresAreCounted:
    """The check left the figures out: the map of 2,000 frames, 1,000 cells
    across, took 160 to 180 MiB to plot beside the whole matrix, so under a
    container's limit of 84 to 116 MB a clustering the check allowed, and
    one at the stride its refusal gave, were killed plotting it."""

    @pytest.mark.parametrize("options", [{}, {"methods": ["dbscan"], "eps": 10.0}],
                             ids=["default", "dbscan-wide"])
    def test_a_whole_run_s_peak_is_within_the_estimate(self, tmp_path, options):
        """Measured as the resident peak of a process running the clustering
        whole, its figures and files with it, from a start where what ran
        before is freed and given back. DBSCAN's lists of neighbours,
        freed, stayed resident in glibc's heap when the map was plotted
        after them: 160 to 165 MiB against 145 planned at 2,000 frames with
        a wide ``eps``. The figures plotted before the map, closed, were
        held until Python's garbage collector ran: from a clean start, 186.6
        MiB on Python 3.11 and 169.8 on 3.13 against 145.3 planned."""
        found = _peak_of_a_run(tmp_path, 2000, options=options)
        # Within the plan, the map planned with what was before it freed,
        # and 4 MiB: the frames, the labels and the rows read a few at a
        # time, terms in n alone left to the share not planned on
        # (`MEMORY_SHARE`). 153.2 to 161.0 MiB on Python 3.10, 148.1 to
        # 156.4 on 3.11 and 142.1 to 142.6 on 3.13 against 162.5 planned;
        # DBSCAN's 138.0 to 145.4. CI's ubuntu-latest 3.10 leg measured
        # DBSCAN at 169.1 (run 42607047496). Measured again on this
        # machine with glibc's defaults (`_glibc_defaults`), 6 runs of
        # DBSCAN and 3 of k-means and average linkage on each Python:
        # DBSCAN 138.6 to 142.5 on 3.10, 138.1 to 149.0 on 3.11 and 138.3
        # to 145.4 on 3.13; k-means and average linkage 153.7 to 158.1,
        # 148.1 to 148.4 and 149.7 to 149.8; against 169.2 planned. Every
        # peak was the map's: DBSCAN held 7.9 to 8.1 MiB as it began, and
        # peaked at 122.6 to 138.2 before it. Said with a peak over the
        # plan, which of the two was over.
        assert found["peak"] <= found["needed"] + 4 * 2**20, {
            key: round(value / 2**20, 1) for key, value in found.items()
            if key in ("peak", "needed", "held_at_the_map", "peak_before_the_map")}
        # And not so far over it that a run that fits is refused: at most
        # 1.23 times a peak counted (DBSCAN's least, 138.1 MiB on 3.11).
        assert found["needed"] <= 1.25 * found["peak"], (
            found["peak"] / 2**20, found["needed"] / 2**20)

    def test_the_stride_a_refusal_gives_fits_the_room(self, tmp_path):
        room = 160 * 10**6
        found = _peak_of_a_run(tmp_path, 2000, room)
        assert found["stride"] > 1
        assert found["peak"] <= room, (found["stride"], found["peak"] / 2**20)

    @pytest.mark.parametrize("n", [1000, 2000])
    def test_the_map_is_in_the_plan(self, n):
        """At 1,000 frames, and at 2,000 in blocks of two, the map is 1,000
        cells across, and a whole run plotting it peaked at up to 169.1 MiB
        on CI's Python 3.10 leg, DBSCAN with a wide ``eps``
        (``FIGURE_BYTES_PER_CELL``). The plan of a clustering that makes the
        least of its matrix is at least that peak, so CI's 3.10 legs keep
        the 4 MiB the whole-run test allows for terms in n alone: 162.5 MiB
        were planned, and that run was over it."""
        assert memory_needed(n, ["kmeans"], "rmsd", "average") >= 169.1 * 2**20


#: The most a clustering of a few hundred frames peaked at, resident from
#: the check in ``fastmdx analyze``'s own process, clustering alone: 500
#: frames, k-means and average linkage, Python 3.13 (``RESIDENT_LEAST``).
FEW_FRAMES_PEAK = 74.1 * 2**20


class TestAFewFramesArePlannedOnWhatAnyClusteringTakes:
    """The resident plan of a few hundred frames was the map's alone, 10 to
    48 MiB, where a run of them peaked at 30 to 74: under a container
    leaving 14 to 45 MiB at the check, 2,000 frames were refused with a
    stride of 17 to 6, and each was killed following it."""

    @pytest.mark.parametrize("methods", [["kmeans", "hierarchical"], ["kmeans"],
                                         ["dbscan"], ["hierarchical"]])
    @pytest.mark.parametrize("n", [2, 118, 223, 334, 500])
    def test_the_plan_is_not_under_the_most_a_few_frames_took(self, n, methods):
        assert memory_needed(n, methods, "rmsd", "average") >= FEW_FRAMES_PEAK

    def test_it_holds_what_a_runner_was_counted_to_take_more(self):
        """CI's Python 3.10 leg counted a whole DBSCAN run of 2,000 frames
        at 169.1 MiB, 31 over the most this machine counted of it (138.1).
        Planned at 76 MiB, 500 frames left 2.2 MiB over the 72.0 to 73.8
        counted here as the command-line test runs them."""
        assert clustering.RESIDENT_LEAST >= FEW_FRAMES_PEAK + 31 * 2**20

    @pytest.mark.parametrize("room_mib", [14, 30, 45])
    def test_under_it_the_refusal_names_no_stride(self, monkeypatch, room_mib):
        from fastmdxplora.refusals import StudyError, refusal_of

        room = room_mib * 2**20
        monkeypatch.setattr(clustering, "memory_available",
                            lambda: (room, "the cgroup v2 limit, mocked"))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(2000))
        refusal = refusal_of(raised.value)
        assert refusal.code == "analysis.memory.matrix_too_large"
        assert refusal.details.get("stride") is None, str(raised.value)
        assert str(raised.value).startswith("Clustering does not fit in the memory at any "
                                            "stride."), str(raised.value)
        assert refusal.details["bytes_needed"] == clustering.RESIDENT_LEAST


_ADDRESS_SCRIPT = _CHAIN_SCRIPT + r"""
import faulthandler, resource
n = int(sys.argv[2])
traj = chain(n)
clustering.Cluster().compute(traj[:60])  # every library and thread started
clustering.libraries_mapped()  # as the check maps them under a limit
# The address space alone planned on: the machine's and the container's
# readings, which fall while other processes run, not read.
clustering._machine_available = lambda: (None, "not read here")
clustering._cgroup_room = lambda: (None, "")
faulthandler.dump_traceback_later(120, exit=True)
analysis = clustering.Cluster()
needed = clustering.address_space_needed(n, analysis.methods, analysis.features,
                                         analysis.linkage)
# Room the check allows: what it plans on is the need, and a little more.
room = int(needed / clustering.ADDRESS_SHARE) + 6 * 2**20
# From a clean start, as the resident peak is counted.
import gc
gc.collect()
clustering.give_back_freed()
held = clustering._process_status()["VmSize"]
resource.setrlimit(resource.RLIMIT_AS, (held + room, resource.RLIM_INFINITY))
try:
    analysis.compute(traj)
    said = "ok"
except BaseException as error:  # noqa: BLE001 -- said, whatever it is
    said = type(error).__name__ + ": " + str(error)[:300]
memory = analysis.findings.get("memory", {})
print(json.dumps({"said": said, "needed": memory.get("bytes_address_needed"),
                  "address": memory.get("bytes_address_space"),
                  "threads": memory.get("threads")}))
"""


class TestAnAddressSpaceLimit:
    """Under `ulimit -v`, scikit-learn's k-means calls BLAS from its OpenMP
    threads, and an OpenBLAS thread whose buffer could not be mapped retried
    for ever: a clustering the check allowed hung, the process idle. The
    room was read as address space and planned on as resident memory."""

    @pytest.mark.skipif(sys.platform != "linux", reason="reads Linux /proc/self/status")
    def test_a_clustering_the_check_allows_finishes(self, tmp_path):
        import subprocess
        from pathlib import Path

        src = str(Path(clustering.__file__).resolve().parents[2])
        # Two threads of each, as a machine of two cores or more runs them;
        # one malloc arena, so a thread started between setting the limit
        # and the check does not take 64 MiB of the room for its own (it
        # failed on a loaded machine 4 times of 4: 552 MiB held as the limit
        # was set, 616 at the check).
        env = {key: value for key, value in _glibc_defaults().items()
               if key not in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}
        env["OMP_NUM_THREADS"] = "2"
        env["MALLOC_ARENA_MAX"] = "1"
        try:
            done = subprocess.run([sys.executable, "-c", _ADDRESS_SCRIPT, src, "300"],
                                  capture_output=True, text=True, timeout=180, cwd=tmp_path,
                                  env=env)
        except subprocess.TimeoutExpired:
            pytest.fail("the clustering hung under the address-space limit")
        assert done.returncode == 0, "hung: " + done.stderr[-2000:]
        found = json.loads(done.stdout.strip().splitlines()[-1])
        assert found["needed"] <= clustering.ADDRESS_SHARE * found["address"], found
        assert found["said"] == "ok", found
        assert found["threads"].startswith("BLAS and OpenMP held to one thread"), found

    def test_the_refusal_says_the_need_and_the_room_apart(self, monkeypatch):
        """It said 0.4 GB needed and 0.4 GB the process may use."""
        import re

        from fastmdxplora.refusals import StudyError

        n = 3000
        needed = clustering.address_space_needed(n, ["kmeans", "hierarchical"], "rmsd",
                                                 "average")
        room = needed - 1000
        monkeypatch.setattr(clustering, "memory_available", lambda: (
            room, "RLIMIT_AS less what this process holds (VmSize)"))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(n))
        said = str(raised.value)
        need = re.search(r"need about ([\d.,]+ [MG]B)", said)
        may = re.search(r"may use ([\d.,]+ [MG]B)", said)
        assert need and may, said
        assert need.group(1) != may.group(1), said
        # All of the address space is planned on, and none said as a share.
        assert "plans on" not in said, said

    def test_mds_s_refusal_says_the_need_and_the_room_apart(self, monkeypatch):
        import re

        from fastmdxplora.analysis.dimred import DimRed, address_space_needed
        from fastmdxplora.refusals import StudyError

        n = 3000
        room = address_space_needed("mds", ["mds"], n) - 1000
        monkeypatch.setattr(clustering, "memory_available", lambda: (
            room, "RLIMIT_AS less what this process holds (VmSize)"))
        with pytest.raises(StudyError) as raised:
            DimRed(methods=["mds"]).compute(_chain(n))
        said = str(raised.value)
        need = re.search(r"takes about ([\d.,]+ [MG]B)", said)
        may = re.search(r"may use ([\d.,]+ [MG]B)", said)
        assert need and may, said
        assert need.group(1) != may.group(1), said
        assert "plans on" not in said, said

    @pytest.mark.parametrize("name", ["cluster", "dimred"])
    def test_its_threads_are_held_to_one_and_said(self, monkeypatch, name):
        """Projections call BLAS as k-means does, so dimred holds them too."""
        import threadpoolctl

        from fastmdxplora.analysis.orchestrator import get_analysis_class

        monkeypatch.setattr(clustering, "_rlimit_room", lambda: (
            10**12, "RLIMIT_AS less what this process holds (VmSize)"))
        held = []
        real = threadpoolctl.threadpool_limits

        def limits(*args, **kwargs):
            held.append(kwargs.get("limits", args[0] if args else None))
            return real(*args, **kwargs)

        monkeypatch.setattr(threadpoolctl, "threadpool_limits", limits)
        analysis = get_analysis_class(name)()
        analysis.compute(_chain(60))
        said = analysis.findings.get("memory", {}).get("threads") or ""
        assert said.startswith("BLAS and OpenMP held to one thread, as RLIMIT_AS"), said
        assert 1 in held, held


_SYMMETRIC_SCRIPT = _CHAIN_SCRIPT + r"""
n, step = int(sys.argv[2]), int(sys.argv[3])
clustering.ROWS_BYTES = 8 * n * step
traj = chain(n)
atoms = np.arange(4)
clustering._pairwise_rmsd(traj[:50], atoms)
# A large array freed, as reading a trajectory does: glibc's threshold for
# mapping a block of its own rises past it, and blocks under it come from
# the heap.
large = np.ones(30 * 2**20 // 8)
del large
with open("/proc/self/clear_refs", "w") as refs:
    refs.write("5")
before = status("VmRSS")
matrix = clustering._pairwise_rmsd(traj, atoms)
print(json.dumps({"matrix": matrix.nbytes, "block": 8 * step * step,
                  "peak": status("VmHWM") - before, "after": status("VmRSS") - before}))
"""


class TestNoBlockIsLeftResident:
    """Past about 2,900 frames the matrix is made symmetric a block at a
    time, and each pass's block came from glibc's heap: freed, the last one
    stayed resident beside the matrix after it was made."""

    @pytest.mark.parametrize("step", [700, 1200], ids=["three-passes", "two-passes"])
    def test_each_pass_holds_one_block_and_none_is_left(self, tmp_path, step):
        import subprocess
        from pathlib import Path

        if not Path("/proc/self/clear_refs").exists():
            pytest.skip("the peak resident memory is read from Linux's /proc")
        src = str(Path(clustering.__file__).resolve().parents[2])
        done = subprocess.run([sys.executable, "-c", _SYMMETRIC_SCRIPT, src, "2000",
                               str(step)], capture_output=True, text=True, timeout=600,
                              cwd=tmp_path)
        assert done.returncode == 0, done.stderr[-2000:]
        found = json.loads(done.stdout.strip().splitlines()[-1])
        slack = 2 * 2**20
        # The peak of any pass: the matrix and one block.
        assert found["peak"] <= found["matrix"] + found["block"] + slack, found
        # After: the matrix alone.
        assert found["after"] <= found["matrix"] + slack, found


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
        real = module._methods_that_fit
        monkeypatch.setattr(module, "_methods_that_fit",
                            lambda self, frames: (order.append("checked"),
                                                  real(self, frames))[1])
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


class TestTheAddressSpaceIsPlannedOn:
    """Under `ulimit -v` a clustering the check allowed, and the stride its
    refusal gave, failed plotting its figures (``std::bad_alloc``) and MDS's
    matrix failed to map (``ENOMEM``): the room was address space and the
    plan resident memory, which is about half of it."""

    def test_a_figure_the_memory_runs_out_plotting_is_said_and_the_rest_saved(
            self, tmp_path, monkeypatch):
        """Matplotlib's ``std::bad_alloc`` (a ``MemoryError``) in the first
        timeline ended the analysis, and the CLI said only that."""
        real = clustering.save_figure

        def save(fig, path, *args, **kwargs):
            if str(path).endswith("cluster_kmeans.png"):
                raise MemoryError("std::bad_alloc")
            return real(fig, path, *args, **kwargs)

        monkeypatch.setattr(clustering, "save_figure", save)
        result = Cluster(output_dir=tmp_path).run(_chain(80))
        assert result.status == "ok", result.message
        folder = result.output_dir
        assert not (folder / "cluster_kmeans.png").exists()
        for saved in ("cluster_kmeans.dat", "cluster_kmeans_counts.png",
                     "cluster_hierarchical.png", "cluster_rmsd_matrix.png"):
            assert (folder / saved).is_file(), saved
        found = json.loads((folder / "options.json").read_text())["findings"]
        said = found["figures_not_plotted"]["cluster_kmeans.png"]
        assert "std::bad_alloc" in said and "stride does not help" in said, said
        assert "cluster_kmeans.png" in result.message

    def test_a_matrix_the_memory_runs_out_making_is_refused_with_a_stride(
            self, monkeypatch):
        from fastmdxplora.refusals import StudyError, refusal_of

        def mapped(dist):
            raise OSError(12, "Cannot allocate memory")

        monkeypatch.setattr(clustering, "_symmetrise", mapped)
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(80))
        refusal = refusal_of(raised.value)
        assert refusal.code == "analysis.memory.matrix_too_large"
        assert "Set `stride: 2`" in str(raised.value), str(raised.value)

    @pytest.mark.parametrize("error", [MemoryError("Unable to allocate 30.5 MiB"),
                                       OSError(12, "Cannot allocate memory")],
                             ids=["MemoryError", "ENOMEM"])
    def test_mds_running_out_is_its_refusal_and_the_rest_are_saved(
            self, tmp_path, monkeypatch, error):
        """``mmap`` in ``_symmetrise`` refused, and PCA and t-SNE were lost
        with MDS."""
        from fastmdxplora.analysis import dimred as module
        from fastmdxplora.analysis.dimred import DimRed

        def out(*args, **kwargs):
            raise error

        monkeypatch.setattr(module, "_classical_mds", out)
        result = DimRed(output_dir=tmp_path, methods=["pca", "mds", "tsne"]).run(_chain(120))
        assert result.status == "ok", result.message
        assert (result.output_dir / "dimred_pca.dat").is_file()
        assert (result.output_dir / "dimred_tsne.dat").is_file()
        assert not (result.output_dir / "dimred_mds.dat").exists()
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        assert found["mds_refused"]["kept"] == ["pca", "tsne"]
        assert "`methods: [pca]`" in found["mds_refused"]["said"], found["mds_refused"]
        assert "MDS refused" in result.message

    @pytest.mark.skipif(sys.platform != "linux", reason="reads Linux /proc/self/status")
    def test_projections_under_a_very_tight_limit_end_with_a_record(self, tmp_path):
        """Every library loaded first, then a limit 30 MiB over what is held:
        OpenBLAS gave up and ended the process, exit 1, no record. Each
        projection saved, or the analysis refused and its record written."""
        extra_mib = 30
        import subprocess
        from pathlib import Path

        script = _CHAIN_SCRIPT + r"""
import faulthandler, resource
from fastmdxplora.analysis.dimred import DimRed
out, extra = sys.argv[2], int(sys.argv[3])
traj = chain(2000)
DimRed(methods=["pca", "mds", "tsne"]).compute(traj[:200])
held = clustering._process_status()["VmSize"]
resource.setrlimit(resource.RLIMIT_AS, (held + extra, resource.RLIM_INFINITY))
faulthandler.dump_traceback_later(300, exit=True)
result = DimRed(output_dir=out, methods=["pca", "mds", "tsne"]).run(traj)
record = json.load(open(os.path.join(out, "dimred", "options.json")))
print(json.dumps({"status": result.status, "message": result.message,
                  "written": sorted(os.listdir(os.path.join(out, "dimred"))),
                  "findings": record.get("findings", {})}))
"""
        src = str(Path(clustering.__file__).resolve().parents[2])
        done = subprocess.run([sys.executable, "-c", script, src, str(tmp_path),
                               str(extra_mib * 2**20)],
                              capture_output=True, text=True, timeout=400, cwd=tmp_path)
        assert done.returncode == 0, done.stdout[-500:] + done.stderr[-2000:]
        found = json.loads(done.stdout.strip().splitlines()[-1])
        if found["status"] == "ok":
            assert {"dimred_pca.dat", "dimred_tsne.dat"} <= set(found["written"]), found
            assert "mds_refused" in found["findings"] or "dimred_mds.dat" in found["written"]
        else:
            refused = found["findings"].get("refused") or {}
            assert refused.get("code") == "analysis.memory.matrix_too_large", found
            assert "ulimit -v" in refused["said"], refused
            assert refused.get("before_it_ran") is True, refused

    def test_a_refusal_s_sizes_are_in_one_unit_and_its_share_is_three_quarters(
            self, monkeypatch):
        """2,000 frames in a container leaving 116 MB: "0.2 GB ... the
        container allows 0.1 GB, of which clustering plans on 75%, 87.0
        MB", 152 MB said as 0.2 GB and the room said twice by two
        derivations. 160 MB now, over the room the least plan
        (``RESIDENT_LEAST``) takes, so a stride is named."""
        import re

        from fastmdxplora.refusals import StudyError

        monkeypatch.setattr(clustering, "memory_available", lambda: (
            160 * 10**6, "the cgroup v2 limit, mocked"))
        monkeypatch.setattr(clustering, "_rlimit_room", lambda: (None, ""))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(2000))
        said = str(raised.value)
        need = re.search(r"need about ([\d.,]+) ([MG]B)", said)
        room = re.search(r"container allows ([\d.,]+) ([MG]B)", said)
        planned = re.search(r"plans on 75%, ([\d.,]+) ([MG]B)", said)
        assert need and room and planned, said
        assert need.group(2) == room.group(2) == planned.group(2) == "MB", said
        places = len(room.group(1).partition(".")[2])
        shown = float(room.group(1).replace(",", ""))
        assert abs(float(planned.group(1).replace(",", "")) - 0.75 * shown) <= (
            0.5 * 10.0**-places), said

    def test_the_any_stride_refusal_s_first_sentence_stands_alone(self, monkeypatch):
        """"Clustering does not fit in the memory at any stride:" was all a
        results table's cell showed of it."""
        from fastmdxplora.refusals import StudyError

        monkeypatch.setattr(clustering, "memory_available", lambda: (
            10 * 10**6, "the cgroup v2 limit, mocked"))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(500))
        first = str(raised.value).split(". ")[0] + "."
        assert first == "Clustering does not fit in the memory at any stride.", str(raised.value)

    def test_the_projections_read_the_room_once_and_say_when(self, monkeypatch):
        """At ``ulimit -v 600000`` clustering read 132 MB and MDS after it
        0.4 MB in one log, each a reading at another time, neither said
        as such."""
        from fastmdxplora.analysis.dimred import DimRed
        from fastmdxplora.refusals import StudyError

        n = 2000
        room = 60 * 2**20
        readings = iter([room, 10**6, 10**6])
        read = []

        def available():
            read.append(1)
            return next(readings), "RLIMIT_AS less what this process holds (VmSize)"

        monkeypatch.setattr(clustering, "memory_available", available)
        with pytest.raises(StudyError) as raised:
            DimRed(methods=["mds"]).compute(_chain(n))
        said = str(raised.value)
        assert len(read) == 1, read
        assert "may use 62.9 MB as this analysis starts" in said, said


#: The command line in its own process, ``analyze`` on a study; at the
#: memory check of the analysis after ``skip`` others, the libraries mapped
#: as the check maps them and ``VmSize`` read: printed where ``room`` is 0,
#: else ``RLIMIT_AS`` set to it and ``room`` more, the limit the check then
#: reads. A limit of 8 GiB more from the start, so BLAS is held to one
#: thread as under ``ulimit -v``.
_CLI_SCRIPT = r"""
import os, resource, sys
sys.path.insert(0, sys.argv[1])
from fastmdxplora.analysis import cluster
study, room, skip, options = sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
analyses = sys.argv[6:]
_, hard = resource.getrlimit(resource.RLIMIT_AS)
if room:
    resource.setrlimit(resource.RLIMIT_AS,
                       (cluster._process_status()["VmSize"] + 8 * 2**30, hard))
real = cluster.memory_available
calls = []

def at_the_check():
    calls.append(1)
    if len(calls) == skip + 1:
        getattr(cluster, "libraries_mapped", lambda: None)()
        held = cluster._process_status()["VmSize"]
        if not room:
            print("VMCHECK", held, flush=True)
            os._exit(0)
        resource.setrlimit(resource.RLIMIT_AS, (held + room, hard))
    return real()

cluster.memory_available = at_the_check
from fastmdxplora.cli.main import main
code = main(["analyze", "--output", study, "--rerun", "--no-explain", "--options", options,
             "--analyses", *analyses])
print("EXIT", code, flush=True)
"""


def _cli_env() -> dict:
    """Two threads, as on a machine of two cores, one malloc arena, and
    glibc's malloc otherwise as it starts (:func:`_glibc_defaults`): what a
    process holds then is the same from one machine to another."""
    env = {key: value for key, value in _glibc_defaults().items()
           if key not in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}
    env.update(OMP_NUM_THREADS="2", MALLOC_ARENA_MAX="1")
    return env


def _cli(study, room: int, analyses, options: dict | None = None, skip: int = 0):
    """``fastmdx analyze`` in its own process, the room set at the check."""
    import subprocess
    from pathlib import Path

    src = str(Path(clustering.__file__).resolve().parents[2])
    return subprocess.run(
        [sys.executable, "-c", _CLI_SCRIPT, src, str(study), str(room), str(skip),
         json.dumps(options or {}), *analyses],
        capture_output=True, text=True, timeout=900, cwd=study, env=_cli_env())


#: ``fastmdx analyze`` in its own process, clustering's resident peak
#: counted from its memory check to its end.
_PEAK_FROM_THE_CHECK_SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from fastmdxplora.analysis import cluster
study, options = sys.argv[2], sys.argv[3]

def status(name):
    for line in open("/proc/self/status"):
        if line.startswith(name + ":"):
            return int(line.split()[1]) * 1024
    raise SystemExit("no " + name)

real, at = cluster.memory_available, {}

def at_the_check():
    if not at:
        with open("/proc/self/clear_refs", "w") as refs:
            refs.write("5")
        at["rss"] = status("VmRSS")
    return real()

cluster.memory_available = at_the_check
run = cluster.Cluster.run

def counted(self, traj):
    result = run(self, traj)
    print("PEAK", json.dumps({"peak": status("VmHWM") - at["rss"], "status": result.status,
                              "message": result.message,
                              "needed": self.findings["memory"]["bytes_needed"],
                              "frames": self.findings["memory"]["frames"]}), flush=True)
    return result

cluster.Cluster.run = counted
from fastmdxplora.cli.main import main
main(["analyze", "--output", study, "--rerun", "--no-explain", "--options", options,
      "--analyses", "cluster"])
"""


def _record(study, name: str) -> tuple[dict, dict]:
    """An analysis's row in the manifest, and its findings."""
    from pathlib import Path

    folder = Path(study) / "analysis"
    row = json.loads((folder / "analysis_manifest.json").read_text())["results"][name]
    try:
        findings = json.loads((folder / name / "options.json").read_text())["findings"]
    except (OSError, KeyError):
        findings = {}
    return row, findings


@pytest.fixture(scope="module")
def studies(tmp_path_factory):
    """Studies of 500, 1,001 and 2,000 frames, analysed once (their RMSD)."""
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_a_phase_command_is_explore_with_one_phase import _analysed

    root = tmp_path_factory.mktemp("cli")
    return {n: _analysed(root / str(n), analyses=("rmsd",), n_frames=n, report=False)
            for n in (500, 1001, 2000)}


#: ``allocator_said(path)``: what the process's allocators have mapped,
#: written to ``path`` and read only by the test (:func:`_allocators_said`):
#: Python's small-object allocator (``sys._debugmallocstats``, through the
#: process's standard error), then ``/proc/self/maps`` (glibc's ``[heap]``)
#: and ``/proc/self/status`` (its threads), copied through a buffer mapped
#: before, so the copy grows no heap. Said just after the ``VmSize`` it goes
#: with is read: an arena or a heap step the reading of
#: ``/proc/self/status`` itself took is in both.
_ALLOCATOR_SAID = r"""
said_through = bytearray(1 << 20)

def allocator_said(path):
    said = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(sys, "_debugmallocstats"):
            os.write(said, b"== allocator\n")
            sys.stderr.flush()
            saved = os.dup(2)
            os.dup2(said, 2)
            try:
                sys._debugmallocstats()
            finally:
                os.dup2(saved, 2)
                os.close(saved)
        for name, source in ((b"== maps\n", "/proc/self/maps"),
                             (b"== status\n", "/proc/self/status")):
            os.write(said, name)
            read = os.open(source, os.O_RDONLY)
            try:
                while True:
                    count = os.readv(read, [said_through])
                    if count <= 0:
                        break
                    os.write(said, memoryview(said_through)[:count])
            finally:
                os.close(read)
    finally:
        os.close(said)
"""

#: ``fastmdx analyze`` in its own process, as `python -m fastmdxplora` runs
#: it, to clustering's memory check: there it prints what the process holds
#: (the libraries mapped, as the check maps them) past what it held as it
#: started, and stops. With :data:`_UNDER_THE_LIMIT_SCRIPT`, what a run of
#: the command line holds at the check is read from a process of its own
#: kind, under coverage or not. Just after each ``VmSize`` is read, Python's
#: allocator says what it has mapped (:data:`_ALLOCATOR_SAID`), into the
#: files ``argv[2]`` + ``.start`` and + ``.check``.
_TO_THE_CHECK_SCRIPT = r"""
import os, runpy, sys
src = sys.argv[1]
sys.path.insert(0, src)

def held():
    for line in open("/proc/self/status"):
        if line.startswith("VmSize:"):
            return int(line.split()[1]) * 1024
    raise SystemExit("no VmSize")

start = held()
""" + _ALLOCATOR_SAID + r"""
said_to = sys.argv[2]
allocator_said(said_to + ".start")
from fastmdxplora.analysis import cluster

def at_the_check():
    cluster.libraries_mapped()
    at_it = held()
    allocator_said(said_to + ".check")
    print("VMCHECK", at_it - start, flush=True)
    os._exit(0)

cluster.memory_available = at_the_check
sys.argv = ["fastmdxplora", *sys.argv[3:]]
runpy.run_module("fastmdxplora", run_name="__main__", alter_sys=True)
"""

#: ``fastmdx analyze`` in its own process, as :data:`_TO_THE_CHECK_SCRIPT`
#: runs it, under ``RLIMIT_AS`` set from what this interpreter holds at the
#: same point of its start: that, what the reading took from there to the
#: check (``argv[3]``), and the room meant (``argv[4]``). Nothing of the
#: command line is changed: the libraries are mapped as they are, and once
#: they are, just after the check reads ``VmSize``, Python's allocator says
#: what it has mapped into the file ``argv[2]`` + ``.check``, and at its
#: start into + ``.start``, as the reading's does. It prints what it held at
#: its start, the limit and its threads, and its threads and ``VmSize`` as
#: it exits.
_UNDER_THE_LIMIT_SCRIPT = r"""
import os, runpy, sys
src = sys.argv[1]
sys.path.insert(0, src)

def status(name):
    for line in open("/proc/self/status"):
        if line.startswith(name + ":"):
            return int(line.split()[1])
    raise SystemExit("no " + name)

start = status("VmSize") * 1024
""" + _ALLOCATOR_SAID + r"""
said_to = sys.argv[2]
allocator_said(said_to + ".start")
import atexit, resource
limit = start + int(sys.argv[3]) + int(sys.argv[4])
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
print("STARTED", start, limit, status("Threads"), flush=True)
atexit.register(lambda: print("ENDED", status("Threads"), status("VmSize") * 1024, flush=True))
from fastmdxplora.analysis import cluster
mapped, status_read, said = cluster.libraries_mapped, cluster._process_status, []

def libraries_mapped(*args, **kwargs):
    mapped(*args, **kwargs)
    if not said:
        said.append("the check reads next")

def process_status():
    held = status_read()
    if said == ["the check reads next"]:
        said.append("said")
        allocator_said(said_to + ".check")
    return held

cluster.libraries_mapped, cluster._process_status = libraries_mapped, process_status
sys.argv = ["fastmdxplora", *sys.argv[5:]]
runpy.run_module("fastmdxplora", run_name="__main__", alter_sys=True)
"""


#: The room ``ulimit -v 600000`` left tri-alanine's clustering in ``fastmdx
#: analyze`` after its RMSD, measured (600,000 KiB less a ``VmSize`` of
#: 548,937,728 bytes at the check, the libraries mapped).
ROOM_AT_600000 = 600000 * 1024 - 548937728

#: How far the room a run of the 600000 test reads at its check may be from
#: :data:`ROOM_AT_600000`, past what Python's allocator and glibc's heap
#: took from its start to the check more or less than in the reading
#: (:func:`_allocators_said`), for the run to be the one the test means, in
#: bytes. Past both, the run took from -0.01 to +0.02 MiB more than its
#: reading in each of 520 runs (Python 3.10, 3.11 and 3.13, 340 of them
#: under CPU load, 120 with the environment's size stepped), where it had
#: taken -1.07 to +1.11 MiB more; the plan for its 500 frames (61.0 MiB) is
#: 1.4 MiB under the room.
ROOM_READ_WITHIN = 2**20 // 4

#: How far glibc's main heap (``[heap]``, one malloc arena with
#: ``MALLOC_ARENA_MAX=1``) may have moved, from the start to the check, more
#: or less than in the reading, in bytes: in those 520 runs it moved -0.20 to
#: +0.19 MiB.
HEAP_MOVED_WITHIN = 3 * 2**20 // 8


def _allocators_said(path) -> dict:
    """What a process's allocators said they had mapped
    (:data:`_ALLOCATOR_SAID`): Python's small-object allocator's arenas
    (``arenas``, None where it has none) and their bytes (``allocator``),
    glibc's main heap (``heap``), and the threads (``threads``). ``unread``
    says what could not be read. The map Python's allocator finds its
    arenas by (128 KiB a node from Python 3.11) is not counted with them:
    by the check it is taken from glibc's heap, and counted there.

    Python's allocator maps an arena whole, 1 MiB from Python 3.10, and
    gives it back once it is empty, so what a process holds moves by an
    arena where the last one fills or empties: as it starts (2 arenas or 3,
    by the hash seed and the environment's size) and at the check. On CI's
    ubuntu 3.11 leg the run read 1.02 MiB less room than the reading said,
    with the clustering whole. Here, of 140 runs with the environment's size
    stepped, 22 moved by more than 1 MiB from their reading, each by an arena
    at the start or at the check, and none of 40 with ``PYTHONMALLOC=malloc``
    by more than 0.11 MiB. glibc's heap moves the same way, by a few pages
    to its top pad and trim threshold (128 KiB), by the environment's size:
    in 520 runs it moved -0.20 to +0.19 MiB more than in the reading, and
    past it and the arenas the run took -0.01 to +0.02 MiB more."""
    import re
    from pathlib import Path

    try:
        text = Path(path).read_text()
    except OSError as error:
        return {"unread": f"nothing written ({error.strerror})"}
    parts = dict(re.findall(r"^== (\w+)\n(.*?)(?=^== |\Z)", text, re.S | re.M))
    said: dict = {"unread": None, "arenas": None, "allocator": 0, "heap": 0}
    allocator = parts.get("allocator", "")
    if allocator.strip():
        arenas = re.search(r"# arenas allocated current\s*=\s*([\d,]+)", allocator)
        size = re.search(r"([\d,]+) bytes/arena", allocator)
        if arenas is None or size is None:
            said["unread"] = "Python's allocator said what this test cannot read"
        else:
            said["arenas"] = int(arenas.group(1).replace(",", ""))
            said["allocator"] = said["arenas"] * int(size.group(1).replace(",", ""))
    said["maps"] = {}
    for line in parts.get("maps", "").splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 5 or "-" not in fields[0]:
            continue
        low, high = (int(end, 16) for end in fields[0].split("-"))
        name = (fields[5].strip() if len(fields) > 5 else "anonymous") + " " + fields[1]
        said["maps"][name] = said["maps"].get(name, 0) + high - low
        if name.startswith("[heap] "):
            said["heap"] += high - low
    threads = re.search(r"^Threads:\s*(\d+)", parts.get("status", ""), re.M)
    if "maps" not in parts or threads is None:
        said["unread"] = said["unread"] or "no maps or status"
    said["threads"] = int(threads.group(1)) if threads else None
    return said


def _maps_moved(reading: dict, run: dict) -> str:
    """The mappings, by file (or ``anonymous``) and protection, a run held
    more or less than its reading at the same point, past glibc's heap,
    16 KiB or more (from :func:`_allocators_said`): ``anonymous ---p`` is
    mostly threads' guard pages, 4 KiB to a thread."""
    moved = []
    for name in sorted(set(reading["maps"]) | set(run["maps"])):
        step = run["maps"].get(name, 0) - reading["maps"].get(name, 0)
        if not name.startswith("[heap] ") and (
                abs(step) >= 2**14 or (step and name == "anonymous ---p")):
            moved.append(f"{name} {step / 2**10:+,.0f} KiB")
    return ", ".join(moved) or "none"


@pytest.mark.skipif(sys.platform != "linux", reason="reads Linux /proc/self/status")
class TestTheCommandLinesOwnProcess:
    """The address space a run maps was planned with an allowance measured
    in a new process (150 MiB for clustering, 130 for the projections),
    where the libraries are mapped during the run; in ``fastmdx analyze``
    they are mapped before it, so they were counted twice, and tri-alanine's
    500 frames were refused "at any stride" from ``ulimit -v`` 530000 to
    700000, where every figure was plotted. Measured from the check in the
    command line's own process now, with the libraries mapped before the
    room is read."""

    def test_500_frames_run_whole_under_ulimit_v_600000(self, studies, tmp_path):
        """Under ``ulimit -v`` itself, set in the run's own interpreter as it
        starts (:data:`_UNDER_THE_LIMIT_SCRIPT`): to what it holds there,
        what such a run takes from there to the check
        (:data:`_TO_THE_CHECK_SCRIPT`), and the room ``ulimit -v`` 600000
        left tri-alanine. The holding was read whole, in a process of another
        kind: on CI's coverage leg the run held more than that, and was
        refused its own 500 frames (56.0 MB of room for 64.0 MB).

        What a run holds at the check also moved with the bytecode it found:
        with the source's caches written by the reading and loaded by the
        run, or some of them stale, the run held 4 to 8 MiB more than the
        reading said (Python 3.11: 58.6 and 55.0 MiB of room for 61.0). Every
        process here compiles into a cache of its own, written by a first
        reading, so the reading and the run load the same bytecode.

        The limit was set from what another interpreter held at its start,
        and on CI's ubuntu 3.13 leg the run read 56.9 MB of room for 64.0 MB
        on 2 runs of 5, 8.1 to 8.2 MiB short. Set from the run's own start, a
        difference at the start is not in the room; one between the start
        and the check, the command line's own, still is: the room the run
        read is asserted, and a run that took more than the reading between
        the two fails, saying both.

        Past what Python's allocator and glibc's heap took
        (:func:`_allocators_said`): an arena more or one fewer than in the
        reading, at the start or at the check, is the allocator's own, and
        on CI's ubuntu 3.11 leg the run read 61.41 MiB of room for 62.43,
        1.02 MiB more taken than the reading's, with the clustering whole;
        the heap moves by a few pages to its pad of 128 KiB the same way.
        More than two arenas either way fails, so does the heap moved past
        :data:`HEAP_MOVED_WITHIN`, and so does what the run took past both
        by more than :data:`ROOM_READ_WITHIN`, naming the threads and the
        mappings that moved. Up to about 2 MiB of small Python objects, or
        :data:`HEAP_MOVED_WITHIN` of the heap, that the command line held
        only under a limit, before the check, would pass as theirs."""
        import subprocess
        from pathlib import Path

        study = studies[500]
        src = str(Path(clustering.__file__).resolve().parents[2])
        analyze = ["analyze", "--output", str(study), "--rerun", "--no-explain",
                   "--analyses", "rmsd", "cluster"]
        env = {key: value for key, value in _cli_env().items()
               if key != "PYTHONDONTWRITEBYTECODE"}
        env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "bytecode")

        def ran(script: str, *args: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-c", script, *args], capture_output=True,
                                  text=True, timeout=900, cwd=study, env=env)

        def said(done: subprocess.CompletedProcess, word: str) -> list[int]:
            line = next((line for line in done.stdout.splitlines()
                         if line.startswith(word + " ")), None)
            assert line is not None, (f"no {word} line, exit {done.returncode}\n"
                                      + done.stdout[-3000:] + done.stderr[-2000:])
            return [int(part) for part in line.split()[1:]]

        # The cache written, then read as the run reads it.
        reading, running = tmp_path / "reading_allocator", tmp_path / "run_allocator"
        said(ran(_TO_THE_CHECK_SCRIPT, src, str(reading), *analyze), "VMCHECK")
        growth = said(ran(_TO_THE_CHECK_SCRIPT, src, str(reading), *analyze), "VMCHECK")[0]

        # A record of an earlier run is not read as this run's.
        (study / "analysis" / "cluster" / "options.json").unlink(missing_ok=True)
        done = ran(_UNDER_THE_LIMIT_SCRIPT, src, str(running), str(growth),
                   str(ROOM_AT_600000), *analyze)
        output = f"exit {done.returncode}\n" + done.stdout[-3000:] + done.stderr[-2000:]
        start, limit, threads = said(done, "STARTED")
        try:
            _, findings = _record(study, "cluster")
        except (OSError, KeyError, ValueError) as error:
            pytest.fail(f"no record of the run's clustering ({error!r}); {output}")
        room = findings.get("memory", {}).get("bytes_address_space")
        assert room is not None, f"no room in the run's record; {output}"
        took = limit - room - start
        ended = next((line.split()[1] for line in done.stdout.splitlines()
                      if line.startswith("ENDED ")), "not read")
        # Python's allocator maps an arena whole, and glibc's heap moves by
        # its pad: what they took from the start to the check more or less
        # than in the reading is theirs, not the command line's.
        dumps = {(who, when): _allocators_said(f"{path}.{when}")
                 for who, path in (("reading", reading), ("run", running))
                 for when in ("start", "check")}
        figures = (
            f"the run read {room / 2**20:.2f} MiB of room at the check, meant "
            f"{ROOM_AT_600000 / 2**20:.2f}: from its start to the check it took "
            f"{took / 2**20:.2f} MiB, the reading {growth / 2**20:.2f} "
            f"({(took - growth) / 2**20:+.2f}")
        unread = [f"{who}'s {when}: {dump['unread']}"
                  for (who, when), dump in dumps.items() if dump["unread"]]
        assert not unread, (f"{figures}); the allocators could not be read at the "
                            f"{'; '.join(unread)}; {output}")

        def moved(key: str) -> int:
            return (dumps["run", "check"][key] - dumps["run", "start"][key]
                    - dumps["reading", "check"][key] + dumps["reading", "start"][key])

        python = dumps["run", "start"]["arenas"] is not None
        arenas = moved("arenas") if python else 0
        allocator, heap = moved("allocator"), moved("heap")
        figures += (
            f"; Python's allocator {allocator / 2**20:+.2f} MiB of it"
            + (f", its arenas {dumps['run', 'start']['arenas']} to "
               f"{dumps['run', 'check']['arenas']}, the reading's "
               f"{dumps['reading', 'start']['arenas']} to "
               f"{dumps['reading', 'check']['arenas']}" if python else ", none here")
            + f"; glibc's heap {heap / 2**20:+.2f} MiB of it); threads at its start "
            f"{threads}, at the check {dumps['run', 'check']['threads']}, the "
            f"reading's {dumps['reading', 'start']['threads']} and "
            f"{dumps['reading', 'check']['threads']}, at its end {ended}; mappings "
            f"the run held past the reading's at the start: "
            f"{_maps_moved(dumps['reading', 'start'], dumps['run', 'start'])}; at the "
            f"check: {_maps_moved(dumps['reading', 'check'], dumps['run', 'check'])}")
        assert abs(arenas) <= 2, (
            f"Python's allocator took {arenas:+d} arenas more than in the reading, "
            f"at most 2: {figures}; {output}")
        assert abs(heap) <= HEAP_MOVED_WITHIN, (
            f"glibc's heap moved {heap / 2**20:+.2f} MiB more than in the reading, at "
            f"most {HEAP_MOVED_WITHIN / 2**20:.2f}: {figures}; {output}")
        assert abs(room + allocator + heap - ROOM_AT_600000) <= ROOM_READ_WITHIN, (
            f"{figures}; {output}")
        assert done.returncode == 0, output
        for name in ("rmsd", "cluster"):
            row, findings = _record(study, name)
            assert row["status"] == "ok", (name, row["message"])
            assert not findings.get("figures_not_plotted"), (name, findings)
        _, findings = _record(study, "cluster")
        assert "stride" not in findings["frames"], findings["frames"]
        assert findings["memory"]["bytes_address_space"] is not None, findings["memory"]

    def test_a_clustering_the_room_holds_runs_whole(self, studies):
        """2,000 frames with 200 MiB of room at the check: refused with
        `stride: 4` on the allowance measured in a new process, where every
        figure of all 2,000 is plotted."""
        study = studies[2000]
        done = _cli(study, 200 * 2**20, ["cluster"])
        assert "EXIT 0" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]
        row, findings = _record(study, "cluster")
        assert row["status"] == "ok", row["message"]
        assert not findings.get("figures_not_plotted"), findings
        assert "cluster_rmsd_matrix.png" in row["message"] or (
            study / "analysis" / "cluster" / "cluster_rmsd_matrix.png").is_file()

    def test_a_run_after_the_rmsd_is_not_refused_far_past_its_need(self, studies):
        """The address plan was the resident plan and a constant, 173.7 MiB
        at 2,000 frames, where a clustering after the RMSD ends whole from
        118 MiB of room on Python 3.11 and 120 on 3.13 (121 at most,
        counted): at 165 MiB it was refused with `stride: 3`. With 1.35
        times the most counted, ``fastmdx analyze`` clusters every frame
        and plots every figure."""
        study = studies[2000]
        done = _cli(study, int(1.35 * 121 * 2**20), ["rmsd", "cluster"])
        assert "EXIT 0" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]
        row, findings = _record(study, "cluster")
        assert row["status"] == "ok", row["message"]
        assert "stride" not in findings["frames"], findings["frames"]
        assert not findings.get("figures_not_plotted"), findings

    def test_1_001_frames_run_whole_in_the_room_planned(self, studies):
        """Past 1,000 frames the map is of blocks of two, 501 cells across,
        and the plan, of the map's cells alone, fell to 61.1 MiB, where the
        dendrogram of 1,001 frames after the RMSD ran out on Python 3.11
        (63 to 64.4 MiB counted): planned on its frames as well, every
        figure is plotted in the room the check plans."""
        study = studies[1001]
        need = clustering.address_space_needed(1001, ["kmeans", "hierarchical"], "rmsd",
                                               "average")
        done = _cli(study, need, ["rmsd", "cluster"])
        assert "EXIT 0" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]
        row, findings = _record(study, "cluster")
        assert row["status"] == "ok", row["message"]
        assert not findings.get("figures_not_plotted"), findings

    def test_one_refused_with_a_stride_runs_at_it_to_its_end(self, studies):
        """2,000 frames with 120 MiB of room at the check: refused with the
        stride that fits, and that stride, followed under the same room,
        runs to its end with every figure plotted. It was refused "at any
        stride" on the allowance measured in a new process."""
        study = studies[2000]
        done = _cli(study, 120 * 2**20, ["cluster"])
        assert "EXIT" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]
        row, findings = _record(study, "cluster")
        assert row["status"] == "error", row["message"]
        stride = findings["memory"].get("stride_that_fits")
        assert stride and f"Set `stride: {stride}`" in row["message"], row["message"]
        done = _cli(study, 120 * 2**20, ["cluster"],
                    {"cluster": {"stride": stride}})
        assert "EXIT 0" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]
        row, findings = _record(study, "cluster")
        assert row["status"] == "ok", row["message"]
        assert not findings.get("figures_not_plotted"), findings

    @pytest.mark.parametrize("stride", [17, 6, 4])
    def test_a_few_frames_peak_within_their_plan(self, studies, stride):
        """Every 17th, 6th and 4th of 2,000 frames, 118, 334 and 500,
        clustered alone: their resident peak from the check, 50.6 to 54.2,
        54.1 to 64.6 and 72.0 to 73.8 MiB counted here, against plans of
        10.2, 26.0 and 48.3 MiB without ``RESIDENT_LEAST``. A container sized
        by those plans killed them. At 500 frames this says the margin a
        runner leaves under the least planned."""
        import subprocess
        from pathlib import Path

        if not Path("/proc/self/clear_refs").exists():
            pytest.skip("the peak resident memory is read from Linux's /proc")
        study = studies[2000]
        src = str(Path(clustering.__file__).resolve().parents[2])
        done = subprocess.run(
            [sys.executable, "-c", _PEAK_FROM_THE_CHECK_SCRIPT, src, str(study),
             json.dumps({"cluster": {"stride": stride}})],
            capture_output=True, text=True, timeout=900, cwd=study, env=_cli_env())
        line = [row for row in done.stdout.splitlines() if row.startswith("PEAK ")]
        assert line, done.stdout[-3000:] + done.stderr[-2000:]
        found = json.loads(line[-1][5:])
        assert found["status"] == "ok", found["message"]
        assert found["frames"] == -(-2000 // stride)
        assert found["peak"] <= found["needed"], (found["peak"] / 2**20,
                                                  found["needed"] / 2**20)

    def test_an_mds_the_check_allows_runs_to_its_end(self, studies):
        """2,000 frames, PCA, MDS and t-SNE with 90 MiB of room at the
        check: MDS's matrix (61 MiB) and the figures fit, and it was refused
        with the whole analysis on the projections' allowance of 130 MiB."""
        study = studies[2000]
        done = _cli(study, 90 * 2**20, ["dimred"],
                    {"dimred": {"methods": ["pca", "mds", "tsne"]}})
        assert "EXIT 0" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]
        row, findings = _record(study, "dimred")
        assert row["status"] == "ok", row["message"]
        assert "methods_refused" not in findings and "mds_refused" not in findings, findings
        assert not findings.get("figures_not_plotted"), findings
        for method in ("pca", "mds", "tsne"):
            assert (study / "analysis" / "dimred" / f"dimred_{method}.dat").is_file(), method


#: ``fastmdx analyze`` in its own process with nothing of clustering's or
#: the projections' mapped yet: at the first memory check, before anything
#: is warmed up, ``RLIMIT_AS`` set to what the process holds and ``room``
#: more.
_COLD_SCRIPT = r"""
import resource, sys
sys.path.insert(0, sys.argv[1])
from fastmdxplora.analysis import cluster
study, room = sys.argv[2], int(sys.argv[3])
real = cluster.memory_available
calls = []

def at_the_check():
    if not calls:
        calls.append(1)
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS,
                           (cluster._process_status()["VmSize"] + room, hard))
    return real()

cluster.memory_available = at_the_check
from fastmdxplora.cli.main import main
code = main(["analyze", "--output", study, "--rerun", "--no-explain",
             "--analyses", *sys.argv[4:]])
print("EXIT", code, flush=True)
"""


@pytest.mark.skipif(sys.platform != "linux", reason="reads Linux /proc/self/status")
class TestALimitTooTightForTheLibrariesIsRefused:
    """The libraries were warmed up (SciPy's eigensolver, a figure) before
    the room was read, and under ``ulimit -v`` 485000 to 520000 OpenBLAS
    could not map its 32 MiB buffer and retried for ever: ``fastmdx
    analyze`` hung at the check with no record, where the run before
    refused with one."""

    def test_a_cold_run_a_few_mib_over_what_it_holds_ends_refused(self, studies):
        import re
        import subprocess
        from pathlib import Path

        study = studies[500]
        src = str(Path(clustering.__file__).resolve().parents[2])
        try:
            done = subprocess.run(
                [sys.executable, "-c", _COLD_SCRIPT, src, str(study), str(8 * 2**20),
                 "cluster", "dimred"],
                capture_output=True, text=True, timeout=300, cwd=study, env=_cli_env())
        except subprocess.TimeoutExpired:
            pytest.fail("fastmdx analyze did not end in 300 s under a limit 8 MiB over "
                        "what it held at the check: it hung warming up the libraries")
        assert "EXIT 1" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]
        for name in ("cluster", "dimred"):
            row, findings = _record(study, name)
            assert row["status"] == "error", (name, row["message"])
            assert findings["refused"]["before_it_ran"], (name, findings)
            memory = findings["memory"]
            assert memory["bytes_libraries_not_mapped"] == clustering.WARM_UP_BYTES, memory
            assert "the first time they run, not mapped here" in row["message"], row["message"]
        # The size to add covers the libraries and the run past them.
        _, findings = _record(study, "cluster")
        more = re.search(r"`ulimit -v` allows ([\d.]+) MB more", findings["refused"]["said"])
        assert more and float(more.group(1)) * 1e6 > clustering.WARM_UP_BYTES, findings

    def test_a_new_process_s_libraries_are_warmed_up_only_where_they_fit(self):
        """The Python API in a new process, where nothing had called NumPy's
        BLAS: under a room of 66 MiB the warm-up's figure mapped NumPy's
        buffer past the limit, and OpenBLAS ended the process ("Memory
        allocation still failed after 10 retries")."""
        import subprocess
        from pathlib import Path

        script = (
            "import resource, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from fastmdxplora.analysis import cluster\n"
            "import scipy.linalg, matplotlib.pyplot\n"
            "held = cluster._process_status()['VmSize']\n"
            "resource.setrlimit(resource.RLIMIT_AS, (held + int(sys.argv[2]), "
            "resource.RLIM_INFINITY))\n"
            "room, said = cluster._rlimit_room()\n"
            "print('ROOM', room, cluster.libraries_owed(), flush=True)\n")
        src = str(Path(clustering.__file__).resolve().parents[2])
        for mib in (30, 66):
            try:
                done = subprocess.run([sys.executable, "-c", script, src, str(mib * 2**20)],
                                      capture_output=True, text=True, timeout=120,
                                      env=_cli_env())
            except subprocess.TimeoutExpired:
                pytest.fail(f"the warm-up under {mib} MiB of room did not end in 120 s")
            assert done.returncode == 0, (mib, done.stdout + done.stderr[-2000:])
            words = next(line.split() for line in done.stdout.splitlines()
                         if line.startswith("ROOM"))
            assert int(words[2]) == clustering.WARM_UP_BYTES, (mib, words)


_RLIMIT = "RLIMIT_AS less what this process holds (VmSize)"


class TestEachProjectionIsPlannedOnItsOwn:
    """dimred planned 130 MiB for any methods at any number of frames: PCA,
    which takes about 60, was refused with t-SNE where both fitted, and
    t-SNE, whose neighbours grow with the frames, was allowed at 24,000
    where they did not fit, and PCA's projection was lost with it."""

    def test_t_sne_s_plan_grows_with_the_frames_and_pca_s_does_not(self, monkeypatch):
        """In one room, t-SNE of 500 frames is allowed and of 24,000 refused
        (its neighbours: 91 of each frame at perplexity 30, measured at about
        75 bytes each, 99 MiB from the check at 15,000 frames); PCA of
        either is allowed."""
        from fastmdxplora.analysis.dimred import DimRed

        room = 100 * 2**20
        monkeypatch.setattr(clustering, "memory_available", lambda: (room, _RLIMIT))
        monkeypatch.setattr(DimRed, "_projection",
                            lambda self, method, traj, *rest: np.zeros((traj.n_frames, 2)),
                            raising=False)
        for n, refused in ((500, set()), (24000, {"tsne"})):
            analysis = DimRed(methods=["pca", "tsne"])
            ran = analysis.compute(_chain(n))
            assert set(analysis.findings.get("methods_refused", {})) == refused, n
            assert set(ran) == {"pca", "tsne"} - refused, n

    def test_at_many_frames_in_a_small_room_pca_runs_and_t_sne_is_refused(
            self, tmp_path, monkeypatch):
        from fastmdxplora.analysis.dimred import DimRed

        n = 24000
        room = 60 * 2**20
        monkeypatch.setattr(clustering, "memory_available", lambda: (room, _RLIMIT))
        result = DimRed(output_dir=tmp_path, methods=["pca", "tsne"]).run(_chain(n))
        assert result.status == "ok", result.message
        folder = result.output_dir
        assert (folder / "dimred_pca.dat").is_file() and (folder / "dimred_pca.png").is_file()
        assert not (folder / "dimred_tsne.dat").exists()
        found = json.loads((folder / "options.json").read_text())["findings"]
        refused = found["methods_refused"]["tsne"]
        assert refused["kept"] == ["pca"] and refused["before_it_ran"] is True, refused
        assert "`methods: [pca]`" in refused["said"], refused
        assert "t-SNE refused for memory" in result.message, result.message

    @pytest.mark.parametrize("error", [MemoryError("Unable to allocate 33.3 MiB for an "
                                                   "array with shape (4368000,)"),
                                       OSError(12, "Cannot allocate memory")],
                             ids=["MemoryError", "ENOMEM"])
    def test_t_sne_running_out_is_its_refusal_and_pca_is_saved(
            self, tmp_path, monkeypatch, error):
        """At 24,000 frames under a limit the CLI ended "error dimred:
        Unable to allocate 33.3 MiB", no setting said, PCA computed first
        and lost."""
        from fastmdxplora.analysis import dimred as module
        from fastmdxplora.analysis.dimred import DimRed

        class OutOfMemory:
            def __init__(self, **kwargs):
                pass

            def fit_transform(self, coords):
                raise error

        monkeypatch.setattr(module, "TSNE", OutOfMemory)
        result = DimRed(output_dir=tmp_path, methods=["pca", "tsne"]).run(_chain(120))
        assert result.status == "ok", result.message
        assert (result.output_dir / "dimred_pca.dat").is_file()
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        refused = found["methods_refused"]["tsne"]
        assert refused["before_it_ran"] is False and refused["kept"] == ["pca"], refused
        assert "`methods: [pca]`" in refused["said"] and "stride: 2" in refused["said"]

    def test_only_the_last_method_refused_refuses_the_analysis(self, monkeypatch):
        """PCA alone past the room: the analysis is refused, with the size
        by which ``ulimit -v`` falls short ("where `ulimit -v` allows
        more" said none)."""
        import re

        from fastmdxplora.analysis.dimred import DimRed
        from fastmdxplora.refusals import StudyError

        room = 12 * 2**20
        monkeypatch.setattr(clustering, "memory_available", lambda: (room, _RLIMIT))
        with pytest.raises(StudyError) as raised:
            DimRed(methods=["pca", "tsne"]).compute(_chain(200))
        said = str(raised.value)
        need = re.search(r"takes about ([\d.]+) MB of address space", said)
        more = re.search(r"`ulimit -v` allows ([\d.]+) MB more", said)
        assert said.startswith("PCA of 200 frames") and need and more, said
        assert float(more.group(1)) == pytest.approx(
            float(need.group(1)) - room / 1e6, abs=0.11), said


class TestARefusalReadsInOrder:
    def test_a_need_just_over_the_share_reads_over_it(self):
        """"need about 1.6 GB ..., and the container allows 2.2 GB, of which
        clustering plans on 75%, 1.7 GB": the share of the room as rounded
        read above the need it was refused for."""
        need, room, planned = clustering.sizes_said(1635095818, 2164731738)
        value = lambda said: float(said.split()[0].replace(",", ""))  # noqa: E731
        assert value(need) > value(planned), (need, room, planned)
        assert value(room) > value(need), (need, room, planned)

    def test_no_need_over_its_share_reads_at_or_under_it(self):
        rng = np.random.default_rng(1)
        value = lambda said: float(said.split()[0].replace(",", ""))  # noqa: E731
        for _ in range(4000):
            room = float(10 ** rng.uniform(7, 10))
            need = MEMORY_SHARE * room * (1 + rng.uniform(1e-4, 0.05))
            said = clustering.sizes_said(need, room)
            assert value(said[0]) > value(said[2]), (need, room, said)
            assert len({s.split()[1] for s in said}) == 1, said


class TestAFigureNotPlottedIsToldWhatHelpsIt:
    """A figure not plotted for memory was told "Set `stride: 4` ... to plot
    half as many frames"; followed, the dendrogram and DBSCAN's figure were
    not plotted again, at stride 4 and 8: their canvas is the same at any
    stride. Only the map of at most 1,000 frames has fewer cells for fewer
    frames."""

    @staticmethod
    def _run_out_on(monkeypatch, ending):
        """``std::bad_alloc`` raised where Agg raises it, in the figure's own
        ``savefig``, so the real ``save_figure`` closes the figure before the
        room is told: a stand-in ``save_figure`` raising before it left the
        figure open, and the room told from it was right only there."""
        import matplotlib.figure

        real = matplotlib.figure.Figure.savefig

        def savefig(fig, fname, *args, **kwargs):
            if str(fname).endswith(ending):
                raise MemoryError("std::bad_alloc")
            return real(fig, fname, *args, **kwargs)

        monkeypatch.setattr(matplotlib.figure.Figure, "savefig", savefig)

    @pytest.mark.parametrize("figure,figsize,room", [
        ("cluster_kmeans.png", None, "26.4 MB"),
        ("cluster_kmeans.png", (10, 6), "51.7 MB"),
        ("cluster_kmeans_counts.png", None, "21.4 MB"),
        ("cluster_hierarchical_dendrogram.png", None, "24.4 MB")])
    def test_a_figure_whose_canvas_is_the_same_is_told_the_room(
            self, tmp_path, monkeypatch, figure, figsize, room):
        """The room is what plotting the figure maps (``figure_needed``),
        from its own size: told from the figures still open, the figure
        that ran out saving was closed already, and every figure was told
        the house figure's 21.8 MB, a timeline of 10 x 6 inches too, where
        it needed 49.2 MB."""
        self._run_out_on(monkeypatch, figure)
        monkeypatch.setattr(clustering, "memory_available", lambda: (10**12, _RLIMIT))
        result = Cluster(output_dir=tmp_path, figsize=figsize).run(_chain(80))
        assert result.status == "ok", result.message
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        said = (found.get("figures_not_plotted", {}).get(figure)
                or json.loads((result.output_dir
                               / "cluster_hierarchical_dendrogram_skipped.json").read_text())["reason"])
        assert "Set `" not in said and "stride does not help" in said, said
        assert f"where `ulimit -v` leaves about {room} for plotting it." in said, said
        assert not plt.get_fignums()

    def test_the_map_of_few_frames_is_told_the_stride_that_makes_it_smaller(
            self, tmp_path, monkeypatch):
        self._run_out_on(monkeypatch, "cluster_rmsd_matrix.png")
        result = Cluster(output_dir=tmp_path).run(_chain(80))
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        said = found["figures_not_plotted"]["cluster_rmsd_matrix.png"]
        assert "Set `stride: 2`" in said, said
        # Followed, the map is of fewer cells.
        assert clustering.map_needed(40) < clustering.map_needed(80)

    def test_the_map_of_many_frames_is_told_the_room(self, tmp_path, monkeypatch):
        """Past 1,000 frames the map's blocks keep it as many cells across:
        2,000 frames and 1,000 make the same map."""
        assert clustering.map_needed(1000) == clustering.map_needed(2000)
        self._run_out_on(monkeypatch, "cluster_rmsd_matrix.png")
        result = Cluster(output_dir=tmp_path, methods=["kmeans"]).run(_chain(2000))
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        said = found["figures_not_plotted"]["cluster_rmsd_matrix.png"]
        assert "Set `" not in said and "stride does not help" in said, said
        # Its mesh of 1,000 cells across as well: 40 MB was said, where it
        # needed 128 MB (TestEachFigureIsPlottedInTheRoomItIsSaidToNeed).
        assert "about 142.0 MB for plotting it." in said or "about 142.0 MB is free" in said, said

    @pytest.mark.parametrize("figure", ["dimred_pca.png", "dimred_pca_landscape.png"])
    def test_a_projection_s_figure_is_told_the_room(self, tmp_path, monkeypatch, figure):
        """Told from its own size, a user's ``figsize`` (the house figure's
        was told for every one), and the landscape's mesh."""
        from fastmdxplora.analysis.dimred import DimRed

        self._run_out_on(monkeypatch, figure)
        result = DimRed(output_dir=tmp_path, figsize=(10, 6)).run(_chain(120))
        assert result.status == "ok", result.message
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        said = found["figures_not_plotted"][figure]
        assert "stride: 2" not in said and "stride does not help" in said, said
        # 2.15 canvases of 3,000 x 1,800 pixels, once, and the landscape's
        # mesh of 40 bins across.
        cells = 40 if "landscape" in figure else 0
        room = (clustering.FIGURE_CANVASES * 4 * 3000 * 1800 + clustering.FIGURE_ONCE_BYTES
                + clustering.FIGURE_MESH_BYTES_PER_CELL * cells ** 2)
        assert f"about {room / 1e6:.1f} MB " in said, said
        assert not plt.get_fignums()


#: One of clustering's figures, the first the process plots past the
#: libraries mapped, made and run out of memory saving it (in the real
#: ``save_figure``, which closes it), then plotted under ``RLIMIT_AS`` set
#: at what the process holds, collected, and what :func:`figure_needed`
#: said from the error. Exits 3 where it runs out.
_FIGURE_IN_ITS_ROOM_SCRIPT = r"""
import resource, sys
sys.path.insert(0, sys.argv[1])
import matplotlib.figure
import numpy as np
from scipy.cluster.hierarchy import linkage
from fastmdxplora.analysis import cluster as C
from fastmdxplora.analysis.plotting import close_figures_opened_since, figures_open
figure, out = sys.argv[2], sys.argv[3]
n = 1000 if figure == "map of 1,000" else 500
rng = np.random.default_rng(0)
labels = rng.integers(0, 5, n)
tree = linkage(rng.normal(size=(n, 3)), "average")
distances = np.abs(rng.normal(size=(n, n)))
C.libraries_mapped()

def made():
    if figure == "timeline":
        fig, ax = C.new_figure(title="Frames (hierarchical)")
        C._plot_cluster_timeline(ax, labels, "hierarchical", frames=np.arange(n))
    elif figure == "counts":
        fig, ax = C.new_figure(title="Cluster populations (hierarchical)", figsize=(5.8, 3.6))
        C._plot_cluster_counts(ax, labels)
    elif figure == "dendrogram":
        fig, ax = C.new_figure(title="Hierarchical clustering dendrogram", figsize=(6.5, 3.8))
        C._plot_hierarchical_dendrogram_from_linkage(ax, tree)
    else:
        fig, ax = C.new_figure(title="RMSD between frames", figsize=(5.6, 4.8))
        C._plot_distance_matrix(ax, distances, np.arange(n), np.arange(n) * 0.01)
    return fig

# Run out where nothing is rendered yet, so the figure plotted below is
# still the process's first.
tight = matplotlib.figure.Figure.tight_layout
matplotlib.figure.Figure.tight_layout = lambda *a, **k: (_ for _ in ()).throw(
    MemoryError("std::bad_alloc"))
before = figures_open()
try:
    C.save_figure(made(), out + "/ran_out.png")
except MemoryError as error:
    assert not figures_open() - before, "save_figure left the figure open"
    needed = C.figure_needed(figures_open() - before, error=error,
                             cells=n if figure.startswith("map") else 0)
matplotlib.figure.Figure.tight_layout = tight
close_figures_opened_since(before)
C.give_back_freed()
for line in open("/proc/self/status"):
    if line.startswith("VmSize:"):
        held = int(line.split()[1]) * 1024
resource.setrlimit(resource.RLIMIT_AS, (held + needed, resource.RLIM_INFINITY))
try:
    C.save_figure(made(), out + "/plotted.png")
except MemoryError as error:
    print("RAN OUT", needed, error, flush=True)
    sys.exit(3)
print("PLOTTED", needed, flush=True)
"""

#: A clustering with k-means of the frames given (:func:`_chain`) in a new
#: process, one of its figures plotted under ``RLIMIT_AS`` set as
#: :meth:`Cluster._plotted` begins, at what the process holds, collected,
#: and the room told for a figure of its size run out saving
#: (:func:`figure_needed` from the error ``save_figure`` raises), and
#: ``argv[6]`` bytes more. Nothing of the figure is plotted before: run out
#: first and plotted again, it was warmed, and a 13 x 8.4 timeline told
#: 80.98 MiB plotted there, where cold it needed 82.2. Exits 3 where it
#: runs out.
_A_FIGURE_IN_A_RUN_SCRIPT = r"""
import resource, sys
sys.path.insert(0, sys.argv[1])
import mdtraj as md
import numpy as np
from fastmdxplora.analysis import cluster as C
from fastmdxplora.analysis.plotting import PAPER_FIGSIZE, scaled_figsize
out, target, n = sys.argv[2], sys.argv[3], int(sys.argv[4])
figsize = None if sys.argv[5] == "house" else tuple(float(v) for v in sys.argv[5].split("x"))
more = int(sys.argv[6]) if len(sys.argv) > 6 else 0
rng = np.random.default_rng(0)
top = md.Topology()
chain = top.add_chain()
for i in range(4):
    top.add_atom("CA", md.element.carbon, top.add_residue("ALA", chain, resSeq=i + 1))
xyz = rng.normal(0.0, 0.4, (4, 3))[None] + np.cumsum(rng.normal(0.0, 0.01, (n, 4, 3)), 0)
traj = md.Trajectory(xyz.astype(np.float32), top)
traj.time = np.arange(n) * 10.0
the_map = target.startswith("cluster_rmsd_matrix")
error = MemoryError("std::bad_alloc")
error.figure_inches = scaled_figsize((5.6, 4.8) if the_map else figsize or PAPER_FIGSIZE)
error.figure_dpi, error.figure_number = 300, None
needed = C.figure_needed(set(), error=error,
                         cells=-(-n // C.map_block(n)) if the_map else 0)
real, done = C.Cluster._plotted, []

def plotted(self, path, plot):
    if path.name != target:
        return real(self, path, plot)
    C.give_back_freed()
    held = C._process_status()["VmSize"]
    resource.setrlimit(resource.RLIMIT_AS, (held + needed + more, resource.RLIM_INFINITY))
    try:
        written = real(self, path, plot)
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY,) * 2)
    done.append(bool(written))
    return written

C.Cluster._plotted = plotted
C.Cluster(output_dir=out, methods=["kmeans"], figsize=figsize).run(traj)
print(("PLOTTED" if done == [True] else "RAN OUT"), needed, done, flush=True)
sys.exit(0 if done == [True] else 3)
"""


class TestEachFigureIsPlottedAfterTheOnesBefore:
    """A figure saved and closed held its canvas until Python's garbage
    collector next ran, and on Python 3.10 it had not when the next was
    plotted: under ``ulimit -v`` 600000, tri-alanine's 500 frames, whose
    plan fitted, ran out plotting the dendrogram (``std::bad_alloc``) with
    four figures' canvases still held, 56.3 MiB past the check (the run's
    peak there; 3.13's run peaked at 43.6, as the map was plotted)."""

    def test_no_figure_before_is_held_as_the_next_is_plotted(self, tmp_path, monkeypatch):
        import weakref

        real = clustering.new_figure
        made, held = [], []

        def counted(*args, **kwargs):
            held.append(sum(1 for figure in made if figure() is not None))
            fig, ax = real(*args, **kwargs)
            made.append(weakref.ref(fig))
            return fig, ax

        monkeypatch.setattr(clustering, "new_figure", counted)
        result = Cluster(output_dir=tmp_path).run(_chain(120))
        assert result.status == "ok", result.message
        # The timeline and counts of each method, the dendrogram, the map.
        assert len(held) == 6, held
        assert held == [0] * 6, held

    @pytest.mark.skipif(sys.platform != "linux", reason="reads Linux /proc/self/status")
    @pytest.mark.parametrize("figure", ["timeline", "counts", "dendrogram", "map",
                                        "map of 1,000"])
    def test_the_room_a_figure_is_said_to_need_plots_it(self, tmp_path, figure):
        """Run out of memory saving it, the figure closed, and told the room
        (``figure_needed``): under ``RLIMIT_AS`` set at what the process
        holds, collected, and that room, it plots. The map of 1,000 cells
        binds the mesh's 116 bytes a cell: counted so, it plotted in 130.0
        MiB on Python 3.10 and 3.11 and 124.5 on 3.13, and is said as 135.1;
        90 a cell said 110.3."""
        import subprocess
        from pathlib import Path

        src = str(Path(clustering.__file__).resolve().parents[2])
        done = subprocess.run(
            [sys.executable, "-c", _FIGURE_IN_ITS_ROOM_SCRIPT, src, figure, str(tmp_path)],
            capture_output=True, text=True, timeout=600, env=_cli_env())
        assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-3000:]
        assert "PLOTTED" in done.stdout, done.stdout[-2000:]

    @pytest.mark.skipif(sys.platform != "linux", reason="reads Linux /proc/self/status")
    @pytest.mark.parametrize("target,frames,figsize,malloc", [
        ("cluster_kmeans.png", 500, "house", ""), ("cluster_kmeans.png", 500, "10x6", ""),
        ("cluster_kmeans.png", 500, "13x8.4", ""), ("cluster_kmeans.png", 500, "20x12", ""),
        ("cluster_kmeans.png", 500, "13x8.4", "fixed"),
        ("cluster_rmsd_matrix.png", 2000, "house", "")])
    def test_a_figure_of_a_run_plots_in_the_room_it_is_told(
            self, tmp_path, target, frames, figsize, malloc):
        """A figure of a clustering in a new process, as a run that follows
        the message, plotted under ``RLIMIT_AS`` set at what the process
        holds, collected, and the room it is told: it plots, with glibc's
        malloc as it starts and with its thresholds fixed (``malloc``
        "fixed", as this machine's own environment sets them). Counted so
        through ``fastmdx analyze`` (:data:`FIGURE_CANVASES`), the first
        k-means timeline needed at most 23.5 MiB at the house's size, 46.95
        at 10 x 6, 82.48 at 13 x 8.4 and 176.79 at 20 x 12, where two
        canvases and 6 MiB told 24.75, 47.2, 80.98 and 170.79. Counted
        after the figure had run out once in the same process, it was
        warmed, and two canvases seemed enough. The map of 2,000 frames,
        1,000 cells across, needed 130.0 MiB on 3.10, 3.11 and 3.13."""
        import subprocess
        from pathlib import Path

        env = _cli_env()
        if malloc == "fixed":
            env.update(MALLOC_MMAP_THRESHOLD_="67108864", MALLOC_TRIM_THRESHOLD_="268435456")
        src = str(Path(clustering.__file__).resolve().parents[2])
        done = subprocess.run(
            [sys.executable, "-c", _A_FIGURE_IN_A_RUN_SCRIPT, src, str(tmp_path), target,
             str(frames), figsize],
            capture_output=True, text=True, timeout=600, env=env)
        assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-3000:]
        assert "PLOTTED" in done.stdout, done.stdout[-2000:]


class TestTheRefusalSaysWhatToSet:
    def test_a_larger_ulimit_v_is_said_with_its_size(self, monkeypatch):
        """"Run it where more memory is given (a larger `ulimit -v`)" named
        no size; the refusal has what it takes."""
        import re

        from fastmdxplora.refusals import StudyError

        room = 30 * 2**20
        monkeypatch.setattr(clustering, "memory_available", lambda: (room, _RLIMIT))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(500))
        said = str(raised.value)
        more = re.search(r"where `ulimit -v` allows ([\d.]+) MB more", said)
        need = re.search(r"takes about ([\d.]+) MB of address space", said)
        assert more and need, said
        assert float(more.group(1)) == pytest.approx(
            float(need.group(1)) - room / 1e6, abs=0.11), said

    def test_the_libraries_share_is_said_as_part_of_the_need(self, monkeypatch):
        """"309.7 MB of address space ..., with 157.3 MB the libraries and
        figures map besides" read as on top of the need."""
        from fastmdxplora.refusals import StudyError

        monkeypatch.setattr(clustering, "memory_available", lambda: (150 * 2**20, _RLIMIT))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(3000))
        said = str(raised.value)
        assert "besides" not in said, said
        assert " of it what the run maps past the libraries" in said, said

    def test_the_map_s_need_is_not_split_by_the_matrix_it_follows(self, monkeypatch):
        """At 2,000 frames the address plan is the map's: "164.4 MB of
        address space to plot the map of their RMSD matrix, once the matrix
        is freed (64.4 MB of it what the run maps past the libraries: its
        figures and the heap)" left 100 MB to the matrix the same sentence
        said was freed."""
        from fastmdxplora.refusals import StudyError

        monkeypatch.setattr(clustering, "memory_available", lambda: (150 * 2**20, _RLIMIT))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(2000))
        said = str(raised.value)
        need = clustering.address_space_needed(2000, ["kmeans", "hierarchical"], "rmsd",
                                               "average")
        assert (f"need about {need / 1e6:.1f} MB of address space to plot the map of "
                "their RMSD matrix, once the matrix is freed, and") in said, said
        assert " of it what the run maps" not in said, said

    def test_the_margin_is_said_for_the_frames_counted_nearest(self, monkeypatch):
        """"The plan keeps 16 MB to 41 MB past what a clustering where other
        analyses ran first was counted to need, at 500 to 3,000 frames": a
        range, not this run's, and said as well where the plan is the least
        at any number of frames, which was not counted so."""
        from fastmdxplora.refusals import StudyError

        methods = ["kmeans", "hierarchical"]
        for n, near in ((2000, 2000), (1900, 2000)):
            monkeypatch.setattr(clustering, "memory_available", lambda: (100 * 2**20, _RLIMIT))
            with pytest.raises(StudyError) as raised:
                Cluster().compute(_chain(n))
            said = str(raised.value)
            kept = (clustering.address_space_needed(near, methods, "rmsd", "average")
                    - clustering.libraries_owed() - clustering.ADDRESS_COUNTED[near]) / 1e6
            where = (f"At these {n:,} frames" if n == near
                     else f"At {near:,} frames, the count nearest these {n:,}")
            assert (f" {where}, the plan keeps {kept:.1f} MB past what k-means and average "
                    "linkage were counted to need where other analyses ran first, because "
                    "a process that ran nothing before maps more.") in said, said
        monkeypatch.setattr(clustering, "memory_available", lambda: (10 * 2**20, _RLIMIT))
        with pytest.raises(StudyError) as raised:
            Cluster().compute(_chain(500))
        said = str(raised.value)
        assert "at any stride" in said and "the plan keeps" not in said, said

    def test_the_matrix_s_file_running_out_is_said_with_its_setting(
            self, tmp_path, monkeypatch):
        """"cluster ✗ error ... Unable to allocate output buffer.": the
        map's data saved compressed ran out, and the CLI said no setting."""
        real = np.savez_compressed

        def out(path, *args, **kwargs):
            if str(path).endswith("cluster_rmsd_matrix.npz"):
                raise MemoryError("Unable to allocate output buffer.")
            return real(path, *args, **kwargs)

        monkeypatch.setattr(clustering.np, "savez_compressed", out)
        result = Cluster(output_dir=tmp_path).run(_chain(80))
        assert result.status == "ok", result.message
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        said = found["not_saved"]["cluster_rmsd_matrix.npz"]
        assert "Set `stride: 2`" in said, said
        assert (result.output_dir / "cluster_rmsd_matrix.png").is_file()
        assert "not saved, the memory ran out: cluster_rmsd_matrix.npz" in result.message

    def test_the_matrix_s_file_of_many_frames_is_said_with_no_canvas(
            self, tmp_path, monkeypatch):
        """Past 1,000 frames no stride makes the map's data smaller, and its
        file was told "Its canvas is the same at any stride ... allows
        more", of a file, with no number: no room was counted for it, so
        none is said."""
        real = np.savez_compressed

        def out(path, *args, **kwargs):
            if str(path).endswith("cluster_rmsd_matrix.npz"):
                raise MemoryError("Unable to allocate output buffer.")
            return real(path, *args, **kwargs)

        monkeypatch.setattr(clustering.np, "savez_compressed", out)
        result = Cluster(output_dir=tmp_path, methods=["kmeans"]).run(_chain(2000))
        assert result.status == "ok", result.message
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        said = found["not_saved"]["cluster_rmsd_matrix.npz"]
        assert said.endswith("The labels and the other figures are saved."), said
        assert "canvas" not in said and "Set `" not in said, said

    def test_one_refused_before_it_ran_is_recorded_so(self, tmp_path, monkeypatch):
        monkeypatch.setattr(clustering, "memory_available", lambda: (
            10 * 10**6, "the cgroup v2 limit, mocked"))
        result = Cluster(output_dir=tmp_path).run(_chain(500))
        assert result.status == "error"
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        assert found["refused"].get("before_it_ran") is True, found["refused"]
