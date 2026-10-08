"""The Viewer plays a finished run's production frames.

A run writes snapshots from minimisation on, through equilibration, while
it goes. They stood in for the trajectory whenever the run's record did not
say "completed" in so many words, so a run whose record still said
"running" after it ended (the Python API writes no end of its own), and
every stopped or failed run, was played as its snapshots of every stage
with steps repeated, not as its production trajectory. The Contact map,
Backbone angles, the movie and States read the same frames.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_a_live_frame_shows_the_ligand_in_its_pocket import _snapshot  # noqa: E402

PRODUCTION_FRAMES = 5


def _study(root: Path) -> Path:
    import mdtraj as md

    from fastmdxplora.gui.live_frames import write_live_frame

    simulation = root / "simulation"
    simulation.mkdir(parents=True)
    (simulation / "topology.pdb").write_text(_snapshot(), encoding="utf-8")
    for step, (stage, shift) in enumerate((("minimization", 0.0), ("nvt", 0.1),
                                           ("npt", 0.2), ("production", 0.3))):
        write_live_frame(simulation, pdb_text=_snapshot(shift), frame_index=step,
                         stage=stage, simulation_time_ns=0.01 * step, archive=True)
    topology = md.load_topology(str(simulation / "topology.pdb"))
    xyz = np.repeat(md.load_pdb(str(simulation / "topology.pdb")).xyz, PRODUCTION_FRAMES, 0)
    md.Trajectory(xyz, topology,
                  unitcell_lengths=np.full((PRODUCTION_FRAMES, 3), 4.0, dtype=np.float32),
                  unitcell_angles=np.full((PRODUCTION_FRAMES, 3), 90.0, dtype=np.float32),
                  ).save_dcd(str(simulation / "production.dcd"))
    return root


def _record(root: Path, **said) -> None:
    (root / "simulation" / "live_status.json").write_text(json.dumps(said), encoding="utf-8")


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


@pytest.mark.parametrize("said", [
    {"status": "completed", "stage": "report"},
    # Ended without saying so: its last word is days old, every step taken.
    {"status": "running", "stage": "production", "current_step": 55000,
     "total_planned_steps": 55000, "last_update_timestamp": _ago(72)},
    {"status": "failed", "stage": "production"},
    {"status": "stopped", "stage": "production"},
    {"status": "interrupted", "stage": "production"},
    # A record saying the run goes on, in a stage after the simulation.
    {"status": "running", "stage": "analysis", "last_update_timestamp": _ago(0)},
], ids=["completed", "ended-unsaid", "failed", "stopped", "interrupted", "analysing"])
def test_a_run_that_is_not_simulating_plays_its_production(tmp_path, said):
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = _study(tmp_path / "study")
    _record(root, **said)
    shown = frames_info(root)
    assert shown["available"], shown
    assert shown["source_kind"] == "production-dcd"
    assert shown["n_frames_total"] == PRODUCTION_FRAMES


def test_a_run_still_simulating_plays_its_snapshots(tmp_path):
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = _study(tmp_path / "study")
    _record(root, status="running", stage="production", current_step=100,
            total_planned_steps=55000, last_update_timestamp=_ago(0))
    shown = frames_info(root)
    assert shown["available"], shown
    assert shown["source_kind"] == "live-history"


def test_the_frames_follow_the_run_as_it_ends(tmp_path):
    """Frames written while it ran are not kept once it has ended."""
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = _study(tmp_path / "study")
    _record(root, status="running", stage="production", current_step=100,
            total_planned_steps=55000, last_update_timestamp=_ago(0))
    assert frames_info(root)["source_kind"] == "live-history"
    _record(root, status="stopped", stage="production")
    assert frames_info(root)["source_kind"] == "production-dcd"
