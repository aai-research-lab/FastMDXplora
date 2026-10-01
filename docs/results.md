# Reading the results

A finished run is a directory, not a number. This page is the map: what is in
it, what to open first, and how to tell a determined value from something the run
could not support.

---

## What a run leaves behind

```
runs/study/
├── setup/                  prepared and solvated structures, and what was decided
├── simulation/             the trajectory, the energy log, the settings used
├── analysis/               one directory per analysis
├── report/                 the written report, slides, dashboard, bundle
├── manifest.json           what happened
├── resolved_config.yml     what was asked for
└── fastmdxplora.log        the full audit trail
```

Three records answer three different questions, and it is worth telling them
apart:

| | |
|---|---|
| `resolved_config.yml` | **What was asked for.** A valid Config carrying every setting the run used, defaults included. Give it back to `fastmdx explore --config` to run the study again — see [Reproducing a run](config.md#reproducing-a-run) |
| `manifest.json` | **What happened.** Every phase, artifact and refusal, plus the exact software stack the run used — [The FastMDXplora Manifest](manifest.md) |
| `analysis/<name>/options.json` | **What one analysis did.** Its selection, every option including the defaults, the findings, and the format of the file beside it |

**The trajectory holds the solute, not the box.** `simulation.save_selection`
defaults to `not water`, because water is nine tenths of a solvated system and
nine tenths of the file: a 20 ns run of a small protein is about 740 MB with it
and under 80 without. What is saved comes with `trajectory_topology.pdb`, the
topology that matches it, and the whole system stays in `setup/`. Set it to
`all` where the solvent is the subject — the water-site analysis needs it and
says so rather than reporting an empty result.

---

## Open these first

**`report/report.pdf`** — the study written up: a methods paragraph you can
paste into a manuscript, the figures, and a section saying what the run does
and does not support, with the checks it was held to each marked passed,
failed or not judged (and why).

**`analysis/rmsd/rmsd.png`** — the first question about any trajectory: has the
structure equilibrated, or is it still moving?

**`setup/setup_parameters.json`** — what was decided about your structure
before any dynamics ran. Every non-standard residue, every protonation call. If
a result surprises you, the explanation is usually here.

Or open all of it at once:

```bash
fastmdx gui --output runs/study
```

`report/dashboard.html` is the offline equivalent: a self-contained file that
opens in a browser with no server, and travels inside
`report/project_bundle.zip`.

---

## How a number tells you what it is worth

Every analysis reports through the same four registers, and reading them is most
of reading a result.

**A number, plainly.** A mean after equilibration arrives with a standard error and the
number of **independent observations** behind it, not the number of frames,
and with its unit (`unit` in the analysis's `options.json`, as its figure's
axis states it; empty for a count or a fraction).
Saving frames more often makes a file larger without making an estimate
better, so the effective sample count is the figure to read. The correlation
behind it is summed over pairs of lags, so frames that alternate, as a stiff
restraint sampled more slowly than it oscillates makes them, do not read as
independent when something slower moves underneath.

How the error is computed was calibrated on series with a known mean
([pre-registered](https://github.com/aai-research-lab/FastMDXplora/blob/main/preregistration/stopping-calibration.md)),
and three things follow from it. The inefficiency is corrected for being
taken about the sample mean, which makes it read low by about the number of
lags summed over the number of frames. After a start is discarded, the error is
the whole run's, scaled to the frames kept, unless the discard gained at least
twice the independent samples: the start is chosen where the inefficiency of
what remains reads lowest, so an error from what remains reads low with it,
while a real relaxation gains far more than twice. And each error records its
**degrees of freedom** (`degrees_of_freedom`, about the frames over the lags
summed): an error resting on eight of them holds the truth within itself 65%
of the time rather than 68%, and the stopping rule widens it by Student's t
to judge it.

**A number, with a statement of what it does not support.** A finding carrying
`not_a_measurement` is still reported, because the value is often the best
available, and the note says what is wrong with it. The commonest case: a run
too short against its own correlation time cannot resolve how correlated it is,
so the effective-sample count is an upper bound and the true figure is smaller.
A correlated run is taken to resolve it once it holds 25 independent samples
by its own count: shorter than that, the estimate reads low, from a third of
the truth at five times the inefficiency to nine tenths at 25.

**A range instead of a number**, where the run recorded enough to bound a
quantity but not to pin it.

**No number at all**, with the reason. A free energy from a bias that never
converged, a potential of mean force across windows that do not overlap, a
binding free energy from a run that never reached bulk: each is refused by name
rather than reported. A refusal is the most informative thing this software
produces, and it always says what would settle the question.

### Not enough data is a refusal too

A mean over a trajectory is not determined until two things are known about
it: whether the system had stopped changing by the time averaging started, and
how many independent observations the average rests on. Where either is
unresolved, the mean is withheld and the withholding says which condition it
was — `analysis.sampling.correlation_unresolved`,
`analysis.sampling.too_few_frames`, `analysis.sampling.drifting`.

The companion question is how much further the run would have to go, and it has
an answer. An analysis that withholds its mean for want of sampling records it
beside the mean (`shortfall` in its `options.json`: further frames, and
nanoseconds where the run's clock is known). The report's convergence section
gives the figure for the study, the largest of them, rounded up, with what it
would take at the speed the study ran (from `simulation/cost.json`) and the
config that extends the study in place; the Agent is given the same. For any
series:

```python
from fastmdxplora.statistics import sampling_shortfall

print(sampling_shortfall(rmsd_series, target_independent=10, frame_interval_ns=0.01))
# 8.7 independent samples of the 10 needed. At one every 54 frames,
# that is 72 further frames, about 0.72 ns more.
```

One caveat the function states and this repeats: where the frames in hand are
too few to resolve the correlation time, the shortfall is a **lower bound**, and
it is never less than as long again as the part of the run already averaged,
since the independent samples the run appears to hold are themselves an upper
bound. It is a planning figure. Run at least that much and analyse again.

Averages taken on a biased run are corrected back to equilibrium where the bias
allows, and labelled as biased where it does not —
[Averages on a biased run](analyses.md#averages-on-a-biased-run).

---

## Where each kind of result is explained

The detail that changes a number lives with the analysis rather than here, so
there is one place to correct when it changes:

- **What each analysis computes**, and the choices that move it — cutoffs,
  switching functions, alignment sets, atom typing:
  [The FastMDXplora analyses](analyses.md).
- **Protein–ligand interactions**, which carry the most criteria and the most
  ways to disagree with another tool:
  [Protein-ligand interactions](interactions.md).
- **Free energy** from umbrella sampling, metadynamics or steered pulling, and
  the gates each output has to pass:
  [Studies beyond a box of water](studies.md).
- **The file formats themselves.** Each analysis writes its data as `.dat` and
  records in its `options.json` whether that is whitespace with no header or
  comma-separated with one, together with the one-liner that reads it. Read the
  record rather than guessing from the extension.

---

## Comparing runs

A campaign writes `batch_manifest.json` beside the members, a `comparison/`
directory overlaying their series, and `members.json`, which collects what each
member concluded.

```
runs/campaign/
├── batch_manifest.json
├── members.json
├── runs/
│   ├── wild_type/          a complete study directory
│   └── L50A/
└── comparison/
    ├── comparison_report.md
    ├── comparison_summary.csv
    ├── overlay_rmsd.png     also rg, qvalue, sasa where they ran
    └── trend_rmsd.png       only where a numeric sweep axis other than the seed exists
```

Four per-frame quantities are overlaid — RMSD, radius of gyration, the fraction
of native contacts Q, and total SASA — and only where at least two runs
produced them.

Runs are compared on the means their own analyses recorded: over the frames
after equilibration, each with its standard error, and a mean the run said is
not determined is marked as such. The trend plots carry those errors as
bars, and the report calls a difference between the ends of a sweep a trend
only where it is more than twice its error. `comparison_summary.csv` gives
each mean with its standard error, the frames discarded before it and what it
is over. A run whose analyses recorded no mean is given the mean of every
frame, marked so. The overlays are plotted against time where every run
recorded its saving interval, and against the frame otherwise. Replicas get a section of their own, setting the spread of
their means against the error each run estimated (below), and no trend plot
against their seeds.

`members.json` **distinguishes two things that look identical on disk.**
Members differing only by random seed are repeats of one calculation, so the
spread of their means is the **error** on it, and it is set against the error
each run estimated for itself; where the replicas spread much wider, the
single-run estimate missed correlation its run could not see. On ten 20 ns
replicas of one protein and ligand, the spread was about ten times the error
one run estimated, including in runs whose correlation time was resolved, so
replicas are what determines that error, not a check on it. Members differing by system, mutation or parameter are different
calculations, so the spread between them is the **result**. The file says which
it decided and why.

---

## What a joined run needs read differently

A trajectory assembled from segments is contiguous in time and is **not** a
single sample path. Reading it as one loses most of it: a join is a small step
change, and equilibration detection is built to find step changes, so on a
ten-segment run it will often discard everything before one of the later joins.

Checked on ten segments drawn from the same distribution with a small shift at
each join:

| | discard | independent samples | mean | error vs truth |
|---|---|---|---|---|
| naive | 2460 of 4000 | 1383 | 10.375 | 18 standard errors |
| join-aware | per segment | 3732 | 10.225 | under 1 |

The naive number is not merely imprecise. It is confidently wrong, with a
standard error on it that says otherwise.

The report finds the joins itself, so this is handled for you where
FastMDXplora joined the run. Doing it by hand:

```python
from fastmdxplora.analysis.joining import joins_beside
from fastmdxplora.statistics import summarise_segments

pooled, why = summarise_segments(rmsd_series, joins_beside("scaffold-3.dcd"))
```

`joins_beside` returns an empty list for a trajectory that went through in one
piece, so nothing has to branch on whether a run was segmented.

**Drift and scatter are not the same thing.** Segment means that disagree *in
no order* say the per-segment errors are too small; the mean stands and its
error should be read as a lower bound. Segment means that *climb or fall* say
the system had not equilibrated at the scale of the whole run — that is a refusal,
`analysis.sampling.drifting`, and the remedy is a longer run rather than more
pooling.

```
equilibrated  mean=10.005 het=0.7   drift_p=0.30   qualified=False
scattered     mean=10.012 het=376   drift_p=0.69   qualified=True
drifting      REFUSED -> analysis.sampling.drifting  (+1.75 first to last)
```

One consequence worth knowing: any join offset large enough to fool the
equilibration detector is also large enough to exceed what the per-segment
errors predict, so a genuinely segmented run will usually come back **qualified
rather than clean**. That is the honest outcome, not a defect in the test.

Segmenting a run in the first place is in
[Production runs and GPUs](production.md#long-runs-and-segments).

---

## See also

- **[The FastMDXplora Manifest](manifest.md)** — the record of what happened
- **[The FastMDXplora analyses](analyses.md)** — every analysis, and what changes it
- **[FastMDXplora refusals](refusals.md)** — reading a refusal, by hand or from a program
