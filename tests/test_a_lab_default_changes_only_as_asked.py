"""A lab's default changes only as the person asks, and New study says what
a start would do with a refused file (IMDE 2.9's leftovers, `imde-r11-port`
reviews E13 and E14).

A folder's `fastmdx-defaults.yml` reaches every new study made there, so a
change nobody meant is a wrong study nobody sees:
- a wheel over a focused number box in Settings' This folder stepped the
  value and saved it on leaving (E14 M2);
- Enter then Tab sent one value twice, and with a slow answer a second
  value typed meanwhile was lost, or Undo took back two (E13 L2, E14 L1);
- a read of the file answered with what the page already said drew every
  row again, wiping a change's "Saved in the file. Undo" and the focus
  (E14 L2).
And New study, with the file refused, said "Checks pass" beside Run enabled,
and the start was refused (E14 M1); a setting given as `null` was told to
be taken out of the file and that it belonged in a study's config (E14 L4).
"""

from __future__ import annotations

import json

import pytest
import yaml

from tests.test_the_workspace_says_its_studies import _study


def test_a_one_study_setting_given_as_nothing_is_told_one_remedy(tmp_path):
    from fastmdxplora.config.defaults_file import read_defaults
    from fastmdxplora.config.loader import ConfigError

    where = tmp_path / "fastmdx-defaults.yml"
    where.write_text("simulation:\n  resume_from: null\n", encoding="utf-8")
    with pytest.raises(ConfigError) as refused:
        read_defaults(where)
    said = str(refused.value)
    assert "take it out of the file" in said
    assert said.count("belong") == 1 and said.rstrip().endswith("read."), said
    # Given a value, the remedy is the study's config, said once.
    where.write_text("simulation:\n  resume_from: /elsewhere\n", encoding="utf-8")
    with pytest.raises(ConfigError) as refused:
        read_defaults(where)
    said = str(refused.value)
    assert said.endswith(": it belongs in that study's config.") and said.count("belong") == 1


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
    yield started, study.parent / "fastmdx-defaults.yml"
    started.server.shutdown()


