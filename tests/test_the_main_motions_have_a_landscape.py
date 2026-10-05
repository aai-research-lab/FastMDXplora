"""The free-energy landscape on the first two principal components.

G = -kT ln P, P normalised over the bin area, in kJ/mol at the temperature
the study recorded, and in units of kT where it recorded none.
"""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.dimred import DimRed, free_energy_landscape
from fastmdxplora.analysis.reweight import KB_KJ_PER_MOL_K


def _two_basins(n_frames: int = 400) -> md.Trajectory:
    """Twelve alpha carbons hopping between two shapes, three quarters of the
    frames in the first, so the basins differ by kT ln 3."""
    rng = np.random.default_rng(2)
    base = rng.normal(0, 0.6, (12, 3))
    mode = rng.normal(0, 1, (12, 3))
    mode -= mode.mean(axis=0)
    mode /= np.linalg.norm(mode)
    frames = []
    for f in range(n_frames):
        side = 0.0 if f % 4 else 1.0
        frames.append(base + 0.8 * side * mode + rng.normal(0, 0.01, (12, 3)))
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(12):
        top.add_atom("CA", md.element.carbon, top.add_residue("ALA", chain))
    return md.Trajectory(np.array(frames, dtype=np.float32), top,
                         time=10.0 * (np.arange(n_frames) + 1))


class TestTheFormula:
    def test_it_is_minus_kt_ln_p_from_its_lowest_bin(self):
        x = np.array([0.1, 0.1, 0.1, 0.9])
        y = np.array([0.1, 0.1, 0.1, 0.9])
        found = free_energy_landscape(x, y, bins=2, temperature_K=300.0)

        kt = KB_KJ_PER_MOL_K * 300.0
        energy = found["free_energy"]
        assert found["unit"] == "kJ/mol"
        assert np.nanmin(energy) == 0.0
        assert energy[1, 1] == pytest.approx(kt * np.log(3.0))
        assert np.isnan(energy[0, 1]) and np.isnan(energy[1, 0])     # empty, masked

    def test_p_is_a_density_over_the_bin_area(self):
        rng = np.random.default_rng(0)
        found = free_energy_landscape(rng.normal(size=500), 3 * rng.normal(size=500), bins=10)
        area = np.outer(np.diff(found["edges_x"]), np.diff(found["edges_y"]))

        assert (found["density"] * area).sum() == pytest.approx(1.0)

    def test_without_a_temperature_it_is_in_units_of_kt(self):
        x = np.array([0.1, 0.1, 0.1, 0.9])
        found = free_energy_landscape(x, x, bins=2, temperature_K=None)

        assert found["unit"] == "kT"
        assert found["free_energy"][1, 1] == pytest.approx(np.log(3.0))


class TestTheAnalysis:
    def test_with_the_study_temperature_it_is_in_kj_per_mol(self, tmp_path):
        simulation = tmp_path / "simulation"
        simulation.mkdir()
        (simulation / "simulation_parameters.json").write_text(
            json.dumps({"parameters": {"temperature_K": 310.0}}), encoding="utf-8")
        analysis = DimRed(methods=["pca"], landscape_bins=20,
                          output_dir=tmp_path / "analysis")
        result = analysis.run(_two_basins())
        assert result.status == "ok", result.message

        folder = tmp_path / "analysis" / "dimred"
        saved = np.load(folder / "dimred_pca_landscape.npz")
        assert str(saved["unit"]) == "kJ/mol"
        assert float(saved["temperature_K"]) == 310.0
        assert saved["free_energy"].shape == (20, 20)
        assert saved["edges_pc1"].shape == (21,) and saved["edges_pc2"].shape == (21,)
        assert np.nanmin(saved["free_energy"]) == 0.0
        # The two basins hold a quarter and three quarters of the frames.
        area = np.outer(np.diff(saved["edges_pc1"]), np.diff(saved["edges_pc2"]))
        mass = saved["density"] * area
        centres = 0.5 * (saved["edges_pc1"][1:] + saved["edges_pc1"][:-1])
        middle = 0.5 * (saved["edges_pc1"][0] + saved["edges_pc1"][-1])
        shares = sorted([mass[centres < middle].sum(), mass[centres >= middle].sum()])
        assert shares == pytest.approx([0.25, 0.75], abs=1e-9)
        # And each occupied bin's free energy is -kT ln P of its density.
        kt = KB_KJ_PER_MOL_K * 310.0
        occupied = saved["counts"] > 0
        expected = -kt * np.log(saved["density"][occupied])
        assert np.allclose(saved["free_energy"][occupied], expected - expected.min())
        assert (folder / "dimred_pca_landscape.png").is_file()
        assert analysis.findings["landscape"]["unit"] == "kJ/mol"

    def test_without_one_it_says_so_and_stays_in_kt(self, tmp_path):
        analysis = DimRed(methods=["pca"], output_dir=tmp_path / "analysis")
        result = analysis.run(_two_basins())
        assert result.status == "ok", result.message

        saved = np.load(tmp_path / "analysis" / "dimred" / "dimred_pca_landscape.npz")
        assert str(saved["unit"]) == "kT"
        assert np.isnan(float(saved["temperature_K"]))
        assert "kT" in analysis.findings["landscape"]["said"]

    def test_the_figure_says_its_unit(self, tmp_path):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from fastmdxplora.analysis.dimred import _plot_landscape

        x = np.array([0.1, 0.1, 0.1, 0.9])
        fig, ax = plt.subplots()
        _plot_landscape(ax, free_energy_landscape(x, x, bins=2), np.array([0.7, 0.2]))
        labels = (ax.get_xlabel(), fig.axes[-1].get_ylabel())
        plt.close(fig)
        assert labels == ("PC 1 (70.0%), nm", "Free energy (kT)")

    def test_too_few_bins_are_refused(self):
        from fastmdxplora.refusals import StudyError

        with pytest.raises(StudyError) as raised:
            DimRed(landscape_bins=1)
        assert raised.value.code == "analysis.option.out_of_range"
