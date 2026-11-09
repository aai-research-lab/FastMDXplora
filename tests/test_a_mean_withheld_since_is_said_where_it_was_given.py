"""A mean an earlier version called determined, which this version withholds,
is said so wherever its error was still given or plotted.

Review of `verdict-one`, round 3 (11-01): the Analysis table's Download gave
the error beside "Determined: no"; the report written by this version
embedded the figures the earlier version plotted with that error (its legend
and band, the thermodynamics' bar, the summary figure, the dashboard, the
slides), and the slides gave a withheld mean bare; the Report page showed an
earlier version's report with no word that this version withholds its means;
the Overview's lead and the Agent's "every mean withheld counted" left them
out; the replicas' sentence said a run "did not determine its own mean" that
it called determined; one footnote served two marks in the written
comparison; and the table rounded 9.5 independent samples to 10.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_a_run_s_overview_leads_with_what_it_needs import _a_run, _withheld
from tests.test_a_study_of_runs_is_shown_as_one import _recorded
from tests.test_an_earlier_verdict_is_read_once import EARLIER

STATIC = Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"

#: What is said under a figure an earlier version plotted with RMSD's error.
UNDER_RMSD = ("Plotted by an earlier version with an error this version withholds (RMSD: "
              "fewer than the 10 independent samples it determines a mean on). Analyze "
              "again to plot it again.")


@pytest.fixture
def root(tmp_path) -> Path:
    """A run whose RMSD an earlier version called determined on 7.3
    independent samples (its figure, `rmsd.png`, plotted then), and whose Rg
    is determined."""
    return _a_run(tmp_path, dict(EARLIER), _recorded(1.2))


def _table_as_rows(overview: dict) -> list[list[str]]:
    """The Analysis page's own Download rows (`tableAsRows`), from a payload."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    script = (STATIC / "analysis-page.js").read_text(encoding="utf-8")
    # With the count of frames averaged it says (`averaged`).
    functions = []
    for name in ("averaged", "tableAsRows"):
        start = script.index(f"  function {name}(")
        functions.append(script[start:script.index("\n  }\n", start) + 4])
    function = "\n".join(functions)
    probe = (f"var overview = {json.dumps(overview)};\n{function}\n"
             "console.log(JSON.stringify(tableAsRows()));")
    said = subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True)
    return json.loads(said.stdout)


def test_the_analysis_table_s_download_gives_no_error_where_the_mean_is_withheld(root):
    from fastmdxplora.gui.analysis_overview import overview_of

    # And a mean its own run withheld that still carries a finite error.
    (root / "analysis" / "rg" / "options.json").write_text(json.dumps({"findings": {"mean": {
        **_recorded(1.2), "not_a_measurement": "Still drifting over the frames it averages."}}}),
        encoding="utf-8")
    overview = overview_of(root, include_time=False, include_data=False)
    rows = {row[0]: row for row in _table_as_rows(overview)[1:]}
    assert (rows["RMSD"][2], rows["RMSD"][3], rows["RMSD"][8]) == ("0.0077659", "", "no")
    assert rows["Radius of gyration"][3] == "" and rows["Radius of gyration"][8] == "no"


def test_the_analysis_table_says_its_samples_as_every_page_does(root):
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.statistics import independent_samples_said

    (root / "analysis" / "rmsd" / "options.json").write_text(json.dumps({"findings": {"mean": {
        **EARLIER, "effective_samples": 9.5, "standard_error": None}}}), encoding="utf-8")
    row = next(r for r in overview_of(root, include_time=False)["rows"]
               if r["analysis"] == "rmsd")
    assert independent_samples_said(9.5) == "9 independent samples"
    assert row["mean"]["samples"] == 9
    assert row["mean"]["why"].startswith("About 9 independent samples, fewer than the 25")


