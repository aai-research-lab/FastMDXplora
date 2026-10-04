"""Everyone a pull request's commits credit signs the contributor agreement.

CLA Assistant Lite asks each commit's author to sign and reads nothing else,
so pull request #52, which ported another contributor's work from #51 with a
`Co-authored-by:` line for him, passed with the porter's signature alone.
`scripts/cla_co_authors.py` reads the commit messages and asks the people
credited there, as the action asks the authors. These tests drive it against
a stand-in for GitHub's REST API that answers in the API's own shapes.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "cla_co_authors.py"
SENTENCE = "I have read the FastMDXplora Contributor License Agreement and I hereby sign it"
REPO = "lab/project"

spec = importlib.util.spec_from_file_location("cla_co_authors", SCRIPT)
cla = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cla
spec.loader.exec_module(cla)

PORTER = {"login": "porter", "id": 101}
ORIGINAL = {"login": "original-author", "id": 202}
PORTER_EMAIL = "101+porter@users.noreply.github.com"
ORIGINAL_EMAIL = "original@example.org"


def _commit(sha, message, *, email, user, name="Someone"):
    """One item of `GET /repos/{repo}/pulls/{n}/commits`."""
    person = {"name": name, "email": email, "date": "2026-10-04T20:00:00Z"}
    return {"sha": sha, "commit": {"message": message, "author": person, "committer": person},
            "author": user, "committer": user}


#: The two commit messages of #52 as GitHub stored them: one with its
#: trailer, one with the trailer after a literal backslash-n.
PORTED = [
    _commit("ea07194" + "0" * 33,
            "fix(viewer): label DCD frames by recorded sample time\n\n"
            f"Co-authored-by: Original Author <{ORIGINAL_EMAIL}>",
            email=PORTER_EMAIL, user=PORTER, name="Porter"),
    _commit("4b9e4db" + "0" * 33,
            "feat(viewer): annotate saved views\n\nPorted selectively from closed PR #51 "
            "commit 5aa441f, adapted to current Mol* saved views.\\n\\n"
            f"Co-authored-by: Original Author <{ORIGINAL_EMAIL}>",
            email=PORTER_EMAIL, user=PORTER, name="Porter"),
]


class GitHub:
    """Answers the calls the script makes, and keeps what it was sent."""

    def __init__(self, commits, *, other_pulls=None, signed=None, comments=None,
                 state="open", users=None, repo_commits=None, public_emails=None):
        self.commits = commits
        self.other_pulls = other_pulls or {}
        self.signed = signed
        self.comments = list(comments or [])
        self.state = state
        self.users = users or {}
        self.repo_commits = repo_commits or {}
        self.public_emails = public_emails or {}
        self.statuses = []
        self.posted = []
        self.patched = []
        self.saved = []

    def _signatures(self):
        if self.signed is None:
            return 404, {"message": "Not Found"}
        raw = json.dumps({"signedContributors": self.signed}).encode("utf-8")
        return 200, {"content": base64.b64encode(raw).decode("ascii"),
                     "sha": f"sha-{len(self.saved)}"}

    def __call__(self, method, path, body=None):
        bare = path.split("?")[0]
        found = re.search(r"[?&]page=(\d+)", path)
        page = int(found.group(1)) if found else 1
        if method == "GET" and bare == f"repos/{REPO}/pulls/52":
            return 200, {"state": self.state, "head": {"sha": "head-sha"},
                         "base": {"repo": {"id": 555}}}
        if method == "GET" and bare == f"repos/{REPO}/pulls/52/commits":
            return 200, self.commits if page == 1 else []
        if method == "GET" and bare == f"repos/{REPO}/pulls":
            return 200, [{"number": 52}] + [{"number": n} for n in self.other_pulls]
        if method == "GET" and bare.startswith(f"repos/{REPO}/pulls/") and bare.endswith("/commits"):
            number = int(bare.split("/")[-2])
            return 200, self.other_pulls.get(number, []) if page == 1 else []
        if method == "GET" and bare.startswith(f"repos/{REPO}/commits/"):
            sha = bare.rsplit("/", 1)[1]
            found = self.repo_commits.get(sha)
            return (200, found) if found else (422, {"message": "No commit found"})
        if method == "GET" and bare.startswith("users/"):
            login = bare.split("/", 1)[1]
            user = self.users.get(login)
            return (200, user) if user else (404, {"message": "Not Found"})
        if method == "GET" and bare == "search/users":
            email = path.split("q=")[1].split("+")[0].replace("%40", "@")
            user = self.public_emails.get(email)
            return 200, {"total_count": int(bool(user)), "items": [user] if user else []}
        if bare == f"repos/{REPO}/contents/{cla.SIGNATURES}":
            if method == "GET":
                return self._signatures()
            assert body["branch"] == cla.SIGNATURES_BRANCH
            self.signed = json.loads(base64.b64decode(body["content"]))["signedContributors"]
            self.saved.append(body)
            return 200, {}
        if method == "GET" and bare == f"repos/{REPO}/issues/52/comments":
            return 200, self.comments if page == 1 else []
        if method == "POST" and bare == f"repos/{REPO}/statuses/head-sha":
            self.statuses.append(body)
            return 201, {}
        if method == "POST" and bare == f"repos/{REPO}/issues/52/comments":
            comment = {"id": 900 + len(self.posted), "body": body["body"],
                       "user": {"login": "github-actions[bot]", "id": 41898282}}
            self.posted.append(comment)
            self.comments.append(comment)
            return 201, comment
        if method == "PATCH" and bare.startswith(f"repos/{REPO}/issues/comments/"):
            number = int(bare.rsplit("/", 1)[1])
            self.patched.append((number, body["body"]))
            for comment in self.comments:
                if comment.get("id") == number:
                    comment["body"] = body["body"]
            return 200, {}
        raise AssertionError(f"unexpected call {method} {path}")


def _check(github, allowlist="bot*"):
    return cla.check(github, REPO, 52, SENTENCE, allowlist)


def _with_the_original_pull_request(**kwargs):
    """#51's commits, by the original author, are where GitHub links his
    address to his account."""
    own = _commit("5aa441f" + "0" * 33, "Add portable research bookmarks",
                  email=ORIGINAL_EMAIL, user=ORIGINAL, name="Original Author")
    return GitHub(PORTED, other_pulls={51: [own]}, **kwargs)


def test_a_co_author_line_is_read_as_written_and_as_mistyped():
    assert cla.trailers(PORTED[0]["commit"]["message"]) == [("Original Author", ORIGINAL_EMAIL)]
    assert cla.trailers(PORTED[1]["commit"]["message"]) == [("Original Author", ORIGINAL_EMAIL)]
    assert cla.trailers("x\n\nco-authored-by: A B <a@b.org>\nCo-Authored-By: C <c@d.org>") == [
        ("A B", "a@b.org"), ("C", "c@d.org")]
    assert cla.trailers("Co-authored-by: nobody") == []


def test_a_cherry_picked_commit_names_its_source():
    message = "Fix it\n\n(cherry picked from commit 0123456789abcdef0123456789abcdef01234567)"
    assert cla.picked_from(message) == ["0123456789abcdef0123456789abcdef01234567"]
    assert cla.noreply_login(PORTER_EMAIL) == "porter"
    assert cla.noreply_login("porter@users.noreply.github.com") == "porter"
    assert cla.noreply_login(ORIGINAL_EMAIL) is None


def test_a_ported_pull_request_waits_for_its_co_author():
    github = _with_the_original_pull_request(signed=[{"name": "porter", "id": 101}])
    assert _check(github) == 1
    assert github.statuses == [{
        "state": "failure", "context": cla.CONTEXT,
        "description": "1 co-author still to sign the agreement.",
        "target_url": f"https://github.com/{REPO}/blob/main/CLA.md"}]
    [comment] = github.posted
    assert comment["body"].startswith(cla.MARKER)
    assert "- @original-author, co-author of `ea07194`, `4b9e4db`" in comment["body"]
    assert f"> {SENTENCE}" in comment["body"]
    assert github.saved == []


def test_the_co_author_signs_on_the_pull_request():
    github = _with_the_original_pull_request(signed=[{"name": "porter", "id": 101}])
    _check(github)
    github.comments.append({"id": 77, "body": f"  {SENTENCE}\n",
                            "created_at": "2026-10-04T21:00:00Z", "user": ORIGINAL})
    assert _check(github) == 0
    # In the action's own form, so its check knows him afterwards.
    assert github.signed[-1] == {"name": "original-author", "id": 202, "comment_id": 77,
                                 "created_at": "2026-10-04T21:00:00Z", "repoId": 555,
                                 "pullRequestNo": 52}
    assert github.statuses[-1]["state"] == "success"
    assert github.patched == [(900, f"{cla.MARKER}\nEvery co-author of this pull request has "
                                    "signed the Contributor License Agreement.")]
    # Checked again, nothing is recorded twice and the comment stands.
    assert _check(github) == 0
    assert len(github.saved) == 1 and len(github.patched) == 1


def test_another_persons_comment_does_not_sign_for_them():
    github = _with_the_original_pull_request(comments=[
        {"id": 78, "body": SENTENCE, "created_at": "t", "user": PORTER}])
    assert _check(github) == 1
    assert github.saved == []


def test_someone_who_signed_before_is_not_asked():
    github = _with_the_original_pull_request(signed=[{"name": "Original-Author", "id": 999}])
    assert _check(github) == 0
    assert github.statuses[-1]["state"] == "success"
    assert github.posted == [] and github.patched == []


def test_the_commits_own_authors_are_left_to_the_action():
    own = _commit("abc1234" + "0" * 33,
                  f"Pair work\n\nCo-authored-by: Porter <{PORTER_EMAIL}>",
                  email=ORIGINAL_EMAIL, user=ORIGINAL)
    github = GitHub(PORTED[:1] + [own], signed=[])
    # The original author made a commit here, so the action asks him; the
    # porter likewise. Nobody is left for this check.
    assert _check(github) == 0
    assert github.posted == []


def test_an_address_no_account_has_is_said_and_can_be_recorded_by_email():
    github = GitHub(PORTED, signed=[])
    assert _check(github) == 1
    [comment] = github.posted
    assert "- Original Author, co-author of `ea07194`, `4b9e4db`: no GitHub account" in comment["body"]
    assert ORIGINAL_EMAIL not in comment["body"]
    # The maintainer records a signed copy by the address.
    github.signed = [{"email": ORIGINAL_EMAIL.upper()}]
    assert _check(github) == 0


def test_an_address_public_on_an_account_finds_it():
    github = GitHub(PORTED, signed=[], public_emails={ORIGINAL_EMAIL: ORIGINAL})
    _check(github)
    assert "@original-author" in github.posted[0]["body"]


def test_an_address_github_gives_is_its_account():
    commit = _commit("abc1234" + "0" * 33,
                     "Fix\n\nCo-authored-by: Helper <303+helper@users.noreply.github.com>",
                     email=PORTER_EMAIL, user=PORTER)
    github = GitHub([commit], signed=[], users={"helper": {"login": "helper", "id": 303}})
    _check(github)
    assert "- @helper, co-author of `abc1234`" in github.posted[0]["body"]


def test_a_cherry_picked_commits_author_is_asked():
    source = "0123456789abcdef0123456789abcdef01234567"
    commit = _commit("abc1234" + "0" * 33, f"Fix\n\n(cherry picked from commit {source})",
                     email=PORTER_EMAIL, user=PORTER)
    picked = {"sha": source, "author": ORIGINAL,
              "commit": {"author": {"name": "Original Author", "email": ORIGINAL_EMAIL}}}
    github = GitHub([commit], signed=[], repo_commits={source: picked})
    assert _check(github) == 1
    assert "- @original-author, author of a commit picked into `abc1234`" in github.posted[0]["body"]
    unknown = GitHub([commit], signed=[])
    assert _check(unknown) == 1
    assert "this repository does not have that commit" in unknown.posted[0]["body"]


def test_bots_and_the_allowlist_are_not_asked():
    commit = _commit("abc1234" + "0" * 33,
                     "Fix\n\nCo-authored-by: bot <99+botty@users.noreply.github.com>\n"
                     "Co-authored-by: Dep <1+dependabot[bot]@users.noreply.github.com>",
                     email=PORTER_EMAIL, user=PORTER)
    github = GitHub([commit], signed=[], users={"botty": {"login": "botty", "id": 99}})
    assert _check(github) == 0


def test_a_name_from_a_message_cannot_write_into_the_comment():
    commit = _commit("abc1234" + "0" * 33,
                     "Fix\n\nCo-authored-by: [x](http://e.vil) @everyone `y` <z@example.org>",
                     email=PORTER_EMAIL, user=PORTER)
    github = GitHub([commit], signed=[])
    _check(github)
    [line] = [line for line in github.posted[0]["body"].splitlines() if "abc1234" in line]
    assert line.startswith("- xhttpe.vil everyone y, co-author of `abc1234`")
    assert "@" not in line and "](" not in line and "`y`" not in line


def test_a_closed_pull_request_is_left_alone():
    github = _with_the_original_pull_request(state="closed")
    assert _check(github) == 0
    assert github.statuses == [] and github.posted == []


@pytest.mark.parametrize("status", [409, 422])
def test_a_signature_saved_beside_another_is_tried_again(status):
    github = _with_the_original_pull_request(signed=[], comments=[
        {"id": 77, "body": SENTENCE, "created_at": "t", "user": ORIGINAL}])
    calls = {"n": 0}
    inner = github.__call__

    def clashing(method, path, body=None):
        if method == "PUT" and calls["n"] == 0:
            calls["n"] += 1
            return status, {"message": "sha does not match"}
        return inner(method, path, body)

    assert cla.check(clashing, REPO, 52, SENTENCE, "bot*") == 0
    assert [entry["id"] for entry in github.signed] == [202]


def test_a_signature_starts_the_file_where_there_is_none():
    github = _with_the_original_pull_request(signed=None, comments=[
        {"id": 77, "body": SENTENCE, "created_at": "t", "user": ORIGINAL}])
    assert _check(github) == 0
    assert "sha" not in github.saved[0]
    assert [entry["name"] for entry in github.signed] == ["original-author"]


def test_a_signature_that_cannot_be_saved_leaves_the_check_failed():
    github = _with_the_original_pull_request(signed=[], comments=[
        {"id": 77, "body": SENTENCE, "created_at": "t", "user": ORIGINAL}])
    inner = github.__call__

    def refusing(method, path, body=None):
        if method == "PUT":
            return 403, {"message": "Resource not accessible by integration"}
        return inner(method, path, body)

    assert cla.check(refusing, REPO, 52, SENTENCE, "bot*") == 1
    assert github.statuses[-1]["state"] == "failure"


def test_an_api_that_fails_fails_the_job_rather_than_passing_it():
    def broken(method, path, body=None):
        return 502, {"message": "Bad Gateway"}

    with pytest.raises(RuntimeError, match="answered 502"):
        cla.check(broken, REPO, 52, SENTENCE, "bot*")


def test_one_person_credited_twice_is_asked_once():
    source = "0123456789abcdef0123456789abcdef01234567"
    commit = _commit("abc1234" + "0" * 33,
                     f"Fix\n\n(cherry picked from commit {source})\n\n"
                     f"Co-authored-by: Original Author <{ORIGINAL_EMAIL}>",
                     email=PORTER_EMAIL, user=PORTER)
    picked = {"sha": source, "author": ORIGINAL,
              "commit": {"author": {"name": "Original Author", "email": ORIGINAL_EMAIL}}}
    other = _commit("def5678" + "0" * 33, "x", email=ORIGINAL_EMAIL, user=ORIGINAL)
    github = GitHub([commit], signed=["not an entry"], repo_commits={source: picked},
                    other_pulls={40: [], 41: [other], 42: []})
    assert _check(github) == 1
    assert github.posted[0]["body"].count("@original-author") == 1


def test_the_api_is_called_with_the_token_and_read_back(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class Answer(BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, self.headers.get("Authorization"),
                         self.rfile.read(length) if length else b""))
            code = 404 if self.path.endswith("/missing") else 200
            raw = json.dumps({"path": self.path}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        api = cla.Api("t0ken", root=f"http://127.0.0.1:{server.server_port}/")
        assert api.call("GET", "repos/a/b") == (200, {"path": "/repos/a/b"})
        assert api.call("GET", "repos/a/missing") == (404, {"path": "/repos/a/missing"})
        assert api.call("POST", "repos/a/b/statuses/x", {"state": "success"})[0] == 200
    finally:
        server.shutdown()
    assert seen[0][2] == "Bearer t0ken"
    assert json.loads(seen[2][3]) == {"state": "success"}


def test_the_job_reads_its_settings_from_the_workflow(monkeypatch, capsys):
    github = _with_the_original_pull_request(signed=[])
    monkeypatch.setattr(cla, "Api", lambda token: type("A", (), {"call": github})())
    for name, value in {"GITHUB_REPOSITORY": REPO, "PULL_REQUEST": "52", "GITHUB_TOKEN": "t",
                        "SENTENCE": SENTENCE, "ALLOWLIST": "bot*"}.items():
        monkeypatch.setenv(name, value)
    assert cla.main() == 0
    assert "1 co-author(s) still to sign." in capsys.readouterr().out
    assert github.statuses[-1]["state"] == "failure"


def test_the_maintainer_credited_is_never_asked():
    commit = _commit("abc1234" + "0" * 33,
                     "Rebuilt from #52\n\nCo-authored-by: Adekunle Aina <Maintainer@Example.org>",
                     email=PORTER_EMAIL, user=PORTER)

    def no_lookups(method, path, body=None):
        assert not path.startswith(("search/", f"repos/{REPO}/pulls?")), path
        return github(method, path, body)

    github = GitHub([commit], signed=[])
    assert cla.check(no_lookups, REPO, 52, SENTENCE, "bot*",
                     "maintainer@example.org, other@example.org") == 0
    assert github.posted == [] and github.statuses[-1]["state"] == "success"
    # Without the exemption he would be asked like anyone else.
    assert cla.check(GitHub([commit], signed=[]), REPO, 52, SENTENCE, "bot*") == 1


def test_the_workflow_names_the_maintainer_s_addresses():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "cla.yml").read_text(
        encoding="utf-8"))
    given = workflow["jobs"]["co-authors"]["steps"][1]["env"]["MAINTAINER"]
    assert "kunleaina@gmail.com" in [a.strip() for a in given.split(",")]
