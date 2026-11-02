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

The slope must also be significant against the means' own scatter about the
line. Fit `y_i = a + b x_i` without weights and take Student's t on `k - 2`
degrees of freedom: `t = |b| sqrt(S / s²)`, with `S = sum((x_i - x_mean)²)`
and `s² = sum(r_i²) / (k - 2)` over the residuals `r_i`, whose
two-sided tails are `1 - (2/pi) atan|t|` for one degree of freedom and
`1 - |t| / sqrt(2 + t²)` for two. Without weights, because this half is for
the case where the errors do not hold, and then they are not the right
weights either: weighted by them, an imprecise segment between two precise
ones leaves almost no residual.

If the line fails its fit check, the combined `drift_p` is 1. Otherwise it is
the larger of the two slope probabilities. As before, a drift refusal also
requires the segment means to disagree beyond their own errors
(`heterogeneity > 1`). A refusal carries `analysis.sampling.drifting` and
`drift_test: slope_against_errors_and_scatter`. An extreme tail that
underflows is floored at the smallest positive floating-point value, rather
than reported as exactly zero.

Why both. With one residual degree of freedom (two at four segments) the
fit check cannot tell errors that hold from errors too small for offsets
between segments. Trusting the errors alone (mean rules version 4) called
unordered offsets three times the smallest error a trend in 12% to 40% of
runs; the permutation test holds 5% on such offsets at five segments, whatever
their size. The scatter half holds that too. The price is power at three
segments: the scatter has one degree of freedom, so the three means must lie
close to a line, and a real trend of about four errors a segment is called
in about a third of runs rather than nine in ten. Four segments lose little.
No test on three means can do both: as the offsets grow, any test that keeps
5% on them must reject only where the means lie close to a line, which is
this one.

With five or more means the order-permutation test and its settings are
unchanged; the record says `drift_test: permutation`. With fewer than three,
it says `not_tested`. Invalid precision weights fall back to the existing
equal-weight permutation test, including its limit at three or four means;
an unknown absolute error is not replaced by an assumed unit error. The test
counts the segments that contributed a mean with a resolved error: one
withheld for too few frames is absent, so three good segments and a 2-frame
one are tested as three.

## Fixed synthetic checks

These are post-implementation checks, separate from the registered sequential
stopping-rule calibration. The helper fixes every seed and case:

```bash
python tests/_short_segment_drift_check.py --output /tmp/p1-check.json
```

To count another version, put its `src` directory first on `PYTHONPATH`. The
columns below are the delivery stack before P1 (`3c0cd4e6`, no test at three
or four), mean rules version 4 (the errors alone) and version 5 (this page).
Counts used Python 3.11.17, NumPy 2.4.6 and SciPy 1.17.1.

The first check generates 10,000 sets of means per row with known Gaussian errors.
Rows one to four use errors evenly spaced from 0.2 to 0.8: linear offsets of
twice the segment index, a jump of 10 in the last mean only, and fixed
unordered offsets `[0, 4, 0]` or `[0, 4, -4, 0]`. The last three rows add a
random offset to each segment, SD 0.6, in no order, three times the
smallest error: errors equal (0.2), a short restart (0.5 for the second
segment) and a precise middle (0.2 for the second, 0.56 for the rest).

| Segments | Case | Before | Version 4 | Version 5 |
|---|---|---:|---:|---:|
| 3 | Stationary | 0 | 461 | 66 |
| 3 | Linear | 0 | 9,478 | 3,279 |
| 3 | Jump | 0 | 0 | 0 |
| 3 | Unordered | 0 | 1 | 0 |
| 3 | Offsets, equal errors | 0 | 2,457 | 387 |
| 3 | Offsets, short restart | 0 | 3,983 | 352 |
| 3 | Offsets, precise middle | 0 | 1,242 | 236 |
| 4 | Stationary | 0 | 476 | 110 |
| 4 | Linear | 0 | 9,490 | 9,298 |
| 4 | Jump | 0 | 0 | 0 |
| 4 | Unordered | 0 | 0 | 0 |
| 4 | Offsets, equal errors | 0 | 1,405 | 459 |
| 4 | Offsets, short restart | 0 | 2,137 | 411 |
| 4 | Offsets, precise middle | 0 | 1,341 | 365 |

The second check runs the complete `summarise_segments` path on contiguous
AR(1) runs (phi 0.8, a statistical inefficiency of 9), estimating every error
from the frames: 1,000 runs per row. A segment of 1,000 frames has a mean
error of about 0.095 frame SDs. "Short restart" is 3,000, 500 and 3,000 frames
(a fourth of 3,000 at four segments); "precise middle" is 500, 4,000 and 500
(500 again at four). "Slow mode" adds a second AR(1) of SD 0.5 with a
correlation time of 1,000 frames that the segments' errors miss; "offsets"
add to each segment a random offset of SD 0.3 (about three errors) or
0.15, in no order. Ramps add the offsets given to each segment. Counts are
drift refusals (`analysis.sampling.drifting`) of 1,000.

