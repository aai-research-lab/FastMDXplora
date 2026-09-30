"""Run until what was asked is determined, and no longer.

A fixed length is chosen before anything is known about the system: too
short and the numbers are not measurements, too long and the compute went
on precision nobody needed. `simulation.stop_when` states what the study is
for (a measure and how well it must be determined) and a ceiling, and the study
then runs in pieces: after each, the analyses are read, and the study is
extended, by what the numbers say is needed, only until each measure is
determined to the precision asked, or the ceiling is reached.

One trap is designed around from the start. A run can look equilibrated
while trapped:
if the molecule never visits a second state, its average stops moving and
its error bar shrinks, and it is still wrong. Precision within one run
cannot see that; only runs started independently can. So by default the
rule asks for replicas (a sweep over `simulation.random_seed`) and stops
only when they agree with each other within their own errors, and is then
judged on their combined error, the larger of what the runs claim and what
their spread shows. A study of one run is accepted only when
`independent_starts: not_required` says so, and the record says what that
leaves unchecked.

Replicas that differ only by seed start from one structure, so they test
trapping only as far as the dynamics carry them apart; runs from different
starting structures test it further.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError

__all__ = ["StopTarget", "check_stopping", "judge", "rule_said", "run_until_known",
           "stopping_section", "targets_of"]

#: Where a study's rounds are recorded.
RECORD = "stopping.json"

#: The most a round may add, as a multiple of the production so far, and
#: the least. An estimate made from a short run is itself uncertain: a
#: correlation read low asks for too little, and one round cannot be
#: allowed to spend the whole ceiling on a number that may move.
MOST_PER_ROUND = 3.0
LEAST_PER_ROUND = 0.25

#: Rounds before the loop stops whatever the numbers say.
MOST_ROUNDS = 25

#: Replicas agree when their means scatter no more than this many times
#: what their own errors predict (Cochran's Q over its degrees of freedom),
#: the threshold the pooled estimate uses for segments.
AGREE_WITHIN = 2.0


@dataclass(frozen=True)
class StopTarget:
    analysis: str
    standard_error: float | None = None
    relative_error: float | None = None

    def allowed(self, mean: float) -> float:
        """The largest error that meets this target, for this mean."""
        if self.standard_error is not None:
            return float(self.standard_error)
        return abs(float(mean)) * float(self.relative_error or 0.0)

    def said(self, unit: str = "") -> str:
        if self.standard_error is not None:
            return f"{self.analysis} to ±{self.standard_error:g}" + (f" {unit}" if unit else "")
        return f"{self.analysis} to ±{100 * float(self.relative_error):g}%"


def targets_of(stop_when: dict[str, Any]) -> list[StopTarget]:
    """The measures ``stop_when`` asks for, each checked."""
    measures = stop_when.get("measures")
    if not isinstance(measures, list) or not measures:
        raise StudyError(
            "simulation.stop_when needs `measures`: a list of what must be determined and "
            "how well, e.g. [{analysis: rmsd, standard_error: 0.01}] or "
            "[{analysis: sasa, relative_error: 0.05}].",
            code="config.option.missing_companion", option="simulation.stop_when",
            requires=["measures"])
    targets = []
    for entry in measures:
        if not isinstance(entry, dict) or not entry.get("analysis"):
            raise StudyError(
                f"simulation.stop_when.measures holds {entry!r}, which names no analysis.",
                code="config.option.missing_companion", option="simulation.stop_when.measures",
                requires=["analysis"])
        absolute, relative = entry.get("standard_error"), entry.get("relative_error")
        if (absolute is None) == (relative is None):
            raise StudyError(
                f"simulation.stop_when asks for {entry['analysis']} with "
                + ("both an absolute and a relative error" if absolute is not None
                   else "neither an absolute nor a relative error")
                + ": give one, `standard_error` in the analysis's own unit or "
                "`relative_error` as a fraction of its mean.",
                code="config.option.conflicting",
                options=["simulation.stop_when.measures.standard_error",
                         "simulation.stop_when.measures.relative_error"])
        for name, value in (("standard_error", absolute), ("relative_error", relative)):
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                      or not math.isfinite(value) or value <= 0):
                raise StudyError(
                    f"simulation.stop_when asks for {entry['analysis']}'s {name} to be "
                    f"{value!r}; it has to be a positive number.",
                    code="config.option.out_of_range",
                    option=f"simulation.stop_when.measures.{name}", given=value, minimum=0)
        targets.append(StopTarget(str(entry["analysis"]),
                                  None if absolute is None else float(absolute),
                                  None if relative is None else float(relative)))
    return targets


def judgeable_analyses() -> list[str]:
    """The analyses that record a mean a rule can judge: those that give
    one number per frame."""
    import fastmdxplora.analysis.analyze  # noqa: F401 - registers them
    from fastmdxplora.analysis.orchestrator import _REGISTRY

    return sorted(name for name, cls in _REGISTRY.items()
                  if getattr(cls, "time_series", False))


def replicas_of(config: dict[str, Any]) -> tuple[bool, int]:
    """Whether a study's runs are replicas, and how many runs it makes.

    Replicas are one system swept over the seed alone, the reading the
    members' aggregate takes (`batch.aggregate.SEED_AXES`).
    """
    from fastmdxplora.batch.aggregate import SEED_AXES
    from fastmdxplora.batch.sweep import normalize_sweep, normalize_systems

    systems = normalize_systems(config["systems"]) if config.get("systems") else []
    sweep = normalize_sweep(config["sweep"]) if config.get("sweep") else {}
    runs = len(systems)
    for values in sweep.values():
        runs *= len(values)
    replicas = len(systems) == 1 and bool(sweep) and set(sweep) <= SEED_AXES
    return replicas, runs


def phases_of(config: dict[str, Any]) -> list[str]:
    """The phases a config runs, as the orchestrator reads its lists."""
    from fastmdxplora.orchestrator import PHASES

    include = config.get("include_phase")
    exclude = config.get("exclude_phase") or []
    if include:
        return [p for p in PHASES if p in include]
    return [p for p in PHASES if p not in exclude]


def check_study(config: dict[str, Any]) -> list[StopTarget] | None:
    """`check_stopping` for a whole config, read as the batch layer will
    expand it. What validation runs, so `fastmdx check-config`, the GUI
    and the Agent's repair loop refuse what the run would."""
    replicas, runs = replicas_of(config)
    return check_stopping(config, replicas=replicas, runs=runs, phases=phases_of(config))


