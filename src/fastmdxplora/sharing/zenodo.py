"""Zenodo, for a shared study: a draft made from it, and a record read back.

`--share-to zenodo` makes a draft, never a publication: Zenodo reserves the
draft's DOI first, the archive is packed naming that DOI as its own, the
archive is uploaded, and the draft's description is filled in from the
study's records. The person reads the draft and presses Publish. A study
shared again is a new version of the record it was shared as, kept in
`shared_to.json`.

Opening a study from a DOI or a Zenodo address reads the record
(:func:`record_of`, :func:`archive_of`) and downloads its archive from the
address Zenodo serves files at, checked against the MD5 Zenodo recorded.

Zenodo's REST API, as documented at developers.zenodo.org: depositions
under `/api/deposit/depositions`, a file put into a deposition's bucket,
records under `/api/records/<id>`. `FASTMDXPLORA_ZENODO_URL` points both
sites elsewhere (a test's own server).

Zenodo limits how often it is asked, in requests a minute (429, with when
it may be asked again in `X-RateLimit-Reset`, or how long to wait in
`Retry-After`) and is at times briefly unable to answer (502, 503, 504):
each request is asked again after a wait, up to :data:`TRIES` times. A
request that makes something (a new draft) is asked again only after a 429,
which Zenodo answers before doing anything. A new draft is asked for with a
title of its own (:func:`_marked`) recorded in the study first: where the
answer is lost after Zenodo may have made it (a 5xx, a connection cut), the
draft of that title is looked for and taken as this one, then and at the
next share; a draft made twice that way (one Zenodo listed only late) is
named for the person to delete, and one the person made is never taken.
"""

from __future__ import annotations

import errno
import html
import http.client
import json
import os
import re
import secrets
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmdxplora.sharing import ShareRefused, said_size

__all__ = ["SHARED_TO", "SITES", "TRIES", "archive_of", "base_url", "draft", "record_of",
           "wait_before"]

SITES = {"zenodo": "https://zenodo.org", "zenodo-sandbox": "https://sandbox.zenodo.org"}
TOKENS = {"zenodo": "ZENODO_TOKEN", "zenodo-sandbox": "ZENODO_SANDBOX_TOKEN"}
#: Zenodo's DOI prefixes: 10.5281 for records, 10.5072 for the sandbox's.
_PREFIXES = {"10.5281": "zenodo", "10.5072": "zenodo-sandbox"}
#: Where a study keeps the Zenodo record it was shared as.
SHARED_TO = "shared_to.json"
TIMEOUT_S = 120
UPLOAD_TIMEOUT_S = 900
_AGENT = "FastMDXplora (https://github.com/aai-research-lab/FastMDXplora)"
#: How many times a request is asked before Zenodo's answer is taken.
TRIES = 5
#: Answers that say to ask again: too many requests, or briefly unable.
AGAIN = frozenset({429, 502, 503, 504})
#: The longest wait between two tries, whatever `Retry-After` asks.
_LONGEST_WAIT_S = 60.0
_sleep = time.sleep


def wait_before(attempt: int, answer: Any = None) -> float:
    """How long to wait before try ``attempt + 1`` (``attempt`` counting from
    1), by the answer's headers (or its `Retry-After` alone): the seconds
    `Retry-After` asks, else until the time `X-RateLimit-Reset` gives (Unix
    seconds), else 1, 2, 4, 8 s; never over a minute."""
    headers = {"Retry-After": answer} if isinstance(answer, str) or answer is None \
        else answer

    def number(name: str) -> float | None:
        try:
            found = float(str(headers.get(name) or ""))
        except (TypeError, ValueError):
            return None
        return found if found == found and found >= 0 else None  # NaN is not one

    wait = number("Retry-After")
    if wait is None:
        reset = number("X-RateLimit-Reset")
        if reset is not None and reset > 1e9:
            wait = max(reset - time.time(), 0.0) + 1.0
    if wait is None:
        wait = 2.0 ** (attempt - 1)
    return min(wait, _LONGEST_WAIT_S)


class _Progress:
    """A file read as it is sent, saying how far it has gone at each tenth."""

    def __init__(self, path: Path, said: Callable[[str], None] | None) -> None:
        self.handle = path.open("rb")
        self.size = path.stat().st_size
        self.sent = 0
        self.tenth = 0
        self.said = said

    def read(self, amount: int = -1) -> bytes:
        piece = self.handle.read(amount)
        self.sent += len(piece)
        if self.said and self.size >= 10 << 20:
            tenth = self.sent * 10 // self.size
            if tenth > self.tenth and tenth < 10:
                self.tenth = tenth
                self.said(f"Uploaded {self.sent / 1e6:.1f} of {self.size / 1e6:.1f} MB.")
        return piece

    def close(self) -> None:
        self.handle.close()


def base_url(site: str) -> str:
    return (os.environ.get("FASTMDXPLORA_ZENODO_URL") or SITES[site]).rstrip("/")


def _a_draft(answer: Any, identifier: str = "", doi: str = "") -> bool:
    """Whether an answer is the draft asked for as Zenodo gives one: its id,
    metadata holding something, and the DOI reserved for it where known. A
    draft read wrong is never written back."""
    if not isinstance(answer, dict) or not isinstance(answer.get("metadata"), dict) \
            or not answer["metadata"] or not isinstance(answer.get("submitted"), bool):
        # (Zenodo says of every draft or record whether it is published.)
        return False
    if not isinstance(answer.get("links", {}), dict) \
            or not isinstance(answer.get("files", []), list):
        # Its links and files not in their forms: not as Zenodo gives a draft.
        return False
    if identifier and str(answer.get("id") or "") != str(identifier):
        return False
    reserved = answer["metadata"].get("prereserve_doi")
    held_doi = str(reserved.get("doi") or "") if isinstance(reserved, dict) else ""
    return not (doi and held_doi and held_doi != doi)


