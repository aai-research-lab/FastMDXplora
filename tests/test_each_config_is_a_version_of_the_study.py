"""Each config the Agent writes is a version of the study.

Toured on `c2d547d` (10-07): after "make it 330 K" the page held two whole
study cards, each with its own Run on this machine, so the 310 K study could
be run by pressing an old button; the edit was shown as the whole plan again;
nothing said where a value came from, though the config's `decisions`
recorded it; and the plan said "System" twice (the system, then its size).
Now an edit says what changed, an older version folds to its head with Show
and Use this version and no Run (user, 10-07: "yes"), each value says where
it came from, and the size is said as Size.
"""

from __future__ import annotations

import json
import tempfile
import urllib.request

import pytest

from fastmdxplora.gui.plan import changes_between, plan_of, sourced

STUDY = {"systems": [{"id": "trpcage_1L2Y", "system": "1L2Y"}],
         "simulation": {"duration_ns": 10, "temperature_K": 310}}


def _lines(config):
    return {line["label"]: line for line in sourced(plan_of(config), config)}


def test_each_line_says_where_its_value_came_from() -> None:
    config = dict(STUDY, setup={"forcefield": "amber14"}, decisions={
        "systems": {"why": "The first structure determined.", "source": "agent"},
        "simulation.temperature_K": {"why": "Body temperature.", "source": "person"},
        "setup.forcefield": {"why": "The lab's.", "source": "fastmdx-defaults.yml"},
    })
    lines = _lines(config)
    assert lines["System"]["source"] == "agent"
    assert lines["System"]["why"] == "The first structure determined."
    assert lines["Conditions"]["source"] == "asked" and "why" not in lines["Conditions"]
    assert lines["Force field"]["source"] == "yours" and lines["Force field"]["why"] == "The lab's."
    assert lines["Equilibration"]["source"] == "default"
    # Set, and no decision recorded for it: nothing is guessed.
    assert "source" not in lines["Production"]


def test_what_the_person_asked_says_more_than_the_agent_s_choice() -> None:
    config = dict(STUDY, simulation={"temperature_K": 330, "pressure_bar": 1.0}, decisions={
        "simulation.pressure_bar": {"why": "The usual.", "source": "agent"},
        "simulation.temperature_K": {"why": "As asked.", "source": "person"}})
    assert _lines(config)["Conditions"]["source"] == "asked"


def test_a_system_is_said_by_its_name_and_what_it_is() -> None:
    assert _lines(STUDY)["System"]["value"] == "trpcage_1L2Y, from 1L2Y"
    assert _lines({"systems": [{"system": "1UBQ"}]})["System"]["value"] == "1UBQ"
    named = {"systems": [{"id": "p", "system": "/data/runs/peptide.pdb"}]}
    assert _lines(named)["System"]["value"] == "p, from peptide.pdb"


def test_a_change_is_said_setting_by_setting() -> None:
    after = json.loads(json.dumps(STUDY))
    after["simulation"]["temperature_K"] = 330
    after["analysis"] = {"include": ["rmsd", "rg"]}
    after["decisions"] = {"simulation.temperature_K": {"why": "As asked.", "source": "person"}}
    before = dict(STUDY, agent="assisted", agent_model="anthropic/x")
    assert changes_between(before, after) == [
        {"setting": "simulation.temperature_K", "label": "Temperature",
         "before": "310 K", "after": "330 K"},
        {"setting": "analysis.include", "label": "Analyses",
         "before": "not set", "after": "rmsd, rg"},
    ]
    other = dict(STUDY, systems=[{"id": "ubq", "system": "1UBQ"}])
    assert changes_between(STUDY, other)[0] == {
        "setting": "systems", "label": "System", "before": "trpcage_1L2Y (1L2Y)",
        "after": "ubq (1UBQ)"}
    assert changes_between(STUDY, STUDY) == []
    assert changes_between(None, STUDY) == []


