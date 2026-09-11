"""The report as a PDF, from the Markdown it already writes.

The document is the thing somebody reads and sends on, and a Markdown file is
neither. It renders differently in every viewer, it cannot be printed with the
figures where the text put them, and attaching one to an email invites the
question of what to open it with.

This converts ``report.md`` rather than the browser dashboard: the dashboard is
a thing to click through, and a PDF of it would be a picture of an interface.
The report is a document, and a document is what a PDF should be.

WeasyPrint does the rendering. It is the actively maintained option --
wkhtmltopdf has been archived since 2023 -- it implements CSS paged media, so
page breaks and figure placement follow rules rather than luck, and it needs
no browser. It does need Pango and Cairo as system libraries, which is the
cost, and why this is an optional extra rather than a requirement: a report in
four formats should not fail because a fifth could not be produced.
"""

from __future__ import annotations

from pathlib import Path
from fastmdxplora.refusals import CodedError

__all__ = ["render_pdf", "PdfUnavailable", "STYLESHEET"]


class PdfUnavailable(CodedError, RuntimeError):
    """Raised when the PDF cannot be produced, saying what would fix it."""

    default_code = "report.format.unavailable"


#: Print styling. Deliberately plain: a report is read for its numbers, and a
#: PDF that looks designed invites the reader to trust the design.
STYLESHEET = """
@page {
    size: A4;
    margin: 2cm 2cm 2.2cm;
    @bottom-center {
        content: counter(page) " of " counter(pages);
        font-family: sans-serif;
        font-size: 8pt;
        color: #666;
    }
}
body {
    font-family: "DejaVu Serif", Georgia, serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #1a1a1a;
}
h1 { font-size: 20pt; margin-bottom: 0.2em; }
/* A section should not start at the foot of a page with one line under it. */
h2 { font-size: 14pt; margin-top: 1.4em; break-after: avoid; }
h3 { font-size: 11.5pt; margin-top: 1.1em; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
code, pre {
    font-family: "DejaVu Sans Mono", monospace;
    font-size: 9pt;
    background: #f4f4f4;
}
pre { padding: 8px; white-space: pre-wrap; break-inside: avoid; }
/* A figure split across a page break is worse than a gap before it. */
img { max-width: 100%; break-inside: avoid; }
table {
    border-collapse: collapse;
    font-size: 9.5pt;
    margin: 0.8em 0;
    width: 100%;
    break-inside: avoid;
}
th, td { border: 1px solid #ccc; padding: 4px 7px; text-align: left; }
th { background: #f0f0f0; }
blockquote { border-left: 3px solid #ddd; margin-left: 0; padding-left: 1em; }
"""


def _markdown_to_html(text: str, title: str) -> str:
    """Markdown to HTML, by whichever converter is present.

    One converter, and it is declared. A second path guarded by a try-import
    and named in no manifest is a dependency nobody knows about until it is
    missing -- which is what the dependency guard exists to catch, and did.
    """
    try:
        import markdown as markdown_lib
    except ImportError as exc:
        raise PdfUnavailable(
            "Rendering the report as a PDF needs a Markdown converter. "
            "Install the report extra (pip install 'fastmdxplora[pdf]') or "
            "conda install -c conda-forge markdown weasyprint."
        ) from exc

    body = markdown_lib.markdown(
        text, extensions=["tables", "fenced_code", "toc"])

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>{body}</body></html>"
    )


def render_pdf(
    markdown_path: str | Path,
    pdf_path: str | Path | None = None,
    *,
    title: str = "FastMDXplora report",
) -> Path:
    """Write ``report.pdf`` beside the Markdown it came from.

    Raises :class:`PdfUnavailable` where the tools are absent, naming what to
    install. The caller decides whether that is fatal; for the report phase it
    is not, because four other formats were produced.
    """
    markdown_path = Path(markdown_path)
    if not markdown_path.is_file():
        raise PdfUnavailable(f"No report to convert at {markdown_path}.")

    target = Path(pdf_path) if pdf_path else markdown_path.with_suffix(".pdf")

    try:
        from weasyprint import CSS, HTML
    except ImportError as exc:
        raise PdfUnavailable(
            "Rendering the report as a PDF needs WeasyPrint, which is not "
            "installed. Install the report extra (pip install "
            "'fastmdxplora[pdf]') or conda install -c conda-forge weasyprint. "
            "On pip it also needs Pango and Cairo as system libraries; the "
            "conda-forge package brings its own."
        ) from exc
    except OSError as exc:
        # WeasyPrint imports but cannot find Pango or Cairo. The message it
        # raises names a shared library, which is not what somebody needs to
        # be told.
        raise PdfUnavailable(
            "WeasyPrint is installed but its system libraries are not: it "
            "needs Pango, Cairo and GDK-PixBuf. On conda-forge these come "
            "with the package (conda install -c conda-forge weasyprint); on "
            "Debian they are libpango-1.0-0, libcairo2 and "
            f"libgdk-pixbuf-2.0-0. The underlying error was: {exc}"
        ) from exc

    html = _markdown_to_html(
        markdown_path.read_text(encoding="utf-8"), title)

    # Resolved against the report's own directory, so the figures it references
    # by relative path are found rather than silently dropped.
    document = HTML(string=html, base_url=str(markdown_path.parent))
    document.write_pdf(str(target), stylesheets=[CSS(string=STYLESHEET)])
    return target


def try_render_pdf(
    markdown_path: str | Path,
    *,
    title: str = "FastMDXplora report",
) -> tuple[Path | None, str | None]:
    """Render if possible; otherwise say why, without failing the run.

    Returns ``(path, None)`` or ``(None, reason)``. A report phase that
    produced a document, a dashboard, slides and a bundle should not be marked
    failed because a fifth format needed a library nobody installed.
    """
    try:
        return render_pdf(markdown_path, title=title), None
    except PdfUnavailable as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - a rendering fault is not fatal
        return None, f"The PDF could not be rendered: {exc}"
