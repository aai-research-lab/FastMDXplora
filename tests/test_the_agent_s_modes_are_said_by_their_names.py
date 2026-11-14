"""The Agent's modes are said by their names in the docs and the CLI's help.

Decided by the user (10-18): "mode" is the one word a person reads for the
Agent's setting, and the modes are Seeker, Ranger, Pathfinder and Pioneer.
`assisted` is said as Seeker (built-in, assisted) and `autonomous` as Ranger
(built-in, autonomous), as the GUI says them; the flags and the values a
Config keeps are unchanged. `unvalidated` is not one of the modes: it is
said as outside the schema.
"""

from __future__ import annotations

from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


def test_the_agent_page_names_the_modes():
    text = (DOCS / "agent.md").read_text(encoding="utf-8")
    modes = _section(text, "\n## The three modes", "\n## Which phases were checked")
    assert "Seeker, Ranger, Pathfinder and Pioneer" in modes
    assert "### Seeker (`assisted`): draft it and stop" in modes
    assert "### Ranger (`autonomous`): draft it and run it" in modes
    assert "### `unvalidated`: outside the schema, not one of the modes" in modes
    # The flags and the Config's values are as they were.
    for flag in ("--assisted", "--autonomous --budget-hours 40", "--unvalidated"):
        assert f'fastmdx agent "..." {flag}' in modes, flag
    assert "`agent: <value>`" in modes
    assert "—" not in modes and "–" not in modes


def test_a_link_to_ranger_s_section_reaches_it():
    production = (DOCS / "production.md").read_text(encoding="utf-8")
    assert "agent.md#ranger-autonomous-draft-it-and-run-it" in production
    assert "agent.md#autonomous--draft-it-and-run-it" not in production


def test_the_cli_page_names_the_modes():
    text = (DOCS / "cli.md").read_text(encoding="utf-8")
    assert "| `--assisted` | *(default)* | Seeker (built-in, assisted): draft it and stop |" in text
    assert ("| `--autonomous` | none | Ranger (built-in, autonomous): draft it and run it. "
            "**Requires `--budget-hours`** |") in text
    assert ("| `--unvalidated` | none | Outside the schema, not one of the modes: marks the "
            "output as having gone outside the schema |") in text


def test_the_help_names_the_modes():
    from fastmdxplora.cli.main import _build_parser

    parser = _build_parser()
    agent = next(action for action in parser._subparsers._group_actions[0].choices.values()
                 if any("--autonomous" in a.option_strings for a in action._actions))
    helps = {a.option_strings[0]: " ".join(a.help.split()) for a in agent._actions
             if a.option_strings and a.option_strings[0] in
             ("--assisted", "--autonomous", "--unvalidated")}
    assert helps["--assisted"].startswith("Seeker (built-in, assisted): draft the study and stop")
    assert helps["--autonomous"].startswith(
        "Ranger (built-in, autonomous): draft the study and run it")
    assert helps["--unvalidated"].startswith("Outside the schema, not one of the modes:")
    consts = {a.option_strings[0]: a.const for a in agent._actions
              if a.option_strings and a.option_strings[0] in helps}
    assert consts == {"--assisted": "assisted", "--autonomous": "autonomous",
                      "--unvalidated": "unvalidated"}


def test_the_rest_of_the_agent_page_says_the_modes_by_their_names():
    text = " ".join((DOCS / "agent.md").read_text(encoding="utf-8").split())
    assert ("the mode by its name and its two words (**Seeker: built-in, assisted** or "
            "**Ranger: built-in, autonomous**), which opens the Agent's settings") in text
    assert "A Ranger (`autonomous`) Config that carries its own `budget_hours`" in text
    assert "a Ranger (`autonomous`) run still needs its budget" in text
    assert "is what Seeker (`assisted`) promises" in text
    # The AI model is said in the Agent's settings and nowhere else in the GUI.
    assert "The engine, the mode and the GPU-hour ceiling are in Settings" not in text
    assert "in **Agent settings**, the one place its AI model is said" in text
