"""A rerun over an unfinished round is said in the GUI as it ended.

Its own file, so the round's tests (`test_a_failed_round_is_said_failed.py`,
which builds the studies) are not in the browser pass: there they ran one at
a time on every CI leg, about 23 minutes on one core, for this one test.
"""

from __future__ import annotations

import pytest

from tests.test_a_failed_round_is_said_failed import (  # noqa: F401
    HAS_BACKENDS,
    _rerun_case,
    _source,
    _the_logger_as_the_module_found_it,
    one_killed_in_its_join,
    sequential_unjoined,
)

pytestmark = pytest.mark.skipif(not HAS_BACKENDS, reason="needs OpenMM, MDTraj and PDBFixer")


@pytest.mark.parametrize("broken", [False, True], ids=["clean", "failing"])
@pytest.mark.parametrize("case", ["cut", "run page", "one run"])
def test_a_rerun_over_an_unfinished_round_is_said_in_a_browser(request, tmp_path, monkeypatch,
                                                                case, broken):
    """In the GUI (Light, 1440): Analyze again of a cut study and of a run's
    own page, and Write it again of a study of one run, ending well, said
    done with the round after it; failing, said with its run and reason and
    the round after it, its command set as code, never done."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.batch.explorer import campaign_of
    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.simulation.stopping import round_ended

    monkeypatch.setenv("PYTHONPATH", _source())
    made = _rerun_case(request, case, broken, tmp_path)
    page_of, where = made["page"], made["phase"]
    done = "Analyzed again." if where == "analysis" else "Written again."
    box = "#analysis-again-ask" if where == "analysis" else "#report-write-ask"
    session = start_dashboard_session(output=str(page_of), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000},
                                    color_scheme="light")
            page.set_default_timeout(60000)
            page.goto(session.url + "#" + where, wait_until="domcontentloaded")
            button = "#analysis-again" if where == "analysis" else "#report-write"
            page.wait_for_selector(button + ":not([hidden])")
            page.click(button)
            page.click(box + " .fix-confirm")
            page.wait_for_function(
                "([box, done, reason]) => { const t = (document.querySelector(box) || {})"
                ".textContent || ''; return t.includes(done) || t.includes(reason); }",
                arg=[box, done, made["reason"]], timeout=300000)
            line = box + (" .again-said" if where == "analysis" else " .fix-said")
            said = page.text_content(line)
            code = page.eval_on_selector_all(line + " code",
                                             "nodes => nodes.map(n => n.textContent)")
            browser.close()
    finally:
        session.server.shutdown()
    ended = round_ended(campaign_of(page_of) or page_of)
    command = " ".join(ended.argv)
    terminal = command if command in ended.carry_on_on_a_page else ""
    if broken:
        assert done not in said and made["reason"] in said, said
        for name in made["names"]:
            assert f"{name} failed in its analysis" in said, said
    else:
        assert said.startswith(done), said
    assert said.endswith(f"{ended.said} {ended.carry_on_on_a_page}"), said
    assert code == ([terminal] if terminal else []), (code, said)
