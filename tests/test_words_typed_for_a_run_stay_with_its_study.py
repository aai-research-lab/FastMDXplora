"""Words typed for a run the Agent started stay with the run's study
(CI-774, third round).

Found by reviews K3 and K4: for a minute after a launch, words typed into
the Agent in the run's study were moved to any study opened next, one
another window opened too, and nothing was kept for the run's study. The
launch now answers the run's study, and only a switch to it takes the words.
"""

from __future__ import annotations

import json
import time
import urllib.request

import pytest

from tests.test_a_conversation_follows_a_run_that_wrote_nothing import (  # noqa: F401
    _CONFIG,
    _browsers_closed,
    _gui,
    _launches,
    _page_on,
    _say,
)


def test_the_launch_answers_the_run_s_study(tmp_path, monkeypatch) -> None:
    from fastmdxplora.gui.agent_panel import run_endpoint
    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.workspace import study_key

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    made = _launches(monkeypatch, workspace)
    runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    started = run_endpoint({"config": {"systems": [{"system": "1UAO"}]}}, runtime)
    assert started["ok"] and started["study_key"] == study_key(made[0])


def _switch_elsewhere(session, folder):
    """Another window (or an AI app) opens ``folder``."""
    base = session.url.split("#")[0].rstrip("/")
    asked = urllib.request.Request(base + "/api/explore/switch", method="POST",
                                   data=json.dumps({"folder": str(folder)}).encode(),
                                   headers={"content-type": "application/json"})
    with urllib.request.urlopen(asked, timeout=30) as answer:
        assert json.loads(answer.read()).get("ok") is not False


def _unsent(page):
    return page.evaluate("""() => {
        const kept = {};
        for (let i = 0; i < sessionStorage.length; i++) {
            const key = sessionStorage.key(i);
            if (key.startsWith('fmx.agentUnsent:')) {
                kept[key.slice('fmx.agentUnsent:'.length)] = JSON.parse(sessionStorage.getItem(key)).words;
            }
        }
        return kept;
    }""")


def _shows(page, key):
    page.wait_for_function("(key) => window.FastMDXDashboard.state.studyShown === key",
                           arg=key, timeout=30000)


def _other(workspace):
    other = workspace / "other"
    other.mkdir()
    (other / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    return other


def _when_known(page):
    """Once the page has read which study is open after a launch: Send is
    offered then, not while the run starts (CI-774 round 3)."""
    page.wait_for_function("() => { const d = window.FastMDXDashboard;"
                           " return d.studyKnown() && d.viewKnown()"
                           " && !window.FastMDXAgentPanel.waits; }", timeout=30000)
    page.wait_for_timeout(300)


def _said(page):
    return page.evaluate("() => (document.getElementById('agent-unsent-note') || {}).textContent || ''")


@pytest.mark.parametrize("taking", [8.0, 0.5], ids=["slow launch", "fast launch"])
def test_words_typed_once_a_first_run_started_stay_with_it(tmp_path, monkeypatch, taking):
    """The first run, started from no study (K3's two scripts)."""
    from fastmdxplora.gui.workspace import study_key

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=taking)
    other = _other(workspace)
    words = "words for run1"
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(800)
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        run = study_key(workspace / "run1")
        _shows(page, run)
        page.wait_for_selector("#agent-thread .agent-running")
        page.wait_for_timeout(1000)
        page.fill("#agent-request", words)
        page.wait_for_timeout(500)
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_timeout(1500)
        box = page.input_value("#agent-request")
        kept = _unsent(page)
        said = _said(page)
        close()
    finally:
        session.server.shutdown()
    assert made
    assert kept.get(run) == words, kept
    assert words not in box
    assert words not in kept.get(study_key(other), "")
    assert "is saved for it, not put here" in said, said
    assert errors == []


def test_words_typed_once_a_run_from_a_study_started_stay_with_it(tmp_path, monkeypatch):
    """A second run, started from the first's study, whose switch the page
    sees while the launch is still going (K4)."""
    from fastmdxplora.gui.workspace import study_key

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=2.0)
    other = _other(workspace)
    words = "words for the run X1"
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG), dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        _shows(page, study_key(workspace / "run1"))
        page.wait_for_selector("#agent-thread .agent-running")
        _when_known(page)
        _say(page, "again")
        page.wait_for_function(
            "() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 2")
        page.wait_for_timeout(500)
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run] >> nth=-1")
        run = study_key(workspace / "run2")
        _shows(page, run)
        page.wait_for_selector("#agent-thread .agent-running >> nth=1", timeout=30000)
        page.fill("#agent-request", words)
        page.wait_for_timeout(500)
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_timeout(1500)
        box = page.input_value("#agent-request")
        kept = _unsent(page)
        said = _said(page)
        close()
    finally:
        session.server.shutdown()
    assert kept.get(run) == words, kept
    assert words not in box
    assert words not in kept.get(study_key(other), "")
    assert "is saved for it, not put here" in said, said
    assert errors == []


def test_a_study_another_window_opens_during_a_launch_takes_no_words(tmp_path, monkeypatch):
    """Another window opens a study while the run is being launched: the
    words typed meanwhile are not the other study's (K4). Typed after Run
    into the thread the run took with it, they are the run's study's
    (L2: they were kept for the study the launch began in)."""
    from fastmdxplora.gui.workspace import study_key

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=5.0)
    other = _other(workspace)
    words = "words while it starts"
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG), dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        left = study_key(workspace / "run1")
        _shows(page, left)
        page.wait_for_selector("#agent-thread .agent-running")
        _when_known(page)
        _say(page, "again")
        page.wait_for_function(
            "() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 2")
        page.wait_for_timeout(500)
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run] >> nth=-1")
        page.focus("#agent-request")
        page.keyboard.type(words)
        time.sleep(1.0)
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        # The page on `other` reads its own thread, not the run's: what it
        # says once the launch has answered is where the words went.
        page.wait_for_function(
            "(key) => !!sessionStorage.getItem('fmx.agentUnsent:' + key)",
            arg=study_key(workspace / "run2"), timeout=30000)
        page.wait_for_timeout(1500)
        box = page.input_value("#agent-request")
        kept = _unsent(page)
        said = _said(page)
        close()
    finally:
        session.server.shutdown()
    run = study_key(workspace / "run2")
    assert words not in box
    assert words not in kept.get(study_key(other), "")
    assert words not in kept.get(left, ""), kept
    assert kept.get(run) == words, kept
    # Named by its folder alone, as every note names a study (L12 F8, L20).
    assert "The unsent message typed for run2 is saved for it, not put here" \
        in said, said
    assert errors == []
