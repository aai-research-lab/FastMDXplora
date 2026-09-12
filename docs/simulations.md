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
a multi-system campaign, so `execution.workers` and `execution.devices` place
them exactly as they would separate systems.

With several cards, `devices: [0, 1]` pins one window per GPU. With one card,
`workers` on its own puts that many windows on it at once, and for umbrella
sampling that is usually worth doing: a window is a small system and one of
them rarely keeps a large GPU busy. Thirty windows of trypsin and benzamidine,
three at a time on one RTX 4090, ran at about 390 ns/day aggregate — a little
over 21 hours for a study that would otherwise have gone one window after
another. How many fit depends on the system and the card, so try two or three
on a short run and read the ns/day the log reports before committing a long
one. Leave the card to the study while it runs; a second job on it makes that
number about scheduling instead.

```yaml
execution:
  mode: parallel
  workers: 3            # three windows sharing one GPU
  continue_on_error: false
```

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

### When one force constant will not do

A restraint at `k` keeps a window within two sigma of its centre against a
free-energy gradient of `2*sqrt(k*kT)`, and no further. On a coordinate with a
steep stretch — a ligand leaving a salt bridge, a torsion crossing a barrier —
that number decides whether the windows there hold their centres at all:

| k (kJ/mol/nm²) | sigma (nm) | holds against |
| --- | --- | --- |
| 3000 | 0.029 | 173 kJ/mol/nm |
| 6000 | 0.020 | 245 |
| 13000 | 0.014 | 360 |

Raising it everywhere does not work, because sigma falls as `sqrt(kT/k)`: the
windows narrow, the overlap goes with them, and the study refuses for a gap
the stiffening opened. So `force_constant` takes a list as well as a number,
one per window, and `centres` takes the spacing those windows need:

```yaml
  umbrella:
    collective_variable: ligand_distance
    select_atoms: "resSeq 189 to 195 and name CA"
    centres: [0.40, 0.46, 0.51, 0.83, 0.86, 0.89, 0.91, 1.10, 1.16]
    force_constant: [3000, 3000, 3000, 13000, 13000, 13000, 13000, 3000, 3000]
```

Windows held at different constants recombine correctly — each window's bias
is built from its own — so the only thing to get right is the overlap, and
half the spacing buys back what twice the stiffness costs.

**How to know which windows need it, and what to set.** Run the study. A
window that could not be held is a measurement of the surface that beat it —
it comes to rest where the restraint's pull matches the free energy's, so its
displacement times its force constant is the gradient there — and the refusal
does that arithmetic for you, per window:

```
  window   held at    sat at    needs k    at spacing
       0    0.8966    0.8276       4286        0.0482
       1    0.9517    0.8319      12944        0.0278
```

Both columns matter. Raising the constant and leaving the windows where they
are trades a refusal for drift for a refusal for a gap the stiffening opened.

One thing to check before believing the numbers: **a window that did not
start at its centre is not measuring a gradient.** If the windows were not
seeded from a pull near their own centres, fix that first — a constant sized
from a window's starting position is sized from the wrong thing. The refusal
says so before it shows the table.

### Sizing a study from a short pilot

The gradient is what fixes both settings, and every window measures the
gradient where it sits. So a handful of windows, run briefly, size the study
that follows: spread six or so over the range and give each a few hundred
picoseconds.

```yaml
simulation:
  production_steps: 150000        # 300 ps a window
  umbrella:
    collective_variable: ligand_distance
    select_atoms: "resSeq 189 to 195 and name CA"
    from: 0.40
    to: 2.00
    n_windows: 6
    force_constant: 3000
```

Every umbrella study writes the design its own windows imply into `pmf.json`
under `next_study`, and prints it when the study refused or when a window
drifted:

```
Next study:     34 windows from these windows' own gradients, 0.4 to 2,
                worst overlap 0.18 predicted
    centres: [
      0.4000, 0.4565, 0.5059, 0.5486, 0.5844, 0.6153, 0.6431, 0.6710,
      ...
    ]
    force_constant: [
      4870, 5530, 7320, 9840, 12750, 16140, 18050, 19960, 20000, 20000,
      ...
    ]
```

Those two lists go into the config as they are.

