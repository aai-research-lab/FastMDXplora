"""A study carried on in pieces reads what ran wherever it is read from.

A study stopped during a resume's analysis keeps its last piece's config in
place of its own (no equilibration, the production that piece ran), and a
later resume does not put the study's back. That config was told from the
study's own by the path its `resume_from` names, which is the study's old
path once it is moved or copied off the cluster, or reached through a link:
NVT and NPT then read as 0 ns, and the methods left out the minimisation
and the equilibration. A study named through a link read nothing at all.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastmdxplora.report.methods import methods_paragraphs
from fastmdxplora.simulation.lengths import (
    FROM_STEPS,
    NOT_READ,
    recorded_stage_lengths,
)
from tests.test_a_study_carried_on_after_a_stop_gives_its_lengths import (
    INTRV,
    VARIABLE,
    _checkpoint,
    _config,
    _energy,
    _intr,
    _intru,
    _intrv,
    _piece,
    _record,
    _state,
)

#: The run settings `md/uA`'s first piece's record holds.
SETTINGS = {"integrator": "langevin_middle", "timestep_fs": 2.0, "ensemble": "npt",
            "temperature_K": 300.0, "friction_per_ps": 1.0, "barostat_frequency": 25,
            "trajectory_interval_steps": 100, "platform": "CPU"}


def _with_its_piece_s_config(tmp_path: Path) -> Path:
    """`md/uA`: `md/intru` stopped again during its resume's analysis, so
    its config is its piece's, naming the study's own checkpoint."""
    root = _intru(tmp_path)
    (root / "simulation" / "state_minimized.xml").write_text("<State/>\n", encoding="utf-8")
    piece = root / "segment-001" / "simulation"
    (piece / "simulation_parameters.json").write_text(json.dumps(
        {"phase": "simulation",
         "parameters": {**SETTINGS, "duration_ns": 0.004, "nvt_steps": 0, "npt_steps": 0},
         "resolved": {"nvt_steps": 0, "npt_steps": 0, "production_steps": 2000},
         "pressure_bar_used": 1.0, "barostat": "isotropic", "platform_used": "CPU"}),
        encoding="utf-8")
    _config(root, {**SETTINGS, "duration_ns": 0.004, "nvt_duration_ns": None,
                   "npt_duration_ns": None, "nvt_steps": 0, "npt_steps": 0,
                   "minimize": False, "resume_unsealed": True,
                   "resume_from": str(root.resolve() / "simulation" / "checkpoint.chk")})
    return root


def _reads_what_ran(root: Path) -> None:
    said = recorded_stage_lengths(root)
    for stage, ns, steps in (("nvt", 0.001, 500), ("npt", 0.0016, 800)):
        assert {k: said[stage][k] for k in ("ns", "steps", "source")} == {
            "ns": ns, "steps": steps, "source": FROM_STEPS}, stage
        assert said["read_from"][stage] == "simulation/live_status.json"
    text = methods_paragraphs(root, {}, {})
    assert "The system was energy-minimized before dynamics." in text
    assert "Equilibration comprised 1 ps in the NVT ensemble followed by 1.6 ps" in text


def test_a_moved_study_s_piece_config_is_not_taken_for_its_own(tmp_path: Path) -> None:
    """`/tmp/A1f/mv/uA` and `o1_moved`: copied to another folder, its config
    names the old one."""
    old = _with_its_piece_s_config(tmp_path / "hpc")
    moved = tmp_path / "workstation" / "intr"
    shutil.copytree(old, moved, symlinks=True)
    shutil.rmtree(old)
    _reads_what_ran(moved)


def test_a_study_reached_through_a_link_reads_what_ran(tmp_path: Path) -> None:
    """`via_link`: a home folder that is a link, as on many clusters."""
    real = _with_its_piece_s_config(tmp_path / "real")
    link = tmp_path / "via_link"
    link.symlink_to(real, target_is_directory=True)
    _reads_what_ran(link)


def test_a_one_go_study_named_through_a_link_gives_its_lengths(tmp_path: Path) -> None:
    """`via_def`: it read "not read" for every stage."""
    real = tmp_path / "def"
    own = _piece(real, None)
    _record(own, {"integrator": "langevin_middle", "timestep_fs": 2.0, "duration_ns": 0.004},
            {"nvt_steps": 500, "npt_steps": 800, "production_steps": 2000})
    link = tmp_path / "via_def"
    link.symlink_to(real, target_is_directory=True)
    said = recorded_stage_lengths(link)
    assert said["record"] == "read"
    assert [said[stage]["ns"] for stage in ("nvt", "npt", "production")] == [
        0.001, 0.0016, 0.004]


def test_a_link_inside_a_study_named_through_a_link_is_still_refused(
        tmp_path: Path) -> None:
    """A guard, not a defect test: the hold, taken to the real folder,
    still refuses what that folder links out."""
    real = tmp_path / "def"
    _piece(real, None)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"parameters": {"timestep_fs": 2.0, "duration_ns": 9.0}}),
                       encoding="utf-8")
    (real / "simulation" / "simulation_parameters.json").symlink_to(outside)
    link = tmp_path / "via_def"
    link.symlink_to(real, target_is_directory=True)
    said = recorded_stage_lengths(link)
    assert said["record"] == NOT_READ and said["production"]["ns"] is None


def test_the_methods_give_the_run_settings_its_first_piece_holds(tmp_path: Path) -> None:
    text = methods_paragraphs(_with_its_piece_s_config(tmp_path), {}, {})
    assert "at 300.0 K with a friction coefficient of 1.0 ps" in text
    assert "with the barostat applied every 25 steps" in text
    assert "Coordinates were written every 0.2 ps." in text
    assert "on the CPU platform" in text
    said = recorded_stage_lengths(_with_its_piece_s_config(tmp_path / "again"))
    piece = "segment-001/simulation/simulation_parameters.json"
    assert said["read_from"]["settings"] == {
        "temperature_K": piece, "friction_per_ps": piece, "barostat_frequency": piece,
        "trajectory_interval_steps": piece, "platform": piece}


def test_a_study_stopped_and_not_carried_on_did_run(tmp_path: Path) -> None:
    """`md/uL.k1`: read before `fastmdx resume`."""
    own = _piece(tmp_path, None)
    _checkpoint(own, 4000, finished=False)
    _energy(own, [(1000, 2.0), (4000, 8.0)])
    text = methods_paragraphs(tmp_path, {}, {})
    assert "simulation did not run" not in text
    assert "the simulation stopped before writing its record" in text


def test_a_clock_under_1_ns_keeps_its_decimals(tmp_path: Path) -> None:
    """`vN`: 10.0183 ps by its clock, said "10 ps"."""
    root = tmp_path / "vN"
    own = _piece(root, None)
    record = {"parameters": {**VARIABLE, "duration_ns": 0.012},
              "resolved": {"nvt_steps": 2000, "npt_steps": 2000, "production_steps": 6000}}
    (own / "simulation_parameters.json").write_text(json.dumps(record), encoding="utf-8")
    _state(own, 6000, 10.0183)
    _checkpoint(own, 6000, finished=True)
    assert "Production dynamics were run for 10.02 ps" in methods_paragraphs(root, {}, record)


def test_both_status_records_linked_out_are_not_read(tmp_path: Path) -> None:
    """`o4_both`."""
    root = _intru(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    for name in ("live_status.json", "simulation.log"):
        shutil.move(str(root / "simulation" / name), str(outside / name))
        (root / "simulation" / name).symlink_to(outside / name)
    said = recorded_stage_lengths(root)
    for stage in ("nvt", "npt"):
        assert said[stage]["source"] == NOT_READ and said[stage]["ns"] is None
        assert "linked out of the study" in said[stage]["said"]


def test_the_plan_beside_a_clock_read_from_the_config_is_planned_steps(
        tmp_path: Path) -> None:
    """A guard, not a defect test: `planned_steps`, which a study read from
    its config gives, is now documented and held to that."""
    production = recorded_stage_lengths(_intrv(tmp_path, **INTRV))["production"]
    assert production["steps"] is None and production["planned_steps"] == 6000
    assert "the whole study's plan, as its config gives it" in production["said"]


def test_a_production_read_from_the_live_status_is_the_first_piece_s_plan(
        tmp_path: Path) -> None:
    said = recorded_stage_lengths(_with_its_piece_s_config(tmp_path))["production"]
    assert said["ns"] == 0.012 and "the first piece's plan" in said["said"]
    assert "simulation/live_status.json" in said["said"]


def test_a_later_piece_s_live_status_in_the_study_s_folder_is_not_taken(
        tmp_path: Path) -> None:
    """`o2_later`: the log, which is the first piece's, is read instead."""
    root = _intru(tmp_path)
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"nvt_steps_planned": 0, "npt_steps_planned": 0, "production_steps_planned": 2000,
         "current_checkpoint_path": str(root / "segment-001" / "simulation"
                                        / "checkpoint.chk")}), encoding="utf-8")
    said = recorded_stage_lengths(root)
    assert said["nvt"]["steps"] == 500 and said["npt"]["steps"] == 800
    assert said["read_from"]["nvt"] == "simulation/simulation.log"


