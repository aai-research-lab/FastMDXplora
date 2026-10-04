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
