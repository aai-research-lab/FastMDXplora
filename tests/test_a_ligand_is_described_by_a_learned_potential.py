"""A machine-learned potential for part of the system: the energy is the
model's, and what the model was not trained on is refused."""

from __future__ import annotations

import numpy as np
import pytest

openmm = pytest.importorskip("openmm")
pytest.importorskip("openmmml")
torchani = pytest.importorskip("torchani")
from openmm import unit  # noqa: E402


@pytest.fixture(scope="module")
def dipeptide():
    from openmmtools import testsystems

    return testsystems.AlanineDipeptideVacuum()


def test_given_every_atom_the_energy_is_ani_2x_s(dipeptide):
    import torch

    from fastmdxplora.setup.ml_potential import with_ml_potential

    mixed, record = with_ml_potential(dipeptide.system, dipeptide.topology,
                                      {"model": "ani2x", "atoms": "all"})
    assert record["count"] == 22 and record["elements"] == ["C", "H", "N", "O"]
    context = openmm.Context(mixed, openmm.VerletIntegrator(0.001),
                             openmm.Platform.getPlatformByName("CPU"))
    context.setPositions(dipeptide.positions)
    found = context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(
        unit.kilojoule_per_mole)
    model = torchani.models.ANI2x(periodic_table_index=True).double()
    numbers = torch.tensor([[a.element.atomic_number for a in dipeptide.topology.atoms()]])
    xyz = torch.tensor(np.array(dipeptide.positions.value_in_unit(unit.angstrom))[None],
                       dtype=torch.float64)
    expected = float(model((numbers, xyz)).energies) * 2625.4996394799
    assert found == pytest.approx(expected, abs=0.01)


def test_part_of_a_molecule_keeps_the_force_field_for_the_rest(dipeptide):
    from fastmdxplora.setup.ml_potential import with_ml_potential

    mixed, record = with_ml_potential(dipeptide.system, dipeptide.topology,
                                      {"atoms": "resname ALA"})
    assert record["model"] == "ani2x" and record["count"] == 10
    assert len(mixed.getForces()) == len(dipeptide.system.getForces()) + 1


@pytest.mark.parametrize("block, said", [
    ({"atoms": "resname NOPE"}, "matched no atoms"),
    ({"model": "ani1ccx", "atoms": "all"}, None),
    ({"atoms": "all", "cutoff": 1.0}, "has no setting cutoff"),
])
def test_what_cannot_be_given_to_the_model_is_refused(dipeptide, block, said):
    from fastmdxplora.config.loader import ConfigError
    from fastmdxplora.refusals import StudyError
    from fastmdxplora.setup.ml_potential import with_ml_potential

    if said is None:
        with_ml_potential(dipeptide.system, dipeptide.topology, block)
        return
    with pytest.raises((StudyError, ConfigError), match=said):
        with_ml_potential(dipeptide.system, dipeptide.topology, block)


def test_a_charged_group_is_refused(dipeptide):
    import copy

    from fastmdxplora.refusals import StudyError
    from fastmdxplora.setup.ml_potential import with_ml_potential

    charged = copy.deepcopy(dipeptide.system)
    force = next(f for f in charged.getForces() if isinstance(f, openmm.NonbondedForce))
    q, sigma, epsilon = force.getParticleParameters(0)
    force.setParticleParameters(0, q + 1.0 * unit.elementary_charge, sigma, epsilon)
    with pytest.raises(StudyError, match="neutral molecules only"):
        with_ml_potential(charged, dipeptide.topology, {"atoms": "all"})


def test_an_element_the_model_was_not_trained_on_is_refused(dipeptide, monkeypatch):
    import fastmdxplora.setup.ml_potential as module
    from fastmdxplora.refusals import StudyError

    monkeypatch.setitem(module.ELEMENTS, "ani2x", {"H", "C", "N"})
    with pytest.raises(StudyError, match="the atoms given hold O"):
        module.with_ml_potential(dipeptide.system, dipeptide.topology, {"atoms": "all"})


def test_too_many_atoms_are_refused(dipeptide, monkeypatch):
    import fastmdxplora.setup.ml_potential as module
    from fastmdxplora.refusals import StudyError

    monkeypatch.setattr(module, "MOST_ATOMS", 10)
    with pytest.raises(StudyError, match="10 is the most"):
        module.with_ml_potential(dipeptide.system, dipeptide.topology, {"atoms": "all"})
