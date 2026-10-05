"""A phase command is `explore` with one phase, and a study is written over
only when asked.

`fastmdx analyze` ran a code path of its own beside `explore`'s, with rules of
its own: it wrote over a study's analysis without a word, leaving the
analyses it did not run again beside the new ones and the report describing
the old ones; it read none of the settings the study recorded; and asked to
analyse a trajectory from elsewhere it wanted a system as well. Each phase
command is now `explore --include-phase <phase>`, run through it: on a study
it starts from the study's record; into a folder that holds what it would
write it needs `--force-overwrite`, which removes that and the output after
it, or `--rerun`, which keeps them in `previous/<phase>`.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from test_report_wiring import _make_traj_files  # noqa: E402

from fastmdxplora import FastMDXplora  # noqa: E402
from fastmdxplora.cli.main import main  # noqa: E402
from fastmdxplora.orchestrator import RUN_PROCESS_FILE  # noqa: E402

REPORT = {"title": "As recorded", "slides": False, "pdf": False, "bundle": False}


def _analysed(root: Path, analyses=("rmsd",), n_frames: int = 30, report: bool = True,
              **analysis) -> Path:
    _make_traj_files(root, n_residues=4, n_frames=n_frames)
    fmdx = FastMDXplora(system=str(root / "simulation" / "topology.pdb"), output_dir=root)
    fmdx.explore(include_phase=["analysis", "report"] if report else ["analysis"],
                 options={"analysis": {"include": list(analyses), **analysis},
                          "report": REPORT})
    # Run in this process, the record of the run going names it until it
    # exits; a run here has ended, as one started apart would have.
    (root / RUN_PROCESS_FILE).unlink(missing_ok=True)
    return root


def _made(root: Path) -> list[str]:
    return list(json.loads((root / "analysis" / "analysis_manifest.json").read_text())["results"])


def _phases(root: Path) -> list[str]:
    return [entry["name"] for entry in json.loads((root / "manifest.json").read_text())["phases"]]


def test_a_study_s_phase_is_written_over_only_when_asked(tmp_path, capsys):
    root = _analysed(tmp_path / "study")
    assert main(["analyze", "--output", str(root), "--analyses", "rg"]) == 2
    said = capsys.readouterr().err
    assert "--force-overwrite" in said and "--rerun" in said
    assert _made(root) == ["rmsd"]


def test_force_overwrite_removes_what_it_replaces_and_what_it_left_stale(tmp_path, capsys):
    root = _analysed(tmp_path / "study", analyses=("rmsd", "rg"))

    assert main(["analyze", "--output", str(root), "--analyses", "rg",
                 "--force-overwrite"]) == 0

    said = capsys.readouterr().out
    assert _made(root) == ["rg"]
    # The analysis left out is gone, not kept beside the new one.
    assert sorted(p.name for p in (root / "analysis").iterdir()) == ["analysis_manifest.json", "rg"]
    assert not (root / "report").exists() and not (root / "previous").exists()
    assert _phases(root) == ["analysis"]
    assert f"`fastmdx report --output {root}` writes it again" in said


def test_rerun_keeps_one_copy_of_each_in_previous(tmp_path, capsys):
    root = _analysed(tmp_path / "study")

    assert main(["analyze", "--output", str(root), "--analyses", "rmsd", "rg", "--rerun"]) == 0
    assert _made(root) == ["rmsd", "rg"] and _made(root / "previous") == ["rmsd"]
    assert (root / "previous" / "report" / "report.md").is_file()
    assert json.loads((root / "previous" / "report" / "phase_record.json").read_text())[
        "name"] == "report"

    assert main(["analyze", "--output", str(root), "--analyses", "rg", "--rerun"]) == 0
    assert _made(root / "previous") == ["rmsd", "rg"]

    assert main(["report", "--output", str(root)]) == 0      # none to write over
    assert (root / "report" / "report.md").read_text().startswith("# As recorded")
    assert _phases(root) == ["analysis", "report"]


def test_a_study_still_running_is_not_written_over(tmp_path, capsys, monkeypatch):
    root = _analysed(tmp_path / "study")
    monkeypatch.setattr("fastmdxplora.simulation.resume._still_running",
                        lambda where: Path(where) == root)

    assert main(["analyze", "--output", str(root), "--rerun"]) == 1

    said = capsys.readouterr()
    assert "is still running. Run its phases again once it has stopped." in said.err
    assert _made(root) == ["rmsd"] and (root / "report").is_dir()
    assert not (root / "previous").exists()


def test_both_flags_together_are_refused():
    with pytest.raises(SystemExit) as stopped:
        main(["analyze", "--output", "x", "--rerun", "--force-overwrite"])
    assert stopped.value.code == 2


def test_the_report_starts_from_the_study_s_record(tmp_path):
    root = _analysed(tmp_path / "study")
    assert main(["report", "--output", str(root), "--rerun"]) == 0
    assert (root / "report" / "report.md").read_text().startswith("# As recorded")
    assert not (root / "report" / "slides.pptx").exists()

    assert main(["report", "--output", str(root), "--rerun", "--title", "From the flag"]) == 0
    assert (root / "report" / "report.md").read_text().startswith("# From the flag")


def test_a_record_that_cannot_be_used_is_said_to_be_the_record_s(tmp_path, capsys):
    root = _analysed(tmp_path / "study", report=False)
    record = yaml.safe_load((root / "resolved_config.yml").read_text())
    record["report"] = {"title": 5}
    (root / "resolved_config.yml").write_text(yaml.safe_dump(record))

    assert main(["report", "--output", str(root)]) == 2

    said = capsys.readouterr().err
    assert f"the settings {root / 'resolved_config.yml'} recorded cannot be used" in said
    assert "give the settings with -c FILE" in said
    assert not (root / "report").exists()


def test_analyses_named_replace_those_recorded_as_left_out(tmp_path):
    root = _analysed(tmp_path / "study", analyses=(), report=False)
    shutil.rmtree(root / "analysis")
    config = yaml.safe_load((root / "resolved_config.yml").read_text())
    config["analysis"] = {"exclude": ["cluster", "dimred"]}
    (root / "resolved_config.yml").write_text(yaml.safe_dump(config))

    assert main(["analyze", "--output", str(root), "--analyses", "rg"]) == 0
    assert _made(root) == ["rg"]


def test_the_study_s_record_still_runs_it_whole(tmp_path):
    root = _analysed(tmp_path / "study")
    recorded = yaml.safe_load((root / "resolved_config.yml").read_text())["include_phase"]

    assert main(["analyze", "--output", str(root), "--rerun"]) == 0

    assert yaml.safe_load((root / "resolved_config.yml").read_text())["include_phase"] == recorded
    assert recorded == ["analysis", "report"]


def test_a_moved_study_is_analysed_from_its_own_frames(tmp_path):
    first = _analysed(tmp_path / "first", analyses=("rg",), n_frames=40, report=False)
    moved = tmp_path / "moved"
    shutil.move(str(first), str(moved))
    _make_traj_files(first, n_residues=4, n_frames=25)

    assert main(["analyze", "--output", str(moved), "--rerun"]) == 0

    manifest = json.loads((moved / "analysis" / "analysis_manifest.json").read_text())
    assert manifest["n_frames"] == 40 and list(manifest["results"]) == ["rg"]


def test_a_trajectory_from_elsewhere_needs_no_system(tmp_path):
    source = tmp_path / "source"
    _make_traj_files(source, n_residues=4, n_frames=20)

    assert main(["analyze", "--trajectory", str(source / "simulation" / "production.dcd"),
                 "--topology", str(source / "simulation" / "topology.pdb"),
                 "--output", str(tmp_path / "new"), "--analyses", "rg"]) == 0
    assert _made(tmp_path / "new") == ["rg"]


def _campaign(tmp_path: Path) -> Path:
    root = tmp_path / "campaign"
    systems = []
    for name, frames in (("a", 30), ("b", 35)):
        source = tmp_path / f"source_{name}"
        _make_traj_files(source, n_residues=4, n_frames=frames)
        held = root / "runs" / name / "simulation"
        held.mkdir(parents=True)
        for each in ("production.dcd", "topology.pdb"):
            shutil.copy(source / "simulation" / each, held / each)
        systems.append({"id": name, "system": str(source / "simulation" / "topology.pdb")})
    FastMDXplora(config_data={"systems": systems, "include_phase": ["analysis"],
                              "analysis": {"include": ["rmsd"]}, "output": str(root)},
                 output_dir=str(root)).explore()
    for run in root.glob("runs/*"):
        (run / RUN_PROCESS_FILE).unlink(missing_ok=True)
    return root


def test_a_study_of_several_runs_is_run_again_run_by_run(tmp_path):
    root = _campaign(tmp_path)

    assert main(["analyze", "--output", str(root), "--analyses", "rmsd", "rg", "--rerun"]) == 0

    for name in ("a", "b"):
        assert _made(root / "runs" / name) == ["rmsd", "rg"]
        assert _made(root / "runs" / name / "previous") == ["rmsd"]
    assert "rg" in (root / "comparison" / "comparison_report.md").read_text()


def test_one_run_analysed_again_has_its_study_s_comparison_built_again(tmp_path, capsys):
    root = _campaign(tmp_path)
    (root / "comparison" / "comparison_report.md").unlink()
    (root / "members.json").unlink()

    assert main(["analyze", "--output", str(root / "runs" / "a"),
                 "--analyses", "rmsd", "rg", "--rerun"]) == 0

    assert _made(root / "runs" / "a") == ["rmsd", "rg"]
    assert _made(root / "runs" / "b") == ["rmsd"]
    assert (root / "comparison" / "comparison_report.md").is_file()
    assert (root / "members.json").is_file()
    assert "The comparison of campaign's runs, built again" in capsys.readouterr().out


def test_the_python_api_keeps_the_previous_too(tmp_path):
    root = _analysed(tmp_path / "study", report=False)
    FastMDXplora(system=str(root / "simulation" / "topology.pdb"), output_dir=root).explore(
        include_phase=["analysis"], options={"analysis": {"include": ["rg"]}},
        keep_previous=True)
    assert _made(root) == ["rg"] and _made(root / "previous") == ["rmsd"]


def test_a_simulation_run_again_takes_its_pieces_with_it(tmp_path):
    from fastmdxplora.replaced import make_way

    root = tmp_path / "study"
    for name in ("simulation", "joined", "segment-001", "analysis"):
        (root / name).mkdir(parents=True)
        (root / name / "x").write_text("1")
    said: list[str] = []

    assert make_way(root, ["simulation"], keep=True, say=said.append) == [
        "simulation", "analysis"]
    kept = root / "previous" / "simulation"
    assert sorted(p.name for p in kept.iterdir()) == ["joined", "segment-001", "x"]
    assert not (root / "joined").exists() and (root / "previous" / "analysis" / "x").is_file()
    assert said[1].startswith("analysis: written from the simulation before, so kept in")


def test_the_bundle_leaves_what_was_replaced_out(tmp_path):
    from fastmdxplora.report.bundle import _iter_project_files

    root = tmp_path / "study"
    (root / "previous" / "analysis").mkdir(parents=True)
    (root / "previous" / "analysis" / "old.dat").write_text("1")
    (root / "analysis").mkdir()
    (root / "analysis" / "new.dat").write_text("1")

    found = [p.relative_to(root).as_posix() for p in
             _iter_project_files(root, root / "report" / "project_bundle.zip")]
    assert found == ["analysis/new.dat"]
