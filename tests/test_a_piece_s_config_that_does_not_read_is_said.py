"""A piece's config that does not read leaves a study's production not
recorded, said, rather than raising.

`resume.extended_production` read each piece's `resolved_config.yml` to
count what it ran, and raised where one did not read as a config: a YAML
list, a `timestep_fs` given as text ("2 fs"), a file that is not UTF-8. So
`lengths.recorded_stage_lengths`, the record behind the methods and the
study pages, raised too. What the pieces ran is then not known: said "not
recorded", with which file and why, never the first piece's alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fastmdxplora.simulation.lengths import NOT_RECORDED, recorded_stage_lengths
from fastmdxplora.simulation.resume import extended_production

SIMULATION = {"timestep_fs": 2.0, "nvt_steps": 100, "npt_steps": 100,
              "trajectory_interval_steps": 50}


def _piece(folder: Path, *, steps: int, config: dict) -> None:
    simulation = folder / "simulation"
    simulation.mkdir(parents=True)
    (simulation / "production.dcd").write_bytes(b"frames")
    (simulation / "checkpoint.chk").write_bytes(b"x")
    (simulation / "checkpoint.chk.json").write_text(json.dumps(
        {"stage": "production", "step": steps, "ensemble": "npt", "timestep_fs": 2.0,
         "finished": True, "trajectory_interval_steps": 50}), encoding="utf-8")
    (simulation / "simulation_parameters.json").write_text(json.dumps(
        {"parameters": {**config["simulation"], "integrator": "langevin_middle"},
         "resolved": {"production_steps": steps, "nvt_steps": 100, "npt_steps": 100},
         "duration_ns_actual": steps * 2e-6}), encoding="utf-8")
    (folder / "resolved_config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")


def _extended(tmp_path: Path) -> Path:
    """A study of 300 steps extended by 200 (0.0006 + 0.0004 ns)."""
    root = tmp_path / "study"
    _piece(root, steps=300, config={"simulation": {**SIMULATION, "duration_ns": 0.0006}})
    _piece(root / "segment-001", steps=200, config={"simulation": {
        **SIMULATION, "duration_ns": 0.0004, "nvt_steps": 0, "npt_steps": 0}})
    return root


UNREADABLE = {
    "a list": ("- simulation\n- 2 fs\n", "it is not a mapping of settings"),
    "a timestep in words": ("simulation:\n  timestep_fs: 2 fs\n  duration_ns: 0.0006\n",
                            "its `simulation.timestep_fs` ('2 fs') is not a length"),
    "not UTF-8": (b"simulation:\n  duration_ns: 0.0006 # \xe9t\xe9\n", "it is not UTF-8 text"),
}


def test_a_study_whose_configs_read_is_summed(tmp_path: Path) -> None:
    root = _extended(tmp_path)
    assert extended_production(root) == (0.001, 2)
    assert recorded_stage_lengths(root)["production_ran_ns"] == 0.001


@pytest.mark.parametrize("case", sorted(UNREADABLE))
@pytest.mark.parametrize("where", ["", "segment-001"])
def test_a_config_that_does_not_read_is_said_not_recorded(tmp_path: Path, case: str,
                                                          where: str) -> None:
    root = _extended(tmp_path)
    text, why = UNREADABLE[case]
    path = (root / where if where else root) / "resolved_config.yml"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    said = recorded_stage_lengths(root)
    assert said["production_ran_ns"] is None
    assert said["production_ran_source"] == NOT_RECORDED
    owner = "the study's" if not where else f"{where}'s"
    assert said["production_ran_said"] == (
        f"not recorded: {owner} resolved_config.yml does not read: {why}")
    assert said["pieces"] == 2
    assert extended_production(root) is None
