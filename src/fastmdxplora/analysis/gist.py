"""Which waters would be costly to displace: grid inhomogeneous solvation
theory (GIST).

Space about a site (the ligand, or ``center_selection``) is cut into cubes
``voxel_nm`` wide, and in each the waters found there over the frames are
described against bulk water (Nguyen, Young and Gilson, J. Chem. Phys. 137,
044101 (2012); Ramsey et al., J. Comput. Chem. 37, 2029 (2016)):

- **density**, ``g``, the waters' number density there over the bulk's;
- **energy**, each water's interaction with the solute (``E_sw``) and half
  its interaction with every other water (``E_ww``), less the bulk water's
  own ``E_ww``, from the study's force field (``setup/system.xml``): Coulomb
  and Lennard-Jones between molecules, by the minimum image, to
  ``cutoff_nm``;
- **translational entropy**, to first order, ``-TS_trans = k T rho0 g ln g``
  per unit volume, zero for water at bulk density;
- **orientational entropy**, from each water's nearest neighbour in
  orientation among the waters in its cube (the Kozachenko-Leonenko
  estimator over the rotation group), zero for waters oriented at random as
  in bulk.

Each is given per unit volume (``_dens``, kJ/mol/nm^3), so summing over a
region gives that region's contribution in kJ/mol. The free energy of the
water in a region, ``dA = dE_sw + dE_ww - T dS``, is what displacing it
would cost a ligand that took its place, before the ligand's own
interactions are counted: positive where the water is unhappy and a ligand
gains by removing it.

Each frame is fitted on the protein's alpha carbons to the first frame
before the waters are binned, so the grid moves with the protein; the
energies are computed in the frame as stored, where the periodic images are
right.

Four things to know.

- **The water has to be in the frames.** Only a study that stored every
  atom (``simulation.save_selection: all``) can be analysed.
- **Long-range electrostatics are cut off.** Energies are summed to the
  cutoff with no Ewald correction, as cpptraj's GIST does by default; each
  water's ``E_ww`` is taken against the bulk's computed the same way, which
  removes most of what is left out.
- **The orientational entropy needs many waters in a cube.** It is given
  only where a cube held at least ``min_waters`` (20) over the run; the rest
  are left at zero and counted.
- **Higher-order correlations are left out** (two waters' joint ordering),
  as in GIST generally: the entropies are first-order and overstate the
  loss.

Not compared with cpptraj's GIST on one trajectory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError

#: kJ/mol per (e^2/nm), Coulomb's constant as OpenMM has it.
COULOMB = 138.935458
#: Boltzmann's constant, kJ/mol/K.
KB = 0.0083144626
#: Euler's constant, from the nearest-neighbour estimator.
EULER = 0.5772156649015329
WATER_NAMES = {"HOH", "WAT", "SOL", "TIP3", "TIP4", "TIP5", "T3P", "T4P", "SPC", "OPC"}
#: Waters whose energies are taken together.
_WATERS_AT_ONCE = 24


def orientational_entropy(quaternions: np.ndarray) -> float:
    """The orientational entropy of these orientations, in units of k,
    against orientations at random: the Kozachenko-Leonenko nearest-neighbour
    estimate over the rotation group, ``gamma + mean(ln(N V_i))``, where
    ``V_i`` is the share of all rotations within the angle to its nearest
    neighbour, ``(w - sin w) / pi``. Zero, give or take its noise, for
    random orientations; below zero for ordered ones."""
    q = np.asarray(quaternions, dtype=np.float64)
    n = len(q)
    if n < 2:
        return 0.0
    closeness = np.abs(q @ q.T)
    np.fill_diagonal(closeness, -1.0)
    nearest = np.clip(closeness.max(axis=1), -1.0, 1.0)
    angle = 2.0 * np.arccos(nearest)
    share = np.maximum((angle - np.sin(angle)) / np.pi, 1e-300)
    return float(EULER + np.mean(np.log((n - 1) * share)))


def _quaternions(rotations: np.ndarray) -> np.ndarray:
    """Unit quaternions of rotation matrices (n, 3, 3)."""
    r = rotations
    w = np.sqrt(np.clip(1 + r[:, 0, 0] + r[:, 1, 1] + r[:, 2, 2], 0, None)) / 2
    x = np.sqrt(np.clip(1 + r[:, 0, 0] - r[:, 1, 1] - r[:, 2, 2], 0, None)) / 2
    y = np.sqrt(np.clip(1 - r[:, 0, 0] + r[:, 1, 1] - r[:, 2, 2], 0, None)) / 2
    z = np.sqrt(np.clip(1 - r[:, 0, 0] - r[:, 1, 1] + r[:, 2, 2], 0, None)) / 2
    x = np.copysign(x, r[:, 2, 1] - r[:, 1, 2])
    y = np.copysign(y, r[:, 0, 2] - r[:, 2, 0])
    z = np.copysign(z, r[:, 1, 0] - r[:, 0, 1])
    return np.stack([w, x, y, z], axis=1)


def water_frames(oxygen: np.ndarray, first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Each water's orientation as a rotation (n, 3, 3), columns its axes:
    x along the HOH bisector, y in the plane towards the first hydrogen,
    z normal to the plane."""
    a, b = first - oxygen, second - oxygen
    x = a / np.linalg.norm(a, axis=1, keepdims=True) + b / np.linalg.norm(b, axis=1, keepdims=True)
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    z = np.cross(a, b)
    z /= np.linalg.norm(z, axis=1, keepdims=True)
    y = np.cross(z, x)
    return np.stack([x, y, z], axis=2)


