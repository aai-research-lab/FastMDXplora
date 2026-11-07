"""What can be refused about a study before anything is built.

The validator judges each setting against the schema; it does not open a
file or parse a selection. Two things a person writes can be found wrong
without building anything, and were found only when setup or an analysis
reached them, after the time it took to get there: a system setup cannot
read (a structure file that is not there, a name that is neither a file nor
a PDB ID, a sequence this software cannot build), and a selection MDTraj
cannot parse.

One check, said the same way by every door a study comes through:
`fastmdx explore` (a dry run and a run alike, before anything is written),
the GUI's Check it and Run as it stands, and the check an AI app or the
Agent makes (`check_study`).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from fastmdxplora.config.loader import ConfigError

PHASES = ("setup", "simulation", "analysis", "report")
_STRUCTURE_SUFFIXES = {".pdb", ".cif", ".pdbx"}


def phases_planned(config: dict[str, Any]) -> list[str]:
    """The phases a config runs, as the orchestrator reads its lists: its
    `include_phase` (or `include`), else all but its `exclude_phase` (or
    `exclude`), else all four."""
    from fastmdxplora.config.loader import _split_on_commas

    # A list written as one string, `setup,report`, as the validator takes
    # it: read letter by letter, it named no phase.
    include = _split_on_commas(config.get("include_phase") or config.get("include"))
    if include:
        named = {str(p) for p in include}
        return [p for p in PHASES if p in named]
    exclude = {str(p) for p in (_split_on_commas(
        config.get("exclude_phase") or config.get("exclude")) or [])}
    return [p for p in PHASES if p not in exclude]


def _simulation(*holders: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for holder in holders:
        block = holder.get("simulation") if isinstance(holder, dict) else None
        if isinstance(block, dict):
            merged.update(block)
    return merged


def setup_reads_the_system(config: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Whether setup will read this system entry's structure.

    Not where setup is out of the plan, nor where the run simulates a system
    prepared before (`setup_from`, `prepared_from`; the orchestrator then
    prepares nothing) or carries on from one (`resume_from`).
    """
    if "setup" not in phases_planned(config):
        return False
    simulation = _simulation(config, entry)
    return not any(simulation.get(key) for key in
                   ("setup_from", "prepared_from", "resume_from"))


def _system_refused(system: str, base: Path | None) -> ConfigError | None:
    """Why setup could not read ``system``, or None. A relative structure
    file is read from ``base``, the folder the run starts in; with no base,
    one that is not there is left for setup to say."""
    from fastmdxplora.refusals import CodedError
    from fastmdxplora.setup.pipeline import _classify_input

    path = Path(system).expanduser()
    if path.suffix.lower() in _STRUCTURE_SUFFIXES:
        if path.is_absolute():
            there = path
        elif base is not None:
            there = Path(base) / path
        else:
            return None
        if there.exists():
            return None
        read_from = (f"read from {base}, the folder the run starts in"
                     if not path.is_absolute() else "an absolute path")
        return ConfigError(
            f"No structure at {system!r} ({read_from}). Give the path from that "
            "folder, or an absolute one, or a 4-character PDB ID.",
            code="environment.path.not_found", given=system)
    try:
        form = _classify_input(system)
    except CodedError as exc:
        return ConfigError(str(exc), code=exc.code or "setup.input.unrecognised",
                           given=system)
    if form == "sequence":
        return ConfigError(
            f"System {system!r} reads as an amino-acid sequence, and a structure "
            "cannot be built from a sequence here: that needs a structure "
            "predictor, which this software does not carry. Predict the "
            "structure first and give its file, or give a 4-character PDB ID "
            "for a deposited structure.",
            code="environment.backend.missing", given=system)
    return None


def _option_default(name: str, key: str) -> Any:
    """The default an analysis gives one of its options, or a marker for
    none (an analysis or an option this version does not know)."""
    try:
        from fastmdxplora.analysis.orchestrator import get_analysis_class

        cls = get_analysis_class(name)
    except Exception:  # noqa: BLE001 - the validator says an unknown name
        return inspect.Parameter.empty
    for klass in cls.__mro__:
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        param = inspect.signature(init).parameters.get(key)
        if param is not None:
            return param.default
    return inspect.Parameter.empty


def _is_selection_key(key: str) -> bool:
    return key in ("select_atoms", "selection") or key.endswith("_selection") \
        or key.startswith("selection_")


