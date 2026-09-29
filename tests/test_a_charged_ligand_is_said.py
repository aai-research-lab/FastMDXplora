"""A charged ligand's binding free energy says the box is not corrected for.

Under Ewald summation the ligand and the receptor interact with each other's
periodic images and with the background that neutralises each, and the part
that changes along the curve is the background's: ``2 pi r^2 / (3 V)`` per
unit charge product, to leading order. Benzamidinium is +1 and trypsin
positive, and nothing said so beside the number.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.simulation.reference_state import (  # noqa: E402
    COULOMB_KJ_NM,
    WATER_DIELECTRIC,
    box_artefact_kjmol,
)
from tests.test_a_binding_free_energy_says_where_its_reference_fails import (  # noqa: E402
    _check,
    _study,
)


def _ewald(r: np.ndarray, length: float, alpha: float = 2.0) -> float:
    """The periodic potential of a unit charge and its background, by Ewald."""
    from scipy.special import erfc

    volume = length ** 3
    real = np.array([[i, j, k] for i in range(-3, 4) for j in range(-3, 4)
                     for k in range(-3, 4)]) * length
    waves = np.array([[i, j, k] for i in range(-7, 8) for j in range(-7, 8)
                      for k in range(-7, 8) if (i, j, k) != (0, 0, 0)]) * 2 * math.pi / length
    k2 = (waves ** 2).sum(axis=1)
    distance = np.linalg.norm(r + real, axis=1)
    return float((erfc(alpha * distance) / distance).sum()
                 + 4 * math.pi / volume * (np.exp(-k2 / (4 * alpha ** 2)) / k2
                                           * np.cos(waves @ r)).sum()
                 - math.pi / (alpha ** 2 * volume))


class TestTheEstimate:

    def test_it_is_the_ewald_sums_own_change_along_the_curve(self) -> None:
        """Averaged over directions, the periodic potential less the bare
        Coulomb term changes between two radii as the leading term says."""
        length, near, far = 6.0, 0.9, 2.9
        index = np.arange(60) + 0.5
        polar = np.arccos(1 - 2 * index / 60)
        azimuth = math.pi * (1 + 5 ** 0.5) * index
        directions = np.column_stack((np.cos(azimuth) * np.sin(polar),
                                      np.sin(azimuth) * np.sin(polar), np.cos(polar)))
        summed = np.mean([_ewald(far * d, length) - _ewald(near * d, length)
                          for d in directions]) - (1 / far - 1 / near)
        estimate = box_artefact_kjmol(1.0, 1.0, length ** 3, near, far,
                                      dielectric=1.0) / COULOMB_KJ_NM
        assert summed == pytest.approx(estimate, rel=0.05)

    def test_a_larger_box_shrinks_it_with_the_volume(self) -> None:
        small = box_artefact_kjmol(1, 6, 190.0, 0.9, 2.9)
        assert box_artefact_kjmol(1, 6, 380.0, 0.9, 2.9) == pytest.approx(small / 2)
        # A +1 ligand and a +6 receptor in a 190 nm^3 box: about a kJ/mol.
        assert small == pytest.approx(138.935458 * 6 / WATER_DIELECTRIC * 2 * math.pi
                                      * (2.9 ** 2 - 0.9 ** 2) / (3 * 190.0))
        assert 0.8 < small < 1.0


def _prepared(window: Path, *, ligand_charge: float, ions: int = 5) -> None:
    """The whole system as setup writes it, beside the window: its topology,
    its charges and its box."""
    saved = md.load(str(window / "simulation" / "trajectory_topology.pdb"))
    full = saved.topology.copy()
    chain = full.add_chain()
    for _ in range(3):
        water = full.add_residue("HOH", chain)
        full.add_atom("O", md.element.oxygen, water)
        full.add_atom("H1", md.element.hydrogen, water)
        full.add_atom("H2", md.element.hydrogen, water)
    for _ in range(ions):
        full.add_atom("NA", md.element.sodium, full.add_residue("NA", chain))
    charges = []
    for atom in full.atoms:
        if atom.residue.name == "LIG":
            charges.append(ligand_charge / 4)
        elif atom.residue.name == "ALA":
            charges.append(6.0 / saved.topology.select("resname ALA").size)
        elif atom.residue.is_water:
            charges.append(-0.834 if atom.name == "O" else 0.417)
        else:
            charges.append(1.0)
    setup = window / "setup"
    setup.mkdir()
    xyz = np.zeros((1, full.n_atoms, 3))
    md.Trajectory(xyz, full).save_pdb(str(setup / "topology.pdb"))
    particles = "".join(f'<Particle q="{q}" sig="0.3" eps="0.5"/>' for q in charges)
    (setup / "system.xml").write_text(
        '<System><Forces><Force type="HarmonicBondForce"/>'
        f'<Force type="NonbondedForce"><Particles>{particles}</Particles></Force>'
        "</Forces></System>", encoding="utf-8")
    (setup / "state.xml").write_text(
        '<State><PeriodicBoxVectors><A x="6" y="0" z="0"/><B x="0" y="6" z="0"/>'
        '<C x="0" y="0" z="6"/></PeriodicBoxVectors></State>', encoding="utf-8")
    (setup / "setup_parameters.json").write_text("{}", encoding="utf-8")


class TestTheStudySaysIt:

    def test_a_charged_ligand_is_said_with_the_size(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 1.0, 3.0, 3.3], n_frames=5)
        _prepared(directories[0], ligand_charge=1.0)
        checked = _check(directories, centres, bulk_from=3.0)
        charge = checked["charge"]
        assert (charge["ligand_charge"], charge["receptor_charge"], charge["ions"]) == (1.0, 6.0, 5)
        assert charge["box_volume_nm3"] == pytest.approx(216.0)
        assert charge["box_artefact_kjmol"] == pytest.approx(
            box_artefact_kjmol(1.0, 6.0, 216.0, 0.4, 3.3))
        said = [w for w in checked["warnings"] if "net charge of +1" in w]
        assert said and "receptor +6" in said[0] and "5 ions" in said[0]

    def test_a_neutral_ligand_is_not_remarked_on(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 1.0, 3.0, 3.3], n_frames=5)
        _prepared(directories[0], ligand_charge=0.0)
        checked = _check(directories, centres, bulk_from=3.0)
        assert checked["charge"]["ligand_charge"] == 0.0
        assert "box_artefact_kjmol" not in checked["charge"]
        assert not [w for w in checked["warnings"] if "net charge" in w]

    def test_without_the_prepared_system_it_says_it_did_not_look(self, tmp_path) -> None:
        directories, centres = _study(tmp_path, [0.4, 3.0], n_frames=5)
        checked = _check(directories, centres, bulk_from=3.0)
        assert "could not be read" in checked["charge"]["not_checked"]
