"""Putting a protein in a lipid bilayer, and the things that go wrong quietly.

A membrane protein simulated in water is not the protein: the hydrophobic belt
that sits in the bilayer is exposed to solvent, the helices splay, and the
trajectory describes a molecule that does not exist. So a membrane system has
to be built as one.

OpenMM builds the bilayer itself, which removes the usual dependency on an
external packing tool. What it does not do is check the two things that make
the result meaningful, and both fail silently:

**The protein has to be oriented and placed before it is embedded.**
``addMembrane`` builds the bilayer in the xy plane, centred at
``membraneCenterZ``, and takes the protein's frame as it is. A structure taken
straight from the PDB is in no such frame: crystallographic axes have no
relation to a membrane normal, and z = 0 has none to its centre. Embed it
anyway and the lipids pack around the wrong part of it, the run proceeds, and
every number that follows is about a structure nobody would recognise. OPM
publishes structures already oriented and centred; for anything else the
normal, centre and thickness are fitted from where the protein's
lipid-facing surface is apolar (:mod:`fastmdxplora.setup.membrane_fit`), and
:func:`place_for_membrane` decides from the study's settings which frame is
used.

**The pressure coupling has to be anisotropic.** An ordinary barostat scales
x, y and z together, which squeezes a bilayer that should be free to change
thickness independently of its area. The result is a wrong area per lipid,
which is the number membrane simulations are validated against. OpenMM has
``MonteCarloMembraneBarostat`` for this, and using the ordinary one is a
mistake that runs to completion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastmdxplora.refusals import CodedError, StudyError
from fastmdxplora.utils.logging import get_logger

logger = get_logger("setup.membrane")

__all__ = [
    "LIPIDS",
    "MAIN_TRANSITION_K",
    "Placement",
    "lipid_parameter_file",
    "membrane_forcefield_files",
    "place_for_membrane",
    "check_chains_point_the_same_way",
    "misoriented_copies",
    "PoreMap",
    "PoreClearing",
    "pore_map",
    "lipid_z_range",
    "lipid_in_a_pore",
    "clear_the_pores",
    "fill_the_pores",
]


#: Lipids OpenMM can build a bilayer from, with what each is usually for.
#: Not a list this software maintains -- it is what ``Modeller.addMembrane``
#: accepts, and offering one it does not would fail at the point of use.
LIPIDS: dict[str, str] = {
    "POPC": "the default for most membrane work; a fluid phosphatidylcholine",
    "POPE": "phosphatidylethanolamine, common in bacterial inner membranes",
    "DLPC": "a short-tailed phosphatidylcholine, thinner bilayer",
    "DLPE": "the phosphatidylethanolamine equivalent",
    "DMPC": "dimyristoyl; often used where a thinner membrane is wanted",
    "DOPC": "dioleoyl; two unsaturated tails, more fluid",
    "DPPC": "dipalmitoyl; gel phase at room temperature",
}

#: Force field files carrying lipid parameters, by the protein force field
#: they go with. A membrane system built against a force field with no lipid
#: parameters fails when the system is created, with a message about a
#: residue template rather than about the missing file.
#: CHARMM36's lipids are in its main file, beside the protein: OpenMM
#: ships no separate lipid file for it, and `charmm36/waters.xml`, named here
#: before, does not exist.
_LIPID_PARAMETERS = {
    "amber14": "amber14/lipid17.xml",
    "charmm36": "charmm36.xml",
}


def __getattr__(name: str) -> Any:
    """`OrientationWarning`, still importable and on its way out.

    Nothing raises it: the orientation check it belonged to is a refusal
    now, or a line in the log. Removing a public name belongs to a major
    release, so until then an import of it works and says so.
    """
    if name == "OrientationWarning":
        import warnings

        warnings.warn(
            "fastmdxplora.setup.membrane.OrientationWarning is never raised and "
            "will be removed in 3.0; nothing needs to catch it.",
            DeprecationWarning, stacklevel=2)

        class OrientationWarning(UserWarning):
            """The protein does not look like it is oriented for a membrane."""

        globals()["OrientationWarning"] = OrientationWarning
        return OrientationWarning
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def lipid_parameter_file(files: list[str], lipid: str) -> str | None:
    """Which of these force field files describes the lipid, for the record.

    The first that can build it alone: ``amber14-all.xml`` includes
    ``amber14/lipid17.xml``, so a methods section naming only the protein's
    bundle would leave the lipids' force field unsaid.
    """
    try:
        from openmm.app import ForceField
    except ImportError:  # pragma: no cover - setup needs OpenMM anyway
        return None
    for name in files:
        try:
            if any(t.startswith(lipid) for t in ForceField(name)._templates):
                return name
        except Exception:  # noqa: BLE001 - a file that needs another to load
            continue
    return None


def membrane_forcefield_files(existing: list[str], lipid: str = "POPC") -> list[str]:
    """Add lipid parameters to a force field that lacks them.

    The test is whether the force field can already build the lipid, not what
    its files are called: ``amber14-all.xml`` is a bundle that carries lipid17
    inside it, and adding the file again raises about a duplicate template for
    a residue nobody mentioned. Reading the name would have missed that, and
    did.

    Without the parameters the run fails at system creation with a message
    about a residue template for POPC, which names the symptom rather than the
    missing file.
    """
    files = list(existing)

    try:
        from openmm.app import ForceField

        if any(name.startswith(lipid) for name in ForceField(*files)._templates):
            return files
    except Exception:  # noqa: BLE001 - if it will not load, say so below
        pass

    for family, parameters in _LIPID_PARAMETERS.items():
        if any(family in f.lower() for f in files):
            candidate = files + [parameters]
            try:
                from openmm.app import ForceField

                templates = ForceField(*candidate)._templates
            except Exception as exc:  # noqa: BLE001
                raise StudyError(
                    f"Adding {parameters} for the {lipid} bilayer did not "
                    f"work: {exc}"
                , code="setup.membrane.lipid_unparameterized", lipid=lipid) from exc
            # Checked, not assumed: a file that loads and carries no
            # template for the lipid fails later, at the residue template.
            if not any(name.startswith(lipid) for name in templates):
                raise StudyError(
                    f"Adding {parameters} did not give the force field a "
                    f"template for {lipid}. Add a lipid parameter file that "
                    f"carries it to `force_field` yourself.",
                    code="setup.membrane.lipid_unparameterized", lipid=lipid)
            return candidate

    raise StudyError(
        f"A {lipid} bilayer needs lipid parameters, and the force field files "
        f"given carry none: {', '.join(files)}. The amber14 and charmm36 "
        "families both have them; if you are using another, add its lipid "
        "parameter file to `force_field` yourself."
    , code="setup.membrane.lipid_unparameterized", lipid=lipid)


#: Main (gel to fluid) transition temperatures of the lipids OpenMM builds,
#: in kelvin. Below it a bilayer's equilibrium is the gel phase, which a
#: simulation reaches, if at all, only over very long runs; a little above
#: it the bilayer is fluid but near a transition. Koynova and Caffrey, Biochim Biophys Acta 1376, 91 (1998),
#: and Silvius, in Lipid-Protein Interactions (1982).
MAIN_TRANSITION_K: dict[str, float] = {
    "DLPC": 271.0, "POPC": 271.0, "DOPC": 256.0, "DMPC": 297.0,
    "POPE": 298.0, "DLPE": 303.0, "DPPC": 314.0,
}

#: A structure whose fitted membrane normal lies within this of z is taken as
#: oriented already, and keeps its frame. The fit comes within 21 degrees of
#: OPM's normal across 65 membrane proteins, and within 15 for all but two
#: fits of 170; a structure oriented by hand, or by another method, differs
#: from the fit by about that much.
FRAME_TOLERANCE_DEG = 20.0

#: How far an OPM file's own membrane centre may lie from where the fit puts
#: it before that is said. OPM places the centre at z = 0, and the fit agreed
#: with it within 0.8 nm across 65 structures, 0.1 at the median.
OPM_CENTRE_TOLERANCE_NM = 1.0


@dataclass
class Placement:
    """A protein placed for a bilayer in the xy plane at z = 0, and how."""

    positions: Any
    record: dict[str, Any] = field(default_factory=dict)


def _in_nm(positions: Any) -> Any:
    import numpy as np

    try:
        from openmm import unit as openmm_unit

        if openmm_unit.is_quantity(positions):
            positions = positions.value_in_unit(openmm_unit.nanometer)
    except ImportError:  # pragma: no cover - only without OpenMM
        pass
    return np.asarray([[float(p[0]), float(p[1]), float(p[2])] for p in positions],
                      dtype=float)


def _as_positions(coordinates: Any, like: Any) -> Any:
    """Back to what came in: an OpenMM Quantity of Vec3 where one came in."""
    try:
        from openmm import Vec3
        from openmm import unit as openmm_unit

        if openmm_unit.is_quantity(like):
            return openmm_unit.Quantity(
                [Vec3(*(float(v) for v in row)) for row in coordinates],
                openmm_unit.nanometer)
    except ImportError:  # pragma: no cover - only without OpenMM
        pass
    return coordinates


def _fit_record(fit: Any) -> dict[str, Any]:
    return {
        "hydrophobic_thickness_nm": round(fit.thickness_nm, 2),
        "fit_score_nm2": round(fit.score_nm2, 1),
        "hydrophobic_fraction": round(fit.hydrophobic_fraction, 3),
        "lipid_facing_share": fit.details.get("lipid_facing_share"),
    }


def _no_belt(fit: Any, lipid: str) -> StudyError:
    from fastmdxplora.setup.membrane_fit import MEMBRANE_SCORE_FLOOR_NM2

    return StudyError(
        "This structure does not look like a membrane protein. The best place "
        "for a bilayer's hydrophobic core buries "
        f"{fit.score_nm2:.1f} nm2 of net apolar surface; every one of 65 "
        "membrane proteins measured buried at least 18.5, and no soluble "
        f"protein more than 5.4 (the line is {MEMBRANE_SCORE_FLOOR_NM2:g}). "
        "A membrane protein whose transmembrane part is missing from the model, "
        "or a soluble partner simulated without it, reads this way too.\n\n"
        "Check which chains are simulated (`chains`), give an oriented "
        "structure from OPM (https://opm.phar.umich.edu), or set "
        "`membrane_orientation_checked: true` to keep the structure's frame "
        "and have the bilayer placed along z where the fit puts it.",
        code="setup.membrane.no_belt", lipid=lipid,
        score_nm2=round(fit.score_nm2, 1))


def _tilted(tilt: float, lipid: str) -> StudyError:
    return StudyError(
        "The structure is not oriented for a bilayer. Its membrane normal, "
        "fitted from where its lipid-facing surface is apolar, lies "
        f"{tilt:.0f} degrees from z, and the bilayer is built in the xy "
        "plane.\n\n"
        "Set `membrane_orient: true` to rotate it onto z by that fit (which "
        "found OPM's normal within 21 degrees for 65 membrane proteins, 3 to 4 "
        "at the median), give an oriented structure from OPM "
        "(https://opm.phar.umich.edu), or set "
        "`membrane_orientation_checked: true` to keep this frame.",
        code="setup.membrane.orientation_unchecked", lipid=lipid,
        tilt_deg=round(tilt, 1))


def place_for_membrane(
    topology: Any,
    positions: Any,
    *,
    lipid: str = "POPC",
    orient: bool = False,
    orientation_checked: bool = False,
    frame: str | None = None,
    center_z_nm: float | None = None,
) -> Placement:
    """Place a protein for a bilayer built in the xy plane at z = 0.

    OpenMM builds the bilayer around ``membraneCenterZ`` and takes the
    protein's frame as it is. Nothing placed it before: the bilayer sat at
    z = 0 of whatever frame the file was in, which is OPM's convention and
    no other, and a rotated protein was centred on its centroid, which for
    a receptor with a G protein is far from its membrane-spanning part.

    Which frame, by what the study says:

    - an OPM file (``frame="opm"``): OPM's frame and centre, kept;
    - ``center_z_nm``, with the orientation stated: the centre given;
    - ``orientation_checked``: the orientation kept, the centre fitted along z;
    - ``orient``: the orientation and centre fitted
      (:func:`~fastmdxplora.setup.membrane_fit.fit_membrane`);
    - neither: fitted, and the frame kept only if it is already oriented
      (within :data:`FRAME_TOLERANCE_DEG`); refused otherwise, as is a
      structure with no surface a bilayer would hold.

    Returns the positions, moved so the membrane centre is at z = 0, and a
    record of how, for the setup record and the methods.
    """
    import numpy as np

    from fastmdxplora.setup.membrane_fit import (
        fit_along,
        fit_membrane,
        rotation_onto_z,
        tilt_deg,
    )

    coordinates = _in_nm(positions)
    along_z = (0.0, 0.0, 1.0)
    record: dict[str, Any] = {"lipid": lipid}
    rotation = np.eye(3)
    pivot = np.zeros(3)

    if center_z_nm is not None and orient:
        raise StudyError(
            "`membrane_center_z_nm` places the bilayer in the structure's own "
            "frame, and `membrane_orient: true` rotates the structure out of "
            "it. Give one: the centre with `membrane_orientation_checked: "
            "true`, or the rotation alone.",
            code="config.option.conflicting")

    if frame == "opm":
        centre = 0.0
        record["placed_by"] = "OPM frame"
        if orient:
            logger.info(
                "The structure is an OPM file, whose orientation comes from a "
                "transfer-energy calculation; it is kept, and "
                "`membrane_orient` is not applied.")
        fit = fit_along(topology, coordinates, along_z)
        if fit is not None:
            record.update(_fit_record(fit))
            record["fitted_centre_z_nm"] = round(float(fit.plane_point[2]), 2)
            if abs(float(fit.plane_point[2])) > OPM_CENTRE_TOLERANCE_NM:
                logger.warning(
                    "The OPM file places the membrane centre at z = 0, and the "
                    "fit places it at z = %.2f nm. OPM's frame is kept.",
                    float(fit.plane_point[2]))
    elif center_z_nm is not None:
        if not orientation_checked:
            raise StudyError(
                "`membrane_center_z_nm` says where the bilayer's centre is in "
                "the structure's frame, which needs that frame to be oriented: "
                "set `membrane_orientation_checked: true` with it.",
                code="config.option.missing_companion")
        centre = float(center_z_nm)
        record["placed_by"] = "stated orientation and centre"
    elif orientation_checked and not orient:
        fit = fit_along(topology, coordinates, along_z)
        if fit is None:
            centre = 0.0
            record["placed_by"] = "stated orientation; no protein to fit a centre to"
        else:
            centre = float(fit.plane_point[2])
            record["placed_by"] = "stated orientation; centre fitted along z"
            record.update(_fit_record(fit))
            if not fit.looks_like_a_membrane_protein:
                logger.warning(
                    "The structure does not look like a membrane protein (net "
                    "apolar surface in the best slab %.1f nm2), so the "
                    "bilayer's centre, placed where the fit puts it, is a "
                    "guess. The orientation is kept, as stated.",
                    fit.score_nm2)
    else:
        fit = fit_membrane(topology, coordinates)
        if fit is None:
            raise StudyError(
                "There is no protein to place in a bilayer: the structure "
                "holds fewer than twenty protein heavy atoms.",
                code="setup.membrane.no_belt", lipid=lipid)
        if not fit.looks_like_a_membrane_protein and not orientation_checked:
            raise _no_belt(fit, lipid)
        tilt = tilt_deg(fit, along_z)
        record.update(_fit_record(fit))
        record["tilt_of_input_deg"] = round(tilt, 1)
        if orient:
            rotation = rotation_onto_z(fit.normal)
            pivot = np.asarray(fit.origin_nm, dtype=float)
            centre = float(fit.centre_nm)
            record["placed_by"] = "fitted orientation and centre"
            turned = round(tilt)
            logger.info(
                "Rotated the structure %s to put its fitted membrane normal on "
                "z (hydrophobic thickness %.1f nm).",
                "by less than a degree" if turned < 1 else
                f"{turned} degree{'s' if turned != 1 else ''}", fit.thickness_nm)
        else:
            if tilt > FRAME_TOLERANCE_DEG:
                raise _tilted(tilt, lipid)
            along = fit_along(topology, coordinates, along_z)
            centre = float(along.plane_point[2])
            record.update(_fit_record(along))
            record["placed_by"] = "structure's own orientation; centre fitted along z"

    moved = (coordinates - pivot) @ rotation.T
    moved[:, 2] -= centre
    record["center_shift_nm"] = round(-centre, 3) + 0.0
    if not np.allclose(rotation, np.eye(3)):
        record["rotation"] = [[round(float(v), 6) for v in row] for row in rotation]
        record["rotated_about_nm"] = [round(float(v), 4) for v in pivot]
    return Placement(positions=_as_positions(moved, positions), record=record)



#: How far apart two copies' membrane normals may be and still share one
#: bilayer. The symmetry relating two copies of a membrane protein in one
#: membrane turns the normal onto itself, so the angle is zero up to how
#: closely the copies' conformations agree; a copy at 90 degrees lies in the
#: plane and one at 180 is upside down.
COPY_NORMAL_TOLERANCE_DEG = 30.0

#: Two chains are copies of one molecule when this share of the shorter one's
#: alpha carbons has a partner of the same residue number and name in the
#: other. Ragged ends are allowed for: 6B73's two receptors came out 289 and
#: 281 residues, the same molecule with different terminal residues
#: unresolved.
_COPY_SHARED_FRACTION = 0.9

#: Fewer matched alpha carbons than this, and the superposition that relates
#: two copies is not worth reading.
_COPY_MINIMUM_MATCHED = 20


def misoriented_copies(
    copies: dict[Any, dict[tuple[int, str], int]],
    coordinates: Any,
    normal: Any = (0.0, 0.0, 1.0),
) -> list[tuple[Any, Any, float]]:
    """Pairs of copies whose membrane normals disagree, and by how much.

    ``copies`` maps each chain to its alpha carbons keyed by residue number
    and name. Two chains matched over most of their length are copies of one
    molecule; the rotation that superposes one onto the other is the
    symmetry relating them, and applied to the membrane normal it gives the
    second copy's normal. In one bilayer the two agree: a trimer's three-fold
    lies along the normal and turns it onto itself. Two copies packed
    head-to-tail in a crystal are related by a two-fold across the normal,
    which turns it through 180 degrees.

    Separate from the topology reading so the geometry can be tested without
    OpenMM.
    """
    import numpy as np

    coordinates = np.asarray(coordinates, dtype=float)
    axis = np.asarray(normal, dtype=float)
    axis = axis / np.linalg.norm(axis)
    names = sorted(copies, key=str)
    found: list[tuple[Any, Any, float]] = []
    for position, first in enumerate(names):
        for other in names[position + 1:]:
            a, b = copies[first], copies[other]
            shared = sorted(set(a) & set(b))
            shorter = min(len(a), len(b))
            if (len(shared) < _COPY_MINIMUM_MATCHED
                    or len(shared) < _COPY_SHARED_FRACTION * shorter):
                continue
            here = coordinates[[a[key] for key in shared]]
            there = coordinates[[b[key] for key in shared]]
            here = here - here.mean(axis=0)
            there = there - there.mean(axis=0)
            left, _, right = np.linalg.svd(here.T @ there)
            sign = np.sign(np.linalg.det(right.T @ left.T)) or 1.0
            rotation = right.T @ np.diag([1.0, 1.0, sign]) @ left.T
            cosine = float(np.clip((rotation @ axis) @ axis, -1.0, 1.0))
            angle = float(np.degrees(np.arccos(cosine)))
            if angle > COPY_NORMAL_TOLERANCE_DEG:
                found.append((first, other, angle))
    return found


def check_chains_point_the_same_way(topology: Any, positions: Any) -> str | None:
    """Whether copies of one chain are placed the same way up in the bilayer.

    Every other check here asks about the structure as one object, and a
    structure holding two copies passes them whichever way the copies are
    related. What none of them can see is the two copies together.

    6B73 is the case: two receptors from the crystal's asymmetric unit,
    related by a two-fold that is not perpendicular to the membrane. Embedded
    together, one was upside down, and their soluble partners came to rest on
    opposite faces, one at z +4.0 nm and one at -4.1 nm. The run completed,
    giving a 193,388-atom system in which one receptor was inverted. A
    membrane has two sides and they are not equivalent; copies of one
    protein in one bilayer have the same side up, always.

    Judged by the symmetry between the copies (:func:`misoriented_copies`),
    not by each copy's longest axis: a porin monomer is as wide as it is
    tall, so the sign of its longest axis along the normal is noise, and a
    trimer whose copies are related by a three-fold along the normal was
    refused as inverted.
    """
    import mdtraj as md
    import numpy as np

    try:
        from openmm import unit as openmm_unit

        if openmm_unit.is_quantity(positions):
            positions = positions.value_in_unit(openmm_unit.nanometer)
    except ImportError:  # pragma: no cover - only without OpenMM
        pass

    mdtop = md.Topology.from_openmm(topology)
    coordinates = np.asarray(
        [[float(p[0]), float(p[1]), float(p[2])] for p in positions],
        dtype=float)

    copies: dict[Any, dict[tuple[int, str], int]] = {}
    for residue in mdtop.residues:
        if not residue.is_protein:
            continue
        chain = residue.chain
        label = getattr(chain, "chain_id", None) or str(chain.index)
        for atom in residue.atoms:
            if atom.name == "CA":
                copies.setdefault(label, {})[(residue.resSeq, residue.name)] = atom.index

    found = misoriented_copies(copies, coordinates)
    if not found:
        return None

    listed = "; ".join(f"chains {a} and {b}, {angle:.0f} degrees" for a, b, angle in found)
    first = found[0][0]
    return (
        "Copies of one molecule in this structure would not have the same side "
        "up in the bilayer. The symmetry relating each pair turns the "
        f"membrane normal through: {listed}. In one membrane every copy of a "
        "protein has the same side up, so the symmetry between them turns the "
        "normal onto itself.\n\n"
        "This is usually two copies from a crystal's asymmetric unit, which "
        "pack against each other rather than sit side by side in a membrane. "
        f"Simulate one copy (`chains: {first}`), or take the oriented assembly "
        "from OPM (https://opm.phar.umich.edu)."
    )


# ---------------------------------------------------------------------------
# Pores: a lipid inside a protein's pore is an artefact, and a runaway.
# ---------------------------------------------------------------------------

#: The counts below are of POPC at 2.0 nm padding, on the structures as
#: they reach `addMembrane`: the porins 2POR and 2OMF, and 11 others, OmpA
#: (1BXW), KcsA (1BL8), rhodopsin (1U19), the beta2 receptor (2RH1), 6B73,
#: bacteriorhodopsin's trimer (1C3W), aquaporin-1's tetramer (1J4N), MscL
#: (2OAR), alpha-hemolysin (7AHL), a c15 ring (2WIE) and GLIC (4HFI).

#: The pore map, read slice by slice across the lipids' z range. Each slice
#: is this thick: thick enough for a barrel's strands, 0.33 nm apart along
#: each, to close its wall in the slice, thin enough that side chains from
#: other heights do not fill a lumen they do not reach. 0.4, 0.8 and 1.2
#: find the same three lumens in 2POR and 2OMF, and take 33, 30 and 25
#: lipids from 2POR's pores where 0.6 takes 31; 0.4 and 0.8 also take 6
#: and 7 from aquaporin-1's vestibules, which 0.6 and 1.2 leave.
PORE_SLICE_NM = 0.6

#: Each protein heavy atom covers a disk of this radius in a slice, so a gap
#: the map closes is under 0.4 nm, narrower than a methylene. 0.15 takes 38
#: lipids from 2POR, 7 from aquaporin-1 and 1 from bacteriorhodopsin; 0.25
#: and 0.3 close more of 2POR's barrel mouths and take 25 and 18, and join
#: aquaporin-1's vestibules into holes that take 14 and 9.
PORE_ATOM_RADIUS_NM = 0.2

#: The slices are laid twice, the second set half a slice from the first.
#: With one set, 2POR's trimer centre closes at 1.6 to 2.2 nm and not at 1.3
#: to 1.9 or 1.9 to 2.5, so a tail between 1.9 and 2.2 nm was not seen.
PORE_SLICE_PHASES = 2

#: The grid the slices are mapped on, half the disk's radius.
PORE_GRID_NM = 0.1

#: The smallest hole taken as a pore. Of the holes holding a heavy atom of
#: a lipid OpenMM leaves in place, none in a helical protein is larger than
#: 1.29 nm2 (aquaporin-1's four vestibules; 0.81 in 6B73, below its
#: ligand), and every porin lumen holding one is 2.08 nm2 or more, nearly
#: all 3.9 to 16. From 0.5 to 2.0, 2POR loses the same 31 lipids and 2OMF
#: 18 or 19; at 0.5 aquaporin-1 loses 4 and 6B73 1, at 1.0 aquaporin-1 4,
#: and from 1.5 none of aquaporin-1, 6B73, bacteriorhodopsin, KcsA and MscL
#: loses one.
PORE_MIN_AREA_NM2 = 1.5

#: A lipid is taken as in a pore with at least this many heavy atoms in
#: one. Three spared a lipid with one or two atoms at the edge of a cavity
#: in aquaporin-1 (DPPC), MscL (POPE, DLPE) and bacteriorhodopsin's trimer
#: (POPE), and 2POR then ran away to NaN in three packings with packing
#: seed 975501023, which had packed with one; a tail tip in a lumen is cut
#: by the wall as surely as a whole tail.
PORE_LIPID_MIN_ATOMS = 1

#: OpenMM's own overlap cutoff in `addMembrane`: a lipid with an atom this
#: close to the half-width protein is removed by OpenMM itself.
_OPENMM_OVERLAP_NM = 0.22


@dataclass
class PoreMap:
    """The protein's pores, slice by slice along z, in the protein's frame.

    Each slice is ``(z_low, z_high, origin_xy, mask)``: a boolean grid of
    ``PORE_GRID_NM`` cells whose first cell starts at ``origin_xy``.
    """

    slices: list[tuple[float, float, Any, Any]] = field(default_factory=list)
    grid_nm: float = PORE_GRID_NM

    def __bool__(self) -> bool:
        return any(mask.any() for _, _, _, mask in self.slices)

    def area_nm2(self) -> list[float]:
        """Pore area in each slice."""
        return [float(mask.sum()) * self.grid_nm ** 2 for _, _, _, mask in self.slices]

    def holds(self, xyz: Any) -> Any:
        """Which of these points (nm, N x 3) lie in a pore."""
        import numpy as np

        xyz = np.asarray(xyz, dtype=float).reshape(-1, 3)
        inside = np.zeros(len(xyz), bool)
        for z_low, z_high, origin, mask in self.slices:
            in_slice = (xyz[:, 2] >= z_low) & (xyz[:, 2] < z_high)
            cell = np.floor((xyz[:, :2] - origin) / self.grid_nm).astype(int)
            ok = (in_slice & (cell >= 0).all(axis=1)
                  & (cell[:, 0] < mask.shape[0]) & (cell[:, 1] < mask.shape[1]))
            inside[ok] |= mask[cell[ok, 0], cell[ok, 1]]
        return inside

    def regions(self) -> tuple[list[tuple[int, int]], list[int]]:
        """The pores as connected regions: each slice's holes, joined where
        they share a cell with a hole of a slice that overlaps or meets it in
        z, across both sets of slices. Returns the holes as ``(slice, label)``
        and the region each belongs to."""
        import numpy as np
        from scipy import ndimage

        labels = [ndimage.label(mask)[0] for _, _, _, mask in self.slices]
        nodes = [(i, k) for i, lab in enumerate(labels) for k in range(1, int(lab.max()) + 1)]
        index = {node: n for n, node in enumerate(nodes)}
        parent = list(range(len(nodes)))

        def root(n: int) -> int:
            while parent[n] != n:
                parent[n] = parent[parent[n]]
                n = parent[n]
            return n

        for i, (low_i, high_i, origin_i, _) in enumerate(self.slices):
            cells = np.argwhere(labels[i] > 0)
            if not len(cells):
                continue
            centres = origin_i + (cells + 0.5) * self.grid_nm
            for j, (low_j, high_j, origin_j, _) in enumerate(self.slices):
                if j <= i or high_i < low_j - 1e-9 or high_j < low_i - 1e-9:
                    continue
                other = np.floor((centres - origin_j) / self.grid_nm).astype(int)
                ok = ((other >= 0).all(axis=1) & (other[:, 0] < labels[j].shape[0])
                      & (other[:, 1] < labels[j].shape[1]))
                for (a, b), k in zip(other[ok], cells[ok]):
                    there = labels[j][a, b]
                    if there:
                        here = labels[i][k[0], k[1]]
                        parent[root(index[(i, here)])] = root(index[(j, int(there))])
        return nodes, [root(n) for n in range(len(nodes))]

    def region_of(self, xyz: Any) -> Any:
        """The region (as :meth:`regions` numbers them) each point lies in, or -1."""
        import numpy as np
        from scipy import ndimage

        xyz = np.asarray(xyz, dtype=float).reshape(-1, 3)
        nodes, region = self.regions()
        index = {node: region[n] for n, node in enumerate(nodes)}
        found = np.full(len(xyz), -1, int)
        for i, (z_low, z_high, origin, mask) in enumerate(self.slices):
            lab = ndimage.label(mask)[0]
            in_slice = (xyz[:, 2] >= z_low) & (xyz[:, 2] < z_high)
            cell = np.floor((xyz[:, :2] - origin) / self.grid_nm).astype(int)
            ok = (in_slice & (cell >= 0).all(axis=1)
                  & (cell[:, 0] < mask.shape[0]) & (cell[:, 1] < mask.shape[1]))
            for n in np.flatnonzero(ok):
                k = lab[cell[n, 0], cell[n, 1]]
                if k:
                    found[n] = index[(i, int(k))]
        return found

    def only(self, keep: set[int]) -> PoreMap:
        """The same map with only the regions in ``keep``."""
        import numpy as np
        from scipy import ndimage

        nodes, region = self.regions()
        index = {node: region[n] for n, node in enumerate(nodes)}
        slices = []
        for i, (z_low, z_high, origin, mask) in enumerate(self.slices):
            lab = ndimage.label(mask)[0]
            wanted = [k for k in range(1, int(lab.max()) + 1) if index[(i, k)] in keep]
            slices.append((z_low, z_high, origin, np.isin(lab, wanted)))
        return PoreMap(slices=slices, grid_nm=self.grid_nm)


def pore_map(protein_nm: Any, heavy: Any, z_low: float, z_high: float, *,
             slice_nm: float = PORE_SLICE_NM,
             radius_nm: float = PORE_ATOM_RADIUS_NM,
             grid_nm: float = PORE_GRID_NM,
             min_area_nm2: float = PORE_MIN_AREA_NM2,
             phases: int = PORE_SLICE_PHASES) -> PoreMap:
    """Map the holes the protein encloses in slices from ``z_low`` to ``z_high``.

    In each slice the protein's heavy atoms cover disks of ``radius_nm``;
    a pore is a region of the slice those disks enclose on every side (what
    ``scipy.ndimage.binary_fill_holes`` closes) of at least
    ``min_area_nm2``. A groove between subunits is open to the bilayer on
    one side, so it is not a pore.

    The slices are laid ``phases`` times, each set shifted by a fraction of
    a slice, and a point is in a pore where any set puts it in one: where a
    hole closes depends on where a slice's edges fall, and one set alone
    misses a hole the next height closes.
    """
    import math

    import numpy as np
    from scipy import ndimage

    protein_nm = np.asarray(protein_nm, dtype=float).reshape(-1, 3)
    heavy = np.asarray(heavy, dtype=bool)
    found = PoreMap(grid_nm=grid_nm)
    count = max(1, int(math.ceil((z_high - z_low) / slice_nm - 1e-9)))
    reach = int(math.ceil(radius_nm / grid_nm))
    edges = [z_low - j * slice_nm / max(1, phases) for j in range(max(1, phases))]
    bounds = [(start + k * slice_nm, start + (k + 1) * slice_nm)
              for start in edges for k in range(count + (1 if start < z_low else 0))]
    for low, high in bounds:
        xy = protein_nm[heavy & (protein_nm[:, 2] >= low) & (protein_nm[:, 2] < high), :2]
        if len(xy) < 3:
            continue
        origin = xy.min(axis=0) - radius_nm - 2 * grid_nm
        shape = np.ceil((xy.max(axis=0) + radius_nm + 2 * grid_nm - origin) / grid_nm).astype(int)
        occupied = np.zeros(shape, bool)
        centres = (xy - origin) / grid_nm
        base = np.floor(centres).astype(int)
        for dx in range(-reach - 1, reach + 2):
            for dy in range(-reach - 1, reach + 2):
                cx, cy = base[:, 0] + dx, base[:, 1] + dy
                ok = (cx >= 0) & (cy >= 0) & (cx < shape[0]) & (cy < shape[1])
                # The cell's centre within the disk.
                d2 = ((cx + 0.5 - centres[:, 0]) ** 2 + (cy + 0.5 - centres[:, 1]) ** 2) * grid_nm ** 2
                hit = ok & (d2 < radius_nm ** 2)
                occupied[cx[hit], cy[hit]] = True
        holes = ndimage.binary_fill_holes(occupied) & ~occupied
        labels, n = ndimage.label(holes)
        if n:
            areas = np.asarray(ndimage.sum(holes, labels, range(1, n + 1))) * grid_nm ** 2
            mask = np.isin(labels, 1 + np.flatnonzero(areas >= min_area_nm2))
        else:
            mask = np.zeros(shape, bool)
        found.slices.append((low, high, origin, mask))
    return found


def lipid_in_a_pore(pores: PoreMap, heavy_nm: Any) -> bool:
    """Whether a lipid with these heavy atoms (nm) is in a pore: at least
    ``PORE_LIPID_MIN_ATOMS`` of them in one."""
    return int(pores.holds(heavy_nm).sum()) >= PORE_LIPID_MIN_ATOMS


def lipid_z_range(lipid: str, center_z_nm: float = 0.0) -> tuple[float, float]:
    """Where `addMembrane` puts the heavy atoms of OpenMM's ``lipid`` patch in z.

    OpenMM centres the patch on the middle of its extremes, water included,
    at ``membraneCenterZ``; the pores are mapped across the lipids' range
    from there. The same range reads a built system, whose frame setup does not move.
    """
    import numpy as np

    patch = _openmm_patch(lipid)
    xyz = _in_nm(patch.positions)
    centre = (xyz[:, 2].min() + xyz[:, 2].max()) / 2
    heavy = [a.index for a in patch.topology.atoms()
             if a.residue.name != "HOH" and a.element is not None
             and a.element.symbol != "H"]
    z = xyz[np.asarray(heavy), 2] - centre
    return center_z_nm + float(z.min()), center_z_nm + float(z.max()) + 1e-6


@dataclass
class PoreClearing:
    """What :func:`clear_the_pores` found, and the patch to pack from.

    ``patch`` is None where nothing needs clearing, or where the patch could
    not be shown to land where OpenMM's own tiling puts it: the bilayer is
    then packed from the lipid's name, as it always was. Counts per leaflet
    are ``[upper, lower]``, as ``lipids_per_leaflet`` is.
    """

    pores: PoreMap
    patch: Any = None
    pore_lipids: list[int] = field(default_factory=lambda: [0, 0])
    balancing_lipids: list[int] = field(default_factory=lambda: [0, 0])
    fallback: str | None = None
    #: The pores to fill with water once the bilayer is packed: the regions
    #: that held an atom of a removed lipid and are lined as water's are.
    to_fill: PoreMap = field(default_factory=lambda: PoreMap())
    #: Regions that held an atom of a removed lipid and are left dry, being
    #: lined as a lipid's site is.
    left_dry: int = 0


class _Patch:
    """A membrane patch as `addMembrane` reads one: a topology and positions."""

    def __init__(self, topology: Any, positions: Any) -> None:
        self.topology = topology
        self.positions = positions


def _openmm_patch(lipid: str) -> Any:
    import os

    from openmm.app import PDBFile
    from openmm.app import modeller as module

    return PDBFile(os.path.join(os.path.dirname(module.__file__), "data",
                                lipid.upper() + ".pdb"))


def clear_the_pores(topology: Any, positions: Any, lipid: str, *,
                    padding_nm: float, center_z_nm: float = 0.0,
                    probe: bool = True) -> PoreClearing:
    """The patch `addMembrane` would tile, without the lipids in the protein's pores.

    `Modeller.addMembrane` shrinks the protein to half its width about its
    centre, removes only the lipids that overlap the half-width protein, and
    then moves the protein back to full size by position over its
    relaxation. A hollow barrel shrunk about a trimer's axis moves inward
    and its lumen shrinks, so lipids are left where its pore will be, and
    the growing wall has to cut through them: 2POR's packings ran away to
    NaN on exactly those lipids. A pore is aqueous, and a lipid in one is
    an artefact even where the packing survives it.

    So the patch is tiled here as `addMembrane` tiles it (the same number of
    copies, the same offsets about the same centre), and the lipids OpenMM
    would leave in place that have a heavy atom in a pore of the full-size protein are
    removed, with as many of the lipids farthest from the protein removed
    from the other leaflet, so the leaflets stay equal. Given a unit cell of
    the whole tiling, OpenMM tiles it once. Where the protein has no pore,
    or no lipid OpenMM would leave lies in one, no patch is returned and
    nothing changes.
    """
    import math

    import numpy as np
    from openmm import Vec3
    from openmm import unit as openmm_unit
    from openmm.app import Topology
    from scipy.spatial import cKDTree

    protein = _in_nm(positions)
    heavy = np.array([atom.element is not None and atom.element.symbol != "H"
                      for atom in topology.atoms()], dtype=bool)
    original = _openmm_patch(lipid)
    patch_xyz = _in_nm(original.positions)
    cell = original.topology.getUnitCellDimensions().value_in_unit(openmm_unit.nanometer)
    cell = np.array([float(cell[0]), float(cell[1]), float(cell[2])])
    residues = list(original.topology.residues())
    lipids = [r for r in residues if r.name != "HOH"]
    lipid_atoms = [np.array([a.index for a in r.atoms()]) for r in lipids]
    lipid_heavy = [np.array([a.index for a in r.atoms()
                             if a.element is not None and a.element.symbol != "H"])
                   for r in lipids]

    # OpenMM's arithmetic, step for step (Modeller.addMembrane, 8.2 to 8.6.1).
    low, high = protein.min(axis=0), protein.max(axis=0)
    size = high - low
    centre = np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, center_z_nm])
    patch_centre = (patch_xyz.min(axis=0) + patch_xyz.max(axis=0)) / 2
    nx = int(math.ceil((size[0] + 2 * padding_nm) / cell[0]))
    ny = int(math.ceil((size[1] + 2 * padding_nm) / cell[1]))
    every = np.concatenate(lipid_atoms)
    mean_z = float(patch_xyz[every, 2].mean())
    leaf = [0 if patch_xyz[idx, 2].mean() < mean_z else 1 for idx in lipid_atoms]
    scaled = protein - centre
    scaled[:, :2] *= 0.5
    scaled += centre

    pores = pore_map(protein, heavy, *lipid_z_range(lipid, center_z_nm))
    found = PoreClearing(pores=pores)
    if not pores:
        return found

    offsets = [np.array([(x - 0.5 * (nx - 1)) * cell[0], (y - 0.5 * (ny - 1)) * cell[1], 0.0])
               for x in range(nx) for y in range(ny)]
    # The tiling's extremes fix OpenMM's centre for it; a lipid holding one
    # is not removed to balance, so the centre stays where it was.
    tiled_low = patch_xyz.min(axis=0) + offsets[0]
    tiled_high = patch_xyz.max(axis=0) + offsets[-1]
    shift = centre - patch_centre
    near_scaled = cKDTree(scaled)
    near_protein = cKDTree(protein)
    dropped: set[tuple[int, int]] = set()
    held: list[Any] = []
    in_pores = [0, 0]
    candidates: list[tuple[float, int, int, int, float, float, bool]] = []
    for t, offset in enumerate(offsets):
        for r, (idx, hv) in enumerate(zip(lipid_atoms, lipid_heavy)):
            world = patch_xyz[idx] + offset + shift
            if any(near_scaled.query_ball_point(world, _OPENMM_OVERLAP_NM, return_length=True)):
                continue  # OpenMM removes it itself, and balances for it.
            if lipid_in_a_pore(pores, patch_xyz[hv] + offset + shift):
                dropped.add((t, r))
                in_pores[leaf[r]] += 1
                held.append(patch_xyz[hv] + offset + shift)
                continue
            distance, _ = near_protein.query(world)
            tiled_xyz = patch_xyz[idx] + offset
            candidates.append((float(np.min(distance)), t, r, leaf[r],
                               float(world[:, 0].mean()), float(world[:, 1].mean()),
                               bool(np.isclose(tiled_xyz, tiled_low).any()
                                    or np.isclose(tiled_xyz, tiled_high).any())))
    if not dropped:
        return found

    extra = [max(in_pores) - n for n in in_pores]
    balancing = [0, 0]
    for _, t, r, side, *_rest in _pick_balancing(candidates, extra,
                                                 (nx * cell[0], ny * cell[1])):
        dropped.add((t, r))
        balancing[side] += 1
    short = max(e - b for e, b in zip(extra, balancing))

    bonds: dict[Any, list[Any]] = {}
    for bond in original.topology.bonds():
        bonds.setdefault(bond[0].residue, []).append(bond)
    tiled = Topology()
    chain = tiled.addChain()
    xyz_out: list[Any] = []
    kept_leaf: list[tuple[Any, int]] = []
    for t, offset in enumerate(offsets):
        lipid_index = 0
        for residue in residues:
            is_lipid = residue.name != "HOH"
            if is_lipid:
                r = lipid_index
                lipid_index += 1
                if (t, r) in dropped:
                    continue
            new_residue = tiled.addResidue(residue.name, chain, residue.id,
                                           residue.insertionCode)
            if is_lipid:
                kept_leaf.append((new_residue, leaf[r]))
            made = {}
            for atom in residue.atoms():
                made[atom] = tiled.addAtom(atom.name, atom.element, new_residue, atom.id)
                xyz_out.append(patch_xyz[atom.index] + offset)
            for bond in bonds.get(residue, []):
                tiled.addBond(made[bond[0]], made[bond[1]], bond.type, bond.order)
    tiled.setUnitCellDimensions(Vec3(float(nx * cell[0]), float(ny * cell[1]),
                                     float(cell[2])) * openmm_unit.nanometer)
    xyz_out = np.asarray(xyz_out)

    problem = _lands_where_openmm_tiles(
        xyz_out, tiled, kept_leaf, patch_centre=patch_centre,
        patch_z=(float(patch_xyz[:, 2].min()), float(patch_xyz[:, 2].max())),
        size=size, padding_nm=padding_nm)
    # Upper first, as `lipids_per_leaflet` is; OpenMM's leaf 1 is the upper.
    found.pore_lipids = [in_pores[1], in_pores[0]]
    found.balancing_lipids = [balancing[1], balancing[0]]
    if short > 0 and not problem:
        problem = (f"the leaflets would differ by {short} lipids, as every lipid "
                   "left to balance them holds an extreme of the tiling, which "
                   "fixes where OpenMM centres it")
    if problem:
        found.fallback = problem
        return found
    patch = _Patch(tiled, openmm_unit.Quantity(
        [Vec3(*(float(v) for v in row)) for row in xyz_out], openmm_unit.nanometer))
    if probe:
        try:
            problem = _openmm_places_it(
                topology, positions, lipid, patch, pores, padding_nm=padding_nm,
                center_z_nm=center_z_nm,
                removed=sum(in_pores) + sum(balancing))
        except _ProbeFailed as exc:
            problem = f"OpenMM could not be asked ({exc})"
        except Exception as exc:  # noqa: BLE001 - any failure means not shown
            problem = f"OpenMM could not be asked ({type(exc).__name__}: {exc})"
        if problem:
            found.fallback = problem
            return found
    found.patch = patch
    found.to_fill, found.left_dry = _pores_to_fill(
        pores, np.concatenate(held), topology, protein)
    return found


#: A pore region is filled with water only where at least this share of the
#: side-chain heavy atoms lining it (within 0.4 nm of it) are N or O.
#: Counted on the regions that held a removed lipid's atoms: 2POR's and
#: 2OMF's lumens 0.27 to 0.31, MscL's pore 0.28 to 0.30, aquaporin-1's
#: central pore 0.33, and bacteriorhodopsin's central cavity, which holds
#: lipids natively, 0.04 to 0.11.
PORE_FILL_MIN_POLAR = 0.2


def _pores_to_fill(pores: PoreMap, held: Any, topology: Any,
                   protein: Any) -> tuple[PoreMap, int]:
    """The pore regions that held these removed lipid atoms and are lined as
    water's are, and how many such regions are not."""
    import numpy as np
    from scipy import ndimage
    from scipy.spatial import cKDTree

    regions = {int(g) for g in pores.region_of(held) if g >= 0}
    if not regions:
        return PoreMap(grid_nm=pores.grid_nm), 0
    atoms = list(topology.atoms())
    side = np.array([a.element is not None and a.element.symbol != "H"
                     and a.name not in ("N", "CA", "C", "O", "OXT")
                     and len(list(a.residue.atoms())) > 1 for a in atoms])
    polar = np.array([a.element is not None and a.element.symbol in ("N", "O")
                      for a in atoms])
    nodes, region = pores.regions()
    lining: dict[int, set[int]] = {g: set() for g in regions}
    for n, (i, k) in enumerate(nodes):
        if region[n] not in regions:
            continue
        z_low, z_high, origin, mask = pores.slices[i]
        cells = np.argwhere(ndimage.label(mask)[0] == k)
        xy = origin + (cells + 0.5) * pores.grid_nm
        near = np.flatnonzero(side & (protein[:, 2] >= z_low - 0.2)
                              & (protein[:, 2] < z_high + 0.2))
        if not len(near):
            continue
        tree = cKDTree(protein[near, :2])
        for hits in tree.query_ball_point(xy, 0.4):
            lining[region[n]].update(int(near[j]) for j in hits)
    wet = {g for g, found in lining.items()
           if found and polar[sorted(found)].mean() >= PORE_FILL_MIN_POLAR}
    return pores.only(wet), len(regions) - len(wet)


