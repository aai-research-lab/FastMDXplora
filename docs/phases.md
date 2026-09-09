# The four phases

```
  setup  →  simulation  →  analysis  →  report
```

Each phase writes into its own directory, records what it did, and can be run
on its own. `fastmdx explore` runs all four; `fastmdx setup`, `simulate`,
`analyze` and `report` run one.

---

## setup

Turns a structure into something that can be simulated.

It fetches by PDB identifier or reads a file, then repairs what is missing —
absent atoms, absent hydrogens, chain breaks — using PDBFixer at the pH you
give. It decides what each non-standard residue is for: a bound ligand is
parameterised, a crystallisation additive discarded, a coordinated metal kept.
Then it solvates, adds ions, and writes a serialised system.

**Where the structure does not say enough, it stops.** An unknown residue, a
ligand whose charge cannot be settled, a clash that means the pose is wrong —
each of these produces a refusal naming what could not be decided and what
would settle it, rather than a guess that runs.

For a ligand, the chemistry is resolved before anything else: from a supplied
SDF, from the Chemical Component Dictionary, or inferred from coordinates —
and which route succeeded is recorded, because an interaction computed from
inferred bond orders is a weaker claim than one from chemistry that was known.

**A chemistry file sets the protonation state.** The file is what gets
parameterised, so the form it is drawn in is the form that is simulated. An
ideal SDF from the Chemical Component Dictionary draws amidines and
carboxylates neutral, which is not their state at physiological pH, and a
study that meant the charged form needs a file in that form. Stating
`ligand_net_charge` checks the file rather than overriding it: where the two
disagree setup stops, because a number cannot change the atoms.

**Point mutations** are applied before anything is repaired or removed,
written as `L99A` or as `LEU-99-ALA`:

```yaml
setup:
  mutations: [L99A]
  mutation_chain: A     # the first chain when not given
```

The original residue is checked against the structure and setup stops where
it does not match. Numbering travels badly — the same protein appears in the
literature with the construct's numbering, the deposition's, and the mature
sequence's — so a mutation written against the wrong one would otherwise
replace whatever sits at that position and every result afterwards would
describe a protein nobody chose. The replacement side chain is placed
geometrically rather than modelled, so a mutant's equilibration is doing more
work than a deposited structure's.

The nonbonded cutoff comes from the force field rather than from a single
default, because a force field is fitted with a particular treatment of the
truncation and its other parameters compensate for that. CHARMM36 is developed
at 1.2 nm with switching from 1.0; the AMBER force fields are developed with
hard truncation near 1.0 and are not switched at all, since switching them
moves a run away from the parameterisation rather than towards it. Setting
`nonbonded_cutoff_nm` or `switch_distance_nm` overrides this, and the run says
which it used.

The switching function is potential-based. A force-based switch is a
different function and is not available here, so a protocol specifying one
cannot be reproduced by setting `switch_distance_nm` alone.

**Writes** `prepared.pdb`, `solvated.pdb`, `system.xml`, `state.xml`,
`setup_parameters.json`, and an SDF per ligand.

---

## simulation

Runs the dynamics: energy minimisation, NVT equilibration, NPT equilibration,
production.

Beyond that it can hold parts of the system still while the solvent settles,
embed a protein in a lipid bilayer, and bias the run along a coordinate you
name. Those are covered in [Beyond a box of water](simulations.md), along with
what the phase says when a run fails.

**Writes** `production.dcd`, `energy.csv`, `checkpoint.chk`,
`simulation_parameters.json`. A biased run also writes the PLUMED input it
generated -- `metadynamics.plumed`, `steered.plumed` or `umbrella.plumed` --
PLUMED's own `COLVAR` and, for metadynamics, `HILLS`; and the result the
method exists to produce, as `metadynamics_surface.json`, `pmf.json` or
`steered_work.json`. Those three are what the `metad_surface`, `pmf` and
`steered_work` analyses read.

---

## analysis

Measures the trajectory. Twenty analyses of the system, each writing its
data, its figure, and the settings it used, and three more that read the
result of a biased run where there was one.

