# Production runs and GPUs

Everything on the other pages works on a laptop. This one is about the runs
that do not.

---

## Where a production run belongs

A real trajectory is hours to days of continuous computation. Two things
follow.

**Run it on a GPU.** OpenMM on a modern GPU is one to two orders of magnitude
faster than on a CPU. `--simulate-platform CUDA` asks for one; the default is
`auto`, which takes the fastest available and says which it chose.

**Run it somewhere it will not be interrupted.** A closed laptop lid or a
disconnected SSH session ends the run. Use a scheduler, or `nohup`, or `tmux`.

---

## Before committing hours to it

```bash
fastmdx info
```

Confirms the simulation backends are present.

```bash
fastmdx explore --system your.pdb --output runs/smoke \
  --simulate-nvt-steps 500 \
  --simulate-npt-steps 500 \
  --simulate-production-steps 5000
```

Ten picoseconds through every phase. It proves the structure prepares, the
system builds, the platform works and the report writes — which is everything
except the length. A setup problem found after six hours of production is the
same problem found in two minutes.

Then read `setup/setup_parameters.json` and check what the setup phase decided
about your structure. That is where a wrong answer is cheapest to catch.

---

## Knowing how long, before committing the card

The same study is an afternoon on one GPU and a fortnight on another, and that
difference is larger than any difference between two studies. So the machine is
measured once, and estimates come from the measurement.

```python
from fastmdxplora.cost import measure_this_machine, estimate_study

measure_this_machine()
# k = 5.0e-07 s per particle-step, from 2000 steps on 3000 particles in 3.0s

estimate_study(config, particles=62_000, platform_name="CUDA", precision="mixed")
# about 5.0 days for 25,000,000 steps on 62,000 particles
```

**One number is measured: seconds per particle per integration step.** Molecular
dynamics is dominated by the nonbonded calculation, whose cost is close to
linear in particle count, so cost over a run is close to `k × particles ×
steps`. `k` is a property of the hardware, the platform and the precision, not
of the protein — which is why one calibration serves every study on a machine.

It is an approximation and says so. It ignores the PME mesh term's mild
superlinearity, the constraint solver's iteration count, and the fixed per-step
overhead that dominates very small systems. **Expect a few tens of per cent**,
which is what a scheduling decision needs and not what a paper would quote.

`measure_this_machine()` runs argon in a periodic box with a cutoff and no
water: the cheapest thing that exercises the nonbonded calculation the model is
built on. It warms up before timing — the first steps pay for kernel
compilation and buffer allocation, and charging them to the constant would
overstate every estimate afterwards, on a GPU by a great deal. And it records
the platform it *actually ran on* rather than the one that was asked for, since
a constant labelled CUDA that was measured on CPU would understate a real CUDA
run enormously.

The calibration is stored beside the AI model choice, in
`$XDG_CONFIG_HOME/fastmdxplora/calibration.json`.

**A budget measures the machine itself when it has to.** A study given
`--budget-hours` is priced for the platform the simulation phase will choose
(`simulation.platform`, with `auto` resolved as that phase resolves it) and
the config's precision. Where the machine has no measurement for that
platform, or one taken under other settings, the study's own prepared system
is timed there, just after setup: a brief minimisation, then 2,000 steps at
1 fs after a warm-up. That constant covers PME and the study's own force
field, which argon leaves out, and it is stored for the next study. A new
container, a fresh cloud GPU or a new cluster node therefore prices its first
study instead of refusing it. Only when that timing cannot be made either
does the study stop with `environment.calibration.absent`.

### Three refusals rather than three guesses

| Code | Means |
|---|---|
| `environment.calibration.absent` | This machine has not been measured |
| `environment.calibration.stale` | The measurement came from different hardware, platform or precision |
| `setup.structure.undetermined` | The particle count is not yet known, because the system has not been solvated |

A default constant would be a number from somebody else's card, and the failure
it produces is the quiet kind: the schedule looks reasonable and is wrong by an
order of magnitude.

### The machine learns from what it has run

Argon is a bootstrap. A machine that has run real studies knows more about
itself than that — every finished run writes `simulation/cost.json` beside its
output, holding particles, steps, seconds, platform and precision.

```python
from fastmdxplora.cost import calibrate_from_runs

fit = calibrate_from_runs("runs", platform_name="CUDA", precision="mixed")
fit.runs      # 4
fit.spread    # 1.01 — the runs agree
```

