"""A shared study reaches Zenodo, and comes back from it, whole.

A study of a paper is gigabytes, and a connection is not always there for
as long: Zenodo limits how often it is asked (429, saying how long to wait)
and is at times briefly unable to answer (502, 503, 504). A download cut
short goes on from where it stopped, an answer that says to ask again is
asked again after a wait, and the archive is checked against Zenodo's MD5
as it lands. A request that makes something is never asked twice where
the first may have made it.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import threading
from pathlib import Path

import pytest

from fastmdxplora.sharing import ShareRefused, zenodo
from fastmdxplora.sharing.opening import _download, open_shared
from tests.test_a_shared_study_goes_to_zenodo import _share_to_zenodo, _Zenodo
from tests.test_a_study_is_shared import _study


class _Unsteady(_Zenodo):
    """Zenodo as above, made to fail as asked: ``faults`` maps a request
    (``"GET file"``, ``"PUT file"``, ``"POST"``, ``"GET record"``) to the
    answers it gives first, in order: an HTTP status, ``"cut"`` (the file's
    first bytes, then the connection closed), ``"whole"`` (the whole file
    with 200, whatever range was asked), ``"elsewhere"`` (206 with the file
    from byte 100, whatever range was asked), ``"stall"`` (the file's first
    bytes, then nothing for 3 s), ``"drop"`` (the connection closed with no
    answer) or ``"made"`` (a draft made, then a 504 in place of the
    answer), ``"made late"`` (as ``"made"``, the draft listed only from the
    third look) or ``"504 cut"`` (a 504 whose own words are cut short). It
    lists the person's drafts as Zenodo does, and its drafts' files carry
    their names (``files``, set by a test)."""

    faults: dict = {}
    ranges: list = []
    payload: bytes | None = None
    drafts: list = []
    files: list | None = None
    late = 2

    gone: set = set()
    uploaded_to: dict = {}
    edited: dict = {}
    latest: dict | None = None
    files_of: dict = {}
    # Drafts of a new version of record 7 there is (Zenodo keeps one).
    versions_begun: list = []
    # Set by a test: each draft keeps the metadata it was made and written
    # with, as Zenodo does, and only the drafts named are published.
    meta: dict | None = None
    published_ids: set | None = None

    def _deposition(self, number):
        made = super()._deposition(number)
        # A draft holds what was uploaded to its own bucket (one made since
        # holds none), where a test does not say.
        uploaded = type(self).uploaded_to.get(number)
        made["files"] = [{"id": "f1", "filename": uploaded}] if uploaded else []
        if type(self).files is not None:
            made["files"] = type(self).files
        if type(self).meta is not None:
            made["metadata"].update(type(self).meta.get(number, {}))
        if type(self).published_ids is not None:
            made["submitted"] = number in type(self).published_ids
        made["metadata"].update(type(self).edited.get(number, {}))
        if number in type(self).files_of:
            made["files"] = type(self).files_of[number]
        if type(self).latest is not None:
            made["links"]["latest"] = f"{self._base()}/api/records/latest"
        made["conceptrecid"] = "c7"
        return made

    def _fault(self, which):
        queue = type(self).faults.get(which) or []
        return queue.pop(0) if queue else None

    def _list(self):
        fault = self._fault("GET list")
        type(self).calls.append(("GET", self.path, self.headers.get("Authorization")))
        if isinstance(fault, int):
            return self._status(fault)
        shown = []
        for draft in type(self).drafts:
            if draft["hidden"]:
                draft["hidden"] -= 1
                continue
            shown.append({"id": draft["id"], "metadata": {"title": draft["title"]}})
        shown += [self._deposition(n) for n in type(self).versions_begun]
        return self._json(shown)

    def _drop(self):
        self.close_connection = True
        self.connection.shutdown(2)

    def _status(self, status):
        if status == "504 cut":
            self.send_response(504)
            self.send_header("Content-Length", "400")
            self.end_headers()
            self.wfile.write(b'{"message": ')
            self.wfile.flush()
            return self._drop()
        self.send_response(status)
        if status == 429:
            self.send_header("Retry-After", "7")
        # Zenodo's own refusals say so in JSON; a gateway's 5xx says nothing.
        said = json.dumps({"status": status, "message": "refused"}).encode() \
            if 400 <= status < 500 else b""
        self.send_header("Content-Length", str(len(said)))
        self.end_headers()
        self.wfile.write(said)

    def do_POST(self):  # noqa: N802
        latest = type(self).latest
        if self.path.endswith("/actions/newversion") and latest is not None \
                and f"/{latest['id']}/actions/" not in self.path:
            # As Zenodo documents: a new version only of the latest one.
            type(self).calls.append(("POST", self.path, None))
            self._body()
            return self._json({"message": "not the latest version"}, 400)
        if self.path.endswith("/actions/newversion"):
            # A new version's draft begun again, the one deleted replaced;
            # with the files of the version it is of.
            type(self).gone.clear()
            if 7 in type(self).uploaded_to:
                type(self).uploaded_to[8] = type(self).uploaded_to[7]
            if 7 in type(self).files_of:
                type(self).files_of[8] = [dict(f) for f in type(self).files_of[7]]
            if type(self).meta is not None:
                # As Zenodo begins one: the latest version's description.
                type(self).meta[8] = {k: v for k, v in type(self).meta.get(7, {}).items()
                                      if k not in ("doi", "publication_date", "version")}
            if self._fault("POST newversion") == "made":
                type(self).versions_begun.append(8)
                type(self).calls.append(("POST", self.path, None))
                self._body()
                return self._status(504)
        fault = self._fault("POST")
        if self.path == "/api/deposit/depositions" and fault in (None, "made", "made late"):
            body = self._body()
            title = (json.loads(body or b"{}").get("metadata") or {}).get("title")
            self.rfile = __import__("io").BytesIO(body)
            self.headers.replace_header("Content-Length", str(len(body))) \
                if "Content-Length" in self.headers else None
            # Made at once, or seen among the drafts only from a later look.
            type(self).drafts.append({"id": 7 + len(type(self).drafts), "title": title,
                                      "hidden": type(self).late if fault == "made late"
                                      else 0})
        if fault in ("made", "made late"):
            type(self).calls.append(("POST", self.path, self.headers.get("Authorization")))
            return self._status(504)
        if self.path == "/api/deposit/depositions" and fault is None:
            type(self).calls.append(("POST", self.path, self.headers.get("Authorization")))
            made = type(self).drafts[-1]
            if type(self).meta is not None:
                type(self).meta[made["id"]] = {"title": made["title"]}
            answer = self._deposition(made["id"])
            answer["metadata"]["title"] = made["title"]
            return self._json(answer, 201)
        if fault == "504 cut":
            type(self).calls.append(("POST", self.path, self.headers.get("Authorization")))
            self._body()
            return self._status("504 cut")
        if fault == "drop":
            type(self).calls.append(("POST", self.path, self.headers.get("Authorization")))
            self._body()
            return self._drop()
        if isinstance(fault, int):
            type(self).calls.append(("POST", self.path, self.headers.get("Authorization")))
            self._body()
            return self._status(fault)
        return super().do_POST()

    def do_DELETE(self):  # noqa: N802
        fault = self._fault("DELETE")
        if isinstance(fault, int):
            type(self).calls.append(("DELETE", self.path, self.headers.get("Authorization")))
            return self._status(fault)
        # A file a test lists, taken away (as Zenodo takes it).
        if "/files/" in self.path:
            number, _, gone = self.path.split("/")[-3:]
            for listed in (type(self).files, type(self).files_of.get(int(number))):
                if listed is not None:
                    listed[:] = [f for f in listed if str(f.get("id")) != gone]
        return super().do_DELETE()

    def do_PUT(self):  # noqa: N802
        if self.path.startswith("/api/deposit/depositions/"):
            fault = self._fault("PUT metadata")
            if isinstance(fault, int):
                type(self).calls.append(("PUT", self.path, self.headers.get("Authorization")))
                self._body()
                return self._status(fault)
            if type(self).meta is not None:
                body = self._body()
                number = int(self.path.rsplit("/", 1)[1])
                sent = dict(json.loads(body)["metadata"])
                sent.pop("prereserve_doi", None)
                type(self).meta[number] = {**type(self).meta.get(number, {}), **sent}
                type(self).stored["metadata"] = json.loads(body)["metadata"]
                type(self).calls.append(("PUT", self.path, self.headers.get("Authorization")))
                if fault == "applied":
                    # Kept, and its answer lost.
                    return self._status(500)
                return self._json(self._deposition(number))
        if self.path.startswith("/files/"):
            fault = self._fault("PUT file")
            if isinstance(fault, int):
                type(self).calls.append(("PUT", self.path, self.headers.get("Authorization")))
                self._body()
                return self._status(fault)
            bucket, name = self.path.split("/")[2:4]
            type(self).uploaded_to[int(bucket.lstrip("b"))] = name
            # A file of that name a test lists as the upload begins holds what
            # was uploaded (one a test lists once it is answered is the test's).
            self._listed = [*(type(self).files or []),
                            *type(self).files_of.get(int(bucket.lstrip("b")), [])]
            return super().do_PUT()
        return super().do_PUT()

    def _uploaded(self):
        bucket, name = self.path.split("/")[2:4]
        sent = hashlib.md5(type(self).stored["archive"]).hexdigest()  # noqa: S324
        for entry in getattr(self, "_listed", []):
            if entry.get("filename") == name and "checksum" in entry:
                entry["checksum"] = sent
        # And one a test lists the draft's files by now holds it.
        for holding in (type(self).files, type(self).files_of.get(int(bucket.lstrip("b")))):
            if holding is not None and name not in {f.get("filename") for f in holding}:
                holding.append({"id": f"up-{name}", "filename": name})

    def do_GET(self):  # noqa: N802
        archive = type(self).payload or type(self).stored.get("archive", b"")
        name = type(self).stored.get("name", "shared.zip")
        if self.path.startswith("/api/deposit/depositions?"):
            return self._list()
        if self.path == "/api/records/latest" and type(self).latest is not None:
            # As Zenodo's records say it: the last version (unless a test
            # says otherwise).
            answer = json.loads(json.dumps(type(self).latest))
            answer.setdefault("metadata", {}).setdefault(
                "relations", {"version": [{"index": 0, "is_last": True}]})
            return self._json(answer)
        latest = type(self).latest
        if latest is not None and self.path == f"/api/deposit/depositions/{latest['id']}":
            made = self._deposition(7)
            made.update(id=latest["id"], submitted=True,
                        files=[{"id": f["key"], "filename": f["key"]} for f in latest["files"]])
            made["metadata"].update(latest["metadata"])
            return self._json(made)
        if self.path.startswith("/api/deposit/depositions/"):
            fault = self._fault("GET deposition")
            if isinstance(fault, int):
                return self._status(fault)
            if self.path.rsplit("/", 1)[1] in {str(n) for n in type(self).gone}:
                type(self).calls.append(("GET", self.path, None))
                return self._json({"message": "not found"}, 404)
        if self.path == "/api/records/7":
            fault = self._fault("GET record")
            if fault == "drop":
                type(self).calls.append(("GET", self.path, None))
                return self._drop()
            if isinstance(fault, int):
                return self._status(fault)
            if type(self).payload is not None:
                return self._json({"id": 7, "doi": "10.5072/zenodo.7",
                                   "files": [{"key": name, "size": len(archive),
                                              "checksum": "md5:"
                                              + hashlib.md5(archive).hexdigest()}]})
        if self.path.startswith(f"/records/7/files/{name}"):
            asked = self.headers.get("Range")
            type(self).ranges.append(asked)
            fault = self._fault("GET file")
            if fault == 416 and not asked:
                fault = None
            if isinstance(fault, int):
                return self._status(fault)
            if fault in ("page", "plain page"):
                # A gateway's page in place of the file.
                page = b"<html><body>Bad gateway</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8" if fault == "page"
                                 else "text/plain")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            start = int(asked.split("=")[1].split("-")[0]) if asked and fault != "whole" else 0
            if start >= len(archive) > 0:
                # A range past the end, as servers answer it.
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(archive)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if fault == "elsewhere":
                start = 100
            body = archive[start:]
            self.send_response(206 if start else 200)
            if start:
                self.send_header("Content-Range", f"bytes {start}-{len(archive) - 1}/"
                                                  f"{len(archive)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if fault == "stall":
                import time

                self.wfile.write(body[:len(body) // 3])
                self.wfile.flush()
                time.sleep(3)
                self.close_connection = True
                return
            if fault == "cut":
                self.wfile.write(body[:len(body) // 3])
                self.wfile.flush()
                self.close_connection = True
                self.connection.shutdown(2)
                return
            self.wfile.write(body)
            return
        return super().do_GET()


@pytest.fixture
def unsteady(monkeypatch):
    _Unsteady.calls, _Unsteady.stored, _Unsteady.submitted = [], {}, False
    _Unsteady.wrong_md5, _Unsteady.faults, _Unsteady.ranges = False, {}, []
    _Unsteady.payload, _Unsteady.drafts, _Unsteady.files = None, [], None
    _Unsteady.late, _Unsteady.gone, _Unsteady.edited = 2, set(), {}
    _Unsteady.latest, _Unsteady.files_of, _Unsteady.versions_begun = None, {}, []
    _Unsteady.meta, _Unsteady.published_ids = None, None
    _Unsteady.uploaded_to = {}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Unsteady)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("FASTMDXPLORA_ZENODO_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ZENODO_SANDBOX_TOKEN", "sandbox-token")
    waits: list[float] = []
    monkeypatch.setattr(zenodo, "_sleep", waits.append)
    _Unsteady.waits = waits
    yield _Unsteady
    server.shutdown()


def _shared(tmp_path, unsteady) -> None:
    assert _share_to_zenodo(_study(tmp_path / "study"), tmp_path / "shared.zip") == 0
    unsteady.calls.clear()


def test_a_download_cut_short_goes_on_from_where_it_stopped(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET file": ["cut", "cut"]}
    told: list[str] = []
    opened = open_shared("10.5072/zenodo.7", workspace=tmp_path / "w", said=told.append)
    assert (opened / "shared_from.json").is_file()
    assert unsteady.ranges[0] is None
    assert unsteady.ranges[1].startswith("bytes=") and unsteady.ranges[1] != "bytes=0-"
    assert len(unsteady.ranges) == 3
    assert any("stopped at" in line and "trying again" in line for line in told)


def test_a_server_that_sends_the_whole_file_again_is_read_from_its_start(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET file": ["cut", "whole"]}
    told: list[str] = []
    opened = open_shared("10.5072/zenodo.7", workspace=tmp_path / "w", said=told.append)
    assert (opened / "shared_from.json").is_file()
    assert any("sent the whole file again, not the rest of it" in line for line in told)


def test_too_many_requests_waits_as_long_as_zenodo_asks(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET record": [429], "GET file": [503, 429]}
    open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert unsteady.waits == [7.0, 1.0, 7.0]


def test_zenodo_unable_for_longer_than_the_tries_is_said(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET file": [503] * zenodo.TRIES}
    with pytest.raises(ShareRefused) as refused:
        open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert refused.value.refusal.code == "environment.service.unreachable"
    assert f"HTTP 503, still after {zenodo.TRIES} tries" in str(refused.value)
    assert not list((tmp_path / "w").glob("*/shared_from.json"))


def test_a_resumed_download_is_still_checked_against_zenodo_s_md5(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET file": ["cut"]}
    unsteady.wrong_md5 = True
    with pytest.raises(ShareRefused) as refused:
        open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert refused.value.refusal.code == "environment.share.unverified"
    # Checked once whole: the rest was fetched before the MD5 was compared.
    assert len(unsteady.ranges) == 2 and unsteady.ranges[1].startswith("bytes=")


def test_an_upload_zenodo_could_not_take_is_sent_again(tmp_path, unsteady):
    unsteady.faults = {"PUT file": [503, 502]}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.stored["archive"] == (tmp_path / "shared.zip").read_bytes()
    assert [c[0] for c in unsteady.calls if c[1].startswith("/files/")] == ["PUT"] * 3


def test_a_draft_is_never_asked_for_twice_where_the_first_may_have_been_made(tmp_path,
                                                                              unsteady,
                                                                              capsys):
    unsteady.faults = {"POST": [503]}
    assert _share_to_zenodo(_study(tmp_path / "study"), tmp_path / "shared.zip") == 2
    assert [c for c in unsteady.calls if c[0] == "POST"] == [
        ("POST", "/api/deposit/depositions", "Bearer sandbox-token")]
    assert "HTTP 503" in capsys.readouterr().err
    # Too many requests: Zenodo made nothing, and the draft is asked for again.
    unsteady.calls.clear()
    unsteady.faults = {"POST": [429]}
    assert _share_to_zenodo(_study(tmp_path / "again"), tmp_path / "again.zip") == 0
    assert [c[1] for c in unsteady.calls if c[0] == "POST"] == [
        "/api/deposit/depositions"] * 2


def test_a_large_transfer_says_how_far_it_has_gone(tmp_path, unsteady):
    unsteady.payload = os.urandom(12 << 20)
    told: list[str] = []
    _download(f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/records/7/files/shared.zip",
              tmp_path / "got.zip", most=1 << 30,
              md5=hashlib.md5(unsteady.payload).hexdigest(), said=told.append,
              size=len(unsteady.payload))
    assert (tmp_path / "got.zip").read_bytes() == unsteady.payload
    progress = [line for line in told if line.startswith("Downloaded ") and " of " in line]
    assert 5 <= len(progress) <= 9 and progress[-1].endswith("of 12.6 MB.")
    told.clear()
    big = tmp_path / "big.zip"
    big.write_bytes(unsteady.payload)
    zenodo._call("PUT", f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/files/b7/big.zip",
                 upload=big, said=told.append)
    assert unsteady.stored["archive"] == unsteady.payload
    assert 5 <= len([line for line in told if line.startswith("Uploaded ")]) <= 9


def test_the_wait_is_what_zenodo_asks_within_a_minute():
    assert zenodo.wait_before(1, "7") == 7.0
    assert zenodo.wait_before(3, None) == 4.0
    assert zenodo.wait_before(2, "Wed, 21 Oct 2026 07:28:00 GMT") == 2.0
    assert zenodo.wait_before(1, "3600") == 60.0
    assert json.dumps(sorted(zenodo.AGAIN)) == "[429, 502, 503, 504]"



def test_a_part_from_anywhere_but_where_it_stopped_is_asked_for_again(tmp_path, unsteady):
    """Review of round 1: a part of the file from another place was added
    where the download had stopped."""
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET file": ["cut", "elsewhere"]}
    opened = open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert (opened / "shared_from.json").is_file()
    assert len(unsteady.ranges) == 3 and unsteady.ranges[2] == unsteady.ranges[1]


def test_a_disk_that_fills_is_said_not_taken_for_a_lost_connection(tmp_path, unsteady,
                                                                   monkeypatch):
    """Review of round 1: a piece this computer could not write was taken
    for a connection cut, skipped, and the MD5 passed over the gap."""
    import errno
    import io

    unsteady.payload = os.urandom(3 << 20)
    real = io.open
    writes = []
    failing = [errno.ENOSPC, "No space left on device"]

    class Full:
        def __init__(self, handle):
            self.handle = handle

        def write(self, piece):
            writes.append(len(piece))
            if len(writes) == 2:
                raise OSError(*failing)
            return self.handle.write(piece)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    target = tmp_path / "got.zip"

    def opened(file, mode="r", *args, **kwargs):
        handle = real(file, mode, *args, **kwargs)
        return Full(handle) if str(file) == str(target) and "w" in mode else handle

    monkeypatch.setattr(io, "open", opened)
    with pytest.raises(ShareRefused) as failed:
        _download(f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/records/7/files/shared.zip",
                  target, most=1 << 30, md5=hashlib.md5(unsteady.payload).hexdigest(),
                  said=None, size=len(unsteady.payload))
    assert failed.value.refusal.code == "environment.share.too_large"
    assert f"could not be written to {tmp_path}: the disk there has no room for it" \
        in str(failed.value)
    assert unsteady.ranges == [None]
    # Another error writing is the system's own, never asked for again.
    writes.clear()
    failing[:] = [errno.EIO, "Input/output error"]
    with pytest.raises(OSError):
        _download(f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/records/7/files/shared.zip",
                  target, most=1 << 30, md5="", said=None, size=len(unsteady.payload))
    assert unsteady.ranges == [None, None]


def test_a_request_that_lost_its_connection_is_asked_again_unless_it_makes_something(
        tmp_path, unsteady):
    """Review of round 1: asking again after a connection lost, and not for a
    draft, were untested."""
    _shared(tmp_path, unsteady)
    unsteady.calls.clear()
    unsteady.faults = {"GET record": ["drop"]}
    open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert [c for c in unsteady.calls if c[1] == "/api/records/7"] == [
        ("GET", "/api/records/7", None)] * 2
    unsteady.calls.clear()
    unsteady.faults = {"POST": ["drop"]}
    assert _share_to_zenodo(_study(tmp_path / "again"), tmp_path / "again.zip") == 2
    assert [c[1] for c in unsteady.calls if c[0] == "POST"] == ["/api/deposit/depositions"]
    base = os.environ["FASTMDXPLORA_ZENODO_URL"]
    assert zenodo._call("DELETE", f"{base}/api/deposit/depositions/99", missing_ok=True) \
        is None


def test_the_wait_is_until_zenodo_s_limit_resets():
    import time

    reset = time.time() + 5
    assert 5.0 <= zenodo.wait_before(1, {"X-RateLimit-Reset": str(reset)}) <= 6.5
    assert zenodo.wait_before(2, {"X-RateLimit-Reset": "0"}) == 2.0
    assert zenodo.wait_before(1, {"Retry-After": "3", "X-RateLimit-Reset": str(reset)}) == 3.0


def test_a_draft_made_whose_answer_was_lost_is_taken_not_made_again(tmp_path, unsteady):
    """Hands-on review of round 1: Zenodo made the draft and its answer was
    lost (a 504), and sharing again made a second, the first left empty."""
    unsteady.faults = {"POST": ["made"]}
    told: list[str] = []
    from fastmdxplora.sharing.pack import share_study

    study = _study(tmp_path / "study")
    zenodo.draft(study, tmp_path / "shared.zip", site="zenodo-sandbox",
                 pack=lambda doi: share_study(study, tmp_path / "shared.zip",
                                              authors=[("A B", "")], identifier=doi),
                 authors=[("A B", "")], license_id="CC-BY-4.0", system="1ABC",
                 said=told.append)
    assert [c[1] for c in unsteady.calls if c[0] == "POST"] == ["/api/deposit/depositions"]
    assert [d["id"] for d in unsteady.drafts] == [7]
    assert unsteady.stored["archive"] == (tmp_path / "shared.zip").read_bytes()
    assert any("Zenodo made draft 7, asked for by this study; taking that one" in line
               for line in told)


def test_a_draft_keeps_its_archive_until_the_new_one_is_there(tmp_path, unsteady, capsys):
    """Hands-on review of round 1: shared again while Zenodo would take no
    upload, the draft lost the archive it had, and the message said neither
    that nor what to do."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    capsys.readouterr()
    unsteady.calls.clear()
    unsteady.stored["archive"] = b"the archive it had"
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[0] == "DELETE" for c in unsteady.calls)
    assert unsteady.stored["archive"] == b"the archive it had"
    said = capsys.readouterr().err
    assert "/uploads/7 is kept, with the archive it had" in said
    assert "share again once Zenodo answers" in said
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert not any(c[0] == "POST" for c in unsteady.calls)


