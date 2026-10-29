"""A study carried on after a stop gives its stage lengths and its clock.

A JCC rerun stopped at its wall time is carried on with `fastmdx resume`,
and its pieces are not what a study run in one go leaves:

- a piece stopped between checkpoints has no final state, and its energy
  log runs past the checkpoint the next piece continued from. Read to its
  last line, the clock said 16.7 ps of a production that ran 13.3 ps;
- a study whose first piece was stopped never wrote its own simulation
  record. Its integrator was taken as fixed step, so a variable-step study
  was given 12 ps (steps times the timestep) where its clocks gave 9.96,
  and every stage and the timestep read "not recorded" while its config
  held them all.

Each fixture keeps only the records the readers use, with the values the
studies these were found on wrote (chignolin, 2 fs, on the CPU).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmdxplora.report.methods import methods_paragraphs
from fastmdxplora.simulation.lengths import (
    FROM_CLOCK,
    FROM_STEPS,
    NOT_RECORDED,
    SET,
    recorded_stage_lengths,
)


def _state(folder: Path, step: int, time: float) -> None:
    (folder / "state_final.xml").write_text(
        f'<?xml version="1.0" ?>\n<State openmmVersion="8.6.1" stepCount="{step}" '
        f'time="{time!r}" type="State" version="1">\n</State>\n', encoding="utf-8")


def _checkpoint(folder: Path, step: int, *, finished: bool) -> None:
    (folder / "checkpoint.chk").write_bytes(b"")
    (folder / "checkpoint.chk.json").write_text(json.dumps(
        {"stage": "production", "step": step, "ensemble": "npt", "timestep_fs": 2.0,
         "finished": finished, "trajectory_interval_steps": 100}), encoding="utf-8")


def _energy(folder: Path, rows: list[tuple[int, float]]) -> None:
    (folder / "energy.csv").write_text(
        '#"Progress (%)","Step","Time (ps)","Potential Energy (kJ/mole)"\n'
        + "".join(f"0%,{step},{time!r},-25000\n" for step, time in rows), encoding="utf-8")


def _piece(root: Path, name: str | None) -> Path:
    folder = (root / name if name else root) / "simulation"
    folder.mkdir(parents=True)
    (folder / "production.dcd").write_bytes(b"")
    return folder


def _record(folder: Path, parameters: dict, resolved: dict) -> None:
    (folder / "simulation_parameters.json").write_text(json.dumps(
        {"phase": "simulation", "parameters": parameters, "resolved": resolved,
         "pressure_bar_used": 1.0, "barostat": "isotropic"}), encoding="utf-8")


def _config(folder: Path, simulation: dict) -> None:
    lines = "".join(f"  {key}: {json.dumps(value)}\n" for key, value in simulation.items())
    (folder / "resolved_config.yml").write_text("simulation:\n" + lines, encoding="utf-8")


VARIABLE = {"integrator": "variable_langevin", "timestep_fs": 2.0,
            "temperature_K": 300.0, "friction_per_ps": 1.0, "ensemble": "npt",
            "trajectory_interval_steps": 100}


def _varc(tmp_path: Path) -> Path:
    """`md/varc`: a variable-step study of 2000 steps, extended, the
    extension stopped at its step 5000 row with its checkpoint at 3000,
    then carried on from that checkpoint for 3000 steps."""
    root = tmp_path / "varc"
    own = _piece(root, None)
    _record(own, {**VARIABLE, "duration_ns": 0.004},
            {"nvt_steps": 500, "npt_steps": 800, "production_steps": 2000})
    _state(own, 2000, 3.2888138923476338)
    _checkpoint(own, 2000, finished=True)
    _energy(own, [(1000, 1.6444069461737851), (2000, 3.2888138923476338)])
    stopped = _piece(root, "segment-001")
    _checkpoint(stopped, 3000, finished=False)
    _energy(stopped, [(1000, 1.681351687221168), (2000, 3.359387647361879),
                      (3000, 5.035231373464441), (4000, 6.70353640214828),
                      (5000, 8.371598711094975)])
    last = _piece(root, "segment-002")
    _record(last, {**VARIABLE, "duration_ns": 0.006, "nvt_steps": 0, "npt_steps": 0},
            {"nvt_steps": 0, "npt_steps": 0, "production_steps": 3000})
    _state(last, 3000, 5.002518749544943)
    _checkpoint(last, 3000, finished=True)
    return root


class TestAPieceStoppedBetweenCheckpoints:

    def test_its_clock_is_read_at_the_checkpoint_the_next_continued_from(
            self, tmp_path: Path) -> None:
        said = recorded_stage_lengths(_varc(tmp_path))
        # 3.289 + 5.035 + 5.003 ps; the energy log's last line gave 16.66.
        assert said["production"]["ns"] == 0.013326564
        assert said["production"]["source"] == FROM_CLOCK
        assert said["production_ran_ns"] == 0.013326564
        assert said["pieces"] == 3

    def test_the_methods_give_the_same_length(self, tmp_path: Path) -> None:
        root = _varc(tmp_path)
        record = json.loads((root / "simulation" / "simulation_parameters.json")
                            .read_text(encoding="utf-8"))
        text = methods_paragraphs(root, {}, record)
        assert ("Production dynamics were run for 13.33 ps (the length by the "
                "integrator's own clock, summed over 3 pieces)") in text
        assert "16.7 ps" not in text

    def test_a_checkpoint_with_no_row_at_its_step_is_not_recorded(
            self, tmp_path: Path) -> None:
        root = _varc(tmp_path)
        _energy(root / "segment-001" / "simulation",
                [(1000, 1.681351687221168), (2000, 3.359387647361879),
                 (4000, 6.70353640214828), (5000, 8.371598711094975)])
        production = recorded_stage_lengths(root)["production"]
        assert production["ns"] is None and production["source"] == NOT_RECORDED
        assert "segment-001" in production["said"]
        assert "step 3,000" in production["said"]


def _intrv(tmp_path: Path, *, checkpoint: int, rows: list[tuple[int, float]],
           last: tuple[int, float]) -> Path:
    """`md/intrv` and `intrv2`: a variable-step study of 0.012 ns stopped in
    its production before writing its record, then carried on with
    `fastmdx resume` from its checkpoint."""
    root = tmp_path / "intrv"
    root.mkdir(parents=True)
    _config(root, {**VARIABLE, "duration_ns": 0.012, "nvt_duration_ns": 0.001,
                   "npt_duration_ns": 0.0016, "nvt_steps": None, "npt_steps": None,
                   "pressure_bar": None})
    own = _piece(root, None)
    _checkpoint(own, checkpoint, finished=False)
    _energy(own, rows)
    piece = _piece(root, "segment-001")
    _record(piece, {**VARIABLE, "duration_ns": 0.012 - checkpoint * 2e-6,
                    "nvt_steps": 0, "npt_steps": 0},
            {"nvt_steps": 0, "npt_steps": 0, "production_steps": 6000 - checkpoint})
    _state(piece, *last)
    _checkpoint(piece, last[0], finished=True)
    return root


INTRV = {"checkpoint": 4000,
         "rows": [(1000, 1.6518022704001654), (2000, 3.3036045408003476),
                  (3000, 4.95540681120053), (4000, 6.607209081600712)],
         "last": (2000, 3.350194230255038)}
INTRV2 = {"checkpoint": 3000,
          "rows": [(1000, 1.6361772159938635), (2000, 3.272354431987714),
                   (3000, 4.908531647981811), (4000, 6.544708863976106),
                   (5000, 8.1808860799704)],
          "last": (3000, 5.016953544120606)}


class TestAVariableStepStudyWithNoRecordOfItsOwn:

    def test_its_production_is_its_clocks(self, tmp_path: Path) -> None:
        for name, study, ns in (("intrv", INTRV, 0.009957403),
                                ("intrv2", INTRV2, 0.009925485)):
            said = recorded_stage_lengths(_intrv(tmp_path / name, **study))
            assert said["record"] == NOT_RECORDED
            assert said["read_from"]["integrator"] == "resolved_config.yml"
            assert said["production"]["ns"] == ns, name
            assert said["production"]["source"] == FROM_CLOCK
            assert said["production_ran_ns"] == ns

    def test_the_methods_give_its_protocol_by_its_clocks(self, tmp_path: Path) -> None:
        for name, study, ps in (("intrv", INTRV, "9.96 ps"), ("intrv2", INTRV2, "9.93 ps")):
            text = methods_paragraphs(_intrv(tmp_path / name, **study), {}, {})
            assert f"Production dynamics were run for {ps}" in text, name
            assert "500 steps in the NVT ensemble followed by 800 steps" in text
            assert "simulation did not run" not in text
            assert "12 ps" not in text


def _intr(tmp_path: Path) -> Path:
    """`md/intr`: a fixed-step study set to nvt 0.001, npt 0.0016 and
    production 0.012 ns, stopped after its step 4000 checkpoint before
    writing its record, then carried on with `fastmdx resume`."""
    root = tmp_path / "intr"
    root.mkdir(parents=True)
    fixed = {"integrator": "langevin_middle", "timestep_fs": 2.0, "ensemble": "npt",
             "temperature_K": 300.0, "friction_per_ps": 1.0,
             "trajectory_interval_steps": 100, "checkpoint_interval_steps": 1000}
    _config(root, {**fixed, "duration_ns": 0.012, "nvt_duration_ns": 0.001,
                   "npt_duration_ns": 0.0016, "nvt_steps": None, "npt_steps": None})
    own = _piece(root, None)
    _checkpoint(own, 4000, finished=False)
    _energy(own, [(1000, 2.0000000000000013), (2000, 3.999999999999781),
                  (3000, 5.999999999999561), (4000, 7.999999999999341)])
    piece = _piece(root, "segment-001")
    _config(piece.parent, {**fixed, "duration_ns": 0.004, "nvt_steps": 0, "npt_steps": 0,
                           "resume_from": str(own / "checkpoint.chk")})
    _record(piece, {**fixed, "duration_ns": 0.004, "nvt_steps": 0, "npt_steps": 0},
            {"nvt_steps": 0, "npt_steps": 0, "production_steps": 2000})
    _state(piece, 2000, 3.999999999999781)
    _checkpoint(piece, 2000, finished=True)
    (root / "joined").mkdir()
    (root / "joined" / "joined.json").write_text(json.dumps(
        {"segments": [0, 1], "frames": 60, "trimmed": {"0": 40}}), encoding="utf-8")
    return root


class TestAFixedStepStudyWithNoRecordOfItsOwn:

    def test_its_stages_and_timestep_are_read_from_its_config(self, tmp_path: Path) -> None:
        said = recorded_stage_lengths(_intr(tmp_path))
        assert said["record"] == NOT_RECORDED
        assert said["timestep_fs"] == 2.0
        assert said["read_from"]["timestep_fs"] == "resolved_config.yml"
        assert all(said["read_from"][stage] == "resolved_config.yml"
                   for stage in ("nvt", "npt", "production"))
        for stage, ns, steps in (("nvt", 0.001, 500), ("npt", 0.0016, 800)):
            assert {k: said[stage][k] for k in ("ns", "steps", "source")} == {
                "ns": ns, "steps": steps, "source": SET}
            assert "resolved_config.yml" in said[stage]["said"]

    def test_its_production_and_frames_are_as_before(self, tmp_path: Path) -> None:
        """A guard, not a defect test: what was right before the fix stays
        right."""
        said = recorded_stage_lengths(_intr(tmp_path))
        assert said["production_ran_ns"] == 0.012
        assert said["production_ran_source"] == "summed over pieces"
        assert said["frames_saved"] == 60

    def test_the_methods_give_its_protocol(self, tmp_path: Path) -> None:
        text = methods_paragraphs(_intr(tmp_path), {}, {})
        assert "Equilibration comprised 1 ps in the NVT ensemble followed by 1.6 ps" in text
        assert "a 2.0 fs timestep" in text
        assert "simulation did not run" not in text


def test_a_record_without_its_plan_says_its_stages_are_not_recorded(
        tmp_path: Path) -> None:
    """`md/old2`: a record from before `resolved` was written."""
    record = {"parameters": {"integrator": "langevin_middle", "timestep_fs": 2.0,
                             "duration_ns": 0.004, "nvt_duration_ns": None,
                             "npt_duration_ns": None, "ensemble": "npt"}}
    text = methods_paragraphs(tmp_path, {}, record)
    assert "The lengths of the NVT and NPT equilibration stages are not recorded." in text
    assert "Production dynamics were run for 4 ps" in text


def test_a_variable_step_record_gives_production_by_its_clock(tmp_path: Path) -> None:
    """`md/var`: its own record said `duration_ns` null beside a
    `duration_ns_actual` of steps times the timestep."""
    from fastmdxplora.simulation.pipeline import _write_manifest

    folder = tmp_path / "simulation"
    folder.mkdir()
    _state(folder, 2000, 3.2735174)
    _checkpoint(folder, 2000, finished=True)
    _write_manifest(folder, {**VARIABLE, "duration_ns": 0.004}, [], [],
                    platform_used="CPU", n_frames=20, duration_ns_actual=0.004,
                    resolved={"nvt_steps": 500, "npt_steps": 800, "production_steps": 2000})
    written = json.loads((folder / "simulation_parameters.json")
                         .read_text(encoding="utf-8"))["stage_lengths"]
    assert written["duration_ns"] == 0.003273517
    assert written["sources"]["duration_ns"] == FROM_CLOCK


def test_a_variable_step_record_without_its_clock_says_why(tmp_path: Path) -> None:
    from fastmdxplora.simulation.pipeline import _write_manifest

    folder = tmp_path / "simulation"
    folder.mkdir()
    _write_manifest(folder, {**VARIABLE, "duration_ns": 0.004}, [], [],
                    platform_used=None)
    written = json.loads((folder / "simulation_parameters.json")
                         .read_text(encoding="utf-8"))["stage_lengths"]
    assert written["duration_ns"] is None
    assert written["sources"]["duration_ns"].startswith(NOT_RECORDED + ": ")
    assert "chose its own step" in written["sources"]["duration_ns"]


def test_a_variable_step_production_is_said_in_a_whole_sentence(tmp_path: Path) -> None:
    root = tmp_path / "var"
    own = _piece(root, None)
    _record(own, {**VARIABLE, "duration_ns": 0.004},
            {"nvt_steps": 500, "npt_steps": 800, "production_steps": 2000})
    _state(own, 2000, 3.2735174)
    _checkpoint(own, 2000, finished=True)
    said = recorded_stage_lengths(root)["production"]["said"]
    assert said == "The integrator chose its own step, so its length is the run's own clock."


# ---- Where the config is not what ran, and what else the study holds ----


def test_a_study_whose_simulation_never_ran_is_not_read_from_its_config(
        tmp_path: Path) -> None:
    """`f1/c5`: a config, written as every study starts, and no piece: what
    was asked, not what ran."""
    root = tmp_path / "c5"
    root.mkdir()
    _config(root, {"integrator": "langevin_middle", "timestep_fs": 2.0,
                   "duration_ns": 2.0, "nvt_duration_ns": 0.5, "npt_duration_ns": 1.0})
    said = recorded_stage_lengths(root)
    for stage in ("nvt", "npt", "production"):
        assert said[stage] == {"ns": None, "steps": None, "source": NOT_RECORDED}
    assert not any(said["read_from"].values())


def _pc(tmp_path: Path) -> Path:
    """`f3/pc`: the first piece stopped, one piece carried on, and the
    study's config the piece's, as `resume._extend_study` leaves it while
    the joined trajectory is analysed (no equilibration, the piece's
    production)."""
    root = tmp_path / "pc"
    root.mkdir(parents=True)
    fixed = {"integrator": "langevin_middle", "timestep_fs": 2.0, "ensemble": "npt"}
    own = _piece(root, None)
    _config(root, {**fixed, "duration_ns": 0.004, "nvt_duration_ns": None,
                   "npt_duration_ns": None, "nvt_steps": 0, "npt_steps": 0,
                   "minimize": False, "resume_from": str(own / "checkpoint.chk")})
    _checkpoint(own, 4000, finished=False)
    _energy(own, [(1000, 2.0), (2000, 4.0), (3000, 6.0), (4000, 8.0)])
    piece = _piece(root, "segment-001")
    _record(piece, {**fixed, "duration_ns": 0.004, "nvt_steps": 0, "npt_steps": 0},
            {"nvt_steps": 0, "npt_steps": 0, "production_steps": 2000})
    _state(piece, 2000, 4.0)
    _checkpoint(piece, 2000, finished=True)
    return root


def test_a_piece_s_config_in_the_study_s_place_never_gives_a_stage_as_0(
        tmp_path: Path) -> None:
    said = recorded_stage_lengths(_pc(tmp_path))
    for stage in ("nvt", "npt"):
        assert said[stage]["ns"] is None and said[stage]["source"] == NOT_RECORDED
        assert said["read_from"][stage] is None
    assert said["read_from"]["integrator"] == "segment-001/simulation/simulation_parameters.json"
    text = methods_paragraphs(_pc(tmp_path / "again"), {}, {})
    assert "The lengths of the NVT and NPT equilibration stages are not recorded." in text
    assert "0 ps" not in text


def _intru(tmp_path: Path, *, status: bool = True) -> Path:
    """`md/intru`: fixed step, NVT and NPT left unset as the JCC runs leave
    them, production 0.012 ns, stopped after its step 4000 checkpoint
    before writing its record, then carried on. The plan its first piece
    worked out is in its live status and its log."""
    root = _intr(tmp_path)
    _config(root, {"integrator": "langevin_middle", "timestep_fs": 2.0, "ensemble": "npt",
                   "temperature_K": 300.0, "friction_per_ps": 1.0,
                   "trajectory_interval_steps": 100, "duration_ns": 0.012,
                   "nvt_duration_ns": None, "npt_duration_ns": None,
                   "nvt_steps": None, "npt_steps": None, "resume_from": None})
    if status:
        (root / "simulation" / "live_status.json").write_text(json.dumps(
            {"current_step": 5295, "timestep_fs": 2.0, "nvt_steps_planned": 500,
             "npt_steps_planned": 800, "production_steps_planned": 6000}), encoding="utf-8")
    (root / "simulation" / "simulation.log").write_text(
        "NVT equilibration: 500 steps\nNPT equilibration: 800 steps\n"
        "Production: 6,000 steps\n", encoding="utf-8")
    return root


def test_an_unset_equilibration_is_read_from_the_first_piece_s_plan(
        tmp_path: Path) -> None:
    for status, record in ((True, "simulation/live_status.json"),
                           (False, "simulation/simulation.log")):
        said = recorded_stage_lengths(_intru(tmp_path / str(status), status=status))
        for stage, ns, steps in (("nvt", 0.001, 500), ("npt", 0.0016, 800)):
            assert {k: said[stage][k] for k in ("ns", "steps", "source")} == {
                "ns": ns, "steps": steps, "source": FROM_STEPS}, record
            assert said["read_from"][stage] == record
            assert record in said[stage]["said"]
        assert said["read_from"]["production"] == "resolved_config.yml"
    text = methods_paragraphs(_intru(tmp_path / "methods"), {}, {})
    assert "Equilibration comprised 1 ps in the NVT ensemble followed by 1.6 ps" in text


def test_the_config_s_length_is_the_study_s_whole(tmp_path: Path) -> None:
    said = recorded_stage_lengths(_intr(tmp_path))["production"]["said"]
    assert "first piece's plan" not in said
    assert "the study's whole production, as its config gives it" in said


def _linked_out(path: Path, outside: Path) -> None:
    import shutil

    target = outside / path.name
    shutil.move(str(path), str(target))
    path.symlink_to(target)


def test_a_piece_linked_out_of_the_study_is_not_read(tmp_path: Path) -> None:
    for name in ("segment-001", "simulation/checkpoint.chk.json"):
        root = _intrv(tmp_path / name.replace("/", "_"), **INTRV)
        outside = tmp_path / ("outside_" + name.replace("/", "_"))
        outside.mkdir()
        _linked_out(root / name, outside)
        said = recorded_stage_lengths(root)
        assert said["production"]["source"] == "not read", name
        assert "linked out of the study" in said["production"]["said"]
        assert said["production_ran_source"] == "not read"


def test_a_stopped_last_piece_is_said_to_its_last_checkpoint(tmp_path: Path) -> None:
    """`f1/c2b`: a study carried on, its last piece stopped at its step 5000
    row with its checkpoint at 3000, not yet carried on again."""
    root = tmp_path / "c2b"
    own = _piece(root, None)
    _record(own, {**VARIABLE, "duration_ns": 0.004},
            {"nvt_steps": 500, "npt_steps": 800, "production_steps": 2000})
    _state(own, 2000, 3.29)
    _checkpoint(own, 2000, finished=True)
    last = _piece(root, "segment-001")
    _checkpoint(last, 3000, finished=False)
    _energy(last, [(1000, 1.68), (2000, 3.36), (3000, 5.03), (4000, 6.7), (5000, 8.37)])
    production = recorded_stage_lengths(root)["production"]
    assert production["ns"] == 0.00832
    assert "the last piece stopped before its end" in production["said"]
    record = json.loads((own / "simulation_parameters.json").read_text(encoding="utf-8"))
    text = methods_paragraphs(root, {}, record)
    assert "8.32 ps (the length by the integrator's own clock, summed over 2 pieces; " \
           "the last piece to its last checkpoint)" in text


def test_a_clock_without_steps_is_said_by_the_methods(tmp_path: Path) -> None:
    """`f1/c4a`: a config that does not read, a variable-step piece carried
    on: the reader gave the clock, the methods said not recorded."""
    root = tmp_path / "c4a"
    root.mkdir()
    (root / "resolved_config.yml").write_text("simulation: [unclosed\n  - : :",
                                              encoding="utf-8")
    own = _piece(root, None)
    _checkpoint(own, 4000, finished=False)
    _energy(own, [(4000, 6.6)])
    piece = _piece(root, "segment-001")
    _record(piece, {**VARIABLE, "duration_ns": 0.004, "nvt_steps": 0, "npt_steps": 0},
            {"nvt_steps": 0, "npt_steps": 0, "production_steps": 2000})
    _state(piece, 2000, 3.35)
    _checkpoint(piece, 2000, finished=True)
    assert recorded_stage_lengths(root)["production"]["ns"] == 0.00995
    text = methods_paragraphs(root, {}, {})
    assert "Production dynamics were run for 9.95 ps" in text
    assert "The length of production is not recorded." not in text


def test_a_summed_production_not_recorded_gives_no_steps_as_its_own(
        tmp_path: Path) -> None:
    root = _varc(tmp_path)
    _energy(root / "segment-001" / "simulation", [(1000, 1.681351687221168)])
    production = recorded_stage_lengths(root)["production"]
    assert production["steps"] is None and production["first_piece_steps"] == 2000
    record = json.loads((root / "simulation" / "simulation_parameters.json")
                        .read_text(encoding="utf-8"))
    text = methods_paragraphs(root, {}, record)
    assert ("Production ran in the NPT ensemble, in 3 pieces each continuing from the "
            "checkpoint of the one before; its length in time is not recorded.") in text
    assert "extended length" not in text


def test_a_record_names_itself_only_for_what_it_holds(tmp_path: Path) -> None:
    own = _piece(tmp_path, None)
    (own / "simulation_parameters.json").write_text(json.dumps(
        {"parameters": {"integrator": "langevin_middle", "timestep_fs": 2.0,
                        "duration_ns": 0.004}}), encoding="utf-8")
    read_from = recorded_stage_lengths(tmp_path)["read_from"]
    assert read_from["integrator"] == read_from["production"] == \
        "simulation/simulation_parameters.json"
    assert read_from["pressure"] is None and read_from["nvt"] is None
