"""An umbrella study opened in the builder keeps its umbrella.

A Config given to the builder as a mapping (an Agent's reply, through its
Download config, Copy the command or Open in the builder) was checked in
place, and the validator expands an umbrella block into a run per window in
place. The form was then filled from the first window: the umbrella was
gone, and what was downloaded or run was one plain run of the structure. It
is now checked on a copy, as a Config read from a file always was.
"""

from __future__ import annotations

import copy

import pytest
import yaml

UMBRELLA = {
    "systems": [{"system": "181L"}],
    "setup": {"forcefield": "amber-openff", "ligand_name": "BNZ"},
    "simulation": {"duration_ns": 5, "umbrella": {
        "collective_variable": "ligand_distance",
        "site_selection": "resSeq 84 to 121 and name CA",
        "from": 0.3, "to": 1.5, "n_windows": 13, "force_constant": 2000}},
}


def test_the_form_holds_the_umbrella() -> None:
    from fastmdxplora.gui.config_builder import state_from_config

    given = copy.deepcopy(UMBRELLA)
    loaded = state_from_config(given)
    assert loaded["ok"]
    state = loaded["state"]
    assert state["system"] == "181L" and state["system_id"] == ""
    assert state["phases"]["simulation"]["umbrella"]["n_windows"] == 13
    # What was given is left as it was.
    assert given == UMBRELLA


def test_what_the_builder_writes_runs_every_window(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.config.loader import validate_config
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            page.goto(session.url + "#run", wait_until="domcontentloaded")
            built = page.evaluate("""async (config) => {
                const loaded = await (await fetch('/api/load-config', {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({config})})).json();
                await window.FastMDXRun.applyLoadedState(loaded.state, {});
                return window.FastMDXRun.fetchConfig();
            }""", UMBRELLA)
            browser.close()
    finally:
        session.server.shutdown()
    assert built["ok"]
    written = yaml.safe_load(built["yaml"])
    validate_config(written, require_systems=True)
    windows = [s["simulation"]["umbrella"] for s in written["systems"]]
    assert len(windows) == 13
    assert [round(w["centre"], 3) for w in windows][:3] == [0.3, 0.4, 0.5]
    assert {w["force_constant"] for w in windows} == {2000.0}
    assert written["setup"]["forcefield"] == "amber-openff"
