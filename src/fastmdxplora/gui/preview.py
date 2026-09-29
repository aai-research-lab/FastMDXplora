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
import re
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
    from fastmdxplora.advisories import advise
    from fastmdxplora.gui.config_builder import build_config
    from fastmdxplora.setup.estimate import estimate_system
    from fastmdxplora.structure_info import count_structure

    try:
        config = build_config(state)
    except Exception as exc:  # noqa: BLE001 - said under the form, as the config route says it
        return {"ok": False, "reason": str(exc)}
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
        for a in advise(count_structure(structure), settings)
        # The box is said by the estimate, from the solute's bounding sphere
        # as OpenMM sizes it; the advisory's reckoning from the longest
        # extent could say otherwise beside it.
        if a.setting != "solvent_padding_nm"
    ]
    return {
        "ok": True,
        "system": given,
        "estimate": estimate.as_record(),
        "time": _time(config, estimate.particles),
        "advisories": advisories,
    }


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


def _cache() -> Path:
    from fastmdxplora.setup.ccd import cache_dir

    target = cache_dir().parent / "structures"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _fetched(pdb_id: str) -> Path:
    from fastmdxplora.setup.pipeline import _fetch_pdb_from_rcsb

    target = _cache() / f"{pdb_id}.pdb"
    if not target.is_file():
        _fetch_pdb_from_rcsb(pdb_id, target)
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
