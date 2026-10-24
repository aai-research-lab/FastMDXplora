"""A mean noted for the solute's own periodic image says so on every page
that shows it, and to the Agent, beside its verdict, which it does not
change.

The report and the Report page said of an RMSD "may be biased: the solute
met its own periodic image in 108 of 108 frames used", while the Overview
tile and the Analysis page's table said "Determined 0.1046 ± 0.0013 nm" and
nothing else, and the Agent said "determined" with no caveat: one mean, two
verdicts. Every reader now reads the note from the record through one
reader (`fastmdxplora.image_note`).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.image_note import said_short
from tests.test_the_analysis_page_reads_as_a_whole import _analysis, _ar1, _manifest

N = 2000
#: Frames 0, 5, 10, ... came within the cutoff of the solute's own image.
EVERY = 5


def _finding() -> tuple[dict, np.ndarray]:
    """The image check's finding and series: within 1 nm of its image in
    every fifth frame, 0.42 nm the closest."""
    series = np.full(N, 2.0)
    series[::EVERY] = 0.8
    series[EVERY * 7] = 0.42
    finding = {
        "cutoff_nm": 1.0, "cutoff_from": "taken because no study was given", "close_nm": 0.5,
        "frames": N, "nearest_nm": 0.42, "nearest_frame": EVERY * 7,
        "frames_under_cutoff": N // EVERY, "frames_under_close": 1,
        "reaches_its_image": True, "solute": "the macromolecules (10 residues in 1 chain); "
                                            "water, ions and lipids left out",
        "said": ("The solute came within 0.420 nm of its own periodic image, inside the 1 nm "
                 f"cutoff in {N // EVERY} of {N} frames (20%) and in contact with it (under "
                 "0.5 nm) in 1 (0.05%). The closest was at frame 35, between ALA1 CA and "
                 "the image of ALA10 CA (heavy atoms only)."),
    }
    return finding, series


def _noted(root: Path, name: str, finding: dict, series: np.ndarray) -> dict:
    """The analysis's record noted as the analysis code notes it (one
    function, `note_on_frames`), over the frames its mean uses."""
    from fastmdxplora.analysis.image_check import note_numbers, note_on_frames

    path = root / "analysis" / name / "options.json"
    record = json.loads(path.read_text())
    findings = record["findings"]
    for key, mean in list(findings.items()):
        if isinstance(mean, dict) and "n_frames" in mean:
            note = note_on_frames(finding, series, np.arange(mean["discard"], N),
                                  analysis=name)
            mean["solute_image"] = note_numbers(note)
            if key == "mean":
                findings["solute_image"] = note
    path.write_text(json.dumps(record))
    return findings


@pytest.fixture
def study(tmp_path: Path) -> Path:
    root = tmp_path / "study"
    _manifest(root, {"image_check": {"status": "ok"}, "rmsd": {"status": "ok"},
                     "sasa": {"status": "ok"}})
    finding, series = _finding()
    folder = root / "analysis" / "image_check"
    folder.mkdir(parents=True)
    (folder / "image_check.dat").write_text("".join(f"{v}\n" for v in series))
    (folder / "options.json").write_text(json.dumps(
        {"analysis": "image_check", "selection": "protein",
         "findings": {"solute_image": finding}}))
    _analysis(root, "rmsd", 0.30 + 0.002 * _ar1(N, 0.5, 1))
    _analysis(root, "sasa", 91.0 + 0.3 * _ar1(N, 0.5, 3), unit="nm²",
              others={"hydrophobic_sasa": 45.0 + 0.1 * _ar1(N, 0.5, 4)})
    for name in ("rmsd", "sasa"):
        _noted(root, name, finding, series)
    return root


def _expected(root: Path, name: str, key: str = "mean") -> str:
    record = json.loads((root / "analysis" / name / "options.json").read_text())
    return said_short(record["findings"][key]["solute_image"])


class TestTheShortForm:
    def test_it_says_the_frames_the_cutoff_and_the_solute_s_own_image(self, study):
        said = _expected(study, "rmsd")
        assert said.startswith("may be biased: ")
        # The solute was within, in so many frames: frames do not come within.
        assert said.startswith("may be biased: in ") and " frames used (" in said
        assert said.endswith(") the solute was within 1 nm of its own image")

    def test_it_says_the_run_s_own_cutoff(self):
        assert said_short(_NOTE_09) == ("may be biased: in 100 of 100 frames used (100%) the "
                                        "solute was within 0.9 nm of its own image")
        assert said_short({**_NOTE_09, "cutoff_nm": None}).endswith(
            "the solute was within the cutoff of its own image")


class TestHowANoteReads:
    """A sub-mean's paragraph gave the main mean's counts (polar SASA's line
    293 of 2,693, its paragraph 344 of 2,744); the end-to-end tile said the
    image three times, its reason and then its paragraph saying the same;
    and the paragraphs showed the report's code marks raw on the pages."""

    FINDINGS = {
        "solute_image": {"frames_used": 2744, "frames_under_cutoff": 344, "cutoff_nm": 1.0,
                         "counted_over": "frames",
                         "said": ("The solute came within 1 nm of its own periodic image in "
                                  "344 of 2,744 frames used (13%). The cutoff is 1 nm, read "
                                  "from the run's `nonbonded_cutoff_nm`.")},
        "mean": {"solute_image": {"frames_used": 2744, "frames_under_cutoff": 344,
                                  "cutoff_nm": 1.0, "counted_over": "frames"}},
        "polar_sasa": {"solute_image": {"frames_used": 2693, "frames_under_cutoff": 293,
                                        "cutoff_nm": 1.0, "counted_over": "frames"}},
    }

    def test_a_sub_mean_s_paragraph_is_said_as_the_analysis_s(self):
        from fastmdxplora.gui.analysis_overview import _noted

        main = _noted(self.FINDINGS["mean"], self.FINDINGS)["noted_why"]
        polar = _noted(self.FINDINGS["polar_sasa"], self.FINDINGS)["noted_why"]
        assert main.startswith("The solute came within 1 nm")
        assert polar == "Of the analysis as a whole, not this mean alone: " + main

    def test_a_reason_that_says_it_is_not_followed_by_it_again(self):
        from fastmdxplora.gui.analysis_overview import _noted

        why = "Not determined. " + self.FINDINGS["solute_image"]["said"]
        said = _noted(self.FINDINGS["mean"], self.FINDINGS, why=why)
        assert said["noted"] and said["noted_why"] is None

    def test_a_page_reads_it_without_code_marks(self):
        from fastmdxplora.image_note import paragraph_of

        from fastmdxplora.gui.analysis_overview import _said

        assert "read from the run's nonbonded_cutoff_nm." in paragraph_of(self.FINDINGS)
        # And a refusal's setting in its row.
        assert _said("cluster", "cluster: Set `stride: 2` under the analysis's "
                                "`options: {cluster: ...}` for it.") == (
            "Set stride: 2 under the analysis's options: {cluster: ...} for it.")


