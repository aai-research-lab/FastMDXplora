"""Every bilayer OpenMM builds is recognised as a membrane.

OpenMM builds a bilayer by tiling a pre-equilibrated patch, and the lipids
keep the patch's three-character residue names: `DMP` for DMPC, `DOP` for
DOPC, `DPP` for DPPC, `DLP` for DLPC and DLPE, `POP` for POPC and POPE. The
lists that decided whether a system is a membrane held `POP` and the full
names, so a DMPC, DOPC, DPPC, DLPC or DLPE bilayer was coupled with the
isotropic barostat, which squeezes it; the crash diagnosis and the default
bilayer of `membrane_depth` missed them too. Read here from OpenMM's own
patches, so a patch renamed in a later OpenMM fails this rather than a run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.lipids import BUILT_LIPIDS, LIPID_RESIDUE_NAMES, is_lipid

openmm = pytest.importorskip("openmm")
app = pytest.importorskip("openmm.app")


def _patch(lipid: str) -> Path:
    return Path(app.__file__).parent / "data" / f"{lipid}.pdb"


@pytest.mark.parametrize("lipid", sorted(BUILT_LIPIDS))
def test_the_patch_names_its_lipid_as_recorded(lipid: str) -> None:
    names = {r.name for r in app.PDBFile(str(_patch(lipid))).topology.residues()} - {"HOH"}
    assert names == {BUILT_LIPIDS[lipid]}


@pytest.mark.parametrize("lipid", sorted(BUILT_LIPIDS))
def test_a_built_bilayer_is_a_membrane_and_gets_the_membrane_barostat(lipid: str) -> None:
    unit = openmm.unit
    from fastmdxplora.simulation.runner import _add_barostat, is_membrane_system

    topology = app.PDBFile(str(_patch(lipid))).topology
    assert is_membrane_system(topology)
    system = openmm.System()
    system.addParticle(1.0)
    _add_barostat({"openmm": openmm, "unit": unit}, system, temperature_K=300.0,
                  pressure_bar=1.0, frequency=25, membrane=is_membrane_system(topology))
    assert isinstance(system.getForce(0), openmm.MonteCarloMembraneBarostat)


def test_water_and_protein_are_not_lipids() -> None:
    for name in ("HOH", "WAT", "ALA", "LYS", "NA", "CL", "LIG"):
        assert not is_lipid(name)


def test_the_diagnosis_reads_the_same_list() -> None:
    from fastmdxplora.simulation import diagnose

    assert diagnose._LIPIDS is LIPID_RESIDUE_NAMES


@pytest.mark.parametrize("lipid", ["DMPC", "DOPC", "DPPC", "DLPC"])
def test_membrane_depth_finds_the_bilayer_by_default(lipid: str) -> None:
    import mdtraj as md

    from fastmdxplora.simulation.metadynamics import build_plumed_script, plan_from_config

    top = md.Topology()
    chain = top.add_chain()
    ligand = top.add_residue("BNZ", chain, resSeq=1)
    for index in range(6):
        top.add_atom(f"C{index}", md.element.carbon, ligand)
    lipids = top.add_chain()
    for index in range(4):
        residue = top.add_residue(BUILT_LIPIDS[lipid], lipids, resSeq=10 + index)
        for atom in range(3):
            top.add_atom(f"C{atom}", md.element.carbon, residue)
    plan = plan_from_config({"collective_variable": "membrane_depth",
                             "ligand_resname": "BNZ", "sigma": 0.05}, top)
    assert len(plan.atoms["bilayer"]) == 12
    assert "mem: COM" in build_plumed_script(plan)
