"""A mean is given to the place its error allows, the same on every page.

The GUI and the Agent gave ``0.1120 ± 0.0020`` while the report beside them
gave ``0.112 ± 0.00201``, the slides the same, the campaign's table
``0.112 ± 0.002`` and the stopping rule's table again its own. One record,
four numbers for a reader to reconcile. Now one function says them all.
"""

from __future__ import annotations

import json

import pytest

from fastmdxplora.statistics import with_its_error

RECORD = {"mean": -123456.7, "standard_error": 12.0, "unit": "kJ/mol",
          "effective_samples": 30.2, "discard": 0}


@pytest.mark.parametrize("value, error, said", [
    (0.1234, 0.0056, "0.1234 ± 0.0056"),
    (0.15, 0.004, "0.1500 ± 0.0040"),
    (0.112, 0.00201, "0.1120 ± 0.0020"),
    (-123456.7, 12.0, "-123,457 ± 12"),
    (0.0001234, 0.12, "0.00 ± 0.12"),
    (5432.1, 0.0004, "5,432.10000 ± 0.00040"),
    (1.5, None, "1.5"),
    (1.5, float("nan"), "1.5"),
    (2.0, 0.0, "2 ± 0"),
])
def test_the_error_to_two_figures_and_the_mean_to_its_place(value, error, said):
    assert with_its_error(value, error) == said


def test_a_difference_keeps_its_sign():
    assert with_its_error(0.0266, 0.0069, sign=True) == "+0.0266 ± 0.0069"
    assert with_its_error(-0.0266, 0.0069, sign=True) == "-0.0266 ± 0.0069"


def test_the_report_says_it_as_the_gui_and_the_agent_do():
    from fastmdxplora.gui.report_dashboard import _with_its_error
    from fastmdxplora.mcp.tools import with_error
    from fastmdxplora.report.document import _findings_notes

    said = " ".join(_findings_notes({"mean": RECORD}))
    assert "-123,457 ± 12 kJ/mol, from 30 independent samples" in said
    assert _with_its_error(-123456.7, 12.0) == with_error(-123456.7, 12.0) == "-123,457 ± 12"


def test_a_report_mean_with_no_error_is_not_given_one():
    from fastmdxplora.report.document import _findings_notes

    said = " ".join(_findings_notes({"mean": {"mean": 0.1234567,
                                              "standard_error": float("nan")}}))
    assert "0.1235." in said and "±" not in said


def test_the_stopping_rule_s_table_says_it_so(tmp_path):
    from fastmdxplora.simulation.stopping import stopping_section

    (tmp_path / "stopping.json").write_text(json.dumps({
        "targets": [{"analysis": "potential_energy", "standard_error": 20.0}],
        "max_duration_ns": 50.0, "runs": ["run"], "outcome": "met",
        "rounds": [{"production_ns": 10.0, "decision": "met", "verdicts": [
            {"analysis": "potential_energy", "value": -123456.7, "error": 12.0,
             "allowed": 20.0, "unit": "kJ/mol"}]}]}), encoding="utf-8")
    table = "\n".join(stopping_section(tmp_path))
    assert "| -123,457 ± 12 (±20 asked) |" in table


def test_the_figure_s_legend_says_it_so():
    from fastmdxplora.analysis.base import Analysis

    class Legend:
        _independent_samples = staticmethod(Analysis._independent_samples)

        def _mean_unit(self):
            return " kJ/mol"

    label = Analysis._mean_label(Legend(), RECORD, True)
    assert label.startswith("mean after equilibration -123,457 ± 12 kJ/mol")


def test_a_reweighted_spread_is_not_said_as_an_error():
    """The spread beside a reweighted mean is the width of what it averages,
    not the mean's error, and a ``±`` after a mean is read as its error."""
    from fastmdxplora.report.reweighted import reweighted_line

    line = reweighted_line({"quantities": [{
        "analysis": "rmsd", "reweighted_mean": 0.2813, "reweighted_std": 0.0421,
        "raw_mean": 0.25, "shift_percent": 12.5}],
        "effective_sample_size": 120.0, "n_frames": 1000}, "rmsd")
    assert "Reweighted mean: 0.2813 (s.d. 0.042)" in line and "±" not in line