class TestTheAnalysisPage:
    def test_a_noted_mean_keeps_its_verdict_and_says_the_note(self, study):
        from fastmdxplora.gui.analysis_overview import overview_of

        rows = {row["analysis"]: row for row in overview_of(study)["rows"]}
        rmsd = rows["rmsd"]["mean"]
        assert rmsd["determined"] is True and rmsd["why"] is None
        assert rmsd["noted"] == _expected(study, "rmsd")
        assert rmsd["noted_why"].startswith("The solute came within 1 nm")
        hydrophobic = next(q for q in rows["sasa"]["quantities"]
                           if q["key"] == "hydrophobic_sasa")
        assert hydrophobic["noted"] == _expected(study, "sasa", "hydrophobic_sasa")

    def test_the_image_check_s_row_carries_its_finding_as_a_sentence(self, study):
        from fastmdxplora.gui.analysis_overview import overview_of

        row = next(r for r in overview_of(study)["rows"] if r["analysis"] == "image_check")
        assert row["finding"] == (f"The solute came within 0.420 nm of its own periodic image, "
                                  f"inside the 1 nm cutoff in {N // EVERY} of {N} frames (20%) "
                                  "and in contact with it (under 0.5 nm) in 1 (0.05%).")
        # The rest of the paragraph, one click away: not the finding again.
        assert row["finding_why"] == ("The closest was at frame 35, between ALA1 CA and the "
                                      "image of ALA10 CA (heavy atoms only).")
        assert row["atoms_said"] == "heavy atoms of the macromolecules (10 residues in 1 chain)"
        assert row["quantities"] == []

    def test_the_convergence_says_it_beside_the_recorded_mean(self, study):
        from fastmdxplora.gui.analysis_overview import convergence_payload

        recorded = convergence_payload(study, "rmsd")["recorded"]
        assert recorded["noted"] == _expected(study, "rmsd")

    def test_the_series_carries_it_with_its_mean(self, study):
        from fastmdxplora.gui.series import series_payload

        assert series_payload(study, "rmsd")["mean"]["noted"] == _expected(study, "rmsd")


class TestTheOverview:
    def test_a_noted_tile_says_it_and_stays_determined(self, study):
        from fastmdxplora.gui.overview_view import overview_payload

        tiles = {t["label"]: t for t in overview_payload(study)["tiles"]}
        rmsd = next(t for t in tiles.values() if t["analysis"] == "rmsd")
        assert rmsd["determined"] is True
        assert rmsd["noted"] == _expected(study, "rmsd")
        assert rmsd["noted_why"].startswith("The solute came within 1 nm")


class TestTheCardsAndTheReportPage:
    def test_the_card_caption_is_the_same_note(self, study):
        from fastmdxplora.gui.report_dashboard import _what_the_analysis_found

        caption = _what_the_analysis_found(study / "analysis" / "rmsd" / "rmsd.dat")
        assert caption.endswith("; " + _expected(study, "rmsd"))

    def test_the_image_check_s_card_is_titled_and_captioned_with_its_finding(self, study):
        from fastmdxplora.gui.report_dashboard import (
            _dashboard_summaries,
            _figure_title_from_path,
        )

        assert _figure_title_from_path(study / "analysis" / "image_check" /
                                       "image_check.png") == "Image check"
        summary = _dashboard_summaries(study)["Image check"].summary
        assert summary.startswith("The solute came within 0.420 nm") and summary.endswith("%).")

    def test_the_image_check_s_card_on_the_analysis_page_carries_it(self, study):
        """Its caption reaches the card: the panel is found by its title."""
        from fastmdxplora.gui.report_dashboard import _analysis_sections, _dashboard_summaries

        (study / "analysis" / "image_check" / "image_check.png").write_bytes(b"\x89PNG")
        panels = [panel for section in _analysis_sections(study, study / "report",
                                                          _dashboard_summaries(study))
                  for panel in section.panels if panel.title == "Image check"]
        assert len(panels) == 1
        assert panels[0].summary.startswith("The solute came within 0.420 nm")

    def test_the_report_page_s_table_says_it_beside_the_verdict(self, study):
        from fastmdxplora.gui.report_dashboard import _metric_rows, _render_metric_row

        row = next(r for r in _metric_rows(study, {}) if r.metric == "RMSD")
        assert row.status == "Determined" and row.noted == _expected(study, "rmsd")
        from html import escape

        assert escape(_expected(study, "rmsd")) in _render_metric_row(row)


