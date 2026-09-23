"""A tab closed mid-request is not an error.

The server already treated a caller hanging up as weather, not a fault:
its handle_error logs a ConnectionError at debug and moves on. But the
routes caught every exception first, so a hang-up never reached it --
each one logged "dashboard route failed: [Errno 32] Broken pipe" as a
warning, then wrote a 500 to the closed socket, which failed again.

The hang-up is raised from inside the route, as a failed write raises it.
A real socket closed mid-response would test the same thing only when the
server's write happened to fail -- a response that fits the send buffer
never fails at all, and then a test of silence passes whether or not
anything was fixed.
"""

from __future__ import annotations

import http.client
import logging
from urllib.parse import urlparse

import pytest

HANG_UPS = [BrokenPipeError, ConnectionResetError, ConnectionAbortedError]


@pytest.fixture()
def address(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=tmp_path, host="127.0.0.1", port=0)
    parsed = urlparse(session.url)
    try:
        yield parsed.hostname, parsed.port
    finally:
        session.stop()


def _fail_first_line_with(monkeypatch, error):
    """Both routes parse the path first; make that raise."""
    from fastmdxplora.gui import server

    def raises(*args, **kwargs):
        raise error("injected")

    monkeypatch.setattr(server, "urlparse", raises)


def _ask(address, method: str):
    """The status the server wrote back, or None if it wrote nothing."""
    host, port = address
    connection = http.client.HTTPConnection(host, port, timeout=10)
    body = "{}" if method == "POST" else None
    headers = {"Content-Type": "application/json"} if body else {}
    try:
        connection.request(method, "/api/anything", body=body, headers=headers)
        return connection.getresponse().status
    except (http.client.RemoteDisconnected, ConnectionError):
        return None
    finally:
        connection.close()


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("hang_up", HANG_UPS, ids=lambda e: e.__name__)
def test_a_hang_up_is_not_logged_as_a_failure(address, caplog, monkeypatch, method, hang_up):
    # Every way a caller leaves: Windows raises ConnectionAbortedError where
    # Linux and macOS raise BrokenPipeError or ConnectionResetError.
    _fail_first_line_with(monkeypatch, hang_up)
    with caplog.at_level(logging.DEBUG, logger="fastmdxplora.gui.server"):
        written = _ask(address, method)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == []
    assert any("hung up" in r.getMessage() for r in caplog.records)
    # And nothing is written back: there is nobody to read it.
    assert written is None


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_real_failure_is_still_a_warning_and_a_500(address, caplog, monkeypatch, method):
    # Quiet for a hang-up only. Anything else that breaks a route is still
    # said out loud and still answered.
    _fail_first_line_with(monkeypatch, RuntimeError)
    with caplog.at_level(logging.WARNING, logger="fastmdxplora.gui.server"):
        written = _ask(address, method)
    assert written == 500
    # Said, and said of the route that failed.
    assert any(r.levelno == logging.WARNING and "/api/anything" in r.getMessage()
               and "failed" in r.getMessage() for r in caplog.records)
