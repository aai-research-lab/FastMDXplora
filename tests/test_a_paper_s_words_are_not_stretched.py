"""What a paper does not say is not made to say it.

A quote that starts inside a number, a count that is some other number, a
flag its words deny, an error whose kind the paper does not give: none is
used as the paper's. A reply cut off part way is refused, not read as one
that found nothing. A study is written with every setting it always has
said, the paper's or this software's; an AMBER force field is not switched
and a CHARMM one is switched as it is developed; ions are no ligand; a
membrane is read however it is worded; plain MD stays plain whatever its
details mention. The file written is the config and nothing more, whatever
words the paper or an AI app gives its label. A reading is counted against
the truth with its wrong values and its extra studies counted.
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


def test_a_protocol_s_settings_that_do_not_fit_one_answer_are_asked_in_two_parts(
        tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path))
    paper = PaperText(parts=jats_parts(jats(), "paper")[0])
    model = scripted()
    answer = model.__call__
    asked = []

    def cut_when_whole(prompt):
        reply = answer(prompt)
        if "settings of one of its MD protocols" in prompt:
            whole = '- "system"' in prompt and '- "method_details"' in prompt
            asked.append(whole)
            assert "give fewer items" not in prompt  # nothing may be left out unsaid
            if whole:
                return reply[: len(reply) // 2]
        return reply

    said = []
    reading = read_studies(paper, cut_when_whole, model="test/model", said=said.append)
    assert asked == [True, False, False]
    assert any("two parts" in line for line in said)
    whole = read_studies(paper, scripted(), model="other/model", use_kept=False)
    fields = {k: v.get("status") for k, v in reading["studies"][0]["fields"].items()}
    assert fields == {k: v.get("status") for k, v in whole["studies"][0]["fields"].items()}
    assert "stated" in fields.values()


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
    ('a POPC bilayer', 'ready', 'POPC'),
    ('POPC, 144 lipids (72 per leaflet)', 'ready', 'POPC'),
    ('POPC with 5% PIP2', 'cannot_run', None),
    ('POPC:POPG 3:1', 'cannot_run', None),
    ('POPC (100%)', 'ready', 'POPC'),
    ('POPC bilayer, 50% hydrated', 'needs_you', 'POPC'),
    ('POPC with 30% cholesterol', 'cannot_run', None),
    ('POPC bilayer built with CHARMM-GUI', 'ready', 'POPC'),
    ('POPE bilayer, 256 lipids, TIP3P water', 'ready', 'POPE'),
    ('POPC with PIP2 and SM', 'cannot_run', None),
    ('POPC bilayer, 150 mM NaCl with CL- ions', 'ready', 'POPC'),
    ('POPC with Na+ and CL- counterions', 'ready', 'POPC'),
    ('POPC (PC headgroups)', 'ready', 'POPC'),
    ('SM-free POPC bilayer', 'needs_you', 'POPC'),
    ('POPE bilayer with no PIP2', 'needs_you', 'POPE'),
    ('POPC, cardiolipin-free', 'needs_you', 'POPC'),
    ('POPC with PE lipids', 'cannot_run', None),
    ('POPC with 30% CL', 'cannot_run', None),
    ('POPC with CL-containing domains', 'cannot_run', None),
    ('POPC bilayer without cholesterol', 'needs_you', 'POPC'),
    ('cholesterol-free POPC bilayer', 'needs_you', 'POPC'),
    ('95% POPC + 5% POP(3)P, symmetric, 418:22 per leaflet', 'cannot_run', None),
    ('POPC with PS- lipids', 'cannot_run', None),
    ('POPC with anionic PS- lipids', 'cannot_run', None),
    ('POPC with 10% PS-', 'cannot_run', None),
    ('POPC with PS− (anionic)', 'cannot_run', None),
    ('POPC with 10% PS⁻', 'cannot_run', None),
    ('POPC with anionic PIP2- lipids', 'cannot_run', None),
    ('POPC with 10% PIP2−', 'cannot_run', None),
    ('POPC containing PIP2-, Na+ ions', 'cannot_run', None),
    ('POPC with GM1-', 'cannot_run', None),
    ('POPC with CHL1-', 'cannot_run', None),
    ('POPC/PG- (3/1)', 'cannot_run', None),
    ('POPC with PG− 30%', 'cannot_run', None),
    ('POPC with 20% PS + Ca2+ ions', 'cannot_run', None),
    ('POPC with 20% PS - Ca2+ bound', 'cannot_run', None),
    ('POPC with PIP2 - 5 mol%', 'cannot_run', None),
    ('POPC with SM - 30%', 'cannot_run', None),
    ('POPC with sphingomyelin - 30 mol%', 'cannot_run', None),
    ('POPC and SM less than 10%', 'cannot_run', None),
    ('POPC with SM less abundant than in vivo', 'cannot_run', None),
    ('POPC with PIP2 depleted from the outer leaflet only', 'cannot_run', None),
    ('POPC with PIP2 less than 5 mol%', 'cannot_run', None),
    ('POPC bilayer with PIP2 free of Mg', 'cannot_run', None),
    ('DOPC with 30% egg PC', 'cannot_run', None),
    ('POPC with 30% brain PC', 'cannot_run', None),
    ('POPC with 20% CL- and K+ counterions', 'cannot_run', None),
    ('POPC bilayer, 20 mol% CL-, Na+ to neutralize', 'cannot_run', None),
    ('POPC/CL- with Na+', 'cannot_run', None),
    ('POPC with 10% CL-, Na+ counterions', 'cannot_run', None),
    ('POPC with phosphatidylserine', 'cannot_run', None),
    ('POPC with PtdIns(4,5)P2', 'cannot_run', None),
    ('POPC with ergosterol', 'cannot_run', None),
    ('POPC with no cholesterol but 10% ergosterol', 'cannot_run', None),
    ('DOPC with 30% egg phosphatidylcholine', 'cannot_run', None),
    ('1-palmitoyl-2-oleoyl-phosphatidylcholine (POPC) bilayer', 'ready', 'POPC'),
    ('POPC (palmitoyl-oleoyl-phosphatidylcholine) bilayer', 'ready', 'POPC'),
    ('POPC (with 20% phosphatidylserine)', 'cannot_run', None),
    ('DOPC (plus 10% phosphatidylethanolamine)', 'cannot_run', None),
    ('POPC (including phosphatidylinositol)', 'cannot_run', None),
    ('phosphatidylserine (POPC bilayer)', 'cannot_run', None),
    ('POPC with phosphatidic acid', 'cannot_run', None),
    ('POPC with 10 percent CL- counterion-neutralised', 'cannot_run', None),
    ('POPC with 10% SOPC', 'cannot_run', None),
    ('POPC with 10% SDPC', 'cannot_run', None),
    ('POPC with 10% OPPC', 'cannot_run', None),
    ('POPC with POPI', 'cannot_run', None),
    ('POPC with 10% DOTAP', 'cannot_run', None),
    ('POPC with 10% lysoPC', 'cannot_run', None),
    ('POPC with 10% plasmalogen', 'cannot_run', None),
    ('POPC with 30% sterols', 'cannot_run', None),
    ('POPC with sitosterol', 'cannot_run', None),
    ('POPC with diacylglycerol', 'cannot_run', None),
    ('POPC with glycolipids', 'cannot_run', None),
    ('POPC with lipopolysaccharide', 'cannot_run', None),
    ('POPC with hopanoids', 'cannot_run', None),
    ('POPC and E. coli polar lipid extract', 'cannot_run', None),
    ('POPC with brain lipid extract', 'cannot_run', None),
    ('POPC bilayer, 0.15 M NaCl (Na+, CL-)', 'ready', 'POPC'),
    ('POPC bilayer with Na+/CL- ions', 'ready', 'POPC'),
    ('POPC bilayer, NaCl/CL- ions', 'ready', 'POPC'),
    ('POPC bilayer, 0.15 M NaCl with 20 CL- ions', 'ready', 'POPC'),
    ('POPE bilayer (phosphatidylethanolamine)', 'ready', 'POPE'),
    ('POPC: palmitoyl-oleoyl-phosphatidylcholine', 'ready', 'POPC'),
    ('POPC, a phosphatidylcholine', 'ready', 'POPC'),
    ('a phosphatidylcholine bilayer of POPC', 'cannot_run', None),
    ('DPPC bilayer, phosphatidylcholine headgroups', 'ready', 'DPPC'),
    ('POPC and dipalmitoylphosphatidylcholine bilayer', 'cannot_run', None),
    ('POPC with dioleoylphosphatidylcholine', 'cannot_run', None),
    ('POPC containing 25% dipalmitoyl phosphatidylcholine', 'cannot_run', None),
    ('POPC with 20% 1,2-dipalmitoyl-sn-glycero-3-phosphatidylcholine', 'cannot_run', None),
    ('DOPC bilayer with saturated phosphatidylcholine domains', 'cannot_run', None),
    ('POPC and dioleoyl phosphatidylcholine', 'cannot_run', None),
    ('POPC with 30 mol% fully saturated phosphatidylcholine', 'cannot_run', None),
    ('POPC and phosphatidylcholine from egg yolk', 'cannot_run', None),
    ('POPE with phosphatidylethanolamine from E. coli', 'cannot_run', None),
    ('POPC bilayer with MOPS buffer', 'ready', 'POPC'),
    ('POPC (phosphatidylcholine)', 'ready', 'POPC'),
])

def test_a_membrane_is_read_however_it_is_worded(membrane, state, lipid):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "membrane": _stated(membrane)})
    assert plan["state"] == state
    if lipid:
        assert plan["config"]["setup"]["membrane"] == lipid


@pytest.mark.parametrize("details, method", [
    ('Unbiased MD of the folded protein at 1 bar', 'plain'),
    ('Plain MD run on AMD GPUs', 'plain'),
    ('Two 6 us runs, analysed with a Markov state model (MSM)', 'plain'),
    ('No replica exchange was used', 'confirm'),
    ('Bias-exchange metadynamics with four replicas', 'replica_exchange'),
    ('Generalized Born implicit solvent model (GB-OBC II)', 'implicit_solvent'),
    ('Gaussian accelerated MD (GaMD)', 'accelerated'),
    ('Conventional MD, compared with REMD from ref 12', 'confirm'),
    ('Starting structures were taken from our previous REMD simulations', 'confirm'),
    ('REST2 (ref 14) with 16 replicas', 'replica_exchange'),
    ('GaMD boost potential applied, dual-boost, as in ref 30', 'accelerated'),
    ('Alchemical free energy perturbation, 20 lambda windows (protocol from ref 5)', 'free_energy'),
    ('Well-tempered bias-exchange metadynamics, following our previous protocol', 'confirm'),
    ('Simulated tempering over a temperature range (range not stated in text)', 'confirm'),
    ('GaMD production was started from the final frame of the previous conventional MD run', 'confirm'),
    ('The generalized Born implicit solvent model with parameters taken from reference 25', 'confirm'),
    ('Solvation was the GB-OBC II model, which has not been widely used for IDPs', 'confirm'),
    ('Replica exchange with 32 replicas, as reported in our earlier work', 'confirm'),
    ('Alchemical decoupling of the ligand, without restraints', 'confirm'),
    ('Started from the final frame of a 500 ns REMD run', 'confirm'),
    ('Run as a control for the FEP calculations', 'confirm'),
    ('Unbiased, in contrast to the GaMD runs', 'confirm'),
    ('Starting from the crystal structure, REMD was run with 24 replicas', 'confirm'),
    ('Backbone atoms restrained; the rest of the protein was free', 'plain'),
    ('The thermostat was decoupled from the barostat', 'confirm'),
    ('MARTINI was not used here', 'confirm'),
    ('REMD was not converged after 100 ns', 'confirm'),
    ('REST2 with 16 replicas', 'replica_exchange'),
    ('Rest2 with 12 replicas', 'replica_exchange'),
    ('gREST with 8 replicas', 'replica_exchange'),
    ('Temperature REMD¹² was performed with 32 replicas', 'replica_exchange'),
    ('Starting from the X-ray structure we performed REMD with 24 replicas', 'confirm'),
    ('Starting from the crystal structure REMD was run with 32 replicas.', 'confirm'),
    ('Simulations started from the crystal structure using REMD with 32 replicas', 'confirm'),
    ('Initial structures were taken from the PDB and simulated with REMD', 'confirm'),
    ('Initial structures were taken from the PDB and REMD simulations were run with 32 replicas.', 'confirm'),
    ('Systems were initialized from the equilibrated structure and simulated with GaMD', 'accelerated'),
    ('Coordinates obtained from PDB 2KOD were simulated using REST2 with 16 replicas', 'replica_exchange'),
    ('Derived from the all-atom model using MARTINI', 'confirm'),
    ('Each replica started from the same structure in REMD', 'confirm'),
    ('Temperatures were selected from 300 to 450 K for the REMD with 32 replicas', 'replica_exchange'),
    ('Lambda values were chosen from 0 to 1 in 20 windows for FEP', 'free_energy'),
    ('Starting from the docked pose we performed GaMD for 500 ns.', 'confirm'),
    ('Systems derived from the cryo-EM model were subjected to GaMD.', 'confirm'),
    ('Simulations were seeded from REMD-derived structures and then extended with REMD for 200 ns.', 'confirm'),
    ('Seeded from REMD-derived structures', 'confirm'),
    ('REMD was not run for longer than 100 ns per replica', 'confirm'),
    ('REMD was not performed in NPT but in NVT', 'confirm'),
    ('GaMD was not applied to the ligand, only to the protein dihedrals', 'confirm'),
    ('QM/MM was not used for the protein, only for the active site', 'confirm'),
    ('MARTINI was not used for the protein but for the lipids', 'confirm'),
    ('REST2 was not applied to water; 16 replicas', 'confirm'),
    ('GaMD was not run to convergence but 3 x 500 ns were collected.', 'confirm'),
    ('replica exchange MD in which 24 replicas were not used for analysis.', 'confirm'),
    ('GaMD, whose boost was not applied to the solvent, was run for 1 us.', 'confirm'),
    ('FEP calculations were not performed for the apo state but for the holo state with 20 lambda windows.', 'confirm'),
    ('MMVT milestoning was not performed with SEEKR2 but with our own code.', 'confirm'),
    ('REMD simulations were not performed here.', 'confirm'),
    ('In contrast to conventional MD, GaMD was used here', 'confirm'),
    ('To control for force field bias, REMD with two force fields was run', 'confirm'),
    ('As opposed to earlier studies, REMD with 32 replicas was used here', 'confirm'),
    ('As a control for convergence, REMD was repeated with 48 replicas', 'confirm'),
    ('Contrasted with experiment, REMD gave a melting temperature of 330 K', 'confirm'),
    ('Unlike conventional MD, REMD was used with 32 replicas.', 'confirm'),
    ('Compared with cMD, GaMD was run for 500 ns.', 'confirm'),
    ('Rather than cMD, GaMD was run for 500 ns.', 'confirm'),
    ('Instead of conventional MD, REMD with 32 replicas was performed.', 'confirm'),
    ('Without explicit solvent, the GBSA model was used.', 'confirm'),
    ('Without restraints, REMD was run.', 'confirm'),
    ('The ligand was decoupled from the solvent over 20 lambda windows', 'free_energy'),
    ('Absolute binding free energies with the double-decoupling method', 'free_energy'),
    ('Ligand electrostatics were decoupled, then Lennard-Jones terms, over 20 windows', 'free_energy'),
    ('The van der Waals interactions were decoupled using 20 lambda windows', 'free_energy'),
    ('Decoupling simulations of the ligand in water and in the complex', 'free_energy'),
    ('The ligand was decoupled over 20 lambda windows', 'free_energy'),
    ('All 32 replicas were started from the equilibrated structures in the REMD simulations', 'confirm'),
    ('Each replica was initialized from the equilibrated structure in the REST2 simulations', 'replica_exchange'),
    ('Windows were started from the equilibrated structure of the FEP calculations', 'confirm'),
    ('The 16 replicas were started from the equilibrated structure in REMD simulations at 300 to 400 K.', 'confirm'),
    ('Not REMD but REST2 with 16 replicas was used', 'confirm'),
    ('not REMD but REST2 with 16 replicas', 'confirm'),
    ('Restraints were not applied during the REMD production', 'confirm'),
    ('Hydrogen mass repartitioning was not used in GaMD', 'confirm'),
    ('Solvent was not scaled in REST2; only the protein was in the hot region', 'confirm'),
    ('Without position restraints REMD with 32 replicas was run', 'confirm'),
    ('We performed no equilibration before REMD with 32 replicas', 'confirm'),
    ('No restraints in REMD; 32 replicas from 300 to 450 K', 'confirm'),
    ('No fewer than 32 REMD replicas were used', 'confirm'),
    ('Instead of aMD GaMD was used for 3 x 500 ns.', 'confirm'),
    ('Rather than standard aMD GaMD was used.', 'confirm'),
    ('Instead of REMD REST2 was used.', 'confirm'),
    ('We did not use cMD but GaMD for 3 x 500 ns.', 'confirm'),
    ('Not cMD but GaMD was used for 3 x 500 ns.', 'confirm'),
    ('Without position restraints REMD simulations were carried out with 32 replicas.', 'confirm'),
    ('No restraints REMD was performed for 200 ns per replica.', 'confirm'),
    ('Production (no restraints) REMD with 32 replicas.', 'confirm'),
    ('Without explicit water GBn2 was used.', 'confirm'),
    ('Without explicit solvent GB-OBC was used.', 'confirm'),
    ('Following the protocol of our previous study [12] REMD was performed with 32 replicas.', 'confirm'),
    ('As described in our earlier work REMD was run with 48 replicas.', 'confirm'),
    ('As in the published protocol REMD was used.', 'confirm'),
    ('A total of more than 10 us of REMD sampling was collected.', 'replica_exchange'),
    ('Unlike explicit solvent the GBn2 model was used to speed sampling.', 'confirm'),
    ('We used more than 40 REMD replicas spanning 300 to 500 K', 'replica_exchange'),
    ('Using the settings of the published REST2 protocol, 16 replicas were run', 'confirm'),
    ('Parameters were taken from the published REST2 protocol with 16 replicas', 'confirm'),
    ('Compared with experiment REMD gave melting temperatures', 'confirm'),
    ('REMD12 with 16 replicas', 'replica_exchange'),
    ('The ligand was decoupled from the bath of solvent over 20 windows.', 'free_energy'),
    ("The ligand's interactions were decoupled from the bath of water molecules using 20 λ values.", 'free_energy'),
    ('Rather than FEP, TI with 12 λ windows.', 'confirm'),
    ('The ligand was annihilated using 21 λ windows', 'free_energy'),
    ('Absolute binding free energies by double annihilation', 'free_energy'),
    ('REUS with 24 windows', 'replica_exchange'),
    ('Coarse-grained with Martini 3', 'coarse_grained'),
    ('Implicit solvent was not used.', 'confirm'),
    ('A coarse-grained model was not used.', 'confirm'),
    ('Seeds came from no REMD at all; plain MD', 'confirm'),
    ('Compared with the conventional GaMD of ref 4', 'confirm'),
    ('Structures were taken from our previous REMD simulations', 'confirm'),
    ('As in our earlier REMD study', 'confirm'),
    ('Structures came from our earlier REMD study', 'confirm'),
    ('Exchange parameters were taken from our previous REMD simulations, with 32 replicas spanning 300-450 K', 'confirm'),
    ('The temperature ladder was taken from our previous REMD simulations', 'confirm'),
    ('Replica temperatures were obtained from the previous REMD runs (32 replicas, 300-450 K)', 'confirm'),
    ('The boost parameters were obtained from the previous GaMD simulations', 'confirm'),
    ('Lambda schedule was taken from the previous FEP calculations', 'confirm'),
    ('Extended from our previous REMD simulations by 100 ns per replica', 'confirm'),
    ('Continued from the previous REMD run for another 200 ns', 'confirm'),
    ('Compared with the REMD run at 300 K, the 310 K replica was more compact', 'confirm'),
    ('As in our previous REMD work, 32 replicas spanning 300-450 K were used.', 'confirm'),
    ('Using the protocol of our previous REMD study, 32 replicas were run.', 'confirm'),
    ('The same settings as in the published REMD study [12] were used: 48 replicas.', 'confirm'),
    ('Same as in the reported REMD calculations of ref 3, with 48 replicas.', 'confirm'),
    ('From the previous REMD runs we kept the temperature ladder of 32 replicas.', 'confirm'),
    ('In the previous REMD simulations (ref 12) only 16 replicas were used, so here 32 replicas spanning 300-450 K were used.', 'confirm'),
    ('Without any REMD-specific restraints, 32 replicas were exchanged', 'confirm'),
    ('Started from the final frame of the 500 ns REMD run, exchanges continued for 200 ns', 'confirm'),
    ('Explicit solvent was not used; the GB model was used.', 'implicit_solvent'),
    ('No REMD or GaMD was used', 'confirm'),
    ('Neither REMD nor GaMD was used', 'confirm'),
    ('Rather than REMD, GaMD was used.', 'confirm'),
    ('No REMD, GaMD was used instead.', 'confirm'),
    ('Instead of conventional REMD, GaMD was applied for 1 us.', 'confirm'),
    ('Rather than GaMD, REMD with 32 replicas was used.', 'confirm'),
    ('Not TI, FEP was used.', 'confirm'),
    ('No QM/MM, Martini was used.', 'confirm'),
    ('Instead of conventional REMD, REST2 was used to reduce the number of replicas.', 'confirm'),
    ('Instead of REMD, REST2 was used with 16 replicas.', 'confirm'),
    ('Instead of T-REMD and H-REMD, REST2 was used.', 'confirm'),
    ('Instead of REMD or GaMD, REST2 was used.', 'confirm'),
    ('No REMD, REST2 instead.', 'confirm'),
    ('Instead of FEP, thermodynamic integration was used.', 'confirm'),
    ('Instead of MARTINI, coarse-grained SIRAH model was used.', 'confirm'),
    ('We did not perform REMD.', 'confirm'),
    ('The simulations did not use REMD.', 'confirm'),
    ('MD was performed without the use of REMD.', 'confirm'),
    ('No T\u2011REMD was used.', 'confirm'),
    ('Replica\u2010exchange MD with 32 replicas was used.', 'replica_exchange'),
    ('REMDs with 32 replicas were used.', 'replica_exchange'),
    ('GaMDs were run.', 'accelerated'),
    ('Two FEPs per mutation.', 'free_energy'),
    ('QM-MM simulations used ORCA.', 'qm_mm'),
    ('A QM\u2013MM scheme was applied.', 'qm_mm'),
    ('LiGaMD simulations of the complex for 5 x 1 us', 'accelerated'),
    ('REST3 with 16 replicas', 'replica_exchange'),
    ('Relative binding free energies (RBFE) were computed', 'free_energy'),
    ('Simulations used igb=8 with mbondi3 radii', 'implicit_solvent'),
    ('GBNeck2 implicit model', 'implicit_solvent'),
    ('OBC2 model with 0.15 M salt', 'implicit_solvent'),
    ('GBSW with CHARMM36m', 'implicit_solvent'),
    ('DFTB3 for the active site', 'qm_mm'),
    ('SCC-DFTB3/3OB for the active site', 'qm_mm'),
    ('Martini22 force field', 'coarse_grained'),
    ('SIRAH force field', 'coarse_grained'),
    ('WESTPA with 100 walkers', 'milestoning'),
    ('PT-MetaD with 8 replicas', 'replica_exchange'),
    ('Started from the final frame of a 500\u2011ns REMD run.', 'confirm'),
    ('Ten replicas were started from the final frame of a 500 ns REMD run.', 'confirm'),
    ('The MM/GBSA binding energy was computed from the trajectories.', 'plain'),
    ('The trajectory was coarse-grained to Ca atoms for analysis.', 'confirm'),
    ('Partial charges were fitted to quantum mechanical calculations.', 'confirm'),
    ('The trajectory occupied 50 GB model files.', 'confirm'),
    ('Each 5 GB solvated system file was stored.', 'confirm'),
    ('The trajectory occupied 50 GB of storage.', 'plain'),
])

def test_a_method_s_details_make_it_another_only_where_they_say_so(details, method):
    """Each wording is the method named (refused), plain MD, or plain MD
    that waits for the person to confirm it ("confirm"): never plain MD that
    runs where the words may say otherwise."""
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    confirm = [c for c in _choices(plan, "method_details") if c["label"] == "needs_you"]
    said = plan["method"] if plan["method"] != "plain" else ("confirm" if confirm else "plain")
    assert said == method


def test_another_method_named_only_as_where_a_study_started_waits_for_you():
    details = "Started from the final frame of a 500 ns REMD run"
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    assert plan["state"] == "needs_you"
    said = _choices(plan, "method_details")
    assert said and said[0]["label"] == "needs_you" and "REMD" in said[0]["why"]
    assert "paper" in plan["config"] and plan["config"]["paper"]["needs"]
    control = "Run as a control for the FEP calculations"
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "method": _stated("plain"), "method_details": _stated(control, control)})
    assert plan["state"] == "needs_you"
    assert "FEP" in _choices(plan, "method_details")[0]["why"]


@pytest.mark.parametrize("details", [
    "MARTINI was not used here.",
    "No REMD was performed.",
    "No REMD and no REST2.",
    "Run as a control for the FEP calculations",
])
def test_a_method_named_with_qualifying_words_is_put_to_the_person(details):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    assert plan["method"] == "plain" and plan["state"] == "needs_you"
    said = _choices(plan, "method_details")[0]
    assert said["label"] == "needs_you" and details.rstrip(".") in said["why"]
    assert plan["config"]["paper"]["needs"]


@pytest.mark.parametrize("fields", ["null", "[]", '"none"'])
def test_settings_given_as_no_object_are_not_read_as_none_stated(fields):
    with pytest.raises(PaperRefused):
        _json_from('{"fields": %s}' % fields, "fields")


@pytest.mark.parametrize("wanted", ["studies", "claims"])
def test_a_list_answered_as_null_is_an_empty_list(wanted):
    assert _json_from('{"%s": null, "more": false}' % wanted, wanted)[wanted] is None
    with pytest.raises(PaperRefused):
        _json_from('{"%s": {"S1": 1}}' % wanted, wanted)


@pytest.mark.parametrize("details, named", [
    ("Simulated tempering over 300-400 K", "simulated tempering"),
    ("Bias-exchange metadynamics with four replicas", "bias-exchange metadynamics"),
    ("REST2 with 16 replicas", "solute tempering"),
    ("Temperature REMD, 32 replicas from 300 to 450 K", "The study is replica exchange,"),
    ("Temperature REMD and REST2 were both run", "The study is replica exchange,"),
    ("T-REMD and H-REMD were both run with 32 replicas", "The study is replica exchange,"),
    ("Temperature REMD (32 replicas); a REST2 test run was discarded",
     "The study is replica exchange,"),
    ("temperature REMD, the REST of the setup as in ref 3", "The study is replica exchange,"),
    ("Hamiltonian replica exchange with 8 replicas", "Hamiltonian replica exchange"),
])
def test_a_kind_of_replica_exchange_is_named_as_the_paper_names_it(details, named):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    assert plan["state"] == "cannot_run"
    assert named in _choices(plan, "method")[0]["why"]


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


def test_two_studies_of_one_id_are_two_files():
    from fastmdxplora.gui.paper_view import configs_download

    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0)})
    body, _name, _media = configs_download({"configs": [
        {"id": "S1", "config": plan["config"]}, {"id": "S1", "config": plan["config"]}]})
    assert sorted(zipfile.ZipFile(io.BytesIO(body)).namelist()) == ["paper-s1-2.yml",
                                                                    "paper-s1.yml"]


def test_a_config_that_cannot_be_written_is_refused_in_words(tmp_path, monkeypatch):
    import urllib.error
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.paper import studies

    def refuse(_plan):
        raise PaperRefused("did not read back", code="environment.paper.unreadable")

    monkeypatch.setattr(studies, "config_text", refuse)
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0)})
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        request = urllib.request.Request(
            session.url + "/api/paper/download", method="POST",
            data=json.dumps({"configs": [{"id": "S1", "config": plan["config"]}]}).encode(),
            headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as answered:
            urllib.request.urlopen(request, timeout=60)
        said = json.loads(answered.value.read())
    finally:
        session.server.shutdown()
    assert said == {"ok": False, "error": "did not read back"}


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


# -- counting a reading --------------------------------------------------------
def _truth():
    return {"key": "x", "studies": [{"id": "S1/S1", "label": "wild type ubiquitin",
                                     "contested": [], "claims": [],
                                     "fields": {"temperature": {"status": "stated", "value": 300},
                                                "timestep": {"status": "stated", "value": 2},
                                                "replicas": {"status": "in_si"}}}]}


def test_a_reading_with_every_value_wrong_is_counted_wrong():
    from fastmdxplora.validation.paper_reading import score

    reading = {"studies": [{"id": "S1", "label": "wild type ubiquitin", "claims": [],
                            "fields": {"temperature": _stated(350), "timestep": _stated(4)}}]}
    counted = score(_truth(), reading)
    assert counted["studies_matched"] == 1
    assert counted["fields"]["wrong"] == 2


def test_a_study_the_truth_does_not_have_counts_its_values_as_invented():
    from fastmdxplora.validation.paper_reading import score

    reading = {"studies": [{"id": "S1", "label": "wild type ubiquitin", "claims": [],
                            "fields": {"temperature": _stated(300), "timestep": _stated(2)}},
                           {"id": "S2", "label": "something made up", "claims": [],
                            "fields": {"temperature": _stated(400), "pressure": _stated(1.0)}}]}
    counted = score(_truth(), reading)
    assert counted["fields"]["correct"] == 2
    assert counted["fields"]["invented"] == 2


def test_a_study_one_reader_found_is_neither_counted_right_nor_invented():
    from fastmdxplora.validation.paper_reading import score

    truth = dict(_truth(), only_b=["the L50A mutant of ubiquitin"])
    reading = {"studies": [{"id": "S2", "label": "ubiquitin L50A mutant", "claims": [],
                            "fields": {"temperature": _stated(300)}}]}
    counted = score(truth, reading)
    assert counted["studies_one_reader_found"] == 1
    assert counted["fields"]["invented"] == 0


def test_a_value_the_truth_places_in_the_supporting_information_is_not_counted():
    from fastmdxplora.validation.paper_reading import score

    reading = {"studies": [{"id": "S1", "label": "wild type ubiquitin", "claims": [],
                            "fields": {"replicas": _stated(3)}}]}
    counted = score(_truth(), reading)
    assert counted["fields"]["elsewhere"] == 1 and counted["fields"]["invented"] == 0


def test_the_registered_claims_are_said_met_or_not():
    from fastmdxplora.validation.paper_reading import claims_met

    results = [{"key": "a", "studies_truth": 10, "studies_read": 10, "studies_matched": 9,
                "fields": {"correct": 90, "wrong": 1, "missed": 20, "invented": 1}},
               {"key": "buch_2011", "refused_by_doi": "environment.paper.not_open",
                "studies_truth": 1, "studies_read": 1, "studies_matched": 1,
                "fields": {"correct": 5, "wrong": 0, "missed": 0, "invented": 0}}]
    claims = claims_met(results)
    assert claims["values_used_right"]["met"] is False  # 95/97 is under 0.98
    assert claims["stated_values_found"]["met"] is True
    assert claims["studies_found"]["value"] == pytest.approx(10 / 11, abs=1e-4)
    assert claims["not_open_refused"]["met"] is True


def test_a_paper_refused_counts_its_studies_as_not_found():
    from fastmdxplora.validation.paper_reading import claims_met

    results = [{"key": "a", "studies_truth": 4, "studies_read": 4, "studies_matched": 4,
                "fields": {"correct": 10}},
               {"key": "b", "refused": "unreachable", "code": "environment.service.unreachable",
                "studies_truth": 4}]
    claims = claims_met(results)
    assert claims["studies_found"]["value"] == pytest.approx(0.5)
    assert claims["refused"] == ["b"]


def test_a_file_for_a_paper_not_registered_is_refused(capsys):
    from fastmdxplora.validation.paper_reading import main

    assert main(["--file", "nobody_2020=x.pdf"]) == 2
    assert "KEY=PATH" in capsys.readouterr().err


# -- round two of the second review: never plain where the words may say otherwise --
@pytest.mark.parametrize("details", [
    'We did not run REMD for longer than 100 ns per replica.',
    'We did not use REMD at temperatures above 450 K.',
    'We did not perform REMD with explicit counterions; 32 replicas in 150 mM NaCl.',
    'We did not apply REST2 to the ligand, only to the binding-site residues (16 replicas).',
    'We do not run REMD with fewer than 32 replicas.',
    'Without the use of REMD, sampling would not have converged.',
    'Such sampling cannot be achieved without the use of REST2 (16 replicas).',
    'No REMD exchanges were attempted during the first 10 ns of equilibration; 32 replicas then exchanged every 2 ps.',
    'Without REST2 scaling of the solvent, 16 replicas were run with only the protein scaled.',
    'No GaMD boost was applied during the first 2 ns; the boost potential was then applied.',
    'Sampling of the folded state would not have been possible without REMD; 32 replicas were used.',
    'Convergence is impossible without REMD, so 32 replicas spanning 300-450 K were run.',
    'This would not be feasible without GaMD.',
    'Position restraints during REMD were not applied.',
    'Position restraints in the REMD were not applied.',
    'Backbone restraints for REMD were not needed.',
    'Hydrogen mass repartitioning in GaMD was not used.',
    'Restraints on the ligand in FEP were not applied.',
    'Ions in the GBSA model were not used.',
    'Salt in the implicit solvent model was not used.',
    'Absolute binding free energy calculations were run with Yank.',
    'Relative binding free energies were computed with OpenFE.',
    'replica\u2013exchange MD with 32 replicas',
    'MREMD with 64 replicas',
    'REX simulations of the peptide',
    'RE-MD of the peptide',
    'PT-WTE with 8 replicas',
    'BE-META with 6 replicas',
    'Expanded ensemble simulations over 20 temperatures',
    'AMD simulations with dual boost',
    'dual-boost accelerated simulations',
    'WExplore sampling',
    'implicit water model',
    'EEF1/IMM1 implicit membrane',
    'ONIOM(B3LYP:AMBER)',
    'DFT/MM simulations',
    'PM6/MM simulations',
    'GFN2-xTB/MM simulations',
    'NNP/MM with ANI-2x',
    'ML/MM simulations',
    'Car-Parrinello MD',
    'ab initio MD of the active site',
    'Structure-based Go model',
    'UNRES force field',
    'AWSEM simulations',
    'oxDNA model',
    'pmx non-equilibrium transitions',
    'lambda-dynamics',
    'Simulations in vacuum',
    'The active site was treated quantum mechanically (B3LYP) and the rest with ff14SB.',
    'The quantum mechanical region comprised the substrate and Zn2+, described at the B3LYP level.',
    'A quantum mechanical (QM) region of 60 atoms was treated with DFT; the remainder with AMBER.',
    'A quantum-mechanical treatment of the active site was coupled to the AMBER force field.',
    'The system was coarse-grained to a 4-to-1 mapping and simulated for 20 us.',
    'The protein was coarse-grained for efficiency and run for 50 us.',
    'T-REX simulations',
    'TREX simulations',
    'CG-MD simulations of 20 us.',
    'The QM region (B3LYP/6-31G*) was embedded in the MM environment.',
    'GB/SA solvation',
    'Simulations were run in GBn2 implicit solvent (igb=8), and MM/GBSA was used for binding energies.',
    'All simulations used the GB-OBC implicit solvent model, with MM/GBSA rescoring of the last 50 ns.',
    'The protein was simulated with the generalized Born model (igb=5) and binding energies were estimated by MM/PBSA.',
    'Implicit solvent (GBn2) MD for 1 us per system and MM/GBSA binding energies.',
    'Production in implicit solvent, MM-GBSA on snapshots.',
    'Simulations were run in GB implicit solvent (igb=8), and binding energies were computed with MM/GBSA.',
    'MD in implicit solvent (GBn2) was followed by MM/GBSA rescoring.',
    'The OBC implicit solvent model (igb=5) was used for the 100 ns MD and for MM/GBSA.',
    'Both MD and MM/GBSA used the generalized Born model of Onufriev.',
    'Implicit-solvent MD with GB-OBC2 was run for 1 us, and MM-GBSA energies were computed from the last 500 ns.',
    'MD was carried out in vacuo, and MM/PBSA energies were computed.',
    'During equilibration, no REMD was used; production used 32 replicas exchanged every 2 ps.',
    'During equilibration, REMD was not used; 32 replicas spanning 300-450 K were then run.',
    'In the heating step, no REST2 was applied; 16 replicas were then run with solute scaling.',
    'In the first stage, no GaMD was used. In the second stage, boosts of 6 kcal/mol were added to the dihedral and total potential.',
    'Without REMD, sampling of the folded state is insufficient; 32 replicas were therefore used.',
    'Without REST2, the ligand remained trapped, so 16 replicas were used.',
    'Without GaMD, the transition is rarely seen within 1 us; boosts of 6 kcal/mol were therefore added.',
    'Without REMD, convergence would be poor.',
    'Thirty-two replicas at temperatures from 300 to 450 K exchanged conformations every 2 ps.',
    'Temperatures from 300 to 450 K were exchanged between 32 copies every 2 ps.',
    'Multiple replicas exchanged configurations every 2 ps across 300-450 K.',
    'REMD was not used; Hamiltonian exchange among 16 replicas was used instead.',
    'No REMD; instead, 16 replicas with scaled solute-solvent interactions were exchanged.',
    'Without REMD, sampling is inadequate, so 48 replicas were exchanged every 2 ps.',
    'Without REMD, convergence is unattainable; 48 replicas were therefore used.',
    'Without REMD, the folded state would be prohibitively slow to reach.',
    'Without GaMD, the transition would take milliseconds.',
    'Without GaMD, such transitions are rarely observed.',
    'Without REMD, the peptide did not fold within 1 us. Therefore 48 replicas spanning 300-500 K were exchanged every 2 ps.',
    '32 replicas were simulated at temperatures from 300 to 450 K.',
    'gREST_SSCR with 8 replicas',
    'GB solvent',
    'No REMD was used, except for the mutants.',
    'No REMD was used, apart from 10 ns.',
    'No GaMD was used, except in the final 200 ns.',
    'No REMD was used, whereas the mutant used it.',
    'No REMD was used, here, unlike for the mutants.',
    'Except for the apo system, no REMD was used.',
    'Apart from the mutant, no REMD was used.',
    'After equilibration, no REMD was used.',
    'From 100 ns onwards, no REMD was used.',
    'For the apo system, no REMD was used.',
    'With the exception of the mutant, REMD was not used.',
    'In the apo simulations, REMD was not used.',
    'For the mutant, REMD was not used.',
    'In the \ufb01rst 50 ns, no REMD was used.',
    '32 replicas at 300\u2013450 K were run.',
    'Replica temperatures were 300\u2013450 K.',
    'Exchange attempts every 2 ps between 24 temperatures (300\u2013450 K).',
    'Neighbouring replicas swapped every 2 ps.',
    'We added a boost to the total potential.',
    'Boosted potential applied to dihedrals.',
    'All MD calculations were performed in vacuo.',
    'Production MD in vacuo after geometry optimisation.',
    'Simulations in vacuum with AM1-BCC charges.',
    'The protein was simulated in vacuo for 100 ns, as in previous calculations.',
    'Paral\xadlel tempering with 24 replicas was used.',
    'Simu\xadlated tempering',
    'Repli\xadca exchange MD',
    'Coarse\xadgrained model',
    'Free\xadenergy perturbation',
    'Thermody\xadnamic integration',
    'Mile\xadstoning',
    'QM\u2212MM simulations of the active site.',
    'QM\u2215MM simulations',
    'QM\u2044MM simulations',
    'QM\uff0fMM simulations',
    'QM\u2014MM simulations',
    'Simulations used GB-HCT.',
    'GB-Neck2 was used.',
    'GBMV2 with CHARMM36m.',
    'MM/GBSA (igb=5) of trajectories run in GB.',
    'No REMD. It was used only for the mutant.',
    'No REMD or MARTINI was used. Both were used for the second system.',
    'Not standard REMD.',
    'AMD was used for 500 ns.',
    'Relative binding free energies were computed by thermodynamic integration, and MM/GBSA was used as a cheaper end-point estimate.',
    'Binding free energies were calculated with thermodynamic integration and with MM-PBSA.',
    'RBFE calculations and MM/GBSA rescoring were performed.',
    'Non-equilibrium switching with pmx was compared against MM/GBSA.',
    'Relative binding free energies were computed with OpenFE and compared with MM/GBSA.',
    'Absolute binding free energy calculations were run with Yank, and MM/PBSA was used for comparison.',
    'ABFE and MM/PBSA calculations were performed for each ligand.',
    'We did not use REMD; the holo system was run with 24 replicas.',
    'REMD was not used. Instead, 32 copies at temperatures from 300 to 450 K swapped configurations every 2 ps.',
    'REMD was not employed here. Neighbouring temperature windows exchanged coordinates every 2 ps.',
    'No REMD. Thirty-two replicas spanned 300 K to 450 K, swapping every 2 ps.',
    'No GaMD was used; a boosting potential was added to the dihedral term.',
    'No GaMD. A harmonic boost (sigma0P = 6 kcal/mol) was applied.',
    'The peptide was simulated in vacuo with AM1-BCC charges.',
    'All calculations were performed in vacuo with a dielectric constant of 4.',
    'The protein was simulated in vacuum with geometric restraints on the backbone.',
    'Gas-phase MD simulations of the protein ions in GROMACS.',
    'continuum dielectric model',
    'distance-dependent dielectric (4r)',
    'A CG model with 4-to-1 mapping',
    'CG simulations of 20 us',
    'WE simulations with 4 walkers per bin',
    'Exchanges between neighbouring temperatures were attempted every 2 ps',
    'hybrid quantum mechanical (QM)/molecular mechanical (MM) simulations of the active site',
    'A combined quantum mechanical and molecular mechanical (QM and MM) approach',
    'quantum mechanics molecular mechanics MD of the enzyme',
    'The chromophore was treated with quantum mechanics, the protein with ff14SB',
    'semi-empirical quantum mechanical MD of the active site',
    'a quantum mechanical description of the active site during dynamics',
    'quantum mechanical potential for the ligand, MM for the protein, 50 ps of dynamics',
    'Each protein was coarse-grained to Cα atoms and simulated with Langevin dynamics for 10 µs.',
    'Proteins were coarse grained onto the Cα trace and simulated for 50 µs',
    'Coarse-grained to the CA atoms, 10 µs of dynamics were run at 300 K',
    'coarse-grained to the alpha carbons; 10 µs Langevin dynamics with a Go-like potential',
    'The MM/GBSA (GBn2) model was the solvent for all 1 µs runs.',
    'MM/GBSA (GB-OBC) was used as solvent for the dynamics',
    'MM/GBSA with igb=5, then 100 ns of GB MD',
    'The ligand was decoupled from the bath over 20 windows',
    'GPU-accelerated molecular dynamics with dihedral boost',
    'Rest simulations with 8 replicas',
    'A quantum mechanics (QM) description of the active site was used, with the remainder at the MM level.',
    'The reaction was modelled with quantum mechanics and molecular mechanics combined.',
    'The reaction was simulated using combined quantum mechanical and molecular mechanical potentials.',
    'MD with a quantum-mechanical active site and molecular-mechanical environment.',
    'The active site was described at the quantum mechanical level (DFT) while the protein used ff14SB.',
    'Electrons of the active site were treated with quantum mechanics during the 50 ps trajectories.',
    'Quantum mechanics-based MD simulations of the active site.',
    'The active site was treated with quantum mechanics, the rest with molecular mechanics.',
    'The protein was coarse-grained to Cα atoms and simulated for 10 µs.',
    'The protein was coarse-grained to Cα atoms and simulated with a Gō-like potential.',
    'MM/GBSA (GB-OBC II) simulations of 100 ns were run.',
    'replica ex-\nchange MD',
    'parallel tem-\npering',
    'Mar-\ntini 3 force field',
    'alche-\nmical transformation',
    'gen-\neralized Born solvent',
    'im-\nplicit solvent',
    'weighted en-\nsemble',
    'thermodynamic inte-\ngration',
    'Ga-\nMD',
    'RE-\nMD',
    'QM/\nMM',
    'Coarse\u2010grained to Cα atoms and simulated for 10 µs',
    'RE\u200bMD with 32 replicas',
    'Ga\u200bMD was run',
    'QM\u200b/MM simulations',
    'RE\u2060MD',
    'RE\ufeffMD',
    'T_REMD',
    'REMD_32',
    'GaMD_run',
    'PT simulations with 24 replicas',
    'temperature-exchange MD',
    'Hamiltonian exchange',
    'replicas between 300 K and 450 K',
    '24 replicas, 300 ~ 450 K',
    'replicas at 300 K\u2192450 K',
    'TIES protocol',
    'MBAR analysis of 16 windows',
    'non-equilibrium switches',
    'CPMD simulations',
    'BOMD',
    'Born-Oppenheimer molecular dynamics',
    'semi-empirical PM6',
    'QM:MM',
    'QM and MM regions were coupled by electrostatic embedding',
    'igb 8',
    'GB MD',
    'DPD',
    'Go-like potential',
    'SMOG2',
    'structure-based model (SMOG)',
    'QM/MM45 MD',
    'REST234 with 16 replicas',
    'aMD45 simulations',
    'H-REX33',
    'TI45 calculations',
    'WExplore12 sampling',
    'REUS12',
    'WESTPA17',
    'temperature replica ex- change molecular dynamics',
    'Gaussian accel- erated MD',
    'QM:MM MD',
    'The QM-region contained 86 atoms',
    'The QM layer was B3LYP',
    'B3LYP/MM',
    'NNP/MM',
    'Simulations with GB (igb 5)',
    'Vacuum simulations of the peptide',
    'dielectric continuum',
    'dielectric constant of 4r',
    'PBSA solvent model during MD',
    'Onufriev\u2013Bashford\u2013Case model',
    'iamd = 3',
    'Well-tempered metadynamics with PLUMED',
    'OPES simulations',
    'Umbrella sampling with 30 windows',
    'Steered MD pulling at 1 Å/ns',
    'Simulations used REMD\u2122 throughout.',
    'Simulations used T-REMD\u2122 throughout.',
    'Simulations used H-REMD\u2122 throughout.',
    'Simulations used GaMD\u2122 throughout.',
    'Simulations used aMD\u2122 throughout.',
    'Simulations used QM/MM\u2122 throughout.',
    'Simulations used FEP\u2122 throughout.',
    'Simulations used MARTINI\u2122 throughout.',
    'Simulations used REST2\u2122 throughout.',
    'Simulations used GBSA\u2122 throughout.',
    'Simulations used GBn2\u2122 throughout.',
    'The MARTINI\u2122 force field was used.',
    'Simulations used REMD\u1d43 throughout.',
    'Simulations used T-REMD\u1d43 throughout.',
    'Simulations used H-REMD\u1d43 throughout.',
    'Simulations used GaMD\u1d43 throughout.',
    'Simulations used aMD\u1d43 throughout.',
    'Simulations used QM/MM\u1d43 throughout.',
    'Simulations used FEP\u1d43 throughout.',
    'Simulations used MARTINI\u1d43 throughout.',
    'Simulations used REST2\u1d43 throughout.',
    'Simulations used GBSA\u1d43 throughout.',
    'Simulations used GBn2\u1d43 throughout.',
    'Simulations used REMD\u00aa throughout.',
    'Simulations used T-REMD\u00aa throughout.',
    'Simulations used H-REMD\u00aa throughout.',
    'Simulations used GaMD\u00aa throughout.',
    'Simulations used aMD\u00aa throughout.',
    'Simulations used QM/MM\u00aa throughout.',
    'Simulations used FEP\u00aa throughout.',
    'Simulations used MARTINI\u00aa throughout.',
    'Simulations used REST2\u00aa throughout.',
    'Simulations used GBSA\u00aa throughout.',
    'Simulations used GBn2\u00aa throughout.',
    'Ligand charges were updated on the fly by quantum mechanical calculations at each step.',
    'Atomic charges were obtained on the fly from quantum mechanical calculations during sampling.',
    'The metal site was handled with quantum mechanics and its charges propagated every step.',
    'For analysis of the folding mechanism, the protein was coarse-grained and run with a 20 fs step for 10 million steps.',
    'For the clustering-based comparison we ran a coarse-grained model with 4-to-1 mapping.',
    'The protein was coarse-grained for the network-based model, with a 20 fs time step and 4-to-1 mapping.',
    'The guest was decoupled from the bath over 21 windows.',
    'Each ligand copy was gradually decoupled from the heat bath through 16 intermediate states.',
    'MM/GBSA (igb=5). Production sampling used the same model.',
    'AMD simulations were carried out with pmemd.cuda on GPUs.',
    'AMD runs were performed with ACEMD on GPUs.',
    'The AMD protocol (Hamelberg et al.) was run on NVIDIA GPUs.',
    'AMD simulations were run on NVIDIA GPUs.',
    'REPLICA EX- CHANGE MD',
    'replica ex-\r\nchange MD with 32 replicas',
    "The ligand's electrostatics were switched off, then its LJ terms, over 20 windows.",
    'cMD-\nGaMD protocol',
    'cMD-\nGaMD',
    'cMD-\naMD',
    'US-\nREMD',
    'the standard MD-\nREMD workflow',
    'Pre-\nREMD equilibration',
    'MD-\nFEP',
    'MM-\nFEP',
    'plain-\nREST2 runs',
    'CG-\nMartini',
    'SCC-\nDFTB',
    'water- remd',
    'mixed- martini model',
    'explicit- gamd',
    'x- fep calculations',
    'The Fe-S cluster was modelled with quantum mechanics (B3LYP), with fitted point charges for the protein environment',
    'The active site was modelled by quantum mechanics with RESP charges for the rest',
    'Quantum mechanical forces and charges were computed on the fly for the ligand',
    'Polarization was included by recomputing quantum mechanical charges every 10 fs',
    'Ligand charges were refitted to quantum mechanical ESP at every step',
    'The protein was coarse-grained for network analysis and sampled with a Langevin integrator',
    'A coarse-grained model for analysis of large motions was sampled with a Langevin thermostat',
    'Coarse-grained model for the analysis: 1 bead per residue, Langevin integrator, 300 K',
    'The drug was decoupled from the bath over 20 windows',
    'The compound was decoupled from the heat bath in 16 steps',
    'The molecule was decoupled from the bath',
    'Binding free energies were computed with MM/GBSA, and solvation free energies by thermodynamic cycles',
    'MM/GBSA (OBC2) sampling',
    'MM/GBSA (igb=8) for both sampling and scoring',
    'AMD simulations of 500 ns were run on GPUs',
    'AMD was performed on 4 GPUs for 1 \u00b5s',
    'AMD on GPUs, E = Vavg + 4 kcal/mol',
    'AMD runs on NVIDIA GPUs with pmemd.cuda',
    'Go-\nlike model',
    'Go- like model',
    'the go- model',
    'QM-\nregion of 40 atoms',
    '\u03c9B97X-D/MM',
    'B3LYP-D3/MM',
    'ANI-2x/AMBER',
    'xtb',
    'PM7',
    'igamd=3',
    'WE',
    'Poisson-Boltzmann continuum solvent',
    'The solvent was represented by a continuum',
    'The protein was simulated without water',
    'solvent-free simulations',
    'Soft-core potentials were used for vanishing atoms',
    'The ligand was turned off in 20 steps',
    'The lambda schedule had 21 points',
    'Each walker was resampled every 100 ps',
    'ELNEDYN22',
    '3-bead model',
    'Replica \u00e9xchange MD',
])
def test_a_method_named_in_words_that_go_on_is_never_plain_md_that_runs(details):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    confirm = [c for c in _choices(plan, "method_details") if c["label"] == "needs_you"]
    assert plan["method"] != "plain" or confirm


@pytest.mark.parametrize("details", [
    'MM/GBSA (igb=5) on 500 snapshots',
    'MMPBSA.py with igb=8',
    'MM/GBSA with the GB-OBC model',
    'MM/GBSA using the generalized Born model of Onufriev',
    'MM-GBSA calculations used the GBn2 model',
    'MM/GBSA (OBC2)',
    'MM/GBSA with GBNeck2',
    'MM\u2013GBSA with GBn2',
    'No REMD, GaMD or FEP was used.',
    'Neither REMD, GaMD nor FEP was used.',
    'Unbiased MD; no REMD, GaMD, or metadynamics.',
    'We did not carry out REMD.',
    'The trajectory was coarse-grained to Ca atoms for analysis.',
    'Partial charges were fitted to quantum mechanical calculations.',
    'RESP charges were derived from HF/6-31G* calculations in the gas phase.',
    'The ligand geometry was optimized in vacuo at the B3LYP/6-31G* level.',
    'Relative binding free energies were computed with MM/GBSA.',
    'Absolute binding free energy calculations were done by MM/PBSA.',
    'Structure-based models of the receptor were built by homology modeling.',
    'GPU-accelerated MD simulations with pmemd.cuda.',
    'Simulations were GPU-accelerated molecular dynamics in OpenMM.',
    'CUDA-accelerated simulations',
    'The ligand geometry was optimized in the gas phase at HF/6-31G*, and RESP charges were derived.',
    'Ligand charges were computed in vacuum with AM1-BCC.',
    'Partial charges were obtained by in vacuo QM calculations.',
    "Nonequilibrium work was computed from steered MD pulling with Jarzynski's equality.",
    'Expanded ensemble of conformers was obtained from NMR.',
    'We used the Rex lab protocol.',
    'No implicit solvent or Martini models were used.',
    'We did not use implicit solvent, GB or Martini.',
    'MM-GBSA calculations used the GBn2 model',
    'Three independent replicas were heated from 0 to 300 K.',
    'MM/GBSA (igb=5) on 500 snapshots',
    'MMPBSA.py with igb=8',
    'MM/GBSA with the GB-OBC model',
    'MM-GBSA calculations used the GBn2 model',
    'The trajectory was coarse-grained to Ca atoms for analysis.',
    'Partial charges were fitted to quantum mechanical calculations.',
    'Simulations were run on GPUs from AMD with the HIP backend of OpenMM.',
    'The thermostat was decoupled from the barostat',
])
def test_plain_md_s_own_words_leave_it_plain(details):
    """Never refused: plain MD, at once or once the person confirms words
    that name another method ("no REMD was used")."""
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    assert plan["method"] == "plain"


@pytest.mark.parametrize("details", [
    'GPU-accelerated MD simulations with pmemd.cuda.',
    'CUDA-accelerated simulations',
    'The trajectory occupied 50 GB of storage.',
    'Three independent replicas were heated from 0 to 300 K.',
    'Unbiased MD of the folded protein at 1 bar',
    'Plain MD run on AMD GPUs',
    'Two 6 us runs, analysed with a Markov state model (MSM)',
    'The pressure was maintained at 1 bar over the entire simulation.',
    'NPT equilibration at 1 bar over 10 ns followed.',
])
def test_plain_md_naming_no_other_method_asks_nothing(details):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    assert plan["method"] == "plain"
    assert not [c for c in _choices(plan, "method_details") if c["label"] == "needs_you"]


@pytest.mark.parametrize("membrane", [
    'POPC with 20% 1-palmitoyl-2-oleoyl-sn-glycero-3-phospho-L-serine',
    'POPC with 25% 1-palmitoyl-2-oleoyl-sn-glycero-3-phosphoethanolamine',
    'POPC with 30% CHL',
    'POPC with 20% TOCL2',
    'POPC with 10% TMCL',
    'POPC with 10% LPE',
    'POPC with 10% LPG',
    'POPC with 10% DPhPC',
    'POPC bilayer containing 20% anionic lipids',
    'POPC with 30% saturated lipids',
    'POPC with 10% PUFA lipids',
    'POPC with 10% bacterial lipids',
    'a bilayer composed mostly of POPC',
    'POPC-rich bilayer',
    'POPC with lanosterol',
    'POPC with lipid A',
    'POPC with 10% oleic acid',
    'POPC with 10% fatty acids',
    'POPC with 10% PEG-lipid',
    'POPC with 10% triolein',
    'POPC with 10% sulfatide',
    'POPC with 10% GalCer',
    'POPC with 10% bis(monoacylglycero)phosphate (BMP)',
    'POPC/DPC',
    'POPC with 20% DPC micelles',
    'POPC with 5% detergent (DDM)',
    'POPC with K+ and CL- lipids',
    'POPC with Na+/CL- 20%',
    'POPC, Na+, CL- (20%)',
    'POPC: dioleoyl-phosphatidylcholine',
    'DOPC bilayer (dipalmitoyl-phosphatidylcholine domains)',
    'POPC: dipalmitoyl-phosphatidylcholine 3 to 1',
    'DOPC (dipalmitoyl-phosphatidylcholine rich) bilayer',
    'POPC with TOCL',
    'POPC with 10% DLiPC',
    'POPC with desmosterol',
    'DPPC with 10% palmitic acid',
    'DPPC with 10% PEGylated lipid',
    'POPC with Na+, CL- lipids (10%)',
    'POPC, Na+/CL- 10 percent',
    'POPC with 10% eggPC',
    'POPC with 10% CHS',
    'POPC with 10% asolectin',
    'POPC with E. coli total lipids',
    'POPC with 20% acidic lipids',
    'POPC with 20% neutral lipids',
    'POPC with 30% ethanolamine lipids',
    'POPC with 30% phosphoethanolamine',
    'POPC with 10% SDS',
    'POPC with 10% LDAO',
    'POPC with 10% Triton X-100',
    'POPC with octyl glucoside',
    'POPC with 10% Gb3',
    'POPC with 10% LPA',
    'POPC with 10% LPI',
    'POPC with 10% Cer',
    'POPC with 10% S1P',
    'POPC with lipid II',
    'POPC with 2% Ni-NTA-DGS',
    'POPC with 5% DOGS',
    'POPC (80 mol%)',
    'POPC 80%',
    'binary POPC bilayer',
    'POPC bilayer, ternary',
    'two-component bilayer of POPC',
    'POPC with 20% minor lipid',
    'POPC with 20% of the lipid X',
    'POPC with 10% squalene',
    'POPC with 10% tocopherol',
    'POPC with 10% DHA',
    'POPC, cholesterol absent from the upper leaflet but 30% in the lower',
    'POPC (cholesterol omitted from the outer leaflet; 20 mol% in the inner leaflet)',
    'POPC with PIP2 absent from the outer leaflet',
    'POPC with SM excluded from the raft domain',
    'POPC with CHL1 excluded from the outer leaflet',
    'POPC; cholesterol was omitted in the control only',
    'POPC bilayer with PIP2 omitted from the outer leaflet',
    'POPC bilayer, cholesterol absent in the outer leaflet only',
    'POPC bilayer; SM is absent from the inner leaflet',
    "POPC with 1,2-dioleoyl-sn-glycero-3-phospho-(1'-rac-glycerol)",
    'POPC with 1-palmitoyl-2-oleoyl-sn-glycero-3-phosphate',
    "POPC with 1-palmitoyl-2-oleoyl-sn-glycero-3-phospho-(1'-myo-inositol)",
    "POPC with 1',3'-bis[1,2-dioleoyl-sn-glycero-3-phospho]-glycerol",
    'POPC with 20% PtdSer',
    'POPC with 20% PtdEtn',
    'POPC with 20% PtdCho',
    'POPC with 20% PtdGro',
    'POPC with 5% DGPC',
    'POPC with 5% DNPC',
    'POPC with 5% DXPC',
    'POPC with 5% DIPC',
    'POPC with 5% DUPC',
    'POPC with 5% PUPC',
    'POPC with 5% NSM',
    'POPC with 5% CER160',
    'POPC with 5% Cer16',
    'POPC with 5% SAPI24',
    'POPC with 5% PI3P',
    'POPC with 5% LBPA',
    'POPC with 5% GlcCer',
    'POPC with 5% CLR',
    'POPC with 5% POPGly',
    'POPC with 5% OLE',
    'POPC with 5% TAG',
    'POPC with 5% C55P',
    'POPC (n=72), DPSM (n=8)',
    'POPC with 10% sopc',
    '90% POPC',
    'POPC at 90 mol%',
    'POPC, 90%',
    'POPC predominantly',
    'POPC as the main lipid',
    'POPC-enriched bilayer',
    'POPC, etc.',
    'POPC (see Table S1 for the full composition)',
    'A model ER membrane containing POPC',
    'POPC (palmitoyl-oleoyl-phosphatidylcholine, 50 mol%)',
    'POPC with 30% cho\xadlesterol',
    'POPC with 20% phosphatidyl\u2010serine',
    'POPC with 5% GM2',
    'POPC with 5% GT1b',
    'POPC with 5% PI4,5P2',
    'POPC with 5% triacylglycerol (TAG)',
    'POPC with 10% oleate',
    'POPC with 10% sodium palmitate',
    'POPC with 10% campesterol',
    'POPC with 10% oxysterols',
    'POPC with 10% zymosterol',
    'POPC with 10% DiPhyPC',
    'POPC with 10% PGs',
    'POPC with 10% GL3',
    'POPC bilayers with and without cholesterol',
    'POPC with K+, CL-, 20% of lipids',
    'POPC 7\uff1a3',
    'POPC 7\u22363',
    'POPC 70/30',
    'POPC (7/3)',
    'POPC 3 to 1',
    'POPC 0.7',
    'POPC bilayer, 70\u2030',
    'POPC with 胆固醇',
    'POPC bilayer with ДОПЕ',
    'POPC bilayer with χοληστερόλη',
    'POPC with K+ CL-, 20 molecules',
    'POPC bilayer with Na+ CL- at 20',
    'POPC with choline chloride',
    'POPC with brain extract',
    'POPC with soy extract',
    'POPC with oleate',
    'POPC with stearate',
    'POPC 70-30',
    'POPC 70\u201330',
    'POPC 70 30',
    'POPC 0,7',
    'POPC 7 of 10',
    'POPC 70 of 100',
    'POPC 7 per 10',
    'POPC 70 per 30',
    'POPC 2 x 64 and 2 x 16 lipids',
    'POPC 64 per leaflet and 16 per leaflet',
    'POPC bilayer, 96 POPC and 32 lipids',
    'POPC in the outer leaflet and lipids in the inner leaflet',
])
def test_a_second_lipid_however_written_makes_a_mixture(membrane):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "membrane": _stated(membrane)})
    # Refused where the second lipid is named; put to the person where only
    # a share says so ("POPC 80%"). Never one lipid that runs.
    assert plan["state"] in ("cannot_run", "needs_you")


@pytest.mark.parametrize("membrane", [
    'POPC (1-palmitoyl-2-oleoyl-sn-glycero-3-phosphatidylcholine) bilayer',
    'DPPC (dipalmitoylphosphatidylcholine) bilayer',
    'POPC (palmitoyloleoylphosphatidylcholine)',
    'POPC (palmitoyl oleoyl phosphatidylcholine) bilayer',
    'POPC (a phosphatidylcholine) bilayer',
    'POPC, a zwitterionic phosphatidylcholine',
    'POPC lipids (phosphatidylcholine)',
    'POPC membrane, neutralized by CL\u2212',
    'POPC bilayer; CL- were added to neutralize',
    'POPC bilayer with 15 CL- ions per leaflet',
    'POPC (1-palmitoyl-2-oleoyl-sn-glycero-3-phosphocholine) bilayer',
    '1-palmitoyl-2-oleoyl-phosphatidylcholine (POPC) bilayer',
    'AMPA receptor in a POPC bilayer',
    'TRPC channel in a POPC bilayer',
    'POPC bilayer, mostly hydrated',
    'POPC bilayer, mainly to test the force field',
    'POPC bilayer with an Arg-rich peptide',
    'POPC bilayer, 128 lipids, other lipids absent',
])
def test_one_lipid_written_out_beside_its_abbreviation_is_one_lipid(membrane):
    """Built as the one lipid named: at once where every word is the
    lipid's, a bilayer's, its ions' or its box's; otherwise once the person
    confirms the words this does not read ("AMPA receptor in a POPC
    bilayer"). Never refused as a mixture."""
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "membrane": _stated(membrane)})
    assert plan["state"] in ("ready", "needs_you")
    assert plan["config"]["setup"]["membrane"]


