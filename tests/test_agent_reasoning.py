"""Reasoning controls must reach transports, with no invented capabilities."""
import json
import io

import pytest

from fastmdxplora.agent import openai_plan
from fastmdxplora.agent.claude_plan import ClaudePlan
from fastmdxplora.agent.kimi_plan import KimiPlan
from fastmdxplora.agent.reasoning import decorate, levels, validate


def stream_opener(events, requests):
    def open_request(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events))
    return open_request


def test_codex_order_and_model_specific_limits():
    rows = [{"id": model, "label": model} for model in
            ["gpt-5.5", "gpt-6-luna", "gpt-6-sol", "gpt-6-astra", "gpt-6.1-sol"]]
    assert [row["id"] for row in decorate("openai-chatgpt", rows)] == [
        "gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.5"]
    assert "none" not in levels("openai-chatgpt", "gpt-6-astra")
    assert "none" in levels("openai-chatgpt", "gpt-6-luna")
    assert "max" not in levels("openai-chatgpt", "gpt-5.5")
    assert levels("gemini", "gemini-2.5-pro")[0] == "128"
    assert levels("gemini", "gemini-unknown") == []
    with pytest.raises(openai_plan.ConnectionError):
        validate("ultra", levels("openai-chatgpt", "gpt-6.1-sol"))


def test_openai_effort_reaches_tool_free_request_and_defaults_are_omitted():
    for effort in ("low", "default"):
        requests = []
        events = [{"type": "response.output_text.delta", "delta": "Answer"},
                  {"type": "response.completed", "response": {"status": "completed", "output": []}}]
        openai_plan.complete("fixture", "gpt-6.1-sol", "question", reasoning=effort,
                             opener=stream_opener(events, requests))
        body = json.loads(requests[0].data)
        assert body["tools"] == []
        assert body.get("reasoning") == ({"effort": "low"} if effort == "low" else None)


def test_claude_native_catalog_metadata_and_explicit_effort(tmp_path, monkeypatch):
    adapter = ClaudePlan(tmp_path)
    monkeypatch.setattr(adapter, "identity", lambda: {"subject": "fixture"})
    commands = []
    monkeypatch.setattr(adapter, "spawn", lambda args: commands.append(args) or args)
    def collect(args, **kwargs):
        if "--input-format" in args:
            assert json.loads(kwargs["text"])["request"]["subtype"] == "initialize"
            models = [{"value": "sonnet", "displayName": "Sonnet", "supportsEffort": True,
                       "supportedEffortLevels": ["low", "high"]},
                      {"value": "fable", "requiresUsageCredits": True}]
            return 0, json.dumps({"type": "control_response", "response": {
                "request_id": "fastmdx-models", "response": {"models": models}}})
        return 0, json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Answer"})
    monkeypatch.setattr(adapter, "collect", collect)
    assert adapter.models("fixture") == [{"id": "sonnet", "label": "Sonnet", "reasoning_levels": ["low", "high"]}]
    assert adapter.complete("question", "fixture", model="sonnet", reasoning="high") == "Answer"
    assert commands[-1][-4:] == ["--model", "sonnet", "--effort", "high"]
    assert commands[-1][commands[-1].index("--tools") + 1] == ""
    with pytest.raises(openai_plan.ConnectionError):
        adapter.complete("question", "fixture", model="sonnet", reasoning="max")


def test_kimi_only_advertises_declared_efforts_or_supported_toggle():
    class API:
        def call(self, *args):
            return {"items": [
                {"provider": "managed:kimi-code", "model": "k3", "support_efforts": ["low", "high", "max"]},
                {"provider": "managed:kimi-code", "model": "fixed", "capabilities": ["thinking", "always_thinking"]},
                {"provider": "managed:kimi-code", "model": "toggle", "capabilities": ["thinking"]}]}
    rows = KimiPlan.catalog_from(API())
    assert [row["reasoning_levels"] for row in rows] == [["low", "high", "max"], [], ["off", "on"]]
