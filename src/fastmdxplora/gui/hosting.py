"""The GUI served to someone else, behind a proxy that signs them in.

On a person's own machine the GUI trusts loopback: whoever can reach
127.0.0.1 is the person at the keyboard, and a page on another site is
refused by its `Host` and `Origin` (B24). Served for somebody else, none
of that holds. The request arrives from a proxy under a public name, the
person has signed in there and not here, and the machine is not theirs.

Hosted mode is one switch, `fastmdx gui --hosted`, and it changes four
things together, because any one of them alone is a way in:

- **Only the proxy is answered.** Every request must carry the secret the
  proxy was given (`X-FastMDX-Proxy-Secret`); anything else is refused
  before it is read. Whoever reaches the port without passing through the
  sign-in gets nothing, whatever the network in between allows.
- **The names it answers to are listed.** `Host` must be one of
  `--allowed-host` (or a loopback name, for the container's own health
  check), and a POST's `Origin` must be one of them, over http or https.
- **One folder is the whole world.** Every path a request names is read
  inside the workspace, and a path outside it, a link out of it included,
  is refused. The file browser starts there and cannot go above it.
- **No path on the server is shown.** Answers carry paths inside the
  workspace as ``~/...``, and paths given back are read the same way, so
  the person sees their own folder and never the machine's layout.

What a study's own settings name (a structure file, a trajectory) is read
by the run, not by the GUI; in a hosted service each workspace runs in a
container of its own, and that is the boundary for those. The proxy must
remove any `X-FastMDX-Proxy-Secret` a caller sends before adding its own.
"""

from __future__ import annotations

import hmac
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import CodedError

#: The header the proxy sets on every request it forwards.
SECRET_HEADER = "X-FastMDX-Proxy-Secret"

#: Where the secret is read from. An environment variable rather than a
#: flag, so it is not in the process list, and removed from the environment
#: once read, so the runs this server starts do not inherit it.
SECRET_ENV = "FASTMDX_PROXY_SECRET"

#: A secret shorter than this is refused: it would be guessable.
SHORTEST_SECRET = 32

#: How a path inside the workspace is shown, and read back.
HOME_MARK = "~"


class HostingError(CodedError, ValueError):
    """Hosted mode was asked for without what it needs to be safe."""


def _name_of(host: str) -> str:
    """A host name without its port, lower case, brackets kept for IPv6."""
    value = host.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


#: A path on the GUI's own site: one leading slash (two would be another
#: site), then plain characters. Nothing a browser would read as a scheme.
_SAME_SITE_PATH = re.compile(r"^/(?!/)[A-Za-z0-9._~/-]*$")


def _same_site(value: str, flag: str, example: str) -> str:
    """A link to the service, as a path on this site, or HostingError."""
    value = (value or "").strip()
    if value and not _SAME_SITE_PATH.match(value):
        raise HostingError(
            f"{flag} {value!r} is not a path on this site. Give the service's page as a "
            f"path, such as {example}.",
            code="config.option.not_permitted", setting=flag)
    return value


