"""Molecules are made whole for every frame at once, as MDTraj makes them.

MDTraj's ``image_molecules`` places a second chain by comparing every atom
of it with every atom of the first, frame by frame: about a quarter of a
second a frame for a dimer of 3,341 atoms a chain, so 200 frames of 1AKE
took 51 seconds to make whole before the Viewer could play them, and every
analysis of the study paid the same. ``fastmdxplora.analysis.imaging`` does
MDTraj's imaging over all frames together, with a k-d tree for the closest
contact, and its coordinates are MDTraj's bit for bit: in a cube, a rhombic
dodecahedron and a truncated octahedron, for one chain, two and five, with a
ligand, ions and water, every atom thrown into a random periodic copy.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("scipy")


def _box(kind: str, d: float) -> np.ndarray:
    """Box vectors as rows, in OpenMM's reduced form."""
    if kind == "cube":
        return np.diag([d, d, d])
    if kind == "dodecahedron":
        return np.array([[d, 0, 0], [0, d, 0], [d / 2, d / 2, d * np.sqrt(2) / 2]])
    return np.array([[d, 0, 0], [d / 3, 2 * np.sqrt(2) * d / 3, 0],
                     [-d / 3, np.sqrt(2) * d / 3, np.sqrt(6) * d / 3]])


def _system(chains: int, length: int = 12, waters: int = 60, ions: int = 4):
    """Peptide chains on helices side by side, a bonded ligand, ions, water."""
    topology = md.Topology()
    xyz = []
    for c in range(chains):
        chain = topology.add_chain()
        base = np.array([1.1 * c, 0.4 * (c % 2), 0.0])
        previous = None
        for r in range(length):
            residue = topology.add_residue("ALA", chain, resSeq=r + 1)
            angle = np.radians(100.0 * r)
            centre = base + [0.23 * np.cos(angle), 0.23 * np.sin(angle), 0.15 * r]
            tangent = np.array([-np.sin(angle), np.cos(angle), 0.4])
            tangent /= np.linalg.norm(tangent)
            side = np.cross(tangent, [0.0, 0.0, 1.0])
            side /= np.linalg.norm(side)
            atoms = {}
            for name, element, place in (
                    ("N", md.element.nitrogen, centre - 0.12 * tangent),
                    ("CA", md.element.carbon, centre),
                    ("C", md.element.carbon, centre + 0.12 * tangent),
                    ("O", md.element.oxygen, centre + 0.12 * tangent + 0.12 * side),
                    ("CB", md.element.carbon, centre - 0.15 * side)):
                atoms[name] = topology.add_atom(name, element, residue)
                xyz.append(place)
            topology.add_bond(atoms["N"], atoms["CA"])
            topology.add_bond(atoms["CA"], atoms["C"])
            topology.add_bond(atoms["C"], atoms["O"])
            topology.add_bond(atoms["CA"], atoms["CB"])
            if previous is not None:
                topology.add_bond(previous, atoms["N"])
            previous = atoms["C"]
    ligand = topology.add_residue("LIG", topology.add_chain())
    ring = [topology.add_atom(f"C{i}", md.element.carbon, ligand) for i in range(6)]
    for i in range(6):
        topology.add_bond(ring[i], ring[(i + 1) % 6])
        xyz.append([1.4 * chains / 2 + 0.14 * np.cos(i * np.pi / 3),
                    1.0 + 0.14 * np.sin(i * np.pi / 3), 0.8])
    rng = np.random.default_rng(7)
    solvent = topology.add_chain()
    for i in range(ions):
        topology.add_atom("NA", md.element.sodium,
                          topology.add_residue("NA", solvent, resSeq=500 + i))
        xyz.append(rng.uniform(-1, 3, 3))
    for i in range(waters):
        residue = topology.add_residue("HOH", solvent, resSeq=600 + i)
        oxygen = topology.add_atom("O", md.element.oxygen, residue)
        for name in ("H1", "H2"):
            topology.add_bond(oxygen, topology.add_atom(name, md.element.hydrogen, residue))
        centre = rng.uniform(-1, 3, 3)
        xyz += [centre, centre + [0.096, 0, 0], centre + [0, 0.096, 0]]
    return topology, np.asarray(xyz, dtype=np.float64)


def _scrambled(topology, xyz, kind: str, d: float, frames: int = 4, seed: int = 0):
    """Rotated a different way in each frame, each atom in a random copy."""
    rng = np.random.default_rng(seed)
    box = _box(kind, d)
    out = np.empty((frames, len(xyz), 3), dtype=np.float32)
    for f in range(frames):
        q = rng.normal(size=4)
        w, x, y, z = q / np.linalg.norm(q)
        rotation = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        moved = (xyz - xyz.mean(axis=0)) @ rotation.T + rng.uniform(0, d, 3)
        moved += rng.integers(-1, 2, (len(xyz), 3)) @ box
        out[f] = moved
    trajectory = md.Trajectory(out, topology)
    trajectory.unitcell_vectors = np.repeat(box[None].astype(np.float32), frames, axis=0)
    return trajectory


