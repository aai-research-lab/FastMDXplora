"""A GUI served to someone else answers only its proxy, inside one folder.

`fastmdx gui --hosted` is for a service that runs the GUI for other people
behind a proxy that signs them in. Four things change together: a request
without the proxy's secret is refused unread; `Host` and a POST's `Origin`
must be a listed name; every path a request names is read inside the
workspace, and one outside it is refused; and no answer shows a path on the
server, only ``~/...``. Each is checked here against a real server.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.hosting import (
    SECRET_ENV,
    SECRET_HEADER,
    Hosting,
    HostingError,
)
from fastmdxplora.gui.server import make_handler

SECRET = "s" * 40
NAME = "app.example.org"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "person"
    study = folder / "first-study"
    (study / "analysis").mkdir(parents=True)
    (study / "manifest.json").write_text(
        json.dumps({"output": str(study)}), encoding="utf-8")
    (study / "notes.md").write_text(f"Ran in {study}\n", encoding="utf-8")
    (folder / "study.yml").write_text("systems:\n  - system: 1UBQ\n", encoding="utf-8")
    return folder


@pytest.fixture()
def outside(tmp_path: Path) -> Path:
    folder = tmp_path / "someone-else"
    (folder / "analysis").mkdir(parents=True)
    (folder / "manifest.json").write_text("{}", encoding="utf-8")
    (folder / "secret.yml").write_text("systems:\n  - system: 1AKE\n", encoding="utf-8")
    return folder


@pytest.fixture()
def hosting(workspace: Path) -> Hosting:
    return Hosting(workspace=workspace.resolve(), allowed_hosts=frozenset({NAME}),
                   secret=SECRET)


@contextlib.contextmanager
def _serving(hosting: Hosting):
    runtime = DashboardRuntime(workspace_root=hosting.workspace,
                               exploration_root=hosting.workspace)
    runtime.hosting = hosting
    handler = make_handler(hosting.workspace, runtime=runtime,
                           allow_control=False, hosting=hosting)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"127.0.0.1:{httpd.server_address[1]}", runtime
    finally:
        httpd.shutdown()
        httpd.server_close()


def _ask(address: str, path: str, *, body: dict | None = None,
         secret: str | None = SECRET, headers: dict | None = None,
         ) -> tuple[int, bytes]:
    sent = dict(headers or {})
    if secret is not None:
        sent[SECRET_HEADER] = secret
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://{address}{path}", data=data,
        method="GET" if body is None else "POST", headers=sent)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.getcode(), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(raw: bytes) -> dict:
    return json.loads(raw or b"{}")


class TestOnlyTheProxyIsAnswered:

    @pytest.mark.parametrize("secret", [None, "", "wrong", SECRET[:-1], SECRET + "x"])
    @pytest.mark.parametrize("route", ["/", "/api/app-state", "/api/browse", "/static/dashboard.js"])
    def test_a_get_without_the_secret_gets_nothing(self, hosting, secret, route) -> None:
        with _serving(hosting) as (address, _):
            status, body = _ask(address, route, secret=secret)
        assert status == 403
        assert body == b""

    def test_a_post_without_the_secret_changes_nothing(self, hosting, workspace) -> None:
        with _serving(hosting) as (address, runtime):
            status, body = _ask(address, "/api/explore/switch",
                                body={"folder": str(workspace / "first-study")},
                                secret=None)
            assert status == 403 and body == b""
            assert runtime.active_root is None

    def test_with_the_secret_everything_the_person_uses_is_answered(self, hosting, workspace) -> None:
        with _serving(hosting) as (address, runtime):
            assert _ask(address, "/")[0] == 200
            status, body = _ask(address, "/api/explore/switch",
                                body={"folder": "~/first-study"})
            assert status == 200 and _json(body)["ok"] is True
            assert runtime.active_root == (workspace / "first-study").resolve()


class TestOnlyTheListedNames:

    def test_the_public_name_and_loopback_are_answered(self, hosting) -> None:
        with _serving(hosting) as (address, _):
            assert _ask(address, "/api/app-state", headers={"Host": NAME})[0] == 200
            assert _ask(address, "/api/app-state", headers={"Host": f"{NAME}:443"})[0] == 200
            assert _ask(address, "/api/app-state")[0] == 200  # 127.0.0.1:port

    def test_another_name_is_refused(self, hosting) -> None:
        with _serving(hosting) as (address, _):
            status, body = _ask(address, "/api/app-state",
                                headers={"Host": "evil.example"})
        assert status == 403
        assert _json(body)["error"] == "This server does not answer to that name."

    @pytest.mark.parametrize("origin", [f"https://{NAME}", f"http://{NAME}",
                                        f"https://{NAME}:8443"])
    def test_a_post_from_its_own_page_is_answered(self, hosting, origin) -> None:
        with _serving(hosting) as (address, _):
            status, _ = _ask(address, "/api/explore/switch",
                             body={"folder": "~/first-study"},
                             headers={"Host": NAME, "Origin": origin})
        assert status == 200

    @pytest.mark.parametrize("origin", ["https://evil.example", "null",
                                        f"https://{NAME}.evil.example",
                                        f"ftp://{NAME}"])
    def test_a_post_from_another_page_is_refused(self, hosting, origin) -> None:
        with _serving(hosting) as (address, runtime):
            status, body = _ask(address, "/api/explore/switch",
                                body={"folder": "~/first-study"},
                                headers={"Host": NAME, "Origin": origin})
            assert status == 403
            assert _json(body)["error"] == "A request from another site is refused."
            assert runtime.active_root is None


class TestOneFolderIsTheWholeWorld:

    def test_the_browser_starts_at_the_workspace_and_cannot_go_above_it(self, hosting) -> None:
        with _serving(hosting) as (address, _):
            listing = _json(_ask(address, "/api/browse")[1])
        assert listing["ok"] is True
        assert listing["path"] == "~"
        assert listing["parent"] is None
        assert listing["home"] == "~"
        assert [e["path"] for e in listing["entries"]] == ["~/first-study"]

    def test_a_folder_inside_is_listed_with_the_workspace_as_its_parent(self, hosting) -> None:
        with _serving(hosting) as (address, _):
            listing = _json(_ask(address, "/api/browse?path=~/first-study")[1])
        assert listing["path"] == "~/first-study"
        assert listing["parent"] == "~"

    @pytest.mark.parametrize("where", ["/", "/etc", "..", "~/..", "~/../someone-else"])
    def test_a_folder_outside_is_refused(self, hosting, where) -> None:
        with _serving(hosting) as (address, _):
            status, body = _ask(address, f"/api/browse?path={where}")
        assert status == 403
        assert _json(body) == {"ok": False, "error": "That is outside your workspace."}

    def test_a_link_out_of_the_workspace_is_refused(self, hosting, workspace, outside) -> None:
        (workspace / "shortcut").symlink_to(outside, target_is_directory=True)
        with _serving(hosting) as (address, runtime):
            assert _ask(address, "/api/browse?path=~/shortcut")[0] == 403
            status, _ = _ask(address, "/api/explore/switch", body={"folder": "~/shortcut"})
            assert status == 403
            assert runtime.active_root is None

    def test_a_link_out_is_not_offered_by_the_browser(self, hosting, workspace, outside) -> None:
        """Opening it was refused, and listing it read it: what it held,
        counted, and a linked file's size, both outside."""
        (workspace / "shortcut").symlink_to(outside, target_is_directory=True)
        (workspace / "their.yml").symlink_to(outside / "secret.yml")
        (workspace / "own.yml").symlink_to(workspace / "study.yml")
        (outside / "theirs.pdb").write_text("END\n", encoding="utf-8")
        (workspace / "first-study" / "theirs").symlink_to(outside, target_is_directory=True)
        # One that leads nowhere, round to itself: not followed, nor offered.
        (workspace / "first-study" / "loop").symlink_to(workspace / "first-study" / "loop")
        with _serving(hosting) as (address, _):
            listing = _json(_ask(address, "/api/browse")[1])
        assert [e["path"] for e in listing["entries"]] == ["~/first-study"]
        assert [f["path"] for f in listing["files"]] == ["~/own.yml", "~/study.yml"]
        # Nor counted from a folder inside that holds such a link.
        assert listing["entries"][0]["structures"] == 0
        assert listing["structures"] == 0

    @pytest.mark.parametrize("route,field", [
        ("/api/explore/switch", "folder"),
        ("/api/load-config", "path"),
        ("/api/check-config", "path"),
        ("/api/run-config", "path"),
        ("/api/agent/attachment", "path"),
    ])
    def test_every_route_that_takes_a_path_refuses_one_outside(
        self, hosting, outside, route, field
    ) -> None:
        target = outside / ("secret.yml" if field == "path" else "")
        with _serving(hosting) as (address, runtime):
            status, body = _ask(address, route, body={field: str(target)})
            assert status == 403, route
            assert _json(body)["error"] == "That is outside your workspace."
            assert runtime.process is None

    def test_a_config_inside_is_read(self, hosting) -> None:
        with _serving(hosting) as (address, _):
            status, body = _ask(address, "/api/check-config", body={"path": "~/study.yml"})
        assert status == 200
        assert "outside your workspace" not in body.decode()

    def test_a_run_cannot_be_written_outside(self, hosting, outside) -> None:
        with _serving(hosting) as (address, runtime):
            status, body = _ask(address, "/api/run-config",
                                body={"path": "~/study.yml",
                                      "output": str(outside / "new")})
            assert _json(body) == {"ok": False,
                                   "error": "The output folder must be inside your workspace."}
            assert runtime.process is None
        assert not (outside / "new").exists()

    def test_the_folder_on_the_server_is_not_opened(self, hosting) -> None:
        with _serving(hosting) as (address, _):
            answer = _json(_ask(address, "/api/open-output")[1])
        assert answer["opened"] is False
        assert answer["detail"] == "Not available in a hosted GUI."


