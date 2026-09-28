"""What the bilayer analyses share: where the bilayer is, and its geometry.

`area_per_lipid`, `bilayer_thickness` and `lipid_order` measure a lipid
bilayer, and each needs the same three things first: which atoms are its
lipids and their heads, where its centre is, and which leaflet each lipid is
in. They are worked out here once.

**The membrane normal is z.** A bilayer built by OpenMM lies in the xy plane,
and so does one from CHARMM-GUI or GROMACS; the membrane barostat couples x
and y together for that reason. The head groups are checked to form two
layers along z before anything is measured, and a system whose lipids do not
is refused rather than measured along the wrong axis.

**The centre of the bilayer is found across the periodic boundary.** A
bilayer can sit anywhere in z, including across the box face, so its centre
is not the mean of the lipid coordinates. The lipids fill one slab of the
periodic cell and the water the rest; the centre is the middle of the lipid
slab, found from the largest stretch of z that holds no lipid. Each head is
assigned to a leaflet by which side of that centre it is on, frame by frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.lipids import (
    BILAYER_MINIMUM_LIPIDS,
    is_lipid,
    is_sterol,
    lipid_count,
)
from fastmdxplora.refusals import StudyError

__all__ = [
    "Bilayer",
    "BilayerSeries",
    "Leaflets",
    "box_vectors",
    "leaflets",
    "CROSS_SECTION_PLANES_NM",
    "LIPID_PROBE_NM",
    "cross_section",
    "find_bilayer",
    "bilayer_centre",
]

#: Heights, relative to the bilayer centre, at which the protein's cross
#: section is taken. They span the hydrophobic core, where the protein
#: displaces acyl chains rather than head groups, so the area the lipids do
#: not have is the mean of the protein's section across it.
CROSS_SECTION_PLANES_NM = (-1.0, -0.5, 0.0, 0.5, 1.0)

#: Spacing of the grid the cross section is counted on.
GRID_SPACING_NM = 0.05

#: The radius of what approaches the protein in the hydrophobic core: an
#: acyl chain's methylene group. A lipid cannot reach a crevice narrower
#: than this, so the protein's area is what lies inside the outline such a
#: probe traces round it.
LIPID_PROBE_NM = 0.20

#: Van der Waals radii (Bondi 1964; H after Rowland and Taylor 1996), nm.
_VDW_NM = {"H": 0.110, "C": 0.170, "N": 0.155, "O": 0.152, "S": 0.180,
           "P": 0.180, "SE": 0.190}
_VDW_DEFAULT_NM = 0.170

#: Covalent radii (Cordero et al. 2008), nm, for finding bonds by distance.
_COVALENT_NM = {"H": 0.031, "C": 0.076, "N": 0.071, "O": 0.066, "P": 0.107,
                "S": 0.105}
_BOND_TOLERANCE_NM = 0.045

#: Phosphate heads closer than this to the bilayer centre are not in a
#: leaflet; more than a tenth of them there, and the lipids are not a
#: bilayer normal to z.
_MID_PLANE_NM = 1.0

#: Water residue names, which neither occupy the bilayer nor are counted.
_WATER = frozenset({"HOH", "WAT", "TIP", "TIP3", "TIP4", "TIP5", "SOL", "H2O",
                    "SPC", "T3P", "T4P"})


def _symbol(atom: Any) -> str:
    element = getattr(atom, "element", None)
    symbol = getattr(element, "symbol", None)
    if symbol:
        return str(symbol).upper()
    return "".join(ch for ch in atom.name if ch.isalpha())[:1].upper()


@dataclass
class Bilayer:
    """Where the bilayer's lipids are in a topology."""

    #: One head atom per lipid: the phosphorus of a phospholipid, the
    #: hydroxyl oxygen of a sterol.
    heads: np.ndarray
    #: Whether each head is a phosphorus.
    phosphate: np.ndarray
    #: Every heavy atom of every lipid, to find the centre from.
    lipid_atoms: np.ndarray
    #: Atoms of anything in the bilayer that is not lipid, water or an ion.
    occupants: np.ndarray
    #: How many of each lipid, by residue name.
    composition: dict[str, int] = field(default_factory=dict)


