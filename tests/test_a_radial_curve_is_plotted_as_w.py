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


class TestTheComparisonReportGivesTheBarrier:
    def test_in_one_line(self, tmp_path) -> None:
        from fastmdxplora.batch.compare import (
            _umbrella_barrier, _umbrella_is_radial, _umbrella_preamble,
            _umbrella_result)

        (tmp_path / "comparison").mkdir()
        x = np.linspace(-np.pi, np.pi, 40, endpoint=False)
        e = 7.5 * (1.0 - np.cos(x))
        (tmp_path / "pmf.json").write_text(json.dumps({
            "pmf": {"coordinate": list(x), "free_energy_kjmol": list(e)},
            "plan": {"collective_variable": "torsion"}, "refused": None,
            "summary": describe_pmf(x, list(e), errors=[0.2] * 40, periodic=True,
                                    closed=True, unit="rad")}), encoding="utf-8")
        picked = tmp_path / "free_energy" / "pmf"
        picked.mkdir(parents=True)
        (picked / "pmf.png").write_bytes(b"")
        md = tmp_path / "comparison" / "comparison_report.md"
        text = "\n".join(_umbrella_preamble(*_umbrella_result(md),
                                            radial=_umbrella_is_radial(md),
                                            barrier=_umbrella_barrier(md)))
        assert "**Barrier:** 15.0 +/- 0.3 kJ/mol at -3.14 rad, on the free energy." in text


def _a_well(depth, at=0.45, width=0.06):
    r = np.linspace(0.4, 2.0, 50)
    w = -depth * np.exp(-((r - at) / width) ** 2)
    kt = KB_KJMOL * 300.0
    a = w - 2.0 * kt * np.log(r)
    return r, a - a.min()


class TestTheBoundStateIsWsWell:
    """The binding free energy's bound range began at A's minimum, which for
    a shallow well near the first window is in bulk: a 6 kJ/mol well at
    0.45 nm had its minimum at 1.98 nm, its range out to 1.63 nm and a
    number (-6.15) more negative than a 12 kJ/mol well's (-5.58)."""

    def test_a_shallow_well_is_found_where_it_is(self) -> None:
        from fastmdxplora.simulation.binding import binding_free_energy

        got = {}
        for depth in (6.0, 12.0):
            r, a = _a_well(depth)
            got[depth] = binding_free_energy(r, list(a), temperature_K=300.0)
            assert got[depth]["minimum_at_nm"] == pytest.approx(0.45, abs=0.02)
            assert 0.45 < got[depth]["bound_cutoff_nm"] < 0.7
        assert got[6.0]["delta_g_kjmol"] > got[12.0]["delta_g_kjmol"]

    def test_its_number_is_the_closed_form(self) -> None:
        # The integral stays on A: with the range ending where W is back
        # within kT of bulk, the closed form over the same range.
        from fastmdxplora.simulation.binding import (
            STANDARD_VOLUME_NM3, binding_free_energy)

        r, a = _a_well(12.0)
        got = binding_free_energy(r, list(a), temperature_K=300.0)
        kt = KB_KJMOL * 300.0
        w = -12.0 * np.exp(-((r - 0.45) / 0.06) ** 2)
        inside = r <= got["bound_cutoff_nm"]
        exact = -kt * np.log(4.0 * np.pi * np.trapezoid(
            r[inside] ** 2 * np.exp(-w[inside] / kt), r[inside]) / STANDARD_VOLUME_NM3)
        assert got["delta_g_kjmol"] == pytest.approx(exact, abs=0.05)

    def test_the_reference_checks_are_given_w_s_well(self, tmp_path,
                                                     monkeypatch) -> None:
        from types import SimpleNamespace

        from fastmdxplora.batch import explorer as explorer_module
        from fastmdxplora.simulation import reference_state

        seen = {}
        monkeypatch.setattr(reference_state, "check_the_reference",
                            lambda *a, **k: seen.update(k) or {})
        batch = explorer_module.BatchExplorer.__new__(explorer_module.BatchExplorer)
        batch.run_specs = [SimpleNamespace(options={"simulation": {"umbrella": {
            "index": 0, "ligand_name": "BEN", "site_selection": "resid 1"}}})]
        r, a = _a_well(6.0)
        plan = _plan([0.4, 2.0])
        batch._with_the_reference_checked(
            {"bulk_from_nm": 1.6}, {"coordinate": list(r), "free_energy_kjmol": list(a)},
            plan, {0: tmp_path}, temperature=300.0, cone=None)
        assert seen["bound_at_nm"] == pytest.approx(0.45, abs=0.02)


