# Repository structure

```
FastMDXplora/
├── src/
│   └── fastmdxplora/
│       ├── __init__.py            # Top-level exports, metadata, supported Python range
│       ├── _version.py            # Written by setuptools-scm (not committed)
│       ├── orchestrator.py        # FastMDXplora project-level orchestrator
│       ├── runs_here.py           # When a study may start beside the others in a workspace: its starting lock and runs
│       ├── gpu_here.py            # This computer's GPUs shared by the studies started on it, and what each run held
│       ├── stop_after.py          # Sees a stop through, in a process that outlives the asker
│       ├── dependencies.py        # Optional-backend detection (OpenMM, PDBFixer, …)
│       ├── statistics.py          # Statistical inefficiency: how many independent samples a mean rests on
│       ├── lipids.py              # Which residues are lipids, and how many make a bilayer
│       ├── protein_names.py       # `protein` covers every amino acid a force field writes, from MDTraj's first import
│       ├── provenance.py          # Which code a run was made from
│       ├── user_dir.py            # Where per-user settings live, outside any study
│       ├── own_programs.py        # Puts this environment's programs (AmberTools) on PATH
│       ├── explain.py             # The prose the CLI prints beside each step
│       ├── scenes.py              # A view of a study as a MolViewSpec scene (.mvsx)
│       ├── study_tags.py          # Tags and a note a person gives a study, kept in its folder
│       ├── structure_search.py    # A structure found by its name: the PDB's entries by protein, AlphaFold DB's models
│       ├── workspace_studies.py   # The studies in a workspace listed and compared, for the Agent and an AI app
│       ├── software_docs.py       # The software's own docs, packaged as `_docs/`: their pages, sections and a search
│       ├── system_id.py           # A study's system as four capitals: its PDB ID, or its file's first four
│       ├── replaced.py            # What a phase run again replaces, and what it leaves stale
│       ├── study_files.py         # What each file of a study is: its phase, run, kind and name
│       ├── deposit.py             # A study's files as a data repository takes them, each with its SHA-256
│       ├── again.py               # What can be run again on a study, and the command that does it
│       ├── movies.py              # Movies of a study's frames, encoded by this computer's ffmpeg
│       ├── movie_maker.py         # `fastmdx movie`: a movie made by the Viewer in a browser with no window
│       ├── demo/                  # The demo study (3PTB, trypsin with benzamidine): 3ptb.yml, copy_demo
│       ├── advisories.py          # What is worth knowing before a run starts, not after
│       ├── cost.py                # How long a study will take, on this machine
│       ├── naming.py              # One rule for the name of a study's output folder
│       ├── refusals.py            # What a refusal is, in a word a program can read
│       ├── remedies.py            # What would fix a refusal, and what the fix costs here
│       ├── marking.py             # Marking what nothing checked, where the mark has to survive
│       ├── uncertainty.py         # An error bar on a quantity that is not a mean
│       ├── __main__.py            # `python -m fastmdxplora`: the `fastmdx` command
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py            # `fastmdx` entry point (explore/xplore/setup/simulate/
│       │                          #   analyze/report/gui/info/config/remote/mcp/scene)
│       ├── sharing/               # A study shared as one file, and opened from it (docs/sharing.md)
│       │   ├── __init__.py        # The profile, its version and the placeholders a packed record says
│       │   ├── crate.py           # The packing list: RO-Crate 1.2 with the study profile, and its check
│       │   ├── pack.py            # `fastmdx report --share`: the files chosen, paths replaced, the zip
│       │   ├── zenodo.py          # `--share-to zenodo`: a draft with its DOI reserved; a record read back
│       │   └── opening.py         # `fastmdx gui --open`: fetched, checked file by file, opened
│       ├── mcp/
│       │   ├── protocol.py        # The Model Context Protocol over stdio, both eras
│       │   ├── app.py             # What `fastmdx mcp` offers an AI app
│       │   ├── content.py         # Its guides, study records and prompts
│       │   ├── tools.py           # Its tools: look, check, save, run, read; the Agent
│       │   ├── remote_tools.py    # Its tools for the person's other machines: send, watch, fetch, stop
│       │   └── workspace.py       # The one folder the tools may use
│       ├── agent/                 # The Agent: an AI model writing and answering about studies
│       │   ├── propose.py         # Propose a study, have it refused, repair it, try again
│       │   ├── conversation.py    # The same loop when the AI model replies by tool calls
│       │   ├── turns.py           # One turn with tools, in each provider's shape
│       │   ├── receipt.py         # What the AI model was sent for one reply, kept bounded
│       │   ├── tools.py           # What the Agent looks at with the software's own tools
│       │   ├── models.py          # A stored provider choice, as the function the Agent calls
│       │   ├── memory.py          # What the Agent remembers of the person, as they see and change it
│       │   ├── evaluate.py        # How well an AI model writes a study, counted on a set of asks
│       │   ├── queue.py           # A waiting line for one card, and a budget it keeps to
│       │   ├── worker.py          # One process taking work off the line, one job at a time
│       │   ├── staged.py          # Running a study the Agent wrote, with the estimate first
│       │   └── run.py             # Running a queued job as a real study
│       ├── setup/
│       │   ├── pipeline.py        # Phase driver: fix, protonate, solvate, ionize
│       │   ├── audit.py           # Optional bounded observations of preparation
│       │   ├── prepare.py         # Modeller assembly, ligand merge, clash checks
│       │   ├── pdbfix.py          # PDBFixer wrapper
│       │   ├── forcefields.py     # Named force-field selector
│       │   ├── ligand.py          # OpenFF small-molecule parameterization
│       │   ├── heterogens.py      # What to keep from the entry, and why
│       │   ├── ccd.py             # Chemical Component Dictionary lookups
│       │   ├── protonation.py     # Protonation states at the run's pH
│       │   ├── membrane.py        # Placing the protein for its bilayer, and the checks the build needs
│       │   ├── membrane_fit.py    # The membrane normal, centre and thickness, fitted to the protein
│       │   ├── assembly.py        # Which biological assembly a deposited structure is simulated as
│       │   ├── ensemble.py        # One model of a structure file that holds several
│       │   ├── mmcif.py           # A deposited mmCIF file, as the PDB records setup reads
│       │   └── estimate.py        # What setup will build from a structure, before it runs
│       ├── simulation/
│       │   ├── pipeline.py        # Phase driver
│       │   ├── runner.py          # minimize → NVT → NPT → production, reporters, platforms
│       │   ├── plumed.py          # PLUMED enhanced sampling on the production stage
│       │   ├── metadynamics.py    # Collective variables and PLUMED input, shared by all three methods
│       │   ├── metad_surface.py   # Free energy surface from the hills, and whether it settled
│       │   ├── umbrella.py        # Window planning, and the PMF stitched from them
│       │   ├── steered.py         # A pull along a coordinate, and the work done
│       │   ├── restraints.py      # Positional restraints and the release ladder
│       │   ├── diagnose.py        # What a failed simulation can be told from its state
│       │   ├── stopping.py        # Run until what was asked is determined, and no longer
│       │   ├── sampling_ask.py    # How much longer a study has to run for the means it withheld
│       │   ├── lengths.py         # How long each stage runs when a config does not say
│       │   ├── ensembles.py       # Which ensemble production runs in
│       │   ├── resume.py          # Whether a run may be stopped and picked up again
│       │   ├── seeding.py         # Umbrella windows' starting structures, from a steered pull
│       │   ├── binding.py         # A binding free energy from a potential of mean force
│       │   └── reference_state.py # Whether that free energy's reference state holds here
│       ├── analysis/
│       │   ├── orchestrator.py    # Analysis-phase orchestrator + auto-detection
│       │   ├── analyze.py         # Top-level analyze() entry point
│       │   ├── base.py            # Analysis base class and shared I/O
│       │   ├── loading.py         # Trajectory/topology loading, scope and selection
│       │   ├── imaging.py         # Molecules made whole, MDTraj's imaging for every frame at once
│       │   ├── water_names.py     # The one set of water residue names every module uses
│       │   ├── starting_frame.py  # Which frames clustering and the projections read, and where the RMSD equilibrates
│       │   ├── plotting.py        # Shared figure style
│       │   ├── rmsd.py rmsf.py rg.py qvalue.py sasa.py ss.py
│       │   ├── hbonds.py dihedrals.py cluster.py dimred.py water_sites.py
│       │   ├── pair_distance.py end_to_end.py moments_of_inertia.py rdf.py
│       │   ├── coordination_number.py order_parameters.py bfactor_comparison.py
│       │   ├── thermodynamics.py  # Density, energy and temperature, from the run's own record
│       │   ├── interaction_summary.py  # How often an interaction was there, and over how much
│       │   ├── joining.py         # A study's pieces put back together, refused where they do not fit
│       │   ├── residues.py        # How a residue is named in what the analyses write
│       │   ├── contacts.py ligand_rmsd.py ligand_rmsf.py pl_hbonds.py   # protein-ligand
│       │   ├── pl_interactions.py interactions.py ligand_chemistry.py   # what holds the ligand
│       │   ├── pmf.py metad_surface.py steered_work.py   # the result of a biased run
│       │   ├── bilayer.py         # Where a bilayer is: its lipids, centre and leaflets
│       │   ├── area_per_lipid.py bilayer_thickness.py lipid_order.py   # the bilayer itself
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
│       │   ├── convergence.py     # Whether the run had equilibrated before the frames analysed
│       │   ├── reweighted.py      # Equilibrium averages recovered from a biased run
│       │   ├── pdf.py             # Markdown → PDF
│       │   ├── markdown_html.py   # Markdown as HTML that carries no markup of its own
│       │   └── bundle.py          # Self-contained .zip project archive
│       ├── gui/                   # All user-interface code: server, views, assets
│       │   ├── exploration.py     # Study builder, config export, run control
│       │   ├── remote_routes.py   # The routes to your machines: plan, send, watch, fetch, stop
│       │   ├── server.py          # Dependency-free ThreadingHTTPServer, on loopback unless bound elsewhere
│       │   ├── telemetry.py       # Phase/progress telemetry feed
│       │   ├── trajectory_frames.py, live_frames.py   # Frame streaming
│       │   ├── protein_preview.py, structure_info.py, ligand_detection.py
│       │   ├── viewed_structure.py  # The structures the Viewer is sent, as it is sent them
│       │   ├── by_residue.py      # Per-residue results to colour by, and DSSP for every frame
│       │   ├── selection.py       # A clicked atom's selection, checked against the analyses' topology
│       │   ├── viewer_selections.py  # Selections typed in MDTraj's language, and named ones
│       │   ├── saved_views.py     # Views of the Viewer saved with the study
│       │   ├── interactions_over_frames.py  # What holds the ligand, frame by frame
│       │   ├── chain_contacts.py  # What holds the chains together, frame by frame
│       │   ├── runs_compared.py   # The runs of a study side by side, resolved differences marked
│       │   ├── analysis_overview.py  # What every analysis determined, read together, and how each series converged
│       │   ├── applicable.py      # Which analyses a study's config and structure leave nothing for, and why
│       │   ├── runs_together.py   # The runs of a study played together in one Viewer
│       │   ├── occupancy.py       # Where the ligand and water went over the frames; water sites placed
│       │   ├── motion.py          # A study's main motions, swung and shown as lines on the first frame
│       │   ├── states.py          # The states the cluster analysis found, and two compared
│       │   ├── backbone_angles.py # Each residue's φ and ψ in each frame played
│       │   ├── backbone_picture.py # A study's backbone for its card, before any figure
│       │   ├── sidebar_icons.py   # The sidebar's line icons, for the GUI and the standalone dashboard
│       │   ├── simulated_time.py  # How long a study ran: its production first, equilibration beside, wall time
│       │   ├── overview_view.py   # What the Overview leads with: the means, the production's clock, the phases
│       │   ├── contact_map.py     # Which residues touch which over the frames played
│       │   ├── pocket_volume.py   # The room in a ligand's pocket, frame by frame (POVME's way)
│       │   ├── beside.py          # Another study beside this one: paired by sequence, fitted, timed
│       │   ├── measure.py         # A distance from the Viewer, over every frame
│       │   ├── series.py          # An analysis's numbers, tied to the trajectory's frames
│       │   ├── figure_data.py     # The numbers behind an analysis's other figures, to plot in the page's colours
│       │   ├── stopping_view.py   # A study run until it is determined, for the Overview
│       │   ├── fixes_view.py      # What would fix the study on screen
│       │   ├── figure_provenance.py  # What made each figure, and the command that makes it again
│       │   ├── report_page.py     # The report as a document, in the page
│       │   ├── browse.py          # Walking the filesystem from the page; what is a study
│       │   ├── again_view.py      # What the GUI offers to run again on the study on screen
│       │   ├── directory_inspect.py  # What is in a folder, and what can be done with it
│       │   ├── config_builder.py  # What the page holds as a config file, checked
│       │   ├── run_from_config.py # Run what the page describes, from the file it would give
│       │   ├── schema_payload.py  # The settings, described for the browser to lay out
│       │   ├── starters.py        # Studies to start from, in the builder
│       │   ├── preview.py         # What the builder's study will build and cost, before it runs
│       │   ├── agent_panel.py     # The browser's two calls into the Agent
│       │   ├── records_answer.py  # The Agent's questions about a study, answered from its records
│       │   ├── citations.py       # What an Agent's answer drew on, as the study recorded it
│       │   ├── stream.py          # Telling an open page when the study it shows has changed
│       │   ├── hosting.py         # The GUI served to someone else, behind a signing-in proxy
│       │   ├── route_imports.py   # The package's modules the GUI's routes can reach
│       │   ├── files_page.py      # The Files page: the study's files in the order it ran, for the GUI and the report
│       │   ├── report_dashboard.py  # Static dashboard written into a report
│       │   ├── static/            # theme.css (shared tokens), dashboard.css, dashboard.js,
│       │   │                      #   frame.js (columns, side panel, theme), theme-first.js
│       │   │                      #   (the scheme before the first paint), fonts/ (Inter and
│       │   │                      #   JetBrains Mono, SIL OFL 1.1), studies.js,
│       │   │                      #   preferences.js (the dialogs, preferences kept),
│       │   │                      #   run-builder.js, file-picker.js, report-page.js, analyze-again.js,
│       │   │                      #   series-chart.js, figure-chart.js, analysis-page.js, files-page.js, files-actions.js, runs-compared.js, stopping.js,
│       │   │                      #   fixes.js, agent-panel.js, agent-beside.js (the Agent
│       │   │                      #   beside any page), icons.js (the line icons for the
│       │   │                      #   scripts), tooltips.js (hints in the page's
│       │   │                      #   own style), charts.js, overview.js (the Overview's
│       │   │                      #   results, run and clock),
│       │   │                      #   molecule-viewer.js, and the viewer:
│       │   │                      #   viewer-engine.js and molstar/ (Mol* 5.12.0),
│       │   │                      #   viewer-sequence.js, viewer-selections.js,
│       │   │                      #   viewer-views.js, viewer-movie.js, frame-series.js,
│       │   │                      #   frame-interactions.js, chain-contacts.js,
│       │   │                      #   viewer-occupancy.js, viewer-motion.js,
│       │   │                      #   viewer-states.js, viewer-ramachandran.js,
│       │   │                      #   viewer-contact-map.js, viewer-pocket.js,
│       │   │                      #   viewer-beside.js, viewer-rail.js (one tool at a time);
│       │   │                      #   palette.js (go to anything, Cmd+K), stop-run.js (Stop the
│       │   │                      #   run, asked first, wherever it is offered), run-notice.js
│       │   │                      #   (progress in the tab's title, a notice at the end);
│       │   │                      #   remote.js and remote.css (Run on one of your machines,
│       │   │                      #   and Remote jobs on All studies);
│       │   │                      #   chats.js (the study's conversations and Chats in the sidebar);
│       │   │                      #   scene-view.js
│       │   │                      #   (a scene on a page of its own)
│       │   └── templates/         # dashboard.html, scene.html
│       ├── remote/
│       │   ├── probe.py           # The read-only inspection script, and reading its answer
│       │   ├── identity.py        # Which code an installation holds: a release's version, a checkout's commit
│       │   ├── transport.py       # The user's own ssh, with one login per session
│       │   ├── survey.py          # Inspecting a machine and asking its installation what loads
│       │   ├── machines.py        # Per-user machine records, and readiness for this version
│       │   ├── plan.py            # How FastMDXplora would be installed there, from conda-forge
│       │   ├── installer.py       # Running a plan, once a person has said yes
│       │   ├── inputs.py          # The files a Config names, gathered to travel with it
│       │   ├── send.py            # Send, status, fetch and cancel
│       │   ├── gpu_room.py        # A workstation's GPU memory free, and what a study needs, learned from runs there
│       │   ├── api.py             # The same from a program or an AI app: records only, no prompts
│       │   ├── jobs.py            # Per-user records of the studies sent
│       │   └── describe.py        # What `fastmdx remote` prints
│       ├── batch/
│       │   ├── explorer.py        # Multi-run driver (sequential/parallel)
│       │   ├── sweep.py           # Parameter cross-product expansion
│       │   ├── compare.py         # Cross-run comparison report
│       │   └── aggregate.py       # One table from a campaign's members, and what their spread means
│       ├── config/
│       │   ├── schema.py          # Config schema (single source of truth for options)
│       │   ├── loader.py          # YAML load, merge, strict validation
│       │   ├── generate.py        # `fastmdx config` templates
│       │   ├── describe.py        # The config language, described for an AI model
│       │   ├── languages.py       # One study as a config file, a command and a script
│       │   ├── diff.py            # What differs between two studies' settings
│       │   ├── recorded.py        # A study's recorded settings, as the base for its phases run again
│       │   ├── defaults_file.py   # Your defaults: fastmdx-defaults.yml filling what a new study leaves unset
│       │   ├── phase_settings.py, phase_settings_types.py  # Each phase's settings, from the schema
│       │   └── agent_modes.py     # Which phases a model wrote, and which were checked
│       ├── validation/            # Checks of the software against independent references
│       │   ├── corpus.py          # Guardrails tried on a corpus of structures
│       │   ├── cross_tool.py      # A run compared with independent reference implementations
│       │   ├── environments.py    # One study run in several places, compared field by field
│       │   ├── replica_calibration.py   # Whether one run's stated error agrees with replicas
│       │   ├── stopping_calibration.py  # Whether a study stopped by `stop_when` states an honest error
│       │   ├── agent_looks.py, agent_looks_v2.py  # Whether the Agent's looking helps, on a real AI model
│       │   └── agent_eval.py      # The Agent's evaluation: a registered set of what a person asks, judged in code
│       └── utils/
│           ├── logging.py         # Structured logging
│           ├── presenter.py       # Terminal presentation layer (banner, phase output)
│           └── native_output.py   # Terminal capability detection
├── tests/                         # About 410 modules and 6,600 test functions;
│                                  #   validation/ checks the docs and methods hold
├── docs/                          # Sphinx + MyST sources (Read the Docs)
├── scripts/                       # Development, benchmarking and release helpers (README.md)
│   ├── run_pdb_smoke_campaign.py  # Multi-PDB smoke campaign
│   ├── gpu_shakedown.py           # What only a real GPU can answer: cost, segments, resuming
│   ├── make_mark.py               # FastMDXplora's mark: the tab's icon and ICONS["mark"]
│   ├── make_demo.py               # A finished 3PTB study packaged as the demo study
│   ├── cla_co_authors.py          # A pull request's co-authors sign the contributor agreement
│   └── make_benzene.py, compare_*.py, name_refusal*.py, measure_nli.py
├── container/                     # Apptainer definition, and Docker made from it
├── preregistration/               # Validation plans written before their results
├── shim-package/                  # `fastmdx` alias on PyPI
│   ├── pyproject.toml
│   ├── README.md
│   └── src/fastmdx/__init__.py
├── recipes/                       # conda-forge submission recipes
│   ├── fastmdxplora/meta.yaml
│   └── fastmdx-alias/meta.yaml
├── .github/workflows/
│   ├── tests.yml                  # CI: OS × Python matrix, CLI smoke test, coverage
│   ├── publish.yml                # PyPI trusted publishing on `v*` tag
│   ├── container.yml              # The Apptainer image (.sif), for a release or a trial
│   └── cla.yml                    # The contributor agreement, signed by authors and co-authors
├── examples/                      # Example inputs (e.g. pdb_list.txt)
├── assets/
├── fastmdx                        # Launcher for an uninstalled checkout
├── environment.yml                # conda environment for the full install
├── pyproject.toml                 # Primary package config
├── setup.py                       # The build step that copies docs/*.md into the package
├── pytest.ini
├── requirements.txt
├── README.md
├── LICENSE
├── CITATION.cff
├── CHANGELOG.md
├── CONTRIBUTING.md
├── CLA.md                         # The contributor licence agreement
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
