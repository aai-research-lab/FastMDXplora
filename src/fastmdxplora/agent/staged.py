"""Running a study the agent wrote, with the estimate in the middle.

`--assisted` hands you a config and stops, because you are there to read
it. `--autonomous` does not, so something else has to be the thing that
stops it, and that thing is a budget.

A budget needs a number, and the number is not available when the agent
finishes writing. Cost scales with the *solvated* particle count, and that
depends on box shape, padding and ion concentration -- decisions setup
makes. A protein of 2,000 atoms is 60,000 solvated, and guessing from the
residue count would be inventing the water, which is most of the atoms.

So an autonomous run goes in two parts:

    setup                 -- cheap, minutes, and it settles the count
    estimate              -- from that count, on this machine
    simulation onwards    -- the expensive part, if the estimate fits

The gate sits where the information first exists and before the cost is
incurred. Anywhere earlier and it would be guessing; anywhere later and
there would be nothing left to stop.

Setup is not free, but it is the wrong order of magnitude to worry about:
minutes against the days the simulation takes, and it produces something
worth having even when the study is then refused -- a solvated system, a
particle count, and a reason.

What this does not do is approve on your behalf. An estimate that does not
fit the budget refuses, and the refusal names the number, because the
answer to "too expensive" is usually a shorter run rather than a bigger
allowance and a caller cannot make that choice without the figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import Refusal, StudyError, refusal_of

__all__ = ["StagedRun", "particles_after_setup", "run_in_stages"]


@dataclass
class StagedRun:
    """What happened, stage by stage.

    Kept whole rather than reduced to a verdict, because a run that stopped
    after setup is a different thing from one that never started, and a
    caller reading a report tomorrow needs to know which.
    """

    output_dir: Path
    setup_done: bool = False
    particles: int | None = None
    estimate_seconds: float | None = None
    simulated: bool = False
    refusal: Refusal | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def stopped_at(self) -> str:
        if self.refusal is None:
            return "" if self.simulated else "nothing ran"
        return "estimate" if self.setup_done else "setup"

    def as_record(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "setup_done": self.setup_done,
            "particles": self.particles,
            "estimate_hours": (None if self.estimate_seconds is None
                               else self.estimate_seconds / 3600),
            "simulated": self.simulated,
            "stopped_at": self.stopped_at,
            "refusal": self.refusal.as_dict() if self.refusal else None,
        }

    def __str__(self) -> str:
        if self.refusal is not None:
            return f"stopped at {self.stopped_at}: {self.refusal.message}"
        if self.simulated:
            return f"ran, {self.particles:,} particles"
        return "nothing ran"


def particles_after_setup(output_dir: Path | str) -> int | None:
    """The solvated particle count, from what setup already recorded.

    Read from ``setup/setup_parameters.json``, which is where setup writes
    ``n_atoms_solvated`` and where the report layer has been reading it
    from all along. Reading rather than recomputing: opening the system a
    second time could give a different answer from the one the study is
    documented with, and then two numbers would describe one run.

    Not the manifest, which was the first guess and wrong -- the manifest
    records artifacts, status and timing per phase, and the counts live
    with the phase's own parameters. Worth saying, because the manifest is
    the obvious place to look and it is not there.
    """
    import json

    record = Path(output_dir) / "setup" / "setup_parameters.json"
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    count = data.get("n_atoms_solvated")
    return int(count) if count else None


def run_in_stages(
    config: dict[str, Any],
    output_dir: Path | str,
    *,
    budget_hours: float | None = None,
    platform_name: str = "",
    precision: str = "mixed",
    explore: Any = None,
) -> StagedRun:
    """Run setup, price the rest, and continue only if it fits.

    Parameters
    ----------
    budget_hours
        The ceiling. ``None`` runs without one, which is right for a
        caller who is watching and wrong for one who is not -- so
        `--autonomous` supplies it and refuses without it.
    explore
        Substituted in tests. Not so a caller can substitute the science:
        whatever this is, the config still goes through the validator.
    """
    from fastmdxplora.cost import estimate_study

    out = Path(output_dir)
    staged = StagedRun(output_dir=out)
    runner = explore or _explore

    setup_only = dict(config)
    setup_only["include"] = ["setup"]
    try:
        runner(config=setup_only, output_dir=str(out))
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        staged.refusal = refusal_of(exc)
        return staged
    staged.setup_done = True

    staged.particles = particles_after_setup(out)
    if staged.particles is None:
        staged.refusal = Refusal(
            code="setup.structure.undetermined",
            message=(
                "Setup finished without recording how many particles the "
                "solvated system holds, so there is nothing to price the "
                "rest of the study from. Running on regardless would spend "
                "an unknown amount, which is the one thing an unattended "
                "run must not do."),
        )
        return staged

    try:
        estimate = estimate_study(
            config, particles=staged.particles,
            platform_name=platform_name, precision=precision)
    except StudyError as exc:
        # An unmeasured machine, or one measured under other settings. Both
        # mean there is no ceiling, and no ceiling is the thing the budget
        # exists to provide.
        staged.refusal = refusal_of(exc)
        return staged
    staged.estimate_seconds = estimate.seconds
    staged.notes.append(str(estimate))

    if budget_hours is not None and estimate.hours > budget_hours:
        staged.refusal = Refusal(
            code="environment.budget.exhausted",
            message=(
                f"This study is estimated at {estimate.hours:.1f} GPU-hours "
                f"and the budget is {budget_hours:.1f}. Setup ran and its "
                "output is kept; the simulation did not start. A shorter "
                "run is usually the answer rather than a larger allowance, "
                "which is why the number is here."),
            details={"estimate_hours": estimate.hours,
                     "budget_hours": budget_hours,
                     "particles": staged.particles},
        )
        return staged

    rest = dict(config)
    rest["exclude"] = ["setup"]
    # `setup_from` is a simulation setting, not a top-level one. Put at the
    # top level the loader refused it and named the right place, which is
    # the guardrail working on the code that was written to use it.
    simulation = dict(rest.get("simulation") or {})
    simulation["setup_from"] = str(out / "setup")
    rest["simulation"] = simulation
    try:
        runner(config=rest, output_dir=str(out))
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        staged.refusal = refusal_of(exc)
        return staged
    staged.simulated = True
    return staged


def _explore(*, config: dict[str, Any], output_dir: str) -> Any:
    from fastmdxplora import FastMDXplora

    return FastMDXplora(config_data=config, output_dir=output_dir).explore()
