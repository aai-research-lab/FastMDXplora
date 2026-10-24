"""The studies in a workspace, as cards, and two of them compared.

The GUI showed one study at a time, and another was opened by walking to
its folder. This finds the studies under a folder and says each in a card:
its structure, what kind of study it is, where it stands, when it was made,
and the means it recorded with their errors, with a figure it plotted. Two
chosen are compared as `fastmdx diff` compares them, the settings in which
they differ with defaults filled in, and beside that the means both
recorded, a difference marked only past twice their combined error, the
rule the Analysis page and the written comparison use.

Everything is read from the studies' own records; nothing is written.
"""

from __future__ import annotations

import copy
import json
import math
import os
import threading
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmdxplora.system_id import system_id

__all__ = ["studies_in", "card_of", "head_of", "studies_compared", "compared_side", "sides_compared",
           "means_of", "tags_used", "thumbnail_of", "thumbnail_series", "study_thumbnail",
           "study_key", "study_keyed", "study_state"]

#: A difference is marked past this many combined standard errors.
RESOLVED_AT = 2.0

#: How deep under the folder to look, and how many studies to say: a home
#: folder can hold thousands of folders, and the page has to answer.
DEEPEST = 3
MOST = 200

#: Folders a study keeps inside it that are never studies of their own.
_INSIDE_A_STUDY = {"runs", "superseded", "setup", "simulation", "analysis", "report",
                   "comparison", "shared_setup", "free_energy", "pmf"}

#: How many folders a study named by its key is looked for in.
_LOOKED_IN = 20000

#: The measures a card names first, in order.
_FIRST = ("rmsd", "rg", "sasa", "rmsf", "hbonds", "ligand_rmsd")


def studies_in(root: Path | str, *, deepest: int = DEEPEST,
               most: int = MOST, linked_in_read: bool = True) -> dict[str, Any]:
    """The studies under ``root``, newest first, each as a card. A folder
    linked into it from outside it is not walked into; a study linked into
    it (one kept on another disk, as on a cluster's scratch) is listed,
    marked a link (``linked_in``, where it leads), and read held to itself
    as its own pages read it, or, where ``linked_in_read`` is False (a
    hosted workspace, whose routes refuse a path outside it), named and not
    read. A study's runs (``<study>/runs``, unfolded on All studies) are
    listed only where ``runs`` is the study's own, each run linked out of
    it named and not read."""
    from fastmdxplora.gui.browse import is_study

    given = Path(root).expanduser().absolute()
    if given.name == "runs" and _out_of_its_study(given / "_") == "runs":
        # Its runs are not looked for where the link leads: not listed.
        return {"ok": True, "root": str(given), "studies": [], "more": False,
                "tags_used": [], "unavailable": ["runs"],
                "not_read": "runs was not read: it is a link to a folder outside the study."}
    for asked in (given, *given.parents):
        # A run of a study asked by its own path (or a folder in it), where
        # the run or its study's ``runs`` leads out of the study: told from
        # the path as given, before anything there is resolved or read, as
        # the thumbnail of it is refused. Its card is the one its study
        # lists, not read; nothing outside is said.
        out = _out_of_its_study(asked) if asked.parent.name == "runs" else None
        if out:
            card = _a_run_not_read(asked, out)
            return {"ok": True, "root": str(given),
                    "studies": [card] if asked == given else [], "more": False,
                    "tags_used": [], "unavailable": [out], "not_read": card["not_read"]}
    base = given
    try:
        base = base.resolve()
    except OSError:
        pass
    if not base.is_dir():
        return {"ok": False, "reason": f"No such folder: {base}"}
    found: list[Path] = []
    linked: set[Path] = set()
    more = False
    runs_of_a_study = given.name == "runs" and is_study(given.parent)

    def walk(folder: Path, depth: int) -> None:
        nonlocal more
        if len(found) >= most:
            more = True
            return
        try:
            # A link to a folder outside the one looked in is not followed:
            # its studies are not this folder's, and it is not listed. A
            # run linked out of its study is listed, as not read (`card_of`).
            children = sorted(p for p in folder.iterdir() if not p.name.startswith("."))
        except OSError:
            return
        for child in children:
            if not _within(child, base):
                if runs_of_a_study and depth == 1 and os.path.islink(child):
                    found.append(child)
                elif (not runs_of_a_study and os.path.islink(child)
                      and child.name not in _INSIDE_A_STUDY and is_study(child)):
                    # A study linked in: listed, never walked into.
                    found.append(child)
                    linked.add(child)
                continue
            if not child.is_dir():
                continue
            if len(found) >= most:
                more = True
                return
            if child.name in _INSIDE_A_STUDY:
                continue
            if is_study(child):
                found.append(child)
            elif depth < deepest:
                walk(child, depth + 1)

    if is_study(base):
        found.append(base)
    else:
        walk(base, 1)
    # A run linked out of its study is not read (`card_of`), as its
    # study's pages do not read it.
    cards = [_linked_in(folder, linked_in_read) if folder in linked else card_of(folder)
             for folder in found]
    cards.sort(key=lambda card: card.get("when") or "", reverse=True)
    return {"ok": True, "root": str(base), "studies": cards, "more": more,
            "tags_used": tags_used(cards)}


def _linked_in(folder: Path, read: bool) -> dict[str, Any]:
    """The card of a study linked into the folder looked in from outside
    it: read held to the study it leads to (`card_of`), as its own pages
    read it, and marked a link, ``linked_in`` where it leads; or, where it
    is not ``read``, named, marked, and nothing of it read."""
    if read:
        try:
            target = str(folder.resolve())
        except (OSError, RuntimeError):
            target = ""
        # Named as the folder looked in names it, as its path is.
        return {**card_of(folder), "name": folder.name, "linked_in": target}
    why = "it is a link to a folder outside the workspace, which is not read here."
    return {"path": str(folder), "name": folder.name, "system": "", "structure": "",
            "kind": "", "state": "not known", "when": "", "last_active": "", "means": [],
            "tags": [], "note": "", "molecule": None, "unread": list(_PARTS),
            "unavailable": [folder.name], "unavailable_why": {folder.name: "outside"},
            "outside": True, "linked_in": "", "not_read": f"{folder.name} was not read: {why}"}