# -- what the review of 1604 found missed or asked without need -------------
def _method_said(details):
    """The method planned, or "confirm" for plain MD that waits for the
    person to confirm the details' words."""
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    confirm = [c for c in _choices(plan, "method_details") if c["label"] == "needs_you"]
    return plan["method"] if plan["method"] != "plain" else ("confirm" if confirm else "plain")


@pytest.mark.parametrize("details, method", [
    ("Free energies were estimated with the Bennett acceptance ratio.", "confirm"),
    ("Analysed with pymbar.", "confirm"),
    ("alchemlyb was used for the analysis.", "confirm"),
    ("Simulations were set up with OpenFE.", "confirm"),
    ("perses was used to set up the transformations.", "confirm"),
    ("QM\\MM simulations of the active site", "qm_mm"),
    ("QM|MM simulations", "qm_mm"),
    ("Replica \u0435xchange MD", "replica_exchange"),
    ("R\u0415MD simulations", "replica_exchange"),
    ("The string method with swarms of trajectories was used.", "confirm"),
    ("Constant pH MD at pH 7.", "confirm"),
    ("constant-pH simulations were run.", "confirm"),
    ("Brownian dynamics simulations of association.", "confirm"),
    ("ABF along the distance.", "confirm"),
    ("adaptive biasing force along the distance", "confirm"),
    ("TAMD with a fictitious temperature of 3000 K", "confirm"),
    ("Hyperdynamics with a bias potential", "confirm"),
    ("Enhanced sampling with 32 replicas.", "confirm"),
    ("Metropolis acceptance between neighbouring temperatures.", "confirm"),
    ("the rest simulations used 16 replicas", "confirm"),
    ("Free energies came from a thermodynamic cycle.", "confirm"),
    # A soft hyphen after a whole name is read apart as well as joined.
    ("Simulations used REMD\u00ad\nsimulations with 24 replicas", "replica_exchange"),
    ("T-REMD\u00ad\nsimulations were run.", "replica_exchange"),
    ("aMD\u00ad\nsimulations were run", "accelerated"),
    ("QM/MM\u00ad\nMD", "qm_mm"),
    ("FEP\u00ad\nlike", "free_energy"),
    ("Martini\u00ad\nmodel", "coarse_grained"),
    ("GBSA\u00ad\nmodel", "implicit_solvent"),
    # A force field after a QM method is the MM half of a QM/MM label.
    ("B3LYP/GAFF2/AMBER", "confirm"),
    ("HF/GAFF/AMBER", "confirm"),
    ("M06-2X/TIP3P/CHARMM", "confirm"),
    ("QM(B3LYP)/GAFF2/AMBER", "confirm"),
    ("B3LYP/6-31G*:ff14SB/AMBER", "confirm"),
    ("B3LYP / TIP3P / CHARMM", "confirm"),
    ("B3LYP-GAFF2/AMBER", "confirm"),
    # Weighted ensemble's capitals before a verb, and exchange moves.
    ("WE used 100 iterations with 4 trajectories per bin.", "confirm"),
    ("WE ran 500 iterations of 100 ps each.", "confirm"),
    ("The WE used 200 iterations", "confirm"),
    ("WE thank the reviewers.", "confirm"),
    ("Proton exchange attempts were made every 500 steps.", "confirm"),
    ("Solvent exchange between neighbouring windows every 2 ps.", "confirm"),
    ("ion exchange moves between the reservoir and the box", "confirm"),
    ("lipid exchange moves between leaflets", "confirm"),
    ("Water exchange moves were attempted every 1000 steps.", "confirm"),
    ("21 lambda = 0.05 nm spacing", "confirm"),
    # Other wordings of the methods newly read.
    ("CpHMD at pH 7", "confirm"),
    ("Brownian-dynamics simulations", "confirm"),
    ("BD simulations of association", "confirm"),
    ("Association rates from Browndye", "confirm"),
    ("The string-method was used.", "confirm"),
    ("A finite temperature string was optimised.", "confirm"),
    ("eABF along the distance", "confirm"),
    ("meta-eABF along two distances", "confirm"),
    ("adaptive-biasing force", "confirm"),
    ("hyper-dynamics", "confirm"),
    ("parallel replica dynamics", "confirm"),
    ("d-AFED in the torsions", "confirm"),
    ("mbar analysis of the windows", "confirm"),
    ("Set up with BioSimSpace", "confirm"),
    ("Crooks fluctuation theorem", "confirm"),
    ("Jarzynski equality", "confirm"),
    ("Zwanzig equation", "confirm"),
    ("free energy calculations were run", "confirm"),
    ("Open-FE was used", "confirm"),
    ("pmx was used to build hybrid topologies", "confirm"),
    ("YANK", "confirm"),
    ("SOMD", "confirm"),
    ("temperature-accelerated MD", "accelerated"),
    # The second review's.
    ("Bennett\u2019s acceptance ratio", "confirm"),
    ("bennett-acceptance ratio", "confirm"),
    ("QligFEP", "confirm"),
    ("QM\u2216MM simulations", "qm_mm"),
    ("QM\u2223MM simulations", "qm_mm"),
    ("QM\u29f8MM simulations", "qm_mm"),
    ("\u0280\u1d07\u1d0d\u1d05 simulations", "replica_exchange"),
    ("R\u1d07MD simulations", "replica_exchange"),
    ("The reactive center was treated with B3LYP/6-31G* and the rest of the enzyme with "
     "ff14SB/AMBER, coupled through electrostatic embedding.", "confirm"),
    ("The enzyme active site was described at the B3LYP-D3/def2-SVP level and the "
     "environment with ff14SB/AMBER using electrostatic embedding in ChemShell.", "confirm"),
    ("B3LYP/ \nGAFF2/AMBER level", "confirm"),
    ("B3LYP/  GAFF2/AMBER", "confirm"),
    ("B3LYP and GAFF2/AMBER", "confirm"),
    ("GAFF2/AMBER/MM", "confirm"),
    ("TIP3P/CHARMM/MM", "confirm"),
    ("a" * 41 + "/MM", "confirm"),
    ("Water exchanges were attempted every 1000 steps by GCMC.", "confirm"),
    ("Water exchange with a bulk reservoir was performed every 1 ps using grand canonical "
     "Monte Carlo.", "confirm"),
    ("water exchange between adjacent windows", "confirm"),
    ("Water exchange was attempted every 1000 steps.", "confirm"),
    ("Gas-phase minimization and 100 ns MD were performed for the peptide ion.", "confirm"),
    ("The protein ion was optimised in the gas phase and then simulated for 1 \u03bcs.", "confirm"),
    ("Gaussian-accelerated using GPUs", "confirm"),
    ("The GB data were compared with explicit-solvent runs.", "confirm"),
    ("adaptive bias force", "confirm"),
    ("Brownian-dynamic simulations", "confirm"),
    ("driven adiabatic free energy dynamics", "confirm"),
    ("ParRep", "confirm"),
    ("thermodynamic-cycle", "confirm"),
    ("free energy estimation", "confirm"),
    ("SOMD2", "confirm"),
    ("Open Free Energy toolkit", "confirm"),
    ("AWH was used along the distance CV", "confirm"),
    ("Targeted MD (TMD)", "confirm"),
    ("Supervised MD (SuMD)", "confirm"),
    ("Grand canonical Monte Carlo/MD (GCMC/MD)", "confirm"),
    ("adaptive sampling seeded from MSM states", "confirm"),
    ("The heme has an open Fe coordination site.", "plain"),
])
def test_another_method_the_review_found_missed_is_never_plain_md_that_runs(details, method):
    assert _method_said(details) == method


