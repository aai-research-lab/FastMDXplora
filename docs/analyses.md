# The FastMDXplora analyses

Twenty-seven analyses of the trajectory, and three more that read what a biased
run itself produced.

Each writes its data, its figure, and the settings it actually used:

```
analysis/<name>/
├── <name>.dat            the numbers
├── <name>.png            the figure, at figure_width (the page unless set)
├── <name>_greyscale.png  only with figure_colours: both
└── options.json          selection, every option, findings, and the .dat format
```

Choose them in a [Config](config.md):

```yaml
analysis:
  include: [rmsd, rmsf, rg, ss]        # or exclude: [sasa, dimred]
  scope: solute
  options:
    cluster: {methods: [kmeans], n_clusters: 8}
```

---

## The catalogue

### Shape and size

| | |
|---|---|
| `rmsd` | How far the structure has moved from a reference frame |
| `rg` | Radius of gyration — how compact it is |
| `sasa` | Solvent-accessible surface area: total, per residue, or each residue's mean |
| `ss` | Secondary structure per residue per frame, by DSSP |

### Flexibility

| | |
|---|---|
| `rmsf` | Per-atom or per-residue fluctuation about the mean |
| `order_parameters` | Backbone N–H order parameters, the quantity NMR relaxation measures experimentally |
| `bfactor_comparison` | Per-residue fluctuation against the deposited structure's B-factors |
| `thermodynamics` | Density, energies and temperature, from the state record the run wrote |
| `rdf` | The radial distribution between two selections, stopped at half the box |
| `coordination_number` | How many of one selection sit within a shell of the other |
| `dihedrals` | Backbone phi, psi and omega, with the Ramachandran plot |

`coordination_number`'s cutoff is asked for, or read from this run's own first
minimum in g(r) — never assumed, because the radius decides the number.

### Geometry

| | |
|---|---|
| `pair_distance` | The separation of two selections, by centre of mass or closest approach, folded into the periodic cell |
| `end_to_end` | The distance between the two ends of a chain, the coarsest description of extension there is |
| `moments_of_inertia` | The three principal moments, which separate a rod from a disc where the radius of gyration cannot |

### The bilayer

| | |
|---|---|
| `area_per_lipid` | The area a lipid occupies in the plane of the bilayer, with the protein's cross section taken out |
| `bilayer_thickness` | The distance between the two leaflets' phosphate planes, D_PP |
| `lipid_order` | The acyl-chain order parameter S_CD of every carbon, the quantity deuterium NMR measures |

The three numbers a membrane run is checked against before anything about the
protein in it is believed: each has an experimental value, and each moves when
the force field, the temperature or the barostat is wrong. They run only where
there is a bilayer. See [Membrane proteins](membranes.md) for what each
computes, and what to compare it with.

### Conformations

| | |
|---|---|
| `cluster` | k-means, hierarchical and DBSCAN over the trajectory |
| `dimred` | PCA, MDS, t-SNE and UMAP projections |

### Folding

| | |
|---|---|
| `hbonds` | Hydrogen bonds per frame, and each bond's occupancy |
| `qvalue` | Fraction of native contacts retained |

### Water

| | |
|---|---|
| `water_sites` | Positions a water holds through the run, and whether one molecule stays or many pass through |

`water_sites` finds the waters that are part of a binding site rather than
passing through — a water wedged between a ligand and a backbone carbonyl,
bridging a hydrogen bond neither could make alone. It reports how often a
position is occupied **and by how many distinct molecules**, because a site
held by one water throughout and a position a hundred waters pass through can
share an occupancy and mean entirely different things: the first is a molecule
to displace, the second is geometry the protein favours.

Each frame is first put in the site's own frame: every water position found
is moved and turned with the least-squares fit of the site's atoms onto the
first frame's (moved only where the site has fewer than three atoms), and the
sites are given in the first frame's coordinates. A protein turns in its box
over a run, and a water held on it traced an arc in the box, which was
rejected as a surface or split at part of its occupancy.

It needs an explicitly solvated trajectory — set
`simulation.save_selection: all`.

**Point it at a site, not at a whole protein.** Clustering links neighbours
through neighbours, so a whole-protein scope chains the entire first hydration
shell into one object — on ubiquitin that is tens of thousands of positions and
hundreds of distinct waters, which is a surface rather than a site.
FastMDXplora rejects such a cluster and says so, but the fix is a narrower
`site_selection`: a ligand, a pocket, or a handful of residues. That is a limit
of the method rather than a threshold to tune.

