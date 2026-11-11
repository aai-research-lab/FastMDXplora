"""A bilayer of more than one lipid, or with leaflets that differ."""

from __future__ import annotations

import shutil

import pytest

openmm = pytest.importorskip("openmm")
from openmm import app  # noqa: E402


def test_one_ratio_is_both_leaflets():
    from fastmdxplora.setup.membrane_mixture import _arguments, leaflets

    upper, lower = leaflets({"popc": 7, "CHL1": 3})
    assert upper == lower == {"POPC": 7.0, "CHL1": 3.0}
    assert _arguments(upper, lower) == ("POPC:CHL1", "7:3")


def test_leaflets_that_differ_are_built_apart():
    from fastmdxplora.setup.membrane_mixture import _arguments, leaflets

    upper, lower = leaflets({"upper": {"POPC": 7, "CHL1": 3}, "lower": {"POPE": 1}})
    assert _arguments(upper, lower) == ("POPC:CHL1//POPE", "7:3//1")


@pytest.mark.parametrize("composition, said", [
    ({}, "a ratio of lipids"),
    ({"upper": {"POPC": 1}}, "names both leaflets"),
    ({"POPC": 0}, "a positive number"),
    ({"POPC": "lots"}, "a positive number"),
])
def test_compositions_that_mean_nothing_are_refused(composition, said):
    from fastmdxplora.config.loader import ConfigError
    from fastmdxplora.setup.membrane_mixture import leaflets

    with pytest.raises(ConfigError, match=said):
        leaflets(composition)


def test_a_lipid_the_force_field_cannot_describe_is_refused():
    from fastmdxplora.refusals import StudyError
    from fastmdxplora.setup.membrane_mixture import build_patch

    field = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")
    with pytest.raises(StudyError, match="no template for XYZL"):
        build_patch({"POPC": 1, "XYZL": 1}, field)


def test_bonds_come_from_the_force_field_s_template():
    """A water read with no bonds is given its template's."""
    from fastmdxplora.setup.membrane_mixture import _template_bonds

    topology = app.Topology()
    residue = topology.addResidue("HOH", topology.addChain())
    for name, element in (("O", app.element.oxygen), ("H1", app.element.hydrogen),
                          ("H2", app.element.hydrogen)):
        topology.addAtom(name, element, residue)
    assert _template_bonds(topology, app.ForceField("amber14/tip3p.xml")) == 2
    assert {frozenset((a.name, b.name)) for a, b in topology.bonds()} == {
        frozenset(("O", "H1")), frozenset(("O", "H2"))}


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("packmol-memgen") is None, reason="needs AmberTools")
def test_an_asymmetric_patch_is_built_and_parameterised(tmp_path, monkeypatch):
    from openmm import unit

    from fastmdxplora.setup.membrane_mixture import build_patch

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))
    field = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")
    patch, record = build_patch({"upper": {"POPC": 7, "CHL1": 3}, "lower": {"POPE": 1}},
                                field, patch_nm=3.0)
    assert record["asymmetric"] is True and record["equilibrated"] is False
    assert set(record["lipids_in_patch"]) == {"POPC", "CHL1", "POPE"}
    system = field.createSystem(patch.topology, nonbondedMethod=app.PME,
                                nonbondedCutoff=1.0 * unit.nanometer)
    assert system.getNumParticles() == patch.topology.getNumAtoms()
    # Built once, then taken from the cache.
    _, again = build_patch({"upper": {"POPC": 7, "CHL1": 3}, "lower": {"POPE": 1}},
                           field, patch_nm=3.0)
    assert again["cached_as"] == record["cached_as"]
