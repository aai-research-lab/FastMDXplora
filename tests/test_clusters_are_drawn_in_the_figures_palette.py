"""Clusters are drawn in the figures' own palette.

Every analysis figure asks the plotting module for its colours, so a
greyscale figure is grey and a coloured one is in Okabe and Ito's palette,
which the colour-blind can tell apart. The cluster figures had Tableau's ten
colours written in, and came out in colour when greyscale was asked for.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("mdtraj")

from matplotlib.colors import to_hex  # noqa: E402

from fastmdxplora.analysis.cluster import _plot_cluster_counts, _plot_cluster_timeline  # noqa: E402
from fastmdxplora.analysis.plotting import (  # noqa: E402
    GREYSCALE_PALETTE, PALETTE, category_style, drawn_in, new_figure,
)

LABELS = np.array([0, 0, 1, 1, 2, 2, -1, 3])


def _drawn(mode: str):
    with drawn_in(mode):
        fig, ax = new_figure()
        _plot_cluster_timeline(ax, LABELS, "kmeans")
        fig_counts, counts = new_figure()
        _plot_cluster_counts(counts, LABELS)
    points = [(to_hex(c.get_facecolor()[0]), c.get_label()) for c in ax.collections]
    bars = [to_hex(bar.get_facecolor()) for bar in counts.patches]
    shapes = {c.get_label(): c.get_paths()[0].vertices.shape for c in ax.collections}
    import matplotlib.pyplot as plt

    plt.close(fig)
    plt.close(fig_counts)
    return points, bars, shapes


def test_in_colour_the_palette() -> None:
    points, bars, _ = _drawn("colour")
    by_label = dict((label, colour) for colour, label in points)
    assert by_label["noise"] == "#bbbbbb"
    for k in range(4):
        assert by_label[f"cluster {k}"] == PALETTE[k].lower()
    assert bars == ["#bbbbbb"] + [PALETTE[k].lower() for k in range(4)]


def test_in_greyscale_grey_with_a_shape_each() -> None:
    points, bars, shapes = _drawn("greyscale")
    by_label = dict((label, colour) for colour, label in points)
    for k in range(4):
        assert by_label[f"cluster {k}"] == GREYSCALE_PALETTE[k].lower()
    assert bars[1:] == [GREYSCALE_PALETTE[k].lower() for k in range(4)]
    # Four clusters, four shapes: value alone does not keep ten apart.
    assert len({shapes[f"cluster {k}"] for k in range(4)}) > 1


def test_the_style_cycles() -> None:
    with drawn_in("colour"):
        assert category_style(len(PALETTE)) == category_style(0) == (PALETTE[0], "o")
    with drawn_in("greyscale"):
        assert category_style(1) == (GREYSCALE_PALETTE[1], "s")
