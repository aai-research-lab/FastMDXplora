"""In a browser: a cited mean's note stands on its own line, and Compare says
which settings a bias may stand in for.

The Agent's citation chip put the image note inside the value and the
verdict after it ("0.30 nm; may be biased: ... its own image, not
determined"), so "not determined" read as said of the image (C10 M3): the
value is sent plain, the note alone, and the chip says the value, its
verdict, then the note on a line of its own, left-aligned where it wraps.
Compare, for two studies, said a resolved difference "may be that bias and
not the setting", of studies differing in several (C12 L5).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

import pytest

from fastmdxplora.image_note import said_short

#: The RMSD's note: within 1 nm of its own image in 372 of 1,864 frames.
NOTE = {"frames_used": 1864, "frames_under_cutoff": 372, "fraction_under_cutoff": 0.2,
        "frames_under_close": 3, "nearest_nm": 0.42, "cutoff_nm": 1.0,
        "counted_over": "frames"}


def _browser(pw):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 900, "height": 800})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    return browser, page, errors


def test_a_cited_mean_s_note_stands_below_its_value_and_verdict(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.citations import cited_findings
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis as _drawn
    from tests.test_an_analysis_is_drawn_from_its_numbers import _manifest, _series
    from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES, _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, saving_interval_ps=2.0)
    _drawn(root, "rmsd", _series(FRAMES), {"mean": 0.112, "standard_error": 0.002,
                                           "effective_samples": 11.0, "discard": 5,
                                           "n_frames": FRAMES, "unit": "nm"})
    path = root / "analysis" / "rmsd" / "options.json"
    record = json.loads(path.read_text())
    record["findings"]["mean"].update(solute_image=dict(NOTE),
                                      not_a_measurement="Still drifting after its equilibration.")
    path.write_text(json.dumps(record))
    (cite,) = cited_findings("The RMSD was 0.112 nm.", root)
    # Sent plain, the note alone.
    assert (cite["value"], cite["noted"]) == ("0.1120 nm", said_short(NOTE))
    # An answer saved before carries the note inside its value.
    saved = dict(cite, value=f"{cite['value']}; {cite['noted']}")
    entries = [{"role": "user", "text": "the RMSD?"},
               {"role": "agent", "kind": "answer", "text": "The RMSD was 0.112 nm.",
                "cites": [cite]},
               {"role": "user", "text": "and before?"},
               {"role": "agent", "kind": "answer", "text": "The RMSD was 0.112 nm.",
                "cites": [saved]}]
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    request = urllib.request.Request(
        session.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()
    read = """() => [...document.querySelectorAll('#agent-thread .agent-cite')].map((c) => {
        const v = c.querySelector('.agent-cite-value'), n = c.querySelector('.agent-cite-note');
        const nm = c.querySelector('.agent-cite-name');
        const vb = v.getBoundingClientRect(), nb = n ? n.getBoundingClientRect() : null;
        const nmb = nm.getBoundingClientRect();
        return {value: v.textContent, note: n ? n.textContent : null,
                below: nb ? nb.top >= vb.bottom - 1 : null,
                left: nb ? Math.abs(nb.left - nmb.left) : null,
                align: getComputedStyle(c).textAlign};
    })"""
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.querySelectorAll('#agent-thread .agent-cite').length === 2")
            chips = page.evaluate(read)
            browser.close()
    finally:
        session.server.shutdown()
    line = said_short(NOTE)
    for chip in chips:
        assert chip["value"] == "0.1120 nm, not determined"
        assert chip["note"] == line[:1].upper() + line[1:] + "."
        assert chip["below"] and chip["left"] < 1.5 and chip["align"] == "left", chip
    assert errors == []


def test_compare_of_two_studies_says_the_settings_that_differ(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_workspace_says_its_studies import _study

    first = _study(tmp_path / "ws" / "first", means={"rmsd": (0.1046, 0.0013)})
    second = _study(tmp_path / "ws" / "second", duration=20.0, forcefield="charmm36",
                    means={"rmsd": (0.1200, 0.0013)})
    path = second / "analysis" / "rmsd" / "options.json"
    record = json.loads(path.read_text())
    record["findings"]["mean"]["solute_image"] = dict(NOTE)
    path.write_text(json.dumps(record))
    session = start_dashboard_session(output=str(first), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _browser(pw)
            page.goto(session.url + "#compare/" + "|".join(
                urllib.parse.quote(str(p), safe="") for p in (first, second)),
                wait_until="domcontentloaded")
            page.wait_for_selector('.page[data-page="compare"][data-compared="yes"]')
            settings = page.locator(".compare-settings-table tr").count()
            lines = page.evaluate(
                "() => [...document.querySelectorAll('.compare-noted')].map((n) => n.textContent)")
            browser.close()
    finally:
        session.server.shutdown()
    assert settings >= 2
    assert lines[-1] == ("One resolved difference has a mean that may be biased by the "
                         "solute's own image: it may be that bias and not the settings that "
                         "differ.")
    assert errors == []