class TestTheAgent:
    def test_its_reading_of_the_analyses_says_it_and_the_paragraph_once(self, study):
        from fastmdxplora.gui.agent_panel import _results_summary

        said = _results_summary(study)
        rmsd = next(line for line in said.splitlines() if line.strip().startswith("rmsd:"))
        assert _expected(study, "rmsd") in rmsd
        assert "the image note: The solute came within 1 nm" in rmsd
        asked = _results_summary(study, first="rmsd")
        line = next(line for line in asked.splitlines() if line.strip().startswith("rmsd:"))
        # The analysis asked about keeps the paragraph whole on its line.
        assert "the image note: The solute came within 1 nm" in line
        paragraph = line.split("the image note: ")[1]
        assert "; the closest, " in paragraph and "[" not in paragraph

    def test_its_answer_from_the_records_says_it(self, study):
        from fastmdxplora.gui.records_answer import _means

        line = next(line for line in _means(study) if line.startswith("RMSD:"))
        assert _expected(study, "rmsd") in line

    def test_a_study_card_s_mean_says_it(self, study):
        from fastmdxplora.gui.workspace import _means
        from fastmdxplora.workspace_studies import mean_said

        side = dict(_means(study))["rmsd"]
        assert side["noted"] == _expected(study, "rmsd")
        assert mean_said(side).endswith("; " + _expected(study, "rmsd"))


@pytest.fixture
def profiled(study: Path) -> Path:
    """The study with an RMSF, a profile with no mean over frames, noted
    over the frames it read (`note_on_frames`, from its first frame)."""
    from fastmdxplora.analysis.image_check import note_on_frames

    finding, series = _finding()
    manifest = json.loads((study / "analysis" / "analysis_manifest.json").read_text())
    _manifest(study, {**manifest["results"], "rmsf": {"status": "ok"}})
    folder = study / "analysis" / "rmsf"
    folder.mkdir()
    (folder / "rmsf.dat").write_text("".join(f"{k} {0.04 + 0.001 * k:.4f}\n"
                                             for k in range(1, 11)))
    note = note_on_frames(finding, series, np.arange(0, N), analysis="rmsf")
    (folder / "options.json").write_text(json.dumps(
        {"analysis": "rmsf", "selection": "protein",
         "findings": {"frames": {"first_frame": 0}, "solute_image": note}}))
    return study


def _profile_noted(root: Path) -> str:
    record = json.loads((root / "analysis" / "rmsf" / "options.json").read_text())
    return said_short(record["findings"]["solute_image"])


class TestAProfileWithNoMean:
    """RMSF, the order parameters, the dihedrals, clustering and the
    projections carry a note and showed it nowhere on the Analysis page:
    their rows read "A profile, map or table" and RMSF's caption "mean over
    residues 0.0420 nm", while the report noted each."""

    def test_its_row_says_it(self, profiled):
        from fastmdxplora.gui.analysis_overview import overview_of

        rows = {r["analysis"]: r for r in overview_of(profiled)["rows"]}
        row = rows["rmsf"]
        assert row["quantities"] == [] and row["noted"] == _profile_noted(profiled)
        assert row["noted_why"].startswith("The solute came within 1 nm")
        # The image check's own finding is not a note.
        assert "noted" not in rows["image_check"]

    def test_rmsf_s_caption_and_report_row_say_it(self, profiled):
        from fastmdxplora.gui.report_dashboard import _dashboard_summaries, _metric_rows

        caption = _dashboard_summaries(profiled)["RMSF"].summary
        assert caption.startswith("mean over residues ")
        assert caption.endswith("; " + _profile_noted(profiled))
        row = next(r for r in _metric_rows(profiled, {}) if r.metric == "RMSF")
        assert row.noted == _profile_noted(profiled)


#: A run's mean noted as the analysis code notes it (`note_numbers`).
_NOTE = {"frames_used": 1864, "frames_under_cutoff": 372, "fraction_under_cutoff": 0.2,
         "frames_under_close": 0, "nearest_nm": 0.62, "cutoff_nm": 1.0,
         "counted_over": "frames"}


@pytest.fixture
def two_runs(tmp_path: Path) -> Path:
    """A study of two runs whose RMSDs differ past twice their combined
    error; the second run's solute came within the cutoff of its image."""
    from tests.test_the_runs_of_a_study_are_compared_on_one_page import _study

    root = _study(tmp_path / "sweep", {
        "t300": {"value": 300, "rmsd": (0.160, 0.001, None)},
        "t310": {"value": 310, "rmsd": (0.170, 0.001, None)}})
    path = root / "runs" / "t310" / "analysis" / "rmsd" / "options.json"
    record = json.loads(path.read_text())
    record["findings"]["mean"]["solute_image"] = dict(_NOTE)
    path.write_text(json.dumps(record))
    # Each run's value as the written comparison reads it, for its trend.
    manifest = json.loads((root / "batch_manifest.json").read_text())
    for run, planned in zip(manifest["runs"], manifest["planned"]):
        run["sweep_values"] = planned["sweep_values"]
    (root / "batch_manifest.json").write_text(json.dumps(manifest))
    return root


