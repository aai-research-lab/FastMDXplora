"""An assistant asks the FastMDXplora Agent, when the person asks for it.

The assistant writes a study itself and the validator judges it; that is
the default. `ask_agent` is optional and offered last, since it calls a
second model on the person's own key. It is the Agent as the GUI has it:
the person's own model, the
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


def test_the_agent_is_offered_last_as_optional_and_says_who_pays(workspace):
    wire = _wire(workspace, Model())
    tools = wire.request("tools/list")["result"]["tools"]
    agent = tools[-1]
    assert agent["name"] == "ask_agent"
    assert agent["title"] == "Ask the FastMDXplora Agent (optional; may use your API key)"
    assert agent["description"].startswith("Optional: only when the person asks for "
                                            "FastMDXplora's own Agent.")
    assert "this app's own model where the app lends it" in agent["description"]
    assert "paid for on top of this conversation" in agent["description"]
    said = wire.request("server/discover")["result"]["instructions"]
    assert "give it to check_study. The validator is the judge" in said
    assert "ask_agent is optional" in said and "first" not in said
    wire.close()


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
    whose = "\n\nWritten with the model chosen with `fastmdx agent model`."
    assert ask(wire, request="Simulate my protein")["content"][0]["text"] == (
        "The Agent asks: Which structure: a PDB identifier or a file?\n"
        "Answer it in a new request, with what it asks for." + whose)
    assert ask(wire, request="What does density tell me?")["content"][0]["text"] == (
        "The Agent says: Density tells you whether the box has reached its pressure."
        + whose)
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
        "No model is chosen for the Agent.\nThis app does not lend its own model, so the "
        "Agent writes with one you choose once, in a terminal: `fastmdx agent model`. "
        "Every other tool here works without one.")
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


# ---------------------------------------------------------------------------
# The app's own model, lent (sampling), so nothing is paid twice
# ---------------------------------------------------------------------------
def _no_key():
    raise AssertionError("the person's own key was used where the app lent its model")


def _lent(text, model="lent-model"):
    return {"role": "assistant", "content": {"type": "text", "text": text}, "model": model}


def _lending_wire(workspace):
    return Wire(App(Workspace.at(workspace), complete_for=_no_key).server())


def test_a_legacy_app_lends_its_model_and_the_study_records_it(workspace):
    wire = _lending_wire(workspace)
    wire.initialize(capabilities={"sampling": {}})
    replies = ["USE: inspect_structure\nsystem: ghg.pdb", STUDY]
    asked = []

    def answer(message):
        asked.append(message)
        return _lent(replies.pop(0))

    result = wire.request("tools/call", {"name": "ask_agent", "arguments": {
        "request": "Five nanoseconds of ghg.pdb"}}, modern=False, answer=answer)["result"]
    said = result["content"][0]["text"]
    assert not result["isError"]
    assert [m["method"] for m in asked] == ["sampling/createMessage"] * 2
    params = asked[0]["params"]
    assert params["maxTokens"] == 4000 and params["messages"][0]["role"] == "user"
    assert "Five nanoseconds of ghg.pdb" in params["messages"][0]["content"]["text"]
    assert ("Written with test's own model (lent-model), lent through the protocol: your "
            "own API key was not used.") in said
    config = yaml.safe_load(next(workspace.glob("fastmdxplora_*.yml")).read_text())
    assert (config["agent"], config["agent_model"]) == ("assisted", "test/lent-model")
    wire.close()


def test_a_legacy_app_that_declines_writes_nothing_and_no_key_is_used(workspace):
    wire = _lending_wire(workspace)
    wire.initialize(capabilities={"sampling": {}})
    result = wire.request("tools/call", {"name": "ask_agent", "arguments": {
        "request": "Five nanoseconds of ghg.pdb"}}, modern=False,
        answer=lambda message: {})["result"]
    assert result["isError"]
    assert result["content"][0]["text"] == (
        "The app did not lend its model for this. Nothing was written. Ask again to have "
        "the app lend it; your own API key is not used in its place.")
    assert list(workspace.glob("*.yml")) == []
    wire.close()


def _modern_round(wire, arguments, responses=None, state=None):
    params = {"name": "ask_agent", "arguments": arguments}
    if responses is not None:
        params["inputResponses"] = responses
    if state is not None:
        params["requestState"] = state
    return wire.request("tools/call", params, capabilities={"sampling": {}})["result"]


def test_a_modern_app_lends_its_model_a_round_at_a_time(workspace):
    wire = _lending_wire(workspace)
    arguments = {"request": "Five nanoseconds of ghg.pdb"}
    first = _modern_round(wire, arguments)
    assert first["resultType"] == "input_required"
    (key, request), = first["inputRequests"].items()
    assert key == "fastmdx-sample-0" and request["method"] == "sampling/createMessage"
    assert "Five nanoseconds of ghg.pdb" in request["params"]["messages"][0]["content"]["text"]

    second = _modern_round(wire, arguments,
                           {key: _lent("USE: inspect_structure\nsystem: ghg.pdb")},
                           first["requestState"])
    (key2, request2), = second["inputRequests"].items()
    assert key2 == "fastmdx-sample-1"
    # The look was taken, and the second prompt carries what it found.
    assert "inspect_structure" in request2["params"]["messages"][0]["content"]["text"]

    done = _modern_round(wire, arguments, {key2: _lent(STUDY, "other-model")},
                         second["requestState"])
    assert done["resultType"] == "complete" and not done["isError"]
    said = done["content"][0]["text"]
    assert "Written with test's own model (lent-model, other-model)" in said
    config = yaml.safe_load(next(workspace.glob("fastmdxplora_*.yml")).read_text())
    assert config["agent_model"] == "test/lent-model, test/other-model"
    wire.close()


def test_a_lent_reply_answers_only_its_own_call_and_only_once(workspace):
    wire = _lending_wire(workspace)
    arguments = {"request": "Five nanoseconds of ghg.pdb"}
    first = _modern_round(wire, arguments)
    reply = {"fastmdx-sample-0": _lent(STUDY)}
    # Given back with another request: not this call's reply.
    other = _modern_round(wire, {"request": "Fifty"}, reply, first["requestState"])
    assert other["isError"] and "not this server's for this call" in other["content"][0]["text"]
    # Altered, to carry a reply never given: not this server's.
    import base64
    import json

    body, mac = first["requestState"].split(".")
    said = json.loads(base64.urlsafe_b64decode(body))
    said["c"]["replies"] = [[said["c"]["asked"], STUDY, "forged"]]
    altered = base64.urlsafe_b64encode(json.dumps(said).encode()).decode()
    forged = _modern_round(wire, arguments, reply, altered + "." + mac)
    assert forged["isError"] and "not this server's" in forged["content"][0]["text"]
    padded = _modern_round(wire, arguments, reply, body + "x." + mac)
    assert padded["isError"]
    # Its own, once: accepted; the same state again is not.
    assert not _modern_round(wire, arguments, reply, first["requestState"])["isError"]
    again = _modern_round(wire, arguments, reply, first["requestState"])
    assert again["isError"] and "used before" in again["content"][0]["text"]
    assert len(list(workspace.glob("fastmdxplora_*.yml"))) == 1
    wire.close()


def test_a_modern_app_that_gives_no_reply_writes_nothing(workspace):
    wire = _lending_wire(workspace)
    arguments = {"request": "Five nanoseconds of ghg.pdb"}
    first = _modern_round(wire, arguments)
    declined = _modern_round(wire, arguments, {"fastmdx-sample-0": {"action": "decline"}},
                             first["requestState"])
    assert declined["isError"]
    assert declined["content"][0]["text"].startswith("The app did not lend its model for this.")
    assert list(workspace.glob("*.yml")) == []
    wire.close()


def test_without_lending_the_person_s_own_key_is_used_and_said(workspace, monkeypatch):
    from fastmdxplora.agent.models import ModelChoice, save_choice

    save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"), key="sk-x")
    wire = _wire(workspace, Model(STUDY))
    said = ask(wire, request="Five nanoseconds of ghg.pdb")["content"][0]["text"]
    assert ("Written with your model (anthropic/claude-sonnet-4-6), chosen with "
            "`fastmdx agent model`, on your own API key.") in said
    config = yaml.safe_load(next(workspace.glob("fastmdxplora_*.yml")).read_text())
    assert config["agent_model"] == "anthropic/claude-sonnet-4-6"
    wire.close()


def test_a_study_that_moves_on_between_rounds_is_read_once(workspace, monkeypatch):
    """A running study's record changes by the second (its step, its time
    left). Read again each round, the first prompt would no longer match
    the reply given to it, and the call could never finish."""
    _study(workspace / "ubq", duration=10, means={}, started="2026-09-02T10:00:00+00:00")
    read = []

    def record(folder, *, for_the_agent=False):
        read.append(folder)
        return f"status: running, step {len(read) * 1000}"

    monkeypatch.setattr("fastmdxplora.mcp.tools.study_record", record)
    wire = _lending_wire(workspace)
    arguments = {"request": "How far has ubq got?", "study": "ubq"}
    first = _modern_round(wire, arguments)
    assert "step 1000" in first["inputRequests"]["fastmdx-sample-0"]["params"][
        "messages"][0]["content"]["text"]
    second = _modern_round(wire, arguments,
                           {"fastmdx-sample-0": _lent("USE: inspect_structure\nsystem: ghg.pdb")},
                           first["requestState"])
    done = _modern_round(wire, arguments,
                         {"fastmdx-sample-1": _lent("SAY: It is at step 1000.")},
                         second["requestState"])
    assert not done["isError"]
    assert done["content"][0]["text"].startswith("The Agent says: It is at step 1000.")
    assert len(read) == 1
    wire.close()


def test_a_prompt_that_comes_out_differently_is_not_given_another_s_reply(
        workspace, monkeypatch):
    from fastmdxplora.agent.tools import Toolbox

    rounds = []
    described = Toolbox.describe

    def describe(self):
        rounds.append(1)
        return described(self) + f"\n(round {len(rounds)})\n"

    monkeypatch.setattr(Toolbox, "describe", describe)
    wire = _lending_wire(workspace)
    arguments = {"request": "Five nanoseconds of ghg.pdb"}
    first = _modern_round(wire, arguments)
    later = _modern_round(wire, arguments, {"fastmdx-sample-0": _lent(STUDY)},
                          first["requestState"])
    assert later["isError"]
    assert later["content"][0]["text"].startswith(
        "The work came out differently when done again with the replies given so far")
    assert list(workspace.glob("*.yml")) == []
    wire.close()
