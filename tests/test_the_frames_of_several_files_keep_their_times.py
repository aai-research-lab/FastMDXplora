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


def test_what_reads_the_series_afterwards_reads_those_times(tmp_path):
    """The manifest keeps the loader's times where several files were
    strided, and the GUI's axes read them: they assumed even spacing, and
    gave 10 to 110 ps where the frames were written at 10 to 100."""
    import json

    from fastmdxplora.analysis import AnalysisOrchestrator
    from fastmdxplora.gui.series import analysed_axis

    paths, top = _files(tmp_path, [5, 5])
    out = tmp_path / "study"
    AnalysisOrchestrator(paths, topology=top, output_dir=str(out / "analysis"), stride=2,
                         saving_interval_ps=10.0).run(include=["rg"])
    manifest = json.loads((out / "analysis" / "analysis_manifest.json").read_text())
    assert manifest["frame_times_ps"] == [10.0, 30.0, 50.0, 60.0, 80.0, 100.0]
    frames, x, label = analysed_axis(out, 6)
    assert frames == [0, 2, 4, 5, 7, 9]
    assert x == [0.01, 0.03, 0.05, 0.06, 0.08, 0.1]
    assert label == "Time (ns)"


def test_one_stream_keeps_its_manifest_small(tmp_path):
    import json

    from fastmdxplora.analysis import AnalysisOrchestrator

    paths, top = _files(tmp_path, [7])
    out = tmp_path / "study"
    AnalysisOrchestrator(paths[0], topology=top, output_dir=str(out / "analysis"), stride=3,
                         saving_interval_ps=5.0).run(include=["rg"])
    manifest = json.loads((out / "analysis" / "analysis_manifest.json").read_text())
    assert "frame_times_ps" not in manifest
