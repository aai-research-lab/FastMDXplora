# Implementation note: protein-ligand interactions

*How FastMDXplora's interaction analysis is built, why it is built that way,
and what it was checked against. For what it measures and how to use it, see
[Protein-ligand interactions](interactions.md).*

---

## What the analysis answers

Counting contacts says how much of a protein a ligand touches. It does not say
what holds it there, and those are different questions. A salt bridge that a
charge change would destroy and a hydrophobic packing that tolerates one are
both "a contact"; only one of them tells a medicinal chemist what to do next.

FastMDXplora types the interaction: hydrogen bonds, hydrophobic contacts, salt
bridges, π-stacking, π-cation, halogen bonds, metal coordination and water
bridges.

---

## Why FastMDXplora implements this rather than depending on PLIP or ProLIF

Both tools do this well and neither is a dependency, for reasons specific to
each. They also stand in different relations to what is below: ProLIF was run
against this implementation and its results are recorded here; PLIP supplies
criteria and was not run.

**ProLIF requires MDAnalysis.** That is a second trajectory library beside
MDTraj, with its own file handling, its own selection language and its own
release cadence. Two trajectory libraries in one package means two ways for a
file to load differently, and a user's selection string meaning one thing in
one analysis and another elsewhere.

**PLIP re-protonates every frame with OpenBabel.** That is not deterministic
between runs, and it discards the protonation FastMDXplora's setup phase
settled at the simulated pH — which is the protonation the trajectory was
actually generated under. A tool that re-decides it per frame is answering a
different question from the one the simulation asked.

That is also why PLIP does not appear in the checking below. Its published
criteria are adopted and cited throughout — the hydrophobic threshold, the
π-stacking and π-cation geometry, metal coordination, the water bridge — but a
non-deterministic re-protonation cannot serve as a reference measurement for a
trajectory whose protonation was fixed at setup. Citing a tool's criteria and
running it are different claims, and only the first is made here.

---

## What FastMDXplora knows that a general tool has to infer

The advantage is not the geometry — the criteria are published and anyone can
implement them. It is that FastMDXplora ran the simulation, so it knows things
a tool handed a trajectory has to guess:

| | A general tool | FastMDXplora |
|---|---|---|
| **Protonation** | inferred from the structure, per frame | decided in the setup phase, at the simulated pH, recorded |
| **Ligand chemistry** | perceived from coordinates | resolved from the SDF setup wrote, or the Chemical Component Dictionary |
| **Bond orders** | perceived, which can move a hydrogen | known where the ligand was parameterised |
| **Formal charges** | inferred, often ambiguous | taken from the parameterisation |
| **Which residue is the ligand** | told, or guessed | known |

A wrong bond order moves a hydrogen, and a moved hydrogen invents or destroys
a hydrogen bond. Being able to skip that inference is the substantive
advantage, and it is why the resolution route is recorded with every result:
an interaction computed from perceived chemistry is a weaker claim than one
computed from chemistry that was known, and a reader should be able to tell
which they have.

---

## When the trajectory came from somewhere else

FastMDXplora also analyses trajectories it did not produce, where none of the
above holds. Four routes are tried in order, and which one succeeded is
recorded in `options.json`:

1. an SDF supplied with `ligand_chemistry`
2. the run's own `setup/ligands/<resname>.sdf`, if setup produced it
3. the Chemical Component Dictionary, by residue name
4. inference from the coordinates, with RDKit

Route 4 is the general tool's only route. It is available and it is labelled.

**Charge is refused rather than guessed.** Perceiving a formal charge from
coordinates is ambiguous more often than not: for guanidinium both +1 and −1
balance, and for a phenol both 0 and −2 do. Where the charge was not
determined, salt bridges and π-cation interactions — which are claims about
charge — are not reported, and the reason is recorded. The rest continues: a
ligand whose charge is unknown still has hydrogen bonds.

