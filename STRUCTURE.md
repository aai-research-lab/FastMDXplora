# Repository structure

```
FastMDXplora/
├── src/
│   └── fastmdxplora/
│       ├── __init__.py            # Top-level exports, metadata, supported Python range
│       ├── _version.py            # Written by setuptools-scm (not committed)
│       ├── orchestrator.py        # FastMDXplora project-level orchestrator
│       ├── dependencies.py        # Optional-backend detection (OpenMM, PDBFixer, …)
│       ├── statistics.py          # Statistical inefficiency: how many independent samples a mean rests on
│       ├── provenance.py          # Which code a run was made from
│       ├── user_dir.py            # Where per-user settings live, outside any study
│       ├── explain.py             # The prose the CLI prints beside each step
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py            # `fastmdx` entry point (explore/xplore/setup/simulate/
│       │                          #   analyze/report/gui/info/init-config)
│       ├── setup/
│       │   ├── pipeline.py        # Phase driver: fix, protonate, solvate, ionize
│       │   ├── prepare.py         # Modeller assembly, ligand merge, clash checks
│       │   ├── pdbfix.py          # PDBFixer wrapper
│       │   ├── forcefields.py     # Named force-field selector
│       │   ├── ligand.py          # OpenFF small-molecule parameterization
│       │   ├── heterogens.py      # What to keep from the entry, and why
│       │   ├── ccd.py             # Chemical Component Dictionary lookups
│       │   ├── protonation.py     # Protonation states at the run's pH
│       │   ├── membrane.py        # Bilayer construction
│       │   └── membrane_fit.py    # Orienting the protein in the bilayer, checked not assumed
│       ├── simulation/
│       │   ├── pipeline.py        # Phase driver
│       │   ├── runner.py          # minimize → NVT → NPT → production, reporters, platforms
│       │   ├── plumed.py          # PLUMED enhanced sampling on the production stage
│       │   ├── metadynamics.py    # Collective variables and PLUMED input, shared by all three methods
│       │   ├── metad_surface.py   # Free energy surface from the hills, and whether it settled
│       │   ├── umbrella.py        # Window planning, and the PMF stitched from them
│       │   ├── steered.py         # A pull along a coordinate, and the work done
│       │   ├── restraints.py      # Positional restraints and the release ladder
│       │   └── diagnose.py        # What a failed simulation can be told from its state
│       ├── analysis/
│       │   ├── orchestrator.py    # Analysis-phase orchestrator + auto-detection
│       │   ├── analyze.py         # Top-level analyze() entry point
│       │   ├── base.py            # Analysis base class and shared I/O
│       │   ├── loading.py         # Trajectory/topology loading, scope and selection
│       │   ├── plotting.py        # Shared figure style
│       │   ├── rmsd.py rmsf.py rg.py qvalue.py sasa.py ss.py
│       │   ├── hbonds.py dihedrals.py cluster.py dimred.py water_sites.py
│       │   ├── contacts.py ligand_rmsd.py ligand_rmsf.py pl_hbonds.py   # protein-ligand
│       │   ├── pl_interactions.py interactions.py ligand_chemistry.py   # what holds the ligand
│       │   ├── pmf.py metad_surface.py steered_work.py   # the result of a biased run
│       │   ├── reweight.py         # Weights that undo a known bias
│       │   ├── reweighted_averages.py  # Those weights applied to the analyses
│       │   └── describe.py        # What each analysis is, for the GUI and the docs
│       ├── report/
│       │   ├── run.py             # Top-level report() entry point
│       │   ├── document.py        # Structured Markdown report
│       │   ├── slides.py          # .pptx slide deck (with markdown fallback)
│       │   ├── summary_figure.py  # Single-figure run summary
│       │   ├── region_highlights.py  # Per-region annotations for the report
│       │   ├── context.py         # Shared report context
│       │   ├── methods.py         # The methods section, including what a biased run is not
│       │   ├── convergence.py     # Whether the run had settled by the time it was measured
│       │   ├── reweighted.py      # Equilibrium averages recovered from a biased run
│       │   ├── pdf.py             # Markdown → PDF
│       │   └── bundle.py          # Self-contained .zip project archive
│       ├── gui/                   # All user-interface code: server, views, assets
│       │   ├── exploration.py     # Study builder, config export, run control
│       │   ├── server.py          # Dependency-free ThreadingHTTPServer (127.0.0.1 only)
│       │   ├── telemetry.py       # Phase/progress telemetry feed
│       │   ├── trajectory_playback.py, live_frames.py   # Frame streaming
│       │   ├── protein_preview.py, structure_info.py, ligand_detection.py
│       │   ├── report_dashboard.py  # Static dashboard written into a report
│       │   ├── static/            # theme.css (shared tokens), dashboard.css/js,
│       │   │                      #   molecule-viewer.js, charts.js, vendored 3Dmol.js
│       │   └── templates/         # dashboard.html
│       ├── batch/
│       │   ├── explorer.py        # Multi-run driver (sequential/parallel)
│       │   ├── sweep.py           # Parameter cross-product expansion
│       │   └── compare.py         # Cross-run comparison report
│       ├── config/
│       │   ├── schema.py          # Config schema (single source of truth for options)
│       │   ├── loader.py          # YAML load, merge, strict validation
│       │   └── generate.py        # `fastmdx init-config` templates
│       └── utils/
│           ├── logging.py         # Structured logging
│           ├── presenter.py       # Terminal presentation layer (banner, phase output)
│           └── native_output.py   # Terminal capability detection
├── tests/                         # 32 modules, ~705 test functions
├── docs/                          # Sphinx + MyST sources (Read the Docs)
├── scripts/
│   ├── run_pdb_smoke_campaign.py  # Multi-PDB smoke campaign
│   └── make_benzene.py
├── shim-package/                  # `fastmdx` alias on PyPI
│   ├── pyproject.toml
│   ├── README.md
│   └── src/fastmdx/__init__.py
├── recipes/                       # conda-forge submission recipes
│   ├── fastmdxplora/meta.yaml
│   └── fastmdx-alias/meta.yaml
├── .github/workflows/
│   ├── tests.yml                  # CI: OS × Python matrix, CLI smoke test, coverage
│   └── publish.yml                # PyPI trusted publishing on `v*` tag
├── examples/                      # Example inputs (e.g. pdb_list.txt)
├── assets/
├── fastmdx                        # Launcher for an uninstalled checkout
├── environment.yml                # conda environment for the full install
├── pyproject.toml                 # Primary package config
├── pytest.ini
├── requirements.txt
├── README.md
├── LICENSE
├── CITATION.cff
├── CHANGELOG.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── STRUCTURE.md                   # (this file)
└── .gitignore
```

