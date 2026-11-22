"""The analysis phase's two manifests are strict JSON, so any reader parses
them.

A mean whose error is withheld kept its error as NaN, and `json.dump` wrote
that as the bare word `NaN`, which JSON does not have: Python's `json.loads`
read it, and `JSON.parse`, `jq` and every other reader refused the whole
`options.json`. A load argument of infinity did the same to
`analysis_manifest.json`. Both now write `null` for what is not a finite
number, and the reason a mean is withheld stays beside it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from fastmdxplora.analysis.analyze import run as run_analysis
from fastmdxplora.analysis.end_to_end import EndToEndDistance
from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator
from fastmdxplora.orchestrator import PhaseSkipped
from tests.test_end_to_end import _straight_chains


def _refused(word: str):
    raise ValueError(f"{word} is not JSON")


def _strict(path: Path):
    """What `JSON.parse` would read: the file, refusing NaN and Infinity."""
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_refused)


def _a_chain_reaching_its_image():
    """A chain as long as half its box: its mean end-to-end distance is
    withheld, and the error given beside it is NaN."""
    traj = _straight_chains(n_residues=10, spacing=0.38, box=4.0, frames=400)
    rng = np.random.default_rng(0)
    traj.xyz = (traj.xyz + rng.normal(0.0, 0.01, traj.xyz.shape)).astype(np.float32)
    traj.time = np.arange(400) * 10.0
    return traj


def test_a_withheld_mean_is_written_as_null_with_its_reason(tmp_path):
    analysis = EndToEndDistance(output_dir=tmp_path)
    assert analysis.run(_a_chain_reaching_its_image()).status == "ok"
    assert np.isnan(analysis.findings["mean"]["standard_error"])

    mean = _strict(tmp_path / "end_to_end" / "options.json")["findings"]["mean"]

    assert mean["standard_error"] is None
    assert "its own copy" in mean["not_a_measurement"]
    assert mean["error_withheld_because"]
    assert mean["mean"] == pytest.approx(9 * 0.38, abs=0.01)


def test_the_readers_still_find_the_mean_withheld_and_give_it_no_error(tmp_path):
    from fastmdxplora.batch.aggregate import _finite_or_none
    from fastmdxplora.gui.analysis_overview import _finite
    from fastmdxplora.report.convergence import _number
    from fastmdxplora.statistics import withheld_reason

    EndToEndDistance(output_dir=tmp_path).run(_a_chain_reaching_its_image())
    mean = _strict(tmp_path / "end_to_end" / "options.json")["findings"]["mean"]

    assert "its own copy" in withheld_reason(mean)
    for reader in (_finite_or_none, _finite, _number):
        assert reader(mean["standard_error"]) is None
        assert reader(mean["mean"]) == pytest.approx(9 * 0.38, abs=0.01)


def test_every_nonfinite_number_in_the_findings_is_null(tmp_path):
    analysis = EndToEndDistance(output_dir=tmp_path)
    analysis.output_dir.mkdir()
    analysis.findings = {
        "nan": float("nan"), "inf": float("inf"), "minus_inf": float("-inf"),
        "nested": {"values": [1.0, float("nan"), {"deep": float("-inf")}],
                   "pair": (float("inf"), 2)},
        "numpy": np.float64("nan"),
        "finite": 1.5, "whole": 3, "yes": True, "text": "NaN", "nothing": None,
    }

    found = _strict(analysis._write_options_manifest())["findings"]

    assert found == {
        "nan": None, "inf": None, "minus_inf": None,
        "nested": {"values": [1.0, None, {"deep": None}], "pair": [None, 2]},
        "numpy": None,
        "finite": 1.5, "whole": 3, "yes": True, "text": "NaN", "nothing": None,
    }


def test_the_phase_manifest_is_strict_where_a_load_argument_is_not_finite(tmp_path):
    traj = _a_chain_reaching_its_image()
    with patch("fastmdxplora.analysis.orchestrator.load_trajectory", return_value=traj):
        orchestrator = AnalysisOrchestrator(
            "synthetic.dcd", output_dir=tmp_path, saving_interval_ps=float("inf"))
    orchestrator._write_manifest()

    manifest = _strict(tmp_path / "analysis_manifest.json")

    assert manifest["load_kwargs"]["saving_interval_ps"] is None
    assert manifest["n_frames"] == 400


def test_a_deferred_phase_writes_strict_json(tmp_path):
    (tmp_path / "analysis").mkdir()
    run_analysis(orchestrator=SimpleNamespace(output_dir=tmp_path),
                 output_dir=tmp_path / "analysis")

    manifest = _strict(tmp_path / "analysis" / "analysis_manifest.json")

    assert manifest["status"] == "deferred"
    assert manifest["expected_trajectory"].endswith("production.dcd")


def test_a_skipped_phase_writes_strict_json(tmp_path):
    (tmp_path / "analysis").mkdir()
    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "simulation_parameters.json").write_text(
        json.dumps({"resolved": {"production_steps": 0}}), encoding="utf-8")

    with pytest.raises(PhaseSkipped):
        run_analysis(orchestrator=SimpleNamespace(output_dir=tmp_path),
                     output_dir=tmp_path / "analysis")

    manifest = _strict(tmp_path / "analysis" / "analysis_manifest.json")
    assert manifest["status"] == "skipped"
    assert "no production" in manifest["note"]


def test_the_writer_gives_numpy_numbers_as_plain_ones_and_refuses_unknown_objects(tmp_path):
    from fastmdxplora.analysis.base import write_strict_json

    path = tmp_path / "record.json"
    write_strict_json(path, {"a": np.float64("inf"), "b": np.int64(7), "c": np.bool_(True),
                             "d": np.float32(0.5), "e": np.str_("text")})
    assert _strict(path) == {"a": None, "b": 7, "c": True, "d": 0.5, "e": "text"}

    with pytest.raises(TypeError):
        write_strict_json(path, {"unknown": object()})