@pytest.mark.parametrize("details", [
    "Runs on AMD MI250X GPUs",
    "AMD MI250X",
    "\u03bb = 488 nm",
    "WE THANK THE REVIEWERS.",
    "Pressure was 1013 mbar.",
    "Several GB of storage were used.",
    "Monte Carlo barostat with Metropolis acceptance at 1 bar",
    "The Tabf domain was simulated.",
])
def test_plain_wordings_the_review_found_asked_without_need_ask_nothing(details):
    assert _method_said(details) == "plain"


@pytest.mark.parametrize("details", [
    "TIP3P/CHARMM water",
    "GAFF2/AMBER for the ligand",
    "CGenFF/CHARMM parameters",
    "Simulations were accelerated using GPUs.",
    "Simulations were accelerated by HMR.",
    "Water exchange between the pocket and bulk was analysed.",
    "Ligand gas-phase optimisation at HF/6-31G*.",
])
def test_plain_wordings_that_may_stand_in_another_method_s_description_still_ask(details):
    """Each could be part of a description of a method this does not run (the
    MM half of a QM/MM label, a grand canonical move, a gas-phase system), so
    no exception is made for it: the person confirms."""
    assert _method_said(details) == "confirm"


@pytest.mark.parametrize("method, details", [
    ("umbrella sampling", "Along the distance with 30 windows of 10 ns; the PMF was obtained with "
                          "MBAR using pymbar [40], convergence checked with pymbar timeseries."),
    ("metadynamics", "Convergence was checked with pymbar timeseries."),
    ("umbrella sampling", "The PMF was computed from the umbrella windows using the Bennett "
                          "acceptance ratio (BAR) as implemented in GROMACS gmx bar."),
    ("umbrella sampling", "alchemlyb was used to analyse umbrella windows"),
    ("steered MD", "Forward and reverse pulling work distributions were combined with the "
                   "Bennett acceptance ratio to give the PMF."),
    ("metadynamics", "Well-tempered metadynamics; YANK utilities were used for analysis."),
])
def test_a_general_tool_named_beside_a_runnable_method_leaves_that_method(method, details):
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated(method), "method_details": _stated(details, details)})
    assert plan["method"] == method.split()[0] and plan["state"] == "needs_you"


