"""A study that has plotted no figure shows its backbone on its card.

All studies gave such a study a grey box with its name. Its card now shows
the protein's Cα trace, rendered from the structure the Viewer would render
first, seen face on, coloured from the N terminus to the C terminus; the
name only where there is no structure with a backbone either.
"""

from __future__ import annotations

import math
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest

from tests.test_the_workspace_says_its_studies import PIXEL, _replicas, _study


def _atom(serial: int, name: str, resname: str, chain: str, resseq: int, xyz, *,
          altloc: str = " ", record: str = "ATOM  ", element: str = "C") -> str:
    x, y, z = xyz
    # A one-letter element's atom name starts in column 14 (" CA "); a
    # two-letter element's in column 13 ("CA  ", calcium).
    name = (" " + name).ljust(4) if len(element.strip()) == 1 else name.ljust(4)
    return (f"{record}{serial:5d} {name}{altloc}{resname:>3s} {chain}{resseq:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {element:>2s}\n")


def _helix(count: int, chain: str, start: int, shift=(0.0, 0.0, 0.0)) -> list:
    """Cα positions of an α-helix: 2.3 Å radius, 100° and 1.5 Å a residue
    (3.8 Å between neighbours)."""
    return [(chain, start + k, (shift[0] + 2.3 * math.cos(math.radians(100 * k)),
                                shift[1] + 2.3 * math.sin(math.radians(100 * k)),
                                shift[2] + 1.5 * k)) for k in range(count)]


