"""A long thread is kept in brief, and the AI model offered first is current.

Two things the Agent was given that went stale. The conversation was cut
to its last 12 turns, so in a long thread the force field settled twenty
turns back was gone; now the turns before those are kept by their first
sentence. And the AI model offered first was written in (`claude-sonnet-4-6`,
`gpt-5`) and stayed there as newer ones came out; now it is the newest of
that family the provider offers, the written one only where it cannot be
asked.
"""

from __future__ import annotations

import json
import types

import pytest


def _thread(turns: int) -> list[dict[str, str]]:
    return [{"role": "user" if n % 2 == 0 else "agent",
             "text": f"Turn {n} said this. And then more {n}."} for n in range(turns)]


def test_the_turns_before_the_last_twelve_are_kept_by_their_first_sentence():
    from fastmdxplora.agent.propose import earlier_in_brief

    brief = earlier_in_brief(_thread(20))
    assert brief.startswith("## Earlier in this conversation\n")
    assert "- Person: Turn 0 said this." in brief
    assert "- Agent: Turn 7 said this." in brief
    assert "And then more" not in brief
    assert "Turn 8 " not in brief
    assert earlier_in_brief(_thread(12)) == ""


def test_a_config_written_is_said_as_one():
    from fastmdxplora.agent.propose import earlier_in_brief

    thread = [{"role": "agent", "text": "Wrote a config:\nsystems:\n- system: 1UBQ"}]
    assert "- Agent: Wrote a config." in earlier_in_brief(thread + _thread(12))


def test_both_ways_of_asking_carry_it():
    from fastmdxplora.agent.conversation import _history
    from fastmdxplora.agent.propose import prompt_for

    thread = _thread(20)
    prompt = prompt_for("and now at 310 K", history=thread)
    assert "## Earlier in this conversation" in prompt
    assert "Agent: Turn 19 said this. And then more 19." in prompt
    turns = _history(thread)
    assert turns[0]["role"] == "user" and "Turn 0 said this." in turns[0]["text"]
    assert len(turns) == 13
    assert turns[-1]["text"] == "Turn 19 said this. And then more 19."


def test_the_page_sends_on_the_turns_kept(monkeypatch, tmp_path):
    import fastmdxplora.agent as agent_module
    from fastmdxplora.gui.agent_panel import propose_endpoint
    from fastmdxplora.refusals import StudyError

    given = {}

    def propose(request, complete, **kw):
        given["history"] = kw["history"]
        raise StudyError("stopped here")

    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: object())
    monkeypatch.setattr(agent_module, "propose_config", propose)
    propose_endpoint({"request": "go on", "history": _thread(70)},
                     types.SimpleNamespace(exploration_root=str(tmp_path)))
    assert len(given["history"]) == 60
    assert given["history"][-1]["text"].startswith("Turn 69 ")


@pytest.mark.parametrize("provider, offered, chosen", [
    ("anthropic", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-sonnet-5",
                   "claude-haiku-4-5-20251001", "claude-sonnet-4-6"], "claude-sonnet-5-5"),
    ("anthropic", ["claude-sonnet-4-5-20250929", "claude-sonnet-4-5",
                   "claude-sonnet-4-20250514"], "claude-sonnet-4-5"),
    ("anthropic", ["claude-opus-5-5"], "claude-sonnet-4-6"),
    ("anthropic", [], "claude-sonnet-4-6"),
    ("openai", ["gpt-4o", "gpt-5-mini", "gpt-5", "gpt-5.1", "o3"], "gpt-5.1"),
    ("compatible", ["llama3.1"], ""),
])
def test_the_newest_of_the_default_s_family_is_offered_first(provider, offered, chosen):
    from fastmdxplora.agent.models import default_model

    assert default_model(provider, offered) == chosen


def test_settings_offer_the_newest_once_the_provider_is_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    import fastmdxplora.agent.models as models
    from fastmdxplora.agent.models import ModelChoice, save_choice
    from fastmdxplora.gui.agent_panel import model_endpoint

    save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"))
    monkeypatch.setattr(models, "list_models",
                        lambda choice, **kw: ("claude-sonnet-5-5", "claude-sonnet-4-6"))
    said = model_endpoint({})
    anthropic = next(p for p in said["providers"] if p["id"] == "anthropic")
    openai = next(p for p in said["providers"] if p["id"] == "openai")
    assert anthropic["default_model"] == "claude-sonnet-5-5"
    assert openai["default_model"] == "gpt-5"


def test_a_stored_key_is_never_sent_to_another_provider(monkeypatch, tmp_path):
    from fastmdxplora.agent.models import ModelChoice, _key_for, save_choice
    from fastmdxplora.refusals import StudyError

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FASTMDX_MODEL_API_KEY", raising=False)
    path = tmp_path / "model.json"
    save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"), key="sk-ant-mine", path=path)
    assert _key_for(ModelChoice("anthropic", "x"), path) == "sk-ant-mine"
    with pytest.raises(StudyError):
        _key_for(ModelChoice("openai", "gpt-5"), path)
    save_choice(ModelChoice("compatible", "deepseek-chat", "https://api.deepseek.com"),
                key="sk-ds", path=path)
    with pytest.raises(StudyError):
        _key_for(ModelChoice("compatible", "x", "https://openrouter.ai/api/v1"), path)
    assert json.loads(path.read_text())["api_key"] == "sk-ds"


def test_the_command_suggests_the_newest(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    import fastmdxplora.agent.models as models
    from fastmdxplora.cli.main import _choose_model

    monkeypatch.setattr(models, "list_models",
                        lambda choice, **kw: ("claude-sonnet-5-5", "claude-sonnet-4-6"))
    answers = iter(["1", "", ""])
    prompts = []

    def answer(prompt=""):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    assert _choose_model() == 0
    assert "AI model [claude-sonnet-5-5]: " in prompts
    from fastmdxplora.agent.models import load_choice

    assert load_choice().model == "claude-sonnet-5-5"
