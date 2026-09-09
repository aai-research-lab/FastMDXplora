"""Seeding umbrella windows from a steered pull.

The parts that need OpenMM are skipped where it is absent; everything that
decides *which* frame a window starts from is plain arithmetic and is tested
here, because that is the decision a wrong answer would hide in.
"""

from __future__ import annotations

from pathlib import Path

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
    with pytest.raises(ValueError, match="not the ones that were biased"):
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

    # `str()` of a Path, so the separator is the platform's. Asserting the
    # POSIX spelling passed everywhere except Windows, where the same code
    # is correct and the test was not.
    shared = str(Path("runs/earlier/shared_setup/setup"))
    starts = [s.options["simulation"]["prepared_from"]
              for s in explorer.run_specs]
    assert starts == [shared, "seeds/window-01", shared]


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


# ---------------------------------------------------------------------------
# The periodic boundary, again
# ---------------------------------------------------------------------------
def _cell(edge: float = 8.1432) -> np.ndarray:
    """One rhombic dodecahedron, the shape C1 was solvated in."""
    half = edge / 2.0
    return np.array([[[edge, 0.0, 0.0],
                      [0.0, edge, 0.0],
                      [half, half, edge / np.sqrt(2.0)]]])


def test_a_pair_across_the_boundary_is_measured_the_short_way():
    """The hazard the cross-tool benchmark already documents, in our code.

    Frames are stored wrapped. Two centres in different images differ by a
    vector across the box, and its length is not a distance between the
    molecules -- it is a distance between one of them and a copy.
    """
    from fastmdxplora.simulation.seeding import shortest_vector

    cell = _cell()
    # 0.35 nm apart, then one of them wrapped by a whole cell vector.
    true = np.array([[0.35, 0.0, 0.0]])
    wrapped = true + cell[:, 0, :]

    assert np.linalg.norm(wrapped) > 8.0            # what raw arithmetic sees
    reduced = shortest_vector(wrapped, cell)
    assert np.linalg.norm(reduced) == pytest.approx(0.35, abs=1e-6)


def test_the_skewed_direction_is_reduced_too():
    """A dodecahedron is skewed, and fractional rounding alone is not enough.

    Reducing each fractional coordinate to its nearest integer gives the
    right answer in a cube and not always in a sheared cell, so the
    neighbouring translations are tried as well.
    """
    from fastmdxplora.simulation.seeding import shortest_vector

    cell = _cell()
    displaced = np.array([[0.2, 0.1, 0.05]]) + cell[:, 2, :]

    reduced = shortest_vector(displaced, cell)

    assert np.linalg.norm(reduced) == pytest.approx(
        np.linalg.norm([0.2, 0.1, 0.05]), abs=1e-6)


def test_a_distance_wider_than_the_box_is_named_as_impossible(tmp_path):
    """The refusal should say what went wrong, not only that something did.

    A median that disagrees has two causes and only one of them is the
    selections. A value wider than the cell can hold has exactly one.
    """
    import mdtraj as md

    from fastmdxplora.simulation.seeding import _check_against_colvar

    simulation = tmp_path / "simulation"
    simulation.mkdir()
    (simulation / "COLVAR").write_text(COLVAR, encoding="utf-8")

    topology = md.Topology()
    chain = topology.add_chain()
    residue = topology.add_residue("ALA", chain)
    topology.add_atom("CA", md.element.carbon, residue)
    frame = md.Trajectory(np.zeros((1, 1, 3), dtype=np.float32), topology)
    frame.unitcell_vectors = _cell().astype(np.float32)

    with pytest.raises(ValueError, match="wider than this box allows"):
        _check_against_colvar(tmp_path, np.array([0.34, 8.218]), frame)


