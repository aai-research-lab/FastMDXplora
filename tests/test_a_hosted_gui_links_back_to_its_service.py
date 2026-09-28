"""Hosted behind a service, the GUI's sidebar links to the service's own
page for the person (their account, signing out): the GUI has no other way
back to it. Only a path on the same site is accepted."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_a_hosted_gui_answers_only_its_proxy import NAME, SECRET, _ask, _serving

from fastmdxplora.gui.hosting import Hosting, HostingError
from fastmdxplora.gui.server import make_handler

MARKER = "__FASTMDX_ACCOUNT_LINK__"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "person"
    folder.mkdir()
    return folder


def _page(hosting: Hosting) -> str:
    with _serving(hosting) as (address, _):
        status, body = _ask(address, "/", headers={"Host": NAME})
    assert status == 200
    return body.decode()


def test_the_sidebar_links_to_the_service(workspace: Path) -> None:
    hosting = Hosting(workspace=workspace.resolve(), allowed_hosts=frozenset({NAME}),
                      secret=SECRET, account_url="/_mdx/")
    page = _page(hosting)
    assert '<a class="sidebar-service" href="/_mdx/"' in page
    assert MARKER not in page


def test_without_one_there_is_no_link(workspace: Path) -> None:
    hosting = Hosting(workspace=workspace.resolve(), allowed_hosts=frozenset({NAME}),
                      secret=SECRET)
    page = _page(hosting)
    assert 'class="sidebar-service"' not in page
    assert MARKER not in page


def test_on_ones_own_machine_there_is_no_link(tmp_path: Path) -> None:
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        with urllib.request.urlopen(url, timeout=30) as response:
            page = response.read().decode()
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert 'class="sidebar-service"' not in page
    assert MARKER not in page


@pytest.mark.parametrize("url", ["//evil.example/", "https://evil.example/", "javascript:alert(1)",
                                 "/a b", '/x"onmouseover="alert(1)', "account", "/x?y=1"])
def test_only_a_path_on_this_site(workspace: Path, monkeypatch, url: str) -> None:
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    with pytest.raises(HostingError, match="not a path on this site"):
        Hosting.from_environment(workspace, [NAME], url)


def test_a_path_on_this_site_is_kept(workspace: Path, monkeypatch) -> None:
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    assert Hosting.from_environment(workspace, [NAME], "/_mdx/").account_url == "/_mdx/"
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    assert Hosting.from_environment(workspace, [NAME]).account_url == ""


def test_the_flag_needs_hosted(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "fastmdxplora.cli.main", "gui", "--account-url", "/_mdx/",
         "--no-browser", "--port", "0"],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
        env={**os.environ, "FASTMDX_PROXY_SECRET": ""})
    assert done.returncode == 2
    assert "apply only with --hosted" in done.stderr