def test_a_stalled_download_is_asked_for_again_from_where_it_stopped(tmp_path, unsteady,
                                                                     monkeypatch):
    """Hands-on review of round 1: a server that stalled took two minutes a
    try before the download went on, twelve to give up."""
    from fastmdxplora.sharing import opening

    _shared(tmp_path, unsteady)
    monkeypatch.setattr(opening, "STALLED_S", 1)
    unsteady.faults = {"GET file": ["stall"]}
    told: list[str] = []
    opened = open_shared("10.5072/zenodo.7", workspace=tmp_path / "w", said=told.append)
    assert (opened / "shared_from.json").is_file()
    assert unsteady.ranges[1].startswith("bytes=")
    assert opening.STALLED_S == 1 and zenodo.TRIES == 5


def test_a_refused_open_leaves_no_folder_it_made(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.wrong_md5 = True
    with pytest.raises(ShareRefused):
        open_shared("10.5072/zenodo.7", into=tmp_path / "new" / "place")
    assert not (tmp_path / "new" / "place").exists()


def test_asking_zenodo_again_for_a_record_is_said(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET record": [503]}
    told: list[str] = []
    open_shared("10.5072/zenodo.7", workspace=tmp_path / "w", said=told.append)
    assert any("/api/records/7 with HTTP 503; asking again in 1 s (2 of 5)" in line
               for line in told)


def test_giving_up_says_how_far_the_download_got(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET file": ["cut"] + [503] * zenodo.TRIES}
    with pytest.raises(ShareRefused) as refused:
        open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    size = len(unsteady.stored["archive"])
    # (A small archive's amounts in kB, not "0.0 of 0.0 MB".)
    assert size < 100_000
    assert str(refused.value).endswith(
        f"HTTP 503, still after {zenodo.TRIES} tries, with "
        f"{(size - size * 2 // 3) / 1e3:.1f} of {size / 1e3:.1f} kB downloaded. Nothing was "
        "kept; open it again later.")


def test_a_draft_the_person_made_is_never_taken(tmp_path, unsteady, capsys):
    """Review of round 2: a draft that appeared among the person's after a
    failed request was taken as this share's, theirs or not, and its files
    and description replaced."""
    unsteady.drafts = [{"id": 8, "title": "My own draft", "hidden": 0}]
    for fault in (400, 503, "drop"):
        unsteady.calls.clear()
        unsteady.faults = {"POST": [fault]}
        assert _share_to_zenodo(_study(tmp_path / str(fault)), tmp_path / f"{fault}.zip") == 2
        assert not any("/8" in c[1] for c in unsteady.calls), fault
    assert "may still make the draft (titled 'FastMDXplora: sharing" in capsys.readouterr().err


def test_a_draft_listed_late_is_still_the_one_taken(tmp_path, unsteady):
    """Review of round 2: a draft Zenodo listed only a moment after its
    gateway gave up was not found, and sharing again made a second."""
    unsteady.faults = {"POST": ["made late"]}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert [c[1] for c in unsteady.calls if c[0] == "POST"] == ["/api/deposit/depositions"]
    assert unsteady.waits[:2] == [2.0, 4.0]
    # Listed only after every look: found at the next share, not made again.
    unsteady.calls.clear()
    unsteady.drafts = []
    # Another study's draft, not the first one's (the stand-in has but one).
    unsteady.stored.clear()
    unsteady.uploaded_to.clear()
    unsteady.faults = {"POST": ["made late"]}
    study = _study(tmp_path / "later")
    (study / "shared_to.json").unlink(missing_ok=True)
    zenodo._LOOKS_S, looks = (1.0,), zenodo._LOOKS_S
    try:
        assert _share_to_zenodo(study, tmp_path / "later.zip") == 2
        assert "asked_as" in json.loads((study / "shared_to.json").read_text())[
            "zenodo-sandbox"]
        # Zenodo has since finished making it.
        unsteady.drafts[0]["hidden"] = 0
        assert _share_to_zenodo(study, tmp_path / "later.zip") == 0
    finally:
        zenodo._LOOKS_S = looks
    assert [c[1] for c in unsteady.calls if c[0] == "POST"] == ["/api/deposit/depositions"]
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["id"] == "7"


def test_an_answer_whose_own_words_are_cut_is_a_refusal(tmp_path, unsteady, capsys):
    """Review of round 2: Zenodo's 504 cut short in its own words escaped as
    a traceback, the draft it may have made never looked for."""
    unsteady.faults = {"POST": ["504 cut"]}
    assert _share_to_zenodo(_study(tmp_path / "study"), tmp_path / "shared.zip") == 2
    assert "HTTP 504" in capsys.readouterr().err
    assert any(c[1].startswith("/api/deposit/depositions?") for c in unsteady.calls)


def test_a_cut_is_seen_where_zenodo_records_no_size(tmp_path, unsteady):
    unsteady.payload = os.urandom(3 << 20)
    unsteady.faults = {"GET file": ["cut"]}
    _download(f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/records/7/files/shared.zip",
              tmp_path / "got.zip", most=1 << 30, md5="", said=None, size=0)
    assert (tmp_path / "got.zip").read_bytes() == unsteady.payload
    assert unsteady.ranges[1].startswith("bytes=")


def test_a_kept_draft_is_said_as_it_is(tmp_path, unsteady, capsys):
    """Review of round 2: a draft whose new archive was up, its description
    not, was said to keep the archive it had."""
    unsteady.faults = {"PUT metadata": [400]}
    unsteady.files = []
    assert _share_to_zenodo(_study(tmp_path / "study"), tmp_path / "shared.zip") == 2
    assert "is kept, with the new archive, its description not yet written" in \
        capsys.readouterr().err


def test_a_draft_s_archive_of_the_same_name_is_replaced_and_others_go(tmp_path, unsteady):
    """Review of round 2: the stand-in's files had no names, so a test
    passed taking away the archive just uploaded."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.calls.clear()
    unsteady.files = [{"id": "same", "filename": "shared.zip"},
                      {"id": "other", "filename": "old-name.zip"}]
    _packed_before_as(study, "old-name.zip")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    deleted = [c[1] for c in unsteady.calls if c[0] == "DELETE"]
    assert deleted == ["/api/deposit/depositions/7/files/other"]
    put = next(i for i, c in enumerate(unsteady.calls) if c[1].startswith("/files/"))
    assert put < next(i for i, c in enumerate(unsteady.calls) if c[0] == "DELETE")


def test_every_folder_made_to_open_into_is_taken_away(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.wrong_md5 = True
    with pytest.raises(ShareRefused):
        open_shared("10.5072/zenodo.7", into=tmp_path / "new" / "deeper" / "place")
    assert not (tmp_path / "new").exists()


def _studied(tmp_path, name):
    return _study(tmp_path / name), tmp_path / f"{name}.zip"


def test_a_draft_asked_for_again_keeps_its_version(tmp_path, unsteady, capsys):
    """Review of round 3: a first share that failed made the next one say
    "version 2", one more for each try."""
    study, out = _studied(tmp_path, "study")
    for fault in (400, "drop"):
        unsteady.faults = {"POST": [fault]}
        assert _share_to_zenodo(study, out) == 2
    assert _share_to_zenodo(study, out) == 0
    assert unsteady.stored["metadata"]["version"] == "1"
    assert "Draft made:" in capsys.readouterr().out


def test_a_draft_not_listed_at_the_next_share_is_not_made_again(tmp_path, unsteady,
                                                                capsys):
    """Review of round 3: at the next share, a listing Zenodo did not answer,
    or one that did not yet show the draft, made a second."""
    study, out = _studied(tmp_path, "study")
    unsteady.late = 99
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, out) == 2
    unsteady.calls.clear()
    unsteady.faults = {"GET list": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, out) == 2
    assert "Zenodo did not list your drafts" in capsys.readouterr().err
    assert not any(c[0] == "POST" for c in unsteady.calls)
    # Listed, still without it: asked for again under the same title, and
    # once both are seen, named for the person rather than a third made.
    unsteady.faults = {}
    assert _share_to_zenodo(study, out) == 0
    titles = {d["title"] for d in unsteady.drafts}
    assert len(unsteady.drafts) == 2 and len(titles) == 1
    unsteady.drafts[0]["hidden"] = 0
    (study / "shared_to.json").write_text(json.dumps(
        {"zenodo-sandbox": {"asked_as": titles.pop(), "version": 1}}))
    assert _share_to_zenodo(study, out) == 2
    assert "Zenodo holds 2 drafts titled" in capsys.readouterr().err


def test_the_looks_for_a_lost_draft_are_few_and_short(tmp_path, unsteady, monkeypatch):
    """Review of round 3: each look asked Zenodo five times, waiting what it
    asked, and the last wait was followed by no look."""
    unsteady.late = len(zenodo._LOOKS_S) - 1
    unsteady.faults = {"POST": ["made late"]}
    timeouts = []
    real = zenodo.urllib.request.urlopen

    def urlopen(request, timeout=None):
        if "?status=draft" in request.full_url:
            timeouts.append(timeout)
        return real(request, timeout=timeout)

    monkeypatch.setattr(zenodo.urllib.request, "urlopen", urlopen)
    study, out = _studied(tmp_path, "study")
    assert _share_to_zenodo(study, out) == 0
    # The looks, and one more once the share is done, for a draft made twice.
    assert len(timeouts) == len(zenodo._LOOKS_S) + 1
    assert set(timeouts) == {zenodo._LOOK_TIMEOUT_S}
    assert sum(unsteady.waits) == sum(zenodo._LOOKS_S)
    unsteady.calls.clear()
    unsteady.faults = {"POST": ["made late"], "GET list": [429] * 9}
    unsteady.late = 99
    assert _share_to_zenodo(*_studied(tmp_path, "slow")) == 2
    looks = [c for c in unsteady.calls if "?status=draft" in c[1]]
    assert len(looks) == len(zenodo._LOOKS_S)


def test_a_connection_never_made_is_not_taken_for_a_lost_answer(tmp_path, unsteady,
                                                                monkeypatch):
    """Review of round 3: a connection that could not be made, nothing sent,
    was looked after for a draft for a minute and a half."""
    import urllib.error

    real = zenodo.urllib.request.urlopen

    def urlopen(request, timeout=None):
        if request.get_method() == "POST":
            raise urllib.error.URLError(OSError(101, "Network is unreachable"))
        return real(request, timeout=timeout)

    monkeypatch.setattr(zenodo.urllib.request, "urlopen", urlopen)
    assert _share_to_zenodo(*_studied(tmp_path, "study")) == 2
    assert not any("?status=draft" in c[1] for c in unsteady.calls)
    assert unsteady.waits == []


def test_a_disk_that_fills_unpacking_or_after_is_said(tmp_path, unsteady, monkeypatch):
    """Hands-on review of round 3: a disk that filled unpacking was said to
    be an unsafe archive, and one that filled writing the study's records
    ended in a traceback."""
    import errno

    from fastmdxplora.sharing import opening

    _shared(tmp_path, unsteady)
    archive = tmp_path / "shared.zip"

    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    for step in ("_unpacked", "_put_back"):
        monkeypatch.setattr(opening, step, full)
        with pytest.raises(ShareRefused) as refused:
            open_shared(str(archive), into=tmp_path / "here")
        assert refused.value.refusal.code == "environment.share.too_large", step
        assert str(refused.value).startswith(
            f"The shared study could not be written to {tmp_path / 'here'}: the disk there "
            "has no room for it"), step
        monkeypatch.undo()
        monkeypatch.setattr(zenodo, "_sleep", unsteady.waits.append)


def test_two_archives_left_on_a_draft_are_said_before_it_is_published(tmp_path, unsteady,
                                                                       capsys):
    """Hands-on review of round 3: an earlier archive Zenodo would not take
    away was left beside the new one, unsaid; published so, the record
    opens as neither."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    capsys.readouterr()
    unsteady.files = [{"id": "other", "filename": "old-name.zip"}]
    _packed_before_as(study, "old-name.zip")
    unsteady.faults = {"DELETE": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "with the new archive and the earlier old-name.zip, which Zenodo may not have " \
        "taken away" in said and "before you publish" in said


def test_a_range_refused_is_read_from_the_start(tmp_path, unsteady):
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET file": ["cut", 416]}
    opened = open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert (opened / "shared_from.json").is_file()
    assert unsteady.ranges[-1] is None


def test_what_to_do_is_said_when_zenodo_stays_down(tmp_path, unsteady, capsys):
    """Hands-on review of round 3: giving up on the record, or on the draft
    a study was shared as, said what failed but not what to do."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.faults = {"GET record": [503] * zenodo.TRIES}
    with pytest.raises(ShareRefused) as refused:
        open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert str(refused.value).endswith("Nothing was kept; open it again later.")
    capsys.readouterr()
    unsteady.faults = {"GET deposition": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "The draft or record 7 the study was shared as is as it was; share again once " \
        "Zenodo answers." in capsys.readouterr().err


def test_a_record_that_will_never_open_is_not_said_to_open_later(tmp_path, unsteady):
    """Review of the fourth commit: a DOI naming no record, or a record of no
    one study, was told to open it again later."""
    with pytest.raises(ShareRefused) as refused:
        open_shared("10.5072/zenodo.12345", workspace=tmp_path / "w")
    assert "again later" not in str(refused.value)
    assert str(refused.value).startswith("Zenodo has no published record 12345: look at "
                                         "the DOI or address given")
    assert refused.value.refusal.code == "environment.share.not_a_study"


def test_a_draft_zenodo_listed_only_late_is_named_to_delete(tmp_path, unsteady):
    """Review of the fourth commit: a draft Zenodo listed only after it was
    asked for again was left beside the second, never named."""
    from fastmdxplora.sharing.pack import share_study

    study = _study(tmp_path / "study")
    unsteady.late = 99
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.late = 2
    told: list[str] = []
    zenodo.draft(study, tmp_path / "shared.zip", site="zenodo-sandbox",
                 pack=lambda doi: share_study(study, tmp_path / "shared.zip",
                                              authors=[("A B", "")], identifier=doi),
                 authors=[("A B", "")], license_id="CC-BY-4.0", system="1ABC",
                 said=told.append)
    unsteady.drafts[0]["hidden"] = 0
    told.clear()
    zenodo.draft(study, tmp_path / "shared.zip", site="zenodo-sandbox",
                 pack=lambda doi: share_study(study, tmp_path / "shared.zip",
                                              authors=[("A B", "")], identifier=doi),
                 authors=[("A B", "")], license_id="CC-BY-4.0", system="1ABC",
                 said=told.append)
    named = [line for line in told if line.startswith("Zenodo also holds draft 7 ")]
    assert named and "delete it among your uploads" in named[0]


def test_a_file_too_large_for_the_disk_is_said(tmp_path, unsteady, monkeypatch):
    import errno
    import io

    unsteady.payload = os.urandom(1 << 20)
    real = io.open
    target = tmp_path / "got.zip"

    class Fat:
        def __init__(self, handle):
            self.handle = handle

        def write(self, piece):
            raise OSError(errno.EFBIG, "File too large")

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    monkeypatch.setattr(io, "open", lambda file, mode="r", *a, **k: Fat(real(file, mode, *a, **k))
                        if str(file) == str(target) and "w" in mode else real(file, mode, *a, **k))
    with pytest.raises(ShareRefused) as refused:
        _download(f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/records/7/files/shared.zip",
                  target, most=1 << 30, md5="", said=None, size=len(unsteady.payload))
    assert "takes no file this large" in str(refused.value)


def test_a_token_refused_is_not_told_to_wait(tmp_path, unsteady, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    capsys.readouterr()
    unsteady.faults = {"GET deposition": [401]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "the token was refused" in said and "once Zenodo answers" not in said


def test_a_file_over_the_limit_is_refused_before_it_comes(tmp_path, unsteady):
    unsteady.payload = os.urandom(3 << 20)
    got = tmp_path / "got.zip"
    with pytest.raises(ShareRefused) as refused:
        _download(f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/records/7/files/shared.zip",
                  got, most=1 << 20, md5="", said=None, size=0)
    assert refused.value.refusal.code == "environment.share.too_large"
    assert got.stat().st_size == 0


def test_a_new_version_s_draft_deleted_is_begun_again_from_its_record(tmp_path, unsteady):
    """Hands-on review of the fourth commit: the draft of a new version
    deleted, the next share made a record of its own, version 1."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    kept = json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]
    assert {k: kept[k] for k in ("id", "version", "record", "doi", "archive")} == {
        "id": "8", "version": "2", "record": "7", "doi": "10.5072/zenodo.8",
        "archive": "shared.zip"}
    unsteady.gone = {8}
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert any(c[1].endswith("/7/actions/newversion") for c in unsteady.calls)
    assert not any(c[1] == "/api/deposit/depositions" for c in unsteady.calls if c[0] == "POST")
    assert unsteady.stored["metadata"]["version"] == "2"


def test_a_new_version_the_person_began_and_changed_is_not_taken(tmp_path, unsteady, capsys):
    """Hands-on review of the fourth commit: a draft of a new version the
    person began and changed by hand had its title and files replaced."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    # Begun by the person on Zenodo's site, a co-author added: not the
    # study's, whatever in it was changed.
    unsteady.versions_begun = [8]
    unsteady.edited = {8: {"creators": [{"name": "Their, Co-author"}]}}
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "a draft of a new version of record 7 (8), begun on its site or by a share" in said
    assert not any(c[0] in ("PUT", "DELETE") or c[1].endswith("newversion")
                   for c in unsteady.calls)


def test_a_file_the_person_put_beside_the_archive_stays(tmp_path, unsteady):
    """Hands-on review of the fourth commit: a paper the person added to the
    draft was taken away at the next share; only another archive is."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.calls.clear()
    unsteady.files = [{"id": "p", "filename": "paper.pdf"}, {"id": "z", "filename": "old.zip"}]
    _packed_before_as(study, "old.zip")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert [c[1] for c in unsteady.calls if c[0] == "DELETE"] == [
        "/api/deposit/depositions/7/files/z"]


def test_a_refusal_says_what_to_do(tmp_path, unsteady, capsys, monkeypatch):
    """Hands-on review of the fourth commit: a refused token, or a draft
    never asked for, said what failed and not what to do."""
    import urllib.error

    unsteady.faults = {"POST": [401]}
    assert _share_to_zenodo(_study(tmp_path / "a"), tmp_path / "a.zip") == 2
    assert "with the deposit:write scope, and give it in ZENODO_TOKEN" in \
        capsys.readouterr().err
    real = zenodo.urllib.request.urlopen

    def urlopen(request, timeout=None):
        if request.get_method() == "POST":
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        return real(request, timeout=timeout)

    monkeypatch.setattr(zenodo.urllib.request, "urlopen", urlopen)
    assert _share_to_zenodo(_study(tmp_path / "b"), tmp_path / "b.zip") == 2
    assert "Nothing was made; share again once Zenodo answers." in capsys.readouterr().err


def _packed_before_as(study, name: str) -> None:
    """The study's last share named its archive ``name``."""
    path = study / "shared_to.json"
    kept = json.loads(path.read_text())
    kept["zenodo-sandbox"]["archive"] = name
    path.write_text(json.dumps(kept))


def test_an_archive_the_person_put_on_the_draft_is_never_taken_away(tmp_path, unsteady,
                                                                     capsys):
    """Review of the fifth commit: a zip the person put on the draft was
    taken away without a word as if it were an earlier archive."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.calls.clear()
    unsteady.files = [{"id": "m", "filename": "supplementary-movies.zip"}]
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "holds supplementary-movies.zip, which this study did not put there" in \
        capsys.readouterr().err
    assert not any(c[0] in ("DELETE",) or c[1].startswith("/files/") for c in unsteady.calls)


def test_a_new_version_is_compared_with_and_counted_from_the_latest_published(
        tmp_path, unsteady):
    """Review of the fifth commit: a version the person published by hand
    since made every new version look changed, and its number was the
    study's own count, not the record's."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    # Published by hand since as version 2, with a paper: the new draft
    # Zenodo begins from it carries the paper too.
    unsteady.latest = {"id": 9, "metadata": {"version": "2"},
                       "files": [{"key": "shared.zip"}, {"key": "paper.pdf"}]}
    unsteady.files_of = {8: [{"id": "z", "filename": "shared.zip"},
                             {"id": "p", "filename": "paper.pdf"}]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.stored["metadata"]["version"] == "3"


def test_a_refusal_after_a_deleted_new_version_names_its_record(tmp_path, unsteady,
                                                                 capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.gone = {8}
    unsteady.faults = {"POST": [504]}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "Zenodo may have begun the new version of record 7" in said
    assert "record 8" not in said


def test_a_token_refused_at_the_next_share_is_said_as_such(tmp_path, unsteady, capsys):
    study = _study(tmp_path / "study")
    unsteady.late = 99
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {"GET list": [401]}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "the token was refused" in said and "did not list your drafts" not in said


def test_a_file_too_large_unpacking_is_said(tmp_path, unsteady, monkeypatch):
    import errno

    from fastmdxplora.sharing import opening

    _shared(tmp_path, unsteady)

    def big(*_args, **_kwargs):
        raise OSError(errno.EFBIG, "File too large")

    monkeypatch.setattr(opening, "_unpacked", big)
    with pytest.raises(ShareRefused) as refused:
        open_shared(str(tmp_path / "shared.zip"), into=tmp_path / "fat")
    assert refused.value.refusal.code == "environment.share.too_large"
    assert "takes no file this large" in str(refused.value)


def test_a_new_version_begun_whose_answer_was_lost_is_left_for_the_person(tmp_path,
                                                                         unsteady, capsys):
    """Hands-on review of the sixth commit: a draft of a new version a share
    began and lost the answer to was taken later, with the person's edits
    to it written over. It cannot be told from one the person began: it is
    left for them, and once they delete it the next share begins one."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.faults = {"POST newversion": ["made"]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "the next share finds its draft and asks you to delete it there" in \
        capsys.readouterr().err
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "begun on its site or by a share whose answer was lost" in capsys.readouterr().err
    assert not any(c[0] in ("PUT", "DELETE") or c[1].endswith("newversion")
                   for c in unsteady.calls)
    unsteady.versions_begun = []
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0


def test_the_person_s_edits_to_a_draft_not_yet_published_are_kept(tmp_path, unsteady,
                                                                   capsys):
    """Hands-on review of the fifth commit: shared again, the description the
    person had changed on the draft was written over."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.edited = {7: {"description": "<p>As I want it said.</p>"}}
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "it is kept as it is, and only the archive is new" in capsys.readouterr().out
    assert unsteady.stored["metadata"]["description"] == "<p>As I want it said.</p>"
    assert any(c[1].startswith("/files/") for c in unsteady.calls)


def test_a_full_disk_while_sharing_is_said(tmp_path, unsteady, monkeypatch, capsys):
    """Hands-on review of the fifth commit: a disk that filled writing the
    archive, the study's record of where it is shared, or the archive named
    with its DOI ended in a traceback, a draft left unsaid."""
    import errno

    from fastmdxplora.sharing import pack

    study = _study(tmp_path / "study")
    real = pack._packed
    calls = []

    def full(*args, **kwargs):
        calls.append(kwargs.get("identifier"))
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "x.zip"))
        return real(*args, **kwargs)

    monkeypatch.setattr(pack, "_packed", full)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert f"could not be written to {tmp_path}: the disk there has no room" in said
    assert "/uploads/7 is kept, with no archive yet: share again" in said
    # Room again (the packing fails only the once): that draft finished.
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert not any(c[0] == "POST" for c in unsteady.calls)


def test_the_person_s_edits_stay_theirs_at_every_later_share(tmp_path, unsteady):
    """Review of the sixth commit: an edit kept at one share was written
    over at the next."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.edited = {7: {"description": "<p>As I want it said.</p>"}}
    for _ in range(2):
        unsteady.calls.clear()
        assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
        assert unsteady.stored["metadata"]["description"] == "<p>As I want it said.</p>"


def test_a_new_version_s_draft_not_listed_and_changed_is_left_alone(tmp_path, unsteady,
                                                                     capsys):
    """Review of the sixth commit: a draft of a new version the person began
    and changed, left out of Zenodo's list, was given back to the share's
    request and written over."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.edited = {8: {"title": "Theirs"}}
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "(8) that has been changed (its title)" in capsys.readouterr().err
    assert not any(c[0] in ("PUT", "DELETE") for c in unsteady.calls)


def test_a_new_version_refused_outright_is_not_taken_as_asked(tmp_path, unsteady):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.faults = {"POST": [403]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    kept = json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]
    assert "asked_version_of" not in kept


def test_a_temporary_folder_with_no_room_is_named_as_such(tmp_path, monkeypatch):
    import errno
    import tempfile

    from fastmdxplora.sharing import pack

    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device",
                      str(Path(tempfile.gettempdir()) / "fastmdx-share-x1" / "0.txt"))

    monkeypatch.setattr(pack, "_packed", full)
    with pytest.raises(ShareRefused) as refused:
        pack.share_study(tmp_path, tmp_path / "o.zip", authors=[("A B", "")])
    assert str(refused.value).startswith(f"The temporary folder {tempfile.gettempdir()}, ")
    assert "set TMPDIR" in str(refused.value)


def test_a_token_refused_naming_a_stray_draft_fails_no_share(tmp_path, unsteady):
    study = _study(tmp_path / "study")
    unsteady.late = 99
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {"GET list": [None, 401]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["archive"] \
        == "shared.zip"


def test_the_person_s_archive_is_kept_after_a_share_that_failed(tmp_path, unsteady, capsys):
    """Hands-on review of the sixth commit: a share that failed part way
    forgot the archive it had put there, and the next took the person's
    supplement.zip away without a word."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"][
        "archive"] == "shared.zip"
    unsteady.files = [{"id": "s", "filename": "shared.zip"},
                      {"id": "m", "filename": "supplement.zip"}]
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "holds supplement.zip, which this study did not put there" in \
        capsys.readouterr().err
    assert not any(c[0] == "DELETE" for c in unsteady.calls)


def test_a_draft_the_share_had_not_yet_described_keeps_the_person_s_words(tmp_path,
                                                                           unsteady):
    """Hands-on review of the sixth commit: a draft whose description the
    share could not write had the words the person wrote there since
    written over."""
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT metadata": [503] * (zenodo.TRIES + 1)}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.edited = {7: {"title": "My title", "description": "<p>Mine.</p>"}}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.stored["metadata"]["description"] == "<p>Mine.</p>"


def test_a_published_record_s_edits_are_carried_into_its_new_version(tmp_path, unsteady,
                                                                      capsys):
    """Hands-on review of the sixth commit: what the person changed on a
    record before publishing it was dropped from every new version."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.edited = {7: {"description": "<p>Theirs.</p>"},
                       8: {"description": "<p>Theirs.</p>"}}
    unsteady.submitted = True
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "it is kept as it is, and only the archive is new" in capsys.readouterr().out
    assert unsteady.stored["metadata"]["description"] == "<p>Theirs.</p>"
    assert unsteady.stored["metadata"]["version"] == "2"


def test_a_disk_with_room_for_one_archive_makes_it_again(tmp_path, monkeypatch):
    """Hands-on review of the sixth commit: packed again with its DOI, an
    archive needed room for two, the one before still there as it wrote."""
    import zipfile

    from fastmdxplora.sharing import pack

    study = _study(tmp_path / "study")
    out = tmp_path / "shared.zip"
    out.write_bytes(b"an archive from before")
    there = []
    real = zipfile.ZipFile

    def opened(file, mode="r", *args, **kwargs):
        if mode == "w":
            there.append(out.exists())
        return real(file, mode, *args, **kwargs)

    monkeypatch.setattr(pack.zipfile, "ZipFile", opened)
    pack.share_study(study, out, authors=[("A B", "")])
    assert there == [False]


def test_a_draft_the_study_made_is_described_once_a_share_stopped_after_making_it(
        tmp_path, unsteady):
    """Review of the seventh commit: a draft the study made, its share
    stopped before describing it, was left titled as it was asked for,
    with no description, at every later share."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert unsteady.meta[7]["title"].startswith("FastMDXplora: sharing study (")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[7]["title"] == "The 1ABC study"
    assert unsteady.meta[7]["description"]


def test_a_new_draft_after_one_deleted_is_described(tmp_path, unsteady):
    """Review of the seventh commit: a new draft, made after the person
    deleted the one the study was shared as, was held against the deleted
    one's description and never described."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.gone = {7}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["title"] == "The 1ABC study"


def test_a_new_version_shared_again_and_deleted_is_begun_again_from_its_record(tmp_path,
                                                                               unsteady):
    """Review of the seventh commit: a draft of a new version shared again
    forgot its record, so once deleted the next share made a record of its
    own."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.published_ids = {7}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"][
        "record"] == "7"
    unsteady.gone = {8}
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert any(c[1].endswith("/7/actions/newversion") for c in unsteady.calls)
    assert not any(c[0] == "POST" and c[1] == "/api/deposit/depositions"
                   for c in unsteady.calls)


def _analysed_again(study: Path, words: str) -> None:
    """The study's report sums it up in ``words`` now, and so does its
    description."""
    report = study / "report" / "report.md"
    text = report.read_text()
    assert "The system was simulated for 4 ps." in text
    report.write_text(text.replace("The system was simulated for 4 ps.", words))


def test_a_description_written_whose_answer_was_lost_is_still_the_study_s(tmp_path,
                                                                           unsteady):
    """Hands-on review of the seventh commit: a description Zenodo kept,
    its answer a 500, was taken for the person's at every later share."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT metadata": ["applied"] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    _analysed_again(study, "Analysed again, a longer run.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Analysed again, a longer run." in unsteady.meta[7]["description"]
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["written"]


def test_a_new_version_whose_share_failed_is_described_at_the_next(tmp_path, unsteady):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.published_ids = {7}
    _analysed_again(study, "Analysed again, a longer run.")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8].get("version") == "2"
    assert "Analysed again, a longer run." in unsteady.meta[8]["description"]


def test_an_archive_being_replaced_stays_the_study_s_until_it_is_gone(tmp_path, unsteady):
    """Hands-on review of the seventh commit: the old archive Zenodo would
    not take away refused every later share as not the study's."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.files = [{"id": "a", "filename": "a.zip"}]
    unsteady.faults = {"DELETE": [500] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    unsteady.faults = {}
    unsteady.files = [{"id": "a", "filename": "a.zip"}, {"id": "b", "filename": "b.zip"}]
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 0
    assert [c[1] for c in unsteady.calls if c[0] == "DELETE"] == [
        "/api/deposit/depositions/7/files/a"]


def test_a_version_labelled_with_letters_is_counted_by_its_number(tmp_path, unsteady):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.latest = {"id": 9, "metadata": {"version": "v5"},
                       "files": [{"key": "shared.zip"}]}
    unsteady.files_of = {8: [{"id": "z", "filename": "shared.zip"}]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.stored["metadata"]["version"] == "v6"


def test_where_a_study_is_shared_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    import errno

    study = tmp_path / "study"
    study.mkdir()
    (study / "shared_to.json").write_text('{"zenodo": {"id": "7"}}')
    real = Path.write_text

    def full(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", full)
    with pytest.raises(ShareRefused):
        zenodo._keep(study, {"zenodo": {"id": "7", "record": "5"}})
    assert json.loads((study / "shared_to.json").read_text()) == {"zenodo": {"id": "7"}}
    assert not list(study.glob(".*.tmp"))


def test_a_new_version_begun_again_is_described_by_the_study(tmp_path, unsteady):
    """Hands-on review of the seventh commit: a new version begun again
    after the person deleted the share's kept the record's description, its
    own held against the deleted draft's."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.published_ids = {7}
    _analysed_again(study, "Analysed again, a longer run.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.gone = {8}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8].get("version") == "2"
    assert "Analysed again, a longer run." in unsteady.meta[8]["description"]


def test_a_lost_answer_then_published_leaves_no_later_version_stale(tmp_path, unsteady):
    """Review of the eighth commit: a description written whose answer was
    lost, the draft then published, left every later version with the
    record's old description."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT metadata": ["applied"] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.published_ids = {7}
    _analysed_again(study, "Words of version two.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of version two." in unsteady.meta[8]["description"]


def test_the_person_s_edits_after_a_lost_answer_are_kept(tmp_path, unsteady):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT metadata": ["applied"] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7]["creators"] = [{"name": "Aina, Adekunle"}, {"name": "Their, Co-author"}]
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert len(unsteady.meta[7]["creators"]) == 2


def test_a_version_label_is_counted_on_in_its_own_form():
    assert [zenodo._bumped(v) for v in ("1", "1.0", "2.1.5", "v5", "draft")] == [
        "2", "1.1", "2.1.6", "v6", ""]


def test_every_archive_being_replaced_stays_the_study_s(tmp_path, unsteady):
    """Review of the eighth commit: two earlier archives Zenodo would not
    take away, and only the last was remembered as the study's."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.files = [{"id": "a", "filename": "a.zip"}]
    unsteady.faults = {"DELETE": [500] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    unsteady.files = [{"id": "a", "filename": "a.zip"}, {"id": "b", "filename": "b.zip"}]
    unsteady.faults = {"DELETE": [None] + [500] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "c.zip") == 2
    unsteady.faults = {}
    unsteady.files = [{"id": "a", "filename": "a.zip"}, {"id": "c", "filename": "c.zip"}]
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "c.zip") == 0
    assert [c[1] for c in unsteady.calls if c[0] == "DELETE"] == [
        "/api/deposit/depositions/7/files/a"]


def test_an_archive_whose_upload_answer_was_lost_is_the_study_s(tmp_path, unsteady):
    """Review of the eighth commit: an archive Zenodo kept whose answer was
    lost was, at a later share under another name, said not the study's."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.files = [{"id": "a", "filename": "a.zip"}]
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    unsteady.faults = {}
    unsteady.files = [{"id": "a", "filename": "a.zip"}, {"id": "b", "filename": "b.zip"}]
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "c.zip") == 0
    assert sorted(c[1] for c in unsteady.calls if c[0] == "DELETE") == [
        "/api/deposit/depositions/7/files/a", "/api/deposit/depositions/7/files/b"]


def test_a_description_sent_twice_and_lost_is_still_the_study_s(tmp_path, unsteady,
                                                                 capsys):
    """Hands-on review of the eighth commit: a description that landed, its
    answer lost, then another that never landed, left the draft taken for
    the person's for good."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _analysed_again(study, "Words of the second share.")
    unsteady.faults = {"PUT metadata": ["applied"] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    report = study / "report" / "report.md"
    report.write_text(report.read_text().replace("Words of the second share.",
                                                 "Words of the third share."))
    unsteady.faults = {"PUT metadata": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "changed there since this study last described it" not in capsys.readouterr().out
    assert "Words of the third share." in unsteady.meta[7]["description"]


def test_one_429_on_the_listing_before_a_new_version_is_asked_again(tmp_path, unsteady):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.faults = {"GET list": [429]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0


def test_names_the_person_gave_a_draft_not_yet_described_are_kept(tmp_path, unsteady,
                                                                   capsys):
    """Review of the ninth commit: a co-author and keywords the person added
    to a draft whose first upload failed were written over at the next
    share, the draft taken as still the study's to describe."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7]["creators"] = [{"name": "Aina, Adekunle"}, {"name": "Their, Co-author"}]
    unsteady.meta[7]["keywords"] = ["their keyword"]
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert len(unsteady.meta[7]["creators"]) == 2
    assert unsteady.meta[7]["keywords"] == ["their keyword"]
    assert "changed there before this study described it" in capsys.readouterr().out


def test_a_new_version_begun_again_holds_the_record_s_archive_as_the_study_s(tmp_path,
                                                                            unsteady):
    """Review of the ninth commit: a new version shared under another name,
    then deleted, was begun again holding the record's archive, which the
    share refused as not the study's."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.published_ids = {7}
    # As Zenodo begins a new version: with the files of the one before.
    unsteady.files_of = {n: [{"id": "a", "filename": "a.zip"}] for n in (7, 8)}
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 0
    unsteady.gone = {8}
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 0
    assert any(c[1].endswith("/7/actions/newversion") for c in unsteady.calls)
    assert [c[1] for c in unsteady.calls if c[0] == "DELETE"] == [
        "/api/deposit/depositions/8/files/a"]


def test_a_draft_holding_only_the_person_s_files_is_not_said_to_hold_an_archive(
        tmp_path, unsteady, capsys):
    """Review of the ninth commit: a draft holding only the person's paper,
    its upload failing, was said kept "with the archive it had"."""
    study = _study(tmp_path / "study")
    unsteady.files = [{"id": "p", "filename": "paper.pdf"}]
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "with the archive it had" not in said
    assert "with no archive yet, or the new one if Zenodo kept it" in said


def test_one_429_on_the_listing_after_a_lost_draft_is_asked_again(tmp_path, unsteady):
    """Review of the ninth commit: at the share after a draft whose answer
    was lost, one 429 on the listing refused the share."""
    study, out = _studied(tmp_path, "study")
    unsteady.late = len(zenodo._LOOKS_S)
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, out) == 2
    unsteady.calls.clear()
    unsteady.faults = {"GET list": [429]}
    assert _share_to_zenodo(study, out) == 0
    assert not any(c[0] == "POST" for c in unsteady.calls)
    assert len(unsteady.drafts) == 1


def test_a_description_that_landed_is_the_study_s_whatever_zenodo_added(tmp_path,
                                                                        unsteady):
    """Review of the ninth commit: a description written whose answer was
    lost was told by every field of the draft, so one Zenodo added there
    made it the person's for good."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT metadata": ["applied"] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7]["imprint_publisher"] = "Zenodo"
    _analysed_again(study, "Words of the next share.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of the next share." in unsteady.meta[7]["description"]
    assert unsteady.meta[7]["imprint_publisher"] == "Zenodo"


def test_a_full_disk_refuses_before_a_new_version_is_begun(tmp_path, unsteady, monkeypatch,
                                                            capsys):
    """Hands-on review of the ninth commit: a published study whose disk
    was full began a new version it could not remember, then refused it at
    the next share as one the person began."""
    import errno

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    real = Path.write_text

    def full(self, *args, **kwargs):
        if self.name.startswith(f".{zenodo.SHARED_TO}."):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", full)
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "the disk has no room" in said and "found and finished then" not in said
    assert not any(c[1].endswith("/actions/newversion") for c in unsteady.calls)
    monkeypatch.setattr(Path, "write_text", real)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0


def test_a_record_zenodo_did_not_answer_for_is_not_said_begun(tmp_path, unsteady,
                                                             monkeypatch, capsys):
    """Hands-on review of the ninth commit: a record's GET answering 503 was
    said to have perhaps begun a new version, though none was asked for."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True

    def unanswered(*_args, **_kwargs):
        refused = ShareRefused("Zenodo answered HTTP 503, still after 5 tries.",
                               code="environment.service.unreachable")
        refused.unclear = refused.later = True
        raise refused

    monkeypatch.setattr(zenodo, "_latest_published", unanswered)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "may have begun" not in said
    assert "Record 7 is as it was; share again once Zenodo answers." in said


def test_a_token_refused_part_way_is_not_told_to_wait(tmp_path, unsteady, capsys):
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [401]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "once Zenodo answers" not in said and "share again once that is mended" in said


def test_a_small_limit_is_said_in_megabytes(tmp_path, unsteady):
    unsteady.payload = os.urandom(12 << 20)
    with pytest.raises(ShareRefused) as refused:
        _download(f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/records/7/files/shared.zip",
                  tmp_path / "got.zip", most=10 ** 7, md5="", said=None, size=0)
    assert "over 10 MB" in str(refused.value) and "0.0 GB" not in str(refused.value)


def test_names_the_person_gave_a_draft_found_by_its_title_are_kept(tmp_path, unsteady):
    """Review of the tenth commit: a draft whose answer was lost, found by
    its title at the next share, was taken as made with the co-author and
    keywords the person had given it since, and both written over."""
    unsteady.meta = {}
    study, out = _studied(tmp_path, "study")
    unsteady.late = len(zenodo._LOOKS_S)
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, out) == 2
    unsteady.faults = {}
    unsteady.meta[7] = {"title": unsteady.drafts[0]["title"],
                        "creators": [{"name": "Aina, Adekunle"}, {"name": "Their, Co-author"}],
                        "keywords": ["their keyword"]}
    assert _share_to_zenodo(study, out) == 0
    assert len(unsteady.meta[7]["creators"]) == 2
    assert unsteady.meta[7]["keywords"] == ["their keyword"]


def test_a_new_version_begun_whose_draft_was_not_read_is_said_begun(tmp_path, unsteady,
                                                                   capsys):
    """Review of the eleventh commit: a new version Zenodo began, its draft
    then refused to be read, was said not begun ("Record 7 is as it was")."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.faults = {"GET deposition": [None, 403]}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "is as it was" not in said
    assert "Zenodo began the new version of record 7 (8)" in said
    assert "holding the record as it was and asks you to delete it there" in said


def test_a_new_version_begun_again_holds_whichever_archive_the_record_was_published_with(
        tmp_path, unsteady):
    """Review of the eleventh commit: a record published with the archive
    whose upload answer was lost, the old one deleted, refused that archive
    as not the study's once a new version was begun again."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    unsteady.faults = {}
    # The person deleted a.zip, kept b.zip and published.
    unsteady.published_ids = {7}
    unsteady.files_of = {n: [{"id": "b", "filename": "b.zip"}] for n in (7, 8)}
    assert _share_to_zenodo(study, tmp_path / "c.zip") == 0
    unsteady.gone = {8}
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "c.zip") == 0
    assert [c[1] for c in unsteady.calls if c[0] == "DELETE"] == [
        "/api/deposit/depositions/8/files/b"]


def test_an_answer_that_is_no_draft_is_not_taken_for_one_gone(tmp_path, unsteady,
                                                             monkeypatch, capsys):
    """Review of the eleventh commit: a draft's GET answered with an empty
    list was taken as the draft gone, and a second made beside it."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    real = zenodo._call

    def odd(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/deposit/depositions/7"):
            return []
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", odd)
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "is no draft or record; nothing was made" in capsys.readouterr().err
    assert not any(c[0] == "POST" for c in unsteady.calls)


def test_a_new_version_begun_on_a_disk_that_then_fills_is_named(tmp_path, unsteady,
                                                                monkeypatch, capsys):
    """Hands-on review of the eleventh commit: a new version begun, the
    study's disk full just after, was not named, and the next share took it
    for one whose answer was lost."""
    import errno

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    real, writes = Path.write_text, []

    def fills(self, *args, **kwargs):
        if self.name.startswith(f".{zenodo.SHARED_TO}."):
            writes.append(self)
            if len(writes) > 1:
                raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fills)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "Zenodo had begun a new version of record 7, draft 8" in said
    assert "delete that draft there, make room, then share again" in said
    assert "found and finished then" not in said


def test_a_new_version_left_as_begun_is_not_told_to_be_published(tmp_path, unsteady,
                                                                 capsys):
    """Hands-on review of the eleventh commit: a draft of a new version
    holding the record as it was was said to be published or deleted;
    published, it is the record again."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.published_ids = {7}
    unsteady.meta[8] = {k: v for k, v in unsteady.meta[7].items() if k != "version"}
    unsteady.uploaded_to[8] = unsteady.uploaded_to[7]
    unsteady.versions_begun = [8]
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "delete it there (it holds the record as it was)" in said
    assert "publish it" not in said


def test_names_changed_on_a_described_draft_are_not_said_a_description(tmp_path, unsteady,
                                                                       capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["creators"] = [{"name": "Aina, Adekunle"}, {"name": "Their, Co-author"}]
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    said = capsys.readouterr().out
    assert "holds a description changed" not in said
    assert "changed there since this study last described it (its words, names, " \
        "keywords, access or licence)" in said


def test_a_new_version_shared_again_holds_the_record_s_archives_as_the_study_s(tmp_path,
                                                                              unsteady):
    """Review of the thirteenth commit: a new version's draft shared again
    after a failure took the archive the record was published with for one
    the study did not put there."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    unsteady.published_ids = {7}
    unsteady.files_of = {n: [{"id": "b", "filename": "b.zip"}] for n in (7, 8)}
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "c.zip") == 2
    unsteady.faults = {}
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "c.zip") == 0
    assert [c[1] for c in unsteady.calls if c[0] == "DELETE"] == [
        "/api/deposit/depositions/8/files/b"]


def test_a_new_version_with_a_file_replaced_by_the_person_is_theirs(tmp_path, unsteady,
                                                                    capsys):
    """Review of the thirteenth commit: a new version the person began and
    put a corrected a.zip on was said to hold the record as it was, to be
    deleted."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.published_ids = {7}
    unsteady.files_of = {7: [{"id": "a", "filename": "a.zip", "checksum": "md5:1"}],
                         8: [{"id": "a2", "filename": "a.zip", "checksum": "md5:2"}]}
    unsteady.meta[8] = {k: v for k, v in unsteady.meta[7].items() if k != "version"}
    unsteady.versions_begun = [8]
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 2
    said = capsys.readouterr().err
    assert "publish it, or delete it there" in said and "as it was" not in said


def test_the_person_s_choice_of_access_and_licence_is_kept(tmp_path, unsteady):
    """Review of the thirteenth commit: an embargo, licence and kind the
    person chose for a draft not yet described were written over."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7].update(access_right="embargoed", embargo_date="2027-01-01",
                            license="cc0-1.0", upload_type="software")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[7]["access_right"] == "embargoed"
    assert unsteady.meta[7]["license"] == "cc0-1.0"
    assert unsteady.meta[7]["upload_type"] == "software"


def test_a_doi_zenodo_did_not_reserve_says_the_draft_is_kept(tmp_path, unsteady, capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["prereserve_doi"] = {}
    unsteady.faults = {"PUT metadata": [503] * zenodo.TRIES}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "is kept as it was, its DOI not yet reserved: share again once Zenodo answers" \
        in capsys.readouterr().err


def test_what_the_person_writes_while_the_archive_uploads_is_kept(tmp_path, unsteady,
                                                                  monkeypatch):
    """Hands-on review of the thirteenth commit: words the person wrote on
    the draft while its archive was uploading were written over."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    real = zenodo._call

    def meanwhile(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if kwargs.get("upload") is not None:
            unsteady.meta[7].update(description="<p>Typed by me during the upload.</p>",
                                    keywords=["mine"])
        return answer

    monkeypatch.setattr(zenodo, "_call", meanwhile)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[7]["description"] == "<p>Typed by me during the upload.</p>"
    assert unsteady.meta[7]["keywords"] == ["mine"]


def test_an_answer_that_is_no_json_is_said_to_be_asked_again(tmp_path, unsteady,
                                                             monkeypatch, capsys):
    import urllib.request

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    real = urllib.request.urlopen

    class _Page:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return b"<html>Bad gateway</html>"

    def gateway(request, timeout=None):
        if request.get_method() == "GET" and request.full_url.endswith("/depositions/7"):
            return _Page()
        return real(request, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", gateway)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "was not JSON." in said and "share again once Zenodo answers" in said


def test_a_download_refused_is_not_told_to_be_asked_later(tmp_path, unsteady):
    unsteady.payload = os.urandom(1 << 16)
    unsteady.faults = {"GET file": [403]}
    with pytest.raises(ShareRefused) as refused:
        _download(f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/records/7/files/shared.zip",
                  tmp_path / "got.zip", most=1 << 30, md5="", said=None, size=0)
    assert "ask its owner for access" in str(refused.value)
    assert "open it again later" not in str(refused.value)


def test_a_draft_found_by_its_title_with_zenodo_s_defaults_is_the_study_s(tmp_path,
                                                                         unsteady):
    """Review of the fifteenth commit: a draft whose answer was lost, found
    by its title holding the licence Zenodo gives a draft it makes, was
    taken for the person's and never described."""
    unsteady.meta = {}
    study, out = _studied(tmp_path, "study")
    unsteady.late = len(zenodo._LOOKS_S)
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, out) == 2
    unsteady.faults = {}
    unsteady.meta[7] = {"title": unsteady.drafts[0]["title"], "license": "cc-by",
                        "access_right": "open"}
    assert _share_to_zenodo(study, out) == 0
    assert unsteady.meta[7]["title"] == "The 1ABC study"
    assert unsteady.meta[7]["description"]


@pytest.mark.parametrize("chosen", [{"license": "cc0-1.0"}, {"upload_type": "software"},
                                    {"access_right": "closed"}])
def test_a_licence_kind_or_access_chosen_alone_is_kept(tmp_path, unsteady, chosen):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7].update(chosen)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert all(unsteady.meta[7][key] == value for key, value in chosen.items())


def test_a_draft_deleted_during_the_share_is_not_said_kept(tmp_path, unsteady, monkeypatch,
                                                           capsys):
    """Review of the fifteenth commit: a draft the person deleted while its
    archive uploaded was said kept, with the new archive."""
    study = _study(tmp_path / "study")
    real = zenodo._call

    def deleting(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if kwargs.get("upload") is not None:
            unsteady.gone = {7}
        return answer

    monkeypatch.setattr(zenodo, "_call", deleting)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "was deleted on zenodo-sandbox during this share" in said and "is kept" not in said
    monkeypatch.setattr(zenodo, "_call", real)
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert any(c[0] == "POST" and c[1] == "/api/deposit/depositions" for c in unsteady.calls)


def test_a_new_version_begun_is_read_whole_before_it_is_said_changed(tmp_path, unsteady,
                                                                    monkeypatch, capsys):
    """Review of the fifteenth commit: a draft of a new version, listed
    without its files, was said changed, to be published."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.published_ids = {7}
    unsteady.files_of = {n: [{"id": "a", "filename": "a.zip", "checksum": "md5:1"}]
                         for n in (7, 8)}
    unsteady.meta[8] = {k: v for k, v in unsteady.meta[7].items() if k != "version"}
    unsteady.versions_begun = [8]
    real = zenodo._concept_drafts

    def listed(*args, **kwargs):
        return [{k: v for k, v in entry.items() if k != "files"}
                for entry in real(*args, **kwargs) or []]

    monkeypatch.setattr(zenodo, "_concept_drafts", listed)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 2
    assert "delete it there (it holds the record as it was)" in capsys.readouterr().err


def test_a_file_the_person_names_as_the_record_s_archive_is_not_taken_away(tmp_path,
                                                                           unsteady,
                                                                           capsys):
    """Review of the fifteenth commit: a file the person put on the study's
    draft of a new version, named as the record's earlier archive, was
    deleted without a word."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.published_ids = {7}
    # The record holds the study's archive as it was uploaded.
    first = hashlib.md5((tmp_path / "a.zip").read_bytes()).hexdigest()  # noqa: S324
    unsteady.files_of = {n: [{"id": "a", "filename": "a.zip", "checksum": f"md5:{first}"}]
                         for n in (7, 8)}
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 0
    sent = hashlib.md5((tmp_path / "b.zip").read_bytes()).hexdigest()  # noqa: S324
    unsteady.files_of[8] = [{"id": "b", "filename": "b.zip", "checksum": sent},
                            {"id": "m", "filename": "a.zip", "checksum": "md5:9"}]
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    assert "holds a.zip, which this study did not put there" in capsys.readouterr().err
    assert not any(c[0] == "DELETE" for c in unsteady.calls)


def test_a_draft_found_by_its_title_with_another_licence_zenodo_gave_is_the_study_s(
        tmp_path, unsteady):
    """Hands-on review of the fifteenth commit: a draft whose answer was
    lost, given a licence and kind by Zenodo other than the study's, was
    never described."""
    unsteady.meta = {}
    study, out = _studied(tmp_path, "study")
    real = _Unsteady._list

    def listed(self):
        for draft in type(self).drafts:
            type(self).meta.setdefault(draft["id"], {"title": draft["title"],
                                                     "license": "cc-nc"})
        return real(self)

    _Unsteady._list = listed
    try:
        unsteady.faults = {"POST": ["made"]}
        assert _share_to_zenodo(study, out) == 0
        assert unsteady.meta[7]["title"] == "The 1ABC study"
    finally:
        _Unsteady._list = real


def test_a_draft_deleted_during_its_upload_is_said_gone(tmp_path, unsteady, capsys):
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [404]}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "is gone from zenodo-sandbox (deleted there during this share)" in said
    assert "is kept" not in said


def test_an_empty_answer_to_making_a_draft_says_it_may_be_made(tmp_path, unsteady,
                                                               monkeypatch, capsys):
    study = _study(tmp_path / "study")
    real = zenodo._call

    def empty(method, url, **kwargs):
        if method == "POST" and url.endswith("/api/deposit/depositions"):
            real(method, url, **kwargs)
            return None
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", empty)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "sharing again looks for it by its title" in capsys.readouterr().err
    monkeypatch.setattr(zenodo, "_call", real)
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert not any(c[0] == "POST" for c in unsteady.calls)


def _empty_answer_to(monkeypatch, method: str, ending: str, *, after: bool = True):
    """Zenodo, asked ``method`` of a URL ending in ``ending``, does it (where
    ``after``) and answers 200 with nothing; the steady ``_call`` returned."""
    real = zenodo._call

    def empty(m, url, **kwargs):
        if m == method and url.endswith(ending):
            if after:
                real(m, url, **kwargs)
            return None
        return real(m, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", empty)
    return real


def test_an_empty_answer_to_the_description_leaves_it_the_study_s(tmp_path, unsteady,
                                                                  monkeypatch, capsys):
    """Review of the seventeenth commit: one empty answer to the study's
    description left it the person's for good, never written again."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _analysed_again(study, "Words of the second share.")
    real = _empty_answer_to(monkeypatch, "PUT", "/api/deposit/depositions/7")
    # Not known to be written: said so, to share again.
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    monkeypatch.setattr(zenodo, "_call", real)
    report = study / "report" / "report.md"
    report.write_text(report.read_text().replace("Words of the second share.",
                                                 "Words of the third share."))
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "changed there" not in capsys.readouterr().out
    assert "Words of the third share." in unsteady.meta[7]["description"]



def test_an_empty_answer_for_a_new_version_begun_is_a_refusal(tmp_path, unsteady,
                                                             monkeypatch, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    _empty_answer_to(monkeypatch, "GET", "/api/deposit/depositions/8", after=False)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "Zenodo began the new version of record 7 (8)" in capsys.readouterr().err


def test_a_kind_given_a_draft_found_by_its_title_is_the_person_s(tmp_path, unsteady):
    """Hands-on review of the seventeenth commit: a kind the person gave a
    draft whose answer was lost, before the share that found it, was taken
    as Zenodo's and written over."""
    unsteady.meta = {}
    study, out = _studied(tmp_path, "study")
    unsteady.late = len(zenodo._LOOKS_S)
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, out) == 2
    unsteady.faults = {}
    unsteady.meta[7] = {"title": unsteady.drafts[0]["title"], "upload_type": "software"}
    assert _share_to_zenodo(study, out) == 0
    assert unsteady.meta[7]["upload_type"] == "software"


def test_an_empty_answer_for_the_latest_version_is_not_taken_for_none(tmp_path, unsteady,
                                                                     monkeypatch, capsys):
    """Hands-on review of the seventeenth commit: an empty answer for the
    latest version was taken as no newer one, and a new version asked of an
    older version."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.latest = {"id": 9, "metadata": {"version": "10"}, "files": []}
    _empty_answer_to(monkeypatch, "GET", "/api/records/latest", after=False)
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "named no record" in capsys.readouterr().err
    assert not any(c[1].endswith("/actions/newversion") for c in unsteady.calls)


def test_a_licence_alone_on_a_draft_made_and_answered_is_the_person_s(tmp_path, unsteady):
    """Review of the seventeenth commit: a licence the person set alone on a
    draft whose making was answered, to one Zenodo gives others, was written
    over."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7]["license"] = "cc-zero"
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[7]["license"] == "cc-zero"


def test_a_doi_reserved_for_a_draft_gone_or_unanswered_is_said(tmp_path, unsteady,
                                                              monkeypatch, capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["prereserve_doi"] = {}
    unsteady.faults = {"PUT metadata": [404]}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "is gone from zenodo-sandbox" in capsys.readouterr().err
    unsteady.faults = {}
    _empty_answer_to(monkeypatch, "PUT", "/api/deposit/depositions/7", after=False)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "Zenodo reserved no DOI" in capsys.readouterr().err


def test_the_person_s_file_named_as_the_study_s_archive_is_not_taken_away(tmp_path,
                                                                          unsteady,
                                                                          capsys):
    """Review of the seventeenth commit: a new version's upload failing, the
    person put their own a.zip in place of the record's, and the next share
    deleted it without a word."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    sent = hashlib.md5((tmp_path / "a.zip").read_bytes()).hexdigest()  # noqa: S324
    unsteady.published_ids = {7}
    unsteady.files_of = {n: [{"id": "a", "filename": "a.zip", "checksum": f"md5:{sent}"}]
                         for n in (7, 8)}
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    unsteady.faults = {}
    unsteady.files_of[8] = [{"id": "m", "filename": "a.zip", "checksum": "md5:9"}]
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    assert "holds a.zip, which this study did not put there" in capsys.readouterr().err
    assert not any(c[0] == "DELETE" for c in unsteady.calls)


def test_the_listing_a_share_decides_on_may_take_its_time(monkeypatch):
    asked = []
    monkeypatch.setattr(zenodo, "_call", lambda *a, **k: asked.append(k) or [])
    zenodo._marked("http://z/api/deposit/depositions", "t", "x", None, tries=zenodo.TRIES)
    zenodo._concept_drafts("http://z/api/deposit/depositions", "t",
                           {"conceptrecid": "c7", "id": 7}, None)
    assert [k.get("timeout") for k in asked] == [None, None]


def _md5_of(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324


def test_a_new_version_shared_again_holds_its_own_upload_as_the_study_s(tmp_path,
                                                                       unsteady):
    """Review of the eighteenth commit: a new version shared again before it
    was published took the archive the study had just put there, of the
    record's name, for the person's."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    out = tmp_path / "shared.zip"
    assert _share_to_zenodo(study, out) == 0
    unsteady.published_ids = {7}
    unsteady.files_of = {n: [{"id": "s", "filename": "shared.zip",
                              "checksum": _md5_of(out)}] for n in (7, 8)}
    _analysed_again(study, "Words of version two.")
    assert _share_to_zenodo(study, out) == 0
    unsteady.files_of[8] = [{"id": "s2", "filename": "shared.zip", "checksum": _md5_of(out)}]
    report = study / "report" / "report.md"
    report.write_text(report.read_text().replace("Words of version two.", "Words again."))
    assert _share_to_zenodo(study, out) == 0


def test_an_archive_kept_after_an_upload_failed_is_still_the_study_s(tmp_path, unsteady):
    """Review of the eighteenth commit: an upload that failed under the same
    name left the archive the study had put there before taken for the
    person's at every later share."""
    study = _study(tmp_path / "study")
    out = tmp_path / "shared.zip"
    assert _share_to_zenodo(study, out) == 0
    unsteady.files = [{"id": "s", "filename": "shared.zip", "checksum": _md5_of(out)}]
    _analysed_again(study, "Words of the second share.")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, out) == 2
    unsteady.faults = {}
    assert _share_to_zenodo(study, out) == 0


def test_a_draft_given_zenodo_s_defaults_after_its_answer_is_described(tmp_path, unsteady,
                                                                       monkeypatch):
    """Review of the eighteenth commit: a draft Zenodo gave its documented
    licence only after its answer to making it was taken for the person's,
    and never described."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    real = zenodo._call

    def defaults(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "POST" and url.endswith("/api/deposit/depositions"):
            unsteady.meta[7].update(license="cc-by", access_right="open")
        return answer

    monkeypatch.setattr(zenodo, "_call", defaults)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[7]["title"] == "The 1ABC study"
    assert unsteady.meta[7]["description"]


def test_an_empty_answer_for_the_latest_version_says_to_share_again(tmp_path, unsteady,
                                                                    monkeypatch, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.latest = {"id": 9, "metadata": {"version": "10"}, "files": []}
    _empty_answer_to(monkeypatch, "GET", "/api/records/latest", after=False)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "share again once Zenodo answers" in capsys.readouterr().err


def test_a_zip_of_the_person_s_named_as_the_new_archive_is_not_uploaded_over(tmp_path,
                                                                            unsteady,
                                                                            capsys):
    """Hands-on review of the eighteenth commit: a zip of the person's on
    the draft, the study's archive renamed to its name, was uploaded over
    and lost."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "c.zip") == 0
    unsteady.files = [{"id": "c", "filename": "c.zip"},
                      {"id": "m", "filename": "c-final.zip", "checksum": "md5:7777"}]
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "c-final.zip") == 2
    assert "holds c-final.zip, which this study did not put there" in capsys.readouterr().err
    assert not any(c[0] in ("PUT", "DELETE") and "/files/" in c[1] for c in unsteady.calls)


def test_an_empty_answer_for_a_record_is_not_said_to_be_none(tmp_path, unsteady,
                                                              monkeypatch):
    _empty_answer_to(monkeypatch, "GET", "/api/records/7", after=False)
    with pytest.raises(ShareRefused) as refused:
        open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert "has no published record" not in str(refused.value)
    assert "open it again later" in str(refused.value)


def test_a_share_failing_after_its_description_is_written_says_so(tmp_path, unsteady,
                                                                  monkeypatch, capsys):
    """Hands-on review of the eighteenth commit: a disk filling once Zenodo
    had answered the description said it not yet written."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    real_call, real_keep, described = zenodo._call, zenodo._keep, []

    def call(method, url, **kwargs):
        answer = real_call(method, url, **kwargs)
        if method == "PUT" and (kwargs.get("body") or {}).get("metadata", {}) \
                .get("description"):
            described.append(url)
        return answer

    def keep(where, what, **how):
        if described:
            raise ShareRefused("No room.", code="environment.share.too_large")
        return real_keep(where, what, **how)

    monkeypatch.setattr(zenodo, "_call", call)
    monkeypatch.setattr(zenodo, "_keep", keep)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "with the new archive and its description" in said
    assert "not yet written" not in said


def test_a_share_failing_before_the_earlier_archive_goes_warns_of_two(tmp_path, unsteady,
                                                                      monkeypatch, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.files = [{"id": "a", "filename": "a.zip"}]
    real = zenodo._keep
    kept: list = []

    def fills(where, what, **how):
        kept.append(what)
        if what.get("zenodo-sandbox", {}).get("stale") and "uploading" not in \
                what["zenodo-sandbox"]:
            raise ShareRefused("No room.", code="environment.share.too_large")
        return real(where, what, **how)

    monkeypatch.setattr(zenodo, "_keep", fills)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    said = capsys.readouterr().err
    assert "the earlier a.zip beside it, not yet taken away" in said


def test_a_zip_of_the_person_s_on_a_new_version_named_as_an_old_archive_stays(tmp_path,
                                                                             unsteady,
                                                                             capsys):
    """Review of the twentieth commit: a name the study once gave an archive,
    the record no longer holding it, was taken for the study's on a new
    version, and the person's file of that name deleted."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 0
    unsteady.files = [{"id": "b", "filename": "b.zip"}]
    unsteady.faults = {"DELETE": [500] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 2
    unsteady.faults, unsteady.files = {}, None
    # The person deleted b.zip, as advised, and published.
    unsteady.published_ids = {7}
    unsteady.files_of = {n: [{"id": "a", "filename": "a.zip"}] for n in (7, 8)}
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    # On the draft of version 2, the person's own b.zip.
    unsteady.files_of[8] = [{"id": "a", "filename": "a.zip"},
                            {"id": "p", "filename": "b.zip", "checksum": "md5:5"}]
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 2
    assert "holds b.zip, which this study did not put there" in capsys.readouterr().err
    assert not any(c[0] == "DELETE" for c in unsteady.calls)


def test_an_empty_answer_for_a_draft_found_by_its_title_makes_no_second(tmp_path, unsteady,
                                                                       monkeypatch):
    study = _study(tmp_path / "study")
    real = _empty_answer_to(monkeypatch, "POST", "/api/deposit/depositions")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    monkeypatch.setattr(zenodo, "_call", real)
    _empty_answer_to(monkeypatch, "GET", "/api/deposit/depositions/7", after=False)
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[0] == "POST" for c in unsteady.calls)
    assert len(unsteady.drafts) == 1


def test_a_record_shared_by_an_earlier_release_is_described_again(tmp_path, unsteady):
    """Review of the twentieth commit: a draft shared by 2.6.0, which kept
    only its id and version, was never described again."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    # As 2.6.0 kept it.
    (study / "shared_to.json").write_text(json.dumps(
        {"zenodo-sandbox": {"id": "7", "version": "1", "doi": "10.5072/zenodo.7"}}))
    _analysed_again(study, "Words of the next share.")
    # Its summary not the study's now, which may be the person's: kept, and
    # said; cleared there, the study's.
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of the next share." not in unsteady.meta[7]["description"]
    unsteady.meta[7]["description"] = ""
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of the next share." in unsteady.meta[7]["description"]


def test_an_earlier_release_s_archive_is_said_as_perhaps_its(tmp_path, unsteady, capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "old.zip") == 0
    (study / "shared_to.json").write_text(json.dumps(
        {"zenodo-sandbox": {"id": "7", "version": "1", "doi": "10.5072/zenodo.7"}}))
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "new.zip") == 2
    assert "or an earlier release of FastMDXplora put there" in capsys.readouterr().err


def test_an_archive_zenodo_took_away_is_not_said_perhaps_there(tmp_path, unsteady,
                                                              monkeypatch, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.files = [{"id": "a", "filename": "a.zip"}]
    real = zenodo._keep

    def fills(where, what, **how):
        if what.get("zenodo-sandbox", {}).get("stale") == []:
            raise ShareRefused("No room.", code="environment.share.too_large")
        return real(where, what, **how)

    monkeypatch.setattr(zenodo, "_keep", fills)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    said = capsys.readouterr().err
    assert "No room." in said and "may not have taken away" not in said


def test_a_draft_not_read_again_after_its_upload_is_not_described(tmp_path, unsteady,
                                                                  monkeypatch, capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    _empty_answer_to(monkeypatch, "GET", "/api/deposit/depositions/7", after=False)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "reading the draft" in capsys.readouterr().err
    assert not unsteady.meta[7].get("description")


def test_a_draft_gone_since_it_was_listed_is_asked_for_again(tmp_path, unsteady, capsys):
    study, out = _studied(tmp_path, "study")
    unsteady.late = len(zenodo._LOOKS_S)
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, out) == 2
    unsteady.faults, unsteady.gone = {}, {7}
    capsys.readouterr()
    assert _share_to_zenodo(study, out) == 0
    assert "is gone from Zenodo since it was listed" in capsys.readouterr().out


def _during_upload(monkeypatch, then) -> None:
    """``then()`` done on Zenodo's site as the study's archive uploads."""
    real = zenodo._call

    def call(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if kwargs.get("upload") is not None:
            then()
        return answer

    monkeypatch.setattr(zenodo, "_call", call)


def test_a_zip_put_over_the_study_s_during_its_upload_is_said(tmp_path, unsteady,
                                                             monkeypatch, capsys):
    """Hands-on review of the twentieth commit: a zip of the person's put on
    the draft under the archive's name as it uploaded was taken as the
    study's, and the share said to publish it."""
    study = _study(tmp_path / "study")
    _during_upload(monkeypatch, lambda: setattr(unsteady, "files", [
        {"id": "p", "filename": "shared.zip", "checksum": "md5:feed"}]))
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "holds shared.zip with other contents than this study sent" in said
    assert "is kept" not in said


def test_another_zip_put_on_the_draft_during_its_upload_is_said(tmp_path, unsteady,
                                                               monkeypatch, capsys):
    study = _study(tmp_path / "study")
    _during_upload(monkeypatch, lambda: setattr(unsteady, "files", [
        {"id": "s", "filename": "shared.zip"}, {"id": "n", "filename": "next.zip"}]))
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "holds next.zip, put there on Zenodo during this share" in capsys.readouterr().err


def test_every_earlier_archive_left_is_named(tmp_path, unsteady, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.files = [{"id": "a", "filename": "a.zip"}]
    unsteady.faults = {"DELETE": [500] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    unsteady.files = [{"id": "a", "filename": "a.zip"}, {"id": "b", "filename": "b.zip"}]
    unsteady.faults = {"DELETE": [500] * zenodo.TRIES}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "c.zip") == 2
    assert "the earlier a.zip, b.zip, which Zenodo may not" in capsys.readouterr().err


def test_a_lone_zip_not_the_study_s_is_not_said_one_of_two(tmp_path, unsteady, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.files = [{"id": "m", "filename": "a.zip", "checksum": "md5:9"}]
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 2
    said = capsys.readouterr().err
    assert "holds a.zip, which this study did not put there" in said
    assert "two archives" not in said


def _as_2_6_0_kept_it(study: Path) -> None:
    (study / "shared_to.json").write_text(json.dumps(
        {"zenodo-sandbox": {"id": "7", "version": "1", "doi": "10.5072/zenodo.7"}}))


def test_an_earlier_release_s_draft_is_described_after_a_share_that_failed(tmp_path,
                                                                           unsteady):
    """Review of the twenty-second commit: a draft 2.6.0 shared, one share
    failing after its upload, was taken for the person's for good."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _analysed_again(study, "Words of the next share.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of the next share." in unsteady.meta[7]["description"]


def test_words_the_person_added_to_an_earlier_release_s_draft_stay(tmp_path, unsteady):
    """Review of the twenty-second commit: a paragraph the person added to a
    draft 2.6.0 described was written over."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    unsteady.meta[7]["description"] += "<p>Funded by the person's grant.</p>"
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Funded by the person's grant." in unsteady.meta[7]["description"]


def test_an_earlier_release_s_draft_keeps_its_names_and_is_described(tmp_path, unsteady):
    """Hands-on review of the twenty-second commit: a draft 2.6.0 shared with
    other names than this share's was never described again."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    names = [{"name": "Aina, Adekunle"}, {"name": "Other, Author"}]
    unsteady.meta[7]["creators"] = names
    unsteady.meta[7]["description"] = ""
    _analysed_again(study, "Words of the next share.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of the next share." in unsteady.meta[7]["description"]
    assert unsteady.meta[7]["creators"] == names
    _analysed_again_too = study / "report" / "report.md"
    _analysed_again_too.write_text(_analysed_again_too.read_text().replace(
        "Words of the next share.", "Words of the share after."))
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of the share after." in unsteady.meta[7]["description"]
    assert unsteady.meta[7]["creators"] == names


def test_a_zip_put_back_under_a_replaced_archive_s_name_is_said(tmp_path, unsteady,
                                                               monkeypatch, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.files = [{"id": "a", "filename": "a.zip"}]
    _during_upload(monkeypatch, lambda: setattr(unsteady, "files", [
        {"id": "b", "filename": "b.zip"},
        {"id": "p", "filename": "a.zip", "checksum": "md5:9"}]))
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    assert "holds a.zip, put there on Zenodo during this share" in capsys.readouterr().err


def test_several_zips_not_the_study_s_are_said_as_several(tmp_path, unsteady, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.files = [{"id": "s", "filename": "shared.zip"}, {"id": "x", "filename": "x.zip"},
                      {"id": "y", "filename": "y.zip"}]
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "Delete them there" in capsys.readouterr().err


def test_a_new_draft_after_an_earlier_release_s_is_described_whole(tmp_path, unsteady):
    """Review of the twenty-third commit: a new draft, made once the draft an
    earlier release shared was deleted, was taken for that release's and
    given no names, kind or keywords."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    unsteady.gone = {7}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8].get("creators") and unsteady.meta[8].get("upload_type")
    assert not json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"].get(
        "by_earlier")


def test_an_earlier_release_s_undescribed_draft_is_described_whole(tmp_path, unsteady):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7] = {"title": ""}
    _as_2_6_0_kept_it(study)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[7].get("creators") and unsteady.meta[7].get("keywords")


def test_a_zip_of_the_person_s_on_an_earlier_release_s_draft_is_refused(tmp_path,
                                                                         unsteady, capsys):
    """Review of the twenty-third commit: once a draft an earlier release
    shared was shared here, a zip of the person's of the next archive's
    name was uploaded over."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.files = [{"id": "s", "filename": "shared.zip"},
                      {"id": "p", "filename": "b.zip", "checksum": "md5:5"}]
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    said = capsys.readouterr().err
    assert "holds b.zip, which this study did not put there" in said
    assert "earlier release" not in said
    assert not any(c[0] in ("PUT", "DELETE") and "/files/" in c[1] for c in unsteady.calls)


def test_words_the_person_writes_once_an_earlier_release_s_draft_is_shared_stay(
        tmp_path, unsteady):
    """Review of the twenty-third commit: a summary the person rewrote on a
    draft an earlier release shared was written over at every share."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    mine = unsteady.meta[7]["description"].replace("<p>", "<p>My own summary. ", 1)
    unsteady.meta[7]["description"] = mine
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[7]["description"] == mine


def test_a_summary_the_person_rewrote_on_an_earlier_release_s_draft_stays(tmp_path,
                                                                          unsteady, capsys):
    """Hands-on review of the twenty-third commit: a summary sentence the
    person rewrote on a draft 2.6.0 shared, in its form, was written over."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    mine = unsteady.meta[7]["description"].replace("<p>", "<p>Corrected: ", 1)
    unsteady.meta[7].update(description=mine, title="My own title")
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[7]["description"] == mine
    assert unsteady.meta[7]["title"] == "My own title"
    assert "clear the description there and share again" in capsys.readouterr().out


def test_opening_into_new_folders_on_a_full_disk_is_said(tmp_path, monkeypatch):
    import errno

    from fastmdxplora.sharing import opening

    real = Path.mkdir

    def full(self, *args, **kwargs):
        if "deeper" in str(self):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", full)
    with pytest.raises(ShareRefused) as refused:
        opening.open_shared(str(tmp_path / "x.zip"), into=tmp_path / "new" / "deeper")
    assert refused.value.refusal.code == "environment.share.too_large"
    assert not (tmp_path / "new").exists()


def test_an_answer_for_the_study_s_draft_naming_none_is_said_once(tmp_path, unsteady,
                                                                  monkeypatch, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    real = zenodo._call

    def odd(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/deposit/depositions/7"):
            return {"unexpected": True}
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", odd)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "is no draft or record" in said and "making the draft" not in said
    assert said.count("as it was") == 0


def test_a_record_answer_that_is_no_record_says_to_open_again(tmp_path, unsteady,
                                                             monkeypatch):
    real = zenodo._call

    def odd(method, url, **kwargs):
        if url.endswith("/api/records/7"):
            return {"unexpected": True}
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", odd)
    with pytest.raises(ShareRefused) as refused:
        open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert "open it again later" in str(refused.value)


def test_a_new_version_of_an_earlier_release_s_record_stays_marked_as_such(tmp_path,
                                                                          unsteady,
                                                                          capsys):
    """Review of the twenty-fifth commit: a new version of a record 2.6.0
    shared, its first share stopping before the mark was kept, was from
    then on taken for the person's."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.published_ids = {7}
    _as_2_6_0_kept_it(study)
    unsteady.files_of = {n: [{"id": "s", "filename": "shared.zip"}] for n in (7, 8)}
    assert _share_to_zenodo(study, tmp_path / "new.zip") == 2
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"].get(
        "by_earlier")
    # 2.6.0's zip deleted there, as said.
    unsteady.files_of[8] = []
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "new.zip") == 0
    assert "changed there" not in capsys.readouterr().out


def test_an_earlier_release_s_draft_whose_description_was_not_taken_is_still_the_study_s(
        tmp_path, unsteady, capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    # As 2.6.0 wrote it, naming its own release.
    unsteady.meta[7]["description"] = re.sub(r"FastMDXplora \S+ as", "FastMDXplora 2.6.0 as",
                                             unsteady.meta[7]["description"])
    unsteady.faults = {"PUT metadata": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "changed there" not in capsys.readouterr().out


def test_an_earlier_release_s_archive_stays_its_own_after_an_upload_failed(tmp_path,
                                                                           unsteady):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    unsteady.files = [{"id": "s", "filename": "shared.zip", "checksum": "md5:2600"}]
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.files = [{"id": "s", "filename": "shared.zip", "checksum": "md5:2600"}]
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0


def test_no_folder_made_to_open_into_is_left_on_a_full_disk(tmp_path, monkeypatch):
    import errno

    from fastmdxplora.sharing import opening

    real = os.mkdir

    def full(path, *args, **kwargs):
        # As the disk fails: its folders above made, the last one not.
        if str(path).endswith("c") and Path(path).parent.exists():
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", full)
    with pytest.raises(ShareRefused):
        opening.open_shared(str(tmp_path / "x.zip"), into=tmp_path / "a" / "b" / "c")
    assert not (tmp_path / "a").exists()


def test_a_gateway_s_404_page_is_not_taken_for_a_draft_gone(tmp_path, unsteady,
                                                           monkeypatch, capsys):
    """Hands-on review of the twenty-fifth commit: a gateway's HTML 404 for
    the study's draft was taken as the draft gone, and a second made."""
    import io
    import urllib.error
    import urllib.request

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    real = urllib.request.urlopen

    def gateway(request, timeout=None):
        if request.get_method() == "GET" and request.full_url.endswith("/depositions/7"):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {},
                                         io.BytesIO(b"<html>Not Found</html>"))
        return real(request, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", gateway)
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "share again once Zenodo answers" in capsys.readouterr().err
    assert not any(c[0] == "POST" for c in unsteady.calls)


def test_an_archive_uploaded_after_many_failed_uploads_is_still_the_study_s(tmp_path,
                                                                            unsteady):
    study = _study(tmp_path / "study")
    out = tmp_path / "shared.zip"
    assert _share_to_zenodo(study, out) == 0
    unsteady.files = [{"id": "s", "filename": "shared.zip", "checksum": _md5_of(out)}]
    report = study / "report" / "report.md"
    for n in range(11):
        report.write_text(report.read_text() + f"\nNote {n}.\n")
        unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
        assert _share_to_zenodo(study, out) == 2
    unsteady.faults = {}
    assert _share_to_zenodo(study, out) == 0


def test_names_changed_on_an_earlier_release_s_draft_leave_its_words_the_study_s(
        tmp_path, unsteady):
    """Hands-on review of the twenty-fifth commit: names the person set on a
    draft 2.6.0 shared, as the share advised, made its description theirs."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["creators"] = [{"name": "Aina, Adekunle"}, {"name": "New, Author"}]
    _analysed_again(study, "Words of the next share.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of the next share." in unsteady.meta[7]["description"]
    assert len(unsteady.meta[7]["creators"]) == 2


def test_a_licence_removed_on_an_earlier_release_s_draft_stays_removed(tmp_path, unsteady,
                                                                       capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7].update(license="", access_right="closed")
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert not unsteady.meta[7].get("license")
    assert "licence and access are kept" in capsys.readouterr().out


def test_a_new_version_begun_again_of_an_earlier_release_s_record_keeps_its_words(
        tmp_path, unsteady, capsys):
    """Review of the twenty-seventh commit: a new version begun again, the
    person having changed the record's names, lost what told the study's
    description, and was never described again."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.published_ids = {7}
    unsteady.meta[7]["creators"] = [{"name": "Aina, Adekunle"}, {"name": "New, Author"}]
    words = zenodo._written({"description": unsteady.meta[7]["description"]})
    (study / "shared_to.json").write_text(json.dumps({"zenodo-sandbox": {
        "id": "8", "version": "2", "record": "7", "by_earlier": True,
        "record_written": "not what the record holds", "record_words": words,
        "archive": "shared.zip", "record_archive": ["shared.zip"]}}))
    unsteady.gone = {8}
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "changed there" not in capsys.readouterr().out


def test_names_changed_after_a_lost_answer_on_an_earlier_release_s_draft(tmp_path,
                                                                         unsteady):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _analysed_again(study, "Words of the second share.")
    unsteady.faults = {"PUT metadata": ["applied"] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7]["creators"] = [{"name": "Aina, Adekunle"}, {"name": "New, Author"}]
    report = study / "report" / "report.md"
    report.write_text(report.read_text().replace("Words of the second share.",
                                                 "Words of the third share."))
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of the third share." in unsteady.meta[7]["description"]


def test_what_a_lost_first_write_added_and_the_person_removed_stays_removed(tmp_path,
                                                                            unsteady):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    for key in ("license", "keywords"):
        unsteady.meta[7].pop(key, None)
    unsteady.faults = {"PUT metadata": ["applied"] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7].update(license="", keywords=[])
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert not unsteady.meta[7].get("license") and not unsteady.meta[7].get("keywords")


def test_an_empty_404_for_the_study_s_draft_is_not_taken_for_it_gone(tmp_path, unsteady,
                                                                     monkeypatch):
    import io
    import urllib.error
    import urllib.request

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    real = urllib.request.urlopen

    def gateway(request, timeout=None):
        if request.get_method() == "GET" and request.full_url.endswith("/depositions/7"):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {},
                                         io.BytesIO(b""))
        return real(request, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", gateway)
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[0] == "POST" for c in unsteady.calls)


def test_a_gateway_s_404_on_reserving_a_doi_is_not_said_gone(tmp_path, unsteady,
                                                            monkeypatch, capsys):
    import io
    import urllib.error
    import urllib.request

    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["prereserve_doi"] = {}
    real = urllib.request.urlopen

    def gateway(request, timeout=None):
        if request.get_method() == "PUT" and request.full_url.endswith("/depositions/7"):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {},
                                         io.BytesIO(b"<html>Not Found</html>"))
        return real(request, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", gateway)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "is gone from" not in capsys.readouterr().err


def test_the_study_s_archive_stays_its_own_after_many_failed_uploads(tmp_path, unsteady):
    """Hands-on review of the twenty-seventh commit: after fifty failed
    uploads, the archive the draft held was forgotten as the study's."""
    study = _study(tmp_path / "study")
    out = tmp_path / "shared.zip"
    assert _share_to_zenodo(study, out) == 0
    unsteady.files = [{"id": "s", "filename": "shared.zip", "checksum": _md5_of(out)}]
    report = study / "report" / "report.md"
    for n in range(52):
        report.write_text(report.read_text() + f"\nNote {n}.\n")
        unsteady.faults = {"PUT file": [500]}
        assert _share_to_zenodo(study, out) == 2
    unsteady.faults = {}
    assert _share_to_zenodo(study, out) == 0


def test_a_description_that_landed_stays_the_study_s_after_many_failed_writes(tmp_path,
                                                                             unsteady):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _analysed_again(study, "Words of share 0.")
    unsteady.faults = {"PUT metadata": ["applied"] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    report = study / "report" / "report.md"
    for n in range(1, 23):
        report.write_text(report.read_text().replace(f"Words of share {n - 1}.",
                                                     f"Words of share {n}."))
        unsteady.faults = {"PUT metadata": [400]}
        assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of share 22." in unsteady.meta[7]["description"]


def test_a_new_version_an_earlier_release_began_and_deleted_is_begun_again(tmp_path,
                                                                           unsteady):
    """Hands-on review of the twenty-seventh commit: a draft of a new version
    2.6.0 began, shared here then deleted, was followed by a record of its
    own rather than a new version of its record."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.published_ids = {7}
    unsteady.latest = {"id": 7, "metadata": dict(unsteady.meta[7]),
                       "files": [{"key": "shared.zip"}]}
    unsteady.meta[8] = {k: v for k, v in unsteady.meta[7].items() if k != "version"}
    (study / "shared_to.json").write_text(json.dumps(
        {"zenodo-sandbox": {"id": "8", "version": 2, "doi": "10.5072/zenodo.8"}}))
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"][
        "record"] == "7"
    unsteady.gone = {8}
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert any(c[1].endswith("/7/actions/newversion") for c in unsteady.calls)
    assert not any(c[0] == "POST" and c[1] == "/api/deposit/depositions"
                   for c in unsteady.calls)


def test_a_study_shared_twice_at_once_is_refused_the_second_time(tmp_path, unsteady,
                                                                 capsys):
    import fcntl

    study = _study(tmp_path / "study")
    with (study / ".shared_to.json.lock").open("a") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        capsys.readouterr()
        assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "is being shared already, by another command" in capsys.readouterr().err
    assert not any(c[0] == "POST" for c in unsteady.calls)


def test_a_kept_description_on_an_earlier_release_s_draft_says_the_rest_is_kept(
        tmp_path, unsteady, capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    _analysed_again(study, "Words of the next share.")
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "as are its title, creators, keywords, licence and access" in \
        capsys.readouterr().out


def test_a_description_sent_and_lost_on_an_earlier_release_s_draft_reaches_its_new_version(
        tmp_path, unsteady, capsys):
    """Review of the twenty-ninth commit: a description written there whose
    answer was lost, the person then adding a name and publishing, left
    every later version's description the person's."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _analysed_again(study, "Words of the second share.")
    unsteady.faults = {"PUT metadata": ["applied"] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.meta[7]["creators"] = [{"name": "Aina, Adekunle"}, {"name": "New, Author"}]
    unsteady.published_ids = {7}
    report = study / "report" / "report.md"
    report.write_text(report.read_text().replace("Words of the second share.",
                                                 "Words of the third share."))
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "changed there" not in capsys.readouterr().out
    assert "Words of the third share." in unsteady.meta[8]["description"]


def test_a_disk_that_keeps_no_locks_still_shares(tmp_path, unsteady, monkeypatch):
    import errno
    import fcntl

    study = _study(tmp_path / "study")

    def no_locks(*_args, **_kwargs):
        raise OSError(errno.ENOSYS, "Function not implemented")

    monkeypatch.setattr(fcntl, "flock", no_locks)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0


def test_an_earlier_release_s_part_way_draft_gets_what_it_lacks_after_a_failed_write(
        tmp_path, unsteady):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.meta[7] = {"title": ""}
    _as_2_6_0_kept_it(study)
    unsteady.faults = {"PUT metadata": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[7].get("creators") and unsteady.meta[7].get("keywords")


def test_an_upload_answered_but_not_kept_is_not_said_done(tmp_path, unsteady, monkeypatch,
                                                         capsys):
    """Hands-on review of the twenty-ninth commit: an upload answered with
    nothing, the file not kept, was taken as done and the draft said made."""
    study = _study(tmp_path / "study")
    _empty_answer_to(monkeypatch, "PUT", "/files/b7/shared.zip", after=False)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "did not say it was kept" in capsys.readouterr().err


def test_an_archive_zenodo_said_it_took_away_and_did_not_is_said(tmp_path, unsteady,
                                                                 monkeypatch, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    unsteady.files = [{"id": "a", "filename": "a.zip"}]
    _empty_answer_to(monkeypatch, "DELETE", "/files/a", after=False)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    assert "still holds a.zip" in capsys.readouterr().err
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["stale"] == [
        "a.zip"]


def test_a_description_answered_with_nothing_is_not_said_done(tmp_path, unsteady,
                                                             monkeypatch, capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _analysed_again(study, "Words of the second share.")
    _empty_answer_to(monkeypatch, "PUT", "/api/deposit/depositions/7", after=False)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr()
    assert "Draft made" not in said.out and "perhaps not yet written" in said.err


def test_an_empty_answer_to_beginning_a_version_is_not_said_begun(tmp_path, unsteady,
                                                                 monkeypatch, capsys):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    _empty_answer_to(monkeypatch, "POST", "/actions/newversion", after=False)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "Zenodo began the new version" not in said and "may have begun" in said


def test_an_upload_answered_with_nothing_over_the_study_s_archive_takes_nothing_away(
        tmp_path, unsteady, monkeypatch, capsys):
    """Review of the thirty-first commit: an upload answered with nothing
    under the archive's name had the study's own earlier archive said put
    there by someone; under a new name, the earlier one was taken away."""
    study = _study(tmp_path / "study")
    out = tmp_path / "a.zip"
    assert _share_to_zenodo(study, out) == 0
    unsteady.files = [{"id": "a", "filename": "a.zip", "checksum": _md5_of(out)}]
    _analysed_again(study, "Words of the second share.")
    real = _empty_answer_to(monkeypatch, "PUT", "/files/b7/a.zip", after=False)
    capsys.readouterr()
    assert _share_to_zenodo(study, out) == 2
    said = capsys.readouterr().err
    assert "during this share" not in said and "did not say it was kept" in said
    monkeypatch.setattr(zenodo, "_call", real)
    _empty_answer_to(monkeypatch, "PUT", "/files/b7/b.zip", after=False)
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "b.zip") == 2
    said = capsys.readouterr().err
    assert not any(c[0] == "DELETE" for c in unsteady.calls)
    assert "kept, with the new archive" not in said


def test_a_version_answered_with_nothing_is_not_said_given(tmp_path, unsteady, monkeypatch,
                                                         capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["description"] = "<p>Mine.</p>"
    unsteady.published_ids = {7}
    _empty_answer_to(monkeypatch, "PUT", "/api/deposit/depositions/8", after=False)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr()
    assert "Draft of version 2 made" not in said.out
    assert "version is perhaps not yet written" in said.err


def test_a_gateway_s_404_beginning_a_version_says_to_share_again(tmp_path, unsteady,
                                                                 monkeypatch, capsys):
    import io
    import urllib.error
    import urllib.request

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    real = urllib.request.urlopen

    def gateway(request, timeout=None):
        if request.full_url.endswith("/actions/newversion"):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {},
                                         io.BytesIO(b"<html>Not Found</html>"))
        return real(request, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", gateway)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "share again once Zenodo answers" in capsys.readouterr().err


def test_a_gateway_s_404_downloading_is_not_said_closed(tmp_path, unsteady, monkeypatch):
    import io
    import urllib.error
    import urllib.request

    def gateway(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {},
                                     io.BytesIO(b"<html>Not Found</html>"))

    monkeypatch.setattr(urllib.request, "urlopen", gateway)
    with pytest.raises(ShareRefused) as refused:
        _download(f"{os.environ['FASTMDXPLORA_ZENODO_URL']}/records/7/files/shared.zip",
                  tmp_path / "got.zip", most=1 << 30, md5="", said=None, size=0)
    assert "ask its owner" not in str(refused.value)
    assert "open it again later" in str(refused.value)


def test_an_upload_whose_answer_was_lost_stays_the_study_s_after_another_fails(tmp_path,
                                                                               unsteady):
    """Hands-on review of the thirty-first commit: an archive kept whose
    answer was lost, a share under another name then failing, was refused
    as not the study's."""
    study = _study(tmp_path / "study")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "s.zip") == 2
    # Zenodo kept it.
    unsteady.files = [{"id": "s", "filename": "s.zip"}]
    unsteady.faults = {"PUT file": [500] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "v2.zip") == 2
    unsteady.faults = {}
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "v2.zip") == 0
    assert [c[1] for c in unsteady.calls if c[0] == "DELETE"] == [
        "/api/deposit/depositions/7/files/s"]


def test_each_open_takes_a_folder_of_its_own_as_it_names_it(tmp_path):
    from fastmdxplora.sharing.opening import _placed

    first, second = _placed(tmp_path, "the-study"), _placed(tmp_path, "the-study")
    assert first != second and first.is_dir() and second.is_dir()


@pytest.mark.parametrize("odd", [["x"], "x", 3, {"links": "x"}])
def test_an_answer_to_beginning_a_version_not_zenodo_s_is_a_refusal(tmp_path, unsteady,
                                                                    monkeypatch, odd):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    real = zenodo._call

    def strange(method, url, **kwargs):
        if url.endswith("/actions/newversion"):
            return odd
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", strange)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2


def test_a_draft_s_doi_not_zenodo_s_is_not_a_crash(tmp_path, unsteady, monkeypatch):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    real = zenodo._call

    def strange(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "GET" and url.endswith("/depositions/7") and isinstance(answer, dict):
            answer["metadata"]["prereserve_doi"] = "x"
        return answer

    monkeypatch.setattr(zenodo, "_call", strange)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") in (0, 2)


@pytest.mark.parametrize("files", [["s.zip"], {"entries": ["s.zip"]},
                                   [{"key": "s.zip", "size": "big"}]])
def test_a_record_whose_files_are_not_zenodo_s_is_no_crash(tmp_path, unsteady,
                                                           monkeypatch, files):
    real = zenodo._call

    def strange(method, url, **kwargs):
        if url.endswith("/api/records/7"):
            return {"id": 7, "doi": "10.5072/zenodo.7", "files": files}
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", strange)
    if isinstance(files, list) and isinstance(files[0], dict):
        # Its size not a number: taken as not known.
        assert zenodo.archive_of("zenodo-sandbox", "7")["size"] == 0
    else:
        with pytest.raises(ShareRefused):
            zenodo.archive_of("zenodo-sandbox", "7")


def test_a_gateway_s_404_on_the_upload_is_said_as_such(tmp_path, unsteady, monkeypatch,
                                                      capsys):
    import io
    import urllib.error
    import urllib.request

    study = _study(tmp_path / "study")
    real = urllib.request.urlopen

    def gateway(request, timeout=None):
        if "/files/" in request.full_url and request.get_method() == "PUT":
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {},
                                         io.BytesIO(b"<html>Not Found</html>"))
        return real(request, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", gateway)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "a gateway's" in said and "no such record or draft" not in said


def _reads_of_the_draft_hold_nothing(monkeypatch, *, from_read: int = 1) -> None:
    """Zenodo's answers for the draft, from its ``from_read``-th read on,
    hold an empty metadata."""
    real, reads = zenodo._call, []

    def empty(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "GET" and url.endswith("/api/deposit/depositions/7"):
            reads.append(url)
            if len(reads) >= from_read and isinstance(answer, dict):
                return {**answer, "metadata": {}}
        return answer

    monkeypatch.setattr(zenodo, "_call", empty)


@pytest.mark.parametrize("from_read", [1, 2])
def test_a_draft_read_holding_nothing_is_never_written_back(tmp_path, unsteady,
                                                            monkeypatch, from_read):
    """Review of the thirty-third commit: a read of the draft answered with an
    empty metadata was taken as the draft, and the person's words written
    over or wiped."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7].update(description="<p>Mine.</p>", notes="My notes.")
    _reads_of_the_draft_hold_nothing(monkeypatch, from_read=from_read)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert unsteady.meta[7]["description"] == "<p>Mine.</p>"
    assert unsteady.meta[7]["notes"] == "My notes."


def test_a_description_answered_without_it_is_not_taken_as_written(tmp_path, unsteady,
                                                                   monkeypatch, capsys):
    """Review of the thirty-third commit: a description written, answered with
    an empty metadata, was taken as written: the study's own description
    then said the person's for good."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    real = zenodo._call

    def empty(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "PUT" and (kwargs.get("body") or {}).get("metadata", {}).get(
                "description"):
            return {**answer, "metadata": {}}
        return answer

    monkeypatch.setattr(zenodo, "_call", empty)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    monkeypatch.setattr(zenodo, "_call", real)
    _analysed_again(study, "Words of the next share.")
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "changed there" not in capsys.readouterr().out
    assert "Words of the next share." in unsteady.meta[7]["description"]


def test_an_archive_kept_damaged_is_the_study_s_to_replace(tmp_path, unsteady,
                                                           monkeypatch, capsys):
    study = _study(tmp_path / "study")
    real = zenodo._call

    def damaged(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if kwargs.get("upload") is not None:
            return {"key": "shared.zip", "checksum": "md5:dead"}
        return answer

    monkeypatch.setattr(zenodo, "_call", damaged)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "with other contents than were sent" in capsys.readouterr().err
    monkeypatch.setattr(zenodo, "_call", real)
    unsteady.files = [{"id": "s", "filename": "shared.zip", "checksum": "md5:dead"}]
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0


def _answered_for(monkeypatch, ending: str, odd) -> None:
    real = zenodo._call

    def strange(method, url, **kwargs):
        if method == "GET" and url.endswith(ending):
            return odd
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", strange)


@pytest.mark.parametrize("odd", [{"x": 1}, {"id": 5}])
def test_an_answer_for_the_latest_version_not_zenodo_s_begins_nothing(tmp_path, unsteady,
                                                                      monkeypatch, odd):
    """Hands-on review of the thirty-third commit: the latest version's read
    answered with what is not a deposition was taken as one, and a new
    version asked of another."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.latest = {"id": 9, "metadata": {"version": "2"}, "files": []}
    _answered_for(monkeypatch, "/api/deposit/depositions/9", odd)
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[1].endswith("/actions/newversion") for c in unsteady.calls)


def test_a_begun_version_not_read_whole_is_not_said_to_be_published(tmp_path, unsteady,
                                                                    monkeypatch, capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.published_ids = {7}
    unsteady.meta[8] = {k: v for k, v in unsteady.meta[7].items() if k != "version"}
    unsteady.versions_begun = [8]
    _answered_for(monkeypatch, "/api/deposit/depositions/8", {"x": 1})
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "publish it" not in said and "share again once Zenodo answers" in said


def test_a_version_an_earlier_release_began_is_counted_from_the_record(tmp_path, unsteady):
    """Hands-on review of the thirty-third commit: a draft of a new version
    2.6.0 began, its record published as 1.0, was given 2, not 1.1."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["version"] = "1.0"
    unsteady.published_ids = {7}
    unsteady.latest = {"id": 7, "metadata": dict(unsteady.meta[7]),
                       "files": [{"key": "shared.zip"}]}
    unsteady.meta[8] = {k: v for k, v in unsteady.meta[7].items() if k != "version"}
    unsteady.meta[8]["version"] = "2"
    (study / "shared_to.json").write_text(json.dumps(
        {"zenodo-sandbox": {"id": "8", "version": 2, "doi": "10.5072/zenodo.8"}}))
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["version"] == "1.1"


@pytest.mark.parametrize("odd", ["another", "empty"])
def test_the_study_s_record_read_not_as_zenodo_gives_it_begins_nothing(tmp_path, unsteady,
                                                                       monkeypatch, odd):
    """Review of the thirty-fifth commit: the published record's read,
    answered with another deposition or with nothing in it, had a new
    version begun of that, or said changed."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    real = zenodo._call

    def strange(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "GET" and url.endswith("/api/deposit/depositions/7") \
                and isinstance(answer, dict):
            return {**answer, "id": 102} if odd == "another" else {**answer, "metadata": {}}
        return answer

    monkeypatch.setattr(zenodo, "_call", strange)
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[1].endswith("/actions/newversion") for c in unsteady.calls)


def test_an_earlier_release_s_version_whose_record_is_not_read_is_not_given_one(
        tmp_path, unsteady, monkeypatch):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["version"] = "1.0"
    unsteady.published_ids = {7}
    unsteady.latest = {"id": 7, "metadata": dict(unsteady.meta[7]),
                       "files": [{"key": "shared.zip"}]}
    unsteady.meta[8] = {k: v for k, v in unsteady.meta[7].items() if k != "version"}
    unsteady.meta[8]["version"] = "2"
    (study / "shared_to.json").write_text(json.dumps(
        {"zenodo-sandbox": {"id": "8", "version": 2, "doi": "10.5072/zenodo.8"}}))
    real = zenodo._call

    def down(method, url, **kwargs):
        if url.endswith("/api/records/latest"):
            refused = ShareRefused("Zenodo answered HTTP 502.",
                                   code="environment.service.unreachable")
            refused.later = refused.unclear = True
            raise refused
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", down)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert unsteady.meta[8]["version"] == "2"


def test_a_damaged_copy_is_said_as_such(tmp_path, unsteady, monkeypatch, capsys):
    study = _study(tmp_path / "study")
    real = zenodo._call

    def damaged(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if kwargs.get("upload") is not None:
            return {"key": "shared.zip", "checksum": "md5:dead"}
        return answer

    monkeypatch.setattr(zenodo, "_call", damaged)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "holds a damaged copy of shared.zip" in said and "answer was lost" not in said


def test_a_draft_read_back_empty_once_made_is_not_written_back(tmp_path, unsteady,
                                                               monkeypatch):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    real, reads = zenodo._call, []

    sent: list = []

    def empty_once(method, url, **kwargs):
        if method == "PUT" and "body" in kwargs:
            sent.append(kwargs["body"]["metadata"])
        answer = real(method, url, **kwargs)
        if method == "GET" and url.endswith("/api/deposit/depositions/7") and not reads:
            reads.append(url)
            return {**answer, "metadata": {}}
        return answer

    monkeypatch.setattr(zenodo, "_call", empty_once)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    # (Zenodo's PUT replaces the whole metadata: one sent with nothing in it
    # but the DOI asked for would empty the draft.)
    assert all(set(body) - {"prereserve_doi"} for body in sent)


def test_a_zip_put_on_the_draft_as_it_is_described_is_said(tmp_path, unsteady,
                                                          monkeypatch, capsys):
    """Hands-on review of the thirty-fifth commit: a zip the person put on the
    draft between its read after the upload and its description was missed,
    and the draft said made."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    real = zenodo._call

    def late(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "PUT" and (kwargs.get("body") or {}).get("metadata", {}).get(
                "description") and isinstance(answer, dict):
            return {**answer, "files": [*answer.get("files", []),
                                        {"id": "m", "filename": "mine.zip"}]}
        return answer

    monkeypatch.setattr(zenodo, "_call", late)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr()
    assert "holds mine.zip, put there on Zenodo during this share" in said.err
    assert "Draft made" not in said.out


def _answers_changed(monkeypatch, method: str, ending: str, change) -> None:
    """Zenodo, asked ``method`` of a URL ending in ``ending``, does it and
    answers with ``change`` of its answer."""
    real = zenodo._call

    def changed(m, url, **kwargs):
        answer = real(m, url, **kwargs)
        if m == method and url.endswith(ending) and isinstance(answer, dict):
            return change(answer, kwargs)
        return answer

    monkeypatch.setattr(zenodo, "_call", changed)


_THE_PERSON_S = {"id": 42, "submitted": False,
                 "metadata": {"title": "My own paper", "description": "MY OWN WORDS"},
                 "files": [{"id": "p", "filename": "paper.pdf"}],
                 "links": {"bucket": "http://127.0.0.1:9/files/b42"}}


@pytest.mark.parametrize("other", [_THE_PERSON_S, {**_THE_PERSON_S, "files": []},
                                   {**_THE_PERSON_S, "submitted": True}])
def test_another_draft_answered_for_the_one_made_is_left_alone(tmp_path, unsteady,
                                                                monkeypatch, other):
    """Review of the thirty-seventh commit: the answer to making a draft,
    naming one of the person's own, had that one titled, described and
    given the archive."""
    study = _study(tmp_path / "study")
    _answers_changed(monkeypatch, "POST", "/api/deposit/depositions",
                     lambda answer, sent: json.loads(json.dumps(other)))
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["id"] == "7"
    assert not any("/42" in c[1] or "/b42" in c[1] for c in unsteady.calls)


def _no_doi_in(answer, sent):
    """A draft of a new version read as Zenodo begins one: no DOI reserved."""
    return {**answer, "metadata": {k: v for k, v in answer["metadata"].items()
                                   if k != "prereserve_doi"}}


def test_another_s_doi_answered_for_the_one_reserved_is_not_packed(tmp_path, unsteady,
                                                                    monkeypatch, capsys):
    """Review of the thirty-seventh commit: the answer to reserving a new
    version's DOI, naming another deposition, had that one's DOI written
    into the archive and kept, and every later share refused."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    real = zenodo._call

    def other(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if not isinstance(answer, dict) or not url.endswith("/api/deposit/depositions/8"):
            return answer
        if method == "GET":
            return _no_doi_in(answer, kwargs)
        if method == "PUT" and "prereserve_doi" in (kwargs.get("body") or {}).get(
                "metadata", {}):
            return {**answer, "id": 101, "metadata": {
                **answer["metadata"], "prereserve_doi": {"doi": "10.5072/zenodo.101",
                                                         "recid": 101}}}
        return answer

    monkeypatch.setattr(zenodo, "_call", other)
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "share again once Zenodo answers" in capsys.readouterr().err
    assert not any(c[0] == "PUT" and c[1].startswith("/files/") for c in unsteady.calls)
    assert "zenodo.101" not in (study / "shared_to.json").read_text()


@pytest.mark.parametrize("reserved", [{"doi": "10.5072/zenodo.101", "recid": 101},
                                      {"doi": "10.5072/zenodo.101"}])
def test_a_doi_reserved_for_another_record_is_not_packed(tmp_path, unsteady, monkeypatch,
                                                         capsys, reserved):
    """Hands-on review of the thirty-eighth commit: another record's DOI,
    given without its number apart, was packed and kept."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/8", lambda answer, sent: {
        **answer, "metadata": {**answer["metadata"], "prereserve_doi": reserved}})
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "named the DOI of another record (10.5072/zenodo.101)" in capsys.readouterr().err
    assert not any(c[0] == "PUT" and c[1].startswith("/files/") for c in unsteady.calls)


def test_another_deposition_answered_for_a_new_version_names_none(tmp_path, unsteady,
                                                                  monkeypatch, capsys):
    """Review of the thirty-seventh commit: the answer to beginning a new
    version, naming another deposition, had that one's draft named for the
    person to delete."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    _answers_changed(monkeypatch, "POST", "/7/actions/newversion",
                     lambda answer, sent: {**answer, "id": 101})
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "Zenodo may have begun the new version of record 7" in said and "101" not in \
        said.split("named another deposition.")[-1]
    assert not any(c[1].endswith("/101") for c in unsteady.calls)


@pytest.mark.parametrize("change", [{"conceptrecid": "c99"}, {"submitted": True}])
def test_a_new_version_s_draft_of_another_record_is_not_written(tmp_path, unsteady,
                                                                monkeypatch, change):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/8",
                     lambda answer, sent: {**answer, **change})
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[0] == "PUT" for c in unsteady.calls)


def test_a_latest_version_of_another_concept_begins_nothing(tmp_path, unsteady,
                                                            monkeypatch):
    """Review of the thirty-seventh commit: the latest version's concept was
    compared, and no test failed with that undone."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.latest = {"id": 9, "metadata": {"version": "2"}, "files": []}
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/9",
                     lambda answer, sent: {**answer, "conceptrecid": "c99"})
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[1].endswith("/actions/newversion") for c in unsteady.calls)


def test_a_new_version_answered_with_its_draft_as_zenodo_answers_is_taken(tmp_path, unsteady,
                                                                          monkeypatch):
    """Review of the thirty-eighth commit: Zenodo answers the beginning of a
    new version with the draft begun (not the record its documents give),
    and every new version was refused."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    real = zenodo._call

    def as_zenodo(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "POST" and url.endswith("/7/actions/newversion"):
            return real("GET", answer["links"]["latest_draft"], token=kwargs.get("token"))
        return answer

    monkeypatch.setattr(zenodo, "_call", as_zenodo)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.stored["metadata"]["version"] == "2"


def _as_zenodo_keeps(value):
    """What Zenodo keeps of text sent: its characters' composed forms,
    quotes straightened, ligatures written out."""
    import unicodedata

    if isinstance(value, dict):
        return {k: _as_zenodo_keeps(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_zenodo_keeps(v) for v in value]
    if isinstance(value, str):
        value = unicodedata.normalize("NFC", value).replace("\ufb01", "fi")
        return value.translate(str.maketrans("\u02bc\u2018\u2019\u201c\u201d\u2028",
                                             "'''\"\"\n"))
    return value


@pytest.mark.parametrize("name, words", [
    ("Aina\u2019s study", "Its \u201cfolded\u201d \ufb01bril held."),
    ("Cafe\u0301", "Its \u201cfolded\u201d \ufb01bril held."),
    ("\ufb01bril", "Its \u201cfolded\u201d \ufb01bril held."),
    ("Aina\u02bcs study", "Aina\u02bcs \ufb01bril\u2028held.")])
def test_a_study_whose_words_zenodo_fixes_is_shared_once(tmp_path, unsteady, monkeypatch,
                                                         name, words):
    """Review of the thirty-eighth commit: a study's name or summary Zenodo
    keeps in other characters (a curly apostrophe, a decomposed accent, a
    ligature) had a new draft made at every share, none shared."""
    unsteady.meta = {}
    # (Built under a plain name: the trajectory's writer takes no other.)
    study = _study(tmp_path / "study").rename(tmp_path / name)
    _analysed_again(study, words)
    real = zenodo._call

    def kept_fixed(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method in ("GET", "PUT", "POST") and "/api/deposit/" in url:
            for number, held in unsteady.meta.items():
                unsteady.meta[number] = _as_zenodo_keeps(held)
            for draft in unsteady.drafts:
                draft["title"] = _as_zenodo_keeps(draft["title"])
            return _as_zenodo_keeps(answer)
        return answer

    monkeypatch.setattr(zenodo, "_call", kept_fixed)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _analysed_again_from(study, words, "Its \u2018second\u2019 look.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert sum(c[0] == "POST" and c[1] == "/api/deposit/depositions"
               for c in unsteady.calls) == 1
    assert "Its 'second' look." in zenodo._text(unsteady.meta[7]["description"])


def _analysed_again_from(study: Path, was: str, words: str) -> None:
    report = study / "report" / "report.md"
    text = report.read_text()
    assert was in text
    report.write_text(text.replace(was, words))


def test_a_draft_read_back_with_another_record_s_doi_is_not_packed(tmp_path, unsteady,
                                                                    monkeypatch):
    study = _study(tmp_path / "study")
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/7", lambda answer, sent: {
        **answer, "metadata": {**answer["metadata"],
                               "prereserve_doi": {"doi": "10.5072/zenodo.101"}}})
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[0] == "PUT" and c[1].startswith("/files/") for c in unsteady.calls)
    assert "zenodo.101" not in (study / "shared_to.json").read_text()


def test_a_record_read_naming_no_concept_begins_no_version_of_another(tmp_path, unsteady,
                                                                      monkeypatch):
    """Hands-on review of the thirty-eighth commit: the study's record read
    with no concept and its latest version another of the person's records
    had a new version of that one begun, and the archive put in it."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.latest = {"id": 9, "metadata": {"version": "3"}, "files": []}
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/7", lambda answer, sent: {
        k: v for k, v in answer.items() if k != "conceptrecid"})
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[1].endswith("/actions/newversion") or c[0] == "PUT"
                   for c in unsteady.calls)


def _another_answers(monkeypatch, wanted, other) -> None:
    """Zenodo, asked a PUT ``wanted`` picks, does nothing and answers with
    another deposition, ``other`` of what was sent."""
    real = zenodo._call

    def instead(method, url, **kwargs):
        if method == "PUT" and url.endswith("/api/deposit/depositions/7") and wanted(kwargs):
            return other(kwargs["body"]["metadata"])
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", instead)


@pytest.mark.parametrize("files", [[], [{"id": "k", "filename": "kb.zip"}]])
def test_a_description_answered_by_another_draft_is_not_said_written(tmp_path, unsteady,
                                                                     monkeypatch, capsys,
                                                                     files):
    """Hands-on review of the thirty-eighth commit: the description's answer,
    another study's draft holding the same words and version, had the draft
    said made with none of them; or holding a zip, that zip said put on
    this one."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    _another_answers(monkeypatch, lambda sent: sent["body"]["metadata"].get("description"),
                     lambda said: {"id": 101, "metadata": dict(said), "files": files})
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr()
    assert "Draft made" not in said.out and "kb.zip" not in said.err
    assert "said nothing of it" in said.err


def test_a_version_answered_by_another_draft_is_not_said_given(tmp_path, unsteady,
                                                               monkeypatch, capsys):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    # The person's words there, its version taken away.
    unsteady.edited = {7: {"description": "My own words.", "version": ""}}
    _another_answers(monkeypatch, lambda sent: "version" in sent["body"]["metadata"],
                     lambda said: {"id": 101, "metadata": dict(said), "files": []})
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "did not say it was given" in capsys.readouterr().err


#: A record's relations as Zenodo gives the last version's.
_LAST = {"relations": {"version": [{"index": 1, "is_last": True}]}}


@pytest.mark.parametrize("answer, opened", [
    ({"id": 1009, "conceptrecid": "1000"}, False),
    ({"id": 1009, "conceptrecid": "1007", "metadata": _LAST}, True),
    ({"id": 1009, "parent": {"id": "1007"}, "metadata": _LAST}, True),
    ({"id": 1007, "conceptrecid": "1000"}, True)])
def test_a_record_answered_as_another_is_not_opened(monkeypatch, answer, opened):
    """Hands-on review of the thirty-eighth commit: a record's answer naming
    another record (not a version of the concept asked for) was opened as
    the one asked for."""
    monkeypatch.setattr(zenodo, "_call", lambda method, url, **kwargs: {
        **answer, "doi": f"10.5072/zenodo.{answer['id']}",
        "files": [{"key": "s.zip", "size": 1, "checksum": "md5:" + "0" * 32}]})
    if opened:
        assert zenodo.archive_of("zenodo-sandbox", "1007")["id"] == str(answer["id"])
    else:
        with pytest.raises(ShareRefused, match="named another, 1009"):
            zenodo.archive_of("zenodo-sandbox", "1007")


@pytest.mark.parametrize("deleted", [False, True])
def test_a_record_read_not_saying_it_is_published_is_not_written(tmp_path, unsteady,
                                                                  monkeypatch, deleted):
    """Review of the fortieth commit: the study's record read without saying
    whether it is published was taken as a draft, written to, and its
    account in shared_to.json lost."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    if deleted:
        unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
        assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
        unsteady.faults = {}
        unsteady.gone = {8}
    kept = (study / "shared_to.json").read_text()
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/7", lambda answer, sent: {
        k: v for k, v in answer.items() if k != "submitted"})
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[0] == "PUT" for c in unsteady.calls)
    assert (study / "shared_to.json").read_text() == kept


def test_a_draft_s_title_asked_for_is_in_plain_letters(tmp_path, unsteady):
    study = _study(tmp_path / "study").rename(tmp_path / "Ainaʼs Café Ａ")
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    asked = json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["asked_as"]
    assert asked.isascii() and asked.startswith("FastMDXplora: sharing Ainas Cafe A (")


def test_text_is_compared_as_zenodo_keeps_it():
    """Review of the fortieth commit: Zenodo straightens a modifier
    apostrophe, breaks lines at line and paragraph separators, reads
    entities in text holding no markup, and a C1 control as Windows-1252
    reads it; review of the forty-first commit: and keeps apart what it
    keeps apart."""
    assert zenodo._plain(" Ainaʼs &amp; B x y\u0085z ") == "Aina's & B\nx\ny…z"
    assert zenodo._plain("<p>&amp;</p>") == "<p>&amp;</p>"
    assert zenodo._plain("ŉ ﬁ Ａ") == "'n fi A"
    for apart in ("Ca²⁺", "H₂O", "µs", "™", "½", "a­b",
                  "a‍b", "…"):
        assert zenodo._plain(apart) == apart


def test_the_person_s_edit_of_a_superscript_alone_is_kept(tmp_path, unsteady, capsys):
    """Review of the forty-first commit: a description the person changed
    only in its superscripts and subscripts was taken as the study's, and
    written over."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    _analysed_again(study, "Ca2+ binds in H2O.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    theirs = unsteady.meta[7]["description"].replace(
        "Ca2+ binds in H2O.", "Ca²⁺ binds in H₂O.")
    unsteady.edited = {7: {"description": theirs}}
    _analysed_again_from(study, "Ca2+ binds in H2O.", "Ca2+ binds in H2O at 300 K.")
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "it is kept as it is, and only the archive is new" in capsys.readouterr().out
    assert "at 300 K" not in unsteady.stored["metadata"]["description"]


def _in_zenodo_s_forms(value):
    """A description as Zenodo gives it back: each creator with an
    affiliation, none if none was given; a licence by its earlier id."""
    if isinstance(value, dict):
        out = {k: _in_zenodo_s_forms(v) for k, v in value.items()}
        if "name" in out and "affiliation" not in out:
            out["affiliation"] = None
        if isinstance(out.get("license"), str):
            out["license"] = {"cc0-1.0": "cc-zero", "mit": "mit-license", "cc-by": "cc-by-4.0",
                              "apache": "apache2.0"}.get(out["license"], out["license"])
        return out
    if isinstance(value, list):
        return [_in_zenodo_s_forms(v) for v in value]
    return value


@pytest.mark.parametrize("licence", ["CC0-1.0", "MIT", "CC-BY-4.0", "cc-by", "apache"])
def test_a_description_whose_answer_was_lost_follows_the_study_in_zenodo_s_forms(
        tmp_path, unsteady, monkeypatch, capsys, licence):
    """Hands-on review of the fortieth commit: Zenodo gives creators back with
    an affiliation and licences by their earlier ids, and one description
    whose answer was lost was taken for the person's at every later share."""
    from fastmdxplora.cli.main import main

    unsteady.meta = {}
    study = _study(tmp_path / "study")
    real = zenodo._call
    lose = {"description": False}

    def as_zenodo(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        for number, held in unsteady.meta.items():
            unsteady.meta[number] = _in_zenodo_s_forms(held)
        if lose["description"] and method == "PUT" and (kwargs.get("body") or {}).get(
                "metadata", {}).get("description"):
            lose["description"] = False
            return None
        return _in_zenodo_s_forms(answer)

    monkeypatch.setattr(zenodo, "_call", as_zenodo)

    def share() -> int:
        return main(["report", "--output", str(study), "--share", str(tmp_path / "s.zip"),
                     "--share-author", "Adekunle Aina;0000-0002-8215-7452",
                     "--share-to", "zenodo-sandbox", "--share-license", licence])

    assert share() == 0
    _analysed_again(study, "Words of the second share.")
    lose["description"] = True
    assert share() == 2
    _analysed_again_from(study, "Words of the second share.", "Words of the third share.")
    capsys.readouterr()
    assert share() == 0
    assert "changed there" not in capsys.readouterr().out
    assert "Words of the third share." in unsteady.meta[7]["description"]


def test_an_earlier_release_s_draft_as_the_study_gives_it_is_not_said_otherwise(
        tmp_path, unsteady, monkeypatch, capsys):
    """Hands-on review of the fortieth commit: a 2.6.0 draft holding what the
    study gives, in the forms Zenodo gives it back in, was said to hold
    other names, licence or access at every share."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    real = zenodo._call

    def as_zenodo(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        for number, held in unsteady.meta.items():
            unsteady.meta[number] = _in_zenodo_s_forms(held)
        return _in_zenodo_s_forms(answer)

    monkeypatch.setattr(zenodo, "_call", as_zenodo)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    said = capsys.readouterr().out
    assert "Change them there" not in said


def test_a_record_holding_an_edit_not_published_begins_no_new_version(tmp_path, unsteady,
                                                                      monkeypatch, capsys):
    """Hands-on review of the forty-first commit: an edit saved on the
    published record and not published (Zenodo reads it in the record's
    place) had every share begin a new version and name it as the person's,
    changed, to delete."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/7", lambda answer, sent: {
        **answer, "state": "inprogress",
        "metadata": {**answer["metadata"], "keywords": ["mine"]}})
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "holds an edit saved there and not published: publish or discard that edit" in \
        capsys.readouterr().err
    assert not any(c[1].endswith("/actions/newversion") or c[0] == "PUT"
                   for c in unsteady.calls)


def test_a_full_disk_after_the_draft_is_made_says_it_is_found_again(tmp_path, unsteady,
                                                                      monkeypatch, capsys):
    """Hands-on review of the forty-first commit: a disk that filled once the
    draft was made said only to make room, not that the draft is there."""
    import errno

    study = _study(tmp_path / "study")
    real = Path.write_text
    writes = []

    def fills(self, *args, **kwargs):
        if self.name.startswith(f".{zenodo.SHARED_TO}."):
            writes.append(self)
            if len(writes) > 1:
                raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fills)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "a draft on Zenodo made for it is found and finished then" in \
        capsys.readouterr().err
    monkeypatch.setattr(Path, "write_text", real)
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert not any(c[0] == "POST" for c in unsteady.calls)



