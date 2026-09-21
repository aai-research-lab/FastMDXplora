"""Running the simulation phase with the runner stood in for.

What the phase does with a setting -- hands it to the runner, builds a
record from what the run left -- is only known by running the phase. A real
run needs a prepared system and minutes, so the runner is replaced and
everything the phase does around it runs as it would. The phase checks that
the prepared system's files exist and never reads them itself; the runner,
stood in for, is the only reader, so empty files are enough.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


class Reached(Exception):
    """Raised by a stand-in runner once it has recorded its arguments."""


def _placeholders(root: Path) -> None:
    setup = root / "setup"
    setup.mkdir(parents=True, exist_ok=True)
    for name in ("system.xml", "state.xml", "topology.pdb"):
        (setup / name).write_text("", encoding="utf-8")
    # The orchestrator makes the phase's folder before running it, and the
    # phase writes its record there, on a failure as well.
    (root / "simulation").mkdir(exist_ok=True)


def _phase(root: Path, **options: Any) -> list[str]:
    from fastmdxplora.simulation import pipeline

    return pipeline.run(orchestrator=SimpleNamespace(output_dir=root, _presenter=None),
                        output_dir=root / "simulation", production_steps=10, **options)


def what_the_runner_receives(root: Path, monkeypatch, **options: Any) -> dict:
    """The arguments the phase hands run_simulation, stopping there."""
    from fastmdxplora.simulation import runner

    _placeholders(root)
    received: dict = {}

    def stand_in(**kwargs):
        received.update(kwargs)
        raise Reached

    monkeypatch.setattr(runner, "run_simulation", stand_in)
    with pytest.raises(Reached):
        _phase(root, **options)
    return received


def run_the_phase(root: Path, monkeypatch, **options: Any) -> list[str]:
    """Run the phase to its end, the runner reporting a finished run.

    The result is the runner's own type with every file placed inside the
    phase's folder, so what the phase reads from it is what it would read
    from a real one."""
    from fastmdxplora.simulation import runner
    from fastmdxplora.simulation.runner import SimulationResult

    _placeholders(root)
    out = root / "simulation"

    def stand_in(**kwargs):
        return SimulationResult(
            trajectory=out / "production.dcd", topology=out / "topology.pdb",
            final_state=out / "final_state.xml", energy_csv=out / "energies.csv",
            log_file=out / "simulation.log", platform_used="CPU",
            n_production_frames=10, duration_ns_actual=0.00002)

    monkeypatch.setattr(runner, "run_simulation", stand_in)
    return _phase(root, **options)
