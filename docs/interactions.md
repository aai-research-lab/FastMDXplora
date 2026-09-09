# Protein-ligand interactions

Counting contacts tells you how much of a protein a ligand touches. It does
not tell you what is holding it there, and those are different questions. A
salt bridge that a charge change would destroy and a hydrophobic packing that
tolerates one are both "a contact"; only one of them tells a medicinal chemist
what to do next.

The `pl_interactions` analysis answers the second question. It runs
automatically whenever a ligand is present.

```bash
fastmdx explore --system 181L --setup-forcefield amber-openff \
  --output runs/lysozyme
```

## What it measures

Eight interaction types, each implemented against a published criterion named
in the rule's own docstring.

| Interaction | Criterion | Source |
|---|---|---|
| Hydrogen bond | H···A < 3.5 Å, D-H···A > 120° | Baker & Hubbard 1984; McDonald & Thornton 1994 |
| Hydrophobic | C···C < 4.0 Å, both bonded only to carbon or hydrogen | PLIP |
| Salt bridge | opposite charged groups < 4.5 Å, centre to centre | ProLIF |
| π-stacking | ring centres < 5.5 Å, planes within 30° of parallel or perpendicular, offset < 2.0 Å | PLIP |
| π-cation | charge to ring centre < 6.0 Å, offset < 2.0 Å | PLIP |
| Halogen bond | X···A < 3.5 Å, C-X···A between 130° and 180° | ProLIF |
| Metal coordination | metal to donor < 3.0 Å | PLIP |
| Water bridge | one water 2.5–4.1 Å from each side, angle at the water 71–140° | PLIP |

Every threshold is a setting, because the published values disagree and the
disagreement is a real one rather than a rounding difference. PLIP allows a
hydrogen bond at 4.1 Å and 100°; the literature standard is 3.5 Å and 120°.
The stricter values are the default here, because a force field positions its
hydrogens and a criterion written for structures with inferred hydrogens does
not need to be as forgiving. PLIP's values remain reachable.

Where the tools disagree about chemistry rather than geometry, the literature
decides and the disagreement is recorded. PLIP counts fluorine as a halogen
bond donor; ProLIF does not. Organic fluorine bound to carbon has a negative
σ-hole and does not halogen bond, so it is excluded by default and
`include_fluorine` matches PLIP for anyone comparing.

## The chemistry comes first

Whether a nitrogen donates, whether a ring is aromatic, whether a group is
charged — these are chemistry, not coordinates, and a trajectory carries only
coordinates. The chemistry is resolved before any geometry is measured, by the
first of these that works:

1. an SDF you supply with `ligand_chemistry`
2. the run's own `setup/ligands/<resname>.sdf`, written when setup prepared it
3. the Chemical Component Dictionary, by residue name
4. inference from the coordinates with RDKit

Which route succeeded is recorded in `options.json` and stated in the report,
because an interaction computed from inferred bond orders is a weaker claim
than one computed from chemistry that was resolved. A wrong bond order moves a
hydrogen, and a moved hydrogen invents or destroys a hydrogen bond.

## Some measurements are refused

**Salt bridges and π-cation interactions are claims about charge.** A ligand's
charge inferred from coordinates is ambiguous more often than not: for
guanidinium both +1 and −1 balance, and for a phenol both 0 and −2 do. Where
the charge was not determined, those two are not reported and the reason is
recorded. The rest of the analysis continues — a ligand whose charge is
unknown still has hydrogen bonds.

**A topology without bonds is refused outright.** Hydrogen bonds cannot be
found without knowing which hydrogen belongs to which donor, and a trajectory
loaded from a bare DCD may carry no bonds at all. The analysis stops with the
count of orphan hydrogens and what to do about it, rather than reporting the
zero bonds it would otherwise find. This defect was present in version 1, in
PLIP, and in ProLIF: all three return an answer where the honest response is
that the question cannot be answered from what was given.

