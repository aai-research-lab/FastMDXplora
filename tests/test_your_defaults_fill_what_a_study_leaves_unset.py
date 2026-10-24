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


@pytest.mark.parametrize("setting, value", [
    ("simulation.resume_from", "runs/old"), ("simulation.resume_unsealed", True),
    ("simulation.setup_from", "runs/old"), ("simulation.prepared_from", "runs/old"),
    ("simulation.extra_ns", 5.0), ("analysis.trajectory", "/data/old/production.dcd"),
    ("analysis.topology", "/data/old/topology.pdb"), ("setup.fixed_pdb", "prepared.pdb"),
    ("setup.ligand", "ligand.sdf"), ("setup.ligand_pose", "file"),
    ("setup.mutations", ["L99A"]), ("setup.chains", ["A"]), ("setup.model", 2),
    ("setup.ligand_net_charge", -1), ("setup.ligand_name", "BEN"),
    ("setup.ligand_resname", "BEN"), ("setup.mutation_chain", "B"),
    ("setup.residue_states", {"A:57": "HIP"}), ("setup.membrane_center_z_nm", 2.0),
    ("simulation.dashboard_ligand_resname", "EPE"),
    ("simulation.umbrella", {"collective_variable": "distance", "from": 0.3, "to": 1.5,
                             "n_windows": 7, "force_constant": 5000}),
    ("simulation.steered", {"collective_variable": "distance", "to": 1.5, "steps": 1000}),
    ("simulation.metadynamics", {"collective_variable": "radius_of_gyration",
                                 "selection": "protein and name CA", "sigma": 0.02}),
    ("simulation.plumed", {"enabled": True, "script": "metad.dat"}),
    ("setup.membrane_orientation_checked", True),
    ("report.region_highlights", [{"label": "active site", "start": 57, "end": 102}]),
    ("report.title", "Lysozyme L99A"),
])
def test_a_setting_of_one_study_is_refused_in_the_file(tmp_path, setting, value):
    """A folder's defaults say how studies are run, never what is simulated
    or which study's files are read: the study one study continues or is
    prepared from, the files it analyses, its structure, ligand, mutations,
    chains and model are that study's, and so is every setting that names
    one structure's parts (a ligand's charge and name, residues by number,
    the chain mutated, the bilayer's place and its orientation checked by
    hand, a bias chosen for one system, one study's title). In the file,
    every new study made in the folder would take them (`L99A` would mutate
    every protein, `ligand_net_charge: -1` charge every ligand,
    `membrane_orientation_checked` build every bilayer unchecked across its
    own structure)."""
    from fastmdxplora.config.defaults_file import write_defaults

    block, _, key = setting.partition(".")
    data = {"simulation": {"temperature_K": 310}}
    data.setdefault(block, {})[key] = value
    with pytest.raises(ConfigError) as caught:
        read_defaults(_write(tmp_path, data))
    assert caught.value.code == "config.option.inapplicable"
    assert caught.value.refusal.details["option"] == setting
    said = str(caught.value)
    assert DEFAULTS_FILE in said and f"`{setting}`" in said
    assert "never what is simulated or which study's files are read" in said
    # Nor written into one (Settings' This folder), the file left as it was.
    where = _write(tmp_path / "written")
    before = where.read_text()
    with pytest.raises(ConfigError) as caught:
        write_defaults(where, given={setting: value})
    assert caught.value.code == "config.option.inapplicable"
    assert where.read_text() == before


@pytest.mark.parametrize("setting, value, shown, not_said", [
    ("setup.membrane_orientation_checked", False, "false", "turns off"),
    ("report.title", None, "null", "one study's title"),
    ("setup.mutations", [], "[]", "simulates"),
])
def test_a_setting_of_one_study_given_as_nothing_is_said_as_that(tmp_path, setting, value, shown,
                                                                 not_said):
    """A full config writes `membrane_orientation_checked: false`, and a
    file holding it was refused as saying one structure was oriented by
    hand and turning the membrane's stops off, the opposite of `false`;
    `report.title: null` as one study's title. Refused still, as what it is."""
    block, _, key = setting.partition(".")
    with pytest.raises(ConfigError) as caught:
        read_defaults(_write(tmp_path, {block: {key: value}}))
    said = str(caught.value)
    assert caught.value.code == "config.option.inapplicable"
    assert f"`{setting}`, which is one study's setting; given as `{shown}` it sets nothing" in said
    assert not_said not in said, said


