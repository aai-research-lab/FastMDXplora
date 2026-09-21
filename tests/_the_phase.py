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


def a_prepared_water_box(tmp_path) -> dict:
    """The smallest prepared system the runner will load: one water,
    solvated into a small box. Enough to reach the checks that follow
    loading, without preparing a protein."""
    openmm = pytest.importorskip("openmm")
    from openmm import unit
    from openmm.app import PME, ForceField, HBonds, Modeller, PDBFile

    seed = tmp_path / "seed.pdb"
    seed.write_text(
        "CRYST1   20.000   20.000   20.000  90.00  90.00  90.00 P 1\n"
        "ATOM      1  O   HOH A   1      10.000  10.000  10.000  1.00  0.00           O\n"
        "ATOM      2  H1  HOH A   1      10.800  10.500  10.000  1.00  0.00           H\n"
        "ATOM      3  H2  HOH A   1       9.200  10.500  10.000  1.00  0.00           H\n"
        "END\n")
    pdb = PDBFile(str(seed))
    forcefield = ForceField("amber14-all.xml", "amber14/tip3pfb.xml")
    modeller = Modeller(pdb.topology, pdb.positions)
    modeller.addSolvent(forcefield, padding=1.0 * unit.nanometer)
    system = forcefield.createSystem(modeller.topology, nonbondedMethod=PME,
                                     nonbondedCutoff=0.9 * unit.nanometer,
                                     constraints=HBonds)
    context = openmm.Context(system, openmm.VerletIntegrator(0.001 * unit.picoseconds))
    context.setPositions(modeller.positions)
    state = context.getState(getPositions=True, getVelocities=True)
    setup = tmp_path / "setup"
    setup.mkdir()
    (setup / "system.xml").write_text(openmm.XmlSerializer.serialize(system))
    (setup / "state.xml").write_text(openmm.XmlSerializer.serialize(state))
    with (setup / "topology.pdb").open("w") as handle:
        PDBFile.writeFile(modeller.topology, modeller.positions, handle)
    return {"system_xml": str(setup / "system.xml"), "state_xml": str(setup / "state.xml"),
            "topology_pdb": str(setup / "topology.pdb")}


def what_plumed_is_given(root: Path, monkeypatch, **bias: Any) -> tuple[dict, Path]:
    """Run the runner on a small system with `bias` -- a metadynamics block,
    a pull -- and record what is handed to PLUMED, stopping there, so neither
    PLUMED nor a real run is needed. Returns that and the run's folder."""
    from fastmdxplora.simulation import plumed
    from fastmdxplora.simulation.runner import run_simulation

    given: dict = {}

    def stand_in(omm, system, plumed_config, output_dir, **kwargs):
        given.update(config=dict(plumed_config), output_dir=Path(output_dir))
        raise Reached

    monkeypatch.setattr(plumed, "add_plumed_force", stand_in)
    out = root / "out"
    with pytest.raises(Reached):
        run_simulation(**a_prepared_water_box(root), output_dir=str(out),
                       production_steps=10, nvt_steps=0, npt_steps=0,
                       minimize=False, platform="CPU", **bias)
    return given, out


def what_preparation_receives(root: Path, monkeypatch, **options: Any) -> dict:
    """The arguments the setup phase hands prepare_system, stopping there.

    The structure is a small peptide handed over as already fixed, so the
    phase goes straight from reading it to preparing it."""
    from fastmdxplora.setup import pipeline, prepare
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    root.mkdir(parents=True, exist_ok=True)
    structure = root / "peptide.pdb"
    structure.write_text(TRI_ALANINE, encoding="utf-8")
    received: dict = {}

    def stand_in(*args, **kwargs):
        received.update(kwargs)
        raise Reached

    monkeypatch.setattr(prepare, "prepare_system", stand_in)
    orchestrator = SimpleNamespace(system=str(structure), _structure_provenance=None,
                                   _presenter=None)
    (root / "setup").mkdir(exist_ok=True)
    with pytest.raises(Reached):
        pipeline.run(orchestrator=orchestrator, output_dir=root / "setup",
                     fixed_pdb=str(structure), **options)
    return received