## Occupancy carries its observation

A contact present in 3 frames of 500 and one present in 450 are both
"present". Reporting both as an occupancy hides that the first rests on three
observations.

Worse, two contacts can share a fraction and differ entirely in what supports
it. A contact present in 450 consecutive frames formed once and stayed. One
present in 450 alternating frames formed and broke 450 times. Both are fifty
per cent; only the second has an error bar worth printing.

So each interaction is reported with:

- **occupancy** — the fraction of frames it was present
- **episodes** — how many separate times it formed
- **standard error** — computed from episodes, not frames, because
  consecutive frames are correlated and using the frame count gives a number
  several times too small
- **well sampled** — whether it rests on enough independent observation to
  average

The figure draws thinly observed contacts hollow rather than footnoting them,
because an occupancy resting on one observation should not look like one
resting on four hundred.

## Binding modes and transitions

A binding mode is the set of interactions present in a frame; frames sharing a
set are in the same mode, and the modes are what a ligand moves between.
Interactions below `minimum_occupancy` are dropped before grouping, because
otherwise a single fleeting contact splits one arrangement into two.

Transitions between modes are **counted always** and given as probabilities
**only where enough were seen**. A matrix built from three observed switches is
arithmetic rather than kinetics: the uncertainty on such a rate is larger than
the rate. Below ten observed transitions the counts are reported and the
probabilities are not, with the reason stated. What is withheld is the claim,
not the observation.

## What a run looks like

T4 lysozyme L99A with benzene bound (PDB 181L) is the standard test case for a
purely hydrophobic binding site. A short run of it finds hydrophobic contacts
and nothing else — benzene has no donors, no acceptors, no charge and no
halogens — with the highest occupancy on ALA99, which is the mutation that
creates the cavity:

```
kind,ligand_atom,protein_atom,occupancy,episodes,well_sampled,residue
hydrophobic,2640,1568,0.94,5,True,ALA99
hydrophobic,2639,1568,0.92,7,True,ALA99
hydrophobic,2639,1743,0.90,7,True,VAL111
hydrophobic,2637,1385,0.81,13,True,VAL87
```

## Settings

| Setting | Default | What it does |
|---|---|---|
| `kinds` | all eight | Which interactions to look for |
| `ligand_chemistry` | none | An SDF stating the ligand's chemistry |
| `ligand_net_charge` | none | The ligand's charge, where you know it |
| `protein_selection` | `protein` | The other side: a chain, a domain, or `nucleic` for a nucleic-acid receptor |
| `minimum_occupancy` | 0.1 | How often an interaction must appear to count towards a binding mode |
| `periodic` | true | Measure across the periodic boundary where the trajectory carries a cell |

## Why not PLIP or ProLIF

Neither is a dependency, and the two stand in different relations to this
implementation: one supplied criteria, the other supplied results.

**PLIP is where several of the criteria come from.** The 4.0 Å hydrophobic
contact, π-stacking, π-cation, metal coordination and the water bridge are
PLIP's published definitions, cited in the table above and in each rule's
docstring. PLIP itself is not run — not as a dependency and not as a check.
It re-protonates every frame with OpenBabel, which is not deterministic
between runs and discards the protonation that setup settled at the simulated
pH. A tool that re-decides the protonation per frame is answering a different
question from the one the simulation asked, and that disqualifies it as a
reference measurement for the same reason it disqualifies it as a dependency.

**ProLIF was run against this implementation.** It is not a dependency because
it requires MDAnalysis: a second trajectory library beside MDTraj, with its own
file handling and its own selection language.

What the checking found is recorded in `interactions_design.md`. Against
ProLIF the partners agree exactly, and the counts agree once the threshold and
counting differences are accounted for; MDTraj's `baker_hubbard` agrees on the
protein-internal hydrogen bonds. Neither difference from ProLIF is a defect;
both are settings.
