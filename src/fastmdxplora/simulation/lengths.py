"""How long each stage runs when a config does not say: a time, not a step count.

The defaults were step counts: 250,000 steps of NVT, 500,000 of NPT and
1,000,000 of production. At the default 2 fs timestep those are 500 ps, 1 ns
and 2 ns, which is what the docs, the methods text and the Agent all said;
at the 4 fs hydrogen mass repartitioning allows they were 1 ns, 2 ns and
4 ns, and a study said to equilibrate for 1.5 ns equilibrated for 3. They
are times now, and the steps follow from the timestep.

Every reader of a stage's length takes it from here: the runner, the cost
estimate, the ensemble a config implies and the continuation of a stopped
study each kept a copy, and the continuation's was already a time while the
runner's was a step count.
"""

from __future__ import annotations

DEFAULT_TIMESTEP_FS = 2.0
DEFAULT_NVT_NS = 0.5
DEFAULT_NPT_NS = 1.0
DEFAULT_PRODUCTION_NS = 2.0


def steps_in(ns: float, timestep_fs: float) -> int:
    """The steps that cover `ns` nanoseconds at `timestep_fs`, counted as the
    runner has always counted a stated duration."""
    steps_per_ns = int(round(1_000_000.0 / float(timestep_fs)))
    return int(round(float(ns) * steps_per_ns))