def test_a_full_disk_before_any_draft_is_asked_for_says_none_is_found(tmp_path, unsteady,
                                                                      monkeypatch, capsys):
    """Review of the forty-third commit: a disk full before anything was
    asked of Zenodo said a draft made for the study is found again."""
    import errno

    study = _study(tmp_path / "study")
    real = Path.write_text

    def full(self, *args, **kwargs):
        if self.name.startswith(f".{zenodo.SHARED_TO}."):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", full)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "the disk has no room" in said and "found and finished then" not in said
    assert not any(c[0] == "POST" for c in unsteady.calls)


@pytest.mark.parametrize("odd", [["x"], [{"id": 9}], [{"metadata": {"title": "t"}}]])
def test_a_listing_not_of_drafts_makes_no_second(tmp_path, unsteady, monkeypatch, capsys,
                                                 odd):
    """Review of the forty-third commit: a listing of the person's drafts
    holding what is no draft was taken as one holding none of the title
    asked for, and a second draft made."""
    study = _study(tmp_path / "study")
    unsteady.late = 99
    unsteady.faults = {"POST": ["made late"]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.late = 0
    for draft in unsteady.drafts:
        draft["hidden"] = 0
    real = zenodo._call

    def listed_oddly(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "GET" and "?status=draft" in url and isinstance(answer, list):
            return [*answer, *odd]
        return answer

    monkeypatch.setattr(zenodo, "_call", listed_oddly)
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[0] == "POST" for c in unsteady.calls)


def test_text_is_compared_as_zenodo_keeps_it_through_its_last_fixes():
    """Review of the forty-third commit: a terminal's escape, entities read
    over again, line breaks in ftfy's order, and nothing around what
    Zenodo kept."""
    assert zenodo._plain("a\x1b[1mb\x1b[0m") == "ab"
    assert zenodo._plain("&amp;amp;") == "&"
    assert zenodo._plain("5 per&cent") == "5 per&cent"
    assert zenodo._plain("a\r b") == "a\n\nb"
    assert zenodo._plain("\x00 b") == zenodo._plain(" b") == "b"
    assert zenodo._plain("tag\U000e0041") == "tag\U000e0041"


def test_a_new_version_begun_again_from_a_record_under_edit_is_not_said_begun(
        tmp_path, unsteady, monkeypatch, capsys):
    """Review of the forty-third commit: a share whose new version's draft
    was deleted, its record holding an edit not published, said it was
    beginning a new version again, then refused."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.gone = {8}
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/7", lambda answer, sent: {
        **answer, "state": "inprogress"})
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr()
    assert "beginning a new version again" not in said.out
    assert "holds an edit saved there and not published" in said.err


def _as_zenodo_cleans(value):
    """A description as Zenodo keeps it: full-width letters made plain (as
    ftfy makes them), then what that makes markup written as entities (as
    bleach writes it)."""
    if isinstance(value, dict):
        return {k: (_as_zenodo_cleans_text(v) if k == "description" and isinstance(v, str)
                    else _as_zenodo_cleans(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_zenodo_cleans(v) for v in value]
    return value


def _as_zenodo_cleans_text(text):
    import re
    import unicodedata

    plain = re.sub("[！-￮]", lambda c: unicodedata.normalize("NFKC", c.group()), text)
    # (Its own <p> and <code> stay markup; the rest is written as entities.)
    marked = re.split(r"(</?(?:p|code)>)", plain)
    return "".join(part if re.fullmatch(r"</?(?:p|code)>", part) else
                   part.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                   .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                   for part in marked)


def test_a_description_zenodo_writes_entities_into_follows_the_study(tmp_path, unsteady,
                                                                     monkeypatch, capsys):
    """Hands-on review of the forty-third commit: a summary holding a
    full-width "<", ">" or "&" (made plain, then written as an entity
    there), its description's answer lost once, was taken for the person's
    at every later share."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    _analysed_again(study, "Kｄ ＜ 5 nM for A＆B.")
    real = zenodo._call
    lose = {"description": False}

    def as_zenodo(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        for number, held in unsteady.meta.items():
            unsteady.meta[number] = _as_zenodo_cleans(held)
        if lose["description"] and method == "PUT" and (kwargs.get("body") or {}).get(
                "metadata", {}).get("description"):
            lose["description"] = False
            return None
        return _as_zenodo_cleans(answer)

    monkeypatch.setattr(zenodo, "_call", as_zenodo)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "&lt;" in unsteady.meta[7]["description"]
    _analysed_again_from(study, "Kｄ ＜ 5 nM for A＆B.", "Kｄ ＞ 9 nM.")
    lose["description"] = True
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    _analysed_again_from(study, "Kｄ ＞ 9 nM.", "Words of the third share.")
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "changed there" not in capsys.readouterr().out
    assert "Words of the third share." in unsteady.meta[7]["description"]


def test_a_draft_answered_with_no_bucket_says_to_share_again(tmp_path, unsteady,
                                                            monkeypatch, capsys):
    study = _study(tmp_path / "study")
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/7", lambda answer, sent: {
        **answer, "links": {k: v for k, v in answer["links"].items() if k != "bucket"}})
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "named no bucket to upload into. The draft is kept as it is: share again" in \
        capsys.readouterr().err


