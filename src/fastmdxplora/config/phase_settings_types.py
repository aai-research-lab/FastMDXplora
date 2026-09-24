"""One TypedDict per phase method, generated from the schema.

Written by ``python -m fastmdxplora.config.phase_settings``; not edited by
hand. A test fails while it disagrees with the schema."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

__all__ = ["SetupSettings", "SimulateSettings", "AnalyzeSettings", "ReportSettings"]


class SetupSettings(TypedDict, total=False):
    """What ``setup()`` accepts: the ``setup:`` settings."""

    agent: Literal['assisted', 'autonomous', 'unvalidated']
    ph: float
    heterogens: Literal['auto', 'drop', 'keep']
    mutations: list[Any]
    mutation_chain: str
    protonation_margin: float
    replace_nonstandard_residues: bool
    chains: list[Any]
    build_missing_termini: bool
    keep_heterogens: bool
    keep_water: bool
    fixed_pdb: str
    forcefield: Literal['auto', 'amber-fb15', 'amber-openff', 'amber14', 'charmm36']
    force_field: list[Any]
    water_model: str
    ligand: str | list[Any]
    ligand_forcefield: str
    ligand_name: str
    ligand_resname: str
    ligand_net_charge: int
    ligand_pose: str
    check_ligand_clashes: bool
    ligand_clash_threshold_nm: float
    membrane: Literal['POPC', 'POPE', 'DLPC', 'DLPE', 'DMPC', 'DOPC', 'DPPC']
    membrane_orient: bool
    membrane_orientation_checked: bool
    solvent_padding_nm: float
    box_shape: Literal['cube', 'dodecahedron', 'octahedron']
    ion_positive: str
    ion_negative: str
    ion_concentration_M: float
    neutralize: bool
    nonbonded_method: Literal['NoCutoff', 'CutoffNonPeriodic', 'CutoffPeriodic', 'PME', 'Ewald']
    nonbonded_cutoff_nm: float
    ewald_error_tolerance: float
    use_switching_function: bool
    switch_distance_nm: float
    dispersion_correction: bool
    remove_cm_motion: bool
    constraints: str
    rigid_water: bool
    hydrogen_mass_amu: float
    temperature_K: float


class SimulateSettings(TypedDict, total=False):
    """What ``simulate()`` accepts: the ``simulation:`` settings."""

    agent: Literal['assisted', 'autonomous', 'unvalidated']
    duration_ns: float
    nvt_duration_ns: float
    npt_duration_ns: float
    nvt_steps: int
    ensemble: Literal['npt', 'nvt']
    npt_steps: int
    production_steps: int
    setup_from: str
    prepared_from: str
    extra_ns: float
    resume_unsealed: bool
    resume_from: str
    minimize: bool
    integrator: Literal[
        'langevin_middle', 'langevin', 'brownian', 'verlet', 'variable_langevin',
        'variable_verlet'
    ]
    integrator_error_tolerance: float
    minimize_tolerance_kjmol_per_nm: float
    minimize_max_iterations: int
    timestep_fs: float
    temperature_K: float
    pressure_bar: float
    pressure_atm: float
    friction_per_ps: float
    barostat_frequency: int
    random_seed: int
    platform: Literal['auto', 'CUDA', 'OpenCL', 'CPU', 'HIP']
    precision: Literal['single', 'mixed', 'double']
    device_index: str
    trajectory_interval_steps: int
    state_interval_steps: int
    save_selection: str
    checkpoint_interval_steps: int
    live_telemetry: bool
    telemetry_interval: int
    dashboard_ligand_resname: str
    dashboard_binding_pocket_cutoff_A: float
    dashboard_max_playback_frames: int
    restrain: str
    restraint_release: list[Any]
    restrain_production: bool
    umbrella: dict[str, Any]
    steered: dict[str, Any]
    metadynamics: dict[str, Any]
    plumed: dict[str, Any]


class AnalyzeSettings(TypedDict, total=False):
    """What ``analyze()`` accepts: the ``analysis:`` settings."""

    agent: Literal['assisted', 'autonomous', 'unvalidated']
    trajectory: str
    topology: str
    include: list[Literal[
        'rmsd', 'rmsf', 'rg', 'hbonds', 'ss', 'sasa', 'dihedrals', 'qvalue', 'cluster', 'dimred',
        'water_sites', 'ligand_rmsd', 'ligand_rmsf', 'pl_contacts', 'pl_hbonds', 'pl_interactions',
        'order_parameters', 'bfactor_comparison', 'thermodynamics', 'rdf', 'pmf', 'metad_surface',
        'steered_work', 'coordination_number', 'end_to_end', 'moments_of_inertia', 'pair_distance'
    ]]
    exclude: list[Literal[
        'rmsd', 'rmsf', 'rg', 'hbonds', 'ss', 'sasa', 'dihedrals', 'qvalue', 'cluster', 'dimred',
        'water_sites', 'ligand_rmsd', 'ligand_rmsf', 'pl_contacts', 'pl_hbonds', 'pl_interactions',
        'order_parameters', 'bfactor_comparison', 'thermodynamics', 'rdf', 'pmf', 'metad_surface',
        'steered_work', 'coordination_number', 'end_to_end', 'moments_of_inertia', 'pair_distance'
    ]]
    select_atoms: str
    selection: str
    scope: Literal['solute', 'protein', 'ligand', 'all']
    stride: int
    first: int
    last: int
    figure_colours: Literal['colour', 'greyscale', 'both']
    options: dict[str, Any]


class ReportSettings(TypedDict, total=False):
    """What ``report()`` accepts: the ``report:`` settings."""

    agent: Literal['assisted', 'autonomous', 'unvalidated']
    title: str
    author: str
    document: bool
    slides: bool
    pdf: bool
    bundle: bool
    include_methods: bool
    include_reproducibility: bool
    region_highlights: list[Any]
    comparison: bool
