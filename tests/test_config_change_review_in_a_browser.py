"""Choose Config changes through the real workspace and keep the result."""
from copy import deepcopy
import json
import time
import urllib.request

import pytest
import yaml

from tests._agent_page import ready

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

BASE = {"systems": [{"id": "ubq", "system": "1UBQ"}], "include_phase": ["simulation"],
        "simulation": {"temperature_K": 300, "duration_ns": 2}}
LIVE = '#agent-thread .agent-study:not(.is-replaced):not([hidden])'
FITS = """card => {
    const box = card.getBoundingClientRect();
    return [...card.querySelectorAll('*')].filter(el => {
        const r = el.getBoundingClientRect();
        return r.width && el.checkVisibility() && (r.left < box.left - 1 || r.right > box.right + 1);
    }).map(el => el.className + ': ' + (el.textContent || '').slice(0, 40));
}"""


def _entry(config, version):
    from fastmdxplora.gui.plan import plan_of, sourced

    return {"role": "agent", "kind": "config", "config": config,
            "yaml": yaml.safe_dump(config), "version": version,
            "plan": sourced(plan_of(config), config), "attempts": [], "cycles": 1}


def _request(session, path, body=None):
    request = urllib.request.Request(session.url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Origin": session.url, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield browser
        browser.close()


@pytest.fixture
def site(tmp_path, monkeypatch):
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))
    session = start_dashboard_session(output=str(tmp_path / "workspace"), host="127.0.0.1", port=0)
    yield session
    session.stop()


def _seed(site, before=None, after=None, changed=True):
    from fastmdxplora.gui.plan import changes_between

    before = deepcopy(before or BASE)
    after = deepcopy(after or before)
    if after == before and changed:
        after["simulation"].update(temperature_K=330, duration_ns=10)
    second = _entry(after, 2)
    second["changes"] = changes_between(before, after)
    _request(site, "/api/agent/conversation", {"entries": [
        {"role": "user", "text": "A study of ubiquitin."}, _entry(before, 1),
        {"role": "user", "text": "Change the conditions."}, second]})


