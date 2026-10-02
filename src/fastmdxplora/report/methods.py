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


#: How the Methods names each phase in a sentence about its software.
_PHASE_WORDS = {"setup": "system setup", "simulation": "simulation",
                "analysis": "analysis"}


def _made_with_sentence(made_with: list[tuple[str, list[str]]]) -> str:
    """Which FastMDXplora produced which phases, as one sentence.

    A study simulated under one release and analysed under another says so,
    rather than crediting the release that wrote the report with all of it.
    """
    def words(phases: list[str]) -> str:
        named = [_PHASE_WORDS.get(phase, phase) for phase in phases]
        return named[0] if len(named) == 1 else (
            ", ".join(named[:-1]) + " and " + named[-1])

    said = [(version, phases) for version, phases in made_with if phases]
    if not said:
        from fastmdxplora import __version__

        return (f"This report was written with FastMDXplora {__version__}; "
                "the run does not record which version produced it.")
    first_version, first_phases = said[0]
    verb = "was" if len(first_phases) == 1 else "were"
    clauses = [f"{words(first_phases)} {verb} performed with FastMDXplora "
               f"{first_version}"]
    clauses.extend(f"{words(phases)} with FastMDXplora {version}"
                   for version, phases in said[1:])
    if len(clauses) == 1:
        sentence = clauses[0]
    else:
        sentence = ", ".join(clauses[:-1]) + ", and " + clauses[-1]
    return sentence[0].upper() + sentence[1:] + "."


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


#: How each placement reads in a methods section.
_PLACED_BY = {
    "OPM frame": "in the orientation and position published by the OPM database",
    "fitted orientation and centre": (
        "on the membrane normal and centre fitted to its apolar lipid-facing "
        "surface"),
    "structure's own orientation; centre fitted along z": (
        "in its own orientation, at the membrane centre fitted to its apolar "
        "lipid-facing surface"),
    "stated orientation; centre fitted along z": (
        "in the orientation supplied, at the membrane centre fitted to its "
        "apolar lipid-facing surface"),
    "stated orientation and centre": "in the orientation and position supplied",
}


#: The lipid force field each file OpenMM ships carries, by the name its
#: developers publish it under.
_LIPID_FORCE_FIELDS = {
    "amber14-all.xml": "AMBER Lipid17",
    "amber14/lipid17.xml": "AMBER Lipid17",
    "charmm36.xml": "CHARMM36",
}


def _lipid_parameters_clause(source: Any) -> str:
    """Which force field described the lipids, where it was recorded."""
    if not source:
        return ""
    named = _LIPID_FORCE_FIELDS.get(str(source))
    return (f", described by {named} ({source})" if named
            else f", described by {source}")


