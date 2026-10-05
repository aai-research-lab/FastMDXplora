"""The GUI's Analysis page analyzes the study open again, after asking.

The GUI ran a study's phases only into a new, empty folder: a finished
study's analyses could be run again only from the command line. **Analyze
again** offers the analyses the study ran last, ticked, and every other one
this release has; says what is written again and what is kept aside; and
runs the phase command with `--rerun` on the study through the GUI's own
start rule, once its own button is pressed.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_a_phase_command_is_explore_with_one_phase import _analysed, _made  # noqa: E402

from fastmdxplora.gui.exploration import DashboardRuntime  # noqa: E402


def _runtime(root: Path) -> DashboardRuntime:
    runtime = DashboardRuntime(workspace_root=root.parent, exploration_root=root.parent)
    runtime.active_root = root
    return runtime


def test_the_analyses_chosen_are_run_again_in_the_study_open(tmp_path):
    root = _analysed(tmp_path / "study")
    with mock.patch.object(DashboardRuntime, "_spawn",
                           return_value={"launched": True}) as spawn:
        answer = _runtime(root).run_again(["analysis"], ["rmsd", "rg"])
    command, where, _ = spawn.call_args.args
    assert answer["ok"] and "The report is written again too" in answer["said"]
    assert command == [sys.executable, "-m", "fastmdxplora", "explore",
                       "--output", str(root.resolve()), "--include-phase", "analysis", "report",
                       "--rerun", "--analyze-analyses", "rmsd", "rg"]
    assert where == root


def test_a_refusal_is_said_at_once_and_nothing_starts(tmp_path):
    root = _analysed(tmp_path / "study")
    with mock.patch.object(DashboardRuntime, "_spawn") as spawn:
        unknown = _runtime(root).run_again(["analysis"], ["rmsdd"])
        in_place = _runtime(root).run_again(["simulation"])
    spawn.assert_not_called()
    assert unknown["code"] == "analysis.unknown" and "did you mean 'rmsd'" in unknown["error"]
    assert in_place["code"] == "config.option.not_permitted"


def test_the_page_is_told_what_can_be_run_again(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study", analyses=("rmsd", "rg"))
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        offered = json.loads(urllib.request.urlopen(
            session.url.rstrip("/") + "/api/again", timeout=30).read())
    finally:
        session.server.shutdown()
    assert offered["available"] and offered["analysis"]["can"]
    assert offered["recorded"] == ["rmsd", "rg"]
    titles = {item["name"]: item["title"] for item in offered["catalogue"]}
    assert titles["rmsd"] == "Root-mean-square deviation" and "sasa" in titles
    assert offered["previous"] == "previous"


def test_the_analysis_page_analyses_again_after_asking(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    asked: list[str] = []
    errors: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda r: asked.append(r.method) if r.url.endswith("/api/again")
                    else None)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#analysis-again:not([hidden])")
            page.click("#analysis-again")
            page.wait_for_selector("#analysis-again-ask .again-choices")
            ticked = page.eval_on_selector_all(
                "#analysis-again-ask input:checked", "boxes => boxes.map(b => b.value)")
            page.click("#analysis-again-ask .again-more summary")
            page.check("#analysis-again-ask input[value='rg']")
            said = page.text_content("#analysis-again-ask .again-said")
            assert "POST" not in asked          # nothing yet: asked first
            page.click("#analysis-again-ask .fix-confirm")
            page.wait_for_selector("#analysis-again-ask:has-text('Analyzed again.')",
                                   timeout=180000)
            browser.close()
    finally:
        session.server.shutdown()
    assert ticked == ["rmsd"]
    assert "with the 2 analyses ticked" in said and "Nothing is simulated." in said
    assert "kept in previous/" in said and "The report is written again too" in said
    assert asked.count("POST") == 1
    assert _made(root) == ["rmsd", "rg"] and _made(root / "previous") == ["rmsd"]
    assert errors == []
