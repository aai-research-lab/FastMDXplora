"""A rejected status read after a launch answer cannot establish the study."""

from __future__ import annotations

import pytest

from tests.test_the_workspace_says_its_studies import _study


def test_a_failed_status_read_does_not_say_the_study_is_known(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "ubiquitin", means={"rmsd": (0.1, 0.01)})
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    rejected = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page()
            page.set_default_timeout(30000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXDashboard && "
                                   "window.FastMDXDashboard.state.statusAsked > 0")
            page.wait_for_timeout(1500)

            def reject_status(route):
                rejected.append(route.request.url)
                route.abort()

            page.route("**/api/status*", reject_status)
            page.evaluate("() => window.FastMDXDashboard.studyChangeAsked()")
            page.evaluate("() => window.FastMDXDashboard.studyChangeAnswered('')")
            page.wait_for_function(
                "() => window.FastMDXDashboard.state.apiErrors.status", timeout=30000)

            assert rejected, "the status route was not rejected"
            assert page.evaluate("() => window.FastMDXDashboard.studyKnown()") is False
            browser.close()
    finally:
        session.server.shutdown()