@pytest.fixture(scope="module")
def well():
    grid = np.linspace(0.25, 2.1, 185001)
    kt = KB_KJMOL * 300.0
    w_true = -6.0 * np.exp(-((grid - 0.5) / 0.08) ** 2)
    a_true = w_true - 2.0 * kt * np.log(grid)
    plan = _plan(np.linspace(0.4, 2.0, 30), force=1500.0)
    rng = np.random.default_rng(4)
    picked = {}
    for window in plan.windows:
        weight = np.exp(-(a_true + 0.5 * window.force_constant
                          * (grid - window.centre) ** 2) / kt)
        picked[window.index] = rng.choice(grid, size=20000, p=weight / weight.sum())
    logging.disable(logging.WARNING)
    try:
        return compute_pmf(picked, plan, temperature_K=300.0, bootstrap_resamples=0)
    finally:
        logging.disable(logging.NOTSET)


class TestARecombinedWellIsReadOffW:
    def test_its_minimum_is_the_well_at_zero(self, well) -> None:
        # Read off A, the well's bin stood 1.4 kJ/mol above A's lowest
        # point, at the outer end.
        deepest = well["summary"]["minima"][0]
        assert deepest["coordinate"] == pytest.approx(0.5, abs=0.04)
        assert deepest["free_energy_kjmol"] == pytest.approx(0.0, abs=1e-9)

    def test_its_plateau_is_the_well_s_depth(self, well) -> None:
        # W in bulk is the well's depth above it; with 1.5 or 3 in place of
        # 2 in front of kT ln r it would be 6 -/+ kT ln(r / 0.5), 1.7 off at
        # 2 nm.
        r = np.asarray(well["pmf"]["coordinate"])
        w = np.asarray(well["pmf"]["potential_of_mean_force_kjmol"], dtype=float)
        bulk = r > 1.4
        assert np.mean(w[bulk]) == pytest.approx(6.0, abs=0.4)
        assert well["summary"]["highest_kjmol"] == pytest.approx(6.0, abs=0.6)


class TestTheInnerEndIsNotAWell:
    def test_w_is_zero_between_the_windows_centres(self, free) -> None:
        # The end bin below the first window holds the extreme sample and
        # reads low; W is zeroed between the windows' centres, so the free
        # particle's W there is flat about 0, not 2.7 above a dip.
        r = np.asarray(free["pmf"]["coordinate"])
        w = np.asarray(free["pmf"]["potential_of_mean_force_kjmol"], dtype=float)
        between = (r >= 0.4) & (r <= 2.0)
        assert np.nanmin(w[between]) == pytest.approx(0.0, abs=1e-12)
        assert np.nanmax(w[between]) < 0.8
        assert "between the outermost windows' centres" in \
            free["pmf"]["curves"]["potential_of_mean_force_kjmol"]

    def test_beyond_them_it_is_plotted_faint(self, tmp_path) -> None:
        import matplotlib.pyplot as plt

        from fastmdxplora.analysis.pmf import PMF

        r = np.linspace(0.25, 2.0, 50)
        a = -2.0 * KT * np.log(r)
        a[0] -= 2.7
        (tmp_path / "pmf.json").write_text(json.dumps({
            "pmf": {"coordinate": list(r), "free_energy_kjmol": list(a - a.min())},
            "plan": {"collective_variable": "ligand_distance"},
            "temperature_K": TEMPERATURE, "covered": [0.4, 2.0],
            "refused": None, "unsampled_bins": 0, "n_windows": 25,
        }), encoding="utf-8")
        analysis = PMF(output_dir=tmp_path / "analysis")
        result = analysis.compute(None)
        w = result["potential_of_mean_force_kjmol"]
        assert np.min(w[r >= 0.4]) == pytest.approx(0.0, abs=1e-9)
        fig, ax = plt.subplots()
        analysis.plot(result, ax)
        labels = [line.get_label() for line in ax.get_lines()]
        assert "beyond the outermost windows' centres" in labels
        solid = next(line for line in ax.get_lines()
                     if line.get_label() == "W(r) = A(r) + 2kT ln r")
        assert np.isnan(solid.get_ydata()[0])
        plt.close(fig)


