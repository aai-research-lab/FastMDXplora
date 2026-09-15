# Reading the results

A finished run is a directory, not a number. This page is the map: what is in
it, what to open first, and how to tell a measurement from something the run
could not support.

---

## What a run leaves behind

```
runs/study/
├── setup/                  prepared and solvated structures, and what was decided
├── simulation/             the trajectory, the energy log, the settings used
├── analysis/               one directory per measure
├── report/                 the written report, slides, dashboard, bundle
├── manifest.json           what happened
├── resolved_config.yml     what was asked for
└── fastmdxplora.log        the full audit trail
```

Three records answer three different questions, and it is worth telling them
apart:

| | |
|---|---|
| `resolved_config.yml` | **What was asked for.** A valid Config. Give it back to `fastmdx explore --config` to run the study again. It is meant to carry every setting the run used, defaults included; today it carries only what was explicitly set — see [Reproducing a run](config.md#reproducing-a-run) |
| `manifest.json` | **What happened.** Every phase, artifact and refusal, plus the exact software stack the run used — [The FastMDXplora Manifest](manifest.md) |
| `analysis/<name>/options.json` | **What one measure did.** Its selection, every option including the defaults, the findings, and the format of the file beside it |

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
and does not support.

**`analysis/rmsd/rmsd.png`** — the first question about any trajectory: has the
structure settled, or is it still moving?

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

Every measure reports through the same four registers, and reading them is most
of reading a result.

**A number, plainly.** A settled mean arrives with a standard error and the
number of **independent observations** behind it, not the number of frames.
Saving frames more often makes a file larger without making a measurement
better, so the effective sample count is the figure to read.

**A number, with a statement of what it does not support.** A finding carrying
`not_a_measurement` is still reported, because the value is often the best
available, and the note says what is wrong with it. The commonest case: a run
too short against its own correlation time cannot measure how correlated it is,
so the effective-sample count is an upper bound and the true figure is smaller.

**A range instead of a number**, where the run recorded enough to bound a
quantity but not to pin it.

**No number at all**, with the reason. A free energy from a bias that never
settled, a potential of mean force across windows that do not overlap, a
binding free energy from a run that never reached bulk: each is refused by name
rather than drawn. A refusal is the most informative thing this software
produces, and it always says what would settle the question.

### Not enough data is a refusal too

A mean over a trajectory is not a measurement until two things are known about
it: whether the system had stopped changing by the time averaging started, and
how many independent observations the average rests on. Where either is
unresolved, the mean is withheld and the withholding says which condition it
was — `analysis.sampling.correlation_unresolved`,
`analysis.sampling.too_few_frames`, `analysis.sampling.drifting`.

The companion question is how much further the run would have to go, and it has
an answer:

```python
from fastmdxplora.statistics import sampling_shortfall

print(sampling_shortfall(rmsd_series, target_independent=10, frame_interval_ns=0.01))
# 8.7 independent samples of the 10 needed. At one every 54 frames,
# that is 72 further frames, about 0.72 ns more.
```

One caveat the function states and this repeats: where the frames in hand are
too few to resolve the correlation time, the shortfall is a **lower bound**. It
is a planning figure. Run at least that much and measure again.

Averages taken on a biased run are corrected back to equilibrium where the bias
allows, and labelled as biased where it does not —
[Averages on a biased run](analyses.md#averages-on-a-biased-run).

---

## Where each kind of result is explained

The detail that changes a number lives with the measure rather than here, so
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
    └── trend_rmsd.png       only where a numeric sweep axis exists
```

Four per-frame measures are overlaid — RMSD, radius of gyration, the fraction
of native contacts Q, and total SASA — and only where at least two runs
produced them.

`members.json` **distinguishes two things that look identical on disk.**
Members differing only by random seed are repeats of one measurement, so the
spread of their means is the **error** on it, and it is set against the error
each run estimated for itself; where the replicas spread much wider, the
single-run estimate was reading a correlation time off a trajectory too short
to contain it. Members differing by system, mutation or parameter are different
measurements, so the spread between them is the **result**. The file says which
it decided and why.

---

## What a joined run needs read differently

A trajectory assembled from segments is contiguous in time and is **not** a
single sample path. Reading it as one loses most of it: a join is a small step
change, and equilibration detection is built to find step changes, so on a
ten-segment run it will often discard everything before one of the later joins.

Measured on ten segments drawn from the same distribution with a small shift at
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
the system had not settled at the scale of the whole run — that is a refusal,
`analysis.sampling.drifting`, and the remedy is a longer run rather than more
pooling.

```
settled    mean=10.005 het=0.7   drift_p=0.30   qualified=False
scattered  mean=10.012 het=376   drift_p=0.69   qualified=True
drifting   REFUSED -> analysis.sampling.drifting  (+1.75 first to last)
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
- **[The FastMDXplora analyses](analyses.md)** — every measure, and what changes it
- **[FastMDXplora refusals](refusals.md)** — reading a refusal, by hand or from a program
