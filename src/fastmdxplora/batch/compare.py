"""Cross-run comparison report for a sweep / multi-system study.

After a batch of runs completes, this module reads each run's analysis
outputs and produces a single comparison report at the batch root:

    <batch_output>/comparison/
        overlay_<analysis>.png        # all runs' curves on one axes
        trend_<analysis>.png          # summary scalar vs swept parameter
        comparison_summary.csv        # one row per run, summary scalars
        comparison_report.md          # the written report tying it together

Two complementary views are produced:

  - **Overlays** — for per-frame analyses (RMSD, Rg, Q-value, total SASA),
    every run's time series is drawn on one set of axes, labelled by its
    swept value, so divergence across the sweep is visible at a glance.
  - **Trends** — each run is reduced to a summary scalar (e.g. mean RMSD
    over the production trajectory) and plotted against the swept
    parameter, turning a directory of runs into a structure-property
    relationship.

The report degrades gracefully: analyses that didn't run, runs that
errored, and sweeps over non-numeric axes are handled without failing —
the report simply includes what it can and notes the rest.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from fastmdxplora.analysis.plotting import new_figure, save_figure
from fastmdxplora.utils.logging import get_logger
from fastmdxplora.analysis.plotting import closes_what_it_opens as _closes_what_it_opens

logger = get_logger("compare")


# Per-frame scalar analyses worth overlaying. For each: the column of the
# .dat file that holds the per-frame value, a human label, and how to
# reduce the series to one summary scalar per run.
#
# Each entry: name -> (label, unit, summary_fn, summary_label)
_OVERLAY_ANALYSES: dict[str, tuple[str, str, str]] = {
    "rmsd":   ("RMSD", "nm", "mean"),
    "rg":     ("Radius of gyration", "nm", "mean"),
    "qvalue": ("Fraction of native contacts (Q)", "", "mean"),
    "sasa":   ("Total SASA", "nm²", "mean"),
}

_SUMMARY_FNS = {
    "mean": (np.nanmean, "mean"),
    "final": (lambda a: a[-1] if len(a) else np.nan, "final-frame"),
    "max": (np.nanmax, "max"),
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _load_series(dat_path: Path) -> np.ndarray | None:
    """Load a per-frame 1-D series from an analysis .dat file.

    Many analyses write a single column (rmsd, qvalue); some write a
    leading index/extra columns (rg by_chain, sasa). This takes the last
    column as the per-frame scalar of interest when 2-D, which matches
    the convention that the primary quantity is the rightmost series.
    Returns None if the file is missing or unreadable.
    """
    if not dat_path.is_file():
        return None
    try:
        arr = np.loadtxt(dat_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read %s: %s", dat_path, exc)
        return None
    arr = np.asarray(arr, dtype=float)
    if arr.ndim == 0:
        return arr.reshape(1)
    if arr.ndim == 2:
        # Take the last column as the per-frame scalar.
        arr = arr[:, -1]
    return arr.ravel()


def _run_analysis_dir(run_output_dir: Path, analysis: str) -> Path:
    """Locate <run>/analysis/<analysis>/<analysis>.dat."""
    return run_output_dir / "analysis" / analysis / f"{analysis}.dat"


# ---------------------------------------------------------------------------
# Sweep-axis handling
# ---------------------------------------------------------------------------
def _primary_sweep_axis(manifest: dict[str, Any]) -> str | None:
    """Pick the sweep axis to use as the x-axis of trend plots.

    Uses the first axis in the sweep definition. Returns None if there is
    no sweep (e.g. a multi-system batch with no parameter axes).
    """
    sweep = manifest.get("sweep") or {}
    if not sweep:
        return None
    return next(iter(sweep))


def _run_label(run: dict[str, Any], axis: str | None) -> str:
    """A short label for a run in legends — its swept value, or run id."""
    sv = run.get("sweep_values") or {}
    if axis and axis in sv:
        return f"{_short_axis(axis)}={sv[axis]}"
    if sv:
        return ", ".join(f"{_short_axis(k)}={v}" for k, v in sv.items())
    return run.get("run_id", "run")


def _short_axis(axis: str) -> str:
    """`simulation.temperature_K` -> `temperature_K` for compact labels."""
    return axis.split(".")[-1]


def _axis_numeric_value(run: dict[str, Any], axis: str) -> float | None:
    """The run's value on `axis` as a float, or None if non-numeric/missing."""
    sv = run.get("sweep_values") or {}
    if axis not in sv:
        return None
    try:
        return float(sv[axis])
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------
@_closes_what_it_opens()
def _overlay_plot(
    analysis: str,
    label: str,
    unit: str,
    series_by_run: list[tuple[str, np.ndarray]],
    out_path: Path,
) -> Path | None:
    """Overlay every run's per-frame series on one axes."""
    if not series_by_run:
        return None
    ylabel = f"{label} ({unit})" if unit else label
    fig, ax = new_figure(
        title=f"{label} across runs",
        xlabel="Frame",
        ylabel=ylabel,
    )
    for run_label, series in series_by_run:
        ax.plot(np.arange(len(series)), series, label=run_label, alpha=0.9)
    ax.legend(title=None, loc="best", ncol=1 if len(series_by_run) <= 6 else 2)
    return save_figure(fig, out_path)


