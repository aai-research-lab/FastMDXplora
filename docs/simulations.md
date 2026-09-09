# Simulations beyond a box of water

Version 2.3 could minimise, heat, equilibrate and run production in water.
That covers a soluble protein and not much else. This page covers what 2.4
added: holding parts of a system still while the rest settles, putting a
protein in a lipid bilayer, three ways of biasing a run along a coordinate you
name, and being told what went wrong when one fails.

Each is a setting on the simulation or setup phase, so each is available from
the command line, a config file, and the GUI without anything further. The
three biasing methods take a block of settings rather than one value, and the
GUI gives those a box you write the block into, one setting per line, the
way it appears in a config file.

## Restraints

A structure that has just been minimised is not at equilibrium. Heating it
lets the solvent find its arrangement, and it also lets the solute move: side
chains relax into the space crystal packing left, a ligand drifts out of the
pose that was measured, lipids thin around a protein that has not yet found
its depth.

The conventional remedy is to hold the solute while the solvent equilibrates
around it, and then let go in stages.

```bash
fastmdx explore --system 181L \
  --simulate-restrain "protein and not element H" \
  --output runs/lysozyme
```

That is the short form and it is what most equilibrations want: position
restraints on the given selection at 1000 kJ/mol/nm², released through 500 and
100 to zero as equilibration proceeds. On a solvated peptide, heavy atoms move
about a sixth as far under restraint as free.

The long form takes a list, and four kinds are available:

```yaml
simulation:
  restrain:
    - kind: position
      selection: "protein and not element H"
      force_constant: 1000.0        # kJ/mol/nm^2
    - kind: distance
      selection: "index 412 1055"
      force_constant: 500.0
      target: 0.35                  # nm
    - kind: torsion
      selection: "index 12 13 14 15"
      force_constant: 50.0          # kJ/mol/rad^2
  restraint_release: [1000, 500, 100, 0]
```

Position and distance restraints are in kJ/mol/nm²; angle and torsion are in
kJ/mol/rad². The units differ because the coordinate does, and one number for
both is how an angle restraint ends up a thousand times too weak.

### What a restraint is, and is not

Each is a harmonic penalty: the force grows with the square of the departure,
so a restraint is a spring rather than a wall. A constrained atom cannot move;
a restrained one can, and the restraint says what it cost.

**They are released before production.** A biased production run measures the
bias, and measures of flexibility computed from one -- RMSF, clustering,
dimensionality reduction -- describe the restraint as much as the system.
Keeping them is possible with `restrain_production: true`, which logs what it
costs and records it with the results, so a reader comparing that trajectory
against a free one can tell which they have.

### Two refusals

A selection matching **no atoms** stops the run. A restraint on nothing holds
nothing, and a run that applied it silently would look restrained and not be.

A distance, angle or torsion restraint with **no force constant** stops the
run. There is a conventional value for holding heavy atoms in place and there
is none for a distance, and inventing one would be inventing the strength of a
bias.

## Membrane systems

A membrane protein simulated in water is not the protein: the hydrophobic belt
that sits in the bilayer is exposed to solvent and the helices splay. So it
has to be built as a membrane system.

```bash
fastmdx explore --system 1AFO \
  --setup-forcefield amber14 \
  --setup-membrane POPC --setup-membrane-orient \
  --simulate-restrain "protein and not element H" \
  --output runs/glycophorin
```

That is a membrane protein from a PDB identifier to a finished report in one
command. Seven lipids are available -- POPC, POPE, DLPC, DLPE, DMPC, DOPC and
DPPC -- and OpenMM packs the bilayer, so no external packing tool is needed.

### Orientation

`addMembrane` places the bilayer in the xy plane and assumes the protein is
already lying along z. A structure taken from the PDB usually is not:
crystallographic axes have no relation to a membrane normal, and 1AFO's NMR
frame has its helices lying in the plane the membrane is about to occupy.
Embedding it anyway packs lipids around a protein lying flat in them, the run
completes, and every number describes a structure nobody would recognise.

