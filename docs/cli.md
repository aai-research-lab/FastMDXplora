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
| `fastmdx analyze` | Analyse a trajectory |
| `fastmdx report` | Write up an existing run |
| `fastmdx agent` | Write a Config from a sentence — [the Agent](agent.md) |
| `fastmdx gui` | Serve [the GUI](gui.md) |
| `fastmdx mcp` | Serve FastMDXplora to an AI app: [FastMDXplora from your AI app](mcp.md) |
| `fastmdx config` | Write a commented Config template |
| `fastmdx select` | Show what a selection matches, before a run depends on it |
| `fastmdx diff` | The settings two studies or Configs differ in |
| `fastmdx scene` | Write a view of a study as a scene file (MolViewSpec) |
| `fastmdx movie` | Make a movie of a study's frames, as the GUI's Viewer makes one |
| `fastmdx info` | What is installed, and how to get what is not |
| `fastmdx remote` | Inspect other machines over SSH: [Other machines](remote.md) |
| `fastmdx resume` | Carry a study that stopped part-way on to its end: [When it stops early](production.md#when-it-stops-early) |

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

Each phase command is `explore` with one phase: `fastmdx analyze` is
`fastmdx explore --include-phase analysis`, its flags those of `explore`
without the `--analyze-` prefix, run through the same code, by the same
rules. Given an `--output` folder that holds a study and no system or
Config, it runs on that study, from the settings the study recorded in its
`resolved_config.yml` (a title, region highlights, which analyses and their
options), the flags given laid over them. A setting left unset when the
study ran was recorded with its default then. The study's own trajectory and
structure are found where the study is now, not at the paths it recorded,
so a study moved or extended since runs on its own frames.

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
| `--force-overwrite`, `--force` | Run phases whose output the folder already holds, removing that output first |
| `--rerun` | As `--force-overwrite`, keeping what it replaces in the study's `previous/<phase>` |
| `--rerun-window N [N ...]` | Umbrella studies: run these windows again in place, keep the rest, recombine |
| `--rerun-force-constant K` | With `--rerun-window`: hold those windows at K (kJ/mol per unit of the variable squared); the others keep theirs |

Without `--force-overwrite` or `--rerun`, a run is refused where the folder
already holds output of a phase it would write. The check looks only at the
phases *this* run will produce, so running `analyze` into a directory that
already holds a finished `simulation/` is the intended workflow and needs no
flag; into a new folder, nothing is needed either. These two flags are on
the four phase commands too.

With either, what the run writes is cleared first, and so is the output of
any phase after it that this run does not do again, since it was written
from what is being replaced: `fastmdx analyze --rerun` sets the report aside
too, and says the command that writes it again. `--force-overwrite` removes
them; `--rerun` keeps them in `previous/<phase>` (`previous/analysis`,
`previous/report`), one copy of each, in place of what was kept there
before, with the phase's record from the Manifest beside it. `previous/` is
left out of the report's bundle. A study of several runs is run again run
by run, and the comparison of its runs built again; so is that comparison
when one run of it is analysed again on its own (`--output study/runs/a`).
Neither is done while a run of the study is going.

```bash
fastmdx analyze --output runs/study --analyses rmsd rg sasa --rerun
fastmdx report --output runs/study --rerun
fastmdx explore --output runs/study --include-phase analysis report --rerun
```

Setup or simulation run again this way sets aside everything after them; to
keep a study and try another preparation or a longer run, start a new study
from it with `simulation.setup_from` or `simulation.resume_from`.

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

On a study, `report` needs no `--system`: it reads the study's record.
Writing a report over one the study has needs `--force-overwrite` or
`--rerun`.

```bash
fastmdx report --output runs/study --rerun --no-slides --no-bundle
```

**Sharing it.** `--share FILE` packs the finished study as one zip another
FastMDXplora opens (`fastmdx gui --open`); the report is not written again.
Full documentation: [Sharing a study](sharing.md).

```bash
fastmdx report --output runs/study --share study.zip --share-author "A. Researcher"
fastmdx report --output runs/study --share study.zip --share-author "A. Researcher;0000-0002-1825-0097" --share-to zenodo
```

| Flag | Default | What it does |
|---|---|---|
| `--share FILE` | none | Write the study, as its pages show it, to `FILE` |
| `--share-all` | off | Every file of the study, not only what its pages read |
| `--share-license ID` | `CC-BY-4.0` | The licence, as an SPDX identifier |
| `--share-author NAME` | the report's `--author` | An author, given once for each; `"NAME;ORCID"` adds an ORCID iD |
| `--share-to SITE` | none | Also make a draft on `zenodo` or `zenodo-sandbox`, with the token in `ZENODO_TOKEN` or `ZENODO_SANDBOX_TOKEN`; you publish it |

---

## `agent`

Write a Config from a sentence. Full documentation:
[The FastMDXplora Agent](agent.md).

```bash
fastmdx agent model                                      # choose an AI model, once
fastmdx agent "simulate ubiquitin at pH 6.5 for 50 ns"
fastmdx agent -f request.txt -o study.yml
fastmdx agent                                            # opens the GUI Agent panel
```

| Flag | Default | What it does |
|---|---|---|
| `request` (positional) | — | The study, in plain language. The literal word `model` shows the AI model in use and runs the chooser instead (`set`, its old name, stops and names `model`). With no request, the agent panel opens |
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
| `--demo [DIR]` | off | Copy the demo study (3PTB, finished) into DIR and open it; fetched the first time (about 11 MB) and kept in the cache |
| `--open SOURCE` | off | Open a shared study from its DOI, its Zenodo address or its zip, every file checked against its SHA-256 first ([Sharing a study](sharing.md)) |
| `--open-into DIR` | current directory | Where `--open` unpacks it |
| `--open-most-gb GB` | `2` | The largest archive `--open` downloads and opens |
| `--ligand-resname NAME` | auto-detected | Which residue the viewer treats as the ligand |
| `--binding-pocket-cutoff-A X` | `5.0` | How near counts as the pocket |

**These, and the flags for serving it to someone else ([hosting](hosting.md)), are the whole of `fastmdx gui`.** The dashboard flags below
(`--dashboard-host`, `--dashboard-port` and the rest) belong to `explore` and
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
| `--dashboard-max-playback-frames N` | `2000`, fewer for a system over 5,000 atoms (ten million atoms times frames at most) |

`--dashboard` turns live telemetry on for the simulation whether or not
`live_telemetry` was set.

---

## `mcp`

```bash
fastmdx mcp --workspace ~/studies               # started by an AI app, not by hand
fastmdx mcp --workspace ~/studies --read-only   # it may check and read, not run
```

Serves FastMDXplora to an AI app that speaks the Model Context Protocol, on
standard input and output: the tools that look and check, so the AI app's
model writes a study and the validator judges it, starting and stopping a
checked study with your go-ahead, and, if you ask for it, the Agent
(`ask_agent`, with the AI app's model where the AI app lends it, else on your
own API key), all inside the one workspace folder. The AI app starts it from
its own settings. See [FastMDXplora from your AI app](mcp.md).

---

## `config`

```bash
fastmdx config                                     # writes fastmdxplora.yml
fastmdx config -f study.yml                        # or --file study.yml
fastmdx config -f study.yml --minimal              # a short starter instead
fastmdx config -f study.yml --force-overwrite      # overwrite
```

Refuses an existing file with exit 2 unless `--force-overwrite`. The old
name, `fastmdx init-config`, stops with exit 2 and names this one.

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

## `diff`

The settings two studies, or two Configs, differ in: where two runs came out
differently, what they were asked to do differently.

```bash
fastmdx diff runs/at_300K runs/at_310K
fastmdx diff study.yml runs/at_300K --json
```

A study is read from the `resolved_config.yml` it wrote. A phase setting either
side leaves out is taken at its default, so a short Config and the full one it
resolves to compare as the same study; a path inside a study's own folder is
said from it (`<output>/joined/production.dcd`), so two studies' trajectories
do not differ for being in two folders. A study against a Config also shows
what the software resolved when the study ran (the force field's files, the
water model), and says so.

Exits **0** where they ask for the same study (they may still be written to
different folders), **1** where they differ, **2** where either is not a study
or a Config.

---

## `scene`

A view of a study written as a scene file: MolViewSpec, the Mol\* team's
format for what a molecular viewer shows, as an `.mvsx` archive that opens as
it was shown in any viewer built on Mol\* (molstar.org among them).

```bash
fastmdx scene runs/trypsin --view "pocket at 40 ns"
fastmdx scene runs/trypsin --frame 120 --name frame120 -o frame120.mvsx
```

`--view` names a view saved with the study in the GUI's Viewer (its camera,
frame, representation, colouring and parts shown); `--frame` is a frame as
the Viewer plays them, from 0. The scene holds the atoms shown, at that
frame, with the study's DSSP as the cartoon, its colours (a result such as
RMSF on the Viewer's scale) and the selections named in the GUI, unless
`--no-selections`. It is written in the study's `scenes/` folder and copied
where `--output` says; what a scene cannot hold (water at a frame, the
periodic box) is said. Exits **0** when it is written, **1** when it could
not be made, **2** where the folder or the view is not there.