def check_stopping(config: dict[str, Any], *, replicas: bool, runs: int,
                   phases: list[str]) -> list[StopTarget] | None:
    """Refuse, before anything runs, a stopping rule this study cannot keep.

    None where the study asks for none, or runs no simulation or analysis
    this time (the pieces of an extension, and an analysis rerun, carry
    the rule and do not apply it).
    """
    simulation = config.get("simulation") or {}
    stop_when = simulation.get("stop_when") if isinstance(simulation, dict) else None
    if not stop_when:
        return None
    if "simulation" not in phases or "analysis" not in phases:
        return None
    if not isinstance(stop_when, dict):
        raise StudyError("simulation.stop_when is a block of settings, not a value.",
                         code="config.option.wrong_type", option="simulation.stop_when",
                         expected_type="mapping", found_type=type(stop_when).__name__)
    targets = targets_of(stop_when)
    judgeable = judgeable_analyses()
    for target in targets:
        if target.analysis not in judgeable:
            # A rule over an analysis with no mean per frame would run to
            # its ceiling, judging nothing, and call that undetermined.
            raise StudyError(
                f"simulation.stop_when judges {target.analysis!r}, which records no "
                "single mean to judge. A measure is one of the analyses that give "
                f"one number per frame: {', '.join(judgeable)}.",
                code="config.option.not_permitted",
                option="simulation.stop_when.measures.analysis",
                given=target.analysis, permitted=judgeable)
    ceiling = stop_when.get("max_duration_ns")
    if not isinstance(ceiling, (int, float)) or isinstance(ceiling, bool) or ceiling <= 0:
        raise StudyError(
            "simulation.stop_when needs `max_duration_ns`, the most production any run "
            "may reach: a study that runs until a measure is determined needs a "
            "point at which it stops trying.",
            code="config.option.missing_companion", option="simulation.stop_when",
            requires=["max_duration_ns"])
    first = first_piece_ns(simulation)
    if first <= 0:
        raise StudyError(
            "simulation.stop_when judges production, and this study runs none "
            "(its duration is zero).", code="config.option.inapplicable",
            option="simulation.stop_when", context="a study with no production")
    if float(ceiling) < first:
        raise StudyError(
            f"simulation.stop_when.max_duration_ns is {ceiling:g} ns, below the "
            f"{first:g} ns the first piece runs.",
            code="config.option.out_of_range", option="simulation.stop_when.max_duration_ns",
            given=ceiling, minimum=first)
    from fastmdxplora.simulation.resume import segmentability

    verdict = segmentability(config)
    if not verdict.allowed:
        raise StudyError(
            f"simulation.stop_when runs a study in pieces, and this one cannot be split: "
            f"{verdict.reason}", code=verdict.code or "simulation.resume.not_segmentable")
    if simulation.get("umbrella"):
        raise StudyError(
            "simulation.stop_when judges the means of plain runs; an umbrella study's "
            "free energy has its own measure of convergence and is not extended by it.",
            code="config.option.inapplicable", option="simulation.stop_when",
            context="an umbrella study")
    independent = str(stop_when.get("independent_starts") or "required")
    if independent not in ("required", "not_required"):
        raise StudyError(
            f"simulation.stop_when.independent_starts is {independent!r}; it is "
            "`required` or `not_required`.", code="config.option.not_permitted",
            option="simulation.stop_when.independent_starts", given=independent,
            permitted=["required", "not_required"])
    if independent == "required" and (not replicas or runs < 2):
        raise StudyError(
            "simulation.stop_when stops when replicas agree, and this study has none. "
            "A single run can look equilibrated while trapped in one state, and its error bar "
            "cannot show it; runs started independently can. Sweep "
            "simulation.random_seed over three or more values, or say "
            "`independent_starts: not_required` to accept precision within one run, "
            "which the record will say was not checked against independent starts.",
            code="simulation.stopping.no_replicas",
            option="simulation.stop_when.independent_starts", runs=runs)
    analysis = config.get("analysis") or {}
    chosen = analysis.get("include") if isinstance(analysis, dict) else None
    if isinstance(chosen, str):
        chosen = [part.strip() for part in chosen.split(",") if part.strip()]
    if chosen:
        missing = [t.analysis for t in targets if t.analysis not in chosen]
        if missing:
            raise StudyError(
                f"simulation.stop_when judges {', '.join(missing)}, which analysis.include "
                "does not run.", code="config.option.missing_companion",
                option="analysis.include", requires=missing)
    return targets


