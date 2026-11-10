"""A look gives the analysis asked about first, and a reason said once.

A look gives the AI model 4,000 characters. The record of a short run, each
analysis not determined saying the same rule at length, was cut in the
RMSD entry of the 1L2Y study the `context` run was asked against, and the
analyses after it never reached the Agent (version 2's Results). Now a
reason gives its own first sentence on its analysis's line and the rest
once under the list, and `read_study` gives the analysis asked about first:
named with `analysis`, by the Report's section or the
file open in the reader, or by an analysis named with @. The analysis
asked about keeps its reasons whole, and notes are numbered from the rows
shown only.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from fastmdxplora.agent.context import named_in
from fastmdxplora.agent.tools import MOST_SAID, Toolbox, current_view_tool
from fastmdxplora.gui.agent_panel import _analysis_named, _results_summary

#: The reason a mean is withheld, as the 1L2Y study recorded it for each
#: analysis whose run was short against its correlation time.
SHORT = ("This run is not long against its own correlation time: {frames} frames hold "
         "{n} independent samples by their own estimate, and a correlated run shorter "
         "than 25 of them reads its correlation time low. That count is an upper bound, "
         "so an error computed from it would be a lower bound -- and none is reported "
         "here rather than one that is wrong in a knowable direction. On ten replicas of "
         "one system differing only by seed, errors computed from one run were about ten "
         "times smaller than the spread of the ten means. The remedy is a longer run, or "
         "better, replicas.")
TAIL = SHORT.split("reads its correlation time low. ", 1)[1]


def _finding(mean, n, *, withheld=False, error=None):
    found = {"mean": mean, "effective_samples": n, "discard": 1000, "n_frames": 2000,
             "unit": "nm"}
    if error is not None:
        found["standard_error"] = error
    if withheld:
        found["not_a_measurement"] = SHORT.format(frames=1000, n=n)
    return found


def _short_study(where: Path, withheld: int = 6) -> Path:
    """A study like the 1L2Y one: analyses before `rmsd` whose means are not
    determined, each saying why at length, and `rmsd` and `rg` determined."""
    where.mkdir(parents=True)
    (where / "exploration.yml").write_text(yaml.safe_dump({
        "systems": [{"system": "1L2Y"}], "simulation": {"duration_ns": 0.5}}))
    (where / "manifest.json").write_text(json.dumps({"phases": []}))
    findings = {f"a{n:02d}_shape": {"mean": _finding(1.1, 14.5 + n, withheld=True)}
                for n in range(withheld)}
    findings["rg"] = {"mean": _finding(0.71841, 107.2, error=0.00051)}
    findings["rmsd"] = {"mean": _finding(0.0645, 41.0, error=0.0017)}
    findings["sasa"] = {"mean": _finding(19.409, 60.0, error=0.026)}
    for name, found in findings.items():
        folder = where / "analysis" / name
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps({"analysis": name, "findings": found}))
    return where


def test_a_reason_gives_its_first_sentence_and_the_rest_once(tmp_path):
    said = _results_summary(_short_study(tmp_path / "s"))
    assert said.count(TAIL) == 1
    assert said.count("reads its correlation time low. [1]") == 6
    assert "the rest of each reason marked [n] above, said once:\n  [1] That count is" in said
    # Each line keeps its own numbers.
    assert "1000 frames hold 14.5 independent samples" in said
    assert "1000 frames hold 19.5 independent samples" in said


def test_the_analysis_asked_about_is_found_first(tmp_path):
    study = _short_study(tmp_path / "s")
    said = _results_summary(study, first="rmsd")
    lines = said.splitlines()
    assert lines[1].startswith("  rmsd: mean 0.0645 ± 0.0017 nm (s.e.)")
    assert said.splitlines()[1:] != _results_summary(study).splitlines()[1:]


def test_an_analysis_is_named_by_its_folder_or_its_heading(tmp_path):
    study = _short_study(tmp_path / "s")
    assert _analysis_named(study, "rmsd") == "rmsd"
    assert _analysis_named(study, " Radius  of gyration ") == "rg"
    assert _analysis_named(study, "RMSD") == "rmsd"
    assert _analysis_named(study, "ligand_rmsd") is None
    assert _analysis_named(study, "") is None and _analysis_named(None, "rmsd") is None


def test_the_look_holds_the_analysis_asked_about_whole(tmp_path):
    """The case of the `context` run: a study whose record ran past the
    look's length before `rmsd`. Asked about `rmsd`, its entry is whole, the
    first of what the analyses found, after the study's short config."""
    study = _short_study(tmp_path / "s", withheld=14)
    box = Toolbox(open_study=study)
    look = box.use("read_study", {"study": str(study), "analysis": "rmsd"})
    assert look.ok
    after = look.said.split("what the analyses found:\n", 1)[1]
    assert after.startswith("  rmsd: mean 0.0645 ± 0.0017 nm (s.e.), 41 effective samples, "
                            "first 1000 of 2000 frames discarded as unequilibrated\n")
    assert look.said.index("the config this run used") < look.said.index(
        "what the analyses found")
    assert "duration_ns: 0.5" in look.said
    assert len(look.said) <= MOST_SAID + len(" ... (cut)")
    unknown = box.use("read_study", {"study": str(study), "analysis": "ligand_rmsd"})
    assert "holds no analysis 'ligand_rmsd'" in unknown.said


