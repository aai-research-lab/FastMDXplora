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

__all__ = ["report_payload"]


def report_payload(root: Path | str) -> dict[str, Any]:
    """What the Report page needs, or why there is nothing to show."""
    base = Path(root)
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

    return {
        "ok": True,
        "html": html,
        "not_produced": not_produced,
        "downloads": downloads,
        "generated": _generated_line(text),
        "rendered": rendered,
    }


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
    library is the one this software already trusts for its report."""
    try:
        import markdown
    except ImportError:
        return '<pre class="report-plain">' + _escape(text) + "</pre>", "plain"
    return (markdown.markdown(text, extensions=["tables", "fenced_code", "toc"],
                              output_format="html5"), "html")