**A topology without bonds is refused outright**, for the reason and with the
message given in [Protein-ligand interactions](interactions.md#some-measurements-are-refused).
What belongs here is why that refusal is the right behaviour rather than a
gap.

That defect was present in FastMDAnalysis v1, in PLIP and in ProLIF: all three
return an answer where the honest response is that the question cannot be
answered from what was given.

---

## The criteria, and where they come from

| Interaction | Criterion | Source |
|---|---|---|
| Hydrogen bond | H···A < 3.5 Å, D-H···A > 120° | Baker & Hubbard (1984); McDonald & Thornton (1994) |
| Hydrophobic | C···C < 4.0 Å, both bonded only to C or H | PLIP (Adasme et al. 2021) |
| Salt bridge | charged group centres < 4.5 Å | ProLIF (Bouysset & Fiorucci 2021) |
| π-stacking | ring centres < 5.5 Å, planes within 30° of parallel or perpendicular, offset < 2.0 Å | PLIP |
| π-cation | charge to ring centre < 6.0 Å, offset < 2.0 Å | PLIP |
| Halogen bond | X···A < 3.5 Å, C-X···A 130–180° | ProLIF |
| Metal coordination | metal to donor < 3.0 Å | PLIP |
| Water bridge | one water 2.5–4.1 Å from each side, angle at the water 71–140° | PLIP |

Every threshold is a setting, because the published values disagree and the
disagreement is real rather than a rounding difference.

**Where the tools disagree, the literature decides and the disagreement is
recorded.** Two cases:

*Hydrogen bond geometry.* PLIP allows 4.1 Å and 100°; the literature standard
is 3.5 Å and 120°. FastMDXplora defaults to the stricter pair, because a force
field positions its hydrogens and a criterion written for structures with
inferred hydrogens does not need to be as forgiving. PLIP's values remain
reachable.

*Fluorine as a halogen-bond donor.* PLIP counts it; ProLIF does not.
Politzer and co-workers attribute fluorine's failure to halogen bond to its
electronegativity and sp hybridisation neutralising the σ-hole, and organic
fluorine bound to carbon is unlikely to participate. FastMDXplora excludes it
by default; `include_fluorine` matches PLIP.

---

## Geometry worth stating

**Charged groups, not atoms.** A carboxylate's charge sits across both
oxygens and a guanidinium's across all three nitrogens, so the centre is the
group's, not any one atom's. An early version included the carboxylate carbon
and put the centre in the wrong place.

**Histidine is excluded from protein charges.** It titrates near physiological
pH, and its protonation was decided in the setup phase; contradicting that
decision downstream is worse than leaving it out.

**Ring planes are fitted, not taken from three atoms.** A ring puckers, and
three atoms of a puckered ring give a normal that swings with which three were
chosen. FastMDXplora fits the plane by SVD.

**Torsional wrap.** π-stacking angles are folded into 0–90°, because a normal
has no preferred direction and 170° between normals is the same arrangement as
10°.

**Tryptophan offers both its rings.** They are fused, but a partner sits over
one or the other, and the centre of the pair is over neither.

---

## Occupancy carries its observation

A contact present in 3 frames of 500 and one present in 450 are both
"present". Worse, two contacts can share a fraction and differ entirely in
what supports it: one present in 450 consecutive frames formed once and
stayed; one present in 450 alternating frames formed and broke 450 times. Both
are fifty per cent, and only the second has an error bar worth printing.

So each interaction is reported with its occupancy, the number of separate
times it formed, and a standard error computed from **episodes rather than
frames** — using the frame count gives a number several times too small,
because consecutive frames are correlated.

Transitions between binding modes are counted always and given as
probabilities only where enough were observed. A matrix from three switches is
arithmetic, not kinetics.

---

## What checking against the other tools found

**ProLIF initially returned nothing**, because the Universe carried no bonds —
the same defect FastMDXplora had just been taught to refuse. After
`guess_bonds()`, the partners were identical: TRP6, LEU7, SER13, SER14.

**Counts agree once the settings are matched.** ProLIF's defaults differ from
FastMDXplora's in two ways — 130° against 120°, and counting per residue pair
rather than per atom pair. At matched settings the counts agree. Neither
difference is a defect; both are choices, and FastMDXplora counts unreduced
because reduction is a decision the reader should make.

**MDTraj's `baker_hubbard` agrees** on the protein-internal hydrogen bonds,
which is the expected result since FastMDXplora uses the same criterion.

---

## References

- Baker, E. N.; Hubbard, R. E. *Hydrogen bonding in globular proteins.* Prog.
  Biophys. Mol. Biol. **1984**, 44, 97–179.
- McDonald, I. K.; Thornton, J. M. *Satisfying hydrogen bonding potential in
  proteins.* J. Mol. Biol. **1994**, 238, 777–793.
- Adasme, M. F. et al. *PLIP 2021: expanding the scope of the protein-ligand
  interaction profiler.* Nucleic Acids Res. **2021**, 49, W530–W534.
- Bouysset, C.; Fiorucci, S. *ProLIF: a library to encode molecular
  interactions as fingerprints.* J. Cheminform. **2021**, 13, 72.
- Politzer, P.; Murray, J. S.; Clark, T. *Halogen bonding: an electrostatically
  driven highly directional noncovalent interaction.* Phys. Chem. Chem. Phys.
  **2010**, 12, 7748–7757.
- McGibbon, R. T. et al. *MDTraj: a modern open library for the analysis of
  molecular dynamics trajectories.* Biophys. J. **2015**, 109, 1528–1532.
