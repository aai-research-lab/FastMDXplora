"""FastMDXplora reads a paper; the AI model is named only where it is chosen.

User, 10-10: "all the asking AI model needs to go ...... as previously
stated FastMDXplora or FastMDXplora Agent is the agency here ...it the thing
doing an action not the AI model". The terminal said "Asking the AI model
which MD studies the paper reports..." and "Read by
anthropic/claude-opus-5-5"; the builder's card said "Your AI model is asked
a few times"; a setting not used said "The AI model gave words for it the
paper does not contain". The AI model is now named where it is chosen
(`fastmdx agent model`) and in the record of who read the paper
(`paper.read_by`, FastMDXplora with the AI model, or with the AI app
that gave the reading through `fastmdx mcp`), as on the Agent's page.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest
import yaml

from fastmdxplora.paper import PaperRefused
from fastmdxplora.paper.extract import check_reading, read_studies
from fastmdxplora.paper.studies import plans_for, studies_in, write_configs
from fastmdxplora.paper.text import read_paper

from tests._a_paper import CLAIMS, PROTOCOL, STUDIES, jats, scripted

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _own_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def paper_file(tmp_path):
    path = tmp_path / "paper.xml"
    path.write_bytes(jats())
    return path


def test_what_reading_a_paper_says_is_what_fastmdxplora_does(paper_file):
    said: list[str] = []
    read_studies(read_paper(paper_file), scripted(), model="test/model",
                 said=said.append, use_kept=False)
    assert said == ["Reading which MD studies the paper reports...",
                    "Reading the settings of protocol P1...",
                    "Reading the results each study reports..."]


def test_the_command_says_fastmdxplora_read_the_paper(paper_file, tmp_path, monkeypatch,
                                                      capsys):
    from fastmdxplora.paper import command, studies

    monkeypatch.setattr(studies, "the_ai_model", lambda: (scripted(), "test/model"))
    args = argparse.Namespace(paper=str(paper_file), paper_si=None, paper_studies="all",
                              paper_until_determined=False,
                              config_file=str(tmp_path / "study.yml"), force=False)
    assert command.config_from_paper(args) == 0
    out, err = capsys.readouterr()
    assert "Read by FastMDXplora; every value checked against the paper's own words." in out
    assert "AI model" not in out + err and "Asking" not in out + err
    assert "test/model" not in out


def test_the_record_says_who_read_the_paper_and_with_what(paper_file, tmp_path):
    _paper, reading = studies_in(str(paper_file), complete=scripted(), model="test/model")
    written = write_configs(plans_for(reading), tmp_path / "ubq.yml")
    text = written[0]["path"].read_text(encoding="utf-8")
    assert ", as read by FastMDXplora with test/model." in text.splitlines()[1]
    assert yaml.safe_load(text)["paper"]["read_by"] == "FastMDXplora with test/model"


def test_a_reading_an_ai_app_gave_is_recorded_as_fastmdxplora_s_with_the_app(paper_file):
    from fastmdxplora.paper.tools import checked_said

    raw = {"title": "T", "studies": STUDIES, "protocols": [{"id": "P1"}],
           "protocol_fields": {"P1": PROTOCOL}, "claims": CLAIMS}
    checked = checked_said(read_paper(paper_file), json.dumps(raw), model="Test App")
    assert "read_by: FastMDXplora with Test App" in checked
    assert "as read by FastMDXplora with Test App." in checked


def test_a_reading_kept_from_before_is_recorded_as_fastmdxplora_s(paper_file):
    raw = {"title": "T", "studies": STUDIES, "protocols": [{"id": "P1"}],
           "protocol_fields": {"P1": PROTOCOL}, "claims": CLAIMS}
    reading = check_reading(read_paper(paper_file), raw, model="test/model")
    assert reading["read_by"] == "FastMDXplora with test/model"
    del reading["read_by"]  # as a reading kept before the record said who
    assert plans_for(reading)[0]["config"]["paper"]["read_by"] == \
        "FastMDXplora with test/model"
    del reading["model"]  # and one read with no AI model named
    assert plans_for(reading)[0]["config"]["paper"]["read_by"] == "FastMDXplora"


def test_a_setting_not_used_says_why_without_an_ai_model():
    from fastmdxplora.paper.mapping import _UNCHECKED

    assert all("AI model" not in said for said in _UNCHECKED.values())


def test_an_answer_that_cannot_be_read_says_fastmdxplora_could_not_read_it(paper_file):
    with pytest.raises(PaperRefused) as refused:
        read_studies(read_paper(paper_file), lambda prompt: "I could not find any.",
                     model="x", use_kept=False)
    said = str(refused.value)
    assert said.startswith("FastMDXplora could not read the paper:")
    assert said.count("AI model") == 1 and "another AI model with `fastmdx agent model`" in said


def test_the_builder_s_card_names_no_ai_model():
    script = (ROOT / "src" / "fastmdxplora" / "gui" / "static" / "paper-studies.js"
              ).read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    strings = re.findall(r'"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`', code)
    assert not [s for s in strings if "AI model" in s or "Asking" in s]
    assert "AI model" not in code and "Asking" not in code
    assert any("read by FastMDXplora" in s for s in strings)


def test_the_builder_s_card_note_says_fastmdxplora_lists_the_studies():
    page = (ROOT / "src" / "fastmdxplora" / "gui" / "templates" / "dashboard.html"
            ).read_text(encoding="utf-8")
    card = page[page.index('id="run-paper-card"'):]
    card = card[:card.index("</form>")]
    assert "AI model" not in card
    assert "FastMDXplora lists its MD studies;" in card


def test_the_command_s_help_says_fastmdxplora_reads_the_paper(capsys):
    from fastmdxplora.cli.main import main

    with pytest.raises(SystemExit):
        main(["config", "--help"])
    told = " ".join(capsys.readouterr().out.split())
    assert "FastMDXplora lists its studies, with the AI model chosen with" in told
    assert "The AI model chosen" not in told


def test_no_ai_model_chosen_says_reading_a_paper_needs_one(tmp_path):
    from fastmdxplora.agent import models
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError) as refused:
        models.completion_for(None, path=tmp_path / "none.json")
    assert "the Agent or have it read a paper" in str(refused.value)


def test_a_hosted_gui_s_refusal_names_no_ai_model():
    from fastmdxplora.gui.paper_view import read_paper_studies

    said = read_paper_studies({"source": "10.1/x"}, hosted=True)
    assert not said["ok"] and "AI model" not in said["error"]


def test_the_agent_is_told_fastmdxplora_reads_the_paper():
    from fastmdxplora.agent.tools import _TOOLS

    told = _TOOLS["studies_in_paper"][1]
    assert "read by FastMDXplora" in told and "AI model" not in told


def test_the_papers_page_names_the_ai_model_only_as_what_fastmdxplora_reads_with():
    page = " ".join((ROOT / "docs" / "papers.md").read_text(encoding="utf-8").split())
    named = re.findall(r".{0,40}AI model.{0,40}", page)
    assert named
    for place in named:
        assert re.search(r"with the AI model|the AI model it was read with", place), place
