"""A conversation follows a run the Agent started into its folder, though
the run wrote nothing there but its config.

Found by the eighth review (10-08), driving real runs: Stop pressed in the
first second leaves a folder FastMDXplora does not count as a study (no
manifest, no phase folder). The conversation was moved there, and then
every save answered "No such study.", the next run's move "No such source
study.", and after a reload the thread was gone from the page.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastmdxplora.gui.agent_panel import (
    KEPT_AFTER_DELETE,
    attach_conversation,
    list_conversations,
    open_conversation,
    read_conversation,
    write_conversation,
)


def _bare(entries):
    """Entries as saved, without the ``eid`` a read gives an older one."""
    return [{k: v for k, v in e.items() if k != "eid"} for e in entries]


def test_the_thread_is_kept_with_a_run_that_wrote_nothing(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    said = [{"role": "user", "text": "chignolin"}]
    cid = write_conversation(runtime, said)["id"]

    first = workspace / "chignolin"
    first.mkdir()
    (first / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    assert attach_conversation(runtime, str(first), cid, None)["moved"]

    runtime.active_root = first
    more = said + [{"role": "agent", "kind": "action", "action": "run", "version": 1}]
    assert write_conversation(runtime, more, cid, str(first))["ok"]
    assert _bare(read_conversation(runtime)["entries"]) == more
    listed = list_conversations(runtime)
    assert any(c["id"] == cid for g in listed["groups"] for c in g["conversations"])

    # Opened from the list with the workspace open: the GUI loads the
    # folder, as its switch does any folder a run has begun in.
    runtime.active_root = None

    def switch_to(folder):
        runtime.active_root = folder
        return {"ok": True}

    runtime.switch_to = switch_to
    opened = open_conversation(runtime, cid, str(first))
    assert opened["ok"] and _bare(opened["entries"]) == more and opened["loaded_study"]
    assert runtime.active_root == first

    second = workspace / "chignolin-2"
    second.mkdir()
    moved = attach_conversation(runtime, str(second), cid, str(first))
    assert moved["ok"] and moved["moved"]



def _launches(monkeypatch, workspace, taking=0.0):
    """The runtime's launch, without a process: each run gets a new folder
    holding its config, and the GUI switches to it, as a real launch does,
    then answers after ``taking`` seconds."""
    import time

    from fastmdxplora.gui.exploration import DashboardRuntime

    made = []

    def launch(self, state, *, dashboard_url=None, config=None):
        folder = workspace / f"run{len(made) + 1}"
        folder.mkdir()
        (folder / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
        made.append(str(folder))
        self.active_root = folder
        time.sleep(taking)
        return {"ok": True, "output": str(folder)}

    monkeypatch.setattr(DashboardRuntime, "launch_from_config", launch)
    return made


def test_the_run_moves_its_conversation_as_it_starts(tmp_path, monkeypatch) -> None:
    """Found by the tenth review (10-08): moved by the page after the run
    started, the conversation raced the page's own saves and its switch to
    the new study. The server moves it as it starts the run."""
    from fastmdxplora.gui.agent_panel import run_endpoint
    from fastmdxplora.gui.exploration import DashboardRuntime

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    made = _launches(monkeypatch, workspace)
    runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    cid = write_conversation(runtime, [{"role": "user", "text": "chignolin"}])["id"]
    started = run_endpoint({"config": {"systems": [{"system": "1UAO"}]},
                            "conversation": {"id": cid, "study": None}}, runtime)
    assert started["ok"] and started["conversation"]["moved"]
    assert started["conversation"]["study"] == made[0]
    assert read_conversation(runtime)["id"] == cid
    again = run_endpoint({"config": {"systems": [{"system": "1UAO"}]},
                          "conversation": {"id": cid, "study": made[0]}}, runtime)
    assert again["conversation"]["moved"] and again["conversation"]["study"] == made[1]


def test_a_slow_disk_loses_nothing_across_two_runs(tmp_path, monkeypatch) -> None:
    """Found by the tenth review (10-08), with saves taking 0.8 s: the page
    switched to the second run's empty folder while the move waited, and
    the next message was saved over the moved thread; a save in flight
    sent the thread back to Chats."""
    import json
    import tempfile
    import time

    import pytest

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui import agent_panel
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    made = _launches(monkeypatch, workspace)
    written = agent_panel._write_one

    def slow(*args, **kwargs):
        time.sleep(0.8)
        return written(*args, **kwargs)

    monkeypatch.setattr(agent_panel, "_write_one", slow)
    session = start_dashboard_session(output=str(workspace / "here"), host="127.0.0.1",
                                      port=0)

    def config(ns):
        return {"ok": True, "cycles": 1, "yaml": "systems:\n- system: 1UAO\n",
                "config": {"systems": [{"system": "1UAO"}], "simulation": {"duration_ns": ns}},
                "plan": [], "attempts": []}

    replies = [config(2), {"ok": False, "answer": "It has just begun.", "cites": [],
                           "attempts": []}, config(4)]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(30000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/agent/propose*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.wait_for_selector("#agent-request", state="visible")

            def say(text):
                page.fill("#agent-request", text)
                page.keyboard.press("Enter")

            say("chignolin")
            page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
            page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
            say("how is it going?")
            page.wait_for_selector("#agent-thread .agent-answer:has-text('just begun')")
            say("make it 4 ns")
            page.wait_for_function(
                "() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 2")
            page.click("#agent-thread .agent-study:not([hidden]) [data-role=run] >> nth=-1")
            page.wait_for_function(
                "() => document.querySelectorAll('#agent-thread .agent-running').length === 2")
            page.wait_for_timeout(6000)  # every save behind the moves lands
            shown = page.locator("#agent-thread .agent-msg-user").count()
            browser.close()
    finally:
        session.server.shutdown()
    kept = list((tmp_path / "workspace").rglob("conversations/conv-*.json"))
    kept += list((tmp_path / "workspace").rglob(".fastmdxplora_agent_conversations/conv-*.json"))
    assert [p.parent.parent.parent.name for p in kept] == ["run2"]
    entries = json.loads(kept[0].read_text(encoding="utf-8"))["entries"]
    assert [e.get("output") for e in entries if e.get("kind") == "action"] == made
    assert any(e.get("text") == "It has just begun." for e in entries)
    assert shown == 3
    assert errors == []


def test_a_study_of_several_runs_is_loaded_with_its_conversation(tmp_path) -> None:
    """Found by the ninth and tenth reviews (10-08): opened from the list,
    a study of several runs' conversation left the GUI on another study,
    so the thread and what the Agent was told disagreed."""
    workspace = tmp_path / "workspace"
    sweep = workspace / "sweep"
    (sweep / "runs").mkdir(parents=True)
    (sweep / "batch_manifest.json").write_text("{}", encoding="utf-8")
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    cid = write_conversation(runtime, [{"role": "user", "text": "a sweep"}])["id"]
    assert attach_conversation(runtime, str(sweep), cid, None)["moved"]
    switched = []
    runtime.switch_to = lambda folder: switched.append(folder) or {"ok": True}
    assert open_conversation(runtime, cid, str(sweep))["loaded_study"]
    assert switched == [sweep]



def test_a_launch_says_where_the_run_went_in_the_entry_the_page_saved(tmp_path, monkeypatch):
    """Found by the twelfth review (10-08): a reload before the page saved
    the run's entry left the moved thread with no word of the run. The page
    saves the entry first; the move writes where the run went into it."""
    from fastmdxplora.gui.agent_panel import merge_conversation, run_endpoint
    from fastmdxplora.gui.exploration import DashboardRuntime

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    made = _launches(monkeypatch, workspace)
    runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    said = [{"eid": "u1", "role": "user", "text": "chignolin"},
            {"eid": "r1", "role": "agent", "kind": "action", "action": "run", "where": "",
             "version": 1}]
    cid = merge_conversation(runtime, said, [])["id"]
    run_endpoint({"config": {"systems": [{"system": "1UAO"}]},
                  "conversation": {"id": cid, "study": None, "run": "r1"}}, runtime)
    kept = read_conversation(runtime)["entries"]
    assert [e["eid"] for e in kept] == ["u1", "r1"]
    assert kept[-1]["output"] == made[0] and kept[-1]["started"]


def test_a_save_merges_with_what_others_wrote(tmp_path) -> None:
    """Tenth to fourteenth reviews (10-08): the page wrote its whole list,
    so another tab's entries, the run's folder written by the server, and
    entries the page cut were all decided by whoever wrote last."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    first = [{"eid": "a", "role": "user", "text": "one"},
             {"eid": "b", "role": "agent", "kind": "answer", "text": "One."}]
    cid = merge_conversation(runtime, first, [])["id"]
    # Another tab adds its exchange; the server writes where a run went.
    other = first + [{"eid": "c", "role": "user", "text": "from the other tab"}]
    merge_conversation(runtime, other, ["a", "b"], cid, None)
    # This tab, which never saw "c", marks "b" Useful and adds "d".
    mine = [dict(first[0]), dict(first[1], feedback="useful"),
            {"eid": "d", "role": "user", "text": "two"}]
    kept = merge_conversation(runtime, mine, ["a", "b"], cid, None)["entries"]
    assert [e["eid"] for e in kept] == ["a", "b", "d", "c"]
    assert kept[1]["feedback"] == "useful"
    # Then it cuts back to "a" (an edit): what it held goes, "c" stays.
    kept = merge_conversation(runtime, [dict(first[0])], ["a", "b", "c", "d"], cid,
                              None)["entries"]
    assert [e["eid"] for e in kept] == ["a"]


def test_a_deleted_conversation_is_not_written_again(tmp_path) -> None:
    """Found by the thirteenth review (10-08): a conversation deleted while
    a run started came back with the run's save."""
    from fastmdxplora.gui.agent_panel import delete_conversation, merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    cid = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "x"}], [])["id"]
    assert delete_conversation(runtime, cid)["ok"]
    said = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "x"}], ["a"],
                              cid, None)
    assert not said["ok"] and said["gone"]
    assert not list(workspace.rglob(f"{cid}.json"))


def test_an_unnamed_save_never_writes_over_the_current_one(tmp_path) -> None:
    """Found by the thirteenth and fourteenth reviews (10-08): a save from a
    thread with no id yet wrote into whichever conversation was current
    where the GUI was."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    kept = [{"eid": "a", "role": "user", "text": "kept"}]
    first = merge_conversation(runtime, kept, [])["id"]
    second = merge_conversation(runtime, [{"eid": "z", "role": "user", "text": "new"}], [])
    assert second["id"] != first
    assert merge_conversation(runtime, kept, ["a"], first, None)["entries"] == kept


def test_a_conversation_is_found_in_a_run_outside_the_workspace(tmp_path) -> None:
    """Found by the thirteenth review (10-08): a run's folder outside the
    workspace was not looked in, and a save made a second copy."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    elsewhere = tmp_path / "elsewhere" / "run1"
    elsewhere.mkdir(parents=True)
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None,
                              running_root=elsewhere)
    cid = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "x"}], [])["id"]
    assert attach_conversation(runtime, str(elsewhere), cid, None)["moved"]
    saved = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "x"},
                                         {"eid": "b", "role": "user", "text": "y"}],
                               ["a"], cid, None)
    assert saved["study"] == str(elsewhere)
    assert not list((workspace / ".fastmdxplora_agent_conversations").glob("conv-*.json"))


def test_a_save_from_another_tab_goes_where_the_thread_went(tmp_path) -> None:
    """Found by the twelfth review (10-08): a tab still naming Chats saved a
    thread another tab's run had moved, and it lived in two places."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    cid = write_conversation(runtime, [{"role": "user", "text": "chignolin"}])["id"]
    run = workspace / "run1"
    run.mkdir()
    assert attach_conversation(runtime, str(run), cid, None)["moved"]
    said = [{"role": "user", "text": "chignolin"}, {"role": "user", "text": "and?"}]
    saved = write_conversation(runtime, said, cid, None)
    assert saved["study"] == str(run) and saved["entries"] == said
    assert not list((workspace / ".fastmdxplora_agent_conversations").glob("conv-*.json"))


def test_a_folder_the_gui_cannot_load_opens_without_switching(tmp_path) -> None:
    """Its conversation opened; the GUI stays where it is (eleventh review,
    10-08: untested)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    cid = write_conversation(runtime, [{"role": "user", "text": "kept"}])["id"]
    bare = workspace / "bare"
    bare.mkdir()
    assert attach_conversation(runtime, str(bare), cid, None)["moved"]
    switched = []
    runtime.switch_to = lambda folder: switched.append(folder) or {"ok": True}
    opened = open_conversation(runtime, cid, str(bare))
    assert opened["ok"] and not opened["loaded_study"] and switched == []


