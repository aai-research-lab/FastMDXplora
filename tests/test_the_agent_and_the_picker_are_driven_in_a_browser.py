"""The Agent's send path and the file picker, driven in a browser.

Both were checked only by reading their scripts: the Agent's thread was
tested from a conversation restored whole, never from a message typed and
sent, and the picker not at all. Driving them found that the picker put the
server's refusal into the page as markup, and the refusal names the path it
was asked for, which a config written by somebody else can supply: "No such
folder: <img src=x onerror=...>" ran.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("mdtraj")


@pytest.fixture
def study(tmp_path) -> Path:
    from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis, _manifest, _series
    from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES, _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, saving_interval_ps=2.0)
    _analysis(root, "rmsd", _series(FRAMES), {
        "mean": 0.112, "standard_error": 0.002, "effective_samples": 11.0, "discard": 5,
        "n_frames": FRAMES, "unit": "nm"})
    (root / "notes.txt").write_text("The ligand is benzamidine, bound at Asp189.\n",
                                    encoding="utf-8")
    return root


def _browser(pw):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.errors = errors
    return browser, page


def test_a_question_typed_and_sent_is_answered_from_the_study(study, monkeypatch) -> None:
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    import fastmdxplora.agent as agent_mod
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return "SAY: The RMSD settled at 0.112 nm after equilibration."

    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: answer)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page = _browser(pw)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            # A file attached through the picker, opened on the study's folder.
            page.click("#agent-attach")
            page.wait_for_selector("#fastmdx-picker:not([hidden]) .fastmdx-picker-row")
            page.click('#fastmdx-picker .fastmdx-picker-row:has-text("notes.txt")')
            page.wait_for_selector("#agent-attachments :text('notes.txt')")
            page.fill("#agent-request", "Did the RMSD settle?")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-answer")
            said = page.text_content("#agent-thread .agent-answer")
            cite = page.text_content('#agent-thread .agent-cite[data-analysis="rmsd"]')
            browser.close()
    finally:
        session.server.shutdown()
    assert said == "The RMSD settled at 0.112 nm after equilibration."
    assert cite == "RMSD0.1120 ± 0.0020 nm"
    # The model was asked the question with the file and the study's record.
    assert "Did the RMSD settle?" in prompts[-1]
    assert "The ligand is benzamidine, bound at Asp189." in prompts[-1]
    # The model reads the mean as the person does under the answer.
    assert "rmsd: mean 0.1120 ± 0.0020 nm (s.e.)" in prompts[-1]
    assert page.errors == []
    # And the thread was kept with the study, the answer's citations with it.
    kept = list((study / "agent" / "conversations").glob("*.json"))
    assert kept
    entries = json.loads(kept[0].read_text(encoding="utf-8"))["entries"]
    assert entries[-1]["kind"] == "answer" and entries[-1]["cites"][0]["analysis"] == "rmsd"


def test_the_picker_walks_the_folders_and_fills_its_field(study) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page = _browser(pw)
            page.goto(session.url + "#run", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXPicker")
            page.evaluate("""where => {
                const field = document.createElement('input');
                field.id = 'picked'; document.body.appendChild(field);
                window.FastMDXPicker.open({into: 'picked', mode: 'file', start: where});
            }""", str(study))
            page.wait_for_selector("#fastmdx-picker:not([hidden]) .fastmdx-picker-row")
            shown = page.text_content("#fastmdx-picker-path")
            study_row = page.locator('#fastmdx-picker .fastmdx-picker-row:has-text("simulation")')
            study_row.click()
            page.wait_for_function(
                "() => document.getElementById('fastmdx-picker-path').textContent.endsWith('simulation')")
            page.click('#fastmdx-picker .fastmdx-picker-row:has-text("trajectory_topology.pdb")')
            page.wait_for_function("() => document.getElementById('picked').value !== ''")
            picked = page.input_value("#picked")
            closed = page.is_hidden("#fastmdx-picker")
            browser.close()
    finally:
        session.server.shutdown()
    assert shown == str(study.resolve())
    assert picked == str((study / "simulation" / "trajectory_topology.pdb").resolve())
    assert closed
    assert page.errors == []


def test_a_path_the_picker_cannot_open_is_said_as_text(study) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    where = str(study / 'nowhere<img src=x onerror="window.ran=1">')
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page = _browser(pw)
            page.goto(session.url + "#run", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXPicker")
            page.evaluate("where => window.FastMDXPicker.open({into: 'nothing', start: where})", where)
            page.wait_for_function(
                "() => document.querySelector('#fastmdx-picker-list .empty-detail')")
            said = page.text_content("#fastmdx-picker-list .empty-detail")
            images = page.locator("#fastmdx-picker-list img").count()
            ran = page.evaluate("() => window.ran === 1")
            browser.close()
    finally:
        session.server.shutdown()
    assert said.startswith("No such folder: ") and said.endswith('onerror="window.ran=1">')
    assert images == 0
    assert not ran