def _page(browser, site, width=1400, theme="dark"):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script(f"localStorage.setItem('fmx.theme', '{theme}');")
    page = context.new_page()
    page.set_default_timeout(7000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.errors = errors
    return page


def _open(browser, site, before=None, after=None, width=1400, theme="dark", wait="simulation.temperature_K"):
    _seed(site, before, after)
    page = _page(browser, site, width, theme)
    page.goto(site.url + "#agent", wait_until="domcontentloaded")
    page.locator(f'{LIVE} [data-review-setting="{wait}"]').wait_for()
    return page


def _pressed(page):
    return page.locator(LIVE + ' [data-review-setting]').evaluate_all("""rows => Object.fromEntries(rows.map(row => [
        row.dataset.reviewSetting,
        [...row.querySelectorAll('[data-choice]')].filter(b => b.getAttribute('aria-pressed') === 'true')
            .map(b => b.dataset.choice).join('') || null]))""")


def _choose(page, name, choice):
    page.locator(f'{LIVE} [data-review-setting="{name}"] [data-choice="{choice}"]').click()


def _apply(page):
    page.locator(LIVE).get_by_role("button", name="Use reviewed version", exact=True).click()
    page.wait_for_function("() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 3")
    page.wait_for_function("""async () => {
        const data = await (await fetch('/api/agent/conversation')).json();
        return (data.entries || []).filter(e => e.kind === 'config').length === 3;
    }""")


def test_each_change_has_accept_and_reject_beside_use_this_version(browser, site):
    page = _open(browser, site)
    try:
        card = page.locator(LIVE)
        rows = card.locator("[data-review-setting]")
        assert rows.count() == 2
        assert rows.locator(".agent-review-what").all_inner_texts() == [
            "Temperature 300 K\u2192330 K", "Production 2 ns\u219210 ns"]
        assert card.locator('[data-role="change"] .agent-change-rest').inner_text() == "Everything else as version 1."
        for row in rows.all():
            assert row.get_by_role("button", name="Accept", exact=False).is_visible()
            assert row.get_by_role("button", name="Reject", exact=False).is_visible()
        use = card.get_by_role("button", name="Use this version", exact=True)
        reviewed = card.get_by_role("button", name="Use reviewed version", exact=True)
        assert use.is_visible() and use.is_enabled()
        assert reviewed.is_visible() and reviewed.is_disabled()
        assert card.locator('[data-role="run"]').is_visible()
        assert card.get_by_role("button", name="Review each change").count() == 0
        _choose(page, "simulation.temperature_K", "reject")
        assert reviewed.is_enabled() and use.is_visible() and card.locator('[data-role="run"]').is_visible()
        assert card.locator(".agent-review-note").text_content().startswith("1 change rejected.")
        _choose(page, "simulation.duration_ns", "reject")
        assert card.locator(".agent-review-note").text_content().startswith("2 changes rejected.")
        assert page.errors == []
    finally:
        page.context.close()


def test_mixed_choices_become_a_new_version_and_survive_reload(browser, site):
    page = _open(browser, site)
    try:
        assert page.locator(LIVE).get_by_role("button", name="Use reviewed version").is_disabled()
        _choose(page, "simulation.temperature_K", "reject")
        _choose(page, "simulation.duration_ns", "accept")
        _apply(page)
        entries = _request(site, "/api/agent/conversation")["entries"]
        configs = [e for e in entries if e.get("kind") == "config"]
        assert [e["config"]["simulation"] for e in configs] == [
            {"temperature_K": 300, "duration_ns": 2}, {"temperature_K": 330, "duration_ns": 10},
            {"temperature_K": 300, "duration_ns": 10}]
        assert configs[-1]["review"] == {"base": 1, "proposal": 2,
            "accepted": ["simulation.duration_ns"], "rejected": ["simulation.temperature_K"]}
        said = "Reviewed version 2. Accepted: Production 2 ns \u2192 10 ns. Rejected: Temperature 300 K \u2192 330 K."
        assert [e["text"] for e in entries if e.get("role") == "user"][-1] == said
        page.get_by_text(said, exact=True).wait_for()
        # The version made from choices is not a proposal to review again.
        assert page.locator(LIVE + " [data-choice]").count() == 0
        page.reload(wait_until="domcontentloaded")
        page.locator(LIVE + ' [data-role="version"]').filter(has_text="3").wait_for()
        assert page.locator(LIVE + " [data-choice]").count() == 0
        assert page.locator('#agent-thread .agent-study.is-replaced [data-role="run"]:visible').count() == 0
        page.locator(LIVE + ' [data-role="load"]').click()
        page.locator('#run-settings [data-setting="temperature_K"] input').wait_for()
        assert page.input_value('#run-settings [data-setting="temperature_K"] input') == "300"
        assert page.input_value('#run-settings [data-setting="duration_ns"] input') == "10"
        assert page.errors == []
    finally:
        page.context.close()


def test_run_and_next_message_use_the_reviewed_config(browser, site):
    page = _open(browser, site)
    try:
        _choose(page, "simulation.temperature_K", "accept")
        _choose(page, "simulation.duration_ns", "reject")
        _apply(page)
        runs, prompts = [], []
        def run(route):
            runs.append(route.request.post_data_json)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": False, "error": "Run captured for this check."}))
        def prompt(route):
            prompts.append(route.request.post_data_json)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": False, "answer": "The reviewed conditions.", "cites": []}))
        page.route("**/api/agent/run", run)
        page.route("**/api/agent/propose*", prompt)
        page.locator(LIVE + ' [data-role="run"]').click()
        page.get_by_text("Run captured for this check.", exact=True).first.wait_for()
        assert runs[0]["config"]["simulation"] == {"temperature_K": 330, "duration_ns": 2}
        ready(page)
        page.fill("#agent-request", "Describe these conditions.")
        page.keyboard.press("Enter")
        page.get_by_text("The reviewed conditions.", exact=True).wait_for()
        assert yaml.safe_load(prompts[0]["current_config"])["simulation"] == {"temperature_K": 330, "duration_ns": 2}
        assert page.errors == []
    finally:
        page.context.close()


def test_invalid_combination_creates_no_version_and_can_be_corrected(browser, site):
    before = deepcopy(BASE)
    before["analysis"] = {"include": ["rmsd"]}
    after = deepcopy(BASE)
    after["analysis"] = {"exclude": ["rg"]}
    after["simulation"]["temperature_K"] = 330
    page = _open(browser, site, before, after)
    try:
        page.locator(LIVE).get_by_role("button", name="Accept all", exact=True).click()
        _choose(page, "analysis.include", "reject")
        page.locator(LIVE).get_by_role("button", name="Use reviewed version").click()
        refusal = page.locator(LIVE + ' .agent-review-refusal')
        refusal.filter(has_text="mutually exclusive").wait_for()
        assert refusal.text_content().endswith("Change your choice for Analyses left out or Analyses.")
        marked = page.locator(LIVE + ' .agent-review-row.is-refused').evaluate_all(
            "rows => rows.map(r => r.dataset.reviewSetting)")
        assert marked == ["analysis.exclude", "analysis.include"]
        assert page.locator('#agent-thread .agent-study:not([hidden])').count() == 2
        assert page.locator(LIVE + ' [data-role="run"]').is_visible()
        kept = _pressed(page)
        assert kept == {"simulation.temperature_K": "accept", "analysis.exclude": "accept",
                        "analysis.include": "reject"}
        # The choices and the refusal survive a reload; nothing was written.
        page.reload(wait_until="domcontentloaded")
        page.locator(LIVE + ' .agent-review-refusal').filter(has_text="mutually exclusive").wait_for()
        assert _pressed(page) == kept
        assert page.locator(LIVE + ' .agent-review-row.is-refused').count() == 2
        assert len([e for e in _request(site, "/api/agent/conversation")["entries"] if e.get("kind") == "config"]) == 2
        _choose(page, "analysis.exclude", "reject")
        assert page.locator(LIVE + ' .agent-review-row.is-refused').count() == 0
        _apply(page)
        config = [e for e in _request(site, "/api/agent/conversation")["entries"] if e.get("kind") == "config"][-1]["config"]
        assert config["analysis"] == {"include": ["rmsd"]} and config["simulation"]["temperature_K"] == 330
        assert page.errors == []
    finally:
        page.context.close()


def test_a_rejection_not_applied_holds_run_and_the_message_until_one_is_used(browser, site):
    page = _open(browser, site)
    try:
        requests, runs = [], []
        page.route("**/api/agent/propose*", lambda route: (requests.append(route.request), route.abort()))
        page.route("**/api/agent/run", lambda route: (runs.append(route.request), route.abort()))
        _choose(page, "simulation.temperature_K", "reject")
        ready(page)
        page.fill("#agent-request", "Run it.")
        page.keyboard.press("Enter")
        page.locator(LIVE + ' .agent-review-note').filter(has_text="before sending").wait_for()
        assert page.input_value("#agent-request") == "Run it." and requests == []
        page.locator(LIVE + ' [data-role="run"]').click()
        page.locator(LIVE + ' .agent-review-note').filter(has_text="before running").wait_for()
        assert runs == []
        page.locator(LIVE).get_by_role("button", name="Use this version", exact=True).click()
        page.locator(LIVE + ' [data-role="version"]').filter(has_text="3").wait_for()
        assert page.locator(LIVE + " [data-choice]").count() == 0
        # Version 2 as proposed is now the newest: nothing holds Run.
        with page.expect_request("**/api/agent/run") as ran:
            page.locator(LIVE + ' [data-role="run"]').click()
        assert ran.value.post_data_json["config"]["simulation"] == {"temperature_K": 330, "duration_ns": 10}
        assert page.errors == []
    finally:
        page.context.close()


def _versions(site, count):
    """The conversation's config versions once it holds ``count`` of them."""
    for _ in range(100):
        entries = _request(site, "/api/agent/conversation")["entries"]
        if len([e for e in entries if e.get("kind") == "config"]) == count:
            return entries
        time.sleep(0.05)
    raise AssertionError(f"the conversation never held {count} versions")


def test_use_this_version_says_in_the_conversation_that_every_change_was_accepted(browser, site):
    """Round 2 review (D L6): Use this version in the review left no trace."""
    page = _open(browser, site)
    try:
        _choose(page, "simulation.temperature_K", "reject")
        page.locator(LIVE).get_by_role("button", name="Use this version", exact=True).click()
        entries = _versions(site, 3)
        configs = [e for e in entries if e.get("kind") == "config"]
        assert [e["text"] for e in entries if e.get("role") == "user"][-1] == "Use version 2"
        assert configs[-1]["config"] == configs[1]["config"]
        assert (configs[-1]["version"], configs[-1]["again"]) == (3, 2)
        assert configs[-1]["review"] == {"base": 1, "proposal": 2, "rejected": [],
            "accepted": ["simulation.temperature_K", "simulation.duration_ns"]}
        said = ("Version 2 as proposed, again. Accepted: Temperature 300 K \u2192 330 K; "
                "Production 2 ns \u2192 10 ns.")
        assert configs[-1]["note"] == said
        page.reload(wait_until="domcontentloaded")
        page.locator(LIVE + ' [data-role="version"]').filter(has_text="3").wait_for()
        page.get_by_text(said, exact=True).wait_for()
        assert page.locator("#agent-thread .agent-msg-user").last.text_content().startswith("Use version 2")
        assert page.locator(LIVE + " [data-choice]").count() == 0
        assert page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('fmx.reviewChoices.'))") == []
        # The same words as Use this version on an older version.
        page.locator('#agent-thread .agent-study.is-replaced [data-role="use"]').first.click()
        entries = _versions(site, 4)
        assert [e["text"] for e in entries if e.get("role") == "user"][-1] == "Use version 1"
        assert page.errors == []
    finally:
        page.context.close()


