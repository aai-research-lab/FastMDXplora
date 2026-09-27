"""A page on another site cannot drive the GUI through the person's browser.

The GUI on loopback trusted whatever reached it, and a browser sends a
request to 127.0.0.1 for any page it has open. A form on any website, posted
as text/plain, switched the served folder, stored a model and key, or started
a run while the person had the GUI open; and a name that resolves to
127.0.0.1 let such a page read the answers too. On loopback the server now
answers only to a loopback name, and it refuses a POST whose Origin is not
its own and an API request a browser marks as cross-site. A script, which
sends no Origin, is answered as before.
"""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fastmdxplora.gui import server as dashboard
from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.server import make_handler


@pytest.fixture()
def runtime(tmp_path: Path) -> DashboardRuntime:
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    return DashboardRuntime(workspace_root=run, exploration_root=tmp_path,
                            active_root=run)


@pytest.fixture()
def elsewhere(tmp_path: Path) -> Path:
    folder = tmp_path / "elsewhere"
    (folder / "analysis").mkdir(parents=True)
    return folder


@contextlib.contextmanager
def _serving(runtime: DashboardRuntime, *, allow_control: bool = True):
    handler = make_handler(runtime.workspace_root, runtime=runtime,
                           allow_control=allow_control)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _ask(address: str, path: str, *, body: dict | None = None,
         headers: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://{address}{path}", data=data,
        method="GET" if body is None else "POST", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.getcode(), json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


class TestAPostFromAnotherSite:

    @pytest.mark.parametrize("headers", [
        {"Origin": "https://evil.example", "Content-Type": "text/plain"},
        {"Origin": "null", "Content-Type": "text/plain"},
        {"Origin": "http://127.0.0.1:1", "Content-Type": "text/plain"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ])
    def test_it_is_refused_and_changes_nothing(
        self, runtime, elsewhere, headers
    ) -> None:
        before = runtime.active_root
        with _serving(runtime) as address:
            status, answer = _ask(address, "/api/explore/switch",
                                  body={"folder": str(elsewhere)}, headers=headers)
        assert status == 403
        assert answer == {"ok": False, "error": "A request from another site is refused."}
        assert runtime.active_root == before

    def test_the_gui_itself_is_answered(self, runtime, elsewhere) -> None:
        with _serving(runtime) as address:
            status, answer = _ask(
                address, "/api/explore/switch", body={"folder": str(elsewhere)},
                headers={"Origin": f"http://{address}", "Sec-Fetch-Site": "same-origin",
                         "Content-Type": "application/json"})
        assert status == 200 and answer["ok"] is True
        assert runtime.active_root == elsewhere.resolve()

    def test_a_script_is_answered(self, runtime, elsewhere) -> None:
        with _serving(runtime) as address:
            status, answer = _ask(address, "/api/explore/switch",
                                  body={"folder": str(elsewhere)})
        assert status == 200 and answer["ok"] is True


class TestANameThatIsNotThisMachine:

    @pytest.mark.parametrize("path", ["/", "/api/status", "/api/agent/conversations"])
    def test_on_loopback_it_is_refused(self, runtime, path) -> None:
        with _serving(runtime) as address:
            port = address.rsplit(":", 1)[1]
            status, answer = _ask(address, path,
                                  headers={"Host": f"rebound.example:{port}"})
        assert status == 403
        assert answer == {"ok": False, "error": "This server answers only to localhost."}

    @pytest.mark.parametrize("name", ["localhost", "127.0.0.1", "127.0.0.2", "[::1]",
                                      "app.localhost"])
    def test_a_loopback_name_is_answered(self, runtime, name) -> None:
        with _serving(runtime) as address:
            port = address.rsplit(":", 1)[1]
            status, _ = _ask(address, "/api/status", headers={"Host": f"{name}:{port}"})
        assert status == 200

    def test_beyond_loopback_any_name_is(self, runtime) -> None:
        # The machine is reached there by whatever name the network gives it.
        with _serving(runtime, allow_control=False) as address:
            status, _ = _ask(address, "/api/status",
                             headers={"Host": "labbox.example.edu:8765"})
        assert status == 200


class TestAGetFromAnotherSite:

    def test_an_api_request_is_refused(self, runtime, monkeypatch) -> None:
        opened: list = []
        monkeypatch.setattr(dashboard, "_open_local_path",
                            lambda path: (opened.append(path), (True, "opened"))[1])
        with _serving(runtime) as address:
            status, _ = _ask(address, "/api/open-output",
                             headers={"Sec-Fetch-Site": "cross-site"})
            mine, _ = _ask(address, "/api/open-output",
                           headers={"Sec-Fetch-Site": "same-origin"})
        assert status == 403
        assert mine == 200 and len(opened) == 1

    def test_the_page_can_still_be_linked_to(self, runtime) -> None:
        with _serving(runtime) as address:
            request = urllib.request.Request(
                f"http://{address}/", headers={"Sec-Fetch-Site": "cross-site",
                                               "Sec-Fetch-Mode": "navigate"})
            with urllib.request.urlopen(request, timeout=30) as response:
                assert response.getcode() == 200


class TestInTheBrowser:
    """A page served from another address posts to the GUI the way a form
    or a no-cors fetch can, without a preflight."""

    def test_the_post_does_not_land(self, runtime, elsewhere) -> None:
        pytest.importorskip("playwright.sync_api")
        from playwright.sync_api import sync_playwright

        with _serving(runtime) as address:
            attack = (
                "<!doctype html><body><script>"
                f"fetch('http://{address}/api/explore/switch', {{method: 'POST',"
                " mode: 'no-cors', headers: {'Content-Type': 'text/plain'},"
                f" body: JSON.stringify({{folder: {json.dumps(str(elsewhere))}}})}})"
                ".finally(() => { document.body.dataset.done = '1'; });"
                "</script></body>").encode()

            class Page(BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802 - stdlib API
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(attack)))
                    self.end_headers()
                    self.wfile.write(attack)

                def log_message(self, *args):
                    return

            other = ThreadingHTTPServer(("127.0.0.1", 0), Page)
            threading.Thread(target=other.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    page = browser.new_page()
                    # Another port is another origin, and the same site:
                    # the case of a second local tool's page.
                    page.goto(f"http://localhost:{other.server_address[1]}/")
                    page.wait_for_selector("body[data-done='1']", state="attached",
                                           timeout=20000)
                    browser.close()
            finally:
                other.shutdown()
                other.server_close()
        assert runtime.active_root == runtime.workspace_root