| Segments | Case | Before | Version 4 | Version 5 |
|---|---|---:|---:|---:|
| 3 | Stationary, equal | 0 | 42 | 5 |
| 4 | Stationary, equal | 0 | 51 | 19 |
| 3 | Stationary, short restart | 0 | 29 | 2 |
| 4 | Stationary, short restart | 0 | 64 | 4 |
| 3 | Stationary, precise middle | 0 | 47 | 13 |
| 4 | Stationary, precise middle | 0 | 56 | 12 |
| 3 | Stationary, a 2-frame fourth segment | 0 | 45 | 4 |
| 3 | Stationary, a 2-frame second segment | 0 | 47 | 11 |
| 3 | Slow mode, equal | 0 | 214 | 43 |
| 4 | Slow mode, equal | 0 | 221 | 62 |
| 3 | Slow mode, short restart | 0 | 191 | 31 |
| 4 | Slow mode, short restart | 0 | 192 | 45 |
| 3 | Offsets SD 0.3, equal | 0 | 244 | 41 |
| 4 | Offsets SD 0.3, equal | 0 | 154 | 50 |
| 3 | Offsets SD 0.3, short restart | 0 | 348 | 48 |
| 4 | Offsets SD 0.3, short restart | 0 | 157 | 39 |
| 3 | Offsets SD 0.15, equal | 0 | 171 | 22 |
| 4 | Offsets SD 0.15, equal | 0 | 148 | 28 |
| 3 | Ramp 0, 0.4, 0.8 | 0 | 889 | 301 |
| 4 | Ramp 0, 0.4, 0.8, 1.2 | 0 | 935 | 896 |
| 3 | Ramp 0, 0.25, 0.5 | 0 | 857 | 198 |
| 4 | Ramp 0, 0.25, 0.5, 0.75 | 0 | 932 | 731 |
| 3 | Ramp 0, 0.4, 0.8, short restart | 0 | 834 | 250 |
| 3 | Ramp 0, 0.4, 0.8, precise middle | 0 | 733 | 273 |
| 3 | Ramp, a 2-frame fourth segment | 0 | 892 | 322 |
| 3 | Ramp, a 2-frame second segment | 0 | 917 | 320 |
| 4 | Ramp, a 2-frame fifth segment | 0 | 928 | 891 |
| 3 | Levelling off 0, 0.6, 0.75 | 0 | 558 | 66 |
| 4 | Levelling off 0, 0.6, 0.8, 0.85 | 0 | 324 | 157 |
| 4 | Zigzag 0, 1.0, 0.5, 1.5 | 0 | 14 | 4 |

A drift refusal is not the only way such a mean is withheld: in the
production path (`mean_record` with the joins) the whole run's own trend
check withholds more of the ramps. For the 3-segment ramp of 0.4 a segment,
version 5 left 489 of 1,000 means determined (version 4: 79; before P1, with
no joins read: 670).

With five or more segments every run of the same 1,000 (stationary, a ramp of
0.2 a segment and offsets SD 0.3 at five; stationary and a ramp of 0.1 a
segment at six) was withheld or pooled exactly as before P1. A label changed
only where a segment whose error is not resolved, or one drifting within
itself, left three or four segments to test: those runs were withheld before
and now say `drifting` (47 of the ramps at five segments).

## Limits and records

The small-segment test assumes approximately independent, Gaussian mean
errors and uses estimated standard errors as their absolute scale. Correlated
segments, unresolved slow modes, uncertain error estimates or heavy-tailed
means can invalidate that approximation; the scatter half is what keeps such
errors from being read as a trend. A slow mode that neighbouring segments
share still moves their means together, and is called drift in 31 to 62 of
1,000 runs above. Each segment must still resolve its own correlation time
before the pooled error is accepted.

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
sampling. Reports read the analyses' saved verdicts. Existing saved verdicts
are not changed; analyse again to apply the check (mean rules version 5;
version 4 judged three or four segments against their errors alone).
New pooled records and drift refusals identify the method through `drift_test`;
guarded mean records retain `segment_joins`.

The inverse-variance fit and known-error covariance follow the
[GNU Scientific Library's weighted least-squares formulation](https://www.gnu.org/software/gsl/doc/html/lls.html).
The distribution and survival-function conventions are described by
[SciPy's normal](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.norm.html)
and [chi-square](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.chi2.html)
documentation. The choice to require a compatible straight-line fit is the
P1 policy for distinguishing a resolved trend from unexplained scatter.
