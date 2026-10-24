"""A toast is never over the Agent's composer or its Send button.

A toast is shown at the window's foot on the right, where the Agent's
composer and its Send are, beside a page or on the Agent page: "Saved the
view" covered the box being typed in, and an Undo in it took the click
meant for Send. Beside the Agent it is shown at the Agent's left; where the
Agent takes the whole width, and on the Agent page, at the top, above the
composer.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _write_study(tmp_path_factory.mktemp("toast") / "study")
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


WHERE = [(1440, 900, "#agent", False), (1440, 900, "#analysis", True),
         (1250, 800, "#analysis", True), (900, 800, "#analysis", True),
         (900, 800, "#agent", False), (390, 844, "#analysis", True),
         (390, 844, "#agent", False)]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width, height, where, beside", WHERE,
                         ids=[f"{w}-{p.strip('#')}{'-beside' if b else ''}"
                              for w, _, p, b in WHERE])
def test_a_toast_is_shown_clear_of_the_composer_and_send(browser, session, width, height,
                                                         where, beside, scheme):
    context = browser.new_context(viewport={"width": width, "height": height},
                                  color_scheme=scheme)
    tab = context.new_page()
    tab.set_default_timeout(60000)
    errors: list[str] = []
    tab.on("pageerror", lambda error: errors.append(str(error)))
    tab.goto(session.url + where, wait_until="domcontentloaded")
    tab.wait_for_function("() => window.FastMDXAgentBeside && "
                          "document.body.classList.contains('state-ready')")
    if beside:
        tab.evaluate("() => window.FastMDXAgentBeside.open()")
        tab.wait_for_function("() => !document.getElementById('agent-drawer').hidden")
    tab.evaluate("""() => window.FastMDXDashboard.toast(
        'Saved the view "pocket, superposed on the backbone" with the study.', 'ok',
        {label: 'Undo', run() {}})""")
    tab.wait_for_function("""() => {
        const toast = document.getElementById('dashboard-toast');
        return toast && getComputedStyle(toast).opacity === '1'
            && getComputedStyle(toast).transform.replace(/\\s/g, '') === 'matrix(1,0,0,1,0,0)';
    }""")
    said = tab.evaluate("""() => {
        const box = (node) => { const r = node.getBoundingClientRect();
            return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; };
        const composer = [...document.querySelectorAll('.agent-composer')]
            .find((node) => node.offsetParent !== null);
        return {toast: box(document.getElementById('dashboard-toast')),
                composer: composer ? box(composer) : null,
                send: composer ? box(composer.querySelector('.agent-send')) : null,
                page: document.documentElement.dataset.page,
                width: document.documentElement.clientWidth};
    }""")
    context.close()
    assert errors == []
    assert said["composer"] is not None, said

    def apart(a, b):
        return (a["right"] <= b["left"] or b["right"] <= a["left"]
                or a["bottom"] <= b["top"] or b["bottom"] <= a["top"])

    assert apart(said["toast"], said["composer"]), said
    assert apart(said["toast"], said["send"]), said
    # In the window, whole.
    assert 0 <= said["toast"]["left"] and said["toast"]["right"] <= said["width"], said
    assert said["toast"]["top"] >= 0, said