#: Each browser a test opened and has not closed: closed as the test ends,
#: passed or not. One left running (a test timed out before its close) made
#: every later Playwright and asyncio.run() in the run fail (CI run #704).
_OPEN_BROWSERS: list = []


@pytest.fixture(autouse=True)
def _browsers_closed():
    yield
    while _OPEN_BROWSERS:
        _OPEN_BROWSERS.pop()()


def _page_on(session, replies):
    import json

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = None
    closed = []

    def close():
        if closed:
            return
        closed.append(True)
        try:
            if browser is not None:
                browser.close()
        finally:
            pw.stop()
            if close in _OPEN_BROWSERS:
                _OPEN_BROWSERS.remove(close)

    _OPEN_BROWSERS.append(close)
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(30000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/agent/propose*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
    page.goto(session.url + "#agent", wait_until="domcontentloaded")
    page.wait_for_selector("#agent-request", state="visible")
    return page, errors, close


#: A message's Retry, by its word or, drawn as an icon, by its name.
_RETRY = ("button:has-text('Retry'), button[aria-label='Retry'], "
          "button[title^='Send this again']")


def _say(page, text):
    page.fill("#agent-request", text)
    page.keyboard.press("Enter")


_CONFIG = {"ok": True, "cycles": 1, "yaml": "systems:\n- system: 1UAO\n",
           "config": {"systems": [{"system": "1UAO"}]}, "plan": [], "attempts": []}


def _kept_everywhere(workspace):
    import json

    found = {}
    for path in [*workspace.rglob("agent/conversations/conv-*.json"),
                 *workspace.rglob(".fastmdxplora_agent_conversations/conv-*.json")]:
        found[path.stem] = [e.get("text") or e.get("kind")
                            for e in json.loads(path.read_text(encoding="utf-8"))["entries"]]
    return found


def _entries_everywhere(workspace):
    import json

    found = {}
    for path in [*workspace.rglob("agent/conversations/conv-*.json"),
                 *workspace.rglob(".fastmdxplora_agent_conversations/conv-*.json")]:
        found[str(path)] = json.loads(path.read_text(encoding="utf-8"))["entries"]
    return found


def test_a_new_thread_started_while_a_run_starts_loses_neither(tmp_path, monkeypatch):
    """Found by the eleventh and twelfth reviews (10-08): New pressed while
    the launch was answered, and the moved thread was written over with the
    new one's empty list and the run."""
    import tempfile

    import pytest

    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _launches(monkeypatch, workspace, taking=1.5)
    session = start_dashboard_session(output=str(workspace / "here"), host="127.0.0.1",
                                      port=0)
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-new")
        page.wait_for_timeout(4000)
        shown = page.locator("#agent-thread .agent-msg").count()
        close()
    finally:
        session.server.shutdown()
    kept = _kept_everywhere(workspace)
    assert any(entries[:1] == ["chignolin"] and "action" in entries
               for entries in kept.values()), kept
    assert shown == 0
    assert errors == []


def test_a_thread_switched_while_its_save_waits_keeps_both(tmp_path, monkeypatch):
    """Found by the eleventh review (10-08): with saves taking 0.8 s, "alpha"
    answered and marked Useful, then New, then "beta": the first thread
    held only beta's exchange."""
    import tempfile
    import time

    import pytest

    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui import agent_panel
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    written = agent_panel._write_one

    def slow(store, cid, entries, *args, **kwargs):
        if entries:
            time.sleep(0.8)
        return written(store, cid, entries, *args, **kwargs)

    monkeypatch.setattr(agent_panel, "_write_one", slow)
    session = start_dashboard_session(output=str(workspace / "here"), host="127.0.0.1",
                                      port=0)
    answers = [{"ok": False, "answer": f"Answer {n}.", "cites": [], "attempts": []}
               for n in (1, 2)]
    try:
        page, errors, close = _page_on(session, answers)
        _say(page, "alpha")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Answer 1')")
        page.locator("#agent-thread [data-feedback=useful]").last.click()
        page.click("#agent-new")
        page.wait_for_timeout(300)
        _say(page, "beta")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Answer 2')")
        page.wait_for_timeout(5000)
        close()
    finally:
        session.server.shutdown()
    kept = sorted(_kept_everywhere(workspace).values())
    assert ["alpha", "Answer 1."] in kept and ["beta", "Answer 2."] in kept, kept
    # The mark made just before New is kept with its thread (fourteenth
    # review, 10-08: a save reading the thread on screen when its turn
    # came lost it).
    marked = [e for entries in _entries_everywhere(workspace).values() for e in entries
              if e.get("text") == "Answer 1."]
    assert [e.get("feedback") for e in marked] == ["useful"]
    assert errors == []


def test_the_thread_is_left_alone_while_its_run_starts(tmp_path, monkeypatch):
    """The GUI is on the run's folder before the launch answers (the move
    and the run's entry are written after the run starts); the page did not
    empty the thread then (twelfth review, 10-08: untested)."""
    import tempfile
    import time

    import pytest

    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui import agent_panel
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _launches(monkeypatch, workspace)
    said_where = agent_panel._say_where_it_ran

    def slow(*args, **kwargs):
        time.sleep(2.0)  # the GUI is on the run's folder; the launch has not answered
        return said_where(*args, **kwargs)

    monkeypatch.setattr(agent_panel, "_say_where_it_ran", slow)
    session = start_dashboard_session(output=str(workspace / "here"), host="127.0.0.1",
                                      port=0)
    try:
        import time as clock

        def model(payload, *args, **kwargs):
            if "while it starts" in str(payload.get("request")):
                clock.sleep(3.0)  # still being written when the GUI switches
                return _answer("While it starts.")
            return dict(_CONFIG)

        monkeypatch.setattr(agent_panel, "propose_endpoint", model)
        page, errors, close = _page_on(session, [])
        page.unroute("**/api/agent/propose*")
        # The second run: the thread already lives in the first run's
        # folder, and the GUI goes to the second's before the move.
        for n, typed in enumerate(("chignolin", "again"), start=1):
            _say(page, typed)
            page.wait_for_function(
                "n => document.querySelectorAll('#agent-thread .agent-study:not([hidden])')"
                ".length === n", arg=n)
            page.click("#agent-thread .agent-study:not([hidden]) [data-role=run] >> nth=-1")
            if n == 2:
                # Asked while it starts: its answer is shown (eighteenth
                # review, 10-08: without the guard it never was).
                _say(page, "asked while it starts")
            # Asked for the GUI's state again and again while the run
            # starts: one answer falls between the switch and the move.
            for _ in range(14):
                page.wait_for_timeout(500)
                page.evaluate("() => document.getElementById('refresh-now').click()")
            page.wait_for_function(
                "n => document.querySelectorAll('#agent-thread .agent-running').length"
                " >= n", arg=n, timeout=20000)
        # The fake runs have no process, so their summaries soon say they
        # ended: their lines stay, said as run.
        lines = page.locator("#agent-thread .agent-running").count()
        asked = page.locator("#agent-thread .agent-msg-user").count()
        answered = page.locator("#agent-thread .agent-answer:has-text('While it starts.')").count()
        close()
    finally:
        session.server.shutdown()
    assert lines == 2 and asked == 3 and answered == 1
    assert errors == []


def test_a_launch_with_no_answer_is_said_in_the_thread(tmp_path, monkeypatch):
    """Found by the twelfth review (10-08): an error page or a dropped
    connection was said, not kept."""
    import tempfile

    import pytest

    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = start_dashboard_session(output=str(workspace / "here"), host="127.0.0.1",
                                      port=0)
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        page.route("**/api/agent/run", lambda route: route.abort())
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(2000)
        close()
    finally:
        session.server.shutdown()
    # Said, and not as "it did not start": it may have (thirteenth and
    # fourteenth reviews, 10-08); the run asked for stays in the thread.
    kept = _kept_everywhere(workspace)
    assert any(any("could not read the software" in str(e) for e in entries)
               and "action" in entries for entries in kept.values()), kept



def _gui(tmp_path, monkeypatch, taking):
    import tempfile

    import pytest

    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    made = _launches(monkeypatch, workspace, taking=taking)
    session = start_dashboard_session(output=str(workspace / "here"), host="127.0.0.1",
                                      port=0)
    return workspace, made, session


def _answer(text):
    return {"ok": False, "answer": text, "cites": [], "attempts": []}


def _runs_in(entries):
    return [e for e in entries if e.get("kind") == "action" and e.get("action") == "run"]


def test_a_reload_while_a_run_starts_keeps_what_was_said(tmp_path, monkeypatch):
    """Found by the thirteenth and fourteenth reviews (10-08): saves waited
    behind the launch, so a reload dropped what was said while the run
    started, and the reloaded page's next save replaced the run's entry."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=3.0)
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG), _answer("Starting."),
                                                 _answer("After.")])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        _say(page, "asked while it starts")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Starting.')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        page.wait_for_timeout(4000)
        _say(page, "after the reload")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('After.')")
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    threads = [e for e in _entries_everywhere(workspace).values() if e]
    said = [x for entries in threads for x in entries]
    texts = [x.get("text") for x in said]
    assert "asked while it starts" in texts and "Starting." in texts
    assert [r.get("output") for r in _runs_in(said)] == made


def test_a_second_tab_keeps_the_run_the_first_started(tmp_path, monkeypatch):
    """Found by the thirteenth review (10-08): a second tab on the same
    conversation saved its list, which had no word of the run the first
    tab started, over the moved thread."""
    import json

    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=2.0)
    replies = {"a": [dict(_CONFIG)], "b": [_answer("B one."), _answer("B two.")]}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            tabs = {}
            for name in ("a", "b"):
                tab = browser.new_page(viewport={"width": 1400, "height": 900})
                tab.set_default_timeout(30000)
                def answers(said):
                    return lambda route: route.fulfill(
                        status=200, content_type="application/json",
                        body=json.dumps(said.pop(0)))

                tab.route("**/api/agent/propose*", answers(replies[name]))
                tabs[name] = tab
            a, b = tabs["a"], tabs["b"]
            a.goto(session.url + "#agent", wait_until="domcontentloaded")
            a.wait_for_selector("#agent-request", state="visible")
            _say(a, "chignolin")
            a.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
            a.wait_for_timeout(800)
            b.goto(session.url + "#agent", wait_until="domcontentloaded")
            b.wait_for_selector("#agent-thread .agent-study:not([hidden])")
            a.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
            _say(b, "from tab B")
            b.wait_for_selector("#agent-thread .agent-answer:has-text('B one.')")
            a.wait_for_selector("#agent-thread .agent-running")
            _say(b, "again from tab B")
            b.wait_for_selector("#agent-thread .agent-answer:has-text('B two.')")
            b.wait_for_timeout(1500)
            browser.close()
    finally:
        session.server.shutdown()
    threads = {k: v for k, v in _entries_everywhere(workspace).items() if v}
    assert len(threads) == 1, threads
    said = next(iter(threads.values()))
    texts = [x.get("text") for x in said]
    assert "from tab B" in texts and "again from tab B" in texts
    assert [r.get("output") for r in _runs_in(said)] == made


def test_a_conversation_deleted_while_its_run_starts_stays_deleted(tmp_path, monkeypatch):
    """Found by the thirteenth review (10-08): deleted while the launch was
    answered, it came back with the run's save."""
    import json
    import urllib.request

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=2.0)
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(800)
        cid = page.evaluate("() => window.FastMDXAgentPanel.current.id")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(500)
        request = urllib.request.Request(
            session.url + "/api/agent/conversation/delete",
            data=json.dumps({"id": cid, "study": None}).encode(),
            headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
        assert json.loads(urllib.request.urlopen(request, timeout=10).read())["ok"]
        page.wait_for_timeout(4000)
        close()
    finally:
        session.server.shutdown()
    assert not list(workspace.rglob(f"{cid}.json"))


def test_a_run_started_whose_answer_was_lost_is_kept_as_a_run(tmp_path, monkeypatch):
    """Found by the thirteenth and fourteenth reviews (10-08): the server
    started the run, a proxy answered with an error page, and the thread
    then said the run did not start, over the server's word of it."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)

    def lost(route):
        route.fetch()  # the server starts the run
        route.fulfill(status=502, content_type="text/html", body="<h1>Bad gateway</h1>")

    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        page.route("**/api/agent/run", lost)
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(2500)
        close()
    finally:
        session.server.shutdown()
    threads = [e for e in _entries_everywhere(workspace).values() if e]
    said = [x for entries in threads for x in entries]
    assert [r.get("output") for r in _runs_in(said)] == made
    assert not any("did not start" in str(x.get("text")) for x in said)


def _older(workspace, entries):
    """A conversation saved before entries carried an eid."""
    import json

    store = workspace / ".fastmdxplora_agent_conversations"
    store.mkdir(parents=True, exist_ok=True)
    cid = "conv-20261001-120000-000001"
    (store / f"{cid}.json").write_text(json.dumps(
        {"version": 3, "id": cid, "entries": entries}), encoding="utf-8")
    (store / "current").write_text(cid, encoding="utf-8")
    return cid


def test_an_older_conversation_in_two_tabs_is_kept_once(tmp_path) -> None:
    """Found by the fifteenth and sixteenth reviews (10-08): each tab gave
    the older entries eids of its own, and the store kept both copies."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cid = _older(workspace, [{"role": "user", "text": "q1"},
                             {"role": "agent", "kind": "answer", "text": "A1."}])
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    a = read_conversation(runtime)["entries"]
    b = read_conversation(runtime)["entries"]
    assert [e["eid"] for e in a] == [e["eid"] for e in b]
    seen = [e["eid"] for e in a]
    merge_conversation(runtime, a + [{"eid": "x", "role": "user", "text": "from A"}],
                       seen, cid, None)
    kept = merge_conversation(runtime, b + [{"eid": "y", "role": "user", "text": "from B"}],
                              seen, cid, None)["entries"]
    assert [e["text"] for e in kept] == ["q1", "A1.", "from B", "from A"]


