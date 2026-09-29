"""Whether a binding free energy's reference state holds for this study.

A standard binding free energy from a potential of mean force along the
ligand-site distance rests on things the curve cannot show. The shape test in
`simulation.binding` catches a tail that is not yet free; four other ways the
conversion fails leave a smooth curve and a plausible number, and each can be
measured from the windows a study already has:

1. **The shell at the outer range is not open.** The reference assumes the
   ligand in bulk has the whole sphere at its radius (or the whole cap, under
   a cone). Where the protein occupies a share of it, the ligand's room there
   is smaller than ``4 pi r^2`` and the answer is too negative by
   ``kT ln(1/f)``, f the share that is open. On C1 (trypsin and benzamidine)
   the cap was 18% to 58% open across the outer range, worth about 3 kJ/mol,
   and no length of run changes it: the site sat 0.875 nm inside a protein
   reaching 3.2 nm.
2. **The system has a membrane.** Bulk is then a slab of water, not the
   isotropic solvent the ``-2kT ln r`` reference describes. Without a cone
   the shell always meets the bilayer; under one, the first check measures
   whether the cap stays in the water.
3. **The pull went through the protein.** A window seeded along a path the
   ligand cannot take in the bound state (through backbone, not past a side
   chain) samples a protein the pull deformed. The p38 alpha study whose
   steered path dragged Tyr35 through a conformation dissociation does not
   visit came out 15 kcal/mol wrong.
4. **The ligand's orientation was not sampled.** A distance restrains where
   the ligand is, not how it is turned. The conversion assumes each window
   saw every orientation open to it; a ligand held in one orientation for a
   whole window, which a larger or more rigid one near the protein often is,
   leaves that window's free energy a statement about one pose.

The first two refuse the number, because their error is known in size and
direction. The last two are said beside it, because what they measure is
evidence of a problem rather than its size.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: Directions tried on each sphere. Two thousand resolve an open share to
#: about a percent, which is finer than anything the answer depends on.
DIRECTIONS = 2000

#: Two heavy atoms closer than this are in contact. A ligand placed with no
#: atom this close to anything that is not solvent is free there.
CONTACT_NM = 0.4

#: Closer than this, two heavy atoms overlap: no non-bonded pair in an
#: equilibrated structure comes this near. Used for the path, where the
#: question is whether the ligand could be there at all.
OVERLAP_NM = 0.25

#: Frames taken from each window for the geometry. The protein moves little
#: at the scale of a shell, so a handful spread over the window describe it.
GEOMETRY_FRAMES = 12

#: Fewer frames than this and a window's orientation is not judged: the
#: count of independent orientations is itself too uncertain to act on.
ORIENTATION_MINIMUM_FRAMES = 100

#: Frames taken from each window for its orientation series, at most. Enough
#: to count independent orientations in the tens, which is the question; a
#: study of thirty-five windows reads this many from each.
ORIENTATION_FRAMES = 1000


def fibonacci_directions(n: int = DIRECTIONS) -> np.ndarray:
    """``n`` unit vectors spread evenly over the sphere."""
    index = np.arange(n, dtype=float) + 0.5
    polar = np.arccos(1.0 - 2.0 * index / n)
    azimuth = math.pi * (1.0 + 5.0 ** 0.5) * index
    return np.column_stack((np.cos(azimuth) * np.sin(polar),
                            np.sin(azimuth) * np.sin(polar),
                            np.cos(polar)))


def _minimum_image(vectors: np.ndarray, cell: np.ndarray) -> np.ndarray:
    """Each vector reduced to its nearest periodic image, in any cell."""
    from fastmdxplora.simulation.seeding import shortest_vector

    vectors = np.atleast_2d(np.asarray(vectors, dtype=float))
    cells = np.broadcast_to(np.asarray(cell, dtype=float), (len(vectors), 3, 3))
    return shortest_vector(vectors, cells)


def obstacles_around(centre: np.ndarray, atoms: np.ndarray,
                     cell: np.ndarray | None, reach: float) -> np.ndarray:
    """Every copy of ``atoms`` within ``reach`` of ``centre``, relative to it.

    Periodic copies are included, since near the minimum-image limit a point
    on the shell is closer to an atom's neighbouring copy than to the one
    nearest the centre.
    """
    atoms = np.asarray(atoms, dtype=float)
    if atoms.size == 0:
        return np.zeros((0, 3))
    if cell is None:
        relative = atoms - centre
    else:
        cell = np.asarray(cell, dtype=float)
        nearest = _minimum_image(atoms - centre, cell)
        shifts = np.array([[i, j, k] for i in (-1, 0, 1) for j in (-1, 0, 1)
                           for k in (-1, 0, 1)], dtype=float) @ cell
        relative = (nearest[:, None, :] + shifts[None, :, :]).reshape(-1, 3)
    return relative[np.linalg.norm(relative, axis=1) <= reach]


def _tree(points: np.ndarray):
    from scipy.spatial import cKDTree

    return cKDTree(points if len(points) else np.full((1, 3), 1e6))


def open_share(obstacles: np.ndarray, radius: float, ligand: np.ndarray,
               directions: np.ndarray, *, contact: float = CONTACT_NM) -> float:
    """The share of ``directions`` in which the ligand, at ``radius``, is free.

    ``obstacles`` are relative to the site's centre, ``ligand`` the ligand's
    heavy atoms relative to its own centre, as it sat in the frame. The
    ligand is placed at each point of the sphere in that pose and is free
    there if none of its atoms comes within ``contact`` of an obstacle. Its
    real shape rather than a sphere around it, since a sphere as wide as a
    ligand's longest axis closes gaps the ligand fits through.
    """
    if len(directions) == 0:
        return float("nan")
    tree = _tree(obstacles)
    placed = (radius * directions)[:, None, :] + ligand[None, :, :]
    distance, _ = tree.query(placed.reshape(-1, 3), distance_upper_bound=contact)
    touching = np.isfinite(distance).reshape(len(directions), len(ligand))
    return float(np.mean(~touching.any(axis=1)))


def within_cap(directions: np.ndarray, axis: np.ndarray | None,
               half_angle_deg: float | None) -> np.ndarray:
    """The directions inside a cone, or all of them where there is none."""
    if axis is None or half_angle_deg is None:
        return directions
    unit = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    return directions[directions @ unit >= math.cos(math.radians(half_angle_deg))]


# ---------------------------------------------------------------------------
# Superposition, so windows run separately can be compared
# ---------------------------------------------------------------------------
def kabsch(mobile: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(rotation, mobile_centre, target_centre)`` taking mobile onto target.

    Apply as ``(x - mobile_centre) @ rotation + target_centre``.
    """
    a = np.asarray(mobile, dtype=float)
    b = np.asarray(target, dtype=float)
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    h = (a - ca).T @ (b - cb)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(u @ vt))
    rotation = u @ np.diag([1.0, 1.0, d]) @ vt
    return rotation, ca, cb