class TestTheRunsOfAStudy:
    """The runs compared, the Report page's table of runs, the means cited
    under an Agent's answer, the written comparison and the slides showed a
    noted mean with no note: a difference "resolved" between a run whose
    solute saw its image and one whose solute did not, said with none."""

    def test_the_runs_compared_carry_it_and_say_it_of_a_resolved_difference(self, two_runs):
        from fastmdxplora.gui.runs_compared import runs_compared

        (rmsd,) = runs_compared(two_runs)["measures"]
        cells = {c["run_id"]: c for c in rmsd["cells"]}
        assert cells["t300"]["noted"] is None
        assert cells["t310"]["noted"] == said_short(_NOTE)
        assert cells["t310"]["versus"]["resolved"] is True
        assert ("Where either mean may be biased by the solute's own image (1 of these), the "
                "difference may be that bias and not the setting.") in rmsd["said"]
        assert "1 may be biased by the solute's own image, as said beside its value." in rmsd[
            "said"]

    def test_the_report_page_s_table_marks_it_and_says_it_under_the_table(self, two_runs):
        from fastmdxplora.gui.exploration import runs_of_a_study
        from fastmdxplora.gui.report_page import _so_far

        runs = runs_of_a_study(two_runs)
        text = _so_far(two_runs, runs, runs)
        row = next(line for line in text.splitlines() if line.startswith("| t310 |"))
        assert row.endswith(" \u2020 |")
        assert f"\u2020 t310, RMSD: {said_short(_NOTE)}." in text
        assert "\u2020" not in next(line for line in text.splitlines()
                                    if line.startswith("| t300 |"))

    def test_the_mean_cited_under_an_answer_says_it(self, two_runs):
        from fastmdxplora.gui.citations import cited_findings

        (cite,) = cited_findings("The RMSD settled.", two_runs / "runs" / "t310")
        assert cite["noted"] == said_short(_NOTE)
        assert cite["value"].endswith("nm; " + said_short(_NOTE))
        (plain,) = cited_findings("The RMSD settled.", two_runs / "runs" / "t300")
        assert plain["noted"] == "" and plain["value"].endswith(" nm")

    def test_the_written_comparison_marks_it_and_says_it_of_the_trend(self, two_runs):
        pytest.importorskip("matplotlib")
        from fastmdxplora.batch.compare import build_comparison_report

        md = (build_comparison_report(two_runs) / "comparison_report.md").read_text()
        row = next(line for line in md.splitlines() if line.startswith("| t310 |"))
        assert row.endswith(" \u2020 |")
        assert f"\u2020 t310, RMSD: {said_short(_NOTE)}." in md
        assert ("One end's mean may be biased by the solute's own image (the note in the "
                "summary), so the change may be that bias and not the setting.") in md

    def test_the_written_comparison_s_csv_carries_it(self, two_runs):
        pytest.importorskip("matplotlib")
        import csv

        from fastmdxplora.batch.compare import build_comparison_report

        with (build_comparison_report(two_runs) / "comparison_summary.csv").open() as fh:
            rows = {row["run_id"]: row for row in csv.DictReader(fh)}
        assert rows["t310"]["rmsd_noted"] == said_short(_NOTE)
        assert rows["t300"]["rmsd_noted"] == ""

    def test_the_slides_say_it_beside_the_mean(self, study, monkeypatch):
        from fastmdxplora.report import document, slides

        assessed = {"observables": {
            "rmsd": {"observable": "RMSD", "mean": 0.3, "standard_error": 0.001,
                     "effective_samples": 40.0, "equilibrated": True},
            "rg": {"observable": "Rg", "mean": 1.2, "standard_error": 0.01,
                   "effective_samples": 40.0, "equilibrated": True}}, "findings": []}
        monkeypatch.setattr(document, "_assess_this_run", lambda root: assessed)
        rmsd, rg = slides._findings_bullets(study)
        assert rmsd.endswith("; " + _expected(study, "rmsd"))
        assert "may be biased" not in rg


#: Beside a longer run advised, for a run in contact with its image in every
#: frame; and beside means all determined, for such a run.
LONGER = ("A longer run may determine the means; it would not lift the bias of the solute's "
          "own image, in contact with it in every frame: a longer run in the same box, like "
          "replicas in it, samples the same contact, and a larger box, in a new study, is what "
          "lifts it.")
DETERMINED = ("Its means may still be biased by the solute's own image, in contact with it in "
              "every frame: a longer run in the same box, like replicas in it, samples the "
              "same contact, and a larger box, in a new study, is what lifts it.")


def _in_contact(where: Path, rmsd: dict, rg: dict) -> Path:
    """A run whose solute was in contact with its own image in every frame."""
    from tests.test_a_run_s_overview_leads_with_what_it_needs import _a_run

    root = _a_run(where, rmsd, rg)
    finding, _ = _finding()
    finding.update(frames_under_close=N, frames_under_cutoff=N)
    (root / "analysis" / "image_check").mkdir(exist_ok=True)
    (root / "analysis" / "image_check" / "options.json").write_text(json.dumps(
        {"analysis": "image_check", "findings": {"solute_image": finding}}))
    return root


class TestALongerRunAlone:
    """For a run in contact with its image in every frame, the Overview's
    lead and the Agent's answers advised only a longer run ("about 2.5 ns
    more production should determine it"), while the report said a longer
    run in the same box samples the same contact."""

    @pytest.fixture
    def in_contact(self, tmp_path: Path) -> Path:
        from tests.test_a_run_s_overview_leads_with_what_it_needs import _a_run, _withheld
        from tests.test_a_study_of_runs_is_shown_as_one import _recorded

        root = _a_run(tmp_path, _withheld(2.43), _recorded(1.2))
        finding, _ = _finding()
        finding.update(frames_under_close=N, frames_under_cutoff=N)
        (root / "analysis" / "image_check").mkdir(exist_ok=True)
        (root / "analysis" / "image_check" / "options.json").write_text(json.dumps(
            {"analysis": "image_check", "findings": {"solute_image": finding}}))
        return root

    def _said(self) -> str:
        return LONGER

    def test_the_overview_s_lead_says_it(self, in_contact):
        from fastmdxplora.gui.overview_view import overview_payload

        said = overview_payload(in_contact)["lead"]["said"]
        assert said.startswith("1 of 2 means is not determined: about 2.5 ns more production")
        assert said.endswith(" " + self._said())
        assert "a larger box, in a new study, is what lifts it" in self._said()
        # Not where it was in contact in some frames only.
        path = in_contact / "analysis" / "image_check" / "options.json"
        record = json.loads(path.read_text())
        record["findings"]["solute_image"]["frames_under_close"] = N - 1
        path.write_text(json.dumps(record))
        assert "lifts it" not in overview_payload(in_contact)["lead"]["said"]

    def test_the_agent_s_answers_say_it(self, in_contact, monkeypatch):
        from fastmdxplora.gui import records_answer

        monkeypatch.setattr(records_answer, "_supports", lambda base: "")
        for question in ("long_enough", "strengthen"):
            said = records_answer.answer_from_the_records(in_contact, question)
            assert "more production" in said and self._said() in said, question

    def test_the_report_says_it_from_the_same_helper(self, in_contact):
        from fastmdxplora.report.document import _convergence_section

        assert self._said() in _convergence_section(in_contact)


