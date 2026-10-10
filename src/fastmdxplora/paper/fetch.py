"""A paper fetched from where open-access papers are kept, by its DOI.

Europe PMC first: for a paper in its open-access subset it serves the full
text as JATS XML, sections and tables apart, and its supporting
information as one archive. A preprint on bioRxiv or medRxiv is read from
the JATS XML bioRxiv serves; one on arXiv from its PDF. A paper that is
none of these, or that Europe PMC holds without the licence to serve it to
programs, is not fetched: the person gives its PDF instead, and the reason
is said.

Nothing else is fetched, and nothing is sent but the identifier. What was
fetched is kept, so asking again for the same paper fetches nothing.
``FASTMDXPLORA_PAPERS_URL`` points every source at one address, for a
mirror or a test.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.paper import PaperRefused
from fastmdxplora.paper.text import MOST_BYTES, PaperText, jats_parts, read_paper

__all__ = ["identify", "fetch_paper", "open_paper", "SOURCES"]

SOURCES = {
    "europepmc": "https://www.ebi.ac.uk",
    "biorxiv": "https://api.biorxiv.org",
    "arxiv": "https://arxiv.org",
    "arxiv_api": "https://export.arxiv.org",
}

#: The supporting information's archive is read up to this; past it, the
#: paper is read without it and that is said.
MOST_SI_BYTES = 60 * 1024 * 1024


def _base(name: str) -> str:
    return (os.environ.get("FASTMDXPLORA_PAPERS_URL") or SOURCES[name]).rstrip("/")


def identify(source: str) -> tuple[str, str]:
    """What ``source`` names, as ``(kind, identifier)``: ``file`` and a path,
    ``pmcid``, ``arxiv``, or ``doi``. Refused where it is none of them."""
    text = str(source or "").strip()
    if not text:
        raise PaperRefused("No paper was given: give a file, a DOI, a PMCID or an "
                           "arXiv identifier.", code="environment.paper.unreadable")
    path = Path(text).expanduser()
    if path.is_file():
        return "file", str(path)
    lowered = text.lower()
    match = re.fullmatch(r"(?:pmcid:\s*)?(pmc\d{4,9})", lowered)
    if match:
        return "pmcid", match.group(1).upper()
    match = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:|10\.48550/arxiv\.)"
                      r"(\d{4}\.\d{4,5}(?:v\d+)?)", lowered)
    if match or re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", lowered):
        return "arxiv", (match.group(1) if match else lowered)
    match = re.search(r"(10\.\d{4,9}/[^\s\"<>]+)", text)
    if match:
        doi = match.group(1).rstrip(".,;)")
        doi = re.sub(r"(\.full|\.full\.pdf|\.pdf|v\d+)$", "", doi) if doi.startswith("10.1101/") else doi
        return "doi", doi
    from fastmdxplora.paper.text import SUFFIXES

    if path.suffix.lower() in SUFFIXES:
        return "file", str(path)
    raise PaperRefused(
        f"{text!r} is not a file here, a DOI (10.xxxx/...), a PMCID (PMC1234567) or an "
        "arXiv identifier.", code="environment.paper.unreadable")


def _get(url: str, *, most: int = MOST_BYTES, accept: str = "*/*") -> bytes:
    from fastmdxplora import __version__

    request = urllib.request.Request(url, headers={
        "User-Agent": f"FastMDXplora/{__version__} (reading a paper's methods)",
        "Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > most:
                raise PaperRefused(f"{url} is {int(length) / 1e6:.0f} MB, more than a paper is.",
                                   code="environment.paper.unreadable")
            data = response.read(most + 1)
    except urllib.error.HTTPError as exc:
        raise _HTTPError(exc.code, url) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PaperRefused(f"Could not reach {url}: {getattr(exc, 'reason', exc)}",
                           code="environment.service.unreachable", url=url) from None
    if len(data) > most:
        raise PaperRefused(f"{url} is more than a paper is.", code="environment.paper.unreadable")
    return data


class _HTTPError(PaperRefused):
    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"{url} answered {status}.", code="environment.service.unreachable",
                         url=url)
        self.status = status


def _kept_path(kind: str, identifier: str) -> Path:
    from fastmdxplora.paper.extract import cache_root

    digest = hashlib.sha256(f"{kind}:{identifier}".encode()).hexdigest()[:24]
    return cache_root() / "fetched" / digest


def fetch_paper(source: str, *, with_si: bool = True,
                said: Callable[[str], None] | None = None) -> PaperText:
    """The paper ``source`` names (a DOI, a PMCID, an arXiv identifier), its
    supporting information with it where Europe PMC serves it."""
    tell = said or (lambda _message: None)
    kind, identifier = identify(source)
    if kind == "file":
        return read_paper(identifier)
    kept = _kept_path(kind, identifier)
    record = kept / "paper.json"
    if record.is_file():
        try:
            recorded = json.loads(record.read_text(encoding="utf-8"))
            # Kept without the supporting information asked for now, or
            # with it failed to fetch: fetched again.
            if not (with_si and recorded.get("si") in ("not_asked", "failed")):
                return _from_kept(kept, recorded)
        except (OSError, ValueError, KeyError, AttributeError, PaperRefused):
            pass
    si_state = "read"
    if kind == "arxiv":
        paper, files = _arxiv(identifier, tell)
    elif kind == "doi" and identifier.lower().startswith("10.1101/"):
        paper, files = _biorxiv(identifier, tell)
    else:
        paper, files, si_state = _europepmc(kind, identifier, with_si, tell)
    try:
        kept.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (kept / name).write_bytes(data)
        record.write_text(json.dumps({
            "source": source, "title": paper.title, "doi": paper.doi, "route": paper.route,
            "licence": paper.licence, "files": list(files), "si": si_state}),
            encoding="utf-8")
    except OSError:
        pass
    return paper


def _from_kept(folder: Path, record: dict[str, Any]) -> PaperText:
    parts = []
    for name in record["files"]:
        data = (folder / name).read_bytes()
        kind = "si" if name.startswith("si-") else "paper"
        parts.extend(_parts_of(name, data, kind))
    if not parts:
        raise PaperRefused("Nothing kept.", code="environment.paper.unreadable")
    return PaperText(parts=parts, title=record.get("title", ""), doi=record.get("doi", ""),
                     source=record.get("source", ""), route=record.get("route", ""),
                     licence=record.get("licence", ""))


def _parts_of(name: str, data: bytes, kind: str) -> list[Any]:
    """The parts of a fetched file, by its name's suffix."""
    import tempfile

    suffix = Path(name).suffix.lower()
    if suffix in (".xml", ".nxml"):
        return jats_parts(data, kind)[0]
    if suffix in (".pdf", ".docx", ".txt", ".md"):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"file{suffix}"
            path.write_bytes(data)
            try:
                return read_paper(path, kind=kind).parts
            except PaperRefused:
                return []
    return []


