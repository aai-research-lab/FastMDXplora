"""The protein's slow processes, from a Markov state model of its frames.

The frames are described by features (the distances between alpha carbons,
or the sines and cosines of the backbone torsions), projected onto their
slowest linear combinations by time-lagged independent component analysis
(tICA; Pérez-Hernández et al., J. Chem. Phys. 139, 015102 (2013)), each
scaled by its autocorrelation (the kinetic map), and cut into microstates by
k-means in that space. Counting the transitions between
microstates ``lag`` frames apart gives a transition matrix, estimated by
maximum likelihood under detailed balance (Prinz et al., J. Chem. Phys.
134, 174105 (2011)), whose eigenvalues ``l_i`` give the timescales of the
slow processes, ``t_i = -lag / ln l_i``.

**A model is only as good as its lag.** At too short a lag, the jumps
between microstates are not memoryless and the timescales come out too
short; they rise with the lag and level off where the model becomes
Markovian. So the timescales are computed at every lag from one frame to a
fifth of the run (``lags``), and the model is built at the shortest lag at
which the slowest timescale has levelled off (changes by under 10% over the
next lags); where it never levels off the result says so, and the model's
timescales are a lower bound.

**A timescale needs the process seen many times.** A slow process crossed
once or twice in the run has a timescale the model cannot measure: it is
given, and so is how many times the run crossed between the two sides of
its eigenvector; a timescale crossed fewer than ten times, or longer than a
third of the run, is said to be unresolved.

**Only the frames' own connected states count.** A microstate entered and
never left, or left and never entered, has no estimate; the model is built
on the largest set of microstates that can all be reached from each other,
and how many frames that leaves out is said.

Not built here: coarse-graining the microstates into metastable sets
(PCCA+), Chapman-Kolmogorov tests, or Bayesian errors (deeptime and PyEMMA
do these).
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis, _frame_interval_ns
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError

#: The most alpha carbons whose every pair is a feature.
_MOST_PAIRED = 150
#: The fewest crossings of a process from which its timescale is measured.
FEWEST_CROSSINGS = 10


def tica(features: np.ndarray, lag: int, dimensions: int) -> tuple[np.ndarray, np.ndarray]:
    """The ``dimensions`` slowest tICA coordinates of ``features`` (frames,
    features) at ``lag`` frames, and their autocorrelations at that lag."""
    from scipy.linalg import eigh

    x = features - features.mean(axis=0)
    early, late = x[:-lag], x[lag:]
    c0 = (early.T @ early + late.T @ late) / (2 * len(early))
    ct = (early.T @ late + late.T @ early) / (2 * len(early))
    # Directions with no variance have no autocorrelation to speak of.
    c0 += np.eye(len(c0)) * 1e-10 * np.trace(c0)
    values, vectors = eigh(ct, c0)
    order = np.argsort(values)[::-1][:dimensions]
    return x @ vectors[:, order], values[order]


def count_matrix(labels: np.ndarray, states: int, lag: int) -> np.ndarray:
    """Transitions from each state to each, ``lag`` frames apart, sliding."""
    counts = np.zeros((states, states))
    np.add.at(counts, (labels[:-lag], labels[lag:]), 1.0)
    return counts


def largest_connected(counts: np.ndarray) -> np.ndarray:
    """The states of the largest set that can all reach each other."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    _, which = connected_components(csr_matrix(counts > 0), directed=True,
                                    connection="strong")
    biggest = np.bincount(which, weights=counts.sum(axis=1)).argmax()
    return np.flatnonzero(which == biggest)


def reversible_transitions(counts: np.ndarray, rounds: int = 100000,
                           tolerance: float = 1e-12) -> np.ndarray:
    """The maximum-likelihood transition matrix obeying detailed balance,
    by the fixed-point iteration of Prinz et al. (2011)."""
    symmetric = counts + counts.T
    x = symmetric / symmetric.sum()
    row = counts.sum(axis=1)
    for _ in range(rounds):
        xs = x.sum(axis=1)
        new = symmetric / (row[:, None] / xs[:, None] + row[None, :] / xs[None, :])
        new /= new.sum()
        if np.abs(new - x).max() < tolerance:
            x = new
            break
        x = new
    return x / x.sum(axis=1, keepdims=True)