class TestTheReportAndItsCaptions:
    """The report's "The remedy is a longer run, or better, replicas" stood
    under a mean in contact with its image in every frame it uses; and the
    written dashboard's cards for the order parameters, the cluster matrix
    and a landscape had no caption, so no note."""

    REASON = ("This run is not long against its own correlation time. The remedy is a "
              "longer run, or better, replicas.")

    def test_the_remedy_says_neither_lifts_it(self):
        from fastmdxplora.report.document import _findings_notes

        def notes(note):
            return _findings_notes({"mean": {"mean": 1.04, "standard_error": None,
                                             "not_a_measurement": self.REASON,
                                             "solute_image": note}})

        assert (self.REASON + " Neither lifts the bias of the solute's own image, in contact "
                "with it in every frame this mean uses: a larger box, in a new study, "
                "does.") in notes(dict(_NOTE_09))
        assert self.REASON in notes({**_NOTE_09, "frames_under_close": 99})

    def test_a_card_with_no_caption_of_its_own_says_its_note(self, study):
        from fastmdxplora.gui.report_dashboard import _analysis_sections, _dashboard_summaries

        folder = study / "analysis" / "order_parameters"
        folder.mkdir()
        (folder / "order_parameters.png").write_bytes(b"\x89PNG")
        (folder / "options.json").write_text(json.dumps({
            "analysis": "order_parameters", "findings": {"solute_image": dict(_NOTE_09)}}))
        (panel,) = [panel for section in _analysis_sections(study, study / "report",
                                                            _dashboard_summaries(study))
                    for panel in section.panels if panel.source.startswith(
                        "analysis/order_parameters/")]
        noted = said_short(_NOTE_09)
        assert panel.summary == noted[:1].upper() + noted[1:] + "."


class TestADeterminedRunInContact:
    """A run in contact with its image in every frame, its means all
    determined, was told "By its records, yes." and advised "Then
    replicas.", and its Overview led "All 2 means are determined." and no
    more; where no figure says how much longer, a longer run was advised
    with no word of the image."""

    ALL = "All 2 of 2 observables assessed, 2 had equilibrated, their means determined."
    SOME = "Of 2 observables assessed, 1 had too few independent samples to average."

    def test_the_overview_s_lead_says_it(self, tmp_path):
        from tests.test_a_run_s_overview_leads_with_what_it_needs import _withheld
        from tests.test_a_study_of_runs_is_shown_as_one import _recorded

        from fastmdxplora.gui.overview_view import overview_payload

        root = _in_contact(tmp_path / "all", _recorded(0.15), _recorded(1.2))
        assert overview_payload(root)["lead"]["said"] == (
            "All 2 means are determined. " + DETERMINED)
        unsaid = _in_contact(tmp_path / "unsaid", _withheld(None), _recorded(1.2))
        said = overview_payload(unsaid)["lead"]["said"]
        assert said.startswith("1 of 2 means is not determined. How much longer would "
                               "determine it is not in the records")
        assert said.endswith(" " + LONGER)

    def test_the_agent_s_answers_say_it(self, tmp_path, monkeypatch):
        from tests.test_a_study_of_runs_is_shown_as_one import _recorded

        from fastmdxplora.gui import records_answer

        root = _in_contact(tmp_path, _recorded(0.15), _recorded(1.2))
        monkeypatch.setattr(records_answer, "_supports", lambda base: self.ALL)
        enough = records_answer.answer_from_the_records(root, "long_enough")
        assert f"By its records, yes. {self.ALL}\n\n{DETERMINED}\n\n" in enough
        strengthen = records_answer.answer_from_the_records(root, "strengthen")
        assert ("Its means are determined, so a longer run of this one adds precision but "
                f"not a check.\n\n{DETERMINED}\n\nThen replicas in a larger box. ") in strengthen
        monkeypatch.setattr(records_answer, "_supports", lambda base: self.SOME)
        enough = records_answer.answer_from_the_records(root, "long_enough")
        assert enough.count(LONGER) == 1 and f"Not by its records. {self.SOME}" in enough
        strengthen = records_answer.answer_from_the_records(root, "strengthen")
        assert strengthen.count(LONGER) == 1 and f"A longer run first. {self.SOME}" in strengthen
        assert "Then replicas in a larger box. " in strengthen


#: A replica's mean noted at the run's cutoff of 0.9 nm, as the analysis
#: code notes it (`note_numbers`): in contact with its image in every frame.
_NOTE_09 = {"frames_used": 100, "frames_under_cutoff": 100, "fraction_under_cutoff": 1.0,
            "frames_under_close": 100, "nearest_nm": 0.3, "cutoff_nm": 0.9,
            "counted_over": "frames"}

#: Beside the mean of the means of three replicas, two of them noted.
_TWO_OF_THREE = ("may be biased: in 2 of the 3 replicas the solute was within 0.9 nm of its "
                 "own image, in frames their means use")