---

## `movie`

A movie of a study's frames without opening the GUI: the movie the Viewer's
Movie section makes. The GUI is started for the study on this computer,
reachable from it alone; a browser with no window opens its Viewer and
shows a view saved with the study (or the Viewer as it opens), changed as
asked; and the Viewer renders each frame and has ffmpeg encode it into the
study's `movies/` folder, as H.264 in an MP4 or, where that ffmpeg has none,
VP9 or VP8 in a WebM.

```bash
fastmdx movie runs/trypsin --view "pocket at 40 ns" --name pocket
fastmdx movie runs/trypsin --from 0 --to 200 --every 2 --between 3 --turn
fastmdx movie runs/trypsin --colour result:rmsf --superposed backbone --size 3840x2160
```

`--view` names a view saved in the GUI; `--representation`, `--colour` and
`--superposed` change it (or the Viewer as it opens). `--from`, `--to` and
`--every` choose the frames played, `--to` before `--from` playing them
backwards; `--between` puts 1, 3 or 7 frames in between each two, each atom
moved in a straight line, for a smoother movie (not more simulation; the
frames are superposed on the backbone first where they would be shown as
written). `--fps` (10 to 60), `--size` (`1280x720`, `1920x1080`,
`3840x2160`), `--turn` (one turn about the screen's vertical) and
`--no-time` (no simulated time in the corner) as in the GUI. A study without
frames gives its structure turned once. It needs ffmpeg and a browser that
renders WebGL: Playwright's Chromium (`pip install "fastmdxplora[movies]"`,
then `playwright install chromium`), or Chrome or Edge where installed. Exits
**0** when the movie is made and **1** when it could not be, with why.

