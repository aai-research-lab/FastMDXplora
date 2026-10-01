"""The Agent looks with the software's own tools before it answers.

It wrote YAML and answered from what it was given, so a size, a time or a
chain it named was its own guess. It may now look first: a reply that opens
`USE: <tool>` runs the tool (inspect a structure, preview what setup builds
and how long the study takes here, check a config against the validator,
check a selection) and the model is asked again with what the software
said. The tools only look; a look is not an attempt; the looks are capped;
and what the software said is shown under the answer.

The model here is scripted: what is tested is the loop and the tools, which
are the software's, not what a particular model chooses to do.
"""

from __future__ import annotations

import json
import tempfile

import pytest

from fastmdxplora.agent.propose import propose_config, prompt_for
from fastmdxplora.agent.tools import MOST_LOOKS, Toolbox, use_in

ATOM = ("{:6s}{:5d} {:<4s} {:>3s} {}{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00"
        "          {:>2s}\n")


@pytest.fixture
def structure(tmp_path, monkeypatch):
    """A tripeptide with a histidine whose NE2 is 2.4 Angstrom from a zinc."""
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))
    residues = [
        ("GLY", [("N", 0, 0), ("CA", 1.4, 0), ("C", 2.4, 1), ("O", 2.4, 2.2)]),
        ("HIS", [("N", 3.6, .5), ("CA", 5, .5), ("C", 6, 1.5), ("O", 6, 2.7),
                 ("CB", 5.3, -1), ("CG", 5.3, -2.4), ("ND1", 4.3, -3.3),
                 ("CE1", 4.9, -4.5), ("NE2", 6.1, -4.4), ("CD2", 6.4, -3)]),
        ("GLY", [("N", 7.2, 1), ("CA", 8.5, 1), ("C", 9.5, 2), ("O", 9.5, 3.2)]),
    ]
    lines, serial = [], 1
    for number, (resname, atoms) in enumerate(residues, start=1):
        for name, x, y in atoms:
            lines.append(ATOM.format("ATOM", serial, f" {name}" if len(name) < 4 else name,
                                     resname, "A", number, x, y, 0.0, name[0]))
            serial += 1
    lines.append(ATOM.format("HETATM", serial, "ZN", "ZN", "A", 401, 7.6, -6.3, 0.0, "ZN"))
    path = tmp_path / "site.pdb"
    path.write_text("".join(lines) + "END\n", encoding="utf-8")
    return path


def _model(*replies):
    """Replies in order; every prompt kept."""
    given = iter(replies)
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return next(given)
    return complete, prompts


class TestReadingTheAsk:
    def test_the_arguments_below(self):
        assert use_in("USE: preview_setup\nconfig:\n  systems:\n  - system: 1UBQ\n") == (
            "preview_setup", {"config": {"systems": [{"system": "1UBQ"}]}})

    def test_on_the_same_line_and_fenced(self):
        assert use_in('USE: check_selection {"system": "1UBQ", "expression": "name CA"}') == (
            "check_selection", {"system": "1UBQ", "expression": "name CA"})
        assert use_in("USE: `inspect_structure`\n```yaml\nsystem: 1UBQ\n```") == (
            "inspect_structure", {"system": "1UBQ"})

    def test_arguments_it_cannot_read_are_kept_as_what_they_were(self):
        assert use_in("USE: check_config\nconfig: [unclosed") == (
            "check_config", {"unreadable": "config: [unclosed"})
        assert use_in("USE: inspect_structure 1UBQ") == ("inspect_structure", {"value": "1UBQ"})
        assert use_in("USE: check_config") == ("check_config", {})

    def test_anything_else_is_not_a_look(self):
        assert use_in("SAY: use the tools") is None
        assert use_in("systems:\n- system: 1UBQ\n") is None


