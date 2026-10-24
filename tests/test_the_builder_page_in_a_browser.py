"""The Config Builder, driven in a browser.

Each check is something the page got wrong before it was rebuilt, or a
promise the rebuild makes:

- a list of choices with no default leaves the setting alone, and choosing
  a value and then "Not set" again writes nothing (a protein in water read
  as embedded in POPC, and choosing it back wrote `membrane: POPC`);
- Tab out of a changed setting goes to the next control, not the page (every
  change built the whole form again);
- the form is saved in the browser, so a reload loses nothing, and Reset
  puts every setting back, the starting point and the structure kept; the
  note says Saved and is seen saving, without moving where less motion is
  asked for;
- an Agent's reply prices itself without overwriting the form;
- a setting is found by typing its name; a changed one says so, and its
  revert puts the default back;
- a reason given for a value reaches the config as `decisions`, goes with
  its value and is the writer's;
- an analysis of a pair the study names can be chosen, and choosing none is
  said rather than run as every one;
- a study of several systems opened in the form is submitted whole;
- the analyses an NMR peptide in water leaves nothing for say why;
- the summary says whether the study will run.
"""

from __future__ import annotations

import json

import pytest
import yaml


def _peptide(path, models=1):
    atom = "ATOM  {:5d}  {:<3s} GLY A{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00           {}\n"
    body = "".join(atom.format(4 * i + k + 1, name, i + 1, 3.6 * i + 0.4 * k, 0.0, 0.0, name[0])
                   for i in range(3) for k, name in enumerate(("N", "CA", "C", "O")))
    if models > 1:
        text = "".join(f"MODEL     {m:4d}\n{body}ENDMDL\n" for m in range(1, models + 1)) + "END\n"
    else:
        text = body + "END\n"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui import preview
    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path_factory.mktemp("builder")
    _peptide(root / "peptide.pdb")
    _peptide(root / "ensemble.pdb", models=3)
    mp = pytest.MonkeyPatch()
    mp.setenv("FASTMDXPLORA_CACHE_DIR", str(root / "cache"))
    mp.setenv("FASTMDXPLORA_CONFIG_DIR", str(root / "config"))
    mp.setattr(preview, "_platform", lambda requested, precision: ("CPU", precision))
    session = start_dashboard_session(output=str(root / "study"), host="127.0.0.1", port=0)
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            yield {"browser": browser, "url": session.url.rstrip("/") + "/#run", "root": root}
            browser.close()
    finally:
        session.server.shutdown()
        mp.undo()


