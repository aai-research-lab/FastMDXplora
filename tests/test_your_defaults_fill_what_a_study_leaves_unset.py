"""Your defaults fill what a study leaves unset, from every interface alike.

A lab has its usual choices: 310 K because its assays run at body
temperature, one force field, a timestep. Written once in
`fastmdx-defaults.yml` in the folder its studies are kept in, they fill
what a new study leaves unset when it runs from the command line, the GUI's
Run, the Config Builder, the Agent or an AI app, and each value filled is
recorded in the study's `decisions` with the file as its source. They never
decide what is simulated, never override a value a config gives, and leave
a continuation alone.
"""

from __future__ import annotations

import pytest
import yaml

from fastmdxplora.config.defaults_file import (
    DEFAULTS_FILE, defaults_for, find_defaults, read_defaults, with_defaults)
from fastmdxplora.config.loader import ConfigError

LAB = {"simulation": {"temperature_K": 310, "timestep_fs": 2.0},
       "setup": {"ph": 7.0},
       "decisions": {"simulation.temperature_K": {"why": "Our assays run at 310 K."}}}


def _write(folder, data=LAB):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / DEFAULTS_FILE).write_text(yaml.safe_dump(data, sort_keys=False))
    return folder / DEFAULTS_FILE


# ---------------------------------------------------------------------------
# Found, read and checked
# ---------------------------------------------------------------------------
def test_the_nearest_file_up_is_found_and_not_one_above_home(tmp_path):
    home = tmp_path / "home"
    lab = _write(home / "lab")
    study = home / "lab" / "studies" / "trpcage"  # not made yet
    assert find_defaults(study, home=home) == lab.resolve()
    nearer = _write(home / "lab" / "studies")
    assert find_defaults(study, home=home) == nearer.resolve()
    _write(tmp_path)  # above home: never read
    assert find_defaults(home / "elsewhere", home=home) is None


def test_the_file_is_checked_as_part_of_a_config(tmp_path):
    read = read_defaults(_write(tmp_path))
    assert read.settings() == [("simulation.temperature_K", 310),
                               ("simulation.timestep_fs", 2.0), ("setup.ph", 7.0)]
    assert read.whys == {"simulation.temperature_K": "Our assays run at 310 K."}
    for wrong, code in [
        ({"systems": [{"system": "1L2Y"}]}, "config.option.not_permitted"),
        ({"output": "x"}, "config.option.not_permitted"),
        ({"simulation": {"temprature_K": 310}}, "config.option.unknown"),
        ({"setup": {"ph": 25}}, "config.option.out_of_range"),
        ({"simulation": {"temperature_K": 310},
          "decisions": {"setup.ph": {"why": "x"}}}, "config.option.unknown"),
        ({"simulation": {"temperature_K": 310},
          "decisions": {"simulation.temperature_K": {"source": "x"}}},
         "config.option.missing_companion"),
        (["not", "a", "mapping"], "config.file.not_a_mapping"),
    ]:
        with pytest.raises(ConfigError) as caught:
            read_defaults(_write(tmp_path / code.replace(".", "_") / str(id(wrong)), wrong))
        assert caught.value.code == code, wrong
        assert DEFAULTS_FILE in str(caught.value)


# ---------------------------------------------------------------------------
# What they fill
# ---------------------------------------------------------------------------
def test_only_what_is_unset_is_filled_and_each_is_said(tmp_path):
    defaults = read_defaults(_write(tmp_path))
    config = {"systems": [{"system": "1L2Y"}],
              "simulation": {"temperature_K": 300, "duration_ns": 10},
              "setup": {"ph": None},
              "decisions": {"simulation.timestep_fs": {"why": "Mine.", "source": "person"}}}
    before = yaml.safe_dump(config)
    filled, names = with_defaults(config, defaults)
    assert yaml.safe_dump(config) == before  # the config given is not changed
    assert names == ["simulation.timestep_fs"]
    assert filled["simulation"] == {"temperature_K": 300, "duration_ns": 10,
                                    "timestep_fs": 2.0}
    assert filled["setup"] == {"ph": None}  # null is FastMDXplora's default, chosen
    # The person's own reason stays theirs.
    assert filled["decisions"]["simulation.timestep_fs"]["source"] == "person"
    bare, names = with_defaults({"systems": [{"system": "1L2Y"}]}, defaults)
    assert names == ["simulation.temperature_K", "simulation.timestep_fs", "setup.ph"]
    assert bare["decisions"]["simulation.temperature_K"] == {
        "why": "Our assays run at 310 K.", "source": DEFAULTS_FILE}
    assert bare["decisions"]["setup.ph"]["source"] == DEFAULTS_FILE
    from fastmdxplora.config.loader import validate_config

    validate_config(bare, require_systems=True)


