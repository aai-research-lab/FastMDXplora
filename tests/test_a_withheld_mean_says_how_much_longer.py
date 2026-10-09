"""A mean withheld for want of sampling says how much longer the run must be.

An analysis that withheld its mean ended "The remedy is a longer run, or
better, replicas", and nothing anywhere said how much longer: the report, the
GUI and the Agent all stopped at that sentence. The series already shows its
own correlation, and that says how many more frames would give ten
independent samples; the run's clock turns that into nanoseconds, and the
run's own `cost.json` into hours here.

Where the run cannot resolve its correlation time, the ten samples it
appears to hold are an upper bound, and the planning figure was nothing at
all: ``sampling_shortfall`` said the target was met for a series whose mean
was withheld for exactly this reason. It is now at least as long again, and
said to be a floor.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")


def _ar(n: int, phi: float, seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    x, noise = np.zeros(n), rng.normal(size=n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + noise[i]
    return x


def _run(values: np.ndarray, tmp_path: Path, *, interval_ps: float | None = 10.0) -> dict:
    from fastmdxplora.analysis.base import Analysis

    class Given(Analysis):
        name = "given"
        time_series = True

        def compute(self, traj):
            return values

        def plot(self, result, ax):
            ax.plot(result)

        def default_ylabel(self):
            return "Given (nm)"

    top = md.Topology()
    residue = top.add_residue("ALA", top.add_chain())
    top.add_atom("C", md.element.carbon, residue)
    traj = md.Trajectory(np.zeros((values.size, 1, 3)), top)
    traj.time = (np.arange(1, values.size + 1) * interval_ps if interval_ps
                 else np.full(values.size, np.nan))
    analysis = Given(output_dir=tmp_path)
    assert analysis.run(traj).status == "ok"
    return analysis.findings["mean"]


class TestTheShortfall:
    def test_a_run_that_cannot_resolve_its_correlation_is_asked_to_double(self):
        """It holds 19.6 apparent samples, above the ten a mean needs, and the
        mean is withheld because that count is an upper bound. The shortfall
        said the target was met."""
        from fastmdxplora.statistics import sampling_shortfall, summarise

        series = _ar(400, 0.9)
        _, why = summarise(series)
        assert why.refusal.code == "analysis.sampling.correlation_unresolved"
        short = sampling_shortfall(series, frame_interval_ns=0.01)
        assert not short.met and not short.resolved
        discard = summarise(series)[0].discard
        assert short.more_frames >= series.size - discard
        assert short.as_record()["lower_bound"] is True
        assert str(short).startswith("The run is too short to resolve its own correlation time")
        assert "At least" in str(short)

    def test_a_resolved_run_asks_for_what_it_lacks(self):
        from fastmdxplora.statistics import detect_equilibration, sampling_shortfall

        series = _ar(4000, 0.9, seed=3)
        short = sampling_shortfall(series, target_independent=400, frame_interval_ns=0.01)
        assert short.resolved
        discard, g, _ = detect_equilibration(series)
        assert short.more_frames == int(np.ceil(400 * g)) - (series.size - discard) > 0
        assert "lower_bound" not in short.as_record()
        assert "further frames" in str(short) and "At least" not in str(short)


class TestTheAnalysisRecordsIt:
    def test_beside_the_withheld_mean_in_nanoseconds(self, tmp_path):
        mean = _run(_ar(400, 0.9), tmp_path)
        assert mean["not_a_measurement"]
        short = mean["shortfall"]
        assert short["lower_bound"] is True
        # One frame every 10 ps.
        assert short["more_ns"] == pytest.approx(short["more_frames"] * 0.01)
        written = json.loads((tmp_path / "given" / "options.json").read_text(encoding="utf-8"))
        assert written["findings"]["mean"]["shortfall"] == short

    def test_without_a_clock_no_duration_is_invented(self, tmp_path):
        short = _run(_ar(400, 0.9), tmp_path, interval_ps=None)["shortfall"]
        assert short["more_frames"] > 0
        assert "more_ns" not in short

    def test_a_measured_mean_asks_for_nothing(self, tmp_path):
        mean = _run(np.random.RandomState(1).normal(size=2000), tmp_path)
        assert "not_a_measurement" not in mean
        assert "shortfall" not in mean

    def test_three_frames_are_not_a_run_to_extend(self, tmp_path):
        mean = _run(np.array([1.0, 2.0]), tmp_path)
        assert mean["not_a_measurement"]
        assert "shortfall" not in mean


def _study(root: Path, asks: dict[str, dict | None], *, cost: dict | None = None) -> Path:
    for name, shortfall in asks.items():
        folder = root / "analysis" / name
        folder.mkdir(parents=True)
        mean = {"mean": 1.0, "not_a_measurement": "too short"} if shortfall else {"mean": 1.0}
        if shortfall:
            mean["shortfall"] = shortfall
        (folder / "options.json").write_text(
            json.dumps({"analysis": name, "findings": {"mean": mean}}), encoding="utf-8")
    if cost is not None:
        (root / "simulation").mkdir(parents=True, exist_ok=True)
        (root / "simulation" / "cost.json").write_text(json.dumps(cost), encoding="utf-8")
    return root


COST = {"particles": 6560, "steps": 6000, "seconds": 120.0, "platform": "CPU",
        "precision": "mixed", "timestep_fs": 2.0}


class TestTheStudyAsks:
    def test_the_largest_ask_rounded_up_and_its_cost_here(self, tmp_path):
        from fastmdxplora.simulation.sampling_ask import sampling_asked_for

        ask = sampling_asked_for(_study(tmp_path, {
            "rg": {"more_frames": 257, "more_ns": 2.57},
            "hbonds": {"more_frames": 40, "more_ns": 0.4},
            "rmsd": None}, cost=COST))
        assert ask.more_ns == 2.6
        assert ask.analyses == ("rg", "hbonds")
        assert not ask.lower_bound
        # 2.6 ns at 2 fs is 1.3 million steps, at 0.02 s a step.
        assert ask.seconds == pytest.approx(1.3e6 * 0.02)
        assert ask.as_text() == (
            "Radius of gyration and hydrogen bonds withheld their means for want of "
            "sampling: 2.6 ns more "
            "production should give each 10 independent samples; at this run's own "
            "speed on CPU, about 7 h 13 min.")

    def test_a_floor_anywhere_makes_it_a_floor(self, tmp_path):
        from fastmdxplora.simulation.sampling_ask import sampling_asked_for

        ask = sampling_asked_for(_study(tmp_path, {
            "rg": {"more_frames": 257, "more_ns": 2.57},
            "hbonds": {"more_frames": 40, "more_ns": 0.4, "lower_bound": True}}))
        assert ask.lower_bound and ask.seconds is None
        assert ask.as_text().startswith("Radius of gyration and hydrogen bonds withheld their "
                                        "means for want of "
                                        "sampling: at least 2.6 ns more production")
        assert "estimate again" in ask.as_text()

    def test_it_extends_the_study_in_place(self, tmp_path):
        from fastmdxplora.simulation.sampling_ask import sampling_asked_for

        root = _study(tmp_path / "study", {"rg": {"more_frames": 30, "more_ns": 0.301}})
        ask = sampling_asked_for(root)
        assert ask.more_ns == 0.31
        assert ask.config(root) == {"simulation": {"resume_from": str(root.resolve()),
                                                   "extra_ns": 0.31}}

    @pytest.mark.parametrize("value, rounded", [
        (2.57, 2.6), (2.6, 2.6), (0.0123, 0.013), (151.0, 160.0), (9.99, 10.0)])
    def test_rounded_up_not_down(self, value, rounded):
        from fastmdxplora.simulation.sampling_ask import _round_up

        assert _round_up(value) == pytest.approx(rounded)

    def test_nothing_asked(self, tmp_path):
        from fastmdxplora.simulation.sampling_ask import sampling_asked_for

        assert sampling_asked_for(tmp_path) is None
        assert sampling_asked_for(_study(tmp_path / "a", {"rmsd": None})) is None
        # Frames only, with no clock to say them in: nothing in nanoseconds.
        assert sampling_asked_for(_study(tmp_path / "b", {"rg": {"more_frames": 30}})) is None
        broken = _study(tmp_path / "c", {"rg": {"more_frames": 30, "more_ns": 0.3}})
        (broken / "analysis" / "rg" / "options.json").write_text("{", encoding="utf-8")
        assert sampling_asked_for(broken) is None

    def test_a_cost_record_it_cannot_read_leaves_the_time_unsaid(self, tmp_path):
        from fastmdxplora.simulation.sampling_ask import sampling_asked_for

        ask = sampling_asked_for(_study(tmp_path, {"rg": {"more_frames": 1, "more_ns": 1.0}},
                                        cost={"steps": 0, "seconds": 1, "timestep_fs": 2}))
        assert ask.seconds is None and "speed" not in ask.as_text()


class TestItIsSaid:
    def test_to_the_agent_with_the_config_that_runs_it(self, tmp_path, monkeypatch):
        from fastmdxplora.gui.agent_panel import _sampling_summary
        from fastmdxplora.simulation import resume

        root = _study(tmp_path / "study", {"rg": {"more_frames": 257, "more_ns": 2.57}},
                      cost=COST)
        monkeypatch.setattr(resume, "continuation_of",
                            lambda *a, **k: type("C", (), {"possible": True})())
        text = _sampling_summary(root)
        assert text.startswith("what the withheld means need: Radius of gyration withheld its mean")
        assert "extra_ns: 2.6" in text and f"resume_from: {root.resolve()}" in text

    def test_to_the_agent_without_one_where_it_cannot_continue(self, tmp_path):
        from fastmdxplora.gui.agent_panel import _run_status

        root = _study(tmp_path / "study", {"rg": {"more_frames": 257, "more_ns": 2.57}})

        class Runtime:
            active_root = root

            def snapshot(self):
                return {"active_run": str(root), "status": "idle"}

        status = _run_status(Runtime())
        assert "what the withheld means need: Radius of gyration withheld its mean" in status
        assert "extra_ns" not in status

    def test_in_the_report(self, tmp_path):
        from fastmdxplora.report.document import _convergence_section

        root = _study(tmp_path / "study", {"rg": {"more_frames": 257, "more_ns": 2.57}},
                      cost=COST)
        data = root / "analysis" / "rmsd"
        data.mkdir(parents=True)
        (data / "rmsd.dat").write_text("".join(f"{v:.5f}\n" for v in 0.2 + 0.01 * _ar(60, 0.95)),
                                       encoding="utf-8")
        section = _convergence_section(root)
        assert ("**What would support it.** Radius of gyration withheld its mean for want "
                "of sampling: 2.6 ns more production") in section
        assert "extra_ns: 2.6" in section


def test_the_thermodynamic_means_withheld_say_how_much_longer_too(tmp_path):
    """"What they need" offered 0.0004 ns more for RMSD and left out the
    potential energy, temperature and density withheld beside it, which
    needed about ten times more."""
    from fastmdxplora.analysis.thermodynamics import Thermodynamics
    from fastmdxplora.simulation.sampling_ask import sampling_asked_for

    state = tmp_path / "energy.csv"
    rows = ['#"Step","Time (ps)","Potential Energy (kJ/mole)","Temperature (K)"']
    energy, temperature = _ar(60, 0.9, 1) * 50 - 45000, _ar(60, 0.9, 2) * 3 + 300
    for k in range(60):
        rows.append(f"{(k + 1) * 500},{(k + 1) * 1.0},{energy[k]},{temperature[k]}")
    state.write_text("\n".join(rows) + "\n", encoding="utf-8")
    analysis = Thermodynamics(state_csv=str(state))
    analysis.compute(None)
    record = analysis.findings["thermodynamics"]
    withheld = [key for key in ("potential_energy", "temperature")
                if record[key].get("not_a_measurement")]
    assert withheld
    for key in withheld:
        assert record[key]["shortfall"]["more_ns"] > 0
    folder = tmp_path / "study" / "analysis" / "thermodynamics"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text(json.dumps(
        {"analysis": "thermodynamics", "findings": analysis.findings}, default=float),
        encoding="utf-8")
    asked = sampling_asked_for(tmp_path / "study")
    assert asked is not None and set(withheld) <= set(asked.analyses)
    assert "potential energy" in asked.as_text() or "temperature" in asked.as_text()


def test_every_mean_an_analysis_withheld_is_asked_for_and_named(tmp_path):
    """Helix fraction and polar SASA withheld beside SASA's mean were left
    out of "what they need", which asked less than helix fraction's own
    figure; the fix named the quantities by their keys; and an unresolved
    correlation's ask said 10 independent samples where it targets 25."""
    from fastmdxplora.remedies import _longer
    from fastmdxplora.simulation.sampling_ask import sampling_asked_for

    def short(more_ns, target=10.0, floor=False):
        record = {"target_independent": target, "more_ns": more_ns, "more_frames": 5}
        if floor:
            record["lower_bound"] = True
        return {"mean": 1.0, "not_a_measurement": "withheld", "shortfall": record}

    study = tmp_path / "study"
    for name, findings in (
            ("sasa", {"mean": short(0.087), "polar_sasa": short(0.089),
                      "hydrophobic_sasa": {"mean": 2.0, "standard_error": 0.1}}),
            ("ss", {"helix_fraction": short(0.095, target=25.0, floor=True)}),
            ("thermodynamics", {"thermodynamics": {"samples": 60,
                                                   "potential_energy": short(0.01)}})):
        folder = study / "analysis" / name
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps(
            {"analysis": name, "findings": findings}), encoding="utf-8")
    asked = sampling_asked_for(study)
    assert asked.analyses == ("helix_fraction", "polar_sasa", "sasa", "potential_energy")
    assert asked.more_ns == 0.095 and asked.lower_bound
    text = asked.as_text()
    assert text.startswith("Helix fraction, polar SASA, ")
    assert "potential energy" in text
    # They asked for 10 and 25: "each 25" was untrue of the ones asking 10.
    assert "25 independent samples" not in text and "the independent samples it asked for" in text
    from dataclasses import replace

    assert "should give it 25 independent samples" in replace(
        asked, analyses=("helix_fraction",), target=25.0).as_text()
    # Named in the sentence's case: a heading beside a quantity's key.
    named = replace(asked, analyses=("sasa", "polar_sasa", "rmsd")).said()
    assert named == "solvent accessible surface area, polar SASA and RMSD", named
    from fastmdxplora.gui.report_dashboard import ANALYSIS_SECTION_BY_FOLDER

    for folder, heading in ANALYSIS_SECTION_BY_FOLDER.items():
        mid = replace(asked, analyses=("rmsd", folder)).said().split(" and ", 1)[1]
        first = heading.split(" ", 1)[0]
        if first[:1].isupper() and first[1:2].islower():
            assert mid[:1].islower(), (folder, mid)
    fix = _longer("analysis.sampling.too_few_independent", "", "", study).fix
    assert "potential_energy" not in fix and "polar SASA" in fix