class TestTheTools:
    def test_a_structure_is_inspected(self, structure):
        said = Toolbox().use("inspect_structure", {"system": str(structure)})
        assert said.ok
        assert "chains: A; 3 protein residues" in said.said
        assert "ions: ZN" in said.said
        assert "HIS 1 (A:2)" in said.said
        assert "A:2 HIS: NE2 is 2.4 Å from ZN A:401" in said.said

    def test_what_setup_builds_is_previewed(self, structure):
        said = Toolbox().use("preview_setup", {"config": {
            "systems": [{"system": str(structure)}], "setup": {"box_shape": "cube"}}})
        assert said.ok and "particles in a cube" in said.said
        assert "time here: This machine has not been timed yet." in said.said

    def test_a_config_is_checked_with_its_fix(self, structure):
        refused = Toolbox().use("check_config", {"config": {
            "systems": [{"system": str(structure)}], "setup": {"box_shape": "sphere"}}})
        assert not refused.ok
        assert refused.said.startswith("Refused (config.option.not_permitted)")
        assert "What would fix it: Set `setup.box_shape` to one of: cube" in refused.said
        accepted = Toolbox().use("check_config", {"config": {
            "systems": [{"system": str(structure)}], "simulation": {"duration_ns": 5}}})
        assert accepted.ok and "Production: 5 ns, 2 fs steps" in accepted.said

    def test_a_selection_is_checked(self, structure):
        pytest.importorskip("mdtraj")
        said = Toolbox().use("check_selection", {"system": str(structure),
                                                 "expression": "resname HIS and name NE2"})
        assert said.said == ("'resname HIS and name NE2' matches 1 of 19 atoms in 1 residue: "
                             "HIS2 (chain A)")
        empty = Toolbox().use("check_selection", {"system": str(structure),
                                                  "expression": "resid 500"})
        assert "matches none" in empty.said and "resSeq" in empty.said

    def test_only_a_structure_is_read(self, tmp_path):
        secret = tmp_path / "keys.txt"
        secret.write_text("not a structure", encoding="utf-8")
        said = Toolbox().use("inspect_structure", {"system": str(secret)})
        assert not said.ok and "not a PDB identifier or a structure file" in said.said
        assert "not a structure" not in said.said

    def test_hosted_it_reads_inside_the_workspace_only(self, structure):
        box = Toolbox(path_for=lambda given: None)
        said = box.use("inspect_structure", {"system": str(structure)})
        assert not said.ok and "outside the workspace" in said.said

    def test_another_studys_record_is_read(self, tmp_path):
        from tests.test_every_refusal_says_its_fix_and_price import (
            STOPPED, _config, _manifest, _speed, _stopped_at)

        root = tmp_path / "earlier"
        _config(root)
        _speed(root)
        _stopped_at(root, 100_000)
        _manifest(root, "simulation", STOPPED)
        folder = root / "analysis" / "rmsd"
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps({"analysis": "rmsd", "findings": {
            "mean": {"mean": 0.112, "standard_error": 0.002, "unit": "nm"}}}),
            encoding="utf-8")
        said = Toolbox().use("read_study", {"study": str(root)})
        assert said.ok
        assert "duration_ns: 0.5" in said.said
        assert "rmsd: mean 0.1120 \u00b1 0.0020 nm (s.e.)" in said.said
        assert "fastmdx resume" in said.said and "about 15 min" in said.said
        not_one = Toolbox().use("read_study", {"study": str(tmp_path / "missing")})
        assert not not_one.ok and "is not a study folder" in not_one.said
        hosted = Toolbox(path_for=lambda given: None).use("read_study", {"study": str(root)})
        assert not hosted.ok and "outside the workspace" in hosted.said

    def test_what_a_tool_cannot_use_is_refused_in_words(self, structure, tmp_path):
        box = Toolbox()
        cases = [
            ("inspect_structure", {}, "Name a structure"),
            ("preview_setup", {"config": "systems: [unclosed"}, "Give the config as `config`"),
            ("check_config", {"config": {}}, "Give the config as `config`"),
            ("check_selection", {"system": str(structure)}, "Give the selection"),
            ("check_selection", {"system": str(structure), "expression": "name =="},
             "is not a valid selection"),
            ("read_study", {}, "Name the study"),
        ]
        for tool, asked, said in cases:
            look = box.use(tool, asked)
            assert not look.ok and said in look.said, (tool, look.said)
        # And each carries the registry's name for it.
        from fastmdxplora.agent.tools import _Refused

        assert _Refused("x").refusal.code == "agent.tool.refused"

    def test_a_config_given_as_text_is_read(self, structure):
        said = Toolbox().use("check_config", {
            "config": f"systems:\n- system: {structure}\n"})
        assert said.ok and said.said.startswith("Accepted.")

    def test_a_study_that_has_recorded_nothing(self, tmp_path):
        (tmp_path / "fresh" / "analysis").mkdir(parents=True)
        said = Toolbox().use("read_study", {"study": str(tmp_path / "fresh")})
        assert said.ok and "It has recorded nothing yet" in said.said

    def test_an_unknown_tool_is_named_and_the_real_ones_listed(self):
        said = Toolbox().use("run_it", {})
        assert not said.ok and "inspect_structure, preview_setup" in said.said