def test_a_sweep_and_a_continuation_keep_their_own(tmp_path):
    defaults = read_defaults(_write(tmp_path))
    swept, names = with_defaults({"systems": [{"system": "1L2Y"}],
                                  "sweep": {"simulation.temperature_K": [300, 320]}}, defaults)
    assert "simulation.temperature_K" not in names
    assert "temperature_K" not in swept["simulation"]
    continued, names = with_defaults({"systems": [{"system": "1L2Y"}], "simulation": {
        "resume_from": "runs/old", "duration_ns": 5}}, defaults)
    assert names == [] and continued["simulation"] == {"resume_from": "runs/old",
                                                       "duration_ns": 5}
    prepared, names = with_defaults({"systems": [{"system": "1L2Y"}], "simulation": {
        "setup_from": "runs/old"}}, defaults)
    assert "setup.ph" not in names and "simulation.temperature_K" in names


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
def _explore(argv):
    from fastmdxplora.cli.main import _build_explore_config, _build_parser

    return _build_explore_config(_build_parser().parse_args(argv))


def test_a_new_study_from_the_command_line_runs_with_them(tmp_path, capsys):
    _write(tmp_path / "lab")
    config_file = tmp_path / "lab" / "configs" / "study.yml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("systems: [{system: 1L2Y}]\nsimulation: {duration_ns: 1}\n")
    config = _explore(["explore", "--config", str(config_file),
                       "--output", str(tmp_path / "lab" / "studies" / "a")])
    assert config["simulation"]["temperature_K"] == 310
    assert config["decisions"]["setup.ph"]["source"] == DEFAULTS_FILE
    out = capsys.readouterr().out
    assert "fill what the config leaves unset: simulation.temperature_K 310" in out
    assert "--no-defaults runs without them" in out
    plain = _explore(["explore", "--config", str(config_file), "--no-defaults",
                      "--output", str(tmp_path / "lab" / "studies" / "b")])
    assert "temperature_K" not in plain["simulation"] and "decisions" not in plain


def test_a_study_s_own_record_is_not_filled_again(tmp_path):
    _write(tmp_path)
    study = tmp_path / "study"
    study.mkdir()
    (study / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "s1", "system": "1L2Y"}], "output": str(study),
        "simulation": {"duration_ns": 1}}))
    config = _explore(["analyze", "--output", str(study)])
    assert "temperature_K" not in (config.get("simulation") or {})


def test_a_wrong_file_stops_the_run_and_names_itself(tmp_path):
    _write(tmp_path, {"simulation": {"temprature_K": 310}})
    config_file = tmp_path / "study.yml"
    config_file.write_text("systems: [{system: 1L2Y}]\n")
    with pytest.raises(ConfigError) as caught:
        _explore(["explore", "--config", str(config_file), "--output", str(tmp_path / "a")])
    assert DEFAULTS_FILE in str(caught.value)


# ---------------------------------------------------------------------------
# The Agent
# ---------------------------------------------------------------------------
def test_the_agent_is_told_them_and_its_config_has_them_filled(tmp_path):
    from fastmdxplora.agent.propose import propose_config
    from fastmdxplora.agent.turns import ToolCall, Turn, Usage

    defaults = defaults_for(_write(tmp_path).parent)
    asked = []
    study = {"systems": [{"system": "1L2Y"}], "simulation": {"duration_ns": 10}}

    def complete(prompt):  # pragma: no cover - the tool path is taken
        raise AssertionError

    def turn(system, messages, tools):
        asked.append(messages[-1]["text"])
        return Turn("", (ToolCall("c", "propose_config", {
            "config": study, "note": "310 K, your default."}),), Usage(calls=1))

    complete.turn = turn
    proposal = propose_config("trp-cage for 10 ns", complete, defaults=defaults)
    assert "## Your defaults (fastmdx-defaults.yml)\n- `simulation.temperature_K`: 310 " \
           "(Our assays run at 310 K.)" in asked[0]
    assert proposal.accepted and proposal.config["simulation"] == {
        "duration_ns": 10, "temperature_K": 310, "timestep_fs": 2.0}
    assert proposal.config["decisions"]["simulation.temperature_K"]["source"] == DEFAULTS_FILE
    # In the text protocol too, from the same message.
    from fastmdxplora.agent.propose import prompt_for

    assert "## Your defaults" in prompt_for("x", defaults=defaults)
    assert "## Your defaults" not in prompt_for("x")


