"""What a paper does not say is not made to say it.

A quote that starts inside a number, a count that is some other number, a
flag its words deny, an error whose kind the paper does not give: none is
used as the paper's. A reply cut off part way is refused, not read as one
that found nothing. A study is written with every setting it always has
said, the paper's or this software's; an AMBER force field is not switched
and a CHARMM one is switched as it is developed; ions are no ligand; a
membrane is read however it is worded; plain MD stays plain whatever its
details mention. The file written is the config and nothing more, whatever
words the paper or an AI app gives its label.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
import yaml

from fastmdxplora.paper import PaperRefused
from fastmdxplora.paper.extract import _json_from, check_claim, check_field, read_studies
from fastmdxplora.paper.mapping import plan_study
from fastmdxplora.paper.quotes import QuoteIndex
from fastmdxplora.paper.text import Part, PaperText, jats_parts

from tests._a_paper import jats, scripted


def _paper(*texts: str) -> PaperText:
    return PaperText(parts=[Part(f"p. {n}", text, "paper") for n, text in enumerate(texts, 1)])


def _stated(value, quote="the words", where="Methods"):
    return {"status": "stated", "value": value, "quote": quote, "where": where}


def _plan(fields, **kwargs):
    return plan_study({"id": "S1", "label": "a study", "fields": fields}, {}, **kwargs)


def _choices(plan, field):
    return [choice for choice in plan["choices"] if choice["field"] == field]


# -- the words ---------------------------------------------------------------
@pytest.mark.parametrize("text, quote", [
    ("Simulations of 1.5 µs were performed at 310 K.", "5 µs were performed at 310 K"),
    ("Each system had 150 ns of equilibration first.", "50 ns of equilibration first"),
    ("A cutoff of 12 Å was used throughout.", "A cutoff of 1"),
])
def test_a_quote_that_starts_or_ends_inside_a_number_is_not_the_paper_s(text, quote):
    assert QuoteIndex(_paper(text)).find(quote) is None


def test_a_quote_of_whole_numbers_is_found():
    index = QuoteIndex(_paper("Each system had 150 ns of equilibration first."))
    assert index.find("150 ns of equilibration") is not None


def test_a_number_read_from_inside_another_is_not_stated():
    index = QuoteIndex(_paper("Simulations of 1.5 µs were performed at 310 K."))
    record = check_field(index, "production", {"value": 5, "unit": "µs",
                                               "quote": "5 µs were performed at 310 K"})
    assert record["status"] == "not_found"


@pytest.mark.parametrize("quote, given, status, value", [
    ("Production consisted of three independent runs.", 3, "stated", 3),
    ("All runs were done in triplicate.", 3, "stated", 3),
    ("We ran 3 x 100 ns of each system.", 3, "stated", 3),
    ("Integration used a 2 fs time step for the dynamics.", 2, "unread", None),
    ("The single protein was placed in a cubic box.", 1, "unread", None),
    ("We performed 2 independent simulations of each of the 4 systems.", 4, "unread", None),
    ("We performed 2 independent simulations of each of the 4 systems.", 2, "stated", 2),
    ("Each system was run three times for 100 ns.", 3, "stated", 3),
])
def test_a_count_of_replicas_is_a_number_beside_what_it_counts(quote, given, status, value):
    record = check_field(QuoteIndex(_paper(quote)), "replicas", {"value": given, "quote": quote})
    assert record["status"] == status
    assert record.get("value") == value


@pytest.mark.parametrize("quote, given, status, value", [
    ("The system was neutralized with sodium ions.", True, "stated", True),
    ("The system was not neutralized by counterions.", False, "stated", False),
    ("The system was not neutralized by counterions.", True, "unread", None),
    ("Sodium chloride was added to 150 mM in the box.", True, "unread", None),
    ("The system was neutralized by adding Na+ counterions, and no additional salt was added.",
     True, "stated", True),
    ("Neutralized with Cl- ions; non-bonded interactions were cut at 1 nm.", True, "stated",
     True),
    ("Counterions were not added to the box.", False, "stated", False),
])
def test_whether_the_system_was_neutralised_is_read_from_the_words(quote, given, status, value):
    record = check_field(QuoteIndex(_paper(quote)), "neutralized", {"value": given, "quote": quote})
    assert record["status"] == status
    assert record.get("value") == value


def test_a_result_s_error_is_a_standard_error_only_where_the_paper_says_so():
    text = "Table 2 | RMSD | 0.21 ± 0.03 over 2000 frames of the run"
    index = QuoteIndex(_paper(text))
    claim = check_claim(index, {"quantity": "rmsd", "value": 0.21, "error": 0.03, "unit": "",
                                "error_kind": "standard_error", "n": 2000,
                                "quote": "RMSD | 0.21 ± 0.03"})
    assert claim["status"] == "stated"
    assert claim["error_kind"] == "unstated"  # compared as a spread
    assert claim["n"] is None  # 2000 frames are no runs


@pytest.mark.parametrize("text", [
    "Table 3 | RMSD | 0.21 ± 0.03, a mean that is per se of every frame",
    "Table 3 | RMSD | 0.21 ± 0.03 over 95% of frames",
])
def test_a_label_s_number_or_a_word_like_an_error_s_is_not_read_as_one(text):
    claim = check_claim(QuoteIndex(_paper(text)), {
        "quantity": "rmsd", "value": 0.21, "error": 0.03, "unit": "",
        "error_kind": "confidence_95" if "95" in text else "standard_error", "n": 3,
        "quote": "RMSD | 0.21 ± 0.03"})
    assert (claim["error_kind"], claim["n"]) == ("unstated", None)


def test_a_figure_s_letter_is_no_angstrom():
    from fastmdxplora.paper.values import candidates

    assert candidates("as Fig. 2a shows, with a cutoff of 10 Å", "length") == [1.0]


def test_a_result_s_error_kind_and_runs_are_kept_where_the_paper_says_them():
    text = ("Table 1 | RMSD | 0.21 ± 0.03. Errors are standard errors of the mean over "
            "three independent replicas.")
    claim = check_claim(QuoteIndex(_paper(text)), {
        "quantity": "rmsd", "value": 0.21, "error": 0.03, "unit": "",
        "error_kind": "standard_error", "n": 3, "quote": "RMSD | 0.21 ± 0.03"})
    assert (claim["error_kind"], claim["n"]) == ("standard_error", 3)


def test_a_result_that_is_no_finite_number_is_not_stated():
    index = QuoteIndex(_paper("The RMSD was Infinity in this odd table row of ours."))
    claim = check_claim(index, {"quantity": "rmsd", "value": float("inf"), "unit": "",
                                "quote": "The RMSD was Infinity in this odd"})
    assert claim["status"] == "unread"


def test_a_config_with_an_infinite_result_is_refused():
    from fastmdxplora.config.loader import ConfigError, validate_config

    config = {"systems": [{"system": "1UBQ"}], "paper": {"study": "S1", "claims": [
        {"quantity": "rmsd", "value": float("inf")}]}}
    with pytest.raises(ConfigError, match="no number"):
        validate_config(config)


# -- the AI model's replies --------------------------------------------------
def test_a_reply_cut_off_part_way_is_refused_not_read_as_empty():
    whole = json.dumps({"fields": {"temperature": {"value": 300, "unit": "K",
                                                   "quote": "at 300 K"},
                                   "timestep": {"value": 2, "unit": "fs", "quote": "2 fs"}}})
    cut = whole[: int(len(whole) * 0.8)]
    with pytest.raises(PaperRefused) as refused:
        _json_from(cut, "fields")
    assert refused.value.code == "environment.service.unusable_response"
    assert _json_from("Here it is: " + whole, "fields")["fields"]["timestep"]["value"] == 2


def test_a_reading_with_a_cut_reply_is_refused_and_not_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path))
    paper = PaperText(parts=jats_parts(jats(), "paper")[0])
    model = scripted()
    answer = model.__call__

    def cut(prompt):
        reply = answer(prompt)
        return reply[: len(reply) // 2] if "settings of one of its MD protocols" in prompt else reply

    with pytest.raises(PaperRefused):
        read_studies(paper, cut, model="test/model")
    assert not list(tmp_path.rglob("reading-*.json"))


def test_results_given_again_on_a_later_page_are_listed_once(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path))
    paper = PaperText(parts=jats_parts(jats(), "paper")[0])
    model = scripted()
    answer = model.__call__
    pages = {"n": 0}

    def again(prompt):
        reply = answer(prompt)
        if "numerical results its MD studies report" in prompt:
            pages["n"] += 1
            data = json.loads(reply)
            data["more"] = True  # says there are more, and gives the same again
            return json.dumps(data)
        return reply

    reading = read_studies(paper, again, model="test/model")
    claims = [c for s in reading["studies"] for c in s.get("claims") or []]
    keys = [(c.get("quantity"), c.get("value")) for c in claims]
    assert len(keys) == len(set(keys))
    assert pages["n"] == 2  # the second page added nothing, so no third


def test_paging_that_stops_with_more_to_come_is_said(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path))
    paper = PaperText(parts=jats_parts(jats(), "paper")[0])
    answer = scripted().__call__

    def renumbered(prompt):
        reply = answer(prompt)
        if "list the molecular dynamics (MD) studies" in prompt:
            data = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
            data["more"] = True  # and, asked again, gives S1 and S2 again
            return json.dumps(data)
        return reply

    said = []
    read_studies(paper, renumbered, model="test/model", said=said.append)
    assert any("gave none new" in line for line in said)


# -- the config --------------------------------------------------------------
def test_every_setting_a_study_always_has_is_said():
    plan = _plan({"pdb_id": _stated("1UBQ"), "protein_forcefield": _stated("ff14SB"),
                  "water_model": _stated("TIP3P"), "production": _stated(100.0)})
    said = {choice["field"]: choice["label"] for choice in plan["choices"]}
    for field in ("temperature", "pressure", "timestep", "box_shape", "padding",
                  "salt_concentration", "constraints", "electrostatics", "ensemble",
                  "thermostat", "nvt_equilibration", "npt_equilibration"):
        assert said.get(field) == "not_stated", field
    assert plan["state"] == "ready"


def test_a_temperature_the_ai_model_gave_and_the_paper_does_not_hold_needs_you():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "temperature": {"status": "not_found", "said": {"value": 310}}})
    temperature = _choices(plan, "temperature")
    assert temperature[0]["label"] == "needs_you"
    assert temperature[0]["setting"] == "simulation.temperature_K"
    assert plan["state"] == "needs_you"
    assert any("temperature" in need for need in plan["config"]["paper"]["needs"])


def test_an_amber_force_field_as_files_is_not_switched():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff19SB"), "water_model": _stated("OPC"),
                  "cutoff": _stated(1.0)}, files=None)
    setup = plan["config"]["setup"]
    assert setup["force_field"] == ["amber19/protein.ff19SB.xml", "amber19/opc.xml"]
    assert setup["use_switching_function"] is False
    assert _choices(plan, "switch")[0]["label"] == "not_stated"


def test_a_charmm_force_field_with_its_cutoff_stated_is_switched_inside_it():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("CHARMM36m"), "water_model": _stated("TIP3P"),
                  "cutoff": _stated(1.2)}, files=None)
    setup = plan["config"]["setup"]
    assert setup["use_switching_function"] is True
    assert setup["switch_distance_nm"] == pytest.approx(1.0)
    assert setup["nonbonded_cutoff_nm"] == pytest.approx(1.2)


def test_a_water_model_alone_does_not_make_a_protein_force_field_stated():
    tip3p = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                   "water_model": _stated("TIP3P")})
    assert _choices(tip3p, "protein_forcefield")[0]["label"] == "not_stated"
    opc = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                 "water_model": _stated("OPC")}, files=None)
    assert opc["config"]["setup"]["force_field"][0] == "amber14/protein.ff14SB.xml"
    assert _choices(opc, "protein_forcefield")[0]["label"] == "not_stated"


def test_no_additional_salt_is_no_salt():
    quote = "The system was neutralized by adding Na+ counterions, and no additional salt was added."
    index = QuoteIndex(_paper(quote))
    salt = check_field(index, "salt_concentration", {"value": 0, "unit": "M", "quote": quote})
    assert (salt["status"], salt["value"]) == ("stated", 0.0)


def test_a_bilayer_beside_an_amber_list_gets_lipid_parameters_and_a_rectangular_box():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff19SB"), "water_model": _stated("OPC"),
                  "membrane": _stated("pure DPPC bilayer with 128 lipids")}, files=None)
    assert plan["state"] == "ready"
    assert "amber14/lipid17.xml" in plan["config"]["setup"]["force_field"]
    lipids = _choices(plan, "lipid_forcefield")[0]
    assert lipids["label"] == "not_stated" and "fitted with TIP3P water, not OPC" in lipids["why"]
    assert "rectangular" in _choices(plan, "box_shape")[0]["why"]


def test_a_water_model_alone_is_one_choice_for_the_force_field():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "water_model": _stated("OPC")}, files=None)
    force_field = _choices(plan, "protein_forcefield")
    assert [c["label"] for c in force_field] == ["not_stated"]
    assert force_field[0]["why"].startswith("ff14SB with OPC")


def test_a_water_model_given_by_reference_names_the_water_used():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"),
                  "water_model": {"status": "by_reference", "quote": "as before"}})
    water = _choices(plan, "water_model")[0]
    assert water["label"] == "needs_you"
    assert "is TIP3P)" in water["why"] and "CHARMM" not in water["why"]


def test_a_count_of_replicas_the_paper_gives_and_this_cannot_read_needs_you():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "replicas": {"status": "unread", "quote": "run thrice", "said": {"value": 3}}})
    replicas = _choices(plan, "replicas")
    assert replicas[0]["label"] == "needs_you" and "one run" in replicas[0]["why"]
    one = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                 "replicas": _stated(1)})
    assert not _choices(one, "replicas")  # stated: nothing to add


def test_lipid21_is_said_to_need_openmm_8_4():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff19SB"), "lipid_forcefield": _stated("Lipid21"),
                  "water_model": _stated("OPC")}, files=set())
    assert "OpenMM 8.4 or later" in _choices(plan, "protein_forcefield")[0]["why"]


def test_an_older_amber_force_field_beside_a_nucleic_one_cannot_run():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff99SB-ILDN"),
                  "nucleic_forcefield": _stated("OL15"), "water_model": _stated("TIP3P")})
    assert plan["state"] == "cannot_run"
    assert "amber99sbildn.xml" in _choices(plan, "nucleic_forcefield")[0]["why"]


@pytest.mark.parametrize("ligands", [["Zn2+"], "none (the inhibitor was removed)",
                                     ["Na+", "Cl-"], ["crystallographic waters"]])
def test_ions_water_and_none_are_no_ligand(ligands):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff19SB"), "water_model": _stated("OPC"),
                  "ligands": _stated(ligands)}, files=None)
    assert "forcefield" not in plan["config"]["setup"]  # not amber-openff
    assert plan["config"]["setup"]["force_field"][0] == "amber19/protein.ff19SB.xml"


def test_a_ligand_beside_charmm_lipids_cannot_run():
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "lipid_forcefield": _stated("CHARMM36"), "ligands": _stated(["benzamidine"])})
    assert plan["state"] == "cannot_run"


@pytest.mark.parametrize("membrane, state, lipid", [
    ("a POPC bilayer", "ready", "POPC"),
    ("POPC, 144 lipids (72 per leaflet)", "ready", "POPC"),
    ("POPC with 5% PIP2", "cannot_run", None),
    ("POPC:POPG 3:1", "cannot_run", None),
    ("POPC (100%)", "ready", "POPC"),
    ("POPC bilayer, 50% hydrated", "ready", "POPC"),
    ("POPC with 30% cholesterol", "cannot_run", None),
    ("POPC bilayer built with CHARMM-GUI", "ready", "POPC"),
    ("POPE bilayer, 256 lipids, TIP3P water", "ready", "POPE"),
    ("POPC with PIP2 and SM", "cannot_run", None),
])
def test_a_membrane_is_read_however_it_is_worded(membrane, state, lipid):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "membrane": _stated(membrane)})
    assert plan["state"] == state
    if lipid:
        assert plan["config"]["setup"]["membrane"] == lipid


@pytest.mark.parametrize("details, method", [
    ("Unbiased MD of the folded protein at 1 bar", "plain"),
    ("Plain MD run on AMD GPUs", "plain"),
    ("Two 6 us runs, analysed with a Markov state model (MSM)", "plain"),
    ("No replica exchange was used", "plain"),
    ("Bias-exchange metadynamics with four replicas", "replica_exchange"),
    ("Generalized Born implicit solvent model (GB-OBC II)", "implicit_solvent"),
    ("Gaussian accelerated MD (GaMD)", "accelerated"),
    ("Conventional MD, compared with REMD from ref 12", "plain"),
    ("Starting structures were taken from our previous REMD simulations", "plain"),
    ("REST2 (ref 14) with 16 replicas", "replica_exchange"),
    ("GaMD boost potential applied, dual-boost, as in ref 30", "accelerated"),
    ("Alchemical free energy perturbation, 20 lambda windows (protocol from ref 5)",
     "free_energy"),
    ("Well-tempered bias-exchange metadynamics, following our previous protocol",
     "replica_exchange"),
    ("Simulated tempering over a temperature range (range not stated in text)",
     "replica_exchange"),
    ("GaMD production was started from the final frame of the previous conventional MD run",
     "accelerated"),
    ("The generalized Born implicit solvent model with parameters taken from reference 25",
     "implicit_solvent"),
    ("Solvation was the GB-OBC II model, which has not been widely used for IDPs",
     "implicit_solvent"),
    ("Replica exchange with 32 replicas, as reported in our earlier work", "replica_exchange"),
    ("Alchemical decoupling of the ligand, without restraints", "free_energy"),
])
def test_a_method_s_details_make_it_another_only_where_they_say_so(details, method):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    assert plan["method"] == method


def test_a_ready_study_run_until_determined_runs_with_differences():
    claims = [{"status": "stated", "quantity": "rmsd", "analysis": "rmsd", "value": 0.12,
               "error": 0.01, "error_kind": "standard_error", "unit": "nm"}]
    fields = {"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
              "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P")}
    plan = plan_study({"id": "S1", "label": "x", "fields": fields, "claims": claims}, {},
                      until_determined=True)
    assert plan["config"]["simulation"]["stop_when"]["measures"][0]["analysis"] == "rmsd"
    assert plan["state"] == "with_differences"


def test_words_in_a_label_cannot_add_settings_to_the_file():
    from fastmdxplora.paper.studies import config_text

    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0)})
    plan["label"] = ("wild type\nanalysis:\n  injected: true\nsweep:\n"
                     "  simulation.temperature_K: [500] x: 1")
    plan["config"]["paper"]["read_by"] = "An AI app\r\nreport: {title: no}"
    text = config_text(plan)
    assert yaml.safe_load(text) == plan["config"]
    assert "sweep" not in yaml.safe_load(text)


# -- the paper's file --------------------------------------------------------
_ENTITY = ('<!DOCTYPE article [<!ENTITY t "injected 350 K">]>'
           "<article><body><sec><title>Methods</title><p>&t;</p></sec></body></article>")


@pytest.mark.parametrize("data", [
    _ENTITY.encode(),
    b"<!--" + b"x" * 30000 + b"-->" + _ENTITY.encode(),
    ('<?xml version="1.0" encoding="UTF-16"?>' + _ENTITY).encode("utf-16"),
])
def test_an_xml_that_defines_entities_is_refused_however_it_hides_them(data):
    with pytest.raises(PaperRefused, match="its own entities"):
        jats_parts(data, "paper")


def test_a_word_file_that_defines_entities_is_refused(tmp_path):
    from fastmdxplora.paper.text import read_paper

    path = tmp_path / "paper.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", _ENTITY.replace("article", "document"))
    with pytest.raises(PaperRefused, match="its own entities"):
        read_paper(path)


def test_a_share_without_its_unit_above_one_is_a_percentage(tmp_path):
    from fastmdxplora.paper.reproduction import compare_with_paper

    root = tmp_path / "run"
    (root / "analysis" / "ss").mkdir(parents=True)
    (root / "resolved_config.yml").write_text(yaml.safe_dump({"system": "1UBQ", "paper": {
        "study": "S1", "claims": [{"quantity": "secondary_structure", "analysis": "ss",
                                   "what": "helix", "value": 45.0, "error": 3.0, "unit": "",
                                   "error_kind": "standard_error"}]}}), encoding="utf-8")
    (root / "analysis" / "ss" / "options.json").write_text(json.dumps({
        "analysis": "ss", "findings": {"helix_fraction": {
            "mean": 0.44, "standard_error": 0.01, "effective_samples": 60, "unit": "",
            "n_frames": 500, "discard": 0}}}), encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(
        json.dumps({"results": {"ss": {"status": "ok"}}}), encoding="utf-8")
    row = compare_with_paper(root)["rows"][0]
    assert row["paper_value_here"] == pytest.approx(0.45)
    assert row["verdict"] == "agrees"


def test_a_count_without_its_unit_is_compared_as_it_is(tmp_path):
    from fastmdxplora.paper.reproduction import compare_with_paper

    root = tmp_path / "run"
    (root / "analysis" / "hbonds").mkdir(parents=True)
    (root / "resolved_config.yml").write_text(yaml.safe_dump({"system": "1UBQ", "paper": {
        "study": "S1", "claims": [{"quantity": "hydrogen_bonds", "analysis": "hbonds",
                                   "value": 2.3, "error": 0.1, "unit": "",
                                   "error_kind": "standard_error"}]}}), encoding="utf-8")
    (root / "analysis" / "hbonds" / "options.json").write_text(json.dumps({
        "analysis": "hbonds", "findings": {"mean": {
            "mean": 2.3, "standard_error": 0.05, "effective_samples": 60, "unit": "",
            "n_frames": 500, "discard": 0}}}), encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(
        json.dumps({"results": {"hbonds": {"status": "ok"}}}), encoding="utf-8")
    row = compare_with_paper(root)["rows"][0]
    assert row["paper_value_here"] == pytest.approx(2.3)
    assert row["verdict"] == "agrees"


def test_supporting_information_that_failed_to_come_is_fetched_again(tmp_path, monkeypatch):
    from fastmdxplora.paper import fetch

    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as made:
        for number in range(1, 12):
            made.writestr(f"Table_S{number}.txt", f"Supporting table {number}: 100 ns each.")
    asked = {"si": 0}

    def get(url, **_kwargs):
        if "search" in url:
            return json.dumps({"resultList": {"result": [{
                "pmcid": "PMC1234567", "doi": "10.9999/x", "title": "T",
                "isOpenAccess": "Y"}]}}).encode()
        if "fullTextXML" in url:
            return jats()
        asked["si"] += 1
        if asked["si"] == 1:
            raise PaperRefused("unreachable", code="environment.service.unreachable")
        return archive.getvalue()

    monkeypatch.setattr(fetch, "_get", get)
    first = fetch.fetch_paper("10.9999/x")
    assert not any(part.kind == "si" for part in first.parts)
    second = fetch.fetch_paper("10.9999/x")
    assert asked["si"] == 2
    third = fetch.fetch_paper("10.9999/x")
    assert asked["si"] == 2  # kept now, with its supporting information
    assert third.sha256() == second.sha256()
    labels = [part.text for part in third.parts if part.kind == "si"]
    assert labels[0].startswith("Supporting table 1:") and labels[-1].startswith(
        "Supporting table 11:")
