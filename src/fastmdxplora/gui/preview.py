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
    # The first system's own setup settings, over the study's, as setup
    # applies them to that system.
    first = systems[0] if systems and isinstance(systems[0], dict) else {}
    if isinstance(first.get("setup"), dict):
        setup.update(first["setup"])
    try:
        estimate = estimate_system(structure, setup)
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}

    settings = dict(setup)
    settings.update(config.get("simulation") or {})
    counted = count_structure(structure)
    # Each system's own atoms, where its structure can be read here
    # without fetching it: a study of several systems is not priced as its
    # first in every run.
    estimates = _estimates_of(config, estimate, path_for)
    time = _time(config, estimate.particles, estimates=estimates)
    advisories = [
        {"setting": a.setting, "summary": a.summary, "detail": a.detail, "remedy": a.remedy}
        for a in advise(_as_given(counted, given), settings)
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
        "time": time,
        # What it costs, as a compiler's diagnostics: its speed, time, disk
        # and GPU memory here, each a note, a warning or an error.
        "cost": study_cost(config, estimate, time, path_for=path_for, estimates=estimates),
        "advisories": advisories,
        # What the file holds before anything is chosen or discarded: the
        # chains to choose among, its models, its heterogens.
        "structure": facts_of(counted),
        "forcefield": _forcefield_said(setup),
        # Which analyses the study leaves nothing for, with what the
        # structure holds: its models, and the ligands setup simulates.
        "not_applicable": _not_applicable(config, counted, estimate),
    }


def _not_applicable(config: dict[str, Any], counted: dict[str, Any], estimate: Any) -> dict[str, str]:
    from fastmdxplora.gui.applicable import not_applicable

    return not_applicable(config, {"models": counted.get("models") or 1,
                                   "ligands": list(getattr(estimate, "ligands", []) or [])})


def facts_of(counted: dict[str, Any]) -> dict[str, Any]:
    """What a structure file holds, as the builder says it beside the
    system: its models, every chain, its residues and its heterogens."""
    if not counted.get("valid"):
        return {"ok": False, "reason": "The structure could not be read."}
    return {
        "ok": True,
        "models": int(counted.get("models") or 1),
        "chains": list(counted.get("all_chains") or counted.get("chains") or []),
        "residues": int(counted.get("protein_residues") or 0),
        "ligands": sorted(set(counted.get("ligand_resnames") or [])),
        "ions": int(counted.get("ions") or 0),
        "waters": int(counted.get("water_residues") or 0),
    }


def structure_facts(given: Any, *,
                    path_for: Callable[[Any], str | None] | None = None) -> dict[str, Any]:
    """The same for one structure named in the form's list of systems,
    without estimating its box: read, not prepared."""
    from fastmdxplora.structure_info import count_structure

    named = str(given or "").strip()
    if not named:
        return {"ok": False, "reason": "No structure named."}
    try:
        structure = structure_file(named, path_for)
    except StudyError as exc:
        if exc.code == "environment.service.unreachable":
            return {"ok": False, "reason": f"{named.upper()} could not be fetched from the PDB from here."}
        return {"ok": False, "reason": str(exc)}
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}
    return {**facts_of(count_structure(structure)), "system": named}


def _forcefield_said(setup: dict[str, Any]) -> dict[str, Any]:
    """The force field a study will use, as `auto` resolves it here."""
    from fastmdxplora.setup.forcefields import resolve_forcefield

    if setup.get("force_field"):
        return {"name": "files", "description": "The OpenMM force-field files the study names."}
    try:
        choice = resolve_forcefield(setup.get("forcefield"))
    except Exception:  # noqa: BLE001 - a name the validator refuses says itself
        return {"name": str(setup.get("forcefield")), "description": ""}
    # The water's own name where the box is filled with another's geometry
    # (TIP3P-FB in a TIP3P box), for the preview's Water row to say.
    return {"name": choice.name, "description": choice.description,
            "water_model": choice.water_model, "water_name": choice.water_name}


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


