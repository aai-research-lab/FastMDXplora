# The stopping rule's stated precision: pre-registered

**Written 2026-10-01, before any coverage was computed.** The harness is
`fastmdxplora.validation.stopping_calibration`; this file fixes what it
simulates, what it counts and what is claimed from the counts.

## The question

A study with `simulation.stop_when` runs in pieces and stops at the first
look where each quantity's error is within the target. An error that
happens to be read low at that look is the one reported, so stopping on the
data can bias the stated precision low. Two things are asked: whether a
study that stops reports an error that covers the truth as often as an
error should, and how much stopping on the data changes that relative to
the same estimator at a length fixed in advance.

The series are made with a known true mean, recorded as every analysis
records a per-frame series (`statistics.mean_record`: equilibration
detected, the mean of what follows, its error from the statistical
inefficiency, or the mean withheld with how much longer would give one), and
judged and extended by the rule's own loop
(`simulation.stopping.run_until_known`). Nothing of the rule is
reimplemented. Frames are 10 ps apart.

## The cases

| Case | Series | Runs | Target | First piece | Ceiling | Studies |
|---|---|---|---|---|---|---|
| `fast_one_run` | AR(1), mean 1.0, sd 0.05, phi 0.95 (inefficiency 39 frames) | 1 | ±0.01 | 2 ns | 100 ns | 1000 |
| `fast_three_replicas` | the same | 3 | ±0.01 | 2 ns | 100 ns | 1000 |
| `slow_one_run` | AR(1), mean 1.0, sd 0.05, phi 0.995 (inefficiency 399 frames, 4 ns) | 1 | ±0.01 | 5 ns | 400 ns | 500 |
| `slow_three_replicas` | the same | 3 | ±0.01 | 5 ns | 200 ns | 500 |
| `transient_three_replicas` | the fast series starting 0.15 (3 sd) away, relaxing over 300 frames | 3 | ±0.01 | 2 ns | 100 ns | 1000 |
| `two_states_one_start` | two states at 1.0 and 1.2, each held 2000 frames (20 ns) on average, AR(1) noise sd 0.03, phi 0.95; true mean 1.1; every replica starts in the first state | 3 | ±0.01 | 5 ns | 200 ns | 500 |
| `two_states_drawn_starts` | the same, each replica starting in a state drawn at random | 3 | ±0.01 | 5 ns | 200 ns | 500 |

One run is judged with `independent_starts: not_required`, three as
replicas, as the rule does by default. The ceilings are about ten times
the production the stationary cases need to meet their targets.

Under the rule, the check that withholds a mean whose run does not resolve
its own correlation time is counted on its own: the fast and the slow
series at 1.25, 5, 12.5, 50, 100 and 250 times their inefficiency, 500
series at each, counting how many are said unresolved, how many withheld
for any reason, and how often a mean that is given has the truth within one
and two of its errors.

**Seeds.** Study `i` of case number `c` (the order above, from 0) is made
from `SeedSequence([c, i, 0])`, and its fixed-length comparison from
`SeedSequence([c, i, 1])`. The registered set is `i` from 0 to the number
of studies less one. A held-out set, used only as described below, is the
next block of the same size. The withholding counts use
`SeedSequence([100 + s, i, 100 * multiple])` for series `s`.

## What is counted

Per case: how each study ended (determined as asked, at the ceiling, or
otherwise); the median production per run of those determined; among those
determined, the share whose reported value lies within one and within two
of its reported errors of the truth, the root mean square of
`(value - truth) / error`, and its mean; and the share outside two errors
(a precision stated that the truth does not support). For comparison, the
same estimate from runs of one length fixed in advance, equal to that
median, with the same number of runs.

## What is claimed

- **The stated precision holds** for a case if, among the studies
  determined, the truth lies within one reported error in at least the
  nominal 68.3% less four binomial standard errors, and within two in at
  least 95.4% less four (at 1000 studies, 62.4% and 92.8%; the floors are
  computed from the number counted).
