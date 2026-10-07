"""What `--rerun` set aside is listed apart from what replaced it.

`fastmdx analyze --rerun` keeps the analysis and report it replaces in
`previous/<phase>`. The Files page put those 106 files in Analysis data and
Figures beside the new ones, two of every title and nothing to tell them
apart.
"""

from __future__ import annotations

from fastmdxplora.gui.server import ARTIFACT_GROUPS, _artifact_label


def test_a_set_aside_file_is_in_a_group_of_its_own():
    label, group = _artifact_label("previous/analysis/rmsd/rmsd.png")
    assert group == "previous"
    assert label == "rmsd: figure (PNG), set aside"
    assert _artifact_label("previous/report/report.md") == (
        "Report (Markdown), set aside", "previous")
    # The new ones are where they were.
    assert _artifact_label("analysis/rmsd/rmsd.png")[1] == "figures"


def test_a_window_run_again_is_set_aside_too():
    label, group = _artifact_label(
        "superseded/window_03-20261001T120000Z/simulation/production.dcd")
    assert group == "previous"
    assert label == "Production trajectory, of a window run again"


def test_the_group_is_listed_last_and_folded():
    assert ARTIFACT_GROUPS[-1] == ("previous", "Set aside by --rerun")
    from fastmdxplora.gui.files_page import FOLDED
    from fastmdxplora.study_files import place

    # The Files page puts them in a section of their own, folded.
    assert place("previous/analysis/rmsd/rmsd.png")[0] == "previous" and "previous" in FOLDED
