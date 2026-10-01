"""Does a study stopped by `simulation.stop_when` report an honest error?

A study run until its error falls below a target stops at the first look
where it does, and an error that happens to be read low at that look is the
one the study reports: stopping on the data biases the stated precision
(optional stopping). Whether that matters here, and how much, is a number,
and it can be had without a simulation engine: series with a known true
mean, made round by round, recorded as the analyses record them
(`statistics.mean_record`), and judged and extended by the rule's own loop
(`simulation.stopping.run_until_known`). What the loop reports is then set
against the truth.

The cases, the numbers of studies, the seeds and what is claimed from the
counts are fixed in `preregistration/stopping-calibration.md`, written
before any result was read.

    python -m fastmdxplora.validation.stopping_calibration --out stopping_calibration.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

#: Frames per nanosecond of the made series: one every 10 ps.
FRAMES_PER_NS = 100

#: The analysis the targets name. Any analysis that records one mean would
#: do; the rule reads only what is recorded.
ANALYSIS = "rmsd"


# ---------------------------------------------------------------------------
# What is simulated
# ---------------------------------------------------------------------------

def _ar1(noise: np.ndarray, phi: float, previous: float) -> np.ndarray:
    """``y[i] = phi * y[i - 1] + noise[i]``, continuing from ``previous``."""
    from scipy.signal import lfilter

    return lfilter([1.0], [1.0, -phi], noise, zi=[phi * previous])[0]


@dataclass(frozen=True)
class Correlated:
    """A stationary AR(1) series about a known mean, its noise correlated
    over (1 + phi) / (1 - phi) frames, optionally starting displaced and
    relaxing exponentially: an equilibration transient."""

    mean: float
    sigma: float
    phi: float
    offset: float = 0.0
    relax_frames: float = 0.0

    @property
    def truth(self) -> float:
        return self.mean

    def start(self, rng: np.random.Generator) -> dict[str, Any]:
        return {"y": float(rng.normal(0.0, self.sigma)), "t": 0}

    def more(self, rng: np.random.Generator, state: dict[str, Any], n: int) -> np.ndarray:
        noise = rng.normal(0.0, self.sigma * math.sqrt(1.0 - self.phi ** 2), n)
        out = _ar1(noise, self.phi, state["y"])
        state["y"] = float(out[-1])
        t = state["t"] + np.arange(n)
        state["t"] += n
        drift = (self.offset * np.exp(-t / self.relax_frames)
                 if self.offset and self.relax_frames > 0 else 0.0)
        return self.mean + drift + out


@dataclass(frozen=True)
class TwoStates:
    """A series that switches between two states, each held for a mean of
    `dwell_frames` frames, with AR(1) noise within a state. The two are
    equally populated, so the true mean is halfway between them. Started in
    the first state (`start="first"`: replicas from one structure) or in a
    state drawn at random (`"drawn"`: independent starts)."""

    means: tuple[float, float]
    dwell_frames: float
    sigma: float
    phi: float
    start_in: str = "first"

    @property
    def truth(self) -> float:
        return 0.5 * (self.means[0] + self.means[1])

    def start(self, rng: np.random.Generator) -> dict[str, Any]:
        state = 0 if self.start_in == "first" else int(rng.integers(2))
        return {"y": float(rng.normal(0.0, self.sigma)), "s": state}

    def more(self, rng: np.random.Generator, state: dict[str, Any], n: int) -> np.ndarray:
        noise = rng.normal(0.0, self.sigma * math.sqrt(1.0 - self.phi ** 2), n)
        switch = rng.random(n) < 1.0 / self.dwell_frames
        within = _ar1(noise, self.phi, state["y"])
        states = (state["s"] + np.cumsum(switch)) % 2
        state["y"], state["s"] = float(within[-1]), int(states[-1])
        return np.where(states == 0, self.means[0], self.means[1]) + within


@dataclass(frozen=True)
class Case:
    """One stopping rule on one kind of series."""

    name: str
    process: Any
    replicas: int
    standard_error: float
    first_ns: float
    ceiling_ns: float
    studies: int
    said: str

    @property
    def independent_starts(self) -> str:
        return "required" if self.replicas > 1 else "not_required"


_FAST = dict(mean=1.0, sigma=0.05, phi=0.95)
_SLOW = dict(mean=1.0, sigma=0.05, phi=0.995)

#: The cases, as registered. Changing one after a result is read is a new
#: registration, not a revision.
CASES: tuple[Case, ...] = (
    Case("fast_one_run", Correlated(**_FAST), 1, 0.01, 2.0, 100.0, 1000,
         "correlation over 39 frames (0.4 ns), one run"),
    Case("fast_three_replicas", Correlated(**_FAST), 3, 0.01, 2.0, 100.0, 1000,
         "correlation over 39 frames, three replicas"),
    Case("slow_one_run", Correlated(**_SLOW), 1, 0.01, 5.0, 400.0, 500,
         "correlation over 399 frames (4 ns), one run"),
    Case("slow_three_replicas", Correlated(**_SLOW), 3, 0.01, 5.0, 200.0, 500,
         "correlation over 399 frames, three replicas"),
    Case("transient_three_replicas",
         Correlated(**_FAST, offset=0.15, relax_frames=300.0), 3, 0.01, 2.0, 100.0, 1000,
         "the fast case starting 3 sigma away, relaxing over 3 ns, three replicas"),
    Case("two_states_one_start",
         TwoStates((1.0, 1.2), 2000.0, 0.03, 0.95, "first"), 3, 0.01, 5.0, 200.0, 500,
         "two states 0.2 apart held 20 ns each, three replicas all started in the first"),
    Case("two_states_drawn_starts",
         TwoStates((1.0, 1.2), 2000.0, 0.03, 0.95, "drawn"), 3, 0.01, 5.0, 200.0, 500,
         "the same two states, each replica started in a state drawn at random"),
)


# ---------------------------------------------------------------------------
# One study
# ---------------------------------------------------------------------------

@dataclass
class Study:
    case: str
    index: int
    outcome: str
    value: float | None
    error: float | None
    production_ns: float
    rounds: int
    truth: float

    @property
    def z(self) -> float | None:
        if self.value is None or not self.error:
            return None
        return (self.value - self.truth) / self.error


def _record(run: Path, series: np.ndarray) -> None:
    from fastmdxplora.statistics import mean_record

    where = run / "analysis" / ANALYSIS
    where.mkdir(parents=True, exist_ok=True)
    record = mean_record(series, frame_interval_ns=1.0 / FRAMES_PER_NS)
    record["not_a_measurement"] = (str(record["not_a_measurement"])
                                   if "not_a_measurement" in record else None)
    record = {k: v for k, v in record.items() if v is not None}
    (where / "options.json").write_text(json.dumps({"findings": {"mean": record}}),
                                        encoding="utf-8")


def _rng(case_number: int, index: int, purpose: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([case_number, index, purpose]))


def run_study(case: Case, case_number: int, index: int, workdir: Path) -> Study:
    """One study of `case`, judged and extended by the rule's own loop."""
    from fastmdxplora.simulation.stopping import StopTarget, run_until_known

    rng = _rng(case_number, index, 0)
    runs = [workdir / f"run_{k}" for k in range(case.replicas)]
    states = [case.process.start(rng) for _ in runs]
    series = {}
    first = int(round(case.first_ns * FRAMES_PER_NS))
    for run, state in zip(runs, states):
        run.mkdir(parents=True, exist_ok=True)
        series[run] = case.process.more(rng, state, first)
        _record(run, series[run])

    def extend(those: list[Path], more_ns: float) -> list[dict[str, Any]]:
        n = int(round(more_ns * FRAMES_PER_NS))
        for run, state in zip(those, states):
            series[run] = np.concatenate([series[run], case.process.more(rng, state, n)])
            _record(run, series[run])
        return [{"ok": True} for _ in those]

    record = run_until_known(
        runs, [StopTarget(ANALYSIS, standard_error=case.standard_error)],
        {"max_duration_ns": case.ceiling_ns, "independent_starts": case.independent_starts},
        record_in=workdir, extend_all=extend, say=lambda _said: None,
        production_of=lambda run: series[run].size / FRAMES_PER_NS)
    last = record["rounds"][-1]
    verdict = last["verdicts"][0]
    return Study(case.name, index, record["outcome"], verdict.get("value"),
                 verdict.get("error"), last["production_ns"], len(record["rounds"]),
                 case.process.truth)


