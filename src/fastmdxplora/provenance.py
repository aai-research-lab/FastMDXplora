"""Which code a run was actually made from.

A manifest recorded the version string and nothing else. That string is
written by setuptools-scm at *install* time, so an editable install carries
whatever the version was when ``pip install -e .`` was last run and drifts
silently from then on. A real study came back stamped 2.3.0 for a run that
used a feature 2.3.0 did not have: the manifest named a version in which the
run could not have happened, and it is the number the report's reproducibility
section prints.

Where the package is imported from a source checkout, the commit says what the
version string cannot. Where it is not -- a conda or PyPI install -- there is
no checkout to ask, and the version string is the whole answer because the
distribution was built from a tag.

Two decisions are worth stating, because the alternatives are defensible:

**The checkout is found from the package, not the working directory.** A run
started inside some other repository would otherwise record that repository's
commit, which is worse than recording nothing: it is a precise claim about the
wrong code.

**A dirty tree is reported, not refused.** Refusing would block the runs
developers make all day, and this project's habit is to say what a result
cannot support rather than to withhold it. A commit beside uncommitted changes
does not describe the code that ran, so the flag is what makes the commit
honest rather than decorative.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "source_checkout",
    "source_provenance",
    "structure_provenance",
    "described_structure",
]

#: Long enough to be unambiguous in any real repository, short enough to read.
_SHORT = 12

#: A repository the size of a source tree answers both questions in
#: milliseconds. The limit is here so a network filesystem or a repository in a
#: strange state cannot hang a run that was only trying to describe itself.
_PATIENCE_SECONDS = 5.0


def source_checkout() -> Path | None:
    """The checkout this package was imported from, or None if installed.

    Walks up from the package rather than from the working directory: a run
    started inside another repository would otherwise report that
    repository's commit, which is a precise claim about the wrong code.
    """
    here = Path(__file__).resolve()
    for directory in here.parents:
        if (directory / ".git").exists():
            return directory
    return None


def _git(root: Path, *arguments: str) -> str | None:
    try:
        finished = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True, text=True, timeout=_PATIENCE_SECONDS)
    except (OSError, subprocess.SubprocessError):
        # No git, or it could not run. Nothing is recorded, which is the
        # honest answer rather than a guess.
        return None
    if finished.returncode != 0:
        return None
    return finished.stdout.strip()


def code_changed(root: Path, package: Path) -> bool | None:
    """Whether the package's own files differ from the commit.

    Only the package's files count: modified, deleted, or new and not yet
    added, since a new module changes what runs as surely as an edited one.
    A study file, a patch or a run folder left in the checkout's root is
    not code, and counting it marked runs as made from uncommitted changes
    they did not have, and stopped ``fastmdx remote`` from sending a study
    from a checkout whose code was exactly its commit.

    ``None`` where git could not answer: a failure is not an empty answer,
    so it is reported as unknown rather than clean.
    """
    try:
        scope = package.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        scope = "."
    changed = _git(root, "status", "--porcelain", "--", scope)
    return None if changed is None else bool(changed)


@lru_cache(maxsize=1)
def source_provenance() -> dict[str, Any] | None:
    """The commit a run was made from, or None where there is no checkout.

    Returns ``{"commit": ..., "dirty": bool}``, and ``branch`` where the
    checkout is on one. ``dirty`` is the important field: with uncommitted
    changes to the package the commit does not describe the code that ran,
    and saying so is what keeps the commit from being decorative.
    """
    root = source_checkout()
    if root is None:
        return None

    commit = _git(root, "rev-parse", "HEAD")
    if not commit:
        return None

    record: dict[str, Any] = {
        "commit": commit[:_SHORT],
        "dirty": code_changed(root, Path(__file__).resolve().parent),
    }
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch and branch != "HEAD":
        record["branch"] = branch
    return record


def described(record: dict[str, Any] | None) -> str | None:
    """One line a person can read, or None where there is nothing to say."""
    if not record:
        return None
    line = record["commit"]
    if record.get("branch"):
        line += f" on {record['branch']}"
    if record["dirty"] is True:
        line += " (with uncommitted changes, so the commit does not describe "
        line += "the code that ran)"
    elif record["dirty"] is None:
        line += " (whether the tree was modified could not be determined)"
    return line


# ---------------------------------------------------------------------------
# Which structure a run was made from, which is a different question from
# which code it was made from and was being answered by the same word.
# ---------------------------------------------------------------------------

#: The header is the first record of a PDB file and the first block of a CIF.
#: Bounded so the cost does not follow the size of the structure.
_HEADER_SCAN = 4096

_CHUNK = 1 << 20


def structure_provenance(given: str, form: str, path: Path | str) -> dict[str, Any] | None:
    """Which structure a run was made from, past the point the path says.

    The manifest recorded the string somebody typed -- ``4hhb_cleaned.pdb``,
    ``../prep/final.pdb`` -- and nothing else. That is an answer for about a
    week. The file gets moved, the directory tidied, a second copy made under
    a name that sorts better, and the run no longer says which structure it
    used. The report is built from the same field, so a methods section ends
    up stating that coordinates came from a filename, which is not something
    a reader can check.

    Three things make it checkable, none of which costs anything beside a run
    of any length:

    **The digest**, which names the bytes whatever became of the path. Two
    runs agree or they do not, and a file edited since stops matching the run
    that used it.

    **The entry the file names itself.** A local structure is usually a
    deposited one that has been through a preparation step, and it keeps the
    header saying which. That answers "which entry" in exactly the case where
    the path has stopped answering it.

    **When**, because deposited entries are revised. A run made before a
    revision used different coordinates from one made after it, and neither
    the identifier nor the filename records which side of it a run falls on.

    Returns None where there is no file to describe -- a sequence input, or a
    fetch that failed -- because a record of nothing is worse than no record.
    """
    file_path = Path(path)
    try:
        digest = hashlib.sha256()
        head = b""
        size = 0
        with file_path.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                if len(head) < _HEADER_SCAN:
                    head += chunk[: _HEADER_SCAN - len(head)]
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        return None

    record: dict[str, Any] = {
        "form": form,
        "given": str(given),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "bytes": size,
        "sha256": digest.hexdigest(),
    }

    if form == "pdb_id":
        record["url"] = (
            f"https://files.rcsb.org/download/{str(given).strip().upper()}.pdb"
        )
    else:
        # The absolute path, because the given one is resolved against a
        # working directory nobody records and which is not where the run is
        # read from later.
        source = Path(given).expanduser()
        try:
            resolved = source.resolve()
            if not resolved.is_absolute():
                # Windows before Python 3.10 hands a path that does not
                # exist back from resolve() unchanged -- still relative
                # (bpo-38671). A relative path in this field is exactly the
                # rot the field exists to prevent: it is an answer only from
                # the directory nobody recorded.
                resolved = Path.cwd() / resolved
            record["path"] = str(resolved)
            record["modified_at"] = datetime.fromtimestamp(
                source.stat().st_mtime, timezone.utc
            ).isoformat()
        except OSError:  # pragma: no cover -- copied, then moved mid-run
            pass

    record.update(_entry_named_in(head))
    return record


def _entry_named_in(head: bytes) -> dict[str, str]:
    """What the file says it is: the entry, and when it was deposited.

    Only what the formats fix the position of is read. This is the file's own
    word rather than a fact about it -- an edited structure keeps the header
    of the entry it began as, and one built by hand may carry a header that
    was never true -- so it is recorded as a claim and the digest is what
    settles identity.
    """
    for raw in head.splitlines():
        line = raw.decode("ascii", "replace")
        if line.startswith("HEADER"):
            found: dict[str, str] = {}
            # The format fixes the columns: 63-66 the ID code, 51-59 the
            # deposition date. Padded-out headers are common, so both are
            # taken only where something is there.
            entry = line[62:66].strip()
            deposited = line[50:59].strip()
            if entry:
                found["entry"] = entry.upper()
            if deposited:
                found["deposited"] = deposited
            return found
        if line.startswith("_entry.id"):  # mmCIF
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1].strip():
                return {"entry": parts[1].strip().strip("'\"").upper()}
    return {}


def described_structure(record: dict[str, Any] | None) -> str | None:
    """One line a person can read, or None where there is nothing to say."""
    if not record:
        return None
    if record.get("form") == "pdb_id":
        line = f"PDB entry {record.get('given', '').upper()}"
    else:
        line = record.get("path") or record.get("given") or "a local file"
        if record.get("entry"):
            line += f", whose header names entry {record['entry']}"
    if record.get("sha256"):
        line += f" (sha256 {record['sha256'][:12]})"
    return line


#: Packages whose version can change a result rather than merely a message.
#: Not every dependency: this is the list worth carrying in every manifest,
#: chosen because each one decides a number. mdtraj measures the contacts,
#: RDKit perceives the chemistry, OpenMM integrates, PLUMED biases, and the
#: force-field toolkits assign the parameters.
RESULT_BEARING_PACKAGES = (
    "mdtraj",
    "numpy",
    "scipy",
    "openmm",
    "pdbfixer",
    "rdkit",
    "openff.toolkit",
    "openmmforcefields",
    "openmmplumed",
    "propka",
    "sklearn",
)


def environment_record() -> dict[str, str | None]:
    """The versions of the packages that decide what a run reports.

    Read from what is already loaded rather than probed in a subprocess.
    A module that ran is in ``sys.modules``, and its version is therefore
    the one that produced the numbers -- which is the question a manifest
    should answer, and a different question from what could be imported if
    something tried.

    Recorded because a manifest that says only which FastMDXplora ran
    cannot explain a result that changes. The 3PTB benchmark's interaction
    table held 292 rows from the release container and 73 when re-analysed
    on another machine, with identical settings, identical ligand
    chemistry, and the container's own commit giving the second answer.
    The difference was environmental and the record could not say which
    environment, which made a demonstrable discrepancy undiagnosable.

    A package absent from the run is reported as ``None`` rather than
    omitted, because "this run did not use RDKit" and "this manifest
    forgot to say" are different statements.
    """
    import sys

    versions: dict[str, str | None] = {}
    for name in RESULT_BEARING_PACKAGES:
        module = sys.modules.get(name)
        if module is None:
            versions[name] = None
            continue
        version = getattr(module, "__version__", None)
        if version is None:
            # OpenMM keeps it on a submodule.
            version = getattr(getattr(module, "version", None),
                              "version", None)
        if version is None:
            # And PDBFixer carries no attribute at all, while deciding
            # every protonation state in the run. The installed
            # distribution knows even where the module does not.
            try:
                from importlib.metadata import (
                    PackageNotFoundError,
                    version as _distribution_version,
                )

                version = _distribution_version(name.split(".")[0])
            except (PackageNotFoundError, ImportError, ValueError):
                version = None
        # "loaded, version unknown" stays distinguishable from "not
        # loaded", because the two say different things about a run.
        versions[name] = str(version) if version is not None else "loaded"

    versions["python"] = ".".join(str(n) for n in sys.version_info[:3])
    versions["platform"] = sys.platform
    return versions
