"""Replicas from one structure average from the start they share.

They share the relaxation from that structure, and each run's own
equilibration detection sees it through the run's noise: calibrated on series
with a known mean, three such replicas kept the same little of it and their
pooled mean was biased by about half its error. On their frame-by-frame
average the relaxation is the same and the noise smaller.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from fastmdxplora.statistics import detect_equilibration, mean_record, shared_start, summarise


def _ar1(n: int, seed: int, phi: float = 0.95, sigma: float = 0.05) -> np.ndarray:
    from scipy.signal import lfilter

    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, sigma * math.sqrt(1.0 - phi ** 2), n)
    return lfilter([1.0], [1.0, -phi], noise, zi=[phi * rng.normal(0.0, sigma)])[0]


def _relaxing(n: int, seed: int) -> np.ndarray:
    return 1.0 + 0.15 * np.exp(-np.arange(n) / 300.0) + _ar1(n, seed)


def test_the_shared_start_sees_a_relaxation_one_run_keeps_some_of():
    later = 0
    for trial in range(20):
        runs = [_relaxing(2200, 3 * trial + k) for k in range(3)]
        own = np.median([detect_equilibration(r)[0] for r in runs])
        later += shared_start(runs) > own
    assert later >= 15


def test_stationary_replicas_share_an_early_start():
    starts = [shared_start([1.0 + _ar1(2000, 3 * t + k) for k in range(3)]) for t in range(20)]
    assert np.median(starts) <= 0.15 * 2000


def test_one_run_or_none_has_nothing_to_share():
    assert shared_start([]) == 0
    assert shared_start([_relaxing(500, 1)]) == 0


def test_runs_of_different_lengths_share_over_the_frames_they_all_have():
    runs = [_relaxing(2000, 1), _relaxing(2400, 2), _relaxing(2200, 3)]
    assert 0 < shared_start(runs) < 2000


def test_a_series_averages_from_no_earlier_than_the_shared_start():
    series = _relaxing(2000, 4)
    own, _ = summarise(series)
    later, _ = summarise(series, start_at_least=own.discard + 300)
    assert later.discard == own.discard + 300
    furthest, _ = summarise(series, start_at_least=10 ** 6)
    assert furthest.discard == int(2000 * 2 / 3)
    earlier, _ = summarise(series, start_at_least=1)
    assert earlier.discard == own.discard


def test_the_record_says_the_start_was_shared():
    record = mean_record(_relaxing(2000, 5), start_at_least=900)
    assert record["start_shared_with_replicas"] == 900 and record["discard"] == 900
    assert "start_shared_with_replicas" not in mean_record(_relaxing(2000, 5))


def _write(run, series, **extra):
    where = run / "analysis" / "rmsd"
    where.mkdir(parents=True)
    record = {**mean_record(series, frame_interval_ns=0.01), **extra}
    record = {k: (str(v) if k == "not_a_measurement" else v) for k, v in record.items()}
    (where / "options.json").write_text(json.dumps({"findings": {"mean": record}}),
                                        encoding="utf-8")
    np.savetxt(where / "rmsd.dat", series, fmt="%.17g", header="rmsd")


def _read(run):
    return json.loads((run / "analysis" / "rmsd" / "options.json").read_text())["findings"]["mean"]


def test_the_rule_writes_each_replica_from_the_shared_start(tmp_path):
    from fastmdxplora.simulation.stopping import StopTarget, judge

    runs = []
    for k in range(3):
        _write(tmp_path / f"run_{k}", _relaxing(2200, 40 + k), unit="nm")
        runs.append(tmp_path / f"run_{k}")
    at = shared_start([_relaxing(2200, 40 + k) for k in range(3)])
    owns = [_read(run)["discard"] for run in runs]
    assert at > min(owns)
    judge(runs, [StopTarget("rmsd", standard_error=0.01)], 22.0)
    for run, own in zip(runs, owns):
        record = _read(run)
        assert record["discard"] == max(own, at)
        assert record["unit"] == "nm", "what the analysis wrote beside the mean is kept"
        if at > own:
            assert record["start_shared_with_replicas"] == at


def test_without_the_series_the_records_are_judged_as_written(tmp_path):
    from fastmdxplora.simulation.stopping import share_the_start

    runs = []
    for k in range(3):
        _write(tmp_path / f"run_{k}", _relaxing(2200, 50 + k))
        runs.append(tmp_path / f"run_{k}")
    (runs[1] / "analysis" / "rmsd" / "rmsd.dat").unlink()
    before = [_read(run) for run in runs]
    assert share_the_start(runs, "rmsd", 22.0) == 0
    assert [_read(run) for run in runs] == before


def test_a_series_of_another_length_is_not_used(tmp_path):
    from fastmdxplora.simulation.stopping import share_the_start

    runs = []
    for k in range(2):
        _write(tmp_path / f"run_{k}", _relaxing(2200, 60 + k))
        runs.append(tmp_path / f"run_{k}")
    np.savetxt(runs[0] / "analysis" / "rmsd" / "rmsd.dat", _relaxing(2100, 60))
    assert share_the_start(runs, "rmsd", 22.0) == 0


def test_a_comma_separated_file_with_a_header_is_read(tmp_path):
    from fastmdxplora.simulation.stopping import _series_in

    path = tmp_path / "rmsd.dat"
    path.write_text("frame,rmsd\n0,0.10\n1,0.12\n2,0.11\n", encoding="utf-8")
    assert _series_in(path).tolist() == pytest.approx([0.10, 0.12, 0.11])
    assert _series_in(tmp_path / "absent.dat") is None


def _campaign(root, sweep_axis="simulation.random_seed"):
    runs = []
    for k in range(3):
        _write(root / "runs" / f"r{k}", _relaxing(2200, 70 + k), unit="nm")
        runs.append({"run_id": f"r{k}", "status": "ok", "system": "1UAO",
                     "output_dir_relative": f"runs/r{k}", "sweep_values": {sweep_axis: k}})
    (root / "batch_manifest.json").write_text(json.dumps(
        {"sweep": {sweep_axis: [0, 1, 2]}, "runs": runs}), encoding="utf-8")
    return [root / "runs" / f"r{k}" for k in range(3)]


def test_a_campaign_of_replicas_compares_them_from_the_shared_start(tmp_path):
    """Replicas run for a fixed length are compared as the rule judges them:
    their spread and pooled mean from the start they share."""
    from fastmdxplora.batch.aggregate import aggregate_members

    members = _campaign(tmp_path)
    at = shared_start([_relaxing(2200, 70 + k) for k in range(3)])
    owns = [_read(m)["discard"] for m in members]
    assert at > min(owns)
    summary = aggregate_members(tmp_path)
    shared = [_read(m) for m in members]
    assert [r["discard"] for r in shared] == [max(own, at) for own in owns]
    assert summary["analyses"]["rmsd"]["mean_of_means"] == pytest.approx(
        np.mean([r["mean"] for r in shared]))
    # Read again, nothing is written again.
    stamps = [(m / "analysis" / "rmsd" / "options.json").stat().st_mtime_ns for m in members]
    aggregate_members(tmp_path)
    assert stamps == [(m / "analysis" / "rmsd" / "options.json").stat().st_mtime_ns
                      for m in members]


def test_variants_keep_their_own_starts(tmp_path):
    from fastmdxplora.batch.aggregate import aggregate_members

    members = _campaign(tmp_path, sweep_axis="setup.ph")
    before = [_read(m) for m in members]
    aggregate_members(tmp_path)
    assert [_read(m) for m in members] == before


def test_a_study_that_cannot_be_written_is_read_as_it_stands(tmp_path, monkeypatch):
    from pathlib import Path

    from fastmdxplora.simulation.stopping import share_the_start

    members = _campaign(tmp_path)
    before = [_read(m) for m in members]

    def refuse(self, *args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "write_text", refuse)
    assert share_the_start(members, "rmsd") == 0
    monkeypatch.undo()
    assert [_read(m) for m in members] == before