@pytest.mark.parametrize("details, method", [
    ("Veloci\u00adties were assigned at 300 K.", "plain"),
    ("The structural proper\u00adties were analysed.", "plain"),
    ("unres\u00adtrained MD of the protein", "plain"),
    ("GPU-\u00adaccelerated MD", "plain"),
    ("Results were com\u00adpared with REMD data.", "confirm"),
    ("We used the Mar\u00adtini\u00ad\nforce field", "coarse_grained"),
    ("T-RE\u00adMD\u00ad\nsimulations", "replica_exchange"),
    ("a\u00adMD\u00ad\nsimulations", "accelerated"),
    ("REMD\u00ad\nsimulations with 24 replicas", "replica_exchange"),
])
def test_a_soft_hyphen_is_read_as_before(details, method):
    """A soft hyphen is dropped, as it was: a word it breaks is read whole and
    one beside a line's end keeps its space."""
    assert _method_said(details) == method


@pytest.mark.parametrize("details, method", [
    ("Simulations were accelerated using GPUs and a boost potential.", "accelerated"),
    ("Simulations were accelerated using GPUs; boosts were tested.", "confirm"),
    ("GAFF2/AMBER for the ligand and DFT/AMBER for the cofactor", "confirm"),
    ("GAFF2/AMBER for the ligand; DFTB/MM for the active site.", "qm_mm"),
    ("Water exchange between replicas was attempted every 2 ps.", "confirm"),
    ("\u03bb = 488 nm; 21 \u03bb windows were used", "free_energy"),
    ("WE THANK THE REVIEWERS; WE simulations used 4 walkers per bin.", "milestoning"),
    ("Several GB of storage were used for the umbrella windows.", "confirm"),
])
def test_a_plain_phrase_hides_only_its_own_word(details, method):
    """The fixed plain phrases set aside only the word inside them: another
    name or loose word in the same details is read as it would be alone."""
    assert _method_said(details) == method


