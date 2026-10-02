"""The viewer offers its ligand controls where there is a ligand.

Centring on the ligand or its pocket, and showing either, did nothing on a
structure with no ligand and said nothing either. They are now disabled
there, with the reason as their title, and offered as before where there is
one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_a_ligand_needs_a_charge_provider import TRIPEPTIDE  # noqa: E402
from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402

CONTROLS = ('[data-cam="center-ligand"]', '[data-cam="center-pocket"]',
            '[data-vis="ligand"]', '[data-vis="pocket"]')


def _offered(study: Path) -> list[tuple[bool, str | None]]:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer && window.FastMDXMoleculeViewer.STATE.model")
            offered = [page.eval_on_selector(
                selector, "e => [e.disabled, (e.closest('label') || e).getAttribute('title')]")
                for selector in CONTROLS]
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    return [tuple(pair) for pair in offered]


def test_a_structure_with_no_ligand_says_why_they_are_off(tmp_path):
    study = tmp_path / "peptide"
    (study / "setup").mkdir(parents=True)
    (study / "setup" / "prepared.pdb").write_text(TRIPEPTIDE, encoding="utf-8")
    assert _offered(study) == [(True, "This structure has no ligand")] * len(CONTROLS)


def test_a_structure_with_one_offers_them(tmp_path):
    study = _write_study(tmp_path / "complex")
    assert [disabled for disabled, _ in _offered(study)] == [False] * len(CONTROLS)