**Shape and size**

| | |
|---|---|
| `rmsd` | how far the structure has moved from a reference frame |
| `rg` | radius of gyration — how compact it is |
| `sasa` | solvent-accessible surface area, total, per residue, or each residue's mean |
| `ss` | secondary structure per residue per frame, by DSSP |

**Flexibility**

| | |
|---|---|
| `rmsf` | per-atom or per-residue fluctuation about the mean |
| `order_parameters` | backbone N--H order parameters, the quantity NMR relaxation measures |
| `bfactor_comparison` | per-residue fluctuation against the deposited structure's B-factors |
| `thermodynamics` | density, energies and temperature, from the state record the run wrote |
| `rdf` | the radial distribution between two selections, stopped at half the box |
| `dihedrals` | backbone phi, psi and omega, with the Ramachandran plot |

**Conformations**

| | |
|---|---|
| `cluster` | k-means, hierarchical and DBSCAN over the trajectory |
| `dimred` | PCA, MDS, t-SNE and UMAP projections |

**Folding**

| | |
|---|---|
| `hbonds` | hydrogen bonds per frame, and each bond's occupancy |
| `qvalue` | fraction of native contacts retained |

**Water**

| | |
|---|---|
| `water_sites` | positions a water holds through the run, and whether one molecule stays or many pass through |

**The ligand**

| | |
|---|---|
| `ligand_rmsd` | how far the ligand has moved, after aligning on the protein |
| `ligand_rmsf` | which parts of the ligand move |

**Protein and ligand together**

| | |
|---|---|
| `pl_contacts` | how much of the protein the ligand touches, with a per-residue fingerprint |
| `pl_hbonds` | hydrogen bonds between them |
| `pl_interactions` | what holds the ligand: eight interaction types, each against a published criterion |

`water_sites` finds the waters that are part of a binding site rather than
passing through — a water wedged between a ligand and a backbone carbonyl,
bridging a hydrogen bond neither could make alone. It reports how often a
position is occupied *and by how many distinct molecules*, because a site held
by one water throughout and a position a hundred waters pass through can share
an occupancy and mean entirely different things: the first is a molecule to
displace, the second is geometry the protein favours. It needs an explicitly
solvated run.

**Point it at a site, not at a whole protein.** Clustering links neighbours
through neighbours, so a whole-protein scope chains the entire first hydration
shell into one object — on ubiquitin that is tens of thousands of positions
and hundreds of distinct waters, which is a surface rather than a site.
FastMDXplora rejects such a cluster and says so, but the fix is a narrower
`site_selection`: a ligand, a pocket, or a handful of residues. That is a
limit of the method rather than a threshold to tune.

The five ligand analyses run automatically when a ligand is present. See
[Protein-ligand interactions](interactions.md) for what `pl_interactions`
measures and why some of it is refused.

**Enhanced sampling**

These three do not measure the trajectory. They read what a biased run itself
produced, and each runs only where such a run produced it -- so none of them
appears after an ordinary simulation, and none of them needs a ligand.

| | |
|---|---|
| `pmf` | the free energy along an umbrella study's coordinate, drawn from the windows it stitched. Reads the study's result rather than recomputing it |
| `metad_surface` | the free energy surface a metadynamics run filled, drawn from its hills. Draws a provisional surface as readily as a settled one, saying which it is |
| `steered_work` | the work done by a steered pull, against the coordinate. The curve rather than the total, because a pull that accumulated work smoothly met resistance all the way and one that accumulated it in a step snapped past something -- and the total is the same either way. A pathway, not a free energy |

What each of the three methods is for, and what its output is and is not, is
in [Beyond a box of water](simulations.md).

### What the measures actually compute

Details that change the number, and that are worth knowing before comparing
against another tool:

- **RMSD** superposes each frame on the reference before measuring, unless
  `align: false`.
- **Radius of gyration** is mass-weighted by default, which is the physical
  definition; `mass_weighted: false` gives the geometric one.
