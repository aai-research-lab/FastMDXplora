> ### Superseded by [`fastmdxplora`](https://pypi.org/project/fastmdxplora/)
>
> FastMDAnalysis is no longer developed. FastMDXplora has every analysis below,
> and adds system setup, production MD, enhanced sampling and free energies
> behind the same CLI and Python API.
>
> ```bash
> pip install fastmdxplora
> ```
>
> To reproduce published results, pin the last release: `pip install fastmdanalysis==1.1.0`.
> Version 1.1.1 adds this notice and changes nothing else.

---

[![DOI](https://img.shields.io/badge/DOI-10.26434/chemrxiv--2025--x8xnq-blue)](https://doi.org/10.26434/chemrxiv-2025-x8xnq)
[![Total PyPI downloads](https://pepy.tech/badge/fastmdanalysis)](https://pepy.tech/project/fastmdanalysis)

![FastMDAnalysis](assets/fastmdanalysis_banner.png)

[![Tests](https://github.com/aai-research-lab/FastMDAnalysis/actions/workflows/test.yml/badge.svg)](https://github.com/aai-research-lab/FastMDAnalysis/actions)
[![codecov](https://codecov.io/gh/aai-research-lab/FastMDAnalysis/branch/main/graph/badge.svg)](https://codecov.io/gh/aai-research-lab/FastMDAnalysis)
[![Docs](https://img.shields.io/badge/docs-latest-brightgreen.svg)](https://fastmdanalysis.readthedocs.io/en/latest/)
[![PyPI](https://img.shields.io/pypi/v/fastmdanalysis)](https://pypi.org/project/fastmdanalysis/)
[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Highlights
- Perform complex **molecular dynamics analyses** with intuitive, **single-line commands**
- Automatically generate **slide-ready, publication-quality figures** with consistent axes, fonts, and colorbars  
- Seamlessly switch between **Python API** for advanced workflows and **CLI** for rapid batch processing
- **Scalable workflows** that handle everything from quick exploratory analysis to large-scale production runs

<!-- Perform a variety of MD trajectory analyses with a single line of code -->
<!-- Simplify your workflow by loading a trajectory once (with options for frame and atom selection) and then performing multiple analyses without repeating input file details. --> 
<!--  Automatically generate publication-quality figures (with options for customization) -->
<!--  Use the Python API or the Command‐Line Interface (CLI) -->
<!-- [![Documentation](https://readthedocs.org/projects/fastmdanalysis/badge/?version=latest)](https://fastmdanalysis.readthedocs.io) 
<!-- [![Monthly PyPI downloads](https://pepy.tech/badge/fastmdanalysis/month)](https://pepy.tech/project/fastmdanalysis) --> 

---


## Analysis Modules
| Analysis | Description |
|----------|-------------|
| ``rmsd`` | Root-Mean-Square Deviation relative to a reference frame |
| ``rmsf`` | Per-atom Root-Mean-Square Fluctuation |
| ``rg`` | Radius of Gyration for molecular compactness |
| ``hbonds`` | Hydrogen bond detection and count using Baker-Hubbard algorithm |
| ``ss`` | Secondary Structure assignments using DSSP |
| ``cluster`` | Trajectory clustering using KMeans, DBSCAN, and Hierarchical methods |
| ``sasa`` | Solvent Accessible Surface Area with total, per-residue, and average per-residue |
| ``dimred`` | Dimensionality reduction using PCA, MDS, and t-SNE methods |
| ``qvalue`` | Fraction of Native Contacts for protein folding analysis |
| ``dihedrals`` | Phi, Psi, Omega angle analysis + Ramachandra Plots |

---

## Installation

We **strongly recommend installing ``FastMDAnalysis`` in a virtual environment** to avoid conflicts with system packages and ensure the ``fastmda`` command is available in your PATH.


### Install from conda-forge (Recommended)

```bash
conda create -n fastmda_env python=3.11
```
```bash
conda activate fastmda_env
```
```bash
conda install -c conda-forge fastmdanalysis
```

### Verify installation
```bash
fastmda analyze -h
```

### (Alternatively) Install from PyPI 
Make sure you have Python 3.9+ installed and available as 'python'.
```bash
python -m venv fastmda_env
```

```bash
# On Linux/macOS:
source fastmda_env/bin/activate

# Or on Windows:
# fastmda_env\Scripts\activate
```
```bash
pip install fastmdanalysis
```

### Verify installation
```bash
fastmda analyze -h
```

---

## Usage

### Command-Line Interface (CLI) 
After installation, you can run ``FastMDAnalysis`` from the command line using the `fastmda` command. Global options allow you to specify the trajectory and topology file paths.
Optionally, specify frame selection and atom selection. Frame selection is provided as a tuple (start, stop, stride). Negative indices (e.g., -1 for the last frame) are supported. If no options are provided, the entire trajectory and all atoms are used by default.

**Run the ``analyze`` orchestrator to execute multiple analyses in one go.**

**Run all available analyses**
```bash
fastmda analyze -traj path/to/trajectory -top path/to/topology
```
**Include specific analyses**
```bash
fastmda analyze -traj traj.dcd -top top.pdb --include rmsd rg
```
**Exclude specific analyses**
```bash
fastmda analyze -traj traj.dcd -top top.pdb --exclude sasa dimred cluster
```
**Supply options via file (YAML or JSON)**
```bash
fastmda analyze -traj traj.dcd -top top.pdb --options options.yaml
```
**Create a slide deck from generated figures**
```bash
fastmda analyze -traj traj.dcd -top top.pdb  --slides
```


**Global flags:**
- ``--frames start,stop,stride`` (e.g., ``0,-1,10``)
- ``--atoms "MDTraj selection"`` (e.g., ``"protein and name CA"``)
- ``--output DIR`` (output directory name)
- ``--verbose`` (prints progress and writes logs under ``<command>_output/`` unless ``--output`` is set)

**Show help:**
- ``fastmda -h``
- ``fastmda analyze -h``



**Options file (schema)**

Provide per-analysis keyword arguments in a single file. CLI and Python API share the same schema:
```yaml
# options.yaml
rmsd:
  ref: 0
cluster:
  methods: [kmeans, hierarchical]
  n_clusters: 5
```

**Slides:**
- ``--slides`` creates ``fastmda_slides_<ddmmyy.HHMM>.pptx`` in the current working directory.
- ``--slides path/to/deck.pptx`` writes to an explicit filename.




### Python API
Instantiate a `FastMDAnalysis` object with your trajectory and topology file paths. 

**Run the ``analyze`` orchestrator to execute all available analyses.**
```python
from fastmdanalysis import FastMDAnalysis
from fastmdanalysis.datasets import TrpCage  # optional helper

fastmda = FastMDAnalysis(TrpCage.traj, TrpCage.top)
fastmda.analyze()
```

**Include or Exclude specific analyses; specify options, generate slides**
```python
fastmda = FastMDAnalysis(TrpCage.traj, TrpCage.top)
result = fastmda.analyze(
    include=["rmsd", "rg"],                 # or exclude=[...]; omit to run all
    options={"rmsd": {"ref": 0, "align": True}},
    slides=True                             # or slides="results.pptx"
)
```

> **Notes** 
> - Figures are saved during each analysis; slide decks include all figures produced in the run.
> - MDTraj may emit benign warnings (e.g., dummy CRYST1 records); they do not affect results.

---

## Output
Output includes data tables, figures (already stylized for slides), slide deck, log file ...

---

## Validation and Benchmarks
- To validate `FastMDAnalysis` go to https://github.com/aai-research-lab/FastMDAnalysis/blob/validation/README.md
- For benchmarks go to https://github.com/aai-research-lab/FastMDAnalysis/blob/benchmark/README.md

---


## Documentation
The documentation [under development] (with an extensive User Guide) is available [here](https://fastmdanalysis.readthedocs.io).

---

## Contributing
Contributions are welcome. Please submit a Pull Request. 

**Development Installation**

If you want to contribute or modify the code:
```bash
# Clone the repository
git clone https://github.com/aai-research-lab/FastMDAnalysis.git
cd FastMDAnalysis

# Create and activate virtual environment
python -m venv fastmda_env
source fastmda_env/bin/activate  # On Windows: fastmda_env\Scripts\activate

# Install in development mode with test dependencies
pip install -e ".[test]"

# Verify installation
fastmda -h
fastmda analyze -h
```
---

## Citation
If you use `FastMDAnalysis` in your work, please cite:

Aina, A. and Kwan, D. (2025) “FastMDAnalysis: Software for Automated Analysis of Molecular Dynamics Trajectories”. ChemRxiv. https://doi.org/10.26434/chemrxiv-2025-x8xnq

```bibtex
@article{fastmdanalysis,
  author       = {Adekunle Aina and Derrick Kwan},
  title        = {FastMDAnalysis: Software for Automated Analysis of Molecular Dynamics Trajectories},
  year         = {2025},
  doi          = {10.26434/chemrxiv-2025-x8xnq},
  publisher    = {ChemRxiv},
  url          = {https://doi.org/10.26434/chemrxiv-2025-x8xnq},
  type         = {preprint}
}
```

---

## License

`FastMDAnalysis` is licensed under the MIT license. 

---

## Acknowledgements

``FastMDAnalysis`` builds upon excellent open-source libraries to provide its high-performance analysis capabilities and to improve workflow efficiency, usability, and reproducibility in molecular dynamics trajectory analysis. We gratefully acknowledge:

- ``MDTraj`` for foundational trajectory I/O and analysis modules
- ``NumPy/SciPy`` for efficient numerical computations
- ``scikit-learn`` for advanced machine learning algorithms
- ``Matplotlib`` for publication-quality visualization

While leveraging these robust tools, ``FastMDAnalysis`` streamlines analysis for students, professionals, and researchers, especially those new to molecular dynamics. We thank the scientific Python community for their contributions to the ecosystem.


