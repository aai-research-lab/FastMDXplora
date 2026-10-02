"""The report as a document, in the page.

The report phase writes ``report/report.md`` -- the study's methods,
results and convergence, with the sampling caveats in the prose where the
numbers are. It also writes ``not_produced.json`` for anything it could
not make and why: a PDF without WeasyPrint, say.

This turns the first into HTML for the centre column and hands the second
over as notices, so a person reads the report where they ran the study
rather than opening a file to find out what it said.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["methods_payload", "report_payload"]


def report_payload(root: Path | str) -> dict[str, Any]:
    """What the Report page needs, or why there is nothing to show. For a
    study of several runs, the comparison across them."""
    base = Path(root)
    if (base / "batch_manifest.json").is_file():
        return _study_of_runs_payload(base)
    report_dir = base / "report"
    source = report_dir / "report.md"
    if not source.is_file():
        return {"ok": False, "reason": "no report yet",
                "html": "", "not_produced": [], "downloads": {}}

    text = source.read_text(encoding="utf-8", errors="replace")
    # The markdown library lives in the `pdf` extra, not the base
    # install, and a base install still has a report to show. With it,
    # the report is rendered; without it, the same text is shown as it
    # was written, which is legible Markdown. CI installs the base
    # package and found this: five failures where a Mac with the extra
    # had passed.
    html, rendered = render_markdown(text)

    not_produced: list[dict[str, str]] = []
    record = report_dir / "not_produced.json"
    if record.is_file():
        try:
            rows = json.loads(record.read_text(encoding="utf-8"))
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get("artifact"):
                    not_produced.append({
                        "artifact": str(row["artifact"]),
                        "reason": str(row.get("reason") or ""),
                    })
        except (OSError, ValueError):
            pass

    # Only the downloads that exist. A button for a file that was not
    # produced is the not_produced notice's job, not a dead link's.
    downloads: dict[str, str] = {}
    for key, name in (("pdf", "report.pdf"), ("slides", "slides.pptx"),
                      ("bundle", "project_bundle.zip"),
                      ("markdown", "report.md"), ("summary", "analysis_summary.png")):
        if (report_dir / name).is_file():
            downloads[key] = f"/artifacts/report/{name}?download=1"

    # What made each analysis's figures, for a chip under each figure the
    # report shows, as the Analysis page has one (figure_provenance.py).
    try:
        from fastmdxplora.gui.figure_provenance import figure_provenance

        provenance = figure_provenance(base)
    except Exception:  # noqa: BLE001 - the report stands without its chips
        provenance = {}

    return {
        "ok": True,
        "html": html,
        "not_produced": not_produced,
        "downloads": downloads,
        "generated": _generated_line(text),
        "rendered": rendered,
        "figure_provenance": provenance,
    }


def methods_payload(root: Path | str, *, may_read: Any = None) -> dict[str, Any]:
    """The study's methods paragraphs, to read and to copy into a
    manuscript: as the report gives them, written from the records as they
    stand, so they are there before the report is and follow an extension.

    ``html`` is for the page; ``plain`` is for the clipboard, without the
    Markdown, since a manuscript is not Markdown. ``may_read`` is a hosted
    GUI's rule for what is read outside the study.
    """
    import re

    base = Path(root)
    if (base / "batch_manifest.json").is_file():
        return {"ok": False, "reason": "a study of several runs: each run's report "
                                       "gives its own methods"}
    if not any((base / name).exists() for name in ("manifest.json", "simulation", "setup")):
        return {"ok": False, "reason": "nothing has run yet"}
    from fastmdxplora.report.document import methods_prose

    try:
        prose = methods_prose(base, may_read=may_read)
    except PermissionError as exc:
        return {"ok": False, "reason": str(exc)}
    if not prose:
        return {"ok": False, "reason": "nothing recorded yet"}
    plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", prose).replace("`", "")
    return {"ok": True, "html": render_markdown(prose)[0], "plain": plain}


def _generated_line(text: str) -> str:
    """The `_Generated: …_` line near the top, if the report carries one."""
    for line in text.splitlines()[:8]:
        stripped = line.strip().strip("_")
        if stripped.lower().startswith("generated:"):
            return stripped[len("generated:"):].strip()
    return ""


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def render_markdown(text: str) -> tuple[str, str]:
    """Markdown to HTML, or the text as written where the library is not
    installed. One renderer for the report page and the Files tab's
    preview, so a .md file reads the same on both and there is nothing
    vendored: the browser has no Markdown of its own, and the Python
    library is the one this software already trusts for its report.
    Raw HTML in the text is shown as written, and a link goes only to the
    web, to mail or within the study: see `fastmdxplora.report.markdown_html`."""
    from fastmdxplora.report.markdown_html import markdown_as_html

    try:
        return markdown_as_html(text), "html"
    except ImportError:
        return '<pre class="report-plain">' + _escape(text) + "</pre>", "plain"


def _study_of_runs_payload(base: Path) -> dict[str, Any]:
    """The report of a study of several runs: the comparison the batch
    writes once every run has completed, with its figures served from
    comparison/; before that, what the completed runs say so far, and how
    many are still to come. Neither is a run's own report, which each run
    keeps under runs/<id>/report/."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    runs = runs_of_a_study(base) or []
    completed = [r for r in runs if r["state"] == "completed"]
    pending = len(runs) - len(completed)
    comparison = base / "comparison" / "comparison_report.md"
    if comparison.is_file():
        text = comparison.read_text(encoding="utf-8", errors="replace")
        html, rendered = render_markdown(text)
        downloads = {}
        for key, name in (("markdown", "comparison_report.md"),
                          ("summary", "comparison_summary.csv")):
            if (comparison.parent / name).is_file():
                downloads[key] = f"/artifacts/comparison/{name}?download=1"
        return {"ok": True, "html": html, "not_produced": [], "downloads": downloads,
                "generated": _generated_line(text), "rendered": rendered,
                "figures_under": "comparison", "runs": len(runs), "pending": pending,
                "figure_provenance": _comparison_provenance(comparison.parent)}
    if not completed:
        return {"ok": False, "reason": f"none of {len(runs)} runs completed yet",
                "html": "", "not_produced": [], "downloads": {},
                "runs": len(runs), "pending": pending}
    html, rendered = render_markdown(_so_far(base, runs, completed))
    return {"ok": True, "html": html, "not_produced": [], "downloads": {},
            "generated": "", "rendered": rendered, "figures_under": "comparison",
            "runs": len(runs), "pending": pending}