def test_a_length_is_said_in_the_unit_it_reads_in():
    from fastmdxplora.simulation.sampling_ask import length_said

    assert length_said(0.0114) == "11.4 ps"
    assert length_said(2e-5) == "0.02 ps"
    assert length_said(0.99996) == "1 ns"
    assert length_said(2.6) == "2.6 ns"
    assert length_said(12000) == "12000 ns" and length_said(10000) == "10000 ns"
    assert length_said(99999) == "100000 ns" and length_said(1.23456) == "1.235 ns"


def test_the_overview_s_thermodynamic_means_ask_too_without_their_analysis(tmp_path):
    """Analysed without its thermodynamics, a study's potential energy,
    temperature and density were withheld on the Overview and left out of
    "what they need", whose figure fell below the potential energy's own."""
    from fastmdxplora.simulation.sampling_ask import sampling_asked_for

    study = tmp_path / "study"
    (study / "simulation").mkdir(parents=True)
    rows = ['#"Step","Time (ps)","Potential Energy (kJ/mole)","Temperature (K)"']
    energy, temperature = _ar(60, 0.9, 1) * 50 - 45000, _ar(60, 0.9, 2) * 3 + 300
    for k in range(60):
        rows.append(f"{(k + 1) * 500},{(k + 1) * 1.0},{energy[k]},{temperature[k]}")
    (study / "simulation" / "energy.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    folder = study / "analysis" / "rg"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text(json.dumps(
        {"analysis": "rg", "findings": {"mean": {"mean": 1.0, "standard_error": 0.01}}}),
        encoding="utf-8")
    asked = sampling_asked_for(study)
    assert asked is not None and "potential_energy" in asked.analyses, asked
    assert "potential energy" in asked.as_text()
