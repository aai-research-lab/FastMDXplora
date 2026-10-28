"""A variable-step run's methods give production by the run's own clock.

A variable-step integrator chooses its own step, so a stage's steps times
the timestep is not its length. The methods paragraph said "Production
dynamics were run for 4 ps" of a run whose final state read 3.28 ps, while
`recorded_stage_lengths` said the length was not recorded: two readers, one
study. Now the paragraph gives production at the run's own clock (its final
state's time, else its energy log's at its last checkpoint), and the
equilibration in steps.
"""

from __future__ import annotations

from pathlib import Path

from fastmdxplora.report.methods import methods_paragraphs

SIMULATION = {"parameters": {"integrator": "variable_langevin", "timestep_fs": 2.0,
                             "temperature_K": 300.0, "friction_per_ps": 1.0},
              "resolved": {"nvt_steps": 500, "npt_steps": 800, "production_steps": 2000,
                           "trajectory_interval_steps": 100, "ensemble": "npt"}}


def _run(tmp_path: Path, *, state: bool = True, log: bool = False) -> Path:
    folder = tmp_path / "simulation"
    folder.mkdir()
    if state:
        (folder / "state_final.xml").write_text(
            '<?xml version="1.0" ?>\n<State openmmVersion="8.2" time="3.2813" '
            'type="State" version="1">\n</State>\n', encoding="utf-8")
    if log:
        (folder / "energy.csv").write_text(
            '#"Step","Time (ps)","Potential Energy (kJ/mole)"\n1000,1.6,-10\n2000,3.25,-11\n',
            encoding="utf-8")
    return tmp_path


def test_production_is_said_at_the_run_s_own_clock(tmp_path: Path) -> None:
    text = methods_paragraphs(_run(tmp_path), {}, SIMULATION)
    assert ("Production dynamics were run for 3.28 ps (the length by the integrator's "
            "own clock, 2,000 steps)") in text
    assert "500 steps in the NVT ensemble followed by 800 steps in the NPT ensemble" in text
    assert "4 ps" not in text and "1 ps" not in text
    assert "Coordinates were written every 100 steps." in text
    assert "fs timestep" not in text


def test_without_a_final_state_the_energy_log_at_its_checkpoint_is_read(
        tmp_path: Path) -> None:
    import json

    root = _run(tmp_path, state=False, log=True)
    (root / "simulation" / "checkpoint.chk").write_bytes(b"")
    (root / "simulation" / "checkpoint.chk.json").write_text(json.dumps(
        {"stage": "production", "step": 2000, "finished": True}), encoding="utf-8")
    text = methods_paragraphs(root, {}, SIMULATION)
    assert "Production dynamics were run for 3.25 ps" in text


def test_without_a_clock_the_length_in_time_is_not_recorded(tmp_path: Path) -> None:
    text = methods_paragraphs(_run(tmp_path, state=False), {}, SIMULATION)
    assert "2,000 steps (its length in time not recorded)" in text


def test_an_extended_run_sums_its_pieces_clocks(tmp_path: Path) -> None:
    """Carried on in a piece of its own: each piece's clock, summed, where
    steps times the timestep said 6 ps of pieces that ran 3.28 and 1.67."""
    root = _run(tmp_path)
    piece = root / "segment-001" / "simulation"
    piece.mkdir(parents=True)
    (piece / "state_final.xml").write_text(
        '<State openmmVersion="8.2" time="1.672" type="State" version="1">\n</State>\n',
        encoding="utf-8")
    text = methods_paragraphs(root, {}, SIMULATION, extended=(0.006, 2))
    assert ("Production dynamics were run for 4.95 ps (the length by the integrator's "
            "own clock, summed over 2 pieces)") in text
    assert "6 ps" not in text


def test_the_stage_length_reader_gives_the_same_clock(tmp_path: Path) -> None:
    import json

    from fastmdxplora.simulation.lengths import FROM_CLOCK, recorded_stage_lengths

    root = _run(tmp_path)
    (root / "simulation" / "simulation_parameters.json").write_text(
        json.dumps(SIMULATION), encoding="utf-8")
    said = recorded_stage_lengths(root)
    assert said["production"]["ns"] == 0.0032813
    assert said["production"]["source"] == FROM_CLOCK
    assert said["production_ran_ns"] == 0.0032813
    assert said["nvt"]["ns"] is None


def test_a_clock_summed_over_pieces_gives_no_steps_beside_it(tmp_path: Path) -> None:
    """Summed over two pieces, the time is the whole run's; the steps were
    the first piece's plan, given as that, not beside the sum as its own."""
    import json

    from fastmdxplora.simulation.lengths import recorded_stage_lengths

    root = _run(tmp_path)
    (root / "simulation" / "simulation_parameters.json").write_text(
        json.dumps(SIMULATION), encoding="utf-8")
    piece = root / "segment-001" / "simulation"
    piece.mkdir(parents=True)
    (piece / "state_final.xml").write_text(
        '<State openmmVersion="8.2" time="1.672" type="State" version="1">\n</State>\n',
        encoding="utf-8")
    production = recorded_stage_lengths(root)["production"]
    assert production["ns"] == 0.0049533
    assert production["steps"] is None and production["first_piece_steps"] == 2000
    assert "the first piece's plan" in production["said"]