#: Balancing removals are at least this far apart in xy, across the
#: periodic box, so the far bilayer loses one lipid here and there rather
#: than a patch of one leaflet at the box's corner, where every tile's
#: farthest lipids meet. Halved until enough are found. Counted on 2POR
#: where OpenMM starts, in 2 nm discs over the bilayer beyond 2.5 nm of the
#: protein: farthest-first alone left one leaflet at 0.44 of the other's
#: density, 2.0 nm apart at 0.65, 3.0 at 0.67, and OpenMM's own tiling,
#: removing nothing, has 0.71.
BALANCING_SPACING_NM = 3.0


def _pick_balancing(candidates: list[tuple], extra: list[int], box_xy: tuple[float, float],
                    spacing_nm: float = BALANCING_SPACING_NM) -> list[tuple]:
    """The lipids removed so each leaflet loses as many as the other.

    ``candidates`` are ``(distance, tile, residue, leaf, x, y, holds_extreme)``
    and ``extra[leaf]`` how many that leaf still needs to lose. Farthest from
    the protein first, none within ``spacing_nm`` of one already taken in the
    same leaflet, and none holding an extreme of the tiling.
    """
    chosen: list[tuple] = []
    for side in (0, 1):
        need = extra[side]
        pool = sorted((c for c in candidates if c[3] == side and not c[6]),
                      key=lambda c: (-c[0], c[1], c[2]))
        taken: list[tuple] = []
        spacing = spacing_nm
        while need > len(taken):
            for c in pool:
                if len(taken) >= need:
                    break
                if c in taken:
                    continue
                if all(_periodic_xy(c, o, box_xy) >= spacing for o in taken):
                    taken.append(c)
            if spacing == 0:
                break
            spacing = spacing / 2 if spacing > 0.05 else 0.0
        chosen.extend(taken)
    return chosen


