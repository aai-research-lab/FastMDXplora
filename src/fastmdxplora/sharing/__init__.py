"""A study shared as one file, and opened from it: an RO-Crate.

A finished study is packed as a zip of its files as its folder lays them
out, with ``ro-crate-metadata.json``, the packing list: what the archive is,
and what each file in it is, with its size and SHA-256 (:mod:`.pack`). The
format is RO-Crate 1.2, read by repositories and programs that know nothing
of FastMDXplora, and the rules a FastMDXplora study keeps on top of it are a
profile (:mod:`.crate`, `docs/sharing.md`). Put on Zenodo it has a DOI, and
a DOI is all anyone needs to open it.
"""

from __future__ import annotations

from fastmdxplora.refusals import CodedError

__all__ = ["HOME", "HOST", "PLACEHOLDERS", "PROFILE", "PROFILE_VERSION", "STUDY",
           "ShareRefused"]

#: The profile an archive keeps, and its version (MAJOR.MINOR): 1.1 adds a
#: study of several runs and a prepared system carried from outside it.
PROFILE_VERSION = "1.1"
PROFILE = f"https://w3id.org/fastmdxplora/study/{PROFILE_VERSION}"

#: What a packed record says in place of the study's folder, any home
#: folder, and the computer's name: safe in YAML, JSON and Markdown alike.
STUDY = "__FASTMDX_STUDY__"
HOME = "__FASTMDX_HOME__"
HOST = "__FASTMDX_HOST__"
PLACEHOLDERS = (STUDY, HOME, HOST)


class ShareRefused(CodedError, RuntimeError):
    """A study that cannot be shared as it stands, or an archive that cannot
    be opened as one."""

    default_code = "environment.share.not_a_study"


def said_size(size: float) -> str:
    """A file's size as said to the person: in MB, in kB under 0.1 MB, in
    bytes under 1 kB."""
    if size < 1000:
        return f"{int(size)} byte" + ("" if int(size) == 1 else "s")
    return f"{size / 1e6:.1f} MB" if size >= 100_000 else f"{size / 1e3:.1f} kB"