def first_piece_ns(simulation: dict[str, Any]) -> float:
    """The production the study's first piece runs, as the runner resolves it."""
    from fastmdxplora.simulation.runner import plan_stages

    def number(key: str) -> Any:
        value = simulation.get(key)
        return None if value is None or isinstance(value, bool) else value

    timestep = float(simulation.get("timestep_fs") or 2.0)
    steps = plan_stages(
        duration_ns=number("duration_ns"), timestep_fs=timestep,
        nvt_steps=number("nvt_steps"), npt_steps=number("npt_steps"),
        production_steps=number("production_steps"),
        nvt_duration_ns=number("nvt_duration_ns"), npt_duration_ns=number("npt_duration_ns"))
    return steps["production_steps"] * timestep * 1e-6


def rule_said(stop_when: dict[str, Any]) -> str:
    """The rule in one line, for the plan."""
    try:
        targets = targets_of(stop_when)
    except StudyError:
        return "a rule that does not read (it is refused before anything runs)"
    from fastmdxplora.gui.report_dashboard import unit_of

    said = [target.said(unit_of(target.analysis)) for target in targets]
    measures = said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]
    verb = "is" if len(targets) == 1 else "are"
    ceiling = stop_when.get("max_duration_ns")
    replicas = (" and the replicas agree"
                if str(stop_when.get("independent_starts") or "required") == "required"
                else " (one run's own precision, not checked against independent starts)")
    known = f"{measures} {verb} determined{replicas}"
    return (f"{known}; or at {ceiling:g} ns of production"
            if isinstance(ceiling, (int, float)) and not isinstance(ceiling, bool) else known)


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    analysis: str
    met: bool
    said: str
    value: float | None = None
    error: float | None = None
    allowed: float | None = None
    unit: str = ""
    #: Production each run should add for this measure, where it can be said.
    more_ns: float | None = None
    agree: bool | None = None
    replicas: list[dict[str, Any]] = field(default_factory=list)
    #: No mean was recorded at all, which more production does not change:
    #: the analysis did not run, failed, or gives no single number here.
    unrecorded: bool = False

    def as_record(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()
                if v not in (None, [], "") and not (k == "unrecorded" and v is False)}


