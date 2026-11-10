"""The Report says what the study's records hold, in words that agree with them.

Found by the weekly test of the GUI's study pages (10-13): the written rule
for when a mean gets an error left out an exemption the estimator applies;
a count of independent samples under one was rounded to "0"; an analysis's
parameters ran into one paragraph; and a run on the CPU was said to have
used the precision it asked for, which only the GPU platforms take.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fastmdxplora import statistics


def _sim(platform: str, precision: str = "mixed") -> dict:
    return {
        "parameters": {
            "timestep_fs": 2.0, "integrator": "langevin_middle",
            "temperature_K": 300.0, "friction_per_ps": 1.0,
            "nvt_steps": 200, "npt_steps": 200, "production_steps": 2000,
            "trajectory_interval_steps": 20, "platform": "auto",
            "precision": precision, "minimize": True, "random_seed": 7,
        },
        "platform_used": platform,
    }


def _methods(sim: dict, **kwargs) -> str:
    from fastmdxplora.report.methods import methods_paragraphs

    return methods_paragraphs(Path("."), {}, sim, **kwargs)


# -- when a mean is given an error -------------------------------------------
def _ar1(n: int, phi: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.empty(n)
    out[0] = rng.normal()
    for i in range(1, n):
        out[i] = phi * out[i - 1] + rng.normal()
    return out


def test_the_methods_say_the_exemption_the_estimator_applies():
    from fastmdxplora.statistics import mean_record

    said = _methods({}, means={"rg": {**mean_record(1.1 + 0.01 * _ar1(4000, 0.5, 1)),
                                      "unit": "nm"}})
    assert ("An error is given only where the frames averaged hold at least "
            f"{statistics.MINIMUM_EFFECTIVE_SAMPLES:g} independent samples and span at "
            f"least {statistics.RESOLVED_SAMPLES:g} times their own statistical "
            "inefficiency.") in said
    assert ("Frames whose statistical inefficiency, before the correction for the "
            f"sample mean, is under {statistics.UNCORRELATED_INEFFICIENCY:g} are exempt "
            "from the second condition; their error is still computed with the "
            "corrected inefficiency.") in said


def test_the_exemption_in_the_words_is_the_one_in_the_code():
    # Nearly independent frames: determined although they hold fewer than
    # RESOLVED_SAMPLES of their inefficiencies would ask of a correlated run.
    values = 1.0 + 0.01 * np.random.default_rng(4).normal(size=30)
    equilibrated, withheld = statistics.summarise(values)
    kept = values[equilibrated.discard:]
    assert equilibrated.effective_samples < statistics.RESOLVED_SAMPLES
    assert statistics.statistical_inefficiency(kept) < statistics.UNCORRELATED_INEFFICIENCY
    assert statistics.correlation_is_resolved(kept)
    assert withheld is None and np.isfinite(equilibrated.standard_error)


def test_a_withheld_error_says_the_exemption():
    values = 1.0 + 0.01 * _ar1(400, 0.97, 5)
    equilibrated, withheld = statistics.summarise(values)
    assert withheld is not None
    assert not np.isfinite(equilibrated.standard_error)
    said = str(withheld)
    assert ("(only frames whose statistical inefficiency, before the correction for "
            f"the sample mean, is under {statistics.UNCORRELATED_INEFFICIENCY:g} are "
            "exempt)") in said
    # No second inefficiency beside the one the record and the page give.
    assert "these have" not in said


# -- how many independent samples a mean is from ------------------------------
@pytest.mark.parametrize("effective, said", [
    (0.42, ", from fewer than one independent sample"),
    (0.999, ", from fewer than one independent sample"),
    (1.0, ", from 1 independent sample"),
    (1.4, ", from 1.4 independent samples"),
    (30.2, ", from 30.2 independent samples"),
])
def test_a_count_under_one_is_not_rounded_to_none(effective, said):
    from fastmdxplora.report.document import _findings_notes

    [note] = _findings_notes({"mean": {"mean": 0.0789, "standard_error": float("nan"),
                                       "unit": "nm", "effective_samples": effective}})
    assert said + "." in note or said + " after" in note
    assert "from 0 independent" not in note


# -- an analysis's parameters --------------------------------------------------
def test_the_parameters_are_a_list_in_the_report_as_rendered(tmp_path):
    pytest.importorskip("markdown")
    from fastmdxplora.report.document import _results_section
    from fastmdxplora.report.markdown_html import markdown_as_html

    from tests.test_report_wiring import _a_reported_run

    root = _a_reported_run(tmp_path, n_frames=40)
    section = _results_section(root)
    assert "**Parameters:**\n\n- " in section
    html = markdown_as_html(section)
    at = html.index("<strong>Parameters:</strong>")
    after = html[at:]
    assert after.index("<ul>") < after.index("</p>") + len("</p>") + 2
    assert "<p><strong>Parameters:</strong> - " not in html
    # The findings are a list of their own, not more parameters.
    parameters = after[:after.index("</ul>")]
    assert "Mean over the equilibrated part" not in parameters
    assert "<strong>Findings:</strong>" in html


# -- the precision a run used -------------------------------------------------
@pytest.mark.parametrize("platform", ["CPU", "Reference"])
def test_a_run_on_a_platform_without_precision_is_not_said_to_use_one(platform):
    said = _methods(_sim(platform))
    assert f"Simulations used OpenMM on the {platform} platform." in said
    assert "precision" not in said


@pytest.mark.parametrize("platform", ["CUDA", "OpenCL", "HIP"])
def test_a_run_on_a_gpu_platform_says_its_precision(platform):
    said = _methods(_sim(platform, "double"))
    assert f"Simulations used OpenMM on the {platform} platform in double precision." in said


@pytest.mark.parametrize("discard, said", [(1, " after discarding 1 frame."),
                                           (12, " after discarding 12 frames.")])
def test_one_frame_discarded_is_said_as_one(discard, said):
    from fastmdxplora.report.document import _findings_notes

    [note] = _findings_notes({"mean": {"mean": 0.2, "standard_error": 0.01,
                                       "effective_samples": 30, "discard": discard}})
    assert note.endswith(said)