@pytest.mark.parametrize("block, value, named", [
    ("analysis", {"options": {"pair_distance": {"selection_a": "resid 10 and name CA",
                                                "selection_b": "resid 50 and name CA"}}},
     "analysis.options.pair_distance.selection_a"),
    ("analysis", {"options": {"pair_distance": {"selection_b": "resid 50 and name CA"}}},
     "analysis.options.pair_distance.selection_b"),
    ("analysis", {"options": {"sasa": {"ligand_resname": "BEN"}}},
     "analysis.options.sasa.ligand_resname"),
    ("analysis", {"options": {"pl_interactions": {"ligand_net_charge": -1}}},
     "analysis.options.pl_interactions.ligand_net_charge"),
    ("analysis", {"options": {"pl_interactions": {"ligand_chemistry": "ben.sdf"}}},
     "analysis.options.pl_interactions.ligand_chemistry"),
    ("analysis", {"options": {"order_parameters": {"reference": "s2.txt"}}},
     "analysis.options.order_parameters.reference"),
    ("analysis", {"options": {"order_parameters": {"reference_exclude": "1,72-76"}}},
     "analysis.options.order_parameters.reference_exclude"),
    ("analysis", {"options": {"bfactor_comparison": {"structure": "1ubq.pdb"}}},
     "analysis.options.bfactor_comparison.structure"),
    ("analysis", {"options": {"thermodynamics": {"state_csv": "/data/old/energy.csv"}}},
     "analysis.options.thermodynamics.state_csv"),
    ("simulation", {"restrain": [{"kind": "position", "selection": "protein and name CA"},
                                 {"kind": "distance", "selection": "index 120 455",
                                  "force_constant": 1000, "target": 0.3}]},
     "simulation.restrain"),
    ("simulation", {"restrain": {"kind": "angle", "selection": "index 1 2 3",
                                 "force_constant": 100}}, "simulation.restrain"),
    ("simulation", {"restrain": [{"kind": "Torsion", "selection": "index 1 2 3 4",
                                  "force_constant": 100}]}, "simulation.restrain"),
])
def test_an_option_or_a_restraint_of_one_structure_s_atoms_is_refused(
        tmp_path, block, value, named):
    """An analysis's option that names one structure's atoms, its ligand,
    its residues by number or its own files, and a restraint between
    particular atoms, were filled into every new study: each new study
    measured the distance between residues 10 and 50 of its own protein.
    A selection every structure has, and position restraints on one, are a
    lab's."""
    from fastmdxplora.config.defaults_file import write_defaults

    data = {"simulation": {"temperature_K": 310}}
    data.setdefault(block, {}).update(value)
    with pytest.raises(ConfigError) as caught:
        read_defaults(_write(tmp_path, data))
    assert caught.value.code == "config.option.inapplicable"
    assert caught.value.refusal.details["option"] == named
    said = str(caught.value)
    assert f"`{named}`" in said and "never what is simulated" in said
    where = _write(tmp_path / "written")
    before = where.read_text()
    key, held = next(iter(value.items()))
    with pytest.raises(ConfigError):
        write_defaults(where, given={f"{block}.{key}": held})
    assert where.read_text() == before