def test_a_kept_choice_is_not_restored_onto_another_proposal_with_its_number(browser, site):
    """Round 2 review (C L3): a draft was keyed by the version number only."""
    page = _open(browser, site)
    try:
        _choose(page, "simulation.temperature_K", "reject")
        conversation = _request(site, "/api/agent/conversation")["id"]
        # Asked again in the same conversation: another version 2, the same
        # Temperature change and a different Production.
        other = deepcopy(BASE)
        other["simulation"].update(temperature_K=330, duration_ns=50)
        _seed(site, BASE, other)
        assert _request(site, "/api/agent/conversation")["id"] == conversation
        page.reload(wait_until="domcontentloaded")
        page.locator(LIVE + ' [data-review-setting="simulation.duration_ns"]').filter(has_text="50 ns").wait_for()
        assert _pressed(page) == {"simulation.temperature_K": None, "simulation.duration_ns": None}
        assert page.locator(LIVE).get_by_role("button", name="Use reviewed version").is_disabled()
        assert "rejected" not in page.locator(LIVE + " .agent-review-note").text_content()
        # The proposal that was reviewed, shown again, still has its choice.
        _seed(site)
        page.reload(wait_until="domcontentloaded")
        page.locator(LIVE + ' [data-review-setting="simulation.duration_ns"]').filter(has_text="10 ns").wait_for()
        assert _pressed(page) == {"simulation.temperature_K": "reject", "simulation.duration_ns": None}
        assert page.errors == []
    finally:
        page.context.close()


