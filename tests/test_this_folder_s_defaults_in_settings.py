"""Settings' This folder changes the workspace's defaults file, and the
Agent's mode and ceiling are saved (IMDE 2.9, the parts in `server.py` and
`agent-panel.js`).

The page is `test_one_settings_page.py`'s; here are the server's route to
the file (`/api/defaults-file`, read and written by `settings_page.py`) and
the Agent's choices saved over a reload, which until now went back to Seeker
with no ceiling.
"""

from __future__ import annotations

import json

import pytest
import yaml

from tests.test_the_workspace_says_its_studies import _study


def _ask(study, hosting, method="GET", body=None, secret="s" * 40):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.hosting import SECRET_HEADER
    from fastmdxplora.gui.server import make_handler

    runtime = DashboardRuntime(workspace_root=study.parent, exploration_root=study.parent)
    runtime.hosting = hosting
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        study.parent, runtime=runtime, allow_control=True, hosting=hosting))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    headers = {SECRET_HEADER: secret, "Host": "app.example.org"} if hosting else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"
        if hosting:
            headers["Origin"] = "https://app.example.org"
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/defaults-file",
            data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as refused:
            return refused.code, json.loads(refused.read() or b"{}")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_route_reads_and_writes_the_workspace_s_file(tmp_path):
    study = _study(tmp_path / "person" / "study")
    where = study.parent / "fastmdx-defaults.yml"
    status, said = _ask(study, None)
    assert status == 200 and said["path"] == str(where) and not said["exists"]
    status, said = _ask(study, None, "POST", {"set": {"simulation.temperature_K": 310},
                                              "why": {"simulation.temperature_K": "warm"}})
    assert status == 200 and said["ok"] and said["values"] == {"simulation.temperature_K": 310}
    assert yaml.safe_load(where.read_text(encoding="utf-8"))["decisions"] == {
        "simulation.temperature_K": {"why": "warm"}}
    status, said = _ask(study, None, "POST", {"set": {"simulation.temperature_K": "warm"}})
    assert status == 200 and said["ok"] is False and "temperature_K" in said["reason"]
    assert said["values"] == {"simulation.temperature_K": 310}


def test_a_service_s_guests_neither_read_nor_write_it(tmp_path):
    from fastmdxplora.gui.hosting import Hosting

    study = _study(tmp_path / "person" / "study")
    where = study.parent / "fastmdx-defaults.yml"
    where.write_text("setup:\n  ph: 7.0\n", encoding="utf-8")
    hosting = Hosting(workspace=study.parent.resolve(),
                      allowed_hosts=frozenset({"app.example.org"}), secret="s" * 40)
    read = _ask(study, hosting)
    written = _ask(study, hosting, "POST", {"set": {"setup.ph": 6.0}})
    assert read[0] == 403 and read[1]["ok"] is False and "values" not in read[1]
    # A read is refused as one: neither shown nor changed.
    assert read[1]["reason"] == "Not shown or changed here when the GUI is served."
    assert written[0] == 403 and written[1]["ok"] is False
    assert where.read_text(encoding="utf-8") == "setup:\n  ph: 7.0\n"


def _schema(study, *, allow_control):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.server import make_handler

    runtime = DashboardRuntime(workspace_root=study.parent, exploration_root=study.parent)
    # Bound to loopback either way: the flag is what the gate reads.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        study.parent, runtime=runtime, allow_control=allow_control))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_address[1]}/api/schema", timeout=30) as answer:
            return json.loads(answer.read())
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_beyond_loopback_the_schema_neither_shows_nor_says_the_folder_s_file(tmp_path):
    """Beyond loopback `/api/defaults-file` refuses the folder's file, yet
    `/api/schema` gave every value it sets and, for a refused file, the
    refusal with the file's path: New study said it to anyone on the
    network. There the form offers FastMDXplora's own values, with nothing
    said of the file."""
    study = _study(tmp_path / "person" / "study")
    where = study.parent / "fastmdx-defaults.yml"
    where.write_text("setup:\n  ph: 6.5\n", encoding="utf-8")

    def ph(schema):
        return next(f for f in schema["phases"]["setup"]["fields"] if f["name"] == "ph")

    here = _schema(study, allow_control=True)
    public = _schema(study, allow_control=False)
    assert ph(here)["default"] == 6.5 and ph(here)["default_from"] == "fastmdx-defaults.yml"
    assert ph(public)["default"] == 7.4 and "default_from" not in ph(public)
    assert "fastmdx-defaults.yml" not in json.dumps(public)
    where.write_text("setup:\n  mutations: [L99A]\n  ph: 6.5\n", encoding="utf-8")
    here = _schema(study, allow_control=True)
    public = _schema(study, allow_control=False)
    assert str(where) in here["defaults_refused"]
    assert "defaults_refused" not in public and str(where) not in json.dumps(public)
    assert ph(public)["default"] == 7.4


