"""Every co-author of a pull request signs the contributor agreement.

The `cla` job of `.github/workflows/cla.yml` (CLA Assistant Lite) asks each
commit's author to sign `CLA.md`. A commit can also credit people who did not
make it: a `Co-authored-by:` line, which the agreement asks for where a
contribution includes someone else's work (its section 5a), or the line
`git cherry-pick -x` writes, `(cherry picked from commit <sha>)`. The action
reads neither, and records a signature only from a commit's author, so work
ported from another person's pull request passed with the porter's signature
alone.

This reads both from each commit's message (a `Co-authored-by:` line written
with a literal ``\\n`` before it included), finds each person's GitHub
account from a commit GitHub has linked to their address, and asks those
who have not signed to sign with the same sentence. A signature is recorded
in the action's own file, in the action's own form, so either check knows it
afterwards. The verdict is the commit status `Contributor agreement /
co-authors` on the pull request's head, and one comment on the pull request
says who is left.

It runs from the default branch's copy of this file, on `pull_request_target`
and on comments, and reads the pull request only through the API: it never
runs the pull request's code.

Someone with no GitHub account linked to the address given cannot sign by
comment. Once they have signed a copy of the agreement, the maintainer
records it by adding ``{"email": "<address>"}`` to ``signedContributors`` in
the signatures file.
"""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

SIGNATURES = "signatures/version1/cla.json"
SIGNATURES_BRANCH = "cla-signatures"
CONTEXT = "Contributor agreement / co-authors"
MARKER = "<!-- fastmdxplora-cla-co-authors -->"
#: Pull requests read for a commit that links an address to an account.
MOST_PULL_REQUESTS = 50

_TRAILER = re.compile(
    r"^[ \t]*co-authored-by:[ \t]*(?P<name>[^<\n]*?)[ \t]*<(?P<email>[^<>\s]+@[^<>\s]+)>[ \t]*$",
    re.IGNORECASE | re.MULTILINE)
_PICKED = re.compile(r"\(cherry picked from commit (?P<sha>[0-9a-f]{7,40})\)", re.IGNORECASE)
_NOREPLY = re.compile(r"^(?:\d+\+)?(?P<login>[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?)"
                      r"@users\.noreply\.github\.com$", re.IGNORECASE)


@dataclass
class Person:
    """Someone a pull request's commits credit, and how they were found."""

    name: str
    email: str
    commits: list[str] = field(default_factory=list)
    how: str = "co-author"
    login: str | None = None
    id: int | None = None

    def key(self) -> str:
        return self.email.lower() if self.email else f"login:{(self.login or '').lower()}"


def trailers(message: str) -> list[tuple[str, str]]:
    """The `Co-authored-by:` lines of a message, as (name, address)."""
    text = (message or "").replace("\\r\\n", "\n").replace("\\n", "\n")
    return [(m.group("name").strip(), m.group("email").strip()) for m in _TRAILER.finditer(text)]


def picked_from(message: str) -> list[str]:
    """The commits a message says it was cherry picked from."""
    return [m.group("sha").lower() for m in _PICKED.finditer(message or "")]


def noreply_login(email: str) -> str | None:
    found = _NOREPLY.match(email or "")
    return found.group("login") if found else None


def _short(sha: str) -> str:
    return (sha or "")[:7]


def _user(value: Any) -> tuple[str, int] | None:
    if isinstance(value, dict) and isinstance(value.get("login"), str):
        return value["login"], int(value.get("id") or 0)
    return None


def makers(commits: list[dict]) -> tuple[set[str], set[str]]:
    """The logins and addresses of who made the commits: the action's to ask."""
    logins, emails = set(), set()
    for commit in commits:
        for role in ("author", "committer"):
            user = _user(commit.get(role))
            if user:
                logins.add(user[0].lower())
            email = ((commit.get("commit") or {}).get(role) or {}).get("email")
            if email:
                emails.add(email.lower())
    return logins, emails


