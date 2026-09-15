"""The three principal moments of inertia, per frame.

The mass-weighted second-moment tensor of the selected atoms about their
centre of mass, diagonalised. Three numbers per frame, smallest first,
in amu nm^2::

    I_ab = sum_i m_i ( |r_i|^2 delta_ab  -  r_ia r_ib ),   r_i = x_i - R_cm

They describe shape in a way the radius of gyration cannot. Rg collapses a
conformation to one number, so a rod and a disc of the same extent report
the same value; the three principal moments separate them -- a rod has one
small moment and two large equal ones, a disc two small and one large. The
ratio between them is what an asphericity or a prolate/oblate assignment is
computed from.

**Mass-weighted, and refused without masses.** An inertia tensor is defined
by mass; computed with every atom weighted equally it is a different tensor
that happens to have the same units in the numerator. `rg` falls back to
equal weighting because an unweighted radius of gyration is a real, if
less conventional, quantity. There is no corresponding quantity here, so a
topology carrying no elements is a refusal rather than a silent
substitution.

**Rotation-invariant, so alignment does not enter.** The eigenvalues of the
tensor do not change when the molecule is rotated, which means this
analysis neither needs a superposition nor is disturbed by one that ran
before it -- unlike every distance measured against a reference. What does
disturb it is a molecule broken across a periodic boundary: the two halves
sit a box apart, the tensor describes that separation rather than the
molecule, and the moments come out enormous. The extent of the selection is
compared against the box and the run is marked where the two are
comparable.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError


def _masses(topology) -> np.ndarray:
    """Atomic masses in amu, or a refusal naming what is missing."""
    masses = []
    missing: set[str] = set()
    for atom in topology.atoms:
        element = getattr(atom, "element", None)
        mass = getattr(element, "mass", None)
        if not mass:
            missing.add(atom.name)
            continue
        masses.append(float(mass))
    if missing:
        named = ", ".join(sorted(missing)[:8])
        raise StudyError(
            f"This topology carries no element for {len(missing)} atom "
            f"name(s) -- {named}"
            f"{' and others' if len(missing) > 8 else ''} -- so their masses "
            "are unknown. A moment of inertia is defined by mass; weighting "
            "every atom equally instead would produce a tensor with the same "
            "units and a different meaning. A topology read from a PDB "
            "usually carries elements; one built by hand often does not.",
            code="analysis.data.absent",
        )
    return np.asarray(masses, dtype=np.float64)


class MomentsOfInertia(Analysis):
    """Principal moments of inertia per frame, smallest first.

    Parameters
    ----------
    selection : str, optional
        MDTraj atom selection. Defaults to ``"protein"`` -- the moments of a
        solvated box are dominated by the water and describe the box.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``moments_of_inertia.dat`` -- three columns, I1 <= I2 <= I3 in
    amu nm^2, one row per frame.
    """

    name = "moments_of_inertia"
    description = "Principal moments of inertia"
    time_series = True
    #: Three numbers per frame, so there is no single scalar whose weighted
    #: mean is the thing somebody wants; the base class leaves the
    #: reweighted average out rather than picking one of the three.
    reweightable = None
    default_selection = "protein"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def _note_if_the_molecule_may_be_broken(
        self, traj: md.Trajectory, xyz: np.ndarray
    ) -> None:
        """Say when the selection is as wide as the cell that holds it."""
        if traj.unitcell_lengths is None:
            return
        extent = float(np.max(xyz.max(axis=1) - xyz.min(axis=1)))
        smallest = float(np.min(traj.unitcell_lengths))
        if extent > 0.8 * smallest:
            self.findings["not_a_measurement"] = (
                f"The selection spans {extent:.3f} nm in its widest frame, "
                f"against a smallest box dimension of {smallest:.3f} nm. A "
                "molecule wrapped across a periodic boundary looks exactly "
                "like this, and its inertia tensor describes the separation "
                "of the two pieces rather than the molecule. Image the "
                "trajectory so the solute is whole before reading these "
                "moments."
            )

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        atom_idx = self.select_atoms(traj)
        sub = traj.atom_slice(atom_idx) if len(atom_idx) < traj.n_atoms else traj

        if sub.n_atoms < 3:
            raise StudyError(
                f"Selection {self.selection!r} matched {sub.n_atoms} atom(s). "
                "Three principal moments need at least three atoms; with "
                "fewer the tensor is singular and two of the three are zero "
                "by construction rather than by shape.",
                code="analysis.selection.arity",
            )

        masses = _masses(sub.topology)
        xyz = sub.xyz.astype(np.float64)
        self._note_if_the_molecule_may_be_broken(sub, xyz)

        total = masses.sum()
        centre = (xyz * masses[None, :, None]).sum(axis=1) / total
        r = xyz - centre[:, None, :]

        # I_ab = sum_i m_i ( |r_i|^2 delta_ab - r_ia r_ib ), per frame.
        squared = (r ** 2).sum(axis=2)                       # (frames, atoms)
        trace = (masses[None, :] * squared).sum(axis=1)      # (frames,)
        outer = np.einsum("fia,fib,i->fab", r, r, masses)    # (frames, 3, 3)
        tensor = np.eye(3)[None, :, :] * trace[:, None, None] - outer

        # eigvalsh returns ascending eigenvalues for a symmetric matrix,
        # which is the ordering I1 <= I2 <= I3 is quoted in.
        moments = np.linalg.eigvalsh(tensor)

        self.findings["inertia"] = {
            "atoms": int(sub.n_atoms),
            "total_mass_amu": float(total),
            "units": "amu nm^2",
            "ordering": "ascending: I1 <= I2 <= I3",
        }
        return moments

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x = (
            self.frame_axis(self._traj_for_plot)[0]
            if self._traj_for_plot is not None
            else np.arange(result.shape[0])
        )
        for i, label in enumerate(("$I_1$", "$I_2$", "$I_3$")):
            ax.plot(x, result[:, i], linewidth=1.3, label=label)
        ax.legend(loc="best")

    _traj_for_plot: md.Trajectory | None = None

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        return self.frame_axis(self._traj_for_plot)[1]

    def default_ylabel(self) -> str | None:
        return "Moment of inertia (amu nm2)"


register_analysis(MomentsOfInertia.name, MomentsOfInertia)
