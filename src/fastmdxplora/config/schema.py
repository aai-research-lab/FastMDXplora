"""Configuration schema registry.

This module is the single source of truth for FastMDXplora's
configuration surface. Every option a user can set — top-level
(``system``, ``output``, ``include``/``exclude``, ``verbose``) and
per-phase (``setup``, ``simulation``, ``analysis``, ``report``) — is
declared here once, with its type, default, and a human-readable
description.

Four features read from this single registry, so they never drift apart:

  1. **Validation** (:mod:`fastmdxplora.config.loader`) — unknown keys
     are rejected with did-you-mean suggestions; values are type-checked.
  2. **Template generation** (``fastmdx init-config``) — a fully-commented
     YAML template is generated directly from the field descriptions and
     defaults.
  3. **Resolved-config dump** — after a run, the merged configuration is
     written to ``resolved_config.yml`` for reproducibility.
  4. **Documentation** — the field help strings are the canonical
     descriptions used in the template and (eventually) the docs.

The schema deliberately mirrors the keyword arguments accepted by each
phase's ``run()`` function and by :class:`fastmdxplora.FastMDXplora`,
so a config file and the equivalent flags/kwargs produce identical runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Field descriptor
# ---------------------------------------------------------------------------
#: Distinguishes "no separate phase value" from a phase value of ``None``,
#: which is itself meaningful.
_UNSET = object()


#: Every analysis the registry knows, named here so the schema can offer them
#: as choices. Kept as a literal rather than imported from the registry: the
#: analyses import this module, so reading it back would be a cycle. The test
#: below it holds the two in step.
ANALYSIS_NAMES = (
    "rmsd", "rmsf", "rg", "hbonds", "ss", "sasa", "dihedrals", "qvalue",
    "cluster", "dimred", "water_sites", "ligand_rmsd", "ligand_rmsf",
    "pl_contacts", "pl_hbonds", "pl_interactions",
    # Measured against experiment rather than against the trajectory alone:
    # the first is what NMR relaxation reports, the second what a crystal's
    # B-factors imply. Each runs only where the system supplies what it
    # needs -- amide hydrogens, and a deposited file that has B-factors.
    "order_parameters", "bfactor_comparison",
    # Reads the state record the simulation wrote, where there is one.
    "thermodynamics",
    # Needs a periodic box to normalise against, so it runs on solvated
    # systems and leaves itself out of anything built in vacuum.
    "rdf",
    # Reads the umbrella study's result rather than the trajectory, and runs
    # only where such a study produced one.
    "pmf",
    # Reads the metadynamics run's own surface, where one was produced.
    "metad_surface",
    # Reads the pull's own record, where a steered run produced one.
    "steered_work",
)


@dataclass(frozen=True)
class Field:
    """One configurable option.

    Parameters
    ----------
    name : str
        The key as it appears in the YAML file and as the kwarg name.
    type : type | tuple[type, ...]
        Accepted Python type(s) after YAML parsing. Used for validation.
        ``list`` means "a YAML list"; element types are not deeply checked
        (MD option lists are heterogeneous enough that element-level
        checking causes more false positives than it's worth).
    default : Any
        The value a user gets when the option is absent. This is what the
        documentation, the config template, and ``--help`` all report, and
        what the phase initialises with unless ``phase_sentinel`` says
        otherwise. It is declared here and nowhere else.
    help : str
        One-line human-readable description (used in the template).
    example : Any, optional
        A representative value shown in the generated template when the
        default is ``None`` (so the template is illustrative, not blank).
    choices : tuple of str, optional
        The complete set of accepted values, where there is one. Declared
        here so that argparse, the GUI's controls, the config template,
        and validation all offer the same list. They used to hold four
        separate copies of it.
    phase_sentinel : Any, optional
        What the phase table holds, where that must differ from the value
        the user is told about. ``pressure_bar`` is the case this exists
        for: its effective default is 1 bar, but the phase must start from
        ``None`` so that ``pressure_atm`` can be recognised as the setting
        the user actually gave. Writing 1.0 into the phase table would make
        bar always present, and bar wins, so an explicit ``pressure_atm``
        would be silently ignored.

        Both meanings then live in one declaration, next to the reason.
    """

    name: str
    type: type | tuple[type, ...]
    default: Any
    help: str
    example: Any = None
    choices: tuple[str, ...] | None = None
    phase_sentinel: Any = _UNSET

    @property
    def phase_value(self) -> Any:
        """What the phase should initialise this option with."""
        return self.default if self.phase_sentinel is _UNSET else self.phase_sentinel


@dataclass(frozen=True)
class PhaseSchema:
    """The schema for one phase (or the top-level block)."""

    name: str
    description: str
    fields: tuple[Field, ...]

    def field_names(self) -> set[str]:
        return {f.name for f in self.fields}

    def defaults(self) -> dict[str, Any]:
        """The table a phase initialises from.

        A phase used to keep its own copy of this, which is how the pH default
        came to be 7.4 in one place and 7.0 in another. There is one
        declaration now, and this reads it.
        """
        return {f.name: f.phase_value for f in self.fields}

    def get(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


# ---------------------------------------------------------------------------
# Top-level keys
# ---------------------------------------------------------------------------
TOP_LEVEL = PhaseSchema(
    name="(top-level)",
    description="Project-level settings.",
    fields=(
        Field("output", str, None,
              "Output directory for all artifacts. "
              "Default: ./fastmdxplora_output_<UTC-timestamp>.",
              example="./my_study"),
        Field("explain", bool, True,
              "Say why each step happens as it happens, with a reference "
              "where there is one worth reading. On, because a pipeline that "
              "runs silently teaches nothing and somebody's first trajectory "
              "should be one they can defend. Turn it off once the steps are "
              "familiar."),
        Field("verbose", bool, False,
              "Stream debug logging to the terminal in addition to the log file."),
        Field("include", list, None,
              "Subset of phases to run, in order. "
              "Mutually exclusive with `exclude`.",
              example=["setup", "simulation", "analysis", "report"]),
        Field("exclude", list, None,
              "Phases to skip. Mutually exclusive with `include`.",
              example=["report"]),
    ),
)


# ---------------------------------------------------------------------------
# Setup phase
# ---------------------------------------------------------------------------
SETUP = PhaseSchema(
    name="setup",
    description="System preparation: fix structure, solvate, ionize, parameterize.",
    fields=(
        Field("ph", float, 7.4,
              "pH for hydrogen placement, which sets protonation states. The "
              "default is physiological: blood is 7.4, and a protein studied "
              "without a stated reason otherwise is studied there. Cytosol "
              "sits near 7.2, a lysosome near 4.7, so a compartment-specific "
              "study should say so.",
              example=7.0),
        Field("heterogens", str, "auto",
              "How to treat non-standard residues: 'auto' (the default) "
              "decides per component, prepares any ligand it can, and stops "
              "where the structure does not determine what to simulate; "
              "'drop' removes them all, reporting what went; 'keep' retains "
              "them all. 'drop' was the default before 2.0. It never stops, "
              "but a discarded ligand changes what the run answers without "
              "saying so.",
              choices=("auto", "drop", "keep"),
              example="drop"),
        Field("mutations", list, None,
              "Point substitutions to make before anything else, written "
              "either as L99A or as LEU-99-ALA. The original residue is "
              "checked against the structure and setup stops where it does "
              "not match, because a mutation written against one "
              "construct's numbering would otherwise silently replace "
              "whatever sits at that position. The replacement side chain "
              "is placed geometrically rather than modelled, so a mutant "
              "needs its equilibration watched more closely than a "
              "deposited structure does.",
              example=["L99A"]),
        Field("mutation_chain", str, None,
              "Which chain the mutations apply to. The first chain when "
              "not given, which is what a single-chain structure means; "
              "name it explicitly for anything else.",
              example="A"),
        Field("protonation_margin", float, 1.0,
              "How close a ligand's pKa may come to the pH before setup stops "
              "rather than pick a charge state. The default is about the "
              "uncertainty of the pKa calculation itself, so the band marks "
              "where the answer is unresolved rather than merely close. "
              "Narrow it only for a ligand whose protonation you know.",
              example=0.5),
        Field("replace_nonstandard_residues", bool, True,
              "Substitute modified residues (selenomethionine, oxidised "
              "cysteine) with their standard equivalents. They are part of "
              "the polymer, and few force fields describe them directly."),
        Field("chains", list, None,
              "Chains to simulate, by their deposited ID -- for example "
              "[A, B]. Default: every chain in the structure. A deposited "
              "entry is what the experiment produced, not what anyone means "
              "to simulate: a crystal may hold two copies where one is "
              "wanted, and a complex may hold partners that belong to a "
              "different question. Heterogens are kept by proximity rather "
              "than by their own chain ID, so a ligand in a kept binding "
              "site comes with it however the entry numbers it.",
              example=["A", "B"]),
        Field("build_missing_termini", bool, False,
              "Build unresolved residues past the ends of a chain as well as "
              "the gaps between resolved ones. Off, because the two are not "
              "the same: a gap is pinned at both ends and what is built has "
              "to reach between them, while a terminus is anchored at one end "
              "only and what is built there is placed rather than determined. "
              "Cryo-EM entries commonly leave tens of residues unresolved at "
              "each terminus, and building them can extend a chain far enough "
              "to make the structure unusable."),
        Field("keep_heterogens", bool, False,
              "Retain non-standard residues. Equivalent to heterogens: keep."),
        Field("keep_water", bool, False,
              "Retain crystallographic waters."),
        Field("fixed_pdb", str, None,
              "Path to an already-fixed PDB to use directly, skipping "
              "PDBFixer. Default: run PDBFixer on the input.",
              example="prepared.pdb"),
        Field("forcefield", str, "auto",
              "Named force field. "
              "Resolves to the right XML files and water model. For an "
              "unlisted combination, use `force_field` instead.",
              # The registry in setup.forcefields defines these. It cannot
              # be imported here: reaching it runs the setup package's
              # __init__, which imports the phase, which imports this module.
              # So the names are restated and a test holds them level.
              choices=("auto", "amber-fb15", "amber-openff", "amber14",
                       "charmm36"),
              example="charmm36"),
        Field("force_field", list, None,
              "Raw OpenMM force-field XML file(s) — power-user escape hatch "
              "that overrides `forcefield`. Default: use `forcefield`.",
              example=["charmm36.xml", "charmm36/water.xml"]),
        Field("water_model", str, None,
              "Water model for Modeller (e.g. tip3p, tip4pew). "
              "Default: inferred from the force field.",
              example="tip3p"),
        Field("ligand", (str, list), None,
              "Ligand/cofactor SDF or MOL2 file(s) for protein-ligand "
              "systems. Requires a ligand-capable force field "
              "(forcefield: amber-openff).",
              example="ligand.sdf"),
        Field("ligand_forcefield", str, None,
              "OpenFF small-molecule force field for the ligand "
              "(e.g. openff-2.2.1, gaff-2.2.20). Default: per the chosen "
              "force field.",
              example="openff-2.2.1"),
        Field("ligand_name", str, "LIG",
              "Residue/molecule name assigned to the ligand."),
        Field("ligand_net_charge", int, None,
              "Ligand formal net charge. Read from the chemistry file's own "
              "formal charges when not given, and checked against them when "
              "it is: setup stops where the two disagree, because the file "
              "is what gets parameterised and a number cannot change the "
              "atoms in it. Supplying chemistry as a file therefore sets the "
              "protonation state, and an ideal SDF from the Chemical "
              "Component Dictionary is drawn in one particular form -- "
              "amidines and carboxylates in it are neutral, which is not "
              "their state at physiological pH.",
              example=1),
        Field("ligand_pose", str, "auto",
              "Where the ligand's starting coordinates come from. `auto` "
              "takes the pose from the structure where it holds the residue "
              "and from the supplied file where it does not. `structure` "
              "requires the structure's pose, refusing instead of quietly "
              "falling back to the file's arbitrary geometry. `file` keeps "
              "the supplied file's coordinates even on a complex -- a "
              "deliberately unbound start, e.g. as a known-negative control "
              "for the contact analyses. The clash check still applies.",
              example="file"),
        Field("check_ligand_clashes", bool, True,
              "Fail setup if the ligand pose severely overlaps the protein "
              "(the provided coordinates must be a feasible bound pose)."),
        Field("ligand_clash_threshold_nm", float, 0.15,
              "Minimum allowed ligand-protein contact distance in nm; pairs "
              "closer than this count as a clash."),
        Field("membrane", str, None,
              "Embed the protein in a lipid bilayer instead of a box of "
              "water. One of POPC, POPE, DLPC, DLPE, DMPC, DOPC or DPPC -- "
              "the lipids OpenMM can pack. A membrane protein simulated in "
              "water is not the protein: the hydrophobic belt that sits in "
              "the bilayer is exposed to solvent and the helices splay.",
              choices=("POPC", "POPE", "DLPC", "DLPE", "DMPC", "DOPC", "DPPC")),
        Field("membrane_orient", bool, False,
              "Rotate the structure so its longest axis lies along the "
              "membrane normal, which is what the bilayer is built around. "
              "Right for a transmembrane helix or a bundle of them, where "
              "the protein is longest along the direction it spans; wrong "
              "where a large soluble domain drags the axis away from the "
              "normal, and unable to tell which way up the protein ends. "
              "Where either matters, take an oriented structure from OPM."),
        Field("membrane_orientation_checked", bool, False,
              "Proceed with the structure's orientation as it is. The "
              "bilayer is built in the xy plane and the protein has to be "
              "lying along z already, which a PDB entry usually is not; the "
              "setup phase checks and refuses rather than embedding a "
              "protein sideways. Set this where you have oriented it "
              "yourself, or taken it from OPM."),
        Field("solvent_padding_nm", float, 1.0,
              "Minimum distance (nm) between solute and the box wall."),
        Field("box_shape", str, "dodecahedron",
              "Periodic box geometry: cube, dodecahedron, or octahedron. "
              "A dodecahedron holds the same clearance around the solute in "
              "roughly 71% of a cube\'s volume, so about a third of the water "
              "a cube would need is not simulated -- and water is most of the "
              "atoms. A cube is the right choice where the box must be "
              "orthorhombic, or where a solute tumbles far enough that only a "
              "cube keeps it clear of its own image.",
              choices=("cube", "dodecahedron", "octahedron")),
        Field("ion_positive", str, "Na+",
              "Counter-ion cation."),
        Field("ion_negative", str, "Cl-",
              "Counter-ion anion."),
        Field("ion_concentration_M", float, 0.15,
              "Target ionic strength in molar (physiological is 0.15)."),
        Field("neutralize", bool, True,
              "Add ions to neutralize the net solute charge."),
        Field("nonbonded_method", str, "PME",
              "Nonbonded method: NoCutoff, CutoffNonPeriodic, "
              "CutoffPeriodic, PME, or Ewald.",
              choices=("NoCutoff", "CutoffNonPeriodic", "CutoffPeriodic",
                       "PME", "Ewald")),
        Field("nonbonded_cutoff_nm", float, 1.0,
              "Real-space nonbonded cutoff in nm (cutoff/PME/Ewald methods)."),
        Field("ewald_error_tolerance", float, 0.0005,
              "Ewald/PME error tolerance."),
        Field("use_switching_function", bool, True,
              "Apply a switching function near the cutoff (cutoff methods)."),
        Field("switch_distance_nm", (int, float), None,
              "Switching-function turn-on distance in nm. "
              "Default: 0.9 × cutoff.",
              example=0.9),
        Field("dispersion_correction", bool, True,
              "Apply the long-range dispersion (vdW tail) correction."),
        Field("remove_cm_motion", bool, True,
              "Add a center-of-mass motion remover. Matches OpenMM's own "
              "default; without it the system is free to drift as a whole."),
        Field("constraints", str, "HBonds",
              "Bond constraints: None, HBonds, AllBonds, or HAngles."),
        Field("rigid_water", bool, True,
              "Constrain water bond lengths and angles."),
        Field("hydrogen_mass_amu", (int, float), None,
              "Hydrogen-mass-repartitioning mass in amu (enables longer "
              "timesteps). Default: off.",
              example=4.0),
        Field("temperature_K", (int, float), 300.0,
              "Temperature in K for initial velocity assignment."),
    ),
)


# ---------------------------------------------------------------------------
# Simulation phase
# ---------------------------------------------------------------------------
SIMULATION = PhaseSchema(
    name="simulation",
    description="Molecular dynamics: minimize, equilibrate (NVT, NPT), produce.",
    fields=(
        Field("duration_ns", (int, float), None,
              "Production length in ns (standard MD convention — "
              "equilibration is independent). Default: 2 ns.",
              example=100.0),
        Field("nvt_duration_ns", (int, float), None,
              "NVT equilibration in ns. Default: fixed 500 ps regardless "
              "of production length.",
              example=1.0),
        Field("npt_duration_ns", (int, float), None,
              "NPT equilibration in ns. Default: fixed 1 ns regardless of "
              "production length.",
              example=2.0),
        Field("nvt_steps", int, None,
              "NVT step count (overrides nvt_duration_ns). Default: 250000.",
              example=250000),
        Field("npt_steps", int, None,
              "NPT step count (overrides npt_duration_ns). Default: 500000.",
              example=500000),
        Field("production_steps", int, None,
              "Production step count (overrides duration_ns). Default: 1000000.",
              example=1000000),
        Field("prepared_from", str, None,
              "Directory holding a setup phase's system.xml, state.xml and "
              "topology.pdb, to simulate from instead of preparing again. "
              "Runs that share one prepared system share its water "
              "placement, so a difference between them is the setting that "
              "was changed rather than where the solvent happened to land.",
              example="runs/reference/setup"),
        Field("minimize", bool, True,
              "Run energy minimization before equilibration."),
        Field("integrator", str, "langevin_middle",
              "Integrator: langevin_middle, langevin, brownian, verlet, "
              "variable_langevin, or variable_verlet.",
              choices=("langevin_middle", "langevin", "brownian", "verlet",
                       "variable_langevin", "variable_verlet")),
        Field("integrator_error_tolerance", float, 0.001,
              "Error tolerance for the variable-timestep integrators "
              "(variable_langevin / variable_verlet)."),
        Field("minimize_tolerance_kjmol_per_nm", (int, float), 10.0,
              "Minimization force tolerance in kJ/mol/nm."),
        Field("minimize_max_iterations", int, 0,
              "Max minimization iterations (0 = until convergence)."),
        Field("timestep_fs", (int, float), 2.0,
              "Integrator timestep in fs."),
        Field("temperature_K", (int, float), 300.0,
              "Production temperature in K."),
        Field("pressure_bar", (int, float), 1.0,
              "Pressure for the Monte Carlo barostat in bar (OpenMM-native).",
              # The phase must start from None. Bar wins when both units are
              # given, so a phase table holding 1.0 would make bar always
              # present and quietly override an explicit pressure_atm.
              phase_sentinel=None),
        Field("pressure_atm", (int, float), None,
              "Pressure in atm (converted to bar internally). Accepted as "
              "an alternative to pressure_bar; bar wins if both are given.",
              example=1.0),
        Field("friction_per_ps", (int, float), 1.0,
              "Langevin thermostat friction coefficient in 1/ps."),
        Field("barostat_frequency", int, 25,
              "MC barostat volume-move attempt interval in steps."),
        Field("random_seed", int, None,
              "Integrator random seed for reproducibility. Default: unset.",
              example=42),
        Field("platform", str, "auto",
              "OpenMM compute platform: auto, CUDA, OpenCL, CPU, or HIP.",
              choices=("auto", "CUDA", "OpenCL", "CPU", "HIP")),
        Field("precision", str, "mixed",
              "GPU precision: single, mixed, or double.",
              choices=("single", "mixed", "double")),
        Field("device_index", str, None,
              "GPU device index for multi-GPU machines (e.g. '0' or '0,1').",
              example="0"),
        Field("trajectory_interval_steps", int, None,
              "DCD reporter interval in steps. Default: adaptive "
              "(~2000 frames per production run).",
              example=1000),
        Field("state_interval_steps", int, 1000,
              "Energy/state reporter interval in steps."),
        Field("save_selection", str, "not water",
              "Which atoms go into the trajectory, in MDTraj's selection "
              "language. The default leaves the solvent out, because water "
              "is nine tenths of a solvated system and nine tenths of the "
              "file: a 20 ns run of a small protein is about 740 MB with it "
              "and under 80 without. The topology matching what was saved "
              "is written beside the trajectory; the full system stays in "
              "setup/. Use `all` where the water itself is the subject -- "
              "the water-site analysis needs it, and says so rather than "
              "reporting an empty result.",
              example="all"),
        Field("checkpoint_interval_steps", int, 10000,
              "Binary checkpoint (.chk) interval in steps. Written for "
              "recovery by hand with OpenMM's loadCheckpoint; this software "
              "has no resume of its own yet -- see the note in the runner "
              "for why that is more than a missing flag. 0 disables it."),
        Field("live_telemetry", bool, True,
              "Write live_status.json, live_metrics.csv, and live_events.log "
              "for the local live dashboard. Also writes a live-frame PDB so "
              "the molecular viewer can refresh on the simulation. On by "
              "default: it costs a tenth of a per cent of the run, measured "
              "on a solvated system over 2,000 steps with and without, and "
              "the frame history is capped, so the only thing switching it "
              "off saves is the ability to watch. Nothing leaves the machine "
              "-- these are files in the run directory that the GUI reads."),
        Field("telemetry_interval", int, 1000,
              "Minimum step interval for live dashboard telemetry updates."),
        Field("dashboard_ligand_resname", str, None,
              "Override the ligand residue name surfaced in the dashboard's "
              "ligand tools pane. Auto-detected when omitted.",
              example="EPE"),
        Field("dashboard_binding_pocket_cutoff_A", float, 5.0,
              "Default binding-pocket cutoff in angstrom for the dashboard "
              "molecular viewer (used by the 'show pocket' tools).",
              example=5.0),
        Field("dashboard_max_playback_frames", int, 200,
              "Maximum frames the trajectory-playback panel will load. "
              "Frames are downsampled evenly from the full DCD.",
              example=200),
        Field("restrain", str, None,
              "What to hold still during equilibration, as an atom selection "
              "-- 'protein and not element H' is the usual choice. A "
              "structure that has just been minimised is not at equilibrium, "
              "and heating it lets the solute move as well as the solvent: "
              "side chains relax into the space crystal packing left, and a "
              "ligand drifts out of the pose that was measured. Restraints "
              "are released in stages and are off for production."),
        Field("restraint_release", list, None,
              "Force constants in kJ/mol/nm^2 stepped through during "
              "equilibration, ending at zero. Defaults to 1000, 500, 100, 0, "
              "which is the shape standard protocols use. Letting go all at "
              "once releases the solute into a solvent arrangement that "
              "formed around a rigid structure."),
        Field("restrain_production", bool, False,
              "Keep the restraints on during production. Off, because a "
              "biased production run measures the bias: RMSF, clustering and "
              "dimensionality reduction would describe the restraint as much "
              "as the system. Turning it on is recorded with the results."),
        Field("umbrella", dict, None,
              "Umbrella sampling: a free energy along a coordinate, from "
              "equilibrium sampling at a series of positions. A block with "
              "`collective_variable`, the selections it needs, a "
              "`force_constant`, and either `centres` or `from`/`to`/"
              "`n_windows`. Each window becomes a run, and the sampling is "
              "recombined into a potential of mean force -- unless adjacent "
              "windows fail to overlap, in which case the gap is reported "
              "rather than bridged. `minimum_overlap` sets how much two "
              "neighbours must share; `minimum_samples`, how full a window's "
              "histogram must be before anything is concluded from it. The "
              "system is prepared once and every window simulates from it, "
              "so a difference between windows is the restraint rather than "
              "where the water landed. Add a `steered` block beside this one "
              "and the study pulls once first and starts each window from "
              "the frame nearest its own centre -- which is what a window "
              "near a barrier needs, since a restraint cannot move a system "
              "across one and a window started on the wrong side stays "
              "there. `seed_from` reuses a finished pull instead of running "
              "another, so retuning the spacing or the force constant costs "
              "windows and not a pathway.",
              example={"collective_variable": "distance",
                       "selection_a": "resname BNZ", "selection_b": "protein",
                       "from": 0.3, "to": 1.5, "n_windows": 7,
                       "force_constant": 5000}),
        Field("steered", dict, None,
              "Pull the system along a named coordinate. A block with the "
              "same `collective_variable` metadynamics takes, plus `to` (the "
              "value to pull towards), optionally `from`, and `steps`. This "
              "gives a pathway and the work done along it, not a free "
              "energy: the work depends on how fast the anchor moves, and a "
              "single fast pull overestimates a barrier. Its usual purpose "
              "is generating starting structures for umbrella sampling.",
              example={"collective_variable": "distance",
                       "selection_a": "resname BNZ", "selection_b": "protein",
                       "to": 1.5, "steps": 500000}),
        Field("metadynamics", dict, None,
              "Metadynamics from a named collective variable, without "
              "writing PLUMED input. A block with `collective_variable` "
              "(distance, angle, torsion, radius_of_gyration, coordination, "
              "membrane_depth, q, ligand_distance or ligand_rmsd), the "
              "selections it needs, and `sigma` -- "
              "the hill width, roughly the size of the fluctuations within a "
              "single state. Well-tempered by default, because plain "
              "metadynamics never lets the bias settle. Choosing the "
              "variable is the decision the method turns on: if it does not "
              "distinguish the states that matter, the surface converges and "
              "describes something that is not the system.",
              example={"collective_variable": "radius_of_gyration",
                       "selection": "protein and name CA", "sigma": 0.02,
                       "height_kjmol": 1.2, "pace_steps": 500}),
        Field("plumed", dict, None,
              "Optional PLUMED enhanced sampling: `enabled` turns it on, "
              "and `script` is the PLUMED input -- the script text itself, "
              "which then travels inside the config, or a path to a .dat "
              "file on the machine that runs the study. Requires the "
              "openmm-plumed package."),
    ),
)


# ---------------------------------------------------------------------------
# Analysis phase
# ---------------------------------------------------------------------------
ANALYSIS = PhaseSchema(
    name="analysis",
    description="Trajectory analysis: RMSD, RMSF, Rg, H-bonds, SS, SASA, etc.",
    fields=(
        Field("trajectory", str, None,
              "Trajectory file. Default: simulation/production.dcd.",
              example="simulation/production.dcd"),
        Field("topology", str, None,
              "Topology file. Default: simulation/topology.pdb.",
              example="simulation/topology.pdb"),
        Field("include", list, None,
              "Subset of analyses to run. Default: every analysis the "
              "system supports -- nine always, the fraction of native "
              "contacts where the chain is long enough to have a fold, "
              "water sites where the "
              "trajectory has water, five more where there is a ligand, and "
              "the potential of mean force where an umbrella study produced "
              "one, the free energy surface where a metadynamics run "
              "did, and the work done where a pull was steered. "
              "Mutually exclusive with `exclude`.",
              choices=ANALYSIS_NAMES,
              example=["rmsd", "rmsf", "rg", "cluster"]),
        Field("exclude", list, None,
              "Analyses to skip. Mutually exclusive with `include`.",
              choices=ANALYSIS_NAMES,
              example=["dimred"]),
        Field("selection", str, None,
              "Default MDTraj atom selection applied across analyses. "
              "Overrides `scope` when set.",
              example="name CA"),
        Field("scope", str, "solute",
              "Which atoms an analysis measures when it has no selection of "
              "its own. 'solute' is the protein and any ligand, and is the "
              "default: including the water and ions would measure the "
              "solvent box rather than the molecule in it, so a radius of "
              "gyration would report the size of the box and barely move. "
              "'all' is there for when the solvent is the point.",
              choices=("solute", "protein", "ligand", "all"),
              example="solute"),
        Field("stride", int, None,
              "Load every Nth frame from the trajectory.",
              example=1),
        Field("first", int, None,
              "First frame index to include.",
              example=0),
        Field("last", int, None,
              "Last frame index (exclusive). Default: full trajectory.",
              example=10000),
        Field("options", dict, None,
              "Per-analysis option overrides, keyed by analysis name. "
              "E.g. {cluster: {methods: [kmeans], n_clusters: 5}}.",
              example={"cluster": {"methods": ["kmeans"], "n_clusters": 5}}),
    ),
)


# ---------------------------------------------------------------------------
# Report phase
# ---------------------------------------------------------------------------
REPORT = PhaseSchema(
    name="report",
    description="Generate the Markdown report, PPTX slides, and project bundle.",
    fields=(
        Field("title", str, None,
              "Report title. Default: auto-generated from the system name.",
              example="My MD Study"),
        Field("author", str, None,
              "Author name recorded in the report metadata.",
              example="A. Aina"),
        Field("document", bool, True,
              "Generate the Markdown study report."),
        Field("slides", bool, True,
              "Generate the PPTX slide deck."),
        Field("pdf", bool, True,
              "Render the report as a PDF as well as Markdown. Needs "
              "WeasyPrint and a Markdown converter; where they are absent "
              "the run says so and continues, because four other formats "
              "were produced. Install with the [pdf] extra, or from "
              "conda-forge where the system libraries come with it."),
        Field("bundle", bool, True,
              "Generate the self-contained project_bundle.zip."),
        Field("include_methods", bool, True,
              "Include the Methods section in the document."),
        Field("include_reproducibility", bool, True,
              "Include the Reproducibility section in the document."),
        Field("region_highlights", list, None,
              "Optional user-defined residue ranges to highlight on RMSF "
              "report figures. Each item should include label, start, end, "
              "and optionally color.",
              example=[
                  {"label": "example region 1", "start": 3, "end": 7, "color": "#4E79A7"},
                  {"label": "example helix", "start": 10, "end": 14, "color": "#F28E2B"},
              ]),
        Field("comparison", bool, True,
              "For a multi-run study, build the cross-run comparison report "
              "(overlays + parameter trends) under comparison/."),
    ),
)


# ---------------------------------------------------------------------------
# Execution (batch run scheduling)
# ---------------------------------------------------------------------------
EXECUTION = PhaseSchema(
    name="execution",
    description="How the runs are scheduled: sequentially or in parallel.",
    fields=(
        Field("mode", str, "sequential",
              "Run scheduling: 'sequential' (one at a time) or 'parallel'.",
              choices=("sequential", "parallel")),
        Field("workers", int, None,
              "Parallel worker count. Default: one per device if `devices` "
              "is set, else the CPU count (capped at the number of runs).",
              example=2),
        Field("devices", list, None,
              "GPU device indices to distribute parallel runs across, "
              "round-robin (one run per device). GPU runs only.",
              example=[0, 1]),
        Field("continue_on_error", bool, True,
              "If a run fails, record it and continue (True) or stop the "
              "whole batch (False)."),
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# How the settings are grouped
# ---------------------------------------------------------------------------
#: Settings, in the order somebody meets the decisions they stand for.
#:
#: Thirty-six settings for setup and thirty-seven for simulation arrived as one
#: flat list each, in the order they happened to be declared: a pH sat beside a
#: dispersion correction, and finding the one you wanted meant reading all of
#: them. The schema is the one place that knows what a setting is, so it is the
#: place that says what it is *about*.
#:
#: Declared here rather than on each Field so the order within a group is
#: visible as an order, and so adding a group does not mean editing eighty-odd
#: declarations. A setting left out of every group is caught by a test, not by
#: somebody noticing it missing from the page.
SETTING_GROUPS: dict[str, tuple[tuple[str, str, tuple[str, ...]], ...]] = {
    "setup": (
        ("The structure",
         "What is kept, what is repaired, and how it is protonated.",
         ("ph", "protonation_margin", "heterogens", "keep_heterogens",
          "keep_water", "replace_nonstandard_residues",
          "chains", "build_missing_termini", "fixed_pdb",
          "mutations", "mutation_chain")),
        ("The ligand",
         "Found and parameterised, or named if the structure is ambiguous.",
         ("ligand", "ligand_name", "ligand_forcefield", "ligand_net_charge",
          "ligand_pose", "check_ligand_clashes",
          "ligand_clash_threshold_nm")),
        ("The membrane",
         "A bilayer to embed in, and whether the orientation is trusted.",
         ("membrane", "membrane_orient", "membrane_orientation_checked")),
        ("Solvent, ions and the box",
         "How much water, of what kind, at what salt concentration.",
         ("water_model", "solvent_padding_nm", "box_shape", "neutralize",
          "ion_positive", "ion_negative", "ion_concentration_M")),
        ("The force field",
         "What the atoms are, and which motions are held rigid.",
         ("forcefield", "force_field", "constraints", "rigid_water",
          "hydrogen_mass_amu", "temperature_K")),
        ("How forces are computed",
         "The long-range treatment. Defaults suit a solvated protein; "
         "changing one changes the physics.",
         ("nonbonded_method", "nonbonded_cutoff_nm", "ewald_error_tolerance",
          "use_switching_function", "switch_distance_nm",
          "dispersion_correction", "remove_cm_motion")),
    ),
    "simulation": (
        ("How long it runs",
         "Production length, and the equilibration before it.",
         ("duration_ns", "nvt_duration_ns", "npt_duration_ns",
          "production_steps", "nvt_steps", "npt_steps")),
        ("Where it starts",
         "A system prepared here or elsewhere, and how hard it is minimised "
         "first.",
         ("prepared_from", "minimize", "minimize_tolerance_kjmol_per_nm",
          "minimize_max_iterations")),
        ("Conditions",
         "The thermodynamic state the run is held at.",
         ("temperature_K", "pressure_bar", "pressure_atm", "friction_per_ps",
          "barostat_frequency")),
        ("The integrator",
         "How the equations of motion are stepped.",
         ("integrator", "timestep_fs", "integrator_error_tolerance",
          "random_seed")),
        ("Enhanced sampling",
         "Bias the run to reach what it would not reach on its own. Each is "
         "a block of settings, and each says what its output is and is not.",
         ("umbrella", "steered", "metadynamics", "plumed")),
        ("Restraints",
         "Hold part of the system still while the rest settles.",
         ("restrain", "restraint_release", "restrain_production")),
        ("Where it runs",
         "The compute platform, and which device.",
         ("platform", "precision", "device_index")),
        ("What gets written",
         "How often frames, states and checkpoints are saved, and which "
         "atoms go into them.",
         ("trajectory_interval_steps", "state_interval_steps",
          "checkpoint_interval_steps", "save_selection")),
        ("Watching it run",
         "What the live dashboard shows while the simulation is going.",
         ("live_telemetry", "telemetry_interval", "dashboard_ligand_resname",
          "dashboard_binding_pocket_cutoff_A",
          "dashboard_max_playback_frames")),
    ),
    "analysis": (
        ("What to measure",
         "Which analyses run, and how each is configured.",
         ("include", "exclude", "options")),
        ("What to measure it on",
         "The trajectory, and which atoms count.",
         ("trajectory", "topology", "selection", "scope")),
        ("Which frames",
         "Trimming and thinning before anything is measured.",
         ("first", "last", "stride")),
    ),
    "report": (
        ("What it says",
         "Who it is by, and which sections it carries.",
         ("title", "author", "include_methods", "include_reproducibility",
          "region_highlights", "comparison")),
        ("What comes out",
         "The formats written to the run directory.",
         ("document", "slides", "pdf", "bundle")),
    ),
    # Not a phase, and it still has to be reachable. Without an entry here
    # `grouped_fields("execution")` returned an empty tuple, so the GUI drew
    # no controls for it and `--help` had no section to put its flags under.
    "execution": (
        ("How the runs are scheduled",
         "One at a time, or several at once across the devices named.",
         ("mode", "workers", "devices", "continue_on_error")),
    ),
}


def grouped_fields(phase: str) -> tuple[tuple[str, str, tuple[Field, ...]], ...]:
    """A phase's settings in named groups, in the order they are declared.

    Anything the groups do not name is returned last under "Other", so a
    setting added without being placed still appears rather than vanishing
    from every interface at once. The test is what makes that a warning
    rather than a habit.
    """
    group = PHASE_SCHEMAS.get(phase) or (EXECUTION if phase == "execution"
                                         else None)
    if group is None:
        return ()
    by_name = {f.name: f for f in group.fields}
    placed: set[str] = set()
    out: list[tuple[str, str, tuple[Field, ...]]] = []
    for title, why, names in SETTING_GROUPS.get(phase, ()):
        fields = tuple(by_name[n] for n in names if n in by_name)
        placed.update(f.name for f in fields)
        if fields:
            out.append((title, why, fields))
    left = tuple(f for f in group.fields if f.name not in placed)
    if left:
        out.append(("Other", "Not yet placed in a group.", left))
    return tuple(out)


PHASE_SCHEMAS: dict[str, PhaseSchema] = {
    "setup": SETUP,
    "simulation": SIMULATION,
    "analysis": ANALYSIS,
    "report": REPORT,
}

# Phase keys recognized at the top level of a config file (the per-phase
# option blocks).
PHASE_KEYS = tuple(PHASE_SCHEMAS.keys())

# Batch top-level keys. `systems` is the canonical (and only) way to
# specify input — always a list, even for a single system. `sweep`
# defines parameter axes. They have bespoke structure (a list of
# mappings; a mapping of dotted-axis -> value-list) validated by the
# batch layer rather than the per-field type checker.
BATCH_KEYS = ("systems", "sweep", "execution")

# All keys recognized at the top level: the scalar top-level fields plus
# the per-phase block names plus the batch keys.
TOP_LEVEL_KEYS = TOP_LEVEL.field_names() | set(PHASE_KEYS) | set(BATCH_KEYS)


def all_schemas() -> dict[str, PhaseSchema]:
    """Return every schema, including the top-level pseudo-phase.

    `execution` is here and is not in `PHASE_SCHEMAS`, which is the
    distinction worth keeping: the four phases drive the plan, and this
    block says how the runs the plan produces are scheduled. It was in
    neither, so the flag generator, the GUI form builder and the parity
    suite all walked past it -- and the parity suite iterating this
    function is why the omission stayed invisible. `mode`, `workers`,
    `devices` and `continue_on_error` were reachable only by writing a
    config file.
    """
    return {"(top-level)": TOP_LEVEL, **PHASE_SCHEMAS, "execution": EXECUTION}
