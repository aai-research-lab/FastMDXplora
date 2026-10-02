# How FastMDXplora is validated

Distinct from the test suite, which **asserts**. This **measures**: rates,
comparisons against independent implementations, and the numbers a paper would
quote. Nothing here decides whether a build is good; it says what the build
does.

Three things are measured, each against a different kind of reference.

---

## The guardrail corpus

`fastmdxplora.validation.corpus`

Every refusal this software makes is a claim that a study should not proceed.
That claim is worth measuring, so there are two corpora of cases:

- **`DEFECTS`** — studies that *should* be stopped. How many are?
- **`CLEAN`** — studies that should proceed untouched. How many do?

Both are needed, because **a checker that refused every study would score
perfectly on sensitivity alone.** Measuring only defects rewards
obstructiveness; measuring only clean cases rewards permissiveness.

```python
from fastmdxplora.validation.corpus import run_corpus
report = run_corpus()
```

Each case carries what it is, what it expects, **why** it expects that, and
what the message should mention:

```python
Case(name=..., run=..., expect=..., because=..., mentioning=...)
```

### Three outcomes, not two

`proceeded`, `refused`, and **`qualified`**.

A qualification is not a refusal. Counting it as one would make the software
look more obstructive than it is; counting it as a clean pass would make it
look less careful. The constant-pressure segment case is the canonical example
— see
[Long runs and segments](production.md#constant-pressure-is-a-qualification-not-a-refusal).

The corpus runs no dynamics, completes in seconds, and runs every release.

---

## Against other tools

`fastmdxplora.validation.cross_tool`

A real run, analysed again by implementations that share no code with this one:
**ProLIF** and **MDAnalysis**. Neither is a package dependency; they are
installed separately, with the `validation` extra, precisely so that the
comparison is between two stacks rather than inside one.

```bash
python -m fastmdxplora.validation.cross_tool interactions runs/study
python -m fastmdxplora.validation.cross_tool observables  runs/study
python -m fastmdxplora.validation.cross_tool negative     runs/study
```

| Subcommand | What it compares |
|---|---|
| `interactions` | Protein–ligand interactions against ProLIF |
| `observables` | Geometric observables against MDAnalysis |
| `negative` | That a system with no interactions reports none |

**The tolerances are pre-registered** — decided before any comparison was run:
5.0 percentage points on a harmonised occupancy, 10⁻³ nm on an observable.
Deciding a tolerance after seeing the disagreement is how a comparison becomes
a formality.

**Artifacts are discovered through the run's own
[Manifest](manifest.md)**, not through guessed filenames: the provenance index
is the contract. That matters most for a run that happened on a cluster, where
the paths in the record are that machine's.

What the checking found is in
[Protein-ligand interactions: implementation](interactions_design.md). Against
ProLIF the partners agree exactly, and the counts agree once the threshold and
counting differences are accounted for; MDTraj's `baker_hubbard` agrees on the
protein-internal hydrogen bonds. **Neither difference from ProLIF is a defect;
both are settings.**

---

## Across environments

`fastmdxplora.validation.environments`

One Config, run in a container, in a conda environment and from a PyPI wheel,
compared field by field.

| What is compared | Must match |
|---|---|
| `resolved_config.yml` | **Exactly** |
| The input structure's SHA-256, from `setup/setup_parameters.json` | **Exactly** |
| The results | **No** |

The last row is the point. Solvation places water from a generator that is not
fixed by the dynamics seed, so two preparations of the same molecule have
different atom counts and the trajectories differ. **Saying so is part of the
comparison** — an environment check that demanded identical results would either
fail always or be measuring something other than what it claims.

The first row is exact because [`resolved_config.yml` names every setting the
run used](config.md#reproducing-a-run), defaults included. A default that
resolves differently on another machine is what this check exists to catch, and
it can only be caught in a file that writes the defaults down. Keys that say
where a run happened rather than what it was — output paths, platform, device
index, thread count — are reported separately and are not a difference in the
study. Comparing runs from two **versions** is a real comparison, not a false
alarm: if a default moved between them, the studies differ.

See [Reproducing a run](config.md#reproducing-a-run) for what that means when
you repeat a study yourself.

---

## The natural-language interface

How well an AI model actually writes a [Config](config.md) is evaluated rather than
assumed, with `scripts/measure_nli.py`. The harness, what it scores, and the
result for `claude-sonnet-4-6` are in
[Developing FastMDXplora](developers.md#evaluating-the-natural-language-interface).

---

## Errors against replicas

A mean's error is calibrated two ways. On series with a known mean, the
stopping rule's calibration counts how often the stated error holds the
truth ([Developing FastMDXplora](developers.md#calibrating-the-stopping-rule)).
On a real system, replicas give the error a second way, as the spread of
their means, and each run's stated error should predict it:

```bash
python -m fastmdxplora.validation.replica_calibration <campaign folder> --out calibration.json
```

For every analysis that wrote a series of its frames in every replica, it
gives the spread of the means against the mean stated error three ways: as
the runs recorded them, computed again by this release from each run's own
start, and from the start the replicas share. One is honest; with ten
replicas the spread is itself uncertain by about a quarter, so a ratio
between one half and two cannot be told from one. It writes nothing in the
campaign.

---

## Pre-registration

`preregistration/` at the repository root holds the thresholds and the claims
each measurement is made against, written down before the measurement. A
tolerance chosen after seeing the answer is not a tolerance.