def test_pieces_are_counted_with_a_middle_one_linked_out(tmp_path: Path) -> None:
    """`o6_mid_fx2`: three pieces, the middle one's folder linked out."""
    root = _intr(tmp_path)
    middle = root / "segment-002" / "simulation"
    shutil.copytree(root / "segment-001" / "simulation", middle)
    outside = tmp_path / "outside"
    shutil.move(str(root / "segment-001"), str(outside))
    (root / "segment-001").symlink_to(outside, target_is_directory=True)
    said = recorded_stage_lengths(root)
    assert said["production_ran_source"] == NOT_READ
    assert said["pieces"] == 3


def test_no_stage_given_in_steps_is_not_said_to_be(tmp_path: Path) -> None:
    """`f1/c4a`: neither stage given, so not "given in steps"."""
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
    text = methods_paragraphs(root, {}, {})
    assert "The lengths of the NVT and NPT equilibration stages are not recorded." in text
    assert "equilibration is given in steps" not in text


def test_the_pieces_are_named_once(tmp_path: Path) -> None:
    text = methods_paragraphs(_intrv(tmp_path, **INTRV), {}, {})
    assert ("9.96 ps (the length by the integrator's own clock, summed over 2 pieces) in "
            "the NPT ensemble, each piece continuing from the checkpoint of the one "
            "before.") in text