def _europepmc(kind: str, identifier: str, with_si: bool,
               tell: Callable[[str], None]) -> tuple[PaperText, dict[str, bytes], str]:
    """The paper from Europe PMC, its files to keep, and whether its
    supporting information was ``read``, ``not_asked`` or ``failed``."""
    base = _base("europepmc")
    # Europe PMC finds nothing for a PMCID in quotes, so it goes bare (it is
    # only PMC and digits, from identify); a DOI goes in quotes for its "/".
    query = f"PMCID:{identifier}" if kind == "pmcid" else f'DOI:"{identifier}"'
    url = (f"{base}/europepmc/webservices/rest/search?"
           + urllib.parse.urlencode({"query": query, "format": "json", "resultType": "core"}))
    tell(f"Looking for {identifier} in Europe PMC...")
    try:
        found = json.loads(_get(url, most=5 * 1024 * 1024, accept="application/json"))
    except ValueError as exc:
        raise PaperRefused("Europe PMC's answer could not be read.",
                           code="environment.service.unusable_response") from exc
    results = ((found.get("resultList") or {}).get("result") or []) if isinstance(found, dict) else []
    if not results:
        raise PaperRefused(
            f"Europe PMC does not have {identifier}. If the paper is open access "
            "elsewhere, or you have its PDF, give the file instead.",
            code="environment.paper.not_open", identifier=identifier)
    entry = results[0]
    title = str(entry.get("title") or "").rstrip(".")
    doi = str(entry.get("doi") or (identifier if kind == "doi" else ""))
    pmcid = str(entry.get("pmcid") or "")
    licence = str(entry.get("license") or "")
    if not pmcid or str(entry.get("isOpenAccess") or "N").upper() != "Y":
        free = " It is free to read in PubMed Central, but not licensed for programs to " \
               "fetch;" if pmcid else ""
        raise PaperRefused(
            f"{title or identifier} is not in Europe PMC's open-access collection.{free} "
            "Download its PDF (and its supporting information) and give the files: "
            "--paper FILE --paper-si FILE.",
            code="environment.paper.not_open", identifier=identifier)
    tell(f"Fetching the full text of {pmcid}...")
    xml = _get(f"{base}/europepmc/webservices/rest/{pmcid}/fullTextXML",
               accept="application/xml")
    parts, meta = jats_parts(xml, "paper")
    files = {"paper.xml": xml}
    si_state = "read" if with_si else "not_asked"
    if with_si:
        try:
            archive = _get(f"{base}/europepmc/webservices/rest/{pmcid}/supplementaryFiles",
                           most=MOST_SI_BYTES)
            si_files = _si_from_archive(archive)
            for name, data in si_files.items():
                parts.extend(_parts_of(name, data, "si"))
                files[name] = data
            if si_files:
                tell(f"Read its supporting information ({len(si_files)} file(s)).")
        except PaperRefused as exc:
            if getattr(exc, "status", None) != 404:
                si_state = "failed"
                tell(f"Its supporting information could not be fetched ({exc}); read "
                     "without it.")
    return PaperText(parts=parts, title=meta.get("title") or title, doi=meta.get("doi") or doi,
                     source=identifier, route=f"Europe PMC {pmcid}",
                     licence=meta.get("licence") or licence), files, si_state