def test_the_report_says_so_under_the_figure_an_earlier_version_plotted(root):
    from fastmdxplora.report.document import _results_section

    lines = _results_section(root).splitlines()
    at = next(k for k, line in enumerate(lines) if "rmsd.png)" in line)
    assert lines[at + 2].replace("\\", "") == f"_{UNDER_RMSD}_"
    # Not under a figure of a mean determined.
    at = next(k for k, line in enumerate(lines) if "rg.png)" in line)
    assert "earlier version" not in " ".join(lines[at + 1:at + 3])


def test_the_thermodynamics_figure_and_the_summary_figure_too(root):
    from fastmdxplora.report.document import _results_section

    here = root / "analysis" / "thermodynamics"
    here.mkdir()
    (here / "thermodynamics.png").write_bytes(b"")
    (here / "options.json").write_text(json.dumps({"findings": {"thermodynamics": {
        "samples": 20,
        "density": {"units": "g/mL", "mean": 0.9988, "standard_error": 0.0025,
                    "effective_samples": 16.0, "rules": 3},
        "potential_energy": {"units": "kJ/mol", "mean": -19798.3, "standard_error": 42.5,
                             "effective_samples": 7.3, "rules": 2}}}}), encoding="utf-8")
    manifest = root / "analysis" / "analysis_manifest.json"
    record = json.loads(manifest.read_text())
    record["plan"].append("thermodynamics")
    record["results"]["thermodynamics"] = {"status": "ok"}
    manifest.write_text(json.dumps(record), encoding="utf-8")
    (root / "report" / "analysis_summary.png").write_bytes(b"")
    (root / "report" / "analysis_summary_manifest.json").write_text(json.dumps({"included": [
        {"panel": "A", "title": "RMSD over frames", "source": "analysis/rmsd/rmsd.png"},
        {"panel": "B", "title": "Radius of gyration", "source": "analysis/rg/rg.png"},
        {"panel": "C", "title": "Thermodynamics",
         "source": "analysis/thermodynamics/thermodynamics.png"}]}), encoding="utf-8")
    text = _results_section(root).replace("\\", "")
    assert ("_Plotted by an earlier version with an error this version withholds (Potential "
            "energy: fewer than the 10 independent samples") in text
    assert ("_Panels A and C were plotted by an earlier version with errors this version "
            "withholds (RMSD and Potential energy: fewer than the 10") in text


def test_the_dashboard_s_caption_says_so(root):
    from fastmdxplora.gui.report_dashboard import _analysis_sections

    panels = [p for s in _analysis_sections(root, root / "report", {}) for p in s.panels]
    rmsd = next(p for p in panels if p.source == "analysis/rmsd/rmsd.png")
    rg = next(p for p in panels if p.source == "analysis/rg/rg.png")
    assert rmsd.summary.endswith(UNDER_RMSD)
    assert "earlier version" not in rg.summary


def test_the_slides_say_so_and_give_the_mean_as_not_determined(root):
    pptx = pytest.importorskip("pptx")
    from PIL import Image

    from fastmdxplora.report.slides import _build_pptx

    for name in ("rmsd", "rg"):
        Image.new("RGB", (60, 40), "white").save(root / "analysis" / name / f"{name}.png")
    out = root / "report" / "slides.pptx"
    _build_pptx(SimpleNamespace(output_dir=root, system="a.pdb"), "A run", out)
    said = ["\n".join(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame)
            for slide in pptx.Presentation(str(out)).slides]
    rmsd = next(text for text in said if text.startswith("RMSD over frames"))
    assert UNDER_RMSD in rmsd
    findings = next(text for text in said if text.startswith("What the run supports"))
    line = next(x for x in findings.splitlines() if x.startswith("rmsd:"))
    assert line.startswith("rmsd: 0.007766, not determined"), line


