"""What a study reproducing a paper determined, beside what the paper reports.

The results the paper reports for a study are kept in its config before it
runs. Once it has, each is set beside the mean its analysis recorded, in
one unit, with a verdict the numbers allow: agreement within twice the
combined error, a spread that is not an error of the mean, no verdict where
the paper gives no error, and nothing claimed where this study's own mean
was not determined. Replicas are compared by the mean of their means.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fastmdxplora.paper.reproduction import compare_with_paper, paper_standard_error, reproduction_lines


def _claim(quantity, analysis, value, error=None, kind="standard_error", n=None, unit="nm",
           what=""):
    claim = {"quantity": quantity, "analysis": analysis, "what": what, "value": value,
             "unit": unit, "error_kind": kind, "quote": f"{value}", "where": "Table 1"}
    if error is not None:
        claim["error"] = error
    if n is not None:
        claim["n"] = n
    return claim


def _run(root: Path, means: dict[str, dict], claims: list[dict] | None = None) -> Path:
    """A run as FastMDXplora writes one: its resolved config and each
    analysis's recorded mean."""
    root.mkdir(parents=True, exist_ok=True)
    if claims is not None:
        (root / "resolved_config.yml").write_text(yaml.safe_dump({
            "system": "1UBQ", "paper": {"doi": "10.9999/x", "title": "A paper", "study": "S1",
                                        "label": "Ubiquitin", "claims": claims}}),
            encoding="utf-8")
    results = {}
    for name, record in means.items():
        folder = root / "analysis" / name
        folder.mkdir(parents=True, exist_ok=True)
        findings = {"mean": {"n_frames": 500, "discard": 0, **record}}
        if name == "ss":
            findings = {"helix_fraction": {"n_frames": 500, "discard": 0, **record}}
        (folder / "options.json").write_text(json.dumps({"analysis": name, "findings": findings}),
                                             encoding="utf-8")
        results[name] = {"status": "ok"}
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({"results": results}),
                                                               encoding="utf-8")
    return root


def _determined(mean, error, unit="nm"):
    return {"mean": mean, "standard_error": error, "effective_samples": 60, "unit": unit}


def _rows(root):
    return {row["quantity"]: row for row in compare_with_paper(root)["rows"]}


def test_a_mean_within_twice_the_combined_error_agrees(tmp_path):
    root = _run(tmp_path / "run", {"rmsd": _determined(0.125, 0.005)},
                [_claim("rmsd", "rmsd", 0.12, 0.01)])
    row = _rows(root)["rmsd"]
    assert row["verdict"] == "agrees"
    assert row["combined_errors_apart"] == pytest.approx(0.005 / (0.005 ** 2 + 0.01 ** 2) ** 0.5)


def test_a_mean_further_apart_disagrees_and_says_by_how_much(tmp_path):
    root = _run(tmp_path / "run", {"rg": _determined(1.30, 0.01)},
                [_claim("radius_of_gyration", "rg", 1.18, 0.02)])
    row = _rows(root)["radius_of_gyration"]
    assert row["verdict"] == "disagrees" and row["combined_errors_apart"] > 5


def test_a_paper_in_angstroms_is_compared_in_nanometres(tmp_path):
    root = _run(tmp_path / "run", {"rmsd": _determined(0.125, 0.005)},
                [_claim("rmsd", "rmsd", 1.2, 0.1, unit="Å")])
    row = _rows(root)["rmsd"]
    assert row["paper_value_here"] == pytest.approx(0.12)
    assert row["verdict"] == "agrees"


def test_a_standard_deviation_over_named_runs_is_made_an_error_of_the_mean():
    assert paper_standard_error({"error": 0.03, "error_kind": "standard_deviation", "n": 9}) \
        == pytest.approx(0.01)
    assert paper_standard_error({"error": 0.0196, "error_kind": "confidence_95"}) \
        == pytest.approx(0.01)
    assert paper_standard_error({"error": 0.03, "error_kind": "standard_deviation"}) is None