def credited(commits: list[dict]) -> tuple[list[Person], dict[str, list[str]]]:
    """Co-authors who made none of the commits, and the commits picked from."""
    _, made_by = makers(commits)
    people: dict[str, Person] = {}
    picks: dict[str, list[str]] = {}
    for commit in commits:
        message = (commit.get("commit") or {}).get("message") or ""
        sha = _short(commit.get("sha", ""))
        for name, email in trailers(message):
            if email.lower() in made_by:
                continue
            person = people.setdefault(email.lower(), Person(name=name, email=email))
            if sha not in person.commits:
                person.commits.append(sha)
        for source in picked_from(message):
            picks.setdefault(source, []).append(sha)
    return list(people.values()), picks


class Api:
    """The few REST calls needed, with the job's token."""

    def __init__(self, token: str, root: str = "https://api.github.com/"):
        self.token = token
        self.root = root

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.root + path.lstrip("/"), data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "fastmdxplora-cla-co-authors"})
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw) if raw else None
            except ValueError:
                return error.code, None


Call = Callable[..., tuple[int, Any]]


def _get(call: Call, path: str) -> Any:
    status, data = call("GET", path)
    if status != 200:
        raise RuntimeError(f"GET {path} answered {status}")
    return data


def _pages(call: Call, path: str, most: int = 10) -> list[Any]:
    """Every item of a listed resource, 100 at a time."""
    items: list[Any] = []
    sep = "&" if "?" in path else "?"
    for page in range(1, most + 1):
        chunk = _get(call, f"{path}{sep}per_page=100&page={page}")
        items.extend(chunk)
        if len(chunk) < 100:
            break
    return items


def _linked(commits: list[dict], email: str) -> tuple[str, int] | None:
    for commit in commits:
        for role in ("author", "committer"):
            given = ((commit.get("commit") or {}).get(role) or {}).get("email") or ""
            user = _user(commit.get(role))
            if user and given.lower() == email.lower():
                return user
    return None


def resolve(people: list[Person], call: Call, repo: str, number: int,
            commits: list[dict]) -> None:
    """Each person's account: from a GitHub address, from a commit GitHub has
    linked to their address (this pull request's, then the repository's other
    pull requests'), then from the address being public on an account."""
    left = []
    for person in people:
        found = None
        login = noreply_login(person.email)
        if login and login.endswith("[bot]"):
            person.login, person.id = login, 0
            continue
        if login:
            status, user = call("GET", f"users/{login}")
            found = _user(user) if status == 200 else None
        found = found or _linked(commits, person.email)
        if found:
            person.login, person.id = found
        else:
            left.append(person)
    if not left:
        return
    others = _get(call, f"repos/{repo}/pulls?state=all&sort=updated&direction=desc"
                        f"&per_page={MOST_PULL_REQUESTS}")
    for pull in others:
        if not left:
            break
        if pull.get("number") == number:
            continue
        theirs = _pages(call, f"repos/{repo}/pulls/{pull['number']}/commits", most=3)
        for person in list(left):
            found = _linked(theirs, person.email)
            if found:
                person.login, person.id = found
                left.remove(person)
    for person in left:
        status, said = call("GET", f"search/users?q={urllib.parse.quote(person.email)}+in:email")
        items = said.get("items") if status == 200 and isinstance(said, dict) else None
        if items and len(items) == 1:
            found = _user(items[0])
            if found:
                person.login, person.id = found


def picked_authors(picks: dict[str, list[str]], call: Call, repo: str) -> list[Person]:
    """The authors of the commits cherry picked into the pull request."""
    people: dict[str, Person] = {}
    for source, into in picks.items():
        status, commit = call("GET", f"repos/{repo}/commits/{source}")
        if status != 200 or not isinstance(commit, dict):
            people[f"sha:{source}"] = Person(
                name=f"the author of {_short(source)}", email="", commits=list(into),
                how="unknown commit")
            continue
        author = (commit.get("commit") or {}).get("author") or {}
        email = author.get("email") or ""
        person = people.setdefault(email.lower() or f"sha:{source}", Person(
            name=author.get("name") or "", email=email, how="cherry picked"))
        person.commits.extend(sha for sha in into if sha not in person.commits)
        user = _user(commit.get("author"))
        if user:
            person.login, person.id = user
    return list(people.values())


