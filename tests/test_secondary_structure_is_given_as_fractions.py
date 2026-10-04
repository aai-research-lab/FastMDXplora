"""Secondary structure is reported as fractions, per residue and over time.

The analysis wrote the DSSP code of every residue in every frame and a
heatmap of them, and nothing else: how much of the protein is helix, and
how much of the run each residue spent in a strand, had to be counted from
the code matrix by whoever wanted them. They are now written as files, and
the helix and strand fractions over time are given an equilibrated mean
with its error by the statistics every other series goes through.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import mdtraj as md
import numpy as np
import pandas as pd
import pytest

from fastmdxplora.analysis.ss import SS, class_fractions

ASSEMBLIES = Path(__file__).parent / "data" / "assemblies"


@pytest.fixture(scope="module")
def haemoglobin(tmp_path_factory) -> md.Trajectory:
    """1HHO's protein, helical, with frames jittered enough that a few
    residues at the ends of helices change class."""
    folder = tmp_path_factory.mktemp("hho")
    source = folder / "1HHO.pdb"
    source.write_bytes(gzip.decompress((ASSEMBLIES / "1HHO.pdb.gz").read_bytes()))
    structure = md.load(str(source))
    structure = structure.atom_slice(structure.topology.select("protein"))
    rng = np.random.default_rng(5)
    xyz = structure.xyz + rng.normal(0.0, 0.02, (40,) + structure.xyz.shape[1:])
    return md.Trajectory(xyz.astype(np.float32), structure.topology,
                         time=np.arange(40) * 10.0)


def _by_hand(traj: md.Trajectory) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DSSP's eight codes counted directly, without the analysis's helper:
    helix H, G, I; strand E, B; coil the rest; residues DSSP assigns NA
    (none here) left out."""
    codes = md.compute_dssp(traj, simplified=False)
    codes = codes[:, codes[0] != "NA"]
    helix = (codes == "H") | (codes == "G") | (codes == "I")
    strand = (codes == "E") | (codes == "B")
    coil = ~(helix | strand)
    return helix, strand, coil


class TestTheFractionsAreTheCodesCounted:
    @pytest.mark.parametrize("simplified", [False, True])
    def test_per_residue_and_per_frame(self, tmp_path, haemoglobin, simplified) -> None:
        analysis = SS(simplified=simplified, output_dir=tmp_path)
        assert analysis.run(haemoglobin).status == "ok"
        folder = tmp_path / "ss"
        per_residue = pd.read_csv(folder / "ss_fractions_per_residue.csv")
        per_frame = pd.read_csv(folder / "ss_fractions.csv")

        helix, strand, coil = _by_hand(haemoglobin)
        assert list(per_residue.columns) == [
            "chain", "residue", "helix_fraction", "strand_fraction", "coil_fraction"]
        np.testing.assert_allclose(per_residue["helix_fraction"], helix.mean(axis=0))
        np.testing.assert_allclose(per_residue["strand_fraction"], strand.mean(axis=0))
        np.testing.assert_allclose(per_residue["coil_fraction"], coil.mean(axis=0))
        np.testing.assert_allclose(per_frame["helix_fraction"], helix.mean(axis=1))
        np.testing.assert_allclose(per_frame["strand_fraction"], strand.mean(axis=1))
        assert list(per_frame["frame"]) == list(range(haemoglobin.n_frames))
        # Haemoglobin is mostly helix; some residues change class over the run.
        assert 0.6 < per_frame["helix_fraction"].mean() < 0.9
        assert ((per_residue["helix_fraction"] > 0) & (per_residue["helix_fraction"] < 1)).any()

    def test_the_rows_name_the_residues_of_the_code_matrix(self, tmp_path, haemoglobin) -> None:
        analysis = SS(output_dir=tmp_path)
        matrix = analysis.compute(haemoglobin)
        analysis.save_data(matrix, tmp_path / "ss" / "ss.dat")
        per_residue = pd.read_csv(tmp_path / "ss" / "ss_fractions_per_residue.csv")
        named = [f"{c}:{r}" for c, r in zip(per_residue["chain"], per_residue["residue"])]
        assert named == [c for c in matrix.columns if c != "frame"]


class TestTheMeansAreThoseOfEverySeries:
    def test_recorded_as_any_series_is(self, tmp_path, haemoglobin) -> None:
        from fastmdxplora.statistics import mean_record

        analysis = SS(output_dir=tmp_path)
        analysis.run(haemoglobin)
        helix, strand, _ = _by_hand(haemoglobin)
        written = json.loads((tmp_path / "ss" / "options.json").read_text(encoding="utf-8"))
        for name, indicator in (("helix_fraction", helix), ("strand_fraction", strand)):
            expected = mean_record(indicator.mean(axis=1), frame_interval_ns=0.01)
            expected["unit"] = ""
            assert written["findings"][name] == json.loads(json.dumps(expected, default=str))

    def test_the_helper_counts_every_class_once(self) -> None:
        codes = np.array([["H", "G", "I", "E", "B", "T", "S", " ", "C"]])
        per_frame, per_residue = class_fractions(codes)
        np.testing.assert_allclose(per_frame, [[3 / 9, 2 / 9, 4 / 9]])
        np.testing.assert_allclose(per_residue.sum(axis=1), 1.0)
        assert per_residue[:, 0].tolist() == [1, 1, 1, 0, 0, 0, 0, 0, 0]
