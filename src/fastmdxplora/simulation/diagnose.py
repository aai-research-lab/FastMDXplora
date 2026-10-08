"""What went wrong when a simulation failed, read from the state it failed in.

A run that ends with "the coordinates are not finite" has told you almost
nothing. The advice that usually follows -- lower the timestep, lower the
temperature, raise the friction -- is a list of things that sometimes help,
offered without knowing which applies. Sometimes none of them do, because the
problem is a ligand whose parameters are wrong, and no timestep is small
enough to fix that.

The state at failure says more. Which atoms went non-finite first, and what
they belong to, distinguishes the common causes:

- **a ligand alone** -- almost always its parameters, or a pose that was
  already clashing when the run started
- **a lipid, or a protein atom next to one** -- the bilayer packing, which is
  what restrained equilibration exists to survive
- **the whole system at once** -- an integration problem, where the usual
  advice does apply
- **one residue in an otherwise sound protein** -- often a strained ring or a
  missing atom that preparation filled in badly

So this reports what it can see rather than reciting remedies. Where the
evidence points somewhere, it says where; where it does not, it says that too,
which is more useful than a confident list that happens not to apply.

Nothing here retries. A run that exploded because its ligand is wrong will
explode again more slowly at half the timestep, and a "rescue" that produces
a trajectory from a broken system is worse than a failure: the failure is
visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Diagnosis", "diagnose_failure"]


@dataclass(frozen=True)
class Diagnosis:
    """What can be said about a failure from the state it happened in."""

    stage: str
    #: Residues holding the first non-finite atoms, most affected first.
    culprits: list[tuple[str, int]]
    n_bad_atoms: int
    n_atoms: int
    #: What the evidence points at, or None where it points nowhere.
    likely_cause: str | None
    advice: list[str]

    def as_text(self) -> str:
        lines = [f"The simulation became unstable during {self.stage}."]

        if self.n_bad_atoms:
            share = self.n_bad_atoms / max(self.n_atoms, 1)
            lines.append(
                f"{self.n_bad_atoms:,} of {self.n_atoms:,} atoms "
                f"({share:.1%}) have non-finite coordinates."
            )
        if self.culprits:
            named = ", ".join(f"{name} ({count} atoms)"
                              for name, count in self.culprits[:4])
            lines.append(f"The worst affected are: {named}.")

        if self.likely_cause:
            lines.append("")
            lines.append(self.likely_cause)

        if self.advice:
            lines.append("")
            lines.append("What to try:")
            lines.extend(f"  - {item}" for item in self.advice)
        return "\n".join(lines)


#: Lipid residue names, from a module that imports nothing, so a diagnosis
#: cannot fail to load because something else did.
from fastmdxplora.lipids import LIPID_RESIDUE_NAMES as _LIPIDS  # noqa: E402
_SOLVENT = frozenset({"HOH", "WAT", "TIP3", "NA", "CL", "K", "MG", "CA"})
_STANDARD_RESIDUES = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "HID", "HIE", "HIP", "CYX", "ASH", "GLH", "LYN",
})


def _shorter_step(timestep_fs: float | None) -> str:
    """The timestep to try, from the one the run took: half of it, or 2 fs
    where it was longer than the 2 fs bonds to hydrogen usually allow. It
    said 1.0 fs whatever the run had taken, 10 fs included."""
    if timestep_fs is None or timestep_fs <= 0:
        return "Halve the timestep: --simulate-timestep-fs <half of it>"
    if timestep_fs > 4.0:
        return (f"Shorten the timestep from {timestep_fs:g} fs to 2 fs, the most bonds "
                "to hydrogen constrained usually allow: --simulate-timestep-fs 2")
    return f"Halve the timestep: --simulate-timestep-fs {timestep_fs / 2:g}"


def diagnose_failure(topology: Any, positions: Any, *, stage: str,
                     platform: str | None = None,
                     timestep_fs: float | None = None) -> Diagnosis:
    """Read a failed state and say what it points at."""
    import numpy as np

    import mdtraj as md

    try:
        from openmm import unit as openmm_unit

        if openmm_unit.is_quantity(positions):
            positions = positions.value_in_unit(openmm_unit.nanometer)
    except ImportError:  # pragma: no cover - only without OpenMM
        pass

    coordinates = np.asarray(
        [[float(p[0]), float(p[1]), float(p[2])] for p in positions],
        dtype=float)
    bad = ~np.isfinite(coordinates).all(axis=1)
    n_bad = int(bad.sum())

    mdtop = (topology if isinstance(topology, md.Topology)
             else md.Topology.from_openmm(topology))
    atoms = list(mdtop.atoms)

    counts: dict[str, int] = {}
    for index in np.where(bad)[0]:
        if int(index) >= len(atoms):
            continue
        name = atoms[int(index)].residue.name.upper()
        counts[name] = counts.get(name, 0) + 1
    culprits = sorted(counts.items(), key=lambda kv: -kv[1])

    affected = set(counts)
    ligand_like = {name for name in affected
                   if name not in _STANDARD_RESIDUES
                   and name not in _SOLVENT and name not in _LIPIDS}
    lipids_affected = affected & _LIPIDS
    protein_affected = affected & _STANDARD_RESIDUES

    cause: str | None = None
    advice: list[str] = []

    if not n_bad:
        cause = (
            "No atom has non-finite coordinates, so the failure was in the "
            "energy rather than the positions -- usually a force that is "
            "large but not yet infinite."
        )
        advice = [
            "Minimize for longer: --simulate-minimize-max-iterations 2000",
            "Check the setup phase's clash report for a contact that was "
            "close but under the threshold",
        ]
    elif ligand_like and not protein_affected and not lipids_affected:
        cause = (
            f"Only the ligand went unstable ({', '.join(sorted(ligand_like))}), "
            "and nothing around it. That points at the ligand itself rather "
            "than at the integration: either its parameters are wrong, or the "
            "pose it started from was already clashing. No timestep is small "
            "enough to fix a wrong parameter."
        )
        advice = [
            "Check `setup/setup_parameters.json` for how the ligand's "
            "chemistry was resolved -- a perceived bond order can put a "
            "hydrogen in the wrong place",
            "Supply the chemistry explicitly with an SDF: --setup-ligand",
            "Look at the clash check in the setup log; a pose under the "
            "threshold can still be too close after hydrogens are added",
        ]
    elif lipids_affected:
        cause = (
            "Lipids are among the affected atoms, which points at the bilayer "
            "packing rather than at the protein. A membrane needs the solvent "
            "and lipids to relax around the solute before anything moves "
            "freely."
        )
        advice = [
            "Restrain the protein during equilibration: "
            '--simulate-restrain "protein and not element H"',
            "Give equilibration longer: --simulate-nvt-steps 25000 "
            "--simulate-npt-steps 25000",
            "Check the orientation: a protein embedded at the wrong angle "
            "leaves lipids overlapping it",
        ]
    elif len(affected) == 1 and protein_affected:
        only = next(iter(protein_affected))
        cause = (
            f"One residue type went unstable ({only}) and nothing else, which "
            "usually means a local problem -- a strained ring, or an atom "
            "that preparation added in a poor position -- rather than "
            "anything about the run as a whole."
        )
        advice = [
            "Minimize for longer before dynamics: "
            "--simulate-minimize-max-iterations 5000",
            f"Look at the {only} residues in `setup/prepared.pdb` for one "
            "with impossible geometry",
        ]
    elif n_bad > 0.5 * len(coordinates):
        cause = (
            "Most of the system went unstable at once, which is an "
            "integration failure rather than a local problem. This is the "
            "case the usual advice is for."
        )
        advice = [
            _shorter_step(timestep_fs),
            "Raise the friction so the thermostat holds harder: "
            "--simulate-friction-per-ps 5.0",
        ]
        # `Precision` is a property of the GPU platforms only -- the CPU
        # platform offers `Threads` and `DeterministicForces` and nothing
        # else -- so on CPU this advice costs somebody a run and teaches
        # them nothing. Offered where it can be taken.
        if (platform or "").upper() != "CPU":
            advice.append(
                "Run in double precision: --simulate-precision double")
    else:
        cause = (
            "The affected atoms do not fall into a pattern that points "
            "anywhere in particular. That is worth saying rather than "
            "guessing: the remedies below sometimes help and may not apply."
        )
        advice = [
            _shorter_step(timestep_fs),
            "Minimize for longer: --simulate-minimize-max-iterations 5000",
            "Restrain the solute during equilibration: "
            '--simulate-restrain "protein and not element H"',
        ]

    return Diagnosis(
        stage=stage,
        culprits=culprits,
        n_bad_atoms=n_bad,
        n_atoms=len(coordinates),
        likely_cause=cause,
        advice=advice,
    )