def test_a_cut_stays_cut_when_another_tab_still_holds_it(tmp_path) -> None:
    """Found by the fifteenth and sixteenth reviews (10-08): tab A retried
    a question; tab B, which still held it, saved, and it came back."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    said = [{"eid": "q1", "role": "user", "text": "q1"},
            {"eid": "q2", "role": "user", "text": "q2"}]
    cid = merge_conversation(runtime, said, [])["id"]
    retried = [said[0], {"eid": "q2b", "role": "user", "text": "q2 again"}]
    merge_conversation(runtime, retried, ["q1", "q2"], cid, None)
    kept = merge_conversation(runtime, said + [{"eid": "q3", "role": "user", "text": "q3"}],
                              ["q1", "q2"], cid, None)["entries"]
    assert [e["eid"] for e in kept] == ["q1", "q3", "q2b"]


def test_a_thread_s_unnamed_saves_make_one_conversation(tmp_path) -> None:
    """Found by the fifteenth and sixteenth reviews (10-08): a reload with a
    fresh thread's first save in flight made two conversations."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    first = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "one"}], [],
                               key="k1")
    second = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "one"},
                                          {"eid": "b", "role": "user", "text": "two"}], [],
                                key="k1", append=True)
    assert first["id"] == second["id"]
    assert [e["eid"] for e in second["entries"]] == ["a", "b"]


def test_a_save_of_a_thread_not_on_screen_leaves_the_current_one(tmp_path) -> None:
    """Found by the sixteenth review (10-08): a save of a thread no longer
    on screen made it current, and a reload opened it."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    old = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "old"}], [])["id"]
    new = merge_conversation(runtime, [{"eid": "b", "role": "user", "text": "new"}], [])["id"]
    merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "old"}], ["a"], old,
                       None, current=False)
    assert read_conversation(runtime)["id"] == new


def test_a_closing_page_adds_and_never_cuts(tmp_path) -> None:
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    said = [{"eid": "a", "role": "user", "text": "one"},
            {"eid": "b", "role": "agent", "kind": "answer", "text": "One."}]
    cid = merge_conversation(runtime, said, [])["id"]
    kept = merge_conversation(runtime, [dict(said[1], feedback="useful"),
                                        {"eid": "c", "role": "user", "text": "two"}],
                              ["a", "b"], cid, None, append=True)["entries"]
    assert [e["eid"] for e in kept] == ["a", "b", "c"] and kept[1]["feedback"] == "useful"


def test_a_conversation_moved_by_a_run_is_deleted_where_it_went(tmp_path) -> None:
    """Found by the sixteenth review (10-08): deleted from a list drawn
    before the run moved it, it answered "No such conversation"."""
    from fastmdxplora.gui.agent_panel import delete_conversation, merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    cid = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "x"}], [])["id"]
    run = workspace / "run1"
    run.mkdir()
    assert attach_conversation(runtime, str(run), cid, None)["moved"]
    assert delete_conversation(runtime, cid, None)["ok"]
    assert not list(workspace.rglob(f"{cid}.json"))


def test_a_read_while_a_run_starts_waits_for_its_move(tmp_path, monkeypatch) -> None:
    """Found by the sixteenth review (10-08): between the GUI going to the
    run's folder and the move, a reload or another tab read an empty thread
    there, and began a second one."""
    import threading
    import time

    from fastmdxplora.gui.agent_panel import merge_conversation, run_endpoint
    from fastmdxplora.gui.exploration import DashboardRuntime

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    cid = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "x"}], [])["id"]
    read = {}

    def launch(self, state, *, dashboard_url=None, config=None):
        folder = workspace / "run1"
        folder.mkdir()
        self.active_root = folder
        reader = threading.Thread(target=lambda: read.update(read_conversation(self)))
        reader.start()
        time.sleep(0.5)
        read["during"] = dict(read)
        return {"ok": True, "output": str(folder)}

    from fastmdxplora.gui.exploration import DashboardRuntime as Runtime

    monkeypatch.setattr(Runtime, "launch_from_config", launch)
    run_endpoint({"config": {"systems": [{"system": "1UAO"}]},
                  "conversation": {"id": cid, "study": None}}, runtime)
    time.sleep(0.5)
    assert read["during"] == {}
    assert read["id"] == cid and [e["text"] for e in read["entries"]] == ["x"]


def _slow_disk(monkeypatch, seconds):
    import time

    from fastmdxplora.gui import agent_panel

    written = agent_panel._write_one

    def slow(store, cid, entries, *args, **kwargs):
        if entries:
            time.sleep(seconds)
        return written(store, cid, entries, *args, **kwargs)

    monkeypatch.setattr(agent_panel, "_write_one", slow)


def _wait_for(found, seconds=15.0):
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if found():
            return True
        time.sleep(0.25)
    return found()


def test_a_page_closed_with_saves_waiting_keeps_them_in_a_long_thread(tmp_path, monkeypatch):
    """Found by the fifteenth and sixteenth reviews (10-08): the save sent
    as the page closed carried the whole thread, and over 64 KB the browser
    did not send it; the saves still waiting were lost."""
    import json

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    long = [{"role": "user" if i % 2 == 0 else "agent", "kind": "answer",
             "text": f"old {i} " + "x" * 1500} for i in range(60)]
    cid = _older(workspace, long)
    (workspace / "here").mkdir(exist_ok=True)
    _slow_disk(monkeypatch, 2.0)
    try:
        page, errors, close = _page_on(session, [_answer("One."), _answer("Two.")])
        page.wait_for_selector("#agent-thread .agent-answer >> nth=0")
        _say(page, "msg 1")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('One.')")
        _say(page, "msg 2")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Two.')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        store = workspace / ".fastmdxplora_agent_conversations" / f"{cid}.json"
        kept = _wait_for(lambda: "Two." in json.dumps(
            json.loads(store.read_text(encoding="utf-8"))["entries"]), 20.0)
        close()
    finally:
        session.server.shutdown()
    assert kept


def test_a_thread_reopened_while_its_saves_wait_shows_them(tmp_path, monkeypatch):
    """Found by the fourteenth review (10-08): opened again while its save
    waited, the thread was drawn without what that save said."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    try:
        page, errors, close = _page_on(session, [_answer("A."), _answer("B.")])
        _say(page, "alpha")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('A.')")
        page.wait_for_timeout(500)
        first = page.evaluate("() => window.FastMDXAgentPanel.current")
        page.click("#agent-new")
        page.wait_for_timeout(500)
        _slow_disk(monkeypatch, 1.5)
        _say(page, "bee")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('B.')")
        second = page.evaluate("() => window.FastMDXAgentPanel.current")
        page.evaluate("([a, b]) => window.FastMDXAgentPanel.open(a.id, a.study, true)"
                      ".then(() => window.FastMDXAgentPanel.open(b.id, b.study, true))",
                      [first, second])
        page.wait_for_timeout(5000)
        shown = page.locator("#agent-thread .agent-answer").all_text_contents()
        close()
    finally:
        session.server.shutdown()
    assert shown == ["B."]


def test_a_retry_in_one_tab_is_not_undone_by_the_other(tmp_path, monkeypatch):
    """Found by the fifteenth and sixteenth reviews (10-08): tab A retried a
    question; tab B, which still held it, saved, and it came back."""
    import json

    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    replies = {"a": [_answer("A1."), _answer("A2."), _answer("A2 again.")],
               "b": [_answer("B3.")]}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            tabs = {}
            for name in ("a", "b"):
                tab = browser.new_page(viewport={"width": 1400, "height": 900})
                tab.set_default_timeout(30000)

                def answers(said):
                    return lambda route: route.fulfill(
                        status=200, content_type="application/json",
                        body=json.dumps(said.pop(0)))

                tab.route("**/api/agent/propose*", answers(replies[name]))
                tabs[name] = tab
            a, b = tabs["a"], tabs["b"]
            a.goto(session.url + "#agent", wait_until="domcontentloaded")
            a.wait_for_selector("#agent-request", state="visible")
            _say(a, "q1")
            a.wait_for_selector("#agent-thread .agent-answer:has-text('A1.')")
            _say(a, "q2")
            a.wait_for_selector("#agent-thread .agent-answer:has-text('A2.')")
            a.wait_for_timeout(800)
            b.goto(session.url + "#agent", wait_until="domcontentloaded")
            b.wait_for_selector("#agent-thread .agent-answer:has-text('A2.')")
            a.locator("#agent-thread .agent-msg-user").last.hover()
            a.locator("#agent-thread .agent-msg-user").last.locator(
                _RETRY).click()
            a.wait_for_selector("#agent-thread .agent-answer:has-text('A2 again.')")
            a.wait_for_timeout(800)
            _say(b, "q3")
            b.wait_for_selector("#agent-thread .agent-answer:has-text('B3.')")
            b.wait_for_timeout(1500)
            on_b = b.locator("#agent-thread .agent-answer").all_text_contents()
            browser.close()
    finally:
        session.server.shutdown()
    threads = [e for e in _entries_everywhere(workspace).values() if e]
    assert len(threads) == 1
    texts = [e.get("text") for e in threads[0]]
    assert texts.count("A2.") == 0 and "A2 again." in texts and "B3." in texts
    assert "A2." not in on_b and "A2 again." in on_b


def test_an_answer_after_new_is_said_in_its_own_thread(tmp_path, monkeypatch):
    """Found by the sixteenth review (10-08): asked, then New before the
    answer came, and the answer was written into the new thread."""
    import time

    from fastmdxplora.gui import agent_panel

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)

    def slow_answer(payload, *args, **kwargs):
        time.sleep(2.0)  # the AI model, slow to answer
        return _answer("Late.")

    monkeypatch.setattr(agent_panel, "propose_endpoint", slow_answer)
    try:
        page, errors, close = _page_on(session, [])
        page.unroute("**/api/agent/propose*")
        _say(page, "asked before New")
        page.wait_for_timeout(300)
        page.click("#agent-new")
        page.wait_for_timeout(4000)
        shown = page.locator("#agent-thread .agent-msg").count()
        close()
    finally:
        session.server.shutdown()
    threads = [[e.get("text") for e in entries]
               for entries in _entries_everywhere(workspace).values() if entries]
    # Said in the thread it was asked in, and nowhere else.
    assert threads == [["asked before New", "Stopped before it finished."]], threads
    assert shown == 0


