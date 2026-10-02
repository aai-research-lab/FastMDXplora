"""The methods say what an AI model wrote, as journals ask them to.

The manifest records how a study was written, phase by phase, and which AI
model drafted it; the Agent's conversations are kept in the study. The report said
none of it, so a methods section pasted from it was silent about an AI model
that had drafted the protocol.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmdxplora.report.context import PhaseContext
from fastmdxplora.report.methods import methods_paragraphs

MODEL = "example-provider/model-2026-01-15"


def _paragraph(written, conversations=0):
    text = methods_paragraphs(Path("."), {}, {}, written=written,
                              conversations=conversations)
    found = [p for p in text.split("\n\n") if not p.startswith("**Not recorded.**")]
    return found[0] if found else ""


def test_a_drafted_study_says_so_and_names_its_model():
    said = _paragraph({"study": "assisted", "phases": {"setup": "assisted"},
                       "checked": {"setup": True}, "model": MODEL}, conversations=1)
    assert said.startswith("**Use of an AI model.** The study's configuration was "
                           "drafted with an AI model and approved by a person before "
                           "it ran.")
    assert f"The AI model was recorded as {MODEL}." in said
    assert "Every setting was checked by the software's validator before it ran." in said
    assert ("The conversation with the FastMDXplora Agent about this study is kept with it, "
            "in `agent/conversations`.") in said


def test_an_unseen_study_and_a_phase_outside_the_schema_are_said():
    said = _paragraph({"study": "autonomous",
                       "phases": {"simulation": "autonomous", "analysis": "unvalidated"},
                       "checked": {"simulation": True, "analysis": False},
                       "departures": {"analysis": "unvalidated"}})
    assert "run without being shown to a person first, within a stated cost ceiling" in said
    assert ("Its analysis phase was written outside the configuration schema, so the "
            "software's validator did not check it.") in said
    assert "Which AI model was not recorded." in said
    assert "Every setting outside the analysis phase was checked" in said


def test_a_phase_outside_the_schema_alone_is_not_called_an_ai_model_s():
    said = _paragraph({"study": None, "phases": {"analysis": "unvalidated"},
                       "checked": {"analysis": False},
                       "departures": {"analysis": "unvalidated"}})
    assert said.startswith("**How the configuration was written.** The study's "
                           "configuration was written by a person.")
    assert "model" not in said


def test_an_ai_model_named_without_a_mode_is_not_given_one():
    said = _paragraph({"model": MODEL})
    assert said == (f"**Use of an AI model.** The configuration names an AI "
                    f"model, {MODEL}, without saying how it was used (`agent` was not set).")


def test_a_study_a_person_wrote_says_nothing_of_it():
    assert _paragraph(None) == ""
    assert _paragraph({"study": None, "phases": {}}) == ""


def test_the_report_reads_the_manifest_and_counts_conversations_that_say_anything(tmp_path):
    from fastmdxplora.report.document import _methods_section

    (tmp_path / "manifest.json").write_text(json.dumps({"phases": [], "agent": {
        "study": "assisted", "phases": {}, "checked": {}, "model": MODEL}}),
        encoding="utf-8")
    store = tmp_path / "agent" / "conversations"
    store.mkdir(parents=True)
    for name, entries in (("conv-a", [{"role": "user", "text": "chignolin"}]),
                          ("conv-b", [{"role": "agent", "text": "a plan"}]),
                          ("conv-c", [])):
        (store / f"{name}.json").write_text(json.dumps({"entries": entries}),
                                            encoding="utf-8")
    text = _methods_section(tmp_path, PhaseContext(simulation_present=True))
    assert f"The AI model was recorded as {MODEL}." in text
    assert "The conversations with the FastMDXplora Agent about this study are kept" in text
