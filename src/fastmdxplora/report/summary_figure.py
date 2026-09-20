"""Build a compact multi-panel analysis summary figure."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from fastmdxplora.analysis.plotting import apply_style
from fastmdxplora.utils.logging import get_logger

logger = get_logger("report.summary_figure")


@dataclass(frozen=True)
class SummaryPanel:
    title: str
    source: Path


# One panel per analysis, in the order a reader takes them, up to twelve.
# The list used to name twelve specific files, two of which exist only in
# per-residue SASA mode, so a study with twelve analyses got a ten-panel
# summary. Each analysis contributes its first figure; a study with more
# than twelve fills the grid with the first twelve here.
EXPECTED_PANELS: tuple[tuple[str, str], ...] = (
    ("RMSD over frames", "analysis/rmsd/rmsd.png"),
    ("RMSF by residue", "analysis/rmsf/rmsf.png"),
    ("Radius of gyration", "analysis/rg/rg.png"),
    ("Hydrogen bonds", "analysis/hbonds/hbonds.png"),
    ("Secondary structure", "analysis/ss/ss.png"),
    ("Total SASA", "analysis/sasa/sasa.png"),
    ("Backbone dihedrals", "analysis/dihedrals/dihedrals.png"),
    ("Native contacts (Q)", "analysis/qvalue/qvalue.png"),
    ("Order parameters", "analysis/order_parameters/order_parameters.png"),
    ("Thermodynamics", "analysis/thermodynamics/thermodynamics.png"),
    ("End-to-end distance", "analysis/end_to_end/end_to_end.png"),
    ("Moments of inertia", "analysis/moments_of_inertia/moments_of_inertia.png"),
    ("Cluster timeline", "analysis/cluster/cluster_kmeans.png"),
    ("PCA / dimensionality reduction", "analysis/dimred/dimred_pca.png"),
    ("SASA heatmap", "analysis/sasa/sasa_heatmap.png"),
    ("Average SASA by residue", "analysis/sasa/sasa_by_residue.png"),
    ("Cluster populations", "analysis/cluster/cluster_kmeans_counts.png"),
    ("Hierarchical dendrogram", "analysis/cluster/cluster_hierarchical_dendrogram.png"),
)
MAX_PANELS = 12



def build_analysis_summary_figure(
    *,
    project_root: Path,
    output_dir: Path,
) -> list[str]:
    """Create ``analysis_summary.png`` from existing analysis figures.

    Returns artifact paths relative to ``output_dir``. Missing expected
    source figures are recorded in ``analysis_summary_manifest.json``.
    """
    analysis_dir = project_root / "analysis"
    if not analysis_dir.is_dir():
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    panels: list[SummaryPanel] = []
    skipped: list[dict[str, str]] = []
    for title, rel_source in EXPECTED_PANELS:
        if len(panels) >= MAX_PANELS:
            break
        source = project_root / rel_source
        if source.is_file():
            panels.append(SummaryPanel(title=title, source=source))
        else:
            skipped.append(
                {
                    "title": title,
                    "source": rel_source,
                    "reason": "source figure not present",
                }
            )

    manifest_path = output_dir / "analysis_summary_manifest.json"
    manifest = {
        "artifact": "analysis_summary.png",
        "included": [
            {
                "panel": chr(ord("A") + index),
                "title": panel.title,
                "source": panel.source.relative_to(project_root).as_posix(),
            }
            for index, panel in enumerate(panels)
        ],
        "skipped": skipped,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    artifacts = ["analysis_summary_manifest.json"]
    if not panels:
        logger.debug("summary figure: no source figures available")
        return artifacts

    apply_style()
    cols = 3 if len(panels) > 2 else len(panels)
    rows = math.ceil(len(panels) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.0, rows * 3.0))
    flat_axes = list(axes.ravel()) if hasattr(axes, "ravel") else [axes]

    for index, (ax, panel) in enumerate(zip(flat_axes, panels)):
        try:
            image = mpimg.imread(panel.source)
        except Exception as exc:  # noqa: BLE001
            logger.warning("summary figure: could not read %s: %s", panel.source, exc)
            ax.axis("off")
            ax.text(0.5, 0.5, "Unavailable", ha="center", va="center")
            continue
        ax.imshow(image)
        ax.axis("off")
        ax.set_title(panel.title, fontsize=9, fontweight="normal", pad=4)
        ax.text(
            0.01,
            0.99,
            chr(ord("A") + index),
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=12,
            fontweight="bold",
            color="black",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 1.5},
        )

    for ax in flat_axes[len(panels) :]:
        ax.axis("off")

    fig.tight_layout(pad=0.6)
    # Composed from the analyses' own panels and saved directly, so the
    # ambient mark has to be applied by hand here -- see the note in
    # `report/region_highlights.py`.
    from fastmdxplora.analysis.plotting import stamp_current

    stamp_current(fig)
    figure_path = output_dir / "analysis_summary.png"
    fig.savefig(
        figure_path,
        dpi=300,
        bbox_inches="tight",
        facecolor="white",
        edgecolor="white",
        transparent=False,
    )
    svg_path = figure_path.with_suffix(".svg")
    fig.savefig(
        svg_path,
        format="svg",
        bbox_inches="tight",
        facecolor="white",
        edgecolor="white",
        transparent=False,
    )
    plt.close(fig)
    artifacts.insert(0, "analysis_summary.svg")
    artifacts.insert(0, "analysis_summary.png")
    return artifacts
