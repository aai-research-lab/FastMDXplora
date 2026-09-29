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
from fastmdxplora.batch.aggregate import SEED_AXES, member_directory, read_member_findings
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

    Uses the first axis in the sweep definition that is not a random seed.
    Returns None if there is no such axis (a multi-system batch with no
    parameter axes, or replicas that differ only by seed): a "trend" of a
    mean against the seed that drew the velocities is noise read as a
    relationship, and replicas are compared in their own section instead.
    """
    sweep = manifest.get("sweep") or {}
    return next((axis for axis in sweep if axis not in SEED_AXES), None)


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
    series_by_run: list[tuple[str, np.ndarray, np.ndarray | None]],
    out_path: Path,
) -> Path | None:
    """Overlay every run's series on one axes: against time where every run
    recorded its clock, so runs saved at different intervals line up, and
    against the frame otherwise."""
    if not series_by_run:
        return None
    timed = all(times is not None for _, _, times in series_by_run)
    ylabel = f"{label} ({unit})" if unit else label
    fig, ax = new_figure(
        title=f"{label} across runs",
        xlabel="Time (ns)" if timed else "Frame",
        ylabel=ylabel,
    )
    for run_label, series, times in series_by_run:
        ax.plot(times if timed else np.arange(len(series)), series, label=run_label, alpha=0.9)
    ax.legend(title=None, loc="best", ncol=1 if len(series_by_run) <= 6 else 2)
    return save_figure(fig, out_path)


@_closes_what_it_opens()
def _trend_plot(
    analysis: str,
    label: str,
    unit: str,
    summary_label: str,
    axis: str,
    points: list[tuple[float, float, float | None]],
    out_path: Path,
) -> Path | None:
    """Plot each run's mean against the swept parameter, with its standard
    error where the run recorded one: a difference between two points is
    only a trend where it is larger than the bars."""
    if len(points) < 2:
        return None
    points = sorted(points, key=lambda p: p[0])
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    errors = [p[2] if p[2] is not None else 0.0 for p in points]
    ylabel = f"{summary_label} {label} ({unit})" if unit else f"{summary_label} {label}"
    fig, ax = new_figure(
        title=f"{label} vs {_short_axis(axis)}",
        xlabel=_short_axis(axis),
        ylabel=ylabel,
    )
    ax.errorbar(xs, ys, yerr=errors if any(errors) else None, marker="o",
                linewidth=1.4, capsize=3)
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

    # First pass: each run's mean of each analysis. The one the analysis
    # recorded, over the frames after equilibration and with its standard
    # error; the mean of the whole series only where it recorded none. This
    # took the mean of every frame, equilibration included, and compared
    # runs on it without an error, so a trend was reported wherever two
    # numbers differed.
    per_run_scalars: dict[str, dict[str, float]] = {}
    per_run_means: dict[str, dict[str, dict[str, Any]]] = {}
    for run in runs:
        rid = run["run_id"]
        per_run_scalars[rid] = {}
        per_run_means[rid] = {}
        run_out = member_directory(root, run)
        recorded = read_member_findings(run_out)
        for analysis, (_label, _unit, summary_kind) in _OVERLAY_ANALYSES.items():
            mean = _recorded_mean(recorded.get(analysis))
            if mean is None:
                series = _load_series(_run_analysis_dir(run_out, analysis))
                if series is None or not len(series):
                    continue
                fn, _flabel = _SUMMARY_FNS[summary_kind]
                try:
                    mean = {"mean": float(fn(series)), "error": None, "discard": None,
                            "qualified": None, "recorded": False}
                except Exception:  # noqa: BLE001
                    continue
            per_run_means[rid][analysis] = mean
            per_run_scalars[rid][analysis] = mean["mean"]

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
            run_out = member_directory(root, run)
            series = _load_series(_run_analysis_dir(run_out, analysis))
            if series is None or not len(series):
                continue
            series_by_run.append((_run_label(run, axis), series, _times(run_out, len(series))))
            if axis is not None:
                xval = _axis_numeric_value(run, axis)
                if xval is not None and analysis in per_run_means.get(rid, {}):
                    mean = per_run_means[rid][analysis]
                    trend_points.append((xval, mean["mean"], mean["error"]))

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
        for a in summary_scalar_keys:
            header += [f"{a}_{_SUMMARY_FNS[_OVERLAY_ANALYSES[a][2]][1]}",
                       f"{a}_standard_error", f"{a}_frames_discarded", f"{a}_over"]
        writer.writerow(header)
        for run in runs:
            rid = run["run_id"]
            row: list[Any] = [rid, run.get("system", "")]
            if axis is not None:
                sv = run.get("sweep_values") or {}
                row.append(sv.get(axis, ""))
            for a in summary_scalar_keys:
                mean = per_run_means.get(rid, {}).get(a)
                if mean is None:
                    row += ["", "", "", ""]
                    continue
                row += [f"{mean['mean']:.6g}",
                        "" if mean["error"] is None else f"{mean['error']:.6g}",
                        "" if mean["discard"] is None else mean["discard"],
                        _over(mean)]
            writer.writerow(row)

    # Build the Markdown report.
    md_path = cmp_dir / "comparison_report.md"
    _write_markdown(
        md_path, manifest, runs, axis,
        overlay_figs, trend_figs, summary_scalar_keys,
        per_run_means, csv_path, root,
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
    per_run_means: dict[str, dict[str, dict[str, Any]]],
    csv_path: Path,
    root: Path | None = None,
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
                analysis, label, flabel, unit, axis, runs, per_run_means,
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
    header += [f"{flabel_of(a)} {_OVERLAY_ANALYSES[a][0]}"
               + (f" ({_OVERLAY_ANALYSES[a][1]})" if _OVERLAY_ANALYSES[a][1] else "")
               for a in summary_keys]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    whole = qualified = False
    for run in runs:
        rid = run["run_id"]
        cells = [rid]
        if axis is not None:
            sv = run.get("sweep_values") or {}
            cells.append(str(sv.get(axis, "")))
        for a in summary_keys:
            mean = per_run_means.get(rid, {}).get(a)
            if mean is None:
                cells.append("")
                continue
            cell = f"{mean['mean']:.4g}"
            if mean["error"] is not None:
                cell += f" ± {mean['error']:.2g}"
            if not mean["recorded"]:
                cell += " (all frames)"
                whole = True
            if mean["qualified"]:
                cell += " \\*"
                qualified = True
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Each mean is over the frames after equilibration, with its standard "
                 "error, as its run's analysis recorded it."
                 + (" Where a run recorded none, the mean of every frame is given and "
                    "marked (all frames)." if whole else ""))
    if qualified:
        lines += ["", "\\* Not a measurement: the run's analysis found the series too "
                  "short, or with too few independent samples, for its mean to be one."]
    lines.append("")
    lines.append(f"Full table: `{csv_path.name}`.")
    lines.append("")
    if root is not None:
        lines.extend(_across_the_replicas(root))

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
    per_run_means: dict[str, dict[str, dict[str, Any]]],
) -> str | None:
    """How a property differs between the ends of the sweep, and whether
    the difference is larger than its error: the ends' two standard errors
    combined, twice over. A difference within that is not called a trend."""
    if axis is None:
        return None
    pts = []
    for run in runs:
        x = _axis_numeric_value(run, axis)
        mean = per_run_means.get(run["run_id"], {}).get(analysis)
        if x is not None and mean is not None:
            pts.append((x, mean["mean"], mean["error"]))
    if len(pts) < 2:
        return None
    pts.sort(key=lambda p: p[0])
    (x_lo, y_lo, e_lo), (x_hi, y_hi, e_hi) = pts[0], pts[-1]
    unit_str = f" {unit}" if unit else ""
    where = f"From `{_short_axis(axis)}` {x_lo:g} to {x_hi:g}, the {flabel} {label.lower()}"
    change = y_hi - y_lo
    if e_lo is None or e_hi is None:
        return (f"{where} goes from {y_lo:.3g} to {y_hi:.3g}{unit_str}; no standard error "
                "was recorded to judge the change by.")
    bound = 2.0 * float(np.hypot(e_lo, e_hi))
    ends = f"{y_lo:.3g} ± {e_lo:.2g} to {y_hi:.3g} ± {e_hi:.2g}{unit_str}"
    if abs(change) > bound:
        direction = "increases" if change > 0 else "decreases"
        return (f"{where} {direction} from {ends}, a change of {abs(change):.2g}{unit_str}, "
                "more than twice its error.")
    return (f"{where} goes from {ends}: the change, {abs(change):.2g}{unit_str}, is within "
            "twice its error, so these runs do not tell the two ends apart.")


def _times(run_out: Path, n: int) -> np.ndarray | None:
    """Each analysed frame's time in ns, as the analysis phase loaded the
    trajectory (strided, then sliced from `first`), or None where the run
    recorded no saving interval."""
    try:
        manifest = json.loads((run_out / "analysis" / "analysis_manifest.json")
                              .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    loaded = manifest.get("load_kwargs") if isinstance(manifest, dict) else None
    loaded = loaded if isinstance(loaded, dict) else {}
    interval = loaded.get("saving_interval_ps")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
        return None
    stride = loaded.get("stride") if isinstance(loaded.get("stride"), int) else 1
    first = loaded.get("first") if isinstance(loaded.get("first"), int) else 0
    frames = (first + np.arange(n)) * max(stride, 1)
    return (frames + 1) * float(interval) / 1000.0


def _recorded_mean(findings: dict[str, Any] | None) -> dict[str, Any] | None:
    """The mean an analysis recorded (`findings.mean`), or None where it
    recorded none."""
    record = (findings or {}).get("mean") if isinstance(findings, dict) else None
    if not isinstance(record, dict):
        return None
    value = record.get("mean")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        return None
    error = record.get("standard_error")
    error = (float(error) if isinstance(error, (int, float)) and not isinstance(error, bool)
             and np.isfinite(error) else None)
    discard = record.get("discard")
    return {"mean": float(value), "error": error,
            "discard": int(discard) if isinstance(discard, (int, float)) else None,
            "qualified": record.get("not_a_measurement") or None, "recorded": True}


def _over(mean: dict[str, Any]) -> str:
    """What a mean in the table is over, for the CSV."""
    return "after equilibration" if mean["recorded"] else "all frames"


def _across_the_replicas(root: Path) -> list[str]:
    """For replicas, which differ only by seed: the spread of their means
    against the error each run estimated for itself, which is the only check
    a single run's error gets (`fastmdxplora.batch.aggregate`)."""
    from fastmdxplora.batch.aggregate import aggregate_members

    try:
        summary = aggregate_members(root)
    except Exception:  # noqa: BLE001 - the comparison stands without it
        return []
    if summary.get("refused") or not summary.get("replicas"):
        return []
    lines = ["## Across the replicas", "",
             f"These runs are replicas: {summary.get('why')}. That spread is set "
             "here against the error each run estimated for itself.", "",
             "| Analysis | mean of the means | spread of the means | error each run estimated |",
             "|---|---|---|---|"]
    said = []
    for name, entry in summary.get("analyses", {}).items():
        predicted = entry.get("predicted_standard_error")
        lines.append(
            f"| {name} | {entry['mean_of_means']:.4g} | {entry['spread_of_means']:.2g} | "
            + ("" if predicted is None else f"{predicted:.2g}") + " |")
        if entry.get("calibration"):
            said.append(f"**{name}.** {entry['calibration']}")
    lines.append("")
    for sentence in said:
        lines += [sentence, ""]
    return lines
