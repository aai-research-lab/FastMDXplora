# The Agent's looking, again: pre-registered

**Written 2026-10-02, before any reply from an AI model to these questions was
seen.** The harness is `fastmdxplora.validation.agent_looks_v2`; this file
fixes what it asks, how a reply is judged and what is claimed from the counts.
It does not revise `agent-looks.md`, whose result stands as reported.

## Why a second registration

The first asked about well-known entries at their default settings. Every
reply agreed with the software in both arms, because an AI model knows those
entries, so the counts could not show whether looking helps. Its rule also
took the number nearest the software's from a reply, which counted a range
headed 4,000 as agreeing with 3,305. Two changes answer both:

1. **Questions memory cannot answer.** Four structure files are made from
   Protein Data Bank entries at the start of a run, and the settings asked
   about are not the defaults.
2. **A reply is judged on the answer it commits to.** Each request asks for
   a closing line `ANSWER: ` with the number alone or the names alone, and
   only that line is judged.

## The files

Made by `prepare()` from the entries as the PDB serves them, with MDTraj:

| File | Made from |
|---|---|
| `trimmed.pdb` | 1L2Y, first model, protein residues 1 to 12 |
| `renumbered.pdb` | 1UBQ, protein residues 10 to 60, renumbered 201 to 251 |
| `kinase.pdb` | 1AKE, chain A and its AP5, the ligand renamed LG7, no water |
| `alpha.pdb` | 1HHO, chain A, protein only |

The settings asked about are a dodecahedron box with 1.6 nm of padding and
0.3 M NaCl for `trimmed.pdb`, and a dodecahedron with 2.0 nm and 0.5 M for
1UBQ.

## The questions

Each has an answer the software computes with no AI model, by the functions its
tools call. The answers below were computed on 2026-10-02, before any reply
was seen; the harness computes them again at each run and records them.

| Name | Asked | Tool | The software's answer | Tolerance |
|---|---|---|---|---|
| `particles_trimmed` | the solvated size of `trimmed.pdb` at those settings | `preview_setup` | 3,857 | 10% |
| `box_trimmed` | its box width, face to face, in nm | `preview_setup` | 3.912 | 5% |
| `particles_1ubq_settings` | the solvated size of 1UBQ at its settings | `preview_setup` | 20,872 | 10% |
| `residues_renumbered` | protein residues in `renumbered.pdb` | `inspect_structure` | 51 | exact |
| `selection_renumbered` | atoms matched by `name CA and resSeq 1 to 10` in it | `check_selection` | 0 | exact |
| `ligands_kinase` | the ligands in `kinase.pdb`, by residue name | `inspect_structure` | LG7 | the set exactly |
| `histidines_alpha` | histidines in the system setup builds from `alpha.pdb` | `inspect_structure` | 10 | exact |

The tolerances are those of the first registration: the size estimate is
within 3% of what setup builds on the structures it was checked on (1099), so
10% separates a number read from the estimate from one guessed; a twentieth of
this box's width is about a tenth of its padding. Counts are exact. The
selection's answer is zero because the file is numbered from 201; an AI
model answering from 1UBQ would say ten.

## How a reply is judged

- Only an answer is judged. A config, a question back, a refusal or a failure
  is recorded as what it is and counted as not agreeing.
- The answer judged is what follows the last `ANSWER:` in the reply, to the
  end of its line, without emphasis or quotes around it. A reply without one
  commits to nothing and does not agree.
- For a number: the first number on that line, within the tolerance of the
  software's (exactly, where the software's is zero).
- For names: the set of names on that line, split at commas and spaces,
  compared without regard to case, must be the software's set exactly;
  `none` is the empty set.
- **Looked** is whether, in the arm with tools, the AI model used the tool the
  question calls for before its answer.

## What is claimed

Three repeats of each question in each arm, with the AI model the Agent is set
to, named in the run's record. Counted per arm and per question: agreed,
looked, and answered with an `ANSWER:` line.

A question **separates the arms** when the arm with tools agrees in every
repeat and the arm without in none.

Predicted, and to be reported as met or not:

1. At least five of the seven questions separate the arms.
2. In the arm with tools, the AI model looks with the tool each question calls
   for in at least 19 of the 21 replies.
3. In the arm without tools, no reply agrees on `selection_renumbered` or
   `ligands_kinase`, which memory of the entries answers wrongly.

An answer that commits to nothing (no `ANSWER:` line, or for a number a line
holding none) is not counted as agreeing, and is reported per arm as
**declined**, since declining is the right answer for an AI model that cannot
look. No other analysis is claimed; anything
else read from the replies is reported as read, not as a finding.

What is not measured: whether the software's answers are right (that is
setup's own validation), or the configs the Agent writes (that is
`scripts/measure_nli.py`).

## Running it

On a machine that can fetch from the PDB, with an AI model chosen
(`fastmdx agent model`):

```bash
python -m fastmdxplora.validation.agent_looks_v2 --truths-only
python -m fastmdxplora.validation.agent_looks_v2 --repeats 3 --out agent_looks_v2.json
```

The first prints the software's answers and the files' digests and asks
nothing; if an answer differs from the table above (a revised entry, a changed
estimate), the run is reported against the answers it computed and the
difference is stated. The files' digests identify a run's files only, since
MDTraj dates what it writes.
