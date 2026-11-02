"""A variable-step piece stopped at a checkpoint off its energy log's rows
is read by its own clock, by the methods and the Overview alike.

Checkpoints sit on frames (every 500 steps here) and the energy log writes
a row every 1,000 steps, so a piece is often stopped with its last
checkpoint between two rows. Its time there was read from the log's row at
that step, and with none the study's production was "not recorded", and its
methods said "its length in time is not recorded". The checkpoint's record
now carries the state's time as it was written (`time_ps`), and that is
read first.

The fixture holds the records a tri-alanine study left (variable-step
Langevin, 2 fs asked, on the CPU): 2,000 steps, extended by 0.012 ns and
stopped at its checkpoint at step 2,500, then carried on with `fastmdx
resume` for 3,500 steps.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fastmdxplora.report.methods import methods_paragraphs
from fastmdxplora.simulation.lengths import FROM_CLOCK, recorded_stage_lengths

VARIABLE = {"integrator": "variable_langevin", "timestep_fs": 2.0,
            "temperature_K": 300.0, "friction_per_ps": 1.0, "ensemble": "npt",
            "trajectory_interval_steps": 100}


def _piece(root: Path, name: str | None) -> Path:
    folder = (root / name if name else root) / "simulation"
    folder.mkdir(parents=True)
    (folder / "production.dcd").write_bytes(b"")
    return folder


def _checkpoint(folder: Path, step: int, *, finished: bool, time_ps: float | None) -> None:
    (folder / "checkpoint.chk").write_bytes(b"")
    (folder / "checkpoint.chk.json").write_text(json.dumps(
        {"stage": "production", "step": step, "ensemble": "npt", "timestep_fs": 2.0,
         "finished": finished, "trajectory_interval_steps": 100,
         **({} if time_ps is None else {"time_ps": time_ps})}), encoding="utf-8")


def _state(folder: Path, step: int, time: float) -> None:
    (folder / "state_final.xml").write_text(
        f'<?xml version="1.0" ?>\n<State openmmVersion="8.6.1" stepCount="{step}" '
        f'time="{time!r}" type="State" version="1">\n</State>\n', encoding="utf-8")


def _energy(folder: Path, rows: list[tuple[int, float]]) -> None:
    (folder / "energy.csv").write_text(
        '#"Progress (%)","Step","Time (ps)","Potential Energy (kJ/mole)"\n'
        + "".join(f"0%,{step},{time!r},-25000\n" for step, time in rows), encoding="utf-8")


def _record(folder: Path, parameters: dict, resolved: dict) -> None:
    (folder / "simulation_parameters.json").write_text(json.dumps(
        {"phase": "simulation", "parameters": parameters, "resolved": resolved,
         "pressure_bar_used": 1.0, "barostat": "isotropic"}), encoding="utf-8")


def _varyc(tmp_path: Path, *, time_ps: float | None = 4.123377771744367) -> Path:
    root = tmp_path / "varyc"
    own = _piece(root, None)
    _record(own, {**VARIABLE, "production_steps": 2000, "nvt_steps": 100, "npt_steps": 100},
            {"nvt_steps": 100, "npt_steps": 100, "production_steps": 2000})
    _state(own, 2000, 3.208167103966084)
    # Written before the sidecar said its time: read from its final state.
    _checkpoint(own, 2000, finished=True, time_ps=None)
    stopped = _piece(root, "segment-001")
    _checkpoint(stopped, 2500, finished=False, time_ps=time_ps)
    _energy(stopped, [(1000, 1.6534028202403404), (2000, 3.3005176213547744)])
    last = _piece(root, "segment-002")
    _record(last, {**VARIABLE, "duration_ns": 0.007, "nvt_steps": 0, "npt_steps": 0},
            {"nvt_steps": 0, "npt_steps": 0, "production_steps": 3500})
    _state(last, 3500, 5.749152079363507)
    _checkpoint(last, 3500, finished=True, time_ps=5.749152079363507)
    return root


def test_its_clock_at_the_checkpoint_is_the_record_s_time(tmp_path: Path) -> None:
    said = recorded_stage_lengths(_varyc(tmp_path))
    # 3.208 + 4.123 + 5.749 ps.
    assert said["production"]["ns"] == 0.013080697
    assert said["production"]["source"] == FROM_CLOCK
    assert said["production_ran_ns"] == 0.013080697
    assert said["production"]["steps"] is None
    assert said["production"]["first_piece_steps"] == 2000


def test_the_methods_give_the_same_length(tmp_path: Path) -> None:
    root = _varyc(tmp_path)
    record = json.loads((root / "simulation" / "simulation_parameters.json")
                        .read_text(encoding="utf-8"))
    text = methods_paragraphs(root, {}, record)
    assert ("Production dynamics were run for 13.08 ps (the length by the "
            "integrator's own clock, summed over 3 pieces)") in text
    assert "not recorded" not in text


def test_the_overview_gives_the_same_length(tmp_path: Path) -> None:
    from fastmdxplora.gui.simulated_time import simulated_times

    root = _varyc(tmp_path)
    times = simulated_times(root, {})
    # Its pieces' steps times the timestep was said (1.6 ps for a real study
    # of 1.29 ps by the clock), not the methods' length.
    assert times["production_ns"] == recorded_stage_lengths(root)["production_ran_ns"]
    assert times["production_ns"] == 0.013080697
    assert times["pieces"] == 3


def test_a_record_without_its_time_and_no_row_there_is_still_not_recorded(
        tmp_path: Path) -> None:
    production = recorded_stage_lengths(_varyc(tmp_path, time_ps=None))["production"]
    assert production["ns"] is None
    assert "no row at step 2,500" in production["said"]


try:
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False


@unittest.skipUnless(HAS_BACKENDS, "OpenMM and PDBFixer are needed")
class TestTheRunnerWritesTheTimeOfEachCheckpoint(unittest.TestCase):

    def test_each_checkpoint_s_record_gives_the_state_s_time(self):
        from fastmdxplora import FastMDXplora
        from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        pdb = root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        study = root / "study"
        FastMDXplora(config_data={
            "systems": [{"id": "tri", "system": str(pdb)}],
            "setup": {"ph": 7.0, "solvent_padding_nm": 1.0, "nonbonded_cutoff_nm": 0.9},
            "simulation": {"platform": "CPU", "integrator": "variable_langevin",
                           "production_steps": 200, "nvt_steps": 20, "npt_steps": 20,
                           "timestep_fs": 2, "trajectory_interval_steps": 50,
                           "checkpoint_interval_steps": 100},
        }, output_dir=str(study)).explore(include=["setup", "simulation"])
        folder = study / "simulation"
        side = json.loads((folder / "checkpoint.chk.json").read_text(encoding="utf-8"))
        state = (folder / "state_final.xml").read_text(encoding="utf-8")[:2048]
        import re

        self.assertIn("time_ps", side)
        time = float(re.search(r'\btime="([0-9.eE+-]+)"', state).group(1))
        self.assertAlmostEqual(side["time_ps"], time, places=9)
        self.assertGreater(side["time_ps"], 0.0)
