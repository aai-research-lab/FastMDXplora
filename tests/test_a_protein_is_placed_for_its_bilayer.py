"""A protein is oriented and placed for its bilayer before the bilayer is built.

OpenMM builds the bilayer in the xy plane at `membraneCenterZ` and takes the
protein's frame as it is. Nothing placed it: the bilayer sat at z = 0 of
whatever frame the file was in, which is OPM's convention and no other, and
`membrane_orient` rotated by the longest axis and centred on the centroid,
wrong for anything with a soluble domain. An OPM file's membrane markers were
read as a molecule. Placement now follows the study's settings, from a fit
of where the lipid-facing surface is apolar, and says how in the setup record.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.refusals import StudyError
from fastmdxplora.setup.membrane import FRAME_TOLERANCE_DEG, place_for_membrane
from fastmdxplora.setup.membrane_fit import fit_along, rotation_onto_z
from tests.test_membrane_fit import _barrel, _globule


def _belt_centre_z(top, points) -> float:
    return float(fit_along(top, points, (0, 0, 1)).plane_point[2])


class TestTheFrameIsChosenByTheSettings:

    def test_an_opm_frame_is_kept_as_it_is(self) -> None:
        top, points = _barrel()
        placed = place_for_membrane(top, points + [0, 0, 0.3], frame="opm")
        assert np.allclose(placed.positions, points + [0, 0, 0.3])
        assert placed.record["placed_by"] == "OPM frame"
        assert placed.record["fitted_centre_z_nm"] == pytest.approx(0.3, abs=0.15)

    def test_a_stated_orientation_is_kept_and_centred_by_the_fit(self) -> None:
        top, points = _barrel()
        placed = place_for_membrane(top, points + [1.0, 2.0, 2.5], orientation_checked=True)
        moved = np.asarray(placed.positions)
        assert np.allclose(moved[:, :2], (points + [1.0, 2.0, 0])[:, :2])
        assert _belt_centre_z(top, moved) == pytest.approx(0.0, abs=0.15)
        assert placed.record["center_shift_nm"] == pytest.approx(-2.5, abs=0.15)

    def test_a_stated_centre_is_used(self) -> None:
        top, points = _barrel()
        placed = place_for_membrane(top, points, orientation_checked=True, center_z_nm=1.2)
        assert np.allclose(np.asarray(placed.positions)[:, 2], points[:, 2] - 1.2)
        assert placed.record["placed_by"] == "stated orientation and centre"

    def test_a_centre_needs_a_stated_orientation(self) -> None:
        top, points = _barrel()
        with pytest.raises(StudyError) as refused:
            place_for_membrane(top, points, center_z_nm=1.2)
        assert refused.value.code == "config.option.missing_companion"
        with pytest.raises(StudyError) as refused:
            place_for_membrane(top, points, orient=True, center_z_nm=1.2)
        assert refused.value.code == "config.option.conflicting"

    def test_an_oriented_structure_keeps_its_frame(self) -> None:
        tilted = rotation_onto_z((0, np.sin(np.radians(8)), np.cos(np.radians(8)))).T
        top, points = _barrel(normal=tilted @ [0, 0, 1])
        placed = place_for_membrane(top, points + [0, 0, -1.0])
        assert placed.record["tilt_of_input_deg"] < FRAME_TOLERANCE_DEG
        assert "rotation" not in placed.record
        assert placed.record["placed_by"].startswith("structure's own orientation")

    def test_a_structure_on_its_side_is_refused_and_told_the_tilt(self) -> None:
        top, points = _barrel(normal=(1, 0, 0))
        with pytest.raises(StudyError) as refused:
            place_for_membrane(top, points)
        assert refused.value.code == "setup.membrane.orientation_unchecked"
        assert "90 degrees from z" in str(refused.value)
        assert "membrane_orient: true" in str(refused.value)

    @pytest.mark.parametrize("normal", [(1, 0, 0), (0.5, -0.4, 0.77)])
    def test_orienting_puts_the_fitted_normal_on_z_and_the_centre_at_zero(self, normal) -> None:
        """Judged against the barrel's own normal, not by fitting again: a
        second fit adds its own error to the first's, and the two together
        crossed a 5 degree line on some CI platforms and not others."""
        from fastmdxplora.setup.membrane_fit import fit_membrane

        top, points = _barrel(normal=normal)
        start = points + [3, -1, 4]
        placed = place_for_membrane(top, start, orient=True)
        moved = np.asarray(placed.positions)
        again = fit_along(top, moved, (0, 0, 1))
        assert again.plane_point[2] == pytest.approx(0.0, abs=0.15)
        assert placed.record["placed_by"] == "fitted orientation and centre"
        rotation = np.asarray(placed.record["rotation"])
        assert np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-5)
        # The fitted normal goes exactly onto z ...
        assert rotation @ fit_membrane(top, start).normal == pytest.approx(
            [0.0, 0.0, 1.0], abs=1e-5)
        # ... and the barrel's true normal within what the fit resolves.
        true = np.asarray(normal, dtype=float) / np.linalg.norm(normal)
        assert np.degrees(np.arccos(abs((rotation @ true)[2]))) < 8.0

    def test_a_rotation_preserves_the_molecule(self) -> None:
        top, points = _barrel(normal=(0.5, -0.4, 0.77))
        moved = np.asarray(place_for_membrane(top, points, orient=True).positions)
        before = np.linalg.norm(points[:, None] - points[None, :50], axis=-1)
        after = np.linalg.norm(moved[:, None] - moved[None, :50], axis=-1)
        assert np.allclose(before, after, atol=1e-6)


class TestASolubleProteinIsNotEmbedded:

    def test_it_is_refused(self) -> None:
        top, points = _globule()
        with pytest.raises(StudyError) as refused:
            place_for_membrane(top, points, orient=True)
        assert refused.value.code == "setup.membrane.no_belt"
        assert "does not look like a membrane protein" in str(refused.value)

    def test_unless_its_frame_is_stated(self, caplog) -> None:
        top, points = _globule()
        placed = place_for_membrane(top, points, orientation_checked=True)
        assert "is a guess" in caplog.text
        assert placed.record["placed_by"].startswith("stated orientation")


def test_a_quantity_goes_in_and_a_quantity_comes_out() -> None:
    pytest.importorskip("openmm")
    from openmm import Vec3, unit

    top, points = _barrel()
    quantity = unit.Quantity([Vec3(*p) for p in points], unit.nanometer)
    placed = place_for_membrane(top, quantity, orientation_checked=True)
    assert unit.is_quantity(placed.positions)


class TestAnOPMFileIsRecognised:

    def _opm(self, tmp_path: Path) -> Path:
        path = tmp_path / "input.pdb"
        path.write_text(
            "REMARK      1/2 of bilayer thickness:   15.9\n"
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
            "HETATM    2  N   DUM     2     -42.000  -4.000 -15.900\n"
            "HETATM    3  O   DUM     3     -42.000  -4.000  15.900\n"
            "CONECT    2    3\n"
            "END\n", encoding="utf-8")
        return path

    def test_its_markers_are_taken_out_and_its_thickness_read(self, tmp_path) -> None:
        from fastmdxplora.setup.pipeline import _take_out_opm_markers

        path = self._opm(tmp_path)
        found = _take_out_opm_markers(path)
        assert found == {"markers": 2, "half_thickness_nm": pytest.approx(1.59)}
        text = path.read_text(encoding="utf-8")
        assert "DUM" not in text and "CONECT" not in text and " ALA " in text

    def test_another_file_is_left_alone(self, tmp_path) -> None:
        from fastmdxplora.setup.pipeline import _take_out_opm_markers

        path = tmp_path / "input.pdb"
        text = "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\nEND\n"
        path.write_text(text, encoding="utf-8")
        assert _take_out_opm_markers(path) is None
        assert path.read_text(encoding="utf-8") == text


class TestPreparationPlacesThenBuilds:

    def test_the_bilayer_is_built_at_zero_around_the_placed_protein(self, tmp_path, monkeypatch) -> None:
        from tests.test_membrane import _prepared

        built = _prepared(tmp_path, monkeypatch, membrane="DMPC", membrane_orient=True)
        assert built.placed_by == "addMembrane"
        assert built.checked == ["place_for_membrane", "check_chains_point_the_same_way"]
        assert built.asked["placement"]["orient"] is True
        assert built.asked["placement"]["lipid"] == "DMPC"
        centre = built.asked["addMembrane"]["membraneCenterZ"]
        assert centre.value_in_unit(centre.unit) == 0.0

    @pytest.mark.parametrize("stated", [{"membrane_orientation_checked": True},
                                        {"membrane_frame": "opm"}])
    def test_a_stated_or_opm_frame_is_not_second_guessed(self, tmp_path, monkeypatch, stated) -> None:
        from tests.test_membrane import _prepared

        built = _prepared(tmp_path, monkeypatch, membrane="POPC", **stated)
        assert built.checked == ["place_for_membrane"]

    def test_a_refused_placement_builds_nothing(self, tmp_path, monkeypatch) -> None:
        from tests.test_membrane import _prepared

        with pytest.raises(StudyError):
            _prepared(tmp_path, monkeypatch, membrane="POPC",
                      failing="place_for_membrane")

    def test_water_is_not_placed_for_a_bilayer(self, tmp_path, monkeypatch) -> None:
        from tests.test_membrane import _prepared

        built = _prepared(tmp_path, monkeypatch)
        assert built.placed_by == "addSolvent" and built.checked == []


def test_the_built_bilayer_is_counted_by_leaflet() -> None:
    """From OpenMM's own DMPC patch, moved so its centre is at z = 0."""
    openmm = pytest.importorskip("openmm")
    from openmm import app, unit

    from fastmdxplora.setup.prepare import _bilayer_built

    patch = app.PDBFile(str(Path(app.__file__).parent / "data" / "DMPC.pdb"))
    modeller = app.Modeller(patch.topology, patch.positions)
    xyz = np.asarray(modeller.positions.value_in_unit(unit.nanometer))
    lipid = [a.index for a in modeller.topology.atoms() if a.residue.name == "DMP"]
    xyz[:, 2] -= xyz[lipid, 2].mean()
    modeller.positions = unit.Quantity([openmm.Vec3(*p) for p in xyz], unit.nanometer)
    record = _bilayer_built(modeller, unit)
    upper, lower = record["lipids_per_leaflet"]
    assert upper + lower == record["lipids"] > 0
    assert abs(upper - lower) <= 2
    assert record["box_area_per_lipid_nm2"] == pytest.approx(
        record["box_nm"][0] * record["box_nm"][1] / (record["lipids"] / 2), rel=1e-3)
    json.dumps(record)


