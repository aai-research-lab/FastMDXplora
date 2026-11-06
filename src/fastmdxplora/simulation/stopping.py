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

import contextlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from fastmdxplora.refusals import StudyError
from fastmdxplora.statistics import with_its_error

__all__ = ["RoundEnded", "StopTarget", "brought_up", "check_stopping", "judge", "round_ended",
           "round_length", "rule_said", "run_until_known", "stopping_section", "targets_of"]

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

#: A single run is judged on its own error only once that error rests on this
#: many independent samples. An error estimated from one run is itself
#: uncertain, and stopping at the first look where it falls within the target
#: keeps the looks where it read low: calibrated on series with a known mean
#: (``preregistration/stopping-calibration.md``), single runs stopped this way
#: reported errors about half what the truth needed. Replicas need no such
#: floor, since their spread checks what each claims.
JUDGED_ALONE = 50.0

#: The share of the time the error judged should hold the truth: one
#: standard error of a normal mean.
ONE_ERROR = 0.8413447460685429


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
                "single mean to judge. A quantity it judges comes from one of the analyses that give "
                f"one number per frame: {', '.join(judgeable)}.",
                code="config.option.not_permitted",
                option="simulation.stop_when.measures.analysis",
                given=target.analysis, permitted=judgeable)
    ceiling = stop_when.get("max_duration_ns")
    if not isinstance(ceiling, (int, float)) or isinstance(ceiling, bool) or ceiling <= 0:
        raise StudyError(
            "simulation.stop_when needs `max_duration_ns`, the most production any run "
            "may reach: a study that runs until a quantity is determined needs a "
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
            "free energy has its own convergence criteria and is not extended by it.",
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
            "simulation.random_seed over three or more values (or setup.model over "
            "the models of an ensemble), or say "
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


def _judged_error(found: dict[str, Any]) -> float:
    """A run's error as judged: its standard error widened by Student's t at
    the error's own degrees of freedom, so that it holds the truth as often
    as one standard error should. With eight degrees of freedom that is 7%
    wider; a record without them (written before they were) is taken as it
    stands."""
    error = float(found["standard_error"])
    dof = _finite(found.get("degrees_of_freedom"))
    if dof is None or dof <= 0:
        return error
    from scipy.stats import t

    return error * float(t.ppf(ONE_ERROR, dof))


def _more_for(error: float, allowed: float, production_ns: float, kept: float) -> float:
    """Production to add for an error to fall to what is allowed: the error
    of a mean goes as one over the root of the frames it rests on, and only
    the frames after equilibration count."""
    if error <= allowed:
        return 0.0
    return production_ns * kept * ((error / allowed) ** 2 - 1.0)


#: What a run's mean record says that is worked out again from a shared start.
_RECOMPUTED = frozenset({
    "discard", "statistical_inefficiency", "effective_samples", "mean", "standard_error",
    "standard_deviation", "degrees_of_freedom", "not_a_measurement", "shortfall", "n_frames",
    "start_shared_with_replicas", "segment_joins", "drift_test", "withheld_code",
    "drift_unseen"})


def _series_in(path: Path) -> "Any | None":
    """The last number on each data line of an analysis's `.dat` file: the
    series its mean was taken over. Comments and a header are left out."""
    import numpy as np

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    values = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        last = text.replace(",", " ").split()[-1]
        try:
            values.append(float(last))
        except ValueError:
            continue
    return np.asarray(values, dtype=float)


def share_the_start(runs: list[Path], analysis: str, production_ns: float | None = None) -> int:
    """Write each replica's mean again from the start found on their average.

    Replicas over a seed begin in one structure and share its relaxation.
    Each run's own equilibration detection sees that relaxation through the
    run's noise and keeps a little of it, and three replicas keep the same
    little: calibrated on series with a known mean
    (``preregistration/stopping-calibration.md``), their pooled mean was
    biased by about half its error. On the frame-by-frame average of the
    replicas the relaxation is the same and the noise smaller, so the start
    found there (:func:`fastmdxplora.statistics.shared_start`) is later where
    the relaxation is real. Each run then averages from the later of its own
    start and the shared one, and its record says so.

    Done only where every run has its series beside its record, of the
    length the record says; otherwise the records are judged as written.
    ``production_ns`` sizes a withheld mean's shortfall in time; without it
    the shortfall is in frames. Returns the shared start, in frames.
    """
    from fastmdxplora.statistics import mean_record, shared_start

    found = []
    for run in runs:
        folder = run / "analysis" / analysis
        try:
            document = json.loads((folder / "options.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        record = (document.get("findings") or {}).get("mean") if isinstance(document, dict) else None
        series = _series_in(folder / f"{analysis}.dat")
        if (not isinstance(record, dict) or series is None or not series.size
                or record.get("n_frames") != series.size):
            return 0
        found.append((folder / "options.json", document, record, series))
    at = shared_start([series for *_, series in found])
    for path, document, record, series in found:
        shared = record.get("start_shared_with_replicas")
        if shared == at or (not shared and at <= int(record.get("discard") or 0)):
            continue
        again = mean_record(series, start_at_least=at,
                            joins=record.get("segment_joins") or (),
                            unit=record.get("unit") or record.get("units") or None,
                            frame_interval_ns=(production_ns / series.size
                                               if production_ns else None))
        kept = {k: v for k, v in record.items() if k not in _RECOMPUTED}
        again = {k: (str(v) if k == "not_a_measurement" else v) for k, v in again.items()}
        document["findings"]["mean"] = {**kept, **again}
        try:
            path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        except OSError:
            # A study that cannot be written to (one served read-only) is
            # read as its analyses wrote it.
            return 0
    return at


def judge(runs: list[Path], targets: list[StopTarget], production_ns: float) -> list[Verdict]:
    """Each target, judged on the runs as they stand. Replicas are first
    given the start they share (:func:`share_the_start`)."""
    from fastmdxplora.gui.report_dashboard import unit_of

    verdicts = []
    for target in targets:
        if len(runs) > 1:
            share_the_start(runs, target.analysis, production_ns)
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
                                    f"{target.analysis} is not yet determined{where}",
                                    unit=unit, more_ns=max(asks)))
            continue
        means = [float(found["mean"]) for _run, found in records]
        errors = [_judged_error(found) for _run, found in records]
        kept = min(_kept_share(found) for _run, found in records)
        if len(runs) == 1:
            allowed = target.allowed(means[0])
            met = errors[0] <= allowed
            more = None if met else _more_for(errors[0], allowed, production_ns, kept)
            alone = _finite(records[0][1].get("effective_samples"))
            short = met and alone is not None and alone < JUDGED_ALONE
            if short:
                met = False
                more = production_ns * kept * (JUDGED_ALONE / max(alone, 1e-9) - 1.0)
            verdicts.append(Verdict(
                target.analysis, met,
                f"{target.analysis} {with_its_error(means[0], errors[0])}"
                + (f" {unit}" if unit else "")
                + (f", within the ±{allowed:.2g} asked but resting on {alone:.0f} "
                   f"independent samples, and one run is judged alone on "
                   f"{JUDGED_ALONE:g}" if short else
                   (", within" if met else ", outside") + f" the ±{allowed:.2g} asked"),
                value=means[0], error=errors[0], allowed=allowed, unit=unit,
                more_ns=more))
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
        f"{target.analysis} {with_its_error(pooled, error)}{u} across {len(runs)} replicas "
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


#: Told the name of each run whose extension in a round begins
#: (`extension_begins`), while `run_until_known` judges and extends.
_BEGINNING: list[Any] = []


def extension_begins(run: str | Path) -> None:
    """A run's extension in its study's round begins: recorded in the
    round's ``started`` as it begins, so a round cut after it says which
    runs' extensions never began."""
    for note in list(_BEGINNING):
        note(Path(run).name)


def to_begin_another() -> bool:
    """Whether another run's extension in the round may begin: not once a
    stop was asked in the round (`runner.a_stop_asked_in_the_rounds`)."""
    from fastmdxplora.simulation.runner import a_stop_asked_in_the_rounds

    return not a_stop_asked_in_the_rounds()


def extend_one_after_another(runs: list[Path], more_ns: float) -> list[dict[str, Any]]:
    """Each run extended in turn, stopping at the first that fails."""
    from fastmdxplora.simulation.resume import extend_study

    answers = []
    for run in runs:
        if not to_begin_another():
            break
        extension_begins(run)
        answers.append(extend_study(run, more_ns=more_ns))
        if not answers[-1].get("ok"):
            break
    return answers


def extend_each_to(runs: list[Path], total_ns: float) -> list[dict[str, Any]]:
    """Each run extended in turn to ``total_ns`` of production, stopping at
    the first that fails."""
    from fastmdxplora.simulation.resume import extend_study

    answers = []
    for run in runs:
        if not to_begin_another():
            break
        extension_begins(run)
        answers.append(extend_study(run, total_ns=total_ns))
        if not answers[-1].get("ok"):
            break
    return answers


def _the_round_stopped(record: dict[str, Any], entry: dict[str, Any], run: str | None,
                       answer: dict[str, Any]) -> None:
    """The round ``entry`` said stopped by the answer of ``run``'s
    extension: when asked (a stop in production, or the stop's refusal in
    a phase after it), an error, or a worker that ended abruptly, whose
    run is not known (every run of a broken pool gets its error)."""
    from fastmdxplora.simulation.runner import STOPPED_CODE

    refusal = answer.get("refusal") if isinstance(answer.get("refusal"), dict) else {}
    asked = bool(answer.get("stopped")) or refusal.get("code") == STOPPED_CODE
    entry["decision"] = "stopped"
    entry["stopped_by"] = "asked" if asked else "error"
    record["outcome"] = "stopped"
    if asked:
        # What resuming runs first is the round's sentence's to say
        # (`round_ended`): it may join or carry pieces on before it judges.
        record["said"] = ((f"Stopped when asked, while extending {run}. " if run else
                           "Stopped when asked, while the runs were judged, before any "
                           "extension began. ")
                          + "Resuming the study carries its round on from there.")
    elif answer.get("lost"):
        record["said"] = ("Stopped: a worker extending the runs ended abruptly "
                          f"({answer.get('error') or 'no reason recorded'}), and which "
                          "run it was extending is not known")
    else:
        record["said"] = (f"Stopped: extending {run} failed at "
                          f"{answer.get('stage') or 'a step'}: "
                          f"{answer.get('error') or 'no reason recorded'}")
    # The round's own sentence, saved with it: the record's is written
    # over when the study is resumed.
    entry["said"] = record["said"]


def round_not_carried_on(study: str | Path, run: str, answer: dict[str, Any]) -> None:
    """A resume that did not carry on the round its study's process was
    cut in (its record says a round is going): the round said stopped by
    what ended the resume, so the record does not say "running" after it.
    A round already said failed or stopped is left as it says."""
    where = Path(study) / RECORD
    try:
        record = json.loads(where.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    rounds = record.get("rounds") if isinstance(record, dict) else None
    if record.get("outcome") != "running" or not rounds or not isinstance(rounds[-1], dict):
        return
    _the_round_stopped(record, rounds[-1], run, answer)
    where.write_text(json.dumps(record, indent=2), encoding="utf-8")


def _past_the_ceiling(productions: dict[str, float | None],
                      ceiling_ns: float | None) -> list[str]:
    """The runs past the ceiling, where the runs are not at one length (a
    ceiling lowered since their round was cut): brought up to them, the
    others would pass it, so they are not judged together."""
    known = {name: ns for name, ns in productions.items() if ns is not None}
    if (len(known) < 2 or max(known.values()) - min(known.values()) <= _ONE_LENGTH_NS
            or not isinstance(ceiling_ns, (int, float)) or isinstance(ceiling_ns, bool)):
        return []
    return [name for name, ns in known.items() if ns > float(ceiling_ns) + _ONE_LENGTH_NS]


def _held_below_the_ceiling(names: list[str], productions: dict[str, float | None],
                            found: dict[str, dict[str, Any] | None], length: float | None,
                            ceiling: float | None) -> tuple[list[str], list[str]]:
    """What a ceiling lowered since the round keeps a resume from running:
    the runs past it where the runs are not at one length
    (`_past_the_ceiling`), and the runs whose cut extension a resume would
    carry on to the round's length, past it (`resume.how_a_resume_finds`).
    A resume then carries no run on, and `round_ended` says so."""
    over = _past_the_ceiling(productions, ceiling)
    would = [name for name in names
             if ceiling is not None and length is not None
             and length > float(ceiling) + _ONE_LENGTH_NS
             and (found.get(name) or {}).get("carried_on_first")
             and productions.get(name) is not None
             and productions[name] < length - _ONE_LENGTH_NS]
    return over, would


def runs_kept_below_the_ceiling(study: str | Path) -> list[str]:
    """The runs that keep a resume of the study from running anything, by
    the ceiling its config now sets (`_held_below_the_ceiling`), as
    `round_ended` reads them; read by `BatchExplorer` and `_resume_study`."""
    study = Path(study)
    try:
        record = json.loads((study / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rounds = [entry for entry in record.get("rounds") or [] if isinstance(entry, dict)]
    names = [str(name) for name in record.get("runs") or []]
    if not rounds or not names:
        return []
    productions = _production_of_each(study, names)
    over, would = _held_below_the_ceiling(names, productions, _what_resume_finds(study, names),
                                          round_length(rounds),
                                          _ceiling_a_resume_uses(study, record))
    return over + [name for name in would if name not in over]


def _past_the_ceiling_said(past: list[str], ceiling_ns: float) -> str:
    from fastmdxplora.simulation.sampling_ask import length_said

    return (f"Stopped: {_and(past)} {'is' if len(past) == 1 else 'are'} past the "
            f"{length_said(ceiling_ns)} ceiling the study now sets, so the runs, not at "
            "one length, are not judged together; with `max_duration_ns` at least the "
            "longest run's production, a resume brings them to one length and judges them.")


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
                    *, record_in: Path, **given: Any) -> dict[str, Any]:
    """`_run_until_known`, a stop asked while it judges and extends the runs
    noted (`runner._StopInTheRounds`), never raised where the round would
    read cut, and each run's extension recorded as it begins."""
    from fastmdxplora.simulation.runner import _StopInTheRounds

    current: dict[str, Any] = {}

    def begun(name: str) -> None:
        entry, write = current.get("entry"), current.get("write")
        if entry is None or write is None:
            return
        started = entry.setdefault("started", [])
        if name not in started:
            started.append(name)
            write()

    _BEGINNING.append(begun)
    try:
        with _StopInTheRounds(folder=record_in) as heard:
            return _run_until_known(runs, targets, stop_when, record_in=record_in,
                                    heard=heard, current=current, **given)
    finally:
        _BEGINNING.remove(begun)


def _run_until_known(runs: list[Path], targets: list[StopTarget], stop_when: dict[str, Any],
                     *, record_in: Path, extend_all: Any = None,
                     say: Any = print, at_once: int = 1,
                     earlier_rounds: list[dict[str, Any]] | None = None,
                     production_of: Any = None, extend_to: Any = None,
                     heard: Any = None, current: dict[str, Any] | None = None
                     ) -> dict[str, Any]:
    """Judge the runs, extend every one of them by what is needed, and judge
    again, until each target is met or the ceiling is reached. The rounds
    are written to ``record_in / stopping.json`` as they happen.

    ``extend_all(runs, more_ns)`` extends the runs and returns an answer
    for each it tried, as `extend_study` gives them; a campaign passes one
    that runs them side by side, and says in ``at_once`` how many run
    together, which is what the time a round takes rests on.
    ``earlier_rounds`` are a resumed study's rounds before it stopped,
    kept so its record reads as one history. ``production_of(run)`` says how
    much production a run has done; it is read from the run's checkpoints
    unless given, which the rule's calibration does with series it makes
    itself (`validation/stopping_calibration.py`).

    A resumed study whose runs are not at one length (its last round was
    cut while it extended them) first brings the shorter ones up to the
    round's length (`brought_up`), with ``extend_to(runs, total_ns)``, and
    judges them there: judged at the shortest and extended by one length
    from there, the runs the round had extended went past the ceiling."""
    from fastmdxplora.simulation.resume import production_done_ns
    from fastmdxplora.simulation.sampling_ask import length_said

    extend_all = extend_all or extend_one_after_another
    extend_to = extend_to or extend_each_to
    production_of = production_of or production_done_ns
    ceiling = float(stop_when["max_duration_ns"])
    independent = str(stop_when.get("independent_starts") or "required")
    record: dict[str, Any] = {
        "targets": [t.__dict__ for t in targets], "max_duration_ns": ceiling,
        "independent_starts": independent, "runs": [run.name for run in runs],
        "at_once": max(1, int(at_once)), "rounds": list(earlier_rounds or []),
        "outcome": "running"}

    def write() -> None:
        (record_in / RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")

    current = {} if current is None else current
    current["write"] = write

    def asked() -> bool:
        return bool(heard is not None and heard.requested)

    def ended_by(entry: dict[str, Any], tried: list[Path],
                 answers: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The round stopped where an extension of ``tried`` failed or was
        stopped when asked, or a stop was asked between its pieces (a join,
        a run's start: `runner._StopInTheRounds`); None where each finished
        with no stop asked."""
        failed = [(run, answer) for run, answer in zip(tried, answers) if not answer.get("ok")]
        if not failed and len(answers) >= len(tried) and not asked():
            return None
        if failed:
            run, answer = failed[0]
        elif asked() and not answers:
            # Asked while the rule judged the runs, before any extension
            # began: no run named as being extended.
            run, answer = None, {"stopped": True}
        elif asked():
            # The run its round was working on when asked.
            run, answer = tried[len(answers) - 1], {"stopped": True}
        else:
            run, answer = tried[len(answers)], {}
        # The runs whose extension finished: side by side, the others
        # can, and the runs are then not at one length.
        entry["extended"] = [r.name for r, a in zip(tried, answers) if a.get("ok")]
        # One after another, a run after the one that ended was never begun:
        # as each began (`extension_begins`), else from the answers.
        began = entry.get("started")
        entry["started"] = ([str(name) for name in began] if isinstance(began, list) and began
                            else [r.name for r, _ in zip(tried, answers)])
        # A study of one run is named "the run", not by its folder; runs
        # stopped side by side are each named.
        if run is None:
            named = None
        else:
            named = run.name
            if answer.get("stopped"):
                named = _and([r.name for r, a in failed if a.get("stopped")] or [run.name])
            named = named if len(runs) > 1 else "the run"
        _the_round_stopped(record, entry, named, answer)
        write()
        say(record["said"])
        return record

    past = _past_the_ceiling({run.name: production_of(run) for run in runs},
                             ceiling) if record["rounds"] else []
    if past:
        # A ceiling lowered since the round: those runs are not brought
        # down, and the others brought up to them would pass it, so the
        # runs are not judged together. Said, with what would judge them.
        # No round is recorded: none was judged, and the round before is
        # said as it ended (`round_ended`) until the ceiling is raised.
        say(_past_the_ceiling_said(past, ceiling))
        return record

    if record["rounds"]:
        productions = {run.name: production_of(run) for run in runs}
        target, short = brought_up(record["rounds"], productions, ceiling)
        if short and target is not None:
            lowest = min(productions[name] for name in short)
            longer = [run.name for run in runs if run.name not in short]
            say(f"Extending {_and(short)} to {length_said(target)}"
                + (f", the length of {_and(longer)}" if longer else "")
                + ", before the runs are judged again.")
            # Said as a round of its own, extending the shorter runs from
            # the shortest, so a failure here reads as any round's.
            entry = {"production_ns": round(lowest, 9), "verdicts": [],
                     "decision": "extend", "more_ns": round(target - lowest, 9),
                     "brought_up": list(short)}
            record["rounds"].append(entry)
            record["said"] = entry["said"] = (
                f"Extending {_and(short)} to {length_said(target)}, the length the "
                "other runs reached, before the runs are judged again.")
            entry["started"] = []
            current["entry"] = entry
            write()
            tried = [run for run in runs if run.name in short]
            stopped = ended_by(entry, tried, [] if asked() else list(extend_to(tried, target)))
            if stopped is not None:
                previous = record["rounds"][-2] if len(record["rounds"]) > 1 else {}
                if (entry.get("stopped_by") == "asked" and previous.get("stopped_by") == "asked"
                        and all(abs(production_of(run) - productions[run.name])
                                <= _ONE_LENGTH_NS
                                for run in runs if productions.get(run.name) is not None)):
                    # Stopped when asked before any run moved, carrying on a
                    # round itself stopped when asked: that round is said
                    # stopped by this, with what this began and extended,
                    # not a second round recorded at the same lengths. A
                    # round that failed or was cut keeps its own ending, and
                    # this is said beside it.
                    record["rounds"].pop()
                    previous.update(decision="stopped", stopped_by="asked",
                                    said=entry["said"], started=entry.get("started", []),
                                    extended=entry.get("extended", []))
                    write()
                return stopped

    last = record["rounds"][-1] if record["rounds"] else {}
    try:
        before = json.loads((record_in / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = {}
    # The rule as judged then: its targets and independent starts, as now.
    same_rule = (isinstance(before, dict) and before.get("targets") == record["targets"]
                 and str(before.get("independent_starts") or "required") == independent)
    if (same_rule and last.get("decision") == "ceiling"
            and isinstance(last.get("production_ns"), (int, float))
            and float(last["production_ns"]) >= ceiling - 1e-9
            and all(abs(production_of(run) - float(last["production_ns"])) <= _ONE_LENGTH_NS
                    for run in runs)):
        # Already at the ceiling it still sets, judged at these lengths by
        # the rule now asked: a resume with nothing run adds no round, the
        # same one again. A rule changed since is judged.
        record["outcome"] = last["decision"]
        record["said"] = str(last.get("said") or "")
        say("Nothing run: the runs were judged at these lengths already. " + record["said"])
        # Said to its caller, never written.
        return {**record, "nothing_run": True}
    for _ in range(MOST_ROUNDS):
        production = min(production_of(run) for run in runs)
        verdicts = judge(runs, targets, production)
        entry: dict[str, Any] = {"production_ns": round(production, 9),
                                 "verdicts": [v.as_record() for v in verdicts]}
        record["rounds"].append(entry)
        unrecorded = [v for v in verdicts if v.unrecorded]
        if unrecorded:
            entry["decision"] = "stopped"
            entry["stopped_by"] = "no mean"
            record["outcome"] = "stopped"
            record["said"] = entry["said"] = (
                "Stopped: " + "; ".join(v.said for v in unrecorded)
                + ". More production does not give an analysis a mean it does not "
                "record; its own log says why.")
            write()
            say(record["said"])
            return record
        if all(v.met for v in verdicts):
            entry["decision"] = "met"
            record["outcome"] = "met"
            record["said"] = entry["said"] = _said_met(verdicts, production, len(runs),
                                                       independent, axes=_swept(record_in))
            write()
            say(record["said"])
            return record
        if production >= ceiling - 1e-9:
            entry["decision"] = "ceiling"
            record["outcome"] = "ceiling"
            record["said"] = entry["said"] = (
                f"Stopped at the {length_said(ceiling)} ceiling, with what was asked not "
                "determined to the precision asked: "
                + "; ".join(v.said for v in verdicts if not v.met) + ".")
            write()
            say(record["said"])
            return record
        more = next_piece(verdicts, production, ceiling, _frame_ns(runs[0]))
        if more <= 0:
            entry["decision"] = "ceiling"
            record["outcome"] = "ceiling"
            record["said"] = entry["said"] = (
                f"Stopped: the {length_said(ceiling)} ceiling leaves no whole frame to add, "
                "with what was asked not determined to the precision asked: "
                + "; ".join(v.said for v in verdicts if not v.met) + ".")
            write()
            say(record["said"])
            return record
        entry["decision"] = "extend"
        entry["more_ns"] = more
        entry["started"] = []
        current["entry"] = entry
        record["said"] = entry["said"] = (
            f"Extending by {length_said(more)}, to {length_said(production + more)} of the "
            f"{length_said(ceiling)} allowed: "
            + "; ".join(v.said for v in verdicts if not v.met) + ".")
        write()
        say(f"Not yet determined: {'; '.join(v.said for v in verdicts if not v.met)}. "
            f"Extending {'every run' if len(runs) > 1 else 'the run'} by {length_said(more)} "
            f"(to {length_said(production + more)} of the {length_said(ceiling)} allowed).")
        stopped = ended_by(entry, runs, [] if asked() else list(extend_all(runs, more)))
        if stopped is not None:
            return stopped
    record["outcome"] = "rounds"
    record["said"] = record["rounds"][-1]["said"] = (
        f"Stopped after {MOST_ROUNDS} rounds with what was asked not determined to the "
        "precision asked.")
    write()
    say(record["said"])
    return record


def _swept(study: Path) -> list[str] | None:
    """The axes the study's runs were swept over, from its batch manifest;
    None where it was not read."""
    try:
        sweep = json.loads((study / "batch_manifest.json").read_text(encoding="utf-8"))["sweep"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return [str(axis) for axis in sweep] if isinstance(sweep, dict) else None


def _said_met(verdicts: list[Verdict], production: float, runs: int, independent: str,
              *, axes: list[str] | None = None) -> str:
    """What a study determined as asked says, its replicas said as they
    differ (`batch.aggregate.how_replicas_differ`): from one structure by
    seed, or from different models of an ensemble; neither where the axes
    were not read."""
    from fastmdxplora.batch.aggregate import how_replicas_differ
    from fastmdxplora.simulation.sampling_ask import length_said

    known = "; ".join(v.said for v in verdicts)
    each = " in each run" if runs > 1 else ""
    text = f"After {length_said(production)} of production{each}: {known}."
    if runs == 1:
        text += (" One run: precise within itself, and not checked against independent "
                 "starts, so a run trapped in one state, or still relaxing more slowly "
                 "than it ran, would look the same (independent_starts: not_required).")
    elif axes is not None and "setup.model" in axes:
        text += (f" The {runs} replicas {how_replicas_differ(axes)}, so they check trapping "
                 "as far as those structures and their dynamics carried them apart; a "
                 "relaxation slower than the runs that every model shares they cannot check.")
    elif axes:
        text += (f" The {runs} replicas started from one structure, "
                 f"{how_replicas_differ(axes, as_participle=True)}, so they check trapping "
                 "as far as their dynamics carried them apart; a relaxation slower than the "
                 "runs, shared from that structure, they cannot check.")
    else:
        text += (f" The {runs} replicas check trapping as far as they were carried apart; a "
                 "relaxation slower than the runs that they all share they cannot check.")
    return text


# ---------------------------------------------------------------------------
# The record, written up
# ---------------------------------------------------------------------------

#: How `run_until_known` begins the record's sentence for a round stopped
#: while it extended the runs: asked to stop (Ctrl+C, or a signal such as
#: a scheduler's or the GUI's Stop), or an extension that failed. Read for
#: a record written before a round recorded what stopped it
#: (``stopped_by``) and its own sentence (``said``); the record's sentence
#: is the last round's only, since an earlier round's, in a study resumed
#: since, was written over.
_STOPPED_WHEN_ASKED = "Stopped when asked"
_EXTENDING_FAILED = "Stopped: extending "

#: What `batch.explorer` writes for a study stopped when asked before its
#: rule was applied: the study's sentence, never a round's. A resumed study
#: keeps its rounds under it.
STOPPED_BEFORE_THE_RULE = ("Stopped when asked, before the rule was applied. Resuming the "
                           "study applies it.")
#: The same, where the record keeps the rounds judged before it was resumed.
STOPPED_BEFORE_THE_RULE_AGAIN = ("Stopped when asked, before the rule was applied again. "
                                 "Resuming the study applies it.")


def study_ended(record: dict[str, Any]) -> str:
    """How the study ended where its record's sentence is the study's own,
    written by `batch.explorer` with no round judged then: "asked" (stopped
    when asked before the rule was applied) or "not applied" (too few runs
    finished); "" where the sentence is a round's. A study with rounds that
    ended so was resumed after them."""
    outcome = str(record.get("outcome") or "")
    sentence = str(record.get("said") or "")
    if outcome == "not_applied":
        return "not applied"
    if outcome == "stopped" and sentence.startswith(STOPPED_BEFORE_THE_RULE.split(".")[0]):
        return "asked"
    return ""


def study_said(record: dict[str, Any]) -> str:
    """The record's sentence for the study, as the card and the Report say
    it (`record_said`): a study stopped when asked before its rule was
    applied, whose record keeps rounds judged before it was resumed, is
    said stopped before the rule was applied again, as the Timeline and the
    methods say it, though a record written before says it without
    "again"."""
    rounds = record.get("rounds")
    if study_ended(record) == "asked" and isinstance(rounds, list) and rounds:
        return STOPPED_BEFORE_THE_RULE_AGAIN
    return record_said(str(record.get("said") or ""))


#: Two runs within this of each other are at one length (a femtosecond).
_ONE_LENGTH_NS = 1e-6


def round_length(rounds: list[dict[str, Any]]) -> float | None:
    """The length the last round was extending the runs to: its production
    and what it added. None where it extended nothing (it decided, or a
    mean was not recorded) or the record does not say."""
    last = rounds[-1] if rounds and isinstance(rounds[-1], dict) else {}
    if str(last.get("decision") or "") not in ("extend", "stopped"):
        return None
    production, more = last.get("production_ns"), last.get("more_ns")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (production, more)):
        return None
    return round(float(production) + float(more), 9)


def brought_up(rounds: list[dict[str, Any]], productions: dict[str, float | None],
               ceiling_ns: float | None) -> tuple[float | None, list[str]]:
    """What a resumed study runs before it judges its runs again, read by
    `run_until_known` and said by `round_ended`: where its runs are not at
    one length (a round cut while it extended them), the length to bring
    them to, the round's, never past the ceiling, and the runs below it.
    Judged at the shortest and extended from there, the runs the round had
    extended went past the ceiling (a run at 8 ps extended with one at 2 ps
    ended at 36 ps of 30 ps). ``(None, [])`` where they are at one length,
    and where a run is past the ceiling (`_past_the_ceiling`)."""
    known = {name: ns for name, ns in productions.items() if ns is not None}
    if (len(known) < 2 or max(known.values()) - min(known.values()) <= _ONE_LENGTH_NS
            or _past_the_ceiling(productions, ceiling_ns)):
        return None, []
    top = max(known.values())
    target = round_length(rounds)
    if target is None or target < top - _ONE_LENGTH_NS:
        target = top
    if isinstance(ceiling_ns, (int, float)) and not isinstance(ceiling_ns, bool):
        target = min(target, float(ceiling_ns))
    return target, [name for name, ns in known.items() if ns < target - _ONE_LENGTH_NS]


@dataclass(frozen=True)
class RoundEnded:
    """A study whose stopping rule's round ended before it was judged
    again: an extension that failed (an error, a worker killed), one
    stopped when asked, or one whose process ended with the round going.
    Its runs each ended well, so read from them alone the study read
    completed while its record said the round failed."""

    #: "failed" (an extension's error), "stopped" (asked to stop) or
    #: "interrupted" (its process ended while it extended the runs).
    state: str
    #: What ended it, as the record says it, and how far each run got.
    said: str
    #: What `fastmdx resume` would run for it.
    carry_on: str
    #: The command that carries it on.
    argv: tuple[str, ...]
    #: Each run's production now, in the record's order (None: not read).
    productions: tuple[tuple[str, float | None], ...] = ()

    def as_record(self) -> dict[str, Any]:
        """As `batch_manifest.json` and an answer record it: what carries
        it on is read when asked (`round_ended`), not saved, since it rests
        on the study's config and runs as they stand then."""
        return {"state": self.state, "said": self.said, "command": " ".join(self.argv)}

    def as_text(self) -> str:
        return f"{self.said} {self.carry_on}"

    @property
    def carry_on_on_a_page(self) -> str:
        """What carries it on, read on a page as plain text: the code marks
        the CLI and the report give the command are left out."""
        return self.carry_on.replace("`", "")

    @property
    def label(self) -> str:
        """The word a page's chip says for it."""
        return {"failed": "Failed", "stopped": "Stopped"}.get(self.state, "Interrupted")


def _production_of_each(study: Path, names: list[str]) -> dict[str, float | None]:
    """Each run's production as its checkpoints say it (`production_done_ns`):
    a run of several in ``runs/<name>``, a study of one run in its folder."""
    from fastmdxplora.simulation.resume import production_done_ns

    from fastmdxplora import record_access

    found: dict[str, float | None] = {}
    for name in names:
        folder = study / "runs" / name
        try:
            if not record_access.is_dir(folder):
                folder = study
            found[name] = production_done_ns(folder)
        except Exception:  # noqa: BLE001 - not read, and said so by its absence
            found[name] = None
    return found


def _its_process_is_gone(study: Path) -> bool:
    """Whether the process that ran the study's rounds has ended, by the
    record it keeps while it runs: no process has its number here, or the
    record was written on another machine, in another container or before
    this machine's last boot. Never where it names this process or one above it (the study judging its own rounds);
    nor where a process other than its run has its number now, which the
    card says as not finished when written. Gone where a study has no
    record: every process that runs its rounds holds it while it runs (a
    stop between a piece and its join took it away and left the round
    going).
    Asked after `_carried_on_elsewhere`, so no resume holds the study."""
    from fastmdxplora.gui.exploration import _process_alive
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, record_is_from_elsewhere
    from fastmdxplora.simulation.resume import (_from_another_host,
                                                _this_process_and_its_parents)

    from fastmdxplora import record_access

    try:
        process = json.loads(record_access.read_text(study / RUN_PROCESS_FILE, encoding="utf-8"))
    except FileNotFoundError:
        # A study (its resolved_config.yml, which every run writes as it
        # starts) whose rounds ran: a record only, from no run, says nothing.
        try:
            return record_access.is_file(study / "resolved_config.yml")
        except OSError:
            return False
    except (OSError, ValueError):
        return False
    pid = process.get("pid") if isinstance(process, dict) else None
    if not isinstance(pid, int) or pid <= 0:
        return False
    if record_is_from_elsewhere(process) or _from_another_host(process):
        # This machine before its last boot: every process of that boot is
        # gone. Another machine or container: asked after
        # `_carried_on_elsewhere`, which reads it going while its records
        # are written (`resume.going_elsewhere`); quiet for a day, it is
        # read as its records say it ended, its sentence saying where
        # (`_where_its_process_was`).
        return True
    if pid in _this_process_and_its_parents():
        return False
    try:
        return not _process_alive(pid)
    except Exception:  # noqa: BLE001 - not known gone
        return False


def _where_its_process_was(study: Path) -> str:
    """Where the study's process record was written, where that is another
    machine or container (`resume.another_host_said`), said after its
    round's sentence once its records have gone quiet: not known to have
    ended there, so resumed only once it has. Empty otherwise."""
    from fastmdxplora.simulation.resume import another_host_said

    try:
        where = another_host_said(study)
    except Exception:  # noqa: BLE001 - said without where
        return ""
    return (f" Its process record was written {where}, which cannot be asked from here, and "
            "nothing has been written for an hour: resume it only once it has ended there."
            if where else "")


def _carried_on_elsewhere(study: Path) -> bool:
    """Whether another process carries the study on now (`fastmdx resume`
    or an extension holds its lock, or its run record names a live
    process): it is going, whatever its record of rounds last said. Not
    this process's own hold: a resume reads how its own round ended."""
    from fastmdxplora.simulation import resume

    try:
        root = study.expanduser().resolve()
        with resume._CARRYING:
            if resume._carried_here.get(root):
                return False
        return resume.being_carried_on(root)
    except Exception:  # noqa: BLE001 - not known going
        return False


def _ceiling_a_resume_uses(study: Path, record: dict[str, Any]) -> float | None:
    """The ceiling `fastmdx resume` applies: the study's config's, which
    it reads (lowered or raised since the round), else the record's. A
    run's page shows its study's card, read in the study's held read
    (`server.stopping_card`), so it reads the same config."""
    import yaml

    from fastmdxplora import record_access

    try:
        # A config linked out of the study is not read, and named so where
        # the read is held (`record_access`): the record's ceiling is said.
        config = yaml.safe_load(record_access.read_text(study / "resolved_config.yml",
                                                        encoding="utf-8"))
        value = config["simulation"]["stop_when"]["max_duration_ns"]
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        value = record.get("max_duration_ns")
    return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)) else None)


def _what_resume_finds(study: Path, names: list[str]) -> dict[str, dict[str, Any] | None]:
    """What `fastmdx resume` finds in each run (`resume.how_a_resume_finds`,
    which its resume reads too): a last piece or the run itself stopped
    with an answer, given again; an extension cut or analyses owed,
    carried on first. Read through `record_access`, held to the study."""
    from fastmdxplora.simulation.resume import how_a_resume_finds

    from fastmdxplora import record_access

    found: dict[str, dict[str, Any] | None] = {}
    for name in names:
        folder = study / "runs" / name
        try:
            if not record_access.is_dir(folder):
                folder = study
            found[name] = how_a_resume_finds(folder)
        except Exception:  # noqa: BLE001 - not known
            found[name] = None
    return found


#: In the record of a study whose resume carries it on, from before its
#: work until it ends (`carried_on_marked`): a resume that died leaves it,
#: so the study reads cut, never stopped when asked by an earlier stop's
#: sentence; written over by the rule's next round.
CARRIED_ON_BY = "carried_on_by"


@contextlib.contextmanager
def carried_on_marked(study: str | Path) -> Iterator[None]:
    """The study's record of its rounds says a resume of this process
    carries it on, while it does: written before its work, taken away
    after however it ended. Nothing where the study keeps no such record."""
    where = Path(study) / RECORD

    def rewrite(change: Any) -> None:
        try:
            record = json.loads(where.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(record, dict) and change(record):
            try:
                where.write_text(json.dumps(record, indent=2), encoding="utf-8")
            except OSError:
                pass

    def mark(record: dict[str, Any]) -> bool:
        record[CARRIED_ON_BY] = _this_resume()
        return True

    def unmark(record: dict[str, Any]) -> bool:
        if _is_this_resume(record.get(CARRIED_ON_BY)):
            record.pop(CARRIED_ON_BY)
            return True
        return False

    rewrite(mark)
    try:
        yield
    finally:
        rewrite(unmark)


def _this_resume() -> dict[str, Any]:
    """This process, told apart as the study's lock tells it: its number,
    when it began, and the machine, boot and process namespace that gave
    the number out."""
    import os

    from fastmdxplora.orchestrator import process_start_ticks, this_machine

    return {"pid": os.getpid(), "started": process_start_ticks(os.getpid()), **this_machine()}


def _is_this_resume(marked: Any) -> bool:
    """Whether a marker names this very process, never another given its
    number since (a reboot, a copy, another machine)."""
    return isinstance(marked, dict) and all(marked.get(key) == value
                                            for key, value in _this_resume().items())


def _a_resume_died(record: dict[str, Any]) -> bool:
    """Whether the record says a resume carried the study on, and that
    resume has ended without taking its marker away (killed, out of
    memory). Asked once nothing carries the study on
    (`_carried_on_elsewhere`: a live resume holds the study's lock), so a
    marker left is a resume that died, whatever process has its number
    now; this process's own marker excepted. The one reading of the marker,
    for the study and its runs' pages alike."""
    marked = record.get(CARRIED_ON_BY)
    return bool(marked) and not _is_this_resume(marked)


def _failed_said(record: dict[str, Any], number: int) -> str:
    """The sentence of a round that failed (an extension's error)."""
    body = study_said(record).removeprefix("Stopped: ").rstrip(".") or "no reason recorded"
    return f"Its stopping rule's round {number} failed: {body}."


def _its_record_held(study: Path) -> Any:
    """The study's record of its rounds, read where the read is held, or
    in a hold of its own over that record only (`resume._held_alone`): a
    run's page held to the run (Problems) has nothing refused for it. None
    where it cannot be read."""
    from fastmdxplora.simulation.resume import _held_alone

    try:
        return json.loads(_held_alone(study / RECORD))
    except (OSError, ValueError):
        return None


def round_failed(study: str | Path) -> str | None:
    """The sentence of the study's last round where it failed (an
    extension's error: its join, its piece), by its record of its rounds
    alone, as `round_ended` says it; None otherwise. Read held to the
    study, its record only, so a run's page reads it as its study's page
    does: a piece left unjoined by a join that failed is not a process
    that ended."""
    record = _its_record_held(Path(study))
    if not isinstance(record, dict) or str(record.get("outcome") or "") != "stopped":
        return None
    rounds = [entry for entry in record.get("rounds") or [] if isinstance(entry, dict)]
    if not rounds or study_ended(record) or _a_resume_died(record):
        return None
    if round_decided(rounds[-1], last=True, record=record)["stopped_by"] != "error":
        return None
    return _failed_said(record, rounds_judged(rounds))


def round_stopped_when_asked(study: str | Path) -> bool:
    """Whether the study's last round was stopped when asked, by its record
    of its rounds alone (never by when its files were written, which a copy
    or a touch changes): its last round's ``stopped_by``, and no resume
    since that died carrying it on (`CARRIED_ON_BY`). Read held to the
    study, its record only (a run's page reads no other record of it)."""
    record = _its_record_held(Path(study))
    if not isinstance(record, dict) or _a_resume_died(record):
        return False
    rounds = [entry for entry in record.get("rounds") or [] if isinstance(entry, dict)]
    if str(record.get("outcome") or "") != "stopped":
        return False
    return (study_ended(record) == "asked"
            or bool(rounds) and rounds[-1].get("stopped_by") == "asked")


def rule_owed(study: str | Path, *, resuming: bool = False) -> RoundEnded | None:
    """A study whose stopping rule was never applied to how its runs now
    stand, though nothing goes on: its process ended (killed, out of
    memory) before the rule's first round was judged (``running``, no
    round), or it was stopped when asked before the rule was applied (again).
    Its runs can each read completed, and it read completed with what it
    asked never judged and no fix offered. Said stopped (asked) or
    interrupted (ended), with `fastmdx resume`, which applies the rule, and
    given by `round_ended`, so every reader of the study's state says it.
    Only where each run ended well by its own records (a run stopped or cut
    says so itself, in its own words). None for a study with no rule, going,
    or judged since."""
    from fastmdxplora import record_access

    study = Path(study)
    try:
        record = json.loads(record_access.read_text(study / RECORD, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not record.get("targets"):
        return None
    outcome = str(record.get("outcome") or "")
    rounds = [entry for entry in record.get("rounds") or [] if isinstance(entry, dict)]
    said = record.get("said")
    died = not resuming and _a_resume_died(record)
    if outcome == "stopped" and said in (STOPPED_BEFORE_THE_RULE, STOPPED_BEFORE_THE_RULE_AGAIN):
        # Stopped when asked, unless a resume carried it on since and died.
        state = "interrupted" if died else "stopped"
    elif outcome == "running" and not rounds:
        state = "interrupted"
    else:
        return None
    # Its runs first: one that did not end well says so itself, and the
    # study's own records (its process) are read no further for it.
    names = [str(name) for name in record.get("runs") or []]
    if not names or not _each_ended_well(study, names):
        return None
    if not resuming and _carried_on_elsewhere(study):
        return None
    if state == "interrupted" and not resuming and not _its_process_is_gone(study):
        return None
    argv = ("fastmdx", "resume", str(study))
    command = f"`{' '.join(argv)}`"
    again = " again" if rounds else ""
    said = ("Its stopping rule was not applied" + again + ": "
            + ("it was stopped when asked before the rule was applied" + again + "."
               if state == "stopped" else
               "the resume carrying it on ended before the rule was applied" + again + "."
               if died else
               "the study's process ended before its first round was judged."
               + _where_its_process_was(study)))
    return RoundEnded(state=state, said=said,
                      carry_on=f"{command} judges the runs as they stand and extends them "
                               "until what was asked is determined.",
                      argv=argv,
                      productions=tuple(_production_of_each(study, names).items()))


def _each_ended_well(study: Path, names: list[str]) -> bool:
    """Whether each run of the study ended well by its own records, as its
    resume reads it (`resume._where_it_stands`): finished, with no answer
    of its own, nothing extended since nor analyses owed, no piece left."""
    import yaml

    from fastmdxplora import record_access
    from fastmdxplora.simulation.resume import _where_it_stands

    for name in names:
        run = study / "runs" / name
        if not run.is_dir():
            run = study
        try:
            # A run that never wrote its Manifest did not end well, and is
            # read no further (its other records are its going's).
            if not record_access.is_file(run / "manifest.json"):
                return False
            finished, stopped, extended, piece = _where_it_stands(run, lengths=False)
        except (OSError, ValueError, StudyError, yaml.YAMLError):
            return False
        if not finished or stopped is not None or extended or piece is not None:
            return False
    return True


def round_numbers(rounds: list[Any]) -> list[int]:
    """Each entry's round number, as the rule judged its rounds: an entry
    that brings shorter runs up (`brought_up`) carries the round before it
    on and is shown as part of it, never as a round of its own. One
    numbering for every reader: the sentence (`round_ended`), the Overview's
    card and its count, the Timeline, the methods and the Agent's context."""
    numbers: list[int] = []
    number = 0
    for entry in rounds or []:
        if number == 0 or not (isinstance(entry, dict) and entry.get("brought_up")):
            number += 1
        numbers.append(number)
    return numbers


def rounds_judged(rounds: list[Any]) -> int:
    """How many rounds the rule judged (`round_numbers`)."""
    numbers = round_numbers(rounds)
    return numbers[-1] if numbers else 0


def round_ended(study: str | Path, *, resuming: bool = False) -> RoundEnded | None:
    """The one derivation of a study whose rule's last round failed, was
    stopped, or was cut by its process ending while it extended the runs,
    read by the CLI's last line and exit code, `batch_manifest.json`, every
    GUI page's state, the Overview's card, the Agent and the report, and by
    `fastmdx resume`, which runs what it says. None for a study with no
    rule, one still running or being carried on, and one whose rounds ended
    as the rule decides (determined, the ceiling, the rounds allowed, a mean
    not recorded). ``resuming``: asked by the resume that carries it on,
    which has found no process running it, so a round its record says is
    going was cut."""
    from fastmdxplora.simulation.sampling_ask import length_said

    from fastmdxplora import record_access

    study = Path(study)
    try:
        # Held to its study where the read is (`record_access`), as the
        # pages that read it are.
        record = json.loads(record_access.read_text(study / RECORD, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    outcome = str(record.get("outcome") or "")
    rounds = [entry for entry in record.get("rounds") or [] if isinstance(entry, dict)]
    if ((outcome == "running" and not rounds)
            or (outcome == "stopped" and record.get("said") in (
                STOPPED_BEFORE_THE_RULE, STOPPED_BEFORE_THE_RULE_AGAIN))):
        # No round judged since its runs ended well: its rule never applied
        # to them (`rule_owed`), said here so every reader says it.
        return rule_owed(study, resuming=resuming)
    if not rounds or outcome not in ("stopped", "running"):
        return None
    if not resuming and _carried_on_elsewhere(study):
        # A resume carries it on: going, as the join is while one runs it.
        return None
    # Rounds the rule judged: one that brings shorter runs up carries the
    # round before it on, and is not a round of its own in the count.
    number = rounds_judged(rounds)
    names = [str(name) for name in record.get("runs") or []]
    them = "the runs" if len(names) > 1 else "the run"
    more = rounds[-1].get("more_ns")
    more = (float(more) if isinstance(more, (int, float)) and not isinstance(more, bool)
            else None)
    if outcome == "running":
        # Its record says a round goes on, and the process that ran it is
        # gone (killed hard, out of memory): read completed from its runs.
        if (str(rounds[-1].get("decision") or "") != "extend"
                or not (resuming or _its_process_is_gone(study))):
            return None
        state = "interrupted"
        brought = rounds[-1].get("brought_up")
        length = round_length(rounds)
        if isinstance(brought, list) and brought and length is not None:
            # A catch-up round extends only the shorter runs, to a length.
            what = f"{_and([str(n) for n in brought])} to {length_said(length)}"
        else:
            what = them + (f" by {length_said(more)}" if more is not None else "")
        said = (f"Its stopping rule's round {number} did not finish: the study's process "
                f"ended while it extended {what}, with no record of how the round ended."
                + _where_its_process_was(study))
    else:
        if study_ended(record):
            return None
        decided = round_decided(rounds[-1], last=True, record=record)
        if decided["stopped_by"] not in ("error", "asked"):
            return None
        state = "failed" if decided["stopped_by"] == "error" else "stopped"
        sentence = study_said(record)
        if state == "failed":
            said = _failed_said(record, number)
        else:
            # Its own "Resuming the study ..." is said below, with the command.
            first = sentence.split(". ")[0].rstrip(".")
            said = (f"Its stopping rule's round {number} was stopped when asked"
                    f"{first.removeprefix(_STOPPED_WHEN_ASKED)}."
                    if first.startswith(_STOPPED_WHEN_ASKED)
                    else f"Its stopping rule's round {number} was stopped: {first}.")
        if not resuming and _a_resume_died(record):
            # A resume carried the round on and its process ended (killed,
            # out of memory) with no record of how: cut, never said stopped
            # when asked, nor failed, by the earlier round's sentence.
            state = "interrupted"
            said = (f"Its stopping rule's round {number} did not finish: the resume carrying "
                    "it on ended with no record of how the round ended.")
    productions = _production_of_each(study, names) if names else {}
    ceiling = _ceiling_a_resume_uses(study, record)
    length = round_length(rounds)
    said += _how_far_each_got(names, rounds[-1], productions, length)
    argv = ("fastmdx", "resume", str(study))
    command = f"`{' '.join(argv)}`"
    # What the resume does, from what it finds (`resume.how_a_resume_finds`)
    # and the lengths it reads, in the order it acts on them.
    found = _what_resume_finds(study, names) if names else {}
    over, would = _held_below_the_ceiling(names, productions, found, length, ceiling)
    answered = [(name, f) for name in names
                if (f := found.get(name)) and (f["piece"] is not None or f["own"])]
    if (over or would) and ceiling is not None:
        # The resume carries no run on (`runs_kept_below_the_ceiling`).
        one = len(names) == 1
        parts = []
        if over:
            parts.append(f"{_and(over)} {'is' if len(over) == 1 else 'are'} past the "
                         f"{length_said(ceiling)} ceiling the study now sets")
        if would and length is not None:
            parts.append(("its extension" if one else
                          f"the extension of {_and(would)}")
                         + f" was to run to {length_said(length)}"
                         + (", past it" if over else
                            f", past the {length_said(ceiling)} ceiling the study now sets"))
        needed = max([productions[n] or 0.0 for n in over]
                     + ([length] if would and length is not None else []))
        carry_on = (f"{command} carries no run on: " + "; ".join(parts)
                    + f". With `max_duration_ns` at least {length_said(needed)} in its "
                    f"config, {command} carries "
                    + ("the run on and judges it." if one
                       else "the runs on and judges them at one length."))
    elif answered:
        name, f = answered[0]
        several = len(names) > 1
        if f["piece"] is not None:
            piece = f["piece"]
            whose = (f"{name}'s last piece, {piece.name}," if several
                     else f"the run's last piece, {piece.name},")
            carry_on = (f"{command} does not carry the round on: {whose} stopped with an "
                        "answer of its own, and running it again would give the same one. "
                        f"To run it again anyway, move {piece} out of "
                        + ("its run's" if several else "the study's")
                        + f" folder, then {command} carries the round on.")
        else:
            phase = str(f["own"]).split(":")[0].strip() or "a phase"
            run = study / "runs" / name if several else study
            carry_on = (f"{command} does not carry the round on: "
                        + (name if several else "the run")
                        + f" stopped with an error of its own in its {phase}. Once what it "
                        "says is put right, "
                        f"`fastmdx explore --output {run} --include analysis report --rerun` "
                        f"runs its analyses and report again, then {command} carries the "
                        "round on.")
    else:
        # Each run's extension cut is carried on to the round's length, and
        # its pieces' analyses owed are run, before the runs are compared.
        carried = [n for n in names if (found.get(n) or {}).get("carried_on_first")]
        cut = [n for n in carried if length is not None and productions.get(n) is not None
               and productions[n] < length - _ONE_LENGTH_NS]
        # Pieces at the round's length the join has not taken in (stopped at
        # their last step), and joined pieces whose analyses are owed.
        unjoined = [n for n in carried if n not in cut and (found.get(n) or {}).get("unjoined")]
        owed = [n for n in carried if n not in cut and n not in unjoined]
        projected = {n: (length if n in cut else ns) for n, ns in productions.items()}
        target, short = brought_up(rounds, projected, ceiling)
        steps = []
        if cut:
            steps.append("carries "
                         + ("the run's extension" if len(names) == 1
                            else f"the extension{'s' if len(cut) > 1 else ''} of {_and(cut)}")
                         + f" on to {length_said(length)}")
        if owed:
            steps.append("runs the analyses and report of "
                         + ("the run's" if len(names) == 1 else f"{_and(owed)}'s")
                         + " joined pieces")
        if unjoined:
            steps.append("joins "
                         + ("the run's" if len(names) == 1 else f"{_and(unjoined)}'s")
                         + " pieces and runs their analyses and report")
        if short and target is not None:
            longer = [name for name in names if name not in short]
            steps.append(f"extends {_and(short)} to {length_said(target)}"
                         + (f", the length of {_and(longer)}" if longer else ""))
        units = {v.get("analysis"): v.get("unit") or ""
                 for entry in rounds for v in entry.get("verdicts") or [] if isinstance(v, dict)}
        try:
            targets = [StopTarget(**t) for t in record.get("targets") or []]
        except TypeError:
            targets = []
        asked = "; ".join(t.said(units.get(t.analysis, "")) for t in targets)
        each = " in each run" if len(names) > 1 else ""
        first = f"first {', then '.join(steps)}, then " if steps else ""
        carry_on = (f"{command} {first}judges "
                    + (f"its {len(names)} runs" if len(names) > 1 else "the run")
                    + " again and extends "
                    + ("them" if len(names) > 1 else "it")
                    + " until what it asks is determined"
                    + (f" ({asked})" if asked else "")
                    + (f", with at most {length_said(ceiling)} of production{each}"
                       if ceiling is not None else "")
                    + ".")
    return RoundEnded(state=state, said=said, carry_on=carry_on, argv=argv,
                      productions=tuple((name, productions.get(name)) for name in names))


def _how_far_each_got(names: list[str], last: dict[str, Any],
                      productions: dict[str, float | None], length: float | None) -> str:
    """Which runs the round extended and how far the others got, from each
    run's production and the record's runs whose extension finished: a run
    at the round's length got there whatever its worker's answer (a pool
    broken by another run's worker gives every run its error), and a run
    whose extension did not finish may have run part of it, so it is said
    by its production, never said not extended. A round that brought the
    shorter runs up (`brought_up`) says the others were at its length."""
    from fastmdxplora.simulation.sampling_ask import length_said

    known = {name: ns for name, ns in productions.items() if ns is not None}
    differ = (len(known) > 1
              and max(known.values()) - min(known.values()) > _ONE_LENGTH_NS)
    if len(names) < 2:
        ns = known.get(names[0]) if names else None
        if length is not None and ns is not None and ns < length - _ONE_LENGTH_NS:
            return (f" Its extension did not finish: the run has {length_said(ns)} of "
                    "production.")
        return ""
    extended = last.get("extended")
    brought = last.get("brought_up")
    brought = ([str(name) for name in brought] if isinstance(brought, list) else None)
    done = [name for name in names
            if (isinstance(extended, list) and name in extended)
            or (length is not None and known.get(name) is not None
                and known[name] >= length - _ONE_LENGTH_NS)]
    left = [name for name in names if name not in done]
    if not left:
        return ""
    began = last.get("started")
    if isinstance(began, list) and not began and not done and last.get("stopped_by") == "asked":
        # Stopped before any extension began: its sentence says so.
        return ""
    unbegun = ([name for name in left if name not in began]
               if isinstance(began, list) else [])
    left = [name for name in left if name not in unbegun]
    after = (f"; the extension of {_and(unbegun)} did not start" if unbegun else "")
    if not left:
        # Only runs whose extension never began are short.
        where = [known.get(name) for name in unbegun]
        at = (f" ({unbegun[0]} has {length_said(where[0])} of production)"
              if len(unbegun) == 1 and where[0] is not None else "")
        lead = (f" {_and(done)} {'was' if len(done) == 1 else 'were'} extended"
                + (f" to {length_said(length)}" if length is not None else "") + ";"
                if done else "")
        return (f"{lead} the extension of {_and(unbegun)} did not start{at}"
                if done else f" The extension of {_and(unbegun)} did not start{at}") + (
            ", so the runs are not at one length." if differ else ".")
    where = [known.get(name) for name in left]
    at = ""
    if all(ns is not None for ns in where):
        at = (f" ({left[0]} has {length_said(where[0])} of production)" if len(left) == 1
              else f" (they have {_and([length_said(ns) for ns in where])} of production)")
    tail = after + (", so the runs are not at one length." if differ else ".")
    if not done:
        return f" The extension of {_and(left)} did not finish{at}{tail}" if differ else ""
    to = f" to {length_said(length)}" if length is not None else ""
    there = [name for name in done if brought is not None and name not in brought]
    moved = [name for name in done if name not in there]
    parts = []
    if there:
        parts.append(f"{_and(there)} {'is' if len(there) == 1 else 'are'} at "
                     f"{length_said(length) if length is not None else 'that length'}")
    if moved:
        parts.append(f"{_and(moved)} {'was' if len(moved) == 1 else 'were'} extended{to}")
    return (f" {'; '.join(parts)}; the extension of {_and(left)} did not finish{at}{tail}")


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def record_said(text: str) -> str:
    """A sentence a record holds, with its lengths in ps under 1 ns
    (`length_said`): a record written before its sentences were said so
    gave "the 0.0012 ns ceiling", "Extending by 0.25 ns" and its ceiling
    with no unit, "of the 0.0012 allowed"."""
    import re

    from fastmdxplora.simulation.sampling_ask import length_said

    number = r"(\d+(?:\.\d+)?(?:[eE]-?\d+)?)"

    def said(found: "re.Match[str]") -> str:
        value = float(found.group(1))
        return length_said(value) if 0 < value < 1 else found.group(0)

    text = re.sub(rf"(?<![\w.]){number} ns\b", said, str(text or ""))
    return re.sub(rf"\bof the {number} allowed\b",
                  lambda found: f"of the {length_said(float(found.group(1)))} allowed", text)


def round_decided(entry: dict[str, Any], *, last: bool, record: dict[str, Any]) -> dict[str, Any]:
    """What was decided on one round of a study's record, and what stopped
    a stopped one, as every page and the report say it: ``said``, the words
    after "Round N: "; ``stopped_by``, "no mean", "asked" (Ctrl+C or a
    signal), "error", or "not recorded" where the record does not say;
    ``going``, a round whose extension the record does not say finished
    with the study still said running (still extending, or killed while it
    extended, which the record cannot tell apart); ``theirs``, whether
    ``sentence`` is this round's own, the record's words for it.

    From what the record holds: the round's own ``stopped_by`` and ``said``,
    written since, or, in an older record, its verdicts and the record's
    last sentence, where that is the last round's: never the study's own
    (`study_ended`), which a study stopped before its rule was applied
    again writes over it. The last round's extension, with the study since
    ended so, is not said finished either."""
    from fastmdxplora.simulation.sampling_ask import length_said

    decision = str(entry.get("decision") or "")
    outcome = str(record.get("outcome") or "")
    ended = study_ended(record)
    own = entry.get("said") if isinstance(entry.get("said"), str) else None
    # An older record's sentence is its last round's, unless the study's.
    sentence = (own if own is not None
                else str(record.get("said") or "") if last and not ended else "")
    names = record.get("runs") if isinstance(record.get("runs"), list) else []
    several = len(names) > 1
    verdicts = [v for v in entry.get("verdicts") or [] if isinstance(v, dict)]
    more = entry.get("more_ns")
    more = (float(more) if isinstance(more, (int, float)) and not isinstance(more, bool)
            and math.isfinite(more) else None)
    whom = "each run" if several else "the run"
    were = "the runs were" if several else "the run was"
    stopped_by, going, theirs = "", False, False
    # A resumed study's runs brought to one length before they are judged
    # (`brought_up`): only those runs, to the length of the others.
    shorter = [str(n) for n in entry.get("brought_up") or []] \
        if isinstance(entry.get("brought_up"), list) else []
    production = entry.get("production_ns")
    to = (length_said(round(float(production) + more, 9))
          if shorter and more is not None and isinstance(production, (int, float))
          and not isinstance(production, bool) else "")
    if shorter and to:
        whom, were = _and(shorter), f"{_and(shorter)} {'was' if len(shorter) == 1 else 'were'}"
    if decision == "extend" and shorter and to:
        if last and (outcome == "running" or ended):
            going, theirs = outcome == "running", outcome == "running"
            said = f"extending {whom} to {to}; the record does not say it finished"
        else:
            said = f"{whom} extended to {to}, the length of the other runs"
    elif decision == "extend" and more is not None:
        if last and (outcome == "running" or ended):
            going, theirs = outcome == "running", outcome == "running"
            said = (f"extending {whom} by {length_said(more)}; the record does not say "
                    "it finished")
        else:
            said = f"{whom} extended by {length_said(more)}"
            if last and outcome == "rounds":
                said += ", the last of the rounds allowed"
                theirs = True
    elif decision == "met":
        said = "determined as asked, so stopped"
    elif decision == "ceiling":
        said = "stopped at the ceiling, not determined as asked"
    elif decision == "stopped":
        unrecorded = [str(v.get("said")) for v in verdicts if v.get("unrecorded") and v.get("said")]
        mine = sentence if own is not None or outcome == "stopped" else ""
        stopped_by = str(entry.get("stopped_by") or "")
        if stopped_by not in ("no mean", "asked", "error"):
            stopped_by = ("no mean" if unrecorded
                          else "asked" if mine.startswith(_STOPPED_WHEN_ASKED)
                          else "error" if mine.startswith(_EXTENDING_FAILED)
                          else "not recorded")
        theirs = bool(mine) and (own is not None or stopped_by in ("asked", "error"))
        said = {
            "no mean": "stopped: " + ("; ".join(unrecorded) or "an analysis recorded no mean"),
            "asked": f"stopped when asked (Ctrl+C or a signal) while {were} extended",
            "error": f"stopped by an error while {were} extended",
        }.get(stopped_by, "stopped; what stopped it is not recorded")
    else:
        said = "judged; what was decided is not recorded"
    return {"said": said, "stopped_by": stopped_by, "going": going, "theirs": theirs,
            "sentence": record_said(sentence) if theirs else ""}


def stopping_section(root: str | Path, *, ended_said_above: bool = False) -> list[str]:
    """The report's account of a study run until it knew: the rule, each
    round's numbers and what was decided on them. Empty where the study
    ran to a fixed length. ``ended_said_above``: the report leads with how
    its last round ended (`round_ended`), so it is not said twice."""
    try:
        record = json.loads((Path(root) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(record, dict) or not record.get("rounds"):
        return []
    from fastmdxplora.simulation.sampling_ask import length_said

    targets = [StopTarget(**t) for t in record.get("targets") or []]
    runs = record.get("runs") or []
    ceiling = record.get("max_duration_ns")
    units = {v.get("analysis"): v.get("unit") or ""
             for entry in record["rounds"] for v in entry.get("verdicts") or []}
    lines = ["## How long it ran, and why", "",
             "This study did not run for a length fixed in advance. It stated what it "
             "was for, " + "; ".join(t.said(units.get(t.analysis, "")) for t in targets)
             + (f", with at most {length_said(ceiling)} of production" if ceiling else "")
             + (f" in each of {len(runs)} runs" if len(runs) > 1 else "")
             + ", and ran in pieces: after each, the analyses were read and the study "
             "was extended by what the numbers said was still needed.", ""]
    names = [t.analysis for t in targets]
    lines.append("| round | production | " + " | ".join(names) + " | decision |")
    lines.append("|---|---|" + "---|" * len(names) + "---|")
    for number, entry in enumerate(record["rounds"], start=1):
        verdicts = {v.get("analysis"): v for v in entry.get("verdicts") or []}
        cells = []
        for name in names:
            v = verdicts.get(name) or {}
            if entry.get("brought_up") and not verdicts:
                # Runs brought to one length before they were judged.
                cells.append("not judged")
                continue
            if v.get("value") is None or v.get("error") is None:
                cells.append("not yet determined")
                continue
            cell = f"{with_its_error(v['value'], v['error'])} (±{v['allowed']:.2g} asked)"
            if v.get("agree") is False:
                cell += ", replicas disagree"
            cells.append(cell)
        decided = round_decided(entry, last=number == len(record["rounds"]),
                                record=record)["said"]
        production = entry.get("production_ns", 0)
        production = (length_said(float(production))
                      if isinstance(production, (int, float)) and not isinstance(production, bool)
                      else "not recorded")
        lines.append(f"| {number} | {production} | " + " | ".join(cells) + f" | {decided} |")
    lines.append("")
    said = study_said(record)
    if said and record.get("outcome") == "running":
        # A record cannot tell a study still running from one killed: what
        # it last said it was doing, never that it is running.
        lines += [f"**{_OUTCOME['running']}.** The record does not say the last round "
                  f"finished; it last says: {said}", ""]
    elif (ended := round_ended(root)) is not None:
        # As every page says it (`round_ended`), with what carries it on:
        # once, at the top, in a report that leads with it.
        lines += [f"**{ended.label}.** "
                  + ("How, and what carries it on, is said at the top of this report."
                     if ended_said_above else ended.as_text()), ""]
    elif said:
        lines += [f"**{_OUTCOME.get(record.get('outcome'), 'Outcome')}.** {said}", ""]
    if record.get("outcome") == "met":
        lines += [
            "Stopping as soon as an error falls below a target favours a round whose error "
            "came out small by chance, so each error is widened by its own degrees of "
            "freedom"
            + (", the combined error is the larger of what the runs claim and what their "
               "spread shows, " if len(runs) > 1 else
               ", and one run is judged alone only on 50 independent samples, ")
            + "and calibrated on series with a known mean, the errors judged this way held "
            "the truth about as often as they state. A state none of the runs left is not "
            "in the error, and neither is a relaxation slower than the runs that they all "
            "began from: on series relaxing over 30 ns from 3 standard deviations away, "
            "studies stopped near 40-60 ns with the truth inside two of their errors in "
            "fewer than 3 in 100, alone or as replicas from one structure. Runs from "
            "different structures are what show it.", ""]
    return lines


#: Each outcome a record holds, as the Report and the stopping card say it.
#: "running" is what the record said when it was last written: a page that
#: knows the run is live (its process record) says it running.
_OUTCOME = {"met": "Determined as asked", "ceiling": "Not determined as asked",
            "stopped": "Stopped early", "rounds": "Not determined as asked",
            "not_applied": "Not applied", "running": "Not finished when this was written"}
