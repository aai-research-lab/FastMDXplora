"""An open-access paper is fetched by its identifier, and only an open one.

Europe PMC first, for a paper in its open-access collection: the full text
as JATS XML and its supporting information. bioRxiv's JATS XML for a
preprint there, arXiv's PDF for one there. A paper free to read but not
licensed for programs to fetch, or not open at all, is refused with what to
do instead. What is fetched is kept, so a paper is fetched once.
"""

from __future__ import annotations

import io
import json
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from fastmdxplora.paper import PaperRefused
from fastmdxplora.paper.fetch import fetch_paper, identify

from tests._a_paper import DOI, TITLE, jats, pdf

OPEN_DOI = DOI
CLOSED_DOI = "10.9999/closed.0002"
PREPRINT = "10.1101/2024.01.01.000001"
ARXIV = "2407.14794"


_EUROPE_PMC = (
    {"pmcid": "PMC1234567", "doi": OPEN_DOI, "title": TITLE + ".", "isOpenAccess": "Y",
     "license": "cc by"},
    {"pmcid": "PMC7654321", "doi": CLOSED_DOI, "title": "A closed paper", "isOpenAccess": "N"},
)


def _finds(query: str, entry: dict) -> bool:
    """Whether Europe PMC's search finds ``entry`` for ``query``, as it
    answers (checked 10-10 on PMC7906464): a DOI in quotes or bare, a PMCID
    only bare; ``PMCID:"PMC7906464"`` finds nothing."""
    doi = re.fullmatch(r'DOI:"([^"]+)"|DOI:(\S+)', query)
    if doi:
        return (doi.group(1) or doi.group(2)).lower() == entry["doi"].lower()
    pmcid = re.fullmatch(r"PMCID:(PMC\d+)", query)
    return bool(pmcid) and pmcid.group(1) == entry["pmcid"]


class _Archive(BaseHTTPRequestHandler):
    asked: list[str] = []

    def log_message(self, *args):  # noqa: D401 - quiet
        return

    def _send(self, body: bytes, kind: str = "application/json", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        type(self).asked.append(self.path)
        url = urlparse(self.path)
        if url.path == "/europepmc/webservices/rest/search":
            query = parse_qs(url.query)["query"][0]
            results = [entry for entry in _EUROPE_PMC if _finds(query, entry)]
            self._send(json.dumps({"resultList": {"result": results}}).encode())
        elif url.path == "/europepmc/webservices/rest/PMC1234567/fullTextXML":
            self._send(jats(), "application/xml")
        elif url.path == "/europepmc/webservices/rest/PMC1234567/supplementaryFiles":
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("si/Table_S1.pdf", pdf([["Table S1. Each replica ran 100 ns."]]))
                archive.writestr("si/movie.mp4", b"\x00" * 10)
            self._send(buffer.getvalue(), "application/zip")
        elif url.path == f"/details/biorxiv/{PREPRINT}":
            self._send(json.dumps({"collection": [{
                "version": "2", "title": "A preprint", "license": "cc_by",
                "jatsxml": "https://www.biorxiv.org/content/early/2024/01/01/source.xml"}]}).encode())
        elif url.path == "/content/early/2024/01/01/source.xml":
            self._send(jats(), "application/xml")
        elif url.path == f"/pdf/{ARXIV}":
            self._send(pdf([["Methods", "Simulations lasted 500 ns at 300 K."]]), "application/pdf")
        elif url.path == "/api/query":
            self._send(b"<feed><entry><title>An arXiv paper</title></entry></feed>", "application/xml")
        else:
            self._send(b"{}", status=404)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    _Archive.asked = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Archive)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("FASTMDXPLORA_PAPERS_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    yield _Archive
    server.shutdown()


@pytest.mark.parametrize("given, kind, identifier", [
    ("10.1371/journal.pone.0247841", "doi", "10.1371/journal.pone.0247841"),
    ("https://doi.org/10.1371/journal.pone.0247841", "doi", "10.1371/journal.pone.0247841"),
    ("PMC7906464", "pmcid", "PMC7906464"),
    ("arXiv:2407.14794", "arxiv", "2407.14794"),
    ("https://arxiv.org/abs/2407.14794v2", "arxiv", "2407.14794v2"),
    ("https://www.biorxiv.org/content/10.1101/2024.03.23.586267v1", "doi",
     "10.1101/2024.03.23.586267"),
])
def test_an_identifier_is_told_from_a_file(given, kind, identifier):
    assert identify(given) == (kind, identifier)


def test_something_that_names_no_paper_is_refused():
    with pytest.raises(PaperRefused, match="not a file here, a DOI"):
        identify("the trypsin paper")


def test_an_open_paper_is_fetched_with_its_supporting_information(archive):
    paper = fetch_paper(OPEN_DOI)
    assert paper.title == TITLE and paper.route == "Europe PMC PMC1234567"
    labels = [part.label for part in paper.parts]
    assert "Methods: Molecular dynamics simulations" in labels
    assert "SI p. 1" in labels  # the SI's PDF read; the movie is not
    asked = len(archive.asked)
    again = fetch_paper(OPEN_DOI)
    assert len(archive.asked) == asked  # kept, not fetched again
    assert [part.label for part in again.parts] == labels


def test_a_paper_not_open_for_programs_is_not_fetched(archive):
    with pytest.raises(PaperRefused, match="Download its PDF") as refused:
        fetch_paper(CLOSED_DOI)
    assert refused.value.code == "environment.paper.not_open"
    assert not any("PMC7654321/fullTextXML" in path for path in archive.asked)


def test_an_open_paper_is_fetched_by_its_pmcid(archive):
    # Check 5 (10-10): every registered paper is given by its PMCID, and
    # Europe PMC found none of them while the PMCID was sent in quotes.
    paper = fetch_paper("PMC1234567")
    assert paper.title == TITLE and paper.route == "Europe PMC PMC1234567"
    searched = [parse_qs(urlparse(path).query)["query"][0] for path in archive.asked
                if urlparse(path).path.endswith("/rest/search")]
    assert searched == ["PMCID:PMC1234567"]


def test_a_paper_not_open_for_programs_is_refused_as_such_by_its_pmcid(archive):
    with pytest.raises(PaperRefused) as refused:
        fetch_paper("pmcid: pmc7654321")
    said = str(refused.value)
    assert refused.value.code == "environment.paper.not_open"
    assert "not in Europe PMC's open-access collection" in said
    assert "free to read in PubMed Central" in said and "does not have" not in said
    assert not any("PMC7654321/fullTextXML" in path for path in archive.asked)


def test_a_paper_europe_pmc_does_not_have_is_said(archive):
    with pytest.raises(PaperRefused, match="does not have"):
        fetch_paper("10.9999/nowhere.0003")


def test_a_preprint_is_fetched_from_biorxiv(archive):
    paper = fetch_paper(PREPRINT)
    assert paper.route == "biorxiv version 2"
    assert any(part.label == "Table 1" for part in paper.parts)


def test_an_arxiv_paper_is_fetched_as_its_pdf(archive):
    paper = fetch_paper(f"arXiv:{ARXIV}")
    assert paper.title == "An arXiv paper"
    assert paper.doi == f"10.48550/arXiv.{ARXIV}"
    assert "500 ns" in paper.parts[0].text


def test_an_archive_that_cannot_be_reached_is_said(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_PAPERS_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    with pytest.raises(PaperRefused) as refused:
        fetch_paper(OPEN_DOI)
    assert refused.value.code == "environment.service.unreachable"