def test_a_closing_page_s_cuts_are_kept(tmp_path) -> None:
    """Found by the eighteenth review (10-08): a retry with its save still
    waiting, then a reload; the save sent as the page closed only added,
    and the retried-away answer came back."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    said = [{"eid": "q2", "role": "user", "text": "q2"},
            {"eid": "a2", "role": "agent", "kind": "answer", "text": "A2."}]
    cid = merge_conversation(runtime, said, [])["id"]
    kept = merge_conversation(runtime, [{"eid": "q2b", "role": "user", "text": "q2"}],
                              ["q2", "a2", "q2b"], cid, None, append=True,
                              dropped=["q2", "a2"])["entries"]
    assert [e["eid"] for e in kept] == ["q2b"]


def test_new_and_a_closing_save_make_one_conversation(tmp_path) -> None:
    """Found by the eighteenth review (10-08): New, then a reload before
    /new was sent; the closing save made one conversation, /new another."""
    from fastmdxplora.gui.agent_panel import merge_conversation, new_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    made = new_conversation(runtime, key="k9")["id"]
    saved = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "typed"}], [],
                               key="k9", append=True)
    assert saved["id"] == made
    first = merge_conversation(runtime, [{"eid": "b", "role": "user", "text": "x"}], [],
                               key="k10", append=True)["id"]
    assert new_conversation(runtime, key="k10")["id"] == first


def test_a_conversation_moved_by_a_run_is_named_where_it_went(tmp_path) -> None:
    """Found by the seventeenth review (10-08): renamed from a list drawn
    before a run moved it, it answered "No such conversation"."""
    from fastmdxplora.gui.agent_panel import merge_conversation, rename_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    cid = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "x"}], [])["id"]
    run = workspace / "run1"
    run.mkdir()
    assert attach_conversation(runtime, str(run), cid, None)["moved"]
    assert rename_conversation(runtime, cid, None, "Named")["title"] == "Named"


def test_a_run_moves_its_conversation_from_where_it_lives(tmp_path, monkeypatch) -> None:
    """Found by the fifteenth review (10-08): a tab that last heard of the
    thread in Chats started a run after another tab's run had moved it;
    the move looked in Chats only and left it behind."""
    from fastmdxplora.gui.agent_panel import merge_conversation, run_endpoint
    from fastmdxplora.gui.exploration import DashboardRuntime

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    made = _launches(monkeypatch, workspace)
    runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    cid = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "x"}], [])["id"]
    run_endpoint({"config": {"systems": [{"system": "1UAO"}]},
                  "conversation": {"id": cid, "study": None}}, runtime)
    again = run_endpoint({"config": {"systems": [{"system": "1UAO"}]},
                          "conversation": {"id": cid, "study": None}}, runtime)
    assert again["conversation"]["moved"] and again["conversation"]["study"] == made[1]


def _two_tabs(pw, session, replies):
    import json

    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    tabs = {}
    for name in replies:
        tab = browser.new_page(viewport={"width": 1400, "height": 900})
        tab.set_default_timeout(30000)

        def answers(said):
            return lambda route: route.fulfill(status=200, content_type="application/json",
                                               body=json.dumps(said.pop(0)))

        tab.route("**/api/agent/propose*", answers(replies[name]))
        tabs[name] = tab
    return browser, tabs


def _retry(page):
    page.locator("#agent-thread .agent-msg-user").last.hover()
    page.locator("#agent-thread .agent-msg-user").last.locator(
        _RETRY).click()


def _texts(workspace):
    threads = [e for e in _entries_everywhere(workspace).values() if e]
    return threads, [[e.get("text") for e in entries] for entries in threads]


def test_a_retry_with_its_save_waiting_is_kept_beside_another_tab(tmp_path, monkeypatch):
    """Found by the eighteenth review (10-08): tab B retried while its save
    waited on a slow disk; that save came back with tab A's entries, the
    redraw brought back what B had cut, and the question was kept twice."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    replies = {"a": [_answer("A0."), _answer("A ans.")],
               "b": [_answer("B ans."), _answer("B ans retried.")]}
    try:
        with sync_playwright() as pw:
            browser, tabs = _two_tabs(pw, session, replies)
            a, b = tabs["a"], tabs["b"]
            a.goto(session.url + "#agent", wait_until="domcontentloaded")
            a.wait_for_selector("#agent-request", state="visible")
            _say(a, "A first")
            a.wait_for_selector("#agent-thread .agent-answer:has-text('A0.')")
            a.wait_for_timeout(800)
            b.goto(session.url + "#agent", wait_until="domcontentloaded")
            b.wait_for_selector("#agent-thread .agent-answer:has-text('A0.')")
            _say(a, "A x")
            a.wait_for_selector("#agent-thread .agent-answer:has-text('A ans.')")
            a.wait_for_timeout(800)
            _slow_disk(monkeypatch, 2.0)
            _say(b, "B q")
            b.wait_for_selector("#agent-thread .agent-answer:has-text('B ans.')")
            _retry(b)
            b.wait_for_selector("#agent-thread .agent-answer:has-text('B ans retried.')")
            b.wait_for_timeout(9000)
            browser.close()
    finally:
        session.server.shutdown()
    threads, texts = _texts(workspace)
    assert len(threads) == 1, texts
    assert texts[0].count("B q") == 1 and "B ans." not in texts[0], texts
    assert "B ans retried." in texts[0] and "A ans." in texts[0]


def test_a_retry_then_a_reload_keeps_the_cut(tmp_path, monkeypatch):
    """Found by the eighteenth review (10-08): retried with saves waiting,
    then reloaded; the closing save only added, and the answer retried
    away came back."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    try:
        page, errors, close = _page_on(session, [_answer("A1."), _answer("A2."),
                                                 _answer("A2 again.")])
        _say(page, "q1")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('A1.')")
        page.wait_for_timeout(500)
        _slow_disk(monkeypatch, 2.0)
        _say(page, "q2")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('A2.')")
        _retry(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('A2 again.')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        page.wait_for_timeout(9000)
        close()
    finally:
        session.server.shutdown()
    threads, texts = _texts(workspace)
    assert "A2." not in [t for entries in texts for t in entries], texts


def test_new_then_a_reload_keeps_what_was_typed_in_one_thread(tmp_path, monkeypatch):
    """Found by the eighteenth review (10-08): New, a message, and a reload
    before /new was sent; the screen showed an empty conversation, and the
    message sat in a second one."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    try:
        page, errors, close = _page_on(session, [_answer("A1."), _answer("Fresh.")])
        _say(page, "first")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('A1.')")
        page.wait_for_timeout(500)
        _slow_disk(monkeypatch, 2.0)
        page.evaluate("() => window.FastMDXAgentPanel.current")  # settle
        page.locator("#agent-thread [data-feedback=useful]").last.click()
        page.click("#agent-new")
        _say(page, "in the new one")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Fresh.')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Fresh.')",
                               timeout=20000)
        close()
    finally:
        session.server.shutdown()
    threads, texts = _texts(workspace)
    assert sorted(texts) == sorted([["first", "A1."], ["in the new one", "Fresh."]]), texts


def test_a_confirmed_stop_is_said_in_its_own_thread(tmp_path, monkeypatch):
    """Found by the eighteenth review (10-08): Stop, "yes", then New while
    the stop was answered; the stop was written into the new thread."""
    import time

    from fastmdxplora.gui.exploration import DashboardRuntime

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)

    def stop(self):
        time.sleep(2.0)
        return {"stopped": True, "detail": "Workflow stopped.", "state": {}}

    monkeypatch.setattr(DashboardRuntime, "stop", stop)
    try:
        page, errors, close = _page_on(session, [
            {"ok": False, "action": "stop", "where": "", "attempts": []}])
        _say(page, "stop the run")
        page.wait_for_selector("#agent-thread .agent-confirm")
        _say(page, "yes")
        page.wait_for_timeout(300)
        page.click("#agent-new")
        page.wait_for_timeout(4000)
        close()
    finally:
        session.server.shutdown()
    threads, texts = _texts(workspace)
    stops = [entries for entries in threads
             if any(e.get("action") == "stop" for e in entries)]
    assert len(stops) == 1 and stops[0][0].get("text") == "stop the run", texts


def test_a_reply_being_written_survives_another_tab_s_change(tmp_path, monkeypatch):
    """Found by the seventeenth and eighteenth reviews (10-08): another
    tab's entries came back with a save and the thread was redrawn under a
    reply being written; the answer was kept and never shown."""
    import time

    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    from fastmdxplora.gui import agent_panel

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    answered = {"slow": _answer("Slow answer."), "fast": _answer("Fast.")}

    def model(payload, *args, **kwargs):
        if "slow" in str(payload.get("request")):
            time.sleep(3.0)
            return answered["slow"]
        return answered["fast"] if "tab A" in str(payload.get("request")) else _answer("Zero.")

    monkeypatch.setattr(agent_panel, "propose_endpoint", model)
    try:
        with sync_playwright() as pw:
            browser, tabs = _two_tabs(pw, session, {"a": [], "b": []})
            for tab in tabs.values():
                tab.unroute("**/api/agent/propose*")
            a, b = tabs["a"], tabs["b"]
            b.goto(session.url + "#agent", wait_until="domcontentloaded")
            b.wait_for_selector("#agent-request", state="visible")
            _say(b, "first")
            b.wait_for_selector("#agent-thread .agent-answer:has-text('Zero.')")
            b.wait_for_timeout(800)
            a.goto(session.url + "#agent", wait_until="domcontentloaded")
            a.wait_for_selector("#agent-thread .agent-answer:has-text('Zero.')")
            _say(b, "a slow one")
            b.wait_for_timeout(300)
            _say(a, "from tab A")
            a.wait_for_selector("#agent-thread .agent-answer:has-text('Fast.')")
            a.wait_for_timeout(500)
            b.locator("#agent-thread [data-feedback=useful]").first.click()
            b.wait_for_selector("#agent-thread .agent-answer:has-text('Slow answer.')",
                                timeout=15000)
            b.wait_for_selector("#agent-thread .agent-answer:has-text('Fast.')", timeout=15000)
            browser.close()
    finally:
        session.server.shutdown()


def test_a_retry_while_its_run_starts_keeps_the_run(tmp_path, monkeypatch):
    """Found by the eighteenth review (10-08): the run's entry, saved before
    the launch, was cut by a retry above it while the run started; the run
    ran, and the thread forgot it."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=2.0)
    try:
        page, errors, close = _page_on(session, [_answer("A1."), dict(_CONFIG),
                                                 _answer("A1 again.")])
        _say(page, "q1")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('A1.')")
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(400)
        page.locator("#agent-thread .agent-msg-user").first.hover()
        page.locator("#agent-thread .agent-msg-user").first.locator(
            _RETRY).click()
        page.wait_for_timeout(5000)
        close()
    finally:
        session.server.shutdown()
    threads = [e for e in _entries_everywhere(workspace).values() if e]
    said = [x for entries in threads for x in entries]
    assert [r.get("output") for r in _runs_in(said)] == made


def test_two_tabs_keep_one_summary_of_a_run(tmp_path, monkeypatch):
    """Found by the eighteenth review (10-08): both tabs asked for a run's
    summary, and both kept their own."""
    import json

    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    from fastmdxplora.gui import records_answer
    from tests.test_a_run_s_end_is_summarised_from_its_records import SUPPORTED, _ended

    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    # Each tab asks before the other's summary is kept (nineteenth review,
    # 10-08: answered at once, the second read the first's, and the test
    # passed without its fix).
    import time

    from fastmdxplora.gui import agent_panel

    real = agent_panel.run_summary_endpoint

    def slow(*args, **kwargs):
        time.sleep(1.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(agent_panel, "run_summary_endpoint", slow)
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    ran = _ended(workspace / "ran")
    cid = _older(workspace, [
        {"eid": "u", "role": "user", "text": "run it"},
        {"eid": "r", "role": "agent", "kind": "action", "action": "run", "where": "",
         "version": 1, "started": "2026-10-07T10:00:00Z", "output": str(ran)}])
    try:
        with sync_playwright() as pw:
            browser, tabs = _two_tabs(pw, session, {"a": [], "b": []})
            for tab in tabs.values():
                tab.goto(session.url + "#agent", wait_until="domcontentloaded")
            for tab in tabs.values():
                tab.wait_for_selector("#agent-thread .agent-summary")
            tabs["a"].wait_for_timeout(3000)
            browser.close()
    finally:
        session.server.shutdown()
    store = workspace / ".fastmdxplora_agent_conversations" / f"{cid}.json"
    kept = json.loads(store.read_text(encoding="utf-8"))["entries"]
    assert [e.get("kind") for e in kept].count("summary") == 1


def test_a_run_never_confirmed_is_not_said(tmp_path, monkeypatch):
    """Found by the sixteenth review (10-08): a reload while the run's entry
    was saved, before the run was asked for, kept the entry, said as asked
    and told to the Agent as done."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _older(workspace, [{"eid": "u", "role": "user", "text": "run it"},
                       {"eid": "r", "role": "agent", "kind": "action", "action": "run",
                        "where": "", "version": 1}])
    try:
        page, errors, close = _page_on(session, [])
        page.wait_for_selector("#agent-thread .agent-msg-user")
        page.wait_for_timeout(800)
        said = page.text_content("#agent-thread")
        close()
    finally:
        session.server.shutdown()
    assert "run" not in said.replace("run it", "").lower()


def test_a_closing_page_sends_only_what_was_not_kept(tmp_path, monkeypatch):
    """The save sent as a page closes carries only the entries the server
    has not confirmed (seventeenth review, 10-08: a whole thread over 64 KB
    is not sent by the browser)."""
    import json

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _older(workspace, [{"role": "user" if i % 2 == 0 else "agent", "kind": "answer",
                        "text": f"old {i} " + "x" * 1500} for i in range(60)])
    _slow_disk(monkeypatch, 3.0)
    try:
        page, errors, close = _page_on(session, [_answer("One.")])
        page.wait_for_selector("#agent-thread .agent-answer >> nth=0")
        _say(page, "msg 1")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('One.')")
        page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
        kept = page.evaluate("() => sessionStorage.getItem('fmx-agent-unsent')")
        close()
    finally:
        session.server.shutdown()
    bodies = json.loads(kept)
    assert len(json.dumps(bodies)) < 64 * 1024
    assert [e.get("text") for e in bodies[0]["entries"]] == ["msg 1", "One."]


