"""An analysis that keys its finding by its own name is named once.

`read_study` said "order_parameters: order_parameters mean 0.8565": the
summary dropped the finding's key only where it was "mean", and the order
parameters key theirs by the analysis's name. Found running `fastmdx mcp`
from an AI app (2026-10-01).
"""

from __future__ import annotations

import json


def _study(tmp_path):
    folder = tmp_path / "study" / "analysis" / "order_parameters"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text(json.dumps({
        "analysis": "order_parameters",
        "findings": {
            "order_parameters": {"mean": 0.8565, "standard_error": 0.004,
                                 "effective_samples": 40.0, "unit": ""},
            "loops": {"mean": 0.61, "standard_error": 0.02, "unit": ""}}}), encoding="utf-8")
    (tmp_path / "study" / "manifest.json").write_text("{}", encoding="utf-8")
    return tmp_path / "study"


def test_the_summary_names_it_once(tmp_path):
    from fastmdxplora.gui.agent_panel import _results_summary

    text = _results_summary(_study(tmp_path))
    assert "order_parameters order_parameters" not in text
    assert "order_parameters: mean 0.8565 ± 0.0040 (s.e.)" in text
    # A finding keyed otherwise still says which it is.
    assert "loops mean 0.610 ± 0.020" in text


def test_an_ai_app_reads_it_once(tmp_path):
    from fastmdxplora.mcp.tools import study_record

    said = study_record(_study(tmp_path))
    assert "order_parameters order_parameters" not in said
    assert "order_parameters: mean 0.8565" in said
