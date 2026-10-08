"""What differs between two studies' settings.

"Why did these two runs come out differently?" starts with what they were
asked to do, and two resolved configs are a hundred lines each. This reads
two Configs, or the folders two studies were written to, and lists the
settings in which they differ, with every phase setting either leaves out
taken at its default, so a short Config and the full one it resolves to
compare as the same study.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["Difference", "config_of", "differences", "settings_of"]

#: A setting one side does not have at all.
ABSENT = object()

#: Settings that say where and by what a study was written, not what it
#: asked for: two copies of one study differ in these and nothing else.
WHERE_IT_WAS_WRITTEN = frozenset({"output", "agent_model"})

#: Settings the run works out from the others (the force field's files,
#: the water model, the cutoff and the seed setup chose; step counts from
#: the lengths and the timestep, the ensemble, the files analysed) and
#: writes into the study's record as it reaches them. A record that never
#: reached them (a run stopped early, the first piece of a resumed study, a
#: trajectory analysed) holds null there: it did not ask for something
#: else, it never recorded it.
WORKED_OUT_BY_THE_RUN = frozenset({
    "setup.force_field", "setup.water_model", "setup.ligand_forcefield",
    "setup.nonbonded_cutoff_nm", "setup.use_switching_function", "setup.random_seed",
    "simulation.nvt_steps", "simulation.npt_steps", "simulation.production_steps",
    "simulation.ensemble", "simulation.pressure_bar",
    "simulation.trajectory_interval_steps",
    "analysis.trajectory", "analysis.topology",
})

#: Settings that follow from others, said only where those agree: two runs
#: of different lengths differ in their step counts because they differ in
#: their lengths, which are listed (as are their timesteps).
FOLLOWS_FROM = {
    "simulation.nvt_steps": ("simulation.nvt_duration_ns", "simulation.timestep_fs"),
    "simulation.npt_steps": ("simulation.npt_duration_ns", "simulation.timestep_fs"),
    "simulation.production_steps": ("simulation.duration_ns", "simulation.timestep_fs"),
    "simulation.trajectory_interval_steps": ("simulation.duration_ns",
                                             "simulation.timestep_fs"),
}


@dataclass(frozen=True)
class Difference:
    setting: str
    first: Any
    second: Any

    @property
    def where_only(self) -> bool:
        return self.setting in WHERE_IT_WAS_WRITTEN

    @property
    def unrecorded(self) -> bool:
        """A setting the run works out, which one side never recorded."""
        return self.setting in WORKED_OUT_BY_THE_RUN and (
            _unset(self.first) != _unset(self.second))

    def as_record(self) -> dict[str, Any]:
        return {"setting": self.setting,
                "first": None if self.first is ABSENT else self.first,
                "second": None if self.second is ABSENT else self.second,
                "in_first": self.first is not ABSENT,
                "in_second": self.second is not ABSENT}


def config_of(path: str | Path) -> tuple[dict[str, Any], Path]:
    """The Config a path names: a file, or the `resolved_config.yml` a study
    wrote in its folder."""
    from fastmdxplora.config import ConfigError
    from fastmdxplora.config.loader import load_config_file

    where = Path(path)
    if where.is_dir():
        where = where / "resolved_config.yml"
        if not where.is_file():
            raise ConfigError(f"{path} holds no resolved_config.yml: it is not a "
                              "study this software wrote, or the study never started",
                              code="environment.path.not_found")
    elif not where.is_file():
        raise ConfigError(f"no such Config or study: {path}", code="environment.path.not_found")
    return load_config_file(where), where


def settings_of(config: dict[str, Any]) -> dict[str, Any]:
    """Every setting, by its dotted name; each phase's defaults filled in."""
    from fastmdxplora.config.schema import PHASE_SCHEMAS

    flat: dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict) and value:
            for key, inner in value.items():
                walk(f"{prefix}.{key}" if prefix else str(key), inner)
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            for index, inner in enumerate(value):
                walk(f"{prefix}[{index}]", inner)
        else:
            flat[prefix] = value

    walk("", config)
    # A path inside the study's own folder is said relative to it: two
    # studies' trajectories are each in their own folder, and that is where
    # they were written, not something they were asked to differ in.
    output = config.get("output")
    if isinstance(output, str) and output:
        root = output.rstrip("/") + "/"
        for key, value in flat.items():
            if key == "output" or not isinstance(value, str):
                continue
            if value.rstrip("/") == output.rstrip("/"):
                flat[key] = "<output>"
            elif value.startswith(root):
                flat[key] = "<output>/" + value[len(root):]
            elif value.startswith(Path(output).name + "/simulation/"):
                # Recorded under the folder's name, relative to where it ran:
                # "trpcage/simulation/production.dcd" is its own trajectory.
                flat[key] = "<output>/" + value[len(Path(output).name) + 1:]
    for phase, schema in PHASE_SCHEMAS.items():
        for field in schema.fields:
            flat.setdefault(f"{phase}.{field.name}", field.default)
    # No phases named is every phase, none excluded is none: a study that
    # wrote the list out asked for the same as one that left it out.
    from fastmdxplora.orchestrator import PHASES

    if flat.get("include_phase") is None:
        flat["include_phase"] = list(PHASES)
    if not flat.get("exclude_phase"):
        flat["exclude_phase"] = []
    return flat


def differences(first: dict[str, Any], second: dict[str, Any]) -> list[Difference]:
    """The settings two Configs disagree on, in the order they appear."""
    a, b = settings_of(first), settings_of(second)
    found = []
    for setting in list(a) + [key for key in b if key not in a]:
        left, right = a.get(setting, ABSENT), b.get(setting, ABSENT)
        if not _same(left, right):
            found.append(Difference(setting, left, right))
    differing = {d.setting for d in found}
    return [d for d in found
            if not any(source in differing for source in FOLLOWS_FROM.get(d.setting, ()))]


def _unset(value: Any) -> bool:
    return value is ABSENT or value is None


def _same(left: Any, right: Any) -> bool:
    """Equal as settings: 2 and 2.0 are one duration; a setting left out
    and one written as null are one setting not set."""
    if _unset(left) and _unset(right):
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        # true and 1 are not one setting written two ways.
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=0.0)
    return left == right
