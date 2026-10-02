"""The second registration of whether the Agent's looking helps.

The first asked about well-known entries at their defaults, and an AI model
answered all of them from memory in both arms. The second
(`fastmdxplora.validation.agent_looks_v2`, registered in
``preregistration/agent-looks-v2.md``) asks about files made at the start of
a run and settings that are not the defaults, and judges only the answer a
reply commits to on its ``ANSWER:`` line. These tests drive it with scripted
AI models: what is checked is the harness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.validation.agent_looks import Question, run
from fastmdxplora.validation.agent_looks_v2 import (FILES, committed, declined,
                                                    judge_committed, questions)

SIZE = Question("size", "", "preview_setup", lambda: 3857, 0.10)
COUNT = Question("count", "", "check_selection", lambda: 0)
NAMES = Question("names", "", "inspect_structure", lambda: ["LG7"])


class TestTheAnswerCommittedTo:
    @pytest.mark.parametrize("reply,line", [
        ("SAY: It has 51.\nANSWER: 51", "51"),
        ("SAY: About 3,900 atoms. **ANSWER:** 3,857", "3,857"),
        ("SAY: ANSWER: 12, then on reflection\nANSWER: `LG7`.", "LG7"),
        ("SAY: about 4,000", None),
    ])
    def test_it_is_the_last_answer_line(self, reply, line):
        assert committed(reply) == line

    def test_a_number_is_the_first_on_that_line_not_the_nearest_anywhere(self):
        # The first registration took the number nearest the truth from a
        # range; this one takes what the reply commits to.
        reply = "SAY: Between 3,500 and 4,500 atoms.\nANSWER: 4,500"
        agreed, why = judge_committed(SIZE, 3857, reply)
        assert not agreed and why == "commits to 4500 against 3857, 16.7% off"
        assert judge_committed(SIZE, 3857, "SAY: x\nANSWER: 3,900 atoms")[0]
        assert judge_committed(SIZE, 3857, "SAY: about 3,857 atoms") == (
            False, "commits to no answer (no ANSWER line)")
        assert not judge_committed(SIZE, 3857, "SAY: x\nANSWER: unknown")[0]

    def test_a_count_of_none_is_zero(self):
        assert judge_committed(COUNT, 0, "SAY: none match.\nANSWER: 0")[0]
        assert not judge_committed(COUNT, 0, "SAY: residues 1 to 10.\nANSWER: 10")[0]

    def test_names_are_the_set_exactly(self):
        assert judge_committed(NAMES, ["LG7"], "SAY: x\nANSWER: lg7")[0]
        assert judge_committed(NAMES, ["LG7"], "SAY: x\nANSWER: AP5") == (
            False, "names AP5 where the file has LG7")
        assert not judge_committed(NAMES, ["LG7"], "SAY: x\nANSWER: LG7, MG")[0]
        none = Question("none", "", "inspect_structure", lambda: [])
        assert judge_committed(none, [], "SAY: x\nANSWER: none")[0]


def test_the_arms_are_counted_on_what_is_committed_to(monkeypatch):
    from fastmdxplora.agent import tools

    patched = {name: (arguments, what, lambda box, asked: "0 atoms match")
               for name, (arguments, what, _) in tools._TOOLS.items()}
    monkeypatch.setattr(tools, "_TOOLS", patched)

    def model(prompt: str) -> str:
        if "## What the software said" in prompt:
            return "SAY: The software says none match.\nANSWER: 0"
        if "## Looking before you answer" in prompt:
            return "USE: check_selection\nsystem: x.pdb\nexpression: name CA"
        return "SAY: Residues 1 to 10 have ten alpha carbons.\nANSWER: 10"

    from fastmdxplora.validation.agent_looks_v2 import judge_committed as rule

    result = run(model, repeats=2, questions=(COUNT,), judge_with=rule)
    assert result["summary"]["tools"]["agreed"] == 2
    assert result["summary"]["tools"]["looked"] == 2
    assert result["summary"]["no tools"]["agreed"] == 0
    assert {t["why"] for t in result["trials"] if t["arm"] == "no tools"} == {
        "commits to 10 against 0"}


def test_an_answer_that_commits_to_nothing_is_counted_as_declined():
    trials = [
        {"arm": "no tools", "kind": "answer", "why": "commits to no answer (no ANSWER line)"},
        {"arm": "no tools", "kind": "answer",
         "why": "commits to 'cannot know', which is not a number"},
        {"arm": "no tools", "kind": "answer", "why": "commits to 10 against 0"},
        {"arm": "tools", "kind": "question", "why": "a question, not an answer"},
    ]
    assert declined(trials) == {"no tools": 2}


def test_the_questions_name_the_files_and_ask_for_one_line(tmp_path):
    files = {name: tmp_path / name for name in FILES}
    asked = questions(files)
    assert [q.name for q in asked] == [
        "particles_trimmed", "box_trimmed", "particles_1ubq_settings", "residues_renumbered",
        "selection_renumbered", "ligands_kinase", "histidines_alpha"]
    for question in asked:
        assert "ANSWER: " in question.request
        assert question.name == "particles_1ubq_settings" or str(tmp_path) in question.request
    assert "dodecahedron box, 1.6 nm of solvent padding and 0.3 M NaCl" in asked[0].request


@pytest.mark.network
def test_the_files_give_the_registered_answers(tmp_path):
    """What the registration fixed, from the entries as the PDB gives them."""
    from fastmdxplora.validation.agent_looks_v2 import prepare

    truths = {q.name: q.truth() for q in questions(prepare(tmp_path))}
    assert truths["particles_trimmed"] == 3857
    assert truths["box_trimmed"] == pytest.approx(3.912, abs=1e-3)
    assert truths["particles_1ubq_settings"] == 20872
    assert (truths["residues_renumbered"], truths["selection_renumbered"]) == (51, 0)
    assert truths["ligands_kinase"] == ["LG7"] and truths["histidines_alpha"] == 10
    assert sorted(p.name for p in Path(tmp_path).iterdir()) == sorted(FILES)


def _entries(tmp_path, monkeypatch):
    """Stand-ins for the four entries, so nothing is fetched: proteins of
    alanines, a second chain and an AP5 where the files are made from them."""
    import mdtraj as md
    import numpy as np

    from fastmdxplora.gui import preview

    def entry(name, residues, *, chains=1, ligand=False, frames=1):
        topology = md.Topology()
        for _ in range(chains):
            chain = topology.add_chain()
            for number in range(1, residues + 1):
                residue = topology.add_residue("ALA", chain, resSeq=number)
                for atom, element in (("N", md.element.nitrogen), ("CA", md.element.carbon),
                                      ("C", md.element.carbon), ("O", md.element.oxygen)):
                    topology.add_atom(atom, element, residue)
        if ligand:
            residue = topology.add_residue("AP5", topology.add_chain(), resSeq=1)
            for k in range(3):
                topology.add_atom(f"C{k}", md.element.carbon, residue)
        rng = np.random.default_rng(0)
        xyz = rng.normal(0, 1.5, (frames, topology.n_atoms, 3)).astype(np.float32)
        path = tmp_path / "entries" / f"{name}.pdb"
        path.parent.mkdir(exist_ok=True)
        md.Trajectory(xyz, topology).save_pdb(str(path))
        return path

    made = {"1L2Y": entry("1L2Y", 20, frames=2), "1UBQ": entry("1UBQ", 76),
            "1AKE": entry("1AKE", 30, chains=2, ligand=True),
            "1HHO": entry("1HHO", 25, chains=2)}
    monkeypatch.setattr(preview, "structure_file",
                        lambda system, _given=None: made.get(str(system), Path(str(system))))
    return made


def test_the_files_are_made_as_registered_from_what_the_entries_hold(tmp_path, monkeypatch):
    import mdtraj as md

    from fastmdxplora.validation.agent_looks_v2 import prepare

    _entries(tmp_path, monkeypatch)
    files = prepare(tmp_path / "work")
    trimmed = md.load(str(files["trimmed.pdb"]))
    assert (trimmed.n_frames, trimmed.n_residues) == (1, 12)
    renumbered = md.load(str(files["renumbered.pdb"])).topology
    assert [r.resSeq for r in renumbered.residues][::50] == [201, 251]
    kinase = md.load(str(files["kinase.pdb"])).topology
    assert kinase.n_chains == 2 and "LG7" in {r.name for r in kinase.residues}
    assert "AP5" not in {r.name for r in kinase.residues}
    assert md.load(str(files["alpha.pdb"])).topology.n_chains == 1


def test_a_run_records_its_files_registration_and_declined_answers(tmp_path, monkeypatch, capsys):
    import json

    import fastmdxplora.agent as agent
    from fastmdxplora.validation import agent_looks_v2

    _entries(tmp_path, monkeypatch)
    monkeypatch.setattr(agent, "load_choice", lambda: None)
    monkeypatch.setattr(agent, "completion_for",
                        lambda: (lambda prompt: "SAY: I cannot know without looking."))
    out = tmp_path / "out.json"
    assert agent_looks_v2.main(["--work", str(tmp_path / "work"), "--repeats", "1",
                                "--out", str(out)]) == 0
    result = json.loads(out.read_text())
    assert result["registration"] == "preregistration/agent-looks-v2.md"
    assert set(result["files"]) == set(FILES) and all(
        len(f["sha256"]) == 64 for f in result["files"].values())
    asked = {t["question"] for t in result["trials"]}
    assert result["declined"].get("no tools") == len(asked)
    assert "committed to nothing" in capsys.readouterr().out
    assert agent_looks_v2.main(["--work", str(tmp_path / "work"), "--truths-only"]) == 0
    said = capsys.readouterr().out
    assert "residues_renumbered: 51" in said and "selection_renumbered: 0" in said
    assert "ligands_kinase: ['LG7']" in said and "renumbered.pdb: sha256 " in said
