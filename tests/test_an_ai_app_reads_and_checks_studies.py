"""An AI app reads and checks studies through `fastmdx mcp`.

The tools say what the software finds, in its words: what a structure
holds, what a selection matches, whether the validator accepts a config and
the plan it would run, what studies the workspace holds and what each
recorded. Each is held to the workspace, and a tool that cannot do what it
was asked says why as an error the model can act on.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from fastmdxplora.mcp import App, Workspace
from fastmdxplora.mcp.tools import TOOLS
from tests._mcp_wire import Wire, meta, text_of

ATOM = ("{:6s}{:5d} {:<4s} {:>3s} {}{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00"
        "          {:>2s}\n")


def _structure(path: Path) -> Path:
    """Gly-His-Gly."""
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
            lines.append(ATOM.format("ATOM", serial, f" {name}", resname, "A", number,
                                     x, y, 0.0, name[0]))
            serial += 1
    path.write_text("".join(lines) + "END\n", encoding="utf-8")
    return path


def _study(where: Path, *, duration: float, means: dict, started: str) -> Path:
    where.mkdir(parents=True)
    (where / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"system": "1UBQ"}], "simulation": {"duration_ns": duration}}),
        encoding="utf-8")
    (where / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "setup", "status": "ok", "started_at": started},
        {"name": "simulation", "status": "ok"}]}), encoding="utf-8")
    for name, (mean, error) in means.items():
        folder = where / "analysis" / name
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps({"analysis": name, "findings": {
            "mean": {"mean": mean, "standard_error": error, "unit": "nm",
                     "effective_samples": 40}}}), encoding="utf-8")
    return where


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    root.mkdir()
    _structure(root / "ghg.pdb")
    (root / "ghg.yml").write_text("systems:\n  - system: ghg.pdb\nsimulation:\n"
                                  "  duration_ns: 5\n", encoding="utf-8")
    _study(root / "ubq_10ns", duration=10, means={"rmsd": (0.1234, 0.0056), "rg": (1.18, 0.002)},
           started="2026-09-02T10:00:00+00:00")
    _study(root / "ubq_20ns", duration=20, means={"rmsd": (0.1500, 0.0040), "rg": (1.181, 0.003)},
           started="2026-09-03T10:00:00+00:00")
    _structure(tmp_path / "elsewhere.pdb")
    return root


@pytest.fixture
def wire(workspace):
    wire = Wire(App(Workspace.at(workspace)).server())
    yield wire
    wire.close()


def call(wire: Wire, tool: str, **arguments) -> dict:
    return wire.request("tools/call", {"name": tool, "arguments": arguments})["result"]


class TestTheList:
    def test_each_tool_is_listed_with_its_schema_and_hints(self, wire):
        tools = wire.request("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        assert names == [t.name for t in TOOLS]
        check = next(t for t in tools if t["name"] == "check_study")
        assert check["inputSchema"] == {
            "type": "object", "properties": {"config": check["inputSchema"]["properties"]["config"]},
            "additionalProperties": False, "required": ["config"]}
        assert check["annotations"]["readOnlyHint"] is True
        save = next(t for t in tools if t["name"] == "save_study")
        assert save["annotations"]["readOnlyHint"] is False
        assert save["annotations"]["destructiveHint"] is False
        listed = next(t for t in tools if t["name"] == "list_studies")
        assert listed["inputSchema"] == {"type": "object", "properties": {
            "tag": listed["inputSchema"]["properties"]["tag"]}, "additionalProperties": False}
        assert listed["inputSchema"]["properties"]["tag"]["type"] == "string"

    def test_arguments_are_held_to_the_schema(self, wire):
        unknown = call(wire, "check_study", config="ghg.yml", force=True)
        assert unknown["isError"] and "takes no force" in unknown["content"][0]["text"]
        missing = call(wire, "check_selection", system="ghg.pdb")
        assert missing["isError"] and "needs `expression`" in missing["content"][0]["text"]
        wrong = call(wire, "read_study", study=3)
        assert wrong["isError"] and "`study` is text" in wrong["content"][0]["text"]
        error = wire.request("tools/call", {"name": "run_anything", "arguments": {}})["error"]
        assert error == {"code": -32602, "message": "Unknown tool: run_anything"}


class TestChecking:
    def test_an_accepted_config_gives_its_plan_and_its_plan_id(self, wire, workspace):
        result = call(wire, "check_study", config="ghg.yml")
        said = result["content"][0]["text"]
        assert not result["isError"]
        assert said.startswith("Accepted by the validator. The plan:\n  System: ghg.pdb")
        assert "  Production: 5 ns, 2 fs steps" in said
        digest = hashlib.sha256((workspace / "ghg.yml").read_bytes()).hexdigest()[:12]
        assert said.endswith(f"plan_id: {digest} (for start_study, once the person has "
                             "agreed to this plan; it changes if ghg.yml does)")

    def test_a_config_given_as_text_is_checked_but_has_no_plan_id(self, wire):
        said = text_of(wire.request("tools/call", {"name": "check_study", "arguments": {
            "config": "systems:\n  - system: ghg.pdb\n"}}))
        assert "plan_id" not in said and said.endswith("To run it, save it first with save_study.")

    def test_a_refused_config_says_why_and_what_would_fix_it(self, wire):
        result = call(wire, "check_study",
                      config="systems:\n  - system: ghg.pdb\nsimulaton:\n  duration_ns: 5\n")
        said = result["content"][0]["text"]
        assert result["isError"]
        assert said.startswith("Refused (config.option.unknown): Unknown top-level key "
                               "'simulaton'")
        assert "\nWhat would fix it: " in said

    def test_a_config_outside_the_workspace_is_not_read(self, wire, workspace):
        (workspace.parent / "outside.yml").write_text("systems:\n  - system: 1UBQ\n")
        result = call(wire, "check_study", config="../outside.yml")
        assert result["isError"] and "is outside the workspace" in result["content"][0]["text"]
        missing = call(wire, "check_study", config="nothing.yml")
        assert missing["content"][0]["text"] == "There is no config at nothing.yml in the workspace."

    def test_a_saved_config_is_new_accepted_and_never_written_over(self, wire, workspace):
        text = "# 5 ns is enough to see whether the histidine flips\nsystems:\n  - system: ghg.pdb\n"
        said = call(wire, "save_study", name="ghg run/2", config=text)["content"][0]["text"]
        assert said.startswith("Saved to ghg_run_2.yml; accepted by the validator. Recorded "
                               "as written in an AI app (agent: assisted, in test (its "
                               "own AI model, which the AI app does not name)). Nothing has "
                               "been run.")
        # Marked as an AI app's, in the AI app that named itself, and no AI model
        # claimed that the AI app did not name; the comment and the order kept.
        written = ("agent: assisted\nagent_model: test (its own AI model, which the AI app "
                   "does not name)\n" + text)
        assert (workspace / "ghg_run_2.yml").read_text() == written
        again = call(wire, "save_study", name="ghg_run_2.yml", config="systems: []\n")
        assert again["isError"]
        assert (workspace / "ghg_run_2.yml").read_text() == written
        twice = call(wire, "save_study", name="ghg_run_2", config=text)
        assert "is never written over" in twice["content"][0]["text"]
        assert call(wire, "save_study", name="x", config="ghg.yml")["isError"]


    def test_a_config_the_person_wrote_is_recorded_as_theirs(self, wire, workspace):
        text = "systems:\n  - system: ghg.pdb\n"
        said = call(wire, "save_study", name="mine", config=text, by_hand=True)
        assert "Recorded as the person's own." in said["content"][0]["text"]
        assert (workspace / "mine.yml").read_text() == text

    def test_a_config_that_says_how_it_was_written_is_left_saying_it(self, wire, workspace):
        text = "agent: autonomous\nbudget_hours: 2\nsystems:\n  - system: ghg.pdb\n"
        said = call(wire, "save_study", name="theirs", config=text)["content"][0]["text"]
        assert "Recorded as it says it was written (agent: autonomous)." in said
        assert (workspace / "theirs.yml").read_text() == text

    def test_a_config_in_flow_style_is_marked_too(self, wire, workspace):
        import yaml

        call(wire, "save_study", name="flow", config="{systems: [{system: ghg.pdb}]}")
        saved = yaml.safe_load((workspace / "flow.yml").read_text())
        assert saved == {"agent": "assisted", "systems": [{"system": "ghg.pdb"}],
                         "agent_model": "test (its own AI model, which the AI app does not name)"}

    def test_a_model_the_config_names_is_kept(self, wire, workspace):
        text = "agent_model: someone/some-model\nsystems:\n  - system: ghg.pdb\n"
        call(wire, "save_study", name="named", config=text)
        assert (workspace / "named.yml").read_text() == "agent: assisted\n" + text


class TestLooking:
    def test_a_structure_is_inspected_and_one_outside_is_not(self, wire):
        said = call(wire, "inspect_structure", system="ghg.pdb")
        assert not said["isError"] and "HIS" in said["content"][0]["text"]
        outside = call(wire, "inspect_structure", system="../elsewhere.pdb")
        assert outside["isError"] and "outside the workspace" in outside["content"][0]["text"]

    def test_setup_is_previewed_from_a_config_file(self, wire):
        said = text_of(wire.request("tools/call", {"name": "preview_setup",
                                                   "arguments": {"config": "ghg.yml"}}))
        assert said.startswith("about 1,964 particles in a dodecahedron")
        assert "solute: 34 atoms with hydrogens, 3 residues, net charge +0" in said
        assert said.endswith("setup's own numbers replace it once it has run.")

    def test_a_selection_says_what_it_matches(self, wire):
        said = text_of(wire.request("tools/call", {"name": "check_selection", "arguments": {
            "system": "ghg.pdb", "expression": "resname HIS"}}))
        assert said == ("'resname HIS' matches 10 of 18 atoms in 1 residue: "
                        "HIS2 (chain A)")


class TestReading:
    def test_the_studies_are_listed_with_what_each_recorded(self, wire):
        said = text_of(wire.request("tools/call", {"name": "list_studies", "arguments": {}}))
        lines = said.splitlines()
        assert lines[0].startswith("2 studies in ") and lines[0].endswith(", newest first:")
        assert lines[1].startswith("- ubq_20ns: 1UBQ, ") and "20 ns production" in lines[1]
        assert "    RMSD: 0.1500 ± 0.0040 nm" in lines
        assert lines[-1] == "YAML files at the top of the workspace: ghg.yml"

    def test_a_study_is_read_and_a_folder_that_is_not_one_is_said(self, wire, workspace):
        said = call(wire, "read_study", study="ubq_10ns")["content"][0]["text"]
        assert said.startswith("The study at ubq_10ns\nstatus: completed")
        assert "0.1234" in said
        (workspace / "notes").mkdir()
        refused = call(wire, "read_study", study="notes")
        assert refused["isError"] and "is not a study folder" in refused["content"][0]["text"]

    def test_two_studies_are_compared_setting_by_setting_and_mean_by_mean(self, wire):
        said = text_of(wire.request("tools/call", {"name": "compare_studies", "arguments": {
            "first": "ubq_10ns", "second": "ubq_20ns"}}))
        assert said.splitlines()[:3] == [
            "ubq_10ns against ubq_20ns", "1 setting differs:",
            "  simulation.duration_ns: 10 -> 20"]
        assert "  RMSD: 0.1234 ± 0.0056 nm | 0.1500 ± 0.0040 nm; second minus first " \
               "+0.0266 ± 0.0069 nm, resolved" in said
        assert "; second minus first +0.0010 ± 0.0036 nm, not resolved" in said


class TestTheCommand:
    def test_standard_output_carries_the_protocol_and_nothing_else(self, workspace):
        request = {"jsonrpc": "2.0", "id": 1, "method": "server/discover",
                   "params": {"_meta": meta()}}
        done = subprocess.run([sys.executable, "-m", "fastmdxplora", "mcp", "--workspace",
                               str(workspace)], input=json.dumps(request) + "\n",
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 0
        lines = done.stdout.splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["result"]["supportedVersions"] == ["2026-07-28"]
        assert f"workspace {workspace}" in done.stderr

    def test_the_top_of_the_file_system_is_not_a_workspace(self):
        done = subprocess.run([sys.executable, "-m", "fastmdxplora", "mcp", "--workspace", "/"],
                              input="", capture_output=True, text=True, timeout=120)
        assert done.returncode == 2 and done.stdout == ""
        assert "cannot be the top of the file system" in done.stderr


class TestTheWorkspaceHolds:
    def test_a_config_naming_a_file_outside_is_refused(self, wire, workspace):
        outside = workspace.parent / "elsewhere.pdb"
        result = call(wire, "check_study", config=f"systems:\n  - system: {outside}\n")
        assert result["isError"]
        assert result["content"][0]["text"].startswith(
            f"`systems[0].system` names {outside}, outside the workspace")
        climbing = call(wire, "check_study", config=(
            "systems:\n  - system: ghg.pdb\nsimulation:\n  setup_from: ../elsewhere\n"))
        assert "`simulation.setup_from` names ../elsewhere" in climbing["content"][0]["text"]
        assert call(wire, "save_study", name="out", config=(
            f"systems:\n  - system: {outside}\n"))["isError"]
        assert not (workspace / "out.yml").exists()

    def test_a_study_holding_a_link_out_is_not_read_or_listed(self, wire, workspace):
        secret = workspace.parent / "secret.txt"
        secret.write_text("API_KEY=not-for-the-model")
        study = workspace / "ubq_10ns"
        (study / "resolved_config.yml").unlink()
        (study / "resolved_config.yml").symlink_to(secret)
        refused = call(wire, "read_study", study="ubq_10ns")
        assert refused["isError"]
        assert refused["content"][0]["text"] == ("ubq_10ns/resolved_config.yml links out of "
                                                 "the workspace, so ubq_10ns is not read.")
        _study(workspace.parent / "outside_study", duration=1, means={},
               started="2026-09-04T10:00:00+00:00")
        (workspace / "linked").symlink_to(workspace.parent / "outside_study")
        listed = text_of(wire.request("tools/call", {"name": "list_studies", "arguments": {}}))
        assert listed.startswith("1 study in ") and "- ubq_20ns: " in listed
        assert "secret" not in listed and "outside_study" not in listed

    def test_a_folder_of_studies_named_runs_is_looked_in(self, wire, workspace):
        _study(workspace / "runs" / "reference", duration=5, means={},
               started="2026-09-05T10:00:00+00:00")
        listed = text_of(wire.request("tools/call", {"name": "list_studies", "arguments": {}}))
        assert listed.splitlines()[1].startswith("- runs/reference: 1UBQ")

    def test_a_config_is_not_saved_under_a_name_that_makes_a_study(self, wire, workspace):
        said = call(wire, "save_study", name="exploration", config="systems:\n  - system: x.pdb\n")
        assert said["isError"] and "the name a study gives its own config" in \
            said["content"][0]["text"]
        saved = text_of(wire.request("tools/call", {"name": "save_study", "arguments": {
            "name": "plain", "config": "systems:\n  - system: ghg.pdb\n"}}))
        assert "\nThe plan:\n  System: ghg.pdb\n" in saved and "\nplan_id: " in saved

    def test_a_one_line_setting_is_yaml_and_another_file_is_said_not_to_be_a_config(self, wire):
        setting = call(wire, "check_study", config="output: run.yml")
        assert setting["isError"] and "There is no config" not in setting["content"][0]["text"]
        other = call(wire, "check_study", config="ghg.pdb")
        assert other["content"][0]["text"] == ("ghg.pdb is not a config file: a config is "
                                               "YAML, in a .yml or .yaml file, or the YAML "
                                               "itself.")

    def test_the_top_of_the_file_system_and_the_home_folder_are_not_workspaces(
            self, tmp_path, monkeypatch):
        from fastmdxplora.refusals import StudyError

        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "work").mkdir()
        for folder in ("/", tmp_path):
            with pytest.raises(StudyError):
                Workspace.at(folder)
        assert Workspace.at(tmp_path / "work").inside("x\0y") is None


class TestNumbers:
    @pytest.mark.parametrize("value, error, said", [
        (0.1234, 0.0056, "0.1234 ± 0.0056"),
        (0.15, 0.004, "0.1500 ± 0.0040"),
        (-123456.7, 12.0, "-123,457 ± 12"),
        (0.0001234, 0.12, "0.00 ± 0.12"),
        (5432.1, 0.0004, "5,432.10000 ± 0.00040"),
        (1.5, None, "1.5"),
        (2.0, 0.0, "2 ± 0"),
    ])
    def test_a_value_is_given_to_the_place_its_error_allows(self, value, error, said):
        from fastmdxplora.mcp.tools import with_error

        assert with_error(value, error) == said

    def test_a_difference_is_given_with_its_sign(self):
        from fastmdxplora.mcp.tools import with_error

        assert with_error(0.0266, 0.0069, sign=True) == "+0.0266 ± 0.0069"
        assert with_error(-0.0266, 0.0069, sign=True) == "-0.0266 ± 0.0069"

    def test_a_difference_that_cannot_be_judged_is_said_so(self, wire, workspace):
        study = _study(workspace / "no_error", duration=10, means={"rmsd": (0.13, 0.0)},
                       started="2026-09-06T10:00:00+00:00")
        options = study / "analysis" / "rmsd" / "options.json"
        record = json.loads(options.read_text())
        record["findings"]["mean"]["standard_error"] = None
        options.write_text(json.dumps(record))
        said = text_of(wire.request("tools/call", {"name": "compare_studies", "arguments": {
            "first": "ubq_10ns", "second": "no_error"}}))
        assert ("  RMSD: 0.1234 ± 0.0056 nm | 0.13 nm; the difference is not assessed: "
                "the second recorded no standard error") in said


def test_a_read_only_server_says_how_a_checked_file_is_run(workspace):
    wire = Wire(App(Workspace.at(workspace), runs=False).server())
    said = text_of(wire.request("tools/call", {"name": "check_study",
                                               "arguments": {"config": "ghg.yml"}}))
    assert said.endswith("To run it: `fastmdx explore --config ghg.yml`, or the GUI. This "
                         "server does not start studies.")
    wire.close()


def test_the_records_words_for_the_gui_agent_are_said_as_they_apply_here(monkeypatch, tmp_path):
    """The record tells the GUI's Agent to `DO: run the fix` and to answer
    with a config; an AI app is told what those mean here. The record is
    made by its own code, with a study that can be continued, has a fix and
    has a withheld mean standing in for one that ran."""
    from types import SimpleNamespace

    from fastmdxplora.mcp import tools
    from fastmdxplora.simulation.resume import Continuation

    study = _study(tmp_path / "s", duration=1, means={}, started="2026-09-01T10:00:00+00:00")
    going_on = Continuation(parent=str(study), checkpoint=str(study / "end.chk"),
                            production_done_ns=1.0, production_planned_ns=2.0, config={})
    monkeypatch.setattr("fastmdxplora.simulation.resume.last_segment", lambda root: root)
    monkeypatch.setattr("fastmdxplora.simulation.resume.continuation_of",
                        lambda root, **kw: going_on)
    fix = SimpleNamespace(argv=("resume", str(study)), decision=False,
                          as_text=lambda: "fastmdx resume s; about 15 min here")
    monkeypatch.setattr("fastmdxplora.remedies.remedies_of", lambda here: [fix])
    asked = SimpleNamespace(as_text=lambda: "2 ns more for rmsd",
                            config=lambda root: {"simulation": {"extra_ns": 2.0}})
    monkeypatch.setattr("fastmdxplora.simulation.sampling_ask.sampling_asked_for",
                        lambda root: asked)

    for_the_agent = tools.study_record(study, for_the_agent=True)
    here = tools.study_record(study)
    for theirs, ours in tools._SAID_HERE:
        assert theirs in for_the_agent, theirs
        assert theirs not in here and ours in here



class TestWhatIsSaidBack:
    def test_arguments_of_the_wrong_shape_are_said_so(self, wire):
        listed = wire.request("tools/call", {"name": "list_studies", "arguments": None})
        assert not listed["result"]["isError"]
        shapes = [
            ({"name": "list_studies", "arguments": [1]}, "The arguments are an object"),
            ({"name": "ask_agent", "arguments": {"request": "x", "save": "yes"}},
             "`save` is true or false."),
            ({"name": "ask_agent", "arguments": {"request": "x", "phases": ["setup", "fold"]}},
             "`phases` is a list of: setup, simulation, analysis, report."),
            ({"name": "check_study", "arguments": {"config": "systems: [\n"}},
             "That is not YAML a config can be read from"),
            ({"name": "check_study", "arguments": {"config": "just: [1]\n- 2"}},
             "That is not YAML a config can be read from"),
            ({"name": "check_study", "arguments": {"config": "- 1\n- 2\n"}},
             "A config is a mapping of settings, as written to a file."),
            ({"name": "check_study", "arguments": {
                "config": "simulation:\n  resume_from: ../elsewhere\n  extra_ns: 1\n"}},
             "resume_from names ../elsewhere, outside the workspace."),
            ({"name": "save_study", "arguments": {"name": "///",
                                                  "config": "systems:\n  - system: ghg.pdb\n"}},
             "Name the file with letters or digits."),
        ]
        for params, said in shapes:
            result = wire.request("tools/call", params)["result"]
            assert result["isError"], params
            assert result["content"][0]["text"].startswith(said), (params, result)

    def test_a_continuation_and_a_machine_that_cannot_run_are_said(self, wire, workspace,
                                                                  monkeypatch):
        monkeypatch.setattr("fastmdxplora.mcp.tools._cannot_run_here",
                            lambda config: "OpenMM is not installed.")
        said = text_of(wire.request("tools/call", {"name": "check_study", "arguments": {
            "config": "simulation:\n  resume_from: ubq_10ns\n  extra_ns: 5\n"}}))
        assert ("It continues the study at ubq_10ns in place: its next segment, joined to "
                "the others, with the analyses rerun over the whole.") in said
        assert "\nThis machine cannot run it yet: OpenMM is not installed.\n" in said

    def test_a_mean_not_determined_is_listed_and_compared_as_such(self, wire, workspace):
        study = _study(workspace / "short", duration=0.1, means={"rmsd": (0.2, 0.01)},
                       started="2026-09-07T10:00:00+00:00")
        options = study / "analysis" / "rmsd" / "options.json"
        record = json.loads(options.read_text())
        record["findings"]["mean"]["not_a_measurement"] = "too few independent samples"
        options.write_text(json.dumps(record))
        listed = text_of(wire.request("tools/call", {"name": "list_studies", "arguments": {}}))
        assert "    RMSD: not determined (too few independent samples)" in listed
        compared = text_of(wire.request("tools/call", {"name": "compare_studies", "arguments": {
            "first": "short", "second": "ubq_10ns"}}))
        assert "the difference is not assessed: the first mean is not determined" in compared

    def test_a_standard_error_of_zero_is_said_as_zero(self, wire, workspace):
        """A series that never moved: its error is zero, said, and the
        difference is judged on the other's error alone, as the software
        combines them."""
        _study(workspace / "flat", duration=10, means={"rmsd": (0.13, 0.0)},
               started="2026-09-08T10:00:00+00:00")
        compared = text_of(wire.request("tools/call", {"name": "compare_studies", "arguments": {
            "first": "ubq_10ns", "second": "flat"}}))
        assert ("  RMSD: 0.1234 ± 0.0056 nm | 0.13 ± 0 nm; second minus first +0.0066 ± "
                "0.0056 nm, not resolved") in compared

    def test_why_a_difference_was_not_assessed_names_the_side(self):
        from fastmdxplora.mcp.tools import _why_not

        zero = {"mean": 1.0, "error": 0.0}
        some = {"mean": 1.0, "error": 0.1}
        assert _why_not({"first": zero, "second": zero}) == \
            "the first recorded a standard error of zero"
        assert _why_not({"first": some, "second": {"mean": 1.0, "error": None}}) == \
            "the second recorded no standard error"
        assert _why_not({"first": some, "second": some}) == "no standard error to judge it by"


def test_a_tool_that_fails_says_so_and_a_cursor_is_refused(workspace):
    from fastmdxplora.mcp.tools import Tool

    def breaks(ctx, args):
        raise RuntimeError("the disk is full")

    app = App(Workspace.at(workspace))
    app.tools = (Tool("breaks", "Breaks", "It breaks.", {}, (), {"readOnlyHint": True},
                      breaks),)
    wire = Wire(app.server())
    result = wire.request("tools/call", {"name": "breaks", "arguments": {}})["result"]
    assert result["isError"] and result["content"][0]["text"].endswith("the disk is full")
    assert wire.request("tools/list", {"cursor": "2"})["error"] == {
        "code": -32602, "message": "Unknown cursor: every list here is one page."}
    wire.close()


def test_the_workspace_reads_paths_as_a_shell_does(workspace):
    from fastmdxplora.refusals import StudyError

    place = Workspace.at(workspace)
    assert place.inside("") == place.root and place.shown(place.root) == "the workspace"
    assert place.inside("~no_such_user_here/x") is None
    assert place.shown("/somewhere/else") == "/somewhere/else"
    with pytest.raises(StudyError):
        Workspace.at(workspace / "not_a_folder")
