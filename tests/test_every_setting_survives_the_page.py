"""Every study setting, through the GUI's page itself and back.

The interface guard's GUI column reads the payload the page builds its form
from: it says a control exists, not that the page carries the value. This
drives the real page in a browser for every setting: a study with the setting
is opened the way the page opens one, the form is read back through the
page's own currentState, and the config the server builds from it is compared
with the study, both normalised. A ratchet like the guard's, in its own file
because only the CI job with a browser can run it.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    _HAVE_PLAYWRIGHT = True
except ImportError:
    _HAVE_PLAYWRIGHT = False

#: setting -> why it does not survive the page. Closing one means taking it
#: off here; a setting newly lost fails.
KNOWN_GAPS: dict[str, str] = {
    "(study).systems": "the form takes one system",
    "analysis.topology": "only meaningful for a study that starts from a trajectory; "
                         "dropped beside a structure, which is right",
}


def _phases_run(config: dict) -> list[str]:
    from fastmdxplora.config.schema import PHASE_KEYS

    included = config.get("include_phase") or list(PHASE_KEYS)
    excluded = set(config.get("exclude_phase") or [])
    return [phase for phase in PHASE_KEYS if phase in included and phase not in excluded]


def _measure() -> dict[str, bool]:
    import sys

    import yaml
    from playwright.sync_api import sync_playwright

    sys.path.insert(0, "src")
    from fastmdxplora.config.loader import normalise_config
    from fastmdxplora.gui.server import start_dashboard_session
    from tests._interfaces import (BECOME_RUNS, a_config, a_value_other_than_the_default,
                                   read_back, same, settings, the_runs)

    work = Path(tempfile.mkdtemp())
    (work / "ws").mkdir()
    studies = []
    for block, name in settings():
        value = a_value_other_than_the_default(block, name)
        config = a_config(block, name, value)
        path = work / f"{block.strip('()')}.{name}.yml"
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        studies.append((block, name, config, str(path)))

    session = start_dashboard_session(output=str(work / "ws"), host="127.0.0.1", port=0)
    survived: dict[str, bool] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(session.url, wait_until="domcontentloaded")
            page.wait_for_function(
                "window.FastMDXRun && window.FastMDXRun.state && window.FastMDXRun.state.schema",
                timeout=30000)
            for block, name, config, path in studies:
                built = page.evaluate("""async (path) => {
                    const response = await fetch('/api/load-config', {method: 'POST',
                        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
                    const loaded = await response.json();
                    if (!loaded.ok) return {ok: false, error: loaded.error};
                    const run = window.FastMDXRun;
                    run.applyLoadedState(loaded.state, {from: path});
                    return await run.fetchConfig();
                }""", path)
                try:
                    wanted = normalise_config(copy.deepcopy(config))
                    produced = normalise_config(yaml.safe_load(built["yaml"])) if built.get("ok") else {}
                    if name in ("include_phase", "exclude_phase"):
                        # The phases that run, however the study says it:
                        # "exclude report" and "include the other three" are
                        # the same plan, and the form holds the plan.
                        ok = built.get("ok", False) and _phases_run(produced) == _phases_run(wanted)
                    else:
                        ok = built.get("ok", False) and same(
                            read_back(produced, block, name), read_back(wanted, block, name)) and (
                            name not in BECOME_RUNS or same(the_runs(produced), the_runs(wanted)))
                except Exception:  # noqa: BLE001 -- not surviving is the finding
                    ok = False
                survived[f"{block}.{name}"] = bool(ok)
            browser.close()
    finally:
        session.server.shutdown()
    return survived


@unittest.skipUnless(_HAVE_PLAYWRIGHT, "playwright not installed")
class TestEverySettingSurvivesThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.survived = _measure()

    def test_no_setting_is_newly_lost(self):
        lost = sorted(s for s, ok in self.survived.items() if not ok and s not in KNOWN_GAPS)
        self.assertEqual(lost, [], f"these no longer survive the page: {lost}")

    def test_a_closed_gap_is_taken_off_the_list(self):
        closed = sorted(s for s in KNOWN_GAPS if self.survived.get(s))
        self.assertEqual(closed, [], f"these now survive; take them off KNOWN_GAPS: {closed}")

    def test_it_measured_every_setting(self):
        self.assertGreater(len(self.survived), 100)