class TestATorsionsDataFileSaysItsUnits:
    def test_rad(self, tmp_path) -> None:
        from fastmdxplora.analysis.pmf import PMF

        x = np.linspace(-np.pi, np.pi, 40, endpoint=False)
        (tmp_path / "pmf.json").write_text(json.dumps({
            "pmf": {"coordinate": list(x), "free_energy_kjmol": list(1.0 - np.cos(x))},
            "plan": {"collective_variable": "torsion"},
            "temperature_K": TEMPERATURE, "covered": [-np.pi, np.pi],
            "refused": None, "unsampled_bins": 0, "n_windows": 24,
        }), encoding="utf-8")
        analysis = PMF(output_dir=tmp_path / "analysis")
        result = analysis.compute(None)
        lines = analysis.save_data(result, tmp_path / "pmf.dat").read_text().splitlines()
        assert lines[0] == "# coordinate free_energy_kjmol"
        assert lines[1] == ("# coordinate is the torsion in rad; free_energy_kjmol "
                            "in kJ/mol, zero at its lowest bin.")


class TestTheConsoleGivesTheBarrier:
    def test_in_one_line(self, tmp_path, monkeypatch, capsys) -> None:
        from fastmdxplora.batch import explorer as explorer_module
        from fastmdxplora.simulation import umbrella
        from fastmdxplora.simulation.umbrella import expand_umbrella

        r = np.linspace(0.4, 2.0, 41)
        e = 6.0 * np.exp(-((r - 1.0) / 0.08) ** 2)
        summary = {**describe_pmf(r, list(e), errors=[2.5] * 41, unit="nm"),
                   "read_off": "potential_of_mean_force_kjmol"}
        monkeypatch.setattr(umbrella, "collect_samples", lambda *a, **k: {})
        monkeypatch.setattr(umbrella, "design_from_a_pilot", lambda *a, **k: {})
        monkeypatch.setattr(umbrella, "compute_pmf", lambda *a, **k: {
            "pmf": None, "refused": None, "summary": summary})
        batch = explorer_module.BatchExplorer.__new__(explorer_module.BatchExplorer)
        batch._raw = expand_umbrella({
            "systems": [{"id": "s", "system": "x.pdb"}],
            "simulation": {"umbrella": {
                "collective_variable": "distance", "select_atoms_a": "resid 1",
                "select_atoms_b": "resid 2", "force_constant": 1000,
                "from": 0.4, "to": 1.2, "n_windows": 5}}})
        batch.run_specs = []
        batch.output_dir = tmp_path
        batch._draw_pmf = lambda path: None
        batch._maybe_build_pmf(bootstrap_resamples=0)
        printed = [line for line in capsys.readouterr().out.splitlines()
                   if line.startswith("Barrier:")]
        assert printed == [
            "Barrier:        none given. The highest peak, 6.0 kJ/mol at 1 nm, "
            f"stands less than {summary['within_error'][0]['multiplier']:.2f} times "
            "its error (3.5 kJ/mol) above the lowest point beside it."]


