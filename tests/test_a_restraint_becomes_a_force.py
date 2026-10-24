"""From a config block to the forces that hold a system, without OpenMM.

`build_restraint_forces` takes the OpenMM module as an argument, which makes
it testable with a recorder standing where OpenMM stands: every energy
expression, parameter and particle it would register can be read back and
checked. The expressions matter more than most strings -- `periodicdistance`
is the difference between a restraint and an atom yanked the width of the box
when it wraps, and the torsion's `1 - cos(theta - theta0)` form is what keeps
the penalty continuous across pi, where a plain square jumps by 4*pi^2 and
kicks the atoms apart. Neither had a test that would notice its loss.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.simulation.restraints import (  # noqa: E402
    DEFAULT_POSITION_FORCE,
    ReleaseSchedule,
    build_restraint_forces,
    parse_restraints,
)


# ---------------------------------------------------------------------------
# OpenMM, as a recorder.
# ---------------------------------------------------------------------------

class _Force:
    def __init__(self, energy: str):
        self.energy = energy
        self.globals: list[tuple[str, float]] = []
        self.per_term: list[str] = []
        self.terms: list[tuple] = []

    def addGlobalParameter(self, name, value):
        self.globals.append((name, value))

    def _per(self, name):
        self.per_term.append(name)

    addPerParticleParameter = _per
    addPerBondParameter = _per
    addPerAngleParameter = _per
    addPerTorsionParameter = _per

    def addParticle(self, index, params):
        self.terms.append((index, list(params)))

    def addBond(self, i, j, params):
        self.terms.append((i, j, list(params)))

    def addAngle(self, i, j, k, params):
        self.terms.append((i, j, k, list(params)))

    def addTorsion(self, i, j, k, l, params):  # noqa: E741
        self.terms.append((i, j, k, l, list(params)))


class _OpenMM:
    CustomExternalForce = _Force
    CustomBondForce = _Force
    CustomAngleForce = _Force
    CustomTorsionForce = _Force


@pytest.fixture
def peptide(monkeypatch):
    """Four backbone atoms of one residue, at known positions."""
    topology = md.Topology()
    chain = topology.add_chain()
    residue = topology.add_residue("ALA", chain)
    for name, element in (("N", md.element.nitrogen),
                          ("CA", md.element.carbon),
                          ("C", md.element.carbon),
                          ("O", md.element.oxygen)):
        topology.add_atom(name, element, residue)
    # The builder converts an OpenMM topology; here it is already mdtraj's.
    monkeypatch.setattr(md.Topology, "from_openmm",
                        staticmethod(lambda given: given))
    positions = np.array([[0.0, 0.0, 0.0], [0.15, 0.0, 0.0],
                          [0.30, 0.0, 0.0], [0.45, 0.0, 0.0]])
    return topology, positions


class TestPositionRestraints:
    def test_each_atom_is_held_where_it_is_now(self, peptide) -> None:
        topology, positions = peptide
        [(force, parameter)] = build_restraint_forces(
            _OpenMM, topology, positions, parse_restraints("name CA"))
        assert parameter == "restraint_k_0"
        assert force.globals == [("restraint_k_0", DEFAULT_POSITION_FORCE)]
        # One particle -- the CA -- anchored to its own coordinates.
        [(index, reference)] = force.terms
        assert index == 1
        assert reference == pytest.approx(list(positions[1]))

    def test_the_distance_is_the_periodic_one(self, peptide) -> None:
        """An atom that wraps across the boundary must not be yanked the
        width of the box back to its reference point."""
        topology, positions = peptide
        [(force, _)] = build_restraint_forces(
            _OpenMM, topology, positions, parse_restraints("name CA"))
        assert "periodicdistance(x, y, z, x0, y0, z0)" in force.energy
        assert force.per_term == ["x0", "y0", "z0"]


class TestTheOtherThreeKinds:
    def test_a_distance_restraint_binds_its_two_atoms(self, peptide) -> None:
        topology, positions = peptide
        spec = [{"kind": "distance", "selection": "name N CA",
                 "force_constant": 500.0, "target": 0.2}]
        [(force, _)] = build_restraint_forces(
            _OpenMM, topology, positions, parse_restraints(spec))
        assert force.energy == "restraint_k_0*(r - r0)^2"
        assert force.terms == [(0, 1, [0.2])]

    def test_a_distance_without_a_target_is_held_where_it_is(
            self, peptide) -> None:
        """It was held at zero, pulling the two atoms onto each other, while
        ``target: None`` was documented as "where it is now"."""
        topology, positions = peptide
        spec = [{"kind": "distance", "selection": "name N CA",
                 "force_constant": 500.0}]
        [(force, _)] = build_restraint_forces(
            _OpenMM, topology, positions, parse_restraints(spec))
        assert force.terms[0][-1] == [pytest.approx(0.15)]

    def test_an_angle_restraint_takes_three(self, peptide) -> None:
        topology, positions = peptide
        spec = [{"kind": "angle", "selection": "name N CA C",
                 "force_constant": 100.0, "target": 1.9}]
        [(force, _)] = build_restraint_forces(
            _OpenMM, topology, positions, parse_restraints(spec))
        assert force.energy == "restraint_k_0*(theta - theta0)^2"
        assert force.terms == [(0, 1, 2, [1.9])]

    def test_the_torsion_penalty_is_continuous_across_pi(self, peptide) -> None:
        """Written through cos, so the wrap at pi costs nothing: a plain
        (theta - theta0)^2 jumps by 4*pi^2 there."""
        topology, positions = peptide
        spec = [{"kind": "torsion", "selection": "name N CA C O",
                 "force_constant": 50.0, "target": 3.1}]
        [(force, _)] = build_restraint_forces(
            _OpenMM, topology, positions, parse_restraints(spec))
        assert "1 - cos(theta - theta0)" in force.energy
        assert force.terms == [(0, 1, 2, 3, [3.1])]

    def test_the_torsion_is_as_stiff_as_the_angle_given_the_same_number(
            self, peptide) -> None:
        """2k(1 - cos x) has the curvature 2k of k x^2; k(1 - cos x) had
        half of it, so one number meant two springs."""
        topology, positions = peptide
        spec = [{"kind": "torsion", "selection": "name N CA C O",
                 "force_constant": 50.0, "target": 3.1}]
        [(force, _)] = build_restraint_forces(
            _OpenMM, topology, positions, parse_restraints(spec))
        assert force.energy == "2*restraint_k_0*(1 - cos(theta - theta0))"

    def test_each_restraint_gets_its_own_dial(self, peptide) -> None:
        """One global parameter per restraint is what staged release is: a
        force cannot be removed from a live context, but its parameter can be
        turned to zero."""
        topology, positions = peptide
        spec = ["name CA", {"kind": "distance", "selection": "name N CA",
                            "force_constant": 500.0}]
        built = build_restraint_forces(
            _OpenMM, topology, positions, parse_restraints(spec))
        assert [parameter for _f, parameter in built] == [
            "restraint_k_0", "restraint_k_1"]


class TestRefusals:
    def test_a_selection_matching_nothing_is_refused(self, peptide) -> None:
        """A restraint on nothing holds nothing, and a run that silently
        applied it would look restrained and not be."""
        topology, positions = peptide
        with pytest.raises(ValueError, match="matched no"):
            build_restraint_forces(_OpenMM, topology, positions,
                                   parse_restraints("name ZZ"))

    def test_an_unreadable_selection_is_refused_with_the_string(
            self, peptide) -> None:
        topology, positions = peptide
        with pytest.raises(ValueError, match="could not be"):
            build_restraint_forces(_OpenMM, topology, positions,
                                   parse_restraints("name (((("))

    @pytest.mark.parametrize("kind,selection,needs", [
        ("distance", "name N CA C", "two"),
        ("angle", "name N CA", "three"),
        ("torsion", "name N CA C", "four"),
    ])
    def test_the_wrong_atom_count_is_refused_by_number(
            self, peptide, kind, selection, needs) -> None:
        topology, positions = peptide
        spec = [{"kind": kind, "selection": selection, "force_constant": 1.0}]
        with pytest.raises(ValueError, match=needs):
            build_restraint_forces(_OpenMM, topology, positions,
                                   parse_restraints(spec))


class TestTheParserEdges:
    def test_an_unknown_kind_is_refused_with_the_list(self) -> None:
        with pytest.raises(ValueError, match="position"):
            parse_restraints([{"kind": "pull", "selection": "all"}])

    def test_a_block_without_a_selection_is_refused(self) -> None:
        with pytest.raises(ValueError, match="selection"):
            parse_restraints([{"kind": "position"}])

    def test_only_position_has_a_conventional_default_force(self) -> None:
        """Guessing the strength of a distance bias would be inventing it."""
        with pytest.raises(ValueError, match="force_constant"):
            parse_restraints([{"kind": "distance", "selection": "name N CA"}])
        [got] = parse_restraints([{"kind": "position", "selection": "all"}])
        assert got.force_constant == DEFAULT_POSITION_FORCE

    def test_a_single_dict_is_one_restraint(self) -> None:
        [got] = parse_restraints({"kind": "position", "selection": "all"})
        assert got.kind == "position"

    def test_units_follow_the_coordinate(self) -> None:
        """A spring on a length and a spring on an angle are not measured in
        the same thing."""
        blocks = parse_restraints([
            {"kind": "distance", "selection": "s", "force_constant": 1.0},
            {"kind": "torsion", "selection": "s", "force_constant": 1.0},
        ])
        assert blocks[0].units == "kJ/mol/nm^2"
        assert blocks[1].units == "kJ/mol/rad^2"


class TestTheReleaseSchedule:
    def test_the_default_lets_go_in_stages_and_ends_free(self) -> None:
        schedule = ReleaseSchedule()
        stages = [schedule.force_at(f) for f in (0.0, 0.3, 0.6, 0.9, 1.0)]
        assert stages == [1000.0, 500.0, 100.0, 0.0, 0.0]

    def test_past_the_end_is_the_last_step(self) -> None:
        assert ReleaseSchedule().force_at(2.0) == 0.0

    def test_no_steps_is_no_force(self) -> None:
        assert ReleaseSchedule(steps=()).force_at(0.5) == 0.0

    def test_the_record_carries_its_units_in_the_name(self) -> None:
        assert ReleaseSchedule().as_record() == {
            "steps_kjmol_per_nm2": [1000.0, 500.0, 100.0, 0.0]}
