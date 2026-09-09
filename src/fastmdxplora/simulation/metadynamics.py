"""Metadynamics without writing PLUMED input.

PLUMED can express almost any enhanced-sampling scheme, and the cost of that
is a language to learn before running the commonest one. Most metadynamics on
a protein-ligand system biases one of a handful of things: how far the ligand
has moved from its pose, how far apart two groups are, a torsion, or how
compact the protein is. Those do not need a language.

So this generates the input for them, and the PLUMED integration that already
exists runs it. Anything more elaborate is still written by hand and passed
through as before -- this is a shorter path to the common case, not a
replacement for the general one.

**What a collective variable commits you to.** Metadynamics fills the free
energy landscape along whatever you bias, and reports a free energy as a
function of it. If the variable does not distinguish the states that matter --
if two genuinely different arrangements have the same value -- the surface
converges and describes something that is not the system. This is the failure
mode of the method, it does not announce itself, and no amount of running
longer fixes it. Each variable below says what it separates and what it does
not.

**And a run that has not converged has no free energy.** The bias is still
growing, so the surface is still moving, and reading a barrier off it is
reading the current state of the filling rather than the landscape. What can
be checked -- whether the hills have stopped growing, whether the run has
revisited the states it left -- is reported rather than assumed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

__all__ = [
    "COLLECTIVE_VARIABLES",
    "MetadynamicsPlan",
    "MetadynamicsPair",
    "plan_pair_from_config",
    "build_plumed_script_pair",
    "Walls",
    "Funnel",
    "build_plumed_script",
    "plan_from_config",
]


#: What each variable separates, and what it does not. Written out because
#: choosing one is the decision the method turns on, and a name alone does not
#: carry it.
COLLECTIVE_VARIABLES: dict[str, str] = {
    "ligand_rmsd": (
        "how far the ligand has moved from its starting pose. Separates bound "
        "from unbound and one pose from another. Does not separate two "
        "unbound arrangements at the same distance, so the unbound basin is "
        "one broad well rather than the many states it really is."
    ),
    "ligand_distance": (
        "the distance from the ligand's centre to the binding site's. "
        "Separates depth of binding along one direction. Does not distinguish "
        "leaving by one route from leaving by another, which matters when the "
        "question is how a ligand escapes rather than how tightly it is held."
    ),
    "torsion": (
        "a single dihedral. Separates rotameric states cleanly. Does not "
        "separate anything coupled to that bond, so it suits a question about "
        "one torsion and misleads about a conformational change involving "
        "several."
    ),
    "radius_of_gyration": (
        "how compact the protein is. Separates folded from extended. Does not "
        "separate a correctly folded structure from a compact wrong one, "
        "which is the classic way a folding free energy surface comes out "
        "converged and wrong."
    ),
    "coordination": (
        "how many contacts there are between two groups, counted through a "
        "switching function rather than a hard cutoff. Separates bound from "
        "unbound more robustly than a distance does, because it does not "
        "break when a ligand rotates or when one contact is exchanged for "
        "another. Does not separate one close contact from several distant "
        "ones -- a single atom at 3 Angstroms and three at 5 can give the "
        "same number, which is the price of the robustness."
    ),
    "membrane_depth": (
        "how far a molecule sits from the middle of a bilayer, along the "
        "membrane normal. The coordinate for a permeation free energy, for "
        "where a drug partitions, and for how deeply a peptide inserts. Does "
        "not separate the two leaflets -- a molecule two nanometres above "
        "the centre and one two below have the same depth unless the sign is "
        "kept, and it does not distinguish sitting among the headgroups from "
        "passing through them."
    ),
    "angle": (
        "the angle at three atoms or three groups. Separates a hinge opening "
        "from a hinge closed, and an orientation from its opposite. Does not "
        "separate the two ways of reaching the same angle, since an angle "
        "has no sign -- a torsion does, and is the right variable where "
        "which way round matters."
    ),
    "q": (
        "the fraction of the reference structure's native contacts that are "
        "still formed, in the sense of Best, Hummer & Eaton (PNAS 2013). The "
        "standard folding coordinate: it separates folded from unfolded "
        "through hundreds of contacts at once rather than one distance, so "
        "it does not mistake a compact wrong structure for the native one "
        "the way a radius of gyration does. Does not separate two structures "
        "that keep the same contacts in different arrangements, and says "
        "nothing about anything the reference does not contain -- a contact "
        "formed only in the unfolded state is invisible to it, because S is "
        "fixed by the reference and never grows."
    ),
    "distance": (
        "the distance between two atom selections, by their centres. The "
        "general case of the ligand distance above. Does not separate two "
        "arrangements that happen to put the centres the same distance "
        "apart, which a pair of large groups often will."
    ),
}

#: Well-tempered metadynamics by default. Plain metadynamics keeps depositing
#: at full height forever, so the bias never settles and the surface never
#: converges; well-tempered shrinks the hills as a region fills, which is what
#: makes the free energy recoverable at all.
DEFAULT_BIAS_FACTOR = 10.0

#: How often to deposit, in steps. Too frequent and the hills correlate and
#: the error is underestimated; too rare and it takes forever.
DEFAULT_PACE_STEPS = 500

#: Hill height in kJ/mol. The convention is a value small compared with the
#: barriers being crossed.
DEFAULT_HEIGHT_KJMOL = 1.2


@dataclass(frozen=True)
class Walls:
    """Limits beyond which the run is pushed back.

    A metadynamics run on a ligand's distance from its site will, given time,
    push the ligand out into bulk solvent -- where the landscape is flat and
    unbounded, so the bias fills a basin that is effectively infinite and the
    run never returns to the question. An upper wall stops that: past it the
    ligand is pushed back, the unbound basin has a finite volume, and the
    binding free energy computed from the surface means something.

    Without a wall, a ligand-distance run is not wrong so much as unfinishable.
    """

    upper: float | None = None
    lower: float | None = None
    #: How hard the wall pushes, in kJ/mol per unit of the variable squared.
    kappa: float = 1000.0

    def as_record(self) -> dict[str, Any]:
        return {"upper": self.upper, "lower": self.lower, "kappa": self.kappa}


@dataclass(frozen=True)
class Funnel:
    """A cone over the binding site widening into a cylinder in the solvent.

    A flat upper wall bounds how far a ligand goes and not where it goes, so
    the run still explores a whole shell of unbound positions at that
    distance. A funnel bounds both: near the site it is narrow, following the
    exit path, and further out it opens into a cylinder of fixed radius. The
    unbound state is then a well-defined volume, which is what makes the
    absolute binding free energy recoverable -- and it is what funnel
    metadynamics is for.

    The axis has to be given: it is the direction the ligand leaves by, from
    the site out into solvent, and nothing here can work that out. A funnel
    pointed the wrong way blocks the exit instead of following it.

    Parameters follow the usual convention -- the cone half-angle, the
    distance at which the cone becomes a cylinder, and the cylinder's radius.
    """

    axis_selection: str
    alpha_rad: float = 0.55
    switch_distance_nm: float = 1.5
    cylinder_radius_nm: float = 0.1
    kappa: float = 15000.0

    def as_record(self) -> dict[str, Any]:
        return {
            "axis_selection": self.axis_selection,
            "alpha_rad": self.alpha_rad,
            "switch_distance_nm": self.switch_distance_nm,
            "cylinder_radius_nm": self.cylinder_radius_nm,
            "kappa": self.kappa,
        }


@dataclass(frozen=True)
class MetadynamicsPlan:
    """A metadynamics run, described in terms of what it biases."""

    collective_variable: str
    #: Atom selections the variable needs, already resolved to indices.
    atoms: dict[str, list[int]]
    sigma: float
    height_kjmol: float = DEFAULT_HEIGHT_KJMOL
    pace_steps: int = DEFAULT_PACE_STEPS
    bias_factor: float = DEFAULT_BIAS_FACTOR
    temperature_K: float = 300.0
    walls: Walls | None = None
    funnel: Funnel | None = None
    #: Where a contact stops counting, in nm, for `coordination`. The
    #: switching function is smooth rather than a step, so this is the
    #: half-way point rather than a cutoff. 0.3 nm counts a hydrogen bond or
    #: a close contact; 0.5 counts a coordination shell.
    coordination_r0: float = 0.3
    #: Q's contact criteria, carried so the record says what was biased.
    #: These are the paper's values; `q_lambda` is how far a contact may
    #: stretch, as a multiple of the distance it had natively, before it
    #: stops counting.
    q_cutoff: float = 0.45
    q_beta: float = 50.0
    q_lambda: float = 1.8
    q_min_seq_separation: int = 4

    def as_record(self) -> dict[str, Any]:
        return {
            "collective_variable": self.collective_variable,
            "what_it_separates": COLLECTIVE_VARIABLES.get(
                self.collective_variable, ""),
            "sigma": self.sigma,
            "height_kjmol": self.height_kjmol,
            "pace_steps": self.pace_steps,
            "bias_factor": self.bias_factor,
            "well_tempered": self.bias_factor > 1.0,
            "n_atoms_biased": {k: len(v) for k, v in self.atoms.items()},
            **({"q_criteria": {
                "cutoff_nm": self.q_cutoff,
                "beta_per_nm": self.q_beta,
                "lambda": self.q_lambda,
                "min_seq_separation": self.q_min_seq_separation,
                "contact_count": "stated in the generated PLUMED input, "
                                 "which is kept beside the run",
            }} if self.collective_variable == "q" else {}),
            "walls": self.walls.as_record() if self.walls else None,
            "funnel": self.funnel.as_record() if self.funnel else None,
        }


def _plumed_list(indices: list[int]) -> str:
    """Atom indices as PLUMED writes them.

    PLUMED numbers atoms from one; everything else here numbers from zero.
    Getting this wrong biases the atom next door, which is a mistake that
    produces a plausible surface for the wrong coordinate.
    """
    return ",".join(str(int(i) + 1) for i in indices)


#: Everything a prepared system contains that is not a candidate ligand:
#: the standard residues, the solvent, the ions and the lipids.
_NOT_A_LIGAND = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "HID", "HIE", "HIP", "CYX", "ASH", "GLH", "LYN", "ACE", "NME", "NMA",
    "A", "C", "G", "U", "T", "DA", "DC", "DG", "DT",
    "HOH", "WAT", "TIP", "TIP3", "SOL", "H2O",
    "NA", "CL", "K", "MG", "CA", "ZN", "FE", "MN",
    "POP", "POPC", "POPE", "DLPC", "DLPE", "DMPC", "DOPC", "DPPC",
})


def detect_ligand(topology: Any) -> str | None:
    """The ligand's residue name, worked out from the system.

    A prepared system contains the protein, the solvent, the ions, possibly
    lipids, and the thing that was parameterised as a ligand. Where exactly
    one residue is none of the former, it is the latter -- and that is a
    deduction rather than a guess.

    Returns ``None`` where there is no candidate or more than one, because
    "which of these two is the ligand" is a question the topology cannot
    answer and the caller should.
    """
    import mdtraj as md

    mdtop = (topology if isinstance(topology, md.Topology)
             else md.Topology.from_openmm(topology))
    candidates = {r.name.upper() for r in mdtop.residues
                  if r.name.upper() not in _NOT_A_LIGAND}
    return next(iter(candidates)) if len(candidates) == 1 else None


#: What names a collective variable and the atoms it is measured on. Shared:
#: metadynamics, steering and umbrella sampling all resolve a variable the
#: same way, so a study biasing a ligand's depth in a bilayer names the
#: bilayer the same way whichever method is doing the biasing.
COLLECTIVE_VARIABLE_KEYS: frozenset[str] = frozenset({
    "collective_variable", "selection", "site_selection", "bilayer_selection",
    "axis_selection", "ligand_resname", "switch_distance_nm",
    # The setup block's word for the same string. Accepted by the reader
    # since both spellings became one concept, and it has to be accepted
    # here too -- a validator that refuses what the reader understands is
    # the same defect as a reader that refuses what the user meant, and it
    # is the one the user meets first.
    "ligand_name",
    # A distance and a coordination are between two groups, so they are named
    # in pairs. Read in a loop rather than one at a time, which is how they
    # went missing from the first version of this list.
    "selection_a", "selection_b",
})

#: What belongs to metadynamics itself: the shape of the hills and the bounds
#: on where the bias may go.
METADYNAMICS_KEYS: frozenset[str] = COLLECTIVE_VARIABLE_KEYS | frozenset({
    "sigma", "height_kjmol", "pace_steps", "bias_factor",
    "walls", "funnel", "lower", "upper", "kappa", "alpha_rad",
    "cylinder_radius_nm", "unbounded",
})


def plan_from_config(
    spec: dict[str, Any],
    topology: Any,
    *,
    temperature_K: float = 300.0,
    ligand_resname: str | None = None,
) -> MetadynamicsPlan:
    """Read a metadynamics block and resolve its selections to atoms."""
    import mdtraj as md

    variable = str(spec.get("collective_variable", "")).lower()
    if variable not in COLLECTIVE_VARIABLES:
        raise ValueError(
            f"Unknown collective variable {variable!r}. Available: "
            + ", ".join(sorted(COLLECTIVE_VARIABLES))
            + ". Anything else can be biased by writing PLUMED input directly "
            "and passing it as `plumed`."
        )

    mdtop = (topology if isinstance(topology, md.Topology)
             else md.Topology.from_openmm(topology))

    def select(expression: str, label: str) -> list[int]:
        found = [int(i) for i in mdtop.select(expression)]
        if not found:
            raise ValueError(
                f"The {label} selection {expression!r} matched no atoms, so "
                "there is nothing to bias."
            )
        return found

    atoms: dict[str, list[int]] = {}
    if variable in ("ligand_rmsd", "ligand_distance"):
        # `ligand_name` is what the setup block calls this, and a user who
        # named the ligand once should not have to learn a second word for
        # it to bias the thing they named. Both are accepted everywhere.
        resname = (spec.get("ligand_resname") or spec.get("ligand_name")
                   or ligand_resname or detect_ligand(topology))
        if not resname:
            raise ValueError(
                f"{variable} needs a ligand. None was given, and the system "
                "contains either no residue that could be one or more than "
                "one -- which is a question the topology cannot answer. Give "
                "`ligand_resname`."
            )
        atoms["ligand"] = select(f"resname {resname}", "ligand")
        if variable == "ligand_distance":
            site = spec.get("site_selection")
            if not site:
                raise ValueError(
                    "ligand_distance measures from the ligand to a site, and "
                    "`site_selection` says where the site is -- the pocket "
                    "residues, usually. Without it there is no second point."
                )
            atoms["site"] = select(str(site), "site")
    elif variable == "distance":
        for name in ("selection_a", "selection_b"):
            expression = spec.get(name)
            if not expression:
                raise ValueError(f"`distance` needs `{name}`.")
            atoms[name] = select(str(expression), name)
    elif variable == "coordination":
        for name in ("selection_a", "selection_b"):
            expression = spec.get(name)
            if not expression:
                raise ValueError(
                    f"`coordination` counts contacts between two groups and "
                    f"needs `{name}`."
                )
            atoms[name] = select(str(expression), name)
    elif variable == "membrane_depth":
        molecule = spec.get("selection") or (
            f"resname {spec.get('ligand_resname') or ligand_resname}"
            if (spec.get("ligand_resname") or ligand_resname) else None)
        if not molecule:
            raise ValueError(
                "`membrane_depth` needs a `selection` for the molecule whose "
                "depth is measured, or a ligand for it to default to."
            )
        atoms["molecule"] = select(str(molecule), "molecule")
        # The bilayer centre is the reference, and it moves: a membrane
        # drifts in the box over a long run, so depth measured against a
        # fixed plane slowly becomes depth against nothing.
        bilayer = spec.get("bilayer_selection") or "resname POP POPC POPE DOPC DPPC DMPC DLPC DLPE"
        atoms["bilayer"] = select(str(bilayer), "bilayer")
    elif variable == "angle":
        expression = spec.get("selection")
        if not expression:
            raise ValueError(
                "`angle` needs a `selection` matching exactly three atoms, "
                "which are the angle."
            )
        found = select(str(expression), "angle")
        if len(found) != 3:
            raise ValueError(
                f"An angle is three atoms; {expression!r} matched {len(found)}."
            )
        atoms["angle"] = found
    elif variable == "torsion":
        expression = spec.get("selection")
        if not expression:
            raise ValueError(
                "`torsion` needs a `selection` matching exactly four atoms, "
                "which are the dihedral."
            )
        found = select(str(expression), "torsion")
        if len(found) != 4:
            raise ValueError(
                f"A torsion is four atoms; {expression!r} matched {len(found)}."
            )
        atoms["torsion"] = found
    else:  # radius_of_gyration
        atoms["group"] = select(
            str(spec.get("selection", "protein and name CA")), "group")

    sigma = spec.get("sigma")
    if sigma is None:
        raise ValueError(
            "Metadynamics needs a `sigma`: the width of the hills, in the "
            "units of the variable being biased. It should be roughly the "
            "size of the fluctuations in that variable within a single state "
            "-- around 0.05 nm for a distance or an RMSD, around 0.35 rad for "
            "a torsion. There is no default that is right for an arbitrary "
            "coordinate, and a wrong one either smears the surface flat or "
            "takes forever to fill."
        )

    walls = None
    wall_spec = spec.get("walls")
    if wall_spec:
        walls = Walls(
            upper=(None if wall_spec.get("upper") is None
                   else float(wall_spec["upper"])),
            lower=(None if wall_spec.get("lower") is None
                   else float(wall_spec["lower"])),
            kappa=float(wall_spec.get("kappa", 1000.0)),
        )
        if walls.upper is None and walls.lower is None:
            raise ValueError(
                "A `walls` block needs an `upper`, a `lower`, or both. One "
                "with neither is a wall nowhere."
            )

    funnel = None
    funnel_spec = spec.get("funnel")
    # `is not None`, because an empty block is falsy and would fall straight
    # through to the unbounded check -- answering "you gave no funnel" to
    # somebody who gave one without an axis.
    if funnel_spec is not None:
        if variable != "ligand_distance":
            raise ValueError(
                "A funnel bounds where a ligand goes as it leaves, so it "
                f"applies to ligand_distance and not to {variable}."
            )
        axis = funnel_spec.get("axis_selection")
        if not axis:
            raise ValueError(
                "A funnel needs `axis_selection`: the direction the ligand "
                "leaves by, given as atoms out towards solvent from the site. "
                "Nothing here can work that out, and a funnel pointed the "
                "wrong way blocks the exit instead of following it."
            )
        atoms["funnel_axis"] = select(str(axis), "funnel axis")
        funnel = Funnel(
            axis_selection=str(axis),
            alpha_rad=float(funnel_spec.get("alpha_rad", 0.55)),
            switch_distance_nm=float(
                funnel_spec.get("switch_distance_nm", 1.5)),
            cylinder_radius_nm=float(
                funnel_spec.get("cylinder_radius_nm", 0.1)),
            kappa=float(funnel_spec.get("kappa", 15000.0)),
        )

    # A statement, not a conditional expression: `raise X if cond else None`
    # raises None when the condition is false, which is a TypeError rather
    # than the run proceeding.
    if (variable in ("ligand_distance", "ligand_rmsd")
            and not (walls or funnel)
            and not spec.get("unbounded")):
        raise ValueError(
            f"{variable} without a wall or a funnel will push the ligand out "
            "into bulk solvent, where the landscape is flat and unbounded: "
            "the bias fills a basin that is effectively infinite and the run "
            "never comes back to the question. Give `walls: {upper: <nm>}` to "
            "bound how far it goes, or a `funnel` to bound where it goes as "
            "well -- the second is what makes an absolute binding free energy "
            "recoverable. To proceed without one anyway, say "
            "`unbounded: true`."
        )

    if variable == "q":
        atoms["group"] = select(str(spec.get("selection", "protein")), "Q")

    return MetadynamicsPlan(
        collective_variable=variable,
        q_cutoff=float(spec.get("q_cutoff", 0.45)),
        q_beta=float(spec.get("q_beta", 50.0)),
        q_lambda=float(spec.get("q_lambda", 1.8)),
        q_min_seq_separation=int(spec.get("q_min_seq_separation", 4)),
        walls=walls,
        funnel=funnel,
        atoms=atoms,
        sigma=float(sigma),
        height_kjmol=float(spec.get("height_kjmol", DEFAULT_HEIGHT_KJMOL)),
        pace_steps=int(spec.get("pace_steps", DEFAULT_PACE_STEPS)),
        bias_factor=float(spec.get("bias_factor", DEFAULT_BIAS_FACTOR)),
        temperature_K=float(temperature_K),
        coordination_r0=float(spec.get("coordination_r0", 0.3)),
    )


def _q_contact_lines(plan: "MetadynamicsPlan",
                     reference_pdb: str,
                     *, label: str = "cv") -> list[str]:
    """PLUMED for Q, over the same contacts the analysis measures.

    The contact set comes from `analysis.qvalue.native_contact_pairs`, which
    is also what the Q analysis uses. Biasing one set of contacts while
    reporting another would produce a free energy along a coordinate no
    reported number corresponds to, and the disagreement would read as a
    sampling problem rather than a definition problem.

    PLUMED implements this switching function natively as `Q`, citing the
    same paper. Its `R_0` is not a contact distance here: for the Q form it
    only sets where the function is guaranteed to reach one, and PLUMED's
    own documentation says to leave it small, so the value below is a
    tenth of an Angstrom rather than anything physical. `WEIGHT` is 1/|S|
    on every contact, which is what makes the SUM a fraction rather than a
    count.
    """
    import mdtraj as md

    from fastmdxplora.analysis.qvalue import native_contact_pairs

    reference = md.load(reference_pdb)
    pairs, r0 = native_contact_pairs(
        reference,
        ref=0,
        cutoff=plan.q_cutoff,
        min_seq_separation=plan.q_min_seq_separation,
        atom_indices=plan.atoms.get("group"),
    )
    if len(pairs) == 0:
        raise ValueError(
            f"The reference structure {reference_pdb} has no native contacts "
            f"under the criteria in force (cutoff {plan.q_cutoff} nm, "
            f"sequence separation {plan.q_min_seq_separation}), so Q would "
            "be a fraction of nothing. An extended or unfolded reference "
            "does this, and so does a selection that matched one region."
        )

    weight = 1.0 / len(pairs)
    lines = [
        f"# Q over {len(pairs)} native contacts taken from {reference_pdb}.",
        "# Best, Hummer & Eaton, PNAS 2013, 110, 17874.",
        f"{label}: CONTACTMAP ...",
    ]
    for n, ((i, j), d0) in enumerate(zip(pairs, r0), start=1):
        # PLUMED counts atoms from one; mdtraj counts from zero.
        lines.append(
            f"  ATOMS{n}={int(i) + 1},{int(j) + 1} "
            f"SWITCH{n}={{Q R_0=0.01 BETA={plan.q_beta:g} "
            f"LAMBDA={plan.q_lambda:g} REF={d0:.5f}}} "
            f"WEIGHT{n}={weight:.8g}"
        )
    lines.append("  SUM")
    lines.append("...")
    return lines


def cv_lines(plan: "MetadynamicsPlan",
             reference_pdb: str | None = None,
             *, suffix: str = "") -> list[str]:
    """The PLUMED that defines ``cv`` for a plan's collective variable.

    One function, because steered MD needs the same translation and had a
    copy of it -- so a variable added to one arrived in that one only, and
    membrane_depth reached metadynamics while steering fell through to a
    branch expecting a radius of gyration. Both call this now, and a new
    variable reaches every method that biases a coordinate.
    """
    variable = plan.collective_variable
    lines: list[str] = []

    # Two variables biased in one run each need their own labels, or the
    # second definition of `lig` silently replaces the first and PLUMED
    # biases one coordinate twice. The default suffix is empty, so a
    # single-variable script is unchanged.
    def label(name: str) -> str:
        return f"{name}{suffix}"

    cv = label("cv")
    if variable == "ligand_rmsd":
        if not reference_pdb:
            raise ValueError(
                "ligand_rmsd is measured against a reference structure, and "
                "none was given."
            )
        lines.append(f"{cv}: RMSD REFERENCE={reference_pdb} TYPE=OPTIMAL")
    elif variable == "ligand_distance":
        lines.append(f"{label('lig')}: COM "
                     f"ATOMS={_plumed_list(plan.atoms['ligand'])}")
        lines.append(f"{label('site')}: COM "
                     f"ATOMS={_plumed_list(plan.atoms['site'])}")
        lines.append(f"{cv}: DISTANCE "
                     f"ATOMS={label('lig')},{label('site')}")
    elif variable == "distance":
        lines.append(f"{label('a')}: COM "
                     f"ATOMS={_plumed_list(plan.atoms['selection_a'])}")
        lines.append(f"{label('b')}: COM "
                     f"ATOMS={_plumed_list(plan.atoms['selection_b'])}")
        lines.append(f"{cv}: DISTANCE ATOMS={label('a')},{label('b')}")
    elif variable == "coordination":
        lines.append(f"{label('ga')}: GROUP "
                     f"ATOMS={_plumed_list(plan.atoms['selection_a'])}")
        lines.append(f"{label('gb')}: GROUP "
                     f"ATOMS={_plumed_list(plan.atoms['selection_b'])}")
        # A switching function rather than a hard cutoff: a step function has
        # an infinite derivative at the cutoff, and a bias needs a force.
        lines.append(
            f"{cv}: COORDINATION GROUPA={label('ga')} "
            f"GROUPB={label('gb')} "
            f"R_0={plan.coordination_r0:g} NN=6 MM=12")
    elif variable == "membrane_depth":
        lines.append(f"{label('mol')}: COM "
                     f"ATOMS={_plumed_list(plan.atoms['molecule'])}")
        # Against the bilayer's own centre, which moves with it.
        lines.append(f"{label('mem')}: COM "
                     f"ATOMS={_plumed_list(plan.atoms['bilayer'])}")
        lines.append(f"{label('sep')}: DISTANCE "
                     f"ATOMS={label('mem')},{label('mol')} COMPONENTS")
        lines.append(f"{cv}: CUSTOM ARG={label('sep')}.z "
                     "FUNC=z VAR=z PERIODIC=NO")
    elif variable == "q":
        if not reference_pdb:
            raise ValueError(
                "q is the fraction of a reference structure's native "
                "contacts, and no reference structure was given. S is fixed "
                "by that structure, so without one there is no set of "
                "contacts and nothing to bias."
            )
        lines.extend(_q_contact_lines(plan, reference_pdb, label=cv))
    elif variable == "angle":
        lines.append(f"{cv}: ANGLE "
                     f"ATOMS={_plumed_list(plan.atoms['angle'])}")
    elif variable == "torsion":
        lines.append(f"{cv}: TORSION "
                     f"ATOMS={_plumed_list(plan.atoms['torsion'])}")
    else:
        lines.append(f"{cv}: GYRATION "
                     f"ATOMS={_plumed_list(plan.atoms['group'])}")

    return lines


#: The collective variables whose bounds are the variable's own geometry
#: rather than a guess, as ``(GRID_MIN, GRID_MAX, width)``. A PLUMED
#: ``TORSION`` is periodic on [-pi, pi] and cannot leave it.
PERIODIC_RANGES: dict[str, tuple[str, str, float]] = {
    "torsion": ("-pi", "pi", 2.0 * math.pi),
}

#: Grid points per sigma. PLUMED's own guidance is a spacing no coarser than
#: half the smallest sigma; five is comfortably inside that and still cheap --
#: a 2-CV torsion grid at sigma 0.35 is 90x90 doubles.
GRID_POINTS_PER_SIGMA = 5


def _grid_keywords(biased: "list[tuple[str, float]]") -> str:
    """``GRID_*`` for a METAD line, when every biased variable has real bounds.

    Without a grid PLUMED evaluates the bias by summing over every hill
    deposited so far, at every step, so the cost per step grows with the
    number of hills and a long run slows without bound. Measured on the
    2-CV torsion run this was found on -- ``PACE=500`` with a 2 fs step,
    so 1,000 hills per nanosecond -- the instantaneous rate fell from 312
    ns/day at 2,000 hills to 272 at 8,000, and ``1/rate = 2.95e-3 +
    9.98e-8 * n_hills`` (R^2 = 0.93) puts 100 ns at about 19 hours where
    the same run on a grid holds its opening rate and takes about 7. The
    displayed estimate does not show this: OpenMM extrapolates the
    cumulative average speed as though it were constant, so it reads low
    all the way and slides upward for the length of the run.

    Only where the bounds are the variable's own geometry, which is why
    this is a table and not a heuristic. A distance, a ligand RMSD or a
    radius of gyration has no ceiling the setup can know, and PLUMED stops
    the run when a variable steps outside its grid -- so an assumed
    ceiling would trade a slow run for one that dies partway through, at
    an unpredictable point, having written a partial HILLS. Walls do not
    make a variable bounded either: they are restraints, and a soft one is
    crossed.

    Returns the keywords with a leading space, or an empty string, so the
    caller can concatenate unconditionally.
    """
    if not biased or not all(name in PERIODIC_RANGES for name, _ in biased):
        return ""
    mins, maxes, bins = [], [], []
    for name, sigma in biased:
        low, high, width = PERIODIC_RANGES[name]
        mins.append(low)
        maxes.append(high)
        # Bins, not spacing: PLUMED derives one from the other, and stating
        # the count keeps the script readable and the test arithmetic exact.
        bins.append(str(max(
            1, math.ceil(width / (float(sigma) / GRID_POINTS_PER_SIGMA)))))
    return (f" GRID_MIN={','.join(mins)} GRID_MAX={','.join(maxes)} "
            f"GRID_BIN={','.join(bins)}")


def build_plumed_script(plan: MetadynamicsPlan, reference_pdb: str | None = None) -> str:
    """The PLUMED input for a plan.

    Written out rather than hidden, because it is the thing that decides what
    the run measures, and somebody checking a result should be able to read
    what was biased without reading this module.
    """
    lines = [
        "# Generated by FastMDXplora from a named collective variable.",
        f"# Biasing: {plan.collective_variable} -- "
        f"{COLLECTIVE_VARIABLES[plan.collective_variable]}",
        "",
    ]

    lines.extend(cv_lines(plan, reference_pdb))
    lines.append("")
    lines.append(
        "metad: METAD ARG=cv "
        f"SIGMA={plan.sigma:g} "
        f"HEIGHT={plan.height_kjmol:g} "
        f"PACE={plan.pace_steps:d} "
        f"BIASFACTOR={plan.bias_factor:g} "
        f"TEMP={plan.temperature_K:g} "
        "FILE=HILLS"
        + _grid_keywords([(plan.collective_variable, plan.sigma)])
    )
    if plan.walls:
        lines.append("")
        if plan.walls.upper is not None:
            lines.append(
                f"uwall: UPPER_WALLS ARG=cv AT={plan.walls.upper:g} "
                f"KAPPA={plan.walls.kappa:g}")
        if plan.walls.lower is not None:
            lines.append(
                f"lwall: LOWER_WALLS ARG=cv AT={plan.walls.lower:g} "
                f"KAPPA={plan.walls.kappa:g}")

    if plan.funnel:
        lines.append("")
        lines.append("# Funnel: a cone over the site opening into a cylinder.")
        lines.append("# The ligand's position is split into how far it has")
        lines.append("# gone along the exit axis and how far it is from that")
        lines.append("# axis; the second is bounded by a radius that depends")
        lines.append("# on the first, which is the funnel shape.")
        lines.append(
            f"axpt: COM ATOMS={_plumed_list(plan.atoms['funnel_axis'])}")
        lines.append("axis: DISTANCE ATOMS=site,axpt COMPONENTS")
        lines.append("rel: DISTANCE ATOMS=site,lig COMPONENTS")
        lines.append(
            "proj: CUSTOM ARG=rel.x,rel.y,rel.z,axis.x,axis.y,axis.z "
            "FUNC=(x*a+y*b+z*c)/sqrt(a*a+b*b+c*c) "
            "VAR=x,y,z,a,b,c PERIODIC=NO")
        lines.append(
            "rad: CUSTOM ARG=rel.x,rel.y,rel.z,proj "
            "FUNC=sqrt(max(0,x*x+y*y+z*z-p*p)) VAR=x,y,z,p PERIODIC=NO")
        lines.append(
            f"limit: CUSTOM ARG=proj FUNC=max("
            f"{plan.funnel.cylinder_radius_nm:g},"
            f"{plan.funnel.cylinder_radius_nm:g}+"
            f"({plan.funnel.switch_distance_nm:g}-p)*"
            f"tan({plan.funnel.alpha_rad:g})) VAR=p PERIODIC=NO")
        lines.append("outside: CUSTOM ARG=rad,limit FUNC=r-l VAR=r,l PERIODIC=NO")
        lines.append(
            f"funnel: UPPER_WALLS ARG=outside AT=0 "
            f"KAPPA={plan.funnel.kappa:g}")

    lines.append("")
    # The variable and the bias, every deposition. Without these there is no
    # way to tell afterwards whether the run converged, and a metadynamics run
    # whose convergence cannot be checked has not measured a free energy.
    lines.append(
        f"PRINT ARG=cv,metad.bias STRIDE={plan.pace_steps:d} FILE=COLVAR")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class MetadynamicsPair:
    """Two collective variables biased by one deposition.

    Held as two ordinary plans rather than as a plan that grew a second of
    everything. Each variable keeps its own selections, its own sigma and
    its own walls, and the translation to PLUMED is the same function
    called twice with different labels, so a variable added for the
    one-dimensional case arrives here having done nothing.

    The hills are shared: one METAD over both arguments, which is what
    makes the surface two-dimensional rather than two surfaces.
    """

    first: MetadynamicsPlan
    second: MetadynamicsPlan

    @property
    def plans(self) -> tuple[MetadynamicsPlan, MetadynamicsPlan]:
        return (self.first, self.second)

    @property
    def collective_variables(self) -> tuple[str, str]:
        return (self.first.collective_variable,
                self.second.collective_variable)

    def as_record(self) -> dict[str, Any]:
        return {
            "collective_variables": list(self.collective_variables),
            "dimensions": [plan.as_record() for plan in self.plans],
            "height_kjmol": self.first.height_kjmol,
            "pace_steps": self.first.pace_steps,
            "bias_factor": self.first.bias_factor,
            "well_tempered": self.first.bias_factor > 1.0,
            "shared_deposition": (
                "one METAD over both variables, so the hills describe the "
                "surface across them rather than two profiles"),
        }


def plan_pair_from_config(
    spec: dict[str, Any],
    topology: Any,
    *,
    temperature_K: float = 300.0,
    ligand_resname: str | None = None,
) -> MetadynamicsPair:
    """Read a metadynamics block that names two variables.

    The block carries a `variables` list of exactly two entries, each in
    the same shape a one-variable block takes. Deposition settings given at
    the top level apply to both, because they describe the hills and there
    is only one set of those.
    """
    entries = spec.get("variables")
    if not isinstance(entries, list) or len(entries) != 2:
        raise ValueError(
            "A two-variable metadynamics block needs `variables` holding "
            "exactly two entries, each shaped like a one-variable block. "
            f"This one has {0 if entries is None else len(entries)}. "
            "Three or more variables is a study whose surface cannot be "
            "read off a page, and this does not generate it."
        )

    shared = {key: spec[key] for key in
              ("height_kjmol", "pace_steps", "bias_factor")
              if key in spec}

    plans = []
    for entry in entries:
        merged = {**shared, **entry}
        plans.append(plan_from_config(
            merged, topology,
            temperature_K=temperature_K,
            ligand_resname=ligand_resname,
        ))

    for plan in plans:
        if plan.funnel:
            raise ValueError(
                "A funnel restraint is built around one ligand coordinate "
                "and the axis it leaves along, and this generates it as a "
                "wall on that one coordinate. Combining it with a second "
                "biased variable is a study design worth stating "
                "explicitly rather than inferring, so it is refused here: "
                "bias the funnel coordinate alone, or write the PLUMED "
                "input directly."
            )

    return MetadynamicsPair(first=plans[0], second=plans[1])


def build_plumed_script_pair(
    pair: MetadynamicsPair, reference_pdb: str | None = None
) -> str:
    """The PLUMED input for two variables under one deposition."""
    first, second = pair.plans
    lines = [
        "# Generated by FastMDXplora from two named collective variables.",
        f"# Biasing: {first.collective_variable} -- "
        f"{COLLECTIVE_VARIABLES[first.collective_variable]}",
        f"# Biasing: {second.collective_variable} -- "
        f"{COLLECTIVE_VARIABLES[second.collective_variable]}",
        "",
    ]

    lines.extend(cv_lines(first, reference_pdb, suffix="1"))
    lines.append("")
    lines.extend(cv_lines(second, reference_pdb, suffix="2"))
    lines.append("")
    lines.append(
        "metad: METAD ARG=cv1,cv2 "
        f"SIGMA={first.sigma:g},{second.sigma:g} "
        f"HEIGHT={first.height_kjmol:g} "
        f"PACE={first.pace_steps:d} "
        f"BIASFACTOR={first.bias_factor:g} "
        f"TEMP={first.temperature_K:g} "
        "FILE=HILLS"
        + _grid_keywords([(first.collective_variable, first.sigma),
                          (second.collective_variable, second.sigma)])
    )

    for plan, arg in zip(pair.plans, ("cv1", "cv2")):
        if not plan.walls:
            continue
        lines.append("")
        if plan.walls.upper is not None:
            lines.append(
                f"uwall_{arg}: UPPER_WALLS ARG={arg} "
                f"AT={plan.walls.upper:g} KAPPA={plan.walls.kappa:g}")
        if plan.walls.lower is not None:
            lines.append(
                f"lwall_{arg}: LOWER_WALLS ARG={arg} "
                f"AT={plan.walls.lower:g} KAPPA={plan.walls.kappa:g}")

    lines.append("")
    # Both variables and the bias, every deposition. The surface is judged
    # one dimension at a time, and that needs the trajectory of each.
    lines.append(
        "PRINT ARG=cv1,cv2,metad.bias STRIDE="
        f"{first.pace_steps:d} FILE=COLVAR")
    lines.append("")
    return "\n".join(lines)
