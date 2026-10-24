"""One table from a campaign's members, and what their spread means.

A campaign leaves each member in its own directory with its own analyses.
Reading them is arithmetic; knowing what the spread across them measures is
not, and it is the whole of why this exists.

**Replicas and variants are opposites, and they look identical on disk.**
Members that differ only by random seed, or that start from different
models of one ensemble, are repeats of one measurement, so
the spread of their means is the error on it. Members that differ by
system, mutation or parameter are different measurements, so the spread
between them is the result. Averaging the second kind reports a mutant
series' biology as noise; quoting the first kind as a finding reports noise
as biology. This tells them apart from the campaign's own sweep record
rather than from the shape of the numbers, and says which it decided.

**Replicas are what makes a reported uncertainty checkable.** Every recorded
mean carries a standard error estimated from one trajectory's
autocorrelation. Ten trajectories give the same quantity a second way, as
the spread of ten means, and the two should agree. Where the single-run
estimate is much the smaller, the run was too short to see its own
correlation time and the error bars it printed were too tight. That
comparison is only available to a seed sweep, which is why it is reported
only there.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "SEED_AXES",
    "aggregate_members",
    "batch_manifest",
    "finished_members",
    "how_replicas_differ",
    "member_directory",
    "read_member_findings",
    "replica_error",
    "spread_against_own",
]

#: Sweep axes that make members repeats rather than different studies.
#: Written out rather than matched on the word "seed", because a study
#: sweeping something merely named seed-like is not replicating anything.
SEED_AXES = frozenset({
    "simulation.random_seed",
    "random_seed",
    # Prepared from different random placements of hydrogens and ions,
    # and otherwise the same study: repeats of one measurement.
    "setup.random_seed",
    # Prepared from different models of one ensemble: the same molecule
    # started from different structures, which is what independent starts
    # are.
    "setup.model",
})

#: How far the single-run error estimate may sit from the spread of the
#: replicas' means before the estimate is called optimistic. A factor of
#: two: the estimate is a variance, so agreeing to better than a factor of
#: two on ten replicas is already at the limit of what ten can resolve.
CALIBRATION_FACTOR = 2.0


def member_directory(batch_dir: str | Path, run: dict[str, Any]) -> Path:
    """Where a member's results are: relative to the batch where the
    manifest says so and the folder is there, so a campaign that was moved
    or copied reads its own members; otherwise the path recorded. A
    campaign moved without that record found every member missing, or, with
    another campaign at the old path, read that one's."""
    from fastmdxplora import record_access

    # Through `record_access`: held to the study where the read is (the
    # Compare page), a folder linked out of it is not looked into.
    root = Path(batch_dir)
    relative = run.get("output_dir_relative")
    run_id = run.get("run_id")
    for inside in ((root / str(relative)) if relative else None,
                   (root / "runs" / str(run_id)) if run_id else None):
        if inside is None:
            continue
        if record_access.is_dir(inside):
            return inside
        if os.path.lexists(inside):
            # There, and its read refused (a link out of the study): the
            # member is this one, not read and named so, rather than the
            # path recorded when it ran, which is outside the study too
            # and could only be said "an unavailable record".
            return inside
    return Path(str(run.get("output_dir", "")))


