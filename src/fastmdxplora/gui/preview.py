"""What the builder's study will build and cost, said before it runs.

The builder wrote a config and ran it, and what the settings amounted to was
learned from setup's log: the box, the particle count, and so the time. The
structure and the settings decide most of it, so the builder now says it
while they are still being chosen: the system setup will build
(:mod:`fastmdxplora.setup.estimate`), how long the study will take on this
machine where it has been timed, and what is worth knowing about the
structure under these settings (:mod:`fastmdxplora.advisories`).
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.refusals import StudyError

#: A PDB identifier, as setup recognises one.
PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


def system_preview(state: dict[str, Any], *,
                   path_for: Callable[[Any], str | None] | None = None) -> dict[str, Any]:
    """The system, the time and the advisories for the builder's form state.

    ``path_for`` is the server's rule for a path a request names (inside the
    workspace, when hosted); ``None`` from it means refused.
    """
    from fastmdxplora.gui.config_builder import build_config

    try:
        config = build_config(state)
    except Exception as exc:  # noqa: BLE001 - said under the form, as the config route says it
        return {"ok": False, "reason": str(exc)}
    return preview_of_config(config, path_for=path_for)


def preview_of_config(config: dict[str, Any], *,
                      path_for: Callable[[Any], str | None] | None = None) -> dict[str, Any]:
    """The same, for a config rather than the builder's form: what the
    Agent's `preview_setup` tool reads."""
    from fastmdxplora.advisories import advise
    from fastmdxplora.setup.estimate import estimate_system
    from fastmdxplora.structure_info import count_structure

    systems = config.get("systems") or []
    given = str((systems[0] if systems else {}).get("system") or "").strip()
    if not given:
        return {"ok": False, "reason": "Name a structure to see what setup will build from it."}
    try:
        structure = structure_file(given, path_for)
    except StudyError as exc:
        if exc.code == "environment.service.unreachable":
            return {"ok": False, "reason": (
                f"{given.upper()} could not be fetched from the PDB from here, so "
                "what setup will build from it cannot be said yet.")}
        return {"ok": False, "reason": str(exc)}
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}

    setup = dict(config.get("setup") or {})
    try:
        estimate = estimate_system(structure, setup)
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}

    settings = dict(setup)
    settings.update(config.get("simulation") or {})
    advisories = [
        {"setting": a.setting, "summary": a.summary, "detail": a.detail, "remedy": a.remedy}
        for a in advise(_as_given(count_structure(structure), given), settings)
        # The box is said by the estimate, from the solute's bounding sphere
        # as OpenMM sizes it; the advisory's reckoning from the longest
        # extent could say otherwise beside it.
        if a.setting != "solvent_padding_nm"
    ]
    return {
        "ok": True,
        "system": given,
        "estimate": estimate.as_record(),
        # What is kept, copies included, for the picture beside the numbers.
        "drawing": estimate.drawing(),
        # The residues whose protonation state the study may set, for the
        # form's rows and for a click on one in the picture.
        "titratable": titratable_residues(estimate.atoms),
        "time": _time(config, estimate.particles),
        "advisories": advisories,
    }


#: How close a metal must be to a side chain's nitrogen or oxygen to be said
#: beside the residue: a coordinating Zn-N is near 2.1 Angstrom and a Ca-O
#: near 2.4, and 3 is past both without reaching a second shell.
METAL_REACH_ANGSTROM = 3.0

#: Most residues listed, so a very large assembly does not send thousands.
MOST_TITRATABLE = 2000

_BACKBONE = frozenset({"N", "CA", "C", "O", "OXT"})


def titratable_residues(atoms: list[Any]) -> list[dict[str, Any]]:
    """Every residue of the kept structure that `setup.residue_states` can
    set, as it names them (chain and number), with the states each takes.

    Where a structural metal sits within reach of one's side chain, which
    atom and how far, since a histidine that holds a metal holds it by a
    nitrogen with no hydrogen on it. That is a distance in the structure,
    said as one; which state follows is the person's to decide.
    """
    import math

    from fastmdxplora.setup.pdbfix import RESIDUE_STATES
    from fastmdxplora.setup.prepare import STRUCTURAL_METALS

    metals = [atom for atom in atoms
              if atom.record == "HETATM" and atom.resname.upper() in STRUCTURAL_METALS]
    found: dict[str, dict[str, Any]] = {}
    for atom in atoms:
        name = atom.resname.upper()
        if atom.record != "ATOM" or name not in RESIDUE_STATES:
            continue
        key = f"{atom.chain}:{atom.resseq}{atom.icode.strip()}"
        entry = found.get(key)
        if entry is None:
            if len(found) >= MOST_TITRATABLE:
                continue
            entry = found[key] = {"key": key, "resname": name, "chain": atom.chain,
                                  "number": atom.resseq, "states": list(RESIDUE_STATES[name]),
                                  "near": None, "_reach": METAL_REACH_ANGSTROM}
        if atom.name in _BACKBONE or atom.element.upper() not in ("N", "O"):
            continue
        for metal in metals:
            distance = math.dist(atom.xyz, metal.xyz)
            if distance <= entry["_reach"]:
                entry["_reach"] = distance
                entry["near"] = (f"{atom.name} is {distance:.1f} \u00c5 from "
                                 f"{metal.resname} {metal.chain}:{metal.resseq}")
    listed = []
    for entry in found.values():
        entry.pop("_reach")
        listed.append(entry)
    return listed


