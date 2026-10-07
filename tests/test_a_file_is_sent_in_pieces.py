"""A study's file is sent from the disk in pieces, a range of it on request.

`/artifacts/` read the whole file into memory and sent it with `no-store`:
a 10 GB trajectory took 10 GB of the server's memory, a download cut short
started again from nothing, and a figure shown twice was sent twice.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora.gui.server import attachment_named, byte_range, start_dashboard_session


@pytest.fixture()
def served(tmp_path: Path):
    study = tmp_path / "study"
    (study / "simulation").mkdir(parents=True)
    (study / "manifest.json").write_text("{}", encoding="utf-8")
    data = bytes(range(256)) * 9000  # 2.3 MB, more than one piece
    (study / "simulation" / "production.dcd").write_bytes(data)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        yield session.url, data, study
    finally:
        session.server.shutdown()


def _get(url: str, headers: dict[str, str] | None = None, method: str = "GET"):
    request = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_the_whole_file_says_it_can_be_had_in_parts(served):
    url, data, _ = served
    status, headers, body = _get(url + "/artifacts/simulation/production.dcd")
    assert status == 200 and body == data
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Length"] == str(len(data))
    assert headers["ETag"].startswith('"')


def test_a_range_is_sent_alone(served):
    url, data, _ = served
    status, headers, body = _get(url + "/artifacts/simulation/production.dcd",
                                 {"Range": "bytes=1000-1999"})
    assert status == 206
    assert body == data[1000:2000]
    assert headers["Content-Range"] == f"bytes 1000-1999/{len(data)}"
    # A download resumed from where it stopped.
    status, _, body = _get(url + "/artifacts/simulation/production.dcd",
                           {"Range": f"bytes={len(data) - 10}-"})
    assert status == 206 and body == data[-10:]
    status, _, body = _get(url + "/artifacts/simulation/production.dcd", {"Range": "bytes=-5"})
    assert status == 206 and body == data[-5:]


def test_a_range_past_the_end_is_refused(served):
    url, data, _ = served
    status, headers, _ = _get(url + "/artifacts/simulation/production.dcd",
                              {"Range": f"bytes={len(data)}-"})
    assert status == 416
    assert headers["Content-Range"] == f"bytes */{len(data)}"


def test_a_copy_the_browser_holds_is_not_sent_again(served):
    url, _, study = served
    _, headers, _ = _get(url + "/artifacts/simulation/production.dcd")
    status, _, body = _get(url + "/artifacts/simulation/production.dcd",
                           {"If-None-Match": headers["ETag"]})
    assert status == 304 and body == b""
    # Changed, it is sent again, and a range of the old one is not mixed in.
    (study / "simulation" / "production.dcd").write_bytes(b"new")
    status, _, body = _get(url + "/artifacts/simulation/production.dcd",
                           {"If-None-Match": headers["ETag"], "Range": "bytes=0-0",
                            "If-Range": headers["ETag"]})
    assert status == 200 and body == b"new"


def test_head_says_the_size_without_the_file(served):
    url, data, _ = served
    status, headers, body = _get(url + "/artifacts/simulation/production.dcd", method="HEAD")
    assert status == 200 and body == b""
    assert headers["Content-Length"] == str(len(data))
    assert _get(url + "/api/status", method="HEAD")[0] == 405


def test_the_file_is_never_read_whole(served, monkeypatch):
    url, data, _ = served

    def whole(self):  # pragma: no cover - failing is the point
        raise AssertionError(f"{self} was read whole")

    monkeypatch.setattr(Path, "read_bytes", whole)
    status, _, body = _get(url + "/artifacts/simulation/production.dcd")
    assert status == 200 and body == data


def test_the_ranges_read_as_the_standard_reads_them():
    assert byte_range("bytes=0-9", 100) == (0, 9)
    assert byte_range("bytes=90-200", 100) == (90, 99)
    assert byte_range("bytes=-10", 100) == (90, 99)
    assert byte_range("bytes=-500", 100) == (0, 99)
    assert byte_range("bytes=100-", 100) is None
    assert byte_range("bytes=0-", 0) is None
    # Several ranges, a unit not bytes, or nonsense: the whole file.
    assert byte_range("bytes=-0", 100) is None
    assert byte_range("bytes=+5-9", 100) == ()
    assert byte_range("bytes=1_0-20", 100) == ()
    assert byte_range("bytes=-", 100) == ()
    assert byte_range("bytes=0-1,5-6", 100) == ()
    assert byte_range("items=0-1", 100) == ()
    assert byte_range("bytes=x-y", 100) == ()
    assert byte_range("bytes=5-2", 100) == ()


def test_a_name_is_kept_for_the_download():
    assert attachment_named("report.md") == 'attachment; filename="report.md"'
    said = attachment_named("résumé \"1\".pdf")
    assert said.startswith('attachment; filename="r_sum_ _1_.pdf"; ')
    assert said.endswith("filename*=UTF-8''r%C3%A9sum%C3%A9%20%221%22.pdf")
