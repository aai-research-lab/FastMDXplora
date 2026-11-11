"""Base-pair and step parameters, held against helices built from known
parameters and against the Dickerson dodecamer (1BNA)."""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"


@pytest.fixture(scope="module")
def dodecamer():
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as folder:
        path = Path(folder) / "1BNA.pdb"
        path.write_bytes(gzip.decompress((DATA / "1BNA.pdb.gz").read_bytes()))
        whole = md.load_pdb(str(path))
    return whole.atom_slice(whole.topology.select("not water"))


def _run(trajectory, tmp_path, **options):
    from fastmdxplora.analysis.nucleic_acid import NucleicAcid

    analysis = NucleicAcid(output_dir=tmp_path, **options)
    return analysis, analysis.run(trajectory)


def test_a_helix_built_from_its_steps_gives_them_back():
    """3DNA's rebuilding and its analysis are inverses: any step, built and
    measured, comes back."""
    from fastmdxplora.analysis.nucleic_acid import frame_parameters, step_from

    rng = np.random.default_rng(0)
    frame, origin = np.eye(3), np.zeros(3)
    for _ in range(50):
        step = np.array([rng.normal(0, 0.1), rng.normal(0, 0.1), 0.33 + rng.normal(0, 0.03),
                         rng.normal(0, 8), rng.normal(0, 12), 36 + rng.normal(0, 8)])
        following, there = step_from(frame, origin, step)
        assert np.allclose(frame_parameters(frame, origin, following, there)[0], step,
                           atol=1e-9)
        frame, origin = following, there


def test_the_base_letters_are_read_from_every_naming():
    from fastmdxplora.analysis.nucleic_acid import base_letter

    assert [base_letter(n) for n in ("DA", "DG5", "C3", "RU", "THY", "A", "ALA")] == [
        "A", "G", "C", "U", "T", "A", None]


def test_the_dickerson_dodecamer_is_b_dna(dodecamer, tmp_path):
    analysis, result = _run(dodecamer, tmp_path)
    assert result.status == "ok", result.message
    helix = analysis.findings["helix"]
    assert helix["pairs"] == 12 and helix["steps"] == 11
    assert helix["mean_rise_nm"] == pytest.approx(0.336, abs=0.01)
    assert helix["mean_twist_deg"] == pytest.approx(35.6, abs=1.0)
    # B-DNA's propeller is negative, and strongest at the central AATT.
    assert helix["mean_propeller_deg"] < -8
    import pandas as pd

    pairs = pd.read_csv(tmp_path / "nucleic_acid" / "nucleic_acid_pairs.dat")
    assert pairs["pair"].tolist()[:2] == ["C1-G24", "G2-C23"]
    central = pairs[pairs["pair"].isin(["A5-T20", "A6-T19", "T7-A18", "T8-A17"])]
    assert central["propeller"].max() < -15
    # The paired bases' frames nearly coincide: shear, stretch and stagger
    # are a few hundredths of a nm.
    assert pairs[["shear", "stretch", "stagger"]].abs().max().max() < 0.1
    steps = result.data
    assert steps.loc[2, "step"] == "C3-G22/G4-C21" and steps.loc[2, "twist"] < 28


def test_a_pair_that_comes_apart_is_said_to(dodecamer, tmp_path):
    moved = md.join([dodecamer, dodecamer])
    base = moved.topology.select("chainid 1 and resSeq 13")
    xyz = moved.xyz.copy()
    xyz[1, base] += np.array([1.0, 0.0, 0.0])
    moved.xyz = xyz
    analysis, result = _run(moved, tmp_path)
    assert result.status == "ok", result.message
    assert "G12-C13" in analysis.findings["pairs_apart"]


def test_a_protein_has_no_helix_to_measure(tmp_path):
    topology = md.Topology()
    chain = topology.add_chain()
    topology.add_atom("CA", md.element.carbon, topology.add_residue("ALA", chain))
    result = _run(md.Trajectory(np.zeros((1, 1, 3), dtype=np.float32), topology),
                  tmp_path)[1]
    assert result.status == "error" and "a double helix needs" in result.message


def test_a_cutoff_that_means_nothing_is_refused(tmp_path):
    from fastmdxplora.analysis.nucleic_acid import NucleicAcid
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError, match="from 0.25 to 0.5 nm"):
        NucleicAcid(output_dir=tmp_path, pair_cutoff_nm=1.0)
