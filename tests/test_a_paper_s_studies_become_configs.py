"""A paper's MD studies become configs, each setting from the paper's words.

An AI model lists the studies and reads their settings and results; each
value is used only where its words are the paper's and hold it. Each study
is then written as a config, each setting as stated, not stated, differing
here, needing the person, or not possible here, with the paper's words as
its reason; a study that cannot run is not written, and one that needs the
person is refused by the validator until it is completed.
"""

from __future__ import annotations

import argparse
import copy
import json

import pytest
import yaml

from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.paper import PaperRefused
from fastmdxplora.paper.extract import check_reading, read_studies
from fastmdxplora.paper.mapping import STRUCTURE_TO_GIVE, plan_study
from fastmdxplora.paper.studies import choose, plans_for, studies_in, write_configs
from fastmdxplora.paper.text import read_paper

from tests._a_paper import CLAIMS, DOI, PROTOCOL, STUDIES, field, jats, scripted


@pytest.fixture(autouse=True)
def _own_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def paper_file(tmp_path):
    path = tmp_path / "paper.xml"
    path.write_bytes(jats())
    return path


def _plans(paper_file, model=None, **kwargs):
    _paper, reading = studies_in(str(paper_file), complete=model or scripted(), model="test/model")
    return reading, plans_for(reading, **kwargs)


def _choice(plan, field_name, setting=None):
    return next(c for c in plan["choices"]
                if c["field"] == field_name and (setting is None or c.get("setting") == setting))


def test_each_study_is_written_as_the_paper_states_it(paper_file):
    reading, plans = _plans(paper_file)
    assert [plan["id"] for plan in plans] == ["S1", "S2"]
    config = plans[0]["config"]
    assert config["systems"][0]["system"] == "1UBQ"
    setup, simulation = config["setup"], config["simulation"]
    assert setup["forcefield"] == "amber14"
    assert setup["box_shape"] == "octahedron"
    # 10 Å to the box's edge is 2 nm to the nearest image, as padding is measured here.
    assert setup["solvent_padding_nm"] == pytest.approx(2.0)
    assert setup["ion_concentration_M"] == pytest.approx(0.15)
    assert setup["constraints"] == "HBonds" and setup["nonbonded_cutoff_nm"] == 1.0
    assert setup["nonbonded_method"] == "PME"
    assert simulation["temperature_K"] == 300 and simulation["pressure_bar"] == 1
    assert simulation["timestep_fs"] == 2
    assert simulation["nvt_duration_ns"] == 1 and simulation["npt_duration_ns"] == 1
    assert simulation["duration_ns"] == 100 and simulation["ensemble"] == "npt"
    assert config["sweep"] == {"simulation.random_seed": [1, 2, 3]}
    assert plans[1]["config"]["setup"]["mutations"] == ["L50A"]
    validate_config(copy.deepcopy(config), require_systems=True)


def test_each_setting_s_reason_is_the_paper_s_words(paper_file):
    _reading, plans = _plans(paper_file)
    config = plans[0]["config"]
    why = config["decisions"]["simulation.temperature_K"]["why"]
    assert "at 300 K" in why and "Methods: Molecular dynamics simulations" in why
    assert config["decisions"]["simulation.temperature_K"]["source"] == f"doi:{DOI}"
    assert config["paper"]["doi"] == DOI and config["paper"]["study"] == "S1"
    assert config["paper"]["read_by"] == "FastMDXplora with test/model"
    assert [claim["analysis"] for claim in config["paper"]["claims"]] == ["rmsd", "rg"]


def test_what_openmm_does_otherwise_is_said_to_differ(paper_file):
    _reading, plans = _plans(paper_file)
    plan = plans[0]
    assert _choice(plan, "thermostat")["label"] == "differs"
    assert "Langevin" in _choice(plan, "thermostat")["why"]
    assert _choice(plan, "barostat")["label"] == "differs"
    assert _choice(plan, "engine")["label"] == "differs"
    assert plan["state"] == "with_differences"


def test_a_value_the_paper_does_not_say_is_not_used(paper_file):
    invented = copy.deepcopy(PROTOCOL)
    # Words the paper does not contain.
    invented["temperature"] = field(310, "Simulations were run at 310 K", "K")
    # The paper's words, not holding the value: a total over the replicas.
    studies = copy.deepcopy(STUDIES)
    studies[0]["fields"]["production"] = field(300, "three independent 100-ns simulations", "ns")
    reading, plans = _plans(paper_file, scripted(studies=studies, protocol=invented))
    fields = reading["studies"][0]["fields"]
    assert fields["temperature"]["status"] == "not_found"
    assert fields["production"]["status"] == "unread"
    simulation = plans[0]["config"]["simulation"]
    assert "temperature_K" not in simulation and "duration_ns" not in simulation
    assert plans[0]["state"] == "needs_you"


