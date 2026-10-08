"""What is read of an MD study in a paper, one entry a setting.

The list the AI model is asked to fill, the list each reading is checked
against, and the list a reading is scored by (:mod:`fastmdxplora.validation.
paper_reading`): one list, so the three cannot drift apart. ``kind`` says
how a value is read from the paper's words (:mod:`.values`): a quantity in a
unit, a count, or a name, which must be in the words it was read from.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Field", "FIELDS", "BY_NAME", "STUDY_FIELDS", "PROTOCOL_FIELDS",
           "METHODS", "CLAIM_QUANTITIES", "ERROR_KINDS"]


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # temperature, pressure, time, timestep, length, concentration, mass, count, name, names, flag
    asks: str


FIELDS: tuple[Field, ...] = (
    # What is simulated.
    Field("system", "name", "what is simulated, in a few words (protein, complex, bilayer, duplex)"),
    Field("pdb_id", "name", "the PDB entry the starting structure came from (four characters)"),
    Field("structure_source", "name",
          "where the starting structure came from when not a PDB entry as deposited "
          "(a model, a docked pose, a built peptide, another paper's system)"),
    Field("chains", "names", "which chains of the entry were simulated"),
    Field("mutations", "names", "point mutations made, each as written (D189N, Asp189Asn)"),
    Field("ligands", "names", "small molecules, cofactors and ions kept or added beside the macromolecule"),
    Field("membrane", "name", "the bilayer's lipids and their ratio"),
    Field("copies", "count", "how many copies of the molecule are in the box"),
    # The force field.
    Field("protein_forcefield", "name", "the force field for the protein (ff14SB, CHARMM36m)"),
    Field("nucleic_forcefield", "name", "the force field for DNA or RNA (OL15, bsc1)"),
    Field("lipid_forcefield", "name", "the force field for lipids (CHARMM36, Lipid21)"),
    Field("ligand_forcefield", "name", "how small molecules were parameterised (GAFF2, CGenFF, OpenFF)"),
    Field("ligand_charges", "name", "the ligand's partial-charge method (AM1-BCC, RESP)"),
    Field("water_model", "name", "the water model (TIP3P, OPC, TIP4P-Ew)"),
    # The box.
    Field("box_shape", "name", "the box's shape (cubic, truncated octahedron, dodecahedron, rectangular)"),
    Field("padding", "length", "the least distance from the solute to the box's edge"),
    Field("salt_concentration", "concentration", "the salt added beyond neutralising"),
    Field("ions", "names", "which ions (Na+, Cl-, K+)"),
    Field("neutralized", "flag", "whether counter-ions neutralised the system's charge"),
    Field("protonation", "name", "protonation states set or the pH they were set for"),
    # The physics.
    Field("temperature", "temperature", "the temperature of production"),
    Field("pressure", "pressure", "the pressure"),
    Field("ensemble", "name", "the ensemble of production (NPT, NVT)"),
    Field("thermostat", "name", "the thermostat (Langevin, Nose-Hoover, v-rescale, Berendsen)"),
    Field("barostat", "name", "the barostat (Monte Carlo, Parrinello-Rahman, Berendsen, C-rescale)"),
    Field("timestep", "timestep", "the integration timestep of production"),
    Field("constraints", "name", "which bonds were constrained (bonds to hydrogen, all bonds) and how (SHAKE, LINCS)"),
    Field("hydrogen_mass", "mass", "the hydrogen mass when hydrogen mass repartitioning was used"),
    Field("cutoff", "length", "the nonbonded (van der Waals) cutoff"),
    Field("switch", "length", "where the van der Waals switching or force-switching begins"),
    Field("electrostatics", "name", "the long-range electrostatics (PME, Ewald, reaction field)"),
    # The protocol.
    Field("minimization", "name", "how the system was minimised"),
    Field("nvt_equilibration", "time", "how long equilibration at constant volume lasted"),
    Field("npt_equilibration", "time", "how long equilibration at constant pressure lasted"),
    Field("equilibration_restraints", "name", "what was restrained during equilibration"),
    Field("production", "time", "the length of production, per replica"),
    Field("replicas", "count", "how many independent production runs of this study"),
    Field("engine", "name", "the simulation program and its version"),
    Field("method", "name",
          "plain MD, or the enhanced-sampling or other method (umbrella sampling, "
          "metadynamics, replica exchange, free-energy perturbation, accelerated MD, "
          "steered MD, QM/MM, coarse-grained, implicit solvent, milestoning)"),
    Field("method_details", "name", "the method's settings (windows, collective variables, biases)"),
)

BY_NAME = {field.name: field for field in FIELDS}

#: What tells one study from another in a paper: asked for each study.
STUDY_FIELDS = ("system", "pdb_id", "structure_source", "chains", "mutations", "ligands",
                "membrane", "copies", "protein_forcefield", "nucleic_forcefield",
                "lipid_forcefield", "ligand_forcefield", "water_model", "temperature",
                "production", "replicas", "method", "engine")

#: The protocol's settings, often shared by every study: asked once per protocol.
PROTOCOL_FIELDS = tuple(field.name for field in FIELDS)

#: The methods a study's ``method`` is read as, for deciding what can run.
METHODS = ("plain", "umbrella", "metadynamics", "replica_exchange", "free_energy",
           "accelerated", "steered", "qm_mm", "coarse_grained", "implicit_solvent",
           "milestoning", "other")

#: What a reported result can be, and the analysis of this software that
#: gives the same quantity, where one does.
CLAIM_QUANTITIES = {
    "rmsd": "rmsd",
    "rmsf": "rmsf",
    "radius_of_gyration": "rg",
    "sasa": "sasa",
    "secondary_structure": "ss",
    "hydrogen_bonds": "hbonds",
    "end_to_end_distance": "end_to_end",
    "distance": "pair_distance",
    "ligand_rmsd": "ligand_rmsd",
    "contacts": "pl_contacts",
    "interaction_occupancy": "pl_interactions",
    "area_per_lipid": "area_per_lipid",
    "bilayer_thickness": "bilayer_thickness",
    "order_parameter": "lipid_order",
    "free_energy": "pmf",
    "other": "",
}

#: What a reported ``±`` is.
ERROR_KINDS = ("standard_error", "standard_deviation", "confidence_95", "range", "none",
               "unstated")
