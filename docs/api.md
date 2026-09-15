# The FastMDXplora API

Everything the command line does is available from Python, through one class.

```python
import fastmdxplora as fastmdx

runs = fastmdx.FastMDXplora(system="1UBQ", output_dir="runs/ubiquitin").explore()
print(runs[0].output_dir)
```

Like the [GUI](gui.md) and the [CLI](cli.md), the API is a way of building a
[Config](config.md) and running it. The settings it takes are the Config's
blocks, under the same names.

---

## `FastMDXplora`

```python
FastMDXplora(
    system=None,          # str | PathLike | None — a PDB/CIF path or a 4-char PDB ID
    *,
    config=None,          # str | PathLike | None — a Config file
    config_data=None,     # dict | None — the same, already parsed
    output_dir=None,      # str | PathLike | None
    options=None,         # dict[str, dict] | None — settings, by phase
    verbose=False,
    include=None,         # list[str] | None
    exclude=None,         # list[str] | None
)
```

Give **one** of `system`, `config` or `config_data`. Giving both a system and a
config raises `StudyError(code="config.option.conflicting")`; giving none
raises `StudyError(code="config.option.missing_companion")`.

`output_dir` defaults to `fastmdxplora_output_<UTC timestamp>`.

### Settings go in `options`, keyed by phase

```python
fastmdx.FastMDXplora(
    system="1UBQ",
    output_dir="runs/study",
    options={
        "setup":      {"ph": 7.0, "forcefield": "amber-openff"},
        "simulation": {"duration_ns": 100, "random_seed": 42},
        "analysis":   {"include": ["rmsd", "rmsf", "ss"]},
        "report":     {"author": "A. Researcher"},
    },
)
```

Those are exactly the Config's phase blocks, so anything in the
[Config reference](config_reference.md) works here.

There are **no `setup=` or `simulation=` keyword arguments.** Passing settings
as top-level keywords raises `TypeError`.

### From a Config file

```python
fastmdx.FastMDXplora(config="study.yml").explore()
```

### From a Config you built in Python

```python
study = {
    "systems": [{"id": "wt", "system": "1UBQ"}],
    "simulation": {"duration_ns": 100},
    "analysis": {"include": ["rmsd", "rmsf", "rg"]},
}
runs = fastmdx.FastMDXplora(config_data=study, output_dir="runs/study").explore()
```

`config_data` takes the same shape as a Config on disk, so it **must carry a
`systems` list** — a single system is a list of one. This is the route to take
when a script is assembling studies, and it is what the CLI and the
[Agent](agent.md) use internally.

To check one before running it:

```python
from fastmdxplora.config import validate_config, ConfigError

try:
    validate_config(study, require_systems=True)
except ConfigError as exc:
    print(exc.code, exc)
```

---

## Running it

```python
study.explore(
    include=None,       # list[str] | None
    exclude=None,       # list[str] | None
    options=None,       # dict[str, dict] | None — merged over the constructor's
    report=True,        # False drops the report phase
    dry_run=False,      # plan only; writes nothing
    force=False,        # run into an output directory that already holds results
)  # -> list[RunResult]
```

```python
study = fastmdx.FastMDXplora(system="1UBQ", output_dir="runs/study")
study.explore(include=["setup", "simulation"])   # stop after the trajectory
study.explore(exclude=["report"])                # everything but the write-up
```

Or one phase at a time. Each takes the same settings as its Config block and
returns a `PhaseResult`:

```python
study.setup(ph=7.0, forcefield="amber-openff")
study.simulate(duration_ns=100, platform="CUDA")
study.analyze(include=["rmsd", "rmsf"])
study.report(author="A. Researcher")
```

Note the names: the methods are `simulate` and `analyze`, the phases are
`"simulation"` and `"analysis"`.

And, for a study of more than one run:

```python
study.compare(output_dir=None)   # -> Path | None — rebuilds comparison/
```

`compare` returns `None` where there are fewer than two successful runs or no
comparable analysis output.

---

## What comes back

`explore()` returns **a list of `RunResult`, always** — a single study is a
list of one with `run_id="s1"`; a sweep or an umbrella study is a list of many.

```python
from fastmdxplora.orchestrator import RunResult, PhaseResult

for run in runs:
    print(run.run_id, run.status, run.output_dir)
    for phase in run.phases:
        print("  ", phase.name, phase.status, phase.message)
```

```python
@dataclass
class RunResult:
    run_id: str
    system: str
    status: str                  # "ok" | "error" | "skipped" | "planned"
    output_dir: Path | None
    sweep_values: dict           # which sweep point this run is
    phases: list[PhaseResult]
    message: str
    error_type: str | None
    def to_dict(self) -> dict
    def phase(self, name: str) -> PhaseResult | None
```

