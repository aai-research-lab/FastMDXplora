"""Seeding, run against files rather than around them.

`tests/test_seeding.py` covers the arithmetic -- which frame a window takes,
whether a reduction is shortest, whether a refusal fires. It covers none of
the code that opens a trajectory, builds an OpenMM context, or writes a
`state.xml`, because the environment it was written in had no OpenMM. That
was 44% of the diff, and both bugs that reached a real run lived in it: a
distance measured without the minimum-image convention, and a trajectory
holding a selection where a complete set of positions was needed.

So this builds a small system, pulls a ligand across it, and seeds windows
from the result -- the whole path, on real files, in about a second.

Skipped where OpenMM is absent. The environment `environment.yml` describes
has it, and the corpus job runs there.
"""

from __future__ import annotations

import numpy as np
import pytest

openmm = pytest.importorskip("openmm")
md = pytest.importorskip("mdtraj")

from openmm import unit  # noqa: E402

#: Small enough to build in milliseconds, big enough that a centre of mass
#: is a centre of mass.
SITE_ATOMS = 9
LIGAND_ATOMS = 3
BOX_NM = 6.0


def _topology():
    """A `site` residue and a `LIG` residue, bonded within each."""
    top = md.Topology()
    chain = top.add_chain()
    site = top.add_residue("ALA", chain, resSeq=189)
    previous = None
    for index in range(SITE_ATOMS):
        atom = top.add_atom("CA" if index == 0 else f"C{index}",
                            md.element.carbon, site)
        if previous is not None:
            top.add_bond(previous, atom)
        previous = atom

    ligand_chain = top.add_chain()
    ligand = top.add_residue("LIG", ligand_chain, resSeq=900)
    previous = None
    for index in range(LIGAND_ATOMS):
        atom = top.add_atom(f"C{index}", md.element.carbon, ligand)
        if previous is not None:
            top.add_bond(previous, atom)
        previous = atom
    return top


def _system(n_atoms: int):
    """Particles with a soft nonbonded force, so an energy exists."""
    system = openmm.System()
    force = openmm.NonbondedForce()
    force.setNonbondedMethod(openmm.NonbondedForce.CutoffPeriodic)
    force.setCutoffDistance(1.0 * unit.nanometer)
    for _ in range(n_atoms):
        system.addParticle(12.0 * unit.amu)
        force.addParticle(0.0, 0.3 * unit.nanometer,
                          0.1 * unit.kilojoule_per_mole)
    system.addForce(force)
    system.setDefaultPeriodicBoxVectors(
        openmm.Vec3(BOX_NM, 0, 0) * unit.nanometer,
        openmm.Vec3(0, BOX_NM, 0) * unit.nanometer,
        openmm.Vec3(0, 0, BOX_NM) * unit.nanometer)
    return system


def _positions(separation_nm: float, top) -> np.ndarray:
    """A site plane, and the ligand `separation` away along the normal.

    The site is a 3x3 grid in y-z at x = 2, with the CA at its centre; the
    ligand sits on the x axis through that centre, its own atoms spread in z
    and symmetric about it. Laid out along one line instead -- the first
    attempt -- the ligand walks *through* the site, and at 1.4 nm one of its
    atoms lands exactly on top of a site atom: an energy of 1e25 kJ/mol,
    which the seed guard refused, correctly, about a fixture rather than
    about the code.
    """
    xyz = np.zeros((top.n_atoms, 3), dtype=np.float32)
    site = top.select("resname ALA")
    ligand = top.select("resname LIG")

    centre = np.array([2.0, 3.0, 3.0], dtype=np.float32)
    offsets = [(0.0, 0.0)] + [(0.35 * dy, 0.35 * dz)
                              for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                              if (dy, dz) != (0, 0)]
    for index, (dy, dz) in zip(site, offsets):
        xyz[index] = centre + np.array([0.0, dy, dz], dtype=np.float32)

    for order, index in enumerate(ligand):
        xyz[index] = centre + np.array(
            [separation_nm, 0.0, 0.35 * (order - (LIGAND_ATOMS - 1) / 2)],
            dtype=np.float32)
    return xyz


@pytest.fixture
def a_finished_pull(tmp_path):
    """A prepared system and a pull that crosses it, written to disk."""
    top = _topology()
    system = _system(top.n_atoms)

    prepared = tmp_path / "shared_setup" / "setup"
    prepared.mkdir(parents=True)
    (prepared / "system.xml").write_text(
        openmm.XmlSerializer.serialize(system), encoding="utf-8")

    bound = _positions(0.40, top)
    context = openmm.Context(
        system, openmm.VerletIntegrator(0.001),
        openmm.Platform.getPlatformByName("Reference"))
    context.setPositions(bound * unit.nanometer)
    (prepared / "state.xml").write_text(
        openmm.XmlSerializer.serialize(
            context.getState(getPositions=True, getVelocities=True)),
        encoding="utf-8")
    md.Trajectory(bound[None, :, :], top).save_pdb(
        str(prepared / "topology.pdb"))

    # The pull: the ligand walks from 0.40 to 2.00 nm over 200 frames.
    walk = np.linspace(0.40, 2.00, 200)
    frames = np.stack([_positions(d, top) for d in walk])
    trajectory = md.Trajectory(frames, top)
    trajectory.unitcell_vectors = np.tile(
        np.eye(3, dtype=np.float32) * BOX_NM, (len(walk), 1, 1))

    pull = tmp_path / "seed_pull" / "simulation"
    pull.mkdir(parents=True)
    trajectory.save_dcd(str(pull / "production.dcd"))
    trajectory[0].save_pdb(str(pull / "trajectory_topology.pdb"))

    # PLUMED's own record of the same quantity, on a finer stride.
    fine = np.linspace(0.40, 2.00, 1000)
    (pull / "COLVAR").write_text(
        "#! FIELDS time cv restraint.bias\n"
        + "".join(f" {0.2 * i:.6f} {v:.6f} 0.0\n" for i, v in enumerate(fine)),
        encoding="utf-8")

    return tmp_path, prepared, pull.parent


