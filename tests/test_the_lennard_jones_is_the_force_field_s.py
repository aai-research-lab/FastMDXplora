"""The Lennard-Jones treatment a run gets is the one its force field was fitted with.

Two ways it was not. Every run passed `useDispersionCorrection=True` to
`createSystem`, and OpenMM takes a keyword over the file, so CHARMM36, whose
`charmm36.xml` declares the correction off and whose lipids were fitted
without it, ran with an isotropic tail (-0.14 kJ/mol per water and -75 bar in
CHARMM water at 1.2 nm). And an explicit `force_field` list was switched at
0.9 nm whatever it held, so AMBER listed by hand ran switched while `amber14`
by name did not. The methods text said neither.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fastmdxplora.setup.forcefields import nonbonded_scheme


def _scheme(files, **stated):
    return nonbonded_scheme(files, cutoff_nm=stated.get("cutoff"),
                            use_switching_function=stated.get("switching"),
                            switch_distance_nm=stated.get("switch"))


class TestTheDispersionCorrectionIsTheFile_s:

    def test_the_files_say_off_for_charmm36_and_on_for_amber(self):
        omm = pytest.importorskip("openmm")
        from openmm import app, unit

        from fastmdxplora.setup.prepare import _dispersion_correction_of

        def built(*files):
            field = app.ForceField(*files)
            modeller = app.Modeller(app.Topology(), [])
            modeller.addSolvent(field, model="tip3p",
                                boxSize=omm.Vec3(2.6, 2.6, 2.6) * unit.nanometer)
            return field.createSystem(modeller.topology, nonbondedMethod=app.PME,
                                      nonbondedCutoff=1.2 * unit.nanometer)

        assert _dispersion_correction_of(
            omm, built("charmm36.xml", "charmm36/water.xml")) is False
        assert _dispersion_correction_of(
            omm, built("amber14-all.xml", "amber14/tip3p.xml")) is True

    def test_charmm36_2024_says_nothing_so_openmm_would_turn_it_on(self):
        """Why the scheme, not the file, decides for CHARMM36."""
        omm = pytest.importorskip("openmm")
        from openmm import app, unit

        from fastmdxplora.setup.prepare import _dispersion_correction_of

        try:
            field = app.ForceField("charmm36_2024.xml", "charmm36_2024/water.xml")
        except ValueError:
            pytest.skip("this OpenMM ships no charmm36_2024.xml (it came in 8.3)")
        modeller = app.Modeller(app.Topology(), [])
        modeller.addSolvent(field, model="tip3p",
                            boxSize=omm.Vec3(2.6, 2.6, 2.6) * unit.nanometer)
        system = field.createSystem(modeller.topology, nonbondedMethod=app.PME,
                                    nonbondedCutoff=1.2 * unit.nanometer)
        assert _dispersion_correction_of(omm, system) is True

    def test_a_record_of_it_is_worked_out_by_the_run(self):
        from fastmdxplora.config.diff import WORKED_OUT_BY_THE_RUN

        assert "setup.dispersion_correction" in WORKED_OUT_BY_THE_RUN

    @staticmethod
    def _keywords(tmp_path, **settings):
        """What `prepare_system` hands `createSystem`, with OpenMM stood in."""
        from fastmdxplora.setup import prepare as _prepare

        captured = {}

        def create_system(topology, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        field = MagicMock()
        field.createSystem = create_system
        modeller = MagicMock(
            topology=MagicMock(getNumAtoms=MagicMock(return_value=10),
                               getPeriodicBoxVectors=MagicMock(return_value=None)),
            positions=MagicMock())
        fake = {
            "openmm": MagicMock(
                XmlSerializer=MagicMock(serialize=MagicMock(return_value="<x/>")),
                VerletIntegrator=MagicMock(), Context=MagicMock()),
            "unit": MagicMock(nanometer=1, molar=1, kelvin=1, picoseconds=1, amu=1),
            "ForceField": MagicMock(return_value=field),
            "HBonds": object(),
            "Modeller": MagicMock(return_value=modeller),
            "PDBFile": MagicMock(side_effect=lambda p: MagicMock(
                topology=MagicMock(), positions=MagicMock())),
            **{name: object() for name in ("PME", "NoCutoff", "CutoffNonPeriodic",
                                           "CutoffPeriodic", "Ewald")},
        }
        fake["PDBFile"].writeFile = MagicMock()
        prepared = tmp_path / "prepared.pdb"
        prepared.write_text("ATOM\nEND\n")
        with patch.object(_prepare, "_import_openmm", return_value=fake):
            _prepare.prepare_system(prepared, tmp_path / "out", **settings)
        return captured

    def test_unstated_charmm36_runs_without_it(self, tmp_path):
        assert self._keywords(tmp_path, forcefield="charmm36")[
            "useDispersionCorrection"] is False

    def test_unstated_charmm36_2024_files_run_without_it(self, tmp_path):
        """`charmm36_2024.xml` says nothing, which OpenMM reads as on; its
        lipids are the C36 lipids all the same."""
        assert self._keywords(tmp_path, force_field=[
            "charmm36_2024.xml", "charmm36_2024/water.xml"])[
            "useDispersionCorrection"] is False

    def test_unstated_amber_is_left_to_the_file(self, tmp_path):
        assert "useDispersionCorrection" not in self._keywords(
            tmp_path, forcefield="amber14")
        assert "useDispersionCorrection" not in self._keywords(
            tmp_path, force_field=["amber14-all.xml", "amber14/tip3p.xml"])

    def test_stated_against_the_scheme_it_is_warned_of(self, tmp_path, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            self._keywords(tmp_path, forcefield="charmm36", dispersion_correction=True)
        assert any("fitted without it" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("stated", [True, False])
    def test_stated_it_wins(self, tmp_path, stated):
        assert self._keywords(tmp_path, forcefield="charmm36",
                              dispersion_correction=stated)[
            "useDispersionCorrection"] is stated

    def test_the_schema_hands_over_nothing(self):
        from fastmdxplora.config.schema import SETUP

        assert SETUP.get("dispersion_correction").default is None


class TestAListOfFilesTakesItsFamily_sScheme:

    def test_amber_files_are_not_switched(self):
        cutoff, switching, distance, said = _scheme(
            ["amber14-all.xml", "amber14/tip3p.xml"])
        assert (cutoff, switching, distance) == (1.0, False, None)
        assert "AMBER files given" in said

    def test_amber_beside_a_plain_water_file_is_still_amber(self):
        assert _scheme(["amberfb15.xml", "tip3p.xml"])[:3] == (1.0, False, None)

    def test_charmm36_files_take_charmm36_s(self):
        cutoff, switching, distance, said = _scheme(
            ["charmm36.xml", "charmm36/water.xml"])
        assert (cutoff, switching, distance) == (1.2, True, 1.0)
        assert "CHARMM36 files given" in said

    def test_a_person_s_own_folder_does_not_decide(self):
        assert _scheme(["charmm36.xml", "charmm36/water.xml",
                        "/home/u/amber_ligs/lig.xml"])[:3] == (1.2, True, 1.0)

    def test_an_absolute_folder_named_like_a_family_does_not_decide(self):
        assert _scheme(["charmm36.xml", "charmm36/water.xml",
                        "/home/u/amber/lig.xml"])[:3] == (1.2, True, 1.0)

    @pytest.mark.parametrize("named,files", [
        ("charmm36", ["charmm36.xml", "charmm36/water.xml"]),
        ("amber14", ["amber14-all.xml", "amber14/tip3p.xml"])])
    def test_the_box_advisory_reads_the_list_as_setup_does(self, named, files):
        from fastmdxplora.advisories import _a_box_too_small_for_the_cutoff

        structure = {"extents_angstrom": [5.0, 5.0, 5.0]}
        for padding in (1.2, 1.5, 1.8):
            by_name = _a_box_too_small_for_the_cutoff(
                structure, {"forcefield": named, "solvent_padding_nm": padding})
            by_list = _a_box_too_small_for_the_cutoff(
                structure, {"force_field": files, "solvent_padding_nm": padding})
            assert (by_name is None) == (by_list is None), (named, padding)

    def test_the_box_estimate_reads_the_list_as_setup_does(self):
        from fastmdxplora.setup.estimate import _cutoff

        assert _cutoff({"force_field": ["charmm36.xml", "charmm36/water.xml"]}) == 1.2
        assert _cutoff({"force_field": ["amber14-all.xml", "amber14/tip3p.xml"]}) == 1.0

    def test_openmmforcefields_charmm_pair_is_charmm36(self):
        assert _scheme(["charmm/charmm36_nowaters.xml",
                        "charmm/waters_ions_default.xml"])[:3] == (1.2, True, 1.0)

    @pytest.mark.parametrize("files", [
        ["amber14-all.xml", "charmm36.xml"], ["mine.xml"], ["charmm_polar_2019.xml"]])
    def test_anything_else_takes_openmm_s_default_and_says_so(self, files):
        cutoff, switching, distance, said = _scheme(files)
        assert (cutoff, switching, distance) == (1.0, False, None)
        assert "OpenMM's own default" in said

    def test_a_stated_scheme_is_kept(self):
        assert _scheme(["mine.xml"], cutoff=1.1, switching=True, switch=0.95) == (
            1.1, True, 0.95, None)
        assert _scheme(["amber14-all.xml"], switching=True)[1] is True

    def test_a_switch_distance_alone_asks_for_a_switch(self):
        assert _scheme(["mine.xml"], switch=0.9)[:3] == (1.0, True, 0.9)


class TestTheMethodsSayTheLennardJones:

    @staticmethod
    def _methods(tmp_path, setup):
        from fastmdxplora.report.methods import methods_paragraphs

        return methods_paragraphs(tmp_path, setup, {})

    def test_a_charmm36_run(self, tmp_path):
        text = self._methods(tmp_path, {
            "parameters": {"forcefield": "charmm36", "nonbonded_cutoff_nm": None},
            "resolved_forcefield": {"xmls": ["charmm36.xml", "charmm36/water.xml"]},
            "resolved": {"nonbonded_cutoff_nm": 1.2, "use_switching_function": True,
                         "switch_distance_nm": 1.0, "dispersion_correction": False}})
        assert ("Lennard-Jones interactions were cut off at 1.20 nm, switched from "
                "1.00 nm (OpenMM's potential switch), with no long-range dispersion "
                "correction.") in text
        assert "older CHARMM36 release (toppar_c36_aug15), without the CHARMM36m" in text

    def test_an_amber_run(self, tmp_path):
        text = self._methods(tmp_path, {
            "parameters": {"forcefield": "amber14", "nonbonded_cutoff_nm": None},
            "resolved_forcefield": {"xmls": ["amber14-all.xml", "amber14/tip3p.xml"]},
            "resolved": {"nonbonded_cutoff_nm": 1.0, "use_switching_function": False,
                         "dispersion_correction": True}})
        assert ("Lennard-Jones interactions were cut off at 1.00 nm with no switching "
                "function, with the isotropic long-range dispersion correction.") in text
        assert "CHARMM36m" not in text

    def test_with_no_cutoff_there_is_no_lennard_jones_cutoff(self, tmp_path):
        text = self._methods(tmp_path, {"parameters": {
            "nonbonded_method": "NoCutoff", "nonbonded_cutoff_nm": 1.0,
            "use_switching_function": False, "dispersion_correction": True}})
        assert "Lennard-Jones" not in text

    def test_without_periodic_boundaries_no_tail_is_said(self, tmp_path):
        text = self._methods(tmp_path, {"parameters": {
            "nonbonded_method": "CutoffNonPeriodic", "nonbonded_cutoff_nm": 1.0,
            "use_switching_function": False, "dispersion_correction": True}})
        assert "cut off at 1.00 nm with no switching function." in text
        assert "dispersion" not in text

    def test_a_record_that_says_neither_says_nothing_about_it(self, tmp_path):
        text = self._methods(tmp_path, {"parameters": {"nonbonded_cutoff_nm": 1.0}})
        assert "Lennard-Jones" not in text
