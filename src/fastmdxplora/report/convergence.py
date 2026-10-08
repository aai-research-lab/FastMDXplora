"""Whether a trajectory holds enough information to be worth interpreting.

A hundred frames look like a hundred measurements. Consecutive frames of a
molecular dynamics run are almost the same structure, so they are closer to
one, and every mean and error bar computed over them inherits that. Reporting
an RMSD as mean plus or minus standard deviation over a hundred correlated
frames gives an uncertainty several times too small -- the same mistake this
software fixed for interaction occupancies, in the analysis everybody reads
first.

So this reports, for each observable:

- how many **independent** samples the trajectory holds, from the
  autocorrelation time rather than the frame count;
- whether the observable has **stopped drifting**, by comparing the first and
  last thirds of the run against the noise within them;
- and for the energy and temperature, whether the **integration was sound**:
  a drifting total energy or a temperature away from its target means the
  numbers describe an artefact rather than the system.

Where a run is too short to answer, it says so. A convergence report that
returns a number for everything launders a four-picosecond run into an
apparently validated one, which is worse than having no report: somebody
reading it would have less reason to doubt than they started with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = ["CHECKS", "Assessment", "assess_series", "assess_run", "autocorrelation_time"]


#: Below this many independent samples, a mean is a number without a useful
#: error bar. Two is the least that permits any spread at all; five is where
#: the interval starts to narrow enough to say something.
# One bar for one judgement. This module had its own, at five, while the
# per-analysis prose in the same report said ten -- so the Convergence
# table counted rg at 8.3 independent samples as adequately sampled while
# the section above it said it was not, and the Summary's count of "too
# few" disagreed with the table under it. The constant lives in
# statistics.py with its reasoning; this is a reference to it.
from fastmdxplora.statistics import MINIMUM_EFFECTIVE_SAMPLES as _ENOUGH_SAMPLES

#: Energy drift above this, per nanosecond per degree of freedom, is the
#: conventional sign that the integration is not conserving what it should.
#: In units of kJ/mol per ns per atom.
_ENERGY_DRIFT_LIMIT = 1.0

#: How far the mean temperature may sit from the thermostat's target before
#: the ensemble is not the one the settings describe.
_TEMPERATURE_LIMIT_K = 5.0

#: What a run is held to, in the order it is judged: said before a run (an
#: Agent's plan) and ticked after it (the report, the Agent), from this one
#: list, so what was promised and what was checked cannot differ. Each is
#: ``(key, what is checked, the same in a few words)``.
CHECKS: tuple[tuple[str, str, str], ...] = (
    ("equilibrated", "each observable equilibrates before it is averaged",
     "each observable equilibrated"),
    ("correlation", "each observable's correlation time is resolved by the run",
     "each correlation time resolved"),
    ("sampled", f"each mean rests on at least {_ENOUGH_SAMPLES:g} independent samples",
     f"at least {_ENOUGH_SAMPLES:g} independent samples per mean"),
    ("temperature", f"the mean temperature is within {_TEMPERATURE_LIMIT_K:g} K of the target",
     f"temperature within {_TEMPERATURE_LIMIT_K:g} K of the target"),
    ("energy", f"the potential energy's trend stays under {_ENERGY_DRIFT_LIMIT:g} kJ/mol "
               "per ns per atom",
     f"potential energy trend under {_ENERGY_DRIFT_LIMIT:g} kJ/mol per ns per atom"),
)


@dataclass(frozen=True)
class Assessment:
    """What one observable says about the run that produced it."""

    name: str
    n_frames: int
    #: Frames discarded before averaging, so that the mean describes the
    #: equilibrium rather than the approach to it. The same point the
    #: per-analysis findings use, so the two agree.
    discard: int
    mean: float
    #: Standard deviation over the frames, which is a property of the
    #: trajectory rather than an uncertainty on the mean.
    spread: float
    #: Frames per independent sample, from the autocorrelation.
    correlation_frames: float
    effective_samples: float
    #: Difference between the last third and the first third, in units of the
    #: within-third noise. Above about 2 the observable is still moving.
    drift_in_noise: float
    #: Whether the correlation time is itself measurable from this many
    #: frames. The estimate truncates at half the series, so a short run
    #: cannot see a correlation longer than that -- and reports the
    #: independence it failed to rule out, which is the wrong direction for a
    #: convergence report to err in.
    correlation_is_measurable: bool = True
    #: Whether the series is long enough to say anything about drift at all.
    #: Two points cannot show a trend, and reporting one as equilibrated is a
    #: claim from no evidence -- the same defect as reporting independence that
    #: was never measured.
    drift_is_measurable: bool = True
    #: Why the analyses' record gives this mean no error, where it gives
    #: none (`statistics.summarise`); None where the mean is determined.
    withheld: str | None = None

    @property
    def standard_error(self) -> float:
        """Uncertainty on the mean, counting independent samples, where the
        analyses' record gives one; NaN where it withholds it. The table
        printed an error for every mean, so it gave one beside a mean the
        Analysis page said was not determined.

        Dividing by the frame count instead would understate it by the square
        root of the correlation time -- a factor of three or four is ordinary.
        """
        if self.withheld is not None or self.effective_samples < 2:
            return float("nan")
        return float(self.spread / np.sqrt(self.effective_samples))

    @property
    def is_determined(self) -> bool:
        """Whether the mean is determined, as every page says it."""
        return self.withheld is None and np.isfinite(self.standard_error)

    @property
    def is_sampled_enough(self) -> bool:
        """Enough independent observation to average over.

        A correlation time that cannot be measured from this many frames
        counts as not enough, whatever the arithmetic says: the number would
        be the independence the estimate failed to disprove.
        """
        return (self.correlation_is_measurable
                and self.effective_samples >= _ENOUGH_SAMPLES)

    @property
    def has_equilibrated(self) -> bool | None:
        """Whether the observable has stopped moving in one direction.

        ``None`` where the series is too short to tell. A two-point series
        reported as equilibrated is a claim from no evidence, and it appeared
        beside "too few independent samples" about the same numbers.
        """
        if not self.drift_is_measurable:
            return None
        return abs(self.drift_in_noise) < 2.0

    @property
    def has_settled(self) -> bool | None:
        """The former name, kept so nothing outside has to move at once.

        The word was never the field's: `detect_equilibration` implements
        Chodera's automated equilibration detection, and the literature
        calls the discarded transient the equilibration period. "Settled"
        was a second vocabulary for one idea.
        """
        return self.has_equilibrated

    def as_record(self) -> dict[str, Any]:
        return {
            "observable": self.name,
            "frames": self.n_frames,
            # Six significant figures at least, not five places: an error of
            # 0.0017484 kept as 0.00175 was given as 0.0018 in the report's
            # table and 0.0017 on every other page.
            "mean": _six_figures(self.mean),
            "spread": _six_figures(self.spread),
            "frames_per_independent_sample": round(self.correlation_frames, 2),
            "effective_samples": round(self.effective_samples, 2),
            "discard": self.discard,
            "standard_error": (None if np.isnan(self.standard_error)
                               else _six_figures(self.standard_error)),
            "determined": self.is_determined,
            "withheld": self.withheld,
            "sampled_enough": self.is_sampled_enough,
            "correlation_measurable": self.correlation_is_measurable,
            # Both spellings. `equilibrated` is the name; `settled` is what
            # runs already on disk carry, and a report regenerated from one
            # of those must not lose the field.
            "equilibrated": self.has_equilibrated,
            "settled": self.has_equilibrated,
            "drift_measurable": self.drift_is_measurable,
            "drift_in_noise": round(self.drift_in_noise, 2),
        }


def autocorrelation_time(values: Any) -> float:
    """Frames until a series forgets where it was.

    Integrated autocorrelation, summed until the correlation first goes
    negative -- the standard truncation, because the tail of the estimate is
    noise and summing it adds variance rather than information.

    One implementation, in the analysis layer, because there were briefly two:
    this one and a second written for the per-frame analyses. They agreed to
    two decimal places on every correlated series tested and disagreed on a
    constant one, where this one was right -- a series that never changes is
    perfectly correlated, and no number of frames of it is more than one
    observation. Two functions computing the same statistic is how one of them
    quietly becomes wrong.
    """
    from fastmdxplora.statistics import statistical_inefficiency

    return statistical_inefficiency(values)


def _drift_in_noise(values: Any) -> float:
    """How far the series moved, against how much it wobbles: one
    implementation, the analyses' (`statistics.drift_in_spread`)."""
    from fastmdxplora.statistics import drift_in_spread

    return drift_in_spread(values)


