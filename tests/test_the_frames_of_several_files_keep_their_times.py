"""Strided frames of several files are given the times they were written.

MDTraj strides each file on its own, from that file's first frame, and the
loader gave the frames times as though they were one stream: two files of
five frames, stride 2, written every 10 ps, are frames 0, 2, 4, 5, 7, 9 of
the run, written at 10, 30, 50, 60, 80 and 100 ps. They read 10 to 110.
"""

from __future__ import annotations

import mdtraj as md
import numpy as np

from fastmdxplora.analysis.loading import load_trajectory


def _files(tmp_path, lengths):
    """DCD files of the given lengths; each frame carries its index in the
    run as the x coordinate of its first atom."""
    top = md.Topology()
    residue = top.add_residue("ALA", top.add_chain())
    for i in range(3):
        top.add_atom(f"C{i}", md.element.carbon, residue)
    paths, start = [], 0
    for k, length in enumerate(lengths):
        xyz = np.zeros((length, 3, 3), dtype=np.float32)
        xyz[:, 0, 0] = np.arange(start, start + length)
        xyz[:, 1, 1] = 1.0
        xyz[:, 2, 2] = 1.0
        path = tmp_path / f"run{k:02d}.dcd"
        md.Trajectory(xyz, top).save_dcd(str(path))
        paths.append(str(path))
        start += length
    pdb = tmp_path / "top.pdb"
    md.Trajectory(np.zeros((1, 3, 3), dtype=np.float32) + np.eye(3, dtype=np.float32),
                  top).save_pdb(str(pdb))
    return paths, str(pdb)


def test_each_frame_is_given_the_time_it_was_written(tmp_path):
    paths, top = _files(tmp_path, [5, 5])
    loaded = load_trajectory(paths, top=top, stride=2, saving_interval_ps=10.0)

    written = np.rint(loaded.xyz[:, 0, 0]).astype(int)
    assert list(written) == [0, 2, 4, 5, 7, 9]
    assert np.allclose(loaded.time, (written + 1) * 10.0)


def test_lengths_that_are_multiples_of_the_stride_read_as_before(tmp_path):
    paths, top = _files(tmp_path, [6, 4])
    loaded = load_trajectory(paths, top=top, stride=2, saving_interval_ps=10.0)

    assert np.allclose(loaded.time, (np.arange(5) * 2 + 1) * 10.0)


def test_one_file_reads_as_before(tmp_path):
    paths, top = _files(tmp_path, [7])
    loaded = load_trajectory(paths[0], top=top, stride=3, saving_interval_ps=5.0)

    assert np.allclose(loaded.time, [5.0, 20.0, 35.0])
