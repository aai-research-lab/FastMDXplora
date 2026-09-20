# The FastMDXplora Config

A FastMDXplora Config is the whole description of a study: the system, how it
is prepared, how it is simulated, what is measured, and how it is written up.
Capture that and the four phases run themselves.

It is written as YAML, which is the format rather than the thing. The same Config
drives the [GUI](gui.md), the [CLI](cli.md) and the [API](api.md), and each of
them can write one — as can the [Agent](agent.md).

```bash
fastmdx init-config -o study.yml      # a commented template with every setting
fastmdx explore --config study.yml
```

---

## The shape of it

```yaml
output: runs/study                # where everything goes

systems:                          # one or more; always a list
  - system: 1UBQ
    id: wild_type                 # optional; s1, s2… if omitted

include: [setup, simulation, analysis, report]   # which phases

setup:
  ph: 7.4
  forcefield: amber-openff

simulation:
  duration_ns: 100
  temperature_K: 310

analysis:
  include: [rmsd, rmsf, rg, ss]

report:
  author: A. Researcher
```

**Every block is optional.** `systems:` is the only key a run requires, and a
single system still goes in the list. What you leave out takes its default.

The smallest useful Config is four lines:

```yaml
systems:
  - system: 1UBQ
output: runs/ubiquitin
```

---

## The fourteen top-level keys

Nothing else is accepted at the top level; an unknown key is refused with the
nearest match.

### The study

| Key | Type | What it is |
|---|---|---|
| `systems` | list | **Required.** One entry per system. See below |
| `output` | str | Where everything goes. Defaults to `./fastmdxplora_<system>_study_<UTC timestamp>` |
| `include` | list | Which phases to run: `setup`, `simulation`, `analysis`, `report` |
| `exclude` | list | Which phases to skip. Mutually exclusive with `include` |

### The phases

| Key | What it holds |
|---|---|
| `setup` | 43 settings — structure, ligand, membrane, solvent, force field, forces |
| `simulation` | 41 settings — length, conditions, integrator, platform, enhanced sampling |
| `analysis` | 13 settings — which measures, over which atoms, over which frames |
| `report` | 11 settings — title, formats, highlighted regions |

Every setting in each is in the [Config reference](config_reference.md).

### Campaigns

| Key | Type | What it is |
|---|---|---|
| `sweep` | mapping | Vary one or more settings across a cross-product of runs |
| `execution` | mapping | How the runs are scheduled: `mode`, `workers`, `devices`, `continue_on_error` |

### Everything else

| Key | Type | Default | What it is |
|---|---|---|---|
| `explain` | bool | `true` | Say why each step is happening, as it happens |
| `verbose` | bool | `false` | Stream debug logging to the terminal |
| `agent` | str | — | Records that the [Agent](agent.md) wrote this study: `assisted`, `autonomous` or `unvalidated` |
| `agent_model` | str | — | Which model wrote it, as `provider/model` |

`agent` and `agent_model` are provenance, not behaviour. They are described in
[The FastMDXplora Agent](agent.md).

---

## `systems`

Always a list, even for one system. Each entry:

```yaml
systems:
  - system: 1UBQ              # required: a PDB/CIF path, or a 4-character PDB ID
    id: wild_type             # optional: names the run directory and the report label
  - system: mutant.pdb
    id: L50A
    simulation:               # optional: overrides the top-level block, this system only
      temperature_K: 320
```

`system` accepts a path to a `.pdb`/`.cif` file, or a four-character PDB
identifier fetched from RCSB. The form is detected, so there is no separate
setting for an identifier.

`id` names the run — it becomes the run's directory under `runs/` and its label
in the comparison report. Without one the systems are `s1`, `s2` and so on,
which is harder to read six months later. Two systems cannot share an `id`.

A phase block inside an entry overrides the top-level one **for that system
only**, which is how you vary a single setting across a campaign.

---

## `sweep`

A mapping of dotted `phase.setting` to a list of values. Every combination is
run.

```yaml
systems:
  - system: 1UBQ
sweep:
  simulation.temperature_K: [300, 310, 320]
  setup.ion_concentration_M: [0.15, 0.30]
```

That is six runs — systems outermost, sweep axes inner. Settings merge lowest
to highest: the top-level block, then the per-system block, then the sweep
value.

**`sweep` is Config-only.** There is no command-line flag for it, which is one
of the reasons a real campaign lives in a file.

---

## `execution`

```yaml
execution:
  mode: parallel          # or sequential, the default
  workers: 2              # how many runs at once
  devices: [0, 1]         # GPU indices; one run pinned per device, round-robin
  continue_on_error: true
```

Two defaults are inferred rather than written, and both are worth knowing:

**Asking for `workers` or `devices` is asking for `parallel`.** Setting
`workers: 3` and nothing else schedules three at a time. (`workers: 3` with an
explicit `mode: sequential` still runs sequentially — the explicit setting
wins.)

**`continue_on_error` defaults to `true` for a campaign and `false` for an
umbrella study.** A campaign should carry on when one system fails, because the
others are still results. A free energy cannot be computed at all if a window
is missing, so continuing there spends hours producing runs that will be thrown
away.

Parallelism is process-based and always local to one machine. There is no
multi-node dispatch; see [Running FastMDXplora elsewhere](clusters.md).

---

## Per-analysis settings

An analysis's own settings go in a block under its name, inside
`analysis.options`:

```yaml
analysis:
  include: [hbonds, cluster, sasa]
  options:
    hbonds:
      distance_cutoff: 0.30
      angle_cutoff: 150.0
    cluster:
      methods: [kmeans, hierarchical]
      n_clusters: 8
      random_state: 7
    sasa:
      mode: average_residue
```

What each analysis accepts is in [The FastMDXplora analyses](analyses.md), and
the GUI shows it beside each analysis. A misspelled option is refused rather
than ignored.

---

## Validation

Every Config is validated before anything reaches a GPU — whether it was
written by hand, by the GUI, by the CLI, by the API or by the
[Agent](agent.md). There is one validator and no way around it: there is no
`--skip-validation`, no `--no-validate`, and no setting that turns it off.

Validation checks, in this order: that every top-level key is known; that
`include` and `exclude` are not both given; that `systems` is present; that
`sweep` and `execution` are well formed; and then, per phase block, that every
setting is known, of the right type, one of its permitted values, and within
its bounds.

A refusal names the setting, what you gave, and — where the schema can say so
without guessing at your chemistry — what it will accept:

```
Unknown setup option 'pH' (did you mean 'ph'?). Valid options: agent, box_shape, …
setup option 'ph' should be number, got str ('high').
setup option 'ph' is 25, above the largest value it can have (14.0).
execution option 'mode' does not accept 'turbo'. Accepted values: sequential, parallel.
```

`fastmdx` exits **2** on a Config error. See
[FastMDXplora refusals](refusals.md) for what each code means and how to read
one from a program.

### Numeric settings have bounds, not just types

`choices` refuses a name the software does not know; bounds refuse a value the
quantity cannot be.

```
pH 25             REFUSED   above the largest value it can have (14.0)
salt 150 M        REFUSED   pure water is about 55 M
salt 4 M          accepted  saturated NaCl is near 6
timestep 10 fs    accepted  unstable, and not impossible
```

The line is between a fact and a view. A pH of 25 is not a strict reading of
pH, it is not a pH. A 10 fs timestep is unstable for almost every system and
somebody's coarse-grained run may want it, so it has no ceiling here — and the
runner already refuses an integration that blows up, which is where a judgement
about stability belongs.

The salt ceiling is the one that would have caught somebody: writing `150` when
the field is molar and the sentence said millimolar is a thousandfold error
that parsed, ran, and produced an ordinary-looking trajectory.

### Checking a Config without running it

```bash
fastmdx explore --config study.yml --dry-run
```

Validates the syntax and every setting, prints the plan, and stops. It creates
no output directory at all.

The GUI does the same from **A config I already have → Check it**, and will
also open the file for editing without rewriting it.

---

## Two spellings, one setting

A few settings have an older name that is still accepted:

| Setting | Also accepted | Which wins if both are given |
|---|---|---|
| `simulation.setup_from` | `prepared_from` | `prepared_from` |
| `setup.ligand_name` | `ligand_resname` | `ligand_name` |
| `analysis.select_atoms` | `selection` | `selection` |
| `umbrella.centres` | `centers` | `centres` |
| `umbrella.selection_a` / `_b` | `select_atoms_a` / `_b` | `selection_a` / `_b` |

**They are settled when the Config is read, not where it is used.** After
loading, a block given either name carries both, with the same value, so no
part of a run can disagree with another about what it says. Where both are
given with different values, the winner is the one in the third column — the
longer-established spelling in each pair. Give one, not both.

This is how compatibility is handled: there is no `version:` key in a Config,
no migration step, and no upgrade path to run. A Config written for an older
FastMDXplora either validates or names the setting that changed.

---

## Where the settings come from

There is no list of options to keep in step with the software, because the
options **are** the software's declaration. One file gives the command line its
flags, the Config file its keys, the GUI its form fields, and the Agent its
description of what it may write.

Four generated views of the same declaration:

```bash
fastmdx init-config -o study.yml   # every setting, with its help as a comment
fastmdx explore --help             # the same settings, as flags
fastmdx gui                        # the same settings, as a form
```

