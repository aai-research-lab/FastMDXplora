"""Which ensemble production runs in, asked as its own question.

`npt_steps` used to answer two: how long to equilibrate at constant
pressure, and whether there was a barostat at all. The runner gated the
barostat on `npt_steps > 0`, so the two could not be separated, and one
combination could not be expressed:

    equilibrate at constant pressure, then run production at constant
    volume

which is the right way to do an NVT production run. The density has to be
learned from a barostat before the box is fixed at it -- this package's
own explain text says so -- and with one setting doing both jobs, asking
for it was impossible. `npt_steps: N` gave NPT production. `npt_steps: 0`
gave NVT at whatever density solvation happened to produce, which the same
explain text calls "the one option nobody intends".

It also made segmentation wrong. A resumed segment wants no equilibration
and the barostat the study runs under; zeroing `npt_steps` to skip the
first silently removed the second, so a joined trajectory held NPT and NVT
stretches with nothing able to tell.

So `ensemble` is its own setting, and `npt_steps` means only how long to
equilibrate.

**Nothing already written changes meaning.** With `ensemble` absent the
answer is inferred exactly as the runner used to decide it, so a config
saying `npt_steps: 0` is still an NVT run and one saying nothing is still
NPT. What changes is that the answer is now recorded rather than implied,
and that it can be stated when the inference is not what somebody wants.
"""

from __future__ import annotations

from typing import Any

__all__ = ["NPT", "NVT", "ENSEMBLES", "resolve_ensemble", "recorded_ensemble",
           "describe_choice"]

NPT = "npt"
NVT = "nvt"
ENSEMBLES = (NPT, NVT)

#: The runner's default NPT stage, used when a config says nothing. Kept
#: here as well because the inference has to match what the runner would
#: have done, and a second copy that drifts would silently change the
#: ensemble of every study that leaves the stage lengths alone.
DEFAULT_NPT_STEPS = 500_000


def resolve_ensemble(simulation: dict[str, Any] | None) -> str:
    """Which ensemble production runs in.

    Stated wins. Absent, the old rule applies: a positive NPT stage means
    a barostat that production inherits, and a zero one means constant
    volume. That is what the runner did before this setting existed, so
    every config already written keeps its meaning.
    """
    block = simulation or {}
    stated = block.get("ensemble")
    if stated in ENSEMBLES:
        return str(stated)
    return NPT if _npt_stage_steps(block) > 0 else NVT


def recorded_ensemble(record: dict[str, Any] | None) -> str:
    """Which ensemble a run's production ran in, from its simulation record.

    The runner records its answer under ``resolved``. A record written
    before it did is answered by the resolver, over the parameters the run
    was handed and the NPT stage the runner recorded -- the two things the
    runner decided from -- so the answer is still the runner's. Asked of
    the parameters' ``npt_steps`` alone, a default study reads as NVT,
    because the default stage is left unset there.
    """
    record = record or {}
    resolved = record.get("resolved")
    resolved = resolved if isinstance(resolved, dict) else {}
    if resolved.get("ensemble") in ENSEMBLES:
        return str(resolved["ensemble"])
    asked = record.get("parameters")
    block = dict(asked) if isinstance(asked, dict) else {}
    if resolved.get("npt_steps") is not None:
        block["npt_steps"] = resolved["npt_steps"]
    return resolve_ensemble(block)


def _npt_stage_steps(simulation: dict[str, Any]) -> int:
    """How long the NPT equilibration stage runs, defaults included."""
    steps = simulation.get("npt_steps")
    if steps is not None:
        return int(steps)
    duration = simulation.get("npt_duration_ns")
    if duration is not None:
        timestep = float(simulation.get("timestep_fs") or 2.0)
        return int(float(duration) * 1e6 / timestep)
    return DEFAULT_NPT_STEPS


def describe_choice(simulation: dict[str, Any] | None) -> str:
    """One line for the record, saying whether it was stated or inferred.

    A reader of a resolved config should be able to tell a decision
    somebody made from one the software made on their behalf. Both are
    legitimate; only one of them is theirs.
    """
    block = simulation or {}
    ensemble = resolve_ensemble(block)
    if block.get("ensemble") in ENSEMBLES:
        return f"{ensemble.upper()} production, as the config asks"
    steps = _npt_stage_steps(block)
    if ensemble == NPT:
        return (f"NPT production, inferred from an NPT stage of {steps:,} "
                "steps. Set `ensemble` to say so outright, or to ask for "
                "NVT production after a constant-pressure equilibration.")
    return ("NVT production, inferred from an NPT stage of zero steps -- "
            "so the box keeps whatever density solvation gave it. Set "
            "`ensemble: nvt` with a positive `npt_steps` to fix the box at "
            "a density a barostat found instead.")
