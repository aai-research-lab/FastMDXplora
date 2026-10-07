"""Every place that lists or packs a study's files reads each file one way.

The project bundle walked the folder its own way and carried the 200
snapshots the live view keeps (trypsin: 52 MB of PDB beside a 7.8 MB
trajectory), and the Files page read it another. `study_files` is the one
reading: a file's phase, its run, its kind, and what it is in words.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.study_files import filter_of, is_scratch, kind_of, label_of, place


@pytest.mark.parametrize("rel, phase", [
    ("setup/input.pdb", "setup"),
    ("shared_setup/setup/solvated.pdb", "setup"),
    ("simulation/production.dcd", "simulation"),
    ("joined/production.dcd", "simulation"),
    ("segment-002/simulation/production.dcd", "simulation"),
    ("stopping.json", "simulation"),
    ("analysis/rmsd/rmsd.dat", "analysis"),
    ("comparison/comparison_summary.csv", "analysis"),
    ("report/report.md", "report"),
    ("scenes/pocket.mvsx", "saved"),
    ("movies/turn.mp4", "saved"),
    ("viewer_views.json", "saved"),
    ("deposit/3PTB_deposit_2026-10-06.zip", "deposit"),
    ("manifest.json", "record"),
    ("resolved_config.yml", "record"),
    ("previous/analysis/rmsd/rmsd.png", "previous"),
    ("superseded/window_1-20261001T000000Z/simulation/production.dcd", "previous"),
    ("simulation/live_frames/frame_000001_nvt_000000001000.pdb", "scratch"),
    ("simulation/frames.dcd", "scratch"),
    ("simulation/frames_topology.pdb", "scratch"),
    ("simulation/frames_superposed_backbone.dcd", "scratch"),
    ("simulation/frames_pieces/piece_0.xtc", "scratch"),
    ("simulation/contact_map.json", "scratch"),
    ("viewer_runs/run_1.dcd", "scratch"),
    ("viewer_beside/abc.dcd", "scratch"),
])
def test_each_file_is_put_with_the_phase_that_wrote_it(rel, phase):
    assert place(rel)[0] == phase


def test_a_run_of_a_study_of_several_is_read_as_that_run_s():
    assert place("runs/s1__seed-2/simulation/production.dcd") == (
        "simulation", "s1__seed-2", "simulation/production.dcd")
    assert place("runs/s1__seed-2/simulation/live_frames/f.pdb")[0] == "scratch"
    assert place("runs/s1__seed-2/manifest.json") == ("record", "s1__seed-2", "manifest.json")


def test_the_run_s_own_records_are_not_scratch():
    # What the live view wrote that is a record of the run, not a cache.
    for name in ("live_status.json", "live_metrics.csv", "live_events.log", "live_frame.pdb"):
        assert not is_scratch(f"simulation/{name}"), name
    assert not is_scratch("simulation/production.dcd")
    assert not is_scratch("analysis/frames.dat")


@pytest.mark.parametrize("rel, kind, shown_as", [
    ("setup/input.pdb", "structure", "structure"),
    ("simulation/state_final.xml", "state", "structure"),
    ("setup/system.xml", "config", "config"),
    ("simulation/checkpoint.chk", "state", "structure"),
    ("simulation/production.dcd", "trajectory", "trajectory"),
    ("analysis/rmsd/rmsd.dat", "data", "data"),
    ("analysis/cluster/cluster_rmsd_matrix.npz", "data", "data"),
    ("analysis/rmsd/rmsd.svg", "figure", "figure"),
    ("report/report.md", "doc", "doc"),
    ("report/slides.pptx", "slides", "doc"),
    ("report/project_bundle.zip", "archive", "doc"),
    ("simulation/simulation.log", "log", "log"),
    ("resolved_config.yml", "config", "config"),
    ("analysis/metad/COLVAR", "data", "data"),
])
def test_each_file_is_of_a_kind(rel, kind, shown_as):
    assert kind_of(rel) == kind
    assert filter_of(kind) == shown_as


def test_a_file_says_what_it_is():
    assert label_of("setup/retained.pdb") == "Heterogens kept"
    assert label_of("analysis/rmsd/options.json") == "rmsd: options used"
    assert label_of("analysis/rmsd/rmsd.png") == "rmsd: figure (PNG)"
    assert label_of("previous/analysis/rmsd/rmsd.png") == "rmsd: figure (PNG), set aside"
    assert label_of("simulation/live_frames/frame_1.pdb") == "Live view snapshot: frame_1.pdb"
    assert label_of("scenes/pocket.mvsx") == "Scene: pocket"
    assert label_of("weird.bin") == "weird.bin"


def test_the_bundle_leaves_the_scratch_and_the_deposits_out(tmp_path: Path):
    from fastmdxplora.report.bundle import _iter_project_files

    for rel in ("simulation/production.dcd", "simulation/live_status.json",
                "simulation/live_frames/frame_000001_nvt_000000001000.pdb",
                "simulation/live_frame_history.json", "simulation/frames.dcd",
                "viewer_runs/run_1.dcd", "deposit/x_deposit.zip",
                "superseded/w-1/simulation/production.dcd", "analysis/rmsd/rmsd.dat"):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    bundled = {p.relative_to(tmp_path).as_posix()
               for p in _iter_project_files(tmp_path, tmp_path / "report" / "project_bundle.zip")}
    assert bundled == {"simulation/production.dcd", "simulation/live_status.json",
                       "analysis/rmsd/rmsd.dat"}