def test_the_reduction_agrees_with_an_exhaustive_search():
    """The one check that would have caught the greedy walk.

    An earlier version updated the running best inside the scan, so each
    translation was applied to whatever had won so far rather than to the
    fractionally reduced vector. Lattice points were skipped, and for 17 of
    3000 sampled points it returned a vector up to 2.42 nm too long -- with
    the winning translation, (0, 0, -1), inside the search the whole time.

    A distance that is quietly too long is a window seeded from the wrong
    frame, and nothing downstream can tell. Compared here against every
    translation within three cells, on three box shapes, because a reduction
    is either exact or it is a source of plausible numbers.
    """
    from fastmdxplora.simulation.seeding import shortest_vector

    edge = 8.1432
    root2, root6 = np.sqrt(2.0), np.sqrt(6.0)
    shapes = {
        "cube": np.eye(3) * edge,
        "dodecahedron": np.array([[edge, 0.0, 0.0],
                                  [0.0, edge, 0.0],
                                  [edge / 2, edge / 2, edge / root2]]),
        "truncated octahedron": np.array(
            [[edge, 0.0, 0.0],
             [edge / 3, edge * 2 * root2 / 3, 0.0],
             [-edge / 3, edge * root2 / 3, edge * root6 / 3]]),
    }

    for name, cell in shapes.items():
        lattice = np.array([i * cell[0] + j * cell[1] + k * cell[2]
                            for i in range(-3, 4) for j in range(-3, 4)
                            for k in range(-3, 4)])
        rng = np.random.default_rng(1)
        points = (rng.random((600, 3)) * 3 - 1.5) @ cell

        mine = np.linalg.norm(
            shortest_vector(points, np.broadcast_to(
                cell, (600, 3, 3)).copy()), axis=1)
        exhaustive = np.linalg.norm(
            points[:, None, :] + lattice[None, :, :], axis=2).min(axis=1)

        assert np.allclose(mine, exhaustive, atol=1e-9), name


def test_seeding_survives_the_key_being_renamed(monkeypatch):
    """`setup_from` must reach seeding exactly as `prepared_from` does.

    The reuse path is where a seeded rerun lives, and it read one spelling.
    Renaming the key in the schema without renaming it here would have made
    every window start in the same place again -- silently, and only
    visible two days later when the overlap gate refused.
    """
    from types import SimpleNamespace

    from fastmdxplora.batch.explorer import BatchExplorer

    for key in ("setup_from", "prepared_from"):
        explorer = BatchExplorer.__new__(BatchExplorer)
        explorer._is_umbrella = True
        explorer._raw = {"simulation": {key: "runs/earlier"}}
        explorer.run_specs = [
            SimpleNamespace(options={"simulation": {"umbrella": {"index": i}}})
            for i in range(2)
        ]
        monkeypatch.setattr(explorer, "_maybe_seed_the_windows",
                            lambda prepared: {0: "seeds/window-00",
                                              1: "seeds/window-01"})

        explorer._maybe_prepare_once(None, ["setup"])

        starts = [s.options["simulation"]["prepared_from"]
                  for s in explorer.run_specs]
        assert starts == ["seeds/window-00", "seeds/window-01"], key


def test_the_pull_is_not_run_as_an_umbrella_window(monkeypatch, tmp_path):
    """The pull is the study's own run, not a copy of window 0.

    `RunSpec.to_dict()` nests the phase blocks under "options", which is
    where `_execute_run` reads them. Writing `options["simulation"]` put a
    key at the top level that nothing reads and left window 0's block in
    place -- so the pull started as "umbrella window 0, held at 0.4 nm with
    k=3000", restrained at a point instead of dragging the ligand anywhere.
    """
    from types import SimpleNamespace

    import fastmdxplora.batch.explorer as explorer_module
    from fastmdxplora.batch.explorer import BatchExplorer

    seen = {}

    def _capture(spec_dict, run_out, include, exclude, *args, **kwargs):
        seen["spec"] = spec_dict
        seen["include"] = include
        return SimpleNamespace(status="ok", message="")

    monkeypatch.setattr(explorer_module, "_execute_run", _capture)
    monkeypatch.setattr(explorer_module, "_a_pull_is_there",
                        lambda directory: False)

    explorer = BatchExplorer.__new__(BatchExplorer)
    explorer.output_dir = tmp_path
    explorer.verbose = False
    explorer.force = False
    explorer._raw = {
        "simulation": {
            "duration_ns": 10,
            "steered": {"collective_variable": "ligand_distance",
                        "ligand_name": "BEN", "to": 2.0},
        },
    }

    def _window(index, centre):
        return {"umbrella": {"index": index, "centre": centre,
                             "force_constant": 3000.0,
                             "collective_variable": "ligand_distance",
                             "ligand_name": "BEN",
                             "select_atoms": "resSeq 189 to 195 and name CA"}}

    # The plan is rebuilt from the expanded systems, so there have to be
    # windows there for the study to be an umbrella study at all.
    explorer._raw["systems"] = [
        {"id": f"window-{i:02d}", "simulation": _window(i, 0.4 + 0.05 * i)}
        for i in range(3)
    ]
    window = _window(0, 0.4)
    explorer.run_specs = [
        SimpleNamespace(options={"simulation": dict(window)},
                        to_dict=lambda: {"run_id": "window-00",
                                         "system_id": "s", "system": "x.pdb",
                                         "sweep_values": {},
                                         "options": {"simulation": dict(window)}})
    ]

    # Stops after the pull "runs": there is no trajectory to seed from.
    try:
        explorer._maybe_seed_the_windows(tmp_path / "prepared")
    except Exception:  # noqa: BLE001 -- the pull is what is under test
        pass

    simulation = seen["spec"]["options"]["simulation"]
    assert "umbrella" not in simulation, "the pull inherited a window"
    assert simulation["steered"]["to"] == 2.0
    assert simulation["save_selection"] == "all"
    assert seen["include"] == ["simulation"]


