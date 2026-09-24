# Your first FastMDXplora study

From nothing to a finished study of a real protein. About ten minutes, most of
which is the install.

---

## Install

```bash
conda create -n fastmdxplora -c conda-forge fastmdxplora
conda activate fastmdxplora
```

Check what you got:

```bash
fastmdx info
```

That lists every backend, grouped by what it is for, and gives the command for
anything missing. If the simulation backends are present you can run everything
on this page.

Other routes, the platforms supported (Linux and macOS), and what to do about a
partial install are in [Installing FastMDXplora](installation.md).

---

## The quickest route: the GUI

```bash
fastmdx gui
```

A tab opens. Type `1L2Y` as the structure — that is Trp-cage, a 20-residue
protein that folds in microseconds and simulates in minutes — leave everything
else alone, and press **Run**.

You will watch the setup phase clean the structure up and solvate it, the
simulation heat and equilibrate it, and then the molecule itself moving in the
viewer while the energy and temperature plot alongside. When it finishes, the
figures and the report are on the same page.

That is the whole loop. [The FastMDXplora GUI](gui.md) covers what else it can
do, including opening a run that happened on a cluster.

---

## The same study from the command line

```bash
fastmdx explore --system 1L2Y --output runs/trpcage
```

One command: it fetches 1L2Y from the PDB, prepares it, simulates it, analyses
the trajectory, and writes a report. The default is a real simulation, so this
takes a while. For something that finishes in a minute, ask for less of it:

```bash
fastmdx explore --system 1L2Y --output runs/smoke \
  --simulate-nvt-steps 500 \
  --simulate-npt-steps 500 \
  --simulate-production-steps 5000 \
  --simulate-trajectory-interval-steps 50
```

That is 10 ps, which is far too short to mean anything — and the report will
tell you so, in as many words. It is for checking the machinery works.

---

## Or describe it in a sentence

```bash
fastmdx agent set                              # choose a model, once
fastmdx agent "simulate Trp-cage for 50 ns" -o trpcage.yml
fastmdx explore --config trpcage.yml
```

The [FastMDXplora Agent](agent.md) writes a Config; what comes out goes through
exactly the same validation as anything you type by hand, and runs the same
way.

---

## What you get

```
runs/trpcage/
├── setup/                  prepared.pdb, solvated.pdb, system.xml, and what was decided
├── simulation/             production.dcd, energy.csv, and the settings used
├── analysis/               one directory per measure: data, figure, and its options
├── report/                 report.md, report.pdf, slides.pptx, dashboard.html
├── manifest.json           what happened: every phase, artifact and parameter
├── resolved_config.yml     what was asked for: a Config that runs this again
└── fastmdxplora.log        the full audit trail
```

Three things are worth opening first.

**`report/report.pdf`** — the study written up, including a methods paragraph
you can paste into a manuscript and a convergence section saying what the run
does and does not support.

**`analysis/rmsd/rmsd.png`** — has the structure settled, or is it still
moving?

**`setup/setup_parameters.json`** — what the setup phase decided about your
structure, and why. Every non-standard residue, every protonation call.

Or open the lot in the GUI:

```bash
fastmdx gui --output runs/trpcage
```

[Reading the results](results.md) is the full map: which record answers which
question, and how a measure says whether its number is one.

---

## It says why, while it happens

Each step explains itself as it runs, with a citation where there is one worth
following:

```
▸ Minimizing energy
  The starting structure has strain in it — atoms slightly too close,
  bonds slightly too long — from the experiment, from adding hydrogens,
  and from dropping the protein into water. At the temperature of a
  simulation that strain becomes violent motion. Minimisation walks the
  structure downhill to a nearby arrangement with no such forces in it,
  before anything moves.
```

Sixteen of them. On by default; `--no-explain` turns them off. See
[How FastMDXplora works](how_it_works.md#explanations-while-it-happens).

---

## A protein with a ligand

Nothing extra to do — hand it a structure that has one:

```bash
fastmdx explore --system 181L --setup-forcefield amber-openff --output runs/lysozyme
```

181L is T4 lysozyme with benzene bound. The setup phase finds the benzene,
looks its chemistry up, settles its protonation in the binding site,
parameterises it with OpenFF, and discards the crystallisation additives that
are not part of the question. The analysis phase then adds the protein–ligand
measures, including what is holding the ligand there rather than just how much
of the protein it touches.

Where a structure is genuinely ambiguous — an unknown residue, a charge that
cannot be settled — setup stops and says what it could not decide, rather than
guessing.

See [Protein-ligand interactions](interactions.md) for what the measures mean.

---

## Doing it repeatedly

For anything beyond a single run, put it in a [Config](config.md):

```bash
fastmdx init-config -o study.yml     # a commented template with every setting
fastmdx explore --config study.yml
```

The same file drives the CLI, the Python API and the GUI. Build one in the GUI
and download it; or write one and open it in the GUI to check before running.

From Python:

```python
import fastmdxplora as fastmdx

runs = fastmdx.FastMDXplora(system="1L2Y", output_dir="runs/trpcage").explore()
print(runs[0].output_dir)
```

---

## When something goes wrong

**The run stops during setup.** Read the message — the setup phase refuses
rather than guesses, and it says what it could not decide and what would settle
it.

**The simulation becomes unstable.** The message names which atoms went wrong
and what that points at: a ligand alone usually means its parameters, lipids
mean the packing, the whole system at once means the integration. The remedies
differ, and it gives the ones that apply.

**A backend is missing.** `fastmdx info` says which and how to get it.

**The numbers look odd.** Read the convergence section of the report before
anything else. A short run has almost no independent information in it, and the
report says how much.

Every refusal carries an identifier as well as a sentence — see
[FastMDXplora refusals](refusals.md).

---

## Where next

- **[How FastMDXplora works](how_it_works.md)** — what each phase decides
- **[The FastMDXplora Config](config.md)** — writing a study down
- **[Worked examples](examples.md)** — recipes for common studies
- **[Studies beyond a box of water](studies.md)** — restraints, membranes, enhanced sampling
- **[Reading the results](results.md)** — what each number is worth