def principal_axis(atoms: np.ndarray) -> np.ndarray | None:
    """The longest axis of a group of atoms, or None where it has none."""
    atoms = np.asarray(atoms, dtype=float)
    if len(atoms) < 3:
        return None
    centred = atoms - atoms.mean(axis=0)
    values, vectors = np.linalg.eigh(centred.T @ centred)
    if values[-1] <= 0 or values[-1] - values[-2] < 1e-3 * values[-1]:
        # Two axes as long as each other: a disc or a sphere has no longest
        # one, and the one picked would be noise.
        return None
    return vectors[:, -1]


def orientation_samples(axes: np.ndarray) -> dict[str, Any]:
    """How many independent orientations a series of axes holds.

    The axis has no sign (a molecule turned end over end has the same one),
    so the series used is the orientation tensor ``e e^T``, whose five
    independent components are what a sign-free orientation is. The least
    sampled of them decides.
    """
    from fastmdxplora.statistics import (
        correlation_is_resolved,
        statistical_inefficiency,
    )

    axes = np.asarray(axes, dtype=float)
    n = len(axes)
    if n < 3:
        return {"frames": n, "effective_samples": None, "resolved": None}
    components = [axes[:, 0] ** 2 - axes[:, 1] ** 2, axes[:, 2] ** 2,
                  axes[:, 0] * axes[:, 1], axes[:, 0] * axes[:, 2],
                  axes[:, 1] * axes[:, 2]]
    worst, resolved = 1.0, True
    for series in components:
        if float(np.std(series)) < 1e-6:
            continue
        worst = max(worst, statistical_inefficiency(series))
        resolved = resolved and correlation_is_resolved(series)
    return {"frames": n, "effective_samples": float(n / worst),
            "resolved": bool(resolved)}