def _bilayer_sentence(bilayer: dict[str, Any], padding: Any, positive: str,
                      negative: str, concentration: Any) -> str:
    """The protein embedded in its bilayer, as a sentence: how it was placed,
    the fitted hydrophobic thickness, which lipid, how many and per leaflet,
    and the water and ions around it."""
    lipid = bilayer.get("lipid") or "lipid"
    count = bilayer.get("lipids")
    leaflets = bilayer.get("lipids_per_leaflet") or []
    placed = _PLACED_BY.get(str(bilayer.get("placed_by")),
                            str(bilayer.get("placed_by") or "as given"))
    published = bilayer.get("opm_hydrophobic_thickness_nm")
    thickness = bilayer.get("hydrophobic_thickness_nm")
    if bilayer.get("placed_by") == "OPM frame" and published:
        said = f" (hydrophobic thickness {float(published):.1f} nm in OPM)"
    elif thickness:
        said = f" (fitted hydrophobic thickness {float(thickness):.1f} nm)"
    else:
        said = ""
    text = (f"The protein was placed {placed}" + said
            + f" and embedded in a {lipid} bilayer"
            + (f" of {count} lipids ({leaflets[0]} and {leaflets[1]} per leaflet)"
               if count and len(leaflets) == 2 else "")
            + _lipid_parameters_clause(bilayer.get("lipid_parameters"))
            + " built with OpenMM's Modeller, in a rectangular box")
    if padding is not None:
        text += f" with at least {_nm(padding)} of padding"
    if concentration is not None:
        text += f", and {positive}/{negative} ions at {concentration} M"
    return text + "."


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
    # A setting left unset is answered by what the run recorded deciding,
    # under the same name: `resolved` is written in the config's own terms.
    # Without it a study given a duration has no production length here,
    # and so no sentence saying what ensemble production ran in.
    resolved = manifest.get("resolved")
    for name, value in (resolved if isinstance(resolved, dict) else {}).items():
        if flat.get(name) is None:
            flat[name] = value
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
    made_with: list[tuple[str, list[str]]] | None = None,
    tools_recorded: bool = True,
    extended: tuple[float, int] | None = None,
    means: dict[str, dict[str, Any]] | None = None,
    stopping: dict[str, Any] | None = None,
) -> str:
    """The methods text, as prose rather than a list of settings.

    A list of settings is what the software knows; a methods section is what a
    reader needs, and they are not the same document. The list is still
    written elsewhere in the report -- this is the part somebody pastes into a
    manuscript.

    ``extended`` is the production of a study extended in pieces and how
    many (:func:`fastmdxplora.simulation.resume.extended_production`): the
    simulation record is the first piece's, and a methods section reading
    it alone gives that piece's length for the whole study.

    ``means`` are the analyses' mean records by name (``findings.mean`` in
    each ``analysis/<name>/options.json``) and ``stopping`` the record of a
    study run until it knew (``stopping.json``); from them the Analysis
    paragraph says how each mean and error was determined, and why the
    production is as long as it is.
    """
    from fastmdxplora.simulation.ensembles import NPT, recorded_ensemble

    parts: list[str] = []
    versions = versions or {}
    # From the whole record, before it is flattened: the runner's own answer
    # where it gave one, and its resolver over what it recorded where not.
    production_ensemble = recorded_ensemble(sim)
    bilayer = setup.get("bilayer") if isinstance(setup.get("bilayer"), dict) else None
    barostat_kind = sim.get("barostat") if isinstance(sim, dict) else None
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
                "drop": "all non-standard residues were removed",
                "keep": "all non-standard residues were retained",
            }.get(str(heterogens), f"heterogens were handled under the `{heterogens}` policy")
            # Said only where it is true: the record holds a decision per
            # component under `auto`, and none under `drop` or `keep`.
            recorded = (" The decision taken for each, with its reason, is "
                        "recorded in `setup_parameters.json`."
                        if _get(setup, "heterogen_decisions") else "")
            preparation.append(f"Heterogens: {policy}.{recorded}")

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
        if bilayer:
            solvation.append(_bilayer_sentence(bilayer, padding, positive,
                                               negative, concentration))
        elif padding is not None:
            # What the padding measures, since the word does not say: OpenMM
            # sizes a box as the solute's bounding sphere plus the padding,
            # so it is the least distance to the nearest periodic image. A
            # reader used to padding measured to the box's wall would read
            # the box as twice as large as it was.
            resolved = setup.get("resolved") if isinstance(setup.get("resolved"), dict) else {}
            grown = resolved.get("solvent_padding_nm")
            was_grown = isinstance(grown, (int, float)) and grown != padding
            used = grown if was_grown else padding
            solvation.append(
                f"The complex was solvated in a {box or 'cubic'} box sized to "
                f"leave at least {_nm(used)} between the solute and its nearest "
                f"periodic image"
                + (f" (grown from the {_nm(padding)} asked for, so that the box "
                   f"is at least twice the cutoff across)" if was_grown else "")
                + (f", with {positive}/{negative} ions at {concentration} M"
                   if concentration is not None else "")
                + "."
            )
        if atoms:
            solvation.append(f"The solvated system contained {int(atoms):,} atoms.")
        # Read from what the phase used: `random_seed` here is the setting,
        # and it is absent where a seed was drawn.
        seed = _get(setup, "_random_seed")
        if seed is not None:
            # The one thing a reader needs to repeat the preparation rather
            # than make an equivalent one: hydrogens and ions are placed at
            # random, and a bilayer's packing is not seeded at all.
            solvation.append(
                f"Hydrogens and ions were placed with random seed {int(seed)} "
                "(`setup.random_seed`)"
                + (", which reproduces the solvated system except the bilayer's "
                   "packing" if bilayer else ", which reproduces the solvated system")
                + ".")

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

        # What was held while the solvent equilibrated. A restrained equilibration
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
                    "the trajectory is biased: flexibility analyses run "
                    "on it describe the restraint as well as the "
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
             "trajectory analyses describe a system held there and do not "
             "describe the unrestrained system; they are also not "
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
        if production and extended:
            total, pieces = extended
            length = f"{total:.3g} ns" if total >= 1 else f"{total * 1000:.3g} ps"
            protocol.append(
                f"Production dynamics were run for {length} in the "
                f"{production_ensemble.upper()} ensemble, in {pieces} pieces, "
                "each continuing from the checkpoint of the one before."
            )
        elif production:
            protocol.append(
                f"Production dynamics were run for "
                f"{_steps_to_ns(production, timestep)} in the "
                f"{production_ensemble.upper()} ensemble."
            )
        elif production == 0:
            protocol.append("No production was run: the study equilibrated only.")
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
        # The runner records a pressure whatever the ensemble, and a barostat
        # acts only where there is one: through production at constant
        # pressure, or through the NPT stage before constant-volume
        # production. Stated without saying which, it reads as production's.
        barostat = (f", with the barostat applied every {barostat_every} steps"
                    if barostat_every else "")
        if barostat_kind == "membrane":
            # A bilayer's area and thickness move separately; an isotropic
            # barostat would couple them and squeeze it.
            barostat += (" (a membrane barostat: x and y coupled, z "
                         "independent, zero surface tension)")
        if pressure is not None and production_ensemble == NPT and production != 0:
            protocol.append(f"Pressure was maintained at {pressure} bar{barostat}.")
        elif pressure is not None and npt:
            protocol.append(
                f"Pressure was maintained at {pressure} bar during NPT "
                f"equilibration{barostat}.")
        if interval and timestep and production != 0:
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

    # ---- analysis -----------------------------------------------------
    analysis = _analysis_paragraph(means or {}, stopping)
    if analysis:
        parts.append("**Analysis.** " + analysis)

    # ---- software -----------------------------------------------------
    if versions or made_with is not None:
        # FastMDXplora did the work -- setup, simulation and analysis -- and
        # the libraries it calls are the record of what it stood on. The
        # two are not the same kind of thing and were listed as one.
        ours = versions.get("FastMDXplora")
        tools = {k: v for k, v in versions.items() if k != "FastMDXplora"}
        if made_with is not None:
            parts.append("**Software.** " + _made_with_sentence(made_with))
        elif ours:
            parts.append(
                "**Software.** System setup, simulation and analysis were "
                f"performed with FastMDXplora {ours}."
            )
        if tools:
            named = ", ".join(f"{name} {version}" for name, version in
                              sorted(tools.items(), key=lambda kv: kv[0].lower()))
            sentence = f"**Tools.** FastMDXplora calls {named}."
            if not tools_recorded:
                sentence += (
                    " The run did not record its libraries, so these are the "
                    "versions installed where this report was written.")
            parts.append(sentence)

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


