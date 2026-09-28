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

from fastmdxplora.refusals import StudyError
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
    "OrientationWarning",
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


class OrientationWarning(UserWarning):
    """The protein does not look like it is oriented for a membrane."""


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