def fixed_length(case: Case, case_number: int, index: int, length_ns: float,
                 workdir: Path) -> Study:
    """The same estimate from runs of one length chosen in advance: what the
    estimator gives without stopping on the data, for comparison."""
    from fastmdxplora.simulation.stopping import StopTarget, judge

    rng = _rng(case_number, index, 1)
    runs = [workdir / f"run_{k}" for k in range(case.replicas)]
    n = int(round(length_ns * FRAMES_PER_NS))
    for run in runs:
        run.mkdir(parents=True, exist_ok=True)
        _record(run, case.process.more(rng, case.process.start(rng), n))
    verdict = judge(runs, [StopTarget(ANALYSIS, standard_error=case.standard_error)],
                    length_ns)[0]
    return Study(case.name, index, "fixed", verdict.value, verdict.error, length_ns, 1,
                 case.process.truth)



# ---------------------------------------------------------------------------
# The check under the rule: whether a mean is withheld for its correlation
# ---------------------------------------------------------------------------

#: Series lengths, in multiples of the true inefficiency, at which the
#: withholding is counted, and the series it is counted on.
CHECK_LENGTHS = (1.25, 5.0, 12.5, 50.0, 100.0, 250.0)
CHECK_SERIES = (("fast", Correlated(**_FAST), 39.0), ("slow", Correlated(**_SLOW), 399.0))
CHECK_COUNT = 500