def _radial_study(wfun, centres, force, samples, seed, resamples=40):
    """Windows along r sampled from exp(-(A + spring)/kT), A = W - 2kT ln r,
    continuous values, recombined with a bootstrap."""
    plan = UmbrellaPlan(
        windows=tuple(Window(index=i, centre=float(c), force_constant=force)
                      for i, c in enumerate(centres)),
        collective_variable="ligand_distance", equilibration_fraction=0.2,
        minimum_overlap=0.0, minimum_samples=50, cone=None,
        bootstrap_resamples=resamples)
    kt = KB_KJMOL * 300.0
    grid = np.linspace(0.15, 2.2, 200001)
    step = grid[1] - grid[0]
    a = wfun(grid) - 2.0 * kt * np.log(grid)
    rng = np.random.default_rng(seed)
    picked = {}
    for window in plan.windows:
        weight = np.exp(-(a + 0.5 * force * (grid - window.centre) ** 2) / kt)
        picked[window.index] = (rng.choice(grid, size=samples, p=weight / weight.sum())
                                + rng.uniform(0.0, step, size=samples))
    logging.disable(logging.WARNING)
    try:
        return compute_pmf(picked, plan, temperature_K=300.0,
                           bootstrap_resamples=resamples, bootstrap_seed=seed)
    finally:
        logging.disable(logging.NOTSET)


def _well_and_hump(depth, hump):
    return lambda r: (-depth * np.exp(-((r - 0.6) / 0.1) ** 2)
                      + hump * np.exp(-((r - 1.0) / 0.08) ** 2))


class TestARadialBarrierIsJudgedOnW:
    """A well at 0.6 nm and an escape hump 5 kJ/mol above bulk at 1.0 nm.
    Judged against A's resamples, every departure carried 2kT ln(r_i / r_j),
    the multiplier came out at 21 to 26 and the barrier was refused as
    noise; judged against W's, about 3.8."""

    @pytest.mark.parametrize("depth, exact", [(15.0, 20.0), (10.0, 15.0)])
    def test_it_is_given(self, depth, exact) -> None:
        payload = _radial_study(_well_and_hump(depth, 5.0), np.linspace(0.4, 2.0, 33),
                                2000.0, 2400, seed=1)
        summary = payload["summary"]
        assert summary["barrier_kjmol"] == pytest.approx(exact, abs=0.6)
        assert summary["barrier_at"] == pytest.approx(1.0, abs=0.03)
        assert 3.0 < summary["barrier_multiplier"] < 5.0
        assert summary["resamples_on"] == "potential_of_mean_force_kjmol"

    def test_an_older_record_is_judged_again_on_w(self, tmp_path) -> None:
        from fastmdxplora.analysis.pmf import PMF

        payload = _radial_study(_well_and_hump(15.0, 5.0), np.linspace(0.4, 2.0, 33),
                                2000.0, 2400, seed=1)
        # As the round before saved it: judged against A's resamples.
        payload["summary"].pop("resamples_on")
        payload["summary"]["barrier_kjmol"] = None
        payload["plan"] = {"collective_variable": "ligand_distance"}
        (tmp_path / "pmf.json").write_text(json.dumps(payload), encoding="utf-8")
        result = PMF(output_dir=tmp_path / "analysis").compute(None)
        assert result["summary"]["barrier_kjmol"] == pytest.approx(20.0, abs=0.6)
        assert 3.0 < result["summary"]["barrier_multiplier"] < 5.0


def _a_well_with_a_low_end_bin():
    """A 4 kJ/mol well at 1.0 nm; the windows from 0.4 nm; below them the
    curve open and free, its end bin at 0.2 nm 5 kJ/mol low, as the bin
    holding the extreme sample reads."""
    kt = KB_KJMOL * 300.0
    r = np.linspace(0.2, 2.0, 55)
    w = -4.0 * np.exp(-((r - 1.0) / 0.1) ** 2)
    w[0] -= 5.0
    a = w - 2.0 * kt * np.log(r)
    return r, a - a.min(), w


