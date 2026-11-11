"""How tightly the ligand is held: its MM/GBSA binding energy, frame by frame.

From one trajectory of the complex, each frame's energy of the complex less
that of the receptor and of the ligand taken apart, each in a continuum of
water (Kollman et al., Acc. Chem. Res. 33, 889 (2000); Genheden and Ryde,
Expert Opin. Drug Discov. 10, 449 (2015))::

    dG = G(complex) - G(receptor) - G(ligand)
       = dE_elec + dE_vdW + dG_GB + dG_SA

The force field is the study's own, read from ``setup/system.xml``: the
charges and Lennard-Jones parameters of every atom, the ligand's included,
and the exceptions between bonded atoms. The solvent is OpenMM's
generalised Born model of Onufriev, Bashford and Case (OBC2, AMBER's
``igb=5``), with mbondi2 radii and the standard screening factors, and the
nonpolar part from the Born radii by the ACE approximation at 0.0054
kcal/mol/Å^2 (OpenMM's default). The interactions are counted with no
cutoff, as a continuum has no box.

Since the receptor and ligand are cut from the same frame as the complex,
their bonds, angles and torsions are the complex's and cancel: they are not
computed. What is left are the interactions between the two and how the
solvent's screening of each changes when they meet.

**It is not a binding free energy.** The solute's loss of entropy on binding
is left out (a normal-mode or quasi-harmonic estimate costs more than the
rest together and is noisier than the difference it corrects), as is the
cost of reorganising either partner, since both are taken from the bound
frames. The numbers are usually several times larger than measured binding
free energies, and are for ranking ligands bound to one receptor against
each other, not for comparison with an experiment.

**The partners are placed together.** The frames are periodic, and a ligand
stored in the next cell from its receptor would be counted as unbound.
Each frame, each molecule of the complex is moved by whole cell vectors to
the image nearest the molecules already placed, the receptor's first chain
first.

Each frame's value is a time series like any other, with its mean, error
and independent samples; the parts are written beside it.
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

KJ_PER_KCAL = 4.184
#: The parts, in the order of their force groups.
PARTS = ("elec", "vdw", "gb", "sa")
#: The forces whose terms are the bonded ones, which cancel between the
#: complex and its partners cut from the same frame.
_BONDED = {"HarmonicBondForce", "HarmonicAngleForce", "PeriodicTorsionForce",
           "RBTorsionForce", "CMAPTorsionForce", "CustomTorsionForce",
           "CustomBondForce", "CustomAngleForce", "CMMotionRemover",
           "MonteCarloBarostat", "MonteCarloMembraneBarostat",
           "MonteCarloAnisotropicBarostat", "MonteCarloFlexibleBarostat"}


def _first_heavy_distance(placed: np.ndarray, moving: np.ndarray) -> float:
    from scipy.spatial import cKDTree

    return float(cKDTree(placed).query(moving, k=1)[0].min())


def placed_together(xyz: np.ndarray, box: np.ndarray | None,
                    molecules: list[np.ndarray]) -> np.ndarray:
    """``xyz`` (atoms, 3) with each molecule after the first moved by whole
    cell vectors (``box`` rows) to the image nearest those already placed."""
    if box is None or len(molecules) < 2:
        return xyz
    xyz = xyz.copy()
    shifts = np.array([i * box[0] + j * box[1] + k * box[2]
                       for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)])
    placed = xyz[molecules[0]]
    for atoms in molecules[1:]:
        best = min(shifts, key=lambda s: _first_heavy_distance(placed, xyz[atoms] + s))
        xyz[atoms] += best
        placed = np.concatenate([placed, xyz[atoms]])
    return xyz


class _Energies:
    """One solute's nonbonded and continuum-solvent energies, part by part."""

    def __init__(self, openmm: Any, nonbonded: Any, atoms: np.ndarray,
                 gb: np.ndarray, platform: Any) -> None:
        mm = openmm
        self.atoms = atoms
        place = {int(a): k for k, a in enumerate(atoms)}
        system = mm.System()
        for _ in atoms:
            system.addParticle(1.0)
        elec, vdw = mm.NonbondedForce(), mm.NonbondedForce()
        polar, nonpolar = mm.GBSAOBCForce(), mm.GBSAOBCForce()
        for force in (elec, vdw):
            force.setNonbondedMethod(mm.NonbondedForce.NoCutoff)
            force.setUseDispersionCorrection(False)
        for force in (polar, nonpolar):
            force.setNonbondedMethod(mm.GBSAOBCForce.NoCutoff)
            force.setSoluteDielectric(1.0)
            force.setSolventDielectric(78.5)
        polar.setSurfaceAreaEnergy(0.0)
        for k, atom in enumerate(atoms):
            charge, sigma, epsilon = nonbonded.getParticleParameters(int(atom))
            elec.addParticle(charge, sigma, 0.0)
            vdw.addParticle(0.0, sigma, epsilon)
            radius, scale = gb[k]
            polar.addParticle(charge, radius, scale)
            nonpolar.addParticle(0.0, radius, scale)
        for index in range(nonbonded.getNumExceptions()):
            i, j, product, sigma, epsilon = nonbonded.getExceptionParameters(index)
            if i in place and j in place:
                elec.addException(place[i], place[j], product, sigma, 0.0)
                vdw.addException(place[i], place[j], 0.0, sigma, epsilon)
        for group, force in enumerate((elec, vdw, polar, nonpolar)):
            force.setForceGroup(group)
            system.addForce(force)
        self.context = mm.Context(system, mm.VerletIntegrator(0.001), platform)

    def of(self, xyz_nm: np.ndarray) -> np.ndarray:
        """The four parts, kJ/mol, at these positions (the solute's atoms)."""
        self.context.setPositions(xyz_nm)
        return np.array([
            self.context.getState(getEnergy=True, groups={group})
            .getPotentialEnergy()._value for group in range(4)])