```python
from fastmdxplora.config.describe import describe_schema
print(describe_schema())          # the same settings, as prose for a model
```

The template is the most useful of the four when writing a Config: it carries
every key at its default with a sentence about what it does. Its phase headers
are commented out, so the file is valid as written and defaults apply until you
opt in.

```bash
fastmdx init-config -o study.yml --minimal     # a short starter instead
```

`init-config` refuses to overwrite an existing file; pass `--force` if you mean
to.

---

## Reproducing a run

Every run writes `resolved_config.yml` into its output directory — a valid
Config that reproduces the study:

```bash
fastmdx explore --config runs/original/resolved_config.yml --output runs/repeat
```

It is the Config file you gave, **plus any command-line flags or API arguments
folded in**, with `systems` canonicalised to a list and the alias spellings
settled. So a run started as

```bash
fastmdx explore --config study.yml --setup-ph 6.0
```

leaves a `resolved_config.yml` saying `ph: 6.0`, not the `7.0` the file said.

**It carries every setting the run used, defaults included.** That is what the
file is for: a study you can repeat from what the run left behind rather than
from what somebody remembers typing. Every phase gets a block whether or not
you touched it, and every option in that block is named — 110 settings for a
study that set two. The block is exactly the dictionary the phase was handed,
not a reconstruction of it.

**And it carries what the run decided for itself.** Plenty of settings have no
value until the run works one out: `simulation.duration_ns: 50` is not a number
of steps until a timestep says so, and `setup.forcefield: auto` is not a force
field until a registry says which. Each phase records the answers it reached,
and the resolved config writes them down:

```yaml
simulation:
  duration_ns: 50           # what you asked for
  production_steps: 25000000  # what that came to, at this timestep
  trajectory_interval_steps: 12500
  pressure_bar: 1.2159      # a 1.2 atm run, in the unit the barostat used
setup:
  forcefield: auto
  force_field: [amber14-all.xml, amber14/tip3p.xml]   # what `auto` chose
  water_model: tip3p
  switch_distance_nm: 0.9   # nine tenths of the cutoff, worked out
analysis:
  include: [rmsd, rmsf, rg, cluster]   # the default set, named
```

Both halves are kept, and they agree. Where a derived value and its source sit
together, the derived one wins on replay — `production_steps` over the
`duration_ns` it came from — so you get the same run either way. **That is only
safe because the number written is the run's own answer.** A step count that was
merely the schema default, written beside a duration you chose, would override
that duration and silently shorten the study. So only a recorded resolution is
allowed to do this; anything else stays `null`, which is what the Config reader
takes as "decide this again".

```{note}
**What `null` means, and why nothing is left open.** A setting written as `null`
is one you did not ask for: no membrane, no ligand, no mutation, no particular
chain. `null` is the value, not a question the file failed to answer.

The one setting the run derives without recording is `report.title`, which falls
back to `FastMDXplora Study — <system>`. That is a heading on the report, not a
property of the study: replay it on any version and you get the same
trajectory, the same measurements and the same numbers, under a title that may
be worded differently. It is in the first group, not a gap in the second.

The test for whether a derived value has to be written down is whether losing it
changes the science. `analysis.include` decides which measures run;
`simulation.production_steps` decides how long. Both are recorded, above. A
title decides what the first line says.
```

The phases' own records go further than a config can, and are still worth
reading for what a study actually *did* rather than what it set out to do —
the atom count after solvation, the platform, the frames written, the
per-measure findings: `setup/setup_parameters.json`,
`simulation/simulation_parameters.json`, `analysis/analysis_manifest.json`,
`analysis/<name>/options.json`. See
[The FastMDXplora Manifest](manifest.md).

### What "reproduces" means

Re-running `resolved_config.yml` gives an **equivalent** study, not an
identical one. Solvation places water by a procedure that does not give the
same answer twice, so two preparations of the same molecule have different atom
counts. Fixing the seed fixes the dynamics, not the solvent:

```yaml
simulation:
  random_seed: 42
```

The report's methods section states whether a seed was fixed, because its
absence is what makes a run irreproducible. To repeat a study on the *same*
prepared system rather than an equivalent one, point at the finished setup:

```yaml
simulation:
  setup_from: runs/original/setup
```

---

## See also

- **[Config reference](config_reference.md)** — every setting, by phase
- **[Selections in a Config](selections.md)** — the four places a Config names atoms
- **[Studies beyond a box of water](studies.md)** — restraints, membranes, enhanced sampling
- **[Worked examples](examples.md)** — complete Configs to copy
- **[The FastMDXplora Manifest](manifest.md)** — what the run records back