def withholding(*, count: int = CHECK_COUNT, start: int = 0) -> list[dict[str, Any]]:
    """For each series and length, how often the recorded mean is withheld
    because the run is said not to resolve its own correlation, how often it
    is withheld for any reason, and how often a mean that is given has the
    truth within its error. A stationary series many times longer than its
    correlation time should not be withheld; one shorter than a few times
    it should."""
    from fastmdxplora.statistics import mean_record

    rows = []
    for number, (name, process, g) in enumerate(CHECK_SERIES):
        for multiple in CHECK_LENGTHS:
            n = int(round(multiple * g))
            unresolved = withheld = 0
            zs = []
            for index in range(start, start + count):
                rng = _rng(100 + number, index, int(multiple * 100))
                record = mean_record(process.more(rng, process.start(rng), n))
                reason = record.get("not_a_measurement")
                if reason is not None:
                    withheld += 1
                    code = getattr(getattr(reason, "refusal", None), "code", "")
                    unresolved += code == "analysis.sampling.correlation_unresolved"
                elif record.get("standard_error"):
                    zs.append((record["mean"] - process.truth) / record["standard_error"])
            z = np.asarray(zs)
            rows.append({
                "series": name, "inefficiency": g, "multiple": multiple, "frames": n,
                "counted": count, "withheld": withheld, "unresolved": unresolved,
                "given": int(z.size),
                "given_within_one_error": round(float(np.mean(np.abs(z) <= 1.0)), 4) if z.size else None,
                "given_within_two_errors": round(float(np.mean(np.abs(z) <= 2.0)), 4) if z.size else None,
            })
    return rows

# ---------------------------------------------------------------------------
# What the counts say
# ---------------------------------------------------------------------------

def threshold(nominal: float, n: int) -> float:
    """The registered floor for a coverage: four binomial standard errors
    below the nominal value, at the number of studies counted."""
    return nominal - 4.0 * math.sqrt(nominal * (1.0 - nominal) / max(n, 1))


ONE_SIGMA = math.erf(1.0 / math.sqrt(2.0))
TWO_SIGMA = math.erf(2.0 / math.sqrt(2.0))


def coverage(studies: list[Study]) -> dict[str, Any]:
    zs = [s.z for s in studies if s.z is not None]
    if not zs:
        return {"counted": 0}
    z = np.asarray(zs)
    within_one = float(np.mean(np.abs(z) <= 1.0))
    within_two = float(np.mean(np.abs(z) <= 2.0))
    return {
        "counted": int(z.size),
        "within_one_error": round(within_one, 4),
        "within_two_errors": round(within_two, 4),
        "floor_one": round(threshold(ONE_SIGMA, z.size), 4),
        "floor_two": round(threshold(TWO_SIGMA, z.size), 4),
        "holds": bool(within_one >= threshold(ONE_SIGMA, z.size)
                      and within_two >= threshold(TWO_SIGMA, z.size)),
        "rms_z": round(float(np.sqrt(np.mean(z ** 2))), 3),
        "mean_z": round(float(np.mean(z)), 3),
    }