def _open(site, structure="peptide.pdb", context=None):
    context = context or site["browser"].new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.errors = errors
    page.goto(site["url"], wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.state.schema")
    if structure:
        page.click("#run-start [data-start='structure']")
        page.fill("#run-system", str(site["root"] / structure))
        page.wait_for_selector('#run-settings [data-setting="ph"] input')
    return page


def _built(page) -> dict:
    return page.evaluate("() => window.FastMDXRun.fetchConfig()")


def _config(page) -> dict:
    built = _built(page)
    assert built["ok"], built.get("error")
    return yaml.safe_load(built["yaml"])


class TestASettingLeftAlone:
    def test_a_list_without_a_default_starts_unset_and_can_go_back(self, site):
        page = _open(site)
        select = '#run-settings [data-setting="membrane"] select'
        first = page.locator(f"{select} option").first.text_content()
        assert page.input_value(select) == "" and first == "Not set: no membrane"
        page.select_option(select, "POPE")
        assert _config(page)["setup"]["membrane"] == "POPE"
        page.select_option(select, "")
        assert "membrane" not in (_config(page).get("setup") or {})
        assert page.errors == []
        page.context.close()

    def test_an_empty_box_says_what_it_does_or_gives_an_example(self, site):
        page = _open(site)
        model = page.get_attribute('#run-settings [data-setting="mutations"] input', "placeholder")
        production = page.get_attribute('#run-settings [data-setting="duration_ns"] input', "placeholder")
        assert model.startswith("e.g. ")
        assert production == "Not set: 2 ns"
        page.context.close()


class TestTheKeyboard:
    def test_tab_out_of_a_changed_setting_goes_on(self, site):
        page = _open(site)
        ph = '#run-settings [data-setting="ph"] input'
        page.click(ph)
        page.fill(ph, "7.0")
        page.keyboard.press("Tab")
        where = page.evaluate("""() => {
            const active = document.activeElement;
            const field = active.closest('[data-setting]');
            return field ? field.dataset.setting : active.tagName;
        }""")
        assert where == "ph"
        assert page.evaluate("() => document.activeElement.tagName") == "BUTTON"
        page.context.close()

    def test_a_number_outside_its_range_is_said_before_anything_is_sent(self, site):
        page = _open(site)
        ph = '#run-settings [data-setting="ph"] input'
        page.fill(ph, "25")
        said = page.text_content('#run-settings [data-setting="ph"] .builder-limit')
        assert said == "pH runs from 0 to 14."
        assert page.is_disabled("#run-start-button")
        page.context.close()


class TestTheDraft:
    def test_a_reload_loses_nothing_and_reset_puts_the_settings_back(self, site):
        context = site["browser"].new_context(viewport={"width": 1440, "height": 900})
        page = _open(site, context=context)
        page.fill('#run-settings [data-setting="ph"] input', "6.5")
        page.dispatch_event('#run-settings [data-setting="ph"] input', "change")
        page.wait_for_selector("#run-draft:not([hidden])")
        page.wait_for_timeout(500)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector('#run-settings [data-setting="ph"] input')
        assert page.input_value('#run-settings [data-setting="ph"] input') == "6.5"
        assert page.input_value("#run-system").endswith("peptide.pdb")
        page.click("#run-reset")
        page.wait_for_timeout(500)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector('#run-settings [data-setting="ph"] input')
        assert page.input_value('#run-settings [data-setting="ph"] input') == ""
        assert page.input_value("#run-system").endswith("peptide.pdb")
        context.close()

    # Each write of the draft and each animation of its note, from the
    # document's start, so a test waits on them rather than on time.
    WATCH = """(() => {
        // Once a document, however often the script is run in it.
        if (window.__writes) return;
        window.__writes = []; window.__saves = []; window.__failWrites = false;
        const set = Storage.prototype.setItem;
        Storage.prototype.setItem = function (key, value) {
            if (String(key).startsWith('fmx.builderDraft')) {
                if (window.__failWrites) throw new DOMException('full', 'QuotaExceededError');
                window.__writes.push(String(value));
            }
            return set.call(this, key, value);
        };
        document.addEventListener('animationstart', (event) => {
            if (event.target && event.target.id === 'run-draft') window.__saves.push(event.animationName);
        }, true);
    })();"""

    def _watched(self, site, **options):
        context = site["browser"].new_context(viewport={"width": 1440, "height": 900}, **options)
        context.add_init_script(self.WATCH)
        page = _open(site, context=context)
        page.wait_for_selector("#run-draft:not([hidden])")
        self._quiet(page)
        # What opening the form played (its structure chosen is a change).
        self._frames(page)
        page.evaluate("() => window.__saves.splice(0)")
        return page

    @staticmethod
    def _quiet(page):
        """Once the form has stopped writing its draft (a save waits 300 ms
        after the last change)."""
        seen = -1
        while True:
            count = page.evaluate("() => window.__writes.length")
            if count == seen:
                return
            seen = count
            page.wait_for_timeout(700)

    @staticmethod
    def _frames(page):
        page.evaluate("() => new Promise((done) => requestAnimationFrame("
                      "() => requestAnimationFrame(() => done())))")

    def _plays(self, page, value, *, changes=True):
        """What the note played as ``value`` was given to pH and saved."""
        written = page.evaluate("() => window.__writes.length")
        page.fill('#run-settings [data-setting="ph"] input', value)
        page.dispatch_event('#run-settings [data-setting="ph"] input', "change")
        page.wait_for_function("""([n, value]) => window.__writes.length > n
            && String(Object.values(JSON.parse(window.__writes[window.__writes.length - 1])
                .values || {}).map((phase) => (phase || {}).ph).find((ph) => ph !== undefined))
               === value""", arg=[written, value], timeout=30000)
        if changes:
            # Saved, and not played: the note's play is what this waits for.
            page.wait_for_function("() => window.__saves.length >= 2", timeout=30000)
        self._frames(page)
        return page.evaluate("() => window.__saves.splice(0)")

    def test_the_draft_says_saved_and_is_seen_saving(self, site):
        """The note says Saved (in this browser, to a screen reader), and
        plays again at each save that changed the draft, not at one that
        changed nothing; once played, it is ready to play again."""
        page = self._watched(site)
        said = page.evaluate("""() => { const note = document.getElementById('run-draft');
            return [note.firstChild.textContent, note.querySelector('.sr-only').textContent]; }""")
        first = self._plays(page, "6.5")
        page.wait_for_function("() => !document.getElementById('run-draft')"
                               ".classList.contains('just-saved')")
        again = self._plays(page, "7.5")
        unchanged = self._plays(page, "7.5", changes=False)
        overflow = page.evaluate("() => getComputedStyle(document.getElementById('run-draft')).overflow")
        assert said == ["Saved", " in this browser"]
        assert sorted(first) == ["builder-saved", "builder-saved-dot"]
        assert sorted(again) == ["builder-saved", "builder-saved-dot"]
        assert unchanged == []
        # The dot grows past its box as it plays: not clipped.
        assert overflow == "visible"
        assert page.errors == []
        page.context.close()

    def test_reset_plays_only_where_it_changed_the_draft(self, site):
        """Reset writes the draft over rather than discarding it first: the
        note stays, plays where the settings went back, and stays still on
        a form already at its defaults."""
        page = self._watched(site)
        self._plays(page, "6.5")
        page.evaluate("""() => { window.__hidden = [];
            new MutationObserver(() => window.__hidden.push(
                document.getElementById('run-draft').hidden)).observe(
                document.getElementById('run-draft'), {attributes: true,
                                                        attributeFilter: ['hidden']}); }""")
        written = page.evaluate("() => window.__writes.length")
        page.click("#run-reset")
        page.wait_for_function("() => window.__saves.length >= 2")
        back = page.evaluate("() => window.__saves.splice(0)")
        last = page.evaluate("() => JSON.parse(window.__writes[window.__writes.length - 1])")
        self._quiet(page)
        page.evaluate("() => window.__saves.splice(0)")
        written = page.evaluate("() => window.__writes.length")
        page.click("#run-reset")
        page.wait_for_function("n => window.__writes.length > n", arg=written)
        self._frames(page)
        still = page.evaluate("() => window.__saves.splice(0)")
        hidden = page.evaluate("() => window.__hidden.filter(Boolean).length")
        assert sorted(back) == ["builder-saved", "builder-saved-dot"]
        assert not any("ph" in (phase or {}) for phase in (last.get("values") or {}).values())
        assert still == []
        assert hidden == 0
        assert page.errors == []
        page.context.close()

    def test_a_draft_opened_again_is_not_played(self, site):
        page = self._watched(site)
        self._plays(page, "6.5")
        self._quiet(page)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#run-draft:not([hidden])")
        self._quiet(page)
        self._frames(page)
        played = page.evaluate("() => window.__saves")
        restored = page.input_value('#run-settings [data-setting="ph"] input')
        assert restored == "6.5"
        assert played == []
        page.context.close()

    def test_a_save_that_fails_is_not_said(self, site):
        page = self._watched(site)
        page.evaluate("() => { window.__failWrites = true; }")
        page.fill('#run-settings [data-setting="ph"] input', "6.5")
        page.dispatch_event('#run-settings [data-setting="ph"] input', "change")
        page.wait_for_selector("#run-draft", state="hidden")
        assert page.errors == []
        page.context.close()

    def test_less_motion_asked_for_saves_without_moving(self, site):
        page = self._watched(site, reduced_motion="reduce")
        written = page.evaluate("() => window.__writes.length")
        page.fill('#run-settings [data-setting="ph"] input', "6.5")
        page.dispatch_event('#run-settings [data-setting="ph"] input', "change")
        page.wait_for_function("n => window.__writes.length > n", arg=written)
        self._frames(page)
        moves = page.evaluate("""() => { const note = document.getElementById('run-draft');
            return [note.classList.contains('just-saved'), getComputedStyle(note).animationName,
                    getComputedStyle(note, '::before').animationName, window.__saves.length]; }""")
        assert moves == [True, "none", "none", 0]
        page.context.close()

    # The page's 300 ms waits held, as on a machine that reloads first: a
    # save still waiting is made only if the page makes it as it goes.
    HOLD = """() => { const later = window.setTimeout;
        window.setTimeout = (run, ms, ...rest) => ms === 300 ? later(() => {}, 1e9) : later(run, ms, ...rest); }"""

    def test_a_reload_as_a_save_waits_loses_nothing(self, site):
        """Reset, then a reload within the 300 ms a save waits, opened the
        settings from before Reset; an edit reloaded as soon was lost."""
        ph = '#run-settings [data-setting="ph"] input'
        page = self._watched(site)
        self._plays(page, "6.5")
        self._quiet(page)
        page.evaluate(self.HOLD)
        page.click("#run-reset")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(ph)
        after_reset = page.input_value(ph)
        self._quiet(page)
        page.evaluate(self.HOLD)
        page.fill(ph, "7.5")
        page.dispatch_event(ph, "change")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(ph)
        edited = page.input_value(ph)
        page.context.close()
        errors = page.errors
        assert after_reset == ""
        assert edited == "7.5"
        assert errors == []

    def test_a_save_said_twice_plays_twice(self, site):
        """A second save as the first still plays starts it again; and one
        cut short as the page is hidden does not play when it is shown."""
        page = self._watched(site)
        # Long enough that the next save comes as it plays.
        page.add_style_tag(content="#run-draft.just-saved { animation-duration: 60s !important; }")
        self._plays(page, "6.5")
        again = self._plays(page, "7.5")
        # The field let go of: leaving it saves, which also ends a play.
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        self._quiet(page)
        page.evaluate("() => window.__saves.splice(0)")
        # Saved again with the keyboard elsewhere, then hidden as it plays,
        # as another page is shown, with nothing saved as it goes.
        written = page.evaluate("() => window.__writes.length")
        page.evaluate("""() => { const box = document.querySelector('#run-settings [data-setting="ph"] input');
            box.value = '8.0'; box.dispatchEvent(new Event('input', {bubbles: true}));
            box.dispatchEvent(new Event('change', {bubbles: true})); }""")
        page.wait_for_function("(n) => window.__writes.length > n && window.__saves.length >= 2",
                               arg=written)
        page.evaluate("() => window.__saves.splice(0)")
        written = page.evaluate("() => window.__writes.length")
        run = ".page[data-page='run']"
        page.evaluate("(run) => { document.querySelector(run).hidden = true; }", run)
        page.wait_for_function("() => !document.getElementById('run-draft')"
                               ".classList.contains('just-saved')", timeout=10000)
        page.evaluate("(run) => { document.querySelector(run).hidden = false; }", run)
        self._frames(page)
        shown = page.evaluate("() => window.__saves.splice(0)")
        saved = page.evaluate("() => window.__writes.length") - written
        errors = page.errors
        page.context.close()
        assert "builder-saved" in again
        assert saved == 0
        assert shown == []
        assert errors == []

    def test_a_save_made_while_the_page_is_hidden_is_not_played_when_shown(self, site):
        """A value given and another page opened within the 300 ms a save
        waits: the save is made while the form is hidden, where its play
        never starts and so never ends, and it played once the form was
        shown again."""
        run = ".page[data-page='run']"
        page = self._watched(site)
        written = page.evaluate("() => window.__writes.length")
        page.evaluate("""(run) => { const box = document.querySelector(
                '#run-settings [data-setting="ph"] input');
            box.value = '6.5'; box.dispatchEvent(new Event('input', {bubbles: true}));
            box.dispatchEvent(new Event('change', {bubbles: true}));
            document.querySelector(run).hidden = true; }""", run)
        page.wait_for_function("(n) => window.__writes.length > n", arg=written)
        kept = page.evaluate("() => document.getElementById('run-draft')"
                             ".classList.contains('just-saved')")
        page.evaluate("(run) => { document.querySelector(run).hidden = false; }", run)
        self._frames(page)
        played = page.evaluate("() => window.__saves.splice(0)")
        errors = page.errors
        page.context.close()
        assert kept is False
        assert played == []
        assert errors == []

    def test_a_save_waiting_as_the_page_goes_is_made(self, site):
        """The page's own pagehide, as a browser that sends no
        visibilitychange as a page goes (older Safari) leaves it."""
        ph = '#run-settings [data-setting="ph"] input'
        page = self._watched(site)
        page.evaluate(self.HOLD)
        written = page.evaluate("() => window.__writes.length")
        page.fill(ph, "6.5")
        page.dispatch_event(ph, "change")
        page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide',"
                      " {persisted: false}))")
        made = page.evaluate("(n) => window.__writes.slice(n)", written)
        errors = page.errors
        page.context.close()
        assert len(made) == 1
        values = json.loads(made[0]).get("values") or {}
        assert any(str((phase or {}).get("ph")) == "6.5" for phase in values.values())
        assert errors == []

    def test_a_choice_already_made_and_left_focused_keeps_the_form_through_a_reload(self, site):
        """The start already chosen, clicked again or reached with the
        keyboard, holds the focus: a change sent to it as the page went set
        its default phases again, the Report left out put back."""
        report = "#run-phases [data-phase='report'] input"
        page = self._watched(site)
        page.click(report)
        self._quiet(page)
        page.focus("#run-start [data-start='structure'] input")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(report)
        kept = page.is_checked(report)
        page.context.close()
        assert kept is False

    def test_a_tab_left_behind_does_not_write_over_a_newer_draft(self, site):
        """A tab whose field kept the focus after its value was saved, left
        while another tab saved a newer draft, and then reloaded: the change
        sent to its field as it went wrote its own older draft back."""
        ph = '#run-settings [data-setting="ph"] input'
        context = site["browser"].new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script(self.WATCH)
        first = _open(site, context=context)
        first.fill(ph, "6.5")
        first.dispatch_event(ph, "change")
        self._quiet(first)
        second = context.new_page()
        second.set_default_timeout(60000)
        second.goto(site["url"], wait_until="domcontentloaded")
        second.wait_for_selector(ph)
        second.fill(ph, "8.0")
        second.dispatch_event(ph, "change")
        self._quiet(second)
        first.reload(wait_until="domcontentloaded")
        first.wait_for_selector(ph)
        kept = first.input_value(ph)
        context.close()
        assert kept == "8.0"

    def test_a_tab_left_behind_with_typing_does_not_write_over_a_newer_draft(self, site):
        """A tab with a value typed and not left, while another tab saved a
        newer draft, then closed: the change sent to its field as it went
        wrote its whole older form over the newer draft."""
        ph = '#run-settings [data-setting="ph"] input'
        context = site["browser"].new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script(self.WATCH)
        first = _open(site, context=context)
        self._quiet(first)
        first.fill(ph, "6.6")
        second = context.new_page()
        second.set_default_timeout(60000)
        second.goto(site["url"], wait_until="domcontentloaded")
        second.wait_for_selector(ph)
        second.fill("#run-output", "newer_from_b")
        self._quiet(second)
        first.close(run_before_unload=True)
        second.wait_for_timeout(500)
        kept = json.loads(second.evaluate("""() => localStorage.getItem(Object.keys(localStorage)
            .filter((key) => key.startsWith('fmx.builderDraft'))[0])"""))
        context.close()
        assert kept["output"] == "newer_from_b"

    def test_a_value_typed_and_run_with_the_keyboard_is_the_study_s(self, site):
        """Ctrl+Enter from a field still being typed in: the study ran
        without the value, and its draft was written after it started."""
        ph = '#run-settings [data-setting="ph"] input'
        page = self._watched(site)
        sent = []

        def answer(route):
            sent.append(route.request.post_data or "")
            route.fulfill(status=200, content_type="application/json",
                          body='{"ok": true, "config_path": "study.yml"}')

        page.route("**/api/run", answer)
        page.fill(ph, "6.9")
        page.keyboard.press("Control+Enter")
        page.wait_for_selector("#run-draft", state="hidden")
        page.wait_for_timeout(1000)
        kept = page.evaluate("""() => Object.keys(localStorage)
            .filter((key) => key.startsWith('fmx.builderDraft'))""")
        page.context.close()
        assert len(sent) == 1
        assert "6.9" in sent[0]
        assert kept == []

    @pytest.mark.parametrize("refused", ["said", "unreached"])
    def test_a_study_not_started_leaves_the_focus_in_the_field(self, site, refused):
        """Ctrl+Enter from a field, and the study not started (refused with
        no setting named, or the server not reached): the field let go of
        to send its value had the focus no more."""
        ph = '#run-settings [data-setting="ph"] input'
        page = self._watched(site)
        if refused == "said":
            page.route("**/api/run", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body='{"ok": false, "error": "A study is already running here."}'))
        else:
            page.route("**/api/run", lambda route: route.abort())
        page.fill(ph, "6.9")
        page.keyboard.press("Control+Enter")
        page.wait_for_function("() => !document.getElementById('run-start-button').disabled"
                               " && document.getElementById('run-note').textContent"
                               " !== 'Starting\u2026'")
        focused = page.evaluate("""() => !!document.activeElement
            && !!document.activeElement.closest('[data-setting="ph"]')
            && document.activeElement.tagName === 'INPUT'""")
        value = page.input_value(ph)
        # The summary's check, set off as the field was let go of, comes
        # back after the refusal and left the note blank.
        page.wait_for_timeout(1500)
        note = page.text_content("#run-note")
        page.context.close()
        assert focused is True
        assert value == "6.9"
        assert note == ("A study is already running here." if refused == "said"
                        else "Could not reach the server.")

    def test_a_config_path_typed_is_kept_as_the_page_is_hidden(self, site):
        """Only a change kept the path, and its check saves only once its
        answer comes: a phone that closes a page switched away from, with
        no pagehide, lost the path being typed."""
        page = self._watched(site)
        page.click("#run-start [data-start='config']")
        self._quiet(page)
        page.evaluate(self.HOLD)
        written = page.evaluate("() => window.__writes.length")
        page.fill("#run-config-path", "/data/my_study.yml")
        page.evaluate("""() => { Object.defineProperty(document, 'visibilityState',
            {value: 'hidden', configurable: true});
            document.dispatchEvent(new Event('visibilitychange')); }""")
        made = page.evaluate("(n) => window.__writes.slice(n)", written)
        page.context.close()
        assert [json.loads(one).get("configPath") for one in made] == ["/data/my_study.yml"]

    def test_a_study_started_as_a_save_waits_leaves_no_draft(self, site):
        """Start pressed within the 300 ms a save waits, then a reload: the
        save still waiting wrote the started study's draft back."""
        ph = '#run-settings [data-setting="ph"] input'
        page = self._watched(site)
        page.route("**/api/run", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"ok": true, "config_path": "study.yml"}'))
        page.evaluate(self.HOLD)
        page.fill(ph, "6.5")
        page.dispatch_event(ph, "change")
        page.click("#run-start-button")
        page.wait_for_function("() => document.getElementById('run-note')"
                               ".textContent.startsWith('Running. Config saved to')")
        page.wait_for_selector("#run-draft", state="hidden")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.state.schema")
        kept = page.evaluate("""() => Object.keys(localStorage)
            .filter((key) => key.startsWith('fmx.builderDraft'))
            .map((key) => localStorage.getItem(key))""")
        page.context.close()
        assert kept == []

    def test_a_value_typed_and_not_left_is_kept_through_a_reload(self, site):
        """A field still being typed in has had no change event, and the
        browser sends none as the page goes."""
        ph = '#run-settings [data-setting="ph"] input'
        page = self._watched(site)
        page.fill(ph, "7.5")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(ph)
        kept = page.input_value(ph)
        page.context.close()
        assert kept == "7.5"

    def test_a_save_waiting_as_the_page_is_hidden_is_made(self, site):
        """A phone may close a page switched away from without its pagehide:
        the save waiting is made as the page is hidden."""
        ph = '#run-settings [data-setting="ph"] input'
        page = self._watched(site)
        page.evaluate(self.HOLD)
        written = page.evaluate("() => window.__writes.length")
        page.fill(ph, "6.5")
        page.dispatch_event(ph, "change")
        page.evaluate("""() => { Object.defineProperty(document, 'visibilityState',
            {value: 'hidden', configurable: true});
            document.dispatchEvent(new Event('visibilitychange')); }""")
        made = page.evaluate("(n) => window.__writes.slice(n)", written)
        # A tab hidden keeps the page laid out: its save is not played.
        played = page.evaluate("() => document.getElementById('run-draft')"
                               ".classList.contains('just-saved')")
        page.context.close()
        assert played is False
        assert len(made) == 1
        values = json.loads(made[0]).get("values") or {}
        assert any(str((phase or {}).get("ph")) == "6.5" for phase in values.values())

    def test_a_save_that_changes_nothing_leaves_a_play_going(self, site):
        page = self._watched(site)
        page.add_style_tag(content="#run-draft.just-saved { animation-duration: 60s !important; }")
        self._plays(page, "6.5")
        self._plays(page, "6.5", changes=False)
        playing = page.evaluate("() => document.getElementById('run-draft')"
                                ".classList.contains('just-saved')")
        page.context.close()
        assert playing

    def test_an_agent_reply_does_not_overwrite_it(self, site):
        page = _open(site)
        page.fill('#run-settings [data-setting="ph"] input', "6.5")
        page.dispatch_event('#run-settings [data-setting="ph"] input', "change")
        cost = page.evaluate("""async () => {
            const response = await fetch('/api/load-config', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({config: {systems: [{system: '1UBQ'}],
                                               simulation: {duration_ns: 3}}})});
            const loaded = await response.json();
            const built = await window.FastMDXRun.forLoaded(loaded.state).fetchConfig();
            return built.yaml;
        }""")
        assert "duration_ns: 3" in cost
        assert page.evaluate("() => window.FastMDXRun.state.values.setup.ph") == "6.5"
        page.context.close()


