"""What the GUI, an AI app and the Agent are offered to run again on a study.

They run the phase command with `--rerun` (`fastmdx analyze`, `fastmdx
report`, or `fastmdx explore --include-phase analysis report` where the
study has a report), and `fastmdxplora.again` checks first what it would
do: the phases a study can run again where it is, the analyses this
release has, a study with frames to analyse, none of it running. The
command it names is run here as it is offered.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_a_phase_command_is_explore_with_one_phase import _analysed, _campaign, _made  # noqa: E402

from fastmdxplora import again  # noqa: E402
from fastmdxplora.cli.main import main  # noqa: E402
from fastmdxplora.refusals import StudyError  # noqa: E402


def _code(call) -> str:
    with pytest.raises(StudyError) as refused:
        call()
    return refused.value.code


def test_only_the_analysis_and_the_report_are_offered():
    assert again.phases_named(["report", "Analyse"]) == ("analysis", "report")
    assert _code(lambda: again.phases_named(["simulate"])) == "config.option.not_permitted"
    assert _code(lambda: again.phases_named(["dance"])) == "config.option.not_permitted"
    assert _code(lambda: again.phases_named([])) == "config.option.not_permitted"
    with pytest.raises(StudyError, match="simulation.setup_from"):
        again.phases_named(["setup"])


def test_the_analyses_are_this_release_s():
    assert again.analyses_named(None) is None
    assert again.analyses_named(["rg", "rmsd", "rg"]) == ("rg", "rmsd")
    with pytest.raises(StudyError, match="did you mean 'rmsd'"):
        again.analyses_named(["rmsdd"])
    assert _code(lambda: again.analyses_named([])) == "config.option.not_permitted"


def test_each_command_named_is_the_phase_command():
    study = Path("/s")
    named = {phases: again.Again(study, phases, analyses, (study,)).command()
             for phases, analyses in ((("report",), None), (("analysis",), ("rg",)),
                                      (("analysis", "report"), ("rg",)))}
    assert named[("report",)] == ["report", "--output", "/s", "--rerun"]
    assert named[("analysis",)] == ["analyze", "--output", "/s", "--rerun", "--analyses", "rg"]
    assert named[("analysis", "report")] == [
        "explore", "--output", "/s", "--include-phase", "analysis", "report", "--rerun",
        "--analyze-analyses", "rg"]
    assert again.Again(study, ("analysis",), None, (study,)).command() == [
        "analyze", "--output", "/s", "--rerun"]


def test_the_analysis_offered_runs_as_offered(tmp_path):
    root = _analysed(tmp_path / "study", report=False)
    planned = again.plan(root, ["analysis"], ["rg"])
    assert planned.phases == ("analysis",) and not planned.report_added
    assert planned.as_dict()["command"][:2] == ["fastmdx", "analyze"]

    assert main(planned.command()) == 0

    assert _made(root) == ["rg"] and _made(root / "previous") == ["rmsd"]


def test_what_is_not_a_study_or_has_no_frames_is_refused(tmp_path):
    assert _code(lambda: again.plan(tmp_path, ["report"])) == "environment.path.not_found"
    root = _analysed(tmp_path / "study", report=False)
    (root / "simulation" / "production.dcd").unlink()
    assert _code(lambda: again.plan(root, ["analysis"])) == "environment.path.not_found"
    offered = again.offered(root)
    assert not offered["analysis"]["can"] and offered["report"]["can"]
    assert offered["recorded"] == ["rmsd"]


def test_a_study_still_running_is_refused(tmp_path, monkeypatch):
    root = _analysed(tmp_path / "study")
    monkeypatch.setattr("fastmdxplora.simulation.resume._still_running",
                        lambda where: Path(where) == root.resolve())
    assert _code(lambda: again.plan(root, ["report"])) == "environment.workspace.run_going"


def test_a_study_of_several_says_each_run_and_any_left_out(tmp_path):
    root = _campaign(tmp_path)
    (root / "runs" / "b" / "simulation" / "production.dcd").unlink()
    planned = again.plan(root, ["analysis"])
    said = planned.said()
    assert planned.campaign == root.resolve()
    assert "The comparison of the runs is built again after." in said
    assert "Run b is left out: it has no trajectory to analyse." in said
    assert again.recorded_analyses(root) == ["rmsd"]
    alone = again.plan(root / "runs" / "a", ["report"])
    assert alone.campaign == root.resolve() and "this study" in alone.said()


def test_the_analyses_recorded_are_offered_before_any_ran(tmp_path):
    root = _analysed(tmp_path / "study", report=False)
    shutil.rmtree(root / "analysis")
    assert again.recorded_analyses(root) == ["rmsd"]
    (root / "resolved_config.yml").write_text("systems: [", encoding="utf-8")
    assert again.recorded_analyses(root) == []


def test_frames_from_outside_the_study_are_its_frames(tmp_path):
    source = _analysed(tmp_path / "source", report=False)
    root = tmp_path / "outside"
    assert main(["analyze", "--trajectory", str(source / "simulation" / "production.dcd"),
                 "--topology", str(source / "simulation" / "topology.pdb"),
                 "--output", str(root), "--analyses", "rg"]) == 0
    assert again.frames_of(root) == source / "simulation" / "production.dcd"
    (root / "resolved_config.yml").write_text("systems: [", encoding="utf-8")
    assert again.frames_of(root) is None


def _unfinished(root: Path, how: str) -> None:
    """An analysis stopped part-way (some of its outputs, no manifest), or
    one whose manifest is damaged: `--rerun` removes it with its report."""
    manifest = root / "analysis" / "analysis_manifest.json"
    if how == "part-way":
        manifest.unlink()
    else:
        manifest.write_text("{not json", encoding="utf-8")


@pytest.mark.parametrize("how", ["part-way", "damaged"])
def test_an_analysis_that_did_not_finish_is_said_removed_not_saved(tmp_path, how):
    from fastmdxplora.gui.again_view import again_fix

    root = _analysed(tmp_path / "study")
    _unfinished(root, how)
    planned = again.plan(root, ["analysis"])
    said = again.saved_said(planned)
    assert said == ("The analysis/ there now did not finish, so it is removed, with its "
                    "report/, not saved in previous/."), said
    assert said in planned.said()
    assert again.replaces_something(planned) and not again.saves_something(planned)
    assert again.done_said(planned) == ("What it replaced is removed, not saved in "
                                        "previous/, as its analysis did not finish.")
    fix = again_fix(root, "analyze again", {"analyses": ["rmsd"]})["fix"]["fix"]
    assert "previous/" not in fix and "removing the analysis there now" in fix, fix
    offered = again.offered(root)["analysis"]
    assert offered["replaces"] is True and offered["saves"] is False
    # As `make_way` does it: removed, nothing saved in previous/.
    assert main(planned.command()) == 0
    assert not (root / "previous").exists()
    # What previous/ holds of an analysis is named only where it holds one.
    _unfinished(root, how)
    assert again.saved_said(again.plan(root, ["analysis"])) == (
        "The analysis/ there now did not finish, so it is removed, with its report/, not "
        "saved in previous/.")
    (root / "previous" / "analysis").mkdir(parents=True)
    assert again.saved_said(again.plan(root, ["analysis"])).endswith(
        "not saved in previous/; what previous/ holds of it is left as it is.")


def test_only_what_the_study_holds_is_named_saved(tmp_path):
    root = _analysed(tmp_path / "study")
    shutil.rmtree(root / "analysis")
    planned = again.plan(root, ["analysis"])
    assert planned.phases == ("analysis", "report")
    assert again.saved_said(planned) == (
        "The report/ there now is saved in previous/, in place of what was saved there "
        "before.")
    assert again.done_said(planned) == "What it replaced is in previous/."
    both = again.plan(_analysed(tmp_path / "both"), ["analysis"])
    assert again.saved_said(both) == (
        "The analysis/ and report/ there now are saved in previous/, in place of what was "
        "saved there before.")