class TestTheBoundStateIsSearchedWhereTheWindowsWere:
    def test_the_end_bin_does_not_take_the_well(self) -> None:
        from fastmdxplora.simulation.binding import (
            STANDARD_VOLUME_NM3, binding_free_energy)

        r, a, w = _a_well_with_a_low_end_bin()
        got = binding_free_energy(r, list(a), temperature_K=300.0,
                                  covered_nm=(0.4, 2.0), judge_the_bulk=False)
        # Searched over the whole grid the minimum was the end bin and the
        # study was refused as holding no bound state.
        assert got["refused"] is None, got["refused"]
        assert got["minimum_at_nm"] == pytest.approx(1.0, abs=0.02)
        kt = KB_KJMOL * 300.0
        inside = r <= got["bound_cutoff_nm"]
        bulk = np.mean(w[-14:])
        exact = -kt * np.log(4.0 * np.pi * np.trapezoid(
            r[inside] ** 2 * np.exp(-(w[inside] - bulk) / kt), r[inside])
            / STANDARD_VOLUME_NM3)
        assert got["delta_g_kjmol"] == pytest.approx(exact, abs=1e-6)

    def test_the_reference_checks_are_given_the_well_too(self, tmp_path,
                                                         monkeypatch) -> None:
        from types import SimpleNamespace

        from fastmdxplora.batch import explorer as explorer_module
        from fastmdxplora.simulation import reference_state

        seen = {}
        monkeypatch.setattr(reference_state, "check_the_reference",
                            lambda *a, **k: seen.update(k) or {})
        batch = explorer_module.BatchExplorer.__new__(explorer_module.BatchExplorer)
        batch.run_specs = [SimpleNamespace(options={"simulation": {"umbrella": {
            "index": 0, "ligand_name": "BEN", "site_selection": "resid 1"}}})]
        r, a, _ = _a_well_with_a_low_end_bin()
        batch._with_the_reference_checked(
            {"bulk_from_nm": 1.6}, {"coordinate": list(r), "free_energy_kjmol": list(a)},
            _plan([0.4, 2.0]), {0: tmp_path}, temperature=300.0, cone=None)
        assert seen["bound_at_nm"] == pytest.approx(1.0, abs=0.02)


class TestAHoleIsJudgedAgainstTheBoundState:
    def test_against_w_s_minimum_not_a_s_in_bulk(self) -> None:
        # A 6 kJ/mol well at 0.45 nm, its wall bin 14.2 kJ/mol (5.7 kT) above
        # the well's bottom and an empty bin inside it. Against A's lowest
        # point, out in bulk, the wall stood 6.5 kT high and the empty bin
        # was taken as holding nobody; against the bound state it is 5.7.
        from fastmdxplora.simulation.binding import binding_free_energy

        kt = KB_KJMOL * 300.0
        r = np.linspace(0.4, 2.0, 50)
        w = -6.0 * np.exp(-((r - 0.45) / 0.06) ** 2)
        w[1] = w[2] + 14.2
        a = w - 2.0 * kt * np.log(r)
        energy = list(a - a.min())
        energy[0] = None
        got = binding_free_energy(r, energy, temperature_K=300.0, covered_nm=(0.4, 2.0))
        assert got["delta_g_kjmol"] is None
        assert "hold no samples" in got["refused"]


class TestTheDataFileSaysWhereWIsZero:
    def test_header(self, tmp_path) -> None:
        from fastmdxplora.analysis.pmf import PMF

        r = np.linspace(0.25, 2.0, 50)
        a = -2.0 * KT * np.log(r)
        (tmp_path / "pmf.json").write_text(json.dumps({
            "pmf": {"coordinate": list(r), "free_energy_kjmol": list(a - a.min())},
            "plan": {"collective_variable": "ligand_distance"},
            "temperature_K": TEMPERATURE, "covered": [0.4, 2.0],
            "refused": None, "unsampled_bins": 0, "n_windows": 25,
        }), encoding="utf-8")
        analysis = PMF(output_dir=tmp_path / "analysis")
        header = analysis.save_data(analysis.compute(None),
                                    tmp_path / "pmf.dat").read_text().splitlines()[1]
        assert "W(r) at its lowest bin between the outermost windows' centres" in header

    def test_an_older_record_s_w_is_zeroed_there_too(self, tmp_path) -> None:
        from fastmdxplora.analysis.pmf import PMF

        # Saved before the rule: W zeroed at the end bin, which read 2.7 low.
        r = np.linspace(0.25, 2.0, 50)
        w = np.zeros(50)
        w[0] = -2.7
        w -= w.min()
        a = w - 2.0 * KT * np.log(r)
        (tmp_path / "pmf.json").write_text(json.dumps({
            "pmf": {"coordinate": list(r), "free_energy_kjmol": list(a - a.min()),
                    "potential_of_mean_force_kjmol": list(w)},
            "plan": {"collective_variable": "ligand_distance"},
            "temperature_K": TEMPERATURE, "covered": [0.4, 2.0],
            "refused": None, "unsampled_bins": 0, "n_windows": 25,
        }), encoding="utf-8")
        got = PMF(output_dir=tmp_path / "analysis").compute(None)
        mean_force = got["potential_of_mean_force_kjmol"]
        assert np.min(mean_force[r >= 0.4]) == pytest.approx(0.0, abs=1e-12)
        assert mean_force[0] == pytest.approx(-2.7)


