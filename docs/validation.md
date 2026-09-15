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

A real run, measured again by implementations that share no code with this one:
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

See [Reproducing a run](config.md#reproducing-a-run) for what that means when
you repeat a study yourself.

---

## The natural-language interface

How well a model actually writes a [Config](config.md) is measured rather than
assumed, with `scripts/measure_nli.py`. The harness, what it scores, and the
result for `claude-sonnet-4-6` are in
[Developing FastMDXplora](developers.md#measuring-the-natural-language-interface).

---

## Pre-registration

`preregistration/` at the repository root holds the thresholds and the claims
each measurement is made against, written down before the measurement. A
tolerance chosen after seeing the answer is not a tolerance.