# ---- Whose platform, the decimals at every scale, and where a study began ----


def _ran_on(root: Path, first: str | None, *later: tuple[str, str | None]) -> Path:
    """The first piece's live status naming the platform it ran on, and each
    later piece's record naming its own (None: no record)."""
    status = root / "simulation" / "live_status.json"
    whole = json.loads(status.read_text(encoding="utf-8"))
    if first is not None:
        whole.update({"platform": first, "precision": "mixed", "precision_applied": True})
    status.write_text(json.dumps(whole), encoding="utf-8")
    for name, used in later:
        record = root / name / "simulation" / "simulation_parameters.json"
        if used is None:
            record.unlink()
            continue
        document = json.loads(record.read_text(encoding="utf-8"))
        document["platform_used"] = used
        record.write_text(json.dumps(document), encoding="utf-8")
    return root


def test_the_platform_is_the_first_piece_s_own(tmp_path: Path) -> None:
    """`f/pl2`: `platform: auto`, the first piece on CUDA, `segment-001` on
    the CPU. It was said "on the CPU platform", from a config that did not
    give it."""
    root = _ran_on(_intru(tmp_path), "CUDA", ("segment-001", "CPU"))
    text = methods_paragraphs(root, {}, {})
    assert "on the CPU platform" not in text and "on the CUDA platform" not in text
    assert "The platform differed between pieces: CUDA for the first piece, CPU for segment-001." \
        in text
    platform = recorded_stage_lengths(root)["read_from"]["settings"]["platform"]
    assert platform.startswith("simulation/live_status.json")


def test_the_platform_with_the_config_set_aside_is_the_first_piece_s(tmp_path: Path) -> None:
    """`f/pl1`: `md/uA`, its config its piece's."""
    root = _ran_on(_with_its_piece_s_config(tmp_path), "CUDA", ("segment-001", "CPU"))
    text = methods_paragraphs(root, {}, {})
    assert "on the CPU platform" not in text
    assert "CUDA for the first piece, CPU for segment-001" in text


def test_a_platform_is_not_taken_from_past_a_piece_without_a_record(tmp_path: Path) -> None:
    """`f/pl3`: `segment-001` stopped before writing its record; it took
    `segment-002`'s, the third piece's."""
    root = _intru(tmp_path)
    shutil.copytree(root / "segment-001", root / "segment-002")
    _ran_on(root, "CUDA", ("segment-001", None), ("segment-002", "CPU"))
    text = methods_paragraphs(root, {}, {})
    assert "on the CPU platform" not in text
    assert "CUDA for the first piece, CPU for segment-002" in text