- **The rule is usable** on a stationary case (`fast`, `slow`,
  `transient`) if at least 90% of its studies end determined before the
  ceiling.
- **Stopping on the data** is the difference in coverage between the
  studies and their fixed-length comparisons, reported as found, with no
  threshold.
- **The two-state cases** have no threshold. What is reported is how often
  a study is determined with the truth outside two of its errors: a run
  trapped in one state looks determined, which the rule's documentation
  already says replicas from one structure can test only as far as their
  dynamics carry them apart. Drawn starts are the comparison.
- **The withholding check** is reported as counted. A stationary series of
  50 or more times its inefficiency that is withheld as unresolved is a
  withholding the check should not make, and a series of 1.25 or 5 times
  its inefficiency whose mean is given is one it should have made.

## If a claim fails

Any change to the rule or to the estimator under it is chosen by looking at
the registered set only, and is adopted only if the same claims hold for it
on the held-out set. Both results are reported, the failure first. A claim
that fails is not re-tested on new seeds until it passes.

## Disclosed before registration

Three studies of each case, with indices from 1,000,000 (outside both
sets), were run to check the harness runs and to time it. Their outcomes
and lengths were seen; no coverage was computed. They showed means withheld
as unresolved at 100 ns in replicas of the fast series, and a single run of
the slow series determined at 5 ns. The withholding counts above were added
to this registration because of them, and the cases were not changed.

## Result on the registered set

**Counted 2026-10-01**, as registered: the seven cases at their registered
numbers of studies from index 0, and the withholding count, with the harness
as committed with this file. Every study and every count is in
[`stopping-calibration-registered.json`](stopping-calibration-registered.json).
**No claim holds.**

### The stated precision

| Case | Determined | At the ceiling | Median production | Within 1 error | Within 2 | Floors | Holds | Fixed length, within 1 / 2 |
|---|---|---|---|---|---|---|---|---|
| `fast_one_run` | 1000 of 1000 (100.0%) | 0 | 6.6 ns | 42.7% | 70.9% | 62.4%, 92.8% | no | 52.9% / 77.8% (446 given at 6.6 ns) |
| `fast_three_replicas` | 797 of 1000 (79.7%) | 203 | 32.0 ns | 64.4% | 91.6% | 61.7%, 92.5% | no | 69.5% / 94.6% (295 given at 32.0 ns) |
| `slow_one_run` | 497 of 500 (99.4%) | 3 | 23.5 ns | 31.0% | 51.1% | 59.9%, 91.7% | no | 33.6% / 56.6% (152 given at 23.5 ns) |
| `slow_three_replicas` | 168 of 500 (33.6%) | 332 | 150.4 ns | 64.9% | 86.3% | 53.9%, 89.0% | no | 61.3% / 91.4% (93 given at 150.4 ns) |
| `transient_three_replicas` | 748 of 1000 (74.8%) | 252 | 47.7 ns | 48.9% | 79.0% | 61.5%, 92.4% | no | 59.0% / 89.5% (344 given at 47.7 ns) |
| `two_states_one_start` | 37 of 500 (7.4%) | 463 | 20.0 ns | 0.0% | 0.0% | 37.7%, 81.8% | no | 54.4% / 68.4% (57 given at 20.0 ns) |
| `two_states_drawn_starts` | 23 of 500 (4.6%) | 477 | 23.0 ns | 0.0% | 0.0% | 29.4%, 78.1% | no | 52.7% / 69.1% (55 given at 23.0 ns) |

The fixed-length column counts only the comparisons whose mean was given;
the rest were withheld.

- **A single run reports an error about half the size the truth needs.**
  `fast_one_run` stopped at a median of 6.6 ns, 17 times its inefficiency,
  with the truth within one reported error in 42.7% of studies and within
  two in 70.9%. `slow_one_run`: 31.0% and 51.1%. The root mean square of
  `(value - truth) / error`, which is 1 for an honest error, was 2.5 and 5.5.
