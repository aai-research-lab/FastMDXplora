"""The Problems panel reads records, opens evidence and follows the study."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from fastmdxplora.statistics import MEAN_RULES  # noqa: E402


def _write_study(root: Path, why: str | None = "Only four independent samples.") -> Path:
    folder = root / "analysis" / "rmsd"
    folder.mkdir(parents=True)
    (root / "simulation").mkdir()
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "analysis", "status": "completed"}]}), encoding="utf-8")
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "stage": "production"}), encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "results": {"rmsd": {"status": "success"}}}), encoding="utf-8")
    mean = ({"mean": 0.12, "unit": "nm", "effective_samples": 4, "not_a_measurement": why}
            if why else {"mean": 0.12, "standard_error": 0.01, "unit": "nm",
                         "n_frames": 200, "effective_samples": 100, "rules": MEAN_RULES})
    (folder / "options.json").write_text(json.dumps({"findings": {"mean": mean}}),
                                         encoding="utf-8")
    _narrated(root)
    return root


def _narrated(root: Path) -> None:
    """The narration and the log every run writes beside its records."""
    from fastmdxplora.gui.telemetry import TelemetryWriter
    from fastmdxplora.utils.logging import attach_file_logger, detach_file_logger, get_logger

    TelemetryWriter(root / "simulation").event("Production started")
    attach_file_logger(root / "fastmdxplora.log", level=logging.DEBUG)
    try:
        get_logger("orchestrator").info("Plan: setup -> simulation -> analysis")
    finally:
        detach_file_logger(root / "fastmdxplora.log")


def _running(root: Path) -> None:
    """A run going now: its record just written, its process this one."""
    from datetime import datetime, timezone

    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, this_machine

    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "running", "stage": "production",
        "last_update_timestamp": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    (root / RUN_PROCESS_FILE).write_text(json.dumps({"pid": os.getpid(), **this_machine()}),
                                         encoding="utf-8")


@pytest.fixture(scope="module")
def sessions(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    base = tmp_path_factory.mktemp("problems")
    studies = {"first": _write_study(base / "first"),
               "second": _write_study(base / "second", "Second study's reason."),
               "clean": _write_study(base / "clean", None),
               "no-manifest": _write_study(base / "no-manifest", None),
               "vanishing": _write_study(base / "vanishing", None),
               "running": _write_study(base / "running", None),
               "empty": base / "empty",
               "not-a-study": base / "not-a-study"}
    (studies["no-manifest"] / "manifest.json").unlink()
    (studies["running"] / "manifest.json").unlink()
    studies["empty"].mkdir()
    # Files, none of them FastMDXplora's: no study is open.
    studies["not-a-study"].mkdir()
    (studies["not-a-study"] / "notes.txt").write_text("Papers to read.\n", encoding="utf-8")
    sessions = {name: start_dashboard_session(output=str(path), host="127.0.0.1", port=0)
                for name, path in studies.items()}
    yield sessions, studies
    for session in sessions.values():
        session.stop()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _open(browser, session, *, width=1440, theme="dark"):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script(
        f"localStorage.setItem('fmx.theme', '{theme}');"
        "localStorage.setItem('fmx.panelCollapsed', '0');")
    page = context.new_page()
    page.set_default_timeout(15000)
    page.goto(session.url + "#overview", wait_until="domcontentloaded")
    page.wait_for_function("() => !!window.FastMDXDashboard?.state.appState.mode")
    page.wait_for_function("() => document.getElementById('loading-screen').getAttribute('aria-hidden') === 'true'")
    if width <= 800:
        page.get_by_role("button", name="Study problems", exact=True).click()
    elif page.locator("#side-expand").is_visible():
        page.locator("#side-expand").click()
    page.locator("#problems-tab").click()
    return page


@pytest.mark.parametrize("width", [390, 900, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_recorded_reason_and_remedy_fit_the_panel(browser, sessions, width, theme):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["first"], width=width, theme=theme)
    try:
        expect(page.locator("#problems-summary")).to_have_text("1 recorded problem")
        expect(page.locator("#problems-body")).to_contain_text("Only four independent samples.")
        expect(page.locator("#problems-body h4")).to_have_text("What would fix it")
        expect(page.locator("#problems-tab")).to_have_attribute("aria-selected", "true")
        assert page.evaluate("""() => {
            const pane = document.getElementById('problems-pane');
            const panel = pane.getBoundingClientRect();
            return panel.left >= -1 && panel.right <= innerWidth + 1
              && pane.scrollWidth <= pane.clientWidth + 1
              && [...pane.querySelectorAll('h3,h4,p,code,button')].every(el => {
                  const r = el.getBoundingClientRect();
                  return r.left >= panel.left - 1 && r.right <= panel.right + 1;
              });
        }""")
        assert page.locator("html").get_attribute("data-theme") == theme
    finally:
        page.context.close()


def test_evidence_opens_in_the_existing_files_reader(browser, sessions):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["first"])
    try:
        page.get_by_role("button", name="Open analysis/rmsd/options.json in Files", exact=True).click()
        expect(page.locator('[data-side-tab="files"]')).to_have_attribute("aria-selected", "true")
        expect(page.locator("#side-preview-name")).to_contain_text("options.json")
        expect(page.locator("#side-preview-body")).to_contain_text("Only four independent samples.")
    finally:
        page.context.close()


def test_phone_evidence_and_escape_keep_the_panel_usable(browser, sessions):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["first"], width=390)
    try:
        page.get_by_role("button", name="Open analysis/rmsd/options.json in Files", exact=True).click()
        expect(page.locator("#side-preview-body")).to_contain_text("Only four independent samples.")
        page.keyboard.press("Escape")
        expect(page.locator("#problems-mobile")).not_to_have_attribute("open", "")
        expect(page.get_by_role("button", name="Study problems", exact=True)).to_be_focused()
        page.get_by_role("button", name="Study problems", exact=True).click()
        expect(page.locator("#problems-summary")).to_have_text("1 recorded problem")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_function("() => !document.getElementById('problems-mobile').open")
        expect(page.locator(".app-shell > #side-panel")).to_have_count(1)
    finally:
        page.context.close()


def test_recorded_markup_is_shown_as_text(browser, sessions):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["first"])
    try:
        page.route("**/api/problems?*", lambda route: route.fulfill(json={
            "ok": True, "study": page.evaluate("new URL(location.href).searchParams.get('study')"),
            "active_run": str(sessions[1]["first"]), "available": True, "notes": [],
            "items": [{"id": "x", "title": "<img src=x onerror=alert(1)>",
                       "reason": "<script>window.bad=true</script>", "fix": "<b>Read</b>"}]}))
        page.get_by_role("button", name="Refresh problems", exact=True).click()
        expect(page.locator("#problems-body")).to_contain_text("<script>window.bad=true</script>")
        assert page.locator("#problems-body img, #problems-body script, #problems-body b").count() == 0
        assert page.evaluate("window.bad === undefined")
    finally:
        page.context.close()


def test_a_failed_read_is_visible_and_can_be_retried(browser, sessions):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["first"])
    try:
        expect(page.locator("#problems-summary")).to_have_text("1 recorded problem")
        page.route("**/api/problems?*", lambda route: route.fulfill(status=500, body="unavailable"))
        page.get_by_role("button", name="Refresh problems", exact=True).click()
        # Said once, in the page's one block (its title; no reason was given).
        expect(page.locator("#problems-body .fmx-state[data-kind='error']")).to_have_text(
            "The problems could not be readTry again")
        expect(page.locator("#problems-summary")).to_have_text("")
        assert page.locator(".problem-item").count() == 0
        # The server's reason, where it gives one, is what the block says.
        page.unroute("**/api/problems?*")
        page.route("**/api/problems?*", lambda route: route.fulfill(
            status=503, json={"ok": False, "reason": "The records are being written."}))
        page.get_by_role("button", name="Refresh problems", exact=True).click()
        expect(page.locator("#problems-body .fmx-state-detail")).to_have_text(
            "The records are being written.")
        page.unroute("**/api/problems?*")
        page.get_by_role("button", name="Refresh problems", exact=True).click()
        expect(page.locator("#problems-summary")).to_have_text("1 recorded problem")
    finally:
        page.context.close()


def test_a_late_response_cannot_restore_the_previous_study(browser, sessions):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["first"])
    try:
        expect(page.locator("#problems-summary")).to_have_text("1 recorded problem")
        # Let the HTTP request reach the old study, but hold its reply while
        # app-state moves on. The promise must remain harmless after that.
        page.evaluate("""() => {
            const original = window.fetch;
            window.__releaseProblems = null;
            window.fetch = (...args) => {
                const answer = original(...args);
                if (!String(args[0]).startsWith('/api/problems')) return answer;
                return answer.then(response => new Promise(resolve => {
                    window.__releaseProblems = () => resolve(response);
                }));
            };
        }""")
        page.get_by_role("button", name="Refresh problems", exact=True).click()
        page.wait_for_function("() => !!window.__releaseProblems")
        page.evaluate("""() => window.FastMDXDashboard.applyAppState({
            mode: 'run', active_run: '/different-study', status: 'idle'
        })""")
        assert page.locator(".problem-item").count() == 0
        page.evaluate("() => window.__releaseProblems()")
        expect(page.locator("#problems-body .fmx-state")).to_contain_text("Reading the study's records")
        assert page.locator(".problem-item").count() == 0
        expect(page.get_by_role("button", name="Refresh problems", exact=True)).to_be_enabled()
    finally:
        page.context.close()


def test_no_study_does_not_claim_a_clean_bill_of_health(browser, sessions):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["not-a-study"])
    try:
        expect(page.locator("#problems-body .fmx-state")).to_have_text("Open a study to read its problems.")
        expect(page.locator("#problems-summary")).to_have_text("")
        assert page.locator(".problem-item").count() == 0
    finally:
        page.context.close()


def test_a_folder_a_study_is_about_to_be_in_has_no_records_yet(browser, sessions):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["empty"])
    try:
        expect(page.locator("#problems-summary")).to_have_text(
            "No records yet. A study's problems are read from the records its run writes, "
            "and this folder holds none so far.")
        assert page.locator(".problem-item").count() == 0
    finally:
        page.context.close()


def test_a_healthy_running_study_has_no_record_said_unavailable(browser, sessions):
    from playwright.sync_api import expect

    _running(sessions[1]["running"])
    page = _open(browser, sessions[0]["running"])
    try:
        expect(page.locator("#problems-summary")).to_have_text(
            "No problems listed in the records read.")
        expect(page.locator("#problems-body")).to_contain_text(
            "manifest.json is written when the run ends")
    finally:
        page.context.close()


def test_another_study_opened_shows_its_own_list_never_beside_the_first(browser, sessions):
    from playwright.sync_api import expect

    from fastmdxplora.gui.workspace import study_key

    first, second = sessions[1]["first"], sessions[1]["second"]
    page = _open(browser, sessions[0]["first"])
    try:
        expect(page.locator("#problems-body")).to_contain_text("Only four independent samples.")
        # Every text the pane shows from here on, as it changes.
        page.evaluate("""() => {
            window.__shown = [];
            const body = document.getElementById('problems-pane');
            new MutationObserver(() => window.__shown.push(body.textContent))
                .observe(body, {subtree: true, childList: true, characterData: true});
        }""")
        page.evaluate("""(folder) => fetch('/api/explore/switch', {method: 'POST',
            headers: {'content-type': 'application/json'}, body: JSON.stringify({folder})})""",
                      str(second))
        page.wait_for_function("(key) => window.FastMDXDashboard.state.studyShown === key",
                               arg=study_key(second))
        expect(page.locator("#problems-body")).to_contain_text("Second study's reason.")
        shown = page.evaluate("window.__shown")
        assert not any("Only four" in text and "Second study's" in text for text in shown)
        new = next(i for i, text in enumerate(shown) if "Second study's" in text)
        assert not any("Only four" in text for text in shown[new:])
        assert any("Only four" not in text and "Second study's" not in text
                   for text in shown[:new])
    finally:
        page.evaluate("""(folder) => fetch('/api/explore/switch', {method: 'POST',
            headers: {'content-type': 'application/json'}, body: JSON.stringify({folder})})""",
                      str(first))
        page.context.close()


def test_the_endpoint_refuses_to_read_a_different_study(sessions):
    from urllib.request import urlopen

    with urlopen(sessions[0]["first"].url + "/api/problems?study=wrong", timeout=30) as response:
        answer = json.load(response)
    assert answer["ok"] is False
    assert answer["reason"] == "The open study changed."
    assert "items" not in answer


def test_an_empty_list_says_so_and_names_the_records_it_read(browser, sessions):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["clean"])
    try:
        expect(page.locator("#problems-summary")).to_have_text(
            "No problems listed in the records read.")
        expect(page.locator("#problems-body .problems-read")).to_have_text(
            "Records read: manifest.json, simulation/live_status.json, "
            "simulation/live_events.log, fastmdxplora.log, "
            "analysis/analysis_manifest.json, analysis/rmsd/options.json.")
        assert page.locator(".problem-item").count() == 0
        # The status bar says it as the panel does.
        expect(page.locator("#sb-problems .sb-text")).to_have_text("No problems")
    finally:
        page.context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_a_missing_record_is_said_and_never_as_no_problems(browser, sessions, width):
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["no-manifest"], width=width)
    try:
        summary = page.locator("#problems-summary")
        expect(summary).to_have_text(
            "Some records are unavailable. The 5 records read list no problems.")
        expect(page.locator("#problems-body")).to_contain_text("manifest.json is not there")
        expect(page.locator("#problems-body .problems-read")).to_have_text(
            "Records read: simulation/live_status.json, simulation/live_events.log, "
            "fastmdxplora.log, analysis/analysis_manifest.json, analysis/rmsd/options.json.")
        # Nor in the status bar: a count of none is said only of every record read.
        expect(page.locator("#sb-problems")).to_be_hidden()
        assert "No problems" not in page.locator("#sb-problems").text_content()
    finally:
        page.context.close()


def test_a_study_folder_gone_is_said_without_offering_refresh(browser, sessions):
    from playwright.sync_api import expect

    folder = sessions[1]["vanishing"]
    page = _open(browser, sessions[0]["vanishing"])
    try:
        expect(page.locator("#problems-summary")).to_have_text(
            "No problems listed in the records read.")
        folder.rename(folder.with_name("vanished"))
        page.get_by_role("button", name="Refresh problems", exact=True).click()
        expect(page.locator("#problems-summary")).to_have_text(
            "The study's folder is no longer there, so its records cannot be read.")
        assert page.locator(".problem-item").count() == 0
    finally:
        page.context.close()
        folder.with_name("vanished").rename(folder)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_agent_beside_takes_the_side_panels_place_and_gives_it_back(
        browser, sessions, theme):
    """With the Agent beside a page, the Agent stands where the side panel
    was (the layout's design), so Log, Files and Problems wait behind it.
    At about 1250px nothing runs past the window, and closing the Agent
    brings the side panel back with Problems as it was."""
    from playwright.sync_api import expect

    page = _open(browser, sessions[0]["first"], width=1250, theme=theme)
    try:
        expect(page.locator("#problems-summary")).to_have_text("1 recorded problem")
        page.locator("#agent-beside-open").click()
        page.wait_for_function("() => !document.getElementById('agent-drawer').hidden")
        page.wait_for_function("() => document.activeElement.id === 'agent-request'")
        past = page.evaluate("""() => {
            const out = [];
            if (document.documentElement.scrollWidth > innerWidth + 1) out.push('page scrolls sideways');
            for (const el of document.querySelectorAll('.app-shell *, #agent-drawer *')) {
                const r = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                if (!r.width || !r.height || style.visibility !== 'visible') continue;
                if (el.closest('[hidden]')) continue;
                if (r.left < -1 || r.right > innerWidth + 1) out.push(el.id || el.className);
            }
            return out;
        }""")
        assert past == []
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('agent-drawer').hidden")
        expect(page.locator("#side-panel")).to_be_visible()
        expect(page.locator("#problems-tab")).to_have_attribute("aria-selected", "true")
        expect(page.locator("#problems-pane")).to_be_visible()
        expect(page.locator("#problems-summary")).to_have_text("1 recorded problem")
        box = page.locator("#problems-pane").bounding_box()
        assert box is not None and box["width"] > 200 and box["x"] + box["width"] <= 1251
    finally:
        page.context.close()
