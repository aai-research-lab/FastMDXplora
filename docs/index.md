# FastMDXplora

> **F**ully **A**utomated **Sy**s**T**em for **M**olecular **D**ynamics e**Xplora**tion

Give it a structure — or just a PDB identifier — and it prepares the system,
runs the dynamics, measures the trajectory, and writes the study up. The parts
that usually need an expert are done for you, and refused where the structure
does not say enough to do them.

## The quickest way in

```bash
conda create -n fastmdxplora -c conda-forge fastmdxplora
conda activate fastmdxplora
fastmdx gui
```

A tab opens. Type `1L2Y`, press **Run**, and watch a protein fold. Everything
FastMDXplora does is on that page: designing a run, starting it, watching the
molecule move, reading the results.

Not a dashboard attached to a command-line tool. The form is generated from
the same schema the CLI validates against, so every system FastMDXplora can
study can be built in the browser -- a protein, a protein and its ligand, a
membrane protein, an umbrella or metadynamics or steered study, a trajectory
from another engine, a sweep across many systems -- and every setting is
reachable. What it hands back is a config file, and `fastmdx explore --config`
runs those same bytes anywhere. Decide the study where it is comfortable to
think; run it where the compute is.

If you would rather use a terminal:

```bash
fastmdx explore --system 1L2Y --output runs/trpcage
```

[Your first run](getting_started.md) takes either route from nothing to a
finished report in about ten minutes.

## The config is the study

Both routes above are building the same thing. A FastMDXplora config describes
a study completely -- the system, its preparation, the simulation, what is
measured, how it is reported -- and the four phases run themselves from it.
The [GUI](gui.md), the [command line](cli_reference.md) and the
[Python API](api.md) each build one and each run all four phases; a config can
also be written by hand, since the YAML is short. Every run writes
`resolved_config.yml` with every setting filled in, so a study can be repeated
from what it left behind rather than from what somebody remembers typing.

The GUI is worth using even for a command-line or Python workflow: it is the
one place where every setting is visible, explained, and checked before you
leave the page. See [Configuration](configuration.md).

## Finding your way around

- **New here?** [Installation](installation.md), then
  [Your first run](getting_started.md).
- **Want to see what it can do?** [The FastMDXplora GUI](gui.md) and
  [The four phases](phases.md). Every step explains itself as it runs, so
  [Your first run](getting_started.md) is also the shortest way to learn what
  the steps are for.
- **Running something real?** [Beyond a box of water](simulations.md) for
  restraints, membranes, and enhanced sampling — umbrella, steered and
  metadynamics; [Production and GPUs](production.md)
  for long runs.
- **Reading results?** [Reading the results](results.md) for what a run
  leaves behind and what makes a number one;
  [Protein-ligand interactions](interactions.md) for the measure that
  carries the most criteria.
- **Naming which atoms?** [Selections](selections.md) -- one language in
  four places, and why `resid 189` is not residue 189.
- **Looking for a flag or a setting?** [CLI reference](cli_reference.md),
  [Configuration](configuration.md), or `fastmdx explore --help`, which is
  generated and therefore never out of date.

```{toctree}
:maxdepth: 2
:caption: Start here

installation
getting_started
phases
```

```{toctree}
:maxdepth: 2
:caption: Running simulations

simulations
production
```

```{toctree}
:maxdepth: 2
:caption: Reading the results

results
interactions
```

```{toctree}
:maxdepth: 2
:caption: The Config

configuration
selections
usage_examples
```

```{toctree}
:maxdepth: 2
:caption: Interfaces

gui
cli_reference
api
```

```{toctree}
:maxdepth: 2
:caption: Running it elsewhere

remote
```

```{toctree}
:maxdepth: 2
:caption: For maintainers

interactions_design
```