**The arithmetic.** A window comes to rest where the restraint's pull matches
the free energy's, so `k` times its displacement is the gradient `G` there.
Two requirements then fix the design together: a window has to stay within
half the distance to its neighbour, which sets the constant from below, and
neighbours have to overlap — `d <= 2.5 sigma` — which sets it from above.
Asking a window to use four fifths of the room it is allowed and solving both
at once leaves

    d = 2.5 kT / G        k = G² / kT

with `kT = 2.494 kJ/mol` at 300 K. A stretch measuring 223 kJ/mol/nm gets
windows 0.028 nm apart held at 20,000; a flat stretch measuring 10 gets the
widest spacing the pilot's own softest constant still overlaps at. Neither
setting is ever loosened past what the pilot ran, since a window sitting on
its centre measures nothing and, unbounded, would ask for infinitely wide
windows.

Three things worth reading beside the answer, all in `next_study`:

- `predicted` says, for every window it proposes, where that window would come
  to rest, how much of its allowance that uses, and the area it would share
  with its neighbour. The design is checked against the gates it will be
  judged by before it runs.
- `measured_over` is where the readings are. A window on a rising surface
  comes to rest below its centre, so the readings stop short of the far end of
  the range, and the stretch beyond them is held at the last slope measured.
- `crossed` names windows that came to rest past one another. That stretch was
  too steep for the pilot's constant to resolve; the design takes the steeper
  reading, and running it again at what it recommends resolves it.

A pilot has to be held from its first step and seeded near its centres, which
is what the umbrella phase does — the point is that nothing else is required
of a pilot. Three hundred picoseconds is enough: a displacement converges like
a mean, and 1,500 samples fix the gradient to about 6 kJ/mol/nm.

### The sphere a distance coordinate leaves open

A window on a distance holds the ligand at a radius and leaves it free to be
anywhere on the sphere of that radius. That is what the `-2kT ln r` in the bulk
reference is about: the room at radius r is `4πr²`, so a free ligand is more
likely to be found at 2 nm than at 1 nm for no energetic reason at all.

Two things have to be true for that reference to mean anything, and on a real
site neither is:

- **The sphere has to be open.** A coordinate measured to a group of backbone
  atoms has its origin inside the protein. On trypsin's S1 site, 12% of the
  sphere at 1.0 nm is outside the protein and 50% at 2.0 — so the room grows as
  `r^4.3`, not `r²`, and no length of run makes `-2kT ln r` the right shape.
- **The ligand has to visit it.** A sphere of radius 2 nm has 50 nm² of
  surface. A ligand held there crosses a few nm² in ten nanoseconds, so one
  window sees a patch.

`cone` answers both. It puts a flat-bottomed wall on the angle between the
site-to-ligand line and an axis fixed in the protein, so the ligand is confined
to a cap:

```yaml
  umbrella:
    collective_variable: ligand_distance
    ligand_name: BEN
    select_atoms: "resSeq 189 to 195 and name CA"
    centres: [...]
    force_constant: [...]
    cone:
      half_angle_deg: 30
      force_constant: 5000        # kJ/mol/rad², the wall
      axis_selection: protein     # what the cone points away from
```

The cap's area is `Ω r²` with `Ω = 2π(1 − cos θ)`, still exactly proportional
to `r²` — so the reference is right by construction — and at 30° the cap is
7% of a sphere, which a ligand covers in a fraction of the time.

The axis is the direction from `axis_selection`'s centre to the site, so
"straight out" is away from the protein and the cone turns with the molecule
rather than pointing at a fixed corner of the box.

**Flat-bottomed, not harmonic.** Inside the cone there is no bias at all, so
what happens there is the system's own. A harmonic restraint on the angle would
pull the ligand towards the axis everywhere, including in the bound state.

**What it costs.** The bulk state under a cone is `4π/Ω` smaller than a free
ligand's, while a bound pose that fits inside the cone loses nothing — so a
binding free energy measured this way is too negative by `kT ln(4π/Ω)` until
that is added back. The study records the number: the plan's `cone` block
carries `share_of_a_sphere` and `correction_kjmol`, computed by integrating the
wall's own Boltzmann factor rather than assuming a hard edge, because the wall
is soft and the ligand leans on it.

**The check that comes with it.** The correction depends on the angle and the
answer must not. Run two cone angles and compare: 20° and 45° differ by
3.8 kJ/mol in the correction, so if the corrected binding free energies agree
the correction is being applied properly, and if they do not, it is not.

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