def _as_bleach_strips(value):
    """A description as Zenodo keeps it: full-width letters made plain, then
    what reads as a tag it does not allow taken out (as bleach takes it)."""
    import re
    import unicodedata

    if isinstance(value, dict):
        return {k: (re.sub(r"<(?!/?(?:p|code)>)[A-Za-z][^>]*>?", "", re.sub(
                    "[！-￮]", lambda c: unicodedata.normalize("NFKC", c.group()), v))
                    if k == "description" and isinstance(v, str) else _as_bleach_strips(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_as_bleach_strips(v) for v in value]
    return value


def test_a_summary_zenodo_would_read_as_markup_is_sent_as_text(tmp_path, unsteady,
                                                               monkeypatch):
    """Review of the forty-fifth commit: a summary holding a full-width "<"
    before a letter (made plain there, then taken out as a tag) was cut
    short on Zenodo, and taken for the person's at every later share."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    _analysed_again(study, "Kd＜Ki at 300 K, so binding holds.")
    real = zenodo._call

    def as_zenodo(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        for number, held in unsteady.meta.items():
            unsteady.meta[number] = _as_bleach_strips(held)
        return _as_bleach_strips(answer)

    monkeypatch.setattr(zenodo, "_call", as_zenodo)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Kd&lt;Ki at 300 K, so binding holds." in unsteady.meta[7]["description"]
    _analysed_again_from(study, "Kd＜Ki at 300 K, so binding holds.", "Words of the next.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "Words of the next." in unsteady.meta[7]["description"]


def test_entities_are_read_as_zenodo_reads_them():
    """Review of the forty-fifth commit: an entity read inside a longer one,
    one in capitals, and lines read after one holding markup."""
    assert zenodo._plain("&notit;") == "&notit;"
    assert zenodo._plain("&NTILDE;") == "Ñ"
    assert zenodo._plain("é &amp;lt;&lt;a") == "é <<a"
    assert zenodo._plain("a &amp; b\nc < d &amp; e") == "a & b\nc < d &amp; e"
    assert zenodo._plain("&#xZZ;") == "&#xZZ;"


def test_a_new_version_begun_again_from_a_newer_version_is_said_begun(tmp_path, unsteady,
                                                                     monkeypatch, capsys):
    """Review of the forty-fifth commit: the new version's draft deleted,
    the record it was of holding an edit not published but a newer version
    published since, a new version was begun from that one unsaid."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.faults = {"PUT file": [503] * zenodo.TRIES}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    unsteady.faults = {}
    unsteady.gone = {8}
    unsteady.latest = {"id": 9, "metadata": {"version": "2"}, "files": []}
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/7", lambda answer, sent: {
        **answer, "state": "inprogress"})
    capsys.readouterr()
    _share_to_zenodo(study, tmp_path / "shared.zip")
    said = capsys.readouterr()
    assert "beginning a new version again, from the latest version of record 9" in said.out


