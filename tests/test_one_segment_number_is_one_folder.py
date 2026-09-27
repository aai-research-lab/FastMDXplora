"""A segment number names one folder, and each folder is read as it is named.

The join took a segment's number from its folder name, so `segment-1` and
`segment-001` were both segment one: the join held whichever came later in
the listing and the continuation resumed from whichever the maximum met
first. Two readers also rebuilt a segment's folder from its number as
`segment-NNN`, so a folder named `segment-1` was counted as a segment and
then never read: its production was left out of what was done, and a
killed one's frames were not trimmed. Two folders with one number are now
refused with both named, and every reader uses the folder that was found.
"""

from __future__ import annotations

import shutil

import pytest

from fastmdxplora.analysis.joining import survey_segments
from fastmdxplora.refusals import StudyError
from fastmdxplora.simulation.resume import production_done_ns
from tests.test_a_study_is_extended_in_place import _study
from tests.test_an_extended_study_goes_on_from_its_last_segment import _segment


def test_two_folders_with_one_number_are_refused() -> None:
    root = _study()
    first = _segment(root, 1)
    shutil.copytree(first, root / "segment-1")
    with pytest.raises(StudyError) as refused:
        survey_segments(root)
    assert refused.value.code == "simulation.resume.segment_named_twice"
    assert "segment-001 and segment-1" in str(refused.value)


def test_a_continuation_refuses_before_it_runs_anything() -> None:
    from fastmdxplora.simulation.resume import extension_of

    root = _study(done_steps=50_000, finished=False)
    first = _segment(root, 1)
    shutil.copytree(first, root / "segment-1")
    with pytest.raises(StudyError, match="both segment 1"):
        extension_of(root, more_ns=0.1)


def test_a_folder_named_without_padding_is_counted() -> None:
    """0.5 ns from the study's own run and 0.1 from its one extension,
    whatever the extension's folder is called."""
    padded = _study()
    _segment(padded, 1)
    unpadded = _study()
    shutil.move(str(_segment(unpadded, 1)), str(unpadded / "segment-1"))
    assert production_done_ns(padded) == pytest.approx(0.6)
    assert production_done_ns(unpadded) == pytest.approx(0.6)
