"""The one folder an assistant's tools may use.

A model calling tools can be talked into reading what it should not by text
it was shown elsewhere, so the tools here reach one folder and nothing
outside it: the studies are written there, the structures and configs read
from there, and a path that leaves it, by ``..`` or by a link, is refused.
A PDB identifier is not a path and is fetched as the builder fetches it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError

__all__ = ["Workspace"]


@dataclass(frozen=True)
class Workspace:
    """The folder, resolved once."""

    root: Path

    @classmethod
    def at(cls, folder: str | os.PathLike[str]) -> Workspace:
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            raise StudyError(f"The workspace {root} is not a folder.",
                             code="environment.path.not_found", path=str(root))
        if root == Path(root.anchor):
            raise StudyError(
                "The workspace cannot be the top of the file system; give the folder "
                "your studies live in, as `fastmdx mcp --workspace FOLDER`.",
                code="config.option.not_permitted", setting="--workspace")
        try:
            home = Path.home().resolve()
        except (RuntimeError, OSError):  # no home folder to be told of
            home = None
        if root == home:
            # An app may start the server in the home folder; the whole of it
            # is not a folder of studies.
            raise StudyError(
                "The workspace cannot be your home folder itself; give the folder "
                "your studies live in, as `fastmdx mcp --workspace FOLDER`.",
                code="config.option.not_permitted", setting="--workspace")
        return cls(root)

    def inside(self, given: Any) -> Path | None:
        """A path as named, read inside the workspace, or None.

        Relative paths are the workspace's; ``~`` is the home folder, as in
        a shell. Empty is the workspace itself.
        """
        text = str(given or "").strip()
        if not text:
            return self.root
        try:
            candidate = Path(text).expanduser()
        except RuntimeError:  # ~user for a user this machine does not have
            return None
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError, ValueError):
            return None  # a loop of links, or a NUL in the name
        if resolved == self.root or self.root in resolved.parents:
            return resolved
        return None

    def path_for(self, given: Any) -> str | None:
        """The rule the Agent's tools are held to: a path inside, or None."""
        found = self.inside(given)
        return str(found) if found is not None else None

    def shown(self, path: str | os.PathLike[str]) -> str:
        """A path as the person reads it: relative to the workspace."""
        target = Path(path)
        # As named first, so a link is shown where it is, not where it leads.
        candidates = [target]
        try:
            candidates.append(target.resolve())
        except (OSError, RuntimeError, ValueError):
            pass
        for candidate in candidates:
            try:
                text = candidate.relative_to(self.root).as_posix()
            except ValueError:
                continue
            return text if text != "." else "the workspace"
        return str(target)