def test_a_spread_without_its_count_is_not_compared_as_a_mean(tmp_path):
    root = _run(tmp_path / "run", {"rg": _determined(1.43, 0.004)},
                [_claim("radius_of_gyration", "rg", 1.422, 0.016, kind="standard_deviation")])
    row = _rows(root)["radius_of_gyration"]
    assert row["verdict"] == "within_spread"
    assert "not the error of its mean" in row["why"]


def test_a_value_without_an_error_gets_no_verdict(tmp_path):
    root = _run(tmp_path / "run", {"rmsd": _determined(0.2, 0.01)},
                [_claim("rmsd", "rmsd", 0.12, kind="none")])
    row = _rows(root)["rmsd"]
    assert row["verdict"] == "no_error" and row["difference"] == pytest.approx(0.08)


def test_a_mean_not_determined_here_is_said_as_such(tmp_path):
    root = _run(tmp_path / "run", {"rmsd": {"mean": 0.2, "effective_samples": 4,
                                            "not_a_measurement": "Too few independent samples."}},
                [_claim("rmsd", "rmsd", 0.12, 0.01)])
    row = _rows(root)["rmsd"]
    assert row["verdict"] == "not_determined" and "Too few" in row["why"]


def test_a_quantity_without_a_mean_here_is_not_compared(tmp_path):
    root = _run(tmp_path / "run", {"rmsd": _determined(0.2, 0.01)},
                [_claim("rmsf", "rmsf", 0.1, 0.01), _claim("sasa", "sasa", 50.0, 1.0, unit="nm2")])
    rows = _rows(root)
    assert rows["rmsf"]["verdict"] == "not_compared"
    assert rows["sasa"]["verdict"] == "not_compared"  # sasa did not run here


def test_a_helix_share_in_percent_is_compared_as_a_fraction(tmp_path):
    root = _run(tmp_path / "run", {"ss": _determined(0.21, 0.01, unit="")},
                [_claim("secondary_structure", "ss", 20.0, 1.0, unit="%", what="helix")])
    row = _rows(root)["secondary_structure"]
    assert row["paper_value_here"] == pytest.approx(0.20)
    assert row["verdict"] == "agrees"


def test_replicas_are_compared_by_the_mean_of_their_means(tmp_path):
    root = tmp_path / "study"
    claims = [_claim("rmsd", "rmsd", 0.12, 0.01)]
    runs = []
    for number, mean in enumerate((0.11, 0.13, 0.12), start=1):
        _run(root / "runs" / f"r{number}", {"rmsd": _determined(mean, 0.002)}, claims)
        runs.append({"run_id": f"r{number}", "status": "ok"})
    (root / "batch_manifest.json").write_text(json.dumps({"runs": runs}), encoding="utf-8")
    compared = compare_with_paper(root)
    row = compared["rows"][0]
    assert compared["runs"] == 3 and row["runs"] == 3
    assert row["value"] == pytest.approx(0.12)
    assert row["error"] == pytest.approx(0.01 / 3 ** 0.5)


def test_the_report_has_the_section_only_for_a_study_from_a_paper(tmp_path):
    root = _run(tmp_path / "run", {"rmsd": _determined(0.125, 0.005)},
                [_claim("rmsd", "rmsd", 0.12, 0.01, what="backbone")])
    lines = "\n".join(reproduction_lines(root))
    assert "## Reproducing the paper" in lines
    assert "| rmsd: backbone | 0.12 ± 0.01 nm |" in lines
    assert "agrees" in lines
    plain = _run(tmp_path / "plain", {"rmsd": _determined(0.1, 0.01)})
    assert reproduction_lines(plain) == []


def test_a_replica_s_own_report_says_the_study_pools_them(tmp_path):
    from fastmdxplora.report.document import _reproduction_section

    root = tmp_path / "study"
    claims = [_claim("rmsd", "rmsd", 0.12, 0.01)]
    member = _run(root / "runs" / "r1", {"rmsd": _determined(0.12, 0.002)}, claims)
    (root / "batch_manifest.json").write_text(json.dumps({"runs": [{"run_id": "r1",
                                                                     "status": "ok"}]}))
    section = _reproduction_section(member)
    assert "one run of the study's replicas" in section