def _pdb(path: Path, residues: list, extra: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for serial, (chain, resseq, xyz) in enumerate(residues, start=1):
        lines.append(_atom(2 * serial - 1, "N", "ALA", chain, resseq,
                           (xyz[0] + 1.0, xyz[1], xyz[2]), element="N"))
        lines.append(_atom(2 * serial, "CA", "ALA", chain, resseq, xyz))
    path.write_text("".join(lines) + extra + "END\n", encoding="utf-8")
    return path


def _segments(svg: str) -> int:
    return len(re.findall(r"<line ", svg))


def test_the_trace_is_broken_where_the_chain_is(tmp_path):
    from fastmdxplora.gui.backbone_picture import backbone_svg, structure_for_picture

    study = _study(tmp_path / "helices", system="2HLX")
    residues = (_helix(10, "A", 1)
                # A missing loop: 12 Å on, the same chain.
                + _helix(8, "A", 20, shift=(0.0, 0.0, 27.0))
                + _helix(6, "B", 1, shift=(15.0, 0.0, 0.0)))
    extra = (
        # A calcium ion is "CA  ", not an alpha carbon.
        _atom(90, "CA", "CA", "C", 1, (40.0, 40.0, 40.0), record="HETATM", element="CA")
        # A second conformation of residue 1 is not a residue more.
        + _atom(91, "CA", "ALA", "A", 1, (9.0, 9.0, 9.0), altloc="B")
        + "ENDMDL\n"
        # A second model is not read.
        + _atom(92, "CA", "ALA", "D", 1, (80.0, 0.0, 0.0)))
    _pdb(study / "setup" / "prepared.pdb", residues, extra)
    assert structure_for_picture(study) == study / "setup" / "prepared.pdb"
    svg = backbone_svg(study)
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    # Three pieces: 9 + 7 + 5 segments, nothing joined across a gap or a chain.
    assert _segments(svg) == 21
    assert "The backbone of 24 residues (Cα trace)" in svg
    assert svg.count(">N</text>") == 1 and svg.count(">C</text>") == 1
    # Coloured from the N terminus (viridis's purple) to the C terminus.
    from fastmdxplora.gui.backbone_picture import _colour

    assert (_colour(0.0), _colour(1.0)) == ("#440154", "#7ad151")
    assert len(set(re.findall(r'stroke="(#[0-9a-f]{6})"', svg))) == 21
    # Kept by the file's size and time: the same text again.
    assert backbone_svg(study) is svg


def test_the_n_terminus_is_on_the_left(tmp_path):
    from fastmdxplora.gui.backbone_picture import backbone_svg

    study = _study(tmp_path / "strand", system="STRD")
    # A straight strand along x, from x = 30 down to x = 0.
    _pdb(study / "setup" / "prepared.pdb",
         [("A", k + 1, (30.0 - 3.8 * k * 0.99, 0.3 * (k % 2), 0.0)) for k in range(9)])
    svg = backbone_svg(study)
    labels = dict((label, float(x)) for x, label in
                  re.findall(r'<text x="([0-9.]+)"[^>]*>([NC])</text>', svg))
    assert labels["N"] < labels["C"]


def test_where_there_is_no_backbone(tmp_path):
    from fastmdxplora.gui.backbone_picture import backbone_svg
    from fastmdxplora.gui.workspace import card_of

    nothing = _study(tmp_path / "nothing", system="1L2Y")
    assert backbone_svg(nothing) is None and card_of(nothing)["thumbnail"] is None
    # Two atoms are not a trace.
    short = _study(tmp_path / "short", system="2RES")
    _pdb(short / "setup" / "prepared.pdb", _helix(2, "A", 1))
    assert backbone_svg(short) is None
    # A nucleic acid by its phosphorus atoms.
    dna = _study(tmp_path / "dna", system="1BNA")
    (dna / "setup").mkdir()
    (dna / "setup" / "prepared.pdb").write_text("".join(
        _atom(k + 1, "P", " DA", "A", k + 1, (6.5 * k, 0.4 * k, 0.0), element="P")
        for k in range(6)), encoding="utf-8")
    assert "The backbone of 6 residues (phosphorus trace)" in backbone_svg(dna)


def test_a_card_s_picture(tmp_path):
    from fastmdxplora.gui.workspace import card_of

    plotted = _study(tmp_path / "plotted", means={"rmsd": (0.1, 0.01)})
    _pdb(plotted / "setup" / "prepared.pdb", _helix(10, "A", 1))
    assert card_of(plotted)["thumbnail"] == "figure"
    unplotted = _study(tmp_path / "unplotted")
    _pdb(unplotted / "setup" / "prepared.pdb", _helix(10, "A", 1))
    assert card_of(unplotted)["thumbnail"] == "backbone"
    # A study of runs: the first run's structure.
    runs = _replicas(tmp_path / "chignolin")
    _pdb(runs / "runs" / "seed1" / "setup" / "prepared.pdb", _helix(10, "A", 1))
    assert card_of(runs)["thumbnail"] == "backbone"


def test_the_route_sends_it(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    plotted = _study(tmp_path / "plotted", means={"rmsd": (0.1, 0.01)})
    unplotted = _study(tmp_path / "unplotted")
    _pdb(unplotted / "setup" / "prepared.pdb", _helix(10, "A", 1))
    nothing = _study(tmp_path / "nothing")
    session = start_dashboard_session(output=str(plotted), host="127.0.0.1", port=0)
    base = session.url.rstrip("/") + "/api/study-thumbnail?"

    def get(folder):
        try:
            with urllib.request.urlopen(base + urlencode({"path": str(folder)}),
                                        timeout=30) as answer:
                return (answer.status, answer.headers["Content-Type"],
                        answer.headers.get("Content-Security-Policy"), answer.read())
        except urllib.error.HTTPError as exc:
            return (exc.code, None, None, b"")

    try:
        figure, picture, none = get(plotted), get(unplotted), get(nothing)
    finally:
        session.server.shutdown()
    assert figure[:2] == (200, "image/png") and figure[3] == PIXEL
    assert picture[:3] == (200, "image/svg+xml", "default-src 'none'")
    assert picture[3].startswith(b"<svg")
    assert none[0] == 404


def test_the_cards_two_a_row_and_the_picture_shown(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    _study(tmp_path / "plotted", means={"rmsd": (0.1, 0.01)},
           started="2026-09-03T10:00:00+00:00")
    unplotted = _study(tmp_path / "unplotted", started="2026-09-02T10:00:00+00:00")
    _pdb(unplotted / "setup" / "prepared.pdb", _helix(12, "A", 1))
    # A structure file that holds no backbone: the card falls back to the name.
    broken = _study(tmp_path / "broken", system="9XYZ", started="2026-09-01T10:00:00+00:00")
    (broken / "setup").mkdir()
    (broken / "setup" / "prepared.pdb").write_text("REMARK nothing\nEND\n", encoding="utf-8")
    _study(tmp_path / "named", system="1L2Y", started="2026-08-31T10:00:00+00:00")
    session = start_dashboard_session(output=str(tmp_path / "plotted"), host="127.0.0.1",
                                      port=0)
    sizes = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector(".study-card")
            for width in (1400, 700):
                page.set_viewport_size({"width": width, "height": 1000})
                sizes[width] = page.evaluate(
                    "() => getComputedStyle(document.getElementById('studies-grid'))"
                    ".gridTemplateColumns.split(' ').length")
            page.set_viewport_size({"width": 1400, "height": 1000})
            card = '.study-card[data-path$="unplotted"] .study-thumb'
            page.wait_for_function(f"() => document.querySelector('{card} img')"
                                   ".naturalWidth > 0")
            picture = page.get_attribute(card, "data-picture")
            title = page.get_attribute(card, "title")
            page.wait_for_selector('.study-card[data-path$="broken"] .study-thumb-none')
            fallen = page.text_content('.study-card[data-path$="broken"] .study-thumb-none')
            named = page.text_content('.study-card[data-path$="named"] .study-thumb-none')
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert sizes == {1400: 2, 700: 1}
    assert picture == "backbone" and title.startswith("No figure yet: the protein's backbone")
    assert (fallen, named) == ("9XYZ", "1L2Y")


def test_what_is_not_read_and_what_is_kept(tmp_path, monkeypatch):
    from fastmdxplora.gui import backbone_picture
    from fastmdxplora.gui.backbone_picture import _KEPT, backbone_svg

    study = _study(tmp_path / "odd", system="ODD1")
    residues = _helix(6, "A", 1)
    extra = (
        # The same residue's alpha carbon twice: read once.
        _atom(50, "CA", "ALA", "A", 6, (0.0, 0.0, 30.0))
        # Coordinates that are not numbers: left out.
        + _atom(51, "CA", "ALA", "A", 7, (0.0, 0.0, 0.0)).replace("   0.000   0.000",
                                                                  "   x.xxx   0.000"))
    _pdb(study / "setup" / "prepared.pdb", residues, extra)
    assert "The backbone of 6 residues" in backbone_svg(study)
    # A file too large for a card is not read.
    big = _study(tmp_path / "big", system="BIG1")
    _pdb(big / "setup" / "prepared.pdb", _helix(6, "A", 1))
    monkeypatch.setattr(backbone_picture, "MOST_BYTES", 10)
    assert backbone_svg(big) is None
    monkeypatch.undo()
    # The oldest picture let go when more are kept than allowed.
    monkeypatch.setattr(backbone_picture, "MOST_KEPT", 1)
    assert backbone_svg(big).startswith("<svg") and len(_KEPT) == 1
