"""Selections of the Viewer: typed in MDTraj's language, and named.

A selection typed in the Viewer is read by MDTraj in the very structure the
Viewer renders (the structure with or without its solvent, the live frame,
or the frames' topology), so the atoms it names are the atoms shown, by
their place in that file. A selection named is saved with the study
(``viewer_selections.json``), with how it is shown: its colour, whether it
is shown, a representation of its own and labels. Only what the Viewer can
set is kept, each value checked.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import warnings
from collections import OrderedDict
from pathlib import Path
from typing import Any

__all__ = ["MOST_SELECTIONS", "SELECTIONS_FILE", "atoms_selected", "delete_selection",
           "save_selection", "selections_of"]

SELECTIONS_FILE = "viewer_selections.json"
MOST_SELECTIONS = 50
MOST_RESIDUES = 20000
MOST_CHARACTERS = 500
REPRESENTATIONS = ("none", "sticks", "spheres", "lines", "surface", "cartoon")

_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,40}$")
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOCK = threading.Lock()
_TOPOLOGIES: OrderedDict[tuple[str, int, int], Any] = OrderedDict()
_TOPOLOGIES_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# A typed selection
# --------------------------------------------------------------------------

def atoms_selected(pdb: bytes | None, expression: Any, *, key: tuple[str, int, int]
                   ) -> dict[str, Any]:
    """The atoms an MDTraj selection names in a structure the Viewer renders,
    by their place in it.

    ``pdb`` is the structure as the Viewer was sent it, and ``key`` names
    that version of it (its path, modification time and size), so a large
    solvated system is read once.
    """
    expression = str(expression or "").strip()
    if not expression:
        return {"ok": False, "reason": "Type a selection, such as: resSeq 10 to 25 and name CA."}
    if len(expression) > MOST_CHARACTERS:
        return {"ok": False, "reason": f"A selection is at most {MOST_CHARACTERS} characters."}
    if not pdb:
        return {"ok": False, "reason": "There is no structure to select in."}
    try:
        topology = _topology(pdb, key)
    except Exception as exc:  # noqa: BLE001 - said, not raised
        return {"ok": False, "reason": f"MDTraj could not read the structure: {exc}"}
    try:
        atoms = topology.select(expression)
    except Exception as exc:  # noqa: BLE001 - the person's words, answered
        return {"ok": False, "reason": f"MDTraj could not read that selection: {exc}"}
    residues = len({topology.atom(int(i)).residue.index for i in atoms})
    return {"ok": True, "expression": expression, "atoms": [int(i) for i in atoms],
            "residues": residues, "n_atoms": int(topology.n_atoms)}


def _topology(pdb: bytes, key: tuple[str, int, int]) -> Any:
    with _TOPOLOGIES_LOCK:
        if key in _TOPOLOGIES:
            _TOPOLOGIES.move_to_end(key)
            return _TOPOLOGIES[key]
    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    handle, name = tempfile.mkstemp(suffix=".pdb")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(pdb)
        # MDTraj's notes on the file (residues numbered alike where an
        # insertion code tells them apart) change no atom's place.
        with suppress_native_output(), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            topology = md.load_topology(name)
    finally:
        os.unlink(name)
    with _TOPOLOGIES_LOCK:
        _TOPOLOGIES[key] = topology
        while len(_TOPOLOGIES) > 4:
            _TOPOLOGIES.popitem(last=False)
    return topology


# --------------------------------------------------------------------------
# Named selections
# --------------------------------------------------------------------------

def selections_of(root: Path | str) -> dict[str, Any]:
    """The selections named in the study, in the order they were named."""
    try:
        data = json.loads((Path(root) / SELECTIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    listed = data.get("selections") if isinstance(data, dict) else None
    kept = []
    for selection in listed if isinstance(listed, list) else []:
        clean = _checked(selection)
        if clean is not None and isinstance(selection, dict) \
                and _NAME.match(str(selection.get("name"))):
            kept.append({"name": str(selection["name"]), **clean})
    return {"ok": True, "selections": kept}


def save_selection(root: Path | str, name: Any, selection: Any) -> dict[str, Any]:
    """A selection saved under ``name``, in place of one of that name."""
    name = str(name or "").strip()
    if not _NAME.match(name):
        return {"ok": False, "reason": "A selection is named in 1 to 40 characters."}
    clean = _checked(selection)
    if clean is None:
        return {"ok": False, "reason": "That is not a selection the Viewer can show."}
    with _LOCK:
        listed = selections_of(root)["selections"]
        at = next((i for i, s in enumerate(listed) if s["name"] == name), None)
        if at is None and len(listed) >= MOST_SELECTIONS:
            return {"ok": False, "reason": f"A study keeps at most {MOST_SELECTIONS} "
                                           "selections; forget one first."}
        if at is None:
            listed.append({"name": name, **clean})
        else:
            listed[at] = {"name": name, **clean}
        _write(Path(root), listed)
    return {"ok": True, "selections": listed}


def delete_selection(root: Path | str, name: Any) -> dict[str, Any]:
    """The selection of that name forgotten."""
    with _LOCK:
        listed = selections_of(root)["selections"]
        kept = [s for s in listed if s["name"] != str(name)]
        if len(kept) == len(listed):
            return {"ok": False, "reason": "There is no selection of that name."}
        _write(Path(root), kept)
    return {"ok": True, "selections": kept}


def _write(root: Path, selections: list[dict[str, Any]]) -> None:
    target = root / SELECTIONS_FILE
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps({"selections": selections}, indent=2), encoding="utf-8")
    temporary.replace(target)


def _residue(value: Any) -> list[Any] | None:
    """[chain, number, insertion code, name], each as a PDB file holds it."""
    if not isinstance(value, list) or len(value) != 4:
        return None
    chain, number, icode, name = value
    if not isinstance(number, int) or isinstance(number, bool) or abs(number) > 10**8:
        return None
    if not (isinstance(chain, str) and len(chain) <= 4 and isinstance(icode, str)
            and len(icode) <= 1 and isinstance(name, str) and 0 < len(name) <= 5):
        return None
    return [chain, number, icode, name]


def _checked(selection: Any) -> dict[str, Any] | None:
    """The parts of a selection the Viewer can set, each checked; None if it
    names nothing it could select."""
    if not isinstance(selection, dict):
        return None
    clean: dict[str, Any] = {}
    if selection.get("kind") == "expression":
        expression = selection.get("expression")
        if not isinstance(expression, str) or not 0 < len(expression.strip()) <= MOST_CHARACTERS:
            return None
        clean.update(kind="expression", expression=expression.strip())
    elif selection.get("kind") == "residues":
        residues = selection.get("residues")
        if not isinstance(residues, list) or not 0 < len(residues) <= MOST_RESIDUES:
            return None
        kept = [_residue(r) for r in residues]
        if any(r is None for r in kept):
            return None
        clean.update(kind="residues", residues=kept)
    else:
        return None
    colour = selection.get("colour")
    clean["colour"] = colour.lower() if isinstance(colour, str) and _COLOUR.match(colour) else None
    clean["shown"] = selection.get("shown") is not False
    representation = selection.get("representation")
    clean["representation"] = representation if representation in REPRESENTATIONS else "none"
    clean["labelled"] = selection.get("labelled") is True
    return clean
