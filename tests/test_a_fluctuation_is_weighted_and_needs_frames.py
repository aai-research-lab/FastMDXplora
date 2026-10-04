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
    got = RMSF(selection="all", equilibrated_from=0).compute(traj)[:, 1]

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


def _relaxing_loop(frames=1000):
    """3PTB's alpha carbons, a loop of twenty relaxing 0.4 nm away from the
    start over the first ~300 frames (time constant 80), every atom
    jittering by 0.03 nm per axis. Once relaxed the loop fluctuates by
    sqrt(3) * 0.03 = 0.052 nm."""
    import gzip
    from pathlib import Path

    text = gzip.decompress(
        (Path(__file__).parent / "data" / "assemblies" / "3PTB.pdb.gz").read_bytes())
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "3PTB.pdb"
        path.write_bytes(text)
        structure = md.load(str(path))
    alpha = structure.atom_slice(structure.topology.select("protein and name CA"))
    rng = np.random.default_rng(0)
    xyz = np.repeat(alpha.xyz, frames, axis=0).astype(np.float64)
    loop = np.arange(60, 80)
    xyz[:, loop, 0] += (0.4 * (1 - np.exp(-np.arange(frames) / 80.0)))[:, None]
    xyz += rng.normal(scale=0.03, size=xyz.shape)
    traj = md.Trajectory(xyz.astype(np.float32), alpha.topology)
    traj.time = np.arange(1, frames + 1) * 10.0
    return traj, loop


def test_the_relaxation_is_left_out():
    """All frames read the loop at 0.0819 nm; its equilibrium is 0.052."""
    traj, loop = _relaxing_loop()
    analysis = RMSF()
    result = analysis.compute(traj)

    discard = analysis.findings["discard"]
    assert 150 < discard["frames"] < 500
    assert discard["ns"] == pytest.approx(discard["frames"] * 0.01)
    loop_rmsf = result[loop, 1].mean()
    assert loop_rmsf == pytest.approx(np.sqrt(3) * 0.03, abs=0.004)
    assert RMSF(equilibrated_from=0).compute(traj)[loop, 1].mean() > 0.075


def test_the_start_can_be_given(tmp_path):
    traj, loop = _relaxing_loop()
    analysis = RMSF(equilibrated_from=600, output_dir=tmp_path)
    result = analysis.compute(traj)

    assert analysis.findings["discard"]["frames"] == 600
    assert analysis.options["equilibrated_from"] == 600
    expected = traj[600:]
    expected.superpose(traj, 0, atom_indices=np.arange(traj.n_atoms))
    deviation = expected.xyz - expected.xyz.mean(axis=0)
    assert np.allclose(result[:, 1], np.sqrt((deviation ** 2).sum(axis=2).mean(axis=0)),
                       atol=1e-5)


def test_a_start_leaving_fewer_than_two_frames_is_refused():
    traj = _mobile_hydrogens(frames=5)
    with pytest.raises(StudyError) as raised:
        RMSF(selection="all", equilibrated_from=4).compute(traj)
    assert raised.value.code == "analysis.option.out_of_range"


def test_the_figure_says_which_frames(tmp_path):
    traj, _ = _relaxing_loop(frames=400)
    analysis = RMSF(output_dir=tmp_path)
    assert analysis.run(traj).status == "ok"
    said = analysis._which_frames()
    frames = analysis.findings["discard"]["frames"]
    assert said.startswith(f"over frames {frames:,} to 399")
    assert "left out as equilibration" in said
