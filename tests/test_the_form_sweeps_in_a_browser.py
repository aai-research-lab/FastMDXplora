"""The sweep, in the page itself.

The server side is tested in test_the_form_sweeps.py. This drives the page:
a study with a sweep opened in the form shows its rows, the summary says how
many runs, and what the page submits builds the same sweep back. Then a row
added by hand, the way a person would.
"""

from __future__ import annotations

import unittest

try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    _HAVE_PLAYWRIGHT = True
except ImportError:
    _HAVE_PLAYWRIGHT = False

SWEEP = {"simulation.temperature_K": [300.0, 310.0],
         "analysis.select_atoms": ["name CA, name CB", "protein"]}


@unittest.skipUnless(_HAVE_PLAYWRIGHT, "playwright not installed")
class TestTheFormSweeps(unittest.TestCase):
    def _session(self):
        import sys
        import tempfile
        from pathlib import Path

        import yaml

        sys.path.insert(0, "src")
        from fastmdxplora.gui.server import start_dashboard_session

        work = Path(tempfile.mkdtemp())
        study = work / "sweep.yml"
        study.write_text(yaml.safe_dump({"systems": [{"system": "1UBQ"}], "sweep": SWEEP},
                                        sort_keys=False), encoding="utf-8")
        return start_dashboard_session(output=str(work / "ws"), host="127.0.0.1", port=0), study

    def _page(self, pw, session):
        page = pw.chromium.launch().new_page(viewport={"width": 1400, "height": 900})
        page.goto(session.url, wait_until="domcontentloaded")
        page.wait_for_function(
            "window.FastMDXRun && window.FastMDXRun.state && window.FastMDXRun.state.schema",
            timeout=20000)   # the first /api/schema builds the whole payload
        return page

    def test_a_study_opened_in_the_form_shows_its_sweep_and_submits_it(self):
        import yaml
        from playwright.sync_api import sync_playwright

        session, study = self._session()
        try:
            with sync_playwright() as pw:
                page = self._page(pw, session)
                after = page.evaluate("""async (path) => {
                    const response = await fetch('/api/load-config', {method: 'POST',
                        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
                    const loaded = await response.json();
                    const run = window.FastMDXRun;
                    run.applyLoadedState(loaded.state, {from: path});
                    const rows = Array.from(document.querySelectorAll('#run-sweep .run-sweep-row'))
                        .map(r => [r.querySelector('.run-sweep-axis').value,
                                   r.querySelector('.run-sweep-values').value]);
                    const built = await run.fetchConfig();
                    return {rows, summary: document.getElementById('run-summary').textContent,
                            head: document.querySelector('#run-sweep .run-section-count').textContent,
                            yaml: built.yaml || built.error};
                }""", str(study))
                page.context.browser.close()
        finally:
            session.server.shutdown()
        self.assertEqual([r[0] for r in after["rows"]], list(SWEEP))
        self.assertEqual(after["rows"][0][1], "300.0, 310.0")
        self.assertIn("4 runs", after["summary"])
        self.assertIn("2 settings, 4 runs", after["head"])
        self.assertEqual(yaml.safe_load(after["yaml"])["sweep"], SWEEP)

    def test_a_row_added_by_hand_reaches_the_config(self):
        import yaml
        from playwright.sync_api import sync_playwright

        session, _ = self._session()
        try:
            with sync_playwright() as pw:
                page = self._page(pw, session)
                # A study with no sweep, opened as the page opens one, so the
                # form is drawn and the section is there to add to.
                page.evaluate("() => window.FastMDXRun.applyLoadedState({"
                              " system: '1UBQ', start: 'structure',"
                              " include_phase: ['setup', 'simulation'], phases: {}})")
                page.evaluate("() => window.FastMDXDashboard.navigate('run')")
                page.wait_for_selector("#run-sweep .run-section-head", state="visible", timeout=10000)
                page.click("#run-sweep .run-section-head")
                page.click("#run-sweep .run-sweep-add")
                page.select_option("#run-sweep .run-sweep-axis", "simulation.temperature_K")
                page.fill("#run-sweep .run-sweep-values", "300, 310, 320")
                page.dispatch_event("#run-sweep .run-sweep-values", "change")
                built = page.evaluate("() => window.FastMDXRun.fetchConfig()")
                summary = page.text_content("#run-summary")
                page.context.browser.close()
        finally:
            session.server.shutdown()
        self.assertEqual(yaml.safe_load(built["yaml"])["sweep"],
                         {"simulation.temperature_K": [300, 310, 320]})
        self.assertIn("3 runs", summary)
