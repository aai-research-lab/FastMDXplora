# The FastMDXplora CLI

```bash
fastmdx explore --system 1UBQ --output runs/study
```

A command, a structure, somewhere to put the results. That is the shape of it.

The CLI writes a [Config](config.md) from its flags and runs it. Anything you
can express as flags you can express as a Config, and for anything beyond a
single run the Config is easier to keep.

Two command names are installed and they are the same program: **`fastmdx`**
and **`fastmdxplora`**. Everything below uses `fastmdx`.

---

## The commands

| | |
|---|---|
| `fastmdx explore` | Run all four phases |
| `fastmdx xplore` | An exact alias of `explore` |
| `fastmdx setup` | Prepare a system and stop |
| `fastmdx simulate` | Run dynamics on a prepared system |
| `fastmdx analyze` | Measure a trajectory |
| `fastmdx report` | Write up an existing run |
| `fastmdx agent` | Write a Config from a sentence — [the Agent](agent.md) |
| `fastmdx gui` | Serve [the GUI](gui.md) |
| `fastmdx init-config` | Write a commented Config template |
| `fastmdx select` | Show what a selection matches, before a run depends on it |
| `fastmdx info` | What is installed, and how to get what is not |

Plus two global flags, which go **before** the subcommand:

```bash
fastmdx --version     # or -V
fastmdx --cite        # the citation and DOI
```

`fastmdx` with no subcommand at all opens the GUI on the current directory.

---

## How the flags are named

Every setting belongs to a phase, and its flag is the phase and the setting:

```
--{phase}-{setting}          on explore / xplore
--{setting}                  on that phase's own command
```

So `setup.ph` is `--setup-ph` on `explore` and `--ph` on `fastmdx setup`.
`simulation.timestep_fs` is `--simulate-timestep-fs` or `--timestep-fs`. The
prefixes are `setup`, `simulate`, `analyze`, `report` and `execution` — note
that they are the **command** names, while `--include`/`--exclude` take the
**phase** names (`simulation`, `analysis`).

A boolean setting that defaults on gets a `--no-` flag to turn it off;
`--report-pdf` and `--report-no-pdf` both exist.

**The flags are generated from the same declaration** the Config file and the
GUI are built from, so a setting that exists has a flag and a flag that exists
does something. There are around 125 of them across the phases. The
authoritative list is `--help`, which is generated and therefore never out of
date:

```bash
fastmdx explore --help          # every flag, in the same groups the GUI uses
fastmdx simulate --help         # just the simulation ones
```

This page covers the structure, the commands, and the flags you will actually
reach for.

---

## `explore` / `xplore`

All four phases. Every flag on this page's later sections is available here
with its phase prefix.

### Input and output

These five are on `explore` and on each of the four phase commands.

| Flag | What it does |
|---|---|
| `-s`, `-system`, `--system` | A `.pdb`/`.cif` path, or a 4-character PDB ID. The form is detected — there is no separate flag for an identifier |
| `-c`, `-config`, `--config` | A Config file. **Flags override what the file says** |
| `--output DIR` | Where everything goes |
| `--verbose` | Also stream debug logging to the terminal |
| `--no-explain` | Turn off the running explanations. There is no positive `--explain`; they are on by default |

The single-dash long forms (`-system`, `-config`) are there because GROMACS,
AMBER and NAMD all spell flags that way.

### Choosing phases

| Flag | What it does |
|---|---|
| `--include PHASE [PHASE …]` | Only these phases. Accumulates across repeated uses |
| `--exclude PHASE [PHASE …]` | Everything but these. **Mutually exclusive with `--include`** |
| `--no-report` | Shorthand for adding `report` to `--exclude` |

```bash
fastmdx explore --system 1UBQ --output runs/study --include setup simulation
```

Giving both `--include` and `--exclude` exits **2** with a message.

### Running it or not

| Flag | What it does |
|---|---|
| `--dry-run` | Validate everything, print the plan, run nothing. Creates no output directory |
| `--force-overwrite`, `--force` | Run into an output directory that already holds results |

Without `--force`, a second run into an occupied directory is refused. The
check looks only at the phases *this* run will produce, so running `analyze`
into a directory that already holds a finished `simulation/` is the intended
workflow and needs no flag.

### Scheduling

Only on `explore`/`xplore`.