def test_one_platform_throughout_is_said_from_the_first_piece(tmp_path: Path) -> None:
    root = _ran_on(_intru(tmp_path), "CUDA", ("segment-001", "CUDA"))
    text = methods_paragraphs(root, {}, {})
    assert "Simulations used OpenMM on the CUDA platform in mixed precision." in text
    settings = recorded_stage_lengths(root)["read_from"]["settings"]
    assert settings["platform"] == settings["precision"] == "simulation/live_status.json"
    assert "barostat_frequency" not in settings


def _variable_clock(tmp_path: Path, ps: float) -> str:
    own = _piece(tmp_path, None)
    record = {"parameters": {**VARIABLE, "duration_ns": 20.0},
              "resolved": {"nvt_steps": 500, "npt_steps": 800, "production_steps": 1000}}
    (own / "simulation_parameters.json").write_text(json.dumps(record), encoding="utf-8")
    _state(own, 1000, ps)
    _checkpoint(own, 1000, finished=True)
    return methods_paragraphs(tmp_path, {}, record)


def test_a_clock_keeps_its_decimals_at_every_scale(tmp_path: Path) -> None:
    for ps, said in ((10018.3, "10.02 ns"), (19960.0, "19.96 ns"), (999.996, "1.00 ns"),
                     (123.456, "123.46 ps")):
        text = _variable_clock(tmp_path / str(ps), ps)
        assert f"Production dynamics were run for {said} (" in text, said
    assert "1000.00 ps" not in _variable_clock(tmp_path / "edge", 999.996)


def test_a_length_set_in_ns_is_said_as_before(tmp_path: Path) -> None:
    """A guard, not a defect test: the clock's two decimals leave a length
    set in ns as it was said."""
    record = {"parameters": {"integrator": "langevin_middle", "timestep_fs": 2.0,
                             "duration_ns": 20.0, "nvt_duration_ns": 0.5,
                             "npt_duration_ns": 1.0}}
    text = methods_paragraphs(tmp_path, {}, record)
    assert "Equilibration comprised 500 ps in the NVT ensemble followed by 1 ns" in text
    assert "Production dynamics were run for 20 ns" in text


def _started_elsewhere(tmp_path: Path, *, status: bool) -> Path:
    """`f/cont1`, `cont1_nostatus`: a study started from another study's
    production checkpoint, its first piece stopped and carried on."""
    root = _intr(tmp_path)
    parent = tmp_path / "private" / "parent"
    _config(root, {"integrator": "langevin_middle", "timestep_fs": 2.0, "ensemble": "nvt",
                   "duration_ns": 0.012, "nvt_steps": 0, "npt_steps": 0, "minimize": False,
                   "resume_unsealed": False,
                   "resume_from": str(parent / "simulation" / "checkpoint.chk")})
    if status:
        (root / "simulation" / "live_status.json").write_text(json.dumps(
            {"nvt_steps_planned": 0, "npt_steps_planned": 0,
             "production_steps_planned": 6000}), encoding="utf-8")
    return root


def test_a_study_started_elsewhere_ran_no_equilibration(tmp_path: Path) -> None:
    for status in (True, False):
        root = _started_elsewhere(tmp_path / str(status), status=status)
        said = recorded_stage_lengths(root)
        for stage in ("nvt", "npt"):
            assert {k: said[stage][k] for k in ("ns", "steps", "source")} == {
                "ns": 0.0, "steps": 0, "source": FROM_STEPS}, status
        text = methods_paragraphs(root, {}, {})
        assert ("Production continued from the checkpoint of the study `parent`; no "
                "minimisation or equilibration was run.") in text
        assert "private" not in text and str(tmp_path) not in text
        assert "not recorded" not in text.split("**Not recorded.**")[0]


def test_a_missing_live_status_beside_a_log_linked_out_is_not_read(tmp_path: Path) -> None:
    root = _intru(tmp_path)
    (root / "simulation" / "live_status.json").unlink()
    outside = tmp_path / "outside.log"
    shutil.move(str(root / "simulation" / "simulation.log"), str(outside))
    (root / "simulation" / "simulation.log").symlink_to(outside)
    said = recorded_stage_lengths(root)
    assert said["nvt"]["source"] == said["npt"]["source"] == NOT_READ


