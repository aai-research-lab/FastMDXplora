"""GIST, held against what is known of its parts and of an ion in water."""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
openmm = pytest.importorskip("openmm")
from openmm import app, unit  # noqa: E402

KJ = unit.kilojoule_per_mole


def test_orientations_at_random_have_no_orientational_entropy():
    from scipy.spatial.transform import Rotation

    from fastmdxplora.analysis.gist import orientational_entropy

    found = [orientational_entropy(Rotation.random(1000, random_state=seed)
                                   .as_quat()[:, [3, 0, 1, 2]]) for seed in range(10)]
    assert abs(np.mean(found)) < 0.05


@pytest.mark.parametrize("spread", [0.1, 0.3])
def test_ordered_orientations_have_the_entropy_of_their_spread(spread):
    """Rotations spread as a narrow Gaussian of rotation vectors: entropy
    against random is 3/2 ln(2 pi e s^2) - ln(8 pi^2), the Gaussian's over
    the volume of the rotation group."""
    from scipy.spatial.transform import Rotation

    from fastmdxplora.analysis.gist import orientational_entropy

    turns = Rotation.from_rotvec(np.random.default_rng(1).normal(0, spread, (2000, 3)))
    expected = 1.5 * np.log(2 * np.pi * np.e * spread ** 2) - np.log(8 * np.pi ** 2)
    assert orientational_entropy(turns.as_quat()[:, [3, 0, 1, 2]]) == pytest.approx(
        expected, abs=0.15)


@pytest.fixture(scope="module")
def ion_in_water(tmp_path_factory):
    """A sodium ion held still in a box of TIP3P water, 40 ps of it."""
    root = tmp_path_factory.mktemp("ion") / "study"
    (root / "setup").mkdir(parents=True)
    topology = app.Topology()
    chain = topology.addChain()
    topology.addAtom("NA", app.element.sodium, topology.addResidue("NA", chain))
    model = app.Modeller(topology, [openmm.Vec3(1.3, 1.3, 1.3)] * unit.nanometer)
    field = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")
    model.addSolvent(field, boxSize=openmm.Vec3(2.6, 2.6, 2.6) * unit.nanometer,
                     neutralize=False)
    system = field.createSystem(model.topology, nonbondedMethod=app.PME,
                                nonbondedCutoff=1.0 * unit.nanometer, constraints=app.HBonds)
    system.setParticleMass(0, 0.0)
    (root / "setup" / "system.xml").write_text(openmm.XmlSerializer.serialize(system))
    with open(root / "setup" / "topology.pdb", "w") as handle:
        app.PDBFile.writeFile(model.topology, model.positions, handle)
    integrator = openmm.LangevinMiddleIntegrator(300 * unit.kelvin, 1 / unit.picosecond,
                                                 0.002 * unit.picoseconds)
    integrator.setRandomNumberSeed(3)
    simulation = app.Simulation(model.topology, system, integrator,
                                openmm.Platform.getPlatformByName("CPU"))
    simulation.context.setPositions(model.positions)
    simulation.minimizeEnergy(maxIterations=200)
    simulation.context.setVelocitiesToTemperature(300 * unit.kelvin, 3)
    simulation.step(2000)
    xyz, boxes = [], []
    for _ in range(100):
        simulation.step(100)
        state = simulation.context.getState(getPositions=True)
        xyz.append(state.getPositions(asNumpy=True).value_in_unit(unit.nanometer))
        boxes.append(state.getPeriodicBoxVectors(asNumpy=True).value_in_unit(unit.nanometer))
    frames = md.Trajectory(np.array(xyz, dtype=np.float32),
                           md.Topology.from_openmm(model.topology),
                           time=np.arange(100) * 0.2)
    frames.unitcell_vectors = np.array(boxes, dtype=np.float32)
    return root, frames, system


