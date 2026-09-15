# How FastMDXplora works

A FastMDXplora study is a Config, four phases, and a Manifest.

```
   Config  ──▶  setup  ──▶  simulation  ──▶  analysis  ──▶  report  ──▶  Manifest
```

The [Config](config.md) says what the study is. The four phases run themselves
from it, each writing into its own directory. The [Manifest](manifest.md)
records what actually happened.

Every phase can be run on its own. `fastmdx explore` runs all four;
`fastmdx setup`, `simulate`, `analyze` and `report` run one each. In the
Config, `include:` and `exclude:` choose which.

---

## setup

**Turns a structure into something that can be simulated.**

It fetches by PDB identifier or reads a file, then repairs what is missing —
absent atoms, absent hydrogens, chain breaks — using PDBFixer at the pH you
give. It decides what each non-standard residue is for: a bound ligand is
parameterised, a crystallisation additive discarded, a coordinated metal kept.
Then it solvates, adds ions, and writes a serialised system.

**Where the structure does not say enough, it stops.** An unknown residue, a
ligand whose charge cannot be settled, a clash that means the pose is wrong —
each produces a refusal naming what could not be decided and what would settle
it, rather than a guess that runs.

### What it decides about a ligand

The chemistry is resolved before anything else: from a supplied SDF, from the
Chemical Component Dictionary, or inferred from coordinates. Which route
succeeded is recorded, because an interaction computed from inferred bond
orders is a weaker claim than one computed from chemistry that was known.

**A chemistry file sets the protonation state.** The file is what gets
parameterised, so the form it is drawn in is the form that is simulated. An
ideal SDF from the Chemical Component Dictionary draws amidines and
carboxylates neutral, which is not their state at physiological pH, and a study
that meant the charged form needs a file in that form. Stating
`setup.ligand_net_charge` checks the file rather than overriding it: where the
two disagree, setup stops, because a number cannot change the atoms.

### Point mutations

Applied before anything is repaired or removed, written as `L99A` or as
`LEU-99-ALA`:

```yaml
setup:
  mutations: [L99A]
  mutation_chain: A     # the first chain when not given
```

The original residue is checked against the structure and setup stops where it
does not match. Numbering travels badly — the same protein appears in the
literature with the construct's numbering, the deposition's, and the mature
sequence's — so a mutation written against the wrong one would otherwise
replace whatever sits at that position, and every result afterwards would
describe a protein nobody chose.

The replacement side chain is placed geometrically rather than modelled, so a
mutant's equilibration is doing more work than a deposited structure's.

### The nonbonded cutoff comes from the force field

Not from a single default. A force field is fitted with a particular treatment
of the truncation, and its other parameters compensate for that. CHARMM36 is
developed at 1.2 nm with switching from 1.0; the AMBER force fields are
developed with hard truncation near 1.0 and are not switched at all, since
switching them moves a run away from the parameterisation rather than towards
it. Setting `setup.nonbonded_cutoff_nm` or `setup.switch_distance_nm` overrides
this, and the run records which it used.

The switching function is potential-based. A force-based switch is a different
function and is not available here, so a protocol specifying one cannot be
reproduced by setting `switch_distance_nm` alone.

**Writes** `input.pdb`, `prepared.pdb`, `solvated.pdb`, `topology.pdb`,
`system.xml`, `state.xml`, `setup_parameters.json`, and an SDF per ligand under
`ligands/`.

---

## simulation

**Runs the dynamics:** energy minimisation, NVT equilibration, NPT
equilibration, production.

The equilibration stages are independent of `simulation.duration_ns`, which
sets the production length only. Defaults are 500 ps NVT, 1 ns NPT and 2 ns
production, at a 2 fs timestep and 300 K.

Beyond that it can hold parts of the system still while the solvent settles,
embed a protein in a lipid bilayer, and bias the run along a coordinate you
name. Those are in [Studies beyond a box of water](studies.md), along with what
the phase says when a run fails.

**Writes** `production.dcd`, `topology.pdb`, `trajectory_topology.pdb`,
`energy.csv`, `state_minimized.xml`, `state_final.xml`, `checkpoint.chk`,
`simulation.log`, `simulation_parameters.json`.

A biased run also writes the PLUMED input it generated —
`metadynamics.plumed`, `steered.plumed` or `umbrella.plumed` — and `plumed.dat`,
the resolved script actually attached to the run. Beside those go PLUMED's own
`COLVAR`, and for metadynamics `HILLS`; and the result the method exists to
produce, as `metadynamics_surface.json`, `steered_work.json`, or `pmf.json` at
the study root for umbrella sampling. Those three are what the
`metad_surface`, `steered_work` and `pmf` analyses read.

---

## analysis

**Measures the trajectory.** Twenty-four analyses of the system, plus three
that read what a biased run itself produced.

Each writes its data, its figure, and the settings it actually used. The full
catalogue — what each one computes, what changes the number, and when each runs
automatically — is [The FastMDXplora analyses](analyses.md).