- **Stopping on the data makes it worse; the estimator under-covers without
  it.** At the median length fixed in advance the same estimator gave 52.9%
  and 77.8% (fast) and 33.6% and 56.6% (slow).
- **Replicas come closest, and the rule is not usable on them.**
  `fast_three_replicas` held within one error (64.4%) and not within two
  (91.6% against 92.5%), and 203 of 1000 studies reached the 100 ns ceiling,
  where the rule is usable only if 90% end determined. `slow_three_replicas`:
  332 of 500 at the ceiling. A study of three replicas is held whenever any
  one of them has its mean withheld, and the check below withholds at random.
- **The transient leaves a bias.** `transient_three_replicas`: 48.9% and
  79.0%, with a mean `(value - truth) / error` of +1.05. The start each run
  discards leaves part of the relaxation in its mean, and three replicas from
  one start carry the same part, so their agreement cannot reveal it. 252 of
  1000 reached the ceiling.
- **Two states.** A study trapped in one state looked determined in 37 of 500
  started together (7.4%) and 23 of 500 with drawn starts (4.6%), and in every
  one of them the truth was outside two errors. The rest reached the ceiling
  with their replicas disagreeing, which is the rule declining as it should.
  Drawn starts lowered the rate little: with two states, all three replicas
  begin in the same one a quarter of the time.

### The check under the rule

| Series | Length (x inefficiency) | Said unresolved | Withheld | Given within 1 / 2 errors |
|---|---|---|---|---|
| fast | 1.25 | 431 of 500 | 442 | 6.9% / 13.8% (58 given) |
| fast | 5 | 337 of 500 | 373 | 30.7% / 52.8% (127 given) |
| fast | 12.5 | 292 of 500 | 293 | 48.3% / 78.7% (207 given) |
| fast | 50 | 194 of 500 | 194 | 55.2% / 85.6% (306 given) |
| fast | 100 | 148 of 500 | 148 | 63.6% / 93.2% (352 given) |
| fast | 250 | 101 of 500 | 101 | 69.7% / 95.2% (399 given) |
| slow | 1.25 | 375 of 500 | 447 | 5.7% / 15.1% (53 given) |
| slow | 5 | 336 of 500 | 367 | 29.3% / 51.1% (133 given) |
| slow | 12.5 | 285 of 500 | 288 | 44.3% / 72.6% (212 given) |
| slow | 50 | 205 of 500 | 205 | 60.0% / 87.5% (295 given) |
| slow | 100 | 150 of 500 | 150 | 70.3% / 94.6% (350 given) |
| slow | 250 | 94 of 500 | 94 | 69.7% / 94.8% (406 given) |

- **Long stationary series are withheld.** At 50 to 250 times its
  inefficiency a series was said unresolved 19% to 41% of the time; none
  should be.
- **Short series are given.** At 1.25 times its inefficiency 11% to 12% of
  means were given, with the truth within their errors 6% to 7% of the time;
  all should be withheld. At 5 times, a quarter to a third were given, and
  29% to 31% of those had the truth within one error.
- Halving a series moves its estimated inefficiency by more than the 15% the
  check allows through noise alone, until the series is far longer than any
  run here. So the check withholds at random, and the means it gives are
  those whose whole-series inefficiency happened to read low against its
  half: the ones whose errors are too small.

### Why, as examined after the count

Examined after the counts, to choose a remedy, on series made from the
registered seeds and from seeds outside both sets. None of this is a
registered claim.

1. **The start discarded is chosen where the error reads small.** Detecting
   equilibration keeps the start that gives the most independent samples,
   which is where the inefficiency of what remains reads lowest. On
   stationary series 25 times their inefficiency (600 series) it discarded a
   start in 43%; the truth was within one and two errors in 68% and 92% with
   no discard, 60% and 86% with it, and 66% and 92% for the mean after the
   discard with the error of the whole run scaled to the frames kept. The
   mean is not what goes wrong; its error is.
