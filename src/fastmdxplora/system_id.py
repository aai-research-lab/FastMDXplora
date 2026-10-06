"""A study's system as four capital characters, as the GUI names it.

A study was shown under whatever named its structure: a PDB entry in the
case it was typed, a file's name with its suffix (``topology.pdb``), or a
path. The GUI names every study the same way, in four capital characters:
the entry where the study was given a PDB ID (``1l2y`` is ``1L2Y``), and
otherwise the first four letters or digits of the structure file's name
(``trp_cage.pdb`` is ``TRPC``). A name of the person's own for it is kept
in the browser, for display only, and wins over this one.
"""

from __future__ import annotations

import re
from pathlib import PurePath

__all__ = ["system_id", "PDB_ID"]

#: A PDB entry: a digit, then three letters or digits.
PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
#: The suffixes a structure file is named with, compressed or not.
_SUFFIX = re.compile(r"(\.(pdb|ent|cif|mmcif|pdbx|bcif|gro|mol2|sdf|xml|prmtop|parm7|psf|top|xyz))?"
                     r"(\.(gz|bz2|xz|zip))?$", re.IGNORECASE)


def system_id(system: object) -> str:
    """The system's four capital characters, or '' where nothing names it.

    Several systems (``"3 systems"``) are said as they are."""
    text = "" if system is None else str(system).strip()
    if not text:
        return ""
    if PDB_ID.match(text):
        return text.upper()
    if re.fullmatch(r"\d+ systems", text):
        return text
    name = PurePath(text.replace("\\", "/")).name
    stem = _SUFFIX.sub("", name) or name
    letters = re.sub(r"[^A-Za-z0-9]", "", stem)
    return (letters or stem)[:4].upper()