def _open(browser, url, where="#settings"):
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    page.set_default_timeout(60000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.goto(url + where, wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard && window.FastMDXSettings")
    page.wait_for_selector("body:not(.state-loading)")
    return page


PH = '.settings-field[data-name="setup.ph"]'


def _folder(page):
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{PH} .settings-control")


def _posts(page):
    sent = []
    page.on("request", lambda r: sent.append(json.loads(r.post_data))
            if r.method == "POST" and "/api/defaults-file" in r.url else None)
    return sent


def _file(where):
    text = where.read_text(encoding="utf-8") if where.exists() else ""
    return yaml.safe_load(text) or {} if text.strip() else {}


def test_a_wheel_over_a_focused_number_box_changes_no_default(browser, session):
    started, where = session
    where.write_text("setup:\n  ph: 6.5\n", encoding="utf-8")
    page = _open(browser, started.url)
    _folder(page)
    sent = _posts(page)
    box = page.locator(f"{PH} .settings-control")
    box.click()
    focused = page.evaluate("() => document.activeElement.classList.contains('settings-control')")
    box.hover()
    for _ in range(3):
        page.mouse.wheel(0, -120)
        page.wait_for_timeout(100)
    shown = box.input_value()
    page.click("#settings-folder-lead")
    page.wait_for_timeout(600)
    errors = page.errors
    page.context.close()
    assert errors == [] and focused
    assert shown == "6.5", shown
    assert sent == [] and _file(where) == {"setup": {"ph": 6.5}}


def test_enter_then_tab_on_a_slow_answer_sends_once_and_undoes_once(browser, session):
    started, where = session
    page = _open(browser, started.url)
    _folder(page)
    sent = _posts(page)
    held = []
    page.route("**/api/defaults-file",
               lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
    page.fill(f"{PH} .settings-control", "6.5")
    page.press(f"{PH} .settings-control", "Enter")
    page.press(f"{PH} .settings-control", "Tab")
    page.wait_for_timeout(400)
    while held:
        held.pop(0).continue_()
        page.wait_for_timeout(400)
    page.wait_for_selector(f"{PH}.is-set:not([aria-busy]) .settings-undo")
    page.wait_for_timeout(400)
    while held:
        held.pop(0).continue_()
        page.wait_for_timeout(400)
    once = list(sent)
    page.unroute("**/api/defaults-file")
    page.click(f"{PH} .settings-undo")
    page.wait_for_selector(f"{PH}:not(.is-set):not([aria-busy])")
    page.wait_for_timeout(400)
    errors = page.errors
    page.context.close()
    assert errors == []
    assert once == [{"set": {"setup.ph": 6.5}}], once
    assert sent[1:] == [{"unset": ["setup.ph"]}], sent
    assert _file(where) == {}


def test_a_value_typed_while_the_last_is_in_flight_is_sent_after_it(browser, session):
    started, where = session
    page = _open(browser, started.url)
    _folder(page)
    sent = _posts(page)
    held = []
    page.route("**/api/defaults-file",
               lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
    page.fill(f"{PH} .settings-control", "6.5")
    page.press(f"{PH} .settings-control", "Enter")
    page.wait_for_timeout(300)
    page.fill(f"{PH} .settings-control", "7.0")
    page.press(f"{PH} .settings-control", "Enter")
    page.wait_for_timeout(300)
    first = list(sent)
    held.pop(0).continue_()
    for _ in range(40):
        if held:
            break
        page.wait_for_timeout(100)
    while held:
        held.pop(0).continue_()
    page.wait_for_function(f"""() => {{ const r = document.querySelector('{PH}');
        return r && !r.hasAttribute('aria-busy') && r.querySelector('.settings-control').value === '7'; }}""")
    page.wait_for_timeout(300)
    errors = page.errors
    page.context.close()
    assert errors == []
    assert first == [{"set": {"setup.ph": 6.5}}], first
    assert sent == [{"set": {"setup.ph": 6.5}}, {"set": {"setup.ph": 7}}], sent
    assert _file(where) == {"setup": {"ph": 7}}


def test_a_read_that_changes_nothing_leaves_a_change_s_undo_and_the_focus(browser, session):
    started, where = session
    page = _open(browser, started.url)
    _folder(page)
    page.fill(f"{PH} .settings-control", "6.5")
    page.press(f"{PH} .settings-control", "Enter")
    page.wait_for_selector(f"{PH}.is-set:not([aria-busy]) .settings-undo")
    page.focus(f"{PH} .settings-why")
    with page.expect_response(lambda r: r.request.method == "GET" and "/api/defaults-file" in r.url):
        page.evaluate("() => window.FastMDXSettings.reload()")
    page.wait_for_timeout(300)
    said = (page.text_content(f"{PH} .settings-status"),
            page.evaluate("() => document.activeElement.classList.contains('settings-why')"))
    errors = page.errors
    page.context.close()
    assert errors == []
    assert said[0].startswith("Saved in the file.") and said[1], said


def test_new_study_s_run_is_quiet_while_the_defaults_are_refused(browser, session, tmp_path):
    started, where = session
    where.write_text("setup:\n  mutations: [L99A]\n", encoding="utf-8")
    pdb = tmp_path / "peptide.pdb"
    pdb.write_text("ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N\n"
                   "ATOM      2  CA  GLY A   1       1.450   0.000   0.000  1.00  0.00           C\n"
                   "ATOM      3  C   GLY A   1       2.900   0.000   0.000  1.00  0.00           C\nEND\n",
                   encoding="utf-8")
    page = _open(browser, started.url, "#run")
    page.wait_for_selector("#run-defaults-refused:not([hidden])")
    page.click("#run-start [data-start='structure']")
    page.fill("#run-system", str(pdb))
    page.wait_for_function("() => /Will not start here/.test(document.getElementById('run-status').textContent)")
    refused = page.evaluate("""() => [document.getElementById('run-start-button').disabled,
        document.getElementById('run-status').textContent,
        document.getElementById('run-download').disabled,
        document.getElementById('run-defaults-refused').textContent]""")
    # Put right and read again, Run is offered.
    where.write_text("setup:\n  ph: 6.5\n", encoding="utf-8")
    page.evaluate("() => window.FastMDXDashboard.navigate('overview')")
    page.evaluate("() => window.FastMDXDashboard.navigate('run')")
    page.wait_for_selector("#run-defaults-refused .builder-defaults-again")
    page.click("#run-defaults-refused .builder-defaults-again")
    page.wait_for_function("() => !document.getElementById('run-start-button').disabled")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert refused[0] is True, refused
    assert "refused until it is put right" in refused[1], refused
    # The Config can still be taken away and run elsewhere.
    assert refused[2] is False, refused
    assert "a study started here is refused until the file is put right" in refused[3], refused