def _within(folder: Path, study: Path) -> bool:
    try:
        folder.resolve().relative_to(study.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _out_of_its_study(folder: Path) -> str | None:
    """For a run of a study of several, as its path is given
    (``<study>/runs/<run>``): what of it leads out of the study, ``runs``
    or ``runs/<run>``; None for a run inside its study, or a folder that is
    not a run of a study. Told from where the path leads, without opening
    anything there."""
    from fastmdxplora.gui.browse import is_study

    folder = Path(folder).expanduser().absolute()
    runs, study = folder.parent, folder.parent.parent
    if runs.name != "runs" or not is_study(study):
        return None
    if not _within(runs, study):
        return "runs"
    if folder.name != "_" and os.path.lexists(folder) and not _within(folder, study):
        return f"runs/{folder.name}"
    return None


def _a_run_not_read(folder: Path, name: str | None = None) -> dict[str, Any]:
    """The card of a run of a study whose folder, or whose study's ``runs``,
    is a link out of it: named, and nothing of it read."""
    name = name or f"runs/{folder.name}"
    return {"path": str(folder), "name": folder.name, "system": "", "structure": "",
            "kind": "", "state": "not known", "when": "", "last_active": "", "means": [],
            "tags": [], "note": "", "molecule": None, "unread": list(_PARTS),
            "unavailable": [name], "unavailable_why": {name: "outside"}, "outside": True,
            "not_read": f"{name} was not read: it is a link to a folder outside the study."}


def study_key(folder: Path | str) -> str:
    """The key a page names a study by in its address: the study's folder,
    hashed, so no path is said; the same key `/api/status` gives."""
    import hashlib

    return hashlib.sha256(str(Path(folder).expanduser().resolve()).encode("utf-8")).hexdigest()[:16]


def study_keyed(key: str, near: list[Path | str | None],
                under: list[Path | str | None] = ()) -> Path | None:
    """The study an address names by its key: one of the folders ``near``
    (the one open, the one running), the study each sits in, or a run
    inside one of those; else a study under the folders ``under`` (the ones
    the GUI looks in) as All studies finds them, or a run inside one. None
    where none has that key. Only ``under`` is walked: the folders around a
    study are not looked through."""
    import re

    from fastmdxplora.gui.browse import is_study

    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{16}", key):
        return None
    looked = 0
    seen: set[Path] = set()

    def matches(folder: Path) -> bool:
        if folder in seen:
            return False
        seen.add(folder)
        return is_study(folder) and study_key(folder) == key

    def runs_of(study: Path) -> Path | None:
        try:
            runs = [p for p in (study / "runs").iterdir() if p.is_dir()]
        except OSError:
            return None
        return next((run for run in runs if matches(run)), None)

    def walk(folder: Path, depth: int) -> Path | None:
        nonlocal looked
        try:
            children = sorted(p for p in folder.iterdir()
                              if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return None
        for child in children:
            looked += 1
            if looked > _LOOKED_IN:
                return None
            if child.name in _INSIDE_A_STUDY:
                continue
            if matches(child):
                return child
            if is_study(child):
                found = runs_of(child)
            elif depth < DEEPEST:
                found = walk(child, depth + 1)
            else:
                found = None
            if found is not None:
                return found
        return None

    def resolved(folders: Any) -> list[Path]:
        kept: list[Path] = []
        for given in folders:
            if given is None:
                continue
            try:
                kept.append(Path(given).expanduser().resolve())
            except OSError:
                continue
        return kept

    bases: list[Path] = []
    for base in resolved(near) + resolved(under):
        # A run inside a study: the study, and its other runs, are near too.
        bases.extend(b for b in (base, base.parent.parent) if b not in bases)
    for base in bases:
        if not base.is_dir():
            continue
        if matches(base):
            return base
        found = runs_of(base) if is_study(base) else None
        if found is not None:
            return found
    for base in resolved(under):
        if base.is_dir():
            found = walk(base, 1)
            if found is not None:
                return found
    return None


def tags_used(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tags the studies carry, the most used first, each with how many
    carry it: one tag whatever its case, written as it was first."""
    counted: dict[str, list[Any]] = {}
    for card in cards:
        for tag in card.get("tags") or []:
            counted.setdefault(tag.casefold(), [tag, 0])[1] += 1
    return [{"tag": tag, "studies": n}
            for tag, n in sorted(counted.values(), key=lambda kept: (-kept[1], kept[0].casefold()))]


def head_of(folder: Path | str) -> dict[str, Any]:
    """What a study is, from its records: its folder, its system, what kind
    of study it is and where it stands. Read through `record_access`, held
    to the study where the read is (the Compare page): where the record its
    state is said from was not read, the state is not known, never one
    taken from that record's absence."""
    from fastmdxplora import record_access

    base = Path(folder)
    head = _head(base, *_records_of(base))
    for name in ("batch_manifest.json", "manifest.json"):
        if os.path.lexists(base / name) and not record_access.can_read(base / name):
            head["state"] = f"not known: {name} was not read"
            break
    return head


def _head(base: Path, config: Any, batch: Any, manifest: Any) -> dict[str, Any]:
    named = _system_of(config, batch, manifest)
    return {
        "path": str(base),
        "name": base.name,
        # The system in four capital characters (fastmdxplora.system_id),
        # and what named it: a PDB entry, or its structure file's name.
        "system": system_id(named),
        "structure": named,
        "kind": _kind_of(config, batch, simulated=_simulated(base)),
        "state": _state_of(base, batch, manifest),
    }


def _records_of(base: Path) -> tuple[Any, Any, Any]:
    """The study's Config (its phase settings lifted), batch manifest and
    Manifest, each None where it has none."""
    config = _lifted(_read_yaml(base / "resolved_config.yml")
                     or _read_yaml(base / "exploration.yml"))
    return config, _read_json(base / "batch_manifest.json"), _read_json(base / "manifest.json")


def card_of(folder: Path | str) -> dict[str, Any]:
    """What a card says of one study, from its records, read held to the
    study as its pages are (`server._confined`, through `record_access`): a
    record linked out of it, or a file of it that is not one of its results
    (a hidden one), is not read. Each record not read is named
    (``unavailable``, each with ``unavailable_why``; ``not_read`` in
    words), and what it would have given is said not read (``unread``:
    ``state``, ``length``, ``means``, ``molecule``), never taken from its
    absence."""
    base = Path(folder)
    out = _out_of_its_study(base)
    if out:
        return _a_run_not_read(base, out)
    # Read once while the study's records stand as they were: a list of a
    # hundred studies took several times as long as before the cards were
    # held to their studies. A study running is read each time (its
    # process can end without a record changing).
    stamp = _stamp_of(base)
    with _CARDS_LOCK:
        kept = _CARDS_KEPT.get(str(base))
    if (kept is not None and stamp is not None and kept[0] == stamp
            and _stamp_of_files(kept[2]) == kept[3]):
        with _CARDS_LOCK:
            if str(base) in _CARDS_KEPT:
                _CARDS_KEPT.move_to_end(str(base))
        return copy.deepcopy(kept[1])
    card = _card_of(base)
    # The structures its molecule is drawn from, or would be, wherever in
    # the study they are (a start the Config names in a folder of its own):
    # stamped too, so a structure replaced is drawn again.
    watched = tuple(card.pop("_drawn_from", ()))
    if stamp is not None and card.get("state") != "running" and "pace" not in card:
        with _CARDS_LOCK:
            _CARDS_KEPT[str(base)] = (stamp, copy.deepcopy(card), watched,
                                      _stamp_of_files(watched))
            while len(_CARDS_KEPT) > _MOST_CARDS_KEPT:
                _CARDS_KEPT.popitem(last=False)
    return card


#: Cards kept in memory by their study's records' places, sizes and times.
_MOST_CARDS_KEPT = 1024
_CARDS_KEPT: "OrderedDict[str, tuple[tuple, dict[str, Any], tuple, tuple]]" = OrderedDict()
_CARDS_LOCK = threading.Lock()
#: The folders of a study whose files a card reads, looked into for its
#: stamp; a run's folder under `runs`, and an analysis's under `analysis`.
_STAMPED = {"analysis", "simulation", "setup", "report", "runs", "pmf", "agent",
            "conversations", "free_energy", "comparison"}


def _stamp_of_files(paths: tuple) -> tuple:
    """Each file's place, mode, size and time, the link itself where one is
    (nothing read, no link at its end followed); None for one not there."""
    seen = []
    for path in paths:
        try:
            info = os.lstat(path)
            seen.append((path, info.st_mode, info.st_size, info.st_mtime_ns))
        except OSError:
            seen.append((path, None))
    return tuple(seen)


def _stamp_of(base: Path) -> tuple | None:
    """What a study's card is read from, by each file's place, size and
    time (no file read, no link followed): its folder and those it keeps
    its records in. None where the folder cannot be looked in."""
    seen: list[tuple] = []

    def look(folder: Path, relative: str, depth: int) -> None:
        with os.scandir(folder) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                name = f"{relative}{entry.name}"
                seen.append((name, info.st_mode, info.st_size, info.st_mtime_ns))
                if (depth < 5 and entry.is_dir(follow_symlinks=False)
                        and (entry.name in _STAMPED or entry.name.startswith("segment-")
                             or folder.name in ("runs", "analysis"))):
                    look(Path(entry.path), name + "/", depth + 1)

    try:
        info = os.stat(base)
        look(base, "", 1)
        if base.parent.name == "runs":
            # A run of a study of several is read with its study's records
            # of its rounds (`server._a_run_in_its_study`).
            with os.scandir(base.parent.parent) as entries:
                for entry in entries:
                    found = entry.stat(follow_symlinks=False)
                    seen.append((f"../../{entry.name}", found.st_mode, found.st_size,
                                 found.st_mtime_ns))
    except OSError:
        return None
    return (info.st_mtime_ns, tuple(sorted(seen)))


def _card_of(base: Path) -> dict[str, Any]:
    """`card_of`, read."""
    from fastmdxplora.gui.server import _as_opened, _confined

    card = _confined(base, _card_read)
    if not isinstance(card, dict) or "state" not in card:
        # The study's folder could not be opened to hold the read in.
        why = card.get("reason") if isinstance(card, dict) else None
        return {"path": str(base), "name": base.name, "system": "", "structure": "",
                "kind": "", "state": "not known", "when": "", "last_active": "",
                "means": [], "tags": [], "note": "", "molecule": None,
                "unread": list(_PARTS), "not_read": str(why or _RECORDS_NOT_READ)}
    try:
        inside = str(base.resolve())
    except OSError:
        inside = str(base)
    if inside != str(base):
        card = _as_opened(card, inside, str(base))
    if card.get("unavailable"):
        card["unavailable"] = _named_first(card["unavailable"])
        card["not_read"] = _not_read_said(card["unavailable"], card.get("unavailable_why") or {})
    return card


def _named_first(names: list[str]) -> list[str]:
    """Records not read, those named by their place in the study first, as
    the row shows the first two: one the read could not place ("an
    unavailable record") last."""
    return sorted(names, key=lambda name: (name.startswith(("a ", "an ")), name))


#: What a card says that a record not read leaves unknown.
_PARTS = ("state", "length", "means", "molecule")
_RECORDS_NOT_READ = "The study's records could not be read."


def _not_read_said(names: list[str], why: dict[str, str]) -> str:
    """The records a held read refused, in words: each reason once, with
    the records it applies to."""
    from fastmdxplora.gui.series import NOT_READ_BECAUSE

    by_why: dict[str, list[str]] = {}
    for name in names:
        by_why.setdefault(str(why.get(name) or "outside"), []).append(name)
    said = []
    for reason, which in by_why.items():
        because = NOT_READ_BECAUSE.get(reason, NOT_READ_BECAUSE["outside"])
        if len(which) == 1:
            said.append(f"{which[0]} was not read: {because}.")
        else:
            said.append(f"{', '.join(which)} were not read: each{because[2:]}.")
    return " ".join(said)


def _card_read(base: Path) -> dict[str, Any]:
    """`card_of`, its records read as they are: within `_confined`, held to
    the study. Each part of the card a refused record leaves unknown is
    named in ``unread``."""
    token = _READ_ONCE.set({})
    try:
        return _card_read_once(base)
    finally:
        _READ_ONCE.reset(token)


def _card_read_once(base: Path) -> dict[str, Any]:
    from fastmdxplora import record_access

    unread: list[str] = []

    def part(name: str, read: Any, otherwise: Any = None) -> Any:
        with record_access.refusals_noted() as refused:
            try:
                value = read()
            except OSError:
                value = otherwise
        if refused and name not in unread:
            unread.append(name)
        return value

    records: list[Any] = [None, None, None]
    seen: dict[str, Any] = {}

    def head() -> dict[str, Any]:
        records[:] = _records_of(base)
        return _head(base, *records)

    said = part("state", head) or {**_head(base, None, None, None)}
    if "state" in unread:
        # Where a record it is said from was not read, where it stands and
        # what kind of study it is are not known.
        said = {**said, "state": "not known", "kind": ""}
    config, batch, manifest = records
    active = part("active", lambda: _last_active(base, of_runs=isinstance(batch, dict)), "")
    card: dict[str, Any] = {
        **said,
        "when": _when(base, manifest),
        # When anything was last done with it: run, analysed, reported,
        # tagged, viewed or talked about. Not said where a record of that
        # was not read: it may be the newest.
        "last_active": "" if "active" in unread else active,
        "means": [],
    }
    from fastmdxplora.study_tags import tags_of

    # What the person said the study is, kept beside its records.
    card.update(tags_of(base))
    simulation = (config or {}).get("simulation") if isinstance(config, dict) else None
    from fastmdxplora.simulation.resume import extended_production

    far = part("length", lambda: _how_far(base, batch, card, seen, config), {})
    # An extended study's config carries its last piece's length after the
    # join, so its pieces are what say how long it ran: as its production
    # was read for how far it got (`simulated_times`, which reads its
    # pieces once it has stopped), else read here.
    times = seen.get("times")
    if isinstance(batch, dict):
        extended = None
    elif times is not None and not seen.get("running"):
        extended = ((times["production_ns"], times["pieces"])
                    if (times.get("pieces") or 1) > 1 and times.get("production_ns") is not None
                    else None)
    else:
        extended = part("length", lambda: extended_production(base))
    if extended:
        card["production_ns"], card["pieces"] = round(extended[0], 6), extended[1]
    elif isinstance(simulation, dict) and simulation.get("duration_ns") is not None:
        card["production_ns"] = simulation.get("duration_ns")
    forcefield = ((config or {}).get("setup") or {}).get("forcefield") \
        if isinstance(config, dict) else None
    if forcefield and str(forcefield) != "auto":
        card["forcefield"] = str(forcefield)
    if not isinstance(batch, dict):
        # Every mean the study recorded, the measures named first leading:
        # a row shows the first and the rest on asking.
        card["means"] = part("means", lambda: [{"analysis": name, **mean}
                                               for name, mean in _means(base)], [])
    else:
        card["runs"] = _runs_count(batch)
        card["means"] = part("means", lambda: _ensemble_means(base, card["runs"]), [])
    if "length" not in unread:
        # A run's records not read: how long it ran is not a sum of the rest.
        card.update(far)
    if card["state"] == "analysed" and card["kind"] == "one run" and not _simulated(base):
        card["kind"] = "a trajectory analysed"
    # The molecule, as All studies draws it (backbone_picture.py).
    from fastmdxplora.gui.backbone_picture import backbone_points, start_outside

    try:
        card["molecule"] = part("molecule", lambda: backbone_points(base))
    except Exception:  # noqa: BLE001 - a row stands without its picture
        card["molecule"] = None
    if card["molecule"] is not None and "molecule" in unread:
        # Drawn from a structure that was read, which it names.
        unread.remove("molecule")
    if card["molecule"] is None:
        outside = start_outside(base)
        if outside:
            card["molecule_outside"] = outside
    card["_drawn_from"] = _drawn_from(base)
    pmf = _read_json(base / "pmf.json")
    if isinstance(pmf, dict):
        # Whether the windows gave a free energy, and if not the first
        # sentence of why: the profile itself is on the study's pages.
        refused = str(pmf.get("refused") or "")
        card["free_energy"] = ({"refused": refused.split(". ")[0].rstrip(".") + "."}
                               if refused else {"recombined": True})
    kept = [name for name in _PARTS if name in unread]
    if kept:
        card["unread"] = kept
    return card


def _drawn_from(base: Path) -> list[str]:
    """The structure the card's molecule is drawn from and the start its
    Config names, those in the study, for `card_of` to stamp: either can be
    in a folder the study's stamp does not look in."""
    from fastmdxplora import record_access
    from fastmdxplora.gui.backbone_picture import _start_named, molecule_structure

    paths: list[str] = []
    try:
        found = molecule_structure(base)
    except OSError:
        found = None
    for path in (found[0] if found else None, _start_named(base)):
        if path is not None and record_access.can_read(path) and str(path) not in paths:
            paths.append(str(path))
    return paths


def _picture_of(base: Path) -> str | None:
    if thumbnail_of(base) is not None:
        return "figure"
    from fastmdxplora.gui.backbone_picture import structure_for_picture

    return "backbone" if structure_for_picture(base) is not None else None


def _series_of(base: Path) -> str | None:
    """The first of the card's measures with a series to plot (series.py)."""
    from fastmdxplora.gui.series import SERIES

    from fastmdxplora import record_access

    for name in _FIRST:
        if name in SERIES and record_access.is_file(base / "analysis" / name / f"{name}.dat"):
            return name
    return None


#: The most points a card's series is sent: a card is 300 px across.
CARD_POINTS = 240


def thumbnail_series(folder: Path | str) -> dict[str, Any]:
    """The series a card plots, thinned to what a card can show."""
    from fastmdxplora.gui.series import series_payload

    base = Path(folder)
    name = _series_of(base)
    if name is None:
        return {"ok": False, "reason": "no series to plot"}
    found = series_payload(base, name)
    if not found.get("ok"):
        return found
    n = len(found["y"])
    every = max(1, -(-n // CARD_POINTS))
    mean = found.get("mean") or {}
    return {
        "ok": True, "analysis": name, "label": found["label"], "unit": found["unit"],
        "kind": found["kind"], "x_label": found.get("x_label") or "",
        "x": found["x"][::every], "y": found["y"][::every],
        "mean": mean.get("value"), "from_x": mean.get("from_x"),
    }


def thumbnail_of(folder: Path | str) -> Path | None:
    """A figure the study plotted, for its card: the first of its measures'
    own figures, else the report's summary figure. Through `record_access`,
    held to the study where the read is (`/api/study-thumbnail`)."""
    from fastmdxplora import record_access

    base = Path(folder)
    for name in _FIRST:
        figure = base / "analysis" / name / f"{name}.png"
        if record_access.is_file(figure):
            return figure
    summary = base / "report" / "analysis_summary.png"
    if record_access.is_file(summary):
        return summary
    # An umbrella study's own figure is its free energy profile.
    for profile in sorted(record_access.glob(base / "pmf", "*.png")):
        if record_access.is_file(profile):
            return profile
    for figure in sorted(record_access.glob(base / "analysis", "*/*.png")):
        if record_access.is_file(figure):
            return figure
    return None


def study_thumbnail(folder: Path | str, *, scheme: str = "light",
                    series: bool = False) -> dict[str, Any]:
    """What `/api/study-thumbnail` sends for a study, read held to it
    (`server._confined`): with ``series``, the card's series
    (`thumbnail_series`); else a figure it plotted (``data``, ``kind``),
    else its backbone drawn as SVG, else ``ok`` False. A record linked out
    of the study is not read and is named (``unavailable``, ``not_read``)."""
    from fastmdxplora.gui.server import _confined

    def read(inside: Path) -> dict[str, Any]:
        from fastmdxplora import record_access
        from fastmdxplora.gui.backbone_picture import backbone_svg

        if series:
            return thumbnail_series(inside)
        figure = thumbnail_of(inside)
        if figure is not None:
            try:
                return {"ok": True, "data": record_access.read_bytes(figure),
                        "kind": "image/png"}
            except OSError:
                pass
        picture = backbone_svg(inside, scheme)
        if picture is None:
            return {"ok": False, "reason": "no figure, and no structure to render"}
        return {"ok": True, "data": picture.encode("utf-8"), "kind": "image/svg+xml"}

    out = _out_of_its_study(Path(folder))
    if out:
        # A run linked out of its study, as its card says: not read.
        return {"ok": False, "unavailable": [out], "unavailable_why": {out: "outside"},
                "not_read": f"{out} was not read: it is a link to a folder outside the study."}
    answer = _confined(Path(folder), read)
    if isinstance(answer, dict) and answer.get("unavailable"):
        answer["not_read"] = _not_read_said(answer["unavailable"],
                                            answer.get("unavailable_why") or {})
    return answer


def studies_compared(first: Path | str, second: Path | str) -> dict[str, Any]:
    """Two studies side by side: the settings in which they differ, and the
    means both recorded, a difference marked only where it is resolved."""
    a, b = Path(first), Path(second)
    try:
        one, other = compared_side(a), compared_side(b)
    except Exception as exc:  # noqa: BLE001 - said, not raised
        from fastmdxplora.refusals import refusal_of

        return {"ok": False, "reason": refusal_of(exc).message}
    return {"ok": True, "first": card_of(a), "second": card_of(b),
            **sides_compared(one, other)}


def compared_side(base: Path) -> dict[str, Any]:
    """What a comparison reads of one study: its Config, the phases and
    analyses it ran, and the means it recorded. Each record is read through
    `record_access`, so a read held to the study refuses one linked out of
    it; an analysis whose record was refused is named in ``not_read``,
    never taken for one that recorded no mean. Raises where the study has
    no Config to compare."""
    from fastmdxplora import record_access
    from fastmdxplora.config.diff import config_of

    config, _ = config_of(base)
    not_read: list[str] = []
    try:
        folders = [f for f in record_access.iterdir(base / "analysis") if record_access.is_dir(f)]
    except OSError:
        folders = []
    for folder in folders:
        options = folder / "options.json"
        if os.path.lexists(options) and not record_access.can_read(options):
            not_read.append(folder.name)
    return {"config": config, "ran": _phases_ran(base), "analysed": _analysed(base),
            "ran_analyses": _ran_analyses(base), "means": means_of(base),
            "not_read": sorted(not_read)}


def means_of(base: Path) -> dict[str, dict[str, Any]]:
    """Every mean a comparison sets beside another's: each analysis's own,
    then the thermodynamic means as the run's Overview reads them (from the
    thermodynamics analysis where it recorded them, from production's
    energy file otherwise), each with its error only where it is
    determined. A thermodynamic mean whose record was not read is said so."""
    means = dict(_means(base))
    try:
        from fastmdxplora.gui.overview_view import THERMODYNAMICS, _thermodynamics

        found = _thermodynamics(base)
    except Exception:  # noqa: BLE001 - compared without them
        return means
    shown = found.get("means") if isinstance(found.get("means"), dict) else {}
    for key, label, unit in THERMODYNAMICS:
        said = shown.get(key)
        if isinstance(said, dict) and _finite(said.get("mean")) is not None:
            determined = bool(said.get("determined"))
            means[key] = {"label": label, "mean": _finite(said.get("mean")),
                          "error": _finite(said.get("error")) if determined else None,
                          "unit": unit,
                          "withheld": None if determined else (str(said.get("why") or "")
                                                               or "not determined"),
                          "noted": said.get("noted") or None}
        elif key in shown and found.get("not_read"):
            means[key] = {"label": label, "mean": None, "error": None, "unit": unit,
                          "withheld": None, "not_read": True}
        elif key in (found.get("no_mean") or {}):
            # Its analysis ran and said why it gave no mean: not determined,
            # never "not recorded".
            means[key] = {"label": label, "mean": None, "error": None, "unit": unit,
                          "withheld": found["no_mean"][key]}
    return means


def sides_compared(one: dict[str, Any], other: dict[str, Any],
                   errors: dict[str, tuple[float, float]] | None = None) -> dict[str, Any]:
    """The settings in which two studies differ and the means each recorded,
    from what `compared_side` read of each. ``errors`` gives, by quantity,
    the error of each one's mean to compare them with in place of the
    error each run estimated for itself: two replicas' (the Compare page,
    `batch.aggregate.replica_error`)."""
    from fastmdxplora.config.diff import differences

    found = [d for d in differences(one["config"], other["config"],
                                    ran=(one["ran"], other["ran"]))
             if not d.where_only]

    def not_reached(d: Any) -> bool:
        # The analyses left to their default by a study that never reached
        # its analysis: null there is not a choice of none, and was listed
        # against the default list the other study recorded.
        return d.setting == "analysis.include" and any(
            value is None and not side["analysed"]
            for value, side in ((d.first, one), (d.second, other)))

    def as_ran(d: Any) -> Any:
        # Left to the default by a study that ran its analyses: what it ran
        # is what that default was. Listed as "not set" against the same
        # list written out, or against another list.
        if d.setting != "analysis.include":
            return d
        first = one["ran_analyses"] if d.first is None else d.first
        second = other["ran_analyses"] if d.second is None else d.second
        if (isinstance(first, list) and isinstance(second, list)
                and sorted(map(str, first)) == sorted(map(str, second))):
            return None
        return type(d)(d.setting, d.first if first is None else first,
                       d.second if second is None else second)

    found = [d for d in (as_ran(d) for d in found) if d is not None]
    settings = [d.as_record() for d in found if not (d.unrecorded or not_reached(d))]
    unrecorded = [d.setting for d in found if d.unrecorded or not_reached(d)]

    mine, theirs = one["means"], other["means"]
    # An analysis whose record was not read is said so on its side, never
    # as one that recorded no mean.
    for side, means in ((one, mine), (other, theirs)):
        for name in side.get("not_read") or ():
            means.setdefault(name, {"label": _label_of(name), "mean": None, "error": None,
                                    "unit": "", "withheld": None, "not_read": True})
    from fastmdxplora.gui.overview_view import THERMODYNAMICS

    thermodynamic = [key for key, _, _ in THERMODYNAMICS]
    names = set(mine) | set(theirs)
    measures = []
    for name in (_in_order(names - set(thermodynamic))
                 + [key for key in thermodynamic if key in names]):
        first, second = mine.get(name), theirs.get(name)
        row: dict[str, Any] = {"analysis": name, "first": first, "second": second,
                               "label": (first or second or {}).get("label", name),
                               "unit": (first or second or {}).get("unit", "")}
        usable = [side for side in (first, second)
                  if side and side.get("mean") is not None and side.get("error") is not None]
        if len(usable) == 2:
            difference = second["mean"] - first["mean"]
            given = (errors or {}).get(name)
            error = math.hypot(*(given or (first["error"], second["error"])))
            row["versus"] = {"difference": difference, "error": error,
                             "resolved": error > 0 and abs(difference) > RESOLVED_AT * error}
            if given:
                row["versus"]["errors"] = list(given)
                row["versus"]["error_of"] = "replicas"
        measures.append(row)
    return {"settings": settings, "unrecorded": unrecorded, "measures": measures,
            "resolved_at": RESOLVED_AT}


def _phases_ran(base: Path) -> set[str] | None:
    """The phases the study's records say it ran: its Manifest's, and setup
    and simulation where its live record says it simulated. None where
    neither says (its Config's phases are read then)."""
    manifest = _read_json(base / "manifest.json")
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    ran = {str(p.get("name")) for p in phases or [] if isinstance(p, dict) and p.get("name")
           and str(p.get("status") or "").lower() != "skipped"}
    if _simulated(base):
        ran |= {"setup", "simulation"}
    return ran or None


def _ran_analyses(base: Path) -> list[str] | None:
    """The analyses the study ran, as its analysis manifest planned them."""
    manifest = _read_json(base / "analysis" / "analysis_manifest.json")
    plan = manifest.get("plan") if isinstance(manifest, dict) else None
    return [str(name) for name in plan] if isinstance(plan, list) and _analysed(base) else None


def _analysed(base: Path) -> bool:
    """Whether the study's analysis ran: its Manifest names the phase."""
    manifest = _read_json(base / "manifest.json")
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    return any(isinstance(p, dict) and p.get("name") == "analysis" for p in phases or [])


# ---------------------------------------------------------------------------
def _means(base: Path) -> list[tuple[str, dict[str, Any]]]:
    """(analysis, {mean, error, unit, withheld}) for each mean the study
    recorded, the measures a card names first leading."""
    from fastmdxplora.batch.aggregate import read_member_findings
    from fastmdxplora.gui.report_dashboard import unit_of

    try:
        found = read_member_findings(base)
    except Exception:  # noqa: BLE001 - a card stands without them
        return []
    out = []
    for name in _in_order(found):
        record = found.get(name) or {}
        mean = record.get("mean") if isinstance(record.get("mean"), dict) else None
        if mean is None:
            continue
        withheld = mean.get("not_a_measurement") or None
        from fastmdxplora.image_note import note_of, said_short

        out.append((name, {
            "label": _label_of(name),
            "mean": _finite(mean.get("mean")),
            "error": None if withheld else _finite(mean.get("standard_error")),
            "unit": unit_of(name, mean),
            "withheld": str(withheld) if withheld else None,
            # Its image note beside its verdict (`image_note`).
            "noted": said_short(note_of(mean)) or None,
        }))
    return out


def _label_of(name: str) -> str:
    """An analysis by the name its series has, else its Analysis page
    heading: "end to end" was its folder's name."""
    from fastmdxplora.gui.report_dashboard import ANALYSIS_SECTION_BY_FOLDER
    from fastmdxplora.gui.series import SERIES

    if name in SERIES:
        return SERIES[name][0]
    return ANALYSIS_SECTION_BY_FOLDER.get(name) or name.replace("_", " ").capitalize()


def _in_order(names: Any) -> list[str]:
    names = list(names)
    return sorted(names, key=lambda n: (_FIRST.index(n) if n in _FIRST else len(_FIRST), n))


def _system_of(config: Any, batch: Any, manifest: Any) -> str:
    for source in (config, batch):
        systems = source.get("systems") if isinstance(source, dict) else None
        if isinstance(systems, list) and systems and isinstance(systems[0], dict):
            named = {str(s.get("system")) for s in systems
                     if isinstance(s, dict) and s.get("system")}
            if len(named) == 1:
                return _short(next(iter(named)))
            if named:
                return f"{len(named)} systems"
    if isinstance(manifest, dict) and manifest.get("system"):
        return _short(str(manifest["system"]))
    return ""


def _short(system: str) -> str:
    """A structure by its identifier, or a file by its name."""
    return Path(system).name if ("/" in system or "\\" in system) else system


def _simulated(base: Path) -> bool:
    """Whether the study's own live record says it simulated: its
    production taken, or steps planned. Analyze again wrote its config's
    phases as `[analysis, report]`, and a run read as a trajectory analysed."""
    record = _read_json(base / "simulation" / "live_status.json")
    if not isinstance(record, dict):
        return False
    states = record.get("stage_states") if isinstance(record.get("stage_states"), dict) else {}
    taken = str(states.get("production") or "waiting").lower() not in ("waiting", "skipped")
    planned = record.get("total_planned_steps")
    return taken or (isinstance(planned, (int, float)) and planned > 0)


def _kind_of(config: Any, batch: Any, *, simulated: bool = False) -> str:
    if isinstance(batch, dict):
        planned = batch.get("planned") or []
        runs = int(batch.get("n_runs") or len(planned) or 0)
        if not runs:
            return "several runs"
        if any(isinstance(p, dict) and ((p.get("options") or {}).get("simulation") or {})
               .get("umbrella") for p in planned):
            return f"umbrella sampling, {runs} windows"
        sweep = batch.get("sweep") if isinstance(batch.get("sweep"), dict) else {}
        axes = sorted(sweep)
        from fastmdxplora.batch.aggregate import SEED_AXES

        if axes and set(axes) <= SEED_AXES:
            return f"{runs} replicas"
        if axes:
            return f"{runs} runs across {', '.join(a.split('.')[-1] for a in axes)}"
        return f"{runs} runs"
    phases = (config or {}).get("include_phase") if isinstance(config, dict) else None
    if isinstance(phases, list) and phases and "simulation" not in phases and not simulated:
        return "a trajectory analysed" if "analysis" in phases else "prepared"
    simulation = (config or {}).get("simulation") if isinstance(config, dict) else None
    if isinstance(simulation, dict):
        for key, said in (("metadynamics", "metadynamics"), ("steered", "steered pulling")):
            if simulation.get(key):
                return said
    return "one run"


def study_state(folder: Path | str) -> str:
    """A study's state, from its records: the one derivation every page
    reads (All studies' cards, the sidebar, the Overview). For a study of
    several runs, from its runs' states (`exploration.runs_of_a_study`)."""
    base = Path(folder)
    return _state_of(base, _read_json(base / "batch_manifest.json"),
                     _read_json(base / "manifest.json"))


def _state_of(base: Path, batch: Any, manifest: Any) -> str:
    try:
        from fastmdxplora.simulation.resume import _still_running

        if _still_running(base):
            return "running"
    except Exception:  # noqa: BLE001 - a record, not a verdict
        pass
    if not isinstance(batch, dict):
        # The live record, before the manifest: the manifest is written when
        # a run ends, so a run cut short has none, or the one before it. A
        # record still saying the run goes on is believed unless the run has
        # ended without saying so (telemetry.status_as_it_stands).
        status = _status_of(base)
        if status.get("between_rounds"):
            # Its study extends it again in its next round: the Manifest's
            # phases, all ok, say how its pieces so far ended.
            return "running"
        if status.get("recorded_status") and status.get("status") == "completed":
            return "completed"
        said = str(status.get("status") or "").lower()
        if said == "interrupted":
            return "interrupted"
        if said in ("running", "starting", "paused"):
            return "running"
        # A run whose record says it failed or was stopped, as its Overview
        # says: a failure found after the run's own end (the GUI's check of
        # what a run left) is recorded there, and the study was listed
        # Completed beside an Overview saying "Simulation failed".
        if said in ("failed", "stopped"):
            return said
    if isinstance(batch, dict):
        from fastmdxplora.gui.exploration import runs_of_a_study

        runs = runs_of_a_study(base) or []
        states = [run["state"] for run in runs]
        if states and all(state == "completed" for state in states):
            return "completed"
        if any(state == "failed" for state in states):
            return "stopped" if _stopped_in(batch) else "failed"
        # A run stopped or killed in a later piece (its study's rounds).
        if any(state == "stopped" for state in states):
            return "stopped"
        # A run going on, as its record and its process say
        # (`runs_of_a_study` calls one whose process is gone interrupted).
        if any(state == "running" for state in states):
            return "running"
        return "incomplete"
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    if not isinstance(phases, list) or not phases:
        # No phase recorded: what its other records say. Analysed (a
        # trajectory analysed here, or a run whose Manifest is missing,
        # analysed after it simulated); simulated and no more, not known;
        # "not started" said the opposite beside its length and means.
        from fastmdxplora import record_access

        if record_access.is_file(base / "analysis" / "analysis_manifest.json"):
            return "analysed"
        if _simulated(base):
            return "not known"
        return "not started"
    for phase in phases:
        if isinstance(phase, dict) and phase.get("status") == "error":
            code = str((phase.get("refusal") or {}).get("code") or "")
            return "stopped" if code == "simulation.run.stopped" else "failed"
    return "completed"


def _runs_count(batch: dict[str, Any]) -> int:
    planned = batch.get("planned") or []
    return int(batch.get("n_runs") or len(planned) or 0)


def _ensemble_means(base: Path, of: int | None = None) -> list[dict[str, Any]]:
    """The means of a study of replicas, as the runs' table gives them
    (`runs_compared`): its runs' records together (`batch.aggregate`, read
    as they stand, nothing written), each quantity's mean of means with its
    error and the spread, or without an error and why, by the one
    derivation (`runs_compared.mean_of_replicas`). The study's
    `members.json` is not read: written when its runs ended, it can say
    less than their records now do, and a study need not keep one. A
    mean of fewer replicas than the study's ``of`` runs keeps ``of``, and
    says so."""
    from fastmdxplora.batch.aggregate import aggregate_members
    from fastmdxplora.gui.report_dashboard import unit_of
    from fastmdxplora.gui.runs_compared import mean_of_replicas

    summary = aggregate_members(base, share=False)
    if summary.get("refused") or not summary.get("replicas"):
        return []
    analyses = summary.get("analyses") if isinstance(summary.get("analyses"), dict) else {}
    out = []
    for name in _in_order(analyses):
        found = analyses.get(name)
        together = mean_of_replicas(found, of=of) if isinstance(found, dict) else None
        if together is None:
            continue
        members = [m for m in found.get("members") or [] if isinstance(m, dict)]
        out.append({"analysis": name, "label": _label_of(name), "mean": together["mean"],
                    "error": together["error"], "spread": together["spread"],
                    "replicas": together["runs"],
                    "unit": unit_of(name, members[0]) if members else "",
                    "withheld": together["withheld"], "noted": together["noted"],
                    **({"of": together["of"]} if "of" in together else {})})
    return out


#: Sweep axes that can make one run of a study longer than another: where
#: one is swept, a run not begun is not taken to be as long as those begun.
_LENGTH_AXES = ("duration", "steps", "timestep", "equilibration", "nvt", "npt", "stop_when")

#: How many of a run's newest samples the page's rate is read from: as many
#: as the Overview's time left reads (run-progress.js `stepsPerSecond`).
_SAMPLES_FOR_THE_RATE = 12


def _how_far(base: Path, batch: Any, card: dict[str, Any],
             seen: dict[str, Any] | None = None, config: Any = None) -> dict[str, Any]:
    """How much the study has simulated and was to, as each run's Overview
    reads it (`simulated_time.simulated_times`): ``simulated_ns`` (its
    production, every run's), ``planned_ns`` (every run's planned, begun or
    not; not said where a run not begun cannot be told), ``lengths`` (each
    run's production so far, for a study of several), and ``pace``: what
    the page works out its progress and time left from, as the Overview and
    the status bar do (run-progress.js): each run's steps taken and planned,
    and for a run going its record and its newest samples; how the study
    runs them (``mode``, ``workers``, from what its runner recorded in its
    ``batch_manifest.json``, else its Config's ``execution``). For
    a study of one run, what was read of it is put in ``seen`` (``times``,
    ``running``)."""
    from fastmdxplora import record_access
    from fastmdxplora.gui.overview_view import thinned_metrics
    from fastmdxplora.gui.server import MOST_CHART_SAMPLES
    from fastmdxplora.gui.simulated_time import simulated_times
    from fastmdxplora.gui.telemetry import read_metrics

    of_runs = isinstance(batch, dict)
    runs = base / "runs"
    if of_runs:
        named = [str(p.get("run_id")) for p in batch.get("planned") or []
                 if isinstance(p, dict) and p.get("run_id")]
        if not named and record_access.is_dir(runs):
            named = [p.name for p in sorted(record_access.iterdir(runs))
                     if record_access.is_dir(p)]
        folders = [runs / name for name in named]
    else:
        folders = [base]
    done = planned = 0.0
    known = True
    lengths: list[float] = []
    paced: list[dict[str, Any]] = []
    begun: list[dict[str, Any]] = []
    for folder in folders:
        if not record_access.is_dir(folder / "simulation"):
            paced.append({"state": "waiting"})
            continue
        status = _status_of(folder)
        times = simulated_times(folder, status, wall=False) or {}
        going = str(status.get("status") or "").lower() in ("running", "starting", "paused")
        if seen is not None and folder == base:
            seen.update(times=times, running=going)
        to_run = _finite(times.get("production_planned_ns"))
        if to_run is None or times.get("production_not_read"):
            known = False
            paced.append({"state": "not known"})
            continue
        so_far = _finite(times.get("production_ns")) or 0.0
        done += so_far
        planned += to_run
        lengths.append(round(so_far, 6))
        run = {"state": "going" if going else "ended",
               "steps": _finite(status.get("total_planned_steps")),
               "taken": _finite(status.get("current_step")),
               "production_ns": to_run}
        if going:
            # As the status bar reads it: only what the record says.
            run["status"] = {key: status[key] for key in
                             ("current_step", "total_planned_steps", "progress_percent")
                             if status.get(key) is not None}
            rows = thinned_metrics(read_metrics(folder, limit=None), MOST_CHART_SAMPLES)
            run["samples"] = [{"step": row.get("step"), "timestamp": row.get("timestamp")}
                              for row in rows][-_SAMPLES_FOR_THE_RATE:]
        paced.append(run)
        begun.append(run)
    # A run not begun: as long as those begun, where they are alike and
    # nothing that sets a run's length is swept; else not known.
    swept = [str(axis) for axis in (batch.get("sweep") or {})] if of_runs else []
    alike = (begun and not any(word in axis for axis in swept for word in _LENGTH_AXES)
             and len({(r["steps"], r["production_ns"]) for r in begun}) == 1)
    for run in paced:
        if run["state"] != "waiting":
            continue
        if alike:
            run["steps"], run["production_ns"] = begun[0]["steps"], begun[0]["production_ns"]
            planned += begun[0]["production_ns"]
        else:
            run["steps"] = run["production_ns"] = None
            known = False
    out: dict[str, Any] = {}
    if begun:
        out["simulated_ns"] = round(done, 6)
        if known:
            out["planned_ns"] = round(planned, 6)
    if of_runs:
        out["lengths"] = lengths
        # Every run with a record of its simulation began, its length
        # given or not.
        out["begun"] = sum(1 for run in paced if run["state"] != "waiting")
    if card.get("state") == "running" and any(r["state"] == "going" for r in paced):
        # How the runner ran them, as it recorded (the workers it resolved
        # where the Config set only ``mode: parallel``); else as the Config
        # asks.
        recorded = batch.get("execution") if of_runs else None
        execution = config.get("execution") if isinstance(config, dict) else None
        execution = (recorded if isinstance(recorded, dict) and recorded.get("mode")
                     else execution if isinstance(execution, dict) else {})
        devices = execution.get("devices")
        workers = execution.get("workers") or (len(devices) if isinstance(devices, list) else None)
        mode = execution.get("mode") or ("parallel" if workers else "sequential")
        out["pace"] = {"runs": paced, "mode": mode if of_runs else "one",
                       "workers": int(workers) if isinstance(workers, (int, float)) else None}
    return out


def _stopped_in(batch: dict[str, Any]) -> bool:
    return any(isinstance(run, dict) and run.get("error_type") == "Stopped"
               for run in batch.get("runs") or [])


def _when(base: Path, manifest: Any) -> str:
    phases = manifest.get("phases") if isinstance(manifest, dict) else None
    if isinstance(phases, list):
        for phase in phases:
            if isinstance(phase, dict) and phase.get("started_at"):
                return str(phase["started_at"])
    try:
        stamp = os.path.getmtime(base)
    except OSError:
        return ""
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")


#: The records a study's activity changes: its run, its analyses and report,
#: and what the person keeps with it (tags, views, selections). The Agent's
#: conversations are looked at as a folder, each file in it.
_ACTIVE_RECORDS = (
    "manifest.json", "batch_manifest.json", "simulation/live_status.json",
    "analysis/analysis_manifest.json", "report/report.md", "study_tags.json",
    "viewer_views.json", "viewer_selections.json",
)
_CONVERSATIONS = Path("agent") / "conversations"


def _last_active(base: Path, *, of_runs: bool = False) -> str:
    """When the study was last active, by its newest record, or ''. Its
    records through `record_access`; its conversations, which are not
    read, by their times, in its own folder only. A study of several runs
    was last active when its newest run was: a run going writes its own
    records, not its study's, and the study said "1 h ago" beside its run's
    "Just now"."""
    from fastmdxplora import record_access

    newest = _newest_record(base)
    if of_runs and record_access.is_dir(base / "runs"):
        for run in record_access.iterdir(base / "runs"):
            if record_access.is_dir(run):
                newest = max(newest, _newest_record(run))
    if not newest:
        return ""
    return datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds")


def _newest_record(base: Path) -> float:
    """The time of a study's newest record of what was done with it (0 for
    none): `_last_active`'s."""
    from fastmdxplora import record_access

    newest = 0.0
    for name in _ACTIVE_RECORDS:
        try:
            newest = max(newest, record_access.stat(base / name).st_mtime)
        except OSError:
            continue
    kept = base / _CONVERSATIONS
    try:
        within = (not any(p.is_symlink() for p in (base / _CONVERSATIONS.parts[0], kept))
                  and kept.is_dir())
        if within:
            with os.scandir(kept) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False):
                        newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
    except OSError:
        pass
    return newest


def _lifted(config: Any) -> Any:
    """A study of several runs writes its phase settings under `options`;
    lifted beside the rest, so one reading serves both."""
    if isinstance(config, dict) and isinstance(config.get("options"), dict):
        return {**config, **config["options"]}
    return config


def _read_json(path: Path) -> Any:
    # Through `record_access`: held to its study where the read is (a study
    # of runs' state said on `/api/status`), as pathlib elsewhere.
    from fastmdxplora import record_access

    try:
        return json.loads(record_access.read_text(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_yaml(path: Path) -> Any:
    """A YAML record, read once within a card's read (`_once`)."""
    import yaml

    from fastmdxplora import record_access

    def read() -> Any:
        try:
            return yaml.load(record_access.read_text(path, encoding="utf-8"),
                             Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
        except (OSError, yaml.YAMLError):
            return None

    return _once(("yaml", str(path)), read)


#: What a card's read has read once, for the rest of that read (a study's
#: Config, read for its head, its length and its molecule; a run's record
#: as it stands, read for its state and how far it got).
_READ_ONCE: ContextVar[dict | None] = ContextVar("card_read_once", default=None)


def _once(key: tuple, read: Any) -> Any:
    """``read()``, or within a card's read its answer of the first time,
    the records it refused then named again."""
    from fastmdxplora import record_access

    kept = _READ_ONCE.get()
    if kept is None:
        return read()
    if key in kept:
        value, refused = kept[key]
        record_access.note_refused(refused)
        return value
    with record_access.refusals_noted() as refused:
        value = read()
    kept[key] = (value, frozenset(refused))
    return value


def _status_of(folder: Path) -> dict[str, Any]:
    """`telemetry.status_as_it_stands`, read once within a card's read."""
    from fastmdxplora.gui.telemetry import status_as_it_stands

    return _once(("status", str(folder)), lambda: status_as_it_stands(folder))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None