def test_the_seeder_reads_the_spelling_the_documentation_leads_with(
        monkeypatch, tmp_path):
    """`select_atoms` has to reach the seeder, not only the PLUMED builder.

    `with_general_selection_names` translates the general `select_atoms`
    into whichever role name a variable uses, and it runs inside
    `plan_from_config` -- the PLUMED builder. The seeding hand-off read
    `site_selection` straight off the window block and coerced a miss to
    `""` with `or ""`.

    So a study written the way `docs/selections.md` leads -- `select_atoms`
    -- pulled correctly for two and a half hours, because the pull is built
    through PLUMED, and then handed MDTraj an empty expression. MDTraj
    answered "Expected '=~' operations (at char 0), (line:1, col:1)", which
    names a character position in a string nobody wrote. Thirty windows
    never started.

    Two readers of one block, which is what patch 0038 was about; the line
    directly above the bug already carried the lesson for the ligand name
    and not for the selection beside it.
    """
    from pathlib import Path
    from types import SimpleNamespace

    import fastmdxplora.batch.explorer as explorer_module
    from fastmdxplora.batch.explorer import BatchExplorer

    SITE = "resSeq 189 to 195 and name CA"
    seen = {}

    def _capture_seed(pull, prepared, centres, out, **kwargs):
        seen.update(kwargs)
        # The real one creates this; the caller writes `seeds.json` into it.
        Path(out).mkdir(parents=True, exist_ok=True)
        return []

    monkeypatch.setattr("fastmdxplora.simulation.seeding.seed_windows",
                        _capture_seed)
    monkeypatch.setattr(explorer_module, "_a_pull_is_there",
                        lambda directory: True)

    explorer = BatchExplorer.__new__(BatchExplorer)
    explorer.output_dir = tmp_path
    explorer.verbose = False
    explorer.force = False
    explorer._raw = {
        "simulation": {
            "duration_ns": 10,
            "steered": {"collective_variable": "ligand_distance",
                        "ligand_name": "BEN", "to": 2.0},
        },
    }

    def _window(index, centre):
        # `select_atoms`, and deliberately no `site_selection`: this is the
        # config the documentation tells a user to write.
        return {"umbrella": {"index": index, "centre": centre,
                             "force_constant": 3000.0,
                             "collective_variable": "ligand_distance",
                             "ligand_name": "BEN",
                             "select_atoms": SITE}}

    explorer._raw["systems"] = [
        {"id": f"window-{i:02d}", "simulation": _window(i, 0.4 + 0.05 * i)}
        for i in range(3)
    ]
    window = _window(0, 0.4)
    explorer.run_specs = [
        SimpleNamespace(options={"simulation": dict(window)},
                        to_dict=lambda: {"run_id": "window-00",
                                         "system_id": "s", "system": "x.pdb",
                                         "sweep_values": {},
                                         "options": {"simulation": dict(window)}})
    ]

    explorer._maybe_seed_the_windows(tmp_path / "prepared")

    assert seen.get("site_selection") == SITE, (
        "the seeder did not receive the selection the study declared; "
        f"it got {seen.get('site_selection')!r}"
    )
    assert seen.get("ligand_resname") == "BEN"


def test_an_empty_site_selection_is_refused_by_name(tmp_path):
    """The refusal says which key is missing, not which character.

    `topology.select("")` raises a pyparsing error pointing at column one.
    The check for a selection matching *no atoms* sits below it and never
    runs, because a string that is not an expression fails earlier than one
    that matches nothing.
    """
    import numpy as np
    import pytest

    md = pytest.importorskip("mdtraj")
    from fastmdxplora.simulation.seeding import measure_along

    top = md.Topology()
    chain = top.add_chain()
    residue = top.add_residue("LIG", chain)
    top.add_atom("C", md.element.carbon, residue)
    trajectory = md.Trajectory(np.zeros((1, 1, 3), dtype=np.float32), top)

    with pytest.raises(ValueError, match="select_atoms"):
        measure_along(trajectory, "LIG", "")
