"""A restraint is held at the force constant, the target and the atoms it names.

Three things were not. The release ladder was written into every restraint's
parameter as it stood, so a position restraint asked for at 200, a distance
at 500 and a torsion at 50 kJ/mol/rad^2 all ran at 1000, 500, 100, 0 of a
length's units, and with `restrain_production` came back at 1000 when
production began, after equilibration had let go. A distance, angle or
torsion given no target was held at zero. And an angle's or dihedral's
atoms were taken in index order, so "index 8 6 4 14" became 4, 6, 8, 14.
Also one "force constant" meant two springs: an angle was k x^2 and a
torsion k(1 - cos x), half as stiff.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.config.loader import ConfigError, validate_config  # noqa: E402
from fastmdxplora.simulation.restraints import (  # noqa: E402
    ReleaseSchedule,
    build_restraint_forces,
    parse_restraints,
    resolve_restraints,
)


# ---------------------------------------------------------------------------
# Four free atoms in OpenMM: the restraints alone, measured by OpenMM itself.
# ---------------------------------------------------------------------------

def _four_atoms():
    omm = pytest.importorskip("openmm")
    from openmm import app

    topology = app.Topology()
    chain = topology.addChain()
    residue = topology.addResidue("UNK", chain)
    for name in ("C1", "C2", "C3", "C4"):
        topology.addAtom(name, app.Element.getBySymbol("C"), residue)
    # Not in a plane and not in index order along the chain, so a sign or an
    # order mistake shows.
    positions = np.array([[0.00, 0.00, 0.00], [0.15, 0.02, -0.01],
                          [0.21, 0.16, 0.03], [0.36, 0.17, 0.12]])
    return omm, topology, positions


def _energy(omm, forces, positions):
    system = omm.System()
    for _ in range(len(positions)):
        system.addParticle(12.0)
    for force, _parameter in forces:
        system.addForce(force)
    context = omm.Context(system, omm.VerletIntegrator(0.001),
                          omm.Platform.getPlatformByName("Reference"))
    context.setPositions(positions)
    return context.getState(getEnergy=True).getPotentialEnergy()._value


class TestWithNoTargetARestraintIsHeldWhereItIs:

    @pytest.mark.parametrize("kind,selection", [
        ("distance", "index 0 3"), ("angle", "index 0 1 2"),
        ("torsion", "index 0 1 2 3"), ("torsion", "index 3 1 0 2"),
    ])
    def test_its_energy_is_zero_where_it_starts(self, kind, selection):
        omm, topology, positions = _four_atoms()
        restraints = parse_restraints([{"kind": kind, "selection": selection,
                                        "force_constant": 400.0}])
        assert _energy(omm, build_restraint_forces(
            omm, topology, positions, restraints), positions) == pytest.approx(
                0.0, abs=1e-9)
        moved = positions + np.random.default_rng(3).normal(0.0, 0.02, positions.shape)
        assert _energy(omm, build_restraint_forces(
            omm, topology, positions, restraints), moved) > 1e-3

    def test_the_target_is_recorded(self):
        omm, topology, positions = _four_atoms()
        [(restraint, atoms)] = resolve_restraints(
            topology, positions,
            parse_restraints([{"kind": "distance", "selection": "index 0 3",
                               "force_constant": 400.0}]))
        assert atoms == [0, 3]
        assert restraint.target == pytest.approx(
            float(np.linalg.norm(positions[3] - positions[0])))

    def test_a_stated_target_is_kept(self):
        omm, topology, positions = _four_atoms()
        [(restraint, _)] = resolve_restraints(
            topology, positions,
            parse_restraints([{"kind": "distance", "selection": "index 0 3",
                               "force_constant": 400.0, "target": 0.5}]))
        assert restraint.target == 0.5


class TestAnIndexListKeepsTheOrderWritten:

    def test_the_apex_is_the_middle_atom_written(self):
        omm, topology, positions = _four_atoms()
        [(restraint, atoms)] = resolve_restraints(
            topology, positions,
            parse_restraints([{"kind": "angle", "selection": "index 2 0 1",
                               "force_constant": 100.0}]))
        assert atoms == [2, 0, 1]
        a, b = positions[2] - positions[0], positions[1] - positions[0]
        at_atom_0 = math.acos(np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b))
        assert restraint.target == pytest.approx(at_atom_0)

    def test_a_dihedral_keeps_its_written_order(self):
        omm, topology, positions = _four_atoms()
        [(restraint, atoms)] = resolve_restraints(
            topology, positions,
            parse_restraints([{"kind": "torsion", "selection": "index 3 1 0 2",
                               "force_constant": 100.0}]))
        assert atoms == [3, 1, 0, 2]
        from fastmdxplora.simulation.restraints import _value_now

        assert restraint.target == pytest.approx(_value_now("torsion", positions[atoms]))
        assert restraint.target != pytest.approx(
            _value_now("torsion", positions[[0, 1, 2, 3]]))

    def test_another_selection_comes_in_index_order(self):
        omm, topology, positions = _four_atoms()
        [(_, atoms)] = resolve_restraints(
            topology, positions,
            parse_restraints([{"kind": "angle", "selection": "name C3 C1 C2",
                               "force_constant": 100.0}]))
        assert atoms == [0, 1, 2]


class TestInAPeriodicBoxTheNearestImageIsMeasured:
    """Setup writes each molecule wrapped into the box, so two atoms of
    different molecules can lie a box apart in the coordinates."""

    @staticmethod
    def _split():
        omm, topology, positions = _four_atoms()
        positions = positions.copy()
        positions[3] = [2.95, 0.02, 0.0]   # 0.10 nm from atom 0 across x
        box = np.diag([3.0, 3.0, 3.0])
        return omm, topology, positions, box

    def test_the_target_is_the_nearest_image(self):
        omm, topology, positions, box = self._split()
        [(restraint, _)] = resolve_restraints(
            topology, positions,
            parse_restraints([{"kind": "distance", "selection": "index 0 3",
                               "force_constant": 400.0}]), box=box)
        assert restraint.target == pytest.approx(math.hypot(0.05, 0.02))

    def test_the_force_measures_it_too(self):
        omm, topology, positions, box = self._split()
        restraints = parse_restraints([{"kind": "distance", "selection": "index 0 3",
                                        "force_constant": 400.0, "target": 0.2}])
        forces = build_restraint_forces(omm, topology, positions, restraints, box=box)
        system = omm.System()
        system.setDefaultPeriodicBoxVectors(*[omm.Vec3(*row) for row in box])
        for _ in range(4):
            system.addParticle(12.0)
        for force, _ in forces:
            system.addForce(force)
        context = omm.Context(system, omm.VerletIntegrator(0.001),
                              omm.Platform.getPlatformByName("Reference"))
        context.setPositions(positions)
        energy = context.getState(getEnergy=True).getPotentialEnergy()._value
        nearest = math.hypot(0.05, 0.02)
        assert energy == pytest.approx(400.0 * (nearest - 0.2) ** 2, rel=1e-6)


class TestATargetIsInTheCoordinate_sOwnUnits:

    @pytest.mark.parametrize("kind,selection,target", [
        ("angle", "index 0 1 2", 109.5), ("torsion", "index 0 1 2 3", 4.0),
        ("distance", "index 0 3", 0.0), ("angle", "index 0 1 2", -0.1)])
    def test_outside_it_is_refused(self, kind, selection, target):
        with pytest.raises(ValueError, match="is not a"):
            parse_restraints([{"kind": kind, "selection": selection,
                               "force_constant": 10.0, "target": target}])

    def test_the_edges_are_accepted(self):
        parse_restraints([{"kind": "angle", "selection": "s", "force_constant": 1.0,
                           "target": math.pi},
                          {"kind": "torsion", "selection": "s", "force_constant": 1.0,
                           "target": -math.pi}])


class TestABlockSaysOnlyWhatARestraintTakes:

    def test_a_misspelt_key_is_refused_with_the_word_meant(self):
        """Ignored, `taget: 0.35` held the restraint where it started."""
        with pytest.raises(ValueError, match="did you mean 'target'"):
            parse_restraints([{"kind": "distance", "selection": "index 0 1",
                               "force_constant": 10.0, "taget": 0.35}])

    def test_a_negative_force_constant_is_refused(self):
        with pytest.raises(ValueError, match="pushes the atoms away"):
            parse_restraints([{"kind": "distance", "selection": "index 0 1",
                               "force_constant": -5.0}])

    def test_a_position_restraint_takes_no_target(self):
        with pytest.raises(ValueError, match="has no single value"):
            parse_restraints([{"kind": "position", "selection": "protein",
                               "target": 0.3}])

    def test_an_entry_that_is_neither_is_named(self):
        with pytest.raises(ValueError, match="Restraint 2 is 5"):
            parse_restraints(["protein", 5])

    def test_a_rising_ladder_is_refused(self):
        with pytest.raises(ValueError, match="rises above its first step"):
            ReleaseSchedule(steps=(100.0, 1000.0, 0.0)).check()


class TestOneNumberIsOneSpring:
    """E = k x^2 for every kind: a departure of x costs k x^2."""

    @pytest.mark.parametrize("kind,selection", [
        ("angle", "index 0 1 2"), ("torsion", "index 0 1 2 3")])
    def test_a_small_departure_costs_k_x_squared(self, kind, selection):
        omm, topology, positions = _four_atoms()
        [(restraint, _)] = resolve_restraints(
            topology, positions,
            parse_restraints([{"kind": kind, "selection": selection,
                               "force_constant": 100.0}]))
        departure = 0.01
        offset = parse_restraints([{"kind": kind, "selection": selection,
                                    "force_constant": 100.0,
                                    "target": restraint.target + departure}])
        forces = build_restraint_forces(omm, topology, positions, offset)
        assert _energy(omm, forces, positions) == pytest.approx(
            100.0 * departure ** 2, rel=1e-3)


class TestTheLadderStepsEachRestraintDownFromItsOwnConstant:

    def test_the_shares_follow_the_ladder(self):
        ladder = ReleaseSchedule(steps=(1000.0, 500.0, 100.0, 0.0))
        shares = [ladder.fraction_at(f) for f in (0.0, 0.3, 0.6, 0.9, 1.0)]
        assert shares == [1.0, 0.5, 0.1, 0.0, 0.0]

    @pytest.mark.parametrize("steps,says", [
        ((), "empty"), ((0.0, 0.0), "starts at zero"),
        ((1000.0, -10.0), "negative")])
    def test_a_ladder_that_cannot_be_read_is_refused(self, steps, says):
        with pytest.raises(ValueError, match=says):
            ReleaseSchedule(steps=steps).check()

    def test_a_restraint_with_no_constant_says_so(self):
        short, = parse_restraints("protein")
        stated, = parse_restraints([{"kind": "position", "selection": "protein",
                                     "force_constant": 200.0}])
        assert not short.stated and stated.stated


class TestTheRunAppliesThem:
    """Through `run_simulation`, watching every value OpenMM is given."""

    @staticmethod
    def _parameters_set(tmp_path, monkeypatch, *, restrain, release=None,
                        keep=False):
        omm = pytest.importorskip("openmm")
        import openmm.unit as unit
        from openmm.app import HBonds, PME, ForceField, Modeller, PDBFile

        from tests.test_restraints import _ALANINE_PEPTIDE
        from fastmdxplora.simulation.runner import run_simulation

        (tmp_path / "start.pdb").write_text(_ALANINE_PEPTIDE, encoding="utf-8")
        pdb = PDBFile(str(tmp_path / "start.pdb"))
        field = ForceField("amber14-all.xml", "amber14/tip3p.xml")
        modeller = Modeller(pdb.topology, pdb.positions)
        modeller.addHydrogens(field)
        modeller.addSolvent(field, padding=0.6 * unit.nanometer)
        system = field.createSystem(modeller.topology, nonbondedMethod=PME,
                                    nonbondedCutoff=1.0 * unit.nanometer,
                                    constraints=HBonds)
        context = omm.Context(system, omm.VerletIntegrator(0.001),
                              omm.Platform.getPlatformByName("CPU"))
        context.setPositions(modeller.positions)
        # Minimised here, as `test_restraints.py` does: the clashes addSolvent
        # leaves survive a capped minimisation and take a water to NaN in NVT.
        omm.LocalEnergyMinimizer.minimize(context, 10.0, 0)
        (tmp_path / "system.xml").write_text(omm.XmlSerializer.serialize(system))
        (tmp_path / "state.xml").write_text(omm.XmlSerializer.serialize(
            context.getState(getPositions=True, getVelocities=True)))
        with open(tmp_path / "topology.pdb", "w", encoding="utf-8") as handle:
            PDBFile.writeFile(modeller.topology, modeller.positions, handle)

        seen: dict[str, list[float]] = {}
        original = omm.Context.setParameter

        def spy(self, name, value):
            if name.startswith("restraint_k_"):
                values = seen.setdefault(name, [])
                if not values or values[-1] != float(value):
                    values.append(float(value))
            return original(self, name, value)

        monkeypatch.setattr(omm.Context, "setParameter", spy)
        result = run_simulation(
            system_xml=tmp_path / "system.xml", state_xml=tmp_path / "state.xml",
            topology_pdb=tmp_path / "topology.pdb", output_dir=tmp_path / "out",
            nvt_steps=40, npt_steps=0, production_steps=20, ensemble="nvt",
            platform="CPU", minimize_max_iterations=50,
            trajectory_interval_steps=10, state_interval_steps=10,
            checkpoint_interval_steps=20, restrain=restrain,
            restraint_release=release, restrain_production=keep, random_seed=1)
        return seen, result

    _ASKED = [
        "protein and name CA",
        {"kind": "position", "selection": "protein and name CA",
         "force_constant": 200.0},
        {"kind": "distance", "selection": "index 4 14", "force_constant": 500.0},
        {"kind": "torsion", "selection": "index 4 6 8 14", "force_constant": 50.0},
    ]

    def test_each_is_stepped_down_from_its_own_constant(self, tmp_path, monkeypatch):
        seen, result = self._parameters_set(tmp_path, monkeypatch,
                                            restrain=self._ASKED)
        assert seen["restraint_k_0"] == [1000.0, 500.0, 100.0, 0.0]
        assert seen["restraint_k_1"] == [200.0, 100.0, 20.0, 0.0]
        assert seen["restraint_k_2"] == [500.0, 250.0, 50.0, 0.0]
        assert seen["restraint_k_3"] == [50.0, 25.0, 5.0, 0.0]
        held = result.resolved["restraints_resolved"]
        assert [r["force_constant"] for r in held] == [1000.0, 200.0, 500.0, 50.0]
        assert held[2]["target"] is not None and held[2]["target"] > 0.1

    def test_kept_for_production_they_are_never_let_go(self, tmp_path, monkeypatch):
        seen, _ = self._parameters_set(tmp_path, monkeypatch,
                                       restrain=self._ASKED, keep=True)
        assert seen["restraint_k_0"] == [1000.0]
        assert seen["restraint_k_1"] == [200.0]
        assert seen["restraint_k_3"] == [50.0]

    def test_production_is_free_whatever_the_last_step(self, tmp_path, monkeypatch):
        seen, _ = self._parameters_set(tmp_path, monkeypatch,
                                       restrain="protein and name CA",
                                       release=[800.0, 400.0])
        assert seen["restraint_k_0"] == [800.0, 400.0, 0.0]

    def test_a_dihedral_written_out_of_order_is_held_as_written(
            self, tmp_path, monkeypatch):
        _, result = self._parameters_set(tmp_path, monkeypatch, restrain=[
            {"kind": "torsion", "selection": "index 14 8 6 4",
             "force_constant": 50.0}])
        [held] = result.resolved["restraints_resolved"]
        assert held["selection"] == "index 14 8 6 4"
        assert held["atoms"] == [14, 8, 6, 4]
        assert -math.pi <= held["target"] <= math.pi


class TestTheLongFormIsAStudySetting:
    """`docs/studies.md` documents it; the schema refused it as not a string."""

    def test_the_documented_block_is_accepted(self):
        validate_config({"simulation": {
            "restrain": [
                {"kind": "position", "selection": "protein and not element H",
                 "force_constant": 1000.0},
                {"kind": "distance", "selection": "index 412 1055",
                 "force_constant": 500.0, "target": 0.35},
                {"kind": "torsion", "selection": "index 12 13 14 15",
                 "force_constant": 50.0},
            ],
            "restraint_release": [1000, 500, 100, 0]}})

    def test_a_bad_block_is_refused_with_the_config(self):
        with pytest.raises(ConfigError, match="Unknown restraint kind"):
            validate_config({"simulation": {"restrain": [
                {"kind": "gravity", "selection": "protein"}]}})
        with pytest.raises(ConfigError, match="force_constant"):
            validate_config({"simulation": {"restrain": [
                {"kind": "distance", "selection": "index 1 2"}]}})

    def test_a_ladder_beside_kept_restraints_is_refused(self):
        """Kept through production they are never stepped down: a ladder
        given with them would be ignored."""
        with pytest.raises(ConfigError, match="both given") as caught:
            validate_config({"simulation": {"restrain": "protein",
                                            "restraint_release": [1000, 100, 10],
                                            "restrain_production": True}})
        assert caught.value.refusal.code == "config.option.conflicting"
        validate_config({"simulation": {"restrain": "protein",
                                        "restrain_production": True}})

    def test_an_unreadable_ladder_is_named_as_the_ladder(self):
        with pytest.raises(ConfigError, match="restraint_release") as caught:
            validate_config({"simulation": {"restrain": "protein",
                                            "restraint_release": ["a"]}})
        assert caught.value.refusal.details["option"] == "restraint_release"

    def test_a_ladder_starting_at_zero_is_refused(self):
        with pytest.raises(ConfigError, match="starts at zero"):
            validate_config({"simulation": {"restrain": "protein",
                                            "restraint_release": [0, 0]}})

    def test_a_system_s_or_a_sweep_s_restraints_are_read_as_the_study_s(self):
        """A system entry and a sweep value are held to what the study's own
        block is, as each run will be made."""
        bad = [{"kind": "gravity", "selection": "protein"}]
        with pytest.raises(ConfigError, match="Unknown restraint kind"):
            validate_config({"systems": [{"system": "1L2Y",
                                          "simulation": {"restrain": bad}}]})
        with pytest.raises(ConfigError, match="for run '.*': Unknown restraint kind"):
            validate_config({"systems": [{"system": "1L2Y"}],
                             "sweep": {"simulation.restrain": ["protein", bad]}})
        with pytest.raises(ConfigError, match="both given") as caught:
            validate_config({"systems": [{"system": "1L2Y",
                                          "simulation": {"restrain_production": True}}],
                             "simulation": {"restrain": "protein",
                                            "restraint_release": [500, 0]}})
        assert caught.value.refusal.code == "config.option.conflicting"
        validate_config({"systems": [
            {"system": "1L2Y", "simulation": {"restrain": [
                {"kind": "distance", "selection": "index 1 2",
                 "force_constant": 500.0}]}},
            {"system": "1UBQ"}]})


class TestTheMethodsSayWhatWasHeld:

    def test_each_restraint_with_its_target_and_form(self):
        from pathlib import Path

        from fastmdxplora.report.methods import methods_paragraphs

        sim = {"parameters": {
            "timestep_fs": 2.0, "integrator": "langevin_middle",
            "production_steps": 1000,
            "restrain": [{"kind": "distance", "selection": "index 4 14",
                          "force_constant": 500.0}]},
            "resolved": {"restraint_release": [1000.0, 500.0, 100.0, 0.0],
                         "restraints_resolved": [
                             {"kind": "distance", "selection": "index 4 14",
                              "force_constant": 500.0, "units": "kJ/mol/nm^2",
                              "target": 0.3512}]}}
        text = methods_paragraphs(Path("."), {}, sim)
        assert "E = k x² (no factor of one half" in text
        assert "the distance between atoms `index 4 14` held at 0.351 nm, " \
               "k = 500 kJ/mol/nm²" in text
        assert "harmonically restrained" not in text
