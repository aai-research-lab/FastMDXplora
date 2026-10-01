"""The Agent takes tools from outside without this package changing.

A service that runs studies knows things this package cannot: what a lab
has run before, what a run there would cost. It adds tools for the Agent
under the ``fastmdxplora.agent_tools`` entry point, or a program hands them
to the toolbox as ``extra``. Either way they are held to the tools' rules:
a tool here keeps its name, a malformed one is left out with a warning,
and one that fails to load never stops the Agent.
"""

from __future__ import annotations

import logging

import pytest

from fastmdxplora.agent import tools as tools_module
from fastmdxplora.agent.propose import propose_config
from fastmdxplora.agent.tools import AgentTool, ToolRefused, Toolbox


def _history(box: Toolbox, asked: dict) -> str:
    lab = str(asked.get("lab") or "")
    if not lab:
        raise ToolRefused("Name the lab as `lab`.")
    return f"{lab} ran 3 studies of 1UBQ at 300 K, each 100 ns."


HISTORY = AgentTool("lab_history", "`lab` (its name).",
                    "the studies a lab has run before.", _history)


class _Point:
    def __init__(self, name: str, value: str, loaded):
        self.name, self.value, self._loaded = name, value, loaded

    def load(self):
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


@pytest.fixture
def installed(monkeypatch):
    """Entry points as an installed package would give them."""
    given: list[_Point] = []

    def entry_points(group=None):
        assert group == tools_module.ENTRY_POINT_GROUP
        return list(given)

    monkeypatch.setattr("importlib.metadata.entry_points", entry_points)
    tools_module.plugged_in.cache_clear()
    tools_module._warned.clear()
    yield given
    tools_module.plugged_in.cache_clear()
    tools_module._warned.clear()


def test_a_tool_given_as_extra_is_listed_described_and_used(installed):
    box = Toolbox(extra=(HISTORY,))
    assert box.names[-1] == "lab_history"
    assert "`lab_history`: the studies a lab has run before. Arguments: `lab` (its name)." \
        in box.describe()
    look = box.use("lab_history", {"lab": "Aina lab"})
    assert look.ok and look.said == "Aina lab ran 3 studies of 1UBQ at 300 K, each 100 ns."
    refused = box.use("lab_history", {})
    assert not refused.ok and refused.said == "Name the lab as `lab`."


def test_an_installed_tool_is_found_by_its_entry_point(installed):
    installed.append(_Point("history", "service.tools:history", lambda: [HISTORY]))
    box = Toolbox()
    assert "lab_history" in box.names
    assert box.use("lab_history", {"lab": "X"}).ok


def test_a_tool_cannot_take_a_name_already_here(installed, caplog):
    def replaced(box, asked):
        return "not the validator"

    box = Toolbox(extra=(AgentTool("check_config", "`config`.", "anything.", replaced),))
    with caplog.at_level(logging.WARNING, logger="fastmdx.agent.tools"):
        assert box.names.count("check_config") == 1
        look = box.use("check_config", {"config": {"systems": []}})
    assert look.said != "not the validator"
    assert "'check_config' is already taken" in caplog.text


def test_a_broken_or_malformed_tool_is_left_out_and_the_rest_work(installed, caplog):
    installed.append(_Point("broken", "service.tools:broken", ImportError("no module")))
    installed.append(_Point("good", "service.tools:good", HISTORY))
    bad = AgentTool("Lab History", "`lab`.", "the studies.", _history)
    with caplog.at_level(logging.WARNING, logger="fastmdx.agent.tools"):
        box = Toolbox(extra=(bad, "not a tool"))
        names = box.names
        assert box.names == names  # a second listing does not say it again
    assert "lab_history" in names and "Lab History" not in names
    assert "inspect_structure" in names
    assert "could not be loaded, so they are left out: no module" in caplog.text
    assert caplog.text.count("is not a lower-case name") == 1
    assert "is not an AgentTool" in caplog.text


def test_the_agent_looks_with_a_tool_from_outside(installed):
    replies = iter([
        "USE: lab_history\nlab: Aina lab",
        "SAY: The Aina lab has run 1UBQ at 300 K three times, 100 ns each.",
    ])
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    proposal = propose_config("What has my lab run?", complete,
                              tools=Toolbox(extra=(HISTORY,)))
    assert proposal.answer.startswith("The Aina lab has run 1UBQ")
    assert [look.tool for look in proposal.looks] == ["lab_history"]
    assert "`lab_history`" in prompts[0]
    assert "Aina lab ran 3 studies of 1UBQ" in prompts[1]


def test_a_tool_saying_nothing_of_itself_or_an_unreadable_install_is_left_out(
        installed, monkeypatch, caplog):
    silent = AgentTool("quiet", "`x`.", "  ", _history)
    with caplog.at_level(logging.WARNING, logger="fastmdx.agent.tools"):
        assert "quiet" not in Toolbox(extra=(silent,)).names

        def broken(group=None):
            raise OSError("metadata unreadable")

        monkeypatch.setattr("importlib.metadata.entry_points", broken)
        tools_module.plugged_in.cache_clear()
        assert tools_module.plugged_in() == ()
    assert "needs a look to call and a line saying what it tells" in caplog.text
    assert "could not be listed: metadata unreadable" in caplog.text
