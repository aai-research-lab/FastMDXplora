"""Batch sweep expansion.

Turns a batch configuration (a list of ``systems`` and a ``sweep`` of
parameter axes) into a flat list of concrete single-run configurations —
the Cartesian product of systems × sweep points.

This module is deliberately pure: it does no I/O and constructs no
orchestrators. It just computes *what* runs should happen and *with what
options*, so the expansion logic is trivially testable in isolation.

Sweep axes use dotted keys naming a phase option, e.g.
``simulation.temperature_K``. Multiple axes form a full Cartesian
product. Each entry in ``systems`` may carry its own per-phase option
overrides, which are applied beneath the sweep values (sweep wins, since
the sweep is the thing being varied).

Example
-------
Input::

    systems:
      - {id: a, system: a.pdb}
      - {id: b, system: b.pdb}
    sweep:
      simulation.temperature_K: [300, 310]

produces four runs: a@300, a@310, b@300, b@310.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

from fastmdxplora.config.schema import PHASE_KEYS
from fastmdxplora.refusals import CodedError


class SweepError(CodedError, ValueError):
    """Raised for malformed systems/sweep specifications."""

    default_code = "batch.sweep.invalid"


# A safe slug for run-directory names: keep alnum, dot, plus, minus.
_SLUG_CLEAN = re.compile(r"[^A-Za-z0-9.+-]+")


@dataclass
class RunSpec:
    """One concrete run in a batch.

    Attributes
    ----------
    run_id : str
        Unique, human-readable identifier (also the run subdirectory name).
    system : str
        The system input for this run.
    options : dict[str, dict[str, Any]]
        Per-phase options for this run (merged: base < system < sweep).
    sweep_values : dict[str, Any]
        The swept axis values for this run (dotted key -> value), recorded
        for the batch manifest.
    system_id : str
        The originating system's id.
    """

    run_id: str
    system: str
    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    sweep_values: dict[str, Any] = field(default_factory=dict)
    system_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "system_id": self.system_id,
            "system": self.system,
            "sweep_values": self.sweep_values,
            "options": self.options,
        }


def _slug(value: Any) -> str:
    """Make a value safe for a directory-name fragment."""
    s = str(value)
    s = _SLUG_CLEAN.sub("-", s).strip("-")
    return s or "x"


def _split_dotted(key: str) -> tuple[str, str]:
    """Split ``simulation.temperature_K`` -> ("simulation", "temperature_K").

    Raises SweepError if the key isn't a ``phase.option`` form naming a
    known phase.
    """
    if "." not in key:
        raise SweepError(
            f"Sweep axis '{key}' must be a dotted phase.option key, e.g. "
            f"'simulation.temperature_K'."
        )
    phase, _, option = key.partition(".")
    if phase not in PHASE_KEYS:
        raise SweepError(
            f"Sweep axis '{key}' names phase '{phase}', which is not a valid "
            f"phase ({', '.join(PHASE_KEYS)})."
        )
    if not option:
        raise SweepError(f"Sweep axis '{key}' is missing an option name.")
    return phase, option


def _deep_merge_options(
    base: dict[str, dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge per-phase option dicts; overrides win per option key."""
    out: dict[str, dict[str, Any]] = {p: dict(v) for p, v in base.items()}
    for phase, opts in overrides.items():
        if not isinstance(opts, dict):
            continue
        merged = dict(out.get(phase, {}))
        merged.update(opts)
        out[phase] = merged
    return out


