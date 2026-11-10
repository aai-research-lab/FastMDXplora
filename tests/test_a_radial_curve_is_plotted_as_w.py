"""Along a distance between centres the curve is plotted as W(r), and the
binding integral reads A(r).

A(r) = -kT ln P(r) carries the room in a shell, 4 pi r^2; W(r) = A(r) +
2kT ln r does not. A free particle in a shell has A = -2kT ln r + const and
a W that is flat, which is the exact answer checked here, at a temperature
other than 300 K so the kT used is the study's.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from fastmdxplora.simulation.binding import KB_KJMOL
from fastmdxplora.simulation.umbrella import (
    SHELL_COORDINATES, UmbrellaPlan, Window, compute_pmf, describe_pmf)

TEMPERATURE = 350.0
KT = KB_KJMOL * TEMPERATURE
GRID = np.linspace(0.3, 2.1, 180001)
#: A free particle: nothing but the shell.
A_FREE = -2.0 * KT * np.log(GRID)


def _plan(centres, force=800.0, variable="ligand_distance"):
    return UmbrellaPlan(
        windows=tuple(Window(index=i, centre=float(c), force_constant=force)
                      for i, c in enumerate(centres)),
        collective_variable=variable, equilibration_fraction=0.2,
        minimum_overlap=0.0, minimum_samples=10, cone=None,
        bootstrap_resamples=0)


def _recombined(seed=0, samples=20000, also=None, resamples=0):
    plan = _plan(np.linspace(0.4, 2.0, 25))
    rng = np.random.default_rng(seed)
    picked = {}
    for window in plan.windows:
        weight = np.exp(-(A_FREE + 0.5 * window.force_constant
                          * (GRID - window.centre) ** 2) / KT)
        picked[window.index] = rng.choice(GRID, size=samples, p=weight / weight.sum())
    logging.disable(logging.WARNING)
    try:
        return compute_pmf(picked, plan, temperature_K=TEMPERATURE,
                           bootstrap_resamples=resamples, also=also)
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture(scope="module")
def free():
    return _recombined()


class TestAFreeParticleInAShell:
    def test_w_is_flat_and_a_falls_by_2kt_ln_r(self, free) -> None:
        pmf = free["pmf"]
        r = np.asarray(pmf["coordinate"])
        w = np.asarray(pmf["potential_of_mean_force_kjmol"], dtype=float)
        a = np.asarray(pmf["free_energy_kjmol"], dtype=float)
        # A across the curve: 2kT ln(r2 / r1) at 350 K, about 10.7 kJ/mol
        # over the curve's range; W flat to its noise. With kT at 300 K in
        # place of 350, W would slope by 2 (kT350 - kT300) = 0.83 kJ/mol per
        # unit of ln r; it slopes by a few hundredths.
        fall = 2.0 * KT * np.log(r[-1] / r[0])
        assert a[0] - a[-1] == pytest.approx(fall, abs=0.3)
        slope = np.polyfit(np.log(r), w, 1)[0]
        assert abs(slope) < 0.2
        assert np.ptp(w) < 0.8
        # Nothing to cross: a flat W has no barrier.
        assert free["summary"]["barrier_kjmol"] is None
        assert free["summary"]["read_off"] == "potential_of_mean_force_kjmol"

    def test_both_curves_are_named_with_the_study_s_kt(self, free) -> None:
        curves = free["pmf"]["curves"]
        assert set(curves) == {"free_energy_kjmol", "potential_of_mean_force_kjmol"}
        assert "binding free energy" in curves["free_energy_kjmol"]
        assert f"kT = {KT:.4f} kJ/mol at 350 K" in curves["potential_of_mean_force_kjmol"]
        assert "nm" in curves["free_energy_kjmol"]
        assert free["temperature_K"] == TEMPERATURE

    def test_the_binding_reader_is_handed_a(self) -> None:
        # `also` is how the binding free energy rides on the resamples; it
        # is given A(r), which falls by 2kT ln(r2 / r1), and not W(r).
        seen = []
        _recombined(samples=4000, resamples=2, also=lambda r, e: seen.append(
            (np.asarray(r), np.asarray(e, dtype=float))) or 0.0)
        r, e = seen[0]
        assert e[0] - e[-1] == pytest.approx(2.0 * KT * np.log(r[-1] / r[0]), abs=0.6)


class TestTheAnalysisSaysW:
    def _record(self, tmp_path):
        r = np.linspace(0.4, 2.0, 50)
        a = -2.0 * KT * np.log(r)
        (tmp_path / "pmf.json").write_text(json.dumps({
            "pmf": {"coordinate": list(r), "free_energy_kjmol": list(a - a.min())},
            "plan": {"collective_variable": "ligand_distance"},
            "temperature_K": TEMPERATURE, "covered": [0.4, 2.0],
            "refused": None, "unsampled_bins": 0, "n_windows": 25,
        }), encoding="utf-8")
        return r

    def test_an_older_record_s_w_is_exactly_flat(self, tmp_path) -> None:
        from fastmdxplora.analysis.pmf import PMF

        self._record(tmp_path)
        analysis = PMF(output_dir=tmp_path / "analysis")
        result = analysis.compute(None)
        assert np.max(np.abs(result["potential_of_mean_force_kjmol"])) < 1e-9
        assert result["summary"]["read_off"] == "potential_of_mean_force_kjmol"

    def test_its_axes_and_data_file_name_both(self, tmp_path) -> None:
        from fastmdxplora.analysis.pmf import PMF

        self._record(tmp_path)
        analysis = PMF(output_dir=tmp_path / "analysis")
        result = analysis.compute(None)
        assert analysis.default_xlabel() == "r, distance between the centres (nm)"
        assert analysis.default_ylabel() == "W(r), potential of mean force (kJ/mol)"
        lines = analysis.save_data(result, tmp_path / "pmf.dat").read_text().splitlines()
        assert lines[0] == "# coordinate free_energy_kjmol potential_of_mean_force_kjmol"
        header = "\n".join(line for line in lines if line.startswith("#"))
        assert "r in nm" in header
        assert f"kT = {KT:.4f} kJ/mol at 350 K" in header
        assert "binding free energy reads" in header

    def test_a_torsion_s_axis_is_in_radians(self, tmp_path) -> None:
        from fastmdxplora.analysis.pmf import PMF

        x = np.linspace(-np.pi, np.pi, 40, endpoint=False)
        (tmp_path / "pmf.json").write_text(json.dumps({
            "pmf": {"coordinate": list(x), "free_energy_kjmol": list(1.0 - np.cos(x))},
            "plan": {"collective_variable": "torsion"},
            "temperature_K": TEMPERATURE, "covered": [-np.pi, np.pi],
            "summary": describe_pmf(x, list(1.0 - np.cos(x))),
            "refused": None, "unsampled_bins": 0, "n_windows": 24,
        }), encoding="utf-8")
        analysis = PMF(output_dir=tmp_path / "analysis")
        analysis.compute(None)
        assert analysis.default_xlabel() == "Torsion (rad)"
        assert analysis.default_ylabel() == "Free energy (kJ/mol)"


class TestEveryReaderReadsTheCurveItMeans:
    def test_the_coordinates_with_a_volume_are_the_shell_s(self) -> None:
        from fastmdxplora.batch import explorer

        for variable in ("ligand_distance", "distance", "torsion", "angle",
                         "ligand_rmsd", None):
            plan = _plan([0.5, 0.6], variable=variable)
            assert (explorer._the_coordinate_has_a_volume(plan)
                    == (variable in SHELL_COORDINATES))

    def test_the_study_recombines_at_its_own_temperature(self, tmp_path,
                                                         monkeypatch) -> None:
        from fastmdxplora.batch import explorer as explorer_module
        from fastmdxplora.simulation import umbrella
        from fastmdxplora.simulation.umbrella import expand_umbrella

        seen = {}
        monkeypatch.setattr(umbrella, "collect_samples", lambda *a, **k: {})
        monkeypatch.setattr(umbrella, "compute_pmf",
                            lambda *a, **k: seen.update(k) or {"pmf": None})
        batch = explorer_module.BatchExplorer.__new__(explorer_module.BatchExplorer)
        batch._raw = expand_umbrella({
            "systems": [{"id": "s", "system": "x.pdb"}],
            "simulation": {"temperature_K": TEMPERATURE, "umbrella": {
                "collective_variable": "distance", "select_atoms_a": "resid 1",
                "select_atoms_b": "resid 2", "force_constant": 1000,
                "from": 0.4, "to": 1.2, "n_windows": 5}}})
        batch.run_specs = []
        batch.output_dir = tmp_path
        try:
            batch._maybe_build_pmf(bootstrap_resamples=0)
        except Exception:  # noqa: BLE001 - only the call to compute_pmf is checked
            pass
        assert seen.get("temperature_K") == TEMPERATURE


class TestTheComparisonReportSaysWhichCurve:
    def _study(self, tmp_path, w):
        (tmp_path / "comparison").mkdir()
        (tmp_path / "pmf.json").write_text(json.dumps({
            "pmf": {"coordinate": [0.5, 1.0], "free_energy_kjmol": [0.0, 1.0],
                    "potential_of_mean_force_kjmol": w},
            "refused": None}), encoding="utf-8")
        picked = tmp_path / "free_energy" / "pmf"
        picked.mkdir(parents=True)
        (picked / "pmf.png").write_bytes(b"")
        return tmp_path / "comparison" / "comparison_report.md"

    def test_along_a_distance_it_names_w_and_a(self, tmp_path) -> None:
        from fastmdxplora.batch.compare import (
            _umbrella_is_radial, _umbrella_preamble, _umbrella_result)

        md = self._study(tmp_path, [0.0, 0.0])
        text = "\n".join(_umbrella_preamble(*_umbrella_result(md),
                                            radial=_umbrella_is_radial(md)))
        assert "W(r) = A(r) + 2kT ln r" in text
        assert "binding free energy is integrated from A(r)" in text

    def test_along_a_torsion_it_does_not(self, tmp_path) -> None:
        from fastmdxplora.batch.compare import (
            _umbrella_is_radial, _umbrella_preamble, _umbrella_result)

        md = self._study(tmp_path, None)
        assert not _umbrella_is_radial(md)
        text = "\n".join(_umbrella_preamble(*_umbrella_result(md),
                                            radial=_umbrella_is_radial(md)))
        assert "W(r)" not in text
