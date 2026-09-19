# FastMDXplora Config reference

Every setting a Config accepts, by phase, grouped the way the
[GUI](gui.md) form and `fastmdx explore --help` present them — all three read
from the same declaration, so moving between them is not learning a second
arrangement.

**This page is written by hand and the software is not.** The authoritative
list is always generated:

```bash
fastmdx init-config -o study.yml   # every setting, with its help, as a template
fastmdx explore --help             # every setting, as a flag
fastmdx gui                        # every setting, as a form
```

For how a Config is put together, see [The FastMDXplora Config](config.md).
For the flag spelling of any setting below, see
[The FastMDXplora CLI](cli.md).

---

## Top level — 7 settings

| Setting | Type | Default | What it does |
|---|---|---|---|
| `output` | str | `./fastmdxplora_<system>_study_<UTC timestamp>` | Where everything goes |
| `include` | list | all four | Phases to run: `setup`, `simulation`, `analysis`, `report` |
| `exclude` | list | — | Phases to skip. Mutually exclusive with `include` |
| `explain` | bool | `true` | Say why each step is happening, as it happens |
| `verbose` | bool | `false` | Stream debug logging to the terminal |
| `agent` | str | — | `assisted`, `autonomous`, `unvalidated` — see [the Agent](agent.md) |
| `agent_model` | str | — | Which model wrote it, as `provider/model` |

`systems`, `sweep` and `execution` are also top-level; they are described in
[The FastMDXplora Config](config.md).

---

## `setup` — 43 settings

### How this phase was written

| Setting | Type | Default | What it does |
|---|---|---|---|
| `agent` | str | — | This phase's [Agent](agent.md) mode, where it differs from the study's |

### The structure

What is kept, what is repaired, and how it is protonated.

| Setting | Type | Default | What it does |
|---|---|---|---|
| `ph` | float | `7.4` | pH for hydrogen placement, which sets protonation states. Bounds 0–14 |
| `protonation_margin` | float | `1.0` | How close a ligand's pKa may come to the pH before setup stops rather than pick a charge state |
| `heterogens` | str | `auto` | `auto` decides per component; `drop` removes them all; `keep` retains them |
| `keep_heterogens` | bool | `false` | Equivalent to `heterogens: keep` |
| `keep_water` | bool | `false` | Retain crystallographic waters |
| `replace_nonstandard_residues` | bool | `true` | Substitute selenomethionine, oxidised cysteine and the like with their standard equivalents |
| `chains` | list | every chain | Chains to simulate, by deposited ID — `[A, B]` |
| `build_missing_termini` | bool | `false` | Build unresolved residues past the ends of a chain, not only the gaps between resolved ones |
| `fixed_pdb` | str | — | An already-fixed PDB to use directly, skipping PDBFixer |
| `mutations` | list | — | Point substitutions, as `L99A` or `LEU-99-ALA` |
| `mutation_chain` | str | first chain | Which chain the mutations apply to |

A deposited entry is what the experiment produced, not what anyone means to
simulate — hence `chains`. `build_missing_termini` is off because a gap is
pinned at both ends and a terminus is not: what gets built there is a guess
with one end free.

### The ligand

| Setting | Type | Default | What it does |
|---|---|---|---|
| `ligand` | str or list | — | SDF or MOL2 file(s). Requires `forcefield: amber-openff` |
| `ligand_name` | str | `LIG` | Residue name assigned to the ligand |
| `ligand_resname` | str | — | The same setting under the spelling the biasing blocks use. `ligand_name` wins if both |
| `ligand_forcefield` | str | per force field | OpenFF small-molecule force field, e.g. `openff-2.2.1` |
| `ligand_net_charge` | int | from the file | Formal net charge. **Checked against the file, never overriding it** |
| `ligand_pose` | str | `auto` | Where the starting coordinates come from: `auto`, `structure` or `file` |
| `check_ligand_clashes` | bool | `true` | Refuse a pose that severely overlaps the protein |
| `ligand_clash_threshold_nm` | float | `0.15` | What counts as a clash |

