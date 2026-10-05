"""The ensemble, read back from the record the run already kept."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.analysis.thermodynamics import (
    Thermodynamics,
    read_state_table,
)

HEADER = ('#"Step","Time (ps)","Potential Energy (kJ/mole)",'
          '"Kinetic Energy (kJ/mole)","Total Energy (kJ/mole)",'
          '"Temperature (K)","Box Volume (nm^3)","Density (g/mL)"')


def _state(tmp_path: Path, *, n=400, vary_volume=True, seed=0) -> Path:
    rng = np.random.RandomState(seed)
    lines = [HEADER]
    for i in range(n):
        pot = -12000 + rng.normal(scale=40)
        kin = 3000 + rng.normal(scale=30)
        temp = 300 + rng.normal(scale=3)
        vol = 64.0 + (rng.normal(scale=0.05) if vary_volume else 0.0)
        dens = 0.997 * 64.0 / vol
        lines.append(
            f'{i * 500},{i * 1.0},{pot:.4f},{kin:.4f},{pot + kin:.4f},'
            f'{temp:.4f},{vol:.6f},{dens:.6f}')
    path = tmp_path / "state_data.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestReadingTheRecord:
    def test_the_header_is_read_with_its_quotes(self, tmp_path):
        """The field names contain commas inside their quotes.

        Splitting the header on commas gives "Potential Energy (kJ/mole)"
        as two columns, and every column after it is then off by one, so
        the density read back would be the box volume.
        """
        table = read_state_table(_state(tmp_path))
        assert "Density (g/mL)" in table
        assert "Potential Energy (kJ/mole)" in table
        assert table["Density (g/mL)"].size == 400

    def test_an_empty_record_is_not_a_crash(self, tmp_path):
        path = tmp_path / "state_data.csv"
        path.write_text(HEADER + "\n", encoding="utf-8")
        assert read_state_table(path) == {}


class TestWhatItReports:
    def test_each_observable_carries_its_own_support(self, tmp_path):
        analysis = Thermodynamics(state_csv=str(_state(tmp_path)))
        result = analysis.compute(None)
        record = analysis.findings["thermodynamics"]

        assert result.shape[1] == 4
        assert "temperature" in record
        assert record["temperature"]["effective_samples"] > 0
        assert record["temperature"]["mean"] == pytest.approx(300, abs=1.0)
        assert record["ensemble"] == "constant pressure"

    def test_a_fixed_box_is_not_a_density_measurement(self, tmp_path):
        """Under NVT the density is a constant the setup chose.

        Quoting a mean and an error on it would describe the arithmetic
        rather than the system, and the spread would be zero, which reads
        as precision rather than as absence.
        """
        analysis = Thermodynamics(
            state_csv=str(_state(tmp_path, vary_volume=False)))
        analysis.compute(None)
        density = analysis.findings["thermodynamics"]["density"]

        assert analysis.findings["thermodynamics"]["ensemble"] == (
            "constant volume")
        assert "not_a_measurement" in density
        assert "constant the setup fixed" in density["not_a_measurement"]
        assert "standard_error" not in density

    def test_a_fixed_box_is_not_called_too_short(self, tmp_path):
        """A constant volume is one observation however long the run, so
        `summarise` refused it as "not long against its own correlation
        time" and named a longer run as the remedy. It is a value the
        ensemble fixed, said as one, beside the density."""
        analysis = Thermodynamics(
            state_csv=str(_state(tmp_path, vary_volume=False)))
        analysis.compute(None)
        volume = analysis.findings["thermodynamics"]["volume"]

        assert "correlation time" not in volume["not_a_measurement"]
        assert "held it constant" in volume["not_a_measurement"]
        assert volume["value"] == pytest.approx(64.0)
        assert "standard_error" not in volume

    def test_a_varying_box_does_give_a_volume(self, tmp_path):
        analysis = Thermodynamics(state_csv=str(_state(tmp_path)))
        analysis.compute(None)
        volume = analysis.findings["thermodynamics"]["volume"]

        assert volume["mean"] == pytest.approx(64.0, abs=0.01)
        assert "standard_error" in volume

    def test_a_varying_box_does_give_a_density(self, tmp_path):
        analysis = Thermodynamics(state_csv=str(_state(tmp_path)))
        analysis.compute(None)
        density = analysis.findings["thermodynamics"]["density"]

        assert "not_a_measurement" not in density
        assert density["mean"] == pytest.approx(0.997, abs=0.01)
        assert density["standard_error"] > 0

    def test_no_record_says_what_is_missing(self, tmp_path):
        analysis = Thermodynamics(state_csv=str(tmp_path / "absent.csv"))
        analysis.output_dir = tmp_path
        with pytest.raises(FileNotFoundError, match="coordinates and not"):
            analysis.compute(None)


class TestItJoinsTheRegisters:
    def test_the_schema_names_it(self):
        from fastmdxplora.config.schema import ANALYSIS_NAMES

        assert "thermodynamics" in ANALYSIS_NAMES

    def test_it_does_not_pretend_to_honour_a_selection(self):
        """The record is of the whole box.

        An atom selection would name a different quantity than the one
        that was written, so the analysis declares that it ignores it
        rather than accepting it and doing nothing.
        """
        assert Thermodynamics.honours_selection is False


class TestTheOutputsItWrites:
    """A plot that crashes on real data is a failed report phase.

    These paths are short and mechanical, which is exactly why nothing
    exercised them: they are the ones discovered broken by a run rather
    than by a test.
    """

    def test_it_writes_a_table_with_one_row_per_observable(self, tmp_path):
        analysis = Thermodynamics(state_csv=str(_state(tmp_path)))
        result = analysis.compute(None)
        written = analysis.save_data(result, tmp_path / "thermodynamics.dat")

        text = written.read_text(encoding="utf-8")
        assert "observable" in text.splitlines()[0]
        assert "temperature" in text

    def test_it_plots_without_a_shared_axis(self, tmp_path):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        analysis = Thermodynamics(state_csv=str(_state(tmp_path)))
        result = analysis.compute(None)
        _fig, ax = plt.subplots()
        analysis.plot(result, ax)
        assert ax.get_yticklabels()
        assert "per cent" in analysis.default_xlabel()
        plt.close("all")

    def test_it_says_so_when_nothing_settled(self, tmp_path):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        analysis = Thermodynamics(state_csv=str(_state(tmp_path)))
        analysis._labels = []
        _fig, ax = plt.subplots()
        analysis.plot(np.empty((0, 4)), ax)
        assert ax.texts
        plt.close("all")

    def test_it_discovers_the_record_the_runner_writes(self, tmp_path):
        """The path that fails on a cluster, where nothing is where the
        laptop put it."""
        run = tmp_path / "run"
        (run / "simulation").mkdir(parents=True)
        state = _state(run / "simulation").rename(
            run / "simulation" / "energy.csv")
        analysis = Thermodynamics()
        analysis.output_dir = run / "analysis" / "thermodynamics"
        analysis.output_dir.mkdir(parents=True)
        analysis.compute(None)

        assert analysis.findings["thermodynamics"]["source"] == str(state)

    @pytest.mark.parametrize("relative_path", [
        Path("simulation/state_data.csv"),
        Path("state_data.csv"),
        Path("simulation/production_state.csv"),
    ])
    def test_it_retains_legacy_record_names(self, tmp_path, relative_path):
        run = tmp_path / "run"
        state = run / relative_path
        state.parent.mkdir(parents=True, exist_ok=True)
        _state(state.parent).rename(state)
        analysis = Thermodynamics()
        analysis.output_dir = run / "analysis" / "thermodynamics"
        analysis.output_dir.mkdir(parents=True)

        assert analysis._state_path() == state

    def test_an_explicit_record_takes_precedence(self, tmp_path):
        run = tmp_path / "run"
        (run / "simulation").mkdir(parents=True)
        _state(run / "simulation").rename(run / "simulation" / "energy.csv")
        (tmp_path / "explicit").mkdir()
        explicit = _state(tmp_path / "explicit")
        analysis = Thermodynamics(state_csv=str(explicit))
        analysis.output_dir = run / "analysis" / "thermodynamics"
        analysis.output_dir.mkdir(parents=True)

        assert analysis._state_path() == explicit