def test_choices_not_applied_survive_a_reload(browser, site):
    page = _open(browser, site)
    try:
        _choose(page, "simulation.temperature_K", "reject")
        _choose(page, "simulation.duration_ns", "accept")
        page.reload(wait_until="domcontentloaded")
        page.locator(LIVE + ' [data-review-setting="simulation.temperature_K"]').wait_for()
        assert _pressed(page) == {"simulation.temperature_K": "reject", "simulation.duration_ns": "accept"}
        assert page.locator(LIVE).get_by_role("button", name="Use reviewed version").is_enabled()
        # Only the reviewed choices are kept, never a Config, and nothing on the server.
        assert len([e for e in _request(site, "/api/agent/conversation")["entries"] if e.get("kind") == "config"]) == 2
        _apply(page)
        page.reload(wait_until="domcontentloaded")
        page.locator(LIVE + ' [data-role="version"]').filter(has_text="3").wait_for()
        assert page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('fmx.reviewChoices.'))") == []
        assert page.errors == []
    finally:
        page.context.close()


def test_the_page_works_without_its_storage(browser, site):
    _seed(site)
    page = _page(browser, site)
    try:
        page.context.add_init_script("""Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }});""")
        page.goto(site.url + "#agent", wait_until="domcontentloaded")
        page.locator(LIVE + ' [data-review-setting="simulation.temperature_K"]').wait_for()
        _choose(page, "simulation.temperature_K", "reject")
        _apply(page)
        assert [e for e in _request(site, "/api/agent/conversation")["entries"]
                if e.get("kind") == "config"][-1]["config"]["simulation"]["temperature_K"] == 300
    finally:
        page.context.close()


