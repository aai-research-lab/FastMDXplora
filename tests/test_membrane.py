"""Putting a protein in a lipid bilayer, and the things that go wrong quietly.

A membrane protein simulated in water is not the protein: the hydrophobic belt
that sits in the bilayer is exposed to solvent and the helices splay. OpenMM
builds the bilayer itself, which removes the usual dependency on an external
packing tool -- but it does not check the two things that make the result
meaningful, and both fail silently.
"""

from __future__ import annotations

import numpy as np
import pytest


class TestTheProteinHasToBeOrientedFirst:
    """`addMembrane` places the bilayer in the xy plane and assumes the
    protein is already lying along z. A PDB entry usually is not:
    crystallographic axes have no relation to a membrane normal. Embedding one
    sideways packs lipids around it wrong, the run completes, and every number
    describes a structure nobody would recognise.
    """

    @staticmethod
    def _rod(axis: int):
        """A protein-shaped object extended along one axis."""
        import mdtraj as md

        top = md.Topology()
        chain = top.add_chain()
        coordinates = []
        for index in range(40):
            residue = top.add_residue("ALA", chain, resSeq=index + 1)
            for name in ("N", "CA", "C"):
                top.add_atom(name, md.element.carbon, residue)
            base = np.zeros(3)
            base[axis] = index * 0.35
            for offset in (0.0, 0.12, 0.24):
                point = base.copy()
                point[(axis + 1) % 3] += offset
                coordinates.append(point)
        return top.to_openmm(), np.array(coordinates)

    def test_a_protein_along_z_is_accepted(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import check_orientation

        topology, positions = self._rod(axis=2)
        assert check_orientation(topology, positions) is None

    def test_a_protein_lying_sideways_is_refused(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import check_orientation

        topology, positions = self._rod(axis=0)
        problem = check_orientation(topology, positions)
        assert problem is not None
        assert "membrane normal" in problem

    def test_the_refusal_says_where_oriented_structures_come_from(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import check_orientation

        topology, positions = self._rod(axis=0)
        problem = check_orientation(topology, positions)
        assert "opm.phar.umich.edu" in problem
        assert "membrane_orientation_checked" in problem, (
            "and how to proceed anyway"
        )

    def test_something_too_small_to_judge_is_not_refused(self) -> None:
        """The test is the shape of the protein, and a handful of atoms has
        no shape worth reading."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        import mdtraj as md

        from fastmdxplora.setup.membrane import check_orientation

        top = md.Topology()
        residue = top.add_residue("ALA", top.add_chain(), resSeq=1)
        top.add_atom("CA", md.element.carbon, residue)
        assert check_orientation(top.to_openmm(), np.zeros((1, 3))) is None


class TestTheForceFieldNeedsLipidParameters:
    """Without them the run fails at system creation with a message about a
    residue template for POPC, which names the symptom rather than the missing
    file."""

    def test_a_bundle_that_already_has_them_is_left_alone(self) -> None:
        """The first version decided by looking for "lipid" in the filename.
        `amber14-all.xml` is a bundle carrying lipid17 inside it, so adding
        the file again raised about a duplicate template for a residue nobody
        mentioned. The question is what the force field can build, not what
        its files are called."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import membrane_forcefield_files

        given = ["amber14-all.xml", "amber14/tip3p.xml"]
        assert membrane_forcefield_files(given) == given

    def test_one_that_lacks_them_gets_them(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import membrane_forcefield_files

        found = membrane_forcefield_files(
            ["amber14/protein.ff14SB.xml", "amber14/tip3p.xml"])
        assert any("lipid" in f for f in found)

    def test_a_force_field_with_no_lipids_available_says_so(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import membrane_forcefield_files

        with pytest.raises(ValueError, match="lipid parameters"):
            membrane_forcefield_files(["some-other-field.xml"])


class TestTheBarostatKnowsItIsAMembrane:
    """An ordinary barostat scales x, y and z together, which squeezes a
    bilayer that should be free to change thickness independently of its area.

    Area per lipid is the number membrane simulations are validated against,
    so getting this wrong invalidates the run without stopping it.
    """

    @staticmethod
    def _topology(names):
        import mdtraj as md

        top = md.Topology()
        chain = top.add_chain()
        for name in names:
            residue = top.add_residue(name, chain, resSeq=1)
            top.add_atom("C", md.element.carbon, residue)
        return top.to_openmm()

    def test_lipids_are_detected_from_the_topology(self) -> None:
        """Rather than taken as a setting: the barostat has to be right
        whether or not anybody remembered to say so."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.simulation.runner import is_membrane_system

        assert not is_membrane_system(self._topology(["ALA", "HOH"]))
        assert is_membrane_system(self._topology(["ALA", "POP", "HOH"]))

    def test_a_membrane_gets_the_membrane_barostat(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        import openmm as omm
        import openmm.unit as unit

        from fastmdxplora.simulation.runner import _add_barostat

        system = omm.System()
        system.addParticle(1.0)
        _add_barostat({"openmm": omm, "unit": unit}, system,
                      temperature_K=300.0, pressure_bar=1.0, frequency=25,
                      membrane=True)
        assert isinstance(system.getForce(0), omm.MonteCarloMembraneBarostat)

    def test_and_water_gets_the_ordinary_one(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        import openmm as omm
        import openmm.unit as unit

        from fastmdxplora.simulation.runner import _add_barostat

        system = omm.System()
        system.addParticle(1.0)
        _add_barostat({"openmm": omm, "unit": unit}, system,
                      temperature_K=300.0, pressure_bar=1.0, frequency=25)
        assert isinstance(system.getForce(0), omm.MonteCarloBarostat)
        assert not isinstance(system.getForce(0),
                              omm.MonteCarloMembraneBarostat)

    def test_the_membrane_plane_is_coupled_and_the_normal_is_free(self) -> None:
        """Which is what makes it the right barostat: the bilayer keeps its
        area while its thickness moves."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        import openmm as omm
        import openmm.unit as unit

        from fastmdxplora.simulation.runner import _add_barostat

        system = omm.System()
        system.addParticle(1.0)
        _add_barostat({"openmm": omm, "unit": unit}, system,
                      temperature_K=300.0, pressure_bar=1.0, frequency=25,
                      membrane=True)
        barostat = system.getForce(0)
        assert barostat.getXYMode() == omm.MonteCarloMembraneBarostat.XYIsotropic
        assert barostat.getZMode() == omm.MonteCarloMembraneBarostat.ZFree
        assert barostat.getDefaultSurfaceTension().value_in_unit(
            unit.bar * unit.nanometer) == 0.0, (
            "a bilayer at equilibrium has no surface tension, and imposing "
            "one is a decision to make deliberately"
        )


class TestTheSettingsReachEveryInterface:
    def test_they_are_declared(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        membrane = PHASE_SCHEMAS["setup"].get("membrane")
        assert membrane is not None
        assert membrane.choices and "POPC" in membrane.choices
        assert PHASE_SCHEMAS["setup"].get("membrane_orientation_checked") is not None

    def test_they_reach_the_command_line(self) -> None:
        from fastmdxplora.cli.main import _PHASE_SPEC

        table, _prefix = _PHASE_SPEC["setup"]
        offered = {dest for _flag, dest, _kw in table}
        assert {"membrane", "membrane_orientation_checked"} <= offered

    def test_the_pipeline_passes_them(self, tmp_path, monkeypatch) -> None:
        from tests._the_phase import what_preparation_receives

        received = what_preparation_receives(
            tmp_path, monkeypatch, membrane="POPC", membrane_orient=True,
            membrane_orientation_checked=True)
        assert received["membrane"] == "POPC"
        assert received["membrane_orient"] is True
        assert received["membrane_orientation_checked"] is True

class TestOrientingAStructureThatIsInTheWrongFrame:
    """The alternative offered was to fetch an oriented structure from OPM,
    which is correct and is a detour. For a transmembrane helix or a bundle of
    them the orientation is not a hard question: the protein is longest along
    the normal, because that is the direction it spans.
    """

    @staticmethod
    def _sideways():
        import mdtraj as md

        top = md.Topology()
        chain = top.add_chain()
        coordinates = []
        for index in range(40):
            residue = top.add_residue("ALA", chain, resSeq=index + 1)
            for name in ("N", "CA", "C"):
                top.add_atom(name, md.element.carbon, residue)
            for offset in (0.0, 0.12, 0.24):
                coordinates.append([index * 0.35, offset, 0.0])
        return top.to_openmm(), np.array(coordinates)

    def test_it_turns_a_sideways_structure_upright(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import (
            check_orientation,
            orient_for_membrane,
        )

        topology, positions = self._sideways()
        assert check_orientation(topology, positions) is not None
        turned = orient_for_membrane(topology, positions)
        assert check_orientation(topology, turned) is None

    def test_it_rotates_rather_than_reflects(self) -> None:
        """A reflection would turn the structure into its mirror image, which
        is a different molecule."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import orient_for_membrane

        topology, positions = self._sideways()
        turned = np.asarray(orient_for_membrane(topology, positions))

        for a, b in ((0, 1), (0, 5), (3, 20)):
            before = np.linalg.norm(positions[a] - positions[b])
            after = np.linalg.norm(turned[a] - turned[b])
            assert np.isclose(before, after), (
                "a rotation preserves every distance; a reflection would too, "
                "but would invert the structure -- the determinant check is "
                "what tells them apart"
            )

    def test_it_is_asked_for_rather_than_done_quietly(self) -> None:
        """It is wrong where a large soluble domain drags the axis away from
        the normal, so it is a choice."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS["setup"].get("membrane_orient")
        assert field is not None
        assert field.default is False
        assert "soluble domain" in field.help

    def test_the_refusal_offers_it_first(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import check_orientation

        topology, positions = self._sideways()
        problem = check_orientation(topology, positions)
        assert "membrane_orient: true" in problem
        assert "opm.phar.umich.edu" in problem

    def test_the_log_says_what_it_is_about_to_do(self, tmp_path, monkeypatch) -> None:
        """It announced "Solvating" and then refused to solvate, because the
        message printed before the branch chose. One message now chooses
        what to say, and says only that."""
        membrane = _prepared(tmp_path / "bilayer", monkeypatch, membrane="POPC",
                             membrane_orientation_checked=True)
        assert membrane.placed_by == "addMembrane"
        assert any("Embedding in a POPC bilayer" in m for m in membrane.said)
        assert not any("Solvating" in m for m in membrane.said)

        water = _prepared(tmp_path / "water", monkeypatch)
        assert water.placed_by == "addSolvent"
        assert any("Solvating in a cube box" in m for m in water.said)
        assert not any("Embedding" in m for m in water.said)

class TestItHandlesWhatThePipelineActuallyPasses:
    """The tests above pass plain numpy arrays, which is convenient and is not
    what the pipeline does.

    OpenMM's Modeller holds positions as a Quantity of Vec3, and the branch
    handling that was the only branch the software ever took -- and the only
    one the tests never ran. It took Vec3 from openmm.unit, where it does not
    live, and failed on the first real structure.
    """

    @staticmethod
    def _sideways_with_units():
        import mdtraj as md
        from openmm import Vec3
        from openmm import unit

        top = md.Topology()
        chain = top.add_chain()
        points = []
        for index in range(40):
            residue = top.add_residue("ALA", chain, resSeq=index + 1)
            for name in ("N", "CA", "C"):
                top.add_atom(name, md.element.carbon, residue)
            for offset in (0.0, 0.12, 0.24):
                points.append(Vec3(index * 0.35, offset, 0.0))
        return top.to_openmm(), unit.Quantity(points, unit.nanometer)

    def test_a_quantity_goes_in_and_a_quantity_comes_out(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from openmm import unit

        from fastmdxplora.setup.membrane import orient_for_membrane

        topology, positions = self._sideways_with_units()
        turned = orient_for_membrane(topology, positions)
        assert unit.is_quantity(turned), (
            "the pipeline hands on what it gets back, so the units have to "
            "survive the rotation"
        )

    def test_and_it_is_oriented_afterwards(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import (
            check_orientation,
            orient_for_membrane,
        )

        topology, positions = self._sideways_with_units()
        assert check_orientation(topology, positions) is not None
        assert check_orientation(
            topology, orient_for_membrane(topology, positions)) is None

    def test_the_orientation_check_takes_a_quantity_too(self) -> None:
        """It is called on Modeller's positions, which always carry units."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import check_orientation

        topology, positions = self._sideways_with_units()
        assert check_orientation(topology, positions) is not None


class TestWhetherTheRotationCanBeTrusted:
    """`membrane_orient` rotated and proceeded regardless: the warning about
    soluble domains was a log line, not a check.

    So a protein whose shape has nothing to do with a membrane got rotated
    onto an arbitrary axis and the run continued, which is the failure this
    software exists to remove.
    """

    @staticmethod
    def _helix(pattern, spherical=False):
        import mdtraj as md
        import numpy as np

        top = md.Topology()
        chain = top.add_chain()
        points = []
        rng = np.random.RandomState(0)
        for index, name in enumerate(pattern):
            residue = top.add_residue(name, chain, resSeq=index + 1)
            for atom in ("N", "CA", "C", "CB"):
                top.add_atom(atom, md.element.carbon, residue)
                base = (rng.normal(scale=0.4, size=3) if spherical
                        else np.array([0.0, 0.0,
                                       (index - len(pattern) / 2) * 0.15]))
                points.append(base + rng.normal(scale=0.05, size=3))
        return top.to_openmm(), np.array(points)

    #: Charged at the ends, hydrophobic through the middle: a bilayer-spanning
    #: arrangement.
    MEMBRANE_LIKE = (["LYS", "ASP"] * 3 + ["LEU", "ILE", "VAL", "PHE"] * 6
                     + ["ARG", "GLU"] * 3)
    #: Charged and hydrophobic mixed evenly: a soluble one.
    SOLUBLE_LIKE = ["LYS", "LEU", "ASP", "ILE", "ARG", "VAL"] * 5

    def test_a_bilayer_spanning_arrangement_passes(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import check_hydrophobic_belt

        topology, positions = self._helix(self.MEMBRANE_LIKE)
        assert check_hydrophobic_belt(topology, positions) is None

    def test_a_soluble_arrangement_is_refused(self) -> None:
        """It has no hydrophobic belt, so it does not belong in a bilayer
        however it is rotated."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import check_hydrophobic_belt

        topology, positions = self._helix(self.SOLUBLE_LIKE)
        problem = check_hydrophobic_belt(topology, positions)
        assert problem is not None
        assert "hydrophobic" in problem
        assert "opm.phar.umich.edu" in problem

    def test_the_threshold_has_room_on_both_sides(self) -> None:
        """A bare comparison sat on the noise between the two cases and would
        have decided a real question by a rounding difference. Measured, the
        arrangements give about 0.4 and 1.0."""
        from fastmdxplora.setup.membrane import _BELT_RATIO

        assert 0.5 < _BELT_RATIO < 0.95

    def test_a_shape_with_no_longest_axis_is_refused(self) -> None:
        """The axis the calculation returns is then chosen by noise, and the
        same protein in a different starting frame would give a different
        answer."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import check_axis_is_well_defined

        topology, positions = self._helix(self.MEMBRANE_LIKE, spherical=True)
        problem = check_axis_is_well_defined(topology, positions)
        assert problem is not None
        assert "chosen by noise" in problem

    def test_an_elongated_shape_is_not(self) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup.membrane import check_axis_is_well_defined

        topology, positions = self._helix(self.MEMBRANE_LIKE)
        assert check_axis_is_well_defined(topology, positions) is None

    def test_too_few_residues_to_judge_says_nothing(self) -> None:
        """A claim from ten atoms would be a claim from nothing."""
        pytest.importorskip("openmm", reason="requires the [md] extra")


        from fastmdxplora.setup.membrane import check_hydrophobic_belt

        topology, positions = self._helix(["LEU", "LYS"])
        assert check_hydrophobic_belt(topology, positions) is None

    def test_both_checks_run_in_the_setup_phase(self, tmp_path, monkeypatch) -> None:
        # Whether the axis can be trusted before rotating, and whether what
        # came out looks like a membrane protein -- both, for a membrane, and
        # neither for a box of water.
        bilayer = _prepared(tmp_path / "bilayer", monkeypatch, membrane="POPC",
                            membrane_orient=True)
        assert {"check_axis_is_well_defined", "check_hydrophobic_belt"} <= set(bilayer.checked)
        water = _prepared(tmp_path / "water", monkeypatch, membrane_orient=True)
        assert water.checked == []

    def test_and_can_be_overridden_deliberately(self, tmp_path, monkeypatch) -> None:
        """Somebody who knows their structure should not be blocked by a
        coarse test -- but has to say so."""
        from fastmdxplora.refusals import StudyError

        for check in ("check_axis_is_well_defined", "check_hydrophobic_belt"):
            with pytest.raises(StudyError) as caught:
                _prepared(tmp_path / f"refused-{check}", monkeypatch, membrane="POPC",
                          membrane_orient=True, failing=check)
            assert caught.value.code == "setup.membrane.orientation_unchecked", check
            assert "found wanting" in str(caught.value), check

            said_so = _prepared(tmp_path / f"said-{check}", monkeypatch, membrane="POPC",
                                membrane_orient=True, membrane_orientation_checked=True,
                                failing=check)
            assert said_so.placed_by == "addMembrane", check
            assert check not in said_so.checked, check


class _Placed(Exception):
    """Raised in place of packing lipids or water, which takes minutes."""


def _prepared(where, monkeypatch, *, failing=None, **options):
    """Prepare a small peptide with `options`, stopping where the bilayer or
    the water would be placed. The orientation checks are recorded and pass
    -- a tripeptide is not a membrane protein -- except `failing`, which
    reports a problem. Returns what was placed, what was checked, and what
    was logged."""
    import logging
    from types import SimpleNamespace

    from openmm.app import Modeller

    from fastmdxplora.setup import membrane
    from fastmdxplora.setup.prepare import prepare_system
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    where.mkdir(parents=True, exist_ok=True)
    structure = where / "peptide.pdb"
    structure.write_text(TRI_ALANINE, encoding="utf-8")
    checked: list = []

    def stand_in(name):
        def check(*args, **kwargs):
            checked.append(name)
            return "The orientation was found wanting." if name == failing else None
        return check

    for name in ("check_axis_is_well_defined", "check_orientation",
                 "check_hydrophobic_belt", "check_chains_point_the_same_way"):
        monkeypatch.setattr(membrane, name, stand_in(name))
    def placing(method):
        def place(self, *args, **kwargs):
            raise _Placed(method)
        return place

    for method in ("addMembrane", "addSolvent"):
        monkeypatch.setattr(Modeller, method, placing(method))
    said: list = []

    class Heard(logging.Handler):
        def emit(self, record):
            said.append(record.getMessage())

    log = logging.getLogger("fastmdx")
    heard, level = Heard(), log.level
    log.addHandler(heard)
    log.setLevel(logging.INFO)
    try:
        prepare_system(prepared_pdb=str(structure), output_dir=str(where / "setup"), **options)
        placed_by = None
    except _Placed as placed:
        placed_by = str(placed)
    finally:
        log.removeHandler(heard)
        log.setLevel(level)
    return SimpleNamespace(placed_by=placed_by, checked=checked, said=said)
