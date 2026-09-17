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

    try:
        import markdown
    except ImportError:  # pragma: no cover - declared, but be honest
        return {"ok": False, "reason": "markdown is not installed",
                "html": "", "not_produced": [], "downloads": {}}

    text = source.read_text(encoding="utf-8", errors="replace")
    html = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "toc"],
        output_format="html5",
    )

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
    }


def _generated_line(text: str) -> str:
    """The `_Generated: …_` line near the top, if the report carries one."""
    for line in text.splitlines()[:8]:
        stripped = line.strip().strip("_")
        if stripped.lower().startswith("generated:"):
            return stripped[len("generated:"):].strip()
    return ""