def structure_file(given: str, path_for: Callable[[Any], str | None] | None = None) -> Path:
    """The file to read for what the builder names: a path as given, or a
    PDB identifier fetched once from the PDB and kept."""
    if PDB_ID.match(given) and not Path(given).exists():
        return _fetched(given.upper())
    named = path_for(given) if path_for is not None else given
    if named is None:
        raise StudyError(f"{given} is outside the workspace.",
                         code="environment.path.not_found", path=given)
    path = Path(named).expanduser()
    if not path.is_file():
        raise StudyError(f"There is no file at {given}.",
                         code="environment.path.not_found", path=given)
    if path.suffix.lower() in (".cif", ".mmcif", ".pdbx"):
        return _as_pdb(path)
    return path


def given_by_identifier(given: Any) -> str | None:
    """The PDB identifier a structure was named by, or None for a file."""
    text = str(given or "").strip()
    return text.upper() if PDB_ID.match(text) and not Path(text).exists() else None


def _as_given(counted: dict[str, Any], given: Any) -> dict[str, Any]:
    """What the structure holds, and the entry it was fetched from: the
    advisories read the file the identifier was fetched into, which says
    nothing of where it came from."""
    entry = given_by_identifier(given)
    return {**counted, "entry": entry} if entry else dict(counted)


def _cache() -> Path:
    from fastmdxplora.setup.ccd import cache_dir

    target = cache_dir().parent / "structures"
    target.mkdir(parents=True, exist_ok=True)
    return target


#: One fetch of an entry at a time. The builder's preview and its estimate
#: ask for the structure together as an identifier is typed, and each found
#: it missing and fetched it (5O3L twice in a second); the second could read
#: the first's file half written, since it was written where it is read.
_FETCHING: dict[str, threading.Lock] = {}
_FETCHING_GUARD = threading.Lock()


def _fetched(pdb_id: str) -> Path:
    from fastmdxplora.setup.pipeline import _fetch_pdb_from_rcsb

    target = _cache() / f"{pdb_id}.pdb"
    if target.is_file():
        return target
    with _FETCHING_GUARD:
        lock = _FETCHING.setdefault(pdb_id, threading.Lock())
    with lock:
        if not target.is_file():
            # Written beside it and moved into place whole.
            partial = target.with_name(f".{pdb_id}.{os.getpid()}.{threading.get_ident()}.part")
            try:
                _fetch_pdb_from_rcsb(pdb_id, partial)
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
                partial.with_suffix(".cif").unlink(missing_ok=True)
    return target


def _as_pdb(cif: Path) -> Path:
    """An mmCIF file's records as PDB, as setup reads it, kept by content."""
    from fastmdxplora.setup.mmcif import to_pdb_lines

    digest = hashlib.sha256(cif.read_bytes()).hexdigest()[:16]
    target = _cache() / f"{cif.stem}-{digest}.pdb"
    if not target.is_file():
        target.write_text("\n".join(to_pdb_lines(cif)) + "\n", encoding="utf-8")
    return target


def _time(config: dict[str, Any], particles: int) -> dict[str, Any]:
    """How long the study would take on this machine, every run of it, or
    why that cannot be said."""
    from fastmdxplora.agent.staged import _runs_of
    from fastmdxplora.cost import estimate_runs, load_calibration

    runs, _ = _runs_of(config, Path("."))
    simulation = config.get("simulation") or {}
    precision = str(simulation.get("precision") or "mixed")
    platform = ""
    try:
        # A machine never timed is said to be without asking OpenMM which
        # platform it would choose: asking tries each, which took over a
        # minute on a loaded machine and held the answer up for nothing.
        if load_calibration() is None:
            raise StudyError("This machine has not been measured.",
                             code="environment.calibration.absent")
        platform, precision = _platform(str(simulation.get("platform") or "auto"), precision)
        estimate = estimate_runs([(particles, steps) for _, _, steps in runs],
                                 platform_name=platform, precision=precision)
    except StudyError as exc:
        reasons = {
            "environment.calibration.absent": (
                "This machine has not been timed yet. The first study given a "
                "budget times it on its own prepared system, and from then on "
                "the time is said here."),
            "environment.calibration.stale": (
                f"This machine was timed on another platform or precision than "
                f"{platform or 'this study'} {precision}; a study given a budget "
                "times it again."),
        }
        return {"ok": False, "code": exc.code, "reason": reasons.get(exc.code, str(exc))}
    return {"ok": True, "seconds": estimate.seconds, "runs": estimate.runs,
            "steps": estimate.steps, "platform": platform, "precision": precision,
            "text": str(estimate)}


@functools.lru_cache(maxsize=8)
def _platform(requested: str, precision: str) -> tuple[str, str]:
    """The platform the simulation phase would choose, asked once per server:
    choosing tries each platform, which is too slow to repeat per keystroke."""
    from fastmdxplora.agent.staged import _what_it_will_run_on

    return _what_it_will_run_on({"simulation": {"platform": requested, "precision": precision}},
                                "", "")
