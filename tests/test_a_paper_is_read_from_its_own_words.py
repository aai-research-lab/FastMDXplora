"""A paper's words are read as parts that can be pointed at, and an AI
model's quote is the paper's only where the paper has it.

What a paper states reaches a config only with the words it was read from,
found in the paper here, and with its number read from them here: a quote
the paper does not contain, or one that does not hold the value it was
given for, is not used. PDFs break words over lines and spell ``Å`` as
``A˚``, so the words are compared with spacing, case, accents and
hyphenation set aside.
"""

from __future__ import annotations

import zipfile

import pytest

from fastmdxplora.paper import PaperRefused
from fastmdxplora.paper.quotes import QuoteIndex
from fastmdxplora.paper.text import PaperText, Part, jats_parts, read_paper
from fastmdxplora.paper.values import candidates, read_value

from tests._a_paper import DOI, TITLE, jats, pdf


def test_a_jats_article_is_read_section_by_section_with_its_tables(tmp_path):
    path = tmp_path / "paper.xml"
    path.write_bytes(jats())
    paper = read_paper(path)
    labels = [part.label for part in paper.parts]
    assert paper.title == TITLE and paper.doi == DOI
    assert "creativecommons.org/licenses/by/4.0" in paper.licence
    assert "Methods: Molecular dynamics simulations" in labels
    assert "Table 1" in labels
    table = next(part for part in paper.parts if part.label == "Table 1")
    assert "Wild type | 0.12 ± 0.01 | 1.18 ± 0.02" in table.text
    # The references are not read.
    assert not any("Someone A" in part.text for part in paper.parts)


def test_an_article_defining_its_own_entities_is_not_parsed():
    with pytest.raises(PaperRefused):
        jats_parts(b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><article/>')


def test_a_pdf_is_read_page_by_page(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(pdf([["Methods", "The system was simulated at 310 K for 200 ns."],
                          ["Results", "The RMSD was low."]]))
    paper = read_paper(path)
    assert [part.label for part in paper.parts] == ["p. 1", "p. 2"]
    found = QuoteIndex(paper).find("simulated at 310 K for 200 ns")
    assert found is not None and found.label == "p. 1"


def test_supporting_information_is_said_as_such(tmp_path):
    path = tmp_path / "si.pdf"
    path.write_bytes(pdf([["Table S1. Each system was run five times."]]))
    paper = read_paper(path, kind="si")
    assert paper.parts[0].label == "SI p. 1" and paper.parts[0].kind == "si"


def test_a_word_file_is_read(tmp_path):
    path = tmp_path / "si.docx"
    body = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Each run lasted </w:t></w:r><w:r><w:t>500 ns.</w:t></w:r></w:p>"
            "</w:body></w:document>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", body)
    paper = read_paper(path, kind="si")
    assert "Each run lasted 500 ns." in paper.parts[0].text


def test_a_file_with_no_text_is_refused(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(pdf([[]]))
    with pytest.raises(PaperRefused, match="no text"):
        read_paper(path)


def test_a_file_that_is_not_a_paper_is_refused(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(b"\x89PNG")
    with pytest.raises(PaperRefused, match="not a file a paper is read from"):
        read_paper(path)


def _paper(*texts: str) -> PaperText:
    return PaperText(parts=[Part(f"p. {n}", text) for n, text in enumerate(texts, start=1)])


def test_a_quote_is_found_across_line_breaks_hyphens_and_accents():
    index = QuoteIndex(_paper("leaving at least 10 A˚ between the pro-\ntein and the box"))
    found = index.find("leaving at least 10 Å between the protein and the box")
    assert found is not None and found.label == "p. 1"
    assert "pro-\ntein" in found.words


def test_a_quote_the_paper_does_not_contain_is_not_found():
    index = QuoteIndex(_paper("The system was simulated at 300 K."))
    assert index.find("The system was simulated at 310 K.") is None
    assert index.find("simulated with the CHARMM36m force field") is None


def test_a_quote_too_short_to_place_is_not_found():
    index = QuoteIndex(_paper("at 300 K"))
    assert index.find("300 K") is None


def test_a_quote_with_an_ellipsis_is_found_when_each_piece_is_near_the_last():
    index = QuoteIndex(_paper("The protein was solvated in TIP3P water and then, after "
                              "minimization, equilibrated for 1 ns."))
    assert index.find("solvated in TIP3P water ... equilibrated for 1 ns") is not None
    assert index.find("equilibrated for 1 ns ... solvated in TIP3P water") is None


@pytest.mark.parametrize("words, kind, expected", [
    ("at 310 K", "temperature", [310.0]),
    ("at 37 °C", "temperature", [310.15]),
    ("for 1 μs each", "time", [1000.0]),
    ("a 2-fs time step", "timestep", [2.0]),
    ("a cutoff of 10 Å", "length", [1.0]),
    ("switched from 1.0 nm to 1.2 nm", "length", [1.0, 1.2]),
    ("150 mM NaCl", "concentration", [0.15]),
    ("1 atm", "pressure", [1.01325]),
    ("in triplicate", "count", [3.0]),
    ("300.0 (POPS), 303.15 (POPC), and 323.15 K (DPPC)", "temperature", [300.0, 303.15, 323.15]),
    ("no ion was added in the DPPC systems", "concentration", [0.0]),
    ("no ions other than counterions; 0.15 M NaCl", "concentration", [0.15]),
])
def test_a_number_is_read_in_this_software_s_unit(words, kind, expected):
    assert candidates(words, kind) == expected


def test_a_value_the_words_do_not_hold_is_not_read():
    # Three replicas of 100 ns: 100 is there, the total of 300 is not.
    assert read_value("three independent 100-ns simulations", "time", 100, "ns") == 100.0
    assert read_value("three independent 100-ns simulations", "time", 300, "ns") is None
    # A unit misread is a value not held.
    assert read_value("a cutoff of 10 Å", "length", 10, "nm") is None
    assert read_value("a cutoff of 10 Å", "length", 10, "Å") == 1.0
    # Several values and none said: none chosen.
    assert read_value("1 ns of NVT and 2 ns of NPT", "time") is None