def _minimum_image(delta: np.ndarray, box: np.ndarray | None) -> np.ndarray:
    if box is None:
        return delta
    fractional = delta @ np.linalg.inv(box)
    return (fractional - np.round(fractional)) @ box


def pair_energies(xyz: np.ndarray, box: np.ndarray | None, atoms: np.ndarray,
                  others: np.ndarray, charge: np.ndarray, sigma: np.ndarray,
                  epsilon: np.ndarray, cutoff: float) -> np.ndarray:
    """The Coulomb and Lennard-Jones energy (kJ/mol) between each of
    ``atoms`` (rows of (molecules, atoms in one)) and all of ``others``,
    by the minimum image to ``cutoff``, Lorentz-Berthelot combined."""
    found = np.zeros(len(atoms))
    for begin in range(0, len(atoms), _WATERS_AT_ONCE):
        block = atoms[begin:begin + _WATERS_AT_ONCE]
        flat = block.ravel()
        delta = _minimum_image(xyz[others][None, :, :] - xyz[flat][:, None, :], box)
        r = np.linalg.norm(delta, axis=2)
        near = (r < cutoff) & (r > 0)
        r = np.where(near, r, 1.0)
        qq = charge[flat][:, None] * charge[others][None, :]
        s = 0.5 * (sigma[flat][:, None] + sigma[others][None, :])
        e = np.sqrt(epsilon[flat][:, None] * epsilon[others][None, :])
        s6 = (s / r) ** 6
        energy = np.where(near, COULOMB * qq / r + 4 * e * (s6 * s6 - s6), 0.0)
        found[begin:begin + len(block)] = energy.sum(axis=1).reshape(len(block), -1).sum(axis=1)
    return found