def _model(monkeypatch, slow=(), seconds=3.0):
    """The AI model, on the server: "ans:" and the request, after
    ``seconds`` for a request holding one of ``slow``. The page's own
    replies are left alone."""
    import time

    from fastmdxplora.gui import agent_panel

    def model(payload, *args, **kwargs):
        asked = str(payload.get("request") or "")
        if any(word in asked for word in slow):
            time.sleep(seconds)
        return _answer("ans:" + asked)

    monkeypatch.setattr(agent_panel, "propose_endpoint", model)


def _model_page(session):
    page, errors, close = _page_on(session, [])
    page.unroute("**/api/agent/propose*")
    return page, errors, close


def _counting_saves(page):
    """From now on, the page counts the saves the server confirmed."""
    page.evaluate("""() => { window.__fmxSaved = 0;
        window.addEventListener('fmx:conversation-saved', () => { window.__fmxSaved += 1; }); }""")


def _saves_reach(page, count):
    page.wait_for_function("(n) => window.__fmxSaved >= n", arg=count)


def _answered_on_the_server(monkeypatch):
    """An event set once the AI model has answered, whether or not a page
    was there to hear it."""
    import threading

    from fastmdxplora.gui import agent_panel

    answered = threading.Event()
    model = agent_panel.propose_endpoint

    def told(*args, **kwargs):
        try:
            return model(*args, **kwargs)
        finally:
            answered.set()

    monkeypatch.setattr(agent_panel, "propose_endpoint", told)
    return answered


def _a_reload_while_answered(tmp_path, monkeypatch, hold=0.0, cut_first=False):
    """"first" answered and saved; "second" asked, its answer held until
    the reloaded page has shown it stopped; the page reloaded while it is
    written. ``hold``: each save saying it stopped (the closing page's and
    its copy's) reaches the server that much later. ``cut_first``: the
    answer's request fails before the page hears it is going. What is kept
    once every such save has been merged, and what the reloaded page shows."""
    import threading
    import time

    from fastmdxplora.gui import agent_panel

    merge = agent_panel.merge_conversation
    merges = {"in": 0, "out": 0}
    counting = threading.Lock()

    def late(runtime, entries, *args, **kwargs):
        stopping = any("Stopped before" in str(e.get("text", "")) for e in entries or []
                       if isinstance(e, dict))
        if stopping:
            with counting:
                merges["in"] += 1
        try:
            if hold and stopping:
                time.sleep(hold)
            return merge(runtime, entries, *args, **kwargs)
        finally:
            if stopping:
                with counting:
                    merges["out"] += 1

    # The closing page's save and its copy; with a route set, Playwright
    # drops the request a page sends as it goes, and only the copy comes.
    saying_stopped = 1 if cut_first else 2

    def merged_all():
        with counting:
            return merges["in"] >= saying_stopped and merges["in"] == merges["out"]

    monkeypatch.setattr(agent_panel, "merge_conversation", late)
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    release = threading.Event()

    def model(payload, *args, **kwargs):
        asked = str(payload.get("request") or "")
        if "second" in asked:
            release.wait(30)
        return _answer("ans:" + asked)

    monkeypatch.setattr(agent_panel, "propose_endpoint", model)
    answered = _answered_on_the_server(monkeypatch)
    try:
        page, errors, close = _model_page(session)
        _counting_saves(page)
        _say(page, "first")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:first')")
        _saves_reach(page, 1)
        answered.clear()
        if cut_first:
            page.route("**/api/agent/propose-stream", lambda route: route.abort())
        _say(page, "second")
        if cut_first:
            page.wait_for_selector("#agent-thread :text('The Agent did not answer')")
        else:
            page.wait_for_selector("#agent-propose.is-writing")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Stopped before')")
        release.set()
        if not cut_first:
            assert answered.wait(15)
        end = time.monotonic() + 20
        while not merged_all() and time.monotonic() < end:
            time.sleep(0.05)
        assert merged_all(), merges
        texts = _texts(workspace)[1]
        shown = page.text_content("#agent-thread")
        close()
    finally:
        release.set()
        session.server.shutdown()
    return texts, shown


def test_a_question_being_answered_is_kept_through_a_reload(tmp_path, monkeypatch):
    """Found by the nineteenth review (10-08): a question was saved only
    with its answer, and a reload while the answer was written lost it."""
    texts, shown = _a_reload_while_answered(tmp_path, monkeypatch)
    assert texts == [["first", "ans:first", "second", "Stopped before it finished."]], texts
    assert "second" in shown and "ans:second" not in shown


def test_a_closing_save_heard_late_is_kept_and_shown(tmp_path, monkeypatch):
    """CI run #704 (10-08): the closing page's save saying the answer
    stopped reached the server after the reloaded page had read the thread.
    Held 2 s here, it is still kept, and the reloaded page shows it (the
    copy it sends before reading carries the same)."""
    texts, shown = _a_reload_while_answered(tmp_path, monkeypatch, hold=2.0)
    assert texts == [["first", "ans:first", "second", "Stopped before it finished."]], texts
    assert "Stopped before it finished." in shown


def test_a_reply_cut_off_before_the_page_went_keeps_its_question_stopped(tmp_path, monkeypatch):
    """CI run #704 (10-08): the reload cut the answer's request off before
    the page heard it was going; the question was kept with nothing after
    it, and the reloaded page never said it stopped."""
    texts, shown = _a_reload_while_answered(tmp_path, monkeypatch, cut_first=True)
    assert texts == [["first", "ans:first", "second", "Stopped before it finished."]], texts
    assert "Stopped before it finished." in shown


def test_what_is_said_after_a_delete_elsewhere_is_kept_as_new(tmp_path, monkeypatch):
    """Found by the nineteenth review (10-08): another window deleted the
    conversation, and everything typed in it after was dropped without a
    word."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    try:
        page, errors, close = _model_page(session)
        _say(page, "before")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:before')")
        page.wait_for_timeout(800)
        # Deleted elsewhere: as another window does it.
        page.evaluate("""() => fetch('/api/agent/conversation/delete', {method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify(Object.assign({}, window.FastMDXAgentPanel.current))})""")
        _say(page, "after the delete")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:after the delete')")
        page.wait_for_selector("#agent-thread :text('deleted in another window')")
        _say(page, "and more")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:and more')")
        page.wait_for_timeout(1500)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:and more')")
        close()
    finally:
        session.server.shutdown()
    threads, texts = _texts(workspace)
    assert len(texts) == 1, texts
    assert texts[0][-5:] == ["after the delete", "ans:after the delete",
                             KEPT_AFTER_DELETE, "and more", "ans:and more"]
    assert errors == []


def test_a_thread_nobody_spoke_in_stays_deleted(tmp_path, monkeypatch):
    """A mark set on a deleted thread does not bring it back."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    try:
        page, errors, close = _model_page(session)
        _say(page, "before")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:before')")
        page.wait_for_timeout(800)
        page.evaluate("""() => fetch('/api/agent/conversation/delete', {method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify(Object.assign({}, window.FastMDXAgentPanel.current))})""")
        page.locator("#agent-thread [data-feedback=useful]").first.click()
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == []


def test_a_chat_of_no_study_is_made_in_chats_by_a_closing_page(tmp_path, monkeypatch):
    """Found by the nineteenth review (10-08): New chat of no study while a
    study was open, and a reload before the chat was made: it was made in
    the study's folder, and made current there."""
    import json
    import tempfile

    from fastmdxplora.gui.agent_panel import WORKSPACE_CONVERSATIONS_DIR
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    study = _write_study(tmp_path / "workspace" / "study")
    _model(monkeypatch)
    _slow_disk(monkeypatch, 2.0)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        page, errors, close = _model_page(session)
        page.wait_for_function("() => document.body.classList.contains('state-ready')")
        _say(page, "about the study")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:about the study')")
        page.evaluate("() => { window.FastMDXAgentPanel.fresh(null); }")
        _say(page, "in a chat")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:in a chat')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        chats = tmp_path / "workspace" / WORKSPACE_CONVERSATIONS_DIR

        def kept():
            return [[e.get("text") for e in json.loads(p.read_text(encoding="utf-8"))["entries"]]
                    for p in chats.glob("conv-*.json")]

        # Every save is two seconds, one at a time.
        _wait_for(lambda: ["in a chat", "ans:in a chat"] in kept(), 30.0)
        page.wait_for_timeout(3000)
        close()
    finally:
        session.server.shutdown()
    assert ["in a chat", "ans:in a chat"] in kept(), (kept(), _texts(tmp_path / "workspace")[1])
    assert ["in a chat", "ans:in a chat"] not in _texts(study)[1]


def test_an_unnamed_save_naming_a_place_is_made_there(tmp_path) -> None:
    """The server's half: a save carrying only its key, and a place."""
    from fastmdxplora.gui.agent_panel import (
        WORKSPACE_CONVERSATIONS_DIR,
        merge_conversation,
    )
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    study = _write_study(tmp_path / "study")
    runtime = SimpleNamespace(exploration_root=tmp_path, active_root=study)
    made = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "hi"}], [],
                              None, None, key="k-chat")
    assert made["ok"] and made["study"] is None
    assert (tmp_path / WORKSPACE_CONVERSATIONS_DIR / f"{made['id']}.json").is_file()


def test_a_mark_set_while_a_save_is_on_its_way_goes_with_a_closing_page(tmp_path,
                                                                       monkeypatch):
    """Found by the nineteenth review (10-08): an entry was confirmed as
    the page held it when the save came back, not as it went, so a mark
    set meanwhile was skipped by a page closing."""
    import json

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _slow_disk(monkeypatch, 2.0)
    try:
        page, errors, close = _page_on(session, [_answer("One.")])
        _say(page, "q")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('One.')")
        page.wait_for_timeout(300)
        page.locator("#agent-thread [data-feedback=useful]").first.click()
        page.wait_for_timeout(2700)  # the first save back, the mark's on its way
        page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
        kept = page.evaluate("() => sessionStorage.getItem('fmx-agent-unsent')")
        close()
    finally:
        session.server.shutdown()
    bodies = json.loads(kept or "[]")
    assert any(e.get("feedback") == "useful" for b in bodies for e in b["entries"]), bodies


def test_an_entry_held_twice_is_kept_once(tmp_path) -> None:
    """Found by the nineteenth review (10-08): a summary another tab kept
    was sent twice by a page that held it twice, and both were stored."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    runtime = SimpleNamespace(exploration_root=tmp_path, active_root=None)
    said = [{"eid": "u", "role": "user", "text": "run it"},
            {"eid": "sum-r", "role": "agent", "kind": "summary", "head": "Ended"}]
    cid = merge_conversation(runtime, said, [], None, key="k")["id"]
    again = merge_conversation(runtime, said + [said[1]], ["u", "sum-r"], cid, None)
    assert [e["eid"] for e in again["entries"]] == ["u", "sum-r"]


def test_an_answer_to_a_question_cut_while_written_is_not_kept(tmp_path, monkeypatch):
    """Found by the nineteenth review (10-08): Retry above a question
    whose answer was being written; the retry waited in the box, and the
    answer was saved with no question."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch, slow=("q2",), seconds=3.0)
    try:
        page, errors, close = _model_page(session)
        _say(page, "q1")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:q1')")
        _say(page, "q2")
        page.wait_for_timeout(600)
        page.locator("#agent-thread .agent-msg-user").first.hover()
        page.locator("#agent-thread .agent-msg-user").first.locator(
            _RETRY).click()
        page.wait_for_function(
            "() => document.querySelectorAll('#agent-thread .agent-answer').length === 1"
            " && document.querySelector('#agent-thread .agent-answer').textContent"
            ".includes('ans:q1')")
        page.wait_for_timeout(4000)
        shown = page.text_content("#agent-thread")
        box = page.input_value("#agent-request")
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == [["q1", "ans:q1"]]
    assert "q2" not in shown and box == ""


