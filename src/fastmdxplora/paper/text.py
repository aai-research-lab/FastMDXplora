"""A paper's words, as parts that can be pointed at.

A PDF is read page by page (``p. 3``), the JATS XML that open-access
archives serve section by section (``Methods``, ``Table 2``), a Word file
and plain text whole. Each part keeps where it came from, so a value read
from it can be said with its place: ``Methods`` of the paper, ``SI p. 4``
of its supporting information.

Nothing here interprets the text. It is what the AI model is shown, and
what every quote it gives is looked for in (:mod:`.quotes`).
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.paper import PaperRefused

__all__ = ["Part", "PaperText", "read_paper", "jats_parts", "SUFFIXES", "MOST_BYTES"]

#: What can be given as a paper or its supporting information.
SUFFIXES = (".pdf", ".xml", ".nxml", ".docx", ".txt", ".md")

#: A file larger than this is not read: a paper with its figures is a few
#: megabytes, and one of a hundred is more likely a thesis or a mistake.
MOST_BYTES = 100 * 1024 * 1024

#: Pages read from one PDF. A supporting information of 200 pages is
#: mostly tables of coordinates; the methods are early.
MOST_PAGES = 400


@dataclass(frozen=True)
class Part:
    """One page, section or table: ``label`` says where it is (``p. 3``,
    ``Methods``, ``SI p. 2``), ``kind`` whether it is the paper or its
    supporting information."""

    label: str
    text: str
    kind: str = "paper"


@dataclass
class PaperText:
    """A paper's parts and what is known of it. ``route`` says how it was
    had: ``file`` or the open-access source it was fetched from."""

    parts: list[Part]
    title: str = ""
    doi: str = ""
    source: str = ""
    route: str = "file"
    licence: str = ""
    files: list[str] = field(default_factory=list)

    def sha256(self) -> str:
        """The digest of its words, the key a reading of it is kept under."""
        digest = hashlib.sha256()
        for part in self.parts:
            digest.update(part.kind.encode())
            digest.update(b"\0")
            digest.update(part.label.encode())
            digest.update(b"\0")
            digest.update(part.text.encode("utf-8", "replace"))
            digest.update(b"\0")
        return digest.hexdigest()

    def chars(self) -> int:
        return sum(len(part.text) for part in self.parts)

    def shown(self, budget: int) -> tuple[str, list[str]]:
        """The text the AI model is shown, each part under its label as
        ``[[label]]``, and the labels of the parts left out to keep within
        ``budget`` characters: the references first, then the parts that
        say least about a simulation. Left-out parts are named, so a reader
        knows a value may be in one of them."""
        keep = list(range(len(self.parts)))
        left_out: list[str] = []
        total = sum(len(self.parts[i].text) for i in keep)
        if total > budget:
            for index in list(keep):
                if _is_references(self.parts[index]) and total > budget:
                    keep.remove(index)
                    left_out.append(self.parts[index].label)
                    total -= len(self.parts[index].text)
        if total > budget:
            ranked = sorted(keep, key=lambda i: _md_density(self.parts[i].text))
            for index in ranked:
                if total <= budget:
                    break
                keep.remove(index)
                left_out.append(self.parts[index].label)
                total -= len(self.parts[index].text)
        lines = []
        for index in sorted(keep):
            part = self.parts[index]
            prefix = "SI " if part.kind == "si" and not part.label.startswith("SI") else ""
            lines.append(f"[[{prefix}{part.label}]]\n{part.text.strip()}")
        return "\n\n".join(lines), left_out


#: Words that say a part is about how a simulation was run.
_MD_WORDS = re.compile(
    r"\b(ns|ps|fs|µs|μs|force ?field|tip3p|tip4p|opc|spc|water model|thermostat|"
    r"barostat|langevin|nos[eé]|parrinello|berendsen|pme|ewald|cutoff|cut-off|"
    r"time ?step|shake|lincs|settle|npt|nvt|equilibrat\w*|production|replica\w*|"
    r"amber|charmm|gromacs|openmm|namd|desmond|acemd|ff\d\d\w*|gaff|cgenff|"
    r"openff|umbrella|metadynamics|plumed|simulation\w*|trajector\w*|"
    r"rmsd|rmsf|radius of gyration)\b",
    re.IGNORECASE)


def _md_density(text: str) -> float:
    return len(_MD_WORDS.findall(text)) / max(1.0, len(text) / 1000.0)


def _is_references(part: Part) -> bool:
    head = part.label.strip().lower()
    if head in ("references", "bibliography", "references and notes", "literature cited"):
        return True
    # A PDF page made mostly of numbered references.
    entries = re.findall(r"(?m)^\s*\(?\[?\d{1,3}[\].)]\s+[A-Z][\w'-]+,", part.text)
    return len(entries) >= 8 and len(entries) * 120 > len(part.text) * 0.5


# ---------------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------------
def read_paper(path: str | Path, *, kind: str = "paper") -> PaperText:
    """A paper (``kind="paper"``) or its supporting information
    (``kind="si"``) read from a file, by its suffix."""
    file = Path(path).expanduser()
    if not file.is_file():
        raise PaperRefused(f"No file at {file}.", code="environment.path.not_found",
                           path=str(file))
    suffix = file.suffix.lower()
    if suffix not in SUFFIXES:
        raise PaperRefused(
            f"{file.name} is not a file a paper is read from: give a PDF, the "
            f"JATS XML an open-access archive serves, a Word file (.docx), or "
            f"text ({', '.join(SUFFIXES)}).",
            code="environment.paper.unreadable", path=str(file))
    size = file.stat().st_size
    if size > MOST_BYTES:
        raise PaperRefused(
            f"{file.name} is {size / 1e6:.0f} MB; a paper is read up to "
            f"{MOST_BYTES / 1e6:.0f} MB.", code="environment.paper.unreadable",
            path=str(file))
    meta: dict[str, Any] = {}
    if suffix == ".pdf":
        parts = _pdf_parts(file, kind)
    elif suffix in (".xml", ".nxml"):
        parts, meta = jats_parts(file.read_bytes(), kind)
    elif suffix == ".docx":
        parts = _docx_parts(file, kind)
    else:
        parts = [Part("text" if kind == "paper" else "SI",
                      file.read_text(encoding="utf-8", errors="replace"), kind)]
    parts = [part for part in parts if part.text.strip()]
    if not parts:
        raise PaperRefused(
            f"{file.name} holds no text to read. A scanned PDF is pictures of "
            "pages: give the publisher's PDF, or the paper's DOI if it is "
            "open access.", code="environment.paper.unreadable", path=str(file))
    return PaperText(parts=parts, title=str(meta.get("title") or ""),
                     doi=str(meta.get("doi") or ""), source=str(file), route="file",
                     licence=str(meta.get("licence") or ""), files=[str(file)])


def _pdf_parts(file: Path, kind: str) -> list[Part]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - a dependency of the package
        raise PaperRefused(
            "Reading a PDF needs pypdf (`pip install pypdf`).",
            code="environment.backend.missing") from exc
    prefix = "SI p." if kind == "si" else "p."
    try:
        reader = PdfReader(str(file))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise PaperRefused(
                        f"{file.name} is protected by a password and cannot be read.",
                        code="environment.paper.unreadable", path=str(file))
            except NotImplementedError as exc:
                raise PaperRefused(
                    f"{file.name} is encrypted in a way this cannot read.",
                    code="environment.paper.unreadable", path=str(file)) from exc
        parts = []
        for number, page in enumerate(reader.pages[:MOST_PAGES], start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one page's fault, not the file's
                text = ""
            parts.append(Part(f"{prefix} {number}", _tidy(text), kind))
        return parts
    except PaperRefused:
        raise
    except (PdfReadError, OSError, ValueError, KeyError, TypeError) as exc:
        raise PaperRefused(f"{file.name} could not be read as a PDF: {exc}",
                           code="environment.paper.unreadable", path=str(file)) from exc


class _EntityDeclared(Exception):
    pass


def _parsed(data: bytes) -> Any:
    """``data`` parsed as XML, refused where it declares an entity of its
    own. An article an archive serves declares none, and one that does
    could expand to far more than it is. The declarations are looked for by
    the XML parser itself, so no comment, encoding or length hides one."""
    import xml.etree.ElementTree as ET
    from xml.parsers import expat

    def refuse(*_args: Any) -> None:
        raise _EntityDeclared

    scan = expat.ParserCreate()
    scan.EntityDeclHandler = refuse
    scan.UnparsedEntityDeclHandler = refuse
    try:
        scan.Parse(data, True)
    except _EntityDeclared:
        raise PaperRefused("The XML defines its own entities, which an archive's article "
                           "does not; it is not read.",
                           code="environment.paper.unreadable") from None
    except expat.ExpatError:
        pass  # said by the parse below, as any malformed XML is
    return ET.fromstring(data)


def _docx_parts(file: Path, kind: str) -> list[Part]:
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(file) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MOST_BYTES:
                raise PaperRefused(f"{file.name} unpacks to more than a paper does.",
                                   code="environment.paper.unreadable", path=str(file))
            root = _parsed(archive.read(info))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise PaperRefused(f"{file.name} could not be read as a Word file: {exc}",
                           code="environment.paper.unreadable", path=str(file)) from exc
    paragraphs = []
    for paragraph in root.iter():
        if _local(paragraph.tag) != "p":
            continue
        words = "".join(node.text or "" for node in paragraph.iter()
                        if _local(node.tag) == "t")
        if words.strip():
            paragraphs.append(words)
    label = "SI" if kind == "si" else "text"
    return [Part(label, "\n".join(paragraphs), kind)]


def _tidy(text: str) -> str:
    # A word broken over a line by the page's hyphenation, joined; the
    # page's own line breaks kept, since a table reads by them.
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return re.sub(r"[ \t]+", " ", text)


# ---------------------------------------------------------------------------
# JATS XML
# ---------------------------------------------------------------------------
#: What a part's text leaves out: tables and figures are parts of their
#: own, and references are not read.
_SKIPPED = ("table-wrap", "fig", "ref-list")
_REFERENCES = re.compile(r"references?( and notes)?|bibliography|literature cited|"
                         r"notes and references")


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _words(node: Any, *, skip: tuple[str, ...] = ()) -> str:
    """The text of an element, its children's included, with a space
    where block elements meet, and none of ``skip``'s elements."""
    pieces: list[str] = []

    def walk(element: Any) -> None:
        name = _local(element.tag)
        if name in skip:
            if element.tail:
                pieces.append(element.tail)
            return
        if element.text:
            pieces.append(element.text)
        for child in element:
            walk(child)
        if name in ("p", "title", "td", "th", "label", "caption", "list-item"):
            pieces.append(" ")
        if element.tail:
            pieces.append(element.tail)

    walk(node)
    return re.sub(r"\s+", " ", "".join(pieces)).strip()


