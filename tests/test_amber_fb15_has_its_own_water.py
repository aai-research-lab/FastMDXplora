"""AMBER-FB15 runs with TIP3P-FB, the water it was developed with.

The registry paired it with plain TIP3P. AMBER-FB15 (Wang et al., J Phys
Chem B 121, 4023, 2017) was fitted beside TIP3P-FB, whose charges and
Lennard-Jones are refitted (O -0.848 e against TIP3P's -0.834), and OpenMM
ships it as `tip3pfb.xml`. Modeller has no box of that name, so the box is
its 3-site `tip3p` one, parameterised by the file; the records and the
Methods name the water TIP3P-FB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.setup.forcefields import resolve_forcefield


def test_the_registry_pairs_it_with_tip3p_fb():
    choice = resolve_forcefield("amber-fb15")
    assert choice.xmls == ("amberfb15.xml", "tip3pfb.xml")
    assert choice.water_model == "tip3p"
    assert choice.water_name == "TIP3P-FB"


def test_a_box_built_so_carries_tip3p_fb_s_parameters():
    omm = pytest.importorskip("openmm")
    from openmm import app, unit

    choice = resolve_forcefield("amber-fb15")
    field = app.ForceField(*choice.xmls)
    modeller = app.Modeller(app.Topology(), [])
    modeller.addSolvent(field, model=choice.water_model,
                        boxSize=omm.Vec3(2.2, 2.2, 2.2) * unit.nanometer,
                        ionicStrength=0.15 * unit.molar)
    system = field.createSystem(modeller.topology, nonbondedMethod=app.PME,
                                nonbondedCutoff=1.0 * unit.nanometer,
                                constraints=app.HBonds)
    nonbonded = next(f for f in system.getForces() if isinstance(f, omm.NonbondedForce))
    oxygen = next(a.index for a in modeller.topology.atoms()
                  if a.residue.name == "HOH" and a.name == "O")
    charge, sigma, epsilon = nonbonded.getParticleParameters(oxygen)
    assert charge.value_in_unit(unit.elementary_charge) == pytest.approx(-0.848448690103)
    assert sigma.value_in_unit(unit.nanometer) == pytest.approx(0.317796456355)
    assert {r.name for r in modeller.topology.residues()} >= {"HOH", "NA", "CL"}


def test_the_methods_name_the_water():
    from fastmdxplora.report.methods import methods_paragraphs

    text = methods_paragraphs(Path("."), {
        "parameters": {"forcefield": "amber-fb15"},
        "resolved_forcefield": {"name": "amber-fb15",
                                "xmls": ["amberfb15.xml", "tip3pfb.xml"],
                                "water_model": "tip3p", "water_name": "TIP3P-FB"}}, {})
    assert "with the TIP3P-FB water model" in text


def test_a_record_written_before_reads_as_before():
    from fastmdxplora.report.methods import methods_paragraphs

    text = methods_paragraphs(Path("."), {
        "parameters": {"forcefield": "amber-fb15"},
        "resolved_forcefield": {"name": "amber-fb15",
                                "xmls": ["amberfb15.xml", "tip3p.xml"],
                                "water_model": "tip3p"}}, {})
    assert "with the tip3p water model" in text


class TestAStudyPreparedBeforeKeepsItsWater:
    """A study's record holds the name and the files it became. One prepared
    with plain TIP3P, carried on, keeps it: refused before, as "not both",
    or with the name deciding, it would have changed water mid-study."""

    def test_its_files_are_what_the_name_was(self):
        from fastmdxplora.setup.pipeline import _the_list_is_the_name, _the_list_was_the_name

        before = ["amberfb15.xml", "tip3p.xml"]
        assert _the_list_was_the_name("amber-fb15", before)
        assert not _the_list_is_the_name("amber-fb15", before)
        assert not _the_list_was_the_name("amber14", before)
        assert not _the_list_was_the_name("amber-fb15", ["amberfb15.xml", "tip3pfb.xml"])

    @staticmethod
    def _setup(tmp_path, **options):
        import json
        from unittest.mock import MagicMock, patch

        from fastmdxplora.setup import prepare as _prepare_mod
        from fastmdxplora.setup.pipeline import run as setup_run
        from tests.test_forcefield_selector import _MINI_PDB

        pdb = tmp_path / "mini.pdb"
        pdb.write_text(_MINI_PDB)
        orch = MagicMock(system=str(pdb), output_dir=tmp_path / "proj", _presenter=None)
        orch.output_dir.mkdir()
        out = orch.output_dir / "setup"
        out.mkdir()
        # The chemistry is mocked, as in the selector's tests: the record is
        # what is checked, and the fragment cannot be templated where
        # PDBFixer lets preparation go on.
        with patch.object(_prepare_mod, "prepare_system", return_value={}):
            setup_run(orchestrator=orch, output_dir=out, **options)
        # Written whether or not preparation goes on (it stops here without
        # PDBFixer), and it is what replay and the Methods read.
        return json.loads((out / "setup_parameters.json").read_text())

    def test_carried_on_it_is_not_refused_and_keeps_its_files(self, tmp_path):
        record = self._setup(tmp_path, forcefield="amber-fb15",
                             force_field=["amberfb15.xml", "tip3p.xml"])
        assert record["parameters"]["force_field"] == ["amberfb15.xml", "tip3p.xml"]
        assert record["parameters"]["nonbonded_cutoff_nm"] == 1.0
        assert record["parameters"]["use_switching_function"] is False
        assert record["resolved_forcefield"]["xmls"] == ["amberfb15.xml", "tip3p.xml"]
        assert record["resolved_forcefield"]["name"] == "amber-fb15"
        assert "water_name" not in record["resolved_forcefield"]

    def test_a_new_study_replayed_still_names_its_water(self, tmp_path):
        """resolved_config.yml states `water_model: tip3p` and the new list."""
        record = self._setup(tmp_path, forcefield="amber-fb15",
                             force_field=["amberfb15.xml", "tip3pfb.xml"],
                             water_model="tip3p")
        assert record["resolved_forcefield"]["water_name"] == "TIP3P-FB"


def test_the_preview_names_the_water():
    """The Config Builder's preview fills its Water row from `water_model`
    (the box's geometry) and can say the water's own name beside it."""
    from fastmdxplora.gui.preview import _forcefield_said

    said = _forcefield_said({"forcefield": "amber-fb15"})
    assert said["water_model"] == "tip3p" and said["water_name"] == "TIP3P-FB"
    assert _forcefield_said({"forcefield": "amber14"})["water_name"] is None
