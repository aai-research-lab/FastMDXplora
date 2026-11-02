# Drift across three or four joined segments

P1 addresses a limit of an order-permutation test: even a perfectly ordered
sequence of three segment means has a two-sided permutation p-value of at
least 2/6, and four have at least 2/24. Neither reaches 0.05, however precise
the means are. The earlier guard could therefore qualify a very precise
drifting run's error without refusing its pooled mean.

## Decision

For three or four segment means with finite, positive standard errors, fit
`y_i = a + b x_i` against where each mean sits in time: `x_i` is the centre
frame of the frames segment `i`'s mean is taken over, after its own discard.
Weights are inverse variances, `w_i = 1 / SE_i²`. Center both columns by
their weighted averages. Against segment index, a drift steady in time over
segments of unequal length (500, 500 and 4,000 frames) bends off a straight
line, fails the fit check below and was missed, the more the larger it was.

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
line. Fit `y_i = a + b x_i` (the same centres) without weights and take
Student's t on `k - 2`
degrees of freedom: `t = |b| sqrt(S / s²)`, with `S = sum((x_i - x_mean)²)`
and `s² = sum(r_i²) / (k - 2)` over the residuals `r_i`, whose
two-sided tails are `1 - (2/pi) atan|t|` for one degree of freedom and
`1 - |t| / sqrt(2 + t²)` for two. Without weights, because this half is for
the case where the errors do not hold, and then they are not the right
weights either: weighted by them, an imprecise segment between two precise
ones leaves almost no residual.

If the line passes its fit check, the combined `drift_p` is the larger of the
two slope probabilities. If it fails, the errors cannot judge the slope (the
means stray from a line by more than they allow) and `drift_p` is the scatter's
probability alone. Returning 1 there (as first built) pooled a large monotone
drift off a straight line, and the larger the drift against the errors, the
surer it failed the fit: a real run's temperature, 177.79, 185.45, 186.61 and
192.58 K over errors of 0.5 to 0.7 K (Q 310 on 3 degrees of freedom), was
pooled; its scatter's p is 0.024, and it is now called. The refusal says how much the
line changes by: its slope times the span from the first centre to the last.
As before, a drift refusal also
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
unchanged (it asks the order alone, so the centres do not enter it); the record says `drift_test: permutation`. With fewer than three,
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
or four), mean rules version 4 (the errors alone, `956957d6`) and version 5
(this page). The second table also gives version 5 as first built
(`b366867e`), which fitted the means against segment index rather than time;
`--cases` counts only the trajectory rows named, and `--by-index` places each
mean at its segment's index rather than its centre in time.
Counts used Python 3.11.17, NumPy 2.4.6 and SciPy 1.17.1.

Reading the scatter alone where the line fails the errors' fit (`de2b5dc9`
returned 1 there) moved only four-segment counts. In the first table: linear
9,298 to 9,535, offsets with equal errors 459 to 472, a short restart 411 to
413; every three-segment row and every other four-segment row is unchanged.
In the second: slow mode 73 to 78 (equal) and 47 to 53 (short restart),
offsets SD 0.3 53 to 56 (equal) and 44 to 45 (short restart), the ramps 890
to 919, 724 to 725 and 901 to 939 (a 2-frame fifth segment), levelling off
166 to 173, the zigzag 0 to 1, and the ramp in time 451 to 475. Every
three-segment row, every stationary row, and every run at five or six
segments is as it was.

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
| 4 | Linear | 0 | 9,490 | 9,535 |
| 4 | Jump | 0 | 0 | 0 |
| 4 | Unordered | 0 | 0 | 0 |
| 4 | Offsets, equal errors | 0 | 1,405 | 472 |
| 4 | Offsets, short restart | 0 | 2,137 | 413 |
| 4 | Offsets, precise middle | 0 | 1,341 | 365 |