def test_a_long_run_of_letters_and_brackets_is_read_at_once():
    import time

    for details in ("(a" * 15000, "water exchange " * 6000, "ion exchange " * 6000,
                    "\u03bb" + " " * 30000 + "x", "lambda" + " " * 30000 + "x",
                    "bennett" + " " * 30000 + "x", "a" + " " * 30000 + "/MM"):
        start = time.monotonic()
        _method_said(details)
        assert time.monotonic() - start < 10


@pytest.mark.parametrize("membrane, state", [
    ("POPC (96, 32)", "needs_you"),
    ("POPC bilayer of 96, 32 lipids", "needs_you"),
    ("POPC 2 x 64 and 2 x 16 lipids", "needs_you"),
    ("POPC bilayer, 0,15 M NaCl", "ready"),
    ("POPC bilayer, 1-1.5 nm of water", "ready"),
    ("POPC bilayer, 70-30", "needs_you"),
    ("POPC bilayer, 96,32", "needs_you"),
    ("POPC 70,30 a", "needs_you"),
    ("POPC, 96-32 a", "needs_you"),
    ("POPC 70,30 mol", "needs_you"),
    ("POPC 75-25 mol", "needs_you"),
    ("POPC 80-20 m", "needs_you"),
    ("POPC 96-32 k", "needs_you"),
    ("POPC 96\u00ad\n32 lipids", "needs_you"),
    ("POPC 70\u00ad\n30", "needs_you"),
    ("POPC 10 x 10 %", "needs_you"),
    ("POPC 50 x 2 %", "needs_you"),
    ("POPC 10 x 10 mol%", "needs_you"),
    ("POPC " + "9" * 5000 + " x 2 lipids", "needs_you"),
    ("POPC " + "9" * 3000 + " x " + "9" * 3000, "needs_you"),
    ("POPC bilayer 70-30.", "needs_you"),
    ("POPC bilayer, 75-25.", "needs_you"),
    ("POPC 96,32.", "needs_you"),
    ("POPC bilayer with 70,30.", "needs_you"),
    ("POPC bilayer of 96-32. TIP3P water", "needs_you"),
    ("POPC bilayer 70-30.\nwater", "needs_you"),
    ("POPC bilayer, 1-1.5.", "needs_you"),
    ("POPC bilayer 0,15.", "needs_you"),
    ("POPC bilayer, 96 and 32.", "needs_you"),
    ("POPC bilayer, 96, 32.", "needs_you"),
    ("POPC bilayer of 128 lipids.", "ready"),
    ("POPC bilayer of 128 lipids, 150 mM NaCl, 1-1.5 nm of water.", "ready"),
    ("POPC bilayer 70 x 30", "needs_you"),
    ("POPC bilayer (70 x 30)", "needs_you"),
    ("POPE/\u0421L bilayer", "needs_you"),
    ("POPE bilayer with \u0421\u029f", "needs_you"),
    ("POPC bilayer with lipid \u0391", "needs_you"),
])
def test_counts_of_lipids_are_read_as_counts(membrane, state):
    """Two counts of lipids however written ("(96, 32)" or "70 x 30") need
    the person; a decimal comma or a range with its unit does not. A word in
    letters that only look Latin needs the person too."""
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "membrane": _stated(membrane)})
    assert plan["state"] == state
    assert plan["config"]["setup"]["membrane"] == membrane[:4]