def _periodic_xy(a: tuple, b: tuple, box_xy: tuple[float, float]) -> float:
    dx = (a[4] - b[4]) - box_xy[0] * round((a[4] - b[4]) / box_xy[0])
    dy = (a[5] - b[5]) - box_xy[1] * round((a[5] - b[5]) / box_xy[1])
    return (dx * dx + dy * dy) ** 0.5


#: How long one run of OpenMM's `addMembrane`, as far as its relaxation, may
#: take before the patch is given up: 6 to 7 s for 2POR's 310,000 atoms.
PROBE_SECONDS = 120.0


class _ProbeFailed(CodedError, RuntimeError):
    """The probe of OpenMM's own `addMembrane` was given up. Caught where it is
    run, and the bilayer packed from the lipid's name, with why in a warning."""

    default_code = "setup.membrane.pores_unprobed"


def _openmm_starts_from(topology: Any, positions: Any, lipid_type: Any, *,
                        padding_nm: float, center_z_nm: float) -> tuple[Any, Any]:
    """The membrane OpenMM's own `addMembrane` builds from ``lipid_type``,
    stopped where its relaxation would begin: its topology and positions (nm).

    No force field is used: a stand-in gives OpenMM a system with one
    uncharged particle per atom, and the context it makes stops OpenMM at
    the first positions it is given.
    """
    import numpy as np
    import openmm
    from openmm import unit as openmm_unit
    from openmm.app import Modeller
    from openmm.app import modeller as module

    class _Stop(Exception):
        pass

    made: list[Any] = []
    started: list[Any] = []

    class _NoForceField:
        def createSystem(self, topology, **kwargs):  # noqa: N802 - OpenMM's name
            system = openmm.System()
            nonbonded = openmm.NonbondedForce()
            for _ in range(topology.getNumAtoms()):
                system.addParticle(1.0)
                nonbonded.addParticle(0.0, 0.3, 0.0)
            system.addForce(nonbonded)
            made.append(topology)
            return system

    class _Context:
        def __init__(self, *args, **kwargs):
            pass

        def setPositions(self, xyz):  # noqa: N802 - OpenMM's name
            if openmm_unit.is_quantity(xyz):
                xyz = xyz.value_in_unit(openmm_unit.nanometer)
            started.append(np.array([[p[0], p[1], p[2]] for p in xyz], dtype=float))
            raise _Stop

    import inspect

    names = set(getattr(getattr(inspect.unwrap(Modeller.addMembrane), "__code__", None),
                        "co_names", ()))
    if not {"Context", "createSystem"} <= names:
        raise _ProbeFailed("addMembrane no longer makes its context by the name "
                           "the stand-in replaces")

    import signal
    import threading
    import time

    def _too_long(*_):
        raise _ProbeFailed(f"addMembrane ran past {PROBE_SECONDS:g} s")

    # A SIGALRM handler installed from C reads as None and could not be put
    # back, so the probe then runs untimed and leaves it and its timer alone.
    timed = (threading.current_thread() is threading.main_thread()
             and signal.getsignal(signal.SIGALRM) is not None)
    # The stand-in replaces the module's `Context` for the whole process while
    # it runs, and is put back in `finally`; setup prepares one system at a
    # time on one thread, so nothing else builds a membrane meanwhile.
    original = module.Context
    started_at = time.monotonic()
    if timed:
        # A timer already running (a test's time limit) is saved and armed
        # again after, less the time the probe took: one deadline falling
        # inside the probe comes when it ends.
        previous = signal.signal(signal.SIGALRM, _too_long)
        outer = signal.setitimer(signal.ITIMER_REAL, PROBE_SECONDS)
    try:
        module.Context = _Context
        trial = Modeller(topology, positions)
        try:
            trial.addMembrane(_NoForceField(), lipidType=lipid_type,
                              membraneCenterZ=center_z_nm * openmm_unit.nanometer,
                              minimumPadding=padding_nm * openmm_unit.nanometer)
        except _Stop:
            pass
    finally:
        module.Context = original
        if timed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            if outer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL,
                                 max(outer[0] - (time.monotonic() - started_at), 1e-6),
                                 outer[1])
    if time.monotonic() - started_at > PROBE_SECONDS:
        raise _ProbeFailed(f"addMembrane ran past {PROBE_SECONDS:g} s")
    if not made or not started:
        raise _ProbeFailed("addMembrane did not reach its relaxation")
    built = made[0]
    return built, started[0][:built.getNumAtoms()]


