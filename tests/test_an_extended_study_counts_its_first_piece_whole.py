"""An extended study counts its own first piece whole.

A checkpoint written before the step counter was understood could carry a
whole-run step, and a step past the plan's production was read as one and
had the equilibration taken off it. Once a study has been extended, its
own config is the one the join rewrote, with the last piece's length: a
first piece of 300 production steps, extended by 50, was counted as 100,
and the study's production went down as it grew. A study run until it
knew, which extends by what its numbers ask, met it on its third round.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from fastmdxplora.simulation.resume import continuation_of
from fastmdxplora.simulation.runner import write_checkpoint_sidecar


def _study(tmp_path: Path, *, interval: int | None) -> Path:
    study = tmp_path / "study"
    (study / "simulation").mkdir(parents=True)
    # As the join leaves it: the last piece's length beside the first
    # piece's equilibration.
    (study / "resolved_config.yml").write_text(yaml.safe_dump({
        "simulation": {"duration_ns": 0.0001, "nvt_steps": 100, "npt_steps": 100,
                       "timestep_fs": 2.0}}), encoding="utf-8")
    checkpoint = study / "simulation" / "checkpoint.chk"
    checkpoint.write_bytes(b"x")
    write_checkpoint_sidecar(checkpoint, stage="production", step=300, ensemble="npt",
                             temperature_K=300.0, timestep_fs=2.0, study=str(study),
                             finished=True, trajectory_interval_steps=interval)
    return study


def test_a_sidecar_that_records_its_interval_is_read_as_written(tmp_path):
    assert continuation_of(_study(tmp_path, interval=50)).production_done_ns == 0.0006


def test_one_from_before_is_still_converted(tmp_path):
    # The case the conversion exists for, unchanged.
    assert abs(continuation_of(_study(tmp_path, interval=None)).production_done_ns
               - 0.0002) < 1e-12