def test_a_retry_as_a_run_starts_then_no_answer_keeps_the_run(tmp_path, monkeypatch):
    """Found by the nineteenth review (10-08): Retry above a run as it
    started, then an answer the page could not read: the run ran, and the
    thread had no run."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)

    def lost(route):
        route.fetch()  # the server starts the run
        page.evaluate("""() => {
            const first = document.querySelector('#agent-thread .agent-msg-user');
            [...first.querySelectorAll('button')].find((b) => b.textContent === 'Retry'
                || b.getAttribute('aria-label') === 'Retry'
                || (b.title || '').startsWith('Send this again')).click();
        }""")
        page.wait_for_timeout(1500)
        route.fulfill(status=502, content_type="text/html", body="<h1>Bad gateway</h1>")

    try:
        page, errors, close = _page_on(session, [_answer("A1."), dict(_CONFIG),
                                                 _answer("A1 again.")])
        page.route("**/api/agent/run", lost)
        _say(page, "q1")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('A1.')")
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('A1 again.')")
        page.wait_for_timeout(3000)
        close()
    finally:
        session.server.shutdown()
    threads = [e for e in _entries_everywhere(workspace).values() if e]
    said = [x for entries in threads for x in entries]
    assert [r.get("output") for r in _runs_in(said)] == made


def test_a_conversation_moved_by_a_run_is_opened_where_it_went(tmp_path) -> None:
    """Found by the nineteenth review (10-08): a row drawn before a run
    took its conversation said "No such conversation."."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None,
                              switch_to=lambda folder: {"ok": True})
    cid = write_conversation(runtime, [{"role": "user", "text": "chignolin"}])["id"]
    run = workspace / "run1"
    run.mkdir()
    (run / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    assert attach_conversation(runtime, str(run), cid, None)["moved"]
    opened = open_conversation(runtime, cid, None)
    assert opened["ok"] and opened["study"] == str(run)
    assert _bare(opened["entries"]) == [{"role": "user", "text": "chignolin"}]


def test_a_reload_as_a_run_starts_says_nothing_of_its_answer(tmp_path, monkeypatch):
    """A reload as the run starts keeps the run, and nothing says it did not
    start (nineteenth review, 10-08). The old page's own late word is
    guarded by the test after this one."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=2.0)
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(700)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        page.wait_for_timeout(4000)
        close()
    finally:
        session.server.shutdown()
    threads = [e for e in _entries_everywhere(workspace).values() if e]
    said = [x for entries in threads for x in entries]
    assert [r.get("output") for r in _runs_in(said)] == made
    assert not any("could not read" in str(x.get("text")) for x in said), said


def test_a_launch_cut_off_by_the_page_going_says_nothing(tmp_path, monkeypatch):
    """The launch's request ended by the page going (a reload) is not said
    as an answer the page could not read (nineteenth review, 10-08)."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)

    def going(route):
        route.fetch()  # the server starts the run
        page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
        route.abort()

    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        page.route("**/api/agent/run", going)
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(2500)
        shown = page.text_content("#agent-thread")
        close()
    finally:
        session.server.shutdown()
    said = [x for entries in _entries_everywhere(workspace).values() for x in entries]
    assert [r.get("output") for r in _runs_in(said)] == made
    assert not any("could not read" in str(x.get("text")) for x in said), said
    assert "could not read" not in shown


def test_a_long_thread_is_not_drawn_again_by_each_save(tmp_path, monkeypatch):
    """Found by the nineteenth review (10-08): past the length kept, the
    oldest entries the server let go read as cut, and every save drew the
    thread again, wiping an edit in progress."""
    from fastmdxplora.gui.agent_panel import CONVERSATION_KEEP

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _older(workspace, [{"role": "user" if i % 2 == 0 else "agent", "kind": "answer",
                        "text": f"old {i}"} for i in range(CONVERSATION_KEEP)])
    _model(monkeypatch)
    try:
        page, errors, close = _model_page(session)
        page.wait_for_selector("#agent-thread .agent-msg-user")
        page.evaluate("() => { document.querySelector('#agent-thread').firstElementChild"
                      ".dataset.marked = 'yes'; }")
        _say(page, "one more")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:one more')")
        _say(page, "and another")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:and another')")
        page.wait_for_timeout(1500)
        marked = page.evaluate(
            "() => !!document.querySelector('#agent-thread [data-marked=yes]')")
        close()
    finally:
        session.server.shutdown()
    assert marked


def test_new_pressed_as_the_page_opens_keeps_the_new_thread(tmp_path, monkeypatch):
    """Found by the nineteenth review (10-08): New pressed before the
    thread was read, and the old one was drawn over the new."""
    import time

    from fastmdxplora.gui import agent_panel

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _older(workspace, [{"eid": "u", "role": "user", "text": "old q"},
                       {"eid": "a", "role": "agent", "kind": "answer", "text": "old a"}])
    real = agent_panel.read_conversation

    def slow(runtime):
        time.sleep(2.0)
        return real(runtime)

    monkeypatch.setattr(agent_panel, "read_conversation", slow)
    _model(monkeypatch)
    try:
        page, errors, close = _model_page(session)
        page.click("#agent-new")
        _say(page, "fresh")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:fresh')")
        page.wait_for_timeout(1500)
        shown = page.text_content("#agent-thread")
        close()
    finally:
        session.server.shutdown()
    assert "old q" not in shown
    assert sorted(_texts(workspace)[1]) == [["fresh", "ans:fresh"], ["old q", "old a"]]


def test_a_message_typed_before_the_thread_is_read_is_said_in_it(tmp_path, monkeypatch):
    """The first read waited for (seventeenth review, 10-08): a message
    typed as the page opened went into a second conversation."""
    import time

    from fastmdxplora.gui import agent_panel

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _older(workspace, [{"eid": "u", "role": "user", "text": "old q"},
                       {"eid": "a", "role": "agent", "kind": "answer", "text": "old a"}])
    real = agent_panel.read_conversation

    def slow(runtime):
        time.sleep(2.0)
        return real(runtime)

    monkeypatch.setattr(agent_panel, "read_conversation", slow)
    try:
        page, errors, close = _page_on(session, [_answer("New a.")])
        _say(page, "typed early")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('New a.')")
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == [["old q", "old a", "typed early", "New a."]]


def _delete_elsewhere(page):
    """The thread on screen deleted, as another window does it."""
    page.evaluate("""() => fetch('/api/agent/conversation/delete', {method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify(Object.assign({}, window.FastMDXAgentPanel.current))})""")


def test_words_after_a_mark_met_a_delete_are_kept(tmp_path, monkeypatch):
    """Found by the twentieth review (10-08): a mark's save met the delete
    first, nobody had spoken, and every message after was left unsaved."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    try:
        page, errors, close = _model_page(session)
        _say(page, "before")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:before')")
        page.wait_for_timeout(800)
        _delete_elsewhere(page)
        page.locator("#agent-thread [data-feedback=useful]").first.click()
        page.wait_for_timeout(800)
        _say(page, "after the mark")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:after the mark')")
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    texts = _texts(workspace)[1]
    assert len(texts) == 1 and texts[0][-3:] == [
        "after the mark", "ans:after the mark",
        KEPT_AFTER_DELETE], texts


def test_a_question_asked_after_a_delete_elsewhere_survives_a_reload(tmp_path, monkeypatch):
    """Found by the twentieth review (10-08): deleted elsewhere, then a
    question, then a reload while it was answered: the closing page's save
    named the deleted conversation, and was dropped."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch, slow=("slow",), seconds=4.0)
    try:
        page, errors, close = _model_page(session)
        _say(page, "before")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:before')")
        page.wait_for_timeout(800)
        _delete_elsewhere(page)
        _say(page, "a slow question")
        page.wait_for_timeout(800)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-thread :text('a slow question')")
        page.wait_for_timeout(4500)
        close()
    finally:
        session.server.shutdown()
    from fastmdxplora.gui.agent_panel import KEPT_AFTER_DELETE

    assert _texts(workspace)[1] == [["a slow question", "Stopped before it finished.",
                                     KEPT_AFTER_DELETE]]


def test_a_conversation_deleted_here_as_it_is_answered_stays_deleted(tmp_path, monkeypatch):
    """Found by the twentieth review (10-08): deleted from the list while
    its answer was written, it came back as a new conversation, said to be
    deleted "in another window"."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch, slow=("slow",), seconds=3.0)
    try:
        page, errors, close = _model_page(session)
        page.on("dialog", lambda dialog: dialog.accept())
        _say(page, "first")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:first')")
        page.wait_for_timeout(800)
        _say(page, "a slow one")
        page.wait_for_timeout(500)
        page.click("#agent-conversations")
        page.wait_for_selector("#agent-conv-list .agent-conv-row.current")
        page.hover("#agent-conv-list .agent-conv-row.current")
        page.click("#agent-conv-list .agent-conv-row.current .conv-delete")
        page.wait_for_timeout(4000)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == []


def test_another_tab_s_words_do_not_bring_a_deleted_thread_back(tmp_path, monkeypatch):
    """Found by the twentieth review (10-08): another tab's entries, taken
    in and never confirmed, counted as this person's words after a delete."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    try:
        with sync_playwright() as pw:
            browser, tabs = _two_tabs(pw, session, {"a": [], "b": []})
            a, b = tabs["a"], tabs["b"]
            for tab in (a, b):
                tab.unroute("**/api/agent/propose*")
            a.goto(session.url + "#agent", wait_until="domcontentloaded")
            a.wait_for_selector("#agent-request", state="visible")
            _say(a, "a1")
            a.wait_for_selector("#agent-thread .agent-answer:has-text('ans:a1')")
            a.wait_for_timeout(800)
            b.goto(session.url + "#agent", wait_until="domcontentloaded")
            b.wait_for_selector("#agent-thread .agent-answer:has-text('ans:a1')")
            _say(b, "b1")
            b.wait_for_selector("#agent-thread .agent-answer:has-text('ans:b1')")
            b.wait_for_timeout(800)
            a.locator("#agent-thread [data-feedback=useful]").first.click()
            a.wait_for_selector("#agent-thread .agent-answer:has-text('ans:b1')")
            _delete_elsewhere(b)
            a.wait_for_timeout(500)
            a.locator("#agent-thread [data-feedback=wrong]").first.click()
            a.wait_for_timeout(1500)
            browser.close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == []


def test_a_tab_closing_leaves_which_thread_is_current_alone(tmp_path, monkeypatch):
    """Found by the twentieth review (10-08): a tab's closing save made its
    thread the current one, and another tab's reload opened it. A thread
    with an id is sent as it closes without being made current."""
    import json

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    try:
        page, errors, close = _model_page(session)
        _say(page, "x1")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:x1')")
        page.wait_for_timeout(800)
        _slow_disk(monkeypatch, 2.0)
        page.locator("#agent-thread [data-feedback=useful]").first.click()
        page.wait_for_timeout(300)
        page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
        kept = page.evaluate("() => sessionStorage.getItem('fmx-agent-unsent')")
        close()
    finally:
        session.server.shutdown()
    bodies = json.loads(kept or "[]")
    assert bodies and bodies[0]["id"] and bodies[0]["current"] is False, bodies


def test_a_key_names_its_conversation_after_a_restart(tmp_path) -> None:
    """Found by the twentieth review (10-08): the keys were kept in memory
    only, and a closing page's save sent again after the GUI restarted
    made a second conversation."""
    from fastmdxplora.gui import agent_panel
    from fastmdxplora.gui.agent_panel import merge_conversation, new_conversation

    runtime = SimpleNamespace(exploration_root=tmp_path, active_root=None)
    said = [{"eid": "u", "role": "user", "text": "hi"}]
    first = merge_conversation(runtime, said, [], None, key="k-1", append=True)["id"]
    agent_panel._MADE_FOR.clear()
    again = merge_conversation(runtime, said, [], None, key="k-1", append=True)
    assert again["id"] == first and [e["eid"] for e in again["entries"]] == ["u"]
    made = new_conversation(runtime, key="k-2")["id"]
    agent_panel._MADE_FOR.clear()
    assert new_conversation(runtime, key="k-2")["id"] == made


def test_a_closing_page_s_words_are_kept_once_after_a_delete(tmp_path) -> None:
    """The server's half: a closing page's save naming a deleted
    conversation, with what the person said, is kept as a new one, once."""
    from fastmdxplora.gui.agent_panel import (
        KEPT_AFTER_DELETE,
        delete_conversation,
        merge_conversation,
    )

    runtime = SimpleNamespace(exploration_root=tmp_path, active_root=None)
    cid = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "a"}], [],
                             None, key="k")["id"]
    assert delete_conversation(runtime, cid, None)["ok"]
    late = [{"eid": "b", "role": "user", "text": "b"}]
    gone = merge_conversation(runtime, late, ["a", "b"], cid, None, append=True)
    assert gone.get("gone")
    kept = merge_conversation(runtime, late, ["a", "b"], cid, None, append=True, keep="k2")
    again = merge_conversation(runtime, late, ["a", "b"], cid, None, append=True, keep="k2")
    assert kept["ok"] and again["id"] == kept["id"] != cid
    assert [e["text"] for e in again["entries"]] == ["b", KEPT_AFTER_DELETE]