def _openmm_places_it(topology: Any, positions: Any, lipid: str, patch: Any,
                      pores: PoreMap, *, padding_nm: float, center_z_nm: float,
                      removed: int) -> str | None:
    """Why OpenMM itself would not build from ``patch`` what it builds from
    ``lipid`` less the lipids removed, or None.

    The patch copies OpenMM's tiling, so it is checked against OpenMM: its
    own `addMembrane` is run on both, as far as the relaxation, and must
    give the same box and water, the patch's lipids where its own tiling
    puts them, exactly ``removed`` fewer, none with a heavy atom in a pore,
    and the leaflets equal.
    """
    import numpy as np

    from openmm import unit as openmm_unit
    from scipy.spatial import cKDTree

    def read(built, xyz):
        lipids, waters = [], []
        heavy_in_pore = 0
        for residue in built.residues():
            index = [a.index for a in residue.atoms()]
            if residue.name == "HOH":
                waters.append(xyz[index[0]])
                continue
            lipids.append(xyz[index].mean(axis=0))
            heavy = [a.index for a in residue.atoms()
                     if a.element is not None and a.element.symbol != "H"]
            heavy_in_pore += int(lipid_in_a_pore(pores, xyz[heavy]))
        box = np.array(built.getPeriodicBoxVectors().value_in_unit(openmm_unit.nanometer),
                       dtype=float)
        return np.array(lipids).reshape(-1, 3), np.array(waters), box, heavy_in_pore

    own, own_water, own_box, _ = read(*_openmm_starts_from(
        topology, positions, lipid, padding_nm=padding_nm, center_z_nm=center_z_nm))
    ours, our_water, our_box, in_pore = read(*_openmm_starts_from(
        topology, positions, patch, padding_nm=padding_nm, center_z_nm=center_z_nm))
    if not np.allclose(own_box, our_box, rtol=0, atol=1e-9):
        return "OpenMM gives it a different box"
    if len(own_water) != len(our_water) or (
            len(own_water) and np.abs(np.sort(own_water, axis=0)
                                      - np.sort(our_water, axis=0)).max() > 1e-6):
        return "OpenMM places different water from it"
    if len(ours) and (cKDTree(own).query(ours)[0] > 1e-6).any():
        return "OpenMM places its lipids where its own tiling does not"
    if len(own) - len(ours) != removed:
        return (f"OpenMM leaves {len(own) - len(ours)} fewer lipids, "
                f"not the {removed} removed")
    if in_pore:
        return f"OpenMM still starts {in_pore} lipids in a pore"
    z = ours[:, 2]
    if len(z) and (z >= z.mean()).sum() != (z < z.mean()).sum():
        return "OpenMM leaves the leaflets unequal"
    return None