def find_bilayer(topology: md.Topology) -> Bilayer:
    """The heads, lipid atoms and occupants of the bilayer, or a refusal."""
    names = [residue.name for residue in topology.residues]
    if lipid_count(names) < BILAYER_MINIMUM_LIPIDS:
        raise StudyError(
            f"This system holds {lipid_count(names)} lipid molecule(s), and a "
            f"bilayer has at least {BILAYER_MINIMUM_LIPIDS}. There is no "
            "bilayer to measure.",
            code="analysis.system.inapplicable")
    heads: list[int] = []
    phosphate: list[bool] = []
    lipid_atoms: list[int] = []
    occupants: list[int] = []
    composition: dict[str, int] = {}
    for residue in topology.residues:
        atoms = list(residue.atoms)
        if is_lipid(residue.name):
            lipid_atoms.extend(a.index for a in atoms if _symbol(a) != "H")
            head = next((a for a in atoms if a.name.upper() == "P"), None)
            if head is not None:
                heads.append(head.index)
                phosphate.append(True)
            elif is_sterol(residue.name):
                oxygens = [a for a in atoms if _symbol(a) == "O"]
                named = [a for a in oxygens if a.name.upper() == "O3"]
                if named or oxygens:
                    heads.append((named or oxygens)[0].index)
                    phosphate.append(False)
            else:
                continue
            composition[residue.name] = composition.get(residue.name, 0) + 1
        elif residue.name.upper() not in _WATER and len(atoms) > 1:
            occupants.extend(a.index for a in atoms)
    return Bilayer(np.asarray(heads, dtype=int), np.asarray(phosphate, dtype=bool),
                   np.asarray(lipid_atoms, dtype=int),
                   np.asarray(occupants, dtype=int), composition)


def _wrapped(dz: np.ndarray, length: np.ndarray | float) -> np.ndarray:
    """A displacement along z brought into [-L/2, L/2)."""
    return dz - length * np.floor(dz / length + 0.5)


def bilayer_centre(z: np.ndarray, length: float) -> float:
    """The middle of the lipid slab in a periodic cell of height ``length``.

    The water slab is the longest run of z that holds (nearly) no lipid
    atom; the lipid slab is everything else, and its centre is the mean of
    the lipid coordinates measured from the middle of the water. Without a
    water slab (a box too thin to have one) the circular mean is used.
    """
    bins = max(16, int(np.floor(length / 0.1)))
    width = length / bins
    counts = np.bincount((np.mod(z, length) / width).astype(int) % bins,
                         minlength=bins)
    empty = counts <= 0.02 * counts.max()
    if not empty.any():
        angle = 2 * np.pi * z / length
        mean = np.arctan2(np.sin(angle).mean(), np.cos(angle).mean())
        return float(np.mod(mean * length / (2 * np.pi), length))
    doubled = np.concatenate([empty, empty])
    best_start, best_length, start = 0, 0, None
    for index, is_empty in enumerate(doubled):
        if is_empty and start is None:
            start = index
        if (not is_empty or index == len(doubled) - 1) and start is not None:
            stop = index if not is_empty else index + 1
            if min(stop - start, bins) > best_length:
                best_start, best_length = start, min(stop - start, bins)
            start = None
    gap_middle = (best_start + best_length / 2) * width
    shifted = np.mod(z - gap_middle, length)
    return float(np.mod(gap_middle + shifted.mean(), length))


