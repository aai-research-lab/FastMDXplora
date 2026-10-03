"""Play, pressed first, plays from the first frame.

The box that follows a running study's newest frame is hidden for a
finished one, and was still ticked: the first Play loaded the frames at the
last one and, with nothing after it, stopped there. It now follows only
while the run writes frames, and Play at the end starts again from the first
frame, as a player does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("mdtraj")

STATE = "window.FastMDXMoleculeViewer.STATE"


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    study: Path = _helical_study(tmp_path_factory.mktemp("play") / "study", analyse=False)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            opened = browser.new_page(viewport={"width": 1440, "height": 900})
            opened.set_default_timeout(60000)
            opened.errors = []
            opened.on("pageerror", lambda error: opened.errors.append(str(error)))
            opened.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not opened.evaluate("() => !!document.createElement('canvas')"
                                   ".getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            opened.wait_for_function(f"() => {STATE}.model && {STATE}.runStatus === 'completed'")
            opened.evaluate("""() => {
                window.framesShown = [];
                window.addEventListener('dashboard:frame-shown',
                    (event) => window.framesShown.push(event.detail.index));
            }""")
            yield opened
            browser.close()
    finally:
        session.server.shutdown()


def _shown(page) -> list[int]:
    return page.evaluate("() => window.framesShown.slice()")


def test_the_first_play_of_a_finished_study_starts_at_the_first_frame(page):
    assert page.is_checked("#traj-follow") and page.is_hidden("#traj-follow")
    page.click('[data-action="play-trajectory"]')
    page.wait_for_function("() => window.framesShown.length >= 3")
    shown = _shown(page)
    assert shown[:3] == [0, 1, 2], shown
    page.wait_for_function(f"() => !{STATE}.playbackPlaying")
    assert _shown(page)[-1] == 5 and page.input_value("#traj-slider") == "5"


def test_play_at_the_last_frame_starts_again_from_the_first(page):
    page.evaluate("() => { window.framesShown = []; }")
    page.click('[data-action="play-trajectory"]')
    page.wait_for_function("() => window.framesShown.length >= 3")
    assert _shown(page)[:3] == [0, 1, 2]
    # Choosing where to play from is choosing not to follow the run.
    assert not page.is_checked("#traj-follow")
    page.wait_for_function(f"() => !{STATE}.playbackPlaying")
    assert page.errors == []


def test_a_running_study_s_frames_open_at_the_newest(page):
    # While the run writes frames, ticked Follow shows the newest. The run's
    # status held at running, so a poll of the finished study does not say
    # otherwise in the meantime.
    page.evaluate(f"""() => Object.defineProperty({STATE}, 'runStatus',
        {{get: () => 'running', set: () => undefined, configurable: true}})""")
    page.evaluate("() => { window.framesShown = [];"
                  " document.getElementById('traj-follow').checked = true; }")
    page.evaluate(f"""async () => {{
        {STATE}.playbackLoaded = false;
        {STATE}.playbackSignature = null;
        document.getElementById('traj-slider').value = '0';
        await window.FastMDXMoleculeViewer.loadPlayback(
            await (await fetch('/api/frames-info')).json());
    }}""")
    page.wait_for_function("() => window.framesShown.length >= 1")
    assert _shown(page)[-1] == 5