def _lands_where_openmm_tiles(xyz: Any, topology: Any, kept_leaf: list[tuple[Any, int]], *,
                              patch_centre: Any, patch_z: tuple[float, float],
                              size: Any, padding_nm: float) -> str | None:
    """Why `addMembrane` would not put this tiling where its own goes, or None.

    The tiling copies OpenMM's arithmetic, so it is checked against that
    arithmetic as OpenMM applies it to a patch it is given: one copy in x and
    in y, the same centre (OpenMM recentres a patch on the middle of its
    extremes), the same z extremes (which decide where it adds water beyond
    the patch), and every lipid in the leaflet it was in.
    """
    import math

    import numpy as np
    from openmm import unit as openmm_unit

    cell = topology.getUnitCellDimensions().value_in_unit(openmm_unit.nanometer)
    copies = (int(math.ceil((size[0] + 2 * padding_nm) / float(cell[0]))),
              int(math.ceil((size[1] + 2 * padding_nm) / float(cell[1]))))
    if copies != (1, 1):
        return f"OpenMM would tile it {copies[0]} x {copies[1]}, not once"
    centre = (xyz.min(axis=0) + xyz.max(axis=0)) / 2
    if not np.allclose(centre, patch_centre, rtol=0, atol=1e-6):
        return "its centre moved"
    if not np.allclose([xyz[:, 2].min(), xyz[:, 2].max()], patch_z, rtol=0, atol=1e-6):
        return "its z extremes moved"
    lipid_atoms = {residue: [a.index for a in residue.atoms()] for residue, _ in kept_leaf}
    every = np.concatenate([np.asarray(v) for v in lipid_atoms.values()])
    mean_z = float(xyz[every, 2].mean())
    for residue, side in kept_leaf:
        if (0 if xyz[lipid_atoms[residue], 2].mean() < mean_z else 1) != side:
            return "a lipid changed leaflet"
    return None