def test_no_choice_is_offered_for_a_version_without_changes(browser, site):
    after = dict(deepcopy(BASE), agent="assisted")
    _seed(site, BASE, after, changed=False)
    page = _page(browser, site)
    try:
        with page.expect_response("**/api/agent/review-config") as answered:
            page.goto(site.url + "#agent", wait_until="domcontentloaded")
        assert answered.value.json()["changes"] == []
        page.evaluate("() => new Promise(requestAnimationFrame)")
        card = page.locator(LIVE)
        expect(card.locator('[data-role="change"]')).to_have_text("The same study as version 1.")
        assert card.locator("[data-choice]").count() == 0
        assert card.get_by_role("button", name="Review each change").count() == 0
        assert card.get_by_role("button", name="Use reviewed version").count() == 0
        assert card.locator('[data-role="review"]').is_hidden()
        assert page.errors == []
    finally:
        page.context.close()


def test_compound_values_are_said_as_a_person_reads_them(browser, site):
    after = deepcopy(BASE)
    after["sweep"] = {"simulation.random_seed": [1, 2, 3]}
    page = _open(browser, site, after=after, wait="sweep")
    try:
        text = page.locator(LIVE + ' [data-review-setting="sweep"] .agent-review-what').inner_text()
        assert text == "Varied random seed not set\u21921, 2, 3"
        assert "{" not in page.locator(LIVE).inner_text()
        assert page.errors == []
    finally:
        page.context.close()


def test_a_late_review_reply_cannot_enter_a_new_conversation(browser, site):
    page = _open(browser, site)
    try:
        page.locator(LIVE).get_by_role("button", name="Reject all", exact=True).click()
        held = []
        page.route("**/api/agent/review-config", lambda route: held.append(route))
        page.locator(LIVE).get_by_role("button", name="Use reviewed version").click()
        page.wait_for_function("() => document.querySelector('.agent-review-note').textContent.includes('Checking')")
        assert held
        page.click("#agent-new")
        page.wait_for_function("() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 0")
        from fastmdxplora.gui.config_review import review_config
        checked = review_config(held[0].request.post_data_json)
        held[0].fulfill(status=200, content_type="application/json", body=json.dumps(checked))
        # A fetch after the held one provides an event-loop barrier, not a timed sleep.
        page.evaluate("async () => { await fetch('/api/agent/conversation'); await new Promise(requestAnimationFrame); }")
        assert page.locator('#agent-thread .agent-study:not([hidden])').count() == 0
        assert "Your choices" not in page.text_content("#agent-thread")
        assert page.errors == []
    finally:
        page.context.close()


@pytest.mark.parametrize("width", [390, 980, 1600])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_review_controls_fit_and_work_by_keyboard(browser, site, width, theme):
    page = _open(browser, site, width=width, theme=theme)
    try:
        focused = page.locator(LIVE + ' [data-review-setting="simulation.temperature_K"] [data-choice="accept"]')
        focused.focus()
        page.keyboard.press("Space")
        assert focused.get_attribute("aria-pressed") == "true"
        page.keyboard.press("Tab")
        page.keyboard.press("Space")
        assert page.locator(LIVE + ' [data-review-setting="simulation.temperature_K"] [data-choice="reject"]').get_attribute("aria-pressed") == "true"
        assert page.locator(LIVE).evaluate(FITS) == []
        # What each row says sits inside the row, never on a border.
        assert page.locator(LIVE + ' .agent-review-row').evaluate_all("""rows => rows.filter(row => {
            const r = row.getBoundingClientRect(), h = row.querySelector('.agent-review-what').getBoundingClientRect();
            return h.top < r.top || h.bottom > r.bottom || h.left < r.left || h.right > r.right; })""") == []
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.errors == []
    finally:
        page.context.close()


def test_alias_names_share_one_visible_choice_and_one_result(browser, site):
    before = deepcopy(BASE)
    before["simulation"].update(prepared_from="runs/old", setup_from="runs/old")
    after = deepcopy(before)
    after["simulation"].update(prepared_from="runs/new", setup_from="runs/new", temperature_K=330)
    after["decisions"] = {"simulation.setup_from": {"why": "Use the new preparation.", "source": "person"}}
    page = _open(browser, site, before, after)
    try:
        rows = page.locator(LIVE + ' [data-review-setting="simulation.prepared_from"]')
        assert rows.count() == 1
        assert page.locator(LIVE + ' [data-review-setting="simulation.setup_from"]').count() == 0
        assert "Prepared from and Setup from are two names for one setting" in rows.text_content()
        assert "Use the new preparation." in rows.text_content()
        _choose(page, "simulation.temperature_K", "reject")
        _choose(page, "simulation.prepared_from", "accept")
        _apply(page)
        config = [e for e in _request(site, "/api/agent/conversation")["entries"] if e.get("kind") == "config"][-1]["config"]
        assert config["simulation"]["prepared_from"] == config["simulation"]["setup_from"] == "runs/new"
        assert config["simulation"]["temperature_K"] == 300
        assert config["decisions"]["simulation.setup_from"]["why"] == "Use the new preparation."
        assert page.errors == []
    finally:
        page.context.close()