def test_an_entry_the_server_already_holds_is_drawn_once(tmp_path, monkeypatch):
    """Found by the twentieth review (10-08): a run's summary this page
    said while its save was on its way, which another tab had kept already,
    was drawn twice when the save brought that tab's entries back."""
    import json
    import threading
    import time
    import urllib.request

    from fastmdxplora.gui import agent_panel, records_answer
    from tests.test_a_run_s_end_is_summarised_from_its_records import SUPPORTED, _ended

    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    real = agent_panel.run_summary_endpoint

    def slow(*args, **kwargs):
        time.sleep(1.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(agent_panel, "run_summary_endpoint", slow)
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    ran = _ended(workspace / "ran")
    said = [{"eid": "u", "role": "user", "text": "run it"},
            {"eid": "r", "role": "agent", "kind": "action", "action": "run", "where": "",
             "version": 1, "started": "2026-10-07T10:00:00Z", "output": str(ran)}]
    cid = _older(workspace, said)
    other = said + [{"eid": "sum-r", "role": "agent", "kind": "summary", "study": str(ran),
                     "head": "Finished", "status": "completed"},
                    {"eid": "b1", "role": "user", "text": "from the other tab"},
                    {"eid": "b2", "role": "agent", "kind": "answer", "text": "ans:other"}]

    def other_tab():
        body = json.dumps({"id": cid, "study": None, "entries": other,
                           "seen": [e["eid"] for e in other], "current": False})
        urllib.request.urlopen(urllib.request.Request(
            session.url.rstrip("/") + "/api/agent/conversation", data=body.encode(),
            headers={"content-type": "application/json"}), timeout=30).read()

    try:
        page, errors, close = _page_on(session, [_answer("ans:a1")])
        page.wait_for_selector("#agent-thread .agent-msg-user")
        _slow_disk(monkeypatch, 2.0)
        threading.Thread(target=other_tab, daemon=True).start()
        page.wait_for_timeout(200)
        _say(page, "a1")
        page.wait_for_selector("#agent-thread :text('from the other tab')", timeout=20000)
        page.wait_for_timeout(1000)
        summaries = page.locator("#agent-thread .agent-summary").count()
        close()
    finally:
        session.server.shutdown()
    assert summaries == 1


def test_a_save_sent_again_after_a_delete_stays_deleted(tmp_path) -> None:
    """Found by the twenty-first review (10-08): a closing page's save, sent
    again on a later load, made anew a conversation deleted after it was
    first sent: by its key, and by its id with what it already held."""
    from fastmdxplora.gui import agent_panel
    from fastmdxplora.gui.agent_panel import delete_conversation, merge_conversation

    runtime = SimpleNamespace(exploration_root=tmp_path, active_root=None)
    said = [{"eid": "q", "role": "user", "text": "q"},
            {"eid": "s", "role": "agent", "kind": "answer", "text": "Stopped before it finished."}]
    made = merge_conversation(runtime, said, [], None, key="k", append=True)["id"]
    assert delete_conversation(runtime, made, None)["ok"]
    agent_panel._MADE_FOR.clear()
    again = merge_conversation(runtime, said, [], None, key="k", append=True)
    assert again.get("gone") and _texts(tmp_path)[1] == []
    # Named, with a key to keep it by: what it held was deleted with it.
    cid = merge_conversation(runtime, said, [], None, key="k2")["id"]
    assert delete_conversation(runtime, cid, None)["ok"]
    kept = merge_conversation(runtime, said, ["q", "s"], cid, None, append=True, keep="k3")
    assert kept.get("gone") and _texts(tmp_path)[1] == []


def test_a_long_thread_deleted_elsewhere_sends_only_what_is_new_as_it_closes(
        tmp_path, monkeypatch):
    """Found by the twenty-first review (10-08): a thread deleted elsewhere
    went whole as the page closed, too large to go, and the question asked
    since was lost."""
    import json

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _older(workspace, [{"role": "user", "text": f"old {i}"} if i % 2 == 0 else
                       {"role": "agent", "kind": "answer", "cites": [],
                        "text": f"old {i} " + "x" * 3000} for i in range(60)])
    _model(monkeypatch, slow=("slow",), seconds=4.0)
    try:
        page, errors, close = _model_page(session)
        page.wait_for_selector("#agent-thread .agent-msg-user")
        _delete_elsewhere(page)
        with page.expect_response(lambda r: r.url.endswith("/api/agent/conversation")
                                  and r.request.method == "POST"):
            page.locator("#agent-thread [data-feedback=useful]").first.click()
        _say(page, "a slow question")
        page.wait_for_selector("#agent-propose.is-writing")
        page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
        kept = page.evaluate("() => sessionStorage.getItem('fmx-agent-unsent')")
        close()
    finally:
        session.server.shutdown()
    bodies = json.loads(kept or "[]")
    assert len(bodies) == 1 and bodies[0]["id"] and bodies[0]["keep"]
    # What is new: the question and its stop. The answer marked went with
    # the deleted conversation, which the mark's save was told (`held`).
    sent = [e["text"].split(" x")[0] for e in bodies[0]["entries"]]
    assert sent == ["a slow question", "Stopped before it finished."], sent
    assert len(json.dumps(bodies)) < 64 * 1024


def test_a_page_kept_to_come_back_to_keeps_nothing_as_new(tmp_path, monkeypatch):
    """A page put in the back-forward cache keeps its thread: its closing
    save asks nothing to be kept anew (twenty-first review, 10-08). Its copy
    for the next load does: dropped from the cache, the page comes back as
    a fresh load, which sends it (twenty-second review, 10-08)."""
    import json

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch, slow=("slow",), seconds=4.0)
    try:
        page, errors, close = _model_page(session)
        _say(page, "before")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:before')")
        page.wait_for_timeout(800)
        sent = []
        page.on("request", lambda r: sent.append(r.post_data or "")
                if r.url.endswith("/api/agent/conversation") and r.method == "POST" else None)
        _say(page, "a slow question")
        page.wait_for_timeout(500)
        page.evaluate("() => window.dispatchEvent("
                      "new PageTransitionEvent('pagehide', {persisted: true}))")
        kept = page.evaluate("() => sessionStorage.getItem('fmx-agent-unsent')")
        page.wait_for_timeout(300)
        close()
    finally:
        session.server.shutdown()
    went = [json.loads(b) for b in sent if b]
    assert went and all("keep" not in b for b in went), went
    bodies = json.loads(kept or "[]")
    assert bodies and all(b.get("keep") for b in bodies), bodies
    assert [e["text"] for e in bodies[0]["entries"]] == ["a slow question"]


def test_a_thread_deleted_from_a_row_drawn_before_it_was_named_is_left(tmp_path, monkeypatch):
    """Found by the twenty-first review (10-08): the list was drawn while
    the thread's first save was on its way; deleting it left it on screen,
    and what was typed next was dropped."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    first = {"held": True}

    def late(route):
        if not first["held"] or route.request.method != "POST":
            route.continue_()
            return
        first["held"] = False
        answered = route.fetch()  # the server makes the conversation
        page.click("#agent-conversations")
        page.wait_for_selector("#agent-conv-list .agent-conv-row")
        route.fulfill(response=answered)

    try:
        page, errors, close = _model_page(session)
        page.on("dialog", lambda dialog: dialog.accept())
        page.route("**/api/agent/conversation", late)
        _say(page, "q")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:q')")
        page.wait_for_timeout(800)
        page.hover("#agent-conv-list .agent-conv-row")
        page.click("#agent-conv-list .agent-conv-row .conv-delete")
        page.wait_for_function(
            "() => !document.querySelector('#agent-thread .agent-msg-user')")
        _say(page, "after")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:after')")
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == [["after", "ans:after"]]


def test_a_run_started_after_a_delete_elsewhere_takes_its_thread(tmp_path, monkeypatch):
    """Found by the twenty-first review (10-08): Run pressed in a thread
    deleted elsewhere; the thread was kept anew, but the launch had asked
    before, with no conversation, and the run went unsaid."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(800)
        _delete_elsewhere(page)
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_selector("#agent-thread .agent-running")
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    moved = list((workspace / "run1").rglob("conv-*.json"))
    assert len(moved) == 1
    import json

    entries = json.loads(moved[0].read_text(encoding="utf-8"))["entries"]
    assert [r.get("output") for r in _runs_in(entries)] == made
    assert entries[0]["text"] == "chignolin"