def test_an_older_version_under_edit_listed_among_drafts_begins_the_new_one(
        tmp_path, unsteady, monkeypatch, capsys):
    """Hands-on review of the forty-fifth commit: an older published version
    holding an edit saved there, listed with the person's drafts, was named
    as a draft of a new version to publish or delete."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    real = zenodo._call

    def listed_with_an_edit(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "GET" and "?status=draft" in url and isinstance(answer, list):
            return [*answer, {"id": 5, "conceptrecid": "c7", "submitted": True,
                              "state": "inprogress", "metadata": {"title": "An older one"}}]
        return answer

    monkeypatch.setattr(zenodo, "_call", listed_with_an_edit)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "(5)" not in capsys.readouterr().err
    assert any(c[1].endswith("/7/actions/newversion") for c in unsteady.calls)


def test_an_earlier_release_s_description_with_curly_quotes_is_the_study_s(tmp_path,
                                                                            unsteady,
                                                                            capsys):
    """Review of the forty-seventh commit: a draft 2.6.0 described from a
    summary holding a curly quote (kept straight there) was compared with
    the summary as this release sends it, escaped, and said to be perhaps
    the person's."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    _analysed_again(study, "It’s “folded” here.")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    # As 2.6.0 wrote it (its summary as it was), and Zenodo kept it.
    unsteady.meta[7]["description"] = re.sub(
        r"<p>[^<]*</p>", "<p>It's \"folded\" here.</p>",
        re.sub(r"FastMDXplora \S+ as", "FastMDXplora 2.6.0 as",
               unsteady.meta[7]["description"]), count=1)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    said = capsys.readouterr().out
    assert "may be yours" not in said and "changed there" not in said


