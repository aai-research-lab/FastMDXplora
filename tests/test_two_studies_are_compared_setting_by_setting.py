"""Two studies, or two Configs, compared setting by setting.

"Why did these two runs come out differently?" starts with what they were
asked to do, and two resolved configs are a hundred lines each. `fastmdx diff`
lists the settings two Configs or two studies differ in, with the phase
settings either leaves out taken at their defaults, and the paths inside each
study's own folder said relative to it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fastmdxplora.config.diff import ABSENT, config_of, differences, settings_of


def _study(folder: Path, config: dict) -> Path:
    folder.mkdir(parents=True)
    body = dict(config, output=str(folder))
    (folder / "resolved_config.yml").write_text(yaml.safe_dump(body), encoding="utf-8")
    return folder


class TestTheDifferences:
    def test_a_default_left_out_is_the_default(self):
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        timestep = next(f for f in PHASE_SCHEMAS["simulation"].fields if f.name == "timestep_fs")
        short = {"simulation": {"temperature_K": 310}}
        written_out = {"simulation": {"temperature_K": 310, "timestep_fs": timestep.default}}
        assert differences(short, written_out) == []

    def test_a_number_is_a_number(self):
        assert differences({"simulation": {"duration_ns": 2}},
                           {"simulation": {"duration_ns": 2.0}}) == []
        assert [d.setting for d in differences({"simulation": {"minimize": True}},
                                               {"simulation": {"minimize": 1}})] == [
            "simulation.minimize"]

    def test_each_setting_by_its_dotted_name(self):
        found = differences({"systems": [{"id": "a", "system": "1UBQ"}],
                             "simulation": {"temperature_K": 300}},
                            {"systems": [{"id": "a", "system": "1AKE"}],
                             "simulation": {"temperature_K": 310}, "sweep": {"x": [1, 2]}})
        assert [(d.setting, d.first, d.second) for d in found] == [
            ("systems[0].system", "1UBQ", "1AKE"),
            ("simulation.temperature_K", 300, 310),
            ("sweep.x", ABSENT, [1, 2])]

    def test_paths_inside_a_study_are_said_from_it(self):
        flat = settings_of({"output": "/x/study", "analysis": {
            "trajectory": "/x/study/joined/production.dcd"},
            "simulation": {"setup_from": "/x/study"}})
        assert flat["analysis.trajectory"] == "<output>/joined/production.dcd"
        assert flat["simulation.setup_from"] == "<output>"
        found = differences({"output": "/x/a", "analysis": {"trajectory": "/x/a/t.dcd"}},
                            {"output": "/y/b", "analysis": {"trajectory": "/y/b/t.dcd"}})
        assert [(d.setting, d.where_only) for d in found] == [("output", True)]


    def test_left_out_and_null_are_one_setting_not_set(self):
        # "exclude_phase not set vs null", and every phase named against
        # none named, were listed as differences.
        same = {"report": {"title": "A study"}}
        assert differences(dict(same, exclude_phase=None), same) == []
        assert differences(dict(same, include_phase=["setup", "simulation", "analysis",
                                                     "report"]),
                           dict(same, include_phase=None)) == []
        assert [d.setting for d in differences(dict(same, include_phase=["analysis"]),
                                               same)] == ["include_phase"]

    def test_what_the_run_works_out_is_unrecorded_not_different(self):
        found = differences({"simulation": {"ensemble": None, "production_steps": None}},
                            {"simulation": {"ensemble": "npt", "production_steps": 5000}})
        assert sorted(d.setting for d in found) == ["simulation.ensemble",
                                                    "simulation.production_steps"]
        assert all(d.unrecorded for d in found)
        # And what setup works out: the force field's files, the seed.
        [seed] = differences({"setup": {"random_seed": None}}, {"setup": {"random_seed": 7}})
        assert seed.unrecorded
        # Recorded on both sides and different, it is a difference.
        [both] = differences({"simulation": {"production_steps": 4000}},
                             {"simulation": {"production_steps": 5000}})
        assert not both.unrecorded
        # A setting asked for, left unset on one side, is a difference.
        [asked] = differences({"report": {"title": None}}, {"report": {"title": "A study"}})
        assert not asked.unrecorded


class TestTheStudiesPage:
    def test_the_page_says_only_what_was_asked_differently(self, tmp_path):
        from fastmdxplora.gui.workspace import studies_compared

        first = _study(tmp_path / "first", {
            "include_phase": ["setup", "simulation", "analysis", "report"],
            "simulation": {"duration_ns": 0.02, "ensemble": None}})
        second = _study(tmp_path / "second", {
            "simulation": {"duration_ns": 0.01, "ensemble": "npt"}, "exclude_phase": None})
        said = studies_compared(first, second)
        assert [d["setting"] for d in said["settings"]] == ["simulation.duration_ns"]
        assert said["unrecorded"] == ["simulation.ensemble"]

    def test_an_analysis_is_named_by_its_heading(self, tmp_path):
        from fastmdxplora.gui.workspace import _label_of

        assert _label_of("end_to_end") == "End-to-end distance"
        assert _label_of("rmsd") == "RMSD"
        assert _label_of("made_up_thing") == "Made up thing"


class TestWhatIsRead:
    def test_a_study_is_read_from_its_resolved_config(self, tmp_path):
        folder = _study(tmp_path / "a", {"simulation": {"temperature_K": 300}})
        config, where = config_of(folder)
        assert where == folder / "resolved_config.yml"
        assert config["simulation"]["temperature_K"] == 300

    def test_what_is_not_a_study_or_a_config_is_refused(self, tmp_path):
        from fastmdxplora.config import ConfigError

        with pytest.raises(ConfigError, match="holds no resolved_config.yml"):
            config_of(tmp_path)
        with pytest.raises(ConfigError, match="no such Config or study"):
            config_of(tmp_path / "nothing.yml")


class TestTheCommand:
    def run(self, *argv: str, capsys) -> tuple[int, str]:
        from fastmdxplora.cli.main import main

        code = main(["diff", *argv])
        return code, capsys.readouterr().out

    def test_two_studies_that_differ(self, tmp_path, capsys):
        a = _study(tmp_path / "a", {"simulation": {"temperature_K": 300}})
        b = _study(tmp_path / "b", {"simulation": {"temperature_K": 310}})
        code, out = self.run(str(a), str(b), capsys=capsys)
        assert code == 1
        assert "1 setting differ" in out
        assert "simulation.temperature_K  300  ->  310" in out
        assert "Written differently only in: output" in out

    def test_two_copies_of_one_study(self, tmp_path, capsys):
        a = _study(tmp_path / "a", {"simulation": {"temperature_K": 300}})
        b = _study(tmp_path / "b", {"simulation": {"temperature_K": 300.0}})
        code, out = self.run(str(a), str(b), capsys=capsys)
        assert code == 0 and "They ask for the same study." in out

    def test_a_study_against_a_config_says_what_that_means(self, tmp_path, capsys):
        a = _study(tmp_path / "a", {"simulation": {"temperature_K": 300}})
        config = tmp_path / "study.yml"
        config.write_text(yaml.safe_dump({"simulation": {"temperature_K": 300}}), encoding="utf-8")
        _, out = self.run(str(a), str(config), capsys=capsys)
        assert "One is a study and one a Config" in out

    def test_as_json(self, tmp_path, capsys):
        # The second leaves the temperature to its default, 300 K.
        a = _study(tmp_path / "a", {"simulation": {"temperature_K": 310}})
        b = _study(tmp_path / "b", {"setup": {"ph": 6.5}})
        code, out = self.run(str(a), str(b), "--json", capsys=capsys)
        record = json.loads(out[out.index("{"):])
        assert code == 1
        settings = {d["setting"]: d for d in record["differences"]}
        assert settings["setup.ph"]["second"] == 6.5 and settings["output"]["where_only"]
        assert settings["simulation.temperature_K"]["first"] == 310
        assert settings["simulation.temperature_K"]["in_second"]

    def test_a_folder_that_is_not_a_study(self, tmp_path, capsys):
        from fastmdxplora.cli.main import main

        assert main(["diff", str(tmp_path), str(tmp_path)]) == 2
        assert "holds no resolved_config.yml" in capsys.readouterr().err


def test_what_follows_from_a_listed_difference_is_not_listed_again():
    """Two lengths listed their step counts beside them; a trajectory
    recorded under its folder's name read as another study's."""
    found = differences(
        {"output": "/a/trpcage", "simulation": {"duration_ns": 0.1, "production_steps": 50000},
         "analysis": {"trajectory": "trpcage/simulation/production.dcd"}},
        {"output": "/b/run-short", "simulation": {"duration_ns": 0.01, "production_steps": 5000},
         "analysis": {"trajectory": "/b/run-short/simulation/production.dcd"}})
    assert [d.setting for d in found if not d.where_only] == ["simulation.duration_ns"]
    # Chosen by a person, the analyses are compared even where one is unset.
    [chosen] = differences({"analysis": {"include": None}}, {"analysis": {"include": ["rmsd"]}})
    assert not chosen.unrecorded