def _noted_replicas(root: Path, noted: tuple[str, ...] = ("seed1", "seed2")) -> Path:
    """Three replicas whose RMSDs are each determined; those in ``noted``
    noted at a cutoff of 0.9 nm."""
    from tests.test_the_runs_of_a_study_are_compared_on_one_page import _study

    _study(root, {f"seed{n}": {"value": n, "rmsd": (0.160 + 0.004 * n, 0.001, None)}
                  for n in (1, 2, 3)}, axis="simulation.random_seed")
    for run in noted:
        path = root / "runs" / run / "analysis" / "rmsd" / "options.json"
        record = json.loads(path.read_text())
        record["findings"]["mean"]["solute_image"] = dict(_NOTE_09)
        path.write_text(json.dumps(record))
    return root


class TestTheReplicasTogether:
    """Three replicas, each RMSD noted, gave their mean of means
    "0.165 ± 0.00291" with no note on the Overview, the All studies card and
    the written comparison, though replicas share the box and the bias is
    in the mean of them; and the runs compared lost "3 may be biased ...",
    replaced by the calibration."""

    def test_the_mean_of_their_means_says_how_many_are_noted(self, tmp_path):
        from fastmdxplora.gui.runs_compared import runs_compared

        (rmsd,) = runs_compared(_noted_replicas(tmp_path / "replicas"))["measures"]
        assert rmsd["ensemble"]["noted"] == _TWO_OF_THREE
        assert rmsd["ensemble"]["error"] is not None
        # The calibration and the cells' note, both.
        assert rmsd["said"].startswith("The replicas spread ")
        assert rmsd["said"].endswith(
            " 2 may be biased by the solute's own image, as said beside each value.")
        (plain,) = runs_compared(_noted_replicas(tmp_path / "plain", ()))["measures"]
        assert plain["ensemble"]["noted"] is None

    def test_each_and_one_of_them_said(self):
        from fastmdxplora.gui.runs_compared import mean_of_replicas

        def entry(*notes):
            return {"members": [{"solute_image": note} for note in notes],
                    "mean_of_means": 0.168, "spread_of_means": 0.004}

        every = mean_of_replicas(entry(_NOTE_09, _NOTE_09))
        assert every["noted"] == ("may be biased: in each of the 2 replicas the solute was "
                                  "within 0.9 nm of its own image, in frames their means use")
        one = mean_of_replicas(entry(None, {**_NOTE_09, "cutoff_nm": 1.2}, None))
        assert one["noted"] == ("may be biased: in 1 of the 3 replicas the solute was within "
                                "1.2 nm of its own image, in frames its mean uses")
        # Two cutoffs: neither said as the replicas'.
        two = mean_of_replicas(entry({**_NOTE_09, "cutoff_nm": 1.2}, _NOTE_09))
        assert "within the cutoff of its own image" in two["noted"]
        assert mean_of_replicas(entry(None, None))["noted"] is None

    def test_the_study_card_and_the_written_comparison_say_it(self, tmp_path):
        from fastmdxplora.batch.compare import _across_the_replicas
        from fastmdxplora.gui.workspace import card_of

        root = _noted_replicas(tmp_path / "replicas")
        (rmsd,) = card_of(root)["means"]
        assert rmsd["noted"] == _TWO_OF_THREE
        written = _across_the_replicas(root)
        assert any(line.startswith("| rmsd | 0.168 † | ") for line in written), written
        assert f"† rmsd: {_TWO_OF_THREE}." in written


# --------------------------------------------------------------------------
# In a browser: the Overview tile, the Analysis page's table and the cell
# over its card give the same note for one mean.
# --------------------------------------------------------------------------

