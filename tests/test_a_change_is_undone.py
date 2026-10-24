"""A change that can be undone is offered Undo in the notice after it.

Tags and a note kept on All studies, and a view saved in the Viewer or
forgotten there, were final: a slip took the person retyping what was
there, or setting the view up again by hand. The notice that says the
change now carries Undo for a while, which puts back what was there before;
a view saved over another is that one again, a new one goes.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from tests import viewer_hooks as hooks
from tests.test_the_workspace_says_its_studies import _study

pytest.importorskip("playwright.sync_api")

from fastmdxplora.gui.workspace import study_key  # noqa: E402

CAMERA = {"position": [1.0, 2.0, 30.0], "target": [1.0, 2.0, 3.0], "up": [0.0, 1.0, 0.0],
          "radius": 12.5, "fov": 0.785, "mode": "perspective"}


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.study_tags import set_tags

    workspace = tmp_path_factory.mktemp("undo")
    first = _study(workspace / "wild_type", started="2026-09-02T10:00:00+00:00")
    _study(workspace / "mutant", system="1UBQ", started="2026-09-03T10:00:00+00:00")
    set_tags(first, ["reference"], "the wild type, 300 K")
    started = start_dashboard_session(output=str(first), host="127.0.0.1", port=0)
    started.workspace, started.first = workspace, first
    yield started
    started.server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _page(browser, session, where):
    page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
    page.set_default_timeout(60000)
    # All studies opens on its list; these read its cards, as a person who
    # chose them would.
    page.add_init_script("try { localStorage.setItem('fmx.studiesView', 'cards'); } catch (e) {}")
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.goto(session.url + where, wait_until="domcontentloaded")
    return page


def _views(session):
    with urllib.request.urlopen(session.url.rstrip("/") + "/api/views", timeout=30) as answer:
        return json.loads(answer.read())["views"]


def _save(session, name, view):
    base = session.url.rstrip("/")
    request = urllib.request.Request(base + "/api/views", data=json.dumps(
        {"action": "save", "name": name, "view": view}).encode(),
        headers={"Content-Type": "application/json", "Origin": base}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as answer:
        assert json.loads(answer.read())["ok"]


def _undo(page, said):
    page.wait_for_selector(f"#dashboard-toast.show:has-text('{said}') .dashboard-toast-action")
    page.click("#dashboard-toast .dashboard-toast-action")


def test_tags_and_a_note_kept_are_undone(browser, session) -> None:
    from fastmdxplora.study_tags import tags_of

    before = tags_of(session.first)
    page = _page(browser, session, "#studies")
    page.evaluate(f"() => window.FastMDXStudies.load({str(session.workspace)!r})")
    card = '.study-card[data-path$="wild_type"]'
    page.wait_for_selector(card)
    page.click(f"{card} .study-tag-edit")
    page.fill(f"{card} .study-tag-input", "slip")
    page.fill(f"{card} .study-note-input", "a note typed over the other")
    page.click(f"{card} .study-tag-editor button:has-text('Save')")
    page.wait_for_selector("#dashboard-toast.show:has-text('Saved the tags')")
    page.wait_for_selector(f"{card} .study-tag:has-text('slip')")
    kept = tags_of(session.first)
    _undo(page, "Saved the tags and note")
    page.wait_for_selector("#dashboard-toast:has-text('back as they were')")
    shown = page.text_content(card)
    errors = page.errors
    page.context.close()
    assert "slip" in kept["tags"]
    assert tags_of(session.first) == before
    assert "slip" not in shown and "the wild type, 300 K" in shown
    assert errors == []


def test_tags_undone_after_the_list_was_read_again_show_on_the_card(browser, session) -> None:
    """All studies left and opened again before Undo: the server's tags went
    back, the card kept the undone ones, and the next save wrote them back."""
    from fastmdxplora.study_tags import tags_of

    before = tags_of(session.first)
    page = _page(browser, session, "#studies")
    page.evaluate(f"() => window.FastMDXStudies.load({str(session.workspace)!r})")
    card = '.study-card[data-path$="wild_type"]'
    page.wait_for_selector(card)
    page.click(f"{card} .study-tag-edit")
    page.fill(f"{card} .study-tag-input", "zeta")
    page.click(f"{card} .study-tag-editor button:has-text('Save')")
    page.wait_for_selector(f"{card} .study-tag:has-text('zeta')")
    page.evaluate("() => window.FastMDXDashboard.navigate('overview')")
    page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
    page.evaluate(f"() => window.FastMDXStudies.load({str(session.workspace)!r})")
    page.wait_for_selector(f"{card} .study-tag:has-text('zeta')")
    _undo(page, "Saved the tags and note")
    page.wait_for_selector("#dashboard-toast:has-text('back as they were')")
    page.wait_for_function(f"() => !document.querySelector(`{card}`).textContent.includes('zeta')")
    page.context.close()
    assert tags_of(session.first) == before


def test_undo_of_tags_with_the_keyboard_comes_back_to_the_card(browser, session) -> None:
    """The card was drawn again as the tags were kept, so the keyboard was
    left on the page itself, the notice's Undo out of reach and nowhere to
    come back to."""
    page = _page(browser, session, "#studies")
    page.evaluate(f"() => window.FastMDXStudies.load({str(session.workspace)!r})")
    card = '.study-card[data-path$="wild_type"]'
    page.wait_for_selector(card)
    page.click(f"{card} .study-tag-edit")
    page.fill(f"{card} .study-tag-input", "kept")
    page.click(f"{card} .study-tag-editor button:has-text('Save')")
    page.wait_for_selector("#dashboard-toast.show .dashboard-toast-action")
    on = "() => document.activeElement.matches('.study-card[data-path$=\"wild_type\"] .study-tag-edit')"
    after_save = page.evaluate(on)
    page.focus("#dashboard-toast .dashboard-toast-action")
    page.keyboard.press("Enter")
    page.wait_for_selector("#dashboard-toast:has-text('back as they were')")
    after_undo = page.evaluate(on)
    page.context.close()
    assert after_save and after_undo, (after_save, after_undo)


def test_a_view_forgotten_is_undone(browser, session) -> None:
    first = {"camera": CAMERA, "frame": 3}
    _save(session, "one", first)
    page = _page(browser, session, "#viewer")
    page.wait_for_selector("#viewer-views option[value='one']", state="attached")
    hooks.tool(page, "side-saved")
    page.select_option("#viewer-views", "one")
    page.click("#viewer-view-forget")
    page.wait_for_function("() => !document.querySelector(\"#viewer-views option[value='one']\")")
    gone = _views(session)
    _undo(page, "Forgot the view one")
    page.wait_for_selector("#viewer-views option[value='one']", state="attached")
    back = _views(session)
    errors = page.errors
    page.context.close()
    assert gone == []
    assert [(view["name"], view["frame"], view["camera"]["radius"]) for view in back] == [("one", 3, 12.5)]
    assert errors == []


def test_undo_is_offered_while_it_shows_and_not_after(browser, session) -> None:
    """Hidden after its ten seconds, the notice kept its Undo in the page:
    reached with Tab and pressed, it undid a change long after. Kept while
    the person is on it."""
    page = _page(browser, session, "#overview")
    page.wait_for_function("() => document.body.classList.contains('state-ready')")
    page.evaluate("""() => { window.__undone = 0; window.FastMDXDashboard.toast('Kept it.', 'ok',
        {label: 'Undo', run: () => { window.__undone += 1; }}); }""")
    page.focus("#dashboard-toast .dashboard-toast-action")
    page.wait_for_timeout(11000)
    held = page.evaluate("""() => [document.getElementById('dashboard-toast').classList.contains('show'),
        document.querySelector('#dashboard-toast .dashboard-toast-action').getAttribute('aria-label')]""")
    page.evaluate("() => document.activeElement.blur()")
    page.wait_for_timeout(4500)
    left = page.evaluate("() => document.querySelectorAll('#dashboard-toast .dashboard-toast-action').length")
    page.context.close()
    assert held == [True, "Undo: Kept it."]
    assert left == 0


def test_a_view_chosen_before_the_page_s_first_state_stays_chosen(browser, session) -> None:
    """The page's first state is said as the study changed, though it is the
    study the page opened: a view chosen before it came was put back to none,
    and Forget, refusing it, could not be pressed."""
    _save(session, "kept", {"camera": CAMERA, "frame": 1})
    page = _page(browser, session, "#viewer")
    page.wait_for_selector("#viewer-views option[value='kept']", state="attached")
    hooks.tool(page, "side-saved")
    page.select_option("#viewer-views", "kept")
    page.evaluate("""() => window.dispatchEvent(new CustomEvent('dashboard:run-changed',
        {detail: {previousRun: '', activeRun: '', first: true}}))""")
    page.wait_for_timeout(500)
    chosen = page.evaluate("""() => [document.getElementById('viewer-views').value,
        document.getElementById('viewer-view-forget').disabled]""")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert chosen == ["kept", False]


def test_a_view_is_not_undone_into_another_study(browser, session) -> None:
    """Undo pressed after another study was opened saved or forgot the view
    in that study."""
    _save(session, "two", {"camera": CAMERA, "frame": 1})
    page = _page(browser, session, "#viewer")
    page.wait_for_selector("#viewer-views option[value='two']", state="attached")
    hooks.tool(page, "side-saved")
    page.select_option("#viewer-views", "two")
    page.click("#viewer-view-forget")
    page.wait_for_selector("#dashboard-toast.show .dashboard-toast-action")
    # On the Undo, so it is still offered however long the page takes to see
    # the other study (its next poll, where the change stream is slow).
    page.focus("#dashboard-toast .dashboard-toast-action")
    page.evaluate("""(folder) => fetch('/api/explore/switch', {method: 'POST',
        headers: {'content-type': 'application/json'}, body: JSON.stringify({folder})})""",
                  str(session.workspace / "mutant"))
    page.wait_for_function("(key) => window.FastMDXDashboard.state.studyShown === key",
                           arg=study_key(session.workspace / "mutant"))
    page.click("#dashboard-toast .dashboard-toast-action")
    page.wait_for_timeout(1000)
    elsewhere = _views(session)
    said = page.text_content("#dashboard-toast")
    page.evaluate("""(folder) => fetch('/api/explore/switch', {method: 'POST',
        headers: {'content-type': 'application/json'}, body: JSON.stringify({folder})})""",
                  str(session.first))
    page.context.close()
    assert elsewhere == []
    assert "another study is open" in said


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    from fastmdxplora.gui import preview
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_builder_page_in_a_browser import _peptide

    root = tmp_path_factory.mktemp("builder-undo")
    _peptide(root / "peptide.pdb")
    mp = pytest.MonkeyPatch()
    mp.setenv("FASTMDXPLORA_CACHE_DIR", str(root / "cache"))
    mp.setenv("FASTMDXPLORA_CONFIG_DIR", str(root / "config"))
    mp.setattr(preview, "_platform", lambda requested, precision: ("CPU", precision))
    started = start_dashboard_session(output=str(root / "study"), host="127.0.0.1", port=0)
    started.peptide = root / "peptide.pdb"
    yield started
    started.server.shutdown()
    mp.undo()


def test_the_builder_takes_its_edits_back_and_forward(browser, builder) -> None:
    page = _page(browser, builder, "#run")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.click("#run-start [data-start='structure']")
    page.fill("#run-system", str(builder.peptide))
    ph = '#run-settings [data-setting="ph"] input'
    page.wait_for_selector(ph)
    first = page.input_value(ph)
    for value in ("6.5", "8.0"):
        page.fill(ph, value)
        page.dispatch_event(ph, "change")
    # Away from the field, so the keys are the form's, not the typing's.
    page.click("#run-summary-panel")
    seen = []
    for keys in ("Control+z", "Control+z", "Control+Shift+z", "Control+y"):
        page.keyboard.press(keys)
        seen.append((page.input_value(ph), page.evaluate(
            "() => String(window.FastMDXRun.state.values.setup && window.FastMDXRun.state.values.setup.ph)")))
    said = page.text_content("#run-note")
    errors = page.errors
    page.context.close()
    assert [shown for shown, _ in seen] == ["6.5", first, "6.5", "8.0"], seen
    assert float(seen[0][1]) == 6.5 and float(seen[-1][1]) == 8.0
    assert said == "Done again."
    assert errors == []


def test_a_config_loaded_is_a_step_of_its_own(browser, builder) -> None:
    """A config the Agent filled in, then one edit: Cmd+Z took back the
    edit and the config together, to the form before it."""
    page = _page(browser, builder, "#run")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.click("#run-start [data-start='structure']")
    page.fill("#run-system", str(builder.peptide))
    ph = '#run-settings [data-setting="ph"] input'
    page.wait_for_selector(ph)
    page.fill(ph, "6.5")
    page.dispatch_event(ph, "change")
    page.click("#run-summary-panel")
    # A config of the form with another pH, filled in as the Agent's is.
    import yaml

    built = page.evaluate("() => window.FastMDXRun.fetchConfig()")
    config = yaml.safe_load(built["yaml"])
    config.setdefault("setup", {})["ph"] = 9.1
    path = builder.peptide.parent / "loaded.yml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    page.evaluate("""(path) => fetch('/api/load-config', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})})
        .then(r => r.json()).then(loaded => window.FastMDXRun.applyLoadedState(loaded.state, {}))""",
                  str(path))
    page.wait_for_function(f"() => document.querySelector('{ph}').value === '9.1'")
    page.fill(ph, "9.2")
    page.dispatch_event(ph, "change")
    page.click("#run-summary-panel")
    page.keyboard.press("Control+z")
    once = page.input_value(ph)
    page.context.close()
    assert once == "9.1"


def test_a_stop_undone_is_the_one_shown(browser, builder) -> None:
    """Undone, the ceiling of a study run until it is determined showed the
    value undone, from the rows' own copy, while the config held the one
    put back; the next edit there wrote the shown one back."""
    page = _page(browser, builder, "#run")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.click("#run-start [data-start='structure']")
    page.fill("#run-system", str(builder.peptide))
    page.wait_for_selector('#run-settings [data-setting="ph"] input')
    page.click('#run-length-mode [data-value="until"]')
    field = page.locator('[data-setting="stop_when"]')
    row = field.locator(".builder-stopping-row").first
    row.locator(".builder-stopping-analysis").select_option("rmsd")
    row.locator(".builder-stopping-amount").fill("0.01")
    row.locator(".builder-stopping-amount").dispatch_event("change")
    ceiling = field.locator(".builder-stopping-ceiling")
    ceiling.fill("50")
    ceiling.dispatch_event("change")
    page.click("#run-summary-panel")
    ceiling.fill("123")
    ceiling.dispatch_event("change")
    page.click("#run-summary-panel")
    page.keyboard.press("Control+z")
    shown = field.locator(".builder-stopping-ceiling").input_value()
    built = page.evaluate("() => window.FastMDXRun.fetchConfig()")
    page.context.close()
    import yaml

    assert shown == "50"
    assert yaml.safe_load(built["yaml"])["simulation"]["stop_when"]["max_duration_ns"] == 50


def test_undo_goes_on_from_a_field_it_changed(browser, builder) -> None:
    """Once an undo step had changed the field the keyboard was in, Cmd+Z
    there was left to the browser, which had nothing to undo: stuck."""
    page = _page(browser, builder, "#run")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.click("#run-start [data-start='structure']")
    page.fill("#run-system", str(builder.peptide))
    ph = '#run-settings [data-setting="ph"] input'
    page.wait_for_selector(ph)
    first = page.input_value(ph)
    page.fill(ph, "6.5")
    page.dispatch_event(ph, "change")
    page.click("#run-output")
    page.keyboard.type("myrun")
    page.keyboard.press("Tab")
    page.click("#run-output")
    page.keyboard.type("2")
    seen = []
    for _ in range(3):
        page.keyboard.press("Control+z")
        seen.append((page.input_value("#run-output"), page.input_value(ph)))
    page.context.close()
    assert seen == [("myrun", "6.5"), ("", "6.5"), ("", first)], seen


def test_typing_is_undone_and_done_again_in_its_field(browser, builder) -> None:
    """Cmd+Z in a field put back everything typed there, and Shift+Cmd+Z
    could not bring it back."""
    page = _page(browser, builder, "#run")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.click("#run-start [data-start='structure']")
    page.click("#run-output")
    page.fill("#run-output", "")
    page.keyboard.type("first")
    # The caret moved: the browser's typing undone from here (it takes back
    # all typing since the caret last moved, however slow).
    page.keyboard.press("End")
    page.keyboard.type(" second")
    page.keyboard.press("Control+z")
    undone = page.input_value("#run-output")
    page.keyboard.press("Control+Shift+z")
    again = page.input_value("#run-output")
    page.context.close()
    # The browser's own undo, as much as it takes back at once; none of it lost.
    assert undone.startswith("first") and undone != "first second", undone
    assert again == "first second", again


def test_the_agent_s_request_beside_the_builder_keeps_its_typing(browser, builder) -> None:
    """Cmd+Z in the Agent's request, open beside the Config Builder, took the
    whole request away for good."""
    page = _page(browser, builder, "#run")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.click("#run-summary-panel")
    page.keyboard.press("Control+j")
    page.wait_for_selector("#agent-drawer #agent-request")
    page.click("#agent-request")
    page.keyboard.type("Set up a run of the peptide at 300 K")
    # As above: the caret moved, so the browser's undo stops here. A pause
    # in the typing does not, and the request came back empty.
    page.keyboard.press("End")
    page.keyboard.type(" for 10 ns")
    page.keyboard.press("Control+z")
    undone = page.input_value("#agent-request")
    page.keyboard.press("Control+Shift+z")
    again = page.input_value("#agent-request")
    page.context.close()
    assert undone.startswith("Set up a run of the peptide at 300 K"), undone
    assert undone != "Set up a run of the peptide at 300 K for 10 ns", undone
    assert again == "Set up a run of the peptide at 300 K for 10 ns", again


@pytest.mark.parametrize("begun", ["moved", "key", "paste"])
def test_typing_begun_at_once_in_another_field_is_one_step(browser, builder, begun) -> None:
    """A field left for another and typed in at once: the change of the field
    left was read once the page got to it, which on a busy machine is after
    the first key (the browser takes keys first), and the typing was taken
    back in two steps, "m" and then the rest. The change is read as the next
    field takes the keyboard, or, where it has it already, as a key or a
    paste begins."""
    page = _page(browser, builder, "#run")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.click("#run-start [data-start='structure']")
    page.fill("#run-system", str(builder.peptide))
    ph = '#run-settings [data-setting="ph"] input'
    page.wait_for_selector(ph)
    first = page.input_value(ph)
    page.fill(ph, "6.5")
    page.dispatch_event(ph, "change")
    page.wait_for_timeout(300)
    if begun != "moved":
        page.click("#run-output")
    # The pH field's change and the first letter of the next field in one go,
    # as a busy page meets them: the change read only after the letter.
    page.evaluate("""([ph, begun]) => {
        const output = document.getElementById('run-output');
        document.querySelector(ph).value = '7.0';
        document.querySelector(ph).dispatchEvent(new Event('change', {bubbles: true}));
        if (begun === 'moved') output.focus();
        if (begun === 'paste') {
          output.dispatchEvent(new InputEvent('beforeinput',
            {inputType: 'insertFromPaste', data: 'm', bubbles: true, cancelable: true}));
        } else {
          output.dispatchEvent(new KeyboardEvent('keydown', {key: 'm', bubbles: true}));
        }
        output.value = 'm';
        output.dispatchEvent(new InputEvent('input', {data: 'm', bubbles: true}));
    }""", [ph, begun])
    # A read still waiting for its timer takes it now, before the rest.
    page.evaluate("() => new Promise((done) => setTimeout(done, 0))")
    page.keyboard.type("yrun")
    page.keyboard.press("Tab")
    seen = []
    for _ in range(3):
        page.keyboard.press("Control+z")
        seen.append((page.input_value("#run-output"), page.input_value(ph)))
    page.context.close()
    assert seen == [("", "7.0"), ("", "6.5"), ("", first)], seen


def test_a_phase_left_out_is_a_step_of_its_own(browser, builder) -> None:
    """A phase chip's own change draws the chips again, so its event reached
    the page gone from it: the phase left out was taken back with the
    typing after it."""
    page = _page(browser, builder, "#run")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.click("#run-start [data-start='structure']")
    page.fill("#run-system", str(builder.peptide))
    page.wait_for_selector('#run-settings [data-setting="ph"] input')
    report = "#run-phases [data-phase='report']"
    chosen = page.is_checked(f"{report} input")
    page.click(f"{report} .run-phase-label")
    page.wait_for_function(f"(was) => document.querySelector(\"{report} input\").checked !== was",
                           arg=chosen)
    page.click("#run-output")
    page.keyboard.type("abc")
    page.keyboard.press("Tab")
    seen = []
    for _ in range(2):
        page.keyboard.press("Control+z")
        seen.append((page.input_value("#run-output"), page.is_checked(f"{report} input")))
    page.context.close()
    assert seen == [("", not chosen), ("", chosen)], seen
