"""A sweep is one study in every interface.

A sweep is a set of runs: every combination of the values given for each axis.
Written in a config, on the command line, in a script, or read back from the
file a study of several runs leaves at its root, it has to make the same runs.
It was dropped from all but the config: the command line had no flag for it,
`cli_command` and `python_script` left it out, and the only record a study of
several runs kept of itself was a manifest that is not a config.
"""

from __future__ import annotations

import json
import shlex

import pytest

pytest.importorskip("mdtraj")

BASE = {"systems": [{"system": "1UBQ"}], "simulation": {"duration_ns": 0.5}}
SWEEP = {"simulation.temperature_K": [300.0, 310.0],
         "simulation.friction_per_ps": [1.0, 2.0]}


def _study():
    return {**BASE, "systems": [dict(s) for s in BASE["systems"]],
            "simulation": dict(BASE["simulation"]), "sweep": dict(SWEEP)}


def _runs(config, where):
    """The runs a study makes: each one's system and swept values, and the
    settings the study chose, as a batch expands them."""
    from fastmdxplora.batch.explorer import BatchExplorer

    specs = BatchExplorer(config_data=config, output_dir=str(where)).run_specs
    return sorted((spec.system, json.dumps(spec.sweep_values, sort_keys=True),
                   (spec.options.get("simulation") or {}).get("duration_ns"))
                  for spec in specs)


def _explore_parser():
    from fastmdxplora.cli.main import _build_parser

    parser = _build_parser()
    return parser


class TestTheFlag:
    def test_it_reads_values_as_the_config_does(self):
        from fastmdxplora.cli.main import _sweep_from_flags

        axes = _sweep_from_flags(["simulation.temperature_K=300,310.5",
                                  "setup.keep_water=true,false"])
        assert axes == {"simulation.temperature_K": [300, 310.5],
                        "setup.keep_water": [True, False]}

    def test_a_value_holding_a_comma_is_written_in_brackets(self):
        from fastmdxplora.cli.main import _sweep_from_flags

        axes = _sweep_from_flags(['analysis.select_atoms=["name CA, name CB", "protein"]'])
        assert axes == {"analysis.select_atoms": ["name CA, name CB", "protein"]}

    def test_a_malformed_axis_says_what_to_write(self):
        from fastmdxplora.cli.main import _sweep_from_flags

        with pytest.raises(SystemExit, match="AXIS=VALUES"):
            _sweep_from_flags(["simulation.temperature_K"])
        with pytest.raises(SystemExit):
            _sweep_from_flags(["temperature=300"])      # not phase.option

    def test_it_merges_over_the_configs_sweep_by_axis(self, tmp_path):
        import yaml

        from fastmdxplora.cli.main import _build_explore_config

        path = tmp_path / "study.yml"
        path.write_text(yaml.safe_dump({"systems": [{"system": "1UBQ"}], "sweep": SWEEP}),
                        encoding="utf-8")
        args = _explore_parser().parse_args(
            ["explore", "--config", str(path), "--sweep", "simulation.friction_per_ps=5,6"])
        assert _build_explore_config(args)["sweep"] == {
            "simulation.temperature_K": [300.0, 310.0],
            "simulation.friction_per_ps": [5, 6]}


class TestEveryInterfaceMakesTheSameRuns:
    def test_the_command(self, tmp_path):
        from fastmdxplora.cli.main import _build_explore_config
        from fastmdxplora.config.languages import cli_command

        command = cli_command(_study())
        assert "--sweep" in command
        rebuilt = _build_explore_config(_explore_parser().parse_args(shlex.split(command)[1:]))
        assert _runs(rebuilt, tmp_path / "b") == _runs(_study(), tmp_path / "a")
        assert len(_runs(rebuilt, tmp_path / "c")) == 4

    def test_the_script(self, tmp_path):
        import types

        from fastmdxplora.config.languages import python_script

        given = {}
        stand_in = types.ModuleType("fastmdxplora")

        class Study:
            def __init__(self, **kwargs):
                given.update(kwargs)

            def explore(self, **kwargs):
                return []

        stand_in.FastMDXplora = Study
        source = python_script(_study()).replace("import fastmdxplora as fastmdx", "")
        exec(compile(source, "<script>", "exec"), {"fastmdx": stand_in})
        assert "config_data" in given
        assert _runs(given["config_data"], tmp_path / "b") == _runs(_study(), tmp_path / "a")

    def test_the_file_a_study_of_several_runs_leaves(self, tmp_path):
        from fastmdxplora.batch.explorer import BatchExplorer

        study = BatchExplorer(config_data=_study(), output_dir=str(tmp_path / "study"))
        study.output_dir.mkdir(parents=True)
        study._write_study_config()
        written = tmp_path / "study" / "resolved_config.yml"
        again = BatchExplorer(config=str(written), output_dir=str(tmp_path / "again")).run_specs
        assert sorted((s.system, json.dumps(s.sweep_values, sort_keys=True)) for s in again) \
            == [(system, values) for system, values, _ in _runs(_study(), tmp_path / "a")]


class TestTheStudyFileIsWrittenWhereItBelongs:
    """Before any run starts, so a study that stops part-way still leaves the
    recipe; and only for a study of several runs, whose root is not a run."""

    class Started(Exception):
        pass

    def _started(self, config, where, monkeypatch):
        from fastmdxplora.batch.explorer import BatchExplorer

        def stop(self, *args, **kwargs):
            raise TestTheStudyFileIsWrittenWhereItBelongs.Started

        monkeypatch.setattr(BatchExplorer, "_run_sequential", stop)
        with pytest.raises(self.Started):
            BatchExplorer(config_data=config, output_dir=str(where)).run()
        return where / "resolved_config.yml"

    def test_a_sweep_leaves_it_before_the_runs(self, tmp_path, monkeypatch):
        written = self._started(_study(), tmp_path / "sweep", monkeypatch)
        assert written.is_file()
        import yaml

        assert yaml.safe_load(written.read_text(encoding="utf-8"))["sweep"] == SWEEP

    def test_a_single_run_does_not(self, tmp_path, monkeypatch):
        assert not self._started(dict(BASE), tmp_path / "one", monkeypatch).exists()
