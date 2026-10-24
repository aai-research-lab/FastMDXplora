"""Every setting on one Settings page (IMDE 2.9, user 10-21).

Settings were four places: the gear's popup (theme, accent, the Agent),
the Preferences dialog (the Viewer, time, charts, notices), the Agent's
own dialog, and the workspace's `fastmdx-defaults.yml`, which the GUI read
for new studies and could not change. They are one page, found by their
words, with two scopes: You, saved in this browser, and This folder, the
defaults file every new study made here starts from. Each applies as it
is changed. The Display size (the ideas session's large-screen entry,
Auto by default, user 10-21) is one of You's: the whole page at once,
text included, Auto from the screen.
"""

from __future__ import annotations

import pytest
import yaml

from tests.test_the_workspace_says_its_studies import _study


# --------------------------------------------------------------------------
# The defaults file, written
# --------------------------------------------------------------------------


def test_a_defaults_file_is_written_checked_and_taken_out_again(tmp_path):
    from fastmdxplora.config.defaults_file import read_defaults, write_defaults

    where = tmp_path / "fastmdx-defaults.yml"
    write_defaults(where, given={"simulation.temperature_K": 310, "analysis.include": ["rmsd"]},
                   whys={"simulation.temperature_K": "body temperature"})
    assert yaml.safe_load(where.read_text(encoding="utf-8")) == {
        "simulation": {"temperature_K": 310},
        "analysis": {"include": ["rmsd"]},
        "decisions": {"simulation.temperature_K": {"why": "body temperature"}},
    }
    # Another setting leaves the others as they are.
    write_defaults(where, given={"setup.ph": 7.0})
    found = read_defaults(where)
    assert dict(found.settings()) == {"setup.ph": 7.0, "simulation.temperature_K": 310,
                                      "analysis.include": ["rmsd"]}
    # Taken out, its reason goes with it; an empty reason is none.
    write_defaults(where, unset=["simulation.temperature_K"], whys={"setup.ph": ""})
    found = read_defaults(where)
    assert dict(found.settings()) == {"setup.ph": 7.0, "analysis.include": ["rmsd"]}
    assert dict(found.whys) == {}
    # Nothing is left beside it, and it is readable as it was.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fastmdx-defaults.yml"]
    where.chmod(0o644)
    write_defaults(where, given={"setup.ph": 7.4})
    assert where.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("given", [
    {"simulation.temperature_K": "hot"},
    {"simulation.timestep_fs": -2},
    {"simulation.no_such_setting": 1},
    {"systems.x": 1},
    {"simulation.umbrella.windows": 3},
])
def test_a_value_any_defaults_file_refuses_leaves_the_file_as_it_was(tmp_path, given):
    from fastmdxplora.config.defaults_file import write_defaults
    from fastmdxplora.config.loader import ConfigError

    where = tmp_path / "fastmdx-defaults.yml"
    write_defaults(where, given={"setup.ph": 7.0})
    before = where.read_text(encoding="utf-8")
    with pytest.raises(ConfigError) as refused:
        write_defaults(where, given=given)
    assert where.read_text(encoding="utf-8") == before
    # Said of the file, never of the file written beside it to check.
    assert ".fastmdx-defaults." not in str(refused.value)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fastmdx-defaults.yml"]


def test_a_file_wrong_already_is_not_written_over(tmp_path):
    from fastmdxplora.config.defaults_file import write_defaults
    from fastmdxplora.config.loader import ConfigError

    where = tmp_path / "fastmdx-defaults.yml"
    wrong = "setup:\n  ph: acid\n"
    where.write_text(wrong, encoding="utf-8")
    with pytest.raises(ConfigError):
        write_defaults(where, given={"simulation.temperature_K": 310})
    assert where.read_text(encoding="utf-8") == wrong


def test_a_file_with_comments_is_changed_by_hand(tmp_path):
    """Written again from its values, a file would lose its comments: Settings
    says so and leaves it, and shows it as it is."""
    from fastmdxplora.gui.settings_page import defaults_changed, defaults_said, has_comments

    commented = "# the lab's\nsetup:\n  ph: 7.0  # as the buffer\n"
    where = tmp_path / "fastmdx-defaults.yml"
    where.write_text(commented, encoding="utf-8")
    said = defaults_said(tmp_path)
    assert said["comments"] and said["values"] == {"setup.ph": 7.0}
    refused = defaults_changed(tmp_path, {"set": {"setup.ph": 6.5}})
    assert refused["ok"] is False and "comments" in refused["reason"]
    assert where.read_text(encoding="utf-8") == commented
    # A colour is no comment.
    assert not has_comments("report:\n  region_highlights:\n  - {label: a, start: 1, end: 3, "
                            "color: '#4E79A7'}\n")


# --------------------------------------------------------------------------
# This folder, as Settings says it
# --------------------------------------------------------------------------