class TestFindingAndChanging:
    def test_a_setting_is_found_by_its_name(self, site):
        page = _open(site)
        page.fill("#run-find", "ewald")
        page.wait_for_selector('#run-settings [data-setting="ewald_error_tolerance"]', state="visible")
        assert not page.is_visible('#run-settings [data-setting="ph"]')
        page.press("#run-find", "Escape")
        assert page.is_visible('#run-settings [data-setting="ph"]')
        page.context.close()

    def test_a_change_is_said_and_reverted(self, site):
        page = _open(site)
        ph = '#run-settings [data-setting="ph"]'
        page.fill(f"{ph} input", "7.0")
        page.dispatch_event(f"{ph} input", "change")
        assert "is-changed" in page.get_attribute(ph, "class")
        assert page.text_content("#run-changed-n") == "1"
        page.click(f"{ph} .builder-revert")
        assert "is-changed" not in page.get_attribute(ph, "class")
        assert page.input_value(f"{ph} input") == ""
        assert "ph" not in (_config(page).get("setup") or {})
        page.context.close()

    def test_a_reason_reaches_the_config(self, site):
        page = _open(site)
        ph = '#run-settings [data-setting="ph"]'
        page.fill(f"{ph} input", "7.0")
        page.dispatch_event(f"{ph} input", "change")
        page.click(f"{ph} .builder-why-add")
        page.fill(f"{ph} .builder-decision-edit textarea", "The assay buffer is pH 7.0.")
        page.fill(f"{ph} .builder-decision-edit input", "7.4")
        page.click(f"{ph} .builder-decision-edit .ghost-btn")
        assert _config(page)["decisions"] == {"setup.ph": {
            "why": "The assay buffer is pH 7.0.", "source": "person", "alternatives": ["7.4"]}}
        page.click(f"{ph} .builder-revert")
        assert "decisions" not in _config(page)
        page.context.close()

    def test_a_reason_goes_with_its_value_and_is_the_writer_s(self, site):
        page = _open(site)
        # A reason the Agent gave, rewritten here, is the person's.
        page.evaluate("""() => {
            const s = window.FastMDXRun.state;
            s.values.setup = Object.assign({}, s.values.setup, {ph: '7.0'});
            s.decisions['setup.ph'] = {why: 'The Agent read the paper.', source: 'agent'};
            window.FastMDXRun.renderSettings();
        }""")
        ph = '#run-settings [data-setting="ph"]'
        page.click(f"{ph} .builder-decision .builder-why-edit")
        page.fill(f"{ph} .builder-decision-edit textarea", "The assay buffer is pH 7.0.")
        page.click(f"{ph} .builder-decision-edit .ghost-btn")
        assert _config(page)["decisions"]["setup.ph"]["source"] == "person"
        # A choice taken back takes its reason with it, and brings it back.
        page.evaluate("""() => {
            const s = window.FastMDXRun.state;
            s.values.simulation = Object.assign({}, s.values.simulation,
                {metadynamics: {collective_variables: [{kind: 'distance', atoms: [1, 2]}]}});
            s.decisions['simulation.metadynamics'] = {why: 'The barrier is slow.', source: 'person'};
            window.FastMDXRun.renderSettings();
        }""")
        page.click('#run-settings [data-sampling="none"]')
        assert "simulation.metadynamics" not in _config(page).get("decisions", {})
        page.click('#run-settings [data-sampling="metadynamics"]')
        assert page.evaluate(
            "() => window.FastMDXRun.state.decisions['simulation.metadynamics'].why") == (
            "The barrier is slow.")
        # Reset forgets what was set aside.
        page.click('#run-settings [data-sampling="none"]')
        page.click("#run-reset")
        page.click('#run-settings [data-sampling="metadynamics"]')
        assert not page.evaluate("() => window.FastMDXRun.state.values.simulation"
                                 " && window.FastMDXRun.state.values.simulation.metadynamics")
        assert "simulation.metadynamics" not in page.evaluate("() => window.FastMDXRun.state.decisions")
        page.evaluate("""() => {
            const s = window.FastMDXRun.state;
            s.values.setup = Object.assign({}, s.values.setup, {ph: '7.0'});
            s.decisions['setup.ph'] = {why: 'The assay buffer is pH 7.0.', source: 'person'};
            s.values.simulation = Object.assign({}, s.values.simulation,
                {metadynamics: {collective_variables: [{kind: 'distance', atoms: [1, 2]}]}});
            s.decisions['simulation.metadynamics'] = {why: 'The barrier is slow.', source: 'person'};
            window.FastMDXRun.renderSettings();
        }""")
        # Without its phase, a reason has no setting to explain.
        page.evaluate("""() => {
            window.FastMDXRun.state.phases.delete('setup');
            window.FastMDXRun.renderSettings();
        }""")
        assert list(_config(page)["decisions"]) == ["simulation.metadynamics"]
        page.context.close()