def _replace_summary(equilibrated, **changes):
    """A copy of an Equilibrated with some fields replaced.

    Used to carry the pooled mean and error onto a record whose discard and
    inefficiency came from one segment. Dataclasses.replace rather than
    mutation because Equilibrated is frozen, and it is frozen so that a
    record cannot be edited after the thing that computed it has gone.
    """
    import dataclasses

    if equilibrated is None:
        return None
    return dataclasses.replace(equilibrated, **changes)


def assess_series(name: str, values: Any,
                  joins: "list[int] | tuple[int, ...] | None" = None,
                  record: "dict[str, Any] | None" = None) -> Assessment:
    """What one series says about how well it was sampled, by the rules the
    analyses record a mean with (`statistics.summarise`), so the report
    judges a mean as the Overview, the Analysis page and the Agent do.

    ``record`` is the analysis's own record of this series' mean, where it
    wrote one (`statistics.mean_record`): its mean, discard, independent
    samples and verdict are taken as written, so one mean has one verdict.
    """
    series = np.asarray(values, dtype=np.float64)
    series = series[np.isfinite(series)]
    n = series.size
    if n == 0:
        return Assessment(name, 0, 0, float("nan"), float("nan"), 1.0, 0.0, 0.0,
                          withheld="There is nothing to average.")

    from fastmdxplora.statistics import summarise

    reason: Any = None
    if joins:
        # A joined run read as a single series loses most of it: the
        # equilibration detector finds a join, calls it a transient, and
        # discards everything before. Measured at six of ten segments on a
        # ten-segment run, with the surviving mean eighteen standard errors
        # from the truth and a standard error saying otherwise.
        from fastmdxplora.statistics import summarise_segments

        pooled, withheld = summarise_segments(series, joins)
        if pooled is None:
            # The joined run supports no mean -- drifting segments, or too
            # few independent samples once the joins are accounted for.
            # Falling back to the naive reading here would report the very
            # number the join-aware path just refused, which is worse than
            # having never asked.
            equilibrated, _reason = summarise(series)
            equilibrated = _replace_summary(
                equilibrated, mean=float("nan"), standard_error=float("nan"))
            reason = withheld or "The joined run supports no mean."
        else:
            equilibrated = pooled.segments[0] if pooled.segments else None
            # The pooled mean and error are what the run supports. The
            # discard and inefficiency come from the first segment, since
            # per-segment equilibration is what was actually done and there
            # is no single discard for a joined run.
            equilibrated = _replace_summary(
                equilibrated, mean=pooled.mean,
                standard_error=pooled.standard_error,
                effective_samples=pooled.effective_samples)
            reason = withheld
    else:
        equilibrated, reason = summarise(series)
    if isinstance(record, dict) and _number(record.get("mean")) is not None:
        # The analysis's own record, as written.
        discard = int(_number(record.get("discard")) or 0)
        effective = _number(record.get("effective_samples"))
        spread = _number(record.get("standard_deviation"))
        equilibrated = _replace_summary(
            equilibrated, mean=float(record["mean"]), discard=discard,
            effective_samples=effective if effective is not None else (
                equilibrated.effective_samples if equilibrated else 1.0),
            standard_deviation=spread if spread is not None else (
                equilibrated.standard_deviation if equilibrated else 0.0))
        reason = record.get("not_a_measurement") or None
        if reason is None and _number(record.get("standard_error")) is None:
            reason = "No error was recorded."
    code = str(getattr(getattr(reason, "refusal", None), "code", "") or "")
    if not code and isinstance(reason, str) and reason.startswith(
            "This run is not long against its own correlation time"):
        code = "analysis.sampling.correlation_unresolved"
    discard = int(equilibrated.discard) if equilibrated else 0
    kept = series[discard:] if discard < n else series[-1:]
    effective = (float(equilibrated.effective_samples) if equilibrated
                 else float(n / autocorrelation_time(series)))
    return Assessment(
        name=name,
        n_frames=int(n),
        discard=discard,
        mean=float(equilibrated.mean) if equilibrated else float(series.mean()),
        spread=(float(equilibrated.standard_deviation) if equilibrated
                else (float(series.std(ddof=1)) if n > 1 else 0.0)),
        correlation_frames=(float(kept.size / effective) if effective > 0
                            else float(kept.size)),
        effective_samples=effective,
        # Drift over the frames the mean is taken over: what was discarded
        # is the equilibration, which moves by definition.
        drift_in_noise=_drift_in_noise(kept),
        # Resolved as the analyses resolve it, after the equilibration: the
        # report asked the whole series, transient and all, and called the
        # correlation of an RMSD the Analysis page had determined
        # unresolved.
        correlation_is_measurable=code != "analysis.sampling.correlation_unresolved",
        drift_is_measurable=bool(kept.size >= 6),
        withheld=str(reason) if reason else None,
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if np.isfinite(value) else None


#: An observable as a reader says it, where its record's key is not.
_SAID_AS = {"potential_energy": "potential energy", "rmsd": "RMSD", "rg": "Rg",
            "sasa": "SASA", "total_energy": "total energy",
            "kinetic_energy": "kinetic energy"}


def said_as(name: str) -> str:
    """An observable's key as a reader says it: potential_energy is
    "potential energy", rmsd "RMSD"."""
    return _SAID_AS.get(name, name.replace("_", " "))


def _named(names: "list[str]") -> str:
    return ", ".join(said_as(name) for name in sorted(names))


def assess_run(
    series: dict[str, Any],
    *,
    records: "dict[str, dict[str, Any]] | None" = None,
    joins: "list[int] | tuple[int, ...] | None" = None,
    duration_ns: float | None = None,
    n_atoms: int | None = None,
    target_temperature_K: float | None = None,
) -> dict[str, Any]:
    """Everything the run says about whether it can be interpreted.

    ``series`` maps an observable's name to its values over the run: the
    potential energy and temperature from ``energy.csv``, and whichever
    structural measures were computed.

    ``joins`` is where the segments of a joined run begin, in frames.
    :func:`fastmdxplora.analysis.joining.joins_beside` reads it from the
    record the joiner leaves, so a caller with a trajectory path does not
    have to carry it separately. Absent for a run that went through in one
    piece, which is most of them.
    """
    records = records or {}
    assessments = {name: assess_series(name, values, joins=joins, record=records.get(name))
                   for name, values in series.items() if values is not None}

    findings: list[str] = []
    # Each check's verdict: True passed, False failed, None could not be
    # judged from what the run recorded; and what decided it.
    verdicts: dict[str, tuple[bool | None, str]] = {}
    energy = assessments.get("potential_energy")
    # The range of a two-point series is the difference between two numbers,
    # which says nothing about drift: it was reported as 94 kJ/mol per ns per
    # atom from two samples of a run that had just been minimized.
    if (energy is not None and energy.n_frames >= 6
            and duration_ns and n_atoms and duration_ns > 0):
        # Drift per nanosecond per atom, which is how the conventional limit
        # is quoted and the only form comparable between systems. The
        # trend's slope, fitted over the run, not its range: the range of a
        # thermostatted energy is its fluctuation, which over 100 ps of a
        # small protein read 2.2 kJ/mol per ns per atom and failed a run
        # whose energy had no trend at all.
        energies = np.asarray(list(series["potential_energy"]), dtype=np.float64)
        energies = energies[np.isfinite(energies)]
        times = np.linspace(0.0, float(duration_ns), energies.size)
        slope, error = 0.0, 0.0
        if energies.size >= 6:
            fitted = np.polyfit(times, energies, 1)
            slope = float(fitted[0])
            residual = energies - np.polyval(fitted, times)
            spread = float(np.sum((times - times.mean()) ** 2))
            error = (float(np.sqrt(np.sum(residual ** 2) / (energies.size - 2) / spread))
                     if spread > 0 else 0.0)
        moved = slope * float(duration_ns)
        drift = abs(slope) / max(n_atoms, 1)
        # A trend only past twice its error: the noise of a short run fits
        # a slope of its own, and failed a run with none.
        trend = abs(slope) > 2.0 * error
        verdicts["energy"] = (not trend or drift <= _ENERGY_DRIFT_LIMIT,
                              f"{drift:.2g} kJ/mol per ns per atom")
        if trend and drift > _ENERGY_DRIFT_LIMIT:
            findings.append(
                f"The potential energy's trend moved it by {moved:,.0f} kJ/mol over "
                f"{duration_ns:.3g} ns, which is {drift:.2g} kJ/mol per ns "
                f"per atom. Above about {_ENERGY_DRIFT_LIMIT} the integration "
                "is usually the cause rather than the system: check the "
                "timestep and the constraints."
            )

    temperature = assessments.get("temperature")
    if temperature is not None and target_temperature_K:
        away = abs(temperature.mean - target_temperature_K)
        verdicts["temperature"] = (away <= _TEMPERATURE_LIMIT_K,
                                   f"{temperature.mean:.1f} K against {target_temperature_K:.1f} K")
        if away > _TEMPERATURE_LIMIT_K:
            findings.append(
                f"The mean temperature was {temperature.mean:.1f} K against a "
                f"target of {target_temperature_K:.1f} K. A thermostat that "
                "does not hold its target means the ensemble is not the one "
                "the settings describe."
            )

    still_drifting = [a.name for a in assessments.values()
                      if a.has_equilibrated is False]
    undecidable = [a.name for a in assessments.values()
                   if a.has_equilibrated is None]
    if still_drifting:
        findings.append(
            "Still moving in one direction: " + _named(still_drifting)
            + ". The run has not finished equilibrating, so averages over it "
            "describe the approach rather than the state."
        )

    if undecidable:
        findings.append(
            "Too short to say whether it has equilibrated: "
            + _named(undecidable)
            + ". Drift is judged by comparing the start of the run against "
            "the end, and a series this short has no start and end to "
            "compare."
        )

    unmeasurable = [a.name for a in assessments.values()
                    if not a.correlation_is_measurable]
    if unmeasurable:
        findings.append(
            "Too short to tell how correlated it is: "
            + _named(unmeasurable)
            + ". The correlation time is estimated by summing until the "
            "series forgets itself, and a run this length cannot see a memory "
            "longer than half of it. Any independence reported here is the "
            "independence the estimate failed to rule out, not independence "
            "that was found."
        )

    thin = [a.name for a in assessments.values()
            if not a.is_sampled_enough and a.correlation_is_measurable]
    if thin:
        findings.append(
            "Too few independent samples to average: "
            + _named(thin)
            + ". Consecutive frames are nearly the same structure, so the "
            "number of independent observations is set by how fast the "
            "observable forgets, not by how often it was written out."
        )

    if still_drifting:
        verdicts["equilibrated"] = (False, "still moving: " + _named(still_drifting))
    elif undecidable:
        verdicts["equilibrated"] = (None, "too short to say: " + _named(undecidable))
    elif assessments:
        verdicts["equilibrated"] = (True, "on " + _named(list(assessments)))
    if unmeasurable:
        verdicts["correlation"] = (False, "not resolved: " + _named(unmeasurable))
    elif assessments:
        verdicts["correlation"] = (True, "on " + _named(list(assessments)))
    measurable = [a for a in assessments.values() if a.correlation_is_measurable]
    if thin:
        verdicts["sampled"] = (False, "too few: " + _named(thin))
    elif measurable:
        verdicts["sampled"] = (
            None if unmeasurable else True,
            "fewest: " + said_as(min(measurable, key=lambda a: a.effective_samples).name)
            + f", {min(a.effective_samples for a in measurable):.0f}"
            + ("; the rest cannot be counted" if unmeasurable else ""))
    elif assessments:
        verdicts["sampled"] = (None, "no observable's correlation time could be resolved")

    # Why a check could not be judged, where it could not.
    if "energy" not in verdicts:
        verdicts["energy"] = (None, (
            "no potential energy was recorded" if energy is None
            else f"{energy.n_frames} energy records, too few to judge" if energy.n_frames < 6
            else "the run's length or size was not recorded"))
    if "temperature" not in verdicts:
        verdicts["temperature"] = (None, (
            "no temperature was recorded" if temperature is None
            else "no target temperature was recorded"))

    checks = []
    for key, said, _short in CHECKS:
        passed, detail = verdicts.get(key, (None, "not recorded by this run"))
        checks.append({"check": key, "said": said, "passed": passed, "detail": detail})

    return {
        "observables": {name: a.as_record() for name, a in assessments.items()},
        "findings": findings,
        "checks": checks,
        # The single question somebody wants answered, and the honest answer
        # for most short runs is no.
        "interpretable": not (thin or still_drifting or unmeasurable
                              or undecidable),
    }


def _six_figures(value: float) -> float:
    """A number kept to five places, or to six significant figures where
    that keeps more (NaN and infinities as they are)."""
    value = float(value)
    if not np.isfinite(value) or value == 0:
        return value
    return round(value, max(5, 5 - int(np.floor(np.log10(abs(value))))))