def _anchors(topology):
    from fastmdxplora.analysis.residues import _is_polymer

    molecules = topology.find_molecules()
    solute = [m for m in molecules if not all(a.residue.is_water for a in m)]
    return [m for m in solute if any(_is_polymer(a.residue) for a in m)] or solute


@pytest.mark.parametrize("kind", ["cube", "dodecahedron", "octahedron"])
@pytest.mark.parametrize("chains", [1, 2, 5])
def test_the_coordinates_are_mdtrajs_bit_for_bit(kind, chains):
    from fastmdxplora.analysis.imaging import image_trajectory

    topology, xyz = _system(chains)
    # A box only a little larger than the molecules, where a copy two cells
    # away can matter, and a roomy one.
    extent = float(np.ptp(xyz[:-180], axis=0).max())
    for d in (extent + 0.8, extent + 3.0):
        trajectory = _scrambled(topology, xyz, kind, d, seed=chains)
        by_mdtraj = trajectory.image_molecules(inplace=False, anchor_molecules=_anchors(topology))
        imaged = trajectory[:]
        image_trajectory(imaged, _anchors(topology))
        assert np.array_equal(imaged.xyz, by_mdtraj.xyz), (kind, chains, d)
        bonds = np.array([[a.index, b.index] for a, b in topology.bonds])
        assert md.compute_distances(imaged, bonds, periodic=False).max() < 0.3


def test_the_ligand_and_chains_as_anchors_are_mdtrajs_too():
    from fastmdxplora.analysis.imaging import image_trajectory

    topology, xyz = _system(3)
    trajectory = _scrambled(topology, xyz, "dodecahedron", 7.0, seed=11)
    anchors = [m for m in topology.find_molecules()
               if not all(a.residue.is_water or a.residue.name == "NA" for a in m)]
    assert len(anchors) == 4
    by_mdtraj = trajectory.image_molecules(inplace=False, anchor_molecules=anchors)
    image_trajectory(trajectory, anchors)
    assert np.array_equal(trajectory.xyz, by_mdtraj.xyz)


def test_the_loader_makes_molecules_whole_as_it_did():
    """``_made_whole``: MDTraj's imaging, then the solute to its nearest copy."""
    from fastmdxplora.analysis import loading

    topology, xyz = _system(2)
    trajectory = _scrambled(topology, xyz, "octahedron", 6.5, seed=3)
    before = trajectory[:]
    large = _anchors(topology)
    before.image_molecules(inplace=True, anchor_molecules=large)
    others = [m for m in topology.find_molecules()
              if m not in large and not all(a.residue.is_water for a in m)]
    loading._nearest_copies(before, large, others)
    assert np.array_equal(loading._made_whole(trajectory).xyz, before.xyz)


def test_what_cannot_be_imaged_is_refused_with_its_code():
    from fastmdxplora.analysis.imaging import image_molecules
    from fastmdxplora.analysis.loading import TrajectoryLoadError

    topology, xyz = _system(1, waters=0, ions=0)
    trajectory = _scrambled(topology, xyz, "cube", 6.0)
    box, n = trajectory.unitcell_vectors, topology.n_atoms
    bonds = np.array([[a.index, b.index] for a, b in topology.bonds])
    everything = [list(range(n))]
    for args, code, said in (
            ((trajectory.xyz[:, :-1].copy(), box, n, bonds, everything, []),
             "analysis.trajectory.unreadable", "not frames of"),
            ((trajectory.xyz.copy(), box, n, np.empty((0, 2)), everything, []),
             "analysis.system.inapplicable", "no bonds"),
            ((trajectory.xyz.copy(), box, n, bonds, [], everything),
             "analysis.system.inapplicable", "centre the others on")):
        with pytest.raises(TrajectoryLoadError, match=said) as refused:
            image_molecules(*args)
        assert refused.value.refusal.code == code


def test_a_topology_without_bonds_is_left_as_stored(caplog):
    from fastmdxplora.analysis.loading import _made_whole

    topology, xyz = _system(1, waters=0, ions=0)
    trajectory = _scrambled(topology, xyz, "cube", 6.0)
    unbonded = md.Topology.from_dataframe(topology.to_dataframe()[0], bonds=None)
    stored = md.Trajectory(trajectory.xyz.copy(), unbonded,
                           unitcell_lengths=trajectory.unitcell_lengths,
                           unitcell_angles=trajectory.unitcell_angles)
    assert np.array_equal(_made_whole(stored).xyz, trajectory.xyz)


def test_many_anchors_are_left_to_mdtraj(monkeypatch):
    from fastmdxplora.analysis import imaging

    topology, xyz = _system(3)
    trajectory = _scrambled(topology, xyz, "cube", 8.0, frames=2)
    by_mdtraj = trajectory.image_molecules(inplace=False, anchor_molecules=_anchors(topology))
    monkeypatch.setattr(imaging, "MOST_ANCHORS", 2)
    monkeypatch.setattr(imaging, "image_molecules",
                        lambda *a, **k: pytest.fail("placed here, not by MDTraj"))
    imaging.image_trajectory(trajectory, _anchors(topology))
    assert np.array_equal(trajectory.xyz, by_mdtraj.xyz)