class TestTheMethodsSayHowItWasBuilt:
    """A membrane system's methods said it was solvated in a dodecahedron and
    coupled isotropically, which is neither what was built nor what ran."""

    @staticmethod
    def _methods(bilayer, barostat="membrane", tmp_path=Path(".")) -> str:
        from fastmdxplora.report.methods import methods_paragraphs

        setup = {"parameters": {"membrane": bilayer["lipid"], "box_shape": "dodecahedron",
                                "solvent_padding_nm": 1.0, "ion_concentration_M": 0.15,
                                "forcefield": "amber14"},
                 "bilayer": bilayer}
        sim = {"parameters": {"temperature_K": 310.0, "pressure_bar": 1.0,
                              "npt_steps": 500000, "production_steps": 5000000,
                              "timestep_fs": 2.0},
               "barostat": barostat}
        return methods_paragraphs(tmp_path, setup, sim, system_name="1AFO")

    def test_an_opm_placement_cites_opm_s_thickness(self, tmp_path) -> None:
        text = self._methods({"lipid": "DMPC", "placed_by": "OPM frame",
                              "hydrophobic_thickness_nm": 3.0,
                              "opm_hydrophobic_thickness_nm": 3.2, "lipids": 114,
                              "lipids_per_leaflet": [57, 57]}, tmp_path=tmp_path)
        assert "published by the OPM database" in text
        assert "hydrophobic thickness 3.2 nm in OPM" in text
        assert "57 and 57 per leaflet" in text
        assert "dodecahedron" not in text
        assert "membrane barostat" in text

    def test_a_fitted_placement_says_it_was_fitted(self, tmp_path) -> None:
        text = self._methods({"lipid": "POPC", "placed_by": "fitted orientation and centre",
                              "hydrophobic_thickness_nm": 3.1, "lipids": 128,
                              "lipids_per_leaflet": [64, 64]}, tmp_path=tmp_path)
        assert "fitted to its apolar lipid-facing surface" in text
        assert "fitted hydrophobic thickness 3.1 nm" in text