def allowed(login: str | None, allowlist: str) -> bool:
    patterns = [p.strip() for p in (allowlist or "").split(",") if p.strip()]
    return bool(login) and (login.endswith("[bot]") or any(
        fnmatch.fnmatchcase(login, pattern) for pattern in patterns))


def has_signed(person: Person, signed: list[dict]) -> bool:
    for entry in signed:
        if not isinstance(entry, dict):
            continue
        if person.id and entry.get("id") == person.id:
            return True
        if person.login and str(entry.get("name") or "").lower() == person.login.lower():
            return True
        if person.email and str(entry.get("email") or "").lower() == person.email.lower():
            return True
    return False


def signed_here(people: list[Person], comments: list[dict], sentence: str,
                repo_id: int, number: int) -> list[dict]:
    """Signatures posted on the pull request by the people asked, in the
    action's own form."""
    wanted = {person.id: person for person in people if person.login and person.id}
    found: dict[int, dict] = {}
    for comment in comments:
        user = _user(comment.get("user"))
        body = str(comment.get("body") or "").strip().lower()
        if user and user[1] in wanted and body == sentence.strip().lower() and user[1] not in found:
            found[user[1]] = {"name": user[0], "id": user[1], "comment_id": comment.get("id"),
                              "created_at": comment.get("created_at"), "repoId": repo_id,
                              "pullRequestNo": number}
    return list(found.values())


def _plain(text: str) -> str:
    """A name from a commit message, safe to put in a comment."""
    kept = re.sub(r"[^\w .,'-]", "", text or "", flags=re.UNICODE).strip()
    return kept[:80] or "someone not named"


def comment_text(waiting: list[Person], sentence: str, repo: str) -> str:
    if not waiting:
        return (f"{MARKER}\nEvery co-author of this pull request has signed the "
                "Contributor License Agreement.")
    lines = [MARKER,
             "Part of this pull request is credited to people who have not signed the "
             f"[FastMDXplora Contributor License Agreement](https://github.com/{repo}/blob/main/CLA.md): "
             "its commits name them as co-authors, or were cherry picked from their commits. "
             "They sign it as the commits' authors do.", ""]
    can_sign = [p for p in waiting if p.login]
    cannot = [p for p in waiting if not p.login]
    for person in can_sign:
        lines.append(f"- @{person.login}, {_role(person)} {_listed(person)}")
    if can_sign:
        lines += ["", "To sign, each posts this comment on this pull request:", "",
                  f"> {sentence}"]
    if cannot:
        lines.append("")
        for person in cannot:
            if person.how == "unknown commit":
                lines.append(f"- {_plain(person.name)}, picked into {_listed(person)}: this "
                             "repository does not have that commit, so its author cannot be "
                             "found. The maintainer checks who wrote it.")
            else:
                lines.append(f"- {_plain(person.name)}, {_role(person)} {_listed(person)}: no "
                             "GitHub account is linked to the address given. Once they have "
                             "signed a copy of the agreement, the maintainer records it.")
    return "\n".join(lines)


def _role(person: Person) -> str:
    return "co-author of" if person.how == "co-author" else "author of a commit picked into"


def _listed(person: Person) -> str:
    return ", ".join(f"`{sha}`" for sha in person.commits)