class TestSeveralSystems:
    def test_a_study_of_two_is_submitted_whole(self, site):
        page = _open(site, structure=None)
        config = page.evaluate("""async () => {
            const response = await fetch('/api/load-config', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({config: {systems: [
                    {system: '1L2Y', id: 'trpcage'},
                    {system: '1UBQ', id: 'ubq', simulation: {duration_ns: 20}}]}})});
            const loaded = await response.json();
            await window.FastMDXRun.applyLoadedState(loaded.state, {});
            return window.FastMDXRun.currentState().systems;
        }""")
        assert config == [{"system": "1L2Y", "id": "trpcage"},
                          {"system": "1UBQ", "id": "ubq", "simulation": {"duration_ns": 20}}]
        assert page.locator("#run-systems .builder-system-row:not(.builder-system-head)").count() == 2
        assert "simulation.duration_ns: 20" in page.text_content("#run-systems")
        page.context.close()


class TestWhatTheStudyLeavesNothingFor:
    def test_the_analyses_that_will_not_run_say_why(self, site):
        page = _open(site, structure="ensemble.pdb")
        page.wait_for_function(
            "() => document.querySelector('#run-settings [data-setting=\"analysis:bfactor_comparison\"] .analyse-why')")
        why = page.text_content('#run-settings [data-setting="analysis:bfactor_comparison"] .analyse-why')
        lipid = page.text_content('#run-settings [data-setting="analysis:area_per_lipid"] .analyse-why')
        assert why == "An NMR entry has no crystallographic B-factors"
        assert lipid == "No membrane in this study"
        rmsd = '#run-settings [data-setting="analysis:rmsd"]'
        assert page.locator(f"{rmsd} .analyse-why").count() == 0
        page.context.close()