def selections_in(config: dict[str, Any]) -> list[tuple[str, str]]:
    """Every MDTraj selection a config writes, with the setting it is in:
    the analyses' own, each analysis's selection options in
    `analysis.options`, the atoms the trajectory saves, the atoms each
    restraint holds (`simulation.restrain`, short form or blocks), the same
    in each system entry's blocks, and each value a `sweep` axis gives one
    of these.

    An analysis option is read as a selection by its name (`selection`,
    `..._selection`, `selection_...`), and not where its value is that
    analysis's own default: `water_sites`'s `site_selection: auto` asks the
    analysis to find the sites, and is not a selection.
    """
    found: list[tuple[str, str]] = []

    def blocks(where: str, holder: dict[str, Any]) -> None:
        analysis = holder.get("analysis")
        if isinstance(analysis, dict):
            for key in ("select_atoms", "selection"):
                if isinstance(analysis.get(key), str):
                    found.append((f"{where}analysis.{key}", analysis[key]))
            options = analysis.get("options")
            if isinstance(options, dict):
                for name, given in options.items():
                    if not isinstance(given, dict):
                        continue
                    for key, value in given.items():
                        if not (isinstance(value, str) and _is_selection_key(str(key))):
                            continue
                        if value == _option_default(str(name), str(key)):
                            continue
                        found.append((f"{where}analysis.options.{name}.{key}", value))
        simulation = holder.get("simulation")
        if isinstance(simulation, dict) and isinstance(simulation.get("save_selection"), str):
            found.append((f"{where}simulation.save_selection", simulation["save_selection"]))
        if isinstance(simulation, dict) and simulation.get("restrain") not in (None, ""):
            from fastmdxplora.simulation.restraints import restraint_selections

            for within, selection in restraint_selections(simulation["restrain"]):
                found.append((f"{where}simulation.restrain{within}", selection))

    blocks("", config)
    for entry in config.get("systems") or []:
        if isinstance(entry, dict):
            blocks(f"systems[{entry.get('id') or entry.get('system')}].", entry)
    sweep = config.get("sweep")
    if isinstance(sweep, dict):
        # Each value an axis takes, read as the block it sets in a run, and
        # named by the axis and its place there: `sweep.simulation.restrain[2]`.
        for axis, values in sweep.items():
            phase, _, option = str(axis).partition(".")
            if not option:
                continue
            for number, value in enumerate(
                    values if isinstance(values, (list, tuple)) else [values], start=1):
                holder: dict[str, Any] = {}
                inner = holder.setdefault(phase, {})
                *outer, last = option.split(".")
                for key in outer:
                    inner = inner.setdefault(key, {})
                inner[last] = value
                start = len(found)
                blocks("", holder)
                found[start:] = [(f"sweep.{axis}[{number}]{setting[len(str(axis)):]}", given)
                                 for setting, given in found[start:]]
    return found


def refuse_what_can_be_seen(config: dict[str, Any], *, base: Path | None = None) -> None:
    """Raise, as a Config error, what can be found wrong before anything is
    built: a system setup will read and cannot, and a selection MDTraj
    cannot parse.

    ``base`` is the folder a run starts in, which a relative structure path
    is read from; with none, only what needs no folder is checked.
    """
    for entry in config.get("systems") or []:
        holder = entry if isinstance(entry, dict) else {"system": entry}
        system = holder.get("system")
        if system is None or not setup_reads_the_system(config, holder):
            continue
        refusal = _system_refused(str(system), base)
        if refusal is not None:
            raise refusal

    found = selections_in(config)
    if not found:
        return
    import mdtraj as md

    empty = md.Topology()
    for setting, selection in found:
        try:
            empty.select(selection)
        except Exception as exc:  # noqa: BLE001 - MDTraj's parser says why
            raise ConfigError(
                f"{setting}: {selection!r} is not an MDTraj selection ({exc}). "
                "Write it in MDTraj's selection language, for example "
                "'protein and name CA'; `fastmdx select` shows what one matches.",
                code="config.option.not_permitted", option=setting,
                given=selection) from exc


def refused_before_a_run(config: dict[str, Any], filled: dict[str, Any], defaults: Any,
                         *, base: Path | None = None) -> ConfigError | None:
    """What :func:`refuse_what_can_be_seen` refuses in ``filled``, the
    study with your defaults (``defaults``) filled in, naming the file where
    the study as written (``config``) is not refused: a selection your file
    gives was said as the Config's own, which the person could not find."""
    try:
        refuse_what_can_be_seen(filled, base=base)
    except ConfigError as exc:
        if defaults is None or getattr(defaults, "path", None) is None:
            return exc
        try:
            refuse_what_can_be_seen(config, base=base)
        except ConfigError as own:
            return own
        from fastmdxplora.config.defaults_file import DEFAULTS_FILE

        return ConfigError(
            f"With your defaults ({defaults.path}) filled in, the study is refused: "
            f"{exc} Set that setting in the study's config, or change {DEFAULTS_FILE}.",
            code=exc.code, **exc.refusal.details)
    return None
