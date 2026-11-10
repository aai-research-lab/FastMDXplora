"""A clustering clusters at most 5,000 frames unless told otherwise.

Past them every k-th frame is clustered, k the least stride that leaves at
most that many, and the stride is recorded with each cluster's frames. A
run of 128,000 frames was killed making its matrix; the frames' matrix
grows with their square, so the cap bounds it.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

import fastmdxplora.analysis  # noqa: F401 -- registers the analyses
from fastmdxplora.analysis import cluster as clustering
from fastmdxplora.analysis.cluster import Cluster
from fastmdxplora.refusals import StudyError

sys.path.insert(0, str(Path(__file__).parent))
from test_a_clustering_fits_the_machine import _chain  # noqa: E402


def _findings(result) -> dict:
    return json.loads((result.output_dir / "options.json").read_text())["findings"]


class TestTheDefault:
    def test_it_is_5000_frames_and_offered(self):
        from fastmdxplora.analysis.describe import describe_analysis

        assert getattr(clustering, "MAX_FRAMES", None) == 5000
        options = {option.name: option for option in describe_analysis("cluster")}
        assert options["max_frames"].default == 5000
        assert options["max_frames"].documented
        assert options["stride"].default == 1

    @pytest.mark.parametrize("given, stride", [
        (10, 1), (5000, 1), (5001, 2), (10000, 2), (10001, 3), (12000, 3),
        (128000, 26)])
    def test_the_stride_is_the_least_that_leaves_at_most_5000(self, given, stride):
        assert clustering.stride_for(given, 5000) == stride
        assert math.ceil(given / stride) <= 5000
        if stride > 1:
            assert math.ceil(given / (stride - 1)) > 5000

    def test_past_5000_frames_every_kth_is_clustered(self):
        """On coordinates k-means makes no matrix, so 5,001 frames are cheap."""
        analysis = Cluster(methods=["kmeans"], features="coordinates")
        labels = analysis.compute(_chain(5001))["kmeans"]
        assert len(labels) == 2501
        frames = analysis.findings["frames"]
        assert frames["stride"] == 2 and frames["n_frames_clustered"] == 2501
        assert frames["max_frames"] == 5000
        assert "Every 2nd frame of those was clustered, 2,501 frames" in frames["said"]
        assert "`max_frames: 5000`" in frames["said"]
        assert "`max_frames: null` leaves the frames to `stride`" in frames["said"]
        # The memory is checked on the frames clustered.
        assert analysis.findings["memory"]["frames"] == 2501

    def test_at_5000_frames_every_one_is_clustered(self):
        analysis = Cluster(methods=["kmeans"], features="coordinates")
        assert len(analysis.compute(_chain(5000))["kmeans"]) == 5000
        assert "stride" not in analysis.findings["frames"]
        assert analysis.options["max_frames"] == 5000 and analysis.options["stride"] == 1


@pytest.fixture(scope="module")
def capped(tmp_path_factory):
    """250 frames at most 100: every 3rd frame, 84 of them."""
    out = tmp_path_factory.mktemp("capped")
    return Cluster(output_dir=out, max_frames=100).run(_chain(250))


class TestTheStrideIsRecordedWithThePopulations:
    N, MOST = 250, 100

    def test_each_cluster_s_frames_with_the_stride(self, capped):
        assert capped.status == "ok", capped.message
        found = _findings(capped)
        assert found["frames"]["stride"] == 3
        counted = found["populations"]
        assert counted["stride"] == 3
        assert counted["n_frames_clustered"] == 84 and counted["n_frames_analysed"] == self.N
        for method in ("kmeans", "hierarchical"):
            clusters = counted["by_method"][method]["clusters"]
            assert sum(row["frames"] for row in clusters) == 84
            # True fractions of the frames clustered.
            for row in clusters:
                assert row["fraction"] == pytest.approx(row["frames"] / 84)
            table = pd.read_csv(capped.output_dir / f"cluster_{method}_populations.csv")
            assert list(table["frames"]) == [row["frames"] for row in clusters]

    def test_it_is_said_counted_on_every_kth_frame(self, capped):
        said = _findings(capped)["populations"]["said"]
        assert said.startswith("Cluster populations were counted on every 3rd frame "
                               "(`max_frames: 100`)")
        assert "of the 84 clustered, of 250 analysed" in said

    def test_the_labels_are_of_every_kth_frame(self, capped):
        table = pd.read_csv(capped.output_dir / "cluster_kmeans.dat")
        assert list(table["frame"][:3]) == [0, 3, 6] and len(table) == 84

    def test_the_report_says_it(self, capped):
        from fastmdxplora.report.document import _findings_notes

        notes = _findings_notes(_findings(capped))
        assert any(note.startswith("Cluster populations were counted on every 3rd frame")
                   for note in notes), notes

    def test_every_frame_says_nothing_of_a_stride(self, tmp_path):
        from fastmdxplora.report.document import _findings_notes

        result = Cluster(output_dir=tmp_path, methods=["kmeans"]).run(_chain(80))
        found = _findings(result)
        assert found["populations"]["stride"] == 1
        assert "said" not in found["populations"]
        assert not any("counted on every" in note for note in _findings_notes(found))

    def test_dbscan_s_noise_is_counted_apart(self, tmp_path):
        result = Cluster(output_dir=tmp_path, methods=["dbscan"], eps=0.02,
                         max_frames=self.MOST).run(_chain(self.N))
        assert result.status == "ok", result.message
        entry = _findings(result)["populations"]["by_method"]["dbscan"]
        labels = pd.read_csv(tmp_path / "cluster" / "cluster_dbscan.dat")["cluster"]
        assert entry.get("noise_frames", 0) == int((labels == -1).sum())
        assert sum(row["frames"] for row in entry["clusters"]) + entry.get(
            "noise_frames", 0) == 84


class TestAPersonSetsTheirOwn:
    def test_null_clusters_every_frame(self):
        analysis = Cluster(methods=["kmeans"], features="coordinates", max_frames=None)
        assert len(analysis.compute(_chain(5001))["kmeans"]) == 5001
        assert "stride" not in analysis.findings["frames"]

    def test_a_stride_over_the_cap_s_is_used(self):
        analysis = Cluster(methods=["kmeans"], stride=4, max_frames=100)
        assert len(analysis.compute(_chain(250))["kmeans"]) == 63
        assert analysis.options["stride"] == 4
        assert "63 frames, as `stride: 4` asks." in analysis.findings["frames"]["said"]

    def test_a_stride_under_the_cap_s_is_raised_and_said(self, tmp_path):
        result = Cluster(output_dir=tmp_path, stride=2, max_frames=100).run(_chain(250))
        assert result.status == "ok", result.message
        found = _findings(result)
        assert found["frames"]["stride"] == 3 and found["frames"]["n_frames_clustered"] == 84
        assert ("the least stride that leaves at most 100 (`max_frames: 100`), where "
                "`stride: 2` asked for every 2nd.") in found["frames"]["said"]
        assert "(`max_frames: 100`)" in found["populations"]["said"]

    @pytest.mark.parametrize("stride, clustered", [(1, 250), (2, 125)])
    def test_null_leaves_the_frames_to_the_stride(self, stride, clustered):
        analysis = Cluster(methods=["kmeans"], stride=stride, max_frames=None)
        assert len(analysis.compute(_chain(250))["kmeans"]) == clustered

    @pytest.mark.parametrize("given", [0, -5, True, 2.5, "all"])
    def test_a_cap_that_is_not_one_is_refused(self, given):
        with pytest.raises(StudyError) as raised:
            Cluster(max_frames=given)
        assert raised.value.code == "analysis.option.out_of_range"
        assert "`max_frames`" in str(raised.value) and "null to leave the frames" in str(
            raised.value)

    def test_every_frame_is_still_checked_against_the_memory(self, monkeypatch):
        """Past the cap, the matrix of every frame is refused where it does
        not fit, with the stride that does."""
        n = 1000
        room = int(clustering.memory_needed(n // 2, ["kmeans", "hierarchical"], "rmsd",
                                            "average") / clustering.MEMORY_SHARE) + 1
        monkeypatch.setattr(clustering, "memory_available", lambda: (room, "mocked"))
        big = clustering.memory_needed(n, ["kmeans", "hierarchical"], "rmsd", "average")
        assert big > clustering.MEMORY_SHARE * room
        with pytest.raises(StudyError) as raised:
            Cluster(max_frames=None).compute(_chain(n))
        assert raised.value.code == "analysis.memory.matrix_too_large"
        assert "stride: " in str(raised.value)


class TestTheMemoryCheckAgreesWithTheCap:
    def test_the_plan_is_of_the_frames_clustered(self, monkeypatch):
        monkeypatch.setattr(clustering, "memory_available", lambda: (10**12, "mocked"))
        analysis = Cluster(methods=["kmeans"], max_frames=100)
        analysis.compute(_chain(250))
        memory = analysis.findings["memory"]
        assert memory["frames"] == 84
        assert memory["bytes_needed"] == clustering.memory_needed(
            84, ["kmeans"], "rmsd", "average")

    def test_a_refusal_names_no_stride_under_the_cap_s(self, monkeypatch):
        """The stride the refusal searches from is the cap's: one under it
        would cluster more frames than were refused."""
        n, most = 3000, 1500           # the cap's stride is 2, 1,500 frames
        methods = ["kmeans", "hierarchical"]
        need = clustering.memory_needed(1500, methods, "rmsd", "average")
        room = int(need * 0.9 / clustering.MEMORY_SHARE)
        monkeypatch.setattr(clustering, "memory_available", lambda: (room, "mocked"))
        with pytest.raises(StudyError) as raised:
            Cluster(max_frames=most).compute(_chain(n))
        from fastmdxplora.refusals import refusal_of

        said = str(raised.value)
        stride = refusal_of(raised.value).details["stride"]
        assert stride > 2
        fits = math.ceil(n / stride)
        assert clustering.memory_needed(fits, methods, "rmsd", "average") <= (
            clustering.MEMORY_SHARE * room)
        assert f"Set `stride: {stride}`" in said
        assert "clustering 1,500 frames (every 2nd of 3,000) does not" in said
