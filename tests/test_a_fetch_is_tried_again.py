"""A structure fetched from RCSB is asked for again when the request drops.

The nightly validation on 2.6.0 failed 6B73 on one `Connection reset by
peer`: setup was over in 0.1 s, as the fetch was made once, and the study
was told the machine had no route to the internet, though it had one. A
connection dropped once made, a transfer cut short, or a busy or failing
service is now asked again twice, and a study stopped by it is told what
happened. A name that does not resolve, no route, a connection refused or
never answered, or an entry not there is not asked again: a firewalled
cluster node times out connecting, and would wait out every try to be told
the service was at fault.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import urllib.error

import pytest

from fastmdxplora.refusals import StudyError
from fastmdxplora.setup import pipeline
from fastmdxplora.setup.pipeline import _fetch_pdb_from_rcsb

PDB = "https://files.rcsb.org/download/6B73.pdb"
CIF = "https://files.rcsb.org/download/6B73.cif"


def _reset() -> OSError:
    return urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer"))


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(PDB, code, "x", {}, None)


@pytest.fixture()
def server(monkeypatch):
    """RCSB, answering each request with the next failure given, then the
    file; the waits between tries kept and not slept."""
    state = {"calls": [], "waits": [], "failures": []}

    def download(url, dest):
        state["calls"].append(url)
        if state["failures"]:
            failure = state["failures"].pop(0)
            if failure is not None:
                # Something written before it fails, as a cut-short transfer is.
                pipeline.Path(dest).write_text("HEADER half")
                raise failure
        pipeline.Path(dest).write_text("HEADER fetched\nEND\n")
        return str(dest), None

    monkeypatch.setattr(pipeline, "_download", download)
    monkeypatch.setattr(pipeline, "_sleep", state["waits"].append)
    return state


def test_a_reset_is_asked_again_and_the_file_comes(server, tmp_path) -> None:
    server["failures"] = [_reset()]
    dest = _fetch_pdb_from_rcsb("6b73", tmp_path / "input.pdb")
    assert server["calls"] == [PDB, PDB]
    assert server["waits"] == [2.0]
    assert dest.read_text() == "HEADER fetched\nEND\n"


DROPPED = {
    "reset": _reset(),
    "bare-reset": ConnectionResetError(104, "Connection reset by peer"),
    "aborted": urllib.error.URLError(ConnectionAbortedError(103, "Software caused connection abort")),
    "broken-pipe": urllib.error.URLError(BrokenPipeError(32, "Broken pipe")),
    "tls-eof": urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")),
    "read-timeout": TimeoutError("The read operation timed out"),
    "cut-short": urllib.error.ContentTooShortError("retrieval incomplete", None),
    "incomplete": http.client.IncompleteRead(b"half"),
    "closed": http.client.RemoteDisconnected("Remote end closed connection without response"),
}


@pytest.mark.parametrize("failure", list(DROPPED.values()), ids=list(DROPPED))
def test_a_request_that_drops_each_time_says_so(server, tmp_path, failure) -> None:
    server["failures"] = [failure] * 3
    dest = tmp_path / "input.pdb"
    with pytest.raises(StudyError) as raised:
        _fetch_pdb_from_rcsb("6B73", dest)
    said = str(raised.value)
    assert server["calls"] == [PDB] * 3
    assert server["waits"] == [2.0, 8.0]
    assert "in 3 tries" in said
    assert "no route to the internet" not in said
    assert f"curl -O {PDB}" in said
    assert raised.value.code == "environment.service.unreachable"
    assert raised.value.__cause__ is failure
    assert not dest.exists()


@pytest.mark.parametrize("code", [429, 500, 503])
def test_a_busy_or_failing_service_is_asked_again(server, tmp_path, code) -> None:
    server["failures"] = [_http(code)]
    _fetch_pdb_from_rcsb("6B73", tmp_path / "input.pdb")
    assert server["calls"] == [PDB, PDB]


def test_a_service_failing_each_time_says_how_to_go_on(server, tmp_path) -> None:
    server["failures"] = [_http(503)] * 3
    with pytest.raises(StudyError) as raised:
        _fetch_pdb_from_rcsb("6B73", tmp_path / "input.pdb")
    assert server["calls"] == [PDB] * 3
    assert "in 3 tries" in str(raised.value)
    assert f"curl -O {PDB}" in str(raised.value)


NO_ROUTE = {
    "name": urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution")),
    "bare-name": OSError("[Errno -3] Temporary failure in name resolution"),
    "no-route": urllib.error.URLError(OSError(113, "No route to host")),
    "refused": urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
    # A firewall that drops what goes out: the connection is never answered.
    "connect-timeout": urllib.error.URLError(TimeoutError(110, "Connection timed out")),
}


@pytest.mark.parametrize("failure", list(NO_ROUTE.values()), ids=list(NO_ROUTE))
def test_no_route_out_is_not_asked_again(server, tmp_path, failure) -> None:
    server["failures"] = [failure]
    with pytest.raises(StudyError) as raised:
        _fetch_pdb_from_rcsb("6B73", tmp_path / "input.pdb")
    assert server["calls"] == [PDB]
    assert server["waits"] == []
    assert "no route to the internet" in str(raised.value)


def test_a_refusal_is_not_asked_again(server, tmp_path) -> None:
    server["failures"] = [_http(403)]
    with pytest.raises(StudyError) as raised:
        _fetch_pdb_from_rcsb("6B73", tmp_path / "input.pdb")
    assert server["calls"] == [PDB]
    assert server["waits"] == []
    assert "RCSB refused" in str(raised.value)
    assert "tries" not in str(raised.value)


def test_an_entry_with_no_pdb_file_goes_to_its_mmcif_once(server, tmp_path,
                                                          monkeypatch) -> None:
    written = []
    monkeypatch.setattr(pipeline, "_write_mmcif_as_pdb",
                        lambda cif, target: written.append(cif.name) or target)
    server["failures"] = [_http(404), _reset()]
    _fetch_pdb_from_rcsb("6B73", tmp_path / "input.pdb")
    # The 404 is the answer, not asked again; the mmCIF fetch is.
    assert server["calls"] == [PDB, CIF, CIF]
    assert written == ["input.cif"]


def test_an_mmcif_that_drops_each_time_names_it(server, tmp_path) -> None:
    server["failures"] = [_http(404)] + [_reset()] * 3
    with pytest.raises(StudyError) as raised:
        _fetch_pdb_from_rcsb("6B73", tmp_path / "input.pdb")
    assert server["calls"] == [PDB] + [CIF] * 3
    assert "in 3 tries" in str(raised.value)
    assert f"curl -O {CIF}" in str(raised.value)
    assert not (tmp_path / "input.cif").exists()


@pytest.fixture()
def stalling(monkeypatch):
    """A server on loopback that answers each request with ``said`` and then,
    where ``hold`` is set, holds the connection open, sending nothing more;
    else closes it."""
    import threading

    listener = socket.create_server(("127.0.0.1", 0))
    state = {"said": b"", "hold": True, "stop": threading.Event()}

    def serve() -> None:
        while True:
            try:
                peer, _ = listener.accept()
            except OSError:
                return
            with peer:
                peer.recv(65536)
                peer.sendall(state["said"])
                if state["hold"]:
                    state["stop"].wait(10)

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setattr(pipeline, "_FETCH_TIMEOUT_S", 0.3)
    state["url"] = f"http://127.0.0.1:{listener.getsockname()[1]}/6B73.pdb"
    yield state
    state["stop"].set()
    listener.close()


def test_a_server_that_goes_quiet_is_given_up_on_and_asked_again(stalling,
                                                                  tmp_path) -> None:
    # `urlretrieve` takes no timeout: this waited for as long as the
    # connection stayed open, and the read timeout below was never raised.
    stalling["said"] = b"HTTP/1.0 200 OK\r\nContent-Length: 100\r\n\r\nHEADER"
    with pytest.raises(TimeoutError) as raised:
        pipeline._download(stalling["url"], tmp_path / "input.pdb")
    assert pipeline._passes(raised.value)


def test_a_body_shorter_than_it_said_is_cut_short(stalling, tmp_path) -> None:
    stalling["said"] = b"HTTP/1.0 200 OK\r\nContent-Length: 100\r\n\r\nHEADER"
    stalling["hold"] = False  # the connection closes after the first bytes
    with pytest.raises(urllib.error.ContentTooShortError) as raised:
        pipeline._download(stalling["url"], tmp_path / "input.pdb")
    assert pipeline._passes(raised.value)