#: Quotes Zenodo straightens in what it is sent (as it fixes text it keeps).
#: What Zenodo changes in text it keeps (as ftfy fixes text, run on every
#: title, description and version it is sent): Latin ligatures written out,
#: then quotes straightened and lines broken at line and paragraph
#: separators.
_LIGATURES = str.maketrans({
    "\u0132": "IJ", "\u0133": "ij", "\u0149": "\u02bcn", "\u01f1": "DZ", "\u01f2": "Dz",
    "\u01f3": "dz", "\u01c4": "D\u017d", "\u01c5": "D\u017e", "\u01c6": "d\u017e",
    "\u01c7": "LJ", "\u01c8": "Lj", "\u01c9": "lj", "\u01ca": "NJ", "\u01cb": "Nj",
    "\u01cc": "nj", "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "\u017ft", "\ufb06": "st", "\u3000": " "})
_QUOTES = str.maketrans({
    "\u02bc": "'", "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"'})

#: What ftfy takes out of text as it fixes it: control characters (but
#: tabs, line breaks and form feeds), deprecated format controls, byte-order
#: marks, interlinear annotations.
_CONTROLS = re.compile("[\x00-\x08\x0b\x0e-\x1f\x7f\u206a-\u206f\ufeff\ufff9-\ufffc]")

#: What Zenodo takes out once ftfy has fixed it: what no XML may hold, and
#: zero-width spaces.
_TAKEN_OUT = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\u200b\ufffe\uffff]")

#: A terminal's escape (a colour, say), which Zenodo takes out.
_TERMINAL = re.compile("\x1b\\[((?:\\d|;)*)([a-zA-Z])")

#: An entity Zenodo reads as what it stands for: named, ended by ";".
_ENTITY = re.compile("&#?[0-9A-Za-z]{1,24};")


def _entities() -> dict[str, str]:
    """The entities Zenodo reads (as ftfy does): each HTML5 one ended by
    ";", and those of lower-case names written in capitals where that reads
    as no other."""
    import html.entities

    found = {}
    for name, char in html.entities.html5.items():
        if name.endswith(";"):
            found["&" + name] = char
            if name == name.lower() and html.unescape("&" + name.upper()) == "&" + name.upper():
                found["&" + name.upper()] = char.upper()
    return found


_ENTITIES = _entities()


def _entity(found: re.Match[str]) -> str:
    """An entity as Zenodo reads it: whole, or not at all."""
    named = found.group()
    if named in _ENTITIES:
        return _ENTITIES[named]
    if named.startswith("&#"):
        read = html.unescape(named)
        return named if ";" in read else read
    return named


def _c1(found: re.Match[str]) -> str:
    """A C1 control character as the Windows-1252 one Zenodo reads it as."""
    try:
        return bytes([ord(found.group())]).decode("cp1252")
    except UnicodeDecodeError:
        return found.group()


def _plain(value: Any) -> Any:
    """Text as Zenodo keeps what it is sent, for comparing the two: as ftfy
    fixes it (its composed form, C1 controls read as Windows-1252, quotes
    straightened, ligatures and full-width letters written plainly, some
    control characters taken out, entities read in text holding no markup,
    nothing around it), and nothing else: what it keeps apart (a
    superscript, a soft hyphen) is kept apart here too."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if not isinstance(value, str):
        return value
    # (In ftfy's order, on the text with nothing around it, until nothing
    # more changes; with nothing around what it gives either, so text and
    # what Zenodo kept of it compare alike.)
    # (Line by line, as ftfy reads it: entities read until the first line,
    # as given, holding markup, and never after.)
    lines, reading = [], True
    for fixed in re.split("(?<=\n)", value.strip()):
        reading = reading and "<" not in fixed
        for _ in range(8):
            was = fixed
            if reading:
                fixed = _ENTITY.sub(_entity, fixed)
            fixed = re.sub("[\x80-\x9f]", _c1, fixed).translate(_LIGATURES)
            fixed = re.sub("[\uff01-\uffee]",
                           lambda c: unicodedata.normalize("NFKC", c.group()), fixed)
            fixed = fixed.translate(_QUOTES).replace("\r\n", "\n").replace("\r", "\n")
            fixed = fixed.replace("\u2028", "\n").replace("\u2029", "\n")
            # (A lone surrogate as the character ftfy puts in its place.)
            fixed = re.sub("[\ud800-\udfff]", "\ufffd", fixed)
            # (A terminal's escapes go once the rest is fixed, as ftfy takes them.)
            fixed = unicodedata.normalize("NFC", _CONTROLS.sub("", _TERMINAL.sub("", fixed)))
            if fixed == was:
                break
        lines.append(fixed)
    return unicodedata.normalize("NFC", _TAKEN_OUT.sub("", "".join(lines))).strip()


#: Licences by the ids Zenodo takes them as, where these are not the ids it
#: is given (its aliases, then its earlier ids), as its own code maps them:
#: one sent in one form may come back in another.
_LICENCES_ONCE = {
    "agpl-1.0": "agpl-1.0-only", "agpl-3.0": "agpl-3.0-only", "agpl-v3": "agpl-3.0-only",
    "apache": "apache-2.0", "apache2.0": "apache-2.0", "apgl-v3": "agpl-3.0-only",
    "apl1.0": "apl-1.0", "artistic-license-2.0": "artistic-2.0", "attribution": "aal",
    "bsd-2-clause-freebsd": "bsd-2-clause-views", "bsd-2-clause-netbsd": "bsd-2-clause",
    "bsd-license": "bsd-1-clause", "bsl1.0": "bsl-1.0", "ca-tosl1.1": "catosl-1.1",
    "canada-crown": "ogl-canada-2.0", "cc-by": "cc-by-4.0", "cc-by-sa": "cc-by-sa-4.0",
    "cc-nc": "cc-by-nc-2.0", "cc-zero": "cc0-1.0", "cddl1": "cddl-1.0", "cpal_1.0": "cpal-1.0",
    "cuaoffice": "cua-opl-1.0", "ecl2": "ecl-2.0", "eclipse-1.0": "epl-1.0",
    "eiffel": "efl-2.0", "fal": "fal-1.3", "frameworx": "frameworx-1.0",
    "gfdl": "gfdl-1.3-no-cover-texts-no-invariant-sections", "gfdl-1.1": "gfdl-1.1-only",
    "gfdl-1.2": "gfdl-1.2-only", "gfdl-1.3": "gfdl-1.3-only", "gpl-1.0": "gpl-1.0-only",
    "gpl-1.0+": "gpl-1.0-or-later", "gpl-2.0": "gpl-2.0-only", "gpl-2.0+": "gpl-2.0-or-later",
    "gpl-3.0": "gpl-3.0-only", "gpl-3.0+": "gpl-3.0-or-later", "ibmpl": "ipl-1.0",
    "intel-osl": "intel", "ipafont": "ipa", "isc-license": "isc", "lgpl-2.0": "lgpl-2.0-only",
    "lgpl-2.0+": "lgpl-2.0-or-later", "lgpl-2.1": "lgpl-2.1-only",
    "lgpl-2.1+": "lgpl-2.1-or-later", "lgpl-3.0": "lgpl-3.0-only",
    "lgpl-3.0+": "lgpl-3.0-or-later", "lucent1.02": "lpl-1.02", "mit-license": "mit",
    "mozilla": "mpl-1.0", "mozilla1.1": "mpl-1.1", "nasa1.3": "nasa-1.3", "nethack": "ngpl",
    "nosl3.0": "nposl-3.0", "ntp-license": "ntp", "oclc2": "oclc-2.0", "odc-by": "odc-by-1.0",
    "odc-odbl": "odbl-1.0", "odc-pddl": "pddl-1.0", "odc-pddl-1.0": "pddl-1.0",
    "openfont": "ofl-1.1", "opengroup": "ogtsl", "php": "php-3.0", "pythonpl": "cnri-python",
    "pythonsoftfoundation": "python-2.0", "qtpl": "qpl-1.0", "real": "rpsl-1.0",
    "ricohpl": "rscpl", "rpl1.5": "rpl-1.5", "standardml-nj": "smlnj", "sun-issl": "sissl",
    "sunpublic": "spl-1.0", "sybase": "watcom-1.0", "uk-ogl": "ogl-uk-1.0", "uoi-ncsa": "ncsa",
    "ver2_eiffel": "efl-2.0", "vovidapl": "vsl-1.0", "zlib-license": "zlib", "zpl": "zpl-2.0"
}


def _licence(given: Any) -> str:
    """A licence's id as Zenodo takes it, whichever form it was given in."""
    named = str((given.get("id") if isinstance(given, dict) else given) or "").strip().lower()
    return _LICENCES_ONCE.get(named, named)


def _as_kept(value: Any) -> Any:
    """A description's fields in the forms Zenodo gives them back in, for
    comparing what was sent with what it holds: its text as it keeps text,
    a licence by its id now, and no field it fills with nothing (a
    creator's affiliation given none)."""
    if isinstance(value, dict):
        # (A description's entities read on both sides: Zenodo writes one
        # for what its fixing turns into markup, a full-width "<" say.)
        return {k: _licence(v) if k == "license" else _plain(html.unescape(v))
                if k == "description" and isinstance(v, str) else _as_kept(v)
                for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_as_kept(v) for v in value]
    return _plain(value)


def _a_listing(answer: Any) -> bool:
    """Whether an answer is a listing of drafts as Zenodo gives one: each
    entry a deposition, with its id and metadata (else the drafts in it are
    not known, and one may be there unread)."""
    return isinstance(answer, list) and all(
        isinstance(entry, dict) and entry.get("id") and isinstance(entry.get("metadata"), dict)
        and entry["metadata"] for entry in answer)


def _text(described: Any) -> str:
    """A description's words, whatever markup Zenodo gives them back in."""
    plain = html.unescape(re.sub(r"<[^>]*>", " ", str(described or "")))
    return " ".join(_plain(plain).split())


def _zenodo_s(answer: str, code: int) -> bool:
    """Whether an error's body is Zenodo's own answer: a JSON object giving
    its status or a message (as its API documents), not a gateway's page."""
    try:
        said_back = json.loads(answer)
    except ValueError:
        return False
    return isinstance(said_back, dict) and (said_back.get("status") in (code, str(code))
                                             or bool(said_back.get("message")))


def _call(method: str, url: str, *, token: str = "", body: Any = None,
          upload: Path | None = None, missing_ok: bool = False,
          said: Callable[[str], None] | None = None, tries: int = TRIES,
          timeout: float | None = None, gone: Any = None) -> Any:
    """Zenodo's answer as JSON (or None for an empty one), asked again where
    Zenodo says to (:data:`AGAIN`) or could not be reached; refused, naming
    what Zenodo said, where it does not answer as it documents."""
    headers = {"User-Agent": _AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    sent: bytes | None = None
    if body is not None:
        sent = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if upload is not None:
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(upload.stat().st_size)
    # Asking again to make something could make it twice: only once Zenodo
    # has said it did nothing (429).
    makes = method == "POST"
    attempt = 0
    while True:
        attempt += 1
        data: Any = _Progress(upload, said) if upload is not None else sent
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            # Zenodo answers an upload once it has kept the file, which for
            # gigabytes takes longer than an answer otherwise does.
            with urllib.request.urlopen(request, timeout=timeout or (
                    TIMEOUT_S if upload is None else UPLOAD_TIMEOUT_S)) as answer:
                text = answer.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            if missing_ok and exc.code in (404, 410):
                try:
                    said_back = exc.read().decode("utf-8", errors="replace")
                except (OSError, http.client.HTTPException):
                    said_back = ""
                if not _zenodo_s(said_back, exc.code):
                    # A gateway's page, not Zenodo's own answer: not known.
                    unknown = ShareRefused(
                        f"Zenodo answered {method} {url} with HTTP {exc.code} in a page not "
                        "its own (a gateway's): whether it is there is not known.",
                        code="environment.service.unreachable", url=url)
                    unknown.later = True
                    raise unknown from exc
                return gone
            if exc.code in AGAIN and attempt < tries and (not makes or exc.code == 429):
                _again(f"Zenodo answered {method} {url} with HTTP {exc.code}", attempt,
                       exc.headers, said)
                continue
            try:
                answered = exc.read().decode("utf-8", errors="replace")[:400]
            except (OSError, http.client.HTTPException):
                # The answer's own words cut short: said without them.
                answered = ""
            why = {401: "the token was refused (make one with the deposit:write scope, and "
                        "give it in ZENODO_TOKEN, or ZENODO_SANDBOX_TOKEN for the sandbox)",
                   403: "the token may not do this (its scopes: deposit:write, and "
                        "deposit:actions for a new version of a published record)"} \
                if token else {401: "it is not open to be read (restricted, or not "
                                    "published yet: ask its owner for access, or download "
                                    "its zip and open the file)",
                               403: "it is not open to be read (restricted, or not "
                                    "published yet: ask its owner for access, or download "
                                    "its zip and open the file)"}
            why = {**why,
                   404: "no such record or draft" if _zenodo_s(answered, 404)
                   else "HTTP 404 in a page not Zenodo's own (a gateway's)",
                   429: f"too many requests, still after {attempt} tries"}.get(
                exc.code, f"HTTP {exc.code}" + (f", still after {attempt} tries"
                                                 if exc.code in AGAIN and attempt > 1 else ""))
            refused = ShareRefused(f"Zenodo answered {method} {url}: {why}. {answered}".strip(),
                                   code="environment.service.unreachable", url=url)
            refused.status = exc.code
            # Said by Zenodo itself (a gateway's page is not).
            refused.own = _zenodo_s(answered, exc.code)
            refused.later = exc.code in AGAIN or exc.code >= 500 \
                or (exc.code in (404, 410) and not refused.own)
            # Zenodo, or the gateway before it, gave up part way: what was
            # asked may have been done.
            refused.unclear = exc.code >= 500
            raise refused from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if attempt < tries and not makes:
                _again(f"The connection to Zenodo at {url} was lost or not made ({exc})",
                       attempt, None, said)
                continue
            refused = ShareRefused(f"The connection to Zenodo at {url} was lost or not made: "
                                   f"{exc}" + (f", still after {attempt} tries" if attempt > 1
                                               else "") + ".",
                                   code="environment.service.unreachable", url=url)
            # Failing as the request was made or sent (urllib says so as a
            # URLError: refused, no such host or network, TLS, a timeout
            # connecting): nothing was done. Lost once it had gone, waiting
            # for or reading the answer: it may have been.
            refused.unclear = not isinstance(exc, urllib.error.URLError)
            refused.later = True
            raise refused from exc
        finally:
            if isinstance(data, _Progress):
                data.close()
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        refused = ShareRefused(f"Zenodo's answer to {method} {url} was not JSON.",
                               code="environment.service.unreachable", url=url)
        # A gateway's page in place of Zenodo's answer: what was asked may
        # have been done, and asking later may be answered.
        refused.unclear = refused.later = True
        raise refused from exc


# -- reading a record --------------------------------------------------------

def _again(what: str, attempt: int, answer: Any,
           said: Callable[[str], None] | None) -> None:
    wait = wait_before(attempt, answer)
    if said:
        said(f"{what}; asking again in {wait:.0f} s ({attempt + 1} of {TRIES}).")
    _sleep(wait)


def record_of(source: str) -> tuple[str, str]:
    """The site and record a DOI or a Zenodo address names; refused for any
    other (`environment.share.not_a_study`)."""
    text = source.strip()
    text = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:https?://)?(?:www\.|sandbox\.)?zenodo\.org/doi/", "", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"^doi:", "", text, flags=re.IGNORECASE)
    found = re.fullmatch(r"(10\.\d{4,9})/zenodo\.(\d+)", text, flags=re.IGNORECASE)
    if found and found.group(1) in _PREFIXES:
        return _PREFIXES[found.group(1)], found.group(2)
    url = urllib.parse.urlparse(source.strip())
    host = (url.hostname or "").lower().removeprefix("www.")
    here = urllib.parse.urlparse(os.environ.get("FASTMDXPLORA_ZENODO_URL") or "")
    sites = {urllib.parse.urlparse(v).hostname: k for k, v in SITES.items()}
    if here.hostname and host == here.hostname:
        sites[host] = "zenodo"
    # A published record's address; a draft's (/uploads/N) opens only for
    # its owner, and is published first.
    numbered = re.search(r"/records?/(\d+)", url.path or "")
    if host in sites and numbered:
        return sites[host], numbered.group(1)
    raise ShareRefused(
        f"{source!r} is not a file here, a Zenodo DOI (10.5281/zenodo.N) or a Zenodo "
        "address (https://zenodo.org/records/N).", code="environment.share.not_a_study")


def archive_of(site: str, record: str,
               said: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The record's archive: its `name`, `size`, `md5`, the `url` it is
    downloaded from, and the record's `doi`, `id` and `version`."""
    base = base_url(site)
    found = _call("GET", f"{base}/api/records/{record}", said=said, missing_ok=True,
                  gone=_GONE)
    if found is _GONE:
        raise ShareRefused(f"Zenodo has no published record {record}: look at the DOI or "
                           "address given (a draft's reserved DOI opens once it is published).",
                           code="environment.share.not_a_study")
    if not isinstance(found, dict):
        # Answered, but with no record: a gateway's, perhaps, not Zenodo's.
        unread = ShareRefused(f"Zenodo's answer for record {record} was no record.",
                              code="environment.service.unreachable")
        unread.later = True
        raise unread
    if not found.get("id") and not found.get("files"):
        unread = ShareRefused(f"Zenodo's answer for record {record} was no record.",
                              code="environment.service.unreachable")
        unread.later = True
        raise unread
    parent = found.get("parent") if isinstance(found.get("parent"), dict) else {}
    if found.get("id") and str(record) not in (
            str(found["id"]), str(found.get("conceptrecid") or parent.get("id") or "")):
        # Another record's answer (a concept's is its latest version's).
        other = ShareRefused(f"Zenodo's answer for record {record} named another, "
                             f"{found['id']}.", code="environment.service.unreachable")
        other.later = True
        raise other
    if found.get("id") and str(record) != str(found["id"]) and _not_the_last(found):
        # A concept's answer naming a version before its last.
        older = ShareRefused(f"Zenodo's answer for record {record} named version "
                             f"{found['id']}, not the latest.",
                             code="environment.service.unreachable")
        older.later = True
        raise older
    entries = found.get("files") or []
    if isinstance(entries, dict):
        entries = entries.get("entries") or []
        entries = list(entries.values()) if isinstance(entries, dict) else entries
    zips = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("key") or entry.get("filename") or "")
        if name.lower().endswith(".zip"):
            checksum = str(entry.get("checksum") or "")
            try:
                size = int(entry.get("size") or entry.get("filesize") or 0)
            except (TypeError, ValueError):
                size = 0
            zips.append({"name": name, "size": size,
                         "md5": checksum.split(":", 1)[-1].lower()})
    if len(zips) != 1:
        raise ShareRefused(
            f"Zenodo's record {record} holds {len(zips)} zip files"
            + (", so there is no shared study to open." if not zips else
               f" ({', '.join(z['name'] for z in zips)}): download the study's and open "
               "the file."), code="environment.share.not_a_study")
    archive = zips[0]
    identifier = str(found.get("id") or record)
    # The address Zenodo serves a record's files at; the API's own links
    # have been found to move.
    archive["url"] = (f"{base}/records/{identifier}/files/"
                      f"{urllib.parse.quote(archive['name'])}?download=1")
    metadata = found.get("metadata") if isinstance(found.get("metadata"), dict) else {}
    archive.update(doi=str(found.get("doi") or metadata.get("doi") or ""), id=identifier,
                   version=str(metadata.get("version") or ""), site=site)
    return archive


# -- making a draft ----------------------------------------------------------

#: How long one look among the person's drafts may take.
_LOOK_TIMEOUT_S = 20.0


def _marked(depositions: str, token: str, title: str,
            said: Callable[[str], None] | None, *, tries: int = 1) -> list[str] | None:
    """The ids of the person's drafts titled ``title``, the title a new
    draft is asked for with; None where Zenodo did not list them (in one
    try by default, soon given up: the looks are timed by their caller)."""
    try:
        found = _call("GET", f"{depositions}?status=draft&sort=mostrecent&size=100",
                      token=token, missing_ok=True, said=said, tries=tries,
                      timeout=_LOOK_TIMEOUT_S if tries == 1 else None)
    except ShareRefused as exc:
        if not getattr(exc, "later", False):
            # The token refused, say: no waiting mends it.
            raise
        return None
    if not _a_listing(found):
        return None
    return [str(entry.get("id")) for entry in found
            if _plain(entry["metadata"].get("title")) == _plain(title)]


#: The waits before each look among the person's drafts for one whose answer
#: was lost: Zenodo may finish making it after its gateway has given up.
_LOOKS_S = (0.0, 2.0, 4.0, 8.0, 16.0)


def _taken(depositions: str, token: str, title: str, made: list[str],
           said: Callable[[str], None] | None) -> dict[str, Any] | None:
    """The one draft titled ``title``; refused where there are several; None
    where it is gone since it was listed."""
    if len(made) > 1:
        raise ShareRefused(
            f"Zenodo holds {len(made)} drafts titled {title!r} ({', '.join(made)}), made by "
            "shares whose answers were lost. Delete all but one among your uploads, then "
            "share again.", code="environment.service.unusable_response")
    if said:
        said(f"Zenodo made draft {made[0]}, asked for by this study; taking that one.")
    try:
        found = _call("GET", f"{depositions}/{made[0]}", token=token, said=said,
                      missing_ok=True, gone=_GONE)
    except ShareRefused as exc:
        raise ShareRefused(f"{exc} Draft {made[0]} is as it was; share again once Zenodo "
                           "answers, and it is taken then.", code=exc.refusal.code) from exc
    if found is _GONE:
        if said:
            said(f"Draft {made[0]} is gone from Zenodo since it was listed.")
        return None
    if not _a_draft(found, made[0]):
        unread = ShareRefused(f"Zenodo's answer for draft {made[0]} was no draft. It is as "
                              "it was; share again once Zenodo answers, and it is taken then.",
                              code="environment.service.unreachable")
        unread.later = True
        raise unread
    return found


def _reserved_for(draft: dict[str, Any]) -> str:
    """The record the DOI reserved in a draft's answer is for (its `recid`,
    else its number), or "" where it names none."""
    reserved = (draft.get("metadata") or {}).get("prereserve_doi")
    if not isinstance(reserved, dict):
        return ""
    numbered = re.search(r"zenodo\.(\d+)$", str(reserved.get("doi") or ""))
    return str(reserved.get("recid") or (numbered.group(1) if numbered else ""))


def _new_draft(depositions: str, token: str, title: str,
               said: Callable[[str], None] | None) -> tuple[dict[str, Any], bool]:
    """A new draft titled ``title``, made once: where Zenodo's answer to
    making it is lost and it may have been made, the draft of that title is
    looked for and taken; never one of another title. An answer naming
    another deposition is taken as lost: the draft it may have made is
    looked for the same way."""
    try:
        made = _call("POST", depositions, token=token,
                     body={"metadata": {"prereserve_doi": True, "title": title}},
                     said=said)
        if not isinstance(made, dict) or not made.get("id") or (
                _a_draft(made) and not made.get("submitted") and not made.get("files")
                and _plain(made["metadata"].get("title")) == _plain(title)
                and _reserved_for(made) in ("", str(made["id"]))):
            # The new draft; or none named, refused as such by the caller.
            return made, True
        other = ShareRefused(f"Zenodo's answer to making the draft (titled {title!r}) named "
                             "another, or another record's DOI.",
                             code="environment.service.unreachable")
        other.unclear = other.later = True
        raise other
    except ShareRefused as exc:
        if not getattr(exc, "unclear", False):
            if getattr(exc, "later", False):
                raise ShareRefused(f"{exc} Nothing was made; share again once Zenodo answers.",
                                   code=exc.refusal.code) from exc
            raise
        for wait in _LOOKS_S:
            if wait:
                if said:
                    said(f"Zenodo's answer was lost; looking again in {wait:.0f} s for the "
                         "draft it may have made.")
                _sleep(wait)
            made = _marked(depositions, token, title, said)
            taken = _taken(depositions, token, title, made, said) if made else None
            if taken is not None:
                return taken, False
        raise ShareRefused(
            f"{exc} Zenodo may still make the draft (titled {title!r}): sharing again looks "
            "for it first and finishes it; one it makes later still is named for you to "
            "delete.",
            code=exc.refusal.code) from exc


#: What Zenodo itself sets in a draft's description, not what anyone wrote
#: there: told apart from both what the study wrote and what the person did.
_SET_BY_ZENODO = frozenset({"doi", "prereserve_doi", "publication_date", "version"})


def _not_the_last(record: Any) -> bool:
    """Whether a record's answer does not say it is its concept's last
    version (as Zenodo's records each say, in their relations): one that
    says nothing of it is not taken as the last."""
    metadata = record.get("metadata") if isinstance(record, dict) else None
    relations = (metadata.get("relations") or {}) if isinstance(metadata, dict) else {}
    versions = relations.get("version") if isinstance(relations, dict) else None
    first = versions[0] if isinstance(versions, list) and versions else None
    return not (isinstance(first, dict) and first.get("is_last") is True)


def _latest_published(found: dict[str, Any], depositions: str, token: str,
                      said: Callable[[str], None] | None) -> dict[str, Any]:
    """The latest published version of the record ``found`` is a version of,
    as a deposition (as its drafts are read): by its `latest` link, which
    names it as a record; ``found`` itself where it names none. A record
    read naming no concept (as Zenodo gives every one) is refused: no
    version of it, nor of another, is told from it."""
    concept = str(found.get("conceptrecid") or "")
    if not concept:
        unnamed = ShareRefused(f"Zenodo's answer for record {found.get('id')} named no "
                               "concept.", code="environment.service.unreachable")
        unnamed.later = True
        raise unnamed
    link = str((found.get("links") or {}).get("latest") or "")
    if not link:
        return found
    record = _call("GET", link, token=token, missing_ok=True, said=said, gone=_GONE)
    if record is _GONE and not found.get("submitted"):
        # A draft's concept never published: no version of it yet.
        return found
    if record is _GONE:
        # A published record's latest version not found: not known.
        unknown = ShareRefused(f"Zenodo found no latest version at {link}.",
                               code="environment.service.unreachable")
        unknown.later = True
        raise unknown
    newest = str(record.get("id") or "") if isinstance(record, dict) else ""
    if not newest:
        # An answer naming no version: the latest is not known, and a new
        # version begun from another would be counted wrong.
        unnamed = ShareRefused(f"Zenodo's answer for {link} named no record.",
                               code="environment.service.unreachable")
        unnamed.later = True
        raise unnamed
    if _not_the_last(record) or (found.get("submitted") and newest.isdigit()
                                 and str(found.get("id") or "").isdigit()
                                 and int(newest) < int(found["id"])):
        # A version before another: the latest is not known from it (Zenodo
        # numbers each version after the one before).
        older = ShareRefused(f"Zenodo's answer for {link} named record {newest}, not the "
                             "latest version.", code="environment.service.unreachable")
        older.later = True
        raise older
    if newest == str(found.get("id")):
        return found
    deposition = _call("GET", f"{depositions}/{newest}", token=token, missing_ok=True,
                       said=said)
    if not _a_draft(deposition, newest) or not deposition.get("submitted") \
            or str(deposition.get("conceptrecid") or "") != concept:
        unread = ShareRefused(f"Zenodo's answer for the latest version, {newest}, was no "
                              "record.", code="environment.service.unreachable")
        unread.later = True
        raise unread
    return deposition


def _counted_past(draft: dict[str, Any], depositions: str, token: str,
                  said: Callable[[str], None] | None, version: str, record: str) -> str:
    """The label of a draft of a new version, not yet published: ``version``,
    counted from ``record``, unless a version was published since (by hand,
    as the share began this draft), then counted on from that one, as a new
    version is. Where the latest is not read, as it was."""
    base = depositions.rsplit("/api/", 1)[0]
    asked = {**draft, "links": {**(draft.get("links") or {}),
                                "latest": f"{base}/api/records/{draft['conceptrecid']}"}}
    try:
        newest = _latest_published(asked, depositions, token, said)
    except ShareRefused:
        return version
    newest_id = str(newest.get("id"))
    if not newest.get("submitted") or newest_id in (str(draft.get("id")), str(record)):
        return version
    if newest_id.isdigit() and str(record).isdigit() and int(newest_id) < int(record):
        # Older than the record it was counted from (an answer kept from
        # before): no version published since.
        return version
    # Where its label holds no number, the draft's own, as a new version's.
    return _bumped(str((newest.get("metadata") or {}).get("version") or "")) or version


def _concept_drafts(depositions: str, token: str, record: dict[str, Any],
                    said: Callable[[str], None] | None) -> list[dict[str, Any]] | None:
    """The person's drafts of a new version of ``record`` (the drafts of its
    concept); None where Zenodo did not list them."""
    concept = str(record.get("conceptrecid") or "")
    if not concept:
        return []
    try:
        found = _call("GET", f"{depositions}?status=draft&sort=mostrecent&size=100",
                      token=token, missing_ok=True, said=said)
    except ShareRefused as exc:
        if not getattr(exc, "later", False):
            raise
        return None
    if not _a_listing(found):
        return None
    # (A version published, under an edit saved there, is listed too: no
    # draft of a new version.)
    return [entry for entry in found if str(entry.get("conceptrecid") or "") == concept
            and not entry.get("submitted")
            and str(entry.get("id")) != str(record.get("id"))]


def _described(metadata: Any) -> dict[str, Any]:
    return {k: v for k, v in (metadata if isinstance(metadata, dict) else {}).items()
            if k not in _SET_BY_ZENODO}


def _written(metadata: Any) -> str:
    """A draft's description as Zenodo holds it, as one digest: what this
    study wrote, told from what the person changed there since."""
    import hashlib

    return hashlib.sha256(json.dumps(_as_kept(_described(metadata)), sort_keys=True)
                          .encode()) \
        .hexdigest()


#: What the study sends as a draft's description (:func:`_metadata`), less
#: what Zenodo sets.
_SENT = ("upload_type", "title", "description", "creators", "access_right", "license",
         "keywords", "related_identifiers")


#: The description an earlier release wrote, of its own words throughout.
_EARLIER_WROTE = re.compile(
    r"<p>[^<]*</p><p>A molecular dynamics study packed by FastMDXplora \S* as an RO-Crate "
    r"\(ro-crate-metadata\.json lists every file with its SHA-256\)\. Open it with "
    r"<code>fastmdx gui --open &lt;this record's DOI&gt;</code>\.</p>", re.DOTALL)

#: What only someone writes in a draft's description: a draft holding none
#: of it is as Zenodo made it.
_SOMEONE_S = ("description", "creators", "contributors", "keywords", "notes", "references",
              "related_identifiers", "subjects", "communities", "grants", "embargo_date",
              "access_conditions")

#: Choices Zenodo may give a draft as it makes it, which the person may
#: change: changed, and not to what the study sends, the draft is theirs.
_MADE_WITH = ("access_right", "upload_type", "license")

#: What Zenodo documents it gives a draft it makes, for a draft found by its
#: title, whose making the study did not see.
_ZENODO_GIVES = {"access_right": ("open",), "license": ("cc-by", "cc-zero")}


def _sent(metadata: Any) -> str:
    """What the study sends as a draft's description, as one digest of those
    fields alone: a draft written whose answer was lost is told by it, one
    the person changed since is not, and a field Zenodo adds or the person
    sets that the study does not send (carried over as it is) does not
    count."""
    held = metadata if isinstance(metadata, dict) else {}
    return _written({key: held.get(key) for key in _SENT})


def _bumped(label: str) -> str:
    """The version after ``label``, of its own form: its last number one
    more ("1.0" gives "1.1", "v5" gives "v6", "2.1.5" gives "2.1.6");
    "" where it holds no number."""
    found = list(re.finditer(r"\d+", label))
    if not found:
        return ""
    last = found[-1]
    return f"{label[:last.start()]}{int(last.group()) + 1}{label[last.end():]}"


def _md5(checksum: Any) -> str:
    """A checksum as Zenodo gives it ("md5:<hex>", or the hex alone), as hex."""
    return str(checksum or "").rsplit(":", 1)[-1].lower()


def _sums_kept(kept: Any) -> dict[str, list[str]]:
    """Each archive name's checksums as kept (one, or several), as hex."""
    if not isinstance(kept, dict):
        return {}
    return {str(name): list(dict.fromkeys(_md5(v) for v in (sums if isinstance(sums, list)
                                                            else [sums]) if v))
            for name, sums in kept.items() if sums}


def _known(*kept: Any) -> dict[str, list[str]]:
    """Every checksum known for each name, from each of ``kept``."""
    known: dict[str, list[str]] = {}
    for one in kept:
        for name, sums in _sums_kept(one).items():
            known[name] = list(dict.fromkeys([*known.get(name, []), *sums]))
    return known


def _md5_of(path: Path) -> str:
    import hashlib

    digest = hashlib.md5()  # noqa: S324 (Zenodo's own checksum)
    with path.open("rb") as source:
        for piece in iter(lambda: source.read(1 << 20), b""):
            digest.update(piece)
    return digest.hexdigest()


def _names_kept(kept: Any) -> list[str]:
    """Archive names as kept (one, or several), in order, each once."""
    names = [kept] if isinstance(kept, str) else kept if isinstance(kept, list) else []
    return list(dict.fromkeys(str(n) for n in names if n))


#: How many descriptions sent whose answers were lost are remembered.
_SENDINGS = 1000


def _sendings(kept: Any) -> list[str]:
    """The digests sent whose answers were lost, as kept (one, or several)."""
    if isinstance(kept, str):
        return [kept] if kept else []
    return [str(k) for k in kept or [] if k]


def _names(entry: dict[str, Any]) -> set[str]:
    return {str(f.get("filename") or f.get("key") or "") for f in entry.get("files") or []
            if isinstance(f, dict)} - {""}


def _changed_since(record: dict[str, Any], draft: dict[str, Any]) -> str:
    """What a draft of a new version holds that the version it was begun
    from does not: said (its title, its creators, its files...), or ""
    where the draft is as Zenodo began it."""
    was, now = _described(record.get("metadata")), _described(draft.get("metadata"))
    said = [f"its {key}" for key in sorted(set(was) | set(now)) if was.get(key) != now.get(key)]
    if _contents(draft) != _contents(record):
        said.append("its files")
    return ", ".join(said)


def _contents(entry: dict[str, Any]) -> set[tuple[str, str]]:
    """A draft's or record's files, each by its name and checksum."""
    return {(str(f.get("filename") or f.get("key") or ""), str(f.get("checksum") or ""))
            for f in entry.get("files") or [] if isinstance(f, dict)} - {("", "")}


def _creators(authors: list[tuple[str, str]]) -> list[dict[str, str]]:
    out = []
    for name, orcid in authors:
        parts = name.split()
        # Given as Zenodo writes a name ("Family, Given"): sent as it keeps it.
        family, comma, given = name.partition(",")
        said = ", ".join(part for part in (family.strip(), given.strip()) if part) \
            if comma and "," not in given else name.strip() if comma else \
            f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name
        creator = {"name": said}
        if orcid:
            creator["orcid"] = orcid
        out.append(creator)
    return out


def _metadata(crate: dict[str, Any], *, authors: list[tuple[str, str]], license_id: str,
              system: str, version: str) -> dict[str, Any]:
    root = next(e for e in crate["@graph"] if e.get("@id") == "./")
    software = next((e for e in crate["@graph"] if e.get("@id") == "#fastmdxplora"), {})
    # The summary in the form Zenodo keeps it, then made safe as markup: its
    # fixing then makes no markup of it (a full-width "<" before a letter).
    description = (f"<p>{html.escape(_plain(str(root.get('description', ''))))}</p>"
                   f"<p>A molecular dynamics study packed by FastMDXplora "
                   f"{software.get('version', '')} as an RO-Crate (ro-crate-metadata.json "
                   "lists every file with its SHA-256). Open it with "
                   "<code>fastmdx gui --open &lt;this record's DOI&gt;</code>.</p>")
    related = [{"identifier": "https://github.com/aai-research-lab/FastMDXplora",
                "relation": "isCompiledBy", "scheme": "url", "resource_type": "software"}]
    if re.fullmatch(r"[0-9][A-Za-z0-9]{3}", system or ""):
        related.append({"identifier": f"10.2210/pdb{system.lower()}/pdb",
                        "relation": "isDerivedFrom", "scheme": "doi"})
    keywords = ["molecular dynamics", "FastMDXplora", "RO-Crate"]
    if system:
        keywords.insert(0, system)
    return {"upload_type": "dataset", "title": str(root.get("name") or ""),
            "description": description, "creators": _creators(authors),
            "access_right": "open", "license": license_id.lower(), "keywords": keywords,
            "related_identifiers": related, "version": str(version),
            "prereserve_doi": True}


def _not_a_copy(study: Path, kept: dict[str, Any]) -> None:
    """The study's `shared_to.json` its own, not one copied with a study it
    was copied from: refused where the folder it names still holds the same
    draft or record. (A study moved goes on: its folder is gone.) The folder
    is then kept as this one's."""
    here = str(study.resolve())
    recorded = kept.get("folder") if isinstance(kept.get("folder"), str) else ""
    named = _named_in(kept)
    if recorded and recorded != here and named:
        there = Path(recorded)
        try:
            # The same folder reached by another way (a bind mount, say).
            same = there.is_dir() and there.samefile(study)
            theirs = {} if same or not there.is_dir() else _load(there / SHARED_TO)
        except OSError:
            # Not to be looked into (another's, or gone stale): not there.
            same, theirs = False, {}
        both = named & _named_in(theirs) if not same else set()
        if both:
            # The draft or record itself where both name it, before its concept.
            where, key, which = min((b.split(":", 2) for b in both),
                                    key=lambda b: _SAID_FIRST.index(b[1]))
            named_as = {"id": f"the draft or record {which}", "record": f"the record {which}",
                        "asked_as": f'the draft asked for as "{which}"',
                        "concept": f"versions of the record {which}"}[key]
            raise ShareRefused(
                f"{study / SHARED_TO} is the one of the study at {there}, copied with it: "
                f"both name {named_as} on {where}, and sharing this "
                f"one would write over that one's. To share this study as one of its own, "
                f"delete {study / SHARED_TO} and share again; to go on with that one, share "
                "it from its own folder (or, for this one to carry on its draft or record instead, "
                f"move {there / SHARED_TO} over {study / SHARED_TO}).", code="environment.share.not_finished",
                path=str(study / SHARED_TO))
    kept["folder"] = here


# What a `shared_to.json` names a draft or record by: its id, its record's,
# the title a draft was asked for under (its answer lost), the title its
# first draft was asked for under (kept through every version), its concept's.
_NAMED = ("id", "record", "asked_as", "asked_first", "concept")
# Named in a refusal in this order: what the person finds on Zenodo first (a
# title asked for is the draft's only until the share describes it).
_SAID_FIRST = ("id", "record", "concept", "asked_as")


def _named_in(kept: dict[str, Any]) -> set[str]:
    """Every draft and record a `shared_to.json` names, on whichever site."""
    # (A title asked for is one, kept as the last asked or as the first.)
    return {f"{site}:{'asked_as' if key == 'asked_first' else key}:{entry.get(key)}"
            for site, entry in kept.items()
            if isinstance(entry, dict) for key in _NAMED if entry.get(key)}


def _load(path: Path) -> dict[str, Any]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _keep(study: Path, kept: dict[str, Any], *, found_again: bool = True) -> None:
    """``kept`` written as the study's `shared_to.json`; a disk with no room
    for it refuses, before anything more is asked of Zenodo, saying (where
    ``found_again``, the draft named or asked for in it already) that the
    draft is found again then."""
    temporary = study / f".{SHARED_TO}.{os.getpid()}.tmp"
    try:
        # Whole or not at all: emptied, it would forget the record it names.
        temporary.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, study / SHARED_TO)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        if exc.errno not in (errno.ENOSPC, errno.EDQUOT):
            raise
        raise ShareRefused(
            f"{study / SHARED_TO}, where the study keeps the record it is shared as, could "
            f"not be written: the disk has no room ({exc.strerror}). Make room there and "
            "share again" + (": a draft on Zenodo made for it is found and finished "
                             "then, not made again." if found_again else "."),
            code="environment.share.too_large", path=str(study / SHARED_TO)) from exc


def draft(study: Path, archive: Path, *, site: str, pack: Callable[[str], dict[str, Any]],
          authors: list[tuple[str, str]], license_id: str, system: str,
          said: Callable[[str], None] | None = None) -> dict[str, Any]:
    """As :func:`_draft`, one share of a study at a time: two at once would
    each make a draft, neither knowing the other's."""
    try:
        import fcntl
    except ImportError:  # Windows: no lock, as before.
        return _draft(study, archive, site=site, pack=pack, authors=authors,
                      license_id=license_id, system=system, said=said)
    lock_path = study / f".{SHARED_TO}.lock"
    try:
        held = lock_path.open("a")
    except OSError:
        # A study that cannot be written is said so where it is written.
        return _draft(study, archive, site=site, pack=pack, authors=authors,
                      license_id=license_id, system=system, said=said)
    with held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                # A disk that keeps no locks (some cluster and network ones):
                # shared as where there are none.
                return _draft(study, archive, site=site, pack=pack, authors=authors,
                              license_id=license_id, system=system, said=said)
            raise ShareRefused(
                f"{study} is being shared already, by another command: share it again once "
                "that one is done.", code="environment.share.not_finished",
                path=str(study)) from exc
        return _draft(study, archive, site=site, pack=pack, authors=authors,
                      license_id=license_id, system=system, said=said)


def _draft(study: Path, archive: Path, *, site: str, pack: Callable[[str], dict[str, Any]],
           authors: list[tuple[str, str]], license_id: str, system: str,
           said: Callable[[str], None] | None = None) -> dict[str, Any]:
    """A draft on Zenodo of the study packed by ``pack``, which is given the
    DOI the draft reserved (or "") and returns what :func:`share_study`
    does; the draft's `url`, `doi` and `id`. Never published.

    The study is packed once before Zenodo is asked anything, so a study
    that cannot be shared leaves no draft behind; the draft is recorded in
    `shared_to.json` as soon as it exists, so a failure after leaves a
    draft the next share makes again rather than another one; and a draft
    made again loses its old archive only once the new one is written.

    What the person does on Zenodo's site is theirs: a description they
    changed, files they added, a draft of a new version they began are
    never written over or taken; where the study cannot tell, it leaves
    them and says so."""
    token = os.environ.get(TOKENS[site], "")
    if not token:
        raise ShareRefused(
            f"--share-to {site} needs a Zenodo token in {TOKENS[site]}, made at "
            f"{SITES[site]}/account/settings/applications/ with the deposit:write scope.",
            code="environment.service.unreachable")
    packed = pack("")
    base = base_url(site)
    depositions = f"{base}/api/deposit/depositions"
    kept = _load(study / SHARED_TO)
    earlier = kept.get(site) if isinstance(kept.get(site), dict) else {}
    _not_a_copy(study, kept)
    found_as = _found(study, kept, earlier, site=site, depositions=depositions, token=token,
                      said=said)
    deposition, version = found_as["deposition"], found_as["version"]
    published, versioned = found_as["published"], found_as["versioned"]
    title = ""
    made_here = False
    if deposition is None and earlier.get("asked_as") and not earlier.get("id"):
        # Asked for at a share whose answer was lost: taken if Zenodo made
        # it, asked for again under the same title if not, so that a draft
        # it makes later is seen beside it rather than lost.
        title = str(earlier["asked_as"])
        made = _marked(depositions, token, title, said, tries=TRIES)
        if made is None:
            raise ShareRefused(
                f"Zenodo did not list your drafts, so whether it made the one asked for at "
                f"the last share (titled {title!r}) is not known; nothing was made. Share "
                "again once Zenodo answers.", code="environment.service.unreachable")
        if made:
            deposition = _taken(depositions, token, title, made, said)
            made_here = deposition is not None
    # Made where Zenodo's own answer gave it: as it was made. One found by its
    # title since may hold what the person gave it.
    answered = False
    if deposition is None:
        # In plain letters: what Zenodo keeps of it is what was asked for.
        plain_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", unicodedata.normalize(
            "NFKD", study.name).encode("ascii", "ignore").decode()).strip(" _") or "study"
        title = title or f"FastMDXplora: sharing {plain_name} ({secrets.token_hex(4)})"
        # Kept before it is asked for, so a draft made whose answer is lost
        # is found again at the next share.
        kept[site] = {"asked_as": title, "version": version,
                      "asked_first": str(earlier.get("asked_first") or earlier.get("asked_as")
                                         or title)}
        _keep(study, kept, found_again=False)
        (deposition, answered), made_here = _new_draft(depositions, token, title, said), True
    if not isinstance(deposition, dict) or not deposition.get("id"):
        raise ShareRefused(
            f"Zenodo's answer to making the draft (titled {title!r}) named none; it may have "
            "made it: sharing again looks for it by its title and finishes it.",
            code="environment.service.unreachable")
    identifier = str(deposition["id"])
    same = str(earlier.get("id") or "") == identifier
    # What is known of this draft is kept with it from one share to the next.
    kept[site] = {"id": identifier, "version": version,
                  **{k: earlier[k] for k in ("archive", "written", "doi", "record", "asked_as",
                                             "sending", "record_written", "record_sending",
                                             "record_archive", "record_sums", "stale",
                                             "uploading", "made_as", "sums", "by_earlier",
                                             "written_words", "record_words", "sending_words",
                                             "record_sending_words")
                     if same and earlier.get(k)}}
    # The record's concept, kept from one version to the next: a copy is told
    # by it however many versions the study went on since.
    concept = str(deposition.get("conceptrecid") or earlier.get("concept") or "")
    if concept:
        kept[site]["concept"] = concept
    # So is the title its first draft was asked for under: a copy made while
    # nothing else was known of it is told by it, whatever the study did since.
    first = str(earlier.get("asked_first") or earlier.get("asked_as") or title or "")
    if first:
        kept[site]["asked_first"] = first
    if versioned:
        kept[site]["record"] = published
        if earlier.get("by_earlier"):
            kept[site]["by_earlier"] = True
            recorded_words = earlier.get("record_words") if found_as["begun_again"] \
                else earlier.get("written_words")
            if recorded_words:
                kept[site]["written_words"] = kept[site]["record_words"] = recorded_words
            # Sent for the record and its answer lost: what it holds may be that.
            sent_words = earlier.get("record_sending_words") if found_as["begun_again"] \
                else earlier.get("sending_words")
            if sent_words:
                kept[site]["sending_words"] = kept[site]["record_sending_words"] = sent_words
        # The archive the record holds, the study's: begun again, a draft of
        # it holds that one, not the one put on the draft deleted.
        # (And any it was replacing, or sent whose answer was lost: one of
        # them, the one it was published with.)
        recorded_as = _names_kept(earlier.get("record_archive")) if found_as["begun_again"] \
            else _names_kept([earlier.get("archive"), *_names_kept(earlier.get("stale")),
                              earlier.get("uploading")])
        # Only those the record holds: a name of the study's it no longer
        # holds is free for the person's own file on the new version.
        if found_as["record_files"]:
            recorded_as = [name for name in recorded_as if name in found_as["record_files"]]
            # And only as the study uploaded it: one of its name the person
            # put on a version they published since is theirs.
            uploaded = _known(earlier.get("sums"), earlier.get("record_sums"))
            recorded_as = [name for name in recorded_as if not uploaded.get(name)
                           or _md5(found_as["record_files"][name]) in ("", *uploaded[name])]
        if recorded_as:
            kept[site]["archive"], kept[site]["record_archive"] = recorded_as[0], recorded_as
            # As the record holds each: one of that name the person puts on
            # the draft since is not the study's.
            kept[site]["record_sums"] = {name: found_as["record_files"].get(name, "")
                                         for name in recorded_as}
        # A new version begins with the record's description: what the study
        # wrote for the record (not for a draft of a version since deleted).
        recorded = earlier.get("record_written") if found_as["begun_again"] \
            else earlier.get("written")
        if recorded:
            kept[site]["written"] = kept[site]["record_written"] = recorded
        # Sent for the record and its answer lost: what it holds is that.
        sent = earlier.get("record_sending") if found_as["begun_again"] \
            else earlier.get("sending")
        if sent:
            kept[site]["sending"] = kept[site]["record_sending"] = sent
    # Shared by an earlier release, which kept no record of what it wrote but
    # wrote the description at every share: the study's where it is still
    # as such a share wrote it (its own words, names and keywords).
    # Its names, keywords, licence and access, which the study cannot tell from
    # the person's, stay as the draft holds them in that record from then on;
    # its description and title follow the study's.
    # (Marked in shared_to.json at its first share here, before anything else is
    # kept, so a share that
    # fails part way leaves it so marked.)
    # (Only the draft that release shared, or a new version of its record:
    # a new draft made here is this release's own.)
    by_earlier = bool(kept[site].get("by_earlier")) or (
        (same or versioned) and bool(earlier.get("id")) and not any(
            earlier.get(key) for key in (
                "written", "sending", "asked_as", "asked_first", "archive", "sums",
                "record_written",
                "record_sending", "record_archive", "stale", "uploading", "made_as",
                "record")))
    if by_earlier:
        kept[site]["by_earlier"] = True
        # The zip it holds of the archive's name, as that release left it,
        # is the study's (that release kept no account of it), until this
        # release has named one there.
        held_there = dict(_contents(deposition)).get(archive.name)
        if held_there and not kept[site].get("archive") \
                and archive.name not in _sums_kept(kept[site].get("sums")):
            kept[site]["sums"] = {**_sums_kept(kept[site].get("sums")),
                                  archive.name: [_md5(held_there)]}
        # A draft of a new version that release began: of the record its
        # concept's latest version is, so a deleted one is begun again. (By
        # its concept's address where Zenodo gives a draft no `latest` link,
        # as it gives it only to what is published.)
        links = deposition.get("links") if isinstance(deposition.get("links"), dict) else {}
        concept = str(deposition.get("conceptrecid") or "")
        if not versioned and not kept[site].get("record") and (links.get("latest") or concept):
            try:
                of = _latest_published(
                    deposition if links.get("latest") else
                    {**deposition, "links": {**links, "latest": f"{base}/api/records/{concept}"}},
                    depositions, token, said)
            except ShareRefused as exc:
                # Not known: neither its record nor the version it is.
                unknown = ShareRefused(
                    f"{exc} The draft {identifier}, "
                    + ("of a new version an earlier release began"
                       if links.get("latest") else "shared by an earlier release")
                    + ", is as it was; share again once Zenodo answers.",
                    code=exc.refusal.code)
                unknown.later = True
                raise unknown from exc
            if str(of.get("id") or "") not in ("", identifier):
                kept[site]["record"] = str(of["id"])
                # Its version counted on from the record's, as a new version
                # begun here is (that release counted its own).
                counted = _bumped(str((of.get("metadata") or {}).get("version") or ""))
                if counted:
                    version = kept[site]["version"] = counted
    if made_here and title:
        # The title it was asked for under: a draft Zenodo lists under it
        # later is one made twice, to be named.
        kept[site]["asked_as"] = title
        # As made, the study's to describe; changed there before it is
        # described (a co-author added after a failed upload), the person's.
        made_with = deposition.get("metadata") if isinstance(deposition.get("metadata"),
                                                             dict) else {}
        if answered:
            # Read back at once: Zenodo's answer to making a draft may leave
            # out what it gives it (its licence, its access).
            try:
                seen = _call("GET", f"{depositions}/{identifier}", token=token, said=said,
                             missing_ok=True)
            except ShareRefused:
                seen = None
            if _a_draft(seen, identifier):
                deposition, made_with = seen, seen["metadata"]
                kept[site]["written"] = _written(made_with)
            else:
                # Not seen as made: told as a draft found by its title is.
                answered = False
        if answered or (_plain(str(made_with.get("title") or "")) == _plain(title)
                        and not any(made_with.get(key) for key in _SOMEONE_S)):
            # As Zenodo made it: seen in its answer, or found by its title
            # with no one's words or names in it yet.
            # (Its kind Zenodo does not choose: one found set is the person's.)
            kept[site]["made_as"] = {key: made_with.get(key) for key in _MADE_WITH
                                     if answered or (made_with.get(key)
                                                     and key != "upload_type")}
    try:
        _keep(study, kept, found_again=not versioned or same)
    except ShareRefused as exc:
        if not versioned or same:
            raise
        # Begun, and not remembered: named, as no later share can tell it
        # from one begun on Zenodo's site.
        begun_at = str((deposition.get("links") or {}).get("html")
                       or f"{base}/uploads/{identifier}")
        raise ShareRefused(
            f"{exc} Zenodo had begun a new version of record {published}, draft "
            f"{identifier} ({begun_at}), which this study could not remember: delete that "
            "draft there, make room, then share again.",
            code=exc.refusal.code, path=str(study / SHARED_TO)) from exc
    held = deposition.get("metadata") if isinstance(deposition.get("metadata"), dict) else {}
    inherited = {k: v for k, v in held.items() if k not in ("doi", "prereserve_doi")}
    page = str((deposition.get("links") or {}).get("html") or f"{base}/uploads/{identifier}")
    earlier_files = [entry for entry in deposition.get("files") or []
                     if isinstance(entry, dict) and entry.get("id")]
    # The study's archive is there already (else only the person's files).
    was_stale = earlier.get("stale")
    had = bool(_names(deposition) & ({str(kept[site].get("archive") or ""), archive.name,
                                      str(earlier.get("uploading") or ""),
                                      *_names_kept(kept[site].get("record_archive")),
                                      *(map(str, was_stale) if isinstance(was_stale, list)
                                        else [str(was_stale or "")])} - {""}))
    reserved_as = held.get("prereserve_doi")
    doi = str(reserved_as.get("doi") or "") if isinstance(reserved_as, dict) else ""
    if not doi:
        try:
            reserved = _call("PUT", f"{depositions}/{identifier}", token=token,
                             body={"metadata": {**inherited, "prereserve_doi": True}},
                             said=said)
        except ShareRefused as exc:
            if getattr(exc, "status", 0) in (404, 410) and getattr(exc, "own", False):
                raise ShareRefused(
                    f"{exc} The draft {page} is gone from {site} (deleted there during this "
                    "share): share again and a new one is made.",
                    code="environment.service.unusable_response", url=page) from exc
            mend = "share again once Zenodo answers" if getattr(exc, "later", False) \
                else "share again once that is mended"
            raise ShareRefused(
                f"{exc} The draft {page} is kept as it was, its DOI not yet reserved: "
                f"{mend}, and that draft is finished, not another made.",
                code=exc.refusal.code, url=page) from exc
        # Taken only from an answer naming this draft: another's DOI written
        # into the archive would name the wrong record.
        reserved_as = (reserved["metadata"].get("prereserve_doi")
                       if _a_draft(reserved, identifier) else None)
        doi = str(reserved_as.get("doi") or "") if isinstance(reserved_as, dict) else ""
        if not doi:
            raise ShareRefused(
                f"Zenodo reserved no DOI for the draft {page} (its answer named none for it). The "
                "draft is kept as it was: share again once Zenodo answers, and that draft is "
                "finished, not another made.", code="environment.service.unreachable",
                url=page)
    # A DOI Zenodo reserves names the record it is for, as its number.
    recid = str(reserved_as.get("recid") or "") if isinstance(reserved_as, dict) else ""
    numbered = re.search(r"zenodo\.(\d+)$", doi)
    if (recid and recid != identifier) or (numbered and numbered.group(1) != identifier):
        raise ShareRefused(
            f"Zenodo's answer for the draft {page} named the DOI of another record ({doi}). "
            "The draft is kept as it was: share again once Zenodo answers, and that draft is "
            "finished, not another made.", code="environment.service.unreachable", url=page)
    if doi:
        try:
            packed = pack(f"https://doi.org/{doi}")
        except ShareRefused as exc:
            holds = "with the archive it had" if had else "with no archive yet"
            raise ShareRefused(
                f"{exc} The draft {page} is kept, {holds}: share again once it can be "
                "written, and that draft is finished, not another made.",
                code=exc.refusal.code) from exc
    kept[site]["doi"] = doi
    _keep(study, kept)
    bucket = str((deposition.get("links") or {}).get("bucket") or "")
    if not bucket:
        unbucketed = ShareRefused(
            f"Zenodo's answer for the draft {deposition.get('id')} named no bucket to upload "
            "into. The draft is kept as it is: share again once Zenodo answers, and that "
            "draft is finished, not another made.", code="environment.service.unreachable")
        unbucketed.later = True
        raise unbucketed
    # Only the archive this study put there is taken away; another one
    # keeps the share back, as a record of two archives opens as neither.
    ours = str(kept[site].get("archive") or "")
    replacing = {str(n) for n in (earlier.get("stale") or []) if n} \
        if isinstance(earlier.get("stale"), list) else {str(earlier.get("stale") or "")} - {""}
    # Sent at a share whose answer was lost: there, perhaps, and the study's.
    if earlier.get("uploading"):
        replacing.add(str(earlier["uploading"]))
    # Each the study put on the record a new version is of, whichever it was
    # published with.
    replacing |= set(_names_kept(kept[site].get("record_archive")))
    # What each archive of the study's holds, where known (as uploaded, or as
    # the record holds it): one of its name holding anything else is not the
    # study's, but put there by the person.
    # (Each name may have been uploaded with several: the one the draft holds
    # is the study's if it is any of them.)
    known = _known(kept[site].get("sums"), kept[site].get("record_sums"))
    held_sums = {name: _md5(checksum) for name, checksum in _contents(deposition)}

    def _its(name: str) -> bool:
        return not known.get(name) or not held_sums.get(name) or held_sums[name] in known[name]

    # The names the study gave its archives on this draft: a zip of the new
    # archive's name it never gave one, uploaded over, would be the person's
    # lost. (A draft an earlier release made names none.)
    named = {ours, *replacing, *known} - {""}
    # Until this release names an archive on it, its zip may be that release's.
    earlier_release = by_earlier and not any(kept[site].get(key) for key in (
        "archive", "record_archive"))
    replacing = {name for name in replacing if _its(name)} - {archive.name}
    stale = [entry for entry in earlier_files
             if str(entry.get("filename") or entry.get("key") or "") == ours
             and ours and ours != archive.name and _its(ours)]
    stale += [entry for entry in earlier_files
              if str(entry.get("filename") or entry.get("key") or "") in replacing
              and entry not in stale]
    foreign = sorted(name for name in _names(deposition)
                     if name.lower().endswith(".zip")
                     and (name not in {archive.name, ours, *replacing} or not _its(name)
                          or (name == archive.name and name not in named
                              and not earlier_release)))
    if foreign:
        raise ShareRefused(
            f"The draft {page} holds {', '.join(foreign)}, which this study did not put "
            "there" + (" (or an earlier release of FastMDXplora put there, which kept no "
                       "record of its archive)" if earlier_release else "")
            + ("; a record of two archives opens as neither" if len(
                [n for n in _names(deposition) if n.lower().endswith(".zip")]) > 1
               else "; a record holding it would not open as the study")
            + (". Delete them there, or keep them in a record of their own, then share again."
               if len(foreign) > 1 else
               ". Delete it there, or keep it in a record of its own, then share again."),
            code="environment.service.unusable_response", url=page)
    # The description: the study's where it wrote it last or none was
    # written yet; else the person's, kept.
    marker = str(kept[site].get("asked_as") or title or "")
    # What the study wrote last, where it is of this draft (or, for a new
    # version, of the record it is begun from).
    last = str(kept[site].get("written") or "")
    # Written, its answer lost: the draft holds what was sent.
    # Each sent since it was last written, its answer lost: the draft holds
    # one of them, whichever landed.
    sending = _sendings(kept[site].get("sending"))
    made_as = kept[site].get("made_as") if isinstance(kept[site].get("made_as"), dict) else {}

    # (The summary that release wrote is not known: a description of its form
    # whose summary is the study's own now, or none, is the study's; one whose
    # summary differs may be the person's, and is kept.)
    summary_now = _metadata(packed["crate"], authors=authors, license_id=license_id,
                            system=system, version=version)["description"].split("</p>")[0]

    def _as_earlier_wrote(held: dict[str, Any]) -> bool:
        # Until this release has written it: from then on, as any draft.
        said_there = str(held.get("description") or "")
        return by_earlier and not last and (
            not said_there or (bool(_EARLIER_WROTE.fullmatch(said_there))
                               and _text(said_there.split("</p>")[0])
                               == _text(summary_now)))

    # The study's description alone, as written or sent there (an earlier
    # release's draft, whose other fields stay the person's).
    words = [w for w in (str(kept[site].get("written_words") or ""),
                         *_sendings(kept[site].get("sending_words"))) if w]

    def _the_study_s(held: dict[str, Any]) -> bool:
        return (bool(last) and _written(held) == last) or _sent(held) in sending \
            or (by_earlier and _written({"description": held.get("description")}) in words) \
            or _as_earlier_wrote(held) or (
            # Still as asked for: titled so, and no one's words or names in it
            # (whatever defaults Zenodo gave it since).
            not versioned and _plain(str(held.get("title") or "")) in ("", _plain(marker))
            and not any(held.get(key) for key in _SOMEONE_S)
            and all(_as_kept({key: held.get(key)}).get(key) in (
                        None, "", *(_as_kept({key: given}).get(key) for given in (
                            ours, *((made_as[key],) if key in made_as
                                    else _ZENODO_GIVES.get(key, ())))))
                    for key, ours in (("access_right", "open"), ("upload_type", "dataset"),
                                      ("license", license_id.lower()))))

    untouched = _the_study_s(held)
    uploaded, removing, described = False, "", False
    try:
        if said:
            said(f"Uploading {archive.name} ({said_size(archive.stat().st_size)}) to "
                 f"{site}...")
        # The new archive first: one of the same name is replaced by it, and
        # the draft keeps the one it had until the new one is there.
        before = str(kept[site].get("uploading") or "")
        if before and before != archive.name:
            # Sent before, its answer lost, perhaps kept: the study's still,
            # to be taken away once the new one is there.
            kept[site]["stale"] = sorted({*_names_kept(kept[site].get("stale")), before})
        kept[site]["uploading"] = archive.name
        sent_sums = _sums_kept(kept[site].get("sums"))
        sent_sums[archive.name] = [*sent_sums.get(archive.name, []), _md5_of(archive)][-_SENDINGS:]
        kept[site]["sums"] = sent_sums
        _keep(study, kept)
        put = _call("PUT", f"{bucket}/{urllib.parse.quote(archive.name)}", token=token,
                    upload=archive, said=said)
        # Kept only as Zenodo says so: the file it holds named (and, where it
        # gives one, its checksum the one sent); an answer that says nothing
        # leaves it not known, and nothing of the draft is taken away.
        put_sum = _md5((put or {}).get("checksum")) if isinstance(put, dict) else ""
        if isinstance(put, dict) and put_sum and put_sum != sent_sums[archive.name][-1] \
                and str(put.get("key") or put.get("filename") or "") == archive.name:
            # Kept, but not as sent: the study's still, replaced at the next share.
            kept[site]["sums"] = {**_sums_kept(kept[site].get("sums")),
                                  archive.name: [*sent_sums[archive.name], put_sum]}
            _keep(study, kept)
            damaged = ShareRefused(
                f"Zenodo kept {archive.name} with other contents than were sent (its "
                "checksum is not the archive's).", code="environment.service.unreachable",
                url=page)
            damaged = ShareRefused(
                f"{damaged} The draft {page} holds a damaged copy of {archive.name}: share "
                "again before you publish, and it is replaced.",
                code="environment.service.unreachable", url=page)
            damaged.later = damaged.whole = True
            raise damaged
        if not (isinstance(put, dict) and str(put.get("key") or put.get("filename") or "")
                == archive.name and (not put_sum or put_sum == sent_sums[archive.name][-1])):
            unsaid = ShareRefused(
                f"Zenodo's answer to the upload of {archive.name} did not say it was kept.",
                code="environment.service.unreachable", url=page)
            unsaid.later = unsaid.unclear = True
            raise unsaid
        uploaded = True
        kept[site]["archive"] = archive.name
        kept[site].pop("uploading", None)
        # Each still the study's, until it is taken away.
        kept[site]["stale"] = sorted({str(e.get("filename") or e.get("key")) for e in stale})
        _keep(study, kept)
        for entry in stale:
            removing = str(entry.get("filename") or entry.get("key"))
            # Gone already is what was asked: a try that did it, then lost
            # its answer, is asked again.
            _call("DELETE", f"{depositions}/{identifier}/files/{entry['id']}",
                  token=token, missing_ok=True, said=said)
            kept[site]["stale"] = [n for n in kept[site]["stale"] if n != removing]
            # Taken away: said so even where what remembers it cannot be written.
            removing = ""
            _keep(study, kept)
        kept[site].pop("stale", None)
        # Read again: an upload takes minutes, and what the person wrote there
        # meanwhile is theirs.
        again = _call("GET", f"{depositions}/{identifier}", token=token, said=said,
                      missing_ok=True, gone=_GONE)
        if again is _GONE:
            deleted = ShareRefused(
                f"The draft {page} was deleted on {site} during this share. Share again "
                "and a new one is made.", code="environment.service.unusable_response",
                url=page)
            deleted.gone = True
            raise deleted
        if not _a_draft(again, identifier, doi):
            # Not read: what the person wrote meanwhile is not known, and is
            # not written over.
            unread = ShareRefused(f"Zenodo's answer reading the draft {page} again was no "
                                  "draft.", code="environment.service.unreachable")
            unread.later = True
            raise unread
        # What the draft holds now: the archive as sent, and no zip the
        # person put there meanwhile.
        now_sums = {name: _md5(checksum) for name, checksum in _contents(again)}
        sent_md5 = (kept[site]["sums"].get(archive.name) or [""])[-1]
        # (Not one the study has just taken away, which a listing may show a
        # moment longer.)
        deleted_ids = {str(e.get("id")) for e in stale}
        lagging = {str(f.get("filename") or f.get("key") or "")
                   for f in again.get("files") or []
                   if isinstance(f, dict) and str(f.get("id")) in deleted_ids}
        others = sorted(name for name in _names(again) if name.lower().endswith(".zip")
                        and name != archive.name and name not in lagging
                        and name not in (_names(deposition) - {e.get("filename") or e.get("key")
                                                               for e in stale}))
        if archive.name not in _names(again):
            # Answered as kept, and not there: not kept.
            lost = ShareRefused(
                f"Zenodo answered the upload of {archive.name} but the draft {page} does not "
                "hold it.", code="environment.service.unreachable", url=page)
            lost.later = True
            raise lost
        if lagging:
            # Asked to take them away, answered so, and still there: the study's
            # still, to be taken away at the next share.
            kept[site]["stale"] = sorted(lagging)
            _keep(study, kept)
            still = ShareRefused(
                f"The draft {page} still holds {', '.join(sorted(lagging))}, which Zenodo was "
                "asked to take away and said it had; a record of two archives opens as "
                "neither. Share again before you publish, and it is taken away.",
                code="environment.service.unreachable", url=page)
            still.whole = still.later = True
            raise still
        if now_sums.get(archive.name) == sent_md5:
            # Held as sent: the one checksum of that name the study need know.
            kept[site]["sums"] = {**_sums_kept(kept[site].get("sums")),
                                  archive.name: [sent_md5]}
        if (now_sums.get(archive.name) and now_sums[archive.name] != sent_md5) or others:
            clash = ShareRefused(
                f"The draft {page} holds "
                + (f"{archive.name} with other contents than this study sent"
                   if now_sums.get(archive.name) and now_sums[archive.name] != sent_md5
                   else ", ".join(others))
                + ", put there on Zenodo during this share; a record of two archives opens "
                "as neither, and one not the study's does not open as it. Delete it there "
                "before you publish, then share again.",
                code="environment.service.unusable_response", url=page)
            clash.whole = True
            raise clash
        if isinstance(again, dict) and isinstance(again.get("metadata"), dict):
            held = again["metadata"]
            inherited = {k: v for k, v in held.items() if k not in ("doi", "prereserve_doi")}
            untouched = _the_study_s(held)
        if untouched:
            ours_said = _metadata(packed["crate"], authors=authors, license_id=license_id,
                                  system=system, version=version)
            if by_earlier:
                # Its description, and what the draft lacks: the rest (its
                # title, names, keywords, licence, access) stays as it holds it.
                wanted = {key: value for key, value in ours_said.items()
                          if key in ("description", "version", "prereserve_doi")
                          or (not held.get(key) and not last
                              and _written({"description": held.get("description")})
                              not in words)}
                if said and any(held.get(key) and _as_kept({key: held.get(key)})
                                != _as_kept({key: ours_said.get(key)})
                                for key in ("creators", "license", "keywords", "title",
                                            "access_right")):
                    said(f"The draft {page} was shared by an earlier release of "
                         "FastMDXplora, which kept no account of what it wrote: its title, "
                         "creators, keywords, licence and access are kept as it holds them. "
                         "Change them there if they are to be the ones given here (its "
                         "description stays the study's).")
                ours_said = wanted
            kept[site]["sending"] = [*_sendings(kept[site].get("sending")),
                                     _sent({**inherited, **ours_said})][-_SENDINGS:]
            if by_earlier:
                # Its description alone too: names the person changes after it
                # landed, its answer lost, leave it the study's.
                kept[site]["sending_words"] = [
                    *_sendings(kept[site].get("sending_words")),
                    _written({"description": ours_said.get("description")})][-_SENDINGS:]
            _keep(study, kept)
            answer = _call("PUT", f"{depositions}/{identifier}", token=token,
                           body={"metadata": {**inherited, **ours_said}}, said=said)
            described = True
            # Said only by an answer naming this draft.
            answered_as = answer["metadata"] if _a_draft(answer, identifier) else None
            if not (isinstance(answered_as, dict)
                    and _text(answered_as.get("description"))
                    == _text(ours_said.get("description"))
                    and _plain(str(answered_as.get("version") or ""))
                    == _plain(str(version))):
                # Not said: whether it was written is not known.
                unsaid = ShareRefused(
                    f"Zenodo's answer to describing the draft {page} said nothing of it.",
                    code="environment.service.unreachable", url=page)
                unsaid.later = unsaid.unclear = True
                described = False
                raise unsaid
            if isinstance(answer, dict) and isinstance(answer.get("metadata"), dict):
                kept[site]["written"] = _written(answer["metadata"])
                if by_earlier:
                    # The study writes only its words there: names or a licence
                    # the person changes since leave them the study's.
                    kept[site]["written_words"] = _written(
                        {"description": answer["metadata"].get("description")})
                kept[site].pop("sending", None)
                kept[site].pop("sending_words", None)
                # A zip put there since the draft was read again, as its answer
                # lists it.
                late = sorted(name for name in _names(answer) if name.lower().endswith(".zip")
                              and name != archive.name)
                if late:
                    _keep(study, kept)
                    put_late = ShareRefused(
                        f"The draft {page} holds {', '.join(late)}, put there on Zenodo during "
                        "this share; a record of two archives opens as neither. Delete it "
                        "there before you publish, then share again.",
                        code="environment.service.unusable_response", url=page)
                    put_late.whole = True
                    raise put_late
            # Else what it holds is not known: what was sent stays the study's.
        else:
            # The person's words, kept; the version is the study's to say.
            if str(held.get("version") or "") != str(version):
                labelled = _call("PUT", f"{depositions}/{identifier}", token=token,
                                 body={"metadata": {**inherited, "version": str(version)}},
                                 said=said)
                if not (_a_draft(labelled, identifier) and _plain(str(
                        labelled["metadata"].get("version") or "")) == _plain(str(version))):
                    unlabelled = ShareRefused(
                        f"Zenodo's answer to giving the draft {page} version {version} did "
                        "not say it was given. Its version is perhaps not yet written; the "
                        "draft is kept, with the new archive: share again once Zenodo "
                        "answers, and that draft is finished, not another made.",
                        code="environment.service.unreachable", url=page)
                    unlabelled.later = unlabelled.whole = True
                    raise unlabelled
            if said and by_earlier and not last:
                said(f"The draft {page} was shared by an earlier release of FastMDXplora, "
                     "which kept no account of what it wrote: its description is not the "
                     "study's summary now, and may be yours, so it is kept as it is, as are "
                     "its title, creators, keywords, licence and access. To have the study "
                     "write it, clear the description there and share again.")
            elif said and not held.get("description"):
                said(f"The draft {page} was changed there before this study described it: "
                     "it is kept as it is, with no description, and only the archive is "
                     "new. Describe it there before you publish.")
            elif said:
                said(f"The draft {page} was changed there since this study last described "
                     "it (its words, names, keywords, access or licence): it is kept as it "
                     "is, and only the archive is new. Write it there as you want it before "
                     "you publish.")
        _keep(study, kept)
    except ShareRefused as exc:
        if getattr(exc, "gone", False) or getattr(exc, "whole", False):
            raise
        if getattr(exc, "status", 0) in (404, 410) and getattr(exc, "own", False):
            raise ShareRefused(
                f"{exc} The draft {page} is gone from {site} (deleted there during this "
                "share): share again and a new one is made.",
                code="environment.service.unusable_response", url=page) from exc
        left = ", ".join(n for n in kept[site].get("stale") or [removing] if n) or removing
        kept_as = (f"with the new archive and the earlier {left}, which Zenodo may not "
                   "have taken away (a record of two archives opens as neither: share again, "
                   "or delete it there, before you publish)" if removing
                   else f"with the new archive and the earlier "
                   f"{', '.join(kept[site]['stale'])} beside it, not yet taken away (a record "
                   "of two archives opens as neither: share again before you publish)"
                   if uploaded and kept[site].get("stale")
                   else "with the new archive and its description" if uploaded and described
                   else "with the new archive, its description "
                   + ("perhaps " if getattr(exc, "unclear", False) else "") + "not yet written"
                   if uploaded and untouched
                   else "with the new archive" if uploaded
                   else "with the archive it had, and perhaps the new one beside it if Zenodo "
                   "kept it before its answer was lost (a record of two archives opens as "
                   "neither: share again before you publish, and the earlier one is taken "
                   "away)" if had and getattr(exc, "unclear", False)
                   and archive.name not in _names(deposition)
                   else "with the archive it had, or the new one if Zenodo kept it before its "
                   "answer was lost" if had and getattr(exc, "unclear", False)
                   else "with the archive it had" if had
                   else "with no archive yet, or the new one if Zenodo kept it before its "
                   "answer was lost" if getattr(exc, "unclear", False)
                   else "with no archive yet")
        mend = ("share again once there is room" if exc.refusal.code
                == "environment.share.too_large" else "share again once Zenodo answers"
                if getattr(exc, "later", False) else "share again once that is mended")
        raise ShareRefused(
            f"{exc} The draft {page} is kept, {kept_as}: {mend}, and that draft is finished, "
            "not another made.", code=exc.refusal.code, url=page) from exc
    twice = str(kept[site].get("asked_as") or "")
    try:
        others = [n for n in (_marked(depositions, token, twice, said) or [])
                  if n != identifier] if twice else []
    except ShareRefused:
        # The share is done; the stray draft is named at the next one.
        others = []
    if others and said:
        said(f"Zenodo also holds draft{'s' if len(others) > 1 else ''} {', '.join(others)} "
             f"titled {twice!r}, made by a share whose answer was lost and listed only late: "
             f"delete {'them' if len(others) > 1 else 'it'} among your uploads "
             f"({base}/me/uploads).")
    if said:
        said(f"Draft {f'of version {version} ' if str(version) != '1' else ''}made: {page}"
             + (f" (DOI {doi}, reserved until you publish)." if doi else ".")
             + " Read it there and press Publish; until then nothing is public.")
    return {"url": page, "doi": doi, "id": identifier, "version": version, **packed}


#: Zenodo's answer for a draft or record it no longer holds.
_GONE = object()


def _found(study: Path, kept: dict[str, Any], earlier: dict[str, Any], *, site: str,
           depositions: str, token: str,
           said: Callable[[str], None] | None) -> dict[str, Any]:
    """The draft the study was shared as, made again (its `deposition`), or
    a draft of a new version of the record it was published as, begun now;
    None where a new draft is to be made; with the `version` it is, the
    `published` record a new version is of, and whether it is one
    (`versioned`)."""
    out: dict[str, Any] = {"deposition": None, "version": str(earlier.get("version") or "1"),
                           "published": str(earlier.get("record") or ""), "versioned": False,
                           "begun_again": False, "record_files": {}}
    if not earlier.get("id"):
        return out
    published = out["published"]
    again_from = asked = answered = False
    try:
        # Gone only where Zenodo says so (404, 410): any other answer that is
        # no draft is no reason to make another.
        found = _call("GET", f"{depositions}/{earlier['id']}", token=token, missing_ok=True,
                      said=said, gone=_GONE)
        if found is _GONE and published and published != str(earlier["id"]):
            again_from = True
            # The draft of a new version deleted: begun again from its record.
            found = _call("GET", f"{depositions}/{published}", token=token, missing_ok=True,
                          said=said, gone=_GONE)
        if found is not _GONE and (not isinstance(found, dict) or not found.get("id")):
            odd = ShareRefused(
                f"Zenodo's answer for the draft or record {earlier['id']} this study was "
                "shared as is no draft or record; nothing was made. Share again later.",
                code="environment.service.unusable_response")
            odd.whole = True
            raise odd
        if not isinstance(found, dict):
            if said:
                said(f"The draft or record {earlier['id']} this study was shared as is gone "
                     f"from {site}; making a new one.")
            out["version"] = "1"
            return out
        # Read as Zenodo gives it (the draft or record asked for), else nothing
        # is begun from it or written over.
        asked_for = published if again_from else str(earlier["id"])
        if not _a_draft(found, asked_for, "" if again_from else str(earlier.get("doi") or "")):
            misread = ShareRefused(
                f"Zenodo's answer for the draft or record {asked_for} held not it. It is as "
                "it was; share again once Zenodo answers.",
                code="environment.service.unreachable")
            misread.whole = misread.later = True
            raise misread
        if not found.get("submitted"):
            # The draft made last time, never published: made again.
            out["deposition"] = found
            if earlier.get("record") and found.get("conceptrecid"):
                out["version"] = _counted_past(found, depositions, token, said,
                                               str(earlier.get("version") or "1"),
                                               str(earlier["record"]))
            return out
    except ShareRefused as exc:
        if getattr(exc, "whole", False):
            raise
        again = "; share again once Zenodo answers." if getattr(exc, "later", False) else "."
        raise ShareRefused(f"{exc} The draft or record {earlier['id']} the study was shared "
                           f"as is as it was{again}", code=exc.refusal.code) from exc
    # Published: a new version, begun from the latest one published (which
    # may be one the person published by hand since), and counted on from it.
    # Until the latest is known, the record is the one the study was shared as.
    published = str(found.get("id") or earlier["id"]) if not again_from else published
    try:
        newest = _latest_published(found, depositions, token, said)
        published = str(newest.get("id") or found["id"])
        if newest.get("state") == "inprogress":
            # An edit saved on the published record, not published: read in
            # its place, so a new version begun would be told from it, not
            # from the record.
            raise ShareRefused(
                f"Record {published} on Zenodo holds an edit saved there and not published: "
                "publish or discard that edit there, then share again.",
                code="environment.service.unusable_response",
                url=str((newest.get("links") or {}).get("html") or ""))
        if again_from and said:
            said(f"The draft {earlier['id']} of a new version is gone from {site}; "
                 f"beginning a new version again, from the latest version of record "
                 f"{published}.")
        # The next of the latest version's own label; where it holds no
        # number, the study's own count: the version its deleted draft was
        # to be, or the one after the one it published.
        mine = str(earlier.get("version") or "1")
        version = _bumped(str((newest.get("metadata") or {}).get("version") or "")) or (
            mine if again_from else _bumped(mine) or "2")
        begun = _concept_drafts(depositions, token, newest, said)
        if begun is None:
            raise ShareRefused(
                f"Zenodo did not list your drafts, so whether a new version of record "
                f"{published} is begun already is not known; nothing was changed. Share again "
                "once Zenodo answers.", code="environment.service.unreachable")
        if begun:
            # Zenodo keeps one draft of a new version, and gives it to whoever
            # asks: one there already is the person's, or one a share began
            # and lost the answer to; either way left alone.
            draft_id = str(begun[0].get("id"))
            # Read whole (a listing may leave out its files): unchanged, it is
            # the record again, and publishing it adds nothing.
            whole = _call("GET", f"{depositions}/{draft_id}", token=token, said=said,
                          missing_ok=True)
            if not _a_draft(whole, draft_id):
                # Not read whole: what it holds is not known, and nothing is said
                # of it but that it is there.
                unread = ShareRefused(
                    f"Zenodo holds a draft of a new version of record {published} "
                    f"({draft_id}), begun on its site or by a share whose answer was lost, "
                    "and its answer for it held not the draft: share again once Zenodo "
                    "answers.", code="environment.service.unreachable",
                    url=str((begun[0].get("links") or {}).get("html") or ""))
                unread.later = True
                raise unread
            seen = whole
            what = ("delete it there (it holds the record as it was), then share again"
                    if not _changed_since(newest, seen)
                    else "publish it, or delete it there, then share again")
            raise ShareRefused(
                f"Zenodo holds a draft of a new version of record {published} ({draft_id}), "
                f"begun on its site or by a share whose answer was lost: {what}.",
                code="environment.service.unusable_response",
                url=str((begun[0].get("links") or {}).get("html") or ""))
        # Where the study is written, written first: a disk with no room
        # refuses before Zenodo begins a version the study could not remember.
        _keep(study, kept, found_again=False)
        asked = True
        newer = _call("POST", f"{depositions}/{published}/actions/newversion", token=token,
                      said=said)
        # Answered as Zenodo answers: begun. An empty answer may be either.
        answered = isinstance(newer, dict) and bool(newer)
        links = newer.get("links") if isinstance(newer, dict) else None
        latest = str(links.get("latest_draft") or "") if isinstance(links, dict) else ""
        # Answered with the record it is of (as Zenodo documents it) or with
        # the draft begun (as Zenodo answers): any other deposition's answer
        # leaves what was begun not known.
        if answered and str(newer.get("id") or "") not in (
                published, latest.rsplit("/", 1)[-1] if latest else published):
            answered = False
            other = ShareRefused("Zenodo's answer to beginning a new version named another "
                                 "deposition.", code="environment.service.unreachable")
            other.unclear = other.later = True
            raise other
        if not latest:
            unnamed = ShareRefused("Zenodo's answer to beginning a new version named no "
                                   "draft of it.", code="environment.service.unreachable")
            # It may have begun one, or not.
            unnamed.unclear = unnamed.later = True
            raise unnamed
        deposition = _call("GET", latest, token=token, said=said)
        if not _a_draft(deposition, latest.rsplit("/", 1)[-1]):
            raise ShareRefused(f"Zenodo's answer for {latest} was no draft.",
                               code="environment.service.unreachable")
        if deposition.get("submitted") or str(deposition.get("conceptrecid") or "") \
                != str(newest.get("conceptrecid") or ""):
            # Not a draft of this record's next version: what was begun, and
            # where, is not known.
            answered = False
            other = ShareRefused(f"Zenodo's answer to beginning a new version named {latest}, "
                                 f"no draft of a new version of record {published}.",
                                 code="environment.service.unreachable")
            other.unclear = other.later = True
            raise other
    except ShareRefused as exc:
        if exc.refusal.code == "environment.service.unusable_response" \
                or "did not list your drafts" in str(exc):
            raise
        # Only a request to begin one may have begun one; answered, it did.
        began = answered or (asked and getattr(exc, "unclear", False))
        again = "; share again once Zenodo answers." if getattr(exc, "later", False) else "."
        begun_as = f" ({latest.rsplit('/', 1)[-1]})" if answered and latest else ""
        raise ShareRefused(
            f"{exc} "
            + (f"Zenodo began the new version of record {published}{begun_as}: the next "
               "share finds it holding the record as it was and asks you to delete it there."
               if answered
               else f"Zenodo may have begun the new version of record {published}: the next "
               "share finds its draft and asks you to delete it there, as it cannot tell it "
               "from one you began." if began else f"Record {published} is as it was{again}"),
            code=exc.refusal.code) from exc
    changed = _changed_since(newest, deposition)
    if changed:
        # The one draft Zenodo keeps, begun by the person and not listed:
        # given back to this share, and left alone.
        raise ShareRefused(
            f"Zenodo holds a draft of a new version of record {published} "
            f"({latest.rsplit('/', 1)[-1]}) that has been changed ({changed}): publish or "
            "delete it there, then share again.",
            code="environment.service.unusable_response", url=latest)
    out.update(deposition=deposition, version=version, published=published, versioned=True,
               begun_again=again_from, record_files=dict(_contents(newest)))
    return out