def read_member_findings(run_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Every analysis's findings in one member, keyed by analysis name."""
    from fastmdxplora import record_access

    root = Path(run_dir)
    out: dict[str, dict[str, Any]] = {}
    # Through `record_access`: held to the study where the read is (the
    # Compare page), as pathlib elsewhere.
    for options in sorted(record_access.glob(root, "analysis/*/options.json")):
        try:
            record = json.loads(record_access.read_text(options, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = record.get("analysis") or options.parent.name
        findings = record.get("findings")
        if isinstance(findings, dict):
            out[str(name)] = findings
    return out


def batch_manifest(batch_dir: str | Path) -> dict[str, Any]:
    """A campaign's manifest, read through `record_access`: held to the
    study where the read is (the Compare page), as pathlib elsewhere.
    Raises OSError or ValueError where it cannot be read."""
    from fastmdxplora import record_access

    manifest = json.loads(record_access.read_text(Path(batch_dir) / "batch_manifest.json",
                                                  encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("a batch manifest is an object")
    return manifest


def _finished(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The campaign's manifest and its members that finished."""
    manifest = batch_manifest(root)
    return manifest, [r for r in manifest.get("runs", []) if r.get("status") == "ok"]


def finished_members(batch_dir: str | Path) -> tuple[list[tuple[str, Path]], bool, str]:
    """The members of a campaign that finished, each its run's id and its
    folder; whether they are replicas, and why that was decided, from its
    manifest as :func:`aggregate_members` reads it. Raises OSError or
    ValueError where the manifest cannot be read."""
    root = Path(batch_dir)
    manifest, runs = _finished(root)
    replicas, why = _members_are_replicas(manifest)
    return ([(str(run.get("run_id") or ""), member_directory(root, run)) for run in runs],
            replicas, why)


def replica_error(means: Any, own: float | None) -> float | None:
    """The standard error of one replica's mean: the spread of the
    replicas' means (sample standard deviation, ``spread_of_means``), since
    replicas are repeats of one measurement and
    their spread is its error; or the run's own error where that is the
    larger, since a spread of few replicas can fall short of it by chance
    and a run's error is never narrowed by its repeats. None where fewer
    than two means give a spread. The one error a difference between two
    replicas is set against (the Compare page) as here."""
    values = np.asarray([m for m in means], dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return None
    spread = float(np.std(values, ddof=1))
    mine = _finite_or_none(own)
    return max(spread, mine) if mine is not None and mine > 0 else spread


def spread_against_own(means: Any, errors: Any) -> dict[str, Any] | None:
    """Replicas' spread set against the errors the runs estimated for
    themselves, the one check a single run's error gets: ``observed``, the
    spread of their means (sample standard deviation); ``predicted``, the
    mean of the errors given (each above zero); their ``ratio``; and
    ``too_tight``, the spread past ``CALIBRATION_FACTOR`` times the
    prediction, so the errors the runs printed are too tight. Of two
    replicas whose own errors are all there is to set them against (the
    Compare page) as of all of a study's (`aggregate_members`). None where
    there are fewer than two means or no error of their own."""
    values = np.asarray([m for m in means], dtype=float)
    given = [float(e) for e in errors if e is not None and np.isfinite(e) and e > 0]
    if values.size < 2 or not given:
        return None
    observed = float(np.std(values, ddof=1))
    predicted = float(np.mean(given))
    return {"observed": observed, "predicted": predicted, "ratio": float(observed / predicted),
            "too_tight": observed > CALIBRATION_FACTOR * predicted,
            "factor": CALIBRATION_FACTOR}


def how_replicas_differ(axes: Any, *, models: Any = None,
                        as_participle: bool = False) -> str:
    """How a study's replicas differ, as every page and record says it,
    from the axes they were swept over: "that differ only by random seed"
    ("differing only by random seed" as a participle), or, where they were
    prepared from different models of an ensemble (`setup.model`),
    "started from different models of the ensemble" (the models named where
    ``models`` gives them), with a seed of its own each where a seed was
    swept too. Read after "runs" or "replicas"."""
    axes = {str(axis) for axis in axes or ()}
    if "setup.model" in axes:
        said = "started from " + (
            "models " + ", ".join(str(m) for m in models) + " of the ensemble" if models
            else "different models of the ensemble")
        if axes & (SEED_AXES - {"setup.model"}):
            said += ", each with a seed of its own"
        return said
    return "differing only by random seed" if as_participle else "that differ only by random seed"


def _members_are_replicas(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Whether the campaign repeated one study or compared several."""
    sweep = manifest.get("sweep") or {}
    axes = set(sweep)
    systems = {
        (run.get("system") or run.get("structure"))
        for run in manifest.get("runs", [])
    }
    systems.discard(None)

    if len(systems) > 1:
        return False, (
            f"the members cover {len(systems)} different systems, so the "
            "spread between them is what the campaign set out to compare"
        )
    if not axes:
        return False, (
            "the campaign has no sweep axis, so there is nothing saying the "
            "members are repeats of one another"
        )
    if axes <= SEED_AXES:
        return True, (
            f"the members are replicas {how_replicas_differ(axes)}, so they are "
            "repeats of one estimate, and the spread of their means is its error once each "
            "run has determined its own mean"
        )
    return False, (
        "the members differ by "
        + ", ".join(sorted(axes))
        + ", so the spread between them is a result rather than an error"
    )


def _spread_not_an_error(unbacked: int, runs: int, said_so: int = 0, *,
                         one_structure: bool = True) -> str:
    """Why replicas' spread is not yet their error: ``unbacked`` of the
    ``runs`` did not determine their own mean, ``said_so`` of them by saying
    their mean is not a measurement (an error of their own given or not),
    the rest by giving no error of their own."""
    which = (f"None of the {runs} replicas determined its own mean" if unbacked == runs
             else f"{unbacked} of the {runs} replicas did not determine "
                  f"{'its' if unbacked == 1 else 'their'} own mean")
    plain = unbacked - said_so
    if not said_so:
        how = " with an error of its own"
    elif not plain:
        how = (": it said its mean is not determined" if unbacked == 1
               else ": each said its mean is not determined")
    else:
        how = (f": {said_so} said {'its' if said_so == 1 else 'their'} mean is not "
               f"determined, and {plain} gave no error of {'its' if plain == 1 else 'their'} own")
    begun = "begun from one structure, " if one_structure else ""
    return (f"{which}{how}, so their spread is not yet an "
            f"error: {begun}runs too short to resolve their "
            "correlation time are not yet independent samples.")


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _share_the_start(root: Path, runs: list[dict[str, Any]]) -> None:
    """Replicas' means from the start they share, as the stopping rule takes
    them (:func:`fastmdxplora.simulation.stopping.share_the_start`): each
    run's own equilibration detection keeps a little of the relaxation they
    share from their one structure, and the replicas' spread and pooled
    mean then carry it. Written into each member's record, which says so."""
    from fastmdxplora.simulation.stopping import share_the_start

    members = [member_directory(root, run) for run in runs]
    names = set.intersection(*[
        {p.parent.name for p in member.glob("analysis/*/options.json")} for member in members])
    for name in sorted(names):
        share_the_start(members, name)


def aggregate_members(batch_dir: str | Path, *, share: bool = True) -> dict[str, Any]:
    """Collect a campaign's members into one comparison.

    Returns the per-member values for every analysis that reported a
    recorded mean, and, where the members are replicas, the comparison
    between the error each run estimated for itself and the spread the
    replicas actually show. Replicas are given the start they share first
    (written into their records); with ``share`` False their records are
    read as they stand and nothing is written (All studies' rows).

    The spread of replicas' means is their error only where each run
    determined its own mean, with an error of its own: begun from one
    structure, runs each too short to resolve their correlation time are
    not yet independent samples, however close their means fall. Where any
    run did not, the entry names those runs (``qualified_by_their_own_runs``)
    and says why the spread is not yet an error (``spread_not_an_error``).
    """
    from fastmdxplora import record_access
    from fastmdxplora.image_note import note_of

    root = Path(batch_dir)
    manifest_path = root / "batch_manifest.json"
    if not record_access.is_file(manifest_path):
        return {"members": [], "refused": (
            f"No batch_manifest.json in {root}, so there is no campaign here "
            "to aggregate. A single run is not a campaign."
        )}

    manifest, runs = _finished(root)
    if len(runs) < 2:
        return {"members": [], "refused": (
            f"{len(runs)} member(s) finished, and a comparison across "
            "members needs at least two."
        )}

    replicas, why = _members_are_replicas(manifest)
    if replicas and share:
        _share_the_start(root, runs)

    per_analysis: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        findings = read_member_findings(member_directory(root, run))
        for analysis, record in findings.items():
            mean = record.get("mean") or {}
            if "mean" not in mean:
                continue
            per_analysis.setdefault(analysis, []).append({
                "member": run.get("run_id"),
                "sweep_values": run.get("sweep_values") or {},
                "mean": float(mean["mean"]),
                # NaN where the run could not estimate one: None, as for a
                # run that recorded none (NaN is not JSON a page can read).
                "standard_error": _finite_or_none(mean.get("standard_error")),
                "effective_samples": mean.get("effective_samples"),
                # Carried through rather than summarised away: a member that
                # said its own number was not a measurement must not be
                # averaged into one silently.
                "not_a_measurement": mean.get("not_a_measurement"),
                # Its image note (`image_note`), for the mean of the means
                # to say how many of the replicas' means carry one.
                "solute_image": note_of(mean),
            })

    table: dict[str, Any] = {}
    for analysis, rows in sorted(per_analysis.items()):
        if len(rows) < 2:
            continue
        means = np.array([r["mean"] for r in rows], dtype=float)
        if replicas:
            for r in rows:
                r["error_as_a_replica"] = replica_error(means, r["standard_error"])
        entry: dict[str, Any] = {
            "members": rows,
            "mean_of_means": float(np.mean(means)),
            "spread_of_means": float(np.std(means, ddof=1)),
            "spread_is": (
                "the error on one estimate" if replicas
                else "the difference between the studies compared"),
        }

        # Only an error a run could estimate is a prediction: a run too short
        # to give one records NaN (read as None above), and NaN compared
        # with anything is false, so the spread was said to be "within the
        # factor" of "nan" and the error "supported by repeating it".
        errors = [r["standard_error"] for r in rows
                  if r["standard_error"] is not None and r["standard_error"] > 0]
        # The runs whose own errors do not back the spread: one that said
        # its mean is not a measurement, or gave no error of its own.
        unbacked = [r["member"] for r in rows if r["not_a_measurement"]
                    or r["standard_error"] is None or r["standard_error"] <= 0]
        # Those of them that said so themselves.
        said_so = [r["member"] for r in rows if r["not_a_measurement"]]
        if replicas and unbacked:
            entry["qualified_by_their_own_runs"] = unbacked
            entry["spread_not_an_error"] = _spread_not_an_error(
                len(unbacked), len(rows), len(said_so),
                one_structure="setup.model" not in (manifest.get("sweep") or {}))
        if replicas and not errors:
            observed = float(np.std(means, ddof=1))
            entry["predicted_standard_error"] = None
            entry["observed_standard_error"] = observed
            entry["ratio_observed_to_predicted"] = None
            entry["calibration"] = (
                f"The replicas spread {observed:.3g}. No run gave an error "
                "of its own for this observable, so there is nothing to "
                "check the spread against. " + entry["spread_not_an_error"]
            )
        elif replicas and errors:
            # Two means or more (`rows`), and an error of their own (`errors`).
            check = spread_against_own(means, errors)
            predicted, observed = check["predicted"], check["observed"]
            entry["predicted_standard_error"] = predicted
            entry["observed_standard_error"] = observed
            entry["ratio_observed_to_predicted"] = check["ratio"]
            qualified = entry.get("qualified_by_their_own_runs") or []
            # A prediction from some of the runs is said as such.
            some = ("" if len(errors) == len(rows) else
                    f" (from the {len(errors)} of {len(rows)} runs that "
                    "gave an error of their own)")
            if check["too_tight"]:
                entry["calibration"] = (
                    f"The replicas spread {observed:.3g} where a single run "
                    f"predicted {predicted:.3g}{some}, more than the factor of "
                    f"{CALIBRATION_FACTOR:g} that ten replicas can resolve. "
                    "The single-run estimate reads the correlation time off "
                    "a trajectory too short to contain it, so the error bars "
                    "printed for this observable are too tight"
                    # "The runs said as much" only of runs that said it.
                    + ((". The runs said as much themselves. " if said_so else ". ")
                       + entry["spread_not_an_error"]
                       if qualified else " and the replicas are the honest figure.")
                )
            else:
                entry["calibration"] = (
                    f"The replicas spread {observed:.3g} against a predicted "
                    f"{predicted:.3g}{some}, within the factor of "
                    f"{CALIBRATION_FACTOR:g} that this many replicas can "
                    "resolve"
                    + (". " + entry["spread_not_an_error"] if qualified else
                       ": the error this observable reports for itself "
                       "is supported by repeating it.")
                )
        table[analysis] = entry

    return {
        "members": [r.get("run_id") for r in runs],
        "replicas": replicas,
        "why": why,
        "analyses": table,
        "refused": None if table else (
            "No analysis recorded a mean in two or more members, so "
            "there is nothing to compare across them."
        ),
    }
