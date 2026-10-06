"""Chats about no study are kept apart from a study's conversations.

Asked for (10-06): after the active study in the sidebar, Chats, the
conversations that belong to no study; a study's conversations under it;
the Agent's Conversations better kept. A chat begun with no study, or as
one while a study is open, is kept in the workspace and stays a chat as
it goes on; a study's conversation stays its study's when another study
is opened. Each can be named; the list gives the one talked in last first.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from fastmdxplora.gui.agent_panel import (
    CONVERSATIONS_SUBDIR,
    WORKSPACE_CONVERSATIONS_DIR,
    list_conversations,
    new_conversation,
    open_conversation,
    rename_conversation,
    write_conversation,
)


class _Runtime:
    def __init__(self, workspace: Path) -> None:
        self.exploration_root = workspace
        self.active_root = None

    def switch_to(self, path):
        self.active_root = Path(path)
        return {"ok": True}


def _study(where: Path, system: str) -> Path:
    (where / "simulation").mkdir(parents=True)
    (where / "manifest.json").write_text(json.dumps({"system": system}), encoding="utf-8")
    return where


@pytest.fixture
def place(tmp_path):
    runtime = _Runtime(tmp_path)
    return runtime, _study(tmp_path / "trp", "1l2y"), _study(tmp_path / "lig", "/x/ben_trypsin.pdb")


def test_a_chat_begun_while_a_study_is_open_stays_a_chat(place) -> None:
    runtime, trp, _ = place
    runtime.active_root = trp
    begun = new_conversation(runtime, None)
    assert begun["study"] is None
    said = [{"role": "user", "text": "Which force field for an IDP?"}]
    saved = write_conversation(runtime, said, begun["id"], None)
    assert saved["ok"] and saved["study"] is None
    assert (trp.parent / WORKSPACE_CONVERSATIONS_DIR / f"{begun['id']}.json").is_file()
    assert not (trp / CONVERSATIONS_SUBDIR / f"{begun['id']}.json").exists()
    # Opened again with the study still open, it is still the chat.
    opened = open_conversation(runtime, begun["id"], None)
    assert opened["ok"] and opened["study"] is None and runtime.active_root == trp


def test_a_study_s_conversation_stays_its_own_when_another_is_open(place) -> None:
    runtime, trp, lig = place
    runtime.active_root = trp
    begun = new_conversation(runtime)
    runtime.active_root = lig
    saved = write_conversation(runtime, [{"role": "user", "text": "about trp"}],
                               begun["id"], str(trp))
    assert saved["study"] == str(trp)
    assert (trp / CONVERSATIONS_SUBDIR / f"{begun['id']}.json").is_file()
    assert not (lig / CONVERSATIONS_SUBDIR).exists()


def test_a_name_is_kept_as_it_goes_on_and_given_back(place) -> None:
    runtime, trp, _ = place
    runtime.active_root = trp
    begun = new_conversation(runtime)
    write_conversation(runtime, [{"role": "user", "text": "Why is the RMSD rising?"}],
                       begun["id"], str(trp))
    assert rename_conversation(runtime, begun["id"], str(trp), "  RMSD   drift ")["title"] \
        == "RMSD drift"
    write_conversation(runtime, [{"role": "user", "text": "Why is the RMSD rising?"},
                                 {"role": "agent", "kind": "answer", "text": "It levels."}],
                       begun["id"], str(trp))
    row = list_conversations(runtime)["groups"][0]["conversations"][0]
    assert (row["title"], row["named"], row["entries"]) == ("RMSD drift", True, 2)
    assert rename_conversation(runtime, begun["id"], str(trp), "")["title"] \
        == "Why is the RMSD rising?"
    refused = rename_conversation(runtime, begun["id"], str(trp), "x" * 81)
    assert not refused["ok"] and "80" in refused["error"]
    assert not rename_conversation(runtime, "../x", str(trp), "a")["ok"]
    assert not rename_conversation(runtime, begun["id"], str(trp.parent / "none"), "a")["ok"]


def test_the_list_is_this_study_others_by_last_talk_then_chats(place) -> None:
    runtime, trp, lig = place
    for study, text in ((trp, "trp first"), (lig, "lig"), (trp, "trp second")):
        runtime.active_root = study
        begun = new_conversation(runtime)
        write_conversation(runtime, [{"role": "user", "text": text}], begun["id"], str(study))
        time.sleep(0.01)
    chat = new_conversation(runtime, None)
    write_conversation(runtime, [{"role": "user", "text": "a chat"}], chat["id"], None)
    # The first trp conversation talked in again, last.
    first = sorted((trp / CONVERSATIONS_SUBDIR).glob("conv-*.json"))[0]
    later = time.time() + 5
    os.utime(first, (later, later))
    runtime.active_root = lig
    groups = list_conversations(runtime)["groups"]
    assert [(g["label"], g["loaded"], bool(g.get("chats"))) for g in groups] == [
        ("BENT", True, False), ("1L2Y", False, False), ("Chats", False, True)]
    assert groups[1]["folder"] == "trp"
    assert [c["title"] for c in groups[1]["conversations"]] == ["trp first", "trp second"]
    assert [c["title"] for c in groups[2]["conversations"]] == ["a chat"]
    assert groups[2]["study"] is None


def test_the_sidebar_lists_them_and_opens_one_beside_the_page(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    study = _write_study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    post = """async ([url, body]) => (await fetch(url, {method: 'POST',
        headers: {'content-type': 'application/json'}, body: JSON.stringify(body)})).json()"""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXAgentPanel && window.FastMDXChats"
                                   " && document.body.classList.contains('state-ready')")
            run = page.evaluate("() => window.FastMDXDashboard.state.appState.active_run")
            for where, text in ((None, "A chat about nothing open"), (run, "About this study")):
                begun = page.evaluate(post, ["/api/agent/conversation/new", {"study": where}])
                page.evaluate(post, ["/api/agent/conversation", {
                    "id": begun["id"], "study": where, "entries": [{"role": "user", "text": text}]}])
            page.evaluate("() => window.FastMDXChats.show()")
            page.wait_for_selector("#sidebar-chats-list .sidebar-conv")
            lists = page.evaluate("""() => ({
                chats: [...document.querySelectorAll('#sidebar-chats-list .sidebar-conv-title')]
                    .map((t) => t.textContent),
                study: [...document.querySelectorAll('#sidebar-study-convs-list .sidebar-conv-title')]
                    .map((t) => t.textContent),
                after: document.getElementById('sidebar-chats').compareDocumentPosition(
                    document.querySelector('.sidebar-study')) & Node.DOCUMENT_POSITION_PRECEDING})""")
            page.click("#sidebar-chats-list .sidebar-conv")
            page.wait_for_function("() => !document.getElementById('agent-drawer').hidden")
            page.wait_for_selector("#agent-thread :text('A chat about nothing open')")
            current = page.evaluate("() => window.FastMDXAgentPanel.current")
            marked = page.get_attribute("#sidebar-chats-list .sidebar-conv", "aria-current")
            # The Conversations list: found by typing, named in place.
            page.click("#agent-conversations")
            page.wait_for_selector("#agent-conv-list .conv-search input")
            heads = page.eval_on_selector_all("#agent-conv-list .agent-conv-group",
                                              "(all) => all.map((h) => h.textContent)")
            page.fill("#agent-conv-list .conv-search input", "nothing")
            found = page.eval_on_selector_all("#agent-conv-list .agent-conv-row .title",
                                              "(all) => all.map((t) => t.textContent)")
            page.hover("#agent-conv-list .agent-conv-row")
            page.click("#agent-conv-list .agent-conv-row .conv-rename")
            page.fill("#agent-conv-list .conv-name", "Force fields")
            page.keyboard.press("Enter")
            page.wait_for_selector("#sidebar-chats-list .sidebar-conv-title:text-is('Force fields')")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    # Chats come after the study's block (DOCUMENT_POSITION_PRECEDING, 2).
    assert lists == {"chats": ["A chat about nothing open"], "study": ["About this study"],
                     "after": 2}
    assert current["study"] is None and marked == "true"
    assert heads[0].startswith("This study") and heads[1] == "Chats · no study"
    assert found == ["A chat about nothing open"]