def test_changed_alternatives_are_visible_before_choosing(browser, site):
    before = deepcopy(BASE)
    before["decisions"] = {"simulation.temperature_K": {"why": "Same reason.", "source": "person", "alternatives": [310]}}
    after = deepcopy(before)
    after["decisions"]["simulation.temperature_K"]["alternatives"] = [330]
    page = _open(browser, site, before, after)
    try:
        reason = page.locator(LIVE + ' [data-review-setting="simulation.temperature_K"] .agent-review-reasons').text_content()
        assert "alternatives: 310 K" in reason and "alternatives: 330 K" in reason
        _choose(page, "simulation.temperature_K", "reject")
        _apply(page)
        config = [e for e in _request(site, "/api/agent/conversation")["entries"] if e.get("kind") == "config"][-1]["config"]
        assert config["decisions"]["simulation.temperature_K"]["alternatives"] == [310]
        assert page.errors == []
    finally:
        page.context.close()


@pytest.mark.parametrize("full", [False, True])
def test_download_keeps_reviewed_settings_even_in_an_inactive_phase(browser, site, full):
    after = deepcopy(BASE)
    after["simulation"]["temperature_K"] = 330
    after["report"] = {"title": "An accepted title for later."}
    after["decisions"] = {"report.title": {"why": "The chosen title.", "source": "person"}}
    page = _open(browser, site, after=after)
    try:
        _choose(page, "simulation.temperature_K", "reject")
        _choose(page, "report.title", "accept")
        _apply(page)
        page.evaluate("window.showSaveFilePicker = undefined")
        page.locator(LIVE + ' [data-role="more"] > summary').click()
        if full:
            page.locator(LIVE + ' [data-role="full"]').check()
        with page.expect_download() as saved:
            page.locator(LIVE + ' [data-role="download"]').click()
        with open(saved.value.path(), encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
        assert config["report"]["title"] == "An accepted title for later."
        assert config["decisions"]["report.title"]["why"] == "The chosen title."
        assert config["simulation"]["temperature_K"] == 300
        assert config["include_phase"] == ["simulation"]
        assert page.errors == []
    finally:
        page.context.close()


LONG = "runs/ubiquitin_in_tip3p_water_prepared_again_with_a_larger_box_and_a_long_name_2026_10_16/setup"


@pytest.mark.parametrize("width, beside", [(1440, False), (1250, True), (900, False), (390, False)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_long_value_wraps_inside_the_proposal_card(browser, site, width, beside, theme):
    after = deepcopy(BASE)
    after["simulation"].update(prepared_from=LONG, temperature_K=330)
    _seed(site, BASE, after)
    page = _page(browser, site, width, theme)

    def show(again=False):
        if again:
            page.reload(wait_until="domcontentloaded")
        else:
            page.goto(site.url + ("#overview" if beside else "#agent"), wait_until="domcontentloaded")
        if beside:
            page.wait_for_function("() => document.getElementById('loading-screen').getAttribute('aria-hidden') === 'true'")
            page.click("#agent-beside-open")
            page.wait_for_selector("#agent-drawer:not([hidden])")
    try:
        show()
        card = page.locator(LIVE)
        card.locator('[data-review-setting="simulation.prepared_from"]').wait_for()
        assert LONG in card.locator('[data-review-setting="simulation.prepared_from"]').inner_text().replace("\n", "")
        assert card.evaluate(FITS) == []
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # The change summary, shown where the changes cannot be read one by one.
        page.route("**/api/agent/review-config", lambda route: route.abort())
        show(again=True)
        card.locator('[data-role="change"]').filter(has_text="Accept and Reject are not available here").wait_for()
        assert card.locator("[data-review-setting]").count() == 0
        assert LONG in card.locator('[data-role="change"]').inner_text().replace("\n", "")
        assert card.evaluate(FITS) == []
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.errors == []
    finally:
        page.context.close()
