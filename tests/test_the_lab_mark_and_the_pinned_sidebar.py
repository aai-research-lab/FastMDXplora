"""The lab's mark is the tab's icon and the avatar, and the sidebar's ends cover what scrolls.

On a person's own machine the GUI had no icon for its tab and an empty grey
circle at the foot of the sidebar. The AAi Research Lab's logo (from its
site, scaled to 128 px) is now both, in the GUI and in the standalone
dashboard. A hosted GUI shows it too unless the service gives its own with
`--product-logo`, and the avatar is the person's initials wherever the proxy
names somebody.

The sidebar's two pinned ends, the name at the top and the avatar at the
foot, sat inside its padding: 14px in from either side and, at the foot,
12px above the bottom. What scrolled under them showed through beside them
and below the foot. They now reach the sidebar's edges.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.test_a_hosted_gui_answers_only_its_proxy import NAME, SECRET
from tests.test_a_hosted_gui_shows_its_service import (
    _foot, _hosting, _on_ones_own_machine, _page, workspace)  # noqa: F401 - a fixture

from fastmdxplora.gui.hosting import ACCOUNT_HEADER, Hosting, HostingError

import fastmdxplora.gui as gui_pkg
from fastmdxplora.gui.server import LAB_LOGO

LOGO = Path(gui_pkg.__file__).with_name("static") / LAB_LOGO
ICON = f'<link rel="icon" href="/static/{LAB_LOGO}">'
MARK = f'<img class="sidebar-account-logo" src="/static/{LAB_LOGO}" alt="">'


def test_the_mark_ships_as_a_small_png() -> None:
    data = LOGO.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) < 20_000


def test_on_ones_own_machine_it_is_the_icon_and_the_avatar(tmp_path: Path) -> None:
    page = _on_ones_own_machine(tmp_path)
    head = page[:page.index("</head>")]
    assert ICON in head
    assert MARK in _foot(page)


def _service_logo(tmp_path: Path) -> Path:
    logo = tmp_path / "service.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8">'
                    '<rect width="8" height="8"/></svg>', encoding="utf-8")
    return logo


def test_hosted_it_is_the_labs_unless_the_service_gives_its_own(workspace, tmp_path,  # noqa: F811
                                                                monkeypatch) -> None:
    lab = _page(_hosting(workspace, product_name="Example Lab MD"))
    assert ICON in lab and MARK in _foot(lab)

    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    own = Hosting.from_environment(workspace, [NAME], product_logo=str(_service_logo(tmp_path)))
    assert own.product_logo.startswith("data:image/svg+xml;base64,")
    page = _page(own)
    assert LAB_LOGO not in page
    assert f'<link rel="icon" href="{own.product_logo}">' in page
    assert f'<img class="sidebar-account-logo" src="{own.product_logo}" alt="">' in _foot(page)


def test_a_webp_logo_is_taken_by_its_content(workspace, tmp_path, monkeypatch) -> None:  # noqa: F811
    logo = tmp_path / "logo.bin"
    logo.write_bytes(b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 16)
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    found = Hosting.from_environment(workspace, [NAME], product_logo=str(logo))
    assert found.product_logo.startswith("data:image/webp;base64,")


def test_a_person_signed_in_is_the_avatar(workspace) -> None:  # noqa: F811
    page = _page(_hosting(workspace), **{ACCOUNT_HEADER: "Ada%20Lovelace"})
    foot = _foot(page)
    assert '<span class="sidebar-account-avatar" aria-hidden="true">AL</span>' in foot
    assert "sidebar-account-logo" not in foot
    assert ICON in page, "the tab keeps its icon"


@pytest.mark.parametrize("content, said", [
    (b"just text", "not a PNG, JPEG, GIF, WebP, ICO or SVG"),
    (b"\x89PNG\r\n\x1a\n" + b"0" * (300 * 1024), "at most 262,144"),
])
def test_a_logo_that_is_not_a_small_picture_is_refused(workspace, tmp_path, monkeypatch,  # noqa: F811
                                                       content, said) -> None:
    logo = tmp_path / "logo.png"
    logo.write_bytes(content)
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    with pytest.raises(HostingError, match=said):
        Hosting.from_environment(workspace, [NAME], product_logo=str(logo))
    monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
    with pytest.raises(HostingError, match="cannot be read"):
        Hosting.from_environment(workspace, [NAME], product_logo=str(tmp_path / "none.png"))


def test_the_logo_is_a_hosted_option_only(tmp_path, capsys) -> None:
    from fastmdxplora.cli.main import main

    assert main(["gui", "--output", str(tmp_path), "--no-browser",
                 "--product-logo", str(_service_logo(tmp_path))]) == 2
    assert "--product-logo" in capsys.readouterr().err


def test_the_standalone_dashboard_carries_the_same_mark(tmp_path: Path) -> None:
    from fastmdxplora.gui.report_dashboard import build_dashboard

    (tmp_path / "manifest.json").write_text(json.dumps({"system": "1L2Y", "phases": []}),
                                            encoding="utf-8")
    build_dashboard(orchestrator=SimpleNamespace(output_dir=tmp_path, system="1L2Y"),
                    output_dir=tmp_path / "report", title="A study")
    html = (tmp_path / "report" / "dashboard.html").read_text(encoding="utf-8")
    inline = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")
    assert f'<link rel="icon" type="image/png" href="{inline}">' in html
    assert f'<img class="sidebar-account-logo" src="{inline}" alt="">' in html


def _what_shows_at_the_ends(page) -> list[str]:
    """What is painted along the top and bottom strips of the scrolled
    sidebar, edge to edge: only its pinned ends should be."""
    return page.evaluate("""() => {
      const side = document.querySelector('.sidebar');
      side.scrollTop = 140;
      const box = side.getBoundingClientRect(), found = [];
      // Along the top and bottom of each pinned end, however tall it is.
      // The GUI pins its name with New study and the Agent under it.
      const brand = (side.querySelector('.sidebar-top') || side.querySelector('.sidebar-brand'))
        .getBoundingClientRect();
      const foot = side.querySelector('.sidebar-foot').getBoundingClientRect();
      for (const y of [box.top + 3, brand.bottom - 4, box.bottom - 4, foot.top + 4])
        for (const x of [box.left + 3, box.left + box.width / 2, box.right - 4]) {
          const at = document.elementFromPoint(x, y);
          found.push(!at ? 'nothing' : at.closest('.sidebar-top, .sidebar-brand') ? 'brand'
                     : at.closest('.sidebar-foot') ? 'foot' : at.className || at.tagName);
        }
      return found; }""")


def test_what_scrolls_passes_under_the_sidebars_ends(tmp_path: Path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.report_dashboard import build_dashboard
    from fastmdxplora.gui.server import start_dashboard_session

    (tmp_path / "manifest.json").write_text(json.dumps({"system": "1L2Y", "phases": []}),
                                            encoding="utf-8")
    build_dashboard(orchestrator=SimpleNamespace(output_dir=tmp_path, system="1L2Y"),
                    output_dir=tmp_path / "report", title="A study")
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    seen: dict[str, list[str]] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for name, url, height in (
                    ("gui", session.url, 520),
                    # The standalone sidebar holds less, so a shorter window.
                    ("standalone", (tmp_path / "report" / "dashboard.html").as_uri(), 380)):
                page = browser.new_page(viewport={"width": 1280, "height": height})
                page.set_default_timeout(60000)
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_selector(".sidebar-foot .sidebar-account-logo")
                if name == "gui":
                    # The loading screen gone, not fading, or it is what is read.
                    page.wait_for_function(
                        "() => document.body.classList.contains('state-ready') && "
                        "getComputedStyle(document.querySelector('.loading-screen'))"
                        ".opacity === '0'")
                seen[name] = _what_shows_at_the_ends(page)
                page.close()
            browser.close()
    finally:
        session.server.shutdown()
    for name, found in seen.items():
        assert found == ["brand"] * 6 + ["foot"] * 6, (name, found)
