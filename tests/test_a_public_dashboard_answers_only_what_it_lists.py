"""Bound beyond loopback, the dashboard answers only the routes it lists.

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

The GETs were still a list of routes to refuse, and the conversations were
kept in the study, so `/artifacts/` and `/api/file-text` served in full what
the conversation routes refused. With no run open, `/api/file-text` read
under the folder `fastmdx gui` was typed in: a home folder, holding the API
key the agent stores. The GETs are now listed like the POSTs, and a file is
served beyond loopback only from the run being watched, only from a folder
FastMDXplora wrote, and never a hidden one or the agent's conversations.
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
from fastmdxplora.gui.server import (
    GET_PREFIXES_ANSWERED_BEYOND_LOOPBACK,
    GETS_ANSWERED_BEYOND_LOOPBACK,
    POSTS_ANSWERED_BEYOND_LOOPBACK,
    make_handler,
)

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


def _routes(handler: str) -> set[str]:
    """Every path a dispatcher compares against, read from its source.

    Read rather than listed here, so a route added to the server is a
    route this file exercises without anybody editing it.
    """
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    start = source.index(f"def {handler}(self)")
    body = source[start:source.index("\n        def ", start)]
    routes = set(re.findall(r'path == "([^"]+)"', body))
    for group in re.findall(r"path in [({\[]([^)}\]]*)[)}\]]", body):
        routes |= set(re.findall(r'"([^"]+)"', group))
    routes |= {prefix + "anything"
               for prefix in re.findall(r'path\.startswith\("([^"]+)"\)', body)}
    return routes


def _post_routes() -> set[str]:
    return _routes("_dispatch_post")


def _get_routes() -> set[str]:
    return _routes("_dispatch")


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


class TestEveryGetRouteIsRefusedUnlessListed:

    def test_the_list_is_what_watching_a_run_needs(self) -> None:
        # Widening it is a decision, so it has to be a change to this test.
        assert GETS_ANSWERED_BEYOND_LOOPBACK == frozenset({
            "/", "/index", "/results", "/live",
            "/api/app-state", "/api/explore/state", "/api/schema",
            "/api/status", "/api/metrics", "/api/events", "/api/report",
            "/api/artifacts", "/api/files", "/api/results", "/api/analyses",
            "/api/file-text", "/api/protein-preview", "/api/structure-info",
            "/api/ligands", "/api/live-frame-index", "/api/live-coordinates",
            "/api/playback-info", "/api/series", "/analysis-figures-svg.zip",
            "/structure/topology.pdb", "/structure/live-frame.pdb",
            "/structure/playback.pdb",
        })
        assert GET_PREFIXES_ANSWERED_BEYOND_LOOPBACK == ("/static/", "/artifacts/")

    def test_the_routes_were_found(self) -> None:
        routes = _get_routes()
        assert {"/api/browse", "/api/inspect-directory", "/api/open-output",
                "/api/agent/conversation", "/api/file-text"} <= routes
        assert GETS_ANSWERED_BEYOND_LOOPBACK <= routes

    def test_each_unlisted_route_refuses(self, runtime, tmp_path) -> None:
        unlisted = sorted(
            path for path in _get_routes() - GETS_ANSWERED_BEYOND_LOOPBACK
            if not path.startswith(GET_PREFIXES_ANSWERED_BEYOND_LOOPBACK))
        assert unlisted
        with _serving(runtime, allow_control=False) as url:
            answers = {path: _ask(url + path + "?path=" + str(tmp_path))
                       for path in unlisted}
        wrong = {path: answer for path, answer in answers.items()
                 if answer[0] != 403 or json.loads(answer[1]) != REFUSAL}
        assert not wrong, f"answered beyond loopback: {wrong}"

    def test_a_route_nobody_has_written_yet_is_refused(self, runtime) -> None:
        with _serving(runtime, allow_control=False) as url:
            status, body = _ask(url + "/api/not-written-yet")
        assert status == 403 and json.loads(body) == REFUSAL

    def test_the_listed_routes_still_answer(self, runtime) -> None:
        with _serving(runtime, allow_control=False) as url:
            answers = {path: _ask(url + path)
                       for path in ("/", "/api/status", "/api/files",
                                    "/api/results", "/static/frame.js")}
        assert all(status == 200 for status, _ in answers.values()), answers


class TestOnlyTheRunsResultsLeaveTheMachine:
    """What `/artifacts/`, `/api/file-text` and the file list will give."""

    @pytest.fixture()
    def conversation(self, runtime) -> str:
        from fastmdxplora.gui.agent_panel import write_conversation

        answer = write_conversation(
            runtime, [{"role": "user", "text": "the contents of private.pdb"}])
        assert answer["ok"]
        stored = f"agent/conversations/{answer['id']}.json"
        assert (runtime.active_root / stored).is_file()
        return stored

    def test_a_conversation_is_neither_listed_nor_served(
        self, runtime, conversation
    ) -> None:
        with _serving(runtime, allow_control=False) as url:
            listed = _ask(url + "/api/files")[1] + _ask(url + "/api/results")[1]
            served = _ask(url + "/artifacts/" + conversation)
            previewed = _ask(url + "/api/file-text?path=" + conversation)
        assert b"agent/conversations" not in listed
        assert served[0] == 404
        assert json.loads(previewed[1])["ok"] is False
        assert b"private.pdb" not in served[1] + previewed[1]

    def test_on_loopback_it_is_not_listed_as_a_result(
        self, runtime, conversation
    ) -> None:
        # The Agent tab is where a conversation is read; the file list is
        # for what the run produced.
        with _serving(runtime, allow_control=True) as url:
            listed = _ask(url + "/api/files")[1]
        assert b"agent/conversations" not in listed

    def test_a_hidden_file_is_not_served(self, runtime) -> None:
        hidden = runtime.active_root / ".env"
        hidden.write_text("TOKEN=not-for-the-network\n", encoding="utf-8")
        with _serving(runtime, allow_control=False) as url:
            served = _ask(url + "/artifacts/.env")
            listed = _ask(url + "/api/files")[1]
        assert served[0] == 404
        assert b".env" not in listed and b"not-for-the-network" not in served[1]

    def test_a_link_out_of_the_run_is_neither_listed_nor_served(
        self, runtime, elsewhere
    ) -> None:
        link = runtime.active_root / "analysis"
        try:
            link.symlink_to(elsewhere, target_is_directory=True)
        except OSError:
            pytest.skip("this filesystem does not make symbolic links")
        with _serving(runtime, allow_control=False) as url:
            listed = _ask(url + "/api/files")[1]
            served = _ask(url + "/artifacts/analysis/../analysis/secret.txt")
            direct = _ask(url + "/artifacts/analysis/secret.txt")
        assert b"secret.txt" not in listed
        assert b"not for the network" not in served[1] + direct[1]

    def test_the_runs_own_files_are_still_served(self, runtime) -> None:
        figure = runtime.active_root / "analysis" / "rmsd" / "rmsd.dat"
        figure.parent.mkdir(parents=True)
        figure.write_text("0 0.1\n", encoding="utf-8")
        with _serving(runtime, allow_control=False) as url:
            served = _ask(url + "/artifacts/analysis/rmsd/rmsd.dat")
            previewed = _ask(url + "/api/file-text?path=analysis/rmsd/rmsd.dat")
            listed = _ask(url + "/api/files")[1]
        assert served == (200, b"0 0.1\n")
        assert json.loads(previewed[1])["text"] == "0 0.1\n"
        assert b"analysis/rmsd/rmsd.dat" in listed


class TestAFolderThatIsNotARunIsNotServed:

    @pytest.fixture()
    def home(self, tmp_path: Path) -> Path:
        """Where `fastmdx gui` is typed, holding the key the agent stores."""
        folder = tmp_path / "home" / "someone"
        key = folder / ".config" / "fastmdxplora" / "model.json"
        key.parent.mkdir(parents=True)
        key.write_text('{"api_key": "sk-not-for-the-network"}', encoding="utf-8")
        (folder / "notes.txt").write_text("not for the network\n", encoding="utf-8")
        return folder

    @pytest.mark.parametrize("allow_control", [False, True])
    def test_with_no_run_open_the_workspace_is_not_read(
        self, home, allow_control
    ) -> None:
        runtime = DashboardRuntime(workspace_root=home, exploration_root=home,
                                   active_root=None)
        with _serving(runtime, allow_control=allow_control) as url:
            answers = [_ask(url + "/api/file-text?path=" + name)
                       for name in (".config/fastmdxplora/model.json", "notes.txt",
                                    str(home / "notes.txt"))]
            answers.append(_ask(url + "/artifacts/notes.txt"))
        for _, body in answers:
            assert b"not-for-the-network" not in body
            assert b"not for the network" not in body

    def test_a_folder_named_by_output_is_served_as_no_run(self, home) -> None:
        # `fastmdx gui --output ~ --host 0.0.0.0`.
        runtime = DashboardRuntime(workspace_root=home, exploration_root=home.parent,
                                   active_root=home)
        with _serving(runtime, allow_control=False) as url:
            listed = _ask(url + "/api/files")
            served = _ask(url + "/artifacts/notes.txt")
            previewed = _ask(url + "/api/file-text?path=notes.txt")
        assert json.loads(listed[1]) == {"artifacts": []}
        assert served[0] == 404
        assert b"not for the network" not in served[1] + previewed[1]


class TestAViewerDoesNotSetWorkGoing:

    def test_regenerate_and_force_are_for_loopback(self, runtime, monkeypatch) -> None:
        asked: dict[str, list] = {"preview": [], "playback": []}

        def preview(root, *, regenerate=False):
            asked["preview"].append(regenerate)
            return {}

        def playback(root, *, max_browser_frames, force=False, **_):
            asked["playback"].append((max_browser_frames, force))
            return {}

        monkeypatch.setattr(dashboard, "protein_preview_payload", preview)
        monkeypatch.setattr(dashboard, "playback_info", playback)
        for allow_control in (False, True):
            with _serving(runtime, allow_control=allow_control) as url:
                _ask(url + "/api/protein-preview?regenerate=1")
                _ask(url + "/api/playback-info?max=100000&force=1")
        default = dashboard.DashboardConfig().max_browser_frames
        assert asked == {"preview": [False, True],
                         "playback": [(default, False), (100000, True)]}