| Flag | Default | What it does |
|---|---|---|
| `--execution-mode` | `sequential` | `sequential` or `parallel` |
| `--execution-workers N` | inferred | How many runs at once |
| `--execution-devices …` | — | GPU indices; one run pinned per device |
| `--execution-continue-on-error` / `--execution-no-continue-on-error` | `true` | Carry on when one run fails |

---

## `setup`

Prepare a system and stop. Flag names here drop the `--setup-` prefix.

**How long / what to keep**

```bash
--ph 7.0
--heterogens auto|drop|keep
--keep-water
--chains A B
--mutations L99A --mutation-chain A
--fixed-pdb prepared.pdb          # skip PDBFixer
```

**The ligand**

```bash
--ligand ligand.sdf [more.sdf ...]
--ligand-name BNZ
--ligand-net-charge 0
--ligand-forcefield openff-2.2.1
--no-ligand-clash-check
```

**The force field**

```bash
--forcefield amber14              # protein only
--forcefield amber-openff         # and a ligand
--force-field amber14-all.xml amber14/tip3pfb.xml    # raw OpenMM XMLs
--water-model tip3p
--constraints HBonds
--hydrogen-mass-amu 4.0
```

**The box**

```bash
--solvent-padding-nm 1.2
--box-shape dodecahedron
--ion-concentration-M 0.15
--ion-positive K+ --ion-negative Cl-
--no-neutralize
```

**The membrane**

```bash
--membrane POPC --membrane-orient
```

**How forces are computed**

```bash
--nonbonded-method PME
--nonbonded-cutoff-nm 1.0
--no-use-switching-function
--no-dispersion-correction
```

