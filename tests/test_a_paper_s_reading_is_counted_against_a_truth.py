"""How well a paper's MD studies are read, counted against a truth made by hand.

Two readers read each registered paper apart; what both state with one value
is the truth, the rest contested and left out of the count. A reading is
counted field by field: the value used is the truth's, another one, none
where the truth states one, or one where the truth says the paper gives none.
The truth keeps values and where they are, never the paper's words.
"""

from __future__ import annotations

import json

import pytest

from fastmdxplora.paper.fields import BY_NAME
from fastmdxplora.validation import paper_reading
from fastmdxplora.validation.paper_reading import (PAPERS, TRUTH, main, match_studies, reconcile,
                                                   same_value, score)


def _stated(value, unit=""):
    return {"status": "stated", "value": value, "unit": unit, "where": "Methods",
            "quote": "the paper's words"}


def _study(sid, label, **fields):
    return {"id": sid, "label": label, "fields": fields, "claims": []}


@pytest.mark.parametrize("name, a, b, same", [
    ("temperature", 300, 300.0, True),
    ("temperature", 300, 310, False),
    ("timestep", 2, "2.0", True),
    ("protein_forcefield", "AMBER ff14SB", "ff14SB", True),
    ("protein_forcefield", "ff14SB", "CHARMM36m", False),
    ("water_model", "TIP3P", "tip3p", True),
    ("mutations", "L50A, K63R", ["K63R", "Leu50Ala"], True),
    ("mutations", "L50A", "L50G", False),
    ("pdb_id", "1UBQ", "PDB 1ubq", True),
    ("pdb_id", "1UBQ", "3PTB", False),
    ("neutralized", True, "yes", True),
])
def test_two_readings_of_one_field_are_one_value_or_not(name, a, b, same):
    assert same_value(name, a, b) is same


def test_studies_are_paired_by_what_tells_them_apart():
    first = [_study("S1", "wild type", pdb_id=_stated("1UBQ")),
             _study("S2", "L50A mutant", pdb_id=_stated("1UBQ"), mutations=_stated("L50A"))]
    second = [_study("S1", "the L50A mutant", pdb_id=_stated("1UBQ"),
                     mutations=_stated("L50A")),
              _study("S2", "wild-type ubiquitin", pdb_id=_stated("1UBQ"))]
    assert match_studies(first, second) == [(0, 1), (1, 0)]


def test_what_two_readers_agree_on_is_the_truth_and_the_rest_contested():
    a = {"key": "x", "doi": "10.9999/x", "studies": [_study(
        "S1", "wild type", temperature=_stated(300, "K"), timestep=_stated(2, "fs"),
        water_model=_stated("TIP3P"), replicas={"status": "in_si"})]}
    b = {"key": "x", "studies": [_study(
        "S1", "wild type", temperature=_stated(300, "K"), timestep=_stated(4, "fs"),
        replicas={"status": "in_si"})], "claims": []}
    a["studies"][0]["claims"] = [{"quantity": "rmsd", "value": 0.12, "unit": "nm",
                                  "quote": "0.12 nm"}]
    b["studies"][0]["claims"] = [{"quantity": "rmsd", "value": 0.120, "unit": "nm"}]
    truth = reconcile(a, b)
    study = truth["studies"][0]
    assert study["fields"]["temperature"]["value"] == 300
    assert study["fields"]["replicas"] == {"status": "in_si"}
    assert set(study["contested"]) == {"timestep", "water_model"}
    assert study["claims"][0]["value"] == 0.12
    assert "quote" not in json.dumps(truth)