def test_a_name_is_held_only_where_its_words_name_it(paper_file):
    protocol = copy.deepcopy(PROTOCOL)
    protocol["protein_forcefield"] = field("CHARMM36m", "described by the AMBER ff14SB force field")
    reading, _plans_ = _plans(paper_file, scripted(protocol=protocol))
    assert reading["studies"][0]["fields"]["protein_forcefield"]["status"] == "unread"


def test_a_study_that_needs_the_person_is_refused_until_completed(paper_file):
    studies = copy.deepcopy(STUDIES)
    studies[0]["fields"].pop("pdb_id")
    studies[0]["fields"]["structure_source"] = field(
        "built in silico", "The L50A mutant was built from it in silico")
    _reading, plans = _plans(paper_file, scripted(studies=studies))
    plan = plans[0]
    assert plan["state"] == "needs_you"
    config = plan["config"]
    assert config["systems"][0]["system"] == STRUCTURE_TO_GIVE
    assert config["paper"]["needs"]
    with pytest.raises(ConfigError, match="not complete") as refused:
        validate_config(copy.deepcopy(config), require_systems=True)
    assert refused.value.code == "config.option.missing_companion"
    completed = copy.deepcopy(config)
    completed["systems"][0]["system"] = "1UBQ"
    del completed["paper"]["needs"]
    validate_config(completed, require_systems=True)


@pytest.mark.parametrize("protein, water, ligand, label, expected", [
    ("OPLS-AA/L", None, False, "not_possible", None),
    ("CHARMM22*", None, False, "not_possible", None),
    ("CHARMM36m", "TIP3P", False, "as_stated", ["charmm36_2024.xml", "charmm36_2024/water.xml"]),
    ("ff19SB", "OPC", False, "as_stated", ["amber19/protein.ff19SB.xml", "amber19/opc.xml"]),
    ("ff99SB-ILDN", "TIP4P-Ew", False, "as_stated", ["amber99sbildn.xml", "tip4pew.xml"]),
    ("ff99SB", "TIP3P", True, "differs", "amber-openff"),
    ("CHARMM36m", "TIP3P", True, "not_possible", None),
])
def test_a_force_field_is_one_openmm_ships_or_the_study_cannot_run(protein, water, ligand,
                                                                   label, expected):
    def stated(value):
        return {"status": "stated", "value": value, "quote": str(value), "where": "Methods"}

    fields = {"pdb_id": stated("1UBQ"), "protein_forcefield": stated(protein),
              "production": {**stated(10.0), "unit": "ns"}}
    if water:
        fields["water_model"] = stated(water)
    if ligand:
        fields["ligands"] = stated(["benzamidine"])
    plan = plan_study({"id": "S1", "label": "x", "fields": fields}, {"doi": DOI}, files=None)
    choice = _choice(plan, "protein_forcefield")
    assert choice["label"] == label
    if expected is None:
        assert plan["state"] == "cannot_run" and plan["config"] is None
    elif isinstance(expected, str):
        assert plan["config"]["setup"]["forcefield"] == expected
    else:
        assert plan["config"]["setup"]["force_field"] == expected


def test_a_force_field_this_openmm_lacks_needs_a_newer_one():
    fields = {"pdb_id": {"status": "stated", "value": "1UBQ", "quote": "1UBQ"},
              "protein_forcefield": {"status": "stated", "value": "ff19SB", "quote": "ff19SB"},
              "water_model": {"status": "stated", "value": "OPC", "quote": "OPC"}}
    plan = plan_study({"id": "S1", "label": "x", "fields": fields}, {}, files={"amber14-all.xml"})
    choice = _choice(plan, "protein_forcefield")
    assert choice["label"] == "needs_you" and "OpenMM 8.3" in choice["why"]


@pytest.mark.parametrize("method, details", [
    ("replica_exchange", ""),
    ("metadynamics", "well-tempered bias-exchange metadynamics with four replicas"),
    ("free_energy", "alchemical decoupling"),
    ("coarse_grained", "Martini 2.2"),
])
def test_a_method_this_software_does_not_run_cannot_run(method, details):
    fields = {"pdb_id": {"status": "stated", "value": "1UBQ", "quote": "1UBQ"},
              "method": {"status": "stated", "value": method, "quote": method}}
    if details:
        fields["method_details"] = {"status": "stated", "value": details, "quote": details}
    plan = plan_study({"id": "S1", "label": "x", "fields": fields}, {}, files=None)
    assert plan["state"] == "cannot_run" and plan["config"] is None


