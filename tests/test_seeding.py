"""Seeding umbrella windows from a steered pull.

The parts that need OpenMM are skipped where it is absent; everything that
decides *which* frame a window starts from is plain arithmetic and is tested
here, because that is the decision a wrong answer would hide in.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.simulation.seeding import (
    COLVAR_AGREEMENT_NM,
    ENERGY_TOLERANCE_KJMOL_PER_ATOM,
    _check_against_colvar,
    _refuse_an_impossible_seed,
    frames_for_centres,
    read_colvar,
)
from fastmdxplora.simulation.umbrella import expand_umbrella


COLVAR = """\
#! FIELDS time cv restraint.bias
 1500.000000 0.333884 2.185643
 1500.200000 0.356577 0.942786
 1500.400000 0.343765 1.581183
"""


def test_the_colvar_columns_are_found_by_name(tmp_path):
    path = tmp_path / "COLVAR"
    path.write_text(COLVAR, encoding="utf-8")

    time, cv = read_colvar(path)

    assert time[0] == pytest.approx(1500.0)
    assert cv[0] == pytest.approx(0.333884)
    assert cv.size == 3


def test_a_reordered_header_does_not_shift_the_variable(tmp_path):
    """The second column is not always `cv`.

    A run that biases something else, or prints the work, writes more
    columns and in a different order. Reading position 1 gave the bias
    instead of the variable, which is a plausible-looking number.
    """
    path = tmp_path / "COLVAR"
    path.write_text(
        "#! FIELDS time restraint.bias cv\n"
        " 1500.000000 2.185643 0.333884\n"
        " 1500.200000 0.942786 0.356577\n",
        encoding="utf-8")

    _, cv = read_colvar(path)

    assert cv[0] == pytest.approx(0.333884)


def test_a_colvar_without_the_variable_is_no_colvar(tmp_path):
    path = tmp_path / "COLVAR"
    path.write_text("#! FIELDS time restraint.bias\n 1500.0 2.18\n",
                    encoding="utf-8")

    assert read_colvar(path) is None


def test_a_missing_colvar_is_not_an_error(tmp_path):
    assert read_colvar(tmp_path / "nothing") is None


def test_each_window_takes_the_frame_nearest_its_centre():
    measured = np.linspace(0.30, 2.00, 171)  # 0.01 nm apart
    centres = [0.40, 0.75, 1.95]

    chosen = frames_for_centres(measured, centres)

    for (frame, value), centre in zip(chosen, centres):
        assert abs(value - centre) <= 0.005
        assert measured[frame] == pytest.approx(value)


def test_a_centre_beyond_the_pull_takes_the_closest_frame_there_is():
    """No extrapolation, and no silent failure.

    A window past where the pull reached gets the end of the pull. The
    caller reports how far off it is, and a placement that is far off is
    visible rather than invented.
    """
    measured = np.linspace(0.30, 1.00, 71)

    (frame, value), = frames_for_centres(measured, [2.0])

    assert frame == measured.size - 1
    assert value == pytest.approx(1.00)


def test_the_pull_is_checked_against_what_plumed_actually_biased(tmp_path):
    """A selection mismatch is caught before any window runs.

    Recomputing the variable here and reading PLUMED's own record are two
    measurements of one quantity. Where they disagree, the seeds would sit
    at distances nobody asked for -- so the study stops.
    """
    simulation = tmp_path / "simulation"
    simulation.mkdir()
    (simulation / "COLVAR").write_text(COLVAR, encoding="utf-8")

    ours = np.array([0.334, 0.40, 0.343])
    _check_against_colvar(tmp_path, ours)  # agrees: no refusal

    wrong = ours + 10 * COLVAR_AGREEMENT_NM
    with pytest.raises(ValueError, match="different quantities"):
        _check_against_colvar(tmp_path, wrong)


def test_a_held_window_is_not_refused_for_fluctuating(tmp_path):
    """The check must survive a series that goes nowhere.

    Comparing first and last values suits a pull, whose ends are a
    nanometre apart. A restrained window moves further than the tolerance
    between one frame and the next, so endpoint comparison refused correct
    measurements. The median does not care where the series happened to be
    when the last frame was written.
    """
    simulation = tmp_path / "simulation"
    simulation.mkdir()
    rng = np.random.default_rng(0)
    held = 0.817 + 0.05 * rng.standard_normal(4000)
    rows = "\n".join(f" {1500 + 0.2 * i:.6f} {v:.6f} 1.0"
                      for i, v in enumerate(held))
    (simulation / "COLVAR").write_text(
        "#! FIELDS time cv restraint.bias\n" + rows + "\n", encoding="utf-8")

    # The same distribution, sampled on a coarser stride, as a trajectory is.
    _check_against_colvar(tmp_path, held[::50])


def test_no_colvar_means_no_cross_check_rather_than_a_failure(tmp_path):
    _check_against_colvar(tmp_path, np.array([0.3, 0.4]))


def test_a_seed_that_could_not_run_is_refused():
    particles = 30_000
    reference = -400_000.0

    # Strained, as a pulled frame is, and well within tolerance.
    _refuse_an_impossible_seed(reference + 1_000.0, reference, particles,
                               3, 0.817)

    doomed = reference + 2 * ENERGY_TOLERANCE_KJMOL_PER_ATOM * particles
    with pytest.raises(ValueError, match="above the prepared system"):
        _refuse_an_impossible_seed(doomed, reference, particles, 3, 0.817)


def test_an_infinite_seed_is_refused_even_without_a_reference():
    with pytest.raises(ValueError, match="potential energy"):
        _refuse_an_impossible_seed(float("inf"), None, 30_000, 0, 0.4)


def test_the_windows_do_not_inherit_the_pull():
    """The block that says "pull once" must not become "pull in every window".

    Every non-umbrella simulation key is copied into each window so the
    windows keep the study's step counts and timestep. `steered` is the one
    that must not be, and copying it would have twenty-four restrained
    windows each dragging the ligand to 2 nm.
    """
    config = {
        "systems": [{"id": "complex", "system": "x.pdb"}],
        "simulation": {
            "duration_ns": 10,
            "timestep_fs": 2.0,
            "steered": {"collective_variable": "ligand_distance",
                        "from": 0.334, "to": 2.0},
            "umbrella": {"collective_variable": "ligand_distance",
                         "ligand_resname": "BEN",
                         "site_selection": "resid 189 to 195 and name CA",
                         "force_constant": 3000.0,
                         "from": 0.4, "to": 2.0, "n_windows": 4},
        },
    }

    expanded = expand_umbrella(config)

    assert len(expanded["systems"]) == 4
    for entry in expanded["systems"]:
        simulation = entry["simulation"]
        assert "steered" not in simulation
        assert simulation["duration_ns"] == 10
        assert simulation["umbrella"]["force_constant"] == 3000.0
    # The study keeps it: that is where the seeding step reads it from.
    assert "steered" in expanded["simulation"]


def test_the_plan_still_rebuilds_from_a_seeded_study():
    """Seeding must not cost the study its recombination.

    The free energy is rebuilt from each window's own block, so anything
    that changes those blocks has to leave the plan readable.
    """
    from fastmdxplora.simulation.umbrella import plan_from_expanded

    expanded = expand_umbrella({
        "systems": [{"id": "complex", "system": "x.pdb"}],
        "simulation": {
            "steered": {"to": 2.0, "from": 0.334},
            "umbrella": {"collective_variable": "ligand_distance",
                         "force_constant": 3000.0,
                         "from": 0.4, "to": 2.0, "n_windows": 5},
        },
    })

    plan = plan_from_expanded(expanded)

    assert plan is not None
    assert len(plan.windows) == 5
    assert [w.index for w in plan.windows] == [0, 1, 2, 3, 4]
    assert plan.windows[0].force_constant == 3000.0


def _explorer_with(windows, raw, monkeypatch, seeds=None):
    """A BatchExplorer with only what these two paths read.

    Built without `__init__` on purpose: constructing a real study needs a
    structure, a force field and a directory, and none of that is what is
    being tested here. What is being tested is which windows end up pointing
    at which starting system.
    """
    from types import SimpleNamespace

    from fastmdxplora.batch.explorer import BatchExplorer

    explorer = BatchExplorer.__new__(BatchExplorer)
    explorer._is_umbrella = True
    explorer._raw = raw
    explorer.run_specs = [
        SimpleNamespace(options={"simulation": {"umbrella": {"index": i}}})
        for i in range(windows)
    ]
    monkeypatch.setattr(explorer, "_maybe_seed_the_windows",
                        lambda prepared: dict(seeds or {}))
    return explorer


def test_reusing_a_prepared_system_still_seeds_the_windows(monkeypatch):
    """Excluding setup must not turn seeding off.

    Reusing a prepared system is how a study avoids preparing twice, and it
    is the path a seeded rerun takes. Seeding lived on the other path, so
    asking for both gave every window the same starting point and the
    original failure back, two days later.
    """
    explorer = _explorer_with(
        3,
        {"simulation": {"prepared_from": "runs/earlier/shared_setup/setup"}},
        monkeypatch,
        seeds={0: "seeds/window-00", 1: "seeds/window-01",
               2: "seeds/window-02"},
    )

    assert explorer._maybe_prepare_once(None, ["setup"]) is None

    starts = [s.options["simulation"]["prepared_from"]
              for s in explorer.run_specs]
    assert starts == ["seeds/window-00", "seeds/window-01", "seeds/window-02"]


def test_a_window_without_a_seed_falls_back_to_the_shared_system(monkeypatch):
    """A partial seeding is not a silent one.

    Where no seed exists for a window it starts from the shared prepared
    system, which is the behaviour before any of this existed.
    """
    explorer = _explorer_with(
        3,
        {"simulation": {"prepared_from": "runs/earlier/shared_setup/setup"}},
        monkeypatch,
        seeds={1: "seeds/window-01"},
    )

    explorer._maybe_prepare_once(None, ["setup"])

    starts = [s.options["simulation"]["prepared_from"]
              for s in explorer.run_specs]
    assert starts == ["runs/earlier/shared_setup/setup",
                      "seeds/window-01",
                      "runs/earlier/shared_setup/setup"]


def test_nothing_prepared_and_nothing_supplied_leaves_the_windows_alone(
        monkeypatch):
    explorer = _explorer_with(2, {"simulation": {}}, monkeypatch)

    assert explorer._maybe_prepare_once(None, ["setup"]) is None
    for spec in explorer.run_specs:
        assert "prepared_from" not in spec.options["simulation"]


def test_the_pull_block_never_reaches_a_window(monkeypatch):
    """Windows must not inherit `steered` even by the reuse path."""
    explorer = _explorer_with(
        2,
        {"simulation": {"prepared_from": "runs/earlier/shared_setup/setup"}},
        monkeypatch,
        seeds={0: "seeds/window-00", 1: "seeds/window-01"},
    )
    for spec in explorer.run_specs:
        spec.options["simulation"]["steered"] = {"to": 2.0, "from": 0.334}

    explorer._maybe_prepare_once(None, ["setup"])

    for spec in explorer.run_specs:
        assert "steered" not in spec.options["simulation"]
