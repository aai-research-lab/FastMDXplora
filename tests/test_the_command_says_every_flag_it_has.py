"""What the command line can say, cli_command says, and what it says reads
back as the study it came from.

cli_command kept one flag per setting, so a boolean, which has two, lost
one direction and was refused as untranslatable when set against its
default. Its execution block was dropped without a word, and verbose and
explain were never written. On the reading side --no-explain was acted on
and then left out of the study, a list flag read its items as text so the
devices arrived as "0" and "1", and live telemetry, hand-written when it
defaulted off, had only the flag that turns it on.
"""

from __future__ import annotations

import shlex

import pytest

pytest.importorskip("mdtraj")


def _round_trip(config):
    from fastmdxplora.cli.main import _build_explore_config, _build_parser
    from fastmdxplora.config.languages import cli_command

    command = cli_command({"systems": [{"system": "1UBQ"}], **config})
    return command, _build_explore_config(_build_parser().parse_args(shlex.split(command)[1:]))


@pytest.mark.parametrize("block, name, value", [
    ("simulation", "restrain_production", True),
    ("simulation", "resume_unsealed", True),
    ("setup", "build_missing_termini", True),
    ("setup", "membrane_orient", True),
    ("simulation", "live_telemetry", False),
    ("execution", "continue_on_error", False),
])
def test_a_boolean_set_against_its_default_is_said_and_read_back(block, name, value):
    command, back = _round_trip({block: {name: value}})
    assert back[block][name] is value, command


def test_how_the_runs_are_scheduled_is_said_and_read_back():
    scheduling = {"mode": "parallel", "workers": 2, "devices": [0, 1]}
    command, back = _round_trip({"execution": scheduling})
    assert "--execution-workers 2" in command
    assert back["execution"] == scheduling          # devices as numbers, not "0" "1"


def test_verbose_and_explain_are_said_and_read_back():
    command, back = _round_trip({"verbose": True, "explain": False})
    assert "--verbose" in command and "--no-explain" in command
    assert back["verbose"] is True and back["explain"] is False


def test_live_telemetry_has_both_directions():
    from fastmdxplora.cli.main import _build_parser

    explore = _build_parser()._subparsers._group_actions[0].choices["explore"]
    spellings = sorted(a.option_strings[-1] for a in explore._actions
                       if a.dest == "simulate__live_telemetry")
    assert spellings == ["--simulate-live-telemetry", "--simulate-no-live-telemetry"]


def test_a_list_flag_reads_each_item_as_the_config_would():
    from fastmdxplora.cli.main import _build_explore_config, _build_parser

    parsed = _build_explore_config(_build_parser().parse_args(
        ["explore", "--system", "x", "--setup-chains", "A", "B", "--execution-devices", "0", "1"]))
    assert parsed["setup"]["chains"] == ["A", "B"]
    assert parsed["execution"]["devices"] == [0, 1]


def test_a_mapping_and_a_list_of_blocks_are_said_and_read_back():
    # Withheld as things a flag cannot carry, before a flag read YAML.
    settings = {"analysis": {"options": {"cluster": {"n_clusters": 5}}},
                "report": {"region_highlights": [{"label": "helix", "start": 3, "end": 7}]}}
    command, back = _round_trip(settings)
    assert "--analyze-options" in command and "--report-region-highlights" in command
    assert "{'" not in command                       # JSON, not a Python repr
    assert back["analysis"]["options"] == settings["analysis"]["options"]
    assert back["report"]["region_highlights"] == settings["report"]["region_highlights"]


def test_how_the_study_was_written_is_said_and_read_back():
    study = {"agent": "assisted", "agent_model": "some-model", "budget_hours": 0.5}
    command, back = _round_trip(study)
    assert "--agent assisted" in command and "--budget-hours 0.5" in command
    assert {k: back[k] for k in study} == study


def test_the_study_flags_are_on_explore_and_the_phase_agent_stays_the_phases():
    from fastmdxplora.cli.main import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["setup", "--system", "x", "--agent", "assisted"])
    assert args.agent == "assisted"                   # the setup phase's own field
    with pytest.raises(SystemExit):                   # no study flag on a phase command
        parser.parse_args(["setup", "--system", "x", "--budget-hours", "1"])
    explore = parser.parse_args(["explore", "--system", "x", "--agent", "assisted",
                                 "--budget-hours", "1"])
    assert (explore.agent, explore.budget_hours) == ("assisted", 1.0)


def test_a_script_carries_how_the_study_was_written():
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
    script = python_script({"systems": [{"system": "1UBQ"}], "agent": "assisted",
                            "budget_hours": 0.5, "verbose": True})
    exec(compile(script.replace("import fastmdxplora as fastmdx", ""), "<script>", "exec"),
         {"fastmdx": stand_in})
    study = given["config_data"]
    assert (study["agent"], study["budget_hours"], study["verbose"]) == ("assisted", 0.5, True)


def test_the_resolved_config_records_that_a_run_did_not_explain(tmp_path):
    import yaml

    from fastmdxplora.config import write_resolved_config

    written = write_resolved_config({"system": "1UBQ", "explain": False, "options": {}}, tmp_path)
    assert yaml.safe_load(written.read_text(encoding="utf-8"))["explain"] is False
    kept_on = write_resolved_config({"system": "1UBQ", "explain": True, "options": {}},
                                    tmp_path / "on")
    assert "explain" not in yaml.safe_load(kept_on.read_text(encoding="utf-8"))
