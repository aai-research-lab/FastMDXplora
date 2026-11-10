"""The run page's header says the study's state whole at a phone's width.

At 390 px the header of a study of one run, cut while its stopping rule's
round extended it, said "Interrupte", and once that was whole the account
button wrapped to a row of its own. Its own file, so the round's tests
(`test_a_failed_round_is_said_failed.py`, which builds the study) are not
in the browser pass.
"""

from __future__ import annotations

import shutil

import pytest

from tests.test_a_failed_round_is_said_failed import HAS_BACKENDS, cut_one  # noqa: F401

pytestmark = pytest.mark.skipif(not HAS_BACKENDS, reason="needs OpenMM, MDTraj and PDBFixer")


def test_the_header_s_state_is_said_whole_at_390(cut_one, tmp_path):  # noqa: F811
    """At 390 px the run page's header said "Interrupte"."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = tmp_path / "study"
    shutil.copytree(cut_one["study"], study, symlinks=True)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => (document.getElementById('topbar-status-text') || {}).textContent"
                " === 'Interrupted'")
            found = page.evaluate("""() => {
              const text = document.getElementById('topbar-status-text');
              const line = text.closest('.study-status');
              const a = text.getBoundingClientRect(), b = line.getBoundingClientRect();
              // The account button on the header's row, beside the study.
              const card = document.querySelector('.sidebar-study').getBoundingClientRect();
              const account = document.querySelector('.sidebar-foot').getBoundingClientRect();
              return {inside: a.right <= b.right + 1 && a.left >= b.left - 1,
                      page: document.documentElement.scrollWidth <= 390,
                      row: account.top < card.bottom && account.bottom > card.top};
            }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert found == {"inside": True, "page": True, "row": True}