def test_a_new_version_an_earlier_release_began_is_known_by_its_concept(tmp_path, unsteady,
                                                                       monkeypatch):
    """Review of the forty-seventh commit: Zenodo gives a draft no `latest`
    link (only what is published), so a draft of a new version 2.6.0 began
    was never known as one, and once deleted, followed by a record of its
    own."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.published_ids = {7}
    unsteady.latest = {"id": 7, "metadata": dict(unsteady.meta[7]),
                       "files": [{"key": "shared.zip"}]}
    unsteady.meta[8] = {k: v for k, v in unsteady.meta[7].items() if k != "version"}
    (study / "shared_to.json").write_text(json.dumps(
        {"zenodo-sandbox": {"id": "8", "version": 2, "doi": "10.5072/zenodo.8"}}))
    real = zenodo._call

    def as_zenodo(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/records/c7"):
            # The concept's address: its latest version.
            return real(method, url.replace("/c7", "/latest"), **kwargs)
        answer = real(method, url, **kwargs)
        if method == "GET" and url.endswith("/api/deposit/depositions/8") \
                and isinstance(answer, dict):
            return {**answer, "links": {k: v for k, v in answer["links"].items()
                                        if k != "latest"}}
        return answer

    monkeypatch.setattr(zenodo, "_call", as_zenodo)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"][
        "record"] == "7"


def test_control_characters_go_as_ftfy_takes_them_out():
    """Review of the forty-seventh commit: ftfy takes its control characters
    out as it fixes, so an entity they break is read; Zenodo then takes out
    what no XML may hold (a form feed)."""
    assert zenodo._plain("&am\x01p;") == "&"
    assert zenodo._plain("x\x0cy") == "xy"
    assert zenodo._plain("a\x7fb") == "ab"


def test_a_zip_of_the_archive_s_name_in_a_version_published_by_hand_is_the_person_s(
        tmp_path, unsteady, monkeypatch, capsys):
    """Hands-on review of the forty-seventh commit: a version the person
    published by hand, its zip of the archive's name replaced by their own,
    had that zip written over in the study's next version."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    theirs = {"id": "z", "filename": "shared.zip", "checksum": "md5:" + "d" * 32}
    unsteady.latest = {"id": 9, "metadata": {"version": "2"}, "files": [{"key": "shared.zip"}]}
    unsteady.files_of = {8: [dict(theirs)]}
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/9",
                     lambda answer, sent: {**answer, "files": [dict(theirs)]})
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "which this study did not put there" in capsys.readouterr().err
    assert not any(c[0] == "PUT" and c[1].startswith("/files/") for c in unsteady.calls)


