"""A study's record read to run its phases again, and what a run replaces.

`config.recorded` gives a phase command the settings a study recorded, its
own files found where the study is now, a continuation not carried on by
an analysis, the phases it runs put back after some ran again, and options
laid over analysis by analysis. `replaced.make_way` clears what a phase run
again replaces and leaves stale, kept in `previous/<phase>` with its
manifest record where asked.
"""

from __future__ import annotations

import json

import yaml


class TestTheRecord:

    def test_a_record_that_is_no_config_is_none(self, tmp_path):
        from fastmdxplora.config.recorded import RECORDED, study_config

        (tmp_path / RECORDED).write_text("systems: [", encoding="utf-8")
        assert study_config(tmp_path) is None
        (tmp_path / RECORDED).write_text("- a list\n", encoding="utf-8")
        assert study_config(tmp_path) is None

    def test_a_continuation_is_not_carried_on_by_an_analysis(self, tmp_path):
        from fastmdxplora.config.recorded import RECORDED, study_config

        (tmp_path / RECORDED).write_text(yaml.safe_dump({
            "systems": [{"system": "1L2Y"}], "budget_hours": 3,
            "simulation": {"resume_from": "../old", "extra_ns": 5, "duration_ns": 1}}))
        config = study_config(tmp_path, ["analysis"])
        assert config["simulation"] == {"duration_ns": 1} and "budget_hours" not in config
        assert study_config(tmp_path, ["simulation"])["simulation"]["extra_ns"] == 5

    def test_a_relative_file_is_read_from_the_study(self, tmp_path):
        from fastmdxplora.config.recorded import leave_the_study_s_own_files

        settings = {"trajectory": "simulation/production.dcd", "topology": "/elsewhere/t.pdb"}
        leave_the_study_s_own_files(settings, None, tmp_path)
        assert settings == {"topology": "/elsewhere/t.pdb"}

    def test_the_phases_put_back_skip_what_cannot_be_read(self, tmp_path):
        from fastmdxplora.config.recorded import RECORDED, keep_the_study_s_phases

        (tmp_path / "batch_manifest.json").write_text("{}")
        for name, text in (("a", "# made by\nsystems: []\n"), ("b", "systems: ["), ("c", "- x\n")):
            (tmp_path / "runs" / name).mkdir(parents=True)
            (tmp_path / "runs" / name / RECORDED).write_text(text)
        keep_the_study_s_phases(tmp_path, ["analysis", "report"], None)
        kept = (tmp_path / "runs" / "a" / RECORDED).read_text()
        assert kept.startswith("# made by\n")
        assert yaml.safe_load(kept)["include_phase"] == ["analysis", "report"]
        assert (tmp_path / "runs" / "b" / RECORDED).read_text() == "systems: ["

    def test_options_are_laid_over_analysis_by_analysis(self):
        from fastmdxplora.config.recorded import laid_over

        recorded = {"include": ["rmsd"], "options": {"rmsd": {"ref": 0, "sel": "CA"}}}
        given = {"exclude": ["rg"], "options": {"rmsd": {"ref": 5}, "rg": {"sel": "all"}}}
        assert laid_over(recorded, given) == {
            "exclude": ["rg"], "options": {"rmsd": {"ref": 5, "sel": "CA"}, "rg": {"sel": "all"}}}

    def test_a_phase_s_settings_from_a_record(self, tmp_path):
        from fastmdxplora.config.recorded import RECORDED, phase_settings

        (tmp_path / RECORDED).write_text(yaml.safe_dump({
            "systems": [{"system": "1L2Y"}], "output": str(tmp_path),
            "analysis": {"trajectory": str(tmp_path / "simulation" / "production.dcd"),
                         "include": ["rg"]}}))
        assert phase_settings(tmp_path / RECORDED, "analysis", study=tmp_path) == {
            "include": ["rg"]}


def test_the_replaced_record_is_kept_beside_what_was_replaced(tmp_path):
    from fastmdxplora.replaced import make_way

    root = tmp_path / "study"
    (root / "analysis").mkdir(parents=True)
    (root / "analysis" / "x.txt").write_text("before")
    (root / "joined").mkdir()
    (root / "joined" / "production.dcd").write_text("frames")
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "simulation", "status": "ok"}, {"name": "analysis", "status": "ok"}]}))
    said = []
    assert make_way(root, ["simulation"], keep=True, say=said.append) == [
        "simulation", "analysis"]
    # The simulation's pieces are kept, in a folder of its own.
    assert (root / "previous" / "simulation" / "joined" / "production.dcd").is_file()
    assert json.loads((root / "previous" / "analysis" / "phase_record.json").read_text())[
        "name"] == "analysis"
    assert json.loads((root / "manifest.json").read_text())["phases"] == []
    assert make_way(root, [], keep=False) == []
    (root / "manifest.json").write_text("[]")
    (root / "analysis").mkdir()
    (root / "analysis" / "y.txt").write_text("again")
    assert make_way(root, ["analysis"], keep=False, say=said.append) == ["analysis"]
    assert not (root / "analysis").exists()