def test_a_window_is_seeded_from_the_frame_nearest_its_centre(a_finished_pull):
    """The whole path: read, measure, check, image, write, verify.

    Every file this touches is one a real study produces, and every line it
    runs is one no other test reaches.
    """
    from fastmdxplora.simulation.seeding import seed_windows

    root, prepared, pull = a_finished_pull
    centres = [0.40, 1.00, 1.80]

    seeds = seed_windows(
        pull, prepared, centres, root / "seeds",
        ligand_resname="LIG", site_selection="resname ALA and name CA",
        temperature_K=300.0, random_seed=7)

    assert [s.index for s in seeds] == [0, 1, 2]
    for seed, centre in zip(seeds, centres):
        assert seed.measured == pytest.approx(centre, abs=0.02)
        for name in ("system.xml", "state.xml", "topology.pdb"):
            assert (root / "seeds" / f"window-{seed.index:02d}" /
                    name).is_file()


def test_the_written_state_holds_the_positions_it_claims(a_finished_pull):
    """A `state.xml` that loads is not the same as one that is right."""
    from fastmdxplora.simulation.seeding import seed_windows

    root, prepared, pull = a_finished_pull

    seeds = seed_windows(
        pull, prepared, [1.40], root / "seeds",
        ligand_resname="LIG", site_selection="resname ALA and name CA")

    state = openmm.XmlSerializer.deserialize(
        (root / "seeds" / "window-00" / "state.xml").read_text())
    positions = state.getPositions(asNumpy=True).value_in_unit(unit.nanometer)

    top = md.load(str(prepared / "topology.pdb")).topology
    site = positions[top.select("resname ALA and name CA")].mean(axis=0)
    ligand = positions[top.select("resname LIG")].mean(axis=0)

    assert float(np.linalg.norm(ligand - site)) == pytest.approx(
        seeds[0].measured, abs=1e-4)
    assert seeds[0].measured == pytest.approx(1.40, abs=0.02)


def test_velocities_are_drawn_fresh_rather_than_carried(a_finished_pull):
    """A pulled frame's velocities are those of a system being dragged.

    Starting equilibrium sampling from them biases the first picoseconds in
    the direction of the pull, which is the one direction that matters here.
    """
    from fastmdxplora.simulation.seeding import seed_windows

    root, prepared, pull = a_finished_pull

    seed_windows(pull, prepared, [1.00], root / "seeds",
                 ligand_resname="LIG",
                 site_selection="resname ALA and name CA",
                 temperature_K=300.0, random_seed=3)

    state = openmm.XmlSerializer.deserialize(
        (root / "seeds" / "window-00" / "state.xml").read_text())
    speeds = np.linalg.norm(
        state.getVelocities(asNumpy=True).value_in_unit(
            unit.nanometer / unit.picosecond), axis=1)

    # The pull wrote none, and Maxwell-Boltzmann at 300 K gives every atom
    # some. Zero here would mean the seed inherited a static frame.
    assert speeds.min() > 0.0
    assert 0.1 < float(speeds.mean()) < 10.0


def test_a_trajectory_missing_its_solvent_is_refused(a_finished_pull):
    """The failure a real pull hit: frames holding a selection.

    `save_selection` leaves the water out by default, so the trajectory has
    fewer particles than the system. A seed is a complete set of positions
    and cannot be built from a subset.
    """
    from fastmdxplora.simulation.seeding import seed_windows

    root, prepared, pull = a_finished_pull

    # A system with more particles than the pull recorded, as a solvated
    # one is against a solute-only trajectory.
    bigger = _system(_topology().n_atoms + 500)
    (prepared / "system.xml").write_text(
        openmm.XmlSerializer.serialize(bigger), encoding="utf-8")

    with pytest.raises(ValueError, match="save_selection"):
        seed_windows(pull, prepared, [1.00], root / "seeds",
                     ligand_resname="LIG",
                     site_selection="resname ALA and name CA")


def test_a_pull_the_selections_do_not_match_is_refused(a_finished_pull):
    """Seeding on a variable PLUMED did not bias, caught by its own record."""
    from fastmdxplora.simulation.seeding import seed_windows

    root, prepared, pull = a_finished_pull

    with pytest.raises(ValueError, match="not the ones that were biased"):
        seed_windows(pull, prepared, [1.00], root / "seeds",
                     ligand_resname="LIG",
                     # The whole residue rather than its first atom: a
                     # different point, and a different distance.
                     site_selection="resname LIG")