def test_a_default_that_does_not_fit_the_study_is_said_not_filled(tmp_path, monkeypatch):
    from fastmdxplora.agent import propose as propose_module
    from fastmdxplora.agent.propose import Proposal, _with_your_defaults

    defaults = defaults_for(_write(tmp_path).parent)
    accepted = Proposal(config={"systems": [{"system": "1L2Y"}]}, attempts=())
    calls = []

    def refuse_filled(config, **kwargs):
        calls.append(config)
        raise ConfigError("NVT with a barostat setting.", code="config.option.not_permitted",
                          option="ensemble")

    monkeypatch.setattr(propose_module, "validate_config", refuse_filled)
    kept = _with_your_defaults(accepted, defaults)
    assert kept.config == {"systems": [{"system": "1L2Y"}]}
    assert kept.note.startswith("Your defaults (fastmdx-defaults.yml) do not fit this study")


# ---------------------------------------------------------------------------
# The GUI and an AI app
# ---------------------------------------------------------------------------
def test_the_builder_offers_them_as_yours(tmp_path):
    from fastmdxplora.gui.schema_payload import schema_payload

    payload = schema_payload(defaults_for(_write(tmp_path).parent))
    simulation = {f["name"]: f for f in payload["phases"]["simulation"]["fields"]}
    assert simulation["temperature_K"]["default"] == 310
    assert simulation["temperature_K"]["default_from"] == DEFAULTS_FILE
    assert "default_from" not in simulation["duration_ns"]
    grouped = [f for g in payload["phases"]["simulation"]["groups"] for f in g["fields"]
               if f["name"] == "temperature_K"]
    assert grouped and all(f["default"] == 310 for f in grouped)
    assert schema_payload()["phases"]["simulation"]["fields"][0].get("default_from") is None


def test_an_ai_app_saves_and_checks_what_will_run(tmp_path, monkeypatch):
    from fastmdxplora.mcp import App, Workspace
    from tests._mcp_wire import Wire
    from tests.test_an_ai_app_reads_and_checks_studies import _structure

    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    _write(root)
    _structure(root / "ghg.pdb")
    wire = Wire(App(Workspace.at(root), complete_for=lambda: None).server())
    study = "systems:\n  - system: ghg.pdb\nsimulation:\n  duration_ns: 5\n"
    try:
        checked = wire.request("tools/call", {"name": "check_study", "arguments": {
            "config": study}})["result"]["content"][0]["text"]
        assert ("Your defaults (fastmdx-defaults.yml) fill what it leaves unset: "
                "simulation.temperature_K, simulation.timestep_fs, setup.ph.") in checked
        wire.request("tools/call", {"name": "save_study", "arguments": {
            "config": study, "name": "ghg"}})
    finally:
        wire.close()
    saved = yaml.safe_load((root / "ghg.yml").read_text())
    assert saved["simulation"]["temperature_K"] == 310
    assert saved["decisions"]["simulation.temperature_K"]["source"] == DEFAULTS_FILE


# ---------------------------------------------------------------------------
# Found in review
# ---------------------------------------------------------------------------
def test_settings_that_answer_one_question_are_not_filled_beside_each_other(tmp_path):
    # The file's `analysis.include` filled beside the study's own
    # `analysis.exclude` made a valid study fail as setting both.
    defaults = defaults_for(_write(tmp_path, {"analysis": {"include": ["rmsd", "rg"]}}).parent)
    study = {"systems": [{"system": "1L2Y"}], "analysis": {"exclude": ["sasa"]}}
    filled, names = with_defaults(study, defaults)
    assert names == [] and filled["analysis"] == {"exclude": ["sasa"]}
    alone, names = with_defaults({"systems": [{"system": "1L2Y"}]}, defaults)
    assert names == ["analysis.include"]


