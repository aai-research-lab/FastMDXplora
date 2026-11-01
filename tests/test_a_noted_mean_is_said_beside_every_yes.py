"""A run whose means are all determined and noted is told so beside "yes".

The solute came within the cutoff of its own image in some frames, not in
contact with it in every one (C13's stD2): each mean carried its note beside
its verdict, and the Agent answered "By its records, yes." and "Then
replicas.", and the Overview led "All 3 means are determined.", with no word
of the note; only a run in contact in every frame was told. Where any mean
is noted, how many is said once, with what lifts it (a larger box, named by
its setting, the solute kept farther from its image than the run's cutoff),
and replicas are advised in a larger box.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_the_image_note_reaches_every_page import study  # noqa: F401

#: Said once of the study's three means, each noted (the Agent's Markdown).
NOTED = ("Each of its 3 means may be biased by the solute's own image, as said beside "
         "each; a larger box (`setup.solvent_padding_nm`), in a new study, one that keeps "
         "the solute farther from its image than the run's 1 nm cutoff, lifts it.")

#: The report's line for means all determined.
ALL = "All 2 of 2 observables assessed, 2 had equilibrated, their means determined."


def _unnoted(root: Path, names: tuple[str, ...] = ("rmsd", "sasa")) -> None:
    """The same study with the means of ``names`` not noted."""
    for name in names:
        path = root / "analysis" / name / "options.json"
        record = json.loads(path.read_text())
        findings = record["findings"]
        findings.pop("solute_image", None)
        for mean in findings.values():
            if isinstance(mean, dict):
                mean.pop("solute_image", None)
        path.write_text(json.dumps(record))


@pytest.fixture
def answers(monkeypatch):
    from fastmdxplora.gui import records_answer

    monkeypatch.setattr(records_answer, "_supports", lambda base: ALL)
    return lambda root, question: records_answer.answer_from_the_records(root, question)


class TestTheAgent:
    def test_long_enough_is_not_an_unqualified_yes(self, study, answers):  # noqa: F811
        said = answers(study, "long_enough")
        assert "By its records, yes." not in said
        assert ("By its records, long enough to determine its means, though they may be "
                "biased by the solute's own image, which no length of run lifts. " + ALL) in said
        assert f"\n\n{NOTED}\n\n" in said
        # The seed sweep it advises is in the same box: replicas in a larger.
        assert "\n\nThen replicas in a larger box. A single run's error" in said

    def test_strengthen_says_it_and_a_larger_box(self, study, answers):  # noqa: F811
        said = answers(study, "strengthen")
        assert ("Its means are determined, so a longer run of this one adds precision but "
                f"not a check.\n\n{NOTED}\n\nThen replicas in a larger box. ") in said

    def test_one_of_three_noted_is_counted_and_none_is_a_plain_yes(self, study,  # noqa: F811
                                                                   answers):
        _unnoted(study, ("sasa",))
        for question in ("long_enough", "strengthen"):
            said = answers(study, question)
            assert ("\n\n1 of its 3 means may be biased by the solute's own image, as said "
                    "beside it; a larger box") in said, question
            assert "Then replicas in a larger box. " in said, question
        _unnoted(study)
        enough, strengthen = (answers(study, q) for q in ("long_enough", "strengthen"))
        assert f"By its records, yes. {ALL}\n\nA single run's error" in enough
        assert "larger box" not in enough + strengthen
        assert "\n\nThen replicas. A single run's error" in strengthen


def test_the_overview_s_lead_says_it(study) -> None:  # noqa: F811
    from fastmdxplora.gui.overview_view import overview_payload

    payload = overview_payload(study)
    # Plain text on the page: no code marks.
    assert payload["lead"]["said"] == "All 3 means are determined. " + NOTED.replace("`", "")
    assert (payload["means"], payload["determined"], payload.get("noted")) == (3, 3, 3)


def test_what_lifts_it_names_the_setting_and_how_large() -> None:
    """The advice of "a larger box, in a new study" named no setting and no
    size (C12 L3, C13 L1)."""
    from fastmdxplora.image_note import determined_said, longer_run_said

    finding = {"frames": 100, "frames_under_close": 100, "cutoff_nm": 1.2}
    for said in (longer_run_said(finding), determined_said(finding)):
        assert said.endswith(
            "a larger box (`setup.solvent_padding_nm`), in a new study, one that keeps the "
            "solute farther from its image than the run's 1.2 nm cutoff, is what lifts it.")


def test_a_run_of_one_mean_in_contact_is_said_its_mean(tmp_path, monkeypatch) -> None:
    """The Agent gave `determined_said` no count, so a run of one mean read
    "Its means may still be biased" (C12 L5)."""
    from tests.test_a_study_of_runs_is_shown_as_one import _recorded
    from tests.test_the_image_note_reaches_every_page import _in_contact

    from fastmdxplora.gui import records_answer
    from fastmdxplora.gui.overview_view import overview_payload

    root = _in_contact(tmp_path, _recorded(0.15), _recorded(1.2))
    for folder in ("rg",):
        for path in sorted((root / "analysis" / folder).iterdir()):
            path.unlink()
        (root / "analysis" / folder).rmdir()
    assert overview_payload(root)["means"] == 1
    monkeypatch.setattr(records_answer, "_supports", lambda base: ALL)
    said = records_answer.answer_from_the_records(root, "long_enough")
    assert "\n\nIts mean may still be biased by the solute's own image" in said
    assert "Its means may" not in said
    assert said.count("By its records, long enough to determine its mean, though it may "
                      "be biased") == 1