def test_a_thread_in_a_run_outside_the_workspace_is_found_later(tmp_path) -> None:
    """Found by the twenty-first review (10-08): moved into a run's folder
    outside the workspace, a thread was taken for deleted once the GUI held
    another folder."""
    from fastmdxplora.gui.agent_panel import merge_conversation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SimpleNamespace(exploration_root=workspace, active_root=None)
    cid = write_conversation(runtime, [{"eid": "a", "role": "user", "text": "a"}])["id"]
    outside = tmp_path / "elsewhere" / "run1"
    outside.mkdir(parents=True)
    (outside / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    assert attach_conversation(runtime, str(outside), cid, None)["moved"]
    runtime.active_root = None
    later = merge_conversation(runtime, [{"eid": "a", "role": "user", "text": "a"},
                                         {"eid": "b", "role": "user", "text": "b"}],
                               ["a", "b"], cid, None)
    assert later["ok"] and later["study"] == str(outside)
    runtime.switch_to = lambda folder: {"ok": True}
    assert open_conversation(runtime, cid, None)["ok"]


def test_a_delete_answered_before_the_thread_was_named_leaves_it(tmp_path, monkeypatch):
    """Found by the twenty-second review (10-08): the delete answered
    before the thread's first save was; the thread stayed on screen, took
    the deleted id, and what was typed next was dropped."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    first = {"held": True}

    def late(route):
        if not first["held"] or route.request.method != "POST":
            route.continue_()
            return
        first["held"] = False
        answered = route.fetch()  # the server makes the conversation
        page.click("#agent-conversations")
        page.wait_for_selector("#agent-conv-list .agent-conv-row")
        page.hover("#agent-conv-list .agent-conv-row")
        page.click("#agent-conv-list .agent-conv-row .conv-delete")
        page.wait_for_timeout(1500)  # the delete answered first
        route.fulfill(response=answered)

    try:
        page, errors, close = _model_page(session)
        page.on("dialog", lambda dialog: dialog.accept())
        page.route("**/api/agent/conversation", late)
        _say(page, "q")
        page.wait_for_function(
            "() => !document.querySelector('#agent-thread .agent-msg-user')")
        _say(page, "after")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:after')")
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == [["after", "ans:after"]]


def test_new_for_a_deleted_key_stays_deleted(tmp_path) -> None:
    """Found by the twenty-second review (10-08): /new with a key whose
    conversation was deleted made a new one and pointed the key at it, and
    a closing page's save sent again landed there."""
    from fastmdxplora.gui import agent_panel
    from fastmdxplora.gui.agent_panel import (
        delete_conversation,
        merge_conversation,
        new_conversation,
    )

    runtime = SimpleNamespace(exploration_root=tmp_path, active_root=None)
    said = [{"eid": "q", "role": "user", "text": "q"}]
    made = merge_conversation(runtime, said, [], None, key="k", append=True)["id"]
    assert delete_conversation(runtime, made, None)["ok"]
    agent_panel._MADE_FOR.clear()
    assert new_conversation(runtime, key="k").get("gone")
    assert merge_conversation(runtime, said, [], None, key="k", append=True).get("gone")
    assert _texts(tmp_path)[1] == []


def test_a_run_then_another_study_opened_still_runs(tmp_path, monkeypatch):
    """Found by the twenty-second review (10-08): Run, then another study's
    conversation opened as the run's save was answered: the open reloaded
    the page while the run was asked for, and its answer went unheard. The
    page now reloads once the run has answered."""
    import json

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=1.5)
    other = workspace / "other"
    store = other / "agent" / "conversations"
    store.mkdir(parents=True)
    (other / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    (store / "conv-20261008-000000-000000.json").write_text(json.dumps(
        {"version": 3, "id": "conv-20261008-000000-000000",
         "entries": [{"eid": "o", "role": "user", "text": "about other"}]}), encoding="utf-8")
    events = []
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(800)
        page.on("requestfinished", lambda r: events.append("run answered")
                if r.url.endswith("/api/agent/run") else None)
        page.on("requestfailed", lambda r: events.append("run cut off")
                if r.url.endswith("/api/agent/run") else None)
        page.on("framenavigated", lambda f: events.append("reload")
                if f == page.main_frame else None)
        _slow_disk(monkeypatch, 1.0)
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.evaluate("(where) => window.FastMDXAgentPanel.open("
                      "'conv-20261008-000000-000000', where, false)", str(other))
        page.wait_for_timeout(7000)
        close()
    finally:
        session.server.shutdown()
    assert made and events[:2] == ["run answered", "reload"], events
    said = [x for entries in _entries_everywhere(workspace).values() for x in entries]
    assert [r.get("output") for r in _runs_in(said)] == made


def test_a_second_press_on_a_deleted_row_keeps_it_deleted(tmp_path, monkeypatch):
    """Found by the twenty-third review (10-08): the bin pressed twice, the
    second delete failed and cleared the page's mark, and a save on its way
    brought the deleted conversation back as a new one."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    once = []

    def held(route):
        if once or route.request.method != "POST" or \
                "second" not in (route.request.post_data or ""):
            route.continue_()
            return
        once.append(True)
        page.click("#agent-conversations")
        page.wait_for_selector("#agent-conv-list .agent-conv-row.current")
        page.hover("#agent-conv-list .agent-conv-row.current")
        page.evaluate("""() => {
            const bin = document.querySelector('#agent-conv-list .agent-conv-row.current .conv-delete');
            bin.click(); bin.click();
        }""")
        page.wait_for_timeout(1500)
        route.continue_()

    # A conversation from an earlier visit: no key of this page names it.
    _older(workspace, [{"eid": "u", "role": "user", "text": "first"},
                       {"eid": "a", "role": "agent", "kind": "answer", "text": "ans:first"}])
    try:
        page, errors, close = _model_page(session)
        page.on("dialog", lambda dialog: dialog.accept())
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:first')")
        page.route("**/api/agent/conversation", held)
        _say(page, "second")
        page.wait_for_timeout(4000)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == []


def test_words_typed_once_a_delete_is_answered_are_kept(tmp_path, monkeypatch):
    """Found by the twenty-third review (10-08): the thread on screen was
    reset only once the saves queued were answered, and what was typed
    meanwhile went to the deleted conversation and was dropped."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)

    def held(route):
        if route.request.method != "POST" or "second" not in (route.request.post_data or ""):
            route.continue_()
            return
        answered = route.fetch()
        page.click("#agent-conversations")
        page.wait_for_selector("#agent-conv-list .agent-conv-row.current")
        page.hover("#agent-conv-list .agent-conv-row.current")
        page.click("#agent-conv-list .agent-conv-row.current .conv-delete")
        page.wait_for_function(
            "() => !document.querySelector('#agent-thread .agent-msg-user')")
        _say(page, "after the delete")
        page.wait_for_timeout(800)
        route.fulfill(response=answered)

    try:
        page, errors, close = _model_page(session)
        page.on("dialog", lambda dialog: dialog.accept())
        _say(page, "first")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:first')")
        page.wait_for_timeout(800)
        page.route("**/api/agent/conversation", held)
        _say(page, "second")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:after the delete')")
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == [["after the delete", "ans:after the delete"]]


def test_a_cached_page_s_copy_used_elsewhere_keeps_the_words_once(tmp_path, monkeypatch):
    """Found by the twenty-third review (10-08): a cached page's copy, sent
    by another load in the same tab, kept the words; the page, restored,
    kept them again in a second conversation."""
    import json

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    once = []

    def held(route):
        if once or route.request.method != "POST" or \
                "after delete" not in (route.request.post_data or ""):
            route.continue_()
            return
        once.append(True)
        page.evaluate("() => window.dispatchEvent("
                      "new PageTransitionEvent('pagehide', {persisted: true}))")
        copy = json.loads(page.evaluate("() => sessionStorage.getItem('fmx-agent-unsent')"))
        for body in copy:  # another load in this tab sends it
            page.evaluate("""(body) => fetch('/api/agent/conversation', {method: 'POST',
                headers: {'content-type': 'application/json'}, body: JSON.stringify(body)})
                .then((r) => r.json())""", body)
        page.evaluate("() => { sessionStorage.removeItem('fmx-agent-unsent');"
                      " window.dispatchEvent(new PageTransitionEvent('pageshow',"
                      " {persisted: true})); }")
        route.continue_()

    try:
        page, errors, close = _model_page(session)
        _say(page, "before")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:before')")
        page.wait_for_timeout(800)
        _delete_elsewhere(page)
        page.route("**/api/agent/conversation", held)
        _say(page, "after delete")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:after delete')")
        page.wait_for_timeout(1000)
        _say(page, "later")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:later')")
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    texts = _texts(workspace)[1]
    assert sum("after delete" in t for t in texts) == 1, texts
    assert len(texts) == 1 and texts[0].count(KEPT_AFTER_DELETE) == 1, texts


def _hidden_and_shown(page, used=True, persisted=True):
    """The page put in the back-forward cache (or closed, not ``persisted``);
    its copy for the next load sent, as another load in the tab sends it,
    if ``used``; then the page restored."""
    import json

    page.evaluate("(p) => window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: p}))",
                  persisted)
    copy = json.loads(page.evaluate("() => sessionStorage.getItem('fmx-agent-unsent')") or "[]")
    for body in copy if used else ():
        page.evaluate("""(body) => fetch('/api/agent/conversation', {method: 'POST',
            headers: {'content-type': 'application/json'}, body: JSON.stringify(body)})
            .then((r) => r.json())""", body)
    if persisted:
        page.evaluate("() => { sessionStorage.removeItem('fmx-agent-unsent');"
                      " window.dispatchEvent(new PageTransitionEvent('pageshow',"
                      " {persisted: true})); }")
    return copy


def _a_cached_trip(tmp_path, monkeypatch, then):
    """Deleted elsewhere, then "after delete" said, and its save cut off as
    the page went into the cache; the copy used by another load; the page
    restored; then ``then(page)``. What is kept."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    once = []

    def held(route):
        if once or route.request.method != "POST" or \
                "after delete" not in (route.request.post_data or ""):
            route.continue_()
            return
        once.append(True)
        _hidden_and_shown(page)
        route.abort()  # the save on its way, cut off by the cache

    try:
        page, errors, close = _model_page(session)
        _say(page, "before")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:before')")
        page.wait_for_timeout(800)
        _delete_elsewhere(page)
        page.route("**/api/agent/conversation", held)
        _say(page, "after delete")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:after delete')")
        page.wait_for_timeout(1500)
        then(page)
        page.wait_for_timeout(2500)
        close()
    finally:
        session.server.shutdown()
    return _texts(workspace)[1]


@pytest.mark.parametrize("then", ["cached again, copy used", "cached again", "closed",
                                  "reloaded"])
def test_a_restored_page_keeps_the_words_once_however_it_goes_on(tmp_path, monkeypatch, then):
    """Found by the twenty-fourth review (10-08): each time the page went,
    its words were kept under a new key, so a page restored, then cached
    again, closed or reloaded, kept them a second time."""
    def going_on(page):
        if then == "reloaded":
            page.reload(wait_until="domcontentloaded")
        elif then == "closed":
            _hidden_and_shown(page, persisted=False)
        else:
            _hidden_and_shown(page, used=then.endswith("used"))

    texts = _a_cached_trip(tmp_path, monkeypatch, going_on)
    assert sum(t.count("after delete") for t in texts) == 1, texts
    assert sum(t.count(KEPT_AFTER_DELETE) for t in texts) == 1, texts


def test_a_kept_conversation_deleted_on_purpose_stays_deleted(tmp_path, monkeypatch):
    """Found by the twenty-fourth review (10-08): the words a cached page's
    copy kept as new were deleted by the person; the page, restored, kept
    them again, twice."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    once = []

    def held(route):
        if once or route.request.method != "POST" or \
                "after delete" not in (route.request.post_data or ""):
            route.continue_()
            return
        once.append(True)
        page.evaluate("() => window.dispatchEvent("
                      "new PageTransitionEvent('pagehide', {persisted: true}))")
        copy = page.evaluate("() => JSON.parse(sessionStorage.getItem('fmx-agent-unsent'))")
        for body in copy:  # the next load keeps the words, and they are deleted there
            page.evaluate("""(body) => fetch('/api/agent/conversation', {method: 'POST',
                headers: {'content-type': 'application/json'}, body: JSON.stringify(body)})
                .then((r) => r.json()).then((d) => fetch('/api/agent/conversation/delete',
                {method: 'POST', headers: {'content-type': 'application/json'},
                 body: JSON.stringify({id: d.id})}))""", body)
        page.evaluate("() => { sessionStorage.removeItem('fmx-agent-unsent');"
                      " window.dispatchEvent(new PageTransitionEvent('pageshow',"
                      " {persisted: true})); }")
        route.continue_()  # back on the page: its save answered

    try:
        page, errors, close = _model_page(session)
        _say(page, "before")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:before')")
        page.wait_for_timeout(800)
        _delete_elsewhere(page)
        page.route("**/api/agent/conversation", held)
        _say(page, "after delete")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:after delete')")
        page.wait_for_timeout(2500)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == []


def test_a_row_opened_while_its_delete_waits_leaves_it(tmp_path, monkeypatch):
    """Found by the twenty-fourth review (10-08): a conversation opened again
    while its delete was on its way stayed on screen, deleted, and what was
    typed next was lost."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    waiting = []
    _older(workspace, [{"eid": "u", "role": "user", "text": "first"},
                       {"eid": "a", "role": "agent", "kind": "answer", "text": "ans:first"}])
    try:
        page, errors, close = _model_page(session)
        page.on("dialog", lambda dialog: dialog.accept())
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:first')")
        page.click("#agent-new")
        page.wait_for_timeout(800)
        page.route("**/api/agent/conversation/delete", lambda route: waiting.append(route))
        page.click("#agent-conversations")
        row = page.locator("#agent-conv-list .agent-conv-row", has_text="first").first
        row.hover()
        row.locator(".conv-delete").click()
        page.wait_for_timeout(300)
        row.locator(".title").click()
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:first')")
        page.wait_for_timeout(500)
        waiting[0].continue_()
        page.wait_for_function(
            "() => !document.querySelector('#agent-thread .agent-answer')")
        _say(page, "after")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:after')")
        page.wait_for_timeout(1500)
        close()
    finally:
        session.server.shutdown()
    assert [t for t in _texts(workspace)[1] if t] == [["after", "ans:after"]]


def test_a_second_press_whose_failure_is_heard_first_keeps_it_deleted(tmp_path, monkeypatch):
    """Found by the twenty-fourth review (10-08): the bin pressed twice, and
    the second delete's failure heard before the first's answer cleared the
    page's mark; a save on its way brought the conversation back."""
    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch)
    dialogs, first, once = [], {}, []

    def on_dialog(dialog):
        dialogs.append(dialog.message)
        dialog.accept()

    def deletes(route):
        if first:
            route.continue_()  # the second press: fails, heard first
            return
        first["answer"] = route.fetch()  # deleted on the server, answer held
        first["route"] = route

    def held(route):
        if once or route.request.method != "POST" or \
                "second" not in (route.request.post_data or ""):
            route.continue_()
            return
        once.append(True)
        page.click("#agent-conversations")
        page.wait_for_selector("#agent-conv-list .agent-conv-row.current")
        page.hover("#agent-conv-list .agent-conv-row.current")
        page.evaluate("""() => {
            const bin = document.querySelector('#agent-conv-list .agent-conv-row.current .conv-delete');
            bin.click(); bin.click();
        }""")
        page.wait_for_timeout(1500)
        first["route"].fulfill(response=first["answer"])
        page.wait_for_timeout(1000)
        route.continue_()

    _older(workspace, [{"eid": "u", "role": "user", "text": "first"},
                       {"eid": "a", "role": "agent", "kind": "answer", "text": "ans:first"}])
    try:
        page, errors, close = _model_page(session)
        page.on("dialog", on_dialog)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('ans:first')")
        page.route("**/api/agent/conversation/delete", deletes)
        page.route("**/api/agent/conversation", held)
        _say(page, "second")
        page.wait_for_timeout(4000)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == []
    assert not [m for m in dialogs if "No such" in m], dialogs


def test_a_thread_whose_new_was_answered_gone_keeps_a_question_as_it_closes(
        tmp_path, monkeypatch):
    """Found by the twenty-fourth review (10-08): /new answered gone (its
    key's conversation made by a closing copy and deleted), and a question
    asked there was lost as the page closed while it was answered."""
    import json

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.0)
    _model(monkeypatch, slow=("slow",), seconds=4.0)

    def made_and_deleted(route):
        key = json.loads(route.request.post_data or "{}").get("key")
        page.evaluate("""(key) => fetch('/api/agent/conversation', {method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify({entries: [{eid: 'copy', role: 'user', text: 'kept by a copy'}],
                                  seen: [], key: key, append: true})})
            .then((r) => r.json()).then((d) => fetch('/api/agent/conversation/delete',
            {method: 'POST', headers: {'content-type': 'application/json'},
             body: JSON.stringify({id: d.id})}))""", key)
        route.continue_()

    try:
        page, errors, close = _model_page(session)
        page.wait_for_timeout(800)
        page.route("**/api/agent/conversation/new", made_and_deleted)
        page.click("#agent-new")
        page.wait_for_timeout(1000)
        _say(page, "a slow question")
        page.wait_for_timeout(800)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(4500)
        close()
    finally:
        session.server.shutdown()
    assert _texts(workspace)[1] == [["a slow question", "Stopped before it finished.",
                                     KEPT_AFTER_DELETE]]