def test_this_folder_says_every_setting_with_fastmdxplora_s_own(tmp_path):
    from fastmdxplora.gui.settings_page import defaults_said

    said = defaults_said(tmp_path)
    assert said["ok"] and not said["exists"]
    assert said["path"] == str(tmp_path / "fastmdx-defaults.yml")
    fields = {f["name"]: f for f in said["fields"]}
    assert {f["block"] for f in said["fields"]} == {"setup", "simulation", "analysis", "report",
                                                    "execution"}
    assert fields["simulation.temperature_K"]["default"] == 300
    assert fields["simulation.temperature_K"]["control"] == "number"
    # A list of numbers is written from numbers between commas; one of
    # mappings is not drawn, and is changed in the file by hand.
    assert fields["simulation.restraint_release"]["items"] == "number"
    assert fields["setup.force_field"]["items"] == "text"
    assert fields["analysis.options"]["control"] == ""
    # Who chose a phase's settings is the study's record, and a setting of
    # one study (what it continues or is prepared from, the files it
    # analyses, what it simulates) is never a folder's: not offered.
    for one_study_s in ("setup.agent", "simulation.resume_from", "simulation.resume_unsealed",
                        "simulation.setup_from", "simulation.prepared_from",
                        "simulation.extra_ns", "analysis.trajectory", "analysis.topology",
                        "setup.fixed_pdb", "setup.ligand", "setup.ligand_pose",
                        "setup.mutations", "setup.chains", "setup.model",
                        "setup.ligand_net_charge", "setup.ligand_name", "setup.ligand_resname",
                        "setup.mutation_chain", "setup.residue_states",
                        "setup.membrane_center_z_nm", "simulation.dashboard_ligand_resname",
                        "simulation.umbrella", "simulation.steered", "simulation.metadynamics",
                        "simulation.plumed", "setup.membrane_orientation_checked",
                        "report.region_highlights", "report.title"):
        assert one_study_s not in fields, one_study_s


def test_a_file_setting_what_is_simulated_is_refused_and_said(tmp_path):
    """`L99A` in the folder's file would mutate every new study's protein."""
    from fastmdxplora.gui.settings_page import defaults_changed, defaults_said

    where = tmp_path / "fastmdx-defaults.yml"
    where.write_text("setup:\n  mutations: [L99A]\n", encoding="utf-8")
    said = defaults_said(tmp_path)
    # Said once, under the file's own name: not its path again.
    assert said["refused"].startswith("It sets `setup.mutations`, which says what one study "
                                      "simulates"), said["refused"]
    assert str(where) not in said["refused"] and said["values"] == {}
    refused = defaults_changed(tmp_path, {"set": {"setup.ph": 7.0}})
    assert refused["ok"] is False and str(where) not in refused["reason"]
    assert where.read_text(encoding="utf-8") == "setup:\n  mutations: [L99A]\n"
    # Asked of a file that is right, it is refused the same, the file as it was.
    where.write_text("setup:\n  ph: 7.0\n", encoding="utf-8")
    refused = defaults_changed(tmp_path, {"set": {"simulation.resume_from": "runs/old"}})
    assert refused["ok"] is False
    assert refused["reason"].startswith("It sets `simulation.resume_from`, which names the study")
    assert str(where) not in refused["reason"]
    assert where.read_text(encoding="utf-8") == "setup:\n  ph: 7.0\n"


def test_a_ligand_s_charge_in_the_file_is_refused_and_said(tmp_path):
    """`ligand_net_charge: -1` in the folder's file gave every new study's
    ligand that charge: a wrong one where its file does not settle it, and
    a study refused where it does."""
    from fastmdxplora.gui.settings_page import defaults_changed, defaults_said

    where = tmp_path / "fastmdx-defaults.yml"
    where.write_text("setup:\n  ligand_net_charge: -1\n", encoding="utf-8")
    said = defaults_said(tmp_path)
    assert said["refused"].startswith("It sets `setup.ligand_net_charge`, which says one "
                                      "ligand's formal charge"), said["refused"]
    assert str(where) not in said["refused"] and said["values"] == {}
    where.write_text("setup:\n  ph: 7.0\n", encoding="utf-8")
    refused = defaults_changed(tmp_path, {"set": {"setup.ligand_net_charge": -1}})
    assert refused["ok"] is False
    assert refused["reason"].startswith("It sets `setup.ligand_net_charge`")
    assert where.read_text(encoding="utf-8") == "setup:\n  ph: 7.0\n"


