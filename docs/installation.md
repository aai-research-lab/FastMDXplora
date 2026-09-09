# Installation

## Recommended: conda-forge

```bash
conda create -n fastmdxplora -c conda-forge fastmdxplora
conda activate fastmdxplora
fastmdx info
```

That gives you everything: the simulation engine, the ligand chemistry stack,
the PDF report, enhanced sampling. `fastmdx info` confirms it.

conda-forge is the recommendation and not a preference. Two of the packages
FastMDXplora depends on — the OpenFF toolkit and openmm-plumed — **have no
PyPI distribution at all**, so no pip command can reach them.

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

**The ligand path** needs the OpenFF toolkit, which pip cannot install:

```bash
conda install -c conda-forge openff-toolkit openmmforcefields
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

It carries rdkit, propka and openff-toolkit for ligand chemistry and pKa
assignment, openmm-plumed for enhanced sampling, weasyprint for the PDF
report, and scipy, pillow and netcdf4. A test checks this file against the
declared dependencies, so an environment built from it runs every phase.

---

## Platforms

| | |
|---|---|
| **Linux** | Everything, including CUDA. The usual choice for production. |
| **macOS** | Everything. Apple Silicon runs on the CPU platform; OpenCL is not usable, and FastMDXplora falls back automatically and says so. |
| **Windows** | Analysis and reporting run natively. Simulation needs WSL2, for the reasons in the next section. |

Python 3.9 to 3.13.

---

## Windows

*Written from what continuous integration and the dependency declarations
show, and not yet run end to end on a Windows machine. Corrections welcome:
open an issue with what you hit and this section will say so.*

Two thirds of FastMDXplora runs natively on Windows and one third does not,
and it is worth knowing which before you start rather than after.

**What works natively.** The analysis, reporting and GUI layers, and the
whole test suite: continuous integration runs it on `windows-latest` for
Python 3.9, 3.11 and 3.13 alongside Linux and macOS. If your trajectories
come from somewhere else -- a cluster, a collaborator, GROMACS -- and you
want the measures, the report and the GUI, install the base
package and stop there:

```powershell
py -m pip install fastmdxplora
fastmdx info
```

**What needs Python 3.10 or newer.** Running dynamics needs OpenMM, and
OpenMM publishes no Windows wheel for Python 3.9. On 3.10 and above:

```powershell
py -m pip install "fastmdxplora[md]"
```

**What does not work natively, and why.** Ligand parameterisation and
enhanced sampling depend on packages that are distributed only through
conda-forge, and PLUMED has no Windows build at all. This is upstream and
not something FastMDXplora can route around:

| | |
|---|---|
| `openmmforcefields`, `openff-toolkit` | conda-forge only, so a ligand cannot be parameterised natively |
| `openmm-plumed`, `plumed` | no Windows build; umbrella sampling, metadynamics and steered pulling are unavailable |

**For the whole stack, use WSL2.** It is a first-class Linux environment
on the same machine, not an emulator, and the Linux instructions above
apply unchanged inside it:

```powershell
wsl --install -d Ubuntu
```

Then, in the Ubuntu shell that opens, follow
[the conda-forge instructions](#recommended-conda-forge) exactly as
written. WSL2 runs a real Linux kernel rather than translating system
calls, which is why the conda stack behaves there exactly as it does on a
cluster, and an NVIDIA GPU in the same machine is visible to OpenMM
through it.

Your Windows drives appear at `/mnt/c/`, so a config can be edited in
Windows and run in WSL2 without copying. **Run the study itself in the
Linux filesystem, though.** Reads and writes across the `/mnt/c/` bridge
are much slower than native ones, and a trajectory is hundreds of
megabytes: keep `output` under `~/runs/` and reach across the bridge for
inputs and for the report, not for every frame.

**Whatever you install, `fastmdx info` tells you what you got.** It prints
the phases that are available and names what is missing, so a study that
cannot run says so before it starts rather than partway through:

```powershell
fastmdx info
```

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

- [Your first run](getting_started.md)
- [The FastMDXplora GUI](gui.md)
- [Running somewhere else](remote.md) — clusters, containers, and machines
  with no network