class TestTheLoop:
    def test_it_looks_then_answers_from_what_the_software_said(self, structure):
        complete, prompts = _model(
            f"USE: inspect_structure\nsystem: {structure}\n",
            "SAY: The histidine at A:2 holds the zinc by NE2, 2.4 Angstrom away.")
        box = Toolbox()
        proposal = propose_config("Which residue holds the zinc?", complete, tools=box)
        assert proposal.answer.startswith("The histidine at A:2")
        assert [look.tool for look in proposal.looks] == ["inspect_structure"]
        # A look is not an attempt.
        assert proposal.attempts == ()
        # Asked again with what the software said.
        assert "## What the software said" not in prompts[0]
        assert "## What the software said" in prompts[1]
        assert "NE2 is 2.4 Å from ZN A:401" in prompts[1]

    def test_it_previews_then_writes_the_study(self, structure):
        config = f"systems:\n- system: {structure}\nsimulation:\n  duration_ns: 5\n"
        complete, _ = _model("USE: preview_setup\nconfig:\n" + "\n".join(
            "  " + line for line in config.splitlines()), config)
        proposal = propose_config("5 ns of this", complete, tools=Toolbox())
        assert proposal.accepted and proposal.cycles == 1
        assert proposal.looks[0].tool == "preview_setup" and proposal.looks[0].ok

    def test_the_looking_is_capped(self, structure):
        replies = [f"USE: inspect_structure\nsystem: {structure}\n"] * (MOST_LOOKS + 3)
        complete, prompts = _model(*replies)
        proposal = propose_config("look forever", complete, tools=Toolbox(), max_cycles=3)
        assert len(proposal.looks) == MOST_LOOKS
        assert not proposal.accepted
        assert proposal.refusal.message.startswith("The looks for this reply are used up")
        assert len(prompts) == MOST_LOOKS + 3
        assert "That is all the looking there is for this reply." in prompts[-1]

    def test_without_a_toolbox_a_look_is_not_offered(self):
        assert "## Looking before you answer" not in prompt_for("1UBQ for 5 ns")
        told = prompt_for("1UBQ for 5 ns", tools=Toolbox())
        assert "## Looking before you answer" in told
        for name in ("inspect_structure", "preview_setup", "check_config", "check_selection",
                     "read_study"):
            assert f"`{name}`" in told


def test_the_panel_is_given_what_it_looked_at(structure, monkeypatch) -> None:
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.agent_panel import propose_endpoint

    complete, _ = _model(f"USE: inspect_structure\nsystem: {structure}\n",
                         "SAY: Chain A, with a zinc.")
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: complete)
    answer = propose_endpoint({"request": "What is in it?"})
    assert answer["answer"] == "Chain A, with a zinc."
    (look,) = answer["looks"]
    assert look["tool"] == "inspect_structure" and look["ok"]
    assert look["asked"] == {"system": str(structure)} and "ions: ZN" in look["said"]


def test_the_thread_shows_it_and_keeps_it(structure, tmp_path, monkeypatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    replies = iter([f"USE: inspect_structure\nsystem: {structure}\n",
                    "SAY: Chain A, with a zinc held by the histidine."])
    monkeypatch.setattr(agent_mod, "completion_for",
                        lambda *a, **k: (lambda prompt: next(replies)))
    study = tmp_path / "study"
    (study / "analysis").mkdir(parents=True)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.fill("#agent-request", "What is in this structure?")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-answer")
            head = page.text_content("#agent-thread .agent-looks > summary")
            page.click("#agent-thread .agent-looks > summary")
            said = page.text_content('#agent-thread .agent-look[data-tool="inspect_structure"] '
                                     ".agent-look-said")
            browser.close()
    finally:
        session.server.shutdown()
    assert head == "Checked with the software: inspected the structure"
    assert "NE2 is 2.4 Å from ZN A:401" in said
    assert errors == []
    kept = list((study / "agent" / "conversations").glob("*.json"))
    entries = json.loads(kept[0].read_text(encoding="utf-8"))["entries"]
    assert entries[-1]["looks"][0]["tool"] == "inspect_structure"