def fill_the_pores(modeller: Any, forcefield: Any, pores: PoreMap) -> int:
    """Fill the protein's pores with water, after the bilayer is packed.

    `addMembrane` takes its water from the patch, which holds none between
    the leaflets, so a pore cleared of lipid is left empty there. OpenMM's
    own `addSolvent` is run on a copy, which places water only where it
    clashes with nothing, and of the waters it adds only those whose oxygen
    lies in a pore are retained. No ions are added: those `addMembrane` placed
    already neutralize the system. Returns how many waters were added.
    """
    import numpy as np
    from openmm import unit as openmm_unit
    from openmm.app import Modeller

    if not pores:
        return 0
    trial = Modeller(modeller.topology, modeller.positions)
    before = trial.topology.getNumResidues()
    trial.addSolvent(forcefield, neutralize=False)
    xyz = np.asarray(trial.positions.value_in_unit(openmm_unit.nanometer))
    added = list(trial.topology.residues())[before:]
    oxygen = np.array([next(a.index for a in r.atoms()
                            if a.element is not None and a.element.symbol == "O")
                       for r in added], dtype=int)
    keep = pores.holds(xyz[oxygen]) if len(oxygen) else np.zeros(0, bool)
    trial.delete([r for r, k in zip(added, keep) if not k])
    modeller.topology = trial.topology
    modeller.positions = trial.positions
    return int(keep.sum())
