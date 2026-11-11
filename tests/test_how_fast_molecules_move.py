"""The diffusion analysis: molecules followed across the periodic faces,
their mean squared displacement, and the coefficient its slope gives.

Held against random walks of a known coefficient, which is what a liquid's
molecules do at long times, stored as a simulation stores them: wrapped
into the cell, in a cell that may breathe, in frames that may follow
something else.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

WATER = np.array([[0.0, 0.0, 0.0], [0.0957, 0.0, 0.0], [-0.024, 0.0927, 0.0]])


def _waters(count: int):
    topology = md.Topology()
    chain = topology.add_chain()
    for _ in range(count):
        residue = topology.add_residue("HOH", chain)
        oxygen = topology.add_atom("O", md.element.oxygen, residue)
        for name in ("H1", "H2"):
            topology.add_bond(oxygen, topology.add_atom(name, md.element.hydrogen, residue))
    return topology


def _walkers(*, count: int = 300, frames: int = 400, coefficient: float = 2.3,
             apart_ns: float = 0.002, side: float = 3.0, breathing: bool = False,
             drift: float = 0.0, flat: bool = False, clock: bool = True, seed: int = 1):
    """Waters walking at random with the coefficient given (nm^2/ns), wrapped
    into a cubic cell; the frames carried ``drift`` nm further each frame."""
    rng = np.random.default_rng(seed)
    step = np.sqrt(2 * coefficient * apart_ns)
    moves = rng.normal(0, step, (frames - 1, count, 3))
    if flat:
        moves[:, :, 2] = 0.0
    walk = rng.uniform(0, side, (count, 3)) + np.concatenate(
        [np.zeros((1, count, 3)), np.cumsum(moves, axis=0)])
    # A simulation holds its centre of mass still.
    walk -= walk.mean(axis=1, keepdims=True) - walk[0].mean(axis=0)
    walk += drift * np.arange(frames)[:, None, None]
    sides = side * (1 + (0.01 * np.sin(np.arange(frames) / 7) if breathing
                         else np.zeros(frames)))
    xyz = np.empty((frames, count * 3, 3), dtype=np.float32)
    for frame in range(frames):
        centre = walk[frame] * (sides[frame] / side)
        centre -= np.floor(centre / sides[frame]) * sides[frame]
        xyz[frame] = (centre[:, None, :] + WATER[None]).reshape(-1, 3)
    return md.Trajectory(
        xyz, _waters(count),
        time=np.arange(frames) * apart_ns * 1000 if clock else np.full(frames, np.nan),
        unitcell_lengths=np.repeat(sides[:, None], 3, axis=1).astype(np.float32),
        unitcell_angles=np.full((frames, 3), 90, dtype=np.float32))


def _run(trajectory, tmp_path, **options):
    from fastmdxplora.analysis.diffusion import Diffusion

    analysis = Diffusion(output_dir=tmp_path, **options)
    return analysis, analysis.run(trajectory)


def test_the_transform_gives_what_the_pairs_taken_one_by_one_give():
    from fastmdxplora.analysis.diffusion import mean_squared_displacement

    path = np.cumsum(np.random.default_rng(3).normal(size=(60, 4, 3)), axis=0)
    one_by_one = np.array([
        [((path[lag:, m] - path[:60 - lag, m]) ** 2).sum(axis=1).mean() for m in range(4)]
        for lag in range(60)])
    assert np.allclose(mean_squared_displacement(path), one_by_one, atol=1e-9)


@pytest.mark.parametrize("how", [{}, {"breathing": True}, {"drift": 0.01}])
def test_a_walk_of_a_known_coefficient_is_read_back(how, tmp_path):
    analysis, result = _run(_walkers(**how), tmp_path)
    assert result.status == "ok", result.message
    found = analysis.findings["diffusion"]
    # The walkers' own centre is held still, which takes 1/300 of it away.
    assert found["coefficient_nm2_per_ns"] == pytest.approx(2.3, rel=0.12)
    assert abs(found["coefficient_nm2_per_ns"] - 2.3) < 4 * found["standard_error_nm2_per_ns"]
    assert found["exponent"] == pytest.approx(1.0, abs=0.08)
    assert found["dimensions"] == 3
    assert "not_yet_diffusive" not in analysis.findings
    table = result.data
    assert list(table.columns) == ["lag_ns", "msd_nm2", "sem_nm2"]
    assert table["msd_nm2"][0] == 0.0 and len(table) == 400
    assert result.figure_path.is_file()


def test_motion_in_a_plane_is_over_two_dimensions(tmp_path):
    analysis, result = _run(_walkers(flat=True), tmp_path, plane="xy")
    assert result.status == "ok", result.message
    found = analysis.findings["diffusion"]
    assert found["dimensions"] == 2
    assert found["coefficient_nm2_per_ns"] == pytest.approx(2.3, rel=0.12)


def test_molecules_held_in_place_are_said_not_to_diffuse(tmp_path):
    """Walkers that rattle about fixed places: the displacement stops
    growing, and the slope is not a coefficient."""
    trajectory = _walkers()
    rng = np.random.default_rng(4)
    held = trajectory.xyz[0][None] + np.repeat(
        rng.normal(0, 0.05, (trajectory.n_frames, 300, 1, 3)), 3, axis=2).reshape(
            trajectory.n_frames, 900, 3)
    trajectory.xyz = held.astype(np.float32)
    analysis, result = _run(trajectory, tmp_path)
    assert result.status == "ok", result.message
    assert analysis.findings["diffusion"]["exponent"] < 0.5
    assert "caged or confined" in analysis.findings["not_yet_diffusive"]


def test_frames_too_far_apart_to_follow_a_molecule_are_refused(tmp_path):
    _, result = _run(_walkers(apart_ns=0.1), tmp_path)
    assert result.status == "error"
    assert "over a fifth of the cell's narrowest width" in result.message
    assert "Save frames closer together" in result.message


def test_frames_with_no_clock_are_refused(tmp_path):
    _, result = _run(_walkers(clock=False), tmp_path)
    assert result.status == "error"
    assert "carry no clock" in result.message


def test_a_lone_solute_in_frames_that_hold_nothing_else_is_refused(tmp_path):
    _, result = _run(_walkers(count=3), tmp_path)
    assert result.status == "error"
    assert "little else to measure their motion against" in result.message


def test_too_few_molecules_have_no_error(tmp_path):
    """Twelve followed among three hundred: a coefficient, and no error."""
    analysis, result = _run(_walkers(), tmp_path, selection="resid 0 to 11")
    assert result.status == "ok", result.message
    found = analysis.findings["diffusion"]
    assert found["standard_error_nm2_per_ns"] is None
    assert "too few" in found["error"]
    assert analysis.findings["molecules"]["followed"] == 12


def test_no_more_molecules_are_followed_than_asked(tmp_path):
    analysis, result = _run(_walkers(), tmp_path, max_molecules=50)
    assert result.status == "ok", result.message
    assert analysis.findings["molecules"] == {
        "followed": 50, "in_the_selection": 300,
        "by": "each molecule's centre of mass over the selection's atoms in it",
        "residues": ["HOH"]}


@pytest.mark.parametrize("options, said", [
    ({"plane": "yz"}, "`plane` is one of"),
    ({"relative_to": "protein"}, "`relative_to` is one of"),
    ({"fit_from": 0.6, "fit_to": 0.5}, "the first below the second"),
    ({"max_molecules": 0}, "at least 1"),
])
def test_options_that_mean_nothing_are_refused(options, said, tmp_path):
    from fastmdxplora.analysis.diffusion import Diffusion
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError, match=said):
        Diffusion(output_dir=tmp_path, **options)


def test_it_runs_only_where_a_study_names_it():
    from fastmdxplora.analysis.diffusion import Diffusion
    from fastmdxplora.config.schema import ANALYSIS_NAMES

    assert Diffusion.requires_naming is True
    assert "diffusion" in ANALYSIS_NAMES