# -- 1606: names across a line's end, more spellings, counts said once -------
@pytest.mark.parametrize("details, method", [
    ("unres-\ntrained MD of the protein", "confirm"),
    ("Unres-\ntricted sampling", "confirm"),
    ("reus-\nable scripts", "confirm"),
    ("remd-\nesivir binding", "confirm"),
    ("Martini-\nque style", "confirm"),
    ("Material proper-\nties were computed. We used QM/MM-\nbased dynamics.", "qm_mm"),
    ("tar-\ngeted MD- and US-based runs", "confirm"),
    ("Material proper-\nties were computed.", "confirm"),
    ("The structural proper-\nties were analysed.", "confirm"),
    ("MATERIAL PROPER-\nTIES WERE COMPUTED.", "confirm"),
    ("TI-\nTRATION CURVES WERE FITTED.", "confirm"),
    ("We used QM/MM-\nbased dynamics.", "qm_mm"),
    ("REMD-\nbased sampling with 24 replicas.", "replica_exchange"),
    ("GaMD-\nbased runs.", "accelerated"),
    ("REMD- and US-based sampling.", "replica_exchange"),
    ("We ran replica ex-\nchange MD.", "replica_exchange"),
    ("REMD\u00ad\nsimulations with 24 replicas", "replica_exchange"),
    ("REMD\u00ad simulations with 24 replicas", "replica_exchange"),
    ("Mar-\ntini\u00ad model of the protein", "coarse_grained"),
    ("We used the Mar-\ntini\u00ad\nforce field.", "coarse_grained"),
    ("T-RE-\nMD\u00ad\nsimulations were run.", "replica_exchange"),
    ("We ran Ga-\nMD\u00ad\nsimulations of the receptor.", "accelerated"),
    ("with\u00ad\nRE-\nMD runs", "confirm"),
    ("\u03b1MD simulations of the helix", "plain"),
])
def test_a_name_found_only_across_a_line_s_end_asks(details, method):
    """A name read with line-end hyphens joined makes the study that
    method, and so does one read as written before a hyphen and a word
    of a fixed list ("QM/MM-" before "based"); one read only as the words
    are written otherwise ("proper-" before "ties" reads TIES) may be a
    piece of a broken word, so the person decides."""
    assert _method_said(details) == method


