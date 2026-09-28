"""Putting a protein in a lipid bilayer, and the things that go wrong quietly.

A membrane protein simulated in water is not the protein: the hydrophobic belt
that sits in the bilayer is exposed to solvent and the helices splay. OpenMM
builds the bilayer itself, which removes the usual dependency on an external
packing tool -- but it does not check the two things that make the result
meaningful, and both fail silently.
"""

from __future__ import annotations

import pytest


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

    def test_charmm36_gets_its_own_lipids(self) -> None:
        """CHARMM36's lipids are in `charmm36.xml`. The fallback named
        `charmm36/waters.xml`, which OpenMM does not ship, so a CHARMM36 list
        without the main file was refused for a file that does not exist."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from openmm.app import ForceField

        from fastmdxplora.setup.membrane import membrane_forcefield_files

        found = membrane_forcefield_files(["charmm36/water.xml"], lipid="DOPC")
        assert found == ["charmm36/water.xml", "charmm36.xml"]
        assert any(name.startswith("DOPC") for name in ForceField(*found)._templates)

    def test_a_file_that_loads_without_the_lipid_is_refused(self, monkeypatch) -> None:
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.setup import membrane

        monkeypatch.setitem(membrane._LIPID_PARAMETERS, "amber14", "amber14/tip3p.xml")
        with pytest.raises(ValueError, match="template for POPC"):
            membrane.membrane_forcefield_files(["amber14/protein.ff14SB.xml"])

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
        assert is_membrane_system(self._topology(["ALA", *["POP"] * 40, "HOH"]))

    def test_a_ligand_named_like_a_lipid_is_not_a_bilayer(self) -> None:
        """`POP` is pyrophosphate in the PDB, `PC` phosphocholine and `CHL`
        chlorophyll b. An enzyme with one bound is coupled isotropically."""
        pytest.importorskip("openmm", reason="requires the [md] extra")

        from fastmdxplora.simulation.runner import is_membrane_system

        for ligand in ("POP", "PC", "CHL"):
            assert not is_membrane_system(self._topology(["ALA", ligand, "HOH"]))

    def test_a_split_lipid_is_counted_once(self) -> None:
        from fastmdxplora.lipids import BILAYER_MINIMUM_LIPIDS, is_bilayer, lipid_count

        assert lipid_count(["PA", "PC", "OL"] * 3) == 3
        assert not is_bilayer(["PA", "PC", "OL"] * (BILAYER_MINIMUM_LIPIDS - 1))
        assert is_bilayer(["PA", "PC", "OL"] * BILAYER_MINIMUM_LIPIDS)

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

class _Placed(Exception):
    """Raised in place of packing lipids or water, which takes minutes."""


def _prepared(where, monkeypatch, *, failing=None, **options):
    """Prepare a small peptide with `options`, stopping where the bilayer or
    the water would be placed. The placement and the copies check are
    recorded and pass -- a tripeptide is not a membrane protein -- except
    `failing`, which refuses. Returns what was placed, what was checked,
    what `addMembrane` was given, and what was logged."""
    import logging
    from types import SimpleNamespace

    pytest.importorskip("openmm")
    from openmm.app import Modeller

    from fastmdxplora.refusals import StudyError
    from fastmdxplora.setup import membrane
    from fastmdxplora.setup.prepare import prepare_system
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    where.mkdir(parents=True, exist_ok=True)
    structure = where / "peptide.pdb"
    structure.write_text(TRI_ALANINE, encoding="utf-8")
    checked: list = []
    asked: dict = {}

    def place(topology, positions, **kwargs):
        checked.append("place_for_membrane")
        asked["placement"] = kwargs
        if failing == "place_for_membrane":
            raise StudyError("The placement was found wanting.",
                             code="setup.membrane.orientation_unchecked")
        return membrane.Placement(positions=positions,
                                  record={"lipid": kwargs.get("lipid"),
                                          "placed_by": "a stand-in"})

    def copies(*args, **kwargs):
        checked.append("check_chains_point_the_same_way")
        return ("The copies were found wanting."
                if failing == "check_chains_point_the_same_way" else None)

    monkeypatch.setattr(membrane, "place_for_membrane", place)
    monkeypatch.setattr(membrane, "check_chains_point_the_same_way", copies)

    def placing(method):
        def place_it(self, *args, **kwargs):
            asked[method] = kwargs
            raise _Placed(method)
        return place_it

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
    return SimpleNamespace(placed_by=placed_by, checked=checked, said=said, asked=asked)
