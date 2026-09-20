"""The paragraph a journal asks for, written from what the run recorded.

A methods section for a molecular dynamics study has to state a specific list
of things: where the coordinates came from, how protonation was decided, which
force field *version*, which water model, the ion concentration, the box, the
ensembles, the integrator and timestep, the thermostat and barostat with their
coupling constants, the cutoffs and how long-range electrostatics were handled,
the constraints, and how long each phase ran. The list is not folklore -- it is
published, in the Journal of Chemical Information and Modeling's *Guidelines
for Reporting Molecular Dynamics Simulations* (Soares et al., 2023) and in
Communications Biology's reliability and reproducibility checklist (2023).

Every one of those values is already recorded here, in
``setup_parameters.json``, ``simulation_parameters.json``, and the config
written beside the results. What was missing was the assembly. Nobody types
this paragraph correctly from memory, which is why the drMD authors reported
difficulty reproducing a published protocol -- not for technical reasons, but
because of "ambiguity in how the protocol was presented".

So this writes it, and where a value was not recorded it says so rather than
filling in what is usual. A methods section that quietly states a default
nobody chose is worse than one with a gap in it: the gap is visible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["methods_paragraphs", "missing_from_methods", "CHECKLIST"]

#: What each integrator is called in the literature. The identifiers are FastMDXplora's
#: and a reader looking one up needs the published name.
_INTEGRATOR_NAMES = {
    "langevin_middle": "Langevin middle-scheme",
    "langevin": "Langevin",
    "verlet": "Verlet",
    "brownian": "Brownian",
    "nose_hoover": "Nose-Hoover",
    "variable_langevin": "variable-timestep Langevin",
    "variable_verlet": "variable-timestep Verlet",
}


#: What a methods section has to state, and where this software keeps it.
#: Written down so the gaps can be reported rather than discovered by a
#: reviewer.
CHECKLIST: tuple[tuple[str, str], ...] = (
    ("starting coordinates", "the structure and its source"),
    ("protonation", "pH and how ionizable groups were assigned"),
    ("missing atoms", "what was rebuilt and how"),
    ("force field", "name and version, for protein and for any ligand"),
    ("water model", "the explicit solvent model"),
    ("box", "shape and how much padding"),
    ("ions", "species and concentration, and whether neutralising"),
    ("system size", "atoms after solvation"),
    ("electrostatics", "method and cutoff"),
    ("constraints", "which bonds, and whether water is rigid"),
    ("minimisation", "algorithm and tolerance"),
    ("ensembles", "what ran under which ensemble, and for how long"),
    ("integrator", "which one, and the timestep"),
    ("thermostat", "temperature and coupling"),
    ("barostat", "pressure and coupling frequency"),
    ("sampling", "how often frames were kept, and how many"),
    ("software", "versions of everything that did the work"),
)


def _get(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in params and params[name] not in (None, ""):
            return params[name]
    return default


def _nm(value: Any) -> str:
    return f"{float(value):.2f} nm" if value is not None else "not recorded"


def _steps_to_ns(steps: Any, timestep_fs: Any) -> str:
    """Steps are what somebody sets; nanoseconds are what a reader needs."""
    try:
        ns = int(steps) * float(timestep_fs) / 1e6
    except (TypeError, ValueError):
        return "not recorded"
    if ns >= 1:
        return f"{ns:.3g} ns"
    return f"{ns * 1000:.3g} ps"


def missing_from_methods(setup: dict[str, Any], sim: dict[str, Any]) -> list[str]:
    """Checklist items this run cannot supply.

    Reported rather than glossed. A reviewer will ask; better that the report
    asks first.

    Reads the same flattened view the prose does. Checking the raw manifests
    while the prose read the resolved ones had the report say "with the tip3p
    water model" and "does not supply water model" in adjacent paragraphs,
    which is worse than either alone: a reader cannot tell which to believe.
    """
    setup = _flatten(setup)
    sim = _flatten(sim)

    gaps = []
    if not setup:
        gaps.append("system preparation (setup did not run, or recorded nothing)")
    if not sim:
        gaps.append("simulation protocol (simulation did not run, or recorded nothing)")

    resolved = setup.get("resolved_forcefield")
    resolved = resolved if isinstance(resolved, dict) else {}
    if setup and not (resolved.get("water_model") or _get(setup, "water_model")):
        gaps.append("water model")
    if setup and _get(setup, "n_atoms_solvated", "solvated_atoms",
                      "n_atoms", "atom_count") is None:
        gaps.append("system size after solvation")
    if sim and _get(sim, "integrator") is None:
        gaps.append("integrator")
    if sim and _get(sim, "pressure_bar_used", "pressure_bar") is None:
        gaps.append("pressure, for the constant-pressure stages")
    return gaps


def _flatten(manifest: dict[str, Any]) -> dict[str, Any]:
    """One mapping from a manifest that nests.

    ``setup_parameters.json`` holds what was asked for under ``parameters``
    and what the run worked out beside it: the system under ``input``, the
    force field it resolved to under ``resolved_forcefield``. Reading only
    ``parameters`` gave a methods section saying the force field was
    "amber-openff" -- the name of a choice rather than the thing chosen --
    and no water model at all, because the resolution is where that lives.
    """
    flat = dict(manifest.get("parameters") or {})
    for key, value in manifest.items():
        if key == "parameters":
            continue
        if isinstance(value, dict):
            for inner, inner_value in value.items():
                # An inner name wins only where the outer did not supply one:
                # `parameters` is what somebody set, and stands.
                flat.setdefault(inner, inner_value)
            flat.setdefault(key, value)
        else:
            flat.setdefault(key, value)
    return flat


def methods_paragraphs(
    project_root: Path,
    setup: dict[str, Any],
    sim: dict[str, Any],
    *,
    system_name: str | None = None,
    versions: dict[str, str] | None = None,
) -> str:
    """The methods text, as prose rather than a list of settings.

    A list of settings is what the software knows; a methods section is what a
    reader needs, and they are not the same document. The list is still
    written elsewhere in the report -- this is the part somebody pastes into a
    manuscript.
    """
    parts: list[str] = []
    versions = versions or {}
    setup = _flatten(setup)
    sim = _flatten(sim)

    # ---- system preparation -------------------------------------------
    if setup:
        source = system_name or _get(setup, "system", default="the input structure")
        # Setup records what the input was; guessing from the length of the
        # string calls any four-character filename a deposited structure, and
        # a methods section saying coordinates came from the Protein Data Bank
        # when they came off a disk is a false statement about provenance.
        recorded = setup.get("input")
        form = recorded.get("form") if isinstance(recorded, dict) else None
        if form is None:
            form = "pdb_id" if isinstance(source, str) and len(source) == 4 else None
        origin = (
            f"the Protein Data Bank entry {source}"
            if form == "pdb_id"
            else f"`{source}`"
        )

        # Setup now records what the structure was, and this sentence is the
        # reason it is worth recording: a methods section saying coordinates
        # came from `prepped.pdb` states a filename on somebody's laptop.
        # Where the file's own header names the entry it began as, that is
        # the checkable version of the same sentence.
        structure = (recorded or {}).get("structure") if isinstance(recorded, dict) else None
        structure = structure if isinstance(structure, dict) else {}
        entry = structure.get("entry")
        when = str(structure.get("retrieved_at") or "")[:10]

        if form == "pdb_id" and when:
            # Deposited entries are revised, so the identifier alone does not
            # say which coordinates were used.
            origin += f", retrieved {when}"
        elif form != "pdb_id" and entry:
            # Attributed to the header, not asserted: a prepared structure
            # keeps the header of the entry it began as, which is what makes
            # this useful and also what stops it being a claim of identity.
            deposited = structure.get("deposited")
            origin += (
                f", whose header identifies it as Protein Data Bank entry "
                f"{entry}"
            )
            origin += f" (deposited {deposited})" if deposited else ""

        preparation = [
            f"Starting coordinates were taken from {origin}."
        ]
        digest = structure.get("sha256")
        if digest:
            preparation.append(
                f"The file itself is recorded in the setup manifest by its "
                f"SHA-256 digest ({digest[:12]}), which identifies it "
                f"independently of the path it was read from."
            )
        ph = _get(setup, "ph")
        if ph is not None:
            preparation.append(
                f"Missing heavy atoms and hydrogens were added with PDBFixer at "
                f"pH {ph}, which sets the protonation of ionizable side chains."
            )
        heterogens = _get(setup, "heterogens")
        if heterogens:
            # Say what the policy is, not only its name. "the `auto` policy"
            # told a reader nothing they could repeat.
            policy = {
                "auto": "non-standard residues were decided per component: "
                        "any ligand that could be parameterised was kept and "
                        "prepared, crystallographic water and buffer were "
                        "removed, and setup stopped rather than guess where "
                        "the structure did not determine what to simulate",
                "drop": "all non-standard residues were removed, and what "
                        "went is recorded",
                "keep": "all non-standard residues were retained",
            }.get(str(heterogens), f"heterogens were handled under the `{heterogens}` policy")
            preparation.append(
                f"Heterogens: {policy}. The decision taken for each is "
                "recorded in `setup/setup_parameters.json`."
            )

        resolved = setup.get("resolved_forcefield")
        resolved = resolved if isinstance(resolved, dict) else {}
        # The XML files are what was actually used; the name is FastMDXplora's label for
        # a choice, and a reader cannot look up "amber-openff".
        xmls = resolved.get("xmls")
        force_field = (
            ", ".join(str(x) for x in xmls) if xmls
            else _get(setup, "forcefield", "force_field")
        )
        water = resolved.get("water_model") or _get(setup, "water_model")
        ligand_ff = (resolved.get("small_molecule_forcefield")
                     or _get(setup, "ligand_forcefield"))
        # Whether a ligand was *parameterised*, not whether one could have
        # been. `ligand_name` defaults to LIG -- a naming convention for a
        # ligand if there is one -- and the small-molecule force field is a
        # property of the protein force field, present whether or not it was
        # used. Testing those two put "The ligand LIG was parameterized with
        # openff-2.2.1" into the methods section of every run, including a
        # tri-alanine in water with no ligand in it. A methods section is the
        # part of a report that gets published.
        parameterised = _get(setup, "ligand")
        ligand_name = _get(setup, "ligand_name", "ligand")

        parameterisation = []
        if force_field:
            parameterisation.append(
                f"The protein was described by the {force_field} force field"
                + (f" with the {water} water model" if water else "")
                + "."
            )
        if parameterised and ligand_name and ligand_ff:
            charge = _get(setup, "ligand_net_charge")
            parameterisation.append(
                f"The ligand {ligand_name} was parameterized with {ligand_ff}"
                + (f" at a net charge of {charge:+d}"
                   if isinstance(charge, int) else "")
                + "."
            )

        padding = _get(setup, "solvent_padding_nm")
        box = _get(setup, "box_shape")
        concentration = _get(setup, "ion_concentration_M")
        positive = _get(setup, "ion_positive", default="Na+")
        negative = _get(setup, "ion_negative", default="Cl-")
        atoms = _get(setup, "n_atoms_solvated", "solvated_atoms",
                     "n_atoms", "atom_count")

        solvation = []
        if padding is not None:
            solvation.append(
                f"The complex was solvated in a {box or 'cubic'} box with "
                f"{_nm(padding)} of padding"
                + (f", and {positive}/{negative} ions at {concentration} M"
                   if concentration is not None else "")
                + "."
            )
        if atoms:
            solvation.append(f"The solvated system contained {int(atoms):,} atoms.")

        method = _get(setup, "nonbonded_method", default="PME")
        cutoff = _get(setup, "nonbonded_cutoff_nm")
        constraints = _get(setup, "constraints")
        rigid = _get(setup, "rigid_water")
        interactions = []
        if cutoff is not None:
            interactions.append(
                f"Long-range electrostatics were treated with {method} and a "
                f"real-space cutoff of {_nm(cutoff)}."
            )
        if constraints:
            interactions.append(
                f"Bonds were constrained with `{constraints}`"
                + (", and water was held rigid" if rigid else "")
                + "."
            )

        block = " ".join(preparation + parameterisation + solvation + interactions)
        parts.append("**System preparation.** " + block)

    # ---- simulation protocol ------------------------------------------
    if sim:
        timestep = _get(sim, "timestep_fs")
        integrator = _get(sim, "integrator")
        integrator = _INTEGRATOR_NAMES.get(str(integrator).lower(), integrator)
        temperature = _get(sim, "temperature_K")
        friction = _get(sim, "friction_per_ps")
        # What ran, then what was asked for. Unset means one bar, and the
        # runner is the only place that knew.
        pressure = _get(sim, "pressure_bar_used", "pressure_bar")
        barostat_every = _get(sim, "barostat_frequency")
        nvt = _get(sim, "nvt_steps")
        npt = _get(sim, "npt_steps")
        production = _get(sim, "production_steps")
        interval = _get(sim, "trajectory_interval_steps")
        # What ran, not what was asked for: "auto" is a request, and a
        # methods section saying a run used the auto platform says nothing.
        platform = _get(sim, "platform_used")
        if platform in (None, "auto"):
            platform = _get(sim, "platform")
            if platform == "auto":
                platform = None
        precision = _get(sim, "precision")
        seed = _get(sim, "random_seed")

        protocol = []
        if _get(sim, "minimize", default=True):
            protocol.append("The system was energy-minimized before dynamics.")

        # What was held while the solvent settled. A restrained equilibration
        # is part of a protocol, and a reader cannot repeat one that held the
        # protein at a thousand kilojoules and released it in four steps
        # unless the paragraph says so.
        held = _get(sim, "restrain")
        if held:
            release = _get(sim, "restraint_release") or [1000.0, 500.0, 100.0, 0.0]
            steps = ", ".join(f"{float(k):g}" for k in release)
            protocol.append(
                f"Atoms matching `{held}` were harmonically restrained to "
                f"their minimized positions during equilibration, with the "
                f"force constant stepped through {steps} kJ/mol/nm² as "
                "equilibration proceeded."
            )
            if _get(sim, "restrain_production", default=False):
                protocol.append(
                    "**The restraints were retained during production**, so "
                    "the trajectory is biased: measures of flexibility "
                    "computed from it describe the restraint as well as the "
                    "system."
                )
            else:
                protocol.append(
                    "They were released before production, which ran "
                    "unrestrained."
                )
        # A biased production run, and what can be recovered from it.
        #
        # The restraint case above says this well and the three enhanced
        # sampling methods said nothing at all -- the cases where biasing is
        # the entire point of the run. Ten analyses of the trajectory were
        # reported beside the free energy with no distinction between them,
        # and a reader would take a mean RMSD over a metadynamics run as a
        # measurement of the system.
        #
        # The three are not alike, and one caveat covering all of them would
        # be wrong about two. Metadynamics deposits a known bias on a known
        # coordinate, so the unbiased ensemble is recoverable by weighting
        # each frame by exp(V/RT). An umbrella window is a separate biased
        # simulation whose analyses describe a peptide held where it was put;
        # what combines them is the free energy, not an average over windows.
        # A steered pull is not an equilibrium ensemble at all, so no
        # weighting exists.
        for block, paragraph in (
            ("metadynamics",
             "Production was biased by metadynamics, so the trajectory is not "
             "a Boltzmann ensemble: a state the bias filled early is visited "
             "more often than equilibrium would give, and one filled late "
             "less. The free energy surface accounts for this. Trajectory "
             "analyses reported alongside it do not unless they say they "
             "were reweighted, and the weights are recoverable from the "
             "deposited bias."),
            ("umbrella",
             "This run is one window of an umbrella study, held at its own "
             "position on the coordinate by a harmonic restraint. Its "
             "trajectory analyses describe a system held there and are not "
             "measurements of the unrestrained system; they are also not "
             "comparable between windows, which differ because the "
             "restraints differ. What combines the windows is the potential "
             "of mean force, computed from their overlapping distributions "
             "-- not an average of any quantity across them."),
            ("steered",
             "Production was a steered pull, which is not an equilibrium "
             "ensemble: the system was dragged along the coordinate rather "
             "than sampling it. Trajectory analyses describe the pulling, "
             "and no reweighting recovers an equilibrium average from a "
             "single non-equilibrium trajectory. The work is reported as a "
             "pathway rather than a free energy for the same reason."),
        ):
            if _get(sim, block):
                protocol.append(paragraph)

        stages = []
        if nvt:
            stages.append(f"{_steps_to_ns(nvt, timestep)} in the NVT ensemble")
        if npt:
            stages.append(f"{_steps_to_ns(npt, timestep)} in the NPT ensemble")
        if stages:
            protocol.append("Equilibration comprised " + " followed by ".join(stages) + ".")
        if production:
            protocol.append(
                f"Production dynamics were run for "
                f"{_steps_to_ns(production, timestep)} in the NPT ensemble."
            )
        if integrator and timestep:
            protocol.append(
                f"Equations of motion were integrated with the {integrator} "
                f"integrator and a {timestep} fs timestep"
                # Each clause only where the value is there. A methods
                # section reading "a friction coefficient of None" is worse
                # than one that omits it: the reader cannot tell whether the
                # run had no friction or the software lost the number.
                + (f", at {temperature} K" if temperature is not None else "")
                + (f" with a friction coefficient of {friction} ps⁻¹"
                   if friction is not None else "")
                + "."
            )
        if pressure is not None:
            protocol.append(
                f"Pressure was maintained at {pressure} bar"
                + (f", with the barostat applied every {barostat_every} steps"
                   if barostat_every else "")
                + "."
            )
        if interval and timestep:
            protocol.append(
                f"Coordinates were written every "
                f"{_steps_to_ns(interval, timestep)}."
            )
        if platform:
            protocol.append(
                f"Simulations used OpenMM on the {platform} platform"
                + (f" in {precision} precision" if precision else "")
                + "."
            )
        # A seed is what makes a run repeatable, and its absence is what makes
        # one irreproducible -- so either is worth stating.
        protocol.append(
            f"The random seed was {seed}." if seed is not None
            else "No random seed was fixed, so velocities differ between runs."
        )
        parts.append("**Simulation protocol.** " + " ".join(protocol))

    # ---- software -----------------------------------------------------
    if versions:
        # FastMDXplora did the work -- setup, simulation and analysis -- and
        # the libraries it calls are the record of what it stood on. The
        # two are not the same kind of thing and were listed as one.
        ours = versions.get("FastMDXplora")
        tools = {k: v for k, v in versions.items() if k != "FastMDXplora"}
        if ours:
            parts.append(
                "**Software.** System setup, simulation and analysis were "
                f"performed with FastMDXplora {ours}."
            )
        if tools:
            named = ", ".join(f"{name} {version}" for name, version in
                              sorted(tools.items()))
            parts.append(f"**Tools.** FastMDXplora calls {named}.")

    gaps = missing_from_methods(setup, sim)
    if gaps:
        parts.append(
            "**Not recorded.** This run does not supply "
            + "; ".join(gaps)
            + ". A methods section stating a default nobody chose is worse "
            "than one with a visible gap, so these are left for you to fill "
            "in or to note as unrecorded."
        )

    return "\n\n".join(parts)