Two behaviours are worth knowing before you read any result:

**Each analysis gets its own copy of the trajectory.** MDTraj's superposition
rotates coordinates in place, so an analysis that aligns would otherwise change
what the next one measures. An interaction analysis once reported 252
hydrophobic contacts after RMSF and ligand RMSD had aligned the frames, and 10
when run alone. The 10 was right.

**Unknown per-analysis options are refused, not ignored.** Every analysis ends
in `**kwargs`, so a misspelled `n_clusteres` used to cluster at the default and
report success.

**Writes** `analysis/<name>/` per analysis, each with `<name>.dat`,
`<name>.png` and `options.json`, plus `analysis_manifest.json`. On a biased run
it also writes `analysis/reweighted/`.

---

## report

**Assembles everything into things you can send.**

The report opens with a **methods paragraph** rather than a list of settings. A
methods section for a molecular dynamics study has to state a particular list
of things — coordinates and their source, protonation, force field version,
water model, box, ions, ensembles, integrator, thermostat, barostat, cutoffs,
constraints, durations — and that list is published, in JCIM's reporting
guidelines and Communications Biology's reproducibility checklist. Every value
is already recorded, so the paragraph is assembled from them. Nothing is
invented, and anything the run did not record is named as missing rather than
filled in with what is usual.

It then reports **convergence**, which is a statement about how much
independent information the trajectory holds. A frame is not an observation:
consecutive frames are nearly the same structure, so the number of independent
observations depends on how quickly a measure forgets where it was, not on how
often frames were written. On a five-thousand-frame trajectory of Trp-cage the
RMSD holds about twenty independent observations, so its uncertainty is some
sixteen times what counting frames would give. Where a run is too short to say
— and a short one usually is — the section says what it cannot support.

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

`label` and `color` are optional — an unlabelled region becomes "Region 1", an
uncoloured one takes the next of six palette colours.

This produces `analysis/rmsf/rmsf_region_highlights.png` and, where PyMOL is
installed (`conda install -c conda-forge pymol-open-source`),
`report/structure_region_highlights.png` with the `.pml` script beside it so the
rendering can be adjusted by hand. Without PyMOL the RMSF figure is still
written and the report records that the structure rendering was skipped and why.

Regions attach to **RMSF** and nothing else, because RMSF is indexed by residue
— RMSD is indexed by frame, so a residue range has no meaning on it. The RMSF
analysis therefore has to have run.

A range outside the residues RMSF measured is refused, with both ranges named:
the one you asked for and the one that exists. That is usually an off-by-one
between a paper's numbering and the structure's, and seeing both makes it
obvious.

The labels are yours. FastMDXplora does not work out that residues 84 to 92 are
a binding loop; it draws what you tell it to and calls it what you call it.

**Writes** `report.md`, `report.pdf`, `slides.pptx`, `slides_outline.md`,
`dashboard.html`, `analysis_summary.png`, `project_bundle.zip`. The PDF needs
WeasyPrint; where it is absent the run records that in `not_produced.json` and
writes the rest.

`dashboard.html` is a self-contained file that opens in a browser with no
server. It is not the [FastMDXplora GUI](gui.md), which is a live interface
served by `fastmdx gui`.

---

## Explanations, while it happens

Molecular dynamics has a lot of steps that are obvious once you know them and
opaque before that. A pipeline that does all of it silently is quicker to use
and teaches nothing: you end up with a trajectory you cannot defend.

So each step says why it is happening, as it happens, with a citation where
there is one worth following:

```
▸ Minimizing energy
  The starting structure has strain in it — atoms slightly too close,
  bonds slightly too long — from the experiment, from adding hydrogens,
  and from dropping the protein into water. At the temperature of a
  simulation that strain becomes violent motion. Minimisation walks the
  structure downhill to a nearby arrangement with no such forces in it,
  before anything moves.
```

Sixteen of them, covering protonation, heterogens, ligand chemistry and
parameters, solvation, minimisation, NVT, NPT, which ensemble production runs
in, restraints, membranes and their barostat, metadynamics, interactions and
convergence. Each says *why* rather than repeating what the step already said,
and a reference carries authors and a year, or is absent.

On by default. `explain: false` in the Config, or `--no-explain` on the command
line, turns them off.

---

## What a run leaves behind

```
runs/study/
├── setup/                  prepared and solvated structures, and what was decided
├── simulation/             the trajectory, the energy log, the settings used
├── analysis/               one directory per measure
├── report/                 the written report, slides, dashboard, bundle
├── manifest.json           what happened          ← the Manifest
├── resolved_config.yml     what was asked for     ← re-runnable
└── fastmdxplora.log        the full audit trail
```

[The FastMDXplora Manifest](manifest.md) covers `manifest.json` and the
per-phase records beside it. [Reading the results](results.md) is the map: what
to open first, and how a measure says whether its number is one.