def test_a_variable_step_stage_of_no_steps_is_no_time() -> None:
    from fastmdxplora.simulation.lengths import stage_lengths

    said = stage_lengths({"parameters": {**VARIABLE, "duration_ns": 0.008},
                          "resolved": {"nvt_steps": 0, "npt_steps": 0,
                                       "production_steps": 4000}})
    assert said["nvt"] == said["npt"] == {"ns": 0.0, "steps": 0, "source": FROM_STEPS}
    assert said["production"]["ns"] is None


# ---- A platform said by its piece, where a study began, and the macOS layout ----


def _segments_ran_on(root: Path, *used: str) -> Path:
    """`segment-001` onward, each with its record naming the platform it
    ran on."""
    for index, platform in enumerate(used, start=1):
        folder = root / f"segment-{index:03d}"
        if not folder.exists():
            shutil.copytree(root / "segment-001", folder)
        record = folder / "simulation" / "simulation_parameters.json"
        document = json.loads(record.read_text(encoding="utf-8"))
        document["platform_used"] = platform
        record.write_text(json.dumps(document), encoding="utf-8")
    return root


def test_a_segment_s_platform_is_not_said_as_the_first_piece_s(tmp_path: Path) -> None:
    """`/tmp/A1i/s/h_nostatus_differ`: no live status, as `live_telemetry:
    false` leaves it; `segment-001` on the CPU, `segment-002` on CUDA. It
    said "CPU for the first piece"."""
    root = _segments_ran_on(_intru(tmp_path, status=False), "CPU", "CUDA")
    text = methods_paragraphs(root, {}, {})
    assert ("The first piece's platform is not recorded; segment-001 ran on CPU, "
            "segment-002 on CUDA.") in text
    assert "for the first piece" not in text


def test_segments_that_agree_are_not_said_for_the_study(tmp_path: Path) -> None:
    """`h_nostatus_same`: it said "on the CPU platform" of the study."""
    root = _segments_ran_on(_intru(tmp_path, status=False), "CPU", "CPU")
    text = methods_paragraphs(root, {}, {})
    assert ("Simulations used OpenMM on the CPU platform for segment-001 to segment-002; "
            "the first piece's platform is not recorded.") in text


def test_a_live_status_linked_out_gives_no_platform_for_the_first_piece(
        tmp_path: Path) -> None:
    """`h_status_linked`, read in a hold, as the GUI reads it."""
    from fastmdxplora import record_access

    root = _segments_ran_on(_intru(tmp_path), "CPU", "CPU")
    status = root / "simulation" / "live_status.json"
    document = json.loads(status.read_text(encoding="utf-8"))
    outside = tmp_path / "outside_status.json"
    outside.write_text(json.dumps({**document, "platform": "CUDA"}), encoding="utf-8")
    status.unlink()
    status.symlink_to(outside)
    real = root.resolve()
    with record_access.confined_records(real, lambda _path: True):
        text = methods_paragraphs(real, {}, {})
    assert "the first piece's platform is not recorded" in text
    assert "Simulations used OpenMM on the CPU platform." not in text and "CUDA" not in text


START = {"integrator": "langevin_middle", "timestep_fs": 2.0, "ensemble": "npt",
         "duration_ns": 0.02, "nvt_duration_ns": 0.1, "npt_duration_ns": 0.1}


def test_a_study_started_from_a_checkpoint_and_minimized_says_both(tmp_path: Path) -> None:
    """`/tmp/A1i/e/e1`: a checkpoint written before production is minimised
    and equilibrated from. The minimisation was left out, and production
    said to have continued before an equilibration."""
    record = {"parameters": {**START, "minimize": True,
                             "resume_from": "/runs/parentA/simulation/checkpoint.chk"},
              "resolved": {"nvt_steps": 50_000, "npt_steps": 50_000,
                           "production_steps": 10_000}}
    text = methods_paragraphs(tmp_path, {}, record)
    assert ("The run started from the checkpoint of the study `parentA`. The system was "
            "energy-minimized before dynamics. Equilibration comprised 100 ps") in text
    assert "Production continued" not in text