def test_an_earlier_release_s_first_draft_whose_concept_is_not_read_is_not_a_new_version(
        tmp_path, unsteady, monkeypatch, capsys):
    """Review of the forty-ninth commit: a draft 2.6.0 made (no new version),
    whose concept's address could not be read, was said to be of a new
    version."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _as_2_6_0_kept_it(study)
    real = zenodo._call

    def unread(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/records/c7"):
            down = ShareRefused(f"Zenodo answered GET {url} with HTTP 503, still after 5 "
                                "tries.", code="environment.service.unreachable")
            down.later = True
            raise down
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", unread)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    said = capsys.readouterr().err
    assert "The draft 7, shared by an earlier release, is as it was" in said
    assert "of a new version" not in said


def test_a_terminal_s_escape_goes_once_the_rest_is_fixed():
    """Review of the forty-ninth commit: ftfy takes a terminal's escape out
    after its other fixes, so a full-width bracket or a ligature in it is
    taken out with it."""
    assert zenodo._plain("\x1b［31m") == ""
    assert zenodo._plain("\x1b[ﬁ") == "i"


@pytest.mark.parametrize("page", ["page", "plain page"])
def test_a_gateway_s_page_in_place_of_the_archive_is_asked_again(tmp_path, unsteady, page):
    """Hands-on review of the thirty-sixth commit: a gateway's web page in
    place of the archive was read as the archive, and refused as another
    file by its MD5; of the fifty-second: one sent as plain text too."""
    _shared(tmp_path, unsteady)
    unsteady.faults = {"GET file": [page]}
    told: list[str] = []
    opened = open_shared("10.5072/zenodo.7", workspace=tmp_path / "w", said=told.append)
    assert (opened / "shared_from.json").is_file()
    assert any("a page of text, not the archive" in line for line in told)


def test_a_record_s_size_larger_than_its_file_opens_by_its_md5(tmp_path, unsteady,
                                                                monkeypatch):
    """Hands-on review of the forty-third commit: a record whose recorded
    size is larger than its file ended in a 416 after every try."""
    _shared(tmp_path, unsteady)
    real = zenodo._call

    def larger(method, url, **kwargs):
        answer = real(method, url, **kwargs)
        if method == "GET" and url.endswith("/api/records/7") and isinstance(answer, dict):
            return {**answer, "files": [{**f, "size": int(f["size"]) + 100}
                                        for f in answer["files"]]}
        return answer

    monkeypatch.setattr(zenodo, "_call", larger)
    opened = open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert (opened / "shared_from.json").is_file()


def test_what_an_open_killed_part_way_left_is_taken_away_by_the_next(tmp_path, unsteady):
    """Hands-on review of the thirty-fifth commit: an open killed part way
    left its working folder beside the studies for good. Review of the
    fifty-second commit: one running is told by the lock it holds, in any
    process space, never by its process's number."""
    import fcntl
    import socket

    _shared(tmp_path, unsteady)
    into = tmp_path / "w"
    into.mkdir()
    from fastmdxplora.sharing.opening import _host

    host = _host(socket.gethostname())
    left = into / f".fastmdx-opening-{host}-x1"
    (left / "part").mkdir(parents=True)
    (left / "part" / "archive.zip").write_bytes(b"half")
    (left / ".open.lock").write_text("")
    running = into / f".fastmdx-opening-{host}-x2"
    running.mkdir()
    held = (running / ".open.lock").open("w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    unlocked = into / f".fastmdx-opening-{host}-x3"
    unlocked.mkdir()
    elsewhere = into / ".fastmdx-opening-another.host-x4"
    elsewhere.mkdir()
    (elsewhere / ".open.lock").write_text("")
    try:
        open_shared("10.5072/zenodo.7", into=into)
    finally:
        held.close()
    assert not left.exists()
    assert running.is_dir() and unlocked.is_dir() and elsewhere.is_dir()


def test_an_open_holds_its_working_folder_s_lock_while_it_runs(tmp_path, unsteady,
                                                                monkeypatch):
    import fcntl

    from fastmdxplora.sharing import opening

    _shared(tmp_path, unsteady)
    real, tested = opening._download, []

    def download(url, to, **kwargs):
        lock = next(folder / ".open.lock" for folder in to.parents
                    if folder.name.startswith(".fastmdx-opening-"))
        with lock.open("a") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                tested.append("free")
            except BlockingIOError:
                tested.append("held")
        return real(url, to, **kwargs)

    monkeypatch.setattr(opening, "_download", download)
    open_shared("10.5072/zenodo.7", workspace=tmp_path / "w")
    assert tested == ["held"]


def test_where_no_locks_are_kept_nothing_left_is_taken_away(tmp_path, unsteady, monkeypatch):
    """Review of the fifty-second commit: on Windows, where no process's
    life can be told by a signal and no lock is taken this way, the folder
    is left for the person."""
    import socket
    import sys

    _shared(tmp_path, unsteady)
    into = tmp_path / "w"
    into.mkdir()
    from fastmdxplora.sharing.opening import _host

    host = _host(socket.gethostname())
    left = into / f".fastmdx-opening-{host}-x1"
    left.mkdir()
    (left / ".open.lock").write_text("")
    monkeypatch.setitem(sys.modules, "fcntl", None)
    open_shared("10.5072/zenodo.7", into=into)
    assert left.is_dir()

def test_a_small_archive_s_size_is_said_in_kb_both_ways(tmp_path, unsteady, capsys):
    """Review of the fifty-second commit: a small archive's upload and
    download were still said as "0.0 MB"."""
    study = _study(tmp_path / "study")
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert "kB) to zenodo-sandbox" in capsys.readouterr().out
    told: list[str] = []
    open_shared("10.5072/zenodo.7", workspace=tmp_path / "w", said=told.append)
    assert any("kB)..." in line for line in told) and not any("0.0 MB" in line for line in told)


def test_computers_named_alike_but_for_punctuation_are_told_apart():
    """Hands-on review of the fifty-second commit: "lab-1" and "lab_1" named
    their opens' working folders alike, and one took the other's away."""
    from fastmdxplora.sharing.opening import _host

    assert _host("lab-1") != _host("lab_1") != _host("lab--1")
    assert _host("lab-1") == _host("lab-1") and _host("lab-1").startswith("lab_1.")


def test_a_name_given_as_zenodo_writes_one_is_taken_as_it_is():
    """Hands-on review of the fifty-second commit: "Aina, Adekunle" was sent
    as "Adekunle, Aina,"."""
    assert zenodo._creators([("Aina, Adekunle", "")]) == [{"name": "Aina, Adekunle"}]
    assert zenodo._creators([("Adekunle Aina", "")]) == [{"name": "Aina, Adekunle"}]


@pytest.mark.parametrize("given", ["Aina ,Adekunle", "Aina,  Adekunle", "Aina,Adekunle",
                                   " Aina, Adekunle "])
def test_a_name_with_one_comma_is_sent_as_zenodo_keeps_it(given):
    """Review of the fifty-fourth commit: a name holding one comma was sent
    as typed, and Zenodo keeps it split there and trimmed, so a description
    whose answer was lost was taken for the person's."""
    assert zenodo._creators([(given, "")]) == [{"name": "Aina, Adekunle"}]


def test_an_answer_naming_another_record_s_doi_for_the_draft_made_makes_no_second(
        tmp_path, unsteady, monkeypatch):
    """Review of the fifty-fourth commit: the answer to making a draft,
    its DOI reserved for another record than its own id, had that id kept,
    and the next share made a second draft."""
    study = _study(tmp_path / "study")
    _answers_changed(monkeypatch, "POST", "/api/deposit/depositions", lambda answer, sent: {
        **answer, "id": 9, "metadata": {**answer["metadata"], "prereserve_doi": {
            "doi": "10.5072/zenodo.7", "recid": 7}}})
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["id"] == "7"
    assert sum(c[0] == "POST" and c[1] == "/api/deposit/depositions"
               for c in unsteady.calls) == 1


def test_an_open_s_lock_file_is_found_only_once_it_is_held(tmp_path, monkeypatch):
    """Review of the fifty-fourth commit: the lock file was made, then
    locked, so a later open finding it in between took a folder just begun
    for one left."""
    import fcntl

    from fastmdxplora.sharing import opening

    real, seen = fcntl.flock, []

    def flock(handle, how):
        seen.append((tmp_path / ".open.lock").exists())
        return real(handle, how)

    monkeypatch.setattr(fcntl, "flock", flock)
    held = opening._held(tmp_path)
    try:
        assert held is not None and seen == [False]
        assert sorted(p.name for p in tmp_path.iterdir()) == [".open.lock"]
    finally:
        held.close()


@pytest.mark.parametrize("latest", [
    {"id": 9, "metadata": {"version": "2", "relations": {"version": [{"is_last": False}]}},
     "files": []},
    {"id": 5, "metadata": {"version": "0.9"}, "files": []}])
def test_an_answer_for_the_latest_version_naming_an_earlier_one_begins_nothing(
        tmp_path, unsteady, capsys, latest):
    """Hands-on review of the fifty-fourth commit: the latest version's read,
    answered with a version before the last, had a new version counted on
    from it and asked of it."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.latest = latest
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "not the latest version" in capsys.readouterr().err
    assert not any(c[1].endswith("/actions/newversion") for c in unsteady.calls)


def test_a_latest_version_read_as_a_draft_begins_nothing(tmp_path, unsteady, monkeypatch):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.latest = {"id": 9, "metadata": {"version": "2"}, "files": []}
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/9",
                     lambda answer, sent: {**answer, "submitted": False})
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert not any(c[1].endswith("/actions/newversion") for c in unsteady.calls)


def test_a_concept_answered_with_a_version_before_its_last_is_not_opened(monkeypatch):
    """Hands-on review of the fifty-fourth commit: a concept DOI whose answer
    named a version before the last was opened as the latest."""
    monkeypatch.setattr(zenodo, "_call", lambda method, url, **kwargs: {
        "id": 1009, "conceptrecid": "1007", "doi": "10.5072/zenodo.1009",
        "metadata": {"relations": {"version": [{"index": 0, "is_last": False}]}},
        "files": [{"key": "s.zip", "size": 1, "checksum": "md5:" + "0" * 32}]})
    with pytest.raises(ShareRefused, match="not the latest"):
        zenodo.archive_of("zenodo-sandbox", "1007")


@pytest.mark.parametrize("answered", ["gone", "unsaid", "the study's own"])
def test_a_latest_version_not_said_to_be_the_last_counts_nothing_on(tmp_path, unsteady,
                                                                    monkeypatch, capsys,
                                                                    answered):
    """Hands-on review of the fifty-sixth commit: the latest version's read
    answered with Zenodo's 404, or with a record not saying it is the last,
    had the study's own record taken as the latest, and a new version Zenodo
    began from a later one labelled as if from it."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.submitted = True
    unsteady.latest = {"id": 9, "metadata": {"version": "2"}, "files": []}
    real = zenodo._call

    def unconfirmed(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/records/latest"):
            if answered == "gone":
                return kwargs.get("gone")
            return {"id": 9 if answered == "unsaid" else 7, "metadata": {"version": "2"}}
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", unconfirmed)
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "share again once Zenodo answers" in capsys.readouterr().err
    assert not any(c[1].endswith("/actions/newversion") for c in unsteady.calls)


def test_a_concept_answered_without_saying_it_is_the_last_is_not_opened(monkeypatch):
    monkeypatch.setattr(zenodo, "_call", lambda method, url, **kwargs: {
        "id": 1009, "conceptrecid": "1007", "doi": "10.5072/zenodo.1009", "metadata": {},
        "files": [{"key": "s.zip", "size": 1, "checksum": "md5:" + "0" * 32}]})
    with pytest.raises(ShareRefused, match="not the latest"):
        zenodo.archive_of("zenodo-sandbox", "1007")


def test_a_tiny_size_is_said_in_bytes():
    from fastmdxplora.sharing import said_size

    assert said_size(20) == "20 bytes" and said_size(4900) == "4.9 kB"
    assert said_size(12_500_000) == "12.5 MB"


def test_an_open_running_in_this_process_is_kept_whatever_its_lock_says(tmp_path, unsteady,
                                                                        monkeypatch):
    """Review of the fifty-sixth commit: on NFS a lock is the whole
    process's, so of two opens at once in one process (the GUI's), one could
    take the other's working folder for one left."""
    import socket

    from fastmdxplora.sharing import opening

    _shared(tmp_path, unsteady)
    into = tmp_path / "w"
    into.mkdir()
    other = into / f".fastmdx-opening-{opening._host(socket.gethostname())}-x9"
    other.mkdir()
    (other / ".open.lock").write_text("")
    # Its lock taken as free, as NFS would let this process take it.
    with opening._running(other):
        open_shared("10.5072/zenodo.7", into=into)
        assert other.is_dir()
    open_shared("10.5072/zenodo.7", into=into)
    assert not other.exists()


def test_a_study_copied_with_its_shared_to_json_is_refused_and_its_remedy_works(
        tmp_path, unsteady, capsys):
    """Hands-on review of the fifty-eighth commit: a study's folder copied by
    hand, its shared_to.json with it, shared onto the original's draft, and
    the two then wrote over each other's."""
    import shutil

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 2
    said = capsys.readouterr().err
    assert f"is the one of the study at {study.resolve()}, copied with it" in said
    assert f"delete {copy / 'shared_to.json'} and share again" in said
    assert not any(c[0] in ("PUT", "POST", "DELETE") for c in unsteady.calls)
    # As said: shared as a study of its own, and the original goes on.
    (copy / "shared_to.json").unlink()
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 0
    assert json.loads((copy / "shared_to.json").read_text())["zenodo-sandbox"]["id"] != "7"
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["id"] == "7"


def test_a_study_moved_goes_on_with_its_draft(tmp_path, unsteady):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    moved = study.rename(tmp_path / "moved")
    unsteady.calls.clear()
    assert _share_to_zenodo(moved, tmp_path / "a.zip") == 0
    kept = json.loads((moved / "shared_to.json").read_text())
    assert kept["zenodo-sandbox"]["id"] == "7" and kept["folder"] == str(moved.resolve())
    assert not any(c[0] == "POST" and c[1] == "/api/deposit/depositions"
                   for c in unsteady.calls)


def test_a_lone_surrogate_is_compared_as_zenodo_keeps_it():
    """Review of the fifty-eighth commit: ftfy puts U+FFFD for a lone
    surrogate, which was taken out here."""
    assert zenodo._plain("a\ud800b") == "a�b"


def test_a_tiny_download_s_amounts_are_said_in_bytes():
    from fastmdxplora.sharing.opening import _got

    assert _got(5, 17, "at") == " at 5 of 17 bytes"
    assert _got(5, 17) == ", with 5 of 17 bytes downloaded"


def test_a_copy_shared_first_to_the_other_site_is_still_refused(tmp_path, unsteady,
                                                                 monkeypatch, capsys):
    """Review of the fifty-ninth commit: a copy shared once to the other site
    had its folder kept as its own, and its next share took the original's
    draft."""
    import shutil

    from fastmdxplora.cli.main import main

    monkeypatch.setenv("ZENODO_TOKEN", "token")
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    capsys.readouterr()
    assert main(["report", "--output", str(copy), "--share", str(tmp_path / "b.zip"),
                 "--share-author", "Adekunle Aina", "--share-to", "zenodo"]) == 2
    assert "copied with it" in capsys.readouterr().err
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 2


def test_the_same_folder_reached_by_another_path_is_no_copy(tmp_path, unsteady):
    """Review of the fifty-ninth commit: one folder reached by two paths (a
    bind mount) was taken for a copy, and deleting its shared_to.json as
    said made a second draft."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    other_path = tmp_path / "mounted"
    other_path.symlink_to(study)
    kept = json.loads((study / "shared_to.json").read_text())
    kept["folder"] = str(other_path)
    (study / "shared_to.json").write_text(json.dumps(kept))
    unsteady.calls.clear()
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    assert not any(c[0] == "POST" and c[1] == "/api/deposit/depositions"
                   for c in unsteady.calls)


def test_a_recorded_folder_not_to_be_looked_into_is_taken_as_not_there(tmp_path, unsteady,
                                                                       monkeypatch):
    """Review of the fifty-ninth commit: a folder named in shared_to.json that
    cannot be looked into (another's, or a stale mount) ended in a
    traceback."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    closed = tmp_path / "closed" / "study"
    kept = json.loads((study / "shared_to.json").read_text())
    kept["folder"] = str(closed)
    (study / "shared_to.json").write_text(json.dumps(kept))
    real = Path.is_dir

    def is_dir(self):
        if self == closed:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0


def test_one_byte_is_said_as_one():
    from fastmdxplora.sharing import said_size

    assert said_size(1) == "1 byte" and said_size(2) == "2 bytes"


@pytest.mark.parametrize("field, odd", [("links", "x"), ("links", []), ("files", 3)])
def test_a_draft_read_with_its_fields_in_other_forms_is_refused_not_raised(
        tmp_path, unsteady, monkeypatch, capsys, field, odd):
    """Review of the sixty-first commit: a draft read whose links or files
    were not in their forms ended in a traceback."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    _answers_changed(monkeypatch, "GET", "/api/deposit/depositions/7",
                     lambda answer, sent: {**answer, field: odd})
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 2
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("odd", [[], "x"])
def test_a_latest_version_s_metadata_in_another_form_is_not_the_last(odd):
    assert zenodo._not_the_last({"id": 9, "metadata": odd})


def test_the_copy_refusal_says_how_the_copy_may_carry_on_the_record(tmp_path, unsteady,
                                                                    capsys):
    import shutil

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    capsys.readouterr()
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 2
    assert (f"move {study.resolve() / 'shared_to.json'} over {copy / 'shared_to.json'})"
            in capsys.readouterr().err)


def test_a_copy_is_told_by_its_record_s_concept_after_the_original_went_on(tmp_path,
                                                                          unsteady,
                                                                          capsys):
    """Hands-on review of the sixty-first commit: a copy made before the
    original went on two versions named no id the original still named, and
    took a new version of the original's record."""
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    kept = json.loads((study / "shared_to.json").read_text())
    assert kept["zenodo-sandbox"]["concept"] == "c7"
    copy = tmp_path / "copy"
    import shutil

    shutil.copytree(study, copy)
    # The original went on since: its draft and record are others now.
    kept["zenodo-sandbox"].update(id="104", record="103")
    (study / "shared_to.json").write_text(json.dumps(kept))
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 2
    assert "copied with it" in capsys.readouterr().err
    assert not any(c[0] in ("PUT", "POST", "DELETE") for c in unsteady.calls)


def test_a_new_version_s_label_published_by_hand_meanwhile_is_counted_past(
        tmp_path, unsteady, monkeypatch, capsys):
    """Hands-on review of the sixty-first commit: a version the person
    published by hand as the share began its draft gave the draft the same
    label, kept at every later share."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["version"] = "1.0"
    unsteady.published_ids = {7}
    unsteady.latest = {"id": 7, "metadata": {"version": "1.0"}, "files": [{"key": "shared.zip"}]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["version"] == "1.1"
    # The person published 1.1 by hand meanwhile.
    unsteady.latest = {"id": 9, "metadata": {"version": "1.1"}, "files": []}
    real = zenodo._call

    def by_concept(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/records/c7"):
            return real(method, url.replace("/c7", "/latest"), **kwargs)
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", by_concept)
    capsys.readouterr()
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["version"] == "1.2"
    assert json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"][
        "version"] == "1.2"


def test_a_copy_made_while_only_the_draft_asked_for_is_named_is_refused(
        tmp_path, unsteady, capsys):
    """Code review of the sixty-third commit: a copy made while shared_to.json
    named only the title a draft was asked for under (its answer lost) named
    nothing the original named, and took the original's draft."""
    import shutil

    unsteady.faults = {"POST": ["made late"]}
    study = _study(tmp_path / "study")
    zenodo._LOOKS_S, looks = (1.0,), zenodo._LOOKS_S
    try:
        assert _share_to_zenodo(study, tmp_path / "a.zip") == 2
    finally:
        zenodo._LOOKS_S = looks
    kept = json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]
    assert "asked_as" in kept and "id" not in kept
    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    unsteady.drafts[0]["hidden"] = 0
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 2
    said = capsys.readouterr().err
    assert f'both name the draft asked for as "{kept["asked_as"]}"' in said
    assert not any(c[0] in ("PUT", "POST", "DELETE") for c in unsteady.calls)
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0


def test_the_copy_refusal_names_the_draft_not_its_concept(tmp_path, unsteady, capsys):
    """Code review of the sixty-third commit: the refusal named the record's
    concept as "the draft or record", a number no draft of the person's has."""
    import shutil

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    capsys.readouterr()
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 2
    said = capsys.readouterr().err
    assert "both name the draft or record 7 on zenodo-sandbox" in said and "c7" not in said


def test_a_tiny_or_nearly_whole_download_s_amount_is_said_as_it_is():
    """Code review of the sixty-third commit: "1 bytes", and 999 of 1000
    bytes said as "1.0 of 1.0 kB", as if finished."""
    from fastmdxplora.sharing.opening import _got

    assert _got(1, 0) == ", with 1 byte downloaded"
    assert _got(0, 1, "at") == " at 0 of 1 byte"
    assert _got(999, 1000, "at") == " at 999 of 1000 bytes"
    assert _got(500, 1000, "at") == " at 500 bytes of 1.0 kB"
    assert _got(0, 0, "at") == " at 0 bytes"


@pytest.mark.parametrize("by_hand, expected", [("2.0", "2.1"), ("1.2", "1.3"),
                                               ("v2 (final)", "v3 (final)"),
                                               ("final", "1.1")])
def test_a_waiting_new_version_is_counted_on_from_any_version_published_since(
        tmp_path, unsteady, monkeypatch, by_hand, expected):
    """Hands-on review of the sixty-third commit: a waiting draft of a new
    version was counted past a version published by hand only where that
    version held the draft's own label; 2.0, or 1.1 then 1.2, left it 1.1."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["version"] = "1.0"
    unsteady.published_ids = {7}
    unsteady.latest = {"id": 7, "metadata": {"version": "1.0"}, "files": [{"key": "shared.zip"}]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["version"] == "1.1"
    # The person published a version by hand meanwhile.
    unsteady.latest = {"id": 9, "metadata": {"version": by_hand}, "files": []}
    real = zenodo._call

    def by_concept(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/records/c7"):
            return real(method, url.replace("/c7", "/latest"), **kwargs)
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", by_concept)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["version"] == expected
    # Counted once: shared again, as it is.
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["version"] == expected


def test_a_waiting_new_version_keeps_its_label_while_none_was_published_since(
        tmp_path, unsteady, monkeypatch):
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["version"] = "1.0"
    unsteady.published_ids = {7}
    unsteady.latest = {"id": 7, "metadata": {"version": "1.0"}, "files": [{"key": "shared.zip"}]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    real = zenodo._call

    def by_concept(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/records/c7"):
            return real(method, url.replace("/c7", "/latest"), **kwargs)
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", by_concept)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["version"] == "1.1"


def test_a_latest_answer_older_than_the_record_leaves_a_waiting_label_as_it_is(
        tmp_path, unsteady, monkeypatch):
    """Code review of the sixty-fifth commit: a concept's answer naming a
    version older than the study's record (kept from before) counted the
    waiting draft on from it, down to a label already published."""
    unsteady.meta = {}
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    unsteady.meta[7]["version"] = "1.0"
    unsteady.published_ids = {7}
    unsteady.latest = {"id": 7, "metadata": {"version": "1.0"}, "files": [{"key": "shared.zip"}]}
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["version"] == "1.1"
    # Counted from record 9 (1.1), as 1.2.
    kept = json.loads((study / "shared_to.json").read_text())
    kept["zenodo-sandbox"].update(record="9", version="1.2")
    (study / "shared_to.json").write_text(json.dumps(kept))
    unsteady.meta[8]["version"] = "1.2"
    # Zenodo's answer for the concept is one older than record 9.
    real = zenodo._call

    def by_concept(method, url, **kwargs):
        if method == "GET" and url.endswith("/api/records/c7"):
            return real(method, url.replace("/c7", "/latest"), **kwargs)
        return real(method, url, **kwargs)

    monkeypatch.setattr(zenodo, "_call", by_concept)
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    assert unsteady.meta[8]["version"] == "1.2"


def test_a_copy_made_while_only_the_draft_asked_for_is_named_is_refused_after_a_version(
        tmp_path, unsteady, capsys):
    """Hands-on review of the sixty-fifth commit: the title the first draft
    was asked for under was dropped at a new version, so a copy made while
    nothing else was named made a second record of the study."""
    import shutil

    unsteady.meta = {}
    unsteady.faults = {"POST": ["made late"]}
    study = _study(tmp_path / "study")
    zenodo._LOOKS_S, looks = (1.0,), zenodo._LOOKS_S
    try:
        assert _share_to_zenodo(study, tmp_path / "a.zip") == 2
    finally:
        zenodo._LOOKS_S = looks
    asked = json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]["asked_as"]
    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    unsteady.drafts[0]["hidden"] = 0
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    # Published by hand, then shared again: a draft of a new version.
    unsteady.meta[7]["version"] = "1"
    unsteady.published_ids = {7}
    unsteady.latest = {"id": 7, "metadata": {"version": "1"}, "files": [{"key": "a.zip"}]}
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    kept = json.loads((study / "shared_to.json").read_text())["zenodo-sandbox"]
    assert kept["id"] == "8" and "asked_as" not in kept and kept["asked_first"] == asked
    unsteady.calls.clear()
    capsys.readouterr()
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 2
    assert f'the draft asked for as "{asked}"' in capsys.readouterr().err
    assert not any(c[0] in ("PUT", "POST", "DELETE") for c in unsteady.calls)


def test_a_download_just_begun_is_said_in_bytes():
    """Hands-on review of the sixty-fifth commit: one byte of 4.9 kB was
    said as "0.0 of 4.9 kB"."""
    from fastmdxplora.sharing.opening import _got

    assert _got(1, 4900, "at") == " at 1 byte of 4.9 kB"
    assert _got(600, 4900, "at") == " at 600 bytes of 4.9 kB"
    assert _got(1200, 4900, "at") == " at 1.2 of 4.9 kB"
    assert _got(50_000, 5_000_000_000, "at") == " at 50.0 kB of 5000.0 MB"
    assert _got(50_000, 5_000_000_000) == ", with 50.0 kB of 5000.0 MB downloaded"


def test_the_copy_refusal_s_remedy_to_carry_on_the_record_followed_makes_no_second(
        tmp_path, unsteady, capsys):
    """Code review of the sixty-seventh commit: the remedy said, deleting the
    original's shared_to.json, left a copy that named only the title its
    draft was asked for under to make a second draft."""
    import shutil

    unsteady.faults = {"POST": ["made late"]}
    study = _study(tmp_path / "study")
    zenodo._LOOKS_S, looks = (1.0,), zenodo._LOOKS_S
    try:
        assert _share_to_zenodo(study, tmp_path / "a.zip") == 2
    finally:
        zenodo._LOOKS_S = looks
    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    unsteady.drafts[0]["hidden"] = 0
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    capsys.readouterr()
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 2
    assert "move " in capsys.readouterr().err
    # As said: the original's shared_to.json moved over the copy's.
    (study / "shared_to.json").replace(copy / "shared_to.json")
    unsteady.calls.clear()
    assert _share_to_zenodo(copy, tmp_path / "a.zip") == 0
    assert json.loads((copy / "shared_to.json").read_text())["zenodo-sandbox"]["id"] == "7"
    assert not any(c[0] == "POST" and c[1] == "/api/deposit/depositions"
                   for c in unsteady.calls)


def test_a_copy_refusal_names_the_record_before_a_title_no_longer_on_zenodo(
        tmp_path, unsteady, capsys):
    """Hands-on review of the sixty-seventh commit: after a first share, the
    copy refusal named the title the draft was first asked for under, long
    since replaced on Zenodo by the study's own."""
    import shutil

    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "a.zip") == 0
    kept = json.loads((study / "shared_to.json").read_text())
    kept["zenodo-sandbox"].update(id="104", record="103")
    (study / "shared_to.json").write_text(json.dumps(kept))
    copy = tmp_path / "copy"
    shutil.copytree(study, copy)
    copied = json.loads((copy / "shared_to.json").read_text())
    copied["zenodo-sandbox"].update(id="7", record="")
    (copy / "shared_to.json").write_text(json.dumps(copied))
    capsys.readouterr()
    assert _share_to_zenodo(copy, tmp_path / "b.zip") == 2
    said = capsys.readouterr().err
    assert "both name versions of the record c7" in said and "asked for as" not in said


def test_a_download_that_got_nothing_says_so_in_bytes():
    from fastmdxplora.sharing.opening import _got

    assert _got(0, 5000, "at") == " at 0 bytes of 5.0 kB"
