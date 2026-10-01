"""A campaign of replicas sets each run's stated error against their spread.

The read-back for V5r and any campaign like it: ten replicas of one system
give a mean's error a second way, as the spread of their means.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from fastmdxplora.statistics import mean_record
from fastmdxplora.validation import replica_calibration


def _ar1(n: int, seed: int, phi: float = 0.95, sigma: float = 0.05) -> np.ndarray:
    from scipy.signal import lfilter

    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, sigma * math.sqrt(1.0 - phi ** 2), n)
    return lfilter([1.0], [1.0, -phi], noise, zi=[phi * rng.normal(0.0, sigma)])[0]


def _campaign(root, axis="simulation.random_seed", replicas=10, n=4000, with_series=True):
    runs = []
    for k in range(replicas):
        series = 1.0 + 0.15 * np.exp(-np.arange(n) / 300.0) + _ar1(n, 100 + k)
        where = root / "runs" / f"r{k}" / "analysis" / "rg"
        where.mkdir(parents=True)
        record = {k2: (str(v) if k2 == "not_a_measurement" else v)
                  for k2, v in mean_record(series).items()}
        # As an older release might have recorded it: a smaller error.
        record["standard_error"] = record["standard_error"] / 3.0
        (where / "options.json").write_text(json.dumps({"findings": {"mean": record}}))
        if with_series:
            np.savetxt(where / "rg.dat", series, header="rg")
        runs.append({"run_id": f"r{k}", "status": "ok", "system": "1UBQ",
                     "output_dir_relative": f"runs/r{k}", "sweep_values": {axis: k}})
    (root / "batch_manifest.json").write_text(json.dumps(
        {"sweep": {axis: list(range(replicas))}, "runs": runs}))


def test_the_three_ways_are_set_side_by_side(tmp_path, capsys):
    _campaign(tmp_path)
    stamps = sorted(p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file())
    assert replica_calibration.main([str(tmp_path), "--out", str(tmp_path / "out.json")]) == 0
    said = capsys.readouterr().out
    assert "10 replicas" in said and "| rg | as recorded |" in said
    result = json.loads((tmp_path / "out.json").read_text())
    rg = result["analyses"]["rg"]
    assert rg["shared_start_frame"] > 0
    # The error recorded a third as large reads three times too small; the
    # estimator's own error is near the spread.
    assert rg["recorded"]["ratio"] == pytest.approx(3 * rg["own_start"]["ratio"], rel=1e-6)
    assert 0.5 < rg["shared_start"]["ratio"] < 2.0
    # Nothing in the campaign was written.
    after = sorted(p.stat().st_mtime_ns for p in tmp_path.rglob("*")
                   if p.is_file() and p.name != "out.json")
    assert after == stamps


def test_an_analysis_without_its_series_is_left_out(tmp_path, capsys):
    _campaign(tmp_path, with_series=False)
    assert replica_calibration.main([str(tmp_path)]) == 0
    assert "no analysis wrote a series" in capsys.readouterr().out


def test_variants_are_not_replicas(tmp_path, capsys):
    _campaign(tmp_path, axis="setup.ph")
    assert replica_calibration.main([str(tmp_path)]) == 2
    assert "not replicas" in capsys.readouterr().err


def test_a_folder_with_no_campaign_is_said_plainly(tmp_path, capsys):
    assert replica_calibration.main([str(tmp_path)]) == 2
    assert "Cannot compare" in capsys.readouterr().err
