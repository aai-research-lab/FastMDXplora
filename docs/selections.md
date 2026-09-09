# Selections

A selection says *which atoms*. FastMDXplora asks for one in four places, and
they all speak the same language.

```yaml
analysis:
  select_atoms: "protein and name CA"
```

## One language: MDTraj's

Every selection expression is parsed by MDTraj's
[atom selection language](https://mdtraj.org/1.9.4/atom_selection.html) and
resolved with `topology.select()`. Whatever the key is called and whichever
phase reads it, the string means what MDTraj says it means.

The word `select_atoms` is MDAnalysis's, and the language is MDTraj's. That
is deliberate: the word is what most people arrive knowing, and the language
is what this package already parses everywhere else. Borrowing one without
the other would be worse than borrowing neither.

### `resid` is not the residue number

This catches people, and it has caught this project. MDTraj has two ways to
name a residue and they are not the same:

| expression | means |
| --- | --- |
| `resid 189` | the **190th residue in the file**, counting from zero |
| `resSeq 189` | the residue **numbered 189** in the file |

For a structure numbered from 1 with no gaps they coincide. For a PDB entry
they usually do not. Trypsin (3PTB) is numbered 16–245 with gaps, so:

```
resid  189 to 195  ->  Ile212 Val213 Ser214 Trp215 Gly216 Ser217 Gly219
resSeq 189 to 195  ->  Asp189 Ser190 Cys191 Gln192 Gly193 Asp194 Ser195
```

Both are real selections and neither is empty, so nothing downstream can tell
you which one you meant. **If you are naming residues from a paper, a figure,
or a PDB entry, you almost certainly want `resSeq`.**

Check before you run:

```python
import mdtraj as md
top = md.load("runs/my-study/shared_setup/setup/topology.pdb").topology
print([str(top.atom(i).residue) for i in top.select("resSeq 189 to 195 and name CA")])
```

## Where selections appear

### What an analysis measures

```yaml
analysis:
  select_atoms: "name CA"     # `selection` is the earlier name, still accepted
  scope: solute               # used when select_atoms is absent
```

`scope` is the shorthand, and it resolves to a real selection:

| scope | resolves to |
| --- | --- |
| `solute` *(default)* | `protein or resname <ligand>`, or `protein` if there is no ligand |
| `protein` | `protein` |
| `ligand` | `resname <ligand>` — fails if no ligand is known |
| `all` | no selection; every atom |

The default is `solute` rather than everything because a radius of gyration
computed over the water box reports the size of the box and barely moves.

An individual analysis may carry its own selection in `analysis.options`,
which wins over both:

```yaml
analysis:
  scope: solute
  options:
    rmsd: {selection: "name CA"}
```

### What goes into the trajectory

```yaml
simulation:
  save_selection: "not water"   # the default
```

Water is ten times the file for questions most runs are not asking, so it is
left out by default and the matching topology is written beside the
trajectory. Two consequences worth knowing:

- An analysis that needs solvent — `water_sites`, `rdf` — needs
  `save_selection: all`.
- **A frame can only be used as a starting structure if it holds every
  atom.** A study that seeds umbrella windows from a pull sets
  `save_selection: all` for that pull itself, because a seed is a complete
  set of positions and a subset cannot become one.

### What is held still during equilibration

```yaml
simulation:
  restrain: "protein and not element H"
```

A minimised structure is not at equilibrium, and heating it lets the solute
move as well as the solvent: side chains relax into space that crystal
packing left, and a ligand drifts out of the pose that was measured.
Restraints are released in stages and are off for production.

### What a biased coordinate is measured between

The `umbrella`, `steered` and `metadynamics` blocks share one
collective-variable layer, so a variable is named the same way whichever
method biases it.

| variable | selections it needs |
| --- | --- |
| `distance` | `select_atoms_a`, `select_atoms_b` |
| `coordination` | `select_atoms_a`, `select_atoms_b` |
| `ligand_distance` | the ligand by name, plus `select_atoms` for the site |
| `ligand_rmsd` | the ligand by name; no selection |
| `angle` | `select_atoms` matching **exactly three** atoms |
| `torsion` | `select_atoms` matching **exactly four** atoms |
| `radius_of_gyration` | `select_atoms` *(default `protein and name CA`)* |
| `membrane_depth` | `select_atoms` for the molecule, `bilayer_selection` for the membrane |

`select_atoms` names the selection wherever a variable takes exactly one.
Where it takes two, `select_atoms_a` and `select_atoms_b` say which is which
— a bare `select_atoms` on a `distance` is refused rather than assigned to a
group and hoped over.

The role-specific names all still work and say more where they apply:
`site_selection`, `bilayer_selection`, `axis_selection`, `selection`,
`selection_a`, `selection_b`. Given both, the role name wins.

**The two spellings are settled when the config is read, not where it is
used.** After loading, a block that was given either name carries both, with
the same value. Nothing downstream has to know which word you wrote, and no
part of a run can disagree with another about what the block says — because
there is no absent spelling for one of them to miss.

This is not a detail. A study written with `select_atoms` once pulled a
ligand for two and a half hours and then stopped, because the code that
builds the PLUMED script translated the word and the code that seeds the
windows did not. The translation was correct; it just lived in one of the two
readers. It now lives before both.

The ligand is named by residue, not by selection, and `ligand_name` and
`ligand_resname` are the same key:

```yaml
setup:
  ligand_name: BEN
simulation:
  umbrella:
    collective_variable: ligand_distance
    ligand_name: BEN                              # the same word as above
    select_atoms: "resSeq 189 to 195 and name CA"
```

Left out entirely, the ligand is detected from the topology — which works
when there is exactly one candidate residue and refuses when there are
several, because that is a question the topology cannot answer.

## When a selection is resolved

Twice, and the first time is a guard.

A biasing block's selections are resolved **against the prepared system
before any window is launched**. `resid 3 and name CA` on a tripeptide is
empty — MDTraj counts residues from zero — and finding that out when the
first window starts costs a full preparation and a failed run, or in a
parallel study three failed windows at once. The topology is on disk and the
selections are in the config, so nothing about it needs a simulation to
discover.

That check is silent where it cannot tell. A selection it fails to resolve is
not thereby wrong, and refusing on that basis would be worse than the wait.

A selection matching **no atoms** is an error, and the message names the
expression:

```
The site selection 'resid 900' matched no atoms, so there is nothing to bias.
```

A selection matching the **wrong** atoms is not an error, and nothing can
make it one. That is what the `resid`/`resSeq` table above is for.

## Recorded with the results

Every analysis writes the options it actually used into `options.json` beside
its output, the resolved selection among them. This is not bookkeeping: when
this project's radius of gyration disagreed with an independent recomputation
by 5×10⁻² nm, the run's own record of which atoms it had measured is what
resolved it — and the difference fell to 10⁻⁶ nm, floating-point reading
precision, once the recomputation used the same selection.

If two tools disagree about a number, compare their selections first.