def jats_parts(data: bytes, kind: str = "paper") -> tuple[list[Part], dict[str, str]]:
    """A JATS article's parts: its abstract, each top-level section of its
    body under the section's title (subsections inside it), each table on
    its own (rows on lines, cells by ``|``), appendices and footnotes; the
    references left out. Also its title, DOI and licence."""
    import xml.etree.ElementTree as ET

    if len(data) > MOST_BYTES:
        raise PaperRefused("The article's XML is larger than a paper is.",
                           code="environment.paper.unreadable")
    try:
        root = _parsed(data)
    except ET.ParseError as exc:
        raise PaperRefused(f"The article's XML could not be read: {exc}",
                           code="environment.paper.unreadable") from exc
    meta: dict[str, str] = {}
    prefix = "SI " if kind == "si" else ""
    for element in root.iter():
        name = _local(element.tag)
        if name == "article-title" and "title" not in meta:
            meta["title"] = _words(element)
        elif name == "article-id" and element.get("pub-id-type") == "doi" and "doi" not in meta:
            meta["doi"] = (element.text or "").strip()
        elif name == "license" and "licence" not in meta:
            link = next((value for key, value in element.attrib.items()
                         if _local(key) == "href"), "")
            meta["licence"] = link or _words(element)[:200]

    parts: list[Part] = []
    tables: list[Any] = []
    for element in root.iter():
        if _local(element.tag) == "abstract":
            parts.append(Part(f"{prefix}Abstract", _words(element), kind))
            break
    body = next((e for e in root.iter() if _local(e.tag) == "body"), None)
    if body is not None:
        loose: list[str] = []
        for child in body:
            name = _local(child.tag)
            if name == "sec":
                title = next((_words(t) for t in child if _local(t.tag) == "title"), "")
                if _REFERENCES.fullmatch(title.strip().lower()) or any(
                        _local(e.tag) == "ref-list" for e in child.iter()):
                    continue
                tables.extend(e for e in child.iter() if _local(e.tag) == "table-wrap")
                subsections = [s for s in child if _local(s.tag) == "sec"]
                if subsections:
                    # Each subsection its own part, so a value is said
                    # with the subsection it is in.
                    lead = " ".join(_words(e, skip=_SKIPPED) for e in child
                                    if _local(e.tag) not in ("sec", "title"))
                    if lead.strip():
                        parts.append(Part(f"{prefix}{title or 'Section'}", lead, kind))
                    for sub in subsections:
                        sub_title = next((_words(t) for t in sub if _local(t.tag) == "title"), "")
                        label = f"{title}: {sub_title}" if title and sub_title else (
                            title or sub_title or "Section")
                        parts.append(Part(f"{prefix}{label}", _words(sub, skip=_SKIPPED), kind))
                else:
                    parts.append(Part(f"{prefix}{title or 'Section'}",
                                      _words(child, skip=_SKIPPED), kind))
                figures = [e for e in child.iter() if _local(e.tag) == "fig"]
                for figure in figures:
                    label = next((_words(t) for t in figure if _local(t.tag) == "label"),
                                 "Figure")
                    caption = next((_words(t) for t in figure if _local(t.tag) == "caption"),
                                   "")
                    if caption:
                        parts.append(Part(f"{prefix}{label} caption", caption, kind))
            elif name == "table-wrap":
                tables.append(child)
            else:
                loose.append(_words(child))
        if any(loose):
            parts.append(Part(f"{prefix}Body", " ".join(t for t in loose if t), kind))
    back = next((e for e in root.iter() if _local(e.tag) == "back"), None)
    if back is not None:
        for child in back.iter():
            name = _local(child.tag)
            if name in ("app", "fn-group", "notes", "ack"):
                title = next((_words(t) for t in child if _local(t.tag) == "title"),
                             name.replace("-", " ").title())
                words = _words(child, skip=_SKIPPED)
                if words:
                    parts.append(Part(f"{prefix}{title}", words, kind))
                tables.extend(e for e in child.iter() if _local(e.tag) == "table-wrap")
    seen: set[int] = set()
    for table in tables:
        if id(table) in seen:
            continue
        seen.add(id(table))
        parts.append(_table_part(table, prefix, kind))
    return [part for part in parts if part.text.strip()], meta


def _table_part(table: Any, prefix: str, kind: str) -> Part:
    label = next((_words(t) for t in table if _local(t.tag) == "label"), "Table")
    caption = next((_words(t) for t in table if _local(t.tag) == "caption"), "")
    rows = []
    for row in table.iter():
        if _local(row.tag) != "tr":
            continue
        cells = [_words(cell) for cell in row if _local(cell.tag) in ("td", "th")]
        rows.append(" | ".join(cells))
    foot = " ".join(_words(e) for e in table if _local(e.tag) == "table-wrap-foot")
    text = "\n".join(t for t in [caption, *rows, foot] if t)
    return Part(f"{prefix}{label}", text, kind)
