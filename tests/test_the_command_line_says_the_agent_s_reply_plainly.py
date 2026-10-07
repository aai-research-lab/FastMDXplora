"""`fastmdx agent` says what the Agent replied, as a terminal shows it.

Found on the first live run with a provider's key (10-07): every message
was announced as "Writing a config..." though most were answered; an
accepted config read "Accepted after 1 attempt(s)" where the page says
"Accepted first time."; an answer's emphasis came out as `**1AKI**`; and
the first message's tokens, written to the provider's cache for the next
one, were counted as plain input with nothing to say why the next message
cost a tenth as much.
"""

from __future__ import annotations

import argparse

from fastmdxplora.agent.turns import ToolCall, Turn, Usage


def _run(monkeypatch, tmp_path, reply: Turn) -> None:
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    import fastmdxplora.agent as agent_module
    from fastmdxplora.cli.main import _run_agent

    def complete(prompt):  # pragma: no cover - the tool path is taken
        raise AssertionError("asked in text")

    complete.turn = lambda system, messages, tools, **kw: reply
    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: complete)
    args = argparse.Namespace(request="anything", request_file=None, agent_mode="assisted",
                              phases="setup,simulation", attempts=None, agent_output=None,
                              budget_hours=None)
    _run_agent(args)


def test_an_answer_is_not_announced_as_a_config(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, Turn("The usual one is **2 fs**, see "
                                     "[the docs](https://example.org/x).", (),
                                     Usage(1, 10, 0, 0, 5)))
    out = capsys.readouterr().out
    assert "Writing a config" not in out
    assert "Asking the AI model..." in out
    assert "The usual one is 2 fs, see the docs (https://example.org/x)." in out
    assert "**" not in out


def test_a_config_accepted_first_time_says_so(monkeypatch, tmp_path, capsys):
    study = {"systems": [{"system": "1L2Y"}], "simulation": {"duration_ns": 10}}
    _run(monkeypatch, tmp_path, Turn("", (ToolCall("c1", "propose_config", {
        "config": study, "note": "1L2Y is an *NMR* ensemble."}),), Usage(1, 10, 0, 0, 5)))
    out = capsys.readouterr().out
    assert "✓ Accepted first time." in out
    assert "attempt(s)" not in out
    assert "1L2Y is an NMR ensemble." in out


def test_tokens_kept_for_the_next_message_are_said():
    assert Usage(1, 93, 0, 17806, 498).said() == (
        "1 call, 17,899 tokens in (17,806 written to the cache), 498 out")
    assert Usage(2, 100, 28770, 300, 412).said() == (
        "2 calls, 29,170 tokens in (28,770 cached, 300 written to the cache), 412 out")
    assert Usage(1, 900, 0, 0, 10).said() == "1 call, 900 tokens in, 10 out"


def test_code_in_backticks_and_lone_asterisks_are_kept():
    from fastmdxplora.cli.main import _in_plain_text

    assert _in_plain_text("set `simulation.timestep_fs` to 2") == (
        "set `simulation.timestep_fs` to 2")
    assert _in_plain_text("2*3 and a*b*c") == "2*3 and a*b*c"
    assert _in_plain_text("* a list item") == "* a list item"


def test_code_and_names_with_underscores_are_kept_as_written():
    from fastmdxplora.cli.main import _in_plain_text

    assert _in_plain_text("Use `*.pdb` or `*.cif`, **not** a `.gro`") == (
        "Use `*.pdb` or `*.cif`, not a `.gro`")
    assert _in_plain_text("open `src/__init__.py` and __this__") == (
        "open `src/__init__.py` and this")
    assert _in_plain_text("foo__bar__baz and snake_case_name") == (
        "foo__bar__baz and snake_case_name")
    assert _in_plain_text("**bold** *it* [a](https://x.org/a_b)") == (
        "bold it a (https://x.org/a_b)")
