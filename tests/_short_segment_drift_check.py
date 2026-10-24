"""Reproduce P1's fixed synthetic checks and write their counts as JSON.

Run from an installed checkout:
    python tests/_short_segment_drift_check.py --output /tmp/p1.json
To compare another checkout, put its src directory first on PYTHONPATH.
The seeds and cases are fixed here; the thresholds live in statistics.py.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import platform

import numpy as np
import scipy
from scipy.signal import lfilter

from fastmdxplora.refusals import refusal_of
from fastmdxplora.statistics import _drift_test, summarise_segments


def check() -> dict:
    rows = []
    for count in (3, 4):
        errors = np.linspace(0.2, 0.8, count)
        shapes = {
            "stationary": np.zeros(count),
            "linear": 2.0 * np.arange(count),
            "jump": np.r_[np.zeros(count - 1), 10.0],
            "unordered": np.array([0.0, 4.0, 0.0] if count == 3 else [0.0, 4.0, -4.0, 0.0]),
        }
        for index, (name, offsets) in enumerate(shapes.items()):
            seed = 740001 + count * 100 + index
            trials = 10000
            rng = np.random.default_rng(seed)
            means = rng.normal(size=(trials, count)) * errors + offsets
            refused = sum(_drift_test(row, 1.0 / errors**2)[2] is not None for row in means)
            rows.append(dict(case=name, segments=count, trials=trials, seed=seed,
                             errors=errors.tolist(), means=offsets.tolist(),
                             drift_refusals=refused, rate=refused / trials))

    trajectories = []
    for count in (3, 4):
        for name, delta in [("stationary", 0.0), ("linear", 1.0)]:
            reasons: Counter[str] = Counter()
            start = 860001 + 100 * count
            for trial in range(40):
                rng = np.random.default_rng(start + trial)
                frames, phi = 4000, 0.9
                noise = rng.normal(0.0, np.sqrt(1.0 - phi**2), frames * count)
                trace = lfilter([1.0], [1.0, -phi], noise, zi=[phi * rng.normal()])[0]
                trace += np.repeat(delta * np.arange(count), frames)
                pooled, why = summarise_segments(trace, [frames * i for i in range(1, count)])
                if pooled is not None:
                    key = "pooled_qualified" if pooled.qualification else "pooled_clean"
                else:
                    key = refusal_of(why).code
                reasons[key] += 1
            trajectories.append(dict(case=name, segments=count, trials=40,
                                     frames_per_segment=4000, phi=0.9,
                                     seeds=list(range(start, start + 40)),
                                     counts=dict(sorted(reasons.items()))))
    return dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
                known_errors=rows, contiguous_ar1=trajectories)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(check(), indent=2) + "\n", encoding="utf-8")