So the setup phase checks, and `--setup-membrane-orient` rotates the structure
so its longest axis lies along the normal. That is the right answer for a
transmembrane helix or a bundle of them, where the protein is longest along
the direction it spans.

**It is checked rather than trusted.** Two refusals guard it:

- **Before rotating**, whether there is a longest axis worth rotating onto. A
  protein roughly as long in two directions has its "longest" chosen by noise,
  and the same structure from a different starting frame would come out
  differently.
- **After rotating**, whether the result looks like a membrane protein. A
  bilayer-spanning fold has hydrophobic side chains banded around its middle
  and charged ones at the two interfaces. Where the hydrophobic residues are
  not gathered near the centre, either the structure is soluble or the
  rotation put it in the wrong frame.

Neither can tell which way up the protein ends: a rotation putting the
extracellular side down is as valid to the calculation as one putting it up.
Where that matters, the [OPM database](https://opm.phar.umich.edu) publishes
structures oriented against a real transfer energy, and their coordinates can
be used directly. `membrane_orientation_checked: true` proceeds with a
structure as it is, for somebody who knows theirs.

### The barostat

A membrane gets a different one, and this is the part that goes wrong
quietly. An ordinary barostat scales x, y and z together, which squeezes a
bilayer that should be free to change thickness independently of its area --
and area per lipid is what membrane simulations are validated against. The run
completes and is wrong.

FastMDXplora uses `MonteCarloMembraneBarostat` with the plane coupled, the
normal free, and no imposed surface tension. It is chosen from the topology
rather than from a setting, because it has to be right whether or not anybody
remembered to say so.

## Metadynamics

PLUMED can express almost any enhanced-sampling scheme, and the cost of that
is a language to learn before running the commonest one. Most metadynamics on
a protein-ligand system biases one of a handful of things, and those do not
need a language:

```yaml
simulation:
  metadynamics:
    collective_variable: ligand_distance
    site_selection: "resid 84 to 121 and name CA"
    sigma: 0.05
    walls:
      upper: 2.5          # nm; see "Bounding where the ligand goes" below
```

Nine are available: `ligand_rmsd`, `ligand_distance`, `distance`, `angle`,
`torsion`, `radius_of_gyration`, `coordination`, `membrane_depth` and `q`.
`membrane_depth` measures how deep a group sits in a bilayer, and is the one to
reach for on a membrane system. `q` is the fraction of a reference structure's
native contacts, the standard folding coordinate, and it needs a reference
structure the way `ligand_rmsd` does: the set of contacts is fixed by that
structure. It is biased over exactly the contacts the `qvalue` analysis
measures, so the coordinate a surface is drawn along is the one the run
reports. The block becomes PLUMED input, written to
`metadynamics.plumed` beside the results, and the existing PLUMED integration
runs it. Anything more elaborate is still written by hand and passed as
`plumed`, as before -- this is a shorter path to the common case, not a
replacement for the general one.

### Choosing what to bias

This is the decision the method turns on. Metadynamics fills the free energy
landscape along whatever you bias and reports a free energy as a function of
it. If the variable does not distinguish the states that matter -- if two
genuinely different arrangements share a value -- the surface converges and
describes something that is not the system. It does not announce itself, and
running longer does not fix it.

So each variable states what it does **not** separate:

| variable | separates | does not separate |
|---|---|---|
| `ligand_rmsd` | bound from unbound, one pose from another | two unbound arrangements at the same distance |
| `ligand_distance` | depth of binding along one direction | leaving by one route from leaving by another |
| `torsion` | rotameric states of one bond | anything coupled to that bond |
| `radius_of_gyration` | folded from extended | a correct fold from a compact wrong one |
| `distance` | separation of two groups | arrangements putting the centres equally far apart |
| `angle` | a hinge open from a hinge closed | the two ways of reaching the same angle — an angle has no sign, and a torsion does |
| `coordination` | bound from unbound, without breaking when the ligand rotates | one close contact from several distant ones, which is the price of that robustness |
| `membrane_depth` | how deeply something sits in a bilayer, measured against the bilayer's own centre | the two leaflets, unless the sign is kept, or headgroups from passage through them |
| `q` | folded from unfolded, through hundreds of contacts at once rather than one distance | two structures keeping the same contacts in a different arrangement, and anything the reference does not contain — a contact formed only when unfolded is invisible to it |

`coordination` is the second most used variable after `distance`, and
`membrane_depth` is measured against the bilayer's own centre rather than a
fixed plane — a membrane drifts, and depth against a fixed plane becomes depth
against nothing.

Runs are **well-tempered** by default. Plain metadynamics deposits at full
height forever, so the bias never settles and no free energy is recoverable.

`sigma` -- the hill width -- has no default and is refused if missing. It
should be about the size of the fluctuations within a single state, around
0.05 nm for a distance or an RMSD and around 0.35 rad for a torsion. There is
no value that is right for an arbitrary coordinate, and a wrong one either
smears the surface flat or never fills it.

The collective variable and the bias are written to `COLVAR` every deposition,
because a run whose convergence cannot be checked has not measured a free
energy.

### What the analyses do with it

Every analysis still runs on a metadynamics trajectory, and every mean they
report is an average over the flattened ensemble rather than the real one. The
analysis phase corrects the ones a weighted average is meaningful for and
labels the rest; see [Averages on a biased
run](phases.md#averages-on-a-biased-run) for what is corrected, what is not,
and why an umbrella window and a steered pull cannot be.

### Bounding where the ligand goes

A run biasing a ligand's distance will, given time, push the ligand out into
bulk solvent. There the landscape is flat and the accessible volume
effectively infinite, so the bias fills a basin that never fills and the run
never comes back to the question. Such a run is not wrong so much as
unfinishable, and FastMDXplora refuses to start one: `ligand_distance` and
`ligand_rmsd` need a bound, or `unbounded: true` to say you meant it.

**A wall** bounds how far:

```yaml
    walls:
      upper: 2.5           # nm
      kappa: 1000          # kJ/mol/nm^2, how hard it pushes back
```

**A funnel** bounds where, and that is the difference that matters for a
binding free energy. A flat wall still lets the ligand explore a whole shell
of unbound positions at that distance, so the unbound state has no defined
volume and the absolute free energy has no reference. A funnel gives it one: a
cone over the binding site mouth, narrowing into a cylinder out in the
solvent.

```yaml
    funnel:
      axis_selection: "resid 210 to 214 and name CA"   # out towards solvent
      alpha_rad: 0.55              # the cone's half-angle
      switch_distance_nm: 1.5      # where the cone becomes a cylinder
      cylinder_radius_nm: 0.1
```

The axis has to be given. It is the direction the ligand leaves by, and
nothing here can work that out from the structure — a funnel pointed the wrong
way blocks the exit instead of following it. Pick atoms on the far side of the
exit channel from the site.

The funnel is built from `COM`, `DISTANCE`, `CUSTOM` and `UPPER_WALLS`, so it
needs only a standard PLUMED rather than the optional FUNNEL module. The
generated input is in `metadynamics.plumed`, and reading it is the way to
check the geometry is what you meant.

## Pulling along a coordinate

Some things do not happen on their own within reach of a simulation. Steered
MD attaches a spring to a coordinate and moves the anchor, dragging the system
whether or not it wants to go:

```yaml
simulation:
  steered:
    collective_variable: ligand_distance
    site_selection: "resid 84 to 121 and name CA"
    from: 0.4          # nm
    to: 3.0
    steps: 5000000     # over which the anchor travels
```

The same eight variables metadynamics offers, resolved the same way.

**This gives a pathway and the work done along it, not a free energy.** The
work depends on how fast you pull: drag a ligand out in a nanosecond and most
of the work goes into pushing water aside and straining the protein, not into
breaking the interactions you meant to measure. A single fast pull
overestimates a barrier, sometimes by a great deal. Jarzynski's equality
recovers a free energy from an ensemble of pulls, but the average is dominated
by rare low-work trajectories, so it needs many repeats and converges badly
when the pulling is fast.

FastMDXplora reports the pulling rate and the work, and does not claim a free
energy from a steered run.

**What it is genuinely good for is generating starting structures.** Pull
once, take frames along the way, and each is a window for umbrella sampling —
which does give a free energy, from equilibrium sampling at each position
rather than from work done in a hurry.

A run can be steered or biased with metadynamics, not both: they are two ways
of moving the same coordinate, and their forces would add.

## Umbrella sampling

A free energy along a coordinate, from equilibrium sampling at a series of
positions rather than from work done in a hurry:

```yaml
systems:
  - system: 181L
simulation:
  duration_ns: 5
  umbrella:
    collective_variable: ligand_distance
    site_selection: "resid 84 to 121 and name CA"
    from: 0.4          # nm
    to: 2.0
    n_windows: 17
    force_constant: 1000
```

Each window becomes a run. They are scheduled by the same machinery that runs
a multi-system campaign, so `execution.workers` and `execution.devices` pin
them one per GPU exactly as they would separate systems.

### Overlap is what makes it work

Recombination stitches the windows' histograms together. Where two neighbours
never visit the same value there is nothing to stitch: the free energy on one
side cannot be placed relative to the other, and a curve drawn through the gap
is interpolation presented as a measurement.

So overlap is measured and **a gap is reported rather than bridged**, naming
which windows and by how much. What closes a gap is more windows between them,
or a softer force constant so each wanders further. Sampling for longer does
not.

How much is enough is `minimum_overlap`, three per cent by default. That is
enough to stitch and it is thin — on a real study, pairs sharing seven per
cent passed while a reader might reasonably want fifteen. Raise it where the
free energy matters:

```yaml
    minimum_overlap: 0.15
```

The refusal states the threshold it applied, so a genuine gap can be told from
a strict setting.

The `force_constant` therefore has no default: it decides how far a window
wanders and so whether neighbours meet. Too stiff and they do not; too soft
and the system escapes towards the nearest minimum.

### One system, many windows

The windows are the same molecule held at different points along the
coordinate, so FastMDXplora prepares it once — into `shared_setup/` — and every
window simulates from that.

This matters beyond the minutes saved. Solvation does not place water the same
way twice: preparing each window separately gives each its own water, and a
seven-window study came out with 37,212, 37,254, 37,436 and 37,445 atoms. Four
different systems for one measurement, and the difference between windows is
then partly where the solvent happened to land rather than the restraint.

A sweep over `setup` settings turns the sharing off, because the windows are
being asked to be prepared differently and quietly ignoring that would be worse
than preparing seven times.

The same setting is available on its own, for a system prepared elsewhere:

```yaml
simulation:
  prepared_from: runs/reference/setup
```

It names the `setup` directory of a run that completed — the one holding
`system.xml`, `state.xml` and `topology.pdb` — not the run directory above it.

### Where the windows start

Windows started from a single structure are strained at the far end of the
range, and the strain relaxes into the sampling as drift. The usual source is
a steered run: pull once, take a frame near each window's centre, and each
window begins near where it will sit.

The first fifth of each window is discarded before recombination, because a
window begins away from where it settles and counting the approach biases the
histogram towards where the run started.

A recombination is only attempted once the histograms have something in them.
Below `minimum_samples` (200 values per window, after equilibration is
discarded) the overlaps are reported as observations and nothing is concluded
from them: an overlap is the area two histograms share, and from tens of points
that number is noise. A smoke test that wants to reach the recombination can
lower the threshold.

A misspelled setting is refused rather than ignored, with the spelling it was
probably meant to be. Accepting `minimum_ovelap` and dropping it would let a
study stitch at the three per cent default while believing otherwise.

If the windows never reach their centres, the recombination says so rather
than reporting the gap that leaves. A study seeded from one bound structure
came back with four windows held at 0.3, 0.5, 0.7 and 0.9 nm all sampling
below 0.5, and two more held at 1.1 and 1.3 both settled at 1.19 -- the
restraints had lost, and the hole between 1.24 and 1.41 was the symptom. The
remedy there is to seed from a steered run or hold harder; a softer force
constant, which is what a genuine gap wants, would make it worse.

A short run puts windows away from their centres too -- a restraint needs time
to pull a system to where it is held -- so where the sampling is also thin the
two cannot be told apart, and the message says so rather than choosing.

### Two variables at once

A `variables` list of exactly two entries biases both under one deposition,
which is what makes the result a surface rather than two profiles. Each entry
takes the same keys a single-variable block does:

```yaml
metadynamics:
  variables:
    - collective_variable: torsion
      selection: "index 4 6 8 14"
      sigma: 0.35
    - collective_variable: torsion
      selection: "index 6 8 14 16"
      sigma: 0.35
  height_kjmol: 1.2
  pace_steps: 500
```

Deposition settings given at the top level apply to both, because they
describe the hills and there is only one set of those. Each variable keeps
its own selections, its own `sigma` and its own walls.

The surface is judged one dimension at a time, on the free energy along each
variable with the other integrated out. That is the point of doing it that
way: a run can fill a torsion thoroughly while the distance it was also
biasing never left one basin, and a single verdict would either pass it,
hiding the stuck coordinate, or fail it, burying the good one. A refusal
names the dimension it is about.

Periodicity is per variable too. A torsion biased against a distance is
circular in one and not the other, and it is read from each variable's own
definition rather than from a single flag.

### What PLUMED is still needed for

This covers well-tempered metadynamics on one or two of the nine variables,
with walls or a funnel. Beyond that, write PLUMED input and pass it as
`plumed`:

- **three or more collective variables** — a surface across three coordinates
  is not something to read off a page, and this does not generate one
- **reweighting to a variable that was not biased** — averages over the
  biased run are corrected automatically (see
  [Averages on a biased run](phases.md#averages-on-a-biased-run)), but
  projecting the free energy onto a *different* coordinate is PLUMED's job
- **multiple walkers**, path collective variables, and the rest of PLUMED

The two mechanisms are the same underneath; the block is a shorter way to
describe the common case. `plumed` takes the script text itself or a path to
a `.dat` file, and the GUI gives it a control of its own rather than a YAML
box: an on-switch, a Browse for the file, and a place to write the input
directly.

## When a run fails

A simulation that ends with "the coordinates are not finite" has said almost
nothing, and the advice that usually follows -- lower the timestep, lower the
temperature, raise the friction -- is a list of things that sometimes help,
offered without knowing which applies.

The state at failure says more. Which atoms went non-finite, and what they
belong to, distinguishes:

- **a ligand alone**: its parameters, or a pose that was already clashing. No
  timestep is small enough to fix a wrong parameter, so that advice is not
  given here.
- **lipids, or protein next to them**: the bilayer packing, which is what
  restrained equilibration exists to survive.
- **most of the system at once**: an integration failure, which is the case
  the usual advice is actually for.
- **one residue type**: something local -- a strained ring, or an atom that
  preparation added in a poor position.

Where the evidence points nowhere, the message says so. That is more useful
than a confident list that happens not to apply.

Nothing is retried. A run that exploded because its ligand is wrong will
explode again more slowly at half the timestep, and a rescue that produces a
trajectory from a broken system is worse than a failure -- the failure is
visible.