The fit does something a single point cannot: it says whether the cost model's
assumption holds **here at all**. Seconds are taken to go as particles times
steps, and if that fails on some hardware — a mesh term dominating differently,
occupancy changing sharply with system size — the per-run constants will not
agree.

```
environment.calibration.inconsistent
  The 5 runs under runs/ disagree about what a particle-step costs by a
  factor of 16.1. … Averaging through it would give a confident constant
  for a relationship that is not there.
```

Refused rather than averaged, at a generous threshold of a factor of three,
because refusing a usable fit sends somebody back to argon — which is worse
information.

Runs from another platform or precision are excluded rather than averaged in: a
mixed-precision GPU run and a double-precision CPU run have genuinely different
constants, and a mean of the two describes neither. The **median** is used
rather than the mean, because a run that swapped or shared the card is slow by
an arbitrary amount while nothing makes a run anomalously fast.

---

## Launching one

```bash
nohup fastmdx explore \
  --config study.yml \
  --output /scratch/$USER/runs/my_run \
  --simulate-platform CUDA \
  > /scratch/$USER/runs/my_run.log 2>&1 &
```

Under a scheduler, the same command goes in the job script — see
[Running FastMDXplora elsewhere](clusters.md#batch-schedulers).

Put the output on fast local storage rather than a network filesystem: the
trajectory writer touches the file constantly, and NFS makes that the
bottleneck.

---

## Watching it

The log says what is happening. For anything more, point the GUI at the output
directory:

```bash
fastmdx gui --output /scratch/$USER/runs/my_run
```

That works while the run is in progress — telemetry, energies, and the molecule
moving as frames arrive.

From your own machine against a cluster, keep the directory in sync:

```bash
rsync -az --delete user@cluster:/scratch/$USER/runs/my_run/ ~/runs/my_run/
fastmdx gui --output ~/runs/my_run
```

Re-run the `rsync` as often as you like.

---

## Choosing a length

The honest answer is that it depends on what you are asking, and the report
will tell you whether you got there. Its convergence section reports how many
**independent** observations the trajectory holds — far fewer than the frame
count, because consecutive frames are nearly the same structure.

A useful pattern: run something, read the convergence section, and extend if it
says the observables have not equilibrated or rest on too little. That is more reliable
than choosing a number in advance.

`sampling_shortfall` turns that into a figure — see
[Reading the results](results.md#not-enough-data-is-a-refusal-too).

### Running until it is determined

The pattern above can be the study's own. `simulation.stop_when` says what the
study is for and how well it must be known, and a ceiling; the study then runs
in pieces, reads its analyses after each, and extends every run by what the
numbers say is still needed:

```yaml
systems:
  - {system: 1UAO}
sweep:
  simulation.random_seed: [1, 2, 3]
simulation:
  duration_ns: 5                 # the first piece
  stop_when:
    measures:
      - {analysis: rmsd, standard_error: 0.01}
      - {analysis: sasa, relative_error: 0.05}
    max_duration_ns: 50
```

After each piece:

- **A mean the analysis withheld** (too few independent samples, or not
  equilibrated) asks for its own shortfall, where the analysis worked one out,
  and otherwise for as long again.
- **A mean with an error** asks for enough production that its error falls to
  what was asked: the error of a mean goes as one over the root of the frames
  after equilibration, so twice the error allowed asks for four times those
  frames. The error judged is the standard error widened by Student's t at
  its own degrees of freedom, so that it holds the truth as often as one
  standard error should.
- **Replicas average from the start they share.** Replicas over a seed begin
  in one structure and share its relaxation, which each run's own
  equilibration detection sees through that run's noise. Before replicas are
  judged, the start is found again on their frame-by-frame average, and each
  run's mean is taken from the later of its own start and that one; its
  record says so (`start_shared_with_replicas`).
- **One run is judged alone** only once its mean rests on 50 independent
  samples, and until then asks for the production that would give them. An
  error estimated from one run is itself uncertain, and stopping where it first
  falls within the target keeps the looks where it read low; replicas need no
  such floor, since their spread checks what each claims.
- **Every run is extended by the most any quantity asks** (side by side in a
  parallel study, as its runs were), at least a quarter and at most three
  times what has run so far, in whole frames, and never past
  `max_duration_ns`. The bounds are there because an error estimated from a
  short run is itself uncertain.

**Why replicas are required.** A run can look equilibrated while trapped: if the molecule
never visits a second state, its average stops moving and its error bar
shrinks, and it is still wrong. Block averaging and the count of independent
samples say how precise one run is; only runs started independently can show
that it is trapped. So by default the study must be one system swept over
`random_seed`, and the rule is met only when the replicas' means agree within
their own errors (their scatter no more than twice what those errors predict).
The error judged is then the larger of what the runs claim together and what
their spread shows. Replicas that each look precise and disagree are extended,
not accepted.

Replicas over a seed share one structure, so they test trapping only as far
as their dynamics carry them apart. Where the structure is an ensemble (an NMR
entry's models), sweep `setup.model` instead, and each replica starts from a
different structure the data allow; the rule counts them as independent starts
the same way:

```yaml
sweep:
  setup.model: [1, 8, 15]
```

`independent_starts: not_required` accepts one run's own precision, and the
record says that it was not checked against independent starts. Replicas that
differ only by seed start from one structure (each prepared with a setup seed
of its own unless `setup.random_seed` is given), so
they test trapping only as far as their dynamics carry them apart; runs from
different starting structures, or replica exchange, test it further.

**What comes out.** `stopping.json` beside the study records every round: the
production, each quantity with its error and what was asked, whether the
replicas agreed, and what was decided. The report (for one run) or the
comparison report (for replicas) has a section, *How long it ran, and why*,
built from it, and the Agent is given it. A budget prices the study at its
ceiling, since that is what it may spend.

**How far to trust the precision it states.** Stopping as soon as an error
falls below a target favours a round whose error came out small by chance.
The rule was calibrated on series with a known mean
([pre-registered](https://github.com/aai-research-lab/FastMDXplora/blob/main/preregistration/stopping-calibration.md)),
and as first written its stated precision did not hold: a single run that
stopped reported an error about half the size the truth needed, the truth
within one error in 43% of studies where an honest error gives 68%. With the
error's estimate corrected, the start discarded chosen with the relaxation in
mind, an unresolved correlation judged on 25 independent samples, and the
rule's two judgements above, single runs and replicas of stationary series
held the truth within one error in 65% to 70% of studies and within two in
91% to 97%, on the set these were chosen on and again on a held-out set.
With replicas averaged from the start they share, so did replicas sharing a
relaxation from their one structure (68.6% and 94.3% on a second held-out
set). What is not in the error is a state no replica left: studies trapped in
one of two slowly exchanging states looked determined in 3% to 6%. Replicas
from different structures, or replica exchange, test that.

The rule needs a study that can be run in pieces (not metadynamics, steered
or PLUMED runs), is not applied to umbrella windows, and is refused before
anything runs where the study cannot keep it.

---

## Several runs at once

```yaml
execution:
  mode: parallel
  workers: 2
  devices: [0, 1]         # one run pinned per GPU
  continue_on_error: true
```

Each worker takes a share of the cores rather than all of them, so the runs
divide the machine instead of fighting over it. Parallelism is process-based
and mandatory — OpenMM contexts and the GIL do not share across threads.

For most systems `workers` should not exceed the number of GPUs: two large runs
sharing one device are slower than the same two in sequence. **Umbrella windows
are the exception** — a window is a small system and rarely keeps a large GPU
busy, so three or four on one card is usually worth doing. See
[Umbrella sampling](studies.md#umbrella-sampling).

`continue_on_error` is what you want overnight: one system failing does not end
the campaign, and the failures are in the [Manifest](manifest.md). It defaults
to `false` for an umbrella study, because a free energy cannot be computed at
all if a window is missing.

---

## Long runs and segments

A hundred nanoseconds run in one piece is a hundred nanoseconds you cannot
interrupt, cannot reschedule, and lose entirely to a crash at ninety. Splitting
it into segments is, on one card, the difference between finishing three
candidates in a week and finishing one.

**For two methods it is also a way of producing a confidently wrong answer.**

```python
from fastmdxplora.simulation.resume import segmentability

segmentability({"simulation": {"duration_ns": 100}}).allowed        # True
segmentability({"simulation": {"metadynamics": {...}}}).allowed     # False
```

A checkpoint restores positions and velocities. It does not restore the biasing
state, and for two methods that is the whole calculation.

| Method | Splittable | Why not |
|---|---|---|
| Plain MD | **Yes** | Nothing beyond positions and velocities |
| Umbrella | **Yes** | The restraint is a function of the coordinate, not of time |
| Metadynamics | **No** | PLUMED does not re-read `HILLS` without `RESTART`. A split run begins the second piece from zero bias with the system in a well it has already filled, and produces a free energy surface that is wrong without looking wrong |
| Steered | **No** | The moving restraint is placed by absolute step number, so a resumed piece pulls from an anchor the protein is not at, and the work integral is taken along a path nothing walked |
| A hand-written PLUMED script | **No** | What state it keeps is not something this software can read, and guessing permissively is the expensive way to be wrong |

Submitting the same study in one piece is always allowed — the refusal is about
splitting, not about the method.

### Constant pressure is a qualification, not a refusal

Measured on the CPU platform with argon in a periodic box, because the answer
differs between constant volume and constant pressure.

A **constant-volume** run resumed from a checkpoint at the same thread count
reproduces the run it continued to within about 1×10⁻⁹ nm over fifty steps.
Positions, velocities and box vectors all come back exactly. At another thread
count the thermostat draws different noise, so the resumed run continues the
ensemble rather than the trajectory.

A **constant-pressure** run does not, and seeding the barostat does not fix it.
The Monte Carlo barostat's adaptive volume-move size is not in the checkpoint
and is not a Context parameter, so it restarts at its default and re-adapts over
the moves after each join.

```python
segmentability({"simulation": {"duration_ns": 100}})
# .allowed        True
# .qualification  "The barostat's adaptive move size is not carried by a
#                  checkpoint, so it restarts at its default and re-adapts
#                  after each join…"
```

Constant pressure means the ensemble production runs in, decided as the runner
decides it: a study that names no `ensemble` is NPT and is qualified, and one
with `ensemble: nvt` is not, even with a `pressure_bar` in its config — that
pressure is the equilibration's.

That is a **qualification**. The state the second piece starts from is
physically right and the trajectory it produces is a valid sample of the same
ensemble. It is simply not the trajectory an unsplit run would have produced,
and the barostat's acceptance rate is off for a while at each join.

Refusing would refuse constant pressure, which is most work anybody does.
Saying nothing would leave a volume artefact for somebody to find. So the
qualification travels into the run's own provenance, and volume or density
averaged across a join should discard a window on either side —
`provenance["joins"]` records where they are.

### The checkpoint seal

OpenMM will not tell you a checkpoint is truncated. Measured: one cut to half
its length loads without complaint and gives the right positions; cut to a
tenth it loads without complaint and gives wrong ones. There is no length or
checksum in the format.

So every checkpoint is written with `checkpoint.chk.sha256` beside it, holding
the size and digest: **a way to detect truncation**. It says the file is whole,
which is as true of a checkpoint written partway through a run as of the last
one, so the checkpoint a killed run leaves is one that can be resumed from.

Whether the run **got to the end** is a different fact, and the checkpoint's
sidecar (`checkpoint.chk.json`) records it: `finished` is false for the
checkpoints written along the way and true for the one written at a clean
finish. A join reads it to know which segments were killed, and leaves out the
frames a killed segment wrote after its last checkpoint, since the resume runs
them again.

Required for segments, where the predecessor was written by this software and
its checkpoints are always sealed. Not required otherwise — refusing a
hand-made checkpoint would be refusing a legitimate use over a convention
nobody agreed to.

### Putting a segmented run back together

Each segment writes to its own directory. Appending into one file would leave a
crashed segment's half-written frames in the middle of the run's output with no
way to tell which were good, so joining is a separate, explicit step and the
joined trajectory is a derived artefact.

```python
from fastmdxplora.analysis.joining import survey_segments, join_segments

survey_segments("runs/tau/scaffold-3")                    # what is on disk
join_segments("runs/tau/scaffold-3", "scaffold-3.dcd")    # produce the file
```

`survey_segments` is separate so that somebody returning to an overnight run
can see the state without committing to producing a file.

Concatenation is four lines. The refusals are the module:

- **A gap** is the failure that most looks like success — segments two and four
  concatenate perfectly with three missing, and what comes out is not a shorter
  trajectory but one with a jump in the middle. Equilibration detection would
  find a transient that is really a discontinuity, and a correlation time
  computed across it means nothing.
- **An unfinished segment**, one whose checkpoint sidecar does not say it
  finished. Its trajectory ends wherever the process died and nothing in the
  file says so.
- **Segments from two studies** look alike on disk and concatenate without
  complaint. Each segment's resolved Config says which study it was, and the
  settings that vary between segments by design — production steps, minimize,
  `resume_from` — are excluded before comparing.

**A joined run has to be read differently** from one that went through in a
single piece. See
[What a joined run needs read differently](results.md#what-a-joined-run-needs-read-differently).

**After an extension the study's config is still the study's.** The join's
analyses run from the last piece's config, which names that piece's
checkpoint, its own length and no equilibration. The study's
`resolved_config.yml` is put back as it was, with `duration_ns` the production
of every piece, so `fastmdx diff`, the All studies comparison and a study run
again from the file read the study and not its last extension. Analysing the
study again in place reads `joined/production.dcd`, found from the study's own
folder rather than from a path in the config, and only while it is newer than
the first piece's `simulation/production.dcd`.

---

## Running a campaign with a budget

For an unattended campaign — many candidates, one card, a fixed allowance —
`fastmdxplora.agent` carries a queue. It has nothing to do with an AI
model; it lives there because
[`--autonomous`](agent.md#autonomous--draft-it-and-run-it) needs something that
can stop a run.

```python
from fastmdxplora.agent import Queue

with Queue("queue.db") as queue:
    queue.set_budget("tau-scaffolds", hours=40)
    queue.submit("tau-scaffolds", "simulate", {"system": "scaffold-3"},
                 estimate_s=estimate.seconds, segments=10)

    job = queue.claim("tau-scaffolds")     # None when the budget is spent
    queue.finish(job.id, seconds=3 * 3600)
```

It is backed by a SQLite file, so it survives the process.

**Long runs go in as chained segments.** A hundred nanoseconds goes in as ten
ten-nanosecond jobs, each blocked on the one before. A crash costs one segment.
You get a decision point every few hours. And a study that has already gone
wrong can be abandoned at twenty nanoseconds instead of at a hundred:

```python
queue.abandon(job.id, "Interface RMSD past 1.5 nm by 30 ns.")
```

Abandoned rather than failed, because those segments never ran and nothing is
wrong with them. Marking them failed would put seven failures in a report where
there was one.

`submit(..., segments=N)` consults `segmentability` when the payload carries a
Config, so a metadynamics study's refusal arrives before the first segment's
hours are spent.

**The budget is arithmetic.** A job whose estimate would take the campaign past
its allowance does not start, and the queue records
`environment.budget.exhausted` on it so a caller reading tomorrow sees why the
campaign stopped. A running job holds its estimate against the budget, so two
jobs each fitting the remainder cannot both start. A campaign with no budget
set has no ceiling — a default allowance would stop somebody's overnight run
for a reason they never chose.

### Running the line

```python
from fastmdxplora.agent import Queue, work, study_runner, join_finished

def watch(job, result):
    if result["interface_rmsd_nm"] > 1.5:
        return "the binder has left the epitope"
    return None

report = work(queue, study_runner("runs", queue=queue),
              campaign="tau-scaffolds", watch=watch)
# 3 finished, 0 refused, 7 abandoned, 9.0 GPU-hours — the line is empty
```

The worker claims, runs, records and looks again. Its only real decisions are
about stopping.

**A refusal from one job does not stop it.** One study refusing says nothing
about the next, and a worker that stopped on the first would turn a campaign of
forty candidates into however many came before the first awkward structure.

`watch` is a callback rather than a rule, because what makes a run not worth
continuing is a question about the science rather than about queueing.

`report.stopped_because` distinguishes an empty line from a spent budget from a
job that would not fit — a campaign whose next job was estimated above its
allowance has an empty line *and* a full budget, and being told the line is
empty sends somebody looking for a job they already submitted.

**Joining is offered, not done.** The worker reports what became ready while it
ran:

```python
report.ready_to_join      # ['scaffold-3']
join_finished(queue, "tau-scaffolds", "runs")
# {'joined': {...}, 'refused': {...}, 'already_whole': ['scaffold-7']}
```

Offered rather than automatic, because joining reads every frame of every
segment and somebody who has just spent a week of GPU time may reasonably want
to look first. A study that ran in one piece is skipped, a study with an
abandoned segment is not offered at all, and a study whose join refuses is
recorded with its refusal rather than stopping the rest.

---

## When it stops early

Every run writes checkpoints during production, each sealed as it is written
and placed on a frame. A run that is killed, whether by a machine restarting, a
job reaching its time limit or a rented GPU taken back, leaves its last one
behind.

**A run asked to stop ends on a frame.** SIGTERM, which a scheduler, a cloud
provider, a container platform and the GUI's Stop button all send, and Ctrl-C
at a terminal, are noted during production rather than obeyed at once. The run
steps on to its next frame, writes a checkpoint there, and ends; `fastmdx
resume` carries it on from that frame, so nothing after the last interval
checkpoint is run twice and the joined trajectory keeps its spacing. It does
so only when the next frame can be reached within 20 seconds, which leaves
time inside the 30 or so a scheduler allows before it kills; otherwise the last
interval checkpoint stands. `FASTMDX_STOP_GRACE_SECONDS` sets the 20 for a
platform that allows more or less. A second signal is obeyed at once. A
second SIGTERM within two seconds of the first is the same request arriving
twice, as it does when sent to a whole process group, and counts once; a
second Ctrl-C, however quick, means now.

**A study of several runs stops as one.** Running its runs in parallel, the
study passes SIGTERM on to each of them (Ctrl-C reaches them from the terminal
itself): a run in production ends on a frame as
above, and one in setup, equilibration or its analyses ends with the same
retryable refusal, so the phase it was in runs again on resume. No further run
starts, and those not started say so. The GUI's Stop, which signals the study
alone, reaches every run this way. Run one at a time, a study stopped in one
run does not start the next.

**`fastmdx resume` carries the study on to the end of its plan.** It reads how
far the study got and does what is left, once:

```bash
fastmdx resume runs/study
```

| What the study holds | What is run |
|---|---|
| Every phase of its plan recorded as done | Nothing |
| All of production, and not its analyses or report | The analyses and the report, over the whole trajectory |
| Part of production | The rest of it, from the last sealed checkpoint, then the join and the analyses, as [extending a study](#long-runs-and-segments) does |
| No production yet | The whole study again: setup and equilibration leave nothing a run can continue from |

**A study of several runs is carried on run by run.** A sweep, several systems
or an umbrella study's windows: each run that started is carried on as above,
each that never started is run, they share out the workers and devices the
study asked for, and the aggregate, an umbrella study's free energy and the
comparison across runs are rebuilt once every run has its answer. A structure
the study named by a relative path is found beside the study's folder, so the
resume need not be run from where the study was. The study says, when it
stops, the one command that carries all of it on.

It refuses a folder that is not a study, and a study any run of which is still
running. A study that stopped with a
refusal is not run again, since that was its answer; one the refusal registry
marks as worth retrying, such as a GPU that went away, counts as an
interruption. Production that is written and cannot be continued, a checkpoint
off the frame grid for instance, is not thrown away by starting again: the
reason is said instead. Running `fastmdx resume` twice does the work once, so a
service can run it every time a job restarts; `--json` prints the outcome as
one line for a program to read.

**By hand, recovery is at phase boundaries.** A finished setup or trajectory in a
directory is reused rather than redone, and `--include` picks up from the phase
that stopped:

```bash
fastmdx explore --config study.yml --output runs/study --include analysis report
```

Within the simulation phase, `simulation.resume_from` continues from a
checkpoint — valid only for the exact system, platform and precision it was
written from, and refusing rather than proceeding on a mismatch. That is the
mechanism segments are built on, and the table above says which methods may use
it.

Naming a study directory instead continues that study in place: its next
segment runs inside it, every segment is joined, and the analyses and report
are rerun over the whole. `duration_ns` is then the total production the study
should end with, and `extra_ns` an amount more. The same config does this from
the command line, from Python and from the GUI:

```python
from fastmdxplora import FastMDXplora

FastMDXplora(config_data={"simulation": {"resume_from": "runs/study",
                                         "extra_ns": 5}}).explore()
```

If the run became unstable rather than being killed, the message says which
atoms went wrong and what that points at — see
[When a run fails](studies.md#when-a-run-fails). Nothing is retried
automatically.

**The log keeps what happened, including the attempts that failed.**
`fastmdxplora.log` in the run directory is appended to rather than replaced, so
a second attempt does not erase the first — which is usually the one saying why
the second was needed. Each invocation is separated by a banner naming the time
and the version that made it, so a directory re-run over weeks reads as a
sequence rather than a wall.

That is worth knowing when a directory has been re-run with
`--force-overwrite`: the other artefacts were replaced and the log was not, so
it describes runs whose outputs are no longer there. The banners are what tell
you which part of it is about the files you are looking at.

---

## See also

- **[Running FastMDXplora elsewhere](clusters.md)** — clusters, containers, offline installs
- **[Studies beyond a box of water](studies.md)** — what each method's output is and is not
- **[Reading the results](results.md)** — including how to read a joined run
- **[Worked examples](examples.md)** — campaigns and comparisons
