"""Every figure the report names opens from where the report is.

report.md is written in report/, and Markdown resolves a relative link
against the file's own folder. The analysis figures were linked as
`analysis/rmsd/rmsd.png`, which is where they are from the study's root and
nowhere from report/: a viewer showed a broken image for every analysis, the
PDF renderer (which resolves from report/ as well) left them out without a
word, and the GUI's Report page asked the server for report/analysis/... and
got a 404 for each. Only the summary figure, which is written in report/,
ever appeared.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urljoin

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora import FastMDXplora

IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def _study(root: Path) -> Path:
    """A short trajectory of a five-residue chain, analysed for real."""
    rng = np.random.default_rng(0)
    top = md.Topology()
    chain = top.add_chain()
    for i in range(5):
        residue = top.add_residue("ALA", chain, resSeq=i + 1)
        for name, element in (("N", md.element.nitrogen), ("CA", md.element.carbon),
                              ("C", md.element.carbon), ("O", md.element.oxygen)):
            top.add_atom(name, element, residue)
    top.create_standard_bonds()
    base = rng.uniform(-0.3, 0.3, size=(top.n_atoms, 3))
    xyz = base[None] + rng.normal(scale=0.02, size=(30, top.n_atoms, 3))
    traj = md.Trajectory(xyz=xyz.astype(np.float32), topology=top,
                         time=np.arange(30) * 10.0)
    simulation = root / "simulation"
    simulation.mkdir(parents=True)
    traj[0].save_pdb(str(simulation / "topology.pdb"))
    traj.save_dcd(str(simulation / "production.dcd"))
    study = FastMDXplora(system=str(simulation / "topology.pdb"), output_dir=root)
    study.explore(include=["analysis"],
                  options={"analysis": {"include": ["rmsd", "rg", "cluster"],
                                        "options": {"cluster": {"methods": ["kmeans"],
                                                                "n_clusters": 2}}}})
    study.report()
    return root


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    return _study(tmp_path_factory.mktemp("figures") / "study")


def _images(study: Path) -> list[tuple[str, str]]:
    return IMAGE.findall((study / "report" / "report.md").read_text(encoding="utf-8"))


def test_every_figure_named_is_where_the_link_says(study) -> None:
    images = _images(study)
    targets = {target for _, target in images}
    assert {"../analysis/rmsd/rmsd.png", "../analysis/rg/rg.png",
            "analysis_summary.png"} <= targets
    missing = [t for t in targets if not (study / "report" / unquote(t)).is_file()]
    assert missing == []


def test_the_captions_name_the_figure_plainly(study) -> None:
    captions = {caption for caption, _ in _images(study)}
    assert {"rmsd", "rg", "cluster: kmeans"} <= captions
    assert not any("—" in c or "–" in c for c in captions)


def test_the_title_has_no_dash(study) -> None:
    first = (study / "report" / "report.md").read_text(encoding="utf-8").splitlines()[0]
    assert first == "# FastMDXplora Study: topology"


def test_the_pdf_carries_every_figure(study) -> None:
    """WeasyPrint resolves from report/ as well, and drops what it cannot
    find without saying so."""
    pypdf = pytest.importorskip("pypdf")
    pdf = study / "report" / "report.pdf"
    if not pdf.is_file():
        pytest.skip("no PDF renderer here")
    embedded = sum(len(page.images) for page in pypdf.PdfReader(str(pdf)).pages)
    assert embedded >= len(_images(study))


def test_the_gui_report_page_can_fetch_each_figure(study) -> None:
    """What the page does: resolve each relative src against
    /artifacts/report/ and ask the server for it. Without the Markdown
    library the page shows the report as text, with no figures to fetch."""
    pytest.importorskip("markdown")
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=study, port=0)
    try:
        with urllib.request.urlopen(session.url + "/api/report", timeout=10) as reply:
            payload = json.loads(reply.read())
        sources = re.findall(r'<img[^>]+src="([^"]+)"', payload["html"])
        assert sources
        under = session.url + "/artifacts/" + payload.get("figures_under", "report") + "/"
        for src in sources:
            with urllib.request.urlopen(urljoin(under, src), timeout=10) as reply:
                assert reply.status == 200
                assert reply.headers["Content-Type"] == "image/png"
    finally:
        session.server.shutdown()


def test_a_report_written_elsewhere_links_from_there(study, tmp_path) -> None:
    """The report folder is whatever the phase was given, not report/ by name."""
    from fastmdxplora.report.document import _results_section

    elsewhere = tmp_path / "deeper" / "report"
    targets = [t for _, t in IMAGE.findall(_results_section(study, elsewhere))]
    assert any(t.endswith("rmsd/rmsd.png") for t in targets)
    assert all((elsewhere / unquote(t)).resolve().is_file()
               for t in targets if t.endswith(("rmsd.png", "rg.png")))


def test_the_reweighting_figure_links_from_the_report(tmp_path) -> None:
    from fastmdxplora.report.reweighted import reweighted_section

    figure = tmp_path / "analysis" / "reweighted" / "reweighted_averages.png"
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b"png")
    record = {"applies": True, "quantities": [], "settled": True}
    text = reweighted_section(tmp_path, record)
    assert "](../analysis/reweighted/reweighted_averages.png)" in text
