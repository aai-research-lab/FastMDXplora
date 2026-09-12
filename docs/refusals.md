# Refusals

FastMDXplora refuses a great deal, on purpose. A structure that does not
determine what should be simulated, a ligand whose protonation at the
study's pH is not resolved, a trajectory with too few independent samples
to support the claim being asked of it — each of these stops the run and
says why, rather than producing a number that looks like the others.

Every refusal carries two things: a sentence for a person, and an
identifier for a program.

```python
from fastmdxplora.config.loader import validate_config, ConfigError
from fastmdxplora.refusals import refusal_of

try:
    validate_config({"setup": {"box_shape": "dodecahedran"}})
except ConfigError as exc:
    refusal = refusal_of(exc)

refusal.code        # 'config.option.not_permitted'
refusal.kind        # 'structural'
refusal.permitted   # ('cube', 'dodecahedron', 'octahedron')
refusal.details     # {'option': 'box_shape', 'given': 'dodecahedran',
                    #  'suggestion': 'dodecahedron', ...}
str(exc)            # the sentence, unchanged
```

`refusal_of` is total. An exception from a raise site that has not been
classified yet, or from a dependency, comes back as `unclassified`
carrying its own message, so code written against refusals works today
and gains resolution as the migration proceeds.

## Reading a code

Identifiers are dotted, lowercase, and hierarchical:
`<phase>.<family>.<what>`. Match one exactly, or match a family:

```python
if refusal.matches("setup.chemistry"):
    ...          # any chemistry refusal from the setup phase
```

`matches` respects path segments, so `config.opt` does not match
`config.option.unknown`.

## The four kinds

The kind decides who can fix it, which is the only thing a caller
usually needs to branch on.

| Kind | Means | Remedy |
|---|---|---|
| `structural` | The config says something the schema does not permit, or omits something it requires | Correct the config and retry |
| `semantic` | The study is well-formed and the system does not determine what to simulate | A decision about the science |
| `environmental` | A backend is missing, or a service did not answer | The machine, not the study |
| `insufficient` | The study is sound and there is not enough data to answer it | More sampling |

`insufficient` is separated from `semantic` because it is the only one of
the four that more computing can fix. A caller that can extend a run
should be able to recognise that without reading prose.

`retryable` is true only for transient external failures — a service that
did not answer may answer next time. A missing package is not retryable:
something has to be installed first, which is a change.

## What the software will tell you

This is the part worth understanding, because it is the rule the design
turns on:

> The validator may say what the schema permits. It may never say what
> the chemistry requires.

A refusal because `box_shape` was misspelled can safely be answered with
the list of shapes. The schema holds that set, it is complete, and there
is no judgement in it. A refusal because a ligand's pKa sits inside the
pH margin cannot be answered at all — the software does not know the
answer, and a suggestion would be a guess wearing the package's
authority.

Each code declares which it is, in `disclosure`:

| Disclosure | The software will give you |
|---|---|
| `permitted_values` | The complete legal set |
| `field_only` | The name of the offending or missing setting, not a value for it |
| `action` | An exact remedy that is not a scientific judgement — an install command, a path |
| `nothing` | Nothing. The software does not know |

The gate is enforced by the registry rather than by the raise site.
`refusal.permitted` returns `None` for any code whose disclosure is not
`permitted_values`, **even if the raise site passed a set**. A raise site
cannot widen a disclosure by accident.

## Refusals in the manifest

An errored phase records its refusal in `manifest.json` beside the
message, so a reader opening the run afterwards sees what an in-process
caller saw:

```json
{
  "name": "setup",
  "status": "error",
  "message": "LIG carries groups whose protonation depends on pH ...",
  "refusal": {
    "code": "setup.chemistry.protonation_undetermined",
    "kind": "semantic",
    "disclosure": "nothing",
    "retryable": false,
    "details": {"resname": "LIG", "ph": 7.4, "margin": 1.0}
  }
}
```

The classification travels with the record. A reader a year from now does
not need this version of this module to know that a refusal was
environmental and worth retrying.

## Stability

The identifiers are a public interface. Adding a code is a minor change.
Renaming one goes through `fastmdxplora.refusals.SUPERSEDED`, which
`resolve()` reads, so a caller written against an old identifier keeps
working.

Changing what an existing code *means* is not a minor change and will not
be done by surprise.

## Driving the package from a program

The two facts that make an automated caller workable:

**Structural refusals are cheap and complete.** Validation runs before
anything reaches a GPU, so a propose-validate-repair loop costs nothing
but time, and `permitted` plus `suggestion` is usually enough to repair in
one pass.

**Semantic refusals are not negotiable by retrying.** A caller that keeps
mutating fields until something validates will eventually produce a config
that passes and is scientifically wrong — which is the failure this whole
mechanism exists to prevent. Cap the repair attempts, and treat exhausting
the cap as a refusal rather than falling through to whatever last
validated.

## Not enough data is a refusal too

