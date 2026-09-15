"""Region-highlight report artifacts for RMSF profiles."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np

from fastmdxplora.analysis.plotting import apply_style, new_figure, save_figure
from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import StudyError

logger = get_logger("report.region_highlights")

DEFAULT_COLORS = (
    "#4E79A7",
    "#F28E2B",
    "#59A14F",
    "#E15759",
    "#B07AA1",
    "#76B7B2",
)


@dataclass(frozen=True)
class RegionHighlight:
    label: str
    start: int
    end: int
    color: str


@dataclass(frozen=True)
class PymolRenderer:
    kind: str
    command: tuple[str, ...]


PYMOL_INSTALL_NOTE = (
    "Structure rendering skipped because PyMOL was not available. Install "
    "pymol-open-source to enable 3D cartoon structure highlights "
    "(conda/micromamba install -c conda-forge pymol-open-source)."
)


def build_region_highlight_artifacts(
    *,
    project_root: Path,
    output_dir: Path,
    region_highlights: list[dict[str, Any]] | None,
) -> list[str]:
    """Create optional RMSF/structure region-highlight report artifacts."""
    if not region_highlights:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "region_highlight_manifest.json"
    artifacts = ["region_highlight_manifest.json"]
    manifest: dict[str, Any] = {
        "configured": region_highlights,
        "status": "ok",
        "artifacts": [],
        "skipped": [],
    }

    try:
        rmsf_data = _load_rmsf(project_root / "analysis" / "rmsf" / "rmsf.dat")
        regions = validate_region_highlights(region_highlights, rmsf_data[:, 0])
        rmsf_path = project_root / "analysis" / "rmsf" / "rmsf_region_highlights.png"
        _plot_rmsf_regions(rmsf_data, regions, rmsf_path)
        manifest["artifacts"].append(_rel(rmsf_path, project_root))
        artifacts.append(_rel_to(rmsf_path, output_dir))

        structure_path = output_dir / "structure_region_highlights.png"
        pymol_script_path = output_dir / "structure_region_highlights.pml"
        structure_note = _render_structure_regions(
            project_root=project_root,
            regions=regions,
            output_path=structure_path,
            script_path=pymol_script_path,
        )
        if structure_path.is_file():
            manifest["renderer"] = "PyMOL"
            manifest["artifacts"].append("report/structure_region_highlights.png")
            manifest["artifacts"].append("report/structure_region_highlights.pml")
            artifacts.append("structure_region_highlights.png")
            artifacts.append("structure_region_highlights.pml")
        else:
            manifest["skipped"].append(
                {
                    "artifact": "structure_region_highlights.png",
                    "reason": structure_note,
                }
            )

        summary_path = output_dir / "region_highlight_summary.png"
        _plot_region_summary(
            rmsf_path=rmsf_path,
            structure_path=structure_path if structure_path.is_file() else None,
            output_path=summary_path,
        )
        manifest["artifacts"].append("report/region_highlight_summary.png")
        artifacts.append("region_highlight_summary.png")
    except Exception as exc:  # noqa: BLE001
        manifest["status"] = "error"
        manifest["error"] = str(exc)
        logger.warning("region highlights: %s", exc)

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return artifacts


def validate_region_highlights(
    raw_regions: list[dict[str, Any]],
    residue_values: np.ndarray,
) -> list[RegionHighlight]:
    """Validate user-supplied region highlight dictionaries."""
    if not isinstance(raw_regions, list) or not raw_regions:
        raise StudyError("report.region_highlights must be a non-empty list.", code="report.region.invalid")

    residues = np.asarray(residue_values, dtype=float)
    if residues.size == 0:
        raise StudyError("RMSF data contains no residue values.", code="analysis.data.absent")
    min_res = int(np.nanmin(residues))
    max_res = int(np.nanmax(residues))

    regions: list[RegionHighlight] = []
    for index, item in enumerate(raw_regions, start=1):
        if not isinstance(item, dict):
            raise StudyError(f"region_highlights[{index}] must be a mapping.", code="report.region.invalid")
        try:
            start = int(item["start"])
            end = int(item["end"])
        except KeyError as exc:
            raise StudyError(
                f"region_highlights[{index}] is missing required key {exc.args[0]!r}."
            , code="report.region.invalid") from exc
        except (TypeError, ValueError) as exc:
            raise StudyError(
                f"region_highlights[{index}] start/end must be integers."
            , code="report.region.invalid") from exc

        if start < 1:
            raise StudyError(
                f"region_highlights[{index}] start must be >= 1; got {start}."
            , code="report.region.invalid")
        if end < start:
            raise StudyError(
                f"region_highlights[{index}] end must be >= start; "
                f"got start={start}, end={end}."
            , code="report.region.invalid")
        if start < min_res or end > max_res:
            raise StudyError(
                f"region_highlights[{index}] range {start}-{end} is outside "
                f"the RMSF residue range {min_res}-{max_res}."
            , code="report.region.invalid")

        label = str(item.get("label") or f"Region {index}")
        color = str(item.get("color") or DEFAULT_COLORS[(index - 1) % len(DEFAULT_COLORS)])
        regions.append(RegionHighlight(label=label, start=start, end=end, color=color))
    return regions


def _load_rmsf(path: Path) -> np.ndarray:
    if not path.is_file():
        raise StudyError(
            "region_highlights require existing RMSF output at "
            "analysis/rmsf/rmsf.dat. Run RMSF analysis first."
        , code="analysis.data.absent")
    try:
        data = np.loadtxt(path)
    except ValueError:
        data = np.genfromtxt(path, delimiter=",", names=True)
        if getattr(data, "dtype", None) is not None and data.dtype.names:
            cols = [np.asarray(data[name], dtype=float) for name in data.dtype.names[:2]]
            data = np.column_stack(cols)
    data = np.asarray(data, dtype=float)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[1] < 2:
        raise StudyError("RMSF data must have at least two columns.", code="analysis.data.absent")
    return data[:, :2]


def _plot_rmsf_regions(
    rmsf_data: np.ndarray,
    regions: list[RegionHighlight],
    output_path: Path,
) -> None:
    fig, ax = new_figure(
        title="RMSF with highlighted residue regions",
        xlabel="Residue",
        ylabel="RMSF (nm)",
        figsize=(7.2, 4.2),
    )
    x = rmsf_data[:, 0]
    y = rmsf_data[:, 1]
    ymax = float(np.nanmax(y)) if y.size else 1.0
    for region in regions:
        ax.axvspan(region.start, region.end, color=region.color, alpha=0.18, lw=0)
        ax.text(
            (region.start + region.end) / 2,
            ymax * 1.03,
            region.label,
            ha="center",
            va="bottom",
            fontsize=8,
            color=region.color,
        )
    ax.plot(x, y, linewidth=1.5, marker="o", markersize=3, color="#4E79A7")
    ax.fill_between(x, 0, y, alpha=0.10, color="#4E79A7")
    ax.set_ylim(top=max(ymax * 1.18, ymax + 0.01))
    save_figure(fig, output_path)


def detect_pymol_renderer() -> PymolRenderer | None:
    """Return a usable PyMOL renderer command, if available."""
    exe = shutil.which("pymol")
    if exe:
        return PymolRenderer(kind="command", command=(exe, "-cq"))
    try:
        __import__("pymol")
    except Exception:  # noqa: BLE001
        return None
    return PymolRenderer(kind="module", command=(sys.executable, "-m", "pymol", "-cq"))


def _render_structure_regions(
    *,
    project_root: Path,
    regions: list[RegionHighlight],
    output_path: Path,
    script_path: Path,
) -> str:
    topology_path = _find_topology(project_root)
    if topology_path is None:
        return "no topology/PDB file found for PyMOL structure rendering"
    renderer = detect_pymol_renderer()
    if renderer is None:
        return PYMOL_INSTALL_NOTE

    script = build_pymol_script(
        topology_path=topology_path,
        output_path=output_path,
        regions=regions,
    )
    script_path.write_text(script, encoding="utf-8")
    try:
        subprocess.run(
            [*renderer.command, str(script_path)],
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return f"PyMOL rendering failed: {exc}"
    _trim_image_margins(output_path)
    return "ok"


def build_pymol_script(
    *,
    topology_path: Path,
    output_path: Path,
    regions: Sequence[RegionHighlight],
) -> str:
    """Build an original PyMOL script for cartoon region highlights."""
    lines = [
        f"load {_pymol_quote(topology_path)}, prot",
        "hide everything, all",
        "show cartoon, prot",
        "color gray70, prot",
        "set cartoon_fancy_helices, 1",
        "set cartoon_smooth_loops, 1",
        "set cartoon_highlight_color, grey50",
        "set ray_trace_mode, 1",
        "set ray_opaque_background, off",
        "set antialias, 2",
        "bg_color white",
    ]
    for index, region in enumerate(regions, start=1):
        color_name = f"fastmdx_region_{index}"
        rgb = _hex_to_rgb(region.color)
        lines.append(
            f"set_color {color_name}, [{rgb[0]:.4f}, {rgb[1]:.4f}, {rgb[2]:.4f}]"
        )
        selection = f"prot and polymer.protein and resi {region.start}-{region.end}"
        lines.extend(
            [
                f"select fastmdx_sel_{index}, {selection}",
                f"color {color_name}, fastmdx_sel_{index}",
                f"show sticks, fastmdx_sel_{index} and sidechain",
            ]
        )
    lines.extend(
        [
            "orient prot",
            "zoom prot, 5",
            "ray 1800, 1200",
            f"png {_pymol_quote(output_path)}, dpi=300",
            "quit",
            "",
        ]
    )
    return "\n".join(lines)


def _plot_region_summary(
    *,
    rmsf_path: Path,
    structure_path: Path | None,
    output_path: Path,
) -> None:
    apply_style()
    if structure_path is not None:
        fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.5))
        panels = [
            ("A", "Structure region map", structure_path),
            ("B", "RMSF region highlights", rmsf_path),
        ]
    else:
        fig, axes = plt.subplots(1, 1, figsize=(7.2, 4.5))
        axes = [axes]
        panels = [("A", "RMSF region highlights", rmsf_path)]

    for ax, (letter, _title, path) in zip(np.ravel(axes), panels):
        ax.imshow(mpimg.imread(path))
        ax.axis("off")
        ax.text(
            0.01,
            0.99,
            letter,
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=12,
            fontweight="bold",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 1.5},
        )

    fig.tight_layout(pad=0.6)
    # This one composes its own panels and saves with `bbox_inches="tight"`,
    # so it does not pass through `save_figure` and would otherwise be the
    # figure in the report that carries no mark while the analyses behind it
    # all do. Stamped after tight_layout, as save_figure does, so the mark is
    # not counted as content and squeezed into the plot area.
    from fastmdxplora.analysis.plotting import stamp_current

    stamp_current(fig)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
        facecolor="white",
        edgecolor="white",
        transparent=False,
    )
    if output_path.suffix.lower() != ".svg":
        fig.savefig(
            output_path.with_suffix(".svg"),
            format="svg",
            bbox_inches="tight",
            facecolor="white",
            edgecolor="white",
            transparent=False,
        )
    plt.close(fig)


def _find_topology(project_root: Path) -> Path | None:
    analysis_manifest = project_root / "analysis" / "analysis_manifest.json"
    try:
        data = json.loads(analysis_manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    for candidate in (
        data.get("topology_input"),
        project_root / "simulation" / "topology.pdb",
        data.get("expected_topology"),
    ):
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_absolute():
            project_relative = project_root / path
            if project_relative.is_file():
                return project_relative
        if path.is_file():
            return path
    return None


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _rel_to(path: Path, root: Path) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _hex_to_rgb(color: str) -> tuple[float, float, float]:
    text = color.strip()
    named = {
        "blue": "#4E79A7",
        "orange": "#F28E2B",
        "green": "#59A14F",
        "red": "#E15759",
        "purple": "#B07AA1",
        "cyan": "#76B7B2",
    }
    text = named.get(text.lower(), text)
    if not text.startswith("#") or len(text) != 7:
        text = "#4E79A7"
    return (
        int(text[1:3], 16) / 255.0,
        int(text[3:5], 16) / 255.0,
        int(text[5:7], 16) / 255.0,
    )


def _pymol_quote(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _trim_image_margins(path: Path, *, pad: int = 30) -> None:
    """Crop mostly-white/transparent borders from a rendered image."""
    try:
        image = mpimg.imread(path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not trim image margins for %s: %s", path, exc)
        return
    arr = np.asarray(image)
    if arr.ndim < 3 or arr.shape[0] < 2 or arr.shape[1] < 2:
        return
    rgb = arr[..., :3]
    if rgb.dtype.kind in "ui":
        rgb = rgb.astype(float) / 255.0
    has_alpha = arr.shape[2] >= 4
    alpha = arr[..., 3] if has_alpha else np.ones(arr.shape[:2])
    edge_width = max(1, min(12, arr.shape[0] // 8, arr.shape[1] // 8))
    edge_samples = np.concatenate(
        [
            rgb[:edge_width, :, :].reshape(-1, 3),
            rgb[-edge_width:, :, :].reshape(-1, 3),
            rgb[:, :edge_width, :].reshape(-1, 3),
            rgb[:, -edge_width:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    background = np.median(edge_samples, axis=0)
    color_delta = np.linalg.norm(rgb - background, axis=2)
    if has_alpha and np.nanmin(alpha) < 0.98:
        non_background = alpha > 0.05
    else:
        non_background = color_delta > 0.035
    rows = np.where(non_background.any(axis=1))[0]
    cols = np.where(non_background.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return
    y0 = max(0, int(rows[0]) - pad)
    y1 = min(arr.shape[0], int(rows[-1]) + pad + 1)
    x0 = max(0, int(cols[0]) - pad)
    x1 = min(arr.shape[1], int(cols[-1]) + pad + 1)
    cropped = arr[y0:y1, x0:x1]
    plt.imsave(path, cropped)
