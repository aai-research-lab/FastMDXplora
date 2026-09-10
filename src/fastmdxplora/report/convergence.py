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

__all__ = ["Assessment", "assess_series", "assess_run", "autocorrelation_time"]


#: Below this many independent samples, a mean is a number without a useful
#: error bar. Two is the least that permits any spread at all; five is where
#: the interval starts to narrow enough to say something.
_ENOUGH_SAMPLES = 5

#: Energy drift above this, per nanosecond per degree of freedom, is the
#: conventional sign that the integration is not conserving what it should.
#: In units of kJ/mol per ns per atom.
_ENERGY_DRIFT_LIMIT = 1.0


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

    @property
    def standard_error(self) -> float:
        """Uncertainty on the mean, counting independent samples.

        Dividing by the frame count instead would understate it by the square
        root of the correlation time -- a factor of three or four is ordinary.
        """
        if self.effective_samples < 2:
            return float("nan")
        return float(self.spread / np.sqrt(self.effective_samples))

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
            "mean": round(self.mean, 5),
            "spread": round(self.spread, 5),
            "frames_per_independent_sample": round(self.correlation_frames, 2),
            "effective_samples": round(self.effective_samples, 2),
            "discard": self.discard,
            "standard_error": (None if np.isnan(self.standard_error)
                               else round(self.standard_error, 5)),
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
    """How far the series moved, measured against how much it wobbles.

    A raw slope says nothing without a scale: 0.01 nm of drift is large for a
    stable fold and small for one still relaxing. Comparing the ends against
    the noise within them gives a number that means the same thing for any
    observable.
    """
    series = np.asarray(values, dtype=np.float64)
    if series.size < 6:
        return 0.0
    third = series.size // 3
    first, last = series[:third], series[-third:]
    noise = np.sqrt((first.var() + last.var()) / 2)
    if noise <= 0:
        return 0.0 if np.isclose(first.mean(), last.mean()) else float("inf")
    return float((last.mean() - first.mean()) / noise)


def assess_series(name: str, values: Any) -> Assessment:
    """What one series says about how well it was sampled."""
    series = np.asarray(values, dtype=np.float64)
    series = series[np.isfinite(series)]
    n = series.size
    if n == 0:
        return Assessment(name, 0, 0, float("nan"), float("nan"), 1.0, 0.0, 0.0)

    from fastmdxplora.statistics import correlation_is_resolved, summarise

    # The mean is taken over the equilibrated part, which is what the
    # findings report. Taken over the whole series here, the same observable
    # appeared twice in one document with two different means and nothing to
    # say why: the RMSD of a real study read 0.08895 in this table and 0.09461
    # in its own section, both labelled the mean.
    #
    # Drift is still measured over the whole series. It is the question of
    # whether the run was still relaxing, and discarding the relaxation before
    # asking would answer it by construction.
    equilibrated, _reason = summarise(series)
    correlation = autocorrelation_time(series)
    # Whether the series can see how long its own memory is. A correlation
    # approaching what the sum can reach is a floor rather than a measurement,
    # and the effective-sample count that follows is the independence the
    # estimate failed to rule out.
    #
    # The rule here was a tenth of the run, which is the usual working limit
    # and lets the flattering case through: a series with a true correlation
    # of 2000 measured 361 over 4000 frames, and 361 is under a tenth of 4000.
    # Halving the series and asking whether the estimate moves catches it,
    # and it is the same criterion the analyses apply -- one run should not be
    # measurable in the report and unresolved in the findings.
    measurable = correlation_is_resolved(series)
    # Drift is compared between the first and last thirds, so a series that
    # cannot be divided into thirds with anything in them says nothing about
    # trend. Six is the least that gives two points per third.
    drift_measurable = n >= 6
    return Assessment(
        name=name,
        n_frames=int(n),
        discard=int(equilibrated.discard) if equilibrated else 0,
        mean=float(equilibrated.mean) if equilibrated else float(series.mean()),
        spread=(float(equilibrated.standard_deviation) if equilibrated
                else (float(series.std(ddof=1)) if n > 1 else 0.0)),
        correlation_frames=(float(equilibrated.inefficiency) if equilibrated
                            else correlation),
        effective_samples=(float(equilibrated.effective_samples) if equilibrated
                           else float(n / correlation)),
        drift_in_noise=_drift_in_noise(series),
        correlation_is_measurable=bool(measurable),
        drift_is_measurable=bool(drift_measurable),
    )


def assess_run(
    series: dict[str, Any],
    *,
    duration_ns: float | None = None,
    n_atoms: int | None = None,
    target_temperature_K: float | None = None,
) -> dict[str, Any]:
    """Everything the run says about whether it can be interpreted.

    ``series`` maps an observable's name to its values over the run: the
    potential energy and temperature from ``energy.csv``, and whichever
    structural measures were computed.
    """
    assessments = {name: assess_series(name, values)
                   for name, values in series.items() if values is not None}

    findings: list[str] = []
    energy = assessments.get("potential_energy")
    # The range of a two-point series is the difference between two numbers,
    # which says nothing about drift: it was reported as 94 kJ/mol per ns per
    # atom from two samples of a run that had just been minimized.
    if (energy is not None and energy.n_frames >= 6
            and duration_ns and n_atoms and duration_ns > 0):
        # Drift per nanosecond per atom, which is how the conventional limit
        # is quoted and the only form comparable between systems.
        span = float(np.ptp(np.asarray(list(series["potential_energy"]),
                                       dtype=np.float64)))
        drift = abs(energy.mean and (span / duration_ns / max(n_atoms, 1)))
        if drift > _ENERGY_DRIFT_LIMIT:
            findings.append(
                f"The potential energy moved by {span:,.0f} kJ/mol over "
                f"{duration_ns:.3g} ns, which is {drift:.2g} kJ/mol per ns "
                f"per atom. Above about {_ENERGY_DRIFT_LIMIT} the integration "
                "is usually the cause rather than the system: check the "
                "timestep and the constraints."
            )

    temperature = assessments.get("temperature")
    if temperature is not None and target_temperature_K:
        away = abs(temperature.mean - target_temperature_K)
        if away > 5.0:
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
            "Still moving in one direction: " + ", ".join(sorted(still_drifting))
            + ". The run has not finished equilibrating, so averages over it "
            "describe the approach rather than the state."
        )

    if undecidable:
        findings.append(
            "Too short to say whether it has equilibrated: "
            + ", ".join(sorted(undecidable))
            + ". Drift is judged by comparing the start of the run against "
            "the end, and a series this short has no start and end to "
            "compare."
        )

    unmeasurable = [a.name for a in assessments.values()
                    if not a.correlation_is_measurable]
    if unmeasurable:
        findings.append(
            "Too short to tell how correlated it is: "
            + ", ".join(sorted(unmeasurable))
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
            + ", ".join(sorted(thin))
            + ". Consecutive frames are nearly the same structure, so the "
            "number of independent observations is set by how fast the "
            "observable forgets, not by how often it was written out."
        )

    return {
        "observables": {name: a.as_record() for name, a in assessments.items()},
        "findings": findings,
        # The single question somebody wants answered, and the honest answer
        # for most short runs is no.
        "interpretable": not (thin or still_drifting or unmeasurable
                              or undecidable),
    }
