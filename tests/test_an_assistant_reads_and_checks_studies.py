"""An assistant reads and checks studies through `fastmdx mcp`.

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
        assert listed["inputSchema"] == {"type": "object", "properties": {},
                                         "additionalProperties": False}

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
        assert f"plan_id: {digest} (for start_study; it changes if ghg.yml does)" in said

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
        assert said.startswith("Saved to ghg_run_2.yml; accepted by the validator. Nothing "
                               "has been run.")
        assert (workspace / "ghg_run_2.yml").read_text() == text
        again = call(wire, "save_study", name="ghg_run_2.yml", config="systems: []\n")
        assert again["isError"]
        assert (workspace / "ghg_run_2.yml").read_text() == text
        twice = call(wire, "save_study", name="ghg_run_2", config=text)
        assert "is never written over" in twice["content"][0]["text"]
        assert call(wire, "save_study", name="x", config="ghg.yml")["isError"]


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
        assert "    RMSD: 0.15 ± 0.004 nm" in lines
        assert lines[-1] == "Config files here: ghg.yml"

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
        assert "RMSD: 0.1234 ± 0.0056 nm | 0.15 ± 0.004 nm; second minus first +0.0266 ± " \
               "0.0069 nm, resolved" in said
        assert "+0.001 ± 0.0036 nm, not resolved" in said


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
