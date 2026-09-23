# FastMDXplora refusals

FastMDXplora refuses a great deal, on purpose. A structure that does not
determine what should be simulated, a ligand whose protonation at the study's
pH is not resolved, a trajectory with too few independent samples to support
the claim being asked of it — each of these stops the run and says why, rather
than producing a number that looks like the others.

**Every refusal carries two things: a sentence for a person, and an identifier
for a program.**

On the command line you see the sentence, without a traceback:

```
fastmdx: Unknown setup option 'pH' (did you mean 'ph'?). Valid options: agent, box_shape, …
```

In a program you get both:

```python
from fastmdxplora.config import validate_config, ConfigError
from fastmdxplora import refusal_of

try:
    validate_config({"setup": {"box_shape": "dodecahedran"}})
except ConfigError as exc:
    refusal = refusal_of(exc)

refusal.code        # 'config.option.not_permitted'
refusal.kind        # 'structural'
refusal.permitted   # ('cube', 'dodecahedron', 'octahedron')
refusal.details     # {'option': 'box_shape', 'given': 'dodecahedran',
                    #  'suggestion': 'dodecahedron', …}
str(exc)            # the sentence, unchanged
```

`refusal_of` is **total**. Anything you hand it comes back as a `Refusal` — an
exception from a dependency, or from a raise site that has not been classified
yet, arrives as `unclassified` carrying its own message. So code written
against refusals works today and gains resolution over time rather than
breaking.

---

## Reading a code

Identifiers are dotted, lowercase and hierarchical: `<phase>.<family>.<what>`.
Match one exactly, or match a family:

```python
if refusal.matches("setup.chemistry"):
    ...          # any chemistry refusal from the setup phase
if refusal.matches("setup"):
    ...          # anything the setup phase refused
```

`matches` respects path segments, so `config.opt` does not match
`config.option.unknown`.

```python
from fastmdxplora.refusals import CODES, codes_under

for code in codes_under("analysis.sampling"):
    print(code.id, "--", code.summary)
```

---

## The four kinds

The kind decides **who can fix it**, which is usually the only thing to branch
on.

| Kind | Means | Remedy |
|---|---|---|
| `structural` | The Config says something the schema does not permit, or omits something it requires | Correct the Config and retry |
| `semantic` | The study is well-formed and the system does not determine what to simulate | A decision about the science |
| `environmental` | A backend is missing, or a service did not answer | The machine, not the study |
| `insufficient` | The study is sound and there is not enough data to answer it | More sampling |

`insufficient` is separated from `semantic` because it is the only one of the
four that **more computing can fix**. A caller that can extend a run should be
able to recognise that without reading prose.

`retryable` is true only for transient external failures — a service that did
not answer may answer next time. A missing package is not retryable: something
has to be installed first, which is a change.

---

## What the software will tell you

This is the rule the design turns on:

> **The validator may say what the schema permits. It may never say what the
> chemistry requires.**

A refusal because `box_shape` was misspelled can safely be answered with the
list of shapes. The schema holds that set, it is complete, and there is no
judgement in it. A refusal because a ligand's pKa sits inside the pH margin
cannot be answered at all — the software does not know the answer, and a
suggestion would be a guess wearing the package's authority.

Each code declares which it is:

| Disclosure | The software will give you |
|---|---|
| `permitted_values` | The complete legal set |
| `field_only` | The name of the offending or missing setting, not a value for it |
| `action` | An exact remedy that is not a scientific judgement — an install command, a path, a `curl` |
| `nothing` | Nothing. The software does not know |

The gate is enforced by the registry rather than by the raise site.
`refusal.permitted` returns `None` for any code whose disclosure is not
`permitted_values`, **even if the raise site passed a set**. A raise site cannot
widen a disclosure by accident.

This is also why the [Agent](agent.md) is not handed the fix for a semantic
refusal: what it is told is exactly what you are told.

---

## Refusals in the Manifest

An errored phase records its refusal in `manifest.json` beside the message, so
a reader opening the run afterwards sees what an in-process caller saw:

```json
{
  "name": "setup",
  "status": "error",
  "message": "LIG carries groups whose protonation depends on pH …",
  "refusal": {
    "code": "setup.chemistry.protonation_undetermined",
    "kind": "semantic",
    "disclosure": "nothing",
    "retryable": false,
    "details": {"resname": "LIG", "ph": 7.4, "margin": 1.0}
  }
}
```

The classification travels with the record. A reader a year from now does not
need this version of this module to know that a refusal was environmental and
worth retrying. See [The FastMDXplora Manifest](manifest.md).

```python
runs[0].phase("setup").refusal["code"]
```

---

## Not enough data is a refusal too

The `analysis.sampling.*` family is the one this software is most distinctive
for. A mean over a trajectory is not a measurement until two things are known
about it: whether the system had stopped changing by the time averaging
started, and how many independent observations the average rests on.