# ---------------------------------------------------------------------------
# Reading the windows
# ---------------------------------------------------------------------------
def _is_ion(residue: Any) -> bool:
    return residue.n_atoms == 1 and not residue.is_water


def _groups(topology: Any, ligand_resname: str, site_selection: str) -> dict[str, np.ndarray]:
    ligand = topology.select(f"resname {ligand_resname}")
    heavy_ligand = np.array([i for i in ligand
                             if topology.atom(int(i)).element is not None
                             and topology.atom(int(i)).element.symbol != "H"], dtype=int)
    site = topology.select(site_selection)
    ligand_set = set(int(i) for i in ligand)
    obstacles = np.array([
        atom.index for atom in topology.atoms
        if atom.index not in ligand_set
        and not atom.residue.is_water and not _is_ion(atom.residue)
        and atom.element is not None and atom.element.symbol != "H"], dtype=int)
    backbone = topology.select("protein and backbone")
    anchor = topology.select("protein and name CA")
    return {"ligand": ligand, "ligand_heavy": heavy_ligand if heavy_ligand.size else ligand,
            "site": site, "obstacles": obstacles, "backbone": backbone,
            "anchor": anchor if anchor.size >= 3 else site}


def _centre(xyz: np.ndarray, atoms: np.ndarray, masses: np.ndarray) -> np.ndarray:
    weight = masses[atoms]
    if weight.sum() <= 0:
        weight = np.ones(len(atoms))
    return (xyz[atoms] * weight[:, None]).sum(axis=0) / weight.sum()


def _masses(topology: Any) -> np.ndarray:
    return np.array([getattr(atom.element, "mass", 0.0) or 0.0
                     for atom in topology.atoms], dtype=float)