def _time(config: dict[str, Any], particles: int, *,
          estimates: dict[str, Any] | None = None) -> dict[str, Any]:
    """How long the study would take on this machine, every run of it, or
    why that cannot be said. Each run at its own system's particles
    (``estimates``, by system; one not known leaves its runs out, and they
    are named in ``not_worked_out``) and its own settings."""
    from fastmdxplora.cost import estimate_runs, load_calibration, total_steps

    plan = _run_plan(config)
    known = [(_particles_of(system, particles, estimates), total_steps(simulation))
             for system, simulation in plan]
    missing = _missing(plan, estimates)
    runs = [(p, steps) for p, steps in known if p is not None]
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
        if not runs:
            return {"ok": False, "reason": "No system of the study is read here yet.",
                    "not_worked_out": missing}
        estimate = estimate_runs(runs, platform_name=platform, precision=precision)
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
        return {"ok": False, "code": exc.code, "reason": reasons.get(exc.code, str(exc)),
                "not_worked_out": missing}
    return {"ok": True, "seconds": estimate.seconds, "runs": estimate.runs,
            "steps": estimate.steps, "platform": platform, "precision": precision,
            "text": str(estimate), "not_worked_out": missing}


def _run_plan(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Each run the study makes, by its system and its own simulation
    settings, expanded exactly as the batch layer expands them
    (`batch.sweep.expand_runs`, base under system under sweep), a sweep of
    one value included; an umbrella study's pull, where it seeds the
    windows, a run of its own (as `agent.staged._runs_of` counts it)."""
    import copy

    study = config.get("simulation") or {}
    first = (_system_ids(config) or [""])[0]
    try:
        from fastmdxplora.batch.sweep import expand_runs, normalize_sweep, normalize_systems
        from fastmdxplora.config import phase_options, validate_config
        from fastmdxplora.simulation.umbrella import plan_from_expanded

        data = copy.deepcopy(config)
        validate_config(data, require_systems=True)
        specs = expand_runs(
            systems=normalize_systems(data["systems"]),
            sweep=normalize_sweep(data["sweep"]) if data.get("sweep") is not None else None,
            base_options=phase_options(data))
        plan = [(str(spec.system_id), spec.options.get("simulation") or {}) for spec in specs]
        if plan and plan_from_expanded(data) is not None:
            seeded = (plan[0][1].get("umbrella") or {}).get("seed_from")
            if study.get("steered") and not seeded:
                plan.append((plan[0][0], {k: v for k, v in study.items()
                                          if k not in ("umbrella", "steered")}))
        if plan:
            return plan
    except Exception:  # noqa: BLE001 - the study as one run, as setup reads it
        pass
    return [(first, study)]


def _system_ids(config: dict[str, Any]) -> list[str]:
    """Each system's id as the batch layer names it (`normalize_systems`):
    its own, or `s1`, `s2` by its place."""
    from fastmdxplora.batch.sweep import default_system_id

    systems = [s for s in (config.get("systems") or []) if isinstance(s, dict)]
    return [str(s.get("id") or default_system_id(i)) for i, s in enumerate(systems)]


def _particles_of(system: str, particles: int, estimates: dict[str, Any] | None) -> int | None:
    if estimates is None or system not in estimates:
        return particles if estimates is None or not estimates else None
    found = estimates[system]
    return int(found.particles) if found is not None else None


def _missing(plan: list[tuple[str, dict[str, Any]]],
             estimates: dict[str, Any] | None) -> list[str]:
    """The systems whose runs are not worked out: their atoms not known."""
    if not estimates:
        return []
    return list(dict.fromkeys(system for system, _ in plan
                              if system in estimates and estimates[system] is None))


def _estimates_of(config: dict[str, Any], first: Any,
                  path_for: Callable[[Any], str | None] | None) -> dict[str, Any]:
    """What setup builds of each system of the study, by its id: the first's
    as worked out already, each other's from its own structure and setup
    settings, or None where its structure is not read here (an identifier
    not yet fetched, a file not there): its runs are then said not worked
    out, never priced as the first's."""
    from fastmdxplora.setup.estimate import estimate_system

    systems = [s for s in (config.get("systems") or []) if isinstance(s, dict)]
    found: dict[str, Any] = {}
    for index, (name, system) in enumerate(zip(_system_ids(config), systems)):
        if index == 0:
            found[name] = first
            continue
        if name in found:
            continue
        given = str(system.get("system") or "").strip()
        setup = dict(config.get("setup") or {})
        if isinstance(system.get("setup"), dict):
            setup.update(system["setup"])
        entry = given_by_identifier(given)
        if entry is not None and not (_cache() / f"{entry}.pdb").is_file():
            # An identifier not fetched yet: not fetched here for a cost.
            found[name] = None
            continue
        try:
            found[name] = estimate_system(structure_file(given, path_for), setup)
        except Exception:  # noqa: BLE001 - not worked out, and said so
            found[name] = None
    return found


@functools.lru_cache(maxsize=8)
def _platform(requested: str, precision: str) -> tuple[str, str]:
    """The platform the simulation phase would choose, asked once per server:
    choosing tries each platform, which is too slow to repeat per keystroke."""
    from fastmdxplora.agent.staged import _what_it_will_run_on

    return _what_it_will_run_on({"simulation": {"platform": requested, "precision": precision}},
                                "", "")


# ---------------------------------------------------------------------------
# What the study costs, line by line
# ---------------------------------------------------------------------------

#: Atoms in one water molecule of each model, as setup builds it: what the
#: default `save_selection` ("not water") leaves out of the trajectory.
WATER_SITES = {"tip3p": 3, "tip3pfb": 3, "spce": 3, "tip4pew": 4, "tip4pfb": 4, "opc": 4,
               "tip5p": 5, "opc3": 3}

#: A DCD frame: three 4-byte coordinates an atom, each axis a Fortran record
#: (8 bytes of markers), and the unit cell (six doubles, 8 more).
DCD_BYTES_PER_ATOM = 12
DCD_BYTES_PER_FRAME = 3 * 8 + 6 * 8 + 8

#: Free disk below this share of what is free is a warning: the study would
#: leave the disk all but full.
DISK_WARNING_SHARE = 0.8

#: The GPUs as last read, and when: `nvidia-smi` is asked at most this often
#: while a form is written.
_ROOM_SECONDS = 30.0
_room: dict[str, Any] = {"at": -1.0, "room": None}
_ROOM_LOCK = threading.Lock()


def _gpus_now() -> Any:
    import time as clock

    from fastmdxplora.gpu_here import room_here

    with _ROOM_LOCK:
        now = clock.monotonic()
        if _room["at"] < 0 or now - _room["at"] > _ROOM_SECONDS:
            try:
                _room["room"] = room_here()
            except Exception:  # noqa: BLE001 - GPUs not read are said as such
                _room["room"] = None
            _room["at"] = now
        return _room["room"]


def _three_figures(value: float) -> str:
    """A rate to three significant figures as a person reads it: 1,230 and
    56.8, not 1.23e+03."""
    import math

    if not math.isfinite(value) or value <= 0:
        return f"{value:g}"
    places = 2 - math.floor(math.log10(value))
    return f"{round(value, places):,.{max(0, places)}f}"


def _size(n_bytes: float) -> str:
    """Bytes as a person reads them: 820 MB, 3.4 GB."""
    for unit, size in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if n_bytes >= size:
            value = n_bytes / size
            return f"{value:.1f} {unit}" if value < 10 else f"{value:,.0f} {unit}"
    return f"{n_bytes:.0f} bytes"


def _frames_planned(simulation: dict[str, Any]) -> tuple[int, bool]:
    """The production frames one run writes, and whether that is a ceiling
    (a study run until it is determined may stop sooner). The interval is
    the runner's own (`trajectory_interval_for` of the first piece's
    production, as `run_simulation` picks it, unless the Config sets one),
    and every continuation keeps it (`resume`), so a study run in pieces up
    to a ceiling writes the ceiling's production at the first piece's
    interval."""
    from fastmdxplora.simulation.lengths import DEFAULT_TIMESTEP_FS, steps_in
    from fastmdxplora.simulation.runner import plan_stages, trajectory_interval_for

    def number(key: str) -> Any:
        value = simulation.get(key)
        return None if value is None or isinstance(value, bool) else value

    timestep = float(simulation.get("timestep_fs") or DEFAULT_TIMESTEP_FS)
    first = plan_stages(
        duration_ns=number("duration_ns"), timestep_fs=timestep,
        nvt_steps=number("nvt_steps"), npt_steps=number("npt_steps"),
        production_steps=number("production_steps"),
        nvt_duration_ns=number("nvt_duration_ns"),
        npt_duration_ns=number("npt_duration_ns"))["production_steps"]
    interval = number("trajectory_interval_steps")
    interval = int(interval) if interval else trajectory_interval_for(first)
    stop_when = simulation.get("stop_when")
    ceiling = stop_when.get("max_duration_ns") if isinstance(stop_when, dict) else None
    if isinstance(ceiling, (int, float)) and not isinstance(ceiling, bool) and ceiling > 0:
        return steps_in(ceiling, timestep) // max(1, interval), True
    return first // max(1, interval), False


def _simulates(config: dict[str, Any]) -> bool:
    included = config.get("include_phase")
    excluded = config.get("exclude_phase") or []
    if included:
        return "simulation" in included
    return "simulation" not in excluded


def _where_written(config: dict[str, Any],
                   path_for: Callable[[Any], str | None] | None) -> Path | None:
    """The nearest folder that exists on the way to where the study is
    written, for its free disk; ``None`` where a hosted page may not name it."""
    given = str(config.get("output") or "").strip() or "."
    if path_for is not None:
        allowed = path_for(given)
        if allowed is None:
            return None
        given = allowed
    folder = Path(given).expanduser()
    folder = folder if folder.is_absolute() else Path.cwd() / folder
    for here in (folder, *folder.parents):
        if here.is_dir():
            return here
    return None


def study_cost(config: dict[str, Any], estimate: Any, time: dict[str, Any], *,
               path_for: Callable[[Any], str | None] | None = None,
               estimates: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """What the study costs here, as the builder shows it beside its summary:
    the speed one run makes, the time every run takes, the disk its
    trajectories take against what is free where it is written, and the GPU
    memory one run needs against what the GPUs have free. Each line is a
    ``note``, a ``warning`` (it fits, barely) or an ``error`` (it does not
    fit), with ``kind`` one of speed, time, disk and gpu."""
    from fastmdxplora.cost import load_calibration
    from fastmdxplora.simulation.lengths import DEFAULT_TIMESTEP_FS

    if not _simulates(config):
        return [{"kind": "time", "level": "note",
                 "said": "Nothing is simulated, so no time, trajectory or GPU memory is counted."}]
    simulation = config.get("simulation") or {}
    particles = int(estimate.particles)
    # Every run the study makes, timed or not, each from its own system and
    # settings: the disk does not wait on the calibration.
    plan = _run_plan(config)
    missing = _missing(plan, estimates)
    runs = len(plan)
    lines: list[dict[str, str]] = []

    # The speed of one run, from the timing the time is worked out from.
    calibration = load_calibration() if time.get("ok") else None
    if calibration is not None and particles > 0:
        timestep = float(simulation.get("timestep_fs") or DEFAULT_TIMESTEP_FS)
        per_day = timestep * 1e-6 * 86400.0 / (calibration.seconds_per_particle_step * particles)
        platform = str(time.get("platform") or "this machine")
        # A precision is the GPU's to choose; the CPU platform has its own.
        precision = (f" {time.get('precision')}" if time.get("precision")
                     and platform not in ("CPU", "Reference", "this machine") else "")
        lines.append({"kind": "speed", "level": "note", "said": (
            f"about {_three_figures(per_day)} ns/day on {platform}{precision}, for about {particles:,} "
            f"particles" + (f" ({plan[0][0]}, the first system)" if len(estimates or {}) > 1 else "")
            + f", from this machine's timing of {str(calibration.measured_at)[:10]}")})
    else:
        why = str(time.get("reason") or "this machine has not been timed").rstrip(".")
        lines.append({"kind": "speed", "level": "note",
                      "said": "ns/day not known: " + why[:1].lower() + why[1:] + "."})

    unread = _not_worked_out(missing)
    if time.get("ok"):
        counted = int(time.get("runs") or runs)
        lines.append({"kind": "time", "level": "note", "said": (
            f"about {_duration(float(time['seconds']))}"
            + (f" for {counted} runs" if counted > 1 else "")
            + f", {int(time['steps']):,} steps in all, equilibration included" + unread)})

    # The trajectories: production frames of the atoms the run keeps.
    # Each run from its own settings: a sweep over the production length,
    # the rule it stops by, the interval or what it keeps writes each run's
    # trajectory as that run's settings say.
    def kept_atoms(run: dict[str, Any], built: Any) -> tuple[int, str]:
        everything = int(built.particles)
        waters = int(getattr(built, "waters", 0) or 0)
        sites = WATER_SITES.get(str(getattr(built, "water_model", "")).lower(), 3)
        kept = run.get("save_selection", "not water")
        if kept in (None, "", "all"):
            return everything, "every atom"
        if str(kept).strip() == "not water":
            return max(0, everything - waters * sites), "the atoms but water"
        return everything, f"at most every atom (`{kept}` is not worked out here)"

    planned = []
    for system, run in plan:
        built = estimate if estimates is None or not estimates else estimates.get(system, estimate)
        if built is None:
            continue
        frames, ceiling = _frames_planned(run)
        atoms, which = kept_atoms(run, built)
        planned.append((frames, atoms, which, ceiling))
    if not planned:
        lines.append({"kind": "disk", "level": "note",
                      "said": "not worked out: no system's structure is read here yet."})
        lines.append(_gpu_line(config, path_for))
        return lines
    many = len(planned)
    needed = sum(f * (DCD_BYTES_PER_ATOM * a + DCD_BYTES_PER_FRAME) for f, a, _w, _c in planned)
    ceiling = any(c for *_rest, c in planned)
    frames, atoms, which, _ = planned[0]
    if len({(f, a, w) for f, a, w, _c in planned}) == 1:
        said = (f"about {_size(needed)} of trajectory{' at most' if ceiling else ''}: "
                f"{frames:,} frames of {atoms:,} atoms ({which})"
                + (f" in each of {many} runs" if many > 1 else ""))
    else:
        counts = sorted(f for f, *_rest in planned)
        kinds = sorted({f"{a:,} atoms ({w})" for _f, a, w, _c in planned})
        frames_said = (f"{counts[0]:,} frames" if counts[0] == counts[-1]
                       else f"{counts[0]:,} to {counts[-1]:,} frames")
        said = (f"about {_size(needed)} of trajectory{' at most' if ceiling else ''} over "
                f"{many} runs: {frames_said} a run, of " + " or ".join(kinds))
    said += unread
    level = "note"
    where = _where_written(config, path_for)
    if where is not None:
        import shutil

        try:
            free = shutil.disk_usage(where).free
        except OSError:
            free = None
        if free is not None:
            said += f", with {_size(free)} free where it is written"
            if needed > free:
                level, said = "error", said + ": it does not fit"
            elif needed > DISK_WARNING_SHARE * free:
                level, said = "warning", said + ": it would leave the disk all but full"
    lines.append({"kind": "disk", "level": level, "said": said})

    lines.append(_gpu_line(config, path_for))
    return lines


def _not_worked_out(missing: list[str]) -> str:
    """The systems left out of a line, said after it."""
    if not missing:
        return ""
    named = ", ".join(missing)
    return (f"; the runs of {named} not worked out here: "
            + ("its structure is" if len(missing) == 1 else "their structures are")
            + " not read yet")


def _gpu_line(config: dict[str, Any],
              path_for: Callable[[Any], str | None] | None) -> dict[str, str]:
    """The GPU memory one run needs against what this computer's GPUs have
    free, as a Run here would choose its GPU (`gpu_here.choice_here`)."""
    from fastmdxplora.gpu_here import choice_here, held_from
    from fastmdxplora.remote.send import simulates_on_gpu

    if not simulates_on_gpu(config, os.cpu_count()):
        return {"kind": "gpu", "level": "note",
                "said": "runs on the CPU, so no GPU memory is needed"}
    room = _gpus_now()
    if room is None or not room.gpus:
        return {"kind": "gpu", "level": "note",
                "said": "not checked: no GPU here that nvidia-smi reads"}
    folder = _where_written(config, path_for) or Path.cwd()
    choice, notes = choice_here(config, folder, held_from(_going_near(folder)), room=room)
    if choice is None:
        return {"kind": "gpu", "level": "note",
                "said": " ".join(notes) or "not checked"}
    if choice.refused:
        return {"kind": "gpu", "level": "error", "said": choice.refused}
    gpu = choice.gpu
    on = (f"GPU {gpu.index} ({gpu.name}), {gpu.free_mb / 1024:.1f} GB free of "
          f"{gpu.total_mb / 1024:.1f} GB" if gpu is not None else "the GPUs the config names")
    if choice.need.mb is None:
        return {"kind": "gpu", "level": "note",
                "said": f"runs on {on}; the memory one run needs is {choice.need.how}"}
    return {"kind": "gpu", "level": "note",
            "said": f"about {choice.need.mb / 1024:.1f} GB of GPU memory a run "
                    f"({choice.need.how}), on {on}"}


def _going_near(folder: Path) -> list[dict[str, Any]]:
    """The runs started from the GUI going in the folder the study is
    written to or a folder above it (its workspace's record of them,
    `runs_here.going_in`): the GPUs they hold are not free to this study,
    as a Run here counts them."""
    from fastmdxplora.runs_here import going_in

    going: dict[str, dict[str, Any]] = {}
    for here in (folder, *list(folder.parents)[:3]):
        try:
            runs = going_in(here, walk=False)
        except OSError:
            continue
        for run in runs:
            going.setdefault(str(run.get("folder")), run)
    return list(going.values())


def _duration(seconds: float) -> str:
    """As the builder says a time: 40 seconds, 12 minutes, 3.4 hours, 2.1 days."""
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    if seconds < 129600:
        return f"{seconds / 3600:.1f} hours"
    return f"{seconds / 86400:.1f} days"
