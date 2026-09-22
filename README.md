<div align="center">

# FastMDXplora

**Molecular dynamics from a PDB code to a finished study — in one command.**

[![DOI](https://img.shields.io/badge/DOI-10.1002%2Fjcc.70350-blue?labelColor=black)](https://doi.org/10.1002/jcc.70350)
[![PyPI](https://img.shields.io/pypi/v/fastmdxplora?label=pypi&labelColor=black)](https://pypi.org/project/fastmdxplora/)
[![conda-forge](https://img.shields.io/conda/vn/conda-forge/fastmdxplora?label=conda-forge&color=44A833&labelColor=black)](https://anaconda.org/conda-forge/fastmdxplora)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/fastmdxplora?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/fastmdxplora)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?labelColor=black)](https://pypi.org/project/fastmdxplora/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?labelColor=black)](https://opensource.org/licenses/MIT)

[![Tests](https://img.shields.io/github/actions/workflow/status/aai-research-lab/FastMDXplora/tests.yml?branch=main&label=tests&labelColor=black)](https://github.com/aai-research-lab/FastMDXplora/actions/workflows/tests.yml)
[![codecov](https://img.shields.io/codecov/c/github/aai-research-lab/FastMDXplora/main?labelColor=black)](https://codecov.io/gh/aai-research-lab/FastMDXplora)
[![Docs](https://img.shields.io/readthedocs/fastmdxplora?label=docs&labelColor=black)](https://fastmdxplora.readthedocs.io)

[**Quick start**](https://fastmdxplora.readthedocs.io/en/latest/first_study.html) ·
[**Agent**](https://fastmdxplora.readthedocs.io/en/latest/agent.html) ·
[**Cite**](#citation)

</div>

---

```bash
fastmdx explore --system 181L
```

```
setup  →  simulation  →  analysis  →  report
```

Four characters of PDB ID as input. FastMDXplora fetches T4 lysozyme,
parameterises the benzene bound in its cavity, runs the dynamics, analyses the
trajectory, works out which residues hold the ligand in place, and writes the
whole study up as a PDF.

Or configure the whole MD study in the GUI:

```bash
fastmdx gui
```

## Install

```bash
conda create -n fastmdxplora -c conda-forge fastmdxplora
conda activate fastmdxplora
```

`fastmdx info` lists every backend and how to get anything missing:

```bash
fastmdx info
```

## What you can study

| | |
|---|---|
| **A protein on its own** | Fold, flexibility, secondary structure, native contacts, conformational clustering — from a PDB code. Fluctuations can be set against the crystal's own B-factors, and backbone order parameters against what NMR relaxation measures. |
| **A protein with a ligand** | The ligand is found, its chemistry resolved, its protonation settled in the binding site. Eight interaction types against published criteria tell you what *holds* it, not just what it touches. |
| **A membrane protein** | Embedded in one of seven bilayers, with the orientation checked rather than assumed and pressure coupling that suits a lipid system. |
| **Free energy along a coordinate** | Umbrella sampling, metadynamics and steered MD from a named collective variable — nine of them, one or two at a time — without writing PLUMED input. Each says what its output is and is not: a surface if the bias converged, a pathway and the work along it, a potential of mean force if the windows overlap. |
| **A trajectory from another engine** | Skip the simulation and analyse what you already have — GROMACS `.xtc` and `.trr`, Amber `.nc`, NAMD and CHARMM `.dcd`, LAMMPS `.lammpstrj`, or `.pdb`, `.cif` and `.h5` that carry their own topology. |
| **Many systems at once** | Mutants against wild type — named as `L99A`, and checked against the residue actually there — a sweep across a setting, runs pinned one per GPU, and a comparison report across all of them. Where the members differ only by seed, the spread of their means is set against the error each run claimed for itself. |

**It refuses rather than guesses.** An ambiguous ligand charge, a protein
backwards in its membrane, a free-energy surface that never converged — each
stops the run and is named, not papered over. Biased averages are corrected
to equilibrium where the bias allows and labelled where it does not. Every
step explains itself and cites the paper worth reading. What comes out, you
can defend; what you cannot is marked.

## The config is the study

A FastMDXplora config is the whole description of a molecular dynamics study:
the system, how it is prepared, how it is simulated, what is measured, and how
it is written up. Capture that, and the four phases — setup, simulation,
analysis, report — run themselves.

```yaml
systems:
  - system: 181L
simulation:
  duration_ns: 100
```

That is a complete study. Everything unnamed takes a documented default, and
every run writes `resolved_config.yml` — the file, the command line and the
API arguments merged — so the exact study can be run again by anyone holding
that one file.

**Three ways to build a config**, and each of them also runs all four phases:

| | |
|---|---|
| **The GUI** | `fastmdx gui`. A form generated from the schema, so every setting is reachable. Worth using even for a command-line or Python workflow: build the study where the options are visible and explained, then take the file away. |
| **The CLI** | `fastmdx explore --config study.yml`, or `fastmdx init-config` for a commented template, or a flag for any setting. |
| **The Python API** | `FastMDXplora(config="study.yml").explore()`, or the same blocks passed as options. |

None of these is the primary interface and none is a subset of another —
form, flags and API are generated from one declaration — and a study designed
in the GUI on a laptop runs unchanged on a cluster, because what travels is
the config.

**And a fourth, in place of a person.** The FastMDXplora Agent writes a config
from a sentence, and is reachable through all three of the above:

```bash
fastmdx agent "simulate trypsin with benzamidine bound at pH 6.5 for 100 ns"
```

It gets no special treatment. What it writes goes through exactly the same
validator, by the same code, with the same refusals, as a config typed by hand
— and a proposal that did not validate carries no config at all.

## The manifest is the result

What a config is to a study, `manifest.json` is to its result: every phase that
ran, every artifact it produced, every setting it used, and the exact software
stack it ran on — including which package versions were loaded, because those
decide numbers. A run directory is readable by somebody who was not there.

## Documentation

**Start here** — [Install](https://fastmdxplora.readthedocs.io/en/latest/installation.html) ·
[Your first study](https://fastmdxplora.readthedocs.io/en/latest/first_study.html) ·
[How FastMDXplora works](https://fastmdxplora.readthedocs.io/en/latest/how_it_works.html)

**The Config** — [The FastMDXplora Config](https://fastmdxplora.readthedocs.io/en/latest/config.html) ·
[Config reference](https://fastmdxplora.readthedocs.io/en/latest/config_reference.html) ·
[Selections](https://fastmdxplora.readthedocs.io/en/latest/selections.html) ·
[Examples](https://fastmdxplora.readthedocs.io/en/latest/examples.html)

**Writing a Config** — [GUI](https://fastmdxplora.readthedocs.io/en/latest/gui.html) ·
[CLI](https://fastmdxplora.readthedocs.io/en/latest/cli.html) ·
[API](https://fastmdxplora.readthedocs.io/en/latest/api.html) ·
[Agent](https://fastmdxplora.readthedocs.io/en/latest/agent.html)

**Going further** — [Restraints, membranes, enhanced sampling](https://fastmdxplora.readthedocs.io/en/latest/studies.html) ·
[Production and GPUs](https://fastmdxplora.readthedocs.io/en/latest/production.html) ·
[Running elsewhere](https://fastmdxplora.readthedocs.io/en/latest/clusters.html)

**Results** — [The FastMDXplora Manifest](https://fastmdxplora.readthedocs.io/en/latest/manifest.html) ·
[Reading the results](https://fastmdxplora.readthedocs.io/en/latest/results.html) ·
[The analyses](https://fastmdxplora.readthedocs.io/en/latest/analyses.html) ·
[Protein-ligand interactions](https://fastmdxplora.readthedocs.io/en/latest/interactions.html) ·
[Refusals](https://fastmdxplora.readthedocs.io/en/latest/refusals.html)

**For developers** — [Developing FastMDXplora](https://fastmdxplora.readthedocs.io/en/latest/developers.html) ·
[How it is validated](https://fastmdxplora.readthedocs.io/en/latest/validation.html)

## Citation

> Aina, A.; Kwan, D. *FastMDAnalysis: Software for Automated Analysis of Molecular Dynamics Trajectories.* J. Comput. Chem. **2026**, 47, e70350. DOI: [10.1002/jcc.70350](https://doi.org/10.1002/jcc.70350)

```bibtex
@article{aina2026fastmd,
  author  = {Aina, Adekunle and Kwan, Derrick},
  title   = {FastMDAnalysis: Software for Automated Analysis of Molecular Dynamics Trajectories},
  journal = {Journal of Computational Chemistry},
  volume  = {47},
  number  = {8},
  pages   = {e70350},
  year    = {2026},
  doi     = {10.1002/jcc.70350},
}
```

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). FastMDXplora
follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## License

MIT. See [LICENSE](LICENSE).

---

<div align="center">

Built in the [AAI Research Lab](https://aai-research-lab.github.io) at
California State University Dominguez Hills, on MDTraj, OpenMM, PDBFixer,
OpenFF, RDKit, NumPy, SciPy, scikit-learn and Matplotlib.

</div>
