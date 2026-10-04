"""The Agent proposes a scene; the person writes it with a button.

An answer about something that can be seen (a frame, residues that move, a
colouring by one of the study's results) may end with a `SHOW:` line. It is
read by a strict pattern, shown under the answer in words, and written with
the study as a scene file only when the person presses Write this scene: the
Agent's tools only look, and this only offers. A part the pattern does not
allow drops the proposal, never the answer.
"""

from __future__ import annotations

import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.agent.propose import _scene_in, prompt_for, propose_config


@pytest.mark.parametrize("line, scene", [
    ("SHOW: frame 3; colour result:rmsd; highlight resSeq 2 to 4; labels yes; name late",
     {"frame": 3, "colour": "result:rmsd", "highlight": "resSeq 2 to 4", "labels": True,
      "name": "late"}),
    ("show: representation ball and stick; superposed pocket; colour Secondary_Structure",
     {"representation": "ballAndStick", "superposed": "pocket",
      "colour": "secondary_structure"}),
    ("SHOW: representation spheres", {"representation": "spacefill"}),
])
def test_a_show_line_is_read_whole(line, scene):
    said, found = _scene_in(f"The loop opens late.\n{line}")
    assert said == "The loop opens late." and found == scene


@pytest.mark.parametrize("line", [
    "SHOW: frame forty", "SHOW: frame 3; frame 4", "SHOW: colour purple",
    "SHOW: colour result:RMSF; frame", "SHOW: highlight `rm -rf`", "SHOW: zoom 3",
    "SHOW: superposed elsewhere", "SHOW: labels maybe", "SHOW: name ../up", "SHOW:",
])
def test_a_part_not_allowed_drops_the_proposal_not_the_answer(line):
    assert _scene_in(f"The answer.\n{line}") == ("The answer.", None)


def test_an_answer_without_one_is_left_alone():
    assert _scene_in("Just an answer.\nOn two lines.") == ("Just an answer.\nOn two lines.",
                                                          None)
    assert _scene_in("SHOW: frame 2")[0] == "…"


def test_the_agent_is_told_how_and_the_loop_carries_it():
    prompt = prompt_for("which loop moves most?")
    assert "SHOW: frame 40; colour result:rmsf; highlight resSeq 20 to 25" in prompt
    proposal = propose_config(
        "which loop moves most?",
        lambda prompt: "SAY: Residues 2 to 4 move most.\nSHOW: colour result:rmsf; "
                       "highlight resSeq 2 to 4")
    assert proposal.answer == "Residues 2 to 4 move most."
    assert proposal.scene == {"colour": "result:rmsf", "highlight": "resSeq 2 to 4"}
    assert proposal.config is None and proposal.action is None


def test_it_is_offered_only_with_a_study_open(tmp_path):
    from fastmdxplora.gui.agent_panel import _proposal_answer

    proposal = SimpleNamespace(action=None, answer="Two to four.", question=None,
                               scene={"frame": 2}, attempts=(), accepted=False)
    study = tmp_path / "study"
    (study / "simulation").mkdir(parents=True)
    with_study = _proposal_answer(proposal, {}, SimpleNamespace(active_root=study), "q", "x")
    without = _proposal_answer(proposal, {}, SimpleNamespace(active_root=None), "q", "x")
    assert with_study["scene"] == {"frame": 2} and "scene" not in without


@pytest.fixture
def study(tmp_path) -> Path:
    pytest.importorskip("mdtraj")
    from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis, _manifest, _series
    from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES, _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, saving_interval_ps=2.0)
    _analysis(root, "rmsd", _series(FRAMES), {
        "mean": 0.112, "standard_error": 0.002, "effective_samples": 11.0, "discard": 5,
        "n_frames": FRAMES, "unit": "nm"})
    return root


def test_the_route_writes_the_atoms_it_highlights(study, monkeypatch):
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        request = urllib.request.Request(
            base + "/api/scenes", method="POST", headers={"content-type": "application/json",
                                                         "origin": base},
            data=json.dumps({"name": "shown", "view": {"frame": 3, "colour": "chain"},
                             "highlight": "resSeq 2 to 4", "labels": True}).encode())
        said = json.loads(urllib.request.urlopen(request, timeout=60).read())
    finally:
        session.server.shutdown()
    assert said["ok"], said
    state = json.loads(zipfile.ZipFile(study / "scenes" / "shown.mvsx").read("index.mvsj"))
    chosen = state["root"]["custom"]["fastmdxplora"]["selections"]
    assert {"name": "highlight", "expression": "resSeq 2 to 4", "colour": "#e69f00",
            "labelled": True}.items() <= next(s for s in chosen
                                               if s["name"] == "highlight").items()


def test_the_person_writes_it_and_sees_it(study, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    import fastmdxplora.agent as agent_mod
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (
        lambda prompt: "SAY: Residues 2 to 4 move most, late in the run.\n"
                       "SHOW: frame 3; colour chain; highlight resSeq 2 to 4; name late loop"))
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(90000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.fill("#agent-request", "Which residues move most?")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-scene")
            answer = page.text_content("#agent-thread .agent-answer")
            offered = page.text_content("#agent-thread .agent-scene-said")
            written_before = (study / "scenes").exists()
            page.click("#agent-thread .agent-scene button:has-text('Write this scene')")
            page.wait_for_function("() => location.hash === '#viewer'")
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer.STATE.engine"
                " && window.FastMDXMoleculeViewer.STATE.engine.frame() === 3")
            colour = page.evaluate("() => window.FastMDXMoleculeViewer.STATE.colorMode")
            # The thread kept says it was written.
            page.goto(session.url + "#agent")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#agent-thread .agent-scene")
            kept = page.text_content("#agent-thread .agent-scene-status")
            disabled = page.is_disabled("#agent-thread .agent-scene button:has-text('Written')")
            browser.close()
    finally:
        session.server.shutdown()
    assert answer == "Residues 2 to 4 move most, late in the run."
    assert offered == ("A scene to show this: frame 3, coloured by chain, resSeq 2 to 4 "
                       "highlighted.")
    assert not written_before and (study / "scenes" / "late loop.mvsx").is_file()
    assert colour == "chain"
    assert kept == "Written as scenes/late loop.mvsx." and disabled
    assert errors == []
