"""Top-level report phase entry point.

Combines outputs from the document, slides, and bundle generators into a
unified report artifact set.
"""

from __future__ import annotations

import json

from fastmdxplora.utils.logging import get_logger
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmdxplora.report.bundle import build_bundle
from fastmdxplora.report.context import _system_label
from fastmdxplora.gui.report_dashboard import build_dashboard
from fastmdxplora.report.document import build_document
from fastmdxplora.config.schema import REPORT
from fastmdxplora.report.region_highlights import build_region_highlight_artifacts
from fastmdxplora.report.slides import build_slides
from fastmdxplora.report.summary_figure import build_analysis_summary_figure

if TYPE_CHECKING:
    from fastmdxplora.orchestrator import FastMDXplora

logger = get_logger("report")


#: What each option starts as, read from the schema that declares it.
#: A phase keeping its own copy is a second place for a default to live,
#: and the two drifted once already.
DEFAULTS: dict[str, Any] = REPORT.defaults()


def run(
    *,
    orchestrator: "FastMDXplora",
    output_dir: Path,
    **options: Any,
) -> list[str]:
    """Run the report phase.

    Parameters
    ----------
    orchestrator : FastMDXplora
        Parent orchestrator (used to locate sibling phase outputs).
    output_dir : Path
        Where to write report artifacts.
    **options
        See ``DEFAULTS``.

    Returns
    -------
    list of str
        Paths (relative to ``output_dir``) of artifacts produced.
    """
    params = {**DEFAULTS, **options}
    title = params["title"] or f"FastMDXplora Study — {_system_label(orchestrator.system)}"

    presenter = getattr(orchestrator, "_presenter", None)
    artifacts: list[str] = []
    not_produced: list[tuple[str, str]] = []

    region_artifacts = build_region_highlight_artifacts(
        project_root=orchestrator.output_dir,
        output_dir=output_dir,
        region_highlights=params.get("region_highlights"),
    )
    artifacts.extend(region_artifacts)
    if presenter:
        for art in region_artifacts:
            presenter.step(f"Wrote {art}")

    summary_artifacts = build_analysis_summary_figure(
        project_root=orchestrator.output_dir,
        output_dir=output_dir,
    )
    artifacts.extend(summary_artifacts)
    if presenter:
        for art in summary_artifacts:
            presenter.step(f"Wrote {art}")

    if params["document"]:
        doc_artifacts = build_document(
            orchestrator=orchestrator,
            output_dir=output_dir,
            title=title,
            author=params["author"],
            include_methods=params["include_methods"],
            include_reproducibility=params["include_reproducibility"],
        )
        artifacts.extend(doc_artifacts)
        if presenter:
            presenter.step("Wrote report.md")

        # The document as a PDF, which is the form somebody sends on. Its
        # absence does not fail the phase: a report that produced a document,
        # a dashboard, slides and a bundle should not be marked failed because
        # a fifth format needed a library nobody installed.
        if params.get("pdf", True):
            from fastmdxplora.report.pdf import try_render_pdf

            pdf_path, reason = try_render_pdf(
                output_dir / "report.md", title=title)
            if pdf_path is not None:
                artifacts.append("report.pdf")
                if presenter:
                    presenter.step("Wrote report.pdf")
            else:
                # Written down as well as said. A run read from its files
                # later shows four formats where five were asked for, and
                # nothing to say the fifth was attempted -- the terminal
                # warning is gone by then, and the difference between "not
                # requested" and "requested and not possible" is exactly what
                # somebody checking the outputs needs.
                not_produced.append(("report.pdf", reason))
                if presenter:
                    presenter.step(f"No PDF: {reason}", status="warn")

    if params["slides"]:
        slide_artifacts = build_slides(
            orchestrator=orchestrator,
            output_dir=output_dir,
            title=title,
        )
        artifacts.extend(slide_artifacts)
        if presenter:
            for art in slide_artifacts:
                presenter.step(f"Wrote {art}")

    dashboard_artifacts = build_dashboard(
        orchestrator=orchestrator,
        output_dir=output_dir,
        title=title,
        include_bundle_link=bool(params["bundle"]),
    )
    artifacts.extend(dashboard_artifacts)
    if presenter:
        for art in dashboard_artifacts:
            presenter.step(f"Wrote {art}")

    if params["bundle"]:
        bundle_artifacts = build_bundle(
            orchestrator=orchestrator,
            output_dir=output_dir,
        )
        artifacts.extend(bundle_artifacts)
        if presenter:
            presenter.step("Wrote project_bundle.zip")

    if not_produced:
        (output_dir / "not_produced.json").write_text(
            json.dumps(
                [{"artifact": name, "reason": why} for name, why in not_produced],
                indent=2),
            encoding="utf-8")
        artifacts.append("not_produced.json")
    else:
        # Everything was produced this time, so a record of what an earlier
        # run could not produce is no longer true. Left in place, the page
        # said the PDF was missing beside a link to download it.
        (output_dir / "not_produced.json").unlink(missing_ok=True)

    logger.debug("report: wrote %d artifact(s) to %s", len(artifacts), output_dir)
    return artifacts