def test_the_setup_record_carries_opm_s_thickness() -> None:
    from fastmdxplora.setup.pipeline import _with_opm_s_thickness

    placed = {"placed_by": "OPM frame"}
    assert _with_opm_s_thickness(placed, {"markers": 2, "half_thickness_nm": 1.59}) == {
        "placed_by": "OPM frame", "opm_hydrophobic_thickness_nm": 3.18}
    assert _with_opm_s_thickness(placed, None) is placed
    assert _with_opm_s_thickness(None, {"half_thickness_nm": 1.5}) is None


class TestTheLipidsHaveAForceField:

    @pytest.mark.parametrize("files, expected", [
        (["amber14-all.xml", "amber14/tip3p.xml"], "amber14-all.xml"),
        (["charmm36.xml", "charmm36/water.xml"], "charmm36.xml"),
        (["amber14/protein.ff14SB.xml"], None),
    ])
    def test_the_file_that_describes_them_is_found(self, files, expected) -> None:
        pytest.importorskip("openmm")
        from fastmdxplora.setup.membrane import lipid_parameter_file

        assert lipid_parameter_file(files, "DMPC") == expected

    def test_the_methods_name_the_lipid_force_field(self, tmp_path) -> None:
        text = TestTheMethodsSayHowItWasBuilt._methods(
            {"lipid": "POPC", "placed_by": "fitted orientation and centre",
             "hydrophobic_thickness_nm": 3.1, "lipids": 128,
             "lipids_per_leaflet": [64, 64], "lipid_parameters": "amber14-all.xml"},
            tmp_path=tmp_path)
        assert "described by AMBER Lipid17 (amber14-all.xml)" in text


