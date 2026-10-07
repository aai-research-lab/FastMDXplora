"""A file of the study is shown in the file manager, and its SHA-256 copied.

The Files page's menu for a file: **Show in Finder** (Explorer, or the
folder elsewhere) on the person's own computer, and **Copy SHA-256**, by
which a deposit's reader or a methods section names the very file.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from fastmdxplora.deposit import sha256_of
from fastmdxplora.gui import server as dashboard
from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.server import make_handler


@pytest.fixture()
def study(tmp_path: Path) -> Path:
    root = (tmp_path / "study").resolve()
    (root / "simulation").mkdir(parents=True)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "simulation" / "production.dcd").write_bytes(b"frames" * 400_000)
    (root / ".hidden").write_text("not for the page", encoding="utf-8")
    return root


@contextlib.contextmanager
def _serving(study: Path, *, allow_control: bool = True):
    runtime = DashboardRuntime(workspace_root=study, exploration_root=study.parent,
                               active_root=study)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                make_handler(study, runtime=runtime, allow_control=allow_control))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _ask(url: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_sha256_is_the_file_s(study):
    expected = hashlib.sha256((study / "simulation" / "production.dcd").read_bytes()).hexdigest()
    assert sha256_of(study / "simulation" / "production.dcd") == expected
    with _serving(study) as url:
        status, said = _ask(url + "/api/files/sha256?path=simulation/production.dcd")
        assert status == 200 and said == {"ok": True, "path": "simulation/production.dcd",
                                          "sha256": expected}
        assert _ask(url + "/api/files/sha256?path=../elsewhere")[0] == 403
        assert _ask(url + "/api/files/sha256?path=.hidden")[0] == 404
        assert _ask(url + "/api/files/sha256?path=nothing.dcd")[0] == 404
        # A name with a percent in it, decoded once.
        (study / "a%41.dat").write_text("percent", encoding="utf-8")
        status, said = _ask(url + "/api/files/sha256?path=a%2541.dat")
        assert status == 200 and said["path"] == "a%41.dat"


def test_a_file_is_shown_in_the_file_manager(study, monkeypatch):
    shown = []
    monkeypatch.setattr(dashboard, "_reveal_local_path",
                        lambda path: shown.append(path) or (True, "shown"))
    with _serving(study) as url:
        status, said = _ask(url + "/api/files/reveal", {"path": "simulation/production.dcd"})
        assert status == 200 and said == {"ok": True, "error": ""}
        assert _ask(url + "/api/files/reveal", {"path": "../elsewhere.txt"})[0] == 403
    assert shown == [study / "simulation" / "production.dcd"]


def test_beyond_loopback_neither_is_offered_nor_answered(study):
    with _serving(study, allow_control=False) as url:
        assert _ask(url + "/api/files/sha256?path=simulation/production.dcd")[0] == 403
        assert _ask(url + "/api/files/reveal", {"path": "simulation/production.dcd"})[0] == 403
        assert not any(_ask(url + "/api/files-page")[1]["can"].values())
    with _serving(study) as url:
        can = _ask(url + "/api/files-page")[1]["can"]
        assert can["zip"] and can["reveal"] and can["sha"]


def test_each_system_s_file_manager_is_asked_its_own_way(tmp_path, monkeypatch):
    target = tmp_path / "production.dcd"
    target.write_bytes(b"x")
    started = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda args: started.append(args))
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    assert dashboard._reveal_local_path(target) == (True, "shown")
    assert started[-1] == ["open", "-R", str(target)]
    monkeypatch.setattr(dashboard.sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: None)
    opened = []
    monkeypatch.setattr(dashboard, "_open_local_path", lambda path: opened.append(path) or (True, "opened"))
    assert dashboard._reveal_local_path(target) == (True, "opened")
    assert opened == [tmp_path]
    assert dashboard._reveal_local_path(tmp_path / "gone.dcd")[0] is False


def test_the_menu_offers_both_in_a_browser(study):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            context.grant_permissions(["clipboard-read", "clipboard-write"])
            page = context.new_page()
            page.goto(session.url + "#files", wait_until="domcontentloaded")
            page.wait_for_selector('.files-row[data-path="simulation/production.dcd"]', timeout=30000)
            page.click('.files-row[data-path="simulation/production.dcd"] [data-menu]')
            items = page.eval_on_selector_all(".files-menu [role=menuitem]",
                                              "els => els.map(e => e.getAttribute('data-do') || 'open')")
            assert items == ["open", "copy", "reveal", "sha"]
            page.click(".files-menu [data-do=sha]")
            copied = ""
            for _ in range(100):  # read once computed: a promise is not a wait
                copied = page.evaluate("navigator.clipboard.readText()")
                if len(copied) == 64:
                    break
                page.wait_for_timeout(200)
            assert copied == sha256_of(study / "simulation" / "production.dcd")
            browser.close()
    finally:
        session.server.shutdown()