```python
@dataclass
class PhaseResult:
    name: str                    # "setup" | "simulation" | "analysis" | "report"
    status: str                  # "ok" | "skipped" | "error"
    output_dir: Path | None
    started_at: str              # ISO-8601 UTC
    finished_at: str
    message: str
    artifacts: list[str]         # relative to output_dir
    refusal: dict                # Refusal.as_dict(), on an errored phase
    produced_by: dict            # {version, host, environment}
    def to_dict(self) -> dict
```

Two things to know about the shape of a result:

**A run stops at its first errored phase.** Later phases never run and are
absent from `.phases` entirely. The run's own `status` is `"error"` if any
phase errored.

**The same information is on disk**, in the run's
[Manifest](manifest.md). Read that when the process has ended.

```python
import json
manifest = json.loads((runs[0].output_dir / "manifest.json").read_text())
```

---

## Handling a refusal

Every failure carries a stable identifier alongside its sentence.

```python
from fastmdxplora import refusal_of
from fastmdxplora.refusals import StudyError

try:
    runs = study.explore()
except StudyError as exc:
    refusal = refusal_of(exc)
    refusal.code        # 'setup.chemistry.protonation_undetermined'
    refusal.kind        # 'semantic'
    refusal.retryable   # False
    refusal.permitted   # None — the software does not know the answer
```

`refusal_of` is total: anything you hand it comes back as a `Refusal`, even an
exception from a dependency. And on a phase that errored without raising, the
same record is already there:

```python
runs[0].phase("setup").refusal["code"]
```

See [FastMDXplora refusals](refusals.md) for the four kinds and what each one
means you should do.

---

## Measuring a trajectory directly

`AnalysisOrchestrator` is the analysis phase on its own, for a trajectory that
came from anywhere.

```python
from fastmdxplora import AnalysisOrchestrator

runner = AnalysisOrchestrator(
    "production.xtc",
    "system.pdb",
    output_dir="runs/analysis",
    scope="solute",              # or selection="protein and name CA"
    ligand_resname="BNZ",
    stride=2, first=100, last=5000,
    figure_colours="colour",
)
results = runner.run(include=["rmsd", "rmsf", "rg"])   # -> dict[str, AnalysisResult]

results["rmsd"].status        # "ok" | "error" | "skipped"
results["rmsd"].figure_path
results["rmsd"].data_path
results["rmsd"].options_path  # what it actually used
```

The trajectory is loaded at construction, onto `runner.traj`.
`runner.analyze(...)` is an alias for `run`.

The registry is open:

```python
from fastmdxplora.analysis import available_analyses, get_analysis_class, register_analysis

available_analyses()              # the 27 names, in execution order
get_analysis_class("rmsd")
register_analysis("my_measure", MyAnalysis)
```

Writing one is in [Developing FastMDXplora](developers.md).

---

## Working with Configs

```python
from fastmdxplora.config import (
    load_config_file,     # (path) -> dict
    validate_config,      # (data, *, require_systems=False) -> None; raises ConfigError
    phase_options,        # (data) -> dict[str, dict]
    generate_template,    # (*, minimal=False) -> str
    write_resolved_config,# (merged, output_dir, *, filename="resolved_config.yml") -> Path
    PHASE_SCHEMAS, PhaseSchema, ConfigError,
)

study = load_config_file("study.yml")
validate_config(study, require_systems=True)
```

`load_config_file` parses and normalises; it does **not** validate. The two are
separate calls on purpose, so a tool can load a file it knows is incomplete.

To see the schema itself:

```python
from fastmdxplora.config.describe import describe_schema, schema_as_json

describe_schema(phases=["setup", "simulation"])   # prose, as the Agent is given it
schema_as_json()                                  # the same, structured
```

---

## Knowing how long, before committing the card

The same study is an afternoon on one GPU and a fortnight on another, and that
difference is larger than any difference between two studies. So the machine is
measured once, and estimates come from the measurement.

```python
from fastmdxplora.cost import measure_this_machine, estimate_study, calibrate_from_runs

measure_this_machine()
# k = 5.0e-07 s per particle-step, from 2000 steps on 3000 particles in 3.0s

estimate_study(study, particles=62_000, platform_name="CUDA", precision="mixed")
# about 5.0 days for 25,000,000 steps on 62,000 particles
```

