"""Each projection keeps what belongs to it, and runs on the frames it is given."""

from __future__ import annotations

import mdtraj as md
import numpy as np

from fastmdxplora.analysis.dimred import DimRed


def _alpha_carbons(n_frames: int, n_atoms: int = 8, seed: int = 0) -> md.Trajectory:
    rng = np.random.default_rng(seed)
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(n_atoms):
        top.add_atom("CA", md.element.carbon, top.add_residue("ALA", chain))
    return md.Trajectory(rng.normal(0, 0.5, (n_frames, n_atoms, 3)).astype(np.float32), top)


def test_the_pca_axes_keep_their_variance_when_mds_runs_after(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from fastmdxplora.analysis.dimred import _plot_dimred_scatter

    analysis = DimRed(methods=["pca", "mds"], output_dir=tmp_path)
    result = analysis.run(_alpha_carbons(30))
    assert result.status == "ok", result.message

    fig, ax = plt.subplots()
    _plot_dimred_scatter(ax, analysis.result["pca"], "pca", analysis._explained_variance)
    label = ax.get_xlabel()
    plt.close(fig)
    assert label.startswith("PC 1 (") and label.endswith("%)")       # was "PCA 1"


def test_tsne_runs_on_five_frames(tmp_path):
    import json

    result = DimRed(methods=["tsne"], output_dir=tmp_path).run(_alpha_carbons(5))

    assert result.status == "ok", result.message
    record = json.loads((tmp_path / "dimred" / "options.json").read_text())
    assert record["findings"]["tsne"]["perplexity_used"] == 4 / 3


def test_the_perplexity_used_stays_below_the_frames():
    from fastmdxplora.analysis.dimred import _tsne_perplexity

    for n in range(2, 200):
        assert 1.0 <= _tsne_perplexity(30.0, n) < n
    assert _tsne_perplexity(30.0, 1000) == 30.0