def _config(base, body):
    import urllib.request

    request = urllib.request.Request(base + "/api/config", data=json.dumps(body).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as answer:
        return json.loads(answer.read())


def test_beyond_loopback_the_config_handed_back_neither_shows_nor_says_the_folder_s_file(tmp_path):
    """`/api/config` is the one POST answered beyond loopback, and it drew
    the config from the folder's file the schema there leaves out: New
    study's fields said "Default 7.4" while its preview and command said
    `ph: 6.5`, named the file and quoted its reason."""
    import threading
    from http.server import ThreadingHTTPServer

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.server import make_handler, start_dashboard_session

    study = _study(tmp_path / "person" / "study")
    (study.parent / "fastmdx-defaults.yml").write_text(
        "setup:\n  ph: 6.5\nreport:\n  author: Lab Person\n"
        "decisions:\n  setup.ph:\n    why: our assay buffer\n", encoding="utf-8")
    body = {"pdb_id": "1UBQ", "full": True}

    def answered(allow_control):
        runtime = DashboardRuntime(workspace_root=study.parent, exploration_root=study.parent)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            study.parent, runtime=runtime, allow_control=allow_control))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            return _config(f"http://127.0.0.1:{httpd.server_address[1]}", body)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def ph(answer):
        return yaml.safe_load(answer["yaml"])["setup"]["ph"]

    here = answered(True)
    public = answered(False)
    # Bound to an address beyond loopback, as `fastmdx gui --host` does.
    started = start_dashboard_session(output=str(study), host="192.0.2.2", port=0)
    try:
        bound = _config(started.url, body)
    finally:
        started.stop()
    assert here["ok"] and ph(here) == 6.5 and "our assay buffer" in json.dumps(here)
    for answer in (public, bound):
        said = json.dumps(answer)
        assert answer["ok"] and ph(answer) == 7.4, answer["yaml"]
        assert "fastmdx-defaults" not in said and "our assay buffer" not in said
        assert "Lab Person" not in said


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