2. **The halving check**, as counted above.
3. **The inefficiency reads low by the sample mean's own fluctuation.** An
   autocorrelation taken about the sample mean is low by about `g/N` at every
   lag, and summed over the `M` lags kept that lowers the inefficiency by a
   factor of about `(1 - (2M+1)/N) / (1 - g/N)`.
4. **An error from one run has few degrees of freedom**, about `N/(2M+1)`:
   a median of eight at 25 times the inefficiency, half of such series
   between six and twelve. With eight the truth lies within one error 65% of
   the time and within two 92%, not 68% and 95%.
5. **Stopping at the first look whose error is within the target** keeps the
   looks where the error reads low.

## The remedy, chosen on the registered set

Chosen after the counts above by looking at the registered set only, as this
registration requires, and committed with its count here before the held-out
set was run. Five changes, in `statistics.py` and `simulation/stopping.py`:

1. **The inefficiency is corrected for the sample mean**, by the factor in
   cause 3 above.
2. **After a discard, the error is the whole run's**, scaled to the frames
   kept, unless discarding gained at least twice the independent samples
   (cause 1). A real relaxation gains far more than twice; on stationary
   series 25 to 100 times their inefficiency, noise alone gained twice in
   under 4% of them.
3. **The start discarded is the latest that keeps within a tenth of the most
   independent samples** any start keeps, not the start that keeps the most,
   which leaves the most of a relaxation in the mean. It costs at most a
   tenth of the samples.
4. **A correlated series resolves its correlation time at 25 independent
   samples** by its own count (cause 2), replacing the halving check.
5. **The rule widens each run's error by Student's t** at the error's own
   degrees of freedom, `N/(2M+1)`, recorded with every mean (cause 4), **and
   judges a single run alone only once its mean rests on 50 independent
   samples** (cause 5). Replicas need no such floor; their spread checks what
   each claims.

