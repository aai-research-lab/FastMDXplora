# Python API

Everything the command line does is available from Python, through one class.

```python
import fastmdxplora as fastmdx

runs = fastmdx.FastMDXplora(system="1UBQ", output_dir="runs/ubiquitin").explore()
print(runs[0].output_dir)
```

`explore()` runs all four phases and returns one result per system.

---

## Constructing it

```python
fastmdx.FastMDXplora(
    system="1UBQ",              # a PDB identifier or a path
    output_dir="runs/study",
    options={                   # settings, by phase
        "setup": {"ph": 7.0, "forcefield": "amber-openff"},
        "simulation": {"duration_ns": 100},
        "analysis": {"include": ["rmsd", "rmsf", "ss"]},
    },
)
```

Or from a config file, which is the same file the CLI and the GUI use:

```python
fastmdx.FastMDXplora(config="study.yml")
```

The settings under `options` are exactly the config file's blocks, so anything
[Configuration](configuration.md) describes works here.

---

## Running part of it

```python
study = fastmdx.FastMDXplora(system="1UBQ", output_dir="runs/study")

study.explore(include=["setup", "simulation"])   # stop after the trajectory
study.explore(exclude=["report"])                # everything but the write-up
```

---

## What comes back

`explore()` returns a `RunResult` per system, carrying where the output went,
which phases ran, and what each produced. The same information is in
`manifest.json` in the output directory, which is what to read if the process
has ended.

```python
for run in runs:
    print(run.output_dir)
    for phase in run.phases:
        print(" ", phase.name, phase.status)
```

---

## Watching a run from Python

There is no separate Python dashboard. Serve the output directory with the
GUI from another terminal, while the Python process runs:

```bash
fastmdx gui --output runs/study
```

That gives telemetry, the molecule in 3D, and the results as they appear —
the same as for a run started from the command line.

---

## Reference

Generated from the source, so it says what the code says. Every entry
carries a `[source]` link to the implementation.

Docstrings here are longer than reference documentation usually is,
because a docstring is where a decision gets recorded: what a measure
computes, what it refuses and why, and which choices move the number.
That is the material to read before comparing a result against another
tool.

### The entry points

```{eval-rst}
.. autoclass:: fastmdxplora.FastMDXplora
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: fastmdxplora.AnalysisOrchestrator
   :members:
   :undoc-members:
   :show-inheritance:
```

### Configuration

```{eval-rst}
.. automodule:: fastmdxplora.config.schema
   :members:
   :undoc-members:

.. automodule:: fastmdxplora.config.loader
   :members:
```

### Setup

```{eval-rst}
.. automodule:: fastmdxplora.setup.pdbfix
   :members:

.. automodule:: fastmdxplora.setup.prepare
   :members:

.. automodule:: fastmdxplora.setup.ligand
   :members:
```

### Simulation

```{eval-rst}
.. automodule:: fastmdxplora.simulation.runner
   :members:

.. automodule:: fastmdxplora.simulation.metadynamics
   :members:

.. automodule:: fastmdxplora.simulation.metad_surface
   :members:

.. automodule:: fastmdxplora.simulation.umbrella
   :members:

.. automodule:: fastmdxplora.simulation.steered
   :members:

.. automodule:: fastmdxplora.simulation.restraints
   :members:

.. automodule:: fastmdxplora.simulation.binding
   :members:
```

### Analyses

Each is a class with the same shape: options in the constructor,
`compute()` for the numbers, `run()` to write the data, the figure and the
record of what it did.

```{eval-rst}
.. automodule:: fastmdxplora.analysis.base
   :members:

.. automodule:: fastmdxplora.analysis.orchestrator
   :members:

.. automodule:: fastmdxplora.analysis.rmsd
   :members:

.. automodule:: fastmdxplora.analysis.rmsf
   :members:

.. automodule:: fastmdxplora.analysis.rg
   :members:

.. automodule:: fastmdxplora.analysis.sasa
   :members:

.. automodule:: fastmdxplora.analysis.ss
   :members:

.. automodule:: fastmdxplora.analysis.dihedrals
   :members:

.. automodule:: fastmdxplora.analysis.qvalue
   :members:

.. automodule:: fastmdxplora.analysis.order_parameters
   :members:

.. automodule:: fastmdxplora.analysis.bfactor_comparison
   :members:

.. automodule:: fastmdxplora.analysis.thermodynamics
   :members:

.. automodule:: fastmdxplora.analysis.rdf
   :members:

.. automodule:: fastmdxplora.analysis.coordination_number
   :members:

.. automodule:: fastmdxplora.analysis.pair_distance
   :members:

.. automodule:: fastmdxplora.analysis.end_to_end
   :members:

.. automodule:: fastmdxplora.analysis.moments_of_inertia
   :members:

.. automodule:: fastmdxplora.analysis.cluster
   :members:

.. automodule:: fastmdxplora.analysis.dimred
   :members:

.. automodule:: fastmdxplora.analysis.hbonds
   :members:

.. automodule:: fastmdxplora.analysis.water_sites
   :members:

.. automodule:: fastmdxplora.analysis.ligand_rmsd
   :members:

.. automodule:: fastmdxplora.analysis.ligand_rmsf
   :members:

.. automodule:: fastmdxplora.analysis.contacts
   :members:

.. automodule:: fastmdxplora.analysis.pl_hbonds
   :members:

.. automodule:: fastmdxplora.analysis.pl_interactions
   :members:

.. automodule:: fastmdxplora.analysis.interaction_summary
   :members:

.. automodule:: fastmdxplora.analysis.interactions
   :members:

.. automodule:: fastmdxplora.analysis.pmf
   :members:

.. automodule:: fastmdxplora.analysis.metad_surface
   :members:

.. automodule:: fastmdxplora.analysis.steered_work
   :members:

.. automodule:: fastmdxplora.analysis.reweight
   :members:

.. automodule:: fastmdxplora.analysis.reweighted_averages
   :members:
```

### Statistics and provenance

```{eval-rst}
.. automodule:: fastmdxplora.statistics
   :members:

.. automodule:: fastmdxplora.provenance
   :members:

.. automodule:: fastmdxplora.explain
   :members:

.. automodule:: fastmdxplora.advisories
   :members:
```

### Campaigns

```{eval-rst}
.. automodule:: fastmdxplora.batch.explorer
   :members:

.. automodule:: fastmdxplora.batch.sweep
   :members:

.. automodule:: fastmdxplora.batch.aggregate
   :members:

.. automodule:: fastmdxplora.batch.compare
   :members:
```

### Validation

Measurements of the software's own behaviour, run against each release.
Distinct from the test suite, which asserts: these report rates and
comparisons against implementations that share no code with it.

```{eval-rst}
.. automodule:: fastmdxplora.validation.corpus
   :members:

.. automodule:: fastmdxplora.validation.environments
   :members:
```

---

## See also

- [Worked examples](usage_examples.md) — recipes, several with Python
- [Configuration](configuration.md) — every setting `options` accepts
- [Reading the results](results.md) — what a run leaves, and what each
  number is worth