def _open(browser, url, where="#settings", width=1440, height=900):
    page = browser.new_context(viewport={"width": width, "height": height}).new_page()
    page.set_default_timeout(60000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.goto(url + where, wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard && window.FastMDXSettings")
    page.wait_for_selector("body:not(.state-loading)")
    return page


ROW = '.settings-field[data-name="simulation.temperature_K"]'


def test_a_setting_of_this_folder_is_set_given_its_reason_undone_and_taken_out(browser, session):
    started, where = session
    page = _open(browser, started.url)
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{ROW} .settings-control")
    lead = page.text_content("#settings-folder-lead")
    theirs = page.text_content(f"{ROW} .settings-theirs")
    # Set, as it is changed: no Save.
    page.fill(f"{ROW} .settings-control", "310")
    page.press(f"{ROW} .settings-control", "Enter")
    page.wait_for_selector(f"{ROW}.is-set")
    set_said = page.text_content(f"{ROW} .settings-status")
    page.fill(f"{ROW} .settings-why", "the body's temperature")
    page.press(f"{ROW} .settings-why", "Tab")
    page.wait_for_function(f"() => /Reason saved/.test(document.querySelector('{ROW} .settings-status')"
                           ".textContent)")
    written = yaml.safe_load(where.read_text(encoding="utf-8"))
    # The New study form offers it as its default.
    offered = page.evaluate("""async () => {
        const s = await (await fetch('/api/schema')).json();
        return s.phases.simulation.fields.find((f) => f.name === 'temperature_K').default; }""")
    # Refused: said beside it, the file as it was.
    page.fill(f"{ROW} .settings-control", "-4")
    page.press(f"{ROW} .settings-control", "Enter")
    page.wait_for_selector(f"{ROW} .settings-status.is-refused")
    refused = (page.text_content(f"{ROW} .settings-status"),
               page.get_attribute(f"{ROW} .settings-control", "aria-invalid"))
    after_refusal = yaml.safe_load(where.read_text(encoding="utf-8"))
    # Found by its words, This folder's count beside its name.
    page.fill("#settings-find", "production temperature")
    count = page.text_content("#settings-scope-folder .settings-count")
    page.fill("#settings-find", "")
    # FastMDXplora's again, and Undo puts it back with its reason.
    page.click(f"{ROW} .settings-use-theirs")
    page.wait_for_selector(f"{ROW}:not(.is-set)")
    taken_out = yaml.safe_load(where.read_text(encoding="utf-8")) if where.read_text(
        encoding="utf-8").strip() else {}
    page.click(f"{ROW} .settings-undo")
    page.wait_for_selector(f"{ROW}.is-set")
    put_back = yaml.safe_load(where.read_text(encoding="utf-8"))
    why_back = page.input_value(f"{ROW} .settings-why")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert str(where) in lead and "Not there yet" in lead
    assert theirs.startswith("FastMDXplora's: 300")
    assert set_said.startswith("Saved in the file.")
    assert written == {"simulation": {"temperature_K": 310},
                       "decisions": {"simulation.temperature_K": {"why": "the body's temperature"}}}
    assert offered == 310
    assert refused[1] == "true" and "temperature_K" in refused[0] and str(where) not in refused[0]
    assert after_refusal == written
    assert count == "1"
    assert taken_out == {}
    assert put_back == written and why_back == "the body's temperature"


def test_a_change_saved_while_the_file_is_read_is_shown_set(browser, session):
    """Settings re-reads the file as it is shown. A change typed at once,
    while a slow server had not answered that read, was saved in the file
    and shown empty and unset: the read's answer, the file as it was before
    the change, drew every row again, and the change's answer then replaced
    a row no longer in the page (E8's quick.py, five times in six)."""
    started, where = session
    ph = '.settings-field[data-name="setup.ph"]'
    page = _open(browser, started.url)
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{ph} .settings-control")
    reads, writes = [], []

    def held(route):
        if route.request.method == "GET":
            # Answered now, with the file as it is, and given to the page later.
            reads.append((route, route.fetch()))
        elif hold_writes:
            writes.append(route)
        else:
            route.continue_()

    def answered(routes):
        while not routes:
            page.wait_for_timeout(50)

    def read_given(route, response):
        with page.expect_response(lambda r: r.request.method == "GET"
                                  and "/api/defaults-file" in r.url):
            route.fulfill(response=response)
        page.wait_for_timeout(300)

    def row(name):
        selector = f'.settings-field[data-name="{name}"]'
        return (page.get_attribute(selector, "class"),
                page.input_value(f"{selector} .settings-control"),
                page.text_content(f"{selector} .settings-status"))

    hold_writes = False
    page.route("**/api/defaults-file", held)
    # A read asked, then two changes sent and answered before it is.
    page.evaluate("() => window.FastMDXSettings.reload()")
    answered(reads)
    page.fill(f"{ph} .settings-control", "6.5")
    page.press(f"{ph} .settings-control", "Enter")
    page.fill(f"{ROW} .settings-control", "310")
    page.press(f"{ROW} .settings-control", "Enter")
    page.wait_for_selector(f"{ph}.is-set")
    page.wait_for_selector(f"{ROW}.is-set")
    read_given(*reads.pop())
    both = (row("setup.ph"), row("simulation.temperature_K"))
    # A change sent, then a read asked and answered before the change is:
    # the change's answer finds its row again.
    hold_writes = True
    page.fill(f"{ph} .settings-control", "6.0")
    page.press(f"{ph} .settings-control", "Enter")
    answered(writes)
    page.evaluate("() => window.FastMDXSettings.reload()")
    answered(reads)
    read_given(*reads.pop())
    with page.expect_response(lambda r: r.request.method == "POST"
                              and "/api/defaults-file" in r.url):
        writes.pop().continue_()
    page.wait_for_selector(f"{ph} .settings-status.is-saved")
    found_again = row("setup.ph")
    page.unroute("**/api/defaults-file")
    errors = page.errors
    page.context.close()
    assert errors == []
    for (classes, value, status), wanted in zip(both, ("6.5", "310")):
        assert "is-set" in classes.split() and value == wanted, (classes, value, status)
        assert status.startswith("Saved in the file."), status
    assert "is-set" in found_again[0].split() and found_again[1] == "6", found_again
    assert found_again[2].startswith("Saved in the file."), found_again
    assert yaml.safe_load(where.read_text(encoding="utf-8")) == {
        "setup": {"ph": 6.0}, "simulation": {"temperature_K": 310}}


def test_a_read_asked_while_a_change_is_in_flight_does_not_undo_it(browser, session):
    """A read asked after a change was sent, and before it was answered,
    can read the file before the change is written and be answered after
    it: the threaded server answered the read with the file as it was, and
    the row was drawn unset again."""
    started, where = session
    ph = '.settings-field[data-name="setup.ph"]'
    page = _open(browser, started.url)
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{ph} .settings-control")
    reads, writes = [], []

    def held(route):
        if route.request.method == "GET":
            # Read now, the change not yet written, and given to the page later.
            reads.append((route, route.fetch()))
        else:
            writes.append(route)

    def answered(routes):
        while not routes:
            page.wait_for_timeout(50)

    page.route("**/api/defaults-file", held)
    page.fill(f"{ph} .settings-control", "6.5")
    page.press(f"{ph} .settings-control", "Tab")
    answered(writes)
    page.evaluate("() => window.FastMDXSettings.reload()")
    answered(reads)
    with page.expect_response(lambda r: r.request.method == "POST"
                              and "/api/defaults-file" in r.url):
        writes.pop().continue_()
    page.wait_for_selector(f"{ph} .settings-status.is-saved")
    route, response = reads.pop()
    with page.expect_response(lambda r: r.request.method == "GET"
                              and "/api/defaults-file" in r.url):
        route.fulfill(response=response)
    page.wait_for_timeout(300)
    shown = (page.get_attribute(ph, "class"), page.input_value(f"{ph} .settings-control"),
             page.text_content(f"{ph} .settings-status"))
    page.unroute("**/api/defaults-file")
    errors = page.errors
    page.context.close()
    assert errors == [] and not writes
    assert "is-set" in shown[0].split() and shown[1] == "6.5", shown
    assert shown[2].startswith("Saved in the file."), shown
    assert yaml.safe_load(where.read_text(encoding="utf-8")) == {"setup": {"ph": 6.5}}


def test_a_value_set_with_enter_is_sent_once_and_undone(browser, session):
    """Enter sent the value, and the row drawn again from the answer took
    the box away, which fired its change and sent the value again: Undo
    then put back the value just set, so it undid nothing."""
    started, where = session
    ph = '.settings-field[data-name="setup.ph"]'
    page = _open(browser, started.url)
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{ph} .settings-control")
    posts = []
    page.on("request", lambda r: posts.append(json.loads(r.post_data))
            if r.method == "POST" and "/api/defaults-file" in r.url else None)
    page.fill(f"{ph} .settings-control", "6.5")
    page.press(f"{ph} .settings-control", "Enter")
    page.wait_for_selector(f"{ph}.is-set:not([aria-busy]) .settings-undo")
    page.wait_for_timeout(500)
    sent = list(posts)
    if sent != [{"set": {"setup.ph": 6.5}}]:
        page.context.close()
    assert sent == [{"set": {"setup.ph": 6.5}}], sent
    page.click(f"{ph} .settings-undo")
    page.wait_for_selector(f"{ph}:not(.is-set):not([aria-busy])")
    page.wait_for_timeout(500)
    text = where.read_text(encoding="utf-8")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert posts[1:] == [{"unset": ["setup.ph"]}], posts
    assert not text.strip() or yaml.safe_load(text) in (None, {}), text


def test_a_file_with_comments_is_shown_and_not_written(browser, session):
    started, where = session
    where.write_text("simulation:\n  temperature_K: 305  # the lab's\n", encoding="utf-8")
    page = _open(browser, started.url)
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{ROW} .settings-fixed")
    said = (page.text_content("#settings-folder-body .settings-cannot"),
            page.text_content(f"{ROW} .settings-fixed"),
            page.locator(f"{ROW} input, {ROW} select").count())
    page.context.close()
    assert "comments" in said[0] and said[1] == "305" and said[2] == 0


@pytest.mark.parametrize("held, words", [
    ("mutations: [L99A]", "sets setup.mutations, which says what one study simulates"),
    ("ligand_net_charge: -1", "sets setup.ligand_net_charge, which says one ligand's formal charge"),
])
def test_a_file_setting_what_is_simulated_is_said_once_and_not_changed(browser, session, held, words):
    """`L99A` in the folder's file would mutate every new study's protein,
    and `ligand_net_charge: -1` charge every new study's ligand: refused,
    said with the path once (in the lead) and the setting's name as code,
    and nothing offered to change."""
    started, where = session
    text = f"setup:\n  {held}\n"
    where.write_text(text, encoding="utf-8")
    page = _open(browser, started.url)
    page.click("#settings-scope-folder")
    page.wait_for_selector("#settings-folder-body .settings-cannot")
    said = page.evaluate("""() => {
        const note = document.querySelector('#settings-folder-body .settings-cannot');
        return [note.textContent, [...note.querySelectorAll('code')].map((c) => c.textContent),
                document.querySelectorAll('#settings-folder-body .settings-control').length,
                document.getElementById('settings-folder-lead').textContent]; }""")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert words in said[0], said[0]
    assert "`" not in said[0] and str(where) not in said[0]
    assert said[1] == ["setup." + held.split(":")[0]]
    assert said[2] == 0 and str(where) in said[3]
    assert where.read_text(encoding="utf-8") == text


def test_new_study_says_a_refused_defaults_file(browser, session):
    """A lab's file refused for one setting filled nothing, and New study
    showed FastMDXplora's values with nothing said: the study was refused
    only once it started. The refusal is said above the form, with the
    way to This folder, and is gone once the file is put right and the
    form drawn from it."""
    started, where = session
    where.write_text("setup:\n  mutations: [L99A]\n  ph: 6.5\n", encoding="utf-8")
    note = "#run-defaults-refused"
    page = _open(browser, started.url, "#run")
    page.wait_for_selector(f"{note}:not([hidden])")
    said = page.evaluate(f"""() => {{ const n = document.querySelector('{note}');
        return [n.textContent, [...n.querySelectorAll('code')].map((c) => c.textContent)]; }}""")
    page.click(f"{note} a")
    page.wait_for_selector('.page[data-page="settings"]:not([hidden])')
    page.wait_for_selector("#settings-folder-body .settings-cannot")
    scope = page.get_attribute("#settings-scope-folder", "aria-selected")
    # Put right, New study says nothing more once the form is drawn from it
    # (until then, that it was drawn with the file as it was).
    where.write_text("setup:\n  ph: 6.5\n", encoding="utf-8")
    page.evaluate("() => window.FastMDXDashboard.navigate('run')")
    page.click(f"{note} .builder-defaults-again")
    page.wait_for_selector(f"{note}[hidden]", state="attached")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert said[0].startswith("Your defaults are not used. "), said[0]
    assert "sets setup.mutations, which says what one study simulates" in said[0], said[0]
    assert "FastMDXplora's own values until the file is put right" in said[0]
    assert said[0].count(str(where)) == 1 and "`" not in said[0]
    assert "setup.mutations" in said[1]
    assert scope == "true"


def test_new_study_follows_the_defaults_file_it_was_drawn_with(browser, session):
    """New study's form keeps the defaults it was drawn with: changed in
    This folder, put right or refused since, its fields said "Your default
    6.5" beside a note that said otherwise, and the run took the file as it
    then was. A read that differs is said above the form, and Read the
    defaults again draws the form from the file as it is."""
    started, where = session
    where.write_text("setup:\n  ph: 6.5\n", encoding="utf-8")
    note = "#run-defaults-refused"
    ph = '.builder-field[data-phase="setup"][data-setting="ph"] .builder-default'
    page = _open(browser, started.url, "#run")
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.wait_for_selector(ph, state="attached")
    drawn = page.text_content(ph)

    def changed_while_away(text):
        where.write_text(text, encoding="utf-8")
        page.evaluate("() => window.FastMDXDashboard.navigate('settings')")
        page.wait_for_selector('.page[data-page="settings"]:not([hidden])')
        page.evaluate("() => window.FastMDXDashboard.navigate('run')")
        page.wait_for_selector(f"{note}:not([hidden]) .builder-defaults-again")
        return page.text_content(note), page.text_content(ph)

    def read_again():
        page.click(f"{note} .builder-defaults-again")
        page.wait_for_selector(f"{note} .builder-defaults-again", state="detached")
        return page.text_content(note), page.is_hidden(note), page.text_content(ph)

    # Changed in This folder: said, the fields as drawn, then drawn again.
    changed = changed_while_away("setup:\n  ph: 6.8\n")
    redrawn = read_again()
    focused = page.evaluate("() => document.activeElement.className")
    # Refused while the page is open: the refusal is said first, as on
    # opening, never that a study runs with the file (the start refuses
    # it); the form drawn with 6.8 is not said to offer FastMDXplora's
    # values until it is drawn again.
    refused = changed_while_away("setup:\n  mutations: [L99A]\n  ph: 6.8\n")
    refused_again = read_again()
    # Put right: the form takes the file's value again.
    right = changed_while_away("setup:\n  ph: 6.5\n")
    right_again = read_again()
    # Read again with nothing changed: nothing said.
    page.evaluate("() => window.FastMDXDashboard.navigate('settings')")
    page.evaluate("() => window.FastMDXDashboard.navigate('run')")
    page.wait_for_selector(f"{note}[hidden]", state="attached")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert drawn == "Your default 6.5"
    assert changed[0].startswith("Your defaults changed since this form was drawn."), changed[0]
    assert "FastMDXplora's own values" not in changed[0] and changed[1] == "Your default 6.5"
    assert redrawn[1] and redrawn[2] == "Your default 6.8", redrawn
    assert "page-title" in focused
    assert refused[0].startswith("Your defaults are not used. "), refused[0]
    assert "sets setup.mutations, which says what one study simulates" in refused[0], refused[0]
    assert "drawn with the file's values" in refused[0], refused[0]
    assert "runs with the file" not in refused[0] and "until the file is put right" not in refused[0]
    assert refused[1] == "Your default 6.8"
    assert refused_again[0].startswith("Your defaults are not used. "), refused_again
    assert "setup.mutations" in refused_again[0] and refused_again[2] == "Default 7.4"
    assert right[1] == "Default 7.4" and "FastMDXplora's own values" not in right[0]
    assert right_again[1] and right_again[2] == "Your default 6.5"


def test_at_390_the_note_is_seen_on_coming_back_from_this_folder(browser, session):
    """On a phone the page scrolls as a whole, and New study, come back to
    after a change far down This folder, opened where Settings was left:
    the note that the defaults changed was thousands of pixels above, and
    the fields said the old defaults with Run enabled."""
    started, where = session
    where.write_text("setup:\n  ph: 6.5\n", encoding="utf-8")
    note = "#run-defaults-refused"
    page = _open(browser, started.url, "#run", width=390, height=844)
    page.wait_for_function("() => window.FastMDXRun && window.FastMDXRun.ready")
    page.wait_for_selector('.builder-field[data-phase="setup"][data-setting="ph"] .builder-default',
                           state="attached")
    page.evaluate("() => window.FastMDXDashboard.navigate('settings')")
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{ROW} .settings-control")
    where.write_text("setup:\n  ph: 6.8\n", encoding="utf-8")
    left = page.evaluate("""() => { for (const s of [document.scrollingElement, document.body]) {
        s.scrollTop = s.scrollHeight; } return document.body.scrollTop
        || document.scrollingElement.scrollTop; }""")
    page.evaluate("() => window.FastMDXDashboard.navigate('run')")
    page.wait_for_selector(f"{note}:not([hidden]) .builder-defaults-again")
    seen = page.evaluate(f"""() => {{ const n = document.querySelector('{note}');
        const r = n.getBoundingClientRect();
        const at = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height, 20) / 2);
        return [Math.round(r.top), Math.round(r.bottom), innerHeight, Boolean(at && n.contains(at))]; }}""")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert left > 1000, left
    assert seen[0] >= 0 and seen[1] <= seen[2] and seen[3], seen


def test_the_settings_main_changed_are_said_as_they_now_are(browser, session):
    """The restraints take a selection or a list of blocks, and the
    dispersion correction is on, off or as the force field is developed
    (the Physics rounds): blocks the box cannot say are shown as the file
    holds them, changed by hand, and unset is said as New study says it."""
    started, where = session
    where.write_text("simulation:\n  restrain:\n  - kind: position\n"
                     "    selection: protein and name CA\n    force_constant: 500\n",
                     encoding="utf-8")
    restrain = '.settings-field[data-name="simulation.restrain"]'
    dispersion = '.settings-field[data-name="setup.dispersion_correction"]'
    page = _open(browser, started.url)
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{restrain} .settings-fixed")
    held = (page.text_content(f"{restrain} .settings-fixed"),
            page.locator(f"{restrain} .settings-control").count(),
            page.text_content(f"{restrain} .settings-by-hand"))
    theirs = page.text_content(f"{dispersion} .settings-theirs")
    # Written as a selection, the restraints are a box again.
    where.write_text("simulation:\n  restrain: protein and not element H\n", encoding="utf-8")
    page.reload(wait_until="domcontentloaded")
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{restrain} input.settings-control")
    words = page.input_value(f"{restrain} .settings-control")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert yaml.safe_load(held[0]) == [{"kind": "position", "selection": "protein and name CA",
                                        "force_constant": 500}]
    assert held[1] == 0 and held[2] == "Changed in the file by hand"
    assert theirs.startswith("FastMDXplora's: as the force field was developed")
    assert words == "protein and not element H"


def test_what_the_file_holds_is_said_and_a_list_of_choices_stays_open(browser, session):
    """A `null` in the file is FastMDXplora's, chosen, not a blank box; a
    stopping rule is shown as the file holds it and counted; a list of
    choices stays open while it is ticked, one tick after another."""
    started, where = session
    where.write_text("simulation:\n  temperature_K: 310\n  stop_when:\n    measures:\n"
                     "    - analysis: rmsd\n      standard_error: 0.01\n    max_duration_ns: 50\n"
                     "setup:\n  dispersion_correction: null\n", encoding="utf-8")
    dispersion = '.settings-field[data-name="setup.dispersion_correction"]'
    stop = '.settings-field[data-name="simulation.stop_when"]'
    include = '.settings-field[data-name="analysis.include"]'
    page = _open(browser, started.url)
    page.click("#settings-scope-folder")
    page.wait_for_selector(f"{stop} .settings-fixed")
    null = page.evaluate(f"""() => {{ const s = document.querySelector('{dispersion} select');
        return [s.value, s.selectedOptions[0] && s.selectedOptions[0].textContent]; }}""")
    held = page.text_content(f"{stop} .settings-fixed")
    # As the file holds it, under the words and read from the left; one
    # the file does not set says it is set only by hand there.
    options = '.settings-field[data-name="analysis.options"]'
    shown = page.evaluate(f"""() => {{
        const pre = document.querySelector('{stop} pre.settings-several');
        const words = document.querySelector('{stop} .settings-words').getBoundingClientRect();
        const unset = document.querySelector('{options}');
        return [pre.textContent, getComputedStyle(pre).textAlign,
                pre.getBoundingClientRect().top >= words.bottom - 0.5,
                document.querySelector('{stop} .settings-by-hand').textContent,
                unset.querySelector('.settings-by-hand').textContent,
                unset.querySelectorAll('.settings-fixed').length]; }}""")
    page.check("#settings-only-set")
    only = (page.text_content("#settings-only-set-count"),
            page.evaluate("""() => [...document.querySelectorAll('#settings-folder-body .settings-field')]
                .filter((r) => !r.hidden).map((r) => r.dataset.name)"""))
    page.uncheck("#settings-only-set")
    page.click(f"{include} summary")
    page.check(f'{include} input[value="rmsd"]')
    page.wait_for_function(f"() => /Saved/.test(document.querySelector('{include} .settings-status')"
                           ".textContent)")
    still_open = page.evaluate(f"() => document.querySelector('{include} details').open")
    page.check(f'{include} input[value="rmsf"]')
    page.wait_for_function(f"""() => [...document.querySelectorAll('{include} input:checked')]
        .length === 2 && !document.querySelector('{include}').hasAttribute('aria-busy')""")
    written = yaml.safe_load(where.read_text(encoding="utf-8"))
    errors = page.errors
    page.context.close()
    assert errors == []
    assert null == ["", "FastMDXplora's"]
    assert '"max_duration_ns": 50' in held
    assert shown[0] == json.dumps({"measures": [{"analysis": "rmsd", "standard_error": 0.01}],
                                   "max_duration_ns": 50}, indent=2), shown[0]
    assert shown[1:] == ["left", True, "Changed in the file by hand", "Set only by hand in the file", 0]
    assert only == (" (3)", ["setup.dispersion_correction", "simulation.stop_when",
                             "simulation.temperature_K"])
    assert still_open
    assert written["analysis"]["include"] == ["rmsd", "rmsf"]


def test_the_agent_s_mode_and_ceiling_are_saved(browser, session):
    started, _ = session
    page = _open(browser, started.url, "#agent")
    page.evaluate("() => window.FastMDXAgent.openSettings()")
    page.select_option("#agent-mode", "autonomous")
    page.fill("#agent-budget", "12")
    page.dispatch_event("#agent-budget", "change")
    page.keyboard.press("Escape")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("body:not(.state-loading)")
    page.wait_for_function("() => document.getElementById('sb-agent')"
                           " && !document.getElementById('sb-agent').hidden")
    said = page.evaluate("""() => [document.getElementById('agent-mode').value,
        document.getElementById('agent-budget').value,
        document.getElementById('settings-mode').textContent,
        document.getElementById('settings-page-mode').textContent,
        document.getElementById('sb-agent').textContent]""")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert said[:2] == ["autonomous", "12"]
    assert said[2] == said[3] == "Ranger: built-in, autonomous"
    assert "Ranger" in said[4]


def test_k_finds_a_setting_and_opens_settings_at_it(browser, session):
    """⌘K found each setting of Preferences in its dialog; it finds those of
    Settings' You, and opens the page at the one chosen."""
    started, _ = session
    page = _open(browser, started.url, "#overview")
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette:not([hidden])")
    page.fill("#palette-input", "display size")
    page.wait_for_timeout(200)
    lines = page.evaluate("""() => [...document.querySelectorAll('#palette-list li')]
        .map((li) => li.textContent.replace(/\\s+/g, ' ').trim())""")
    page.keyboard.press("Enter")
    page.wait_for_selector('.page[data-page="settings"]:not([hidden])')
    page.wait_for_function("() => document.activeElement.id === 'setting-display-size'")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert lines[0] == "Setting" and lines[1].startswith("Display size")


def test_k_finds_settings_by_its_old_name_and_lands_on_something(browser, session):
    """"preferences" found nothing; Engine and Mode, which have no control
    of their own, opened the page with nothing focused, as Settings did."""
    started, _ = session
    page = _open(browser, started.url, "#overview")
    landed = {}
    for typed in ("preferences", "mode", "engine"):
        page.evaluate("() => window.FastMDXDashboard.navigate('overview')")
        page.keyboard.press("Control+k")
        page.wait_for_selector("#palette:not([hidden])")
        page.fill("#palette-input", typed)
        page.wait_for_timeout(200)
        # The first line after its kind's.
        first = page.evaluate("""() => [...document.querySelectorAll('#palette-list li')]
            .map((li) => li.textContent.replace(/\\s+/g, ' ').trim())[1] || ''""")
        page.keyboard.press("Enter")
        page.wait_for_selector('.page[data-page="settings"]:not([hidden])')
        page.wait_for_function("() => document.activeElement && document.activeElement !== document.body")
        page.wait_for_timeout(150)
        landed[typed] = (first, page.evaluate("() => document.activeElement.id || "
                                               "document.activeElement.textContent.trim()"))
    errors = page.errors
    page.context.close()
    assert errors == []
    assert landed["preferences"][0].startswith("Settings"), landed
    assert landed["preferences"][1] == "Settings", landed
    assert landed["mode"][0].startswith("Mode") and landed["mode"][1] == "settings-page-agent", landed
    assert landed["engine"][0].startswith("Engine") and landed["engine"][1] == "settings-page-agent"