Tried on the registered set and not taken: confirming a met target at the
next look (it raised a single run's coverage less than judging it on 50
independent samples did, and sent more replica studies to the ceiling); a test
of the first half of what was kept against the second, pooled over replicas
(it barely moved the transient: a bias the size of the error is the size of
the test's own noise); requiring 50 independent samples of every analysis
(replicas then ran 39 ns where 26 sufficed); and a start within a fifth
rather than a tenth (the transient's best, 66.9% and 91.4%, but slow single
runs reached the ceiling in 13% of studies).

### Counted on the registered set

Every study is in
[`stopping-calibration-remedy-registered.json`](stopping-calibration-remedy-registered.json).

| Case | Determined | At the ceiling | Median production | Within 1 error | Within 2 | Floors | Holds | Fixed length, within 1 / 2 |
|---|---|---|---|---|---|---|---|---|
| `fast_one_run` | 1000 of 1000 (100.0%) | 0 | 25.0 ns | 65.2% | 92.9% | 62.4%, 92.8% | yes | 69.5% / 94.9% (928 given at 25.0 ns) |
| `fast_three_replicas` | 990 of 1000 (99.0%) | 10 | 26.1 ns | 69.8% | 96.7% | 62.4%, 92.8% | yes | 73.8% / 96.7% (839 given at 26.1 ns) |
| `slow_one_run` | 461 of 500 (92.2%) | 39 | 258.4 ns | 66.2% | 90.7% | 59.6%, 91.6% | no | 68.9% / 94.9% (473 given at 258.4 ns) |
| `slow_three_replicas` | 294 of 500 (58.8%) | 206 | 200.0 ns | 66.7% | 94.2% | 57.4%, 90.6% | yes | 72.7% / 97.0% (333 given at 200.0 ns) |
| `transient_three_replicas` | 987 of 1000 (98.7%) | 13 | 31.9 ns | 61.2% | 88.5% | 62.3%, 92.8% | no | 66.4% / 93.9% (724 given at 31.9 ns) |
| `two_states_one_start` | 54 of 500 (10.8%) | 446 | 24.3 ns | 0.0% | 0.0% | 42.9%, 84.1% | no | 59.8% / 68.5% (127 given at 24.3 ns) |
| `two_states_drawn_starts` | 43 of 500 (8.6%) | 457 | 24.3 ns | 0.0% | 0.0% | 39.9%, 82.7% | no | 62.3% / 72.1% (122 given at 24.3 ns) |

| Series | Length (x inefficiency) | Said unresolved | Withheld | Given within 1 / 2 errors |
|---|---|---|---|---|
| fast | 1.25 | 415 of 500 | 455 | 4.4% / 13.3% (45 given) |
| fast | 5 | 499 of 500 | 499 | 0.0% / 0.0% (1 given) |
| fast | 12.5 | 468 of 500 | 468 | 37.5% / 65.6% (32 given) |
| fast | 50 | 72 of 500 | 72 | 61.7% / 91.1% (428 given) |
| fast | 100 | 6 of 500 | 6 | 66.0% / 94.3% (494 given) |
| fast | 250 | 0 of 500 | 0 | 69.4% / 96.4% (500 given) |
| slow | 1.25 | 499 of 500 | 499 | 0.0% / 0.0% (1 given) |
| slow | 5 | 498 of 500 | 498 | 0.0% / 50.0% (2 given) |
| slow | 12.5 | 475 of 500 | 475 | 36.0% / 60.0% (25 given) |
| slow | 50 | 79 of 500 | 79 | 63.9% / 90.5% (421 given) |
| slow | 100 | 3 of 500 | 3 | 70.6% / 95.4% (497 given) |
| slow | 250 | 0 of 500 | 0 | 71.6% / 96.8% (500 given) |

- **The stated precision holds** for `fast_one_run`, `fast_three_replicas` and
  `slow_three_replicas`. It does not for `slow_one_run` within two errors
  (90.7% against 91.6%), nor for `transient_three_replicas` (61.2% and 88.5%,
  against 62.3% and 92.8%), whose mean `(value - truth) / error` fell from
  +1.05 to +0.43 and is not gone.
- **The rule is usable** on `fast_three_replicas` (99.0%), `slow_one_run`
  (92.2%) and `transient_three_replicas` (98.7%), and not on
  `slow_three_replicas` (58.8%): each replica now needs about 25 times its
  4 ns inefficiency, 100 ns, before its mean is given, and the ceiling is
  200 ns.
- **The cost is length.** A single fast run stopped at a median of 25 ns
  rather than 6.6 ns, since one run is judged alone only on 50 independent
  samples; the replicas at 26 ns rather than 32, since none is withheld at
  random.
- **Two states.** Studies trapped in one state looked determined more often:
  54 of 500 started together (10.8%, from 7.4%) and 43 of 500 with drawn
  starts (8.6%, from 4.6%), every one with the truth outside two errors. The
  random withholding had blocked some of these stops by accident; what tests
  trapping is independent starts, and three replicas from one structure, or
  from two states drawn at random, are too few.
- **The check.** At 100 and 250 times its inefficiency a stationary series is
  withheld in at most 1.2% of cases, from 19% to 30%; at 50 times, in 14% to
  16%, from 39% to 41%. At 1.25 and 5 times, 49 of 2000 means are given, from
  371.

### What the held-out set is to show

Written before it was counted. The held-out set is study indices 1000 to
1999 (500 to 999 for the cases of 500 studies) and 500 to 999 for the check,
run with the code committed with this section. The remedy is adopted if each
claim that holds for it above holds there too: the stated precision for
`fast_one_run`, `fast_three_replicas` and `slow_three_replicas`, and
usability for `fast_three_replicas`, `slow_one_run` and
`transient_three_replicas`. The claims that fail above are not expected to
hold there and do not decide it. Both results are reported, whichever way
they go.

## Result on the held-out set

**Counted 2026-10-01**, after the section above was committed, with the same
code: study indices 1000 to 1999 (500 to 999 for the cases of 500 studies) and
500 to 999 for the check. Every study is in
[`stopping-calibration-heldout.json`](stopping-calibration-heldout.json).
**Every claim the remedy was to show holds, so it is adopted.**

What fails, first:

- **The transient's precision does not hold**, as above: 60.3% within one
  error and 87.3% within two, a mean `(value - truth) / error` of +0.47.
- **Slow replicas reach the ceiling** in 218 of 500 studies (56.4% end
  determined).
- **Two states.** 67 of 500 studies started together (13.4%) and 45 of 500
  with drawn starts (9.0%) looked determined while trapped, every one with the
  truth outside two errors.
- **The check** still withholds 13% to 14% of stationary series 50 times their
  inefficiency, and gives 53 of 2000 means at 1.25 and 5 times.

| Case | Determined | At the ceiling | Median production | Within 1 error | Within 2 | Floors | Holds | Fixed length, within 1 / 2 |
|---|---|---|---|---|---|---|---|---|
| `fast_one_run` | 1000 of 1000 (100.0%) | 0 | 25.2 ns | 65.4% | 93.5% | 62.4%, 92.8% | yes | 67.2% / 94.7% (935 given at 25.2 ns) |
| `fast_three_replicas` | 990 of 1000 (99.0%) | 10 | 26.6 ns | 68.3% | 96.4% | 62.4%, 92.8% | yes | 70.7% / 94.9% (840 given at 26.6 ns) |
| `slow_one_run` | 467 of 500 (93.4%) | 33 | 242.8 ns | 65.5% | 94.4% | 59.7%, 91.6% | yes | 67.9% / 95.8% (449 given at 242.8 ns) |
| `slow_three_replicas` | 282 of 500 (56.4%) | 218 | 200.0 ns | 69.2% | 94.7% | 57.2%, 90.5% | yes | 69.0% / 96.0% (326 given at 200.0 ns) |
| `transient_three_replicas` | 985 of 1000 (98.5%) | 15 | 31.2 ns | 60.3% | 87.3% | 62.3%, 92.8% | no | 65.6% / 94.4% (695 given at 31.2 ns) |
| `two_states_one_start` | 67 of 500 (13.4%) | 433 | 19.5 ns | 0.0% | 0.0% | 45.5%, 85.3% | no | 61.5% / 72.6% (135 given at 19.5 ns) |
| `two_states_drawn_starts` | 45 of 500 (9.0%) | 455 | 22.8 ns | 0.0% | 0.0% | 40.5%, 83.0% | no | 71.3% / 80.9% (136 given at 22.8 ns) |

| Series | Length (x inefficiency) | Said unresolved | Withheld | Given within 1 / 2 errors |
|---|---|---|---|---|
| fast | 1.25 | 421 of 500 | 453 | 6.4% / 10.6% (47 given) |
| fast | 5 | 495 of 500 | 495 | 20.0% / 20.0% (5 given) |
| fast | 12.5 | 474 of 500 | 474 | 26.9% / 50.0% (26 given) |
| fast | 50 | 66 of 500 | 66 | 64.3% / 93.3% (434 given) |
| fast | 100 | 6 of 500 | 6 | 68.4% / 95.3% (494 given) |
| fast | 250 | 0 of 500 | 0 | 66.8% / 94.4% (500 given) |
| slow | 1.25 | 499 of 500 | 499 | 0.0% / 0.0% (1 given) |
| slow | 5 | 500 of 500 | 500 | n/a / n/a (0 given) |
| slow | 12.5 | 473 of 500 | 473 | 40.7% / 63.0% (27 given) |
| slow | 50 | 69 of 500 | 69 | 64.0% / 93.5% (431 given) |
| slow | 100 | 10 of 500 | 10 | 66.9% / 94.7% (490 given) |
| slow | 250 | 0 of 500 | 0 | 67.4% / 95.4% (500 given) |

What holds:

- **The stated precision** for `fast_one_run` (65.4% and 93.5%),
  `fast_three_replicas` (68.3% and 96.4%) and `slow_three_replicas` (69.2% and
  94.7%), and also for `slow_one_run` (65.5% and 94.4%), which narrowly missed
  within two errors on the registered set.
- **Usability** for `fast_three_replicas` (99.0%), `slow_one_run` (93.4%) and
  `transient_three_replicas` (98.5%).
- The root mean square of `(value - truth) / error` was 1.00 to 1.09 for the
  four stationary cases, from 1.13 to 5.49 as registered.

## A second remedy: the start replicas share

What the held-out set left failing above, a relaxation shared by replicas
from one start, was examined on the registered set after it was counted. Each
run's own equilibration detection sees the relaxation through that run's
noise and keeps a little of it, and three replicas keep the same little.
Chosen on the registered set: **before replicas are judged, the start is
found again on their frame-by-frame average** (`statistics.shared_start`),
where the relaxation is the same and the noise smaller by the square root of
the replicas, and **each run's mean is taken from the later of its own start
and that one** (`simulation.stopping.share_the_start`, from the series each
analysis writes beside its record; the harness now writes it too). A single
run is unchanged.

### Counted on the registered set

Single runs are as above and were not counted again. Every replica study is
in
[`stopping-calibration-shared-start-registered.json`](stopping-calibration-shared-start-registered.json).

| Case | Determined | At the ceiling | Median production | Within 1 error | Within 2 | Floors | Holds | Fixed length, within 1 / 2 |
|---|---|---|---|---|---|---|---|---|
| `fast_three_replicas` | 990 of 1000 (99.0%) | 10 | 27.4 ns | 70.7% | 96.1% | 62.4%, 92.8% | yes | 73.5% / 97.4% (821 given at 27.4 ns) |
| `slow_three_replicas` | 272 of 500 (54.4%) | 228 | 200.0 ns | 68.0% | 93.8% | 57.0%, 90.4% | yes | 71.6% / 97.6% (292 given at 200.0 ns) |
| `transient_three_replicas` | 988 of 1000 (98.8%) | 12 | 44.2 ns | 65.1% | 93.0% | 62.4%, 92.8% | yes | 72.5% / 97.3% (746 given at 44.2 ns) |
| `two_states_one_start` | 36 of 500 (7.2%) | 464 | 20.6 ns | 0.0% | 0.0% | 37.2%, 81.6% | no | 57.8% / 62.5% (64 given at 20.6 ns) |
| `two_states_drawn_starts` | 21 of 500 (4.2%) | 479 | 25.4 ns | 0.0% | 0.0% | 27.6%, 77.3% | no | 65.7% / 75.7% (70 given at 25.4 ns) |

- **The transient now holds**: 65.1% within one error and 93.0% within two,
  from 61.2% and 88.5%, and the mean `(value - truth) / error` fell from
  +0.43 to +0.13. It costs length: a median of 44 ns per replica, from 32.
- **Two states.** Studies trapped in one state looked determined less often:
  36 of 500 started together (7.2%, from 10.8%) and 21 of 500 with drawn
  starts (4.2%, from 8.6%).
- Fast and slow replicas still hold. Slow replicas reach the ceiling a
  little more often (228 of 500, from 206).

### What the second held-out set is to show

Written before it was counted. Neither set above was used to choose this, but
the first held-out set has been read, so the shared start is counted on a
second: study indices 2000 to 2999 for the cases of 1000 studies and 1000 to
1499 for those of 500, the replica cases only, with the code committed with
this section. It is adopted if the stated precision holds there for
`fast_three_replicas`, `slow_three_replicas` and `transient_three_replicas`,
and the rule is usable there on `fast_three_replicas` and
`transient_three_replicas`. Both results are reported, whichever way they go.