def test_a_change_is_one_a_person_can_see() -> None:
    """Found by the review (10-07): a structure changed under the same name
    read "System protein -> protein", an emptied block "Setup not set -> {}",
    and a bias removed "Umbrella not set -> not set"."""
    same_name = changes_between({"systems": [{"id": "protein", "system": "1L2Y"}]},
                                {"systems": [{"id": "protein", "system": "1UAO"}]})
    assert [(c["before"], c["after"]) for c in same_name] == [("protein (1L2Y)", "protein (1UAO)")]
    assert changes_between({"setup": {"ph": 7}}, {"setup": {}}) == [
        {"setting": "setup.ph", "label": "pH", "before": "7", "after": "not set"}]
    assert changes_between({"setup": {}}, {}) == []
    assert changes_between({"simulation": {"umbrella": None}}, {"simulation": {}}) == []


def test_a_change_inside_a_system_is_said() -> None:
    """Found by the second review (10-07): a system's own override changed,
    or its file moved to another folder under the same name, read the same
    as before and was left out, so the new version said no change."""
    held = {"id": "p", "system": "1L2Y", "setup": {"residue_states": {"A:57": "HIP"}}}
    other = dict(held, setup={"residue_states": {"A:57": "HID"}})
    said = changes_between({"systems": [held]}, {"systems": [other]})
    assert [c["label"] for c in said] == ["System"]
    assert "HIP" in said[0]["before"] and "HID" in said[0]["after"]
    moved = changes_between({"systems": [{"id": "p", "system": "/data/v1/protein.pdb"}]},
                            {"systems": [{"id": "p", "system": "/data/v2/protein.pdb"}]})
    assert [(c["before"], c["after"]) for c in moved] == [
        ("p (/data/v1/protein.pdb)", "p (/data/v2/protein.pdb)")]
    # Still nothing where nothing a person would see changed.
    assert changes_between({"systems": [held]}, {"systems": [dict(held)]}) == []
    reordered = {"setup": {"residue_states": {"A:57": "HIP"}}, "system": "1L2Y", "id": "p"}
    assert changes_between({"systems": [held]}, {"systems": [reordered]}) == []
    assert changes_between({"setup": {"ph": None}}, {"setup": {}}) == []


def test_a_change_inside_a_system_is_said_beside_another_change() -> None:
    """Found by the fourth review (10-07): a system's own change was still
    hidden when the list changed in any other way (another system renamed
    or added), and an empty override read as a change."""
    p = {"id": "p", "system": "1L2Y", "setup": {"residue_states": {"A:57": "HIP"}}}
    q = {"id": "q", "system": "1UBQ"}
    after = [dict(p, setup={"residue_states": {"A:57": "HID"}}), dict(q, id="r")]
    said = changes_between({"systems": [p, q]}, {"systems": after})
    assert len(said) == 1
    assert "HIP" in said[0]["before"] and "HID" in said[0]["after"]
    assert "r (1UBQ)" in said[0]["after"]
    moved = changes_between({"systems": [{"id": "p", "system": "/v1/a.pdb"}]},
                            {"systems": [{"id": "p", "system": "/v2/a.pdb"}, q]})
    assert "/v1/a.pdb" in moved[0]["before"] and "/v2/a.pdb" in moved[0]["after"]
    # Unset either way is no change, inside a system too (third and fourth
    # reviews, 10-07).
    plain = {"id": "p", "system": "1L2Y"}
    for empty in ({"setup": {}}, {"setup": None}, {"setup": {"ph": None}}):
        assert changes_between({"systems": [plain]}, {"systems": [dict(plain, **empty)]}) == []


