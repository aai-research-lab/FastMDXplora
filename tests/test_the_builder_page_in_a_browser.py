"""The Config Builder, driven in a browser.

Each check is something the page got wrong before it was rebuilt, or a
promise the rebuild makes:

- a list of choices with no default leaves the setting alone, and choosing
  a value and then "Not set" again writes nothing (a protein in water read
  as embedded in POPC, and choosing it back wrote `membrane: POPC`);
- Tab out of a changed setting goes to the next control, not the page (every
  change built the whole form again);
- the form is saved in the browser, so a reload loses nothing, and Reset
  puts every setting back, the starting point and the structure kept;
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
        page.click(f"{ph} .builder-decision .builder-linkish")
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
