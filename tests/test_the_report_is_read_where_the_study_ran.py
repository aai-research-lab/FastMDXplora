"""The Report page: report.md as a document, in the centre column.

The report phase writes the study's methods, results and convergence, with
the sampling caveats in the prose where the numbers are. A person should
read that where they ran the study rather than opening a file to find out
what it said. And anything the phase could not produce -- a PDF without
WeasyPrint -- is a notice with the phase's own reason, not a dead button.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen

import fastmdxplora.gui as gui

STATIC = pathlib.Path(gui.__file__).parent / "static"
TEMPLATE = pathlib.Path(gui.__file__).parent / "templates" / "dashboard.html"


def _run_with_report(**extra_files) -> Path:
    root = Path(tempfile.mkdtemp())
    report = root / "report"
    report.mkdir()
    (report / "report.md").write_text(
        "# FastMDXplora Study — 1UAO\n\n_Generated: 2026-09-17 (UTC)_\n\n"
        "## Summary\n\nThe system was simulated for 2 ns.\n\n"
        "| Observable | Mean |\n|---|---|\n| RMSD | 1.8 Å |\n\n"
        "![summary](analysis_summary.png)\n", encoding="utf-8")
    for name, content in extra_files.items():
        (report / name).write_text(content, encoding="utf-8")
    return root


class TestThePayload(unittest.TestCase):

    def test_no_report_says_so(self):
        from fastmdxplora.gui.report_page import report_payload

        answer = report_payload(tempfile.mkdtemp())
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], "no report yet")

    def test_the_markdown_becomes_a_document(self):
        from fastmdxplora.gui.report_page import report_payload

        answer = report_payload(_run_with_report())
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["generated"], "2026-09-17 (UTC)")
        if answer["rendered"] == "html":
            self.assertIn("<h1", answer["html"])
            self.assertIn("<table>", answer["html"])
        else:
            # The markdown library is in the `pdf` extra. A base install
            # shows the report as written, which is legible Markdown.
            self.assertIn("# FastMDXplora Study", answer["html"])
            self.assertIn('<pre class="report-plain">', answer["html"])

    def test_without_the_markdown_library_the_report_still_shows(self):
        # CI installs the base package and found this: five failures where
        # a machine with the extra had passed.
        import builtins
        import importlib

        from fastmdxplora.gui import report_page

        real = builtins.__import__

        def hide(name, *a, **k):
            if name == "markdown":
                raise ImportError("hidden for the test")
            return real(name, *a, **k)

        builtins.__import__ = hide
        try:
            answer = report_page.report_payload(_run_with_report())
        finally:
            builtins.__import__ = real
            importlib.reload(report_page)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["rendered"], "plain")
        self.assertIn("# FastMDXplora Study", answer["html"])
        self.assertNotIn("<h1", answer["html"])

    def test_only_downloads_that_exist_are_offered(self):
        from fastmdxplora.gui.report_page import report_payload

        root = _run_with_report(**{"slides.pptx": "x"})
        answer = report_payload(root)
        self.assertIn("slides", answer["downloads"])
        self.assertIn("markdown", answer["downloads"])
        # No PDF was written, so no PDF button. A dead download is the
        # not_produced notice's job to explain, not a link's job to 404.
        self.assertNotIn("pdf", answer["downloads"])

    def test_what_could_not_be_made_is_reported_with_the_reason(self):
        from fastmdxplora.gui.report_page import report_payload

        root = _run_with_report(**{"not_produced.json": json.dumps([
            {"artifact": "report.pdf",
             "reason": "Rendering the report as a PDF needs WeasyPrint."}])})
        answer = report_payload(root)
        self.assertEqual(len(answer["not_produced"]), 1)
        self.assertEqual(answer["not_produced"][0]["artifact"], "report.pdf")
        self.assertIn("WeasyPrint", answer["not_produced"][0]["reason"])

    def test_a_malformed_not_produced_file_does_not_take_the_page_down(self):
        from fastmdxplora.gui.report_page import report_payload

        root = _run_with_report(**{"not_produced.json": "{not json"})
        answer = report_payload(root)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["not_produced"], [])


class TestThePage(unittest.TestCase):

    def page(self):
        return TEMPLATE.read_text(encoding="utf-8")

    def script(self):
        return (STATIC / "report-page.js").read_text(encoding="utf-8")

    def test_it_exists_and_is_in_the_nav(self):
        page = self.page()
        self.assertIn('<section class="page" data-page="report"', page)
        self.assertIn('data-view-link="report"', page)
        self.assertIn('<script src="/static/report-page.js"></script>', page)

    def test_it_needs_a_run(self):
        # Like Analysis and the Viewer: nothing to show without one.
        page = self.page()
        nav = page[page.index('data-view-link="report"') - 60:page.index('data-view-link="report"') + 80]
        self.assertIn('data-requires-run="true"', nav)

    def test_the_router_finds_it_without_a_list_to_update(self):
        # state.pages is read off the DOM, so a new section is known the
        # moment it is in the template.
        script = (STATIC / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn("state.pages = $$('.page')", script)

    def test_relative_figures_are_pointed_at_the_run(self):
        # The report refers to its figures by bare name; the page resolves
        # them through the artifacts route rather than the page's own URL.
        script = self.script()
        self.assertIn('"/artifacts/report/" + src', script)

    def test_a_missing_report_shows_the_empty_state_not_an_error(self):
        script = self.script()
        self.assertIn("empty.hidden = false", script)
        self.assertIn(".catch(function () { render(null); })", script)

    def test_it_does_not_rerender_on_every_poll_while_hidden(self):
        # A 20 KB document rendered on each app-state tick is a cost with
        # no reader. Only when the page is showing.
        self.assertIn("if (page && !page.hidden) load();", self.script())


class TestItServes(unittest.TestCase):

    def test_the_endpoint_answers_for_a_run_with_a_report(self):
        from fastmdxplora.gui.server import start_dashboard_session

        root = _run_with_report(**{"not_produced.json": json.dumps([
            {"artifact": "report.pdf", "reason": "needs WeasyPrint"}])})
        session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
        self.addCleanup(session.server.shutdown)
        base = f"http://127.0.0.1:{session.port}"
        answer = json.loads(urlopen(base + "/api/report").read())
        self.assertTrue(answer["ok"])
        self.assertIn("FastMDXplora Study", answer["html"])
        self.assertEqual(answer["not_produced"][0]["artifact"], "report.pdf")
        self.assertEqual(urlopen(base + "/static/report-page.js").getcode(), 200)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