def _si_from_archive(data: bytes) -> dict[str, bytes]:
    """The readable files of a supporting-information archive, by a name
    of their own: PDFs, Word files and text, each under 60 MB."""
    out: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            total = 0
            for info in archive.infolist():
                name = Path(info.filename).name
                suffix = Path(name).suffix.lower()
                if info.is_dir() or suffix not in (".pdf", ".docx", ".txt"):
                    continue
                if info.file_size > MOST_SI_BYTES or total + info.file_size > MOST_SI_BYTES:
                    continue
                total += info.file_size
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80]
                out[f"si-{len(out) + 1}-{safe}"] = archive.read(info)
    except zipfile.BadZipFile:
        return {}
    return out


def _biorxiv(doi: str, tell: Callable[[str], None]) -> tuple[PaperText, dict[str, bytes]]:
    base = _base("biorxiv")
    tell(f"Looking for {doi} on bioRxiv...")
    for server in ("biorxiv", "medrxiv"):
        try:
            found = json.loads(_get(f"{base}/details/{server}/{doi}", most=5 * 1024 * 1024,
                                    accept="application/json"))
        except (PaperRefused, ValueError):
            continue
        versions = found.get("collection") if isinstance(found, dict) else None
        if not versions:
            continue
        latest = versions[-1]
        xml_url = str(latest.get("jatsxml") or "")
        if not xml_url.startswith(("https://", "http://")):
            continue
        if os.environ.get("FASTMDXPLORA_PAPERS_URL"):
            xml_url = base + urllib.parse.urlparse(xml_url).path
        xml = _get(xml_url, accept="application/xml")
        parts, meta = jats_parts(xml, "paper")
        return PaperText(parts=parts, title=meta.get("title") or str(latest.get("title") or ""),
                         doi=doi, source=doi,
                         route=f"{server} version {latest.get('version', '')}".strip(),
                         licence=str(latest.get("license") or "")), {"paper.xml": xml}
    raise PaperRefused(f"bioRxiv and medRxiv do not have {doi}.",
                       code="environment.paper.not_open", identifier=doi)


def _arxiv(identifier: str, tell: Callable[[str], None]) -> tuple[PaperText, dict[str, bytes]]:
    tell(f"Fetching arXiv:{identifier}...")
    pdf = _get(f"{_base('arxiv')}/pdf/{identifier}", accept="application/pdf")
    if not pdf.startswith(b"%PDF"):
        raise PaperRefused(f"arXiv did not answer {identifier} with a PDF.",
                           code="environment.service.unusable_response")
    parts = _parts_of("paper.pdf", pdf, "paper")
    title = ""
    try:
        feed = _get(f"{_base('arxiv_api')}/api/query?id_list={identifier}", most=1024 * 1024)
        match = re.search(rb"<entry>.*?<title>(.*?)</title>", feed, re.S)
        if match:
            title = re.sub(r"\s+", " ", match.group(1).decode("utf-8", "replace")).strip()
    except PaperRefused:
        pass
    if not parts:
        raise PaperRefused(f"arXiv:{identifier}'s PDF holds no text to read.",
                           code="environment.paper.unreadable")
    return PaperText(parts=parts, title=title, doi=f"10.48550/arXiv.{identifier.split('v')[0]}",
                     source=identifier, route=f"arXiv {identifier}"), {"paper.pdf": pdf}


def open_paper(source: str, si: list[str] | tuple[str, ...] = (), *,
               said: Callable[[str], None] | None = None) -> PaperText:
    """The paper ``source`` names, a file or an identifier, with the files
    in ``si`` read as its supporting information."""
    paper = fetch_paper(source, said=said)
    for path in si:
        extra = read_paper(path, kind="si")
        paper.parts.extend(extra.parts)
        paper.files.extend(extra.files)
    return paper