def _load(directory: Path, *, frames: int, skip: float = 0.0,
          selection: str | None = None) -> Any | None:
    """A window's production trajectory, thinned to about ``frames``.

    ``skip`` is the share at the start left out, as the recombination leaves
    it out. ``selection`` loads only those atoms. Molecules are made whole
    and the ligand moved to the copy nearest the protein, as every analysis
    here reads a trajectory.
    """
    import mdtraj as md

    from fastmdxplora.analysis.loading import _made_whole
    from fastmdxplora.utils import suppress_native_output

    simulation = Path(directory) / "simulation"
    trajectory = simulation / "production.dcd"
    topology_file = simulation / "trajectory_topology.pdb"
    if not (trajectory.is_file() and topology_file.is_file()):
        return None
    try:
        topology = md.load_topology(str(topology_file))
        atoms = topology.select(selection) if selection else None
        # The DCD reader says what it found on the terminal, below Python.
        with suppress_native_output():
            with md.open(str(trajectory)) as handle:
                total = len(handle)
            first = int(total * float(skip))
            stride = max(1, (total - first) // max(1, frames))
            loaded = md.load(str(trajectory), top=topology, atom_indices=atoms,
                             stride=stride)
    except Exception as exc:  # noqa: BLE001 - a window that will not load is left out
        logger.debug("Could not load %s: %s", trajectory, exc)
        return None
    loaded = loaded[first // stride:][:frames]
    if loaded.n_frames == 0:
        return None
    return _made_whole(loaded)


def _topology_of(directory: Path) -> Any | None:
    import mdtraj as md

    path = Path(directory) / "simulation" / "trajectory_topology.pdb"
    try:
        return md.load_topology(str(path))
    except Exception:  # noqa: BLE001
        return None


def _axis_atoms_in_saved(directory: Path, full_indices: "list[int] | None",
                         saved: Any) -> np.ndarray | None:
    """The cone's axis atoms, numbered as the saved trajectory numbers them.

    The cone records them as the whole system numbers them, and a trajectory
    saved without water numbers them again. Mapped through the prepared
    system's topology, and not at all where the two cannot be matched.
    """
    import mdtraj as md

    from fastmdxplora.simulation.pipeline import setup_records_of

    if not full_indices:
        return None
    try:
        prepared = setup_records_of(directory)
        full = md.load_topology(str(Path(prepared) / "topology.pdb")) if prepared else None
    except Exception:  # noqa: BLE001
        full = None
    if full is None:
        return None
    if full.n_atoms == saved.n_atoms:
        kept = list(range(full.n_atoms))
    else:
        kept = [atom.index for atom in full.atoms if not atom.residue.is_water]
        if len(kept) != saved.n_atoms:
            return None
    position = {index: n for n, index in enumerate(kept)}
    mapped = [position.get(int(i)) for i in full_indices]
    if any(m is None for m in mapped):
        return None
    return np.array(mapped, dtype=int)


#: Coulomb's constant in kJ nm / (mol e^2).
COULOMB_KJ_NM = 138.935458

#: The dielectric constant of water at 298 K. Lower than any common water
#: model's (TIP3P's is about 94), so the finite-size estimate made with it is
#: the larger one.
WATER_DIELECTRIC = 78.4


def box_artefact_kjmol(q_ligand: float, q_receptor: float, volume_nm3: float,
                       bound_nm: float, bulk_nm: float, *,
                       dielectric: float = WATER_DIELECTRIC) -> float:
    """How much the periodic box shifts the curve between bound and bulk.

    Under Ewald summation two charges interact with each other's images and
    with the uniform background that neutralises each, and the potential of
    a unit charge near itself is ``1/r - xi/L + 2 pi r^2 / (3 V)`` to leading
    order. The constant cancels along the curve; the quadratic term, the
    background's, does not. Between the bound state and bulk it adds

        k q_L q_R / eps * 2 pi (r_u^2 - r_b^2) / (3 V)

    to the free energy, for point charges in a continuum of dielectric eps
    with no salt. Salt screens it, so this is an estimate of size and sign,
    not a correction.
    """
    return (COULOMB_KJ_NM * float(q_ligand) * float(q_receptor) / float(dielectric)
            * 2.0 * math.pi * (float(bulk_nm) ** 2 - float(bound_nm) ** 2)
            / (3.0 * float(volume_nm3)))


def _charges_and_box(prepared: Path, ligand_resname: str) -> dict[str, Any] | None:
    """The ligand's and the receptor's net charges, the ions, and the box.

    Read from the prepared system, since the saved trajectory has no charges
    and no water. The receptor is everything that is not the ligand, water or
    a single-atom ion: protein, cofactors, lipids.
    """
    import xml.etree.ElementTree as ET

    import mdtraj as md

    try:
        topology = md.load_topology(str(prepared / "topology.pdb"))
        system = ET.parse(str(prepared / "system.xml")).getroot()
        state = ET.parse(str(prepared / "state.xml")).getroot()
    except Exception:  # noqa: BLE001 - said by the caller
        return None
    nonbonded = next((force for force in system.iter("Force")
                      if force.get("type") == "NonbondedForce"), None)
    if nonbonded is None:
        return None
    charges = [float(particle.get("q", 0.0))
               for particle in nonbonded.find("Particles").iter("Particle")]
    if len(charges) != topology.n_atoms:
        return None
    ligand = receptor = 0.0
    ions = 0
    for atom in topology.atoms:
        residue = atom.residue
        if residue.name == ligand_resname:
            ligand += charges[atom.index]
        elif residue.is_water:
            continue
        elif _is_ion(residue):
            ions += 1
        else:
            receptor += charges[atom.index]
    box = state.find("PeriodicBoxVectors")
    if box is None:
        return None
    vectors = np.array([[float(box.find(axis).get(k)) for k in ("x", "y", "z")]
                        for axis in ("A", "B", "C")])
    return {"ligand_charge": round(ligand, 3), "receptor_charge": round(receptor, 3),
            "ions": ions, "box_volume_nm3": float(abs(np.linalg.det(vectors)))}


def _what_the_charges_do(window: Path, ligand_resname: str, *,
                         bound_nm: float, bulk_nm: float) -> dict[str, Any]:
    """The charges, the box's effect on the curve, and the sentence for it."""
    from fastmdxplora.simulation.pipeline import setup_records_of

    prepared = setup_records_of(window)
    found = _charges_and_box(Path(prepared), ligand_resname) if prepared else None
    if found is None:
        return {"not_checked": "The prepared system's charges could not be read."}
    q_ligand = found["ligand_charge"]
    if abs(q_ligand) < 0.5:
        return found
    shift = box_artefact_kjmol(q_ligand, found["receptor_charge"],
                               found["box_volume_nm3"], bound_nm, bulk_nm)
    found["box_artefact_kjmol"] = shift
    screening = (f", before the screening of the {found['ions']} ions in the box, "
                 "which reduces it" if found["ions"] else ", with no salt to screen it")
    found["said"] = (
        f"The ligand carries a net charge of {q_ligand:+.0f} and the receptor "
        f"{found['receptor_charge']:+.0f}, and nothing corrects the free energy "
        "for the periodic box they sit in. To leading order the box's "
        f"neutralising background shifts the curve by {shift:+.2f} kJ/mol "
        f"between {bound_nm:.2f} and {bulk_nm:.2f} nm in this "
        f"{found['box_volume_nm3']:.0f} nm^3 box, in water's dielectric"
        f"{screening}. A larger box shrinks it with the volume.")
    return found


def check_the_reference(
    directories: "dict[int, Any]",
    centres: "dict[int, float]",
    *,
    ligand_resname: str,
    site_selection: str,
    bound_at_nm: float,
    bulk_from_nm: float,
    temperature_K: float,
    cone: Any = None,
    allowed_kjmol: float,
    skip: float = 0.0,
) -> dict[str, Any]:
    """Measure the four things the conversion rests on, from the windows.

    ``bound_at_nm`` is where the free energy is lowest and ``bulk_from_nm``
    where the curve's bulk range begins; ``skip`` is the share of each window
    the recombination discarded. Returns a record of each check, ``refused``
    where the reference does not apply, and ``warnings``.
    """
    from fastmdxplora.lipids import is_bilayer
    from fastmdxplora.simulation.binding import KB_KJMOL
    from fastmdxplora.statistics import MINIMUM_EFFECTIVE_SAMPLES

    kt = KB_KJMOL * float(temperature_K)
    record: dict[str, Any] = {"refused": None, "warnings": []}
    order = sorted(directories)
    if not order:
        return record
    topology = _topology_of(directories[order[0]])
    if topology is None:
        record["not_checked"] = "The windows' trajectories are not on disk."
        return record
    try:
        groups = _groups(topology, ligand_resname, site_selection)
    except Exception as exc:  # noqa: BLE001
        record["not_checked"] = f"The ligand or site could not be found ({exc})."
        return record
    if groups["ligand"].size == 0 or groups["site"].size == 0:
        record["not_checked"] = "The ligand or site matches no atom in the trajectory."
        return record
    masses = _masses(topology)

    # ---- 2. a membrane -------------------------------------------------
    membrane = is_bilayer([residue.name for residue in topology.residues])
    record["membrane"] = bool(membrane)
    if membrane and cone is None:
        record["refused"] = (
            "The system has a bilayer, so bulk is a slab of water and not the "
            "isotropic solvent a standard binding free energy is measured "
            "against: the shell around the site meets the membrane at every "
            "radius, and the -2kT ln r the reference assumes does not hold. "
            "Run the windows under a cone pointing into the water, so the "
            "reference is the cap there, and the cap's openness is measured.")
        return record

    # ---- a charged ligand -----------------------------------------------
    # Not one of the four, and not refused: said, with its size, because the
    # conversion is taken in a periodic box and nothing corrects for it.
    record["charge"] = _what_the_charges_do(
        Path(directories[order[0]]), ligand_resname,
        bound_nm=float(bound_at_nm), bulk_nm=float(max(centres.values())))
    said = record["charge"].get("said")
    if said:
        record["warnings"].append(said)

    # ---- 1. is the outer shell open -------------------------------------
    axis_saved = None
    half_angle = None
    if cone is not None:
        half_angle = float(cone.half_angle_deg)
        axis_saved = _axis_atoms_in_saved(
            Path(directories[order[0]]), list(cone.axis_atoms or []), topology)
        if axis_saved is None and getattr(cone, "axis_selection", None):
            try:
                axis_saved = topology.select(str(cone.axis_selection))
            except Exception:  # noqa: BLE001
                axis_saved = None
            if axis_saved is not None and axis_saved.size == 0:
                axis_saved = None
    directions = fibonacci_directions()
    outer = [i for i in order if centres[i] >= bulk_from_nm]
    shells: list[dict[str, Any]] = []
    for index in outer:
        loaded = _load(Path(directories[index]), frames=GEOMETRY_FRAMES, skip=skip)
        if loaded is None:
            continue
        shares, radii = [], []
        for frame in range(loaded.n_frames):
            xyz = loaded.xyz[frame]
            cell = (loaded.unitcell_vectors[frame]
                    if loaded.unitcell_vectors is not None else None)
            site = _centre(xyz, groups["site"], masses)
            ligand_centre = _centre(xyz, groups["ligand"], masses)
            offset = ligand_centre - site
            if cell is not None:
                offset = _minimum_image(offset, cell)[0]
            radius = float(np.linalg.norm(offset))
            pose = xyz[groups["ligand_heavy"]] - ligand_centre
            reach = radius + float(np.max(np.linalg.norm(pose, axis=1))) + CONTACT_NM
            obstacles = obstacles_around(site, xyz[groups["obstacles"]], cell, reach)
            axis = None
            if axis_saved is not None:
                axis = site - _centre(xyz, axis_saved, masses)
                if cell is not None:
                    axis = _minimum_image(axis, cell)[0]
            cap = within_cap(directions, axis, half_angle)
            shares.append(open_share(obstacles, radius, pose, cap))
            radii.append(radius)
        if shares:
            shells.append({"window": int(index), "radius_nm": float(np.mean(radii)),
                           "open_share": float(np.mean(shares))})
    record["shell"] = {
        "windows": shells,
        "within_cone": bool(cone is not None and axis_saved is not None),
        "contact_nm": CONTACT_NM,
    }
    if cone is not None and axis_saved is None:
        record["warnings"].append(
            "The cone's axis atoms could not be found in the saved trajectory, "
            "so the whole shell was measured rather than the cap.")
    if shells:
        least = min(shells, key=lambda s: s["open_share"])
        share = least["open_share"]
        cost = kt * math.log(1.0 / share) if share > 0 else float("inf")
        record["shell"]["least_open"] = least
        record["shell"]["error_bound_kjmol"] = cost
        where = "cap" if record["shell"]["within_cone"] else "shell"
        if cost > allowed_kjmol:
            record["refused"] = (
                f"The {where} around the site is not open where the curve is "
                f"taken as bulk: at {least['radius_nm']:.2f} nm (window "
                f"{least['window']}) the ligand is free in {share:.0%} of it, "
                "the rest being protein or other solute. The reference assumes "
                f"all of it, so the answer would be too negative by up to "
                f"{cost:.1f} kJ/mol, against {allowed_kjmol} allowed. No "
                "length of run changes this: the windows have to reach "
                "further out, or run under a cone that points where the "
                "solvent is.")

    # ---- 3 and 4. the path and the orientation --------------------------
    # Only the protein's heavy atoms and the ligand: enough to superpose on
    # and to find backbone, and a tenth of a membrane system's atoms.
    wanted = (f"(protein and not element H) or resname {ligand_resname}")
    bound_index = min(order, key=lambda i: abs(centres[i] - bound_at_nm))
    reference = _load(Path(directories[bound_index]), frames=GEOMETRY_FRAMES,
                      skip=skip, selection=wanted)
    if reference is None:
        return record
    small = _groups(reference.topology, ligand_resname, site_selection)
    anchor = small["anchor"]
    target = reference.xyz[0][anchor]
    backbone_frames = []
    for frame in range(reference.n_frames):
        rotation, from_, to = kabsch(reference.xyz[frame][anchor], target)
        backbone_frames.append((reference.xyz[frame][small["backbone"]] - from_)
                               @ rotation + to)
    through: list[dict[str, Any]] = []
    unsampled: list[dict[str, Any]] = []
    for index in order:
        loaded = _load(Path(directories[index]), frames=ORIENTATION_FRAMES,
                       skip=skip, selection=wanted)
        if loaded is None or loaded.n_atoms != reference.n_atoms:
            continue
        axes = []
        for frame in range(loaded.n_frames):
            rotation, _from, _to = kabsch(loaded.xyz[frame][anchor], target)
            longest = principal_axis(loaded.xyz[frame][small["ligand_heavy"]])
            if longest is not None:
                axes.append(longest @ rotation)
        if len(axes) >= ORIENTATION_MINIMUM_FRAMES:
            said = orientation_samples(np.array(axes))
            if said["effective_samples"] is not None and (
                    not said["resolved"]
                    or said["effective_samples"] < MINIMUM_EFFECTIVE_SAMPLES):
                unsampled.append({"window": int(index),
                                  "centre_nm": float(centres[index]), **said})
        if not bound_at_nm < centres[index] < bulk_from_nm or not small["backbone"].size:
            continue
        # Where the ligand was in this window, placed in the bound state's
        # protein: does it overlap backbone in every frame of that state?
        first = loaded.xyz[0]
        rotation, from_, to = kabsch(first[anchor], target)
        ligand = (first[small["ligand_heavy"]] - from_) @ rotation + to
        blocked = all(
            float(np.min(np.linalg.norm(
                ligand[:, None, :] - backbone[None, :, :], axis=2))) < OVERLAP_NM
            for backbone in backbone_frames)
        if blocked:
            through.append({"window": int(index), "centre_nm": float(centres[index])})
    record["path"] = {"through_backbone": through, "overlap_nm": OVERLAP_NM}
    record["orientation"] = {"unsampled": unsampled}
    if through:
        listed = ", ".join(f"{w['window']} ({w['centre_nm']:.2f} nm)" for w in through)
        record["warnings"].append(
            "The pull took the ligand through the protein: in "
            f"{'window' if len(through) == 1 else 'windows'} "
            f"{listed} it sits where the bound state has backbone, in every "
            "frame of that state. Those windows sample a protein the pull "
            "deformed, and a free energy along such a path can be wrong by far "
            "more than its error bar. Seed along a way out the bound state "
            "has open, or run a longer pull so the protein can make way.")
    if unsampled:
        listed = ", ".join(f"{w['window']}" for w in unsampled)
        record["warnings"].append(
            "The ligand's orientation was not sampled in "
            f"{'window' if len(unsampled) == 1 else 'windows'} {listed}: "
            "it turned too slowly for the window to see more than a few "
            "orientations. A distance restrains where the ligand is and not "
            "how it is turned, so those windows describe the poses they "
            "happened to hold. Longer windows, or orientational restraints "
            "with their own correction, are what a ligand like this needs.")
    return record