## Architectural overview

FastMDXplora is a **project-level orchestrator**. The central class
`FastMDXplora` holds shared state (system input, output directory,
per-phase options) and coordinates the four canonical phases:

```
  setup → simulation → analysis → report
```

This continues the orchestrator pattern of **FastMDXplora version 1** (Aina & Kwan,
JCC 2026), which orchestrates analysis modules within a trajectory.
FastMDXplora applies the same pattern one level up the hierarchy.

Three subsystems sit alongside the phases rather than inside them:

- **`batch/`** runs many studies (multiple systems, parameter sweeps) and
  compares them. Each run is structurally identical to a single study.
- **`gui/`** owns every user interface: the localhost server and browser
  application, and the static dashboard written into a report.
  It never reimplements phase science; it observes and launches.
- **`dependencies.py`** detects the optional chemistry backends. Missing
  backends are reported at the point of use with the exact install command,
  rather than failing mid-phase.

### Key design principles

1. **Self-contained.** FastMDXplora has no runtime dependency on
   external MD-analysis or simulation packages. Each phase is implemented
   directly under `fastmdxplora.<phase>`.

2. **Intent over DAG.** Users express intent (`include=["setup", "analysis"]`,
   `exclude=["report"]`, per-phase option overrides). The workflow is
   built-in, so this is not a general-purpose workflow engine.

3. **Structured I/O at every phase.** Every phase writes a JSON parameters
   manifest plus its canonical artifacts. The orchestrator writes a
   top-level `manifest.json` and a `resolved_config.yml` recording the
   session exactly as it ran.

4. **Lazy phase imports.** Each phase is imported only when invoked, so
   optional heavy dependencies (OpenMM, PDBFixer) do not impose a cost on
   users who only use a subset of phases.

5. **Continue version 1 conventions.** The analysis subpackage uses the
   same module taxonomy (`rmsd`, `rmsf`, `rg`, `hbonds`, `ss`, `cluster`,
   `sasa`, `dimred`, `qvalue`, `dihedrals`) established in FastMDXplora
   version 1, now extended with protein-ligand analyses.

6. **One source of truth per fact.** The config schema defines the option
   surface for both the CLI and the Python API; `MIN_PYTHON` / `MAX_PYTHON`
   in `__init__.py` define the supported Python range for `pyproject.toml`,
   and `environment.yml`.

### Naming alignment

| Surface | Name |
|---|---|
| Project / brand | FastMDXplora |
| PyPI primary | `fastmdxplora` |
| PyPI alias | `fastmdx` (depends on `fastmdxplora`) |
| Python import | `fastmdxplora` (commonly aliased: `import fastmdxplora as fastmdx`) |
| CLI command | `fastmdx` |
| GitHub repo | `aai-research-lab/FastMDXplora` |
| DOI | 10.1002/jcc.70350 (foundational JCC paper) |
