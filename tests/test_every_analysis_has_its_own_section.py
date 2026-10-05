"""Every analysis has its own section on the Analysis page, under what it
studies.

Twelve of the thirty analyses were missing from the list of sections, so
their figures fell together into one "Other" section: end-to-end distance
beside lipid order, a radial distribution beside the B-factors. The list is
now one table of themes and sections that every registered analysis is in.
"""

from __future__ import annotations

from pathlib import Path

from fastmdxplora.analysis import available_analyses
from fastmdxplora.gui.report_dashboard import (
    ANALYSIS_SECTION_BY_FOLDER,
    ANALYSIS_THEMES,
    SECTION_ORDER,
    SECTION_THEME,
    analysis_sections_for,
)


def test_every_registered_analysis_has_a_section() -> None:
    missing = [name for name in available_analyses() if name not in ANALYSIS_SECTION_BY_FOLDER]
    assert missing == []


def test_each_section_is_named_once_and_has_a_theme() -> None:
    titles = [title for _, members in ANALYSIS_THEMES for _, title in members]
    assert len(titles) == len(set(titles))
    assert all(SECTION_THEME.get(title) for title in SECTION_ORDER)
    assert SECTION_ORDER[-1] == "Other"


def _figure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")


def test_two_analyses_once_left_out_are_two_sections(tmp_path: Path) -> None:
    _figure(tmp_path / "analysis" / "end_to_end" / "end_to_end.png")
    _figure(tmp_path / "analysis" / "rdf" / "rdf.png")
    _figure(tmp_path / "analysis" / "rmsd" / "rmsd.png")
    sections = analysis_sections_for(tmp_path)
    named = [(section.title, section.theme) for section in sections]
    assert named == [
        ("RMSD", "Structure and stability"),
        ("End-to-end distance", "Structure and stability"),
        ("Radial distribution function", "Contacts and solvent"),
    ]


def test_a_figure_of_an_unknown_folder_is_other(tmp_path: Path) -> None:
    _figure(tmp_path / "analysis" / "my_own" / "my_own.png")
    sections = analysis_sections_for(tmp_path)
    assert [(s.title, s.theme) for s in sections] == [("Other", "Other")]