def test_until_determined_stops_at_the_paper_s_own_error(paper_file):
    _reading, plans = _plans(paper_file, until_determined=True)
    simulation = plans[0]["config"]["simulation"]
    rule = simulation["stop_when"]
    assert rule["max_duration_ns"] == 100
    assert {"analysis": "rmsd", "standard_error": 0.01} in rule["measures"]
    assert simulation["duration_ns"] == 20
    validate_config(copy.deepcopy(plans[0]["config"]), require_systems=True)


def test_a_reading_is_kept_and_not_asked_again(paper_file):
    model = scripted()
    studies_in(str(paper_file), complete=model, model="test/model")
    asked = len(model.asked)
    assert asked == 3
    studies_in(str(paper_file), complete=model, model="test/model")
    assert len(model.asked) == asked


def test_a_reply_that_is_not_json_is_refused(paper_file):
    paper = read_paper(paper_file)
    with pytest.raises(PaperRefused, match="not in the form it reads"):
        read_studies(paper, lambda prompt: "I could not find any.", model="x", use_kept=False)


def test_studies_are_chosen_by_id_number_or_all(paper_file):
    _reading, plans = _plans(paper_file)
    assert [p["id"] for p in choose(plans, "S2")] == ["S2"]
    assert [p["id"] for p in choose(plans, "1, 2")] == ["S1", "S2"]
    assert len(choose(plans, "all")) == 2
    with pytest.raises(PaperRefused, match="no study S9"):
        choose(plans, "S9")


def test_the_chosen_studies_are_written_beside_one_another(paper_file, tmp_path):
    _reading, plans = _plans(paper_file)
    written = write_configs(plans, tmp_path / "ubq.yml")
    paths = [entry["path"].name for entry in written]
    assert paths == ["ubq-s1.yml", "ubq-s2.yml"]
    text = written[0]["path"].read_text(encoding="utf-8")
    assert text.startswith("# S1: Ubiquitin, wild type")
    assert "&id" not in text  # no YAML anchors in a file a person reads
    assert all(entry["refused"] is None for entry in written)
    with pytest.raises(PaperRefused, match="already exists"):
        write_configs(plans, tmp_path / "ubq.yml")


def test_the_command_lists_then_writes(paper_file, tmp_path, monkeypatch, capsys):
    from fastmdxplora.paper import command, studies

    monkeypatch.setattr(studies, "the_ai_model", lambda: (scripted(), "test/model"))
    args = argparse.Namespace(paper=str(paper_file), paper_si=None, paper_studies=None,
                              paper_until_determined=False,
                              config_file=str(tmp_path / "study.yml"), force=False)
    assert command.config_from_paper(args) == 0
    listed = capsys.readouterr().out
    assert "S1  Ubiquitin, wild type" in listed and "[3 x 100 ns]" in listed
    assert not (tmp_path / "study.yml").exists()
    args.paper_studies = "S2"
    assert command.config_from_paper(args) == 0
    written = yaml.safe_load((tmp_path / "study.yml").read_text(encoding="utf-8"))
    assert written["paper"]["study"] == "S2"
    assert "fastmdx explore --config" in capsys.readouterr().out


def test_the_paper_flags_need_the_paper():
    from fastmdxplora.cli.main import main

    assert main(["config", "--paper-studies", "all"]) == 2


def test_a_claim_is_kept_only_with_its_number_and_unit_in_the_paper(paper_file):
    paper = read_paper(paper_file)
    claims = copy.deepcopy(CLAIMS)
    claims[0]["value"] = 0.13  # not in the row quoted
    claims[1]["unit"] = "Å"    # not the table's unit
    raw = {"studies": copy.deepcopy(STUDIES), "protocol_fields": {"P1": PROTOCOL},
           "claims": claims}
    reading = check_reading(paper, raw, model="x")
    statuses = [c["status"] for c in reading["studies"][0]["claims"]]
    assert statuses == ["unread", "unread"]


def test_the_config_s_paper_record_is_checked():
    base = {"systems": [{"system": "1UBQ"}]}
    validate_config({**base, "paper": {"doi": DOI, "claims": [{"value": 1.0}]}})
    with pytest.raises(ConfigError, match="no number for its `value`"):
        validate_config({**base, "paper": {"claims": [{"value": "about one"}]}})
    with pytest.raises(ConfigError, match="has 'journal'"):
        validate_config({**base, "paper": {"journal": "J Test"}})
    with pytest.raises(ConfigError):
        validate_config({**base, "paper": "10.9999/x"})
    assert json.dumps({**base, "paper": {"needs": []}})  # an empty list holds nothing back
    validate_config({**base, "paper": {"needs": []}})
