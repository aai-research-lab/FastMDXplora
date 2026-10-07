"""A shared study is made a draft on Zenodo, never a publication.

`--share-to zenodo` packs the study first, so a study that cannot be shared
leaves nothing on Zenodo; asks Zenodo for a draft with its DOI reserved,
packs the archive naming that DOI as its own, uploads it and fills in the
draft's description from the records. Shared again before it is
published, the same draft is made again; after, a new version.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
import zipfile
from pathlib import Path

import pytest

from fastmdxplora.sharing.crate import METADATA
from tests.test_a_study_is_shared import _study

class _Zenodo(http.server.BaseHTTPRequestHandler):
    """Zenodo's deposition and record API, as developers.zenodo.org gives it."""

    calls: list = []
    stored: dict = {}
    submitted = False
    wrong_md5 = False

    def _json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _base(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def _deposition(self, number):
        return {"id": number, "submitted": type(self).submitted,
                "metadata": {"prereserve_doi": {"doi": f"10.5072/zenodo.{number}"}},
                "links": {"bucket": f"{self._base()}/files/b{number}",
                          "html": f"{self._base()}/uploads/{number}",
                          "latest_draft": f"{self._base()}/api/deposit/depositions/8"},
                "files": [{"id": "f1"}] if type(self).stored.get("archive") else []}

    def do_POST(self):  # noqa: N802
        type(self).calls.append(("POST", self.path, self.headers.get("Authorization")))
        self._body()
        if self.path == "/api/deposit/depositions":
            return self._json(self._deposition(7), 201)
        if self.path.endswith("/actions/newversion"):
            return self._json(self._deposition(7))
        self._json({}, 404)

    def do_PUT(self):  # noqa: N802
        body = self._body()
        type(self).calls.append(("PUT", self.path, self.headers.get("Authorization")))
        if self.path.startswith("/files/"):
            type(self).stored["archive"] = body
            type(self).stored["name"] = self.path.rsplit("/", 1)[1]
            return self._json({"key": self.path.rsplit("/", 1)[1]})
        type(self).stored["metadata"] = json.loads(body)["metadata"]
        self._json(self._deposition(int(self.path.rsplit("/", 1)[1])))

    def do_DELETE(self):  # noqa: N802
        type(self).calls.append(("DELETE", self.path, self.headers.get("Authorization")))
        self.send_response(204)
        self.end_headers()

    def do_GET(self):  # noqa: N802
        type(self).calls.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path.startswith("/api/deposit/depositions/"):
            number = int(self.path.rsplit("/", 1)[1])
            if number not in (7, 8):
                return self._json({"message": "not found"}, 404)
            return self._json(self._deposition(number))
        archive = type(self).stored.get("archive", b"")
        name = type(self).stored.get("name", "shared.zip")
        if self.path == "/api/records/7":
            md5 = "0" * 32 if type(self).wrong_md5 else hashlib.md5(archive).hexdigest()
            return self._json({"id": 7, "doi": "10.5072/zenodo.7",
                               "metadata": {"version": "1"},
                               "files": [{"key": name, "size": len(archive),
                                          "checksum": f"md5:{md5}"}]})
        if self.path.startswith(f"/records/7/files/{name}"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(archive)))
            self.end_headers()
            self.wfile.write(archive)
            return
        self._json({}, 404)

    def log_message(self, *args):
        return


@pytest.fixture
def zenodo(monkeypatch):
    _Zenodo.calls, _Zenodo.stored, _Zenodo.submitted, _Zenodo.wrong_md5 = [], {}, False, False
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Zenodo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("FASTMDXPLORA_ZENODO_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ZENODO_SANDBOX_TOKEN", "sandbox-token")
    yield _Zenodo
    server.shutdown()


def _share_to_zenodo(study: Path, out: Path) -> int:
    from fastmdxplora.cli.main import main

    return main(["report", "--output", str(study), "--share", str(out),
                 "--share-author", "Adekunle Aina;0000-0002-8215-7452",
                 "--share-to", "zenodo-sandbox"])


def test_a_draft_is_made_with_its_doi_in_the_archive(tmp_path, zenodo):
    study = _study(tmp_path / "study")
    out = tmp_path / "shared.zip"
    assert _share_to_zenodo(study, out) == 0
    with zipfile.ZipFile(out) as archive:
        root = json.loads(archive.read(METADATA))["@graph"][1]
    assert root["identifier"] == "https://doi.org/10.5072/zenodo.7"
    assert zenodo.stored["archive"] == out.read_bytes()
    metadata = zenodo.stored["metadata"]
    assert metadata["creators"] == [{"name": "Aina, Adekunle", "orcid": "0000-0002-8215-7452"}]
    assert (metadata["license"], metadata["upload_type"]) == ("cc-by-4.0", "dataset")
    assert {"identifier": "10.2210/pdb1abc/pdb", "relation": "isDerivedFrom",
            "scheme": "doi"} in metadata["related_identifiers"]
    assert all(auth == "Bearer sandbox-token" for _, _, auth in zenodo.calls)
    assert not any("publish" in path for _, path, _ in zenodo.calls)
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["id"] == "7"
    # Shared again before it was published: the same draft, its file replaced.
    assert _share_to_zenodo(study, out) == 0
    assert ("DELETE", "/api/deposit/depositions/7/files/f1", "Bearer sandbox-token") \
        in zenodo.calls
    # Shared again once published: a new version of the record.
    zenodo.submitted = True
    assert _share_to_zenodo(study, out) == 0
    assert any(path.endswith("/7/actions/newversion") for _, path, _ in zenodo.calls)


def test_no_token_no_draft(tmp_path, zenodo, monkeypatch, capsys):
    monkeypatch.delenv("ZENODO_SANDBOX_TOKEN")
    assert _share_to_zenodo(_study(tmp_path / "study"), tmp_path / "shared.zip") == 2
    assert "ZENODO_SANDBOX_TOKEN" in capsys.readouterr().err
    assert zenodo.calls == []


def test_a_study_that_cannot_be_shared_makes_no_draft(tmp_path, zenodo):
    study = _study(tmp_path / "study", phase_status="error")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert zenodo.calls == []


def test_a_deleted_draft_is_made_again(tmp_path, zenodo):
    study = _study(tmp_path / "study")
    (study / "shared_to.json").write_text(json.dumps({"zenodo-sandbox": {"id": "99"}}))
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert ("POST", "/api/deposit/depositions", "Bearer sandbox-token") in zenodo.calls
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["id"] == "7"
