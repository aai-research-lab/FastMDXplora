"""Your defaults: the values a workspace's studies start from.

FastMDXplora has its own defaults (300 K, 2 fs, ...), written in the
schema. A lab has its usual choices too: its assays run at 310 K, it
simulates with one force field, it runs three replicas. Written once in
``fastmdx-defaults.yml`` in the folder the studies are kept in, they fill
what a new study leaves unset, from every interface alike: the command line,
the GUI's Run and Config Builder, the Agent and an AI app.

The rules, each for a reason:

- **It fills only what a study leaves unset.** A value the config gives,
  even `null` (FastMDXplora's default, chosen), is the study's. A setting
  absent from the config is the only one filled.
- **It never decides what is simulated.** `systems`, `output`, `sweep` and
  the study-level keys are refused in it: the file holds how a lab
  simulates, not what.
- **A continuation is left alone.** A study that continues another
  (`simulation.resume_from`) keeps that study's settings, and one prepared
  from another (`simulation.setup_from`) keeps its setup.
- **Each value it fills is said.** It is recorded in the study's
  `decisions` with `source: fastmdx-defaults.yml` and the file's `why`, so
  the report's **Why these settings** says which values were the lab's,
  and the resolved config records the value itself, so the study runs the
  same again without the file.

The file is found as git finds its repository: the nearest
``fastmdx-defaults.yml`` in the study's folder or a folder above it,
stopping at the home folder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["DEFAULTS_FILE", "YourDefaults", "defaults_for", "find_defaults",
           "read_defaults", "refused_with", "with_defaults"]

DEFAULTS_FILE = "fastmdx-defaults.yml"

#: What the file may set: how a study is simulated, never what.
_BLOCKS = ("setup", "simulation", "analysis", "report", "execution")

#: Settings that answer one question between them: a config that gives one
#: is not given another from the file (`analysis.include` filled beside the
#: config's `analysis.exclude` was refused as setting both).
_ONE_OF = ({"analysis.include", "analysis.exclude"},)


@dataclass(frozen=True)
class YourDefaults:
    """A defaults file, read and checked."""

    path: Path
    #: The settings it gives, block by block, as a config holds them.
    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Why each is the usual value, by its dotted name, where the file says.
    whys: dict[str, str] = field(default_factory=dict)

    def settings(self) -> list[tuple[str, Any]]:
        """Every setting it gives, as (dotted name, value), in file order."""
        return [(f"{block}.{key}", value) for block, given in self.values.items()
                for key, value in given.items()]

    def said(self) -> str:
        """For the Agent and a person: each setting, its value and why."""
        import json

        lines = []
        for name, value in self.settings():
            why = self.whys.get(name)
            shown = value if isinstance(value, str) else json.dumps(value)
            lines.append(f"- `{name}`: {shown}" + (f" ({why})" if why else ""))
        return "\n".join(lines)


def find_defaults(start: str | Path, *, home: str | Path | None = None) -> Path | None:
    """The nearest defaults file in ``start`` or a folder above it, stopping
    at the home folder (or the top of the file system, for a folder outside
    it); None where there is none. ``start`` need not exist yet: a study's
    folder is named before it is made."""
    here = Path(start).expanduser()
    if not here.is_absolute():
        here = Path.cwd() / here
    stop = Path(home).expanduser() if home is not None else Path.home()
    try:
        stop = stop.resolve()
    except OSError:  # pragma: no cover - a home folder that cannot be resolved
        pass
    for folder in [here, *here.parents]:
        try:
            resolved = folder.resolve()
        except OSError:  # pragma: no cover - an unreadable parent
            continue
        candidate = resolved / DEFAULTS_FILE
        if candidate.is_file():
            return candidate
        if resolved == stop:
            return None
    return None


def read_defaults(path: str | Path) -> YourDefaults:
    """The file at ``path``, checked: a mapping of the blocks it may set,
    each setting one the schema knows with a value it accepts, and a `why`
    only for a setting it gives. Raises ConfigError saying what is wrong
    and in which file."""
    import yaml

    from fastmdxplora.config.loader import (
        ConfigError, canonical_phase_keys, validate_config)

    where = Path(path)
    try:
        data = yaml.safe_load(where.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"{where} could not be read: {exc}",
                          code="config.file.missing", path=str(where)) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{where} is not YAML that can be read: {exc}",
                          code="config.file.unparseable", path=str(where)) from exc
    if data is None:
        return YourDefaults(where)
    if not isinstance(data, dict):
        raise ConfigError(
            f"{where} holds your defaults as a mapping of blocks (`simulation:`, "
            f"`setup:`, ...); it holds a {type(data).__name__}.",
            code="config.file.not_a_mapping", path=str(where),
            found_type=type(data).__name__)
    canonical_phase_keys(data)
    decisions = data.pop("decisions", None) or {}
    for key in data:
        if key not in _BLOCKS:
            raise ConfigError(
                f"{where} sets `{key}`. Your defaults say how a study is simulated "
                f"({', '.join(_BLOCKS)}), never what is: `systems`, `output`, `sweep` "
                "and the study's own keys belong to each study's config.",
                code="config.option.not_permitted", option=str(key),
                context=DEFAULTS_FILE, permitted=list(_BLOCKS))
        if not isinstance(data[key], dict):
            raise ConfigError(
                f"{where}: `{key}` is a mapping of settings.",
                code="config.option.wrong_type", option=str(key), context=DEFAULTS_FILE,
                expected_type="mapping", found_type=type(data[key]).__name__)
    try:
        # Checked as part of a config: every setting known, every value one
        # the schema accepts. On a copy: the check settles some values in
        # place, and the file is filled in as written.
        import copy

        validate_config(copy.deepcopy(data))
    except ConfigError as exc:
        raise ConfigError(f"{where}: {exc}", code=exc.code,
                          **exc.refusal.details) from exc
    given = {f"{block}.{key}" for block, settings in data.items() for key in settings}
    if not isinstance(decisions, dict):
        raise ConfigError(
            f"{where}: `decisions` is a mapping from a setting's dotted name to "
            "`{why: ...}`.", code="config.option.wrong_type", option="decisions",
            context=DEFAULTS_FILE, expected_type="mapping",
            found_type=type(decisions).__name__)
    whys: dict[str, str] = {}
    for name, said in decisions.items():
        if str(name) not in given:
            raise ConfigError(
                f"{where} gives a reason for `{name}`, which it does not set. A "
                "reason here is for one of your defaults.",
                code="config.option.unknown", option=str(name), context=DEFAULTS_FILE,
                permitted=sorted(given))
        why = said.get("why") if isinstance(said, dict) else said
        if not isinstance(why, str) or not why.strip():
            raise ConfigError(
                f"{where}: the reason for `{name}` is `{{why: ...}}`, a sentence.",
                code="config.option.missing_companion", option=str(name),
                context=DEFAULTS_FILE, requires=["why"])
        whys[str(name)] = " ".join(why.split())
    return YourDefaults(where, {block: dict(settings) for block, settings in data.items()},
                        whys)


def defaults_for(start: str | Path, *, home: str | Path | None = None) -> YourDefaults | None:
    """The defaults that apply to a study in ``start``, or None."""
    found = find_defaults(start, home=home)
    return read_defaults(found) if found is not None else None


def _swept(config: dict[str, Any]) -> set[str]:
    sweep = config.get("sweep")
    return {str(name) for name in sweep} if isinstance(sweep, dict) else set()


def with_defaults(config: dict[str, Any],
                  defaults: YourDefaults | None) -> tuple[dict[str, Any], list[str]]:
    """The config with your defaults filling what it leaves unset, and the
    dotted names of the settings filled. The config given is not changed.

    A value the config gives, `null` included, is kept; a setting the study
    sweeps is left to its sweep; a continuation is left whole, and a study
    prepared from another keeps that one's setup. Each setting filled is
    recorded in `decisions` with the file as its source, unless the config
    already says why it has its value.
    """
    if defaults is None or not defaults.values:
        return config, []
    simulation = config.get("simulation") if isinstance(config.get("simulation"), dict) else {}
    if simulation.get("resume_from"):
        return config, []
    skipped_blocks = {"setup"} if simulation.get("setup_from") else set()
    swept = _swept(config)
    out = dict(config)
    decisions = dict(out.get("decisions") or {}) if isinstance(
        out.get("decisions"), dict) else {}
    filled: list[str] = []
    for block, settings in defaults.values.items():
        if block in skipped_blocks:
            continue
        held = out.get(block)
        if held is not None and not isinstance(held, dict):
            continue
        merged = dict(held or {})
        answered = {f"{block}.{key}" for key in merged}
        for key, value in settings.items():
            name = f"{block}.{key}"
            if key in merged or name in swept or any(
                    name in group and group & answered for group in _ONE_OF):
                continue
            merged[key] = value
            filled.append(name)
            if name not in decisions:
                decisions[name] = {
                    "why": defaults.whys.get(name)
                    or f"Your usual value, from {DEFAULTS_FILE}.",
                    "source": DEFAULTS_FILE}
        if merged:
            out[block] = merged
    if filled:
        out["decisions"] = decisions
    return out, filled


def refused_with(config: dict[str, Any], filled: dict[str, Any],
                 defaults: YourDefaults | None, *, require_systems: bool = False) -> Any:
    """The refusal your defaults cause, naming the file, where the config is
    accepted without them and refused with them; None otherwise. A refusal
    the config has of its own is left to be said as its own."""
    import copy

    from fastmdxplora.config.loader import ConfigError, validate_config

    if defaults is None or filled is config:
        return None
    try:
        validate_config(copy.deepcopy(config), require_systems=require_systems)
    except ConfigError:
        return None
    try:
        validate_config(copy.deepcopy(filled), require_systems=require_systems)
    except ConfigError as exc:
        return ConfigError(
            f"With your defaults ({defaults.path}) filled in, the study is refused: "
            f"{exc} Set that setting in the study's config, or change {DEFAULTS_FILE}.",
            code=exc.code, **exc.refusal.details)
    return None