class MMGBSA(Analysis):
    """The MM/GBSA binding energy of the ligand to the receptor, per frame.

    Options
    -------
    ligand_resname : str
        Ligand residue name (e.g. ``"LIG"``). Supplied automatically by the
        orchestrator from the setup manifest; ``ligand_selection`` overrides it.
    ligand_selection : str, default ""
        The ligand's atoms. Empty: the residues named by the study's ligand
        (``ligand_resname``).
    receptor_selection : str, default "protein"
        The receptor's atoms.
    system_xml : str, default ""
        The OpenMM system whose force field is used. Empty: the study's
        ``setup/system.xml``.
    topology_pdb : str, default ""
        The structure ``system_xml`` was built from, every atom of it in its
        order. Empty: the study's ``setup/topology.pdb``.

    Output
    ------
    ``mmgbsa.dat`` -- one column, kJ/mol, one row per frame: the binding
    energy. ``mmgbsa_parts.dat`` -- comma-separated, one row per frame:
    ``elec``, ``vdw``, ``gb``, ``sa`` and ``total``, each the complex's less
    its partners', kJ/mol.
    """

    name = "mmgbsa"
    description = "MM/GBSA binding energy"
    time_series = True
    honours_selection = False
    #: An afternoon's computing on a long run, and a number about binding
    #: that is not a free energy: asked for, not offered.
    requires_naming = True

    def __init__(self, *, ligand_resname: str | None = None,
                 ligand_selection: str = "", receptor_selection: str = "protein",
                 system_xml: str = "", topology_pdb: str = "",
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.ligand_resname = ligand_resname
        self.ligand_selection = str(ligand_selection or "")
        self.receptor_selection = str(receptor_selection or "protein")
        self.system_xml, self.topology_pdb = str(system_xml or ""), str(topology_pdb or "")
        self.options.update(ligand_selection=self.ligand_selection,
                            receptor_selection=self.receptor_selection)
        self._parts: pd.DataFrame | None = None
        self._traj_for_plot: md.Trajectory | None = None

    # ------------------------------------------------------------------
    def _files(self) -> tuple[Path, Path]:
        setup = (self.study_dir / "setup") if self.study_dir is not None else None
        found = []
        for given, default in ((self.system_xml, "system.xml"),
                               (self.topology_pdb, "topology.pdb")):
            path = Path(given) if given else (setup / default if setup else None)
            if path is None or not path.is_file():
                raise StudyError(
                    f"MM/GBSA reads the study's force field from setup's "
                    f"`{default}`, and {'there is no study folder' if path is None else f'{path} is not there'}. "
                    f"Give it as `{'system_xml' if default == 'system.xml' else 'topology_pdb'}`.",
                    code="analysis.data.absent")
            found.append(path)
        return found[0], found[1]

    def _selected(self, traj: md.Trajectory) -> tuple[np.ndarray, np.ndarray, str]:
        topology = traj.topology
        if self.ligand_selection:
            ligand_said = self.ligand_selection
        elif self.ligand_resname:
            ligand_said = f"resname {self.ligand_resname}"
        else:
            raise StudyError(
                "The study names no ligand, and MM/GBSA needs one. Name its atoms "
                "in `ligand_selection`.", code="analysis.data.absent")
        try:
            ligand = topology.select(ligand_said)
            receptor = topology.select(self.receptor_selection)
        except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
            raise StudyError(f"A selection MDTraj cannot read: {exc}",
                             code="analysis.selection.invalid") from exc
        if not len(ligand) or not len(receptor):
            raise StudyError(
                f"The ligand ({ligand_said!r}) matched {len(ligand)} atoms and the "
                f"receptor ({self.receptor_selection!r}) {len(receptor)}; both are "
                "needed.", code="analysis.selection.empty")
        if np.intersect1d(ligand, receptor).size:
            raise StudyError(
                f"The ligand ({ligand_said!r}) and the receptor "
                f"({self.receptor_selection!r}) share atoms; they are taken apart.",
                code="analysis.selection.arity")
        return ligand, receptor, ligand_said

    @staticmethod
    def _in_the_system(traj: md.Trajectory, whole: md.Topology,
                       atoms: np.ndarray) -> np.ndarray:
        """Where each of ``atoms`` (in the frames) is among the system's
        particles: the k-th atom of a residue name, number and atom name in
        the frames is the k-th in the system, which holds every atom in the
        order the frames keep."""
        def key(atom) -> tuple[str, int, str]:
            return (atom.residue.name, int(atom.residue.resSeq), atom.name)

        occurrences: dict[tuple[str, int, str], list[int]] = {}
        for atom in whole.atoms:
            occurrences.setdefault(key(atom), []).append(atom.index)
        seen: dict[tuple[str, int, str], int] = {}
        found = np.empty(len(atoms), dtype=int)
        for k, index in enumerate(atoms):
            name = key(traj.topology.atom(int(index)))
            count = seen.get(name, 0)
            among = occurrences.get(name, [])
            if count >= len(among):
                raise StudyError(
                    f"{traj.topology.atom(int(index))} is in the frames and not in "
                    "the structure the force field was built from, so its charge "
                    "is not known.", code="analysis.data.not_this_system")
            found[k] = among[count]
            seen[name] = count + 1
        # The frames keep every atom of a molecule the system has, or the
        # k-th of a name is not the k-th: said rather than guessed.
        for name, count in seen.items():
            if count != len(occurrences[name]) and name[0] not in ("HOH", "WAT", "SOL"):
                raise StudyError(
                    f"The frames hold {count} of the system's "
                    f"{len(occurrences[name])} atoms named {name[2]} in "
                    f"{name[0]}{name[1]}, so which is which cannot be told. "
                    "Store every atom of the receptor and the ligand.",
                    code="analysis.data.not_this_system")
        return found

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        from openmm import Platform, XmlSerializer
        import openmm

        from openmm.app.internal.customgbforces import GBSAOBC2Force

        system_path, topology_path = self._files()
        system = XmlSerializer.deserialize(system_path.read_text(encoding="utf-8"))
        whole = md.load_topology(str(topology_path))
        if whole.n_atoms != system.getNumParticles():
            raise StudyError(
                f"{topology_path.name} holds {whole.n_atoms} atoms and "
                f"{system_path.name} {system.getNumParticles()} particles; they are "
                "not one system.", code="analysis.data.not_this_system")
        forces = {type(f).__name__: f for f in system.getForces()}
        unknown = sorted(set(forces) - _BONDED - {"NonbondedForce"})
        if unknown:
            raise StudyError(
                f"The force field puts part of its interactions in "
                f"{', '.join(unknown)}, which this does not take apart (a CHARMM "
                "NBFIX, a polarisable model, or a restraint): only a force field "
                "whose nonbonded terms are all in one NonbondedForce is read.",
                code="analysis.system.inapplicable")
        if "NonbondedForce" not in forces:
            raise StudyError("The system has no NonbondedForce.",
                             code="analysis.system.inapplicable")
        nonbonded = forces["NonbondedForce"]

        ligand, receptor, ligand_said = self._selected(traj)
        complex_atoms = np.concatenate([receptor, ligand])
        particles = self._in_the_system(traj, whole, complex_atoms)
        sub = traj.topology.subset(complex_atoms)
        gb = np.asarray(GBSAOBC2Force.getStandardParameters(sub.to_openmm()), dtype=float)
        n_receptor = len(receptor)
        platform = Platform.getPlatformByName(
            "CPU" if "CPU" in [Platform.getPlatform(i).getName()
                               for i in range(Platform.getNumPlatforms())] else "Reference")
        complex_ = _Energies(openmm, nonbonded, particles, gb, platform)
        receptor_ = _Energies(openmm, nonbonded, particles[:n_receptor], gb[:n_receptor],
                              platform)
        ligand_ = _Energies(openmm, nonbonded, particles[n_receptor:], gb[n_receptor:],
                            platform)

        molecules = []
        for chain_atoms in ([a for a in receptor if traj.topology.atom(int(a)).residue.chain
                             is chain] for chain in traj.topology.chains):
            if chain_atoms:
                molecules.append(np.searchsorted(complex_atoms[:n_receptor],
                                                 np.array(chain_atoms)))
        molecules.append(np.arange(n_receptor, len(complex_atoms)))
        boxes = traj.unitcell_vectors

        parts = np.empty((traj.n_frames, 4))
        for frame in range(traj.n_frames):
            xyz = traj.xyz[frame, complex_atoms].astype(np.float64)
            xyz = placed_together(xyz, None if boxes is None else boxes[frame], molecules)
            parts[frame] = (complex_.of(xyz) - receptor_.of(xyz[:n_receptor])
                            - ligand_.of(xyz[n_receptor:]))
        total = parts.sum(axis=1)
        self._parts = pd.DataFrame(parts, columns=list(PARTS)).assign(total=total)

        mean = parts.mean(axis=0)
        self.findings["binding_energy"] = {
            "mean_kj_per_mol": float(total.mean()),
            "mean_kcal_per_mol": float(total.mean() / KJ_PER_KCAL),
            "parts_kj_per_mol": {name: float(v) for name, v in zip(PARTS, mean)},
            "parts": {"elec": "Coulomb between the two, in vacuum",
                      "vdw": "Lennard-Jones between the two",
                      "gb": "the change in the solvent's polar screening (OBC2)",
                      "sa": "the change in the nonpolar term from the Born radii (ACE)"},
            "not_a_free_energy": (
                "The solute's loss of entropy and the partners' reorganisation are "
                "left out: for ranking ligands bound to one receptor, not for "
                "comparison with a measured binding free energy."),
        }
        self.findings["partners"] = {
            "ligand": ligand_said, "ligand_atoms": int(len(ligand)),
            "receptor": self.receptor_selection, "receptor_atoms": int(n_receptor),
            "placed_together": "each molecule moved by whole cell vectors to the "
                               "image nearest those already placed, every frame",
        }
        self.findings["method"] = {
            "force_field": f"the study's own, from {system_path.name}: charges, "
                           "Lennard-Jones and exceptions",
            "solvent": "OBC2 generalised Born (AMBER igb=5), mbondi2 radii, solute "
                       "dielectric 1, solvent 78.5, no salt",
            "nonpolar": "ACE from the Born radii, 0.0054 kcal/mol/Å^2",
            "bonded": "not computed: the partners are cut from the complex's frame, "
                      "so they cancel",
            "cutoff": "none",
            "trajectories": "one, the complex's",
        }
        return total

    # ------------------------------------------------------------------
    def save_data(self, result: np.ndarray, path: Path) -> Path:
        written = super().save_data(result, path)
        if self._parts is not None:
            self._parts.to_csv(path.with_name("mmgbsa_parts.dat"), index=False,
                               float_format="%.4f")
        return written

    def default_ylabel(self) -> str | None:
        return "Binding energy (kJ/mol)"

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        return self.frame_axis(self._traj_for_plot)[1]

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x = (self.frame_axis(self._traj_for_plot)[0] if self._traj_for_plot is not None
             else np.arange(len(result)))
        ax.plot(x, result, linewidth=1.0)

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)


register_analysis(MMGBSA.name, MMGBSA)
