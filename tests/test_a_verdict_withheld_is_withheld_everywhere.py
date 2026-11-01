"""A mean withheld on one page is withheld on every page, the Agent's and the
report's included, by one reader.

A mean an earlier version called determined on fewer independent samples
than this version determines a mean on read "not determined, error not
given" on the Overview, the Analysis table, the card and the convergence
panel, and with its error and no verdict in the Agent's citation chip, the
records text the Agent reads, the report's findings, its Convergence table
and Methods, the Analysis page's figure, and both Compare pages, which each
read the record's `not_a_measurement` alone (review of `image-said`, 11-01).
Each reads `statistics.withheld_reason` now.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_a_run_s_overview_leads_with_what_it_needs import _a_run
from tests.test_a_study_of_runs_is_shown_as_one import _recorded
from tests.test_an_earlier_verdict_is_read_once import EARLIER, WHY


@pytest.fixture
def root(tmp_path) -> Path:
    return _a_run(tmp_path, dict(EARLIER), _recorded(1.2))


def test_the_agent_s_citation_chip(root) -> None:
    from fastmdxplora.gui.citations import cited_findings

    (chip,) = cited_findings("The RMSD mean", root)
    assert chip["value"] == "0.007766 nm"
    assert chip["withheld"].startswith(WHY)


def test_the_records_text_the_agent_reads(root) -> None:
    from fastmdxplora.gui.agent_panel import _results_summary

    line = next(x for x in _results_summary(root, "rmsd").splitlines()
                if x.strip().startswith("rmsd:"))
    assert "±" not in line and "(s.e.)" not in line
    assert "-- not determined: " + WHY in line


def test_the_report_s_findings(root) -> None:
    from fastmdxplora.report.document import _findings_notes

    findings = json.loads((root / "analysis" / "rmsd" / "options.json").read_text())["findings"]
    notes = _findings_notes(findings)
    assert notes[0] == ("Mean over the equilibrated part of the run: 0.007766 nm, from 7 "
                        "independent samples after discarding 66 frames.")
    assert notes[1].startswith(WHY)


def test_the_report_s_convergence_table(root) -> None:
    from fastmdxplora.report.document import _convergence_section

    row = next(x for x in _convergence_section(root).splitlines() if x.startswith("| RMSD |"))
    assert row.split(" | ")[4:6] == ["0.007766", "not determined"]


def test_the_report_s_methods(root) -> None:
    from fastmdxplora.report.document import _recorded_means
    from fastmdxplora.report.methods import _analysis_paragraph

    assert "No error is given for `rmsd`" in _analysis_paragraph(_recorded_means(root), None)


def test_the_analysis_page_s_figure(root) -> None:
    from fastmdxplora.gui.series import series_payload

    (root / "analysis" / "rmsd" / "rmsd.dat").write_text(
        "".join(f"{k} {0.0077 + 0.0001 * (k % 3):.5f}\n" for k in range(100)), encoding="utf-8")
    mean = series_payload(root, "rmsd")["mean"]
    assert mean["error"] is None
    assert mean["not_a_measurement"].startswith(WHY)


def test_compare_of_two_studies(root, tmp_path) -> None:
    from fastmdxplora.gui.workspace import studies_compared

    other = _a_run(tmp_path / "other", {**_recorded(0.02), "unit": "nm"}, _recorded(1.3))
    rmsd = next(m for m in studies_compared(root, other)["measures"] if m["analysis"] == "rmsd")
    assert rmsd["first"]["error"] is None and rmsd["first"]["withheld"].startswith(WHY)
    assert "versus" not in rmsd


def _with_an_earlier_record(tmp_path: Path) -> Path:
    from tests.test_the_runs_of_a_study_are_compared_on_one_page import _study

    root = _study(tmp_path / "sweep", {
        "t300": {"value": 300, "rmsd": (0.160, 0.001, None)},
        "t310": {"value": 310, "rmsd": (0.170, 0.001, None)},
    })
    for run, samples in (("t300", 40.0), ("t310", 7.3)):
        options = root / "runs" / run / "analysis" / "rmsd" / "options.json"
        record = json.loads(options.read_text())
        record["findings"]["mean"].update(effective_samples=samples, rules=3)
        if samples < 10:
            del record["findings"]["mean"]["rules"]
        options.write_text(json.dumps(record), encoding="utf-8")
    return root


def test_compare_of_a_study_s_runs(tmp_path) -> None:
    from fastmdxplora.gui.runs_compared import runs_compared

    rmsd = runs_compared(_with_an_earlier_record(tmp_path))["measures"][0]
    cells = {c["run_id"]: c for c in rmsd["cells"]}
    assert cells["t310"]["error"] is None
    assert cells["t310"]["withheld"].startswith("7 independent samples, fewer than the 10")
    assert "versus" not in cells["t310"]


def test_the_runs_backing_a_spread(tmp_path) -> None:
    from fastmdxplora.batch.aggregate import aggregate_members

    root = _with_an_earlier_record(tmp_path)
    (root / "batch_manifest.json").write_text(json.dumps({
        "runs": [{"run_id": r, "output_dir": str(root / "runs" / r), "status": "ok",
                  "system": "a.pdb", "sweep_values": {"simulation.random_seed": s}}
                 for r, s in (("t300", 1), ("t310", 2))],
        "sweep": {"simulation.random_seed": [1, 2]}}), encoding="utf-8")
    entry = aggregate_members(root)["analyses"]["rmsd"]
    assert entry.get("qualified_by_their_own_runs") == ["t310"]


def test_the_written_comparison_marks_it(tmp_path) -> None:
    from fastmdxplora.batch.compare import _recorded_mean

    assert str(_recorded_mean({"mean": dict(EARLIER)})["qualified"]).startswith(WHY)