- **Hydrogen bonds** use Baker-Hubbard at 0.25 nm and 120° by default. Both
  are settings, because published criteria disagree — PLIP allows 4.1 Å and
  100°. Bonds are counted in every frame they exist, including transient ones.
- **Secondary structure** uses MDTraj's DSSP and excludes anything DSSP cannot
  assign, so a ligand does not appear as coil.
- **Q-value** uses a switching function rather than a hard cutoff, with β and
  λ exposed. Its contact set S is over *pairs of heavy atoms*, which is the
  published definition; `scheme: residue-closest-heavy` gives the coarser
  reading that takes one distance per residue pair instead, and the two are
  not comparable — on a peeling hairpin they stand 0.26 apart on a scale
  running zero to one. The `selection` narrows it further: `protein` is the
  all-atom measure, `backbone` reports the fold's topology and ignores
  side-chain repacking, and `name CA` is the coarse-grained quantity from
  Gō-model work, which is a different measure rather than a rounding of the
  others. All four choices are written to `options.json`, because a Q quoted
  without them is not one number.
- **Clustering** seeds k-means at 42 by default. A clustering that survives a
  change of seed is a finding; one that does not is an artefact of where the
  algorithm started, and the seed is a setting so that can be tested.
- **Contacts and hydrogen bonds** measure across the periodic boundary where
  the trajectory carries a unit cell.
- **Interaction occupancy per residue** is the union of that residue's atom
  pairs' frames, written to `pl_interactions_by_residue.dat` beside the pair
  table. It cannot be recovered from the pair table: pairs firing in the same
  frames give the largest single pair, pairs that never coincide give their
  sum, and every real case lies between.
- **Order parameters** are the Lipari–Szabo S² of each backbone N–H, taken as
  the closed form of the correlation plateau after superposition. The
  alignment set is a choice that changes the answer and is recorded. A
  trajectory too short reports S² *too high* rather than too noisy, because
  motion it never saw is indistinguishable from rigidity, so the two halves
  are compared and the values are called an upper bound where they disagree.
- **B-factor comparison** converts a refined B through B = (8π²/3)⟨u²⟩ and
  correlates it with the simulated RMSF. It is a correlation and not an
  accuracy: a B carries static disorder and refinement choices, and the
  lattice damps loop motion, so B-factors bound amplitudes from below. No
  regression slope is reported, because the two are not the same measurement.
- **Thermodynamics** reads the state record the simulation wrote and treats
  each column as a correlated series. Density is reported only from a
  constant-pressure run: at fixed volume it is a constant the setup chose,
  and a mean with an error on it would describe arithmetic.
- **g(r)** stops at half the smallest box dimension. Past that the
  minimum-image convention supplies only part of each shell, so the curve
  falls away for a reason belonging to the box rather than the liquid — and
  it falls smoothly enough to read as structure.
- **Secondary structure** uses MDTraj's DSSP only. Version 1 could also shell
  out to an external `mkdssp`; that is not offered here, because a system
  package is a poor dependency for something that already works — and where
  the two disagree, that is a finding about DSSP worth reporting rather than
  configuring around.

**Writes** `analysis/<name>/` per analysis, each with `<name>.dat`,
`<name>.png`, `<name>.svg` and `options.json`. On a biased run it also writes
`analysis/reweighted/`, which holds `reweighted_averages.json` and, where
there were averages to correct, a `.dat` table and a figure. It has no
`options.json` because it is not an analysis with options: it is a pass over
the ones that already ran.

### Averages on a biased run

A metadynamics trajectory is not a Boltzmann ensemble. The bias flattened it
on purpose, so a mean over its frames is an average over a distribution nobody
wanted, and reported without qualification it reads as a measurement of the
system.

Where the bias is known it can be undone. Each frame is weighted by
`exp((V - c(t))/RT)`, with `V` the bias that frame was actually sampled under
-- the hills laid down before it, not the fully deposited surface -- and
`c(t)` the Tiwary-Parrinello offset. Both details matter. Weighting by the
final surface inflates early frames, which were sampled under almost no bias
at all. And without `c(t)` the weights rank frames by *when they were
written* rather than by where the system was, because the bias grows as hills
accumulate: on a converged well-tempered test run, the last fifth of the
frames carried the entire weight and an average over five hundred rested on
seven of them.

