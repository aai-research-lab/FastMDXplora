"""How long the Agent's instructions stay in the provider's cache.

Every message starts with the same 17,900 tokens of instructions, kept in
Anthropic's cache for five minutes. On the Agent page a person reads a plan
before answering, and a reply after a five-minute pause wrote them again at
1.25 times the input price instead of reading them at a tenth. Kept for an
hour, writing them costs twice the price once. So the setting is `auto` (an
hour on the page, five minutes from the command line), `1h` or `5m`.
"""

from __future__ import annotations

import json

import pytest

from fastmdxplora.agent.models import ModelChoice, cache_for, save_cache_for, save_choice
from fastmdxplora.agent.turns import _blocks_body


def _body(kept: str) -> dict:
    return _blocks_body("claude-x", "the instructions", [{"role": "user", "text": "hi"}],
                        [], 100, kept)


def test_an_hour_is_asked_for_on_both_marks_and_five_minutes_as_before():
    hour = _body("1h")
    assert hour["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert hour["messages"][-1]["content"][-1]["cache_control"] == {
        "type": "ephemeral", "ttl": "1h"}
    five = _body("5m")
    assert five["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert five["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_auto_is_an_hour_on_the_page_and_five_minutes_elsewhere(tmp_path):
    path = tmp_path / "model.json"
    assert cache_for("page", path) == "1h" and cache_for("command line", path) == "5m"
    save_choice(ModelChoice("anthropic", "claude-x"), key="sk-mine", path=path)
    save_cache_for("5m", path)
    assert cache_for("page", path) == "5m"
    save_cache_for("1h", path)
    assert cache_for("command line", path) == "1h"
    record = json.loads(path.read_text())
    assert record["api_key"] == "sk-mine" and record["cache_for"] == "1h"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError):
        save_cache_for("1d", path)


def test_the_page_asks_for_its_cache_and_the_command_line_for_its_own(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import fastmdxplora.agent.turns as turns
    from fastmdxplora.agent.models import completion_for

    save_choice(ModelChoice("anthropic", "claude-x"))
    asked = []
    monkeypatch.setattr(turns, "take_turn", lambda **kw: asked.append(kw["cache_for"]))
    completion_for(where="page").turn("s", [{"role": "user", "text": "hi"}], [])
    completion_for().turn("s", [{"role": "user", "text": "hi"}], [])
    assert asked == ["1h", "5m"]


def test_the_page_builds_its_completion_as_the_page(monkeypatch, tmp_path):
    import types

    import fastmdxplora.agent as agent_module
    from fastmdxplora.gui.agent_panel import propose_endpoint
    from fastmdxplora.refusals import StudyError

    given = {}

    def built(*a, **kw):
        given.update(kw)
        raise StudyError("no AI model here", code="environment.model.unset")

    monkeypatch.setattr(agent_module, "completion_for", built)
    monkeypatch.setattr("fastmdxplora.gui.records_answer.answered_from_the_records",
                        lambda *a: None)
    propose_endpoint({"request": "hi"}, types.SimpleNamespace(exploration_root=str(tmp_path)))
    assert given == {"where": "page"}


def test_settings_show_and_keep_it(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    import fastmdxplora.agent.models as models
    from fastmdxplora.gui.agent_panel import model_endpoint

    monkeypatch.setattr(models, "list_models", lambda choice, **kw: ())
    assert model_endpoint({})["cache_for"] == "auto"
    saved = model_endpoint({"provider": "anthropic", "model": "claude-x", "cache_for": "5m"})
    assert saved["ok"]
    assert model_endpoint({})["cache_for"] == "5m"
    refused = model_endpoint({"provider": "anthropic", "model": "claude-x", "cache_for": "1d"})
    assert not refused["ok"] and "auto, 1h, 5m" in refused["error"]


def test_the_command_asks_for_anthropic_only(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    import fastmdxplora.agent.models as models
    from fastmdxplora.cli.main import _choose_model

    monkeypatch.setattr(models, "list_models", lambda choice, **kw: ())
    for answers, kept in ((["1", "", "", "1h"], "1h"), (["2", "", ""], "auto")):
        replies = iter(answers)
        monkeypatch.setattr("builtins.input", lambda prompt="", replies=replies: next(replies))
        assert _choose_model() == 0
        record = json.loads(models.model_path().read_text())
        assert record["cache_for"] == kept