`ligand_pose: auto` takes the pose from the structure where it holds a residue
of that name, and from the supplied file where it does not. That is usually
right, and [Worked examples](examples.md#a-protein-with-a-ligand) covers the
two cases and how to pin either.

`ligand_pose` accepts any string — the three values above are the ones it acts
on.

### The membrane

| Setting | Type | Default | What it does |
|---|---|---|---|
| `membrane` | str | — | `POPC`, `POPE`, `DLPC`, `DLPE`, `DMPC`, `DOPC` or `DPPC` |
| `membrane_orient` | bool | `false` | Rotate the structure so its longest axis lies along the membrane normal |
| `membrane_orientation_checked` | bool | `false` | Proceed with the structure's orientation as it is |

See [Studies beyond a box of water](studies.md#membrane-systems) — the
orientation check has two refusals and a barostat that is chosen for you.

### Solvent, ions and the box

| Setting | Type | Default | What it does |
|---|---|---|---|
| `water_model` | str | from the force field | e.g. `tip3p`, `tip4pew` |
| `solvent_padding_nm` | float | `1.0` | Minimum distance from solute to box wall |
| `box_shape` | str | `dodecahedron` | `cube`, `dodecahedron` or `octahedron` |
| `neutralize` | bool | `true` | Add ions to neutralise the net solute charge |
| `ion_positive` | str | `Na+` | Counter-ion cation |
| `ion_negative` | str | `Cl-` | Counter-ion anion |
| `ion_concentration_M` | float | `0.15` | Ionic strength in molar. Bounds 0–20 |

A dodecahedron holds the same clearance around the solute in roughly 71% of a
cube's volume, so about a third of the water a cube would need is saved.

### The force field

| Setting | Type | Default | What it does |
|---|---|---|---|
| `forcefield` | str | `auto` | `auto`, `amber-fb15`, `amber-openff`, `amber14`, `charmm36` |
| `force_field` | list | — | Raw OpenMM XML file(s). Overrides `forcefield` |
| `constraints` | str | `HBonds` | `None`, `HBonds`, `AllBonds` or `HAngles` |
| `rigid_water` | bool | `true` | Constrain water bond lengths and angles |
| `hydrogen_mass_amu` | int or float | off | Hydrogen-mass repartitioning, which allows a longer timestep |
| `temperature_K` | int or float | `300.0` | Temperature for **initial velocity assignment** — not the production temperature, which is `simulation.temperature_K` |

Only a ligand-capable force field accepts `setup.ligand`; `amber-openff` is the
one to reach for.

`constraints` accepts any string — the four values above are the ones OpenMM
acts on, so check the spelling.

### How forces are computed

Defaults suit a solvated protein. Changing one changes the physics.

| Setting | Type | Default | What it does |
|---|---|---|---|
| `nonbonded_method` | str | `PME` | `NoCutoff`, `CutoffNonPeriodic`, `CutoffPeriodic`, `PME`, `Ewald` |
| `nonbonded_cutoff_nm` | float | `1.0` | Real-space cutoff |
| `ewald_error_tolerance` | float | `0.0005` | Ewald/PME error tolerance |
| `use_switching_function` | bool | `true` | Apply a switching function near the cutoff |
| `switch_distance_nm` | int or float | 0.9 × cutoff | Where the switching function turns on |
| `dispersion_correction` | bool | `true` | Long-range dispersion (vdW tail) correction |
| `remove_cm_motion` | bool | `true` | Add a centre-of-mass motion remover |

The cutoff is taken from the force field when you do not set it —
see [How FastMDXplora works](how_it_works.md#the-nonbonded-cutoff-comes-from-the-force-field).

---

## `simulation` — 41 settings

### How this phase was written

| Setting | Type | Default | What it does |
|---|---|---|---|
| `agent` | str | — | This phase's [Agent](agent.md) mode |

### How long it runs

Production length, and the equilibration before it. **Equilibration is
independent of production length** — a 500 ns production run gets the same
1.5 ns of equilibration as a 2 ns one unless you say otherwise.

| Setting | Type | Default | What it does |
|---|---|---|---|
| `duration_ns` | int or float | 2 ns | Production length |
| `nvt_duration_ns` | int or float | 500 ps | NVT equilibration |
| `npt_duration_ns` | int or float | 1 ns | NPT equilibration |
| `production_steps` | int | 1,000,000 | Overrides `duration_ns` |
| `nvt_steps` | int | 250,000 | Overrides `nvt_duration_ns` |
| `npt_steps` | int | 500,000 | Overrides `npt_duration_ns` |

### Where it starts

| Setting | Type | Default | What it does |
|---|---|---|---|
| `setup_from` | str | — | A finished study or setup directory to simulate from instead of running setup again |
| `prepared_from` | str | — | The earlier name for `setup_from`, still accepted |
| `resume_from` | str | — | A checkpoint this run continues from |
| `minimize` | bool | `true` | Run energy minimisation before equilibration |
| `minimize_tolerance_kjmol_per_nm` | int or float | `10.0` | Minimisation force tolerance |
| `minimize_max_iterations` | int | `0` | `0` means until convergence |

`setup_from` accepts the study directory — `runs/reference` — and finds the
`setup/` inside. **Naming one turns preparation off**, because solvation does
not place water the same way twice: preparing a second system gives a second
set of atoms, and anything taken from the named one then belongs to a different
molecule.

`resume_from` is only valid for the exact system, platform and precision the
checkpoint was written from; loading refuses rather than proceeding on a
mismatch. Metadynamics and steered runs cannot be split at all — see
[Long runs and segments](production.md#long-runs-and-segments).

### Conditions

| Setting | Type | Default | What it does |
|---|---|---|---|
| `temperature_K` | int or float | `300.0` | Production temperature |
| `pressure_bar` | int or float | `1.0` | Monte Carlo barostat pressure (OpenMM-native) |
| `pressure_atm` | int or float | — | The same in atm. `pressure_bar` wins if both |
| `friction_per_ps` | int or float | `1.0` | Langevin thermostat friction |
| `barostat_frequency` | int | `25` | Volume-move attempt interval in steps |

### The integrator

| Setting | Type | Default | What it does |
|---|---|---|---|
| `integrator` | str | `langevin_middle` | `langevin_middle`, `langevin`, `brownian`, `verlet`, `variable_langevin`, `variable_verlet` |
| `timestep_fs` | int or float | `2.0` | Integrator timestep |
| `integrator_error_tolerance` | float | `0.001` | Variable-timestep integrators only |
| `random_seed` | int | unset | Integrator seed. Fixes the dynamics, not the solvation |

### Enhanced sampling

Each is a **block of settings**, not a value. All four are Config-only in
practice — the command line has a flag for each, but a flag cannot carry a
mapping.

| Setting | Type | What it does |
|---|---|---|
| `umbrella` | mapping | A free energy along a coordinate, from equilibrium sampling at a series of positions |
| `steered` | mapping | Pull along a coordinate at constant velocity and record the work |
| `metadynamics` | mapping | Fill the free energy landscape along a named coordinate |
| `plumed` | mapping | `enabled` and `script` — raw PLUMED input, as text or a path |

`--simulate-plumed-script PATH` is the exception: it sets
`plumed: {enabled: true, script: …}` on the `explore` command.

Every key each block takes is in
[Studies beyond a box of water](studies.md).

### Restraints

| Setting | Type | Default | What it does |
|---|---|---|---|
| `restrain` | str or list | — | What to hold still during equilibration. A selection, or a list of restraint blocks |
| `restraint_release` | list | `[1000, 500, 100, 0]` | Force constants stepped through during equilibration, in kJ/mol/nm² |
| `restrain_production` | bool | `false` | Keep restraints on during production |

`restrain_production` is off because a biased production run measures the bias:
RMSF, clustering and dimensionality reduction would describe the restraint as
much as the system. Turning it on logs what it costs and records it with the
results.

### Where it runs

| Setting | Type | Default | What it does |
|---|---|---|---|
| `platform` | str | `auto` | `auto`, `CUDA`, `OpenCL`, `CPU`, `HIP` |
| `precision` | str | `mixed` | `single`, `mixed`, `double` |
| `device_index` | str | — | GPU index on a multi-GPU machine, e.g. `"0"` or `"0,1"` |

`auto` takes the fastest available and says which it chose. Ask for a platform
by name when you are checking whether the hardware is reachable at all — an
explicit request fails loudly where `auto` falls back.

### What gets written

| Setting | Type | Default | What it does |
|---|---|---|---|
| `trajectory_interval_steps` | int | adaptive, ≈2000 frames | DCD reporter interval |
| `state_interval_steps` | int | `1000` | Energy/state reporter interval |
| `checkpoint_interval_steps` | int | `10000` | Binary checkpoint interval. `0` disables |
| `save_selection` | str | `not water` | Which atoms go into the trajectory |

Water is nine tenths of a solvated system and nine tenths of the file: a 20 ns
run of a small protein is about 740 MB with it and under 80 without. What is
saved comes with `trajectory_topology.pdb` beside it, and the whole system
stays in `setup/`.

Set `save_selection: all` where the solvent is the subject — `water_sites`,
`rdf` and `coordination_number` need it — and for any run whose frames will
seed another study, because a seed is a complete set of positions and a subset
cannot become one.

### Watching it run

Nothing leaves the machine: these are files in the run directory that the
[GUI](gui.md) reads.

| Setting | Type | Default | What it does |
|---|---|---|---|
| `live_telemetry` | bool | `true` | Write `live_status.json`, `live_metrics.csv`, `live_events.log` and live-frame PDBs |
| `telemetry_interval` | int | `1000` | Minimum step interval between telemetry updates |
| `dashboard_ligand_resname` | str | auto-detected | Which residue the viewer treats as the ligand |
| `dashboard_binding_pocket_cutoff_A` | float | `5.0` | How near counts as the pocket |
| `dashboard_max_playback_frames` | int | `200` | Frames the playback panel will load |

---

## `analysis` — 13 settings

### How this phase was written

| Setting | Type | Default | What it does |
|---|---|---|---|
| `agent` | str | — | This phase's [Agent](agent.md) mode |

### What to measure

| Setting | Type | Default | What it does |
|---|---|---|---|
| `include` | list | the default plan | Only these analyses |
| `exclude` | list | — | Everything but these. Mutually exclusive with `include` |
| `options` | mapping | — | Per-analysis settings, keyed by analysis name |

The 27 names `include` and `exclude` accept:

```
rmsd  rmsf  rg  hbonds  ss  sasa  dihedrals  qvalue  cluster  dimred
water_sites  ligand_rmsd  ligand_rmsf  pl_contacts  pl_hbonds  pl_interactions
order_parameters  bfactor_comparison  thermodynamics  rdf  coordination_number
end_to_end  moments_of_inertia  pair_distance  pmf  metad_surface  steered_work
```

Several do not run by default and are gated on what the run contains — a
ligand, water, a periodic box, a biased run. Naming one in `include` runs it
anyway. [The FastMDXplora analyses](analyses.md) has what each measures and
what gates it.

### What to measure it on

| Setting | Type | Default | What it does |
|---|---|---|---|
| `trajectory` | str | `simulation/production.dcd` | The trajectory to measure |
| `topology` | str | `simulation/trajectory_topology.pdb`, else `simulation/topology.pdb` | The topology that matches it |
| `select_atoms` | str | — | Which atoms. Overrides `scope` |
| `selection` | str | — | The earlier name for `select_atoms`. `selection` wins if both |
| `scope` | str | `solute` | `solute`, `protein`, `ligand`, `all` |

`scope` is shorthand that resolves to a real selection — see
[Selections in a Config](selections.md#what-an-analysis-measures).

### Which frames

| Setting | Type | Default | What it does |
|---|---|---|---|
| `first` | int | `0` | First frame |
| `last` | int | end | Last frame, exclusive |
| `stride` | int | `1` | Take every *n*th frame |

### What the figures look like

| Setting | Type | Default | What it does |
|---|---|---|---|
| `figure_colours` | str | `colour` | `colour`, `greyscale` or `both` |

`both` writes `<name>_greyscale.png` beside each figure. American spellings of
the values are accepted.

---

## `report` — 11 settings

### How this phase was written

| Setting | Type | Default | What it does |
|---|---|---|---|
| `agent` | str | — | This phase's [Agent](agent.md) mode |

### What it says

| Setting | Type | Default | What it does |
|---|---|---|---|
| `title` | str | from the system name | The study's title |
| `author` | str | — | Who it is by |
| `include_methods` | bool | `true` | The methods paragraph |
| `include_reproducibility` | bool | `true` | The reproducibility section |
| `region_highlights` | list | — | Residue ranges to shade on the RMSF trace and colour on the structure |
| `comparison` | bool | `true` | The cross-run comparison, for a study of more than one run |

`region_highlights` entries take `label`, `start`, `end` and an optional
`color` — see
[How FastMDXplora works](how_it_works.md#highlighting-regions-you-care-about).

### What comes out

| Setting | Type | Default | What it does |
|---|---|---|---|
| `document` | bool | `true` | `report.md` |
| `pdf` | bool | `true` | `report.pdf`, from `report.md`. Needs WeasyPrint |
| `slides` | bool | `true` | `slides.pptx`, with `slides_outline.md` as a fallback |
| `bundle` | bool | `true` | `project_bundle.zip` |

`dashboard.html` is written whichever of these are off — it has no setting.

---

## `execution` — 4 settings

| Setting | Type | Default | What it does |
|---|---|---|---|
| `mode` | str | `sequential` | `sequential` or `parallel` |
| `workers` | int | one per device, else CPU count capped at the run count | How many runs at once |
| `devices` | list | — | GPU indices; runs are pinned round-robin |
| `continue_on_error` | bool | `true` (`false` for umbrella) | Carry on when one run fails |

Setting `workers` or `devices` implies `mode: parallel` — see
[The FastMDXplora Config](config.md#execution).