### The ligand

| | |
|---|---|
| `ligand_rmsd` | How far the ligand has moved, after aligning on the protein. Where it leaves the site it started in (no heavy atom within 0.6 nm of it), that is said, the frames are shaded, its distance to the site is written to `ligand_site_distance.dat`, and no mean is given |
| `ligand_rmsf` | Which parts of the ligand move |

### Protein and ligand together

| | |
|---|---|
| `pl_contacts` | How much of the protein the ligand touches, with a per-residue fingerprint |
| `pl_hbonds` | Hydrogen bonds between them |
| `pl_interactions` | What holds the ligand: eight interaction types, each against a published criterion |

The five ligand analyses run automatically when a ligand is present. See
[Protein-ligand interactions](interactions.md) for what `pl_interactions`
computes and why some of it is refused.

### Enhanced sampling

These three do not analyse the trajectory. They read what a biased run itself
produced, and each runs only where such a run produced it — so none appears
after an ordinary simulation, and none needs a ligand.

| | |
|---|---|
| `pmf` | The free energy along an umbrella study's coordinate, from the windows it stitched. Reads the study's result rather than recomputing it |
| `metad_surface` | The free energy surface a metadynamics run filled, from its hills. Plots a provisional surface as readily as a converged one, saying which it is |
| `steered_work` | The work done by a steered pull, against the coordinate |

`steered_work` gives the curve rather than the total, because a pull that
accumulated work smoothly met resistance all the way and one that accumulated
it in a step snapped past something — and the total is the same either way. A
pathway, not a free energy.

What each method is for, and what its output is and is not, is in
[Studies beyond a box of water](studies.md).

---

## Which ones run automatically

Not every analysis is in the default plan. Several are **gated on what the run
actually contains**, because running them anyway would mean answering the
question rather than analysing it.

| Gate | Analyses it holds back | Runs when |
|---|---|---|
| A ligand | `pl_contacts`, `pl_hbonds`, `pl_interactions`, `ligand_rmsd`, `ligand_rmsf` | A ligand residue name is known |
| Water | `water_sites`, `rdf`, `coordination_number` | The trajectory carries water residues |
| A periodic box | `rdf`, `coordination_number` | The trajectory carries unit-cell vectors |
| Amide hydrogens | `order_parameters` | The topology has backbone N–H pairs |
| Crystallographic B-factors | `bfactor_comparison` | The input PDB carries real B-factors |
| A state record | `thermodynamics` | `simulation/energy.csv` exists |
| A bilayer | `area_per_lipid`, `bilayer_thickness`, `lipid_order` | The trajectory holds at least 20 lipids and a periodic box |
| Tertiary structure | `qvalue` | The solute has more residues than the sequence separation |
| Enough atoms to align | any analysis that aligns | The default selection matches at least 3 atoms |
| A biased run's result | `pmf`, `metad_surface`, `steered_work` | `pmf.json`, `metadynamics_surface.json` or `steered_work.json` exists |
| **Naming** | `coordination_number`, `pair_distance` | **Never automatically** |

The last one is the interesting case. `coordination_number` and
`pair_distance` compute a quantity between *two named things*, and the trajectory does not
contain which two. Running them anyway would mean picking a pair, which is
answering the question rather than analysing it.

**Naming an analysis in `include` runs it regardless of its gate**, which is
how you get `rdf` out of a run whose trajectory does carry solvent, or a
coordination number between the two groups you meant.

---

## What the analyses actually compute

Details that change the number, and that are worth knowing before comparing
against another tool.

- **RMSD** superposes each frame on the reference before computing it, unless
  `align: false`.

- **Radius of gyration** is mass-weighted by default, which is the physical
  definition; `mass_weighted: false` gives the geometric one.

- **Hydrogen bonds** use Baker–Hubbard at 0.25 nm and 120° by default. Both are
  settings, because published criteria disagree — PLIP allows 4.1 Å and 100°.
  Bonds are counted in every frame they exist, including transient ones.

- **Secondary structure** uses MDTraj's DSSP and excludes anything DSSP cannot
  assign, so a ligand does not appear as coil. There is no option to shell out
  to an external `mkdssp`: a system package is a poor dependency for something
  that already works, and where the two disagree that is a finding about DSSP
  worth reporting rather than configuring around.

