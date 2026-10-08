"""One mean has one verdict, wherever it is said.

The Overview and the Analysis page said Determined of an RMSD while the
report's convergence checks said its correlation time was not resolved: the
report asked the whole series, its equilibration included, where the record
the pages read asks what is averaged. The report's table printed an error
beside means the Analysis page withheld, its summary counted "too few
independent samples" of means its table gave 18 of, the Agent's answer from
the records named means by their keys and gave errors the page withheld and
left out the thermodynamic means, and the Overview averaged the live
record's samples where the report averaged production's energy file. Each
now reads the analyses' record, judged by one rule, which also withholds a
mean whose averaged frames still drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

ENERGY_HEADER = ('#"Progress (%)","Step","Time (ps)","Potential Energy (kJ/mole)",'
                 '"Kinetic Energy (kJ/mole)","Total Energy (kJ/mole)","Temperature (K)",'
                 '"Box Volume (nm^3)","Density (g/mL)","Speed (ns/day)"')


def _ar1(phi: float, n: int, seed: int, scale: float = 1.0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    values = np.zeros(n)
    for i in range(1, n):
        values[i] = phi * values[i - 1] + rng.normal()
    return values * scale


def _with_transient(n: int = 400, seed: int = 0) -> np.ndarray:
    """A relaxation then a stationary, well-resolved stretch: the whole
    series' correlation is long, what is averaged after it is short."""
    series = 0.10 + _ar1(0.5, n, seed, 0.002)
    series[: n // 2] += np.linspace(0.08, 0.0, n // 2)
    return series


def _code(record: dict) -> str:
    reason = record.get("not_a_measurement")
    return str(getattr(getattr(reason, "refusal", None), "code", "") or "")


def _analysis(root: Path, name: str, series: np.ndarray) -> None:
    from fastmdxplora.statistics import mean_record

    folder = root / "analysis" / name
    folder.mkdir(parents=True, exist_ok=True)
    np.savetxt(folder / f"{name}.dat", series)
    record = mean_record(series)
    record = {key: (str(value) if key == "not_a_measurement" else value)
              for key, value in record.items()}
    (folder / "options.json").write_text(json.dumps(
        {"analysis": name, "findings": {"mean": record}}, default=float), encoding="utf-8")


def _study(root: Path) -> Path:
    _analysis(root, "rmsd", _with_transient())
    _analysis(root, "rg", 0.7 + _ar1(0.95, 300, 3, 0.002))       # correlation unresolved
    simulation = root / "simulation"
    simulation.mkdir(parents=True)
    potential = -45000 + _ar1(0.6, 200, 5, 60.0)
    lines = [ENERGY_HEADER]
    for k, value in enumerate(potential):
        lines.append(f"{k}%,{1000 * (k + 1)},{2.0 * (k + 1)},{value},8500,{value + 8500},"
                     f"{300 + 2 * np.sin(k)},{33.0 + 0.1 * np.cos(k)},{1.02 + 0.001 * np.cos(k)},5")
    (simulation / "energy.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    live = ["timestamp,stage,step,simulation_time_ns,potential_energy,total_energy,"
            "temperature,volume,density"]
    for k in range(0, 200, 20):   # the live record's coarser samples
        live.append(f",Production,{1000 * (k + 1)},{0.002 * (k + 1)},{potential[k] + 500},"
                    f"0,300,33.0,1.02")
    (simulation / "live_metrics.csv").write_text("\n".join(live) + "\n", encoding="utf-8")
    (simulation / "simulation_parameters.json").write_text(json.dumps(
        {"duration_ns_actual": 0.4, "parameters": {"temperature_K": 300.0}}), encoding="utf-8")
    return root


def test_the_report_resolves_a_correlation_as_the_record_does():
    from fastmdxplora.report.convergence import assess_series
    from fastmdxplora.statistics import correlation_is_resolved, mean_record

    series = _with_transient()
    record = mean_record(series)
    # The whole series cannot resolve it; what the record averages does.
    assert not correlation_is_resolved(series)
    assert _code(record) == "" and np.isfinite(record["standard_error"])
    assessed = assess_series("rmsd", series)
    assert assessed.correlation_is_measurable and assessed.is_determined
    assert assessed.standard_error == pytest.approx(record["standard_error"], rel=1e-9)
    assert assessed.mean == pytest.approx(record["mean"])


def test_a_mean_the_record_withholds_has_no_error_in_the_report(tmp_path):
    from fastmdxplora.report.document import _convergence_section, _what_the_run_supports
    from fastmdxplora.statistics import mean_record

    root = _study(tmp_path / "study")
    rg = mean_record(np.loadtxt(root / "analysis" / "rg" / "rg.dat"))
    assert _code(rg) == "analysis.sampling.correlation_unresolved"
    section = _convergence_section(root)
    row = next(line for line in section.splitlines() if line.startswith("| Rg |"))
    assert row.split("|")[6].strip() == "not determined", row
    rmsd = next(line for line in section.splitlines() if line.startswith("| RMSD |"))
    assert rmsd.split("|")[6].strip() not in ("", "not determined"), rmsd
    checks = next(line for line in section.splitlines() if "correlation time is resolved" in line)
    assert "Rg" in checks and "RMSD" not in checks
    summary = _what_the_run_supports(root)
    assert "too few independent samples of means" not in summary
    assert "e+0" not in section


def test_the_agent_answers_as_the_analysis_page_says(tmp_path):
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.gui.records_answer import answer_from_the_records

    root = _study(tmp_path / "study")
    thermo = root / "analysis" / "thermodynamics"
    thermo.mkdir(parents=True)
    from fastmdxplora.statistics import mean_record

    density = mean_record(np.full(50, 1.0) + _ar1(0.2, 50, 9, 0.001))
    potential = mean_record(-45000 + _ar1(0.95, 40, 9, 50.0))
    found = {"thermodynamics": {"samples": 50,
                                "density": {**density, "units": "g/mL"},
                                "potential_energy": {**potential, "units": "kJ/mol"}}}
    (thermo / "options.json").write_text(json.dumps(
        {"analysis": "thermodynamics", "findings": found}, default=str), encoding="utf-8")
    said = answer_from_the_records(root, "found")
    page = {q["label"]: q for row in overview_of(root)["rows"] for q in row["quantities"]}
    assert "Density:" in said and "Potential energy:" in said
    assert "potential_energy" not in said and "rmsd:" not in said
    line = next(text for text in said.splitlines() if text.startswith("- Radius of gyration"))
    assert "±" not in line and "not determined" in line
    assert page["Radius of gyration"]["said"] in line
    assert "e+0" not in said


def test_the_overview_averages_what_the_report_averages(tmp_path):
    from fastmdxplora.gui.overview_view import _thermodynamics
    from fastmdxplora.report.document import _assess_this_run

    root = _study(tmp_path / "study")
    shown = _thermodynamics(root)["means"]["potential_energy"]
    reported = _assess_this_run(root)["observables"]["potential_energy"]
    assert shown["mean"] == pytest.approx(reported["mean"], abs=1e-6)


def test_a_mean_whose_averaged_frames_still_drift_is_withheld():
    """The record's own rule, which every page now reads. A trend usually
    reads as a long correlation time and is withheld for that first; this
    is the rule behind it, for frames whose correlation reads short while
    their last third sits well away from their first."""
    from fastmdxplora.statistics import _summary

    rng = np.random.RandomState(4)
    climbing = np.linspace(0.0, 10.0, 600) + rng.normal(scale=0.5, size=600)
    kept = (1.0, 599.0)
    record, withheld = _summary(climbing, 0, 1.0, kept, lambda: kept, 10.0)
    assert withheld is not None
    assert withheld.refusal.code == "analysis.sampling.still_drifting"
    assert not np.isfinite(record.standard_error)
    steady = rng.normal(scale=0.5, size=600)
    _, none = _summary(steady, 0, 1.0, kept, lambda: kept, 10.0)
    assert none is None


def test_a_stationary_resolved_mean_is_never_called_drifting():
    """Crossed by a trend, almost never by noise: on stationary series long
    enough to resolve their correlation, none is withheld as drifting."""
    from fastmdxplora.statistics import mean_record

    drifting = 0
    for seed in range(200):
        record = mean_record(_ar1(0.8, 600, seed))
        drifting += _code(record) == "analysis.sampling.still_drifting"
    assert drifting == 0


def test_the_summary_counts_each_observable_once(monkeypatch):
    """Six observables were said as "3 had equilibrated, 3 could not be
    judged from a run this length, the mean of 6 more is not determined"."""
    from pathlib import Path

    from fastmdxplora.report import document

    def record(equilibrated, determined):
        return {"equilibrated": equilibrated, "determined": determined}

    records = {"observables": {
        "a": record(True, True), "b": record(True, True), "c": record(True, False),
        "d": record(None, False), "e": record(None, False), "f": record(None, False)}}
    monkeypatch.setattr(document, "_assess_this_run", lambda root: records)
    said = document._what_the_run_supports(Path("/nowhere"))
    assert said.startswith("Of 6 observables assessed, 3 had equilibrated (the mean of 1 of "
                           "them not determined), 3 could not be judged from a run this "
                           "length;"), said
    assert "more" not in said


def test_the_report_rounds_an_error_once():
    """An error of 0.0017484 was kept as 0.00175 and given as 0.0018 in the
    report's table, 0.0017 on every other page."""
    from fastmdxplora.report.convergence import _six_figures
    from fastmdxplora.statistics import with_its_error

    assert _six_figures(0.0017484) == 0.0017484
    assert with_its_error(0.098812, _six_figures(0.0017484)) == with_its_error(0.098812, 0.0017484)
    assert _six_figures(-45427.123456) == -45427.12346