def test_a_system_s_own_settings_are_said_whatever_else_changed() -> None:
    """Found by the fifth and sixth reviews (10-07): a system's own change
    was hidden when its file or name changed too, and two systems on one
    structure without names were taken for one."""
    def pair(before, after):
        said = changes_between({"systems": before}, {"systems": after})
        assert len(said) == 1
        return said[0]["before"], said[0]["after"]

    was, now = pair([{"id": "p", "system": "a.pdb", "setup": {"ph": 7}}],
                    [{"id": "p", "system": "b.pdb", "setup": {"ph": 5}}])
    assert '"ph": 7' in was and '"ph": 5' in now and "b.pdb" in now
    was, now = pair([{"id": "a", "system": "1L2Y", "setup": {"ph": 7}}],
                    [{"id": "a2", "system": "1L2Y", "setup": {"ph": 5}}])
    assert '"ph": 7' in was and now.startswith("a2") and '"ph": 5' in now
    was, now = pair([{"system": "1UAO", "setup": {"ph": 7}}],
                    [{"system": "1L2Y", "setup": {"ph": 5}}])
    assert '"ph": 7' in was and '"ph": 5' in now
    twins = [{"system": "1L2Y", "setup": {"x": 1}}, {"system": "1L2Y", "setup": {"x": 2}}]
    was, now = pair(twins, [{"system": "1L2Y", "setup": {"x": 9}}, twins[1]])
    assert '"x": 1' in was and '"x": 9' in now
    # A system added beside an unchanged one is said in brief.
    assert pair([{"id": "p", "system": "1L2Y"}],
                [{"id": "p", "system": "1L2Y"}, {"id": "q", "system": "1UBQ"}]) == (
        "p (1L2Y)", "p (1L2Y), q (1UBQ)")


def test_a_list_of_settings_that_are_not_systems_is_said_once() -> None:
    """Found by the eighth review (10-08): a restraint's change was said
    twice over, as Python and as JSON, once systems were paired."""
    restraint = {"kind": "position", "selection": "backbone", "force_constant": 1000.0}
    said = changes_between({"simulation": {"restrain": [restraint]}},
                           {"simulation": {"restrain": [dict(restraint, force_constant=500.0)]}})
    assert len(said) == 1 and said[0]["before"].count("1000") == 1
    assert said[0]["after"].count("500") == 1 and "'kind'" not in said[0]["after"]


def test_a_system_removed_beside_its_twin_is_said_in_full() -> None:
    """Found by the eighth review (10-08): of two unnamed systems on one
    file, the plain one removed read as a pH added to the other."""
    twins = [{"system": "a.pdb"}, {"system": "a.pdb", "setup": {"ph": 7}}]
    said = changes_between({"systems": twins},
                           {"systems": [{"system": "a.pdb", "setup": {"ph": 7}}]})
    assert said[0]["before"] == 'a.pdb, a.pdb {"setup": {"ph": 7}}'
    assert said[0]["after"] == 'a.pdb {"setup": {"ph": 7}}'


def test_a_block_unset_inside_either_way_is_no_change() -> None:
    """Found by the seventh review (10-08): a block holding only unset
    values, three levels down, read as a change from not set."""
    assert changes_between({"report": {"formats": {"pdf": {"dpi": None}}}}, {"report": {}}) == []


def test_a_switch_a_list_a_sweep_and_a_block_are_said_plainly() -> None:
    said = changes_between(
        {"setup": {"use_switching_function": True},
         "sweep": {"simulation.temperature_K": [300, 310]},
         "report": {"formats": {"pdf": {"dpi": 150}}}},
        {"setup": {"use_switching_function": False},
         "sweep": {"simulation.temperature_K": [300, 330]},
         "report": {"formats": {"pdf": {"dpi": 300}}}})
    assert [(c["label"], c["before"], c["after"]) for c in said] == [
        ("Use switching function", "yes", "no"),
        ("Varied temperature", "300, 310", "300, 330"),
        ("PDF", '{"dpi": 150}', '{"dpi": 300}')]
    file = changes_between({"systems": [{"id": "p", "system": "1L2Y"}]},
                           {"systems": [{"id": "p", "system": "/data/1L2Y.pdb"}]})
    assert file[0]["after"] == "p (1L2Y.pdb)"


def test_the_reply_carries_its_changes_and_sources(monkeypatch) -> None:
    import yaml

    import fastmdxplora.agent as agent_mod
    from fastmdxplora.agent.turns import ToolCall, Turn, Usage
    from fastmdxplora.gui.agent_panel import propose_endpoint

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    warm = json.loads(json.dumps(STUDY))
    warm["simulation"]["temperature_K"] = 330
    replies = [Turn("", (ToolCall("c1", "propose_config", {
        "config": warm, "reasons": {"simulation.temperature_K": {"why": "As asked.",
                                                                  "asked": True}}}),),
                    Usage(calls=1))]

    def complete(prompt: str) -> str:
        raise AssertionError("asked in text")

    complete.turn = lambda system, messages, tools, **kw: replies.pop(0)
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: complete)
    answer = propose_endpoint({"request": "make it 330 K",
                               "current_config": yaml.safe_dump(STUDY)})
    assert answer["ok"]
    assert answer["changes"] == [{"setting": "simulation.temperature_K", "label": "Temperature",
                                  "before": "310 K", "after": "330 K"}]
    lines = {line["label"]: line for line in answer["plan"]}
    assert lines["Conditions"]["source"] == "asked"