- **Q-value** uses a switching function rather than a hard cutoff, with β and λ
  exposed. Its contact set is over *pairs of heavy atoms*, which is the
  published definition; `scheme: residue-closest-heavy` gives the coarser
  reading that takes one distance per residue pair instead, and the two are not
  comparable — on a peeling hairpin they stand 0.26 apart on a scale running
  zero to one. The `selection` narrows it further: `protein` is the all-atom
  quantity, `backbone` reports the fold's topology and ignores side-chain
  repacking, and `name CA` is the coarse-grained quantity from Gō-model work,
  which is a different quantity rather than a rounding of the others. All four
  choices are written to `options.json`, because a Q quoted without them is not
  one number.

- **Clustering** seeds k-means at 42 by default. A clustering that survives a
  change of seed is a finding; one that does not is an artefact of where the
  algorithm started, and the seed is a setting so that can be tested.

- **Contacts and hydrogen bonds** are computed across the periodic boundary where
  the trajectory carries a unit cell.

- **Molecules are made whole when a trajectory is loaded**, and put in one
  periodic copy. The protein and nucleic chains are kept together, and every
  other solute molecule (a ligand, an ion) is moved to the copy whose centre
  is nearest theirs, searched exactly for any box shape. So a radius of
  gyration, SASA or RMSD of a selection that includes a ligand which has left
  the pocket describes the ligand beside the protein, not a box length away.

- **Interaction occupancy per residue** is the union of that residue's atom
  pairs' frames, written to `pl_interactions_by_residue.dat` beside the pair
  table. A residue is named by chain as well where the structure has several
  (`A:SER45`) and by insertion code where it has one (`GLY184A`), in this
  table, the pair table and `pl_contacts` alike. It cannot be recovered from the pair table: pairs firing in the same
  frames give the largest single pair, pairs that never coincide give their
  sum, and every real case lies between.

- **Each interaction's frames** are written to `pl_interactions_frames.json`
  beside the pair table, as runs `[first, last]` of the analysed frames it
  was present in (both included), with its kind, atoms (indices into the
  topology analysed, and their names) and occupancy. The Viewer reads it to
  show what holds the ligand in the frame shown, and when each contact formed
  and broke.

- **Order parameters** are the Lipari–Szabo S² of each backbone N–H, taken as
  the closed form of the correlation plateau after superposition. The alignment
  set is a choice that changes the answer and is recorded. A trajectory too
  short reports S² *too high* rather than too noisy, because motion it never
  saw is indistinguishable from rigidity — so the two halves are compared and
  the values are called an upper bound where they disagree.

- **B-factor comparison** converts a refined B through B = (8π²/3)⟨u²⟩ and
  correlates it with the simulated RMSF. It is a correlation and **not an
  accuracy**: a B carries static disorder and refinement choices, and the
  lattice damps loop motion, so B-factors bound amplitudes from below. No
  regression slope is reported, because the two are not the same quantity.

- **Thermodynamics** reads the state record the simulation wrote and treats
  each column as a correlated series. Density is reported only from a
  constant-pressure run: at fixed volume it is a constant the setup chose, and
  a mean with an error on it would describe arithmetic.

- **g(r)** stops at half the smallest box dimension. Past that the
  minimum-image convention supplies only part of each shell, so the curve falls
  away for a reason belonging to the box rather than the liquid — and it falls
  smoothly enough to read as structure.

---

## Averages on a biased run

A metadynamics trajectory is not a Boltzmann ensemble. The bias flattened it on
purpose, so a mean over its frames is an average over a distribution nobody
wanted, and reported without qualification it reads as a property of the
system.

Where the bias is known it can be undone. Each frame is weighted by
`exp((V − c(t))/RT)`, with `V` the bias **that frame was actually sampled
under** — the hills laid down before it, not the fully deposited surface — and
`c(t)` the Tiwary–Parrinello offset. Both details matter. Weighting by the
final surface inflates early frames, which were sampled under almost no bias at
all. And without `c(t)` the weights rank frames by *when they were written*
rather than by where the system was, because the bias grows as hills
accumulate: on a converged well-tempered test run, the last fifth of the frames
carried the entire weight and an average over five hundred rested on seven of
them.

The corrected value is reported first and the biased one beside it, so the size
of the correction stays visible, and the **effective sample size** is printed
with it rather than in a footnote. A reweighted mean over a thousand frames
whose weight sits in five of them is a mean over five, and there is no
arrangement of a document in which that should be readable without the five.

**What is corrected:** the analyses reporting one value per frame — RMSD,
radius of gyration, hydrogen bonds, SASA, the fraction of native contacts,
ligand RMSD, the coordination number, the end-to-end distance, the distance
between two selections, the area per lipid and the bilayer thickness — along
with cluster populations, which are weighted counts.