def _recorded(run: Path, analysis: str) -> dict[str, Any] | None:
    try:
        record = json.loads((run / "analysis" / analysis / "options.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = (record.get("findings") or {}).get("mean") if isinstance(record, dict) else None
    return found if isinstance(found, dict) else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _kept_share(mean: dict[str, Any]) -> float:
    """The share of the frames the mean was taken over, after equilibration."""
    n = mean.get("n_frames")
    discard = mean.get("discard") or 0
    if isinstance(n, int) and n > 0 and isinstance(discard, int):
        return max(0.05, (n - discard) / n)
    return 1.0


def _more_for(error: float, allowed: float, production_ns: float, kept: float) -> float:
    """Production to add for an error to fall to what is allowed: the error
    of a mean goes as one over the root of the frames it rests on, and only
    the frames after equilibration count."""
    if error <= allowed:
        return 0.0
    return production_ns * kept * ((error / allowed) ** 2 - 1.0)


def judge(runs: list[Path], targets: list[StopTarget], production_ns: float) -> list[Verdict]:
    """Each target, judged on the runs as they stand."""
    from fastmdxplora.gui.report_dashboard import unit_of

    verdicts = []
    for target in targets:
        records = [(run, _recorded(run, target.analysis)) for run in runs]
        missing = [run.name for run, found in records if found is None]
        if missing:
            verdicts.append(Verdict(target.analysis, False,
                                    f"{target.analysis} recorded no mean"
                                    + (f" in {', '.join(missing)}" if len(runs) > 1 else ""),
                                    unrecorded=True))
            continue
        unit = unit_of(target.analysis, records[0][1])
        withheld = [(run, found) for run, found in records
                    if found.get("not_a_measurement") or _finite(found.get("mean")) is None
                    or _finite(found.get("standard_error")) is None]
        if withheld:
            # Not yet a measurement: the analysis's own shortfall, where it
            # recorded one in nanoseconds, else as long again.
            asks = []
            for _run, found in withheld:
                short = found.get("shortfall") if isinstance(found.get("shortfall"), dict) else {}
                asks.append(_finite(short.get("more_ns")) or production_ns)
            where = "" if len(runs) == 1 else f" in {len(withheld)} of {len(runs)} runs"
            verdicts.append(Verdict(target.analysis, False,
                                    f"{target.analysis} is not yet a measurement{where}",
                                    unit=unit, more_ns=max(asks)))
            continue
        means = [float(found["mean"]) for _run, found in records]
        errors = [float(found["standard_error"]) for _run, found in records]
        kept = min(_kept_share(found) for _run, found in records)
        if len(runs) == 1:
            allowed = target.allowed(means[0])
            met = errors[0] <= allowed
            verdicts.append(Verdict(
                target.analysis, met,
                f"{target.analysis} {means[0]:.4g} ± {errors[0]:.2g}"
                + (f" {unit}" if unit else "")
                + (", within" if met else ", outside") + f" the ±{allowed:.2g} asked",
                value=means[0], error=errors[0], allowed=allowed, unit=unit,
                more_ns=None if met else _more_for(errors[0], allowed, production_ns, kept)))
            continue
        verdicts.append(_judge_replicas(target, runs, means, errors, unit, production_ns, kept))
    return verdicts


def _judge_replicas(target: StopTarget, runs: list[Path], means: list[float],
                    errors: list[float], unit: str, production_ns: float,
                    kept: float) -> Verdict:
    """Replicas: they must agree with each other within their own errors,
    and the combined error is the larger of what they claim and what their
    spread shows."""
    import numpy as np

    from fastmdxplora.statistics import heterogeneity_ratio

    m = np.asarray(means)
    w = 1.0 / np.square(np.maximum(np.asarray(errors), 1e-300))
    pooled = float((w * m).sum() / w.sum())
    claimed = float(1.0 / math.sqrt(w.sum()))
    spread = float(np.std(m, ddof=1) / math.sqrt(m.size))
    error = max(claimed, spread)
    scatter = heterogeneity_ratio(m, w)
    agree = scatter <= AGREE_WITHIN
    allowed = target.allowed(pooled)
    replicas = [{"run": run.name, "mean": mean, "standard_error": err}
                for run, mean, err in zip(runs, means, errors)]
    u = f" {unit}" if unit else ""
    if not agree:
        return Verdict(
            target.analysis, False,
            f"{target.analysis}: the {len(runs)} replicas disagree, scattering "
            f"{scatter:.1f} times what their own errors predict. Each may be sampling a "
            "different part of the landscape, or their errors are too small; either way "
            "no one of them is yet the answer",
            value=pooled, error=error, allowed=allowed, unit=unit,
            more_ns=production_ns, agree=False, replicas=replicas)
    met = error <= allowed
    return Verdict(
        target.analysis, met,
        f"{target.analysis} {pooled:.4g} ± {error:.2g}{u} across {len(runs)} replicas "
        f"that agree" + (", within" if met else ", outside") + f" the ±{allowed:.2g} asked",
        value=pooled, error=error, allowed=allowed, unit=unit,
        more_ns=None if met else _more_for(error, allowed, production_ns, kept),
        agree=True, replicas=replicas)


def next_piece(verdicts: list[Verdict], production_ns: float, ceiling_ns: float,
               frame_ns: float | None) -> float:
    """How much every run adds next: the most any unmet measure asks for,
    bounded per round, never past the ceiling, and a whole number of frames."""
    asked = max((v.more_ns or production_ns) for v in verdicts if not v.met)
    more = min(max(asked, LEAST_PER_ROUND * production_ns), MOST_PER_ROUND * production_ns)
    more = min(more, ceiling_ns - production_ns)
    if frame_ns and frame_ns > 0:
        frames = math.ceil(more / frame_ns - 1e-9)
        more = frames * frame_ns
        if production_ns + more > ceiling_ns + 1e-9:
            more = math.floor((ceiling_ns - production_ns) / frame_ns + 1e-9) * frame_ns
    return round(max(0.0, more), 9)


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def _frame_ns(run: Path) -> float | None:
    from fastmdxplora.simulation.resume import last_segment, trajectory_interval_of

    try:
        import yaml

        config = yaml.safe_load((run / "resolved_config.yml").read_text(encoding="utf-8")) or {}
        timestep = float((config.get("simulation") or {}).get("timestep_fs") or 2.0)
        interval = trajectory_interval_of(last_segment(run))
    except Exception:  # noqa: BLE001 - rounding is a nicety, not a requirement
        return None
    return interval * timestep * 1e-6 if interval else None


def extend_one_after_another(runs: list[Path], more_ns: float) -> list[dict[str, Any]]:
    """Each run extended in turn, stopping at the first that fails."""
    from fastmdxplora.simulation.resume import extend_study

    answers = []
    for run in runs:
        answers.append(extend_study(run, more_ns=more_ns))
        if not answers[-1].get("ok"):
            break
    return answers


def planned_record(targets: list[StopTarget], stop_when: dict[str, Any],
                   runs: list[str]) -> dict[str, Any]:
    """The record before the first piece is judged, so a study running
    its first piece already says what it is running until."""
    return {"targets": [t.__dict__ for t in targets],
            "max_duration_ns": float(stop_when["max_duration_ns"]),
            "independent_starts": str(stop_when.get("independent_starts") or "required"),
            "runs": list(runs), "rounds": [], "outcome": "running",
            "said": "Running the first piece; it is judged when its analyses are done."}


def run_until_known(runs: list[Path], targets: list[StopTarget], stop_when: dict[str, Any],
                    *, record_in: Path, extend_all: Any = None,
                    say: Any = print, at_once: int = 1,
                    earlier_rounds: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Judge the runs, extend every one of them by what is needed, and judge
    again, until each target is met or the ceiling is reached. The rounds
    are written to ``record_in / stopping.json`` as they happen.

    ``extend_all(runs, more_ns)`` extends the runs and returns an answer
    for each it tried, as `extend_study` gives them; a campaign passes one
    that runs them side by side, and says in ``at_once`` how many run
    together, which is what the time a round takes rests on.
    ``earlier_rounds`` are a resumed study's rounds before it stopped,
    kept so its record reads as one history."""
    from fastmdxplora.simulation.resume import production_done_ns

    extend_all = extend_all or extend_one_after_another
    ceiling = float(stop_when["max_duration_ns"])
    independent = str(stop_when.get("independent_starts") or "required")
    record: dict[str, Any] = {
        "targets": [t.__dict__ for t in targets], "max_duration_ns": ceiling,
        "independent_starts": independent, "runs": [run.name for run in runs],
        "at_once": max(1, int(at_once)), "rounds": list(earlier_rounds or []),
        "outcome": "running"}

    def write() -> None:
        (record_in / RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")

    for _ in range(MOST_ROUNDS):
        production = min(production_done_ns(run) for run in runs)
        verdicts = judge(runs, targets, production)
        entry: dict[str, Any] = {"production_ns": round(production, 9),
                                 "verdicts": [v.as_record() for v in verdicts]}
        record["rounds"].append(entry)
        unrecorded = [v for v in verdicts if v.unrecorded]
        if unrecorded:
            entry["decision"] = "stopped"
            record["outcome"] = "stopped"
            record["said"] = (
                "Stopped: " + "; ".join(v.said for v in unrecorded)
                + ". More production does not give an analysis a mean it does not "
                "record; its own log says why.")
            write()
            say(record["said"])
            return record
        if all(v.met for v in verdicts):
            entry["decision"] = "met"
            record["outcome"] = "met"
            record["said"] = _said_met(verdicts, production, len(runs), independent)
            write()
            say(record["said"])
            return record
        if production >= ceiling - 1e-9:
            entry["decision"] = "ceiling"
            record["outcome"] = "ceiling"
            record["said"] = (
                f"Stopped at the {ceiling:g} ns ceiling, with what was asked not "
                "determined to the precision asked: "
                + "; ".join(v.said for v in verdicts if not v.met) + ".")
            write()
            say(record["said"])
            return record
        more = next_piece(verdicts, production, ceiling, _frame_ns(runs[0]))
        if more <= 0:
            entry["decision"] = "ceiling"
            record["outcome"] = "ceiling"
            record["said"] = (f"Stopped: the {ceiling:g} ns ceiling leaves no whole frame "
                              "to add, with what was asked not determined to the "
                              "precision asked: " + "; ".join(v.said for v in verdicts if not v.met)
                              + ".")
            write()
            say(record["said"])
            return record
        entry["decision"] = "extend"
        entry["more_ns"] = more
        record["said"] = (f"Extending by {more:g} ns, to {production + more:g} ns of the "
                          f"{ceiling:g} allowed: " + "; ".join(v.said for v in verdicts
                                                             if not v.met) + ".")
        write()
        say(f"Not yet determined: {'; '.join(v.said for v in verdicts if not v.met)}. "
            f"Extending {'every run' if len(runs) > 1 else 'the run'} by {more:g} ns "
            f"(to {production + more:g} ns of the {ceiling:g} allowed).")
        answers = list(extend_all(runs, more))
        failed = [(run, answer) for run, answer in zip(runs, answers) if not answer.get("ok")]
        if failed or len(answers) < len(runs):
            run, answer = failed[0] if failed else (runs[len(answers)], {})
            entry["decision"] = "stopped"
            record["outcome"] = "stopped"
            if answer.get("stopped"):
                record["said"] = (f"Stopped when asked, while extending {run.name}. "
                                  "Resuming the study judges the runs again and carries "
                                  "on from there.")
            else:
                record["said"] = (f"Stopped: extending {run.name} failed at "
                                  f"{answer.get('stage') or 'a step'}: "
                                  f"{answer.get('error') or 'no reason recorded'}")
            write()
            say(record["said"])
            return record
    record["outcome"] = "rounds"
    record["said"] = (f"Stopped after {MOST_ROUNDS} rounds with what was asked not "
                      "determined to the precision asked.")
    write()
    say(record["said"])
    return record


def _said_met(verdicts: list[Verdict], production: float, runs: int, independent: str) -> str:
    known = "; ".join(v.said for v in verdicts)
    each = " in each run" if runs > 1 else ""
    text = f"After {production:g} ns of production{each}: {known}."
    if runs == 1:
        text += (" One run: precise within itself, and not checked against independent "
                 "starts, so a run trapped in one state would look the same "
                 "(independent_starts: not_required).")
    else:
        text += (f" The {runs} replicas started from one structure and differ by seed, so "
                 "they check trapping as far as their dynamics carried them apart.")
    return text


# ---------------------------------------------------------------------------
# The record, written up
# ---------------------------------------------------------------------------

_DECIDED = {"met": "stopped: determined as asked", "extend": "extended by {more:g} ns",
            "ceiling": "stopped at the ceiling", "stopped": "stopped: an extension failed"}


def stopping_section(root: str | Path) -> list[str]:
    """The report's account of a study run until it knew: the rule, each
    round's numbers and what was decided on them. Empty where the study
    ran to a fixed length."""
    try:
        record = json.loads((Path(root) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(record, dict) or not record.get("rounds"):
        return []
    targets = [StopTarget(**t) for t in record.get("targets") or []]
    runs = record.get("runs") or []
    ceiling = record.get("max_duration_ns")
    units = {v.get("analysis"): v.get("unit") or ""
             for entry in record["rounds"] for v in entry.get("verdicts") or []}
    lines = ["## How long it ran, and why", "",
             "This study did not run for a length fixed in advance. It stated what it "
             "was for, " + "; ".join(t.said(units.get(t.analysis, "")) for t in targets)
             + (f", with at most {ceiling:g} ns of production" if ceiling else "")
             + (f" in each of {len(runs)} runs" if len(runs) > 1 else "")
             + ", and ran in pieces: after each, the analyses were read and the study "
             "was extended by what the numbers said was still needed.", ""]
    names = [t.analysis for t in targets]
    lines.append("| round | production (ns) | " + " | ".join(names) + " | decision |")
    lines.append("|---|---|" + "---|" * len(names) + "---|")
    for number, entry in enumerate(record["rounds"], start=1):
        verdicts = {v.get("analysis"): v for v in entry.get("verdicts") or []}
        cells = []
        for name in names:
            v = verdicts.get(name) or {}
            if v.get("value") is None or v.get("error") is None:
                cells.append("not yet a measurement")
                continue
            cell = f"{v['value']:.4g} ± {v['error']:.2g} (±{v['allowed']:.2g} asked)"
            if v.get("agree") is False:
                cell += ", replicas disagree"
            cells.append(cell)
        decided = _DECIDED.get(entry.get("decision"), str(entry.get("decision") or ""))
        decided = decided.format(more=entry.get("more_ns") or 0.0)
        lines.append(f"| {number} | {entry.get('production_ns', 0):g} | "
                     + " | ".join(cells) + f" | {decided} |")
    lines.append("")
    if record.get("said"):
        lines += [f"**{_OUTCOME.get(record.get('outcome'), 'Outcome')}.** {record['said']}", ""]
    if record.get("outcome") == "met":
        lines += [
            "Stopping as soon as an error falls below a target favours a round whose error "
            "came out small by chance, so an error judged this way is biased a little low. "
            + ("Between replicas the combined error is the larger of what the runs claim "
               "and what their spread shows, which limits that. " if len(runs) > 1 else "")
            + "Each round adds at least a quarter of what ran before, so the rule is not "
            "consulted after every frame.", ""]
    return lines


_OUTCOME = {"met": "Determined as asked", "ceiling": "Not determined as asked",
            "stopped": "Stopped early", "rounds": "Not determined as asked",
            "not_applied": "Not applied", "running": "Still running"}