@dataclass
class CaseResult:
    case: str
    said: str
    studies: int
    outcomes: dict[str, int]
    median_production_ns: float | None
    met: dict[str, Any]
    fixed: dict[str, Any] = field(default_factory=dict)
    false_determinations: int = 0


def run_case(case: Case, case_number: int, *, start: int = 0,
             studies: int | None = None, say: Any = print) -> tuple[CaseResult, list[Study]]:
    count = case.studies if studies is None else studies
    done: list[Study] = []
    with tempfile.TemporaryDirectory(prefix="stopping_") as tmp:
        for index in range(start, start + count):
            done.append(run_study(case, case_number, index, Path(tmp) / f"s{index}"))
            if say and (index - start + 1) % 100 == 0:
                say(f"  {case.name}: {index - start + 1} of {count}")
        met = [s for s in done if s.outcome == "met"]
        outcomes: dict[str, int] = {}
        for s in done:
            outcomes[s.outcome] = outcomes.get(s.outcome, 0) + 1
        median = float(np.median([s.production_ns for s in met])) if met else None
        fixed: dict[str, Any] = {}
        if median is not None:
            controls = [fixed_length(case, case_number, index, median,
                                     Path(tmp) / f"f{index}")
                        for index in range(start, start + count)]
            fixed = {"length_ns": median, **coverage(controls)}
        false = sum(1 for s in met if s.z is not None and abs(s.z) > 2.0)
    return CaseResult(case.name, case.said, count, outcomes, median, coverage(met), fixed,
                      false), done


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fastmdxplora.validation.stopping_calibration",
        description="Whether a study stopped by simulation.stop_when reports an honest error.")
    parser.add_argument("--out", default="stopping_calibration.json",
                        help="Where every study and the counts are written.")
    parser.add_argument("--cases", default="",
                        help="Comma-separated case names; all registered cases, and the "
                             "withholding count, by default.")
    parser.add_argument("--start", type=int, default=0,
                        help="The first study index. The registered set starts at 0; a "
                             "held-out set starts at each case's number of studies.")
    parser.add_argument("--studies", type=int, default=None,
                        help="Studies per case; the registered number by default.")
    parser.add_argument("--check-only", action="store_true",
                        help="Count only how often a mean is withheld for its correlation.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    chosen = [c.strip() for c in args.cases.split(",") if c.strip()]
    unknown = sorted(set(chosen) - {c.name for c in CASES})
    if unknown:
        print(f"No such case: {', '.join(unknown)}. The cases are: "
              f"{', '.join(c.name for c in CASES)}.", file=sys.stderr)
        return 2
    # The check under the rule is counted once: with every case, or alone.
    check = withholding(start=args.start) if (args.check_only or not chosen) else []
    if check:
        print("The check under the rule: a mean withheld for its correlation")
    for row in check:
        print(f"  {row['series']} at {row['multiple']:g} times its inefficiency: "
              f"{row['unresolved']} of {row['counted']} said unresolved, {row['withheld']} withheld; "
              f"given within one error {row['given_within_one_error']}")
    results, every = [], []
    for number, case in enumerate(CASES):
        if args.check_only:
            break
        if chosen and case.name not in chosen:
            continue
        print(f"{case.name}: {case.said}")
        result, studies = run_case(case, number, start=args.start, studies=args.studies)
        results.append(result)
        every += [asdict(s) for s in studies]
        m = result.met
        print(f"  {result.outcomes}; met at a median {result.median_production_ns} ns; "
              f"within one error {m.get('within_one_error')}, two {m.get('within_two_errors')}; "
              f"fixed length {result.fixed.get('within_one_error')}, "
              f"{result.fixed.get('within_two_errors')}")
    Path(args.out).write_text(json.dumps(
        {"start": args.start, "frames_per_ns": FRAMES_PER_NS, "withholding": check,
         "cases": [asdict(r) for r in results], "studies": every}, indent=1), encoding="utf-8")
    print(f"Every study is in {args.out}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
