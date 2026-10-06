"""Recent shortens a name in its middle and shows each study's state as a light.

A long name was cut at its end, where studies of one system differ, while
the active study's folder below it gave up its middle. And each state was a
grey word beside the name, the same for a study that finished and one that
failed. Now each name keeps its ends, and the state is a light where the
word was: green completed, amber stopped, red failed, an amber ring for one
that ended without saying so, a grey ring for one not started and the
accent while it runs. The word is on hover and read out with the name.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402
from tests.test_the_workspace_says_its_studies import _study  # noqa: E402

LONG = "trialanine_in_tip3p_with_150mM_NaCl_at_310K_replica_seven"


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    workspace = tmp_path_factory.mktemp("recent")
    _study(workspace / "done", system="1UBQ", started="2026-09-01T10:00:00+00:00")
    _study(workspace / "failed", system="5AWL", started="2026-09-02T10:00:00+00:00",
           failed="simulation.numerics.nan")
    _study(workspace / "stopped", system="2JOF", started="2026-09-03T10:00:00+00:00",
           failed="simulation.run.stopped")
    _study(workspace / "long", system=LONG, started="2026-09-04T10:00:00+00:00")
    ended = _study(workspace / "ended", system="1L2Y", started="2026-09-05T10:00:00+00:00")
    (ended / "simulation").mkdir()
    (ended / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "running", "stage": "production",
        "last_update_timestamp": (datetime.now(timezone.utc)
                                  - timedelta(days=21)).isoformat()}), encoding="utf-8")
    open_study = _write_study(workspace / "open")
    # The open study, newest; it keeps no start time, so its folder's.
    now = datetime.now(timezone.utc).timestamp()
    os.utime(open_study, (now, now))
    started = start_dashboard_session(output=str(open_study), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


@pytest.fixture(scope="module")
def recent(session):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        tab = browser.new_page(viewport={"width": 1440, "height": 900})
        tab.set_default_timeout(60000)
        tab.goto(session.url + "#overview", wait_until="domcontentloaded")
        tab.wait_for_function("() => document.body.classList.contains('state-ready') && "
                              "document.querySelectorAll('.sidebar-recent-item').length === 6")
        tab.evaluate("() => document.fonts.ready")
        said = tab.evaluate("""() => [...document.querySelectorAll('.sidebar-recent-item')]
            .map(item => {
                const name = item.querySelector('.sidebar-recent-name');
                const light = item.querySelector('.recent-light');
                const seen = getComputedStyle(light);
                return {
                    full: name.dataset.name, shown: name.textContent,
                    fits: name.scrollWidth <= name.clientWidth + 0.5,
                    state: light.dataset.state, hidden: light.getAttribute('aria-hidden'),
                    fill: seen.backgroundColor, ring: seen.borderTopColor,
                    ringWidth: seen.borderTopWidth, title: item.title,
                    read: item.getAttribute('aria-label') || '',
                    word: !!item.querySelector('.sidebar-recent-state'),
                    lightRight: light.getBoundingClientRect().left
                        > name.getBoundingClientRect().left,
                };
            })""")
        # The status colours, as the scheme declares them.
        colours = tab.evaluate("""() => {
            const probe = document.createElement('span');
            document.body.appendChild(probe);
            const of = (token) => { probe.style.color = `var(${token})`;
                                    return getComputedStyle(probe).color; };
            const found = Object.fromEntries(['--status-live', '--status-completed',
                '--status-waiting', '--status-error', '--status-stale'].map(t => [t, of(t)]));
            probe.remove();
            return found; }""")
        # Narrower, the long name is fitted again.
        long = """() => [...document.querySelectorAll('.sidebar-recent-name')]
            .find(n => n.dataset.name.startsWith('trialanine')).textContent"""
        wide = tab.evaluate(long)
        tab.evaluate("() => { document.querySelector('.sidebar').style.width = '200px'; }")
        tab.wait_for_function(f"() => ({long})() !== {json.dumps(wide)}")
        narrower = tab.evaluate(long)
        # Folded and opened again after it was built again while folded, it
        # is fitted again.
        tab.click("#sidebar-recent > summary")
        tab.evaluate("() => window.FastMDXStudies.showRecent(true)")
        tab.wait_for_timeout(500)
        tab.click("#sidebar-recent > summary")
        tab.wait_for_function(f"() => ({long})().includes('\\u2026')")
        browser.close()
    return said, colours, narrower


def _by_state(said: list[dict]) -> dict[str, dict]:
    return {row["state"]: row for row in said}


def test_each_state_is_a_light_of_its_own(recent) -> None:
    said, colours, _ = recent
    rows = _by_state(said)
    assert set(rows) == {"completed", "failed", "stopped", "interrupted", "not started"}
    # Filled for an end the study recorded.
    for state, token in (("failed", "--status-error"), ("stopped", "--status-waiting")):
        assert rows[state]["fill"] == colours[token], state
    assert rows["completed"]["fill"] == colours["--status-completed"]
    # A ring, in amber, for one that ended without saying so.
    assert rows["interrupted"]["fill"] == "rgba(0, 0, 0, 0)"
    assert rows["interrupted"]["ring"] == colours["--status-waiting"]
    assert rows["interrupted"]["ringWidth"] != "0px"
    # A grey ring for one not started.
    assert rows["not started"]["fill"] == "rgba(0, 0, 0, 0)"
    assert rows["not started"]["ring"] == colours["--status-stale"]
    assert len({colours[t] for t in colours}) == len(colours)


def test_the_word_is_on_hover_and_read_out_not_shown(recent) -> None:
    rows = _by_state(recent[0])
    for state, word in (("completed", "Completed"), ("failed", "Failed"),
                        ("stopped", "Stopped"), ("interrupted", "Interrupted"),
                        ("not started", "Not started")):
        row = rows[state]
        # The word over the study's folder.
        assert row["title"].startswith(word + "\n") and len(row["title"]) > len(word) + 2, \
            row["title"]
        # Read out whole, with its state, though shortened on screen.
        assert row["read"] == row["full"] + ", " + word
        assert row["hidden"] == "true" and not row["word"] and row["lightRight"]


def test_a_long_name_keeps_its_ends(recent) -> None:
    found, _, narrower = recent
    long = next(r for r in found if r["full"] == LONG)
    assert long["fits"] and "…" in long["shown"]
    head, tail = long["shown"].split("…")
    assert LONG.startswith(head) and LONG.endswith(tail) and len(head) >= len(tail)
    assert tail and len(head) - len(tail) <= 1
    assert "…" in narrower and len(narrower) < len(long["shown"])
    # A short name is left whole.
    assert all(r["shown"] == r["full"] for r in found if r["full"] != LONG)
