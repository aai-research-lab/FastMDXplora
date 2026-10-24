# Drift across three or four joined segments

P1 addresses a limit of an order-permutation test: even a perfectly ordered
sequence of three segment means has a two-sided permutation p-value of at
least 2/6, and four have at least 2/24. Neither reaches 0.05, however precise
the means are. The earlier guard could therefore qualify a very precise
drifting run's error without refusing its pooled mean.

## Decision

For three or four segment means with finite, positive standard errors, fit
`y_i = a + b x_i` against segment index `x_i = 0, ..., k-1`, with inverse-variance
weights `w_i = 1 / SE_i²`. Center both columns by their weighted averages.

The slope's variance is `1 / Sxx`, where `Sxx = sum(w_i (x_i - x_bar)²)`.
The two-sided Gaussian slope probability is `erfc(|b| sqrt(Sxx) / sqrt(2))`.
Its uncertainty comes from the supplied errors; estimating it from the line's
residuals would give a perfect line zero slope uncertainty even when its
segment means were very uncertain.

The line must also explain the disagreement at the scale of those errors.
Compute `Q_line = sum(w_i (y_i - a - b x_i)²)`. Its chi-square survival
probability on `k - 2` degrees of freedom must be at least 0.05. For one degree
of freedom the survival function is `erfc(sqrt(Q_line/2))`; for two it is
`exp(-Q_line/2)`.

If the line fails that check, the combined `drift_p` is 1. Otherwise it is the
slope probability. Under the stated Gaussian-error model this gate makes the
test conservative: it can only remove rejections from the slope test. As
before, a drift refusal also requires the segment means to disagree beyond
their own errors (`heterogeneity > 1`). A refusal carries
`analysis.sampling.drifting` and `drift_test: weighted_slope`.
An extreme normal tail that underflows is floored at the smallest positive
floating-point value, rather than reported as exactly zero.

With five or more means the order-permutation test and its settings are
unchanged; the record says `drift_test: permutation`. With fewer than three,
it says `not_tested`. Invalid precision weights fall back to the existing
equal-weight permutation test, including its limit at three or four means;
an unknown absolute error is not replaced by an assumed unit error.

## Fixed synthetic checks

These are post-implementation checks, separate from the registered sequential
stopping-rule calibration. The helper fixes every seed and case:

```bash
python tests/_short_segment_drift_check.py --output /tmp/p1-check.json
```

To count the baseline, use the same helper with the `src` directory of
`main` at `5fb9ae6b` first on `PYTHONPATH`. Counts below used Python 3.12.14,
NumPy 2.3.5 and SciPy 1.17.0.

The first check generates 10,000 independent sets of means per row with known
Gaussian errors, evenly spaced from 0.2 to 0.8. Linear offsets are twice the
segment index. The jump changes only the last mean by 10; unordered offsets
are `[0, 4, 0]` or `[0, 4, -4, 0]`. Seeds are `740001 + 100*k + case_index`,
with cases in the table's order for each segment count.

| Segments | Case | Baseline drift refusals | P1 drift refusals |
|---|---|---:|---:|
| 3 | Stationary | 0/10,000 | 461/10,000 (4.61%) |
| 3 | Linear | 0/10,000 | 9,478/10,000 (94.78%) |
| 3 | Jump | 0/10,000 | 0/10,000 |
| 3 | Unordered | 0/10,000 | 1/10,000 |
| 4 | Stationary | 0/10,000 | 476/10,000 (4.76%) |
| 4 | Linear | 0/10,000 | 9,490/10,000 (94.90%) |
| 4 | Jump | 0/10,000 | 0/10,000 |
| 4 | Unordered | 0/10,000 | 0/10,000 |

The baseline's zero rate on both stationary and drifting cases follows from
its combinatorial floor. The stationary P1 rates are consistent with the
nominal 5% level. They apply to the stated model, rather than proving a rate
for arbitrary molecular-dynamics trajectories.

The second check runs the complete `summarise_segments` path, estimating the
errors from contiguous AR(1) trajectories with `phi = 0.9` and 4,000 frames
per segment. Each case uses 40 seeds, starting at `860001 + 100*k`.
The linear case adds one unit per segment to the same stationary process.

| Segments | Case | Baseline drift refusals | P1 drift refusals | Other P1 outcomes |
|---|---|---:|---:|---|
| 3 | Stationary | 0/40 | 2/40 | 33 clean, 5 qualified |
| 3 | Linear | 0/40 | 36/40 | 4 qualified |
| 4 | Stationary | 0/40 | 2/40 | 35 clean, 3 qualified |
| 4 | Linear | 0/40 | 37/40 | 3 qualified |

The baseline qualified all 40 linear cases at each count. These smaller
trajectory checks exercise estimated errors, equilibration detection and the
pooling path; 40 cases do not establish broad calibration. Regression tests
also check the probability against an independent matrix least-squares fit,
both significance boundaries, unequal errors, reversal, unit changes, invalid
weights, the report's withholding and method metadata.

## Limits and records

The small-segment test assumes approximately independent, Gaussian mean
errors and uses estimated standard errors as their absolute scale. Correlated
segments, unresolved slow modes, uncertain error estimates or heavy-tailed
means can invalidate that approximation. Each segment must still resolve its
own correlation time before the pooled error is accepted.

The trend is against segment order. Unequal segment durations and nonlinear
movement can fail the straight-line check. Returning 1 for that case does not
prove stationarity: substantial unexplained scatter continues to qualify the
pooled error as a lower bound.

Loading a trajectory reads its `.join.json` sidecar and maps the recorded
boundaries through stride and frame selection. The analysis copies preserve
these boundaries. `mean_record(..., joins=...)` uses the segment check as an
additional refusal guard; it does not replace the existing single-series
estimate with a pooled one. Thermodynamic analyses count boundaries after
trimming in each energy column's own samples, independently of trajectory
sampling. Reports retain the analyses' saved verdicts. Existing saved verdicts
are not changed; rerun analysis to apply the check (mean rules version 4).
New pooled records and drift refusals identify the method through `drift_test`;
guarded mean records retain `segment_joins`.

The inverse-variance fit and known-error covariance follow the
[GNU Scientific Library's weighted least-squares formulation](https://www.gnu.org/software/gsl/doc/html/lls.html).
The distribution and survival-function conventions are described by
[SciPy's normal](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.norm.html)
and [chi-square](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.chi2.html)
documentation. The choice to require a compatible straight-line fit is the
P1 policy for distinguishing a resolved trend from unexplained scatter.