def test_only_a_study_s_folder_is_named(tmp_path: Path) -> None:
    """Never a home folder, a file, or a name that ends the code span; the
    checkpoint's own record's study before the path named."""
    def opening(**given: object) -> str:
        record = {"parameters": {**START, "minimize": False, "nvt_duration_ns": 0,
                                 "npt_duration_ns": 0, **given.pop("parameters", {})},
                  "resolved": {"nvt_steps": 0, "npt_steps": 0, "production_steps": 10_000},
                  **given}
        text = methods_paragraphs(tmp_path, {}, record)
        return text.split("Production continued from the checkpoint of ")[1].split(";")[0]

    assert opening(parameters={"resume_from": "/home/kunle/secret.chk"}) == "another study"
    assert opening(parameters={"resume_from": "/home/kunle/data/state.xml"}) == "another study"
    assert opening(parameters={"resume_from": "/w/a`b/simulation/checkpoint.chk"}) == \
        "the study `` a`b ``"
    assert opening(parameters={"resume_from": "/w/alpha/segment-002/simulation/checkpoint.chk"}) \
        == "the study `alpha`"
    assert opening(parameters={"resume_from": "/w/alpha/simulation/checkpoint.chk"},
                   continues={"study": "/old/src0", "checkpoint": "/w/alpha/x.chk"}) == \
        "the study `src0`"


def _renamed_with_its_piece_s_config(tmp_path: Path, **config: object) -> Path:
    """`s/ren_nokey`, `ren_false`, `vary_ren`: a study holding its piece's
    config, moved under a new name, the config's `resume_unsealed` absent or
    false; the `segment-001.yml` resume wrote beside it names the same
    checkpoint."""
    old = _with_its_piece_s_config(tmp_path / "hpc")
    text = (old / "resolved_config.yml").read_text(encoding="utf-8")
    named = json.loads(text.split("resume_from: ")[1].splitlines()[0])
    whole = {**SETTINGS, "duration_ns": 0.004, "nvt_duration_ns": None,
             "npt_duration_ns": None, "nvt_steps": 0, "npt_steps": 0, "minimize": False,
             "resume_from": named, **config}
    _config(old, {key: value for key, value in whole.items() if value != "absent"})
    (old / "segment-001.yml").write_text(
        "simulation:\n  resume_unsealed: true\n"
        f"  resume_from: {json.dumps(named)}\n", encoding="utf-8")
    renamed = tmp_path / "workstation" / "renamed"
    shutil.copytree(old, renamed, symlinks=True)
    shutil.rmtree(old)
    return renamed


def test_a_piece_s_config_is_told_by_the_segment_config_beside_it(tmp_path: Path) -> None:
    for name, config in (("ren_nokey", {"resume_unsealed": "absent"}),
                         ("ren_false", {"resume_unsealed": False}),
                         ("vary_ren", {"resume_unsealed": False, "nvt_steps": 500,
                                       "npt_steps": 800})):
        root = _renamed_with_its_piece_s_config(tmp_path / name, **config)
        said = recorded_stage_lengths(root)
        assert said["nvt"]["steps"] == 500 and said["npt"]["steps"] == 800, name
        assert said["read_from"]["nvt"] == "simulation/live_status.json", name
        text = methods_paragraphs(root, {}, {})
        assert "The system was energy-minimized before dynamics." in text, name
        assert "continued from the checkpoint" not in text, name
        assert "started from the checkpoint" not in text, name


def test_a_clock_under_1_ps_keeps_two_decimals(tmp_path: Path) -> None:
    """Two decimals of ps, as above 1 ps; 0.9996 ps said "1 ps"."""
    for ps, said in ((0.5, "0.50 ps"), (0.9996, "1.00 ps"), (0.837, "0.84 ps")):
        assert f"Production dynamics were run for {said} (" in _variable_clock(
            tmp_path / str(ps), ps), said


def test_a_study_under_a_folder_reached_through_a_link_reads_its_pieces(
        tmp_path: Path) -> None:
    """The macOS layout: `/var` is a link to `/private/var`, so a study in
    the temporary folder is named through a link above it, not at it. Its
    pieces read "not read" and counted 1 of 3 (CI on macOS, `aa70a32a`)."""
    real = tmp_path / "private" / "var"
    real.mkdir(parents=True)
    (tmp_path / "var").symlink_to(real, target_is_directory=True)
    _intr(real / "folders")
    study = tmp_path / "var" / "folders" / "intr"
    said = recorded_stage_lengths(study)
    assert said["pieces"] == 2 and said["production_ran_ns"] == 0.012
    assert said["production_ran_source"] == "summed over pieces"
    assert said["nvt"]["source"] != NOT_READ
