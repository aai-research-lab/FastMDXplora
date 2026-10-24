"""The Agent page and `fastmdx agent` speak as the Agent.

The AI model is the Agent's own, its brain, and is named only where it is
chosen, in the Agent's settings and `fastmdx agent model` (user, 10-07: "no
talk about the AI model as a separate entity ... The Agent owns it"). The
page said "No AI model is set yet", "What the AI model was sent (asked 2
times)", "From this study's records: no AI model was asked."; the terminal
said "Asking the AI model...". And the Agent's replies were set in Georgia,
a system serif FastMDXplora does not ship, beside Inter, which it does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

GUI = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui"


def _strings_of(script: str) -> list[str]:
    """The string literals of a script, its comments left out."""
    code = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    return re.findall(r'"((?:[^"\\\n]|\\.)*)"', code)


def test_the_page_s_script_names_the_ai_model_only_where_it_is_chosen() -> None:
    said = [s for s in _strings_of((GUI / "static" / "agent-panel.js").read_text(encoding="utf-8"))
            if "AI model" in s]
    # The no-choice message and the Settings dialog's custom-model field.
    assert said == ["No AI model is set.", "Type an AI model name\\u2026"]


def test_the_agent_page_names_no_ai_model_outside_its_settings() -> None:
    page = (GUI / "templates" / "dashboard.html").read_text(encoding="utf-8")
    section = page[page.index('data-page="agent"'):]
    section = section[:section.index("</section>")]
    section = re.sub(r"<!--.*?-->", "", section, flags=re.S)
    assert "AI model" not in section
    assert "I am not set up yet." in section and ">Set me up<" in section


def test_an_answer_from_the_records_says_where_it_is_from() -> None:
    from fastmdxplora.gui.records_answer import MARK

    assert MARK == "*From this study's records.*"


def test_a_kept_record_s_refusals_say_what_i_read(tmp_path) -> None:
    import types

    from fastmdxplora.gui.agent_panel import receipt_endpoint

    said = receipt_endpoint(types.SimpleNamespace(exploration_root=str(tmp_path)), "0" * 64)
    assert not said["ok"] and "AI model" not in said["error"]


@pytest.mark.parametrize("where", ["models", "turns"])
def test_a_provider_s_error_is_said_as_mine(where) -> None:
    if where == "turns":
        from fastmdxplora.agent.turns import _stream_error

        said = _stream_error({"error": {"message": "overloaded"}})
    else:
        import io
        import json

        from fastmdxplora.agent import models
        from fastmdxplora.refusals import StudyError

        events = [json.dumps({"type": "error", "error": {"message": "overloaded"}})]
        body = io.BytesIO(("\n".join("data: " + e for e in events) + "\n").encode())
        with pytest.raises(StudyError) as raised:
            models._streamed(body, lambda piece: None, "https://example.org")
        said = str(raised.value)
    assert said.startswith("I could not finish: the provider said: overloaded")


def test_take_turn_refuses_an_unsupported_shape_before_network(monkeypatch) -> None:
    from fastmdxplora.agent.turns import take_turn
    from fastmdxplora.refusals import StudyError

    calls = []

    def unexpected_request(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("an unsupported shape must be refused before network I/O")

    monkeypatch.setattr("urllib.request.urlopen", unexpected_request)
    with pytest.raises(StudyError) as raised:
        take_turn(
            url="https://example.org/v1/chat/completions", headers={},
            shape="responses", model="test-model", system="system",
            messages=[], tools=[], label="test provider",
        )

    assert raised.value.code == "config.option.inapplicable"
    assert calls == []


def test_streamed_openai_error_without_choices_is_said() -> None:
    import io
    import json

    from fastmdxplora.agent.models import _streamed
    from fastmdxplora.refusals import StudyError

    event = {"error": {"message": "overloaded"}}
    response = io.BytesIO(("data: " + json.dumps(event) + "\n\n").encode())
    with pytest.raises(StudyError) as raised:
        _streamed(response, lambda _piece: None, "https://example.org/v1/chat/completions")

    assert raised.value.code == "environment.service.unusable_response"
    assert "overloaded" in str(raised.value)


def test_the_command_line_thinks_and_says_what_was_used(monkeypatch, capsys, tmp_path) -> None:
    import argparse

    import fastmdxplora.agent as agent_module
    from fastmdxplora.agent.turns import Turn, Usage
    from fastmdxplora.cli.main import _run_agent

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))

    def complete(prompt: str) -> str:
        return "SAY: unused"

    def turn(system, messages, tools, on_text=None, on_call=None):
        return Turn("A 2 fs step.", (), Usage(1, 900, 0, 10))

    complete.turn = turn
    complete.turned_away = lambda: False
    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: complete)
    args = argparse.Namespace(request="what timestep?", request_file=None,
                              agent_mode="assisted", phases="setup,simulation",
                              attempts=None, agent_output=None, budget_hours=None)
    assert _run_agent(args) == 0
    out = capsys.readouterr().out
    assert "Thinking..." in out and "Used: 1 call, 900 tokens in, 10 out" in out
    assert "AI model" not in out


def test_the_replies_are_in_the_font_shipped() -> None:
    css = (GUI / "static" / "dashboard.css").read_text(encoding="utf-8")
    rule = css[css.index(".agent-answer, .agent-msg-agent .agent-attempt {"):]
    rule = rule[:rule.index("}")]
    assert "font-family: var(--font-stack);" in rule and "Georgia" not in rule