class GIST(Analysis):
    """Grid inhomogeneous solvation theory about a site.

    Options
    -------
    ligand_resname : str
        Ligand residue name (e.g. ``"LIG"``). Supplied automatically by the
        orchestrator from the setup manifest; the grid is centred on it.
    center_selection : str, default ""
        Atoms whose centre in the first frame is the grid's centre. Empty:
        the ligand's.
    size_nm : float, default 2.0
        The grid's edge.
    voxel_nm : float, default 0.05
        Each cube's edge.
    cutoff_nm : float, default 1.0
        The cutoff of the energies.
    min_waters : int, default 20
        The fewest waters a cube has to hold over the run for its
        orientational entropy to be estimated.
    temperature_K : float, default 300.0
        The run's temperature, which the entropies are multiplied by.
    system_xml, topology_pdb : str, default ""
        The force field and the structure it was built for. Empty: the
        study's ``setup/system.xml`` and ``setup/topology.pdb``.

    Output
    ------
    ``gist.dat`` -- comma-separated, one row per cube that held water:
    ``x_nm``, ``y_nm``, ``z_nm`` (its centre, in the first frame), ``waters``,
    ``g``, ``dE_sw_dens``, ``dE_ww_dens``, ``TdS_trans_dens``,
    ``TdS_orient_dens`` and ``dA_dens`` (kJ/mol/nm^3).
    ``gist_g.dx`` and ``gist_dA.dx`` -- the density and the free energy
    density as OpenDX maps.
    """

    name = "gist"
    description = "Grid inhomogeneous solvation theory"
    honours_selection = False
    #: It needs every water stored and a site to describe: asked for.
    requires_naming = True

    def __init__(self, *, ligand_resname: str | None = None, center_selection: str = "",
                 size_nm: float = 2.0, voxel_nm: float = 0.05, cutoff_nm: float = 1.0,
                 min_waters: int = 20, temperature_K: float = 300.0,
                 system_xml: str = "", topology_pdb: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.ligand_resname = ligand_resname
        self.center_selection = str(center_selection or "")
        self.size_nm, self.voxel_nm = float(size_nm), float(voxel_nm)
        self.cutoff_nm, self.min_waters = float(cutoff_nm), int(min_waters)
        self.temperature_K = float(temperature_K)
        self.system_xml, self.topology_pdb = str(system_xml or ""), str(topology_pdb or "")
        for name, value, low, high in (("size_nm", self.size_nm, 0.5, 5.0),
                                       ("voxel_nm", self.voxel_nm, 0.02, 0.2),
                                       ("cutoff_nm", self.cutoff_nm, 0.6, 2.0),
                                       ("temperature_K", self.temperature_K, 1.0, 1000.0)):
            if not low <= value <= high:
                raise StudyError(f"`{name}` is from {low:g} to {high:g}; it was {value:g}.",
                                 code="analysis.option.out_of_range")
        if self.min_waters < 2:
            raise StudyError(f"`min_waters` is at least 2; it was {self.min_waters}.",
                             code="analysis.option.out_of_range")
        self.options.update(center_selection=self.center_selection, size_nm=self.size_nm,
                            voxel_nm=self.voxel_nm, cutoff_nm=self.cutoff_nm,
                            min_waters=self.min_waters, temperature_K=self.temperature_K)
        self._maps: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    def _parameters(self, traj: md.Trajectory) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Each stored atom's charge, sigma and epsilon, from the study's
        force field."""
        from openmm import XmlSerializer

        from fastmdxplora.analysis.mmgbsa import MMGBSA

        reader = MMGBSA(system_xml=self.system_xml, topology_pdb=self.topology_pdb,
                        output_dir=self.output_dir.parent)
        reader.study_dir = self.study_dir
        system_path, topology_path = reader._files()
        system = XmlSerializer.deserialize(system_path.read_text(encoding="utf-8"))
        whole = md.load_topology(str(topology_path))
        if whole.n_atoms != system.getNumParticles():
            raise StudyError(
                f"{topology_path.name} holds {whole.n_atoms} atoms and {system_path.name} "
                f"{system.getNumParticles()} particles; they are not one system.",
                code="analysis.data.not_this_system")
        if traj.n_atoms != whole.n_atoms:
            raise StudyError(
                f"The frames hold {traj.n_atoms} of the system's {whole.n_atoms} atoms. GIST "
                "needs every water, so only a run that stored every atom "
                "(`simulation.save_selection: all`) can be analysed.",
                code="analysis.data.absent")
        forces = [f for f in system.getForces() if type(f).__name__ == "NonbondedForce"]
        if not forces:
            raise StudyError("The system has no NonbondedForce.",
                             code="analysis.system.inapplicable")
        if any(type(f).__name__ == "CustomNonbondedForce" for f in system.getForces()):
            raise StudyError(
                "The force field puts part of its nonbonded terms in a "
                "CustomNonbondedForce, which this does not read.",
                code="analysis.system.inapplicable")
        force = forces[0]
        values = np.array([[v._value for v in force.getParticleParameters(i)]
                           for i in range(force.getNumParticles())])
        return values[:, 0], values[:, 1], values[:, 2]

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        from scipy.spatial import cKDTree

        from fastmdxplora.analysis.pore_profile import _fitted

        topology = traj.topology
        charge, sigma, epsilon = self._parameters(traj)
        waters = []
        for residue in topology.residues:
            if residue.name.upper() not in WATER_NAMES:
                continue
            atoms = list(residue.atoms)
            oxygen = [a.index for a in atoms if a.element is not None and a.element.symbol == "O"]
            hydrogens = [a.index for a in atoms if a.element is not None and a.element.symbol == "H"]
            if len(oxygen) == 1 and len(hydrogens) == 2:
                waters.append([oxygen[0], hydrogens[0], hydrogens[1]]
                              + [a.index for a in atoms if a.index not in
                                 (oxygen[0], *hydrogens)])
        if len(waters) < 50:
            raise StudyError(
                f"The frames hold {len(waters)} water molecule(s); GIST describes the "
                "water about a site, and needs the solvent stored.",
                code="analysis.data.absent")
        width = max(len(w) for w in waters)
        if any(len(w) != width for w in waters):
            raise StudyError("The waters are not all of one model.",
                             code="analysis.system.inapplicable")
        water_atoms = np.array(waters)
        is_water = np.zeros(traj.n_atoms, dtype=bool)
        is_water[water_atoms.ravel()] = True
        solute = np.flatnonzero(~is_water)
        all_water = np.flatnonzero(is_water)
        if not len(solute):
            raise StudyError("The frames hold water and nothing else.",
                             code="analysis.system.inapplicable")

        said = self.center_selection or (f"resname {self.ligand_resname}"
                                         if self.ligand_resname else "")
        if not said:
            raise StudyError(
                "The study names no ligand; name the site's atoms in `center_selection`.",
                code="analysis.data.absent")
        try:
            centred_on = topology.select(said)
        except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
            raise StudyError(f"{said!r} is not a selection MDTraj reads: {exc}",
                             code="analysis.selection.invalid") from exc
        if not len(centred_on):
            raise StudyError(f"{said!r} matched no atoms.", code="analysis.selection.empty")
        alpha = topology.select("name CA and not water")
        fit_on = alpha if len(alpha) >= 3 else solute
        reference = traj.xyz[0, fit_on].astype(np.float64)
        centre = traj.xyz[0, centred_on].astype(np.float64).mean(axis=0)
        cells = int(round(self.size_nm / self.voxel_nm))
        origin = centre - cells * self.voxel_nm / 2
        volume = self.voxel_nm ** 3

        boxes = (None if traj.unitcell_vectors is None
                 else np.asarray(traj.unitcell_vectors, dtype=np.float64))
        kT = KB * self.temperature_K
        frames = traj.n_frames
        count = np.zeros((cells,) * 3)
        e_sw = np.zeros((cells,) * 3)
        e_ww = np.zeros((cells,) * 3)
        orientations: dict[int, list[np.ndarray]] = {}
        bulk_energy, far_waters, far_volume = [], 0.0, 0.0
        rng = np.random.default_rng(0)

        for frame in range(frames):
            xyz = traj.xyz[frame].astype(np.float64)
            box = None if boxes is None else boxes[frame]
            # Where each water is in the first frame's place, the protein fitted.
            moved = _fitted(xyz[None], fit_on, reference)[0]
            oxygen_here = moved[water_atoms[:, 0]]
            cell = np.floor((oxygen_here - origin) / self.voxel_nm).astype(int)
            inside = np.all((cell >= 0) & (cell < cells), axis=1)
            chosen = np.flatnonzero(inside)
            # Bulk: waters well away from the solute, their own E_ww.
            tree = cKDTree(xyz[solute])
            distance, _ = tree.query(xyz[water_atoms[:, 0]], k=1)
            far = np.flatnonzero(distance > max(1.2, self.cutoff_nm))
            if box is not None and len(far):
                points = rng.random((2000, 3)) @ box
                reach, _ = tree.query(points, k=1)
                share = float((reach > max(1.2, self.cutoff_nm)).mean())
                far_volume += share * abs(float(np.linalg.det(box)))
                far_waters += len(far)
            sample = rng.choice(far, size=min(len(far), 50), replace=False) if len(far) else far
            def water_water(rows: np.ndarray, xyz: np.ndarray = xyz,
                            box: np.ndarray | None = box) -> np.ndarray:
                """Half of each water's energy with every other water."""
                total = np.zeros(len(rows))
                for k, molecule in enumerate(water_atoms[rows]):
                    others = all_water[~np.isin(all_water, molecule)]
                    total[k] = 0.5 * pair_energies(xyz, box, molecule[None], others, charge,
                                                   sigma, epsilon, self.cutoff_nm)[0]
                return total

            if len(sample):
                bulk_energy.extend(water_water(sample).tolist())
            if not len(chosen):
                continue
            sw = pair_energies(xyz, box, water_atoms[chosen], solute, charge, sigma, epsilon,
                               self.cutoff_nm)
            ww = water_water(chosen)
            place = cell[chosen]
            np.add.at(count, tuple(place.T), 1)
            np.add.at(e_sw, tuple(place.T), sw)
            np.add.at(e_ww, tuple(place.T), ww)
            turned = water_frames(moved[water_atoms[chosen, 0]], moved[water_atoms[chosen, 1]],
                                  moved[water_atoms[chosen, 2]])
            flat = np.ravel_multi_index(tuple(place.T), (cells,) * 3)
            for index, q in zip(flat, _quaternions(turned)):
                orientations.setdefault(int(index), []).append(q)

        if not bulk_energy:
            raise StudyError(
                "No water was found farther than the cutoff from the solute, so the bulk "
                "the waters are measured against is not in the box. A larger box is needed.",
                code="analysis.system.inapplicable")
        bulk_e_ww = float(np.mean(bulk_energy))
        rho0 = far_waters / far_volume if far_volume > 0 else 33.4
        expected = rho0 * volume * frames
        g = count / expected
        with np.errstate(divide="ignore", invalid="ignore"):
            per_water_sw = np.where(count > 0, e_sw / np.maximum(count, 1), 0.0)
            per_water_ww = np.where(count > 0, e_ww / np.maximum(count, 1) - bulk_e_ww, 0.0)
            density = count / (frames * volume)
            trans = np.where(g > 0, kT * rho0 * g * np.log(np.where(g > 0, g, 1.0)), 0.0)
        orient = np.zeros_like(g)
        estimated = 0
        for index, held in orientations.items():
            if len(held) >= self.min_waters:
                s = orientational_entropy(np.array(held))
                orient.flat[index] = -kT * s * density.flat[index]
                estimated += 1
        de_sw = per_water_sw * density
        de_ww = per_water_ww * density
        # -T dS is positive where the water is more ordered than in bulk.
        da = de_sw + de_ww + trans + orient

        holding = np.argwhere(count > 0)
        centres = origin + (holding + 0.5) * self.voxel_nm
        pick = tuple(holding.T)
        table = pd.DataFrame({
            "x_nm": np.round(centres[:, 0], 4), "y_nm": np.round(centres[:, 1], 4),
            "z_nm": np.round(centres[:, 2], 4), "waters": count[pick].astype(int),
            "g": np.round(g[pick], 4), "dE_sw_dens": np.round(de_sw[pick], 4),
            "dE_ww_dens": np.round(de_ww[pick], 4),
            "TdS_trans_dens": np.round(trans[pick], 4),
            "TdS_orient_dens": np.round(orient[pick], 4), "dA_dens": np.round(da[pick], 4)})
        self._maps = {"g": g, "dA": da, "origin": origin}

        def total(values: np.ndarray) -> float:
            return float(values.sum() * volume)

        self.findings["region"] = {
            "centre_nm": [round(float(v), 4) for v in centre], "centred_on": said,
            "size_nm": cells * self.voxel_nm, "voxel_nm": self.voxel_nm,
            "frames": frames, "fitted_on": f"{len(fit_on)} atoms of the solute, to the "
                                           "first frame",
        }
        self.findings["bulk"] = {
            "density_per_nm3": float(rho0), "e_ww_kj_per_mol": bulk_e_ww,
            "from": f"waters farther than {max(1.2, self.cutoff_nm):g} nm from the solute",
        }
        self.findings["totals_kj_per_mol"] = {
            "dE_sw": total(de_sw), "dE_ww": total(de_ww),
            "minus_TdS_trans": total(trans), "minus_TdS_orient": total(orient),
            "dA": total(da),
            "meaning": "summed over the region: the water there against bulk, which a "
                       "ligand filling the region would remove",
        }
        self.findings["orientation"] = {
            "cubes_estimated": int(estimated),
            "cubes_with_water": int(len(holding)),
            "left_at_zero": f"cubes that held fewer than {self.min_waters} waters over the run",
        }
        self.findings["method"] = {
            "energies": f"Coulomb and Lennard-Jones from the study's force field, minimum "
                        f"image, cut off at {self.cutoff_nm:g} nm, no Ewald correction",
            "entropies": "first-order: translational from g ln g, orientational by the "
                         "nearest-neighbour estimator over rotations",
            "reference": "Nguyen, Young and Gilson, J. Chem. Phys. 137, 044101 (2012)",
            "not_compared": "with cpptraj's GIST on one trajectory",
        }
        return table

    # ------------------------------------------------------------------
    def save_data(self, result: pd.DataFrame, path: Path) -> Path:
        written = super().save_data(result, path)
        if self._maps is not None:
            for key in ("g", "dA"):
                path.with_name(f"gist_{key}.dx").write_text(
                    _dx(self._maps[key], self._maps["origin"] * 10.0, self.voxel_nm * 10.0),
                    encoding="utf-8")
        return written

    def default_xlabel(self) -> str | None:
        return "Water density over bulk, g"

    def default_ylabel(self) -> str | None:
        return "Free energy density (kJ/mol/nm$^3$)"

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        ax.scatter(result["g"], result["dA_dens"], s=4, alpha=0.5, linewidths=0)
        ax.axhline(0, color="0.4", linewidth=0.8)
        ax.axvline(1, color="0.4", linewidth=0.8, linestyle="--")


def _dx(values: np.ndarray, origin_angstrom: np.ndarray, spacing_angstrom: float) -> str:
    """An OpenDX map, origin and spacing in Å, as molecular viewers read it."""
    nx, ny, nz = values.shape
    head = [f"object 1 class gridpositions counts {nx} {ny} {nz}",
            "origin " + " ".join(f"{v:.4f}" for v in origin_angstrom),
            f"delta {spacing_angstrom:.4f} 0 0", f"delta 0 {spacing_angstrom:.4f} 0",
            f"delta 0 0 {spacing_angstrom:.4f}",
            f"object 2 class gridconnections counts {nx} {ny} {nz}",
            f"object 3 class array type double rank 0 items {values.size} data follows"]
    flat = values.ravel()
    rows = [" ".join(f"{v:.6g}" for v in flat[k:k + 3]) for k in range(0, len(flat), 3)]
    tail = ['attribute "dep" string "positions"', 'object "regular positions regular '
            'connections" class field', 'component "positions" value 1',
            'component "connections" value 2', 'component "data" value 3']
    return "\n".join(head + rows + tail) + "\n"


register_analysis(GIST.name, GIST)