def _listed(names: list[str]) -> str:
    names = [f"`{name}`" for name in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _estimator_sentences(means: dict[str, dict[str, Any]]) -> list[str]:
    """How the means and their errors were determined.

    Said from the records rather than from the release writing the report:
    a record carrying its degrees of freedom was written by the estimator
    described here, and one without them by an earlier one, whose details
    differ. The constants are read from the module that applies them, so
    this cannot describe a threshold the estimator no longer uses.
    """
    from fastmdxplora.statistics import (EQUILIBRATION_TOLERANCE,
                                         MINIMUM_EFFECTIVE_SAMPLES, RESOLVED_SAMPLES,
                                         WHOLE_RUN_UNLESS_GAIN)

    names = sorted(means)
    said = ((f"Each per-frame quantity ({_listed(names)})" if len(names) > 1
             else f"The per-frame quantity {_listed(names)}")
            + " was averaged over the frames after an equilibration period detected "
            "automatically")
    if not all("degrees_of_freedom" in record for record in means.values()):
        return [said + " (Chodera, J. Chem. Theory Comput. 2016, 12, 1799), with a "
                "standard error from the statistical inefficiency of the frames kept. "
                "These means were recorded by an earlier release than the estimator "
                "the current documentation describes, whose details differ; analysing "
                "the study again records them by the current one."]
    gain = {2.0: "doubled", 3.0: "tripled"}.get(
        float(WHOLE_RUN_UNLESS_GAIN), f"multiplied by {WHOLE_RUN_UNLESS_GAIN:g}")
    return [
        said + ", as the latest start that keeps at least "
        f"{100 * (1 - EQUILIBRATION_TOLERANCE):g}% of the most statistically independent "
        "samples any start keeps (after Chodera, J. Chem. Theory Comput. 2016, 12, "
        "1799).",
        "Standard errors account for the correlation between frames through the "
        "statistical inefficiency, the number of frames per independent sample, "
        "estimated from the autocorrelation function summed over Geyer's initial "
        "positive sequence (Stat. Sci. 1992, 7, 473) and corrected for the bias of an "
        "autocorrelation taken about the sample mean.",
        "Where frames were discarded, the error is that of the whole run scaled to "
        f"the frames kept, unless the discard at least {gain} the independent samples.",
        "An error is given only where the frames averaged span at least "
        f"{RESOLVED_SAMPLES:g} times their own statistical inefficiency and hold at "
        f"least {MINIMUM_EFFECTIVE_SAMPLES:g} independent samples.",
    ]


def _discard_sentence(means: dict[str, dict[str, Any]]) -> str:
    """How much was discarded, over what."""
    def whole(value: Any) -> int | None:
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    discards = [whole(r.get("discard")) for r in means.values()]
    frames = {whole(r.get("n_frames")) for r in means.values()}
    if None in discards or len(frames) != 1 or None in frames:
        return ""
    n = frames.pop()
    low, high = min(discards), max(discards)
    if high == 0:
        return f"No equilibration period was detected in the {n:,} frames analysed."
    if low == high:
        return (f"The first {low:,} of the {n:,} frames analysed were discarded as "
                "equilibration" + (" in each." if len(discards) > 1 else "."))
    return (f"The equilibration periods discarded ran from {low:,} to {high:,} of the "
            f"{n:,} frames analysed.")


def _stopping_sentences(stopping: dict[str, Any], units: dict[str, str]) -> list[str]:
    """Why the production is as long as it is, for a study run until it knew."""
    from fastmdxplora.simulation.stopping import (AGREE_WITHIN, JUDGED_ALONE, ONE_ERROR,
                                                  StopTarget)

    rounds = [r for r in stopping.get("rounds") or [] if isinstance(r, dict)]
    try:
        targets = [StopTarget(**t) for t in stopping.get("targets") or []]
    except TypeError:
        return []
    if not targets or not rounds:
        return []
    runs = stopping.get("runs") or []
    replicas = len(runs) > 1
    ceiling = stopping.get("max_duration_ns")
    asked = "; ".join(t.said(units.get(t.analysis, "")) for t in targets)
    said = [
        "The production length was not fixed in advance. The study ran in rounds, each "
        "extended by what its analyses said was still needed, until "
        + asked + (" was" if len(targets) == 1 else " were") + " determined"
        + (f" in {len(runs)} replicas that agree within their errors" if replicas else "")
        + (f", with at most {ceiling:g} ns of production" + (" per run" if replicas else "")
           if isinstance(ceiling, (int, float)) and not isinstance(ceiling, bool) else "")
        + ".",
        "Each error was judged widened by Student's t at its own degrees of freedom, so "
        "that it holds the true mean as often as one standard error of a normal mean "
        f"does ({100 * (2 * ONE_ERROR - 1):.1f}% of the time)"
        + ("; the replicas were pooled weighted by their errors, judged on the larger "
           "of their combined error and the spread of their means, and only once those "
           "means agreed within their errors (Cochran's Q over its degrees of freedom "
           f"at most {AGREE_WITHIN:g})." if replicas else
           f"; a single run's error was accepted only once it rested on "
           f"{JUDGED_ALONE:g} independent samples, and one run cannot show a state it "
           "never left."),
    ]
    last = rounds[-1]
    produced = last.get("production_ns")
    at = (f", at {produced:g} ns of production" + (" per run" if replicas else "")
          if isinstance(produced, (int, float)) else "")
    count = f"{len(rounds)} round" + ("s" if len(rounds) != 1 else "")
    outcome = stopping.get("outcome")
    said.append({
        "met": f"It stopped after {count}{at}, with each determined as asked.",
        "ceiling": f"It stopped at the ceiling after {count}{at}, before each was "
                   "determined as asked.",
        "stopped": f"It stopped after {count}{at}, when an extension did not complete.",
    }.get(outcome, f"It was still running when this was written, after {count}{at}."))
    return said


def _analysis_paragraph(means: dict[str, dict[str, Any]],
                        stopping: dict[str, Any] | None) -> str:
    """How each mean and its error were determined, and, for a study run
    until it knew, why it ran as long as it did. Empty where nothing
    recorded a mean and the study ran to a fixed length."""
    means = {name: record for name, record in means.items()
             if isinstance(record, dict) and record.get("mean") is not None}
    said: list[str] = []
    if means:
        said += _estimator_sentences(means)
        discard = _discard_sentence(means)
        if discard:
            said.append(discard)
        shared = sorted({record["start_shared_with_replicas"] for record in means.values()
                         if isinstance(record.get("start_shared_with_replicas"), int)})
        if shared:
            said.append(
                "As one of a set of replicas started from one structure, "
                + ("each quantity was" if len(means) > 1 else "it was")
                + " averaged from no earlier than frame "
                + " or ".join(f"{frame:,}" for frame in shared)
                + ", the start detected on the replicas' frame-by-frame average, where the "
                "relaxation they share shows through less noise than in one run.")
        withheld = sorted(name for name, record in means.items()
                          if record.get("not_a_measurement"))
        if withheld:
            said.append(f"No error is given for {_listed(withheld)}; the report says why"
                        + (" for each." if len(withheld) > 1 else "."))
    if isinstance(stopping, dict):
        units = {name: str(record.get("unit") or "") for name, record in means.items()}
        said += _stopping_sentences(stopping, units)
    return " ".join(said)
