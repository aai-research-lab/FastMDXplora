"""A study opened in the form keeps the phases it chose.

The server sends the phase list as `include_phase`, which it has since the
phase lists took one name, and the page read `include`: every phase came up
unticked, and the page would have submitted none. The Agent's configs reach
the form the same way. Driven through the page's own load path, in a browser.
"""

from __future__ import annotations

import unittest

try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    _HAVE_PLAYWRIGHT = True
except ImportError:
    _HAVE_PLAYWRIGHT = False


@unittest.skipUnless(_HAVE_PLAYWRIGHT, "playwright not installed")
class TestALoadedStudyKeepsItsPhases(unittest.TestCase):
    def test_the_phases_it_chose_are_ticked_and_submitted(self):
        import sys
        import tempfile
        from pathlib import Path

        import yaml

        sys.path.insert(0, "src")
        from playwright.sync_api import sync_playwright

        from fastmdxplora.gui.server import start_dashboard_session

        work = Path(tempfile.mkdtemp())
        study = work / "two_phases.yml"
        study.write_text(yaml.safe_dump({"systems": [{"system": "1UBQ"}],
                                         "include_phase": ["setup", "simulation"],
                                         "simulation": {"duration_ns": 2.0}}), encoding="utf-8")
        session = start_dashboard_session(output=str(work / "ws"), host="127.0.0.1", port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page()
                page.goto(session.url, wait_until="domcontentloaded")
                # The builder is usable once it has read the settings it draws.
                page.wait_for_function(
                    "window.FastMDXRun && window.FastMDXRun.state && window.FastMDXRun.state.schema",
                    timeout=8000)
                # What loadConfigIntoForm does: ask the server, hand the page its answer.
                after = page.evaluate("""async (path) => {
                    const response = await fetch('/api/load-config', {method: 'POST',
                        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
                    const loaded = await response.json();
                    const run = window.FastMDXRun;
                    run.applyLoadedState(loaded.state, {from: path});
                    const built = await run.fetchConfig();
                    return {ticked: Array.from(run.state.phases),
                            submitted: run.currentState().include_phase, yaml: built.yaml || built.error};
                }""", str(study))
                browser.close()
        finally:
            session.server.shutdown()
        self.assertEqual(sorted(after["ticked"]), ["setup", "simulation"])
        self.assertEqual(sorted(after["submitted"]), ["setup", "simulation"])
        self.assertEqual(sorted(yaml.safe_load(after["yaml"])["include_phase"]), ["setup", "simulation"])
