"""One study description, run in several places, compared field by field.

The claim is that a configuration is the study: hand it to a container on a
cluster, a conda installation on a workstation and a wheel from PyPI, and
the same thing happens. That is checkable, and it is checkable more
precisely than by comparing results, because two of the three artefacts a
run leaves are supposed to be identical regardless of where it ran.

**The resolved configuration must match exactly.** It is every default
filled in, and a default that resolves differently on another machine is
the failure this file exists to catch: the study said one thing and two
machines heard two.

**The input digests must match exactly.** Setup records the SHA-256 of
the structure that entered the run, in ``setup/setup_parameters.json``
(``input.structure.sha256``). Two runs of one study that started from
different bytes are not two runs of one study, whatever their
configurations say. A run with no such record (a sequence input, or no
setup) is reported as not recorded, and its digest is not checked.

**The results must not be expected to match exactly, and saying so is part
of the comparison.** The preparation is seeded by ``setup.random_seed``,
written into ``resolved_config.yml``, so one seed, structure and settings
prepare the same atoms again where the packages that place them are the
same versions. The dynamics repeat their start only where
``simulation.random_seed`` is given, and step for step only as far as the
platform's arithmetic does; across environments the platform and its
builds differ, so the trajectories diverge. The observables are therefore
compared against the spread across environments rather than for equality,
and the reason is carried with the result.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

__all__ = [
    "ENVIRONMENTAL_KEYS",
    "compare_environments",
    "digest_of",
]

#: Keys whose values are about where a run happened rather than what it
#: was. A difference in these is not a difference in the study, and
#: reporting one as a discrepancy would bury the discrepancies that matter
#: under a list of paths.
ENVIRONMENTAL_KEYS = frozenset({
    "output_dir", "output", "run_id", "started_at", "finished_at",
    "hostname", "platform", "device_index", "precision", "threads",
    "version", "versions", "python", "duration_s", "seed_source",
})


def digest_of(path: str | Path) -> str:
    """SHA-256 of a file, for comparing inputs byte for byte."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """A nested configuration as one level of dotted keys.

    Compared flat because a nested diff reports the outermost block that
    changed, and the useful answer is which setting did.
    """
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(_flatten(item, f"{prefix}{key}."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}{index}."))
    else:
        out[prefix.rstrip(".")] = value
    return out


def _read_config(run_dir: Path) -> dict[str, Any]:
    for name in ("resolved_config.yml", "resolved_config.yaml"):
        path = run_dir / name
        if path.is_file():
            try:
                import yaml

                return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except ImportError:  # pragma: no cover - yaml is a dependency
                return {}
    return {}


def _input_digest(run_dir: Path) -> str | None:
    """The structure's SHA-256, as setup recorded it, or None.

    Setup writes it in ``setup_parameters.json`` under
    ``input.structure.sha256``; the run's ``manifest.json`` does not carry
    it. A run given `setup_from` is read from the setup it simulated.
    """
    try:
        from fastmdxplora.simulation.pipeline import setup_records_of

        setup = setup_records_of(run_dir) or run_dir / "setup"
    except Exception:  # noqa: BLE001 - a record to read, not to repair
        setup = run_dir / "setup"
    path = setup / "setup_parameters.json"
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    structure = (record.get("input") or {}).get("structure") \
        if isinstance(record, dict) else None
    if isinstance(structure, dict) and structure.get("sha256"):
        return str(structure["sha256"])
    return None


def compare_environments(
    runs: "dict[str, str | Path]",
) -> dict[str, Any]:
    """Compare one study as it ran in several environments.

    ``runs`` maps a name for each environment to that run's directory.
    """
    if len(runs) < 2:
        return {"agreed": None, "refused": (
            f"{len(runs)} environment(s) given, and a comparison across "
            "environments needs at least two."
        )}

    configs: dict[str, dict[str, Any]] = {}
    digests: dict[str, str | None] = {}
    missing: list[str] = []
    for name, directory in runs.items():
        path = Path(directory)
        config = _read_config(path)
        if not config:
            missing.append(name)
            continue
        configs[name] = _flatten(config)
        digests[name] = _input_digest(path)

    if len(configs) < 2:
        return {"agreed": None, "refused": (
            "No resolved configuration was found for "
            + ", ".join(missing)
            + ". A run writes one; a directory without it is not a run this "
            "can compare."
        )}

    keys = set().union(*(set(c) for c in configs.values()))
    study_differences: dict[str, dict[str, Any]] = {}
    environmental: dict[str, dict[str, Any]] = {}
    for key in sorted(keys):
        values = {name: config.get(key) for name, config in configs.items()}
        if len({json.dumps(v, sort_keys=True, default=str)
                for v in values.values()}) == 1:
            continue
        leaf = key.split(".")[-1]
        if leaf in ENVIRONMENTAL_KEYS:
            environmental[key] = values
        else:
            study_differences[key] = values

    unique_digests = {d for d in digests.values() if d is not None}
    digests_agree = len(unique_digests) <= 1
    unknown_digests = [n for n, d in digests.items() if d is None]

    return {
        "environments": sorted(configs),
        "agreed": not study_differences and digests_agree,
        "study_differences": study_differences,
        "environmental_differences": environmental,
        "input_digest": {
            "agree": digests_agree,
            "digests": digests,
            "not_recorded_for": unknown_digests,
        },
        "missing_configuration": missing,
        "what_is_not_compared": (
            "Results. The preparation is seeded by setup.random_seed, so "
            "one seed, structure and settings prepare the same atoms again "
            "where the packages that place them are the same versions. The "
            "dynamics repeat their start only where simulation.random_seed "
            "is given, and step for step only as far as the platform's "
            "arithmetic does; across environments the platform and its "
            "builds differ, so the trajectories diverge, and their "
            "observables should agree within the spread between replicas, "
            "not exactly."
        ),
        "refused": None,
    }