Every other `setup` setting has a flag too, spelled from its name. See
[Config reference](config_reference.md#setup--43-settings) and
`fastmdx setup --help`.

---

## `simulate`

**How long**

```bash
--duration-ns 100                 # production; equilibration is separate
--nvt-duration-ns 0.5
--npt-duration-ns 1.0
--production-steps 50000000       # or the three step counts directly
--nvt-steps 250000
--npt-steps 500000
--timestep-fs 2.0
```

**Conditions**

```bash
--temperature-K 310
--pressure-bar 1.0                # or --pressure-atm
--friction-per-ps 1.0
--integrator langevin_middle
--random-seed 42
```

**Where it runs**

```bash
--platform CUDA
--precision mixed
--device-index 0
```

**Where it starts**

```bash
--setup-from runs/reference       # reuse a prepared system; pair with --exclude setup
--resume-from runs/seg0/simulation/checkpoint.chk
--no-minimize
```

**What gets written**

```bash
--trajectory-interval-steps 5000
--state-interval-steps 1000
--checkpoint-interval-steps 10000
--save-selection all              # default is "not water"
```

**Restraints and PLUMED**

```bash
--restrain "protein and not element H"
--restraint-release 1000 500 100 0
--restrain-production
--plumed-script bias.dat          # explore only; see below
```

**Enhanced sampling blocks** — `umbrella`, `steered` and `metadynamics` are
mappings, not values. Flags exist for them but cannot carry a mapping; write
them in a [Config](studies.md) instead.

---

## `analyze`

```bash
--trajectory production.dcd
--topology system.pdb
--analyses rmsd rmsf rg ss        # only these
--exclude-analyses sasa dimred    # everything but
--selection "protein and name CA"
--scope solute|protein|ligand|all
--stride 2 --first 100 --last 5000
--figure-colours colour|greyscale|both
--ligand-resname BNZ
```

`--analyses` and `--exclude-analyses` are mutually exclusive. Unlike
`--include`, a second use **replaces** rather than accumulates.

Six per-analysis settings have convenience flags of their own:

```bash
--cluster-methods kmeans hierarchical
--cluster-n-clusters 8
--cluster-features rmsd|coordinates
--cluster-linkage ward|complete|average|single
--dimred-methods pca tsne umap
--dimred-components 3
```

Anything else goes under `analysis.options` in a Config.

---

## `report`

```bash
--title "Trypsin–benzamidine, 100 ns"
--author "A. Researcher"
--no-document --no-slides --no-pdf --no-bundle
--no-methods --no-reproducibility
```

`report` does not need `--system`: it recovers it from the run's
[Manifest](manifest.md).

```bash
fastmdx report --output runs/study --no-slides --no-bundle
```

---

## `agent`

Write a Config from a sentence. Full documentation:
[The FastMDXplora Agent](agent.md).

```bash
fastmdx agent set                                        # choose a model, once
fastmdx agent "simulate ubiquitin at pH 6.5 for 50 ns"
fastmdx agent -f request.txt -o study.yml
fastmdx agent                                            # opens the GUI Agent panel
```

| Flag | Default | What it does |
|---|---|---|
| `request` (positional) | — | The study, in plain language. The literal word `set` runs the model chooser instead |
| `-f`, `-file`, `--file FILE` | — | Read the request from a file |
| `-o`, `--output FILE` | — | Also write the Config here; it prints either way |
| `--phases PHASES` | `setup,simulation` | Which phases to write, comma-separated |
| `--attempts N` | `3` | Self-correction cycles before giving up |
| `--assisted` | *(default)* | Draft it and stop |
| `--autonomous` | — | Draft it and run it. **Requires `--budget-hours`** |
| `--unvalidated` | — | Mark the output as having gone outside the schema |
| `--budget-hours H` | — | Maximum GPU time, checked after setup |
| `--host` / `--port` / `--no-browser` | `127.0.0.1` / `8765` | For the no-request panel route |

The three mode flags are mutually exclusive.

---

## `gui`

```bash
fastmdx gui                        # design a new study
fastmdx gui --output runs/study    # open a run, live or finished
```

| Flag | Default | What it does |
|---|---|---|
| `--output DIR` | current directory, in "home" mode | The run to watch or read |
| `--host` | `127.0.0.1` | What to bind to |
| `--port` | `8765` | Which port. If busy, the next free one is used and reported |
| `--no-browser` | opens a tab | Print the URL instead |
| `--ligand-resname NAME` | auto-detected | Which residue the viewer treats as the ligand |
| `--binding-pocket-cutoff-A X` | `5.0` | How near counts as the pocket |

**These six are the whole of `fastmdx gui`.** The dashboard flags below —
`--dashboard-host`, `--dashboard-port` and the rest — belong to `explore` and
the four phase commands, not to this one.

---

## Watching a run from the same command

Any of `explore`, `xplore`, `setup`, `simulate`, `analyze` and `report` can
start the GUI alongside itself:

```bash
fastmdx explore --config study.yml --dashboard --dashboard-stop-on-complete
```

| Flag | Default |
|---|---|
| `--dashboard`, `--live-dashboard` | off |
| `--dashboard-host` | `127.0.0.1` |
| `--dashboard-port` | `8765` |
| `--dashboard-stop-on-complete` | waits for Ctrl-C otherwise |
| `--dashboard-refresh-seconds X` | `3.0` |
| `--dashboard-frame-interval STEPS` | — |
| `--dashboard-ligand-resname NAME` | auto-detected |
| `--dashboard-binding-pocket-cutoff-A X` | `5.0` |
| `--dashboard-max-playback-frames N` | `200` |

`--dashboard` turns live telemetry on for the simulation whether or not
`live_telemetry` was set.

---

## `init-config`

```bash
fastmdx init-config                        # writes fastmdxplora.yml
fastmdx init-config -o study.yml
fastmdx init-config -o study.yml --minimal # a short starter instead
fastmdx init-config -o study.yml --force   # overwrite
```

Refuses an existing file with exit 2 unless `--force`.

---

## `select`

What a selection matches, before a study depends on it. A selection that
matches the *wrong* atoms is not an error and nothing downstream detects it.

```bash
fastmdx select "resSeq 189 to 195 and name CA" -s trypsin.pdb
fastmdx select "protein and name CA" -s prepared.pdb --atoms --limit 0
```

| Argument | Required | Default |
|---|---|---|
| `expression` (positional) | yes | — |
| `-s`, `--structure`, `--topology` | **yes** | — |
| `--limit N` | no | `40`; `0` lists all |
| `--atoms` | no | lists residues otherwise |

Note `-s` means **structure** on this command, not system.

Exits **0** on a match, **1** on an empty match (with a `resSeq` vs `resid`
hint), **2** on a missing file or an invalid expression — so it can gate a
script. See [Selections in a Config](selections.md).

---

## `info`

No flags. Prints the version, authors and DOI; a readiness line per phase; a
grouped backend table marking each of OpenMM, PDBFixer, the OpenFF toolkit,
`openmmforcefields`, RDKit, PROPKA, WeasyPrint, Markdown, UMAP and
`openmmplumed` as `installed`, `missing` or `broken`, with the conda command
for anything missing; and the citation.

A backend that is present but will not load — WeasyPrint without Pango, say —
is reported as **broken** rather than missing, because reinstalling something
already there fixes nothing.

---

## Flags and Configs together

**Flags win over the file.** Only flags you actually typed override it; an
unset flag leaves the file's value alone.

```bash
fastmdx explore --config study.yml --setup-ph 6.0
```

runs at pH 6.0 even if `study.yml` says `7.0`, and `resolved_config.yml`
records `6.0`.

**`--config` behaves differently on the phase commands.** On `explore` and
`xplore` the whole file is applied. On `setup`, `simulate`, `analyze` and
`report`, the file is read only for the system to work on — that command's own
flags are what drive the run. If you want a Config's `setup:` block applied,
use:

```bash
fastmdx explore --config study.yml --include setup
```

not `fastmdx setup --config study.yml`.

**Some things are Config-only**, because a flag cannot carry them: `sweep`,
`analysis.options`, `report.region_highlights`, `report.comparison`, and the
`umbrella` / `steered` / `metadynamics` blocks.

---

## Exit codes

| Code | Means |
|---|---|
| **0** | Success. Also `--version`, `--cite`, `info`, a written template, a matched selection, a completed dry run |
| **1** | The work ran and failed: a phase errored, a selection matched nothing, the Agent gave up or exceeded its budget |
| **2** | A usage or Config error: an unknown setting, a bad value, `--include` with `--exclude`, no system given, a refusal to overwrite |
| **130** | Interrupted with Ctrl-C |

Failures print `fastmdx: <message>` on stderr **without a traceback** — the
traceback is kept at debug level. What you see is the refusal, not the stack
that produced it. [FastMDXplora refusals](refusals.md) explains the codes
behind them.

There is no `--validate-only`, no `--check` and no `--skip-validation`.
Validation is unconditional; `--dry-run` is the closest thing to validating
without running.

---

## Output formatting

The only CLI-level controls are `--verbose` and `--no-explain`. Beyond those,
three environment variables:

| Variable | Values | Effect |
|---|---|---|
| `FASTMDX_LOG_STYLE` | `pretty`, `plain` | Console style |
| `FASTMDX_LOGLEVEL` | `DEBUG`…`CRITICAL` | Level for console and file |
| `NO_COLOR` | set | Disable colour. Colour also requires a TTY |

---

## Commands that work

```bash
# The whole pipeline, single-dash form
fastmdx explore -system protein.pdb

# A real study
fastmdx xplore --system 1L2Y --simulate-duration-ns 50.0 --output runs/trpcage

# Ten picoseconds, to prove the machinery works
fastmdx explore --system 1L2Y --output runs/smoke \
  --simulate-nvt-steps 500 --simulate-npt-steps 500 \
  --simulate-production-steps 5000 --simulate-trajectory-interval-steps 50

# A ligand
fastmdx explore --system 181L --setup-forcefield amber-openff --output runs/lysozyme

# A Config, with one setting overridden
fastmdx explore --config study.yml --setup-ph 6.0

# Plan a campaign without running it
fastmdx explore --config sweep.yml --dry-run

# Prepare only
fastmdx setup -system protein.pdb --ph 6.5 --output runs/prepared

# Simulate a system prepared earlier
fastmdx explore --config study.yml --exclude setup \
  --simulate-setup-from runs/prepared

# Measure a trajectory from another engine
fastmdx analyze --trajectory production.xtc --topology system.pdb \
  --output runs/analysis --analyses rmsd rmsf rg

# Re-write the report over a finished run
fastmdx report --output runs/study --no-slides --no-bundle

# Watch a run in the browser while it happens
fastmdx explore --config study.yml --dashboard --dashboard-stop-on-complete

# Check a selection before a study depends on it
fastmdx select "resSeq 189 to 195 and name CA" -s trypsin.pdb

# Diagnostics
fastmdx info
fastmdx --version
```

---

## See also

- **[The FastMDXplora Config](config.md)** — the same settings in a file
- **[Config reference](config_reference.md)** — every setting, by phase
- **[Worked examples](examples.md)** — complete recipes
- **[The FastMDXplora Agent](agent.md)** — `fastmdx agent` in full