The corrected value is reported first and the biased one beside it, so the
size of the correction stays visible, and the **effective sample size** is
printed with it rather than in a footnote. A reweighted mean over a thousand
frames whose weight sits in five of them is a mean over five, and there is no
arrangement of a document in which that should be readable without the five.

Analyses reporting one value per frame are corrected -- RMSD, radius of
gyration, hydrogen bonds, SASA, the fraction of native contacts, ligand RMSD
-- along with cluster populations, which are weighted counts. What reweighting does not fix is
which clusters exist: the clustering ran on the biased frames, so the states
themselves are shaped by where the bias sent the system. The dimensionality
reduction is not corrected at all, because a projection is not an average.

The other two methods get no correction, and this is a property of the
methods rather than a gap. An umbrella window is a system held where it was
put; its averages describe it there, they are not comparable between windows,
and what combines the windows is the potential of mean force. A steered pull
is not an equilibrium ensemble at all. For both, the averages are still
reported -- they describe what the run did -- and are labelled as being of a
biased ensemble wherever they appear, including in the dashboard's metrics
table.

---

## report

Assembles everything into things you can send.

The report opens with a **methods paragraph** rather than a list of settings.
A methods section for a molecular dynamics study has to state a particular
list of things — coordinates and their source, protonation, force field
version, water model, box, ions, ensembles, integrator, thermostat, barostat,
cutoffs, constraints, durations — and that list is published, in JCIM's
reporting guidelines and Communications Biology's reproducibility checklist.
Every value is already recorded, so the paragraph is assembled from them.
Nothing is invented, and anything the run did not record is named as missing
rather than filled in with what is usual.

It then reports **convergence**, which is a statement about how much
independent information the trajectory holds. A frame is not an observation:
consecutive frames are nearly the same structure, so the number of independent
observations depends on how quickly a measure forgets where it was, not on how
often frames were written. On a five-thousand-frame trajectory of Trp-cage the
RMSD holds about twenty independent observations, so its uncertainty is
fourteen times what counting frames would give. Where a run is too short to
say — and a short one usually is — the section says what it cannot support.

**Writes** `report.md`, `report.pdf`, `dashboard.html`, `slides.pptx`,
`project_bundle.zip`. The PDF needs the `pdf` extra; where it is absent the
run says so and writes the rest.

### Highlighting regions you care about

A per-residue figure with two hundred residues on the x-axis says little about
the eight that matter. Name them and they are shaded on the RMSF trace and
coloured on a cartoon of the structure:

```yaml
report:
  region_highlights:
    - label: "binding loop"
      start: 84
      end: 92
      color: "#4E79A7"
    - label: "catalytic helix"
      start: 118
      end: 131
```

`label` and `color` are optional — an unlabelled region becomes "Region 1",
an uncoloured one takes the next of six palette colours.

This produces `analysis/rmsf/rmsf_region_highlights.png` and, where PyMOL is
installed (`conda install -c conda-forge pymol-open-source`),
`report/structure_region_highlights.png` with the `.pml` script beside it so
the rendering can be adjusted by hand. Without PyMOL the RMSF figure is still
written and the report records that the structure rendering was skipped and
why.

Regions attach to **RMSF** and nothing else, because RMSF is indexed by
residue — RMSD is indexed by frame, so a residue range has no meaning on it.
RMSF analysis therefore has to have run.

A range outside the residues RMSF measured is refused, with both ranges named:
the one you asked for and the one that exists. That is usually an off-by-one
between a paper's numbering and the structure's, and seeing both makes it
obvious.

The labels are yours. FastMDXplora does not work out that residues 84 to 92
are a binding loop; it draws what you tell it to and calls it what you call
it.

---

## What a run leaves behind

A run directory, with the four phases' outputs beside the two records that
say what was asked for and what happened. The map, what to open first, and
how to tell a measurement from a number the run could not support are in
[Reading the results](results.md).