def test_one_note_on_the_overview_the_table_and_the_card(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study
    from tests.test_the_overview_leads_with_what_was_determined import (
        _metrics,
        _phases,
        _status,
    )

    root = tmp_path / "study"
    _write_study(root)
    (root / "simulation" / "energy.csv").unlink(missing_ok=True)
    _manifest(root, {"image_check": {"status": "ok"}, "rmsd": {"status": "ok"}})
    finding, series = _finding()
    folder = root / "analysis" / "image_check"
    folder.mkdir(parents=True)
    (folder / "image_check.dat").write_text("".join(f"{v}\n" for v in series))
    (folder / "options.json").write_text(json.dumps(
        {"analysis": "image_check", "selection": "protein",
         "findings": {"solute_image": finding}}))
    _analysis(root, "rmsd", 0.30 + 0.002 * _ar1(N, 0.5, 1))
    _noted(root, "rmsd", finding, series)
    # And a profile with no mean, noted over the frames it read.
    _manifest(root, {"image_check": {"status": "ok"}, "rmsd": {"status": "ok"},
                     "rmsf": {"status": "ok"}})
    from fastmdxplora.analysis.image_check import note_on_frames

    (root / "analysis" / "rmsf").mkdir()
    (root / "analysis" / "rmsf" / "rmsf.dat").write_text(
        "".join(f"{k} {0.04 + 0.001 * k:.4f}\n" for k in range(1, 11)))
    (root / "analysis" / "rmsf" / "options.json").write_text(json.dumps(
        {"analysis": "rmsf", "selection": "protein", "findings": {
            "frames": {"first_frame": 0},
            "solute_image": note_on_frames(finding, series, np.arange(0, N),
                                           analysis="rmsf")}}))
    # Each analysis's figure, which its card on the Analysis page shows.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for name in ("rmsd", "image_check", "rmsf"):
        figure = plt.figure(figsize=(2, 1.5))
        plt.plot([0, 1], [0, 1])
        figure.savefig(root / "analysis" / name / f"{name}.png")
        plt.close(figure)
    _metrics(root)
    _status(root)
    _phases(root)
    expected = _expected(root, "rmsd")

    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            found = {}
            for width, scheme in ((1440, "light"), (390, "dark")):
                page = browser.new_page(viewport={"width": width, "height": 900},
                                        color_scheme=scheme)
                page.set_default_timeout(60000)
                errors: list[str] = []
                page.on("pageerror", lambda error, seen=errors: seen.append(str(error)))
                page.goto(session.url + "#overview", wait_until="domcontentloaded")
                page.wait_for_selector('#overview-tiles [data-tile-analysis="rmsd"] .tile-noted')
                tile = page.text_content('#overview-tiles [data-tile-analysis="rmsd"] .tile-noted')
                badge = page.text_content('#overview-tiles [data-tile-analysis="rmsd"] .tile-badge')
                page.goto(session.url + "#analysis", wait_until="domcontentloaded")
                page.wait_for_selector('#analysis-results-table tr[data-analysis="rmsd"] '
                                       '.analysis-results-noted summary')
                table = page.text_content('#analysis-results-table tr[data-analysis="rmsd"] '
                                          '.analysis-results-noted summary')
                status = page.text_content('#analysis-results-table tr[data-analysis="rmsd"] '
                                           '.analysis-results-status')
                check = page.text_content('#analysis-results-table '
                                          'tr[data-analysis="image_check"] td')
                page.wait_for_selector(".cell-noted")
                cell = page.text_content('.analysis-cell-head[data-analysis="rmsd"] .cell-noted')
                profile_row = page.text_content('#analysis-results-table '
                                                'tr[data-analysis="rmsf"] '
                                                '.analysis-results-noted summary')
                page.wait_for_selector('.analysis-cell-head[data-analysis="rmsf"] .cell-noted')
                profile_card = page.text_content('.analysis-cell-head[data-analysis="rmsf"] '
                                                 '.cell-noted')
                wide = page.evaluate("() => document.documentElement.scrollWidth "
                                     "- document.documentElement.clientWidth")
                found[(width, scheme)] = (tile, badge, table, status, check, cell, wide, errors,
                                          profile_row, profile_card)
                page.close()
            browser.close()
    finally:
        session.server.shutdown()
    profile = _profile_noted(root)
    for (width, scheme), (tile, badge, table, status, check, cell, wide,
                          errors, profile_row, profile_card) in found.items():
        assert profile_row == profile_card == profile, (width, scheme, profile_row)
        assert not errors, (width, scheme, errors)
        assert tile == table == cell == expected, (width, scheme, tile, table, cell)
        assert badge == "Determined" and status.strip() == "Determined"
        assert check.startswith("The solute came within 0.420 nm"), check
        assert wide <= 0, (width, scheme, wide)


def test_the_runs_compared_mark_it_and_say_it_under_the_table(two_runs) -> None:
    """In a browser: the noted run's cell carries a mark, the note on it,
    and the note is said in full under the table."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(two_runs), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#runs-compared-table .is-noted")
            marks = page.locator("#runs-compared-table .is-noted")
            count = marks.count()
            row = page.locator("#runs-compared-table .is-noted").first.evaluate(
                "node => node.closest('tr').dataset.run")
            title = marks.first.get_attribute("title")
            under = page.locator("#runs-compared-said .runs-compared-noted").all_text_contents()
            browser.close()
    finally:
        session.server.shutdown()
    noted = said_short(_NOTE)
    assert count == 1 and row == "t310"
    assert title == noted[:1].upper() + noted[1:] + "."
    assert under == [f"† temperature 310 K, RMSD: {noted}."]
    assert errors == []


def test_all_studies_says_a_noted_mean_beside_its_verdict(tmp_path) -> None:
    """In a browser: a single run's noted RMSD on its row and its card read
    "RMSD 0.1046 ± 0.0013 nm" with the filled dot and the title "Determined,
    with its standard error", and nothing of the note; the replicas' mean of
    their means likewise."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_all_studies_is_a_table_first import _members
    from tests.test_the_workspace_says_its_studies import _replicas
    from tests.test_the_workspace_says_its_studies import _study as _a_study

    space = tmp_path / "ws"
    one = _a_study(space / "one", means={"rmsd": (0.1046, 0.0013)})
    path = one / "analysis" / "rmsd" / "options.json"
    record = json.loads(path.read_text())
    record["findings"]["mean"]["solute_image"] = dict(_NOTE_09)
    path.write_text(json.dumps(record))
    reps = _replicas(space / "reps")
    _members(reps, [0.10, 0.12, 0.14])
    for run in ("seed1", "seed2"):
        path = reps / "runs" / run / "analysis" / "rmsd" / "options.json"
        record = json.loads(path.read_text())
        record["findings"]["mean"]["solute_image"] = dict(_NOTE_09)
        path.write_text(json.dumps(record))
    single = said_short(_NOTE_09)
    session = start_dashboard_session(output=str(one), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            found = {}
            for width, scheme in ((1440, "light"), (390, "dark")):
                page = browser.new_page(viewport={"width": width, "height": 900},
                                        color_scheme=scheme)
                page.set_default_timeout(60000)
                errors: list[str] = []
                page.on("pageerror", lambda error, seen=errors: seen.append(str(error)))
                page.goto(session.url + "#studies", wait_until="domcontentloaded")
                page.wait_for_selector("body.state-ready")
                page.evaluate(f"() => window.FastMDXStudies.load({str(space)!r})")
                shown = {}
                for view, where in (("table", "#studies-table tr.studies-row"),
                                    ("cards", ".study-card")):
                    if view == "cards":
                        page.click('[data-studies-view="cards"]')
                    for name in ("one", "reps"):
                        item = f'{where}[data-path$="/{name}"] .studies-mean-item'
                        page.wait_for_selector(item)
                        shown[(view, name)] = page.evaluate(
                            "(sel) => { const n = document.querySelector(sel); return ["
                            "(n.querySelector('.studies-mean-noted') || {}).textContent || '',"
                            "n.title, n.dataset.determined]; }", item)
                wide = page.evaluate("() => document.documentElement.scrollWidth "
                                     "- document.documentElement.clientWidth")
                found[(width, scheme)] = (shown, wide, errors)
                page.close()
            browser.close()
    finally:
        session.server.shutdown()
    for (width, scheme), (shown, wide, errors) in found.items():
        assert not errors and wide <= 0, (width, scheme, errors, wide)
        for view in ("table", "cards"):
            line, title, determined = shown[(view, "one")]
            assert line == single[:1].upper() + single[1:] + ".", (width, view, line)
            assert title == "Determined, with its standard error. " + line, title
            assert determined == "true"
            line, title, determined = shown[(view, "reps")]
            assert line == _TWO_OF_THREE[:1].upper() + _TWO_OF_THREE[1:] + ".", (width, view, line)
            assert title.endswith("over √3. " + line), title


def test_compare_says_a_noted_mean_on_either_side(two_runs, tmp_path) -> None:
    """In a browser: the two runs picked on the Overview, and two studies,
    gave each side's mean with no note, and judged a difference "resolved"
    between a mean noted and one not, saying nothing of it."""
    pytest.importorskip("playwright.sync_api")
    import urllib.parse

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_workspace_says_its_studies import _study as _a_study

    for run, kelvin in (("t300", 300), ("t310", 310)):
        (two_runs / "runs" / run / "resolved_config.yml").write_text(
            f"simulation:\n  temperature_K: {kelvin}\n", encoding="utf-8")
    first = _a_study(tmp_path / "ws" / "first", means={"rmsd": (0.1046, 0.0013)})
    second = _a_study(tmp_path / "ws" / "second", means={"rmsd": (0.1200, 0.0013)})
    path = second / "analysis" / "rmsd" / "options.json"
    record = json.loads(path.read_text())
    record["findings"]["mean"]["solute_image"] = dict(_NOTE_09)
    path.write_text(json.dumps(record))
    read = """() => ({
        cells: [...document.querySelectorAll(
            '.compare-means-table tr[data-analysis="rmsd"] td')].map((n) => n.textContent),
        titles: [...document.querySelectorAll(
            '.compare-means-table tr[data-analysis="rmsd"] td.is-noted')].map((n) => n.title),
        lines: [...document.querySelectorAll('.compare-noted')].map((n) => n.textContent),
    })"""
    found = {}
    for study, place in ((two_runs, None), (first, f"{first}|{second}")):
        session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.set_default_timeout(60000)
                errors: list[str] = []
                page.on("pageerror", lambda error, seen=errors: seen.append(str(error)))
                if place is None:
                    page.goto(session.url + "#overview", wait_until="domcontentloaded")
                    page.wait_for_selector("#overview-runs-table tbody tr")
                    page.locator(".overview-runs-pick").nth(0).check()
                    page.locator(".overview-runs-pick").nth(1).check()
                    page.click("#overview-runs-compare")
                else:
                    page.goto(session.url + "#compare/" + "|".join(
                        urllib.parse.quote(p, safe="") for p in place.split("|")),
                        wait_until="domcontentloaded")
                page.wait_for_selector('.page[data-page="compare"][data-compared="yes"]')
                found[place is None] = (page.evaluate(read), errors)
                browser.close()
        finally:
            session.server.shutdown()
    single = said_short(_NOTE)
    (runs, errors) = found[True]
    assert errors == []
    assert not runs["cells"][1].endswith("†") and runs["cells"][2].endswith(" nm †")
    assert runs["titles"] == [single[:1].upper() + single[1:] + "."]
    assert runs["lines"] == [
        f"\u2020 sweep, temperature 310 K, RMSD: {single}.",
        "One resolved difference has a mean that may be biased by the solute's own image: "
        "it may be that bias and not the setting."]
    (studies, errors) = found[False]
    assert errors == []
    assert studies["cells"][2].endswith(" nm †")
    assert studies["lines"][0] == f"† second, RMSD: {said_short(_NOTE_09)}."


def test_the_overview_of_a_study_of_runs_says_what_its_mark_means(two_runs, tmp_path) -> None:
    """In a browser: the Overview of a study of runs marked a noted mean
    with † and put the note only in the cell's title, which the keyboard
    and a touch screen do not reach; the replicas' row "The runs together"
    gave the mean of their means with no mark at all."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    replicas = _noted_replicas(tmp_path / "replicas")
    found = {}
    for study in (two_runs, replicas):
        session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
                for width in (1440, 390):
                    page = browser.new_page(viewport={"width": width, "height": 900})
                    page.set_default_timeout(60000)
                    errors: list[str] = []
                    page.on("pageerror", lambda error, seen=errors: seen.append(str(error)))
                    page.goto(session.url + "#overview", wait_until="domcontentloaded")
                    page.wait_for_selector("#overview-runs-table .is-noted")
                    found[(study.name, width)] = (page.evaluate("""() => ({
                        lines: [...document.querySelectorAll('#overview-runs-noted p')]
                            .filter((n) => n.getClientRects().length).map((n) => n.textContent),
                        together: (document.querySelector(
                            '.overview-runs-together .is-noted') || {}).textContent || '',
                    })"""), errors)
                    page.close()
                browser.close()
        finally:
            session.server.shutdown()
    for width in (1440, 390):
        shown, errors = found[("sweep", width)]
        assert errors == []
        assert shown["lines"] == [f"† temperature 310 K, RMSD: {said_short(_NOTE)}."]
        shown, errors = found[("replicas", width)]
        assert errors == []
        assert shown["together"].endswith(" †"), shown
        noted = said_short(_NOTE_09)
        assert shown["lines"] == [f"† seed 1, RMSD: {noted}.", f"† seed 2, RMSD: {noted}.",
                                  f"† The runs together, RMSD: {_TWO_OF_THREE}."], shown