class TestTheBarrierLineSaysWhatItRestsOn:
    def _line(self, **summary):
        from fastmdxplora.simulation.umbrella import the_barrier_in_a_line

        return the_barrier_in_a_line({
            "plan": {"collective_variable": "torsion"},
            "summary": {"barrier_kjmol": 6.3, "barrier_at": 0.0524, **summary}})

    def test_with_no_error_the_margin_alone(self) -> None:
        assert self._line(barrier_error_kjmol=None) == (
            "6.3 kJ/mol at 0.0524 rad, on the free energy (no error computed; "
            "judged by the 2.5 kJ/mol margin alone).")

    def test_with_one_it_sits_high_by_its_zero(self) -> None:
        from fastmdxplora.simulation.umbrella import THE_CURVE_SITS_HIGH

        assert self._line(barrier_error_kjmol=1.0, barrier_zero=THE_CURVE_SITS_HIGH) == (
            "6.3 +/- 1.0 kJ/mol at 0.0524 rad, on the free energy, about 0.8 of "
            "its error high as the curve's zero is.")


class TestNoWellIsNoBoundState:
    """Searched between the windows' centres, a free particle's flat W had
    its minimum wherever the noise put it and was integrated from there:
    -7.4 to +0.8 kJ/mol on four seeds. A bound range is where W stands more
    than kT below bulk; a curve with nowhere that deep has none."""

    def _curve(self, depth_kt):
        kt = KB_KJMOL * 300.0
        r = np.linspace(0.4, 2.0, 50)
        w = -depth_kt * kt * np.exp(-((r - 0.8) / 0.08) ** 2)
        a = w - 2.0 * kt * np.log(r)
        return r, list(a - a.min())

    def test_shallower_than_kt_is_refused(self) -> None:
        from fastmdxplora.simulation.binding import binding_free_energy

        r, a = self._curve(0.9)
        got = binding_free_energy(r, a, temperature_K=300.0, covered_nm=(0.4, 2.0))
        assert got["delta_g_kjmol"] is None
        assert "less than kT" in got["refused"] and "no bound state" in got["refused"]
        assert got["well_depth_kjmol"] == pytest.approx(0.9 * KB_KJMOL * 300.0, abs=0.05)

    def test_deeper_than_kt_is_given(self) -> None:
        from fastmdxplora.simulation.binding import binding_free_energy

        r, a = self._curve(1.2)
        got = binding_free_energy(r, a, temperature_K=300.0, covered_nm=(0.4, 2.0))
        assert got["refused"] is None and got["delta_g_kjmol"] is not None

    def test_a_free_particle_recombined_is_refused(self) -> None:
        from fastmdxplora.simulation.binding import binding_free_energy

        payload = _radial_study(lambda r: 0.0 * r, np.linspace(0.4, 2.0, 25),
                                1000.0, 1600, seed=1, resamples=0)
        got = binding_free_energy(payload["pmf"]["coordinate"],
                                  payload["pmf"]["free_energy_kjmol"],
                                  temperature_K=300.0, covered_nm=(0.4, 2.0))
        assert got["delta_g_kjmol"] is None
        assert "no bound state" in got["refused"]