def box_vectors(traj: md.Trajectory) -> np.ndarray:
    """The unit cell vectors, or a refusal where there are none.

    MDTraj builds them from lengths and angles with the first two in the xy
    plane, which is the plane of a bilayer built with its normal along z.
    """
    vectors = traj.unitcell_vectors
    if vectors is None:
        raise StudyError(
            "This trajectory carries no periodic box. A bilayer's area is the "
            "area of the box it spans, so without one there is nothing to "
            "divide.", code="analysis.system.inapplicable")
    return np.asarray(vectors, dtype=np.float64)


@dataclass
class Leaflets:
    centre: np.ndarray        # (frames,) bilayer centre in z, nm
    dz: np.ndarray            # (frames, heads) height of each head above it
    upper: np.ndarray         # (frames, heads) bool


def leaflets(traj: md.Trajectory, bilayer: Bilayer) -> Leaflets:
    vectors = box_vectors(traj)
    heights = vectors[:, 2, 2]
    z_lipid = traj.xyz[:, bilayer.lipid_atoms, 2].astype(np.float64)
    centre = np.array([bilayer_centre(z_lipid[f], heights[f])
                       for f in range(traj.n_frames)])
    z_heads = traj.xyz[:, bilayer.heads, 2].astype(np.float64)
    dz = _wrapped(z_heads - centre[:, None], heights[:, None])
    phosphorus = dz[:, bilayer.phosphate]
    if phosphorus.size:
        near_middle = float(np.mean(np.abs(phosphorus) < _MID_PLANE_NM))
        upper_share = float(np.mean(phosphorus > 0))
        if near_middle > 0.1 or not 0.25 <= upper_share <= 0.75:
            raise StudyError(
                f"The lipids' phosphates do not form two layers normal to z: "
                f"{near_middle:.0%} of them are within {_MID_PLANE_NM} nm of "
                f"the middle of the lipid slab, and {upper_share:.0%} are above "
                "it. A bilayer in the xy plane has almost none in the middle "
                "and half above. Measuring this as a bilayer would report "
                "numbers for a structure that is not there.",
                code="analysis.system.inapplicable")
    return Leaflets(centre, dz, dz > 0)


def _area_xy(vectors: np.ndarray) -> np.ndarray:
    return np.abs(vectors[:, 0, 0] * vectors[:, 1, 1]
                  - vectors[:, 0, 1] * vectors[:, 1, 0])


def _radii(topology: md.Topology, atoms: np.ndarray) -> np.ndarray:
    return np.array([_VDW_NM.get(_symbol(topology.atom(int(i))), _VDW_DEFAULT_NM)
                     for i in atoms])


def cross_section(xy: np.ndarray, radii: np.ndarray, cell: np.ndarray,
                  spacing: float = GRID_SPACING_NM, probe: float = 0.0) -> float:
    """Area a disc of radius ``probe`` cannot reach, among discs at ``xy``, nm^2.

    With ``probe`` zero, the area of the union of the discs. With a probe,
    the area inside the outline the probe traces as it rolls round them
    (a closing: the discs grown by the probe, then shrunk by it), with any
    region the probe cannot get into filled: the gap between packed helices
    is protein as far as a lipid is concerned.

    ``cell`` holds the two in-plane box vectors as rows. Counted on a grid of
    the cell's own fractional coordinates, so a disc across the box edge is
    counted once, on both sides of it.
    """
    if len(xy) == 0:
        return 0.0
    matrix = cell.T                       # columns are the box vectors
    inverse = np.linalg.inv(matrix)
    total = abs(np.linalg.det(matrix))
    lengths = np.linalg.norm(cell, axis=1)
    counts = np.maximum(1, np.ceil(lengths / spacing).astype(int))
    na, nb = int(counts[0]), int(counts[1])
    grown = radii + probe
    frac = xy @ inverse.T
    frac -= np.floor(frac)
    ci = np.floor(frac[:, 0] * na).astype(int)
    cj = np.floor(frac[:, 1] * nb).astype(int)
    # Grid lines along each vector are this far apart, measured across them.
    across = np.array([total / lengths[1] / na, total / lengths[0] / nb])
    reach = np.ceil(grown.max() / across).astype(int) + 1
    di, dj = np.meshgrid(np.arange(-reach[0], reach[0] + 1),
                         np.arange(-reach[1], reach[1] + 1), indexing="ij")
    di, dj = di.ravel(), dj.ravel()
    i = ci[:, None] + di[None, :]
    j = cj[:, None] + dj[None, :]
    centre = np.stack([(i + 0.5) / na - frac[:, 0, None],
                       (j + 0.5) / nb - frac[:, 1, None]], axis=-1)
    offset = centre @ matrix.T
    inside = (offset ** 2).sum(axis=-1) < (grown ** 2)[:, None]
    occupied = np.zeros(na * nb, dtype=bool)
    occupied[(np.mod(i, na) * nb + np.mod(j, nb))[inside]] = True
    if probe > 0:
        occupied = _closed(occupied.reshape(na, nb), matrix, na, nb, probe).ravel()
    return float(occupied.sum()) * total / (na * nb)