```python
from fastmdxplora.statistics import summarise, sampling_shortfall
from fastmdxplora import refusal_of

equilibrated, why = summarise(rmsd_series)
if why is not None:
    refusal_of(why).code
    # 'analysis.sampling.correlation_unresolved'
```

`why` is still a plain string and prints as it always did — it is a `str`
subclass carrying the code alongside.

The companion question is how much further the run would have to go:

```python
print(sampling_shortfall(rmsd_series, target_independent=10, frame_interval_ns=0.01))
# 8.7 independent samples of the 10 needed. At one every 54 frames,
# that is 72 further frames, about 0.72 ns more.
```

Both numbers come from the same statistical inefficiency the refusal used,
measured from **this** series rather than assumed, so the answer is for this
system rather than for a typical one. Two systems with the same trajectory
length can need very different amounts of further sampling for the same claim,
and the difference is not visible in the length.

Where the frames in hand are too few to resolve the correlation time, the
shortfall is a **lower bound**. It is a planning figure: run at least that much
and measure again.

See [Reading the results](results.md#how-a-number-tells-you-what-it-is-worth).

---

## Stability

The identifiers are a **public interface**, versioned with the package.

- Adding a code is a minor change.
- Renaming one goes through `fastmdxplora.refusals.SUPERSEDED`, which
  `resolve()` reads, so a caller written against an old identifier keeps
  working.
- Changing what an existing code *means* is not a minor change and will not be
  done by surprise.

---

## Driving FastMDXplora from a program

Two facts make an automated caller workable, and they are the same two the
[Agent](agent.md) is built on.

**Structural refusals are cheap and complete.** Validation runs before anything
reaches a GPU, so a propose–validate–repair loop costs nothing but time, and
`permitted` plus `suggestion` is usually enough to repair in one pass.

**Semantic refusals are not negotiable by retrying.** A caller that keeps
mutating fields until something validates will eventually produce a Config that
passes and is scientifically wrong — which is the failure this whole mechanism
exists to prevent. Cap the repair attempts, and treat exhausting the cap as a
refusal rather than falling through to whatever last validated.

```python
from fastmdxplora import refusal_of
from fastmdxplora.refusals import StudyError

try:
    runs = study.explore()
except StudyError as exc:
    r = refusal_of(exc)
    if r.kind == "structural" and r.permitted:
        ...          # answerable by reading the Config
    elif r.retryable:
        ...          # a service did not answer; try again
    else:
        raise        # a decision, or the machine
```

If you are **adding** a refusal to FastMDXplora rather than handling one, see
[Developing FastMDXplora](developers.md#raising-a-refusal).

---

## Common refusals

A few you are likely to meet, and what each means.

| Code | What to do |
|---|---|
| `config.option.unknown` | A setting name the schema does not have. The message names the nearest match |
| `config.option.not_permitted` | A value outside a setting's choices. The full legal set is in the message |
| `config.option.out_of_range` | A number the quantity cannot be — a pH of 25, salt at 150 M |
| `config.option.conflicting` | Two settings that cannot both be given — `include` with `exclude`, `forcefield` with `force_field` |
| `config.option.missing_companion` | A setting that needs another one. Most often: no `systems:` list |
| `setup.structure.undetermined` | The structure does not determine what to simulate. Usually a ligand whose chemistry could not be looked up — supply an SDF |
| `setup.chemistry.protonation_undetermined` | A ligand's pKa sits inside the pH margin. A decision about the science |
| `environment.path.exists` | The output directory already holds results. `--force-overwrite` if you mean to |
| `environment.path.not_found` | A named file is not where the run was started from |
| `environment.service.unreachable` | RCSB did not answer. The message carries the exact `curl` to run elsewhere |
| `environment.service.machine_unreachable` | `ssh` could not reach a machine. The message quotes `ssh`'s own reason; try `ssh <name>` in the same terminal |
| `remote.machine.unknown` | A machine that has not been inspected here. `fastmdx remote --machine <name>` first |
| `remote.machine.not_ready` | Nothing on the machine holds this computer's code with its backends loading. `fastmdx remote --machine <name>` says why |
| `environment.calibration.absent` | This machine has not been measured — see [Production runs and GPUs](production.md#knowing-how-long-before-committing-the-card) |
| `environment.budget.exhausted` | An [Agent](agent.md) study was priced above its `--budget-hours` |
| `analysis.unknown` | An analysis name that does not exist. The message names the nearest match and the full list |
| `analysis.option.inapplicable` | A per-analysis option that analysis does not take |
| `analysis.data.absent` | The trajectory or prepared system named is not there. Every candidate path is listed |
| `analysis.sampling.*` | The run is sound and too short. `sampling_shortfall` says by how much |
| `simulation.resume.bias_not_carried` | A metadynamics or steered run cannot be split into segments |