@_closes_what_it_opens()
def _trend_plot(
    analysis: str,
    label: str,
    unit: str,
    summary_label: str,
    axis: str,
    points: list[tuple[float, float]],
    out_path: Path,
) -> Path | None:
    """Plot the per-run summary scalar against the swept parameter."""
    if len(points) < 2:
        return None
    points = sorted(points, key=lambda p: p[0])
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    ylabel = f"{summary_label} {label} ({unit})" if unit else f"{summary_label} {label}"
    fig, ax = new_figure(
        title=f"{label} vs {_short_axis(axis)}",
        xlabel=_short_axis(axis),
        ylabel=ylabel,
    )
    ax.plot(xs, ys, marker="o", linewidth=1.4)
    return save_figure(fig, out_path)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_comparison_report(batch_output_dir: str | Path) -> Path | None:
    """Build the cross-run comparison report for a completed batch.

    Parameters
    ----------
    batch_output_dir : path
        The batch root directory containing ``batch_manifest.json`` and a
        ``runs/`` directory.

    Returns
    -------
    Path or None
        The path to the comparison report directory, or None if there was
        nothing to compare (fewer than two successful runs, or no analysis
        outputs were found).
    """
    root = Path(batch_output_dir)
    manifest_path = root / "batch_manifest.json"
    if not manifest_path.is_file():
        logger.debug("No batch_manifest.json at %s — skipping comparison.", root)
        return None

    with manifest_path.open(encoding="utf-8") as fh:
        manifest = json.load(fh)

    runs = [r for r in manifest.get("runs", []) if r.get("status") == "ok"]
    if len(runs) < 2:
        logger.info(
            "Comparison report needs ≥2 successful runs (found %d) — skipping.",
            len(runs),
        )
        return None

    axis = _primary_sweep_axis(manifest)
    cmp_dir = root / "comparison"

    overlay_figs: dict[str, Path] = {}
    trend_figs: dict[str, Path] = {}
    summary_scalar_keys: list[str] = []

    # First pass: per-run summary scalars (for the CSV) keyed by analysis.
    per_run_scalars: dict[str, dict[str, float]] = {}
    for run in runs:
        rid = run["run_id"]
        per_run_scalars[rid] = {}
        run_out = Path(run["output_dir"])
        for analysis, (_label, _unit, summary_kind) in _OVERLAY_ANALYSES.items():
            series = _load_series(_run_analysis_dir(run_out, analysis))
            if series is None or not len(series):
                continue
            fn, _flabel = _SUMMARY_FNS[summary_kind]
            try:
                per_run_scalars[rid][analysis] = float(fn(series))
            except Exception:  # noqa: BLE001
                continue

    # Which analyses actually have data in ≥2 runs?
    analyses_present = [
        a for a in _OVERLAY_ANALYSES
        if sum(1 for rid in per_run_scalars if a in per_run_scalars[rid]) >= 2
    ]
    if not analyses_present:
        logger.info("No comparable analysis outputs found — skipping comparison.")
        return None

    # There is something to compare — now create the report directory.
    cmp_dir.mkdir(parents=True, exist_ok=True)

    # Overlays + trends per present analysis.
    for analysis in analyses_present:
        label, unit, summary_kind = _OVERLAY_ANALYSES[analysis]
        _flabel = _SUMMARY_FNS[summary_kind][1]

        # Overlay: load each run's series again (cheap; keeps memory low).
        series_by_run: list[tuple[str, np.ndarray]] = []
        trend_points: list[tuple[float, float]] = []
        for run in runs:
            rid = run["run_id"]
            run_out = Path(run["output_dir"])
            series = _load_series(_run_analysis_dir(run_out, analysis))
            if series is None or not len(series):
                continue
            series_by_run.append((_run_label(run, axis), series))
            if axis is not None:
                xval = _axis_numeric_value(run, axis)
                if xval is not None and rid in per_run_scalars \
                        and analysis in per_run_scalars[rid]:
                    trend_points.append((xval, per_run_scalars[rid][analysis]))

        fig = _overlay_plot(
            analysis, label, unit, series_by_run,
            cmp_dir / f"overlay_{analysis}.png",
        )
        if fig is not None:
            overlay_figs[analysis] = fig

        if axis is not None:
            tfig = _trend_plot(
                analysis, label, unit, _flabel, axis, trend_points,
                cmp_dir / f"trend_{analysis}.png",
            )
            if tfig is not None:
                trend_figs[analysis] = tfig

    # Safety net: if figure generation produced nothing (e.g. every run
    # had a single frame), don't leave an empty directory behind.
    if not overlay_figs and not trend_figs:
        logger.info("No comparable analysis outputs found — skipping comparison.")
        try:
            cmp_dir.rmdir()
        except OSError:
            pass
        return None

    # Build the summary CSV.
    summary_scalar_keys = analyses_present
    csv_path = cmp_dir / "comparison_summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        header = ["run_id", "system"]
        if axis is not None:
            header.append(_short_axis(axis))
        header += [
            f"{a}_{_SUMMARY_FNS[_OVERLAY_ANALYSES[a][2]][1]}"
            for a in summary_scalar_keys
        ]
        writer.writerow(header)
        for run in runs:
            rid = run["run_id"]
            row: list[Any] = [rid, run.get("system", "")]
            if axis is not None:
                sv = run.get("sweep_values") or {}
                row.append(sv.get(axis, ""))
            for a in summary_scalar_keys:
                v = per_run_scalars.get(rid, {}).get(a)
                row.append(f"{v:.6g}" if v is not None else "")
            writer.writerow(row)

    # Build the Markdown report.
    md_path = cmp_dir / "comparison_report.md"
    _write_markdown(
        md_path, manifest, runs, axis,
        overlay_figs, trend_figs, summary_scalar_keys,
        per_run_scalars, csv_path,
    )

    logger.info("Wrote cross-run comparison report: %s", cmp_dir)
    return cmp_dir