def _run(root, frames, out, **options):
    from fastmdxplora.analysis.gist import GIST

    analysis = GIST(output_dir=out, **{"center_selection": "resname NA", "size_nm": 1.2,
                                       "voxel_nm": 0.1, "min_waters": 10, **options})
    analysis.study_dir = root
    return analysis, analysis.run(frames)


def test_an_ion_and_a_water_interact_as_openmm_says(ion_in_water):
    """One water near the ion, its energy with the ion from OpenMM, the
    system cut down to the two."""
    from fastmdxplora.analysis.gist import pair_energies

    root, frames, system = ion_in_water
    xyz = frames.xyz[0].astype(np.float64)
    oxygens = frames.topology.select("water and name O")
    nearest = oxygens[np.argmin(np.linalg.norm(xyz[oxygens] - xyz[0], axis=1))]
    molecule = np.array([nearest, nearest + 1, nearest + 2])
    force = next(f for f in system.getForces() if isinstance(f, openmm.NonbondedForce))
    params = np.array([[v._value for v in force.getParticleParameters(i)]
                       for i in range(system.getNumParticles())])
    mine = pair_energies(xyz, None, molecule[None], np.array([0]), params[:, 0],
                         params[:, 1], params[:, 2], 1.0)[0]
    two = openmm.System()
    pair = openmm.NonbondedForce()
    pair.setNonbondedMethod(openmm.NonbondedForce.NoCutoff)
    for index in [0, *molecule]:
        two.addParticle(1.0)
        pair.addParticle(*force.getParticleParameters(int(index)))
    for a in (1, 2, 3):
        for b in (1, 2, 3):
            if a < b:
                pair.addException(a, b, 0.0, 1.0, 0.0)
    two.addForce(pair)
    context = openmm.Context(two, openmm.VerletIntegrator(0.001),
                             openmm.Platform.getPlatformByName("Reference"))
    context.setPositions(xyz[[0, *molecule]])
    expected = context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(KJ)
    assert mine == pytest.approx(expected, abs=1e-6)
    assert mine < -50  # a first-shell water is held hard by a sodium


def test_an_ion_s_first_shell_is_dense_held_and_ordered(ion_in_water, tmp_path):
    root, frames, _ = ion_in_water
    analysis, result = _run(root, frames, tmp_path)
    assert result.status == "ok", result.message
    table = result.data
    near = np.linalg.norm(table[["x_nm", "y_nm", "z_nm"]].to_numpy() - frames.xyz[0, 0], axis=1)
    shell = table[near < 0.3]
    beyond = table[(near > 0.45) & (near < 0.6)]
    # Water crowds round the ion, held by it, and ordered: a loss of entropy.
    assert shell["g"].max() > 2.0
    assert shell["dE_sw_dens"].sum() < 0
    assert shell["TdS_orient_dens"].sum() > 0
    assert abs(beyond["g"].mean() - 1.0) < 0.6
    totals = analysis.findings["totals_kj_per_mol"]
    assert totals["dE_sw"] < -100 and totals["minus_TdS_orient"] > 0
    bulk = analysis.findings["bulk"]
    # TIP3P's own density, near 33 waters a nm^3, and half its energy near -41 kJ/mol.
    assert 28 < bulk["density_per_nm3"] < 38
    assert -48 < bulk["e_ww_kj_per_mol"] < -36
    assert (tmp_path / "gist" / "gist_dA.dx").is_file()


def test_frames_without_every_atom_are_refused(ion_in_water, tmp_path):
    root, frames, _ = ion_in_water
    _, result = _run(root, frames.atom_slice(np.arange(frames.n_atoms - 3)), tmp_path)
    assert result.status == "error" and "save_selection: all" in result.message


@pytest.mark.parametrize("options, said", [({"size_nm": 9.0}, "`size_nm` is from"),
                                           ({"min_waters": 1}, "at least 2")])
def test_options_that_mean_nothing_are_refused(options, said, tmp_path):
    from fastmdxplora.analysis.gist import GIST
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError, match=said):
        GIST(output_dir=tmp_path, **options)