def _comparison_provenance(folder: Path) -> dict[str, dict[str, Any]]:
    """What each comparison figure was plotted from, by its file's stem, as
    the comparison recorded it (`figures.json`): the runs, the release that
    analysed each, and the release that plotted them. Empty for a comparison
    plotted before it was recorded."""
    from fastmdxplora import __version__

    try:
        record = json.loads((folder / "figures.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    figures = record.get("figures") if isinstance(record, dict) else None
    if not isinstance(figures, dict):
        return {}
    return {str(name): {**figure, "version": record.get("version"),
                        "host": record.get("host"), "made": record.get("made"),
                        "this_version": str(__version__)}
            for name, figure in figures.items() if isinstance(figure, dict)}


def _so_far(base: Path, runs: list[dict[str, Any]], completed: list[dict[str, Any]]) -> str:
    """A table of the means each completed run determined, one row per run and
    one column per measure with a mean, read from the runs' own findings.
    The comparison across them, with its figures, comes when the last run
    finishes.

    Each mean is given with its standard error and unit, and a mean the
    analysis said is not a measurement is marked as one. The table read an
    `uncertainty` and a `unit` that no analysis writes (its test's fixture
    wrote them), so every mean stood without its error, and a series too
    short to measure stood among them as though it were a result."""
    from fastmdxplora.batch.aggregate import read_member_findings
    from fastmdxplora.gui.report_dashboard import _format_metric_value, unit_of
    from fastmdxplora.statistics import with_its_error

    axes: list[str] = sorted({axis for r in runs for axis in (r.get("values") or {})})
    short = {axis: axis.split(".")[-1] for axis in axes}
    means: dict[str, dict[str, Any]] = {}
    measures: list[str] = []
    for run in completed:
        found = read_member_findings(Path(run["path"]))
        row = {}
        for analysis, findings in found.items():
            mean = findings.get("mean") if isinstance(findings, dict) else None
            if isinstance(mean, dict) and isinstance(mean.get("mean"), (int, float)):
                row[analysis] = mean
                if analysis not in measures:
                    measures.append(analysis)
        means[run["run_id"]] = row
    pending = len(runs) - len(completed)
    lines = [f"# {base.name}: {len(completed)} of {len(runs)} runs completed", ""]
    if pending:
        lines += [f"_{pending} still to run. The comparison across all of them, with its "
                  "figures, is written when the last one finishes._", ""]
    if not measures:
        lines += ["No run has recorded a mean yet.", ""]
        return "\n".join(lines)
    units = {m: next((unit_of(m, row[m]) for row in means.values() if m in row), "")
             for m in measures}
    head = [short[a] for a in axes] + [
        f"{m} mean" + (f" ({units[m]})" if units[m] else "") for m in measures]
    lines.append("| run | " + " | ".join(head) + " |")
    lines.append("|---|" + "---|" * len(head))
    qualified = False
    for run in completed:
        cells = [str((run.get("values") or {}).get(axis, "")) for axis in axes]
        for measure in measures:
            record = means[run["run_id"]].get(measure)
            if record is None:
                cells.append("")
                continue
            error = record.get("standard_error")
            cell = (with_its_error(record["mean"], error)
                    if isinstance(error, (int, float)) and not isinstance(error, bool)
                    else _format_metric_value(record["mean"]))
            if record.get("not_a_measurement"):
                cell += " \\*"
                qualified = True
            cells.append(cell)
        lines.append(f"| {run['run_id']} | " + " | ".join(cells) + " |")
    lines += ["", "Each mean is over the frames after equilibration, with its standard error."]
    if qualified:
        lines += ["", "\\* Not determined: the analysis found the series too short, or "
                  "with too few independent samples, for its mean to be determined."]
    return "\n".join(lines) + "\n"