def test_the_report_page_says_how_many_of_an_earlier_report_s_means_are_withheld(root):
    from fastmdxplora.gui.report_page import report_payload
    from fastmdxplora.statistics import MEAN_RULES

    said = report_payload(root, trace=False).get("withheld_since", "")
    assert said == ("Written by an earlier version, which called 1 of its means determined "
                    "that this version withholds (RMSD: fewer than the 10 independent samples "
                    "it determines a mean on). Write it again to give this version's verdict.")
    # Written again by this version, which says so of each mean itself.
    (root / "report" / "records_read.json").write_text(json.dumps(
        {"start_shared_with_replicas": {}, "mean_rules": MEAN_RULES}), encoding="utf-8")
    assert report_payload(root, trace=False).get("withheld_since", "") == ""


def test_a_report_written_by_this_version_records_its_rules(root):
    from fastmdxplora.report.document import _save_the_starts_read
    from fastmdxplora.statistics import MEAN_RULES

    _save_the_starts_read(root, root / "report")
    read = json.loads((root / "report" / "records_read.json").read_text())
    assert read.get("mean_rules") == MEAN_RULES


def test_the_overview_s_lead_counts_them(root):
    from fastmdxplora.gui.overview_view import overview_payload

    assert overview_payload(root)["lead"]["said"] == (
        "1 of 2 means is not determined. This version withholds it, which an earlier "
        "version called determined: Analyze again to record this version's verdict.")


def test_every_mean_withheld_counted_counts_them(root):
    from fastmdxplora.gui.overview_view import overview_payload
    from fastmdxplora.gui.records_answer import _long_enough

    (root / "analysis" / "sasa").mkdir()
    (root / "analysis" / "sasa" / "options.json").write_text(json.dumps(
        {"findings": {"mean": _withheld(2.43)}}), encoding="utf-8")
    lead = overview_payload(root)["lead"]["said"]
    assert lead.startswith("2 of 3 means are not determined: about 2.5 ns more production "
                           "should determine the 1 withheld for want of sampling"), lead
    assert ("This version withholds 1 of the 2, which an earlier version called determined: "
            "Analyze again to record this version's verdict.") in lead
    plan = next(x for x in _long_enough(root).split("\n\n")
                if x.startswith("What they need, every mean withheld counted: "))
    assert plan.endswith("RMSD: called determined by an earlier version and withheld by this "
                         "one; Analyze again to record this version's verdict."), plan


def test_the_replicas_sentence_does_not_contradict_itself():
    from fastmdxplora.batch.aggregate import _spread_not_an_error

    one = _spread_not_an_error(1, 3, 0, withheld=1)
    assert one.startswith("1 of the 3 replicas' means, which its run called determined, this "
                          "version withholds on fewer than 10 independent samples, so their "
                          "spread is not yet an error"), one
    mixed = _spread_not_an_error(2, 3, 1, withheld=1)
    assert mixed.startswith("2 of the 3 replicas' means are not determined: 1 said its mean "
                            "is not determined, and this version withholds the mean 1 of "
                            "them called determined on fewer than 10"), mixed
    assert "did not determine" not in one + mixed


def test_the_written_comparison_marks_the_two_kinds_apart(tmp_path):
    from fastmdxplora.batch.compare import build_comparison_report
    from tests.test_a_verdict_withheld_is_withheld_everywhere import _three_seeds

    root = _three_seeds(tmp_path)
    # Seed 2's RMSD withheld by its own run.
    options = root / "runs" / "s2" / "analysis" / "rmsd" / "options.json"
    record = json.loads(options.read_text())
    record["findings"]["mean"]["not_a_measurement"] = "Still drifting over the frames it averages."
    options.write_text(json.dumps(record), encoding="utf-8")
    build_comparison_report(root)
    lines = (root / "comparison" / "comparison_report.md").read_text(encoding="utf-8").splitlines()
    row = {line.split(" | ")[0][2:]: line for line in lines if line.startswith("| s")}
    assert row["s2"].rstrip(" |").endswith("\\*") and "‡" not in row["s2"]
    assert row["s3"].rstrip(" |").endswith("‡") and "\\*" not in row["s3"]
    star = next(x for x in lines if x.startswith("\\* Not determined"))
    mark = next(x for x in lines if x.startswith("‡ Not determined"))
    assert "this version withholds it" in mark and "this version" not in star