def test_the_page_s_look_reads_the_analysis_it_is_on(tmp_path):
    study = _short_study(tmp_path / "s", withheld=14)
    for hints, first in (
            ({"page": "report", "study": str(study), "section": "RMSD"}, "rmsd"),
            ({"page": "analysis", "study": str(study), "analysis": "rg"}, "rg"),
            ({"page": "files", "study": str(study), "file": "analysis/rg/options.json"}, "rg")):
        tool = current_view_tool(hints, active_root=str(study))
        said = Toolbox(extra=(tool,), open_study=study).use("current_view", {}).said
        found = said.split("Factual evidence from read_study:\n", 1)[1]
        assert found.split("what the analyses found:\n", 1)[1].startswith(f"  {first}: mean"), \
            hints


def test_an_analysis_named_with_at_is_read_with_it_first(tmp_path):
    study = _short_study(tmp_path / "s")
    said = named_in("What did @rmsd come to?", Toolbox(), study)
    assert f"read_study (study: {study}, analysis: rmsd)" in said


def test_a_record_like_the_1l2y_study_s_now_fits_one_look(tmp_path):
    """Six analyses not determined, as in the 1L2Y study: each saying the
    rule whole ran past the look's length; said once, the whole record fits,
    its note included."""
    study = _short_study(tmp_path / "s", withheld=6)
    look = Toolbox(open_study=study).use("read_study", {"study": str(study)})
    assert look.ok and not look.said.endswith("(cut)")
    # The record as it was: each reason whole on its line, and no note.
    as_it_was = look.said.split("\nthe rest of each reason marked", 1)[0].replace(
        " [1]", " " + TAIL)
    assert len(as_it_was) > MOST_SAID >= len(look.said)
    assert "  sasa: mean 19.409 ± 0.026 nm (s.e.)" in look.said
    assert look.said.count(TAIL) == 1


def test_the_analysis_asked_about_keeps_its_reason_whole(tmp_path):
    """In a long record the notes at the end are what is cut, so the
    analysis asked about says its reasons on its own line."""
    study = _short_study(tmp_path / "s", withheld=14)
    said = _results_summary(study, first="a03_shape")
    asked = said.splitlines()[1]
    assert asked.startswith("  a03_shape: mean") and asked.endswith(TAIL)
    assert "[" not in asked
    box = Toolbox(open_study=study)
    look = box.use("read_study", {"study": str(study), "analysis": "a03_shape"}).said
    assert TAIL in look.split("what the analyses found:\n", 1)[1].splitlines()[0]


def test_notes_are_numbered_from_the_rows_shown(tmp_path):
    """Past twenty rows a reason is not shown, and neither is its note."""
    study = _short_study(tmp_path / "s", withheld=3)
    other = SHORT.replace("The remedy is a longer run", "The cure is a longer run")
    for n in range(20):
        folder = study / "analysis" / f"z{n:02d}_late"
        folder.mkdir(parents=True)
        found = _finding(1.0, 12.0, withheld=True)
        found["not_a_measurement"] = (other if n == 19 else SHORT).format(frames=1000, n=12.0)
        (folder / "options.json").write_text(json.dumps(
            {"analysis": folder.name, "findings": {"mean": found}}))
    said = _results_summary(study)
    assert len(said.split("\nthe rest of each reason marked", 1)[0].splitlines()) == 21
    assert "The cure is" not in said
    assert said.count("  [1] ") == 1 and "[2]" not in said


def test_a_reason_as_summarise_gives_it_today_is_said_once(tmp_path):
    """The reason as the statistics give it now, its parenthesis included."""
    import numpy as np

    from fastmdxplora.statistics import summarise

    walk = np.cumsum(np.random.default_rng(0).normal(size=400)) * 0.01
    _, why = summarise(walk + np.sin(np.arange(400) / 30))
    reason = str(why)
    first, rest = reason.split(" are exempt). ", 1)
    study = _short_study(tmp_path / "s", withheld=0)
    for name in ("a_shape", "b_shape"):
        folder = study / "analysis" / name
        folder.mkdir(parents=True)
        found = _finding(1.0, 4.3)
        found["not_a_measurement"] = reason
        (folder / "options.json").write_text(json.dumps(
            {"analysis": name, "findings": {"mean": found}}))
    said = _results_summary(study)
    assert said.count(first + " are exempt). [1]") == 2
    assert said.count(rest) == 1 and said.endswith(f"  [1] {rest}")


def test_a_legacy_folder_is_named_by_its_heading(tmp_path):
    study = _short_study(tmp_path / "s")
    (study / "analysis" / "contacts").mkdir()
    assert _analysis_named(study, "Protein-ligand contacts") == "contacts"


def test_an_analysis_asked_about_with_nothing_recorded_is_said_so(tmp_path):
    study = _short_study(tmp_path / "s")
    (study / "analysis" / "rmsf").mkdir()
    said = _results_summary(study, first="rmsf")
    assert said.splitlines()[1] == "  rmsf: nothing it recorded gives a mean or a reason"
    assert said.count("  rmsd: mean") == 1
