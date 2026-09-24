# Developing FastMDXplora

For working **on** FastMDXplora rather than with it. Everything on this page
is about changing the software.

Contributing guidelines, the review process and the linter rules are in
[`CONTRIBUTING.md`](https://github.com/aai-research-lab/FastMDXplora/blob/main/CONTRIBUTING.md);
the repository layout is in `STRUCTURE.md`.

---

## The development install

```bash
git clone https://github.com/aai-research-lab/FastMDXplora.git
cd FastMDXplora
conda env create -f environment.yml
conda activate fastmdxplora
pip install -e ".[dev]"
pytest -q
```

`environment.yml` carries the conda-only packages — openmm-plumed above all,
which has no PyPI distribution — so a clone set up this way has the whole
stack: rdkit, propka, openff-toolkit and ambertools for ligand chemistry,
charges and pKa assignment, openmm-plumed for enhanced sampling, weasyprint and markdown for
the PDF report, umap-learn for the dimensionality reduction that offers it, and
scipy, pillow and netcdf4.

**What it does not carry is MDAnalysis and ProLIF**, the `validation` extra.
That is deliberate: they exist here to compare against, and a comparison
between two stacks means less when one environment holds both. Install them
separately when you want to run that comparison —
[How FastMDXplora is validated](validation.md).

A test holds `environment.yml` to that paragraph — every requirement named, at
a floor no lower than `pyproject.toml`'s — so a conda environment and a pip
install are the same software rather than two stacks wearing one name.

**Do not `pip install -U fastmdxplora` into the same environment.** It replaces
the editable install with an ordinary one, reports success, and says nothing
about what it displaced; every `pytest` afterwards imports from
`site-packages`. `tests/test_the_tests_run_against_this_checkout.py` fails first
and says so.

---

## The architecture, in one idea

**The schema is the declaration, and everything reads it.**

`src/fastmdxplora/config/schema.py` declares every setting once — its name,
type, default, choices, bounds and help text. Four things are generated from
it, and none of them is maintained by hand:

| Reader | Produces |
|---|---|
| `cli/main.py` | Every command-line flag, and the sections `--help` prints |
| `config/loader.py` | What a [Config](config.md) may say, and every refusal it raises |
| `gui/schema_payload.py` | Every control in the [GUI](gui.md) form, and its grouping |
| `config/describe.py` | The prose the [Agent](agent.md) writes from |

`SETTING_GROUPS` holds the shared ordering, and a test asserts that `--help`
and the GUI read in the same order.

The consequence to internalise: **adding a setting to the schema is the whole
change.** It appears as a flag, as a Config key, as a form control and in the
Agent's prompt, at the right default, with the right help, immediately. The
form was hand-written once and offered 11 of 83 settings, with whole phases
unreachable.

The direction of the dependency also matters and is asserted by a test:
`fastmdxplora.agent` imports from the core, and **nothing in the core imports
from the agent**. That is what makes the claim in
[the Agent page](agent.md#the-agent-has-no-privileges) checkable rather than
asserted.

---

## A setting that validates is a setting that runs

`simulation.resume_from` was once added to the schema, added to
`run_simulation`'s signature, and never connected between the two. A segment's
Config said where to continue from, validation accepted it, and the run started
from the pre-equilibration state instead. Silently.

That is this package's own failure mode, occurring inside it. The loader
refuses a setting it does not know; nothing was checking that a setting it
**does** know reaches the code that would honour it.

So a test now holds the property: **every option the simulation schema declares
must be read where the runner's arguments are built, or be named in a short
list with a reason saying what else consumes it.** An exemption without a reason
is how the bug comes back.

If you add a setting, wire it, or add it to that list with a reason.

---

## Raising a refusal

Prefer the specific exception class where one exists — `LigandError`,
`ProtonationError`, `TrajectoryLoadError` and the rest each say something a
generic class does not.

Where none fits, raise `fastmdxplora.refusals.StudyError`. It subclasses
`ValueError`, so anything catching `ValueError` today keeps catching it, and
unlike a bare `ValueError` it can carry a code:

```python
from fastmdxplora.refusals import StudyError

raise StudyError(
    f"Unknown force field {name!r}. Valid choices: {valid}.",
    code="setup.forcefield.unknown",
    given=name, permitted=sorted(_REGISTRY),
)
```

**The prose comes first and stays whole.** It is what a person reads, and it
should not be assembled from the details by a formatter that does not know
which particulars matter. The details repeat what the sentence says, for a
reader that is not a person.

Three rules for a new code:

1. **Pick the kind by who can fix it** — `STRUCTURAL`, `SEMANTIC`,
   `ENVIRONMENTAL` or `INSUFFICIENT`. See
   [FastMDXplora refusals](refusals.md#the-four-kinds).
2. **Declare the disclosure in the registry, not at the raise site.**
   `Refusal.permitted` reads the registry, so a raise site cannot widen a
   disclosure by accident. The rule: the validator may say what the schema
   permits, and may never say what the chemistry requires.
3. **Adding a code is a minor change; changing what one means is not.** Rename
   through `SUPERSEDED`, which `resolve()` reads, so callers written against the
   old identifier keep working.

```python
from fastmdxplora.refusals import CODES, codes_under
for code in codes_under("analysis.sampling"):
    print(code.id, "--", code.summary)
```

---

## Writing an analysis

An analysis is one class and one registration. The registry is populated by
import order in `analysis/__init__.py`, and **that order is the default
execution order**.

```python
from typing import Any
import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis


class EndToEndish(Analysis):
    """One sentence saying what this measures — and what it does not."""

    name = "end_to_endish"
    description = "Distance between the first and last alpha carbon"
    time_series = True                              # one value per frame
    reweightable = (None, "Distance (nm)")          # a mean worth correcting
    default_selection = "name CA"

    def __init__(self, *, squared: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.squared = bool(squared)
        self.options.update(squared=self.squared)   # so options.json records it

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        idx = self.select_atoms(traj)
        d = np.linalg.norm(traj.xyz[:, idx[-1]] - traj.xyz[:, idx[0]], axis=1)
        return d**2 if self.squared else d

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        ax.plot(result)


register_analysis(EndToEndish.name, EndToEndish)
```

`Analysis.run()` then handles the rest: the data file, the figure, the greyscale
variant, and `options.json`.

### The class attributes that matter

| Attribute | What it declares |
|---|---|
| `name` | The identifier used in `analysis.include`, the output directory and the manifest |
| `description` | Used in figure titles |
| `time_series` | `compute` returns one value per frame — **declared, not inferred**, because a per-atom result on a trajectory with as many frames as atoms would otherwise be summarised as a time series and look right |
| `honours_selection` | Whether `selection` means anything here. A protein–ligand measure works both sides out from the residue name, so offering a control that does nothing is worse than not offering one |
| `default_selection` | What it measures when nothing narrower is given |
| `min_atoms_to_align` | The floor below which a superposition is undefined. Alanine dipeptide has one CA, and MDTraj responded with "UNCONVERGED ROTATION MATRIX. RETURNING IDENTITY" once per frame and a column of numbers that looked like results |
| `reweightable` | `(column, label)` — the per-frame scalar whose mean can be corrected on a biased run |
| `reweightable_populations` | `compute` returns per-frame categorical labels, so populations reweight as means do |

### The gates

Declare what the analysis needs and the orchestrator leaves it out of the
default plan when the run does not have it. A gated analysis is **still run
when named in `include`**.

| Attribute | Runs only when |
|---|---|
| `requires_ligand` | A ligand residue name is known |
| `requires_water` | The trajectory carries water residues |
| `requires_periodic_box` | The trajectory carries unit-cell vectors |
| `requires_amide_hydrogens` | The topology has backbone N–H pairs |
| `requires_crystallographic_bfactors` | The input PDB carries real B-factors |
| `requires_state_record` | `simulation/energy.csv` exists |
| `requires_tertiary_structure` + `min_seq_separation` | The solute has more residues than the separation |
| `requires_umbrella` / `requires_metadynamics` / `requires_steered` | That method's result file exists |
| `requires_naming` | **Never automatically.** The measure is between two named things and the trajectory does not contain which two |

### Two invariants to respect

**Do not mutate the trajectory.** Each analysis is handed its own copy
(`analysis.run(self.traj[:])`) because MDTraj's superposition rotates in place.
An interaction analysis once reported 252 hydrophobic contacts after RMSF and
ligand RMSD had aligned the frames, and 10 when run alone. The 10 was right.

**Put every option in `self.options`.** It is what `options.json` records and
what makes two disagreeing measurements of the same trajectory resolvable. Also
note that unknown options are refused rather than ignored — every analysis ends
in `**kwargs`, so `n_clusteres` used to cluster at the default and report
success.

---

## Logging

`setup_console()` attaches a console handler. It used to also set
`propagate = False` on the `fastmdx` logger, which stops records reaching anyone
else's handlers.

That is right for the command line, which owns the terminal and ends when the
run does. It was wrong everywhere else: importing the package and running one
study left the caller's own logging permanently unable to see anything from
FastMDXplora, silently, for the rest of the session — and `caplog` stopped
working in their tests.

The decision now has a name and one caller:

```python
from fastmdxplora.utils.logging import own_the_console, release_the_console
```

`own_the_console()` stops propagation. **The CLI calls it; nothing else does**,
and a test counts the call sites so a second one shows up in review rather than
in somebody's logs.

The trade is not free, and it is the better of the two available. A caller who
has configured root handlers will see this package's records twice: once
through FastMDXplora's handler, once through theirs. Doubled output is visible
and the handler can be turned off. Silent swallowing is invisible and there is
nothing they can do about what they cannot see.

Three environment variables control console behaviour: `FASTMDX_LOG_STYLE`,
`FASTMDX_LOGLEVEL` and `NO_COLOR`.

---

## Where the phases live

| Phase | Entry point |
|---|---|
| `setup` | `fastmdxplora.setup.pipeline.run` |
| `simulation` | `fastmdxplora.simulation.pipeline.run` |
| `analysis` | `fastmdxplora.analysis.analyze.run` |
| `report` | `fastmdxplora.report.run` |

Each is `run(orchestrator, output_dir, **kwargs)`, lazily imported, and the
canonical phase order is declared exactly once, at
`fastmdxplora.orchestrator.PHASES`.

Each phase takes its defaults from the schema — `DEFAULTS = SETUP.defaults()` —
rather than from a second copy. The pH default was 7.4 in the schema and 7.0 in
the pipeline once.

**Every run goes through `BatchExplorer`**, including a single one: a one-system
Config is a batch of one. There is no separate single-run path to keep in step.

Each phase writes its own record before re-raising on failure, so the
parameters that produced a failure are not lost with it. See
[The FastMDXplora Manifest](manifest.md).

---

## Adding a collective variable

`simulation/metadynamics.py` holds `COLLECTIVE_VARIABLES`, shared by umbrella,
steered and metadynamics so a variable is named the same way whichever method
biases it.

**Each registry entry states what the variable separates and what it does
not.** That is not decoration: a variable that does not distinguish the states
that matter produces a surface that converges and describes something else, and
it does not announce itself. An entry without that sentence should not be
merged.

Unknown names raise `ValueError` listing `sorted(COLLECTIVE_VARIABLES)`.

---

## Measuring the natural-language interface

`propose_config` has a cycle counter, and what a real model does with
`describe_schema()` decides whether the [Agent](agent.md) is worth having. So it
is measured rather than assumed.

```bash
ANTHROPIC_API_KEY=... python scripts/measure_nli.py
ANTHROPIC_API_KEY=... python scripts/measure_nli.py --terse   # no help text
```

```
6/8 correct, 8/8 valid, 5 first time, 1.4 cycles on average
  ok    plain          1 cycle(s)
  valid salt           1 cycle(s)  setup.ion_concentration_M is 150, asked for 0.15
  refusals seen: config.option.unknown x2
```

Fifteen requests across three tiers, reported apart — one number over three
difficulties hides where a model stops rather than whether it succeeds.
**Easy** states the value outright. **Medium** makes the model supply what the
sentence did not: the number behind "physiological", a microsecond in
nanoseconds, four settings at once. **Hard** is where a plausible answer is
wrong.

Two numbers, and the second matters more:

- **Valid** is cycles to a Config the validator accepts. Cheap, comparable
  between models, and a direct reading of whether the generated schema
  description does its job.
- **Correct** is whether it meant what was asked. A Config can validate and be
  the wrong study: 300 K when the sentence said 310, a setting simply omitted,
  or a concentration given in millimolar where the field is molar. Validation
  catches ill-formed, not wrong. A harness reporting only the first would score
  every model perfectly and measure the thing nobody cares about.

Each request asserts only what its sentence specified. A request saying "at
pH 6.5" checks the pH and leaves the box shape alone — marking a model wrong
for choosing something it was never asked about would measure obedience rather
than comprehension.

**The refusal tally is the most useful output.** A code appearing in most runs
is not a model being careless. It is the schema description failing to say
something, and it says where to look.

Measured with `claude-sonnet-4-6` on the original eight:

| | first time | mean cycles | refusals |
|---|---|---|---|
| with help text | 7 / 8 | 1.1 | 1 |
| `--terse` | 3 / 8 | 2.0 | 8 |

Both reached 8/8. **The help does not change whether the model succeeds; it
changes how much work that takes.** And a model working from bare field names
still got every study right — the validator is carrying the weight, which is
the architecture's claim and now a measurement rather than an argument.
`--attempts` defaults to 3 on that evidence.

---

## See also

- **[How FastMDXplora is validated](validation.md)** — corpus, cross-tool and environment checks
- **[Protein-ligand interactions: implementation](interactions_design.md)** — why that analysis is implemented here rather than depended on
- **[The FastMDXplora API](api.md)** — what is public and what is not