**What is not.** Reweighting does not fix which clusters exist: the clustering
ran on the biased frames, so the states themselves are shaped by where the bias
sent the system. It says how often each was really visited, not that the right
ones were found. The dimensionality reduction is not corrected at all, because
a projection is not an average.

**The other two methods get no correction, and this is a property of the
methods rather than a gap.** An umbrella window is a system held where it was
put; its averages describe it there, they are not comparable between windows,
and what combines the windows is the potential of mean force. A steered pull is
not an equilibrium ensemble at all. For both, the averages are still reported —
they describe what the run did — and are labelled as being of a biased ensemble
wherever they appear, including in the GUI's metrics table.

Results land in `analysis/reweighted/`: `reweighted_averages.json` always, and
a `.dat` table and a figure where there were averages to correct. It carries no
`options.json`, because it is not an analysis with options — it is a pass over
the ones that already ran.

---

## Adding your own

The registry is open, and an analysis is one class:

```python
from fastmdxplora.analysis import register_analysis, Analysis
register_analysis("my_analysis", MyAnalysis)
```

See [Developing FastMDXplora](developers.md#writing-an-analysis).

---

## Generated reference

Every registered analysis, from the source. Each is a class with the same
shape: options in the constructor, `compute()` for the numbers, and `run()` to
write the data, the figure and the record of what it did.

The docstrings are longer than reference documentation usually is, because a
docstring is where a decision gets recorded: what an analysis computes, what it
refuses and why, and which choices move the number. That is the material to
read before comparing a result against another tool.

```{eval-rst}
.. automodule:: fastmdxplora.analysis.rmsd
   :members:

.. automodule:: fastmdxplora.analysis.rmsf
   :members:

.. automodule:: fastmdxplora.analysis.rg
   :members:

.. automodule:: fastmdxplora.analysis.sasa
   :members:

.. automodule:: fastmdxplora.analysis.ss
   :members:

.. automodule:: fastmdxplora.analysis.dihedrals
   :members:

.. automodule:: fastmdxplora.analysis.qvalue
   :members:

.. automodule:: fastmdxplora.analysis.order_parameters
   :members:

.. automodule:: fastmdxplora.analysis.bfactor_comparison
   :members:

.. automodule:: fastmdxplora.analysis.thermodynamics
   :members:

.. automodule:: fastmdxplora.analysis.rdf
   :members:

.. automodule:: fastmdxplora.analysis.coordination_number
   :members:

.. automodule:: fastmdxplora.analysis.pair_distance
   :members:

.. automodule:: fastmdxplora.analysis.end_to_end
   :members:

.. automodule:: fastmdxplora.analysis.moments_of_inertia
   :members:

.. automodule:: fastmdxplora.analysis.area_per_lipid
   :members:

.. automodule:: fastmdxplora.analysis.bilayer_thickness
   :members:

.. automodule:: fastmdxplora.analysis.lipid_order
   :members:

.. automodule:: fastmdxplora.analysis.cluster
   :members:

.. automodule:: fastmdxplora.analysis.dimred
   :members:

.. automodule:: fastmdxplora.analysis.hbonds
   :members:

.. automodule:: fastmdxplora.analysis.water_sites
   :members:

.. automodule:: fastmdxplora.analysis.ligand_rmsd
   :members:

.. automodule:: fastmdxplora.analysis.ligand_rmsf
   :members:

.. automodule:: fastmdxplora.analysis.contacts
   :members:

.. automodule:: fastmdxplora.analysis.pl_hbonds
   :members:

.. automodule:: fastmdxplora.analysis.pl_interactions
   :members:

.. automodule:: fastmdxplora.analysis.interaction_summary
   :members:

.. automodule:: fastmdxplora.analysis.interactions
   :members:

.. automodule:: fastmdxplora.analysis.pmf
   :members:

.. automodule:: fastmdxplora.analysis.metad_surface
   :members:

.. automodule:: fastmdxplora.analysis.steered_work
   :members:

.. automodule:: fastmdxplora.analysis.reweight
   :members:

.. automodule:: fastmdxplora.analysis.reweighted_averages
   :members:
```

---

## See also

- **[Reading the results](results.md)** — how an analysis says whether its number is determined
- **[Selections in a Config](selections.md)** — which atoms an analysis reads
- **[Protein-ligand interactions](interactions.md)** — the analysis with the most criteria