The `analysis.sampling.*` family is the one this software is most
distinctive for. A mean over a trajectory is not a measurement until two
things are known about it: whether the system had stopped changing by the
time averaging started, and how many independent observations the average
rests on. `summarise` has always withheld a mean it could not stand
behind; the withholding now says which condition it was.

```python
from fastmdxplora.statistics import summarise, sampling_shortfall
from fastmdxplora.refusals import refusal_of

equilibrated, why = summarise(rmsd_series)
if why is not None:
    refusal_of(why).code
    # 'analysis.sampling.correlation_unresolved'
```

`why` is still a plain string and prints as it always did — it is a `str`
subclass that carries the code alongside.

The companion question is how much further the run would have to go:

```python
short = sampling_shortfall(rmsd_series,
                           target_independent=10,
                           frame_interval_ns=0.01)
print(short)
# 8.7 independent samples of the 10 needed. At one every 54 frames,
# that is 72 further frames, about 0.72 ns more.
```

Both numbers come from the same statistical inefficiency the refusal
used, measured from this series rather than assumed, so the answer is for
this system rather than for a typical one. Two systems with the same
trajectory length can need very different amounts of further sampling for
the same claim, and the difference is not visible in the length.

One caveat the function states and this repeats: where the frames in hand
are too few to resolve the correlation time, `g` is an underestimate and
the shortfall is a lower bound. It is a planning figure. Run at least that
much and measure again.

## Which exception to raise

Prefer the specific class where one exists — `LigandError`,
`ProtonationError`, `TrajectoryLoadError` and the rest each say something
a generic class does not.

Where none fits, raise `fastmdxplora.refusals.StudyError`. It subclasses
`ValueError`, so anything catching `ValueError` today keeps catching it,
and unlike a bare `ValueError` it can carry a code.

```python
from fastmdxplora.refusals import StudyError

raise StudyError(
    f"Unknown force field {name!r}. Valid choices: {valid}.",
    code="setup.forcefield.unknown",
    given=name, permitted=sorted(_REGISTRY),
)
```

The prose comes first and stays whole. It is what a person reads, and it
should not be assembled from the details by a formatter that does not know
which particulars matter. The details repeat what the sentence says, for a
reader that is not a person.

## Listing the registry

```python
from fastmdxplora.refusals import CODES, codes_under

for code in codes_under("analysis.sampling"):
    print(code.id, "--", code.summary)
```

## Driving it from a program

`fastmdxplora[agent]` adds a propose-validate-repair loop. A caller
supplies a function taking a prompt and returning text; no model client is
constructed here and no key is handled.

```python
from fastmdxplora.agent import propose_config

result = propose_config("Simulate ubiquitin at pH 7.4 for 10 ns",
                        complete=my_model, phases=["setup", "simulation"])

result.accepted   # True
result.cycles     # 2 — it took one repair
result.config     # the validated study
```

The schema description it proposes from is generated, so it cannot name a
setting validation would refuse. A config that comes back is one that
passed `validate_config`, and a proposal that did not pass carries no
config at all — not the last thing that nearly worked with a caveat
attached.

Three restraints are worth knowing about.

**Structural refusals are retried; semantic ones are not.** A config that
does not match the schema is answerable by reading it. A system that does
not determine its own protonation is not, and a model that retries it is
guessing at the question the software declined to guess at. The loop stops
and returns the refusal.

**Cycles are counted and capped.** The count is a measurement — how many
attempts a model needs is a direct reading of its domain competence,
comparable across models and free to collect. The cap is there because
cheap validation invites thrashing, and a config that validates on the
fortieth mutation validates for reasons nobody chose. Exhausting the cap
is a refusal, not a fall-through.

**The repair prompt withholds.** It names the offending setting and, where
the registry permits, the legal set. It volunteers no remedy beyond that.
A validator that hands over the fix turns every rejection into a
well-specified task, which flatters the measurement and moves the domain
reasoning out of the part being measured.

Nothing in the core package imports `fastmdxplora.agent`, and a test
asserts it. That direction is what makes the design's claim checkable: a
caller bypassing the agent and calling `validate_config` directly is
refused in exactly the same way, by the same code. The agent has no
privileges.

## Knowing how long, before committing the card

The same study is an afternoon on one GPU and a fortnight on another, and
that difference is larger than any difference between two studies. So the
machine is measured once, and estimates come from the measurement.

```python
from fastmdxplora.cost import calibrate, estimate_study

# After any short run: what it was, and what it took.
calibrate(particles=30_000, steps=5_000, seconds=42.0,
          platform_name="CUDA", precision="mixed")

estimate_study(config, particles=62_000,
               platform_name="CUDA", precision="mixed")
# about 5.0 days for 25,000,000 steps on 62,000 particles
```

One number is measured: seconds per particle per integration step.
Molecular dynamics is dominated by the nonbonded calculation, whose cost
is close to linear in particle count, so cost over a run is close to
`k x particles x steps`. `k` is a property of the hardware, the platform
and the precision, not of the protein — which is why one calibration
serves every study on a machine.