@dataclass(frozen=True)
class Hosting:
    """What a hosted GUI trusts, and the one folder it may use."""

    workspace: Path
    allowed_hosts: frozenset[str]
    secret: str
    #: The service's own page for the person (their account, their runs,
    #: signing out), linked from the GUI's sidebar. A path on the same site,
    #: or empty for no link.
    account_url: str = ""
    #: The service's page that sends a study to compute elsewhere (a GPU it
    #: rents). With it, the builder offers **Run on a GPU**: the config is
    #: saved in the workspace and that page opens with it filled in, for the
    #: person to confirm there. A path on the same site, or empty.
    runs_url: str = ""

    @classmethod
    def from_environment(cls, workspace: str | Path,
                         allowed_hosts: list[str] | tuple[str, ...],
                         account_url: str = "", runs_url: str = "") -> Hosting:
        """Hosted mode as the command line starts it.

        Refuses to start rather than start open: without a secret the proxy
        is not the only caller, and without a name no browser page can post.
        """
        secret = os.environ.pop(SECRET_ENV, "")
        if len(secret) < SHORTEST_SECRET:
            raise HostingError(
                f"--hosted needs the proxy's secret in {SECRET_ENV}, at least "
                f"{SHORTEST_SECRET} characters (for example "
                "`python -c 'import secrets; print(secrets.token_urlsafe(32))'`). "
                "Without it anyone who reaches the port is answered.",
                code="config.option.missing_companion", setting=SECRET_ENV)
        names = frozenset(_name_of(h) for h in allowed_hosts if h.strip())
        if not names:
            raise HostingError(
                "--hosted needs at least one --allowed-host: the name the "
                "proxy serves this GUI under, such as app.example.org.",
                code="config.option.missing_companion", setting="--allowed-host")
        root = Path(workspace).expanduser().resolve()
        if not root.is_dir():
            raise HostingError(f"--workspace {root} is not a folder.",
                               code="environment.path.not_found", path=str(root))
        if root == Path(root.anchor):
            raise HostingError(
                "--workspace cannot be the top of the file system; give the "
                "one folder this person's studies live in.",
                code="config.option.not_permitted", setting="--workspace")
        account_url = _same_site(account_url, "--account-url", "/account/")
        runs_url = _same_site(runs_url, "--runs-url", "/runs")
        return cls(workspace=root, allowed_hosts=names, secret=secret,
                   account_url=account_url, runs_url=runs_url)

    # ---- who is answered ----
    def admits(self, presented: str | None) -> bool:
        """Whether a request came through the proxy."""
        return bool(presented) and hmac.compare_digest(
            str(presented).encode("utf-8"), self.secret.encode("utf-8"))

    def answers_to(self, host_header: str) -> bool:
        """Whether a `Host` header names this GUI: a listed name, or
        loopback for a check from inside the container."""
        from fastmdxplora.gui.server import _names_this_machine

        if not host_header:
            return False
        return (_name_of(host_header) in self.allowed_hosts
                or _names_this_machine(host_header))

    def own_origin(self, origin: str) -> bool:
        """Whether a POST's `Origin` is a page this GUI served."""
        scheme, _, rest = origin.strip().lower().partition("://")
        return scheme in {"http", "https"} and _name_of(rest.split("/", 1)[0]) in self.allowed_hosts

    # ---- paths ----
    def inside(self, given: Any) -> Path | None:
        """A path a request named, read inside the workspace, or None.

        ``~/x``, ``x`` and an absolute path already inside the workspace all
        mean the same file. Anything that leaves it, by ``..`` or by a link,
        is None. An empty path is the workspace itself.
        """
        text = str(given or "").strip()
        if text in ("", HOME_MARK):
            return self.workspace
        if text.startswith(HOME_MARK + "/"):
            candidate = self.workspace / text[len(HOME_MARK) + 1:]
        else:
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = self.workspace / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            return None
        if resolved == self.workspace or self.workspace in resolved.parents:
            return resolved
        return None

    def shown(self, path: str | Path) -> str:
        """A path as the person sees it: ``~/...`` inside the workspace."""
        return self.scrub(str(path))

    def scrub(self, value: Any) -> Any:
        """An answer with every path on the server written as ``~/...``.

        Applied to every JSON answer, so a route added later cannot leak a
        path by forgetting to. Both spellings of the workspace are replaced,
        as typed and as resolved, since a link in its path (``/tmp`` on
        macOS) gives two.
        """
        if isinstance(value, str):
            return self._scrub_text(value)
        if isinstance(value, os.PathLike):
            return self._scrub_text(os.fspath(value))
        if isinstance(value, dict):
            return {key: self.scrub(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(item) for item in value]
        return value

    def _scrub_text(self, text: str) -> str:
        # The interpreter first: a run's command line names it, and it is
        # the machine's layout, not the person's folder.
        text = text.replace(sys.executable, "python")
        for prefix in self._prefixes():
            if text == prefix:
                return HOME_MARK
            text = text.replace(prefix + "/", HOME_MARK + "/")
        return text

    def _prefixes(self) -> tuple[str, ...]:
        spellings = {str(self.workspace), os.path.realpath(self.workspace),
                     os.path.abspath(self.workspace)}
        # Longest first, so a spelling that contains another is replaced whole.
        return tuple(sorted(spellings, key=len, reverse=True))
