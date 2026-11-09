"""`--dashboard-open-browser` opens the live dashboard in the local browser.

The flag was parsed and then read by nothing: the URL was printed and the
browser stayed shut. It is off by default, for a machine with no display.
"""

from __future__ import annotations

import argparse
import importlib
import types

import pytest

# The module, not the `main` function the package exports under its name.
cli = importlib.import_module("fastmdxplora.cli.main")


@pytest.fixture
def opened(monkeypatch, tmp_path):
    import webbrowser

    import fastmdxplora.gui.server as server

    session = types.SimpleNamespace(url="http://127.0.0.1:8765/", port_was_changed=False,
                                    requested_port=8765, port=8765)
    monkeypatch.setattr(server, "start_dashboard_session", lambda **_: session)
    monkeypatch.delenv("FASTMDX_DASHBOARD_ACTIVE", raising=False)
    monkeypatch.delenv("FASTMDX_DASHBOARD_OUTPUT", raising=False)
    urls: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, new=0: urls.append(url))

    def start(open_browser: bool) -> list[str]:
        args = argparse.Namespace(dashboard_host="127.0.0.1", dashboard_port=8765,
                                  dashboard_stop_on_complete=True,
                                  dashboard_open_browser=open_browser)
        cli._start_dashboard_for_command(args, tmp_path)
        return urls

    return start


def test_the_browser_opens_when_asked(opened):
    assert opened(True) == ["http://127.0.0.1:8765/"]


def test_the_browser_stays_shut_by_default(opened):
    assert opened(False) == []
