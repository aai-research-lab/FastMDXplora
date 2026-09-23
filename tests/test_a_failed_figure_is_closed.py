"""A figure that fails to be drawn or saved is closed all the same.

A figure is made, drawn, and closed by save_figure. When drawing or saving
raised in between, it was never closed: one analysis whose plot failed left
one open for the rest of the process, and a sweep or a long GUI session
accumulated them and their memory, until matplotlib warned that more than
twenty were open. Each place that makes a figure is made to fail here, and
the figures open afterwards are the ones that were open before.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import mdtraj as md  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from pathlib import Path  # noqa: E402


def _open():
    return set(plt.get_fignums())


def _a_peptide(tmp_path, n_frames=12):
    topology = md.Topology()
    chain = topology.add_chain()
    for _ in range(6):
        residue = topology.add_residue("ALA", chain)
        for name, element in (("N", "nitrogen"), ("CA", "carbon"), ("C", "carbon"),
                              ("O", "oxygen"), ("CB", "carbon")):
            topology.add_atom(name, getattr(md.element, element), residue)
    rng = np.random.default_rng(0)
    xyz = rng.normal(0, 0.8, (1, topology.n_atoms, 3)) + np.cumsum(
        rng.normal(0, 0.02, (n_frames, topology.n_atoms, 3)), axis=0)
    return md.Trajectory(xyz.astype(np.float32), topology)


def test_a_caller_s_own_figure_is_left_open(tmp_path) -> None:
    # Closing is for what the failing step opened; a figure somebody had
    # open already -- in a notebook, say -- is theirs.
    from fastmdxplora.analysis.plotting import closes_what_it_opens

    theirs = plt.figure()
    with pytest.raises(RuntimeError), closes_what_it_opens():
        plt.figure()
        raise RuntimeError("drawing failed")
    assert _open() == {theirs.number}
    plt.close(theirs)


def test_an_analysis_whose_plot_fails(tmp_path) -> None:
    from fastmdxplora.analysis.base import Analysis

    class Fails(Analysis):
        name = "fails_to_plot"
        description = "computes, then cannot draw"

        def compute(self, traj):
            return pd.DataFrame({"frame": [0, 1], "value": [1.0, 2.0]})

        def plot(self, result, ax):
            raise RuntimeError("plot failed")

    before = _open()
    result = Fails(output_dir=str(tmp_path)).run(_a_peptide(tmp_path))
    assert result.status == "error"
    assert _open() == before


def test_a_save_that_fails(tmp_path) -> None:
    from fastmdxplora.analysis.plotting import new_figure, save_figure

    (tmp_path / "a_file").write_text("not a folder", encoding="utf-8")
    before = _open()
    fig, ax = new_figure(title="t")
    with pytest.raises(OSError):
        save_figure(fig, tmp_path / "a_file" / "figure.png")
    assert _open() == before


@pytest.mark.parametrize("name, option", [("cluster", {"methods": ["kmeans"], "n_clusters": 2}),
                                          ("dimred", {"methods": ["pca"]})])
def test_an_analysis_that_draws_its_own_figures(tmp_path, monkeypatch, name, option) -> None:
    # Cluster and dimred make their figures in their own run, and catch
    # every failure there to report it; so they close in that handler.
    import importlib

    from fastmdxplora.analysis.orchestrator import get_analysis_class

    module = importlib.import_module(f"fastmdxplora.analysis.{name}")

    def fails(fig, path, **kwargs):
        raise OSError("the figure could not be written")

    monkeypatch.setattr(module, "save_figure", fails)
    before = _open()
    result = get_analysis_class(name)(output_dir=str(tmp_path), **option).run(_a_peptide(tmp_path))
    assert result.status == "error"
    assert _open() == before


def test_the_dendrogram_that_fails_on_its_own(tmp_path, monkeypatch) -> None:
    # Its failure is caught and the run carries on without it; its figure
    # is closed there, not left behind.
    from fastmdxplora.analysis import cluster

    real = cluster.save_figure

    def fails_for_the_dendrogram(fig, path, **kwargs):
        # By the file's name: the folder pytest makes is named after this
        # test, and holds the word too.
        if "dendrogram" in Path(path).name:
            raise OSError("the dendrogram could not be written")
        return real(fig, path, **kwargs)

    monkeypatch.setattr(cluster, "save_figure", fails_for_the_dendrogram)
    before = _open()
    result = cluster.Cluster(output_dir=str(tmp_path), methods=["hierarchical"],
                             n_clusters=2).run(_a_peptide(tmp_path))
    assert result.status == "ok", result.message
    assert _open() == before


@pytest.mark.parametrize("module, function", [
    ("fastmdxplora.batch.compare", "_trend_plot"),
    ("fastmdxplora.batch.compare", "_overlay_plot"),
])
def test_a_comparison_figure_that_fails(tmp_path, monkeypatch, module, function) -> None:
    import importlib

    compare = importlib.import_module(module)

    def fails(*args, **kwargs):
        raise OSError("the figure could not be written")

    monkeypatch.setattr(compare, "save_figure", fails)
    before = _open()
    with pytest.raises(OSError):
        if function == "_trend_plot":
            compare._trend_plot("rmsd", "RMSD", "nm", "mean", "temperature_K",
                                [(300.0, 0.1), (310.0, 0.2)], tmp_path / "trend.png")
        else:
            compare._overlay_plot("rmsd", "RMSD", "nm",
                                  [("a", np.arange(3.0)), ("b", np.arange(3.0) + 1)],
                                  tmp_path / "overlay.png")
    assert _open() == before
