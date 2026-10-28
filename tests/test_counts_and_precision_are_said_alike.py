"""A count of independent samples, and the precision a run used, said alike.

Found beside the weekly test's fixes to the Report (1845): the slides and
the reweighted averages still rounded a count under one to 0 and said "1
independent samples"; two checks wrote the exemption's 2 as a literal; and
the start banner said "CPU (mixed precision)", a precision the CPU platform
never takes.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from unittest import mock

import numpy as np
import pytest

from fastmdxplora import statistics
from fastmdxplora.statistics import independent_samples_said


# -- one way to say a count -----------------------------------------------------
@pytest.mark.parametrize("count, said", [
    (0.0, "fewer than one independent sample"),
    (0.42, "fewer than one independent sample"),
    (0.999, "fewer than one independent sample"),
    (1.0, "1 independent sample"),
    (1.001, "1 independent sample"),
    (1.49, "1.4 independent samples"),
    (2.6, "2.6 independent samples"),
    (9.6, "9.6 independent samples"),
    (9.96, "9.9 independent samples"),
    (9.99, "9.9 independent samples"),
    (10.4, "10.4 independent samples"),
    (21.27, "21.2 independent samples"),
    (21.0, "21 independent samples"),
    (24.999999999999996, "24.9 independent samples"),
    (25.0, "25 independent samples"),
    (30.2, "30.2 independent samples"),
    (float("nan"), "a number of independent samples that could not be counted"),
])
def test_a_count_is_said_one_way(count, said):
    assert independent_samples_said(count) == said


def test_about_hedges_the_count_unless_it_is_under_one():
    assert independent_samples_said(16.9, about=True) == "about 16.9 independent samples"
    assert independent_samples_said(0.4, about=True) == "fewer than one independent sample"
    # Beside a threshold of ten, a count under ten never reads as ten.
    assert independent_samples_said(9.6, about=True) == "about 9.6 independent samples"
    assert independent_samples_said(2.4, about=True) == "about 2.4 independent samples"


def test_the_slides_say_a_count_under_one_as_the_report_does(monkeypatch, tmp_path):
    from fastmdxplora.report import document, slides

    assessed = {"observables": {"rmsd": {
        "observable": "RMSD", "mean": 0.0789, "standard_error": float("nan"),
        "effective_samples": 0.42, "equilibrated": None}}, "findings": []}
    monkeypatch.setattr(document, "_assess_this_run", lambda root: assessed)
    [bullet] = slides._findings_bullets(tmp_path)
    assert "[fewer than one independent sample]" in bullet
    assert "0 independent" not in bullet


def test_the_reweighted_caption_says_a_count_under_one(monkeypatch, tmp_path):
    pytest.importorskip("matplotlib")
    from fastmdxplora.analysis import reweighted_averages

    saved = []
    monkeypatch.setattr(reweighted_averages, "save_figure",
                        lambda fig, path, *a, **k: saved.append(fig))
    record = {"effective_sample_size": 3.2, "n_frames": 400, "independent_samples": 0.3,
              "converged": True,
              "quantities": [{"label": "RMSD (nm)", "shift_percent": 4.0,
                              "raw_mean": 0.25, "reweighted_mean": 0.26}]}
    reweighted_averages._plot(record, tmp_path / "plot.png")
    [figure] = saved
    caption = figure.axes[0].get_xlabel()
    assert "fewer than one independent sample" in caption
    assert "about 0 independent" not in caption


@pytest.mark.parametrize("independent, said", [
    (0.3, "fewer than one independent sample"),
    (1.0, "about 1 independent sample"),
    (2.4, "about 2.4 independent samples"),
])
def test_the_report_s_reweighted_line_says_the_count_as_its_plot_does(independent, said):
    from fastmdxplora.report.reweighted import _rests_on

    line = _rests_on({"effective_sample_size": 4.0, "n_frames": 400,
                      "independent_samples": independent})
    assert line == f"4.0 weight-concentration effective frames of 400, {said}"


def test_a_reweighted_mean_withheld_for_too_few_samples_says_the_count_one_way():
    from fastmdxplora.analysis.reweight import Weights, reweighted_estimate

    values = 1.0 + 0.01 * np.random.default_rng(2).normal(size=400)
    # Weight concentrated into a few frames: fewer than 10 independent samples.
    weights = np.full(400, 1e-3)
    weights[:6] = 1.0
    weights *= 400 / weights.sum()
    kish = float(weights.sum() ** 2 / (weights ** 2).sum())
    record = reweighted_estimate(values, Weights(weights, kish, True))
    said = str(record.get("not_a_measurement") or "")
    assert record.get("refusal") == "analysis.sampling.too_few_independent"
    assert "hold about 6.8 independent samples." in said


# -- the exemption's 2, from one place -----------------------------------------
def test_a_reweighted_error_follows_the_exemption_constant(monkeypatch):
    from fastmdxplora.analysis.reweight import Weights, reweighted_estimate

    # Thirty frames, each its own weight, correlated a little (1.5 frames to
    # each sample) by the collective variable: exempt at 2, held to the 25
    # at 1.2.
    values = 1.0 + 0.01 * np.random.default_rng(7).normal(size=30)
    weights = Weights(np.ones(30), 30.0, True)
    given = reweighted_estimate(values, weights, inefficiency=1.5)
    assert given["standard_error"] is not None
    monkeypatch.setattr(statistics, "UNCORRELATED_INEFFICIENCY", 1.2)
    withheld = reweighted_estimate(values, weights, inefficiency=1.5)
    assert withheld["standard_error"] is None


def test_an_occupancy_s_error_follows_the_exemption_constant(monkeypatch):
    from fastmdxplora.analysis.interaction_summary import Occupancy

    # Correlated a little (1.5 frames to each sample) and short against 25
    # of them: exempt at 2, held to the 25 at 1.2.
    seen = Occupancy("hbond", 1, 2, frames_present=10, frames_total=20, episodes=4,
                     inefficiency=1.5)
    assert np.isfinite(seen.uncertainty)
    monkeypatch.setattr(statistics, "UNCORRELATED_INEFFICIENCY", 1.2)
    assert np.isnan(seen.uncertainty)


# -- the precision a run is said to use -----------------------------------------
def _banner(argv: list[str]) -> str:
    from fastmdxplora.utils.presenter import SessionPresenter

    buf = io.StringIO()
    with mock.patch.object(sys, "argv", ["fastmdx"] + argv):
        with redirect_stdout(buf):
            SessionPresenter().banner(System="1L2Y.pdb", Output="runs/x", Version="0.0.0")
    return buf.getvalue()


def _platform_line(text: str) -> str:
    [line] = [line for line in text.splitlines() if "Platform" in line]
    return line


def test_the_banner_names_no_precision_on_the_cpu():
    line = _platform_line(_banner(["run", "-s", "1L2Y.pdb", "--simulate-platform", "CPU"]))
    assert "CPU" in line and "precision" not in line


@pytest.mark.parametrize("platform", ["CUDA", "OpenCL", "HIP"])
def test_the_banner_names_the_precision_on_a_gpu_platform(platform):
    line = _platform_line(_banner(["run", "-s", "1L2Y.pdb", "--simulate-platform", platform,
                                   "--simulate-precision", "double"]))
    assert f"{platform} (double precision)" in line


def test_the_banner_says_auto_takes_the_precision_only_on_a_gpu():
    line = _platform_line(_banner(["run", "-s", "1L2Y.pdb", "--simulate-platform", "auto"]))
    assert "auto (mixed precision if a GPU is chosen)" in line


def test_a_count_just_under_ten_is_not_said_as_ten_beside_the_threshold():
    from fastmdxplora.analysis.reweight import Weights, reweighted_estimate

    values = 1.0 + 0.01 * np.random.default_rng(3).normal(size=400)
    # Weight concentrated into about 9.6 frames, nearly uncorrelated.
    weights = np.full(400, 2e-3)
    weights[:8] = 1.0
    weights *= 400 / weights.sum()
    kish = float(weights.sum() ** 2 / (weights ** 2).sum())
    record = reweighted_estimate(values, Weights(weights, kish, True))
    assert record.get("refusal") == "analysis.sampling.too_few_independent"
    said = str(record.get("not_a_measurement") or "")
    assert "hold about 9.6 independent samples. Below 10" in said
