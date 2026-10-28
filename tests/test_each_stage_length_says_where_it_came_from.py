"""Each stage's length is read from the run's records, with its source.

A study that left its equilibration to the defaults recorded
`nvt_duration_ns` and `npt_duration_ns` as null and the step counts under
`resolved`, so a reader taking the length from the setting read none, and a
table of runs said "NVT/NPT/production 0/0/20 ns" of runs that equilibrated
for 1.5 ns. The record now carries every stage's length and its source, and
`recorded_stage_lengths` reads them: "set", "from steps and timestep", or
"not recorded", never 0 for a length nobody recorded.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmdxplora.simulation.lengths import (
    FROM_STEPS,
    NOT_RECORDED,
    SET,
    recorded_stage_lengths,
    stage_lengths,
)
from fastmdxplora.simulation.pipeline import _write_manifest
from fastmdxplora.simulation.runner import plan_stages

#: A study's simulation settings with only the production length given.
ONLY_PRODUCTION = {"timestep_fs": 2.0, "duration_ns": 20.0,
                   "nvt_duration_ns": None, "npt_duration_ns": None,
                   "nvt_steps": None, "npt_steps": None, "production_steps": None}


def _only_production_written(folder: Path) -> dict:
    """The record the simulation phase writes of a run given only its
    production length, its plan as the runner works it out."""
    plan = plan_stages(duration_ns=20.0, timestep_fs=2.0, nvt_steps=None,
                       npt_steps=None, production_steps=None)
    (folder / "simulation").mkdir(parents=True)
    _write_manifest(folder / "simulation", dict(ONLY_PRODUCTION), [], [],
                    platform_used="CPU", n_frames=2000, duration_ns_actual=20.0,
                    resolved=dict(plan))
    return json.loads((folder / "simulation" / "simulation_parameters.json")
                      .read_text(encoding="utf-8"))


class TestARunGivenOnlyItsProductionLength:

    def test_the_record_carries_each_equilibration_length(self, tmp_path: Path) -> None:
        written = _only_production_written(tmp_path)["stage_lengths"]
        assert written["nvt_duration_ns"] == 0.5
        assert written["npt_duration_ns"] == 1.0
        assert written["duration_ns"] == 20.0
        assert written["timestep_fs"] == 2.0

    def test_the_record_says_where_each_came_from(self, tmp_path: Path) -> None:
        sources = _only_production_written(tmp_path)["stage_lengths"]["sources"]
        assert sources == {"nvt_duration_ns": FROM_STEPS,
                           "npt_duration_ns": FROM_STEPS,
                           "duration_ns": SET}

    def test_each_length_is_read_back_with_its_source(self, tmp_path: Path) -> None:
        _only_production_written(tmp_path)
        said = recorded_stage_lengths(tmp_path)
        assert said["record"] == "read"
        assert said["nvt"] == {"ns": 0.5, "steps": 250_000, "source": FROM_STEPS}
        assert said["npt"] == {"ns": 1.0, "steps": 500_000, "source": FROM_STEPS}
        assert said["production"] == {"ns": 20.0, "steps": 10_000_000, "source": SET}
        assert said["production_ran_ns"] == 20.0
        assert said["frames_saved"] == 2000


class TestARecordWithNoStageLengths:
    """As the preprint's runs left it: what production ran, and nothing of
    the plan."""

    RECORD = {"phase": "simulation",
              "parameters": {"timestep_fs": 2.0, "duration_ns": None,
                             "nvt_duration_ns": None, "npt_duration_ns": None},
              "duration_ns_actual": 20.0}

    def test_each_stage_is_not_recorded_rather_than_0(self) -> None:
        said = stage_lengths(self.RECORD)
        for stage in ("nvt", "npt", "production"):
            assert said[stage] == {"ns": None, "steps": None, "source": NOT_RECORDED}

    def test_what_production_ran_is_still_said(self) -> None:
        assert stage_lengths(self.RECORD)["production_ran_ns"] == 20.0

    def test_a_study_with_no_record_says_so(self, tmp_path: Path) -> None:
        said = recorded_stage_lengths(tmp_path)
        assert said["record"] == NOT_RECORDED
        assert all(said[s]["ns"] is None and said[s]["source"] == NOT_RECORDED
                   for s in ("nvt", "npt", "production"))
        assert said["frames_kept"] == {}


def test_a_stage_set_to_0_is_0_set() -> None:
    said = stage_lengths({"parameters": {"timestep_fs": 2.0, "npt_duration_ns": 0},
                          "resolved": {"npt_steps": 0}})
    assert said["npt"] == {"ns": 0.0, "steps": 0, "source": SET}


def test_steps_the_study_gave_are_a_length_from_steps() -> None:
    said = stage_lengths({"parameters": {"timestep_fs": 4.0, "nvt_steps": 1000}})
    assert said["nvt"] == {"ns": 0.004, "steps": 1000, "source": FROM_STEPS}


def test_steps_without_a_timestep_are_no_length() -> None:
    said = stage_lengths({"parameters": {}, "resolved": {"nvt_steps": 1000}})
    assert said["nvt"] == {"ns": None, "steps": 1000, "source": NOT_RECORDED}


def test_the_frames_each_mean_is_over_are_said(tmp_path: Path) -> None:
    """The frames left after each analysis's equilibration discard, which
    its mean is over; an analysis that recorded no discard says None."""
    for name, mean in (("rmsd", {"n_frames": 2000, "discard": 150, "mean": 0.2}),
                       ("rg", {"n_frames": 2000, "mean": 1.1}),
                       ("contacts", None)):
        folder = tmp_path / "analysis" / name
        folder.mkdir(parents=True)
        findings = {"mean": mean} if mean else {}
        (folder / "options.json").write_text(json.dumps({"findings": findings}),
                                            encoding="utf-8")
    kept = recorded_stage_lengths(tmp_path)["frames_kept"]
    assert kept == {"rmsd": {"frames": 2000, "unit": "frames", "discarded": 150, "kept": 1850},
                    "rg": {"frames": 2000, "unit": "frames", "discarded": None, "kept": None}}


def test_a_variable_step_has_no_length_in_time() -> None:
    """A variable-step integrator chose its own step, so the timestep the
    record holds says nothing of how long a stage ran."""
    said = stage_lengths({
        "parameters": {"integrator": "variable_langevin", "timestep_fs": 2.0,
                       "duration_ns": 10.0},
        "resolved": {"nvt_steps": 250_000, "npt_steps": 500_000,
                     "production_steps": 5_000_000},
        "duration_ns_actual": 10.0})
    for stage, steps in (("nvt", 250_000), ("npt", 500_000), ("production", 5_000_000)):
        assert said[stage]["ns"] is None and said[stage]["source"] == NOT_RECORDED
        assert said[stage]["steps"] == steps and "chose its own step" in said[stage]["said"]
    assert said["production_ran_ns"] is None


def test_steps_that_disagree_with_the_length_given_are_what_ran() -> None:
    said = stage_lengths({"parameters": {"timestep_fs": 2.0, "nvt_steps": 50_000,
                                         "nvt_duration_ns": 0.5},
                          "resolved": {"nvt_steps": 50_000}})
    assert {k: said["nvt"][k] for k in ("ns", "steps", "source")} == {
        "ns": 0.1, "steps": 50_000, "source": FROM_STEPS}
    assert "the steps are what ran" in said["nvt"]["said"]


def test_a_study_extended_in_pieces_gives_their_production() -> None:
    from tests.test_an_extended_study_is_said_at_its_length import _extended

    said = recorded_stage_lengths(_extended())
    assert said["pieces"] == 3
    assert abs(said["production_ran_ns"] - 0.3) < 1e-9
    assert said["production_ran_source"] == "summed over pieces"
    assert "first piece's plan" in said["production"]["said"]
    # Never joined: the frames of the whole are not recorded, said so.
    assert said["frames_saved"] is None
    assert said["frames_saved_source"].startswith("not recorded")


def test_a_record_linked_out_of_the_study_is_not_read(tmp_path: Path) -> None:
    """Called as documented, with no read held: it holds its own."""
    outside = tmp_path / "elsewhere"
    _only_production_written(outside)
    study = tmp_path / "study"
    (study / "simulation").mkdir(parents=True)
    (study / "simulation" / "simulation_parameters.json").symlink_to(
        outside / "simulation" / "simulation_parameters.json")
    said = recorded_stage_lengths(study)
    assert said["record"] == "not read"
    assert said["production"] == {"ns": None, "steps": None, "source": "not read"}


def test_means_held_one_level_down_are_counted_and_an_unread_one_named(
        tmp_path: Path) -> None:
    folder = tmp_path / "analysis" / "sasa"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text(json.dumps({"findings": {
        "mean": {"n_frames": 100, "discard": 10, "mean": 50.0},
        "by_kind": {"hydrophobic": {"n_frames": 100, "discard": 20, "mean": 30.0,
                                    "units": "nm^2"}}}}), encoding="utf-8")
    broken = tmp_path / "analysis" / "rg"
    broken.mkdir()
    (broken / "options.json").write_text("{not json", encoding="utf-8")
    kept = recorded_stage_lengths(tmp_path)["frames_kept"]
    assert kept["sasa"] == {"frames": 100, "unit": "frames", "discarded": 10, "kept": 90}
    assert kept["sasa.by_kind.hydrophobic"] == {"frames": 100, "unit": "frames", "discarded": 20, "kept": 80}
    assert kept["rg"]["source"] == "not read"


def test_thermodynamics_gives_its_samples_and_a_discard_of_0(tmp_path: Path) -> None:
    """Its quantities are held in a group whose samples are counted once
    for all, and a recorded discard of 0 is 0, not missing."""
    folder = tmp_path / "analysis" / "thermodynamics"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text(json.dumps({"findings": {"thermodynamics": {
        "samples": 4,
        "density": {"mean": 1.0, "discard": 0, "units": "g/cm^3"}}}}), encoding="utf-8")
    kept = recorded_stage_lengths(tmp_path)["frames_kept"]
    assert kept == {"thermodynamics.density": {"frames": 4, "unit": "samples", "discarded": 0, "kept": 4}}


def test_an_analysis_record_linked_out_of_the_study_is_named(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps({"findings": {"mean": {"n_frames": 9, "discard": 1,
                                                          "mean": 0.1}}}), encoding="utf-8")
    study = tmp_path / "study"
    (study / "analysis" / "rmsd").mkdir(parents=True)
    (study / "analysis" / "rmsd" / "options.json").symlink_to(outside)
    kept = recorded_stage_lengths(study)["frames_kept"]
    assert kept == {"rmsd": {"frames": None, "unit": "frames", "discarded": None, "kept": None,
                             "source": "not read"}}


def test_an_analysis_folder_linked_out_of_the_study_is_not_listed(tmp_path: Path) -> None:
    """Its folders' names are the outside's: none is listed, and the whole
    is said not read."""
    outside = tmp_path / "elsewhere"
    (outside / "secret_folder_name").mkdir(parents=True)
    study = tmp_path / "study"
    study.mkdir()
    (study / "analysis").symlink_to(outside)
    kept = recorded_stage_lengths(study)["frames_kept"]
    assert list(kept) == ["analysis"]
    assert kept["analysis"]["source"] == "not read"