This is covered, with what it does and does not account for, in
[Production runs and GPUs](production.md#knowing-how-long-before-committing-the-card).

---

## Reading a result's statistics

```python
from fastmdxplora.statistics import summarise, sampling_shortfall

equilibrated, why = summarise(rmsd_series)
if why is not None:
    print(why)          # a sentence, and refusal_of(why).code is an identifier
else:
    equilibrated.mean, equilibrated.standard_error, equilibrated.effective_samples
```

`summarise` returns `(None, reason)` rather than a mean it cannot stand behind.
`sampling_shortfall` says how much further the run would have to go:

```python
print(sampling_shortfall(rmsd_series, target_independent=10, frame_interval_ns=0.01))
# 8.7 independent samples of the 10 needed. At one every 54 frames,
# that is 72 further frames, about 0.72 ns more.
```

Both numbers come from the same statistical inefficiency measured from *this*
series rather than assumed, so the answer is for this system rather than for a
typical one. Two runs of the same length can need very different amounts of
further sampling for the same claim, and the difference is not visible in the
length.

---

## Watching a run from Python

There is no separate Python dashboard. Serve the output directory with the GUI
from another terminal while the Python process runs:

```bash
fastmdx gui --output runs/study
```

That gives telemetry, the molecule in 3D and the results as they appear — the
same as for a run started from the command line.

---

## Logging

Importing FastMDXplora does not take over your logging. The package attaches a
console handler and leaves propagation alone, so records reach your handlers
too.

```python
from fastmdxplora.utils.logging import own_the_console, release_the_console
```

`own_the_console()` stops propagation, which is right for a program that owns
the terminal and ends when the run does. The CLI calls it; nothing else should.

---

## What is public

| Module | Status |
|---|---|
| `fastmdxplora` | The entry class, the analysis orchestrator, refusals, version metadata |
| `fastmdxplora.analysis` | `AnalysisOrchestrator`, `Analysis`, `AnalysisResult`, the registry, `load_trajectory` |
| `fastmdxplora.config` | Schema, loader, template generation |
| `fastmdxplora.setup` / `.simulation` / `.report` | Each phase's `run()` and its direct-use helpers |
| `fastmdxplora.refusals`, `.statistics`, `.cost`, `.provenance`, `.explain`, `.advisories` | Public |
| `fastmdxplora.validation` | Measurements of the software's own behaviour — see [How FastMDXplora is validated](validation.md) |
| `fastmdxplora.agent` | [The Agent](agent.md), plus campaign machinery |
| `fastmdxplora.batch` | The execution layer underneath `FastMDXplora`. Usable, but `FastMDXplora(config=…)` is the supported path |
| `fastmdxplora.utils` | Internal |

Module-level names beginning with `_` are internal and may change without
notice. Every package declares an `__all__`; that is the contract.

The top-level `__all__`:

```python
FastMDXplora, AnalysisOrchestrator, Refusal, refusal_of,
MIN_PYTHON, MAX_PYTHON, python_range_string,
__version__, __author__, __license__, __expansion__, __citation__, __doi__
```

`AnalysisOrchestrator` resolves lazily, so `import fastmdxplora` does not pull
in MDTraj, matplotlib and scikit-learn.

Python 3.9 to 3.13.

---

## Generated reference

Generated from the source, so it says what the code says. Every entry carries a
`[source]` link to the implementation.

Docstrings here are longer than reference documentation usually is, because a
docstring is where a decision gets recorded: what a measure computes, what it
refuses and why, and which choices move the number. That is the material to
read before comparing a result against another tool.

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

### The Config

```{eval-rst}
.. automodule:: fastmdxplora.config.schema
   :members:
   :undoc-members:

.. automodule:: fastmdxplora.config.loader
   :members:
```

### Refusals and statistics

```{eval-rst}
.. automodule:: fastmdxplora.refusals
   :members:

.. automodule:: fastmdxplora.statistics
   :members:

.. automodule:: fastmdxplora.cost
   :members:

.. automodule:: fastmdxplora.provenance
   :members:
```

### The phases

```{eval-rst}
.. automodule:: fastmdxplora.setup.prepare
   :members:

.. automodule:: fastmdxplora.setup.pdbfix
   :members:

.. automodule:: fastmdxplora.setup.ligand
   :members:

.. automodule:: fastmdxplora.simulation.runner
   :members:

.. automodule:: fastmdxplora.simulation.metadynamics
   :members:

.. automodule:: fastmdxplora.simulation.umbrella
   :members:

.. automodule:: fastmdxplora.simulation.steered
   :members:

.. automodule:: fastmdxplora.simulation.restraints
   :members:

.. automodule:: fastmdxplora.analysis.base
   :members:

.. automodule:: fastmdxplora.analysis.orchestrator
   :members:
```

### Campaigns

```{eval-rst}
.. automodule:: fastmdxplora.batch.explorer
   :members:

.. automodule:: fastmdxplora.batch.sweep
   :members:
   :no-index:

.. automodule:: fastmdxplora.batch.compare
   :members:

.. automodule:: fastmdxplora.batch.aggregate
   :members:
```

### The Agent

```{eval-rst}
.. automodule:: fastmdxplora.agent.propose
   :members:

.. automodule:: fastmdxplora.agent.models
   :members:

.. automodule:: fastmdxplora.agent.staged
   :members:
   :no-index:

.. automodule:: fastmdxplora.agent.queue
   :members:
   :no-index:
```

The full per-analysis reference — every one of the 27 classes, its options and
what each changes — is in [The FastMDXplora analyses](analyses.md).

---

## See also

- **[The FastMDXplora Config](config.md)** — every setting `options` accepts
- **[The FastMDXplora Manifest](manifest.md)** — what a finished run records
- **[Worked examples](examples.md)** — recipes, several with Python