def _read(page):
    """Once the structure is read, what applies is known."""
    page.wait_for_function("() => (window.FastMDXRun.state.estimate || {}).not_applicable")


class TestChoosingAnalyses:
    def test_a_pair_can_be_chosen_and_then_runs(self, site):
        page = _open(site)
        _read(page)
        page.click('#run-analyses-mode [data-value="only"]')
        row = '#run-settings [data-setting="analysis:pair_distance"]'
        page.wait_for_selector(f"{row} .analyse-why")
        assert page.text_content(f"{row} .analyse-why") == (
            "Runs only when chosen, with its selections named")
        page.check(f"{row} input[type=checkbox]")
        page.wait_for_function(
            "() => !document.querySelector('#run-settings [data-setting=\"analysis:pair_distance\"] .analyse-why')")
        assert "pair_distance" in _config(page)["analysis"]["include"]
        page.context.close()

    def test_a_pair_that_needs_water_stays_out(self, site):
        page = _open(site)
        _read(page)
        page.click('#run-analyses-mode [data-value="only"]')
        row = '#run-settings [data-setting="analysis:coordination_number"]'
        assert page.text_content(f"{row} .analyse-why") == "Needs water in the frames"
        assert page.is_disabled(f"{row} input[type=checkbox]")
        page.context.close()

    def test_none_chosen_is_said_and_runs_nothing(self, site):
        page = _open(site)
        _read(page)
        page.click('#run-analyses-mode [data-value="only"]')
        page.evaluate("""() => {
            document.querySelectorAll('#run-settings .analyse-row input[type=checkbox]:checked')
              .forEach((box) => box.click());
        }""")
        page.wait_for_function("() => document.getElementById('run-start-button').disabled")
        assert page.text_content("#run-note") == (
            "Choose at least one analysis, or run every one that applies.")
        page.context.close()


