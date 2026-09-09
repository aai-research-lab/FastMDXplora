# Command-line reference

```bash
fastmdx explore --system 1UBQ --output runs/study
```

That is the shape of it: a command, a structure, somewhere to put the results.

---

## The commands

| | |
|---|---|
| `fastmdx explore` | all four phases |
| `fastmdx setup` | prepare a system and stop |
| `fastmdx simulate` | run dynamics on a prepared system |
| `fastmdx analyze` | measure a trajectory |
| `fastmdx report` | write up an existing run |
| `fastmdx gui` | the [GUI](gui.md) |
| `fastmdx info` | what is installed, and how to get what is not |
| `fastmdx select` | what a selection matches, before a run uses it |
| `fastmdx init-config` | write a commented config template |

---

## How the flags are named

Every setting belongs to a phase, and its flag is the phase and the setting:

```
--{phase}-{setting}
```

So `setup.ph` is `--setup-ph`, `simulation.timestep_fs` is
`--simulate-timestep-fs`, `report.pdf` is `--report-pdf` (and
`--report-no-pdf` to turn it off).

That is not a convention somebody maintains — the flags are **generated from
the same declaration** the config file and the GUI are built from, so a
setting that exists has a flag, and a flag that exists does something.

**The authoritative list is `--help`:**

```bash
fastmdx explore --help          # every flag, with what it does
fastmdx simulate --help         # just the simulation ones
```

It is generated, so it is never out of date. This page covers the ones you
will reach for.

---

## Input and output

```bash
--system 1UBQ                   # a PDB identifier
--system protein.pdb            # or a file
--output runs/study             # where everything goes
--config study.yml              # or take it all from a file
```

The form of `--system` is detected, so there is no separate flag for a PDB
identifier.

---

## Running part of it

```bash
fastmdx explore --system 1UBQ --output runs/study \
  --include setup simulation          # stop after the trajectory

fastmdx analyze \
  --trajectory production.dcd \
  --topology system.pdb \
  --output runs/analysis              # measure something you already have
```

---

## A first run that finishes quickly

The defaults are a real simulation. For checking the machinery:

```bash
fastmdx explore --system 1L2Y --output runs/smoke \
  --simulate-nvt-steps 500 \
  --simulate-npt-steps 500 \
  --simulate-production-steps 5000 \
  --simulate-trajectory-interval-steps 50
```

Ten picoseconds, which the report will tell you supports nothing.

---

## The flags you will actually use

**How long**

```bash
--simulate-duration-ns 100            # or the three step counts separately
--simulate-timestep-fs 2.0
```

**Conditions**

```bash
--simulate-temperature-K 310
--setup-ph 7.0
--setup-ion-concentration-M 0.15
```

**Force field**

```bash
--setup-forcefield amber14            # protein only
--setup-forcefield amber-openff       # and a ligand
```

**Which analyses**

```bash
--analyze-analyses rmsd rmsf ss       # only these
--analyze-exclude-analyses sasa       # everything but
```

**Where it runs**

```bash
--simulate-platform CUDA
--simulate-device-index 0
--simulate-precision mixed
```

**Enhanced sampling, membranes, restraints** — see
[Beyond a box of water](simulations.md).

---

## Checking things

```bash
fastmdx --version
fastmdx info                          # backends, and what to install
fastmdx explore --config study.yml --dry-run   # validate without running

# What a selection matches, before a study depends on it. A selection that
# matches the wrong atoms is not an error and nothing downstream detects it.
fastmdx select "resSeq 189 to 195 and name CA" -s trypsin.pdb
fastmdx select "protein and name CA" -s prepared.pdb --atoms --limit 0
```

`select` exits non-zero when the expression matches nothing, so it can gate a
script. See [Selections](selections.md).

---

## See also

- [Configuration](configuration.md) — the same settings in a file
- [Worked examples](usage_examples.md) — complete recipes
