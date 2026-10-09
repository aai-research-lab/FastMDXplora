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

    def test_a_study_that_never_reached_its_analysis_asked_for_no_other(self, tmp_path):
        """A study that failed before its analysis was listed as asking for
        no analyses against the default list another recorded."""
        import json

        from fastmdxplora.gui.workspace import studies_compared

        ran = _study(tmp_path / "ran", {"analysis": {"include": ["rmsd", "rg"]}})
        (ran / "manifest.json").write_text(json.dumps(
            {"phases": [{"name": "analysis", "status": "ok"}]}), encoding="utf-8")
        failed = _study(tmp_path / "failed", {"analysis": {"include": None}})
        said = studies_compared(ran, failed)
        assert "analysis.include" not in [d["setting"] for d in said["settings"]]
        assert "analysis.include" in said["unrecorded"]

    def test_a_phase_one_study_did_not_run_is_not_compared(self, tmp_path):
        """A trajectory analysed was listed against a run for its platform
        ("CPU | auto"), its length and its equilibration."""
        from fastmdxplora.gui.workspace import studies_compared

        run = _study(tmp_path / "run", {"simulation": {"duration_ns": 0.1, "platform": "CPU"}})
        analysed = _study(tmp_path / "analysed", {"include_phase": ["analysis", "report"]})
        settings = [d["setting"] for d in studies_compared(run, analysed)["settings"]]
        assert "include_phase" in settings
        assert not [s for s in settings if s.startswith(("simulation.", "setup."))]

    def test_a_study_is_compared_by_the_phases_its_records_say_it_ran(self, tmp_path):
        """A simulated study whose Config said `[analysis, report]` (as
        Analyze again wrote it) was compared as a trajectory analysed: a
        tenfold difference in length went unsaid. And a study that ran its
        analyses with the default list was listed as "not set" against the
        same list written out."""
        import json

        from fastmdxplora.gui.workspace import studies_compared

        names = ["rmsd", "rg"]
        run = _study(tmp_path / "run", {"simulation": {"duration_ns": 0.1},
                                        "analysis": {"include": names}})
        rewritten = _study(tmp_path / "rewritten", {
            "include_phase": ["analysis", "report"], "simulation": {"duration_ns": 0.01},
            "analysis": {"include": None}})
        for study in (run, rewritten):
            (study / "simulation").mkdir()
            (study / "simulation" / "live_status.json").write_text(json.dumps(
                {"status": "completed", "stage_states": {"production": "completed"}}),
                encoding="utf-8")
            (study / "manifest.json").write_text(json.dumps({"phases": [
                {"name": phase, "status": "ok"} for phase in ("setup", "simulation",
                                                               "analysis", "report")]}),
                encoding="utf-8")
            (study / "analysis").mkdir()
            (study / "analysis" / "analysis_manifest.json").write_text(
                json.dumps({"plan": names}), encoding="utf-8")
        said = studies_compared(run, rewritten)
        settings = [d["setting"] for d in said["settings"]]
        assert "simulation.duration_ns" in settings
        assert "analysis.include" not in settings + said["unrecorded"]
        # Both ran the same four phases, whatever the rewritten Config says.
        assert "include_phase" not in settings

    def test_a_run_that_ended_early_asked_for_what_it_asked_for(self, tmp_path):
        """A stopped or failed run compared with a finished one had a row
        `include_phase ["setup","simulation"] | [all four]`: how far it got,
        said as a setting it asked for. And a study that ran the default
        analyses was "not set" against another list."""
        import json

        from fastmdxplora.gui.workspace import studies_compared

        def recorded(study, phases, plan):
            (study / "simulation").mkdir()
            (study / "simulation" / "live_status.json").write_text(json.dumps(
                {"status": "stopped", "stage_states": {"production": "stopped"}}),
                encoding="utf-8")
            (study / "manifest.json").write_text(json.dumps({"phases": [
                {"name": phase, "status": "ok"} for phase in phases]}), encoding="utf-8")
            if plan:
                (study / "analysis").mkdir()
                (study / "analysis" / "analysis_manifest.json").write_text(
                    json.dumps({"plan": plan}), encoding="utf-8")

        done = _study(tmp_path / "done", {"analysis": {"include": None}})
        recorded(done, ("setup", "simulation", "analysis", "report"), ["rmsd", "rg", "sasa"])
        short = _study(tmp_path / "short", {
            "include_phase": ["setup", "simulation", "analysis", "report"],
            "analysis": {"include": ["rmsd", "rg"]}})
        recorded(short, ("setup", "simulation"), None)
        said = studies_compared(done, short)
        assert "include_phase" not in [d["setting"] for d in said["settings"]], said
        other = _study(tmp_path / "other", {"analysis": {"include": ["rmsd"]}})
        recorded(other, ("setup", "simulation", "analysis", "report"), ["rmsd"])
        [row] = [d for d in studies_compared(done, other)["settings"]
                 if d["setting"] == "analysis.include"]
        assert "not set" not in json.dumps(row) and "sasa" in json.dumps(row), row

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