def test_a_lab_s_selections_and_position_restraints_are_taken(tmp_path):
    data = {"analysis": {"options": {"rdf": {"selection_a": "protein",
                                             "selection_b": "water and name O"},
                                     "pl_contacts": {"protein_selection": "protein"},
                                     "rmsd": {"selection": "name CA"}}},
            "simulation": {"restrain": [{"kind": "position", "selection": "protein and name CA",
                                         "force_constant": 500},
                                        "backbone"]}}
    defaults = read_defaults(_write(tmp_path, data))
    assert dict(defaults.settings())["simulation.restrain"][1] == "backbone"
    assert read_defaults(_write(tmp_path / "short", {
        "simulation": {"restrain": "protein and not element H"}})).values


def test_each_refusal_of_one_study_s_setting_says_what_it_is():
    """`resume_unsealed` names no study, and `setup_from` prepares a study
    rather than continuing one: each is said as what it is."""
    from fastmdxplora.config.defaults_file import _ONE_STUDY_SAID

    assert "continues" in _ONE_STUDY_SAID["simulation.resume_from"]
    assert "names" not in _ONE_STUDY_SAID["simulation.resume_unsealed"]
    for prepared in ("simulation.setup_from", "simulation.prepared_from"):
        assert "prepared from" in _ONE_STUDY_SAID[prepared]
        assert "continue" not in _ONE_STUDY_SAID[prepared]
    assert "files" in _ONE_STUDY_SAID["analysis.trajectory"]
    assert "simulates" in _ONE_STUDY_SAID["setup.mutations"]
    # The pose says where the starting coordinates come from, not what is
    # simulated; each setting of one structure's parts says its own.
    assert "starting coordinates" in _ONE_STUDY_SAID["setup.ligand_pose"]
    assert "simulates" not in _ONE_STUDY_SAID["setup.ligand_pose"]
    assert "charge" in _ONE_STUDY_SAID["setup.ligand_net_charge"]
    assert "chain" in _ONE_STUDY_SAID["setup.mutation_chain"]
    assert "residues" in _ONE_STUDY_SAID["setup.residue_states"]
    assert "bilayer" in _ONE_STUDY_SAID["setup.membrane_center_z_nm"]
    assert "dashboard" in _ONE_STUDY_SAID["simulation.dashboard_ligand_resname"]
    # A bias is along variables chosen for one system, which may name no
    # structure's atoms (the radius of gyration of `protein and name CA`).
    for bias in ("simulation.umbrella", "simulation.steered", "simulation.metadynamics",
                 "simulation.plumed"):
        assert "variables chosen for it" in _ONE_STUDY_SAID[bias], bias
        assert "atoms" not in _ONE_STUDY_SAID[bias], bias
    assert "oriented by hand" in _ONE_STUDY_SAID["setup.membrane_orientation_checked"]
    assert "stops" in _ONE_STUDY_SAID["setup.membrane_orientation_checked"]
    assert "residues" in _ONE_STUDY_SAID["report.region_highlights"]
    assert "title" in _ONE_STUDY_SAID["report.title"]


def test_the_rule_is_said_in_the_docs():
    from pathlib import Path

    docs = Path(__file__).resolve().parent.parent / "docs"
    refusals = (docs / "refusals.md").read_text(encoding="utf-8")
    row = [line for line in refusals.splitlines() if line.startswith("| `config.option.inapplicable`")]
    assert row and "never what is simulated or which study's files are read" in row[0]
    config = (docs / "config.md").read_text(encoding="utf-8")
    assert "never what is simulated or which study's files are read" in config
    # Each setting refused is named in both.
    from fastmdxplora.config.defaults_file import (
        _ONE_STUDY_OPTIONS, _ONE_STUDY_SAID, _RESTRAINT_OF_ATOMS)

    section = config[config.index("never what is simulated"):config.index("**Which file.**")]
    for name in [*_ONE_STUDY_SAID, *_ONE_STUDY_OPTIONS]:
        key = name.partition(".")[2] or name
        assert f"`{key}`" in row[0] or f"`{name}`" in row[0], name
        assert f"`{key}`" in section or f"`{name}`" in section, name
    for kind in _RESTRAINT_OF_ATOMS:
        assert kind in row[0] and kind in section, kind
    assert "`restrain`" in row[0] and "`simulation.restrain`" in section


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