def _closed(grid: np.ndarray, matrix: np.ndarray, na: int, nb: int,
            probe: float) -> np.ndarray:
    """Shrink a grown occupancy grid by ``probe`` and fill what it encloses."""
    from scipy import ndimage

    if grid.all() or not grid.any():
        return grid
    # Rolled so an empty row and column lie on the edges, and nothing
    # occupied is cut by them: filling takes a hole that touches an edge for
    # open space.
    shifts = []
    for axis in (0, 1):
        empty = np.flatnonzero(~grid.any(axis=1 - axis))
        shifts.append(-int(empty[0]) if len(empty) else 0)
    grid = np.roll(grid, shifts, axis=(0, 1))
    k = np.array([np.linalg.norm(matrix[:, 0]) / na,
                  np.linalg.norm(matrix[:, 1]) / nb])
    ra, rb = (np.ceil(probe / k).astype(int) + 1)
    oi, oj = np.meshgrid(np.arange(-ra, ra + 1), np.arange(-rb, rb + 1), indexing="ij")
    step = np.stack([oi / na, oj / nb], axis=-1) @ matrix.T
    element = (step ** 2).sum(axis=-1) <= probe ** 2
    padded = np.pad(grid, ((ra, ra), (rb, rb)), mode="wrap")
    shrunk = ndimage.binary_erosion(padded, structure=element)[ra:-ra, rb:-rb]
    return ndimage.binary_fill_holes(shrunk)


class BilayerSeries(Analysis):
    """What the two per-frame bilayer measures share."""

    time_series = True
    honours_selection = False
    requires_bilayer = True
    _traj_for_plot: md.Trajectory | None = None

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        if self._traj_for_plot is not None:
            x = self.frame_axis(self._traj_for_plot)[0]
        else:
            x = np.arange(len(result))
        ax.plot(x, result, linewidth=1.2)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        return self.frame_axis(self._traj_for_plot)[1]

    def _note_composition(self, bilayer: Bilayer, sides: Leaflets) -> None:
        upper = sides.upper.sum(axis=1)
        lower = (~sides.upper).sum(axis=1)
        self.findings["bilayer"] = {
            "lipids": int(len(bilayer.heads)),
            "composition": dict(sorted(bilayer.composition.items())),
            "per_leaflet_first_frame": [int(upper[0]), int(lower[0])],
            "normal": "z",
        }
        if np.any(upper != upper[0]):
            self.findings["leaflet_changes"] = (
                f"The number of lipids in the upper leaflet ranges from "
                f"{int(upper.min())} to {int(upper.max())} over the run. A lipid "
                "crossing the bilayer (flip-flop) takes hours on the "
                "experimental clock, so a change within a simulation is a "
                "head group wandering near the middle, a bilayer that has "
                "come apart, or a lipid that has left it.")