# ---- In a browser ----------------------------------------------------------

pytest.importorskip("playwright.sync_api")


def _kept(session, entries) -> None:
    request = urllib.request.Request(
        session.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()


def _config(temperature, number=None, changes=None):
    config = json.loads(json.dumps(STUDY))
    config["simulation"]["temperature_K"] = temperature
    entry = {"role": "agent", "kind": "config", "yaml": f"t: {temperature}\n", "config": config,
             "cycles": 1, "attempts": [], "changes": changes,
             "plan": sourced(plan_of(config), config)}
    if number:
        entry["version"] = number
    return entry


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    from fastmdxplora.gui.server import start_dashboard_session

    started = start_dashboard_session(output=str(tmp_path / "study"), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


def _page(pw, url):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "#agent", wait_until="domcontentloaded")
    page.wait_for_selector("#agent-thread .agent-study:not([hidden])")
    return browser, page, errors


def _seen(page):
    return page.evaluate("""() => [...document.querySelectorAll('#agent-thread .agent-study:not([hidden])')]
        .map(s => ({version: s.querySelector('[data-role=version]').textContent,
                    folded: s.classList.contains('is-replaced'),
                    run: !!s.querySelector('[data-role=run]').offsetParent,
                    replaced: s.querySelector('[data-role=replaced]').hidden ? ''
                      : s.querySelector('[data-role=replaced]').textContent}))""")


def test_only_the_newest_version_can_be_run(session) -> None:
    from playwright.sync_api import sync_playwright

    changed = [{"setting": "simulation.temperature_K", "label": "Temperature",
                "before": "310 K", "after": "330 K"}]
    _kept(session, [{"role": "user", "text": "trp-cage for 10 ns"}, _config(310),
                    {"role": "user", "text": "make it 330 K"}, _config(330, changes=changed)])
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url)
        page.wait_for_selector("#agent-thread .agent-change-row")
        seen = _seen(page)
        change = page.text_content("#agent-thread .agent-study:not(.is-replaced):not([hidden])"
                                   " .agent-study-change")
        count = page.text_content("#agent-thread .agent-study:not(.is-replaced) [data-role=count]")
        chips = page.locator("#agent-thread .agent-study:not(.is-replaced) .agent-source"
                             ).all_text_contents()
        # The older version read, and still not runnable.
        page.click("#agent-thread .agent-study.is-replaced [data-role=unfold]")
        opened = page.is_visible("#agent-thread .agent-study.is-replaced .agent-plan")
        old_run = page.is_visible("#agent-thread .agent-study.is-replaced [data-role=run]")
        browser.close()
    assert seen == [
        {"version": "Study, version 1", "folded": True, "run": False,
         "replaced": "replaced by version 2"},
        {"version": "Study, version 2", "folded": False, "run": True, "replaced": ""}]
    assert "Temperature 310 K→330 K" in change and "Everything else as version 1." in change
    assert count == "1 change"
    assert "default" in chips
    assert opened and not old_run
    assert errors == []


def test_an_older_version_is_used_again_without_asking(session) -> None:
    from playwright.sync_api import sync_playwright

    _kept(session, [{"role": "user", "text": "trp-cage for 10 ns"}, _config(310),
                    {"role": "user", "text": "make it 330 K"}, _config(330)])
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url)
        page.click("#agent-thread .agent-study.is-replaced [data-role=use]")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) >> nth=2")
        seen = _seen(page)
        said = page.locator("#agent-thread .agent-msg-user").last.text_content()
        plan = page.text_content("#agent-thread .agent-study:not(.is-replaced) .agent-plan")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) >> nth=2")
        again = _seen(page)
        browser.close()
    assert [v["run"] for v in seen] == [False, False, True]
    assert seen[2]["version"] == "Study, version 3" and "310 K" in plan
    assert said.startswith("Use version 1")
    assert again == seen
    assert errors == []