def _umbrella_result(md_path: Path) -> tuple[Path | None, str | None] | None:
    """The study's free energy, and why there is none where there is none.

    Read from `pmf.json` rather than inferred from a figure being present.
    Checking only for the file meant a study that refused -- windows too far
    apart to overlap -- kept the previous run's plot and the report showed it
    as this study's result. A refusal and a figure are different outcomes,
    and only one of them has a picture.

    Returns `None` where these runs are not an umbrella study at all.
    """
    study = md_path.parent.parent
    record_path = study / "pmf.json"
    if not record_path.is_file():
        return None
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    refused = record.get("refused")
    if refused:
        return None, str(refused)
    drawn = study / "free_energy" / "pmf" / "pmf.png"
    return (drawn if drawn.is_file() else None), None


def _umbrella_preamble(
    free_energy: Path | None, refused: str | None = None
) -> list[str]:
    """What the study measured, ahead of what its windows happened to do."""
    lines = [
        "## Free energy",
        "",
        "These runs are the windows of an umbrella study: one experiment "
        "sampled in pieces, each piece held at a different position along "
        "the coordinate by a spring. The result is the free energy, not the "
        "comparison that follows it.",
        "",
    ]
    if refused:
        # The refusal and nothing after it. A generic remedy was added here
        # -- "move them closer or soften the restraint" -- and the refusal
        # it followed said the opposite: four of five windows had slid off
        # their restraints, so a softer one makes the gap worse. The module
        # that computed the failure knows which failure it was; a sentence
        # written without that knowledge can only guess, and a guess printed
        # beside a diagnosis reads as though it carried equal weight.
        lines += ["**No free energy was computed for this study.** " + refused, ""]
    elif free_energy is not None:
        relative = Path("..") / free_energy.relative_to(
            free_energy.parent.parent.parent)
        lines += [f"![Potential of mean force]({relative.as_posix()})", ""]
    return lines + [
        "The overlays and the table below describe each window\'s own "
        "restrained trajectory. They differ from one another because the "
        "restraints differ, which is the method working rather than a "
        "disagreement between runs -- a quantity that varies with the "
        "coordinate will vary across windows by construction, and its mean "
        "over a restrained window is not a measurement of the system.",
        "",
    ]


