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
