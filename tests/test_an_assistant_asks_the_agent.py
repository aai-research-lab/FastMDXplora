"""An assistant asks the FastMDXplora Agent, rather than writing a study itself.

`ask_agent` is the Agent as the GUI has it: the person's own model, the
software's tools to look with, the validator as the judge. A study it
writes comes back accepted, recorded as the Agent's, saved as a new file
with its plan and plan_id; a question, an answer or an instruction comes
back as itself; nothing is run. The model here is scripted: what is tested
is what the server does with each kind of reply.
"""

from __future__ import annotations

import pytest
import yaml

from fastmdxplora.mcp import App, Workspace
from fastmdxplora.mcp.tools import plan_id_of
from fastmdxplora.refusals import StudyError
from tests._mcp_wire import Wire
from tests.test_an_assistant_reads_and_checks_studies import _structure, _study

STUDY = "systems:\n  - system: ghg.pdb\nsimulation:\n  duration_ns: 5\n"


class Model:
    """Replies in order, every prompt kept."""

    def __init__(self, *replies: str) -> None:
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    root.mkdir()
    _structure(root / "ghg.pdb")
    return root


def _wire(workspace, model):
    return Wire(App(Workspace.at(workspace), complete_for=lambda: model).server())


def ask(wire, **arguments):
    return wire.request("tools/call", {"name": "ask_agent", "arguments": arguments,
                                       "_meta": {"progressToken": "t"}})["result"]


def test_a_study_comes_back_accepted_saved_and_planned(workspace):
    model = Model("USE: inspect_structure\nsystem: ghg.pdb", STUDY)
    wire = _wire(workspace, model)
    result = ask(wire, request="Five nanoseconds of the tripeptide in ghg.pdb")
    said = result["content"][0]["text"]
    assert not result["isError"]
    assert said.startswith("The FastMDXplora Agent wrote a study, and the validator "
                           "accepted it first time.\nSaved to fastmdxplora_")
    saved = sorted(workspace.glob("fastmdxplora_*.yml"))
    assert len(saved) == 1
    text = saved[0].read_text()
    assert text.startswith("# Written by the FastMDXplora Agent, asked: Five nanoseconds "
                           "of the tripeptide in ghg.pdb\n")
    config = yaml.safe_load(text)
    assert config["agent"] == "assisted" and config["output"] == saved[0].stem
    assert config["simulation"] == {"duration_ns": 5}
    assert "  Production: 5 ns, 2 fs steps" in said
    assert f"plan_id: {plan_id_of(saved[0])} (for start_study" in said
    assert "What the Agent checked with the software:\n  - inspect_structure: " in said
    progress = [m["params"]["message"] for m in wire.seen
                if m.get("method") == "notifications/progress"]
    assert progress == ["The Agent is writing", "The Agent looks: inspect_structure",
                        "The Agent is writing"]
    wire.close()


def test_a_refusal_on_the_way_is_said_and_unsaved_is_not_written(workspace):
    model = Model("systems:\n  - system: ghg.pdb\nsimulaton:\n  duration_ns: 5\n", STUDY)
    wire = _wire(workspace, model)
    said = ask(wire, request="Five nanoseconds", save=False)["content"][0]["text"]
    assert "accepted it after 2 attempts.\nRefused on the way, and corrected:\n" \
           "  - config.option.unknown: " in said
    assert "Not saved; save_study writes it." in said and "plan_id" not in said
    assert list(workspace.glob("*.yml")) == []
    wire.close()


def test_a_question_an_answer_and_an_instruction_come_back_as_themselves(workspace):
    model = Model("ASK: Which structure: a PDB identifier or a file?",
                  "SAY: Density tells you whether the box has reached its pressure.",
                  "DO: run", "DO: open viewer")
    wire = _wire(workspace, model)
    assert ask(wire, request="Simulate my protein")["content"][0]["text"] == (
        "The Agent asks: Which structure: a PDB identifier or a file?\n"
        "Answer it in a new request, with what it asks for.")
    assert ask(wire, request="What does density tell me?")["content"][0]["text"] == (
        "The Agent says: Density tells you whether the box has reached its pressure.")
    run = ask(wire, request="run it")["content"][0]["text"]
    assert run.startswith("The Agent read this as an instruction: run.\ncheck_study")
    assert "fastmdx gui" in ask(wire, request="show me")["content"][0]["text"]
    assert list(workspace.glob("*.yml")) == []
    wire.close()


def test_giving_up_is_an_error_with_each_refusal(workspace):
    bad = "systems:\n  - system: ghg.pdb\nsimulaton: {}\n"
    wire = _wire(workspace, Model(bad, bad, bad))
    result = ask(wire, request="Something")
    said = result["content"][0]["text"]
    assert result["isError"]
    assert said.startswith("The Agent gave up after 3 attempts; the validator refused each.")
    assert said.count("  - config.option.unknown: ") == 3
    wire.close()


def test_a_change_and_a_study_reach_the_agent(workspace):
    (workspace / "ghg.yml").write_text(STUDY)
    _study(workspace / "ubq", duration=10, means={"rmsd": (0.1234, 0.0056)},
           started="2026-09-02T10:00:00+00:00")
    model = Model(STUDY.replace("duration_ns: 5", "duration_ns: 10"),
                  "SAY: The RMSD of ubq is 0.1234 ± 0.0056 nm.")
    wire = _wire(workspace, model)
    changed = ask(wire, request="the same but 10 ns", config="ghg.yml")["content"][0]["text"]
    assert "  Production: 10 ns, 2 fs steps" in changed
    assert "duration_ns: 5" in model.prompts[0]
    assert (workspace / "ghg.yml").read_text() == STUDY
    ask(wire, request="What did ubq find?", study="ubq")
    assert "rmsd: mean 0.1234 ± 0.0056 nm" in model.prompts[1]
    wire.close()


def test_without_a_model_it_says_how_to_choose_one(workspace):
    def no_model():
        raise StudyError("No model is chosen for the Agent.", code="environment.model.unset")

    wire = Wire(App(Workspace.at(workspace), complete_for=no_model).server())
    result = ask(wire, request="Five nanoseconds of ghg.pdb")
    assert result["isError"]
    assert result["content"][0]["text"] == (
        "No model is chosen for the Agent.\nThe Agent writes with a model you choose once, "
        "in a terminal: `fastmdx agent model`. Every other tool here works without one.")
    wire.close()


def test_a_study_naming_a_file_outside_is_not_saved(workspace):
    outside = workspace.parent / "elsewhere.pdb"
    wire = _wire(workspace, Model(f"systems:\n  - system: {outside}\n"))
    result = ask(wire, request="Simulate the structure next door")
    assert result["isError"]
    assert f"`systems[0].system` names {outside}, outside the workspace" in \
        result["content"][0]["text"]
    assert list(workspace.glob("*.yml")) == []
    wire.close()


def test_a_read_only_server_says_where_a_study_is_run(workspace):
    model = Model("DO: run", STUDY)
    wire = Wire(App(Workspace.at(workspace), runs=False,
                    complete_for=lambda: model).server())
    run = ask(wire, request="run it")["content"][0]["text"]
    assert "This server does not start or stop studies" in run
    wrote = ask(wire, request="Five nanoseconds of ghg.pdb")["content"][0]["text"]
    assert "plan_id" not in wrote and "To run it: `fastmdx explore --config " in wrote
    wire.close()