@pytest.mark.parametrize("details, method", [
    ("pHREMD simulations at pH 4 to 8", "replica_exchange"),
    ("targeted-MD toward the open state", "confirm"),
    ("adaptive-sampling rounds of 50 runs", "confirm"),
    ("Supervised MD of the ligand's approach", "confirm"),
    ("Water was sampled with GCNCMC moves.", "confirm"),
    ("We used GCMC.", "confirm"),
    ("Targeted MDs pulled the ligand out.", "confirm"),
    ("Ten targeted MD1-MD10 runs", "confirm"),
    ("Supervised MDs of the ligand", "confirm"),
    ("Buried waters were placed by GCMC.", "confirm"),
    ("Simulations in the muVT ensemble", "confirm"),
    ("Simulated temper\u03afng over 300 to 400 K", "replica_exchange"),
    ("Replica \u0401xchange MD", "replica_exchange"),
    ("Simulations in the \u03bcVT ensemble", "confirm"),
    ("Simulations in the \u00b5VT ensemble", "confirm"),
    ("Q ligFEP was used for the series.", "confirm"),
    ("QM\u2502MM simulations of the active site", "qm_mm"),
    ("QM\u2503MM simulations of the active site", "qm_mm"),
    ("QM\u01c0MM simulations of the active site", "qm_mm"),
    ("B3LYP/6-31G*/MM optimisation of the cofactor", "confirm"),
    ("Replica \u0451xchange MD", "replica_exchange"),
    ("Simulated temper\u03b9ng over 300 to 400 K", "replica_exchange"),
    ("Hamiltonian replica ex\u0441hange", "replica_exchange"),
    ("\ua730EP of the ligand series", "free_energy"),
    ("\ua731MD of the pulling", "confirm"),
])
def test_more_spellings_of_another_method_are_never_plain_md_that_runs(details, method):
    assert _method_said(details) == method


@pytest.mark.parametrize("details", [
    "\u03bbmax = 280 nm",
    "lambda_max = 280 nm",
    "\u03bbem 520 nm",
    "Runs on AMD-MI250X GPUs",
    "Runs on AMD-EPYC processors",
    "Runs on AMD MI300A and MI250X GPUs",
    "targeted MDM2 inhibitors",
])
def test_more_plain_wordings_ask_nothing(details):
    assert _method_said(details) == "plain"


@pytest.mark.parametrize("details", [
    "\u03bbmax = 280 nm; 21 \u03bb windows",
    "AMD-boosted dihedrals",
    "TIP3P | AMBER",
    "REMD\u03bbmax = 280 nm",
    "FEP\u03bbmax = 280 nm",
    "AMD-MI250XREMD",
    "REST2\u03bbmax = 280 nm",
    "PM6\u03bbmax = 280 nm",
    "AMD-MI210TI",
    "AMD-MI100US",
])
def test_a_plain_wording_beside_another_word_still_asks(details):
    assert _method_said(details) != "plain"


@pytest.mark.parametrize("membrane, state", [
    ("POPC bilayer 96,32.5", "needs_you"),
    ("POPC bilayer 70,30.0", "needs_you"),
    ("POPC bilayer, 70,1.5 m", "needs_you"),
    ("POPC bilayer 1,234.5", "needs_you"),
    ("POPC bilayer, 0,15 M NaCl.", "ready"),
    ("POPC bilayer of 2 x 64 lipids", "ready"),
    ("POPC bilayer of 2 x 64 lipids.", "ready"),
    ("POPC bilayer of 2\u00d764 lipids", "ready"),
    ("POPC bilayer of 2x64 lipids", "ready"),
    ("POPC bilayer, (64, 64) lipids per leaflet", "needs_you"),
    ("POPC bilayer, 64 and 64 lipids per leaflet", "needs_you"),
    ("POPC bilayer, leaflets of 2 x 64 and 64 lipids", "needs_you"),
    ("POPC bilayer, 64 and 64 lipids", "needs_you"),
    ("POPC bilayer (50, 50)", "needs_you"),
    ("POPC bilayer, 1 and 1", "needs_you"),
    ("POPC 72 and 72 and 72 lipids per leaflet", "needs_you"),
    ("POPC bilayer, 2 x 2 x 64", "needs_you"),
    ("POPC bilayer, 2 x 72 lipids, 64 per leaflet", "needs_you"),
    ("POPC bilayer, 2 x 64 and 2 per leaflet", "needs_you"),
    ("POPC bilayer of 128 lipids, 64 per leaflet", "ready"),
    ("POPC bilayer of 2 x 64 lipids, 64 per leaflet", "ready"),
    ("POPC bilayer, 2 x 64 lipids, 32 per leaflet", "needs_you"),
    ("POPC bilayer, 2 x 128 lipids, 64 per leaflet", "needs_you"),
    ("POPC bilayer, leaflets of 72 and 72 lipids, 2 x 10 nm", "needs_you"),
    ("POPC bilayer, (96, 32) lipids per leaflet", "needs_you"),
    ("POPC bilayer, 2 x 64 and 2 x 16 lipids", "needs_you"),
    ("POPC bilayer 64 x 64", "needs_you"),
    ("POPC bilayer 70 x 30.", "needs_you"),
    ("POPC bilayer at 1 bar", "ready"),
    ("POPC bilayer at 1 atm pressure", "ready"),
    ("POPC bilayer at 310 K, at a pressure of 1.01325 bar", "ready"),
    ("POPC bilayer at -100 bar", "needs_you"),
    ("POPC bilayer at 2000 bar", "needs_you"),
    ("POPC bilayer, pressure of -50 bar", "needs_you"),
    ("POPC bilayer at \u2212 1 bar", "needs_you"),
    ("POPC bilayer at 100 \u00b1 1 bar", "needs_you"),
    ("POPC bilayer at 1 bar and 2000 bar", "needs_you"),
    ("POPC bilayer under a surface pressure of 1 bar", "needs_you"),
    ("POPC bilayer at 1.01325 atm", "needs_you"),
    ("POPC bilayer, 130 lipids, 64 per leaflet", "needs_you"),
])
def test_counts_said_per_leaflet_are_one_count(membrane, state):
    """"2 x 64" is 64 lipids in each leaflet; a count beside a per-leaflet
    count must be it or twice it, and a "2 x" count must be it; two counts
    otherwise still need the person; a share whose second number has a
    decimal is still a share; only the pressure kept, 1 bar, said in a
    few fixed phrases, is plain."""
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "membrane": _stated(membrane)})
    assert plan["state"] == state
    assert plan["config"]["setup"]["membrane"] == membrane[:4]


# -- 1607: soft hyphens, glued names, more readings, counts of each kind ------
@pytest.mark.parametrize("details, method", [
    ("We ran RE\u00ad\nMD of the peptide", "confirm"),
    ("Metady\u00ad\nnamics of the loop", "confirm"),
    ("REMDsimulations with 24 replicas", "confirm"),
    ("Runs on AMD-EPYC processorsREMD", "confirm"),
    ("REMDlambda max = 280 nm", "confirm"),
    ("The QM-\nregion held the cofactor.", "qm_mm"),
    ("A G\u014d-\nlike model of the protein", "coarse_grained"),
    ("G-\nREST over the binding site", "replica_exchange"),
    ("gREST over the binding site", "replica_exchange"),
    ("REMD-\nmodels of the loop", "replica_exchange"),
    ("GaMD-\nprotocols of the receptor", "accelerated"),
    ("FEP-\ncalculations of the series", "free_energy"),
    ("REMD-\nMDs of the loop", "replica_exchange"),
    ("Replica e\u03c7change MD", "replica_exchange"),
    ("\ua4e3\ua4f0\ua4df\ua4d3 simulations with 24 replicas", "replica_exchange"),
    ("We targeted MDM2 GB model runs.", "implicit_solvent"),
    ("We targeted MDM2 with GB simulations.", "implicit_solvent"),
    ("HIV-1 GB simulations were run.", "confirm"),
    ("2 GB simulations of 100 ns each.", "confirm"),
    ("REMDSimulations were run.", "confirm"),
    ("32REMD replicas.", "confirm"),
    ("REMD32replicas", "confirm"),
    ("HREXsimulations", "confirm"),
    ("MARTINImodel of the protein", "confirm"),
    ("Simulations were run with-\nout replica-\nexchange.", "confirm"),
    ("Our results were com-\npared with REMD-\nmethods.", "confirm"),
    ("Ex-\ncept the QM- atoms, all was MM.", "confirm"),
    ("Binding energies came from MM/\nGBSA-\ncalculations on the trajectory.", "confirm"),
    ("Binding energies were computed with MM / GBSA.", "confirm"),
    ("Binding energies were computed with MM/ GBSA.", "confirm"),
    ("Simulations were run with\u00ad\nout REMD-\nmethods.", "confirm"),
    ("Simulations were run wit\u00ad\nhout REMD.", "confirm"),
    ("Simulations were run ins -\ntead GaMD-\nprotocols here.", "confirm"),
    ("Binding energies from MM/GB\u00ad\nSA with an implicit solvent model.", "confirm"),
    ("REMD\u00ad\nsimulations were run without restraints.", "confirm"),
    ("Simulations were run with\u00ad\nout REMD-\nmethods; not\u00ad\nably, it folded.", "confirm"),
    ("Simulations were run with\u00ad\nout REMD-\nmethods. No\u00ad\nnetheless, it folded.",
     "confirm"),
    ("REMDsimulations were run with\u00ad\nout restraints.", "confirm"),
])
def test_a_name_broken_softly_or_glued_to_a_word_asks(details, method):
    """A name read only where a soft hyphen at a line's end is joined, or a
    name of capitals glued to a word, may be a piece of another word, so
    the person decides; a break inside the name itself, or a hyphen before a
    word of the fixed list ("models", "trajectories"), reads it whole."""
    assert _method_said(details) == method


def test_a_long_list_of_places_is_cut_short():
    details = " ".join(f"Run {n} was a targeted MD." for n in range(20))
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "method": _stated("plain"), "method_details": _stated(details, details)})
    why = [c["why"] for c in _choices(plan, "method_details") if c["label"] == "needs_you"]
    assert len(why) == 1
    assert '"Run 11 was a targeted MD"; and 8 more places,' in why[0]
    assert "Run 12 was" not in why[0]


@pytest.mark.parametrize("membrane, state", [
    ("POPC bilayer, 128 and 64 per leaflet", "needs_you"),
    ("POPC bilayers, 2 x 128 lipids", "needs_you"),
    ("POPC bilayers of 128 lipids each", "needs_you"),
    ("POPC bilayer 96.5,32", "needs_you"),
    ("POPC bilayer (70.0, 30.0)", "needs_you"),
    ("POPC bilayer, 70.5 and 29.5", "needs_you"),
    ("POPC bilayer, 2 x 0 lipids", "needs_you"),
    ("POPC bilayer, 2 x 64 lipids, 128 total", "needs_you"),
    ("POPC bilayer, 1.5 nm of water", "ready"),
    ("POPC bilayer of 128 lipids, 64 per leaflet", "ready"),
    ("POPC bilayer of 128 lipids (64 per leaflet)", "ready"),
    ("POPC bilayer, a total of 128, 64 per leaflet", "ready"),
    ("POPC bilayer of 128 lipids, 6000 waters and 20 ions", "ready"),
    ("POPC bilayer of 128 lipids, 17 Na+ and 17 Cl- ions", "ready"),
    ("POPC bilayer of 128 lipids, 17 Na and 17 Cl", "needs_you"),
    ("POPC bilayer of 128 lipids, 5000 SPC waters", "ready"),
    ("POPC bilayer with 120 POPC and 8 SPC", "needs_you"),
    ("POPC bilayer of 120 lipids and 8 SPC molecules", "needs_you"),
    ("POPC bilayer, 64 per leaflet and 32 SPC", "needs_you"),
    ("POPC bilayer of 120 POPC and 8 OPC", "needs_you"),
    ("POPC bilayer, 75 and 25 mol", "needs_you"),
    ("POPC bilayer, 70 mol and 30 mol", "needs_you"),
    ("POPC bilayer with 50.0 waters per lipid", "ready"),
    ("POPC bilayer of 128 POPC, 64 per leaflet", "ready"),
    ("POPC bilayer, 64 per leaflet, total 128", "ready"),
    ("POPC bilayer, 64 per leaflet, total: 128", "ready"),
    ("POPC bilayer, 64 POPC-60 per leaflet", "needs_you"),
    ("POPC bilayer of 96 POPC-32 lipids", "needs_you"),
    ("POPC bilayer of 96 POPC\u201432", "needs_you"),
    ("POPC bilayer of 96 POPC -32", "needs_you"),
    ("POPC bilayer, 2 x 64 POPC-16", "needs_you"),
    ("POPC bilayer, 96 POPC and 32 Na+ (lipids)", "needs_you"),
    ("POPC bilayer, 96 POPC and 32 Na+ bilayer lipids", "needs_you"),
    ("POPC bilayer, 96 POPC and 32 Na+-POPC", "needs_you"),
    ("POPC bilayer, 64 per leaflet, 60 ions per leaflet", "needs_you"),
    ("POPC bilayer, 64 per leaflet and 60 sodium per leaflet", "needs_you"),
    ("POPC bilayer of 128 lipids, CHARMM-36", "ready"),
    ("POPC bilayer, 64 per leaflet, CHARMM-36", "ready"),
    ("POPC bilayer, 128 lipids, 40 waters per lipid", "ready"),
    ("POPC bilayer of 128 lipids with 20 Na+ ions and 20 Cl- ions", "ready"),
    ("POPC bilayer, 64 per leaflet and 60 na+ lipids per leaflet", "needs_you"),
    ("POPC bilayer of 96 POPC and 32 Na+ lipids", "needs_you"),
    ("POPC bilayer, 96 POPC and 32 sodium POPC", "needs_you"),
    ("POPC bilayer, 96 lipids and 32 Na+ salt lipids", "needs_you"),
    ("POPC bilayer of 128 lipids and 5000 water molecules", "ready"),
    ("POPC bilayer, 64 POPC per leaflet, 128 lipids", "ready"),
    ("POPC bilayer in a 7.5 x 7.5 x 10 nm box", "ready"),
    ("POPC bilayer, 0.15 M NaCl at 310.15 K", "ready"),
    ("POPC bilayer of 2 x 64 lipids", "ready"),
    ("POPC bilayer at 1.0 bar", "ready"),
])
def test_counts_of_waters_and_ions_are_not_lipids(membrane, state):
    """A count of waters or ions is not a count of lipids; twice a count per
    leaflet is plain only as the lipids' total; a share written with
    decimals, a "2 x 0" and a count beside more than one bilayer still need
    the person."""
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "membrane": _stated(membrane)})
    assert plan["state"] == state
    assert plan["config"]["setup"]["membrane"] == membrane[:4]


