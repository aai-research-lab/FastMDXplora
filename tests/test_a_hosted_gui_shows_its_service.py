"""Hosted behind a service, the GUI shows the service rather than itself:
its name and line at the top of the sidebar, the signed-in person at the foot with
their initials, and the way back to the service's own page for the person
as the first item of the menu there, since the GUI has no other way back.
Only a path on the same site is accepted, only a short one-line name, and
the person's name only from the proxy."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.test_a_hosted_gui_answers_only_its_proxy import NAME, SECRET, _ask, _serving

from fastmdxplora.gui.hosting import ACCOUNT_HEADER, Hosting, HostingError, initials
from fastmdxplora.gui.server import make_handler

PLACEHOLDER = re.compile(r"__FASTMDX_[A-Z_]+__")


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "person"
    folder.mkdir()
    return folder


def _hosting(workspace: Path, **kw) -> Hosting:
    return Hosting(workspace=workspace.resolve(), allowed_hosts=frozenset({NAME}),
                   secret=SECRET, **kw)


def _page(hosting: Hosting, **headers: str) -> str:
    with _serving(hosting) as (address, _):
        status, body = _ask(address, "/", headers={"Host": NAME, **headers})
    assert status == 200
    page = body.decode()
    assert not PLACEHOLDER.search(page), PLACEHOLDER.search(page)
    return page


def _foot(page: str) -> str:
    start = page.index('id="settings-open"')
    return page[start:page.index("</button>", start)]


def _on_ones_own_machine(tmp_path: Path) -> str:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read().decode()
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---- the way back ----

def test_the_menu_opens_with_the_service(workspace: Path) -> None:
    page = _page(_hosting(workspace, account_url="/_mdx/"))
    popup = page[page.index('id="settings-popup"'):]
    item = popup.index('<a href="/_mdx/" class="settings-item" id="settings-account-link">')
    assert item < popup.index('data-theme="graphite"'), "first in the menu"
    assert "sidebar-service" not in page, "one foot item, not two"


def test_without_one_there_is_no_link(workspace: Path) -> None:
    assert 'id="settings-account-link"' not in _page(_hosting(workspace))


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


# ---- the service's name ----

def test_the_service_is_named_where_this_software_was(workspace: Path) -> None:
    page = _page(_hosting(workspace, product_name="Example Lab MD"))
    assert "<title>Example Lab MD</title>" in page
    assert '<div class="brand-product">Example Lab MD</div>' in page
    assert '<div class="loading-product">Example Lab MD</div>' in page
    assert '<div class="brand-tagline"></div>' in page, "no borrowed tagline"
    # The citation and the links to this software stay.
    assert "Cite FastMDXplora&hellip;" in page and 'id="cite-title">Cite FastMDXplora<' in page


def test_the_service_gives_its_own_line(workspace: Path) -> None:
    page = _page(_hosting(workspace, product_name="Example Lab MD",
                          product_tagline="Simulations for the Example Lab"))
    assert '<div class="brand-tagline">Simulations for the Example Lab</div>' in page
    assert '<div class="loading-tagline">Simulations for the Example Lab</div>' in page
    assert "Fully Automated SysTem for Molecular Dynamics eXploration</div>" not in page


def test_a_line_alone_keeps_this_softwares_name(workspace: Path) -> None:
    page = _page(_hosting(workspace, product_tagline="Hosted for the Example Lab"))
    assert '<div class="brand-product">FastMDXplora</div>' in page
    assert '<div class="brand-tagline">Hosted for the Example Lab</div>' in page


def test_a_line_is_shown_as_text(workspace: Path) -> None:
    page = _page(_hosting(workspace, product_tagline="<b>bold</b>"))
    assert "<b>bold</b>" not in page and "&lt;b&gt;bold&lt;/b&gt;" in page


def test_a_long_line_is_refused(workspace: Path, monkeypatch) -> None:
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    with pytest.raises(HostingError, match="--product-tagline"):
        Hosting.from_environment(workspace, [NAME], product_tagline="x" * 81)
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    kept = Hosting.from_environment(workspace, [NAME], product_tagline="x" * 80)
    assert kept.product_tagline == "x" * 80


def test_a_name_is_shown_as_text(workspace: Path) -> None:
    page = _page(_hosting(workspace, product_name='<img src=x onerror="alert(1)">'))
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page


@pytest.mark.parametrize("name", ["x" * 41, "two\nlines", "tab\there"])
def test_only_a_short_name_on_one_line(workspace: Path, monkeypatch, name: str) -> None:
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    if name.startswith("x"):
        with pytest.raises(HostingError, match="--product-name"):
            Hosting.from_environment(workspace, [NAME], product_name=name)
    else:
        # Runs of white space, a new line among them, are one space.
        shown = Hosting.from_environment(workspace, [NAME], product_name=name).product_name
        assert shown == " ".join(name.split())


def test_a_name_with_a_control_character_is_refused(workspace: Path, monkeypatch) -> None:
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    with pytest.raises(HostingError, match="--product-name"):
        Hosting.from_environment(workspace, [NAME], product_name="a\x07b")


def test_without_a_name_this_software_is_named(workspace: Path) -> None:
    page = _page(_hosting(workspace))
    assert "<title>FastMDXplora GUI</title>" in page
    assert '<div class="brand-product">FastMDXplora</div>' in page
    assert "Fully Automated SysTem for Molecular Dynamics eXploration</div>" in page


# ---- the person ----

def test_the_person_is_named_at_the_foot(workspace: Path) -> None:
    hosting = _hosting(workspace, product_name="MDXplora")
    foot = _foot(_page(hosting, **{ACCOUNT_HEADER: "Ad%C3%A9%20Lovelace"}))
    assert '<span class="sidebar-account-avatar" aria-hidden="true">AL</span>' in foot
    assert 'id="account-name">Adé Lovelace</span>' in foot
    assert 'title="Adé Lovelace"' in foot


def test_the_name_changes_with_the_request(workspace: Path) -> None:
    # Named per request, so a name changed at the service shows on the next
    # page load, with nothing restarted.
    hosting = _hosting(workspace)
    with _serving(hosting) as (address, _):
        pages = [_ask(address, "/", headers={"Host": NAME, ACCOUNT_HEADER: who})[1].decode()
                 for who in ("ada%40example.org", "Grace%20Hopper")]
    assert 'id="account-name">ada@example.org<' in _foot(pages[0])
    assert 'aria-hidden="true">A</span>' in _foot(pages[0])
    assert 'id="account-name">Grace Hopper<' in _foot(pages[1])


def test_a_persons_name_is_shown_as_text(workspace: Path) -> None:
    foot = _foot(_page(_hosting(workspace),
                       **{ACCOUNT_HEADER: "%3Cscript%3Ealert(1)%3C%2Fscript%3E"}))
    assert "<script>" not in foot
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in foot


@pytest.mark.parametrize("spelling", ["__FASTMDX_ACCOUNT_INITIALS__", "__FASTMDX_ACCOUNT_NAME__",
                                      "__FASTMDX_TAGLINE__", "__FASTMDX_PRODUCT__"])
def test_a_name_spelled_like_a_placeholder_is_only_a_name(workspace: Path, spelling: str) -> None:
    hosting = _hosting(workspace, product_name=spelling, product_tagline=spelling)
    with _serving(hosting) as (address, _):
        status, body = _ask(address, "/", headers={"Host": NAME, ACCOUNT_HEADER: spelling})
    page = body.decode()
    assert f'id="account-name">{spelling}<' in _foot(page)
    assert f'<div class="brand-product">{spelling}</div>' in page
    assert f'<div class="brand-tagline">{spelling}</div>' in page


def test_hidden_and_reordering_characters_are_dropped(workspace: Path) -> None:
    # A right-to-left override, a zero-width space and a byte-order mark;
    # the joiners some scripts need are kept.
    foot = _foot(_page(_hosting(workspace),
                       **{ACCOUNT_HEADER: "Ada%E2%80%AE%E2%80%8B%20X%EF%BB%BF%E2%80%8Cy"}))
    assert 'id="account-name">Ada X\u200cy<' in foot


def test_a_product_name_with_a_direction_override_is_refused(workspace: Path,
                                                             monkeypatch) -> None:
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    with pytest.raises(HostingError, match="--product-name"):
        Hosting.from_environment(workspace, [NAME], product_name="MD\u202eX")


def test_a_long_name_is_shortened(workspace: Path) -> None:
    foot = _foot(_page(_hosting(workspace), **{ACCOUNT_HEADER: "a" * 300}))
    assert ("a" * 79 + "…") in foot and "a" * 80 not in foot


def test_without_the_header_the_foot_names_the_product(workspace: Path) -> None:
    foot = _foot(_page(_hosting(workspace, product_name="MDXplora")))
    assert 'id="account-name">MDXplora<' in foot
    # No initials for nobody: the logo is the avatar.
    assert '<span class="sidebar-account-avatar" aria-hidden="true"><img ' in foot


def test_without_the_secret_the_header_is_not_read(workspace: Path) -> None:
    with _serving(_hosting(workspace)) as (address, _):
        status, body = _ask(address, "/", secret=None,
                            headers={"Host": NAME, ACCOUNT_HEADER: "Mallory"})
    assert status == 403 and b"Mallory" not in body


@pytest.mark.parametrize("name, letters", [
    ("Ada Lovelace", "AL"), ("Adekunle Aina Jr", "AA"), ("ada@example.org", "A"),
    ("ada.lovelace@example.org", "A"), ("  ", ""), ("", ""), ("Émile Zola", "ÉZ"),
    ("-", ""), ("\ufb01sh Jones", "FI"), ("\u00df@example.org", "S")])
def test_initials(name: str, letters: str) -> None:
    assert initials(name) == letters


# ---- on one's own machine ----

def test_on_ones_own_machine_nothing_changes(tmp_path: Path) -> None:
    page = _on_ones_own_machine(tmp_path)
    assert not PLACEHOLDER.search(page)
    assert 'id="settings-account-link"' not in page
    assert '<div class="brand-product">FastMDXplora</div>' in page
    assert 'id="account-name">FastMDXplora<' in _foot(page)
    assert '<span class="sidebar-account-avatar" aria-hidden="true"><img ' in _foot(page)


def test_on_ones_own_machine_the_header_is_not_read(tmp_path: Path) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/",
                                         headers={ACCOUNT_HEADER: "Mallory"})
        with urllib.request.urlopen(request, timeout=30) as response:
            page = response.read().decode()
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert "Mallory" not in page


@pytest.mark.parametrize("flag", [["--account-url", "/_mdx/"], ["--product-name", "MDXplora"],
                                  ["--product-tagline", "A line"]])
def test_the_flags_need_hosted(tmp_path: Path, flag: list[str]) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "fastmdxplora.cli.main", "gui", *flag,
         "--no-browser", "--port", "0"],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
        env={**os.environ, "FASTMDX_PROXY_SECRET": ""})
    assert done.returncode == 2
    assert "apply only with --hosted" in done.stderr
