"""The settings a study recorded, read to run its phases again in its folder.

Every study writes the config it ran as ``resolved_config.yml``, each
setting filled in, a setting left unset recorded with its default then.
`fastmdx explore --output STUDY` with no system or config given, and so
each of `fastmdx setup`, `simulate`, `analyze` and `report` on a study,
starts from that record, so a phase run again runs as the study asked; the
settings given for this run are laid over it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

RECORDED = "resolved_config.yml"


def phase_settings(source: Path, phase: str, *, study: Path | None = None) -> dict[str, Any]:
    """The settings of ``phase`` in the config at ``source``, checked.

    ``study`` is the study the config is the record of, where it is one:
    the trajectory and topology it names inside itself are then left out
    (:func:`leave_the_study_s_own_files`). Raises the validator's
    :class:`~fastmdxplora.config.ConfigError` for a config it refuses.
    """
    from fastmdxplora.config.loader import load_config_file, phase_options, validate_config

    data = load_config_file(source)
    validate_config(data)
    settings = dict(phase_options(data).get(phase, {}))
    if study is not None:
        leave_the_study_s_own_files(settings, data.get("output"), study)
    return settings


def leave_the_study_s_own_files(settings: dict[str, Any], recorded_at: Any, study: Path) -> None:
    """Drop a recorded trajectory or topology that was the study's own.

    The record names the study's own files by the path the study had then,
    so a study moved or copied would analyse the frames at its old place,
    another study's if one is there now; and a study extended since would
    analyse its first piece, not its joined trajectory. Left out, the
    analysis finds the study's own frames where they are now. A trajectory
    from outside the study, which an analysis of an existing trajectory
    names, is kept.
    """
    study = Path(study)
    homes = [study.resolve()]
    if recorded_at:
        homes.append(Path(str(recorded_at)).expanduser().resolve())
    for key in ("trajectory", "topology"):
        named = settings.get(key)
        if not named:
            continue
        path = Path(str(named)).expanduser()
        if not path.is_absolute():
            path = study / path
        if any(path.resolve().is_relative_to(home) for home in homes):
            settings.pop(key)


def study_config(study: Path, phases: list[str] | None = None) -> dict[str, Any] | None:
    """The config a study recorded, to run ``phases`` of it again where it
    is; None where it recorded none.

    Its own trajectory and topology are found where the study is now
    (:func:`leave_the_study_s_own_files`), and its structure from where the
    study is. Left out: the budget, which priced the study as first run; and
    where the simulation is not run again, a continuation it was started
    as (`simulation.resume_from`, `extra_ns`), which would otherwise carry
    the study on rather than analyse it.
    """
    import yaml

    from fastmdxplora.simulation.resume import _found_from

    study = Path(study)
    try:
        config = yaml.safe_load((study / RECORDED).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(config, dict):
        return None
    recorded_at = config.pop("output", None)
    config.pop("budget_hours", None)
    config["systems"] = [
        {**entry, "system": _found_from(study, entry.get("system"))}
        if isinstance(entry, dict) else entry
        for entry in config.get("systems") or []]
    analysis = config.get("analysis")
    if isinstance(analysis, dict):
        analysis = dict(analysis)
        leave_the_study_s_own_files(analysis, recorded_at, study)
        config["analysis"] = analysis
    simulation = config.get("simulation")
    if phases is not None and "simulation" not in phases and isinstance(simulation, dict):
        config["simulation"] = {key: value for key, value in simulation.items()
                                if key not in ("resume_from", "extra_ns")}
    return config


def keep_the_study_s_phases(study: Path, include: Any, exclude: Any) -> None:
    """Put back the phases a study's record says it runs, after some of
    them were run again: the record then still runs the study whole.

    Each run of a study of several, and the study itself, were written by
    the run as the phases it was asked for.
    """
    import yaml

    study = Path(study)
    records = [study / RECORDED]
    runs = study / "runs"
    if (study / "batch_manifest.json").is_file() and runs.is_dir():
        records += [run / RECORDED for run in sorted(runs.iterdir())]
    for record in records:
        try:
            text = record.read_text(encoding="utf-8")
            config = yaml.safe_load(text)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(config, dict):
            continue
        header = "".join(line for line in text.splitlines(keepends=True)
                         if line.startswith("#"))
        config["include_phase"] = include
        config["exclude_phase"] = exclude
        record.write_text(header + yaml.safe_dump(config, sort_keys=False),
                          encoding="utf-8")


def laid_over(recorded: dict[str, Any], given: dict[str, Any]) -> dict[str, Any]:
    """The settings given over the recorded ones.

    An analysis's ``options`` are merged one analysis at a time rather than
    replaced whole. Analyses named to run replace a recorded list of those
    to leave out, and the other way round, since a phase takes one or the
    other: the two together were refused.
    """
    out = dict(recorded)
    for key, value in given.items():
        if key == "options" and isinstance(value, dict) and isinstance(out.get("options"), dict):
            merged = {name: dict(found) for name, found in out["options"].items()}
            for name, found in value.items():
                merged.setdefault(name, {}).update(found)
            out["options"] = merged
        else:
            out[key] = value
    if given.get("include") is not None:
        out.pop("exclude", None)
    elif given.get("exclude") is not None:
        out.pop("include", None)
    return out