def _record(call: Call, repo: str, new: list[dict]) -> bool:
    """Add signatures to the file the action keeps, read again on a clash."""
    path = f"repos/{repo}/contents/{SIGNATURES}"
    for _ in range(3):
        status, held = call("GET", f"{path}?ref={SIGNATURES_BRANCH}")
        if status == 200:
            record = json.loads(base64.b64decode(held["content"]).decode("utf-8"))
            sha = held["sha"]
        elif status == 404:
            record, sha = {"signedContributors": []}, None
        else:
            return False
        signed = record.setdefault("signedContributors", [])
        known = {entry.get("id") for entry in signed if isinstance(entry, dict)}
        signed.extend(entry for entry in new if entry["id"] not in known)
        body = {"message": "Record signatures of the Contributor License Agreement",
                "branch": SIGNATURES_BRANCH,
                "content": base64.b64encode(
                    (json.dumps(record, indent=2) + "\n").encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        status, _ = call("PUT", path, body)
        if status in (200, 201):
            return True
        if status not in (409, 422):
            return False
    return False


def check(call: Call, repo: str, number: int, sentence: str, allowlist: str,
          maintainer: str = "") -> int:
    """Read, decide and say; the number of people still to sign. The
    agreement's Maintainer, named by the addresses in `maintainer`, signs
    nothing to himself and is never asked."""
    pull = _get(call, f"repos/{repo}/pulls/{number}")
    if pull.get("state") != "open":
        return 0
    head = pull["head"]["sha"]
    repo_id = int((pull.get("base") or {}).get("repo", {}).get("id") or 0)
    commits = _pages(call, f"repos/{repo}/pulls/{number}/commits", most=3)
    own = {a.strip().lower() for a in (maintainer or "").split(",") if a.strip()}
    people, picks = credited(commits)
    people += picked_authors(picks, call, repo)
    people = [person for person in people if person.email.lower() not in own]
    resolve([person for person in people if not person.login and person.email],
            call, repo, number, commits)
    made_logins, made_emails = makers(commits)
    asked: dict[str, Person] = {}
    for person in people:
        if person.login and (person.login.lower() in made_logins
                             or allowed(person.login, allowlist)):
            continue
        if person.email and person.email.lower() in made_emails:
            continue
        held = asked.get(person.key())
        if held:
            held.commits.extend(sha for sha in person.commits if sha not in held.commits)
        else:
            asked[person.key()] = person
    people = list(asked.values())

    status, held = call("GET", f"repos/{repo}/contents/{SIGNATURES}?ref={SIGNATURES_BRANCH}")
    signed = (json.loads(base64.b64decode(held["content"]).decode("utf-8"))
              .get("signedContributors", []) if status == 200 else [])
    waiting = [person for person in people if not has_signed(person, signed)]
    comments = _pages(call, f"repos/{repo}/issues/{number}/comments")
    new = signed_here(waiting, comments, sentence, repo_id, number)
    if new and _record(call, repo, new):
        signed = signed + new
        waiting = [person for person in waiting if not has_signed(person, signed)]

    description = ("Every co-author has signed the agreement." if not waiting else
                   f"{len(waiting)} co-author{'s' if len(waiting) != 1 else ''} "
                   "still to sign the agreement.")
    call("POST", f"repos/{repo}/statuses/{head}", {
        "state": "failure" if waiting else "success", "context": CONTEXT,
        "description": description,
        "target_url": f"https://github.com/{repo}/blob/main/CLA.md"})

    ours = [c for c in comments if MARKER in str(c.get("body") or "")
            and (_user(c.get("user")) or ("",))[0] == "github-actions[bot]"]
    if waiting or ours:
        text = comment_text(waiting, sentence, repo)
        if ours:
            if ours[-1].get("body") != text:
                call("PATCH", f"repos/{repo}/issues/comments/{ours[-1]['id']}", {"body": text})
        else:
            call("POST", f"repos/{repo}/issues/{number}/comments", {"body": text})
    return len(waiting)


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    number = int(os.environ["PULL_REQUEST"])
    waiting = check(Api(os.environ["GITHUB_TOKEN"]).call, repo, number,
                    os.environ["SENTENCE"], os.environ.get("ALLOWLIST", ""),
                    os.environ.get("MAINTAINER", ""))
    print(f"{waiting} co-author(s) still to sign." if waiting
          else "Every co-author has signed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
