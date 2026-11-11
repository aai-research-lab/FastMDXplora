"""MM/GBSA, held against OpenMM's own implicit-solvent force field.

Hemoglobin's alpha and beta chains (1HHO) stand in for a receptor and its
ligand: amber14 parameterises both, so the binding energy from the study's
explicit-solvent `system.xml` can be compared term for term with the same
chains built afresh with `implicit/obc2.xml`, all of whose forces are
counted. The bonded terms cancel in both.
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
openmm = pytest.importorskip("openmm")
from openmm import app, unit  # noqa: E402

DATA = Path(__file__).parent / "data" / "assemblies"
FRAMES = 4


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    """A study's setup folder for hemoglobin's first two chains in water, and
    frames of them alone, as `not water` stores them."""
    root = tmp_path_factory.mktemp("hemoglobin")
    pdb = app.PDBFile(io.StringIO(
        gzip.decompress((DATA / "1HHO.pdb.gz").read_bytes()).decode()))
    model = app.Modeller(pdb.topology, pdb.positions)
    model.delete([r for r in model.topology.residues()
                  if r.name in ("HOH", "HEM", "OXY", "PO4")])
    two = list(model.topology.chains())[:2]
    model.delete([c for c in model.topology.chains() if c not in two])
    field = app.ForceField("amber14-all.xml", "amber14/tip3pfb.xml")
    model.addHydrogens(field)
    model.addSolvent(field, padding=0.8 * unit.nanometer)
    system = field.createSystem(model.topology, nonbondedMethod=app.PME,
                                nonbondedCutoff=1.0 * unit.nanometer,
                                constraints=app.HBonds)
    (root / "setup").mkdir()
    (root / "setup" / "system.xml").write_text(openmm.XmlSerializer.serialize(system))
    with open(root / "setup" / "topology.pdb", "w") as handle:
        app.PDBFile.writeFile(model.topology, model.positions, handle, keepIds=True)
    whole = md.load(str(root / "setup" / "topology.pdb"))
    solute = whole.atom_slice(whole.topology.select("protein"))
    rng = np.random.default_rng(0)
    xyz = solute.xyz[0][None] + rng.normal(0, 0.003, (FRAMES,) + solute.xyz[0].shape)
    frames = md.Trajectory(
        xyz.astype(np.float32), solute.topology, time=np.arange(FRAMES) * 10.0,
        unitcell_lengths=np.repeat(whole.unitcell_lengths, FRAMES, axis=0),
        unitcell_angles=np.repeat(whole.unitcell_angles, FRAMES, axis=0))
    return root, frames


def _run(root, frames, out, **options):
    from fastmdxplora.analysis.mmgbsa import MMGBSA

    options = {"ligand_selection": "chainid 1", "receptor_selection": "chainid 0",
               **options}
    analysis = MMGBSA(output_dir=out, **options)
    analysis.study_dir = root
    return analysis, analysis.run(frames)


def _implicit(topology, xyz):
    field = app.ForceField("amber14-all.xml", "implicit/obc2.xml")
    system = field.createSystem(topology.to_openmm(), nonbondedMethod=app.NoCutoff,
                                constraints=None, soluteDielectric=1.0,
                                solventDielectric=78.5)
    context = openmm.Context(system, openmm.VerletIntegrator(0.001),
                             openmm.Platform.getPlatformByName("Reference"))
    context.setPositions(xyz)
    return context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(
        unit.kilojoule_per_mole)


def test_it_is_what_openmm_s_implicit_solvent_gives(study, tmp_path):
    root, frames = study
    analysis, result = _run(root, frames, tmp_path)
    assert result.status == "ok", result.message
    first, second = frames.topology.select("chainid 0"), frames.topology.select("chainid 1")
    both = np.concatenate([first, second])
    for frame in (0, 1):
        xyz = frames.xyz[frame].astype(float)
        expected = (_implicit(frames.topology.subset(both), xyz[both])
                    - _implicit(frames.topology.subset(first), xyz[first])
                    - _implicit(frames.topology.subset(second), xyz[second]))
        assert result.data[frame] == pytest.approx(expected, abs=0.2)


def test_the_parts_add_up_and_are_written(study, tmp_path):
    import pandas as pd

    root, frames = study
    analysis, result = _run(root, frames, tmp_path)
    parts = pd.read_csv(tmp_path / "mmgbsa" / "mmgbsa_parts.dat")
    assert list(parts.columns) == ["elec", "vdw", "gb", "sa", "total"]
    assert np.allclose(parts[["elec", "vdw", "gb", "sa"]].sum(axis=1), parts["total"],
                       atol=1e-3)
    assert np.allclose(parts["total"], result.data, atol=1e-3)
    found = analysis.findings["binding_energy"]
    # Two chains in contact attract, and the solvent pays for it.
    assert found["parts_kj_per_mol"]["vdw"] < 0 < found["parts_kj_per_mol"]["gb"]
    assert "not for comparison with a measured" in found["not_a_free_energy"]


def test_a_partner_stored_a_cell_away_is_placed_back(study, tmp_path):
    root, frames = study
    moved = frames[:2]
    xyz = moved.xyz.copy()
    xyz[1] = xyz[0]
    xyz[1, moved.topology.select("chainid 1")] += moved.unitcell_vectors[1, 0]
    moved.xyz = xyz
    _, result = _run(root, moved, tmp_path)
    assert result.status == "ok", result.message
    # The same frame, but for single precision's rounding a box length away.
    assert result.data[1] == pytest.approx(result.data[0], abs=0.02)


def test_frames_with_atoms_the_system_lacks_are_refused(study, tmp_path):
    root, frames = study
    renamed = frames[:]
    next(iter(renamed.topology.residues)).name = "XYZ"
    _, result = _run(root, renamed, tmp_path, ligand_selection="chainid 1",
                     receptor_selection="chainid 0")
    assert result.status == "error"
    assert "its charge is not known" in result.message


def test_a_force_field_it_cannot_take_apart_is_refused(study, tmp_path):
    root, frames = study
    other = tmp_path / "other"
    (other / "setup").mkdir(parents=True)
    system = openmm.XmlSerializer.deserialize(
        (root / "setup" / "system.xml").read_text())
    extra = openmm.CustomNonbondedForce("0")
    for _ in range(system.getNumParticles()):
        extra.addParticle([])
    system.addForce(extra)
    (other / "setup" / "system.xml").write_text(openmm.XmlSerializer.serialize(system))
    (other / "setup" / "topology.pdb").write_bytes(
        (root / "setup" / "topology.pdb").read_bytes())
    _, result = _run(other, frames, tmp_path)
    assert result.status == "error" and "CustomNonbondedForce" in result.message


@pytest.mark.parametrize("options, said", [
    ({"ligand_selection": ""}, "names no ligand"),
    ({"ligand_selection": "chainid 7"}, "matched 0 atoms"),
    ({"receptor_selection": "protein"}, "share atoms"),
])
def test_partners_that_cannot_be_taken_apart_are_refused(study, tmp_path, options, said):
    root, frames = study
    _, result = _run(root, frames, tmp_path, **options)
    assert result.status == "error" and said in result.message


def test_a_study_without_its_setup_is_refused(study, tmp_path):
    _, frames = study
    _, result = _run(tmp_path, frames, tmp_path / "out")
    assert result.status == "error" and "system.xml" in result.message


def test_it_runs_only_where_a_study_names_it():
    from fastmdxplora.analysis.mmgbsa import MMGBSA

    assert MMGBSA.requires_naming is True and MMGBSA.time_series is True
