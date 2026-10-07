"""What the AI model was sent for a reply, kept; and the Agent told what the
page shows, through a tool that reads the study's own records.

From the context-aware Agent of Prince Otegbulu (#51) and Derrick Kwan
(#52). A receipt records each time the AI model is asked: in the text
protocol the prompt, in the tool-call protocol the system prompt (kept once),
the tools declared and the messages it had not been given before. It is
kept beside the conversation, named by its SHA-256, its system prompts once
each, and read back checked against that digest.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastmdxplora.agent.propose import propose_config
from fastmdxplora.agent.receipt import digest_of
from fastmdxplora.agent.tools import Toolbox, current_view_tool
from fastmdxplora.agent.turns import ToolCall, Turn, Usage


def _model(*replies):
    given = iter(replies)
    prompts = []

    def complete(prompt):
        prompts.append(prompt)
        return next(given)

    return complete, prompts


def _turns(*replies):
    """A completion that replies by tool calls, every request kept."""
    given = list(replies)
    asked = []

    def complete(prompt):
        raise AssertionError("asked in text")

    def turn(system, messages, tools):
        asked.append({"system": system, "messages": [dict(m) for m in messages],
                      "tools": [t.name for t in tools]})
        return given.pop(0)

    complete.turn = turn
    return complete, asked


def _said(*calls, text=""):
    return Turn(text, tuple(calls), Usage(calls=1, input_tokens=100, output_tokens=10))


def _study(root):
    (root / "analysis").mkdir(parents=True)
    return root


def test_a_text_reply_keeps_each_prompt_as_it_went(tmp_path):
    study = _study(tmp_path / "study")
    complete, prompts = _model(
        "USE: current_view",
        "SAY: The active study has no recorded findings yet.",
    )
    proposal = propose_config("What is in the active view?", complete,
                              tools=Toolbox(extra=(current_view_tool({"study": str(study)}),)))
    receipt = proposal.receipt.as_record()

    assert [entry["prompt"] for entry in receipt["sent"]] == prompts
    assert "read_study" in prompts[1]
    assert receipt["sha256"] == digest_of(receipt) and len(receipt["sha256"]) == 64


def test_a_reply_by_tool_calls_keeps_the_system_once_and_each_turns_new_messages(tmp_path):
    study = _study(tmp_path / "study")
    complete, asked = _turns(
        _said(ToolCall("call_1", "current_view", {})),
        _said(text="The study has no recorded findings yet."))
    proposal = propose_config("What is in the active view?", complete,
                              tools=Toolbox(extra=(current_view_tool({"study": str(study)}),)))
    receipt = proposal.receipt.as_record()

    assert proposal.protocol == "tools" and proposal.answer.startswith("The study")
    assert receipt["systems"] == [asked[0]["system"]]
    first, second = receipt["sent"]
    assert first["system"] == second["system"] == 0
    assert first["tools"] == second["tools"] == 0
    declared = {tool["name"]: tool for tool in receipt["toolsets"][0]}
    assert {"current_view", "propose_config"} <= set(declared)
    assert declared["current_view"]["parameters"] == {
        "type": "object", "properties": {}, "additionalProperties": False}
    assert [m["role"] for m in first["messages"]] == ["user"]
    assert first["messages"][0]["text"] == asked[0]["messages"][-1]["text"]
    # The second turn: the AI model's call, and what the look gave back.
    assert [m["role"] for m in second["messages"]] == ["assistant", "results"]
    assert second["messages"][0]["calls"] == [
        {"id": "call_1", "name": "current_view", "arguments": {}}]
    assert "read_study" in second["messages"][1]["results"][0]["content"]
    assert len(asked[1]["messages"]) == 3


def test_a_reply_names_what_was_sent_and_it_is_kept(tmp_path, monkeypatch):
    from fastmdxplora.gui.agent_panel import (
        RECEIPT_SYSTEMS_DIR,
        propose_endpoint,
        receipt_endpoint,
    )

    study = _study(tmp_path / "study")
    runtime = SimpleNamespace(active_root=study, exploration_root=tmp_path)
    complete, asked = _turns(_said(text="No active study findings."))
    import fastmdxplora.agent as agent

    monkeypatch.setattr(agent, "completion_for", lambda **_: complete)
    answer = propose_endpoint({"request": "Summarize this study."}, runtime)

    # The reply says it in brief; what was sent is not sent back with it.
    brief = answer["context_receipt"]
    assert brief == {"sha256": brief["sha256"], "asked": 1, "looks": 0,
                     "truncated": False, "kept": True}
    store = study / "agent" / "conversations" / "receipts"
    on_disk = json.loads((store / f"{brief['sha256']}.json").read_text())
    # The system prompt is kept once, by its digest, not in every receipt.
    assert on_disk["systems"] == [{"sha256": on_disk["systems"][0]["sha256"]}]
    assert (store / RECEIPT_SYSTEMS_DIR / f"{on_disk['systems'][0]['sha256']}.txt").read_text() \
        == asked[0]["system"]
    kept = receipt_endpoint(runtime, brief["sha256"])
    assert kept["ok"] and kept["receipt"]["systems"] == [asked[0]["system"]]
    assert kept["receipt"]["sha256"] == brief["sha256"] == digest_of(kept["receipt"])
    for wrong in ("", "../x", "A" * 64, "0" * 63, None):
        assert receipt_endpoint(runtime, wrong)["ok"] is False
    # A receipt changed on disk is said to have changed, not shown as sent.
    on_disk["sent"][0]["messages"][0]["text"] = "Something else."
    (store / f"{brief['sha256']}.json").write_text(json.dumps(on_disk))
    assert "changed" in receipt_endpoint(runtime, brief["sha256"])["error"]


def test_the_newest_receipts_are_kept_and_the_systems_they_name(tmp_path, monkeypatch):
    import os

    import fastmdxplora.gui.agent_panel as panel

    study = _study(tmp_path / "study")
    runtime = SimpleNamespace(active_root=study, exploration_root=tmp_path)
    monkeypatch.setattr(panel, "MOST_RECEIPTS", 3)
    monkeypatch.setattr(panel, "MOST_UNNAMED_SYSTEMS", 1)
    store = panel._receipts_of(runtime)
    digests = []
    for n in range(5):
        record = {"version": 3, "systems": [f"system {n // 2}"], "toolsets": [],
                  "sent": [{"prompt": f"p{n}"}], "truncated": False}
        record["sha256"] = digest_of(record)
        brief = panel._kept_receipt(store, record)
        digests.append(brief["sha256"])
        os.utime(store / f"{brief['sha256']}.json", ns=(n * 10**9, n * 10**9))
        for text in (store / panel.RECEIPT_SYSTEMS_DIR).glob("*.txt"):
            os.utime(text, ns=(n * 10**9, n * 10**9))  # none written this minute
    assert sorted(p.stem for p in store.glob("*.json")) == sorted(digests[-3:])
    # "system 0" was named only by the two receipts let go.
    assert len(list((store / panel.RECEIPT_SYSTEMS_DIR).glob("*.txt"))) == 2
    assert panel.receipt_endpoint(runtime, digests[0])["error"].endswith("(the newest 3 are).")
    assert panel.receipt_endpoint(runtime, digests[-1])["receipt"]["systems"] == ["system 2"]


def test_a_view_that_is_not_the_active_study_is_refused(tmp_path, monkeypatch):
    from fastmdxplora.gui.agent_panel import propose_endpoint

    study = _study(tmp_path / "study")
    other = _study(tmp_path / "other")
    runtime = SimpleNamespace(active_root=study, exploration_root=tmp_path)
    called = []
    import fastmdxplora.agent as agent

    monkeypatch.setattr(agent, "completion_for", lambda **_: called.append(True))
    answer = propose_endpoint({"request": "Read this view.",
                               "current_view": {"study": str(other)}}, runtime)

    assert not answer["ok"] and answer["code"] == "agent.view.changed"
    assert "current view changed" in answer["error"].lower()
    assert called == []


def test_a_receipt_stays_with_the_study_it_was_asked_about(tmp_path, monkeypatch):
    """Another study opened while the AI model answers: the prompts about
    the first are kept with the first, never written into the second, and
    the reply is not offered."""
    from fastmdxplora.gui.agent_panel import propose_endpoint

    first = _study(tmp_path / "first")
    second = _study(tmp_path / "second")
    runtime = SimpleNamespace(active_root=first, exploration_root=tmp_path)

    def complete(prompt):
        runtime.active_root = second
        return "SAY: Nothing recorded yet."
    import fastmdxplora.agent as agent

    monkeypatch.setattr(agent, "completion_for", lambda **_: complete)
    answer = propose_endpoint({"request": "About the first study.",
                               "current_view": {"study": str(first)}}, runtime)

    assert answer["code"] == "agent.view.changed"
    digest = answer["context_receipt"]["sha256"]
    receipts = Path("agent") / "conversations" / "receipts" / f"{digest}.json"
    assert (first / receipts).is_file()
    assert not (second / "agent").exists()


def test_the_page_s_view_is_read_not_one_the_ai_model_writes(tmp_path):
    study = _study(tmp_path / "study")
    other = _study(tmp_path / "other")
    box = Toolbox(extra=(current_view_tool({"study": str(study)}, active_root=study),))

    look = box.use("current_view", {"hints": {"study": str(other)}})

    assert look.ok and str(study) in look.said and str(other) not in look.said
    # One look, as one was asked for: the record it read is part of it.
    assert [item.tool for item in box.looks] == ["current_view"]


def test_the_view_s_hints_are_bounded(tmp_path):
    too_large = Toolbox(extra=(current_view_tool({"selection": "x" * 5000}),))
    look = too_large.use("current_view", {})
    assert not look.ok and "bounded" in look.said
    unread = Toolbox(extra=(current_view_tool({"selection": ["A184"]}),))
    assert not unread.use("current_view", {}).ok
    nothing = Toolbox(extra=(current_view_tool(None),)).use("current_view", {})
    assert nothing.ok and "no bounded hints" in nothing.said


def test_the_residues_selected_reach_the_ai_model_as_the_page_named_them(tmp_path):
    study = _study(tmp_path / "study")
    box = Toolbox(extra=(current_view_tool({
        "study": str(study), "frame": 4,
        "selection": [{"chain": "A", "resi": 184, "icode": "A", "resn": "GLY", "x": "dropped"},
                      {"chain": "A", "resi": 189, "icode": "", "resn": "ASP"}]}),))
    look = box.use("current_view", {})
    assert look.ok
    assert ('"selection": [{"chain": "A", "icode": "A", "resi": 184, "resn": "GLY"}, '
            '{"chain": "A", "icode": "", "resi": 189, "resn": "ASP"}]') in look.said
    assert "dropped" not in look.said


def test_a_receipt_says_what_it_cut_and_keeps_numbers_as_numbers():
    from fastmdxplora.agent.receipt import MAX_RECEIPT_ITEMS, ReceiptBuilder

    deep = {"a": {"b": {"c": {"d": {"e": 5, "f": 1.5, "g": None, "h": True}}}}}
    kept = ReceiptBuilder()
    kept.turn("system", [{"role": "assistant", "text": "",
                          "calls": [ToolCall("c1", "propose_config", {"config": deep})]}], [])
    record = kept.freeze().as_record()
    assert record["sent"][0]["messages"][0]["calls"][0]["arguments"] == {"config": deep}
    assert record["truncated"] is False

    many = ReceiptBuilder()
    many.turn("system", [{"role": "assistant", "text": "", "calls": [ToolCall(
        "c1", "propose_config", {str(n): n for n in range(MAX_RECEIPT_ITEMS + 1)})]}], [])
    assert many.freeze().as_record()["truncated"] is True


def test_a_system_prompt_is_read_back_as_it_was_kept(tmp_path):
    """Line endings included: a description written on another system keeps
    its carriage returns, and the receipt still holds its digest."""
    import fastmdxplora.gui.agent_panel as panel

    study = _study(tmp_path / "study")
    runtime = SimpleNamespace(active_root=study, exploration_root=tmp_path)
    record = {"version": 3, "systems": ["line one\r\nline two"], "toolsets": [],
              "sent": [], "truncated": False}
    record["sha256"] = digest_of(record)
    brief = panel._kept_receipt(panel._receipts_of(runtime), record)
    said = panel.receipt_endpoint(runtime, brief["sha256"])
    assert said["ok"] and said["receipt"]["systems"] == ["line one\r\nline two"]
