import json

import pytest

from fastmdxplora.gui.trajectory_playback import _recorded_dcd_times


def test_physical_time_uses_recorded_reporter_sampling_not_frame_ordinals(tmp_path):
    record = {"resolved": {"trajectory_interval_steps": 250, "production_steps": 500000},
              "parameters": {"timestep_fs": 2.0, "integrator": "langevin_middle"},
              "n_production_frames": 2000, "continues": None}
    target = tmp_path / "simulation_parameters.json"
    target.write_text(json.dumps(record))
    assert _recorded_dcd_times(tmp_path / "production.dcd", 2000, [0, 30, 1999]) == pytest.approx(
        [0.0005, 0.0155, 1.0]
    )
    assert _recorded_dcd_times(tmp_path / "production.dcd", 1999, [0, 30]) == [None, None]
    record["continues"] = "earlier-segment"
    target.write_text(json.dumps(record))
    assert _recorded_dcd_times(tmp_path / "production.dcd", 2000, [0, 30]) == [None, None]
    target.unlink()
    assert _recorded_dcd_times(tmp_path / "production.dcd", 2000, [0, 30]) == [None, None]