class TestABilayerIsAdvisedOnBeforeItRuns:
    """What a membrane run gets wrong before it starts: a lipid in its gel
    phase's range, and too little constant pressure for its area to settle."""

    @staticmethod
    def _said(**settings):
        from fastmdxplora.advisories import advise

        return {a.summary: a for a in advise({}, settings)}

    def test_dppc_at_the_default_temperature_is_below_its_transition(self) -> None:
        said = self._said(membrane="DPPC", temperature_K=300.0, npt_duration_ns=5.0)
        (summary, advisory), = said.items()
        assert "below its main transition" in summary and "314 K" in summary
        assert advisory.setting == "temperature_K"
        assert "320" in advisory.remedy

    def test_just_above_is_said_to_be_close(self) -> None:
        said = self._said(membrane="DMPC", temperature_K=300.0, npt_duration_ns=5.0)
        (summary,) = said
        assert "3 K above its main transition" in summary

    def test_a_fluid_bilayer_equilibrated_long_enough_says_nothing(self) -> None:
        assert self._said(membrane="POPC", temperature_K=310.0, npt_duration_ns=5.0) == {}
        assert self._said(membrane="DMPC", temperature_K=310.0) == {}

    def test_a_short_npt_is_said_to_be_short(self) -> None:
        said = self._said(membrane="POPC", temperature_K=310.0, npt_steps=50_000,
                          timestep_fs=2.0)
        (summary, advisory), = said.items()
        assert "0.1 ns of NPT" in summary
        assert advisory.setting == "npt_duration_ns"

    def test_no_membrane_no_advice_about_one(self) -> None:
        said = " ".join(self._said(temperature_K=280.0, npt_steps=1000))
        assert "bilayer" not in said and "transition" not in said

    def test_a_membrane_box_is_not_judged_as_a_dodecahedron(self) -> None:
        """The box is whole patches about 6 nm across, not solute plus padding."""
        from fastmdxplora.advisories import advise

        structure = {"extents_angstrom": [10.0, 10.0, 10.0]}
        settings = {"temperature_K": 310.0, "solvent_padding_nm": 0.5,
                    "npt_duration_ns": 5.0, "box_shape": "dodecahedron"}
        assert advise(structure, settings), "the water box would be advised on"
        assert advise(structure, {**settings, "membrane": "POPC"}) == []