class TestTheSummary:
    def test_it_says_whether_the_study_will_run(self, site):
        page = _open(site)
        page.wait_for_function("() => document.getElementById('run-status').dataset.state === 'ok'")
        assert page.text_content("#run-status") == "Checks pass"
        assert "systems:" in page.text_content("#run-config-preview")
        assert page.text_content("#run-start-button") == "Run on this machine"
        assert page.errors == []
        page.context.close()

    def test_the_agent_opens_beside_it(self, site):
        page = _open(site, structure=None)
        page.click("#run-step-start .builder-agent-link")
        page.wait_for_function("() => !document.getElementById('agent-drawer').hidden")
        assert page.evaluate("() => location.hash") == "#run"
        assert page.evaluate("() => document.activeElement.id") == "agent-request"
        page.context.close()

    def test_the_side_panel_is_closed_on_this_page(self, site):
        page = _open(site, structure=None)
        page.evaluate("() => window.FastMDXDashboard.navigate('run')")
        page.wait_for_function("() => document.body.classList.contains('panel-collapsed')")
        page.context.close()


class TestItsIcons:
    """The builder's everyday buttons are their icons, named on hover and to
    a screen reader by the names the docs give them, and its small ones keep
    their size beside a setting's name."""

    def test_each_is_named_and_the_small_ones_stay_small(self, site):
        page = _open(site)
        ph = '#run-settings [data-setting="ph"]'
        page.fill(f"{ph} input", "7.0")
        page.dispatch_event(f"{ph} input", "change")
        named = page.evaluate("""(ids) => ids.map(id => {
            const b = document.getElementById(id);
            return [b.getAttribute('aria-label'), b.title.split(':')[0],
                    !!b.querySelector('svg.line-icon'), b.textContent.trim()];
        })""", ["run-download", "run-copy-command", "run-download-script", "run-open-config"])
        small = page.evaluate("""(ph) => {
            const b = document.querySelector(ph + ' .builder-revert');
            const r = b.getBoundingClientRect();
            return [b.getAttribute('aria-label'), b.title, !!b.querySelector('svg.line-icon'),
                    r.height <= 20];
        }""", ph)
        page.context.close()
        assert named == [["Download config", "Download config", True, ""],
                         ["Copy the command", "Copy the command", True, ""],
                         ["Download a script", "Download a script", True, ""],
                         ["Open and change it", "Open and change it", True, ""]]
        assert small == ["pH back to the default", "pH back to the default", True, True]
