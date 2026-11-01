"""What the Agent's answers count is counted once, as the Overview counts it.

Review of `image-said` (11-01): with 1 of 3 means noted the yes read "long
enough to determine its means, though they may be biased" above "1 of its 3
means may be biased"; a run of one mean read "Its means are determined"
beside "Its mean may be biased"; the answers said "as said beside it" where
no mean's note is beside them; the records text the Agent reads put a
mean's note before its verdict, which the chip under an answer puts after
it; and a run whose Overview counted one mean was told "All 4 observables
assessed had equilibrated, and each mean is determined" (the report's
count), then asked for more production for its total energy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_a_noted_mean_is_said_beside_every_yes import ALL, _unnoted
from tests.test_the_image_note_reaches_every_page import study  # noqa: F401


@pytest.fixture
def answers(monkeypatch):
    from fastmdxplora.gui import records_answer

    monkeypatch.setattr(records_answer, "_supports", lambda base: ALL)
    return lambda root, question: records_answer.answer_from_the_records(root, question)


def test_the_qualified_yes_counts_the_noted_means(study, answers) -> None:  # noqa: F811
    _unnoted(study, ("sasa",))
    said = answers(study, "long_enough")
    assert ("By its records, long enough to determine its means, though 1 of them may be "
            "biased by the solute's own image") in said
    assert "though they may be" not in said


def test_a_run_of_one_mean_is_said_its_mean(tmp_path, answers) -> None:
    from tests.test_a_study_of_runs_is_shown_as_one import _recorded
    from tests.test_the_image_note_reaches_every_page import _in_contact

    from fastmdxplora.gui.overview_view import overview_payload

    root = _in_contact(tmp_path, _recorded(0.15), _recorded(1.2))
    for path in sorted((root / "analysis" / "rg").iterdir()):
        path.unlink()
    (root / "analysis" / "rg").rmdir()
    assert overview_payload(root)["means"] == 1
    said = answers(root, "strengthen")
    assert "Its mean is determined, so a longer run of this one adds precision" in said
    assert "Its means are" not in said


def test_the_answers_say_where_each_note_is(study, answers) -> None:  # noqa: F811
    from fastmdxplora.gui.overview_view import overview_payload

    for question in ("long_enough", "strengthen"):
        said = answers(study, question)
        assert "as said beside" not in said, question
        assert "biased by the solute's own image, as the Overview says beside each; " in said
    # On the Overview each tile is beside its lead.
    assert ", as said beside each; " in overview_payload(study)["lead"]["said"]


def test_the_records_text_puts_the_note_after_the_verdict(study) -> None:  # noqa: F811
    from fastmdxplora.gui.agent_panel import _results_summary
    from fastmdxplora.image_note import note_of, said_short

    path = study / "analysis" / "rmsd" / "options.json"
    record = json.loads(path.read_text())
    record["findings"]["mean"]["not_a_measurement"] = "Too few independent samples."
    path.write_text(json.dumps(record))
    noted = said_short(note_of(record["findings"]["mean"]))
    line = next(x for x in _results_summary(study, "rmsd").splitlines()
                if x.strip().startswith("rmsd:"))
    assert f" -- not determined: Too few independent samples. -- {noted}" in line


def _one_mean_and_the_production_s(root: Path) -> dict:
    """The Overview of a run with an RMSD mean determined and the
    production's four thermodynamic means, one not (`clean-rmsd-only`)."""
    return {"means": 1, "determined": 1, "noted": 0, "not_read": 0,
            "thermodynamics": {"means": {
                "potential_energy": {"mean": -19798.3, "determined": True},
                "total_energy": {"mean": -16290.0, "determined": False},
                "temperature": {"mean": 298.0, "determined": True},
                "density": {"mean": 0.9989, "determined": True}}}}


def test_the_answers_count_the_means_as_the_overview_does(tmp_path, monkeypatch) -> None:
    from tests.test_a_run_s_overview_leads_with_what_it_needs import _a_run
    from tests.test_a_study_of_runs_is_shown_as_one import _recorded

    from fastmdxplora.gui import overview_view, records_answer
    from fastmdxplora.report import document

    root = _a_run(tmp_path, _recorded(0.15), _recorded(1.2))
    monkeypatch.setattr(overview_view, "overview_payload", _one_mean_and_the_production_s)
    # The report's own count, of the observables its Convergence table holds.
    monkeypatch.setattr(document, "_what_the_run_supports", lambda base: (
        "All 4 observables assessed had equilibrated, and each mean is determined."))
    monkeypatch.setattr(records_answer, "_ask", lambda base: (
        "Total energy withheld its mean for want of sampling: 2 ps more production.", ""))
    counted = ("Its mean is determined; 1 of the production's 4 thermodynamic means is "
               "not determined.")
    for question in ("long_enough", "found"):
        said = records_answer.answer_from_the_records(root, question)
        assert counted in said, question
        assert "observables" not in said, question
    # All determined is a yes, counted the same way.
    monkeypatch.setattr(overview_view, "overview_payload", lambda base: {
        **_one_mean_and_the_production_s(base), "thermodynamics": {"means": {
            "density": {"mean": 0.9989, "determined": True},
            "temperature": {"mean": 298.0, "determined": True}}}})
    monkeypatch.setattr(records_answer, "_ask", lambda base: None)
    said = records_answer.answer_from_the_records(root, "long_enough")
    assert ("By its records, yes. Its mean is determined; the production's 2 thermodynamic "
            "means are determined.") in said
