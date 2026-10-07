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
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmdxplora.sharing import ShareRefused

__all__ = ["SHARED_TO", "SITES", "archive_of", "base_url", "draft", "record_of"]

SITES = {"zenodo": "https://zenodo.org", "zenodo-sandbox": "https://sandbox.zenodo.org"}
TOKENS = {"zenodo": "ZENODO_TOKEN", "zenodo-sandbox": "ZENODO_SANDBOX_TOKEN"}
#: Zenodo's DOI prefixes: 10.5281 for records, 10.5072 for the sandbox's.
_PREFIXES = {"10.5281": "zenodo", "10.5072": "zenodo-sandbox"}
#: Where a study keeps the Zenodo record it was shared as.
SHARED_TO = "shared_to.json"
TIMEOUT_S = 120
_AGENT = "FastMDXplora (https://github.com/aai-research-lab/FastMDXplora)"


def base_url(site: str) -> str:
    return (os.environ.get("FASTMDXPLORA_ZENODO_URL") or SITES[site]).rstrip("/")


def _call(method: str, url: str, *, token: str = "", body: Any = None,
          upload: Path | None = None, missing_ok: bool = False) -> Any:
    """Zenodo's answer as JSON (or None for an empty one); refused,
    naming what Zenodo said, where it does not answer as it documents."""
    headers = {"User-Agent": _AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data: Any = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if upload is not None:
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(upload.stat().st_size)
        data = upload.open("rb")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:
            text = answer.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if missing_ok and exc.code in (404, 410):
            return None
        said = exc.read().decode("utf-8", errors="replace")[:400]
        why = {401: "the token was refused", 403: "the token may not do this (its scopes)",
               404: "no such record or draft"}.get(exc.code, f"HTTP {exc.code}")
        raise ShareRefused(f"Zenodo answered {method} {url}: {why}. {said}".strip(),
                           code="environment.service.unreachable", url=url) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ShareRefused(f"Zenodo could not be reached at {url}: {exc}.",
                           code="environment.service.unreachable", url=url) from exc
    finally:
        if upload is not None and data is not None:
            data.close()
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ShareRefused(f"Zenodo's answer to {method} {url} was not JSON.",
                           code="environment.service.unreachable", url=url) from exc


# -- reading a record --------------------------------------------------------

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


def archive_of(site: str, record: str) -> dict[str, Any]:
    """The record's archive: its `name`, `size`, `md5`, the `url` it is
    downloaded from, and the record's `doi`, `id` and `version`."""
    base = base_url(site)
    found = _call("GET", f"{base}/api/records/{record}")
    if not isinstance(found, dict):
        raise ShareRefused(f"Zenodo has no record {record}.",
                           code="environment.share.not_a_study")
    entries = found.get("files") or []
    if isinstance(entries, dict):
        entries = entries.get("entries") or []
        entries = list(entries.values()) if isinstance(entries, dict) else entries
    zips = []
    for entry in entries if isinstance(entries, list) else []:
        name = str(entry.get("key") or entry.get("filename") or "")
        if name.lower().endswith(".zip"):
            checksum = str(entry.get("checksum") or "")
            zips.append({"name": name, "size": int(entry.get("size") or entry.get("filesize")
                                                    or 0),
                         "md5": checksum.split(":", 1)[-1].lower()})
    if len(zips) != 1:
        raise ShareRefused(
            f"Zenodo's record {record} holds {len(zips)} zip files, "
            + ("so there is no shared study to open" if not zips else
               f"({', '.join(z['name'] for z in zips)}); download the study's and open "
               "the file"), code="environment.share.not_a_study")
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

def _creators(authors: list[tuple[str, str]]) -> list[dict[str, str]]:
    out = []
    for name, orcid in authors:
        parts = name.split()
        said = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name
        creator = {"name": said}
        if orcid:
            creator["orcid"] = orcid
        out.append(creator)
    return out


def _metadata(crate: dict[str, Any], *, authors: list[tuple[str, str]], license_id: str,
              system: str, version: int) -> dict[str, Any]:
    root = next(e for e in crate["@graph"] if e.get("@id") == "./")
    software = next((e for e in crate["@graph"] if e.get("@id") == "#fastmdxplora"), {})
    description = (f"<p>{html.escape(str(root.get('description', '')))}</p>"
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


def _load(path: Path) -> dict[str, Any]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def draft(study: Path, archive: Path, *, site: str, pack: Callable[[str], dict[str, Any]],
          authors: list[tuple[str, str]], license_id: str, system: str,
          said: Callable[[str], None] | None = None) -> dict[str, Any]:
    """A draft on Zenodo of the study packed by ``pack``, which is given the
    DOI the draft reserved (or "") and returns what :func:`share_study`
    does; the draft's `url`, `doi` and `id`. Never published.

    The study is packed once before Zenodo is asked anything, so a study
    that cannot be shared leaves no draft behind; the draft is recorded in
    `shared_to.json` as soon as it exists, so a failure after leaves a
    draft the next share makes again rather than another one; and a draft
    made again loses its old archive only once the new one is written."""
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
    version = int(earlier.get("version") or 0) + 1
    deposition: dict[str, Any] | None = None
    if earlier.get("id"):
        found = _call("GET", f"{depositions}/{earlier['id']}", token=token, missing_ok=True)
        if not isinstance(found, dict):
            if said:
                said(f"The draft or record {earlier['id']} this study was shared as is gone "
                     f"from {site}; making a new one.")
            version = 1
        elif not found.get("submitted"):
            # The draft made last time, never published: made again.
            deposition, version = found, int(earlier.get("version") or 1)
        else:
            newer = _call("POST", f"{depositions}/{found['id']}/actions/newversion",
                          token=token)
            latest = str(((newer or {}).get("links") or {}).get("latest_draft") or "")
            if not latest:
                raise ShareRefused("Zenodo made no new version of the record.",
                                   code="environment.service.unreachable")
            deposition = _call("GET", latest, token=token)
    if deposition is None:
        deposition = _call("POST", depositions, token=token,
                           body={"metadata": {"prereserve_doi": True}})
    if not isinstance(deposition, dict) or not deposition.get("id"):
        raise ShareRefused("Zenodo made no draft.", code="environment.service.unreachable")
    identifier = str(deposition["id"])
    kept[site] = {"id": identifier, "version": version}
    (study / SHARED_TO).write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
    inherited = deposition.get("metadata") if isinstance(deposition.get("metadata"), dict) \
        else {}
    inherited = {k: v for k, v in inherited.items() if k not in ("doi", "prereserve_doi")}
    doi = str(((deposition.get("metadata") or {}).get("prereserve_doi") or {}).get("doi") or "")
    if not doi:
        reserved = _call("PUT", f"{depositions}/{identifier}", token=token,
                         body={"metadata": {**inherited, "prereserve_doi": True}})
        doi = str((((reserved or {}).get("metadata") or {}).get("prereserve_doi") or {})
                  .get("doi") or "")
    if doi:
        packed = pack(f"https://doi.org/{doi}")
    kept[site]["doi"] = doi
    (study / SHARED_TO).write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
    bucket = str((deposition.get("links") or {}).get("bucket") or "")
    if not bucket:
        raise ShareRefused("Zenodo's draft has no bucket to upload into.",
                           code="environment.service.unreachable")
    for entry in deposition.get("files") or []:
        if isinstance(entry, dict) and entry.get("id"):
            _call("DELETE", f"{depositions}/{identifier}/files/{entry['id']}", token=token)
    if said:
        said(f"Uploading {archive.name} ({archive.stat().st_size / 1e6:.1f} MB) to {site}...")
    _call("PUT", f"{bucket}/{urllib.parse.quote(archive.name)}", token=token, upload=archive)
    ours = _metadata(packed["crate"], authors=authors, license_id=license_id, system=system,
                     version=version)
    _call("PUT", f"{depositions}/{identifier}", token=token,
          body={"metadata": {**inherited, **ours}})
    page = str((deposition.get("links") or {}).get("html")
               or f"{base}/uploads/{identifier}")
    if said:
        said(f"Draft {f'of version {version} ' if version > 1 else ''}made: {page}"
             + (f" (DOI {doi}, reserved until you publish)." if doi else ".")
             + " Read it there and press Publish; until then nothing is public.")
    return {"url": page, "doi": doi, "id": identifier, "version": version, **packed}