It is an approximation and says so. It ignores the PME mesh term's mild
superlinearity, the constraint solver's iteration count, and the fixed
per-step overhead that dominates very small systems. Expect a few tens of
per cent, which is what a scheduling decision needs and not what a paper
would quote.

Three refusals rather than three guesses: `environment.calibration.absent`
where the machine has not been measured, `environment.calibration.stale`
where the measurement came from different hardware or a different
precision, and `setup.structure.undetermined` where the particle count is
not yet known because the system has not been solvated.

A default constant would be a number from somebody else's card, and the
failure it produces is the quiet kind: the schedule looks reasonable and
is wrong by an order of magnitude.

## One card, one line

`fastmdxplora.agent.Queue` is a waiting line backed by a SQLite file, so
it survives the process.

```python
from fastmdxplora.agent import Queue

with Queue("queue.db") as queue:
    queue.set_budget("tau-scaffolds", hours=40)
    queue.submit("tau-scaffolds", "simulate", {"system": "scaffold-3"},
                 estimate_s=estimate.seconds, segments=10)

    job = queue.claim("tau-scaffolds")     # None when the budget is spent
    queue.finish(job.id, seconds=3 * 3600)
```

**Long runs are chained segments.** A hundred nanoseconds goes in as ten
ten-nanosecond jobs, each blocked on the one before. A crash costs one
segment. A caller gets a decision point every few hours. And a study that
has already gone wrong can be abandoned at twenty nanoseconds instead of
at a hundred:

```python
queue.abandon(job.id, "Interface RMSD past 1.5 nm by 30 ns.")
# drops the remaining segments — on one card, those hours are another candidate
```

Abandoned rather than failed, because those segments never ran and nothing
is wrong with them. Marking them failed would put seven failures in a
report where there was one.

**The budget is arithmetic.** A job whose estimate would take the campaign
past its allowance does not start, and the queue records
`environment.budget.exhausted` on the job so a caller reading it tomorrow
sees why the campaign stopped. This is not a number in a prompt asking a
model to be mindful of compute: a model that has been told that, and can
submit jobs, will spend the allowance.

A running job holds its estimate against the budget, so two jobs each
fitting the remainder cannot both start. A campaign with no budget set has
no ceiling — a default allowance would stop somebody's overnight run for a
reason they never chose.

## What may be split, and what may not

Segmenting a long run is the difference, on one card, between finishing
three candidates in a week and finishing one. For two methods it is also a
way of producing a confidently wrong answer.

```python
from fastmdxplora.simulation.resume import segmentability

segmentability({"simulation": {"duration_ns": 100}}).allowed        # True
segmentability({"simulation": {"metadynamics": {...}}}).allowed     # False
```

A checkpoint restores positions and velocities. It does not restore the
biasing state, and for two methods that is the whole calculation.

**Metadynamics** — PLUMED does not re-read `HILLS` unless its script says
`RESTART`. A split run begins the second piece from zero bias with the
system in a well it has already filled, and produces a free energy surface
that is wrong without looking wrong. Nothing downstream can tell: the
surface is smooth, the plot is a plot, and the depth is wrong.

**Steered dynamics** — the moving restraint is placed by absolute step
number, so a resumed piece pulls from an anchor the protein is not at, and
the work integral is taken along a path nothing walked.

**A hand-written PLUMED script** — what state it keeps is not something
this software can read, and guessing permissively is the expensive way to
be wrong.

Two are safe. An unbiased run carries nothing beyond positions and
velocities. An umbrella window's restraint is a function of the collective
variable and not of time, so a window that stops at 4 ns and continues is
doing what it was doing before.

`Queue.submit(..., segments=N)` consults this when the payload carries a
config, so the refusal arrives before the first segment's hours are spent.
Submitting the same study in one piece is always allowed — the refusal is
about splitting, not about the method.

Where a run does resume, `resume_provenance()` records the joins. A
trajectory assembled from pieces is not the same object as one that ran
through: the join is a place where reporters restarted, and an analysis
reading equilibration or correlation across it is reading across a
discontinuity. `ran_through` is stated rather than derived, because that
is the thing an analysis wants to test.

## Running the line

```python
from fastmdxplora.agent import Queue, work

def watch(job, result):
    if result["interface_rmsd_nm"] > 1.5:
        return "the binder has left the epitope"
    return None

report = work(queue, run_one_job, campaign="tau-scaffolds", watch=watch)
# 3 finished, 0 refused, 7 abandoned, 9.0 GPU-hours — the line is empty
```

The worker claims, runs, records and looks again. Its only real decisions
are about stopping.

A refusal from one job does not stop it. One study refusing says nothing
about the next, and a worker that stopped on the first would turn a
campaign of forty candidates into however many came before the first
awkward structure.

`watch` is a callback rather than a rule because what makes a run not
worth continuing is a question about the science, not about queueing.

`stopped_because` distinguishes an empty line from a spent budget from a
job that would not fit — a campaign whose next job was estimated above its
allowance has an empty line and a full budget, and being told the line is
empty sends somebody looking for a job they already submitted.
