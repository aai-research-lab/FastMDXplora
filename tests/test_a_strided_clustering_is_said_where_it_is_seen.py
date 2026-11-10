"""A clustering of every k-th frame says so wherever it is seen.

Clustering takes every k-th frame past `max_frames`, and its record and the
report's findings said so; the GUI's Conformations section, its charts and
the clustering's figures (PNG and SVG) did not, nor did the console, and the
report listed `stride: 1` beside "counted on every 3rd frame". A count of
5,000 frames read as the whole of a run of 10,000.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

import fastmdxplora.analysis  # noqa: F401 -- registers the analyses
from fastmdxplora.analysis import cluster as clustering
from fastmdxplora.analysis.cluster import Cluster

sys.path.insert(0, str(Path(__file__).parent))
from test_a_clustering_fits_the_machine import _chain  # noqa: E402

NULL = "`max_frames: null` clusters every frame."


def _study(root: Path, n: int = 250, **options) -> Path:
    """A study folder whose analysis is this clustering alone, its manifest
    as the orchestrator writes its plan and results."""
    analysis = root / "analysis"
    result = Cluster(output_dir=analysis, **options).run(_chain(n))
    assert result.status == "ok", result.message
    (analysis / "analysis_manifest.json").write_text(json.dumps(
        {"plan": ["cluster"], "results": {"cluster": result.to_dict()}}))
    return root


@pytest.fixture(scope="module")
def strided(tmp_path_factory) -> Path:
    return _study(tmp_path_factory.mktemp("strided"), max_frames=100)   # every 3rd


@pytest.fixture(scope="module")
def whole(tmp_path_factory) -> Path:
    return _study(tmp_path_factory.mktemp("whole"), n=80)


def _said(root: Path) -> str:
    return json.loads((root / "analysis" / "cluster" / "options.json").read_text())[
        "findings"]["populations"].get("said", "")


def test_the_record_says_what_clusters_every_frame(strided, whole):
    said = _said(strided)
    assert said.startswith("Cluster populations were counted on every 3rd frame "
                           "(`max_frames: 100`)") and said.endswith(NULL)
    assert _said(whole) == ""


def test_each_figure_says_it_below(tmp_path, monkeypatch):
    saved: dict[str, list[str]] = {}
    real = clustering.save_figure

    def noting(fig, path, *args, **kwargs):
        saved[Path(path).name] = [text.get_text() for text in fig.texts]
        return real(fig, path, *args, **kwargs)

    monkeypatch.setattr(clustering, "save_figure", noting)
    result = Cluster(output_dir=tmp_path / "analysis", max_frames=100).run(_chain(250))
    assert result.status == "ok", result.message
    said = _said(tmp_path)
    for name in ("cluster_kmeans.png", "cluster_kmeans_counts.png",
                 "cluster_hierarchical.png", "cluster_hierarchical_counts.png",
                 "cluster_hierarchical_dendrogram.png", "cluster_rmsd_matrix.png"):
        assert said in saved[name], (name, saved.get(name))
    saved.clear()
    Cluster(output_dir=tmp_path / "every", methods=["kmeans"]).run(_chain(80))
    assert all(not texts for texts in saved.values()), saved


@pytest.mark.parametrize("figure", ["cluster_kmeans", "cluster_kmeans_counts",
                                    "cluster_hierarchical_dendrogram", "cluster_rmsd_matrix"])
def test_the_gui_s_charts_say_it_below(strided, whole, figure):
    from fastmdxplora.gui.figure_data import figure_payload

    payload = figure_payload(strided, f"analysis/cluster/{figure}.png")
    assert payload["ok"], payload
    assert payload["figure_note"] == _said(strided)
    if figure != "cluster_hierarchical_dendrogram":
        every = figure_payload(whole, f"analysis/cluster/{figure}.png")
        assert every["ok"] and not every.get("figure_note"), every


def test_the_conformations_row_says_it(strided, whole):
    from fastmdxplora.gui.analysis_overview import _row

    row = _row(strided, "cluster", {"status": "ok"}, {}, False)
    assert row["finding"] == _said(strided)
    assert not _row(whole, "cluster", {"status": "ok"}, {}, False).get("finding")


def test_the_console_says_the_cap_chose_it(caplog):
    with caplog.at_level(logging.INFO, logger="fastmdx.analysis.cluster"):
        Cluster(methods=["kmeans"], features="coordinates").compute(_chain(5001))
    said = [r.getMessage() for r in caplog.records if "Clustering every" in r.getMessage()]
    assert said == ["Clustering every 2nd frame, 2,501 of the 5,001 analysed: the least "
                    "stride that leaves at most 5,000 (`max_frames: 5000`). " + NULL]


def test_the_report_names_the_stride_used(strided):
    from fastmdxplora.report.document import _results_section

    section = _results_section(strided)
    assert ("- `stride`: `1`: every 3rd frame was used, the least stride "
            "`max_frames: 100` allows") in section
    assert NULL in section


def test_a_stride_given_is_said_with_how_to_cluster_every_frame(tmp_path):
    root = _study(tmp_path, stride=2, max_frames=None)
    assert _said(root).endswith("`stride: 1` with `max_frames: null` clusters every frame.")
    from fastmdxplora.report.document import _results_section

    assert "every 2nd frame was used" not in _results_section(root)


def test_the_timeline_s_medoid_is_of_the_frames_clustered(strided):
    from fastmdxplora.gui.timeline_view import _medoids

    marks = _medoids(strided)
    assert marks and all(mark["label"].endswith("of the frames clustered") for mark in marks)


def test_the_frames_are_counted_alike_in_one_sentence():
    analysis = Cluster(methods=["kmeans"], features="coordinates")
    analysis.compute(_chain(12000))
    said = analysis.findings["frames"]["said"]
    assert said.startswith("All 12,000 frames were analysed")
    assert "4,000 frames" in said