The second check runs the complete `summarise_segments` path on contiguous
AR(1) runs (phi 0.8, a statistical inefficiency of 9), estimating every error
from the frames: 1,000 runs per row. A segment of 1,000 frames has a mean
error of about 0.095 frame SDs. "Short restart" is 3,000, 500 and 3,000 frames
(a fourth of 3,000 at four segments); "precise middle" is 500, 4,000 and 500
(500 again at four). "Slow mode" adds a second AR(1) of SD 0.5 with a
correlation time of 1,000 frames that the segments' errors miss; "offsets"
add to each segment a random offset of SD 0.3 (about three errors) or
0.15, in no order. Ramps add the offsets given to each segment, except
"ramp in time", which adds a rise linear in time of 2.4 frame SDs over the
whole run. Counts are drift refusals (`analysis.sampling.drifting`) of 1,000.
Placing each mean at its centre in time leaves the false rates where they
were (within the counts' noise; segments of equal length differ only by
their discards) and, on unequal segments, calls a ramp in time in 206 and
475 runs of 1,000 that index called in 0 and 31 (0 and 38 by index with
this page's rule). Most of the rest of those
ramps are withheld anyway, for the 4,000-frame segment's own trend
(`still_drifting`, 497 and 455 of 1,000).

| Segments | Case | Before | Version 4 | Version 5, by index | Version 5 |
|---|---|---:|---:|---:|---:|
| 3 | Stationary, equal | 0 | 42 | 5 | 7 |
| 4 | Stationary, equal | 0 | 51 | 19 | 18 |
| 3 | Stationary, short restart | 0 | 29 | 2 | 1 |
| 4 | Stationary, short restart | 0 | 64 | 4 | 5 |
| 3 | Stationary, precise middle | 0 | 47 | 13 | 10 |
| 4 | Stationary, precise middle | 0 | 56 | 12 | 11 |
| 3 | Stationary, a 2-frame fourth segment | 0 | 45 | 4 | 3 |
| 3 | Stationary, a 2-frame second segment | 0 | 47 | 11 | 11 |
| 3 | Slow mode, equal | 0 | 214 | 43 | 48 |
| 4 | Slow mode, equal | 0 | 221 | 62 | 78 |
| 3 | Slow mode, short restart | 0 | 191 | 31 | 30 |
| 4 | Slow mode, short restart | 0 | 192 | 45 | 53 |
| 3 | Offsets SD 0.3, equal | 0 | 244 | 41 | 38 |
| 4 | Offsets SD 0.3, equal | 0 | 154 | 50 | 56 |
| 3 | Offsets SD 0.3, short restart | 0 | 348 | 48 | 47 |
| 4 | Offsets SD 0.3, short restart | 0 | 157 | 39 | 45 |
| 3 | Offsets SD 0.15, equal | 0 | 171 | 22 | 25 |
| 4 | Offsets SD 0.15, equal | 0 | 148 | 28 | 27 |
| 3 | Ramp 0, 0.4, 0.8 | 0 | 889 | 301 | 303 |
| 4 | Ramp 0, 0.4, 0.8, 1.2 | 0 | 935 | 896 | 919 |
| 3 | Ramp 0, 0.25, 0.5 | 0 | 857 | 198 | 191 |
| 4 | Ramp 0, 0.25, 0.5, 0.75 | 0 | 932 | 731 | 725 |
| 3 | Ramp 0, 0.4, 0.8, short restart | 0 | 834 | 250 | 250 |
| 3 | Ramp 0, 0.4, 0.8, precise middle | 0 | 733 | 273 | 240 |
| 3 | Ramp, a 2-frame fourth segment | 0 | 892 | 322 | 315 |
| 3 | Ramp, a 2-frame second segment | 0 | 917 | 320 | 315 |
| 4 | Ramp, a 2-frame fifth segment | 0 | 928 | 891 | 939 |
| 3 | Levelling off 0, 0.6, 0.75 | 0 | 558 | 66 | 69 |
| 4 | Levelling off 0, 0.6, 0.8, 0.85 | 0 | 324 | 157 | 173 |
| 4 | Zigzag 0, 1.0, 0.5, 1.5 | 0 | 14 | 4 | 1 |
| 3 | Stationary, 500, 500, 4,000 | 0 | 48 | 5 | 3 |
| 4 | Stationary, 500, 500, 500, 4,000 | 0 | 47 | 6 | 1 |
| 3 | Ramp in time 2.4, 500, 500, 4,000 | 0 | 8 | 0 | 206 |
| 4 | Ramp in time 2.4, 500, 500, 500, 4,000 | 0 | 178 | 31 | 475 |

A drift refusal is not the only way such a mean is withheld: in the
production path (`mean_record` with the joins) the whole run's own trend
check withholds more of the ramps. For the 3-segment ramp of 0.4 a segment,
version 5 left 490 of 1,000 means determined (the helper's `mean_record`
count) (489 as first built, by index;
version 4: 79; before P1, with no joins read: 670).

With five or more segments every run of the same 1,000 (stationary, a ramp of
0.2 a segment and offsets SD 0.3 at five; stationary and a ramp of 0.1 a
segment at six) was withheld or pooled exactly as before P1. A label changed
only where a segment whose error is not resolved, or one drifting within
itself, left three or four segments to test: those runs were withheld before
and now say `drifting` (57 of the ramps at five segments and 5 of the
offsets; 47 and 6 as first built, by index). Analyses now apply the test to
their saved means too, which before P1 did not read the joins, so a saved
mean of a joined run of five or more segments can now be withheld as
drifting.

## Limits and records

The small-segment test assumes approximately independent, Gaussian mean
errors and uses estimated standard errors as their absolute scale. Correlated
segments, unresolved slow modes, uncertain error estimates or heavy-tailed
means can invalidate that approximation; the scatter half is what keeps such
errors from being read as a trend. A slow mode that neighbouring segments
share still moves their means together, and is called drift in 30 to 78 of
1,000 runs above. Each segment must still resolve its own correlation time
before the pooled error is accepted.

The trend is against each mean's centre in time, so unequal segment
durations do not bend a steady drift. That is a trade, not a cure: a drift
levelling off, the usual shape of an equilibration still under way, sits
further from a straight line in time than in index when the last piece is
long. Counted by the helper (1.2 (1 - exp(-t/0.3)) over the run, 1,000 runs a
row, `--by-index` for the index column), a steady ramp in time over 500, 500
and 4,000 frames is called in 206 runs against 0 (475 against 38 at four
segments), while a drift levelling off over 1,000, 1,000, 1,000 and 5,000
frames is called in 336 against 710, and over 1,000, 1,000 and 6,000 in 126
against 200; a long first piece (6,000, 1,000, 1,000) is called in 35 either
way, and equal pieces (2,000 each) in 80 against 96. In the 4-piece row the
fit in time pools 519 means where index pooled 149, every one qualified as a
lower bound. Neither placing is right for a curved drift, and with one or two
residual degrees of freedom no straight-line rule can be. Where the line fails
the errors' fit, the scatter decides; that does not prove stationarity either:
substantial unexplained scatter continues to qualify the pooled error as a
lower bound. That qualification is said where the means scatter more than
twice what their errors predict, which happens by chance in about 1 of 7
stationary runs of three segments; the sentence that this does not establish
stationarity is added only where Cochran's Q stands against its chi-square on
k - 1 degrees of freedom at 0.05.

**The rule's power at three segments is its known limit.** A real joined run
shows it: a tri-alanine study in TIP3P under a weak thermostat (friction
0.002/ps), its temperature climbing from about 175 K toward 300 K, extended
into pieces of 20 ps (100 state rows each). After three pieces its potential
energy's segment means were -22,360.1 ± 5.8, -22,320.7 ± 5.8 and
-22,258.9 ± 9.6 kJ/mol: one way, 101 kJ/mol overall, over ten times the
largest error. Errors alone (version 4) called that drift (p 1.3e-19). Fitted
against segment index, the steps of 39 and then 62 kJ/mol bend the line, and
with one degree of freedom for the scatter the rule did not (p 0.081). Placed
at their centres in time (frames 64.5, 155 and 274, after discards of 30, 11
and 49 rows), the means lie closer to a line and the rule calls it
(p 0.031); the fourth piece calls it either way (p 0.0037 by index, less than
0.001 by time). The limit stands for means that are not close to a line in
time: three segments give the scatter one degree of freedom, so a real drift
that steps unevenly is not called, and a run of three pieces not called
drifting has not been shown stationary (a real run's temperature at three pieces, 177.8, 185.4 and
186.6 K over errors of 0.6 to 0.7 K, with its thermostat at 300 K, is pooled:
its scatter's p is 0.22). A fourth piece is the stronger test, and more are
better.

A column whose joined summary is withheld because one segment drifts within
itself (`analysis.sampling.still_drifting` across the joins) keeps its own
record: the guard adopts only `analysis.sampling.drifting`, and the
single-series estimate stands, judged over the frames it averages after its
own discard. (In that study, as first built, the temperature's record was
determined from its last 230 frames while its joined summary was withheld for
its first segment; with the means placed in time, its other three segments
are called drifting and the record is withheld.)

Loading a trajectory reads its `.join.json` sidecar and maps the recorded
boundaries through stride and frame selection. The analysis copies preserve
these boundaries. `mean_record(..., joins=...)` uses the segment check as an
additional refusal guard; it does not replace the existing single-series
estimate with a pooled one. Thermodynamic analyses count boundaries after
trimming in each energy column's own samples, independently of trajectory
sampling. Reports read the analyses' saved verdicts, by the refusal's code
each record saves (`withheld_code`): a mean withheld as drifting, between
segments or within its frames, has not equilibrated in the report's
Convergence table, whose finding gives the refusal and its remedy. Existing saved verdicts
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