def normalize_systems(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalize the ``systems`` list.

    Each entry must be a mapping with at least ``system`` (the input).
    ``id`` is optional (defaults to a positional ``sN`` / derived name).
    Any phase-named keys (``setup``, ``simulation``, ...) are treated as
    per-system option overrides.
    """
    if not isinstance(raw, list) or not raw:
        raise SweepError("`systems` must be a non-empty list of mappings.")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise SweepError(f"`systems[{i}]` must be a mapping, got {type(entry).__name__}.", code="batch.sweep.invalid")
        if "system" not in entry or not entry["system"]:
            raise SweepError(f"`systems[{i}]` is missing a `system` input.")

        sid = str(entry.get("id") or f"s{i + 1}")
        if sid in seen_ids:
            raise SweepError(f"Duplicate system id '{sid}' in `systems`.")
        seen_ids.add(sid)

        # Per-system per-phase option overrides
        options: dict[str, dict[str, Any]] = {}
        for phase in PHASE_KEYS:
            if phase in entry and isinstance(entry[phase], dict):
                options[phase] = dict(entry[phase])

        normalized.append({
            "id": sid,
            "system": str(entry["system"]),
            "options": options,
        })
    return normalized


def normalize_sweep(raw: Any) -> dict[str, list[Any]]:
    """Validate and normalize the ``sweep`` mapping.

    Each key is a dotted ``phase.option`` and each value is a non-empty
    list of values to try. Returns the same shape with values coerced to
    lists (a scalar is treated as a one-element axis).
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not raw:
        raise SweepError("`sweep` must be a non-empty mapping of axis -> values.")

    out: dict[str, list[Any]] = {}
    for key, values in raw.items():
        _split_dotted(key)  # validates the key
        if isinstance(values, (list, tuple)):
            vals = list(values)
        else:
            vals = [values]
        if not vals:
            raise SweepError(f"Sweep axis '{key}' has an empty value list.")
        out[key] = vals
    return out


def expand_runs(
    *,
    systems: list[dict[str, Any]] | None,
    sweep: dict[str, list[Any]] | None,
    base_options: dict[str, dict[str, Any]] | None = None,
    base_system: str | None = None,
) -> list[RunSpec]:
    """Expand systems × sweep into a flat list of :class:`RunSpec`.

    Parameters
    ----------
    systems : list of normalized system dicts, or None
        From :func:`normalize_systems`. If None, a single implicit system
        is used from ``base_system``.
    sweep : dict of axis -> values, or None
        From :func:`normalize_sweep`. If None/empty, no sweep is applied
        (one run per system).
    base_options : dict, optional
        Project-level per-phase options that every run inherits (the
        top-level phase blocks of the config). Lowest priority.
    base_system : str, optional
        Used when ``systems`` is None (single implicit system).

    Returns
    -------
    list[RunSpec]
        One entry per (system × sweep-point), in deterministic order:
        systems outer, sweep axes inner (in declared order).
    """
    base_options = base_options or {}

    # Resolve the system list
    if systems:
        sys_entries = systems
    elif base_system is not None:
        sys_entries = [{"id": "s1", "system": base_system, "options": {}}]
    else:
        raise SweepError("expand_runs requires either `systems` or `base_system`.")

    # Build the sweep grid: list of (axis_key, value) tuples per point
    if sweep:
        axis_keys = list(sweep.keys())
        value_lists = [sweep[k] for k in axis_keys]
        sweep_points = [
            dict(zip(axis_keys, combo))
            for combo in itertools.product(*value_lists)
        ]
    else:
        sweep_points = [{}]  # a single empty point => one run per system

    runs: list[RunSpec] = []
    multi_system = len(sys_entries) > 1
    multi_point = len(sweep_points) > 1

    for sys_entry in sys_entries:
        sid = sys_entry["id"]
        sys_options = sys_entry.get("options", {})
        for point in sweep_points:
            # Merge order (lowest -> highest): base < system < sweep
            opts = _deep_merge_options(base_options, sys_options)
            sweep_opts: dict[str, dict[str, Any]] = {}
            for dotted, value in point.items():
                phase, option = _split_dotted(dotted)
                sweep_opts.setdefault(phase, {})[option] = value
            opts = _deep_merge_options(opts, sweep_opts)

            run_id = _make_run_id(sid, point, multi_system, multi_point)
            runs.append(RunSpec(
                run_id=run_id,
                system=sys_entry["system"],
                options=opts,
                sweep_values=dict(point),
                system_id=sid,
            ))

    # Guard against accidental id collisions (shouldn't happen, but be safe)
    _ensure_unique_run_ids(runs)
    return runs


def _make_run_id(
    system_id: str,
    point: dict[str, Any],
    multi_system: bool,
    multi_point: bool,
) -> str:
    """Build a human-readable, filesystem-safe run id.

    Encodes the system id and each swept axis as ``option-value`` so the
    directory name tells you exactly what varied.
    """
    parts: list[str] = [_slug(system_id)]
    for dotted, value in point.items():
        # Use just the option name (not the phase) to keep names short;
        # collisions across phases are vanishingly unlikely and the full
        # mapping is in the manifest anyway.
        _phase, option = _split_dotted(dotted)
        parts.append(f"{_slug(option)}-{_slug(value)}")
    return "__".join(parts)


def _ensure_unique_run_ids(runs: list[RunSpec]) -> None:
    seen: dict[str, int] = {}
    for r in runs:
        if r.run_id in seen:
            seen[r.run_id] += 1
            r.run_id = f"{r.run_id}__dup{seen[r.run_id]}"
        else:
            seen[r.run_id] = 0


def is_batch_config(data: dict[str, Any]) -> bool:
    """Return True if a parsed config requests batch mode.

    Batch mode is active when the config contains a non-empty ``systems``
    list or a non-empty ``sweep`` mapping.
    """
    if not isinstance(data, dict):
        return False
    return bool(data.get("systems")) or bool(data.get("sweep"))


def normalize_summary_for_validation(data: dict[str, Any]) -> None:
    """Validate the batch keys of a parsed config (raises SweepError).

    Checks that ``systems`` and ``sweep`` are well-formed, and that every
    sweep axis names a real option in its phase's schema (so a typo like
    ``simulation.temperature_K`` is caught with a did-you-mean style
    message rather than silently producing a junk run).
    """
    from fastmdxplora.config.schema import PHASE_SCHEMAS

    if data.get("systems") is not None:
        normalize_systems(data["systems"])

    sweep = data.get("sweep")
    if sweep is not None:
        norm = normalize_sweep(sweep)
        for axis in norm:
            phase, option = _split_dotted(axis)
            schema = PHASE_SCHEMAS[phase]
            if schema.get(option) is None:
                valid = sorted(schema.field_names())
                raise SweepError(
                    f"Sweep axis '{axis}' names option '{option}', which is "
                    f"not a valid {phase} option. Valid: {', '.join(valid)}."
                )
