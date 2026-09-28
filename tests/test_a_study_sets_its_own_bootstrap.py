"""An umbrella study sets how many resamples lie behind its interval.

`bootstrap_resamples` reached `compute_pmf` and nothing a study could write,
so a recombination always paid for two hundred: 13.6 s on seventeen windows
of three thousand samples, where the curve itself took 0.04 s, and minutes on
thirty-five windows before any curve could be looked at.
"""

from __future__ import annotations

import pytest

from fastmdxplora.refusals import StudyError
from fastmdxplora.simulation.umbrella import (
    expand_umbrella,
    plan_from_expanded,
    plan_windows,
)
from fastmdxplora.uncertainty import DEFAULT_RESAMPLES

BLOCK = {"collective_variable": "distance", "select_atoms_a": "resid 1",
         "select_atoms_b": "resid 2", "force_constant": 1000,
         "from": 0.4, "to": 1.2, "n_windows": 5}


def test_unset_it_is_the_default() -> None:
    assert plan_windows(dict(BLOCK)).bootstrap_resamples == DEFAULT_RESAMPLES


@pytest.mark.parametrize("given", [0, 50, 1000, 40.0])
def test_a_study_sets_its_own(given) -> None:
    plan = plan_windows({**BLOCK, "bootstrap_resamples": given})
    assert plan.bootstrap_resamples == int(given)
    assert plan.as_record()["bootstrap_resamples"] == int(given)


@pytest.mark.parametrize("given", [-1, 2.5, True, "many", None])
def test_what_is_not_a_count_is_refused(given) -> None:
    with pytest.raises(StudyError, match="bootstrap_resamples"):
        plan_windows({**BLOCK, "bootstrap_resamples": given})


def test_it_survives_the_expansion_into_windows() -> None:
    """The recombination rebuilds the plan from the windows."""
    expanded = expand_umbrella({"systems": [{"id": "s", "system": "x.pdb"}],
                                "simulation": {"umbrella": {**BLOCK,
                                                            "bootstrap_resamples": 30}}})
    assert plan_from_expanded(expanded).bootstrap_resamples == 30


def test_the_recombination_is_given_it(tmp_path, monkeypatch) -> None:
    from fastmdxplora.batch import explorer as explorer_module
    from fastmdxplora.simulation import umbrella

    seen = {}
    monkeypatch.setattr(umbrella, "collect_samples", lambda *a, **k: {})
    monkeypatch.setattr(umbrella, "compute_pmf",
                        lambda *a, **k: seen.update(k) or {"pmf": None})
    batch = explorer_module.BatchExplorer.__new__(explorer_module.BatchExplorer)
    batch._raw = expand_umbrella({"systems": [{"id": "s", "system": "x.pdb"}],
                                  "simulation": {"umbrella": {**BLOCK,
                                                              "bootstrap_resamples": 12}}})
    batch.run_specs = []
    batch.output_dir = tmp_path
    try:
        batch._maybe_build_pmf()
    except Exception:  # noqa: BLE001 - only the call to compute_pmf is checked
        pass
    assert seen.get("bootstrap_resamples") == 12
