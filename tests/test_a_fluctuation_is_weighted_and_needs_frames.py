"""A residue's fluctuation is weighted by mass, and one frame has none.

GROMACS ``gmx rmsf -res`` reports the square root of a residue's
mass-weighted mean squared fluctuation. The package took the unweighted
mean while its comment said it agreed with GROMACS, so a residue with a
mobile hydrogen among rigid heavy atoms read high. And one frame returned
an RMSF of zero for every residue with status "ok".
"""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.rmsf import RMSF
from fastmdxplora.refusals import StudyError


def _mobile_hydrogens(frames=2000):
    """Four residues of CA, HA and N, the hydrogens moving 0.15 nm and the
    heavy atoms 0.02 nm."""
    top = md.Topology()
    chain = top.add_chain()
    for i in range(4):
        residue = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, residue)
        top.add_atom("HA", md.element.hydrogen, residue)
        top.add_atom("N", md.element.nitrogen, residue)
    rng = np.random.default_rng(0)
    base = rng.normal(size=(top.n_atoms, 3)) * 0.5
    amplitude = np.array([0.15 if a.element.symbol == "H" else 0.02 for a in top.atoms])
    xyz = base[None] + rng.normal(size=(frames, top.n_atoms, 3)) * amplitude[None, :, None]
    return md.Trajectory(xyz.astype(np.float32), top)


def test_a_residue_is_the_mass_weighted_mean_of_its_atoms():
    traj = _mobile_hydrogens()
    got = RMSF(selection="all").compute(traj)[:, 1]

    aligned = traj[:]
    aligned.superpose(traj, 0)
    msf = ((aligned.xyz - aligned.xyz.mean(axis=0)) ** 2).sum(axis=2).mean(axis=0)
    mass = np.array([a.element.mass for a in traj.topology.atoms])
    gromacs = [np.sqrt((mass[3 * i:3 * i + 3] * msf[3 * i:3 * i + 3]).sum()
                       / mass[3 * i:3 * i + 3].sum()) for i in range(4)]
    unweighted = [np.sqrt(msf[3 * i:3 * i + 3].mean()) for i in range(4)]

    assert np.allclose(got, gromacs, rtol=1e-4)
    # Far enough apart that the check above tells the two apart.
    assert np.min(np.array(unweighted) / np.array(gromacs)) > 1.5


def test_the_options_state_the_formula(tmp_path):
    analysis = RMSF(selection="all", output_dir=tmp_path)
    assert "m_i MSF_i" in analysis.options["per_residue_average"]


def test_one_frame_is_refused_rather_than_read_as_rigid(tmp_path):
    traj = _mobile_hydrogens(frames=1)
    with pytest.raises(StudyError) as raised:
        RMSF(selection="all").compute(traj)
    assert raised.value.code == "analysis.sampling.too_few_frames"

    result = RMSF(selection="all", output_dir=tmp_path).run(traj)
    assert result.status == "error"


def test_two_frames_are_enough():
    assert RMSF(selection="all").compute(_mobile_hydrogens(frames=2)).shape == (4, 2)