def stationary(transitions: np.ndarray) -> np.ndarray:
    values, vectors = np.linalg.eig(transitions.T)
    pi = np.real(vectors[:, np.argmax(np.real(values))])
    return pi / pi.sum()


def timescales(transitions: np.ndarray, lag: int, count: int) -> np.ndarray:
    """The ``count`` slowest implied timescales, in frames."""
    values = np.sort(np.real(np.linalg.eigvals(transitions)))[::-1][1:count + 1]
    values = np.clip(values, 1e-12, 1 - 1e-12)
    out = np.full(count, np.nan)
    out[:len(values)] = -lag / np.log(values)
    return out


class MarkovModel(Analysis):
    """A Markov state model of the frames, and its slow timescales.

    Options
    -------
    features : str, default "ca_distances"
        ``ca_distances`` (every pair of alpha carbons, at most 150 of them
        taken evenly) or ``backbone_torsions`` (the sines and cosines of phi
        and psi).
    states : int, default 100
        Microstates k-means cuts the tICA space into.
    tica_dimensions : int, default 4
        tICA coordinates the microstates are cut in.
    timescales : int, default 5
        Slow processes reported.
    seed : int, default 0
        The random seed of k-means.

    Output
    ------
    ``markov_model.dat`` -- comma-separated, one row per lag: ``lag_frames``,
    ``lag_ns`` and ``timescale_1_ns`` ... (frames where the run has no
    clock). ``markov_model_states.npz`` -- ``labels`` (the microstate of
    each frame, -1 outside the connected set), ``transitions`` and
    ``populations`` of the model's states, and ``tica`` (frames, dimensions).
    """

    name = "markov_model"
    requires_continuous_trajectory = True
    description = "Markov state model"
    default_selection = "protein"
    #: A model of the slow processes is the study's question, and a short run
    #: does not hold them.
    requires_naming = True

    FEATURES = ("ca_distances", "backbone_torsions")

    def __init__(self, *, features: str = "ca_distances", states: int = 100,
                 tica_dimensions: int = 4, timescales: int = 5, seed: int = 0,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if features not in self.FEATURES:
            raise StudyError(
                f"`features` is one of {', '.join(self.FEATURES)}; it was {features!r}.",
                code="analysis.option.inapplicable")
        self.features = features
        self.states, self.tica_dimensions = int(states), int(tica_dimensions)
        self.timescale_count, self.seed = int(timescales), int(seed)
        for name, value, low in (("states", self.states, 2),
                                 ("tica_dimensions", self.tica_dimensions, 1),
                                 ("timescales", self.timescale_count, 1)):
            if value < low:
                raise StudyError(f"`{name}` is at least {low}; it was {value}.",
                                 code="analysis.option.out_of_range")
        self.options.update(features=features, states=self.states,
                            tica_dimensions=self.tica_dimensions,
                            timescales=self.timescale_count, seed=self.seed)
        self._model: dict[str, np.ndarray] | None = None
        self._unit = "frames"
        self._chosen_lag: float | None = None

    # ------------------------------------------------------------------
    def _features(self, traj: md.Trajectory) -> tuple[np.ndarray, str]:
        chosen = set(int(a) for a in self.select_atoms(traj))
        if self.features == "backbone_torsions":
            parts = []
            for compute in (md.compute_phi, md.compute_psi):
                indices, angles = compute(traj, periodic=False)
                keep = [k for k, four in enumerate(indices) if int(four[1]) in chosen]
                parts += [np.sin(angles[:, keep]), np.cos(angles[:, keep])]
            features = np.concatenate(parts, axis=1)
            said = f"the sines and cosines of {features.shape[1] // 2} backbone torsions"
        else:
            alpha = np.array([a for a in traj.topology.select("name CA") if int(a) in chosen])
            if len(alpha) > _MOST_PAIRED:
                alpha = alpha[np.linspace(0, len(alpha) - 1, _MOST_PAIRED).astype(int)]
            pairs = np.array([(a, b) for k, a in enumerate(alpha) for b in alpha[k + 2:]])
            if not len(pairs):
                features = np.empty((traj.n_frames, 0))
            else:
                features = md.compute_distances(traj, pairs, periodic=False)
            said = (f"the distances between {len(alpha)} alpha carbons, neighbours "
                    "along the sequence left out")
        if features.shape[1] < 1:
            raise StudyError(
                f"{self.selection!r} gives no {self.features.replace('_', ' ')} to "
                "describe the frames by.", code="analysis.selection.empty")
        return features.astype(np.float64), said

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        from scipy.cluster.vq import kmeans2

        frames = traj.n_frames
        if frames < 10 * self.states:
            raise StudyError(
                f"{frames} frames for {self.states} microstates: a transition "
                "matrix needs many visits to each, ten frames a state at the least. "
                "Set fewer `states`, or analyse a longer run.",
                code="analysis.sampling.too_few_frames")
        interval = _frame_interval_ns(traj)
        features, said = self._features(traj)
        longest = max(1, frames // 5)
        lags = np.unique(np.round(np.geomspace(1, longest, 12)).astype(int))
        dimensions = min(self.tica_dimensions, features.shape[1])
        projected, autocorrelation = tica(features, int(lags[0]), dimensions)
        # The kinetic map (Noé and Clementi, J. Chem. Theory Comput. 11, 5002
        # (2015)): each coordinate scaled by its autocorrelation, so distances
        # in it are kinetic and k-means is not cut along fast noise, which
        # tICA's unit variance would otherwise weigh as much as the slowest.
        projected = projected * np.clip(autocorrelation, 0.0, None)[None, :]
        rng_state = np.random.default_rng(self.seed)
        _, labels = kmeans2(projected, self.states, seed=rng_state, minit="++")

        rows, models = [], {}
        for lag in lags:
            counts = count_matrix(labels, self.states, int(lag))
            kept = largest_connected(counts)
            transitions = reversible_transitions(counts[np.ix_(kept, kept)])
            found = timescales(transitions, int(lag), self.timescale_count)
            models[int(lag)] = (kept, transitions)
            rows.append([int(lag)] + list(found))
        table = pd.DataFrame(rows, columns=["lag_frames"] + [
            f"timescale_{k + 1}_frames" for k in range(self.timescale_count)])

        slowest = table["timescale_1_frames"].to_numpy()
        chosen_lag, levelled = int(lags[-1]), False
        for k in range(len(lags) - 2):
            ahead = slowest[k + 1:k + 3]
            if np.all(np.isfinite(ahead)) and np.all(
                    np.abs(ahead - slowest[k]) <= 0.1 * slowest[k]):
                chosen_lag, levelled = int(lags[k]), True
                break
        kept, transitions = models[chosen_lag]
        populations = stationary(transitions)
        at_lag = table[table["lag_frames"] == chosen_lag].iloc[0]
        found = [float(at_lag[f"timescale_{k + 1}_frames"]) for k in range(self.timescale_count)]

        map_back = -np.ones(self.states, dtype=int)
        map_back[kept] = np.arange(len(kept))
        model_labels = map_back[labels]
        in_model = model_labels >= 0

        values, vectors = np.linalg.eig(transitions.T)
        order = np.argsort(np.real(values))[::-1]
        crossings = []
        for k in range(1, min(self.timescale_count, len(kept) - 1) + 1):
            # Each side's core: microstates at least half as far out along the
            # process as its furthest. States near the middle are passed
            # through, and are not a crossing.
            along = np.real(vectors[:, order[k]]) / np.maximum(populations, 1e-300)
            side = np.where(along >= 0.5 * along.max(), 1,
                            np.where(along <= 0.5 * along.min(), -1, 0))
            path = side[model_labels[in_model]]
            path = path[path != 0]
            crossings.append(int(np.count_nonzero(np.diff(path) != 0)))

        def in_time(n_frames: float) -> dict[str, float]:
            out = {"frames": float(n_frames)}
            if interval is not None:
                out["ns"] = float(n_frames * interval)
            return out

        self._unit = "ns" if interval is not None else "frames"
        self._chosen_lag = chosen_lag * (interval if interval is not None else 1.0)
        if interval is not None:
            table.insert(1, "lag_ns", table["lag_frames"] * interval)
            for k in range(self.timescale_count):
                table[f"timescale_{k + 1}_ns"] = table.pop(f"timescale_{k + 1}_frames") * interval

        self._model = {"labels": model_labels, "transitions": transitions,
                       "populations": populations, "tica": projected.astype(np.float32)}
        processes = []
        for k, timescale in enumerate(found):
            if not np.isfinite(timescale):
                continue
            entry = {"process": k + 1, "timescale": in_time(timescale),
                     "crossings_in_the_run": crossings[k] if k < len(crossings) else None}
            crossed = entry["crossings_in_the_run"]
            if timescale > frames / 3:
                entry["unresolved"] = ("longer than a third of the run: the run cannot "
                                       "measure it, and it is a lower bound")
            elif crossed is not None and crossed < FEWEST_CROSSINGS:
                entry["unresolved"] = (
                    f"the run crossed between its two sides {crossed} time(s), and a "
                    f"timescale from fewer than {FEWEST_CROSSINGS} crossings is not a "
                    "measurement but a lower bound")
            processes.append(entry)
        self.findings["model"] = {
            "features": said,
            "tica": {"dimensions": dimensions, "lag_frames": int(lags[0]),
                     "autocorrelations": [float(v) for v in autocorrelation]},
            "microstates": self.states,
            "in_the_connected_set": int(len(kept)),
            "frames_left_out": int((~in_model).sum()),
            "lag": in_time(chosen_lag),
            "lag_chosen_by": ("the shortest lag at which the slowest timescale changes "
                              "by under 10% over the next two lags" if levelled else
                              "nothing: the slowest timescale did not level off over the "
                              "lags tried, so the longest was taken and the timescales "
                              "are lower bounds"),
            "estimated_by": "maximum likelihood under detailed balance (Prinz et al., "
                            "J. Chem. Phys. 134, 174105 (2011))",
        }
        self.findings["slow_processes"] = processes
        if not levelled:
            self.findings["not_markovian"] = (
                "The slowest timescale was still changing at a fifth of the run: the "
                "model is not shown to be Markovian at any lag tried, and its "
                "timescales are lower bounds.")
        self.findings["not_built"] = (
            "Metastable sets (PCCA+), Chapman-Kolmogorov tests and Bayesian errors, "
            "which deeptime and PyEMMA give.")
        return table

    # ------------------------------------------------------------------
    def save_data(self, result: pd.DataFrame, path):
        written = super().save_data(result, path)
        if self._model is not None:
            np.savez_compressed(path.with_name("markov_model_states.npz"), **self._model)
        return written

    def default_xlabel(self) -> str | None:
        return f"Lag ({self._unit})"

    def default_ylabel(self) -> str | None:
        return f"Implied timescale ({self._unit})"

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        unit = "ns" if "lag_ns" in result.columns else "frames"
        lag = result[f"lag_{unit}"]
        for column in [c for c in result.columns if c.startswith("timescale_")]:
            ax.plot(lag, result[column], marker="o", markersize=3, linewidth=1.0,
                    label=f"process {column.split('_')[1]}")
        # Below the line a timescale is shorter than the lag, and the model
        # cannot resolve it.
        ax.fill_between(lag, lag, min(float(np.nanmin(lag)), float(np.nanmin(
            result[[c for c in result.columns if c.startswith('timescale_')]].to_numpy()))),
            color="0.85", linewidth=0, label="shorter than the lag")
        if self._chosen_lag is not None:
            ax.axvline(self._chosen_lag, color="0.3", linewidth=0.8, linestyle="--",
                       label="lag of the model")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.legend(loc="best", fontsize=7)


register_analysis(MarkovModel.name, MarkovModel)
