"""A variable-step study whose first piece was stopped in its production is
read by its clock: before a resume, after it, and on the Overview.

A first piece stopped at its wall time writes no simulation record of its
own, so a study of that one piece was "not recorded", although its
checkpoint's record holds the state's time (`time_ps`): `fastmdx resume`
answered `production_done_before_ns: null`. Carried on, the Overview decided
"variable step" from that record, which is not there, and gave the pieces'
steps times the timestep (6 ps) beside the methods' and the resume's 4.87 ps
by the clock. Both now read what `recorded_stage_lengths` reads: the
study's config, its pieces' records and their checkpoints'.

The records are those a real tri-alanine study left (variable-step
Langevin, 2 fs asked, 3,000 production steps, a checkpoint every 500 steps,
on the CPU), killed once its checkpoint said production step 1,500, then
carried on with `fastmdx resume` for the 1,500 steps left.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fastmdxplora.simulation.lengths import FROM_CLOCK, recorded_stage_lengths
from tests.test_a_piece_stopped_off_an_energy_row_is_read_by_its_clock import (
    VARIABLE,
    _checkpoint,
    _piece,
    _record,
    _state,
)

#: The first piece's time at its checkpoint at step 1,500, and the second
#: piece's at its end, 1,500 steps on.
FIRST_PS = 2.4061242072555253
SECOND_PS = 2.4612643377692223


def _stopped(tmp_path: Path) -> Path:
    """The study as the kill left it: one piece, no record of its own."""
    root = tmp_path / "study"
    own = _piece(root, None)
    _checkpoint(own, 1500, finished=False, time_ps=FIRST_PS)
    (root / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "tri", "system": "tri.pdb"}],
        "simulation": {**VARIABLE, "production_steps": 3000,
                       "nvt_steps": 100, "npt_steps": 100}}), encoding="utf-8")
    return root


def _carried_on(root: Path) -> Path:
    """The piece `fastmdx resume` ran: the 1,500 steps left."""
    last = _piece(root, "segment-001")
    _record(last, {**VARIABLE, "duration_ns": 0.003, "nvt_steps": 0, "npt_steps": 0},
            {"nvt_steps": 0, "npt_steps": 0, "production_steps": 1500})
    _state(last, 1500, SECOND_PS)
    _checkpoint(last, 1500, finished=True, time_ps=SECOND_PS)
    return last


def test_a_stopped_first_piece_is_read_by_its_checkpoint_s_time(tmp_path: Path) -> None:
    said = recorded_stage_lengths(_stopped(tmp_path))
    assert said["production_ran_ns"] == pytest.approx(0.002406124, abs=1e-9)
    assert said["production_ran_source"] == FROM_CLOCK
    assert said["variable_step"] is True
    assert said["production"]["steps"] is None
    assert "stopped before its end" in said["production"]["said"]


def test_carried_on_the_overview_gives_the_clock(tmp_path: Path) -> None:
    from fastmdxplora.gui.simulated_time import simulated_times

    root = _stopped(tmp_path)
    _carried_on(root)
    lengths = recorded_stage_lengths(root)
    # 2.406 + 2.461 ps, as the methods and the resume give it.
    assert lengths["production_ran_ns"] == pytest.approx(0.004867389, abs=1e-9)
    times = simulated_times(root, {})
    # Its steps times the timestep (6 ps) was said.
    assert times["production_ns"] == lengths["production_ran_ns"]
    assert times["pieces"] == 2


def test_stopped_the_overview_gives_the_clock(tmp_path: Path) -> None:
    from fastmdxplora.gui.simulated_time import simulated_times

    root = _stopped(tmp_path)
    # Its live record as the kill left it, at step 1,200 of 3,200 (2 ps by
    # its steps).
    status = {"status": "interrupted", "stage": "production", "current_step": 1200,
              "nvt_steps_planned": 100, "npt_steps_planned": 100,
              "production_steps_planned": 3000, "timestep_fs": 2.0}
    times = simulated_times(root, status)
    assert times["production_ns"] == pytest.approx(0.002406124, abs=1e-9)


def test_a_resume_gives_before_and_after_by_the_clock(tmp_path, monkeypatch, capfd) -> None:
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation import resume

    root = _stopped(tmp_path)
    # Steps times the timestep, as the plan counts them: 1,500 x 2 fs.
    monkeypatch.setattr(resume, "extension_of", lambda study, **_: resume.Continuation(
        parent=str(study), checkpoint="", production_done_ns=0.003,
        production_planned_ns=0.006, config={}))

    def carried_on(study, **_):
        return {"ok": True, "segment": str(_carried_on(Path(study)))}

    monkeypatch.setattr(resume, "extend_study", carried_on)
    assert main(["resume", str(root), "--json"]) == 0
    answer = json.loads(capfd.readouterr().out)
    assert answer["did"] == "continued"
    # It was null, "not recorded".
    assert answer["production_done_before_ns"] == pytest.approx(0.002406124, abs=1e-9)
    assert answer["production_done_ns"] == pytest.approx(0.004867389, abs=1e-9)
    assert answer["production_done_before_source"] == answer["production_done_source"] \
        == FROM_CLOCK
    assert (answer["production_done_ns"] - answer["production_done_before_ns"]
            == pytest.approx(0.002461264, abs=1e-9))
