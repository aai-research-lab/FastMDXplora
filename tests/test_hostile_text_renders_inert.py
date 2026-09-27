"""Text from a study is shown as text, and a page from a study cannot act as the GUI.

A report quotes names a structure file and a config give, the Files preview
renders any `.md` in a study, and the Agent shows what a model replied. The
report and the preview passed raw HTML through, a link could be
`javascript:`, the model's links could close their own attribute, and an HTML
file in a study ran in a frame with the GUI's own standing. Here every one of
those carries a payload that records itself if it runs, and nothing may.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora.gui.report_page import render_markdown
from fastmdxplora.report.markdown_html import is_safe_url

RAN = "__ran"


def _payload(where: str) -> str:
    return (f'<img src="x" onerror="window.{RAN}.push(\'{where}\')">'
            f'<script>window.{RAN}.push("{where}-script")</script>')


HOSTILE_MARKDOWN = (
    "# Report\n\n"
    + _payload("markdown") + "\n\n"
    + f"<div onclick=\"window.{RAN}.push('block')\">block</div>\n\n"
    + "[a](javascript:window.__ran.push('link')) "
    + "[b](JaVa\tScRiPt:window.__ran.push('link')) "
    + "[c](&#106;avascript:window.__ran.push('link')) "
    + "[d][ref]\n\n[ref]: javascript:window.__ran.push('link')\n\n"
    + "| name |\n|---|\n| " + _payload("table") + " |\n"
)


class TestTheRenderer:

    def test_raw_html_is_shown_as_written(self) -> None:
        html, rendered = render_markdown(HOSTILE_MARKDOWN)
        if rendered != "html":
            pytest.skip("the markdown library is not installed")
        assert "<img" not in html and "<script" not in html and "<div" not in html
        assert "&lt;img" in html and "&lt;script&gt;" in html

    def test_no_link_leaves_the_web_mail_or_the_study(self) -> None:
        html, rendered = render_markdown(HOSTILE_MARKDOWN)
        if rendered != "html":
            pytest.skip("the markdown library is not installed")
        assert "javascript" not in html.lower().replace("window.__ran", "")
        assert "href=" not in html

    def test_what_the_report_uses_still_renders(self) -> None:
        html, rendered = render_markdown(
            "# T\n\n| a | b |\n|:-|-:|\n| 1 | 2 |\n\n![fig](figure.png)\n\n"
            "[doi](https://doi.org/10.1/x) [mail](mailto:a@b.org) [sec](#t)\n\n"
            "```\ncode <b>\n```\n")
        if rendered != "html":
            pytest.skip("the markdown library is not installed")
        for piece in ('<h1 id="t">', "<table>", 'src="figure.png"',
                      'href="https://doi.org/10.1/x"', 'href="mailto:a@b.org"',
                      'href="#t"', "code &lt;b&gt;"):
            assert piece in html

    @pytest.mark.parametrize("url", [
        "javascript:x", " javascript:x", "java\nscript:x", "JAVASCRIPT:x",
        "&#106;avascript:x", "data:text/html,x", "vbscript:x", "file:///etc/passwd",
    ])
    def test_an_unsafe_address_is_refused(self, url: str) -> None:
        assert not is_safe_url(url)

    @pytest.mark.parametrize("url", [
        "figure.png", "../analysis/rmsd.png", "#top", "https://x.org/a?b=1&c=2",
        "http://x.org", "mailto:a@b.org", "/artifacts/report/figure.png",
    ])
    def test_a_safe_address_is_kept(self, url: str) -> None:
        assert is_safe_url(url)

    def test_the_pdf_renders_the_same_way(self) -> None:
        pytest.importorskip("markdown")
        from fastmdxplora.report.pdf import _markdown_to_html

        page = _markdown_to_html(HOSTILE_MARKDOWN, "<title>")
        assert "<img" not in page and "<script" not in page
        assert "href=" not in page
        assert "<title>&lt;title&gt;</title>" in page


def _hostile_study() -> Path:
    root = Path(tempfile.mkdtemp()) / "study"
    (root / "simulation").mkdir(parents=True)
    (root / "analysis").mkdir()
    (root / "report").mkdir()
    (root / "manifest.json").write_text(
        json.dumps({"system": _payload("system"), "title": _payload("title")}),
        encoding="utf-8")
    (root / "report" / "report.md").write_text(HOSTILE_MARKDOWN, encoding="utf-8")
    (root / "analysis" / "notes.md").write_text(HOSTILE_MARKDOWN, encoding="utf-8")
    (root / "analysis" / "page.html").write_text(
        "<!doctype html><title>page</title><body>page<script>"
        f"try {{ parent.{RAN}.push('frame'); }} catch (e) {{}}"
        "document.body.dataset.done = '1';"
        "</script></body>", encoding="utf-8")
    if os.name != "nt":
        # A name a POSIX filesystem allows.
        (root / "analysis" / (f"<img src=x onerror={RAN}.push(1)>.txt")).write_text(
            "x", encoding="utf-8")
    return root


class TestInTheBrowser:

    @pytest.fixture()
    def session(self):
        pytest.importorskip("playwright.sync_api")
        from fastmdxplora.gui.agent_panel import write_conversation
        from fastmdxplora.gui.server import start_dashboard_session

        root = _hostile_study()
        session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
        write_conversation(session.runtime, [
            {"role": "user", "text": _payload("said"),
             "attachments": [{"name": _payload("attached")}]},
            {"role": "agent", "kind": "answer",
             "text": ("See https://example.org/\"onmouseover=\"window.__ran.push('reply')\" "
                      "and https://example.org/'onfocus='window.__ran.push('reply')' "
                      + _payload("reply"))},
        ])
        try:
            yield session
        finally:
            session.server.shutdown()
            session.server.server_close()

    @pytest.fixture()
    def page(self, session):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.add_init_script(f"window.{RAN} = [];")
            page.goto(session.url, wait_until="domcontentloaded")
            yield page
            browser.close()

    def _ran(self, page) -> list:
        page.wait_for_timeout(500)
        return page.evaluate(f"window.{RAN}")

    def test_the_report_page(self, page) -> None:
        page.evaluate("location.hash = '#report'")
        page.wait_for_selector("#report-document:not([hidden])", timeout=20000)
        assert self._ran(page) == []
        assert page.eval_on_selector_all("#report-document a[href]", "a => a.length") == 0

    def test_the_files_preview(self, page) -> None:
        page.wait_for_function("!!(window.FastMDXFrame && window.FastMDXFrame.previewPath)")
        page.evaluate("window.FastMDXFrame.previewPath('analysis/notes.md')")
        page.wait_for_selector("#side-preview-modes:not([hidden]) [data-mode=view]",
                               timeout=20000)
        page.click("#side-preview-modes [data-mode=view]")
        page.wait_for_selector("#side-preview-body .report-document", timeout=20000)
        assert self._ran(page) == []

    def test_a_page_from_the_study_runs_without_the_guis_standing(self, page) -> None:
        page.wait_for_function("!!(window.FastMDXFrame && window.FastMDXFrame.previewPath)")
        page.evaluate("window.FastMDXFrame.previewPath('analysis/page.html')")
        frame = page.wait_for_selector("#side-preview-body iframe", timeout=20000)
        assert frame.get_attribute("sandbox") == "allow-scripts"
        content = frame.content_frame()
        content.wait_for_selector("body[data-done='1']", timeout=20000)
        assert self._ran(page) == []

    def test_the_agents_conversation(self, page) -> None:
        page.evaluate("location.hash = '#agent'")
        page.wait_for_selector(".agent-answer a", timeout=20000)
        attributes = page.eval_on_selector_all(
            ".agent-answer a",
            "links => links.map(a => a.getAttributeNames().sort().join(' '))")
        assert attributes and set(attributes) == {"href rel target"}
        for link in page.query_selector_all(".agent-answer a"):
            link.hover()
            link.focus()
        assert self._ran(page) == []

    def test_the_overview_and_the_file_list(self, page) -> None:
        page.wait_for_function("!!(window.FastMDXFrame && window.FastMDXFrame.showTab)")
        page.evaluate("window.FastMDXFrame.showTab('files')")
        page.wait_for_selector("#side-files .side-file", timeout=20000)
        assert self._ran(page) == []


def test_a_document_from_a_study_is_served_in_a_sandbox() -> None:
    from fastmdxplora.gui.server import start_dashboard_session

    root = _hostile_study()
    (root / "analysis" / "figure.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>',
        encoding="utf-8")
    (root / "analysis" / "rmsd.dat").write_text("0 1\n", encoding="utf-8")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        def headers(name: str):
            with urllib.request.urlopen(f"{session.url.rstrip('/')}/artifacts/analysis/{name}",
                                        timeout=20) as answer:
                return answer.headers
        for name in ("page.html", "figure.svg"):
            assert headers(name)["Content-Security-Policy"] == "sandbox allow-scripts"
            assert headers(name)["X-Content-Type-Options"] == "nosniff"
        assert headers("rmsd.dat")["Content-Security-Policy"] is None
        assert headers("rmsd.dat")["X-Content-Type-Options"] == "nosniff"
    finally:
        session.server.shutdown()
        session.server.server_close()
