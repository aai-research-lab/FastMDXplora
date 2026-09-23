"""Which code an installation holds, and whether two hold the same.

A version string cannot answer this for a source checkout. setuptools-scm
writes it when ``pip install -e`` runs, and an editable install keeps
whatever it was then while the checkout moves on: a Mac reported
``2.5.6.dev172+g64f17c43b`` while running a commit two hundred later, with a
subcommand the named commit did not have. ``provenance.py`` records the
commit for exactly this reason, and this module uses it.

So there are two kinds of installation and one rule for each:

- **a release** (conda-forge, PyPI, a release image) is identified by its
  version, which was fixed when the package was built from a tag;
- **a source checkout** is identified by its commit, and only while its tree
  is clean. With uncommitted changes the commit does not describe the code,
  so no other installation can be shown to hold the same thing.

A release and a checkout are never the same code, even at the tagged commit:
nothing on either side proves it cheaply, and a wrong "same" is the failure
this exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CodeIdentity", "same_code", "this_code"]


@dataclass(frozen=True)
class CodeIdentity:
    """The code an installation holds."""

    version: str
    #: The checkout's commit, short. Empty for a release.
    commit: str = ""
    #: Whether the checkout has uncommitted changes; ``None`` where that could
    #: not be told, which is not the same as clean.
    dirty: bool | None = False
    #: Where the checkout is. Empty for a release.
    checkout: str = ""

    @property
    def is_checkout(self) -> bool:
        return bool(self.commit)

    def describe(self) -> str:
        if not self.is_checkout:
            return f"release {self.version or '(version unknown)'}"
        state = {True: ", with uncommitted changes",
                 None: ", tree state unknown"}.get(self.dirty, "")
        return f"checkout at {self.commit}{state}"


def _commits_match(a: str, b: str) -> bool:
    length = min(len(a), len(b))
    return length >= 7 and a[:length] == b[:length]


def same_code(here: CodeIdentity, there: CodeIdentity) -> tuple[bool, str]:
    """Whether ``there`` holds the code ``here`` runs, and if not, why not."""
    if here.is_checkout != there.is_checkout:
        return False, "one is a release and the other a source checkout"
    if not here.is_checkout:
        if here.version and here.version == there.version:
            return True, ""
        return False, f"{there.version or 'an unknown version'}, not {here.version}"
    if not _commits_match(here.commit, there.commit):
        return False, f"at {there.commit}, not {here.commit}"
    if here.dirty is not False:
        return False, "this computer's checkout has uncommitted changes"
    if there.dirty is not False:
        return False, "that checkout has uncommitted changes"
    return True, ""


def this_code() -> CodeIdentity:
    """The code this computer is running."""
    from fastmdxplora import __version__
    from fastmdxplora.provenance import source_checkout, source_provenance

    record = source_provenance()
    if not record:
        return CodeIdentity(version=__version__)
    return CodeIdentity(
        version=__version__,
        commit=str(record.get("commit") or ""),
        dirty=record.get("dirty"),
        checkout=str(source_checkout() or ""),
    )
