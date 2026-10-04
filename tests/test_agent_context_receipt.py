"""Context receipts and the request-scoped active-view Agent tool."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from fastmdxplora.agent.propose import propose_config
from fastmdxplora.agent.tools import Toolbox, current_view_tool


def _model(*replies):
    given = iter(replies)
    prompts = []

    def complete(prompt):
        prompts.append(prompt)
        return next(given)

    return complete, prompts


def _study(root):
    (root / "analysis").mkdir(parents=True)
    return root


def test_receipt_keeps_exact_prompts_and_tool_output_and_hashes_it(tmp_path):
    study = _study(tmp_path / "study")
    tool = current_view_tool({"study": str(study)})
    complete, prompts = _model(
        "USE: current_view\nhints:\n  study: " + str(study),
        "SAY: The active study has no recorded findings yet.",
    )

    proposal = propose_config("What is in the active view?", complete,
                             tools=Toolbox(extra=(tool,)))
    receipt = proposal.receipt.as_record()

    assert receipt["prompts"] == prompts
    assert receipt["tool_outputs"][0]["tool"] == "read_study"
    assert receipt["tool_outputs"][0]["said"] in prompts[1]
    payload = {key: value for key, value in receipt.items() if key != "sha256"}
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    assert receipt["sha256"] == expected
    assert len(receipt["sha256"]) == 64


def test_current_view_is_bounded_and_routes_facts_to_existing_tools(tmp_path):
    study = _study(tmp_path / "study")
    box = Toolbox(extra=(current_view_tool({"study": str(study)}),))

    look = box.use("current_view", {"hints": {"study": str(study)}})

    assert look.ok
    assert "read_study" in look.said
    assert any(item.tool == "read_study" for item in box.looks)
    too_large = box.use("current_view", {"hints": {"selection": "x" * 5000}})
    assert not too_large.ok
    assert "bounded" in too_large.said


def test_propose_endpoint_returns_and_persists_receipt(tmp_path, monkeypatch):
    from fastmdxplora.gui.agent_panel import propose_endpoint

    study = _study(tmp_path / "study")
    runtime = SimpleNamespace(active_root=study, exploration_root=tmp_path)
    complete, prompts = _model("SAY: No active study findings.")
    import fastmdxplora.agent as agent

    monkeypatch.setattr(agent, "completion_for", lambda: complete)
    answer = propose_endpoint({"request": "Summarize this study."}, runtime)

    assert answer["context_receipt"]["prompts"] == prompts
    digest = answer["context_receipt"]["sha256"]
    receipts = list((study / "agent" / "conversations" / "receipts").glob(
        f"{digest}.json"))
    assert len(receipts) == 1
    saved = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert saved == answer["context_receipt"]


def test_endpoint_rejects_a_view_that_is_not_the_active_study(tmp_path, monkeypatch):
    from fastmdxplora.gui.agent_panel import propose_endpoint

    study = _study(tmp_path / "study")
    other = _study(tmp_path / "other")
    runtime = SimpleNamespace(active_root=study, exploration_root=tmp_path)
    called = []
    import fastmdxplora.agent as agent

    monkeypatch.setattr(agent, "completion_for", lambda: called.append(True))
    answer = propose_endpoint({"request": "Read this view.",
                               "current_view": {"study": str(other)}}, runtime)

    assert not answer["ok"]
    assert "current view changed" in answer["error"].lower()
    assert called == []


@pytest.mark.parametrize("key", ["current_view", "view_hints"])
def test_endpoint_scopes_active_view_tool_to_request(tmp_path, monkeypatch, key):
    from fastmdxplora.gui.agent_panel import propose_endpoint

    study = _study(tmp_path / "study")
    runtime = SimpleNamespace(active_root=study, exploration_root=tmp_path)
    complete, prompts = _model(
        "USE: current_view\nhints:\n  study: " + str(study),
        "SAY: The study has no recorded findings.",
    )
    import fastmdxplora.agent as agent

    monkeypatch.setattr(agent, "completion_for", lambda: complete)
    answer = propose_endpoint({"request": "Read this view.", key: {"study": str(study)}},
                              runtime)

    assert answer["answer"].startswith("The study")
    assert any("`current_view`" in prompt for prompt in prompts[:1])