def _write_markdown(
    md_path: Path,
    manifest: dict[str, Any],
    runs: list[dict[str, Any]],
    axis: str | None,
    overlay_figs: dict[str, Path],
    trend_figs: dict[str, Path],
    summary_keys: list[str],
    per_run_scalars: dict[str, dict[str, float]],
    csv_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# Cross-run comparison report")
    lines.append("")

    # An umbrella study is one experiment sampled in pieces, not several
    # experiments. Its windows differ because each is held at a different
    # position by a spring, so a table of their mean RMSD and radius of
    # gyration is a table of the restraint schedule: on a stretched
    # tripeptide the radius rose monotonically with window index, 0.334 to
    # 0.369, and the report presented that as five runs disagreeing. Said
    # first, because a reader who takes the table at face value has been
    # misled by the time they reach any caveat below it.
    umbrella = _umbrella_result(md_path)
    if umbrella is not None:
        lines.extend(_umbrella_preamble(*umbrella))

    n = len(runs)
    sweep = manifest.get("sweep") or {}
    if sweep:
        axes_desc = ", ".join(
            f"`{_short_axis(k)}` ({len(v)} values)" for k, v in sweep.items()
        )
        lines.append(
            f"This study compared **{n} successful runs** over {axes_desc}."
        )
    else:
        lines.append(f"This study compared **{n} successful runs**.")
    lines.append("")

    if axis is not None:
        lines.append(
            f"Trend plots use **`{_short_axis(axis)}`** as the independent "
            f"variable. Overlay plots show every run's per-frame trace on a "
            f"common axes."
        )
        lines.append("")

    # Per-analysis sections.
    for analysis in summary_keys:
        label, unit, summary_kind = _OVERLAY_ANALYSES[analysis]
        flabel = _SUMMARY_FNS[summary_kind][1]
        lines.append(f"## {label}")
        lines.append("")
        if analysis in overlay_figs:
            lines.append(f"![{label} overlay]({overlay_figs[analysis].name})")
            lines.append("")
        if analysis in trend_figs:
            lines.append(f"![{label} trend]({trend_figs[analysis].name})")
            lines.append("")
            # A one-line quantitative takeaway from the trend.
            takeaway = _trend_takeaway(
                analysis, label, flabel, unit, axis, runs, per_run_scalars,
            )
            if takeaway:
                lines.append(takeaway)
                lines.append("")

    # Summary table (inline, plus CSV pointer).
    lines.append("## Summary")
    lines.append("")
    header = ["Run"]
    if axis is not None:
        header.append(_short_axis(axis))
    header += [f"{flabel_of(a)} {_OVERLAY_ANALYSES[a][0]}" for a in summary_keys]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for run in runs:
        rid = run["run_id"]
        cells = [rid]
        if axis is not None:
            sv = run.get("sweep_values") or {}
            cells.append(str(sv.get(axis, "")))
        for a in summary_keys:
            v = per_run_scalars.get(rid, {}).get(a)
            cells.append(f"{v:.4g}" if v is not None else "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(f"Full table: `{csv_path.name}`.")
    lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def flabel_of(analysis: str) -> str:
    """Summary-function label for an analysis (used in table headers)."""
    return _SUMMARY_FNS[_OVERLAY_ANALYSES[analysis][2]][1]


def _trend_takeaway(
    analysis: str,
    label: str,
    flabel: str,
    unit: str,
    axis: str | None,
    runs: list[dict[str, Any]],
    per_run_scalars: dict[str, dict[str, float]],
) -> str | None:
    """One-sentence quantitative summary of how a property varies."""
    if axis is None:
        return None
    pts = []
    for run in runs:
        x = _axis_numeric_value(run, axis)
        y = per_run_scalars.get(run["run_id"], {}).get(analysis)
        if x is not None and y is not None:
            pts.append((x, y))
    if len(pts) < 2:
        return None
    pts.sort()
    (x_lo, y_lo), (x_hi, y_hi) = pts[0], pts[-1]
    direction = "increases" if y_hi > y_lo else "decreases" if y_hi < y_lo else "is flat"
    unit_str = f" {unit}" if unit else ""
    return (
        f"Across `{_short_axis(axis)}` {x_lo:g} → {x_hi:g}, "
        f"{flabel} {label.lower()} {direction} "
        f"({y_lo:.3g} → {y_hi:.3g}{unit_str})."
    )