def test_a_refusal_your_defaults_cause_names_the_file(tmp_path, monkeypatch):
    import fastmdxplora.config.loader as loader
    from fastmdxplora.config.defaults_file import refused_with

    defaults = defaults_for(_write(tmp_path).parent)
    study = {"systems": [{"system": "1L2Y"}]}
    filled, _ = with_defaults(study, defaults)
    real = loader.validate_config

    def refuse_310(config, **kw):
        if config.get("systems") and (config.get("simulation") or {}).get("temperature_K") == 310:
            raise ConfigError("310 K is refused here.", code="config.option.out_of_range",
                              option="simulation.temperature_K")
        return real(config, **kw)

    monkeypatch.setattr(loader, "validate_config", refuse_310)
    refusal = refused_with(study, filled, defaults)
    assert refusal is not None and refusal.code == "config.option.out_of_range"
    assert str(refusal).startswith(f"With your defaults ({defaults.path}) filled in")
    assert "310 K is refused here." in str(refusal)
    assert refused_with(study, study, defaults) is None
    with pytest.raises(ConfigError) as caught:
        config_file = tmp_path / "study.yml"
        config_file.write_text("systems: [{system: 1L2Y}]\n")
        _explore(["explore", "--config", str(config_file), "--output", str(tmp_path / "a")])
    assert DEFAULTS_FILE in str(caught.value) and "--no-defaults" in str(caught.value)


def test_a_phase_run_again_from_a_config_is_not_filled(tmp_path, capsys):
    # `fastmdx analyze --config s.yml --output study` on a study that ran at
    # 300 K was given 310 K and a decision calling it the lab's.
    _write(tmp_path)
    study = tmp_path / "study"
    study.mkdir()
    (study / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "s1", "system": "1L2Y"}], "output": str(study),
        "simulation": {"duration_ns": 1, "temperature_K": 300}}))
    config_file = tmp_path / "s.yml"
    config_file.write_text("systems: [{system: 1L2Y}]\nsimulation: {duration_ns: 1}\n")
    config = _explore(["analyze", "--config", str(config_file), "--output", str(study)])
    assert "temperature_K" not in config["simulation"] and "decisions" not in config
    assert "are not filled in" in capsys.readouterr().out


def test_an_ai_app_is_refused_at_the_check_not_at_the_save(tmp_path, monkeypatch):
    from fastmdxplora.mcp import App, Workspace
    from tests._mcp_wire import Wire
    from tests.test_an_ai_app_reads_and_checks_studies import _structure

    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    _write(root, {"simulation": {"temperature_K": 310}})
    _structure(root / "ghg.pdb")
    import fastmdxplora.config.loader as loader

    real = loader.validate_config

    def refuse_310(config, **kw):
        if config.get("systems") and (config.get("simulation") or {}).get("temperature_K") == 310:
            raise ConfigError("310 K is refused here.", code="config.option.out_of_range",
                              option="simulation.temperature_K")
        return real(config, **kw)

    monkeypatch.setattr(loader, "validate_config", refuse_310)
    wire = Wire(App(Workspace.at(root), complete_for=lambda: None).server())
    try:
        checked = wire.request("tools/call", {"name": "check_study", "arguments": {
            "config": "systems:\n  - system: ghg.pdb\n"}})["result"]
    finally:
        wire.close()
    assert checked["isError"]
    assert "With your defaults" in checked["content"][0]["text"]


def test_an_ai_app_s_comments_and_order_are_kept_when_they_are_filled(tmp_path, monkeypatch):
    from fastmdxplora.mcp import App, Workspace
    from tests._mcp_wire import Wire
    from tests.test_an_ai_app_reads_and_checks_studies import _structure

    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    _write(root)
    _structure(root / "ghg.pdb")
    study = ("# my hand-written comment\nsystems:\n  - system: ghg.pdb   # the peptide\n"
             "simulation:\n    duration_ns: 5\n")
    wire = Wire(App(Workspace.at(root), complete_for=lambda: None).server())
    try:
        wire.request("tools/call", {"name": "save_study", "arguments": {
            "config": study, "name": "ghg", "by_hand": True}})
    finally:
        wire.close()
    text = (root / "ghg.yml").read_text()
    assert text.startswith("# my hand-written comment\nsystems:\n  - system: ghg.pdb   # the")
    assert "    temperature_K: 310\n" in text
    saved = yaml.safe_load(text)
    assert saved["simulation"] == {"temperature_K": 310, "timestep_fs": 2.0, "duration_ns": 5}
    assert saved["setup"] == {"ph": 7.0}
    assert saved["decisions"]["setup.ph"]["source"] == DEFAULTS_FILE
