# FastMDXplora

> **F**ully **A**utomated **Sy**s**T**em for **M**olecular **D**ynamics e**Xplora**tion

Give FastMDXplora a structure — or just a PDB identifier — and it prepares the
system, runs the dynamics, measures the trajectory and writes the study up. The
steps that usually need an expert are done for you, and refused where the
structure does not say enough to do them.

```bash
conda create -n fastmdxplora -c conda-forge fastmdxplora
conda activate fastmdxplora
fastmdx gui
```

A browser tab opens. Type `1L2Y`, press **Run**, and watch a protein fold.

---

## A Config goes in, a Manifest comes out

This is the whole shape of the software, and everything else follows from it.

**A complete study is specified in a FastMDXplora Config.** One file describes
the system, how it is prepared, how it is simulated, what is measured and how
it is reported. Nothing about a study lives anywhere else — not in a flag you
typed once, not in a form you filled in, not in your memory of what you did in
March.

**A complete result is recorded in a FastMDXplora Manifest.** `manifest.json`
records every phase that ran, every artifact it produced, every setting it
used, and the exact software stack it ran on. A reader opening the directory a
year later has the whole account without needing you.

```
    Config  ──▶  setup  ──▶  simulation  ──▶  analysis  ──▶  report  ──▶  Manifest
   the study                                                              the record
```

## Four ways to write a Config

A Config is plain YAML, so you can write one in a text editor. Three
FastMDXplora interfaces will write one for you, and they differ only in how
much they hold your hand:

| | | |
|---|---|---|
| **[FastMDXplora GUI](gui.md)** | A form in a browser | Every setting visible, explained and checked before you leave the page |
| **[FastMDXplora CLI](cli.md)** | Flags on a command | The same settings, generated from the same declaration |
| **[FastMDXplora API](api.md)** | Python | The same settings, as a dictionary, for building studies programmatically |

And in place of a human at any of those three, the
**[FastMDXplora Agent](agent.md)** will write one from a sentence:

```bash
fastmdx agent "simulate trypsin with benzamidine bound at pH 6.5 for 100 ns"
```

The Agent is reachable through all three channels — it is a `fastmdx agent`
subcommand, a panel in the GUI, and a function in the API.

**The Agent gets no special treatment.** What it produces goes through exactly
the same validator, by the same code, with the same refusals, as a Config you
typed by hand. It has no privileges and no private path into the software. The
one thing that distinguishes an Agent-written study is the `agent:` setting,
whose `unvalidated` value records that a phase went outside the schema — and
even then, the Config itself is still validated. See
[The FastMDXplora Agent](agent.md).

## Wherever it was written, it runs anywhere

The Config is the portable unit. Design a study in the GUI on a laptop,
download the file, and:

```bash
fastmdx explore --config study.yml --output runs/study
```

runs those same bytes on a cluster. Decide the study where it is comfortable
to think; run it where the compute is.

---

## Finding your way around

**New here?** [Installing FastMDXplora](installation.md), then
[Your first FastMDXplora study](first_study.md). Ten minutes, most of it the
install.

**Want to know what it does?** [How FastMDXplora works](how_it_works.md) —
the four phases, and what each one decides.

**Writing a study?** [The FastMDXplora Config](config.md) for the shape of
one, [Config reference](config_reference.md) for every setting,
[Worked examples](examples.md) for complete recipes.

**Running something real?** [Studies beyond a box of water](studies.md) for
restraints, membranes and enhanced sampling;
[Production runs and GPUs](production.md) for long runs;
[Running FastMDXplora elsewhere](clusters.md) for clusters and containers.

**Reading a result?** [The FastMDXplora Manifest](manifest.md) for the record
of what happened, [Reading the results](results.md) for the map of the run
directory and how to tell a measurement from a number the run could not
support.

**Something refused?** [FastMDXplora refusals](refusals.md).

---

```{toctree}
:maxdepth: 2
:caption: Start here

installation
first_study
how_it_works
```

```{toctree}
:maxdepth: 2
:caption: The FastMDXplora Config

config
config_reference
selections
studies
examples
```

```{toctree}
:maxdepth: 2
:caption: Writing a Config

gui
cli
api
agent
```

```{toctree}
:maxdepth: 2
:caption: Running a study

production
clusters
remote
```

```{toctree}
:maxdepth: 2
:caption: Results

manifest
results
analyses
interactions
refusals
```

```{toctree}
:maxdepth: 2
:caption: For developers

developers
interactions_design
validation
```