def test_a_setting_of_several_values_the_file_holds_is_shown(tmp_path):
    """A stopping rule in the file makes every new study run until it is
    determined, up to its ceiling: shown as the file holds it, and counted."""
    from fastmdxplora.gui.settings_page import defaults_said

    (tmp_path / "fastmdx-defaults.yml").write_text(
        "simulation:\n  temperature_K: 310\n  stop_when:\n    measures:\n"
        "    - analysis: rmsd\n      standard_error: 0.01\n    max_duration_ns: 50\n"
        "setup:\n  dispersion_correction: null\n", encoding="utf-8")
    said = defaults_said(tmp_path)
    fields = {f["name"]: f for f in said["fields"]}
    assert set(said["values"]) <= set(fields)
    for several in ("simulation.stop_when", "analysis.options"):
        assert fields[several]["control"] == "", several
    assert said["values"]["simulation.stop_when"]["max_duration_ns"] == 50


def test_two_changes_at_once_are_both_written(tmp_path, monkeypatch):
    """Each request is answered in its own thread: two changes that read
    the file before either was written lost the first."""
    import os
    import threading
    import time

    from fastmdxplora.config.defaults_file import read_defaults
    from fastmdxplora.gui.settings_page import defaults_changed

    replace = os.replace

    def slowly(source, target):
        time.sleep(0.4)
        replace(source, target)

    monkeypatch.setattr(os, "replace", slowly)
    asked = [{"set": {"setup.ph": 7.0}}, {"set": {"simulation.temperature_K": 310}}]
    threads = [threading.Thread(target=defaults_changed, args=(tmp_path, body)) for body in asked]
    for thread in threads:
        thread.start()
        time.sleep(0.1)
    for thread in threads:
        thread.join()
    assert dict(read_defaults(tmp_path / "fastmdx-defaults.yml").settings()) == {
        "setup.ph": 7.0, "simulation.temperature_K": 310}