def test_a_four_site_water_is_refused_in_words(tmp_path, monkeypatch) -> None:
    """OpenMM's patches carry three-site water, so a four-site model failed
    inside the packing with OpenMM's advice to call a Modeller method."""
    pytest.importorskip("openmm")
    from openmm.app import Modeller

    from fastmdxplora.setup import membrane
    from fastmdxplora.setup.prepare import prepare_system
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    monkeypatch.setattr(membrane, "place_for_membrane", lambda t, p, **k: membrane.Placement(
        positions=p, record={"lipid": "POPC", "placed_by": "a stand-in"}))
    monkeypatch.setattr(membrane, "check_chains_point_the_same_way", lambda *a, **k: None)

    def packing(self, *args, **kwargs):
        raise ValueError(
            "No template found for residue 114 (HOH).  The set of heavy atoms "
            "matches HOH, but the residue is missing 1 extra site.  You may be "
            "able to add it with Modeller.addExtraParticles().")

    monkeypatch.setattr(Modeller, "addMembrane", packing)
    structure = tmp_path / "peptide.pdb"
    structure.write_text(TRI_ALANINE, encoding="utf-8")
    with pytest.raises(StudyError) as refused:
        prepare_system(prepared_pdb=str(structure), output_dir=str(tmp_path / "setup"),
                       force_field=["amber14-all.xml", "amber14/tip4pew.xml"],
                       membrane="POPC")
    assert refused.value.code == "config.option.conflicting"
    assert "three-site water" in str(refused.value)
    assert "addExtraParticles" not in str(refused.value)


def test_a_real_bilayer_is_built_round_an_opm_file(tmp_path) -> None:
    """Setup for real, on a tripeptide given as an OPM file: the markers are
    taken out, OPM's frame is kept, OpenMM packs a DMPC bilayer round it, and
    the record says so. The rest of this file stops before the packing."""
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    from fastmdxplora import FastMDXplora
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    atoms = [line for line in TRI_ALANINE.splitlines() if line.startswith(("ATOM", "HETATM"))]
    opm = tmp_path / "tri_opm.pdb"
    opm.write_text("REMARK      1/2 of bilayer thickness:   13.0\n" + "\n".join(atoms) + "\n"
                   "HETATM 9001  N   DUM  9001      20.000  20.000 -13.000\n"
                   "HETATM 9002  O   DUM  9002      20.000  20.000  13.000\nEND\n",
                   encoding="utf-8")
    study = FastMDXplora(system=str(opm), output_dir=str(tmp_path / "run"),
                         options={"setup": {"membrane": "DMPC", "forcefield": "amber14"}})
    results = study.explore(include_phase=["setup"])
    assert results[0].status == "ok", results[0].message
    record = json.loads((tmp_path / "run" / "setup" / "setup_parameters.json")
                        .read_text(encoding="utf-8"))
    bilayer = record["bilayer"]
    assert bilayer["placed_by"] == "OPM frame"
    assert bilayer["opm_hydrophobic_thickness_nm"] == pytest.approx(2.6)
    assert bilayer["lipids"] == sum(bilayer["lipids_per_leaflet"]) > 20
    assert bilayer["lipid_parameters"] == "amber14-all.xml"
    assert "DUM" not in (tmp_path / "run" / "setup" / "input.pdb").read_text(encoding="utf-8")
