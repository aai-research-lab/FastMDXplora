"""A live frame shows a bound ligand in its pocket.

A run's snapshots are written with each molecule wrapped into the box on
its own. A protein near a box face had its ligand, bound in the pocket on
that side, wrapped to the far face, and the Viewer's last frame showed it a
box length away: a ligand that never left looked as if it had. Every reader
of the snapshot is now sent it made whole about the protein, as the
trajectory is: the Viewer's structure, its coordinates, the secondary
structure matched to them, and the frames played while a run is going.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

BOX = 40.0


def _line(serial, name, resname, chain, number, xyz, element, record="ATOM  "):
    shown = name if len(name) == 4 else f" {name:<3}"
    x, y, z = xyz
    return (f"{record}{serial:5d} {shown} {resname:>3} {chain}{number:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}{1.0:6.2f}{0.0:6.2f}          {element:>2}")


def _snapshot(shift: float = 0.0, box: float = BOX, named: float | None = None) -> str:
    """Three alanines against the +x face of a cubic box, and a ligand
    beside them that the snapshot wrapped to the -x face. ``named`` is the
    box its CRYST1 says, where that is not the box it was wrapped in."""
    said = box if named is None else named
    lines = [f"CRYST1{said:9.3f}{said:9.3f}{said:9.3f}{90:7.2f}{90:7.2f}{90:7.2f} P 1           1"]
    serial = 1
    for number in range(1, 4):
        base = box - 2.5 + shift
        for name, element, offset in (("N", "N", (0.0, 0.0, 0.0)), ("CA", "C", (1.0, 1.0, 0.0)),
                                      ("C", "C", (1.5, 0.0, 1.0)), ("O", "O", (2.0, 0.5, 2.0)),
                                      ("CB", "C", (0.5, 2.0, 0.5))):
            xyz = (base + offset[0], 18.0 + 3.0 * number + offset[1], 20.0 + offset[2])
            lines.append(_line(serial, name, "ALA", "A", number, xyz, element))
            serial += 1
    lines.append("TER")
    # Bound 0.3 to 1.7 angstroms beyond the face, beside the protein, and
    # stored wrapped to just inside the opposite one.
    for name, element, x in (("C1", "C", 0.3), ("C2", "C", 1.0), ("N1", "N", 1.7)):
        lines.append(_line(serial, name, "BEN", "B", 1, ((box + x + shift) % box, 24.0, 20.5),
                           element, record="HETATM"))
        serial += 1
    lines.append("END")
    return "\n".join(lines) + "\n"


def _xyz(text: str) -> np.ndarray:
    return np.array([(float(line[30:38]), float(line[38:46]), float(line[46:54]))
                     for line in text.splitlines() if line[:6] in ("ATOM  ", "HETATM")])


def _apart(xyz: np.ndarray) -> float:
    """How far the ligand (the last three atoms) is from the protein."""
    protein, ligand = xyz[:-3], xyz[-3:]
    return float(np.min(np.linalg.norm(protein[:, None] - ligand[None], axis=-1)))


class TestTheLiveFrame(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.root = Path(self._folder.name)
        self.simulation = self.root / "simulation"
        self.simulation.mkdir()
        (self.simulation / "live_frame.pdb").write_text(_snapshot(), encoding="utf-8")

    def tearDown(self):
        self._folder.cleanup()

    def test_the_snapshot_as_written_is_split(self):
        self.assertGreater(_apart(_xyz(_snapshot())), 30.0)

    def test_the_structure_sent_has_the_ligand_beside_the_protein(self):
        from fastmdxplora.gui.live_frames import live_frame_text

        text = live_frame_text(self.simulation)
        self.assertLess(_apart(_xyz(text)), 3.0)
        ligand = _xyz(text)[-3:]
        self.assertLess(float(np.max(np.linalg.norm(ligand[1:] - ligand[:-1], axis=-1))), 2.0)

    def test_only_the_coordinates_change(self):
        from fastmdxplora.gui.live_frames import live_frame_text

        sent = live_frame_text(self.simulation).splitlines()
        written = _snapshot().splitlines()
        self.assertEqual(len(sent), len(written))
        for before, after in zip(written, sent):
            self.assertEqual(before[:30], after[:30])
            self.assertEqual(before[54:], after[54:])

    def test_every_reader_is_sent_the_same_frame(self):
        from fastmdxplora.gui.by_residue import _structure_for
        from fastmdxplora.gui.live_frames import live_frame_coordinates, live_frame_text
        from fastmdxplora.gui.viewed_structure import viewer_structure

        text = live_frame_text(self.simulation)
        data, _ = viewer_structure(self.root, "live", with_solvent=False)
        self.assertEqual(data.decode("utf-8"), text)
        self.assertEqual(_structure_for(self.root, "live")[1], text)
        said = live_frame_coordinates(self.simulation)
        first = next(line for line in text.splitlines() if line[:6] in ("ATOM  ", "HETATM"))
        self.assertEqual(said["fingerprint"], first[30:54])

    def test_a_frame_with_no_box_is_sent_as_written(self):
        from fastmdxplora.gui.live_frames import live_frame_text

        unboxed = "\n".join(line for line in _snapshot().splitlines()
                            if not line.startswith("CRYST1")) + "\n"
        (self.simulation / "live_frame.pdb").write_text(unboxed, encoding="utf-8")
        self.assertEqual(live_frame_text(self.simulation), unboxed)

    def test_a_snapshot_is_written_with_the_box_it_was_wrapped_in(self):
        from fastmdxplora.gui.live_frames import (cell_of, live_frame_text, read_live_frame_index,
                                                  write_openmm_live_frame)

        # PDBFile writes the topology's box, the one the run started with.
        def writer(topology, positions, handle, keepIds=True):
            handle.write(_snapshot(box=34.0, named=BOX))

        said = write_openmm_live_frame(self.simulation, pdbfile_writer=writer, topology=None,
                                       positions=None, frame_index=1, stage="production",
                                       box_vectors=np.diag([3.4, 3.4, 3.4]), archive=False)
        self.assertTrue(said["ok"], said)
        written = (self.simulation / "live_frame.pdb").read_text()
        self.assertEqual(cell_of(written)[:3], (34.0, 34.0, 34.0))
        self.assertEqual(read_live_frame_index(self.simulation)["cell"][:3], [34.0, 34.0, 34.0])
        self.assertLess(_apart(_xyz(live_frame_text(self.simulation))), 3.0)

    def test_an_older_snapshot_is_imaged_in_the_trajectory_s_box(self):
        import mdtraj as md

        from fastmdxplora.gui.live_frames import cell_of, live_frame_text

        # Wrapped in a 34 angstrom box, named the 40 angstrom box the run
        # started in, with no box recorded beside it.
        (self.simulation / "live_frame.pdb").write_text(_snapshot(box=34.0, named=BOX))
        self.assertGreater(_apart(_xyz(live_frame_text(self.simulation))), 3.0)
        n_atoms = len(_xyz(_snapshot()))
        topology = md.Topology()
        residue = topology.add_residue("UNK", topology.add_chain())
        for _ in range(n_atoms):
            topology.add_atom("X", md.element.carbon, residue)
        md.Trajectory(np.zeros((2, n_atoms, 3), dtype=np.float32), topology,
                      unitcell_lengths=np.full((2, 3), 3.4, dtype=np.float32),
                      unitcell_angles=np.full((2, 3), 90.0, dtype=np.float32),
                      ).save_dcd(str(self.simulation / "production.dcd"))
        text = live_frame_text(self.simulation)
        self.assertLess(_apart(_xyz(text)), 3.0)
        self.assertAlmostEqual(cell_of(text)[0], 34.0, places=2)

    def test_the_frames_played_while_a_run_is_going_are_whole(self):
        import mdtraj as md

        from fastmdxplora.gui.live_frames import write_live_frame
        from fastmdxplora.gui.trajectory_frames import FRAMES_FILE, FRAMES_TOPOLOGY, frames_info

        for step, shift in enumerate((0.0, 0.3, 0.6)):
            write_live_frame(self.simulation, pdb_text=_snapshot(shift), frame_index=step,
                             stage="production", simulation_time_ns=0.1 * step, archive=True)
        said = frames_info(self.root)
        self.assertTrue(said["available"], said)
        self.assertTrue(said["made_whole"])
        frames = md.load_dcd(str(self.simulation / FRAMES_FILE),
                             top=str(self.simulation / FRAMES_TOPOLOGY))
        for xyz in frames.xyz * 10.0:
            self.assertLess(_apart(xyz), 3.0)
        self.assertLess(_apart(_xyz((self.simulation / FRAMES_TOPOLOGY).read_text())), 3.0)


if __name__ == "__main__":
    unittest.main()