def test_a_file_that_cannot_be_written_is_said_without_its_path_again(tmp_path, monkeypatch):
    """The lead names the file; the reason beside the setting said its path
    a second time."""
    import tempfile

    from fastmdxplora.gui.settings_page import defaults_changed

    def refused(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(tempfile, "mkstemp", refused)
    said = defaults_changed(tmp_path, {"set": {"setup.ph": 7.0}})
    assert said["ok"] is False
    assert said["reason"] == "It could not be written: Permission denied.", said["reason"]


def test_the_nearest_file_above_is_the_one_shown_and_changed(tmp_path):
    from fastmdxplora.gui.settings_page import defaults_changed, defaults_said

    (tmp_path / "fastmdx-defaults.yml").write_text("setup:\n  ph: 7.0\n", encoding="utf-8")
    workspace = tmp_path / "studies"
    workspace.mkdir()
    said = defaults_said(workspace)
    assert said["exists"] and said["path"] == str(tmp_path / "fastmdx-defaults.yml")
    assert said["values"] == {"setup.ph": 7.0}
    changed = defaults_changed(workspace, {"set": {"simulation.temperature_K": 310},
                                           "why": {"simulation.temperature_K": "warm"}})
    assert changed["ok"] and changed["values"]["simulation.temperature_K"] == 310
    assert changed["whys"] == {"simulation.temperature_K": "warm"}
    assert not (workspace / "fastmdx-defaults.yml").exists()
    refused = defaults_changed(workspace, {"set": {"simulation.temperature_K": -5}})
    assert refused["ok"] is False
    assert refused["reason"].startswith("Simulation option 'temperature_K'")
    assert str(tmp_path) not in refused["reason"]
    assert refused["values"]["simulation.temperature_K"] == 310


# --------------------------------------------------------------------------
# In a browser
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


@pytest.fixture
def session(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "ubiquitin", system="1UBQ")
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


def _open(browser, url, where="#settings", screen=None, folder_404=False):
    options = {"viewport": {"width": 1440, "height": 900}}
    if screen:
        options["screen"] = {"width": screen[0], "height": screen[1]}
    context = browser.new_context(**options)
    page = context.new_page()
    page.set_default_timeout(60000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    if folder_404:
        # A server without This folder's route (the GUI before it).
        page.route("**/api/defaults-file", lambda route: route.fulfill(status=404, body="{}"))
    page.goto(url + where, wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard && window.FastMDXSettings")
    page.wait_for_selector("body:not(.state-loading)")
    return page


def test_one_page_two_scopes_one_search(browser, session):
    page = _open(browser, session.url, folder_404=True)
    page.wait_for_selector('.page[data-page="settings"]:not([hidden])')
    shown = page.evaluate("""() => ['settings-you', 'settings-folder']
        .map((id) => !document.getElementById(id).hidden)""")
    # Every preference that was a dialog's, and the popup's theme and
    # accent, are rows of You.
    rows = page.evaluate("""() => [...document.querySelectorAll('#settings-you .pref-row > span:first-child')]
        .map((s) => s.textContent.trim())""")
    page.fill("#settings-find", "water")
    found = page.evaluate("""() => [...document.querySelectorAll('#settings-you .pref-row')]
        .filter((r) => !r.hidden).map((r) => r.textContent.trim())""")
    counted = page.evaluate("""() => [...document.querySelectorAll('.settings-count')]
        .map((c) => c.hidden ? null : c.textContent)""")
    page.fill("#settings-find", "no such words")
    none = page.text_content("#settings-none-you")
    page.keyboard.press("Escape")
    cleared = page.input_value("#settings-find")
    # The keyboard goes from scope to scope with the arrows.
    page.focus("#settings-scope-you")
    page.keyboard.press("ArrowRight")
    moved = page.evaluate("""() => [document.activeElement.id,
        document.getElementById('settings-folder').hidden, document.getElementById('settings-you').hidden]""")
    page.wait_for_selector("#settings-folder-body .settings-cannot")
    cannot = page.text_content("#settings-folder-body")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert shown == [True, False]
    for name in ("Theme", "Accent", "Display size", "Representation", "Ground", "Water", "Ions",
                 "Spin", "Pocket cutoff (Å)", "Time", "Chart history", "Notify when a run ends",
                 "Engine", "Mode"):
        assert any(r.startswith(name) for r in rows), name
    assert len(found) == 1 and found[0].startswith("Water")
    # This folder's count is not said where its file could not be read.
    assert counted == ["1", None]
    assert none.startswith("No setting here has those words.")
    assert cleared == ""
    assert moved == ["settings-scope-folder", False, True]
    assert "cannot read the folder's defaults yet" in cannot


def test_the_popup_the_agent_s_dialog_and_the_palette_lead_here(browser, session):
    page = _open(browser, session.url, "#analysis", folder_404=True)
    page.click("#settings-open")
    page.click("#settings-page-link")
    page.wait_for_selector('.page[data-page="settings"]:not([hidden])')
    from_popup = (page.is_hidden("#settings-popup"), page.evaluate("() => location.hash"))
    page.evaluate("() => window.FastMDXDashboard.navigate('overview')")
    # The Agent's dialog, to its part of the page.
    page.evaluate("() => window.FastMDXAgent.openSettings()")
    page.wait_for_selector("#agent-settings:not([hidden])")
    page.click('#agent-settings [data-settings-part-link="agent"]')
    page.wait_for_selector('.page[data-page="settings"]:not([hidden])')
    page.wait_for_function("() => document.activeElement.id === 'settings-page-agent'")
    from_agent = (page.is_hidden("#agent-settings"),
                  page.text_content("#settings-page-mode") == page.text_content("#settings-mode"))
    # And back to the Agent's dialog from there.
    page.click("#settings-page-agent")
    page.wait_for_selector("#agent-settings:not([hidden])")
    page.keyboard.press("Escape")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert from_popup == (True, "#settings")
    assert from_agent == (True, True)


def test_the_theme_here_is_the_popup_s(browser, session):
    page = _open(browser, session.url, folder_404=True)
    page.click('#settings-you [data-theme="light"]')
    page.click('#settings-you [data-accent="violet"]')
    said = page.evaluate("""() => [document.documentElement.dataset.theme,
        document.documentElement.dataset.accent,
        document.querySelector('#settings-popup [data-theme="light"]').getAttribute('aria-pressed'),
        document.querySelector('#settings-popup [data-accent="violet"]').getAttribute('aria-pressed')]""")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body:not(.state-loading)")
    again = page.evaluate("""() => [document.documentElement.dataset.theme,
        document.querySelector('#settings-you [data-accent="violet"]').getAttribute('aria-pressed')]""")
    page.context.close()
    assert said == ["light", "violet", "true", "true"]
    assert again == ["light", "true"]


@pytest.mark.parametrize("screen, auto", [((1920, 1080), 100), ((2560, 1440), 125),
                                          ((3440, 1440), 125), ((3840, 2160), 150),
                                          ((1366, 768), 100)])
def test_auto_is_chosen_from_the_screen(browser, session, screen, auto):
    page = _open(browser, session.url, screen=screen, folder_404=True)
    said = page.evaluate("""() => [window.FastMDXDisplay.chosen(), window.FastMDXDisplay.zoom(),
        document.getElementById('setting-display-size').selectedOptions[0].textContent]""")
    page.context.close()
    assert said == ["auto", auto / 100, f"Auto: {auto}%"]


def test_a_display_size_chosen_sizes_everything_and_is_saved(browser, session):
    page = _open(browser, session.url, screen=(1920, 1080), folder_404=True)
    page.select_option("#setting-display-size", "125")
    page.wait_for_function("() => window.FastMDXDisplay.zoom() === 1.25")
    sized = page.evaluate("""() => {
        const shell = document.querySelector('.app-shell');
        const title = document.querySelector('.page[data-page="settings"] .page-title');
        const box = title.getBoundingClientRect();
        return {
            zoom: getComputedStyle(document.documentElement).zoom,
            saved: localStorage.getItem('fmx.displaySize'),
            // The page is told of the screen in its own pixels: the
            // shell fills the window it is told of down to the status
            // bar, which ends at its foot, and a box is where the page
            // put it.
            fits: Math.abs(shell.getBoundingClientRect().bottom
                           - document.querySelector('.status-bar').getBoundingClientRect().top) < 2
                && Math.abs(document.querySelector('.status-bar').getBoundingClientRect().bottom
                            - innerHeight) < 2,
            told: innerWidth,
            box: Math.abs(box.width - title.offsetWidth) < 1.5,
        };
    }""")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body:not(.state-loading)")
    again = page.evaluate("() => [window.FastMDXDisplay.zoom(), "
                          "document.getElementById('setting-display-size').value]")
    # Restore defaults puts Auto back.
    page.click("#prefs-restore")
    page.wait_for_function("() => window.FastMDXDisplay.zoom() === 1")
    restored = page.evaluate("() => [localStorage.getItem('fmx.displaySize'), "
                             "document.getElementById('setting-display-size').value]")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert sized["zoom"] == "1.25" and sized["saved"] == "125"
    assert sized["fits"] and sized["told"] == 1440 / 1.25 and sized["box"]
    assert again == [1.25, "125"]
    assert restored == ["auto", "auto"]


def _at(browser, url, size, viewport, where="#overview", folder=False):
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
    context.add_init_script(f"try {{ localStorage.setItem('fmx.displaySize', '{size}'); }} "
                            "catch (e) {}")
    page = context.new_page()
    page.set_default_timeout(60000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    if not folder:
        page.route("**/api/defaults-file", lambda route: route.fulfill(status=404, body="{}"))
    page.goto(url + where, wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard && window.FastMDXSettings")
    page.wait_for_selector("body:not(.state-loading)")
    return page


def _box(page, selector):
    return page.evaluate(f"""() => {{ const r = document.querySelector('{selector}')
        .getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }}""")


@pytest.mark.parametrize("size", ["90", "110", "125", "150"])
def test_at_every_display_size_tips_and_menus_land_where_they_are_seen(browser, session, size):
    """A tooltip was placed from the window's height read from the root
    element, which said the screen's pixels: at 125% the status bar's tip
    was put wholly below the window, and one near the right edge ran past
    it. The gear's menu, opened by a click where its button is seen, opens
    over it; and the columns end at the status bar at every size."""
    page = _at(browser, session.url, size, (1440, 900))
    zoom = int(size) / 100
    said = {"window": page.evaluate("() => [innerWidth, innerHeight]")}
    shell, bar = _box(page, ".app-shell"), _box(page, ".status-bar")
    said["columns"] = abs(shell[3] - bar[1]) < 2 and abs(bar[3] - said["window"][1]) < 2
    tips = {}
    for target in ("#sb-machine", "#refresh-now"):
        at = _box(page, target)
        page.mouse.move(0, 0)
        page.wait_for_timeout(350)
        page.mouse.move((at[0] + at[2]) / 2 * zoom, (at[1] + at[3]) / 2 * zoom)
        page.wait_for_selector("#fmx-tooltip:not([hidden])")
        tip = _box(page, "#fmx-tooltip")
        tips[target] = {"inside": tip[0] >= 0 and tip[1] >= 0 and tip[2] <= said["window"][0] + 0.5
                        and tip[3] <= said["window"][1] + 0.5,
                        "beside": min(abs(at[1] - tip[3]), abs(tip[1] - at[3])) < 12}
    page.mouse.move(0, 0)
    gear = _box(page, "#settings-open")
    page.mouse.click((gear[0] + gear[2]) / 2 * zoom, (gear[1] + gear[3]) / 2 * zoom)
    page.wait_for_selector("#settings-popup:not([hidden])")
    menu = _box(page, "#settings-popup")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert abs(said["window"][0] - 1440 / zoom) < 0.01, said
    assert said["columns"], said
    for target, placed in tips.items():
        assert placed == {"inside": True, "beside": True}, (target, placed)
    assert menu[0] >= 0 and menu[1] >= 0 and menu[2] <= said["window"][0] + 0.5
    assert menu[3] <= said["window"][1] + 0.5
    # Over the button it was opened from, its foot on the button.
    assert menu[0] < gear[2] and gear[0] < menu[2], (menu, gear)
    assert gear[1] - 12 < menu[3] <= gear[3] + 12, (menu, gear)


def test_a_rule_within_container_holds_where_it_holds_at_100(browser, session):
    """The rules sized by the window are copied for the Display size; those
    within `@container` were copied bare, so held at every width: at 125%
    the Run page's one-column summary was capped as the wide one is, and
    the Report's outline ran past the window's foot."""
    page = _at(browser, session.url, "125", (1063, 1125), "#run")
    page.wait_for_selector('.page[data-page="run"] .builder-summary')
    summary = page.evaluate("""() => { const c = getComputedStyle(
        document.querySelector('.page[data-page="run"] .builder-summary'));
        return [c.position, c.maxHeight, c.overflow]; }""")
    page.context.close()
    page = _at(browser, session.url, "125", (1920, 1080), "#report")
    page.wait_for_selector(".report-outline", state="attached")
    outline = page.evaluate("""() => { const c = getComputedStyle(document.querySelector('.report-outline'));
        return [c.position, parseFloat(c.maxHeight), innerHeight]; }""")
    errors = page.errors
    page.context.close()
    assert errors == []
    # One column, as an 850 by 900 window has at 100%: the summary under the form.
    assert summary == ["static", "none", "visible"], summary
    assert outline[0] == "sticky" and outline[1] <= outline[2] - 96 + 0.5, outline


def test_a_media_query_is_compared_at_the_page_s_size(browser, session):
    """The Display size is one zoom: at 150% a 1100 pixel window is a page
    733 wide, and is laid out as a 733 pixel window is at 100%, by its
    style sheets and its scripts alike. The media queries were compared
    with the window's width, so it had the wide layout in a page too
    narrow for it, while the scripts, told the page's width, laid out the
    narrow one."""
    look = """() => {
        const of = (selector) => { const e = document.querySelector(selector);
            if (!e) return null; const c = getComputedStyle(e);
            return [c.display, c.position, c.flexDirection,
                    c.gridTemplateColumns.split(' ').filter(Boolean).length]; };
        return {css: ['.app-shell', '.sidebar', '.side-panel', '.main', '.status-bar',
                      '.page[data-page="overview"] .page-header', '.page-subtitle'].map(of),
                script: [matchMedia('(max-width: 800px)').matches,
                         matchMedia('(min-width: 801px)').matches, innerWidth <= 800]}; }"""
    page = _at(browser, session.url, "100", (733, 600))
    at_100 = page.evaluate(look)
    page.context.close()
    page = _at(browser, session.url, "150", (1100, 900))
    at_150 = page.evaluate(look)
    errors = page.errors
    page.context.close()
    assert errors == []
    assert at_100["script"] == [True, False, True]
    assert at_150 == at_100, (at_150, at_100)


def test_a_pair_of_media_queries_meets_at_every_display_size(browser, session):
    """`(max-width: 800px)` and `(min-width: 801px)` times 1.25 were 1000
    and 1001.25: a window 1001 wide held neither, its sidebar neither the
    narrow layout's nor the wide one's (and the same at 881 for 110%, 1201
    for 150%). Each pair meets at every size, and the scripts say the
    layout the style sheets do."""
    look = """() => [matchMedia('(max-width: 800px)').matches,
                     matchMedia('(min-width: 801px)').matches, innerWidth <= 800,
                     getComputedStyle(document.querySelector('.sidebar')).position]"""
    said = {}
    for size, width in (("100", 800), ("100", 801), ("125", 1000), ("125", 1001),
                        ("110", 881), ("150", 1201)):
        page = _at(browser, session.url, size, (width, 800))
        said[(size, width)] = page.evaluate(look)
        page.context.close()
    narrow, wide = said[("100", 800)], said[("100", 801)]
    assert narrow[:3] == [True, False, True] and wide[:3] == [False, True, False]
    assert narrow[3] != wide[3]
    assert said[("125", 1000)] == narrow, said
    for key in (("125", 1001), ("110", 881), ("150", 1201)):
        assert said[key] == wide, (key, said[key], wide)


def test_a_query_a_script_made_follows_a_display_size_chosen(browser, session):
    """The phone's Problems dialog closes as the window widens past a
    phone's. Its query was made once, as the page loaded: loaded at 125%,
    then 100% chosen, it still said 1000 pixels, and the dialog stayed
    open in a window 900 wide."""
    page = _at(browser, session.url, "125", (700, 900))
    try:
        page.click("#problems-phone")
        page.wait_for_function("() => document.getElementById('problems-mobile').open")
        page.evaluate("() => window.FastMDXDisplay.choose('100')")
        page.wait_for_function("() => window.FastMDXDisplay.zoom() === 1")
        # Still a phone's width at 100%: open.
        still = page.evaluate("() => document.getElementById('problems-mobile').open")
        page.set_viewport_size({"width": 900, "height": 900})
        page.wait_for_function("() => !document.getElementById('problems-mobile').open", timeout=10000)
        errors = page.errors
    finally:
        page.context.close()
    assert errors == []
    assert still


@pytest.mark.parametrize("width", [900, 1250])
def test_at_150_settings_is_laid_out_for_the_room_it_has(browser, session, width):
    """At 150% a 900 pixel window is a page 600 wide, and beside the Agent
    at 1250 the page had about 380: the accents were drawn over their name,
    the scopes broke inside their words, the subtitle was cut to "E." and
    This folder's words stood in a column 100 pixels wide.

    The Agent stands beside the page only where the strip (56), the Agent
    at its least (340) and a page at its least (480) with its margins fit,
    in the page's pixels (932). At 150% a 1250 window is a page 833 wide,
    so the Agent is over the page, as in an 833 window at 100%, and this
    case measures Settings under it; at 125% the page is 1000 wide, and
    the Agent is beside it with the page at least 480 wide."""
    page = _at(browser, session.url, "150", (width, 900), "#settings", folder=True)
    agent = """() => { const main = document.querySelector('.main').getBoundingClientRect();
        return [parseFloat(getComputedStyle(document.querySelector('.app-shell')).paddingRight),
                main.width, main.right - document.getElementById('agent-drawer')
                    .getBoundingClientRect().left]; }"""
    if width == 1250:
        page.keyboard.press("Control+j")
        page.wait_for_selector("#agent-drawer:not([hidden])")
        over = page.evaluate(agent)
        # Over the page: the shell keeps its width, and the drawer covers it.
        assert over[0] == 0 and over[2] > 300, over
        beside = _at(browser, session.url, "125", (width, 900), "#settings", folder=True)
        beside.keyboard.press("Control+j")
        beside.wait_for_selector("#agent-drawer:not([hidden])")
        beside.wait_for_function("() => parseFloat(getComputedStyle("
                                 "document.querySelector('.app-shell')).paddingRight) > 0")
        at_125 = beside.evaluate(agent)
        beside.context.close()
        # Beside it: the page ends where the drawer starts, at least 480 wide.
        assert at_125[1] >= 480 and at_125[2] <= 1, at_125
    said = {}
    for scope in ("you", "folder"):
        page.click(f"#settings-scope-{scope}")
        if scope == "folder":
            page.wait_for_selector("#settings-folder-body .settings-field")
        page.wait_for_timeout(300)
        said[scope] = page.evaluate("""() => {
            const box = (e) => e.getBoundingClientRect();
            const name = box(document.getElementById('settings-page-accent'));
            const accents = [...document.querySelectorAll('#settings-page-accents .accent-swatch')]
                .map(box).filter((r) => r.width);
            const lines = (node) => { const r = document.createRange(); r.selectNodeContents(node);
                return r.getClientRects().length; };
            const words = [...document.querySelectorAll('.settings-scope')].flatMap((tab) =>
                [...tab.childNodes].filter((n) => (n.nodeType === 3 && n.textContent.trim())
                    || (n.nodeType === 1 && n.offsetParent && n.textContent.trim()))
                .map((n) => [n.textContent.trim(), lines(n)]));
            const sub = document.querySelector('.settings-page .page-subtitle');
            const sc = document.scrollingElement;
            return {
                over: accents.some((r) => r.left < name.right - 0.5 && r.top < name.bottom
                                          && r.bottom > name.top),
                broken: words.filter((w) => w[1] > 1).map((w) => w[0]),
                cut: Boolean(sub.offsetParent) && sub.scrollWidth > sub.clientWidth + 1,
                narrowest: Math.min(...[...document.querySelectorAll('#settings-folder-body .settings-words')]
                    .filter((e) => e.offsetParent).map((e) => box(e).width)),
                sideways: sc.scrollWidth > sc.clientWidth + 1,
            }; }""")
    errors = page.errors
    page.context.close()
    assert errors == []
    for scope, layout in said.items():
        assert not layout["over"] and layout["broken"] == [] and not layout["cut"], (scope, layout)
        assert not layout["sideways"], (scope, layout)
    assert said["folder"]["narrowest"] >= 240, said["folder"]


def test_a_display_size_never_makes_the_page_narrower_than_a_phone(browser, tmp_path):
    """At 150% a 390 pixel phone was a page 260 wide: Analysis, All studies
    and Files scrolled sideways, the sort select and the Files actions past
    the edge. The size applied is the largest not above the one chosen that
    keeps the page at least 390 wide, again as the window is resized, and
    Settings says so beside the select. Each page is checked with what it
    shows: a quantity the same in every frame ran 6 pixels past Analysis at
    390, and Files lists a trajectory with a long name."""
    import json

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "helical", system="2I9M",
                   means={"rmsd": (0.213, 0.011), "radius_of_gyration": (1.104, 0.02)})
    # A quantity the same in every frame (an NVT run's kinetic energy at
    # its thermostat's value): its line ran past a 390 page.
    thermodynamics = study / "analysis" / "thermodynamics"
    thermodynamics.mkdir()
    (thermodynamics / "options.json").write_text(json.dumps({
        "analysis": "thermodynamics", "findings": {"thermodynamics": {
            "kinetic_energy": {"units": "kJ/mol", "mean": 8500.0, "standard_error": None,
                               "standard_deviation": 0.0, "effective_samples": 1.0}}}}),
        encoding="utf-8")
    (thermodynamics / "thermodynamics.png").write_bytes((study / "analysis" / "rmsd" / "rmsd.png")
                                                         .read_bytes())
    (study / "simulation").mkdir()
    (study / "simulation" / "production_of_the_helical_peptide_at_310_K.dcd").write_bytes(b"\0" * 64)
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        page = _at(browser, started.url, "150", (390, 844), "#studies")
    except Exception:
        started.server.shutdown()
        raise
    # What scrolls sideways, and what runs past the window's edge outside
    # a part that scrolls sideways itself.
    sideways = """() => {
        const scrolled = [document.scrollingElement, document.querySelector('.main'),
            document.querySelector('.page:not([hidden])')].filter(Boolean)
            .filter((e) => e.scrollWidth > e.clientWidth + 1)
            .map((e) => e.className || e.tagName);
        const scrolls = (e) => { for (let a = e.parentElement; a; a = a.parentElement) {
            const x = getComputedStyle(a).overflowX; if (x === 'auto' || x === 'scroll') return true; }
            return false; };
        const past = [...document.querySelectorAll('.page:not([hidden]) *')].filter((e) => {
            const r = e.getBoundingClientRect();
            return r.width > 1 && r.height > 1 && (r.right > innerWidth + 1 || r.left < -1)
                && getComputedStyle(e).position !== 'fixed' && !scrolls(e); })
            .map((e) => e.id || e.className || e.tagName);
        return scrolled.concat(past.slice(0, 6)); }"""
    # What each page shows, so the check meets content: the analyses'
    # values, the studies and the study's files.
    content = {"analysis": ".analysis-cell-head .cell-value",
               "studies": ".studies-row, .study-card", "files": '.files-row[data-path$=".dcd"]'}
    try:
        said = {}
        met = {}
        for where in ("analysis", "studies", "files"):
            page.evaluate(f"() => window.FastMDXDashboard.navigate('{where}')")
            page.wait_for_selector(f'.page[data-page="{where}"]:not([hidden])')
            page.wait_for_selector(content[where])
            page.wait_for_timeout(400)
            said[where] = page.evaluate(f"() => [({sideways})(), window.FastMDXDisplay.zoom(), innerWidth]")
            met[where] = page.evaluate(f"""() => [...document.querySelectorAll('{content[where]}')]
                .filter((e) => e.getBoundingClientRect().width > 0).map((e) => e.textContent)""")
        for where, (scrolled, zoom, width) in said.items():
            assert scrolled == [] and zoom == 1 and width == 390, (where, scrolled, zoom, width)
        assert "Kinetic energy8,500 kJ/mol in every frame" in met["analysis"], met
        assert met["studies"] and len(met["files"]) == 1, met
        page.evaluate("() => window.FastMDXDashboard.navigate('settings')")
        page.wait_for_selector('.page[data-page="settings"]:not([hidden])')
        notes = [page.text_content("#setting-display-size-note")]
        resized = []
        for width, zoom in ((500, 1.25), (1200, 1.5), (390, 1)):
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_function(f"() => window.FastMDXDisplay.zoom() === {zoom}")
            resized.append(page.evaluate("() => innerWidth"))
            notes.append(page.text_content("#setting-display-size-note"))
        errors = page.errors
    finally:
        page.context.close()
        started.server.shutdown()
    assert errors == []
    assert resized == [400, 800, 390]
    held = "; 100% in a window this narrow, so the page is never under 390 pixels wide."
    assert notes[0] == "150% chosen" + held, notes[0]
    assert notes[1].startswith("150% chosen; 125% in a window this narrow"), notes[1]
    assert notes[2] == "Everything at once, text included.", notes[2]
    assert notes[3] == notes[0]


def test_nothing_of_the_preferences_dialog_is_left(tmp_path):
    """The dialog is the Settings page: no address, rule, script or branch
    names it, and its addresses are the page's, not a dialog's."""
    import re
    from pathlib import Path

    gui = Path(__file__).resolve().parent.parent / "src" / "fastmdxplora" / "gui"
    named = [path.name for folder in ("static", "templates") for path in sorted((gui / folder).glob("*"))
             if path.suffix in {".js", ".css", ".html"}
             and "prefs-dialog" in path.read_text(encoding="utf-8")]
    assert named == []
    dashboard = (gui / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert re.search(r"const DIALOG_OF = \{cite: \"cite-dialog\"\};", dashboard)


def test_the_docs_say_the_settings_page(tmp_path):
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "docs" / "gui.md").read_text(encoding="utf-8")
    said = text[text.index("**Settings**"):]
    assert "This folder" in said and "fastmdx-defaults.yml" in said
    assert "Display size" in said and "Auto" in said
