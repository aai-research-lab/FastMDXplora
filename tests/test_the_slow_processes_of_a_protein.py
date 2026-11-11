"""The Markov state model, held against a hidden Markov chain of known
transition matrix, whose implied timescales are known exactly."""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

#: Three states, reversible: 0 and 1 swap often, 2 is reached rarely.
TRANSITIONS = np.array([[0.90, 0.098, 0.002],
                        [0.098, 0.90, 0.002],
                        [0.004, 0.004, 0.992]])


def _exact_timescales(transitions: np.ndarray) -> np.ndarray:
    values = np.sort(np.real(np.linalg.eigvals(transitions)))[::-1][1:]
    return -1.0 / np.log(values)


def _chain(frames: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    state, path = 0, np.empty(frames, dtype=int)
    cumulative = TRANSITIONS.cumsum(axis=1)
    for k in range(frames):
        path[k] = state
        state = int(np.searchsorted(cumulative[state], rng.random()))
    return path


def _frames(path: np.ndarray, seed: int = 1):
    """Eight alpha carbons whose arrangement is set by the hidden state, with
    noise: each state stretches a different pair of them."""
    topology = md.Topology()
    chain = topology.add_chain()
    for k in range(8):
        topology.add_atom("CA", md.element.carbon,
                          topology.add_residue("ALA", chain, resSeq=k + 1))
    base = np.stack([np.arange(8) * 0.38, np.zeros(8), np.zeros(8)], axis=1)
    shapes = np.zeros((3, 8, 3))
    shapes[1, 6, 1] = 0.6
    shapes[2, 1, 2] = 0.6
    rng = np.random.default_rng(seed)
    xyz = base[None] + shapes[path] + rng.normal(0, 0.03, (len(path), 8, 3))
    return md.Trajectory(xyz.astype(np.float32), topology,
                         time=np.arange(len(path)) * 10.0)


def test_the_reversible_estimate_obeys_detailed_balance():
    from fastmdxplora.analysis.markov_model import (count_matrix,
                                                    reversible_transitions,
                                                    stationary)

    counts = count_matrix(_chain(50000), 3, 1)
    transitions = reversible_transitions(counts)
    pi = stationary(transitions)
    flux = pi[:, None] * transitions
    assert np.allclose(flux, flux.T, atol=1e-10)
    assert np.allclose(transitions, TRANSITIONS, atol=0.01)


def test_a_state_never_left_is_outside_the_connected_set():
    from fastmdxplora.analysis.markov_model import count_matrix, largest_connected

    labels = np.array([0, 1, 0, 1, 0, 1, 2, 2, 2])
    assert largest_connected(count_matrix(labels, 3, 1)).tolist() == [0, 1]


def test_the_hidden_chain_s_timescales_are_recovered(tmp_path):
    from fastmdxplora.analysis.markov_model import MarkovModel

    exact = _exact_timescales(TRANSITIONS)
    analysis = MarkovModel(output_dir=tmp_path, states=12, timescales=2)
    result = analysis.run(_frames(_chain(30000)))
    assert result.status == "ok", result.message
    slow = analysis.findings["slow_processes"]
    # In frames (10 ps apart): about 166 and 9.6 for this matrix.
    assert slow[0]["timescale"]["frames"] == pytest.approx(exact[0], rel=0.15)
    assert slow[0]["timescale"]["ns"] == pytest.approx(exact[0] * 0.01, rel=0.15)
    assert slow[1]["timescale"]["frames"] == pytest.approx(exact[1], rel=0.25)
    assert slow[0]["crossings_in_the_run"] > 50
    assert "not_markovian" not in analysis.findings
    table = result.data
    assert {"lag_frames", "lag_ns", "timescale_1_ns", "timescale_2_ns"} <= set(table.columns)
    with np.load(tmp_path / "markov_model" / "markov_model_states.npz") as held:
        assert held["labels"].shape == (30000,)
        assert held["populations"].sum() == pytest.approx(1.0)


def test_tica_finds_the_slowest_direction():
    """Two coordinates: one hopping slowly between two values, one fast
    noise of larger spread. tICA's first coordinate is the slow one."""
    from fastmdxplora.analysis.markov_model import tica

    rng = np.random.default_rng(2)
    slow = np.repeat(rng.integers(0, 2, 200), 50).astype(float)
    fast = rng.normal(0, 3.0, len(slow))
    projected, autocorrelation = tica(np.stack([fast, slow], axis=1), 5, 1)
    assert abs(np.corrcoef(projected[:, 0], slow)[0, 1]) > 0.95
    assert autocorrelation[0] > 0.8


def test_a_process_crossed_twice_is_said_to_be_unresolved(tmp_path):
    """There and back once: the model holds both sides, and cannot measure
    how long the process takes."""
    from fastmdxplora.analysis.markov_model import MarkovModel

    path = np.repeat([0, 2, 0], 1000)
    analysis = MarkovModel(output_dir=tmp_path, states=6, timescales=1)
    result = analysis.run(_frames(path))
    assert result.status == "ok", result.message
    (slowest,) = analysis.findings["slow_processes"]
    assert "lower bound" in slowest["unresolved"]
    assert slowest["crossings_in_the_run"] <= 3


@pytest.mark.parametrize("options, said", [
    ({"features": "contacts"}, "`features` is one of"),
    ({"states": 1}, "`states` is at least 2"),
])
def test_options_that_mean_nothing_are_refused(options, said, tmp_path):
    from fastmdxplora.analysis.markov_model import MarkovModel
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError, match=said):
        MarkovModel(output_dir=tmp_path, **options)


def test_too_few_frames_for_the_states_are_refused(tmp_path):
    from fastmdxplora.analysis.markov_model import MarkovModel

    result = MarkovModel(output_dir=tmp_path, states=100).run(_frames(_chain(500)))
    assert result.status == "error" and "ten frames a state" in result.message
