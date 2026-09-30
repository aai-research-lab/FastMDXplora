"""The harness that measures whether the Agent's looking helps.

`fastmdxplora.validation.agent_looks` asks the configured model questions
the software answers itself, with the tools and without, and counts the
replies that agree. These tests drive it with scripted models, so what is
checked is the harness: how a reply is read and judged, how the arms are
counted, and that the software's answers are the tools' own.
"""

from __future__ import annotations

import json

import pytest

from fastmdxplora.validation import agent_looks
from fastmdxplora.validation.agent_looks import Question, judge, numbers_in, run


@pytest.fixture
def tools_say(monkeypatch):
    """Every tool answers with a fixed line, so nothing is fetched."""
    from fastmdxplora.agent import tools

    said = {"inspect_structure": "1UBQ: chains: A; 76 protein residues"}
    patched = {name: (arguments, what,
                      lambda box, asked, name=name: said.get(name, "nothing"))
               for name, (arguments, what, _) in tools._TOOLS.items()}
    monkeypatch.setattr(tools, "_TOOLS", patched)
    return said


def _looker(prompt: str) -> str:
    """Looks when it can, then quotes what it was told; guesses when not."""
    if "## What the software said" in prompt:
        return "SAY: The software says 76 protein residues."
    if "## Looking before you answer" in prompt:
        return "USE: inspect_structure\nsystem: 1UBQ"
    return "SAY: Ubiquitin has about seventy residues, I think 74."


RESIDUES = Question("residues", "How many protein residues does 1UBQ have?",
                    "inspect_structure", lambda: 76)


class TestReadingAReply:
    @pytest.mark.parametrize("text,numbers", [
        ("about 5,432 atoms", [5432.0]),
        ("7.25 nm wide, and 2 chains", [7.25, 2.0]),
        ("one histidine", [1.0]),
        ("1L2Y has 20 residues", [20.0]),
        ("none", []),
    ])
    def test_the_numbers_it_states(self, text, numbers):
        assert numbers_in(text) == numbers

    def test_a_number_within_the_tolerance(self):
        size = Question("size", "", "preview_setup", lambda: 3305, 0.10)
        assert judge(size, 3305, "About 3,400 atoms.")[0]
        agreed, why = judge(size, 3305, "About 5,000 atoms.")
        assert not agreed and why == "5000 against 3305, 51.3% off"
        assert judge(size, 3305, "I cannot say.") == (False, "states no number")

    def test_a_count_is_exact(self):
        assert judge(RESIDUES, 76, "76 residues")[0]
        assert not judge(RESIDUES, 76, "75 residues")[0]

    def test_names_every_one(self):
        ligands = Question("ligands", "", "inspect_structure", lambda: ["AP5"])
        assert judge(ligands, ["AP5"], "It holds AP5, a bisubstrate inhibitor.")[0]
        assert judge(ligands, ["AP5", "MG"], "Only AP5.") == (False, "does not name MG")
        # A name inside a longer word is not the name.
        assert not judge(ligands, ["AP5"], "AP5X")[0]

    def test_none_is_an_answer_where_there_are_none(self):
        ligands = Question("ligands", "", "inspect_structure", lambda: [])
        assert judge(ligands, [], "It has no ligands.")[0]
        assert not judge(ligands, [], "It holds ATP.")[0]


