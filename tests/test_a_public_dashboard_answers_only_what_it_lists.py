"""Bound beyond loopback, the dashboard refuses every POST it does not list.

There is no login, so the bind address is the whole of the trust model. The
gate was a list of routes to refuse, and `/api/explore/switch` was not on it:
a caller anywhere on the network could point the served root at any folder
shaped like a study, after which `/artifacts/<path>` and `/api/file-text`
served what was in it, and the switch's two error messages said whether an
arbitrary path existed. The gate now lists what stays open instead, so a
route nobody remembered is refused rather than answered.

The agent's conversations were read back by GET off loopback although every
POST that writes one was refused. They hold what somebody typed and the
content of files they attached, so reading them is gated the same way.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from fastmdxplora.gui import server as dashboard
from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.server import POSTS_ANSWERED_BEYOND_LOOPBACK, make_handler

REFUSAL = {
    "ok": False,
    "error": "This is disabled when the dashboard is bound to a non-loopback address.",
}


def _a_study(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text("{}", encoding="utf-8")
    return folder


@pytest.fixture()
def runtime(tmp_path: Path) -> DashboardRuntime:
    run = _a_study(tmp_path / "run")
    return DashboardRuntime(workspace_root=run, exploration_root=tmp_path,
                            active_root=run)


@pytest.fixture()
def elsewhere(tmp_path: Path) -> Path:
    """Shaped like a study by the rule `is_study` applies -- an `analysis/`
    folder is enough -- and holding a file nobody meant to publish."""
    folder = tmp_path / "home" / "someone"
    (folder / "analysis").mkdir(parents=True)
    (folder / "secret.txt").write_text("not for the network\n", encoding="utf-8")
    return folder


@contextlib.contextmanager
def _serving(runtime: DashboardRuntime, *, allow_control: bool):
    # Bound to loopback either way: the flag is what the gate reads, and a
    # test should not open a port to the network to prove it.
    handler = make_handler(runtime.workspace_root, runtime=runtime,
                           allow_control=allow_control)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _ask(url: str, *, body: dict | None = None) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url, data=data, method="GET" if body is None else "POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.getcode(), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post_routes() -> set[str]:
    """Every path `_dispatch_post` compares against, read from its source.

    Read rather than listed here, so a route added to the server is a
    route this file exercises without anybody editing it.
    """
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    start = source.index("def _dispatch_post(")
    body = source[start:source.index("\n        def ", start)]
    routes = set(re.findall(r'path == "([^"]+)"', body))
    for group in re.findall(r"path in [({\[]([^)}\]]*)[)}\]]", body):
        routes |= set(re.findall(r'"([^"]+)"', group))
    routes |= {prefix + "anything"
               for prefix in re.findall(r'path\.startswith\("([^"]+)"\)', body)}
    return routes


def _files_under(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


class TestSwitchingTheServedFolderIsRefused:

    def test_a_study_shaped_folder_is_not_made_the_served_root(
        self, runtime, elsewhere
    ) -> None:
        before = runtime.active_root
        with _serving(runtime, allow_control=False) as url:
            status, body = _ask(url + "/api/explore/switch",
                                body={"folder": str(elsewhere)})
            served = _ask(url + "/artifacts/secret.txt")
            previewed = _ask(url + "/api/file-text?path=secret.txt")

        assert status == 403
        assert json.loads(body) == REFUSAL
        assert runtime.active_root == before
        assert runtime.data_root() == before
        assert served[0] == 404
        assert b"not for the network" not in served[1] + previewed[1]

    def test_the_answer_does_not_say_whether_a_path_exists(
        self, runtime, elsewhere, tmp_path
    ) -> None:
        """Two different refusals, one for a missing folder and one for a
        folder that is not a study, were a probe of the host's disk."""
        not_a_study = tmp_path / "plain"
        not_a_study.mkdir()
        with _serving(runtime, allow_control=False) as url:
            answers = {
                _ask(url + "/api/explore/switch", body={"folder": str(folder)})
                for folder in (elsewhere, not_a_study, tmp_path / "missing")
            }
        assert len(answers) == 1
        (status, body), = answers
        assert status == 403 and json.loads(body) == REFUSAL

    def test_on_loopback_it_still_switches(self, runtime, elsewhere) -> None:
        # The gate must not be a way of switching the feature off.
        with _serving(runtime, allow_control=True) as url:
            status, body = _ask(url + "/api/explore/switch",
                                body={"folder": str(elsewhere)})
        assert status == 200
        assert json.loads(body)["ok"] is True
        assert runtime.active_root == elsewhere.resolve()


class TestEveryPostRouteIsRefusedUnlessListed:

    def test_the_list_of_open_routes_is_config_alone(self) -> None:
        # Widening it is a decision, so it has to be a change to this test.
        assert POSTS_ANSWERED_BEYOND_LOOPBACK == frozenset({"/api/config"})

    def test_the_routes_were_found(self) -> None:
        # A parser that found nothing would pass everything below.
        routes = _post_routes()
        assert {"/api/config", "/api/explore/switch", "/api/run",
                "/api/agent/conversation/delete"} <= routes
        assert POSTS_ANSWERED_BEYOND_LOOPBACK <= routes

    def test_each_unlisted_route_refuses_and_changes_nothing(
        self, runtime, tmp_path
    ) -> None:
        unlisted = sorted(_post_routes() - POSTS_ANSWERED_BEYOND_LOOPBACK)
        before = _files_under(tmp_path)
        with _serving(runtime, allow_control=False) as url:
            answers = {path: _ask(url + path, body={}) for path in unlisted}

        wrong = {path: answer for path, answer in answers.items()
                 if answer[0] != 403 or json.loads(answer[1]) != REFUSAL}
        assert not wrong, f"answered beyond loopback: {wrong}"
        assert _files_under(tmp_path) == before
        assert runtime.active_root == tmp_path / "run"

    def test_a_route_nobody_has_written_yet_is_refused(self, runtime) -> None:
        """What a route added tomorrow gets before anyone thinks about it."""
        with _serving(runtime, allow_control=False) as url:
            status, body = _ask(url + "/api/not-written-yet", body={})
        assert status == 403 and json.loads(body) == REFUSAL

    def test_the_listed_routes_still_answer(self, runtime) -> None:
        with _serving(runtime, allow_control=False) as url:
            answers = {path: _ask(url + path, body={})
                       for path in POSTS_ANSWERED_BEYOND_LOOPBACK}
        assert all(status != 403 for status, _ in answers.values()), answers


class TestConversationsAreNotReadBeyondLoopback:

    @pytest.fixture()
    def said(self, runtime) -> str:
        from fastmdxplora.gui.agent_panel import write_conversation

        words = "simulate the protein in /home/someone/private.pdb"
        assert write_conversation(runtime, [{"role": "user", "text": words}])["ok"]
        return words

    @pytest.mark.parametrize("path", ["/api/agent/conversation",
                                      "/api/agent/conversations"])
    def test_off_loopback_they_are_refused(self, runtime, said, path) -> None:
        with _serving(runtime, allow_control=False) as url:
            status, body = _ask(url + path)
        assert status == 403
        assert json.loads(body) == REFUSAL
        assert said.encode() not in body

    @pytest.mark.parametrize("path", ["/api/agent/conversation",
                                      "/api/agent/conversations"])
    def test_on_loopback_they_are_served(self, runtime, said, path) -> None:
        with _serving(runtime, allow_control=True) as url:
            status, body = _ask(url + path)
        assert status == 200
        # The list gives titles, the conversation its entries: both carry
        # the start of what was said.
        assert said[:40].encode() in body
