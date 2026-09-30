# The Agent's looking: pre-registered

**Written 2026-09-30, before any reply from a model to these questions was
seen.** The harness is `fastmdxplora.validation.agent_looks`; this file fixes
what it asks, how a reply is judged and what is claimed from the counts.

## The questions

Each has an answer the software computes with no model, by the functions its
tools call.

| Name | Asked | The software's answer | Tool | Tolerance |
|---|---|---|---|---|
| `particles_1l2y` | the solvated size of 1L2Y, default settings | setup's estimate of the particles | `preview_setup` | 10% |
| `box_1ake` | the width of 1AKE's box, face to face, in nm | setup's estimate | `preview_setup` | 5% |
| `residues_1ubq` | 1UBQ's protein residues | the file's count | `inspect_structure` | exact |
| `ligands_1ake` | 1AKE's ligands by residue name | every one the file gives | `inspect_structure` | every name |
| `selection_1l2y` | atoms matched by `name CA and resSeq 1 to 10` in 1L2Y | MDTraj's count | `check_selection` | exact |
| `histidines_1hho` | histidines in the system setup builds from 1HHO | the assembly's count | `inspect_structure` | exact |

The tolerances are the software's own resolution: the size estimate is within
3% of what setup builds on the structures it was checked on (1099), so 10%
separates a number read from the estimate from one guessed; a twentieth of
1AKE's box width is about a tenth of the default padding. Counts are exact.

## How a reply is judged

- Only an answer is judged. A config, a question back, a refusal or a failure
  is recorded as what it is and counted as not agreeing.
- For a number: the number stated nearest the software's is taken, and the
  reply agrees when it is within the tolerance. Digits inside a name (1L2Y)
  or an exponent (nm^3) are not numbers; small numbers written as words are.
- For names: the reply agrees when it names every one, each as a whole word.
- **Looked** is whether, in the arm with tools, the model used the tool the
  question calls for before its answer.

Both rules are lenient to a reply that states several numbers or names more
than asked. The replies are kept whole so that can be read.

## What is claimed

The counts per arm and per question, as counted: agreed with tools, agreed
without, looked. No threshold is set for "the tools help"; with six questions
and three repeats a difference of one or two replies is not a finding and
will not be reported as one. What would be reported is a question where the
arm with tools agrees in every repeat and the arm without in none, or the
reverse, and the model's looking rate, which is a property of the model and
the prompt, not of the tools.

What is not measured: whether the software's answers are right (that is
setup's own validation), or anything about configs the Agent writes (that is
`scripts/measure_nli.py`).
