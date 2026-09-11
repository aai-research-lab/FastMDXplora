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

## Listing the registry

```python
from fastmdxplora.refusals import CODES, codes_under

for code in codes_under("analysis.sampling"):
    print(code.id, "--", code.summary)
```