def test_a_reading_is_counted_against_the_agreed_fields_only():
    truth = {"key": "x", "studies": [{
        "id": "S1/S1", "label": "wild type", "contested": ["timestep"],
        "fields": {"temperature": {"status": "stated", "value": 300},
                   "water_model": {"status": "stated", "value": "TIP3P"},
                   "pressure": {"status": "stated", "value": 1.0},
                   "system": {"status": "stated", "value": "ubiquitin"}},
        "claims": [{"quantity": "rmsd", "value": 0.12}]}]}
    reading = {"studies": [_study(
        "S1", "wild type", temperature=_stated(300), water_model=_stated("TIP4P-Ew"),
        timestep=_stated(9), thermostat=_stated("Langevin"),
        system=_stated("something else entirely"))]}
    reading["studies"][0]["claims"] = [{"quantity": "rmsd", "value": 0.12, "status": "stated"}]
    counted = score(truth, reading)
    fields = counted["fields"]
    assert (fields["correct"], fields["wrong"], fields["missed"], fields["invented"]) == (1, 1, 1, 1)
    assert "timestep" not in counted["by_field"]  # contested
    assert "system" not in counted["by_field"]  # a description
    assert counted["by_field"]["thermostat"]["invented"] == 1
    assert (counted["claims_agreed"], counted["claims_found"]) == (1, 1)


def test_every_registered_paper_has_its_truth_and_the_truth_keeps_no_words():
    assert len(PAPERS) == 20 and len({paper["key"] for paper in PAPERS}) == 20
    for paper in PAPERS:
        truth = json.loads((TRUTH / f"{paper['key']}.json").read_text(encoding="utf-8"))
        assert truth["studies"], paper["key"]
        for study in truth["studies"]:
            assert set(study["fields"]) <= set(BY_NAME), paper["key"]
            assert set(study["contested"]) <= set(BY_NAME), paper["key"]
            for record in study["fields"].values():
                assert "quote" not in record
    assert sorted(path.stem for path in TRUTH.glob("*.json")) == sorted(p["key"] for p in PAPERS)


def test_the_harness_reads_each_paper_and_writes_the_counts(tmp_path, monkeypatch):
    truth = json.loads((TRUTH / "fischer_2024.json").read_text(encoding="utf-8"))
    reading = {"model": "test/model", "studies": [
        {"id": study["id"], "label": study["label"], "claims": [],
         "fields": {name: dict(record) for name, record in study["fields"].items()}}
        for study in truth["studies"]]}
    asked = []

    def studies_in(source, use_kept=True):
        asked.append(source)
        return None, reading

    monkeypatch.setattr("fastmdxplora.paper.studies.studies_in", studies_in)
    out = tmp_path / "counts.json"
    assert main(["--papers", "fischer_2024", "--file", "fischer_2024=here.pdf",
                 "--out", str(out)]) == 0
    assert asked == ["here.pdf"]
    totals = json.loads(out.read_text(encoding="utf-8"))["totals"]
    assert totals["wrong"] == totals["missed"] == totals["invented"] == 0
    assert totals["correct"] > 0


def test_the_paper_not_open_is_tried_by_its_doi_first(tmp_path, monkeypatch):
    from fastmdxplora.paper import PaperRefused

    def refuse(source, *args, **kwargs):
        raise PaperRefused("not open", code="environment.paper.not_open")

    monkeypatch.setattr("fastmdxplora.paper.fetch.open_paper", refuse)
    monkeypatch.setattr("fastmdxplora.paper.studies.studies_in",
                        lambda source, use_kept=True: (None, {"studies": []}))
    out = tmp_path / "counts.json"
    assert main(["--papers", "buch_2011", "--file", "buch_2011=buch.pdf", "--out", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))["results"][0]
    assert result["refused_by_doi"] == "environment.paper.not_open"


def test_the_truth_is_made_again_from_two_readers_folders(tmp_path, capsys):
    first, second, out = tmp_path / "a", tmp_path / "b", tmp_path / "truth"
    first.mkdir()
    second.mkdir()
    one = {"key": "x", "studies": [_study("S1", "wild type", temperature=_stated(300))]}
    (first / "x.json").write_text(json.dumps(one), encoding="utf-8")
    (second / "x.json").write_text(json.dumps(one), encoding="utf-8")
    assert paper_reading.main(["--reconcile", str(first), str(second), str(out)]) == 0
    assert "1 studies matched" in capsys.readouterr().out
    assert json.loads((out / "x.json").read_text())["studies"][0]["fields"]["temperature"][
        "value"] == 300