class TestNoPathOnTheServerIsShown:

    @pytest.mark.parametrize("route", ["/api/app-state", "/api/schema", "/api/browse",
                                       "/api/browse?path=~/first-study"])
    def test_answers_carry_no_server_path(self, hosting, workspace, route) -> None:
        with _serving(hosting) as (address, _):
            _ask(address, "/api/explore/switch", body={"folder": "~/first-study"})
            status, body = _ask(address, route)
        text = body.decode()
        assert status == 200
        for spelling in {str(workspace), str(workspace.resolve()), sys.executable}:
            assert spelling not in text, route

    def test_a_sentence_naming_the_workspace_itself_shows_it_as_home(
        self, hosting, workspace
    ) -> None:
        """Only a path *in* the workspace was rewritten: "That is a folder,
        not a config: /srv/person" showed the server's layout whole."""
        with _serving(hosting) as (address, _):
            answer = _json(_ask(address, "/api/check-config", body={"path": "~"})[1])
        assert answer["error"].endswith(": ~"), answer
        assert str(workspace.resolve()) not in json.dumps(answer)

    def test_a_folder_beside_the_workspace_is_not_read_as_in_it(self, hosting) -> None:
        root = str(hosting.workspace)
        assert hosting.scrub(f"{root} is not a study.") == "~ is not a study."
        assert hosting.scrub(f"\u201c{root}\u201d") == "\u201c~\u201d"
        assert hosting.scrub(f"see {root}.") == "see ~."
        assert hosting.scrub({f"{root}/a": 1}) == {"~/a": 1}
        for beside in (f"{root}2/x", f"{root}.yml", f"{root}-old"):
            assert hosting.scrub(beside) == beside

    @pytest.mark.parametrize("after, rewritten", [
        ("", True), ("/", True), ("/x", True), (" is not a study.", True), (".", True),
        (": no such file", True), (" copy", True), ("`", True), ("\nnext/line", True),
        (" copy/x", False), ("-old", False), (".bak", False), ("2", False), ("(1)", False),
        ("\u201d", True), ("\u2019s", True), ("\u00bb", True), ("\u2026", True),
        ("\u3002", True), ("|x", True), ("&x", True), (" (1)", True), (" copy 2", True),
        (" (1)/x", False), (" copy 2/x", False), (" - Copy/x", False),
    ])
    def test_a_path_ends_where_no_folder_name_goes_on(self, hosting, after, rewritten) -> None:
        """`/srv/ws copy/x` was rewritten as `~ copy/x`: a space let the
        workspace's name go on into a folder beside it."""
        root = str(hosting.workspace)
        assert hosting.scrub(root + after) == ("~" + after if rewritten else root + after)

    def test_a_folder_whose_path_ends_with_the_workspace_is_not_in_it(self, hosting) -> None:
        root = str(hosting.workspace)
        for before in ("/other", "x", "a-", "~"):
            assert hosting.scrub(f"{before}{root}/x") == f"{before}{root}/x"
        for before in (" ", "`", "(", "=", "'"):
            assert hosting.scrub(f"see{before}{root}/x") == f"see{before}~/x"

    def test_the_agent_s_reply_as_it_is_written_shows_no_path(
        self, hosting, workspace, monkeypatch
    ) -> None:
        """`/api/agent/propose` was scrubbed as every answer is; the same
        answer streamed a line at a time was written as it came."""
        from fastmdxplora.gui import agent_panel

        study = str(workspace.resolve() / "first-study")

        def propose(payload, runtime, *, emit=None, **_):
            emit({"type": "look", "text": f"Read {study}/notes.md"})
            return {"ok": True, "config_path": f"{study}/run.yml"}

        monkeypatch.setattr(agent_panel, "propose_endpoint", propose)
        with _serving(hosting) as (address, _):
            status, body = _ask(address, "/api/agent/propose-stream",
                                body={"request": "x"},
                                headers={"Origin": f"https://{NAME}"})
        events = [json.loads(line) for line in body.decode().splitlines()]
        assert status == 200
        assert events[0]["text"] == "Read ~/first-study/notes.md"
        assert events[-1]["answer"]["config_path"] == "~/first-study/run.yml"

    def test_a_path_split_across_the_reply_s_pieces_shows_no_path(
        self, hosting, workspace, monkeypatch
    ) -> None:
        """The streamed reply was scrubbed a piece at a time, so a path split
        across two pieces went out whole."""
        from fastmdxplora.gui import agent_panel

        root = str(workspace.resolve())
        cut_in_the_path = len(root) + len("/first-st")
        cut_in_the_workspace = len(root) // 2
        reply = f"{root}/first-study/notes.md and {root} copy/x, then {root}."

        def propose(payload, runtime, *, emit=None, **_):
            emit({"type": "begin"})
            for start, end in ((0, 5), (5, cut_in_the_workspace + 5),
                               (cut_in_the_workspace + 5, cut_in_the_path + 5),
                               (cut_in_the_path + 5, None)):
                emit({"type": "text", "text": ("Read " + reply)[start:end]})
            return {"ok": True, "reply": "Read " + reply}

        monkeypatch.setattr(agent_panel, "propose_endpoint", propose)
        with _serving(hosting) as (address, _):
            status, body = _ask(address, "/api/agent/propose-stream",
                                body={"request": "x"},
                                headers={"Origin": f"https://{NAME}"})
        events = [json.loads(line) for line in body.decode().splitlines()]
        assert status == 200
        assert [e["type"] for e in events][0] == "begin"
        assert events[-1]["type"] == "done"
        written = "".join(e["text"] for e in events if e["type"] == "text")
        assert written == f"Read ~/first-study/notes.md and {root} copy/x, then ~."
        assert written == events[-1]["answer"]["reply"]
        for event in events[:-1]:
            assert root + "/" not in event.get("text", "")

    def test_the_reply_scrubbed_in_pieces_is_the_reply_scrubbed_whole(self, hosting) -> None:
        root = str(hosting.workspace)
        reply = (f"Read {root}/a.md\n{root} is a folder; {root} copy/x is not, "
                 f"nor /other{root}/b, `{root}`. Done")
        whole = hosting.scrub(reply)
        for cut in range(1, len(reply)):
            for second in (cut + 1, cut + len(root) // 2, cut + len(root) + 3):
                streamed = hosting.scrubbed_as_it_goes()
                said = (streamed.add(reply[:cut]) + streamed.add(reply[cut:second])
                        + streamed.add(reply[second:]) + streamed.flush())
                assert said == whole, (cut, second)

    def test_the_interpreter_after_the_workspace_is_judged_as_rewritten(self, hosting) -> None:
        """The word after the held path was judged as written, though the
        whole scrub first writes the interpreter as `python`: the workspace
        went out as it is, and nothing after it."""
        root = str(hosting.workspace)
        reply = f"Open {root} v{sys.executable} then more words here."
        streamed = hosting.scrubbed_as_it_goes()
        said = "".join(streamed.add(c) for c in reply) + streamed.flush()
        assert said == hosting.scrub(reply) == "Open ~ vpython then more words here."

    def test_any_split_says_what_the_whole_scrub_says(self, tmp_path, workspace) -> None:
        import random

        link = tmp_path / "linked"
        link.symlink_to(workspace)
        hosting = Hosting(workspace=link, allowed_hosts=frozenset({NAME}), secret=SECRET)
        spellings = hosting._prefixes()
        assert len(spellings) == 2
        tokens = [sys.executable, *spellings, *(s + "/a.md" for s in spellings),
                  *(s + " copy/x" for s in spellings), " (1)/x", " copy 2/x", " - Copy/x",
                  "\u201d", "\u2026", "v", "x/y", "copy/x", " ", " ",
                  "\n", ".", ",", "(1)", "\u201d", "then", "+", "`", "-"]
        rng = random.Random(7)
        for _ in range(1500):
            reply = "".join(rng.choice(tokens) for _ in range(rng.randint(1, 12)))
            whole = hosting.scrub(reply)
            cuts = sorted(rng.sample(range(1, len(reply)), min(len(reply) - 1, rng.randint(0, 6))))
            streamed = hosting.scrubbed_as_it_goes()
            said, start = "", 0
            for end in [*cuts, len(reply)]:
                said += streamed.add(reply[start:end])
                start = end
                assert whole.startswith(said), (reply, cuts)
                if not any(s in whole for s in spellings):
                    assert not any(s in said for s in spellings), (reply, cuts)
            said += streamed.flush()
            assert not streamed.replaces
            assert said == whole, (reply, cuts)

    def test_a_scrub_that_stops_extending_what_was_sent_sends_it_again_whole(
        self, hosting, monkeypatch
    ) -> None:
        streamed = hosting.scrubbed_as_it_goes()
        assert streamed.add("First words then ") == "First words "
        rewritten = Hosting._scrub_text
        monkeypatch.setattr(Hosting, "_scrub_text",
                            lambda self, text: rewritten(self, text).replace("First", "1st"))
        assert streamed.add("more and more ") == ""
        assert streamed.add("words ") == ""
        assert streamed.flush() == "1st words then more and more words "
        assert streamed.replaces
        assert streamed.add("Next ") == "" and not streamed.replaces

    def test_the_state_names_the_workspace_as_home(self, hosting) -> None:
        with _serving(hosting) as (address, _):
            _ask(address, "/api/explore/switch", body={"folder": "~/first-study"})
            state = _json(_ask(address, "/api/app-state")[1])
        assert state["workspace"] == "~"
        assert state["active_run"] == "~/first-study"

    def test_a_file_is_shown_as_it_is_on_disk(self, hosting, workspace) -> None:
        """Only the answer's own fields are rewritten; a file's text is the
        person's data and is shown exactly as written."""
        with _serving(hosting) as (address, _):
            _ask(address, "/api/explore/switch", body={"folder": "~/first-study"})
            answer = _json(_ask(address, "/api/file-text?path=notes.md")[1])
        assert answer["text"] == f"Ran in {workspace / 'first-study'}\n"


class TestItRefusesToStartOpen:

    def test_no_secret(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv(SECRET_ENV, raising=False)
        with pytest.raises(HostingError, match=SECRET_ENV):
            Hosting.from_environment(tmp_path, [NAME])

    def test_a_short_secret(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(SECRET_ENV, "short")
        with pytest.raises(HostingError, match="at least"):
            Hosting.from_environment(tmp_path, [NAME])

    def test_no_name(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(SECRET_ENV, SECRET)
        with pytest.raises(HostingError, match="--allowed-host"):
            Hosting.from_environment(tmp_path, [])

    def test_the_whole_file_system_as_workspace(self, monkeypatch) -> None:
        monkeypatch.setenv(SECRET_ENV, SECRET)
        with pytest.raises(HostingError, match="top of the file system"):
            Hosting.from_environment("/", [NAME])

    def test_the_secret_is_not_left_for_the_runs_it_starts(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(SECRET_ENV, SECRET)
        hosting = Hosting.from_environment(tmp_path, [f"{NAME}:443", " "])
        assert SECRET_ENV not in os.environ
        assert hosting.allowed_hosts == frozenset({NAME})
        assert hosting.admits(SECRET) and not hosting.admits(None)

    def test_the_command_line_refuses_hosted_without_a_secret(self, tmp_path) -> None:
        env = {k: v for k, v in os.environ.items() if k != SECRET_ENV}
        done = subprocess.run(
            [sys.executable, "-m", "fastmdxplora.cli.main", "gui", "--hosted",
             "--workspace", str(tmp_path), "--allowed-host", NAME],
            capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 2
        assert SECRET_ENV in done.stderr

    def test_hosted_flags_alone_do_not_serve(self, tmp_path) -> None:
        done = subprocess.run(
            [sys.executable, "-m", "fastmdxplora.cli.main", "gui",
             "--workspace", str(tmp_path)],
            capture_output=True, text=True, timeout=120)
        assert done.returncode == 2
        assert "only with --hosted" in done.stderr