# -- 1608: names beside a soft hyphen, other spellings, counts after a dash --
@pytest.mark.parametrize("details, method", [
    ("These proper\u00ad\nties of the loop", "confirm"),
    ("THESE PROPER\u00ad\nTIES OF THE LOOP", "confirm"),
    ("We ran RE\u00ad\u00ad\nMD of the peptide", "confirm"),
    ("We ran RE\u00ad\u200b\nMD of the peptide", "confirm"),
    ("We ran RE\u2027\nMD of the peptide", "confirm"),
    ("We ran RE\u1806\nMD of the peptide", "confirm"),
    ("We ran RE\ufeff\u00ad \nMD of the peptide", "confirm"),
    ("REMD\u00ad\nsettings of the loop", "confirm"),
    ("REMD\u00ad\nsimulations with 24 replicas", "replica_exchange"),
    ("REMD-\nforce field runs", "replica_exchange"),
    ("We ran remdsimulations", "confirm"),
    ("Runs of gamdsimulations of the receptor", "confirm"),
    ("The qm/mmregion held the cofactor.", "confirm"),
    ("REXsimulations of the site", "confirm"),
    ("aMDsimulations of the site", "confirm"),
    ("An OBC2model of the solvent", "confirm"),
    ("\u13a1\u13ac\u13b7\u13a0 of the peptide", "replica_exchange"),
    ("\u04baREX of the peptide", "replica_exchange"),
    ("\u051cESTPA runs of the loop", "milestoning"),
    ("T\u04c0ES of the ligand", "free_energy"),
    ("Hamiltonian-\nREMD of the peptide", "replica_exchange"),
    ("pH-\nREMD of the peptide", "replica_exchange"),
    ("Data moved at 10 GB/s.", "plain"),
    ("MD with GB/S", "confirm"),
    ("We ran replica ex-\u00ad\nchange MD with 32 replicas.", "replica_exchange"),
    ("We ran Ga-\u200b\u00ad\nMD.", "accelerated"),
    ("We used fep\u2027calculations", "free_energy"),
    ("We ran REMD\u00ad\n(8 replicas)", "replica_exchange"),
    ("We ran \u00ad\nREMD with 8 replicas", "replica_exchange"),
    ("PROPER\u00ad\u2063\nTIES were computed", "confirm"),
    ("We ran \u13d2EMD", "replica_exchange"),
    ("We ran \u13d4ESTPA simulations", "milestoning"),
    ("H-\nTIES", "confirm"),
    ("PROPER\u00ad\n\u200bTIES were computed", "confirm"),
    ("PROPE\u0301R\u00ad\nTIES were computed", "confirm"),
    ("PROPER\u00ad\ufff9\nTIES were computed", "confirm"),
    ("We ran \u0301\u00ad\nREMD with 8 replicas", "replica_exchange"),
])
def test_a_name_beside_a_soft_hyphen_or_written_another_way(details, method):
    """A name just after a soft hyphen at a line's end may be the end of a
    word ("proper\u00ad\nties"), and one just before it the start of one,
    unless a word of the fixed list follows; however many soft hyphens or
    invisible characters stand there. A name glued to a word in any case
    asks; Cherokee and more Cyrillic capitals read as the Latin ones; a
    fixed word before a line's end hyphen ("Hamiltonian-") makes a whole
    exchange name of the one after it; "GB/s" after a number is a speed."""
    assert _method_said(details) == method


@pytest.mark.parametrize("membrane, state", [
    ("POPC bilayer, 64 per leaflet-60 POPC", "needs_you"),
    ("POPC bilayer, 64-POPC per leaflet and 60", "needs_you"),
    ("POPC bilayer, 64-POPC and 60", "needs_you"),
    ("POPC bilayer, 64 and 64 lipids per leaflet", "needs_you"),
    ("POPC bilayer of 64 lipids per leaflet (128 lipids)", "ready"),
    ("POPC bilayer, CHARMM-36 lipids, 64 per leaflet", "ready"),
    ("POPC-120 bilayer, 64 per leaflet", "needs_you"),
    ("POPC bilayer, 128-lipid patch, 60 per leaflet", "needs_you"),
    ("POPC bilayer, 64 per leaflet--60 POPC", "needs_you"),
    ("POPC bilayer, 64 per leaflet -60 POPC", "needs_you"),
    ("POPC bilayer, POPC-64 and -60 per leaflet", "needs_you"),
    ("POPC bilayer, with 64 64 lipids per leaflet", "needs_you"),
    ("a 128-POPC bilayer, 64 per leaflet", "ready"),
    ("POPC-128 bilayer, 64 per leaflet", "ready"),
    ("a bilayer of 128 1-palmitoyl-2-oleoyl-sn-glycero-3-phosphocholine (POPC) lipids", "ready"),
    ("POPC bilayer of 128 lipids, 64 lipids per leaflet", "ready"),
    ("POPC bilayer, 64 per leaflet, Lipid-60 POPC", "needs_you"),
    ("POPC bilayer, 64 per leaflet, CHARMM-60 POPC", "needs_you"),
    ("POPC bilayer, 64 per leaflet, 60-palmitoyl-oleoyl lipids", "needs_you"),
    ("POPC bilayer containing 128 lipids (64/leaflet, 60/leaflet)", "needs_you"),
    ("POPC bilayer containing 128 lipids (64/leaflet)", "ready"),
    ("POPC bilayer, TIP4P-2005 water, 64 per leaflet", "ready"),
    ("POPC bilayer, 64 lipids/leaflet (128 total)", "ready"),
    ("POPC bilayer, 64 lipids/leaflet (120 total)", "needs_you"),
])
def test_a_count_after_a_dash_is_a_count(membrane, state):
    """A count after a word's dash or before the lipid's dash is read, and
    one after a dash standing alone or doubled, but not one of a force
    field's fixed versions nor a place in a lipid's chemical name; a count of lipids per
    leaflet is the count per leaflet, unless it ends a list."""
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "membrane": _stated(membrane)})
    assert plan["state"] == state


# -- 1609: TIES in capitals, a hyphen inside a qualifier, a lipid's name written out --
@pytest.mark.parametrize("details, method", [
    ("The ties between the loops held.", "confirm"),
    ("Ties between the loops held.", "confirm"),
    ("We used TIES for the ligands.", "free_energy"),
    ("Plain MD with-out REMD.", "confirm"),
    ("Plain MD with\u2027out REMD.", "confirm"),
    ("\u24c5\u24c7\u24c4\u24c5\u24ba\u24c7\u00ad\nTIES were computed", "confirm"),
    ("PROPER\u00ad\n\U0001f143\U0001f138\U0001f134\U0001f142 were computed", "confirm"),
    ("We ran \U0001f141\U0001f134\u00ad\nMD of the peptide", "confirm"),
    ("We used Ties for the ligands.", "confirm"),
    ("We used \u1d1b\u026a\u1d07s for the ligands.", "confirm"),
    ("Relative binding was computed with ties_md.", "free_energy"),
    ("Relative binding was computed with TIESMD.", "free_energy"),
    ("THE TIES BETWEEN THE LOOPS HELD.", "confirm"),
    ("THE TIES BETWEEN DOMAINS AT pH 7 WERE ANALYSED.", "confirm"),
    ("Method: TIES", "free_energy"),
    ("TIES; 5 replicas per window", "free_energy"),
    ("TIES MD", "free_energy"),
    ("TIES in NAMD", "free_energy"),
    ("HYDROGEN-BOND TIES WERE ANALYSED.", "confirm"),
    ("We ran QM/MM MD of the N-O bond cleavage.", "qm_mm"),
    ("We ran REMD of the V-S bond.", "replica_exchange"),
])
def test_ties_is_a_name_only_in_capitals(details, method):
    """TIES decides the method only in capitals, in a sentence not all in
    capitals, or as a tool's name ("ties_md"); "ties" otherwise asks, as it
    may be a word; a qualifier with a hyphen inside it ("with-out") is read
    joined too, two letters on each side; a letter in a circle or a square
    before a soft hyphen is a letter."""
    assert _method_said(details) == method


@pytest.mark.parametrize("membrane, state", [
    ("DOPC bilayer of 128 lipids (1,2-dioleoyl-sn-glycero-3-phosphocholine)", "ready"),
    ("POPC bilayer, 128 lipids, 1-palmitoyl-2-oleoyl-sn-glycero-3-phosphocholine", "ready"),
    ("DOPC bilayer of 128 lipids, 64 per leaflet (1,2-dioleoyl-sn-glycero-3-phosphocholine)",
     "ready"),
    ("DOPC bilayer of 108 lipids and 20 phosphatidylcholine", "cannot_run"),
    ("DOPC bilayer of 128 lipids (1,2-dipalmitoyl-sn-glycero-3-phosphocholine)", "cannot_run"),
    ("DOPC bilayer of 128 lipids (20% phosphatidylcholine)", "cannot_run"),
    ("DOPC with 20 phosphatidylcholine", "cannot_run"),
    ("DOPC with 20 per leaflet phosphatidylcholine", "cannot_run"),
    ("POPC, 30 total phosphatidylcholine", "cannot_run"),
    ("DOPC with \u0663 phosphatidylcholine", "cannot_run"),
    ("POPC (1-palmitoyl-2-oleoyl-sn-glycero-3-phosphatidylcholine) bilayer", "ready"),
    ("DOPC (1,2-dioleoyl-3-sn-phosphatidylcholine)", "ready"),
    ("1-palmitoyl-2-oleoyl-3-sn-phosphatidylcholine (POPC)", "ready"),
    ("DOPC (1,2-dioleoyl-sn-3-phosphocholine)", "ready"),
    ("DOPC 3 phosphocholine", "cannot_run"),
])
def test_a_lipid_s_name_written_out_after_its_count_is_the_lipid(membrane, state):
    """The lipid's chemical name after a count of it, or after the words of a
    leaflet, is the lipid itself where it names the lipid's chains or stands
    apart ("(", ","); with other chains, a share, a count of it or another
    count beside it, it is another."""
    plan = _plan({"pdb_id": _stated("1UBQ"), "production": _stated(100.0),
                  "protein_forcefield": _stated("ff14SB"), "water_model": _stated("TIP3P"),
                  "membrane": _stated(membrane)})
    assert plan["state"] == state


# -- 1610: more look-alike letters, the first whole name read -----------------
@pytest.mark.parametrize("details, method", [
    ("We used \u0442\u0456\u0435\u0455 for the ligands.", "confirm"),
    ("We ran \u24ad\u24a0\u00ad\nMD of the peptide", "confirm"),
    ("We ran \U0001f181\U0001f174\u00ad\nMD of the peptide", "confirm"),
    ("We ran \U0001f181\U0001f174\U0001f17c\U0001f173 of the peptide", "replica_exchange"),
    ("We ran \U0001f161\U0001f154\U0001f15c\U0001f153 of the peptide", "replica_exchange"),
    ("We ran \u24ad\u24a0\u24a8\u249f of the peptide", "replica_exchange"),
    ("QM/MM-\nbased REMD of the site", "qm_mm"),
    ("REMD-\nbased QM/MM of the site", "replica_exchange"),
    ("REMD of the peptide; QM/MM-\nbased scoring later.", "replica_exchange"),
    ("Protocols: \u249cTI of ligand 1 and \u249dTI of ligand 2.", "free_energy"),
    ("TI\u249c was used.", "free_energy"),
    ("\U0001f170TI was used.", "free_energy"),
    ("\u249cMD of the apo form and \u249dMD of the holo form, 100 ns each.", "plain"),
    ("\U0001f14c\u00ad\nREMD was used.", "replica_exchange"),
    ("Struc-\ntures were equi-\nlibrated, mini-\nmized, re-\nlaxed, re-\nsolvated, re-\nheated "
     "and re-\nequi-\nlibrated. QM/MM-\nbased REMD was run.", "qm_mm"),
    ("We did not use the struc-\ntures were equi-\nlibrated and mini-\nmized, then re-\nheated, "
     "re-\nsolvated, re-\nlaxed, re-\nequi-\nlibrated, re-\nchecked, re-\nverified and re-\nsorted "
     "with alchemical-\nbased transforms; nothing else, so REMD was the method.",
     "replica_exchange"),
    ("TI\u249c\u249d was used.", "free_energy"),
    ("\U0001f170\U0001f171TI", "free_energy"),
    ("REMD\u00ad\n\u249c was used.", "replica_exchange"),
    ("We ran \u24ad\u24a0\u24a8\u249fsimulations of the peptide", "replica_exchange"),
    ("Pre-  pared sys-  tems were equi-  librated by mini-  mization; REMD and QM/MM-\nbased "
     "refinement were run.", "replica_exchange"),
    (" ".join(["sys\u2027\ntems"] * 6) + " were run; REMD and QM/MM-\nbased refinement were run.",
     "replica_exchange"),
])
def test_more_letters_that_look_latin_and_the_first_whole_name(details, method):
    """Cyrillic "\u0442" reads as the small capital T it looks like; letters
    in brackets and in black circles and squares read as letters two or more
    together (one alone is a list's mark), beside a soft hyphen too, and a
    sign in a square is no letter; a whole name the joined reading broke
    ("QM/MM-" at a line's end before "based") names the study where it comes
    first and nothing qualifies it in the joined reading."""
    assert _method_said(details) == method
