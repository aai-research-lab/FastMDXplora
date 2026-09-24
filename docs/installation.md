# Installing FastMDXplora

## Recommended: conda-forge

```bash
conda create -n fastmdxplora -c conda-forge fastmdxplora
conda activate fastmdxplora
fastmdx info
```

That gives you everything: the simulation engine, the ligand chemistry stack,
the PDF report, enhanced sampling. `fastmdx info` confirms it.

conda-forge is the recommendation and not a preference. Three of the packages
FastMDXplora depends on — the OpenFF toolkit, AmberTools and openmm-plumed —
**have no PyPI distribution at all**, so no pip command can reach them.

AmberTools is where a ligand's charges come from. Every ligand is given AM1-BCC
partial charges, which the OpenFF toolkit computes by calling AmberTools' `sqm`
program; the toolkit installs without it and then cannot charge anything. The
conda-forge package brings it, and `fastmdx info` reports it on the line
**AM1-BCC charges**. Where nothing can compute them, a study with a ligand
refuses before setup repairs or solvates anything, and says what to install.

---

## Installing from PyPI

If you already manage the chemistry stack, or you only need part of the
pipeline:

```bash
pip install fastmdxplora            # analysis and reporting
pip install "fastmdxplora[md]"      # adds setup and simulation
pip install "fastmdxplora[ligand]"  # adds most of ligand preparation
pip install "fastmdxplora[pdf]"     # adds the PDF report
```

Two of those are partial, and `fastmdx info` will tell you which parts are
missing and how to get them.

**The ligand path** needs the OpenFF toolkit, and AmberTools to give each
ligand its charges. Neither is on PyPI, so after a pip install they have to
come from conda-forge:

```bash
conda install -c conda-forge openff-toolkit openmmforcefields ambertools
```

**Enhanced sampling** needs openmm-plumed, likewise:

```bash
conda install -c conda-forge openmm-plumed
```

**The PDF report** needs Pango, Cairo and GDK-PixBuf as system libraries. The
conda-forge package brings its own; from PyPI they have to be there already:

```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0
```

Where they are missing, the run says so and writes the other formats rather
than failing.

---

## Working on FastMDXplora itself

```bash
git clone https://github.com/aai-research-lab/FastMDXplora.git
cd FastMDXplora
conda env create -f environment.yml
conda activate fastmdxplora
pip install -e ".[dev]"
pytest -q
```

The environment file carries the conda-only packages -- openmm-plumed above
all, which has no PyPI distribution -- so a clone set up this way has the whole
stack.

It carries rdkit, propka, openff-toolkit and ambertools for ligand chemistry,
charges and pKa assignment, openmm-plumed for enhanced sampling, weasyprint
and markdown for the PDF report, umap-learn for the dimensionality-reduction
analysis that offers it, and scipy, pillow and netcdf4.

What it does not carry is MDAnalysis and ProLIF, the `validation` extra. That
is deliberate: they exist here to compare against, and a comparison between
two stacks means less when one environment holds both. Install them
separately when you want to run that comparison.

`tests/test_the_conda_file_installs_what_it_claims.py` holds this file to
that paragraph — every requirement named, at a floor no lower than
`pyproject.toml`'s — so a conda environment and a pip install are the same
software rather than two stacks wearing one name.

---

## Platforms

| | |
|---|---|
| **Linux** | Everything, including CUDA. The usual choice for production. |
| **macOS** | Everything. Apple Silicon runs on the CPU platform; OpenCL is not usable, and FastMDXplora falls back automatically and says so. |

Python 3.10 to 3.13.

**Windows is not supported.** AmberTools, which gives every ligand its
charges, has no Windows build, so a Windows install cannot prepare a ligand
at all. Continuous integration, the package metadata and this guide cover
Linux and macOS, and `fastmdx info` says so when it runs on Windows.

---

## Checking what you have

```bash
fastmdx info
```

It lists every backend grouped by what it is for, and for anything missing it
gives the command that installs it. A backend that is present but will not
load — WeasyPrint without Pango, say — is reported as broken rather than
missing, because reinstalling something already there fixes nothing.

---

## If something goes wrong

**`fastmdx: command not found`** — the environment is not active. `conda
activate fastmdxplora`.

**A phase reports a backend missing** — `fastmdx info` names it and the
command to install it. This is the common case after a pip install.

**`No template found for residue ...`** — the force field has no parameters
for something in your structure. The message names the residue and what to do:
supply parameters, exclude it, or pass it as a ligand.

**Solver conflicts on conda** — install into a fresh environment rather than
an existing one. The chemistry stack pins a lot, and resolving it against
whatever is already there is usually slower than starting clean.

---

## Where to go next

- [Your first FastMDXplora study](first_study.md)
- [The FastMDXplora GUI](gui.md)
- [Running FastMDXplora elsewhere](clusters.md) — clusters, containers, and machines
  with no network