def test_more_holds_the_file_actions(session) -> None:
    from playwright.sync_api import sync_playwright

    _kept(session, [{"role": "user", "text": "trp-cage for 10 ns"}, _config(310)])
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url)
        hidden = not page.is_visible("#agent-thread [data-role=download]")
        page.click("#agent-thread [data-role=more] > summary")
        shown = page.is_visible("#agent-thread [data-role=download]")
        page.click("#agent-thread [data-role=show]")
        closed = not page.is_visible("#agent-thread [data-role=download]")
        config = page.is_visible("#agent-thread [data-role=result]")
        browser.close()
    assert hidden and shown and closed and config
    assert errors == []


def test_a_cut_thread_changes_its_newest_version_left(session) -> None:
    """Found by the review (10-07): after an edit or a retry, the config sent
    as the current one was read from the history, which missed a version
    written with a note beside it, so the Agent changed an older version."""
    from playwright.sync_api import sync_playwright

    noted = _config(330)
    noted["note"] = "Only the temperature changed."
    _kept(session, [{"role": "user", "text": "trp-cage for 10 ns"}, _config(310),
                        {"role": "user", "text": "make it 330 K"}, noted,
                        {"role": "user", "text": "make it 5 ns"},
                        {"role": "agent", "kind": "answer", "text": "Shortly.", "cites": []}])
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url)
        page.evaluate("""() => {
            window.sentBodies = [];
            const real = window.fetch;
            window.fetch = (url, init) => {
                if (String(url).indexOf('/api/agent/propose') >= 0) {
                    window.sentBodies.push(JSON.parse(init.body));
                    return new Promise(() => {});
                }
                return real(url, init);
            };
            const third = document.querySelectorAll('#agent-thread .agent-msg-user')[2];
            [...third.querySelectorAll('.agent-msg-tools button')]
                .find(b => b.textContent === 'Retry').click(); }""")
        page.wait_for_function("() => window.sentBodies.length === 1")
        sent = page.evaluate("() => window.sentBodies[0].current_config")
        browser.close()
    assert sent == "t: 330\n"
    assert errors == []


def test_a_version_used_again_is_not_a_yes(session) -> None:
    """Found by the review (10-07): a run waiting for its yes still took the
    next message after Use this version, which answered "Not run."."""
    from playwright.sync_api import sync_playwright

    _kept(session, [{"role": "user", "text": "trp-cage"}, _config(310),
                        {"role": "user", "text": "make it 330 K"}, _config(330),
                        {"role": "user", "text": "go on then"},
                        {"role": "agent", "kind": "question", "confirm": "run",
                         "text": "Run version 2 on this machine?", "facts": ""}])
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url)
        page.route("**/api/agent/propose*", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"ok": False, "answer": "About two hours.", "cites": []})))
        page.click("#agent-thread .agent-study.is-replaced [data-role=use]")
        page.fill("#agent-request", "how long will it take?")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('About two hours.')")
        thread = page.text_content("#agent-thread")
        browser.close()
    assert "Not run." not in thread
    assert errors == []


def test_a_cut_thread_runs_its_newest_version_left(session) -> None:
    from playwright.sync_api import sync_playwright

    _kept(session, [{"role": "user", "text": "trp-cage for 10 ns"}, _config(310),
                    {"role": "user", "text": "make it 330 K"}, _config(330)])
    with sync_playwright() as pw:
        browser, page, errors = _page(pw, session.url)
        # Retry the second message: the thread is cut there, and asked again.
        page.evaluate("""() => {
            const second = document.querySelectorAll('#agent-thread .agent-msg-user')[1];
            const retry = [...second.querySelectorAll('.agent-msg-tools button')]
                .find(b => b.textContent === 'Retry');
            window.fetch = () => new Promise(() => {});
            retry.click(); }""")
        page.wait_for_function("() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 1")
        seen = _seen(page)
        browser.close()
    assert seen == [{"version": "Study, version 1", "folded": False, "run": True, "replaced": ""}]
    assert errors == []