class TestCounting:
    def test_each_arm_is_counted(self, tools_say):
        result = run(_looker, repeats=2, questions=(RESIDUES,))
        tools, without = result["summary"]["tools"], result["summary"]["no tools"]
        assert (tools["asked"], tools["agreed"], tools["looked"]) == (2, 2, 2)
        assert (without["asked"], without["agreed"], without["looked"]) == (2, 0, 0)
        assert result["truths"] == {"residues": 76}
        trial = next(t for t in result["trials"] if t["arm"] == "tools")
        assert trial["looks"] == ["inspect_structure"] and trial["why"] == "76 against 76"

    def test_a_config_is_not_an_answer(self, tools_say):
        result = run(lambda prompt: "systems:\n  - {system: 1UBQ}\n", repeats=1,
                     questions=(RESIDUES,), arms=(False,))
        (trial,) = result["trials"]
        assert trial["kind"] == "config" and not trial["correct"]
        assert trial["why"] == "a config, not an answer"

    def test_a_question_the_software_cannot_answer_is_left_out(self, capsys):
        broken = Question("broken", "?", "inspect_structure", lambda: 1 / 0)
        result = run(_looker, repeats=1, questions=(broken,))
        assert result["trials"] == [] and result["truths"] == {"broken": None}
        assert "broken: the software's answer could not be computed" in capsys.readouterr().err

    def test_a_model_that_fails_is_a_result(self):
        def fails(prompt):
            raise RuntimeError("the service did not answer")
        result = run(fails, repeats=1, questions=(RESIDUES,), arms=(False,))
        (trial,) = result["trials"]
        assert trial["kind"] == "failed" and "did not answer" in trial["why"]


def test_it_runs_from_the_command_line(tools_say, monkeypatch, tmp_path, capsys) -> None:
    import fastmdxplora.agent as agent_mod

    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: _looker)
    monkeypatch.setattr(agent_mod, "load_choice", lambda: agent_mod.ModelChoice(
        "openai", "a-model", None))
    monkeypatch.setattr(agent_looks, "QUESTIONS", (RESIDUES,))
    out = tmp_path / "looks.json"
    assert agent_looks.main(["--repeats", "1", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "tools: 1 of 1 agreed with the software, 1 looked" in printed
    assert "no tools: 0 of 1 agreed" in printed
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["model"] == "openai/a-model" and record["repeats"] == 1
    assert len(record["trials"]) == 2


def test_the_questions_are_answered_as_the_tools_answer_them(monkeypatch, tmp_path) -> None:
    """Each question's answer is computed by the functions its tool calls, on
    a structure file, with nothing fetched."""
    from fastmdxplora.gui import preview
    from tests.test_what_setup_will_build_is_said_before_it_runs import _file, _residue

    structure = _file(tmp_path, _residue(1, "GLY", "A", 1, 0.0) + _residue(5, "HIS", "A", 2, 4.0)
                      + _residue(9, "GLY", "A", 3, 8.0) + "END\n")
    monkeypatch.setattr(preview, "structure_file", lambda text, path_for: structure)
    assert agent_looks._counted("X")["protein_residues"] == 3
    assert agent_looks._selected("X", "name CA") == 3
    assert agent_looks._histidines("X") == 1


def test_a_size_is_the_builders_estimate(monkeypatch) -> None:
    from fastmdxplora.gui import preview
    from fastmdxplora.refusals import StudyError

    monkeypatch.setattr(preview, "preview_of_config", lambda config: {
        "ok": True, "estimate": {"particles": 3305, "width_nm": 3.1}})
    assert agent_looks._preview("1L2Y") == {"particles": 3305, "width_nm": 3.1}
    monkeypatch.setattr(preview, "preview_of_config",
                        lambda config: {"ok": False, "reason": "no such entry"})
    with pytest.raises(StudyError, match="no such entry"):
        agent_looks._preview("9ZZZ")


@pytest.mark.network
def test_the_software_answers_every_question() -> None:
    """Every question's answer, from the PDB, as the harness computes it."""
    truths = {q.name: q.truth() for q in agent_looks.QUESTIONS}
    assert truths["residues_1ubq"] == 76
    assert truths["ligands_1ake"] == ["AP5"]
    assert truths["selection_1l2y"] == 10
    # Setup builds 1HHO's tetramer: 19 histidines in each alpha-beta pair.
    assert truths["histidines_1hho"] == 38
    assert 2500 < truths["particles_1l2y"] < 5000
    assert 5.0 < truths["box_1ake"] < 9.0