---

## `info`

Prints the version, authors and DOI; the platform, saying plainly on Windows
that it is not supported; a readiness line per phase; a grouped backend table
marking each of OpenMM, PDBFixer, the OpenFF toolkit, `openmmforcefields`,
RDKit, PROPKA, WeasyPrint, Markdown, UMAP and `openmmplumed` as `installed`,
`missing` or `broken`, with the conda command for anything missing; and the
citation.

One row is not a package: **AM1-BCC charges** says whether anything can compute
a ligand's charges, and names what will (AmberTools, or OpenEye where it is
licensed). The OpenFF toolkit loads without either, so its own row being
`installed` does not answer this.

A backend that is present but will not load — WeasyPrint without Pango, say —
is reported as **broken** rather than missing, because reinstalling something
already there fixes nothing.

`--json` prints the same information as JSON, with no banner, for a program to
read. It is how `fastmdx remote` learns what an installation on another machine
can load.

---

## `remote`

Machines a study can run on, reached with your own `ssh`. See
[Other machines](remote.md).

```bash
fastmdx remote                                     # machines and jobs, without connecting
fastmdx remote --machine gpu-box                   # inspect, record, say if it is ready
fastmdx remote install --machine gpu-box           # run the plan, after you confirm
fastmdx remote send -c study.yml --machine gpu-box # run a study there
fastmdx remote status [JOB]                        # how jobs are doing
fastmdx remote fetch JOB [--with-trajectory]       # bring results back
fastmdx remote cancel JOB                          # stop a job
fastmdx remote forget gpu-box                      # remove the record
```

| `send` argument | Required | Default |
|---|---|---|
| `-c`, `--config FILE` | **yes** | — |
| `--machine NAME` | when more than one is inspected | the only one |
| `--output DIR` | no | the Config's `output`, else a new study folder |
| `--dry-run` | no | sends |
| `--force-overwrite` | no | refuses a job of the same name |
| `--partition NAME`, `--time LIMIT` | no | the cluster's defaults |

`NAME` is an alias from `~/.ssh/config` or `user@host`. Inspection only reads
the machine; `install` runs nothing without a yes at the terminal. Exits **0**
when the action is done (a machine inspected, ready or not), and **1** when a
machine cannot be reached, is not ready for `send`, or a name is not known.

---

## `resume`

```bash
fastmdx resume runs/study          # says what it did
fastmdx resume runs/study --json   # the same, as one line of JSON
```

Reads how far a study got and does what is left: nothing, the analyses and
report, the rest of production from its last sealed checkpoint, or the whole
study again if production had not begun. A study of several runs (a sweep,
several systems, an umbrella study's windows) is carried on run by run, and
what it says across them is rebuilt. It exits 0 when the study is finished,
whether or not it ran anything, and 1 when it could not carry the study on,
with the reason and what would fix it, its command and its price here
(`remedies` in the JSON). See
[When it stops early](production.md#when-it-stops-early) and
[What would fix it](refusals.md#what-would-fix-it-and-what-it-costs).

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
| **130** | Interrupted with Ctrl-C; the study records where it stopped, and `fastmdx resume` carries it on |

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

# Analyse a trajectory from another engine
fastmdx analyze --trajectory production.xtc --topology system.pdb \
  --output runs/analysis --analyses rmsd rmsf rg

# Re-write the report over a finished run, keeping the one before
fastmdx report --output runs/study --rerun --no-slides --no-bundle

# Add an analysis to a finished study; its report is set aside to write again
fastmdx analyze --output runs/study --analyses rmsd rg sasa --rerun

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
