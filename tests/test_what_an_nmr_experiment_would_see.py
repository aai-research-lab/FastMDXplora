"""NMR couplings and NOEs from the frames, beside the experiment's."""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"


@pytest.fixture(scope="module")
def streptavidin():
    """1STP's chain, a beta barrel with a short helix, a few frames of it."""
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as folder:
        path = Path(folder) / "1STP.pdb"
        path.write_bytes(gzip.decompress((DATA / "1STP.pdb.gz").read_bytes()))
        whole = md.load_pdb(str(path))
    protein = whole.atom_slice(whole.topology.select("protein"))
    rng = np.random.default_rng(0)
    xyz = protein.xyz[0][None] + rng.normal(0, 0.005, (5,) + protein.xyz[0].shape)
    return md.Trajectory(xyz.astype(np.float32), protein.topology)


def _run(trajectory, tmp_path, **options):
    from fastmdxplora.analysis.nmr_observables import NMRObservables

    analysis = NMRObservables(output_dir=tmp_path, **options)
    analysis.study_dir = tmp_path
    return analysis, analysis.run(trajectory)


@pytest.mark.parametrize("phi, expected", [(-np.pi, 0.33 + 1.36 * 0.5 + 8.4 * 0.25),
                                           (np.pi / 3, 8.4 - 1.36 + 0.33),
                                           (-np.pi / 6, 0.33)])
def test_the_karplus_curve_is_vogeli_and_bax_s(phi, expected):
    """J = 8.4 cos^2(phi - 60) - 1.36 cos(phi - 60) + 0.33: largest at phi
    +60 degrees, and C alone at phi -30, where the cosine is zero."""
    from fastmdxplora.analysis.nmr_observables import karplus

    assert karplus(np.array([phi]), "Bax2007")[0] == pytest.approx(expected, abs=1e-6)


def test_strands_couple_more_strongly_than_helices(streptavidin, tmp_path):
    analysis, result = _run(streptavidin, tmp_path)
    assert result.status == "ok", result.message
    table = result.data
    shape = md.compute_dssp(streptavidin[0], simplified=True)[0]
    by_residue = {r.resSeq: shape[r.index] for r in streptavidin.topology.residues}
    strand = table[table["residue"].map(by_residue) == "E"]["j_hz"]
    # Phi near -120 in a strand gives about 9 Hz; the barrel is mostly strand.
    assert strand.mean() > 8.0 and len(strand) > 40
    # Agrees with MDTraj's own computation of the same coupling.
    _, own = md.compute_J3_HN_HA(streptavidin, model="Bax2007")
    assert np.allclose(table["j_hz"], own.mean(axis=0), atol=1e-3)


def test_measured_couplings_off_by_a_known_amount_are_said_to_be(streptavidin, tmp_path):
    _, first = _run(streptavidin, tmp_path / "first")
    measured = first.data[["residue", "j_hz"]].copy()
    measured["j_hz"] += 1.5
    (tmp_path / "j.csv").write_text(measured.to_csv(index=False))
    analysis, result = _run(streptavidin, tmp_path, j_couplings_file="j.csv")
    assert result.status == "ok", result.message
    against = analysis.findings["against_the_measured_couplings"]
    assert against["rms_difference_hz"] == pytest.approx(1.5, abs=1e-3)
    assert against["mean_difference_hz"] == pytest.approx(-1.5, abs=1e-3)
    assert against["beyond_the_karplus_scatter"] is True
    assert "measured_hz" in result.data.columns


def test_an_noe_is_averaged_as_r_to_the_minus_six(tmp_path):
    """Two atoms 0.3 nm apart half the time and 0.6 nm the other half: the
    NOE sees the close half, <r^-6>^(-1/6) = 0.336 nm, not the mean 0.45."""
    topology = md.Topology()
    chain = topology.add_chain()
    for name in ("H1", "H2"):
        topology.add_atom(name, md.element.hydrogen, topology.add_residue("ALA", chain))
    xyz = np.zeros((10, 2, 3), dtype=np.float32)
    xyz[:, 1, 0] = np.where(np.arange(10) % 2, 0.6, 0.3)
    frames = md.Trajectory(xyz, topology)
    from fastmdxplora.analysis.nmr_observables import noe_distance

    expected = ((0.3 ** -6 + 0.6 ** -6) / 2) ** (-1 / 6)
    assert noe_distance(frames, np.array([0]), np.array([1])) == pytest.approx(expected, rel=1e-5)
    assert expected == pytest.approx(0.336, abs=1e-3)


def test_noes_beyond_their_bounds_are_violations(streptavidin, tmp_path):
    (tmp_path / "noe.csv").write_text(
        "selection_1,selection_2,upper_nm\n"
        "resid 10 and name CA,resid 11 and name CA,0.5\n"
        "resid 10 and name CA,resid 60 and name CA,0.5\n")
    analysis, result = _run(streptavidin, tmp_path, noe_file="noe.csv")
    assert result.status == "ok", result.message
    import pandas as pd

    noes = pd.read_csv(tmp_path / "nmr_observables" / "nmr_noes.dat")
    assert noes["violation_nm"].iloc[0] == 0.0
    assert noes["violation_nm"].iloc[1] > 0.5
    assert analysis.findings["noes"]["violated"] == 1


@pytest.mark.parametrize("options, said", [
    ({"j_couplings_file": "absent.csv"}, "is not there"),
    ({"noe_file": "bad.csv"}, "has no column"),
])
def test_files_that_cannot_be_read_are_refused(streptavidin, tmp_path, options, said):
    (tmp_path / "bad.csv").write_text("a,b\n1,2\n")
    _, result = _run(streptavidin, tmp_path, **options)
    assert result.status == "error" and said in result.message


def test_an_unknown_karplus_set_is_refused(tmp_path):
    from fastmdxplora.analysis.nmr_observables import NMRObservables
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError, match="`karplus` is one of"):
        NMRObservables(output_dir=tmp_path, karplus="Smith")


def test_what_is_not_computed_is_said(streptavidin, tmp_path):
    analysis, _ = _run(streptavidin, tmp_path)
    assert "SAXS" in analysis.findings["not_computed"]
